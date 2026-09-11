# 材霈內部系統・現況總覽

這份文件回答一個問題：**「材霈內部系統現在長什麼樣子？」**

跟 `HANDOFF.md` 的差別：
- `HANDOFF.md` 是**時間軸變更紀錄**——照開發先後順序，記錄每次新增/修改
  了什麼、踩過哪些雷、當時怎麼解決的，總共 1500 多行，愈看愈舊的東西
  愈難找。
- 這份 `SYSTEM_OVERVIEW.md` 是**目前狀態的快照**——不管歷史，只講「現在
  系統由哪些部分組成、各自負責什麼、還缺什麼設定」，方便快速掌握全貌或
  跟別人介紹這個系統。

細節（例如某個功能當初為什麼這樣設計、完整的操作步驟）還是要回去查
`HANDOFF.md` 對應章節；這份文件之後如果系統架構有大變動，也要跟著更新。

---

## 1. 一句話說明整個系統

材霈有限公司把好幾個原本各自獨立、給不同部門用的小系統，全部整合掛在
**同一個 Cloud Run 服務**（`recruitment-bot`，GCP 專案 `tsaipei-505807`，
地區 `asia-east1`）底下，用網址路徑前綴區分（例如 `/delivery`、`/hr`），
同仁登入一次就能在有權限的部門之間切換，不用重複登入。

除此之外，還有兩個**獨立存在、不在這個 repo 裡**、但會互相呼叫/轉發資料
的專案：配送部另外兩支 LINE 官方帳號背後的 Google Apps Script 轉發邏輯
（`delivery-gas-project` repo），以及獨立架在 Netlify 的職缺維護系統。

## 2. 系統全貌一覽表

| 子系統 | 網址前綴 | 給誰用 | 主要功能 |
|---|---|---|---|
| 招募機器人「沛沛」 | `/callback`、`/test-callback` | 求職者（LINE 對話） | LINE 招募聊天機器人，回答職缺/FAQ、導去線上履歷 |
| 配送部系統 | `/delivery` | 配送部同仁 | 人員報到文件、補款、假別、應徵名單、車輛回報、意外事件回報 |
| 管理部系統 | `/management` | 管理部/業務主管 | 公告/會議記錄/SOP、業績報表、客戶拜訪紀錄、員工名冊組織圖、資產設備（含門號繳費提醒） |
| 人資專區 | `/hr` | 人資 | 意外通報彙整、員工體檢報告、員工關懷彙整、公司證照彙整、教育訓練彙整 |
| 少凱業務開發專區 | `/salesdev` | 業務開發（需 `/accounts` 開通） | 唯讀顯示派遣客戶開發名單、新登記工廠監控彙整（讀自 Google Sheet） |
| 我的專區 | `/me` | 所有已登入同仁（無需開通，人人都有） | 個人化資訊，目前只有薪資補款紀錄（依「申請人/主管」邏輯篩選，讀自 Google Sheet） |
| 帳號權限管理 | `/accounts` | 平台管理員（老闆） | 新增/刪除帳號、設定每個帳號在各模組的角色 |
| 公司管理 | `/companies` | 平台管理員（老闆） | 材霈旗下派遣公司牌照主檔（目前 10 家），「派遣全流程」擴充案第一階段 |
| 廠商管理 | `/vendors` | 平台管理員（老闆） | 合作廠商（蝦皮/UD/UC/順豐…）簽約公司對應，跟配送部既有廠商清單是分開的兩份資料 |
| 派遣契約產生器 | `/dispatch-contracts` | 需 `/accounts` 開通 | 填入客戶的班別/薪資/工作條件，自動套版產生派遣契約 Word 檔並存檔 |
| 統一入口頁 | `/portal` | 所有已登入同仁 | 依帳號權限顯示看得到的模組卡片 |
| 登入頁 | `/login` | 所有同仁 | 統一登入，也銜接職缺維護系統免登入 SSO |

**不在這個 repo 裡、但互相串接的專案：**

