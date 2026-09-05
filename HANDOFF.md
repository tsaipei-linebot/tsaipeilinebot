# 招募機器人（沛沛）專案交接筆記

給 Claude Code 接續使用。這份文件整理目前為止已完成的工作，以及還沒開始、需要接續處理的待辦事項。

## ⚠️ 重要：這個 repo 同時有多個專案在使用，修改時務必只動自己負責的範圍

使用者透過 Claude Code 在**同一個 GitHub repo** 裡協作多個獨立專案，目前至少包含：
- 招募機器人「沛沛」（repo 根目錄：`main.py`、`config.py`、`handlers/`、`services/`、`tests/`、`scripts/`）
- 配送部系統（`delivery/` 目錄底下，含車輛回報、人員缺件管理等，是完全不同的子系統）
- 未來可能還會有更多專案陸續加進來

不管是哪個 Claude Code session 在處理哪個專案，**修改任何檔案前都要確認變更範圍只涵蓋當次任務相關的內容**，絕對不要異動、覆蓋、刪除其他專案的程式碼或文件。特別注意：
- `HANDOFF.md` 是**跨專案共用**的一份文件，各專案都會在這裡記錄自己的交接筆記。修改時一律用 Edit 做局部、精準的段落修改，**絕對不要用 Write 整檔覆寫**（整檔覆寫等於只保留自己讀到的那份內容，會把其他專案累積的紀錄整段清掉）。動手前先 `git diff` 確認自己的改動範圍夠小。
- 如果不確定某段程式碼/內容是不是屬於自己正在處理的專案，先用 `git log`／`grep` 確認再動手，不要用猜的。

## 專案基本資訊

- 專案性質：材霈有限公司的 LINE 招募聊天機器人「沛沛」
- 技術棧：FastAPI + line-bot-sdk + Notion API（職缺/FAQ 資料庫）+ Vertex AI Gemini（決策與回覆生成）+ Firestore（session/槽位儲存）
- GCP 專案 ID：`tsaipei-505807`
- 部署方式：接 GitHub，push 後由 Cloud Build 自動建置、部署到 Cloud Run（服務名稱 `recruitment-bot`，地區 `asia-east1`）
- Cloud Run 服務有 `/callback`（正式環境）與 `/test-callback`（測試環境）兩條 webhook 路由
- 檔案結構：`main.py`、`config.py`、`handlers/message_handler.py`、`services/session_service.py`、`services/matcher_service.py`、`services/notion_service.py`、`services/flex_service.py`、`services/ai_service.py`、`tests/`

## 待辦事項（下一步優先處理）

- **【需與外部工程師協調】線上履歷填完後自動跳轉回官方 LINE 帳號**：求職者點擊「填寫線上履歷」會被導去外部履歷系統（`resume.tsaipei.com.tw`，網址設定在 `config.py` 的 `DEFAULT_RESUME_URLS`），但填完表單後目前不會自動導回 LINE 官方帳號對話。這個機制牽涉到外部履歷系統那端的表單送出後導轉邏輯（例如導回 LINE 的 `line://` deep link 或加上完成頁），不是這個 repo 這邊能單方面決定/實作的，需要先跟負責 `resume.tsaipei.com.tw` 的外部工程師討論介接方式，確認後才回來這裡實作對應的程式（例如可能要在 `flex_service.py` 的履歷網址加上 redirect 參數，或是新增一個 webhook/callback 端點接收「已完成填寫」通知）。
- ✅ **帳單帳戶升級**：已完成，目前是正式付費帳戶（不是免費試用）。
- ✅ **【已解決並驗證，見下方「已完成」第 23 項】Vertex AI 回應延遲逼近/超過 LINE 30 秒 reply token 時限**：用 `scripts/load_test.py`（PR #13）在**已經是付費帳戶**的狀態下實測：
  - 併發 5、總數 30：p50=5.6s、p95=14.3s、p99/max=21.4s——還在範圍內，但長尾已經偏高
  - 併發 15、總數 50（修正前）：p50=4.2s、p95=29.0s、**p99/max=40.2s（已確定超過 30 秒）**，50 筆中有 3 筆超過 25 秒，且明顯集中在批次後段（觀察到「越晚送出的請求越慢」的雪崩效應）
  - 因為帳單已經是付費帳戶，**排除了「免費試用配額過低」這個解釋**，代表 Vertex AI Dynamic Shared Quota 在中等併發（15 左右）就會出現排隊/互搶的雪崩效應，推測是 `ai_service.py` 自己的 429 重試機制在多個請求同時觸發時，重試等待時間彼此疊加造成的。
  - 根本解法（限時同步等待＋長尾才背景 push）已實作完成並重新壓測驗證，細節見下方「已完成」第 23 項，包含一次踩坑與修正的紀錄（第一版把所有回覆都改成計費 push，第二版才修正成只有長尾才 push）。
  - ✅ **Cloud Run「CPU 一律配置」已開啟**：`gcloud run services update recruitment-bot --region asia-east1 --no-cpu-throttling`，目前修訂版本 `recruitment-bot-00126-7rs` 已套用。
  - 修正後併發 15、總數 50 重新實測：p50=4.50s、p95=9.59s、**p99/max=13.31s**（含網路）／伺服器端純處理 p99/max=10.78s，安全落在 30 秒門檻內。50 筆中有 15 筆（約 30%）落在 8~9 秒區間，確認長尾（ack+背景 push）路徑有被正確觸發。
  - 次要可以並行嘗試的方向（仍未做）：① 去 Vertex AI 主控台申請調高 Gemini 模型的配額上限（`gemini-2.5-flash`／`gemini-2.5-flash-lite`，地區 `global`），看能不能進一步緩解雪崩效應、降低走到長尾 push 路徑的比例；② 觀察到部分請求耗時明顯超過 8 秒同步時限（最高到 10.78s），推測是 Cloud Run vCPU 數量偏少、高併發下 Python GIL 競爭造成的延遲，可考慮檢查/調高 `recruitment-bot` 的 vCPU 配置。
