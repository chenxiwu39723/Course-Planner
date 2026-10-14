# 專案：中大 AI 選課助理（NCU Course Planner）原型

## 專案定位
參加「Microsoft × NCU AI Innovation Demo Day」（收件截止 2026/12/4，以 PowerPoint 提案簡報為主，網頁為 demo 補充）。
目前階段：**可測試的原型**，優先確認資料可行性與核心流程，不追求完整介面。

### 核心原則：AI 負責「理解」，演算法負責「排課」
- 語言模型只做：解讀限修條件文字、把白話偏好轉成條件、產生推薦理由
- 衝堂判斷、空堂計算、課表組合一律由確定性的程式完成，語言模型不得決定時間是否衝突
- 語言模型的每個輸出都要經過 JSON schema 驗證；失敗時退回非 AI 的做法（例如關鍵字篩選），網頁不得因此當掉

## 資料來源
- 主要來源：中大課程查詢系統 https://cis.ncu.edu.tw/ 的公開資料
- 參考實作：NCU Course Finder 的資料抓取程式（作者 Raku Zeta，MIT 授權）
  https://github.com/zetaraku/NCU-Course-Finder-DataFetcher-v2
  - 該程式使用課程查詢系統的內部 API，輸出 `data/dynamic/all.json`
  - 該程式以 Node.js 撰寫（`npm run update`），最後更新時間可能較久，**不保證 API 目前仍可用**
- 規範：
  - 只抓不需登入的公開資料；不得抓取或要求學生帳號密碼、成績、個人選課紀錄
  - 請求間隔至少 1 秒，設定可識別的 User-Agent，原始回應快取在 `data/raw/`，避免重複請求
  - `data/raw/` 加入 .gitignore
  - 若沿用參考實作的程式碼或邏輯，README 必須標示出處與 MIT 授權

## 階段 0：資料可行性檢查（必須最先完成，完成後停下來回報）
1. clone 參考實作到 `vendor/`（加入 .gitignore），閱讀 `src/` 找出：
   - 實際呼叫的 API 網址、參數、回應格式
   - 能取得的欄位（課號、名稱、教師、學分、時間、教室、人數、備註、開課系所、課程類別、授課語言、大綱連結等）
2. 撰寫 `scripts/check_data_source.py`，實際請求本學期資料一次，並檢查：
   - API 目前是否仍可使用
   - 備註或限修條件欄位是否存在、是否為自由文字
   - 課程大綱是否能在未登入的情況下取得；若可以，抓 3 門課的大綱測試
   - 通識課的領域分類、語言課的識別方式
   - 節次代碼的完整清單與對應時間（不要自行假設，從資料或官方頁面確認）
3. 結果寫入 `docs/data_feasibility.md`，包含：可取得欄位表、範例資料（3–5 筆）、無法取得的項目與替代方案
4. **完成後停止，等待我確認再進入下一階段**

## 階段 1：資料整理
- `scripts/fetch_courses.py`：抓取本學期所有課程，正規化後輸出 `data/courses.json`
- 統一資料格式（`planner/models.py`，使用 dataclass 或 pydantic）：
  `id, code, name, teacher, dept, credits, category, ge_domain, is_language, language, times[(day, period)], classroom, capacity, notes, syllabus_url, syllabus_text`
- `planner/timeslots.py`：解析節次代碼，提供「兩門課是否衝堂」與「一週空堂格」的函式
- `data/sample_courses.json`：約 50 門**虛構**範例課程，供資料抓取失敗時開發介面使用
  - 每筆加上 `"is_sample": true`，網頁顯示時需標示「範例資料」
- 環境變數 `DATA_SOURCE=live|sample` 決定載入哪份資料（預設 `sample`，確保沒網路也能測試）

