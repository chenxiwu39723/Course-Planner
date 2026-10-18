# 中大 AI 選課助理（NCU Course Planner）— 原型

> 參加「Microsoft × NCU AI Innovation Demo Day」的可測試原型。
> 核心理念：**AI 負責「理解」，演算法負責「排課」。**

## 專案動機

選課時最花時間的不是「算時間」，而是「讀規則」與「找到適合自己的課」：

- 限修條件、備註寫成一長串自由文字，要逐門讀。
- 想「不要早八、週五空堂、修點資料分析相關的通識」時，沒有工具能一次幫你把符合的課排進空堂。

本專案用語言模型處理**理解**的部分（讀懂限修文字、把白話偏好轉成條件、產生推薦理由），
而所有**時間相關的判斷**（衝堂、空堂、課表組合）一律交給確定性的演算法。

### 架構：AI 理解、演算法排課

| 層次 | 負責 | 模組 |
|---|---|---|
| **理解（AI）** | 限修條件解讀、白話偏好解析、語意搜尋、推薦理由 | `eligibility.py`、`preferences.py`、`search.py`、`llm_client.py` |
| **排課（演算法）** | 節次解析、衝堂判斷、空堂計算、回溯排課 | `timeslots.py`、`solver.py` |

關鍵保證：
- **語言模型不決定時間是否衝突**；衝堂/空堂/課表組合全由程式計算。
- 語言模型每個輸出都經 **JSON schema 驗證**，失敗時退回非 AI 的關鍵字規則，網頁不會因此當掉。
- 沒有 API 金鑰也能跑完整流程（`LLM_PROVIDER=mock`）。

---

## 資料來源

