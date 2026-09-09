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

- **專案名稱：材霈招募聊天機器人**
- 專案性質：材霈有限公司的 LINE 招募聊天機器人「沛沛」
- 技術棧：FastAPI + line-bot-sdk + Notion API（職缺/FAQ 資料庫）+ Vertex AI Gemini（決策與回覆生成）+ Firestore（session/槽位儲存）
- GCP 專案 ID：`tsaipei-505807`
- 部署方式：接 GitHub，push 到 `main` 後由 GitHub Actions 工作流程（`.github/workflows/deploy.yml`，「Deploy to Cloud Run」）自動建置、部署到 Cloud Run（服務名稱 `recruitment-bot`，地區 `asia-east1`），細節見下方「CI/CD 自動部署」章節。單次部署歷史紀錄大約 2-3 分鐘跑完。
- Cloud Run 服務有 `/callback`（正式環境）與 `/test-callback`（測試環境）兩條 webhook 路由
- 檔案結構：`main.py`、`config.py`、`handlers/message_handler.py`、`services/session_service.py`、`services/matcher_service.py`、`services/notion_service.py`、`services/flex_service.py`、`services/ai_service.py`、`tests/`

## 待辦事項（下一步優先處理）

- **【上線前流量/正確性盤點，PR 待跑，見下方「已完成」第 27 項】**：使用者希望盡快切換到正式頻道，請 Claude 對現有程式碼、對話流程、可承受流量做一次全面盤點。找到並已在程式碼裡修好 4 個問題（見第 27 項細節），另外有幾項**需要使用者自己去 GCP 動手做，Claude 這邊沒辦法代勞**：
  1. ✅ **Cloud Run `--min-instances=1`：已完成**。用 `gcloud run services describe recruitment-bot --region asia-east1 --format="value(spec.template.metadata.annotations)"` 確認過，`autoscaling.knative.dev/minScale=1` 已生效，不會再有容器冷啟動疊加 AI 決策時間、逼近 LINE 30 秒時限的風險。同時確認 `run.googleapis.com/cpu-throttling=false`（CPU 一律配置，先前就設定過的仍在生效）、`run.googleapis.com/startup-cpu-boost=true`（額外加速容器啟動）。
  2. ✅ **正式上線前重新壓測：已完成，結果健康**。合併＋部署上方第 27 項的修正後，用 `scripts/load_test.py --concurrency 30 --total 100 --distinct-users 20` 實測：100 筆全部成功（無失敗），wall time p50=6.40s／p95=12.32s／p99=13.50s／max=13.50s，伺服器端純處理 p99=11.97s／max=11.97s——安全落在 LINE 30 秒 reply token 上限內（超過 2 倍餘裕）。**跟先前併發 15 的舊紀錄（見上方「Vertex AI 回應延遲」待辦事項）幾乎持平**（舊：wall p99/max=13.31s／伺服器 p99/max=10.78s），代表併發數翻倍後，`min-instances=1`＋CPU 一律配置＋這次修的 Firestore 並發問題，撐住了兩倍流量沒有明顯劣化。p50 落在 5-6 秒區間（一半以上請求要等 5 秒以上才有回覆），不是這次測試才有的新現象，是 Vertex AI 中高併發下既有的排隊現象；如果正式流量長時間維持併發 20-30 這個量級，可以考慮把「去 Vertex AI 主控台申請調高配額」這項低優先待辦往前提。
     - ✅ 壓測已經順便驗證了新加的監控結構化 log（`[AI_DECISION_LOG]`）在真正部署環境下有沒有正常印出來，如果要進一步確認可以去 Cloud Logging 篩選這個關鍵字看一眼。
     - **測完記得去 Cloud Run 環境變數把 `LOAD_TEST_SECRET` 清空或換掉**，不要讓這個能觸發真的 Vertex AI 呼叫的內部端點長期留著有效密鑰。
  3. ✅ **「幾百人同時對話」規模的壓測：已完成，過程中真的撞到並修正了容量上限**。使用者確認正式頻道預期併發規模是「最多幾百人同時對話」，實測過程：
     - 第一次 `--concurrency 200 --total 400 --distinct-users 100`：**16 筆（4%）直接被 Cloud Run 回傳 503 Service Unavailable**（不是我們程式碼的保底邏輯在回應，是 Cloud Run 基礎設施本身回絕），成功的請求 wall p99=24.09s／max=26.97s，明顯比併發 30 時差。判斷是撞到 `maxScale=10` 這個執行個體數上限。
     - 調高上限：`gcloud run services update recruitment-bot --region asia-east1 --max-instances=20`
     - 第二次 `--concurrency 200 --total 400 --distinct-users 300`（同時把不同使用者數拉高、降低同一人並發互搶 Firestore 文件的干擾）：**400 筆全部成功，503 消失**，證實 `max-instances=10→20` 是關鍵修正。延遲沒有因為拉高 distinct-users 而變好（伺服器端 p99 從 15.89s→19.76s，反而微幅上升）——代表延遲的主因不是這次修的 Firestore 並發問題，是 **Vertex AI Gemini 本身在高併發下的排隊效應**（HANDOFF 既有的「雪崩效應」現象，非新問題）。
     - **目前 `max-instances` 已經是 20**，如果之後正式流量長期維持在「幾百人同時」這個量級，建議把下面第 4 項提到的 Vertex AI 配額申請往前提，這是延遲的真正瓶頸。
  4. **服務帳戶權限過寬（已有的舊待辦，這裡追加一項）**：下方「服務帳戶權限過寬」待辦原本只提到要加「Cloud Datastore 使用者」，這次盤點監控機制時發現，等之後拿掉「編輯者」角色時，也要記得加「記錄檢視者」（`roles/logging.viewer`），不然每日/週報告會讀不到 Cloud Run 的 log。
  - 這幾項都是流量/GCP 設定層面，Claude 沒有這個專案的 `gcloud` 執行權限，需要使用者自己在 Cloud Shell 跑。

- ✅ **【已完成】`AI_DECISION_SYNC_TIMEOUT_SECONDS` 從 8 秒調高到 15 秒，並修好一個連帶發現的安全網缺口**：使用者反映 8 秒門檻太保守、太多請求落到計費的 `push_message`，成本偏高。討論後：
  - **沒有採用使用者原先提議的 25 秒**：這個數字離 LINE 30 秒 reply_token 硬性上限的緩衝太小——從 LINE 送出訊息到我們的程式碼真正開始計時，中間可能已經有排隊延遲（尤其高併發時，見上面第 3 項壓測的觀察），這段時間我們的程式完全量不到，25 秒的安全餘裕不夠。改成 15 秒，抓一個折衷值。
  - **這個數字改成可以用環境變數調整**（`config.py` 新增 `AI_DECISION_SYNC_TIMEOUT_SECONDS = int(os.getenv("AI_DECISION_SYNC_TIMEOUT_SECONDS", "15"))`，取代原本寫死在 `handlers/message_handler.py` 裡的常數 8），之後如果要再調整不用改程式碼、重新部署，直接在 Cloud Run 改環境變數即可。
  - **順便發現並修好一個連帶的安全網缺口**：原本「超過時限先送出『查詢中』的 ack」這段程式碼，如果 `reply_message()` 本身失敗（例如 reply_token 剛好已經過期——時限設得越接近 30 秒，這個情況越容易發生），會導致整個函式例外中斷、**根本沒機會把背景算完的正式答案排進 `push_message` 補發**，使用者會完全收不到任何回覆（比原本設計的「多等幾秒」嚴重很多）。已經把這段包進 `try/except`：就算 ack 送失敗，還是會繼續把正式答案排進背景補發，不受 ack 失敗影響。
  - **新增每週報告區塊：「同步回覆／背景補發比例」**，讓使用者可以每週檢視這個時限值設得好不好用——`services/daily_report_service.py` 新增 `compute_delivery_mode_summary()`，統計過去 7 天 `path=="ai_decision"` 事件裡，同步（免費）跟背景補發（計費）的次數與比例，只在週報那天附加顯示。push 比例持續偏高的話，代表現在的秒數接不住多數請求，可以考慮：① 調高這個環境變數（但要注意上面提到的安全緩衡）；② 更根本地去申請調高 Vertex AI 配額，讓 Gemini 本身回得夠快、自然就會落在免費的同步路徑內。

