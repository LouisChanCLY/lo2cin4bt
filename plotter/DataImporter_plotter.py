"""
DataImporter_plotter.py

【功能說明】
------------------------------------------------------------
本模組為 Lo2cin4BT 可視化平台的數據導入核心模組，負責讀取和解析 metricstracker 產生的 parquet 檔案，支援掃描指定資料夾、解析參數組合、提取績效指標和權益曲線數據。

【流程與數據流】
------------------------------------------------------------
- 主流程：掃描目錄 → 讀取檔案 → 解析參數 → 提取數據 → 返回結果
- 各模組間數據流明確，流程如下：

```mermaid
flowchart TD
    A[DataImporterPlotter] -->|掃描| B[目錄掃描]
    B -->|找到| C[Parquet檔案]
    C -->|讀取| D[DataFrame]
    D -->|解析| E[參數組合]
    D -->|提取| F[績效指標]
    D -->|提取| G[權益曲線]
    E -->|返回| H[數據字典]
    F -->|返回| H
    G -->|返回| H
```

【維護與擴充重點】
------------------------------------------------------------
- 新增數據格式、參數結構時，請同步更新頂部註解與對應模組
- 若 parquet 檔案格式有變動，需同步更新解析邏輯
- 新增/修改數據格式、參數結構時，務必同步更新本檔案與所有依賴模組
- 檔案讀取和解析邏輯需要特別注意錯誤處理

【常見易錯點】
------------------------------------------------------------
- 檔案路徑錯誤或檔案不存在
- parquet 檔案格式不符合預期
- 參數解析邏輯錯誤
- 記憶體使用過大

【錯誤處理】
------------------------------------------------------------
- 檔案不存在時提供詳細錯誤訊息
- 解析失敗時提供診斷建議
- 記憶體不足時提供優化建議

【範例】
------------------------------------------------------------
- 基本使用：importer = DataImporterPlotter("path/to/data")
- 載入數據：data = importer.load_and_parse_data()

【與其他模組的關聯】
------------------------------------------------------------
- 被 BasePlotter 調用
- 依賴 metricstracker 產生的 parquet 檔案格式
- 輸出數據供 DashboardGenerator 和 CallbackHandler 使用

【版本與變更記錄】
------------------------------------------------------------
- v1.0: 初始版本，支援基本數據導入
- v1.1: 新增參數解析功能
- v1.2: 新增記憶體優化

【參考】
------------------------------------------------------------
- 詳細流程規範如有變動，請同步更新本註解與 README
- 其他模組如有依賴本檔案的行為，請於對應模組頂部註解標明
- parquet 檔案格式請參考 metricstracker 模組
"""

import os
import glob
import logging
import json
from typing import Dict, Any, List, Optional
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pyarrow.parquet as pq
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.console import Group
import plotly.graph_objects as go
import plotly.express as px
from dash import Dash, html, dcc, Input, Output, callback_context
import dash_bootstrap_components as dbc
from dash.dependencies import Input, Output, State
import warnings
warnings.filterwarnings('ignore')

# 檢查 psutil 是否可用
PSUTIL_AVAILABLE = False
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    pass