- **【程式碼已完成但刻意保持關閉，等使用者確認要正式切換再開啟】日夜接力：白天真人、晚上沛沛**：程式碼端已完成（見下方「已完成」第 24、25 項）——同仁上班時段（10:10–18:50，含 10 分鐘交接緩衝）`message_handler.py` 會靜默略過所有訊息，交給真人在 LINE 聊天模式手動處理；這個時段之外才會進到原本的快速路徑／AI 決策邏輯。
  - ⏸️ **目前刻意關閉**：整個機制受 `config.py` 的 `STAFFED_HOURS_GUARD_ENABLED` 總開關控制（讀環境變數，預設 `false`）。使用者目前仍在測試頻道，且打算等外部工程師完成「線上履歷填完自動跳轉回官方 LINE 帳號」（見上一則待辦事項）之後才切換到正式頻道，這段期間如果守門邏輯生效、剛好在白天測試，機器人會靜默不回覆、容易被誤以為故障，所以暫時不開。
  - **確定要正式啟用時，要做兩件事**：① 在 Cloud Run 設定環境變數 `STAFFED_HOURS_GUARD_ENABLED=true`（不需要改程式碼、重新部署）；② 到 LINE 官方帳號後台「設定」→「回應設定」→「回應時間設定」，排程 10:10–18:50 切到「聊天」模式（同仁手動回覆）、18:50–10:10 切到「Bot」模式（webhook 交給沛沛），這步無法用程式碼代勞。兩者建議一起設定：就算 LINE 後台沒設定或設錯，只要①開了，我們自己的守門邏輯還是會擋住白天的自動回覆（2022 年更新後「聊天」模式跟 Webhook 可以並存），算是雙重保險；但只做①不做②，白天的訊息會進到 LINE 後台一般收件匣，同仁要主動去那邊看才會發現。
  - 已知限制：真人在 LINE App／OA 後台手動回覆完全不會寫入 Firestore（LINE 平台沒有提供這類事件的 webhook），晚間沛沛接手時看不到白天談過什麼，屬於預期中的限制，非 bug。