| 專案 | Repo / 平台 | 關係 |
|---|---|---|
| 配送部另外兩支 LINE 帳號的轉發邏輯（車輛回報、意外事件回報） | `tsaipei-linebot/delivery-gas-project`（Google Apps Script，clasp 部署） | 轉發到這個 repo 的 `/delivery/api/*` webhook |
| 職缺維護系統 | Netlify + Google Apps Script（無 git repo，原始碼手動貼給 Claude） | 透過 `job_portal_sso.py` 做免登入銜接 |

## 3. 技術架構重點

### 3.1 技術棧

FastAPI（Python）＋ line-bot-sdk（LINE 官方帳號串接）＋ Notion API
（招募機器人的職缺/FAQ 資料庫）＋ Vertex AI Gemini（招募機器人的決策與
回覆生成）＋ Firestore（session、各模組資料儲存）＋ Google Cloud Storage
（文件檔案儲存）。

### 3.2 多模組怎麼「掛」在同一個服務下

每個部門模組（`delivery/`、`management/`、`hr/`）都是一個獨立的 FastAPI
子應用程式（各自有自己的 `config.py`／`db.py`／`auth.py`／`routes/`／
`templates/`），用 `main.py` 掛載到指定的路徑前綴。之後如果要加新部門，
照這個目錄結構複製一份、在 `main.py` 掛載，架構上是可以重複的模式。

### 3.3 帳號權限模型

帳號資料存在 Firestore 的 `delivery_users` collection（名稱是歷史包袱，
現在其實是全平台共用，不是配送部專屬）。每個帳號有：

- `is_platform_admin`：全平台只有老闆本人是 `true`，等同所有模組的
  管理員，**不能透過任何網頁表單修改**，只能用指令碼或直接改資料庫。
- `modules`：一個帳號在每個模組各自的角色（`admin` 主管 / `staff`
  專員），沒列出的模組代表完全沒有權限。
- `manager_usernames`：這個帳號的主管（存的是別的帳號的 username，可以
  有多個），在 `/accounts` 網頁上設定，給「主管能看部屬資料」這類跨模組
  功能共用（目前是 `/me` 的薪資補款紀錄在用）。
- `department`：部門，自由文字，**必填**（`/accounts` 新增/編輯帳號表單會擋空白）。
  用途：`/accounts` 帳號權限管理頁面依此分組顯示、組內可拖曳排序；
  「小雞點數自費申請」的申請部門也直接帶入這裡的值。
- `sort_index`：帳號在自己部門內的手動排序數字（拖曳 `/accounts` 頁面存的），
  沒被拖曳過是 `None`（排在有排過序的人後面，依姓名排），新帳號一律
  `None`，天生排在部門最後——見 `platform_accounts.list_accounts()`／
  `reorder_department()`。

跟帳號同一層級（跨模組共用、不屬於任何單一部門）的還有 `/companies`
管理的公司牌照主檔（`platform_companies.py`，Firestore collection
`companies`）——**注意這裡的「公司」是材霈自己的派遣牌照，跟配送部系統
的「廠商」概念（蝦皮/UD/UC/順豐）不一樣，不要混淆**。詳見「派遣全流程」
擴充案，`HANDOFF.md`「未來規劃討論」章節。

`/vendors` 管理的廠商主檔（`platform_vendors.py`，collection
`platform_vendors`）記錄每個廠商簽約哪家公司——**這是全新、獨立的一份
資料，跟配送部系統 `delivery/config.py` 寫死的 `VENDORS`／`VENDOR_MAP`
清單完全沒有連動**，兩邊「廠商」這個詞剛好撞名但指的是不同的兩份資料，
刻意沒有合併（合併需要動配送部好幾個既有功能，風險評估過後決定先不做），
這是目前有意識留著的技術債，之後有安全的時機再處理。

`platform_accounts.py` 統一管理這個邏輯，`MODULES` 清單列出目前掛載的
部門，新增部門只要在這裡多加一筆，`/accounts` 頁面就會自動多一欄可勾選。