class DataImporterPlotter:
    """
    數據導入器
    
    負責讀取和解析 metricstracker 產生的 parquet 檔案，
    提取參數組合、績效指標和權益曲線數據。
    """
    
    def __init__(self, data_path: str, logger: Optional[logging.Logger] = None):
        """
        初始化數據導入器
        
        Args:
            data_path: metricstracker 產生的 parquet 檔案目錄路徑
            logger: 日誌記錄器，預設為 None
        """
        self.data_path = data_path
        self.logger = logger or logging.getLogger(__name__)
        self.logger.setLevel(logging.WARNING)
        # 不再自動加 handler，避免預設 log 輸出
        # if not self.logger.hasHandlers():
        #     handler = logging.StreamHandler()
        #     formatter = logging.Formatter('[%(levelname)s] %(message)s')
        #     handler.setFormatter(formatter)
        #     self.logger.addHandler(handler)
        
        # 確保目錄存在
        if not os.path.exists(self.data_path):
            raise FileNotFoundError(f"數據目錄不存在: {self.data_path}")
        
        # 新增：初始化緩存系統
        self.strategy_analysis_cache = {}
        self.parameter_index_cache = {}
        self.cache_stats = {'hits': 0, 'misses': 0, 'size': 0}
    
    def _get_memory_usage(self) -> Dict[str, float]:
        """獲取當前內存使用情況"""
        if not PSUTIL_AVAILABLE:
            return {'available': 0, 'used': 0, 'percent': 0}
        
        try:
            memory = psutil.virtual_memory()
            return {
                'available': memory.available / 1024 / 1024 / 1024,  # GB
                'used': memory.used / 1024 / 1024 / 1024,  # GB
                'percent': memory.percent
            }
        except Exception as e:
            self.logger.warning(f"獲取內存使用失敗: {e}")
            return {'available': 0, 'used': 0, 'percent': 0}
    
    def _log_memory_usage(self, stage: str):
        """記錄內存使用情況"""
        if PSUTIL_AVAILABLE:
            memory = self._get_memory_usage()
            self.logger.info(f"{stage} - 內存使用: {memory['used']:.2f}GB / {memory['percent']:.1f}%")
    
    def scan_parquet_files(self) -> List[str]:
        """
        掃描目錄中的 parquet 檔案
        
        Returns:
            List[str]: parquet 檔案路徑列表
        """
        try:
            pattern = os.path.join(self.data_path, "*.parquet")
            parquet_files = glob.glob(pattern)
            
            if not parquet_files:
                self.logger.warning(f"在目錄 {self.data_path} 中未找到 parquet 檔案")
                return []
            
            return sorted(parquet_files)
            
        except Exception as e:
            self.logger.error(f"掃描 parquet 檔案失敗: {e}")
            raise
    
    def parse_parameters_from_filename(self, filename: str) -> Dict[str, Any]:
        """
        從檔案名稱解析參數組合
        
        Args:
            filename: 檔案名稱
            
        Returns:
            Dict[str, Any]: 解析出的參數字典
        """
        try:
            # 移除路徑和副檔名
            basename = os.path.basename(filename)
            name_without_ext = os.path.splitext(basename)[0]
            
            # 預設參數結構
            parameters = {
                'filename': basename,
                'reference_code': '',
                'parameters': {}
            }
            
            # 嘗試解析檔案名稱中的參數
            # 格式範例: 20250718_5ey6hl0q_metrics.parquet
            if '_metrics' in name_without_ext:
                parts = name_without_ext.split('_')
                if len(parts) >= 2:
                    parameters['reference_code'] = parts[1]
            
            return parameters
            
        except Exception as e:
            self.logger.warning(f"解析檔案名稱參數失敗 {filename}: {e}")
            return {'filename': os.path.basename(filename), 'reference_code': '', 'parameters': {}}
    
    def extract_metrics_from_metadata(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        從 DataFrame 的 metadata 中提取績效指標
        
        Args:
            df: 包含 metadata 的 DataFrame
            
        Returns:
            Dict[str, Any]: 績效指標字典
        """
        try:
            metrics = {}
            
            # 檢查是否有 metadata
            if hasattr(df, 'metadata') and df.metadata:
                # 嘗試解析 strategy_metrics1
                if 'strategy_metrics1' in df.metadata:
                    try:
                        strategy_metrics = json.loads(df.metadata['strategy_metrics1'])
                        metrics.update(strategy_metrics)
                    except (json.JSONDecodeError, TypeError) as e:
                        self.logger.warning(f"解析 strategy_metrics1 失敗: {e}")
                
                # 嘗試解析 bah_metrics1
                if 'bah_metrics1' in df.metadata:
                    try:
                        bah_metrics = json.loads(df.metadata['bah_metrics1'])
                        metrics.update({f"bah_{k}": v for k, v in bah_metrics.items()})
                    except (json.JSONDecodeError, TypeError) as e:
                        self.logger.warning(f"解析 bah_metrics1 失敗: {e}")
            
            return metrics
            
        except Exception as e:
            self.logger.warning(f"提取績效指標失敗: {e}")
            return {}
    
    def extract_equity_curve_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        提取權益曲線數據
        
        Args:
            df: 原始 DataFrame
            
        Returns:
            pd.DataFrame: 權益曲線數據
        """
        try:
            # 確保必要的欄位存在
            required_columns = ['Time', 'Equity_value']
            missing_columns = [col for col in required_columns if col not in df.columns]
            
            if missing_columns:
                self.logger.warning(f"缺少必要欄位: {missing_columns}")
                return pd.DataFrame()
            
            # 提取權益曲線相關欄位
            equity_columns = ['Time', 'Equity_value', 'Change']
            available_columns = [col for col in equity_columns if col in df.columns]
            
            equity_data = df[available_columns].copy()
            
            # 確保 Time 欄位為 datetime 格式
            if 'Time' in equity_data.columns:
                equity_data['Time'] = pd.to_datetime(equity_data['Time'])
                equity_data = equity_data.sort_values('Time')
            
            return equity_data
            
        except Exception as e:
            self.logger.warning(f"提取權益曲線數據失敗: {e}")
            return pd.DataFrame()
    
    def _load_single_parquet_file_optimized(self, file_path: str) -> List[Dict[str, Any]]:
        """
        優化的單個parquet檔案載入邏輯（批量處理版本）
        
        Args:
            file_path: parquet檔案路徑
            
        Returns:
            List[Dict[str, Any]]: 包含數據和元信息的字典列表
        """
        file_start_time = datetime.now()
        filename = os.path.basename(file_path)
        
        try:
            # 步驟1: 讀取parquet檔案
            step1_start = datetime.now()
            table = pq.read_table(file_path)
            step1_time = (datetime.now() - step1_start).total_seconds()
            
            # 步驟2: 選擇必要列
            step2_start = datetime.now()
            required_columns = ['Time', 'Equity_value', 'BAH_Equity', 'Backtest_id']
            available_columns = [col for col in required_columns if col in table.column_names]
            table = table.select(available_columns)
            step2_time = (datetime.now() - step2_start).total_seconds()
            
            # 步驟3: 轉換為pandas
            step3_start = datetime.now()
            df = table.to_pandas()
            step3_time = (datetime.now() - step3_start).total_seconds()
            
            # 步驟4: 提取metadata
            step4_start = datetime.now()
            meta = table.schema.metadata or {}
            batch_metadata = []
            if b'batch_metadata' in meta:
                batch_metadata = json.loads(meta[b'batch_metadata'].decode())
            step4_time = (datetime.now() - step4_start).total_seconds()
            
            # 步驟5: 批量處理數據（優化版本）
            step5_start = datetime.now()
            
            # 批量處理：一次性分組所有數據
            grouped_data = {}
            for backtest_id, group in df.groupby('Backtest_id'):
                grouped_data[backtest_id] = {
                    'equity_curve': group[['Time', 'Equity_value']] if 'Equity_value' in group.columns else None,
                    'bah_curve': group[['Time', 'BAH_Equity']] if 'BAH_Equity' in group.columns else None
                }
            
            # 批量創建結果
            results = []
            for i, meta_item in enumerate(batch_metadata):
                backtest_id = meta_item.get('Backtest_id')
                if backtest_id is not None and backtest_id in grouped_data:
                    group_data = grouped_data[backtest_id]
                    results.append({
                        'Backtest_id': backtest_id,
                        'metrics': meta_item,
                        'equity_curve': group_data['equity_curve'],
                        'bah_curve': group_data['bah_curve'],
                        'file_path': file_path
                    })
                else:
                    # 如果找不到對應的backtest_id，創建空的結果
                    results.append({
                        'Backtest_id': backtest_id,
                        'metrics': meta_item,
                        'equity_curve': None,
                        'bah_curve': None,
                        'file_path': file_path
                    })
            
            # 總計時間
            total_file_time = (datetime.now() - file_start_time).total_seconds()
            
            return results
            
        except Exception as e:
            total_file_time = (datetime.now() - file_start_time).total_seconds()
            self.logger.error(f"優化載入檔案失敗 {filename}: {e}")
            return []
    
    def load_parquet_file(self, file_path: str) -> List[Dict[str, Any]]:
        """
        載入單個 parquet 檔案
        
        Args:
            file_path: parquet 檔案路徑
            
        Returns:
            List[Dict[str, Any]]: 包含數據和元信息的字典列表
        """
        try:
            self.logger.info(f"載入檔案: {file_path}")
            
            # 讀取 parquet 檔案
            df = pd.read_parquet(file_path)
            table = pq.read_table(file_path)
            meta = table.schema.metadata or {}
            batch_metadata = []
            if b'batch_metadata' in meta:
                batch_metadata = json.loads(meta[b'batch_metadata'].decode())
            else:
                self.logger.warning(f"找不到 batch_metadata: {file_path}")
            
            results = []
            for meta_item in batch_metadata:
                backtest_id = meta_item.get('Backtest_id')
                if backtest_id is not None and 'Backtest_id' in df.columns:
                    df_bt = df[df['Backtest_id'] == backtest_id]
                else:
                    df_bt = df
                equity_curve = df_bt[['Time', 'Equity_value']] if 'Equity_value' in df_bt.columns else None
                bah_curve = df_bt[['Time', 'BAH_Equity']] if 'BAH_Equity' in df_bt.columns else None
                results.append({
                    'file_path': file_path,
                    'Backtest_id': backtest_id,
                    'metrics': meta_item,
                    'equity_curve': equity_curve,
                    'bah_curve': bah_curve,
                    'df': df_bt
                })
                self.logger.debug(f"Backtest_id={backtest_id} metrics={meta_item}")
                self.logger.debug(f"equity_curve len={len(equity_curve) if equity_curve is not None else 'None'}")
                self.logger.debug(f"bah_curve len={len(bah_curve) if bah_curve is not None else 'None'}")
            return results
            
        except Exception as e:
            self.logger.error(f"載入檔案失敗 {file_path}: {e}")
            raise
    
    def load_parquet_files_parallel(self, file_paths: List[str]) -> List[Dict[str, Any]]:
        """
        並行載入多個 parquet 檔案
        
        Args:
            file_paths: parquet 檔案路徑列表
            
        Returns:
            List[Dict[str, Any]]: 包含數據和元信息的字典列表
        """
        try:
            self.logger.info(f"開始並行載入 {len(file_paths)} 個檔案")
            
            # 使用進程池並行讀取
            max_workers = min(multiprocessing.cpu_count(), len(file_paths))
            self.logger.info(f"使用 {max_workers} 個進程並行載入")
            
            with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
                # 並行提交所有檔案載入任務
                future_to_file = {
                    executor.submit(self._load_single_parquet_file_optimized, file_path): file_path 
                    for file_path in file_paths
                }
                
                results = []
                completed_count = 0
                failed_count = 0
                
                # 處理完成的任務
                for future in concurrent.futures.as_completed(future_to_file):
                    file_path = future_to_file[future]
                    completed_count += 1
                    
                    try:
                        file_data = future.result()
                        results.extend(file_data)
                        
                        # 顯示進度
                        if completed_count % 5 == 0 or completed_count == len(file_paths):
                            self.logger.info(f"已載入 {completed_count}/{len(file_paths)} 個檔案")
                            
                    except Exception as e:
                        failed_count += 1
                        self.logger.error(f"並行處理檔案失敗 {file_path}: {e}")
                        continue
            
            self.logger.info(f"並行載入完成，共處理 {len(results)} 個數據項")
            return results
            
        except Exception as e:
            self.logger.error(f"並行載入失敗: {e}")
            # 如果並行載入失敗，回退到串行載入
            self.logger.warning("回退到串行載入模式")
            return self._fallback_serial_load(file_paths)
    
    def _fallback_serial_load(self, file_paths: List[str]) -> List[Dict[str, Any]]:
        """
        串行載入回退方法
        
        Args:
            file_paths: parquet檔案路徑列表
            
        Returns:
            List[Dict[str, Any]]: 包含數據和元信息的字典列表
        """
        self.logger.info("使用串行載入回退方法")
        results = []
        for file_path in file_paths:
            try:
                file_data = self._load_single_parquet_file_optimized(file_path)
                results.extend(file_data)
            except Exception as e:
                self.logger.error(f"串行載入檔案失敗 {file_path}: {e}")
                continue
        return results
    
    def load_and_parse_data(self) -> Dict[str, Any]:
        """
        載入並解析所有選定的 parquet 檔案，並合併所有 Backtest_id 資料
        """
        start_time = datetime.now()
        self.logger.info("開始載入和解析數據")
        
        try:
            # 掃描檔案
            scan_start = datetime.now()
            self._log_memory_usage("掃描檔案開始")
            parquet_files = self.scan_parquet_files()
            scan_time = (datetime.now() - scan_start).total_seconds()
            self._log_memory_usage("掃描檔案完成")
            
            if not parquet_files:
                raise FileNotFoundError("未找到任何 parquet 檔案")

            # 互動式選單
            console = Console()
            # 步驟說明框
            step_content = (
                "🟢 選擇要載入的檔案\n"
                "🔴 生成可視化介面[自動]\n"
                "\n"
                "[bold #dbac30]說明[/bold #dbac30]\n"
                "此步驟用於選擇要載入的 parquet 檔案，支援多檔案同時載入。\n"
                "檔案包含回測結果的績效指標和權益曲線數據。\n\n"
                "[bold #dbac30]檔案選擇格式：[/bold #dbac30]\n"
                "• 單一檔案：輸入數字（如 1）\n"
                "• 多檔案：用逗號分隔（如 1,2,3）\n"
                "• 全部檔案：直接按 Enter\n\n"
                "[bold #dbac30]可選擇的 parquet 檔案：[/bold #dbac30]"
            )
            
            # 準備檔案列表
            file_list = ""
            for i, f in enumerate(parquet_files, 1):
                file_list += f"  [bold #dbac30]{i}.[/bold #dbac30] {os.path.basename(f)}\n"
            
            # 組合完整內容並用 Group 顯示
            complete_content = step_content + "\n" + file_list
            console.print(Panel(complete_content, title=Text("👁️ 可視化 Plotter 步驟：數據選擇", style="bold #dbac30"), border_style="#dbac30"))
            
            # 用戶輸入提示（金色+BOLD格式）
            console.print("[bold #dbac30]輸入可視化檔案號碼：[/bold #dbac30]")
            file_input = input().strip() or 'all'
            if not file_input:  # 如果輸入為空，載入全部檔案
                selected_files = parquet_files
            else:
                try:
                    # 解析用戶輸入的檔案編號
                    file_indices = [int(x.strip()) for x in file_input.split(',')]
                    selected_files = [parquet_files[i-1] for i in file_indices if 1 <= i <= len(parquet_files)]
                    if not selected_files:
                        console.print(Panel("❌ 沒有選擇有效的檔案，預設載入全部檔案。", title=Text("⚠️ 警告", style="bold #8f1511"), border_style="#8f1511"))
                        selected_files = parquet_files
                except (ValueError, IndexError):
                    console.print(Panel("🔔 已自動載入全部檔案。", title=Text("👁️ 可視化 Plotter", style="bold #8f1511"), border_style="#dbac30"))
                    selected_files = parquet_files

            # 載入所有選定檔案
            load_start = datetime.now()
            self._log_memory_usage("檔案載入開始")
            
            all_backtest_ids = []
            all_metrics = {}
            all_equity_curves = {}
            all_bah_curves = {}
            all_file_paths = {}
            all_parameters = []
            
            # 使用並行載入替代串行載入
            try:
                self.logger.info("使用並行載入模式")
                all_file_data = self.load_parquet_files_parallel(selected_files)
                
                for item in all_file_data:
                    backtest_id = item['Backtest_id']
                    if backtest_id is not None:
                        all_backtest_ids.append(backtest_id)
                        all_parameters.append(item['metrics'])
                        all_metrics[backtest_id] = item['metrics']
                        all_equity_curves[backtest_id] = item['equity_curve']
                        all_bah_curves[backtest_id] = item['bah_curve']
                        all_file_paths[backtest_id] = item['file_path']
                        
            except Exception as e:
                self.logger.warning(f"並行載入失敗，回退到串行載入: {e}")
                # 回退到原有的串行載入方式
                for file_path in selected_files:
                    try:
                        file_data = self._load_single_parquet_file_optimized(file_path)
                        for item in file_data:
                            backtest_id = item['Backtest_id']
                            if backtest_id is not None:
                                all_backtest_ids.append(backtest_id)
                                all_parameters.append(item['metrics'])
                                all_metrics[backtest_id] = item['metrics']
                                all_equity_curves[backtest_id] = item['equity_curve']
                                all_bah_curves[backtest_id] = item['bah_curve']
                                all_file_paths[backtest_id] = item['file_path']
                    except Exception as e:
                        self.logger.error(f"處理檔案失敗 {file_path}: {e}")
                        continue
            
            load_time = (datetime.now() - load_start).total_seconds()
            self._log_memory_usage("檔案載入完成")
            
            if not all_parameters:
                raise ValueError("沒有成功載入任何檔案或找到 Backtest_id")
            
            # 識別策略分組
            strategy_start = datetime.now()
            self._log_memory_usage("策略分組開始")
            strategy_groups = DataImporterPlotter.identify_strategy_groups(all_parameters)
            strategy_time = (datetime.now() - strategy_start).total_seconds()
            self._log_memory_usage("策略分組完成")
            
            # 記錄性能統計
            end_time = datetime.now()
            total_time = (end_time - start_time).total_seconds()
            
            self.logger.info(f"數據載入完成統計:")
            self.logger.info(f"  - 總檔案數: {len(selected_files)}")
            self.logger.info(f"  - 總策略數: {len(all_parameters)}")
            self.logger.info(f"  - 總耗時: {total_time:.2f}秒")
            self.logger.info(f"  - 平均每檔案: {total_time/len(selected_files):.3f}秒")
            
            # 顯示緩存統計（如果啟用了緩存）
            if hasattr(self, 'cache_stats'):
                cache_stats = self.get_cache_stats()
                self.logger.info(f"緩存統計: 命中率 {cache_stats['hit_rate']:.2%}")
            
            result = {
                'dataframes': all_metrics,
                'parameters': all_parameters,
                'metrics': all_metrics,
                'equity_curves': all_equity_curves,
                'bah_curves': all_bah_curves,
                'file_paths': all_file_paths,
                'backtest_ids': all_backtest_ids,  # 新增Backtest_id列表
                'strategy_groups': strategy_groups,  # 新增策略分組信息
                'total_files': len(selected_files),
                'loaded_at': datetime.now().isoformat(),
                'load_time_seconds': total_time,  # 新增載入時間統計
                'cache_stats': self.get_cache_stats() if hasattr(self, 'cache_stats') else None  # 新增緩存統計
            }
            return result
        except Exception as e:
            self.logger.error(f"載入和解析數據失敗: {e}")
            raise
    
    def get_parameter_summary(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        獲取參數摘要信息
        
        Args:
            data: 載入的數據字典
            
        Returns:
            Dict[str, Any]: 參數摘要
        """
        try:
            parameters = data.get('parameters', {})
            
            # 統計參數分布
            param_summary = {}
            for param_key, param_data in parameters.items():
                param_dict = param_data.get('parameters', {})
                for key, value in param_dict.items():
                    if key not in param_summary:
                        param_summary[key] = set()
                    param_summary[key].add(str(value))
            
            # 轉換為列表
            for key in param_summary:
                param_summary[key] = sorted(list(param_summary[key]))
            
            return {
                'total_combinations': len(parameters),
                'parameter_distribution': param_summary,
                'parameter_keys': list(param_summary.keys())
            }
            
        except Exception as e:
            self.logger.warning(f"獲取參數摘要失敗: {e}")
            return {}
    
    def filter_data_by_parameters(self, data: Dict[str, Any], filters: Dict[str, Any]) -> Dict[str, Any]:
        """
        根據參數篩選數據
        
        Args:
            data: 原始數據字典
            filters: 篩選條件字典
            
        Returns:
            Dict[str, Any]: 篩選後的數據字典
        """
        try:
            if not filters:
                return data
            
            parameters = data.get('parameters', {})
            filtered_keys = set(parameters.keys())
            
            # 應用篩選條件
            for param_name, param_values in filters.items():
                if not param_values:  # 空值表示不過濾
                    continue
                
                matching_keys = set()
                for key, param_data in parameters.items():
                    param_dict = param_data.get('parameters', {})
                    if param_name in param_dict:
                        param_value = str(param_dict[param_name])
                        if param_value in param_values:
                            matching_keys.add(key)
                
                filtered_keys = filtered_keys.intersection(matching_keys)
            
            # 構建篩選後的數據
            filtered_data = {}
            for key in filtered_keys:
                filtered_data[key] = data.get(key, {})
            
            return filtered_data
            
        except Exception as e:
            self.logger.warning(f"參數篩選失敗: {e}")
            return data 

    @staticmethod
    def parse_all_parameters(parameters: list) -> dict:
        """
        動態展開所有 Entry_params/Exit_params，回傳 {參數名: [所有值]}，同一參數只列一次。
        """
        param_values = {}
        for param in parameters:
            for key in ['Entry_params', 'Exit_params']:
                if key in param and isinstance(param[key], list):
                    for d in param[key]:
                        if isinstance(d, dict):
                            for k, v in d.items():
                                if k not in param_values:
                                    param_values[k] = set()
                                param_values[k].add(str(v))
        # 轉成 list 並排序
        for k in param_values:
            param_values[k] = sorted(list(param_values[k]))
        return param_values 

    @staticmethod
    def parse_entry_exit_parameters(parameters: list):
        """
        分別展開 Entry_params/Exit_params，回傳 (entry_param_values, exit_param_values)
        """
        entry_param_values = {}
        exit_param_values = {}
        for param in parameters:
            if 'Entry_params' in param and isinstance(param['Entry_params'], list):
                for d in param['Entry_params']:
                    if isinstance(d, dict):
                        for k, v in d.items():
                            if k not in entry_param_values:
                                entry_param_values[k] = set()
                            entry_param_values[k].add(str(v))
            if 'Exit_params' in param and isinstance(param['Exit_params'], list):
                for d in param['Exit_params']:
                    if isinstance(d, dict):
                        for k, v in d.items():
                            if k not in exit_param_values:
                                exit_param_values[k] = set()
                            exit_param_values[k].add(str(v))
        for k in entry_param_values:
            entry_param_values[k] = sorted(list(entry_param_values[k]))
        for k in exit_param_values:
            exit_param_values[k] = sorted(list(exit_param_values[k]))
        return entry_param_values, exit_param_values 

    @staticmethod
    def parse_indicator_param_structure(parameters: list):
        """
        統計所有 entry/exit 下 indicator_type 及其所有參數名與值：
        回傳 {
            'entry': {indicator_type: {param: [值]}},
            'exit': {indicator_type: {param: [值]}}
        }
        """
        result = {'entry': {}, 'exit': {}}
        for param in parameters:
            for key, target in [('Entry_params', 'entry'), ('Exit_params', 'exit')]:
                if key in param and isinstance(param[key], list):
                    for d in param[key]:
                        if isinstance(d, dict):
                            indicator_type = str(d.get('indicator_type', 'Unknown'))
                            if indicator_type not in result[target]:
                                result[target][indicator_type] = {}
                            for k, v in d.items():
                                if k == 'indicator_type':
                                    continue
                                if k not in result[target][indicator_type]:
                                    result[target][indicator_type][k] = set()
                                result[target][indicator_type][k].add(str(v))
        # 轉成 list 並排序
        for target in result:
            for ind in result[target]:
                for k in result[target][ind]:
                    result[target][ind][k] = sorted(list(result[target][ind][k]))
        return result 

    @staticmethod
    def identify_strategy_groups(parameters: list) -> Dict[str, Any]:
        """
        識別策略分組，基於 Entry_params 和 Exit_params 的 indicator_type + strat_idx 組合
        
        Args:
            parameters: 參數列表
            
        Returns:
            Dict[str, Any]: 策略分組信息
        """
        strategy_groups = {}
        
        for i, param in enumerate(parameters):
            entry_strategies = []
            exit_strategies = []
            
            # 提取 Entry 策略信息
            if 'Entry_params' in param and isinstance(param['Entry_params'], list):
                for entry_param in param['Entry_params']:
                    if isinstance(entry_param, dict):
                        indicator_type = entry_param.get('indicator_type', 'Unknown')
                        strat_idx = entry_param.get('strat_idx', 'Unknown')
                        strategy_name = f"{indicator_type}{strat_idx}"
                        entry_strategies.append({
                            'indicator_type': indicator_type,
                            'strat_idx': strat_idx,
                            'strategy_name': strategy_name,
                            'full_params': entry_param
                        })
            
            # 提取 Exit 策略信息
            if 'Exit_params' in param and isinstance(param['Exit_params'], list):
                for exit_param in param['Exit_params']:
                    if isinstance(exit_param, dict):
                        indicator_type = exit_param.get('indicator_type', 'Unknown')
                        strat_idx = exit_param.get('strat_idx', 'Unknown')
                        strategy_name = f"{indicator_type}{strat_idx}"
                        exit_strategies.append({
                            'indicator_type': indicator_type,
                            'strat_idx': strat_idx,
                            'strategy_name': strategy_name,
                            'full_params': exit_param
                        })
            
            # 創建策略組合鍵
            if entry_strategies and exit_strategies:
                # 排序策略名稱以確保一致性
                entry_names = sorted([s['strategy_name'] for s in entry_strategies])
                exit_names = sorted([s['strategy_name'] for s in exit_strategies])
                
                strategy_key = f"Entry_{'+'.join(entry_names)}_Exit_{'+'.join(exit_names)}"
                
                if strategy_key not in strategy_groups:
                    strategy_groups[strategy_key] = {
                        'entry_strategies': entry_strategies,
                        'exit_strategies': exit_strategies,
                        'entry_names': entry_names,
                        'exit_names': exit_names,
                        'parameter_combinations': [],
                        'count': 0,
                        'display_name': f"Entry: {', '.join(entry_names)} | Exit: {', '.join(exit_names)}"
                    }
                
                # 添加參數組合
                strategy_groups[strategy_key]['parameter_combinations'].append(i)
                strategy_groups[strategy_key]['count'] += 1
        
        return strategy_groups 

    @staticmethod
    def analyze_strategy_parameters(parameters: list, strategy_key: str) -> Dict[str, Any]:
        """
        分析選中策略的可變參數，用於生成2D參數高原圖表
        
        支持多指標策略和動態參數識別
        
        Args:
            parameters: 參數列表
            strategy_key: 選中的策略鍵
            
        Returns:
            Dict[str, Any]: 參數分析結果
        """
        # 首先識別策略分組
        strategy_groups = DataImporterPlotter.identify_strategy_groups(parameters)
        
        if strategy_key not in strategy_groups:
            return {}
        
        strategy_info = strategy_groups[strategy_key]
        parameter_indices = strategy_info['parameter_combinations']
        
        # 獲取該策略的所有參數組合
        strategy_parameters = [parameters[i] for i in parameter_indices]
        
        # 分析可變參數（支持多指標策略）
        variable_params = {}
        fixed_params = {}
        
        # 收集 Entry 和 Exit 參數的鍵和值
        entry_param_values = {}
        exit_param_values = {}
        
        def parse_parameter_value(value):
            """
            解析參數值，識別並處理不同類型：
            1. 範圍值：如 "10:20:10" -> 解析為實際數值列表
            2. 逗號分隔值：如 "2,2.5,3" -> 解析為數值列表
            3. 單一數值：直接轉換為數值
            """
            if isinstance(value, str):
                # 檢查是否為範圍值 (start:end:step)
                if ':' in value and value.count(':') == 2:
                    try:
                        parts = value.split(':')
                        start, end, step = map(int, parts)
                        if start < end and step > 0:
                            # 生成範圍內的數值列表
                            return list(range(start, end + 1, step))
                    except (ValueError, TypeError):
                        pass
                
                # 檢查是否為逗號分隔值
                if ',' in value:
                    try:
                        # 嘗試轉換為浮點數列表
                        return [float(x.strip()) for x in value.split(',')]
                    except (ValueError, TypeError):
                        pass
                
                # 嘗試轉換為單一數值
                try:
                    if '.' in value:
                        return [float(value)]
                    else:
                        return [int(value)]
                except (ValueError, TypeError):
                    pass
            
            # 如果無法解析，返回原始值
            return [value]
        
        for param in strategy_parameters:
            # 分析 Entry_params（支持多個Entry指標）
            if 'Entry_params' in param:
                for entry_param in param['Entry_params']:
                    # 獲取指標類型和索引，用於區分不同指標的參數
                    indicator_type = entry_param.get('indicator_type', 'Unknown')
                    strat_idx = entry_param.get('strat_idx', 'Unknown')
                    indicator_key = f"{indicator_type}{strat_idx}"
                    
                    for key, value in entry_param.items():
                        if key not in ['indicator_type', 'strat_idx']:  # 排除固定字段
                            # 創建唯一的參數鍵，包含指標信息
                            entry_key = f"Entry_{indicator_key}_{key}"
                            if entry_key not in entry_param_values:
                                entry_param_values[entry_key] = set()
                            
                            # 解析參數值
                            parsed_values = parse_parameter_value(value)
                            for parsed_val in parsed_values:
                                entry_param_values[entry_key].add(parsed_val)
            
            # 分析 Exit_params（支持多個Exit指標）
            if 'Exit_params' in param:
                for exit_param in param['Exit_params']:
                    # 獲取指標類型和索引，用於區分不同指標的參數
                    indicator_type = exit_param.get('indicator_type', 'Unknown')
                    strat_idx = exit_param.get('strat_idx', 'Unknown')
                    indicator_key = f"{indicator_type}{strat_idx}"
                    
                    for key, value in exit_param.items():
                        if key not in ['indicator_type', 'strat_idx']:  # 排除固定字段
                            # 創建唯一的參數鍵，包含指標信息
                            exit_key = f"Exit_{indicator_key}_{key}"
                            if exit_key not in exit_param_values:
                                exit_param_values[exit_key] = set()
                            
                            # 解析參數值
                            parsed_values = parse_parameter_value(value)
                            for parsed_val in parsed_values:
                                exit_param_values[exit_key].add(parsed_val)
        
        # 合併 Entry 和 Exit 參數
        all_param_values = {**entry_param_values, **exit_param_values}
        
        # 判斷參數是否可變（有多個值）
        for key in all_param_values:
            if len(all_param_values[key]) > 1:
                # 轉換為排序的數值列表
                try:
                    sorted_values = sorted(all_param_values[key], key=lambda x: float(x) if isinstance(x, (int, float, str)) else 0)
                    variable_params[key] = sorted_values
                except (ValueError, TypeError):
                    # 如果無法排序，使用原始順序
                    variable_params[key] = list(all_param_values[key])
            else:
                fixed_params[key] = list(all_param_values[key])[0]
        
        # 轉換為排序列表
        variable_param_list = sorted(list(variable_params.keys()))
        
        return {
            'strategy_key': strategy_key,
            'strategy_info': strategy_info,
            'variable_params': variable_params,
            'fixed_params': fixed_params,
            'variable_param_list': variable_param_list,
            'total_combinations': len(strategy_parameters),
            'parameter_indices': parameter_indices
        } 

    def get_cache_stats(self) -> Dict[str, Any]:
        """獲取緩存統計信息"""
        return {
            'hits': self.cache_stats['hits'],
            'misses': self.cache_stats['misses'],
            'size': self.cache_stats['size'],
            'hit_rate': self.cache_stats['hits'] / (self.cache_stats['hits'] + self.cache_stats['misses']) if (self.cache_stats['hits'] + self.cache_stats['misses']) > 0 else 0
        }
    
    def get_strategy_analysis_cached(self, parameters: list, strategy_key: str) -> Dict[str, Any]:
        """
        獲取策略分析結果（帶緩存）
        
        Args:
            parameters: 參數列表
            strategy_key: 選中的策略鍵
            
        Returns:
            Dict[str, Any]: 參數分析結果
        """
        # 調試信息
        print(f"🔍 [DEBUG] 調用緩存方法: strategy_key={strategy_key}, parameters_len={len(parameters)}")
        print(f"🔍 [DEBUG] 緩存系統狀態: has_cache={hasattr(self, 'strategy_analysis_cache')}, cache_size={len(self.strategy_analysis_cache) if hasattr(self, 'strategy_analysis_cache') else 'N/A'}")
        
        # 創建緩存鍵
        cache_key = f"analysis_{strategy_key}_{len(parameters)}"
        print(f"🔍 [DEBUG] 緩存鍵: {cache_key}")
        
        # 檢查緩存
        if hasattr(self, 'strategy_analysis_cache') and cache_key in self.strategy_analysis_cache:
            self.cache_stats['hits'] += 1
            self.logger.debug(f"緩存命中: {strategy_key}")
            print(f"✅ [DEBUG] 緩存命中: {strategy_key}")
            return self.strategy_analysis_cache[cache_key]
        
        # 緩存未命中，執行分析
        self.cache_stats['misses'] += 1
        self.logger.debug(f"緩存未命中，執行分析: {strategy_key}")
        print(f"❌ [DEBUG] 緩存未命中，執行分析: {strategy_key}")
        
        # 使用靜態方法進行分析
        analysis = self.analyze_strategy_parameters(parameters, strategy_key)
        
        # 存入緩存
        if hasattr(self, 'strategy_analysis_cache'):
            self.strategy_analysis_cache[cache_key] = analysis
            self.cache_stats['size'] += 1
            print(f"💾 [DEBUG] 存入緩存: {cache_key}")
        else:
            print(f"⚠️ [DEBUG] 緩存系統未初始化！")
        
        # 如果緩存過大，清理舊的緩存項
        if hasattr(self, 'cache_stats') and self.cache_stats['size'] > 100:
            self._cleanup_cache()
        
        return analysis 
    
    def _cleanup_cache(self):
        """清理緩存，保留最近使用的項目"""
        if len(self.strategy_analysis_cache) > 50:
            # 簡單的緩存清理：刪除一半的緩存項
            keys_to_remove = list(self.strategy_analysis_cache.keys())[:25]
            for key in keys_to_remove:
                del self.strategy_analysis_cache[key]
            self.cache_stats['size'] = len(self.strategy_analysis_cache)
            self.logger.debug(f"緩存清理完成，當前大小: {self.cache_stats['size']}") 