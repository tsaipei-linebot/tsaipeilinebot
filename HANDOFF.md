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

## 新增子系統：配送部系統（`delivery/`）

同一個 repo 底下新增的**獨立子系統**（同仁登入用的內部管理網頁），跟上面的 LINE
招募機器人完全分開（不同的 FastAPI sub-app、不同的 Firestore collection 前綴、
不同的登入機制），只是暫時共用同一個 GCP 專案與同一個 Cloud Run 服務部署。

### 需求來源

同仁提供的手繪畫面草圖，主頁分三塊：
- **選擇廠商**（蝦皮／UD／UC／順豐）→ 點選後列出該廠商配送人員的「缺件狀況」
- **選擇功能**：補款登記、病假登記（病假可上傳收據）→ 輸入後寫入資料庫
- **查詢人員** → 依姓名/身分證字號查詢，一樣顯示缺件狀況

追問後確認「缺件狀況」是指**報到前應備文件**是否齊全：身分證、駕照、強制險、
良民證（後兩者有到期日，過期也算缺件）。

### 架構

- `delivery/app.py`：獨立的 `FastAPI()` sub-app，掛了自己的
  `SessionMiddleware`（cookie session，帳號密碼登入），用
  `app.mount("/delivery", delivery_app)`（見 `main.py`）掛到主服務底下，
  跟 LINE webhook 的路由完全不共用 middleware。
- `delivery/config.py`：廠商清單、應備文件清單、上傳限制、env var 名稱。
- `delivery/db.py`：Firestore collection 存取（`delivery_users`、
  `delivery_personnel`、`delivery_repayments`、`delivery_sick_leaves`），
  **延遲建立 client**（跟 `services/session_service.py` 模組層級直接連線的
  作法不同），單純 import 這個模組不需要 GCP 憑證。
- `delivery/storage.py`：身分證/駕照/強制險/良民證/病假收據等檔案存放到
  Google Cloud Storage（一樣延遲 import/連線）。檔案一律不公開、不用簽名
  網址，只能透過 `delivery/routes/file_routes.py`（需要登入 session）下載，
  因為這些檔案多半是個資。
- `delivery/auth.py`：帳號密碼登入。密碼雜湊用標準函式庫
  `hashlib.pbkdf2_hmac`（200,000 次疊代 + 隨機 salt），沒有另外引入
  passlib/bcrypt。
- `delivery/repository.py`：人員/補款/病假的 CRUD，以及「缺件狀況」判斷邏輯
  （`doc_status` / `missing_documents`，純函式、有單元測試）。
- `delivery/routes/*.py` + `delivery/templates/*.html`：登入、主頁、廠商人員
  清單、人員詳細（上傳/更新文件）、查詢人員、補款登記、病假登記。

### 部署前需要準備的環境變數（目前都還沒設定，正式上線前必須處理）

- `DELIVERY_SESSION_SECRET_KEY`：登入 session cookie 簽章密鑰，**務必**設成
  隨機字串（沒設定時用一個不安全的預設值，只能本機開發用）。
- `DELIVERY_GCS_BUCKET`：存放身分證/駕照/強制險/良民證/病假收據的 GCS
  bucket 名稱。**這個 bucket 需要先手動建立**（這裡沒有權限自動建立），
  Cloud Run 的服務帳號要有這個 bucket 的讀寫權限。沒設定時，檔案上傳/下載
  功能會被擋下來（不會報錯到整個系統掛掉，但無法真的存檔案）。

### 建立第一組登入帳號

系統沒有開放自行註冊，帳號一律用 CLI 腳本建立（需要在有 Firestore 寫入權限
的環境執行，例如透過 Cloud Run 的一次性 job，或本機用有權限的 ADC）：

```
python -m delivery.seed_admin <帳號> <密碼> <顯示名稱> [role，預設 admin]
```

### 目前已知的待辦/簡化事項（下一輪可以接續處理）

