"""統一資料讀取器：不執行畢業資格判定。"""
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parent
def list_programs():
    return json.loads((ROOT / "PROGRAM_REGISTRY.json").read_text(encoding="utf-8"))["programs"]
def load_program(program_id):
    for path in ROOT.glob("*.unified.json"):
        d = json.loads(path.read_text(encoding="utf-8"))
        if d["program_id"] == program_id:
            return d
    raise KeyError(f"Unknown program_id: {program_id}")
def check_supported(program_id):
    program = load_program(program_id)
    raise NotImplementedError(f"{program['department']} 尚未整合可執行的畢業審核規則引擎；不能據此判定畢業資格")