### 3.4 兩種不同的權限維度：部門模組 vs. 個人專區

- **部門模組**（3.3 說的那一套）：控制「這個帳號能不能打開這個頁面」，
  同一個部門/角色的人看到的東西完全一樣，要在 `/accounts` 手動開通。
- **`/me`（我的專區）**：任何登入的帳號都能打開，不需要在 `/accounts`
  開通；控制的是「頁面裡的資料哪些是這個人可以看的」，由各個小工具自己
  的邏輯決定（目前只有薪資補款紀錄，依「申請人本人或其主管」過濾）。之後
  要加其他個人化資訊，走這個模式，不要誤用部門模組的權限開通流程。

### 3.5 單一登入（SSO）機制

`delivery`、`management`、根 `app` 三邊的 session 都用同一組密鑰、同一個
cookie 名稱（`delivery_session`），瀏覽器端其實是同一顆 cookie，效果上
等同單一登入，不需要額外的登入伺服器。**這個機制只在同一個 Cloud Run
服務底下有效**——職缺維護系統架在 Netlify，是完全獨立的系統，不共用這顆
cookie，還是要分開登入。

## 4. 部署與 CI/CD 現況

- **這個 repo**：`.github/workflows/deploy.yml`，合併到 `main` 後
  自動 `gcloud run deploy` 部署到 Cloud Run。用 Workload Identity
  Federation（GitHub 用臨時身分登入 GCP，不存長期密鑰）。**需要一次性
  GCP 設定才會生效**（見 `HANDOFF.md`「CI/CD 自動部署」章節的完整指令），
  設定完成前這個 workflow 會直接失敗，但不影響手動部署照常運作。
- **`delivery-gas-project` repo**：`.github/workflows/clasp-push.yml`，
  合併到 `main` 後自動 `clasp push` + `clasp deploy -i <deployment id>`
  到真正的 Apps Script 專案。**需要先把 `clasp login` 產生的登入憑證存成
  GitHub Secret `CLASPRC_JSON` 才會生效**（同樣見 `HANDOFF.md`）。
- **在這兩項一次性設定完成之前**，改完程式碼／Apps Script 之後，仍然要
  使用者自己在 Cloud Shell 手動跑 `gcloud run deploy`（這個 repo）或
  `git pull && clasp push`（`delivery-gas-project`）才會真正生效——這點
  在 Claude 每次改完 `delivery-gas-project` 的程式碼時都要主動提醒。

## 5. 目前待辦事項彙整

依影響範圍整理（詳細操作步驟都在 `HANDOFF.md` 對應章節，這裡只列重點）：

**招募機器人：**
1. 履歷填完自動跳轉回 LINE——卡在需要與 `resume.tsaipei.com.tw` 的外部
   工程師協調介接方式，這個 repo 這邊無法單方面實作。
2. Cloud Run 服務帳戶權限過寬（掛了「編輯者」，範圍過大且缺 Firestore
   專用角色）——需要照 `HANDOFF.md` 記載的順序調整，避免服務中斷。
3. 每週新工廠登記監控功能：程式碼已完成，但要正式運作還缺 Google Sheet
   權限分享、LINE 推播對象設定、Cloud Scheduler 排程等環境設定。
4. 日夜接力（白天真人／晚上沛沛）：程式碼已完成但刻意保持關閉，等
   線上履歷跳轉問題解決、正式頻道上線後再啟用。

**人資專區（`/hr`）上線前還缺：**
5. 取得意外通報 LINE 群組 ID。
6. 設定兩組觸發密鑰。
7. 設定兩個 Cloud Scheduler 每週排程。
8. 到 `/accounts` 開通相關帳號權限。

**少凱業務開發專區（`/salesdev`）上線前還缺：**
9. 把試算表分享「檢視者」權限給 Cloud Run 服務帳戶（否則頁面會顯示
   「沒有權限讀取」）。
10. 到 `/accounts` 幫需要看這份資料的帳號開通「少凱業務開發專區」權限
    ——改成權限控管之後，除了老闆本人，沒有人會自動看到。