- 補款登記／病假登記目前是「輸入人員姓名的文字欄位」，不是從人員清單挑選
  （沒有連到 `delivery_personnel` 的 `personnel_id`）。畫草圖時的描述是
  「輸入後寫入資料庫」，先照字面做成最簡單的表單；如果之後想要補款/病假
  紀錄能直接連回某個人員的完整資料，需要加一個人員選擇/搜尋的 UI（例如
  下拉選單 + AJAX 搜尋），並把 `personnel_id` 一併存進去。
- 目前只有「同仁登入」，沒有角色權限差異（`role` 欄位有存但没有實際用在
  任何權限檢查上）；如果未來需要區分一般同仁跟管理者能做的事情不同，要
  補上權限檢查。
- 測試涵蓋密碼雜湊、缺件邏輯、路由掛載/導向等不需要真的連線 GCP 的部分；
  真正會讀寫 Firestore/GCS 的路徑（新增人員、上傳文件、補款/病假送出）
  還沒有整合測試，建議在有 GCP 憑證的環境手動測過一輪再正式上線。

### 後續新增：批次匯入人員（CSV）

主頁「選擇廠商」區塊、各廠商人員清單頁都有「批次匯入人員」連結（`/delivery/import`）。
上傳 CSV（`delivery/csv_import.py` 負責解析，UTF-8/Big5 皆可自動判斷），欄位需含
「廠商」「姓名」（身分證字號、電話選填），廠商欄位可填代號或中文名稱。重複判斷
依需求改成比對「姓名+電話」（`repository.find_active_personnel_by_name_and_phone`，
兩者都要有值才會比對，不是用身分證字號），已存在相同組合的在職人員會自動略過，
所以同一份檔案可以重複上傳來修正錯誤，不用擔心建出重複資料。

### 後續新增：應徵名單（接 Google 表單，錄取轉正式人員）

需求來源：現有的「機車外送人員問答」Google 表單（收姓名、聯絡電話、可配合天數、
配送縣市、行政區熟悉度、防詐騙提醒，**沒有**身分證字號/廠商/文件上傳，因為這是
應徵前的篩選問卷，跟「已在職、要追蹤報到文件」的正式人員是不同階段的資料）。

**架構**：表單維持現狀繼續寫入它自己的 Google 試算表；額外在該試算表（或表單）
掛一個 Apps Script「表單提交時」觸發器，送出時打一支新開的 webhook
`POST /delivery/api/form-submission` 把整包回覆寫進 Firestore 的
`delivery_applicants`，在「應徵名單」頁面（`/delivery/applicants`）管理：

- `delivery/form_webhook.py`：純函式，從 Apps Script 傳來的 `answers`
  （題目全名 → 回答）裡用「標題有沒有包含關鍵字」抓姓名/電話（`extract_answer`），
  其餘題目原樣顯示在清單頁參考（`other_answers`）——這樣之後表單題目文字微調
  不需要跟著改程式碼。
- `delivery/routes/webhook_routes.py`：`POST /api/form-submission`，用共用密鑰
  `X-Delivery-Form-Secret` header 驗證（**不**經過同仁登入 session，因為呼叫端是
  Google 的伺服器），沒設定 `DELIVERY_FORM_WEBHOOK_SECRET` 時一律 403。
- `delivery/routes/applicant_routes.py` + `templates/applicants_list.html`：
  列表可勾選「已面試」「放棄」（純狀態，即時更新），「錄取」需要另外選一個廠商
  （表單沒收廠商，人員資料表又必須有）送出，會同時建立正式人員資料
  （`repository.create_personnel`，身分證字號留空，之後到人員詳細頁補齊文件）
  並標記 `converted_personnel_id`，避免同一個應徵者被重複轉正。

**上線前要做的事**（跟 GCS bucket 一樣是這裡沒有權限自動做的手動步驟）：

1. Cloud Run 設定環境變數 `DELIVERY_FORM_WEBHOOK_SECRET`（隨機字串，例如
   `openssl rand -hex 32`），跟下面 Apps Script 裡貼的密鑰要一致。
