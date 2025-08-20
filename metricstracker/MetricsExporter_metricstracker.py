"""
MetricsExporter_metricstracker.py

【功能說明】
------------------------------------------------------------
本模組為 Lo2cin4BT 績效分析框架的績效指標導出工具，負責將績效分析結果導出為多種格式，支援 CSV、Excel、JSON 等格式，便於後續分析。

【流程與數據流】
------------------------------------------------------------
- 由 BaseMetricTracker 調用，導出績效分析結果
- 導出結果供用戶或下游模組分析

```mermaid
flowchart TD
    A[BaseMetricTracker] -->|調用| B[MetricsExporter]
    B -->|導出結果| C[CSV/Excel/JSON]
```

【維護與擴充重點】
------------------------------------------------------------
- 新增/修改導出格式、欄位時，請同步更新頂部註解與下游流程
- 若導出結構有變動，需同步更新本檔案與上游模組
- 導出格式如有調整，請同步通知協作者

【常見易錯點】
------------------------------------------------------------
- 導出格式錯誤或欄位缺失會導致導出失敗
- 檔案權限不足會導致寫入失敗
- 數據結構變動會影響下游分析

【範例】
------------------------------------------------------------
- exporter = MetricsExporter()
  exporter.export_metrics(metrics, format='csv')

【與其他模組的關聯】
------------------------------------------------------------
- 由 BaseMetricTracker 調用，導出結果供用戶或下游模組使用
- 需與上游模組的數據結構保持一致

【參考】
------------------------------------------------------------
- pandas 官方文件
- Base_metricstracker.py、MetricsCalculator_metricstracker.py
- 專案 README
"""

import json
import os

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from rich.console import Console
from rich.panel import Panel

from .MetricsCalculator_metricstracker import MetricsCalculatorMetricTracker

console = Console()


class MetricsExporter:
    @staticmethod
    def add_drawdown_bah(df):
        df = df.copy()
        equity = df["Equity_value"]
        roll_max = equity.cummax()
        df["Drawdown"] = (equity - roll_max) / roll_max
        if "Close" in df.columns:
            initial_equity = equity.iloc[0]
            initial_price = df["Close"].iloc[0]
            df["BAH_Equity"] = initial_equity * (df["Close"] / initial_price)
            df["BAH_Return"] = df["BAH_Equity"].pct_change().fillna(0)
            # 新增 BAH_Drawdown
            bah_roll_max = df["BAH_Equity"].cummax()
            df["BAH_Drawdown"] = (df["BAH_Equity"] - bah_roll_max) / bah_roll_max
        return df

    @staticmethod
    def export(df, orig_parquet_path, time_unit, risk_free_rate):
        orig_table = pq.read_table(orig_parquet_path)
        orig_meta = orig_table.schema.metadata or {}
        # 統一 batch_metadata 寫入，不論單/多策略
        grouped = (
            df.groupby("Backtest_id") if "Backtest_id" in df.columns else [(None, df)]
        )
        batch_metadata = []
        all_df = []
        # 先讀取舊的 batch_metadata
        old_batch_metadata = []
        if b"batch_metadata" in orig_meta:
            try:
                old_batch_metadata = json.loads(orig_meta[b"batch_metadata"].decode())
            except Exception:
                pass
        for Backtest_id, group in grouped:
            group = MetricsExporter.add_drawdown_bah(group)
            all_df.append(group)
            calc = MetricsCalculatorMetricTracker(group, time_unit, risk_free_rate)
            strategy_metrics = calc.calc_strategy_metrics()
            bah_metrics = calc.calc_bah_metrics()
            meta = {"Backtest_id": Backtest_id} if Backtest_id is not None else {}
            for k in strategy_metrics:
                meta[k] = strategy_metrics[k]
            for k in bah_metrics:
                meta[k] = bah_metrics[k]
            batch_metadata.append(meta)
        # 合併舊的 batch_metadata（欄位級合併）
        if old_batch_metadata:
            old_map = {
                m["Backtest_id"]: m for m in old_batch_metadata if "Backtest_id" in m
            }
            new_map = {
                m["Backtest_id"]: m for m in batch_metadata if "Backtest_id" in m
            }
            all_ids = set(old_map.keys()) | set(new_map.keys())
            merged = []
            for bid in all_ids:
                if bid in old_map and bid in new_map:
                    merged_dict = dict(old_map[bid])
                    merged_dict.update(new_map[bid])  # 新欄位覆蓋舊欄位
                    merged.append(merged_dict)
                elif bid in new_map:
                    merged.append(new_map[bid])
                else:
                    merged.append(old_map[bid])
            batch_metadata = merged
        # 過濾空的 DataFrame 以避免 FutureWarning
        filtered_df = []
        for df_item in all_df:
            if not df_item.empty and len(df_item.columns) > 0:
                # 清理 DataFrame：移除全為 NA 的列
                cleaned_df = df_item.dropna(axis=1, how="all")
                if not cleaned_df.empty:
                    filtered_df.append(cleaned_df)

        if filtered_df:
            # 使用更安全的 concat 方式
            try:
                df = pd.concat(filtered_df, ignore_index=True, sort=False)
            except Exception:
                # 如果 concat 失敗，嘗試逐個合併
                df = filtered_df[0]
                for df_item in filtered_df[1:]:
                    df = pd.concat([df, df_item], ignore_index=True, sort=False)
        else:
            df = pd.DataFrame()
        new_meta = dict(orig_meta)
        new_meta = {
            k if isinstance(k, bytes) else str(k).encode(): v
            for k, v in new_meta.items()
        }
        new_meta[b"batch_metadata"] = json.dumps(
            batch_metadata, ensure_ascii=False
        ).encode()
        table = pa.Table.from_pandas(df)
        table = table.replace_schema_metadata(new_meta)
        orig_name = os.path.splitext(os.path.basename(orig_parquet_path))[0]
        out_dir = os.path.join(
            os.path.dirname(os.path.dirname(orig_parquet_path)), "metricstracker"
        )
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{orig_name}_metrics.parquet")
        pq.write_table(table, out_path)

        console.print(
            Panel(
                f"batch_metadata 已計算並輸出：\n{out_path}",
                title="[bold #8f1511]🚦 Metricstracker 交易分析[/bold #8f1511]",
                border_style="#dbac30",
            )
        )

        # 立即讀回檢查
        table2 = pq.read_table(out_path)
        table2.schema.metadata

        console.print(
            Panel(
                "✅ 交易績效分析完成！",
                title="[bold #8f1511]🚦 Metricstracker 交易分析[/bold #8f1511]",
                border_style="#dbac30",
            )
        )
