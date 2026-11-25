# -*- coding: utf-8 -*-
"""讓 pytest 無論從哪裡執行都能 import planner 套件。"""
import os
import sys
from pathlib import Path

# 不讀本機 .env：測試結果不應受個人金鑰或 LLM_PROVIDER 設定影響
os.environ["PLANNER_SKIP_DOTENV"] = "1"

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