- **主要來源**：中大課程查詢系統 <https://cis.ncu.edu.tw/> 的公開資料（免登入）。
- **參考實作**：[NCU-Course-Finder-DataFetcher-v2](https://github.com/zetaraku/NCU-Course-Finder-DataFetcher-v2)
  （作者 **Raku Zeta**，**MIT 授權**）。本專案沿用其對課程查詢系統內部 API 的呼叫方式與部分解析邏輯。
- 取用規範：只抓免登入公開資料；**不抓取、不要求任何學生帳號、密碼、成績或個人選課紀錄**；
  請求間隔 ≥1 秒、設定可識別 User-Agent、原始回應快取於 `data/raw/`（不提交）。
- 資料可行性的完整檢查結果見 [`docs/data_feasibility.md`](docs/data_feasibility.md)。

### 與 NCU Course Finder 的差異

NCU Course Finder 著重「查詢與篩選」。本專案在其資料基礎上**新增**：

- **限修條件解讀**：把備註自由文字與分發條件解析成結構化條件，並比對使用者資料給出「符合／可能不符合／無法判斷」。
- **白話偏好解析**：把「不要早八、週五想空、想學資料分析」轉成可編輯的條件。
- **語意推薦**：以課名＋大綱／備註建向量索引，依主題相關度推薦。
- **自動排課**：在空堂內、不衝堂、符合學分與偏好的前提下，自動組出最多 3 組課表，
  並為每門推薦課附上**同時段可替換的其他課程**供挑選（貼近實際選課習慣）。排課範圍含週末；
  使用者未設學分上限時，預設最多加 6 門通識／語言課，避免排出不合理的滿堂課表。

---

## 安裝與執行

需要 Python 3.11+。

```bash
# 1) 安裝相依套件
pip install -r requirements.txt

# 2) 設定環境變數
cp .env.example .env     # 再依需要填入；完全不填也能用 sample + mock 跑
```

> 資料來源由環境變數 `DATA_SOURCE` 決定，**預設 `live`**（讀 `data/courses.json`）；
> 若該檔不存在會自動退回 `sample`（虛構範例資料），因此沒有資料也能啟動、不會當掉。
>
> 本 repo 已內含 **115 學年度第 1 學期**的真實課程（`data/courses.json`）與限修／分發條件快取，
> 因此 clone 後即可直接 `python app.py` 使用真實資料。唯語意索引（`data/embeddings.npy`）未納入版控，
> 首次需執行 `python scripts/build_index.py --source live` 才有語意推薦（未建立時自動退回關鍵字比對）。

### 免金鑰快速體驗（建議先這樣跑）

使用虛構的範例資料與關鍵字規則，不需網路、不需任何 API 金鑰：

```bash
# 建立範例資料的語意索引（首次或換資料來源時）
python scripts/build_index.py --source sample

# 以範例資料 + mock 模型啟動網站
DATA_SOURCE=sample LLM_PROVIDER=mock python app.py
# 開瀏覽器 → http://localhost:5001
```

> Windows PowerShell 設定環境變數的寫法：`$env:DATA_SOURCE="sample"; $env:LLM_PROVIDER="mock"; python app.py`

### 使用真實課程資料（需要網路）

```bash
# 1) 抓取本學期所有課程 → data/courses.json（遵守 1 秒間隔，約數分鐘）
python scripts/fetch_courses.py

# 2) 補完語種、通識領域、大綱摘要（fetch 只取基礎欄位）
#    --lang 為離線（由課名推導語種）；--outline 逐門抓大綱（連網，預設只補通識/語言課）
python scripts/enrich_courses.py --lang       # 離線，安全
python scripts/enrich_courses.py --outline    # 連網，約數分鐘

# 3) 批次解讀限修條件 → data/eligibility_cache.json
#    無金鑰：用 mock（關鍵字規則）；有 Azure 金鑰品質更高
LLM_PROVIDER=mock python scripts/parse_eligibility_batch.py --source live --max-calls 5000

# 4) 抓結構化「分發條件」→ data/distribution_cache.json（比備註可靠，網頁會自動合併）
python scripts/fetch_distribution.py --source live --targets ge-lang

# 5) 建立語意索引
python scripts/build_index.py --source live

# 6) 啟動網站（使用真實資料；DATA_SOURCE 預設即為 live，可省略）
python app.py
```

### 設定 Azure OpenAI（可選，提升解讀品質）

在 `.env` 填入後，把 `LLM_PROVIDER` 設為 `azure_openai`：

```
LLM_PROVIDER=azure_openai
AZURE_OPENAI_ENDPOINT=https://<your>.openai.azure.com/
AZURE_OPENAI_API_KEY=<your-key>
AZURE_OPENAI_DEPLOYMENT=<deployment-name>
```

金鑰只存在後端 `.env`（已列入 `.gitignore`），前端 JavaScript 不含任何金鑰。

---

## 網頁功能

三步驟流程（使用者資料只存瀏覽器 sessionStorage）：

1. **我的資料**：系所、年級、已修課程、要固定的課（可用關鍵字＋**類別／通識領域**篩選後加入）。
2. **我的偏好**：一個文字框輸入白話需求，解析成**可編輯的條件**（學分、避開時段、想空的星期、主題、通識領域、語言）後再送排課。
3. **推薦結果**：週課表格線圖（固定課／推薦課／空堂／避開以不同顏色與底紋區分、連續節次合併、週末有課才顯示），
   最多 3 組方案可切換；每門推薦課顯示推薦理由、資格狀態與原始備註，並可展開**同時段其他可選**課程直接改選。

介面細節：頁首顯示學年期與資料更新日；`mock` 模式或語意索引未建時會明確標示；桌面為左輸入／右課表雙欄、手機單欄；
主要互動（頁籤、展開、刪除）支援鍵盤操作與 `aria-label`。

---

## 測試

```bash
python -m pytest -q      # 目前 90 passed
```

涵蓋：節次解析、衝堂判斷、空堂計算、solver 不產生衝堂課表、限修條件解析與三狀態比對、
白話偏好解析、語意搜尋與推薦理由把關，以及評估模組的比對邏輯。

---

## 專案結構（重點）

```
app.py                     Flask 後端（6 條 API）
planner/
  models.py                課程資料模型
  timeslots.py             節次/時間、衝堂、空堂
  data_loader.py           DATA_SOURCE=live|sample 載入
  solver.py                候選篩選 + 回溯排課
  eligibility.py           限修條件解析與比對（備註＋分發條件合併）
  distribution.py          結構化分發條件解析
  preferences.py           白話偏好解析
  syllabus.py              課程大綱解析（ge_domain/摘要）
  search.py                語意搜尋 + 推薦理由
  llm_client.py            LLM 統一封裝（azure_openai / mock）
scripts/
  check_data_source.py     階段 0：資料可行性檢查
  fetch_courses.py         階段 1：抓取並正規化課程
  enrich_courses.py        補完 language/ge_domain/syllabus_text
  parse_eligibility_batch.py 階段 3：離線批次解讀限修（備註）
  fetch_distribution.py    階段 3：抓結構化分發條件（比備註可靠）
  build_index.py           階段 4：建立語意索引
data/
  sample_courses.json      52 門虛構範例課程（is_sample=true）
eval/
  preference_cases.json       20 句白話偏好與正確條件
  eval_preferences.py         白話偏好解析正確率
  make_eligibility_labels.py  產生限修條件待標註空白檔
  eligibility_labels.csv      待標註檔（50 門，組員人工填寫）
  eval_eligibility.py         限修條件解析正確率
outputs/eval/              評估報表（README 數字來源）
templates/ static/         前端
tests/                     pytest
docs/data_feasibility.md   階段 0 結果
```

目前資料規模：真實課程 **2330 門**（**115 學年度第 1 學期**，semester 代碼 `1151`）、
範例課程 **52 門**、限修快取 **2210 門**（以 mock/關鍵字產生）。網頁頁首會顯示目前載入的學年期與資料更新日期。
補完欄位（`scripts/enrich_courses.py`）：語種 **102 門**（由課名推導）、通識領域與大綱摘要各 **186 門**
（通識＋語言課，逐門抓大綱）。

---

## 評估（eval/）

評估數字一律來自 `outputs/eval/` 的實際輸出（本腳本產生），不填寫示意數字。

### 白話偏好解析正確率

`eval/preference_cases.json` 收 20 句白話偏好與團隊判定的正確條件；
`eval/eval_preferences.py` 對每句呼叫解析器，逐欄位比對正確率。

```bash
LLM_PROVIDER=mock python eval/eval_preferences.py     # 免金鑰（關鍵字規則）
# 有 Azure 金鑰：LLM_PROVIDER=azure_openai python eval/eval_preferences.py
```

**目前結果（`LLM_PROVIDER=mock`，關鍵字規則，20 筆，見 [`outputs/eval/preferences_report.md`](outputs/eval/preferences_report.md)）：**

| 欄位 | 正確率 |
|---|---|
| `avoid_periods`（避開時段） | 100.0% |
| `free_days`（想空的星期） | 100.0% |
| `min_credits` / `max_credits`（學分） | 95.0% / 95.0% |
| `languages`（語言） | 100.0% |
| `topics`（主題關鍵詞） | 50.0% |
| `ge_domains`（通識領域） | 85.0% |
| **平均欄位正確率** | **89.3%** |
| 整句全對率（所有欄位皆正確） | 45.0%（9/20） |

這組數字正好印證「AI 理解、演算法排課」：**時段、空堂、學分、語言等結構化欄位**用確定性規則就幾近全對；
而 **`topics`、`ge_domains` 這類需要語意理解的欄位**是關鍵字規則的弱點（主題常把贅字一起抓進去、
通識領域完全無法辨識），正是改用語言模型能明顯補強之處。報表亦列出每筆錯誤案例供對照。

### 限修條件解析正確率

依規範由組員**人工標註** 50 筆備註的正確條件後再計算：

```bash
python eval/make_eligibility_labels.py --source live   # 1) 產生待標註空白檔（已附一份）
# 2) 組員填寫 eval/eligibility_labels.csv 的 label_* 欄位（列表以「|」分隔）
LLM_PROVIDER=mock python eval/eval_eligibility.py      # 3) 計算各欄位正確率
```

`eval/eval_eligibility.py` 各欄位（`min_grade`、`allowed_depts`、`prerequisites`、`other_conditions`）
分開計算正確率並列出錯誤案例，輸出至 `outputs/eval/eligibility_report.md`。

> 目前 `eval/eligibility_labels.csv` 為**待標註空白檔**（取自真實資料 50 門、`random_state=42` 可重現），
> 人工標註完成前尚無正確率數字；標註後執行上述步驟即會自動填出。

---

## 隱私說明

- **不收集任何帳號、密碼、成績或個人選課紀錄**；僅使用課程查詢系統的公開課程資料。
- 使用者在網頁輸入的系所、年級、已修課程只存於**瀏覽器 sessionStorage**，不傳回伺服器保存。
- API 金鑰只存於後端 `.env`，不出現在前端程式碼或回應中。

---

## 生成式 AI 使用聲明

本專案程式碼由 **Claude Code 協助撰寫**，內容由團隊審閱並負責。
執行階段的語言模型僅用於「理解」類工作（解讀限修文字、解析白話偏好、產生推薦理由），
所有時間與衝堂判斷由確定性程式完成。

---

## 限制

- **非官方工具**，資料可能與選課系統不同步，一切以課程查詢系統與選課系統為準。
- **限修條件解讀可能出錯**；判定為「符合」不代表一定選得到。
- **擋修、人數上限、抽籤、加簽**等規則不一定反映在公開資料中。
- 大綱若無法取得，語意推薦只依課名與備註，準確度較低。
- 真實資料的 `ge_domain`、`syllabus_text` 目前僅補完**通識與語言課（各 186 門）**；
  其他系所課程若要語意推薦，需再對該範圍跑 `scripts/enrich_courses.py --outline --targets all`。
- 節次 `E`、`F`（深夜）的對應時間為依規律推得，實作前應再向課務組最新說明確認。

---

本工具非學校官方系統，選課前請以[課程查詢系統](https://cis.ncu.edu.tw/)與選課系統為準。