- **【新功能，需完成 GCP 設定才會實際運作】每週新工廠登記監控**：`services/factory_watch_service.py` + `main.py` 的 `POST /internal/factory-watch/run` 端點已完成，邏輯是每次執行去抓政府資料開放平台《[登記工廠名錄](https://data.gov.tw/dataset/6569)》（經濟部產業發展署），篩出近期新登記、且 Firestore 裡沒推播過的工廠，寫入 Google Sheet 明細，並視情況推播 LINE 摘要通知業務。要正式上線還缺以下設定（環境變數留空時，程式仍會安全跳過對應步驟並印出提示，不會噴錯）：
  1. 建一個 Google Sheet 當明細清單，分享編輯權限給 Cloud Run 服務帳戶（`tsaipei-505807` 專案的預設運算服務帳戶，或另外指定的服務帳戶信箱），把試算表 ID 設進 `FACTORY_WATCH_SHEET_ID`
  2. 決定 LINE 推播對象（業務同仁個人帳號或內部群組），取得 LINE user ID / group ID 後設進 `FACTORY_WATCH_LINE_TARGET_ID`（沒設定時只會更新 Sheet，不會推播）
  3. 設一個隨機字串當 `FACTORY_WATCH_TRIGGER_SECRET`，並在 GCP Cloud Scheduler 建一個每週五下午的排程 job，用 HTTP POST 呼叫 Cloud Run 的 `/internal/factory-watch/run`，帶上 header `X-Factory-Watch-Secret: <同一組密鑰>`
  4. 資料源的實際 CSV 下載連結是執行時動態去 data.gov.tw 資料集 API 探測的，欄位名稱也是用關鍵字比對（`services/factory_watch_service.py` 的 `COLUMN_KEYWORDS`），第一次正式跑之後建議看一次 Cloud Run log，確認欄位有抓對、筆數合理（開發環境的網路權限擋掉了 data.gov.tw，這部分沒辦法在開發階段實際跑一次驗證，只做過 CSV 解析/去重/訊息組裝等純邏輯的單元測試）

- ✅ **【已完成，外部系統端處理】線上履歷填完後自動跳轉回官方 LINE 帳號**：求職者點擊「填寫線上履歷」會被導去外部履歷系統（`resume.tsaipei.com.tw`，網址設定在 `config.py` 的 `DEFAULT_RESUME_URLS`）。原本填完表單後不會自動導回 LINE 官方帳號對話，這段導轉邏輯完全是外部履歷系統那端（`resume.tsaipei.com.tw`）負責，這個 repo 這邊沒有修改任何程式碼。使用者已於 2026-09-09 實測確認可以正常導回，這項待辦跟著解除；同時解除下方「日夜接力」待辦事項原本的其中一個前提條件（見下方說明）。
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
  - ⏸️ **目前刻意關閉**：整個機制受 `config.py` 的 `STAFFED_HOURS_GUARD_ENABLED` 總開關控制（讀環境變數，預設 `false`）。原本卡著這項待辦的兩個前提，**「線上履歷跳轉」已於 2026-09-09 完成並實測確認**（見上一則），**目前唯一剩下的前提是使用者自己確定要正式切換到正式頻道**——切換前如果守門邏輯生效、剛好在白天測試，機器人會靜默不回覆、容易被誤以為故障，所以暫時不開。
  - **確定要正式啟用時，要做兩件事**：① 在 Cloud Run 設定環境變數 `STAFFED_HOURS_GUARD_ENABLED=true`（不需要改程式碼、重新部署）；② 到 LINE 官方帳號後台「設定」→「回應設定」→「回應時間設定」，排程 10:10–18:50 切到「聊天」模式（同仁手動回覆）、18:50–10:10 切到「Bot」模式（webhook 交給沛沛），這步無法用程式碼代勞。兩者建議一起設定：就算 LINE 後台沒設定或設錯，只要①開了，我們自己的守門邏輯還是會擋住白天的自動回覆（2022 年更新後「聊天」模式跟 Webhook 可以並存），算是雙重保險；但只做①不做②，白天的訊息會進到 LINE 後台一般收件匣，同仁要主動去那邊看才會發現。
  - 已知限制：真人在 LINE App／OA 後台手動回覆完全不會寫入 Firestore（LINE 平台沒有提供這類事件的 webhook），晚間沛沛接手時看不到白天談過什麼，屬於預期中的限制，非 bug。
- **【程式碼已完成，還缺外部設定才會真正生效】監控與告警機制＋FAQ 週報**：把原本分開討論的「監控告警」跟「FAQ 週報／職缺關鍵字缺口」合併成同一支每日／每週排程端點實作，見下方「已完成」第 26 項的完整說明。這裡只記還缺什麼設定：
  1. **服務帳戶要能讀 Cloud Logging**：`run_daily_report()` 是直接查 Cloud Logging（`google-cloud-logging`），不是走 Cloud Monitoring 記錄型指標——這點跟原始設計草稿（「結構化 log → Cloud Monitoring 指標」）不同，是實作時的簡化：直接查 log 一樣能算出 p95／保底訊息次數，不用多一道設定記錄型指標的手續。目前服務帳戶靠「編輯者」角色能讀 log，但下方安全性待辦要拿掉編輯者時，記得要另外加「記錄檢視者」（`roles/logging.viewer`），不然這個報告會讀不到 log。
  2. **建 LINE 群組＋把沛沛加進去**，取得群組 ID 後設進 `DAILY_REPORT_LINE_TARGET_ID`（沒設定時只會印 log、不推播，可以先這樣測試觀察報告內容對不對）。
  3. **設一個隨機字串當 `DAILY_REPORT_TRIGGER_SECRET`**，並在 GCP Cloud Scheduler 建一個每天一次的排程 job，用 HTTP POST 呼叫 Cloud Run 的 `/internal/daily-report/run`，帶上 header `X-Daily-Report-Secret: <同一組密鑰>`（跟每週工廠監控端點的做法完全一樣）。
  4. **確定要正式生效時，設定環境變數 `DAILY_REPORT_ENABLED=true`**（預設關閉，即使 Cloud Scheduler 已經照排程在打這支端點，沒開這個總開關只會回傳「尚未啟用」、不會真的去讀 log／推播），比照「日夜接力」`STAFFED_HOURS_GUARD_ENABLED` 的做法。
  5. **「Claude 對話串」這個通知管道目前沒有做**：跟使用者討論後的結論是，正式生效的通知只走 LINE 群組（見上面第 2 點），比較不會因為某個 Claude Code session／排程沒有活著而漏發，這點是實作時額外的判斷，跟原始定案設計（雙管道都發）不同，請知悉。
  6. **原生 Cloud Monitoring alert（完全掛掉時 5 分鐘內就通知，不用等每日報告）尚未設定**：這是每日報告以外，另一層獨立的緊急備援，設定方式：
     ```bash
     gcloud logging metrics create ai_decision_fallback_count \
       --project=tsaipei-505807 \
       --description="沛沛 AI 決策保底訊息觸發次數" \
       --log-filter='resource.type="cloud_run_revision" AND textPayload:"[AI_DECISION_LOG]" AND textPayload:"\"fallback_triggered\": true"'
     ```
     建好這個記錄型指標後，到 Cloud Console「監控 (Monitoring) → Alerting」用這個指標建一個提醒（門檻設「5 分鐘內出現次數 ≥ 1」），通知管道選「Email」填自己的信箱即可，不需要再寫任何程式——這步驟用 Console 點選比用 gcloud 打指令更簡單，所以沒有另外寫 gcloud 指令。
  7. **FAQ 週報目前是「先列清單、不做 AI 語意分群」的簡化版**：`run_daily_report()` 在每週設定的那天（預設週一，`FAQ_WEEKLY_REPORT_WEEKDAY`）會把 Notion FAQ 資料庫裡「標準回覆內容」空白、且「啟用狀態」沒被手動設「停用」的問句原樣列出來（見下方「已完成」第 26 項），沒有像原本討論那樣額外呼叫 Gemini 做語意分群／建議答案草稿——因為目前候選問句量還不大（十幾筆），原樣列出已經堪用，等同仁實際用過、真的覺得候選清單太長太亂再考慮加 AI 分群，不是現在優先要做的事。
  - **FAQ 候選清單審核方式不變**：同仁看到週報列出的候選問句後，決定採用就去 Notion 填「標準回覆內容」（會自動生效）；決定不採用，記得手動把該筆的「啟用狀態」設成「停用」，不然下週還會重複出現在清單裡（見下方第 26 項的詳細說明）。
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
26. **監控與告警機制＋FAQ 週報：結構化 log＋每日/週報告端點**：對應上方待辦事項「監控與告警機制＋FAQ 週報」的程式碼部分，**預設關閉、還缺外部設定**（見上方待辦事項的完整清單）。
    - **結構化 log**：新增 `services/monitoring_service.py`，`log_ai_decision_event()` 把每次請求的處理結果印成一行 `[AI_DECISION_LOG] {...}` 開頭的 JSON（走的路徑、判斷出的職缺類別/廠商、action、有沒有觸發保底訊息、耗費秒數、是同步回覆還是逾時後背景補發）；`parse_log_line()` 是反向還原。`message_handler.py` 在 5 個會回覆使用者的地方都掛了這行 log：3 種精準工種直達攔截（外送/門市/momo）、全部瀏覽攔截、FAQ 高信心比對，以及 AI 決策路徑（同步成功／逾時後背景補發兩條路徑都有）。AI 決策路徑為了不更動 `_compute_ai_decision_messages()` 原本「只回傳訊息內容」的回傳值型別，改用新增的選填參數 `log_ctx: dict` 當共用小信箱，讓函式內部把 `action`／`fallback_triggered` 寫進去，呼叫端讀出來記 log，不影響原本呼叫端只需要處理回傳訊息的邏輯。
    - **每日/週報告服務**：新增 `services/daily_report_service.py`——`compute_health_summary()` 算第一層（保底訊息出現 1 次算異常）／第二層（過去期間依固定時間區塊分組，任一區塊 p95 延遲超過門檻算變慢，用固定區塊取代「任一 3 分鐘滑動窗口」，邏輯簡單很多、效果差異不大）；`compute_keyword_gap_candidates()` 統計繞去問 AI、但目前沒有專屬直達路徑（`外送`/`門市`/`momo` 以外）的職缺類別/廠商，被問到一定次數（預設 5 次，見 `FAQ_CANDIDATE_KEYWORD_GAP_MIN_COUNT`）就列入建議清單；`fetch_recent_log_events()` 是實際連 Cloud Logging 查詢過去 N 小時的 log（跟 `factory_watch_service.py` 的網路呼叫一樣，開發環境測不到，只做過純邏輯單元測試）。`services/notion_service.py` 新增 `fetch_pending_faq_candidates()`，重複使用既有的「啟用狀態」欄位分辨「待審」（空白）跟「已審核但不採用」（同仁手動設「停用」），不用新增 Notion 欄位。
    - **端點**：`main.py` 新增 `POST /internal/daily-report/run`，比照 `/internal/factory-watch/run` 的做法，用共用密鑰 `DAILY_REPORT_TRIGGER_SECRET` 驗證，交給 Cloud Scheduler 每天呼叫一次；`DAILY_REPORT_ENABLED` 總開關預設關閉，沒開之前呼叫這支端點只會回「尚未啟用」，不會真的去讀 log／推播（比照 `STAFFED_HOURS_GUARD_ENABLED` 的做法）。每週報告只在 `FAQ_WEEKLY_REPORT_WEEKDAY`（預設週一）當天才會多附加 FAQ 候選清單＋建議新增的職缺關鍵字兩段，其餘日子只有健康狀況。
    - **跟原始定案設計的兩個差異**（實作時的簡化，理由見上方待辦事項）：① 讀 log 的方式改成直接查 Cloud Logging，不是先設定 Cloud Monitoring 記錄型指標；② 只有 LINE 群組會收到報告，沒有另外接「Claude 對話串」這個通知管道；③ FAQ 候選清單目前是原樣列出 Notion 裡的待審問句，沒有加 Gemini 語意分群/建議答案草稿（候選量還小，先不做）。
    - **新增依賴**：`requirements.txt` 加了 `google-cloud-logging`（讀 Cloud Logging 用，`google-auth`/`google-api-python-client` 之前就有）。
    - **新增測試**：`tests/test_monitoring_service.py`（log 格式印出/還原 round-trip）、`tests/test_daily_report_service.py`（兩層門檻判斷、關鍵字缺口統計、報告文字組裝、週報日判斷，共 23 個測試）、`tests/test_notion_service.py` 補了 `FetchPendingFaqCandidatesTests`。
27. **上線前全面盤點：找到並修好 4 個流量/正確性問題**：使用者表示希望盡快切換到正式頻道，請 Claude 對現有程式碼、對話流程、可承受流量做一次全面檢查。逐一讀過 `session_service.py`／`matcher_service.py`／`notion_service.py`／`flex_service.py`／`ai_service.py`／`message_handler.py`／`main.py`／`Dockerfile`／`scripts/load_test.py`，發現並修正以下問題（其餘屬於 GCP 設定/流量層面、需要使用者自己動手的項目，列在上方待辦事項）：
    - **Firestore 讀-改-寫並發遺失更新（`session_service.py`，影響最大）**：原本 `update_user_slots()`／`append_user_history()`／`clear_user_slots()` 都是「獨立讀一次 → 在 Python 記憶體改 → 寫回」，如果同一個使用者短時間內有兩筆並發請求（例如連續快速傳兩則訊息、或第一則訊息的 AI 決策還在背景算的時候又傳了第二則——這在「限時同步等待＋逾時後背景補發」的架構下並不罕見），兩筆都讀到同一份舊資料，後寫入的會整份覆蓋掉先寫入的，可能造成剛設定的地區/班別條件、或某一則對話沒有被記錄下來就憑空消失。**現有的 `scripts/load_test.py` 其實一直都有機會觸發這個情境**（預設把多個模擬請求分配到同一批 `--distinct-users` 循環使用），只是原本的壓測只看回應時間跟狀態碼，沒有另外檢查 Firestore 資料本身有沒有遺失，所以先前沒被抓到。修正方式：把這三個函式的讀-改-寫包進 Firestore transaction（`db.transaction()` + `@firestore.transactional`），保證同一個使用者的並發更新不會互相蓋掉，Firestore 遇到衝突會自動重試。同時把三態合併、歷史紀錄裁切這兩段純邏輯拆成 `_merge_slot_updates()`／`_append_history_entry()`，方便不接真的 Firestore 也能單元測試（原本這個檔案完全沒有單元測試，新增 `tests/test_session_service.py`）。
    - **監控機制自己的盲點：Gemini 呼叫「優雅降級」時抓不到（`ai_service.py`／`message_handler.py`／`daily_report_service.py`）**：`query_gemini_ai()` 在 `MODEL_FALLBACK_LIST` 每個模型都失敗（例如配額用盡）時，會吞掉例外、安靜地回傳空字串，不會讓 `_compute_ai_decision_messages()` 走到 `except Exception:` 那個會標記 `fallback_triggered=True` 的分支——使用者只會收到「單一焦點引導」或預設問候語，感覺像正常對話，但這句話其實完全沒有被 Gemini 真的判斷過，而剛做好的每日健康報告卻會顯示一切正常。這是本次上線前盤點自己發現、屬於這次新加的監控功能本身的漏洞，已經一併修正：`log_ctx` 新增 `ai_decision_empty` 欄位（`action` 解析出來是空字串時設為 `True`），`log_ai_decision_event()`／`compute_health_summary()` 的第一層門檻改成 `fallback_triggered` 或 `ai_decision_empty` 任一個出現 1 次都算異常，報告文字也分開列出兩種次數方便判斷根因。
    - **FAQ 未收錄問題去重查詢完全沒有快取（`notion_service.py`）**：`_fetch_all_faq_question_titles()`（每次求職者問到未收錄的政策類問題就會呼叫一次，寫入前用來判斷是否重複）原本沒有快取，每次都整份掃描 FAQ 資料庫。這次盤點特別留意到一個時間點上的巧合：我們才剛決定「FAQ 候選內容先求量」（見上方 FAQ 週報待辦事項），加上正式頻道流量一多，這支路徑會同時變慢，也可能撞到 Notion API 速率限制——兩件事疊加起來風險比單看任一件事都大。修正方式：比照 `fetch_faqs_data()` 加上 `CACHE_TTL`（30 秒）快取；為了不讓快取視窗內的重複寫入去重失準，寫入成功後直接把新問題併入記憶體快取，不用整份重查。
    - **`/internal/load-test-message` 密鑰比對沒有用固定時間比較（`main.py`）**：這支端點會真的觸發 Vertex AI/Notion/Firestore 呼叫，原本用 `!=` 比較密鑰，跟其餘兩個內部端點（`/internal/factory-watch/run`、`/internal/daily-report/run`）用 `hmac.compare_digest` 不一致，理論上有時間旁道攻擊風險（密鑰外流或被猜到的話，可以拿去打真的 Vertex AI 燒帳單）。已改成一致用 `hmac.compare_digest`。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 441 個測試，OK。
28. **第二輪全面盤點：「用最仔細的方式檢查程式碼有沒有漏洞、邏輯問題或效能需優化」**：接續第 27 項的上線前盤點，使用者再要求一次更仔細的全面檢查。用 `code-review` Skill（max 強度，只掃 recruitment-bot 相關檔案）先跑出 14 個初步發現，每一項都回頭讀原始碼／寫測試驗證過是不是真的問題才動手修，找到並修正以下問題：
    - **`main.py` webhook 事件註冊寫法有整個服務啟動失敗的風險（嚴重度最高，影響範圍不只招募機器人）**：原本用 `@handler.add(...)`／`@test_handler.add(...)` 裝飾器語法註冊訊息處理函式。`handler`／`test_handler` 分別在 `LINE_CHANNEL_SECRET`／`TEST_LINE_CHANNEL_SECRET` 環境變數沒設定時會是 `None`，而裝飾器語法在「模組匯入當下」就會執行 `None.add(...)`、丟出 `AttributeError`——這個 `main.py` 是整個 Cloud Run 服務（`recruitment-bot`）的進入點，掛了不只招募機器人壞掉，同一個服務上的 delivery／management／hr 等其他子系統也會一起起不來。已改成先定義好一般函式，再用 `if handler: handler.add(...)(handle_message)` 這種「先判斷再手動註冊」的寫法，某個環境變數沒設定時只會跳過對應功能，不會讓整個服務起不來。
    - **`matcher_service.py` 品牌加分邏輯漏做全半形正規化（台積電查不到）**：`_score_job_for_ai()` 裡職缺加分用的 `brand_slot`（例如「台積電」，`KNOWN_BRANDS` 的 key 本身是半形「台」）沒有先經過 `clean_text_for_search()` 正規化，就直接拿去跟已經正規化過（「台」一律轉「臺」）的 `search_text` 比對字串包含關係，永遠對不到，這個 80 分的品牌加分形同虛設（sibling 函式 `_brand_matches_text` 一直都有做對這一步，這裡是漏掉了）。已修正成先正規化再比對，並新增 `ScoreJobForAiBrandBonusTests` 驗證台積電（有全半形問題）跟美光（沒有）兩種情況。
    - **兩處會組出空 LINE Flex Carousel 導致 API 拒絕的邊界情境（`message_handler.py`）**：「都給我看看」全部瀏覽攔截、以及 AI 決策 RECOMMEND 分支，原本在候選職缺篩到最後仍然是空清單時（例如 Notion 職缺剛好全部停招、或快取讀取失敗沿用了空快取），還是會硬組一個 0 張卡片的 Flex Carousel 送出，LINE API 會直接拒絕、使用者什麼都收不到。兩處都已改成：候選為空時改回一句老實的文字說明（「沛沛這邊目前暫時沒有符合的職缺資料，麻煩稍後再試一次…」），不再嘗試組空卡片。
    - **「查看職缺詳情」舊職缺名稱比對不到時會誤塞第一筆不相關職缺（`message_handler.py`）**：原本邏輯是「比對不到就用 `active_jobs[0]` 當預設值」，但這個預設值原意是給「使用者根本沒帶職缺名稱」這種情境用的；如果使用者確實帶了名稱、只是職缺剛好已經下架/改名（正式上線後會是常態），也會被塞進同一筆邏輯，導致使用者收到一個看起來正確、實際上答非所問的職缺詳情，還可能因此誤點應徵。已加上 `not target_title` 條件，只有真的沒帶名稱才用預設值；有帶名稱但比對不到時，改成往下走一般對話流程，交給 AI 決策判斷。
    - **同步回覆送出失敗時沒有補發機制（`message_handler.py`）**：AI 決策在時限內算完、準備用免費的 `reply_message()` 回覆時，如果這步本身失敗（例如 reply_token 剛好在排隊延遲後過期），原本會直接被例外中斷，已經算好的正式答案就這樣憑空消失，使用者完全收不到任何回覆。已加上 `try/except`：`reply_message()` 失敗時改用不受時效限制的 `push_message` 補發，避免使用者收不到回覆。
    - **Notion 職缺／FAQ 快取在高併發下有「快取驚群」風險（`notion_service.py`）**：`fetch_jobs_data()`／`fetch_faqs_data()` 只是簡單比對 `CACHE_TTL` 有沒有過期，沒有任何鎖——快取剛好過期的瞬間如果同時湧入多筆請求（壓測驗證過的「幾百人同時」規模下並非罕見），每一筆都會判斷「過期了」而各自重新打 Notion API，比原本只需要 1 次呼叫多出好幾倍，容易撞到 Notion API 速率限制。已改成雙重檢查鎖定（double-checked locking）：用 `threading.Lock()` 包住重新抓取的邏輯，同一輪只有一個請求真的去打 Notion，其餘請求等鎖放開後直接用剛更新好的快取。
    - **每次訊息多一次不必要的 Firestore 讀取（`message_handler.py`）**：`update_user_slots()` 寫回 Firestore 後其實已經回傳了寫回後的最新槽位，但後續丟進背景執行緒的 `_compute_ai_decision_messages()` 卻又呼叫一次 `get_user_slots()` 重新讀一次一模一樣的資料，等於每則訊息都多一次沒必要的 Firestore 讀取請求（正式流量放大後，這是白白多付的成本／延遲）。已改成把 `update_user_slots()` 的回傳值直接往下傳（新增選填參數 `known_slots`），沒有的時候才 fallback 回原本重新讀取的邏輯，避免影響其他呼叫路徑。
    - **壓測腳本 `--distinct-users 0` 會除以零崩潰（`scripts/load_test.py`）**：`user_id = f"loadtest-{i % args.distinct_users:04d}"` 在 `--distinct-users` 打成 0 時會直接拋 `ZeroDivisionError`，不是使用者操作打字失誤該有的錯誤訊息。已加上參數檢查，打錯時直接印出清楚的中文提示並結束，不會噴出 Python traceback。
    - **本輪暫緩、判斷優先度較低或需要更大改動、先不動的發現**（供之後參考，非本次必修）：① 背景補發 `push_message` 失敗時沒有重試機制（目前失敗只會記 log，使用者仍然收不到回覆——這是既有行為，這次沒有連帶擴大範圍去加重試邏輯）；② `ai_service.py` 的 Vertex AI client 初始化失敗後不會在後續請求自動重新嘗試初始化（冷啟動當下如果剛好初始化失敗，要等重新部署/重啟才會恢復，目前沒觀察到這個情況實際發生過）；③ 職缺評分邏輯裡，AI 自己前一輪的回覆文字也可能被當成比對來源之一，理論上有微小的自我偏誤風險；④ 「查看門市工作」這類意圖判斷目前只特別處理蝦皮這個品牌，其餘門市品牌沒有對應的精準攔截。這幾項都不影響正確性或會不會崩潰，只是可以再優化，之後有餘裕再處理。
    - **新增測試**：`tests/test_matcher_service.py` 新增 `ScoreJobForAiBrandBonusTests`（2 個）；`tests/test_message_handler.py` 新增 `test_sync_reply_failure_falls_back_to_push_message`、`test_recommend_with_no_candidates_returns_plain_text_not_empty_carousel`、以及 `DirectInterceptEdgeCaseTests` 整個新類別（空職缺清單「都給我看看」、比對不到的舊職缺名稱不再誤塞第一筆），共新增 5 個測試。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 457 個測試，OK。
29. **實際觀察到「重新部署後第一則訊息變慢」，加上服務啟動時預熱**：第 28 項合併部署當天，使用者在測試頻道連續回報三次「完全沒有人用的狀態下傳訊息，超過 15 秒觸發背景補發」（17:53、22:34、22:50）。逐一對照 GitHub Actions 的部署紀錄時間，三次都精準對應到「距離當時最近一次部署後，第一次真的有人傳訊息」的那個時間點——包含一次**觸發部署的改動跟招募機器人完全無關**（另一個系統只改了 HANDOFF.md 文件）的情況，因為這個 Cloud Run 服務是招募機器人跟配送部/管理部/人資等系統共用的，任何團隊推送到 `main` 都會讓整個服務重新部署、產生一支全新的執行版本。
    - **根本原因**：就算設定了 `min-instances=1`，新版本的容器裡，程式跟 Notion／Firestore／Vertex AI Gemini 這三個外部服務之間的連線都還沒真的建立過（連線是「第一次真的要送資料時」才會去握手），所以「重新部署後、第一個真的傳訊息的求職者」都要多負擔這段連線建立的時間——不管距離部署完成是 1 分鐘還是 14 分鐘，只要中間都沒有人傳過訊息，第一則訊息就是要付這個代價，跟「等多久」無關，跟「部署後有沒有人用過」有關。已經逐一排除過其他可能：不是今天改的程式碼造成的（改動內容都不影響單一請求、無並發情境下的效能）；也不是 Vertex AI 配額雪崩效應（那個現象不會精準對到部署時間點）。
    - **修正方式：`main.py` 新增服務啟動時的預熱機制**（`@app.on_event("startup")` 掛的 `_warmup_recruitment_bot_dependencies()`）：服務啟動、正式開始接受任何請求之前，依序對 Firestore（讀一個不存在的文件）、Notion（`fetch_jobs_data()`／`fetch_faqs_data()`，順便預先載入職缺／FAQ 快取）、Vertex AI Gemini（呼叫 `query_gemini_ai()` 送一句暖機測試訊息）各跑一次真的連線，讓連線建立的成本在使用者訊息進來之前就先付掉。FastAPI 的 startup 事件會在應用程式開始處理任何請求前執行完畢，所以無論是我們自己或其他子系統推送到 `main` 觸發的重新部署、還是流量升載多開一台執行個體，都不會再讓「剛好是第一個使用者」的人多等這段時間。三步都各自包一層 `try/except`，任何一步失敗只記 log、不讓服務因此啟動失敗（最壞情況只是退回沒有預熱的舊行為）。
    - **成本說明**：這個機制會讓每一次新的執行個體啟動時，多消耗一次很小的 Gemini 呼叫額度（暖機測試訊息很短，成本可忽略），不是持續性的背景保活，只在「真的有新容器啟動」時才會跑一次。
    - **有考慮過、但沒有採用的替代方案**：把 `AI_DECISION_SYNC_TIMEOUT_SECONDS` 從 15 秒再往上加 2-3 秒。沒有採用的原因：這個時限只是決定「用免費的 reply_message 同步回覆」還是「先回查詢中、改用計費的 push_message 補發」的分界，就算調高，使用者最終還是收得到答案，並沒有真正解決「重新部署後第一則訊息本來就會變慢」這個根本原因；而且會進一步壓縮到 LINE 30 秒 reply_token 硬性上限的安全緩衝（前一輪已經因為同樣理由，把使用者原本想要的 25 秒改成 15 秒）。啟動預熱是處理根因，不需要再動這個時限。
    - ✅ **已合併部署並實測驗證**：部署完成後 4 分鐘，在測試頻道傳訊息確認沒有再觸發 15 秒背景補發機制，符合預期。
30. **求職者提問追蹤：讓招募專員回頭找得到人**：使用者發現一個既有的落差——求職者問到 FAQ 沒收錄的問題（`action == "UNKNOWN_FAQ"`）時，機器人會回覆「已記錄、會由招募專員確認」，但原本的 `append_unresolved_faq_to_notion()` 只把「問題文字」寫進 Notion FAQ 資料庫（給未來的求職者累積常見問答庫用，且**有去重**），完全沒有留下「是誰問的」，加上這筆紀錄要等到每週一的週報才會被同仁看到——招募專員實際上完全沒有辦法真的回頭去回覆那個當下正在問的人，機器人等於是開了一張兌現不了的支票。討論後使用者選擇「補上追蹤能力」（而不是單純把話術改得比較保守，也先不做「即時推播到 LINE 群組通知」）：
    - **新增獨立的「求職者提問追蹤」資料庫**（跟既有 FAQ 候選資料庫分開，`config.py` 新增 `NOTION_UNRESOLVED_QUESTIONS_DB_ID`）：`services/notion_service.py` 新增 `append_unresolved_question_for_followup(question_text, user_id, display_name="")`，寫入求職者暱稱、LINE User ID、提問內容，`已回覆` 勾選方塊預設未勾。**故意不做去重**——跟 `append_unresolved_faq_to_notion()` 的設計目的不同：那邊要的是「這個問題只需要留一筆候選」，這裡要的是「每一次真人事件都要能找到當事人」，同一個問題如果有 5 個人各自問過，就要留 5 筆紀錄，去重反而會讓後面 4 個人的身分資訊憑空消失。
    - **`handlers/message_handler.py`**：`UNKNOWN_FAQ` 分支在原本呼叫 `append_unresolved_faq_to_notion()` 之後，另外呼叫 `target_line_bot_api.get_profile(user_id)` 取得求職者的 LINE 暱稱（包一層 `try/except`，使用者已封鎖官方帳號等情況會失敗，失敗時退回只記錄 user_id，不影響其他功能），再呼叫新的 `append_unresolved_question_for_followup()`。`target_line_bot_api` 因此新增為 `_compute_ai_decision_messages()` 的第 9 個參數（選填，預設 `None`，沒傳入時只記錄 user_id、不查暱稱，向下相容舊的直接呼叫方式）。
    - **同仁怎麼用**：招募專員定期（建議至少每天）打開這個新的 Notion 資料庫，看 `已回覆` 沒打勾的列，去 LINE 官方帳號後台（聊天列表）用「求職者暱稱」搜尋找到那個人的對話串，手動回覆完之後回來把 `已回覆` 打勾即可。**「LINE User ID」這欄不是給搜尋用的**（LINE 官方帳號後台沒辦法用這串 ID 搜尋，只能用暱稱）——保留這欄只是技術上的備用識別碼，用來因應多個求職者剛好用同一個顯示名稱、暱稱無法唯一區分的情況（使用者確認要保留，非必要不拿掉）。**要讓這個功能真的生效，使用者需要自己建立這個 Notion 資料庫並設定環境變數**，完整步驟見上方待辦事項；沒設定之前這個功能會安全跳過（只印 log），不影響其他功能。
    - **新增測試**：`tests/test_notion_service.py` 新增 `AppendUnresolvedQuestionForFollowupTests`（5 個，含「同一問題不同人問不會被去重」的關鍵行為）；`tests/test_message_handler.py` 新增 3 個測試（正常記錄暱稱、`get_profile()` 失敗時退回 user_id、沒傳 `target_line_bot_api` 時也不出錯）。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 465 個測試，OK。
    - ✅ **已合併部署並實測驗證**：使用者已建好 Notion 資料庫、設定好 `NOTION_UNRESOLVED_QUESTIONS_DB_ID`，部署後傳送 FAQ 未收錄問題實測確認有正確寫入「求職者提問追蹤」資料庫（含暱稱、提問內容）。
31. **FAQ 候選清單改成上線初期每天顯示（不用等到週一）**：使用者希望上線初期流量還小、需要密切觀察，FAQ 候選清單／建議新增的職缺關鍵字不要等到每週一才出現。新增環境變數 `FAQ_REPORT_DAILY_MODE`（`config.py`，預設 `false`）：開啟後不管星期幾，`run_daily_report()` 都會附加 FAQ 候選清單／建議關鍵字這兩段。**刻意只影響這兩段，不影響其他部分**：
    - 健康狀況檢查的時間窗口不受影響——只有真正的「週報日」（`FAQ_WEEKLY_REPORT_WEEKDAY`）才會用過去 7 天，其餘每天都還是過去 24 小時，避免視窗被連帶拉長而讓健康狀況誤判。
    - 「同步回覆／背景補發比例」那段（給 `AI_DECISION_SYNC_TIMEOUT_SECONDS` 調整參考用）維持只在真正的週報日才顯示，沒有跟著每天出現——使用者這次只要求 FAQ 候選清單提前，範圍沒有連帶擴大。
    - **這是給上線初期用的臨時開關，等流量穩定、同仁熟悉每天要看這份清單之後，可以考慮把 `FAQ_REPORT_DAILY_MODE` 關掉，改回原本每週一次的頻率**（在 Cloud Run 環境變數改成 `false` 或直接刪除即可，不用改程式碼、重新部署）。
    - **要讓這個切換生效，使用者需要自己在 Cloud Run 設定環境變數 `FAQ_REPORT_DAILY_MODE=true`**（前提是上方第 26 項「監控與告警機制」的 Cloud Scheduler／`DAILY_REPORT_ENABLED` 都已經設定好、每日健康報告已經在正常運作，這只是調整既有機制裡 FAQ 段落出現的頻率，不是獨立的新機制）。
    - **新增測試**：`tests/test_daily_report_service.py` 新增 `FaqReportDailyModeOverrideTests`（4 個，涵蓋開關開/關、健康視窗不受影響、同步/背景補發比例段落不受影響）。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 469 個測試，OK。
32. **正式上線試營運前最後一次全面檢查（邏輯＋安全性）**：使用者確認明天晚上要切換到正式頻道開始試營運，請 Claude 做最全面、詳細的邏輯與安全漏洞檢查。用 `code-review` Skill（max 強度，掃 `main.py`／`config.py`／`handlers/`／`services/`／`scripts/`／`tests/`，排除 `services/factory_watch_service.py` 等其他子系統範圍）找出 7 個發現，逐一讀原始碼驗證後，修正以下確認為真的問題：
    - **`config.py` 多個環境變數用 `int()`/`float()` 直接轉型，沒有防呆（風險最高）**：`os.getenv(name, default)` 只有在變數「完全沒設定」時才會用到預設值——如果使用者在 Cloud Run 主控台把值清空但沒刪掉那一列，變數會是空字串，`int("")` 會直接拋例外，讓 `import config` 失敗，導致整個 Cloud Run 服務（招募機器人＋配送部/管理部/人資等共用同一個服務的其他子系統）啟動失敗。HANDOFF.md 這幾天的紀錄顯示 `AI_DECISION_SYNC_TIMEOUT_SECONDS` 等好幾個數值都已經被手動調整過不只一次，使用者也才剛設定完好幾個新的環境變數，誤觸的風險並非理論上的假設。新增 `_int_env()`／`_float_env()` 兩個安全讀取函式（值缺漏或格式錯誤都安全退回預設值、印警告，不會讓服務掛掉），取代 `FACTORY_WATCH_LOOKBACK_DAYS`、`DAILY_REPORT_LATENCY_P95_THRESHOLD_SECONDS`、`DAILY_REPORT_LATENCY_BUCKET_MINUTES`、`FAQ_WEEKLY_REPORT_WEEKDAY`、`FAQ_CANDIDATE_KEYWORD_GAP_MIN_COUNT`、`AI_DECISION_SYNC_TIMEOUT_SECONDS` 這 6 處原本的裸 `int()`/`float()`。
    - **`message_handler.py` 最外層保底 except 沒有補發機制**：跟這次會期稍早修過的同步路徑／逾時 ack 路徑不同，最外層那個「任何非預期例外都會落到這裡」的保底 `except Exception` 區塊，原本呼叫 `reply_message()` 時完全沒有包 `try/except`——如果這個當下 reply_token 剛好已經過期（很可能，因為前面已經因為某個例外處理耗時），這次 `reply_message()` 失敗會直接讓例外原封不動往外拋，使用者這一輪完全收不到任何回覆（不是「多等幾秒」，是「什麼都沒有」）。已比照其他分支的做法，包一層 `try/except`，失敗時改用不受時效限制的 `push_message` 補發保底訊息。
    - **「查看職缺詳情」比對成功、跟就業服務法年齡/性別合規攔截這兩個分支，漏了寫入求職者這輪的對話歷史**：這個檔案裡其餘所有會回覆使用者的分支（全域重置、單一維度調整、禮貌收尾、全部瀏覽、精準工種直達、高信心 FAQ、AI 決策路徑）都會同時寫入「求職者」跟「招募顧問沛沛」兩則歷史，只有這兩個分支只寫了沛沛自己的回覆——下一輪 AI 讀到的對話歷史會變成「沛沛憑空開口」，缺了使用者實際問了什麼，可能讓 AI 誤判上下文或重複問已經問過的問題。已補上遺漏的 `求職者` 歷史紀錄。
    - **`matcher_service.py` 的 `detect_category_label()` 只檢查文字裡「第一個」符合的同義關鍵字有沒有被否定，跟它的對稱函式 `detect_negated_category()`（逐一檢查所有關鍵字）寫法不一致**：例如「外送」類別底下同時有「外送」跟「司機」兩個同義詞，句子「不要外送，我想要司機的工作」裡第一個比對到的關鍵字是被否定的「外送」，原本的寫法會讓整個類別直接判定成沒命中，白白漏掉後面明確肯定的「司機」訊號。已修正成逐一檢查該類別底下每個關鍵字，只要有任一個沒被否定就算命中。
    - **`handlers/message_handler.py` 全部瀏覽（「都給我看看」）分支還有一處多餘的 Firestore 讀取**：`update_user_slots()` 已經回傳寫回後的最新合併槽位（`current_slots`），這裡卻又呼叫一次 `get_user_slots()` 重新查一次一模一樣的資料——這正是這幾天已經在別處修過的「多餘 Firestore 讀取」同一類問題，這次盤點又抓到一處漏網的。已改成直接沿用 `current_slots`。
    - **`main.py` 啟動預熱機制的三個連線改成同時跑，而不是依序執行**：原本 Firestore／Notion／Vertex AI 三個預熱步驟是一個接一個依序呼叫，總預熱時間是三個時間加總；這段程式碼存在的目的就是要縮短容器啟動後「第一個真人使用者撞到冷連線」的風險窗口，讓三步依序執行反而拉長了這個窗口。已改用 `ThreadPoolExecutor` 讓三步同時跑，總時間只取決於最慢的那一個。
    - **安全性檢查結論**：webhook 簽章驗證（`/callback`、`/test-callback`）跟三個內部端點（`/internal/load-test-message`、`/internal/factory-watch/run`、`/internal/daily-report/run`）的密鑰比對都正確使用 `hmac.compare_digest`，沒有找到可被繞過的簽章驗證漏洞；沒有找到使用者輸入未經清理就用於 Notion 查詢／外部 URL／log 或直接回傳給使用者的注入風險；沒有找到 API 金鑰／密鑰被印進 log 或洩漏給外部使用者的情況。
    - **本輪發現但刻意不在這次處理的項目**：① `@app.on_event("startup")` 是 FastAPI 已標記淘汰的寫法，且 `requirements.txt` 完全沒有釘住任何套件版本——這代表未來任何一次重新部署（不管是招募機器人自己的變更，還是配送部/管理部/人資等其他子系統推送到 main 連帶觸發的重新部署）都可能意外抓到某個套件的新版本、行為跟現在不一樣。這個問題比較適合另外挑一個時間，先確認清楚目前線上實際跑的每個套件版本、逐一驗證過相容性之後再一次性釘住，不適合在上線前這麼緊迫的時間點倉促處理，避免因為版本釘錯反而製造新的相容性問題；② `delivery/routes/reminder_routes.py` 有一處密鑰比對用 `!=` 而非 `hmac.compare_digest`（時間旁道風險）——這是配送部系統的檔案，不屬於招募機器人負責的範圍，這裡只記錄下來，不會主動處理，需要的話請提醒負責配送部系統的 session 處理。
    - **新增測試**：`tests/test_config.py`（新檔案，7 個，涵蓋 `_int_env`/`_float_env` 空字串／格式錯誤／正常值三種情況）；`tests/test_message_handler.py` 新增 `DirectInterceptHistoryTests`（2 個）、`OuterExceptionFallbackTests`（1 個）；`tests/test_matcher_service.py` 新增 1 個混合同義詞否定測試。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 480 個測試，OK。

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

**同一起事件重複回報，改成覆寫而不是新增一筆**：以「人員名稱＋發生時間」
當作識別同一起事件的依據（見 `repository.create_incident_event()` /
`_find_incident_event_by_key()`），這兩個欄位完全相同的回報，會直接覆寫
既有那筆的回報內容（11 個欄位），不會多開一筆重複紀錄——涵蓋兩種常見情境：
同仁手滑重傳一模一樣的內容，或發現打錯字重新回報修正過的版本。刻意不覆寫
`risk_level`／`status`／`created_at`，避免同仁重傳同一起事件時，不小心洗掉
管理員已經做好的風險評估／結案狀態。`create_incident_event()` 回傳
`(incident_id, created)`，`created=False` 時 `handle_incident_report()`
回覆的文字會說「已更新」而不是「已登記」，讓同仁清楚知道系統認得這是同一起
事件的更新，不是又新開了一筆。

**這次新增了兩件跟車輛回報不一樣的事**：
1. **同一筆新回報要推播到兩個群組，但兩邊內容不一樣、而且只有成功登記才轉發**：
   原群組（`replyLineMessage`）收到的一律是配送部系統回傳的簡短確認句
   （`result.reply`）——不管是成功登記還是格式錯誤都會回這句，讓同仁知道
   有沒有填對。第二個群組（`INCIDENT_NOTIFY_GROUP_ID`，例如管理／督導群）
   收到的是同仁原始貼的完整回報文字（`text`，11 個欄位一字不漏，見
   `Project6_Incident.js` 的 `handleIncidentReport_()`），**但只有真的成功
   寫入系統（`result.ok === true`）才會轉發**——格式錯誤的嘗試不會讓管理／
   督導層也收到一則無效的錯誤訊息。`ok` 這個欄位是 Python 的
   `handle_incident_report()` 回傳的（見 `delivery/incident_report.py`，
   回傳值是 `(ok, reply)` 這個 tuple，`webhook_routes.py` 的
   `/api/incident-report` 端點把兩者都包進 JSON 回傳給 GAS）——**改動這兩
   支程式碼時，記得部署順序要先 Python（Cloud Run）再 GAS
   （`clasp push`），不然 GAS 還沒讀得懂 `ok` 欄位時，即使 Python 已經
   有回傳，順序顛倒也不會壞（`result.ok` 是 `undefined`、JS 當假值處理，
   頂多是那段期間第二個群組暫時收不到東西，不會誤發），但還是建議照順序
   部署比較乾淨。**
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
  `is_platform_admin`（老闆）看得到——連管理部的「主管」角色都看不到別人
  的紀錄，這一點跟公告/會議記錄/文件庫的權限模型不一樣，見
  `repository.can_view_client_visit()`（有專門的單元測試涵蓋這個規則）。
  畫面上的說明文字用「主管」這個字眼，但實際權限就是只認
  `is_platform_admin`，這是老闆明確要求的：只改文字、不要真的放寬權限，
  之後改動這段文字或邏輯時要留意兩者刻意不一致。
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
  客戶拜訪紀錄比較特殊，多了「只有本人跟老闆看得到」的可見範圍限制（畫面
  文字寫「主管」，見上一節說明）。
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

## 新增：統一登入入口（/login + 登入才看得到的 /portal）＋ 職缺維護系統免登入銜接

背景：老闆希望材霈內部系統的入口帳密統一——連到 `/portal` 就先要求登入
（不要進了各部門才各自登入一次），登入後依權限自由切換部門；另外還有一套
完全獨立的「職缺維護系統」（Notion + Google Sheets 為資料庫、Netlify +
Google Apps Script 架的靜態網站，網址
`https://ubiquitous-choux-38eefb.netlify.app/`，同仁用「姓名 + 4 碼 PIN」
登入，帳密是同仁在另一個 LINE 群組用機器人自動綁定寫進一份 Google
Sheet），老闆明確表示不想動那個專案的程式碼，只想讓同仁不用在兩套系統
之間重複輸入帳密。這輪把這兩件事一起做掉。

**統一登入 / 登入才看得到的 /portal**：
- 新增全平台共用的 `/login`（GET 顯示表單、POST 驗證）／`/logout`
  （`login_routes.py`＋`templates/login.html`），登入邏輯沿用既有的
  `platform_accounts.authenticate()`，跟 `/accounts`、各部門模組共用同一顆
  `delivery_session` cookie，這裡不是又做一套新的登入機制。`next` 參數
  控制登入成功後要導去哪裡，`_safe_next_path()` 只接受同站相對路徑，避免
  被拿來做開放式轉址。
- `/portal`（`portal_routes.py`＋`templates/portal_home.html`）從原本任何人
  都看得到的靜態頁面，改成登入後才看得到（`_require_login` 依賴：未登入
  導去 `/login?next=/portal`），而且只顯示這個帳號有權限的部門卡片
  （`platform_accounts.MODULES` + `has_module_access()` 篩選），不是固定
  的兩張卡片——之後新增部門模組不用再改這個頁面。各部門自己的登入頁
  （`/delivery/login`、`/management/login`）保留不動，作為深連結
  （direct link）進來時的備援，不是主要入口。
- `templates/base.html` 的頂部導覽列補上登入狀態（顯示姓名、
  `is_platform_admin` 才看得到「帳號權限管理」連結、登出按鈕），
  `accounts_routes.py` 的四個頁面補上 `user` 給樣板用。

**職缺維護系統免登入銜接**（`job_portal_sso.py` + `portal_routes.py` 的
`/portal/job-system-login`、`/api/job-system-sso/exchange`、
`/internal/sync-job-system-identities`）：
- 完全不動職缺維護系統那個專案的程式碼，只在我們這邊做銜接。運作方式
  分兩段：① 排程把「員工主管組織表」這份 Google Sheet 的姓名/PIN 欄位
  定期鏡射進 Firestore（`sync_identities_from_sheet()`，走 Cloud Run 掛載
  的服務帳號身分讀 Sheets API，不需要另外存一組憑證）；② 同仁在 /portal
  點「職缺維護系統」卡片時，拿他這個平台帳號的「姓名」（本名，跟職缺
  系統 Sheet 裡的姓名是同一個人、同一種寫法）去 Firestore 查對應的 PIN，
  查得到就簽發一組 45 秒後失效、簽章保護的一次性代碼放在網址上帶過去，
  查不到就直接導去職缺系統原本網址，同仁照舊手動輸入姓名/PIN，不受影響、
  也不會看到任何錯誤訊息——這張卡片刻意設計成「登入 /portal 的每個人都
  看得到」，比對不到才是唯一的差別。
- 代碼本身不是真的 PIN，安全性完全靠簽章合法+沒過期
  （`SSO_TOKEN_MAX_AGE_SECONDS = 45`），過期或偽造一律回傳 `None`，不區分
  原因。真正的姓名/PIN 是職缺系統的網頁自己在背景用 `fetch` 呼叫
  `/api/job-system-sso/exchange` 換回來，不會出現在網址上，比直接把 PIN
  放進網址安全很多。這支交換端點刻意不要求我們平台自己的登入 session
  （呼叫方是另一個網域的頁面，本來就沒有也不需要有我們的 session
  cookie），改用只允許職缺系統網域的 CORS 白名單
  （`Access-Control-Allow-Origin`）把關。

**上線前要做的事**（GCP 端的準備已經全部做完，只差下面兩步）：

1. 已完成：`gcloud services enable sheets.googleapis.com`（Sheets API）、
   確認 Cloud Run 服務帳號
   （`412901869672-compute@developer.gserviceaccount.com`）、把那份
   「員工主管組織表」Google Sheet 分享給這組服務帳號（檢視者權限）。
2. **還沒做，需要老闆或有權限的同仁執行**：
   - Cloud Run 設定環境變數 `JOB_SHEET_SYNC_SECRET`（隨機字串，例如
     `openssl rand -hex 32`），Cloud Scheduler 呼叫同步端點時要帶同一組
     在 header 裡。未設定時 `/internal/sync-job-system-identities` 一律
     回傳 403，等同這個端點不存在。
   - 設定 Cloud Scheduler 定期觸發同步（例如每小時一次，抓新綁定的
     同仁）：
     ```bash
     gcloud scheduler jobs create http job-system-identity-sync \
       --project=tsaipei-505807 \
       --location=asia-east1 \
       --schedule="0 * * * *" \
       --time-zone="Asia/Taipei" \
       --uri="https://recruitment-bot-412901869672.asia-east1.run.app/internal/sync-job-system-identities" \
       --http-method=POST \
       --headers="X-Job-Sheet-Sync-Secret=跟 Cloud Run 上設定的同一組密鑰"
     ```
   - 部署更新後的程式碼（`gcloud run deploy`），並且**至少手動打一次**
     `POST /internal/sync-job-system-identities`（帶正確的 header），否則
     在 Cloud Scheduler 第一次觸發之前，Firestore 裡還沒有任何職缺系統的
     身分對照資料，同仁點「職缺維護系統」卡片會全部落到「比對不到、導去
     原網址手動登入」的分支。
   - 職缺維護系統 `index.html` 需要補上一小段額外的 `<script>`（純新增，
     不改動既有任何一行）：覆蓋 `window.checkAndPromptLogin`，先檢查網址
     有沒有 `?sso=` 代碼，有的話背景呼叫
     `/api/job-system-sso/exchange` 換回姓名/PIN，再照原本手動登入會做的
     事（呼叫同一支 `VERIFY_LOGIN` GAS 端點、寫入
     `sessionStorage`/`currentUser`）；沒有代碼或交換失敗就呼叫原本的
     登入流程，不影響現有的手動登入。

**上線狀態**：以上設定（環境變數、Cloud Scheduler、`index.html` 銜接片段
手動上傳 Netlify）都已完成並實測成功，同仁登入 `/portal` 點「職缺維護
系統」卡片可以直接免登入進去。

**踩過的雷：職缺系統的 PIN 欄位存的是雜湊值，不是明文**——上線初期同步
完 Firestore 後實測，發現全部帳號都卡在手動輸入畫面、Console 顯示
`verify_login_failed`。追查那個系統的主程式（「程式碼.gs」）才發現
`EmployeeRegistrationService.processRegistration()` 寫入組織表時，PIN
其實是先做 `sha256Hash(pin)`（無鹽 SHA-256）才存進 Sheet；登入驗證
`OrgService.verifyEmployeePin()` 收到明文 PIN 後也會自己雜湊一次比對。
我們原本的同步邏輯把 Sheet 裡的雜湊值原樣存進 Firestore、再原樣轉發給
`VERIFY_LOGIN`，等於雜湊了兩次，永遠對不上。修法：PIN 只有 4 位數字
（10000 種組合），`job_portal_sso.py` 的 `_resolve_plaintext_pin()`
預先算好這 10000 種雜湊值對照回明文的表，同步時直接反查存明文 PIN
進 Firestore（仍相容舊資料的明文 4 碼格式，原樣使用不查表）。

**2026-09-09 補充：上面預言的「改了雜湊方式會整組失效」真的發生了，
已修好。** 使用者提供了職缺維護表單那個系統目前的完整原始碼（五份
`.gs` 檔）才發現：那個系統其實同時存在三種 PIN 格式（明文／無鹽
SHA-256／加鹽 SHA-256），而且**每次同仁登入成功，都會把那筆資料
自動升級成加鹽格式**（`OrgService.verifyEmployeePin()` 命中比對後
的自動升級寫回邏輯，加鹽用的密鑰是對方「指令碼屬性」裡的
`PIN_PEPPER`）。這代表就算我們這邊完全沒改任何東西，只要同仁自己
在對方系統手動登入過一次，那個人的免登入跳轉就會悄悄失效——不是
故障，是資料格式被對方升級了，而且會隨時間愈來愈多人受影響。

修法：`job_portal_sso.py` 新增 `JOB_PIN_PEPPER` 環境變數，反查表
（`_build_pin_hash_table()`）在有設定這個環境變數時，會額外算出
「加鹽格式」的反查表一併收錄；**沒設定的話行為完全跟修之前一樣，
不會出錯，只是加鹽格式那些人暫時還是查不到**（安全的退化，不影響
既有功能）。

**上線還差一步，需要使用者或有權限的同仁執行**：
1. 到職缺維護表單那個 Google Apps Script 專案的編輯器裡，左側齒輪圖示
   「專案設定」→「指令碼屬性」，找到 `PIN_PEPPER` 這一列，複製它的值
   （一段亂碼字串，不是我們自己設的，要跟對方系統完全一致才算得出
   一樣的雜湊）。
2. 在 Cloud Run 設定同樣值的環境變數 `JOB_PIN_PEPPER`（注意變數名稱
   跟對方系統的 `PIN_PEPPER` 不一樣，因為這是兩個獨立系統，用不同
   名稱區分清楚）：
   ```bash
   gcloud run services update recruitment-bot \
     --project=tsaipei-505807 \
     --region=asia-east1 \
     --update-env-vars="JOB_PIN_PEPPER=貼上剛才複製的值"
   ```
   這行指令會直接觸發重新部署一次新的修訂版本，等指令跑完（畫面顯示
   `Service [recruitment-bot] revision ... has been deployed`）就代表
   生效了。
3. 生效後不用手動做任何事，下一次 Cloud Scheduler 排程同步（每小時一次）
   就會自動把已經被升級成加鹽格式的同仁資料補上；如果想立刻生效，可以
   手動打一次 `POST /internal/sync-job-system-identities`（帶
   `X-Job-Sheet-Sync-Secret` header，做法同上線時的手動同步步驟）。

**如果之後對方系統又換了新的雜湊方式**，一樣要回頭看那支主程式的
`hashPinWithPepper()`/`verifyEmployeePin()` 現在怎麼做，重新調整
`job_portal_sso.py` 的反查表邏輯——這件事沒辦法一次徹底解決，因為
我們刻意不去動對方系統的程式碼，只能被動配合它的格式變化。

**已知限制／刻意的取捨**：
- Firestore 鏡射資料跟 Google Sheet 之間有同步延遲（取決於 Cloud
  Scheduler 排程頻率），剛綁定 PIN 的同仁要等下一次同步才會出現在免登入
  名單裡，這段時間點卡片一樣會落到「比對不到、導去原網址手動登入」，
  不會出錯、也不會卡住。
- 姓名比對是完全比對（strip 前後空白，其餘要求一致），沒有做模糊比對；
  兩邊姓名寫法如果不一致（例如簡稱、別名）就不會比對成功——這是老闆
  確認過可以接受的行為（「比對不到的就不給登入就好了」，不是這個功能要
  解決的問題）。
- 測試涵蓋範圍延續既有分工：`mint_sso_token()`/`verify_sso_token()`/
  `_resolve_plaintext_pin()`（純函式，不碰 Firestore）有完整單元測試，
  涵蓋正常換回、被竄改、過期、明文/雜湊 PIN 反查、反查不到等情況；路由
  只測「不需要真的打 Firestore」的部分（未登入時的導向、
  `/api/job-system-sso/exchange` 的代碼驗證邏輯、同步端點的密鑰檢查）。
  `sync_identities_from_sheet()`/`find_identity_by_name()` 需要真的連
  Google Sheets API / Firestore，留給有 GCP 憑證的環境做整合測試。

## 新增：資產管理「門號」繳費提醒 ＋ 管理部專屬 LINE 官方帳號

背景：公司內部同仁配有的公務門號有四、五十支，每支的繳費/扣款日因為
同仁到職日不同而分散在每個月不同的日子，容易忘記繳費。這次補上「每月
繳費日」欄位＋每週固定提醒，並且申請了一個全新的 LINE 官方帳號給管理部
專用（跟招募機器人沛沛、配送部完全獨立），避免推播/群組事務跟沛沛的
求職者對話 AI 混在一起。

**資產/設備管理：「門號」分類新增每月繳費日**
- `management_assets` 文件新增 `sim_payment_day` 欄位（1~31 的數字字串），
  只有分類是「門號」才有意義，其他 3 個分類一律是空字串。這是資產管理
  第一個「依分類顯示不同欄位」的案例（`asset_form.html` 用簡單的 JS
  依 `category` 下拉選單的值切換顯示，不是 delivery `DOC_TYPES` 那種
  設定檔驅動的機制，這裡欄位夠少，直接寫死一個 if 就好）。
- 新增/編輯都會驗證是 1~31 的整數，其餘一律當作沒填（`asset_routes.py`
  的 `_clean_sim_payment_day()`）；設定/修改繳費日跟保管人/狀態的異動
  是分開的獨立表單（`/management/assets/{id}/sim-payment-day`），不會
  留進歷史事件表——這不是一次「事件」，只是這顆門號本身固定屬性的修正。
- 換算下一次繳費日的邏輯（`repository._next_due_date()`）：如果今天已經
  過了這個月的繳費日就換算下個月；如果繳費日超過當月天數（例如 31 號
  但當月是 2 月），自動用當月最後一天代替。

**門號繳費提醒（每週一排程）**
- `POST /management/api/sim-payment-reminder-check`：Cloud Scheduler 每週
  一台北時間上午 9 點呼叫一次，比照配送部文件到期提醒的密鑰驗證作法
  （`X-Management-Asset-Reminder-Secret` header）。抓出「下一次繳費日
  落在今天起 7 天內」的所有門號（`repository.list_sim_payment_reminders()`），
  整理成一則訊息推播到管理部 LINE 群組。
- 固定每週觸發一次，天然不會對同一顆門號重複提醒，**不需要**像配送部
  文件到期提醒那樣另外記錄「提醒過了沒有」，邏輯比配送部那支還單純。

**管理部專屬 LINE 官方帳號**
- 全新申請的 LINE Messaging API Channel（`management/line_bot.py`），跟
  招募機器人（沛沛）、配送部完全獨立——如果共用沛沛的 Channel，管理部
  群組裡的任何訊息都會被沛沛現有的求職者對話 AI 接手處理，內部聊天會被
  誤判成求職者在問工作機會，所以另外申請一個全新帳號兩邊隔離。
- Webhook（`POST /management/line/callback`，見
  `routes/line_webhook_routes.py`）**只對特定查詢指令有反應**：在聊天室
  打「群組ID」／「groupid」（大小寫、有無空格都可以，見
  `_ID_QUERY_PATTERN`）才會回覆這個聊天室的 ID（群組回 Group ID、多人
  聊天室回 Room ID、一對一聊天回 User ID），其餘訊息一律不回應——這個
  群組之後會拿來給同仁討論事情，機器人不能對每一則訊息都跳出來回覆 ID
  打擾大家，只在真的需要查 ID 時才出聲。把這個新機器人拉進管理部 LINE
  群組後，發「群組ID」這則訊息，機器人就會回覆 Group ID，複製貼到環境
  變數即可——比配送部「去 Cloud Logging 撈 log」的做法更方便，而且完全
  沒有自動對話/AI 邏輯。
- `management/line_bot.py` 的 `push_group_message()` 是目前唯一的推播
  入口，只有門號繳費提醒會呼叫；之後如果管理部要加其他推播（公告發布、
  會議記錄等），都可以直接沿用這支函式。

**上線前要做的事**：
1. Cloud Run 設定環境變數：
   - `MANAGEMENT_LINE_CHANNEL_ACCESS_TOKEN`／`MANAGEMENT_LINE_CHANNEL_SECRET`：
     新申請的管理部 LINE 官方帳號的 Channel Access Token / Channel Secret。
   - `MANAGEMENT_LINE_GROUP_ID`：把新機器人拉進管理部群組、發一則訊息，
     機器人會直接回覆這個群組的 Group ID，複製貼過來即可。
   - `MANAGEMENT_ASSET_REMINDER_SECRET`：隨機字串（`openssl rand -hex 32`），
     Cloud Scheduler 呼叫時要帶同一組在 header 裡。
2. 到 LINE Developers Console 把這個新 Channel 的 Webhook URL 設定成
   `https://recruitment-bot-412901869672.asia-east1.run.app/management/line/callback`，
   並開啟「Use webhook」。
3. 設定 Cloud Scheduler 每週一觸發一次：
   ```bash
   gcloud scheduler jobs create http management-sim-payment-reminder \
     --project=tsaipei-505807 \
     --location=asia-east1 \
     --schedule="0 9 * * 1" \
     --time-zone="Asia/Taipei" \
     --uri="https://recruitment-bot-412901869672.asia-east1.run.app/management/api/sim-payment-reminder-check" \
     --http-method=POST \
     --headers="X-Management-Asset-Reminder-Secret=跟 Cloud Run 上設定的同一組密鑰"
   ```
4. 到每一支「門號」資產的詳細頁，補上實際的每月繳費日（新資產可以在
   建立時直接填，舊資產要一筆一筆到詳細頁補設定）。

**已知限制／刻意的取捨**：
- 沒有做「即將設定成非門號分類但已經有繳費日」的自動清空機制——如果
  管理員手動把一個已經設定繳費日的資產改成別的分類，`sim_payment_day`
  欄位會留著舊值但不會再被提醒邏輯用到（`list_sim_payment_reminders()`
  只掃 `category == "sim"` 的資產），不影響功能，純粹是欄位沒有物理清空。
- 順便在 `job_portal_sso.py` 的 Sheet 同步邏輯裡多存一欄「員工 LINE ID」
  進 Firestore（`job_system_identities` collection），目前沒有任何功能
  會用到，只是幫「以後可能的個人提醒功能」預先鋪路（老闆提到未來可能會
  想針對個人而不是整個群組推播）。
- 測試涵蓋範圍延續既有分工：`_parse_payment_day()`/`_next_due_date()`/
  `_ID_QUERY_PATTERN`（純函式/純規則，不碰 Firestore）有完整單元測試，
  涵蓋一般情況、月底天數不足、跨年等邊界狀況，以及查詢指令的各種寫法
  跟一般聊天訊息不會誤觸發；路由只測「不需要真的打 Firestore」的部分
  （未登入時的導向、提醒端點的密鑰檢查、LINE Webhook 在未設定/缺簽章
  header 時的行為）。`list_sim_payment_reminders()`（要連 Firestore）跟
  真正的 LINE 簽章驗證（line-bot-sdk 本身的責任）留給有憑證的環境做
  整合測試。

## 新增：人資專區模組（`/hr`）：意外通報／員工體檢報告／員工關懷彙整／公司證照彙整／教育訓練彙整

比照配送部/管理部的既有架構，新增第三個獨立部門模組，透過既有的多模組
權限架構（`platform_accounts.MODULES` 加一筆 `"hr"`）掛進 `/accounts`
帳號管理、`/portal` 入口頁，登入方式、session 共用機制都完全比照現有
模組，不用額外設計。原始需求裡的「員工資料彙整及追蹤」使用者要求先擱置
（可能跟管理部既有的「員工名冊/組織圖」有重疊，待確認後再補），這次先做
其餘 5 項。

### 意外通報（新群組，`hr/incident_report.py` + `hr/routes/incident_routes.py`）

跟配送部「意外事件回報」（見前面「配送部系統」章節）功能幾乎一樣——同一套
11 個回報欄位、風險等級（低/中/高）、單向結案機制、「人員名稱＋發生時間」
當作識別同一起事件的依據（重複回報會覆寫既有內容，不會多開一筆）——但
**沿用管理部現有的 LINE 官方帳號**（`management/line_bot.py` 那組，目前
只用在門號繳費提醒 + 回覆群組 ID），**不經過 `delivery-gas-project`**：

- 這組帳號的 webhook 本來就直接打進這支服務（`/management/line/callback`），
  不像配送部那組要先經過 GAS 轉發，所以不需要另外申請 LINE 帳號、也不需要
  動 `delivery-gas-project` 那個 repo。
- `management/routes/line_webhook_routes.py` 的訊息處理多一段判斷：訊息
  來自 `HR_INCIDENT_GROUP_ID` 這個群組時，改交給 `hr.incident_report`
  解析、寫入 `hr_incident_events`，並把結果直接回覆到同一個群組；不符合
  意外通報格式的訊息（例如日常聊天）保持沉默，跟這個群組原本「打群組ID
  才回覆」的行為並存，互不干擾。
- **只回覆同一個群組的登記確認訊息，不轉發到第二個群組**——比配送部那套
  單純（配送部因為要通知管理／督導層而多轉發一份原文到第二個群組，這次
  沒有這個需求）。
- **「廠商名稱」這裡改成自由文字**：不像配送部限定蝦皮/UD/UC/順豐，同仁
  填什麼就存什麼，不做白名單驗證（`hr/incident_report.py` 拿掉了對應的
  `invalid_vendor` 錯誤分支）。
- 未結案案件的每週提醒：新增 `POST /hr/api/incident-weekly-reminder-check`，
  Cloud Scheduler 呼叫，直接用 `management.line_bot.push_message()`（見
  下方新增的通用函式）推播回 `HR_INCIDENT_GROUP_ID`，不需要像配送部那樣
  另外在 GAS 那邊設時間驅動觸發器——因為 Python 這邊本來就有這組帳號的
  Token，不需要額外經過 GAS 才能推播。

`management/line_bot.py` 新增通用的 `push_message(target_id, text)`，
`push_group_message()`（門號繳費提醒用）改成呼叫它並固定帶
`LINE_NOTIFY_GROUP_ID`，人資這邊則是直接帶 `HR_INCIDENT_GROUP_ID` 呼叫，
共用同一個 LINE 帳號物件、同一組 Token，不用重複建立客戶端。

### 員工體檢報告／員工關懷彙整／公司證照彙整／教育訓練彙整

四個都是獨立的完整網頁功能（新增/搜尋/編輯/刪除），檔案上傳沿用跟配送部/
管理部同一個 GCS bucket（`DELIVERY_GCS_BUCKET`），blob 路徑前綴改成
`hr/`：

- **員工體檢報告**（`hr_health_checks`）：姓名/部門/受檢日期/下次應受檢
  日期/備註/報告檔案，可依姓名/部門搜尋。**目前沒有自動到期提醒**，只有
  統整＋人工查詢，之後真的需要再仿照公司證照的做法補上（使用者確認過
  這個範圍）。
- **員工關懷彙整**（`hr_care_logs`）：不做死板分類，日期/主題（自由文字，
  例如「關懷面談」「不法侵害會議記錄」）/當事人姓名（選填）/內容/可選
  PDF 附件，可用關鍵字搜尋主題/姓名/內容。
- **公司證照彙整**（`hr_licenses`）：公司本身持有的證照/執照/許可（不是
  同仁個人文件），名稱/發證機關/證照編號/到期日/檔案，到期提醒做法完全
  比照配送部文件到期提醒——`last_reminded_at` 記錄提醒時間避免短時間內
  重複提醒，到期日異動時清掉這個記錄讓提醒週期重新開始算。新增
  `POST /hr/api/license-reminder-check`，Cloud Scheduler 呼叫，**推播對象
  沿用管理部「門號繳費提醒」現有的群組設定**（`push_group_message()`），
  不用另外指定推播對象。
- **教育訓練彙整**（`hr_trainings`）：姓名/課程名稱/上課日期/訓練時數
  （選填）/備註/完訓證明檔案（選填），可依姓名/課程名稱搜尋。

### 上線前要做的事

1. **取得意外通報新群組的 Group ID**：把管理部那組 LINE 官方帳號拉進
   要回報意外事件的新群組，群組裡打「群組ID」，機器人會回覆 Group ID，
   設進 Cloud Run 環境變數 `HR_INCIDENT_GROUP_ID`。
2. Cloud Run 設定其餘兩組觸發密鑰（`openssl rand -hex 32`）：
   - `HR_INCIDENT_REMINDER_SECRET`
   - `HR_LICENSE_REMINDER_SECRET`
3. 設定 Cloud Scheduler 兩個每週排程（時段可以跟現有的門號繳費提醒錯開，
   例如同一天不同時間）：
   ```bash
   gcloud scheduler jobs create http hr-incident-weekly-reminder \
     --project=tsaipei-505807 \
     --location=asia-east1 \
     --schedule="0 9 * * 1" \
     --time-zone="Asia/Taipei" \
     --uri="https://recruitment-bot-412901869672.asia-east1.run.app/hr/api/incident-weekly-reminder-check" \
     --http-method=POST \
     --headers="X-Hr-Incident-Reminder-Secret=跟 Cloud Run 上設定的同一組密鑰"

   gcloud scheduler jobs create http hr-license-reminder \
     --project=tsaipei-505807 \
     --location=asia-east1 \
     --schedule="0 10 * * 1" \
     --time-zone="Asia/Taipei" \
     --uri="https://recruitment-bot-412901869672.asia-east1.run.app/hr/api/license-reminder-check" \
     --http-method=POST \
     --headers="X-Hr-License-Reminder-Secret=跟 Cloud Run 上設定的同一組密鑰"
   ```
4. 到 `/accounts`（全平台管理員登入）把需要用到人資專區的帳號，模組欄位
   勾選「人資專區」的主管或專員角色——這個頁面是自動根據
   `platform_accounts.MODULES` 產生的，不需要額外設定就會多出這一欄。
   全平台管理員（老闆本人）不用另外設定就已經視同人資專區的管理員。

沒有完成第 1、2 步設定時，意外通報只會被當成一般聊天訊息忽略（不會報錯、
也不會誤觸），兩個提醒端點會一律回傳 403，等同功能還沒生效。

**已知限制**：意外通報解析（沿用配送部同一套正則邏輯，只是拿掉廠商白
名單）、各功能的篩選/搜尋純函式都有完整單元測試；路由層級只測「不需要
真的打 Firestore」的部分（未登入時的導向、兩個提醒端點的密鑰檢查）。真正
會讀寫 Firestore/GCS 的路徑（新增/編輯/上傳檔案）以及 LINE 簽章驗證，
留給有 GCP/LINE 憑證的環境做整合測試；上線後建議實際跑一次「意外通報群組
回報一則測試訊息」「上傳一份體檢報告/證照確認下載連結正常」「證照到期
提醒手動觸發一次確認推播到管理部群組」這幾個情境。

## CI/CD 自動部署（GitHub Actions）

以前每次改完程式碼，都要使用者自己在 Cloud Shell 手動跑
`gcloud run deploy`（這個 repo）或 `git pull && clasp push`
（`delivery-gas-project` repo）才會真正生效。現在改成合併到各自的 `main`
分支後，GitHub 自動幫忙部署，不用再手動跑指令。

- **這個 repo（`tsaipeilinebot`）**：`.github/workflows/deploy.yml`，
  合併到 `main` 後自動用 `gcloud run deploy --source .` 部署到 Cloud Run
  服務 `recruitment-bot`。用的是 GCP 的 **Workload Identity
  Federation**（讓 GitHub 用「臨時身分」登入 GCP），刻意不把任何長期
  密鑰存在 GitHub 上，安全性比較高。
- **`delivery-gas-project` repo**：`.github/workflows/clasp-push.yml`，
  合併到 `main` 後自動 `clasp push` 到真正的 Apps Script 專案。這個沒有
  對應的「不存密鑰」做法（`clasp` 本身的機制就是要存一組登入後產生的
  授權資訊），存在 GitHub 這個 repo 的 Secret 裡（`CLASPRC_JSON`），只有
  repo 管理員看得到，一般協作者看不到內容。

**這兩個 workflow 都需要先做一次性設定才會生效**（設定完成前，workflow
檔案雖然已經存在，但因為缺必要的密鑰/身分設定會直接失敗，不影響現有
手動部署方式繼續運作）：

1. **Cloud Run 那邊**：在 Cloud Shell 執行下面整段指令建立一個專門給
   GitHub 用的身分（`github-actions-deployer` 服務帳號）：

   ```bash
   PROJECT_ID="tsaipei-505807"
   REPO="tsaipei-linebot/tsaipeilinebot"
   POOL_ID="github-pool"
   PROVIDER_ID="github-provider"
   SA_NAME="github-actions-deployer"
   SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

   gcloud config set project "$PROJECT_ID"

   gcloud services enable \
     iamcredentials.googleapis.com \
     sts.googleapis.com \
     run.googleapis.com \
     cloudbuild.googleapis.com \
     artifactregistry.googleapis.com

   gcloud iam service-accounts create "$SA_NAME" \
     --display-name="GitHub Actions 自動部署"

   for ROLE in roles/run.admin roles/iam.serviceAccountUser \
     roles/cloudbuild.builds.editor roles/artifactregistry.writer \
     roles/storage.admin; do
     gcloud projects add-iam-policy-binding "$PROJECT_ID" \
       --member="serviceAccount:${SA_EMAIL}" \
       --role="$ROLE"
   done

   gcloud iam workload-identity-pools create "$POOL_ID" \
     --location="global" \
     --display-name="GitHub Actions Pool"

   gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_ID" \
     --location="global" \
     --workload-identity-pool="$POOL_ID" \
     --display-name="GitHub Provider" \
     --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
     --attribute-condition="assertion.repository=='${REPO}'" \
     --issuer-uri="https://token.actions.githubusercontent.com"

   PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")

   gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
     --role="roles/iam.workloadIdentityUser" \
     --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/attribute.repository/${REPO}"

   echo "=== 請把下面這行完整貼給 Claude ==="
   echo "PROJECT_NUMBER=${PROJECT_NUMBER}"
   ```

   跑完之後，把終端機印出來的 `PROJECT_NUMBER=...` 那一行貼給 Claude，
   Claude 會把它填進 `deploy.yml` 裡原本寫 `<PROJECT_NUMBER>` 占位字串
   的地方，設定才算真正完成。

2. **`delivery-gas-project` 那邊**：需要先產生一份 `clasp` 的登入憑證，
   再存成 GitHub 的 Secret：
   ```bash
   npm install -g @google/clasp   # 如果 Cloud Shell 還沒裝過
   clasp login --no-localhost
   ```
   按照畫面指示，用**平常登入這個 Apps Script 專案的 Google 帳號**
   開啟印出來的網址、按同意，把瀏覽器最後跳轉到的那個網址（網址列裡
   會有 `localhost` 跟一長串 `code=...`，即使頁面顯示「無法連線」也
   沒關係，要的只是網址本身）整段複製，貼回 Cloud Shell 的提示。
   接著執行（**不要直接 `cat ~/.clasprc.json` 複製**——那是一整行很長
   的 JSON，Cloud Shell 顯示時會自動換行，滑鼠複製貼上很容易不小心混進
   換行符號，把 JSON 弄壞，之前就是這樣才第一次失敗）：
   ```bash
   cat ~/.clasprc.json | base64 -w 0
   ```
   這樣印出來的是一串 base64 編碼過的文字，即使複製時混進換行符號也
   不影響，之後 workflow 會自動解碼還原。把印出來的整段內容複製起來，到
   `https://github.com/tsaipei-linebot/delivery-gas-project/settings/secrets/actions`
   （或手動：repo 頁面 → Settings → Secrets and variables → Actions →
   New repository secret），Name 填 `CLASPRC_JSON`，Value 貼上剛剛複製
   的內容，按 Add secret（如果之前已經設定過一次，直接編輯覆蓋掉舊的
   內容即可）。

   **這組憑證等於是這個 Google 帳號的登入資訊，只有 repo 管理員看得到、
   不會顯示在任何 log 裡，但還是要留意不要把這段內容貼到別的地方。**

兩邊都設定完成後，之後只要 Claude 把程式碼合併進各自的 `main` 分支，
就會自動部署／`clasp push`，不用再手動執行指令；如果之後想暫停自動
部署（例如想手動控制上線時機），到 GitHub 該 repo 的 Actions 頁籤把
對應的 workflow 停用即可，不影響手動部署方式繼續運作。

**踩過的雷：`delivery-gas-project` 的 Apps Script 專案如果被重建過，
`.clasp.json` 裡的 `scriptId` 要記得同步更新**——2026-09-07 曾經花了不少
時間排查 `clasp push`（不管手動還是自動化）一律回傳
`The caller does not have permission`，一開始誤判是 Google Workspace
網域層級的第三方應用程式限制，換了三個帳號測試都一樣失敗；後來才發現
真正原因是 Apps Script 專案兩天前被重建過（新的 scriptId），但
`.clasp.json` 裡還是指向舊專案——舊專案已經沒有人有對應的存取權，
不管用哪個帳號都會是同一個錯誤。**以後如果 `delivery-gas-project` 的
Apps Script 專案又被重新建立、搬移、或改名，一定要記得同步更新
`.clasp.json` 的 `scriptId`**，否則不管是手動 `clasp push` 還是這裡的
自動化都會一直失敗，而且錯誤訊息長得完全像是權限問題，很容易誤判方向。

**踩過的另一個雷：`clasp push` 只會更新程式碼原始碼，不會自動反映到
「固定版本」的部署（deployment）上，LINE Webhook 如果接的是固定版本，
單純 push 完全沒用**——同一天（2026-09-07）解決 scriptId 問題之後，
`clasp push` 本身確實成功了，但使用者實測意外事件回報，發現第二群組
收到的還是舊邏輯（簡短確認句，不是完整原文）。原因是這個 Apps Script
專案有兩個部署：一個是 `@HEAD`（永遠對應最新程式碼），另一個綁定在
「固定版本」（`clasp deployments` 可以查到，deployment id 開頭
`AKfycbwbKr7...`）——LINE Developers Console 設定的 Webhook 網址接的是
後者，這種部署的程式碼版本要額外執行 `clasp deploy -i <deployment id>`
才會更新，push 再多次都不會自動反映上去。已經在 `delivery-gas-project`
的 `.github/workflows/clasp-push.yml` 加上 `clasp deploy -i ...` 這一步，
自動化現在會同時做 push + deploy 兩件事。**這一步比單純 push 需要更嚴格
的權限（要跟 Apps Script 專案擁有者同網域的帳號），如果之後這個自動化
又開始出現類似「明明 push 成功但沒生效」的狀況，先檢查 `CLASPRC_JSON`
這個 secret 存的帳號是不是還符合這個條件。**

## 新增：少凱業務開發專區改成權限控管 ＋ 唯讀網頁（不再直接連 Google Sheet）

背景：`/portal` 首頁原本有一張「少凱業務開發專區」卡片，是寫死在
`templates/portal_home.html` 裡的一個外部連結，直接連去 Google Sheet 的
編輯畫面——跟其他部門卡片不一樣，**這張卡片沒有走 `/accounts` 的權限
判斷，只要能登入 `/portal` 的任何帳號都看得到**，而且點進去是原始試算表
畫面（可以被誤改、排版也不好看）。這次改成兩件事：① 比照其他部門模組，
用 `/accounts` 控制誰看得到這張卡片；② 卡片點進去是這個系統自己畫的唯讀
網頁，不再直接連去 Google Sheet。

### 權限控管

在 `platform_accounts.py` 的 `MODULES` 多加一筆
`{"code": "salesdev", "name": "少凱業務開發專區"}`，跟其他模組一樣，之後
可以直接在 `/accounts` 頁面勾選哪些帳號看得到、`/accounts` 的帳號編輯表單
也會自動多一欄，不用另外改程式碼。**這次改完之後，除了老闆本人（全平台
管理員），沒有任何帳號會自動有這個模組的權限，要記得去 `/accounts` 手動
勾選誰可以看。**

### 唯讀網頁

- 新增 `services/salesdev_sheet_service.py`：用跟
  `services/factory_watch_service.py` 一樣的 Cloud Run 服務帳戶 ADC 連線
  Google Sheets API，差別是這裡只讀（scope 是
  `spreadsheets.readonly`），把整份試算表的每個分頁都讀出來，整理成
  `[{title, headers, rows, truncated}, ...]`。刻意不管試算表裡實際的欄位
  長什麼樣子（少凱業務開發的名單、工廠監控彙整可能是不同分頁、不同欄位），
  用「第一列當表頭、其餘都是資料列」通用處理，不寫死任何欄位名稱。單一
  分頁超過 500 列只顯示前 500 列（`truncated` 會是 `True`），避免資料量
  一多整頁跑很慢。
- 新增 `salesdev_routes.py` + `templates/salesdev_home.html`：`/salesdev`
  這個路由直接掛在根 `app`（跟 `/accounts`、`/portal`一樣），不像
  delivery/management/hr 是獨立掛載一個子系統——因為這裡資料量小、純唯讀，
  不需要自己的一整套子系統。畫面上每個 Google Sheet 分頁對應一個分頁
  按鈕（純前端 JS 切換，不用重新整理），每個分頁表格上面有一個純前端的
  搜尋框（比對整列文字，不用重新整理、不用打 API）。
- 讀不到 Google Sheet 時（試算表 ID 沒設定／沒分享權限／試算表被刪除）
  畫面上會顯示清楚的中文提示，不會噴 500 錯誤頁。
- 試算表 ID 設在 `config.py` 的 `SALESDEV_SHEET_ID`，預設值就是原本卡片
  寫死連去的那份試算表 ID，所以**不需要額外設定就能沿用原本那份表**；
  之後如果要換一份試算表，改 Cloud Run 環境變數 `SALESDEV_SHEET_ID` 即可，
  不用改程式碼重新部署。

### 上線前要做的事

1. **這份 Google Sheet 要分享「檢視者」權限給 Cloud Run 服務帳戶**（
   `tsaipei-505807` 專案的預設運算服務帳戶，或另外指定的服務帳戶信箱，
   在 Google Sheet 右上角「共用」設定）——沒分享的話 `/salesdev` 頁面會
   顯示「沒有權限讀取」的提示，不影響其他功能。
2. **到 `/accounts` 幫需要看這份資料的帳號（例如少凱本人）勾選「少凱業務
   開發專區」的權限**——改完程式碼這一刻起，除了老闆本人，沒有人會自動
   看到這張卡片／能打開這個頁面。

## 新增：我的專區（`/me`）＋ 第一項功能「薪資補款紀錄」

背景：使用者想要一種跟「部門模組」（同部門的人看到的東西都一樣，差別只在
專員/主管）不一樣的權限模式——**每個人登入後都自動有一個屬於自己的頁面，
裡面顯示「跟這個人有關」的資料，不相關的人打開也看不到別人的東西**，第一
個要用這個模式的具體需求是：同仁在「職缺維護表單」（Netlify + Apps
Script，跟這個 repo 完全獨立，見 CLAUDE.md）送出的薪資補款申請，只有申請人
本人跟他的主管看得到。

**這是一個全新的權限維度，不是「部門模組」的延伸**：部門模組控制的是「這個
帳號能不能打開這個頁面」，`/me` 是任何登入的帳號都能打開，控制的是「頁面裡
的資料哪些是這個人可以看的」，由各個小工具自己的資料來源決定怎麼篩選。

### 資料來源

薪資補款紀錄實際存在職缺維護表單背後的一份 Google Sheet（跟 `/salesdev`
連的完全是不同一份試算表，ID 設在 `config.py` 的
`SALARY_REPAYMENT_SHEET_ID`），裡面有兩個相關分頁：

- **「員工主管組織表」**（`SALARY_REPAYMENT_ORG_SHEET_NAME`）：材霈自己
  同仁（不是配送人員）對到誰是他的主管，「主管姓名」欄位可能是逗號分隔的
  多個名字（例如同時受兩個主管管轄，或是主管欄位填自己代表最上層）。這份
  資料同時也是職缺維護表單的 LINE 帳號綁定資料，含 LINE ID／PIN 碼雜湊等
  欄位，這次只取用「員工姓名」「主管姓名」「員工 LINE ID」三欄。
- **「薪資補款紀錄」**（`SALARY_REPAYMENT_RECORDS_SHEET_NAME`）：實際送出
  的補款申請。**欄位名稱容易搞混：「申請人姓名」是送出這張申請單的材霈
  同仁本人（例如內勤/主管），「員工姓名」欄位反而是這筆補款實際歸屬的
  配送人員（例如某個外送/門市工讀生）——判斷權限用的是「申請人姓名」，
  不是「員工姓名」。**「核准主管」欄位存的是 LINE ID 不是姓名，讀取時會
  用「員工主管組織表」的「員工 LINE ID」欄位換算回姓名再顯示。

比對邏輯是**姓名文字完全相同**才算（跟 `job_portal_sso.py` 的比對方式
一致）：`/me` 用登入帳號的 `name` 欄位，去跟「申請人姓名」比對——相同就是
自己送出的、或是自己的姓名有出現在該申請人的「主管姓名」清單裡就是主管，
兩者都不成立就看不到那一筆。**如果同仁在職缺維護表單填的姓名跟這裡登入
帳號設定的姓名不完全一樣（例如多打一個空格、用簡稱），會比對不到、直接
查無資料，不會噴錯誤，但也不會有任何提示告訴你是姓名對不上，之後如果
發現「明明送過申請卻看不到」，第一個要懷疑的就是姓名有沒有完全一致。**

### 新增的檔案

- `services/salary_repayment_service.py`：讀 Google Sheet、算權限、格式化
  資料的純邏輯，`get_my_repayment_records(viewer_name)` 是唯一對外的
  進入點，回傳 `(records, error)`。刻意不顯示「加項小計/扣項小計/補款
  金額(總計)」這幾欄——目前資料大多是 0 或只有零星幾筆有值，跟「實補
  總額」重複，先不顯示，之後財務真的需要拆項目再加回來即可（顯示欄位
  清單是 `DISPLAY_COLUMNS`，之後要調整顯示哪些欄位改這裡就好）。
- `me_routes.py` + `templates/me_home.html`：`/me` 直接掛在根 `app`（跟
  `/accounts`、`/portal`、`/salesdev` 一樣），只檢查有沒有登入，**沒有
  任何模組權限判斷**——這是刻意的設計，因為每個人本來就都應該看得到自己
  的專區，只是內容依人而異。
- `templates/portal_home.html`：在卡片區塊最前面加了一張「我的專區」，
  跟「職缺維護系統」卡片一樣放在權限迴圈外面，任何登入的帳號都看得到。

### 之後要加其他個人化功能怎麼做

比照 `services/salary_repayment_service.py` 的寫法，各自寫一個回傳
`(資料, error)` 的小工具函式，在 `me_routes.py` 多組一段 context 資料、在
`templates/me_home.html` 多加一段區塊即可，不需要改動 `/me` 的登入判斷，也
不需要去 `/accounts` 額外開權限——這就是這個模式存在的目的。

### 上線前要做的事

**這份 Google Sheet 要分享「檢視者」權限給 Cloud Run 服務帳戶**（
`tsaipei-505807` 專案的預設運算服務帳戶，或另外指定的服務帳戶信箱，在
Google Sheet 右上角「共用」設定）——沒分享的話 `/me` 頁面的薪資補款紀錄
區塊會顯示「沒有權限讀取」的提示，不影響頁面其他部分。

## 新增：帳號的「所屬主管」／「部門」欄位 ＋ 批次匯入既有主管關係

背景：程式碼審查發現 `/me` 薪資補款紀錄判斷「誰是誰的主管」，原本是讀
外部 Google Sheet（職缺維護表單背後那份）的「主管姓名」文字欄位比對——
這份資料這個系統自己完全管不到維護規則，容易出現兩種靜默錯誤：①
公司裡如果有同名同姓的人，比對會抓錯人；②姓名打法只要跟登入帳號設定的
不完全一樣（多一個空格、全形半形），會直接查無資料、不會有任何錯誤提示。
使用者確認後決定改成「主管關係直接存在系統自己的帳號資料裡，在 /accounts
網頁上設定」，從根本排除文字比對的問題。

### 資料改動

`platform_accounts.py` 的帳號資料多兩個欄位：
- `manager_usernames`：這個帳號的主管，存的是**別的帳號的 username**
  （不是文字姓名），可以有多個（沿用外部試算表原本就有「一個人可能同時
  受兩個主管管轄」的情況）。比對用帳號本身，不會有同名同姓的問題。
- `department`：部門，自由文字。**這次只加欄位跟 `/accounts` 的輸入介面，
  不批次匯入任何值**——照使用者要求，這個欄位由使用者之後自己手動在
  `/accounts` 填，還沒有任何功能會用到這個欄位。

`/accounts` 新增/編輯帳號的表單多了「部門」文字欄位、「所屬主管」多選
下拉選單（選現有帳號，編輯自己時會排除自己，避免選到自己當自己的主管）；
帳號列表頁也多兩欄顯示部門跟主管姓名，方便核對匯入結果對不對。

### `/me` 薪資補款紀錄的比對邏輯改了什麼

`services/salary_repayment_service.py` 的 `build_manager_lookup_from_accounts()`
取代了原本讀試算表「主管姓名」欄位的 `build_manager_lookup()`——現在是讀
`platform_accounts.list_accounts()`，把每個帳號的 `manager_usernames`
換算成姓名清單。試算表的「員工主管組織表」分頁還是有在讀，但**只剩一個
用途**：把補款紀錄裡「核准主管」欄位存的 LINE ID 換算回姓名顯示
（`build_line_id_name_lookup()`），跟主管權限判斷已經無關。

**還沒解決的殘留限制**：補款紀錄裡「申請人姓名」要對應到哪一個系統帳號，
還是得靠文字姓名比對（因為這筆資料是同仁在外部表單填的，這個系統管不到），
沒辦法完全排除——但風險比原本小很多，因為現在只剩「這一段」是文字比對，
「誰是誰的主管」這一段已經是系統帳號自己管理的可靠資料。

### 批次匯入既有主管關係

新增 `scripts/import_account_managers.py`：讀外部試算表「員工主管組織表」
分頁現有的主管關係，比對系統帳號姓名，批次幫既有帳號填好
`manager_usernames`，省去第一次要一筆一筆手動勾選的力氣。**只更新
`manager_usernames` 這一個欄位**，不會動到密碼、模組權限、部門等其他資料；
遇到同名同姓的帳號、或是主管姓名在系統裡找不到對應帳號，會整批跳過並且
清楚印出來，不會亂猜。之後要調整主管關係，直接在 `/accounts` 網頁上改
即可，不用再跑這支腳本第二次。

用法（在有 GCP 憑證、能連 Firestore 也能連 Google Sheets API 的環境，例如
Cloud Shell，位於 repo 根目錄執行）：

```bash
python -m scripts.import_account_managers
```

會先印出即將變更的內容（哪些帳號會被設定哪些主管、哪些姓名對不上、哪些
帳號同名同姓需要手動處理）再詢問是否要真的寫入，輸入 `yes` 才會執行。

## 未來規劃討論（尚未實作，先記錄下來）

使用者提出想把這個系統擴充成處理「派遣全流程」的系統，範圍很大（估計是
好幾個月的工作量），這裡先記錄討論出來的方向跟原則，還沒有開始實作
任何一項，之後真的要動工時回來看這一段、確認方向還是不是這樣。

**⏸️ 2026-09-09 更新：使用者決定這個擴充案先暫緩**，優先處理下面
「職缺維護表單整合」這件事（見本章節最後一節）。已經完成的公司主檔
（`/companies`）、廠商主檔（`/vendors`）維持現狀繼續可用，只是後續
（加保/退保事件紀錄、線上履歷、面試安排、黑名單）的工程先不推進。

### 想涵蓋的範圍

1. 線上履歷給求職者填寫——**使用者考慮連這塊都搬進這個系統自己做**，
   不再依賴外部履歷系統（`resume.tsaipei.com.tw`）。好處是可以順便解決
   現有待辦事項「履歷填完自動跳轉回 LINE」卡住的問題（目前卡住是因為
   履歷系統在外部工程師手上，兩邊都是自己的系統就不用再協調介接）；
   代價是要多做一個求職者不用登入就能填的公開表單頁面，工程量比串接
   外部系統大。
2. 從線上履歷資料直接安排面試。
3. 面試後可勾選是否錄取，錄取後續：上傳證件、**加保**（自動產出可以
   匯入勞健保局的檔案）。
4. 離職**退保**檔案。
5. 眾多客戶面試場次預先安排。
6. 廠商管理（配送部系統已經有廠商概念如蝦皮/UD/UC/順豐，可以延伸）。
7. 黑名單機制。
8.（討論過程中新增）**公司管理**：材霈不是只有一張派遣公司牌照，
   加保/退保檔案要能依照不同公司牌照分開產生，這件事影響範圍很廣
   （幾乎所有後續功能都要知道「這筆資料屬於哪家公司」），**建議整個
   擴充案裡最優先處理這塊，其他功能才有東西可以掛上去**。✅ **第一階段
   （公司主檔本身）已經完成，見下方「新增：公司管理」章節。**

### 加保／退保檔案格式（使用者已提供範例檔案，格式比預期簡單）

使用者提供了兩份勞保局匯入用的範例 `.xls` 檔案（純表格格式，不是特殊
二進位格式，欄位大部分能直接對應到系統裡人員資料的既有欄位）：

- **退保二合一**：異動別、勞工保險證號（8 碼）、勞工保險證號檢查碼
  （1 碼英文字）、被保險人外籍／姓名／身分證號／居留證統一證號／
  出生日期，共 8 欄。
- **2 合 1 加保範例**：異動別、格式別、勞工保險證號＋檢查碼、被保險人
  外籍／姓名／身分證號／出生日期、月實際工資、特殊身分別、勞基法特殊
  身分別、已領取社會保險給付種類、被保險人性別、提繳身分別、雇主
  提繳率(%)、個人自願提繳率(%)、勞退提繳日期，共 17 欄。

範例檔案裡「勞工保險證號」（例如 `01458989`）研判是跟著公司牌照走、
不是跟著個人——這也是「公司管理」要優先做的原因之一，這組證號會依
「這個人現在隸屬哪家公司」決定要填哪一組。

**還沒確認、之後要問清楚才能動工的問題**：
1. ✅ **已回答**：目前有 10 家派遣公司牌照，名稱／統編等見下方「新增：
   公司管理」章節，已經建進系統。
2. 每家公司是否各自有獨立的「勞工保險證號」「勞退提繳單位編號」？
   （公司主檔已經留好這兩個欄位，但實際值還沒有拿到，見下方章節。）
3. 範例檔案裡姓名／身分證號／出生日期欄位都是空的——是刻意留空只示範
   格式，還是這幾欄實際送出時本來就可以不填？需要知道哪些欄位必填、
   哪些選填才能正確產生檔案。
4. 「月實際工資」範例都填 1500，是要依個人實際薪資填入（1500 只是
   假資料）嗎？「雇主提繳率(%)」固定都是 6% 嗎，還是也可能不同？

### 核心資料原則：不覆蓋、事件式紀錄（法規要求同仁紀錄至少保留五年）

使用者明確要求：**同仁在職相關的每一筆異動（加保、退保、轉換公司…）
都是完全獨立、永久保留的一筆紀錄，沒有任何一筆會被覆蓋或取代**，不是
「維護一個目前狀態、旁邊搭一份異動歷史」這種模式。例如：

> 胡少凱 2026/9/1 在 A 公司加保、2026/9/2 從 A 公司退保（離職）、
> 2026/9/3 在 B 公司加保——這是三筆完全獨立的紀錄，不是「一筆資料被
> 改了兩次」。

畫面上如果要顯示「這個人現在的狀態」，做法是**查詢這個人最新一筆事件
紀錄來推算現況**（例如查到最新一筆是加保，代表現在在職、隸屬那家
公司），不是另外維護一個會被覆寫的「目前狀態」欄位。**沒被錄取或沒
報到的應徵者不受這條規則限制，可以視需要清除**。

之後如果這個擴充案真的要動工，資料模型（Firestore collection 設計）
要照這個原則設計，不能沿用配送部系統現有「一個人一份文件、改了就地
更新」的做法（例如 `repository.py` 現有的 `update_personnel_document()`
這類寫法，不能直接套用在加保/退保/公司歸屬這類需要保留五年紀錄的
資料上）。

## 新增：公司管理（`/companies`）——派遣全流程擴充案的第一階段

對應上方「未來規劃討論」第 8 項，先把「公司」這個基礎資料建起來，其他
功能（加保/退保、面試安排…）之後才有東西可以掛上去。**這次只做公司
主檔本身（新增/編輯/刪除/列表），還沒有任何功能真的引用它**。

**再次強調跟既有「廠商」概念的差別**：配送部系統既有的「廠商」（蝦皮、
UD、UC、順豐…）是配送人員實際去哪裡上班；這裡新增的「公司」是材霈自己
的派遣牌照、用哪張牌照幫這個人加保。同一個人可能同時「在蝦皮上班」但
「用材霈旗下某張牌照加保」，兩個概念不要混在一起，程式碼裡也是分開的
（`platform_companies.py` vs. `delivery/config.py` 的 `VENDOR_MAP`）。

### 新增的檔案

- `platform_db.py`：新增 `companies_ref()`，跟既有的 `users_ref()`
  一樣是跨模組共用的基礎資料存取，Firestore collection 名稱是
  `companies`。
- `platform_companies.py`：公司資料的 CRUD 邏輯（`list_companies()`／
  `get_company()`／`create_company()`／`update_company()`／
  `delete_company()`／`validate_company_fields()`），欄位定義集中在
  `FIELDS` 常數。**只驗證「簡稱／公司名稱／統一編號」這三個識別用欄位
  必填，其餘（電話、負責人、三個保險相關欄位）刻意不強制**，避免保險
  欄位還沒拿到就卡住公司本身無法先建起來。
- `company_routes.py` + `templates/companies_list.html` /
  `templates/company_form.html`：`/companies`，直接掛在根 app（跟
  `/accounts` 一樣），只有全平台管理員看得到，`base.html` 導覽列在
  「帳號權限管理」旁邊多一個「公司管理」連結。
- 公司的 Firestore 文件 ID 用「簡稱」（例如 `材霈`、`宸恩工廠`），**建立
  後不能修改**（表單裡是唯讀欄位）——避免簡稱被改掉後，變成建立了一份
  跟舊資料脫鉤的新文件，之後其他功能用簡稱關聯到公司時對不起來。
- `scripts/seed_companies.py`：一次性建立腳本，把使用者提供的 10 家
  公司名單（簡稱／公司名稱／英文名／負責人／電話／統一編號）建進
  Firestore。**已經存在的公司（簡稱重複）會直接跳過，不會覆蓋**，所以
  可以放心重複執行；勞工保險證號／勞退提繳單位編號這次沒有提供，先留
  空，之後直接在 `/companies` 網頁上補。

### 目前已知的公司清單（使用者提供）

| 簡稱 | 公司名稱 | 統一編號 |
|---|---|---|
| 材霈 | 材霈有限公司（Tsaipei Co., Ltd.） | 29168344 |
| 瑋政 | 瑋政有限公司 | 68138452 |
| 宸暐 | 宸暐企業有限公司 | 66504925 |
| 宸恩 | 宸恩實業有限公司 | 91005757 |
| 大廷 | 大廷交通貨運有限公司 | 89119852 |
| 鈞羽 | 鈞羽有限公司 | 94029297 |
| 祥舜 | 祥舜人力資源有限公司（SEAN SHUN HUMAN RESOURCES CO., LTD.） | 94250493 |
| 聿見 | 聿見國際有限公司 | 60616413 |
| 宸恩工廠 | 宸恩實業有限公司(工廠) | 91005757 |
| 宗舜 | 宗舜國際人力資源有限公司 | 00249456 |

「宸恩」跟「宸恩工廠」統一編號相同——研判是同一個法人底下兩個不同的
勞保加保單位（例如總公司跟工廠分開投保），照使用者提供的原始清單當成
兩筆獨立的公司資料處理，沒有自作主張合併，實際保險證號要等使用者補上
才能確認是否真的不同。

### 上線前要做的事

跑一次（在有 GCP 憑證、能連 Firestore 的環境，例如 Cloud Shell，位於
repo 根目錄）：

```bash
python -m scripts.seed_companies
```

會先印出即將建立的公司清單再詢問是否要真的寫入，輸入 `yes` 才會執行。
跑完之後到 `/companies` 核對資料，並視情況補上「勞工保險證號」「勞退
提繳單位編號」這兩個目前還空著的欄位。

### 下一步

公司主檔完成後，使用者接著確認：**廠商（蝦皮/UD/UC/順豐…）簽約哪家公司
是廠商本身固定的屬性**（同一個廠商底下的派遣員工都用同一家公司加保，
不是因人而異），所以接著做了「廠商管理」，見下一章節。「未來規劃討論」
章節列的還沒確認問題（3、4，關於加保/退保檔案的必填欄位跟工資/提繳率
填法）還是要問清楚，才能開始設計「加保／退保事件紀錄」這個下一層——
那一層要照「事件式、不覆蓋」的原則設計，會是整個擴充案裡技術上最需要
小心的部分。

## 新增：廠商管理（`/vendors`）——記錄廠商的簽約公司

對應上方確認的原則：廠商（蝦皮/UD/UC/順豐…）簽約哪家公司，決定底下的
派遣員工用哪家公司加保。這是一個**全新、獨立的廠商主檔**，用途只是記錄
「廠商 → 簽約公司」這個對應關係。

**刻意沒有動配送部系統既有的廠商清單**（`delivery/config.py` 的
`VENDORS`／`VENDOR_MAP`）：那份清單是配送部好幾個既有功能（報到文件
規則、意外事件回報白名單、CSV 匯入比對…）在跑，而且是在 Python 模組
「載入當下」就算好（`VENDOR_MAP = {v["code"]: v["name"] for v in VENDORS}`
這種寫法）——如果改成載入當下才去讀 Firestore，Firestore 連線一旦失敗
就會讓整個 Cloud Run 服務（不只配送部）啟動失敗，這正是幾天前才修過的
「main.py 啟動風險」同一類問題，不值得為了這次需求冒險去動它。所以現在
**同時存在兩份「廠商」資料，彼此沒有連動**：配送部舊的那份繼續管文件/
驗證規則，新的這份只管簽約公司——之後如果真的要合併成一份，需要另外
評估安全的做法，不是這次的範圍，這裡先把這個技術債明確記下來。

### 新增的檔案

- `platform_db.py`：新增 `vendors_ref()`，Firestore collection 叫
  `platform_vendors`（刻意不叫 `vendors`，跟配送部的概念保持視覺上的
  區隔）。
- `platform_vendors.py`：廠商資料 CRUD 邏輯，只驗證「代號／廠商名稱」
  必填，「簽約公司」（`company_id`，對應公司主檔的簡稱）刻意不強制。
- `vendors_routes.py`（**複數**，不是 `vendor_routes.py`，避免跟
  `delivery/routes/vendor_routes.py`——那支是「依廠商篩選人員清單」的
  頁面——搞混）＋ `templates/vendors_list.html` /
  `templates/vendor_form.html`：`/vendors`，直接掛在根 app，只有全平台
  管理員看得到，`base.html` 導覽列「公司管理」旁邊多一個「廠商管理」
  連結。編輯表單的「簽約公司」是下拉選單，選項來自 `/companies` 現有
  的公司清單。
- `scripts/seed_vendors.py`：把配送部既有的 4 個廠商名稱（蝦皮/UD/UC/
  順豐，代號沿用 `shopee`/`ud`/`uc`/`sf`）當起點建進新的廠商主檔，
  簽約公司先留空，之後直接在 `/vendors` 網頁上補。已存在的廠商會跳過、
  不會覆蓋，可重複執行。

### 上線前要做的事

跑一次（在有 GCP 憑證、能連 Firestore 的環境，例如 Cloud Shell，位於
repo 根目錄）：

```bash
python -m scripts.seed_vendors
```

跑完之後到 `/vendors` 幫每個廠商補上「簽約公司」（選 `/companies` 建好
的公司）。

## 新規劃：把「職缺維護表單」整合進這個 repo（進行中，取代派遣全流程優先度）

**2026-09-09**：使用者決定暫緩「派遣全流程」擴充案，優先把「職缺維護
表單」（獨立在 Netlify + Google Apps Script 的專案，見 CLAUDE.md，目前
沒有對應的 git repo）整個搬進這個系統自己做。這是一個獨立的專案，跟
上面的「派遣全流程」擴充案分開規劃，不要混在一起看。

### 為什麼要整合（評估過程的結論）

- **技術上可行**：這個 repo 已經有現成的基礎可以延伸——`services/notion_service.py`
  的 `append_unresolved_faq_to_notion()` 已經是寫入 Notion 的既有模式，
  「職缺維護」要新增/編輯職缺寫回 `NOTION_JOBS_DB_ID` 是延伸既有模式；
  官網讀的是 Notion 本身、不是 Netlify 網站，所以搬遷不影響官網跟沛沛的
  職缺讀取。
- **長期風險考量**：現在的職缺維護表單完全沒有版本控管（不在 git 裡，
  改壞了、想查歷史都沒有紀錄），身份系統又是另一套跟這裡的帳號系統
  各自為政、只是「剛好姓名對得起來」在運作——這跟 `/me` 薪資補款紀錄
  之前用文字姓名比對抓錯人的風險是同一類問題，只是現在整個職缺維護
  系統都建立在這個脆弱的基礎上。
- **釐清一個容易混淆的點**：這次要整合的「職缺維護表單」，跟之前招募
  機器人待辦事項提到「履歷填完自動跳轉回 LINE」卡住的那個外部系統
  （`resume.tsaipei.com.tw`，求職者填履歷用）是**兩個完全不同的外部
  系統**，整合職缺維護表單不會解決履歷跳轉那個問題。

### 目前已知職缺維護表單包含的功能

從 `/portal` 卡片說明（「職缺、薪資補款、專案合約維護，同步官網與招募
機器人」）跟既有程式碼推斷：

1. **職缺維護**：新增/編輯開放招募/停招的職缺狀態，寫進 Notion
   （`NOTION_JOBS_DB_ID`），官網跟沛沛都讀這個資料庫。
2. **薪資補款**（提交端）：同仁在這裡送出補款申請，寫進一份 Google
   Sheet「薪資補款紀錄」分頁——**這個 repo 已經有讀取端**
   （`services/salary_repayment_service.py`，`/me` 的薪資補款紀錄
   區塊），只是「送出申請」這個寫入動作還在職缺維護表單那邊，還沒
   搬過來。
3. **專案合約維護**：✅ **2026-09-09 已釐清**（使用者提供了對方系統
   完整原始碼）。同仁填寫廠商名稱、合作類別、簽約模式、約訪專員、
   拜訪主管，再上傳合約檔案（Word/PDF）；跟薪資補款一樣，送出前會先
   檢查「這個姓名有沒有完成 LINE 綁定」才放行。合約檔案存進 Google
   Drive（沿用既有資料夾權限，不額外對外公開），同時寫入一份
   Google Sheet「專案合約紀錄」分頁存檔，再把合約檔案當附件寄信給
   財會信箱。**跟薪資補款不同：這個功能沒有主管核准流程**，送出
   就直接發信，是三個子功能裡最簡單的一個。

### 身份驗證現況（要整合勢必要處理的部分）

職缺維護表單的身份驗證是「姓名 + 4 碼 PIN」，完整運作方式見
`job_portal_sso.py` 的說明：
- PIN 資料存在 Google Sheet「員工主管組織表」分頁（跟 `/me` 薪資補款
  讀的是同一份試算表），欄位存的是雜湊值（明文／無鹽 SHA-256／加鹽
  SHA-256 三種格式並存，同仁登入成功會被自動升級成加鹽格式，見上面
  「踩過的雷」章節 2026-09-09 補充）。
- 這份 Sheet 的資料**來源是同仁在 LINE 群組打「綁定+姓名+PIN碼」自動
  寫入**——這代表背後還有一個完全獨立、不在這個 repo 裡的 LINE 官方
  帳號在跑這段登記邏輯。**如果要完整整合，這支 LINE bot 的登記流程
  也要重建**，不是只搬網頁表單就好。這個綁定流程有防呆設計值得沿用：
  姓名重複時拒絕自動綁定（避免綁錯人）、用 `LockService` 防止同時綁定
  造成的競態問題、5 次登入失敗鎖定 15 分鐘防暴力破解 PIN。
- 「送出表單」的驗證不只是登入頁在用，薪資補款、專案合約維護送出時
  都會**再檢查一次**是否已完成 LINE 綁定，防止有人冒名送出、讓公司
  誤發信或誤核准——不是單純的「進得去網站」驗證。
- 這個平台（`platform_accounts.py`）用的是完全不同的一套帳號密碼系統，
  兩邊目前只是「姓名剛好對得上」在互通（`job_portal_sso.py` 的
  `find_identity_by_name()`）。

**關鍵問題已定案（2026-09-09）**：整合後身份驗證**維持姓名+PIN+LINE
綁定，不換成 `platform_accounts`**。

使用者確認：`platform_accounts` 的使用者跟職缺維護表單的使用者其實是
同一群人（不是我原本以為的兩個不同範圍的族群）。即使如此，這次決定
還是不把兩套身份合併，原因：
- 這組驗證已經深植在三個子功能「送出表單時驗證本人」的商業邏輯裡，
  不只是登入頁，換掉風險比單純換登入頁大很多。
- 同仁已經很熟悉「LINE 傳『綁定+姓名+PIN碼』」這套操作方式，換成帳號
  密碼要重新教育全體同仁，成本可能比技術上的好處還大。

之後如果想讓「同時有 `platform_accounts` 帳號、又是職缺系統核准主管」
的人使用體驗更一致（例如在 `platform_accounts` 帳號上加一個欄位記錄
「對應職缺系統裡的姓名」），是加分項、不是這次整合的必要條件，可以
之後再談。

### 建議的分階段順序

1. ~~請使用者介紹「專案合約維護」的實際內容~~ ✅ 已完成（見上）。
2. ~~決定身份驗證要不要換成 `platform_accounts`~~ ✅ 已定案：不換
   （見上）。
3. ~~`job_portal_sso.py` 的加鹽雜湊過時問題~~ ✅ 已修好（見上面「踩過
   的雷」章節 2026-09-09 補充），**部署上還差使用者設定
   `JOB_PIN_PEPPER` 環境變數這一步**，還沒做。
4. 動工順序建議：**薪資補款送出表單**（跟現有讀取端銜接，風險最低）→
   **職缺維護**（延伸現有 Notion 寫入模式）→ **專案合約維護**與
   **LINE 登記流程重建**（風險相對最高，建議放最後）。目前都還沒
   開始動工。

### 薪資補款送出表單：2026-09-09 定案「方案 A」，之後再升級「方案 B」

討論搬遷「薪資補款送出表單」的實作深度時，區分出兩種做法，**這次
定案先做方案 A，之後有需要再升級到方案 B（不是取消，是先後順序）**：

**方案 A（這次要做的）**：材霈平台這邊只換掉「同仁填表單、上傳照片」
這一段體驗（新頁面、要先登入平台才填得到），**表單送出之後的所有
邏輯完全不動**——重新計算金額、組 LINE Flex 卡片推播給主管、主管在
LINE 按核准/拒絕、寫入「薪資補款紀錄」試算表、拒絕就整筆刪除、核准
後寄 Email——這些全部原封不動轉手給現有的 GAS 程式處理，材霈平台
這邊不留任何一份照片的備份，**現有資料完全不用搬移**。

**方案 B（之後要升級的）**：核准推播/按鈕/寄信整段邏輯都用 Python
重寫、搬進這個 repo，才能真正解決「這套系統活在沒有版本控管的 GAS
裡」的根本風險。啟動方案 B 時要先處理好幾件已知會綁在一起的事：

- 需要使用者到 LINE Developers 後台，把現有那支**職缺系統專用**的
  LINE 官方帳號（不是這個 repo 管的招募機器人「沛沛」，使用者明確
  表示不想共用同一支帳號）的 Channel access token／Channel secret
  交給這邊，並把該帳號的 Webhook 網址從 GAS 改指向這個 repo。
- 因為 Webhook 網址只能設一個，**改指向這邊的同時，現在「綁定+姓名
  +PIN碼」LINE 登記流程也會跟著失效**，必須跟這次升級一起重建，不能
  分開做——這跟原本「動工順序建議」第 4 點就把 LINE 登記流程重建放
  在最後是同一件事，只是現在更明確：升級到方案 B 那一刻，兩件事要
  同時完成。
- **照片公開連結的安全疑慮，同時記錄在這裡**：GAS 現在的做法是每張
  上傳的補款佐證照片都會被明確設成「知道連結的人都能看」（不是資料夾
  層級設定，是每個檔案各自被程式主動設定，光改資料夾權限沒有用），
  原因是 LINE 的伺服器要能不經登入直接抓到圖片網址，才能在核准卡片
  裡顯示縮圖——這是 LINE Flex 卡片顯示圖片的硬性技術限制，任何要在
  LINE 對話裡顯示縮圖的做法都逃不掉「傳送當下網址不能要求登入」這件
  事。做方案 B 時，兩個折衷做法可以考慮：
  1. 改成「短效期簽名連結」（例如 10 分鐘後失效），LINE 推播當下抓
     得到圖、卡片照樣顯示縮圖，但連結很快就失效，不像現在永久有效、
     風險小很多，但嚴格說還是有一個很短暫的非登入可看窗口。
  2. 卡片上完全不放縮圖，只放文字連結，主管要點進去登入材霈平台才
     看得到照片——做到零公開窗口，但主管核准時的體驗會變、要多一個
     登入步驟。
  這件事需要使用者到時候再確認要選哪一種，不是這次方案 A 要處理的
  範圍。

**部署時機**：方案 A 是全新的頁面，跟現在同仁在用的 Netlify+GAS 系統
完全是兩條路，寫程式、部署上線的過程不會中斷或影響現有系統，**不需要
特別挑下班時間**；真正需要挑時間的是「宣布同仁改用新頁面」這個切換
動作（例如把 `/portal` 卡片改指向新頁面），到時候可以配合使用者指定
的時間再做。

### 薪資補款送出表單（方案 A）：2026-09-09 已實作，上線前還差一個環境變數

新增 `/me/salary-repayment/new`（登入即可用，跟 `/me` 其他部分一樣不用
另外的模組權限）：同仁填員工姓名、身分證、廠商/店家、申請日/付款日、
扣款月份、補請款月份、是否可請款、補款方式、備註（必填），加項/扣項
明細可以自己新增多筆「名稱＋金額」，還可以上傳一張佐證照片。送出時
`services/salary_repayment_submit_service.py` 把這些資料組成 GAS
`SUBMIT_SALARY` 端點看得懂的格式（照抄 `Project_Salary.gs` 的
`SalaryWorkflowService.processSalarySubmission()` 現有欄位，沒有新增
或修改任何對方看得懂的欄位名稱），原封不動 POST 過去。GAS 那邊回什麼
（成功、尚未完成 LINE 綁定、找不到核准主管…）就直接把它的中文訊息顯示
給同仁看，材霈平台這邊不重新判斷、不重新組訊息。

- `applicant_name`（送審人）**不開放同仁自己填**，強制用登入帳號的
  「姓名」——避免有人假冒別人的名義送審，跟 `/me` 其他地方「用帳號
  比對、不信任表單裡的姓名欄位」的既有原則一致。
- 送出成功後導回 `/me`（帶 `?submitted=補款單號`，畫面上會有一句提示），
  因為 GAS 是同步寫入試算表，重新整理 `/me` 通常馬上就能在下面的補款
  紀錄表格看到剛送出的這一筆。
- 照片**沒有**額外存一份到 GCS（方案 A 定案內容，見上一節），佐證照片
  上限 20MB，超過會在畫面上顯示錯誤、不會送出一半才失敗。

**上線前還差一步，需要使用者執行**：

1. 到職缺維護表單那個 Google Apps Script 專案的編輯器裡，右上角「部署」
   →「管理部署作業」，找到目前生效中的「網頁應用程式」，複製它的網址
   （注意不是 Apps Script 編輯器本身網址，是那個 `https://script.google.com/macros/s/.../exec`
   結尾的網址）。
2. 在 Cloud Run 設定環境變數 `JOB_PORTAL_GAS_WEBAPP_URL`：
   ```bash
   gcloud run services update recruitment-bot \
     --project=tsaipei-505807 \
     --region=asia-east1 \
     --update-env-vars="JOB_PORTAL_GAS_WEBAPP_URL=貼上剛才複製的網址"
   ```
3. 部署完成後（畫面顯示 `has been deployed and is serving 100 percent of traffic`），
   到材霈平台 `/me` 點「+ 送出新的薪資補款申請」，實際送一筆測試資料
   （可以先填一筆金額很小、備註寫「測試」的），確認：畫面顯示「申請已
   送出」、負責這筆申請的主管有收到 LINE 通知卡片、`/me` 下方的補款紀錄
   表格看得到這筆剛送出的資料——三個都確認過，才代表整個串接是通的。
   測試完這筆真的送出去的申請記得請主管在 LINE 上按「退回」清掉，不然
   會卡在「待審核」狀態。

沒設定 `JOB_PORTAL_GAS_WEBAPP_URL` 之前，同仁點進這個表單、填完送出，
畫面會直接顯示「尚未設定 JOB_PORTAL_GAS_WEBAPP_URL 環境變數」的錯誤
訊息，不會誤以為申請送出成功了。

### 下一步

薪資補款送出表單（方案 A）已經寫好，等使用者設定好
`JOB_PORTAL_GAS_WEBAPP_URL` 並實測通過，這一項就算完成。接下來排
「職缺維護」（延伸現有 Notion 寫入模式），已經有 `Project_Job.gs`
原始碼可以參考，開工前不用再跟使用者要資料。
