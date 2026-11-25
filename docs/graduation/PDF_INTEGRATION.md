# 中大113學年度：全校規則＋學生畢業審核PDF匯入模組

> **2026-11-25 整理後的檔案位置**（本文以下內容為組員原始說明，路徑以此為準）：
> - `*.unified.json` → `data/graduation/rules/`；`PROGRAM_REGISTRY.json` → `data/graduation/`
> - 各學院 PDF → `data/graduation/sources/`
> - `pdf_import/` 位於專案根目錄，於根目錄執行 `python -m pdf_import.importer ...`
> - `test_pdf_import.py` → `tests/`（由 pytest 執行）
> - 數學系獨立腳本（`check_graduation.py` 等）→ `scripts/graduation/`
> - 文中的 `economics_original/` **未上傳到 repo**，目前只有 `data/graduation/economics_113_rules.json` 規則資料

## 內容
- `*.unified.json`、`PROGRAM_REGISTRY.json`：**32 份**全校系所／組別規則資料（資料建檔，並非32系已可執行畢業審核）。
- `economics_113.unified.json`：已用找回的原始 `economics_113_rules.json` 更新 `raw_rules`，保留其完整數位素養清單。
- `economics_original/`：原本經濟系 PDF → 畢業缺口檢查模組、虛構測試學生、17項原有測試。已修正原始 PDF 解析器的「未通過」判斷順序。
- `pdf_import/importer.py`：**全校通用主系 PDF 匯入器**，讀取 `PROGRAM_REGISTRY.json` 判別系所和113入學年度，輸出匿名主系課程 JSON。**不執行畢業資格判定。**
- `test_pdf_import.py`：新匯入器回歸測試。

## 安裝與使用（在本資料夾內執行）
```bash
python3 -m pip install pymupdf
python3 -m pdf_import.importer '/你的檔案/學生畢業審核表.pdf' -o main_major_courses.json
```
如果一個系有多個組別，請依 `PROGRAM_REGISTRY.json` 明確選定：
```bash
python3 -m pdf_import.importer '/你的檔案/學生畢業審核表.pdf' \
  --program-id math_datascience_113 -o main_major_courses.json
```

原始經濟系完整版的指令：
```bash
cd economics_original
python3 analyze_graduation.py --demo
python3 analyze_graduation.py '/你的檔案/經濟系主系審核表.pdf' --output report.json
```

## 主要安全設計
1. 只接受**文字型、版面格式相近的中央大學畢業審核紀錄表**；非這種PDF（例如掃描件、各系應修科目表）會拒絕或需人工確認。
2. 只擷取 `10000 - 主系`，遇到 `30000 - 雙主修` 即停止；不會把雙主修混入主系。
3. 根據PDF的入學年度、主系名稱對照全校 registry；同系多組別必須指定 `--program-id`。有疑義即拒絕。
4. `未通過` → failed、`通過` → passed、`未到` → in_progress、`停修/退選` → withdrawn；不明狀態拒絕匯入。
5. 不輸出姓名、學號、成績分數。但輸出的修課紀錄仍可能屬個人資料，網站應採授權與最短保存期限。
6. 同學期同課號重複列在不同審核分類時去重；不同學期重修保留紀錄，交由將來的規則引擎認列。

## 測試
```bash
python3 -m unittest test_pdf_import.py -v
cd economics_original
python3 -m unittest discover -p 'test_*.py' -v
```
目前：新匯入器7個測試通過；經濟系既有17個測試通過。新測試中的PDF為程式產生的**合成文字PDF**，並非32系實際學生PDF全面驗證。

## 尚待整合／待查
- 32系 JSON `raw_rules` 的 schema 各異，仍需通用畢業審核引擎才能核算實際學分缺額。
- 通用 PDF 匯入器只負責課程紀錄；**沒有接到組員的 Flask 網站**，也沒有把畢業資格判定改為全校通用。
- 不支援掃描圖片 PDF、雙主修主系外的審核、格式不一致的審核表。
- 系所和組別在PDF標頭以不同寫法表示時，可能需要補別名對照或手動確認。
- 新測試使用 NotoSansCJK 字型建立合成 PDF；如測試環境無此字型需調整測試 fixture 字型路徑。