## 階段 2：非 AI 核心功能（先完成並寫測試）
- 空堂計算：輸入已固定的課號清單，回傳一週空堂格
- 候選課篩選：在空堂內可上、不衝堂的通識課與語言課，可依通識領域、學分篩選
- 課表組合 `planner/solver.py`：
  - 硬性條件：不衝堂、總學分上下限、避開指定節次、必須包含的課
  - 以回溯搜尋產生最多 3 組課表，依分數排序（分數 = 偏好符合度 + 語意相關度）
  - 搜尋需設上限（例如 2 秒或 10,000 個節點），超過時回傳目前最佳結果
- `tests/`：以 pytest 測試節次解析、衝堂判斷、空堂計算、solver 不會產生衝堂課表

## 階段 3：限修條件解讀（AI）
- `planner/eligibility.py` + `scripts/parse_eligibility_batch.py`
- **離線批次處理**：對所有備註非空的課程呼叫一次語言模型，結果快取至 `data/eligibility_cache.json`；網頁使用時不即時呼叫
- 輸出格式：
  `{"min_grade": 3, "allowed_depts": ["通訊系"], "prerequisites": ["微積分"], "other_conditions": ["需經教師同意"], "unparsed": "...", "confidence": "high|medium|low"}`
- 比對使用者資料（系所、年級、已修課程）後，判定為三種狀態：**符合／可能不符合／無法判斷**，並附上原因
- 網頁必須同時顯示原始備註文字，讓使用者自行確認
- 文字中沒有提到的條件不得自行推論；不確定時歸為「無法判斷」

## 階段 4：白話偏好與語意推薦（AI）
- `planner/preferences.py`：把使用者輸入的白話文字（例如「不要早八、週五想空著、想學資料分析相關、通識 4 學分」）轉成條件 JSON：
  `{"avoid_periods": [...], "free_days": [...], "min_credits": 16, "max_credits": 20, "topics": ["資料分析"], "ge_domains": [...], "languages": [...]}`
  - 解析後的條件要顯示在網頁上，讓使用者確認或手動修改，再送進 solver
- `planner/search.py`：課程語意搜尋
  - 以課名 + 大綱摘要（若無大綱則用課名 + 備註）建立向量索引 `scripts/build_index.py`
  - 預設使用本機模型 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，避免 API 費用；可用環境變數切換為 Azure OpenAI embeddings
  - 向量存為 `data/embeddings.npy` 與對應的 id 清單
- 推薦理由：每門推薦課程由語言模型產生一句說明，**只能根據該課程的資料欄位**，不得編造課程內容

