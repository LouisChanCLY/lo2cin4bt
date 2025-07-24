"""
Predictor_loader.py

【功能說明】
------------------------------------------------------------
本模組為 Lo2cin4BT 數據預測與特徵工程模組，負責對行情數據進行特徵提取、預測欄位生成、機器學習前處理與差分處理，並確保數據結構與下游模組一致。

【關聯流程與數據流】
------------------------------------------------------------
- 由 DataLoader、DataImporter 或 BacktestEngine 調用，對原始數據進行特徵工程與預測欄位生成
- 處理後數據傳遞給 Calculator、Validator、BacktestEngine 等模組

```mermaid
flowchart TD
    A[DataLoader/DataImporter/BacktestEngine] -->|調用| B(Predictor_loader)
    B -->|特徵/預測欄位| C[數據DataFrame]
    C -->|傳遞| D[Calculator/Validator/BacktestEngine]
```

【主控流程細節】
------------------------------------------------------------
- 互動式載入 Excel/CSV 預測因子檔案，支援自動/手動時間欄位識別
- 自動對齊價格數據與預測因子，合併為單一 DataFrame
- 支援特徵差分（sub/div）、未來報酬率、技術指標等特徵生成
- 清洗與驗證數據，確保欄位型態與缺失值處理正確

【維護與擴充提醒】
------------------------------------------------------------
- 新增/修改特徵類型、欄位、差分邏輯時，請同步更新頂部註解與下游流程
- 若特徵工程流程、欄位結構有變動，需同步更新本檔案與下游模組
- 特徵生成公式如有調整，請同步通知協作者

【常見易錯點】
------------------------------------------------------------
- 預測因子檔案格式錯誤或缺失時間欄位會導致合併失敗
- 欄位型態不符或缺失值未處理會影響下游計算
- 差分選項未正確選擇會導致特徵異常

【範例】
------------------------------------------------------------
- loader = PredictorLoader(price_data)
  df = loader.load()
- df, diff_cols, used_series = loader.process_difference(df, '因子欄位名')

【與其他模組的關聯】
------------------------------------------------------------
- 由 DataLoader、DataImporter、BacktestEngine 調用，數據傳遞給 Calculator、Validator、BacktestEngine
- 需與下游欄位結構保持一致

【維護重點】
------------------------------------------------------------
- 新增/修改特徵類型、欄位、差分邏輯時，務必同步更新本檔案與下游模組
- 欄位名稱、型態需與下游模組協調一致

【參考】
------------------------------------------------------------
- pandas 官方文件
- Base_loader.py、DataValidator、Calculator_loader
- 專案 README
"""
import pandas as pd
import numpy as np
import os
import openpyxl
from .Validator_loader import DataValidator
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
import glob
import os
import re
console = Console()


