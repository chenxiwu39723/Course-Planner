"""NCU 選課助理核心套件。

匯入時自動讀取專案根目錄的 .env（app.py 與 scripts/ 都會 import planner）。
已在終端機設定的環境變數優先，不會被 .env 覆蓋。
測試設 PLANNER_SKIP_DOTENV=1 略過，避免本機 .env 的金鑰讓測試真的呼叫 API。
"""
import os
from pathlib import Path

if not os.environ.get("PLANNER_SKIP_DOTENV"):
    try:
        from dotenv import load_dotenv
    except ImportError:  # 未安裝 python-dotenv 時只用終端機環境變數
        pass
    else:
        load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
