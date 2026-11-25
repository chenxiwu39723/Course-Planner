# 數學系整合：補上傳說明

本包是現有 Course-Planner 專案的更新檔，須套用到專案根目錄。不是獨立完整專案。
基準：GitHub main 的 b1c2ec1b8d737e0e6fd88b1fc987dab7618f442b。

## 上傳

1. 解壓 Course_Planner_fix_upload.zip。
2. 在現有GitHub專案根目錄按 Add file → Upload files。
3. 一起拖入解壓後的檔案與資料夾；保持app.py位於根目錄，planner、data、static、templates等原有路徑不變。不要額外包一層Course_Planner_fix_upload資料夾。
4. 以新分支提交，確認變更清單，再建立Pull Request。

本包會更新網站程式及requirements.txt；原有PROGRAM_REGISTRY.json、全校*.unified.json、校方PDF、根目錄的獨立數學系程式均沿用現有檔案。

## 此包補上的內容

- 網頁：app.py、planner/graduation.py、models.py、solver.py、static/與templates/的整合。
- data/graduation/：數學系113規則與77學分案例。
- pdf_import/：原專案缺少的主系PDF匯入模組，支援registry的系所及組別辨識。
- requirements.txt：恢復原網站套件清單，再加入PyMuPDF，避免只安裝PDF套件而無法啟動Flask。
- tests/test_math_graduation_web.py：網頁與排課整合測試；使用不同檔名，避免與根目錄test_graduation.py衝突。
- test_pdf_import.py：現有PDF測試改用PyMuPDF內建中文字型，另測教育學程排除、組別別名與衝突拒絕。

## 啟動

在套用更新後的專案根目錄：

```bash
python3 -m pip install -r requirements.txt
```

macOS/Linux：

```bash
LLM_PROVIDER=mock python3 app.py
```

Windows PowerShell：

```powershell
$env:LLM_PROVIDER="mock"
python app.py
```

開啟 http://localhost:5001 ，在「我的資料」選數學系計算與資料科學組113入學，再按「載入77學分案例」。

預期：77/128學分、尚差51；115-1在修24學分另列。網頁能產生課表，並避開已完成及在修課號。

## 測試

```bash
python3 -m pytest -q
```

未提供原始審核PDF時預期142 passed、1 skipped。
指定既有案例PDF可跑完143項：

```bash
AUDIT_PDF='/path/audit.pdf' LLM_PROVIDER=mock python3 -m pytest -q
```

本次已驗證143項通過、JavaScript語法檢查通過；Flask測試客戶端確認首頁、2330門課程載入、77學分案例及3組排課回應。尚未完成實際瀏覽器點選測試。

## 功能界線

- 網頁畢業缺額引擎目前只支援數學系113入學；32份全校規則仍是資料建檔，不代表32系都能自動審核。
- 通用PDF匯入器只輸出匿名主系紀錄，不核定畢業資格、不自動套用統計學完成假設。
- 網頁案例維持使用者指定的統計學已完成版本；原始PDF來源狀態不會被修改。
- 課程資料目前為115-1，沒有推定115-2開課。
