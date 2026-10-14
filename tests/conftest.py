# -*- coding: utf-8 -*-
"""讓 pytest 無論從哪裡執行都能 import planner 套件。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
