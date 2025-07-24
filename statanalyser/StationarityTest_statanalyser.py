"""
StationarityTest_statanalyser.py

【功能說明】
------------------------------------------------------------
本模組為 Lo2cin4BT 統計分析子模組，專責對時序資料進行定態性檢定（如 ADF、KPSS），判斷資料是否為平穩過程，協助決定是否需進行差分或轉換以利後續建模。

【關聯流程與數據流】
------------------------------------------------------------
- 由 Base_statanalyser 繼承，接收主流程傳入的資料
- 檢定結果傳遞給 ReportGenerator_statanalyser 產生報表
- 主要數據流：

```mermaid
flowchart TD
    A[main.py/主流程] -->|調用| B[StationarityTest_statanalyser]
    B -->|檢定結果| C[ReportGenerator_statanalyser]
```

【主控流程細節】
------------------------------------------------------------
- analyze() 為主入口，執行 ADF、KPSS 等平穩性檢定
- 同時對因子欄位與收益率欄位進行檢定，並回傳詳細結果
- 根據檢定結果自動給出建議（如建議差分、直接建模等）
- 檢定結果以 dict 格式回傳，供報表模組與下游流程使用

【維護與擴充提醒】
------------------------------------------------------------
- 新增檢定方法、參數時，請同步更新 analyze() 及頂部註解
- 若數據結構或欄位有變動，需同步調整與 Base_statanalyser、ReportGenerator_statanalyser 的介面
- 檢定指標、臨界值如有調整，請於 README 詳列

【常見易錯點】
------------------------------------------------------------
- 檢定樣本數不足時，結果不具統計意義
- 檢定參數設置錯誤會導致判斷失準
- 統計結果格式不符會影響下游報表產生
- 欄位名稱錯誤或資料為常數會導致檢定失敗

【範例】
------------------------------------------------------------
- test = StationarityTest(data, predictor_col="因子欄位", return_col="收益率欄位")
  result = test.analyze()

【與其他模組的關聯】
------------------------------------------------------------
- 由主流程或 Base_statanalyser 調用，檢定結果傳遞給 ReportGenerator_statanalyser
- 依賴 pandas、statsmodels 等第三方庫

【維護重點】
------------------------------------------------------------
- 新增/修改檢定方法、參數時，務必同步更新本檔案、Base_statanalyser 及 README
- 檢定結果格式需與 ReportGenerator_statanalyser 保持一致

【參考】
------------------------------------------------------------
- 詳細檢定規範與指標定義請參閱 README
- 其他模組如有依賴本模組，請於對應檔案頂部註解標明
"""
import pandas as pd
import numpy as np
from .Base_statanalyser import BaseStatAnalyser
from statsmodels.tsa.stattools import adfuller, kpss
import warnings
from typing import Dict
from rich.panel import Panel
from rich.console import Console
from rich.table import Table