class PredictorLoader:
    def __init__(self, price_data):
        """初始化 PredictorLoader，必須提供價格數據"""
        self.price_data = price_data

    def load(self):
        """載入預測因子數據，與價格數據對齊並合併"""
        try:
            console.print(Panel(
                "🟢 選擇價格數據來源\n🟢 輸入預測因子 🔵\n🔴 選擇差分預測因子 🔵\n🔴 導出合併後數據 🔵\n\n🔵可跳過\n\n"
                "你可以提供一份你認為能預測價格的「預測因子」數據檔案（如 Excel/CSV/JSON），\n"
                "例如：BTC ETF 資金流入數據、Google Trends、其他資產價格等。\n\n"
                "系統會自動對齊時間，並用這些因子做後續的統計分析與回測。\n"
                "你也可以輸入另一份價格數據，並選擇用哪個欄位作為預測因子（例如用 AAPL 股價預測 NVDA 股價）。\n\n"
                "如果留空，系統只會用剛才載入的價格數據，適合用於技術分析策略（如均線回測），\n"
                "並會直接跳過統計分析，進行回測。",
                title="[bold #dbac30]📊 數據載入 Dataloader 步驟：輸入預測因子[/bold #dbac30]",
                border_style="#dbac30"
            ))
            import glob
            import os
            import re
            # 自動偵測 import 目錄下的 Excel/CSV/JSON 檔案
            import_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'records', 'dataloader', 'import')
            file_patterns = ["*.xlsx", "*.xls", "*.csv", "*.json"]
            found_files = []
            for pat in file_patterns:
                found_files.extend(glob.glob(os.path.join(import_dir, pat)))
            found_files = sorted(found_files)
            file_choice = None
            if found_files:
                console.print("[bold #dbac30]偵測到以下可用的預測因子檔案：[/bold #dbac30]")
                for idx, f in enumerate(found_files, 1):
                    console.print(f"[bold white][{idx}][/bold white] {os.path.basename(f)}")
                while True:
                    console.print("[bold #dbac30]請輸入檔案編號，或直接輸入完整路徑（留空代表預設 1，僅用價格數據則請輸入 0）：[/bold #dbac30]")
                    user_input = input().strip()
                    if user_input == "" or user_input == "1":
                        file_path = found_files[0]
                        break
                    elif user_input == "0":
                        return "__SKIP_STATANALYSER__"
                    elif user_input.isdigit() and 1 <= int(user_input) <= len(found_files):
                        file_path = found_files[int(user_input)-1]
                        break
                    else:
                        console.print(Panel(
                            f"輸入錯誤，請重新輸入有效的檔案編號（1~{len(found_files)}），或輸入0僅用價格數據。",
                            title="[bold #8f1511]📊 數據載入 Dataloader[/bold #8f1511]",
                            border_style="#8f1511"
                        ))
            else:
                console.print("[bold #dbac30]未偵測到任何 Excel/CSV/JSON 檔案，請手動輸入檔案路徑（留空代表只用價格數據進行回測，並跳過統計分析）：[/bold #dbac30]")
                file_path = input().strip()
                if file_path == "":
                    return "__SKIP_STATANALYSER__"
            console.print("[bold #dbac30]請輸入時間格式（例如 %Y-%m-%d，或留空自動推斷）：[/bold #dbac30]")
            time_format = input().strip() or None

            # 檢查檔案存在
            if not os.path.exists(file_path):
                console.print(Panel(f"❌ 找不到文件 '{file_path}'", title="[bold #8f1511]📊 數據載入 Dataloader[/bold #8f1511]", border_style="#8f1511"))
                return None

            # 讀取檔案
            if file_path.endswith('.xlsx'):
                import pandas as pd
                data = pd.read_excel(file_path, engine='openpyxl')
            elif file_path.endswith('.csv'):
                import pandas as pd
                data = pd.read_csv(file_path)
            else:
                console.print(Panel("❌ 僅支持 .xlsx 或 .csv 格式", title="[bold #8f1511]📊 數據載入 Dataloader[/bold #8f1511]", border_style="#8f1511"))
                return None

            console.print(Panel(f"載入檔案 '{file_path}' 成功，原始欄位：{list(data.columns)}", title="[bold #8f1511]📊 數據載入 Dataloader[/bold #8f1511]", border_style="#dbac30"))

            # 標準化時間欄位
            time_col = self._identify_time_col(data.columns, file_path)
            if not time_col:
                console.print(Panel("❌ 無法確定時間欄位，程式終止", title="[bold #8f1511]📊 數據載入 Dataloader[/bold #8f1511]", border_style="#8f1511"))
                return None

            data = data.rename(columns={time_col: 'Time'})
            try:
                import pandas as pd
                data['Time'] = pd.to_datetime(data['Time'], format=time_format, errors='coerce')
                if data['Time'].isna().sum() > 0:
                    console.print(Panel(f"⚠️ {data['Time'].isna().sum()} 個時間值無效，將移除\n以下是檔案的前幾行數據：\n{data.head()}\n建議：請檢查 '{file_path}' 的 'Time' 欄，確保日期格式為 YYYY-MM-DD（如 2023-01-01）或其他一致格式", title="[bold #8f1511]📊 數據載入 Dataloader[/bold #8f1511]", border_style="#8f1511"))
                    data = data.dropna(subset=['Time'])
            except Exception as e:
                console.print(Panel(f"❌ 時間格式轉換失敗：{e}\n以下是檔案的前幾行數據：\n{data.head()}\n建議：請檢查 '{file_path}' 的 'Time' 欄，確保日期格式為 YYYY-MM-DD（如 2023-01-01）或其他一致格式", title="[bold #8f1511]📊 數據載入 Dataloader[/bold #8f1511]", border_style="#8f1511"))
                return None

            # 清洗數據
            from .Validator_loader import DataValidator
            validator = DataValidator(data)
            cleaned_data = validator.validate_and_clean()
            if cleaned_data is None or cleaned_data.empty:
                console.print(Panel("❌ 資料清洗後為空", title="[bold #8f1511]📊 數據載入 Dataloader[/bold #8f1511]", border_style="#8f1511"))
                return None

            # 時間對齊與合併
            merged_data = self._align_and_merge(cleaned_data)
            if merged_data is None:
                return None

            console.print(Panel(f"合併數據成功，行數：{len(merged_data)}", title="[bold #8f1511]📊 數據載入 Dataloader[/bold #8f1511]", border_style="#dbac30"))
            return merged_data

        except Exception as e:
            console.print(Panel(f"❌ PredictorLoader 錯誤：{e}", title="[bold #8f1511]📊 數據載入 Dataloader[/bold #8f1511]", border_style="#8f1511"))
            return None

    def get_diff_options(self, series):
        """獲取差分選項"""
        if (series == 0).any():
            return ['sub']  # 只能減數差分
        else:
            return ['sub', 'div']  # 兩種都可

    def apply_diff(self, series, diff_type):
        """應用差分"""
        if diff_type == 'sub':
            diff = series.diff()
        elif diff_type == 'div':
            diff = series.pct_change()
        else:
            raise ValueError('未知差分方式')
        return diff

    def process_difference(self, data, predictor_col):
        """
        處理預測因子的差分選項 - 自動判斷並執行差分
        
        Args:
            data: 原始數據
            predictor_col: 預測因子欄名
            
        Returns:
            tuple: (updated_data, diff_cols, used_series)
        """
        df = data.copy()
        factor_series = df[predictor_col]
        
        # 自動判斷差分選項
        has_zero = (factor_series == 0).any()
        diff_cols = [predictor_col]
        diff_col_map = {predictor_col: factor_series}
        
        if has_zero:
            console.print(Panel(f"‼️ 檢測到 {predictor_col} 包含 0 值，只能進行減數差分", title="[bold #8f1511]📊 數據載入 Dataloader[/bold #8f1511]", border_style="#8f1511"))
            diff_series = factor_series.diff().fillna(0)
            diff_col_name = predictor_col + '_diff_sub'
            diff_cols.append(diff_col_name)
            diff_col_map[diff_col_name] = diff_series
            used_series = diff_series
            diff_msg = f"已產生減數差分欄位 {diff_col_name}\n差分處理完成，新增欄位：{[col for col in diff_cols if col != predictor_col]}"
            console.print(Panel(diff_msg, title="[bold #8f1511]📊 數據載入 Dataloader[/bold #8f1511]", border_style="#dbac30"))
        else:
            console.print(Panel(f"{predictor_col} 無 0 值，同時產生減數差分和除數差分", title="[bold #8f1511]📊 數據載入 Dataloader[/bold #8f1511]", border_style="#dbac30"))
            diff_series_sub = factor_series.diff().fillna(0)
            diff_series_div = factor_series.pct_change().fillna(0)
            diff_col_name_sub = predictor_col + '_diff_sub'
            diff_col_name_div = predictor_col + '_diff_div'
            diff_cols.extend([diff_col_name_sub, diff_col_name_div])
            diff_col_map[diff_col_name_sub] = diff_series_sub
            diff_col_map[diff_col_name_div] = diff_series_div
            used_series = diff_series_sub
            diff_msg = f"已產生減數差分欄位 {diff_col_name_sub} 和除數差分欄位 {diff_col_name_div}\n差分處理完成，新增欄位：{[col for col in diff_cols if col != predictor_col]}"
            console.print(Panel(diff_msg, title="[bold #8f1511]📊 數據載入 Dataloader[/bold #8f1511]", border_style="#dbac30"))
            
        # 將所有欄位合併到 df
        for col, series in diff_col_map.items():
            df[col] = series
            
        # 顯示前10行數據表格
        preview = df.head(10)
        table = Table(title="目前數據（含差分欄位）", show_lines=True, border_style="#dbac30")
        for col in preview.columns:
            table.add_column(str(col), style="bold white")
        for _, row in preview.iterrows():
            table.add_row(*[
                f"[#1e90ff]{v}[/#1e90ff]" if isinstance(v, (int, float, complex)) and not isinstance(v, bool) else str(v)
                for v in row
            ])
        console.print(table)
        
        console.print(Panel(
            "⚠️ 目前僅支援單一預測因子進行回測與差分，未來將開放多預測因子功能，敬請期待！",
            title="[bold #dbac30]功能提醒[/bold #dbac30]",
            border_style="#dbac30"
        ))
        return df, diff_cols, used_series

    def _identify_time_col(self, columns, file_path):
        """識別時間欄位，若自動識別失敗則詢問用戶"""
        time_candidates = ['time', 'date', 'timestamp', 'Date', 'Time', 'Timestamp']
        for col in columns:
            if col.lower() in [c.lower() for c in time_candidates]:
                return col

        # 自動識別失敗，詢問用戶
        console.print(Panel(f"\n警告：無法自動識別 '{file_path}' 的時間欄位", title="[bold #8f1511]📊 數據載入 Dataloader[/bold #8f1511]", border_style="#8f1511"))
        console.print(Panel(f"可用欄位：{list(columns)}", title="[bold #8f1511]📊 數據載入 Dataloader[/bold #8f1511]", border_style="#dbac30"))
        console.print("[bold #dbac30]請指定時間欄位（輸入欄位名稱，例如 'Date'）：[/bold #dbac30]")
        while True:
            user_col = input().strip()
            if user_col in columns:
                return user_col
            console.print(Panel(f"錯誤：'{user_col}' 不在欄位中，請選擇以下欄位之一：{list(columns)}", title="[bold #8f1511]📊 數據載入 Dataloader[/bold #8f1511]", border_style="#8f1511"))

    def _align_and_merge(self, predictor_data):
        """與價格數據進行時間對齊並合併"""
        try:
            # 確保價格數據的 Time 為索引
            price_data = self.price_data.copy()
            if 'Time' not in price_data.index.names:
                if 'Time' in price_data.columns:
                    price_data = price_data.set_index('Time')
                else:
                    print("錯誤：價格數據缺少 'Time' 欄位或索引")
                    return None

            # 確保預測因子數據的 Time 為索引
            if 'Time' not in predictor_data.index.names:
                if 'Time' in predictor_data.columns:
                    predictor_data = predictor_data.set_index('Time')
                else:
                    print("錯誤：預測因子數據缺少 'Time' 欄位或索引")
                    return None

            # 時間對齊（inner join）
            merged = price_data.merge(predictor_data, left_index=True, right_index=True, how='inner')

            if merged.empty:
                print("錯誤：價格數據與預測因子數據無時間交集，無法合併")
                return None

            # 重置索引以保持一致性
            merged = merged.reset_index()

            return merged

        except Exception as e:
            print(f"時間對齊與合併錯誤：{e}")
            return None