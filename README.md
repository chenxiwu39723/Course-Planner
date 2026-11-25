# 中央大學 113 學年度：跨系所畢業規則統一資料包

- 總計 **32 份主修／學士班規則**；8學院的建檔資料；額外提供資電院等同課程表及經濟系核對清單，以及依官方PDF重建的經濟系規則。
- **經濟系已補入** `data/programs/economics_113.unified.json`，依管理學院 PDF 第4頁重建，並非找回上次的原始版本；若日後取得原始版本仍應比對。
- `data/programs/PROGRAM_REGISTRY.json`: 統一學程清單；每個 `data/programs/*.unified.json` 含一致的識別資訊、候選課程索引、原始完整規則 `raw_rules`、待查紀錄。
- `unified_loader.py`: 可讀取規則，不執行自動畢業審核。
- `docs/review/REVIEW_FLAGS.csv`: 保留原始 JSON 已標記的未解決事項。

## 重要的安全界線
1. **已建檔不代表已認證／可投入畢業審核。** 沒有實作通用的 `choice_groups`、等同課程、學分上限、特殊採認與成績紀錄判定。
2. `course_candidates_not_for_automatic_credit_counting` 僅供搜尋，來源可能是必修、選修、替代課、擇修與先修，不可直接加總。
3. 不覆寫既有 `data/graduation/standalone/math_datascience_113_rules.json` 或 `graduation.py`，以免破壞現行測試。
4. 校方 PDF 有多處需人工確認的學分分項、紅字修正、完整課程目錄；應以系辦認定為準。

## 後續技術工作
- 修改程式載入器，使下拉選單讀取 `data/programs/PROGRAM_REGISTRY.json`。
- 定義正式的可執行規則模型（必修、擇修、抵修、分組、最大可認列、非學分門檻）。
- 先用數學系與經濟系既有可測案例做回歸測試，再擴充其他系所。
- 每系至少建立基本、缺一門必修、擇修不足、重複／等同課程、跨系抵免、特殊門檻等測試。

## 資料位置

- `data/programs/` 放全校系所規則、學程清單及等同課程表。
- `data/graduation/` 放網站的畢業規則與案例，獨立檢查程式的資料放在 `standalone/`。
- `docs/sources/` 放來源 PDF。
- `docs/review/` 放待確認事項。
- `outputs/graduation/` 放驗證與案例報告。