class StationarityTest(BaseStatAnalyser):
    """平穩性檢驗模組"""

    def __init__(
            self,
            data: pd.DataFrame,
            predictor_col: str,
            return_col: str
    ):
        super().__init__(data, predictor_col, return_col)

    def analyze(self) -> Dict:
        step_content = (
            "🟢 選擇用於統計分析的預測因子\n"
            "🟢 收益率相關性檢驗[自動]\n"
            "🟢 ADF/KPSS 平穩性檢驗[自動]\n"
            "🔴 ACF/PACF 自相關性檢驗[自動]\n"
            "🔴 生成 ACF 或 PACF 互動圖片\n"
            "🔴 統計分佈檢驗[自動]\n"
            "🔴 季節性檢驗[自動]\n\n"
            f"2. '{self.predictor_col}' 平穩性檢驗（ADF/KPSS）\n"
            "檢驗功能：判斷序列是否為平穩過程，適合用於傳統時間序列建模。如序列非平穩，很多模型如自回歸 (AR)、ARIMA 模型、線性回歸分析等效果將大打折扣。\n"
            "成功/失敗標準：ADF p<0.05 為平穩，KPSS p>0.05 為平穩。"
        )
        console = Console()
        # 步驟說明
        console.print(Panel(
            step_content,
            title="[bold #dbac30]統計分析 StatAnalyser 步驟：收益率相關性檢驗[自動][/bold #dbac30]",
            border_style="#dbac30"
        ))
        # 執行檢定並存結果
        def run_stationarity_tests(series):
            result = {}
            try:
                adf_stat, adf_p, _, _, _, _ = adfuller(series.dropna(), autolag='AIC')
                result['adf_stat'] = adf_stat
                result['adf_p'] = adf_p
                result['adf_stationary'] = adf_p < 0.05
            except Exception:
                result['adf_stat'] = 'N/A'
                result['adf_p'] = 'N/A'
                result['adf_stationary'] = False
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    kpss_stat, kpss_p, _, _ = kpss(series.dropna(), nlags='auto')
                result['kpss_stat'] = kpss_stat
                result['kpss_p'] = kpss_p
                result['kpss_stationary'] = kpss_p > 0.05
            except Exception:
                result['kpss_stat'] = 'N/A'
                result['kpss_p'] = 'N/A'
                result['kpss_stationary'] = False
            return result
        self.results['predictor'] = run_stationarity_tests(self.data[self.predictor_col])
        self.results['return'] = run_stationarity_tests(self.data[self.return_col])
        # 結果數據
        pred_adf = self.results['predictor'].get('adf_stat', 'N/A')
        pred_adf_p = self.results['predictor'].get('adf_p', 'N/A')
        pred_kpss = self.results['predictor'].get('kpss_stat', 'N/A')
        pred_kpss_p = self.results['predictor'].get('kpss_p', 'N/A')
        ret_adf = self.results['return'].get('adf_stat', 'N/A')
        ret_adf_p = self.results['return'].get('adf_p', 'N/A')
        ret_kpss = self.results['return'].get('kpss_stat', 'N/A')
        ret_kpss_p = self.results['return'].get('kpss_p', 'N/A')
        df = pd.DataFrame({
            '指標': ['因子ADF', '因子KPSS', '收益率ADF', '收益率KPSS'],
            '統計量': [pred_adf, pred_kpss, ret_adf, ret_kpss],
            'p值': [pred_adf_p, pred_kpss_p, ret_adf_p, ret_kpss_p]
        })
        # 直接用 Rich Table 輸出
        table = Table(title="平穩性檢驗結果", border_style="#dbac30", show_lines=True)
        for col in df.columns:
            table.add_column(str(col), style="bold white")
        for _, row in df.iterrows():
            row_cells = []
            for v in row:
                if isinstance(v, (int, float)) or (isinstance(v, str) and v.replace('.','',1).isdigit()):
                    row_cells.append(f"[#1e90ff]{v}[/#1e90ff]")
                else:
                    row_cells.append(str(v))
            table.add_row(*row_cells)
        console.print(table)
        # 判斷
        pred_adf_bool = self.results['predictor'].get('adf_stationary', False)
        pred_kpss_bool = self.results['predictor'].get('kpss_stationary', False)
        ret_adf_bool = self.results['return'].get('adf_stationary', False)
        ret_kpss_bool = self.results['return'].get('kpss_stationary', False)
        summary = (
            f"因子ADF平穩：{'[bold green]是[/bold green]' if pred_adf_bool else '[bold red]否[/bold red]'}，"
            f"KPSS平穩：{'[bold green]是[/bold green]' if pred_kpss_bool else '[bold red]否[/bold red]'}\n"
            f"收益率ADF平穩：{'[bold green]是[/bold green]' if ret_adf_bool else '[bold red]否[/bold red]'}，"
            f"KPSS平穩：{'[bold green]是[/bold green]' if ret_kpss_bool else '[bold red]否[/bold red]'}\n"
        )
        if pred_adf_bool and pred_kpss_bool:
            summary += "[bold #dbac30]因子序列平穩[/bold #dbac30]，[bold]適合用於傳統時間序列建模（如ARMA/ARIMA）[/bold]\n"
        else:
            summary += "[bold red]因子序列非平穩[/bold red]，[bold]建議差分或轉換後再建模[/bold]\n"
        if ret_adf_bool and ret_kpss_bool:
            summary += "[bold #dbac30]收益率序列平穩[/bold #dbac30]，[bold green]可直接用於收益率建模[/bold green]"
        else:
            summary += "[bold red]收益率序列非平穩[/bold red]，[bold]建議差分或轉換後再建模[/bold]"
        # 結論用紅色 Panel
        console.print(Panel(summary, title="[bold #8f1511]🔬 統計分析 StatAnalyser[/bold #8f1511]", border_style="#dbac30"))
        return self.results