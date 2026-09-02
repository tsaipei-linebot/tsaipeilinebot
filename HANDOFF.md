# 招募機器人（沛沛）專案交接筆記

給 Claude Code 接續使用。這份文件整理目前為止已完成的工作，以及還沒開始、需要接續處理的待辦事項。

## 專案基本資訊

- 專案性質：材霈有限公司的 LINE 招募聊天機器人「沛沛」
- 技術棧：FastAPI + line-bot-sdk + Notion API（職缺/FAQ 資料庫）+ Vertex AI Gemini（決策與回覆生成）+ Firestore（session/槽位儲存）
- GCP 專案 ID：`tsaipei-505807`
- 部署方式：接 GitHub，push 後由 Cloud Build 自動建置、部署到 Cloud Run（服務名稱 `recruitment-bot`，地區 `asia-east1`）
- Cloud Run 服務有 `/callback`（正式環境）與 `/test-callback`（測試環境）兩條 webhook 路由
- 檔案結構：`main.py`、`config.py`、`handlers/message_handler.py`、`services/session_service.py`、`services/matcher_service.py`、`services/notion_service.py`、`services/flex_service.py`、`services/ai_service.py`、`tests/`

## 待辦事項（下一步優先處理）

- **【新功能，需完成 GCP 設定才會實際運作】每週新工廠登記監控**：`services/factory_watch_service.py` + `main.py` 的 `POST /internal/factory-watch/run` 端點已完成，邏輯是每次執行去抓政府資料開放平台《[登記工廠名錄](https://data.gov.tw/dataset/6569)》（經濟部產業發展署），篩出近期新登記、且 Firestore 裡沒推播過的工廠，寫入 Google Sheet 明細，並視情況推播 LINE 摘要通知業務。要正式上線還缺以下設定（環境變數留空時，程式仍會安全跳過對應步驟並印出提示，不會噴錯）：
  1. 建一個 Google Sheet 當明細清單，分享編輯權限給 Cloud Run 服務帳戶（`tsaipei-505807` 專案的預設運算服務帳戶，或另外指定的服務帳戶信箱），把試算表 ID 設進 `FACTORY_WATCH_SHEET_ID`
  2. 決定 LINE 推播對象（業務同仁個人帳號或內部群組），取得 LINE user ID / group ID 後設進 `FACTORY_WATCH_LINE_TARGET_ID`（沒設定時只會更新 Sheet，不會推播）
  3. 設一個隨機字串當 `FACTORY_WATCH_TRIGGER_SECRET`，並在 GCP Cloud Scheduler 建一個每週五下午的排程 job，用 HTTP POST 呼叫 Cloud Run 的 `/internal/factory-watch/run`，帶上 header `X-Factory-Watch-Secret: <同一組密鑰>`
  4. 資料源的實際 CSV 下載連結是執行時動態去 data.gov.tw 資料集 API 探測的，欄位名稱也是用關鍵字比對（`services/factory_watch_service.py` 的 `COLUMN_KEYWORDS`），第一次正式跑之後建議看一次 Cloud Run log，確認欄位有抓對、筆數合理（開發環境的網路權限擋掉了 data.gov.tw，這部分沒辦法在開發階段實際跑一次驗證，只做過 CSV 解析/去重/訊息組裝等純邏輯的單元測試）

- **【需與外部工程師協調】線上履歷填完後自動跳轉回官方 LINE 帳號**：求職者點擊「填寫線上履歷」會被導去外部履歷系統（`resume.tsaipei.com.tw`，網址設定在 `config.py` 的 `DEFAULT_RESUME_URLS`），但填完表單後目前不會自動導回 LINE 官方帳號對話。這個機制牽涉到外部履歷系統那端的表單送出後導轉邏輯（例如導回 LINE 的 `line://` deep link 或加上完成頁），不是這個 repo 這邊能單方面決定/實作的，需要先跟負責 `resume.tsaipei.com.tw` 的外部工程師討論介接方式，確認後才回來這裡實作對應的程式（例如可能要在 `flex_service.py` 的履歷網址加上 redirect 參數，或是新增一個 webhook/callback 端點接收「已完成填寫」通知）。
- **帳單帳戶升級**：目前仍是「免費試用帳戶」，正式頻道流量上量後（估計約 4 萬則/月，群發尖峰每分鐘數百則）容易撞到 Vertex AI 配額上限。建議**在正式切換頻道前**先升級成正式付費帳戶。
- **觀察 Vertex AI 回應延遲 vs LINE 30 秒 reply token 時限**：測試環境曾測到單輪決策約 11.7 秒，正式頻道併發量提高後延遲可能惡化，有機會撞到 LINE 30 秒逾時。目前只能先觀察，建議正式上線後密切看 Cloud Run/Vertex AI 的延遲指標，有異常再回來處理（例如考慮加上逾時保護或非同步通知使用者「處理中」）。
- **考慮加上錯誤告警機制**：目前所有例外只靠 `print()` 寫進 Cloud Run log，沒有主動通知。量小時人工看 log 還行，正式頻道建議至少設一個 Cloud Monitoring alert（例如 5xx 或例外次數異常）。

## 已完成並部署驗證過的項目

### 第一組：架構層級（全部完成）

1. **Session/槽位狀態外部化**：`session_service.py` 從行程記憶體字典改成讀寫 Firestore（database ID 用預設的 `(default)`，Standard edition）。函式簽名維持不變（`get_user_history`、`get_user_slots`、`update_user_slots`、`clear_user_slots`、`append_user_history`），呼叫端不用改。
2. **修正同步阻塞問題**：`main.py` 把 `webhook_handler.handle(...)` 用 `starlette.concurrency.run_in_threadpool` 包起來，避免同步的 Notion/Firestore/Gemini 呼叫卡住 FastAPI event loop。順便把 `/callback`、`/test-callback` 重複邏輯抽成共用的 `_handle_webhook()`。
3. **統一 GCP 設定來源**：`ai_service.py` 不再自己 `os.getenv` 定義 `GCP_PROJECT_ID`/`GCP_LOCATION`，改成 `from config import GCP_PROJECT_ID, GCP_LOCATION`。
4. **整理 `notion_service.py` 檔案結構**：拿掉 `fetch_faqs_data()` 裡的死碼與重複 import。

### 額外處理（第一組期間發現、非原訂項目）

5. **`ai_service.py` 加上 429 重試機制**：`_generate_with_retry()` 只針對 429 RESOURCE_EXHAUSTED 重試（最多 2 次、遞增等待時間），其他錯誤直接換下一個 fallback 模型。
6. **修正 Vertex AI 地區與模型名稱問題（關鍵 bug）**：`gemini-3.5-flash` 在 Vertex AI 上根本不存在，已從 `MODEL_FALLBACK_LIST` 移除。`GCP_LOCATION` 預設值改成 `global`。Cloud Run 服務本身仍跑在 `asia-east1`（容器運行地區，跟 Vertex AI 呼叫地區是兩回事）。

### 第二組：對話邏輯核心（全部完成）

7. **槽位三態機制**（`session_service.py`）：`CLEAR_SLOT` 常數，`update_user_slots` 支援「維持原值／明確清空／設定新值」三態。
8. **否定詞感知的地點/類別抽取**（`matcher_service.py`）：`_keyword_is_negated`、`detect_negated_location`、`detect_negated_category`，能區分「不要 A」跟「想要 B」。
9. **`detect_brand_label` 多項誤判修正**：正則備援抽取必須真的比對到 Notion 廠商名稱才採信；`_vendor_core_name` 處理內部後綴（如「美光(桃園)」→「美光」）；brand 槽位每輪重新判斷、不沿用舊值。
10. **候選集合硬篩→加權排序**：`build_ai_job_candidates` 不再對地區/品牌/休假制度做 hard filter，改成全部職缺加減分後取分數前 70 筆，修掉「地區查無職缺時候選集合直接變空」的 bug（PR #1）。
11. **拆分「全域重置」與「單一維度調整」**：`重新找`/`重來` 才整組清空，`換個條件` 只詢問要換哪一項、其他槽位保留（PR #1）。
12. **收緊禮貌收尾判斷**：加入轉折詞白名單，「謝謝，不過還想問⋯」不會再被誤判成單純道謝（PR #1）。
13. **統一意圖分類來源**：`matcher_service.KNOWN_BRANDS` + `has_recognizable_category_or_brand_keyword()`，取代 `message_handler.py` 原本覆蓋不完整的手動關鍵字清單（PR #1）。
14. **「有美光的工作嗎？」退讓推薦品質**：已用真實對話驗證，AI 會誠實列出美光在其他地區（新北/桃園/台中/台南等）有職缺並推薦，不會誤判成 `NO_MATCH`。

### 第三組：FAQ/職缺分工調整（全部完成，PR #2）

15. **FAQ 高信心比對直接回傳原文**：`find_high_confidence_faq_match()` 雙向完整包含比對命中時，直接回傳 Notion 原文，不經 AI 改寫，避免合規風險並省一次 Gemini 呼叫。
16. **未收錄問題寫入 FAQ 前先去重**：`append_unresolved_faq_to_notion()` 寫入前先查現有問題標題，相似問題不重複寫入。

### 第四組：程式碼品質（全部完成，PR #3、#4）

17. **地區/班別/廠商關鍵字集中化**：新增 `SHIFT_SYNONYMS` 模組常數，`_tokenize_search_terms` 改引用 `LOCATION_CANDIDATES`/`SHIFT_SYNONYMS`/`KNOWN_BRANDS` 單一來源。
18. **`DEFAULT_RESUME_URLS` 環境變數化**：`config.py` 改用 `os.getenv()`，可透過 `RESUME_URL_SPX`/`RESUME_URL_SERVICE`/`RESUME_URL_MANUFACTURE` 覆蓋，未設定時沿用原本網址。
19. **補單元測試**：新增 `tests/` 目錄（標準庫 `unittest`），涵蓋 `matcher_service.py`/`notion_service.py`/`ai_service.py`/`message_handler.py` 的純邏輯函式。
20. **Python 版本升級**：`Dockerfile` base image 從 `python:3.10-slim` 升級到 `python:3.12-slim`（PR #4）。

### 額外發現並修正的問題（不在原訂範圍內）

21. **AI 回覆解析 bug（PR #6）**：實測「有理貨的工作嗎」時發現，Gemini 若沒有照 prompt 範例在 `REPLY:`/`BUTTONS:` 之間換行，`BUTTONS:` 原始文字會被當成訊息內容顯示給使用者。
22. **AI 決策改用結構化 JSON 輸出（PR #7、#8，徹底解決 #21 這類問題的根源）**：`ai_service.py` 新增 `response_schema` 支援 Gemini 原生結構化輸出模式（`response_mime_type="application/json"`）；`message_handler.py` 的 `ai_prompt` 跟解析邏輯改成 `AI_DECISION_SCHEMA` + `json.loads`，取代原本的 `ACTION:`/`REPLY:`/`BUTTONS:`/`IDS:` 文字格式 + 正則表達式解析，格式錯誤在 API 層級就不可能發生。已用真實對話驗證按鈕正確渲染、無格式外洩、跨地區退讓推薦語氣自然。

## 目前所有檔案的狀態

所有檔案都已經在 GitHub `main` 分支上，跟目前 Cloud Run 上手動部署的版本一致（PR #1～#8 均已合併）。接手時建議先 `git log --oneline -10` 確認本地/部署版本沒有落後 main。

`tests/` 目錄有 43 個單元測試，改動前後都建議跑 `python3 -m unittest discover -s tests` 確認沒有回歸。