**我的專區（`/me`）上線前還缺：**
11. 把薪資補款那份 Google Sheet 分享「檢視者」權限給 Cloud Run 服務帳戶
    （否則畫面會顯示「沒有權限讀取」）——不需要到 `/accounts` 開通，`/me`
    人人都有，只是內容依姓名比對結果而定。
12. 跑一次 `python -m scripts.import_account_managers`，把既有的主管關係
    批次匯入到帳號的 `manager_usernames` 欄位（之後要調整直接在
    `/accounts` 網頁上改即可，不用重跑）。

**公司管理（`/companies`）上線前還缺：**
17. 跑一次 `python -m scripts.seed_companies`，把已知的 10 家公司建進
    系統（可重複執行，已存在的公司會跳過不覆蓋）。
18. 到 `/companies` 補上每家公司的「勞工保險證號」「勞退提繳單位編號」
    （這次建立時還沒有這兩項資料）。

**派遣契約產生器（`/dispatch-contracts`）上線前還缺：**
21. 到 `/accounts` 幫需要用這個功能的帳號開通「派遣契約產生器」權限
    （不需要新的環境變數，沿用既有的 `DELIVERY_GCS_BUCKET`）。

**廠商管理（`/vendors`）上線前還缺：**
19. 跑一次 `python -m scripts.seed_vendors`，把已知的 4 個廠商（蝦皮/
    UD/UC/順豐）建進系統（可重複執行，已存在的廠商會跳過不覆蓋）。
20. 到 `/vendors` 幫每個廠商補上「簽約公司」（選 `/companies` 建好的
    公司；這次建立時還沒有這項資料）。

**整體維運：**
13. CI/CD 自動部署的一次性設定（Workload Identity Federation、`clasp`
    登入憑證存成 GitHub Secret）尚未完成，見上方第 4 節。
14. 考慮加 Cloud Monitoring 錯誤告警（目前例外只靠 `print()` 寫進
    Cloud Run log，沒有主動通知）。
15. 考慮加 Firestore TTL 自動清除過期 session。
16. 考慮把各項金鑰（Notion／Gemini／LINE channel secret 等）搬到
    Secret Manager，取代目前明文環境變數的做法。

**「派遣全流程」擴充案（大型規劃，見 `HANDOFF.md`「未來規劃討論」
章節）：⏸️ 2026-09-09 起暫緩**，優先度讓給下面的「職缺維護表單整合」。
目前完成了公司主檔、廠商主檔（含簽約公司對應）兩塊基礎，線上履歷自建、
面試安排、加保/退保事件紀錄、黑名單都還沒開始；核心資料原則是「同仁
在職異動都是獨立永久保留的事件紀錄，不能覆蓋」，之後真的接續開發時要
照這個原則設計資料模型。

**「職缺維護表單」整合案（進行中，見 `HANDOFF.md`「新規劃：把『職缺
維護表單』整合進這個 repo」章節）：** 使用者決定把獨立在 Netlify + GAS
的職缺維護表單整個搬進這個系統（不再依賴外部、沒有版控的程式碼）。
目前還在確認範圍階段，等使用者說明/提供「專案合約維護」這項子功能的
細節，才能規劃具體實作。已知子功能：職缺維護（寫 Notion）、薪資補款
提交（這個 repo 已有讀取端）、專案合約維護（未知）；身份驗證要不要
從姓名+PIN 換成這裡的帳號系統也還沒決定。

## 6. 想知道更多細節，去哪裡查

- **某個功能當初為什麼這樣做、完整的設定步驟** → `HANDOFF.md`（用文件內
  的標題搜尋關鍵字，例如「意外事件回報」「權限模型」）。
- **給 Claude 的操作慣例**（例如「回報步驟要能複製貼上」這類規則）
  → `CLAUDE.md`。
- **配送部另外兩支 LINE 帳號的轉發邏輯出問題** → 去看
  `tsaipei-linebot/delivery-gas-project` 這個獨立 repo，不是這裡的
  `delivery/` 目錄。
