# 匯聘資料模型

對應 `docs/job-app-architecture-plan.md` 的延伸規格。完整版（含 ER 圖、狀態機圖）已發布：
https://claude.ai/code/artifact/ee99c6ce-23b1-4388-b967-842e3ce4c221

## 雙模式設計原則

- **MANAGED**：職缺屬於內部派遣團隊代管的客戶，由 `StaffUser` 操作，`JobPosting.team_id` 必填。**V1 只會用到這個模式。**
- **SELF_SERVE**：職缺屬於未來開放的外部自助商家，由 `MerchantUser` 操作，職缺必須先經審核（`requires_review = true`）才能上線。相關欄位現在就定義好，但 Phase 0 不會有任何資料落在這個模式。

## 實體定義

### Team（V1）
`id` uuid PK · `name` text · `created_at` timestamp

### Client（V1: MANAGED，未來: SELF_SERVE）
- `id` uuid PK
- `name` text — 客戶／商家名稱
- `mode` enum — `MANAGED` / `SELF_SERVE`，V1 全部是 MANAGED
- `tax_id` text nullable — 統一編號，SELF_SERVE 必填並需驗證
- `verified_at` timestamp nullable — 統編驗證通過時間，僅 SELF_SERVE 使用

### TeamClientLink（V1）
`team_id` FK→Team、`client_id` FK→Client、`assigned_at` timestamp — 複合主鍵，同一客戶可對應多筆（多團隊服務同一客戶）

### StaffUser（V1，約 40 人）
- `id` uuid PK、`name`/`email` text（email 唯一，登入帳號）
- `role` enum — `SUPER_ADMIN` / `MANAGER` / `SPECIALIST`
- `team_id` FK→Team nullable — SUPER_ADMIN 為 null（跨團隊）；MANAGER/SPECIALIST 必填
- `status` enum — `ACTIVE` / `DISABLED`

### MerchantUser（Phase 2）
`id` uuid PK · `client_id` FK→Client（必須 mode=SELF_SERVE）· `role` enum(`MERCHANT_ADMIN`/`MERCHANT_STAFF`) · `status` enum

### Store（V1，選用）
`id` uuid PK · `client_id` FK→Client · `name`/`address` text · `geo_lat`/`geo_lng` float nullable

### Candidate（V1）
- `id` uuid PK、`name`/`phone` text
- `line_user_id` text nullable unique — LINE 對話與去重的主要 key
- `resume_summary` jsonb — 學經歷等結構化欄位，實際 schema 待外部履歷系統欄位清單確認後定案
- `source` enum — `EXTERNAL_SYNC`（舊系統匯入）/ `APP_NATIVE`（新系統直接應徵）

### JobPosting（V1）
- `id` uuid PK、`client_id` FK→Client 必填
- `team_id` FK→Team nullable — mode=MANAGED 必填且手動指定；mode=SELF_SERVE 為 null
- `store_id` FK→Store nullable
- `title`/`category` text — category 對應舊系統 ResumeKind 等職務分類
- `job_type` enum — 打工／兼職／正職／派遣
- `pay_type`/`pay_min`/`pay_max`、`shift`、`location_city`/`location_district`
- `status` enum（見狀態機）、`requires_review` boolean — MANAGED=false，SELF_SERVE=true
- `created_by` uuid — StaffUser 或 MerchantUser 的 id，依 client mode 決定

### Application（V1）
- `id` uuid PK、`job_posting_id`/`candidate_id` FK
- `status` enum（見狀態機）
- `assigned_specialist_id` FK→StaffUser nullable — 僅 MANAGED 職缺適用，且必須與職缺同一 team
- `source_channel` enum — `LINE` / `WEB` / `EXTERNAL_SYNC`
- `external_application_id` text nullable — 供舊履歷系統同步去重

### ApplicationEvent（V1）
`id`/`application_id` uuid · `event_type` enum(`STATUS_CHANGED`/`NOTE_ADDED`/`MESSAGE_SENT`) · `actor_type`/`actor_id`(`STAFF`/`MERCHANT`/`SYSTEM`/`CANDIDATE`) · `payload` jsonb

### ResumeSyncRecord（V1）
- `id` uuid PK
- `external_id` text unique — 對應舊系統識別碼（如 DataNo），待外部工程師確認
- `candidate_id` FK→Candidate nullable — 比對/建立候選人後回填
- `raw_payload` jsonb — 外部系統原始資料快照
- `sync_status` enum — `PENDING_ASSIGNMENT`（缺 client/team 對應，待人工分派）/ `LINKED` / `ERROR`

## 狀態機

**JobPosting.status**：`DRAFT` → (MANAGED 免審) `ACTIVE`；(SELF_SERVE) → `PENDING_REVIEW` → `ACTIVE` 或 `REJECTED`；`ACTIVE` ↔ `PAUSED` → `CLOSED`

**Application.status**：`SUBMITTED` → `VIEWED` → `INTERVIEW_INVITED` → `HIRED`；任一階段可轉 `REJECTED` 或 `WITHDRAWN`

## 關鍵業務規則

1. `Client.mode = MANAGED` → `JobPosting.team_id` 必填且由建立者手動指定，不從 client 自動推導（同一客戶可能有多團隊服務）
2. `Client.mode = SELF_SERVE` → `JobPosting.team_id` 恆為 null，`requires_review` 強制 true，狀態必須經過 `PENDING_REVIEW`
3. `Application.assigned_specialist_id` 指派的 `StaffUser`，其 `team_id` 必須與該職缺的 `JobPosting.team_id` 相同，不允許跨團隊指派
4. `MerchantUser.client_id` 對應的 `Client` 必須是 `SELF_SERVE` 模式
5. `ResumeSyncRecord.sync_status = PENDING_ASSIGNMENT` 時需人工在待分派佇列處理，系統不可自動猜測建立關聯
6. `Candidate.line_user_id` 是去重主要依據；無 LINE 應徵者以 `phone` 作次要去重依據
7. 聊天訊息本體沿用現有 repo 已驗證的 Firestore session 模式儲存，Postgres 只存 `ApplicationEvent` 作稽核摘要

## 待確認事項

- **履歷系統技術細節**：連線方式、唯一識別碼、更新時間戳、完整欄位清單、讀寫權限——見前次對外部工程師的問題清單
- **應徵記錄的客戶/職缺對應**：外部系統是否已標記每筆應徵對應的職缺單／客戶
- **越南擴展的 Market 維度**：暫不加入正式欄位，待 Phase 0 穩定後再評估
- **StaffUser 登入方式**：內部帳密邀請制、或串接公司既有 Google Workspace SSO