2. 打開表單的回覆試算表 → 擴充功能 → Apps Script，貼上：
   ```javascript
   function onFormSubmit(e) {
     var answers = {};
     for (var key in e.namedValues) {
       answers[key] = e.namedValues[key][0];
     }
     var options = {
       method: "post",
       contentType: "application/json",
       payload: JSON.stringify({answers: answers}),
       headers: {"X-Delivery-Form-Secret": "跟 Cloud Run 上設定的同一組密鑰"},
       muteHttpExceptions: true
     };
     UrlFetchApp.fetch(
       "https://recruitment-bot-412901869672.asia-east1.run.app/delivery/api/form-submission",
       options
     );
   }
   ```
3. 左側「觸發條件」→ 新增觸發條件：執行的函式選 `onFormSubmit`，事件來源選
   「表單」，事件類型選「提交表單時」，儲存並完成 Google 帳號授權。

**已知簡化**：`extract_answer` 用關鍵字「姓名」「電話」比對題目標題，如果表單
之後新增別的含「電話」兩字但不是本人聯絡電話的題目（例如緊急聯絡人電話），
會抓錯欄位，需要屆時調整關鍵字比對邏輯。

**應徵名單查重**（`repository.upsert_applicant` / `find_applicant_by_name_and_phone`）：
姓名+電話都相同視為同一人重複投遞表單，會直接覆蓋既有那筆應徵紀錄的回覆內容，
並把處理狀態清空回到「未面試」（`converted_personnel_id` 也會被清掉），不會疊加
成新的一筆。如果錄取後又重複投遞導致狀態被清空，正式人員資料本身不受影響
（已經轉正的 `delivery_personnel` 紀錄不會被刪除或改動，只是應徵名單那一筆看起來
要重新處理）。

### 後續調整：應徵名單狀態改成單一欄位 + 搜尋/批次更新 + 版面美化

依實際使用回饋做的調整：

- **狀態模型改成單一 `status` 欄位**（`not_interviewed`/`interviewed`/`withdrawn`/`hired`，
  對應「未面試」「已面試」「放棄」「已錄取」），取代原本 `interviewed`/`hired`/`withdrawn`
  三個獨立布林欄位。`repository.normalize_applicant_status()` 會相容改版前的舊資料
  （沒有 `status` 欄位時，從三個布林欄位推回對應狀態），舊測試資料不用手動搬移。
  「已錄取」不開放手動勾選，只能透過「錄取並建立人員」設定。
- **搜尋/篩選**：`/delivery/applicants` 支援 `?name=&phone=&status=` 這三個 query
  string 篩選；預設（沒有任何篩選條件）不顯示「放棄」的紀錄，避免洗版——主動
  搜尋姓名、或直接篩選狀態為「放棄」才會顯示（`repository.applicant_matches_filters`，
  純函式、有單元測試）。
- **批次更新**：整個表格包在同一個 `<form>` 裡（每列一組 `status_{id}` 單選鈕），
  上下各放一個放大的「已勾選狀態 → 一鍵更新」按鈕，一次送出 POST
  `/delivery/applicants/bulk-status`，後端用 Firestore `batch()` 一次寫入多筆。
  「錄取並建立人員」這個動作因為需要選廠商、跟批次更新是不同的目的地，用
  HTML5 `formaction` 屬性讓同一個按鈕改送到 `/delivery/applicants/{id}/accept`
  （而不是巢狀 `<form>`——瀏覽器不允許 form 裡面再放 form）。
- **凍結表頭**：`.sticky-head thead th { position: sticky; }`，資料多時往下捲動
  表頭仍固定在頂端導覽列下方。
- **整體視覺**：`delivery/static/style.css` 全面重寫，改用橘色系品牌色
  （呼應機車宅配「速度感」）+ 深色標題文字的專業質感配色，主頁與導覽列加上
  手繪 inline SVG 圖示，卡片加陰影、表格/表單加焦點樣式，套用到所有頁面
  （其他頁面本來就共用 `.btn`/`.data-table`/`.home-panel` 等既有 class，
  不需要逐一改 HTML 結構）。