## LLM 設定
- `planner/llm_client.py` 統一封裝所有呼叫
- 環境變數：`LLM_PROVIDER`（預設 `azure_openai`）、`AZURE_OPENAI_ENDPOINT`、`AZURE_OPENAI_API_KEY`、`AZURE_OPENAI_DEPLOYMENT`
- 提供 `.env.example`；`.env` 加入 .gitignore；金鑰只存在後端，前端 JavaScript 不得出現任何金鑰
- 另提供 `LLM_PROVIDER=mock`：回傳固定的假結果，讓沒有 API 金鑰的組員也能跑完整流程
- 逾時 15 秒；要求只輸出 JSON，移除 ```json 標記後解析；解析失敗回傳 fallback
- 批次處理需有最大呼叫次數參數（預設 300），並在開始前印出預估呼叫次數

## 網頁（Flask）
### 後端 app.py 路由
- `GET /`：主頁
- `GET /api/courses?q=&dept=&category=&ge_domain=`：課程搜尋
- `GET /api/course/<id>`：課程詳細資料（含原始備註與解析後的限修條件）
- `POST /api/free_slots`：輸入固定課號，回傳空堂格與可填入的候選課
- `POST /api/parse_preferences`：白話偏好 → 條件 JSON
- `POST /api/schedules`：輸入使用者資料、固定課號、條件 → 最多 3 組課表與推薦理由
- 輸入驗證失敗回傳 400 與 `{"success": false, "error": ...}`；埠號讀環境變數 `PORT`，預設 5001

### 前端（templates/index.html、static/main.js、static/style.css）
分成三個步驟：
1. **我的資料**：系所、年級、已修課程（可貼上課名清單）、要固定的課（搜尋後加入）
2. **我的偏好**：一個文字框輸入白話需求 → 顯示解析後的條件（可修改）
3. **推薦結果**：週課表格線圖（空堂、固定課、推薦課以不同顏色區分）、最多 3 組方案可切換、每門課顯示推薦理由與資格狀態
- 使用者資料只存在瀏覽器（sessionStorage），伺服器不保存
- 頁尾固定顯示：「本工具非學校官方系統，選課前請以課程查詢系統與選課系統為準」

## 評估（eval/，產生簡報用數字）
- `eval/eligibility_labels.csv`：從真實資料中挑 50 筆備註，由組員人工標註正確的限修條件（腳本先產生待標註的空白檔）
- `eval/eval_eligibility.py`：計算語言模型解析的正確率，各欄位分開計算，並列出錯誤案例
- `eval/preference_cases.json`：20 句白話偏好與預期條件
- `eval/eval_preferences.py`：計算偏好解析正確率
- 結果輸出到 `outputs/eval/`；README 中的數字必須來自這些輸出，不得填寫示意數字

## 專案結構
    .
    ├── CLAUDE.md
    ├── README.md
    ├── requirements.txt
    ├── .env.example
    ├── .gitignore
    ├── app.py
    ├── planner/
    │   ├── models.py
    │   ├── timeslots.py
    │   ├── data_loader.py
    │   ├── solver.py
    │   ├── eligibility.py
    │   ├── preferences.py
    │   ├── search.py
    │   └── llm_client.py
    ├── scripts/
    │   ├── check_data_source.py
    │   ├── fetch_courses.py
    │   ├── parse_eligibility_batch.py
    │   └── build_index.py
    ├── data/
    │   ├── raw/                     # 不提交
    │   ├── courses.json
    │   ├── sample_courses.json
    │   ├── eligibility_cache.json
    │   └── embeddings.npy
    ├── eval/
    ├── outputs/eval/
    ├── docs/
    │   └── data_feasibility.md
    ├── templates/
    │   └── index.html
    ├── static/
    │   ├── main.js
    │   └── style.css
    ├── tests/
    └── vendor/                      # 參考實作，不提交

## 程式風格
- Python 3.11；註解、README、介面文字使用繁體中文（台灣用語）
- 隨機性固定 `random_state=42`
- 每支 scripts/ 內的腳本可獨立執行，路徑以 pathlib 相對於專案根目錄
- 每完成一個階段就停下來，說明如何手動測試並列出已知問題，再繼續

## 開發順序
0. 資料可行性檢查 → **停下來回報**
1. 資料整理與範例資料
2. 空堂、衝堂、solver 與測試
3. 限修條件批次解讀與評估
4. 白話偏好解析與語意搜尋
5. Flask 網頁
6. README

## README 必須包含
- 專案動機與「AI 理解、演算法排課」的架構說明
- 資料來源：中大課程查詢系統；參考實作 NCU-Course-Finder-DataFetcher-v2（作者 Raku Zeta，MIT 授權）
- 與 NCU Course Finder 的差異：本專案新增限修條件解讀、白話偏好、語意推薦與自動排課
- 安裝、設定 .env、抓取資料、建立索引、啟動網站的步驟（含 `DATA_SOURCE=sample` 與 `LLM_PROVIDER=mock` 的免金鑰測試方式）
- 評估結果（來自 outputs/eval/）
- 隱私說明：不收集帳號密碼，使用者資料不存於伺服器
- 生成式 AI 使用聲明：本專案程式碼由 Claude Code 協助撰寫，內容由團隊審閱並負責
- 限制：
  - 非官方工具，資料可能與選課系統不同步
  - 限修條件解讀可能出錯，「符合」不代表一定選得到
  - 擋修、人數上限、抽籤等規則不一定反映在公開資料中
  - 大綱若無法取得，語意推薦只依課名與備註，準確度較低
