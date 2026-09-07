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
| 帳號權限管理 | `/accounts` | 平台管理員（老闆） | 新增/刪除帳號、設定每個帳號在各模組的角色 |
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

`platform_accounts.py` 統一管理這個邏輯，`MODULES` 清單列出目前掛載的
部門，新增部門只要在這裡多加一筆，`/accounts` 頁面就會自動多一欄可勾選。

### 3.4 單一登入（SSO）機制

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

**整體維運：**
9. CI/CD 自動部署的一次性設定（Workload Identity Federation、`clasp`
   登入憑證存成 GitHub Secret）尚未完成，見上方第 4 節。
10. 考慮加 Cloud Monitoring 錯誤告警（目前例外只靠 `print()` 寫進
    Cloud Run log，沒有主動通知）。
11. 考慮加 Firestore TTL 自動清除過期 session。
12. 考慮把各項金鑰（Notion／Gemini／LINE channel secret 等）搬到
    Secret Manager，取代目前明文環境變數的做法。

## 6. 想知道更多細節，去哪裡查

- **某個功能當初為什麼這樣做、完整的設定步驟** → `HANDOFF.md`（用文件內
  的標題搜尋關鍵字，例如「意外事件回報」「權限模型」）。
- **給 Claude 的操作慣例**（例如「回報步驟要能複製貼上」這類規則）
  → `CLAUDE.md`。
- **配送部另外兩支 LINE 帳號的轉發邏輯出問題** → 去看
  `tsaipei-linebot/delivery-gas-project` 這個獨立 repo，不是這裡的
  `delivery/` 目錄。