- **考慮加上錯誤告警機制**：目前所有例外只靠 `print()` 寫進 Cloud Run log，沒有主動通知。量小時人工看 log 還行，正式頻道建議至少設一個 Cloud Monitoring alert（例如 5xx 或例外次數異常）。
- **服務帳戶權限過寬，需要重新調整（安全性）**：確認過 `recruitment-bot` 服務目前使用的服務帳戶掛的角色是：服務帳戶使用者、記錄寫入者、**編輯者**、Aiplatform 編輯者、Artifact Registry 寫入者、Cloud Run 管理員。「編輯者 (Editor)」範圍過大（幾乎整個專案的資源都能讀寫），而且清單裡**沒有任何 Firestore/Datastore 相關角色**——代表目前機器人能讀寫 Firestore，其實完全是靠「編輯者」在撐著，這代表直接移除「編輯者」會讓機器人立刻壞掉。修正時**順序一定要對**，避免服務中斷：
  1. 先新增「Cloud Datastore 使用者」（`roles/datastore.user`）角色給同一個服務帳戶
  2. 找 LINE 測試頻道傳幾句話，確認機器人（尤其是需要 Firestore 讀寫的槽位記憶功能）一切正常
  3. 確認沒問題後，移除「編輯者」角色
  4. 再測一次，確認機器人依然正常運作
  - 其餘角色（服務帳戶使用者、記錄寫入者、Artifact Registry 寫入者、Cloud Run 管理員）研判是 Cloud Build 部署流程需要，可以保留；「Aiplatform 編輯者」可以考慮之後降級成範圍較小的「Aiplatform 使用者」（`roles/aiplatform.user`，因為只是呼叫 Gemini 生成回覆，不需要管理模型/端點的權限），非急迫。
  - 相關但優先度較低的資料安全項目，之後也可以一併處理：① Firestore 目前沒有資料保留/自動清除機制（`SESSION_TTL` 只是「軟過期」邏輯，使用者如果不再回來，session 文件會永久留在 Firestore，建議設定 Firestore 原生 [TTL 政策](https://cloud.google.com/firestore/docs/ttl) 自動清掉過期文件）；② 各項金鑰（`NOTION_API_KEY`／`GEMINI_API_KEY`／LINE channel secret／`LOAD_TEST_SECRET`）目前是明文 Cloud Run 環境變數，可以考慮搬到 Secret Manager 多一層存取控制與稽核紀錄。

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
23. **AI 決策改成「限時同步等待＋長尾才背景 push」，解決 reply token 30 秒逾時問題（PR #38，修正 PR #37 的成本問題）**：對應上方待辦事項「Vertex AI 回應延遲逼近/超過 LINE 30 秒 reply token 時限」的最終解法。`message_handler.py` 把 AI 決策丟進固定大小（32 個 worker）的 `ThreadPoolExecutor`，主執行緒最多同步等 `AI_DECISION_SYNC_TIMEOUT_SECONDS`（8 秒）：時限內算完就直接用 `reply_token` 回覆（免費，跟改動前行為一致）；超過時限才先用 `reply_token` 回一句「查詢中」的 ack，背景算完後改用沒有時間限制的 `push_message` 補發正式答案。原本的 `_run_ai_decision_and_push()` 拆成 `_compute_ai_decision_messages()`（純計算，回傳 messages、內部攔截例外回傳保底訊息）、`_fallback_messages()`（保底文案，兩條路徑共用）、`_push_ai_decision_messages()`（逾時後的 done-callback）。
    - **踩過的坑，下次改這段邏輯要記住**：PR #37 第一版把「所有」AI 決策都改成「立即 ack + 背景 push」，結果讓原本免費的 `reply_message` 全部變成計費、佔用 LINE 月則數的 `push_message`——即使大多數請求其實幾秒內就能算完、根本不需要 push。PR #38 才修正成「先同步限時等，只有真的算比較久的長尾請求才 push」。**任何時候要動這段邏輯，都要記得 reply_message 免費、push_message 計費，不要為了保證回得到而讓所有請求都改走計費路徑。**
    - ✅ **部署前提已完成**：Cloud Run「CPU 一律配置」已開啟（`--no-cpu-throttling`），目前修訂版本 `recruitment-bot-00126-7rs` 已套用，背景執行緒不會再受回應送出後的 CPU 節流影響。
    - ✅ **已重新壓測驗證**：`scripts/load_test.py --concurrency 15 --total 50` 實測 p99/max 從 40.2s 降到 13.31s（含網路）／10.78s（純伺服器處理），安全落在 30 秒門檻內，且有約 30% 請求觀察到落在 8 秒同步時限附近，證實長尾路徑確實有被觸發。細節見上方待辦事項。壓測時也順手發現並修掉一個測試盲點：`/internal/load-test-message` 端點的 stub 沒實作 `push_message()`，導致每次長尾請求都在 log 噴出無意義的錯誤（PR #40）。
24. **日夜接力：同仁上班時段沛沛靜默，交給真人手動回覆（PR #42）**：對應上方「日夜接力」待辦事項的程式碼部分。`config.py` 新增 `STAFFED_HOURS_START`（10:10）／`STAFFED_HOURS_END`（18:50）／`TAIPEI_TZ`；`message_handler.py` 新增 `_is_staffed_hours()`，`process_user_message()`／`process_image_message()` 一開頭就檢查，命中同仁上班時段（含 10 分鐘交接緩衝）就直接靜默 return，不做任何 Notion/Firestore/Gemini 呼叫。`process_user_message()` 新增 `bypass_staffed_hours_guard` 參數，只給 `/internal/load-test-message` 內部壓力測試端點用，避免壓測結果受執行當下是白天還是晚上影響。
    - **緩衝時間的取捨**：機器人比同仁實際下班（19:00）提早 10 分鐘於 18:50 啟動、比同仁實際上班（10:00）延後 10 分鐘於 10:10 才停止，寧可緩衝時段內偶爾跟同仁重複回覆（無害），也不要讓求職者在交接空檔完全沒人接（比重複回覆嚴重很多）。
    - **仍待使用者完成**：LINE 官方帳號後台的「回應時間設定」排程仍需手動設定，見上方待辦事項。
25. **日夜接力加上總開關，預設關閉（PR #44）**：PR #42 合併後這個守門邏輯原本會無條件生效、不分測試/正式頻道，但使用者當時仍在測試頻道、還沒設定 LINE 後台排程，也還打算等外部工程師完成履歷跳轉功能才切換正式頻道，如果守門邏輯已經生效，白天測試時機器人會靜默、容易被誤以為故障。新增 `config.py` 的 `STAFFED_HOURS_GUARD_ENABLED`（讀環境變數，**預設 `false`**），`process_user_message()`／`process_image_message()` 的守門判斷改成同時檢查這個開關，關閉時維持「不管幾點都照舊回覆」的舊行為。確定要正式啟用時只需要在 Cloud Run 設定 `STAFFED_HOURS_GUARD_ENABLED=true`，不用再改程式碼、重新部署。**目前這個環境變數尚未設定（等同關閉），日夜接力功能實際上還沒生效**，等使用者確認要切換正式頻道時再一併開啟（見上方待辦事項）。

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

### 後續新增：車輛管理（LINE 群組回報領車/還車 + 網頁管理）

背景：同仁在一個綁定的 LINE 群組裡用固定格式回報領車/還車，這一輪讓這個
回報自動寫進配送部系統，另外補上網頁端的車輛清單/新增/歷史/手動修正。

**重要更正（這次改版取代了第一版做法）**：車輛回報綁的是**另一個獨立的
LINE 官方帳號**，跟這支招募機器人（沛沛）是不同的 LINE Channel，訊息根本
不會經過 `main.py` 的 `/callback`。第一版把攔截邏輯寫進
`handlers/message_handler.py` 是錯的位置，訊息永遠不會進來、功能其實沒有
真的生效；這裡已經整個移除那段程式碼，改成下面這個正確的架構。

**實際架構**：這個另外的官方帳號，訊息是由一個完全獨立的 **Google Apps
Script 專案 `delivery-gas-project`**（另一個 GitHub repo：
`tsaipei-linebot/delivery-gas-project`）接收的——那個專案本身已經在跑「貨量
提醒」「違規騎手通知」「客訴」「排班」四個子功能，`doPost(e)` 是它自己
部署成 Google Web App 的 webhook，本來就支援兩個不同的 LINE Channel（用
`?bot=1`/`?bot=2` URL 參數分流各自的 Channel Token）。

車輛回報這條路徑是：
1. 同仁在綁定的群組傳訊息 → 進到 `delivery-gas-project` 的 `doPost(e)`。
2. `doPost(e)` 判斷這則訊息不是「綁定+工號+姓名」、而且來自 Script
   Properties 設定的 `VEHICLE_REPORT_GROUP_ID` 那個群組時，呼叫新增的
   `handleVehicleReport_(text, replyToken, lineToken)`（新檔案
   `Project5_Vehicle.js`），把整段文字用 `UrlFetchApp.fetch()` **轉發**到
   這支 Python 系統新增的 webhook：`POST /delivery/api/vehicle-report`
   （帶 `X-Delivery-Vehicle-Secret` header，密鑰即
   `DELIVERY_VEHICLE_REPORT_SECRET` 環境變數，跟 Google 表單那支
   `/delivery/api/form-submission` 是同一種「共用密鑰、不經過同仁登入
   session」的做法）。
3. Python 這邊呼叫既有的 `delivery.vehicle_report.handle_vehicle_report()`
   （下面「訊息解析」那段，完全沒有變動，第一版寫的邏輯照樣可用，只是
   呼叫入口從「直接被 message_handler.py 呼叫」換成「被 HTTP webhook 呼叫」），
   回傳 `{"reply": "..."}`。
4. GAS 收到回應後用 `replyLineMessage()` 把 `reply` 文字貼回 LINE 群組。

**群組白名單防呆機制搬到 GAS 那邊做**：`delivery-gas-project` 只有它自己
Script Properties 設定的 `VEHICLE_REPORT_GROUP_ID` 那個群組的訊息才會被
轉發過來；Python 這支 `/delivery/api/vehicle-report` 端點本身只驗證共用
密鑰，不重複判斷群組（沒有必要，因為只有握有密鑰的 GAS 腳本才能呼叫這支
端點，這正是它跟第一版最大的差別——它不再掛在招募機器人的 webhook 上，
不會因為判斷條件寫錯就永遠收不到訊息）。

**訊息解析**（`delivery/vehicle_report.py`，`parse_vehicle_report()` 是純
函式，`handle_vehicle_report()` 才會真的查/寫資料庫）：逐行找「廠商：」
「姓名：」「開始日期：」「結束日期：」「車號：」「服務門市：/還車地點：」
這幾個關鍵字開頭的行抓值，同仁把公司內部的範本說明文字一起複製貼過來也
不影響解析（不符合這幾個關鍵字的行直接忽略）。用「開始日期」還是「結束
日期」有填來判斷是領車還是還車，不是看最後一行的欄位名稱寫「服務門市」
還是「還車地點」——不管寫哪個都當作「地點」存。日期用寬鬆的
`_normalize_date()` 解析（「2026-8-25」這種沒補零的也接受），轉成系統
統一的 YYYY-MM-DD。廠商透過既有的 `VENDOR_LOOKUP` 比對（可以填代號或中文
名稱）。缺欄位/兩個日期都填/日期格式錯/廠商打錯字，都會直接回覆對應的
錯誤訊息到群組，不會靜默失敗或寫入垃圾資料。

**資料模型**（`db.py` 新增兩個 collection）：
- `delivery_vehicles`（車輛主檔，車號當文件 ID，車號全公司唯一，廠商是
  車輛固定屬性）：`vendor`、`status`（`available` 待領用／`in_use` 使用
  中／`maintenance` 待維修，見 `config.py` 的 `VEHICLE_STATUSES`）、
  `current_holder`、`current_location`、`last_event_at`。
- `delivery_vehicle_events`（事件紀錄，只增不改）：每筆領車/還車（不管是
  LINE 回報還是網頁手動補登）存一筆，含 `source`（`"line"`/`"manual"`）
  方便之後追查來源。

**擋止邏輯**（`repository.vehicle_event_error()`，純函式）：
- 車號不存在 → `vehicle_not_found`（回報前要先在網頁「車輛管理」新增這
  台車）。
- 回報的廠商跟車輛登記的廠商不一樣 → `vendor_mismatch`。
- 領車時車輛目前是「使用中」或「待維修」→ `not_available`（同一台車在
  被還車之前不能再被派出去）。
- 還車時車輛目前不是「使用中」→ `not_in_use`。
這組驗證邏輯被 `repository.record_vehicle_event()` 統一使用，LINE 回報跟
網頁上「手動補登事件」共用同一套規則，不會有兩條路徑各自的例外狀況。

**「待維修」是網頁手動切換的**：兩種 LINE 訊息格式都只有領車/還車，沒有
送修情境，所以待維修狀態是同仁在車輛詳細頁手動標記/解除的
（`repository.set_vehicle_status()`），跟 LINE 回報的事件紀錄是分開的兩條
路徑。

**網頁**（`delivery/routes/vehicle_routes.py` + 對應樣板，主頁新增「車輛
管理」面板）：
- `/vehicles`：車輛清單，可依車號/廠商/狀態篩選。
- `/vehicles/new`：新增車輛（車號不能重複）。
- `/vehicles/{車號}`：詳細頁——目前狀態/使用人/地點、標記待維修/解除、
  手動補登一筆事件（表單），跟這台車完整的歷史紀錄。

**上線前要做的事**（這次跟 `delivery-gas-project` 那個獨立 repo 一起動，
兩邊都要處理）：

1. Cloud Run 設定環境變數 `DELIVERY_VEHICLE_REPORT_SECRET`（隨機字串，例如
   `openssl rand -hex 32`）——這組要跟下面第 3 步在 GAS 那邊設定的
   `VEHICLE_REPORT_WEBHOOK_SECRET` 完全一樣。
2. 到 `delivery-gas-project` 那個 repo，把這次新增/修改的 `程式碼.js`、
   `Project5_Vehicle.js` 用 `clasp push` 同步進 Apps Script 專案，然後
   **記得重新部署**（Apps Script 編輯畫面「部署」→「管理部署作業」→編輯
   現有的那個部署（不要新增部署，網址才不會變）→版本選「新版本」→部署）。
3. 在 Apps Script 編輯畫面「專案設定」→「指令碼屬性」新增三個屬性：
   - `VEHICLE_REPORT_GROUP_ID`：要回報車輛的那個 LINE 群組 ID（拿法：把
     `CHANNEL1_LINE_TOKEN` 對應的官方帳號拉進那個群組、群組裡發一則測試
     訊息，這個 GAS 專案本來就有「訊息來自群組時把群組 ID 寫進表格」的
     機制——見「專案1功能：抓取群組 ID」那段，`RECORD_SHEET_ID` 指定的
     試算表 D1/E1 儲存格會出現群組 ID）。
   - `VEHICLE_REPORT_WEBHOOK_URL`：`https://recruitment-bot-412901869672.asia-east1.run.app/delivery/api/vehicle-report`
   - `VEHICLE_REPORT_WEBHOOK_SECRET`：跟第 1 步 Cloud Run 設定的
     `DELIVERY_VEHICLE_REPORT_SECRET` 同一組值。
4. 這個功能用的是 `CHANNEL1_LINE_TOKEN` 這組官方帳號（跟現有「綁定+工號+
   姓名」私訊功能同一個 Channel，`doPost()` 沒帶 `?bot=2` 參數時預設就是
   這組），確認 LINE Developers Console 裡這個 Channel 的 webhook URL
   設定的就是這次重新部署後（步驟 2）的 Apps Script Web App 網址。

沒有完成第 3 步的指令碼屬性設定時，`handleVehicleReport_()` 會直接記一行
log 然後不做任何事（不會報錯、也不會誤觸），等同這個功能完全關閉。

**已知限制**：`parse_vehicle_report()`、`vehicle_event_error()`、
`vehicle_matches_filters()` 這些純函式都有單元測試；`/delivery/api/vehicle-report`
這支端點的密鑰驗證/呼叫流程有 TestClient 手動測試；GAS 那邊 `doPost()` 的
群組判斷/轉發/錯誤處理邏輯用 Node `vm` 模組載入實際程式碼、餵假的
`PropertiesService`/`UrlFetchApp` 手動測試過四種情境（正確群組轉發成功、
其他群組不轉發、既有「綁定+」流程不受影響、webhook 回傳非 200 時優雅顯示
錯誤訊息），但沒有在真正的 Apps Script 執行環境（`clasp run`／實際部署）
跑過，語法只用 `node --check` 驗證過。上線後建議：先在網頁新增一台測試
車輛，到綁定群組實際傳一則領車格式的訊息確認寫入成功、狀態變成使用中，
再傳一次還車格式確認狀態變回待領用；也建議傳一則故意漏欄位或廠商打錯字
的訊息，確認機器人有回覆正確的錯誤說明而不是沒反應。

### 後續新增：意外事件回報（LINE 群組回報 + 網頁查詢/風險等級/結案）

跟車輛回報**同一個 LINE 群組**（同一個 `VEHICLE_REPORT_GROUP_ID`），同一套
「GAS 轉發 → Python 解析寫入 → 回傳文字給 GAS 貼回群組」架構，但走獨立的
webhook 端點/密鑰（`delivery/incident_report.py` + `/delivery/api/incident-report`），
兩個功能的解析邏輯完全分開。GAS 那邊用訊息裡有沒有「意外事件回傳格式」這個
啟動關鍵字，判斷這則群組訊息要走意外事件回報還是車輛回報（見
`程式碼.js` 的 `doPost()` group 分支、`Project6_Incident.js`）。

回報格式是「編號.欄位名：值」（編號可以是「1.」「1、」等寫法，甚至沒有編號
也可以，只認欄位名稱本身），11 個欄位：廠商名稱／身分類別（雇傭／承攬）／
人員名稱／發生時間（`9/4 11:00`，月/日 時:分，沒有年份，系統補上目前年份）／
發生地點／執行勤務中或上下班途中／是否報警（有／無）／受傷情形／是否聯繫
家屬（有／無）／是否牽扯他人（有／無）／意外事件經過。「★風險等級：(此欄
不用填寫)」這行系統會忽略——風險等級（低／中／高）跟結案狀態都不是回報時
填的，是管理員事後在網頁 `/incidents/{id}` 詳細頁設定/操作（單向操作，
比照補款/假別核准機制，僅限管理員）。

**這次新增了兩件跟車輛回報不一樣的事**：
1. **同一筆新回報要推播到兩個群組**：GAS 收到 Python 回傳的確認文字後，除了
   貼回原群組（`replyLineMessage`），還會用同一支 `sendLineMessage()` 推播
   同一則訊息到另一個群組（`INCIDENT_NOTIFY_GROUP_ID`，例如管理／督導群）。
2. **每週一未結案案件提醒**：這個不是 Cloud Scheduler 打 Python（那樣
   Cloud Run 就要另外持有 CHANNEL1 的 Token），而是在 Apps Script 那邊設一個
   **時間驅動觸發器**（跟 Project1/Project2/Project4 現有排程一樣的做法，
   人工在 Apps Script 編輯器「觸發條件」畫面新增，指到 `sendIncidentWeeklyReminder`
   這個函式，設定「星期一」「上午」執行），由 GAS 呼叫 Python 一支唯讀端點
   （`/delivery/api/incident-weekly-reminder-text`）取得未結案案件的提醒文字，
   有內容才用 GAS 自己手上的 `CHANNEL1_LINE_TOKEN` 推播回**原群組**（跟車輛
   回報同一個群組，不是上面那個「第二個群組」）——這樣 CHANNEL1 的 Token
   全程只存在 GAS 那邊，Python／Cloud Run 完全不需要它。

系統登入時的提醒（首頁看到「⚠️ 目前有 N 筆未結案意外事件」）是純網頁功能，
`home_routes.py` 讀 `repository.list_open_incident_events()` 的筆數，跟 LINE
沒有關係。

**上線前要做的事**（一樣兩邊都要動）：

1. Cloud Run 設定環境變數 `DELIVERY_INCIDENT_REPORT_SECRET`（隨機字串）——
   跟下面 GAS 那邊 `INCIDENT_REPORT_WEBHOOK_SECRET` 要完全一樣。
2. 到 `delivery-gas-project`，把新增的 `Project6_Incident.js` 跟修改過的
   `程式碼.js` 用 `clasp push` 同步，**記得重新部署**（編輯現有部署、選
   「新版本」，網址不變）。
3. 在「指令碼屬性」新增：
   - `INCIDENT_REPORT_WEBHOOK_URL`：
     `https://recruitment-bot-412901869672.asia-east1.run.app/delivery/api/incident-report`
   - `INCIDENT_WEEKLY_REMINDER_URL`：
     `https://recruitment-bot-412901869672.asia-east1.run.app/delivery/api/incident-weekly-reminder-text`
   - `INCIDENT_REPORT_WEBHOOK_SECRET`：跟第 1 步 Cloud Run 設定的
     `DELIVERY_INCIDENT_REPORT_SECRET` 同一組值（這支跟每週提醒那支端點
     共用同一組密鑰）。
   - `INCIDENT_NOTIFY_GROUP_ID`：每一筆新回報都要額外推播過去的第二個
     LINE 群組 ID（拿法跟 `VEHICLE_REPORT_GROUP_ID` 一樣：把官方帳號拉進
     那個群組發一則測試訊息，用「抓取群組 ID」那個既有機制拿）。
   `VEHICLE_REPORT_GROUP_ID` 沿用既有設定，不用重複設。
4. 在 Apps Script 編輯器「觸發條件」畫面手動新增一個時間驅動觸發器：函式
   選 `sendIncidentWeeklyReminder`，事件來源選「時間驅動」，類型選「週計時
   器」，時間選「星期一」+ 上午（例如 8-9 點）。

沒有完成第 3 步指令碼屬性設定時，`handleIncidentReport_()` /
`sendIncidentWeeklyReminder()` 都只會記一行 log 就結束，不會報錯、也不會
誤觸。

**已知限制**：跟車輛回報一樣，Python 那邊的解析/驗證純函式
（`parse_incident_report()`、`incident_matches_filters()`）有完整單元測試，
`/delivery/api/incident-report`、`/delivery/api/incident-weekly-reminder-text`
這兩支端點跟 `/incidents` 系列網頁路由都有 TestClient 手動測試（含管理員/
一般同仁看到不同畫面的驗證）；GAS 那邊的分流/轉發/推播兩個群組/每週提醒邏輯
一樣用 Node `vm` 模組手動測試過，沒有在真正的 Apps Script 環境跑過。上線後
建議：先傳一則完整格式的測試意外事件回報，確認原群組跟第二個群組都收到
確認訊息、網頁 `/incidents` 清單看得到這筆、管理員能設定風險等級跟結案；
也可以手動執行一次 `sendIncidentWeeklyReminder`（Apps Script 編輯器裡直接
執行這個函式），確認提醒訊息有正確推播回原群組。

## 新增：內部系統入口頁（`/portal`）

同仁除了配送部系統，另外也有一個獨立的「職缺維護系統」（同仁維護開放招募/
停招等職缺狀態，內容會直接寫進 Notion，再連動到官網跟招募機器人「沛沛」；
目前是 Netlify + Google Apps Script 架構，跟這個 repo 完全獨立、還沒有進
Git）。兩邊帳號密碼各自獨立、有一批同仁兩邊都要用，所以加了這支 `/portal`
路由當作「登入前選擇要進哪個系統」的導覽頁——**這支路由掛在 `main.py`
（根 app），不是 `delivery/` 子系統底下**，因為它要同時導去配送部系統跟一個
完全外部的系統，放進 `delivery/` 的話語意上會很奇怪。

- `main.py` 開機時讀一次 `portal.html`（存在模組層級的 `PORTAL_HTML`
  常數），`/portal` 這個路由直接回傳這份固定內容，沒有任何動態資料，也
  刻意不要求登入——這一頁本身不碰任何資料，只是連結，各系統各自的帳密還是
  在各自的登入頁輸入。
- `portal.html` 直接 `<link>` 引用配送部系統既有的
  `/delivery/static/style.css`（沿用同一套顏色/字體/卡片樣式，包括
  `.home-grid`/`.home-panel`/`.badge` 這些既有 class），配送部系統以後改
  配色，這一頁會自動跟著變，不用兩邊分別維護一份 CSS。
- 畫面上原本放了三張卡片，後來新增管理部系統時多加了第四張（見下面
  「多模組權限架構＋管理部系統」章節）：配送部系統（連去 `/delivery/login`）、
  管理部（連去 `/management/login`）、職缺維護系統
  （連去 `https://ubiquitous-choux-38eefb.netlify.app/`）、一張灰色虛線
  「更多部門系統／即將推出」佔位卡——這是使用者明確要求先放上去的，即使
  目前還沒有對應的下一個系統。
- 既有的 `/`（Cloud Run 健康檢查，回傳純 JSON）完全沒有動，`/portal` 是
  全新的獨立路徑。
- `/delivery/login` 這個網址本身沒有改變任何行為，同仁還是可以直接用原本
  的網址/書籤登入，`/portal` 純粹是額外多一個好記的共用入口，不是強制的
  單一入口。

**已知限制**：`tests/test_portal.py` 用 TestClient 驗證了頁面正常回應、
兩個系統的連結都在、佔位卡有出現、既有的 `/` 健康檢查沒被動到；另外用
`playwright` 手動截圖確認過桌面版跟手機版（`.home-grid` 既有的響應式
斷點在窄螢幕下會自動把卡片疊起來，不用額外寫 CSS）畫面正常、沒有跑版。
沒有做的事：這一頁完全是靜態連結，職缺維護系統那邊的網址如果之後換了，
要記得回來改 `portal.html` 裡的連結。

## 新增：多模組權限架構 ＋ 管理部系統（`/management`）＋ 帳號權限管理（`/accounts`）

背景：老闆打算開始規劃「管理部＋業務主管」專區（v1 先做公告事項/會議記錄/
規章SOP文件庫），這是繼配送部系統之後第一個新部門模組，藉這個機會把帳號
權限從「配送部專屬的單一 role 欄位」改成「一個帳號可以橫跨好幾個部門，
每個部門各自的角色分開設定」，登入一次就能在有權限的部門之間切換，不用
重複登入。

### 權限模型

沿用老闆畫的示意圖（總權限/各部門主管/各部門專員三層），存在 Firestore
`delivery_users` 這個既有 collection（沒有為了改名搬移正式環境資料，純粹
是命名上的歷史包袱）裡每個帳號文件的兩個新欄位：

- `is_platform_admin`（bool）：全平台只會有一個人（老闆本人），視同所有
  模組的管理員。**這個旗標不開放透過任何網頁表單修改**，只能透過
  `delivery/seed_admin.py --platform-admin` 或直接改資料庫設定，避免這麼
  關鍵的權限被誤觸。
- `modules`（dict，例如 `{"delivery": "admin", "management": "staff"}`）：
  帳號在每個模組各自的角色，"admin"（主管）或 "staff"（專員），沒有的
  模組代表完全沒有權限，連首頁都會被導去 `/portal`。

舊版的單一 `role` 欄位已經移除，改由這兩個欄位取代。

### 新增的核心檔案

- **`platform_db.py`**（根目錄）：使用者帳號的 Firestore 存取（從
  `delivery/db.py` 搬出來，因為帳號從此是全平台共用，不是配送部專屬）。
  `delivery/db.py` 保留 `from platform_db import get_db, users_ref` 向下
  相容既有的 `from delivery.db import users_ref` 呼叫端。
- **`platform_accounts.py`**（根目錄）：密碼雜湊/驗證、帳號 CRUD、
  `module_role()`/`has_module_access()`，以及三個 FastAPI 依賴工廠：
  `require_module_access(module_code)`、`require_module_admin(module_code)`、
  `require_platform_admin`。`MODULES` 這個清單就是目前掛載的部門模組
  （`delivery`、`management`），**之後每加一個新部門，只要在這裡多加一筆，
  `/accounts` 帳號權限管理頁面就會自動多一欄可以勾選**，不用再改權限邏輯
  本身。
- **`delivery/auth.py`／`management/auth.py`**：都改成薄薄一層包在
  `platform_accounts.py` 外面，各自把模組代碼固定成 `"delivery"`／
  `"management"`，並且 `current_user()` 會額外算出一個 `role` 欄位（只反映
  該模組自己的角色），讓既有樣板（`base.html`、`incident_detail.html`……）
  裡 `user.role == "admin"` 這種寫法完全不用改。
- **`/accounts`**（`accounts_routes.py` + `templates/accounts_list.html` /
  `templates/account_form.html`，掛在根 app）：唯一能新增/編輯/刪除帳號、
  勾選每個帳號在各模組角色的地方，只有 `is_platform_admin` 看得到。取代了
  舊版配送部系統自己的「帳號管理」（`/delivery/users`，已經整個移除）。

### Session 共用機制

`delivery_app`、`management_app`、根 `app` 三邊都各自掛一份
`SessionMiddleware`，但用**同一組** `secret_key`（`DELIVERY_SESSION_SECRET_KEY`）
跟**同一個** `session_cookie` 名稱（`"delivery_session"`，沿用配送部系統
原本取的名字，沒有改名）。因為 cookie 預設 `path="/"`，瀏覽器端就是同一顆
cookie，三個獨立掛載的 FastAPI 子系統可以互相讀到彼此寫入的登入狀態，
效果上等同單一登入（SSO），不需要額外的登入伺服器或跨服務呼叫。

**這個機制只在同一個 Cloud Run 服務、同一個網域底下的模組之間有效**
（配送部/管理部/未來新部門都算）。職缺維護系統是完全獨立在 Netlify 的
系統，不共用這個 cookie，還是要分開登入。

### 管理部系統（`management/`）

完全比照 `delivery/` 的目錄結構（`config.py`／`db.py`／`auth.py`／
`storage.py`／`repository.py`／`app.py`／`routes/`／`templates/`），掛在
`/management`。v1 三個功能，都是「管理員可以新增/刪除，所有有管理部權限
的同仁都能看」：

- **公告事項**（`management_announcements`）：標題+內容，純文字，沒有附件。
- **會議記錄**（`management_meeting_notes`）：標題/日期/部門（自由文字）/
  內容，可依部門篩選查詢。
- **規章/SOP 文件庫**（`management_documents`）：標題/分類/說明+必填的
  上傳檔案，檔案存在跟配送部系統同一個 GCS bucket（`DELIVERY_GCS_BUCKET`），
  blob 路徑前綴改成 `management/` 避免混在一起，一樣是私有 bucket、只能
  透過登入後的下載路由讀取。

三個功能都刻意不做「編輯」，只有「新增」跟「刪除」——比照這個 repo 一路以來
偏好單向操作的風格，之後如果真的需要編輯再加。

`management/templates/base.html` 直接沿用 `/delivery/static/style.css`，
沒有另外寫一份 CSS；首頁一開始就加了「回主頁」連結（沒有像配送部系統那樣
分兩階段補上）。

### 已知限制／尚未做的事

- `is_platform_admin` 的授予/收回完全沒有網頁介面，只能用
  `delivery/seed_admin.py` 或直接改資料庫，這是刻意的設計（見上面權限模型
  說明），不是遺漏。
- 三個管理部功能都沒有「編輯」，只有新增/刪除；公告/會議記錄也沒有像
  文件庫一樣支援附件——都是先做最小可用版本，之後真的有需要再擴充。
- **正式環境需要跑一次一次性遷移腳本**（新部署這批程式碼之後、同仁開始
  使用管理部系統之前）：
  ```bash
  python -m scripts.migrate_users_to_modules <老闆自己在配送部系統的帳號>
  ```
  這支腳本會把現有帳號的舊版 `role` 欄位轉成新版 `modules` 欄位，並把指定
  的帳號標記成 `is_platform_admin`；執行前會先印出即將變更的內容，要手動
  輸入 `yes` 才會真的寫入。沒有跑這支腳本的話，舊帳號會因為缺少 `modules`
  欄位而完全沒有任何模組的權限（`module_role()` 對空 dict 一律回傳
  `None`），需要透過 `/accounts` 由 `is_platform_admin` 帳號重新指派——但
  在還沒有任何 `is_platform_admin` 帳號之前，`/accounts` 本身也進不去，
  所以這支腳本是必要的第一步，不能跳過。
- 測試涵蓋範圍跟配送部系統一路以來的分工一致：純函式（密碼雜湊、
  `module_role`/`has_module_access`、遷移腳本的規劃邏輯）有完整單元測試；
  路由只測「不需要真的打 Firestore」的部分（未登入時的導向、登入頁渲染），
  需要模擬「已登入且有特定模組權限」才能測到的頁面內容，留給有 GCP 憑證
  的環境做整合測試。另外用偽造的 session cookie（跟 `SessionMiddleware`
  簽章方式一致，純粹本機手動驗證用，沒有寫進自動化測試）搭配 `playwright`
  截圖確認過管理部主頁、`/accounts/new` 帳號權限表單畫面正常。

## 新增：管理部系統 v2（業績報表庫／客戶拜訪紀錄／員工名冊+組織圖／資產設備）

v1 上線後老闆確認了業務主管專用、人事/組織這兩塊的具體需求，這輪補上四個
功能，都掛在既有的 `management/` 模組底下，沒有新增子系統。

- **會議記錄補上附件**：`meeting_routes.py` 新增選填的附件上傳（PDF/PPT/
  Word/Excel/圖片），沿用跟文件庫一樣的私有 GCS bucket，blob 路徑前綴
  `meeting-attachments/`。
- **業績報表庫**（`/management/kpi-reports`，`management_kpi_reports`）：
  刻意做成單純的檔案上傳/下載（Excel/PDF/PPT/圖片），不是計算目標達成率
  的儀表板——老闆明確表示這樣就夠用。管理員上傳/刪除，其餘有管理部權限的
  同仁可檢視/下載，跟文件庫是同一套可見範圍邏輯。
- **客戶拜訪紀錄**（`/management/client-visits`，`management_client_visits`）：
  欄位是客戶名稱/拜訪日期/約訪人員/拜訪人員/跟進狀態/備註。**這是目前
  唯一一個可見範圍不是「全部門共享」的管理部功能**：有管理部權限的同仁都
  能新增（不限管理員），但列表/詳細頁只有記錄本人（`created_by`）跟
  「管理部主管」（`user["role"] == "admin"`，全平台管理員也算在內，因為
  `platform_accounts.module_role()` 對任何模組都會回傳 admin）看得到，
  這一點跟公告/會議記錄/文件庫「全部門共享」的權限模型不一樣，見
  `repository.can_view_client_visit()`（有專門的單元測試涵蓋這個規則）。
- **員工名冊／組織圖**（`/management/staff-directory`、
  `/management/staff-directory/org-chart`，`management_staff_directory`）：
  跟配送部系統的「人員管理」是兩回事——那個是配送員/廠商人員，這裡是公司
  內部同仁（業務/管理部/內勤……），欄位刻意精簡成部門/姓名/職稱。組織圖
  不是另外維護一份資料，是同一份名冊依部門分組後的畫面呈現（見
  `repository.group_staff_by_department()`），畫成「部門卡片＋底下列出
  該部門同仁」的樣式，不是真正畫連接線的樹狀圖——如果之後需要匯報層級
  （誰的主管是誰）而不只是部門分組，欄位要再擴充（加一個「直屬主管」
  欄位），目前的名冊資料沒有這個資訊。
- **資產/設備管理**（`/management/assets`，`management_assets`）：分類
  固定四種（公務車、公務手機、門號、電腦——這裡的「公務車」跟配送部的
  車輛管理無關，是行政用途財產，不是配送人員在騎的營業用車），每筆記錄
  名稱/編號、保管人、狀態（使用中/閒置/維修中/報廢）、備註。

**已知限制／刻意的取捨**：
- 業務團隊人力配置/目標設定（老闆原本列的第三項）沒有另外做成獨立功能——
  老闆表示這階段只需要「掌握團隊人數狀況」，目標設定留到之後真的有需要
  再做，而「人數狀況」用員工名冊/組織圖（依部門分組後自然看得到每個部門
  幾個人）就已經涵蓋，沒有重複做一個功能。
- 這一輪新增的四個功能（業績報表庫/員工名冊/資產設備）都只有「新增」跟
  「刪除」，沒有「編輯」，跟這個 repo 一路以來偏好單向操作的風格一致；
  客戶拜訪紀錄比較特殊，多了「只有本人跟管理部主管看得到」的可見範圍限制。
- 測試涵蓋範圍延續既有分工：`can_view_client_visit()`／
  `group_staff_by_department()` 這兩個純函式有完整單元測試；路由只測
  「不需要真的打 Firestore」的部分（未登入時的導向）。另外用偽造 session
  cookie 搭配 `playwright` 截圖確認過管理部主頁（7 個功能卡片都正常排版）
  跟幾個新增表單頁面畫面正常。

### 資產/設備管理比照配送部車輛管理升級

老闆確認資產/設備要比照配送部系統的車輛管理（`delivery.repository` 的
vehicle 相關函式），原本的最小可用版本（只有新增/刪除）升級成：

- 新增資產詳細頁（`/management/assets/{id}`），狀態/保管人不再是建立時
  就固定寫死，而是可以隨時間更新，每次更新記一筆歷史事件（新增
  `management_asset_events` collection，見
  `repository.record_asset_event()`/`list_asset_events()`），做法完全比照
  車輛管理的「手動補登事件」機制。
- 資產文件多一個 `retired_at`（報廢日期）欄位，跟「狀態剛好是報廢」分開
  存——狀態改成「報廢」送出更新表單時，順便把當次填的日期存進
  `retired_at`，之後要盤點報廢時間不用去翻歷史事件表。狀態改回其他值不會
  自動清掉 `retired_at`（避免誤操作洗掉報廢紀錄）。
- 清單頁補上名稱搜尋、狀態篩選（原本只能篩分類），每列補上「查看詳細」
  連結。
- 狀態/保管人更新僅開放管理員（`admin_required`），跟新增/刪除的權限
  範圍一致；一般同仁仍可檢視詳細頁跟歷史紀錄。
