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
2. **打開「表單本身」（不是回覆試算表）** → 擴充功能 → Apps Script，貼上
   （`vendor`/`cooperation_type` 是這個表單自己固定要標記的來源，不是題目
   答案——每個廠商各自的表單都要貼這段、只改這兩個值，詳見下面「應徵者
   廠商/合作方式/試駕」那節）。**注意**：一個試算表只能綁一個 Apps Script
   專案，如果好幾份不同廠商的表單都指定回覆寫到同一份試算表（各自佔一個
   分頁），程式碼不能綁在那份共用的試算表上，一定要各自打開「表單」本身
   （表單編輯畫面 → 擴充功能 → Apps Script）分別貼、分別設定觸發條件，
   這樣每份表單才有各自獨立的專案，互不干擾：
   ```javascript
   function onFormSubmit(e) {
     var answers = {};
     for (var key in e.namedValues) {
       answers[key] = e.namedValues[key][0];
     }
     var options = {
       method: "post",
       contentType: "application/json",
       payload: JSON.stringify({
         answers: answers,
         vendor: "shopee",  // 這份表單是哪個廠商：shopee / ud / uc / sf
         cooperation_type: ""  // 只有蝦皮的表單才填，例如 "three_wheel_employed"；其他廠商留空字串
       }),
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

### 後續大改：應備文件依廠商/合作方式動態決定 + OCR 辨識到期日 + LINE 到期提醒

依需求把「應備文件」從一份固定的四項清單，改成依**廠商**與新增的**合作方式**
（二輪承攬/二輪雇傭/三輪雇傭）動態決定，每一項還多了「怎麼算缺件」的類型
（`delivery/config.py` 的 `DOC_TYPES`，每項有 `kind`）：

- `kind: "id_number"`（身分證）：不再上傳檔案，同仁直接填 `personnel.id_number`
  欄位，用中華民國身分證字號檢查碼演算法驗證格式（`delivery/validators.py`
  的 `is_valid_taiwan_id`，純函式、有單元測試）。所有廠商都適用。
- `kind: "checkbox"`（駕照、合約簽定）：同仁勾選「有」就算備齊，不用上傳、
  沒有到期日。所有廠商都適用。
- `kind: "file_expiry"`（強制險、公會加保證明、營業用第三責任險、良民證）：
  要上傳檔案 + 記錄到期日，過期也算缺件。
  - `exclude_vendors`：良民證設了 `["shopee"]`，蝦皮的人不會被要求（其他
    廠商不受影響，DOC_TYPES 裡良民證本身沒有刪除，只是套用時被篩掉）。
  - `cooperation_types`：強制險是二輪承攬/二輪雇傭才需要，公會加保證明只有
    二輪承攬才需要，營業用第三責任險只有二輪雇傭才需要，三輪雇傭三項都不用。
    人員還沒設定合作方式時，這三項不會出現在缺件清單裡（等設定好才開始追蹤，
    不是變相视為「不用交」）。
  - 篩選邏輯在 `repository.applicable_doc_types(vendor, cooperation_type)`，
    `doc_status()` / `missing_documents()` / `all_document_statuses()` 都改成
    吃完整的 `personnel` dict（不是只吃 `documents` 子物件），因為身分證那項
    要讀 `personnel.id_number`。**這是 breaking change**：舊的呼叫方式
    `missing_documents(person.get("documents"))` 全部要改成
    `missing_documents(person)`，各 routes 已經同步更新。
  - 合作方式在人員詳細頁最上方（應備文件表格「上面」）用下拉選單設定/修改，
    也可以在「新增人員」表單就先選（非必填）。CSV 批次匯入、應徵名單錄取
    這兩個建立人員的管道目前**沒有**收合作方式，新建的人一律留空，要到
    詳細頁補設定才會開始出現強制險等三項的缺件提示。

- **OCR 自動辨識到期日**：`delivery/ocr.py` 用 Vertex AI Gemini 的多模態能力
  （跟 LINE 招募機器人共用同一個專案設定，不用另外申請）直接讀上傳的保險/
  證明文件圖片或 PDF，辨識「到期日」。上傳表單的到期日欄位改成非必填——
  同仁留空的話由系統辨識，辨識不出來就維持空白，同仁還是可以自己手動填
  同一個表單再送一次做修正；同仁自己有填的話就直接用同仁填的，不會被 OCR
  覆蓋。辨識失敗（例如額度用盡、模型出錯）一律回傳空字串，不會讓上傳這個
  動作失敗或報錯。

- **LINE 到期提醒**：`delivery/routes/reminder_routes.py` 新增
  `POST /delivery/api/expiry-reminder-check`，設計給 Cloud Scheduler 每天呼叫
  一次，掃過所有在職人員的 `file_expiry` 類項目，「到期日在今天起
  `DELIVERY_REMINDER_DAYS_AHEAD`（預設 30）天內」或「已過期」的，整理成一則
  訊息，用公司現有的 LINE 官方帳號（`delivery/line_notify.py`，沿用
  `LINE_CHANNEL_ACCESS_TOKEN`）推播給指定的同仁或群組
  （`DELIVERY_LINE_REMINDER_TARGET`）。同一份文件最多每 7 天提醒一次
  （`repository.list_expiring_documents` / `mark_documents_reminded`，用
  `documents[code].last_reminded_at` 記錄，避免每天洗版；同仁重新上傳/更新
  到期日時這個記錄會自動清掉，重新進入提醒週期）。跟 Google 表單那支 webhook
  一樣用共用密鑰驗證（`X-Delivery-Reminder-Secret` header），不經過同仁登入
  session，因為呼叫端是 Cloud Scheduler。

**上線前要做的事**：

1. Cloud Run 設定環境變數：
   - `DELIVERY_REMINDER_SECRET`：隨機字串（`openssl rand -hex 32`），Cloud
     Scheduler 呼叫時要帶同一組在 header 裡。
   - `DELIVERY_LINE_REMINDER_TARGET`：要收到提醒的 LINE userId 或 groupId。
     **同仁自己的 userId 拿法**：讓對方傳一則訊息給公司的 LINE 官方帳號，
     然後到 Cloud Run 的 Cloud Logging 找同一時間的 log（`handlers/message_handler.py`
     處理訊息時會經手 `event.source.user_id`，可以暫時加一行 log 印出來，
     或直接查 Firestore `user_sessions` collection 的文件 ID，那個 ID 就是
     userId）。**群組 groupId 拿法**：把官方帳號拉進一個 LINE 群組，在群組
     裡發一則訊息，一樣去 log／Firestore 對應時間找 `event.source.group_id`。
   - `DELIVERY_REMINDER_DAYS_AHEAD`（選填，預設 30）：提前幾天算「即將到期」。
2. 設定 Cloud Scheduler 每天觸發一次（例如每天早上 9 點）：
   ```bash
   gcloud scheduler jobs create http delivery-expiry-reminder \
     --project=tsaipei-505807 \
     --location=asia-east1 \
     --schedule="0 9 * * *" \
     --time-zone="Asia/Taipei" \
     --uri="https://recruitment-bot-412901869672.asia-east1.run.app/delivery/api/expiry-reminder-check" \
     --http-method=POST \
     --headers="X-Delivery-Reminder-Secret=跟 Cloud Run 上設定的同一組密鑰"
   ```
   （第一次執行 `gcloud scheduler` 系列指令，專案如果還沒啟用過 Cloud Scheduler
   API，會提示要不要啟用，選是即可。）

**已知限制**：OCR 辨識到期日、合作方式篩選這些邏輯都沒有真的連 Vertex AI /
Firestore 跑過整合測試（測試都是 mock 掉外部服務），上線後建議實際上傳一張
強制險保單照片測試一次辨識結果對不對，抓錯格式或看不懂的圖再回頭調整
`delivery/ocr.py` 的 prompt。

### 後續新增：UD 專屬項目（負責客戶、UBER系統、MOMO測驗、自拍照）

延續同一套「依廠商/合作方式動態決定應備項目」的架構，多加了兩種篩選維度跟
一種新的項目類型：

- **負責客戶**（`CLIENTS`：PCHOME/MOMO）：新欄位 `personnel.client`，跟合作
  方式一樣是全域欄位（不綁死在 UD 上，之後別的廠商要用也不用改架構），在
  人員詳細頁最上面（合作方式旁邊）用下拉選單設定。
- `DOC_TYPES` 新增 `include_vendors`（白名單，只有列在裡面的廠商才要求這項，
  跟既有的 `exclude_vendors` 黑名單相反方向）跟 `clients`（只有負責客戶在
  清單裡才要求）兩種篩選條件。`applicable_doc_types()` 多一個 `client` 參數。
- 新增三個 UD 專屬項目（`include_vendors: ["ud"]`）：
  - `uber_system`（UBER系統）：`kind: "checkbox"`，同仁勾選「已完成」即可。
  - `momo_test`（MOMO測驗）：`kind: "checkbox"`，另外加 `clients: ["momo"]`，
    只有負責客戶是 MOMO 的人才會出現這個項目。
  - `selfie_photo`（自拍照）：**新的 `kind: "file"`**，要上傳檔案但不用記錄
    到期日、不會跑 OCR（純粹「有沒有交」，跟強制險那種 `file_expiry` 不同）。
- 身分證字號驗證、駕照/合約簽定勾選、良民證上傳辨識到期日，這幾項 UD 直接
  沿用上一輪已經全廠商通用的功能，這次沒有額外改動。

### 後續新增：UC/順豐專屬項目 + 人員詳細頁改成一鍵全部更新

**新的篩選/欄位機制：**

- **`required` 旗標**（`DOC_TYPES` 裡 `file_expiry` 類項目專用，預設 `True`）：
  設成 `False` 代表這項不是必填——沒交不算缺件，但只要有交、有到期日，
  一樣會被到期提醒排程掃到、一樣會被記錄過期。目前設成 `False` 的是
  `guild_insurance`（蝦皮的公會加保證明）跟新增的 `sf_guild_insurance`
  （順豐的公會加保證明）。`doc_status()` 回傳的 dict 現在多一個 `required`
  欄位，判斷公式是 `missing = expired or (required and not has_file)`。
- **新的 `kind: "email"`**：同仁直接填 email，`doc_status()` 用簡單的 regex
  （`_EMAIL_PATTERN`）檢查格式，跟身分證字號那類「不是文件、检查欄位本身」
  的做法一樣。對應寫入函式 `repository.update_personnel_email()`。
- **`COOPERATION_TYPE_VENDORS` / `CLIENT_VENDORS`**（`config.py` 新增兩個
  清單）：控制「合作方式」「負責客戶」這兩個下拉選單**只在**列在清單裡的
  廠商頁面上顯示（欄位本身還是全域欄位，只是畫面上非相關廠商不顯示、也不
  會送出這兩個值）。目前 `COOPERATION_TYPE_VENDORS = ["shopee"]`（只有蝦皮
  看得到合作方式選單，UC/UD/順豐都不看合作方式決定應備項目）、
  `CLIENT_VENDORS = ["ud"]`（只有 UD 看得到負責客戶選單）。
- 新增/調整的 `DOC_TYPES`：
  - `uber_system`（UBER系統，checkbox）：`include_vendors` 從只有 `["ud"]`
    擴大成 `["ud", "uc"]`，UC 現在也會要求勾選。
  - `uc_photo`（拍照，`kind: "file"`，`include_vendors: ["uc"]`）：跟自拍照
    一樣純粹「有沒有交」，不記錄到期日。
  - `email`（EMAIL，`kind: "email"`，`include_vendors: ["ud", "uc"]`）：UD/UC
    都要填。
  - `sf_insurance`（強制險，`kind: "file_expiry"`，`include_vendors: ["sf"]`）
    跟 `sf_guild_insurance`（公會加保證明，同上、外加 `required: False`）：
    順豐專屬，**不看合作方式**（直接綁廠商，因為順豐頁面沒有合作方式選單，
    人員的 `cooperation_type` 一律是空字串，用既有的 `cooperation_types`
    篩選方式抓不到，所以另外開兩個獨立項目而不是共用蝦皮/UD 那組
    `insurance`/`guild_insurance`）。

**人員詳細頁改版（`personnel_detail.html` + `vendor_routes.py`）：**

- 原本每一列應備項目各自一個小 `<form>`、要分開送出很多次，改成**整頁一個
  `<form enctype="multipart/form-data">`**，所有欄位（合作方式/負責客戶、
  身分證字號、email、各項勾選、各項檔案上傳、各項到期日）一次送出。
- 對應後端從原本五支個別的更新路由（`update_cooperation_type` /
  `update_client` / `update_id_number` / `update_checkbox` /
  `upload_document`，**已整個移除**）合併成**一支** `POST
  /delivery/personnel/{id}/bulk-update`，用表單欄位名稱規則對應：
  `id_number`、`email`、`cooperation_type`、`client`、
  `checked_{doc.code}`、`file_{doc.code}`、`expiry_date_{doc.code}`。
  合作方式/負責客戶這兩個欄位只有畫面上真的有顯示（`show_cooperation_type`
  / `show_client`）時表單才會帶到，路由用 `"cooperation_type" in form` /
  `"client" in form` 判斷要不要更新，避免沒顯示的廠商頁面誤把值清空。
- 頁面最上方（表格前）跟最下方（表格後）都放了大顆的「一鍵全部更新」按鈕
  （`.btn-bulk-update`），對應原本「蝦皮、順豐、UD、UC 的頁面上方都放大的
  一鍵全部更新按鈕」的需求。
- 身分證字號格式錯誤時（沒通過檢查碼驗證）整份表單一樣會照送，只有身分證
  字號這欄不寫入，並在網址帶 `?error=id_number` 導回同一頁顯示錯誤訊息；
  其他欄位（勾選、上傳、email 等）不受影響照常更新，避免因為一個欄位打錯
  就整份都不儲存。
- `personnel_form.html`（新增人員表單）的合作方式/負責客戶選單也一併改成
  依 `show_cooperation_type` / `show_client` 條件顯示，跟詳細頁行為一致。

**已知限制**：這次新增的 `email` 格式檢查、`required=False` 缺件判斷、
UC/順豐新項目都只有單元測試 + mock 過的 TestClient 手動測試，沒有實際連
Firestore/GCS/Vertex AI 跑過；上線後建議挑一個 UC 跟一個順豐的測試人員，
實際跑一次「一鍵全部更新」（含上傳強制險/公會加保證明照片）確認 OCR 辨識
跟到期提醒排程都正常。

### 後續新增：人員狀態（待報到/在職/離職/放棄報到）+ 廠商清單頁篩選

蝦皮、順豐、UD、UC 四個廠商的人員新增一個「人員狀態」欄位，跟原本 CSV 匯入
時就會寫死的內部欄位 `personnel.status`（一律 `"active"`，判斷資料存不存在
用的隱藏欄位，不開放編輯）是兩回事：

- **`config.py`**：新增 `PERSONNEL_STATUSES`（`pending_onboard` 待報到 /
  `employed` 在職 / `resigned` 離職 / `onboard_withdrawn` 放棄報到）、
  `PERSONNEL_STATUS_MAP`、`PERSONNEL_STATUS_BADGE_CLASS`（畫面上狀態徽章要
  用哪個 CSS class）、`DEFAULT_PERSONNEL_STATUS`（`pending_onboard`）、
  `LEGACY_PERSONNEL_STATUS`（`employed`）、`HIDDEN_PERSONNEL_STATUSES`
  （`{resigned, onboard_withdrawn}`）。
- **新建人員預設狀態**：手動新增表單、CSV 批次匯入、應徵名單錄取建立人員這
  三個管道，統一透過 `create_personnel()` 的 `employment_status` 參數預設值
  （沒傳就用 `DEFAULT_PERSONNEL_STATUS`），一律先是「待報到」，之後同仁自己
  到人員詳細頁改成「在職」等其他狀態。
- **舊資料相容**：這個功能上線前就存在的人員資料沒有 `employment_status`
  欄位。`repository.personnel_employment_status(personnel)` 這個 helper 讀
  取時，欄位不存在就當作「在職」（`LEGACY_PERSONNEL_STATUS`），而不是「待
  報到」——避免舊資料被誤判成剛建立、還沒報到。所有需要讀狀態的地方（清單頁
  篩選、詳細頁顯示、徽章）都要透過這個 helper 讀，不要直接 `personnel.get
  ("employment_status")`。
- **人員詳細頁**：`personnel_detail.html` 的一鍵全部更新表單最上面（原本
  合作方式/負責客戶選單那個 filter-bar，這次改成一定會顯示，不再只有
  `show_cooperation_type`/`show_client` 為真才顯示這個區塊）新增「人員狀態」
  下拉選單，欄位名稱 `employment_status`，後端 `bulk_update_personnel()`
  比照合作方式/負責客戶的處理方式：值合法（在 `PERSONNEL_STATUS_MAP` 裡）
  才寫入。
- **廠商人員清單頁篩選**（`vendor_list.html` / `vendor_routes.py`）：
  - 新增「狀態」下拉（`status` 查詢參數）：預設（沒選）不顯示「離職」
    「放棄報到」的人，跟應徵名單「放棄」預設隱藏是同一套邏輯——主動搜尋
    姓名、或直接篩選狀態為這兩項才會列出來。
  - 新增「缺件狀態」下拉（`missing_status` 查詢參數，選項：全部/缺件/
    無缺件）：**保留原本「已備齊的人預設不顯示，搜尋姓名才顯示」這個隱性
    規則不變**（下拉選單留在「全部」不選時就是這個行為），選「缺件」會
    強制只顯示缺件（即使有搜尋姓名也一樣濾掉已備齊的）、選「無缺件」會
    強制顯示已備齊的人（即使沒搜尋姓名也會顯示），純粹是這個規則之外
    多一個可以明確切換的輔助控制項。
  - `repository.personnel_matches_filters()` 因此多兩個參數
    `status_filter`、`missing_filter`，兩個篩選彼此獨立判斷，互不影響。
  - 清單表格新增「狀態」欄（原本紅框那個空欄位），用
    `PERSONNEL_STATUS_BADGE_CLASS` 對應的徽章顏色顯示（待報到＝黃、在職＝
    綠、離職＝灰、放棄報到＝紅），這幾個 class 定義在 `style.css`。

**已知限制**：`employment_status` 的篩選/預設隱藏邏輯只有單元測試 +
mock 過的 TestClient 手動測試；上線後建議實際把某個人的狀態改成「離職」，
確認清單頁真的會把他藏起來，而搜尋姓名／篩選狀態都還是找得到。

### 後續新增：應徵者廠商/合作方式/試駕（多來源表單）

背景：應徵者其實分蝦皮、UD、UC、順豐四個廠商（蝦皮底下又分二輪承攬/二輪
雇傭/三輪雇傭），實務上會是好幾份不同的 Google 表單各自對應一個廠商（或
蝦皮的一個合作方式），不是同一份表單。這一輪讓應徵名單也能反映「這個人是
從哪裡來的」，並加上試駕流程。

**廠商/合作方式怎麼「自動」判斷**：表單題目不會叫應徵者自己填廠商，是每個
表單各自的 Apps Script 觸發器，在打 webhook 時**直接夾帶固定的廠商代碼**
（`vendor`），蝦皮的表單再多帶一個合作方式代碼（`cooperation_type`）——
不是用題目內容去猜，最穩。看上一節「上線前要做的事」第 2 步的 Apps Script
範例，每個廠商各自的表單只要改 `vendor`/`cooperation_type` 這兩個值即可，
如果之後蝦皮真的拆成三份不同表單（二輪承攬/二輪雇傭/三輪雇傭各一份），
每一份都要各自設定一個 `onFormSubmit` 觸發器、`vendor` 都填 `"shopee"`、
`cooperation_type` 各自填對應的代碼。

- `delivery/routes/webhook_routes.py`：多讀 `body["vendor"]`／
  `body["cooperation_type"]`，不合法的值（不在 `VENDOR_MAP`／
  `COOPERATION_TYPE_MAP` 裡）一律當空字串，不會讓整個請求失敗。
- `repository.upsert_applicant()` 多兩個參數 `vendor`/`cooperation_type`，
  寫入應徵紀錄。**姓名+電話重複投遞覆蓋既有紀錄**這個既有規則不變，廠商/
  合作方式也會跟著這次投遞內容覆蓋；但**試駕狀態不會被覆蓋**——那是同仁
  操作的結果，不是表單填寫的內容，不該被重投表單洗掉（`upsert_applicant`
  裡特別把既有紀錄的 `test_drive` 帶過去，其餘欄位才是整包覆蓋）。

**試駕**：`config.py` 新增 `TEST_DRIVE_STATUSES`（`not_tested` 未試駕 /
`passed` 通過 / `failed` 未通過，預設 `not_tested`）。要不要試駕的判斷是
`repository.applicant_needs_test_drive(vendor, cooperation_type)`：
- UD、UC：一律需要。
- 蝦皮：只有合作方式是「三輪雇傭」才需要（二輪承攬/二輪雇傭不用）。
- 順豐：不需要。

**應徵名單頁面**（`applicants_list.html` + `applicant_routes.py`）：
- 新增「廠商」篩選（下拉，正常顯示，不特別隱藏「未指定」的人）跟「廠商」
  「合作方式」（只有蝦皮的列才顯示這個下拉，沿用 `COOPERATION_TYPE_VENDORS`）
  「試駕」（只有 `applicant_needs_test_drive()` 判斷為 True 的列才顯示這個
  下拉）三個可編輯欄位，都併進原本狀態的一鍵全部更新表單（路由從
  `/applicants/bulk-status` 改名成 `/applicants/bulk-update`，
  `repository.bulk_set_applicant_status` 改名/擴充成
  `repository.bulk_update_applicants`，接受 `{applicant_id: {欄位: 值}}`
  這種巢狀結構，每個欄位各自驗證合不合法）。
- 「錄取並建立人員」：廠商欄位直接沿用清單上那個下拉（不再是錄取那一列
  獨立的選單），送出時如果 `applicant_needs_test_drive()` 判斷需要試駕、
  而試駕狀態不是「通過」，會被擋下來、導回清單頁並顯示錯誤訊息——但擋下
  之前會先把這次提交當下選的廠商/合作方式/試駕存回應徵紀錄，同仁剛才選的
  東西不會因為被擋而消失、不用重選一次。蝦皮的應徵者通過檢查、成功錄取時，
  合作方式會一併帶進新建立的人員資料（`create_personnel(..., cooperation_type=...)`），
  不用進到人員詳細頁重新選一次。

**已知限制**：這一輪的廠商/合作方式/試駕邏輯只有單元測試 + mock 過的
TestClient 手動測試；上線後除了要記得去改每份表單各自的 Apps Script（貼上
新的 `vendor`/`cooperation_type` 值），也建議實際跑一次「試駕沒通過擋錄取」
跟「試駕通過才能錄取」這兩個情境確認行為正確。

### 後續新增：網頁上直接管理同仁帳號（新增/刪除）

背景：帳號原本只能透過 `python -m delivery.seed_admin` 這支命令列工具建立
（見前面「基本架構」那節），每次都要有 GCP 存取權限的人代為執行。這一輪讓
管理員角色可以直接在網頁上新增/刪除帳號，不用再麻煩人跑指令。

- `delivery/auth.py` 新增 `admin_required`：跟 `login_required` 一樣是路由
  依賴，但多檢查 `role == "admin"`；不是管理員一律導回主頁（不是丟 403，
  避免一般同仁看到陌生的錯誤頁）。另外新增 `list_users()`／`get_user()`／
  `user_exists()`／`delete_user()`／`count_admins()`（純 Firestore 存取）
  跟 `validate_user_deletion()`（純函式，判斷能不能刪除：不能刪自己、
  不能刪到剩 0 個管理員，這兩個規則都有寫單元測試）。
- `delivery/routes/user_routes.py`（新檔案）：`GET /users` 帳號清單、
  `GET /users/new` + `POST /users/new` 新增帳號（帳號重複或欄位缺漏會擋
  下並顯示錯誤，不會真的送出）、`POST /users/{username}/delete` 刪除帳號
  （刪除前會先查 `validate_user_deletion`，擋下的話導回清單頁顯示對應
  錯誤訊息：`self` 不能刪自己 / `last_admin` 至少留一個管理員 /
  `not_found` 帳號已經不存在）。這三支路由都掛 `admin_required`。
  這個功能刻意沒有「編輯帳號/改密碼」，只有新增/刪除（照需求範圍做，如果
  之後要補密碼重設，同仁現在還是只能請有 GCP 權限的人跑
  `python -m delivery.seed_admin` 覆寫）。
- `templates/users_list.html`／`user_form.html`：新增的兩個頁面，跟其他
  頁面風格一致。刪除按鈕有 `onsubmit="return confirm(...)"` 的瀏覽器內建
  確認對話框，避免手滑點到。
- `base.html` 的頂部導覽列，`user.role == "admin"` 時才會多顯示一個「帳號
  管理」連結，一般同仁看不到、也進不去（就算直接打網址也會被 `admin_required`
  導回主頁）。

**已知限制**：這一輪只有單元測試（`validate_user_deletion` 的邏輯）+ mock
過的 TestClient 手動測試（權限導向、新增/刪除各種擋下情境）；上線後建議
用你現有的兩個 ADMIN 帳號實際測一次「新增一個 staff 帳號」「刪除它」「試著
刪除自己」「如果只剩一個管理員，試著刪除它」這幾個情境確認行為符合預期。

### 後續調整：主頁改版、補款/假別各自拆成登記+查詢兩頁、核准機制、EXCEL 匯出

- **品牌名稱**：整個系統的名稱從「配送部系統」改成「材霈有限公司-配送部
  系統」，`base.html` 的頂部品牌文字跟每一頁的 `<title>` 都改了。
- **主頁「批次匯入人員」隱藏**：只是把主頁「選擇廠商」面板上的那個連結拿掉，
  `/delivery/import` 這支路由本身完全沒動、還是可以直接打網址進去（也還留在
  各廠商人員清單頁上方的「批次匯入」按鈕裡，那個沒有要求隱藏，維持原樣）。
  之後如果又要在主頁開放，把連結加回 `home.html` 就好。
- **主頁「選擇功能」改成 4 顆按鈕**：補款登記／補款記錄／假別登記／假別查詢，
  對應到下面拆開的 4 個路由。
- **補款/假別（原「病假」，這輪比照需求改叫「假別」並加上假別類型選單）
  都從「一頁同時有表單+清單」拆成「登記」「記錄/查詢」兩個獨立頁面**：
  - 登記頁（`/function/repayment`、`/function/sick-leave`）只留表單，不再
    顯示清單；日期欄位（補款的「日期」、假別的「開始日期」「結束日期」）
    預設值都是**今天**（後端算好 `date.today().isoformat()` 傳給樣板當
    `value`，同仁還是可以自己改）。
  - 記錄/查詢頁（`/function/repayment/records`、`/function/sick-leave/records`）
    新增可搜尋/篩選：人員姓名（局部比對）、廠商（下拉）、月份
    （`<input type="month">`，比對日期欄位開頭是不是那個「YYYY-MM」——補款
    比對 `occurred_date`，假別比對 `start_date`，也就是請假**開始**日期
    落在那個月就算），假別查詢頁另外多一個假別篩選。這些篩選邏輯都寫成純
    函式（`repository.repayment_matches_filters()` /
    `sick_leave_matches_filters()`），有單元測試。
  - `config.py` 新增 `LEAVE_TYPES`（病假/事假/特休/其他）跟
    `LEAVE_TYPE_MAP`；`create_sick_leave()` 多一個 `leave_type` 參數。
- **核准機制**（`approved` 欄位，補款、假別紀錄建立時預設 `False`）：
  - **單向**：核准只能從「未核准」變成「已核准」，沒有取消核准的路徑——
    `repository.bulk_approve_repayments()` / `bulk_approve_sick_leaves()`
    只會把指定的 id 設成 `True`，程式裡完全沒有寫「設回 False」的分支。
  - **只有管理員能操作**：核准的 POST 路由（`/function/repayment/records/approve`、
    `/function/sick-leave/records/approve`）都掛 `admin_required`；記錄/
    查詢頁面本身還是所有登入的同仁都能看，只是**只有管理員的畫面上才會有
    核准勾選框**，一般同仁看到的是唯讀的「未核准」灰底徽章（跟人員狀態徽章
    共用 `.badge-pending` 樣式）。已核准的一律顯示綠色「已核准」徽章
    （`.badge-ok`），不管是誰在看。
  - 操作方式是「勾選 + 一個『核准所選』按鈕」，一次可以核准多筆（跟應徵
    名單的批次狀態更新是同一個 UI 模式），而不是每列各自送出。
- **一鍵下載 EXCEL**（新增 `delivery/excel_export.py`，用 `openpyxl`——
  純 Python、沒有原生編譯依賴，加進 `requirements.txt`）：記錄/查詢頁上方
  的「一鍵下載 EXCEL」連結，會把**目前套用的篩選條件**（姓名/廠商/月份/
  假別）原封不動帶到 `/function/repayment/records/export`、
  `/function/sick-leave/records/export` 這兩支路由，匯出的內容是套用同一組
  篩選條件重新查一次的結果（不是只匯出畫面上剛好渲染出來的那一頁），欄位
  含「核准狀態」欄。`build_repayment_workbook()` / `build_sick_leave_workbook()`
  是純函式（輸入 records 清單、輸出 `.xlsx` 的 bytes），有單元測試驗證欄位
  順序跟內容正確。

**已知限制**：篩選/核准/匯出這些邏輯的純函式部分都有單元測試；跟 Firestore
真的互動的部分（`bulk_approve_*`、`list_repayments`/`list_sick_leaves` 實際
連線查詢）只有 mock 過的 TestClient 手動測試，上線後建議實際登記幾筆補款/
假別資料，跑一次「用姓名/廠商/月份篩選」「管理員核准、核准後檢查一般同仁
看到的畫面」「下載 EXCEL 打開確認欄位跟篩選範圍正確」這幾個情境。
