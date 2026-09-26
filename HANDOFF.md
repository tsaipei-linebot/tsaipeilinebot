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

- 🔴 **【最急迫，需要使用者/GCP權限者處理】延遲持續惡化，9/22 單日 p95 已飆到 747 秒**：使用者貼出 09/09～09/22 兩週的每日/週報告，核對後發現 p95 延遲從 34.6 秒一路惡化到 747 秒，過去兩週沒有一天低於 12 秒門檻。詳見下方「已完成」第 58 項的完整分析。治本需要調高 Vertex AI 配額，但**使用者已自行嘗試申請超過一小時、求助 Google 客服也沒解決**——Gemini 走的是 Dynamic Shared Quota，不一定能透過一般「配額」頁面直接申請調高，可能需要改走 Provisioned Throughput（購買保留吞吐量）或直接聯繫 Google Cloud 業務/客戶經理，這部分 Claude 沒有 GCP 存取權限，需要使用者或有權限的人繼續嘗試。**已從需求端做了緩解**（見第 58 項：蝦皮/理貨倉儲/製造作業員補上直達攔截，減少不必要的 Gemini 呼叫量），但這只是緩解、不是根治，建議持續觀察後續報告的 p95 數字有沒有改善。
- **【上線前流量/正確性盤點，PR 待跑，見下方「已完成」第 27 項】**：使用者希望盡快切換到正式頻道，請 Claude 對現有程式碼、對話流程、可承受流量做一次全面盤點。找到並已在程式碼裡修好 4 個問題（見第 27 項細節），另外有幾項**需要使用者自己去 GCP 動手做，Claude 這邊沒辦法代勞**：
  1. ✅ **Cloud Run `--min-instances=1`：已完成**。用 `gcloud run services describe recruitment-bot --region asia-east1 --format="value(spec.template.metadata.annotations)"` 確認過，`autoscaling.knative.dev/minScale=1` 已生效，不會再有容器冷啟動疊加 AI 決策時間、逼近 LINE 30 秒時限的風險。同時確認 `run.googleapis.com/cpu-throttling=false`（CPU 一律配置，先前就設定過的仍在生效）、`run.googleapis.com/startup-cpu-boost=true`（額外加速容器啟動）。
  2. ✅ **正式上線前重新壓測：已完成，結果健康**。合併＋部署上方第 27 項的修正後，用 `scripts/load_test.py --concurrency 30 --total 100 --distinct-users 20` 實測：100 筆全部成功（無失敗），wall time p50=6.40s／p95=12.32s／p99=13.50s／max=13.50s，伺服器端純處理 p99=11.97s／max=11.97s——安全落在 LINE 30 秒 reply token 上限內（超過 2 倍餘裕）。**跟先前併發 15 的舊紀錄（見上方「Vertex AI 回應延遲」待辦事項）幾乎持平**（舊：wall p99/max=13.31s／伺服器 p99/max=10.78s），代表併發數翻倍後，`min-instances=1`＋CPU 一律配置＋這次修的 Firestore 並發問題，撐住了兩倍流量沒有明顯劣化。p50 落在 5-6 秒區間（一半以上請求要等 5 秒以上才有回覆），不是這次測試才有的新現象，是 Vertex AI 中高併發下既有的排隊現象；如果正式流量長時間維持併發 20-30 這個量級，可以考慮把「去 Vertex AI 主控台申請調高配額」這項低優先待辦往前提。
     - ✅ 壓測已經順便驗證了新加的監控結構化 log（`[AI_DECISION_LOG]`）在真正部署環境下有沒有正常印出來，如果要進一步確認可以去 Cloud Logging 篩選這個關鍵字看一眼。
     - ✅ **`LOAD_TEST_SECRET` 已清掉**（2026-09-25 使用者確認），壓測用的內部端點不再有有效密鑰；之後要再壓測，要先重新設一組、測完再清掉。
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
- ✅ **【已完成並實測確認，1-4 項設定都已生效】監控與告警機制＋FAQ 週報**：把原本分開討論的「監控告警」跟「FAQ 週報／職缺關鍵字缺口」合併成同一支每日／每週排程端點實作，見下方「已完成」第 26 項的完整說明。使用者已於 2026-09-22 確認：`DAILY_REPORT_LINE_TARGET_ID` 已設定，每天 21:00 都有正常收到週報，代表下面 1-4 項設定（服務帳戶讀 log 權限、LINE 群組、Cloud Scheduler 排程、`DAILY_REPORT_ENABLED=true`）全部已生效，這幾項待辦解除。
  1. ✅ **服務帳戶要能讀 Cloud Logging**：`run_daily_report()` 是直接查 Cloud Logging（`google-cloud-logging`），不是走 Cloud Monitoring 記錄型指標——這點跟原始設計草稿（「結構化 log → Cloud Monitoring 指標」）不同，是實作時的簡化：直接查 log 一樣能算出 p95／保底訊息次數，不用多一道設定記錄型指標的手續。目前服務帳戶靠「編輯者」角色能讀 log，但下方安全性待辦要拿掉編輯者時，記得要另外加「記錄檢視者」（`roles/logging.viewer`），不然這個報告會讀不到 log。
  2. ✅ **建 LINE 群組＋把沛沛加進去**，取得群組 ID 後設進 `DAILY_REPORT_LINE_TARGET_ID`：已完成，每天 21:00 正常收到報告，確認推播正常運作。
  3. ✅ **設一個隨機字串當 `DAILY_REPORT_TRIGGER_SECRET`**，並在 GCP Cloud Scheduler 建一個每天一次的排程 job，用 HTTP POST 呼叫 Cloud Run 的 `/internal/daily-report/run`，帶上 header `X-Daily-Report-Secret: <同一組密鑰>`（跟每週工廠監控端點的做法完全一樣）：已完成，每天準時 21:00 觸發。
  4. ✅ **確定要正式生效時，設定環境變數 `DAILY_REPORT_ENABLED=true`**（預設關閉，即使 Cloud Scheduler 已經照排程在打這支端點，沒開這個總開關只會回傳「尚未啟用」、不會真的去讀 log／推播），比照「日夜接力」`STAFFED_HOURS_GUARD_ENABLED` 的做法：已完成。
  5. **「Claude 對話串」這個通知管道目前沒有做**：跟使用者討論後的結論是，正式生效的通知只走 LINE 群組（見上面第 2 點），比較不會因為某個 Claude Code session／排程沒有活著而漏發，這點是實作時額外的判斷，跟原始定案設計（雙管道都發）不同，請知悉。
  6. **（仍待設定，非急迫）原生 Cloud Monitoring alert（完全掛掉時 5 分鐘內就通知，不用等每日報告）尚未設定**：這是每日報告以外，另一層獨立的緊急備援，設定方式：
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

- ✅ **【已完成，使用者確認】履歷點擊紀錄：記錄誰點了職缺卡片的「填寫線上履歷」按鈕**：詳見下方「已完成」第 47 項的完整說明。使用者已於 2026-09-22 確認這項也做好了，以下設定全部生效：
  1. ✅ Notion「履歷點擊紀錄」資料庫已建立、欄位也已確認正確（求職者暱稱/LINE User ID/應徵職缺/產業類別皆為對的類型，點擊時間已改成日期類型）。
  2. ✅ 確認這個資料庫已分享給機器人用的 Notion 整合。
  3. ✅ 到 Cloud Run 設定 `NOTION_RESUME_CLICK_LOG_DB_ID`（該資料庫 ID）與 `SERVICE_BASE_URL`（`https://recruitment-bot-412901869672.asia-east1.run.app`）這兩個環境變數，功能已正式生效。

**注意：面試預約功能（原本在同一個分支上開發）這次刻意沒有一起合併**——使用者明確表示「面試預約請先不要加進去」，程式碼仍然只留在 `claude/tsaipei-linebot-handoff-7jdsks` 分支上，main 這邊完全沒有相關程式碼，之後如果要上線這個功能，需要另外再合併一次。

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
33. **試營運當晚實測發現：職缺地址/文案裡的路名跟行政區同名，會被誤判成該職缺位於該行政區**：使用者切換到正式頻道試營運後，實際測試發現「蝦皮門市」某筆職缺的「行政區」欄位沒有勾選八德，但求職者問「八德有沒有缺額」時沛沛卻回答有。追查發現：地區判斷（`_score_job_for_ai()` 的地區加分、「都給我看看」全部瀏覽、「精準工種直達攔截」的外送/門市/momo 三個分支）全部都是拿 `_search_text` 做字串比對——這個欄位是把「工作內容(對外)」「排版工作說明」「精華亮點」等自由文字全部串在一起產生的，只要職缺地址或行銷文案剛好提到某個地名（最典型的例子就是台北市「八德路」這條知名路名，跟桃園市「八德區」同名但完全是兩個地方），就會被誤判成這個職缺真的位於該行政區，不管同仁在 Notion 裡實際勾選的行政區是什麼。
    - **修正方式**：`services/notion_service.py` 的 `fetch_jobs_data()` 新增專屬的 `_location_search_text` 欄位，只由「縣市」「行政區」這兩個結構化欄位組成，不包含任何自由文字。`services/matcher_service.py` 的 `_score_job_for_ai()`、`handlers/message_handler.py` 裡所有跟地區比對相關的地方（全部瀏覽、外送/門市/momo 直達攔截，共 5 處）全部改成比對 `_location_search_text`，不再使用會混入自由文字的 `_search_text`。**品牌（momo/富邦/富昇）比對維持用 `_search_text`，這次沒有一併調整**——因為使用者這次回報的是地區誤判，範圍沒有連帶擴大到品牌比對。
    - **新增測試**：`tests/test_notion_service.py` 新增 `LocationSearchTextTests`（驗證 `_location_search_text` 不包含自由文字裡的地名，`_search_text` 才會包含）；`tests/test_matcher_service.py` 新增 `LocationScoringUsesStructuredFieldTests`（2 個，驗證自由文字提到地名不會加分、結構化欄位真的命中時加分仍正常）；`tests/test_message_handler.py` 新增 `StoreIntentLocationMatchTests`（重現使用者實測到的「蝦皮門市＋八德」情境，確認不會誤判成直接命中）。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 484 個測試，OK。
    - ⚠️ **部署後同一晚實測，發現同一個症狀還有第二層原因，見下方第 34 項**：第 33 項只修好「候選職缺篩選」這一關，AI 自己在推理階段的過度類推是獨立的第二個問題，光修第 33 項並不夠。
34. **同一晚接續發現：AI 自己會憑常識推論「同縣市其他行政區應該也算」，不是候選職缺篩選錯誤**：第 33 項部署生效（GitHub Actions 確認部署成功）之後，使用者當晚馬上重新實測，沛沛仍然回覆「蝦皮門市在桃園區是有職缺的喔，八德區也涵蓋在內！」。追查後確認這是完全不同層次的問題：候選職缺清單送給 AI 判斷時，「地點:」欄位其實老實顯示的是「桃園市」這種縣市層級、或列出蘆竹/龜山等其他行政區（不含八德）——資料本身是對的，是 **AI 自己在推理階段憑常識類推「八德行政上也屬於桃園市，應該算涵蓋在內」**，原本的提示詞（prompt）沒有明確禁止這種類推。
    - **修正方式**：在 `handlers/message_handler.py` 的 `ai_prompt` 新增一條規則（原本的規則 4「單一焦點追問」往後遞補為規則 5）：明確告知 AI「地點:」欄位是同仁在系統裡實際勾選的正確行政區，求職者問到清單裡沒有明確列出的行政區時，一律視為「此條件無完全相符職缺」，不能因為同縣市有其他行政區的職缺、或地點欄位只寫到縣市層級，就自行推論或宣稱該行政區也涵蓋在內，並附上八德/桃園市的具體反例讓 AI 更容易照做。
    - **這次的教訓**：候選職缺篩選（資料層）跟 AI 推理（語言模型層）是兩個獨立的環節，同一個表面症狀（「八德誤判有缺額」）可能同時有兩層原因，只修好其中一層不代表問題全部解決——之後遇到類似「明明資料/篩選邏輯是對的，AI 回覆卻還是不對」的情況，要優先檢查提示詞有沒有給 AI 足夠明確、禁止過度類推的指示。
    - **新增測試**：`tests/test_message_handler.py` 新增 `test_ai_prompt_forbids_inferring_uncovered_districts`（驗證提示詞裡確實包含這條新規則的關鍵字，不是只憑印象檢查過就算了）。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 485 個測試，OK。
35. **釐清：「行政區」欄位存成「桃園市八德區」這種帶縣市前綴的完整寫法，會不會影響判斷？**——使用者提到同仁習慣在「行政區」欄位直接加上縣市前綴（例如寫成「桃園市八德區」而不是單純「八德區」），原因是有些行政區名稱會跨縣市重複（例如中山區台北市、基隆市都有）。實測確認：**地區比對邏輯完全不受影響**（比對時只看子字串有沒有出現，不管前面有沒有多帶縣市名稱），但這種寫法會讓組出來的顯示文字重複縣市名稱兩次，變成「桃園市（桃園市八德區、桃園市蘆竹區）」這種累贅呈現，同時也會出現在送給 AI 判斷用的「地點:」欄位裡。
    - **修正方式**：`services/flex_service.py` 的 `format_clean_location()` 新增 `_strip_county_prefix()`，在組出顯示文字之前，把每個行政區開頭重複的縣市名稱去掉（同時處理「台/臺」全半形不一致的情況，例如縣市欄位寫「台北市」、行政區欄位卻寫「臺北市中山區」）。修正後「桃園市八德區,桃園市蘆竹區」會正確顯示成「桃園市（八德區、蘆竹區）」；行政區欄位本來就沒有帶縣市前綴的既有資料不受影響，行為維持原樣。
    - **新增測試**：`tests/test_flex_service.py` 新增 `FormatCleanLocationCountyPrefixTests`（4 個，涵蓋多筆行政區去重複前綴、台/臺全半形混用、原本無前綴不受影響、指定 `target_location` 時也套用去前綴後的文字）。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 489 個測試，OK。
36. **接續發現：AI 提示詞的「地點:」欄位，對「全台/多縣市門市自選」這種涵蓋 5 個以上行政區的職缺，沒有反映使用者實際問的地區**：使用者實測「板橋有缺嗎」，沛沛的文字回覆說「板橋區暫無明確列出的職缺」，但同一則訊息附上的職缺卡片卻明明白白顯示「地點：板橋區」——文字跟卡片自相矛盾。追查發現：`handlers/message_handler.py` 組給 AI 判斷用的「地點:」欄位，呼叫 `format_clean_location(j)` 時**沒有帶入 `current_location`**（使用者這輪問的地區），只有組卡片顯示文字時才有帶入。這件事平常不會有影響，但對「蝦皮店到店」這類涵蓋超過 5 個行政區（`format_clean_location()` 的第三種聚合級距）的職缺，沒帶目標地區時只會回傳籠統的「各區門市據點（自選區域）」，AI 因此看不出「板橋」有沒有明確包含在候選職缺裡；卡片文字則正確帶入 `target_location`，能精準比對出「板橋區」確實在清單裡、直接顯示出來。**這個疏漏原本就存在，但因為第 34 項剛加的新規則要求 AI 更嚴格依據「地點:」欄位白紙黑字的內容判斷，才把這個疏漏放大成看得到的矛盾**——沒有第 34 項那條規則時，AI 可能還會憑常識矇對，現在被明確要求「沒寫就不能算」，疏漏反而更容易暴露出來。
    - **修正方式**：`handlers/message_handler.py` 組 `job_index_text` 時，`format_clean_location(j)` 改成 `format_clean_location(j, current_location)`，讓 AI 看到的地點文字跟卡片顯示的地點文字用同一份邏輯、同一個目標地區算出來，兩邊資訊一致。
    - **這次的教訓**：跟第 34 項一樣是「資料層是對的（職缺真的涵蓋板橋），顯示/傳遞層漏了一個參數」的問題，不是候選職缺篩選邏輯錯誤。這類「AI 文字回覆」跟「同一則訊息附的卡片」內容互相矛盾的情況，往後可以當作一個快速線索：優先檢查是不是這兩個地方各自呼叫了同一個格式化函式、但帶的參數不一致。
    - **新增測試**：`tests/test_message_handler.py` 新增 `test_ai_prompt_location_reflects_specific_district_for_broad_coverage_job`（重現使用者實測到的「蝦皮店到店＋板橋」情境，驗證提示詞裡的「地點:」有正確顯示板橋、不是籠統的「自選區域」）。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 490 個測試，OK。
37. **接續發現：跟第 33 項同一種 bug 類型，但這次是「職務類別」誤判，不是地區誤判**：使用者實測「有蝦皮門市嗎」（沒帶地區），沛沛推薦的第一張職缺卡片是完全無關的「【雙北基宜】設備人員」，第二張才是真正的蝦皮門市職缺，使用者回報「感覺邏輯壞掉了」。追查後請使用者確認這筆「設備人員」職缺的實際 Notion 欄位值：「系統廠商名稱」是「蝦皮內勤」（真的是蝦皮旗下的職缺，廠商比對本來就會過）、「職務類別」是「設備人員」（跟門市完全無關）。根本原因：`services/matcher_service.py` 的 `job_matches_category_filter()` 在嚴格比對（只看職缺名稱/職務類別）沒命中、需要退回寬鬆比對時，寬鬆比對用的 `_job_extended_search_text()` 把「工作內容(對外)」這種行銷用自由文字也混進去一起比對——這筆設備人員職缺的工作內容(對外) 寫著「負責各區門市據點（共60區，門市自選）設備維護保養」，這句話是在講「到職地點遍布全台各門市」，不是在講「職務類別是門市」，但寬鬆比對只看字串裡有沒有出現「門市」兩個字，就把它誤判成門市類別職缺。這跟第 33 項「地址路名跟行政區同名被誤判」是完全同一種 bug 模式：寬鬆比對信任了自由文字說明欄位，而不是只信任結構化欄位。
    - **修正方式**：新增 `_job_extended_category_text()`（`services/matcher_service.py`），寬鬆比對「職務類別」專用，只由「職缺名稱」「職缺名稱(對外)」「職務類別」「行業別」這四個結構化欄位組成，**刻意不放「工作內容(對外)」**。`job_matches_category_filter()` 的寬鬆比對階段，職務類別檢查改用這個新欄位；「系統廠商名稱」比對（品牌/廠商比對）維持用原本的 `_job_extended_search_text()`（保留系統廠商名稱等欄位）——**這次只調整職務類別的寬鬆比對，沒有連帶調整廠商比對**，因為使用者這次回報的是職務類別誤判，範圍沒有連帶擴大。
    - **這次的教訓**：跟第 33 項是同一種 bug 的兩個不同面向（一個是地區、一個是職務類別），但根本原因完全一樣——「候選職缺篩選只能信任結構化欄位，不能信任行銷用自由文字說明欄位」這條原則要套用到每一種比對維度（地區／職務類別／未來如果有其他維度），不能修好一種就假設全部都修好了。之後如果又出現「推薦到看起來完全不相關的職缺」，優先檢查是不是又有其他比對維度（例如休假方式/薪資字串）也在偷偷用自由文字欄位判斷。
    - **新增測試**：`tests/test_matcher_service.py` 新增 `CategoryRelaxedMatchingIgnoresFreeTextTests`（3 個：重現使用者實測到的「蝦皮門市查詢誤判到設備人員職缺」情境並確認被排除、確認只靠「行業別」這種結構化欄位命中的門市職缺寬鬆比對仍正常放行、`filter_jobs_by_category_tiered()` 整體行為確認設備人員職缺不會出現在結果清單）。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 493 個測試，OK。
38. **接續發現：分兩句話問（先問廠商+類別，再單獨追問地區）時，沛沛答非所問，且同一份資料兩條路徑會給出矛盾答案**：使用者測試「蝦皮門市有嗎」得到正確的門市職缺卡片後，接著只問「八德有缺嗎」（這句話本身沒有再提到門市/蝦皮），沛沛卻推薦了「全台平價石頭火鍋」這種完全不相關類別的職缺；同一天測試中也發現「蝦皮門市 八德有缺嗎？」單句合併問時一度回答「有」，之後使用者質疑「其他人跟我說八德沒缺」時，沛沛又改口說「沒有明確列出的職缺」——同一份資料被兩種機制矛盾地判讀。追查後（並請使用者實際打開 Notion 核對「蝦皮店到店門市夥伴」這筆職缺的「行政區」欄位，確認裡面完全沒有「八德區」這個項目，只有相似的「八里區」）確認：
    - **根本原因一（分兩句話問答非所問）**：「精準工種直達攔截」（`is_delivery_intent`/`is_store_intent`/`is_momo_intent`）只看「這句話本身」有沒有出現門市/外送/momo等關鍵字，不會延續前一輪已經鎖定的類別；同時「廠商」（brand）這個槽位原本設計成「這句話沒再提到廠商就清空」（跟地區/類別「持續生效直到明確取消」的設計不一樣）——兩個問題疊加，導致單純追問地區的句子，既沒有走進精準攔截、廠商條件也早就不見了，只能落到 AI 決策，AI 只把類別/廠商當成排序加分（不是硬性篩選），才會混進不相關類別的職缺。
    - **根本原因二（同一份資料兩條路徑矛盾）**：`format_clean_location()` 依「行政區」逐一比對出「地點:」欄位是完全正確、確定性的（Notion 沒有八德區，就不會宣稱有）；但落到 AI 決策路徑時，Gemini 本身不是每次都 100% 精準遵守提示詞裡「地點:欄位沒寫就不能算」的規則，會有極少數情況仍然憑語感給出錯誤答案（這是已知的 LLM 殘餘風險，第 34 項已經加過明確禁止類推的規則，但無法保證 100% 遵守）。**確認過使用者的資料本身完全正確**：「蝦皮店到店門市夥伴」這筆職缺的行政區清單裡真的沒有八德區，沛沛後來回覆「沒有明確列出的職缺」才是正確答案，不是騙人。
    - **修正方式**：把「精準工種直達攔截」延伸到「單純追問地區」的情境——`handlers/message_handler.py` 新增 `is_bare_location_followup` 判斷：這句話有抓到明確地名、且沒有夾雜其他新的類別/廠商關鍵字時，才視為延續前一輪鎖定的類別（門市/外送）或廠商（momo），改用之前鎖定的條件走進精準攔截（確定性比對，不會出現 AI 那種偶發不一致）；刻意要求「有抓到地名」才觸發，避免把「發薪日是什麼時候」這種抓不到地名的 FAQ 類問題也一起誤攔進來。同時把「廠商」槽位的行為改成比照地區/類別：沿用到使用者明確換掉、或明確表示「不限廠商/不限品牌/不限公司/其他廠商/別的廠商/換一家/不挑廠商」為止，不再是「這句話沒提到就清空」。「精準工種直達攔截」門市分支原本用「蝦皮門市」這種字面上剛好連在一起的寫法特例判斷廠商，也一併改成直接用（延續後的）`detected_brand`，更通用也更正確。
    - **這次的教訓**：跟第 34/36 項一樣，「同一份資料，不同路徑給出矛盾答案」時，優先假設其中一條路徑（通常是仰賴 AI 推理的那條）不夠可靠，而不是資料本身有問題——這次特地請使用者直接核對 Notion 原始欄位值，確認資料是對的，才能放心把「單純追問地區」這種情境導去更可靠的確定性比對路徑，而不是繼續依賴 AI 每次都能正確遵守提示詞規則。
    - **新增測試**：`tests/test_message_handler.py` 新增 `BareLocationFollowupContinuesContextTests`（5 個：延續前一輪門市+蝦皮追問地區成功攔截、沒有前情脈絡時的地區追問維持原本落到 AI 決策、FAQ 類問題不會被誤攔、廠商槽位這輪沒提到時沿用不清空、使用者明確表示不限廠商時仍能真正清空）。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 498 個測試，OK。
39. **第 38 項合併部署當天馬上發現的副作用：momo 分支的既有退讓機制，被新的「追問地區延續脈絡」誤觸發**：使用者合併部署後實測，發現問完「有momo的職缺嗎」之後，接著單獨問「台南耶？」「台南有嗎？」（這兩句話本身都沒有再提到 momo），沛沛卻回覆「有的！沛沛為您找到符合條件的推薦職缺囉」，還附上一筆「地點：桃園市」的職缺卡片——同一筆桃園職缺，不管問「台南耶」「台南有嗎」還是「有momo的職缺嗎」都會被推薦出來，答非所問。追查後確認：`is_momo_intent` 分支原本就有一段既有的退讓邏輯——`direct_matches = loc_momo if loc_momo else momo_jobs`（這個地區找不到 momo 職缺時，退讓成顯示「全部」momo 職缺，讓使用者至少看看其他地區有什麼）。這段退讓邏輯原本只會在使用者這句話「本身真的有講 momo」時才會被觸發，屬於合理的使用者體驗（畢竟使用者自己提到 momo，多少能接受「這個地區沒有，但其他地區有」這種答案）；但第 38 項新增的「單純問地區時延續前一輪鎖定的類別/廠商」功能上線後，單純問「台南有嗎」這種完全沒提到 momo 的句子，也會因為延續了前一輪的 momo 脈絡而觸發這個分支，連帶也觸發了這段退讓邏輯——變成使用者根本沒提到 momo，卻收到一個跟他問的地區完全無關的職缺，還宣稱「找到符合條件的推薦職缺」。
    - **修正方式（第一版，已被下面的最終版取代）**：一開始只限縮生效條件——新增 `_is_explicit_intent_this_turn` 記住「這句話本身」是否真的有提到門市/外送/momo，只有使用者這句話真的講了 momo 時才允許退讓顯示全部 momo 職缺；延續前一輪脈絡但這句話單純問地區時，就不退讓、直接落到 AI 決策。
    - **請使用者評估後，決定乾脆整個拿掉這段退讓（最終版）**：跟使用者一起盤點過系統裡所有「找不到精準符合就退讓顯示更寬結果」的機制後（momo 分支的地區退讓、門市/外送分支共用的類別/廠商嚴格→寬鬆退讓、「都給我看看」分支的層層退讓、AI 決策層本身的退讓推薦規則），使用者確認：只有 momo 這段「地區找不到就乾脆不管地區」的退讓需要拿掉，其餘都保留。於是把第一版的限縮邏輯簡化成直接刪除整段退讓：不管是延續前一輪脈絡、還是使用者這句話本身真的講了 momo，地區沒有精準命中就是沒有直接命中，一律 `direct_matches = []`、落到 AI 決策，跟 delivery/store 分支的行為完全一致，不再有任何例外；沒有指定地區時（單純問「有momo的職缺嗎」）不算「找不到」，仍然正常顯示全部 momo 職缺，不受影響。`_is_explicit_intent_this_turn` 這個過渡用的變數也一併移除。
    - **這次的教訓**：新增「延續前一輪脈絡」這種功能時，不能只看「這個分支要不要觸發」，還要檢查分支內部有沒有「找不到精準符合就退讓成更寬鬆結果」這類既有邏輯——退讓邏輯通常是為了「使用者自己提到的明確條件」設計的合理退讓，一旦觸發來源換成「系統自己延續的隱性脈絡」，同一段退讓邏輯就可能從「貼心」變成「誤導」。盤點清楚系統裡所有類似的退讓機制、逐一跟使用者確認要不要保留，比只針對單一觸發條件打補丁更根本；這次也證實 momo 的地區退讓拿掉後不用擔心「使用者從此看不到退讓建議」——AI 決策層本身就有更誠實（會講清楚是次要吻合）的退讓機制可以接手。之後如果還要讓其他分支支援延續前一輪脈絡，都要重新檢查一次分支內部有沒有類似的退讓/降級邏輯。
    - **新增測試**：`tests/test_message_handler.py` 新增 `MomoIntentLocationFallbackRemovedTests`（3 個：延續前一輪 momo 脈絡且地區沒有精準命中時落到 AI 決策；使用者這句話本身真的講了 momo、地區沒有精準命中時也落到 AI 決策，不再退讓；完全沒指定地區時仍正常顯示全部 momo 職缺，不受影響）。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 501 個測試，OK。
40. **接續發現：直接攔截找不到精準符合、落到 AI 決策後，AI 對同一件事的判斷會因為「這句話本身有沒有重複提到條件」而不一致**：第 39 項部署後，使用者實測「有蝦皮門市嗎」→「八德有缺人嗎」，沛沛把八德所有類別的職缺（7 筆）都當成「符合條件」推薦出來；但同一時間測「蝦皮門市 八德有缺嗎」這種當下就完整重複條件的問法，卻能正確回答「暫無蝦皮門市職缺」。追查後確認：這兩句話都會先經過「精準工種直達攔截」，因為八德沒有蝦皮門市職缺，兩句話的攔截結果都是空（`direct_matches=[]`），所以都會落到 AI 決策——差別出在 AI 決策這一層本身：提示詞原本完全沒有明講「求職者目前鎖定的類別/廠商是什麼」，AI 只能自己從【過去對話】的文字內容去猜測還有沒有效——這句話當下有沒有重複提到「門市」「蝦皮」，會讓 AI 判斷得不一致，沒重複提到時容易誤判成「不限類別/廠商」，把不相關的職缺也一併推薦出來。
    - **修正方式**：`handlers/message_handler.py` 的 `_compute_ai_decision_messages()` 新增【求職者目前鎖定的條件】區塊，把目前的地區/工作類型/廠商槽位明講進提示詞（例如「工作類型=門市、廠商=蝦皮」），不再只讓 AI 自己從對話歷史猜；同時在規則區塊新增規則 5，明講「這些條件即使這句話本身沒有重複提到，也要當成仍然套用」，並附上八德/門市/蝦皮的具體範例。
    - **這次的教訓**：跟第 34 項是同一種「資料/篩選層是對的，AI 推理層還是可能出錯」的情況，但這次更進一步定位到根因——AI 推理層之所以不穩定，是因為提示詞本身沒有把「持續生效的已知條件」講清楚，讓 AI 得自己從對話歷史猜「這個條件現在還算不算數」，猜測難免不穩定。之後任何「延續前一輪脈絡」的功能，除了在直接攔截這種確定性比對的分支裡做好（第 38 項），也要記得同步讓 AI 決策這條路徑明確拿到同一份已知條件，不能讓 AI 自己從歷史文字猜。
    - **使用者確認前追問**：這個修正會不會讓「已鎖定條件但沒明講取消」的情況變得太嚴格？回答：會，但這是修對、不是新增風險——之前偶爾出現的「答得比較寬鬆」其實是 AI 沒被告知條件還算數才誤判，之後會變成「答得比較精準、也比較常誠實說沒有」。使用者接著追問：那如果求職者這句話明確改問其他廠商/工作，鎖定條件會不會正確更新？——確認：**會**，槽位覆蓋是既有邏輯（這句話裡偵測到新廠商/新類別就直接覆蓋舊值），這次新增的區塊只是把覆蓋後的最新值明講給 AI，不影響覆蓋本身；已補上端對端測試驗證這一點。
    - **新增測試**：`tests/test_message_handler.py` 新增 `test_ai_prompt_explicitly_states_locked_category_and_brand`（驗證提示詞裡有明講已鎖定的工作類型/廠商）、`test_ai_prompt_shows_no_locked_conditions_when_slots_empty`（驗證沒有任何鎖定條件時顯示對應的空狀態文字）、`test_new_brand_mentioned_this_turn_overrides_locked_condition_in_ai_prompt`（端對端驗證：這句話明確改問其他廠商時，送給 AI 的鎖定條件區塊會正確顯示新廠商，不會卡在舊廠商上）。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 504 個測試，OK。
41. **使用者要求：盡量擴充「求職者想重新詢問/不限條件」能被辨識到的說法**：討論過「鎖定條件沒明講取消就會一直沿用」的取捨後，使用者希望盡量多收錄類似「都給我看看」的說法，讓程式更容易判斷求職者是想重新開始/放寬條件，不要卡在少數固定詞組上。盤點後發現一個既有的不對稱：地區、廠商都各自有一份「明確表示不限」的關鍵字清單可以主動清空槽位，但**類別完全沒有**——類別只能靠「否定掉目前鎖定的那個類別」清空（例如「除了外送」），使用者說「不限類型」「什麼工作都可以」這種泛用表態，原本完全沒辦法清空類別。
    - **修正方式**：`handlers/message_handler.py` ①擴充 `full_reset_keywords`／`single_dimension_keywords`／`show_all_keywords` 三份既有清單的詞彙覆蓋範圍（更多自然說法，例如「全部重來」「調整條件」「有哪些職缺」）；②新增共用的 `generic_broaden_keywords`（「都可以」「隨便」「都好」「無所謂」「沒差」「什麼都行」等泛用表態），同時餵給地區/類別/廠商三處的「明確表示不限」判斷——講一次泛用表態，三個維度會一起解鎖，不用逐一分開講「不限地區」「不限類型」「不限廠商」；③新增類別專屬的「不限類型」「不限工作類型」「什麼工作都可以」等清單，補上跟地區/廠商一致的清空機制。**刻意不把單獨的「不限」兩個字放進共用清單**——那樣會讓「不限地區」「不限廠商」這種只針對單一維度的說法，因為子字串比對而誤觸發連帶清掉其他沒被提到的維度，共用清單只收錄語意上真的「三個維度通用」的完整詞組。
    - **這次的教訓**：擴充關鍵字清單時，要分清楚「這個詞是不是真的對所有維度都通用」，不能因為想要清單覆蓋率高，就把某個維度專屬的詞組簡化成更短的子字串放進共用清單，否則會有跨維度誤觸發的風險（例如「不限」這種詞根，一旦放進共用清單，任何「不限X」的說法都會變成「連 X 以外的東西也一起清空」）。
    - **新增測試**：`tests/test_message_handler.py` 新增 `ExpandedBroadenPhraseTests`（3 個：類別能透過新的類別專屬泛用詞清空、共用泛用詞「都可以」一次清空地區/類別/廠商三個維度、廠商專屬的「不限廠商」只清空廠商、不會連帶清掉沒被提到的地區/類別）。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 507 個測試，OK。
42. **接續發現：「門市」類別誤判的第三種成因——內部命名慣例外洩**：使用者依照測試清單實測，回報明確問「蝦皮門市有嗎」時，還是混入「蝦皮設備人員」「蝦皮客服」「蝦皮後勤專員」這三筆完全不相關的內勤職缺。請使用者直接核對 Notion 欄位後確認：這三筆職缺的「職缺名稱(對外)」（求職者實際看到的標題）跟「職務類別」（設備人員/文字客服）都完全沒有「門市」相關字眼，**「門市」「智取店」「店到店」這幾個字眼只出現在「職缺名稱」——這是同仁自己取的內部/行政命名慣例**（例如「蝦皮內勤(北北基宜)門市裝潢工程外勤專員」「蝦皮內勤 智取店客服」），意思是「這個內勤職位支援門市營運」，不代表職務本身是門市類別。
    - **根本原因**：跟第 33、37 項是同一種「信任了不該信任的欄位」模式，但這次連 STRICT（嚴格）比對層都中招——`job_matches_category_filter()` 的嚴格比對原本拿 `primary_text`（職缺名稱+職缺名稱(對外)+職務類別）去比對「門市」關鍵字，其中「職缺名稱」是內部命名，不是求職者看得到、也不是結構化分類用的欄位，寫法完全看同仁習慣，可能為了方便管理而帶到「這個職缺是支援哪個門市據點/團隊」這類行政標籤，跟這個職缺實際的「職務類別」是兩回事。`_job_extended_category_text()`（第 37 項新增的寬鬆比對專用文字）當時也還留著「職缺名稱」，同一個洞沒有堵到底。
    - **修正方式**：`services/matcher_service.py` 新增 `primary_category_text`（只用「職缺名稱(對外)」+「職務類別」，不放「職缺名稱」），嚴格比對的職務類別檢查改用這個新文字；`_job_extended_category_text()` 也拿掉「職缺名稱」，只留「職缺名稱(對外)」「職務類別」「行業別」。**廠商比對不受影響**——職缺名稱通常確實會帶到真正的廠商名稱（例如都以「蝦皮內勤」開頭），繼續信任 `primary_text`／`extended_text`，這次只調整職務類別這一項判斷依據。
    - **這次的教訓**：這是同一個「不能信任的欄位」原則第三次現身，但這次提醒了一個新面向——不是所有「看起來像結構化欄位」的東西都真的結構化。「職缺名稱」雖然不是像「工作內容(對外)」那種行銷自由文字，但它是**內部行政命名**，同仁可以照自己方便的方式取名（帶部門、地區、專案代號都可能），不是給求職者看的對外分類依據。判斷「這個職缺屬於什麼類別」，永遠只能依據求職者看得到的「職缺名稱(對外)」跟同仁明確勾選的「職務類別」/「行業別」，其他任何欄位（不管是自由文字說明、還是內部行政命名）都不能拿來當職務類別的判斷依據。
    - **新增測試**：`tests/test_matcher_service.py` 新增 `CategoryMatchingIgnoresInternalNamingConventionTests`（5 個：直接用使用者提供、並向 Notion 核對過的真實三筆職缺資料重現這次的誤判並確認排除、確認真正的門市職缺仍能透過「職缺名稱(對外)」正常命中、`filter_jobs_by_category_tiered()` 整體行為確認四筆職缺中只有真正的門市職缺會出現在結果清單）。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 512 個測試，OK。
43. **接續查出第 42 項提到「待查」的問題根因：AI 把「之前推薦過同一筆職缺」直接當成「這次也符合」，沒有重新核對地點**：使用者提供完整對話截圖，確認不是「同一句話問多次答案會變」，而是先問「有蝦皮門市的兼職嗎」（沒指定地區，正確推薦了「蝦皮店到店門市夥伴」），接著問「蝦皮門市 八德有缺嗎」，AI 回覆「正是之前沛沛向您推薦的蝦皮門市 (ID:0)，您可以參考看看喔！」——直接沿用剛才那筆職缺當作答案，但完全沒有重新核對「這筆職缺的地點欄位到底有沒有列出八德」（已確認沒有）。卡片本身其實老實（顯示籠統的「各區門市據點」，不是「八德區」），矛盾只出現在 AI 自己生成的文字回覆上。
    - **根本原因**：規則 4（地區判斷只能依據地點欄位、不能自行推論）只涵蓋了「自己憑常識類推同縣市涵蓋範圍」這一種情境，沒有涵蓋「因為之前才剛推薦過同一筆職缺，就跳過重新核對、直接沿用」這種情境——這是對話歷史造成的另一種偏誤，跟第 34 項的「憑常識類推」是不同的心理機制，但一樣是提示詞沒有明確禁止。
    - **修正方式**：`handlers/message_handler.py` 在規則 4 底下新增一條子規則，明講「即使過去對話裡已經推薦過某筆職缺，只要求職者這次問的是更精確或不同的地區，都要重新核對這筆職缺當下的地點欄位，不能因為之前推薦過就直接沿用」。
    - **這次的教訓**：AI 決策層的提示詞規則要覆蓋「AI 可能走捷徑的每一種路徑」，不能只補一種——「自己憑常識類推」跟「偷懶沿用過去推薦過的答案」是兩種不同的走捷徑方式，各自都需要獨立的規則明確禁止，不能以為擋掉一種就代表都擋掉了。
    - **新增測試**：`tests/test_message_handler.py` 新增 `test_ai_prompt_forbids_reusing_previous_recommendation_without_rechecking_location`（驗證提示詞裡有把這條新規則寫進去）。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 513 個測試，OK。
44. **新增功能：地區完全沒有符合的職缺時，退一步推薦同縣市的其他職缺（比照真人派遣專員的對話習慣）**：使用者提出這幾天一直在防堵「AI 自己推論地區涵蓋範圍」，但真人派遣專員平常跟求職者對話，本來就會自然推薦鄰近或類似的工作（例如求職者問「蝦皮門市 八德有缺嗎」，八德沒有時，會提「桃園市其他地方有喔」）——這是合理需求，但要求務必先詳細檢查會不會跟這兩天除錯的內容衝突，確認沒衝突才能上線。
    - **設計方向**：跟使用者確認過，選擇「同縣市」而不是嚴謹的地理相鄰（例如八德只跟大溪/龍潭/平鎮/桃園區實際接壤）——後者需要一份完整的台灣行政區地理相鄰地圖資料，維護成本高；「同縣市」用一份人工維護的「行政區→縣市」對照表（`LOCATION_TO_COUNTY`）就能查表確認，足夠貼近真人專員實務上「這個縣市範圍內」的推薦習慣，且完全是結構化事實比對，不涉及任何地理推論。
    - **實作方式**：`services/matcher_service.py` 新增 `LOCATION_TO_COUNTY`（涵蓋 `LOCATION_CANDIDATES` 全部地名的行政區→縣市對照表）與 `find_county_level_alternative_jobs()`（只依 `_location_search_text` 這個結構化欄位查同縣市，不做任何自由文字比對或地理推論）。`handlers/message_handler.py` 的「精準工種直達攔截」（門市/外送/momo 三個分支）在地區精準比對完全落空時，改成先查一次同縣市有沒有替代方案，有的話用固定樣板句（不是交給 AI 自由生成）誠實回覆「『{地區}』目前沒有明確列出的{類別}職缺，不過同樣在{縣市}還有相關職缺，要不要參考看看呢？」，附上該替代職缺的卡片；同縣市也找不到才會落到 AI 決策，行為跟修正前一致。
    - **跟這兩天除錯內容的衝突檢查（使用者明確要求）**：逐一比對第 33～43 項的每一個修正，確認沒有衝突：
      - 第 33／37／42 項（地區/類別比對只信任結構化欄位，不信任自由文字/內部命名）：這次新功能直接沿用 `_location_search_text`／`filter_jobs_by_category_tiered()` 這些已經修好的函式，不是重新寫一套比對邏輯，自動繼承這些修正，不會走回頭路。
      - 第 34／40／43 項（AI 不能自行推論地區涵蓋範圍／要看鎖定條件／不能沿用舊推薦）：這次新功能完全是**確定性攔截**、固定樣板句，不經過 AI 自由生成，回覆文字明講「原本地區沒有，這是同縣市的其他地方」，不會讓使用者誤以為原本地區也有符合的職缺，方向上跟這幾條規則要防堵的問題（AI 含糊暗示地區涵蓋）完全相反，不衝突。
      - 第 38 項（追問地區延續前一輪脈絡）：這次新功能是接在既有攔截流程「地區精準比對落空」之後，用同一套 `is_delivery_intent`／`is_store_intent`／`is_momo_intent`（含延續前一輪脈絡判斷出的結果），確認過延續前一輪脈絡問地區時（例如先問「蝦皮門市」，接著單獨問「八德有缺嗎」）也能正確觸發同縣市建議。
      - 第 39 項（拿掉 momo「地區沒有就不管地區顯示全部」的退讓邏輯）：這是最需要小心的一項，因為表面上都是「地區沒中，還是給你看職缺」——但差異很關鍵：第 39 項拿掉的是「地區完全沒篩選、把全部 momo 職缺都當成符合條件」且用**同一句**「找到符合條件」的話術；這次新功能是「篩過同縣市」的職缺，且用**另一句明講沒有原地區、這是同縣市**的話術，兩者不是同一段邏輯，也沒有把第 39 項拿掉的退讓邏輯復原。
      - 新增了兩個既有測試需要調整以避免跟新功能混在一起測（`StoreIntentLocationMatchTests` 原本用的測試職缺剛好跟查詢地區同縣市，改成不同縣市的地區，讓「自由文字地名不該誤判」跟「同縣市退讓建議」這兩個功能各自獨立測試，不互相干擾）。
    - **新增測試**：`tests/test_matcher_service.py` 新增 `CountyLevelAlternativeJobsTests`（3 個：同縣市不同行政區能找到、不同縣市找不到、對照表沒收錄的地名安全回傳空清單）；`tests/test_message_handler.py` 新增 `CountyLevelFallbackRecommendationTests`（4 個：地區精準比對落空時正確觸發同縣市建議且不落到 AI 決策、延續前一輪脈絡的地區追問也能觸發、momo 分支也適用、同縣市也找不到替代方案時維持原行為落到 AI 決策）。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 520 個測試，OK。
45. **職缺卡片改用材霈品牌色**：使用者詢問 LINE 職缺卡片是不是公版、能不能美化。確認 LINE 的 Flex Message 不是官方模板，是完全自訂的 JSON 排版，原本用的是 LINE 預設綠色（`#00B900`）跟一般 Material Design 配色，沒有材霈自己的品牌識別。使用者指定用「內部系統網頁那個接近橘色的顏色」——直接在同一個 repo 裡找到：`delivery/static/style.css` 定義的 `--brand: #ea580c`／`--brand-dark: #c2410c`／`--brand-bg: #fff1e8`，這套配色是 `/portal`、`/management`、`/hr`、`/delivery` 這幾個內部系統網頁共用的公司品牌色（`templates/base.html` 等多處 `<head>` 都連結到這份 CSS）。
    - **修正方式**：`services/flex_service.py` 的 `create_job_flex_card()` 新增 `BRAND`／`BRAND_DARK`／`BRAND_BG` 三個常數（跟 `delivery/static/style.css` 完全同色碼），套用到卡片最顯眼的三個地方：頂部「🎯 材霈推薦職缺」標籤文字、職務類別標籤（原本的淺紫色改成品牌橘）、「填寫線上履歷」主要按鈕（原本的 LINE 綠改成品牌橘）。班別／產業／全兼職這幾個次要標籤維持原本的多色系（分別是綠/藍/紫），不整個換成同一個顏色，保留一眼就能分辨不同資訊類型的可讀性；待遇金額維持紅色強調（金額類資訊維持既有的警示色慣例）。
    - **新增測試**：`tests/test_flex_service.py` 新增 `CreateJobFlexCardBrandColorTests`（3 個：頂部標籤文字用品牌橘、主要按鈕用品牌橘、職務類別標籤用品牌橘）。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 523 個測試，OK。
46. **初期把「職缺完全找不到」也一併記錄進常見問答集**：使用者表示初期想盡量多蒐集求職者到底都在問什麼，不管是「常見問題庫沒收錄」還是「職缺完全比對不到」，都先一律寫進同一份常見問答集／求職者提問追蹤，方便之後一次盤點、整理出真正該擴充的職缺類別或該補上的常見問答。原本只有 AI 決策判斷成 `UNKNOWN_FAQ`（問規章/制度/福利等不是問職缺的問題）才會寫入；`NO_MATCH`（求職者問的廠商/地區/類別完全找不到相符職缺）原本只會回一句「暫無」就結束，什麼都沒留下。
    - **修正方式**：`handlers/message_handler.py` 把原本寫死在 `UNKNOWN_FAQ` 分支裡的兩段寫入邏輯（寫進 Notion FAQ 資料庫、另外記一筆「求職者提問追蹤」讓招募專員回去找人手動回覆）抽成共用函式 `_record_unanswered_question()`，`NO_MATCH` 分支現在也會呼叫同一個函式。`ASK`（AI 只是需要使用者補充條件才能繼續判斷，例如「我想找工作」這種還沒講清楚要找哪裡的正常追問）刻意不記錄——這不是「沒比對到答案」，是正常對話流程的一部分，記進常見問答集只會製造雜訊。
    - **兩個 Notion 資料庫本來就有的去重/不去重規則不受影響**：FAQ 候選資料庫寫入前仍會去重（同一句或高度相似的問題不會重複堆積），求職者提問追蹤仍然不去重（同一個問題如果有好幾個人各自問過，要留好幾筆才能各自回覆到）。
    - **新增測試**：`tests/test_message_handler.py` 新增 `test_no_match_also_records_into_faq_and_followup`（驗證 `NO_MATCH` 會呼叫到 `append_unresolved_faq_to_notion`／`append_unresolved_question_for_followup`，且暱稱查詢邏輯跟 `UNKNOWN_FAQ` 共用同一套）、`test_ask_action_does_not_record_into_faq`（驗證 `ASK` 不會誤觸發記錄）。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 542 個測試，OK。
47. **履歷點擊紀錄：記錄誰點了職缺卡片的「填寫線上履歷」按鈕**：使用者希望知道有點擊這顆按鈕的求職者 LINE 名稱，方便招募專員追蹤。技術上有個關鍵限制：這顆按鈕是 LINE 的 `uri` 類型（點下去直接開外部瀏覽器），**點擊本身完全不會觸發任何 webhook 事件**，機器人原本沒有任何方式能在伺服器端知道誰點了它。
    - **做法**：讓按鈕改連到我們自己這支服務新增的 `/apply-click` 轉址端點（`main.py`），把「誰、點了哪個職缺、哪個產業分類」記錄進新的 Notion「履歷點擊紀錄」資料庫，再用 HTTP 302 立刻轉址到真正的履歷網站——求職者感覺不出差異，只多花幾乎瞬間的伺服器轉址時間。`services/flex_service.py` 的 `create_job_flex_card()` 只有在 `SERVICE_BASE_URL` 有設定時才會把按鈕改連到這個轉址端點，沒設定時維持原本直接連履歷網站的行為，按鈕不會壞掉。
    - **安全性考量（開放重導向）**：轉址目的地只接受 `Spx`／`Service`／`Manufacture` 這三個內部白名單代號（`config.py` 的 `DEFAULT_RESUME_URLS`），不接受外部直接傳一個完整網址進來當轉址目標，避免這個轉址端點被拿去偽造成看似「材霈網域開頭」、實際轉去釣魚網站的連結。
    - **兩層防護確保記錄失敗不影響轉址**：`services/notion_service.py` 的 `record_resume_click()` 自己有 try/except（Notion 沒設定、逾時、寫入失敗都只印 log、回傳 `False`）；`main.py` 的端點又整層包了一次 try/except——求職者永遠都能順利到達履歷網站，記錄點擊純粹是附加價值，絕對不能反過來卡住應徵流程。
    - **使用者已完成 Notion 資料庫建立**：「履歷點擊紀錄」資料庫欄位為 `求職者暱稱`（標題）／`LINE User ID`（文字）／`應徵職缺`（文字）／`產業類別`（文字）／`點擊時間`（日期）——第一次建立時「點擊時間」誤設成文字類型、且漏了「產業類別」欄位，已請使用者修正並確認過欄位設定正確。
    - **上線前還需要使用者做（詳見上方「待辦事項」對應段落）**：確認資料庫已分享給 Notion 整合、到 Cloud Run 設定 `NOTION_RESUME_CLICK_LOG_DB_ID`／`SERVICE_BASE_URL` 兩個環境變數（可用 `gcloud run services update` 指令、也可以直接在 Cloud Run 主控台「編輯並部署新修訂版本」→「變數與密鑰」分頁手動新增，兩種方式效果相同）。
    - **新增測試**：`tests/test_notion_service.py` 新增 `RecordResumeClickTests`（5 個：成功寫入含產業類別標籤、沒有顯示名稱時退回用 user_id、資料庫沒設定時安全跳過、缺 user_id 時安全跳過、寫入失敗回傳 False 不拋例外）；`tests/test_flex_service.py` 新增 `CreateJobFlexCardResumeClickTrackingTests`（3 個：`SERVICE_BASE_URL` 沒設定時按鈕維持直接連履歷網站、有設定時按鈕改連轉址端點並帶對的 uid/type/job 參數、網址結尾多一個 `/` 不會產生雙斜線）；新增 `tests/test_apply_click_endpoint.py`（`ApplyClickRedirectTests`，6 個：預設轉址正確、未知 type 代號安全退回 Manufacture、正式頻道能查到顯示名稱時正確記錄、正式頻道查無資料時退回查測試頻道、記錄過程整個失敗也不影響轉址、沒帶 uid 時跳過記錄但仍正常轉址）。
    - **面試預約功能刻意沒有一起合併**：這兩個功能原本在同一個分支上先後開發，但使用者明確表示「面試預約請先不要加進去」，所以只挑這次的履歷點擊紀錄相關 commit 合併進 main，面試預約的程式碼仍然只留在 `claude/tsaipei-linebot-handoff-7jdsks` 分支上，之後要上線需要再另外合併一次。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 556 個測試，OK。
48. **接續發現：職缺涵蓋 5 個以上行政區、地點欄位改用「各區OO據點（自選區域）」概括描述時，AI 還是會自己腦補聲稱某個行政區也算涵蓋在內，甚至跟自己剛講過的話矛盾**：使用者實測「蝦皮門市」→「八德」（直接攔截誠實回答「八德目前沒有明確列出的蝦皮門市職缺」，並附上同縣市退讓建議卡片，這部分正確）→「有哪些區？」，這句話沒有指名任何行政區、也不是「都給我看看」這類既有攔截關鍵字，直接落到 AI 決策，AI 卻回覆「沛沛為您找到蝦皮門市在桃園市各區都有據點可選喔！八德區也在可選範圍內，歡迎參考！」——直接打臉自己上一句話才剛說「八德沒有明確列出」。
    - **根本原因**：第 34/40/43 項的提示詞規則（禁止 AI 自行推論地區涵蓋範圍）只舉了「地點欄位是短行政區清單（例如『桃園市（蘆竹、龜山）』）」這種情境的範例，完全沒涵蓋「地點欄位本身就是『各區OO據點（自選區域）』這種概括性描述」的情境——這種描述是 `format_clean_location()` 對涵蓋 5 個以上行政區的職缺刻意設計的簡化顯示（見第 36 項），本來就沒有列出实際行政區名稱，AI 因此有機可乘：一來提示詞沒講清楚「概括描述 ≠ 每個行政區都確定涵蓋」，二來候選職缺清單裡的「特色:」欄位帶了同仁自己填的行銷文案（例如「工作地點於宜蘭縣、桃園市...共110區，門市自選」），AI 就拿這段自由文字當依據自行拼湊出「八德也算」的結論，完全繞過了地點欄位本身該有的保守判斷；三來提示詞也沒有明講「這一輪的判斷不能跟自己在同一段對話裡剛講過的話矛盾」。
    - **修正方式**：`handlers/message_handler.py` 的 `ai_prompt` 規則 4 新增兩條子規則：① 地點欄位是「各區OO據點（自選區域）」這種概括描述時，代表同仁沒有列出所有行政區明細，不代表每一個都確定涵蓋，遇到「有哪些區」這類問法只能誠實說明系統只顯示概括範圍、建議直接應徵讓招募專員確認，「特色:」欄位的行銷文字同樣不能拿來當作確認依據；② 明講「絕對不能自我矛盾」——同一個行政區在同一段對話裡，如果之前已經回答過「沒有明確列出」，這一輪不能因為換了問法就改口說也算涵蓋在內。
    - **新增測試**：`tests/test_message_handler.py` 新增 `test_ai_prompt_forbids_claiming_vague_aggregate_covers_specific_district`（驗證提示詞裡有明講概括描述不能當作確認依據、特色欄位不能拿來當判斷依據）、`test_ai_prompt_forbids_self_contradiction_on_previously_ruled_out_district`（驗證提示詞裡有「絕對不能自我矛盾」規則，且【過去對話】確實把先前那句「沒有明確列出」的誠實回覆帶給 AI 看）。
    - **順便修正一個因為新規則文字本身用詞導致的既有測試誤判**：`test_ai_prompt_location_reflects_specific_district_for_broad_coverage_job` 原本檢查「整份提示詞裡完全不出現『自選區域』」，但這次新增的規則說明文字本身就會提到這個詞當範例，導致這個檢查即使資料本身完全正確也會誤判失敗——改成只檢查該筆職缺自己那一行「地點:」欄位的內容有沒有落回概括描述，不再檢查整份提示詞。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 558 個測試，OK。
48. **接續改善：同縣市退讓建議的回覆文字，改成直接列出具體有哪些行政區可選，不再只講空泛的「同樣在OO縣市還有相關職缺」**：第 47 項修好「AI 不能自己腦補行政區涵蓋範圍」之後，使用者測試時提出：既然求職者鎖定的是桃園市，而這筆職缺在桃園市底下實際涵蓋哪些行政區其實是同仁在 Notion 裡結構化勾選好的資料（不是要 AI 推論），那能不能乾脆直接把桃園市裡真正有據點的行政區列出來，讓求職者確切知道可以參考哪裡，而不是只講「同縣市還有相關職缺」這種還要求職者自己再問一次「有哪些區」的空泛說法。討論後確認這是確定性資料比對（不經 AI），跟第 47 項要防堵的「AI 自行推論」完全是不同的兩件事，不衝突。
    - **使用者明確要求這個情境不設數量上限**：同縣市地區數量通常不多，但特別提醒不要讓這個「不設上限」的決定，連帶影響到其他本來就需要防止卡片/清單爆量的既有邏輯（例如 `services/flex_service.py` 的 `format_clean_location()` 對涵蓋 5 個以上行政區的職缺仍然維持原本的概括顯示，避免卡片被塞爆；面試時段清單、快速回覆按鈕等其他有筆數上限的既有功能也完全沒有修改）。
    - **實作方式**：`services/matcher_service.py` 新增 `find_same_county_district_labels(same_county_jobs, target_location)`——讀取職缺的原始「行政區」欄位（不是給比對用、逗號會被清乾淨黏成一整串的 `_location_search_text`），用逗號/頓號/空白拆成一個一個地名 token，逐一比對 `LOCATION_TO_COUNTY` 這份既有對照表判斷是不是屬於目標縣市，是的話去掉重複的縣市前綴（跟卡片顯示用的邏輯風格一致）當作顯示用標籤，跨多筆職缺也會自動去重、保留原始出現順序，刻意不設數量上限。`handlers/message_handler.py` 的同縣市退讓建議分支（第 44 項）呼叫這個新函式，能拆出具體地名時就把回覆文字改成「不過{縣市}的{地名1}、{地名2}...有相關職缺」，拆不出來時（例如測試資料或極少數職缺沒有結構化「行政區」欄位）安全退回原本「同樣在{縣市}還有相關職缺」的空泛說法，不會因為列不出清單就整句話都不回覆。
    - **新增測試**：`tests/test_matcher_service.py` 新增 `FindSameCountyDistrictLabelsTests`（6 個：正確拆出並去除縣市前綴、涵蓋很多行政區時真的不設上限全部列出、跨職缺去重、排除不同縣市的行政區、缺少原始「行政區」欄位時安全回傳空清單、地名對照表沒收錄的地名安全回傳空清單）；`tests/test_message_handler.py` 新增 `test_lists_specific_same_county_districts_when_raw_field_available`（驗證整合流程下回覆文字有列出具體行政區、不再是空泛說法）。
49. **接續發現：`LOCATION_CANDIDATES` 除了新北市、桃園市之外，其他縣市完全沒有收錄任何行政區名稱，導致「誤配對到無關行政區」跟「完全沒被辨識、掉到 AI 決策亂猜」兩種真實回報的 bug**：
    - **案例一（竹北）**：使用者實測「蝦皮門市」鎖定類別/廠商後，問「新竹縣 竹北沒缺嗎」，沛沛回覆「有的！」卻附上一張新竹市北區的卡片——完全不相關的行政區。
    - **案例二（佳里）**：使用者問「台南有哪些區有缺呢？」正確找到職缺後，接著問「佳里有缺嗎」（這句話沒再提到「台南」），沛沛卻回覆「目前在台南佳里區暫時沒有符合『蝦皮門市』的職缺喔。不過台南下營區有蝦皮門市的職缺...」——但使用者確認 Notion 裡「行政區」欄位就是「台南市佳里區」，資料明明有。
    - **根本原因**：`extract_current_target_location()` 只查 `LOCATION_CANDIDATES` 這份手動維護的清單，而這份清單只有新北市、桃園市收錄到行政區層級的地名（板橋、八德…），其他每一個縣市都只收錄到縣市層級（新竹、台南…）。案例一是「新竹」被搶先命中、蓋掉本來該辨識出的「竹北」；案例二是「佳里」完全沒被任何清單收錄，訊息裡又沒有其他能辨識的地名，直接落到一般 AI 決策流程，AI 即使看到候選職缺的「地點:」欄位資料正確，仍然自行判斷錯誤（回覆了「沒有」加上一個錯誤的替代建議）。
    - **修正方式（改成從 Notion 職缺資料動態長出行政區清單，而不是手動維護一份涵蓋全台灣的地名表）**：`services/matcher_service.py` 新增一整組動態解析機制，理由是使用者確認 Notion「行政區」欄位一律用「縣市＋行政區」合併寫法（例如「台北市大安區」），跟這份資料共用職缺資料本來就有的 30 秒快取，不會多打一次 Notion API：
        - `_COUNTY_FULL_NAMES`：台灣 22 個縣市的正式全名，數量固定不變，用來當「縣市＋行政區」合併字串的切分依據。
        - `_strip_admin_suffix()` / `_split_district_token()`：把「台南市佳里區」這類字串拆成 (縣市核心字, 行政區核心字)，例如 ("台南", "佳里")；沒有縣市前綴時可以用呼叫端傳入的 fallback 縣市（來自這筆職缺自己「縣市」欄位，且只在該欄位只填單一縣市時才用）。刻意規定去掉字尾後至少要剩 2 個字才去——避免「東區」「西區」被去成單一個字，變成極危險的短字串誤判。
        - `build_district_county_index(active_jobs)`：掃描目前所有有效職缺的「行政區」欄位，建立「行政區核心字 → 對應到哪些縣市（集合）」的索引，例如 `{"佳里": {"台南"}, "東區": {"台中", "台南"}}`。
        - `resolve_county_for_location()`：查一個地名對應的縣市全名，優先查既有的 `LOCATION_TO_COUNTY`，查不到才退一步用上面的動態索引，且只有在「明確只對應到一個縣市」時才回傳，同名跨縣市（例如「東區」）保守回傳空字串。
        - `extract_current_target_location()` / `detect_negated_location()` 改成分三輪、精準度由高到低檢查：① `LOCATION_CANDIDATES` 裡「行政區層級」的詞（板橋、八德…，排除純縣市層級的詞）；② 動態索引裡「目前資料裡明確只對應一個縣市」的行政區核心字；③ 才退回 `LOCATION_CANDIDATES` 裡純縣市層級的詞（新竹、台南…）。**這個順序刻意不是「查完整份清單才查動態索引」**：如果縣市層級的詞跟行政區層級的詞混在同一輪查、縣市層級的詞剛好也是訊息裡的子字串（例如「新竹縣 竹北」裡的「新竹」），會搶先命中、蓋掉根本還沒機會被檢查到的「竹北」，這正是案例一實際發生的原因，開發過程中被新增的回歸測試抓到、才改成三輪分開查。
        - `find_county_level_alternative_jobs()` / `find_same_county_district_labels()`（第 44/48 項）也都加上 `active_jobs` 參數，改呼叫 `resolve_county_for_location()`，讓「同縣市退讓建議」這個既有功能一併吃到動態解析出的縣市，不會因為地名是動態辨識出來的就查不到縣市。
    - **刻意保留的限制（已知後續待辦，明講不隱藏）**：同一個行政區名稱同時存在於多個縣市時（例如「東區」台中、台南都有），這一版刻意不猜、保守回傳空字串，讓這句話落到既有的 AI 決策保底流程——不會誤答，但也不會主動精準攔截，使用者這種情況下可能還是要多問一句講清楚縣市。「向使用者反問釐清是哪個縣市」的體驗後續可以再做，這次沒有實作。
    - **`handlers/message_handler.py` 呼叫端配合更新**：`extract_current_target_location`／`detect_negated_location`／`find_county_level_alternative_jobs`／`find_same_county_district_labels` 這四處呼叫都補上 `active_jobs`（流程一開始就抓好、不用額外查詢）；原本直接查 `LOCATION_TO_COUNTY` 的地方改成呼叫 `resolve_county_for_location()`。
    - **新增測試**：`tests/test_matcher_service.py` 新增 `StripAdminSuffixTests`、`SplitDistrictTokenTests`、`BuildDistrictCountyIndexTests`、`ResolveCountyForLocationTests`、`ExtractLocationDynamicDistrictRegressionTests`（含直接重現竹北／佳里兩個回報案例、同名跨縣市不亂猜、沒傳 `active_jobs` 時維持原行為不出錯等情境）；`tests/test_message_handler.py` 新增 `DynamicDistrictRecognitionRegressionTests`（`test_zhubei_query_matches_zhubei_job_not_unrelated_hsinchu_city_job`、`test_jiali_query_matches_directly_without_falling_to_ai`），走完整的 `process_user_message` 流程重現並驗證兩個案例都修好。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 591 個測試，OK。
50. **接續發現：第 49 項上線後，地區辨識本身變準了，卻連帶暴露出「卡片地點欄位」原本就存在、只是過去沒被踩到的兩個顯示層 bug（不影響底層配對邏輯，純粹是卡片/文字顯示不一致或不精準）**：
    - **案例一（同一筆職缺涵蓋範圍很廣，籠統縣市查詢只顯示第一個符合的行政區）**：使用者實測某筆「蝦皮店到店門市夥伴」職缺，行政區欄位一次列了 110 個行政區（橫跨 15 個縣市），其中台南市底下同時有下營區、佳里區兩筆。問「台南哪些區有缺」、「只有下營嗎」、「台南總共哪些區有缺」都只顯示下營區，問不出佳里區也有。
    - **案例二（同縣市退讓建議的卡片，地點欄位顯示全部縣市，跟回覆文字兜不起來）**：使用者問「八德有缺嗎」，退讓建議文字正確回覆「不過桃園市的桃園區、蘆竹區...有相關職缺」，但附上的卡片「📍地點」卻印出職缺橫跨的全部 15 個縣市（宜蘭縣,桃園市,高雄市...）；問「永康有嗎」也是同樣情況，文字正確列出「台南市的下營、佳里」，卡片一樣顯示全部縣市。
    - **根本原因**：兩個案例都出在 `services/flex_service.py` 的 `format_clean_location()`——① 使用者指定 `target_location` 時，原本邏輯是「找到第一個符合的行政區就立刻回傳」，職缺只列 1~4 個行政區時「第一個」剛好等於「唯一一個」，不會有問題，但這筆職缺涵蓋上百個行政區、同一個縣市底下就有不只一個相符時，就只會顯示第一個，其餘的（佳里）完全看不到；②「同縣市退讓建議」呼叫卡片產生器時，`target_location` 傳的是空字串（因為使用者原本問的地點本來就沒精準命中，傳了也配不到），`format_clean_location()` 完全沒有任何地點線索可以縮小範圍，只能退回「行政區 ≥5 個時用職缺自己整包『縣市』欄位湊字」這條路，這筆職缺的「縣市」欄位本身就是 15 個縣市串起來，卡片自然把全部縣市都印出來；同一時間，回覆文字是另一支獨立函式 `find_same_county_district_labels()`（第 48 項）產生的，這支函式有專門篩選「只挑屬於目標縣市的行政區」，兩邊各自為政、沒有共用同一份「該顯示哪些行政區」的判斷，才會讓文字跟卡片兜不起來。
    - **修正方式（純顯示層調整，不影響底層配對邏輯——配對到哪些職缺本來就是對的，只是卡片/文字沒講清楚）**：`services/flex_service.py`
        - `format_clean_location()` 的 `target_location` 比對邏輯，改成「收集全部符合的行政區、用頓號全部列出」，不再只回傳找到的第一個。
        - 新增 `same_county_scope` 參數，專門給「同縣市退讓建議」使用：傳進縣市名稱後，會先把行政區範圍縮小到「這個縣市底下」（用職缺原始、尚未去除縣市前綴的行政區文字比對，一般職缺沒有前綴時一樣正確運作，不受影響），再套用跟一般情況一樣的「行政區數量級距」判斷（≤4 個列出詳細行政區、≥5 個才用產業專屬概括描述），不會再把職缺橫跨的其他縣市一起印出來。
        - `create_job_flex_card()` 新增同名 `same_county_scope` 參數往下傳給 `format_clean_location()`。
        - `handlers/message_handler.py` 的同縣市退讓建議分支（第 44/48/49 項），呼叫卡片產生器時改傳 `same_county_scope=county_name`（本來就已經算好、給回覆文字用的同一個縣市名稱），讓卡片跟文字一致。
    - **新增測試**：`tests/test_flex_service.py` 新增 `FormatCleanLocationMultipleDistrictMatchTests`（驗證同一個 `target_location` 命中不只一個行政區時全部列出、單一命中維持原行為不受影響）、`FormatCleanLocationSameCountyScopeTests`（縮小到指定縣市後行政區數量分別在 ≤4／≥5／完全沒有相符行政區三種情況、一般單一縣市職缺傳這個新參數也不影響原本結果）、`CreateJobFlexCardSameCountyScopeTests`（驗證 `create_job_flex_card()` 有把參數往下傳）；`tests/test_message_handler.py` 的 `CountyLevelFallbackRecommendationTests` 補上驗證會呼叫 `create_job_flex_card` 時帶 `same_county_scope`，並新增 `test_card_location_scoped_to_county_when_job_spans_many_counties` 直接重現「職缺橫跨桃園市／台南市、卡片只應顯示桃園市部分」的實測案例。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 599 個測試，OK。
51. **新增：福利/配備關鍵字直達攔截（例如「有公司車嗎」直接推薦有勾選該福利的職缺）**：使用者提出：有些求職者問的不是地區/類別/廠商，而是「這份工作有沒有某項福利/配備」（例如「我要公司車的工作」「我選公司車」「有公司車嗎」），這種問法通常很直接對應到某幾筆有勾選該福利的職缺（例如「蝦皮外送三輪雇傭」），問能不能讓沛沛更精準回應。討論後決定不用 FAQ（FAQ 答案只會回文字＋快速回覆按鈕，架構上不會附職缺卡片，見下方「已釐清」）也不寫死在程式碼裡（維護要改程式碼、還要重新部署），改成跟第 49 項行政區動態解析同一種精神：從 Notion 職缺資料庫新增的「福利」欄位動態長出關鍵字清單，同仁自己在 Notion 幫職缺填福利關鍵字就好，不用找人改程式碼。
    - **已釐清（過程中的討論）**：FAQ 資料庫命中（不管是高信心直接命中、還是 AI 判斷成 `UNKNOWN_FAQ`）都只會回純文字＋快速回覆按鈕，職缺卡片只有 AI 判斷成 `RECOMMEND` 這個動作才會附上，讀的是職缺資料（`ai_job_candidates`），跟 FAQ 資料庫是完全分開的兩條路徑，AI 沒辦法「一半照 FAQ 答案回答、一半附職缺卡片」。也確認過現有的加分排序機制（`_score_job_for_ai`／`_tokenize_search_terms`）完全不會抓到「公司車」這種自由關鍵字（只認得地區/班別/廠商/類別這幾份手動維護清單裡收錄的詞），這句話原本只能完全交給 AI 自己從候選職缺的「工作內容(對外)」欄位猜，命不命中純看運氣。
    - **使用者要做的事（Notion 端）**：在職缺資料庫新增一欄「福利」，有該項福利的職缺在這欄填上關鍵字（例如「公司車」），多個福利用逗號分隔（例如「公司車,全勤獎金」），跟「行政區」欄位的多值寫法一致。
    - **差點漏掉的一步（`config.py` 的 Notion 讀取白名單）**：使用者實際在 Notion 加好「福利」欄位、填上「公司車」之後，上線前用 Notion MCP 直接檢查該筆職缺頁面，才發現 `services/notion_service.py` 的 `fetch_jobs_data()` 只會讀取 `config.py` 的 `ALLOWED_PROPERTIES` 白名單裡列出的欄位——「福利」是全新欄位，原本沒有被列進這份白名單，代表即使 Notion 資料填對了，程式也會把這個欄位整個濾掉，`find_benefit_matched_jobs()` 永遠比對不到任何資料，整個功能會悄悄地失效而不會報錯。已把「福利」加進 `ALLOWED_PROPERTIES`，並新增 `tests/test_notion_service.py` 的 `BenefitFieldReadThroughTests` 直接驗證這個欄位確實有被讀進 `job_dict`，避免以後新增其他欄位時又忘記同步更新這份白名單。
    - **實作方式（程式端）**：`services/matcher_service.py` 新增：
        - `build_benefit_keyword_index(active_jobs)`：掃描目前有效職缺的「福利」欄位，建立「福利關鍵字 → 有這項福利的職缺清單」的對照表。
        - `find_benefit_matched_jobs(raw_msg, active_jobs)`：從使用者訊息裡找出有沒有命中上述索引的關鍵字，命中就回傳 `(關鍵字, 職缺清單)`，依關鍵字長度由長到短檢查（避免短關鍵字搶先蓋掉更精確的關鍵字，跟行政區辨識的處理方式一致）。
        - `handlers/message_handler.py` 新增「步驟 1-3：福利/配備關鍵字直達攔截」，排在既有的類別/廠商直達攔截（門市/外送/momo）之後、同縣市退讓建議之前——這句話沒命中類別/廠商關鍵字時才會走到這裡，避免互搶攔截；命中就直接組卡片回覆，不經過 AI 決策。跟其他直達攔截一致：排除否定語氣（「不要公司車的」不會誤觸發）、使用者這輪如果已鎖定地區，一併用地區篩選縮小範圍（篩選後沒有職缺就視為沒命中，落到既有的 AI 決策保底流程，不特別做福利版本的同縣市退讓建議）。
    - **刻意保留的限制（明講不隱藏）**：福利意圖不會像地區/類別/廠商那樣被記進對話槽位、延續到下一輪追問（例如問完「有公司車嗎」，下一句只問「桃園呢」不會自動延續「公司車」這個條件）——這次先只做「當輪訊息裡明確提到福利關鍵字」的直接攔截，多輪追問延續的體驗如果之後有需要可以再擴充。
    - **新增測試**：`tests/test_matcher_service.py` 新增 `BuildBenefitKeywordIndexTests`、`FindBenefitMatchedJobsTests`（含三種講法都能命中同一筆職缺、否定語氣情境、長關鍵字優先於短關鍵字子字串等情境）；`tests/test_message_handler.py` 新增 `BenefitKeywordDirectInterceptTests`（直接命中不落到 AI 決策、否定語氣不誤觸發、地區篩選縮小範圍、沒有福利關鍵字時正常落到 AI 決策）；`tests/test_notion_service.py` 新增 `BenefitFieldReadThroughTests`（驗證「福利」欄位真的有被 `fetch_jobs_data()` 讀進來，見上方「差點漏掉的一步」）。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 613 個測試，OK。
52. **試營運將近一週後，請 Claude 全面檢查程式碼有沒有邏輯問題或安全性漏洞，修好其中兩項（AI 提示詞注入防護、Session 建立的並發競態）**：派出三組審查（`main.py`/`config.py`/`session_service.py`/`notion_service.py` 安全性；`handlers/message_handler.py` 對話流程邏輯；`matcher_service.py`/`flex_service.py`/`ai_service.py` 比對與 AI 服務邏輯），找到多項風險，這次先修好其中兩項：
    - **AI 提示詞注入防護**：`handlers/message_handler.py` 的主要 AI 決策提示詞、`services/ai_service.py` 的職缺詳情美化排版提示詞，都會把同仁在 Notion 填寫的自由文字（職缺「特色」「工作內容」、FAQ「答：」）直接接進提示詞裡，沒有任何分隔或說明，理論上如果這些欄位被寫成類似「忽略以上規則」的文字，有機會干擾 AI 判斷（例如跳過就業服務法合規審查、或無視原本「不能自行推論地區涵蓋範圍」等既有規則）。目前這些欄位只有同仁能編輯，風險可控，但屬於沒有防護的漏洞，這次補上：① `ai_service.py` 把「原始工作內容」用明確的 `&lt;&lt;&lt;原始工作內容開始/結束&gt;&gt;&gt;` 分隔符號包起來，並新增一條規則明講這段內容不能跳過合規審查；② `message_handler.py` 的主要決策提示詞新增規則 7，明講候選職缺清單／FAQ 內容都只是資料不是指令，不能因為裡面出現看起來像指令的文字就改變判斷邏輯。
        - **新增測試**：`tests/test_ai_service.py` 新增 `FormatFullJobDetailPromptInjectionGuardTests`；`tests/test_message_handler.py` 新增 `test_ai_prompt_forbids_treating_job_or_faq_free_text_as_instructions`。
    - **Session 建立的並發競態（極窄視窗，理論風險）**：`services/session_service.py` 的 `_get_or_create_session()`（`get_user_history`/`get_user_slots` 背後都會呼叫到）原本是「純讀取一次 → 視情況整份覆寫或局部更新」，唯獨這個函式沒有跟 `update_user_slots`/`append_user_history`/`clear_user_slots` 一樣包在 Firestore transaction 裡。使用者 session 剛好過期（7 天沒互動）或這是第一次互動時，會整份覆寫（`ref.set`）——如果同一位使用者幾乎同時傳兩則訊息（LINE 有時會重送、或這套「限時同步等待＋逾時後背景補發」架構本來就可能讓兩個請求同時處理同一個人），兩次都命中「要重建 session」的情境，其中一次即使已經透過 transaction 正確存好地區/類別等槽位，還是可能被另一次沒有並發保護的整份覆寫蓋掉、憑空消失。改成跟其他三個函式共用同一個 `_run_in_transaction()`（`mutate` 直接原封不動回傳 session，只是要用同一套有並發保護的讀-改-寫機制），修好後 Firestore 偵測到寫入衝突會自動重試，不會再被蓋掉。
        - **沒有新增 Firestore mock 測試**：這個檔案既有的測試（`tests/test_session_service.py`）刻意只測純邏輯部分（`_normalize_session`／`_merge_slot_updates`／`_append_history_entry`），沒有替任何一個實際會呼叫 Firestore transaction 的函式（含既有的 `update_user_slots` 等）寫過整合測試，這次修正沿用同樣的既有做法，不另外破例。
    - **其餘檢查出來、這次先不動的項目（已跟使用者說明，等對方確認後續處理方式）**：`/apply-click`／Notion `page_id` 格式驗證／log 未過濾換行字元／面試預約流程（尚未上線）信任使用者手動輸入的時段代碼與職缺名稱。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 615 個測試，OK。
53. **接續處理上一項審查抓到的其餘 5 個問題**：
    - **福利關鍵字直達攔截沒有檢查否定語氣、也沒有排除太短的關鍵字**：`services/matcher_service.py` 的 `find_benefit_matched_jobs()` 補上 `_keyword_is_negated()` 檢查（「不要有公司車的工作」不再被誤判成正向意圖）、排除長度小於 2 的關鍵字（避免同仁不小心在「福利」欄位填單一個字，變成任何無關句子都可能誤判命中的危險短字串，跟 `_strip_admin_suffix()` 保留至少 2 個字的既有防呆原則一致）。
    - **已經成功回覆使用者之後，記錄事件本身出錯會讓使用者收到多餘的保底訊息**：`services/monitoring_service.py` 的 `log_ai_decision_event()` 補上自己的 try/except，任何內部失敗都只印警告 log、不再往外拋例外——這支函式幾乎都是「先成功回覆使用者，最後才呼叫這裡記錄結果」，原本記錄本身出錯會被呼叫端的外層保底邏輯誤判成整個流程失敗，多送一則「系統稍有延遲」的訊息給已經收到正確回覆的使用者。
    - **「都給我看看」全部瀏覽攔截沒有檢查否定語氣**：`handlers/message_handler.py` 把 `is_negative = has_negative_intent(raw_msg)` 從步驟 1 提前到步驟 0-3 就先算好，讓步驟 0-4 的 `is_show_all` 判斷也能用同一份否定語氣檢查——修正前「不要都給我看」這種明確否定的話，會被誤判成「要看全部職缺」，跟其他所有攔截分支的既有行為不一致（其他分支都有做這個檢查，只有這個因為 `is_negative` 那時候還沒算出來而漏掉）。
    - **卡片地點顯示比對「台/臺」沒有統一正規化**：`services/flex_service.py` 新增 `_normalize_tai()`，`format_clean_location()` 的 `target_location`／`same_county_scope` 比對都改用正規化後的文字比對（顯示仍然用原始字串，不影響同仁實際填寫的用字）。修正前如果同仁在 Notion 填「臺南市」（正體全形寫法）而不是「台南市」，退讓建議或行政區比對可能會整個失效、退化成只顯示縣市名稱。
    - **一般地點聚合分支沒有去除重複值**：`format_clean_location()` 建立 `dist_list` 時改用 `dict.fromkeys` 去重複（`target_location`／`same_county_scope` 兩個分支原本就有做，只有這個一般分支漏掉），避免同仁複製貼上「行政區」欄位不小心貼出重複值時，行政區數量被灌水誤觸發「≥5 個行政區改用概括描述」規則，或顯示文字重複列出同一個行政區。
    - **新增測試**：`tests/test_matcher_service.py` 的 `FindBenefitMatchedJobsTests` 新增否定語氣、短關鍵字兩個情境；`tests/test_monitoring_service.py` 新增 `test_internal_failure_does_not_propagate_to_caller`；`tests/test_message_handler.py` 新增 `ShowAllNegationTests`；`tests/test_flex_service.py` 新增 `FormatCleanLocationTaiVariantNormalizationTests`、`FormatCleanLocationDedupTests`。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 624 個測試，OK。
54. **修正：蝦皮外送職缺的履歷連結被錯發成蝦皮門市專屬版**：使用者實測回報「蝦皮外送給的履歷連結好像給成蝦皮門市專用版了」。
    - **根本原因**：`services/flex_service.py` 的 `resolve_apply_url_key_by_industry()` 原本只要職缺文字裡出現「蝦皮」兩個字，就直接歸類成 `Spx`（蝦皮門市專屬履歷連結）。蝦皮外送職缺的職缺名稱/職務類別文字裡同時會出現「蝦皮」跟「外送」，一律被「蝦皮」這個過寬的關鍵字搶先攔截，導致外送職缺誤拿到門市版的履歷連結。
    - **權威依據**：在 Notion 找到同仁自己維護、confirm 過的權威對照表「小雞上工客服自動化／職缺分類與履歷連結對照表」，白紙黑字寫明「蝦皮門市專屬」分類不含「蝦皮外送」，「外送一律歸類服務業」；也列出製造業分類額外涵蓋「電商物流、蝦皮物流、momo理貨、pchome理貨」這幾個原本程式碼沒有的關鍵字。
    - **修正方式**：改成三層判斷、刻意把「外送」相關判斷排在最前面（一律先歸類服務業，不管有沒有同時出現「蝦皮」）；「蝦皮門市專屬」改成只認「蝦皮門市／智取店／店到店／蝦皮店到店／門市理貨」這幾個明確組合字，不再用單獨「蝦皮」兩個字判斷；服務業關鍵字維持既有覆蓋範圍（服務、餐飲、服飾、門市、專櫃、店員、廚助），另外補上對照表新增的「客服」「櫃姐」「櫃哥」「內外場」「門市服務」；都沒命中時維持預設「製造業」（對照表的製造業關鍵字本來就不會被前面兩組誤判命中，不需要另外寫一次判斷）。
    - **新增測試**：`tests/test_flex_service.py` 新增 `ResolveApplyUrlKeyByIndustryTests`（7 個：蝦皮外送→服務業、蝦皮店到店／智取店→蝦皮門市專屬、蝦皮物流→製造業不誤判成蝦皮門市專屬、非蝦皮門市／外送職缺仍正確歸類服務業、一般製造業職缺）。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 631 個測試，OK。
55. **修正：「清除所有條件」沒有真的清空槽位，AI 卻回覆說已經清除了**：使用者實測回報，傳「清除所有條件」後，沛沛回覆「已經為您清除了所有查詢條件」，但緊接著問「我想找台北的工作」（完全沒提到任何類別），卻推薦了跟舊條件（理貨/倉儲）有關的職缺，好像條件根本沒被清掉。
    - **根本原因**：判斷「使用者想全域重置」的 `full_reset_keywords` 是逐字完整比對的清單，收錄「清空條件」「清除條件」等講法，但沒有收錄「清除所有條件」（中間多了「所有」兩個字，比對不到）。訊息因此完全沒走到 `clear_user_slots()`，直接掉到一般 AI 決策流程；AI 只是照著使用者的語氣客氣回話，並不知道背後槽位根本沒被清空，生成了一句聽起來像是清除成功、但其實是空頭支票的回覆。下一輪問「台北」時，Firestore 裡沒被清掉的舊類別條件（理貨/倉儲）還在，AI 依照既有規則（已鎖定條件要持續套用）正確套用了這筆舊資料，才會推薦出跟「台北」完全對得上、但類別上使用者這輪根本沒提過的職缺——不是 AI 憑空幻想，是真的有一筆沒清乾淨的舊資料在作祟。
    - **修正方式**：`handlers/message_handler.py` 在逐字比對的既有清單之外，多加一層寬鬆判斷——只要訊息裡「同時」出現「條件」兩個字，跟「清除／清空／重設／重來／重新／重頭／從頭」這幾個動作詞任一個（不要求緊連在一起），一律視為全域重置意圖，觸發真正的 `clear_user_slots()`。這樣「清除所有條件」「清空全部條件」這類原本沒被逐字清單收錄、但語意明確的講法都能被涵蓋，不用每出現一種新講法就手動加一筆進清單裡（包含 AI 自己生成的快速回覆按鈕文字，同樣不保證會落在固定清單裡）。原本的逐字清單完整保留，純粹是新增一層判斷，不影響既有行為。
    - **新增測試**：`tests/test_message_handler.py` 新增 `FullResetKeywordCoverageTests`（4 個：實測回報的「清除所有條件」現在會真的觸發 `clear_user_slots()`、另一種原本沒收錄的講法「清空全部條件」、既有的「清除條件」仍然正常運作、只提到「條件」沒有動作詞時不會誤判成重置）。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 635 個測試，OK。
56. **接續改善第 55 項：全域重置判斷改成「先反問確認、使用者按下確認才真的清空」，不再靠關鍵字直接清空**：第 55 項上線後，使用者自己抓出這個修正其實「太寬鬆」——因為求職情境裡「條件」常常是指「應徵/錄取條件」（工作門檻要求），不是「沛沛記住的搜尋篩選條件」，兩者意思完全不同，程式沒辦法單靠字面精準分辨。實測驗證：「這份工作的應徵條件是什麼？可以重新說明一下嗎」「薪資條件可以重新談嗎」這類完全無關的問法，都會被誤判成想清空搜尋條件、真的把使用者鎖定的地區/類別/廠商清掉。
    - **使用者提出的解法**：與其一直加關鍵字/排除字（關鍵字永遠列不完），不如疑似重置意圖時先反問使用者「是不是要重新提供找工作的條件」，對方確認了才真的清空——就算判斷誤觸發，代價只是多問一句，不會真的清掉資料。
    - **實作方式**：`handlers/message_handler.py`
        - 新增排除詞組 `_reset_exclude_phrases`（應徵條件、錄取條件、符合條件、門檻條件、任職條件、工作條件），命中就直接排除，不進一步判斷，減少最明顯的誤判（這類詞組講的一定是工作門檻，不會是搜尋條件）。
        - 疑似重置意圖（原本第 55 項的判斷邏輯，含逐字清單跟寬鬆判斷）不再直接呼叫 `clear_user_slots()`，改成回覆「請問您是想清空目前鎖定的所有搜尋條件、重新開始找工作嗎？😊」，並附上「✅ 對，全部清空」「❌ 不是，問別的」兩顆快速回覆按鈕。
        - 新增兩個固定文字常數 `RESET_CONFIRM_TEXT`／`RESET_DECLINE_TEXT`，對應上面兩顆按鈕按下去回傳的文字：按下「對，全部清空」才真的呼叫 `clear_user_slots()`；按下「不是，問別的」則禮貌收尾、不清空任何東西，讓使用者可以接著問原本想問的事。這兩個固定文字刻意不含「條件」這個字，避免使用者剛好也手動打出一樣的句子時，被自己的寬鬆判斷攔下來又要求「再確認一次」，卡成迴圈。
        - 這兩個確認/取消的判斷式都放在疑似重置意圖判斷「之前」檢查，即使某天調整了寬鬆判斷的邏輯，也不會影響這兩個固定文字本身的判斷（精準度完全不受關鍵字調整影響）。
    - **新增測試**：`tests/test_message_handler.py` 的 `FullResetKeywordCoverageTests` 全部改成驗證「先反問確認、不直接清空」，並新增 `test_job_eligibility_condition_question_does_not_ask_for_confirmation`（驗證「應徵條件」類問法完全不受干擾）；新增 `ResetConfirmationExactMatchTests`（驗證按下確認按鈕才真的清空、按下取消不清空也不會再問一次）。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 1870 個測試，OK。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 565 個測試，OK。
57. **修正：AI 把職缺行銷文案的「薪資當日結算」誤判成「日領」，推薦了實際上沒有日領選項的職缺**：使用者實測回報，求職者問「台北日領工作」，沛沛推薦了「蝦皮外送三輪雇傭-大型宅配店」，但這筆職缺 Notion 資料庫「領薪方式」欄位其實是「週領,匯款,月領,現金」，根本沒有日領。
    - **根本原因**：不是關鍵字逐字比對誤判（實際核對過該職缺的「工作內容」「精華亮點」等自由文字欄位，完全沒有出現「日領」這兩個字連在一起的寫法），而是這筆推薦本身是交給 AI（Gemini）自己判斷的。程式碼雖然有把結構化的「領薪方式」欄位老實列給 AI 看（`領薪方式:週領,匯款,月領,現金`），但同時也把「精華亮點」這種行銷文案（這筆職缺寫的是「薪資當日結算」，意思是時薪或件酬用當天紀錄去計算，不代表當天真的撥款）一起丟給 AI，AI 把「當日結算」誤判成「日領」，即使結構化欄位白紙黑字沒有日領，還是判斷成符合。跟之前「地區」判斷會被自由文字誤導是同一種問題模式，但發薪方式當時完全沒有對應的防呆規則。
    - **修正方式（使用者明確要求：發薪方式判斷完全不能有 AI 自行延伸推論的風險，只能依 Notion 欄位資料）**：
      1. `services/matcher_service.py` 新增 `PAY_METHOD_SYNONYMS`（日領/週領/雙週領/月領/年薪/現金/匯款，含常見口語同義詞如「日結」「當日領」）、`detect_pay_method_label()`、`find_pay_method_matched_jobs()`——求職者訊息命中任一發薪方式關鍵字時，直接比對職缺結構化的「領薪方式」欄位，完全不看任何自由文字欄位。跟福利關鍵字攔截（`find_benefit_matched_jobs`）不同的是，這份同義詞清單刻意手動維護、不是動態掃描 Notion 資料庫長出來的——即使系統裡目前完全沒有任何職缺勾選「日領」，求職者問「有沒有日領工作」也要能正確辨識出這是在問日領，進而誠實回覆「目前沒有」，不能因為關鍵字清單裡沒收錄就整句話落到 AI 決策保底流程重蹈覆轍。
      2. `handlers/message_handler.py` 新增「步驟 1-3B：領薪方式關鍵字直達攔截」，排在福利關鍵字攔截之後：命中發薪方式關鍵字且有符合的職缺（可再疊加目前鎖定地區）→ 直接推薦，完全不經過 AI 決策；命中關鍵字但完全沒有職缺符合 → 直接誠實回覆「目前沒有」並附上其他地區快速回覆按鈕，同樣不落到 AI 決策流程。
      3. 額外在 AI 決策提示詞加上第 8 條規則（雙重防禦），明講「發薪方式判斷只能依據『領薪方式:』欄位，絕對不能從『特色:』『待遇:』等行銷文案推論」，並具體舉例說明「當日結算」不等於「日領」——即使日後遇到攔截機制沒覆蓋到的邊界情況（例如發薪方式相關的追問落在對話歷史裡、當輪訊息本身沒有直接命中關鍵字），AI 也有明確規則可循。
    - **驗證**：用實際回報案例的職缺資料（領薪方式：週領,匯款,月領,現金；精華亮點：薪資當日結算）搭配一筆真的有日領選項的對照職缺，實測「台北日領工作」只會比對到真正有日領的職缺，不會再誤判到蝦皮外送三輪雇傭這筆；「不要日領的工作」正確被否定語氣排除、不觸發攔截。
    - **新增測試**：`tests/test_matcher_service.py` 新增 `DetectPayMethodLabelTests`、`FindPayMethodMatchedJobsTests`（含實際回報案例的還原測試：精華亮點寫「當日結算」的職缺，問「日領」時不能被算進去）；`tests/test_message_handler.py` 新增 `PayMethodKeywordDirectInterceptTests`（還原實測案例、命中關鍵字但無符合職缺時誠實回覆、否定語氣排除、無發薪方式關鍵字時正常落到 AI 決策）。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 647 個測試，OK。
58. **使用者把兩週份（09/09～09/22）每日/週報告貼給 Claude 分析，找出兩個問題並修正**：
    - **問題一：延遲已持續惡化到嚴重程度（9/22 單日 p95 高達 747 秒）**：核對兩週報告發現 p95 延遲從 09/09 的 34.6 秒一路惡化到 09/22 的 747 秒，且過去兩週沒有一天低於 12 秒門檻，「⚠️ 超標」警示已經失去警示效果。根本原因研判是 Vertex AI Gemini 併發雪崩效應（見上方壓測相關待辦），治本需要調高 Vertex AI 配額（使用者反映自行嘗試申請超過一小時、求助 Google 客服也沒解決——Gemini 走的是 Dynamic Shared Quota，不一定能透過一般配額申請頁面直接調高，可能需要走 Provisioned Throughput 或另外聯繫 Google Cloud 業務窗口，這部分 Claude 無法代勞）；治標則是從「需求端」降低不必要的 Gemini 呼叫量，即問題二的修正。
    - **問題二：「蝦皮」「理貨/倉儲」「製造/作業員」長期高頻被問，卻完全沒有精準工種直達攔截**：兩週報告的「建議新增的職缺關鍵字」裡，這三個類別/廠商幾乎每天/每週都上榜（理貨/倉儲單週最高 281 次、蝦皮單週最高 195 次），代表這幾百次請求原本都能確定性回答，卻每次都硬要排隊等 Gemini，直接加重問題一的雪崩效應。**修正**：比照既有的外送/門市/momo 直達攔截，在 `handlers/message_handler.py` 新增 `is_warehouse_intent`（理貨/倉儲）、`is_manufacturing_intent`（製造/作業員）、`is_shopee_intent`（蝦皮，純廠商、不含類別關鍵字時）三個直達攔截分支，並同步更新 `services/daily_report_service.py` 的 `DIRECT_INTERCEPT_CATEGORIES`／`DIRECT_INTERCEPT_BRANDS`，避免這幾個之後又被誤判成「缺口」重複建議。優先順序刻意排在既有的外送/門市/momo 之後——「蝦皮門市」這種組合已經由門市分支（含品牌篩選）處理，只有訊息完全沒命中任何類別關鍵字、純問蝦皮時才會落到新的 `is_shopee_intent` 分支，避免互搶。
    - **附帶問題：FAQ 候選清單（每週原樣推播到 LINE 群組的報告）混進了求職者個資與廣告垃圾訊息**：翻閱報告內容時發現候選清單裡有「姓名＋電話號碼」（求職者告知資料方便確認履歷，被誤判成 FAQ 候選問句，個資因此外洩到每天推播的報告裡）、以及詐騙/廣告連結（文旦團購、直播詐騙、旅行社廣告等，因為 AI 判斷不出對應職缺/FAQ 而被一併記錄）。**修正**：`services/notion_service.py` 新增 `_looks_unsuitable_for_faq_candidate()`，過濾含電話號碼（`09\d{2}[-\s]?\d{3}[-\s]?\d{3}`）、含網址、或內容過長（>150字，疑似廣告文案）的訊息，接在 `append_unresolved_faq_to_notion()`（FAQ候選清單，會出現在報告）前面生效。**刻意不影響** `append_unresolved_question_for_followup()`（求職者提問追蹤，招募專員用來回頭找到本人手動回覆的獨立資料庫）——即使內容含姓名/電話，招募專員也必須完整看到才能確認履歷，這條過濾邏輯只擋 FAQ 候選清單這一個寫入路徑。
    - **新增測試**：`tests/test_notion_service.py` 新增 `LooksUnsuitableForFaqCandidateTests`、`AppendUnresolvedFaqFiltersUnsuitableContentTests`（含求職者提問追蹤不受影響的還原測試）；`tests/test_message_handler.py` 新增 `WarehouseManufacturingShopeeDirectInterceptTests`（三個新攔截各自能繞過 AI 直接推薦、蝦皮+門市組合仍正確走門市分支、否定語氣排除）；`tests/test_daily_report_service.py` 新增這三個類別/廠商不再被誤判成缺口的測試。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 2030 個測試，OK。
59. **接續改善第 58 項：蝦皮職缺橫跨多種類型時，改成先反問求職者想看哪一種，不再直接混著顯示**：使用者提出疑慮：蝦皮同時有外送/門市/理貨倉儲等好幾種職缺類型在招，求職者只問「蝦皮有工作嗎」時，第 58 項新增的 `is_shopee_intent` 攔截會把所有類型混在一起顯示前 4 筆，求職者不一定能一眼分辨、也可能漏看真正想找的類型。
    - **設計討論**：使用者一開始問「要不要在類型混雜且筆數超過一張卡片時才反問」，後來提出比照當天稍早「清空所有條件」改成先反問確認的做法（見第 56 項）——與其想辦法精準判斷「算不算混雜」，不如直接固定做成「沒講清楚類型就一律先反問」，邏輯更簡單、也更好維護。討論後拍板：
      1. 反問按鈕的類型選項**動態從 Notion「職務類別」欄位長出來**（跟福利/發薪方式攔截同一種精神），不寫死清單——這樣蝦皮實際在招的類型有變動時（例如某陣子只招外送、沒有門市），系統會自動反映，不用改程式碼。
      2. 但只有「有專屬直達攔截分支」的類型（外送/門市/理貨倉儲/製造作業員）才單獨給一顆按鈕；沒有專屬分支的類型（例如「人資專員」）不單獨給按鈕（避免按下去又繞回同一個反問、卡在無限循環），但仍會被算進保底的「全部類型都看看」按鈕裡，不會完全看不到。
      3. 蝦皮目前如果剛好只有一種（或零種）已知類型在招，不用多問，直接顯示，避免答案明明很明確卻還要求職者多點一次。
    - **實作**：`services/matcher_service.py` 新增 `DIRECT_INTERCEPT_ROUTABLE_CATEGORIES`（目前是外送/門市/理貨倉儲/製造作業員這 4 個，跟已有專屬直達攔截分支的類別清單同步）、`distinct_routable_categories_for_jobs()`（用既有的 `job_matches_category_filter(..., allow_relaxed=False)` 嚴格比對，只信任結構化的「職務類別」／「職缺名稱(對外)」欄位）。`handlers/message_handler.py` 的 `is_shopee_intent` 分支改成：先判斷這輪（已篩過地區的）蝦皮職缺涵蓋幾種已知可路由的類別，≥2 種才送出反問（文字＋依實際類型動態產生的按鈕＋固定加一顆「👀 全部類型都看看」保底按鈕），新增模組常數 `SHOPEE_CLARIFY_ALL_TEXT`（按鈕文字完全由我們自己控制、不是猜使用者打字，跟 `RESET_CONFIRM_TEXT` 同一種精神）——求職者按下這顆保底按鈕時一律直接顯示全部，不再重新判斷要不要問，避免卡在無限循環；求職者點選特定類型按鈕（例如「蝦皮外送」）時，那句話會自然重新命中既有的外送/門市/理貨倉儲/製造作業員分支，不需要另外寫路由邏輯。
    - **驗證**：實測「蝦皮有工作嗎」在蝦皮同時有外送+門市職缺時，正確送出反問（不呼叫 AI、不顯示卡片）；按下「全部類型都看看」正確直接顯示全部（含沒有專屬按鈕的類型）、不會又跳回反問；按下「蝦皮外送」正確只顯示外送職缺；蝦皮只有一種類型在招時正確跳過反問直接顯示。
    - **新增測試**：`tests/test_matcher_service.py` 新增 `DistinctRoutableCategoriesForJobsTests`；`tests/test_message_handler.py` 新增 `ShopeeCategoryClarifyTests`（混合類型觸發反問、單一類型直接顯示、只有不可路由類型時直接顯示、不可路由類型不單獨給按鈕但仍算進全部看看、點選特定類型精準路由、全部看看不會卡在無限循環）。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 2049 個測試，OK。
60. **使用者請 Claude 用模擬對話驗證第 58、59 項的調整，過程中抓到一個真實回歸並修正**：用最新已部署的程式碼模擬了一批貼近真實回報內容的對話（蝦皮多類型混雜、發薪方式、福利、否定語氣、多輪追問等），18 組情境裡有 17 組完全符合預期，抓到 1 個真的問題：
    - **問題**：`job_matches_category_filter()` 的 `brand_label` 參數**只有在 `category_label == "門市"` 時才會真的拿來篩選**（見函式內部的特例判斷），「理貨/倉儲」「製造/作業員」這兩個類別即使傳了 `brand_label` 也完全不會用到、等於沒篩選。第 58 項新增這兩個直達攔截時沿用了這個函式，卻沒發現這個限制，導致求職者問「蝦皮理貨的工作」時，會把其他廠商的理貨/倉儲職缺也一起混進來，答非所問。
    - **修正**：`handlers/message_handler.py` 的 `is_warehouse_intent`／`is_manufacturing_intent` 分支，在 `filter_jobs_by_category_tiered()` 篩完類別+地區後，另外用 `detected_brand` 手動再篩一次（比對 `_search_text` 欄位），不依賴 `job_matches_category_filter()` 的 `brand_label` 參數（該參數對這兩個類別本來就是死的）。沒有指定廠商時（`detected_brand` 為空）維持原本涵蓋所有廠商的行為，不受影響。
    - **附帶發現（非 bug，既有限制，先記錄不處理）**：測試「訊聯理貨的工作」時，因為求職者只打了廠商簡稱「訊聯」（完整廠商名稱是「訊聯生技」），沒有被辨識出來、沒有篩選生效——這是 `detect_brand_label()` 本來就有的設計（要求打出完整或接近完整的廠商名稱，避免簡稱誤判成別的廠商），全部既有的廠商相關攔截（含門市）都是同一套規則，不是這次改動造成的，也不只影響新加的這兩個類別。如果之後同仁反映求職者常用簡稱問廠商、命中率不夠，可以再另外討論。
    - **新增測試**：`tests/test_message_handler.py` 新增 `test_warehouse_intent_with_brand_narrows_to_that_brand_only`、`test_warehouse_intent_without_brand_still_shows_all_vendors`。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 2051 個測試，OK。
61. **重大改版：「廠商/類別 + 福利/發薪方式/休假方式」合併問時，後面的條件不再被忽略——改成六個維度（地區、廠商、類別、福利、發薪方式、休假方式）疊加篩選，找不到就反問求職者要放寬哪一項**：使用者用真實 Notion 資料測試時發現（見第 60 項的後續討論）：問「蝦皮有公司車的工作嗎」，蝦皮橫跨多種類型直接跳去問「您想看哪一種類型」，完全沒理會「公司車」；用簡化資料進一步驗證，「momo有日領的工作嗎」（momo 有兩筆倉別、只有一筆有日領）也會把沒有日領的那筆一起顯示。
    - **根本原因**：原本的攔截設計是「一關一關檢查，第一關（廠商/類別）攔到就直接處理完、回覆，完全不會再檢查第二關（福利）、第三關（發薪方式）」。只要一句話同時符合第一關跟第二/三關的關鍵字，後面的條件形同被吃掉。
    - **設計討論**：先討論「要不要照優先順序放寬其中一項」（版本 A：誠實說沒有＋請換條件；版本 B：照順序放寬一項、老實講清楚），使用者選版本 B，後續加碼問「三項各退讓挑出一個職缺給求職者」的可行性與風險（技術可行，但要清楚講每筆放寬了哪項、且「記住求職者偏好」需要額外的個人化機制，這次不包含），最後拍板採用**最簡單也最一致的做法：直接反問求職者要放寬哪一項**（比照全域重置確認、蝦皮類型反問同一種「真的有歧義就問本人，不要自己猜」的精神），且只列出「這句話真的有講到、且單獨放寬這一項就真的找得到職缺」的選項，不問放寬了也沒用的條件。追加把「休假方式」也納入同一套機制（原本只拿來給 AI 加減分參考，這次升級成確定性攔截，重複使用既有的 `extract_leave_preference()` 分類邏輯）。
    - **實作**：`handlers/message_handler.py` 大幅重構「精準工種直達攔截」這一段：
      1. 六個既有的廠商/類別分支（外送/門市/momo/理貨倉儲/製造作業員/蝦皮）改成只負責「決定候選池」（`_pool`／`_pool_desc`／`_pool_query_phrase`），不再各自立刻篩地區、直接回覆。
      2. 候選池決定後，統一偵測這輪訊息有沒有講到休假方式／福利／發薪方式（`_leave_label`／`_benefit_label`／`_pay_label`），疊加地區一起套用在候選池上（`_apply_secondary_filters()`）。蝦皮類型反問改成只在「沒有講到任何休假/福利/發薪方式」時才會問，因為有講到的話這些條件本身就能幫忙篩出更精準的結果，不需要再多問類型。
      3. 篩完有結果 → 直接推薦；沒有結果但有地區條件、同縣市有替代方案 → 沿用既有的同縣市退讓建議（改成用休假/福利/發薪方式篩過的候選池去查，確保這些條件在退讓建議裡也有生效）；還是沒有、但有講到休假/福利/發薪方式其中之一 → 檢查「單獨放寬某一項會不會找得到」，有的話反問求職者要放寬哪一項（按鈕文字用重組訊息的方式，例如「蝦皮 公司車」，完全由我們自己控制、按下去會自然重新命中同一套邏輯，不需要另外寫路由或存狀態）；放寬任何一項都沒用 → 誠實回覆完全沒有符合的，不落到 AI 決策保底流程。
      4. `services/matcher_service.py` 新增 `find_leave_matched_jobs()`，跟 `find_pay_method_matched_jobs()` 同一種寫法。
    - **驗證**：用真實蝦皮資料重現並確認修好「蝦皮有公司車的工作嗎」（原本答非所問，現在直接推薦有公司車的那筆）；用簡化資料重現並確認修好「momo有日領/有交通車的工作嗎」（原本會混進不符合的那筆，現在只顯示真的符合的）；驗證「蝦皮想要週休二日、有公司車」兩個條件都沒有同時符合時，正確反問要放寬哪一項，點擊放寬按鈕後正確顯示對應結果，且不會卡在無限循環；驗證放寬任何一項都沒用時正確誠實回覆沒有，不落到 AI；驗證否定語氣（「不要公司車的」）仍正確落到 AI，不受影響。
    - **新增測試**：`tests/test_matcher_service.py` 新增 `FindLeaveMatchedJobsTests`；`tests/test_message_handler.py` 新增 `CompoundSecondaryFilterTests`（9 個：廠商+福利/休假方式直接推薦、momo 日領/交通車不再混進不符合的職缺、找不到時反問放寬、放寬後正確路由、完全無法放寬時誠實回覆、否定語氣不受影響）。
    - **全部測試通過**：`python3 -m unittest discover -s tests` 共 2064 個測試，OK。

62. **修正 3 個「候選池沒有依廠商窄化」的真實邏輯問題（用 4 個 agent 分別從蝦皮/外送、製造/作業員、理貨/倉儲、門市/餐飲＋邊界情境四個角度，拉真實 Notion 職缺資料背景測試第 61 項的改版後發現）**：
    - **問題 1：外送類別的候選池完全沒有依廠商窄化**（門市/理貨/製造分支都有，唯獨外送沒有）。實測：「Uber外送的工作」會混進蝦皮的外送職缺；「momo外送的工作」（momo根本沒有外送職缺）也會混進蝦皮/Uber的職缺，完全沒有任何提示這不是使用者指定的廠商。
    - **問題 2：福利關鍵字辨識只看窄化後的候選池，導致條件被整個當成沒說過**。福利關鍵字清單是動態從職缺資料「福利」欄位長出來的（`build_benefit_keyword_index()`），如果候選池先窄化到只剩一兩筆、剛好那幾筆福利欄位是空的，訊息裡真的講到的福利關鍵字（例如「公司車」，實際上只出現在別筆職缺）就會完全辨識不到。實測：「蝦皮門市有沒有公司車的工作」——先窄化成只剩蝦皮門市（福利欄位空的），"公司車" 完全沒被偵測到，直接回蝦皮門市、完全沒理會使用者問的公司車，也不會進入「查無/放寬」的正確流程。
    - **問題 3：只講廠商名稱、沒講類別關鍵字，又同時問休假/福利/發薪方式時，廠商條件會被整個丟掉**。原本六個候選池分支都要靠類別關鍵字（作業員/門市/外送/理貨…）或寫死的品牌（momo/蝦皮）才會建立候選池；求職者只講了廠商名稱（例如「康寧」「惠特科技」，`detect_brand_label()` 認得、但不是這六個分支的觸發詞）時，候選池會整個退回 `active_jobs`，等於廠商條件消失。實測：「惠特科技週領的工作」「康寧有週休二日的工作嗎」都會混進其他廠商的職缺，而且系統還很有把握地直接回覆，不會說查無這家公司資料。
    - **修法**（三個問題同一批修完，都在 `handlers/message_handler.py` 步驟 1a/1b）：
      1. 外送分支比照理貨/倉儲、製造/作業員分支，補上 `detected_brand` 手動窄化（`_search_text` 子字串比對）。
      2. 步驟 1b 偵測休假/福利/發薪方式關鍵字時，改成固定用 `active_jobs`（全部職缺）判斷「這句話有沒有講到」，不再用候選池——候選池窄不窄，只影響「篩出哪些職缺」，不該影響「有沒有偵測到這個詞」這兩件事本來就該分開判斷。
      3. 新增 `_is_bare_brand_pool_intent`：偵測到廠商、六個既有分支都沒建立候選池、但這句話同時有休假/福利/發薪方式其中一項時，改用廠商窄化 `active_jobs` 當候選池（`_pool_desc`/`_pool_query_phrase` 都設成廠商名稱）。刻意只在「同時有次要條件」時才啟用，單純講廠商名稱、沒有其他資訊時（例如只打「康寧」）維持原本會落到 AI 決策的既有行為，不擴大這次修正的範圍。`_resolve_intercept_type()` 補上這個情況的 log 分類（`"brand_only"`）。
      4. 順手把三處 `log_ai_decision_event` 的 `matched_brand` 從 `"momo"/"蝦皮"` 特例判斷改成直接用 `detected_brand`，涵蓋新增的這個分支。
    - **驗證**：`tests/test_message_handler.py` 新增 `BrandPoolNarrowingFixTests`（6 個測試）：外送依廠商窄化、外送廠商零命中時不會混進其他廠商職缺、福利關鍵字即使窄化池子裡沒有也能正確偵測到（改成反問放寬而不是悄悄忽略）、只講廠商名稱+休假條件不會混進其他廠商、廠商+休假條件真的符合時只推薦該廠商、單純講廠商名稱沒有其他資訊時仍維持落到 AI 決策不受影響。全部測試通過：`python3 -m unittest discover -s tests` 共 2070 個測試，OK。
    - 另外同一輪背景測試也發現 2 個非邏輯問題，這次沒有修：（1）問到系統不認得的地區（例如「花蓮」）時會直接忽略地區、不會有任何提示，只回一般清單——屬於既有行為，不是這次改動造成的迴歸；（2）符合條件的職缺超過 4 筆時卡片顯示上限固定只顯示前 4 筆——是既有的設計限制（`[:4]` 顯示上限），不是篩選邏輯的錯。
    - 同一輪測試也再次確認了 `美光(桃園)_Equip`／`美光(桃園)_堆高機`／`美光(桃園)_Porter`／`PChome`／`高瑞實業`／`康寧(世捷)_倉儲` 這 6 筆 Notion 資料的「縣市/行政區」欄位落差（縣市列了 2 個以上、行政區只有其中 1 個，或行政區整個空白），屬於 Notion 資料本身要請同仁手動修正的問題，不是程式邏輯錯誤，已經列清單交給使用者轉交同仁處理。

63. **第 62 項修完後，再用 4 個 agent 同樣的方式重新背景測試一輪（同樣角度分工，改測合併後的 `main`），額外找到 2 個真實邏輯問題 + 1 個顯示瑕疵**：
    - **問題 1：「餐飲/服務」類別完全沒有專屬候選池分支**（外送/門市/理貨倉儲/製造作業員都有，是這次才發現餐飲/服務被漏掉了）。求職者問「類別＋福利/發薪方式/休假方式」合併問、又沒指定廠商時，候選池會整個退回全部職缺，混進完全不相關廠商的職缺。實測案例：「餐飲類的工作有交通車的嗎」——石二鍋（餐飲/服務）沒有交通車，卻推薦了美光的半導體廠作業員職缺（唯一有交通車福利的職缺）。
      - **修法**：`handlers/message_handler.py` 新增 `is_food_service_intent` 判斷跟對應的候選池分支（比照理貨/倉儲、製造/作業員，一樣需要手動補廠商窄化，因為 `job_matches_category_filter()` 的 `brand_label` 只對「門市」生效）；`services/matcher_service.py` 的 `DIRECT_INTERCEPT_ROUTABLE_CATEGORIES` 加入「餐飲/服務」（現在也有專屬分支了，符合這份清單原本的收錄標準），蝦皮類型反問也補上對應 emoji。
      - **附帶發現的另一個既有 bug**：修這個的過程中，既有測試炸開才發現 `category_search_keywords()`（`job_matches_category_filter()` 內部用的另一份獨立關鍵字對照表，跟 `message_handler.py` 判斷使用者意圖用的 `CATEGORY_KEYWORDS`是兩份不同的清單）裡「餐飲/服務」這個類別的關鍵字清單，錯誤地混進了「外送」「店員」——這兩個字明明是外送/門市類別的關鍵字，導致任何外送/門市職缺都會被誤判成同時符合「餐飲/服務」類別篩選。因為之前完全沒有東西會拿「餐飲/服務」去呼叫這個函式，這個錯誤一直是潛伏、沒有實際影響的，這次新增餐飲/服務候選池分支後才會真的被呼叫到、暴露出來，一併修正移除。
    - **問題 2：Uber 這個廠商，只有訊息剛好命中「系統廠商名稱」全名、或該名稱可以用括號/連字號切出短核心名稱時，廠商窄化才會生效**。Notion 上真實的系統廠商名稱是「UBER DRIECT」（同仁把 DIRECT 打成 DRIECT）、「Uber(COSTCO)」、「uber 站所小幫手」三種不同寫法；「Uber(COSTCO)」湊巧因為有括號可以切出「Uber」這個核心名稱、對得上使用者只打的「Uber」，但「UBER DRIECT」沒有括號可切，整串「UBER DRIECT」永遠對不上單純的「Uber」，加上「Uber」原本也沒被收錄進 `KNOWN_BRANDS` 白名單，只要真實資料裡剛好沒有帶括號寫法的 Uber 分店，「Uber外送的工作」的廠商窄化就會完全失效、混進蝦皮的外送職缺。
      - **修法**：`services/matcher_service.py` 的 `KNOWN_BRANDS` 加入 `"Uber": ["uber"]`，求職者打「Uber」就能直接命中所有 Uber 系列的職缺，不再依賴系統廠商名稱剛好能切出短核心名稱。
      - **順手記錄（Notion 資料本身的問題，不是程式錯誤）**：「UBER DRIECT」這個系統廠商名稱本身打錯字了，應該是「UBER DIRECT」，麻煩請同仁到 Notion 修正拼字（不影響這次程式修正的效果，但同仁自己看到时可能會覺得奇怪）。
    - **顯示瑕疵（不影響篩選結果，只是文字重複）**：問「蝦皮門市有沒有公司車的工作」這類訊息時，因為偵測到的廠商名稱剛好命中的是「完整職缺廠商名稱」（某筆職缺系統廠商名稱本身就叫「蝦皮門市」），反問放寬的按鈕重組文字會變成「蝦皮門市門市」這種重複字樣（功能仍正常，點下去還是會正確查詢）。修法：`handlers/message_handler.py` 新增 `_brand_plus_suffix()` 小工具函式，組合廠商名稱跟類別字尾前先檢查字尾是不是已經包含在廠商名稱裡，包含就不重複接一次；套用在外送/門市/理貨倉儲/製造作業員/餐飲服務這五個分支的 `_pool_desc`/`_pool_query_phrase` 組合上。
    - **新增測試**：`tests/test_message_handler.py` 新增 `FoodServicePoolAndUberBrandFixTests`（4 個測試）：餐飲/服務類別+福利不會混進不相關廠商、餐飲/服務類別+福利真的符合時只推薦該廠商、Uber 廠商名稱沒有括號也能正確窄化、蝦皮門市反問放寬按鈕不會出現重複字樣。全部測試通過：`python3 -m unittest discover -s tests` 共 2074 個測試，OK。

64. **重大發現：多輪對話中，鎖定的「廠商/類別」條件會在下一句只問福利/發薪/休假方式時被悄悄丟掉——這是使用者特別要求「用多輪對話測試」才抓到的真實 bug，4 個 agent 各自獨立測到同一個問題**：
    - **具體案例**：求職者先問「蝦皮門市有工作嗎」（鎖定廠商=蝦皮、類別=門市，正確只顯示蝦皮門市），下一句只問「有公司車的嗎」（沒有再提「門市」或「蝦皮」）。地區的鎖定條件（例如先問「桃園的工作」）本來就會正確沿用到下一輪，但廠商/類別的鎖定條件原本完全沒被拿來篩選：
      - 如果廠商也有鎖定（像上面例子），候選池會退回「蝦皮全部類別」，把蝦皮外送（有公司車）也混進來當答案，即使它跟使用者剛剛在問的「蝦皮門市」完全是不同類別。
      - 如果只鎖了類別、沒鎖廠商（例如先問「理貨的工作」），候選池甚至會整個退回全部職缺，混進「製造/作業員」等完全不同類別、不同廠商的職缺——比只漏廠商還嚴重。
      - 真人招募顧問聊到一半換話題問福利，不會突然忘記剛剛在聊哪個廠商、哪個類別；這個 bug 讓沛沛看起來像忘記了上一句話的重點。
    - **根本原因**：`handlers/message_handler.py` 步驟 1a 的六個候選池分支（外送/門市/momo/理貨倉儲/製造作業員/餐飲服務/蝦皮）都只看「這一句話」有沒有類別/品牌關鍵字，第 62 項新增的「只有廠商名稱、沒有類別關鍵字」候選池補救（`_is_bare_brand_pool_intent`）雖然有沿用鎖定的廠商，卻沒有一併沿用鎖定的類別；如果連廠商都沒鎖，這個補救機制完全不會啟動，候選池就直接退回 `active_jobs`。
    - **修法**：把原本的 `_is_bare_brand_pool_intent` 擴大改寫成 `_is_locked_context_pool_intent`——`detected_category_from_text`（步驟 0-3 本來就會在這句話沒提到新類別時自動沿用鎖定值）現在也會被拿來決定候選池：有鎖定類別就用 `filter_jobs_by_category_tiered()` 篩出該類別（再視情況疊加鎖定的廠商），完全沒鎖類別、只鎖了廠商時才退回純廠商篩選。刻意只在「這句話同時偵測到休假/福利/發薪方式其中一項」時才啟用，單純換話題但什麼條件都沒問時（例如只打「康寧」），維持原本會落到 AI 決策的既有行為，不擴大這次修正的範圍。
    - **附帶修正（同一輪 agent 測試發現的相關真實問題）**：`find_leave_matched_jobs()` 原本用 `extract_leave_preference()` 對職缺「休假方式」欄位整串文字只判斷一次，如果同仁在 Notion 把一筆職缺同時填了兩種休假制度（真實案例：康寧的職缺填「做二休二,排休」，代表依班別不同分別適用），因為判斷邏輯是「週休二日→四休二→排休」依序檢查、命中就直接回傳，「做二休二」先命中「四休二」，"排休" 就永遠不會被檢查到，即使欄位裡明明也寫了排休——求職者問「有排休的工作嗎」時，這筆真的有排休的職缺會被漏掉。新增 `_classify_all_leave_labels()`，改成拆開欄位裡的每一段分別判斷（並保留對整串判斷一次當保險），回傳這筆職缺實際涵蓋的「所有」休假制度分類，只要求職者問的那一種有在裡面就算符合。
    - **驗證**：`tests/test_message_handler.py` 新增 `MultiTurnLockedCategoryPersistenceTests`（3 個測試，用真正會保留 session 狀態的多輪測試，不是每輪都重置槽位）：廠商+類別鎖定後第二輪只問休假方式仍正確只推薦鎖定範圍內符合的職缺、廠商+類別鎖定後第二輪只問福利且查無時正確反問放寬而不是混進其他類別的職缺、只鎖類別沒鎖廠商時第二輪仍正確排除其他類別的職缺。`tests/test_matcher_service.py` 新增 2 個測試驗證 `find_leave_matched_jobs()` 能正確辨識職缺欄位同時填兩種休假制度的情況。全部測試通過：`python3 -m unittest discover -s tests` 共 2079 個測試，OK。
    - **這次測試也額外發現、但這次沒有修的兩個次要問題**：（1）求職者明確講「都給我看看」時，如果廠商還鎖定著沒清掉，不會走確定性的「顯示全部」流程，反而會落到 AI 決策——因為系統判斷「有鎖定廠商」等於「有特定意圖」，擋住了 show-all 這條路；日後可以考慮讓「都給我看看」本身也算一種「不限廠商」的表態。（2）「門市」跟「餐飲/服務」兩個類別的關鍵字清單都有「服務」這個字，導致像「佐丹奴正職」（Notion 上職務類別本來就同時標了「門市人員」跟「服務人員」）這種職缺，問「餐飲類的工作」也會被列出來——這其實比較接近 Notion 資料本身就是雙重標籤，不算邏輯錯誤，先記錄不處理。

65. **第三輪多輪對話背景測試（4 個 agent：重新驗證第 64 項、長篇真實求職者對話、反問流程串接、全資料庫自動比對 145 筆招募中職缺／500 多段對話／約 1,600 輪），第一批：明確錯誤的修正**。沒有發現當機或無限循環（每個反問按鈕都點過）。這一批修的是不需要討論設計、確定是錯的地方；需要使用者決定的設計調整（發薪/休假/福利條件要不要跨輪記住、「都可以」要清掉什麼、「都給我看看」的範圍、做四休二跟做二休二要不要分開）使用者已經回覆，放在下一批（第 66 項）；地區比對太粗（台北市中山區被當成整個台北、嘉義縣混到嘉義市）放在第三批。
    - **廠商被記成帶類別字的完整名稱**：求職者點「蝦皮外送」「蝦皮門市」按鈕，`detect_brand_label()` 先比對到系統廠商名稱「蝦皮外送(支援)」「蝦皮門市」，廠商槽位被鎖成「蝦皮外送」「蝦皮門市」，下一句「那理貨呢」就篩不到任何職缺、落到 AI。而且結果取決於 Notion 職缺的排列順序。修法：`detect_brand_label()` 先認 `KNOWN_BRANDS` 品牌家族（蝦皮/momo/美光…），類別交給類別槽位管；其他廠商同時命中好幾個名稱時取比對到最長的那個，不再取列表裡剛好排前面的。
    - **換廠商時沒放掉上一輪的類別**：「蝦皮門市有工作嗎」→「美光有交通車嗎」原本拿美光＋門市去篩，篩空了誤答美光沒有交通車。修法（`message_handler.py` 步驟 0-3）：換了廠商、這句話又沒提到類別時，只有新廠商真的有這個類別的職缺才沿用（「蝦皮外送」→「那Uber有週領的嗎」維持外送，因為 Uber 也有外送職缺）。
    - **「蝦皮全部類型都看看」沒清掉鎖定的類別**：下一句問福利時只在舊類別裡找、誤答沒有。修法：這個按鈕文字當成「類型不限」清空類別槽位。
    - **蝦皮類型反問的問題**：（1）多出一個點下去只看得到「蝦皮內勤（設備人員）」的「蝦皮製造/作業員」選項——`category_search_keywords()` 的製造/作業員清單混了「設備」「包裝」（「電商物流理貨包裝員」也會被算成作業員），移除；（2）沒看鎖定的地區，列出該地區根本沒有的類型——改成只列鎖定地區真的有的類型，只剩一種就直接顯示不問，完全沒有就交給同縣市退讓建議，反問文字也會講出地區。
    - **「服務」兩個字誤判類別**：「作業員的工作」→「有交通車接送服務嗎」把類別換成餐飲/服務。`CATEGORY_KEYWORDS` 跟 `category_search_keywords()` 的餐飲/服務都改成不收單獨的「服務」（改收服務員/服務生/服務人員/服務業/服務類/餐廳），後者也避免「行業別＝服務業」的門市職缺被誤判成餐飲。
    - **發薪方式**：「雙週領」被當成「週領」（字典順序先命中子字串）——改成同義詞由長到短比對，職缺欄位也改成逐一比對選項、不用子字串；補上 Notion 真實存在但沒收錄的「街口」「預支」（原本問到會落到 AI，違反「發薪方式一律不交給 AI」的原則）。
    - **認不出 PChome**（系統廠商名稱「PChome理貨」沒有括號可切）：`KNOWN_BRANDS` 加入 PChome；Coupang 加入 Notion 上真實存在的錯字「coupung」。
    - **「那還有別家的嗎」不會放掉原本的廠商**，後面一直誤答沒有：`explicit_any_brand` 補上 別家/其他家/別間/別的公司/其他公司；「不要蝦皮了」這種明確排除鎖定品牌的說法也會清掉廠商槽位（新增 `detect_negated_brand()`）。
    - **否定詞波及下一個子句**：「不要蝦皮了 高雄有什麼」把高雄當成被排除、清掉地區。`_keyword_is_negated()` 的否定範圍改成只管同一個子句（遇到標點、空白、「了」就斷開，但不切開「除了」）。
    - **系統不認得的縣市**：花蓮/台東/南投/雲林/澎湖/金門/馬祖（連江）原本不在 `LOCATION_CANDIDATES`，「花蓮有週休二日的工作嗎」抓不到地區，直接回「有的！」推薦台北/新北的職缺。全部補上。
    - **廠商名稱剛好是地名**：廠商「新興(代招)」在新北五股，「新興」也是高雄市的行政區——「新興有匯款的嗎」原本把地區換成高雄新興區，廠商篩選又用含地址的 `_search_text` 比對，推薦出高雄的其他廠商。修法：這句話偵測到的地名如果就是這句話偵測到的廠商名稱的一部分，不當成新地區；廠商篩選改用新增的 `job_matches_brand()`，只比對系統廠商名稱/職缺名稱欄位，不比對含地區跟行銷文案的 `_search_text`（全部候選池分支都改用這個）。
    - **查無職缺的回覆怪錯條件**：候選池本身是空的（例如「康寧外送有週領的嗎」，康寧根本沒有外送職缺），或地區根本沒有職缺時，原本一律說成「沒有符合發薪方式：週領」。改成講出真正卡住的是候選池、地區還是次要條件；同縣市退讓建議的文字也會一併講出次要條件（原本「楊梅蝦皮有公司車的嗎」回「楊梅目前沒有明確列出的蝦皮職缺」，但楊梅其實有蝦皮職缺，缺的是公司車）。
    - **職務類別關鍵字補齊**：製造/作業員補上品保/品管/檢驗，理貨/倉儲補上搬運（Notion 上真實存在的職務類別「搬運工」「品保人員」「檢驗人員」原本歸不到任何類別）。
    - **新增測試**：`tests/test_matcher_service.py::MultiTurnRoundThreeMatcherFixTests`（13 個）、`tests/test_message_handler.py::MultiTurnRoundThreeHandlerFixTests`（10 個，用會保留 session 狀態的多輪測試）。確認其中 20 個在修正前的程式碼上會失敗（另外 3 個是確保原本正確的行為不被改壞）。全部測試通過：`python3 -m unittest discover -s tests` 共 2102 個測試，OK。

66. **第三輪多輪對話測試，第二批：使用者決定的四項設計調整**（2026-09-23，使用者看完第 65 項的測試報告後四題都選了建議方案）：
    1. **休假/發薪/福利條件跟地區一樣記住到求職者改口為止**。原本只看當句，「桃園有日領的嗎」→「有交通車的嗎」第二句就不再篩日領——全資料庫自動比對測到這是出錯最多的一項（116 次）。
       - `services/session_service.py` 的槽位新增 `pay`、`benefit`（`leave` 原本就有、但只拿來給 AI 加減分，從來沒拿來篩選）。
       - `handlers/message_handler.py` 步驟 0-3 算出「目前生效」的條件（這句話講的，或上一輪記住的），篩選一律用新增的 `filter_jobs_by_leave_label()`／`filter_jobs_by_pay_label()`／`filter_jobs_by_benefit_label()`（依標籤篩，不再從這句話重新判斷）。
       - **什麼時候清掉**：求職者換成另一個值（覆蓋）、講「休假方式都可以」這種指明那一項的放寬、否定目前記住的值（「不要日領的」）、或全域重置。
       - **刻意的限制**：要不要進入直達篩選，仍然只看「這句話本身」有沒有講到休假/福利/發薪方式（或放寬其中一項）——不然記住日領之後，連「薪水怎麼算」這種 FAQ 問題都會被攔下來改推職缺。但只要有進入直達篩選（例如這句話講了類別或地區），記住的條件就會一起生效；記住的條件讓結果篩到 0 筆時，一樣會反問要放寬哪一項。
       - **放寬按鈕改寫法**：條件會跨輪記住後，按鈕文字不能只是「不提」放寬的那一項（記住的值還在，按下去會得到一模一樣的反問），改成明講「休假方式都可以」「福利都可以」「發薪方式都可以」；保底按鈕改成「其他條件都可以」（三項一起放寬），只有一項條件時不重複列。
       - AI 決策保底流程的【求職者目前鎖定的條件】也會列出記住的休假/發薪/福利條件。
    2. **「都可以」只清句子裡提到的那一項**（新增 `detect_scoped_broaden_dimensions()`）：「班別都可以」只清班別、「休假方式都可以」只清休假方式、「不限地區」只清地區。只說「都可以」、這句話也沒講到任何其他條件時，只清類型跟廠商、保留地區。這句話本身有講到地區/類別/廠商/班別/休假/發薪/福利時（例如「桃園 日領 什麼都可以做」「週休或排休都行」），不當成泛用放寬。原本三個維度一律一起清：「我在三重找工作」→「都可以」會變成推全台職缺，「班別都可以啦」會連類型都清掉。
    3. **已鎖定條件時講「都給我看看」，在鎖定的範圍內全部列出**：`has_specific_intent` 改成只看「這句話本身」有沒有指定廠商/類別，上一輪鎖定的不再擋住「都給我看看」（原本一律落到 AI）；列出時套用鎖定的地區、類別、廠商，以及記住的休假/發薪/福利條件。範圍內真的沒有職缺時老實講出是哪些條件，附「清空條件重新找」（跟「其他條件都可以」）按鈕，不再像原本一樣悄悄改推全台前 5 筆。同一句話同時講了條件（「我住桃園 想找日領的工作 什麼都可以做」）也會套用那些條件，原本會直接列出全部職缺、沒篩日領。
    4. **做四休二跟做二休二分開**：`extract_leave_preference()` 原本把兩者都歸成「四休二」，問「做四休二」會推薦做二休二的美光職缺。改成兩個標籤「做四休二」「做二休二」（「四班二輪」歸做二休二）。
    - **新增／更新測試**：`tests/test_message_handler.py::MultiTurnUserDesignDecisionTests`（10 個多輪測試，其中包含「每個放寬按鈕按下去都能解開、不會又得到同一個反問」）；確認其中 6 個在第一批的程式碼上會失敗。另外有 5 個既有測試寫的是使用者這次決定改掉的舊行為（「都可以」連地區一起清、做四休二＝做二休二、放寬按鈕文字），已照新決定更新。全部測試通過：`python3 -m unittest discover -s tests` 共 2148 個測試，OK。

67. **第三輪多輪對話測試，第三批：地區比對太粗**（全資料庫自動比對測到約 135 次推薦了不在求職者指定地區的職缺）。都在 `services/matcher_service.py::extract_current_target_location()`：
    - **同名的區在好幾個縣市都有**（台北市、基隆市都有中山區；台中、台南、新竹、嘉義都有東區）：原本一律跳過不猜，「台北市中山區有內場的工作嗎」就退回成「整個台北」。改成這句話本身有講是哪個縣市時，組成「台北市中山區」這種完整寫法精準比對（`_qualify_ambiguous_district()`）；沒講縣市時維持不猜。
    - **「桃園區」被當成整個桃園市**：「桃園」同時是縣市名跟區名，求職者講「桃園區」時改成只比對桃園市桃園區。
    - **新竹縣／新竹市、嘉義縣／嘉義市沒分開**：原本「嘉義縣」會把嘉義市的職缺一起列出。求職者有講「縣」或「市」時分開比對，只講「嘉義」時維持兩邊都算。
    - `resolve_county_for_location()` 也認得「台北市中山區」「嘉義縣」這種本身帶完整縣市名稱的地點，同縣市退讓建議才查得到縣市。
    - **新增測試**：`tests/test_matcher_service.py::LocationGranularityTests`（5 個）、`tests/test_message_handler.py::LocationGranularityHandlerTests`（3 個），其中 7 個在修正前的程式碼上會失敗。全部測試通過：`python3 -m unittest discover -s tests` 共 2156 個測試，OK。
    - **沒有修、記錄下來的已知限制**：職缺的「縣市」欄位列了好幾個縣市、「行政區」又只寫「中山區」沒寫縣市前綴時，沒辦法判斷是哪個縣市的中山區，精準比對會漏掉這筆（Notion 資料照「台北市中山區」完整寫法填就不會有這個問題）。

68. **第四輪多輪對話測試，第一批：聽懂求職者的意思**（2026-09-23）。這一輪使用者定了一個原則：**「只要不確定的就跳出選項給求職者選擇，選擇權在求職者手上」**——程式碼不再自己猜，分不出來就給按鈕。按鈕送回來的都是固定句型，保證下一輪會被判斷成確定的意思，不會又問一次。
    1. **問規定還是找工作**（新增 `matcher_service.classify_condition_utterance()`，回傳 `info`／`demand`／`question`）：「可以預支薪水嗎」「週領是禮拜幾發」「交通車有哪些站點」原本會被記成篩選條件、直接回職缺卡片。現在這句話**只**講到班別/休假/發薪/福利、又像在問問題時（有「怎麼」「哪些」「禮拜幾」「規定」這類詞，或句尾是「嗎」但不是「有…嗎」），先問「您是想了解「週領」的相關規定，還是想找有「週領」的職缺呢？」，按鈕是 `想了解週領的規定`（開頭 `INFO_INTENT_PREFIX`＝「想了解」，不記條件、交給 FAQ/AI 回答）跟 `有週領的工作嗎`（照常篩選）。有講「工作」「職缺」或「有…嗎」「就好」「想找」就當成在找工作，不問。
    2. **放寬說法**（新增 `detect_relax_dimensions()`）：「不一定要週休」「日領沒有就算了」「不需要交通車」原本會被當成又講了一次那個條件、一字不差地重問。現在這一項這輪不算條件；如果那一項有記住的值，問「要把「休假方式：週休二日」這個條件拿掉嗎？」，按鈕 `休假方式都可以`（拿掉）跟 `保留目前條件`（`KEEP_CONDITIONS_TEXT`，照目前條件列出職缺）。
    3. **否定詞只管自己那個子句**（新增 `_find_label_mentions()`）：原本整句話只要有「不要」，這句話講的休假/發薪/福利全部不算——「不要夜班了，日領的就好」連日領都丟掉。現在每個詞各自看前面同一個子句有沒有否定詞（先依標點切子句再清理文字，不然逗號被清掉後「日領」前面會碰到「不要」）。否定的是記住的值時只拿掉那一個（「日領|週領」講「不要週領」剩日領），而且這句話會照剩下的條件重新列職缺；第一次就講「不要公司車」這種排除需求，直達篩選做不到，維持交給 AI。
    4. **一句話講好幾個值**：「日領或週領都可以」原本只抓第一個。現在存成 `日領|週領`（符合其中一個就算），講「週領也可以」「早班也行」會跟記住的值合併。回覆文字把 `|` 寫成「或」。
    5. **班別變成篩選條件**（使用者這一輪決定）：原本班別只給 AI 加減分。新增 `filter_jobs_by_shift_label()`，職缺端用「班別」欄位（依逗號切開各自分類，`夜班(打烊班)` 也拆得開）加上「全/兼職」欄位是兼職時算「兼職/工讀」。放寬反問也會列「班別都可以」。同時修正「假日班」被當成早班（裡面有「日班」兩個字）：同一段文字被較長的關鍵字涵蓋時只算較長的那個，「雙週領」也不會同時算成週領、「四三輪休」不會同時算成排休。AI 排序的班別加分也改用同一套分類。
    6. **班別/休假新說法**：班別補「全日班」「打烊班」「三班輪」「早晚輪班」；休假補「做三休三」「四三輪休」「休日一」「自由報班」「做兩休兩」「4天休2天」；拿掉「休假日」（「你們休假日也要上班嗎」是在問問題）。休假分類集中在 `LEAVE_BUCKETS`。「條件都不限」「不限條件」清掉班別/休假/發薪/福利。
    7. **類型關鍵字**：「倉庫」算理貨/倉儲；「服飾」「專櫃」從餐飲/服務移到門市（問服飾店原本會推餐廳內場）。**職缺有填「職務類別」時嚴格比對只看這個欄位**，沒填才看對外職缺名稱——原本對外名稱寫「電商物流理貨包裝」的作業員職缺會被當成理貨。
    8. **「都給我看看」先篩類型再篩地區**（跟步驟 1c 的候選池一致），不然某地區剛好沒有嚴格符合類型的職缺時，會退回寬鬆比對混進只是工作說明提到那個字的職缺。
    9. **回覆講出用了哪些條件**：「有的！沛沛為您找到符合「桃園・理貨/倉儲・發薪方式：日領或週領」的推薦職缺囉」。條件會跨輪記住，求職者不一定記得上一輪講過什麼。
    - **新增測試**：`tests/test_matcher_service.py::RoundFourUnderstandingMatcherTests`（10 個）、`tests/test_message_handler.py::MultiTurnRoundFourUnderstandingTests`（9 個多輪測試）。全部測試通過：`python3 -m unittest discover -s tests` 共 2181 個測試，OK。
    - **下一批（第 69 項）**：查無結果時的按鈕、放寬也能選地區/類型/廠商、蝦皮反問沿用記住的條件、「我想換地區/班別/工作類型」「我要應徵」按鈕、不知道地區時先問地區、只講地區時直接列出。

69. **第四輪多輪對話測試，第二批：對話流程與按鈕迴圈**（2026-09-23，同樣依「不確定就讓求職者選」原則）：
    1. **不知道地區、職缺又分散在好幾個縣市時先問地區**（使用者這一輪決定）。步驟 1c 找到的職缺超過一次顯示的張數（`_CARD_LIMIT`＝4）、又分布在兩個以上縣市時，先回「符合「理貨/倉儲」的職缺分布在好幾個縣市，請問您想在哪個地區工作呢？」，按鈕是有職缺的縣市（依職缺數排序，最多 10 個，文字 `桃園市的工作`）加上 `地區都可以`。結果少到一次看得完時不問。「都給我看看」是明講要看全部，不問。
       - **「哪裡都可以」要記住**：地區槽位改存 `ANY_LOCATION`（＝「不限」），跟「還沒講過地區」分開，之後就不會再問地區；篩選時當成不限地區。「不要桃園」否定鎖定的地區仍然是清空。
    2. **只講地區直接列出該地區職缺**（使用者這一輪決定，原本「桃園」「中壢有缺嗎」落到 AI）：`is_location_only_turn`。類型很雜（兩種以上、超過 4 筆）時先問「桃園目前有外送、門市、理貨/倉儲…這幾種職缺，請問您想看哪一種呢？」，按鈕 `理貨/倉儲的工作` 等加上 `類型都可以`。
    3. **「地區／類型／廠商都可以」會重新列職缺**：原本這三句（放寬按鈕送回來的就是這種）不會進步驟 1c，只剩地區時落到 AI。新增 `_condition_turn` 決定要不要進步驟 1c。
    4. **放寬反問也能選地區、類型、廠商**：原本只問班別/休假/發薪/福利，候選池本身是空的（「康寧外送」）或地區沒有職缺時，落到 AI 或回固定的「新莊/桃園」按鈕。現在每一項條件都會檢查「只拿掉這一項找不找得到」，找得到的才列成按鈕（`地區都可以`、`類型都可以`、`廠商都可以`…）。回覆先講真正卡住的地方（「目前沒有康寧外送的職缺」）。
    5. **完全放寬不出來時**：回覆列出目前全部條件，按鈕是每一項條件的「X都可以」加上「清空條件重新找」，不再是按了條件還在、又回到同一句的「新莊/桃園」固定按鈕。
    6. **蝦皮反問沿用記住的條件**：已經鎖定類型（「桃園門市的工作」→「那蝦皮呢」）時直接用那個類型，不再問「想看哪一種」；列類型選項時也套用記住的班別/休假/發薪/福利條件，不會列出點了才發現都不符合的類型。
    7. **「我想換地區／班別／工作類型」「我要應徵」按鈕**（原本都落到 AI）：換條件時列出「其他條件不變、換成這個真的有職缺」的選項加上「X都可以」；「我要應徵」從最近一次職缺詳情回覆裡找履歷連結直接給，找不到就請求職者先點職缺卡片。
    8. **AI 候選職缺先用記住的條件篩**（`build_ai_job_candidates()`）：原本記住的班別/休假/發薪/福利只拿來加減分，AI 還是會推不符合的職缺。某一項篩完是空的就不篩那一項，留給 AI 判斷怎麼退讓。
    - 新增共用的 `_search_jobs(skip)`（全部條件一起篩，可以跳過其中一項），「都給我看看」跟換條件選項都用它。
    - **新增／更新測試（第 69 項）**：`tests/test_message_handler.py::MultiTurnRoundFourFlowTests`（11 個多輪測試，包含「查無結果的每一顆按鈕按下去都不會回到同一句」）。7 個既有測試寫的是「查無結果時落到 AI」「只講地區落到 AI」的舊行為，已照使用者這一輪的決定更新（仍然檢查不會推不相關的職缺）。全部測試通過：`python3 -m unittest discover -s tests` 共 2192 個測試，OK。

70. **第四輪多輪對話測試，第三批：地區比對更精準**（`services/matcher_service.py`）：
    1. **完整地址用行政區、不用縣市**：「桃園市八德區」原本照 `LOCATION_CANDIDATES` 的順序先命中「桃園」、變成整個桃園市；「宜蘭縣礁溪鄉」先命中職缺資料裡「宜蘭市」的核心字「宜蘭」。`extract_current_target_location()` 改成把句子裡所有行政區層級的地名（手動清單＋職缺資料動態索引）都找出來再挑：跟縣市同名的（桃園、宜蘭、苗栗…）有更精確的地名時不算、被較長地名包住的不算，其餘挑**最早出現**的（「台南市安南區」是安南，不是南區）。
    2. **動態索引改存縣市全名**（新增 `build_district_county_full_index()`）：原本只存核心字「新竹」，竹北（新竹縣）還原成全名時變成新竹市，同縣市退讓建議因此推了新竹市的職缺。`resolve_county_for_location()` 改用這份索引。舊的 `build_district_county_index()` 保留（回傳核心字）。
    3. **結構化的地區比對**（新增 `job_matches_location()`，handler 的 `_filter_by_location()` 全部改用它）：先照原本的字串比對，「台北市中山區」這種帶縣市的寫法再用「縣市」「行政區」欄位比一次——職缺的行政區只寫「中山區」、縣市又列了好幾個時，原本字串比對會漏掉（第 67 項記錄的已知限制，這次修掉）。
    4. **好幾個縣市都有的區名**：句子沒講縣市時，改用上一輪記住的地區所在縣市（先問「台北」再問「中山區呢」→ 台北市中山區）；還是分不出來就問「台北市中山區、基隆市中山區都有職缺，請問您說的是哪一個呢？」（`ambiguous_district_choices()`，只在區名後面真的接著「區/鄉/鎮」時才問，「中山路」不算）。
    5. **廠商名稱剛好也是地名**（廠商「新興(代招)」vs 高雄市新興區）：原本一律當成廠商。現在句子有「新興區」→ 地區；有「廠商」「公司」→ 廠商；已經鎖定地區、這家廠商在那裡就有職缺 → 廠商（第 65 項的「新北」→「新興有匯款的嗎」維持）；都不是就問「您說的「新興」是指高雄市新興區這個地區，還是「新興」這家廠商呢？」。廠商按鈕的文字 `新興這家廠商的工作`（`BRAND_CHOICE_SUFFIX`）會直接列出那家廠商的職缺；求職者自己只打廠商名稱時維持落到 AI（使用者沒有決定要改）。
    6. **「服務人員」只在餐飲業才算餐飲/服務**（`job_matches_category_filter()`）：用按鈕爬蟲把第四輪全部按鈕點過一遍（約 2,400 輪）時測到，問「內場」會推佐丹奴（服飾業）、微風服務台、蝦皮客服，因為這幾筆的職務類別填了很泛的「服務人員」。行業別不是餐飲業時，「服務人員」不算進餐飲/服務（嚴格跟寬鬆比對都一樣）。
    - **按鈕爬蟲結果（修正前 → 修正後）**：反覆繞圈 14 → 2（剩下的是看完職缺詳情再按「都給我看看」，屬正常）、按了又回到同一句 8 → 0、按鈕落到 AI 10 → 1（只剩「發薪日是什麼時候？」，本來就該交給 FAQ/AI）。爬蟲回報的「推了不符合條件的職缺」大多是爬蟲自己的判斷標準還沒跟上這一輪的新規則（「不限」地區、「全/兼職」欄位算兼職、三班輪算輪班），逐項看過不是錯誤；真的有問題的就是上面這一項。
    - **新增測試**：`tests/test_matcher_service.py::RoundFourLocationPrecisionTests`（6 個）、`tests/test_message_handler.py::MultiTurnRoundFourLocationTests`（5 個多輪測試）。全部測試通過：`python3 -m unittest discover -s tests` 共 2203 個測試，OK。

71. **第五輪多輪對話測試（4 個 agent：12 位模擬求職者長對話、按鈕爬蟲約 9 萬輪、145 筆職缺每筆都測到共 6,266 段對話、刁鑽說法），第一批：聽懂求職者的意思**（2026-09-23）。沒有當機、沒有只靠按鈕就繞不出來的迴圈。這一批修的是「聽錯話」：
    1. **句尾加「謝謝」整句被丟掉**：「想找桃園理貨的工作，謝謝」原本只回「不客氣」。步驟 0-0B 的單純道謝判斷改成句子裡有找工作的內容（地區/類型/廠商/班別等，或「工作」「找」這類字）時不算。
    2. **更多否定說法**（`NEGATION_TRIGGERS`）：「沒有夜班的工作」「我不能上夜班」「不接受夜班」「拒絕夜班」「不用上夜班」原本反而被當成要夜班。也支援否定詞放在後面：「夜班不行」「夜班就不要了」「蝦皮以外的」（`_POSTFIX_NEGATION_RE`）。長得像否定詞的「有沒有」「能不能」「非常」「沒有經驗」先拿掉再判斷（`_BENIGN_NEGATION_LOOKALIKES`）；「非日領不可」是雙重否定、算要日領。判斷邏輯集中在 `_negation_at()`。
    3. **沒有標點時否定詞不越界**：「不要夜班日領就好」原本連日領都算成不要。否定詞跟這個詞中間夾著另一個條件詞時，中間是「跟/和/或」列舉才一起否定，這個詞後面接著「就好/可以」時是新的要求（`_span_is_negated()`）。類型、廠商也改成依子句判斷（新增 `clause_clean_text()`）：「不要外送，理貨呢」原本連理貨都當成不要。
    4. **「不要蝦皮」反而鎖定蝦皮**：`detect_brand_label()` 比對系統廠商名稱時沒檢查否定詞。現在被否定的廠商不算，`detect_negated_brand()` 也認得一般廠商（「不要美光的」）。
    5. **班別、路名被當成廠商**：有一筆職缺的廠商欄位填成「M打烊班」，「我想找打烊班」原本被當成廠商、還一路記住；「我住文華路附近」被當成廠商「文華」。現在自然語言抽出的詞含班別/休假/發薪用語時不算廠商，廠商名稱後面接「路/街/大道/巷/段」時是路名。
    6. **廠商別名**：shopee、優步、台哥大、微風；「台積電」原本永遠比對不到（同義詞沒做台→臺轉換）。
    7. **問問題還是找工作**：沛沛問這個問題時原本會偷偷清掉記住的類型跟廠商（這句話講到的條件被清空後，被當成單純的「都可以」）。現在這種時候這句話講到的條件一律先不記、記住的也不動。講到類型/廠商的問題（「倉儲會很累嗎」「外送要自己準備機車嗎」）原本直接丟職缺卡片，現在一樣先問「了解工作內容」還是「找職缺」。問句標記補「週幾」「會很」「證照」「做什麼」等；「早班還是晚班都可以」算在講條件，不問。
    8. **只回一兩個字跳出不相干的 FAQ**：「要」原本命中「面試要帶什麼」。`find_high_confidence_faq_match()` 改成求職者的話太短時不算「被 FAQ 問題包住」。
    9. **新說法**：班別補白天班、早上的班、晚上、半夜、週末、六日的班；類型補品保、品檢、檢驗、搬運（原本只有職缺端認得）；「也沒關係」「也沒差」「也能接受」算追加（「排休也沒關係」原本把週休換掉）；「什麼班都可以」「地方都可以」「哪家都可以」「縣市不限」清對的那一項。
    10. **放寬只放寬講到的那個值**（新增 `detect_relax_labels()`）：「我不需要日領，月領就好」原本連月領都丟掉；記住「日領|週領」時講「不一定要日領」，按「拿掉」只拿掉日領（按鈕文字 `不要日領了`）；放寬的是根本沒記住的條件時不用問，直接照目前條件重新列（原本落到 AI）。
    - **新增測試**：`tests/test_matcher_service.py::RoundFiveUnderstandingMatcherTests`（12 個）、`tests/test_message_handler.py::MultiTurnRoundFiveUnderstandingTests`（12 個多輪測試）。測試輔助的 `_RoundFourSessionMixin` 可以用 `self.faqs` 指定 FAQ。全部測試通過：`python3 -m unittest discover -s tests` 共 2227 個測試，OK。
    - **使用者這一輪的決定（第二、三批處理）**：第一次就講「不要夜班」「除了外送」要真的排除；只打廠商名稱直接列出那家的職缺；一次講兩個地區/類型兩個都算；新增「客服/行政」「設備/技術」兩個類型。

72. **第五輪多輪對話測試，第二批：地名**（`services/matcher_service.py::extract_current_target_location()` 大改）：
    1. **求職者講的縣市被忽略**：「台中市大安區」原本推台北大安區（資料裡的大安區只有台北市）。區名前面緊接著講了別的縣市時，照求職者講的組完整寫法（「台中市大安區」），找不到就老實說沒有。
    2. **區名跨在縣市名上**：「台中西屯」原本變成台南「中西」區、「新竹北區」變成竹北、「新竹東區」變成竹東。跟句子裡縣市名稱部分重疊的區名不算（新增 `_county_mentions()`）；緊接著區名的縣市名只是在修飾那個區。
    3. **縣轄市**：「苗栗市」「宜蘭市」「彰化市」原本變成整個縣（新增 `_COUNTY_SEAT_CITIES`，組成「苗栗縣苗栗市」精準比對）。
    4. **區名用資料裡真正的名稱**（新增 `_district_full_names()`）：原本「大同鄉」的選項出現「台北市大同鄉」這種不存在的地名。現在講「大同鄉」就只剩宜蘭縣、不用問；「大同區」就是台北市。
    5. **「住在X想去Y上班」**：原本取第一個地名 X。前面有「住」的地名，句子裡還有其他地名時不算。
    6. **兩個地區都算**（使用者這一輪決定）：「桃園或新竹都可以」「中壢跟八德」存成 `桃園|新竹`，符合其中一個就算（`job_matches_location()`、卡片顯示 `format_clean_location()`、AI 排序都支援）；「平鎮也可以啦」跟記住的地區合併。回覆寫成「桃園或新竹」（`current_location_text`）。
    7. **否定記住的完整地名**：記住「台北市中山區」時講「不要中山區」原本比不到、清不掉（新增 `location_is_negated()`）。
    8. **「中山區是台北還是基隆」只列在目前其他條件下有職缺的選項**：原本記住「理貨」再問，兩個選項點下去都是沒有。全部都沒有時兩個都列（按了會老實說沒有）。為此 `_search_jobs()` 挪到步驟 0-3b 前面定義。
    9. **放寬按鈕改成固定句型**（按鈕爬蟲測到）：原本把廠商/地區/其他條件重新組進按鈕文字（「做三休三 發薪方式都可以」），去掉空白後黏出別的詞——黏出廠商「三發」、「新興 發薪方式都可以」又問一次新興是地區還是廠商。條件都記在槽位裡，按鈕只要講「發薪方式都可以」「其他條件都可以」。`_pool_query_phrase` 因此拿掉。
    - **已知限制（資料問題）**：職缺「行政區」只寫「大安區」、「縣市」又同時列台北市跟台中市時（鼎王、佐丹奴），沒辦法知道是哪個縣市的大安區，「台中市大安區」會比對到這幾筆。請同仁一律寫「縣市＋區」。
    - **新增／更新測試**：`tests/test_matcher_service.py::RoundFiveLocationMatcherTests`（7 個）、`tests/test_message_handler.py::MultiTurnRoundFiveLocationTests`（5 個多輪測試）；2 個既有測試照這一輪的決定更新（「中壢或八德」兩個都算、放寬按鈕文字）。全部測試通過：`python3 -m unittest discover -s tests` 共 2239 個測試，OK。

73. **第五輪多輪對話測試，第三批：對話流程＋使用者這一輪的四個決定**（2026-09-23）：
    1. **排除條件真的幫忙排除**（使用者決定）：「不要夜班」「除了外送都可以」「不要蝦皮」「不要公司車」原本第一次講就交給 AI。新增槽位 `exclude`（`services/session_service.py`，格式 `shift:大夜班;category:外送`，handler 的 `_parse_exclusions()`／`_format_exclusions()`），跟其他條件一樣記住到求職者改口為止；篩選用 `matcher_service.job_is_excluded()`，**職缺本身還有其他選項時不排除**（「不要夜班」不排掉早班夜班都有的職缺，「不要中壢」不排掉中壢八德都有的職缺，「不要日領」不排掉日領月領都有的職缺）。講到被排除的值（「外送的工作」）就不再排除；「班別都可以」連班別的排除一起清；「排除的條件都可以」全部清掉。回覆文字寫「排除：大夜班」，AI 提示詞也列出「不要=…」。否定的內容都記成排除條件時，整句話的否定語氣不再擋掉直達篩選：「不要外送了 改門市」「不要台南了，桃園有嗎」原本條件記對了卻丟給 AI。步驟 1 的類型/廠商分支也不會被被排除的關鍵字觸發（`_cat_kw()`）。
    2. **只講廠商名稱直接列出那家的職缺**（使用者決定）：「美光」「那Uber呢」原本交給 AI；在問問題（「康寧的福利好嗎」）時不算。職缺分散在好幾個縣市時一樣先問地區。
    3. **兩個類型都算**（使用者決定）：「理貨或門市都可以」存成 `理貨/倉儲|門市`（`job_matches_category_filter()`／`filter_jobs_by_category_tiered()` 支援），「工廠也行」跟記住的類型合併；一次講兩個類型但沒用「或」連時取先講的那個（新增 `detect_category_labels()`，原本取的是字典順序排前面的）。
    4. **新增「客服/行政」「設備/技術」兩個類型**（使用者決定分兩類）：職務類別填「文字客服」「行政人員」「設備人員」的職缺原本不屬於任何類型，問「客服的工作」只能交給 AI。`CATEGORY_KEYWORDS`、`category_search_keywords()`、`DIRECT_INTERCEPT_ROUTABLE_CATEGORIES` 都加上；這兩類沒有專屬分支，只講類型的那一句會走「沿用鎖定類型」的候選池（新增 `_is_category_turn`）。
    5. **同縣市推薦、「都給我看看」找不到時都有按鈕**：原本同縣市推薦沒有任何按鈕，求職者回「好」「不要」都落到 AI；「都給我看看」找不到只給「清空條件」。改成每一項條件各一顆「X都可以」＋「清空條件重新找」（新增 `_drop_condition_buttons()`；同縣市推薦的按鈕掛在卡片上，因為 LINE 只顯示最後一則訊息的快速回覆）。
    6. **「清空條件重新找」按鈕直接清空**（`RESET_DIRECT_TEXT`），不用再按一次確認；打字講「清空條件」這種可能誤判的說法才需要確認。
    7. **蝦皮／momo 的類型比對跟其他分支一致**：蝦皮原本在蝦皮職缺裡分嚴格/寬鬆比對，職務類別是倉儲的蝦皮(長榮)被當成「蝦皮製造/作業員」；momo 原本記了類型卻沒拿來篩。
    8. **職務類別有填時寬鬆比對也不看對外職缺名稱**：晶旺、全家餐飲的對外名稱寫「門市人員」，職務類別是內場/外場，問「雅萱廠有門市的工作嗎」原本推這兩筆。
    9. **「地區還是廠商」按鈕帶著這句話的類型**：「新興有外送的工作嗎」按了廠商按鈕，原本類型被清掉、推了新興的作業員；現在按鈕文字是「新興這家廠商的外送工作」，沒有就老實說並給放寬按鈕。
    10. **其他**：「換地區／換工作類型」不列目前已經選的值；「我要應徵」講出是哪一筆職缺的連結；同名區兩邊在目前條件下都沒有職缺時先講清楚並給放寬按鈕；廠商名稱帶班別字樣（「M打烊班」）時不會順便記成晚班；FAQ 回覆的按鈕不再固定是「新莊／桃園」；「花蓮的外送的職缺」這種「的的」改成「花蓮・外送」。
    - **更新測試**：這一輪決定改掉的舊行為（排除條件交給 AI、只講廠商交給 AI、清空按鈕要確認、換地區列出目前的值、設備人員沒有類型、職務類別有填時仍看對外名稱）有 11 個既有測試照新決定更新，每個都保留原本要檢查的重點（不推不相關的職缺）。
    - **新增測試**：`tests/test_matcher_service.py::RoundFiveFlowMatcherTests`（5 個）、`tests/test_message_handler.py::MultiTurnRoundFiveFlowTests`（11 個多輪測試）。全部測試通過：`python3 -m unittest discover -s tests` 共 2255 個測試，OK。第五輪 agent 的重現腳本（按鈕爬蟲 22 項）重跑，剩下的都是腳本本身寫死舊按鈕文字或直接呼叫舊函式。

74. **第六輪多輪對話測試（5 個 agent，各自扮演不同角度的求職者：16 位不同生活處境的人、145 筆職缺每筆用 7 種聊法找找看共 1,015 段對話、主要在問問題/聊天的人、只按按鈕或行為很跳的人約 3.2 萬輪、條件很專業很細的人），第一批：聽懂不同求職者的說話方式**（2026-09-23）。沒有當機、沒有只靠按鈕就繞不出來的迴圈；144/145 筆職缺至少有一種聊法找得到（LADY M 例外，見下方廠商名稱）。
    1. **「沒有日領的話週領也行」「沒有交通車也沒關係」不是否定**：原本被記成「排除日領／交通車」，反而把那些職缺藏起來。`_negation_at()` 裡否定詞是「沒有」、後面接「的話」「也」時不算否定。
    2. **「夜班 不要」「夜班，不行」中間有空格或逗號**（新住民常這樣打）：原本意思整個反過來、被當成要夜班。後面那個子句只有否定詞時算在前一個詞上（`_NEGATION_ONLY_CLAUSE_RE`）。後置否定也補「不上班」「不做」「做不來」「不能上班」「要休息」。
    3. **「週末休」「假日休息」「假日不上班」「六日休」是休假（週休二日）不是要假日班**：原本意思相反。這些說法收進 `LEAVE_BUCKETS` 的週休二日，找班別時先遮掉。
    4. **「四班二輪」只算班別（輪班）**：原本同時被當成休假「做二休二」，把唯一一筆四班二輪的職缺（永豐餘，排休）藏起來。
    5. **轉行的說法**：「想從餐飲轉到工廠」「我以前做外送 現在想找門市」原本記成以前的類型。有「以前／之前／從」這種說法時，只看最後一個「想找／轉到」後面的部分（`_intent_tail()`）；步驟 1 的類型分支也改用這個結果（`_cat_kw()`）。
    6. **FAQ 有標準答案就先答**：「可以預支薪水嗎？」一字不差是 FAQ 的問題，原本卻先問「了解規定還是找職缺」；按了「了解規定」送回來的「想了解週領的規定」原本交給 AI，現在用「週領」去找問題裡有這個詞的 FAQ（`_faq_for_this_message()`）。
    7. **簡短回覆對到沛沛上一句的問題**（`_rewrite_reply_to_last_prompt()`，對話紀錄改成一開始就讀）：問「想在哪個地區工作呢」回「都可以」→ 當成「地區都可以」（原本清掉類型、推全台不相干的職缺）；問「要把這個條件拿掉嗎」回「好／對／拿掉」→ 拿掉，回「不要／保留」→ 保留；問「了解規定還是找職缺」回「找職缺」→ 找職缺；問「想看哪一種」回「都可以」→ 類型都可以。
    8. **聊天句子先問一下**（使用者這一輪決定）：「我白天要顧小孩」原本被記成早班（意思相反），現在問「要幫您找晚班或大夜班的工作嗎？」；「我老公是司機」「我之前在辦公室做行政」「我現在很急需要現金」一樣先問，不記條件。句子裡同時有明確要求時照常處理（「早班就好 我晚上睡得早」記早班）。
    9. **問公司本身的問題不當成條件**：「你們假日有上班嗎」原本記成假日班、「可以找真人客服嗎」「客服電話幾號」被當成要找客服類的工作。改成交給 FAQ/AI（`classify_condition_utterance()` 回傳 info）。
    10. **其他說法**：「還是新竹」「算了還是桃園」是改口不是問句；「好 我想換地區」「那我想換個地區」一樣進換地區選單；月薪／做一天領一天／兩週領／休禮拜一／早上／送貨／騎手／櫃台／自己排班、英文 part time / night shift / daily pay；「pt」只認整個英文字（原本從「accept」裡抓出兼職）；「辦公室」不再算客服/行政。「自己排班／彈性排班」原本沒有任何職缺會被歸到這一類，現在休假方式是自由報班、班別寫彈性的都算。
    11. **廠商名稱**：呷哺、強茂、高力、瑪諾、三澧（廠商名稱帶廠區/公司字尾，求職者只講品牌）；「LADY M」這種名稱有空格的原本永遠比對不到。
    - **新增測試**：`tests/test_matcher_service.py::RoundSixUnderstandingMatcherTests`（10 個）、`tests/test_message_handler.py::MultiTurnRoundSixUnderstandingTests`（12 個多輪測試）。全部測試通過：`python3 -m unittest discover -s tests` 共 2277 個測試，OK。
    - **使用者這一輪的決定（第二、三批處理）**：符合超過 4 筆時加「看更多」按鈕並講出總共幾筆；記住剛給求職者看了哪些職缺，問「這個有交通車嗎」用那筆的資料回答；聊天句子先問（本批已做）；新增全職/兼職跟薪資篩選。

75. **第六輪多輪對話測試，第二批：排除條件、改口、地名**（2026-09-23）：
    1. **排除之後改口「也可以」就解除**：「不要夜班」之後說「夜班也可以」原本排除條件還在、夜班職缺永遠看不到；現在解除排除（`_label_slot` 一併處理類型、廠商、地區）。
    2. **拿掉兩個條件中的一個**：「桃園或新竹的門市」之後說「不要新竹」→ 只留桃園；類型一樣（「理貨或門市」之後「不要門市」只留理貨）。記住的是中壢、說「不要桃園」時，連中壢一起清掉，換地區的按鈕也不再列出被排除的地方；排除桃園之後又說「龜山」，桃園的排除自動解除（區在被排除的縣市裡）。「新竹以外也可以」是放寬到全部地區，不是排除新竹。
    3. **排除某一種職務**：「不要外場」只排除外場人員，不會把整個餐飲類排掉（新增 `SUBROLE_WORDS`／`detect_negated_subroles()`，排除槽位多一個 `role`）。排除類型時，職缺同時還有別的類型就保留（倉儲+作業員的職缺不會因為「不要作業員」被排掉）。
    4. **「日領現金的工作」兩個都要**：原本只要其中一個就算；改成存 `日領+現金`（`combine_pay_labels()`，`filter_jobs_by_pay_label()` 的 `+` 是「而且」、`|` 是「或」）。
    5. **班別排除不看兼職旗標**：「不要晚班」原本沒排掉兼職的晚班（兼職被當成另一種班別、算「還有其他選項」）。
    6. **地名**：「新竹東區或台南東區」兩個都記對（原本都變成新竹的）；「嘉義市東區」不再變成「嘉義市嘉義市東區」；一次打兩個地址的時候兩個都算；某個類型在該區沒有、但鄰近同縣市有寬鬆符合的（例：礁溪），先用同區的寬鬆比對，不直接跳到別的縣市。
    7. **顯示**：兩個地區的條件顯示成「桃園・新竹」而不是「桃園|新竹」；職缺詳情的標題行整行換掉（原本會留下半截舊文字）；同縣市推薦卡片支援好幾個縣市；換地區/類型選單最多 12 個按鈕；同名區只剩一個有職缺時直接用那個，不再問「是哪一個」。
    8. **「類型都可以」記住**：原本只是清掉類型，下一句又問一次「想看哪一種」；現在存成 `不限`，之後不再問。
    9. **廠商名稱裡的類型字樣**：「蝦皮門市」照樣算門市類，但只有那家廠商真的沒有這個類型的職缺時，才把廠商名稱裡的字當成廠商名稱、不算類型。
    10. **修正換地區後用到舊的地區文字**：地區跟廠商名稱撞名時重新計算目前的地區文字。
    - **更新測試**：「類型都可以」改記成 `不限`（2 個）、只剩一個選項直接選（1 個），照新決定更新。
    - **新增測試**：`tests/test_matcher_service.py::RoundSixExclusionLocationMatcherTests`（6 個）、`tests/test_message_handler.py::MultiTurnRoundSixExclusionTests`（10 個多輪測試）。全部測試通過：`python3 -m unittest discover -s tests` 共 2293 個測試，OK。

76. **第六輪多輪對話測試，第三批：使用者決定的四項新功能**（2026-09-23）：
    1. **「看更多」＋講出總共幾筆**（使用者決定）：符合的職缺超過 4 筆時，回覆最後加一句「符合的職缺共 N 筆，先列出其中 4 筆」，卡片上掛「👀 看更多（還有 N 筆）」按鈕（送出 `MORE_JOBS_TEXT`＝「看更多職缺」）。打字講「還有嗎」「還有其他的嗎」「下一頁」「更多」也算（`_MORE_JOBS_RE`，整句只有這些字才算，「還有桃園的嗎」照一般流程）。列完了就說「都已經列給您看囉」，給換地區／換類型／清空條件按鈕。沒看過職缺時講「還有嗎」照原本流程。
       - 做法：每次給職缺卡片（一般直達、都給我看看、同縣市推薦，統一用新的 `_job_cards()`）時，把這次符合的全部職缺名稱（最多 100 筆）跟看到第幾筆記在新槽位 `shown`（`services/session_service.py`，格式第一行「開始,結束」，後面一行一個職缺名稱，見 `_parse_shown()`）。「都給我看看」原本一次給 5 張，統一改成 4 張＋看更多。
    2. **問剛才看到的職缺，用那筆的資料回答**（使用者決定）：「第二個薪水多少」「第1個有交通車嗎」「這些有宿舍嗎」「這個有交通車嗎」（上一句剛看了某一筆的詳細內容，或剛才只列了一筆時）直接用那筆職缺的欄位回答（薪資、領薪方式、班別、休假方式、全/兼職、地點、福利、工作內容；交通車／宿舍／員工餐／停車先看福利欄位、再找工作說明裡提到的那一行）。資料沒寫的老實說「資料上沒有寫到，建議直接問招募專員確認」，不猜。分不出是哪一筆時列出剛才那幾筆讓求職者選（按鈕送回來的是「第2個有交通車嗎」）。只講「第二個」直接打開那一筆的詳細內容。AI 推薦的職缺也會記住，一樣可以問「這個」。「那個 我想問有交通車的工作嗎」這種在找工作的句子不算（`_JOB_SEARCH_PHRASE_RE`）。
    3. **全職/兼職篩選**（使用者決定）：原本「兼職」算在班別裡（`兼職/工讀`），「全職早班」沒辦法兩個都要、「全職／正職」完全聽不懂。改成獨立一項 `worktype`（`WORKTYPE_SYNONYMS`、`extract_worktype_labels()`、`job_worktype_labels()` 看「全/兼職」欄位），跟其他條件一樣記住、可以排除（「不要兼職」不排掉全職兼職都有的職缺）、可以放寬（「全職/兼職都可以」按鈕；「全職兼職都可以」直接清掉）。
    4. **薪資篩選**（使用者決定）：「時薪200以上」「月薪3萬5」「薪水要有四萬」「35k以上」「一個月3萬2」「時薪兩百五」記成新槽位 `salary`（`時薪200`／`月薪35000`，意思是至少這麼多；`detect_salary_labels()`）。一定要講到薪水字眼、帶萬/k 單位、或講「以上／起」才算，「早上8點」「2個小孩」「30歲」不會被當成薪資；「以下」不支援。職缺的「薪資」欄位是同仁手打的文字，`job_salary_levels()` 全部 145 筆都驗過：範圍取最低值（196~215 算 196，**看的是起薪**）、好幾段的各自算（日班 42500 夜班 49000、早班時薪196 晚班時薪250）、33-38k、年薪換月薪、\$ 跟千分位逗號都處理。只寫時薪的職缺不拿去跟月薪比（換算要假設每月工時，不確定就不猜）；薪資欄位空白的職缺在有薪資條件時不會出現。「月薪3萬5」不會再被順便記成「發薪方式：月領」（`mask_salary_phrases()`）。回覆寫「薪資：月薪35,000元以上」，放寬按鈕「薪資都可以」。
    - **更新測試**：「兼職」改成全職/兼職一項（1 個）、「條件都不限」多清全職/兼職跟薪資（1 個）、清空條件多清 `shown`（1 個）；兩個測試的假 `update_user_slots` 多收新槽位。
    - **新增測試**：`tests/test_matcher_service.py::RoundSixNewFeatureMatcherTests`（6 個）、`tests/test_message_handler.py::MultiTurnRoundSixNewFeatureTests`（12 個多輪測試）。全部測試通過：`python3 -m unittest discover -s tests` 共 2341 個測試，OK。前幾輪的對話腳本重跑比對，唯一差別是「都給我看看」從 5 張變 4 張＋看更多（照這次決定）。
    - **上線後使用者不用另外設定任何東西**：新槽位存在既有的 Firestore `user_sessions`，舊的對話資料讀到時自動補上空值。

77. **第七輪多輪對話測試（5 個 agent，換新角度：新功能壓力測試、打字很亂的人、15～30 輪的超長對話、不是單純找工作的人（個資／敏感／雇主／在職員工）、以整個資料庫為標準答案的組合條件約 1 萬段對話＋按鈕爬蟲），第一批：確定是錯的都修**（2026-09-23）。約 12 萬輪沒有當機。
    1. **「看更多」／問看過的職缺**：
       - 條件改了、這一輪又沒給卡片時（反問類型、0 筆放寬、FAQ），按「看更多」原本翻出舊條件的職缺。現在條件一改就清掉 `shown`（主要的 `update_user_slots()` 呼叫帶 `shown=CLEAR_SLOT`），沒有清單時按「看更多」請求職者先講條件。
       - 看過的清單改用 Notion 頁面 ID 當代號（`_job_key()`；「查看職缺詳情 X」仍用職缺名稱 `_job_title_key()`）：兩筆都叫「薪航宅配」的職缺原本讓「看更多」一直重複同一頁。超過 100 筆也全部記住（原本「共 145 筆」翻到 100 就停）。第二頁起的卡片也帶地區。
       - 「第5個」「第十個」「最後一個」（超過這一頁時從整份清單開頭數）、「第一個跟第三個」一次問兩筆、「那薪水呢」「那第三個呢」「有宿舍嗎」這種沒講「這個」的追問（`_FOLLOWUP_TOPICS`，講了別的廠商/地區/類型不算）、看完詳細內容之後連續問好幾次「這個…」（`_focus_from_history()`）、回「是哪一筆」時只打「1」都聽得懂。
       - 句首「那個」是發語詞（「那個我要兼職」「那個 請問薪水怎麼算」）不再被當成問看過的職缺（`_FILLER_AFTER_THAT`、`_JOB_SEARCH_PHRASE_RE`）。
       - 問勞健保／獎金時去工作說明找；福利、薪資欄位空白時也去工作說明找（`_FIELD_FALLBACK_WORDS`）；交通車不再用「通勤」比對；工作內容的回答拿掉標題、網址、表情符號。回答裡的名稱拿掉「(代招)」「(桃園)」這種內部備註（`_job_display_name()`）。
       - 「第三個」超出範圍時的選擇按鈕原本變成「第1個第三個」一直被問同一題。
    2. **推錯職缺**：
       - 某地區沒有嚴格符合的理貨/作業員時，寬鬆比對用「行業別：物流業／製造業」把**職務類別明明是外送員、門市人員、設備人員**的職缺算進來（「台北理貨」推蝦皮外送，回覆還說符合理貨）。改成職務類別有填、而且是別的類型時不算（`_has_other_strict_category()`）。
       - 廠商名稱只填了「(代招)」的職缺（臻鼎），原本任何提到「代招」的句子都被當成這家廠商。
       - 「不要新竹縣」原本連新竹市一起排除（嘉義同理）；「不要新竹縣了，新竹市有嗎」的新竹市原本抓不到（`detect_negated_location()`、`extract_current_target_location()` 每個出現位置都看）。
       - 排除地區時，縣市欄有、行政區沒寫到的縣市也算職缺的其他地點。
    3. **意思相反**：
       - 回沛沛的問題時講「桃園都可以」「理貨都可以」「班別都可以」原本被改寫成上一題的「地區/類型都可以」（`_rewrite_reply_to_last_prompt()` 先看這句話自己有沒有講內容）。「好～」「好ㄉ」「對阿」「ok啦」「好好好」「不要ㄛ」「👍」也認得（`_normalize_short_reply()`）。
       - 否定詞補「沒辦法／不方便／討厭／受不了／不適合／NO／❌」（放前面）跟「不方便／不太行／免／pass／NG／不ok／❌」（放後面）；「不是兼職的」算不要兼職（只在「不是」緊接在條件前面時）。
       - 「按『排除的條件都可以』」原本把班別、發薪、薪資、福利、全職/兼職全部清掉（「條件都可以」被當成「其他條件都可以」）。
       - 「類型都可以」「地區都可以」之後講「外送也可以」「新竹也可以」原本變成只要外送/新竹，現在維持不限。第一次就講「兼職也可以」不當成只要兼職。
       - 「日領現金的工作」之後「不要現金」→ 剩日領（原本同時記成要現金又排除現金）。「不要外場」之後「外場也可以」會解除排除。
       - 「桃園門市」→「理貨也可以」記住兩種類型、卡片卻只列理貨；「桃園 理貨 門市」卡片列門市、記住理貨：分支改照記住的類型走（`_cat_kw()`）。「理貨，門市也可以」算兩個都要。
       - 「六日要上班也沒關係」原本被當成六日沒空、問要不要找週休二日。
    4. **薪資**：「日薪1500」「日領1500」原本被當成時薪 1,500；「我卡債50萬」「欠了20萬」「想存30萬」「體重100多公斤」「200多人」原本被當成薪資條件。「月薪至少三萬」「月薪多少」「上個月薪水」不再順便記成月領。「薪水高一點」「日薪」回覆裡講清楚沒辦法用這個篩、給「時薪200以上／時薪230以上／月薪3萬5以上」按鈕（`_unparsed_salary_hint()`）。「月薪4萬以上」之後「3萬也可以」取低的。「週結」算週領。
    5. **打字很亂**：handler 一開始先正規化（`_normalize_user_text()`）：全形英數字轉半形（「ＭＯＭＯ」「第１個」）、求職常用字簡轉繁（「桃园理货」「没办法上夜班」「兼职」）、一個字一個字空格隔開的接回來（「桃 園 理 貨」）。按鈕送回來的「查看職缺詳情 X」不動。全形標點不轉（按鈕文字是一字不差比對的）。
    6. **不是單純找工作的人**：
       - 客氣話後面還有一段話（「謝謝，我媽過世了要請喪假」「我知道了你們是詐騙」「先這樣 我要封鎖你」）原本回「不客氣呀！預祝您求職面試順利」。
       - 在職員工（「我上個月薪水少了」「我在蝦皮門市上班 想離職」）、面試完問結果、雇主想徵人（「我是廠商想徵人」「我們工廠缺人」）原本句子裡有地區/廠商就直接推職缺；改成交給 FAQ/AI（`matcher_service._NOT_SEEKER_RE`）。
       - 未成年問年齡（「我16歲有年齡限制嗎」「高中生可以打工嗎」）不再回固定的「所有職缺無年齡限制」，交給 AI（勞基法有童工規定）。
       - 「我媽住高雄」「我昨天上大夜班好累」「我人在高雄出差」這種只講地區/班別的聊天句子先問一句，不直接換條件。
       - 「我出獄後想重新開始，有前科可以應徵嗎」不再被當成要清空條件。
       - 按了「想了解X的規定」時，先用原本的問題找 FAQ；原本的問題還有別的內容（「領失業給付可以打工嗎」）時，只用 X 找到的 FAQ 也要跟原本的問題有關才用。
       - FAQ 候選清單寫進 Notion 前多擋身分證字號、email、市話、門牌地址（`services/notion_service.py`）。
    7. **其他**：「看更多」多收「其它呢」「有更多嗎」「再推薦幾個」「再來幾個」「換一批」「more」、句尾的 emoji／～；「美光 桃園」廠商跟地區一句講完直接列出（原本交給 AI）；還沒看過職缺就問「第2個有交通車嗎」不再被當成要找有交通車的工作。
    - **新增測試**：`tests/test_matcher_service.py::RoundSevenMatcherTests`（7 個）、`tests/test_message_handler.py::MultiTurnRoundSevenTests`（15 個多輪測試）。全部測試通過：`python3 -m unittest discover -s tests` 共 2380 個測試，OK。5 個 agent 回報的重現步驟全部重跑確認修好；前幾輪的對話腳本重跑比對，差別都是這次修的地方；隨機對話 800 段、按鈕爬蟲 750 段沒有當機、沒有迴圈。

78. **第七輪，第二批：使用者這一輪的四個決定**（2026-09-23）：
    1. **內部職缺名稱維持現狀**（使用者決定）：按「了解詳細內容」時聊天室仍顯示「查看職缺詳情 {職缺名稱}」、詳細內容標題也是內部名稱。**請同仁注意職缺名稱不要寫「取消專案獎金」「(測試)」這種備註**（求職者看得到）。
    2. **轉給真人專員**（使用者決定）：`matcher_service.detect_handoff_reason()` 判斷六種情況——業務詢問（廠商想徵人／談合作）、個資／停止聯繫、抱怨／客訴（檢舉、勞工局、你們是詐騙）、在職員工（薪水少了、想離職、想請假）、面試／應徵進度、要找真人。程式直接回固定句（`_HANDOFF_REPLIES`，都寫「專員上班時間會盡快聯繫」，因為沛沛只在同仁下班時段回覆），並記一筆到既有的 Notion「求職者提問追蹤」資料庫，提問內容開頭是「【需要專員處理：業務詢問】」這種標記，**同仁在 Notion 用「提問內容」欄位搜尋「需要專員處理」就能找到**。不用新增任何欄位或設定。「你們是詐騙嗎？」這種問句不算抱怨，照舊交給 AI 回答。
    3. **第一次就講「夜班也可以」先問**（使用者決定）：還沒記住這一項時，問「是只要看「大夜班」的工作，還是「大夜班」跟其他班別都可以呢？」，按鈕「只要大夜班的工作」「班別都可以」。班別、休假、發薪方式、福利、全職/兼職都一樣；一次講兩個（「日領沒有的話週領也行」）照舊兩個都算；已經記住值時照舊合併。
    4. **認不出是不是否定時先問**（使用者決定）：`matcher_service.detect_uncertain_negation()`——這句話講到的條件附近有「不／沒／没／免／NO／NG／❌」、但不是已經認得的否定說法（例如「夜班沒興趣」「夜班不太好」），問「您是想找「大夜班」的工作，還是不要「大夜班」呢？」，按鈕「大夜班的工作」「不要大夜班」。「不錯」「不限」「沒關係」「有沒有」「沒有的話」這些肯定的說法不算；否定詞前面只看到上一個條件詞為止（「不要夜班日領就好」的「不」是夜班的，不問日領）。另外補「不太想」當否定詞。
    - **新增測試**：`tests/test_message_handler.py::MultiTurnRoundSevenDecisionTests`（6 個）；「兼職也可以」的舊測試照新決定改成先問。全部測試通過：`python3 -m unittest discover -s tests` 共 2386 個測試，OK。

79. **「固定休假日」被當成要假日班**（使用者 2026-09-23 LINE 實測回報）：清空條件後依序講「固定休假日」「固定休六日」「周休六日」「休六日」，沛沛推的都是假日班職缺，回覆寫「班別：假日班・休假方式：週休二日」。
    - 原因一：「固定休假日」裡的「假日」被當成假日班。改成「固定休假日」「休假日固定」「假日固定休」「休假日不上班」等算週休二日（`LEAVE_BUCKETS`），找班別時「休假日」一律遮掉，「休假日也可以上班」才算假日班（`extract_shift_labels()`）。
    - 原因二：假日班（假日要上班）跟週休二日（假日休息）互相矛盾，卻能同時記住。改成這句話講了其中一個，就拿掉記住的另一個，以新講的為準（handler 的 `_label_slot` 之後）。
    - **新增測試**：`tests/test_message_handler.py::MultiTurnHolidayOffTests`（4 個）。全部測試通過。
80. **第八輪測試：先修跟說法無關的程式錯誤**（2026-09-23）。第八輪 5 個 agent 只測試不修改，結論跟使用者討論後決定：之後「沒有精準命中的句子交給 AI 判斷語意、程式照 AI 整理的需求單找職缺」（見第 81 項的計畫），但下面這些不管用不用 AI 都是程式本身的錯，先修：
    - **「新北或桃園」被黏成「新北市桃園區」**（兩個縣市用「或／、／跟」連起來，約三成組合會這樣）。原因：縣市名後面隔一個字就算「修飾後面的區」，連「或」也算。改成中間只能隔空白（`extract_current_target_location()` 的 `named_before` 跟縣市層級那段）。18 個縣市兩兩組合 × 4 種連接詞全部驗證過。
    - **「新竹竹北」變成「新竹市竹北區」**：只講「新竹」「嘉義」時縣跟市都要算，竹北在新竹縣。
    - **「后里」認不得**：簡轉繁把「后」一律轉成「後」，后里變後里。改成轉完之後「後里」換回「后里」（`_normalize_user_text()`）。
    - **同名職缺第二筆打不開**（兩筆都叫「薪航宅配」，領薪方式不同）：同名的第二筆起卡片按鈕送「查看職缺詳情 薪航宅配（2）」（`_assign_detail_keys()`，`flex_service` 也改用 `_detail_key`）。
    - **反問之後回「都可以」會清掉類型跟廠商**（第八輪按鈕測試 330 次試驗裡 280 次清錯）：`_rewrite_reply_to_last_prompt()` 補上換地區／班別／類型選單、同名區、「要不要找晚班或大夜班」、清空確認；「您是想找 X 還是不要 X」回「要／不要」、「只要 X 還是都可以」回「都可以／只要」。其他對不上的問題，求職者只回「都可以」時改成問「是指哪一項條件都可以」並列出目前每一項條件的按鈕（`_ANY_WHICH_PROMPT`），不再照一般規則清掉類型。重跑同一組試驗剩 30 次，都是「要清空嗎→都可以」本來就該全清的情況。
    - **自己打「全部清空」「對 全部清空！」「清掉」用舊條件列職缺**：前面有「對／好」或剛問過要不要清空就直接清，其他先問一次。
    - **按鈕標籤截斷在數字中間**（「月薪36K」變「月薪36」）：`_short_label()` 截斷時拿掉尾巴的數字並加「…」。
    - **新增測試**：`tests/test_message_handler.py::MultiTurnRoundEightDefiniteBugTests`（9 個）。全部測試通過；第七輪的回歸對話前後比對沒有差異。
    - **第八輪找到的 Notion 資料問題（請同仁修改）**：縣市跟行政區對不上（題陞：寶山區在新竹縣、縣市寫新竹市；錢都(代招)_時薪 有寶山區但縣市沒有新竹縣；美光(桃園) 四筆、PChome 縣市桃園市、行政區卻只有林口區；三澧 縣市有新竹縣但沒有新竹縣的區；老鼎旺、21世紀、佳瑪、COLDSTONE、貳樓、品田、全家餐飲 也有類似情況）；21 筆職務類別空白（台灣大哥大客服、uber 站所小幫手、愛物科技、LADY M、文華、兩筆薪航宅配…）；「蝦皮內勤(測試)」是測試資料；「美光(桃園)_CCR廠務」職務類別填行政人員；週休二日的職缺其實要週六加班（力山、第一傳動、貝爾威勒、永湖）。
81. **AI 需求單：沒有精準命中的句子交給 AI 判斷語意**（使用者 2026-09-23 決定：「沒辦法都想用程式判斷，要把沒有精準命中的給 AI 判斷語意，把邊界定義好」）。
    - **分工（邊界）**：
      - 程式只處理「精準命中」：按鈕送回來的固定句型（`_is_program_phrase()`），以及整句話拿掉認得的地名／類型／廠商／班別／休假／發薪／全兼職／薪資／福利跟虛詞之後什麼都不剩的短句（`understanding_service.is_precise_hit()`，用「，」分段；有否定詞時只有「不要X」這種按鈕句型算）。
      - 其他句子交給 Gemini 填「需求單」（`services/understanding_service.py` 的 `UNDERSTANDING_SCHEMA`）：意圖（找工作／問問題／轉專員／閒聊／不確定）、想要跟不要的地區、類型、廠商、班別、休假、發薪、全兼職、薪資、福利、要放寬的項目、沒有對應類型的職務、轉專員原因、不確定時的確認問題跟選項。類型、班別這些欄位只能從固定選項選。
      - 程式檢查需求單（`validate_form()`）：地名要程式自己認得、廠商跟福利要資料庫有，同一個值又要又不要以要為準，確認選項要是精準命中的句子；不合格的值一律丟掉。
      - 程式把需求單轉成標準句子（`render_canonical_text()`，例如「桃園或新北 理貨/倉儲的工作，不要大夜班」），交給原本的流程找職缺、組回覆，所以條件跟卡片一定對得上，AI 不會自己挑職缺。
    - **每種意圖怎麼處理**（handler「步驟 0-2b」）：找工作→換成標準句子照原本流程；問問題／閒聊→不動條件，FAQ 完全命中就回原文，否則交給 AI 回答（原本的步驟 2，抽成 `_answer_with_ai_decision()`）；轉專員→固定回覆＋記錄給同仁（跟原本的步驟 0-0B2 同一套 `_reply_handoff()`）；不確定→跳出 AI 給的選項讓求職者選；只講了沒有的職務（保全、清潔…）→老實說沒有，列出現有類型。關鍵字判斷說要轉專員時，也先問 AI，AI 說不是就不轉（第八輪 34 次誤判）。
    - **保底**：AI 沒回應、超過 `AI_UNDERSTANDING_TIMEOUT_SECONDS`（預設 6 秒）、回傳格式不對、或意圖是找工作卻什麼條件都沒有時，照原本的流程處理，不會比原本差。
    - **開關**（Cloud Run 環境變數，預設 `off`，合併上線後什麼都不會變）：`AI_UNDERSTANDING_MODE`＝`off`／`shadow`（照原本回覆，背景另外問 AI、把判斷寫進 log，Logs Explorer 搜「[AI需求單]」）／`on`（正式使用）。`AI_UNDERSTANDING_THINKING_BUDGET`：Gemini 先想一想的額度，預設 0（最快）。
    - **準確率考試**：`scripts/eval_understanding.py` 拿 `scripts/understanding_eval_cases.json`（第四～八輪整理出來的 88 句）問真正的 Gemini，印出通過率跟每句等幾秒。沙盒環境沒有 GCP 權限不能呼叫 Gemini，要在 Cloud Shell 跑（指令寫在腳本開頭）。之後每次改 prompt 都重跑一次。
    - **新增測試**：`tests/test_message_handler.py::AiUnderstandingTests`（14 個，用假的需求單測程式負責的部分：標準句子一定解讀成一樣的條件、分流、檢查、精準命中的邊界）。開關預設關閉，原本 2430 個測試全部照舊通過。
    - **第一次準確率考試結果**（使用者 2026-09-23 在 Cloud Shell 跑，thinking=0）：通過 86／88 題（98%），每題中位數 1.0 秒、最慢的 5% 約 2 秒；有一題剛好碰到 Gemini 排隊等了 66 秒（正式上線時超過 6 秒就照原本的流程處理，不會讓求職者等）。沒通過的兩題已修：「有小夜班嗎」被當成問問題（prompt 補上「問有沒有這種條件的職缺也是找工作」）、「新竹竹北」填成新竹跟竹北兩個（prompt 補上規則，程式也會在句子裡縣市緊接著行政區時拿掉縣市，`drop_county_before_district()`）。
    - **上線步驟**：① 合併（開關是 off，不會有任何變化）→ ② Cloud Shell 跑準確率考試，把結果給 Claude 調整 → ③ 開 `shadow` 一兩天看 log → ④ 改 `on`。
82. **沛沛整個回到 9/22 晚上（使用者傳每日／週報告給 Claude 之前）的版本**（使用者 2026-09-23 決定：「只把沛沛改回 9/22 的版本，其他功能不要改動」）。原因：9/22 晚上到 9/23 這段時間連續八輪測試、邊測邊改，使用者覺得整體結果沒有比較好，決定先回到改之前的版本。
    - **回退的基準**：`main` 上的 `1a1571a`（PR #188 合併後、PR #189 之前，2026-09-22 23:15 台灣時間）。**上面第 58～81 項對沛沛的修改全部不在線上了**（那些段落保留當作紀錄，之後要重做哪一項可以照著看）。
    - **換回舊版的檔案（只有沛沛用到的）**：`handlers/message_handler.py`、`services/matcher_service.py`、`services/flex_service.py`、`services/notion_service.py`、`services/session_service.py`、`services/ai_service.py`、`services/daily_report_service.py`，以及對應的測試 `tests/test_message_handler.py`、`tests/test_matcher_service.py`、`tests/test_notion_service.py`、`tests/test_daily_report_service.py`。這段時間這些檔案只有沛沛的 PR 改過，所以換回去不會動到其他部門。
    - **刪掉的檔案**：`services/understanding_service.py`、`scripts/eval_understanding.py`、`scripts/understanding_eval_cases.json`（AI 需求單，第 81 項），以及誤放進 repo 的 `r8_after1.txt`、`r8_after2.txt`。`config.py` 只拿掉最後面 AI 需求單的三個設定，寄信（SMTP）等其他設定不動。
    - **沒有動的（其他部門，9/22 之後的功能都保留）**：配送部系統（`delivery/`，UD 車輛同步、外送員媒合、Excel 匯入、車輛清單提示）、派遣（`dispatch_*`）、財務（`finance_routes.py`、PDF 轉圖）、保險、薪資補款、平台寄信（`services/email_service.py`、`job_portal_mail_routes.py`）等。
    - **Cloud Run 環境變數**：`AI_UNDERSTANDING_MODE=shadow` 留著也沒關係（舊版程式不會讀它），想乾淨可以刪掉。
    - **測試分支 `claude/eval-kit`**（AI 需求單＋Cloud Shell 考試工具，第 81 項後半）沒有合併、保留在 GitHub 上，之後要重新評估 AI 做法時可以接著用。
    - 全部測試通過（2154 個），每支程式都能正常載入。
83. **「全/兼職」欄位列進給 AI 的職缺清單**（使用者 2026-09-23 決定）。回到 9/22 版本後盤點 Notion 欄位時發現：「全/兼職」原本只印在職缺卡片上，程式不拿來判斷、AI 也看不到，求職者問「兼職」時 AI 只能從職缺名稱或工作內容猜。改成 `_compute_ai_decision_messages()` 組給 Gemini 的每一筆職缺多一欄「全兼職:」（沒填顯示「未提供」）。只多給 AI 資料，沒有新增任何程式判斷規則。新增測試 `test_ai_prompt_includes_fulltime_parttime_field`。
84. **快速通道只接單純的句子，多講了其他條件就交給 AI**（使用者 2026-09-23 決定，選「A」）。實測「Momo有兼職嗎」被 momo 快速通道直接推了全職職缺：外送／門市／momo／福利／發薪方式這五條快速通道只要句子裡出現關鍵字就直接跳卡片，句子後面講的其他條件（兼職、晚班、薪水多少…）完全不看。
    - **改法**：新增 `services/matcher_service.py::is_plain_shortcut_query()`，把句子裡程式本來就認得的詞（台灣所有縣市行政區名、`CATEGORY_KEYWORDS`、`KNOWN_BRANDS`、`PAY_METHOD_SYNONYMS`、職缺資料的系統廠商名稱、這句命中的福利詞）跟語助詞（`_SHORTCUT_FILLER_WORDS`）拿掉，還剩別的字就不走快速通道，往下交給 AI（AI 看得到全兼職、班別、休假、薪資）。
    - **例子**：「桃園外送」「我想找桃園的外送工作」「中壢有momo嗎」「momo理貨」「有交通車嗎」照樣秒回；「Momo有兼職嗎」「外送員薪水多少」「桃園外送晚班」「有日領的兼職嗎」交給 AI。
    - **使用者的原則：不再往程式加判斷字詞**。`_SHORTCUT_FILLER_WORDS` 不需要補齊——漏掉的語助詞只會讓句子交給 AI 處理（慢一點，但不會推錯），不要為了讓更多句子走快速通道去擴充它。
    - 新增測試 `PlainShortcutQueryTests`（4 個）。全部測試通過（2159 個）。
85. **一定要先問到地區才給職缺卡片**（使用者 2026-09-23 決定）。原本快速通道只講一個詞（「外送」「有momo的職缺嗎」）就推全台前 4 筆；交給 AI 的句子（「夜班工作」）則看 AI 當下判斷，有時先問地區、有時直接推全台職缺。
    - **只在「要給卡片」時檢查**，問問題（發薪、福利、面試…）照常回答，不受影響。「都給我看看」維持直接列職缺（求職者主動要看）。
    - **程式認得是在找工作的（快速通道：外送／門市／momo／福利／發薪方式）**：還不知道地區就馬上問地區（`_reply_ask_location_first()`，問法跟按鈕沿用 `build_progressive_question()` 的地區題），不經過 AI。
    - **交給 AI 的**：還不知道地區時，給 AI 的說明多一條「9. 還不知道地區時先問地區」；AI 仍然回 RECOMMEND 的話，程式把它換成問地區（`_compute_ai_decision_messages(..., ask_location_if_missing=True)`）。
    - **兩個保護避免卡住**：① 求職者說不限地區（`explicit_any_location`：都可以、全台、不限地區…）算回答了；② 沛沛最近 6 筆對話裡已經問過地區（回覆有「哪個地區」，清空條件跟年齡性別那兩句固定開場白不算），就不再重複問——求職者回了程式認不出的地名（「台北車站附近」）時交給 AI 照常處理，不會一直被問同一題。
    - **使用者的原則：不加判斷字詞**。判斷依據只有「條件裡有沒有記住地區」，沒有新增任何字詞清單。
    - **上線後修正（同日）**：實測清空條件後問「有外送工作嗎？」仍直接給卡片——清空前 AI 問過一次地區，那句還在最近 6 筆對話裡，被當成已經問過。改成從最近一次「清空先前的搜尋條件」之後重新算。新增測試 `test_location_asked_before_reset_does_not_count`。
    - 新增測試 `LocationRequiredBeforeCardsTests`（5 個）；原本「沒指定地區就列出全部 momo 職缺」的測試改成先問地區，福利快速通道的兩個測試改成已知地區。全部測試通過（2164 個）。

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
「廠商」「姓名」（身分證字號、電話、到職日期選填），廠商欄位可填代號或中文
名稱，到職日期接受 `2024-01-31`／`2024/01/31` 兩種格式（2026-09-12 新增，
給整批搬遷已在職舊資料用，格式看不懂的話該列直接判定失敗，不會用錯的日期
硬建進去——詳見下面「已知的技術限制」附近的說明）。重複判斷
依需求改成比對「姓名+電話」（`repository.find_active_personnel_by_name_and_phone`，
兩者都要有值才會比對，不是用身分證字號），已存在相同組合的在職人員會自動略過，
所以同一份檔案可以重複上傳來修正錯誤，不用擔心建出重複資料。

**⚠️ 踩過的雷（2026-09-12 修正）**：使用者回報「下載範本後欄位都是亂碼」——
原因是 `/import/template.csv`（`delivery/routes/import_routes.py`）原本直接輸出
純 UTF-8 文字，沒有加 BOM（檔案開頭幾個看不到的位元組，用來告訴軟體「這是
UTF-8」）。Windows 版 Excel 雙擊開啟 CSV 時，如果沒看到 BOM，會用系統的中文
編碼（Big5/cp950）去猜這份檔案的編碼，UTF-8 的中文字節被誤判成 Big5 就整份
變亂碼。已修正成輸出 `utf-8-sig`（UTF-8 + BOM）；上傳解析那邊
（`delivery/csv_import.py` 的 `_decode()`）本來就有處理 `utf-8-sig`，所以這個
改動不影響「下載範本、填完再上傳」的既有流程，也不影響使用者原本存成 Big5
的舊檔案（一樣能正常上傳）。

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

### 新增：PR 自動跑測試把關（`.github/workflows/test.yml`，2026-09-15）

使用者表示之後要找同仁一起用 Claude Code 共同開發維護這個 repo（同仁
一樣不懂寫程式，靠中文跟 Claude Code 溝通改東西）。原本 `main` 分支的
規則只有「合併後自動部署上線」（`deploy.yml`），完全沒有「先跑測試、
測試沒過就擋下來」這一關——只有一個人在用還好，多人／多個 Claude Code
session 同時開發後，任何一個人的改動不小心弄壞別的功能，很可能直接被
部署到正式環境，沒有人在第一時間發現。

新增 `.github/workflows/test.yml`：每個 PR 送出/更新到 `main` 時，自動
`pip install -r requirements.txt` 後跑 `python3 -m unittest discover -s
tests -p "test_*.py"`（目前 652 個測試），測試沒過 PR 上會顯示紅色
警告。跟 `deploy.yml` 完全分開、不會碰正式環境，也不需要任何 GCP 憑證
或密鑰——`tests/_env.py` 已經幫全部測試設好安全的假環境變數
（`GEMINI_API_KEY`／`LINE_CHANNEL_SECRET` 等都是 dummy 值）。

**使用者接下來要手動處理的事（多人協作的其餘設定，Claude Code 這邊
沒有對應的 API 可以代勞）**：

1. **把同仁加進 GitHub repo**：GitHub 網頁 → 這個 repo → Settings →
   Collaborators and teams → Add people，權限選 **Write**（可以建分支、
   推程式碼、開 PR，但不能改 repo 設定或刪除 repo）。
2. **設定 branch protection，強制走 PR**：Settings → Branches → Add
   branch protection rule，分支名稱填 `main`，勾選「Require a pull
   request before merging」；如果想連「測試沒過不能合併」都一起強制
   （不是只有顯示警告），可以再勾「Require status checks to pass
   before merging」，並選取上面新增的 `test` 這個檢查項目。
3. **每位同仁要有自己的 Claude 帳號**，並連接自己的 GitHub 帳號，才能
   開 Claude Code session 對這個 repo 提出改動；人數多、想要公司統一
   管理帳號/計費的話，另外研究 Anthropic 有沒有適合團隊的方案。
4. **GCP（Cloud Run／帳單／Cloud Scheduler）權限建議先不要開放給同仁**：
   一般的功能開發完全不需要 GCP 權限（部署是合併後自動觸發的），只有
   維運層級的操作（看帳單、設排程、改環境變數）才需要主控台權限，先
   維持只有使用者自己能動，比較好追蹤問題出在哪。
5. **多人協作後 `HANDOFF.md` 的重要性大幅提高**：每個人的 Claude Code
   session 都是獨立的、看不到別人在聊什麼，這份文件幾乎是唯一能讓不同
   人接手同一個系統還知道彼此做過什麼的地方。建議同仁動工前先看一下
   「待辦事項」有沒有別人在做的項目，改完務必請 Claude Code 更新對應
   章節；如果兩人剛好想同時改同一個功能，先用 LINE 或口頭喊一聲，避免
   兩個 session 改到同一批檔案、合併時互相衝突。

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

## 新增：少凱業務開發專區加上「勾選要反查的職缺」功能（2026-09-17）

> **2026-09-24 起這節描述的做法已被取代**：畫面不再讀試算表、勾選改成以「地點組」為單位、存在 Firestore，見檔案最後「業務開發整併」那節。下面保留當歷史紀錄。

背景：`tsaipei-linebot-recruitment-leads-scraper` 這個獨立 repo（每天自動
搜尋 104/1111/小雞上工的派遣客戶開發職缺，寫進跟 `/salesdev` 讀的同一份
Google Sheet 的「Leads」分頁）這次改版，把「反查要派公司/電話/email」這
一步從「抓到職缺就自動反查」改成「先審查、再反查」——新抓到的職缺一律是
「審查狀態」＝「待審查」，要等使用者勾選過想深入了解的職缺，才由另一個
獨立的每日反查排程（尚未建立，不在這個 repo）去處理。這裡要做的就是那個
「勾選」的畫面，讓少凱不用打開 Google Sheet 原始畫面就能操作。

### 怎麼運作

- `/salesdev` 讀到的每個分頁，只要表頭裡有「審查狀態」這個欄位，這個分頁
  的表格最左邊就會多一欄勾選框——只有「審查狀態」目前是「待審查」的列
  才有勾選框，其他狀態（已勾選待反查／已反查）顯示「—」，不能重複勾選。
  沒有「審查狀態」欄位的分頁（例如「新登記工廠監控」）維持原本純唯讀，
  不會多這一欄。
- 勾選幾筆之後按「送出勾選」，會 POST 到新的 `/salesdev/select` 路由，把
  這些列的「審查狀態」改成「已勾選待反查」，然後導回 `/salesdev` 顯示
  「已送出 N 筆」的提示。跟其他模組一樣，這個路由也是先過
  `_require_access()` 的權限檢查，沒有 `salesdev` 模組權限的帳號沒辦法
  用這個功能寫入資料（跟唯讀畫面用同一套權限設定，不用另外去 `/accounts`
  加開權限）。
- 送出的當下會**先重新讀一次這些列現在的「審查狀態」，只更新目前真的還是
  「待審查」的列**才寫回「已勾選待反查」——防的是使用者畫面開著沒送出、
  這段時間每日反查排程已經把某幾列處理掉的情況，避免舊畫面的送出動作把
  排程剛寫好的結果覆蓋掉。
- 「審查狀態」欄位三個狀態值的字串（「待審查」／「已勾選待反查」／
  「已反查」）跟 `tsaipei-linebot-recruitment-leads-scraper` 的
  `src/models.py`（`REVIEW_STATUS_*`）完全一致，因為兩邊讀寫的是同一份
  Google Sheet 同一欄——**之後如果任何一邊要改這幾個字串，另一邊也要
  跟著改，不然狀態會對不起來**。

### 新增/修改的檔案

- `services/salesdev_sheet_service.py`：
  - `fetch_sheet_tabs()` 的 `rows` 從「每列直接是一串 cell」改成
    `{"row_number", "cells"}`——`row_number` 是這一列在 Google Sheet 裡
    實際的列號，畫面上的勾選框要靠這個列號才能精準指到正確的儲存格
    （不能只靠畫面排第幾筆，因為有搜尋框篩選）。多回傳一個
    `review_status_col_index`：這個分頁表頭裡「審查狀態」欄位的位置，
    沒有這個欄位是 `None`，畫面靠這個值決定要不要顯示勾選框。
  - 新增 `mark_rows_selected_for_reverse_lookup(tab_title, row_numbers)`：
    寫入用的函式，回傳 `(實際更新的列數, error)`。**這是這份試算表第一次
    需要被「寫入」**，改用可讀寫的 `spreadsheets` scope（`_get_sheets_
    service_write()`），原本的讀取路徑維持唯讀 scope 不變。
- `salesdev_routes.py`：新增 `POST /salesdev/select` 路由，接收勾選的
  `tab_title` + `row_numbers`（表單欄位），呼叫上面的服務函式後導回
  `/salesdev`。
- `templates/salesdev_home.html`：有「審查狀態」欄位的分頁表格外面包一層
  `<form>`，加上勾選框欄位跟「送出勾選」按鈕；畫面上方新增送出成功/失敗
  的提示訊息區塊。
- `tests/test_salesdev_sheet_service.py` / `tests/test_salesdev_routes.py`：
  補上 `_col_index_to_letter()`、`mark_rows_selected_for_reverse_lookup()`
  設定缺漏時的錯誤訊息、`/salesdev/select` 未登入時導向登入頁的測試（跟
  既有分工一致，實際打 Google Sheets API 的路徑留給有 GCP 憑證的環境做
  整合測試）。

### 上線前要做的事

1. **這份 Google Sheet 要額外分享「編輯者」權限給 Cloud Run 服務帳戶**
   （原本只分享了「檢視者」，這次「勾選送出」需要能寫入）——到 Google
   Sheet 右上角「共用」，找到原本分享的那個服務帳戶信箱（`tsaipei-505807`
   專案的預設運算服務帳戶，或另外指定的服務帳戶信箱，跟唯讀權限分享的是
   同一個信箱），把權限從「檢視者」改成「編輯者」即可，不用重新分享一次
   或新增一筆分享紀錄。沒有升級權限的話，「送出勾選」會顯示「沒有權限
   寫入這份 Google Sheet」的提示，不影響頁面原本唯讀顯示的部分。
2. 確認 `tsaipei-linebot-recruitment-leads-scraper` 那邊的 `GOOGLE_SHEET_ID`
   跟這裡的 `SALESDEV_SHEET_ID` 真的是同一份試算表、同一個「Leads」分頁
   ——如果不是同一份，這個勾選功能會看不到抓職缺程式寫入的新職缺。
3. 之後「每日反查排程」（讀取「已勾選待反查」的列去執行反查、寫回結果、
   把狀態改成「已反查」）要另外建立，不在這個 repo 的範圍——`/salesdev`
   這裡只負責讓使用者勾選、把狀態改成「已勾選待反查」，不執行任何反查
   邏輯。

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
扣分鐘月份、補請款月份、是否可請款、補款方式、備註（必填），加項/扣項
明細是固定十個項目（不是同仁自己新增的），還可以上傳一張佐證照片。
送出時 `services/salary_repayment_submit_service.py` 把這些資料組成
GAS `SUBMIT_SALARY` 端點看得懂的格式（照抄 `Project_Salary.gs` 的
`SalaryWorkflowService.processSalarySubmission()` 現有欄位，沒有新增
或修改任何對方看得懂的欄位名稱），原封不動 POST 過去。GAS 那邊回什麼
（成功、尚未完成 LINE 綁定、找不到核准主管…）就直接把它的中文訊息顯示
給同仁看，材霈平台這邊不重新判斷、不重新組訊息。

**2026-09-09 依使用者提供的現有 Netlify 表單畫面截圖整個重做過一次，
後來使用者直接提供表單原始碼（`index_6.html`）又核對/補齊了一次細節**
（原本第一版是同仁自己新增「名稱＋金額」列的通用設計，跟同仁原本熟悉
的操作方式差太多）。現在完全照抄現有表單：
- 分成「一、基本資料與請款設定」「二、加項明細 (應領項目)」「三、
  扣項明細 (應扣項目)」「四、補款佐證圖檔上傳 (選填)」四個區塊，欄位
  名稱、必填/選填、順序都跟現有表單一致（見
  `services/salary_repayment_submit_service.py` 的 `EARNING_FIELDS`/
  `DEDUCTION_FIELDS`：工時/天數、薪資、工時獎金、制服退費、勞保退費、
  健保退費、推薦獎金、資遣費、年假代金、其他加項；勞保費、健保費、
  眷屬健保、二代健保、團保費、補扣押金、法扣、欠款、匯費、其他扣項）。
  這些項目的**英文代號**（表單欄位名稱、也是送給 GAS 的
  `earnings`/`deductions` 物件 key，例如 `work_hours`、`labor_ins`）也是
  照抄原始碼裡 `handleSalarySubmit()` 實際使用的代號，不是自己另外
  命名，跟現有表單送出的資料格式完全一致，不只是畫面像而已。
- 加項/扣項小計、底部「應領小計(+)／應扣小計(-)／實補金額(總計)」都是
  即時用 JavaScript 算出來的，同仁填數字馬上看得到總額更新，不用送出
  才知道加總對不對。
- 佐證圖檔可以點擊選取或直接拖曳到框框裡，選好會顯示縮圖預覽。
- 「是否可請款」下拉選單：「可」/「不可」（原本推測正確，原始碼已
  確認）。
- 「補款方式」下拉選單：「立即補款」/「同次月薪」（原始碼確認的完整
  清單，原本只有前者，這次補上第二個選項）。
- **身分證字號改成必填**（原本第一版誤植為選填），並且比照現有表單
  `validateTaiwanId()` 加上台灣身分證字號檢查碼驗證（見
  `services/salary_repayment_submit_service.py` 的 `validate_taiwan_id()`），
  同仁填錯格式會在送出時被擋下來、跟現有表單的行為一致，輸入框也會
  自動轉大寫（`maxlength="10"`），不是這次新增的額外限制。

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

**踩過的雷（2026-09-09 使用者實測回報，已修好）**：送出申請後，材霈
平台要等 GAS 那邊把存照片、寫試算表、推播 LINE 給主管都做完才會收到
回應、才會跳轉畫面，這中間有幾秒鐘畫面看起來像沒反應，同仁誤以為沒
送出、又按了第二次，變成送出兩筆重複的申請。修法：`templates/
salary_repayment_form.html` 送出當下就把按鈕鎖住、文字改成「送出中，
請稍候…」，避免手滑按第二次。這只是前端擋一下、不是嚴謹的防重複
機制（例如同仁把 JS 關掉、或手腳極快還是有機會送出兩筆），GAS 那邊
本身也沒有防重複送出的邏輯——如果之後真的常常發生重複送出，可以
考慮在材霈平台這邊送出前先做一個簡單的「幾秒內同一個人、同一筆內容
不能再送一次」的判斷，這次先以「解決同仁實際回報的操作體驗問題」
為主，還沒做到那麼嚴謹。

另外使用者同時回報「LINE 核准卡片上的退回按鈕可以按兩次，第二次會
顯示『找不到該薪資補款單資料』」——這段是 GAS 原本的邏輯（第一次
退回就把那筆資料從試算表刪除，第二次點同一個按鈕當然找不到），跟
這次新增的送出表單無關，這次沒有修，因為要修需要動到 GAS 的程式碼，
超出方案 A「完全不動 GAS」的範圍；影響也很小（第二次點擊只是顯示
一個錯誤訊息，不會真的重複核准或造成資料損壞）。已跟使用者說明，
待日後決定要不要處理。

**踩過的雷（2026-09-09 使用者實測回報，已修好）：GAS 網頁應用程式
「明明處理完了、卻回傳看不懂的內容」**。使用者實測發現：畫面顯示
「職缺維護系統回應格式異常（HTTP 404）」，但其實那筆申請已經成功
寫進試算表、主管也收到 LINE 通知了——GAS 那支網頁應用程式偶爾會在
「執行完成」跟「把結果回傳給呼叫方」這兩步之間出狀況，回傳一個看不懂
的內容（HTTP 404、不是合法 JSON），不是材霈平台這邊程式邏輯有錯、
也不是 `JOB_PORTAL_GAS_WEBAPP_URL` 網址設定錯誤（一開始誤判成網址
問題，後來確認網址是對的）。

這個情況最大的風險是：**畫面顯示一個看起來很肯定的錯誤訊息，同仁會
很自然地想「重送一次」，但申請其實早就送出去了，重送就會產生兩筆
重複的申請**——比前面那個「按鈕沒反應被誤按兩次」的問題更隱蔽，因為
那次畫面至少沒有明確說「失敗」。

修法：`services/salary_repayment_submit_service.py` 的 `submit_salary_repayment()`
把「確定沒送到 GAS」（網址沒設定、連線被拒絕、DNS 解析失敗）跟「不確定
GAS 有沒有處理完」（請求逾時、收到回應但看不懂內容）分成兩種不同的
`status`：前者才是 `"error"`，可以放心讓同仁重新送出；後者是新增的
`"unknown"`，`me_routes.py` 看到這個狀態時**不會**讓同仁停在原本填好的
表單上、被誘導再按一次送出，而是導去「我的專區」，提醒同仁先確認這筆
申請有沒有出現在下方紀錄裡（`/me` 每次都是即時讀試算表，如果 GAS 那邊
其實已經寫進去了，重新整理就看得到）。

### 下一步

**薪資補款送出表單（方案 A）：2026-09-09 使用者實測通過，這一項正式
完成。**「職缺維護表單整合案」動工順序的下一項是「職缺維護」（延伸
現有 Notion 寫入模式）。已經有 `Project_Job.gs` 原始碼可以參考，但
這次薪資補款表單的經驗顯示：光有 `.gs` 原始碼還不夠精準，**直接拿到
現有 Netlify 表單畫面的原始碼（`index_6.html` 那份，裡面同時有職缺
維護/薪資補款/專案合約三個分頁的前端）比對，才抓得到欄位下拉選單的
完整選項、必填/選填、送給後端的英文欄位代號這些細節**，之後動工前
會請使用者一併提供這份，不會只憑 `.gs` 後端邏輯自己猜前端長相。這份
`index_6.html` 使用者已經提供過（用於比對薪資補款表單），裡面本來就
包含職缺維護分頁（`#sectionJob`）的完整前端，不用再跟使用者要一次。

**2026-09-09 已定案：職缺維護要建成一個新的、獨立的部門模組**（不是
放進「我的專區」，也不是掛在既有的「少凱業務開發專區」底下），會在
`platform_accounts.MODULES` 多加一筆（例如 `job_listings`／「職缺
維護」），跟 `/companies`／`/vendors` 一樣是獨立的模組，可以在
`/accounts` 頁面個別勾選哪些帳號有權限。**使用者特別強調：權限邏輯
跟頁面畫面都必須跟現有 Netlify 表單完全一致**，因為現有系統裡已經
寫好對應的邏輯——現有畫面「操作模式設定」寫的「新增全新職缺，或維護
個人與轄下同仁之職缺（主管具修改權限）」，這剛好對應到這個平台既有的
`MODULE_ROLES`（`staff`/`admin`，「專員」/「主管」）概念，動工時要
延用這個既有機制做這個模組的權限判斷，不要另外發明一套。

### 職缺維護（`/job-listings`）：2026-09-09 已實作，方案 A 模式

使用者後來直接提供了完整的五份 GAS 原始碼（含這次新看的
`Project_Job.gs` 全文），研究後確認：
- 「行業別」「職務類別」「全/兼職」「外籍生」「職缺週期」「班別」
  「休假方式」「領薪方式」「負責所別」這幾個多選下拉選單，選項都是
  **寫死在現有 Netlify 表單前端的固定清單**（`index_6.html` 的
  `NOTION_OPTIONS` 常數），不是即時去 Notion 資料庫抓的，照抄過來即可，
  不用另外接 Notion API 查詢選項這件事。
- 送審之後 GAS 會**自動呼叫 Vertex AI（Gemini）潤飾對外文案**（生成
  吸睛標題、條列式工作內容、精華亮點、排版說明，並過濾違反就業服務法
  的字句如性別/年齡限制），這一套邏輯很細膩、調校過，**沒有搬過來**，
  繼續由 GAS 處理（比照薪資補款的「方案 A」精神）。
- 縣市/行政區也是固定對照表（`TAIWAN_DATA`，22 個縣市），縣市選了才會
  出現對應行政區選項，這裡照抄整份對照表。

**實作內容**（`job_listing_routes.py` + `services/job_listing_submit_service.py`
+ `templates/job_listing_form.html`）：
- 新增獨立部門模組 `job_listings`（`platform_accounts.MODULES`），跟
  `/salesdev` 一樣直接掛在根 app，`/accounts` 頁面可以個別勾選哪些帳號
  有權限，跟 `/companies`／`/vendors`（全平台管理員專用）不同。
- **模組的「專員」/「主管」角色（`MODULE_ROLES`）只決定看不看得到這張
  卡片，不分角色體驗完全一樣**——這點跟原本推測的不同，記錄修正一下：
  現有系統「主管具修改權限」指的是**組織層級的直屬主管**（原刊登人的
  主管才能改），不是「這個工具裡誰被設成管理員角色」，所以「誰能改誰
  的職缺」這件事是用帳號的 `manager_usernames`（反查誰的主管是自己，
  見 `compute_subordinate_names()`）算出「下屬名單」，送給 GAS 的
  `GET_JOBS`／`SUBMIT_JOB` 端點，**最終的權限判斷還是 GAS 自己依它的
  組織表邏輯逐筆把關**（`OrgService.isSupervisorOf`），材霈平台這邊送
  的下屬名單只影響「搜尋清單顯示哪些職缺」，不是真正的權限守門員，就算
  這裡算錯也不會讓沒權限的人真的改到職缺。
- 新增/維護既有職缺兩種模式都做了：「維護既有職缺」用一個搜尋框
  （`/job-listings/api/jobs` 這支 JSON 端點回傳這個帳號能維護的職缺
  清單），選到職缺後用 JavaScript 直接把整包欄位帶入表單（跟現有表單
  的 `populateJobForm()` 做法一致），送出時走同一個表單、同一支
  `SUBMIT_JOB` 端點，用 `mode`/`pageId`/`updateAction` 三個欄位區分。
- **多選下拉選單改成照抄現有表單的操作方式**：點擊欄位跳出選單、選了
  變成一顆一顆可以按 × 移除的標籤，不是瀏覽器內建的 `<select multiple>`
  清單。2026-09-09 第一版原本用了瀏覽器內建多選（理由是操作介面呈現
  方式不影響功能），但使用者實測後回報「感覺不太一樣」、而且行政區
  原本有的搜尋功能不見了，所以改成自己刻一套 JS 元件比照原本的操作
  邏輯：`toggleOption()`/`removeTag()`/`renderTags()`/`renderMenu()` 這
  幾個函式名稱都直接對應現有表單原始碼裡的同名函式，**只有「行政區」
  這個欄位有搜尋框＋全選/清除按鈕**（`renderDropdownOptions()` 裡的
  district 分支才有，其他十個欄位維持純清單），這是現有表單本身的
  行為，不是我方另外加的功能。畫面底下實際負責表單送出資料的還是一個
  `hidden` 的原生 `<select multiple>`，JS 選完會同步過去，材霈平台的
  路由/服務層完全沒有因為這次改版而變動。

  **踩過的雷（2026-09-09，同一天內兩次），這次徹底解決**：這個自訂
  元件版本上線後，使用者實測回報「所有選單功能都是壞的」——點下拉選單
  的選項，選了又立刻自動被取消，完全選不進去。伺服器端 Jinja2 渲染
  測試（`templates.TemplateResponse` 直接產生 HTML 字串）完全看不出
  這個問題，因為錯誤只發生在瀏覽器實際處理 click 事件的行為裡；改用
  **這台機器上已經裝好的 Playwright + Chromium**（見系統環境說明），
  把渲染出來的 HTML 存成靜態檔案、起一個本機網頁伺服器、實際載入畫面
  點擊測試，才抓到真正原因，而且是兩個完全獨立的臭蟲疊在一起：

  1. **瀏覽器對 `<label>` 的原生「順便幫忙點一下裡面的表單控制項」行為**：
     只要點擊 `<label>` 裡任何地方，瀏覽器都會「順便」對裡面第一個可以
     當「標籤對象」的表單控制項（`<select>`、`<button>`…）補送一次
     click 事件。這個自訂元件（`multiselect_widget()`）本來整包包在
     `<label>` 裡，一開始只把 hidden 的 `<select>` 移出 `<label>`
     （為了解決「點擊欄位打不開選單」這個更早的版本就有的問題），但
     選了選項之後動態生成的標籤上有一顆「×」移除鈕（`<button>`，也是
     可以被 `<label>` 標記到的元素），點選單選項時瀏覽器又「順便」對
     這顆新長出來的 × 鈕補送一次 click，變成選了又立刻被移除、完全
     選不進去。**修法：整個自訂元件（含選單、標籤）都搬出 `<label>`
     之外**，改包在一個新的 `.field-block` `<div>`（樣式跟
     `.form-stack label`完全一樣，純粹只是換掉標籤本身避免這個原生
     行為），文字說明另外用 `<div class="field-block">文字...{{
     multiselect_widget(...) }}</div>` 包起來。
  2. **「行政區」這個 hidden 的 `<select name="district">` 一開始渲染
     出來完全沒有 `<option>`**（因為行政區選項要看縣市選了什麼才知道，
     伺服器端渲染不出固定清單），JS 只有 `syncNativeSelect()` 負責切換
     「已經存在的」`<option>` 的 `selected` 屬性，但從來沒有任何地方
     真的去新增 `<option>` 元素——導致就算畫面上看起來選好了行政區、
     `selectedValues.district` 這個 JS 變數也是對的，**實際負責表單
     送出資料的那個 `<select>` 永遠是空的**，同仁選了行政區、送出後
     資料根本不會帶到。這個問題比較隱蔽，因為畫面上完全看不出異狀，
     要直接檢查 `#native_district` 的 `selectedOptions` 才抓得到。
     修法：`onCitySelectionChanged()`（縣市選擇一有變動就會呼叫）現在
     會先把 `native_district` 底下的 `<option>` 元素整組重新生成（照
     `FIELD_OPTIONS.district`），再呼叫 `syncNativeSelect('district')`
     去打勾。

  這件事的教訓記錄下來：**這類「點擊互動、動態表單控制項」的 JS
  功能，光靠伺服器端渲染 HTML 字串比對沒辦法測出瀏覽器裡的真實行為，
  之後遇到類似的自訂互動元件（不是單純表單填寫送出），應該先用
  Playwright 實際跑一輪點擊互動再回報完成**，不能只看 Jinja2 渲染
  結果或用肉眼讀 JS 程式碼判斷「邏輯應該沒問題」。
- **排版對不齊的問題（2026-09-09，使用者截圖回報「排版歪掉了」）**：
  每一欄的說明文字長度不一樣——有些欄位說明文字有加括號補充說明，會
  換成兩行（例如「申請送審同仁 *（目前登入者，鎖定不可更改）」「縣市 *
  （可複選，選了才會出現對應行政區）」），有些欄位說明文字很短，一行
  就結束。CSS Grid 預設會把同一列裡每一欄都撐到跟最高的那一欄一樣高，
  但欄位內部是由上往下排列、貼齊頂端，不會把說明文字置中——結果同一
  排裡，說明文字只有一行的欄位，底下的輸入框/選單位置會比兩行說明
  文字的欄位「高」一截，整排的框看起來歪一邊。修法：把每個欄位的
  說明文字都包一個新的 `.field-caption`（`delivery/static/style.css`），
  固定保留兩行的高度，不管文字實際上是一行還是兩行，底下的輸入框/
  選單就都會對齊在同一個高度。用 Playwright 檢查過每一排的輸入框
  上緣座標，確認同一排的框現在都在同一個高度上，沒有再歪掉。
- **必填欄位：2026-09-09 使用者實測後主動要求改成全部必填，不再照抄
  現有表單「畫面標 * 但沒真的擋」的行為**。剛上線時是刻意比照現有表單
  的實際送出檢查（`handleJobSubmit()` 只真的擋了行業別/職務類別/職缺
  週期/縣市/行政區這五個多選欄位，「全/兼職」「外籍生」「負責所別」
  「班別」「休假方式」「領薪方式」雖然畫面標了紅色 `*` 但沒有真的被
  擋）；使用者測試後明確表示**「除了『備註說明』跟『上傳圖檔』，其他
  所有項目都要設定為必填，沒填不能送出」**，這是使用者主動要求的行為
  變更（不是系統自己加的簡化），所以現在：
  - 全部 11 個多選欄位（含原本沒真的擋的那六個）在 `job_listing_routes.py`
    的 `_REQUIRED_MULTI_SELECT_FIELDS` 都改成必填，沒選任何一項送出時
    會被伺服器擋下、顯示錯誤訊息。
  - 前端也同步補上檢查：多選欄位背後負責送出資料的 `<select multiple>`
    是 `hidden` 的，瀏覽器對「沒有被渲染出來」的欄位不會做 `required`
    檢查（就算加了 `required` 屬性也一樣不會擋、也不會跳提示），所以
    純靠 HTML5 `required` 沒有用，改成表單 `submit` 事件裡用 JS
    （`REQUIRED_MULTISELECT_FIELDS`）自己檢查，沒選滿就 `preventDefault()`
    並跳出視窗告知漏填哪些欄位（中文欄位名稱），檔下按鈕變成
    「送出中」的動作，避免看起來像已經送出。
  - 畫面上原本沒有標 `*` 的那六個欄位（全/兼職、外籍生、負責所別、班別、
    休假方式、領薪方式）現在也補上 `*`，跟「現在真的必填」的狀態一致。
  - 「備註說明」跟「職缺圖檔上傳」維持選填，沒有變動。
- **「工作內容(對內)/(對外)」欄位排版改成左右並列＋輸入框拉高
  （2026-09-12 使用者截圖回報這兩個欄位文案通常很長，原本上下疊放、
  輸入框只有 3 行高很難編輯）**：改成外面包一個新的 `.job-desc-grid`
  （`delivery/static/style.css`，內部 `grid-template-columns: 1fr 1fr`，
  螢幕寬度 720px 以下改回上下疊放，避免手機被擠成兩個過窄的欄位），
  `<textarea>` 的 `rows` 從 3 拉到 9（約 3 倍高）。用 Playwright 截圖
  確認桌面版兩欄並排、輸入框明顯變高，手機版正確改回上下疊放，沒有
  被擠壞版面。
- 圖檔上傳做法跟薪資補款一致（20MB 上限、點擊或拖曳上傳、縮圖預覽），
  另外多了「維護既有職缺」時要把既有圖檔網址帶進 `existing_image_url`
  隱藏欄位，沒換新圖就沿用舊網址（照抄 GAS 的
  `fields.existing_image_url || ''` 邏輯）。
- 送出後同一套「確定沒送到用 error、不確定有沒有處理完用 unknown」的
  防重複送出邏輯（見上面薪資補款章節的說明），這裡原封不動比照辦理。

**已知的小缺口（不影響核心功能，誠實記錄）**：GAS 的 `GET_JOBS` 端點
可以接收一個 `userId`（LINE ID）參數，用來判斷「無主職缺」（Notion 上
刊登人欄位是空的舊資料）要不要出現在系統管理員的搜尋清單裡——材霈
平台這邊目前沒有接這段（要拿到同仁在職缺系統的 LINE ID，需要另外查
`job_portal_sso.py` 同步的 Firestore 資料，目前這個查詢介面還沒有
提供），所以**「無主職缺」目前不會出現在材霈平台這邊的搜尋清單裡**，
同仁自己刊登、或轄下同仁刊登的職缺完全不受影響，真正送出異動時 GAS
還是會用它自己查到的 LINE ID 重新判斷一次權限，不影響資料安全性。

**部署上不需要新的環境變數**——沿用薪資補款表單已經設定好的
`JOB_PORTAL_GAS_WEBAPP_URL`（同一支 GAS 程式、同一個網址，只是換一個
`type` 欄位），設定過一次就對兩個表單都生效，這次不用再麻煩使用者
重新設定一次。

**使用者實測前要做的事**：到 `/accounts` 頁面幫需要用到職缺維護的
帳號勾選「職缺維護」這個模組的權限（跟開通 `/salesdev`／`/hr` 等其他
模組是同一個畫面同一種做法），才會在 `/portal` 看到「職缺維護」卡片。

**2026-09-10：使用者實測確認完成。** 過程中還有兩輪小修正：
- 必填欄位改成使用者要求的「除了備註說明、職缺圖檔上傳，其他全部必填」
  （見上面「必填欄位」那一條的更新說明），以及同一列輸入框因說明文字
  換行長度不一致造成的排版對不齊問題（見上面「排版對不齊的問題」）。
- 這次排版修好、部署成功後，使用者一度回報「電腦版一樣排版還是歪的」
  （手機版正常）——追問後確認是瀏覽器快取了部署前的舊版 `style.css`，
  請使用者強制重新整理（Ctrl+Shift+R）後就正常了，不是程式碼本身的
  問題。**教訓記錄下來**：往後每次靜態檔案（CSS/JS）改完部署後，如果
  使用者回報「畫面沒變」或「看起來還是舊的」，第一步應該先請對方強制
  重新整理或開無痕視窗排除瀏覽器快取，再進一步查程式碼，避免把一個
  其實已經修好的問題，誤判成程式碼還有 bug 而重新除錯一輪。

職缺維護功能到此正式完成。

### 專案合約維護（`/project-contracts`）：2026-09-10 已實作，方案 A 模式

跟職缺維護、薪資補款送出表單同一套「方案 A」：材霈平台這邊收表單，
送出時原封不動轉手給職缺維護表單背後那支 GAS 程式（`type=SUBMIT_PROJECT`，
跟另外兩個表單共用同一個 `JOB_PORTAL_GAS_WEBAPP_URL`，不用使用者另外
設定新的環境變數），由 GAS 的 `ProjectWorkflowService.processProjectSubmission()`
繼續處理：檢查申請同仁是否已完成 LINE 綁定、寄送通知信給人資/財務單位、
把合約檔案存進 Google Drive、寫入「專案合約紀錄」分頁。材霈平台這邊
不重做這些邏輯。

**放置位置：使用者明確決定放成一個獨立模組**（跟職缺維護一樣），不是
放進「我的專區」`/me`（雖然這個功能本身沒有主管權限判斷、任何 LINE 綁定
過的同仁都能提報，性質其實比較接近薪資補款）。跟職缺維護一樣：
`platform_accounts.MODULES` 多一筆 `project_contracts`／「專案合約維護」，
`/portal` 卡片直接連到 `/project-contracts`，沒有獨立登入頁，任何有這個
模組權限的帳號（不分「專員」/「主管」角色）都能用。

**跟職缺維護不一樣的地方**：現有 Netlify 表單裡這個功能本來就只有「提報
新的一筆」，沒有「維護既有」這個模式（GAS 那邊也沒有對應的查詢/編輯
端點，`___.gs` 的 `doPost` 路由完全沒有 `GET_PROJECT` 這種東西），所以
這裡也只做提報表單，**沒有**額外做「查詢我提報過的合約」這種現有系統
沒有的功能——嚴格照抄現有範圍，不順手多加。

**實作內容**（`project_contract_routes.py` + `services/project_contract_submit_service.py`
+ `templates/project_contract_form.html`）：
- 欄位、選項、必填規則都照抄 `index_6.html` 的 `#sectionProject` 分頁與
  `ProjectWorkflowService.gs`：申請同仁姓名（鎖定為目前登入者）、廠商
  名稱、合作類別（下拉：派遣/代招/國際學生）、簽約模式（下拉：實支
  實付/一口價）、約訪專員、拜訪主管，全部都是真的必填（跟職缺維護不同，
  這裡現有表單的 `required` 屬性跟實際送出檢查是完全一致的，不需要
  另外處理「畫面標星號但沒真的擋」那種不一致）。
- 合約檔案上傳限定 PDF / WORD（.doc/.docx），20MB 上限，前後端都擋
  （`CONTRACT_FILE_ALLOWED_EXTENSIONS`/`CONTRACT_FILE_MAX_BYTES`），照抄
  現有表單 `handleContractFileChange()` 的檢查邏輯。
- **踩過一次職缺維護學到的坑，這次直接避開**：上傳合約檔案的
  `<input type="file" hidden>` 也是靠外層 `<label class="upload-zone">`
  觸發選檔（這個用法本身沒問題，是「自訂樣式檔案上傳鈕」的標準做法），
  但因為這個欄位是**必填**（職缺維護、薪資補款的圖片上傳都是選填，
  沒遇過這個狀況），一開始寫了 `required` 屬性，後來想到瀏覽器對
  `hidden`（`display:none`）的欄位不會做 `required` 檢查——這是職缺維護
  那次多選欄位踩過的同一個坑，這次還沒上線就先想到、拿掉沒用的
  `required` 屬性，改在表單 `submit` 事件裡自己判斷 `fileInput.files`
  是否有選檔，沒選就 `preventDefault()` 並跳出提示，不用等使用者
  實測回報才發現。有用本機 Playwright 驗證：完全空白送出時瀏覽器
  自己的必填檢查會先擋下（文字/下拉欄位），把文字/下拉都填完但沒選
  檔案時，換成這裡補的 JS 檢查跳出提示；選了檔案後才會真的送出。
- 送出後同一套「確定沒送到用 error、不確定有沒有處理完用 unknown」的
  防重複送出邏輯（見薪資補款章節的說明），這裡原封不動比照辦理。
- 排版沿用 `.repayment-section`/`.repayment-basic-grid`/`.field-caption`
  等既有樣式類別（跟職缺維護共用，`.field-caption` 已經解決了「說明
  文字換行長度不一致造成同一列對不齊」的問題，這裡直接受惠不用再修）。

**部署上不需要新的環境變數**——沿用另外兩個表單已經設定好的
`JOB_PORTAL_GAS_WEBAPP_URL`。

**使用者實測前要做的事**：到 `/accounts` 頁面幫需要用到專案合約維護的
帳號勾選「專案合約維護」這個模組的權限，才會在 `/portal` 看到卡片。

## 小雞點數自費申請（`/chicken-points`）：2026-09-11 已實作，全新功能

**這是「職缺維護表單整合案」以外的一個全新獨立功能**，不是搬既有系統
的東西過來，材霈平台以前也沒有這個功能——同仁自費購買「小雞點數」時
要填一張紙本申請單（部門、購買月份、金額/點數、主任簽名、副理確認、
會計確認），使用者想把這張紙本單子改成線上填寫＋手指/滑鼠電子簽名。

**決策過程值得記錄，因為推翻了原本以為「跟其他表單一樣可以套方案 A」
的假設**：一開始討論以為可以比照職缺維護/薪資補款「轉送給外部 GAS
系統」的方案 A 做法，但深入了解後發現：
1. 這是全新表單，外部職缺維護 GAS 系統完全不認識它，沒有 `SUBMIT_開頭`
   的對應處理邏輯可以轉送。
2. 材霈平台自己完全沒有「推播 LINE 訊息給指定某一位同仁」的機制（現有
   LINE 推播都是固定發到某個群組，不是一對一）、帳號資料裡也沒有存
   LINE ID、也完全沒有串接 Google Drive。
3. 若要做到「主任在 LINE 收到通知、按按鈕就核准」，同一個 LINE 官方
   帳號同時間只能有一個訊息接收端（webhook）——職缺維護那個帳號的
   接收端目前是 GAS，材霈平台要嘛用「連結＋網頁按鈕」的折衷做法、要嘛
   用另一個材霈平台自己控制的官方帳號、要嘛就是把整套 LINE 登記/審核
   邏輯搬進材霈平台（也就是使用者之前提過、還沒排時間的「方案 B」）。

**使用者最後決定大幅簡化範圍**，不做審核：
- 「主任簽名」「副理確認」「會計確認」三欄全部拿掉，**只保留「本人
  簽名」**。
- 送出即完成，**沒有審核流程、不寄信、不用 LINE 通知任何人**——會計
  自己登入 `/chicken-points` 查看清單即可。
- 表單內容＋簽名合成的圖片**存進材霈平台自己的 Firestore**（不接
  Google Drive，使用者明確表示「先不要接 Drive」）。

**這是目前平台裡第一個「專員」/「主管」角色真的有差別的模組**：專員
登入後只看得到自己送出過的申請紀錄；主管（例如會計的帳號設定成
`admin`）看得到全部同仁的申請紀錄，方便對帳——`chicken_points_routes.py`
的 `chicken_points_home()` 用 `platform_accounts.module_role()` 判斷，
不是只看 `has_module_access()`。

**實作內容**（`chicken_points_routes.py` + `services/chicken_points_service.py`
+ `templates/chicken_points_form.html` + `templates/chicken_points_home.html`）：
- 表單欄位：申請部門（固定選單，沿用職缺維護「負責所別」的所別清單）、
  申請同仁（鎖定為目前登入者）、購買月份、點數（同仁輸入）、應扣金額
  （系統依固定比例 0.65 元/點自動換算，不讓同仁自己填金額，避免算錯）。
- **簽名＋表單合成一張圖，完全在瀏覽器端用 `<canvas>` 完成**，故意不用
  伺服器端圖片處理套件（例如 Pillow）——這個平台原本沒有裝任何圖片
  處理套件，為了這個小功能多加一個相依套件不划算，瀏覽器 canvas API
  就能做到同樣效果，見 `chicken_points_form.html` 的 `composeSignedImage()`：
  簽名板本身是另一個獨立的 `<canvas>`（處理手指/滑鼠畫線，含觸控裝置的
  座標換算），送出前再另外開一個「輸出用」canvas，把表單欄位文字跟
  簽名板的內容畫在同一張圖上，轉成 PNG 的 base64 字串才送給伺服器，
  伺服器只負責存檔，不再處理一次圖片。用 Playwright 實際模擬滑鼠畫線、
  截出送出前合成好的圖片人工核對過排版正確。
- 合成圖直接存進 Firestore 文件的一個欄位（`signed_image_base64`）。
  Firestore 單一文件有 1MB 上限，這裡的合成圖正常情況下只有幾十 KB
  （幾行文字＋一條簽名線），伺服器端另外擋一個遠高於正常情況、但足以
  防止異常巨大檔案的字串長度上限（`_MAX_SIGNED_IMAGE_BASE64_CHARS`）。
- 沒有審核流程，所以也沒有前兩個表單那套「error/unknown/success」三態
  防重複送出邏輯——這裡送出成功與否只取決於材霈平台自己的 Firestore
  寫入有沒有成功，不涉及呼叫外部系統，天生就沒有「不確定對方有沒有
  處理完」這種模糊地帶。

**已知的取捨（不是疏漏，是使用者明確選擇的簡化範圍）**：
- 沒有審核、沒有通知——如果之後同仁反映「看不到有沒有人送出申請」，
  可以再加 Email 通知會計（沿用專案合約維護那套寄信做法），或者之後
  真的排到「方案 B」時，可以順便補上 LINE 一對一推播。
- 記錄清單目前沒有分頁——如果送出的量變大，`chicken_points_home.html`
  一次把所有紀錄（含每一筆的完整合成圖）都嵌進同一個網頁，頁面會越來越
  肥大，屆時需要加分頁或「只顯示縮圖、點了才載入完整圖」，目前使用量
  還小，先不處理。

**踩過的雷（2026-09-11）**：使用者實測回報「點擊縮圖後畫面是空白的」。
原因是縮圖點擊事件原本寫 `onclick="window.open(this.src)"`，而
`this.src` 是一個 `data:image/png;base64,...` 網址（合成圖直接存在
Firestore 裡，不是一個真正的檔案網址）——Chrome 基於防止 data: 網址
被拿來做釣魚攻擊的考量，會擋掉「`window.open()` 直接導覽到一個 data:
網址」這個動作，結果就是跳出一個空白的 `about:blank` 分頁，圖片完全
沒有顯示出來，瀏覽器主控台也不會噴出明顯的錯誤訊息，不容易看出原因。
修法：改成先用 `window.open('')` 開一個空白分頁，再用
`document.write()` 把「裡面包著 `<img src="data:...">`」的網頁內容寫
進那個分頁——這樣瀏覽器看到的是「載入一張圖片資源」，不是「導覽到
data: 網址」，就不會被擋下。用 Playwright 實際點擊縮圖、確認新分頁裡
的 `<img>` 真的有載入圖片（不是只看網址變了）才驗證過這個修法有效。

**部署上不需要新的環境變數**——完全是材霈平台自己的 Firestore，不涉及
任何外部系統或密鑰。

**使用者實測前要做的事**：到 `/accounts` 頁面幫需要用到小雞點數自費
申請的同仁帳號勾選「小雞點數自費申請」這個模組的權限；**負責查看全部
紀錄的會計帳號，角色要選「主管」**（不是「專員」），才看得到所有同仁
的申請紀錄，其餘一般同仁選「專員」即可，只會看到自己的紀錄。

**2026-09-11 追加：新增刪除功能，只有「主管」角色能刪。** 使用者要求
「只有主管或最高管理權限才可以刪」——因為 `platform_accounts.module_role()`
對全平台管理員一律回傳 `ROLE_ADMIN`（見該函式說明），「主管」角色跟
「最高管理權限」在這個模組的權限判斷上本來就是同一件事，直接沿用既有
的 `is_admin_view` 判斷即可，不用另外加邏輯。實作重點：
- 新增 `chicken_points_routes._require_admin_access()`，在
  `_require_access()`（有沒有這個模組的權限）之外再多檢查一次角色是不是
  `ROLE_ADMIN`，不是「專員」就導回 `/chicken-points`——**伺服器端真的會
  擋，不是只有前端把刪除按鈕藏起來而已**（畫面上「專員」角色本來就看
  不到刪除按鈕，這裡是防止有人直接對刪除網址送 POST 繞過畫面）。
- 刪除按鈕只在 `is_admin_view` 為真時渲染那一整欄（含表頭），刪除前
  跳出瀏覽器原生 `confirm()` 二次確認（列出申請人/月份/點數，跟
  `/accounts` 刪除帳號同一種寫法），避免手滑點到。
- 沒有「軟刪除」或回收桶的概念，`delete_request()` 是直接刪掉 Firestore
  文件，刪掉就真的沒了——這是使用者這次要的就是單純的刪除功能，沒有
  額外要求可復原，所以沒有多做。

**2026-09-11 追加修改：申請部門改成自動帶入，不用同仁自己選。**
使用者測試前反映「申請部門直接幫我改成跟建立帳號內的部門相同就好了，
不用讓人員選了」——`platform_accounts.py` 的帳號資料裡本來就有一個
`department`（部門）自由文字欄位（`/accounts` 編輯帳號畫面「部門」那一
欄），先前一直「只是先存起來，還沒有功能會用到」，這是第一個真的用到
它的功能。改法：
- 表單不再顯示「申請部門」下拉選單，改成跟「申請同仁」一樣的鎖定唯讀
  欄位，直接顯示 `user.department`。
- 拿掉原本 `DEPARTMENT_OPTIONS` 固定清單（職缺維護負責所別那份選單），
  因為帳號的部門是自由文字，不受那份清單限制。
- **如果同仁的帳號還沒有設定部門**（`department` 是空字串——很有可能
  發生，因為這個欄位以前沒有功能在用，很多帳號可能從來沒填過），表單
  會整個不顯示，改顯示提示訊息請同仁聯絡平台管理員到「帳號管理」幫忙
  補上部門，不能送出——避免存進一筆部門空白、會計對帳看不出是哪個
  部門的紀錄。**這代表這個功能上線後，使用者可能需要先巡一輪
  `/accounts`，把還沒填部門的帳號補齊，同仁才能順利送出申請**。

## 帳號權限管理（`/accounts`）依部門分組＋拖曳排序：2026-09-11 已實作

使用者要求：帳號權限管理頁面依部門排序顯示，並且可以在**同一個部門內**
手動拖曳調整順序，拖完要記住（下次重新整理、換人登入都要維持排好的
順序）。討論過程中順便定案：部門欄位之後**改成必填**（原本是選填、
自由填寫、沒有功能在用；小雞點數自費申請上線後已經是第一個用到它的
功能，這次帳號權限管理頁面依部門分組顯示是第二個）。

**最終定案的規則**（動工前跟使用者逐項確認過）：
- 畫面先依「部門」分組，部門與部門之間用標題列隔開；部門區塊本身的
  先後順序是部門名稱的字母/筆畫順序（不能手動調整部門區塊的順序）。
- 同一個部門內的人，**拖曳只能在自己部門的區塊內**，不能拖到別的部門
  區塊——因為每個部門各自是獨立的 `<table>`／`<tbody>`，天生就不會拖
  到別的部門去，不需要額外的邊界檢查。
- 拖曳完立刻用背景請求存檔（`POST /accounts/reorder`），不用整頁重新
  整理，也不用「儲存」按鈕。
- 部門內排序規則：**有手動拖曳排過序的人排在前面**（依存下來的排序
  數字），**還沒被拖曳排過的人排在後面**、依姓名排序——這樣新加入
  這個部門的帳號（一定還沒有排序數字）就會自然排在該部門最後面，符合
  使用者「新帳號預設排最後面」的要求，不用另外寫「新帳號插入哪裡」的
  邏輯。

**實作內容**：
- `platform_accounts.py` 帳號資料多一個 `sort_index` 欄位（整數，沒排過
  是 `None`）。`list_accounts()` 排序邏輯改成：先比部門名稱、再比「有沒有
  排序數字」（有的排前面）、最後比排序數字或姓名。新增
  `reorder_department(department, ordered_usernames)`：用 Firestore
  batch write 把一個部門內的帳號依畫面上拖好的新順序寫回
  `sort_index`（0, 1, 2, …）；**只會更新真的屬於這個部門的帳號**，
  其餘（不相干的 username、或剛好被改了部門的帳號）一律忽略，不完全
  信任前端送來的內容。
- `accounts_routes.py`：`accounts_list()` 用 `itertools.groupby()` 把
  `list_accounts()` 回傳的（已排序）扁平清單切成 `[(部門, [帳號,...]), ...]`
  給樣板畫分組列表；新增 `POST /accounts/reorder`（收 JSON body
  `{department, usernames}`，驗證格式後呼叫 `reorder_department()`）；
  新增/編輯帳號的表單驗證都加上「部門不能空白」。
- `templates/accounts_list.html`：改成每個部門各自一個 `<table>`（標題
  用 `.repayment-section-title` 樣式），每個帳號一列都有 `draggable="true"`
  ＋一個「⠿」把手（純視覺提示，實際拖曳事件綁在整列上）。拖曳互動是
  原生 HTML5 Drag and Drop API（`dragstart`/`dragover`/`dragend`），沒有
  另外引入任何 JS 套件——`dragover` 即時把拖曳中的那一列插到滑鼠所在
  位置的前後，`dragend`（放開滑鼠）時把當下畫面上的順序整包送到
  `/accounts/reorder`。用 Playwright 的 `dragTo()` 實際模擬拖曳動作
  （不是只呼叫 JS 函式），確認畫面順序真的改變、送出的 JSON payload
  也正確，才確認這個功能沒問題——單純呼叫函式沒辦法測出 HTML5 原生
  拖曳事件實際串起來有沒有效。
- `templates/account_form.html`：「部門」欄位加上 `required`，說明文字
  更新成「帳號權限管理會依部門分組顯示」。
- `delivery/static/style.css` 新增 `.drag-handle`／`tr[draggable="true"].dragging`
  等排版樣式（拖曳中該列變半透明、加上品牌色背景，給使用者明確的
  視覺回饋）。

**部署上不需要新的環境變數。**

### 下一步

薪資補款送出表單、職缺維護都已經完成並經使用者實測確認；專案合約維護、
小雞點數自費申請都已完成實作，等待使用者實測確認。「職缺維護表單整合
案」排定的下一項是「LINE 登記流程重建」（跟「方案 B：薪資補款核准流程
整段搬進來」綁在一起做，目前還沒排時間）。

動工任何剩餘功能之前，維持一樣的老規矩：跟使用者要一次真正的前端
原始碼（HTML/JS，不是只有截圖或 `.gs` 後端程式碼），欄位名稱、必填
規則、選項清單才能真的做到「完全比照」，不要用猜的——但如果是像小雞
點數這種全新功能（沒有現成原始碼可以照抄），要先確認清楚審核流程、
通知機制、資料存放位置這幾個關鍵設計決定，再動工，避免做完才發現
方向不對。

**2026-09-11 使用者提出的未來考量，記錄下來備查**：目前 `platform_accounts.py`
的帳號權限是「每個部門模組各自一個角色（專員/主管）」這種扁平設計
（`modules[code] == "staff"/"admin"`），跟「部門」（`account.department`，
自由文字、不是正式的組織階層資料，目前給小雞點數自費申請、帳號權限
管理頁面分組顯示這兩個地方用）是兩個互不相關的欄位。使用者表示：之後如果功能持續擴充，**可能
會需要依「部門」或「職級」做更細緻的權限控管**（例如某個部門的主管
只能看到自己部門的資料，看不到別部門的）——目前的帳號資料結構還沒有
支援這種「依部門/職級動態決定看得到什麼」的設計，之後如果真的有這類
需求，需要重新設計帳號的權限欄位（例如把 `department` 從自由文字改成
正式的組織單位資料、或是在 `modules` 的角色之外再加一層依部門/職級的
細粒度規則），不是現在的模組層級權限可以直接支援的，屆時要當成一個
獨立的架構調整項目來規劃，不要在某個小功能裡面順手加一小塊拼裝上去。

## 派遣契約產生器（/dispatch-contracts）

**背景**：材霈是派遣公司，跟派遣員工的勞動契約主條文（法律文字）不會變，
但工作地址/內容、班別薪資、休假/加班/服裝押金這類條件會隨指派的客戶不同
而不一樣，有的客戶一次還有好幾種不同班別/薪資。原本專員每次都要手動改
Word 檔，這個功能讓專員填一個表單，自動套版產生 Word 契約檔下載，同時
系統自己存一份紀錄，之後同客戶要用可以直接重新下載。

**這個功能刻意不處理的事**（2026-09-11 使用者明確要求，設計討論全程見
本次對話紀錄，這裡只記結論）：
- **不處理特定一位派遣員工的資料**。契約範本裡「公司/客戶/員工」相關的
  ``${com_full_name}``／``${cus_name}``／``${rec_name}``／``${rec_id}``／
  ``${rec_rdate}``／``${pap_sign}``／``${com_leader}``／``${com_id}``／
  ``${com_address}`` 這 9 個欄位是材霈另一套簽署系統的既有公式（那套
  系統會自動讀取簽約公司跟客戶名稱），這個功能完全不去讀取或取代它們，
  套版時原封不動留在輸出的 Word 檔裡。也就是說**一次填表單＝產生某個
  客戶的「契約模板」，不是某個人的專屬契約**，同一個客戶要用在多個人
  身上都可以重複下載同一份。
- 不寄信、不通知任何人、沒有審核流程，跟小雞點數自費申請是同一種
  「材霈平台自己收自己存」的模式，不經過外部 GAS 系統。

**套版技術**：用 `docxtpl`（Jinja2 語法 `{{ }}`／`{% %}`，刻意跟既有的
``${...}`` 系統公式是完全不同的括號寫法，兩者不會互相誤判/衝突）。

- Master template：`assets/dispatch_contracts/master_template.docx`，是拿
  使用者提供的真實契約範本（pchome 客戶那份）重新整理過的版本——**所有
  固定法條文字、所有 `${...}` 公式都跟原本一字不差**，只有「工作地址/
  內容/薪資班別表格/條文段落」這個原本區塊改成上下堆疊的清楚版面（原本
  是同一列裡「班別」跟「條文說明」用合併儲存格交錯排在一起，這種排版
  沒辦法在「班別列數本身會變動」的前提下安全維持，所以刻意重新排版，
  文字內容不變，只是版面調整）。**如果之後這份範本本身要改版（例如
  法條文字更新），要直接編輯這個 docx 檔案裡的 Jinja 標籤段落，不要
  用「文字取代」去改，容易破壞 Word 內部的 XML 結構。**
- **⚠️ 踩過的雷（2026-09-12 修正）**：範本裡「上述派遣同意書我皆已詳閱且
  願意配合派遣工作，並遵守上述規定，本人親筆簽名：`${pap_sign}`」這段
  （在文件裡出現兩次，第二條後面一次、文件最後面一次），原本的真實範本
  是用「段落框線」（`<w:pBdr>`，上下左右都是實線）把這段文字圍成一個
  方框，中間留一行空白，讓另一套簽署系統可以把簽名圖檔貼進這個方框裡。
  第一版整理 master template 時，這個框線在過程中被拿掉、三段合併成一段
  純文字，**文字內容一字不差，但視覺上的簽名框不見了**——這種「文字沒
  變、格式被拿掉」的問題光看文字比對是看不出來的，使用者實際用 Word
  打開範本才發現。已修正：兩個位置都復原成「標題段落＋空白段落＋置中
  `${pap_sign}` 段落」三段、都套用跟原本一致的 `<w:pBdr>` 框線，讓
  Word 自動把相鄰同框線的段落合併顯示成一個方框，跟使用者提供的原始
  範本外觀一致。**之後如果又要調整這個 master template，凡是原本範本裡
  有框線／底色／特殊排版的區塊，即使只是移動或重新排版文字，都要額外
  確認這些視覺格式有沒有跟著保留，不能只比對文字內容有沒有一致。**
- 班別薪資表格是**通用 5 欄**（職稱/工作時間/時薪/工時獎金/加班），用
  `docxtpl` 的 `{%tr for %}`/`{%tr endfor %}` 列迴圈語法讓列數可以是
  任意筆數。**踩過的雷**：`{%tr %}` 這個指令會把它所在的**整個 `<w:tr>`
  列**替換成裸的 Jinja 語法，如果跟 `{{ }}` 資料欄位擠在同一列，那些
  資料欄位會被一起吃掉、整份文件變成缺一整段——for 迴圈的起始/結束
  標籤跟真正的資料列，一定要分開放在三個不同的 `<w:tr>`（見
  `services/dispatch_contract_service.py` 頂端的說明、`main` 分支
  commit 訊息裡有更完整的除錯過程）。
- 專員可以勾選這次契約要用到通用 5 欄裡的哪幾欄（不同客戶可能只要
  「職稱+時薪」兩欄，也可能 5 欄都要）——**沒被勾選的欄位，輸出表格
  該欄一律顯示「－」，不會把整欄從 Word 表格拿掉**（動態拿掉表格欄位
  在 OOXML 裡做起來非常脆弱，用「－」表示不適用是同樣清楚、風險低
  很多的做法，這是刻意的簡化；如果之後真的需要「這次契約表格根本沒有
  這一欄」而不是「這一欄顯示－」，要再回來另外設計）。
- 條文段落拆成 5 段（休假/請假制度、加班/津貼/團保說明、服裝儀容/門禁卡/
  押金規定、福利說明、到職準備事項），每段有預設標準文字（抄自使用者
  提供的真實範本，見 `services/dispatch_contract_service.py` 的
  `CLAUSE_DEFAULTS`），專員可以直接沿用或依這次客戶需求修改——不是
  寫死不能改的文字。

**資料存放**：Firestore collection `dispatch_contracts`（`services/
dispatch_contract_service.py`），GCS 存產生的 Word 檔（
`dispatch_contract_storage.py`，blob 路徑前綴 `dispatch_contracts/`，
**沿用跟其他模組同一個 `DELIVERY_GCS_BUCKET` 環境變數，不用另外設定**）。
客戶名稱沒有另外建主檔，表單的自動完成是從最近的送出紀錄反查不重複的
客戶名稱（`list_recent_client_names()`），先用歷史紀錄就夠用。

**權限**：模組代碼 `dispatch_contracts`（`platform_accounts.MODULES`），
跟 `/project-contracts`／`/job-listings` 一樣不分「專員」/「主管」角色，
兩者都能建立契約，體驗完全一樣。

**⚠️ 可見範圍（2026-09-11 修改）**：原本任何有這個模組權限的帳號都能看到
「全部」同仁產生過的紀錄，方便互相接手同一個客戶。使用者確認後明確要求
收斂：**現在只有送出者本人、送出者的主管（`platform_accounts` 的
`manager_usernames`）、或是全平台管理員（`is_platform_admin`）能看到某筆
紀錄**，跟這筆紀錄無關的其他同仁完全看不到（列表頁看不到那一列，也不能
用網址直接下載/預覽）。核心邏輯是
`services/dispatch_contract_service.py` 的 `can_view_submission()`／
`list_visible_submissions()`，`dispatch_contract_routes.py` 的列表
（`GET /dispatch-contracts`）、下載（`.../download`）、預覽
（`.../preview`）三個路由都走同一個判斷，避免只擋列表頁、卻能猜網址
下載別人紀錄的漏洞。**如果同仁互相接手客戶的需求之後又出現，要處理
「同仁 A 手上的客戶交給同仁 B」的情境，可以用 `manager_usernames` 這條
主管關係，或是另外設計一個「轉交」的功能，不建議直接改回「全部都看得
到」。**

**Word 排版預覽**（2026-09-11 新增，使用者明確要求「做法二」——不只列表
頁看到填了什麼，要能直接看到真正的 Word 排版）：產生契約的同時，額外用
LibreOffice（`soffice --convert-to pdf`）把 Word 轉成一份 PDF 存進 GCS，
`/dispatch-contracts` 列表頁每一筆紀錄多一個「預覽」連結，點開用瀏覽器
內建的 PDF 檢視器直接顯示（`Content-Disposition: inline`，不是強制下載）。

這一步刻意設計成失敗容錯：LibreOffice 在這次**開發用的沙盒環境**裡，
不管轉什麼檔案都會失敗（錯誤訊息是「source file could not be loaded」，
排查過不是權限或已知的沙盒 socket 限制問題，比較像是那個沙盒環境本身的
LibreOffice 安裝有缺陷，跟這裡的程式碼寫法無關）。轉檔函式
`convert_docx_to_pdf()`（`services/dispatch_contract_service.py`）失敗時
回傳 `None`，呼叫端就不會存 PDF、列表頁那筆紀錄的「預覽」欄位顯示「－」，
但 **Word 檔案的產生/下載/存檔完全不受影響**，這件事不會讓整個功能掛掉。
如果之後這個功能又出現「預覽」連結看不到內容的狀況，去 Cloud Run 的 log
找 `[派遣契約 PDF 轉檔失敗]` 開頭的訊息，裡面會有實際的失敗原因。

**✅ 2026-09-11 上線後使用者實測確認：正式環境（Cloud Run）的 LibreOffice
轉檔運作正常，「預覽」連結能正確顯示 PDF 排版**——證實了開發階段的判斷：
那個轉檔失敗只是開發沙盒環境本身的安裝問題，Cloud Run 用 `apt-get` 重新
安裝的 LibreOffice 沒有這個問題。這件事也順便印證一個經驗：**這個專案的
開發沙盒環境，不能拿來當作「LibreOffice／需要系統層級套件的功能，在正式
環境也會失敗」的證據**，真的要確認還是得看部署後的實測結果。

**上線前要做的事**：
1. **不需要新的環境變數**（沿用既有的 `DELIVERY_GCS_BUCKET`）。
2. 到 `/accounts` 幫需要用這個功能的帳號開通「派遣契約產生器」模組權限。
3. 建議正式上線前先用一份真實客戶的條件實測一次，下載出來的 Word 檔
   打開確認排版跟 `${...}` 公式都正常，再開始正式使用。

**測試涵蓋範圍**：延續既有分工——`build_shift_rows()`（純函式，欄位
勾選/空白列判斷邏輯）、`render_contract_docx()`（會讀本地 assets 檔案跟
套版，但不碰 Firestore/GCS，歸類為可以直接測的部分，涵蓋系統公式不被
動到、變動欄位有正確代入、班別列數正確、條文段落預設值/覆寫）、
`convert_docx_to_pdf()`（mock 掉 `subprocess.run`，因為真正的 `soffice`
在開發/CI 環境不一定裝得起來或能正常運作，涵蓋轉檔成功、逾時、找不到
執行檔、輸出檔案不存在這幾種失敗容錯路徑）都有完整單元測試；路由層
測試未登入導向、表單驗證錯誤訊息、成功送出時呼叫順序跟下載檔頭、PDF
轉檔成功/失敗時 `pdf_blob_path` 有沒有正確存檔、預覽路由的 404/inline
內容（mock 掉 Firestore/GCS/LibreOffice）。真正的 Firestore/GCS 讀寫、
LibreOffice 轉檔在正式環境是否真的成功、以及產出的 Word 檔案在真正的
Microsoft Word 裡打開排版是否正常，留給有憑證的環境／使用者實測。

## 配送部「假別登記」大改版：加時數、法定額度自動試算、90% LINE 提醒：2026-09-11 已實作

原本的假別登記只有「開始日期／結束日期」兩個日期欄位，假別只有病假/事假/
特休/其他 4 種，沒有時數概念，也完全沒有法定額度、累積用量這些資訊。這次
是使用者主動提出的需求（「先不要實作，跟我討論」開頭，經過多輪確認細節
後才動工），把整個假別登記改成「一天一筆＋記時數」，並且依勞基法自動
試算各假別的年度法定上限、累積使用量，達到 90% 時主動推播 LINE 提醒。

### 需求確認過程中定案的規則（很重要，之後如果要調整這個功能要先想清楚會不會牴觸）

- **登記表單三個欄位**：假別、申請日期（單一日期，不是起訖區間）、申請
  時數（數字，可以有小數，例如 4.5 小時）。**不是「一段期間請幾天」，是
  「一天一筆」**——同仁請 3 天假，就要登記 3 筆記錄，各自對應到那一天
  實際請的時數。
- **假別擴充成勞基法完整清單（13 種）**：特休、事假、病假、婚假、喪假、
  生理假、家庭照顧假、公假、產假、產檢假、陪產（檢）假、育嬰留職停薪、
  其他。詳細的天數上限、額度週期規則都寫在 `delivery/config.py` 的
  `LEAVE_TYPES` 註解裡（含法條依據），這裡不重複列一次，改規則請直接去
  那邊看／改。
- **額度週期**：特休用「到職週年制」（從到職日算，每年到職紀念日重新
  歸零一次）——這是使用者在確認需求時特別要求「用週年制就好」，其他
  所有假別一律用「曆年制」（每年 1/1~12/31）。兩制混用是使用者明確
  要求的，**不是不一致，不要「順手」統一成同一種**。
- **工時換算**：1天＝8小時（`WORKDAY_HOURS`），登記時數統一用小時記錄，
  換算成天數只在畫面顯示、額度比對時計算，不影響存檔格式。
- **家庭照顧假的額度是「疊在事假額度裡」算**：家庭照顧假本身有自己的
  7天上限，但依法規這 7 天同時要算進事假 14 天的額度消耗（不是各自
  獨立的 14+7=21 天）。程式碼裡用 `shares_quota_with` 這個欄位＋
  `repository._quota_pool_codes()` 實作這種「用量互相疊加，但各自還有
  自己的上限」的邏輯，其他假別目前都沒有這種疊加關係。
- **公假／其他／育嬰留職停薪這三種沒有法定天數上限**，`quota_basis` 是
  `None`，不會出現在「年度假別累積」表格裡，也不會被 90% 提醒掃到——
  **育嬰留職停薪是使用者明確要求**「幫我能讓同仁登記 但不用計算天數」，
  所以雖然同仁在表單上看得到、選得到這個選項，送出後不會被算進任何
  額度統計，這是刻意的，不是漏做。
- **90% 提醒沒有「已提醒過」的排除機制**：跟既有的「文件到期提醒」
  不同（到期提醒有 `REMINDER_RESEND_INTERVAL_DAYS` 幾天內不重複提醒的
  設計），假別額度提醒是**只要累積使用還在 90% 以上，排程每次執行都會
  再推播一次**——這是使用者明確說的「達到90%後每次都要提醒」，不是
  忘記加防重複機制。
- **推播目標沿用既有的 `DELIVERY_LINE_REMINDER_TARGET`**：使用者說的
  「配送部的提醒群組(同車輛管理的群組)」，經過翻程式碼確認，指的就是
  現有文件到期提醒在用的同一個 LINE 群組（`delivery_reminder_routes.py`
  裡本來就有的推播目標），系統裡沒有另外存在一個獨立的「車輛管理群組」
  ID，不用另外去找或新增。
- **到職日期欄位只加在「人員詳細頁」，不加在任何新增人員的表單裡**：
  配送部有兩個建立人員資料的流程（①「新增人員」表單、②應徵名單頁面
  按「錄取」直接轉正式人員），使用者確認這兩個當下都還不確定實際報到
  日，所以到職日期改成事後在人員詳細頁的「一鍵全部更新」表單裡自己
  補（跟身分證字號、email 等欄位一樣的做法），**不會**卡在新增/錄取
  流程裡強制填寫。

  **2026-09-12 補充**：③ CSV 批次匯入（`/delivery/import`）**例外**，
  補上選填的「到職日期」欄位——使用者說明批次匯入主要是拿來整批搬遷
  已經在職的舊資料，這種情境下到職日通常是已知的，跟①②「當下還不
  確定報到日」的情境不一樣，所以不牴觸上面那條設計原則。CSV 裡這欄位
  留空一樣合法（沿用「事後到人員詳細頁補」這條路），格式看不懂（不是
  `2024-01-31` 或 `2024/01/31`）的話該列會被判定失敗、不會用錯的日期
  硬建進去。

### 已知限制（技術上無法避免，不是這次做壞了）

- **舊格式假別紀錄不會被算進累積額度**：改版前的假別登記只存了
  `start_date`/`end_date`，沒有 `hours`（也沒有單一 `leave_date`），
  沒辦法回頭幫這些舊紀錄「發明」出一個時數。所以舊紀錄在「假別查詢」
  列表裡還是看得到（`repository.sick_leave_record_date()` 會自動退回
  用 `start_date` 顯示／篩選／排序），但**不會**被算進年度累積使用量、
  也不會影響 90% 提醒的判斷——上線那天之後新登記的紀錄才會被正確
  累計。如果需要精確的歷史額度，只能靠人工回頭補登記（不建議，直接
  往後從上線那天開始算即可）。
- **額度試算靠「廠商＋姓名」比對人員資料，不是靠固定的人員 ID**：假別
  登記表單從一開始設計就只填「廠商」「人員姓名」（自由輸入文字），
  沒有存對應到人員清單的 `personnel_id`，這是既有架構的限制，不是這次
  新增功能造成的。如果同一個廠商底下剛好有兩個同名同姓的在職人員，
  系統只會抓到「其中一筆」在職人員的到職日去試算特休額度，這種情況
  目前無法自動分辨（機率很低，先不處理，真的遇到再個案處理）。

### 程式改動內容

- `delivery/config.py`：`LEAVE_TYPES` 從 4 項擴充成 13 項，每項多了
  `quota_basis`（`"anniversary"`／`"calendar"`／`None`）、`quota_days`、
  `shares_quota_with` 這幾個欄位；新增 `LEAVE_TYPE_LOOKUP`（依代碼查整包
  資料，取代原本的 `LEAVE_TYPE_MAP` 用在需要完整假別設定的地方）、
  `ANNUAL_LEAVE_MAX_DAYS`（30）、`WORKDAY_HOURS`（8）、
  `LEAVE_QUOTA_ALERT_RATIO`（0.9）。
- `delivery/repository.py`：
  - `create_sick_leave()` 參數從 `start_date`/`end_date` 改成
    `leave_date`/`hours`。
  - 新增 `sick_leave_record_date()`，統一處理新舊格式的日期欄位。
  - 新增一整節「假別額度計算」：`compute_annual_leave_days()`（勞基法
    第38條年資級距換算特休天數）、`_add_years()`（處理閏年 2/29 邊界）、
    `years_of_service_at()`（算到某一天為止的累積年資）、
    `leave_period_and_quota()`（回傳某個假別「目前所在額度週期」的起訖
    日跟上限天數）、`_quota_pool_codes()`（家庭照顧假／事假的疊加額度
    邏輯）、`leave_quota_summary_for_person()`（算一個人每種有上限假別
    的年度累積使用量）、`find_personnel_by_name_vendor()`（用廠商+姓名
    反查人員資料，拿到職日期）、`list_leave_quota_alerts()`（掃全部在職
    人員，抓出達 90% 的名單，給排程端點用）、`update_personnel_hire_date()`。
- `delivery/routes/sick_leave_routes.py`：登記表單改吃 `leave_date`/
  `hours`（時數要驗證是大於 0 的數字，可以有小數）；查詢頁在同時篩了
  「姓名」＋「廠商」時，額外算出並帶入這個人的「年度假別累積」表格
  資料（`quota_summary`/`quota_person_name`）。
- `delivery/routes/reminder_routes.py`：新增
  `POST /delivery/api/leave-quota-reminder-check` 端點，驗證方式跟既有
  的 `/api/expiry-reminder-check` 一樣（共用同一組
  `DELIVERY_REMINDER_SECRET`），呼叫 `list_leave_quota_alerts()` 組出
  LINE 訊息推播。
- `delivery/routes/vendor_routes.py`：`bulk_update_personnel()` 多處理
  一個 `hire_date` 表單欄位，比照既有的 email／身分證字號等欄位的
  「有送才更新」寫法。
- `delivery/excel_export.py`：`build_sick_leave_workbook()` 的欄位改成
  「申請日期」「時數」，對舊格式紀錄一樣用 `start_date ~ end_date` 的
  文字格式退回顯示。
- 模板：
  - `templates/sick_leave_form.html`：兩個日期欄位改成「申請日期」
    （單一日期）＋「申請時數」（`<input type="number" step="0.5">`）。
  - `templates/sick_leave_records.html`：列表欄位改成「申請日期」＋
    「時數」（舊格式紀錄退回顯示 `開始日期 ~ 結束日期`）；篩選了姓名＋
    廠商時，額外多一張「年度假別累積」表格，顯示每個有上限假別的
    額度週期、已用天數、法定上限、使用比例（達 90% 以上那一列會特別
    標色提醒）；如果這個人還沒補到職日期，特休那一列就不會出現，並
    顯示提示文字請去人員詳細頁補。
  - `templates/personnel_detail.html`：「一鍵全部更新」表單的篩選列
    新增「到職日期」欄位（`<input type="date" name="hire_date">`）。
- `tests/test_delivery_repayment_sickleave.py`：既有的假別篩選測試改用
  新的 `leave_date`/`hours` 欄位，另外補上舊格式相容性測試；新增大量
  純函式單元測試（特休年資級距換算、週年制/曆年制額度週期、家庭照顧假
  疊加事假額度、舊格式紀錄被正確排除在累積之外等），共 44 個測試案例，
  加上既有的其他測試，整個 repo 目前 652 個測試全部通過。

### 使用者接下來要手動處理的事

1. **回頭幫在職同仁補「到職日期」**：到 `delivery/` 系統 → 廠商底下的
   人員清單 → 點進某位同仁的「人員詳細頁」→ 在最上面的篩選列會看到新的
   「到職日期」欄位，選好日期後按「一鍵全部更新」送出即可。**這一步
   很重要**——沒有補到職日期的同仁，特休額度沒辦法試算（其他假別的
   額度不受影響，因為是曆年制不需要到職日）。
2. **設定新的 Cloud Scheduler 排程，觸發 90% 假別額度提醒**：這一步
   需要在 GCP Cloud Shell 執行（`gcloud` 指令），流程比照既有的「文件
   到期提醒」排程，差別只在呼叫的網址不同：
   ```bash
   gcloud scheduler jobs create http delivery-leave-quota-reminder \
     --location=asia-east1 \
     --schedule="0 9 * * *" \
     --uri="https://recruitment-bot-xxxxxxxxxx.asia-east1.run.app/delivery/api/leave-quota-reminder-check" \
     --http-method=POST \
     --headers="X-Delivery-Reminder-Secret=你目前設定的 DELIVERY_REMINDER_SECRET 值" \
     --time-zone="Asia/Taipei"
   ```
   - `--uri` 那一行要把 `https://recruitment-bot-xxxxxxxxxx.asia-east1.run.app`
     換成你們 Cloud Run 服務實際的網址（跟既有的到期提醒排程用同一個
     網址，只有路徑最後一段從 `/api/expiry-reminder-check` 換成
     `/api/leave-quota-reminder-check`）。
   - `--headers` 那一行的密鑰要跟 Cloud Run 服務目前設定的環境變數
     `DELIVERY_REMINDER_SECRET` 完全一樣的值（既有的到期提醒排程本來
     就有設定這個環境變數，直接照抄同一個值即可，**不用新增環境
     變數**）。
   - `--schedule="0 9 * * *"` 是「每天早上 9 點」，可以自己改成想要的
     時間（cron 格式）。
   - **怎麼確認設定成功**：到 GCP 主控台的 Cloud Scheduler 頁面，找到
     `delivery-leave-quota-reminder` 這個工作，點「強制執行」手動觸發
     一次，確認執行結果是「成功」；如果目前剛好有同仁的某個假別使用量
     達到 90% 以上，配送部的提醒 LINE 群組應該會馬上收到一則新訊息。
3. **告知配送部同仁新的登記方式**：登記假別時要改成「一天一筆」，例如
   請 3 天特休，要登記 3 筆（不是像以前選「開始日期～結束日期」登記一
   次），每一筆填當天實際請的時數（一般整天就是 8 小時）。

## 新增：合約產生器（/client-contracts）

**背景**：材霈業務跟客戶公司簽的「人力派遣服務合約書」，甲方（客戶公司）
條件、乙方（材霈旗下派遣公司）資料、合約期間、撤換條款、匯款日、費率
每次都不一樣，原本要靠專員手動改 Word 檔。這個功能讓專員填一次表單，
套進固定的服務合約範本，產生 Word 契約檔下載，同時系統存一份紀錄。

**⚠️ 這份合約跟「派遣契約產生器」（/dispatch-contracts）是完全不同的兩份
文件，兩邊資料互不相通**：
- `/dispatch-contracts`：材霈跟**個別派遣員工**的契約，範本裡有另一套
  簽署系統要讀的 `${...}` 公式，這個功能完全不碰那些欄位。
- `/client-contracts`（這次新增）：材霈（乙方，旗下某一家派遣公司）跟
  **客戶公司**（甲方）之間的企業對企業服務合約，沒有 `${...}` 系統公式，
  是一份完全獨立的合約範本。

### 甲乙雙方資料怎麼來

- **乙方（材霈旗下派遣公司）**：從 `/companies` 公司主檔用下拉選單挑，
  直接帶出名稱/代表人/統一編號/電話/地址。材霈旗下有 10 家派遣公司牌照，
  不同客戶合約可能用不同牌照公司簽約，所以做成選單而不是寫死一家。
  **`/companies` 因此新增了「地址」欄位**（`platform_companies.py` 的
  `FIELDS`）——**上線前要麻煩使用者到 `/companies` 幫現有 10 家公司補上
  地址**，沒補地址的話，用那家公司當乙方產生出來的合約，地址那格會是
  空白。
- **甲方（客戶公司）**：專員輸入公司名稱或統一編號、按「查詢」，依序查
  「經濟部商工行政資料開放平臺」（統編精準查詢）、「g0v 公司資料庫」
  （名稱或統編皆可查）這兩個免費外部服務（`services/company_registry_
  lookup.py`），查得到就自動帶入名稱/代表人/地址/統一編號（電話這兩個
  服務都沒有，一律手動填），查不到就手動輸入全部欄位。**這兩個都是
  免費外部服務，不是材霈付費訂閱的正式 API，理論上未來可能改版或關閉
  （2026-09-12 使用者明確同意這個失敗容錯做法）**——這支檔案在開發沙盒
  環境裡沒辦法連線測試（沙盒的出口網路政策不開放任意外部網域），跟
  LibreOffice PDF 轉檔在沙盒失敗是同一種狀況，不代表正式環境連不出去，
  **上線後要麻煩使用者用一個已知的客戶統編/名稱實際測試一次，確認查得
  到資料**；查不到的話這個功能一律安全退回讓同仁手動輸入，不會擋住
  合約產生流程。

### 合約版本與其他欄位

- **合約版本**：這是第一版「時薪一口價」（`services/client_contract_
  service.py` 的 `CONTRACT_VERSIONS` 字典，代碼 `hourly_flat_rate`）。
  員工薪資／管理費由專員自行填入單一數值（附件一費率表），不做多列
  費率表。**之後如果材霈需要「實支實付」等其他計費模式的合約範本，
  只要在 `CONTRACT_VERSIONS` 加一個版本代碼＋準備對應的 master
  template 檔案就好，不用重做整個資料結構**——這次刻意把
  `contract_version` 存進每筆紀錄就是為了這個擴充性。
- **合約期間**：簽約日期一填，合約起始日在網頁上會自動帶入同一天（JS
  做的，同仁還是可以手動改）；**合約結束日預設是「使用這個功能當下」
  那一年的 12/31**（`default_contract_end_date()`），同仁可以再手動改。
- **第十七條撤換條款**：撤換人員的通知期限預設 3 天，可改；資遣費用及
  預告工資由「甲方」或「乙方」負擔，做成單選（`severance_payer`），
  範本原文這裡固定寫「乙方」，現在依這次合約談判結果決定。
- **匯款截止日**：預設「次月 10 日前」，可改。

### 技術細節

- Master template：`assets/client_contracts/master_template.docx`，用
  `docxtpl` 套版（跟 `/dispatch-contracts` 同一套技術，但完全獨立的
  範本檔案）。原始範本（使用者提供的真實合約，`XX有限公司`/`瑋政有限
  公司` 那份）用 `python-docx` 直接改寫成套版用的 Jinja 標籤——這份範本
  沒有像派遣契約那樣「大量 fill-in-blank 底線格式的日期」需要保留視覺
  樣式，日期欄位改成一次套一整串「115年01月01日」這種民國紀年字串
  （`roc_date_string()`），比逐字元套版簡單也更不容易出錯。
- **踩過的雷**：原始範本裡「爰甲乙雙方於 ... 所簽署之合約(合約期間於
  民國 ... 至 ... 止)」這段的日期，Word 底線格式把「114」「年」「07」
  「月」「28」「日」拆成十幾個獨立的 run，逐一手動比對 run 索引才能正確
  套版，稍有算錯就會漏改或改錯地方——如果之後要調整這個 master
  template 裡任何「有底線/特殊格式」的日期或數字段落，**務必先用
  `python-docx` 把該段落所有 run 的文字內容跟索引都印出來確認過，
  不要憑印象猜**。
- **共用轉檔工具**：`convert_docx_to_pdf()` 的實際邏輯搬到
  `services/docx_pdf_conversion.py`，跟 `/dispatch-contracts` 共用同一支
  轉檔工具（原本兩邊各自維護一份幾乎一樣的 LibreOffice 轉檔程式碼，這次
  順便合併成一份，避免以後改一邊忘了改另一邊；`/dispatch-contracts` 的
  `convert_docx_to_pdf()` 現在只是呼叫這支共用工具的一層薄包裝，對外
  行為完全沒變）。
- **資料存放**：Firestore collection `client_contracts`（`services/
  client_contract_service.py`），GCS 存產生的 Word/PDF 檔
  （`client_contract_storage.py`，blob 路徑前綴 `client_contracts/`，
  **沿用既有的 `DELIVERY_GCS_BUCKET` 環境變數，不需要新增環境變數**）。
- **權限與可見範圍**：模組代碼 `client_contracts`，跟 `/dispatch-
  contracts` 一樣只有送出者本人、送出者的主管、或全平台管理員看得到
  某筆紀錄（2026-09-12 使用者明確要求，一開始就採用收斂後的權限模型，
  不是先做全部互看再事後收斂）。
- **Word 排版預覽**：跟 `/dispatch-contracts` 一樣，產生 Word 檔的同時
  另外轉一份 PDF 存起來，列表頁可以直接內嵌預覽，失敗容錯設計相同
  （轉檔失敗不影響 Word 檔案本身）。

### 跟「專案合約維護」（/project-contracts）的串接

使用者要求：同仁在合約產生器產生的合約，能在專案合約維護表單直接挑選
帶入，不用重新打一次資料、重新上傳檔案。實作方式：

- `/project-contracts` 表單多一個「從合約產生器帶入」選單，列出目前這個
  帳號在合約產生器那邊看得到的紀錄（同一套可見範圍：送出者本人/主管/
  平台管理員）。
- 選了之後，前端 JS 自動帶入：廠商名稱（＝甲方公司名稱）、合作類別
  預設「派遣」、簽約模式依 `CONTRACT_VERSIONS` 對應（這次的「時薪一
  口價」對應到既有的「一口價」選項），並且用 `fetch` 把合約產生器存的
  Word 檔抓下來，透過 `DataTransfer` 塞進原本的檔案上傳欄位——瀏覽器
  基於安全限制不能直接用 JS 幫使用者「選好一個檔案」，但可以把已經
  抓到的檔案用這個方式放進 `<input type="file">`，效果一樣，使用者事後
  仍然可以手動點擊換成別的檔案（例如實際要送的是另一份已簽名/掃描過的
  版本）。
- **後端完全不用另外處理檔案**，走的還是 `/project-contracts` 原本
  multipart 上傳的同一條路，只是多接收一個 `from_client_contract_id`
  欄位。
- 送出成功後，如果表單帶了 `from_client_contract_id`，會標記那筆合約
  產生器紀錄「已送出專案合約維護」（`mark_sent_to_project_contracts()`，
  存一個時間戳），列表頁會顯示「已送出」，避免同仁不小心對同一份合約
  重複送出——送出前會先確認這個帳號真的看得到那筆紀錄，避免竄改表單
  欄位去標記別人的紀錄。
- 如果使用者手動選了別的檔案取代自動帶入的那份，前端會把
  `from_client_contract_id` 清空，送出後就不會誤標記。

### 使用者接下來要手動處理的事

1. **到 `/companies` 幫現有 10 家公司補上「地址」欄位**——這是這次新增
   的欄位，之前沒有資料，乙方選了地址是空白的那些公司需要補齊。
2. **到 `/accounts` 幫需要用這個功能的帳號開通「合約產生器」模組權限**
   （模組代碼 `client_contracts`）。
3. **上線後找一個已知的客戶（知道正確統編或全名）實際測試一次「甲方
   查詢」**，確認至少其中一個外部資料來源查得到，或確認查不到時手動
   輸入的路徑也正常運作。
4. **測試「從合約產生器帶入」這個串接**：先用合約產生器產生一份測試
   合約，再到 `/project-contracts` 確認選單看得到那筆紀錄、選了之後
   廠商名稱/合作類別/簽約模式/檔案都有正確帶入，送出後回合約產生器
   列表頁確認那筆紀錄顯示「已送出」。

### 2026-09-12 更新：新增「實支實付」版本、檔名加合約年、複製功能

使用者拿到實際用過的「實支實付」合約範本，要求新增第二個合約版本，
另外三個小修改：

1. **檔名（含存進 GCS 的名稱）加上「合約年」**：`合約_{甲方名稱}_
   {年份}.docx`／`.pdf`，年份是**合約起始日期的年份**，不是同仁送出
   表單當下的年份——這是刻意的，因為使用情境常常是年底先產生下一年度
   的續約合約，檔名要能一眼看出是哪一年的合約。實作見
   `client_contract_routes.py` 的 `_build_filename()`（產生新檔案用）／
   `_contract_year()`（下載/預覽既有紀錄時，從存好的
   `contract_start_date` 反查年份）。
2. **費率表拿掉「元/hr」單位文字**：時薪一口價版本的員工薪資/管理費
   套版結果改成乾淨的數字（例如 `200`，不是 `200元/hr`），表單輸入框
   的提示文字（`placeholder="如：200"`）維持不變，這兩件事是分開的
   （提示文字是表單體驗，元/hr 是產出的 Word 檔內容）。
3. **新增「實支實付」合約版本**（`services/client_contract_service.py`
   的 `CONTRACT_VERSIONS["actual_paid"]`）：跟「時薪一口價」共用完全
   相同的甲乙雙方欄位/合約期間/撤換條款/匯款日排版跟 Jinja 標籤，**只有
   附件一報價表格結構不一樣**——薪資/加班費/法定項目（勞保/健保/退休金/
   二代健保）/員工福利（團保/特休/其他法定假別）全部固定寫「實支實付」，
   只有「服務費－全程派遣」那一格是空白的 `service_fee` 欄位讓專員自行
   填寫（例如「人員薪資的15%」）。**因為兩個版本共用主文結構，master
   template 存成兩個獨立檔案**（`assets/client_contracts/
   master_template_hourly_flat_rate.docx`／`master_template_actual_
   paid.docx`），不是同一份範本裡切段落，做法是先把時薪一口價那份
   範本的正確結構準備好，再把使用者提供的實支實付報價表格整個
   （含合併儲存格）搬進去換掉原本的簡單報價表格，其餘完全不動。
4. **「複製」功能**：`/client-contracts` 列表頁每筆紀錄多一個「複製」
   連結，導到 `/client-contracts/new?duplicate_from={id}`，把那筆紀錄
   的全部欄位（含合約版本、報價）預先帶入新增表單——年底要用同樣條件
   續下一年度合約時用。**日期不會自動加一年**，同仁還是要自己把簽約
   日期/合約起訖日期改成新的年度，這是刻意的（避免猜錯使用者實際要的
   日期，比自動加一年更保險）。只能複製自己看得到的紀錄（跟其他權限
   判斷同一套 `can_view_submission()`）。

**「台灣公司網」（twincn.com）查詢——查證後使用者決定不加**：使用者原本
要求甲方公司資料查詢也加上這個來源，查證後發現這個網站**沒有公開的
API**，只有網頁（HTML）可以查，要串接的話只能用網頁爬蟲（scraping）
——這比現有的兩個資料來源（政府開放平台的 API、g0v 的 API）多一層風險：
網站改版就會讓爬蟲失效，而且爬取商業網站的資料可能有使用條款上的疑慮。
跟使用者說明「加連結讓同仁手動查」跟「做爬蟲自動帶入」這兩個選項後，
**使用者選擇維持現狀，不加這個來源**（現有經濟部商工開放平台＋g0v
公司資料庫兩個來源夠用）。之後如果又有需求，直接回來看這段說明，不用
重新查證一次。

### 2026-09-12 更新：刪除功能、新增第三個合約版本「白領代招」

1. **刪除功能**：`/client-contracts` 列表頁每筆紀錄多一個「刪除」按鈕
   （`POST /client-contracts/{id}/delete`），確認要作廢的合約可以直接
   整筆刪掉——刪除會把 Firestore 那筆紀錄跟 GCS 上存的 Word/PDF 檔案
   一起清掉（`client_contract_storage.delete_file()`，路徑空字串或檔案
   本來就不存在都安靜跳過，不影響整筆刪除），能不能刪一樣走
   `can_view_submission()` 那套可見範圍判斷，沒有另外設更嚴格的權限。
   **刪除沒有回收機制，是真的整筆刪掉，不是標記隱藏，點下去前端會先
   跳一個確認對話框**（`confirm()`），但沒有「復原」這個選項，誤刪只能
   重新填一次表單產生新的一筆。

2. **新增第三個合約版本「白領代招」**（`services/client_contract_
   service.py` 的 `CONTRACT_VERSIONS["white_collar_referral"]`）：使用者
   提供一份真實的「人力代招服務合約書」（乙方是祥舜人力資源有限公司，
   已經存在 `/companies` 公司主檔，不用另外新增）。**這個版本跟前兩版
   （時薪一口價／實支實付）不是附件一報價表格代換的關係，而是整份合約
   主文（第一條～第九條，條文結構跟用字）都完全不一樣的另一份合約書**，
   所以是完全獨立建立的 master template（`assets/client_contracts/
   master_template_white_collar_referral.docx`），沒有共用前兩版的 Jinja
   標籤或段落。
   - **沒有獨立的「簽約日期」欄位**：原始合約書末尾的簽署日期跟合約
     起始日期是同一天，所以這個版本不收 `sign_date`，套版時直接拿
     `contract_start_date_roc` 當作簽署日期。
   - **沒有撤換條款**：原始合約書沒有「撤換人員」「資遣費用由誰負擔」
     這件事，所以這個版本不收 `replace_notice_days`／`severance_payer`。
   - `CONTRACT_VERSIONS` 用兩個新的布林旗標
     `requires_sign_date`／`requires_severance_clause` 標記每個版本各自
     需不需要這兩組欄位（時薪一口價／實支實付都是 True，白領代招都是
     False），表單頁面（`client_contract_form.html`）用同一份資料
     （`data-requires="sign_date"`／`"severance_clause"` 屬性）決定要不要
     顯示對應欄位，後端驗證（`client_contract_routes.py`）也用同一組
     旗標決定要不要擋。
   - 這個版本自己專屬的報價欄位：`fee_amount`（招募及代辦服務費，自由
     文字，例如「二千五百元整」，不像前兩版是純數字）、`service_months`
     （附件一報價表「收取時間不超過幾個月」，**預設 12 個月，使用者
     明確要求做成可填欄位而不是寫死**）。
   - **原始範本第四條原文提到「附件二」（甲方自行招募委託乙方代招的
     服務費用），但實際上沒有附件二的內容**——**使用者明確決定直接把
     提到附件二的那句話從條文裡拿掉**，不是留空白附件二、也不是另外
     問使用者附件二內容，這個版本的合約書條文裡完全不會出現「附件二」
     字樣。
   - **跟「專案合約維護」的合作類別對應**：這個版本自然對應
     `COOP_CATEGORY_OPTIONS`（`services/project_contract_submit_
     service.py`）裡的「代招」，不是前兩版對應的「派遣」——`/project-
     contracts` 表單「從合約產生器帶入」選單原本 JS 寫死帶入「派遣」，
     這次一併改成讀 `CONTRACT_VERSIONS[版本代碼]["project_contract_
     coop_category"]`（時薪一口價／實支實付＝「派遣」，白領代招＝
     「代招」），不然帶入這個新版本的合約時合作類別會選錯。簽約模式
     （`project_contract_mode`）則對應到「一口價」——白領代招是固定
     金額的按月收費，性質上比較接近「一口價」而不是「實支實付」的
     實報實銷模式。

**這次沒有新的環境變數／部署步驟要處理**，master template 檔案跟其他
兩個版本一樣直接放在 repo 的 `assets/client_contracts/` 底下，`git push`
＋ Cloud Run 重新部署後就會生效，不需要另外上傳檔案或改設定。

### 2026-09-12 更新：新增第四個合約版本「台籍代招」

使用者又提供一份真實合約書，新增 `CONTRACT_VERSIONS["taiwanese_
referral"]`。這個版本跟剛新增的「白領代招」條文結構高度相似（同樣是
第一條～第九條、同樣沒有簽約日期／撤換條款），**但不是同一份文件，
細節上有好幾處差異，都是使用者提供的原始合約書本來就不一樣，不是
刻意做出來的變化**：

1. **服務費是佔薪資的百分比，不是固定金額**：白領代招是「每人每月X元
   整」的固定金額，台籍代招是「人員應領薪資的X%」——所以這個版本的
   報價欄位名稱刻意取成 `referral_fee_percentage`／`referral_service_
   months`，**沒有沿用白領代招的 `fee_amount`／`service_months`**。
   原因：這兩個版本的報價區塊在表單上是同時存在的兩個 `<div>`，只是
   用 CSS `hidden` 屬性切換顯示——瀏覽器的 `hidden` 屬性只影響畫面顯示，
   不影響表單送出，如果兩個版本共用同一個欄位名稱，送出表單時瀏覽器
   會把兩個同名欄位的值都送出，後端 `request.form().get()` 只會拿到
   其中一個（通常是先出現在 HTML 裡的那個），導致抓到錯誤版本的值。
   **之後如果再加報價欄位形狀類似的新版本，記得取一個不會撞名的新
   欄位名稱**，這是這次踩過的雷，已經寫進 `services/client_contract_
   service.py` 開頭的說明裡。
2. **收取時間上限預設 6 個月**（不是白領代招的 12 個月），一樣做成
   可填欄位——沿用「使用者要求做成可填欄位而不是寫死」的既有決定，
   套用到這個新版本上，因為原始範本裡「收取不超過六個月」這句話本來
   就跟白領代招的十二個月是完全獨立的一份文件內容，沒有理由假設兩個
   版本要共用同一個預設值。
3. **多一段附加條款**：附件表格多一列合併儲存格的「其他說明」，內容是
   「若代招人員任職期間未滿X個月，而發生本合約到期狀況，則應繼續計算
   服務費至人員任職滿X個月止」（原始範本這段是紅字，套版結果保留紅字
   視覺強調）——這句話裡的月數也跟著 `referral_service_months` 這個
   欄位變動，不是寫死「六個月」。
4. **管轄法院是臺灣新竹地方法院**，不是白領代招（跟另外兩個「人力派遣
   服務合約書」版本）共用的臺灣臺北地方法院——每個版本的管轄法院都是
   各自原始合約書上寫的，不是同一個值，之後如果要調整某個版本的管轄
   法院，只改那個版本自己的 master template，不會互相影響。
5. **第四條/第五條用字跟白領代招不同**：第四條沒有「附件一/附件二」的
   區分，只講「服務費用計算依附件計算之」（單一附件，這個版本原本就
   沒有附件二不完整的問題，不需要像白領代招那樣拿掉任何句子）；第五條
   的發票/匯款截止日固定數字（每月5日開發票、原文寫次月20日匯款）也
   跟白領代招（每月3日、次月10日）不一樣——匯款截止日一樣是共用的
   `remit_day` 欄位，同仁自己依實際合約條件填就好，這裡只是原始範本
   預設寫的數字不同，不影響欄位設計。
6. **乙方（材霈旗下派遣公司）**：這份範本的乙方是瑋政有限公司，跟
   時薪一口價／實支實付兩個版本共用同一家，`/companies` 已經有這筆
   資料，不用另外新增。
7. **合作類別對應**：跟白領代招一樣對應「代招」；簽約模式對應「實支
   實付」——因為報價是佔薪資百分比，性質上比較接近實支實付（依實際
   薪資結算），不是白領代招那種固定金額的「一口價」。

**這次一樣沒有新的環境變數／部署步驟**，master template 檔案直接放在
repo 裡，跟其他版本一樣走 `git push` ＋ Cloud Run 重新部署。

## 新增：部門主檔、職級制度，模組權限改成「開放/不開放＋職級」（2026-09-12）

使用者想把「內部編制」（部門、職級）跟帳號系統真正串起來，經過幾輪討論
定案的方向：**外部的職缺維護系統（跟 LINE Webhook 綁在一起，異動風險
高）這次完全不碰**；系統內部改成部門、職級都在帳號這邊統一管理，其他
地方（目前是四個真的用到「主管」角色的模組）都改成直接看這個帳號的
職級，不再各自獨立指定角色。

### 資料模型的異動

- **`platform_departments.py`（新檔案）＋ `/departments` 網頁**：部門
  清單從「帳號 `department` 欄位自由輸入文字」改成一份獨立的部門主檔，
  比照 `platform_companies.py`（材霈旗下派遣公司牌照主檔）同一種做法：
  只有全平台管理員（老闆本人）能新增/編輯/刪除/拖曳排序，之後材霈要
  加部門或改名稱，直接在 `/departments` 操作，不用改程式碼、不用重新
  部署。**帳號的 `department` 欄位存的還是這裡某一筆的「名稱」文字，
  不是外鍵 id**——這是刻意的簡化取捨：`chicken_points_routes.py` 送出
  自費申請時會直接把 `account["department"]` 這個字串存進申請紀錄本身
  （給會計對帳用），維持這個既有行為最單純的做法就是讓 `department`
  全程都是同一份字串，不用另外多一層 id 對應名稱的解析。**代價是**：
  如果之後在 `/departments` 把某個部門改名，已經設定成那個部門的帳號
  不會自動跟著變，需要另外到 `/accounts` 把那些帳號的部門重新選一次；
  刪除部門前會先擋下「還有帳號在用」的情況（`count_accounts_using_
  department()`），避免刪掉之後帳號的部門變成清單裡找不到的孤兒值。
  初始的 9 個部門（台北所(派遣組)/台北所(國際組)/新北所(派遣組)/
  新北所(配送組)/桃園所/台中所/高雄所/管理部/財務部）用 `scripts/
  seed_departments.py` 建立，已經存在的部門會跳過、可以放心重複執行。
- **`platform_accounts.py` 新增 `rank`（公司職級）欄位**：`RANKS` 清單
  由高到低是經理／副理／主任／副主任／專員，`MANAGER_RANK_THRESHOLD =
  "deputy_supervisor"`（副主任）決定門檻，`is_manager_rank()` 判斷一個
  職級代碼夠不夠格。這份清單跟門檻都是業務決定，不是技術限制，之後真的
  要調整層級或門檻，改 `platform_accounts.py` 這幾行常數就好，不用動
  任何模組的程式碼。
- **`modules` 欄位的資料格式改變**：以前存的是 `{"code": "admin"或
  "staff"}` 這種「模組→角色」字典，新增/編輯帳號時要針對每個模組各自
  選角色；改版後只存 `["code1", "code2", ...]` 這種「開放了哪些模組」
  的清單，`/accounts` 表單上模組欄位也從角色選單改成單純勾選「開放」。
  **模組裡算不算管理權限，改由這個帳號的職級決定**：`module_role()`
  回傳的形狀完全沒變（還是 `"admin"`/`"staff"`/`None`），只是內部判斷
  基準換掉——`has_module_access() and is_manager_rank(rank)`。這代表
  `delivery/auth.py`／`hr/auth.py`／`management/auth.py`／
  `chicken_points_routes.py` 這四個目前唯一真的用到「主管」角色的地方
  **完全不用改一行程式碼**，因為它們呼叫的都是 `module_role()`／
  `require_module_admin()`／`ROLE_ADMIN` 這些介面完全沒變的既有函式。
  其餘模組（合約產生器、派遣契約產生器、專案合約維護、少凱業務開發、
  職缺維護……）本來就只有「開放/不開放」的區別，沒有角色概念，完全
  不受影響。
- **舊資料相容，不需要跑遷移程式**：`_open_module_codes()` 讀取
  `modules` 欄位時，字典格式（舊）跟清單格式（新）都認得，一律轉成
  「開放了哪些模組」的清單；新增/編輯帳號一律用新格式存回去，代表帳號
  只要被存過一次（不管改的是誰），就自動轉成新格式，新舊格式並存一段
  時間也不會出錯，不需要另外跑一次性遷移腳本去轉檔。
- **`manager_usernames` 這次沒有被職級取代**：職級決定的是「這個人在
  某個模組裡算不算管理權限」，跟「這個人實際的主管是誰」是兩件事，
  後者還牽動 /me 的薪資補款紀錄可見範圍、合約產生器/派遣契約產生器讓
  主管看到部屬合約這幾個跟職級完全無關的功能，繼續維持獨立設定。
  **新增功能**：新增/編輯帳號選好部門之後，「所屬主管」欄位會自動
  預帶這個部門裡目前職級副主任（含）以上的帳號（`_department_
  managers()`），因為使用者確認過「同一個單位不會有兩個同職級的人」，
  這個自動預帶幾乎每次都有明確答案（部門有主任＋副主任就兩位都帶入、
  只有一位符合資格就帶那一位），只有新增的正是部門裡職級最高的人時才
  需要手動選——這只是「預帶」不是強制套用，選完之後同仁還是可以手動
  調整/清空。

### 「配送部系統」模組改名為「新北所(配送組)系統」

使用者要求部門清單裡「新北所(配送組)」對應到的既有配送部系統模組，連
使用者看到的顯示名稱也一起改——`platform_accounts.MODULES` 裡 `delivery`
這筆的 `name` 從「配送部系統」改成「新北所(配送組)系統」，`/portal`
卡片、`/accounts` 權限管理畫面都是直接讀這個值，自動跟著換；另外把
`delivery/templates/*.html` 裡每個頁面標題／`base.html` 的品牌文字裡
寫死的「配送部系統」字串批次改成新名稱。**模組代碼 `delivery`、網址
`/delivery/...` 都沒有變**，純粹是顯示給使用者看的文字，不影響任何
功能或既有連結。

### 這次特別確認過不受影響的功能

使用者特別要求職缺維護、薪資補款這兩個功能不能有任何異動：
- **職缺維護**（`job_listing_routes.py`／`job_portal_sso.py`）：頁面
  進入權限走 `has_module_access(account, "job_listings")`，這個函式
  的行為完全沒變（只是內部改讀清單格式的 `modules`），`job_portal_
  sso.py` 本身完全不碰 `modules`／`department`／`rank`，只用帳號的
  `name` 欄位跟外部職缺系統的 Google Sheet 姓名比對，這次的改版
  完全沒有動到它會用到的任何一段程式碼或資料格式。
- **薪資補款**（`me_routes.py`／`services/salary_repayment_service.py`）：
  `/me` 完全不做模組權限檢查（登入即可看，跟部門模組無關），可見範圍
  只看 `manager_usernames`（這次沒有被職級取代，見上面說明）跟帳號的
  `name` 欄位，一樣完全沒被這次改版動到。
- 全專案測試套件（含 `tests/test_job_listing_routes.py`／`tests/
  test_me_routes.py`／`tests/test_salary_repayment_service.py`／
  `tests/test_salary_repayment_submit_service.py`／`tests/test_job_
  listing_submit_service.py` 這五個檔案，共 71 筆）改版前後全數
  維持通過、一字未改，作為這個保證的驗證。

### 使用者接下來要手動處理的事（很重要，請儘快處理）

1. **到有 GCP 憑證的環境（Cloud Shell）跑 `python -m scripts.seed_
   departments`**，建立 9 個初始部門，之後才能在 `/accounts` 選部門。
2. **部署後，既有帳號的 `rank` 欄位都是空的**——`is_manager_rank("")`
   一律當作沒有管理權限，代表原本在配送部／管理部／人資／小雞點數
   自費申請這四個模組裡是「主管」的帳號，會**暫時失去管理權限**，
   直到你到 `/accounts` 幫他把職級設到副主任（含）以上為止。**建議
   部署後、開始逐一設定之前，先跑一次 `python -m scripts.report_
   accounts_for_rank_setup`**（純唯讀，不會寫入任何資料），這支腳本
   會列出每個帳號改版前是不是某個模組的主管，方便你決定職級要設多高
   ——只要某個帳號在 `/accounts` 存檔過一次，這支腳本就看不到他改版
   前的舊主管紀錄了，建議儘快、一次把清單跑完再開始逐一設定。
3. **到 `/accounts` 幫每個帳號補上部門（從新清單選）跟職級**，這是
   這次改版能不能正常運作的關鍵，麻煩儘快處理，尤其是原本有主管權限
   的帳號。
4. 確認「新北所(配送組)系統」這個新名稱在 `/portal`、`/accounts`、
   配送部系統本身各個頁面顯示都正確。

## 新增：派遣契約產生器補上刪除功能（2026-09-12）

使用者盤點過全站所有刪除功能有沒有確認視窗時，發現派遣契約產生器
（`/dispatch-contracts`）跟配送部補款登記這兩個地方本來就**沒有刪除
功能**（不是缺確認視窗，是根本沒有這個按鈕）。使用者確認只要幫派遣
契約產生器加上刪除，配送部補款登記先不動。

做法完全比照合約產生器（`/client-contracts`）已經有的刪除功能：
`POST /dispatch-contracts/{id}/delete` 把 Firestore 那筆紀錄跟 GCS 上
存的 Word/PDF 檔案一起刪掉，能不能刪走 `can_view_submission()` 那套
既有的可見範圍判斷（送出者本人／主管／全平台管理員），沒有另外設更
嚴格的權限；前端一樣先跳確認對話框（「確定要刪除「客戶名稱」這筆派遣
契約紀錄嗎？刪除後無法復原。」），刪除沒有回收機制。新增
`dispatch_contract_storage.delete_file()`、`services/dispatch_contract_
service.py` 的 `delete_submission()`，跟 `client_contract_storage.py`／
`services/client_contract_service.py` 的對應函式是同一種寫法。

## 新增：全平台管理員可以切換帳號視角（2026-09-13）

使用者的需求：「能讓最高權限管理員有一個功能請自己切換任何人帳號視角
嗎」——目前所有帳號都是老闆本人在維護，這個功能讓老闆不用知道對方密碼，
就能暫時用某個同仁帳號的視角看畫面（例如同仁反應「我這邊看到的權限
好像不對」，老闆可以直接切過去確認，而不是用猜的）。

**機制**：所有權限判斷都只看 `request.session["user"]`（見
`platform_accounts.current_account()`），所以「切換視角」只要換掉這個
session 欄位就好，不用改任何一支既有的權限檢查程式碼：

- `POST /accounts/{username}/impersonate`（`accounts_routes.py`）：只有
  `require_platform_admin` 擋得住。把目前的管理員帳號存進
  `session["impersonator"]`，`session["user"]` 換成目標帳號的最新資料
  （`platform_accounts.get_account()` 現查，不是拿列表頁快取的舊資料）。
  目標帳號如果本身也是全平台管理員則拒絕切換（避免以後多組管理員帳號
  互相切換的邊角案例，雖然目前只有一組管理員用不到）。
- `POST /impersonate/stop`（`login_routes.py`）：把 `session["user"]`
  換回 `session.pop("impersonator")`，回到 `/accounts`。沒有在切換視角
  中就直接呼叫，安靜跳過（不算錯誤）。
- **不會有巢狀切換**：切換視角後 `current_account()` 回傳的就是目標
  帳號（一定不是全平台管理員，因為上面擋住了），所以
  `require_platform_admin` 這一關本身就會擋住「切換到別人視角後，
  再切到第三個人視角」這件事，不用另外寫檢查邏輯。
- **畫面**：`templates/base.html` 最上面（`{% if request.session.get(
  'impersonator') %}`）顯示一條提醒橫幅「目前正在以「某某人」的視角
  檢視系統（您本人的帳號是「老闆」）」＋「停止檢視，返回我的帳號」
  按鈕，全站每一頁都看得到（不管在哪個模組，因為 Starlette 的
  `Jinja2Templates.TemplateResponse()` 本來就會自動把 `request` 塞進
  每個樣板的 context，不用每支路由額外傳一個變數進去）。因為切換後
  `user.is_platform_admin` 是 `False`，導覽列原本「帳號權限管理／部門
  管理／公司管理／廠商管理」這幾個管理員專屬連結會自動消失，不用另外
  處理。
- **列表頁**：`templates/accounts_list.html` 每個非管理員、非自己的
  帳號那一列多一個「檢視此帳號」按鈕（不用確認對話框，這不是刪除性
  操作，隨時可以按「停止檢視」退回來）。

**沒有新增環境變數，也沒有需要額外部署的步驟**——純粹是 session
（cookie）裡多存一個欄位，跟既有的登入機制共用同一套 `SessionMiddleware`，
`git pull` 之後照平常的部署流程重新部署 Cloud Run 就會生效。

測試：`tests/test_accounts_routes.py` 的 `ImpersonateAccountRouteTests`
（切換成功、目標帳號不存在、目標帳號是管理員時擋下來、`redirect` 已經
擋過時不重複判斷）、`tests/test_login_routes.py` 的
`StopImpersonationRouteTests`（正常換回來、沒在切換視角時安靜跳過）。

## 新增/編輯帳號表單：「所屬主管」改成下拉式多選＋只列主管級同仁（2026-09-13）

使用者反映：「所屬主管」欄位（`/accounts/new`、`/accounts/{username}/edit`）
原本是瀏覽器原生的 `<select multiple size="6">`，要按住 Ctrl/Cmd 才能
多選、清單裡連專員都列出來，不好用。改成兩件事：

1. **只列出職級副主任（含）以上的帳號**：一般專員本來就不會是別人的
   主管，列出來只會讓清單變長、更難找到真正要選的人。
   `accounts_routes.py` 的 `_account_form_context()` 用既有的
   `platform_accounts.is_manager_rank()` 過濾 `manager_options`（跟
   `_department_managers()` 自動預帶用的過濾邏輯一致，這兩處篩選規則
   本來就該一樣）。
2. **改成下拉式的標籤選取元件**（點一下才展開選單，選好的人顯示成
   一個個標籤），而不是常駐展開的清單框。做法比照
   `templates/job_listing_form.html` 既有的 `multiselect_widget()`
   （`.multiselect`／`.multiselect-box`／`.multiselect-menu`／
   `.multiselect-tag`／`.multiselect-option` 這幾個共用 CSS class），
   但這裡只有單一欄位，沒有沿用它那套多欄位共用的通用 JS 機制，另外寫
   一份只服務 `manager_usernames` 這一個欄位的精簡版（`account_form.html`
   底部的 `renderManagerTags()`／`renderManagerMenu()`／
   `toggleManagerOption()`）。可見的下拉框跟實際負責表單送出資料的
   `<select name="manager_usernames" multiple hidden>` 分開兩塊 HTML，
   刻意不用 `<label>` 包住隱藏的 `<select>`——原因跟 `job_listing_form.html`
   開頭註解說的一樣：`<label>` 包住表單控制項時，瀏覽器點擊會「順便」
   對它補送一次 click，跟自訂下拉框的「點外面關閉選單」判斷互相打架。
   選好部門後自動預帶主管人選的邏輯（`DEPARTMENT_MANAGERS`）維持原本
   行為不變，只是改成操作 JS 陣列＋重新畫标籤/選單，不再是操作
   `<option>.selected`。

`_manager_usernames_from_form()`（表單送出時讀取勾選結果）完全沒改，
因為隱藏的 `<select multiple>` 送出的資料格式跟原本一樣，`getlist()`
讀出來的東西沒有變。

測試：`tests/test_accounts_routes.py` 新增
`AccountFormContextManagerOptionsTests`（確認 `manager_options` 只含
副主任以上、且會排除自己）。

## 職缺維護頁面補上「重新載入 Notion 職缺」按鈕（2026-09-13）

使用者反映：職缺維護頁面（`/job-listings`）「維護既有職缺」模式底下的
搜尋清單，只有在頁面剛載入時打一次 `/job-listings/api/jobs` 抓資料
（`templates/job_listing_form.html` 底部 `loadMaintainableJobs()`，
頁面載入時自動呼叫一次），跟原本 Netlify 職缺維護網頁有一顆可以手動
重新整理清單的「重新載入 NOTION 職缺」按鈕不一樣——同仁如果剛送出一筆
新職缺、或別人剛好在 Notion／GAS 那邊異動過資料，想要不整頁重新整理
就馬上編輯到最新資料，原本沒有辦法。

做法：在「搜尋既有職缺」欄位下面加一顆「🔄 重新載入 Notion 職缺」按鈕，
點下去就是重新呼叫一次 `loadMaintainableJobs()`（跟頁面載入時自動呼叫
的是同一支函式，材霈平台這邊的 `/job-listings/api/jobs` 本來就沒有
快取，每次都是即時轉呼叫 GAS 的 `GET_JOBS`，見
`services/job_listing_submit_service.py` 的 `fetch_maintainable_jobs()`），
重新整理搜尋建議清單（`<datalist>`）跟可維護筆數提示文字，並新增按鈕
旁的載入狀態文字（載入中/已重新載入共 N 筆/載入失敗）。純前端變更，
沒有新增或修改任何後端路由或 API。

**已知限制（不是這次要處理的範圍）**：如果 GAS 那支 Web App 自己在
`GET_JOBS` 端點內部也有另外做快取（例如常見的 Google Apps Script
`CacheService`），這顆按鈕只能保證材霈平台這邊即時重新呼叫一次 GAS，
不保證 GAS 回傳的資料本身是不是即時的——這跟原本 Netlify 網頁上同一顆
按鈕的行為原理相同，材霈平台這邊沒有、也不需要另外處理 GAS 內部的
快取邏輯。

## 配送系統「人員狀況」補上刪除功能（2026-09-13）

使用者要求：配送系統的「人員狀況」（`/delivery/vendor/{vendor_code}`，
畫面標題是「{廠商} - 人員缺件狀況」）也要有刪除功能，之前只做了合約
產生器、派遣契約產生器的刪除，這裡本來完全沒有刪除功能（不是缺確認
視窗，是根本沒有這個按鈕）。

**先跟使用者確認了一個關鍵設計決定**：刪除要做成「永久刪除」還是
「隱藏但保留資料」？（`delivery/repository.py` 的人員文件其實本來就有
一個 `status` 欄位、所有查詢都已經在篩 `status == "active"`，但從來
沒有任何程式碼把它設成別的值——是現成、還沒接上的「軟刪除」掛鉤，改用
軟刪除完全不用碰任何既有查詢。）使用者選擇**永久刪除**，要跟合約
產生器／派遣契約產生器的刪除行為一致。

實作內容：
- `delivery/storage.py` 新增 `delete_entity_files(category, entity_id)`：
  人員一筆紀錄底下可能有好幾個各自獨立上傳的檔案（身分證影本、良民證、
  強制險等，存在 `documents` 子物件裡，每個應備項目各自一個
  `file_path`），不是單一欄位，所以用 `upload_file()` 存檔時就固定好的
  路徑前綴 `delivery/{category}/{entity_id}/` 一次列出、一次刪光，不用
  逐一項目讀 `file_path` 再各自刪一次。
- `delivery/repository.py` 新增 `delete_personnel(personnel_id)`：真的
  從 Firestore 刪掉那筆文件，沒有回收機制。
- `delivery/routes/vendor_routes.py` 新增
  `POST /delivery/personnel/{personnel_id}/delete`：**只有主管
  （`admin_required`）能刪**——這是這次額外做的權限決定：人員新增/
  編輯目前是任何有配送部模組權限的帳號都能做，但刪除是不可逆的重大
  操作，比照這個模組裡其他有實質後果的動作（核准補款、核准病假、結案
  事故都是 `admin_required`）收斂到主管才能做，跟合約產生器那種「送出
  者本人或主管才能刪」的可見範圍判斷是不同的權限模型，因為人員名冊是
  全部門共用的名單，不是某個人送出的個別紀錄。
- `delivery/templates/vendor_list.html`：主管登入時，每一列多一個
  「刪除」按鈕（`user.role == "admin"` 才顯示，跟這個模組其他主管專屬
  功能的樣板判斷式一致），送出前跳確認對話框。

測試：新增 `tests/test_delivery_personnel_delete.py`（`redirect` 已擋
過時不重複判斷、查無這筆人員安靜導回首頁、成功刪除時檔案跟紀錄都刪、
未知廠商代碼時退回首頁）。`delivery/storage.py`、`delivery/repository.py`
既有的 Firestore/GCS 讀寫函式在這個 repo 裡本來就沒有單元測試（留給
有 GCP 憑證的環境做整合測試），這次新增的 `delete_entity_files()`／
`delete_personnel()` 比照既有慣例，沒有另外補測試。

### 附帶回答：批次匯入人員，遇到系統已有的資料會辨識並覆蓋嗎？

不會覆蓋，是**跳過**。`delivery/routes/import_routes.py` 匯入每一列時，
都會先用 `repository.find_active_personnel_by_name_and_phone(姓名, 電話)`
查一次——**姓名+電話號碼**這個組合，如果已經有一筆「在職中」
（`status == "active"`）的人員資料完全對得上，這一列就跳過，不會新增
也不會覆蓋既有資料，匯入結果頁面上「已跳過」那個分類會顯示原因（例如
「姓名+手機號碼已存在（蝦皮 - 王小明）」）。

需要注意的兩個情況：
1. **判斷條件是「姓名」+「電話」都要對得上**，兩者只要有一個不同（例如
   同一個人手機換號碼、忘記填電話，或姓名打錯字）就會被當成新的人，
   重複建立一筆。
2. 上面這次新增的刪除功能是**永久刪除**，不是設成離職——如果先刪除某人
   的紀錄，之後同一批或另一批 CSV 又剛好有這個人的資料，因為原本那筆
   已經整個從資料庫消失了（查不到），會被當成全新人員重新建立，不會
   被跳過。

## 配送系統「蝦皮」廠商拆分成 4 個（2026-09-13）

使用者要求：「選擇廠商」（配送系統首頁、人員所屬廠商）的「蝦皮」改名
為「蝦皮三輪」，另外新增「蝦皮二輪公司車」「蝦皮二輪雇傭自備車」
「蝦皮承攬」三個。這比單純改名複雜，牽涉到既有蝦皮人員資料怎麼歸類、
應備文件/保險規則要怎麼定義，動工前先跟使用者確認了兩個關鍵決定：

1. **「蝦皮二輪公司車」的保險文件規則**（這是全新類別，沒有現成規則
   可以照抄）：使用者確認**公司車由公司統一投保，不需要同仁個人上傳
   任何保險文件**，這個類別只保留身分證/駕照/合約簽定這些基本項目。
2. **既有「蝦皮」人員資料怎麼分類到新的 4 個廠商**：因為系統原本建立
   人員後，「廠商」這個欄位沒有地方可以修改，使用者選擇**順便新增
   「修改所屬廠商」功能**，讓既有蝦皮人員可以之後手動一筆一筆改到
   正確的新類別；改版當下，既有蝦皮人員全部先維持在改名後的
   「蝦皮三輪」底下（因為代碼本身沒有換）。

### 實作方式

- **代碼設計**：改名前的 `"shopee"` 代碼保留給改名後的「蝦皮三輪」
  （沿用同一個代碼，既有人員/車輛/應徵者資料完全不用搬移，畫面上顯示
  的名稱自動變成新名稱）；`"shopee_company_car"`（蝦皮二輪公司車）、
  `"shopee_employed_own_car"`（蝦皮二輪雇傭自備車）、
  `"shopee_contract"`（蝦皮承攬）是三個全新代碼。`VENDOR_MAP`／
  `VENDOR_LOOKUP`（CSV 匯入用）都是從 `VENDORS` 清單自動算出來，
  所以應徵名單「錄取」下拉選單、CSV 批次匯入、車輛回報 LINE 訊息解析
  這些地方幾乎都不用改程式碼，新增代碼就自動生效。
- **應備文件/保險規則**（`delivery/config.py` 的 `DOC_TYPES`）：
  - 良民證：`police_clearance` 的 `exclude_vendors` 從只排除
    `"shopee"` 改成同時排除全部 4 個蝦皮代碼。
  - 蝦皮承攬（`shopee_contract`）／蝦皮二輪雇傭自備車
    （`shopee_employed_own_car`）：新增專屬的 `shopee_contract_
    insurance`／`shopee_contract_guild_insurance`／`shopee_
    employed_own_car_insurance`／`shopee_employed_own_car_
    liability_insurance` 這幾項，直接用 `include_vendors` 綁代碼，
    不看「合作方式」——因為這兩個是全新代碼，不會有 cooperation_type
    可以判斷，做法跟順豐既有的 `sf_insurance`／`sf_guild_insurance`
    （不看合作方式、直接綁廠商）是同一種寫法。
  - 蝦皮二輪公司車（`shopee_company_car`）：沒有新增任何保險相關
    項目（依使用者確認，公司統一投保）。
  - **「合作方式」下拉選單刻意只留給 `"shopee"`**（`COOPERATION_
    TYPE_VENDORS` 沒有把三個新代碼加進去）：這三個新代碼本身已經
    講清楚雇用/承攬關係，不需要再選一次合作方式；`"shopee"`（蝦皮
    三輪）維持原本行為，是為了不影響還沒被同仁手動改分類的既有蝦皮
    人員資料——這是過渡期的安排，等同仁都手動改完分類之後，可以再
    考慮要不要把這個下拉選單也收掉。
- **新增「修改所屬廠商」功能**：`delivery/repository.py` 新增
  `update_personnel_vendor()`，`personnel_detail.html` 的一鍵更新
  表單最上面多一個「所屬廠商」下拉選單，改了直接生效（不會連動清掉
  cooperation_type／client／已上傳的文件，新廠商用不到的欄位只是不
  會顯示，跟改變合作方式後的既有行為一致）。

### 已知限制／需要使用者知道的後續影響

- **應徵名單**目前是由外部 Google 表單自己的 Apps Script 觸發器寫死
  帶 `vendor="shopee"` 過來（見 `routes/webhook_routes.py` 開頭
  說明），這支腳本不在這個 repo 裡，材霈平台這邊改不到。新應徵者
  進來時廠商還是會先被標成「蝦皮三輪」，如果實際上是其他三類，招募
  同仁在「應徵名單」頁面「錄取」時記得手動改選正確的廠商（下拉選單
  已經自動包含新的 4 個選項）。
- **車輛回報／意外事件回報**（LINE 群組訊息，`delivery/vehicle_
  report.py`／`delivery/incident_report.py`）：同仁回報時是直接在
  LINE 群組打「廠商：蝦皮」這種格式的文字，這次改名後**單純打
  「蝦皮」兩個字系統看不懂了**，要打完整名稱（「蝦皮三輪」「蝦皮
  二輪公司車」「蝦皮二輪雇傭自備車」「蝦皮承攬」其中一個），已經把
  格式錯誤時的提示訊息一併更新成列出這 4 個選項，但**同仁習慣打法
  需要另外口頭或群組公告告知，材霈平台這邊沒有辦法自動通知到每個人
  的 LINE**。
- 批次匯入 CSV 範本（`/delivery/import/template.csv`）的範例資料
  已經同步改成「蝦皮三輪」。

測試：`tests/test_delivery_doc_types.py` 新增 4 個新代碼的應備文件
規則測試；`tests/test_delivery_personnel_vendor_change.py`（新檔案）
測試「修改所屬廠商」的表單驗證邏輯；既有測試裡原本用「蝦皮」這個字面
文字當測試資料的地方（CSV 匯入、補款 Excel 匯出、車輛回報解析），改名
後全部改成「蝦皮三輪」以維持通過。

## 查詢人員頁面補上廠商／狀態篩選（2026-09-13）

使用者原本要的是「查詢人員」（`/delivery/search`）加一個廠商下拉選單，
沒填姓名、有選廠商就列出該廠商全部人員。動工前先跟使用者討論了幾個
問題（跟「人員狀況」頁面功能重疊、離職的人要不要一起列出來、沒有分頁
等等），釐清真正的需求後才動工：

**使用者實際的痛點**：「人員狀況」（`/delivery/vendor/{廠商}`）預設會
隱藏「缺件齊全」跟「離職／放棄報到」的人，如果一個廠商底下的人都已經
備齊文件，畫面上就會整個空白，沒有地方能單純看「這個廠商目前有哪些
人」。所以「查詢人員」這裡刻意**不**套用那些預設隱藏規則。

**離職的人要不要列出來**：討論後使用者確認——**全部都列出來，畫面上
另外加一個狀態下拉選單讓同仁自己篩**，不是預設隱藏。

### 實作方式

- `delivery/repository.py` 的 `search_personnel()` 簽名從只接受
  `keyword` 改成 `keyword="", vendor="", employment_status=""`
  三個篩選條件，都是「有給值才篩」、同時給多個是 AND 的關係，刻意不做
  任何預設隱藏（跟「人員狀況」的 `personnel_matches_filters()` 不一樣）。
  這支函式目前唯一的呼叫端是 `search_routes.py`，改簽名不影響其他地方。
- `search_routes.py`：`/delivery/search` 新增 `vendor`／`status` 這兩個
  query 參數，不合法的代碼會被忽略（沿用 `VENDOR_MAP`／
  `PERSONNEL_STATUS_MAP` 驗證）。**觸發查詢的條件是「姓名/身分證關鍵字
  或廠商，兩者至少一個有值」**——單純選狀態、姓名跟廠商都沒給的話不會
  查（避免一次列出全公司所有人，跟原本「沒打關鍵字就不查」的行為是
  同一個精神，只是把「有值」的條件從只看關鍵字，改成看關鍵字或廠商）。
- `search.html`：搜尋列多了「廠商」「狀態」兩個下拉選單，結果表格多了
  「狀態」欄位（跟「人員狀況」頁面的狀態徽章樣式一致），沒有這欄的話
  離職的人跟在職的人混在一起會分不清楚。

測試：新增 `tests/test_delivery_search_routes.py`（觸發查詢的條件、
不合法廠商/狀態代碼會被忽略、結果列的狀態顯示正確）。`search_personnel()`
本身沒有另外補單元測試——這支函式需要真的連 Firestore，跟
`delivery/repository.py`、`delivery/storage.py` 裡其他 CRUD/查詢函式
的既有慣例一致（留給有 GCP 憑證的環境做整合測試）。

## 人員詳細頁「一鍵全部更新」送出後跳回廠商清單頁（2026-09-13）

使用者要求：人員詳細頁按「一鍵全部更新」後，可以自動跳回上一頁（人員
狀況的全部列表頁面），不要留在詳細頁。

`delivery/routes/vendor_routes.py` 的 `bulk_update_personnel()` 原本
不管成功失敗，一律導回 `/delivery/personnel/{id}`（同一個詳細頁）。
改成：

- **送出成功**：導回 `/delivery/vendor/{廠商代碼}`（人員狀況清單頁），
  用的是**送出前**查到的舊廠商代碼，不是這次表單裡可能剛改過的新
  廠商——同仁通常是從某個廠商的清單點進來改一筆人員，改完理所當然是
  要回到「原本在處理的那個清單」，就算這次同時把所屬廠商改到別的
  廠商去了，也是回到原本這個清單，不是被改動後的新廠商清單打斷
  原本的工作節奏。
- **身分證字號格式錯誤**：維持原本行為，留在詳細頁顯示錯誤訊息
  （`?error=id_number`）——如果這種情況也跳回清單頁，同仁會看不到
  哪裡沒填對，還要重新點進來一次才看得到錯誤。

測試：`tests/test_delivery_personnel_vendor_change.py` 新增
`BulkUpdatePersonnelRedirectTests`（成功送出跳回舊廠商清單、驗證失敗
留在詳細頁）。

## 廠商管理 ⇄ 派遣契約產生器 ⇄ 合約產生器 連動（Phase 1，2026-09-13）

使用者提出「廠商管理」「派遣契約產生器」「合約產生器」這三個功能能不能
連動，先經過多輪討論才動工（討論紀錄不贅述，這裡記最後定案的設計跟
實作）。**這次只做「送出合約／契約時自動同步一筆到廠商管理」，不做
反向查詢（例如從廠商管理點進去看某廠商所有合約），也沒有回填任何
歷史紀錄**——使用者明確表示歷史資料「太多了」，不回填，廠商管理的
資料從這次上線後開始自然累積。

### 設計重點

- **合約產生器（`/client-contracts`）**：每次送出成功，**不論廠商
  管理裡有沒有同名/同統編紀錄，都自動新增一筆**——同一家客戶簽了
  好幾年、甚至同一年簽了好幾份，使用者要的就是每份合約都留下自己的
  紀錄（原話：「不同合約都保留，除非同仁自行刪除」）。新增的廠商
  紀錄帶入：名稱＝甲方公司名稱、統一編號＝甲方統編、合約年＝合約
  起始日期的年份、簽約公司＝這次選的乙方（材霈旗下公司）。
- **派遣契約產生器（`/dispatch-contracts`）**：客戶名稱欄位原本就是
  文字輸入框＋`<datalist>` 建議清單（可以下拉選、也可以自己打），
  已經滿足使用者「除了下拉也可自行填入」的要求，**這次沒有改欄位的
  UI**，只改了建議清單的資料來源——原本只從這個模組自己的歷史紀錄
  抓名字，現在**先合併廠商管理裡的廠商名稱，再補上歷史紀錄裡還沒
  出現在廠商管理的名字**（避免舊的建議選項消失）。送出成功後，**如果
  廠商管理裡還沒有完全同名（去頭尾空白後比對）的紀錄，才自動新增一筆
  只有名稱的廠商紀錄**——跟合約產生器「每次都新增」不一樣，是因為
  派遣契約常常同一個客戶重複送出很多份，不做這個判斷廠商管理會被
  灌爆重複紀錄。
- **廠商管理（`/vendors`）**：`platform_vendors.py` 的欄位新增「統一
  編號」「合約年」兩個（都選填，同仁也可以自己手動在 `/vendors` 頁面
  編輯／新增時填）。**手動在 `/vendors` 新增的廠商，「代號」欄位還是
  跟以前一樣由同仁自己打、當作文件 ID**；但這兩支產生器自動同步新增
  的廠商紀錄，改用 Firestore 自動配發的文件 ID（因為合約產生器允許
  同一個統編出現在好幾筆不同年份的紀錄裡，不能再拿統編當文件 ID）。
  自動建立的紀錄「代號」欄位預設帶入統一編號（沒有的話退而求其次用
  廠商名稱），純粹方便同仁在列表上辨識用，之後仍可以自己到
  `/vendors` 修改，不影響文件本身的識別。

### 程式面

- `platform_vendors.py`：`FIELDS` 新增 `tax_id`／`contract_year`；
  新增 `create_vendor_auto(fields)`（文件 ID 交給 Firestore 自動配發，
  跟既有 `create_vendor(vendor_id, fields)` 手動指定代號的流程分開）
  跟 `vendor_name_exists(name)`（只比對名稱，派遣契約產生器同步時用
  這支判斷「要不要自動補一筆」）。
- 新檔案 `services/vendor_sync.py`：`sync_vendor_from_client_contract(...)`
  跟 `sync_vendor_from_dispatch_contract(client_name)` 這兩個同步函式，
  獨立成一個模組，不是直接塞進兩個產生器既有的 `save_submission()`
  裡——這樣兩支產生器原本已經測試過的送出邏輯完全不用改，同步這個
  新的副作用在路由層、`save_submission(...)` 呼叫「之後」另外呼叫，
  就算同步邏輯以後要調整，也不會動到既有送出流程的風險。
- `client_contract_routes.py`／`dispatch_contract_routes.py`：在既有
  送出成功、呼叫完 `save_submission(...)` 之後，各自呼叫上面兩支同步
  函式。`dispatch_contract_routes.py` 另外新增 `_client_name_suggestions()`
  組合建議清單（廠商管理優先，歷史紀錄補漏）。
- `templates/vendor_form.html`／`templates/vendors_list.html`：新增
  「統一編號」「合約年」欄位的表單輸入跟列表欄位，並更新頁面說明文字。

### 已知限制／使用者需要知道的事

- **沒有回填任何歷史合約／契約的紀錄**——廠商管理裡目前既有的資料
  不會自動補上統一編號／合約年，只有這次上線之後新送出的合約／契約
  才會自動同步。如果需要幫舊資料補統編／合約年，要同仁自己到
  `/vendors` 手動編輯。
- 派遣契約產生器同步時**只比對名稱完全相同**（去除頭尾空白），如果
  同一家客戶名稱打法不一致（例如多一個空格、简繁体或全形/半形字不
  一樣），會被系統當成不同客戶各自新增一筆——這不是系統判斷錯誤，
  是設計上刻意只做最單純的字串比對，避免誤判把不同客戶合併成一筆。
- 這次上線不需要任何額外的手動設定步驟（沒有新的環境變數、沒有新的
  GCP 權限要開）——正常走 Cloud Run 部署流程即可生效。

測試：`tests/test_platform_vendors.py` 新增 `CreateVendorAutoTests`／
`VendorNameExistsTests`；新檔案 `tests/test_vendor_sync.py`
（`sync_vendor_from_client_contract`／`sync_vendor_from_dispatch_contract`
各自的邏輯）；`tests/test_client_contract_routes.py`／
`tests/test_dispatch_contract_routes.py` 既有的送出成功測試補上
對應的 mock，確認同步函式有在正確時機被呼叫、且不會讓既有測試意外
打到真的 Firestore。全部測試（`python3 -m unittest discover -s tests
-p "test_*.py"`）1043 個全數通過。

**後續（Phase 2，還沒開始實作）**：使用者已經確認要接著做「總表」
功能——合約產生器總表（可勾選年份、每份合約各自一列）跟派遣契約總表
（不分年份，每個客戶只顯示最新一份、每個班別各自一列），網頁呈現＋
匯出 Excel 都要，權限比照現有主管可見範圍（專員完全看不到這個功能），
會依賴這次 Phase 1 的廠商管理連動當作客戶名稱分組的依據。

## 總表功能（/contract-summary，Phase 2，2026-09-13）

Phase 1（廠商管理連動）上線後，接著做使用者原本要的「總表」——整理
「合約產生器」「派遣契約產生器」的紀錄，給主管年底盤點隔年所有客戶的
合約方式、薪資、服務費計算用，網頁呈現＋匯出 Excel 都有。

### 設計重點（討論定案內容）

- **合約產生器總表**：畫面上勾選要看的年份（checkbox，依合約起始日期
  年份分類，跟廠商管理的「合約年」同一套算法），每份合約都是獨立一列，
  **不去重**——跟廠商管理連動同樣的「每份合約都留紀錄」精神，同一個
  客戶不同年份、甚至同一年多份合約，總表上都各自一列。年份勾選清單
  一定會有「今年」跟「明年」這兩個選項，就算明年還沒有任何合約紀錄也
  先給選——這正是這個功能最主要的使用情境（年底盤點隔年客戶）。四種
  合約版本（時薪一口價／實支實付／白領代招／台籍代招）報價欄位形狀都
  不一樣，整理成一欄「報價方式」文字說明（例如「時薪196／管理費20」
  「服務費：人員薪資的15%」），不用四組互相稀疏的獨立欄位塞滿表格。
- **派遣契約總表**：**不勾選年份**，每個客戶只顯示「最新一份」送出的
  派遣契約紀錄（使用者原話：「合約用合約年沒問題，但是契約可能就直接
  用最新的版本」）。同一份契約裡的每個班別各自變成一列，不是塞進同一
  格顯示。
- **權限完全借用既有規則，沒有新增任何權限邏輯**：專員角色完全看不到
  這個功能；主管角色（`platform_accounts.module_role()` 判斷）在「合約
  產生器」「派遣契約產生器」任一個模組是主管，就能看到對應那一半的
  總表；全平台管理員兩半都看得到。「看得到哪些紀錄」直接呼叫兩個
  產生器既有的 `list_visible_submissions()`（自己送出的、自己是送出者
  的主管、或全平台管理員），沒有重新實作可見範圍判斷。
- **沒有自己的模組代碼**：`platform_accounts.MODULES`／`/accounts`
  都沒有新增這個功能的勾選項目，能不能看到純粹是「借」上面那兩個
  既有模組的角色判斷算出來的，這是使用者確認過可以接受的設計（因為
  「所有主管我都會開契約及合約的產生器」）。
- **入口**：`/client-contracts`、`/dispatch-contracts` 首頁各加一個
  「查看總表」按鈕（只有這個帳號在對應模組是主管角色才會顯示），沒有
  在 `/portal` 另外加一個獨立的入口圖示。

### 程式面

- 新檔案 `services/contract_summary_service.py`：資料整理的核心邏輯
  （年份清單計算、年份篩選、報價欄位整理成一句話說明、派遣契約只取
  每個客戶最新一筆），刻意都寫成不直接碰 Firestore 的「純函式」（吃
  已經抓好的紀錄清單、吐處理過的結果），跟兩個產生器既有的
  `list_visible_submissions()` 分開，方便單元測試、也不用重新實作
  可見範圍邏輯。
- 新檔案 `services/contract_summary_excel.py`：兩份總表個別的
  openpyxl 匯出函式，寫法跟既有的 `delivery/excel_export.py`
  （補款/假別查詢的「一鍵下載 EXCEL」）一致。
- 新檔案 `contract_summary_routes.py`：`/contract-summary`（網頁）、
  `/contract-summary/export/client-contracts.xlsx`、
  `/contract-summary/export/dispatch-contracts.xlsx`（匯出）三支路由，
  權限判斷、串接兩個產生器的資料查詢、頁面渲染都在這裡。
- 新樣板 `templates/contract_summary.html`：兩個區塊（合約產生器總表／
  派遣契約總表）依帳號有沒有對應的存取權個別顯示或隱藏。
- `client_contract_routes.py`／`dispatch_contract_routes.py`：首頁多
  傳一個 `show_summary_link` 布林值給樣板，`templates/client_contract_
  home.html`／`templates/dispatch_contract_home.html` 依這個值決定要不
  要顯示「查看總表」按鈕。
- `main.py`：掛載 `contract_summary_routes.router`。

### 已知限制／使用者需要知道的事

- 這次上線不需要任何額外的手動設定步驟（沒有新的環境變數、沒有新的
  GCP 權限或 `/accounts` 勾選項目要設定）——正常走 Cloud Run 部署流程
  即可生效。
- 年份勾選是用網址參數（`?years=2026&years=2027`），如果同仁把畫面上
  的年份全部取消勾選，系統會視為「沒有勾選」重新套用預設值（今年＋
  明年），不會真的顯示「完全不篩年份」或「篩出空白清單」——這是刻意
  的簡化，避免勾選框在網址參數層面「全部取消」跟「第一次進頁面」兩種
  情況分不出來。
- 兩份總表抓紀錄的上限是 5000 筆（`contract_summary_routes._RECORDS_
  LIMIT`），比兩個產生器自己列表頁預設的 200 筆高很多——正常公司規模
  不會碰到這個上限，但如果之後紀錄真的累積到接近這個數字，要回來調高。

測試：新檔案 `tests/test_contract_summary_service.py`（年份清單/篩選/
報價說明/派遣契約最新一筆邏輯，共 15 個測試）、`tests/test_contract_
summary_excel.py`（兩份 Excel 的表頭/內容，共 4 個測試）、`tests/
test_contract_summary_routes.py`（權限判斷、只查有權限那一半的資料、
匯出路由的 404／內容，共 18 個測試）。全部測試（`python3 -m unittest
discover -s tests -p "test_*.py"`）1080 個全數通過。

## 廠商管理服務部門連動 ＋ 總表權限改版 ＋ 合併視圖（2026-09-14）

上一節（總表 Phase 2）上線後，使用者接著提出一連串調整，來回討論了
好幾輪才定案，這裡一次記完整套最終設計跟實作內容。

### 這次做了什麼

1. **廠商管理（`/vendors`）新增「服務部門」欄位**（可複選，選項來自
   `/departments` 部門主檔，存部門名稱字串清單）——同仁自己到 `/vendors`
   手動勾選，合約/契約產生器自動同步新增的廠商紀錄一律從空清單開始，
   系統不會自動幫忙猜。因為合約產生器每次送出都新建一筆廠商紀錄（不
   去重），同一個客戶名稱底下可能同時存在好幾筆廠商紀錄，**每一筆的
   服務部門要各自勾選，不會互相沿用**。
2. **廠商管理新增篩選列**：廠商名稱（模糊搜尋）＋ 合約年（下拉，選項是
   資料裡實際出現過的年份），純前端 JavaScript 即時篩選，不用重新整理
   頁面。
3. **廠商管理列表新增「連動合約」「連動契約」欄位**，可以直接點「預覽」
   「下載」——因為 `/vendors` 本來就是全平台管理員限定頁面，這裡不用
   另外判斷權限。「連動契約」如果同一個廠商被好幾個人各自負責，會各自
   顯示一筆（各自最新版本，標註送出人），不是只顯示單一個全域最新。
4. **合約產生器／派遣契約產生器送出時，新增一個同仁在畫面上看不到的
   內部關聯欄位 `vendor_id`**，記住這筆合約/契約連到廠商管理的哪一筆：
   - 合約產生器：每次送出都新建一筆廠商紀錄，一對一連過去。
   - 派遣契約產生器：新增「選擇對應的合約」下拉選單（列出這個帳號看
     得到的合約產生器紀錄），選了就直接沿用那份合約的客戶名稱／
     `vendor_id`，**完全不用再靠名稱去廠商管理猜**；沒選的話走原本
     「廠商管理有同名就沿用、沒有就新建」的名稱比對備案路徑（過渡期
     保留，等系統上的合約資料齊全再評估要不要拿掉）。
5. **總表（`/contract-summary`）的權限模型整個「取代」成服務部門規則**
   （不是疊加在原本規則上）：能不能打開頁面、看不看得到某筆紀錄，
   完全看「這筆合約/契約連到的廠商紀錄，服務部門有沒有勾自己的部門」
   （主管職級才算，全平台管理員永遠看得到全部）——**不再**沿用合約
   產生器／派遣契約產生器模組本身「送出者本人／送出者的主管」那套
   規則，也不再看有沒有開通這兩個模組。**這個取代只影響總表本身**：
   兩個產生器自己的首頁列表、刪除功能完全沒有改變，還是原本送出人鏈
   的規則；只有「預覽」「下載」這兩個動作額外多開放給服務部門主管
   （兩條規則符合一個即可），確保總表上列出來的連結點進去不會變成
   404。
6. **派遣契約「最新版本」的分組邏輯改版**：原本是「每個客戶名稱只留
   最新一份」，改成「每個客戶名稱＋送出人帳號」各自留最新一份——同一
   個客戶如果同時被不同人／不同團隊各自負責、各自送出自己的派遣契約，
   彼此不會互相蓋掉。如果契約有透過「選擇對應的合約」連到具體某一份
   合約，分組鍵改用「那份合約的 id＋送出人」，比純比對客戶名稱字串更
   精準。這個新規則套用到「派遣契約總表」跟新增的「合併視圖」兩個地方。
7. **新增第三個總表區塊「合併視圖」**：一列＝合約產生器總表的一份合約，
   右側接上這份合約連動的派遣契約資訊。**只有透過「選擇對應的合約」
   明確連過去的契約才會配對進來**——手動輸入客戶名稱、沒有明確連結的
   契約不會出現在合併視圖，避免用名稱亂猜配對，那些改用「派遣契約
   總表」看。多班別固定切成「班別1／班別2／…」幾組欄位（職稱/工作
   時間/時薪/工時獎金/加班各一欄），組數＝所有配對到的契約裡班別數
   最多的那一筆需要幾組（至少留一組）。如果同一份合約底下剛好有好幾
   個人各自負責不同契約版本，會重複這份合約的列、分別接上各自的契約
   內容。原本的「合約產生器總表」「派遣契約總表」兩個區塊保留不動，
   合併視圖是額外新增的第三個區塊，三個都各自能匯出 Excel。

### 已知限制／使用者需要知道的事

- **不回填**：`vendor_id`／`linked_client_contract_id` 這兩個內部關聯
  欄位只有這次上線之後新產生的合約/契約才會有，舊的歷史紀錄沒有這個
  連結，一律不會出現在服務部門主管看得到的範圍（除非同仁自己重新
  產生一次新版本）。廠商管理的「服務部門」欄位也是空白起始，需要同仁
  自己到 `/vendors` 一筆一筆手動補上，才會逐步在總表裡生效。
- **總表權限改版是全面取代，不是疊加**：如果某個主管原本是靠「合約
  產生器模組主管角色＋送出人鏈」規則看得到全部部屬送出的合約，現在
  如果他自己的部門沒有被勾在任何廠商紀錄的服務部門裡，會突然在總表
  裡完全看不到任何東西（但他自己在 `/client-contracts` 首頁還是看得
  到部屬送出的合約，那邊沒有變）——這是使用者明確要求的行為（「這個
  表是實際服務的部門才需要」），不是 bug。
- 年份勾選是用網址參數（`?years=2026&years=2027`），如果同仁把畫面上
  的年份全部取消勾選，系統會視為「沒有勾選」重新套用預設值（今年＋
  明年），不會真的顯示「完全不篩年份」或「篩出空白清單」——這是刻意
  的簡化，避免勾選框在網址參數層面「全部取消」跟「第一次進頁面」兩種
  情況分不出來。
- 這次上線不需要任何額外的手動設定步驟（沒有新的環境變數、沒有新的
  GCP 權限要開，`platform_accounts.MODULES` 也沒有變動）——正常走
  Cloud Run 部署流程即可生效。同仁要記得的是：新客戶的合約產生後，
  記得到「廠商管理」把服務部門勾好，對應部門的主管才看得到總表資料。

### 程式面（給下次要改這塊的人看）

- `platform_vendors.py`：`service_departments` 欄位（獨立於 `FIELDS`
  之外處理，因為是清單不是字串）；`find_vendor_id_by_name()` 取代
  `vendor_name_exists()` 內部實作（後者改成呼叫前者），回傳文件 ID
  而不只是布林值，給同步時記錄 `vendor_id` 用。
- `services/vendor_sync.py`：兩支同步函式都改成回傳這次用到的廠商
  文件 ID。
- `services/contract_summary_service.py`：整個檔案重寫，核心是
  `can_view_via_vendor_department()`（總表批次查詢用，吃預先建好的
  `vendor_lookup`）／`can_view_via_vendor_department_single()`（產生器
  本身的預覽/下載路由用，單筆查詢，不用先建整份 lookup）共用同一個
  內部判斷 `_account_can_claim_department()`；`_dispatch_group_key()`
  決定「最新版本」的分組鍵；`build_merged_summary_rows()` 是合併視圖
  的核心，回傳 `(rows, max_shifts)` 給樣板組動態表頭。
- `contract_summary_routes.py`：`_require_access()`／`_visible_records()`
  改成完全用 `viewer_has_any_department_access()`／
  `visible_client_contract_records()`／`visible_dispatch_contract_records()`
  判斷，不再檢查 `client_contracts`／`dispatch_contracts` 模組權限。
  新增 `/contract-summary/export/merged.xlsx` 匯出路由。
- `vendors_routes.py`：新增 `_client_contract_by_vendor_id()`／
  `_dispatch_contracts_by_vendor_id()` 兩支輔助函式，一次抓好兩邊全部
  紀錄建索引，避免對每一筆廠商各自查一次 Firestore。

測試：`tests/test_platform_vendors.py`／`tests/test_vendor_sync.py`
補上新行為的測試；`tests/test_client_contract_routes.py`／`tests/
test_dispatch_contract_routes.py` 新增 `vendor_id`／「選擇對應的合約」
／服務部門主管預覽下載的測試；`tests/test_vendors_routes.py` 新增
表單/索引輔助函式的測試；`tests/test_contract_summary_service.py`／
`tests/test_contract_summary_excel.py`／`tests/test_contract_summary_
routes.py` 大幅改寫（權限模型、分組邏輯、合併視圖都是全新測試）。
另外用實際的 Jinja2 環境手動渲染過 `vendors_list.html`／
`vendor_form.html`／`dispatch_contract_form.html`／`contract_summary.
html`，確認樣板語法沒問題。全部測試（`python3 -m unittest discover
-s tests -p "test_*.py"`）1135 個全數通過。

### 追加修正：「選擇對應的合約」改成看部門，不是看送出人（2026-09-14）

上線後使用者發現一個沒設想到的漏洞：「選擇對應的合約」下拉選單原本是
照「跟 `/client-contracts` 首頁同一套可見範圍」（送出人本人／送出人的
主管）過濾——如果合約是 A 送出的，契約是跟 A 沒有主管/部屬關係的 B 要
送出，B 在下拉選單裡完全看不到 A 送出的那份合約，選不到，送出時系統
也會再擋一次。這不符合實際「業務出合約、另一位不相干的同仁出契約」的
作業情境。

**修正**：改成用「這個帳號的部門有沒有被勾在那份合約連到的廠商紀錄的
服務部門裡」判斷（`services/contract_summary_service.py` 新增
`viewer_can_link_contract_vendor()`）——**不要求主管職級**，這點跟
總表的 `can_view_via_vendor_department()` 不一樣：總表是「看得到完整
內容」的權限，這裡只是「能不能連結」，一般同仁（專員）本來就常常是
實際負責送出契約的人。選了之後契約端只會帶走合約的客戶名稱跟廠商 ID，
不會因此看到合約本身的價格、統一編號等完整內容，那些還是要透過
`can_view_submission()` 才看得到。

**代價／同仁需要知道的事**：這代表合約送出後，**要先到廠商管理把服務
部門勾好，契約端的同仁才有辦法在下拉選單選到那份合約**——如果同一個
客戶的合約跟契約是不同部門的人負責，記得把廠商紀錄的服務部門同時勾上
雙方的部門（服務部門本來就是可複選）。這不是新增的限制，是延續「服務
部門要同仁自己維護」的既有設計，只是這次讓它同時控制「能不能連結」跟
「總表看不看得到」兩件事。

測試：`tests/test_contract_summary_service.py` 新增
`ViewerCanLinkContractVendorTests`；`tests/test_dispatch_contract_
routes.py` 的 `ClientContractOptionsTests`／`LinkedClientContractTests`
改用新的部門判斷重寫。全部測試 1144 個全數通過。

## 配送部系統：車輛新增「輪別」欄位 ＋ 車輛歷史紀錄／意外事件回報可編輯（2026-09-14）

使用者提出三個配送部（`/delivery`）系統的調整需求：車輛要多一個「三輪
／二輪」欄位（預設三輪）、車輛的領還車歷史紀錄要能編輯、意外事件回報
的內容要能編輯。

### 1. 車輛「輪別」欄位（三輪／二輪）

- `delivery/config.py` 新增 `WHEEL_TYPES`／`WHEEL_TYPE_MAP`／
  `DEFAULT_WHEEL_TYPE`（預設值 `three_wheel`＝三輪）。
- 新增車輛（`/delivery/vehicles/new`）的表單多一個「輪別」下拉選單，
  預設選三輪，同仁可以改選二輪。
- **既有車輛不用手動遷移資料**：Firestore 裡舊的車輛文件沒有這個欄位，
  `repository.get_vehicle()`／`list_vehicles()` 讀取時遇到欄位不存在
  一律當成三輪（`data.setdefault("wheel_type", DEFAULT_WHEEL_TYPE)`），
  車輛清單／詳細頁看起來就是「所有舊車輛都自動變成三輪」，跟這次的
  需求（先都預設三輪）完全符合，不用另外寫遷移腳本、也不用叫使用者
  去跑任何指令。
- 車輛詳細頁（`/delivery/vehicles/{車號}`）新增一個小表單，可以隨時把
  某台車的輪別改成二輪或改回三輪（`repository.set_vehicle_wheel_type`）。
- 車輛清單頁（`/delivery/vehicles`）新增「輪別」欄位顯示，以及輪別篩選
  下拉選單，用法跟既有的廠商／狀態篩選一致。

### 2. 車輛歷史紀錄（領還車事件）可編輯

- 車輛詳細頁的「歷史紀錄」表格，每一列新增「編輯」連結，進去可以修正
  廠商、姓名、事件類型（領車/還車）、日期、地點（`vehicle_event_edit.
  html`，新增樣板）。
- 路由：`GET/POST /delivery/vehicles/{車號}/events/{event_id}/edit`
  （`vehicle_routes.py`），任何登入配送部系統的同仁都能編輯（跟原本
  「手動補登事件」的權限層級一致），會先確認這筆事件真的屬於網址上
  那台車，避免湊網址編輯到別台車的紀錄。
- `repository.update_vehicle_event()`：**如果編輯的剛好是這台車目前
  反映的最新一筆事件**（用事件的建立時間戳記跟車輛主檔的
  `last_event_at` 比對），會連動更新車輛主檔目前顯示的「使用人／地點／
  狀態」，避免歷史紀錄改完之後跟主檔顯示的「目前狀態」兜不起來；如果
  車輛目前是「待維修」，這個連動會跳過（待維修是管理員另外手動標記的
  狀態，不該被歷史紀錄的編輯覆寫掉）。編輯比較舊的一筆歷史紀錄則完全
  不影響車輛主檔目前狀態，純粹只是改歷史紀錄本身的顯示內容。
- 編輯時**不套用**「這筆事件套用到車輛目前狀態合不合理」那套檢查
  （`vehicle_event_error`，那是給新增事件判斷「這台車現在能不能被領/
  還」用的）——編輯的是已經發生過的歷史紀錄，只要求欄位都有填、事件
  類型合法即可。

### 3. 意外事件回報內容可編輯

- 意外事件詳細頁（`/delivery/incidents/{id}`）新增「編輯回報內容」
  按鈕，**只有管理員看得到**（比照風險等級／結案的權限層級，這份是
  正式的意外事件記錄，跟車輛歷史紀錄的權限層級刻意不同——任何登入的
  同仁都能修正自己補登的領還車紀錄，但意外事件回報收斂給管理員改，
  避免正式記錄被隨意更動）。
- 路由：`GET/POST /delivery/incidents/{id}/edit`（`incident_routes.
  py`，新增樣板 `incident_edit.html`），欄位跟 LINE 群組回報格式一樣
  （廠商、身分類別、人員名稱、發生時間、地點、執行勤務中/上下班途中、
  是否報警、受傷情形、是否聯繫家屬、是否牽扯他人、意外事件經過），
  固定選項的欄位（廠商、身分類別、執行勤務中/上下班途中、三個是否類
  欄位）用下拉選單，避免打錯字存進不合法的值。
- `repository.update_incident_event()`：**只更新這 11 個回報欄位**，
  刻意不去動 `risk_level`（風險等級）／`status`（結案狀態）——那兩個
  欄位各自有獨立的操作入口，不該被這裡的編輯表單意外洗掉。

### 測試

新增 `tests/test_delivery_vehicle_routes.py`（輪別新增/修改、歷史紀錄
編輯的表單驗證與權限）、`tests/test_delivery_incident_routes.py`（意外
事件編輯的表單驗證，含驗證「風險等級/結案狀態不受編輯影響」）；擴充
`tests/test_delivery_vehicle.py` 的 `VehicleMatchesFiltersTests`（輪別
篩選，含「舊資料沒有這個欄位時當成三輪」的案例）。這幾個路由都會實際
呼叫 Firestore 寫入，跟這個專案既有的測試慣例一樣不直接測試
`repository.py` 裡會連線 Firestore 的函式本身，改成在路由層用
`unittest.mock.patch.object` 模擬 `repository` 的回傳值來驗證表單驗證
邏輯／呼叫參數是否正確。全部測試（`python3 -m unittest discover -s
tests -p "test_*.py"`）1170 個全數通過。

### 使用者需要知道的事

這次改動**不需要任何手動部署步驟**（沒有新的環境變數、沒有需要另外
執行的遷移指令）——PR 合併之後 GitHub Actions 會自動部署到 Cloud Run，
跟平常一樣。既有車輛登入系統後會直接顯示「三輪」，不用手動處理。

## 配送部系統：車輛新增「服務區域」欄位 ＋「一鍵整理車輛狀況」報告（2026-09-14）

同一天緊接著上一節，使用者又提出兩個需求：車輛管理要多一個「服務
區域」欄位、以及一個按鈕可以一鍵整理成方便直接貼到 LINE 群組的車輛
現況報告文字（附了一個實際的範例格式）。

### 決策過程（先問過使用者兩個問題才動工）

這次開工前先用 `AskUserQuestion` 確認兩件事，因為猜錯的代價比較高
（服務區域一旦車輛開始累積資料，之後要改清單/搬資料比較麻煩；報告
產生範圍如果猜錯，使用者拿到的東西可能整個要重做）：

1. **服務區域要不要做成固定清單**：使用者範例裡的台北/新北/桃園/
   新竹/台中/台南/高雄這 7 個，確認就是目前要用的完整清單（不是
   自由輸入文字）——避免同仁「台北」跟「台北市」這種寫法不一致，
   分區統計對不起來。
2. **「一鍵整理」按鈕要怎麼決定報告涵蓋哪個廠商**：確認是「一次
   產出全部廠商，各自一段」（不是先選廠商再各自產生一份）。

### 這次做了什麼

1. **`delivery/config.py` 新增 `SERVICE_AREAS`／`SERVICE_AREA_MAP`**：
   固定 7 個代碼（台北/新北/桃園/新竹/台中/台南/高雄）。跟輪別不同，
   服務區域**沒有通用預設值**，新增車輛時必填（沒有合理的預設值可以
   套，不像輪別大部分車輛本來就是三輪）；既有車輛（這個欄位新增之前
   建立的舊資料）讀取時當成空字串，報告裡會顯示「未分區」，不會悄悄
   消失，管理員之後可以在車輛詳細頁個別補上。**之後公司拓點到清單
   外的縣市，需要請 Claude 加代碼進 `SERVICE_AREAS`**，這不是同仁自己
   能在網頁上新增的欄位（設計上刻意的權衡，用固定清單換分區統計的
   準確度）。
2. **新增車輛表單／車輛詳細頁**都加上服務區域的下拉選單，跟輪別一樣
   有獨立的「更新服務區域」小表單可以隨時修正；車輛清單頁新增服務
   區域欄位顯示＋篩選下拉選單。
3. **新增 `delivery/vehicle_status_report.py`**（純函式模組，不碰
   Firestore，方便寫單元測試）：
   - `build_vendor_fleet_report(vendor_name, vehicles, today=None)`：
     單一廠商的報告文字，格式完全照使用者提供的範例（含「📊【廠商
     車輛現況】YYYY/M/D」標題、總車輛數/使用中/空車/待維修的欄位
     順序、地區分布每行「共X台／空車X／使用中X／待維修X」的順序—
     —這兩處欄位順序刻意不一樣，是照抄使用者範例，不是打字打錯）。
     沒有任何車輛的地區直接跳過；有車輛沒設定服務區域的話，額外補
     一行「未分區」放在所有命名地區之後，確保總數對得起來。
   - `build_fleet_status_report(all_vehicles, today=None)`：依
     `VENDORS` 宣告順序，每個廠商各自呼叫一次
     `build_vendor_fleet_report()`，中間空一行分隔；完全沒有車輛的
     廠商直接跳過，不產生空段落；系統裡完全沒有車輛時回傳一句提示
     文字。
4. **車輛清單頁新增「📊 一鍵整理車輛狀況」按鈕**，連到新路由
   `GET /delivery/vehicles/status-report`（`vehicle_routes.py`）：
   撈全部車輛（不套用清單頁上當時的篩選條件，永遠是全部車輛）丟進
   `build_fleet_status_report()`，渲染成一個唯讀 `<textarea>` 加
   「複製文字」按鈕（`vehicle_status_report.html`，用
   `navigator.clipboard` API，不支援時退回
   `document.execCommand('copy')`），同仁按一下就能複製貼到 LINE
   群組。**這個路由要註冊在 `/vehicles/{vehicle_no}` 之前**，不然
   `"status-report"` 會被當成車號吃掉、永遠進不到這支函式（實際用
   `TestClient` 驗證過路由順序沒問題）。

### 測試

新增 `tests/test_delivery_vehicle_status_report.py`：其中
`test_matches_user_supplied_example_exactly` 直接照使用者提供的範例
重建車輛資料，逐字比對輸出文字（包含符號、欄位順序、日期沒有前導
零），是這支功能最重要的驗收依據；另外涵蓋地區無車輛時跳過、未分區
排序、廠商排序照 `VENDORS` 宣告順序、無車輛時的提示文字等案例。
`tests/test_delivery_vehicle_routes.py` 新增服務區域新增/修改/清空、
一鍵整理報告頁的路由測試；`tests/test_delivery_vehicle.py` 擴充服務
區域篩選的測試。全部測試（`python3 -m unittest discover -s tests -p
"test_*.py"`）1189 個全數通過；另外用實際的 Jinja2 環境手動渲染過
新增/修改的所有樣板，確認語法沒問題。

### 使用者需要知道的事

這次改動**不需要任何手動部署步驟**（沒有新的環境變數、沒有需要另外
執行的遷移指令）。既有車輛的服務區域會顯示「未設定」，需要的話請到
每台車的詳細頁自己補上（沒有批次補的功能，車輛數量不多，一台一台
補比較不容易出錯）。之後如果公司拓點到台北/新北/桃園/新竹/台中/
台南/高雄以外的縣市，記得回來請 Claude 把新縣市加進服務區域清單。

## 配送部系統：意外事件新增改成 LINE 群組／網站雙軌並行（2026-09-14）

使用者問「車輛管理跟意外事件的功能，除了在 LINE 群組用關鍵字綁定，
也都可以直接在系統網站操作嗎？」——查完現有路由發現車輛管理（新增
車輛、領還車事件、狀態調整……）本來就已經是 LINE／網站雙軌，但意外
事件只有「查詢/風險等級/結案/編輯內容」這些管理動作在網站上，**新增**
一筆意外事件回報只能透過 LINE 群組（沒有網站表單）。使用者接著明確
要求：希望車輛管理跟意外事件都能同時在 LINE 群組跟系統網站操作。

### 這次做了什麼

1. **新增 `GET/POST /delivery/incidents/new`**（`incident_routes.py`），
   網站上直接送出一筆意外事件回報，欄位（廠商/身分類別/人員名稱/發生
   時間/發生地點/執行勤務中或上下班途中/是否報警/受傷情形/是否聯繫
   家屬/是否牽扯他人/意外事件經過）跟 LINE 群組回報格式完全對應，
   固定選項的欄位用下拉選單避免打錯字，發生時間用瀏覽器原生
   `<input type="datetime-local">`。
2. **刻意重用 `repository.create_incident_event()`**（跟 LINE 群組
   回報处理路徑 `handle_incident_report()` 呼叫的是同一個函式），也就
   是說「人員名稱＋發生時間」跟既有紀錄完全相同時，網站新增一樣會
   視為同一起事件、直接更新既有那筆，不會另外開一筆重複紀錄——LINE
   群組跟網站兩條輸入路徑寫進 Firestore 的資料規則完全一致，不是
   各自兜一套邏輯。
3. **權限層級刻意跟編輯/風險等級/結案不一樣**：新增意外事件只要求
   `login_required`（任何登入配送部系統的同仁都能新增），不是
   `admin_required`——因為這比照的是「LINE 群組裡任何人都能回報」
   的既有行為；編輯已存在的回報內容、設定風險等級、標記結案這幾個
   「事後管理」動作維持原本的管理員限定不變，這次沒有調整。
4. **抽出共用的表單驗證/整理邏輯**（`_validate_incident_form()`／
   `_incident_form_data()`），新增和編輯兩個路由共用同一套規則，避免
   兩處各自維護一份幾乎一樣的驗證邏輯、之後改規則忘記改到另一邊。
5. 意外事件清單頁（`/delivery/incidents`）新增「新增意外事件」按鈕，
   提示文字改成「同仁可以在 LINE 群組回報，也可以直接在這裡新增」。

**車輛管理這次沒有額外調整**——上一節已經確認車輛的新增/查詢/狀態
調整/領還車事件/歷史紀錄編輯全部都在網站上，跟 LINE 群組回報是完整
雙軌，不需要再補功能。

### 測試

`tests/test_delivery_incident_routes.py` 新增 `NewIncidentFormTests`／
`NewIncidentSubmitTests`（含驗證 datetime-local 的 "T" 分隔符會換成
空白跟現有資料格式對齊、非管理員一般專員也能新增、驗證失敗不會寫入、
以及路由確實把 `repository.create_incident_event()` 的去重結果原封不動
拿來導頁）。新路由 `/incidents/new` 註冊在 `/incidents/{incident_id}`
之前，避免 "new" 被當成 incident_id 吃掉，有實際用 `TestClient` 驗證
過路由順序。全部測試（`python3 -m unittest discover -s tests -p
"test_*.py"`）1195 個全數通過；另外用實際的 Jinja2 環境手動渲染過
`incident_new.html`／`incident_list.html`，確認樣板語法沒問題。

### 使用者需要知道的事

這次改動**不需要任何手動部署步驟**。現在意外事件清單頁上方多了一個
「新增意外事件」按鈕，任何登入配送部系統的同仁都能用，不限管理員；
跟 LINE 群組回報寫進系統的規則完全一樣（同一個人、同一個發生時間，
就會更新既有那筆，不會變成兩筆）。

## 配送部系統：新增廠商「蝦皮三輪速配倉」（2026-09-14）

使用者要求新增這個廠商。動工前先確認一件事：系統裡每個廠商代碼都綁了
各自的「應備文件」規則（要不要良民證、要哪種保險），加錯會影響同仁的
缺件檢查跟到期提醒，所以先問過使用者——確認「蝦皮三輪速配倉」這批
人員的應備文件/保險規則要**跟「蝦皮三輪」（shopee 代碼）完全一樣**，
只是要用獨立的廠商代碼分開追蹤（人員清單、車輛、意外事件都各自算一
批），不是要另外訂一套不一樣的規則。

### 這次做了什麼

新增代碼 `shopee_speed_warehouse`（`delivery/config.py` 的
`VENDORS`），刻意**不是**比照 2026-09-13 蝦皮拆出的那三個新代碼（那三個
是直接把保險規則綁代碼本身），而是完全比照 "shopee" 的行為：

1. 加進 `COOPERATION_TYPE_VENDORS`，讓這批人員的詳細頁跟「蝦皮三輪」
   一樣會出現「合作方式」下拉選單，靠合作方式（二輪承攬/二輪雇傭/三輪
   雇傭）決定要不要備強制險/公會加保證明/營業用第三責任險。
2. 加進 `DOC_TYPES` 裡 `police_clearance`（良民證）項目的
   `exclude_vendors`，這批人員一樣不用交良民證。
3. 沒有動 `CLIENT_VENDORS`（只有 UD 用）、`TEST_DRIVE_REQUIRED_VENDORS`
   （只有 UD/UC 用）、也沒有新增任何 `shopee_speed_warehouse_*` 開頭的
   專屬 DOC_TYPES 項目——因為規則要完全一樣，不需要另外訂。
4. 車輛管理、意外事件、CSV 批次匯入的廠商下拉選單/說明文字都是直接讀
   `VENDORS` 清單產生，這次新增後自動出現，不用額外改（除了
   `import_form.html` 上手打的「可填代號」說明文字，這個是純文字提示
   不是程式邏輯，一併更新了）。

### 測試

`tests/test_delivery_doc_types.py` 新增
`test_shopee_speed_warehouse_has_identical_rules_to_shopee`：逐一比對
`shopee` 跟 `shopee_speed_warehouse` 在每一種合作方式下算出來的應備
文件清單完全相同，確保之後如果有人在某處加了 "shopee" 專屬規則卻忘記
同步加這個新代碼，測試會抓出來。全部測試（`python3 -m unittest
discover -s tests -p "test_*.py"`）1196 個全數通過；另外用實際的
Jinja2 環境確認 `vehicle_form.html`／`import_form.html` 都能正常渲染
出新廠商選項。

### 使用者需要知道的事

這次改動**不需要任何手動部署步驟**。合併後，車輛管理／人員資料／CSV
匯入／LINE 群組回報的廠商選單都會自動出現「蝦皮三輪速配倉」這個選項，
應備文件規則會跟「蝦皮三輪」一模一樣。之後如果這批人員的規則需要改成
跟「蝦皮三輪」不一樣（例如發現實際上不用備哪個文件、或要多備哪個
文件），需要再回來請 Claude 調整 `DOC_TYPES`。

## 合約產生器新增「上傳廠商版本合約」（2026-09-14）

使用者提出：有些客戶規定要用廠商自己指定格式的合約書，不能用材霈自己
的版本，問能不能在合約產生器一樣填表單產生我們的標準版，但另外把
廠商的版本也存進系統留底。討論了幾輪才定案，過程中確認了兩個關鍵
決策：

1. **不是「二選一」，是「兩份並存」**：系統照舊自動產生材霈自己的標準
   版合約（表單填寫、套版產生 Word/PDF 這整段完全不動），廠商版本合約
   是**額外多上傳**的附件，不會取代、也不會被取代。這樣做的原因是總表
   （`/contract-summary`）、跟派遣契約產生器的連動，讀的都是這裡送出的
   **表單資料欄位**（甲方名稱、`vendor_id` 等），不是讀合約檔案本身，
   所以有沒有另外上傳廠商版本完全不影響那些功能，也不用擔心「改了合約
   產生器的檔案產出邏輯結果連動壞掉」這種風險。
2. **不限定廠商、預設隱藏**：不在廠商管理額外維護一份「哪些廠商規定用
   自己合約」的名單，改成每一筆合約列表上都有一個「上傳」的入口，同仁
   自己判斷要不要用；只有真的上傳過，才會多顯示一個「下載廠商版」的
   按鈕，沒上傳的合約畫面跟以前一模一樣，不會多出一堆空欄位。另外也
   跟使用者確認過：這個改動**完全不會動到**另一個完全獨立的功能「專案
   合約維護」（`/project-contracts`，送資料到外部 Google 試算表/Apps
   Script 那一套）——那邊「從合約產生器帶入」讀的是既有的 `blob_path`
   （公司標準版），這次新增的欄位它不會去讀，兩邊互不影響。

### 這次做了什麼

1. **`client_contract_storage.py`** 新增 `upload_vendor_contract_file()`
   ——跟既有的 `upload_contract_docx()`／`upload_contract_pdf()` 共用同一
   個 bucket、同一個 `"client_contracts/"` 路徑前綴，`download_file()`／
   `delete_file()` 既有的前綴檢查不用另外調整。
2. **`services/client_contract_service.py`** 新增
   `set_vendor_contract_file(submission_id, blob_path, filename,
   uploaded_by)`——寫入四個新欄位（`vendor_contract_blob_path`／
   `vendor_contract_filename`／`vendor_contract_uploaded_by`／
   `vendor_contract_uploaded_at`），跟 `blob_path`／`pdf_blob_path` 是
   完全獨立的欄位，不會互相覆蓋。重複上傳直接覆蓋掉上一次的路徑跟時間
   戳記，GCS 上的舊檔案不會自動清掉（沒有實際影響，只是不會再被任何
   連結指到）。
3. **`client_contract_routes.py`** 新增兩個路由：
   - `POST /client-contracts/{id}/upload-vendor-file`：格式（僅 PDF／
     WORD）跟大小（20MB）限制沿用「專案合約維護」既有的
     `CONTRACT_FILE_ALLOWED_EXTENSIONS`／`CONTRACT_FILE_MAX_BYTES`，不
     另外重訂一套；權限比照下載/預覽的 `_can_preview_or_download()`
     （送出者本人/主管/平台管理員，加上服務部門主管），不是只看送出人
     鏈——服務部門主管也能幫忙補上傳廠商簽回來的合約。失敗一律帶錯誤
     代碼（`upload_error` 查詢參數）導回列表頁顯示對應提示訊息，不會
     讓同仁對著空白畫面不知道發生什麼事。
   - `GET /client-contracts/{id}/vendor-file`：下載廠商版本合約，權限
     跟上傳同一套規則。
   - 刪除整筆合約紀錄時（`client_contract_delete()`），
     `vendor_contract_blob_path` 也會一併清掉，不留孤兒檔案。
4. **`templates/client_contract_home.html`** 列表新增「廠商版本合約」
   欄位：有上傳過的話顯示「下載廠商版」連結（滑鼠移過去可以看到檔名跟
   上傳人），下面永遠有一個小型上傳表單（已上傳過的話按鈕文字變成
   「重新上傳」，方便換版本）；頁面上方新增 `upload_error` 錯誤訊息
   橫幅。

### 測試

`tests/test_client_contract_routes.py` 新增 `UploadVendorFileRouteTests`
（9 個：沒有權限/找不到紀錄/沒選檔案/檔名空白/副檔名不對/儲存空間未
設定/檔案太大/正常上傳/服務部門主管也能上傳）跟
`DownloadVendorFileRouteTests`（4 個：沒有廠商檔案/其他人看不到/本人
可下載/服務部門主管可下載）；`DeleteRouteTests.
test_owner_can_delete_record_and_its_files` 擴充驗證刪除紀錄時廠商版本
檔案也會一起清掉。全部測試（`python3 -m unittest discover -s tests -p
"test_*.py"`）1209 個全數通過；另外用實際的 Jinja2 環境確認
`client_contract_home.html` 在有/沒有上傳過廠商版本、以及顯示錯誤訊息
橫幅的情況下都能正常渲染；用 `TestClient` 確認兩個新路由沒有被既有的
`/client-contracts/{submission_id}/...` 路由蓋掉。

### 使用者需要知道的事

這次改動**不需要任何手動部署步驟**（沒有新的環境變數、沒有需要另外
執行的遷移指令，用的還是既有的 GCS 儲存設定）。合約產生器列表頁上
每一筆合約都會多一個「上傳廠商版本合約」的小表單，平常用不到可以
完全忽略，畫面上不會有任何變化；真的需要用廠商指定格式合約的客戶，
上傳之後同一列會多一個「下載廠商版」的按鈕。

## 安全性修復批次：Excel 公式注入、登入防暴力破解、Cookie Secure 旗標（2026-09-14）

使用者請先確認 `DELIVERY_SESSION_SECRET_KEY` 環境變數已經設定好之後
（前一次全系統複查的🔴最高風險項目），接著請 Claude 直接動手修復複查
報告裡列出的兩項🟠高風險、以及🟡中風險裡的兩項（其中「職缺系統免登入
銜接的 PIN 碼只有簽章沒加密」這項使用者要求先評估能不能整個下架，
另外討論，不在這次修復範圍內）。

### 1. Excel 匯出的公式注入（🟠高風險）

**問題**：合約/契約總表（`services/contract_summary_excel.py`）跟配送部
補款/假別查詢（`delivery/excel_export.py`）匯出 Excel 時，客戶名稱、
統編、人員、原因這些自由文字欄位如果剛好打了以 `=`／`+`／`-`／`@`
開頭的內容，openpyxl 存進 `.xlsx` 時會被標記成公式（`data_type="f"`），
管理員打開匯出的 Excel 時會被當成可執行的公式跑出來（例如
`=HYPERLINK(...)` 導去釣魚網站）。

**修法**：兩個檔案的 `_build_workbook()` 都新增 `_sanitize_cell()`，
寫入儲存格前，字串開頭是那四個字元的話補一個前導單引號——比照 OWASP
的建議做法，讓 openpyxl 存成純文字（`data_type="s"`），Excel 打開時
只會照字面顯示（含那個單引號），不會被當成公式執行。全形符號（例如
班別總表常用來表示「無資料」的「－」）不會被誤判，因為半形 `-` 跟全形
`－` 是不同字元。header（欄位名稱）是程式寫死的字串，不是使用者輸入，
不用另外處理。

**測試**：`tests/test_contract_summary_excel.py` 新增
`SanitizeCellTests`（直接測 `_sanitize_cell()` 的各種情況）跟
`FormulaInjectionInWorkbooksTests`（驗證三個 `build_*_workbook()` 函式
產出的 `.xlsx` 真的把公式看起來的內容存成純文字）；
`tests/test_delivery_repayment_sickleave.py` 的 `ExcelExportTests`
新增一個等價測試。

### 2. 登入沒有防暴力破解機制（🟠高風險）

**問題**：整個平台共用同一套帳密系統，有四個登入入口（`/login`、
`/delivery/login`、`/management/login`、`/hr/login`，都呼叫同一個
`platform_accounts.authenticate()`），原本沒有任何錯誤次數限制，理論上
可以寫程式對著已知帳號一直亂猜密碼。

**修法**：`platform_accounts.py` 新增帳號鎖定機制——連續密碼錯誤達
`LOGIN_MAX_FAILED_ATTEMPTS`（5 次）就鎖定這個帳號
`LOGIN_LOCKOUT_SECONDS`（15 分鐘），鎖定期間 `authenticate()` 直接擋掉、
不會再花成本跑 PBKDF2 密碼比對；登入成功會清掉失敗次數紀錄（門檻是
「連續」失敗，不是帳號史上累計失敗次數，同仁偶爾打錯一兩次密碼不會
被誤鎖）。失敗次數/鎖定時間存在新的 Firestore collection
`login_lockouts`，用 Firestore 而不是記憶體變數，是因為 Cloud Run
服務可能會有多個執行個體，記憶體變數在個體之間不會同步，重開機/
擴縮容也會遺失。

**刻意選擇「鎖帳號」而不是「鎖 IP」**：Cloud Run 前面的請求來源 IP
不一定可靠（共用辦公室網路、之後如果加 CDN／負載平衡器也可能只看得到
中繼 IP），鎖帳號雖然理論上任何人知道帳號名稱就能觸發鎖定，但攻擊者
本來就需要先知道帳號名稱才有意義去猜密碼，兩害相權取其輕。

四個登入頁的錯誤訊息也都更新：密碼錯誤顯示「帳號或密碼錯誤」，帳號
被鎖定時改顯示「登入失敗次數過多，帳號已暫時鎖定，請稍後再試。」，
用 `platform_accounts.is_locked_out()` 判斷要顯示哪一種。

**測試**：`tests/test_platform_accounts.py` 新增
`IsLockedOutTests`／`RecordFailedLoginTests`／`ClearLoginLockoutTests`／
`AuthenticateLockoutIntegrationTests`（共 13 個，模擬 Firestore 文件
驗證鎖定判斷、失敗次數累加、達門檻設定鎖定時間、登入成功清除紀錄、
不存在的帳號不會誤觸發鎖定邏輯）；`tests/test_login_routes.py` 新增
`LoginSubmitLockoutMessageTests`（3 個，驗證 `/login` 顯示對的錯誤
訊息）。`/delivery/login`／`/management/login`／`/hr/login` 三個備援
登入頁的訊息切換邏輯跟 `/login` 完全相同（同一段程式碼複製四份），
沒有另外重複寫三次測試。

### 3. 登入 Cookie 沒有強制要求走 HTTPS 傳輸（🟡中風險）

**問題**：四個 `SessionMiddleware`（`main.py`／`delivery/app.py`／
`management/app.py`／`hr/app.py`）原本沒有設定 `https_only`，Starlette
預設是 `False`，登入 cookie 沒有 `Secure` 屬性。Cloud Run 本來就是
HTTPS 終止，實際風險低，但如果之後自訂網域設定不小心開放了 HTTP，
登入憑證可能被明文傳輸。

**修法**：四個 `SessionMiddleware` 都加上 `https_only=True`，瀏覽器
只在 HTTPS 連線時才會送出這顆登入 cookie。這是保險性質的設定調整，
沒有新增測試（Starlette 的行為本身有官方測試覆蓋，這裡只是改一個
參數值）；有跑過全部測試（見下方）確認沒有意外破壞既有的登入/session
相關測試。

### 這次沒有處理的項目

中風險裡「職缺系統免登入銜接的 PIN 碼只有簽章沒加密」跟「SSO 連結
理論上可重複使用」這兩項，使用者要求先評估能不能整個下架這個功能
（免登入銜接職缺維護系統），評估結果另外回報，不在這次的修復範圍。
低風險項目使用者要求全部處理完這批之後再討論優先順序。

### 測試

全部測試（`python3 -m unittest discover -s tests -p "test_*.py"`）
1235 個全數通過。

## 低風險安全性修復批次（2026-09-14）

延續前一批高／中風險修復（見上面「安全性修復批次：Excel 公式注入、
登入防暴力破解、Cookie Secure 旗標」，那批已開 PR 但當時還沒合併）
之後，使用者確認完低風險項目清單、逐項問清楚細節後要求動工的一批。
這批是獨立的分支/PR，跟前一批彼此不互相依賴，哪個先合併都不影響
另一個。

**這次沒有處理的項目**：低風險清單裡的第 5 項（獎金積分重複送出，
實際是「小雞點數自費申請」）使用者確認不用改；第 6 項（AI 客服提示詞
注入，`handlers/message_handler.py`）使用者明確要求**這次完全不要
動招募機器人的程式碼**，本批完全沒有碰這個檔案；職缺維護系統的 SSO
（`job_portal_sso.py`）評估移除一事，另外獨立處理，不在這批範圍內。

### 1. 內部密鑰比對改用防時間側錄的寫法

`delivery/routes/webhook_routes.py`（4 處）、`delivery/routes/
reminder_routes.py`（2 處）、`management/routes/reminder_routes.py`
（1 處）、`hr/routes/reminder_routes.py`（2 處）、`portal_routes.py`
（1 處）——這 10 處都是 Cloud Scheduler／GAS 專案呼叫的內部端點，原本
是 `if 密鑰 != 收到的值` 這種普通字串比對，理論上可以透過量測伺服器
回應時間差異，一個字元一個字元猜出密鑰（機率極低，但業界標準做法是
一律改用防時間側錄的比對函式）。`main.py` 的 `/internal/load-test-
message` 等端點跟密碼比對（`platform_accounts.hash_password`）本來
就是用 `hmac.compare_digest()`，這次把剩下沒跟上的 10 處也改成一樣
的寫法，行為完全不變（密鑰值對就通過、不對就拒絕），同仁完全不會
感覺到任何差異。

### 2. 密碼加密迭代次數調高（相容舊密碼，不強迫重設）

`platform_accounts.py` 的 `PBKDF2_ITERATIONS` 從 20 萬次調高到 60 萬次
（OWASP 現行建議的下限），採用使用者選定的「方案 a」：

- `hash_password()` 存密碼時，把「這次用了幾次」也存進雜湊字串裡
  （新格式「次數$鹽值$雜湊值」三段，舊格式是「鹽值$雜湊值」兩段）。
- `verify_password()` 驗證時，優先讀雜湊字串裡記錄的次數；讀不到
  （舊格式）就當作是用改版前的 20 萬次雜湊出來的（`_LEGACY_
  PBKDF2_ITERATIONS`）。

效果：**現有同仁的密碼完全不受影響、不用重設、不會被登出**——下次
改密碼、或帳號管理員幫忙重設密碼時，新密碼才會自動套用 60 萬次的
新標準，舊密碼會一直用舊次數驗證，直到被改過為止，不需要另外跑
遷移程式。

### 3. 配送部 webhook 收到格式錯誤資料時回傳乾淨的 400

`delivery/routes/webhook_routes.py` 的三支 POST 端點（表單回覆／
領還車回報／意外事件回報，都是 delivery-gas-project 那支 GAS 程式
轉發過來的）原本如果收到的內容不是合法 JSON（或 JSON 格式對但不是
物件），會讓程式一路噴出「未預期錯誤」（500），現在改成新增的
`_parse_json_body()` 統一先驗證格式，格式不對就回傳清楚的 400 跟
錯誤訊息。純粹是讓之後從錯誤紀錄分辨「GAS 那邊傳來的資料有問題」
跟「我們自己程式壞了」更容易，不影響任何正常情況下的行為。

### 4. 上傳檔案改成真的檢查內容，不只信任瀏覽器回報的類型

新增共用模組 `file_type_sniff.py`：只看檔案開頭幾個位元組的「檔頭
簽章」（PDF 開頭是 `%PDF-`、JPEG/PNG 各有自己的簽章、新版 Office
docx/xlsx/pptx 都是 ZIP 容器格式、舊版 doc/xls/ppt 都是微軟複合文件
格式），驗證檔案的真實內容跟宣稱的類型（瀏覽器回報的 content_type，
或使用者自己打的副檔名）是否相符，擋掉「把其他檔案改副檔名/content_
type 偽裝成允許類型上傳」這種手法。沒有額外安裝套件（python-magic
需要系統另外裝 libmagic，Cloud Run 環境不確定有沒有，用檔頭判斷
維護成本低、對這裡要擋的風險已經足夠）。

套用範圍（全部是需要登入才能用的頁面，不是對外開放的端點）：

- `hr/routes/{care_log,health_check,license,training}_routes.py` 的
  `_read_optional_file()`、`management/routes/{document,kpi,
  meeting}_routes.py`、`delivery/routes/sick_leave_routes.py`、
  `delivery/routes/vendor_routes.py`（人員證件上傳）——這些原本已經
  有 `content_type in 允許清單` 的檢查，現在多加「檔案內容也要符合」
  這個條件（新增 `is_allowed_upload()`）。
- `client_contract_routes.py` 的廠商版本合約上傳、`project_contract_
  routes.py` 的合約檔案上傳——原本只看副檔名（`.pdf`/`.doc`/`.docx`），
  現在加驗證檔案內容跟副檔名相符（新增 `content_matches_claimed_
  extension()`）。
- `job_listing_routes.py`（職缺圖檔）、`me_routes.py`（薪資補款佐證
  照片）——這兩處原本完全沒有檢查檔案類型，只檢查大小，現在加上
  「檔案內容要真的是 JPEG 或 PNG 圖片」的檢查（新增 `looks_like_
  image()`）。

同仁正常操作（上傳真正的 PDF/Word/Excel/圖片）完全不受影響，只有
偽裝過的檔案會被擋下來、顯示格式錯誤訊息。

### 5. 合約總表接近顯示上限時顯示警示

`contract_summary_routes.py` 的 `_RECORDS_LIMIT`（5000 筆，總表撈
資料的查詢上限，不是 Firestore 本身的限制）新增 `_NEAR_LIMIT_
WARNING_THRESHOLD`（上限的 80%，也就是 4000 筆）：客戶合約或派遣
契約任一種**權限過濾前的原始筆數**（代表系統整體資料量，不是這個
帳號實際看得到的筆數）達到這個門檻時，總表頁面最上面會出現一行
提醒文字，請聯繫工程師調高上限設定。在真的接近上限之前，畫面上
不會有任何變化。

### 測試

新增 `tests/test_file_type_sniff.py`（23 個，涵蓋各種檔案類型的
正常辨識跟偽裝檔案的擋下情境）；`tests/test_platform_accounts.py`
新增密碼雜湊相關測試（新格式存迭代次數、舊格式相容、迭代次數/鹽值
格式錯誤時回傳 False 而不是丟例外）；`tests/test_contract_summary_
routes.py` 新增 `NearLimitWarningTests` 跟相關測試（14 個）；
`tests/test_client_contract_routes.py`／`tests/test_project_contract_
routes.py` 調整既有測試的假資料，讓 fake 上傳內容符合真實檔頭格式。
全部測試（`python3 -m unittest discover -s tests -p "test_*.py"`）
1242 個全數通過。

### 使用者需要知道的事

這次改動**不需要任何手動部署步驟**（沒有新的環境變數、沒有需要
另外執行的遷移指令、沒有需要重新登入）。同仁操作上完全不會感覺到
差異，除非：(1) 上傳偽裝過的檔案會被新的格式檢查擋下來；(2) 合約
總表資料量真的接近上限時會多一行提醒文字；(3) 密碼在下次修改時
會自動套用更高的加密強度，這件事同仁不會、也不需要察覺到。

## 配送部系統：應徵名單新增「備註」欄位（2026-09-15）

`/delivery/applicants`（應徵名單）新增一欄「備註」，同仁可以自由輸入
文字，跟其他欄位（廠商/合作方式/試駕/狀態）一樣透過頁面上的「一鍵全部
更新」按鈕批次存檔，也可以個別在「錄取並建立人員」時一起帶入。

### 這次做了什麼

- `delivery/repository.py`：`_normalize_applicant()` 補上 `note` 欄位
  預設值（沒有資料時是空字串）；`bulk_update_applicants()` 新增
  `note` 欄位的處理——這欄是同仁自由輸入的文字，不像狀態/廠商/合作
  方式/試駕有固定選項可以比對合法性，只要表單有帶這個鍵就整段存入
  （去頭尾空白，含清空成空字串），不做內容限制。`upsert_applicant()`
  （Google 表單重複投遞同一人時的覆蓋邏輯）也把 `note` 加進「不隨表單
  重投而重置」的欄位清單，比照既有的試駕狀態——備註是同仁自己記錄的
  結果，不是表單填寫的內容，同一人重複投表單不該把已經寫好的備註洗掉。
- `delivery/routes/applicant_routes.py`：`bulk_update_applicants()`
  路由新增解析表單裡 `note_{applicant_id}` 欄位；`accept_applicant()`
  （錄取並建立人員）也一併讀取、存回這個人當下的備註內容，避免同仁
  剛打好備註就直接按「錄取」時備註被略過沒存到。
- `delivery/templates/applicants_list.html`：表格新增「備註」欄，
  一個自由輸入的文字框，放在「試駕」跟「狀態」之間。

### 測試

`tests/test_delivery_applicants.py` 新增
`NormalizeApplicantNoteTests`（預設值/既有值保留）、
`BulkUpdateApplicantsNoteTests`（存入/去空白/清空/沒帶欄位不動/
完全沒異動時不 commit，5 個，用假的 Firestore batch 驗證實際寫入的
內容）、`BulkUpdateApplicantsRouteTests`（2 個，驗證路由層 `note_`
欄位解析邏輯，跟其他欄位混在同一次表單送出時正確合併成同一筆
update）。全部測試（`python3 -m unittest discover -s tests -p
"test_*.py"`）1288 個全數通過。

### 使用者需要知道的事

這次改動**不需要任何手動部署步驟**。畫面上「應徵名單」的表格會
多一欄「備註」，同仁可以直接打字，跟其他欄位一樣要按「一鍵全部
更新」（或該筆的「錄取並建立人員」）才會真的存檔；沒有字數限制，
但建議簡短記錄重點就好（例如「電話一直沒接」「約好下週三面試」）。

## 配送部系統：應徵名單「其他回覆」拿掉表單固定欄位、頁面加寬（2026-09-15）

使用者操作應徵名單時回報「其他回覆」欄裡的「防詐騙提醒」「時間戳記」
沒有參考價值，畫面比較擁擠；另外要求整個頁面加寬方便操作。

### 這次做了什麼

- `delivery/form_webhook.py`：`other_answers()` 新增排除
  `FRAUD_WARNING_KEYWORD`（"防詐騙"）／`TIMESTAMP_KEYWORD`（"時間戳記"）
  這兩個關鍵字比對到的欄位，跟既有排除姓名/電話的寫法一致（欄位標題
  「包含」關鍵字就排除，不用完全比對，表單題目文字之後微調也不用
  跟著改程式碼）。這是共用的純函式，`/api/form-submission` webhook
  收到新表單回覆時儲存的原始資料完全不變，只有應徵名單頁面「其他
  回覆」欄顯示時會過濾掉這兩項——換句話說，這兩個欄位還是有存進
  資料庫，只是不顯示在畫面上。
- `delivery/templates/base.html`：`<main class="container">` 改成
  `<main class="container {% block container_class %}{% endblock %}">`，
  讓個別頁面可以疊加自己的版面寬度 class，不影響其他頁面（沒有蓋這個
  block 的頁面渲染出來就是原本的 `class="container "`，多一個空白不影響
  瀏覽器解讀）。
- `delivery/static/style.css`：新增 `.container-wide { max-width:
  1440px; }`（原本 `.container` 是 1080px）。
- `delivery/templates/applicants_list.html`：套用
  `{% block container_class %}container-wide{% endblock %}`，只有這
  一頁變寬，配送部系統其他頁面（人員詳細、廠商列表等）版面完全不受
  影響。

### 測試

`tests/test_delivery_form_webhook.py` 新增
`test_excludes_fraud_warning_and_timestamp_keys`。全部測試
（`python3 -m unittest discover -s tests -p "test_*.py"`）1289 個
全數通過。另外用這台機器上已經裝好的 Playwright + Chromium（見系統
環境說明），把 `applicants_list.html` 用假資料直接渲染成靜態 HTML、
起本機網頁伺服器載入畫面，在 1600px／1440px／1024px 三種寬度下截圖
確認：「其他回覆」欄不再出現防詐騙提醒/時間戳記、頁面明顯變寬、
變窄時能正常降級（沒有破版）。

### 使用者需要知道的事

這次改動**不需要任何手動部署步驟**。畫面上會看到兩個變化：(1) 應徵
名單「其他回覆」欄不會再顯示「防詐騙提醒」「時間戳記」這兩項（原始
表單資料本身沒有被刪除，只是不顯示）；(2) 應徵名單這一頁的可用寬度
變寬了，其他頁面（例如人員詳細頁）維持原本寬度不變。

## 配送部系統：應徵名單預設隱藏「已錄取」／「放棄」、新增刪除功能（2026-09-15）

使用者要求應徵名單畫面預設不要再看到已經走完流程的「已錄取」「放棄」
紀錄，並且加一個刪除功能，方便清掉重複投遞、測試資料等不需要保留的
應徵紀錄。

### 這次做了什麼

- `delivery/repository.py`：`applicant_matches_filters()` 原本只有
  「放棄」狀態預設隱藏，現在「已錄取」也比照辦理——沒有搜尋姓名、也
  沒有明確篩選狀態時，這兩種狀態都不會出現在清單裡；主動搜尋姓名、或
  直接把篩選狀態選成「已錄取」／「放棄」，就能查回來，行為跟原本
  「放棄」的邏輯完全一致，維持既有「事後回頭查得到」的設計。
- 新增 `delete_applicant()`：整筆刪除 `applicants` 集合裡的一筆文件。
  應徵紀錄本身沒有另外上傳的檔案，不用像刪除人員那樣額外清 Cloud
  Storage；「已錄取」的應徵紀錄如果被刪除，**已經建立好的正式人員
  資料不會受影響**（`personnel_ref()` 是完全獨立的另一份 Firestore
  集合，`converted_personnel_id` 只是單向記錄「當初是哪一筆應徵紀錄
  轉過來的」，不是雙向連動）。
- `delivery/routes/applicant_routes.py`：新增 `POST /applicants/
  {applicant_id}/delete`，比照 `vendor_routes.py` 的
  `delete_personnel_submit()`——不可逆的刪除動作走 `admin_required`，
  只有配送部主管職級的帳號能刪，不是任何有配送部權限的帳號都能刪。
- `delivery/templates/applicants_list.html`：表格最右側新增「刪除」欄
  （只有主管角色的帳號才看得到這一欄），按鈕跳確認對話框才會真的送出，
  文案有提醒「已錄取建立的正式人員資料不會受影響」，避免同仁誤會刪除
  應徵紀錄會連帶刪掉已經建立的人員。提示文字段落也一併更新，說明「已
  錄取」「放棄」現在都預設不顯示。

### 測試

`tests/test_delivery_applicants.py` 新增 `test_hired_hidden_by_
default`／`test_hired_shown_when_searching_by_name`／
`test_hired_shown_when_explicitly_filtering_status`（比照既有的
withdrawn 測試）、`DeleteApplicantTests`（驗證只刪那一筆 Firestore
文件）、`DeleteApplicantRouteTests`（2 個：授權時刪除並導回列表頁、
沒授權時 `redirect` 短路完全不呼叫刪除）。全部測試（`python3 -m
unittest discover -s tests -p "test_*.py"`）1295 個全數通過。另外用
Playwright 把畫面渲染出來實際點擊「刪除」按鈕，確認會跳出正確文字的
確認對話框，按「取消」不會誤送出表單。

### 使用者需要知道的事

這次改動**不需要任何手動部署步驟**。畫面上會看到：(1) 應徵名單預設
只會顯示「未面試」「已面試」的人，「已錄取」跟「放棄」都不會自動
出現，要查的話用上方搜尋姓名、或篩選狀態選「已錄取」／「放棄」；
(2) 表格最右側多一欄「刪除」，**只有主管職級的帳號才看得到**，一般
專員帳號看不到這個按鈕。刪除前會跳確認對話框，按下去無法復原，但
不會影響已經錄取、建立好的正式人員資料（人員資料要刪除的話，還是
到「人員詳細頁」用既有的刪除功能）。

## 配送部系統：應徵名單「合作方式」「試駕」欄即時連動（2026-09-15）

使用者操作應徵名單時發現「選廠商 → 按更新 → 才跳出合作方式；選合作
方式 → 再按更新 → 才跳出試駕」，要按兩次「一鍵全部更新」（整頁重新
整理）才能一路選到試駕結果，要求改成選了就馬上跟著顯示/隱藏，不用
等更新。

### 這次做了什麼

- `delivery/routes/applicant_routes.py`：`applicants_list()` 新增把
  `TEST_DRIVE_REQUIRED_VENDORS`（`["ud", "uc"]`）、
  `TEST_DRIVE_REQUIRED_SHOPEE_COOPERATION_TYPES`（`["three_wheel_
  employed"]`）這兩個原本只有後端 `applicant_needs_test_drive()` 用
  的常數，也傳給模板——瀏覽器端要即時判斷同一套規則，不能只靠後端
  判斷。
- `delivery/templates/applicants_list.html`：
  - 「合作方式」「試駕」這兩欄的 `<select>` 改成**一律都渲染出來**
    （不再是後端 `{% if %}` 判斷要不要輸出這段 HTML），初始的顯示/
    隱藏狀態還是照後端算出來的結果決定（`style="display:none"` +
    `disabled`），行為跟改版前完全一樣；差別是現在多了一個「-」佔位
    符號跟 `<select>` 同時存在，用 CSS `display` 切換哪一個看得到。
  - 新增 `<script>`：`syncApplicantRow(id)` 讀取目前瀏覽器裡「廠商」
    「合作方式」下拉選單的即時選擇值，用跟後端 `applicant_needs_test_
    drive()` 完全一樣的規則（照抄一份給 JS 用，不是重新設計規則）
    決定「合作方式」「試駕」這兩欄要不要顯示——`廠商`／`合作方式`
    下拉選單加上 `onchange="syncApplicantRow(...)"`，選了就立刻套用，
    不用等表單送出。隱藏的 `<select>` 同時加上 `disabled`，避免使用者
    切換廠商後，舊選的合作方式/試駕值還是被夾帶送出（`disabled` 的
    表單欄位瀏覽器不會送出，跟原本「沒有渲染這個 `<select>`」的效果
    一致）。
  - 頁面重新整理、或瀏覽器上一頁/下一頁從快取還原（bfcache）時，也會
    重新校正一次所有列的顯示狀態，避免瀏覽器自己還原選單的值但沒有
    連帶還原對應的顯示/隱藏狀態。

**維持既有的已知落差，沒有一併修正**：「蝦皮三輪速配倉」目前在合作
方式規則裡跟「蝦皮三輪」一視同仁（都能選合作方式），但試駕規則的
判斷只認「蝦皮三輪」（`vendor == "shopee"`），JS 這裡照抄後端現有的
規則，所以選「蝦皮三輪速配倉」＋「三輪雇傭」還是不會跳出試駕欄，跟
之前口頭跟使用者反映過的落差一致，不在這次修復範圍內（使用者要
確認要不要一併修再另外處理）。

### 測試

`tests/test_delivery_applicants.py` 新增
`ApplicantsListRouteContextTests`，驗證 `applicants_list()` route 有
把 `test_drive_required_vendors`／`test_drive_required_shopee_
cooperation_types`／`cooperation_type_vendors` 這三個規則傳進模板
context。全部測試（`python3 -m unittest discover -s tests -p
"test_*.py"`）1296 個全數通過。另外用 Playwright 把畫面渲染出來，
實際模擬瀏覽器操作驗證：選廠商後「合作方式」欄即時出現／選合作方式
「三輪雇傭」後「試駕」欄即時出現／改選廠商為 UD 後「合作方式」欄
即時隱藏但「試駕」欄保持顯示（UD 一律要試駕）／廠商清空後兩欄都
即時隱藏；也驗證了「蝦皮三輪速配倉」+「三輪雇傭」的組合，「試駕」欄
確實維持隱藏，跟上面提到的已知落差一致，不是這次改動不小心弄壞的。

### 使用者需要知道的事

這次改動**不需要任何手動部署步驟**。操作上會感覺到明顯差異：選
廠商、選合作方式的當下，畫面就會馬上決定要不要多顯示「合作方式」
「試駕」的選單，不用再先按「一鍵全部更新」重新整理頁面才看得到，
實際存檔還是要靠「一鍵全部更新」或「錄取並建立人員」按鈕。

## 配送部系統：蝦皮三輪速配倉補進試駕規則（2026-09-15）

### 背景

上一段「合作方式／試駕即時連動」上線後，使用者問起「合作方式跟試駕
是在哪個環節可以用」，過程中主動跟使用者反映了一個已知落差：試駕
規則只判斷 `vendor == "shopee"`（蝦皮三輪），沒有把 2026-09-14 新增
的「蝦皮三輪速配倉」（`shopee_speed_warehouse`）算進去，即使選了
「三輪雇傭」也不會跳出試駕欄——這個落差當時沒有立即修，留給使用者
自己決定要不要修。這次使用者確認要修，把「蝦皮三輪速配倉」也補進
試駕規則。

### 這次做了什麼

蝦皮三輪速配倉在應備文件／合作方式規則上，原本就是刻意比照「蝦皮
三輪」（`COOPERATION_TYPE_VENDORS = ["shopee", "shopee_speed_
warehouse"]`），只有試駕規則忘了一起改，所以修法是讓試駕規則改成
判斷「是不是 `COOPERATION_TYPE_VENDORS` 裡的廠商」，而不是寫死判斷
`vendor == "shopee"`，這樣以後這個清單再變動，試駕規則會自動跟著
同步，不用每次新增蝦皮系廠商都要記得去改兩個地方。

- `delivery/repository.py`：`applicant_needs_test_drive()` 把
  `vendor == "shopee"` 改成 `vendor in COOPERATION_TYPE_VENDORS`
  （新 import 這個常數）。
- `delivery/config.py`：更新 `TEST_DRIVE_REQUIRED_VENDORS` /
  `TEST_DRIVE_REQUIRED_SHOPEE_COOPERATION_TYPES` 上方的說明註解，
  以及應徵名單那段已經過時的「`COOPERATION_TYPE_VENDORS` 目前就是
  `["shopee"]`」註解（這句話在 2026-09-14 新增蝦皮三輪速配倉之後就
  不對了，一併修正）。
- `delivery/templates/applicants_list.html`：瀏覽器端 `syncApplicant
  Row()` 的 JS 判斷規則同步更新（`vendor === "shopee"` 改成
  `COOPERATION_TYPE_VENDORS.indexOf(vendor) !== -1`），並拿掉上一段
  刻意寫的「蝦皮三輪速配倉不算進試駕規則、不是漏寫」的說明註解，
  因為現在已經算進去了。

### 測試

`tests/test_delivery_applicants.py` 新增
`test_shopee_speed_warehouse_needs_test_drive_only_for_three_wheel_
employed`，驗證蝦皮三輪速配倉在「三輪雇傭」時需要試駕、其他合作
方式（含未選）不需要，跟蝦皮三輪的既有測試對稱。全部測試
（`python3 -m unittest discover -s tests -p "test_*.py"`）1297 個
全數通過。

### 使用者需要知道的事

這次改動**不需要任何手動部署步驟**。操作上的差異：應徵名單裡廠商
選「蝦皮三輪速配倉」、合作方式選「三輪雇傭」的應徵者，現在會跟
「蝦皮三輪」一樣跳出「試駕」欄位可以填寫，畫面即時連動跟存檔規則
都適用。

## 配送部系統：意外事件加車牌欄位、補款登記可編輯、報警改「是」「否」（2026-09-15）

### 背景

使用者一次提出三個需求：(1) 意外事件通報要多一個「車牌號碼」欄位；
(2) 補款登記要能編輯，目前只能新增跟核准，打錯字沒辦法修正；(3) 意外
事件的「是否報警」選項要從「有」「無」改成「是」「否」。這三個需求
影響到同仁已經在用的 LINE 群組回報範本跟資料庫既有紀錄，動工前先跟
使用者確認了三個範圍問題，結果：車牌號碼**只加在網站表單**，LINE
群組回報範本不變；「是否報警」改「是」「否」**網站表單跟 LINE 範本
都要改**（這是刻意的破壞性改動）；車牌號碼欄位設**選填**。

### 這次做了什麼

**1. 意外事件新增「車牌號碼」欄位（選填、只有網站表單有）**

- `delivery/repository.py`：`_INCIDENT_FIELDS` 加入 `license_plate`。
  `create_incident_event()` 特別處理：LINE 群組回報的 `data` 完全不會
  帶 `license_plate` 這個 key（LINE 範本沒有這一項），如果同仁事後在
  LINE 重傳同一起事件（人員名稱＋發生時間相同，會覆寫既有紀錄），
  不能因為這次沒帶這個 key 就把先前在網站上補登的車牌號碼洗成空白——
  只有 `data` 真的帶了這個 key（來自網站表單，包含表單裡刻意清空
  送出的情況）才會覆寫既有值；找不到既有紀錄、真的新建一筆時才用
  空字串當預設值。`update_incident_event()`（網站管理員編輯表單專用）
  不用特殊處理，因為呼叫端一律會明確帶這個欄位。
- `delivery/routes/incident_routes.py`：`_incident_form_data()` 新增
  `license_plate` 參數（預設空字串）；`new_incident_submit()`／
  `edit_incident_submit()` 都新增 `license_plate: str = Form("")`。
- `delivery/templates/incident_new.html`／`incident_edit.html`：新增
  「車牌號碼（選填）」文字輸入欄。
- `delivery/templates/incident_detail.html`：新增「車牌號碼」顯示列，
  沒填顯示「未填」。
- 意外事件清單頁（`incident_list.html`）跟每週未結案提醒訊息沒有把
  車牌號碼加進去，避免清單頁欄位過多、提醒訊息過長，需要看車牌號碼
  時進到詳細頁查看。

**2. 補款登記新增編輯功能（僅限主管）**

- `delivery/repository.py`：新增 `get_repayment(repayment_id)`／
  `update_repayment(repayment_id, vendor, personnel_name, amount,
  reason, occurred_date)`。編輯不會動 `approved`／`created_by`／
  `created_at`——核准狀態有自己的操作入口（`bulk_approve_repayments`），
  不該被編輯表單意外洗掉；已核准的登記一樣可以修正內容，核准狀態不
  受影響（核准本身仍是單向操作，沒有取消核准的路徑）。
- `delivery/routes/repayment_routes.py`：新增 `GET`／`POST
  /function/repayment/records/{repayment_id}/edit`，權限比照意外事件
  編輯用 `admin_required`——補款登記牽涉薪資金額，跟車輛歷史紀錄
  （任何登入同仁都能編輯自己補登的領還紀錄）性質不同。
- `delivery/templates/repayment_edit.html`：新增編輯表單頁（沿用
  `repayment_form.html` 的欄位配置，多帶入既有值）。
- `delivery/templates/repayment_records.html`：清單最後加一欄
  「編輯」連結，只有主管看得到。

**3. 意外事件「是否報警」改成「是」「否」（網站＋LINE 都改，破壞性改動）**

- `delivery/config.py`：新增 `POLICE_CALLED_VALUES = ["是", "否"]`，
  跟「是否聯繫家屬」「是否牽扯他人」繼續用的 `YES_NO_VALUES =
  ["有", "無"]` 分開；`POLICE_CALLED_VALUES` 同時套用到網站表單跟
  LINE 群組回報範本第 7 項。
- `delivery/incident_report.py`：解析邏輯把「是否報警」的驗證從
  `_YES_NO_FIELD_NAMES` 拆出來，改判斷 `POLICE_CALLED_VALUES`；格式
  錯誤訊息也拆成兩種：「是否報警」提示「請填「是」或「否」」，其餘
  兩項維持「請填「有」或「無」」。
- `delivery/routes/incident_routes.py`：`_validate_incident_form()`
  比照拆開判斷；新增／編輯表單都多傳一份 `police_called_values`
  給模板（跟 `yes_no_values` 分開，`police_called` 的下拉選單用
  `police_called_values`，另外兩個維持用 `yes_no_values`）。
- **既有舊資料的相容處理**：2026-09-15 之前建立的意外事件，資料庫裡
  `police_called` 欄位存的還是舊值「有」／「無」——這裡**不會**
  回溯修改資料庫裡的舊紀錄（詳細頁維持原樣顯示「有」／「無」，語意
  上還是看得懂），但編輯表單（`edit_incident_form`）開啟舊紀錄時，
  會把「有」視同新值「是」、「無」視同「否」預先選起來（純粹畫面
  顯示折衷，不會回寫資料庫，只有使用者真的按下「儲存修改」才會存成
  新值）——避免下拉選單開起來沒有任何選項被選中、看起來像沒填過。

### 測試

新增／更新測試檔：
- `tests/test_delivery_incident.py`：更新 LINE 範本測試 fixture 改用
  「是」，新增 `test_legacy_you_wu_police_called_value_now_rejected`
  驗證舊值「有」現在會被擋下。
- `tests/test_delivery_incident_routes.py`：新增車牌號碼傳遞測試、
  `police_called_values` context 測試、編輯表單舊紀錄「有」→「是」
  顯示折衷測試（並驗證不會就地污染原始 incident dict）。
- `tests/test_delivery_incident_repository.py`（新檔）：針對
  `create_incident_event()` 的 license_plate 資料流四種情境（新建
  預設空白／網站表單明確帶值／LINE 重傳不洗掉既有值／網站表單明確
  清空）、`update_incident_event()` 寫入車牌號碼、`get_repayment()`／
  `update_repayment()` 的基本行為，都用 mock 模擬 Firestore 集合驗證。
- `tests/test_delivery_repayment_edit.py`（新檔）：補款編輯表單／
  送出路由的權限、成功更新、金額格式錯誤、記錄不存在等情境。

全部測試（`python3 -m unittest discover -s tests`）1316 個全數通過。
另外用 Jinja2 直接渲染所有改到的模板（`incident_new.html`／
`incident_edit.html`／`incident_detail.html`／`repayment_edit.html`／
`repayment_records.html`），確認沒有變數缺漏或語法錯誤。

### 使用者需要知道的事

這次改動**不需要任何手動部署步驟**，但有一件事**務必要做**：

**請務必重新公告新的 LINE 群組回報範本給配送組同仁**——「是否報警」
那一項（第 7 項）現在要打「是」或「否」，不能再打「有」或「無」。
還在用舊範本、打「有」／「無」的同仁，送出後系統會回覆「❌『是否
報警』請填『是』或『否』。」，回報會被擋下、寫不進系統。建議把群組
裡原本釘選的範本訊息整個換成新版（第 7 項改成「是否報警：是」），
並在群組裡提醒一次。

其餘功能不用額外設定：
- 意外事件的網站表單（新增／編輯）多了一個「車牌號碼」欄位，選填，
  不填也可以送出。
- 補款記錄清單最後一欄多了「編輯」連結，只有主管帳號看得到，點進去
  可以修正金額、日期、廠商、人員姓名、原因說明；已經核准過的登記
  一樣可以編輯內容，核准狀態不會被改掉。

## 配送部系統：網站填寫車輛/意外事件時同步推播群組通知（2026-09-15）

### 背景

使用者要求：同仁如果不是在 LINE 群組回報，而是直接在配送部系統網站
填寫「車輛管理」（領還車）或「意外事件」，也要跟 LINE 群組回報一樣，
讓「配送組作業群組」即時收到通知，不用另外登入系統才看得到最新動態。

### 架構決定

真正推播用的 LINE Channel Token 一直留在另一個獨立 repo
`tsaipei-linebot/delivery-gas-project`（Google Apps Script 專案）那邊，
這個 repo（Cloud Run）從來沒有、也不需要那個 Token——沿用既有的安全
邊界，不把 Token 複製一份到這裡。做法是反過來：Cloud Run 主動呼叫
delivery-gas-project 新增的 `doGet(?type=DELIVERY_NOTIFY)` 橋接（見該
專案的 `Project7_DeliveryNotify.js`），請它用自己手上的 Token 推播到
跟 Project5（車輛回報）／Project6（意外事件回報）同一個「配送組作業
群組」（`VEHICLE_REPORT_GROUP_ID`）。這支橋接沿用該專案既有的 Web App
部署（`Project4_Schedule.js` 的 `doGet` 分派多一個 `type`），**不用
重新部署**。

**這次的範圍**：只有「網站手動補登領車/還車」（`manual_vehicle_event`）
跟「網站新增意外事件」（`new_incident_submit`）這兩個直接對應 LINE 群組
回報的動作會推播通知，比照 LINE 一直以來只回報這兩件事的範圍；車輛/
人員的其他管理操作（新增車輛、改狀態、改輪別/服務區域、編輯意外事件
內容等）不在這次範圍內。意外事件目前也只推播到「配送組作業群組」
（跟 LINE 回報同仁看到的那個群組一樣），**沒有**額外轉發到 LINE 那邊
才有的「管理／督導」第二個群組（`INCIDENT_NOTIFY_GROUP_ID`）——如果
之後也想要網站新增的意外事件轉發過去，需要另外處理。

### 這次做了什麼

**`delivery-gas-project`（PR #10）**：
- 新增 `Project7_DeliveryNotify.js`：`handleDeliveryNotify7_(e)`，驗證
  網址參數 `?secret=` 符合新增的指令碼屬性 `DELIVERY_NOTIFY_SECRET`
  後，把 `?text=` 推播到 `VEHICLE_REPORT_GROUP_ID`（用
  `CHANNEL1_LINE_TOKEN`）。
- `Project4_Schedule.js` 的 `doGet` 多一個 `type === 'DELIVERY_NOTIFY'`
  分派過去，沿用同一個部署。

**`tsaipeilinebot`（這個 repo）**：
- `delivery/config.py`：新增 `DELIVERY_NOTIFY_WEBHOOK_URL` /
  `DELIVERY_NOTIFY_WEBHOOK_SECRET` 環境變數。
- 新增 `delivery/group_notify.py`：`notify_group(text)`，呼叫上面那支
  GAS 橋接（GET 請求，`type`/`secret`/`text` 都放網址參數，因為 Apps
  Script Web App 讀不到自訂 HTTP Header）。沒設定好、逾時、網路錯誤都
  只回傳 `False`，不會拋例外——這是附加的通知功能，不該讓表單本身的
  送出跟著失敗。
- `delivery/routes/vehicle_routes.py`：`manual_vehicle_event()` 補登
  成功後呼叫 `group_notify.notify_group()`，訊息格式比照
  `vehicle_report.py` 的 LINE 回覆文字，加上「📝［網站新增］」前綴。
- `delivery/routes/incident_routes.py`：`new_incident_submit()` 送出
  成功後一樣呼叫，訊息格式比照 `incident_report.py` 的 LINE 回覆文字。

### 測試

`tests/test_delivery_group_notify.py`（新檔）：`is_configured()`／
`notify_group()` 的設定檢查、成功推播、非 200 回應、網路例外情境。
`tests/test_delivery_vehicle_routes.py`／`test_delivery_incident_routes.py`
新增測試，驗證成功時會呼叫 `group_notify.notify_group()`（含正確的
領車/還車/已登記/已更新用字），失敗／驗證不通過時不會呼叫。全部測試
（`python3 -m unittest discover -s tests -p "test_*.py"`）1329 個全數
通過。

### 使用者需要知道的事

這是跨兩個 repo 的功能，**兩邊都要處理**才會真的生效：

**`tsaipeilinebot`（這個 repo）合併後**：不需要手動部署步驟（Cloud
Run 自動部署），但要**額外設定兩個環境變數**才會真的推播（沒設定
的話，網站表單照樣正常運作，只是不會推播通知，不會跳錯誤）：
```
gcloud run services update recruitment-bot \
  --region asia-east1 \
  --update-env-vars DELIVERY_NOTIFY_WEBHOOK_URL="（見下方，跟 GAS 那邊的 Web App 網址一樣）",DELIVERY_NOTIFY_WEBHOOK_SECRET="（自己想一個夠長的隨機字串）"
```

**`delivery-gas-project`（另一個 repo，PR #10）合併後**：會透過現有
CI/CD 自動 `clasp push` + `clasp deploy`，**不用手動跑 clasp**。但要
在 Apps Script 編輯器「專案設定 → 指令碼屬性」手動新增
`DELIVERY_NOTIFY_SECRET`，值要跟上面 Cloud Run 設的
`DELIVERY_NOTIFY_WEBHOOK_SECRET` 完全一樣；另外把這個 Web App 現有
固定部署的 exec 網址（跟 LINE Webhook 設定的是同一個，可在「部署 →
管理部署作業」查到）設進 Cloud Run 的 `DELIVERY_NOTIFY_WEBHOOK_URL`。

設定好之後：同仁在網站補登領車/還車、或新增意外事件，「配送組作業
群組」就會即時收到一則「📝［網站新增］」開頭的通知，內容跟 LINE 群組
回報看到的格式一樣。

### 追加：意外事件通知內容改成完整欄位＋同時通知第二個群組（2026-09-16）

使用者實測這個功能上線後，回報希望跟 LINE 群組回報的體驗完全一致：
1. **推播內容要跟 LINE 同仁貼的完整內容一樣**：原本網站新增意外事件
   推播的只有一句簡短確認（「✅ 已登記意外事件回報：林子椉（雇傭）…」），
   現在改成跟 LINE 範本一樣的完整 11 個欄位（新增
   `incident_routes._format_incident_notify_message()`），額外的
   「車牌號碼」欄位（只有網站表單才有）也會附在最後一行，只有真的有
   填才會出現。
2. **要同時通知第二個群組**：LINE 群組回報時，管理／督導層看的「第二個
   群組」（`INCIDENT_NOTIFY_GROUP_ID`）只有真的成功登記才會收到完整
   內容；網站新增原本完全沒有推播到這個群組。現在網站新增也會推播到
   這個第二個群組，內容跟第一個群組完全一樣。

**架構**：`group_notify.notify_group()` 新增 `also_notify_incident_group`
參數，為 True 時會在打給 GAS 橋接的網址上多帶一個 `alsoNotify=incident`
參數。**故意不直接傳第二個群組的 ID**——那個 ID 只有 GAS
（`delivery-gas-project`）那邊知道，Cloud Run 這邊只表達「這是一筆
意外事件，麻煩也通知第二個群組」的意圖，維持「群組 ID／Token 只留在
GAS 那邊」的既有安全邊界。GAS 那邊的改動見
`delivery-gas-project`（PR #11）：`Project7_DeliveryNotify.js` 收到
`alsoNotify=incident` 後，除了照舊推播到 `VEHICLE_REPORT_GROUP_ID`，
還會額外讀 `INCIDENT_NOTIFY_GROUP_ID` 這個指令碼屬性推播過去，沒設定
的話只記 log、不影響第一個群組的推播結果。車輛領還車
（`manual_vehicle_event`）不受影響，呼叫時不會帶這個參數。

**使用者不需要額外設定任何東西**：如果 `INCIDENT_NOTIFY_GROUP_ID`
先前已經因為 LINE 群組回報功能設定過，這次會直接沿用生效；沒設定過的
話行為維持原樣（只有第一個群組收到通知），不會報錯。

順便新增「假別查詢」頁面的編輯功能（管理員限定，比照意外事件編輯
`incident_routes.edit_incident_form` 的權限層級）：`repository.
get_sick_leave()` / `update_sick_leave()`、新路由
`GET/POST /delivery/function/sick-leave/records/{id}/edit`、新模板
`sick_leave_edit.html`，查詢頁列表在管理員登入時多一欄「編輯」連結。
只更新登記內容本身，`approved`／`created_by`／`created_at` 不受影響
（核准狀態有自己的操作入口）。

**2026-09-16 追加：舊格式（只有 start_date/end_date，沒有
leave_date/hours）的紀錄原本不開放編輯（怕編輯表單顯示空白日期造成
混淆），使用者測試後要求這些舊紀錄也要能編輯。**改成：`sick_leave_
edit_form()` 用 `sick_leave_record_date()` 的退回邏輯，把 `start_date`
帶進「申請日期」欄位當預設值，「申請時數」欄位因為舊紀錄本來就沒有
存這個值，維持空白讓管理員自己填——**只要管理員真的按下「儲存修改」，
這筆舊紀錄就會補齊 `leave_date`/`hours`，自動變成新格式**，之後會被
正確算進年度額度累積（這也是目前唯一能讓舊紀錄「補救」進額度計算的
方法，因為系統沒辦法自動幫舊紀錄猜時數）。查詢頁列表的「編輯」連結
現在對所有紀錄（不分新舊格式）都會顯示。

測試：`tests/test_delivery_group_notify.py`／`test_delivery_incident_routes.py`
新增訊息內容/`alsoNotify` 參數相關測試；新增
`tests/test_delivery_sick_leave_routes.py`（編輯表單/送出）；
`tests/test_delivery_repayment_sickleave.py` 新增
`get_sick_leave`/`update_sick_leave` 的 repository 測試。全部測試
（`python3 -m unittest discover -s tests -p "test_*.py"`）1354 個全數
通過。

## 合約產生器新增第五個版本「傳統一口價」（2026-09-15）

### 背景

使用者提供一張報價截圖，要求合約產生器（`/client-contracts`）新增一個
「傳統一口價」版本——跟現有「時薪一口價」（`hourly_flat_rate`）差異
「只在附件」（使用者原話），主文（甲乙雙方欄位/合約期間/撤換條款/
匯款日）完全共用。截圖是比「時薪一口價」現有附件一（單一列 4 欄簡單
費率表）更細分時段的傳統報價表：平日／休息日／國定假日三大類，各自
再拆「第8小時內」「第9-10小時」「第11-12小時」（休息日是「第1-2/
3-8/9-12小時」）三個時段，共 9 個獨立費率。動工前跟使用者確認：(1)
「主文共用、只換附件」的做法方向正確；(2) 附件右側「由乙方招募派遣
員工／每員每月收取／以上報價不含稅」這 3 條說明文字寫死在範本裡，
不開放每次修改。

### 這次做了什麼

- `assets/client_contracts/master_template_traditional_flat_rate.docx`
  （新檔）：複製自 `master_template_hourly_flat_rate.docx`，只把附件一
  表格換掉（用 python-docx 腳本重建：表頭「項目/平日／休息日／國定
  假日/時段/費用/hr/說明」，9 個資料列，`項目` 欄跟 `說明` 欄都合併
  成單一儲存格橫跨全部 9 列，`平日／休息日／國定假日` 欄每 3 列合併
  一次），主文（合約條文）完全沒動，字體/框線比照原本範本（微軟正
  黑體、10pt、置中、單線框）。
- `services/client_contract_service.py`：
  - `CONTRACT_VERSIONS` 新增 `traditional_flat_rate`（`requires_sign_
    date`／`requires_severance_clause` 都是 True，跟時薪一口價/實支
    實付同一組）。
  - 新增 `TRADITIONAL_RATE_FIELDS`（9 個欄位名稱清單，`rate_weekday_
    8h`／`rate_weekday_9to10h`／`rate_weekday_11to12h`／`rate_
    restday_1to2h`／`rate_restday_3to8h`／`rate_restday_9to12h`／
    `rate_holiday_1to8h`／`rate_holiday_9to10h`／`rate_holiday_
    11to12h`，刻意不跟其他版本欄位名稱衝突）。
  - `render_contract_docx()`／`save_submission()` 都新增
    `traditional_rates: dict` 參數，直接照 `TRADITIONAL_RATE_FIELDS`
    這份清單展開成個別欄位，沒給的欄位預設空字串。
- `client_contract_routes.py`：`_PRICING_FIELDS_BY_VERSION` 新增這個
  版本（引用 `TRADITIONAL_RATE_FIELDS`，9 個都要填才算完整）；
  `client_contract_submit()` 從表單抓出這 9 個欄位組成
  `traditional_rates` dict，一併傳給 `render_contract_docx()`／
  `save_submission()`；`_duplicate_form_values()`（「複製」功能）也
  補上這 9 個欄位。
- `templates/client_contract_form.html`：新增
  `data-pricing-for="traditional_flat_rate"` 區塊（9 個費率輸入欄），
  沿用既有的 JS 顯示/隱藏機制（`applyPricingVisibility()`），不用
  改 JS。
- `services/contract_summary_service.py`：`_pricing_summary()` 新增
  這個版本的一句話摘要（因為 9 個費率放總表一欄放不下，只列平日第8
  小時內費率當代表值，完整報價要點進合約詳細內容看）。

### 測試

`tests/test_client_contract_service.py` 新增
`RenderTraditionalFlatRateContractDocxTests`（真的用 docxtpl 套版新
範本，驗證 9 個費率欄位都正確代換、沒有殘留 Jinja 標籤、說明欄固定
文字存在、缺欄位時代換成空白不是殘留標籤）；`tests/test_client_
contract_routes.py` 新增送出驗證測試（少填一個費率擋下、9 個都填
成功送出且 `traditional_rates` 正確傳給 service 層）；`tests/test_
contract_summary_service.py` 補上這個版本的摘要格式測試。全部測試
（`python3 -m unittest discover -s tests`）1338 個全數通過。

### 使用者需要知道的事

這次改動**不需要任何手動部署步驟**。操作上：合約產生器的「合約
版本」下拉選單多一個「傳統一口價」選項，選了之後會出現 9 個費率
輸入欄（平日/休息日/國定假日各 3 個時段），全部都要填才能送出；
產生出來的 Word 合約，主文（甲乙雙方、合約期間、撤換條款等條文）
跟「時薪一口價」一模一樣，只有附件一報價表格換成這 9 個時段的
費率，右側的「由乙方招募派遣員工／每員每月收取／以上報價不含稅」
說明文字是固定的，不能在表單上修改。

## 合約產生器：修正「代招版本仍顯示撤換條款欄位」的 CSS 錯誤（2026-09-17）

使用者回報：產生「白領代招」「台籍代招」合約時，「撤換人員通知期限
（日）」跟「資遣費用及預告工資由」這兩個欄位還是會出現在表單上，但
這兩個版本本來就不該有這兩個欄位。

### 根本原因

一開始檢查 `templates/client_contract_form.html` 跟 `client_contract_
routes.py`，切版本要不要顯示這兩個欄位的邏輯（`VERSION_REQUIREMENTS`
／`applyPricingVisibility()`）看起來完全正確，用 Playwright 純測
template（不載入 CSS）也真的會正確隱藏，一度以為使用者操作有誤。

後來把 `delivery/static/style.css` 也一起載入重新測試，才抓到真正
的問題：JS 是用 `el.hidden = true` 隱藏欄位，這要靠瀏覽器內建的
`[hidden] { display: none }`（屬於「使用者代理樣式表」）才會生效，
但 `delivery/static/style.css` 裡本來就有 `.form-stack label {
display: flex; ... }` 這條規則——**作者自訂的 CSS 規則，優先權本來
就比瀏覽器內建樣式表高**（不管 selector 的 specificity 高低），所以
凡是 `<label>` 元素被設定 `hidden` 屬性，只要它同時符合
`.form-stack label`，這條規則就會蓋掉 `display: none`，變成「屬性
設了 hidden，畫面卻還是看得到」。這不只影響這兩個欄位，理論上也影響
「簽約日期」那個欄位（`data-requires="sign_date"`）跟其他表單上任何
用同一招隱藏的 `<label>`。

### 修正方式

在 `delivery/static/style.css` 最前面加一條全域規則：
```css
[hidden] { display: none !important; }
```
用 `!important` 把「有 hidden 屬性就一定要隱藏」的優先權拉到最高，
不會被任何其他規則蓋掉。這是全站共用的樣式表，所以這個修正對所有
用 `.hidden`／`el.hidden` 隱藏欄位的表單都有效，不是只修合約產生器
這一處。

用 Playwright 重新測過：選「白領代招」「台籍代招」時，「簽約日期」
跟「撤換人員通知期限（日）」「資遣費用及預告工資由」都正確消失；
選其他版本時維持顯示。全部測試（`python3 -m unittest discover -s
tests`）1435 個全數通過。

### 使用者需要知道的事

這次改動**不需要任何手動部署步驟**，純粹是網頁樣式的修正，PR 合併
後 Cloud Run 重新部署就會生效。之後改版本下拉選單，代招版本就不會
再看到那兩個跟一般派遣合約有關的欄位了。

## 配送部系統：車輛管理新增「廠商」編輯功能（2026-09-16）

使用者發現車輛詳細頁的狀態、輪別、服務區域都各自有編輯表單，唯獨
「廠商」只有在新增車輛（`/vehicles/new`）當下能設定，之後完全沒有
地方可以改——比對後確認這是真的缺口，不是設計上刻意留白。

補上跟輪別/服務區域一模一樣的做法：
- `repository.set_vehicle_vendor(vehicle_no, vendor)`：只接受合法的
  廠商代碼，車輛不存在或代碼不合法都回傳 `False`、不會寫入。
- `vehicle_routes.py` 新增 `POST /vehicles/{vehicle_no}/vendor`。
- `vehicle_detail.html` 在「更新輪別」表單前面加一個一樣排版的
  「更新廠商」下拉選單＋按鈕。

測試：`tests/test_delivery_vehicle_routes.py` 新增
`UpdateVehicleVendorTests`（比照既有的 `UpdateVehicleWheelTypeTests`
寫法）。全部測試（`python3 -m unittest discover -s tests -p
"test_*.py"`）1355 個全數通過。**不需要任何手動部署步驟。**

（使用者同時詢問「意外事件的舊資料也需要能夠編輯」——查證後確認
意外事件的編輯功能本來就沒有排除舊資料，任何一筆意外事件、不分新舊
在詳細頁都看得到「編輯回報內容」按鈕，且早在 2026-09-15 就已經處理過
舊紀錄「是否報警」欄位的舊值相容顯示，這裡沒有實際程式碼變更，如果
使用者之後具體回報卡在哪一步，再回來這裡補記錄。）

## 配送部系統：車輛回報格式錯誤時附上正確範例（2026-09-16）

使用者要求：同仁在 LINE 群組回報「車輛管理」格式錯誤時，除了告知
哪裡錯，也直接附上一份正確範例，同仁照著範例重填貼上就好，不用
另外去找範本。

`delivery/vehicle_report.py` 新增 `_CORRECT_EXAMPLE`（一份完整的
領車格式範例：廠商/姓名/開始日期/結束日期/車號/服務門市），並在
`PARSE_ERROR_MESSAGES` 的四種格式錯誤訊息（`missing_fields`／
`invalid_vendor`／`ambiguous_dates`／`invalid_date`）後面都接上這份
範例。範例統一用領車格式示範（比還車常見），還車的差異（開始日期
留空、改填結束日期，服務門市那行改填還車地點）用一行文字補充說明，
不另外準備一份還車範例，避免訊息太長。

這是純文字調整，不影響 `parse_vehicle_report()` 的解析邏輯或
`error` 代碼，既有測試（只斷言 `error` 代碼，不斷言訊息文字）不受
影響。`delivery-gas-project` 那邊的 GAS 橋接（`Project5_Vehicle.js`）
只是把 Python 回傳的文字原封不動轉發到 LINE 群組，不需要跟著更新。

測試：`tests/test_delivery_vehicle.py` 新增
`ParseErrorMessagesIncludeExampleTests`（四種格式錯誤情境各驗證
回覆內容包含「正確範例」字樣）。全部測試（`python3 -m unittest
discover -s tests -p "test_*.py"`）1360 個全數通過。**不需要任何
手動部署步驟。**

## 配送部系統：車輛回報新增電話/待維修/備註欄位（2026-09-16）

使用者一次提出五項車輛管理的需求，先討論確認邏輯（尤其是「待維修」
這個新欄位在不同情境下該怎麼影響車輛狀態），確認完才動手：

1. LINE 回報格式（領車/還車）加三個選填欄位：電話、待維修、備註。
2. 網頁「手動補登事件」「編輯歷史紀錄」也要能填這三個欄位。
3. 車輛列表要能看到目前使用人的手機號碼。
4. 車輛的廠商要能自己修正（**這項其實 2026-09-16 稍早的 PR #134
   已經做完，這次沒有再動**，見上一節）。
5. 車輛管理主頁要比照「全台三輪車」試算表，一目了然看到地區、車號、
   廠商、目前使用人、手機號碼、狀態、停車地點、備註。

### 「待維修」欄位的規則（跟使用者討論確認過三輪才定案）

同仁去交車給司機、或還車當下，如果發現車輛其實故障了，「待維修」
欄填「是」（其餘任何值，包含留空、填「否」，都當作沒勾選——不因為
這個新欄位擋下整筆回報），車輛就直接變成「待維修」狀態，不用同仁
再另外進系統點一次「標記待維修」：

- **領車 + 待維修＝是**：前提還是要車輛目前是「可用」（沒有放寬
  `vehicle_event_error` 對領車的驗證），但車輛最終狀態不是變成
  「使用中」，而是直接變「待維修」。「目前使用人」統一留空——因為
  車子其實沒有真的被騎走，填了人名畫面上容易誤以為車在他手上（這是
  跟使用者討論後選定的「做法 A」，另一個選項「做法 B：填領車人
  姓名方便追蹤是誰通報的」使用者確認不需要）。
- **還車 + 待維修＝是**：這個規則同時涵蓋「原本使用中的車，用到一半
  發現故障」這個情境——同仁只要照正常「還車」回報（不用先假裝車輛
  沒事還車、再另外標記待維修），姓名、地點照實際狀況填，待維修勾
  「是」即可。跟正常還車一樣清空「目前使用人」，差別只在最終狀態是
  「待維修」不是「可用」。
- **車輛已經是待維修，又被回報「領車」**：這種「重複回報」情況
  維持原本「擋下」的行為（不會真的又寫入一筆），但錯誤訊息從原本
  跟「使用中」共用的籠統說法，改成專門講清楚「這台車已經是待維修
  狀態，不用重複回報」，讓同仁知道不是格式打錯。

### 程式碼變更

- `delivery/vehicle_report.py`：`_FIELD_PATTERNS` 新增
  `phone`／`needs_maintenance`／`note` 三個選填欄位的解析規則；新增
  `_is_yes()`，只有完全填「是」才算勾選待維修。`parse_vehicle_report()`
  回傳的 dict 多這三個 key。`_CORRECT_EXAMPLE` 範例文字補上這三欄的
  示範跟選填說明。`EVENT_ERROR_MESSAGES` 把 `not_available` 拆成
  `not_available`（使用中）跟新增的 `already_maintenance`（已經待維修）
  兩個獨立代碼跟訊息。`handle_vehicle_report()` 成功登記且有勾待維修時，
  回覆訊息額外附上一行「已同步標記待維修」。
- `delivery/repository.py`：
  - `vehicle_event_error()`：領車時依車輛現況分別回傳
    `already_maintenance`（本來就待維修）或 `not_available`（使用中）。
  - `record_vehicle_event()` / `update_vehicle_event()`：新增
    `phone`／`note`／`needs_maintenance` 參數，寫進事件紀錄；
    `needs_maintenance=True` 時車輛主檔狀態直接寫 `maintenance`、
    「目前使用人」清空，其餘情況維持原本領車/還車的狀態切換邏輯。
    `update_vehicle_event()` 保留原本「車輛目前已經是待維修時，跳過
    歷史紀錄編輯連動車輛主檔狀態」這個保護機制，並延伸適用到這次新
    加的待維修情境——如果要撤銷某筆事件造成的待維修狀態，要到車輛
    詳細頁按「解除待維修」，不能單靠編輯歷史紀錄表單悄悄改回來。
  - `get_vehicle()` / `list_vehicles()` 新增 `current_holder_phone`／
    `current_note` 欄位（跟隨事件更新，反映「目前使用人的電話」「目前
    備註」）；`get_vehicle_event()` / `list_vehicle_events()` 新增
    `phone`／`note`／`needs_maintenance` 欄位。這四個函式都對舊資料
    （這次上線前寫入的車輛/事件文件）補上預設值（空字串／False），
    沿用這個系統一貫「不回頭改寫舊資料、讀取時做相容處理」的做法，
    舊紀錄不會壞掉，只是這三個新欄位顯示空白。
- `delivery/routes/vehicle_routes.py`：`manual_vehicle_event()`／
  `edit_vehicle_event_submit()` 新增 `phone`／`note`／`needs_maintenance`
  三個表單欄位（待維修用下拉選單送出 `"1"`/空字串，不是 HTML
  checkbox——checkbox 沒勾選時瀏覽器根本不會送出這個欄位，用下拉選單
  可以確保後端一定收得到值）。手動補登成功推播到群組的訊息，如果有
  填電話/備註/待維修，也會一併附上。
- 範本：`vehicle_detail.html`（頂部資訊列加「目前使用人電話」「備註」；
  手動補登表單加電話/待維修/備註；歷史紀錄表格加電話/備註/待維修欄）、
  `vehicle_event_edit.html`（編輯表單加電話/待維修/備註）、
  `vehicle_list.html`（列表加「手機號碼」「備註」兩欄，對應需求 3、5）。

### GAS 那邊完全不用動

`delivery-gas-project` 的 `Project5_Vehicle.js` 只是把同仁在群組貼的
原始文字整段轉發給這個 repo 的 webhook，格式解析全部在 Python 這邊
做，這次新增欄位不影響轉發邏輯，**不需要 `clasp push` 或改任何
指令碼屬性**。

### 測試

`tests/test_delivery_vehicle.py` 新增：
`ParseVehicleReportTests`（電話/備註/待維修的解析、待維修只有填「是」
才算真的勾選的各種變化）、`EventErrorMessagesTests`
（`already_maintenance` 訊息確實跟 `not_available` 不同）；
`VehicleEventErrorTests.test_checkout_blocked_when_maintenance`
更新為驗證新的 `already_maintenance` 代碼。`tests/test_delivery_
vehicle_routes.py` 新增：`EditVehicleEventSubmitTests.
test_valid_submit_with_maintenance_flagged`、
`ManualVehicleEventGroupNotifyTests.
test_maintenance_flagged_event_notifies_with_maintenance_note`，
並更新既有測試補上新的表單欄位。全部測試（`python3 -m unittest
discover -s tests -p "test_*.py"`）1365 個全數通過。

### 使用者需要知道的事

**不需要任何手動部署步驟**，合併後就直接生效。操作上：

- 之後同仁在 LINE 群組回報車輛，可以多填「電話」「待維修」「備註」
  三行（不填也沒關係，不會被當成格式錯誤）；發現車輛故障時，
  「待維修」那行填「是」，系統就會直接把車輛標成待維修，不用再另外
  進網頁點一次。
- 網頁「車輛詳細」頁的「手動補登事件」表單、還有「編輯歷史紀錄」
  表單，都補上了電話、待維修（下拉選單）、備註這三欄。
- 車輛列表頁（`/delivery/vehicles`）現在會多顯示「手機號碼」跟
  「備註」兩欄，一目了然。
- 這次上線前的舊車輛/舊回報紀錄，因為當時系統還沒有這些欄位，這三欄
  會顯示空白，不是資料遺失，之後有新的回報才會開始有資料。

## 配送部系統：車輛管理／意外事件回報的是非題欄位，「是/否」跟「有/無」互通（2026-09-17）

使用者反映同仁在 LINE 回報時，常把「是」「否」跟「有」「無」這兩組
是非詞混著填（例如「是否報警」填成「有」、「是否聯繫家屬」填成
「是」），這兩組詞意思上是同一件事，沒必要因為填錯詞組就擋下整筆
回報。這次讓系統把兩組詞都當同義詞接受，寫入資料庫前正規化成該
欄位原本慣用的詞（不會因此多出第三種可能的值）：

- **意外事件回報**（`delivery/incident_report.py`）：新增
  `_normalize_police_called_value()`（「是否報警」欄，正規化成
  「是」/「否」）跟 `_normalize_yes_no_value()`（「是否聯繫家屬」
  「是否牽扯他人」欄，正規化成「有」/「無」），在驗證前先套用。
  這**取代了 2026-09-15 那次刻意把「是否報警」改成只認「是」「否」
  的嚴格化**（當時的理由是這欄跟其他欄位詞組不同、怕填錯），現在
  改成兩組詞都接受，回到比較寬鬆但更貼近同仁實際使用習慣的做法。
- **車輛管理**（`delivery/vehicle_report.py`）：`_is_yes()`（「待
  維修」欄）比照同樣邏輯，改成「是」「有」都算勾選。
- 這次調整**只影響「同仁打了另一組詞」這種情況的解析結果**，不影響
  任何驗證規則本身（該擋的格式錯誤還是照樣擋），也不影響任何已經
  寫入資料庫的舊紀錄。

測試：`tests/test_delivery_incident.py` 把原本驗證「有/無」會被擋下
的 `test_legacy_you_wu_police_called_value_now_rejected`（2026-09-15
那次嚴格化留下的測試）改寫成驗證兩組詞互通成功；`tests/test_delivery_
vehicle.py` 的 `_is_yes()` 測試補上「有」的情境。全部測試（`python3
-m unittest discover -s tests -p "test_*.py"`）1366 個全數通過。
**不需要任何手動部署步驟**，GAS 那邊不用動。

## 配送部系統：新增「裝備借還管理」模組（2026-09-17）

新增獨立模組，處理籃子、橘衣等配送裝備的借用/歸還/轉倉/採購/買斷/
核銷登記，比照車輛管理的呈現方式（主頁一張獨立卡片，進去才是子功能
清單）。純網頁表單登記，不走 LINE 群組回報格式。

### 資料模型

- `delivery/db.py`：新增 5 個 Firestore collection
  （`delivery_equipment_items`／`delivery_equipment_locations`／
  `delivery_equipment_stock`／`delivery_equipment_debt`／
  `delivery_equipment_transactions`），各自的 `X_ref()` helper。
- `delivery/config.py`：`EQUIPMENT_TRANSACTION_TYPES`（借用/歸還/
  轉倉/採購新增/買斷/核銷 6 種，固定清單）、
  `EQUIPMENT_TRANSACTION_TYPES_REQUIRING_PERSONNEL`（借用/歸還/買斷）、
  `EQUIPMENT_TRANSACTION_TYPES_REQUIRING_TWO_LOCATIONS`（只有轉倉）、
  `EQUIPMENT_ADMIN_ONLY_TRANSACTION_TYPES`（只有核銷）、
  `EQUIPMENT_ELIGIBLE_PERSONNEL_STATUS`（"employed"，只有在職人員能借）。
  **品項（籃子、橘衣……）跟放置點（新北所、桃園所……）刻意沒有寫在
  這裡**——這兩份清單是主管可以自己在網頁上新增/停用的動態清單，存在
  Firestore，這是這個模組跟系統其他清單（廠商、假別、文件類型……全部
  寫死在 `config.py`）刻意不同的地方，因為使用者明確要求要能自行
  增減品項/放置點。
- `delivery/repository.py` 最後一整節：品項管理、放置點管理（皆為
  「停用不刪除，除非完全沒有異動紀錄過」——`equipment_item_has_history()`
  /`equipment_location_has_history()` 查有沒有任何異動紀錄引用過這個
  品項/放置點 ID，有的話 `delete_equipment_item()`/
  `delete_equipment_location()` 會直接失敗，只能停用，避免刪掉後
  歷史紀錄查不到名稱、帳對不起來）、庫存（`delivery_equipment_stock`，
  文件 ID 是 `{放置點ID}__{品項ID}`，用「讀出現有值再寫回去」而不是
  Firestore 原子遞增，跟系統其他地方風格一致，配送部流量小暫不處理
  併發衝突）、尚欠（`delivery_equipment_debt`，文件 ID 是
  `{人員ID}__{品項ID}`）、異動登記主邏輯：
  - `equipment_transaction_error()`：純函式（Firestore 讀取跟驗證
    分開，方便單元測試），依序檢查數量合法性 → 需要騎士的類型有沒有
    選人、選的人缺件資料補齊了沒（沿用 `missing_documents()`，人員
    缺件清單同一套規則） → 借用/轉倉庫存夠不夠（`override_stock_check`
    可以略過這一項，給主管特批例外用） → 歸還/買斷數量有沒有超過
    實際尚欠。
  - `record_equipment_transaction()`：驗證通過才寫入異動紀錄，同步
    更新庫存/尚欠。**核銷（writeoff）刻意不走這個函式**——核銷邏輯
    跟其他 5 種完全不同（歸零尚欠、限主管、要填原因），獨立成
    `record_equipment_writeoff()`。
- `delivery/routes/equipment_routes.py`（新檔案）：
  `/delivery/equipment`（庫存總覽矩陣＋警戒值設定，管理員可見）、
  `/delivery/equipment/transaction`（單一頁面涵蓋 6 種異動類型，下拉
  選單切換時用 JavaScript 顯示/隱藏對應欄位，核銷選項只有管理員能
  看到、後端也重複擋一次非管理員送核銷）、`/delivery/equipment/records`
  （歷史紀錄查詢＋尚欠總表）、`/delivery/equipment/items`、
  `/delivery/equipment/locations`（品項/放置點管理，限管理員）。
- 樣板：`equipment_home.html`／`equipment_transaction_form.html`／
  `equipment_records.html`／`equipment_items.html`／
  `equipment_locations.html`；主頁 `home.html` 新增「裝備借還管理」
  卡片。

### 幾個實作時做的判斷（原始規格文件沒有寫死答案，這裡記錄下來，
如果使用者實際用起來覺得不對可以再調整）

1. **轉倉沒有拆成「轉出」「轉入」兩步**：原始規格把「轉出」「轉入」
   列成兩種異動類型，這裡合併成一個「轉倉」動作、一次選「來源放置點
   →目的放置點」，同一筆紀錄同時處理兩邊庫存，「轉入一定對應轉出」
   是資料結構保證的，不需要另外做「登記轉出後、等對方確認收到才算
   轉入」這種中途會有「在途中」狀態的兩步流程。如果之後發現運送中途
   真的需要「已出貨、對方還沒收到」這種待確認狀態，才需要拆成兩步。
2. **買斷（buyout）目前沒有限管理員操作**，只有核銷（writeoff）限
   管理員——因為買斷性質是「同仁登記騎士已經付錢了結」，同仁本來就是
   第一線在處理離職人員的裝備結算；核銷是「公司直接認賠、尚欠歸零」，
   風險層級不同。如果覺得買斷也該限管理員，`config.py` 的
   `EQUIPMENT_ADMIN_ONLY_TRANSACTION_TYPES` 直接加 `"buyout"` 即可。
3. **買斷單價不開放每筆異動個別填**，而是先在「品項管理」頁設定
   好每個品項的買斷單價，登記買斷時系統自動帶入計算總金額（不信任
   前端送來的單價，一律用當下品項設定的值）——如果這個品項還沒設定
   買斷單價，登記買斷會被擋下、提示先去品項管理設定。單價要算全新價
   還是折舊價，由使用者自己決定填多少，系統不強制任何公式。
4. **警戒值（第一層庫存提醒）沒有自動預設值**，每個「放置點×品項」
   組合預設是 0（代表不提醒），需要管理員自己到 `/delivery/equipment`
   頁面底下的「警戒值設定」逐一設定；跟庫存不夠時的「直接擋下申請」
   （第二層硬擋）是分開的兩件事，警戒值只影響要不要顯示 ⚠️ 提醒。
5. 主頁面上「借用登記」「歸還登記」兩個入口，實際上是同一個
   `/delivery/equipment/transaction` 表單頁帶不同的 `?type=` 參數
   預選下拉選單，不是各自獨立的頁面——6 種異動類型共用一份表單/驗證
   邏輯，同仁點進去下拉選單已經幫忙選好，體驗上跟獨立頁面一樣。

### 初始品項/放置點清單，需要使用者自己在網頁上新增

系統上線後這兩份清單是空的（動態清單，沒有寫死初始值也沒有寫遷移
腳本自動灌資料，因為這兩份清單設計上就是要讓使用者自己維護）。**部署
後請管理員身分登入，到「裝備借還管理」→「品項管理」新增以下 8 個
品項**（買斷單價欄位選填，之後可以再回來補）：
籃子、大籃子、橘衣、大橘衣、彈力繩、太空袋、黑網、UE籃子

**到「放置點管理」新增以下 4 個放置點：**
新北所、桃園所、台中所、高雄所

新增完成後，`/delivery/equipment` 首頁的庫存總覽矩陣就會顯示出來，
初始庫存都是 0，之後同仁用「採購新增」異動類型把現有庫存登記進去，
就是這個模組的起始資料。

### 測試

`tests/test_delivery_equipment.py`（純函式）：涵蓋
`equipment_transaction_error()` 的每一種擋下情境（數量不合法、缺件、
庫存不夠、尚欠不夠、主管特批略過庫存檢查但缺件照樣擋）。
`tests/test_delivery_equipment_routes.py`（路由層）：欄位驗證（轉倉
來源/目的不能相同、必填欄位）、核銷限管理員（非管理員直接送表單會被
擋下）、主管特批的值不會被非管理員的表單夾帶繞過、買斷單價自動依
品項設定計算（不信任前端送來的值）、品項/放置點管理的 CRUD。全部
測試（`python3 -m unittest discover -s tests -p "test_*.py"`）1395
個全數通過。

### 使用者需要知道的事

**不需要任何手動部署步驟**（跟其他配送部系統功能一樣，合併後 Cloud
Run 自動部署就直接生效），但**部署後有兩個步驟需要管理員身分手動
操作，系統不會自動幫你填**：

1. 登入後到「裝備借還管理」→「品項管理」，新增上面列的 8 個品項
   （籃子、大籃子、橘衣、大橘衣、彈力繩、太空袋、黑網、UE籃子）。
2. 到「放置點管理」，新增上面列的 4 個放置點（新北所、桃園所、
   台中所、高雄所）。

新增完成後就可以開始用「採購新增」把現有庫存登記進去，之後借用/
歸還/轉倉/買斷/核銷都在「裝備借還管理」首頁點進去操作。

以下 3 點是這次實作時做的判斷，不是規格文件裡明確拍板的答案，如果
用起來覺得不符合實際需求，請告訴我再調整（上面「幾個實作時做的
判斷」那節有更詳細的說明）：

- 轉倉只有一步（選來源→目的放置點，一次登記），沒有做「轉出後等
  對方確認收到才算轉入」的兩步流程。
- 買斷目前任何同仁都能登記（不像核銷限管理員）。
- 買斷單價要在「品項管理」頁先設定好（新全價或折舊價都可以，系統
  不限制），登記買斷時會自動帶入計算，不能每筆異動個別改單價。

## 配送部系統：補款/假別/車輛/意外事件/裝備異動都加上「主管刪除」按鈕（2026-09-17）

使用者要求：「配送系統 所有的登記除了編輯格子，可以新增刪除的按鍵給
主管嗎」。確認範圍後（補款登記／假別登記／車輛領還車事件／意外事件
回報／裝備借還管理異動紀錄），這次補上：

- 補款記錄、假別查詢兩個列表頁頂端新增「新增補款登記」「新增假別
  登記」按鈕（原本只能從主頁另外點進登記表單）。
- 五個功能各自的記錄/詳細頁都新增「刪除」按鈕，**限管理員操作**
  （跟編輯是同一個權限層級）。

### 已核准的補款/假別不能刪除

使用者明確選擇：已核准的補款/假別登記**不開放刪除**，因為核准代表
可能已經對過帳、算進薪資發放或年度假別額度累積，刪掉會讓帳/額度對
不起來。`repository.delete_repayment()` / `delete_sick_leave()` 內部
會擋掉已核准的登記（回傳 `False`），畫面上的刪除按鈕本身也只在未核准
的紀錄上顯示——雙重保險，不是只靠前端藏起來。車輛領還車事件、意外
事件回報沒有「核准」這種需要對帳的欄位，管理員可以刪除任何一筆。

### 裝備異動紀錄的刪除／編輯：需要同步復原庫存/尚欠

裝備借還管理跟其他四個功能不一樣的地方：庫存/尚欠是「每筆異動即時
累加的流水帳」（見上一節「裝備借還管理」的說明），如果只是單純刪除
一筆歷史異動的 Firestore 文件，庫存/尚欠總表會直接對不起來（例如
刪掉一筆借用紀錄，那個人的尚欠不會自動消失）。這次新增：

- `repository._apply_equipment_transaction_effect(..., sign)`：把原本
  寫在 `record_equipment_transaction()` 裡的庫存/尚欠異動邏輯抽成
  共用函式，`sign=1` 是新增時的效果，`sign=-1` 是精確的反向（每個
  delta 直接乘 -1），用同一份邏輯的鏡像來復原，不用另外維護一份
  「相反規則」、改一邊忘了改另一邊。
- `delete_equipment_transaction()`：刪除前先用 `sign=-1` 復原這筆
  紀錄的效果（核銷是把核銷掉的尚欠加回去），再刪除文件本身。
- `update_equipment_transaction()`：先用 `sign=-1` 復原舊效果，再用
  復原後的庫存/尚欠狀態驗證新填的值（`equipment_transaction_error()`），
  驗證沒過就把復原的效果加回去（rollback），通過才套用新效果、寫入
  新的欄位值——不會留下「復原了但沒套用新效果」的中間狀態。**核銷
  不吃這套邏輯**，因為核銷的「數量」是核銷當下的實際尚欠、不是使用者
  填的值，改動量沒有意義，這裡編輯核銷只能改「原因」欄位。
  **品項／異動類型不開放修改**——換品項或換類型等於是完全不同性質
  的異動，這裡設計成「改錯了就刪除重登記」，不是硬要在原地改。

### 樣板技術細節：核准表單跟刪除表單不能巢狀

`repayment_records.html`／`sick_leave_records.html` 原本把整張表格
包在一個 `<form>`（核准所選用）裡，這次要在每一列再加一個獨立的
「刪除」`<form>`——但 HTML 不允許 `<form>` 巢狀。改成：核准用的
checkbox/按鈕都加上 `form="repayment-approve-form"`（或
`sick-leave-approve-form`）屬性，關聯到頁面上另一個獨立、不包住表格
的 `<form>`，讓表格本身不在任何表單裡，每一列才能各自再放一個刪除用
的 `<form>`。

### 測試

`tests/test_delivery_records_delete.py`（新檔案）：
`delete_repayment()`／`delete_sick_leave()`（已核准擋下、不存在回傳
False、成功刪除）、`delete_vehicle_event()`／`delete_incident_event()`
（不存在回傳 False、成功刪除）。`tests/test_delivery_equipment.py`
新增：`_apply_equipment_transaction_effect()` 每種類型 sign=1/-1
互為鏡像、`delete_equipment_transaction()`（含核銷分支）、
`update_equipment_transaction()`（核銷只改原因、驗證失敗會 rollback、
驗證通過會套用新效果並寫入欄位）、`get_equipment_transaction()`。
各功能的路由層測試（`test_delivery_repayment_edit.py`／
`test_delivery_sick_leave_routes.py`／`test_delivery_vehicle_routes.py`
／`test_delivery_incident_routes.py`／`test_delivery_equipment_routes.py`）
補上對應的刪除／編輯路由測試。全部測試（`python3 -m unittest
discover -s tests -p "test_*.py"`）1432 個全數通過。

### 使用者需要知道的事

**不需要任何手動部署步驟**，合併後就直接生效。操作上：

- 補款記錄、假別查詢頁面頂端多了「新增○○登記」按鈕，不用再回主頁找。
- 五個功能的刪除按鈕都只有主管（管理員）帳號登入才看得到。
- 補款/假別**已核准的紀錄不能刪除**，如果核准錯了，麻煩直接告訴我
  再處理，不是用刪除來補救。
- 裝備異動紀錄的編輯/刪除都會自動處理庫存/尚欠的加減，不用自己
  另外手動調整。

## 配送部系統：退保連動提醒（2026-09-17）

使用者要求：騎士辦理退保時，如果名下裝備尚有未歸還/尚欠，系統要提醒
經辦人先追回裝備再退保，避免退保後追不回來。

**這個系統目前沒有「退保」這個功能**——`platform_companies.py`（材霈
自己的派遣公司牌照主檔）裡把加保/退保列為「之後」才要做的人資功能，
現在還沒有對應的按鈕或流程。跟使用者確認後，**先暫時掛在人員詳細頁
「人員狀態」改成「離職」這個既有動作上**，之後真的串接退保流程時，
只需要把觸發點換過去（判斷邏輯——查這個人裝備尚欠——是共用的
`repository.list_equipment_debt()`，不用重寫）。使用者確認純提醒即可，
**不擋下**改成離職這個動作。

- `delivery/routes/vendor_routes.py` 的 `personnel_detail()`：新增查詢
  `repository.list_equipment_debt(personnel_id=...)`（裝備借還管理既有
  的尚欠查詢，回傳這個人尚未歸還的每個品項/數量），解析品項名稱後放進
  `equipment_debt` context 傳給樣板。
- `delivery/templates/personnel_detail.html`：
  - 如果目前人員狀態已經是「離職」、且還有裝備尚欠，畫面上方顯示常駐
    警告（列出品項/數量，附連結到裝備借還管理記錄查詢頁篩選這個人）——
    這是給「已經離職但裝備還沒追回」這種已經發生、需要事後盯著處理的
    情況看的。
  - 「人員狀態」下拉選單改選成「離職」的當下（`change` 事件），如果這
    個人有裝備尚欠，用 `window.alert()` 跳出提醒視窗——這是給「正要
    改成離職」這個動作當下的即時提醒，純提醒不擋送出，經辦人看到後
    還是可以繼續把狀態改成離職再按「一鍵全部更新」送出。
  - 只有在職中借用未歸還裝備（正常狀態）**不會**顯示成警告，只有
    「已離職＋還有尚欠」才算需要注意的問題狀態。

### 手機登記方式（純討論，這次沒有改程式碼）

使用者提到公務手機同時跑 UC、蝦皮兩個外送平台的帳號，問「手機」要
怎麼登記。因為「手機」本質上就是一般裝備品項，**不需要改程式**——
主管直接到「品項管理」頁自行新增「手機」這個品項即可，跟籃子、橘衣
是同一套機制。有提醒使用者：目前裝備借還是「算數量、不記個別序號」
的設計（例如「還有 3 支」，但不知道是哪 3 支、哪支門號在誰手上），
使用者確認先不特別限定登記方式，之後看實際使用狀況再統一規則，暫時
不需要加序號欄位。

### 測試

`tests/test_delivery_personnel_equipment_debt.py`（新檔案）：
`personnel_detail()` 正確查詢裝備尚欠並解析品項名稱、沒有尚欠時回傳
空列表、品項被刪除時顯示「（已刪除品項）」佔位文字。全部測試
（`python3 -m unittest discover -s tests -p "test_*.py"`）1435 個
全數通過。

### 使用者需要知道的事

**不需要任何手動部署步驟**，合併後就直接生效。人員詳細頁把某人的
人員狀態改成「離職」時，如果這個人在裝備借還管理裡還有借用未歸還的
裝備，畫面會跳出提醒視窗（純提醒，還是可以繼續存檔）；如果已經是
離職狀態、裝備還沒追回，頁面上方也會有常駐的警告文字，直到裝備歸還
或核銷/買斷結案才會消失。

## 修正各產生器「送出時間」顯示成 UTC 時間（2026-09-17）

使用者回報合約產生器的送出時間不對，要求改成台灣時間。

### 根本原因

`services/client_contract_service.py`／`dispatch_contract_service.py`／
`chicken_points_service.py` 送出合約/契約/雞排點數申請時，都是用
`datetime.now(timezone.utc)` 存 `created_at`（存 UTC 時間本身沒問題，
資料庫本來就該存不受時區影響的絕對時間），但對應的樣板（`client_
contract_home.html`／`dispatch_contract_home.html`／`chicken_points_
home.html`／`project_contract_form.html`）都是直接把這個 datetime
物件印出來，**沒有轉換成台灣時間再顯示**，導致畫面上看到的送出時間
比實際時間晚了 8 小時（例如晚上 9 點送出的合約，畫面顯示下午 1 點）。
這是四個模組共用的同一個 bug，不是只有合約產生器一處。

### 修正方式

在共用的 `platform_templating.py`（`fastapi.templating.Jinja2Templates`
的實例，所有這些模組的路由都從這裡 import `templates`）新增一個
Jinja 篩選器 `taipei_time`，用既有的 `config.TAIPEI_TZ`
（`pytz.timezone("Asia/Taipei")`，`handlers/message_handler.py`／
`services/daily_report_service.py` 本來就在用同一個常數）把時間轉成
台灣時間再格式化成 `YYYY-MM-DD HH:MM`。四個模板的 `{{ record.created_
at }}` 都改成 `{{ record.created_at | taipei_time }}`。這個篩選器是
共用元件，之後其他模組如果也需要顯示時間戳，直接套用同一個篩選器
即可，不用各自重新處理時區轉換。

新增 `tests/test_platform_templating.py`（6 個測試：UTC 轉台灣時間、
沒有 tzinfo 的 datetime 當 UTC 處理、跨日情況、`None`、非 datetime
值原樣返回、自訂格式）。全部測試（`python3 -m unittest discover -s
tests -p "test_*.py"`）1441 個全數通過。

### 使用者需要知道的事

這次改動**不需要任何手動部署步驟**，合併後就直接生效，之後在合約
產生器、派遣契約產生器、專案合約維護、雞排點數這幾個地方看到的
「送出時間」都會是台灣時間，不用再自己心算加 8 小時。

## 配送部系統：車輛管理的服務區域改成主管可自行新增/停用（2026-09-18）

使用者要求：車輛管理的服務區域需要能自行增減，不要再像原本那樣寫死在
`config.py`、拓點到新縣市就要找 Claude 加代碼。改成跟裝備借還管理的
品項/放置點同一套「動態清單」設計：存 Firestore
（`delivery_vehicle_service_areas`），主管可以在網頁上自行新增/停用/
（無歷史紀錄時）刪除，不用改程式碼。

- `delivery/db.py`：新增 `VEHICLE_SERVICE_AREAS_COLLECTION` 跟
  `vehicle_service_areas_ref()`。
- `delivery/repository.py`「車輛服務區域管理」那節：
  `list_vehicle_service_areas()`／`get_vehicle_service_area()`／
  `create_vehicle_service_area()`／`set_vehicle_service_area_active()`／
  `vehicle_service_area_has_history()`／`delete_vehicle_service_area()`，
  完全比照裝備品項/放置點的「停用不刪除，除非完全沒有車輛在用」設計。
  `set_vehicle_service_area()`（車輛詳細頁的更新入口）驗證邏輯改成查
  `get_vehicle_service_area()` 存不存在，不再查固定的 `SERVICE_AREA_MAP`。
- `delivery/config.py`：移除 `SERVICE_AREAS`／`SERVICE_AREA_MAP` 這兩個
  固定清單常數。
- `delivery/vehicle_status_report.py`：`_region_breakdown()`／
  `build_vendor_fleet_report()`／`build_fleet_status_report()` 這幾個
  純函式不再自己 import `SERVICE_AREAS`，改成由呼叫端（
  `vehicle_routes.vehicle_status_report_page()`）傳入
  `repository.list_vehicle_service_areas(include_inactive=True)` 查來的
  清單——**故意包含已停用的服務區域**，這樣即使某個服務區域後來被停用，
  底下如果還掛著車輛，報告照樣能顯示正確的區域名稱，不會被硬塞進
  「未分區」。
- `delivery/routes/vehicle_routes.py`：新增
  `/vehicles/service-areas`（管理頁，限管理員）、
  `/vehicles/service-areas/new`、`/vehicles/service-areas/{id}/active`、
  `/vehicles/service-areas/{id}/delete`，跟 `/vehicles/status-report`
  一樣要註冊在 `/vehicles/{vehicle_no}` 之前，不然會被當成車號吃掉。
  車輛清單/新增/詳細頁的服務區域下拉選單改用
  `repository.list_vehicle_service_areas()`（只列啟用中的），畫面上顯示
  名稱時額外用 `include_inactive=True` 查一份 id→name 對照表，確保車輛
  目前指到的服務區域即使已經停用，畫面上還是能正確顯示名稱，不會顯示
  成「未設定」。
- 樣板：`vehicle_list.html`／`vehicle_form.html`／`vehicle_detail.html`
  的下拉選單/顯示邏輯，`a.code` 全部改成 `a.id`（服務區域現在用
  Firestore 文件 ID 當識別碼，不再是英文代號）；新增
  `vehicle_service_areas.html`（管理頁，樣式比照
  `equipment_locations.html`）；`vehicle_list.html` 頁首新增「服務區域
  管理」按鈕（限管理員）。

### 既有資料怎麼辦：一次性遷移腳本

既有車輛的 `service_area` 欄位存的是舊代碼（`"taipei"`／`"new_taipei"`
…），改版後如果 Firestore 裡完全沒有服務區域資料，這些舊代碼會顯示成
「未設定」（不是資料不見了，只是查不到對應名稱）。新增
`scripts/seed_vehicle_service_areas.py`，把原本 7 個服務區域**用跟舊
代碼完全相同的文件 ID** 建進 Firestore，既有車輛資料完全不用搬移，
建立完成後就能立刻正確顯示名稱。這支腳本可以放心重複執行——已經存在
的服務區域（例如主管已經手動改過名稱）會直接跳過，不會覆蓋。

### 測試

`tests/test_delivery_vehicle_service_areas.py`（新檔案）：
`list/get/create/set_active/has_history/delete_vehicle_service_area()`
的各種情境、`set_vehicle_service_area()` 改用動態查詢後的驗證邏輯、
服務區域管理路由（限管理員）。`tests/test_seed_vehicle_service_areas.py`
（新檔案）：遷移腳本的規劃邏輯（已存在的服務區域會跳過、不會覆蓋）。
`tests/test_delivery_vehicle_status_report.py`／
`tests/test_delivery_vehicle_routes.py`：既有測試改成明確傳入
`service_areas` 參數/mock 動態查詢，確保報告排序/名稱行為不變。全部
測試（`python3 -m unittest discover -s tests -p "test_*.py"`）1454 個
全數通過。

### 使用者需要知道的事：部署後要手動跑一次遷移腳本

**這次有一個手動步驟，一定要做，不然車輛列表/報告會暫時看不到服務
區域名稱**：合併部署完成後，請用有 GCP 憑證的環境（例如 Google Cloud
Shell）依序執行：

```bash
git pull
python -m scripts.seed_vehicle_service_areas
```

腳本會先列出即將新增的 7 個服務區域（台北、新北、桃園、新竹、台中、
台南、高雄），輸入 `yes` 才會真的寫入。跑完之後，車輛列表/詳細頁/
「一鍵整理車輛狀況」報告就會恢復正常顯示，既有車輛的服務區域資料
完全不受影響。

跑完遷移腳本後，之後如果要新增/停用/刪除服務區域（例如公司拓點到新
縣市），直接到「車輛管理」→「服務區域管理」網頁上操作即可，不用再
找我加代碼。

## 配送部系統：合作方式（cooperation_type）改成主管可自行維護的動態清單，並擴大到全部廠商跟應徵名單（2026-09-18）

使用者原本只是想在「車輛管理」清單頁加一欄「騎手身份」（顯示/篩選
這台車目前使用人的合作方式），討論過程中發現這個功能會依賴
`cooperation_type` 這個欄位，但這個欄位有兩個問題：

1. 原本只有蝦皮／蝦皮三輪速配倉這兩個廠商代碼會被要求填合作方式
   （`COOPERATION_TYPE_VENDORS` 寫死在 `config.py`），UD/UC/順豐等其他
   廠商的人員完全沒有這個欄位可以選，畫面會整片空白。
2. 合作方式的選項（二輪承攬/二輪雇傭/三輪雇傭）也是寫死在
   `config.py` 的固定清單，使用者希望「之後都有可能變動」，要能自己
   在網頁上維護，不要每次調整都要找 Claude 改代碼。

跟使用者討論後確認：合作方式要改成跟裝備借還管理的品項/放置點、車輛
服務區域同一套「Firestore 動態清單」設計，而且要開放給**全部廠商**
設定，不只蝦皮系列；同時使用者也要求應徵名單（`/delivery/applicants`）
的合作方式選單一併改成動態（原本也是抄一份 `COOPERATION_TYPES` 固定
清單）。「騎手身份」欄位本身**還沒開始做**，是下一步——這次先把
合作方式這個底層欄位做成動態、且對所有廠商都能填，「騎手身份」才有
資料可以顯示。

### 設計上跟其他動態清單不一樣的地方：一筆合作方式可以套用到多個廠商

裝備品項、車輛服務區域都是「單一擁有者」的清單，合作方式不一樣：
DOC_TYPES 的保險文件規則（`shopee_contract_insurance`／
`shopee_employed_own_car_insurance` 等）是直接比對 `cooperation_type`
這個字串值本身（例如 `"two_wheel_contract"`），蝦皮跟蝦皮三輪速配倉
必須繼續共用完全相同的這幾個 ID，不能各自獨立一份清單，不然這兩個
廠商可能會慢慢長出不同的選項、悄悄弄壞共用的保險規則判斷。

跟使用者討論兩個做法：
- A：每個廠商各自獨立一份合作方式清單（風險：蝦皮跟蝦皮三輪速配倉的
  清單可能會逐漸長歪，弄壞共用的保險規則）。
- B（使用者選定）：新增/編輯合作方式時，勾選這筆資料適用哪些廠商——
  蝦皮/蝦皮三輪速配倉可以繼續勾選同一筆（沿用原本共用的行為），其他
  廠商可以獨立勾自己的，也可以跟蝦皮共用，彈性由主管自己決定。

因此 `delivery_cooperation_types` 這個 Firestore collection 的每筆文件
多了一個 `vendors`（字串陣列）欄位，查詢時用 Firestore 的
`array_contains` 運算子（`list_cooperation_types(vendor="shopee")`）。

### 程式碼異動

- `delivery/db.py`：新增 `COOPERATION_TYPES_COLLECTION` 跟
  `cooperation_types_ref()`。
- `delivery/repository.py`「合作方式管理」那節：`list_cooperation_types
  (vendor="", include_inactive=False)`／`get_cooperation_type(id)`／
  `create_cooperation_type(name, vendors, type_id="", created_by="")`／
  `update_cooperation_type(id, name, vendors)`／
  `set_cooperation_type_active(id, active)`／
  `cooperation_type_has_history(id)`（人員或應徵者有任何一筆在用這個
  ID 就算有歷史紀錄，比照裝備品項「有異動紀錄就只能停用不能刪除」）／
  `delete_cooperation_type(id)`。
  另外修正 `applicant_needs_test_drive()`：不再檢查
  「廠商是不是蝦皮系列」，改成直接看合作方式的值是不是
  `"three_wheel_employed"`（試駕判斷邏輯本來就已經是看這個固定 ID 本身，
  這個 ID 現在對哪些廠商開放完全交給主管在管理頁面上決定）；
  `bulk_update_applicants()` 的合作方式驗證改成呼叫
  `get_cooperation_type()` 確認 ID 存在，不再查固定的
  `COOPERATION_TYPE_MAP`。
- `delivery/config.py`：移除 `COOPERATION_TYPES`／`COOPERATION_TYPE_MAP`
  ／`COOPERATION_TYPE_VENDORS` 這幾個固定清單常數，改成註解說明動態
  清單怎麼運作；`TEST_DRIVE_REQUIRED_SHOPEE_COOPERATION_TYPES =
  ["three_wheel_employed"]` 保留（試駕規則要靠這個固定 ID 判斷，遷移
  腳本會確保這個 ID 繼續存在）。
- `delivery/routes/vendor_routes.py`：
  - `personnel_detail()`／`new_personnel_form()` 的合作方式選單改成
    `repository.list_cooperation_types(vendor=vendor_code)`，**拿掉原本
    只有蝦皮系列才顯示欄位的 `show_cooperation_type` 判斷**，所有廠商
    一律顯示這個欄位（沒有設定任何選項的廠商，選單就只有「尚未決定」
    一個選項，主管要去「合作方式管理」頁面幫這個廠商新增選項）。
  - `create_personnel_submit()`／`bulk_update_personnel()` 的合作方式
    驗證改成 `repository.get_cooperation_type()` 存在性檢查（`
    create_personnel_submit` 另外還檢查這個合作方式的 `vendors` 陣列
    有沒有包含目前這個廠商代碼，避免透過改網址硬塞不屬於這個廠商的
    合作方式）。
  - 新增合作方式管理頁面：`GET /cooperation-types`（限管理員）、
    `POST /cooperation-types/new`、`POST /cooperation-types/{id}/edit`
    （改名稱＋改適用廠商勾選）、`POST /cooperation-types/{id}/active`、
    `POST /cooperation-types/{id}/delete`；`vendor_list.html`／
    `applicants_list.html` 頁首都加了「合作方式管理」按鈕（限管理員）。
- `delivery/routes/applicant_routes.py`：
  - `applicants_list()` 改傳 `cooperation_types_by_vendor`（
    `{廠商代碼: [{id, name}, ...]}` 的字典，用 `list_cooperation_types()`
    依每筆的 `vendors` 陣列分組）給樣板，取代原本的
    `cooperation_types`／`cooperation_type_vendors` 兩個固定清單。
  - `accept_applicant()`：**順便修正一個既有 bug**——原本合作方式的
    保留邏輯寫死 `vendor != "shopee"`，只認蝦皮，沒把蝦皮三輪速配倉
    算進去，代表蝦皮三輪速配倉的應徵者錄取時，合作方式很可能被悄悄
    清空。改成動態驗證（`vendor in coop.get("vendors", [])`）後這個
    廠商代碼也會正確保留合作方式，不用再特別列出廠商名稱。
- `delivery/routes/webhook_routes.py`（`/api/form-submission`，GAS 表單
  webhook 的接收端）：合作方式驗證改成 `repository.get_cooperation_type
  ()` 存在性檢查，不再查固定的 `COOPERATION_TYPE_MAP`。
- 樣板：`personnel_form.html`／`personnel_detail.html` 拿掉
  `show_cooperation_type` 判斷、選單值從 `c.code` 改成 `c.id`；
  `applicants_list.html` 的合作方式欄位改成依「目前選的廠商」動態組出
  選項（伺服器端初始渲染用 `cooperation_types_by_vendor.get(a.vendor,
  [])`；JS 端新增 `COOPERATION_TYPES_BY_VENDOR` 對照表跟
  `rebuildCoopOptions()` 函式，改廠商時即時重建合作方式選單，不用等
  「一鍵全部更新」整頁重新整理）；試駕欄位要不要顯示的判斷簡化成只看
  合作方式的值，不再另外檢查廠商是不是蝦皮系列；新增
  `cooperation_types.html`（管理頁，新增/編輯都用勾選框決定適用哪些
  廠商，樣式比照 `equipment_items.html`）。

### 既有資料怎麼辦：一次性遷移腳本

既有人員/應徵者的 `cooperation_type` 欄位存的是舊代碼
（`"two_wheel_contract"`／`"two_wheel_employed"`／
`"three_wheel_employed"`），改版後如果 Firestore 裡完全沒有合作方式
資料，人員詳細頁的合作方式選單會顯示「尚未決定」被選中（不是資料
不見了，只是查不到對應名稱可以顯示成已選取狀態；DOC_TYPES 的保險
規則、應徵名單試駕判斷因為是直接比對字串值，不受影響會繼續正常
運作）。新增 `scripts/seed_cooperation_types.py`，把原本 3 個合作方式
**用跟舊代碼完全相同的文件 ID**、`vendors: ["shopee",
"shopee_speed_warehouse"]` 建進 Firestore，既有資料完全不用搬移，
建立完成後選單就能立刻正確顯示已選取的名稱。這支腳本可以放心重複
執行——已經存在的合作方式（例如主管已經手動改過名稱或適用廠商）會
直接跳過，不會覆蓋。

### 測試

`tests/test_delivery_cooperation_types.py`（新檔案）：
`list/get/create/update/set_active/has_history/delete_cooperation_type()`
的各種情境（含 `vendors` 陣列的 `array_contains` 查詢、多廠商共用）、
合作方式管理路由（限管理員，含勾選框廠商過濾未知代碼的防呆）。
`tests/test_seed_cooperation_types.py`（新檔案）：遷移腳本的規劃邏輯。
`tests/test_delivery_applicants.py`：`applicant_needs_test_drive()`
不再檢查廠商群組的新行為、`applicants_list()` 傳給樣板的
`cooperation_types_by_vendor` 內容。
`tests/test_delivery_personnel_equipment_debt.py`：
`personnel_detail()` 相關測試補上 `list_cooperation_types` 的 mock。
`tests/test_delivery_doc_types.py`：更新一處過時的註解（合作方式現在
是動態清單，不是寫死的 `COOPERATION_TYPE_VENDORS`）。全部測試
（`python3 -m unittest discover -s tests -p "test_*.py"`）1493 個
全數通過。

### 使用者需要知道的事：部署後要手動跑一次遷移腳本，之後才能開始用「騎手身份」功能

**這次有一個手動步驟，一定要做，不然人員詳細頁的合作方式選單會顯示
「尚未決定」（既有資料沒有不見，只是暫時查不到名稱）**：合併部署
完成後，請用有 GCP 憑證的環境（例如 Google Cloud Shell）依序執行：

```bash
git pull
python -m scripts.seed_cooperation_types
```

腳本會先列出即將新增的 3 個合作方式（二輪承攬、二輪雇傭、三輪雇傭，
適用廠商：蝦皮、蝦皮三輪速配倉），輸入 `yes` 才會真的寫入。跑完之後，
人員詳細頁的合作方式選單就會恢復正常顯示已選取的名稱。

**這次多了一個新頁面「合作方式管理」**（管理員登入後，在「應徵名單」
或任何廠商的人員清單頁首都會看到按鈕），可以自行新增合作方式、勾選
適用哪些廠商、停用/刪除。**如果 UD、UC、順豐等其他廠商也想比照蝦皮
用合作方式管理保險文件規則**，目前系統只有「二輪承攬/二輪雇傭/三輪
雇傭」這三個固定 ID 的保險判斷邏輯是內建的（因為這幾個 ID 直接綁在
`config.py` 的 DOC_TYPES 保險規則裡）；如果要幫其他廠商新增全新的
合作方式選項、且需要搭配對應的保險文件規則，還是要跟我說一聲，讓我
在 `config.py` 加上對應的邏輯——**單純新增合作方式選項本身**（不涉及
保險規則）可以直接在管理頁面上自己操作，不用等我。

「騎手身份」欄位（車輛管理清單頁顯示/篩選）還沒開始做，這次先把
底層的合作方式欄位做成動態、且對所有廠商開放，下一步才會實作這個
欄位本身。

## 配送部系統：車輛管理清單頁新增「騎手身份」欄位跟篩選（2026-09-18）

上一節把合作方式做成動態清單、開放全部廠商之後，這次接著實作使用者
原本要的功能：車輛管理清單頁的手機號碼旁邊顯示這台車目前使用人的
合作方式（畫面上稱「騎手身份」），並且能篩選。

### 反查邏輯：車輛主檔沒有存人員 ID，只能靠姓名比對

車輛主檔的 `current_holder`（目前使用人）是自由輸入的文字欄位，從一
開始就沒有連到人員資料的 `personnel_id`（LINE 群組回報/網頁補登都只是
輸入姓名），所以沒辦法直接查出這個人的合作方式，只能反查對應的人員
資料。使用者原本問「如果有同名同姓的資料，能有什麼方式增加手機號碼的
比對」，討論後採用兩段式比對：

1. 車輛主檔如果有填 `current_holder_phone`（回報領車時填的電話），優先
   用「姓名+電話」查在職人員（`find_active_personnel_by_name_and_phone`，
   原本是批次匯入去重複用的既有函式），比對到的人員是唯一的，不會有
   同名同姓混淆的問題。
2. 車輛主檔完全沒填電話時，才退而用「姓名+廠商」查在職人員
   （`find_personnel_by_name_vendor`，原本是假別額度計算反查人員用的
   既有函式）——這個比對方式如果剛好同廠商有同名同姓的人員，可能會
   抓到錯的人，這是自由輸入文字欄位先天的限制。

**特別注意：如果車輛主檔有填電話、但比對不到人員，不會再退而用姓名+
廠商比對**——寫測試時（`test_phone_match_miss_does_not_fall_back_to_
vendor_match`）才抓到自己一開始寫錯的邏輯（誤把「只要沒比對到人」都
當成要退而比對廠商，沒分清楚「完全沒填電話」跟「填了電話但沒比對
到」是两回事），已經修正。

- `delivery/repository.py`：新增 `resolve_vehicle_rider_cooperation_type
  (vehicle)`，套用上面的兩段式比對邏輯，找不到人員、或人員沒設定合作
  方式都回傳 `None`（畫面上顯示成沒有騎手身份資料，不是查詢錯誤）。
- `delivery/routes/vehicle_routes.py`：`vehicle_list()` 新增
  `cooperation_type` 篩選參數；套用完既有的廠商/狀態/車號/輪別/服務
  區域篩選後，逐台車呼叫 `resolve_vehicle_rider_cooperation_type()`
  反查騎手身份、附加到每台車的資料上，再套用騎手身份篩選（篩選是靠
  反查出來的結果比對，不是車輛主檔本身存的欄位，所以要先反查完才能
  篩）；篩選選項清單用 `repository.list_cooperation_types()`（全部
  廠商合起來的清單，跟其他篩選下拉選單的做法一致）。
- `delivery/templates/vehicle_list.html`：手機號碼欄位旁邊多顯示一個
  騎手身份的徽章（有反查到才顯示，沒有就跟原本一樣只顯示電話）；篩選
  列新增「全部騎手身份」下拉選單。

### 效能上的取捨

車輛清單頁面現在每一列都會多做 1～2 次 Firestore 查詢（姓名+電話或
姓名+廠商各一次，比對到人員後還要再查一次合作方式文件），車輛數量
多的話會比原本慢一些。目前公司車隊規模不大，這個做法跟系統裡其他
「逐筆反查」的既有寫法（例如人員清單逐筆算缺件狀況）是同一種取捨，
先求正確、之後如果車輛數量成長很多、頁面明顯變慢，再考慮優化查詢
方式（例如一次把全部在職人員撈出來在記憶體裡比對，取代逐筆查
Firestore）。

### 測試

`tests/test_delivery_vehicle.py`：新增
`ResolveVehicleRiderCooperationTypeTests`，涵蓋沒有目前使用人、有填
電話優先用電話比對、沒填電話才退而用廠商比對、**填了電話但比對不到
人員時不會退而用廠商比對**、比對不到人員、比對到的人員沒設定合作
方式等情境。`tests/test_delivery_vehicle_routes.py`：新增
`VehicleListRiderCooperationTypeTests`，涵蓋反查結果會附加到每台車
資料上、騎手身份篩選只留下比對相符的車輛、沒有反查到人員的車輛在
篩選時會被排除、沒有套用篩選時保留全部車輛（含反查不到的）。全部
測試（`python3 -m unittest discover -s tests -p "test_*.py"`）1503 個
全數通過。

### 使用者需要知道的事

**不需要任何手動部署步驟**，合併後就直接生效。車輛管理清單頁的手機
號碼欄位，如果系統能反查到這台車目前使用人的合作方式，就會多顯示
一個小標籤；上面篩選列也多了「全部騎手身份」下拉選單可以篩選。如果
某台車的騎手身份沒有顯示出來，通常代表：
1. 這台車的目前使用人在人員資料裡找不到對應（姓名打錯字、或這個人
   根本還沒建立正式人員資料），或
2. 有找到對應的人員，但這個人的合作方式欄位還沒填（去人員詳細頁補上
   即可）。

不是系統故障，是反查邏輯本來就依賴姓名（可能還有電話）能不能對得上
人員資料。

## 全公司公告功能：從配送部主頁搬到 /portal 入口頁（2026-09-18）

先前一版把公告功能做在配送部系統主頁（Firestore collection
`delivery_announcements`，`delivery/repository.py` 的「公告管理」那節、
`delivery/routes/home_routes.py`／`delivery/templates/home.html`／
`announcements.html`），部署上線後使用者才澄清：**公告要放在全公司登入
後都會先看到的 `/portal` 入口頁，不是配送部專屬**——馬上把配送部那一版
整個拿掉（db.py／repository.py／home_routes.py／home.html 都revert回
原狀，`delivery/templates/announcements.html`／
`tests/test_delivery_announcements.py` 直接刪除），改在平台層重做一次。
**下面才是目前實際存在的設計**，配送部主頁沒有公告功能。

### 確認的規格

- 顯示在 `/portal`（所有系統的共用入口頁），不是某個部門子系統。
- 可以同時有多則，一週後自動下架（可調整天數）。
- 系統任何新功能/更新，都比照公司公告發佈——**只有一個例外：「少凱
  業務開發專區」（salesdev 模組）的任何事項/功能異動不列入公告**，那是
  少凱個人的業務開發專區，跟其他人無關，不用全公司廣播。這條是**內容
  原則，不是程式邏輯**，寫在 `platform_announcements.py` 開頭的說明裡，
  提醒我（或之後接手的人）發公告前要記得排除。
- 公告顯示**不分權限**：任何登入的帳號在 `/portal` 都看到同一份公告
  清單，不像卡片本身要依「這個帳號開了哪些模組」篩選——跟配送部那版
  「限管理員」的權限概念不一樣，這版公告是公司層級訊息，登入即可見。
- 發佈/管理公告限**全平台管理員**（`platform_accounts.
  require_platform_admin`，目前就是老闆本人那組帳號）——`/portal` 沒有
  「部門管理員」的概念，公告內容是全公司層級的事，交給老闆帳號管理。

### 程式碼異動

- `platform_db.py`：新增 `ANNOUNCEMENTS_COLLECTION = "platform_announcements"`
  跟 `announcements_ref()`，比照 `users_ref()`／`companies_ref()`／
  `vendors_ref()`／`departments_ref()` 放在這裡（不屬於任何單一部門
  模組的跨系統共用資料）。
- 新檔案 `platform_announcements.py`（跟 `platform_accounts.py`／
  `platform_departments.py` 同一層級）：`ANNOUNCEMENT_DEFAULT_DAYS = 7`、
  `list_active_announcements()`（`/portal` 用，只回傳啟用中且未過期、
  新到舊排序）、`list_announcements()`（管理頁用，回傳全部並標記
  `expired`）、`get_announcement()`、`create_announcement(title, content,
  created_by, days=7)`、`set_announcement_active()`、
  `delete_announcement()`——邏輯跟配送部那一版幾乎一樣，只是資料源換成
  平台層的 `announcements_ref()`。
- `portal_routes.py`：`portal_home()` 多傳 `announcements`
  （`list_active_announcements()` 查來的清單，附加 `created_at_display`
  顯示用日期）；新增公告管理路由 `GET /announcements`（限全平台管理員）、
  `POST /announcements/new`、`POST /announcements/{id}/active`、
  `POST /announcements/{id}/delete`，都掛在根 app（沒有 `/delivery`
  之類的前綴，跟 `/accounts`／`/departments`／`/companies`／`/vendors`
  同一層級）。
- 樣板：`templates/portal_home.html`（根目錄，不是 `delivery/templates/`
  底下那份）標題上方新增公告卡片區塊；`templates/base.html`（根目錄）
  導覽列 `user.is_platform_admin` 那塊多一個「公告管理」連結；新增
  `templates/announcements.html`（根目錄）管理頁面，樣式/欄位跟配送部
  那版幾乎一樣（新增表單：標題、說明、幾天後自動下架；清單顯示發布/
  下架日期、狀態徽章「顯示中／已過期／已下架」、提前下架/恢復顯示/
  刪除）。**`delivery/static/style.css` 的 `.announcement-list`／
  `.announcement-card` 樣式沿用不動**——根目錄的樣板本來就是直接引用
  `/delivery/static/style.css` 這份共用 CSS（見 `templates/base.html`），
  不用另外複製一份樣式。

### 測試

新增 `tests/test_platform_announcements.py`：CRUD 函式的各種情境（排除
已過期、排除已手動下架、新到舊排序、舊資料沒有 active 欄位時預設當作
啟用中、含過期的並標記 `expired`、預設 7 天後過期、自訂天數等）。
`tests/test_portal.py` 新增 `AnnouncementRoutingSmokeTests`（未登入時
導向 `/portal`）、`PortalHomeAnnouncementTests`（`portal_home()` 傳給
樣板的公告清單含顯示用日期）、`AnnouncementAdminRoutesTests`（新增/
切換啟用/刪除路由呼叫 `platform_announcements` 的各種情境，含標題空白
不建立、天數 0 或負數退回預設 7 天的防呆）。刪除
`tests/test_delivery_announcements.py`（配送部那版已經整個撤掉）。全部
測試（`python3 -m unittest discover -s tests -p "test_*.py"`）1522 個
全數通過。

### 使用者需要知道的事

**不需要任何手動部署步驟**，合併後就直接生效。用你（老闆）的帳號登入
`/portal`，導覽列會看到「公告管理」連結，點進去可以新增公告——標題、
說明、幾天後自動下架（預設 7 天，可以改）。公告會馬上出現在 `/portal`
入口頁最上方，**所有登入的帳號都看得到同一份**，不分部門/權限；過期
後系統自動不再顯示，不用你手動處理，也可以隨時「提前下架」。

之後配送部（或其他部門）系統有新功能上線，我會同步在這裡發公告——
**唯一的例外是「少凱業務開發專區」**，那邊的異動不會公告，因為那是
少凱個人的業務開發專區，跟其他同仁無關。

## /portal 卡片新增「使用說明」按鈕，各模組自己的操作說明網頁（2026-09-18）

使用者原本收到一份配送部系統使用說明 PPT，後來要求做成系統裡的網頁，
且要跟著每次功能異動同步更新——討論後確認設計：`/portal` 每張模組卡片
上加一個「使用說明」小按鈕，點進去是**該模組自己的**說明頁（不是全部
模組共用一頁），且沿用該模組原本的權限判斷，沒有這個模組權限的同仁
連卡片都看不到，自然也看不到按鈕、也進不去說明頁本身。**PPT 版本之後
不再維護**，改以這個網頁版為準。

### 設計

- **內容是我自己維護的靜態頁面，不是 Firestore 動態資料**——跟公告不同，
  這裡不需要非技術同仁自己編輯內容，維護責任本來就在我身上：之後配送
  部系統每上線一個新功能，我會同步更新這個頁面，跟維護 HANDOFF.md 是
  同一個習慣，不需要另外做管理介面。
- **權限**：每個模組的說明頁掛在該模組自己的子系統底下、走該模組自己
  既有的 `login_required`（例如配送部就是 `delivery/auth.py` 的
  `login_required`），這組判斷跟 `/portal` 卡片顯不顯示是同一組——不會
  有「按鈕沒有但網址還是看得到內容」的落差，不用另外寫一套權限邏輯。
- **`portal_routes.py` 的 `_MODULE_CARD_INFO`** 每個模組多一個
  `help_href` 欄位：有值就是這個模組已經寫好說明頁，卡片上會多顯示
  「使用說明」按鈕；沒有值（`""`）的模組（目前除了配送部，其他都還
  沒寫）卡片上就不會顯示這個按鈕，之後每寫好一個模組的說明頁，補上
  對應的 URL 即可。
- `templates/portal_home.html` 的模組卡片**改版面結構**：原本整張卡片
  是一個 `<a>`（點卡片任何地方都能進系統），改成外層 `<div>`、裡面用
  兩個獨立按鈕（「前往」進系統／「使用說明」進說明頁，後者只在有
  `help_href` 時才顯示）——因為 HTML 不能巢狀 `<a>` 包 `<a>`，這是唯一
  乾淨的做法。`/me`、「職缺維護系統」那兩張卡沒有說明頁需求，維持原本
  整張卡片可點擊的樣式不變。

### 程式碼異動

- `portal_routes.py`：`_MODULE_CARD_INFO["delivery"]` 新增
  `"help_href": "/delivery/help"`；`portal_home()` 組 `cards` 時多帶
  `help_href`（沒設定的模組給空字串）。
- `delivery/routes/home_routes.py`：新增 `GET /delivery/help`，跟 `home()`
  一樣用 `login_required`，渲染新的 `help.html`。
- `delivery/templates/help.html`（新檔案）：配送部系統完整使用說明，
  依模組分段（系統總覽／登入與權限／人員管理／合作方式管理／應徵
  名單／補款假別登記／車輛管理／意外事件回報／裝備借還管理／查詢
  匯入），頁首有錨點導覽列可以快速跳到對應段落。內容涵蓋目前所有
  已上線功能，包含這次改版才新增的合作方式管理、車輛服務區域管理、
  騎手身份欄位、退保連動提醒等。
- `delivery/static/style.css`：新增 `.help-nav`／`.help-section` 樣式
  （每段一張卡片式區塊，配合錨點導覽）。

### 測試

`tests/test_delivery_home_routes.py`（新檔案）：`help_page()` 正確渲染
`help.html` 並傳入使用者資料。`tests/test_delivery_routes.py` 新增
`/delivery/help` 未登入時導去登入頁的煙霧測試。`tests/test_portal.py`
新增 `PortalHomeHelpLinkTests`：配送部卡片帶 `help_href`、還沒寫說明頁
的模組（例如管理部）`help_href` 是空字串。全部測試（`python3 -m
unittest discover -s tests -p "test_*.py"`）1526 個全數通過。

### 使用者需要知道的事

**不需要任何手動部署步驟**，合併後就直接生效。登入 `/portal` 後，
「新北所(配送組)系統」卡片上會多一個「使用說明」按鈕，點進去就是完整
的操作說明。**其他模組（管理部、人資…）目前還沒有說明頁**，卡片上
不會顯示這個按鈕——之後如果要幫其他模組也做一份，跟我說一聲即可，
做法是同一套。PPT 版本之後不會再更新，有需要的話請改看這個網頁版。

## 其餘 8 個模組的使用說明頁全部補齊（2026-09-18）

使用者要求把 /portal 剩下沒有使用說明的卡片全部補齊，之後每次更新功能
也要同步更新說明——延續上一節的設計（每個模組自己的說明頁、沿用該模組
自己的權限判斷）。為了避免憑空編內容，先分別對 8 個模組（management、
hr、salesdev、job_listings、project_contracts、chicken_points、
dispatch_contracts、client_contracts）各派一個 subagent 讀過對應的
routes/templates/repository/service 檔案，確認實際行為後才動筆寫說明，
避免說明頁寫出跟系統實際行為對不起來的內容。

### 各模組說明頁掛的位置

- `management`（子系統，掛 /management）：`management/routes/home_routes.py`
  新增 `GET /management/help`，跟 `home()` 一樣用 `login_required`，樣板
  `management/templates/help.html`。7 個段落：系統總覽、公告事項、會議
  記錄、規章/SOP 文件庫、業績報表庫、客戶拜訪紀錄、員工名冊/組織圖、
  資產/設備管理。
- `hr`（子系統，掛 /hr）：`hr/routes/home_routes.py` 新增 `GET /hr/help`，
  同樣用 `login_required`，樣板 `hr/templates/help.html`。5 個段落：
  系統總覽、意外通報、員工體檢報告、員工關懷彙整、公司證照彙整、教育
  訓練彙整。
- 其餘 6 個模組都是直接掛在根 app（沒有獨立子系統/登入頁），各自在自己
  的 route 檔案裡新增 `GET /{路徑}/help`，沿用各自檔案裡原本就有的
  `_require_access` 依賴（跟該模組本來就有的其他路由同一套權限判斷），
  樣板放在根目錄 `templates/`：
  - `salesdev_routes.py` → `GET /salesdev/help` → `templates/salesdev_help.html`
  - `job_listing_routes.py` → `GET /job-listings/help` → `templates/job_listing_help.html`
  - `project_contract_routes.py` → `GET /project-contracts/help` → `templates/project_contract_help.html`
  - `chicken_points_routes.py` → `GET /chicken-points/help` → `templates/chicken_points_help.html`
  - `dispatch_contract_routes.py` → `GET /dispatch-contracts/help` → `templates/dispatch_contract_help.html`
  - `client_contract_routes.py` → `GET /client-contracts/help` → `templates/client_contract_help.html`

`portal_routes.py` 的 `_MODULE_CARD_INFO` 每個模組都補上對應的
`help_href`，現在全部 9 個模組（含配送部）的卡片都會顯示「使用說明」
按鈕。

### 內容重點（供之後維護參考，不是完整內容，完整內容看各說明頁本身）

- **management**：除了「資產狀態更新」，內容一律不能編輯，只能刪除
  重新建立；客戶拜訪紀錄的可見範圍是「自己的紀錄」（老闆例外看得到
  全部），跟公告/會議記錄「全部門共享」邏輯不同；門號資產有自動繳費
  提醒（每週一推播 LINE）。
- **hr**：意外通報沒有網頁新增表單，完全靠 LINE 群組訊息觸發建檔；
  公司證照到期前會自動推播提醒；其餘 3 項（體檢報告、關懷彙整、教育
  訓練）都是純手動登記，沒有自動化。
- **salesdev**：唯讀彙整一份 Google 試算表，只有「查看資料」跟「勾選
  待反查」兩個功能，沒有主管/專員的角色差異。
- **job_listings**：分「新增全新職缺」／「維護既有職缺」兩種模式；
  送出後走主管 LINE 核准，核准後自動同步職缺資料庫、官網、招募機器人；
  故意不提舊版 Netlify 職缺系統，避免使用者搞混登入方式。
- **project_contracts**：沒有審核流程，送出即完成；可以從「合約產生器」
  帶入已存在的合約資料省去重複輸入；沒有查詢/編輯自己送出紀錄的功能。
- **chicken_points**：這是第一個「專員/主管」角色真的影響功能的模組——
  一般同仁只看得到自己的申請，會計（主管角色）才看得到全部並能刪除；
  只需要本人簽名，沒有審核關卡。
- **dispatch_contracts**：可見範圍收斂成「自己送出的／自己主管的部屬
  送出的／全平台管理員」，服務部門主管額外開放下載/預覽（但不能刪除）；
  班別薪資表格可勾選要用哪些欄位。
- **client_contracts**：5 種合約版本會動態顯示/隱藏對應欄位；甲方公司
  資料可以自動查政府登記資料庫帶入；「複製」功能方便續簽下一年度合約
  （日期不會自動加一年，需要自己改）；可另外上傳廠商指定格式的合約
  檔案，跟系統產生的標準版並存。

### 「每次更新功能同步更新說明」的落實方式

跟配送部那份說明頁一樣，這是**我的工作流程**，不是額外的程式功能——
之後不管哪個模組上線新功能或調整既有行為，我會同步更新對應那份說明頁
的內容，寫程式碼改動的同一次 PR 就會一併改說明頁，不會事後補。

### 測試

`tests/test_management_routes.py`／`tests/test_hr_routes.py` 各新增一個
`/help` 未登入導向登入頁的煙霧測試；`tests/test_salesdev_routes.py`／
`tests/test_job_listing_routes.py`／`tests/test_project_contract_routes.py`
／`tests/test_chicken_points_routes.py`／`tests/test_dispatch_contract_routes.py`
／`tests/test_client_contract_routes.py` 也各自新增一個 `/help` 導向
`/login?next=/...` 的煙霧測試，驗證跟該模組其他路由共用同一個
`_require_access`。`tests/test_portal.py` 的 `PortalHomeHelpLinkTests`
擴大成驗證全部 9 個模組卡片都帶正確的 `help_href`。全部測試（`python3
-m unittest discover -s tests -p "test_*.py"`）1533 個全數通過。

### 使用者需要知道的事

**不需要任何手動部署步驟**，合併後就直接生效。登入 `/portal` 後，
9 張模組卡片（配送部、管理部、人資、少凱業務開發、職缺維護、專案合約
維護、小雞點數自費申請、派遣契約產生器、合約產生器）都會看到「使用
說明」按鈕，點進去就是各自完整的操作說明。之後這些系統如果有功能調整，
我會同步更新對應的說明頁，不用你特別提醒。

## 配送部系統：全部頁面加寬、車輛詳細頁補上騎手身份、登記領還車自動帶入電話（2026-09-19）

使用者一次提出三項調整，討論後確認範圍並實作：

1. **配送系統所有頁面顯示加寬**：`delivery/templates/base.html` 其實
   從一開始就有預留 `{% block container_class %}` 機制（`.container`
   預設最大寬度 1080px，`.container-wide` 是 1440px），但除了「應徵
   名單」那頁，其餘 29 個頁面都沒有套用，等於這個機制形同虛設。這次
   把剩下 29 個頁面模板都補上 `{% block container_class %}container-
   wide{% endblock %}`，純 CSS 層級調整，不動任何資料邏輯。連登入頁
   也一起加了（`.login-box` 本身有自己的 `max-width: 380px` 置中設定，
   外層容器加寬對登入頁沒有視覺影響，純粹求一致）。
2. **車輛管理詳細頁補上「騎手身份」**：2026-09-18 已經在車輛管理
   **清單頁**做了「反查目前使用人的合作方式」功能（見
   `repository.resolve_vehicle_rider_cooperation_type()`），但只有
   清單頁有，詳細頁沒有。這次在 `vehicle_routes.vehicle_detail()`
   套用同一個既有函式，`vehicle_detail.html` 最上面那排資訊多顯示
   一個「騎手身份」欄位，沒反查到就顯示「-」，跟清單頁的規則完全一致
   （不重新設計）。
3. **登記領/還車表單自動帶入電話**：之前姓名、電話是兩個各自獨立的
   輸入框，同仁打完姓名還要自己再手動查、再打一次電話。新增一支
   AJAX 端點 `GET /delivery/vehicles/personnel-lookup?vendor=...&
   name=...`，做法比照合約產生器甲方查詢
   （`client_contract_routes.client_contract_company_lookup`）：沒
   登入一律回傳查無資料；有登入就用「姓名+廠商」查在職人員
   （`repository.find_personnel_by_name_vendor()`，跟騎手身份反查
   共用同一個既有函式），查得到就回傳電話跟合作方式名稱，查不到就
   回傳 `found: false`。車輛詳細頁「手動補登事件」表單、歷史紀錄
   「編輯」表單這兩個地方，姓名欄位打完離開（blur）或廠商欄位改變
   時會自動觸發查詢，查到就把電話欄位帶入（原本填的值會被蓋掉）並在
   旁�/下方顯示提示文字（含合作方式），查不到則顯示「系統查無此人員
   資料，請自行手動填寫電話」，不會擋住送出——這跟使用者的需求
   「除非系統沒有該人員的資訊」完全對應。

**特別注意路由順序的坑**：`vehicle_personnel_lookup` 這支新端點的
路徑是 `/vehicles/personnel-lookup`，FastAPI 路由是照宣告順序比對，
如果宣告在 `/vehicles/{vehicle_no}` 這個萬用路徑「之後」，
"personnel-lookup" 會被當成車號吃掉，這支端點永遠不會被呼叫到——寫
的時候一開始真的犯了這個錯，後來搬到 `/vehicles/{vehicle_no}` 之前
才修正，之後如果要在車輛模組加新的固定路徑端點，記得放在
`/vehicles/{vehicle_no}` 之前宣告。

（本次沒有處理：車輛管理頁面新增「合作方式」篩選——這個 2026-09-18
就已經做好了，畫面上叫「騎手身份」，只是使用者原本沒注意到，確認後
不用重做。）

### 測試

`tests/test_delivery_vehicle_routes.py` 新增
`VehicleDetailRiderCooperationTypeTests`（詳細頁正確帶入反查結果、
沒反查到時是 `None`）、`VehiclePersonnelLookupTests`（查得到回傳電話
跟合作方式名稱、查得到但沒設定合作方式時名稱是空字串、查不到回傳
`found: false`、沒填廠商或姓名不查詢直接回傳、沒登入不查詢直接回傳）。
全部測試（`python3 -m unittest discover -s tests -p "test_*.py"`）
1540 個全數通過。

### 使用者需要知道的事

**不需要任何手動部署步驟**，合併後就直接生效。之後配送系統各個頁面
會比之前寬一些；車輛詳細頁最上面那排資訊多了「騎手身份」；車輛詳細
頁「手動補登事件」跟歷史紀錄「編輯」這兩個表單，打完姓名（或改廠商）
離開欄位時，如果系統找得到對應的在職人員資料，電話會自動幫你填好，
仍然可以手動改；找不到的話會提示你自己手動填，不會卡住無法送出。

## 合約產生器：新增選填的「附件二：轉正費用」（2026-09-19）

使用者提供一張截圖，要求合約產生器（`/client-contracts`）除了代招版本
以外，其餘版本要多一個選填的附件二——轉正費用計算表（任職未滿門檻
月數內每人收固定金額、超過門檻不收費），沒勾選就跟原本一樣完全沒有
這一段。討論過程確認：「任職月數門檻」「金額」這兩個數字每次都要
同仁自己填、不是寫死的固定值，而且月數門檻要同時套進「任職＿個月內」
跟「任職＿個月以上」兩列（維持邏輯一致），其餘文字全部固定不變。

### 做法

- `CONTRACT_VERSIONS` 每個版本新增 `supports_exhibit_two` 旗標：時薪
  一口價／實支實付／傳統一口價這三個共用主文的家族是 True，白領代招／
  台籍代招是 False。
- 只在這三個版本各自的 master template docx 最後（附件一之後、
  `sectPr` 之前）加上 docxtpl 的條件式區塊
  `{% if include_exhibit_two %}` ... `{% endif %}`，包住「附件二：」
  「轉正費用計算如下：」兩段固定文字跟一個 3 欄 3 列的表格（表頭＋
  「轉正費」合併儲存格橫跨兩個資料列）。代招版本的 master template
  完全沒有加這個區塊——不是加了但關閉，是根本沒有，所以就算表單被
  竄改送出 `include_exhibit_two=1`，套版也不會出錯、也不會憑空冒出
  附件二內容（`RenderWhiteCollarReferralContractDocxTests.test_
  include_exhibit_two_flag_is_harmless_for_referral_version` 驗證
  這件事）。
- `render_contract_docx()`／`save_submission()` 都新增
  `include_exhibit_two`／`exhibit_two_months`／`exhibit_two_amount`
  三個參數（預設 False／空字串），`save_submission()` 存檔時如果
  `include_exhibit_two` 是 False，月數／金額一律存空字串，不會殘留
  沒勾選時欄位裡打過的舊值。
- `client_contract_routes.py`：只有 `supports_exhibit_two` 為 True 的
  版本才會實際收 `include_exhibit_two` 勾選狀態，其餘版本一律當作
  False（即使表單被竄改送出勾選也一樣，等於在後端也擋一次，不是只靠
  前端不顯示這個勾選項）；勾選了才會擋「月數或金額沒填」，沒勾選就
  不檢查這兩個欄位。
- `templates/client_contract_form.html`：新增第六段「附件二（選填）：
  轉正費用」，勾選項＋兩個文字輸入框（不勾就隱藏，套用既有的
  `data-requires` 版本切換機制，跟簽約日期／撤換條款用同一套 JS）。

### 測試

`tests/test_client_contract_service.py` 新增附件二有勾/沒勾兩種情況的
套版結果驗證、月數門檻同時套進兩列的驗證、代招版本收到這個旗標也
不會冒出附件二內容的驗證，以及 `supports_exhibit_two` 旗標本身的
設定驗證。`tests/test_client_contract_routes.py` 新增勾選但沒填會
擋下送出、有勾選時欄位正確傳給套版/存檔、沒勾選時欄位一律清空、
代招版本即使表單被竄改送出勾選也會被忽略、複製功能會正確帶入既有
紀錄的附件二欄位這幾個測試。全部測試（`python3 -m unittest discover
-s tests -p "test_*.py"`）1557 個全數通過。

### 使用者需要知道的事

**不需要任何手動部署步驟**，合併後就直接生效。操作上：產生時薪一口價
／實支實付／傳統一口價這三種版本的合約時，表單最下面會多一個「附件二
（選填）：轉正費用」的勾選項，勾選後才會出現「任職月數門檻」「轉正費
金額」這兩個必填欄位，每次都要自己填；不勾就跟以前一樣，Word 檔不會
有附件二。白領代招／台籍代招這兩個版本沒有這個選項（畫面上完全看
不到），跟你原本的需求一致。

## 配送部系統：車輛管理清單頁手機號碼備援顯示 + 騎手身份補上欄位名稱（2026-09-19）

使用者截圖回報車輛管理清單頁兩個問題：1) 很多列的「手機號碼」欄位是
空的（`-`），2) 騎手身份的徽章跟手機號碼擠在同一欄，上面沒有獨立的
欄位名稱。

### 根本原因

1. **手機號碼空白**：這些車輛的「目前使用人」姓名是有填的，但車輛
   主檔自己的 `current_holder_phone` 是空的——多半是透過 LINE 群組
   回報時同仁沒有附電話號碼。系統其實已經有「反查目前使用人的人員
   資料」這個機制（2026-09-18 新增的騎手身份功能），但過去只拿反查
   結果的合作方式，沒有順便拿電話當備援顯示值，所以就算反查得到
   （合作方式徽章有顯示出來），電話欄位還是顯示 `-`。
2. **騎手身份沒有欄位名稱**：清單頁的表格只有一個「手機號碼」欄位，
   騎手身份的徽章是直接塞進同一個 `<td>`，沒有自己的 `<th>`。

### 修正方式

- `delivery/repository.py`：把原本各自反查一次的做法，改成新函式
  `resolve_vehicle_rider_info(vehicle)`，一次反查同時回傳
  `{"cooperation_type": ..., "phone": ...}`——同一台車只查一次
  Firestore，不會為了拿電話又為了拿合作方式各查一次（`resolve_
  vehicle_rider_cooperation_type()` 保留下來當一個只需要合作方式的
  精簡版本，內部改呼叫 `resolve_vehicle_rider_info()`，不重複寫比對
  邏輯）。
- `vehicle_routes.py` 的 `vehicle_list()`／`vehicle_detail()`：呼叫
  一次 `resolve_vehicle_rider_info()`，電話欄位改成
  `vehicle.current_holder_phone or 反查到的電話`——**只影響畫面顯示，
  不會回寫覆蓋車輛主檔本身的欄位**，車輛主檔自己有填電話的話還是
  優先顯示那個號碼，不會被反查結果蓋掉。
- 模板：反查到的電話（不是車輛主檔自己存的）用 `title` 提示文字
  標註「車輛主檔沒有填電話，這是依人員資料反查到的號碼」，避免同仁
  誤以為這是車輛主檔本來就記錄的電話；清單頁的表格新增獨立的
  「騎手身份」欄位跟表頭，不再跟手機號碼擠在同一格。

### 測試

`tests/test_delivery_vehicle.py` 新增 `ResolveVehicleRiderInfoTests`
（沒有目前使用人時兩個欄位都是空、正確回傳合作方式跟電話、同一台車
只查一次 Firestore、人員沒有電話時回傳空字串、查無人員時兩個欄位都
是空）。`tests/test_delivery_vehicle_routes.py` 的 `VehicleList
RiderCooperationTypeTests`／`VehicleDetailRiderCooperationTypeTests`
改成 mock 新的 `resolve_vehicle_rider_info()`，新增車輛主檔沒有電話
時採用反查結果、車輛主檔已經有電話時不會被反查結果覆蓋這兩組測試。
全部測試（`python3 -m unittest discover -s tests -p "test_*.py"`）
1566 個全數通過。

### 使用者需要知道的事

**不需要任何手動部署步驟**，合併後就直接生效。之後車輛管理清單頁跟
詳細頁，如果車輛主檔自己沒填電話、但系統反查得到目前使用人的人員
資料，電話欄位就會顯示反查到的號碼（滑鼠移上去會提示這是反查來
的，不是車輛主檔本來記錄的）；清單頁「騎手身份」也有自己獨立的
欄位標題了，不會再跟手機號碼擠在一起看不出來是什麼欄位。

## 系統更新自動加入公告（2026-09-19）

使用者要求：系統只要有功能更新就自動列入 `/portal` 公告，不用每次都
手動打字發佈；「少凱業務開發專區」（salesdev 模組）的異動維持既有
原則（見 `platform_announcements.py` 開頭），不列入。

### 做法

- **新端點 `POST /internal/announcements/auto-publish`**
  （`portal_routes.py`）：不需要登入，用共用密鑰
  `AUTO_ANNOUNCE_SECRET`（帶在 `X-Auto-Announce-Secret` header）驗證，
  安全機制完全比照既有的 `/internal/sync-job-system-identities`（沒
  設定密鑰、密鑰不對都回傳 403，等同端點不存在）。收到 `title`／
  `content` 就呼叫 `platform_announcements.create_announcement()`
  建立一則公告，`created_by` 固定存 `"system"`，方便之後在公告管理頁
  分辨哪些是自動發的、哪些是老闆自己手動發的。
- **`.github/workflows/deploy.yml` 部署成功後多一個步驟**：
  1. 用 `git diff --name-only HEAD~1 HEAD` 比對這次 push 到 main 改了
     哪些檔案（`actions/checkout` 補上 `fetch-depth: 2`，不然預設的
     淺層 clone 沒有前一個 commit 可以比）。
  2. 如果全部都是 `salesdev` 相關檔案（含 `HANDOFF.md`，因為每次
     改動都會更新這份文件，不能因為它也改了就誤判成「不是純
     salesdev 改動」），就跳過，不發公告。
  3. 否則就用這次合併的 commit 訊息（squash merge 後就是 PR 標題＋
     內文，已經是給人看的中文說明，不用另外處理）當標題/內容，剝掉
     標題結尾的 `(#123)` PR 編號、剝掉內文尾端的 `Claude-Session:`／
     `Co-authored-by:` 這兩行 attribution，呼叫上面那支新端點。
  4. 這一步失敗（例如密鑰沒設定、網路問題）**不會讓整個部署工作流程
     顯示失敗**（`continue-on-error: true`）——部署本身在這步之前就
     已經跑完了，公告發不出去只是少一則公告，不影響系統正常運作。
  5. commit 訊息是直接放進 `env:` 再用 `$COMMIT_MSG` 引用，**不是**
     直接字串內插進 `run:` 腳本——避免惡意 commit 訊息內容被當成
     shell 指令執行（GitHub Actions 已知的 script injection 風險，
     `github.event.head_commit.message` 是不可信輸入）。

### 測試

`tests/test_portal.py` 新增 `AutoPublishAnnouncementEndpointTests`
（沒設定密鑰／密鑰錯誤都回傳 403、標題空白回傳 400、密鑰正確時正確
呼叫 `create_announcement()`）。GitHub Actions 那段 bash 邏輯（比對
改動檔案、剝離標題/內文）另外手動跑過幾組情境確認行為正確，沒有寫
自動化測試（CI YAML 裡的 shell script 不在這個 repo 的 Python 測試
框架涵蓋範圍內）。全部測試（`python3 -m unittest discover -s tests
-p "test_*.py"`）1570 個全數通過。

### 使用者需要知道的事——這次需要手動設定兩個地方

跟之前不一樣，**這次需要你手動做兩個設定，不做的話自動公告不會生效**
（但也不會影響系統其他功能，只是暫時沒有自動公告而已）：

1. **想一組密鑰**（隨便一串英數字，例如用密碼產生器產生一串 32 碼的
   亂數字串，不需要好記，只是給機器對機器驗證用）。

2. **把這組密鑰設定成 Cloud Run 的環境變數**（在 Cloud Shell 執行）：
   ```bash
   gcloud run services update recruitment-bot \
     --region asia-east1 \
     --update-env-vars AUTO_ANNOUNCE_SECRET="你剛才想的那組密鑰"
   ```
   跑完看到 `Service [recruitment-bot] revision ... has been deployed`
   就代表設定成功。

3. **把「同一組」密鑰也設定成 GitHub 這個 repo 的 Actions 密鑰**：
   - 到 `https://github.com/tsaipei-linebot/tsaipeilinebot/settings/secrets/actions`
   - 按「New repository secret」
   - Name 填 `AUTO_ANNOUNCE_SECRET`
   - Secret 填「跟步驟 2 一模一樣」的那組密鑰
   - 按「Add secret」

兩邊的密鑰**一定要完全一樣**，不然驗證會失敗（會安靜地跳過不發公告，
不會噴錯訊息卡住部署，但也不會有公告）。

設定好之後，之後只要合併一個不是純「少凱業務開發專區」的 PR、部署
成功，`/portal` 就會自動多一則公告，內容就是那次 PR 的標題跟說明，
不用再手動發布。如果之後想暫停這個自動公告功能，把 Cloud Run 上的
`AUTO_ANNOUNCE_SECRET` 環境變數刪掉（或改成跟 GitHub 那邊不一樣的
值）即可，不用改程式碼。

**2026-09-21 補充**：使用者反映這個功能上線後其實一直沒有生效——回頭
查最近幾次部署（PR #163 等）的 GitHub Actions log，「Auto-publish
announcement」那個步驟每次都印出「沒有設定 AUTO_ANNOUNCE_SECRET 這個
GitHub Actions 密鑰，跳過自動公告」，代表上面兩個手動設定步驟當初其實
沒有真的做完（或做完後其中一邊的值後來被改掉、對不起來）。這次已經
重新照上面的步驟設定好兩邊的密鑰，之後可以觀察下一次合併部署時
`/portal` 有沒有正確自動出現新公告來確認生效。

## 外送員接單媒合：即時接單／報班媒合第一階段（2026-09-19 新增）

外送部 CHANNEL1 LINE 官方帳號（`delivery-gas-project` 那個 repo 處理的、
跟這支招募機器人不同的官方帳號）新增兩個讓已登記合作騎士自助操作的
功能：**即時接單**（查詢附近門市當日還有多少宅配貨量可以承接、自行輸入
承接件數）、**報班媒合**（瀏覽並報名店家/物流商發布的人力需求時段）。
跟現有排班「發包」機制（專員主動推播、騎士被動接受）是相反方向的操作
（pull vs. push），資料表、程式邏輯完全分開，不會動到發包機制。

這是使用者（少凱）先寫好完整技術規格再交給 Claude 實作的功能，實作前
先跟使用者確認了 3 個規格書列為待確認事項的產品面決定：
1. **使用資格**：僅限已登記合作的騎士（透過 LINE「綁定+工號+姓名」私訊
   完成綁定），並且要有後台功能能讓管理員停用不合規的騎士——這是使用者
   在確認過程中額外提出、規格書原本沒有的需求。
2. **後台表單使用者**：內部配送部/管理部同仁（沿用既有 `/accounts` 帳號
   權限），不是門市/物流商自己登入填寫，所以不用另開帳號類型。
3. **每日截止自動關閉**：第一階段不做，人工手動關閉即可。

另外實作過程中發現一個能簡化架構、順便解掉規格書「replyToken 時效」
風險的地方：`delivery-gas-project` 現有的車輛回報/意外事件回報，其實是
「GAS 同步呼叫這個系統、等回應、GAS 自己用它手上的 CHANNEL1 Token 呼叫
LINE Reply API 回覆」這個模式——這支系統完全不用持有一份 CHANNEL1 的
Channel Access Token，也不用自己呼叫 LINE API。這次新功能比照同一套
機制（只是把 GAS 回覆的內容從純文字擴充成能傳 Flex 卡片），所以**沒有**
採用規格書原本設想的「新增 `CHANNEL1_CHANNEL_ACCESS_TOKEN` 環境變數」
做法——少一把要另外管理的密鑰，而且是複用已經穩定跑在正式環境的機制。

**架構**：權限沿用配送部模組本身（使用者確認新模組歸在現有「新北所
(配送組)系統」卡片底下，不需要獨立 `/portal` 卡片、不需要改
`platform_accounts.py` 的 `MODULES` 清單），所以沒有蓋成一個獨立模組
（自己的 config.py/db.py/獨立登入），而是直接併進現有 `delivery/`
package，新增這幾支檔案：

| 檔案 | 用途 |
|---|---|
| `delivery/rider_repository.py` | Firestore 資料層＋交易邏輯：騎士綁定/啟用停用、門市當日量 CRUD、承接 transaction、報班時段 CRUD、報名 transaction |
| `delivery/rider_messages.py` | 組出要回覆給騎士的 LINE 訊息 JSON（純文字／Flex Carousel），刻意不用 `linebot.models`——這些訊息最終是 GAS 轉發出去，不是這裡直接呼叫 LINE API |
| `delivery/rider_events.py` | 解析 GAS 轉發過來的騎士事件（文字／位置訊息／Postback），決定要回覆什麼，webhook 路由本身保持很薄 |
| `delivery/routes/rider_routes.py` | 後台管理頁面：門市當日量管理／報班時段管理（`login_required`）、騎士名單管理啟用/停用（`admin_required`，性質上更接近服務區域管理這種限主管操作） |
| `delivery/templates/rider_*.html` | 對應的後台管理樣板 |

`delivery/routes/webhook_routes.py` 新增兩支端點（沿用既有共用密鑰驗證
模式，跟 `/api/vehicle-report` 同一種做法）：
- `POST /delivery/api/rider-events`：接收 GAS 轉發的騎士事件，回傳
  `{"messages": [...]}` 一份 LINE 訊息物件陣列。
- `POST /delivery/api/rider-binding-sync`：GAS 那邊「綁定+工號+姓名」
  成功後同步呼叫，把 LINE UserId↔工號/姓名寫進 `delivery_rider_bindings`
  ——刻意保留既有 `status` 欄位不覆蓋，已經被停用的騎士重新綁定/改名
  不會自動解除停用。

**Firestore 資料模型**（跟規格書原本設想的「真的用 Firestore 子集合」不
同，這裡改成扁平集合＋外鍵欄位，例如 `delivery_rider_claims` 用
`store_delivery_id` 欄位指回它屬於哪一筆 `delivery_rider_store_
deliveries`——整個 repo 目前沒有任何地方用到真的子集合，保持這個唯一的
做法）：`delivery_rider_bindings`、`delivery_rider_store_deliveries`、
`delivery_rider_claims`、`delivery_rider_shift_postings`、
`delivery_rider_shift_registrations`。

**承接／報名的併發保護**：`claim_store_delivery()`／`register_shift()`
都是 `@firestore.transactional` 包起來的「讀一次目前資料→純函式
（`_evaluate_claim()`／`_evaluate_registration()`）決定接不接受→接受
才寫回去」，確保兩位騎士幾乎同時操作同一筆資料不會一起超放/超收——
跟 `services/session_service.py` 修過的並發遺失更新問題是同一種寫法。
這兩個純函式刻意不摸 Firestore，方便直接單元測試邊界情況（剛好用完／
超過／已關閉／重複報名），不需要真的連 Firestore 或搭配 emulator。

**查詢附近門市怎麼排序**：Firestore 沒有「依距離排序」的原生查詢能力，
是先撈出當日 `status==open` 的候選門市，程式碼自己用 Haversine 公式算
距離再排序（`rider_repository._haversine_km()`）——門市數量不多的話
完全沒問題，這不是漏做，是刻意的技術選擇，已經先讓使用者知道。

**騎士輸入承接件數的暫存狀態**：騎士點「承接」後還沒輸入件數，用
騎士綁定資料上的 `pending_claim` 欄位暫存「正要承接哪一筆」，下一則純
數字文字訊息就當作件數處理；暫存超過 `RIDER_PENDING_CLAIM_TTL_SECONDS`
（10 分鐘，`delivery/config.py`）就視為過期，避免騎士點了「承接」放著
不理，很久之後才傳一則不相干的數字訊息被誤當成件數輸入。

**LINE 互動觸發方式**：私訊關鍵字（「查詢附近單」「瀏覽報班」等，見
`rider_events.py` 的 `_NEARBY_ORDER_KEYWORDS`／`_SHIFT_LIST_KEYWORDS`），
不是 LINE 圖文選單（Rich Menu）——那需要另外呼叫 LINE Rich Menu API
設定，一階段用文字關鍵字就能達到一樣的效果，之後真的需要圖文選單
再另外規劃。

**分階段推出**：這次只做規格書的第一階段——門市當日量由後台人工登記
（`source` 欄位固定 `"manual"`），刻意不做「回報送達」跟逾時自動掃描
（規格書原本也說第一階段不需要，等真正接回試算表同步、有重複計算風險
時才需要）。資料模型已經預留擴充空間，第二階段要接回試算表同步時不需要
重新設計資料結構。

### 使用者需要知道的事——這次需要手動設定，且橫跨兩個 repo

1. **這個 repo（`tsaipeilinebot`）只需要一個新的 Cloud Run 環境變數**
   （在 Cloud Shell 執行，`recruitment-bot` 是配送部系統跟招募機器人
   共用的同一個 Cloud Run 服務）：
   ```bash
   gcloud run services update recruitment-bot \
     --region asia-east1 \
     --update-env-vars DELIVERY_RIDER_WEBHOOK_SECRET="自己想一組隨機字串"
   ```
   跑完看到 `Service [recruitment-bot] revision ... has been deployed`
   就代表設定成功。

2. **`delivery-gas-project` 那個 repo 要另外設定三個「指令碼屬性」**
   （這不是 Cloud Run 環境變數，是 Google Apps Script 專案自己的設定，
   要打開 https://script.google.com 進到那個專案的編輯器，左側選單
   「專案設定」→「指令碼屬性」新增）：
   - `RIDER_EVENTS_WEBHOOK_URL`：填
     `https://<Cloud Run 服務網址>/delivery/api/rider-events`
     （Cloud Run 服務網址可以在 GCP Console 的 Cloud Run 頁面看到，
     或執行 `gcloud run services describe recruitment-bot --region
     asia-east1 --format="value(status.url)"` 查詢）
   - `RIDER_BINDING_SYNC_URL`：填
     `https://<Cloud Run 服務網址>/delivery/api/rider-binding-sync`
   - `RIDER_WEBHOOK_SECRET`：填**跟步驟 1 一模一樣**的那組字串
   三個都設定好之後不需要重新部署 Apps Script，指令碼屬性即時生效。

3. **`delivery-gas-project` 這次的程式碼變更，合併到 `main` 後會由
   現有 CI/CD（`clasp-push.yml`）自動 `clasp push` + `clasp deploy`
   上線，不需要手動跑 `git pull && clasp push`**——這點跟這個 repo
   的 CLAUDE.md 目前寫的「需要主動提醒使用者手動 clasp push」不一樣，
   是因為 CLAUDE.md 那段說明是自動部署上線之前寫的，已經過時，這裡
   一併記錄更新後的實際狀況，避免下次又誤以為需要提醒手動部署。

4. **兩個 repo 都設定好之後，還需要騎士先私訊「綁定+工號+姓名」完成
   綁定**（這個既有功能不用改），系統才會知道這是已登記的合作騎士；
   接著到配送部系統的「門市當日量管理」（主頁 → 外送員接單媒合）登記
   幾筆門市當日量，就可以請騎士實際測試「查詢附近單」「瀏覽報班」這
   兩個功能了。

### 測試

新增 `tests/test_delivery_rider_repository.py`（`_evaluate_claim()`／
`_evaluate_registration()`／`_haversine_km()` 純函式邊界情況）、
`tests/test_delivery_rider_events.py`（未綁定/已停用擋下、關鍵字觸發、
Postback 分派、承接件數暫存狀態）、`tests/test_delivery_rider_routes.py`
（後台頁面未登入導向、webhook 密鑰驗證）。全部測試（`python3 -m
unittest discover -s tests -p "test_*.py"`）1608 個全數通過。

`delivery/templates/help.html` 新增「外送員接單媒合」章節，維持每次
新功能都同步更新使用說明的紀律。

### 追加：地點主檔＋搜尋式下拉選單，取代手動輸入經緯度（2026-09-19）

上線後使用者反映「門市當日量管理」新增表單要手動輸入經緯度很不方便。
改成新增「**地點管理**」頁面（`/delivery/rider/locations`），主管先把
常用地點（門市、倉庫等）連同經緯度登記一次，之後「門市當日量管理」
「報班時段管理」的新增表單都改用**帶搜尋功能的下拉選單**（HTML5
`<input list> + <datalist>`，不需要額外的 JS 套件或 Google Maps API
金鑰）選現成的地點，選了之後經緯度自動帶出，不用再手動輸入數字。

- `delivery/rider_repository.py` 新增地點主檔 CRUD：`create_location`／
  `list_locations`／`get_location`／`set_location_active`，跟服務區域
  管理／裝備品項管理同一套「主管自行維護清單、只能停用不能刪除」模式。
  新集合 `delivery_rider_locations`。
- 「新增門市當日量」「新增報班時段」表單改送 `location_id`（不再是
  `store_name`/`lat`/`lng` 或自由輸入的 `location` 文字），後端一律用
  `rider_repository.get_location()` 查地點主檔的名稱/經緯度，不相信表單
  直接送來的數字——比原本讓同仁自己輸入更不容易打錯或存進不合理的座標。
  門市當日量、報班時段的紀錄本身還是各自存一份自己的 `store_name`／
  `location`（在建立當下從地點主檔複製過去），之後就算地點主檔改名或
  停用，既有紀錄的顯示內容也不會跟著變動。
- 前端這組「輸入文字即時比對地點清單、選到後把對應 ID 寫進隱藏欄位」
  的邏輯是純 vanilla JS（見 `rider_store_deliveries.html`／
  `rider_shifts.html` 內嵌的 `<script>`），用 Jinja2 內建的 `tojson`
  filter（Starlette 的 `Jinja2Templates` 有內建，這個 repo 的
  `applicants_list.html` 已經用過）把地點清單傳給前端，沒有新增任何
  npm 套件或第三方地圖服務依賴。
- 這次沒有採用另外兩個討論過的方案：①嵌入互動地圖讓同仁點選位置
  （需要额外的地圖套件或 Google Maps API 金鑰，考慮到只是內部少量地點
  登記，投入產出比不划算）、②貼 Google 地圖分享連結自動解析經緯度
  （同仁要人在門市現場才拿得到分享連結，對「先建好清單、事後隨時登記
  當日量」的使用情境不合適）。地點主檔＋搜尋下拉選單是使用者確認後
  選定的方向，之後如果同仁反映經緯度還是不好查，可以再補上①當作
  「地點管理」新增表單本身的輔助工具（不影響已經做好的下拉選單機制）。

**這次不需要任何額外的手動設定步驟**，合併後自動部署即可生效；地點
資料需要主管自己先到「地點管理」登記，程式碼無法代勞。

新增 `tests/test_delivery_rider_locations.py`：地點主檔 CRUD、
`/rider/locations` 管理路由、以及門市當日量／報班時段建立路由改用
`location_id` 解析後的行為（找不到地點／地點已停用／正常解析出名稱
與經緯度）。全部測試（`python3 -m unittest discover -s tests -p
"test_*.py"`）1624 個全數通過。

### 追加：修正「任何私訊都被回覆尚未綁定」的迴歸（2026-09-19）

上線後使用者回報：騎士傳任意文字（不管是不是在跟接單/報班互動、也不管
有沒有綁定過）都會收到「您目前還沒有完成綁定...」這句話。

**根本原因**：`delivery-gas-project` 的 `doPost()`（見上面「外送員接單
媒合」那節）除了「綁定+工號+姓名」「接受本日發包任務」這兩種固定格式，
其餘私訊文字一律轉發到 `/delivery/api/rider-events`，這個設計本身沒問題
（沿用車輛/意外事件回報同一種「不在這裡解析格式、全部丟給業務邏輯判斷」
做法）；但 `rider_events.handle_rider_event()` 原本的判斷順序是**先查
這個 LINE UserId 有沒有綁定，再判斷事件內容跟接單/報班有沒有關係**——
代表任何人傳任何一句話（例如單純的閒聊），只要還沒綁定，都會先被擋在
「未綁定」這一關，收到文不對題的回覆，比完全不回覆更糟（改版前，這些
不相干的私訊本來就是安靜略過，不會有任何回覆）。

**修正**：把判斷順序反過來——`handle_rider_event()` 先判斷這則事件
是不是真的看起來在跟這兩個功能互動（Postback、位置訊息、或文字符合
查詢附近單/瀏覽報班關鍵字、或是純數字），**不相關的事件直接安靜略過，
完全不會去查 Firestore 的綁定資料**；只有判斷相關之後，才會查綁定
狀態、視情況回覆「尚未綁定」或「已停用」。純數字文字（用來接收騎士
輸入的承接件數）仍然會被視為「相關」，所以理論上還是有極小機率誤判
（例如未綁定的人剛好傳一串純數字的不相干訊息），但比「任何文字都算
相關」的範圍小非常多，也是這個功能設計上必要的取捨（沒有其他方式能
分辨一則純數字訊息是不是回覆件數）。

**這次不需要任何額外的手動設定步驟**，合併後自動部署即可生效，也不用
碰 `delivery-gas-project` 那個 repo（doPost() 的轉發邏輯本身沒有問題，
問題完全在這個 repo 的 `rider_events.py`）。

新增迴歸測試 `test_unrelated_text_from_unbound_user_stays_silent`
（`tests/test_delivery_rider_events.py`），確認不相關文字連
`get_rider_binding()` 都不會呼叫。全部測試（`python3 -m unittest
discover -s tests -p "test_*.py"`）1625 個全數通過。

### 追加：位置訊息／純數字文字改成要有前置動作才算相關（2026-09-19）

使用者反映上一次修正後，「傳送位置資訊」跟「純數字文字」這兩種觸發方式
還是太容易誤觸發——任何一次位置分享、任何一句剛好是數字的訊息，都會
被當成跟接單/報班相關而查綁定狀態、產生回覆，即使使用者根本不是在跟
這兩個功能互動。

**修正**：這兩種情況額外要求「使用者剛做過對應的前置動作」才算相關：
- 位置訊息：只有騎士剛私訊過「查詢附近單」類關鍵字（10 分鐘內、
  `RIDER_PENDING_CLAIM_TTL_SECONDS`）才算相關，改用新的
  `rider_repository.set_awaiting_location()` / `pop_awaiting_location()`
  （跟 `pending_claim` 同一種暫存機制，存在 `delivery_rider_bindings`
  文件的 `awaiting_location` 欄位）。
- 純數字文字：只有真的有暫存中的承接操作（`has_pending_claim()`
  為 True，只檢查不清除）才算相關，清除交給真的處理這則訊息時的
  `pop_pending_claim()` 做，避免尚未確認相關就先把暫存清掉。

不影響「查詢附近單」「瀏覽報班」等關鍵字文字、Postback 按鈕這兩種
明確的觸發方式，這兩種還是無條件視為相關。

**這是使用者主動要求收斂的取捨**，代價是：如果騎士沒有先打關鍵字就
直接分享位置，或者暫存操作真的過期後才回覆數字，系統會完全不回應
（不會再顯示「逾時失效，請重新查詢附近單一次」這種提示）——比起可能
被任意訊息誤觸發，使用者判斷這個代價比較能接受。

**這次不需要任何額外的手動設定步驟**，合併後自動部署即可生效。

新增測試：`tests/test_delivery_rider_repository.py` 的
`HasPendingClaimTests`／`AwaitingLocationTests`（新函式的邊界情況），
`tests/test_delivery_rider_events.py` 更新位置/數字文字相關測試,
新增「沒有前置動作就安靜略過」的測試案例。全部測試（`python3 -m
unittest discover -s tests -p "test_*.py"`）1636 個全數通過。

### 追加：即時接單／報班媒合的地點清單拆成兩份獨立清單（2026-09-19）

使用者反映：即時接單（門市當日量）用到的地點，跟報班媒合用到的地點，
根本是兩組不同的地方，「必須分開」——但上一版的地點主檔（見前面「地點
主檔＋搜尋式下拉選單」那節）是兩個功能共用同一份清單，選單選項會混在
一起。

**修正**：把地點主檔拆成完全獨立的兩份，互不共用、互不顯示：

- 新增 Firestore 集合 `delivery_rider_shift_locations`（報班媒合專用）；
  原本的 `delivery_rider_locations` 集合維持不變，改成只給即時接單
  （門市當日量）用——**既有的地點資料不會遺失，也不需要搬移**，因為
  這份清單原本本來就是給即時接單的門市/取貨地點用的。
- `delivery/rider_repository.py` 原本的通用函式改成兩組各自獨立的
  函式：即時接單用 `create_order_location`／`list_order_locations`／
  `get_order_location`／`set_order_location_active`；報班媒合用新增的
  `create_shift_location`／`list_shift_locations`／`get_shift_location`／
  `set_shift_location_active`。兩組函式各自對應各自的 Firestore 集合，
  完全獨立，不會互相查到對方的資料。
- 後台新增一個獨立頁面「**報班地點管理**」（`/delivery/rider/shift-
  locations`），跟原本的「**即時接單地點管理**」（原本的
  `/delivery/rider/locations`，網址不變，只是頁面標題跟說明文字改成
  明確標示「這份清單只給即時接單用」）並列，操作方式一模一樣（登記
  名稱＋經緯度、可停用不能刪除）。
- 「新增報班時段」表單的搜尋式下拉選單改吃報班地點清單
  （`rider_repository.list_shift_locations()`），「新增門市當日量」
  表單維持吃即時接單地點清單（`rider_repository.list_order_locations()`）
  ——這兩個表單原本就已經各自送出獨立的 `location_id`，這次只是後端
  查詢/驗證的來源改成各自對應的集合，前端下拉選單本身的搜尋邏輯完全
  沒變。
- `delivery/templates/home.html`、`help.html` 都拆成两个各自獨立的
  連結/說明段落，避免使用者誤以為兩個功能共用同一份地點清單。

**使用者需要手動處理的部分**：因為報班媒合原本掛在即時接單那份地點
清單底下，這次拆分之後，**報班媒合會用到的地點目前是空的清單**，需要
主管自己到「報班地點管理」（`/delivery/rider/shift-locations`）把
報班會用到的地點（連同經緯度）重新登記一次——即使名稱剛好跟即時接單
清單裡的地點一樣，也要另外登記，因為兩份清單完全獨立、不會互相帶用。
除此之外**不需要其他手動設定步驟**，合併後自動部署即可生效，不影響
既有的門市當日量資料跟已經建立好的報班時段（這些紀錄本身各自存了自己
的地點名稱/經緯度快照，不受這次拆分影響）。

**（2026-09-22 使用者回報已重新登記完成。）**

`tests/test_delivery_rider_locations.py` 改寫成涵蓋兩組獨立函式跟
路由（含一筆「建立報班時段不會誤用即時接單地點清單」的防呆測試），
新增 `tests/test_delivery_rider_routes.py` 的 `/rider/shift-locations`
登入導向測試。全部測試（`python3 -m unittest discover -s tests -p
"test_*.py"`）1647 個全數通過。

### 追加：即時接單「分享位置」改用 Quick Reply 一鍵按鈕（2026-09-19）

使用者反映騎士要自己點左下角「+」再選「位置資訊」才能分享位置，操作
起來不方便，問有沒有更簡單的方式。

**做法**：`rider_messages.prompt_share_location_message()` 這則提示訊息
改成附上 LINE 的 **Quick Reply**（對話框上方會多跳出一排按鈕，這裡只放
一顆「分享目前位置」）、action 型別是 `location`——騎士點一下這顆按鈕，
LINE 會直接跳出內建的位置選擇畫面，不用再自己去翻「+」選單找「位置
資訊」，省一道操作步驟。

這個改動完全只在 `tsaipeilinebot` 這邊，**delivery-gas-project 那支 GAS
專案完全不用改、也不用重新部署**：`replyLineRawMessages_()` 本來就是把
Python 這邊回傳的訊息物件（`{"messages": [...]}`）原封不動塞進 LINE
Reply API 轉發，`quickReply`只是這個訊息物件裡多一個欄位，GAS 那邊
不需要認得這個欄位是什麼、也不會因為多了這個欄位而出錯。

Quick Reply 按鈕是「這一則訊息附帶的」，只會在騎士回覆這則提示訊息時
出現一次，不會變成常駐選單（跟圖文選單/Rich Menu 是不同機制，也不會
互相衝突）——如果之後使用者自己在 LINE 官方帳號後台設定的圖文選單上
也想加一顆「查詢附近單」按鈕，兩者可以並存，圖文選單按鈕觸發之後一樣
會先跳出這則帶 Quick Reply 按鈕的提示訊息。

**這次不需要任何額外的手動設定步驟**，合併後自動部署即可生效。

新增 `tests/test_delivery_rider_messages.py`（`prompt_share_location_
message()` 附帶 `location` 型別 Quick Reply 按鈕的邊界情況），更新
`tests/test_delivery_rider_events.py` 對應的斷言文字。全部測試
（`python3 -m unittest discover -s tests -p "test_*.py"`）1649 個
全數通過。

### 追加：即時接單/報班媒合改成照人員名冊的合作方式（承攬/雇傭）判斷資格（2026-09-21）

即時接單、報班媒合上線前討論規劃時，使用者說明：即時接單的人員屬於
**承攬制**，報班媒合的人員在上班當日屬於**雇傭制**——這是配送部系統
既有的「人員名冊」（`delivery_personnel`）本來就有在追蹤的
`cooperation_type`（合作方式）欄位所代表的兩個類別，但原本騎士接單
機器人的綁定名單（`delivery_rider_bindings`）跟人員名冊完全是兩份互不
相關的資料（騎士在 LINE 自己打工號/姓名，系統只是存起來，從來沒有真的
去核對人員名冊），沒辦法用「合作方式」判斷資格。使用者確認同一人不會
同時擁有承攬跟雇傭兩種身份（結束一種合作可能會換成另一種），所以不需要
處理「同一人同時多重身份」這種複雜情境，人員名冊原本「一人一個
cooperation_type」的設計本身就夠用。

**做法（把人員名冊跟騎士綁定名單正式串起來）**：

- `delivery_personnel` 新增 `employee_no`（工號）欄位——這是全新欄位，
  之前人員名冊完全沒有「工號」這個概念，靠 Firestore 內部 ID 識別一個
  人。新增/編輯人員的表單（`personnel_form.html`／`personnel_detail.html`
  的「一鍵全部更新」）都加上這個欄位。目前只有蝦皮系列廠商的人員會用到。
- `delivery_cooperation_types`（合作方式清單）每個選項加一個 `category`
  欄位，值是 `contract`（承攬）或 `employed`（雇傭）——原本「二輪承攬／
  二輪雇傭／三輪雇傭」這幾個選項只是主管自己取的中文名稱，系統從來沒有
  結構化地知道「這個選項算承攬還是算雇傭」（只有幾支舊程式碼直接寫死
  比對特定的文件 ID 字串）。改成主管在「合作方式管理」頁面新增/編輯
  合作方式時，都要明確勾選屬於哪一類，之後不管新增多少個選項，資格
  判斷邏輯都不用跟著修改。
- 騎士在 LINE 綁定「工號+姓名」時（`rider_repository.upsert_rider_
  binding()`），改成拿工號去 `repository.find_personnel_by_employee_no()`
  核對人員名冊，核對到就把對應的 `personnel_id` 存進這筆綁定資料——
  **每次綁定同步都重新查一次**，不是只在第一次綁定時查，這樣才能跟著
  人員名冊之後的異動（例如改了合作方式）自動更新。
- 新增 `rider_repository.rider_feature_category(binding)`：查這位騎士
  綁定對應到的人員名冊資料，目前的合作方式屬於承攬還是雇傭，查不到
  人員資料／查無合作方式／合作方式沒設定分類，都回傳空字串（視同兩個
  功能都不能用）。`rider_events.py` 在即時接單相關的關鍵字（查詢附近單
  等）、Postback（`CLAIM_STORE`）判斷資格要求回傳 `contract`；報班媒合
  相關的關鍵字（瀏覽報班）、Postback（`SHIFT_LIST`／`REGISTER_SHIFT`）
  要求回傳 `employed`。不符合資格的話回覆新增的
  `not_eligible_for_order_message()`／`not_eligible_for_shift_message()`。
  只在這幾個「進入點」判斷資格，後續的位置訊息/純數字件數輸入
  （`_handle_location`／純數字分支）不用重複判斷——不符資格的人根本
  不會走到設定 `awaiting_location`／`pending_claim` 那一步，這兩個暫存
  狀態不可能被不符資格的人觸發。
- 「騎士名單管理」（`/rider/riders`）後台頁面加一欄「合作身份」，即時
  顯示每位騎士目前算出來的承攬/雇傭/未對應，方便管理員核對——「未對應」
  代表工號在人員名冊找不到、或合作方式還沒設定分類，頁面上直接附連結到
  人員名冊跟合作方式管理頁面方便排查。**這個啟用/停用開關維持不變**，
  是管理員的手動覆蓋層，疊在合作身份判斷之上（就算身份符合，管理員還是
  可以手動停用特定騎士）。

**一次性資料搬移（工號補進人員名冊）**：新增 `repository.
match_shopee_personnel_employee_no(rows)`，只在**蝦皮系列廠商**（
`SHOPEE_VENDOR_CODES`：蝦皮三輪／蝦皮二輪公司車／蝦皮二輪雇傭自備車／
蝦皮承攬／蝦皮三輪速配倉）的人員名冊資料裡，用姓名找唯一對得上的一筆
寫入工號；同名同姓找到不只一筆、查無此人、或這筆資料本來就已經有工號，
都不自動寫入，回傳清單讓呼叫端列出來給管理員人工核對，避免寫錯人或蓋掉
手動修正過的資料。新增 webhook 端點 `POST /api/personnel-employee-no-
sync`（共用既有的 `RIDER_WEBHOOK_SECRET`，不需要新密鑰），給
`delivery-gas-project` 的 `syncPersonnelEmployeeNo()` 一次性手動執行呼叫
（沿用之前 390 人綁定搬移同一份「人員管理」試算表的工號＋姓名欄位，不
需要蒐集新資料）。

**這次的手動設定步驟**：
1. 到「合作方式管理」（`/delivery/cooperation-types`）把既有的「二輪
   承攬」「二輪雇傭」「三輪雇傭」（以及其他廠商已經建立的合作方式）
   都補上「承攬」或「雇傭」分類——這是**唯一一定要做**的步驟，沒設定
   分類的合作方式，底下的人不管即時接單還是報班媒合都不能用。
   **（2026-09-22 使用者回報已補完成。）**
2. 到 `delivery-gas-project` 的 Apps Script 編輯器新增指令碼屬性
   `PERSONNEL_EMPLOYEE_NO_SYNC_URL`（見該 repo HANDOFF.md），執行一次
   `syncPersonnelEmployeeNo()`，把既有蝦皮系列人員的工號補進人員名冊；
   執行紀錄列出來的「同名同姓」「查無此人」需要另外手動到人員名冊核對
   補上。**（2026-09-22 使用者回報已執行；執行紀錄列出的同名同姓/查無
   此人名單，麻煩之後找時間到人員名冊逐筆核對補上工號。）**
3. 之後新報到的人員，工號在建立人員名冊資料時一起填即可，不需要再跑
   搬移腳本。

即時接單、報班媒合這兩個功能目前都還在規劃/測試階段、尚未正式對外
上線，所以**這次刻意沒有處理「資料還沒補齊時要不要保留舊行為」這種
過渡期的相容性問題**——工號/合作方式分類沒設定好之前，兩個功能一律
不能用，等正式上線前再視情況決定要不要放寬。

新增/更新測試：`tests/test_delivery_cooperation_types.py`（`category`
欄位的 CRUD／表單驗證）、`tests/test_delivery_personnel_employee_no.py`
（工號欄位 CRUD、`find_personnel_by_employee_no()`、
`match_shopee_personnel_employee_no()` 的比對/同名同姓/已有工號情境、
人員詳細頁表單串接）、`tests/test_delivery_rider_repository.py`
（`upsert_rider_binding()` 串 `personnel_id`、`rider_feature_category()`
邊界情況）、`tests/test_delivery_rider_events.py`（新增
`EligibilityGatingTests`，覆蓋即時接單/報班媒合各自的關鍵字跟 Postback
資格判斷）、`tests/test_delivery_rider_routes.py`（`/api/personnel-
employee-no-sync` webhook、騎士名單管理頁面附上合作身份）。全部測試
（`python3 -m unittest discover -s tests -p "test_*.py"`）1684 個全數
通過。

### 追加：即時接單/報班媒合開需求時可以調整服務半徑（幾公里內才看得到，2026-09-21）

使用者需求：同仁開門市當日量（即時接單）或報班時段（報班媒合）時，能
調整「只有幾公里內的騎士才看得到」，預設 10 公里、可以每一筆各自調整。

**即時接單**：
- `create_store_delivery()` 新增 `radius_km` 參數（預設
  `RIDER_DEFAULT_SEARCH_RADIUS_KM = 10`），存進門市當日量這筆資料本身。
- `list_nearby_open_stores()` 算出距離之後，多一道篩選：距離超過**這筆
  門市自己**的 `radius_km` 就跳過（不是全域一個半徑，每筆可以不一樣）；
  算不出距離的（理論上不會發生，建立時就一定會有經緯度）不套用限制，
  一律視為符合，避免資料異常時整筆憑空消失。
- 「新增門市當日量」表單加一個「服務半徑」輸入框，預設值 10，同仁可以
  自行調整；後台清單也加一欄顯示。

**報班媒合**（改動比即時接單大，因為報班時段原本完全沒有位置概念）：
- 報班時段（`rider_shift_postings`）原本只存地點名稱文字，這次比照即時
  接單改成也存 `lat`／`lng`（從報班地點主檔 `get_shift_location()` 解析
  出來，跟門市當日量解析地點的方式一樣），再加 `radius_km`。
- `list_open_shift_postings()` 改成可以選擇性帶 `lat`／`lng`：**沒帶**
  維持原本行為（全部開放中時段、依開始時間排序，後台管理／Postback
  觸發等不知道騎士位置的情境用這個模式，不受影響）；**有帶**才會依照
  每筆時段自己的 `radius_km` 篩選、依距離排序，跟即時接單那份邏輯
  對稱。沒有經緯度的舊資料（這個功能上線前建立的）不套用篩選，一律
  視為符合，避免舊資料整批消失。
- **這是這次改動裡唯一牽動使用者體感的部分**：因為要用騎士目前位置
  才能篩選「幾公里內」，**騎士瀏覽報班媒合現在也要先分享位置**，跟
  即時接單「查詢附近單」是同一種操作（傳「瀏覽報班」→ 跳出「分享目前
  位置」Quick Reply 按鈕 → 分享後才列出附近的時段），不再像之前那樣
  傳「瀏覽報班」就立刻列出全部時段。這是使用者確認過、比照即時接單
  體驗一致的做法（曾經考慮「不分享位置就列出全部」當退回選項，使用者
  選擇統一成分享位置的流程）。
- 新增 `rider_repository.set_awaiting_shift_location()` /
  `pop_awaiting_shift_location()`，跟即時接單的 `set_awaiting_location()`
  / `pop_awaiting_location()` 是同一種暫存機制，但存在不同欄位
  （`awaiting_shift_location`），兩條線互不干擾——一則位置訊息理論上
  可能同時符合兩條線各自的暫存（例如騎士連續問了「查詢附近單」又問
  「瀏覽報班」都還沒分享位置），這種極少見情況下兩邊暫存都會被清掉，
  但只會處理其中一種（即時接單優先），不會兩則都回覆。
- 「新增報班時段」表單、後台清單同步加上服務半徑欄位，跟即時接單那邊
  一致。
- `rider_messages.shifts_carousel()` 有距離資料時順便顯示「約 X.X
  公里」，比照 `nearby_stores_carousel()` 既有的做法；沒有距離資料
  （Postback 觸發、後台管理用的呼叫）就不顯示，不會印出奇怪的內容。

**這次不需要任何額外的手動設定步驟**，合併後自動部署即可生效；既有的
報班時段/門市當日量資料沒有服務半徑欄位時，一律回退成預設 10 公里，
不會突然消失或動不了。

新增/更新測試：`tests/test_delivery_rider_repository.py`
（`CreateStoreDeliveryRadiusTests`／`ListNearbyOpenStoresRadiusTests`／
`CreateShiftPostingRadiusTests`／`ListOpenShiftPostingsRadiusTests`／
`AwaitingShiftLocationTests`）、`tests/test_delivery_rider_events.py`
（報班媒合關鍵字改成先分享位置、位置訊息依前置動作分流到即時接單或
報班媒合）、`tests/test_delivery_rider_locations.py`（建立門市當日量/
報班時段時服務半徑的表單驗證與傳遞）、`tests/test_delivery_rider_
messages.py`（報班版分享位置提示、`shifts_carousel()` 距離顯示）。
全部測試（`python3 -m unittest discover -s tests -p "test_*.py"`）
1708 個全數通過。

### 修正：騎士名單管理的「合作身份」沒有即時反映人員名冊/合作方式的異動（2026-09-21）

使用者回報：在人員名冊補上工號、在合作方式管理設定好承攬/雇傭分類之後，
「騎士名單管理」（`/rider/riders`）的「合作身份」欄位沒有跟著更新，還是
顯示「未對應」。

**根本原因**：`rider_feature_category(binding)` 原本是拿騎士綁定資料裡的
`personnel_id` 去查人員名冊——但 `personnel_id` 這個欄位是
`upsert_rider_binding()`（騎士在 LINE 傳「綁定+工號+姓名」時、或
`backfillRiderBindings8()` 批次搬移時）當下查一次工號寫進去的**快照**，
之後在人員名冊/合作方式管理頁面另外補資料，不會回頭更新這個快照。也就是
說，只有「先在人員名冊建好工號、設定好合作方式分類，騎士才第一次綁定」
或「管理員事後又手動重跑一次 GAS 的 `backfillRiderBindings8()`」這兩種
情境才會抓到最新結果，跟原本設計文件裡寫的「即時查、不是綁定當下寫死」
其實對不上——這是一個真正的程式邏輯漏洞，不是操作步驟或環境設定問題。

**修法**：`rider_feature_category()` 改成不依賴這個快照欄位，每次都直接
拿騎士綁定資料裡的 `employee_id`（騎士自己在 LINE 輸入的工號，這個欄位
本來就每次同步都會更新）現查一次
`repository.find_personnel_by_employee_no()`，再往下查合作方式分類——
真正做到「查詢當下人員名冊/合作方式管理是什麼設定，就回傳什麼結果」，
不會再有「補完資料後还要等騎士重新綁定或管理員重跑搬移腳本」的問題。
綁定資料裡的 `personnel_id` 欄位保留（給後台顯示/除錯用），但資格判斷
已經完全不靠它。

**這次不需要任何手動設定步驟**，合併部署後「騎士名單管理」頁面重新整理
就會看到最新的合作身份，不用請騎士重新綁定、也不用重跑
`backfillRiderBindings8()`。

更新測試：`tests/test_delivery_rider_repository.py` 的
`RiderFeatureCategoryTests` 改成 mock `find_personnel_by_employee_no()`
而不是 `get_personnel()`。全部測試（`python3 -m unittest discover -s
tests -p "test_*.py"`）1708 個全數通過。

## 人員詳細頁：所屬廠商改了，合作方式選單即時跟著篩選/單一選項自動帶入（2026-09-21）

使用者反映：人員詳細頁的「所屬廠商」跟「合作方式」都是單一對應值（例如
某個廠商目前只設定了一種合作方式可以選），改廠商之後應該不用還要自己
再去合作方式選單裡篩選/手動點選。

**原本的行為**：合作方式下拉選單本來就有照「合作方式管理」設定的
「適用廠商」在伺服器端篩選過（`repository.list_cooperation_types(vendor=
...)`），但這是頁面**第一次載入**時算好的；「所屬廠商」是可以直接在
下拉選單改的（不用重新整理頁面），改了之後畫面上合作方式的選項不會
跟著換一批——同仁還是要自己記得「這個廠商應該對應哪個合作方式」、
從舊廠商那份選項清單裡挑（甚至可能挑到不屬於新廠商的選項，一鍵更新
送出時才被伺服器擋下清空，同仁看不出原因）。

**修法**：

- `repository.cooperation_types_by_vendor()`（新增）：回傳
  `{廠商代碼: [{"id":..., "name":...}, ...]}`，把全部合作方式按「適用
  廠商」重新分組一次——這份資料本來就分散在 `applicant_routes.py`
  （應徵名單頁的廠商/合作方式聯動）自己組一次，現在抽成共用函式，兩個
  地方都改用它。
- 人員詳細頁（`personnel_detail.html`）新增一段 JS：所屬廠商選單
  `change` 事件觸發時，用 `COOPERATION_TYPES_BY_VENDOR`（伺服器端傳入
  的上面那份資料）即時重建合作方式選單的選項——**篩選後如果剛好只剩
  一個選項，直接帶入，不用同仁自己點**；如果原本選的值在新廠商底下還
  是合法選項就保留；都不符合就清空回「尚未決定」，不會殘留舊廠商的
  選項。
- 「新增人員」表單（`personnel_form.html`，這個頁面廠商是網址固定的、
  不能在頁面上改）也同步調整：伺服器端算出的合作方式選項如果剛好只有
  一個，預設值直接是那個選項（不是「尚未決定」），同仁還是可以手動
  改選別的，只是不用因為只有一個選項還要多點一次。
- **順便補一個既有漏洞**：「一鍵全部更新」（`bulk_update_personnel`）
  原本存合作方式時只檢查這個 ID 存不存在，沒檢查是不是真的適用**這次
  要存的廠商**——畫面上有 JS 擋，但表單被竄改、或 JS 沒執行時，伺服器
  端會照單全收存進不合理的組合。改成存之前一定重新核對一次「這次要存
  的廠商（如果這次同時改了廠商，用新廠商；沒改就用這個人原本的廠商）」
  是不是真的在這個合作方式的適用清單裡，兜不起來就清空存空字串，跟
  「新增人員」「應徵名單→錄取」既有的驗證邏輯一致。

**這次不需要任何手動設定步驟**，合併部署後直接生效。

新增/更新測試：`tests/test_delivery_cooperation_types.py`
（`CooperationTypesByVendorTests`）、`tests/test_delivery_personnel_
vendor_change.py`（`BulkUpdatePersonnelCooperationTypeVendorMatchTests`，
涵蓋「廠商沒變但合作方式對不上」「同一次送出裡廠商也一起改了」「合作
方式 ID 根本不存在」）、`tests/test_delivery_personnel_equipment_debt.py`
（人員詳細頁 context 有帶上 `cooperation_types_by_vendor`）。全部測試
（`python3 -m unittest discover -s tests -p "test_*.py"`）1716 個全數
通過。

### 追加：一次性腳本把既有人員的合作方式按廠商唯一對應自動補齊（2026-09-21）

使用者需求：很多人員已經選好「所屬廠商」，但「合作方式」還沒填——使用者
已經確認過，這些人目前的廠商在合作方式管理裡都是唯一對應（不是一個廠商
同時掛著好幾種合作方式那種情境），要求一次補齊，不用等同仁一筆一筆去
人員詳細頁手動點。

新增 `scripts/fill_personnel_cooperation_type.py`，做法跟
`scripts/seed_cooperation_types.py` 同一套模式（純函式 `plan_fill()` 負責
規劃、不碰 Firestore 寫入，方便寫單元測試；`main()` 才是真的查資料/寫入
的部分）：

- 只處理「合作方式目前是空的」人員，已經填過的（不管是不是這裡會自動
  判斷出來的值）一律跳過，不會覆蓋同仁手動修正過的資料。
- 只有「這個人的廠商目前剛好對應到唯一一種合作方式」才會自動補上——
  對應到不只一種、或完全沒有對應到任何選項的，都列出來但不自動處理，
  避免猜錯人員身份（會影響外送員接單媒合的承攬/雇傭資格判斷，猜錯的
  代價比留白還高）。
- 沒有選廠商的人員不在處理範圍內，一併跳過。
- 執行前會先印出即將變更的完整名單，要求輸入 `yes` 才會真的寫入。

**使用方式**（在 Cloud Shell，位於 repo 根目錄）：

```
python -m scripts.fill_personnel_cooperation_type
```

看到印出來的名單確認沒問題後輸入 `yes` 執行。如果印出「無法自動判斷、
需要人工處理」的名單，代表那些人的廠商在「合作方式管理」裡還是對應到
不只一種（或零種）合作方式，麻煩到 `/delivery/cooperation-types` 把
「適用廠商」的勾選調整成真的唯一對應之後，可以重新執行這支腳本（已經
補過的人不會被動到，安全地重複執行）。

**這次不需要額外的 Cloud Run/GitHub 設定**，純粹是一次性的資料補齊
腳本。

新增測試：`tests/test_fill_personnel_cooperation_type.py`
（`PlanFillTests`，涵蓋唯一對應/已填過/沒廠商/多選項/零選項五種情境）。
全部測試（`python3 -m unittest discover -s tests -p "test_*.py"`）
1722 個全數通過。

### 追加：騎士名單管理加上編輯工號/姓名的功能（2026-09-21）

使用者需求：騎士名單管理原本只能啟用/停用，沒有地方能修正工號/姓名。

**原本的限制**：騎士的工號/姓名只能靠 LINE「綁定+工號+姓名」私訊帶進來
（`rider_repository.upsert_rider_binding()`），打錯字（尤其工號，直接
影響能不能對應到人員名冊、進而影響合作身份判斷）沒有地方能直接修正，
只能請騎士重新私訊一次，對管理員跟騎士都麻煩。

**做法**：新增 `POST /delivery/rider/riders/{user_id}/edit`
（`update_rider_info`，限管理員），直接呼叫跟 GAS 綁定同步**同一支**
`rider_repository.upsert_rider_binding()`——刻意不另外寫一套更新邏輯，
這樣後台編輯的行為（保留既有啟用/停用狀態、重新核對工號對應的人員
名冊）保證跟騎士自己重新私訊綁定一次完全一致，不會有兩套邏輯不同步
的風險。騎士名單管理頁面（`/rider/riders`）的「工號」「姓名」欄位合併
成一組可以直接編輯的輸入框＋「儲存」按鈕，跟既有的「啟用/停用」按鈕
並排。

**這次不需要任何手動設定步驟**，合併部署後直接生效。

更新測試：`tests/test_delivery_rider_routes.py`
（`UpdateRiderInfoTests`）。全部測試（`python3 -m unittest discover -s
tests -p "test_*.py"`）1724 個全數通過。

## 報班媒合：批次匯入時段、名額改人工審核制、時段清單篩選（2026-09-21）

使用者一次提出三個報班媒合的需求：
1. 報班時段管理需要批次匯入。
2. 騎士報名後不再由系統即時判斷成不成功，改成後台人工核准/駁回，核准＝
   報名成功，駁回＝額滿請改報其他時段。
3. 報班時段清單要能依地點/日期篩選。

**第 2 項原本規劃**要「管理員按核准/駁回時系統主動推播 LINE 訊息通知
騎士」，但這需要讓配送部系統（Python）反過來呼叫 GAS 才能觸發推播，會
動到 GAS 那邊**所有 LINE 機器人共用的核心轉發程式**（車輛回報、意外
事件回報、排班發包都靠它），風險比較高。跟使用者討論後，改成**騎士
自己傳「查詢報名狀態」主動查詢**，不用碰 GAS 那支共用程式，風險小很多
——這次完全沒有動到 delivery-gas-project 那個 repo。

### 報班名額改成人工審核制

- `rider_repository.py` 新增 `REGISTRATION_STATUS_PENDING` /
  `_APPROVED` / `_REJECTED` 三種狀態。`register_shift()` 寫入報名紀錄時
  一律先存成 `pending`；`_evaluate_registration()` 拿掉原本的「名額是否
  已滿」檢查（只保留「時段是否關閉」「是否重複報名」），因為額滿與否
  改成管理員的人工判斷，系統不再自動擋。
- 新增 `update_registration_status(registration_id, status)`：管理員在
  「報名名單」頁面（`/rider/shifts/{id}/registrations`）按「核准」／
  「駁回（額滿）」呼叫，**不會主動推播任何訊息**。
- `count_registrations()` 新增可選的 `status` 參數；`list_shift_postings()`
  跟 `list_open_shift_postings()` 顯示/計算的「剩餘名額」都改成只算
  `approved` 狀態的報名數（待審核的不算進佔用名額），管理員後台清單
  另外顯示「待審核」數量方便核對。`list_registrations()` 對舊資料（這次
  改動之前建立、沒有 `status` 欄位的報名紀錄）一律視為 `approved`，維持
  舊資料原本「能寫進 Firestore 就代表報名成功」的語意，不會讓舊紀錄
  在畫面上突然變成待審核。
- 新增 `list_registrations_by_rider(rider_id, limit=5)`：「查詢報名狀態」
  用，附上對應時段的地點/時間，依報名時間新到舊排序。
- `rider_events.py` 新增關鍵字「查詢報名狀態」「報名狀態」「查詢報班
  狀態」（跟「瀏覽報班」一樣限雇傭身份），回覆
  `rider_messages.shift_registration_status_message()` 組出的純文字
  清單。
- 報名成功的回覆文案改成「已收到您的報名！需等管理人員確認後才算報名
  成功，可以傳「查詢報名狀態」查詢目前結果。」。
- 報名名單頁面（`rider_shift_registrations.html`）每一筆報名加上狀態
  badge 跟「核准」／「駁回（額滿）」兩個按鈕，按鈕本身對應的狀態會停用
  （已經是核准狀態就不能再按一次核准），避免誤觸重複送出。

### 批次匯入報班時段

- 新增 `delivery/rider_csv_import.py`（`parse_shift_posting_csv()`），
  跟 `delivery/csv_import.py`（人員批次匯入）同一種「純函式解析、呼叫端
  決定寫不寫入」的分工。CSV 欄位：地點、開始時間、結束時間、需求人數
  （服務半徑選填，留空用預設值）。**「地點」要跟報班地點清單裡已經
  登記、啟用中的地點名稱完全一樣**，不會自動建立新地點，找不到就整列
  失敗、列出原因，不會用猜的建錯地點。開始/結束時間格式是
  `2024-01-31 09:00`（也接受 `T` 分隔）。
- 報班時段管理頁面（`rider_shifts.html`）新增上傳表單＋範本 CSV 下載
  連結（`/rider/shifts/import/template.csv`），匯入結果（成功/失敗筆數、
  失敗原因）直接顯示在同一頁。

### 報班時段清單篩選

- `list_shift_postings(location="", date_str="")` 新增這兩個可選篩選
  條件——地點/日期規模都不大，用「抓全部後在程式端篩選」，跟
  `repository.search_personnel()` 同一種做法，不用為此另外建 Firestore
  複合索引。日期篩選是比對時段開始時間換算成台北時區日期字串是否相符。
- 報班時段管理頁面新增地點下拉選單（來自報班地點清單）＋日期選擇器的
  篩選表單，網址帶 `?location=...&date=...` 查詢參數。

**這次不需要任何手動設定步驟**，合併部署後直接生效，也沒有動到
delivery-gas-project 那個 repo。

新增/更新測試：`tests/test_delivery_rider_repository.py`
（`EvaluateRegistrationTests` 改成不再測名額已滿、新增
`ListShiftPostingsFilterTests`／`RegistrationStatusTests`／
`ListRegistrationsByRiderTests`）、`tests/test_delivery_rider_events.py`
（新增查詢報名狀態關鍵字的資格判斷與內容測試）、
`tests/test_delivery_rider_messages.py`
（`ShiftRegistrationStatusMessageTests`）、`tests/test_delivery_rider_routes.py`
（`RiderShiftsPageFilterTests`／`UpdateRiderShiftRegistrationStatusTests`／
`RiderShiftsImportSubmitTests`）、新增
`tests/test_delivery_rider_csv_import.py`（`ParseShiftPostingCsvTests`）。
全部測試（`python3 -m unittest discover -s tests -p "test_*.py"`）
1758 個全數通過。

## 桃園所專區：Phase 1（人員/地點管理，2026-09-21）

桃園所有自己獨立的派遣業務（不是配送部的外送騎士）跟獨立的官方帳號，跟
使用者討論過後確認的整體規劃：

1. **入口權限**：不掛 `platform_accounts.MODULES` 那套模組權限勾選，改成
   照「部門」卡權限——帳號的 `department` 是「桃園所」、或全平台管理員，
   才看得到 `/portal` 上的「桃園所專區」卡片、進得去頁面。跟
   `/contract-summary`（`contract_summary_routes.py` 的
   `viewer_has_any_department_access()`）是同一種做法。
2. **LINE 官方帳號**：下一階段會直接在 Cloud Run／Python 這邊處理（比照
   招募主帳號 `main.py` 的 `/callback` 做法——這個系統本來就已經用這種
   方式跑兩個 LINE 帳號了，不是新技術），不會像配送部那樣透過
   delivery-gas-project 的 GAS 轉發——桃園所是全新帳號，沒有沿用 GAS
   既有機制的包袱，直接做在 Python 這邊更單純，之後核准/駁回也能直接
   主動推播通知人員（Python 直接握有這支帳號的 LINE Token）。
3. **運作方式**：跟報班媒合一樣是「開需求時段（地點+時段+人數）→ 人員
   報名→主管審核」，但人員資格會卡權限——人員名冊有「人員資格」欄位
   （理貨/作業員/餐飲有體檢/餐飲無體檢，複選），開需求時段也要勾選這筆
   需求屬於哪些資格類別，只有資格符合的人員看得到、能報名（下一階段
   實作）。

**這次（Phase 1）做的範圍**：新增 `services/taoyuan_dispatch_service.py`
（人員/地點的 Firestore CRUD、CSV 匯入純函式解析、部門權限判斷
`has_taoyuan_access()`）、`taoyuan_dispatch_routes.py`（`/taoyuan-dispatch`
系列頁面路由，直接掛在根 app，不是像 delivery/hr/management 那樣獨立
掛載的子系統，共用主平台登入 session）：

- **人員管理**（`/taoyuan-dispatch/personnel`）：姓名、電話、人員資格
  （複選，可事後修改），支援手動新增跟 CSV 批次匯入（已存在相同
  「姓名+電話」的人員會自動略過）。CSV 的「人員資格」欄位可以填中文
  名稱或代碼，逗號/頓號分隔多筆；看不懂的值只略過那一項資格，不會讓
  整列匯入失敗。
- **地點管理**（`/taoyuan-dispatch/locations`）：名稱、緯度、經度，支援
  手動新增跟 CSV 批次匯入，可停用但不刪除（跟裝備品項/服務區域等既有
  清單同一套設計語言）。
- `portal_routes.py` 的 `portal_home()` 在既有模組權限迴圈之外，另外
  判斷 `has_taoyuan_access()` 加一張卡片。

**這次（Phase 1）刻意還沒做的**：LINE 官方帳號綁定（人員傳「姓名+電話」
核對身份）、需求時段開單（含資格勾選）、人員報名、主管審核、核准/駁回
主動推播——這些是下一階段的 PR。

**這次不需要任何手動設定步驟**（部門「桃園所」、桃園所同仁帳號的部門
設定，使用者已經自行完成）。下一階段實作 LINE 官方帳號時，會需要把
Channel Token/Secret 設成 Cloud Run 環境變數。

新增測試：`tests/test_taoyuan_dispatch_service.py`（人員/地點 CRUD、CSV
解析、`has_taoyuan_access()`）、`tests/test_taoyuan_dispatch_routes.py`
（未登入導向、部門權限判斷、新增人員/地點路由）、`tests/test_portal.py`
（`PortalHomeTaoyuanDispatchCardTests`，卡片依部門顯示/隱藏）。全部測試
（`python3 -m unittest discover -s tests -p "test_*.py"`）1790 個全數
通過。

## 桃園所專區：Phase 2（LINE 官方帳號綁定+需求時段+報名審核+推播，2026-09-21）

延續 Phase 1，把「LINE 官方帳號綁定、需求時段開單、人員報名、主管審核、
核准/駁回推播通知」補齊。

**新增檔案**：

- `taoyuan_dispatch_line.py`：桃園所派遣專屬的第四組 LINE Messaging API
  Channel 客戶端（跟招募機器人、配送部、管理部各自獨立），比照
  `management/line_bot.py` 的做法——只做「實例化 LineBotApi/WebhookHandler、
  提供 `push_message()`」這一件事。
- `taoyuan_dispatch_bot.py`：解析人員在 LINE 上傳來的固定指令（綁定/需求
  列表/報名/我的報名），串接 `services/taoyuan_dispatch_service.py` 讀寫
  資料庫、回傳要回覆的文字。`parse_command()` 是純函式（不碰 Firestore），
  `handle_message()` 才會真的讀寫——跟 `hr/incident_report.py` 的拆法一樣，
  方便單元測試。**完全沒有 AI 對話邏輯**，指令看不懂就回覆固定的操作
  說明。
- `taoyuan_dispatch_webhook_routes.py`：`POST /taoyuan-dispatch/line/callback`
  收到訊息事件時驗證簽章、交給 `taoyuan_dispatch_bot.handle_message()`
  處理、回覆——比照 `management/routes/line_webhook_routes.py` 的拆法（
  webhook 路由只管簽章驗證跟轉交，訊息處理邏輯都在另一個檔案）。
- `services/taoyuan_dispatch_service.py` 新增：LINE 綁定（`bind_line_user()`
  /`get_binding()`/`get_bound_personnel()`——`get_bound_personnel()` 即時
  查目前的人員資格，不吃綁定當下的快照，因為資格之後可能被管理員修改）、
  需求時段（`create_posting()`/`list_postings()`/`set_posting_status()`/
  `list_open_postings_for_personnel()`——只回傳「人員資格符合、還沒報名
  過」的開放需求）、報名（`register_for_posting()` 用 Firestore
  transaction 包住查重複+寫入，`_evaluate_registration()` 抽成純函式方便
  測試，`update_registration_status()`）。
- `templates/taoyuan_dispatch_postings.html`、
  `templates/taoyuan_dispatch_posting_registrations.html`：需求時段管理跟
  報名審核頁面，UI 沿用 `delivery/templates/rider_shifts.html`／
  `rider_shift_registrations.html`（報班媒合）的版面（篩選列/資料表/核准
  駁回按鈕），只是多了「所需人員資格」複選欄位跟顯示。

**LINE 上的操作指令**（人員直接傳文字訊息給桃園所派遣這組官方帳號）：

| 指令 | 說明 |
|---|---|
| `綁定+姓名+電話` | 第一次使用要先綁定身分，例如「綁定+王小明+0912345678」，中間用「+」隔開，用姓名+電話比對既有人員資料 |
| `需求列表` | 查看目前開放報名、符合自己人員資格、還沒報名過的需求 |
| `報名 代碼` | 報名需求列表裡的某一筆（代碼是需求時段 Firestore 文件 ID 最後 6 碼，管理後台跟 LINE 訊息裡都會顯示） |
| `我的報名` | 查詢自己報名紀錄的審核狀態 |

管理員在 `/taoyuan-dispatch/postings` 開需求（地點/時段/人數/需要的人員
資格，資格至少要勾一項），在報名名單頁面按「核准」或「駁回」——按下去
的當下會直接推播 LINE 訊息通知該名人員審核結果（`push_message()` 送失敗
不會擋住審核狀態的寫入，只是這則通知沒送到，審核結果本身還是有效）。

**跟報班媒合（delivery 模組）的差異**：報班媒合核准/駁回不會主動推播，
人員要自己傳「查詢報名狀態」查——因為那組帳號的 LINE Token 目前是透過
`delivery-gas-project`（GAS）中轉，Python 這邊沒有直接握有 Token。桃園所
派遣是全新帳號，Python 直接握有 Token，所以能做到主動推播，不需要人員
自己查詢（不過「我的報名」指令還是留著，方便人員自己確認）。

**使用者需要手動處理的步驟**（這次跟之前申請管理部 LINE 帳號時的流程
一樣，材霈已經有經驗）：

1. **申請/準備一個新的 LINE Official Account + Messaging API Channel**
   （如果人資/業務團隊已經有一個要用在桃園所派遣的 LINE 官方帳號，直接
   用那個帳號啟用 Messaging API 就好，不用申請新的）：
   - 到 [LINE Developers Console](https://developers.line.biz/console/)
     登入，選擇一個 Provider（沒有的話先建立一個），在底下建立一個新的
     Messaging API Channel（或選擇既有的桃園所派遣官方帳號對應的
     Channel）。
   - 進到這個 Channel 的設定頁，「Messaging API」分頁：
     - 找到 **Channel access token**，按「Issue」產生一組長效 token，
       複製下來（這組等一下要貼到 Cloud Run 環境變數）。
     - 回到「Basic settings」分頁，找到 **Channel secret**，複製下來
       （同樣等一下要貼到 Cloud Run 環境變數）。
     - 「Messaging API」分頁裡把 **Webhook 的開關（Use webhook）打開**。
       **Auto-reply messages（自動回應訊息）、Greeting messages（加入
       好友的歡迎訊息）建議都關閉**——不關閉的話 LINE 官方帳號預設的
       罐頭回覆會跟我們自己的機器人搶著回覆，人員會同時收到兩則不一樣
       的訊息。
2. **打開材霈的 Google Cloud Shell**（跟之前部署流程一樣的操作方式），
   確認目前在專案 `tsaipei-505807`：
   ```bash
   gcloud config set project tsaipei-505807
   ```
3. **把上面複製的兩組值設成 Cloud Run 環境變數**（下面指令裡
   `貼上你的CHANNEL_ACCESS_TOKEN`、`貼上你的CHANNEL_SECRET` 要換成剛剛
   複製的實際內容，整段貼上執行）：
   ```bash
   gcloud run services update recruitment-bot \
     --region asia-east1 \
     --update-env-vars TAOYUAN_DISPATCH_LINE_CHANNEL_ACCESS_TOKEN="貼上你的CHANNEL_ACCESS_TOKEN",TAOYUAN_DISPATCH_LINE_CHANNEL_SECRET="貼上你的CHANNEL_SECRET"
   ```
   這一步在做什麼：把這組 LINE 帳號的金鑰交給 Cloud Run 上跑的服務，
   讓它可以代表這個官方帳號收發訊息。指令執行完會自動觸發一次新的
   部署（跟平常 `git push` 觸發的部署是同一件事，只是這次是手動觸發），
   跑完最後一行會顯示 `URL:` 開頭的服務網址，代表部署成功。
4. **回到 LINE Developers Console，把 Webhook URL 設定成**：
   ```
   https://recruitment-bot-412901869672.asia-east1.run.app/taoyuan-dispatch/line/callback
   ```
   貼上後按「Verify」按鈕測試，應該會顯示成功（綠勾勾）——這一步在做
   什麼：告訴 LINE，以後這個官方帳號收到的每一則訊息都要轉發到我們這支
   網址。**如果 Verify 失敗，先確認上一步的環境變數指令有沒有跑完、有
   沒有打錯字**，之後我可以幫忙一起排查。
5. **確認整條流程有接通**：拿一支手機加這個桃園所派遣官方帳號好友，先
   到 `/taoyuan-dispatch/personnel` 建一筆測試用的人員資料（姓名+電話），
   再用那支手機傳「綁定+測試姓名+測試電話」，應該會收到「綁定成功」的
   回覆。如果沒有回覆，先看 Cloud Run 的 Logs（`gcloud run services logs
   read recruitment-bot --region asia-east1 --limit 50`）找
   `[桃園所專區]` 開頭的錯誤訊息。

新增測試：`tests/test_taoyuan_dispatch_bot.py`（指令解析
`parse_command()`、`handle_message()` 綁定/需求列表/報名/我的報名各種
情境）、`tests/test_taoyuan_dispatch_webhook_routes.py`（未設定 Channel
Secret 回 503、缺簽章 header 回 400，跟管理部 webhook 測試同一種寫法）、
`tests/test_taoyuan_dispatch_service.py`（新增綁定/需求時段/報名相關
測試類別）、`tests/test_taoyuan_dispatch_routes.py`（新增開需求路由、
核准/駁回推播路由測試）。全部測試（`python3 -m unittest discover -s
tests -p "test_*.py"`）1848 個全數通過。

## 修正自動公告誤把「Merge pull request」合併說明當成公告標題（2026-09-21）

`.github/workflows/deploy.yml` 部署成功後的「系統更新自動公告」邏輯，
原本假設 PR 合併 commit 是 GitHub 網頁版「Squash and merge」那種格式
（`PR 標題 (#NNN)`），直接把 commit 訊息第一行當公告標題。但這個 repo
實際合併 PR 用的是一般合併（`git merge`／「Create a merge commit」），
commit 訊息格式其實是「Merge pull request #NNN from owner/branch」加
空行加 PR 標題——導致從 2026-09-19 自動公告功能上線後，**每一次**自動
公告的標題都變成這種使用者看不懂的技術性合併說明，真正的 PR 標題反而
被塞進公告內文。使用者在 `/portal` 看到 PR #174 合併後的公告標題長這樣
才發現這個問題。

**修正邏輯**：先判斷 commit 訊息第一行是不是「Merge pull request #NNN
from ...」這種格式，是的話改抓空行之後的第一行（真正的 PR 標題）當公告
標題；不是的話（例如哪天改成 squash 合併）才照原本假設的邏輯處理，
保留向後相容。順便把過濾 `Claude-Session`/`Co-Authored-By` 這兩行的
`grep` 改成不分大小寫（原本的大小寫沒對到我們實際使用的 trailer 格式）。

**舊公告怎麼辦**：這個修正只影響「之後」新發的公告，已經發出去的舊公告
資料不會自動被改掉。使用者選擇用一次性遷移腳本補救（而不是逐一手動
刪除重發），新增 `scripts/fix_legacy_announcement_titles.py`，做法跟
`scripts/fill_personnel_cooperation_type.py` 同一套模式（純函式
`plan_fix()` 負責規劃、不碰 Firestore 寫入，方便寫單元測試；`main()`
才是真的查資料/寫入的部分）：

- 只處理標題符合「Merge pull request #NNN from ...」這個技術性格式的
  公告，其他（含手動在網頁上發的正常公告）一律跳過不動。
- 內文第一行當新標題、其餘行當新內文；正常情況下內文只有一行（PR
  標題本身），修正後新內文會是空字串。
- 內文也是空的（沒辦法從內文救回真正標題）的公告列出來但不亂猜、不
  刪除，需要到 `/announcements` 手動處理。
- 執行前會先印出即將變更的完整名單，要求輸入 `yes` 才會真的寫入。

新增 `platform_announcements.update_announcement_title(announcement_id,
title, content)`，公告管理頁本身目前沒有編輯功能（只有新增/停用/刪除），
這個函式目前只有這支遷移腳本在用。

**使用方式**（在 Cloud Shell，位於 repo 根目錄，部署完這次修正之後再跑）：

```bash
python -m scripts.fix_legacy_announcement_titles
```

看到印出來的名單確認沒問題後輸入 `yes` 執行。這支腳本只需要跑這一次，
之後新發的公告已經不會再有這個問題。

**（2026-09-22 使用者回報已執行完成。）**

新增測試：`tests/test_fix_legacy_announcement_titles.py`（`plan_fix()`
規劃邏輯）、`tests/test_platform_announcements.py`
（`UpdateAnnouncementTitleTests`）。全部測試（`python3 -m unittest
discover -s tests -p "test_*.py"`）1856 個全數通過。

## 人資專區：每日加退保彙總（2026-09-22 新增）

> **⚠️ 2026-09-22 當天稍晚更新**：下面「權限模型」這節描述的「7 個部門
> 也要勾人資專區模組權限才看得到」已經不是目前的設計——使用者事後確認
> 溝通有落差，改成 7 個部門不用勾模組權限，直接在 `/portal` 首頁看到
> 自己部門的卡片。完整說明見本檔案後面的
> 「人資專區：每日加退保拆成部門卡片＋人資彙總兩種入口（2026-09-22 修正）」
> 這節，這裡的內容留著當作原始設計脈絡的紀錄，操作步驟請直接看新的那節。

各部門（台北所(派遣組)/台北所(國際組)/新北所(派遣組)/新北所(配送組)/
桃園所/台中所/高雄所）每天要傳加退保 Excel 給人資，人資收到後統整成
一份彙總資料再作業。新增 `/hr/insurance`：這 7 個部門上傳自己的加退保
Excel，人資（部門欄位設定成「人資部門」的帳號）彙整、收單、下載成一份
總表。跟使用者在對話中逐項討論確認的規劃，重點摘要如下（完整討論過程
見這次 PR 的對話紀錄，不重複列在這裡）：

### 權限模型

**不是走 hr 模組原本的 admin/staff 兩層**（`hr/auth.py` 那套跟公司職級
掛勾的判斷），改成比照 `services/contract_summary_service.py` 的作法，
直接看帳號的 `department` 字串（`hr/insurance_repository.py`）：

- `hr.config.INSURANCE_UPLOAD_DEPARTMENTS`（7 個部門字串，跟
  `scripts/seed_departments.py` 建立的部門主檔名稱要完全一致）的帳號可以
  上傳/查自己部門的歷史紀錄。
- 部門是 `hr.config.INSURANCE_COLLECTOR_DEPARTMENT`（字串「人資部門」）
  的帳號，或全平台管理員，是「人資」身份，可以看全部歷史、收單、下載。
- 兩種身份都不是的 hr 模組帳號，`/hr/insurance` 系列頁面一律導回
  `/hr/`，首頁也不會出現「每日加退保」卡片。

### 上傳 / 收單 / 歷史查詢

- 上傳只存檔案本身（沿用 `hr/storage.py` 既有的 GCS 上傳機制，blob 路徑
  前綴 `hr/insurance/`），**不解析 Excel 內容存進 Firestore**——跟
  `hr/repository.py` 其他彙整功能同一種做法，下載彙總表時才即時讀取
  每個部門的原始檔案組表（見下面「下載彙總表」）。
- 同一個部門、同一天重複上傳＝直接覆蓋，不比對 Excel 內容裡的資料列
  （2026-09-22 使用者明確選擇的簡化做法）——Firestore 文件 id 固定是
  「日期__部門」，重傳就是覆寫同一筆文件，舊檔案在 GCS 上會變成孤兒
  （不影響功能，不主動清理）。
- 人資收單（`hr_insurance_day_locks` 一天一筆）只鎖住那 7 個部門，人資
  自己不受限，收單後仍可以補傳/覆蓋任一部門那天的資料。
- 部門同仁只能查自己部門的歷史（`/hr/insurance/history`）；人資可以看
  全部部門、全部日期，並依部門/日期區間篩選。

### 下載彙總表（`hr/insurance_excel.py`）

人資在 `/hr/insurance/download` 選一個日期區間（選同一天就是只下載那
天），系統即時讀取區間內各部門已上傳的原始檔案（固定 11 欄格式：編號/
廠商/班別/姓名/身分證/勞保加保日期/勞保退保日期/勞保追退日期/健保加保
月份/眷屬健保/備註），攤平成使用者原本「全區域加退保紀錄表」格式的一份
Excel（編號/投保單位/廠商/部門店家/姓名/身分證字號/投保日/出生年月日/
勞退追退日期/班次級距/備註/招募人員）：

- **投保單位／出生年月日／班次級距／招募人員這四欄，系統完全沒有資料
  來源**（討論時盤點過整個 codebase，`platform_companies.py` 公司主檔
  沒有「廠商→保單位」的對照，也沒有任何地方存同仁的出生年月日或招募人
  員），2026-09-22 使用者確認直接留空，人資下載後自己手動補上，**不是
  漏做**。「部門/店家」這欄來源檔案完全沒有對應資料，改填「這份資料是
  哪個部門傳的」（上傳時的部門名稱），是目前系統唯一有意義可填的資訊。
- 「投保日」是唯一能自動組出來的欄位：把「勞保加保日期」「勞保退保
  日期」轉成民國格式接起來（例：「115.09.19當天加退」／「…加保」／
  「…退保」），跟使用者原本範例檔案的寫法一致；日期解析不出來的話原封
  不動把原始文字接起來，不會整欄空白。
- 某個部門的檔案讀不到（GCS 找不到、格式解析失敗）就跳過那個部門，不會
  讓整份彙總表下載失敗。
- 防 Excel 公式注入沿用 `services/contract_summary_excel.py` 同一套
  防呆邏輯（自由文字開頭是 `=`/`+`/`-`/`@` 的補前導單引號）。

**這次範圍不含** UC加退保／蝦皮假日班加退保／材霈_離店與實習通報／
E-learning 這幾種檔案（2026-09-22 使用者確認過，之後有需要再另外處理，
目前這幾種檔案完全不能上傳到這個功能）。

### 使用者需要知道的事——這次需要手動設定一件事

**新增「人資部門」這個部門**，才能指定誰是「人資」身份：

1. 用全平台管理員帳號登入，到 `/departments` 新增一個部門，名稱打
   `人資部門`（要跟這四個字完全一樣，多一個空格或打錯字都不會被系統
   認得）。
2. 到 `/accounts`，把負責彙整加退保的人資同仁帳號的「部門」欄位改選成
   剛新增的「人資部門」，記得該帳號要有「人資專區」模組的權限，才能
   登入 `/hr/` 看到「每日加退保」卡片。
3. 完成後，那位同仁登入人資專區首頁就會看到「每日加退保」卡片，點進去
   會直接進「每日加退彙總」頁面（收單/下載/查全部歷史都在這裡）；7 個
   部門原本負責上傳資料的同仁，只要帳號的「部門」欄位本來就是那 7 個
   名稱之一，不用另外設定，登入人資專區就會看到「每日加退保」卡片，
   點進去是上傳表單。
4. 不用額外設定環境變數、不用重新部署（部署會在合併這個 PR 到 main 後
   自動觸發，跟平常一樣）。

新增 `hr/insurance_repository.py`（權限判斷/Firestore CRUD）、
`hr/insurance_excel.py`（讀取部門檔案/組彙總表 Excel）、
`hr/routes/insurance_routes.py`、四份新樣板
（`insurance_upload.html`/`insurance_history.html`/
`insurance_summary.html`/`insurance_download.html`），`hr/db.py` 新增
`hr_insurance_uploads`/`hr_insurance_day_locks` 兩個 collection，
`hr/config.py` 新增 `INSURANCE_UPLOAD_DEPARTMENTS`/
`INSURANCE_COLLECTOR_DEPARTMENT`，`hr/app.py` 掛載新路由，
`hr/templates/home.html`／`help.html` 補上入口跟使用說明。

新增測試：`tests/test_hr_insurance.py`（權限判斷、Firestore CRUD、日期
民國格式轉換、Excel 讀取/攤平組表的完整涵蓋）、`tests/test_hr_routes.py`
（新路由未登入一律導去登入頁的 smoke test）。全部測試（`python3 -m
unittest discover -s tests -p "test_*.py"`）1921 個全數通過。

## 桃園所專區重構成「多所派遣媒合」＋新增高雄所（2026-09-22）

使用者要把桃園所的報班功能整套複製一份給高雄所（之後還會陸續開其他
所），討論後選擇不是單純複製貼上改名字（那樣以後每個所都要各自改一次
程式碼，容易漏改），而是把原本寫死「桃園所」的 `taoyuan_dispatch_*`
系列檔案，重構成所有所共用同一套程式碼＋同一份 Firestore 資料表，每筆
資料多存一個 `site` 欄位分所。**重構當下桃園所這個功能還沒有任何真實
資料在用**（人員/地點/LINE 綁定都是空的），所以直接把 Firestore 資料表
跟網址都改名，不需要寫遷移腳本、不影響任何正式資料。

### 新架構

- **`dispatch_sites.py`**（新增）：所有所別的設定清單（所別代碼、中文
  名稱、部門權限字串、LINE Channel Token/Secret 的環境變數名稱），先放
  `taoyuan`（桃園所）、`kaohsiung`（高雄所）兩筆。**之後要再開新所，只
  要在這裡加一筆設定＋申請一組新 LINE 官方帳號並設定對應環境變數，不用
  改任何程式碼。**
- **`services/dispatch_service.py`**（取代 `services/taoyuan_dispatch_service.py`）：
  人員/地點/LINE 綁定/需求時段/報名的 CRUD，所有函式第一個參數都是
  `site`（所別代碼）。Firestore 資料表改名：`taoyuan_dispatch_personnel`
  → `dispatch_personnel`，`taoyuan_dispatch_locations` →
  `dispatch_locations`，新增 `dispatch_line_bindings`（原
  `taoyuan_dispatch_line_bindings`）、`dispatch_postings`（原
  `taoyuan_dispatch_postings`）、`dispatch_registrations`（原
  `taoyuan_dispatch_registrations`），每筆文件都多存一個 `site` 欄位。
  **跨所資料隔離**：用文件 ID 直接查一筆的函式（`get_personnel()` 等）
  都會檢查該筆資料的 `site` 欄位是否跟呼叫端要求的所別一致，不一致視同
  查無資料——避免「猜到／記錯另一個所的文件 ID」意外讀到別所資料。LINE
  綁定的文件 ID 額外把所別代碼併進去（`{site}__{line_user_id}`），因為
  LINE 的 `user_id` 少數情況下可能跨不同 Channel 重複。
- **`dispatch_line.py`**（取代 `taoyuan_dispatch_line.py`）：依
  `dispatch_sites.py` 的環境變數名稱，各自建立每個所獨立的
  LineBotApi/WebhookHandler 實例並快取。
- **`dispatch_bot.py`**（取代 `taoyuan_dispatch_bot.py`）：`handle_message(site, ...)`
  多一個 `site` 參數決定讀寫哪個所的資料，指令格式/流程所有所完全共用。
- **`dispatch_webhook_routes.py`**（取代 `taoyuan_dispatch_webhook_routes.py`）：
  網址改成 `POST /dispatch/{site}/line/callback`，對每個有設定好環境
  變數的所各自註冊一次 LINE 事件處理函式（`WebhookHandler` 的
  `@handler.add()` 是綁在特定實例上的，不能只註冊一次共用）。
- **`dispatch_routes.py`**（取代 `taoyuan_dispatch_routes.py`）：後台頁面
  網址改成 `/dispatch/{site}/...`（例如 `/dispatch/taoyuan/personnel`、
  `/dispatch/kaohsiung/personnel`），權限判斷 `has_dispatch_access(account, site)`
  一樣是「帳號部門＝該所部門名稱，或全平台管理員」。
- **樣板**：`templates/taoyuan_dispatch_*.html` 改名成
  `templates/dispatch_*.html`，畫面上的「桃園所」文字改成依所別動態帶入
  的 `{{ site_name }}`，連結改成 `/dispatch/{{ site }}/...`。
- **`portal_routes.py`**：原本只判斷桃園所加一張卡片，改成迴圈跑過
  `dispatch_sites.list_sites()`，帳號的部門符合哪個所就加那張卡片（全
  平台管理員因此每個所都會看到自己的卡片）。
- **`config.py`**：拿掉原本寫死的 `TAOYUAN_DISPATCH_LINE_CHANNEL_ACCESS_TOKEN`
  /`_SECRET` 兩個常數，改成 `dispatch_line.py` 直接依
  `dispatch_sites.py` 登記的環境變數名稱用 `os.getenv()` 讀取。

### 業務邏輯完全沒變

報名資格 4 種類別（理貨/作業員/餐飲有體檢/餐飲無體檢）、LINE 綁定指令
格式（「綁定+姓名+電話」）、報名/審核/推播流程，所有所完全共用同一套，
不分所別客製——2026-09-22 使用者明確確認，這次只是把「這是哪個所」抽
成參數，沒有改任何業務規則。

### 使用者需要知道的事——這次要幫高雄所申請一組新的 LINE 官方帳號

跟當初桃園所上線的流程完全一樣（見上面「桃園所專區：Phase 2」那節的
詳細步驟），差別只是這次是幫高雄所：

1. 申請一組新的 LINE 官方帳號（Messaging API），跟桃園所那組用同一個
   Provider 即可，記下 Channel Access Token、Channel Secret。
2. 到 Cloud Run 服務 `recruitment-bot`（GCP 專案 `tsaipei-505807`，
   region `asia-east1`）設定兩個新環境變數：
   ```bash
   gcloud run services update recruitment-bot \
     --region asia-east1 \
     --update-env-vars KAOHSIUNG_DISPATCH_LINE_CHANNEL_ACCESS_TOKEN="貼上你的CHANNEL_ACCESS_TOKEN",KAOHSIUNG_DISPATCH_LINE_CHANNEL_SECRET="貼上你的CHANNEL_SECRET"
   ```
   這一步在做什麼：把高雄所這組 LINE 帳號的金鑰交給 Cloud Run 上跑的
   服務。指令執行完會自動觸發一次新的部署，跑完最後一行會顯示 `URL:`
   開頭的服務網址，代表部署成功。
3. 回到 LINE Developers Console，把高雄所這組帳號的 Webhook URL 設定成：
   ```
   https://recruitment-bot-412901869672.asia-east1.run.app/dispatch/kaohsiung/line/callback
   ```
   貼上後按「Verify」按鈕測試，應該會顯示成功（綠勾勾）。
4. **桃園所原本設定的環境變數名稱沒有變**
   （`TAOYUAN_DISPATCH_LINE_CHANNEL_ACCESS_TOKEN`/`_SECRET`），已經設定
   過的話不用重新設定；但桃園所的 LINE webhook 網址如果之前已經在 LINE
   Developers Console 設定過，**因為網址從 `/taoyuan-dispatch/line/callback`
   改成 `/dispatch/taoyuan/line/callback`，需要回去 LINE Developers
   Console 重新設定一次桃園所這組帳號的 Webhook URL**，不然桃園所之後
   上線 LINE 綁定功能時會收不到訊息。桃園所這次重構前都還沒有真的申請
   LINE 帳號在用，所以這一步現在做也不影響任何人。
5. 高雄所的人員管理/地點管理/需求時段管理網頁功能（`/dispatch/kaohsiung/...`）
   不需要等 LINE 帳號申請好就能先用；只有「LINE 綁定＋人員在 LINE 上
   查詢/報名＋審核推播」這幾項需要等上面 1-3 步驟做完才會動起來。

**進度（2026-09-22 使用者回報，兩所都已完成，Verify 都成功）**：

- **桃園所**：一開始 Webhook 收到 LINE 打過來的請求回 404，查 Cloud Run
  的 HTTP 請求 log（`gcloud logging read
  'resource.type="cloud_run_revision" resource.labels.service_name=
  "recruitment-bot" httpRequest.requestUrl:"dispatch"' --limit 20
  --format="table(timestamp,httpRequest.requestMethod,httpRequest.status,
  httpRequest.requestUrl)"`）才發現 LINE 實際打的是這次重構前的舊網址
  `/taoyuan-dispatch/line/callback`——使用者是在重構部署上線「之前」就
  設定過 Webhook URL，重構上線後舊網址失效，改成新網址
  `/dispatch/taoyuan/line/callback` 後 Verify 成功。**這是本來就預期
  會發生的過渡期問題，不是這次的新 bug**，只是提醒之後如果還有沒切換
  過網址的舊設定，同樣的排查方式（查 HTTP 請求 log）可以很快定位。
- **高雄所**：Webhook 一直回 503，查 Cloud Run 環境變數（`gcloud run
  services describe recruitment-bot --region asia-east1
  --format="value(spec.template.spec.containers[0].env)" | tr ';' '\n'
  | grep KAOHSIUNG`）發現 `KAOHSIUNG_DISPATCH_LINE_CHANNEL_ACCESS_TOKEN`
  有設定到，但 `KAOHSIUNG_DISPATCH_LINE_CHANNEL_SECRET` 完全沒設定
  進去（第一次跑 `gcloud run services update --update-env-vars` 時兩個
  變數用逗號隔開，猜測是逗號或引號哪裡沒跑對，只有第一個生效）。補上
  `KAOHSIUNG_DISPATCH_LINE_CHANNEL_SECRET` 後 Verify 成功。**這裡有個
  值得記住的排查方式**：503（`dispatch_line.py` 的
  `WebhookHandler(_secret) if _secret else None`）是靠 secret 建立的，
  跟 access token 有沒有設定無關，所以下次遇到「LINE 綁定/webhook 一直
  503」，第一步就是查 `_SECRET` 這個環境變數有沒有真的設定到，不是查
  access token。

新增測試：`tests/test_dispatch_sites.py`（所別設定清單）、
`tests/test_dispatch_service.py`（取代
`tests/test_taoyuan_dispatch_service.py`，新增跨所資料隔離測試）、
`tests/test_dispatch_bot.py`（取代 `tests/test_taoyuan_dispatch_bot.py`）、
`tests/test_dispatch_webhook_routes.py`（取代
`tests/test_taoyuan_dispatch_webhook_routes.py`，新增未知所別回 503 的
測試）、`tests/test_dispatch_routes.py`（取代
`tests/test_taoyuan_dispatch_routes.py`，新增跨所隔離測試）、
`tests/test_portal.py` 的 `PortalHomeDispatchSiteCardTests`（原
`PortalHomeTaoyuanDispatchCardTests`，新增高雄所卡片測試）。全部測試
（`python3 -m unittest discover -s tests -p "test_*.py"`）1945 個全數
通過。

## 新增 `job-portal-gas-project` repo：「職缺維護系統」GAS 程式碼正式接上版控（2026-09-22）

使用者反映薪資補款單已核准、但同仁沒收到通知信，追查後發現：「職缺
維護系統」背後那支 Google Apps Script（`Project_Salary.gs` 等檔案）**一
直沒有對應的 git repo**，只存在 Apps Script 編輯器裡，Claude 完全看不到
程式碼、也沒辦法幫忙除錯，每次都要使用者手動貼程式碼片段出來才能討論。

**這件事跟「要不要把 GAS 邏輯整個搬進這個 repo（方案 B）」是兩件獨立的
事**——接上版控只是讓 Claude 能直接讀/改現有 GAS 程式碼，不代表決定要
把邏輯搬過來重寫，兩者可以分開推進。

使用者比照 `delivery-gas-project` repo 當初的做法，用 `clasp` 把現有
Apps Script 專案的程式碼抓下來、推上新建的
`tsaipei-linebot/job-portal-gas-project` repo（`clasp clone` 直接拉現有
專案，不是重新建立一個新的 GAS 專案）。這個 repo 底下有
`程式碼.js`（主程式：`CONFIG`、`doGet`/`doPost` 路由、同仁 LINE 帳號
綁定）、`Project_Salary.js`（薪資補款）、`Project_Job.js`（職缺維護）、
`Project_BatchEnhance.js`（批次 AI 潤飾）、`ProjectWorkflowService.js`
（專案合約）。

新增 `.github/workflows/clasp-push.yml`，跟 `delivery-gas-project` 同一套
做法（合併到 `main` 後自動 `clasp push`）。**這次還沒確認這個 Apps
Script 專案的 Web App 部署是「@HEAD」還是「固定版本」**——
`delivery-gas-project` 當初踩過「固定版本部署，光 push 不會生效，要另外
`clasp deploy -i <deployment id>`」這個雷（見上面「CI/CD 自動部署」章節
的踩雷記錄），這次尚未確認，所以 workflow 目前只有 `clasp push --force`
這一步。使用者需要在有登入這個 Apps Script 專案權限的環境執行
`clasp deployments` 確認，如果看到固定版本的部署（deployment id 開頭
`AKfycb...`，不是 `@HEAD`），要回來請 Claude 補上 `clasp deploy -i ...`
那一步，不然以後改完程式碼、CI 顯示綠色勾勾，但正式環境其實沒有真的
更新，會很難排查。

**使用者需要手動處理的事（讓自動部署真的能運作）**：
```bash
npm install -g @google/clasp   # 如果 Cloud Shell 還沒裝過
clasp login --no-localhost
```
用平常登入這個 Apps Script 專案的 Google 帳號完成授權後：
```bash
cat ~/.clasprc.json | base64 -w 0
```
把印出來的內容複製，到
`https://github.com/tsaipei-linebot/job-portal-gas-project/settings/secrets/actions`
新增一個叫 `CLASPRC_JSON` 的 repository secret，貼上剛剛的內容——步驟
細節跟 `delivery-gas-project` 當初的設定完全一樣，見上面「CI/CD 自動
部署」章節。

## 修正薪資補款「已核准但沒收到信」+ 新增「補寄信」＋「財務部專區」（2026-09-22）

### 根本原因：GAS 端寄信不誠實

`job-portal-gas-project` 的 `Project_Salary.js`，主管在 LINE 按下「核准」
後，原本不管 `EmailService.sendSalaryCompensationReport()` 這一步實際
成功還是失敗，回覆給主管/申請人/其他主管的三則 LINE 訊息**永遠**都說
「已自動寄出」——寄信失敗（例如財會/主管/申請人信箱湊不出一個有效
email、`GmailApp.sendEmail()` 本身拋例外，例如 Gmail 每日寄信額度用完）
只會印進 `console.error`/`console.warn`，只有主動去 Apps Script 編輯器
「執行項目」才看得到，沒有人會知道要去查。

**修正**：`EmailService.sendSalaryCompensationReport()` 改成回傳
`{success, message, recipients}`，`SalaryWorkflowService.
handleSalaryPostback()` 三則 LINE 訊息都照實反映真正結果，寄信失敗時
提示同仁可以用材霈平台的「補寄信」功能重試。

### 新增「補寄信」按鈕（/me 薪資補款紀錄）

- `job-portal-gas-project` 新增 `doPost` 分支 `RESEND_SALARY_EMAIL`：帶
  `salary_id`，重新抓這筆紀錄（要是「已核准」狀態才會真的補寄，其他
  狀態直接回錯誤訊息）、再呼叫一次 `sendSalaryCompensationReport()`。
- `tsaipeilinebot` 的 `/me` 薪資補款紀錄表格，每筆「審核狀態＝已核准」
  的紀錄旁邊多一顆「補寄信」按鈕，按下去先跳瀏覽器確認視窗防呆
  （`onsubmit="return confirm(...)"`，跟桃園所/高雄所收單按鈕同一種
  做法）。權限沿用「申請人本人或其主管才看得到這筆紀錄」的既有範圍
  （`services/salary_repayment_service.get_my_repayment_records()`）——
  `me_routes.resend_salary_repayment_email_submit()` 在伺服器端再次確認
  這個帳號看不看得到這筆紀錄才會真的呼叫 GAS，不是只靠前端不顯示按鈕，
  避免有人直接組網址繞過。

### 新增「財務部專區」（/finance）

- 權限：帳號部門＝財務部（或全平台管理員），跟人資部門/桃園所/高雄所
  同一套「部門字串比對」做法（見
  `services/salary_repayment_service.has_finance_access()`）；「財務部」
  這個部門名稱本來就在 `scripts/seed_departments.py` 的既有清單裡，
  不用另外新增部門，指派帳號到 `/accounts` 把部門改成「財務部」即可。
- 頁面顯示**所有**「已核准」的薪資補款紀錄（不分申請人/主管，
  `services/salary_repayment_service.get_all_approved_repayment_records()`）
  ——「已退回」的紀錄 GAS 那邊本來就會直接刪除、不會留在試算表；
  「尚未審核」的財務不需要看到，只看確定要撥款的，2026-09-22 使用者
  明確確認。
- 選起訖日期區間（依「申請日」欄位篩選——唯一保證每筆都有填的日期
  欄位，付款日期是選填），按下載，`job-portal-gas-project` 新增
  `doPost` 分支 `EXPORT_SALARY_PDFS`：把區間內所有已核准紀錄各自用
  `EmailService.buildSalaryPdfBlob()`（**跟核准信附件同一份排版邏輯**，
  沒有另外重刻一份）產生 PDF，全部用 `Utilities.zip()` 打包成一個 ZIP、
  base64 編碼後回傳；`tsaipeilinebot` 解碼後直接當附件回給瀏覽器下載，
  使用者點一次拿到一個 ZIP，裡面每筆各自一份 PDF。

### 新增的內部端點都要驗證共用密鑰

`RESEND_SALARY_EMAIL`／`EXPORT_SALARY_PDFS` 這兩個新端點會動到既有資料
（重寄信、批次讀取全部已核准紀錄），不是像 `SUBMIT_SALARY` 那種公開
表單（只靠蜜罐擋機器人）——沿用 GAS 既有的 `ADMIN_API_SECRET`（原本只
保護 `doGet` 管理端查詢），新增 `isValidAdminApiSecretFromBody()` 給
`doPost` 用（密鑰放在 JSON body 的 `admin_secret` 欄位，不是網址參數）。

**使用者需要手動處理的事**：
1. 到 `job-portal-gas-project` 這個 Apps Script 專案的「指令碼屬性」
   確認已經有 `ADMIN_API_SECRET`（如果之前 `doGet` 管理端查詢功能就有
   在用，應該已經設定過；沒有的話新增一個，值是一段不外流的隨機亂碼
   字串）。
2. 到 `tsaipeilinebot` 的 Cloud Run 服務 `recruitment-bot` 新增環境變數
   `JOB_PORTAL_ADMIN_API_SECRET`，**值要跟上面 `ADMIN_API_SECRET` 完全
   一樣**：
   ```bash
   gcloud run services update recruitment-bot \
     --region asia-east1 \
     --update-env-vars JOB_PORTAL_ADMIN_API_SECRET="貼上跟 ADMIN_API_SECRET 一樣的值"
   ```
3. 到 `/departments`（全平台管理員帳號）確認「財務部」這個部門存在
   （應該已經在，`scripts/seed_departments.py` 原始清單就有），到
   `/accounts` 把負責的財務同仁帳號部門改成「財務部」。
4. 合併 `job-portal-gas-project` 的 PR 後，如果自動部署（`clasp push`）
   還沒設定好（見上一節），要先手動 `clasp push` 一次，不然新增的兩個
   端點不會真的生效。

**（2026-09-22 使用者回報財務部專區這幾步都已完成，功能可以正常使用；
`CLASPRC_JSON`／`clasp deploy -i` 固定版本部署的問題也已經在後面
「clasp-push 補上 clasp deploy -i」那節修好並驗證過。）**

新增測試：`tests/test_salary_repayment_service.py` 的
`HasFinanceAccessTests`／`GetAllApprovedRepaymentRecordsTests`、
`tests/test_salary_repayment_submit_service.py` 的
`ResendSalaryRepaymentEmailTests`／`ExportApprovedSalaryPdfsZipTests`、
`tests/test_me_routes.py` 的 `ResendSalaryRepaymentEmailSubmitTests`
（含伺服器端權限二次確認的測試）、新增 `tests/test_finance_routes.py`、
`tests/test_portal.py` 的 `PortalHomeFinanceCardTests`。全部測試
（`python3 -m unittest discover -s tests -p "test_*.py"`）1977 個全數
通過。

## 人資專區：每日加退保拆成部門卡片＋人資彙總兩種入口（2026-09-22 修正）

### 問題：使用者反映「沒有產出所有部門的卡片」

使用者回報加退保彙總「剛才沒有產出所有部門的卡片」。追下去發現不是
程式邏輯的 bug（`hr/insurance_repository.summary_for_date()` 本來就固定
列出全部 7 個部門，這段沒問題），而是原本設計理解有落差：

- 使用者原本的想法是「7 個部門各自有自己的卡片，同仁登入後照自己帳號
  的部門看到自己那張，點進去直接做加退保」，「人資專區」則只是給人資
  收彙總資料用。
- 但當天稍早實作的版本是「7 個部門的同仁都要先在 `/accounts` 把帳號
  勾選『人資專區』這個模組權限，才會在 `/portal` 看到『人資專區』卡片，
  點進去、再點『前往每日加退保』才到得了上傳頁」——變成部門同仁要先有
  一個「人資專區」的權限才能做自己部門的加退保，而且會連帶看得到意外
  通報/體檢報告這些人資才該看的功能。
- 使用者當時只幫 3 個部門的帳號勾了「人資專區」模組權限，另外 4 個
  部門的帳號完全看不到卡片，也進不去——這才是「沒有產出所有部門的
  卡片」的實際原因，不是程式邏輯漏掉部門。

跟使用者確認後，照原本的想法重新設計權限模型，改成下面這樣。

### 新的權限模型：兩種入口分開

- **7 個上傳部門的同仁**：不用再勾「人資專區」模組權限。`/portal` 首頁
  現在會依帳號的 `department` 字串，直接顯示一張「{部門名稱} 加退保」
  的卡片（跟桃園所/高雄所/財務部專區同一種「不掛模組、照部門字串判斷」
  的做法），點進去直接是 `/hr/insurance/upload` 上傳頁，不會經過「人資
  專區」的登入頁或首頁，也看不到意外通報/體檢報告等其他人資功能。
  - 這張卡片刻意**不給全平台管理員例外顯示**（跟桃園所/高雄所/財務部
    卡片不一樣）——因為 `/hr/insurance/upload` 是直接讀帳號自己的
    `department` 決定要看哪個部門，不像 `/dispatch/{所別代碼}` 網址本身
    帶了所別，管理員自己的部門通常是空的，硬顯示 7 張卡片只會變成點
    了進不去的空卡片。管理員本來就能從「人資專區」→「每日加退彙總」
    看到全部部門的彙整資料。
- **人資（或全平台管理員）**：維持原樣，從「人資專區」（`hr` 模組，
  需要勾模組權限）→「每日加退保」卡片，進去彙總／收單／下載，這幾個
  功能本來就是人資同仁在用的，人資帳號本來就需要「人資專區」模組權限
  才能用意外通報等其他功能，這裡沿用同一個權限沒有額外負擔。

### 程式碼層面：`hr/routes/insurance_routes.py` 拆成兩種登入依賴

- 新增 `_require_login()`：只檢查有沒有登入，不檢查「人資專區」模組
  權限，沒登入導去根層級 `/login?next=<原本要去的網址>`（不是
  `/hr/login`）——套用在「首頁自動導向」`/insurance`、「上傳」
  `/insurance/upload`（GET/POST）、「查歷史」`/insurance/history` 這三組
  路由。實際看不看得到資料，交給 `repo.can_upload()`／`is_collector()`
  這層部門字串判斷（`_access_redirect()` 判斷失敗時改導回 `/portal`，
  原本是導回 `/hr/`——因為現在打這幾支路由的同仁不一定有人資模組
  權限，導去 `/hr/` 反而會再被模組權限那層擋一次）。
- 「彙總」`/insurance/summary`、「收單」`/insurance/summary/close`、
  「下載」`/insurance/download`（GET/POST）維持原本的 `login_required`
  （人資專區模組權限）不變，這幾頁本來就只給人資用。
- 連帶修正 `hr/routes/file_routes.py`：加退保檔案下載（`/hr/files/...`，
  blob path 前綴 `hr/insurance/`）原本也是走 `login_required`，同仁
  上傳成功後點「下載目前這份檔案」或查歷史點「下載」會因為沒有人資
  模組權限而進不去——改成加退保檔案改用 `has_insurance_access()` 判斷
  （沒登入導去根層級登入頁；帳號沒有加退保存取權回 404），其他 hr 子
  功能（體檢報告/關懷紀錄/證照/教育訓練）的檔案維持原本 `login_required`
  不變。
- `hr/templates/insurance_upload.html` 的「回主頁」連結原本連去 `/hr/`
  （同樣會被模組權限擋下來），改成 `/portal`，跟財務部專區/桃園所專區
  等頁面的「回主頁」連結一致。

### `/portal` 卡片（`portal_routes.py`）

新增：`if insurance_repo.can_upload(account):` 才加一張卡片，帳號的
`department` 只會對到 0 或 1 個上傳部門，所以最多一張，不用像多所派遣
媒合那樣跑迴圈；卡片文字「{部門} 加退保」、連去 `/hr/insurance/upload`。

### 使用者需要做的事

**這次不需要額外的手動設定**——原本已經勾了「人資專區」模組權限的 3
個部門帳號不受影響（可以繼續用，也可以之後把那個勾選拿掉，不影響加退
保功能，只是他們會少看到其他人資功能）；另外 4 個部門的帳號現在**不用
再勾任何模組權限**，同仁登入後直接在 `/portal` 首頁就會看到自己部門的
「{部門} 加退保」卡片，前提是帳號的「部門」欄位本來就要是那 7 個名稱
之一（跟原本的設定一樣，沒有變動）。不用改環境變數，部署會在合併這個
PR 到 main 後自動觸發。

新增測試：`tests/test_hr_routes.py` 新增
`InsuranceRequireLoginDependencyTests`／`InsuranceAccessRedirectTests`／
`UploadPageAccessTests`／`FileRouteAccessDependencyTests`，`tests/
test_portal.py` 新增 `PortalHomeInsuranceCardTests`（含「部門同仁看得到
但全平台管理員不會看到 7 張卡片」這個刻意的行為差異）。全部測試
（`python3 -m unittest discover -s tests -p "test_*.py"`）1993 個全數
通過。

## 加退保 7 個部門卡片統一命名＋併入既有系統＋部門字串正規化（2026-09-22 再修正）

上一節上線後，使用者提出更完整的設計：7 個上傳部門應該各自有一張
「{部門}專區」卡片（不是「{部門} 加退保」），已經有其他系統的部門
（新北所(配送組)/桃園所/高雄所）不要重複顯示兩張卡片，加退保應該併進
那個系統自己的首頁當一個按鈕。同時使用者主動問到「部門名稱的括號全形
半形會不會影響權限讀取」，確認會之後決定一起修。

### 1. 部門字串比對正規化（`platform_accounts.normalize_department()`）

新增 `normalize_department()`：比對前把全形括號「（）」轉成半形
「()」，只轉括號、不動其他字元。原因：這個平台已經有 5 種功能是靠
`account.get("department")` 完全字串比對來判斷權限（加退保、財務部
專區、桃園所/高雄所專區、契約彙總的服務部門比對、這次新增的配送部
部門存取），只要 `/departments` 部門主檔或帳號的部門欄位當初打字時
全形半形習慣不一致，畫面上會直接看起來像「沒有權限」，沒有任何錯誤
訊息，同仁自己完全抓不出來。套用的地方：`hr/insurance_repository.py`
（`can_upload`／`is_collector`）、`services/dispatch_service.py`
（`has_dispatch_access`）、`dispatch_sites.py`（`site_for_department`）、
`services/salary_repayment_service.py`（`has_finance_access`）、
`services/contract_summary_service.py`（三支服務部門比對函式）、
`delivery/auth.py`（新的 `has_delivery_access`，見下面）。

### 2. 配送部（`delivery` 模組）改成「模組打勾 or 部門字串」

`delivery/auth.py` 新增 `has_delivery_access(account)`：帳號有勾
「新北所(配送組)專區」模組**或**帳號的部門就是「新北所(配送組)」，
符合其一即可，`login_required` 改用這個判斷（不再只認模組打勾）。跟
加退保/財務部/桃園所/高雄所同一套道理——部門本身就是配送部的同仁，
不該還要額外去 `/accounts` 勾模組權限才進得去自己部門的系統。**這是
這幾個「部門字串比對」的功能裡，唯一一個實際放寬既有模組存取範圍的
改動**（其他都是新功能，這個是把舊模組多開一個入口），已經勾模組
權限的既有帳號完全不受影響，都算 staff 角色（`module_role()` 對只靠
部門字串進來的帳號回傳 None，`current_user()` 原本的 fallback 就是
staff）。

### 3. `/portal` 卡片重新設計

- **台北所(派遣組)／台北所(國際組)／新北所(派遣組)／台中所**：沒有
  其他系統，卡片名稱從「{部門} 加退保」改成「{部門}專區」，點進去
  還是直接到 `/hr/insurance/upload`。
- **新北所(配送組)**：不再顯示獨立卡片，`platform_accounts.MODULES`
  的 `delivery` 顯示名稱從「新北所(配送組)系統」改成「新北所(配送組)
  專區」（`/accounts` 模組勾選欄位的名稱也會一起變，同一個地方定義），
  卡片顯示條件從純模組打勾改成 `has_delivery_access()`；配送部系統
  首頁（`delivery/templates/home.html`）新增「每日加退保」功能區塊，
  顯示條件是 `hr.insurance_repository.can_upload(user)`。
- **桃園所／高雄所**：卡片名稱本來就是「{所名}專區」不用改；各自的
  派遣媒合專區首頁（`templates/dispatch_home.html`）新增「每日加退保」
  功能區塊，顯示條件同上。
- `portal_routes.py` 新增 `_INSURANCE_MERGED_ELSEWHERE_DEPARTMENTS`
  （正規化過的新北所(配送組)/桃園所/高雄所），獨立的加退保卡片只給
  「能上傳加退保、但部門不在這份清單裡」的帳號顯示，避免同一個部門
  的同仁畫面上出現兩張功能重疊的卡片。**這張獨立卡片維持不給全平台
  管理員例外顯示**——原因同上一節：`/hr/insurance/upload` 是讀帳號
  自己的 `department` 決定內容，管理員自己部門通常是空的，硬顯示只會
  變成點不進去的卡片。

### 使用者需要做的事

**不需要額外手動設定**——這次全部是程式邏輯調整，部門主檔
（`/departments`）跟帳號的部門欄位維持原樣不用改；如果之前發現過
`/departments` 或某些舊帳號的部門名稱括號打法不一致，這次上線後也
不用特地去修正，程式碼會自動正規化比對。合併後會自動部署，不用手動
跑指令。

新增/調整測試：`tests/test_platform_accounts.py` 新增
`NormalizeDepartmentTests`，`tests/test_hr_insurance.py`／
`tests/test_contract_summary_service.py` 各補一個全形括號比對測試，
`tests/test_delivery_routes.py` 新增 `HasDeliveryAccessTests`／
`DeliveryLoginRequiredDependencyTests`，`tests/test_delivery_home_routes.py`
新增 `HomeInsurancePanelTests`，`tests/test_dispatch_routes.py` 新增
`DispatchHomeInsurancePanelTests`，`tests/test_portal.py` 的
`PortalHomeInsuranceCardTests` 整個改寫成對應新的卡片命名跟併卡邏輯，
`tests/test_report_accounts_for_rank_setup.py`／`tests/test_portal.py`
既有測試跟著 `delivery` 模組顯示名稱改名同步更新。全部測試（`python3
-m unittest discover -s tests -p "test_*.py"`）2016 個全數通過。

## 補齊財務部專區／派遣媒合專區／加退保的使用說明頁（2026-09-22）

使用者發現這幾天新增的功能（財務部專區、桃園所/高雄所派遣媒合、加退保
部門卡片）都沒有「使用說明」入口——這幾個都是照財務部/桃園所/高雄所/
加退保「不掛模組、照部門判斷」那套做法新增的卡片，`help_href` 一直是
空字串，跟其他 8 個掛在 `platform_accounts.MODULES` 的模組（一開始就有
`help_href` 這個欄位設計）比起來是明顯的落差。

### 新增的使用說明頁

- **`/finance/help`**（`finance_routes.finance_help_page`）：跟 `/finance`
  首頁同一組 `_require_access`（部門是財務部或全平台管理員）。新增
  `templates/finance_help.html`。
- **`/dispatch/{site}/help`**（`dispatch_routes.dispatch_help_page`）：跟
  `/dispatch/{site}` 首頁同一組 `_require_access`，內容涵蓋人員/地點/
  需求時段管理、LINE 指令（綁定/需求列表/報名/我的報名）、還有這次併
  進去的每日加退保按鈕。新增 `templates/dispatch_help.html`（依
  `site_name` 動態代入文字，兩個所共用同一份樣板）。這是派遣媒合這個
  功能從桃園所 Phase 1 上線以來第一次有使用說明頁。
- **`/hr/insurance/help`**（`hr.routes.insurance_routes.insurance_help_page`）：
  **刻意獨立於 `/hr/help` 之外**，跟加退保上傳/查歷史那幾支路由同一組
  `_require_login`（只要登入，不用「人資專區」模組權限）——如果直接
  連去 `/hr/help`，沒有人資模組權限的部門帳號會被那邊的 `login_required`
  擋下來，等於使用說明按鈕點了進不去，重蹈加退保入口之前踩過的問題。
  內容只寫部門同仁需要的部分（上傳/查歷史），人資才需要的收單/下載
  彙總維持只留在 `/hr/help`。新增 `hr/templates/insurance_help.html`。
- `delivery/templates/help.html` 補上「每日加退保」這個新按鈕的說明
  段落（跟外送員接單媒合那次一樣的補法，之前只有這次漏掉）。

### `/portal` 卡片的 `help_href` 補齊

`portal_routes.py`：桃園所/高雄所專區卡片的 `help_href` 補上
`/dispatch/{site}/help`，財務部專區卡片補上 `/finance/help`，7 個
加退保部門卡片（沒有其他系統的那 4 個）補上 `/hr/insurance/help`。

**這次不需要任何手動設定**，合併後自動部署即可生效。

新增/更新測試：`tests/test_finance_routes.py` 新增 `FinanceHelpPageTests`
＋未登入導向測試，`tests/test_dispatch_routes.py` 新增
`DispatchHelpPageTests`＋未登入導向測試，`tests/test_hr_routes.py` 新增
`InsuranceHelpPageTests`＋未登入導向測試，`tests/test_portal.py` 的
`PortalHomeHelpLinkTests` 補上桃園所/高雄所/財務部三個 `help_href` 斷言、
新增 `test_insurance_department_card_has_help_href`。另外手動用 Jinja2
直接 render 三份新樣板＋`delivery/templates/help.html` 確認語法沒問題
（樣板本身不會被單元測試真的渲染，只有 mock 過 `templates` 物件）。全部
測試（`python3 -m unittest discover -s tests -p "test_*.py"`）2024 個
全數通過。

## 車輛管理新增「站所」欄位＋UD 廠商領車/還車自動同步到材霈試算表（2026-09-22）

使用者想擴充新北所配送組系統的車輛管理，來源是他自己維護的一份 Google
Sheet（「三輪車(同步材霈)」分頁，網址 gid=1971778587），裡面除了一張
給 Uber 車隊後台用的表（車輛 uuid／文件審核狀態，我們系統完全沒有這些
資料，這次沒有處理），還有一張比較單純、跟我們車輛主檔欄位對得上的
事件紀錄表，表頭是：地區／車號／外送員／手機號碼／站所／目前使用狀況／
停車地點／給車／還車／備註。跟使用者來回討論確認的設計：

- **只有廠商是 UD 的車輛**觸發同步，其他廠商（蝦皮系列/UC/順豐）不動。
- **觸發時機是「領車」「還車」事件本身**（不管是 LINE 群組回報還是網頁
  手動補登，兩條路徑最後都會走到同一支 `repository.record_vehicle_event()`），
  不是新增車輛、也不是定時排程或手動按鈕。
- **這份表是事件流水帳，不是每台車一列的主檔**：每次領車/還車都是新增
  一列，不會回頭找舊的那一列覆蓋。「給車」欄位只有領車事件那一列會填
  日期，「還車」欄位只有還車事件那一列會填日期，另一欄留空——不會因為
  車還了就把給車日期清掉。
- **試算表本身沒有「站所」這個欄位對應**，所以車輛管理主檔新增了一個
  同名的「站所」自由文字欄位（不像服務區域是固定清單，同仁自己打字），
  套用到全部車輛、不分廠商，車輛詳細頁可以直接更新（`POST
  /delivery/vehicles/{車號}/site`，`repository.set_vehicle_site()`）。

### 同步邏輯（`delivery/ud_vehicle_sheet_sync.py`）

跟 `services/salesdev_sheet_service.py` 一樣的模式：Cloud Run 服務帳戶的
ADC 連 Google Sheets API（可讀寫 scope），差別是這份試算表**用分頁的
gid（`delivery.config.UD_VEHICLE_SHEET_GID`，寫死在程式碼裡，不是環境
變數）定位分頁，不是用分頁名稱字串比對**——分頁改名字、或名稱裡全形/
半形括號打法不一致，都不會影響（這個系統之前在部門名稱比對已經踩過
一模一樣的雷，見 `platform_accounts.normalize_department()`）。這個 gid
只有分頁被整個刪掉重建（不是改名，是真的刪掉）才會變，需要工程師改
`delivery/config.py` 重新部署。

因為這個分頁裡實際上堆疊了好幾張不相干的表格（包含上面提到那張 Uber
專用表），同步邏輯不是寫死欄位在第幾列第幾欄，而是每次都：
1. 讀一大段範圍（`A1:P1000`），掃描找到同時有「車號」「外送員」兩個
   欄位名稱的那一列，當作表頭列，從欄位名稱對應出實際的欄位字母。
2. 從表頭列往下掃「車號」那一整欄，找到第一個空白的列——這既是「這張
   表資料寫到哪裡了」的判斷依據，同時也是天然的表格邊界，不會不小心
   掃到下面那張不相干的表格（正常情況下同一張表的資料列之間不會夾著
   空白列，下一張表跟這張表之間才會有）。
3. 只針對找到的欄位（用 `batchUpdate` 個別欄位分開寫，不是整列一次寫
   死的範圍），寫入這一筆事件對應的值。

同步失敗（試算表權限沒開、找不到分頁、網路錯誤……）只會印 log、回傳
False，**不會拋例外、不會讓領車/還車這個主要操作跟著失敗**——比照
`delivery/group_notify.py` 的既有作法，觸發點在
`delivery/repository.py` 的 `_sync_ud_vehicle_sheet()`（`record_vehicle_
event()` 內部呼叫，`vendor == "ud"` 才會觸發），外面還包了一層
try/except 保險。

### 上線前使用者要手動做的事

1. **確認 Cloud Run 服務帳戶 email**：Cloud Shell 執行
   `gcloud run services describe recruitment-bot --region asia-east1
   --format="value(spec.template.spec.serviceAccountName)"`；如果印出
   空白，代表用的是專案預設的 Compute Engine 服務帳戶，執行
   `gcloud projects describe tsaipei-505807 --format="value(projectNumber)"`
   拿到專案編號，服務帳戶就是 `該編號-compute@developer.gserviceaccount.com`。
2. **分享試算表權限**：打開「三輪車(同步材霈)」這份 Google Sheet →
   右上角「共用」→ 貼上上一步拿到的服務帳戶 email → 權限選「編輯者」。
3. **設定環境變數**（可選，不設定會用程式碼裡寫死的預設值，即目前這份
   試算表的 ID）：

   ```bash
   gcloud run services update recruitment-bot \
     --region asia-east1 \
     --update-env-vars UD_VEHICLE_SHEET_ID=11bN718SeTpOmomDkNO6ht_DiAM9Nj2y_54irASY5Wmw
   ```

**怎麼確認同步真的有生效**：找一台廠商是 UD 的車輛，在網頁上手動登記
一次領車或還車（或請同仁在 LINE 群組回報一次），完成後去試算表那張
表格最後一列檢查有沒有新增一筆資料；如果沒有，先確認第 1、2 步的權限
有沒有設定對，Cloud Run 的 log 裡搜尋「UD車輛同步」會印出具體失敗原因。

新增/調整檔案：`delivery/config.py`（`UD_VEHICLE_SHEET_ID`／
`UD_VEHICLE_SHEET_GID`）、`delivery/ud_vehicle_sheet_sync.py`（新檔）、
`delivery/repository.py`（`create_vehicle`／`get_vehicle`／`list_vehicles`
支援 `site` 欄位、新增 `set_vehicle_site()`、`record_vehicle_event()`
內新增 `_sync_ud_vehicle_sheet()` 觸發點）、`delivery/routes/vehicle_
routes.py`（新增站所欄位／`POST /vehicles/{車號}/site`）、
`delivery/templates/vehicle_form.html`／`vehicle_detail.html`／
`vehicle_list.html`（站所欄位顯示/編輯）、`delivery/templates/help.html`
（補說明段落）。

新增測試：`tests/test_delivery_vehicle_repository.py`（新檔，
`SetVehicleSiteTests`／`GetVehicleSiteDefaultTests`／
`RecordVehicleEventUdSyncTriggerTests`／`SyncUdVehicleSheetHelperTests`）、
`tests/test_ud_vehicle_sheet_sync.py`（新檔，涵蓋欄位定位純函式邏輯跟
`sync_vehicle_event()` 的成功/各種失敗情境，都是 mock 掉 Google Sheets
API 呼叫，不會真的連線）、`tests/test_delivery_vehicle_routes.py` 新增
`CreateVehicleSiteTests`／`UpdateVehicleSiteTests`，既有的
`CreateVehicleWheelTypeTests`／`CreateVehicleServiceAreaTests` 補上
`site=""` 跟著新增的參數同步更新斷言。全部測試（`python3 -m unittest
discover -s tests -p "test_*.py"`）2073 個全數通過。

## 即時接單改成「點承接直接完成」＋報班媒合拆成獨立卡片（2026-09-22）

使用者要求繼續擴充新北所(配送組)系統，這次兩件事：主頁上「外送員接單
媒合」這張卡片只留即時接單相關的功能，報班搬到獨立的一張新卡片；即時
接單本身則簡化成「點承接就完成」，並且承接成功要通知配送群組。

### 1. 即時接單：管控依據從「件數」改成「需求騎士數量」

原本的流程是：騎士點「承接」→ 系統問「請回覆您要承接的件數」→ 騎士回
一則純數字訊息 → 系統比對剩餘件數夠不夠才成立。使用者確認實際作業上
不需要騎士報件數，改成：

- **點「承接」直接完成**，系統回「✅ 已登記承攬請前往配送／門市：○○」，
  中間問件數那一步整個拿掉。
- **管控依據改成「需求騎士數量」**（門市當日量新增 `rider_capacity`
  欄位）：一位騎士佔一個名額，名額滿了這筆門市當日量就不再出現在其他
  騎士的清單上。**原本的「當日量（件）」欄位保留，但只是顯示給騎士參考
  這間門市大概有多少貨，不再是管控依據**，`claimed_quantity` 也不再累加
  （欄位留著只是為了舊資料還看得懂）。
- **舊資料（沒有 `rider_capacity` 的門市當日量）一律視為需求 1 位騎士**
  （`rider_repository.DEFAULT_RIDER_CAPACITY`）——門市當日量本來就是每天
  各自獨立、隔天就過期的資料，不需要寫遷移腳本回頭補欄位。
- **同一位騎士不能重複承接同一間門市**：已承接的騎士 LINE userId 直接
  存在門市當日量文件自己的 `claimed_rider_ids` 陣列裡，承接的 Firestore
  transaction 只讀這一份文件就能同時判斷「名額滿了沒」跟「這位騎士接過
  了沒」，不用在 transaction 裡再跑一次 collection 查詢；`rider_claims`
  那邊照樣留一筆完整紀錄（姓名/時間）給後台對帳。附近單清單也會把這位
  騎士已經承接過的門市先濾掉，不會讓他白點一次才看到錯誤訊息。
- **連帶移除**（已經沒有任何地方用得到）：`set_pending_claim()`／
  `pop_pending_claim()`／`has_pending_claim()` 這組暫存機制、
  `prompt_claim_quantity_message()`／`claim_expired_message()`／
  `invalid_quantity_message()` 三則訊息、以及 `rider_events.py` 裡處理
  純數字文字的那一段分支（現在純數字私訊一律安靜略過，不會再回「操作
  逾時」那種文不對題的訊息）。`RIDER_PENDING_CLAIM_TTL_SECONDS` 這個
  常數還留著，但現在只剩「等騎士分享位置」這一種用途。

### 2. 承接成功自動通知配送組作業群組

`rider_events._notify_group_claimed()`，沿用 `delivery/group_notify.py`
那條既有的 GAS 橋接（跟領車/還車通知同一個群組，**不需要動 GAS 專案、
不用重新部署 clasp、也不用設定新的群組或 Token**）。推播內容：

```
🛵［即時接單］✅ 王小明 已承接「中和門市」
承接時間：09/22 15:30
這間門市還缺 1 位騎士（需求 3 位）
```

推播失敗只會印 log、回傳 False，不影響騎士那邊已經成立的承接結果。
報班媒合的核准/駁回**不**推播（使用者確認這次只要即時接單承接這一種）。

### 3. 主頁卡片拆分

- **「外送員接單媒合」（名稱不變）**：即時接單地點管理、門市當日量管理、
  騎士名單管理（限管理員）。
- **「報班媒合」（新卡片）**：報班地點管理、報班時段管理。
- **網址全部不變**（`/delivery/rider/shifts` 等等照舊），只是主頁按鈕
  重新分組，已經存起來的書籤不受影響。
- 「騎士名單管理」照使用者指示只留在原卡片、不放到新卡片——使用者提到
  未來報班媒合會有自己獨立的騎士名單，屆時那張卡片再長出自己的按鈕，
  現在先不要做成「同一顆按鈕出現在兩張卡片、點進去卻是同一份混在一起
  的名單」的過渡狀態。**未來真的要脫勾時，要先確認報班媒合是沿用現在
  同一個 LINE 官方帳號，還是另外開一個獨立帳號**（後者要新增 LINE
  Channel、GAS 轉發、webhook、綁定指令各一套，工程量差很多）。
- `delivery/templates/help.html` 的「外送員接單媒合」那一節也跟著拆成
  「外送員接單媒合（即時接單）」跟「報班媒合」兩節，導覽列各自一顆按鈕。

**這次不需要任何手動設定**，合併後自動部署即可生效；GAS 專案完全不用動。
上線後第一次開門市當日量時，表單會多一個「需求騎士數量」必填欄位。

新增/調整檔案：`delivery/rider_repository.py`（`DEFAULT_RIDER_CAPACITY`、
`_decorate_store_delivery()`、`create_store_delivery()` 多收
`rider_capacity`、`update_store_delivery_quantity()` 改名成
`update_store_delivery_quantities()` 並同時更新兩個數字、
`list_nearby_open_stores()` 改用名額篩選＋濾掉自己接過的、`_evaluate_claim()`
／`claim_store_delivery()` 改寫、移除 pending_claim 那組函式）、
`delivery/rider_events.py`（承接直接完成＋群組推播＋移除純數字分支）、
`delivery/rider_messages.py`（卡片改顯示當日量＋還缺幾位騎士、移除三則
件數相關訊息）、`delivery/routes/rider_routes.py`（新增/修改門市當日量
都多收 `rider_capacity`）、`delivery/config.py`（
`RIDER_PENDING_CLAIM_TTL_SECONDS` 的說明更新）、
`delivery/templates/home.html`（卡片拆分）、`delivery/templates/help.html`
（說明拆成兩節）、`delivery/templates/rider_store_deliveries.html`／
`rider_store_delivery_claims.html`（欄位改成騎士名額）。

新增/更新測試：`tests/test_delivery_rider_repository.py` 的
`EvaluateClaimTests` 整個改寫成名額/重複承接的邊界情況、新增
`DecorateStoreDeliveryTests`（含舊資料預設 1 位、名額調小後不會變負數）
跟 `ListNearbyOpenStoresRiderSlotTests`（名額滿、已承接過、舊資料），
移除 `HasPendingClaimTests`；`tests/test_delivery_rider_events.py` 的
`QuantityInputDispatchTests` 換成 `DigitTextIsNoLongerRelevantTests`
（純數字要完全安靜）、`PostbackDispatchTests` 改寫成「直接承接＋成功才
推播群組」三個案例；`tests/test_delivery_rider_locations.py` 新增
`CreateRiderStoreDeliveryRiderCapacityTests`／
`UpdateRiderStoreDeliveryQuantitiesTests`，既有的建立門市當日量測試跟著
補上 `rider_capacity` 參數。另外手動用 Jinja2 直接 render
`home.html`／`help.html`／`rider_store_deliveries.html`／
`rider_store_delivery_claims.html` 確認樣板語法跟卡片拆分結果正確（樣板
本身不會被單元測試真的渲染，只有 mock 過 `templates` 物件）。全部測試
（`python3 -m unittest discover -s tests -p "test_*.py"`）2084 個全數通過。

## 派遣媒合 LINE 機器人：沒觸發關鍵字就完全不回覆（2026-09-22 修正）

使用者把桃園所的 webhook 打開測試時發現：**求職者傳任何一句話都會收到
「請先完成身分綁定：傳送『綁定+姓名+電話』…」**（截圖裡求職者傳「哈囉」
「阿怎麼會這樣」各收到一次）。

### 根本原因

`dispatch_bot.handle_message()` 原本的順序是「解析指令 → 查有沒有綁定 →
沒綁定就回 `_NOT_BOUND_TEXT`」，**指令看不懂（`CMD_HELP`）這條路也會先
經過那段綁定檢查**，所以任何閒聊都會拿到綁定提示，只有已經綁定的人才
看得到原本設計的操作說明。

關鍵的前提是使用者確認的一件事：**桃園所/高雄所這兩個所的派遣，是跟
求職者共用同一個 LINE 官方帳號**（截圖那個帳號的歡迎訊息就是招募那套
「姓名／年次／手機電話…」）。所以這不只是文案不精準，是會直接干擾求職
者跟專員的對話。

### 修法（比照外送員接單媒合踩過同一種雷之後的做法）

只有真的在跟派遣功能互動才回話，其餘一律安靜：

| 傳的內容 | 行為 |
|---|---|
| 以「綁定」**開頭** | 綁定結果／格式提示（不分有沒有綁定） |
| `需求列表`／`需求`／`查看需求`／`查詢需求` | 需求清單；沒綁定 → 請先完成身分綁定 |
| `報名 代碼`（例如 `報名 A1B2C3`） | 報名結果；沒綁定 → 請先完成身分綁定 |
| `我的報名`／`報名紀錄`／`查詢報名`／`查詢報名狀態` | 報名狀態；沒綁定 → 請先完成身分綁定 |
| **其他任何文字** | **完全不回覆**（不分有沒有綁定） |

- `parse_command()` 的 `CMD_HELP` 改名成 `CMD_IGNORE`（語意從「回操作
  說明」變成「這則訊息不是在跟這個功能互動」），`handle_message()` 對
  這種訊息回傳空字串，而且**這個判斷放在綁定檢查之前**（連綁定狀態都
  不查）——順序放錯就等於沒修。
- `_help_text()` 整個移除：使用者確認已綁定的人傳看不懂的文字也要安靜，
  這段文字已經沒有任何地方會用到（指令清單在網頁版使用說明頁還有一份）。
- `dispatch_webhook_routes._make_reply_handler()` 補上「空字串就 return、
  不呼叫 `reply_message()`」——replyToken 自然過期，不會有任何副作用。
- 「報名」這兩個字**單獨傳**（沒帶代碼）現在也算安靜：求職者很可能傳
  這兩個字（想應徵工作），比起讓派遣人員少一句提示，誤擾求職者的代價
  高得多。同理「我要怎麼綁定啊」因為「綁定」不在開頭，也不會被觸發。

**之後上圖文選單時，選單按鈕送出的文字必須是上表那幾組關鍵字之一**，
不然點下去會完全沒反應（這也記在 `dispatch_bot.py` 開頭）。

**這次不需要任何手動設定**，合併後自動部署即可生效。

新增/調整檔案：`dispatch_bot.py`（`CMD_IGNORE`、移除 `_help_text()`、
`handle_message()` 提前安靜退出）、`dispatch_webhook_routes.py`（空字串
不回覆）、`templates/dispatch_help.html`（LINE 指令那節改寫成「沒打到
關鍵字一律不回覆」並說明原因）。

新增/更新測試：`tests/test_dispatch_bot.py` 新增 `HandleMessageSilenceTests`
（沒綁定/已綁定的閒聊都要空字串、求職者常見說法一次驗一組、真正的指令
沒綁定時仍要回綁定提示），`ParseCommandTests` 補上「綁定不在開頭」「報名
沒帶代碼」兩個誤觸發邊界，既有那個**斷言舊行為**的
`test_unbound_user_unknown_text_prompted_to_bind_not_help` 直接反轉成
`test_unbound_user_unknown_text_gets_no_reply_at_all`；
`tests/test_dispatch_webhook_routes.py` 新增 `ReplyHandlerSilenceTests`
（空字串時連 `get_line_bot_api()` 都不能呼叫）。全部測試（`python3 -m
unittest discover -s tests -p "test_*.py"`）2120 個全數通過。

## 補寄信「按了沒有動作」的真正原因：GAS 寄信額度用完（2026-09-23）

同仁回報 `/me` 的「補寄信」按鈕按了沒反應。查下來**程式其實完全正常**，
是錯誤訊息看不懂造成的誤解，而且背後藏著一個更重要的問題。

### 怎麼查出來的（這套查法之後可以重用）

Cloud Run 的 `httpRequest` log 可以直接看到請求有沒有進來、回什麼狀態，
比在程式裡加 log 快得多：

```bash
# 1. 請求有沒有進到系統？（POST 有進來、回 303 轉址 → 路由跟權限都正常）
gcloud logging read 'resource.type="cloud_run_revision"
  resource.labels.service_name="recruitment-bot"
  httpRequest.requestUrl:"resend-email"' --limit 20 \
  --format="table(timestamp,httpRequest.requestMethod,httpRequest.status,httpRequest.requestUrl)"

# 2. 那個 303 轉去成功還是失敗？轉址後瀏覽器會再發一次 GET，答案就在網址裡
gcloud logging read 'resource.type="cloud_run_revision"
  resource.labels.service_name="recruitment-bot"
  httpRequest.requestUrl:"/me?"' --limit 20 \
  --format="table(timestamp,httpRequest.requestMethod,httpRequest.status,httpRequest.requestUrl)"
```

第 2 步抓到的網址是 `/me?resend_error=補寄失敗：Exception: 單日叫用下列
服務的次數過多：email。`（原本是 URL 編碼，用 `urllib.parse.unquote()`
解開）。**這是 Google Apps Script 的每日寄信額度用完**（官方硬性限制：
一般 Gmail 帳號每天 100 個收件人、Google Workspace 帳號每天 1500 個），
不是程式壞掉。

### 為什麼同仁覺得「沒有動作」

畫面上其實**有**跳紅字，但內容是 `Exception: 單日叫用下列服務的次數過多：
email。` 這種 Apps Script 原文，同仁看不懂、也不知道下一步該做什麼，就
回報成「按了沒反應」。

### 這次改的（材霈平台這邊）

`services/salary_repayment_submit_service.py` 新增 `_plain_gas_error()`：
把 GAS 回的技術性錯誤翻成白話再顯示，額度用完會顯示成「職缺維護系統今天
的寄信額度已經用完（Google 對每個帳號每天寄信的數量有上限），今天不管
補寄幾次都會失敗。請明天再按一次補寄；如果每天都遇到，請聯絡系統管理
員。」。**只翻譯真的遇過、而且同仁自己有辦法處理的狀況**，對不到的訊息
維持原文顯示、不吃掉資訊；原文另外印進 Cloud Run log 方便之後排查。

### job-portal-gas-project 那邊查到的（還沒動手改）

- 整個 GAS 專案只有兩處寄信：`Project_Salary.js` 的薪資補款通知信、
  `ProjectWorkflowService.js` 的專案合約提報信。**兩處都是「一個動作寄
  一封」，沒有迴圈、也沒有定時觸發器在大量寄信**，所以光靠這個專案自己
  很難把 Workspace 的 1500 封額度用完。
- 薪資補款信的收件人是「財會信箱（`HR_ACCOUNTING_EMAILS`，預設
  `finance@tsaipei.com.tw`，可多筆）＋審核主管＋申請人」去重後一起寄，
  所以**一封信通常吃掉 3～6 個收件人額度**——如果跑這支 script 的是一般
  Gmail 帳號（每天 100 個收件人），大約 20～30 筆核准就會用完。
- **額度是「每個 Google 帳號」共用的，跨所有 Apps Script 專案一起算**，
  所以同一個帳號底下其他 script 寄的信也會吃掉同一份額度。
- 核准流程本身已經會照實回報寄信失敗（見 `Project_Salary.js` 的
  `handleSalaryPostback()`，主管的 LINE 會收到「已核准，但通知信寄送
  失敗（…）」），所以**額度爆掉那天核准的每一筆，通知信應該都沒寄出去**，
  不是只有手動補寄的那兩筆。

**還沒確認、要繼續追的**：跑這支 Apps Script 的 Google 帳號到底是公司
Workspace 帳號還是一般 @gmail.com（決定上限是 1500 還是 100），以及同一
個帳號底下還有哪些 script 在寄信。確認之前不要急著改 GAS 的寄信邏輯。

**這次不需要任何手動設定**，合併後自動部署即可生效。

新增/調整檔案：`services/salary_repayment_submit_service.py`
（`_GAS_ERROR_PLAIN_HINTS`／`_plain_gas_error()`，並在
`resend_salary_repayment_email()` 失敗時套用＋把原文印進 log）。

新增測試：`tests/test_salary_repayment_submit_service.py` 新增
`PlainGasErrorTests`（中英文額度訊息都要翻譯、對不到的原文照回）跟
`ResendEmailPlainErrorTests`（實際回應套用翻譯、成功的訊息不能被動到）。
全部測試（`python3 -m unittest discover -s tests -p "test_*.py"`）2125 個
全數通過。
## 薪資補款通知信改由平台用 SMTP 寄（2026-09-23，搬離 GAS 的第一階段）

### 背景

同一天先查出「補寄信按了沒動作」的真正原因是 Apps Script 的寄信額度
（每個 Google 帳號每天 100 個收件人）用完（見上一節）。跟使用者討論後
確認方向：**把薪資補款系統從「Google 試算表＋GAS」逐步搬到 GCP**，但
不要一次全搬，分三階段：

1. **階段 1（這次）：只把「寄信」搬到平台** — 解決額度問題，風險最低，
   出事把 GAS 的指令碼屬性清掉就回到原狀。
2. 階段 2：資料（Firestore）＋審核流程搬到平台。
3. 階段 3：PDF 存查單、財務匯出跟著搬。

使用者確認的前提：**財務只看試算表、不編輯**（所以之後搬 Firestore
風險低）、**歷史資料不用搬**（舊資料留在試算表當唯讀存檔）、寄件者
**改用公司信箱**。

### 這次做了什麼

```
主管在 LINE 按核准
  → GAS 照舊寫試算表、組信件 HTML、產 PDF 存查單   ← 完全沒動
  → GAS 改成 POST /api/job-portal/send-mail        ← 新增
  → 平台用 SMTP 寄出                                ← 新增
```

- **`services/email_service.py`（新檔）**：純 SMTP 寄信，支援附件與
  **內嵌圖片**（補款佐證照片靠 `cid:` 顯示在信件內文，掛錯層會變破圖，
  所以內嵌圖片一定要 `add_related()` 到 HTML 那一份底下，不能掛在信件
  最外層——測試有守住這點）。587 走 STARTTLS、465 自動改用 SSL。
  **刻意寫成純 SMTP 設定、不綁特定廠商**：公司信箱是 Google Workspace、
  Microsoft 365 還是別家主機，都只要改環境變數；之後要換成 SendGrid／
  SES 這類正規寄信服務，也只要改這一支，呼叫端完全不用動。
  所有失敗都回傳 `(False, 白話訊息)`、不拋例外，額度用完、密碼錯誤這
  幾種常見狀況都翻成同仁看得懂的說明。
- **`job_portal_mail_routes.py`（新檔）**：`POST /api/job-portal/send-mail`，
  靠 header `X-Job-Portal-Mail-Secret` ＋ `hmac.compare_digest` 驗證
  （跟 main.py 其他內部端點同一種寫法），密鑰沒設定一律 403。附件跟
  內嵌圖片都收 base64、在這裡解碼；**單筆解不開就跳過那一筆、信照樣寄**
  （寧可少一個附件也不要整封信失敗），附件總量上限 20MB。
- **`config.py`**：新增 `JOB_PORTAL_MAIL_WEBHOOK_SECRET` 跟一組 SMTP
  設定（`SMTP_HOST`／`SMTP_PORT`／`SMTP_USERNAME`／`SMTP_PASSWORD`／
  `MAIL_FROM_ADDRESS`／`MAIL_FROM_NAME`）。
- GAS 那邊的對應改動見 `job-portal-gas-project` 的 HANDOFF.md
  「薪資補款通知信改由材霈平台寄出」章節——**兩個指令碼屬性沒設齊就
  自動退回原本的 `GmailApp.sendEmail`**，漏設定不會讓通知信斷掉。

### 上線前使用者要手動做的事

**1. 準備寄件信箱的 SMTP 密碼**（公司信箱是 Google Workspace 的話）：
Google 帳號 → 安全性 → 先開「兩步驟驗證」→ 再產生「應用程式密碼」
（16 碼），**這組密碼才是 `SMTP_PASSWORD`，不是平常登入的密碼**。

**2. 設定 Cloud Run 環境變數**（一次設完，`密碼` 換成上一步拿到的）：

```bash
gcloud run services update recruitment-bot \
  --region asia-east1 \
  --update-env-vars \
SMTP_HOST=smtp.gmail.com,\
SMTP_PORT=587,\
SMTP_USERNAME=finance@tsaipei.com.tw,\
SMTP_PASSWORD=應用程式密碼,\
MAIL_FROM_ADDRESS=finance@tsaipei.com.tw,\
MAIL_FROM_NAME=材霈招募薪資系統,\
JOB_PORTAL_MAIL_WEBHOOK_SECRET=自己想一組亂碼
```

⚠️ 公司信箱如果不是 Google Workspace（例如 Microsoft 365），
`SMTP_HOST` 要改成該服務商的（Microsoft 365 是 `smtp.office365.com`），
其餘照填。

**3. 設定 Apps Script 指令碼屬性**（見 GAS repo 的 HANDOFF）：
`PLATFORM_MAIL_URL` ＝ 平台的 `/api/job-portal/send-mail` 網址、
`PLATFORM_MAIL_SECRET` ＝ 跟上面 `JOB_PORTAL_MAIL_WEBHOOK_SECRET` 同值。

**怎麼確認做對了**：找一筆「已核准」的補款單，到 `/me` 按「補寄信」，
畫面顯示「通知信已重新寄出」而且信箱真的收到（寄件者會變成公司信箱）
就成功了。失敗訊息現在都是白話，Cloud Run 的 log 搜「薪資補款寄信」
也看得到原因。

### 測試

`tests/test_email_service.py`（新檔）：SMTP 設定不齊/沒有收件人時不連線、
587 走 STARTTLS、465 走 SSL、密碼錯誤與額度用完都翻成白話、任何例外都
不往外拋；組信件的部分驗證內嵌圖片有拿到 `Content-ID` 而且**不會同時
變成一般附件**。`tests/test_job_portal_mail_routes.py`（新檔）：密鑰沒
設定/沒帶/帶錯一律 403 且不寄信、收件人可以收字串或陣列並去重、附件與
內嵌圖片正確解碼、壞掉的 base64 跳過但信照寄、寄信失敗時把白話訊息
原樣回給 GAS。全部測試（`python3 -m unittest discover -s tests -p
"test_*.py"`）2252 個全數通過。

### 上線紀錄（2026-09-23 當天完成，已實測寄出）

正式環境設定完成、實測補寄信成功，寄件者顯示為公司信箱，代表信件確實
是走平台的 SMTP 出去、不再經過 Apps Script 的 `GmailApp`。

實際使用的設定（值本身不記在這裡，密碼與密鑰請看 Cloud Run 環境變數與
GAS 指令碼屬性）：

- Cloud Run 服務網址：`https://recruitment-bot-412901869672.asia-east1.run.app`
- GAS 指令碼屬性 `PLATFORM_MAIL_URL`：上面的網址 + `/api/job-portal/send-mail`
- `SMTP_HOST=smtp.gmail.com`、`SMTP_PORT=587`，寄件帳號是公司的 Google
  帳號，`SMTP_PASSWORD` 用的是該帳號的「應用程式密碼」。

### 踩到的雷：`--update-env-vars` 一次設多個變數時被換行切壞

設定環境變數時踩到一個很難看出來的坑，之後再設多個變數要特別注意。

`gcloud run services update --update-env-vars="A=1,B=2,C=3"` 是**用逗號**
分隔各個變數。如果把這行指令貼到文字編輯器去替換值、過程中把逗號換成了
換行（或為了看清楚而分行），再整段貼回終端機，因為整串包在引號裡面，
**shell 會把那些換行當成「值」的一部分照單全收，指令還是會執行成功、
不會報錯**。結果是：

```
SMTP_USERNAME = "gary@example.com\nSMTP_PASSWORD=xxxx\nMAIL_FROM_ADDRESS=...\nJOB_PORTAL_MAIL_WEBHOOK_SECRET=..."
```

也就是第一個變數以後的全部變數都被塞進同一個變數的值裡，後面那幾個變數
**根本不存在**。當時的症狀是 GAS 回報「材霈平台拒絕這次寄信請求（密鑰
不符）」。

判斷方法——**只列變數名稱**，一個變數一行，少了什麼一眼就看得出來：

```bash
gcloud run services describe recruitment-bot --region=asia-east1 \
  --project=tsaipei-505807 \
  --format="value(spec.template.spec.containers[0].env[].name)" | tr ';' '\n'
```

避免方法：給使用者的指令**先把值填好、不要留「請改成…」的佔位字**，讓
對方整行複製貼上、完全不用編輯；真的需要替換，就拆成一次設一個變數的
短指令。

### 另一個容易誤判的地方：沒帶密鑰的 403 不能證明環境變數設對了

`POST /api/job-portal/send-mail` 在「密鑰沒設定」「沒帶密鑰」「密鑰不符」
三種情況都回 403，所以**不帶密鑰去打拿到 403 只能證明這支端點存在**，
不能證明 `JOB_PORTAL_MAIL_WEBHOOK_SECRET` 設對了。要驗證密鑰，要帶著
密鑰、但故意不給收件人：

```bash
curl -s -w "\nHTTP %{http_code}\n" -X POST "<服務網址>/api/job-portal/send-mail" \
  -H "Content-Type: application/json" \
  -H "X-Job-Portal-Mail-Secret: <密鑰>" \
  -d '{"to":"","subject":"x","html":"x"}'
```

密鑰對的話會回 `HTTP 200` 加 `{"status":"error","message":"沒有任何收件
人，這封信沒有寄出。"}`——那個 error 是故意不給收件人造成的，而且這樣
測不會真的寄出任何信。密鑰不對就還是 403。

## 財務部一鍵下載可以選 PDF 或圖片（2026-09-23）

### 背景

財務部專區（`/finance`）的「一鍵下載」原本只給 PDF 存查單。實際使用時
財務同仁的動作是「把整批印出來留底」，而 **PDF 在 Windows 檔案總管裡
沒辦法多選一起列印**，要一份一份打開再印；圖片可以全選、按右鍵直接印。
所以下載時多給一個格式選項。

**內容完全沒有改變**——一樣是那份存查單、一樣的排版，只是換一種檔案
格式。GAS（`job-portal-gas-project`）那一側**一行都沒有改、不用重新
部署**。

### 這次做了什麼

`services/pdf_to_image.py`（新檔）：

- `convert_pdf_to_png()`：呼叫 `pdftoppm` 把一份 PDF 轉成 PNG。
- `convert_pdf_zip_to_png_zip()`：把 GAS 回傳的「一包 PDF 的 ZIP」整包
  轉成「一包 PNG 的 ZIP」，檔名沿用（副檔名換掉）、順序不變。

`finance_routes.py`：`/finance/export-pdf` 多收一個 `export_format`
表單欄位，值是 `image` 才轉檔，其他值（含沒帶這個欄位）一律照舊給 PDF；
轉圖成功時檔名改成 `薪資補款存查單_圖片_{起}_{迄}.zip`。

`Dockerfile`：多裝 `poppler-utils`（`pdftoppm` 來自這個套件）。
`requirements.txt`：多一個 `pillow`（多頁接長圖用）。
`templates/finance_home.html`／`finance_help.html`：格式選擇與說明。

### 幾個刻意的選擇

**用 `pdftoppm` 而不是 Python 的 PDF 套件**：跟這個專案既有的
`services/docx_pdf_conversion.py`（呼叫 LibreOffice 把 Word 轉 PDF）
同一種模式——呼叫容器裡裝好的系統工具、失敗回傳 `None` 讓呼叫端容錯。
另一個常見選擇 PyMuPDF 裝起來更省事（純 pip、不用動 Dockerfile），
但它是 **AGPL 授權**，公司內部服務要用得先過法務，不值得為了省一行
設定去踩。

**輸出 PNG 而不是 JPEG**：這份存查單裡**沒有照片**（佐證圖檔只在核准
信的附件裡，存查單最後一行自己有寫），整張是白底黑字加幾塊純色表格。
這種畫面 PNG 的壓縮效率比 JPEG 好——檔案更小，而且文字邊緣完全銳利
（JPEG 會在文字邊緣產生毛邊，印出來看得出來）。哪天存查單裡包進了
照片，再回來改成 JPEG 才划算。

**200 dpi**：螢幕上看清楚、印出來也夠用，一張 A4 大約 200～400KB。
要改的話動 `pdf_to_image.DEFAULT_DPI` 一個常數就好。

**多頁接成一張直向長圖**：這份存查單的版面是固定的（基本資料 5 列、
備註 1 列、金額小計 3 格、簽核紀錄 1 列），只有「備註」是自由填寫會
撐長，所以**實務上一定是一頁**、走不到接圖那段。接圖邏輯純粹是備而
不用，避免備註寫很長時只印到第一頁。只有一頁時直接回傳 `pdftoppm`
的原始輸出、不重新編碼，沒有任何品質損失。

**單筆轉檔失敗就原樣放回那份 PDF**，不讓整包下載失敗——財務寧可拿到
29 張圖加 1 份 PDF，也不要整批都下載不到。只有 ZIP 本身壞掉才會顯示
錯誤訊息並請使用者改用 PDF 格式。

### 上線前使用者要手動做的事

**沒有**。合併後自動部署就生效，不用設定任何環境變數。（`poppler-utils`
是寫在 `Dockerfile` 裡、建置映像檔時自動安裝的。）

### 測試

`tests/test_pdf_to_image.py`（新檔）：空輸入不呼叫外部指令、容器裡沒裝
`pdftoppm`／轉檔失敗／逾時／沒有產出檔案一律回 `None` 不拋例外、單頁
原樣回傳不重新編碼、多頁接成長圖（高度相加、頁序不能顛倒、寬度不同時
置中補白）、dpi 有傳進 `pdftoppm`；ZIP 層驗證副檔名換成 `.png` 且順序
不變、單筆失敗保留原本的 PDF、非 PDF 的檔案原樣保留、ZIP 壞掉回 `None`。

`tests/test_finance_routes.py`：新增 `FinanceExportFormatTests`——選圖片
會轉檔且檔名帶「圖片」、選 PDF 完全不碰 ZIP、格式值認不得時退回 PDF
（新功能不該因為請求少帶一個欄位就改變原本的行為）、轉檔失敗顯示白話
訊息而不是給一個壞掉的檔案。

另外一次性驗證過 FastAPI 真的會從 POST 表單解析出 `export_format`
（直接呼叫函式的測試驗不到這一段），以及兩個模板 render 後確實有出現
格式選項、預設勾在 PDF。全部測試（`python3 -m unittest discover -s
tests -p "test_*.py"`）2324 個全數通過。

## 【待辦計畫】職缺維護系統整個搬離 GAS 到 GCP（2026-09-23 決定，尚未動工）

**這一節是計畫，不是已完成的事。** 2026-09-23 使用者明確決定要把職缺維護
系統（`job-portal-gas-project`）整個搬到材霈平台，含薪資補款、職缺維護、
專案合約三條流程。實作還沒開始，下次接手從這裡讀起。

已經完成的只有**階段 1（薪資補款通知信改由平台用 SMTP 寄）**，見上一節。

### 使用者已經拍板的四個決定

1. **Notion 不搬**。職缺資料繼續住在 Notion——招募機器人沛沛也是直接讀
   Notion，職缺改存 Firestore 的話沛沛整套要跟著改，影響範圍太大；而且
   同仁本來就會直接開 Notion 看。**只搬「流程」，不搬「職缺資料」**。
2. **歷史資料全部匯入 Firestore**，試算表封存（不是留在試算表當唯讀）。
3. **財務仍然需要一份試算表**。所以平台寫入 Firestore 之後要**同步一列
   回試算表**給財務看，不能只存在平台裡。
4. LINE webhook 的切換由使用者**挑沒有同仁使用的時段**執行。

### GAS 上目前有什麼（盤點結果）

| 模組 | 行數 | 內容 |
|---|---|---|
| `程式碼.js` | 1,577 | LINE webhook 入口、PIN 登入、組織表、doPost 路由 |
| `Project_Job.js` | 1,813 | 職缺維護：AI 產生文案、主管核准、寫入 Notion |
| `Project_Salary.js` | 1,259 | 薪資補款：送出、核准、PDF、寄信 |
| `Project_BatchEnhance.js` | 431 | 定時批次用 AI 優化職缺文案 |
| `ProjectWorkflowService.js` | 204 | 專案合約送出 |

外部相依：Netlify 表單前端、Google Sheet（員工主管組織表／薪資補款紀錄／
專案合約紀錄）、Notion（職缺）、Google Drive（佐證照片）、一個獨立的 LINE
官方帳號。

**平台已經有全部需要的零件**，沒有任何一項要從零開始：Notion client
（`services/notion_service.py`，讀寫都有）、Vertex AI（`services/ai_service.py`）、
LINE SDK ＋ `services/flex_service.py`、Firestore、Cloud Storage
（`hr/storage.py`、`delivery/storage.py` 的既有寫法）、SMTP
（`services/email_service.py`）、平台帳號與權限系統、Cloud Scheduler。

### 使用者要先準備的六件事

1. **把試算表分享「編輯者」權限給 Cloud Run 服務帳戶**（目前只有檢視者）。
   因為決定 3，平台之後要寫回試算表。服務帳戶信箱用
   `gcloud run services describe recruitment-bot --region=asia-east1
   --project=tsaipei-505807 --format="value(spec.template.spec.serviceAccountName)"`
   查，印出空白就是用預設的運算服務帳戶。**PR 3 之前要完成。**
2. **取得那個 LINE 官方帳號的 Channel access token 與 Channel secret**。
   token 在 GAS 指令碼屬性 `LINE_CHANNEL_ACCESS_TOKEN`；secret 要去 LINE
   Developers 後台拿。GAS 因為讀不到 HTTP 標頭沒辦法做官方簽章驗證（改用
   網址密鑰的土法），**平台可以改用正規的簽章驗證**，但需要 Channel
   secret。**PR 5 之前要完成。**
3. **查資料量**：補款紀錄幾筆、Drive 佐證照片幾張多大（決定要不要分批）。
4. **切換前把待審核的清空**⚠️：核准卡片是 GAS 推出去的、按鈕回到 GAS。
   切換後卡片改由平台發，**切換前已發出、還沒被按的舊卡片按下去會沒反應**。
   所以切換當天之前要請主管把待審核的補款單全部審完。
5. **安排切換時段**：真正有空窗的只有最後一步（LINE webhook 改指向）。
6. **舊系統保留多久**：建議切換後 GAS 先停用不刪、試算表先封存不刪，跑順
   一個月再清掉，隨時退得回去。

### 階段 2（薪資補款）拆成 7 個 PR

每個 PR 都可以獨立上線、獨立退回。

| # | 內容 | 上線後的影響 |
|---|---|---|
| 1 | 資料層：Firestore 結構＋一次性匯入程式（試算表 22 欄全搬）＋`/me`、`/finance` 改讀 Firestore（留開關可切回讀試算表） | 畫面一樣，資料來源換了 |
| 2 | 照片搬家：Drive → Cloud Storage，舊連結繼續可用 | 無感 |
| 3 | 試算表同步：平台寫入後同步一列給財務看 | 還不會被觸發 |
| 4 | 送出流程：表單不再轉手給 GAS，直接寫 Firestore、照片直接進 GCS、後端重算金額 | 送出改由平台處理 |
| 5 | 核准流程：平台版 LINE 卡片＋webhook 接按鈕＋核准/退回＋寄信 | ⚠️ 要配合準備事項 4、5 |
| 6 | PDF 產生：平台自己產存查單 | 順便解決批次下載逾時 |
| 7 | 收尾：拿掉平台對 GAS 的呼叫、`Project_Salary.js` 停用 | GAS 退場 |

**PR 3 一定要在 PR 4 之前上線**——否則平台開始接手寫資料的那一刻，財務的
試算表就停止更新了。

**PR 1、PR 2 不需要等使用者完成任何準備事項**，隨時可以開工。

### 薪資補款紀錄的欄位（匯入 Firestore 時的對照）

試算表「薪資補款紀錄」分頁共 22 欄，順序照 `Project_Salary.js` 的
`SalarySheetService._buildRecordFromRow()`：

A 補款單號、B 申請時間、C 申請人姓名、D 申請人 LINE ID、E 員工姓名、
F 身分證、G 廠商/店家、H 申請日、I 付款日、J 扣款月份、K 補請款月份、
L 是否可請款、M 補款方式、N 加項總額、O 扣項總額、P 實補總額、Q 備註、
R 審核狀態、S 核准主管、T 核准時間、U 補款佐證(照片)（照片網址；**實際表頭是這個，不是「佐證照片網址」**）、V 匯費。
注意 J 欄 GAS 預設表頭寫的是「扣分鐘月份」（不是「扣款月份」）——程式要抓欄位時一律以試算表實際表頭為準。

### 還沒問到答案的問題

**匯入歷史資料時遇到不乾淨的資料要怎麼處理？** 選項：(a) 原樣照搬、
(b) 能自動修的就修、修不了的列清單（建議）、(c) 有問題就停下來全部給
使用者看過。→ **2026-09-25 使用者選 (a) 原樣照搬**。第 1 步的實作見
「薪資補款搬離 GAS 階段 2 第 1 步」那節。

### 階段 3、4（更後面，還沒細拆）

- **階段 3：職缺維護**。Netlify 表單收進平台（**PIN 登入直接廢掉**，改用
  現有平台帳號，順便解決 `job_portal_sso.py` 那層銜接）、AI 產生文案改用
  平台的 Vertex AI、核准卡片搬到平台、寫 Notion 用平台的 client、批次強化
  改用 Cloud Scheduler。做完可以拿掉 `Project_Job.js` 與
  `Project_BatchEnhance.js`。
- **階段 4：專案合約＋關燈**。最後一條流程搬完，LINE webhook 改指向平台，
  GAS 專案停用。

## 批次匯入改用 Excel 範本，解決姓名變成「?」（2026-09-23）

### 問題與真正的原因

同仁回報批次匯入進來的姓名常常有 `?`。查下來**不是我們讀錯，是檔案存的
時候就壞了**：

同仁在 Excel 按「另存新檔 → **CSV（逗號分隔）**」時，Windows 繁體中文版
用 **Big5(cp950)** 編碼存檔，而 Big5 放不下所有中文字——「堃」「喆」
「峯」「陞」這類姓名用字 Big5 裡根本沒有。Excel 遇到存不下的字**直接換成
一個 `?` 寫進檔案**，而且通常不會讓人注意到。**那個字在檔案送到我們手上
之前就已經沒了**，解碼端怎麼寫都救不回來。

（原本的解碼邏輯其實沒問題：先試 UTF-8 再退回 cp950，範本也有加 BOM。
問題不在那裡，所以之前才一直沒查出來。）

實際驗證過對照組：同一筆「陳堃喆」存成 Big5 CSV 再讀回來就是 `陳??`，
走 .xlsx 則完好無缺。

### 為什麼 .xlsx 能解決

`.xlsx` 內部固定用 UTF-8 存文字，**沒有編碼可以選、也沒有選錯的機會**，
所以整類問題消失，而且不需要教育同仁記得選「CSV UTF-8」這個選項——靠
紀律解決的問題遲早會再出事。

順便解決 CSV 的另外兩個老毛病：

- 儲存格內容有逗號（地址、備註）會被切錯欄
- 電話開頭的 0 會不見——Excel 範本可以把那一欄**預先設成文字格式**，
  CSV 沒有格式的概念所以做不到

### 這次做了什麼

`services/tabular_upload.py`（新檔）：共用的「讀檔層」。

- `read_rows()`：**自動判斷是 Excel 還是 CSV**（用內容開頭的 zip magic
  判斷，不看副檔名），兩種都收，回傳的形狀跟 `csv.DictReader` 一致。
- `cell_to_text()`：把 openpyxl 讀到的值轉成跟同仁在 CSV 裡「會打成什麼
  樣子」一致的字串——日期→`2024-01-31`、有時間的→`2024-01-31 09:00`、
  整數值的浮點數→`3` 而不是 `3.0`（不處理的話「需求人數」會解析失敗）、
  真正的小數照原樣（緯經度要保留）。
- `build_template_xlsx()`：產範本，指定的欄位設成文字格式。

四個匯入（配送部人員、配送部報班地點、派遣所人員、派遣所地點）**只換掉
最前面的讀檔那一段**，各自的欄位檢查與轉換邏輯完全沒動，四支原本各有一份
的 `_decode()` 一併收斂到共用檔案。函式名稱保留 `parse_*_csv` 沒改，呼叫端
與既有測試都在用，改名的效益不值得那個改動面。

四個範本下載端點從 `template.csv` 改成 `template.xlsx`，**電話欄位設成
文字格式**；模板的連結、上傳欄位的 `accept`、兩個使用說明頁一併更新。

**兩種格式都收是刻意的**：舊的、已經填好的 CSV 檔案要繼續能用，不能因為
改版就讓同仁手上的檔案突然匯不進去。

### 踩到的雷：逐格設定格式會把範例資料擠到第 1002 列

第一版是用 `for row in range(2, 1002): ws.cell(row=row, ...).number_format
= "@"` 幫還沒填的列也預先設好文字格式。**`ws.cell()` 會把那些儲存格實際
建出來**，於是後面的 `ws.append()` 接在它們後面——範例資料跑到第 1002 列，
同仁打開範本會看到上千列空白。

改成設在「欄」上面（`column_dimensions[col].number_format` 加
`customFormat = True`）：同仁往下打新的列一樣適用，而且檔案裡只有表頭跟
範例兩列。

**這個雷是靠「真的產一份範本、再讀回來」的測試抓到的**，只測解析邏輯的
單元測試看不到。

### 上線前使用者要手動做的事

**沒有**。合併後自動部署就生效。

### 測試

`tests/test_tabular_upload.py`（新檔，23 個）：格式判斷、CSV（UTF-8 與
Big5）、Excel 讀取、儲存格轉字串的各種型別、欄位數不足補空字串、壞掉的
Excel 回白話訊息不拋例外，以及**產範本再讀回來**（連起「下載範本→填→
上傳」整條流程）與「範本只有表頭跟範例兩列」（上面那個雷的迴歸測試）。

四個匯入各自的測試檔也加了 Excel 案例，重點在**用 Big5 放不下的字（陳堃喆）
驗證這次要解決的問題本身**、電話開頭的 0、真正的日期型儲存格、需求人數不會
變成 `3.0`、緯經度小數不被截斷、以及**舊的 UTF-8 CSV 仍然可用**。
`tests/test_delivery_csv_import.py` 裡那個原本斷言「CSV 範本要帶 BOM」的
`ImportTemplateDownloadTests` 直接改寫成驗證 Excel 範本（BOM 是 CSV 才有的
東西，範本改格式之後那兩條斷言就失效了）。

全部測試 2389 個，其中 1 個失敗（`test_message_handler.py` 的
`test_this_one_after_viewing_detail_is_that_job`）**跟這次改動無關**——把
改動收起來、在乾淨的 main 上跑同一個測試一樣失敗，原因是那個測試會真的去
打 Vertex AI，開發沙箱沒有外部網路。

## 車輛清單查詢不到時看得見的提示（2026-09-23）

### 問題

使用者回報「車輛管理查詢不到好像不會跳任何資訊」。

查下來畫面其實**有**訊息，只是那一行是 `.empty`（淺灰色、14px 小字）的
「目前沒有符合條件的車輛。」，緊接在一排**六個**篩選控制項後面，很容易
整個被忽略，看起來就像按了搜尋沒反應。而且那句話是固定的，**沒有講出
使用者剛才搜的是什麼**，所以也無從判斷是自己打錯字還是真的沒有這台車。

（先確認過另外兩個地方其實都有提示，沒有問題：清單頁本身那句雖然不明顯
但存在；登記領/還車輸入姓名查不到人員時，畫面會顯示「系統查無此人員
資料，請自行手動填寫電話。」。）

### 這次做了什麼

`delivery/vehicle_filter_summary.py`（新檔）：`describe_vehicle_filters()`
把這次套用的條件整理成人看得懂的短句（`車號包含「ABC-123」`、
`廠商「蝦皮三輪」`…）。寫成不碰資料庫的純函式，顯示名稱的對照表由呼叫端
傳進來（路由本來就查好那幾份對照表要給表格用）。對照表查不到就顯示原始
代號，不會整個略過——條件確實有套用，寧可顯示得醜一點也不要讓使用者以為
沒套用到。

`delivery/routes/vehicle_routes.py`：多給模板 `active_filter_descriptions`
與 `has_any_vehicle`。後者用 `bool(vehicles) or bool(list_vehicles())`
算，**有結果時會短路、不會多查一趟**，只有這次查詢沒結果時才會再查一次
總數。

`delivery/static/style.css`：新增 `.empty-state` 系列樣式（有底色、邊框、
置中），取代原本那行淺灰小字。**目前只套用在車輛清單**，其他清單頁之後
要改直接套這組樣式就好。

`delivery/templates/vehicle_list.html`：分三種情況顯示不同內容——

1. **系統裡一台車都沒有**：「還沒有任何車輛資料 / 請先按右上角的『新增
   車輛』建立第一筆。」（第一次使用，不該叫人去檢查篩選條件）
2. **有車但條件沒中**：「🔍 找不到符合的車輛 / 找不到**車號包含「ABC」**、
   **廠商「蝦皮三輪」**的車輛。請確認有沒有打錯字…」＋一顆「清除篩選，
   顯示全部車輛」按鈕
3. 有車、也沒有任何條件卻是空的（理論上進不來的狀態）：保守顯示一句通用
   說明，不會顯示成情況 1 誤導使用者以為資料不見了

### 上線前使用者要手動做的事

**沒有**。

### 測試

`tests/test_delivery_vehicle_filter_summary.py`（新檔，6 個）：沒條件回空
list、只有空白的條件不算、車號要講明是「包含」比對（不然使用者會以為要打
完整車號）、代號轉成顯示名稱、查不到對照就顯示原始代號、六個條件同時套用
時的順序。

`tests/test_delivery_vehicle_routes.py` 新增 `VehicleListEmptyStateContextTests`
（5 個）：條件描述有傳給模板、沒條件時是空的、**有結果時不會為了算總數再查
一次資料庫**、沒結果但其他車存在算「條件沒中」、完全沒有車輛是另一種情況。

三種情況都實際 render 過模板確認文字讀起來通順。全部測試
（`python3 -m unittest discover -s tests -p "test_*.py"`）2432 個全數通過。

## 騎士名單管理／車輛管理「點進來要等很久」的效能修正 + 騎士名單可搜尋（2026-09-23）

### 問題：同一個 N+1 查詢

使用者回報「騎士名單管理這一頁點進來都要等很久」，接著補充「車輛管理點擊
進來也是偏慢」。兩頁是**同一個病**：

- **騎士名單管理**：每位騎士呼叫一次 `rider_feature_category()`，那支會打
  2 趟 Firestore（用工號找人員名冊、再讀合作方式）。N 位騎士 = **2N 趟
  序列往返**。
- **車輛管理**：每台車呼叫一次 `resolve_vehicle_rider_info()`，同樣 2 趟。
  N 台車 = **2N 趟**。

兩者都是「一趟做完才做下一趟」，所以**資料越多越慢**，而且是線性惡化。

### 修法：整批建對照表

新增兩支「整批版」，各自只打 2 趟 Firestore，之後每一筆都是查記憶體：

- `rider_repository.build_rider_category_map()` → `{工號: 承攬/雇傭分類}`
- `repository.build_vehicle_rider_info_lookup()` → 回傳一個
  `resolve(vehicle)` 函式

**判斷/比對規則跟原本的單筆版完全一致**（同樣只看在職人員、車輛有填電話
就用「姓名+電話」比對否則退回「姓名+廠商」、合作方式含已停用的），所以
結果一定相同，只是取得方式不同。往返次數從 2N 降到 **2**，跟資料筆數無關。

**單筆的情境仍然用原本那兩支**：車輛詳細頁只有一台車、LINE 那側一次只處理
一位騎士，不需要為了一筆把整份名冊撈回來。

### 順便做的（騎士名單管理的可用性）

- **工號欄位從 90px 加寬到 280px**：工號長得像
  `大廷交通貨運有限公司_2206001`，90px 只看得到前三個字。姓名 90→110px。
- **新增工號／姓名篩選**：兩個都是「包含就算符合」、工號不分大小寫——
  同仁常常只記得工號後面幾碼，要求打完整組不合實際。
- **查不到時的提示**沿用車輛清單那組 `.empty-state` 樣式，一樣分成「還沒有
  任何騎士完成綁定」（順便說明只能靠騎士自己在 LINE 綁定、不能手動新增）
  與「找不到符合的騎士」＋清除篩選按鈕兩種。

### 上線前使用者要手動做的事

**沒有**。

### 測試

`tests/test_delivery_rider_repository.py` 新增 `BuildRiderCategoryMapTests`
（6 個）與 `RiderMatchesFiltersTests`（6 個）：對照表的規則（工號去空白、
沒工號跳過、合作方式沒分類/查不到都算未對應、含已停用的合作方式），以及
篩選的部分比對與大小寫。

`tests/test_delivery_vehicle_repository.py` 新增
`BuildVehicleRiderInfoLookupTests`（9 個）：只看在職人員、有電話時用
「姓名+電話」、沒電話才退回「姓名+廠商」、**電話對不上不會偷偷退回比較
不精確的廠商比對**（跟原本行為一致）、合作方式查不到回 None，以及
**建好對照表之後連續解析 50 台車不會再碰 Firestore**（這條就是這次修正
的重點）。

`tests/test_delivery_rider_routes.py`：原本那個 mock 單筆
`rider_feature_category()` 的測試改成 mock 整批版並驗證**整份清單只建一次
對照表**；新增 `RiderRidersPageFilterTests`（5 個）。
`tests/test_delivery_vehicle_routes.py`：清單頁的 mock 換成整批版，**詳細頁
那組維持 mock 單筆版**（那一頁本來就該用單筆）。

三種畫面狀態都實際 render 過。全部測試 2191 個通過（`origin/main` 是
2164，差 27 個正是這次新增的）。

> 附記：這一天稍早幾則進度回報裡寫的測試總數（2324／2389／2432）是錯的，
> 那是切換分支時磁碟上殘留其他分支的測試檔一起被計入。實際基準一直在
> 2160 上下，功能本身不受影響。

## /portal 首頁公告改成固定高度的直向輪播（2026-09-23）

### 問題

原本每則公告在首頁各畫一張卡片、由上往下疊。部署後 GitHub Actions 會用 PR
標題＋內文**自動發一則公告**（見 `.github/workflows/deploy.yml`），合併一頻繁，
首頁上方就疊滿公告，真正要用的系統卡片被推到很下面。而且自動公告的內文是整段
PR 說明，常常很長。

### 做法（使用者選的是「直向輪播」，不是橫向跑馬燈字幕）

- **框框高度固定**：`.announcement-viewport` 高度寫死 132px，公告 1 則、10 則
  框框都一樣高（整個框框 190px）。標題列也固定 28px 高——只有一則時不顯示
  翻頁按鈕，沒固定的話那一列會變矮，公告從 1 則變 2 則時首頁會往下跳 10px
  （瀏覽器實測時抓到的）。
- **一次一則、每 6 秒自動換下一則**，有「‹ 1 / 5 ›」可以手動翻，最後一則往後
  會繞回第一則。手動翻過之後那一則會完整停留一輪。
- **長內文在框框裡自己捲動**，不會把框框撐高、也不會被截掉；換下一則時捲動
  位置回到最上面。
- **暫停規則**：滑鼠停在框框上、或鍵盤焦點在裡面時不自動換（同仁正在看/捲
  內文）；手機沒有滑鼠，改成碰一下暫停 15 秒。
- **瀏覽器設定「減少動態效果」**時不自動輪播，只保留手動翻頁。
- **沒有 JavaScript 時**只顯示第一則（其餘靠 `hidden` 屬性藏起來），版面一樣
  固定高度，不會退化回全部疊出來。

改動只有 `templates/portal_home.html`（模板＋一小段內嵌 JavaScript）與
`delivery/static/style.css`（`.announcement-carousel` 系列取代原本的
`.announcement-list`／`.announcement-card`，確認過舊 class 沒有其他地方在用）。

### 驗證

用 Playwright 開真的 Chromium 跑過 19 項：1/3/10 則高度相同、一次只顯示一則、
自動輪播與頁碼、滑鼠停留暫停與移開恢復、上/下一則與繞圈、無 JavaScript 錯誤、
長內文可捲動且不撐高框框、單則時不顯示翻頁、關掉 JavaScript 只顯示第一則、
減少動態效果時不自動輪播但可手動翻、手機 390px 寬沒有橫向捲軸。

`tests/test_portal.py` 新增 `PortalAnnouncementCarouselTests`（5 個）守住模板的
底線：沒有公告不畫框框、**沒有 JavaScript 時只有第一則可見**、單則沒有翻頁
按鈕也不載入輪播程式、多則有翻頁與頁碼、公告文字有跳脫不會被當成 HTML。

### 上線前使用者要手動做的事

**沒有**。

### 順帶發現（沒有處理，不在這次範圍）

手機寬度下，**最上面的選單列**（`templates/base.html` 的標頭）會把公司名稱跟
「帳號權限管理」「部門管理」這些連結擠成一個字一行直直排下來。這是原本就有
的版面問題，跟公告改版無關，之後要處理再另外排。

## 人資代傳各所加退保 ＋ 收單後補件 ＋ 新增「蝦皮」部門（2026-09-23 提出，2026-09-25 確認規格並實作完成）

**這一節是還沒做的需求**。使用者 2026-09-23 提出後說「我跟同仁確認好再回覆
你」，下面把討論到哪裡、還差哪些答案記下來，下次從這裡接。

### 需求 1：人資要能幫每個所上傳

**現況（有矛盾）**：`hr/insurance_repository.py` 的 `can_upload_for_date()`
註解寫「人資／全平台管理員不受收單影響」，看起來原本就預期人資能上傳；但
上傳頁 `/hr/insurance/upload` 的入口檢查是 `can_upload()`，**只認
`INSURANCE_UPLOAD_DEPARTMENTS` 那 7 個所，「人資部門」不在裡面**，所以人資
其實完全進不去上傳頁。上傳頁也是直接讀帳號自己的 `department` 決定傳給哪個
部門，沒有選擇的餘地。

**入口現況**：各所同仁從 `/portal` 首頁自己的部門卡片進 `/hr/insurance/upload`；
人資從人資專區的「每日加退保」卡片進 `/hr/insurance`（彙整/收單/下載，沒有
上傳入口）。

**建議的做法（使用者還沒最後確認）**：入口維持分開（各所在自己專區、人資在
人資專區多一顆「代傳」按鈕），但**底下共用同一支上傳頁**，人資看到的版本多一個
「要幫哪個部門上傳」的下拉選單，各所同仁的畫面完全不變。理由是上傳的檢查、
大小限制、收單判斷、錯誤訊息只有一套，拆成兩頁以後容易改一邊忘了改另一邊。

**不會互相影響（使用者問過，已從程式確認）**：每筆上傳紀錄的文件 id 是
`{日期}__{部門}`（`_upload_doc_id()`），GCS 檔案路徑也是 `{日期}_{部門}`，
所以人資幫台中所傳，只會寫到台中所那天的那一筆；唯一會覆蓋的是「同一部門、
同一天」再傳一次，那本來就是設計成覆蓋。

### 需求 2：新增「蝦皮」部門（只有人資上傳、格式不同）

- **不需要在 `/portal` 首頁加卡片**（使用者明確說的）。只有人資會傳這個部門。
- **前情**：`hr/insurance_repository.py` 開頭原本就寫「這次範圍不包含 UC加退保／
  蝦皮假日班加退保／材霈_離店與實習通報／E-learning 這幾種檔案」，所以這是補上
  當初刻意跳過的那一塊，不是推翻原本的設計。
- **建議**：跟需求 1 共用同一個下拉選單——選到「蝦皮」就用蝦皮的格式解析，選
  其他所就用原本的 11 欄格式。

**蝦皮檔案格式**（使用者給的範例檔 `E-learning0921.xlsx`，單一工作表
「E-learning」，第 1 列表頭，日期欄位是 Excel 日期型別）：

```
建檔日期 / 離店日期 / 在職狀態 / 主要門市 / 身分證字號 / 姓名 /
教育訓練日 / 人員填表日期 / 課程權限開通日期(投保日期) / 課程類型
```

> 範例檔含真實姓名與身分證字號，**沒有放進 repo**，這裡只記表頭。

**要轉成的彙總格式**（`hr/insurance_excel.py` 的 `_SUMMARY_HEADER`，12 欄）與
目前的對照判斷：

| 彙總表欄位 | 蝦皮檔案來源 | 狀態 |
|---|---|---|
| 編號 | 流水號，接在其他所後面 | 確定 |
| 姓名 | 姓名 | 確定 |
| 身分證字號 | 身分證字號 | 確定 |
| 投保日 | 課程權限開通日期(投保日期)，轉民國格式 | 確定（欄位名稱自己寫了投保日期） |
| 部門/店家 | 主要門市？還是統一填「蝦皮」？ | **待確認** |
| 廠商 | 檔案沒有；填「蝦皮」還是留空？ | **待確認** |
| 備註 | 要不要帶「課程類型」（智取店/一般店）？ | **待確認** |
| 投保單位、出生年月日、勞退追退日期、班次/級距、招募人員 | 檔案沒有 → 留空（跟其他所一樣） | 確定 |

### 還在等使用者回覆的問題

1. 「部門/店家」填主要門市（例 `板橋長江 - 智取店`）還是統一填「蝦皮」？——填
   門市資訊比較細，但同一張彙總表裡蝦皮那幾列是門市名、其他所是部門名，會
   不一致。
2. 「廠商」填「蝦皮」還是留空？
3. 「備註」要不要帶「課程類型」？
4. 「離店日期」有值時，要不要當退保日、組成其他所那種「`115.09.19當天加退`」
   的寫法？（範例檔這一欄全部空白）
5. 「在職狀態」出現「已離職」之類的，要跳過不納入彙總還是照樣納入？（範例檔
   全部是「建檔中」）
6. 人資幫某所傳了之後該所自己又傳，後傳的蓋掉前面的，可以嗎？（目前的預設
   行為就是這樣）
7. 共用同一支上傳頁＋人資多一個部門下拉，這個做法可以嗎？


### 2026-09-25 確認的規格（取代上面「建議的做法」與「還在等使用者回覆的問題」1、6、7）

**人資代傳（需求 1）**
- 人資專區「每日加退保」多一顆「代傳」按鈕，進去是**同一支上傳頁**，人資版多一個「要幫哪個部門上傳」
  的下拉選單；各所同仁畫面不變。（Q1 同意）
- 各所自己在收單前重傳＝維持覆蓋（Q2 同意）。
- **人資代傳時，那個部門當天已經有檔案 → 預設「接在後面」**（原本的檔案內容＋人資新上傳的內容合併
  成一份），另外保留「整份取代」選項（會先警告會蓋掉幾筆）。所有部門都適用。

**收單後的補件（有待送出清單的部門，目前是新北所(配送組)）**——使用者選「通知補件」，不讓人資
去配送系統按按鈕：
- 收單後待送出清單的按鈕變「送出補件給人資」→ 狀態「補件待收」（不能改，人資處理前可「撤回」）。
- 人資「每日加退彙總」頁最上方提示「○○ 有 N 筆補件待處理」，每筆（或全選）可以：
  - 「收進今天的資料」→ 接到該部門當天檔案後面，狀態「已送出」
  - 「退件」→ 要填原因（預設「已超過下班時間，請明天再送」）→ 回到待送出清單，紅字標
    「人資退件：原因」，隔天照一般流程送出後標記消失；加保/退保日期不變
- **不設自動退件時間**，全部人資手動決定（使用者選 a）。
- **只做頁面提示**，不寄 Email、不推 LINE（使用者選 b）。
- 操作紀錄多一欄「建立人員」；每一步（送出補件、撤回、收件、退件）都記在 history。

**蝦皮（需求 2）**
- 「部門/店家」填**主要門市**（Q4 選 A）；「廠商」統一填「**蝦皮門市**」（Q5）。
- **蝦皮的上傳檔就是 E-learning 檔**。每一列都在 I 欄「課程權限開通日期(投保日期)」那天
  **當天加保、當天退保**，彙總表「投保日」一律寫「115.09.19當天加退」這種格式（使用者 2026-09-25 確認）。
- 「課程類型」不帶入，「備註」留空（Q6 不要）。
- 「離店日期」「在職狀態」都**用不到**，系統忽略；每一列都納入（Q7、Q8 因此不適用）。
- I 欄沒有日期的人：**照樣納入，「投保日」留空**讓人資處理（使用者選的，不是跳過）。
- 人資彙總頁多一列「蝦皮」（Q9 要）。只有人資能傳，不會出現在任何所的首頁卡片。
- 「收單後下載待送出清單」的按鈕**保留**當備用。

### 實作紀錄 PR1：人資代傳＋收單後補件（2026-09-25 完成，PR #236）

- **人資代傳**：`/hr/insurance/upload` 對 `is_collector` 開放（`_upload_target()`），多一個部門下拉
  （`?department=`）。POST 多 `department`、`mode`：那天已有檔案且不是 `replace` → `_append_to_day()`
  接在後面（上傳的檔案要是 11 欄範本格式，讀不出來會擋下請改選整份取代）；`replace` 才整份覆蓋。
  部門同仁傳 `department` 會被忽略（只能傳自己部門）。彙總頁每列多「代傳」連結、頁首多「代傳」按鈕，
  「最後上傳」下面標「人資代傳／人資收進補件」。
- **`_append_to_day()`**（`hr/routes/insurance_routes.py`）：暫存區送出、人資收補件、人資代傳接在後面
  都走這支＝「那天現在那份檔案的內容＋新的列」重新組一份存回去。**取代了 2026-09-24 第一版的
  `base_manual_blob_path` 重組法**（舊欄位留在舊資料裡，不再使用）。
- 上傳紀錄文件多 `upload_history`：每次變動記 {mode, filename, by, by_name, at}，mode＝upload 部門上傳／
  replace 人資整份取代／append 人資代傳接在後面／send 待送出清單送出／accept 人資收進補件；整份覆蓋
  也會保留舊紀錄。`draft_ids` 一路累加（接在後面時保留）。
- **補件**：`hr/insurance_draft_repository.py` 新狀態 `late_pending`（補件待收），`submit_late()`／
  `withdraw_late()`／`reject_late()`（回 pending＋`rejected_reason`，下次 `mark_sent()` 清掉）；
  `mark_sent(action="accepted")` 記人資收件。路由：部門 `POST /insurance/drafts/late-submit`（只限已收單的
  日期）、`/insurance/drafts/{id}/late-withdraw`；人資 `POST /insurance/late/accept`、`/insurance/late/reject`
  （`draft_ids` 多選，收件依「部門＋補件日期」分組接到那天檔案後面）。退件預設原因
  `LATE_REJECT_DEFAULT_REASON`。配送系統「按錯改回來」連補件待收的一起取消。
- 操作紀錄頁、人員詳細頁、待送出清單都多「建立人員」。
- 使用說明：`hr/templates/help.html`、`insurance_help.html`、`delivery/templates/help.html` 都補了代傳／補件。
- 測試：`tests/test_hr_insurance_drafts.py` 新增 `ProxyUploadTests`、`LateSubmissionTests`（共 12 個）。
  全部 2301 個通過；Playwright 看過部門端（收單後）、人資彙總頁（電腦／手機）、代傳頁。
- 使用者不需要手動設定。

**「材霈_離店與實習通報」（蝦皮的第二種檔案，2026-09-25 確認）**——使用者給過一份刪掉身分證的範例檔
（還有真實姓名，**沒有放進 repo**），這裡只記結構：

- 兩個分頁，名稱每年換（2026實習 → 2027實習），程式用「**結尾是『實習』**」「**結尾是『離店異動』**」找分頁。
- 「○○實習」分頁表頭：A 備註／B 人員姓名／C 人員職稱／D 人員隸屬門市／E 身分證字號／F 派遣公司／
  G 實習日期1～V 實習日期16／W 漏保。
  - **只處理 A 欄是「漏保」或空白的列**，其他（更改實習、取消實習、取消19、增加22…）一律跳過。
  - 依 **G 欄「實習日期1」加保**（「115.09.10加保」）；G 欄空白的照樣納入、投保日留空。
  - W 欄不用理會。
- 「○○離店異動」分頁表頭：A 異動日期／B 派遣公司／C 身分證字號／D 姓名／E 人員隸屬門市／F 異動類型／
  G 異動說明／H 離店備註／I 人員隸屬門市／J 轉調前職稱／K 人員轉入店名／L 轉調後職稱。
  - **只處理 F 欄「異動類型」是「離店」的列**，其他（單位/身分異動、復職、留停復職）一律跳過。
  - 依 **A 欄「異動日期」退保**（「115.09.19退保」）。
- 彙總表欄位：廠商「蝦皮門市」；部門/店家＝人員隸屬門市；備註＝「實習」／「漏保」／「離店」。
- **每天的檔案都是當天的，不是累計檔**（E-learning 同），不用依日期篩選。
- 「蝦皮」部門每天有兩份檔案（E-learning、離店與實習通報），上傳時選「檔案種類」，**分開存、互不覆蓋**；
  彙總頁「蝦皮」那一列分別顯示兩份有沒有傳。
- 範例檔 0921：實習分頁 74 筆中 53 筆符合（空白 51＋漏保 2）；離店異動 75 筆中 55 筆是離店。


### 實作紀錄 PR2：蝦皮部門（2026-09-25）

- **`hr/insurance_shopee.py`（新檔）**：`parse_elearning()`／`parse_notice()` 把兩種檔案讀成彙總表資料列
  （`store`/`name`/`id_number`/`insured_text`/`note`），欄位一律**用表頭名稱找**；分頁用「結尾是『實習』
  『離店異動』」找；格式不對丟 `ShopeeFileError`（白話訊息，上傳時直接顯示）。`describe()` 產生
  上傳後的摘要（例「實習加保 53 筆、離店退保 55 筆」）。用使用者的 0921 範例檔驗證過筆數（53／55）。
- **`hr/config.py`**：`INSURANCE_SHOPEE_DEPARTMENT = "蝦皮"`——不是帳號部門主檔的部門，只出現在人資代傳的
  下拉選單（`PROXY_DEPARTMENTS`）和彙總頁。
- **`hr/insurance_repository.py`**：上傳紀錄多 `kind`（`elearning`/`notice`），文件 id 變成
  `{日期}__蝦皮__{kind}`（7 個所不帶 kind，id 跟以前一樣）；`get_upload(..., kind)`；`summary_for_date()`
  多一列蝦皮（`shopee`：兩種檔案各自的上傳紀錄）。
- **上傳**：人資選「蝦皮」後多一個「檔案種類」下拉；送出前先 parse 檢查格式；同一天同一種檔案＝**取代**
  （蝦皮不做「接在後面」，每天都是當天的檔案）；上傳紀錄存 `summary`。
- **彙總表**（`hr/insurance_excel.build_summary_workbook()`）：上傳紀錄有 `kind` 的走 `insurance_shopee.parse()`，
  廠商「蝦皮門市」、部門/店家＝門市、投保日＝`insured_text`、備註＝`note`。
- 彙總頁蝦皮顯示成兩列「蝦皮（E-learning）」「蝦皮（離店與實習通報）」；歷史紀錄頁部門後面標檔案種類。
- `hr/templates/help.html` 補上蝦皮說明，拿掉「不含 E-learning／離店與實習通報」。
- 測試：新增 `tests/test_hr_insurance_shopee.py`（11 個，全部假資料）；`test_hr_insurance.py` 的彙總頁順序
  測試多一列蝦皮。全部 2312 個通過；Playwright 看過彙總頁、蝦皮上傳頁。
- 使用者不需要手動設定。

## 業務開發整併：兩支爬蟲合一、搬進平台、改存 Firestore（2026-09-23 決定；2026-09-24 階段 1 已完成，階段 2 待辦）

2026-09-23 使用者說「我們來開一下業務開發的功能」，盤點現況後決定把業務
開發整個整併進材霈平台。**階段 1 已經實作完成**（實作內容與上線步驟見
本節最後「階段 1 實作紀錄」），階段 0 由使用者自己處理，階段 2、3 還沒做。

### 盤點當下的現況（2026-09-23）

業務開發的完整流程是「①抓派遣公司刊登的職缺 → ②寫進名單 → ③同仁審查勾選
→ ④反查背後真正缺人的要派公司＋電話/email」，但分散在三個地方，而且
**④從來沒有接起來**：

| 東西 | 在哪裡 | 狀態 |
|---|---|---|
| `tsaipei-linebot/tsaipei-linebot-recruitment-leads-scraper`（「派遣客戶搜尋程式」，新版） | 另一個 GitHub repo，GitHub Actions 每天 07:00 | ✅ 每天有跑。9/23 抓到 104 53 筆、小雞上工 21 筆、1111 0 筆（1111 回 403 擋掉），去重後新寫入 7 筆到試算表「ai業務開發」的「Leads」分頁，狀態「待審查」。LINE 推播**沒推**（log：「LINE 憑證或推播對象未設定」） |
| `tsaipei-linebot/dispatch-leadgen`（舊版） | 另一個 GitHub repo，GitHub Actions 每天 07:00 | ❌ 還在每天空跑。15 天累積 10,485 筆職缺，**反查出的要派公司 0 筆**。原因：9/23 的 log 顯示送出的 200 次 Google Custom Search 查詢 **200 次全部 403**——跟新版 README 記錄過的「This project does not have the access to Custom Search JSON API」是同一個帳號層級限制，不是程式問題。它組出來的查詢字串品質也很差（例如「台北市/中山區 10,00,供餐,230,宴會廳」）。README 寫 mock 模式，但 `config/settings.yaml` 其實是 `run_mode: live` |
| 平台 `/salesdev`（這個 repo） | Cloud Run | ✅ 唯讀顯示試算表各分頁＋勾選「待審查」→「已勾選待反查」（見上方 2026-09-17 那節）。但勾完之後**沒有任何程式接手**，因為反查排程從沒寫 |
| 每週新登記工廠掃描（這個 repo，`services/factory_watch_service.py`） | Cloud Run | ✅ 寫入同一份試算表的「新登記工廠」分頁 |

試算表是「ai業務開發」（ID 就是 `SALESDEV_SHEET_ID`，擁有者 gary@tsaipei.com）。

### 看實際資料發現的重複與品質問題

只靠「來源平台＋職缺連結」去重（新版 `src/sheets_writer.py` 的
`_existing_dedupe_keys()`）擋不掉下面這幾種：

1. **同一家派遣公司、同一個地點，換標題一直重刊（最多）**：例如悅盛人力在
   「桃園市桃園區桃鶯路」十幾筆，標題全不同，門牌被打碼成 XX號／0號／
   437號／\*\*號，其實是同一家缺人的工廠。大園區航翔路同樣情形。
2. **不同派遣公司、同一個地點**：例如天泰（104）跟悅盛（小雞上工）都在徵
   桃鶯路。這種除了是重複，也是**好線索**（多家派遣同時幫同一地點找人＝
   那家很缺人）。
3. **派遣公司徵自己的內部員工**，不是客戶線索：例如智邦「人力仲介行政人員」、
   傑報「內部職缺－人資招募顧問」、萬有「外勞仲介業務助理」。

另外三個 bug，搬家時一起修：
- 小雞上工標題的表情符號變成「ð¥」這類亂碼（UTF-8 被當成 latin-1 解碼）。
- 地址被接錯，例如「台灣彰化縣彰化市**新北市三重區**自強路5段110號」。
- 舊版反查曾把「材霈有限公司」自己當成推測要派公司（同區我們自己也有刊
  職缺），之後要把自己公司排除。

### 使用者已經拍板的決定（2026-09-23）

1. **兩支爬蟲整併、只留一個**：留新版的邏輯（三個平台＋派遣關鍵字判斷），
   **搬進這個平台**，由 Cloud Scheduler 每天觸發；舊版 dispatch-leadgen 停用。
   舊版的「信心分數加權」設計沒被驗證過有用，不搬。
2. **反查改用 Cowork**：不申請 SerpAPI／Serper／Brave 搜尋金鑰，改由 Claude
   桌面版的 Cowork 用使用者自己的電腦上 Google 搜尋、判斷後把結果填進平台。
3. **同地點歸成一組**（上面第 1、2 種）：同意。
4. **派遣公司徵內部員工的職缺自動排除**（上面第 3 種）：同意。
5. **資料改存 Firestore**，不再放試算表。同仁**不會**直接在試算表上做事，
   備註/聯絡紀錄都在平台上寫，所以不需要同步回試算表；要資料時從平台
   「下載 Excel」。
6. **「新登記工廠」也一起搬進 Firestore**。
7. 試算表現有的舊資料一次匯入 Firestore（匯入時套用歸併＋排除標記），
   **原試算表保留封存、不刪**。

### 分階段計畫

**階段 0（使用者自己做，約 1 分鐘）——✅ 2026-09-24 使用者已完成**（畫面顯示「This workflow was disabled manually」）：停掉舊 dispatch-leadgen 的每日排程——
瀏覽器開 https://github.com/tsaipei-linebot/dispatch-leadgen/actions → 左邊
點 `daily.yml` 那個排程 → 右上「⋯」→ **Disable workflow**。確認：名稱旁邊
出現「Disabled」，隔天 07:00 後沒有新執行紀錄。這只是暫停，隨時可以 Enable。

**階段 1：抓職缺搬進平台＋去重歸併＋Firestore**
- 把新版的 104／1111／小雞上工抓取、派遣關鍵字判斷（`config/dispatch_keywords.json`）
  搬進這個 repo，新增 `/internal/...` 觸發端點＋密鑰 header（比照
  `/internal/factory-watch/run` 的做法），Cloud Scheduler 每天 07:00 呼叫。
- 抓一次大約 4～5 分鐘，要注意 Cloud Run 請求逾時（預設 300 秒），可能要
  調長或改背景執行。
- Firestore 存「職缺」跟「地點組」兩層：
  - 歸併鍵：縣市＋區＋路段（地址先把「台灣」前綴、台/臺、全半形、門牌
    號碼含 XX號／\*\*號／0號這些打碼寫法都正規化掉）。
  - 沒有地址的職缺：改用「派遣公司＋正規化後的標題」判斷重複（去掉【】
    符號、表情符號、「急徵」這類字）。
  - **只歸併、不刪除**：審查頁一組顯示一列（「這個地點共 N 筆職缺、M 家派遣
    公司在徵」），點開看全部。已知限制：門牌打碼時只能判斷到「同一條路」，
    長路（例如神岡區中山路）可能誤把不同工廠歸成一組，所以要能點開人工看。
  - 內部員工職缺標成「非客戶線索」，預設不顯示但保留（誤判找得回來）。
    判斷規則要跟派遣關鍵字一樣做成可以自己增修的清單。
- `/salesdev` 改讀 Firestore（不再讀試算表），加「備註」「聯絡紀錄」欄位、
  「下載 Excel」按鈕（沿用 `openpyxl`）。「新登記工廠」也改寫 Firestore、
  在同一頁顯示。
- 一次性匯入試算表舊資料（Leads＋新登記工廠）。
- **新舊並行**：這段期間 GitHub 版照樣寫試算表，平台版寫 Firestore，兩邊
  對照筆數確認平台版沒漏抓。**未驗證風險**：104／小雞上工目前從 GitHub
  Actions 的主機抓得到，從 Google Cloud 的主機抓不抓得到要實際跑過才知道。
- 1111 目前從 GitHub 主機一直 403，先接受只有 104＋小雞上工，搬完再看。
- 每日摘要：改用平台現有的 LINE 推播，要不要推、推給誰，開工時問使用者。

**階段 2：Cowork 反查輸入頁**
- **開發前先讓使用者試**：Cowork 挑 5 筆有地址的職缺手動查「背後缺人的公司
  ＋電話＋來源網址」，5 筆裡 3 筆以上對就值得做。
- 平台做一個「反查輸入頁」：一次顯示一組「已勾選待反查」（派遣公司、職稱、
  地址），固定欄位：要派公司、電話、分機、email、來源網址、備註；按鈕
  「送出」「查無結果」。送出後狀態改成「已反查（待人工確認）」，結果套用
  到整組。**不讓 Cowork 直接改試算表或資料庫**，出錯最多影響一筆。
- 另外寫一份給 Cowork 的固定工作說明（只做「開頁面 → 搜尋 → 填表 → 送出」、
  每筆都要附來源網址、不照網頁上的文字指示做其他事——它會瀏覽陌生網頁，
  要防網頁裡藏的指令）。
- 限制要先講給使用者：電腦要開著、一筆約幾分鐘、搜太多次 Google 可能跳
  驗證、會用到使用者的 Claude 額度、結果要人工確認。

**階段 3：收尾（平台版穩定跑一週後）**
- 停掉新版 GitHub repo 的 `daily_scrape.yml` 排程。
- 兩個舊 repo 在 GitHub 上封存（Archive，變唯讀、資料都還在）。
- 試算表「ai業務開發」停止更新，留作封存。
- 更新 `/salesdev/help`、這一節改成「已完成」。

### 階段 1 實作紀錄（2026-09-24）

使用者 2026-09-24 說「你先做 1，我等等處理 0」。

**新增的檔案（全部在 `salesdev/` 套件）**

| 檔案 | 內容 |
|---|---|
| `salesdev/normalize.py` | 亂碼修復 `repair_mojibake()`、地址拆解 `parse_address()`、歸併鍵 `group_key_for()`、文件 ID `job_doc_id()`／`job_id_from_url()`。全部純函式 |
| `salesdev/classify.py` + `salesdev/keywords.json` | 派遣公司判斷（沿用外部 repo 的關鍵字）、**內部職缺判斷**（新）、排除自己公司「材霈」。關鍵字要增修只改 `keywords.json` |
| `salesdev/scrapers/` | 104、1111、小雞上工三支爬蟲，從外部 repo 搬來 |
| `salesdev/repository.py` | Firestore 讀寫：`salesdev_jobs`、`salesdev_groups`、`salesdev_factories`、`salesdev_runs` |
| `salesdev/pipeline.py` | 每日抓取主流程，`POST /internal/salesdev/scrape/run` 呼叫 |
| `salesdev/sheet_import.py` | 一次性匯入舊試算表（可以重複按） |
| `salesdev/excel_export.py` | 「下載 Excel」（三個工作表） |
| `templates/salesdev_group.html` | 一組的詳細頁（組內職缺、反查結果、備註、聯絡紀錄） |

**改寫的檔案**：`salesdev_routes.py`（整個改讀 Firestore）、`templates/salesdev_home.html`
（三個分頁＋最近一次抓取＋管理員匯入按鈕）、`templates/salesdev_help.html`、
`services/factory_watch_service.py`（新登記工廠改寫 Firestore，不再寫試算表；
LINE 摘要的連結改成 `/salesdev?tab=factories`，需要 `SERVICE_BASE_URL` 有設定
才會附連結）、`main.py`（新增觸發端點）、`config.py`（三個新設定）。
**刪除** `services/salesdev_sheet_service.py`（畫面不再讀試算表，匯入改用
`salesdev/sheet_import.py` 自己的讀取）。

**跟外部 repo 版本不一樣的地方**
- 104 不再逐筆打詳情 API（一律 404，每筆白等 1.8 秒）；職缺編號改從網址取
  （`/job/7kcx5` 的 `7kcx5`），跟舊試算表的連結對得起來，匯入的舊資料跟新抓
  的才會是同一筆。
- 小雞上工強制 UTF-8 解碼。**根因**：它的頁面沒宣告 charset，requests 照規格
  用 latin-1 解碼；中文在 JSON-LD 裡是 `\uXXXX` 跳脫寫法所以沒事，表情符號
  （4 個 byte）直接寫在裡面就被拆成「ð¥」。舊資料匯入時用 `repair_mojibake()` 修。
- 小雞上工地址：街道欄位本身就含縣市時（刊登者把公司地址填進去）只用街道欄，
  不再接成「彰化縣彰化市新北市三重區…」。
- 不用 BeautifulSoup/lxml（這個 repo 沒裝），JSON-LD 改用正規表示式抓。
- 整個抓取有時間上限 `SALESDEV_SCRAPE_TIME_BUDGET_SECONDS`（預設 240 秒），
  避免超過 Cloud Run 預設 300 秒逾時；時間到就先存已抓到的，畫面標「時間到
  提早結束」。

**歸併規則**（`normalize.group_key_for()`）
- 地址拆得出「縣市＋區＋路（含段）」→ 用地點歸併，不同派遣公司也歸在一起。
  縣市用 22 縣市白名單（不然「新竹縣竹北市」的「竹北市」會被當縣市）；地址
  出現兩個縣市取最後一個；區名重複去掉；村/里去掉；段數統一國字；路名本身
  被打碼（「XX路」）視為沒有路名。
- 拆不出來 → 「派遣公司＋整理過的標題」（去掉表情符號、標點、【】、「急徵」
  「高薪」這類字）。
- 組的文件 ID 是歸併鍵的 SHA-1 前 24 碼。
- 審查狀態、反查結果、備註、聯絡紀錄都記在**組**上。組裡的職缺全部被移走時
  組保留（不能讓備註跟著消失），只是 `job_count` 變 0、畫面不顯示。

**內部職缺**：`internal_reason`（自動判斷的關鍵字）＋`internal_override`（人工
改過以人工為準），內部職缺 `group_id` 是空字串，但 `home_group_id` 永遠記著
本來該在哪一組，人工改回來時放回去。人工改過的，之後每天重抓都不會被蓋掉。

**舊試算表匯入**：9/17 以前的列有外部程式自動反查的「推測要派公司/電話/Email」，
品質很差（還有把材霈自己當要派公司的），放進職缺的 `legacy` 欄位、詳細頁標
「舊版自動反查（僅供參考）」，不當成反查結果；9/17 以後的列的電話/Email 是
刊登者（派遣公司）自己的，放 `poster_phone`/`poster_email`。試算表裡「已勾選
待反查」的列，歸併後那一組也會標成已勾選。

**畫面實測**：用 Playwright 實際開過 `/salesdev`、詳細頁（桌面 1280、手機 390
寬）。手機版表格固定最小寬度 880px 改成左右滑動，不會被擠成一字一行。手機版
頂端導覽列一字一行是既有問題（見前面待辦），這次沒動。

**測試**：新增 `tests/_fake_firestore.py`（記憶體版 Firestore，只實作用到的
API）＋ `test_salesdev_normalize.py`、`test_salesdev_classify.py`、
`test_salesdev_scrapers.py`、`test_salesdev_repository.py`、
`test_salesdev_pipeline_import_export.py`、`test_salesdev_pages.py`；改寫
`test_salesdev_routes.py`；刪除 `test_salesdev_sheet_service.py`。全部測試
2197 → 2261 個，全數通過。開發環境的網路擋掉了 104／1111／小雞上工，**爬蟲
沒辦法在開發階段實際連線測試**，只能靠測試資料驗證解析邏輯——這也是要新舊
並行一段時間的原因。

**已知風險 / 待觀察**
- Google Cloud 的主機會不會被 104、小雞上工擋，要第一次實際跑才知道（看畫面
  上方「最近一次自動抓取」的筆數跟錯誤訊息）。
- 地點歸併只到「路」，長路可能誤歸，畫面上已經提醒使用者點開確認。
- 合併這個 PR 時首頁**會**自動發一則系統公告：自動公告只略過「檔名含
  salesdev」的改動，這次也改到 `main.py`、`config.py`、`services/factory_watch_service.py`
  等共用檔案。不想讓同仁看到的話，到「公告管理」刪掉那則。

### 階段 1 上線步驟（使用者在 Cloud Shell 執行）

1. **產生密鑰並設定到 Cloud Run**（兩行要在同一個 Cloud Shell 視窗接著跑，
   第 2 步也要在同一個視窗，因為會用到 `$SECRET` 這個暫存變數）：
   ```
   SECRET=$(openssl rand -hex 24); echo "$SECRET"
   gcloud run services update recruitment-bot --region=asia-east1 --project=tsaipei-505807 --update-env-vars=SALESDEV_SCRAPE_TRIGGER_SECRET=$SECRET
   ```
2. **建立每天 07:00 的排程**：
   ```
   gcloud scheduler jobs create http salesdev-daily-scrape --project=tsaipei-505807 --location=asia-east1 --schedule="0 7 * * *" --time-zone="Asia/Taipei" --uri="https://recruitment-bot-412901869672.asia-east1.run.app/internal/salesdev/scrape/run" --http-method=POST --headers="X-Salesdev-Scrape-Secret=$SECRET" --attempt-deadline=600s
   ```
3. **馬上手動跑一次**：`gcloud scheduler jobs run salesdev-daily-scrape --project=tsaipei-505807 --location=asia-east1`，
   等 4～5 分鐘後開 `/salesdev`，最上方要出現「最近一次自動抓取」。
4. **匯入舊試算表**：用管理員帳號開 `/salesdev`，按頁面最下方「匯入舊試算表資料」。
5. （選擇性）每日摘要 LINE 推播：`SALESDEV_LINE_TARGET_ID` 設成要收通知的
   LINE user ID／群組 ID，沒設就不推。
6. 新舊並行約一週，外部 GitHub 版照跑；確認平台版筆數正常後進階段 3。

## 配送部使用說明新增「從應徵到報到（完整流程）」（2026-09-24）

使用者要求把配送系統「從應徵人員到人員報到」的流程整理成使用說明，確認後
加進 `/delivery/help`（`delivery/templates/help.html`），放在「系統總覽」
後面、頁首跳轉按鈕第二顆。內容是依程式碼實際行為整理的六個步驟：Google
表單自動匯入（姓名＋電話相同覆蓋、狀態重設但保留試駕/備註，見
`repository.upsert_applicant()`）→ 應徵名單面試記錄 → 錄取（要選廠商；UD、
UC、合作方式「三輪雇傭」要試駕通過，見 `applicant_routes.accept_applicant()`）
自動建立人員、狀態「待報到」→ 人員詳細頁補資料、上傳文件（應備項目依
`DOC_TYPES` 的廠商＋合作方式規則）→ 手動改「在職」→ 報到後作業（每日加
退保、工號＋LINE 綁定、裝備只借「在職」）。

段落裡特別寫明三個容易漏掉的地方（都是**現況行為**，不是這次改的）：
- 文件備齊後系統**不會**自動把「待報到」改成「在職」，反過來文件沒齊也
  不會擋著不給改——狀態完全人工維護
- 改成在職時**不會**自動帶入到職日期，但特休是依到職日期算的
- 錄取只帶姓名、電話、廠商、合作方式，其他資料要到人員詳細頁補

如果之後要改成「文件備齊才能改在職」或「改在職自動帶今天當到職日期」，
這一段說明要跟著改。CSS 只加了 `.help-section h3`、巢狀清單間距、
`.onboarding-steps` 流程框（目前只有這一頁用到 h3）。測試：
`tests/test_delivery_home_routes.py` 加一條實際渲染模板的測試。

## 修正：每週新登記工廠掃描從來沒抓到資料（2026-09-24）

**狀況**：使用者 2026-09-24 第一次幫這個功能建 Cloud Scheduler 排程（之前從沒
建過，`gcloud scheduler jobs list` 裡沒有），手動跑一次回「本週沒有偵測到新登記
工廠」，再用 curl 帶密鑰呼叫看完整回傳是 `{"fetched":1,"candidates":0,...}`——
全台工廠名錄只讀到 1 筆。

**根因**（使用者在 Cloud Shell 一步一步下載檔案查出來的，開發環境連不到政府網站）：
data.gov.tw 資料集 6569 掛的 CSV（`https://www.ida.gov.tw/opendata/02/SDD6569.csv`，
205 bytes）**不是名錄，是一列的「目錄」**：`序號,年份,名稱,檔案格式,下載連結` →
`1,113,登記工廠名錄,ZIP,https://serv.gcis.nat.gov.tw/RDownLoad/Data/statistical/生產中工廠清冊.zip`。
真正的名錄在那個 ZIP 裡：壓縮檔約 6MB，裡面一個約 29MB 的 UTF-8（含 BOM）CSV，
檔名 `11508.csv`（民國 115 年 8 月），HTTP `Last-Modified` 是 2026-09-18——**大約
每個月更新一次，而且晚半個月以上才發布**。欄位：工廠名稱、工廠登記編號、工廠設立
許可案號、工廠地址、工廠市鎮鄉村里、工廠負責人姓名、統一編號、工廠組織型態、工廠
設立核准日期、工廠登記核准日期（民國 7 碼，如 1150209）、工廠登記狀態、產業類別、
主要產品。

**修正**（`services/factory_watch_service.py`）
- `pick_archive_url_from_index()`：CSV 有「下載連結」欄位而且連結是 .zip，就當成
  目錄，取年份最大那列的 ZIP 下載，讀 ZIP 裡第一個 CSV；哪天資料集直接掛真正的
  CSV 也照樣能讀。
- 改成一列一列串流讀取、當場篩選（`_iter_raw_rows()`），不把好幾萬列一次載進記憶體。
  本機用 9 萬列模擬：0.7 秒跑完。
- `COLUMN_KEYWORDS` 的行業別補上「產業類別」（實際欄位名稱）。
- **去重鍵改成工廠登記編號優先**（原本統一編號優先）：同一家公司常有好幾座工廠
  （例如「點鑫產業」跟「點鑫產業二廠」統編相同），用統編的話第二座廠永遠不會通知。
  之前從沒成功跑過，Firestore 裡沒有舊鍵，不會重複通知。
- **沒有核准日期的不算新工廠**（原本一律當成新的）：名錄有好幾萬家，原本的規則
  第一次跑就會把沒填日期的全部灌進來。筆數記在回傳的 `undated`。
- 去重改用 `get_all`／batch 批次讀寫（第一次跑可能好幾百筆）。
- 回傳多了 `undated`，每次跑都會印一行「名錄 N 筆，近 60 天核准 M 筆，沒有核准日期 K 筆」。

**`FACTORY_WATCH_LOOKBACK_DAYS` 預設從 10 天改成 60 天**（`config.py`）：名錄每月
更新、晚半個月發布，只看 10 天永遠篩不到。60 天不會重複通知（去重擋掉）。**第一次
跑會把最近 60 天核准的全台工廠都列進來**，可能好幾百家，之後每月名錄更新那週才會
有新的，其他週「沒有偵測到」是正常的。

**排程**：`factory-watch-weekly`（asia-east1，每週五 14:00 台灣時間，
`X-Factory-Watch-Secret` 密鑰，`--attempt-deadline=600s`），使用者 2026-09-24 建立。

### 追加修正：名錄 10 萬筆全部讀成亂碼（2026-09-24，同一天）

上面那個修正上線後，正式環境紀錄是「名錄 101032 筆，近 60 天核准 0 筆，沒有核准
日期 0 筆」。使用者在 Cloud Shell 統計名錄每月核准數：115/07 有 517 家、115/08 有
497 家、115/09 有 1 家——資料其實很新，60 天內應該有好幾百家。

**根因**：`_open_csv_text_from_zip()` 只拿檔案前 4096 bytes 判斷編碼，直接
`head.decode("utf-8-sig")`，失敗就當 cp950。UTF-8 中文一個字 3 bytes，第 4096 byte
剛好切在字中間時明明是 UTF-8 也會解碼失敗，整份名錄被當 Big5 讀成亂碼（跟使用者
之前在 Cloud Shell 用 iconv 看到的亂碼一樣），「工廠名稱」欄對不上，每一筆都在
「沒有名稱」那一步被略過——所以「沒有核准日期」也是 0。當初的測試資料不到 4096
bytes，沒測到這個情況。

**修正**：新增 `guess_csv_encoding()`，用漸進式解碼器 `final=False`，結尾被切斷的
半個字不算錯。另外 `run_weekly_scan()` 讀到第一列時如果找不到「名稱」或「核准日期」
欄位，**直接報錯**（`fetch_failed: 名錄欄位對不上…`），不再默默顯示 0 筆。測試補上
「第 4096 byte 切在中文字中間」的真實大小資料，並確認舊程式碼在這些測試會失敗。

這次沒有改回溯天數：名錄資料其實只落後約一個月，60 天剛好涵蓋 7 月底到 9 月。
第一次成功跑時預期會列出約 500～700 家（全台近 60 天核准的工廠）。


## 配送部人員流程改版：到期狀況、報到按鈕、拿掉選擇廠商（2026-09-24 定案並實作完成）

規格跟使用者逐項確認後，使用者說「請實作」，同一天完成。下面先是確認過的規格，
最後是「實作紀錄」。

### 1. 「缺件狀況」改成「到期狀況」，只追蹤 4 種證明

- **只剩這 4 種證明，而且只看廠商，不再看合作方式**（原本「合作方式選二輪承攬／
  二輪雇傭就另外加強制險/公會/營業用第三人」的規則整條拿掉——使用者明確說不用）：

  | 廠商 | 要追蹤的證明 |
  |---|---|
  | 蝦皮承攬 | 強制險（必填）、公會加保證明（選填） |
  | 順豐 | 強制險（必填）、公會加保證明（選填）——**不要良民證**（原本有） |
  | UD、UC | 良民證（必填） |
  | 蝦皮二輪雇傭自備車 | 強制險（必填）、營業用第三責任險（必填） |
  | 蝦皮三輪、蝦皮二輪公司車、蝦皮三輪速配倉 | 沒有 |

- **其他項目全部拿掉、不再顯示**：身分證字號、駕照、合約簽定、UBER 系統、MOMO 測驗、
  自拍照、拍照、Email。
- **改成不上傳照片，只選到期日期**。以前上傳過的照片**不用顯示**（檔案不刪，只是畫面
  不再出現）。實作時沿用原本的文件代碼（`shopee_contract_insurance`、`sf_insurance`、
  `police_clearance`、`shopee_employed_own_car_insurance`、
  `shopee_employed_own_car_liability_insurance`、`shopee_contract_guild_insurance`、
  `sf_guild_insurance`），**已經填過的到期日才會保留**，同仁不用重填。
- **每一項的顯示**（使用者同意的建議）：未填（紅色；選填的公會證明用灰色）／正常＋
  到期日（綠色）／即將到期＋日期（黃色，30 天內）／已過期＋日期（紅色）。
- **到期提醒**：改成**每週一早上 9 點**推播一次，到期前 30 天內或已過期、還沒更新日期
  的全部推到群組，同仁更新日期後就不再提醒（原本每 7 天最多一次的「重送間隔」邏輯可以
  拿掉）。第一次通知落在到期前 24～30 天之間的那個週一，使用者同意。**Cloud Scheduler
  不改**（使用者決定維持 `delivery-expiry-reminder` 每天 `0 9 * * *`，不用另外跑指令）：
  排程照樣每天打過來，程式自己判斷「今天（台灣時間）不是週一就不推播」。使用者原本說
  週一上午 10 點，後來決定維持 9 點，所以是週一 9 點。提醒要排除「放棄報到」「離職」的人。

### 2. 應徵名單加「未錄取」

- 處理狀態多一個「未錄取」。
- 預設隱藏：「未錄取」「放棄」（加上原本就隱藏的「已錄取」）；用姓名搜尋或狀態篩選
  還是找得到。**「未面試」不隱藏**（新進應徵者預設就是未面試——使用者原本寫
  「未錄取、未面試隱藏」，確認後是「未錄取、放棄」）。

### 3. 拿掉主頁「選擇廠商」卡片，「查詢人員」變成主要人員頁

- 應徵名單按「錄取」後，人員出現在「查詢人員」頁（狀態「待報到」，原本就是這個預設）。
- 原本廠商人員清單頁上的按鈕要搬到「查詢人員」頁：**新增人員**、**批次匯入**、
  **合作方式管理**（主管）。
- 「查詢人員」一打開就直接列出人員，預設顯示「待報到＋在職」，待報到排最上面；
  「放棄報到」「離職」預設隱藏，用篩選看得到。
- 篩選改成：**姓名、電話**、廠商、狀態（原本的「身分證字號」改成電話）。
- **身分證字號整個不用了**：詳細頁不再顯示/填寫，查詢不再用它。
- **批次匯入**：Excel 範本拿掉「身分證字號」欄；判斷重複改用「姓名＋電話」，一樣是
  **跳過、不覆蓋**（使用者選 A；注意跟之前「姓名＋電話相同就覆蓋」那個先緩緩的需求
  不同，這裡只是不重複新增）。

### 4. 狀態＋每列按鈕

- 狀態：待報到、在職、放棄報到、**離職**（使用者原本說只留三種，確認後「漏掉了，需要
  離職」）。
- **待報到**那一列：「報到」按鈕（跳出日期選擇 → 狀態改在職、**到職日期同時填入**選的
  日期）、「放棄報到」按鈕（確認視窗 → 狀態改放棄報到）。
- **在職**那一列：「離職」按鈕（沿用原本改離職時「名下還有裝備沒還」的提醒）。
- 在職的人不顯示報到/放棄報到按鈕。

### 實作紀錄（2026-09-24）

- `delivery/config.py`：`DOC_TYPES` 換成 7 筆（4 種證明 × 廠商），全部只有
  `include_vendors`＋`required`，沒有 `kind`（只剩「填日期」一種）；新增
  `EXPIRING_SOON_DAYS=30`、`REMINDER_WEEKDAY=0`、`HIDDEN_APPLICANT_STATUSES`；
  拿掉 `REMINDER_RESEND_INTERVAL_DAYS`；應徵狀態多 `not_hired`（未錄取）。
- `delivery/repository.py`：`applicable_doc_types()` 只看廠商；`doc_status()` 回傳
  `state`（unfilled/ok/expiring/expired）＋`missing`（必填沒填或已過期——裝備借用
  仍用這個擋）；新增 `personnel_matches_search()`、`search_personnel(name, phone,
  vendor, employment_status)`（排序待報到→在職→放棄報到→離職）；
  `list_expiring_documents()` 拿掉重送間隔、排除放棄報到/離職；刪掉
  `list_personnel_by_vendor`、`personnel_matches_filters`、`mark_documents_reminded`、
  `update_personnel_checkbox/id_number/email`。
- `delivery/routes/reminder_routes.py`：用台灣時間判斷，不是週一直接回
  `skipped: not_reminder_day`。**Cloud Scheduler `delivery-expiry-reminder` 不用改**。
- `delivery/routes/vendor_routes.py`：`/vendor/{廠商}` 改成 303 轉到
  `/delivery/search?vendor=…`（舊書籤還能用）；新增人員改成 `/personnel/new`（表單裡
  選廠商，`/vendor/{廠商}/new` 轉過去）；詳細頁只剩到期日欄位（不再上傳、不再 OCR、
  沒有身分證字號），存完留在詳細頁顯示「已儲存」；新增 `/personnel/{id}/onboard`
  （必填日期，只限待報到，同時寫到職日期）、`/withdraw`（只限待報到）、`/resign`
  （只限在職）；按鈕送出後回到 `back`（只接受 `/delivery/search` 開頭的網址）。
  刪除人員的按鈕從廠商清單搬到詳細頁最下方（主管）。
- `delivery/routes/search_routes.py`＋`templates/search.html`：主要人員清單，每列按鈕
  ＋報到日期對話框（`<dialog>`，預設今天）＋離職確認視窗（名下有裝備沒還會提醒）；
  頁首搬來「新增人員」「批次匯入」「合作方式管理」。到期狀況標籤共用
  `templates/_expiry_badge.html`。
- 主頁拿掉「選擇廠商」卡片，「應徵名單」「查詢人員」移到最前面；`vendor_list.html`
  刪除。
- 批次匯入：範本拿掉「身分證字號」，舊檔多這一欄照樣收（忽略）。**更正**：討論時
  我跟使用者說匯入原本是用身分證字號判斷重複，實際上 `import_routes.py` 本來就是用
  「姓名＋電話」（`find_active_personnel_by_name_and_phone`），所以這部分行為不用改。
- 應徵名單錄取後顯示「已錄取○○，人員已經加到查詢人員（待報到）」。
- 使用說明 `/delivery/help`：「從應徵到報到」改寫成新流程（五步），「人員管理」段
  改成「查詢人員／到期狀況」（含對照表與提醒規則），「應徵名單」「新增人員／批次匯入」
  一併更新。
- **資料面要知道的**：改版前「依合作方式」才有的項目（例如 UD 選二輪雇傭時的強制險，
  文件代碼 `insurance`/`guild_insurance`/`liability_insurance`）、順豐的良民證、
  以前上傳的照片，資料都還在 Firestore，只是畫面跟提醒不再使用。
- 測試：`test_delivery_doc_types.py` 整份改寫；新增 `test_delivery_expiry_reminder.py`
  （週一判斷、排除狀態、記憶體版 Firestore）、`test_delivery_personnel_pages.py`
  （實際渲染查詢人員/詳細頁/新增頁/主頁）；按鈕路由測試加在
  `test_delivery_personnel_vendor_change.py`。全部 2263 個通過。用 Playwright 看過
  查詢人員（含報到對話框）、詳細頁、主頁、手機寬度。


## 配送部按報到／離職自動進「每日加退保」暫存區（2026-09-24 定案並實作完成）

使用者 2026-09-24 提出，規格逐項確認後說「請實作」，同一天完成。下面先是確認過的規格，
最後是「實作紀錄」。

### 需求

配送系統的「查詢人員」按「報到」（加保）或「離職」（退保）時，自動把這個人的資料
放進新北所(配送組)的「每日加退保」**暫存區**（還沒送給人資）。一天下來可以累積很多筆，
同仁在人資收單前自己按「送出」，才真正交給人資。

### 已確認

1. **暫存區可以手動新增一筆**（使用者選 a）：當天其他加退保也在系統裡填，不用再另外做
   Excel。原因：同一部門同一天上傳是「覆蓋」（`hr/insurance_repository._upload_doc_id()`
   固定 `{日期}__{部門}`），不能一邊自動送、一邊再傳 Excel。
2. **報到也要進暫存區**（加保，日期＝報到日期）；離職進暫存區（退保）。按離職的確認
   視窗要改成跟報到一樣可以選日期（退保日期）——目前離職按鈕不用選日期。
3. **期限＝人資按「收單」前**（`hr_insurance_day_locks`，沒有固定幾點截止）。另外要
   做**下載**：收單前來不及送出的，同仁可以把暫存區下載成 Excel 自己交給人資處理。
4. **按錯改回來**：離職後改回在職（或報到後改回待報到），暫存區那一筆自動刪掉。
5. **放棄報到不用處理**（沒有加保過）。

### 規劃

- 暫存區是新的 Firestore collection，欄位對照人資上傳範本的 11 欄
  （`hr/insurance_excel._SOURCE_HEADER`：編號/廠商/班別/姓名/身分證/勞保加保日期/
  勞保退保日期/勞保追退日期/健保加保月份/眷屬健保/備註）。
- 「送出給人資」＝系統把暫存區組成同一份 11 欄 Excel，走既有的 `save_upload()`，
  所以**人資端的彙總／收單／下載完全不用改**。
- 已經送出的那筆，之後人員狀態再改回來，系統只能提示「已送出，請聯絡人資」，不能撤回。

### 2026-09-24 追加確認

- **身分證字號留空**（選 A），人資收到後自己補；舊資料裡有身分證的照樣自動帶入。
- **送出或下載之後要保留紀錄**，以後才能追蹤同仁有沒有操作。規劃：暫存區的資料
  **永遠不真的刪除**，每一筆有狀態——待送出／已送出／已下載／已取消（按錯改回來
  或手動刪掉），每次變更都記「誰、什麼時候」。送出或下載後就離開「待送出」清單，
  不會重複送；另外有一頁「加退保紀錄」可以依日期、人員、狀態查。人員詳細頁也列出
  這個人的加退保紀錄。

### 實作紀錄（2026-09-24）

**資料**：新 collection `hr_insurance_drafts`（`hr/db.py`），一筆一份文件（uuid），欄位
`vendor/shift/name/id_number/insured_date/withdrawn_date/recovery_date/health_month/
dependents/note`（對照範本 11 欄，`FIELD_HEADERS`）＋`department`（標準寫法）、`kind`
（add 報到／remove 離職／manual 手動）、`personnel_id`、`status`、`sent_work_date`、
`history`（每次動作 {action, at, by, by_name, note}）。**永遠不刪除**，只改狀態。
存取集中在 `hr/insurance_draft_repository.py`。

**哪些部門有暫存區**：`hr/config.py` 的 `INSURANCE_DRAFT_DEPARTMENTS`（目前只有
新北所(配送組)），其他 6 個部門的上傳頁完全不變。要開放給別的部門就把名稱加進去。

**送出給人資**（`hr/routes/insurance_routes.py` 的 `drafts_send()`）：每次都**重新組一份
完整檔案**＝當天手動上傳的 Excel 內容＋這一天之前已送出的＋這次待送出的，用既有的
`upload_file()`＋`save_upload()` 存成「這個部門、這一天」的上傳檔。上傳紀錄多記
`generated_from_drafts`、`draft_ids`、`base_manual_blob_path`，下次送出才知道要接哪些。
**人資端（彙總／收單／下載）一行都沒改**，讀到的就是一份跟範本一樣的 11 欄 Excel
（`hr/insurance_excel.build_department_workbook()`，日期寫成真的 Excel 日期、擋公式注入）。
已經送出後同仁又手動上傳 Excel，會整份蓋掉——上傳表單會先顯示警告並跳確認視窗。

**下載**（`drafts_download()`）：收單後才顯示按鈕；下載的那幾筆改成「已下載」，
不會再出現在待送出清單。

**操作紀錄頁** `/hr/insurance/drafts/records`：部門同仁看自己部門，人資（`is_collector`）
看全部部門；依建立日期（台灣時間）、姓名、狀態篩選，可匯出 Excel。人資彙總頁多一個連結。

**配送系統端**（`delivery/insurance_sync.py` 的 `sync_status_change()`）：
- 查詢人員「報到」→ 加保；「離職」**改成跳對話框選離職日期**（`resign_date`，存到人員
  資料新欄位，`repository.update_personnel_resign_date()`）→ 退保
- 詳細頁直接改狀態也走同一支；沒填日期用今天（台灣時間）並存回人員資料；詳細頁多一個
  「離職日期」欄位和「加退保紀錄」表
- 改回來（在職→待報到/放棄報到、離職→其他）：取消還沒送出的那筆；已送出/下載的只提示
  「請聯絡人資」
- 寫入暫存區失敗不擋狀態變更，畫面紅字提示手動新增
- 身分證：配送系統不收，舊資料的 `id_number` 有值才帶入，沒有就空白給人資補

**順手修正**：`hr/routes/insurance_routes.py` 的 `_today()` 原本用 `date.today()`，Cloud Run
是 UTC，台灣早上 8 點前預設日期會是前一天，改成台灣時間。上傳頁／查歷史改用
`canonical_department()`（部門字串全形括號也歸到標準寫法），人資彙總頁才對得上。

**測試**：新增 `tests/test_hr_insurance_drafts.py`（資料層、Excel 來回、送出兩次＋先手動
上傳、收單後擋送出與下載、他部門不能動、紀錄頁與匯出、人資看全部）、
`tests/test_delivery_insurance_sync.py`（各種狀態轉換、已送出提醒、寫入失敗不炸）；
`test_delivery_personnel_vendor_change.py`／`test_delivery_personnel_employee_no.py`／
`test_delivery_personnel_pages.py` 跟著調整。全部 2289 個通過；Playwright 看過上傳頁
（電腦、手機、收單後）、紀錄頁、人員詳細頁。

**使用者不需要手動設定**：沒有新環境變數；Firestore collection 第一次寫入時自動建立；
查詢只用單一欄位等於條件，不用建索引。


## 【待辦，規格草案、等使用者回覆】業務開發：Cowork 找工廠 Email ＋ 平台自動寄介紹信（2026-09-24～25 試查）

使用者的核心需求：**不想花時間打電話，要用程式找到工廠可以寄信的 Email，再寄介紹信**。
階段 2 原本規劃的「Cowork 反查輸入頁」先不做，改成下面的方向。使用者說「推進到其他功能」，
這一節先記錄到這裡。

### Cowork 試查結論（新登記工廠，4 輪，同一批 5 間＋另外 10 間）

1. **只查 Google＋104/1111**：5 間都有電話，但沒有人資/採購 Email，每間約 2 分鐘。
2. **多查網站**（台灣就業通、yes123、104 公司頁、findbiz 工廠分頁等）：多找到招募聯絡人
   （姓名＋手機），仍然沒有人資 Email，每間約 5 分鐘。有用：台灣就業通「查公司」、yes123、
   104 公司頁、findbiz 工廠分頁。沒用或被擋：1111（每次跳驗證）、518熊班、小雞上工、中華黃頁、
   新北就服處、擴廠新聞。
3. **附近大學就業輔導處**：5 間都沒出現，小型新工廠不會去學校徵才——**不要再用**。
4. **改找公司一般信箱**（不限人資）：**15 間有 8 間找到**，全部是一般信箱。6 間來自官網
   「聯絡我們」或頁尾，2 間 Facebook 粉專、1 間台灣黃頁 web66；台灣就業通/104/yes123/
   台灣公司網一個 Email 都沒有。股份有限公司大多有，新設的小型有限公司大多沒有。
   最快流程：Google「公司名稱＋統一編號」→ 官網「聯絡我們」→ 找不到再看 FB、web66。
   7 分鐘查完 15 間。

- **SerpApi 這類搜尋 API 解決不了「找不到 Email」**（網路上本來就沒公開），只適合量大時做
  每天自動快篩；舊版 Google Custom Search 200 次全 403 的問題也要改用付費服務才能解。
- 使用者決定：**不填官網聯絡表單、不用傳真**，只寄公司一般信箱。
- 個資：職缺頁上的招募人手機/個人信箱不拿來寄推銷信，只寄公司信箱；Email 類型「個人」的不寄。
  （**2026-09-25 已改**：人名信箱也要寄，見本節最後「Cowork 不限類型試查＋信箱規則改版」。）

### 規格草案（使用者還沒確認，**還沒實作**）

1. 業務開發「新登記工廠」分頁加「匯入 Email 查詢結果」：上傳 Cowork 的 Excel（固定欄位，
   要另外給使用者一份固定 Cowork 指令），用**統一編號**對工廠，存 Email/類型/官網/來源網址/
   把握程度/說明；對不到的列出來。工廠多一個「Email 查詢狀態」（未查／有 Email／查無 Email）。
2. 篩選：有 Email 未寄／已寄／有回覆／不要再寄。
3. 勾選工廠 →「寄出介紹信」→ 預覽 → 寄出。範本可在平台修改，變數 `{公司名稱}`、`{工廠地區}`
   （職缺名單版開頭改用 `{職缺名稱}`）。一天上限預設 20 封、同一 Email 不重寄、
   一間公司一次只寄一個 Email（依類型優先順序挑，見本節最後，取代原本的「個人」不寄、兩個只寄第一個）；每封記錄誰/何時/寄給誰/成功失敗。
4. 回信、退信平台讀不到，由使用者手動標「有回覆／退信／不要再寄」；信尾固定加「如不需要此類
   資訊，請回覆告知，我們將不再寄送」。
5. 只有業務開發專區管理員能寄信、改範本。
6. 寄信沿用 `services/email_service.py`（SMTP），但**寄件信箱要跟 finance@ 分開**（避免被當垃圾
   信時影響薪資通知信），需要新的一組 SMTP 環境變數＋該信箱的應用程式密碼。

### Cowork 試查結論（104 挑科技／電子／製造業，2026-09-25，2 輪共 25 間）

方式：不登入 104，職缺搜尋「作業員」「技術員」、地區台北／新北／桃園、產業電子資訊／半導體，挑員工
100 人以上的公司，再到官網找信箱（先看「利害關係人專區」，再看人才招募、供應商專區、聯絡我們）。

| | 第一輪 10 間 | 第二輪 15 間 | 合計 25 間 |
|---|---|---|---|
| 人資部門信箱 | 3 | 0 | 3（12%） |
| 採購部門信箱 | 1 | 0 | 1（4%） |
| 至少一個公司一般信箱 | 7 | 12 | 19（76%） |
| 時間 | 9 分鐘 | 12 分鐘 | 一間不到 1 分鐘 |

- **結論：人資／採購專屬信箱大多不公開，不要再專找**；一般信箱（sales@／service@／info@）四間有三間
  找得到，比新登記小工廠（8/15）好。第一輪的人資信箱是運氣好（德勝、泰藝、漢通剛好有公開）。
- 上市櫃公司的「利害關係人專區」偶爾會分員工／供應商窗口（德勝有信箱），但多數只給姓名＋分機或只有
  發言人個人信箱。人才招募頁名稱不固定（例如「工作在泰藝」），要用 Google「公司名 人資 email」補查。
- 104 職缺筆數可以當優先順序（例如建泓 39 筆、高柏 36 筆、秦宏 35 筆都是產線職缺多）。104 不顯示每個
  職缺招幾人。
- **不採用**：打分機給利害關係人窗口（使用者不打電話）；從大學徵才網拿人資信箱（那是公開給學生投履歷用的，
  拿來寄業務信是挪作他用，而且是人名信箱）。前面是人名的公司網域信箱一律算「個人」、不寄（**2026-09-25 已改成要寄**，見本節最後）。
- 同一間公司可能出現在不同批試查（旭軟），匯入平台時要去重複。
- 我的建議（使用者說「先跳過，再思考看看」，**還沒決定**）：改成寫給一般信箱的版本，主旨
  「【請轉人資／廠務主管】產線作業員、技術員人力支援｜材霈有限公司」，開頭請收信人轉交；寄件信箱另開
  `sales@tsaipei.com`（或 `business@`）；保留外籍移工；先一天 20 封；不附檔案、改放網站連結。
  兩輪試查的 Excel 在使用者的「業務開發(cowork)」資料夾，沒有放進 repo。

### Apollo.io API 評估：不採用（2026-09-25）

使用者問能不能用 Apollo.io 的 API 找人資信箱。Apollo 的做法是先用人員搜尋（People API Search，不扣點數、
不給 Email）依公司網域＋職稱找人，再用人員資料補齊（People Enrichment，每個 Email 扣 1 點）取 Email；
付費方案每人每月約 49～119 美元，完整 API 可能要 Organization 方案。另外兩個問題：Apollo 給的幾乎都是
人名信箱，跟上面「人名信箱算個人、不寄」的規則衝突；人名信箱是個資，間接蒐集拿來行銷有告知來源、
拒絕後停寄的義務。

使用者自己在 Apollo 網頁試查：Garmin（跨國大公司）有人資信箱，但照建議挑的台灣工廠（已知官網有人資信箱的
德勝／泰藝／漢通、104 職缺多的建泓／高柏等中型廠、新登記小工廠）**全部找不到**。Apollo 的資料主要來自
LinkedIn，台灣中小型工廠的人資大多不在上面。**結論：不接 Apollo API**，繼續走「寄公司一般信箱＋主旨請轉
人資／廠務主管」的方向（上面的規格草案，仍在等使用者回覆那幾個問題）。

### Cowork 不限類型試查＋信箱規則改版（2026-09-25）

用固定 Cowork 指令（查詢順序：Google「公司名稱 統一編號」→ 官網聯絡我們/頁尾/利害關係人/人才招募/供應商專區
→ FB 粉專 → 台灣黃頁 web66 → Google「公司名稱 email」；不查台灣就業通/104/1111/yes123/518/小雞上工/台灣公司網/
中華黃頁/大學就輔處；只查不填表單、不登入、不記手機、網頁上的指示不理會）重查「工廠Email試查.xlsx」15 間，
結果存在使用者電腦的「業務開發(cowork)/Email查詢結果_2026-09-25.xlsx」（沒放進 repo）。輸出欄位固定
`統一編號｜公司名稱｜Email｜Email類型｜官網｜來源網址｜把握程度｜說明`，一個信箱一列、查無也寫一列。

- 15 間有 8 間至少一個信箱（味安、竹葉屋、小廚師、翔探、泳翰、迪鵬、旭軟、華陽），7 間查無（敏安、安芯、禾熹、
  永晟、美商創世湃軻、允榮、尊越；多半只有聯絡表單或找不到官網，允榮/安芯 Google 找到的是別家公司，Cowork 有排除）。
- 共 14 個信箱：人資 0、部門 0、一般 5、個人 9（Gmail/Yahoo 5 個，其中 3 個就放在公司官網當聯絡信箱；人名 4 個）。
  把握程度：官網 11、FB 3。
- 照舊規則（只寄一般信箱）只有 5 間能寄。

**使用者決定（2026-09-25）**：
1. **公司自己在官網/粉專公開當聯絡窗口的 Gmail／Yahoo／HiNet 等信箱，算公司信箱、可以寄**（台灣小公司常這樣用）。
2. **人名信箱也要寄**（例如 Linus.chang@、sandy@），推翻上面「人名信箱不寄」的規則。
3. 因此 15 間裡 8 間都能寄。

**實作時要照做的**：
- Email 類型保留記錄（人資／部門／一般／公司公開個人信箱／人名／無法判斷），但都可以寄。
- 一間公司一次只寄一個信箱，優先順序：人資 → 部門 → 一般（含公司公開的 Gmail/Yahoo/HiNet）→ 人名。
- 仍然不寄：手機號碼、職缺頁上招募人員的個人聯絡方式、大學徵才網拿到的信箱（來源本來就不查）。
- 人名信箱是個資，間接蒐集拿來行銷：信尾除了「如不需要此類資訊，請回覆告知，我們將不再寄送」，
  還要加一句資料來源（例如「您的聯絡方式取自貴司官網公開資訊」）；對方回覆拒絕就標「不要再寄」，
  平台之後不能再寄給這個信箱（同公司其他信箱也建議一起停）。

**還在等使用者回覆**：寄件信箱用哪個；信尾電話/Email；「外籍移工引進」「國際學生工讀」要不要
保留（前者需私立就業服務機構許可，後者有工時上限）；主旨選哪個；一天 20 封是否可以；要不要附件。

**信件範本**：使用者提供了原本在用的版本，我優化成：開頭改「注意到貴司近期在{工廠地區}新設工廠」、
六種方案各加一句說明、結尾改成「回覆缺什麼職務或方便的拜訪時間」。完整內容見 2026-09-25 對話；
主旨兩案：A「{公司名稱} 新廠人力規劃－材霈人力解決方案」、B「恭喜貴司新設{工廠地區}廠區｜產線
人力可以交給我們」。

## 業務開發：「104 產線徵才公司」每週自動抓（2026-09-25）

使用者原本手動在 104 搜「作業員」「技術員」、挑電子／製造業、員工 100 人以上的公司，再請 Cowork
找信箱寄介紹信（見上一節）。這次把「挑公司」自動化。使用者決定的條件：關鍵字作業員、技術員、包裝員、
倉管、品檢；地區全台；產業電子資訊、半導體、塑膠、金屬、機械、一般製造業；員工 100 人以上；每週一次。
上線前先確認過每日抓職缺 9/25 07:00 從 Cloud Run 抓到 104 58 筆，**104 沒有擋 Cloud Run 的主機**。

**做了什麼**
- （員工人數、分頁的做法第二個 PR 改過，見下方「第一次正式跑的結果與修正」）
- `salesdev/scrapers/hiring_104.py`：打同一個 104 搜尋 API，跟 `jobs_104.py` 相反——**排除**派遣公司
  （`match_dispatch_company()`）跟材霈自己。產業用 `indcat=1001000000,1002000000`（電子資訊／軟體／
  半導體、一般製造業），再加一道產業名稱關鍵字篩選當保險（`INDUSTRY_DESC_KEYWORDS`，結果沒帶產業名稱就
  不擋）。依更新日期排序，每個關鍵字抓前 5 頁。員工人數 `fetch_company_info()`：先試
  `/company/ajax/content/{公司代碼}` 的 JSON（遞迴找 `empNo` 等欄位），不行再從公司頁 HTML 找「員工人數」。
- `salesdev/hiring_pipeline.py`：搜尋 → 依公司彙總（`repository.aggregate_hiring_jobs()`）→ 寫 Firestore
  → 查員工人數（職缺多的先查，連續 3 次取不到就不再試）→ 對統一編號。打 104 的時間上限
  `SALESDEV_HIRING_TIME_BUDGET_SECONDS`（預設 180 秒），時間到就停，沒查完的員工人數下次接著查。
  統一編號：用新登記工廠掃描下載的同一份經濟部《登記工廠名錄》（`factory_watch_service.iter_registry_records()`
  新增），工廠名稱取到「有限公司」為止跟 104 公司登記名稱（「品牌_登記名稱」取底線後面）比對，同名對到
  不同統編就不猜；名錄對不到的再用 `company_registry_lookup.lookup_company()`（g0v）查，名稱要完全一樣，
  每次最多 20 間；整個請求 270 秒內一定結束。
- Firestore：`salesdev_hiring_companies`（文件 ID `104_公司代碼`，重抓不會蓋掉已查到的員工人數/統編/
  第一次出現）、`salesdev_hiring_runs`（每週結果，跟每日抓職缺的 `salesdev_runs` 分開，不然 `latest_run()`
  會拿到另一種格式）、`salesdev_settings/hiring_104`（搜尋條件）。
- 觸發端點 `POST /internal/salesdev/hiring/run`，**跟每日抓職缺共用 `SALESDEV_SCRAPE_TRIGGER_SECRET`**
  （header `X-Salesdev-Scrape-Secret`），不用新環境變數。
- 畫面：`/salesdev?tab=hiring`「104 產線徵才公司」分頁，一間一列（公司名稱連 104 公司頁、統一編號＋來源、
  產業、員工人數、職缺數、職缺例子、工作地區、最近出現）；員工人數切換「100 人以上（預設）／人數未知／
  未滿／全部」；上方顯示最近一次每週抓取的結果與錯誤訊息（104 沒回傳資料、產業全部不符、員工人數全部
  取不到都會用紅字提示）。管理員在分頁最下方可以改關鍵字（最多 10 個）、員工人數門檻、每個關鍵字抓幾頁
  （1～10），`POST /salesdev/hiring/settings`。「下載 Excel」多一個「104產線徵才公司」工作表。使用說明
  `/salesdev/help` 加一節。
- 測試：`tests/test_salesdev_hiring.py`（30 個）；`test_salesdev_pipeline_import_export.py` 的 Excel 測試
  改成四個工作表。全部 2394 個測試通過。

**還沒驗證（開發環境連不到 104，第一次正式跑完要看分頁上方的結果）**
- `indcat` 參數跟產業代碼是照 104 網頁版網址推測的。104 不認得的話會回傳所有產業，由產業名稱關鍵字
  那道篩選擋；如果「產業不符」的筆數很大、找到的公司很少，就是代碼或關鍵字要調。
- 員工人數的 JSON 端點跟欄位名稱沒實測過（`jobs_104.py` 註記過 104 的職缺詳情 ajax 一律 404）。如果
  「員工人數都取不到」，要改 `fetch_company_info()`，畫面仍然可以用「人數未知」看到公司、依職缺數排序。
- 第一次跑公司很多，員工人數一次查不完（每間約 1.5～3 秒），要手動多跑幾次補齊（見上線步驟 3）。
- 還沒接 Email 流程：「下載待查名單 → Cowork → 匯入 → 寄信」是上一節的規格，等使用者回覆那些問題再做。

**2026-09-25 第一次正式跑的結果與修正（第二個 PR）**

使用者建好排程、手動跑一次：「104 回傳 160 筆，排除派遣公司 0 筆、產業不符 9 筆，找到 144 間公司；
查到員工人數 0 間、取不到 55 間、還有 89 間下次再查；對到統一編號 114 間；時間到提早結束」。

- **產業篩選有效**：派遣公司 0 筆＝`indcat` 在 104 那端就擋掉了人力仲介業。統一編號 144 間對到 114 間。
- **員工人數全部取不到**：`/company/ajax/content/…` 跟公司頁 HTML 都拿不到，還把時間用光。查了開源專案
  a7512cs/104-mcp-server（實測整理 104 非公開 API 的說明）：**搜尋結果每一筆本身就有 `employeeCount`**，
  0 或沒有這個欄位＝公司沒公開（約半數）。已改成直接讀這個欄位，拿掉打公司頁的程式
  （`fetch_company_info()`、`hiring_companies_needing_employee_count()`、`employee_fail_count` 都刪了）；
  這次沒公開就保留上次的人數。「搜尋結果都沒有員工人數」時畫面會有紅字提醒。
- **只抓到每個關鍵字的第 1 頁**：160 筆＝5 個關鍵字 × 每頁 32 筆。104 回的 `data` 是陣列，總頁數放在
  `metadata.pagination`（`lastPage`／`total`），`jobs_104._extract_items_and_total_page()` 原本遇到陣列
  一律當成 1 頁。已修正，**每日抓職缺也共用這支**，所以每日抓職缺之後每個關鍵字會照原本的
  `MAX_PAGES_PER_SITE`（3 頁）抓，不再只有第 1 頁（有 240 秒時間上限保護）。
- 同一份說明也提到：`order=16` 才是「最新更新在前」（15 是相關性，`jobs_104.py` 註解寫的「依更新日期」
  其實是相關性，每日抓職缺這次沒改）；`jobType=1` 是無視關鍵字的廣告位，產線公司這支已排除；職缺名稱
  裡的 `[[[關鍵字]]]` 標記已清掉。
- Firestore 裡第一次跑留下的 144 間不用清，下次執行會照新方法補上員工人數。
- 測試：`tests/test_salesdev_hiring.py` 改寫（拿掉公司頁相關測試，加上廣告、分頁、employeeCount）。

**上線步驟（使用者在 Cloud Shell 執行，合併部署完成之後）**
1. 取出每日抓職缺用的那把密鑰（存到這個 Cloud Shell 視窗的暫存變數 `SECRET`，第 2 步要在同一個視窗）：
   ```
   SECRET=$(gcloud run services describe recruitment-bot --region=asia-east1 --project=tsaipei-505807 --format=json | python3 -c "import json,sys; env=json.load(sys.stdin)['spec']['template']['spec']['containers'][0].get('env',[]); print(next((e.get('value','') for e in env if e['name']=='SALESDEV_SCRAPE_TRIGGER_SECRET'),''))"); echo "${#SECRET}"
   ```
   確認：印出來的數字是 48（密鑰長度）；是 0 代表沒取到，不要繼續。
2. 建立每週一 06:00 的排程（避開每日 07:00 抓職缺，不要兩個同時打 104）：
   ```
   gcloud scheduler jobs create http salesdev-weekly-hiring --project=tsaipei-505807 --location=asia-east1 --schedule="0 6 * * 1" --time-zone="Asia/Taipei" --uri="https://recruitment-bot-412901869672.asia-east1.run.app/internal/salesdev/hiring/run" --http-method=POST --headers="X-Salesdev-Scrape-Secret=$SECRET" --attempt-deadline=600s
   ```
3. 馬上手動跑一次：`gcloud scheduler jobs run salesdev-weekly-hiring --project=tsaipei-505807 --location=asia-east1`，
   約 5 分鐘後開 `/salesdev?tab=hiring`，上方要出現「最近一次每週抓取」。如果寫「還有 N 間下次再查」，
   隔 5 分鐘再跑一次同一行指令，直到 N 變 0。

## 台北所(派遣組)／台北所(國際組)專區：待進人員＋每日加退保自動帶入（2026-09-25 確認規格，分兩個 PR 實作）

PR1（專區分頁、廠商/班別維護、待進人員，PR #238）、PR2（每日加退保依日期自動帶入）都已完成。

### 需求

台北所(派遣組)、台北所(國際組)兩個部門（功能一樣、資料分開）。原則是**同仁提前維護名單**，到了
「每日加退保」頁，系統自動帶出當天要加退保的人，一鍵送出給人資。人員多是時薪、按日上工的外籍人員，
**大多是當天加退**，同一個人會排好幾天，每天一列。

### 已確認

1. **專區首頁改成分頁**：首頁卡片「台北所(○○組)專區」點進去先到專區首頁，分頁「每日加退保」
   （現有功能）、「待進人員」（新）；主管另外看得到「廠商維護」「班別維護」。
2. **待進人員欄位**＝各所上傳範本 11 欄（編號／廠商／班別／姓名／身分證／勞保加保日期／勞保退保日期／
   勞保追退日期／健保加保月份／眷屬健保／備註）。**身分證必填**（只有配送部例外），日期至少一個。
3. **輸入方式**：一筆一筆手動新增、Excel 批次匯入、**多日期**（填一次人員資料、勾好幾個日期，一次建好
   好幾列）三種都要。
4. **廠商、班別只能下拉選**，選項要先在「廠商維護」「班別維護」建好；**兩組分開維護**；只有**主管**
   （帳號職級副主任以上，`platform_accounts.is_manager_rank()`）能維護。Excel 匯入時廠商或班別不在
   清單上的列不匯入，列出來提醒。
5. **重複**：同一個身分證、同一天，已經有加保又登記加保、或已經有退保又登記退保，才擋下來；擋下要用
   **彈出視窗**提醒。
6. **每日加退保頁自動帶入**：選一天 →
   - 「今天要送的名單」：加保或退保日期＝這一天的人
   - 上方另一區紅色「**日期已過、還沒送出**」：日期比這一天早、還沒送出的人，**預設勾選**一起送出
     （使用者補充：除了忘記送，也可能是日期過了才知道人員有被安排——**事後補登過去日期的人也要能送**，
     一樣出現在這一區）
   - 之後日期的人不出現，等那天再帶出
   - 一鍵送出；已收單就走「送出補件給人資」（跟配送組同一套補件／退件）；紀錄保留、看得到建立人員
7. **人資看不到待進人員**（使用者：之後每個部門都有這功能的話人資頁面會太複雜）。
8. **提醒改彈出視窗**：這次先做台北所專區、每日加退保、待進人員這幾頁——錯誤／警告用彈出視窗、要按
   「確定」；成功訊息在角落出現幾秒自動消失。**整個系統的提醒之後另外一次改**。
9. ~~配送組維持現狀~~ → **2026-09-25 改成配送組也一樣不送出未來日期**（見下面「勞保局規定的討論」）。

### 勞保局規定的討論（2026-09-25）

使用者說明人資辦加退保的規定：「加保只能當天、退保也只能當天，但連假後第一個工作日可以辦連假期間任一天
的加退保」。討論過工作日曆、「已超過可申報日」標記等做法後，使用者決定**先做現在系統能做的**：

1. **配送組也跟台北所一樣，不送出未來日期的資料**：送出時只送日期在選定那天（含）以前的；之後日期的留在
   清單上（畫面另外列一區「之後日期，還不會送出」讓同仁看得到），到那天再送。
2. **加退保日期完全照同仁點選的日期**，給人資的彙總表也完全照這個日期，**系統不判斷能不能申報**——過期的
   人資自己會判斷、另外發函勞保局處理，重點是日期要正確送到人資手上。所以**不做工作日曆、不做「已超過
   可申報日」標記**；「日期已過、還沒送出」那一區照樣預設勾選一起送。
3. 一列裡加保、退保日期不同天（例如 9/25 加保、10/5 退保）時，建議**儲存時自動拆成兩列**（一列加保、一列
   退保），才不會在加保那天把還沒到的退保一起送出去。**使用者確認：要自動拆。**

### 建議、使用者沒有反對（實作前再確認一次）

- **廠商維護的每個選項多一個「對應主頁廠商」欄位**（可留空，下拉選 `/vendors` 廠商管理裡「服務部門」有勾
  這一組的客戶，同名只顯示一次）——主頁廠商以客戶為單位（「蝦皮」），部門廠商是客戶＋地點＋計薪＋身分
  （「蝦皮(台南)(時薪)-外籍」），不能合成一份，但現在先留對應欄位，之後可以自動帶出彙總表「投保單位」
  （主頁廠商有記簽約公司）、依客戶做統計。
- **多日期的類型**：每個勾選的日期建一列，類型選「當天加退／只加保／只退保」。

### 實作紀錄 PR1：專區分頁＋廠商/班別維護＋待進人員（2026-09-25）

- **`hr/config.py`**：`INSURANCE_ZONE_DEPARTMENTS`（台北所兩組），也加進 `INSURANCE_DRAFT_DEPARTMENTS`
  （待進人員存在同一個暫存區 `hr_insurance_drafts`，`kind="zone"`）。
- **`hr/insurance_options.py`（新檔）**：collection `hr_insurance_options`（department/type=vendor|shift/name/
  active/platform_vendor_name）。不能改名、不能刪，只能停用；同部門同種類不能重名。`platform_vendor_choices()`
  列主頁廠商管理裡「服務部門」有勾這一組的客戶名稱（同名一次、全形括號也認）。
- **`hr/insurance_pending.py`（新檔）**：`clean_fields()`（身分證轉大寫去空白、日期接受 2026-09-26／2026/9/26）、
  `validate()`（廠商必選且要是啟用中的選項、班別可空但有填就要在清單、身分證必填、日期至少一個）、`split()`
  （加保/退保不同天拆兩列，追退日期跟退保那列）、`expand_multi()`（多日期）、`find_duplicates()`（同身分證、
  同一天、同是加保或同是退保；已取消的不算；同一批彼此也比）、`prepare_import()`（Excel 每列檢查，錯的列回
  (Excel 列號, 原因)）。
- **`hr/routes/zone_routes.py`（新檔，掛在 hr_app）**：`/hr/zone/pending`（清單＋篩選日期/姓名或身分證/廠商/
  狀態）、`/pending/new`（`mode=single|multi`，`?date=` 預帶日期、`back=` 存完回去——給 PR2 每日加退保的
  「＋新增人員」捷徑用，只接受 `/hr/` 開頭）、`/pending/{id}/edit`（改完不同天也會拆）、`/cancel`、
  `/pending/template.xlsx`、`/pending/import`、`/zone/options/{vendor|shift}`（主管：`is_manager_rank()` 或全平台
  管理員）。權限只看帳號部門（不用勾人資模組）。
- 樣板：`_zone_tabs.html`（分頁）、`_flash_popup.html`（**彈出視窗**：錯誤/警告 `<dialog>` 要按確定、成功右下角
  toast 4 秒；文字伺服器端直接寫進去，JS 另外提供 `window.showAlert()`）、`_option_picker.html`（`<datalist>`
  可搜尋選單，送出前檢查一定要是清單裡的值）、`zone_pending_list.html`、`zone_pending_form.html`（多日期：
  單日加入或連續日期一次加入，最多 62 天）、`zone_options.html`。
- 每日加退保頁（`/hr/insurance/upload`）對台北所顯示專區分頁；**PR2 之前先不顯示待送出清單**（避免把之後
  日期的待進人員一次全送）。首頁卡片說明改成「每日加退保、待進人員」，連結不變。
- 測試：新增 `tests/test_hr_zone.py`（22 個）。全部 2334 個通過；Playwright 看過待進人員清單、多日期新增、
  重複時的彈出視窗、廠商維護。
- 使用者不需要手動設定。上線後請**主管先到「廠商維護」「班別維護」建選項**，同仁才能登記待進人員。

### 實作紀錄 PR2：每日加退保依日期自動帶入（台北所＋配送組，2026-09-25）

- **`hr/routes/insurance_routes._date_groups()`**：待送出清單依「這一列最早的日期」（`zone_routes.entry_day`）分三區：
  `overdue`（比選定日期早、還沒送）、`due`（等於選定日期）、`future`（之後）。overdue＋due 有勾選框（預設勾），
  future 收在 `<details>` 只能看。台北所、配送組共用同一個畫面（台北所多顯示班別、身分證欄，「來源」欄改顯示
  建立人員）。
- **送出／補件／下載都只處理勾選的、而且日期不晚於選定那天的**（`_selected_due()`；表單被竄改送進未來日期的
  id 也不會送）。沒勾任何一筆會擋下並提醒。
- 「＋新增人員」捷徑：台北所 → `/hr/zone/pending/new?date=…&back=每日加退保頁`（日期預帶成當天加退）；配送組 →
  新的 `GET /hr/insurance/drafts/new?date=…`（預帶加保日期，改用 `insurance_draft_edit.html` 的新增模式），
  拿掉原本頁面上展開的手動新增表單。台北所打舊的 `/insurance/drafts/new|edit` 會被導到專區的表單。
- **配送組手動新增／修改也會「加保退保不同天自動拆兩列」**（`insurance_pending.split()`），不然會在加保那天
  把之後的退保一起送出。
- 刪除按鈕用 HTML `form="cancel-{id}"` 指到頁面下方各自的隱藏表單（不能在送出表單裡巢狀 form）。
- 每日加退保頁的成功／錯誤訊息改用彈出視窗（`_flash_popup.html`），7 個所都適用；頁面上固定的狀態提示（已收單、
  整份上傳會蓋掉）維持原本的紅字。
- 使用說明：`hr/templates/insurance_help.html`、`delivery/templates/help.html` 補上依日期送出、新增人員捷徑。
- 測試：`tests/test_hr_zone.py` 新增 `DailyByDateTests`、`DeliveryByDateTests`；`tests/test_hr_insurance_drafts.py`
  送出類的測試改成帶勾選的 id。全部 2340 個通過；Playwright 看過台北所每日加退保頁（電腦、手機）。
- 使用者不需要手動設定。


## 整個系統的提醒改成彈出視窗（2026-09-25）

使用者在台北所專區討論時提出「整個系統所有的提醒能否改為彈出式視窗？比較不會沒注意到」，先做了台北所那幾頁
（PR #238／#239），這次擴大到全系統。

- **`delivery/static/flash.js`（新檔）**：4 個 base.html（`templates/`、`delivery/`、`hr/`、`management/`）都在 `<head>`
  載入（所有子系統本來就共用 `/delivery/static/`）。頁面上 `class` 有 `js-flash` 的訊息：`.success` → 右下角提示 4 秒
  自動消失；`.error`／`.warning` → `<dialog>` 彈出視窗、要按「確定」（保留原本訊息裡的連結；同一頁好幾個錯誤合併成
  一個視窗）。原本的文字只是藏起來，還留在 HTML 裡（沒 JS 也看得到、測試也驗得到）。
- **`window.alert()` 也換成同一個彈出視窗**：系統裡 11 個 alert 都是「提醒完就 return」，不需要擋住程式，所以直接
  覆寫；`window.confirm()` 維持瀏覽器原生（要等使用者回答）。另外提供 `window.showAlert(文字, [清單])`、
  `window.showToast(文字)`。
- **哪些要彈、哪些不彈**：67 個樣板、90 處「操作結果」訊息加了 `js-flash`（`{% if error/err/msg/saved/submitted/
  generated/deleted/hired… %}`、匯入格式錯誤等）。**固定的狀態提示不加**（每次打開頁面都在，彈出來會很煩）：
  配送主頁「有 N 筆未結案意外事件」、人員詳細頁「離職但還有裝備沒還」、每日加退保「已收單」「整份上傳會蓋掉」、
  待進人員「還沒有廠商選項」、合約總表的上限警告、財務/我的專區的資料讀取失敗、業務開發的讀取失敗、表格裡的
  「人資退件」紅字、離職對話框裡的裝備提醒。
- `hr/templates/_flash_popup.html` 改成只輸出 `js-flash` 元素，實際顯示交給 flash.js。
- **新頁面的寫法**：操作結果訊息寫 `<p class="success js-flash">`／`<p class="error js-flash">`；
  `tests/test_flash_popup.py` 會檢查 `{% if error|err|msg %}<p class="…">` 沒有漏加 js-flash。
- 測試：全部 2344 個通過；Playwright 看過成功提示、錯誤彈窗、alert 改用彈窗。

### 補充：專區分頁加「使用說明」連結（2026-09-25）

`hr/templates/_zone_tabs.html` 分頁最右邊多「使用說明」，連到 `/hr/insurance/help#zone`（加退保說明頁的台北所那一段，
`<h2 id="zone">`）；每日加退保上傳頁（7 個所都一樣）下方連結也多「使用說明」。

## 薪資補款搬離 GAS 階段 2 第 1 步：試算表原樣同步進平台＋讀取來源開關（2026-09-25）

上面「職缺維護系統整個搬離 GAS 到 GCP」計畫裡，階段 2 表格的第 1 個 PR。使用者 2026-09-25 看過
7 步流程後同意開工，髒資料選 **(a) 原樣照搬**。

### 做了什麼

- **`services/salary_repayment_store.py`（新檔）**：
  - `salary_repayments`：「薪資補款紀錄」一列一份文件，id＝補款單號。單號空白、重複（第二筆起）、
    含 `/`、或 Firestore 不收的 id，改用 `row-{列號}`，**一列都不少**。
  - 內容 `fields`（表頭→儲存格文字，Sheets API 預設的格式化文字，**不轉型別、不修**）、`row_number`、
    `source="sheet"`、`synced_at`。表頭空白的欄改叫「（第 N 欄）」（Firestore 欄位名稱不能是空字串）。
  - `salary_repayment_org`：「員工主管組織表」原樣照搬，只用來把「核准主管」LINE ID 換姓名。
  - `salary_repayment_meta/state`：`read_source` 開關、最後同步時間／人／結果、表頭順序。
  - **同步＝跟試算表一模一樣**：新增／更新／沒變的不寫／試算表沒有的刪掉（GAS 退回會刪列），**只刪
    `source == "sheet"` 的**，第 4 步平台自己收的單不會被刪。只寫有變的文件，每 400 個操作一個 batch。
- **`services/salary_repayment_service.py`**：抽出 `fetch_sheet_values()`（原始值，同步用）；新增
  `_fetch_rows()` 依開關讀試算表或 Firestore，回傳格式一樣，`/me`、`/finance` 的篩選排序不用改。
  開關讀不到（Firestore 出錯）一律讀試算表。
- **`/finance/migration`（只有全平台管理員）**：「從試算表同步到平台」按鈕、同步結果表（新增／更新／
  沒有變動／刪除）、單號空白或重複的列號提醒、讀取來源切換（還沒同步過不能切到平台資料）。
  讀平台資料時會提醒「新單還是 GAS 寫進試算表，平台只到最後同步那一刻」。財務部專區首頁只有管理員
  看得到目前來源和連結。
- `tests/_fake_firestore.py` 加 `delete`（doc 與 batch）。

### 上線後使用者要做的

1. 以管理員登入 → 財務部專區 → 「薪資補款資料搬家」→ 按「從試算表同步到平台」。
2. 看同步結果：「薪資補款紀錄」的試算表筆數要跟試算表實際列數（扣掉表頭）一樣。
3. 想檢查的話切到「平台資料」，看財務部專區和我的專區顯示是否跟之前一樣，**看完切回「Google 試算表」**。
   第 4 步上線前，新單仍由 GAS 寫進試算表，平台資料不會自動更新。

### 下一步

第 2 步：佐證照片 Drive → Cloud Storage（不需要使用者先準備）。第 4 步上線、平台接手寫資料時，要把
這頁的同步按鈕停掉（不然會用試算表蓋掉平台的新資料——雖然 `source` 不同不會被刪，但同單號會被覆蓋），
並把讀取來源固定為平台資料。

## 薪資補款搬離 GAS 階段 2 第 2 步：佐證照片 Drive → Cloud Storage（2026-09-25）

### 做了什麼

- **`services/salary_repayment_photos.py`（新檔）**：GAS 存的照片網址是
  `https://lh3.googleusercontent.com/d/{Drive 檔案 ID}`，每個檔案都設成「知道連結的人都能看」
  （`Project_Salary.js` 的 `uploadSalaryImageToDrive()`），所以**不需要 Drive API、不需要使用者分享
  任何東西**就能下載：先試 `drive.google.com/uc?export=download&id=`（原始檔），不行再試 lh3。
  - 檔案 ID 抓法跟 GAS 一樣（`[-\w]{25,}`）。
  - 回來是 HTML（檔案被刪／權限改成要登入）＝失敗並記原因；其他內容原樣照存（不限 JPG/PNG，
    延續「原樣照搬」）。
  - 存到共用私有 bucket（`DELIVERY_GCS_BUCKET`，`config.SALARY_PHOTO_GCS_BUCKET`）的
    `salary/photos/{文件 id}/{隨機}.副檔名`。
  - 結果寫在 `salary_repayments` 文件：`photo_blob`／`photo_content_type`／`photo_source_url`／
    `photo_copied_at`，失敗 `photo_error`／`photo_error_url`／`photo_error_at`。
  - 狀態 `photo_status()`：沒有照片／已搬好／失敗／待搬；`photo_source_url` 跟目前網址不同就當待搬（重搬）。
  - `copy_batch()` 一次最多約 45 秒，避免網頁請求逾時；失敗的只有勾「連失敗的也一起重試」才會重試。
- **`services/salary_repayment_store.py`**：重新同步改成保留同步以外的欄位（`{**舊文件, 新欄位}`），
  不然第 1 步的同步會把搬好的照片紀錄洗掉。
- **`/finance/migration` 加第 2 區「搬佐證照片」**：統計表、「搬下一批照片」按鈕、抽查連結、失敗清單。
  `/finance/migration/photo/{文件 id}`（只有全平台管理員）看搬好的照片；只有 jpeg/png/gif/webp 直接顯示，
  其他類型一律改成下載＋`nosniff`（避免舊資料裡萬一有 SVG／HTML 在我們網域被當網頁執行）。
- **Drive 上的照片、試算表網址都沒動**，LINE 卡片、通知信照舊用 Drive 連結。

### 上線後使用者要做的

1. 財務部專區 →「薪資補款資料搬家」→ 先按第 1 區「從試算表同步到平台」（同步最新資料）。
2. 第 2 區按「搬下一批照片」，一直按到「待搬」變成 0。
3. 點「抽查已搬好的照片」的幾個連結，確認照片正常。
4. 有「失敗」的：先勾「連失敗的也一起重試」再按一次；還是失敗的，把失敗清單的原因貼給 Claude。

### 下一步

**修正（同一天）**：第一版照上面欄位對照表寫成找「佐證照片網址」欄，實際表頭是「補款佐證(照片)」，上線後使用者
回報統計是「有照片 0、沒有照片 218」。改成 `photo_url()` 依序找：已知表頭（補款佐證(照片)／全形括號／佐證照片網址）
→ 表頭含「佐證」→ 值是 lh3／drive.google.com 網址的欄。不用重新同步，資料本來就原樣存在，修好後直接按搬照片即可。

第 3 步：平台寫入時同步一列回試算表給財務看。**開工前使用者要先把「薪資補款」試算表分享「編輯者」
權限給 Cloud Run 服務帳戶**（見上面「使用者要先準備的六件事」第 1 項）。照片在平台上給同仁/主管看的
畫面，等第 4、5 步（送出、核准改由平台處理）再一起做。

**第 1、2 步上線後核對（2026-09-25）**：同步結果「薪資補款紀錄」218 筆、「員工主管組織表」46 筆，沒有空白或
重複的補款單號；照片欄修正後「有照片 10、已搬好 10、失敗 0、沒有照片 208」，使用者到試算表篩選 U 欄確認
確實只有 10 筆有照片。使用者也已經把「薪資補款」試算表分享「編輯者」給 Cloud Run 服務帳戶（第 3 步準備）。

## 薪資補款搬離 GAS 階段 2 第 3 步：平台寫回「薪資補款紀錄」試算表（2026-09-25）

### 財務實際看的是哪份表

財務看的是「材霈會計對帳表」，是 GAS 的一次性工具 `setupAccountingSyncSpreadsheet()`（`程式碼.js`）建的，
A1 放 `=QUERY(IMPORTRANGE(來源, "薪資補款紀錄!A:U"), "select Col1, Col2, Col3, Col5…Col21", 1)`（排除 D 欄
申請人 LINE ID）。**所以平台只要照 GAS 的方式寫「薪資補款紀錄」分頁，會計對帳表就自動跟著更新**，不用另外
寫第二份、也不用動會計對帳表。注意 IMPORTRANGE 只帶到 U 欄，V 欄「匯費」會計對帳表本來就看不到。

### 做了什麼

- **`services/salary_repayment_sheet_writer.py`（新檔）**，照 `Project_Salary.js` 的三種寫法：
  - `append_record(fields)`：最後加一列（GAS `appendRow`）。fields 的 key 是表頭文字，每次先讀第 1 列
    對位置，表頭沒有的欄位不寫、回報出來。`USER_ENTERED`（跟 appendRow 一樣日期文字會被當日期）。
  - `update_review(單號, 狀態, 核准主管, 核准時間)`：核准只改「審核狀態／核准主管／核准時間」三格；
    **「已退回」整列刪除**（GAS `updateSalaryReviewStatus` 也是刪，退回的不給會計看）。找列只看 A 欄、
    跳過表頭、重複單號取第一筆（同 GAS）。刪列用 `deleteDimension`，分頁 gid 用分頁名稱查。
  - `check_write_access()`：把第 1 列表頭 RAW 原樣寫回，確認有編輯權限，內容不變。
  - 全部回傳 `(ok, 白話訊息)`，403 翻成「請把服務帳戶改成編輯者」。
- **這一步還不會被觸發**：第 4 步（送出）呼叫 `append_record`、第 5 步（核准/退回）呼叫 `update_review`。
- **`/finance/migration` 第 3 區「檢查寫入權限」按鈕**（原本第 3 區「讀取來源」改成第 4 區）。
- 測試 `tests/test_salary_repayment_sheet_writer.py` 用記憶體版假試算表驗證欄位對位、核准只動三格、
  退回刪列、表頭列不會被當成資料、錯誤不拋例外、只有管理員能按。

### 上線後使用者要做的

財務部專區 →「薪資補款資料搬家」→ 第 3 區按「檢查寫入權限」。跳出「平台可以寫入…內容沒有任何改變」
就對了；跳出「沒有權限」就是試算表分享的帳號不對或權限不是編輯者。

### 下一步：第 4 步（送出流程改由平台處理）要注意

- 補款單號怎麼產生要跟 GAS 一致（看 `Project_Salary.js` 送出那段），金額後端重算（GAS 有 `verifiedSummary`）。
- 平台寫 Firestore（`source="platform"`）＋ `append_record()` 寫試算表＋照片直接存 GCS。
- **第 1 步的「從試算表同步到平台」按鈕要停用或改成只補不蓋**：同單號的列會被試算表版本覆蓋成
  `source="sheet"`。讀取來源也要固定成平台資料。
- GAS 那邊送出的程式要停用（不然兩邊都收單），核准卡片還是 GAS 發的話，GAS 核准時改的是試算表、不是
  Firestore——所以第 4、5 步可能要一起切換，開工前再跟使用者確認切換順序。

## 職缺維護 LINE 官方帳號改由平台當「總機」（2026-09-25，GAS 搬家階段 2 方案 B 第一段）

### 為什麼

第 4、5 步（送出、核准改由平台處理）討論時提出兩個方案：A＝核准卡片按鈕改開平台網頁、LINE Webhook 不動；
B＝LINE Webhook 改指向平台，平台處理補款、其他轉給 GAS。使用者一開始以為 GAS 會整個停掉，說明「這個官方
帳號同時負責補款／職缺維護／專案合約／綁定＋PIN 登記，要到階段 4 全部搬完才能關 GAS」之後，**選 B**：
Webhook 切換只做一次、現在就做，之後每搬一個功能就是平台多接一種訊息、少轉一種，階段 4 不再轉就關 GAS。

### 這一段做了什麼（行為完全不變）

- `POST /api/job-portal/line-webhook`（`job_portal_line_relay_routes.py`）：用 Channel secret 驗
  `X-Line-Signature`（不對 403、沒設定 503），**馬上回 200，背景把 body 原封不動 POST 給
  `JOB_PORTAL_LINE_RELAY_TARGET_URL`**（就是原本 LINE 後台的 GAS 網址含 `?webhook_secret=`，GAS 的驗證照舊）。
  GAS 用事件裡的 replyToken 回覆，轉發很快不會過期。
- `services/job_portal_line_relay.py`：簽章、事件種類摘要（`message`、`postback:review_salary`…，**不存訊息
  內容**）、轉發、最近 30 筆轉發紀錄（Firestore `job_portal_line_relay/recent`）。
- `/finance/migration` 第 4 區「LINE 總機」顯示是否設定好、最近轉發紀錄；原本第 4 區「讀取來源」改成第 5 區。
- 新環境變數 `JOB_PORTAL_LINE_CHANNEL_SECRET`、`JOB_PORTAL_LINE_RELAY_TARGET_URL`（`config.py`）。

### 使用者要做的（切換步驟）

1. LINE Developers → 職缺維護那個官方帳號的 channel →「Messaging API」分頁 → Webhook URL：**整串複製**（這就是
   轉發網址，含 `?webhook_secret=`），先貼到記事本存著（也是退回用的網址）。
2. 同一個 channel →「Basic settings」分頁 → Channel secret：複製。
3. Cloud Shell 設環境變數（兩個值用各自的一行設，網址裡有 `?`、`=` 要整個用雙引號包起來）。
4. 挑沒人用的時段，把 LINE 後台 Webhook URL 改成
   `https://recruitment-bot-412901869672.asia-east1.run.app/api/job-portal/line-webhook` → 按「Verify」要 Success。
5. 用 LINE 傳一則訊息給官方帳號、請主管試按一張職缺或補款核准卡片（或做一次綁定），到
   `/finance/migration` 第 4 區看紀錄都是 ✅。
6. **退回**：把 LINE 後台 Webhook URL 改回第 1 步存的原網址即可，平台這邊不用動。

### 下一段

補款送出＋核准改由平台處理：平台自己收單、推核准卡片（沿用 `action=review_salary` 的 postback 格式），總機
攔下 `review_salary` 的 postback 自己處理——**只攔平台建立的單（Firestore `source="platform"`）**，切換前
GAS 發出去、還沒按的舊卡片照樣轉給 GAS，這樣就不用要求主管切換前把待審核的單審完。需要 Channel access token。

**切換結果（2026-09-26）**：使用者設好兩個環境變數、把 LINE 後台 Webhook 改成平台，`/finance/migration`
第 4 區看到「（LINE 後台驗證）✅」「message ✅ GAS 已處理」，總機上線。使用者提醒：**平常操作都是網頁驅動**
（網頁表單直接打 GAS Web App，不經過總機），經過總機的主要是主管在 LINE 按核准卡片（postback）；GAS 另外
只有「綁定＋姓名＋PIN」「綁定群組」兩個很少用的打字指令，一般打字 GAS 不回覆。

## 薪資補款核准信＋PDF 存查單改由平台產生（2026-09-26，第二段 PR 1）

第二段（補款送出＋核准改由平台處理）的流程使用者 2026-09-26 回「好」確認：同仁／主管看到的都跟現在一樣；
核准信要附 PDF，所以原本第 6 步（平台產 PDF）提前一起做；做「補款改由平台處理」開關；切換前的舊卡片照樣轉
GAS；員工主管組織表繼續由 GAS 維護、平台直接讀。分兩個 PR：PR 1 產生內容＋預覽、PR 2 收單＋卡片＋核准＋開關。

### PR 1 做了什麼（不寄信、不改資料）

- **`services/salary_repayment_report.py`（新檔）**，逐項照抄 `Project_Salary.js`：
  - `build_record()`：**照 GAS 的欄位位置取值**（`data[i][4]` 那種），順序用第 1 步存的 `record_headers`，不靠表頭文字。
  - `Org`：員工主管組織表照 GAS 欄位位置（0 姓名、1 LINE ID、2 主管姓名、3 主管 LINE ID、4 主管 Email、
    9 員工 Email）；`supervisors()`＝`getSupervisorsByApplicantUserId()`（不含組織表沒設就退回系統管理員那段，
    PR 2 要補 ADMIN_LINE_USER_ID／ADMIN_EMAIL）；`applicant_email()`＝`findApplicantEmail()` 三段順序。
  - `recipients()`：財會（新環境變數 **`SALARY_HR_ACCOUNTING_EMAILS`**，對應 GAS 指令碼屬性 HR_ACCOUNTING_EMAILS）
    ＋主管＋申請人，去重複；`subject()`、`email_html()` 同 GAS 的 HTML／CSS。
  - `build_docx()`＋`build_pdf()`：python-docx 排出跟 GAS Google Docs 一樣的存查單（標題、一基本資料 4 欄表＋備註、
    二金額三色格、三簽核紀錄、頁尾說明），LibreOffice 轉 PDF（容器已有 libreoffice-writer＋fonts-noto-cjk）。
    字型全部指定 Noto Sans CJK TC（不指定的話英數字會變成襯線字）。
  - **唯一刻意跟 GAS 不同**：信件／PDF 的「核准主管」GAS 印 S 欄 LINE User ID，這裡換成姓名（換不到才印原值）。
- **`/finance/migration` 第 5 區「預覽」**：列最新 20 筆已核准舊單 → `/finance/migration/preview/{id}`（主旨、收件人、
  附件、信件內容 iframe，照片用平台搬好的那張）、`/finance/migration/preview/{id}/pdf`。讀取來源改成第 6 區。
- 測試 `tests/test_salary_repayment_report.py`：民國年、千分位、欄位依位置、主管／申請人 Email 查找、HTML 跳脫、
  docx 內容、預覽只列已核准、只有管理員。

### 使用者要做的

1. 設定財會收件信箱（值在 GAS 編輯器 → 專案設定 → 指令碼屬性 → HR_ACCOUNTING_EMAILS）。
2. `/finance/migration` 第 5 區挑幾筆舊單「預覽信件」「看 PDF」，跟當初 GAS 寄出的核准信比對。

