# 匯聘計畫 — 求職 App 架構規劃（草案，尚未實作）

規劃目標：綜合小雞上工、Worky、GoGoJob 三個台灣求職平台的市場定位，設計一個同時涵蓋「打工／兼職快速媒合」與「正職求職內容化」的雙邊市場（求職者 × 雇主）平台架構。

完整版含系統架構圖、比較表、路線圖時間軸，已發布為互動式文件：
https://claude.ai/code/artifact/4a62ba10-c4d5-4202-b5ad-f501f21e4c18

## 1. 三平台市場定位比較（公開市場觀察，非官方規格）

| 平台 | 核心定位 | 典型情境 | 本平台採納策略 |
|---|---|---|---|
| 小雞上工 | 打工／短期工快速媒合，「今天找、明天上工」 | 學生找一日工、假日班、寒暑期打工 | 一鍵應徵、免重填履歷、班別/時薪快速篩選 |
| Worky | 行動優先、輕量互動體驗，降低求職與招募門檻 | 快速瀏覽職缺、即時聊天決定面試 | 即時聊天、輕量履歷卡片、推播式配對通知 |
| GoGoJob | 內容導向求職媒體，職缺 + 產業資訊 + 企業品牌頁並重 | 轉職者研究公司、比較待遇、閱讀職涯文章 | 企業品牌頁、產業快訊/心得社群、進階篩選搜尋 |
| **匯聘計畫** | 打工快媒合 × 輕互動體驗 × 內容化正職平台，三者合一 | 同一帳號可切換「找打工」與「找正職」模式 | 統一使用者主檔，依職缺類型套用不同應徵流程 |

## 2. 使用者輪廓

- **打工族／學生**：要快、要近、要準時領薪，應徵到回覆的時間差是流失主因
- **轉職者／社會新鮮人**：要比較待遇、公司風評與職涯發展，決策週期長
- **門市店長／中小雇主**：職缺刊登頻率高、人力缺口急迫，需快速篩到可上工的人
- **平台營運／審核人員**：要防詐騙職缺、違法工時、惡意留言，同時維持刊登效率

## 3. 核心功能地圖

**求職者端**：多元登入（LINE Login/OTP/Email）、快速履歷卡、地圖/分類/關鍵字搜尋、一鍵應徵、應徵進度追蹤、即時聊天、收藏與到貨推播、雇主評價、薪資試算工具、產業快訊與心得社群。

**雇主端**：多分店/多職缺帳號管理、職缺範本、ATS 看板、聊天室與快速回覆範本、企業品牌頁、成效儀表板、付費加值曝光。

**平台端**：職缺審核（詐騙/違法工時偵測）、檢舉與黑名單、內容管理、金流與訂閱帳務、客服工單、營運 BI 報表。

## 4. 系統架構

前期以「模組化單體」在 Cloud Run 上起步，延續現有 repo 已驗證的 FastAPI + GCP 部署模式，依流量與團隊規模逐步拆分獨立服務：

- **接觸渠道**：Web PWA、LINE OA/LIFF、原生 App（Phase 3）
- **API Gateway/BFF**（FastAPI）
- **核心服務**：Auth、求職者 Profile、雇主/職缺、配對/推薦、應徵流程（ATS）、即時聊天、通知、評價/審核、金流/訂閱、內容 CMS
- **資料與 AI 層**：PostgreSQL（交易核心）、Firestore（即時 session/聊天，沿用現有 repo 模式）、Redis（快取）、Typesense（搜尋索引）、GCS（履歷/照片）、Vertex AI Gemini（語意配對/內容審核）
- **Pub/Sub** 作為事件匯流排，「應徵送出」「職缺刊登」等事件驅動通知服務，避免服務間直接耦合

## 5. 核心資料模型

`User`（求職者：auth_providers、resume、preferences、verification_status）、`Employer/Store`、`JobPosting`（type/pay_range/shift/status/boost_level）、`Application`（狀態機）、`Conversation`、`Review`、`Notification`、`Subscription/Invoice`。

## 6. 配對推薦引擎演進

| 階段 | 作法 | 依據訊號 |
|---|---|---|
| Phase 1 | 規則式加權排序 | 地點距離、班別符合度、薪資期待、類別偏好 |
| Phase 2 | 語意向量比對 | Vertex AI text-embedding 比對履歷與職缺描述 |
| Phase 3 | Learning-to-Rank | 點擊率、應徵率、錄取率回饋持續優化 |

> 延續現有經驗：本 repo `services/matcher_service.py` 已驗證「不做 hard filter、全量加減分取前 N 筆」的排序策略，可作為 Phase 1 規則引擎設計起點。

## 7. LINE 生態整合角色

現有 repo 是材霈公司專屬單一雇主 LINE 招募 bot；新平台將 LINE 定位為「多渠道之一」：

- LINE Login 作為最低門檻註冊方式
- 職缺配對通知透過 LINE 訊息推播
- 對話式應徵：延續現有 repo 已驗證的 Firestore session/槽位模式 + Gemini 結構化 JSON 決策輸出
- LIFF 內嵌頁面承接完整表單場景（上傳照片、完整履歷）

## 8. 非功能需求

- **資安/合規**：履歷與身分文件加密儲存、多租戶資料隔離、Gemini 初篩詐騙職缺與違法工時內容
- **可用性/效能**：晚間週末尖峰壓測、事件驅動避免同步阻塞、Cloud Monitoring 告警（補足現有 repo 待辦中的告警缺口）
- **可觀測性**：結構化日誌 + 分散式追蹤、Sentry 補足現有「只靠 print() 寫 log」的缺口

## 9. 分階段路線圖

1. **Phase 1 · MVP（約 2–3 個月）**：核心求職迴圈（瀏覽/應徵/聊天）、LINE Login、Web PWA、PostgreSQL、規則式配對
2. **Phase 2 · 深化（約 2 個月）**：評價系統、Vertex AI 語意配對、雇主付費曝光與金流、LINE 對話式應徵
3. **Phase 3 · 規模化（約 2–3 個月）**：原生 App、ATS 拖拉看板、企業品牌頁、數據儀表板、內容社群、Learning-to-Rank
4. **Phase 4 · 擴張**：多語言、跨區域擴展、企業 API 開放、與外部 HR 系統整合

## 10. 技術棧建議

- **後端/服務**：FastAPI（模組化單體起步）、Cloud Run、Cloud Build CI/CD、Pub/Sub、Cloud Tasks
- **資料層**：Cloud SQL PostgreSQL、Firestore、Memorystore Redis、Typesense、GCS
- **AI/前端/金流**：Vertex AI Gemini、Next.js、LINE LIFF、React Native（Phase 3）、TapPay/綠界 ECPay、Sentry

## 11. 與現有專案的關係

現有 repo（`tsaipeilinebot`）是材霈公司專屬的單一雇主 LINE 招募 bot，核心業務邏輯（Notion 職缺庫、單一租戶）與此處規劃的多租戶市場平台在本質上不同。

**建議**：以全新專案起步，作為獨立的多租戶市場平台；現有 repo 的 LINE session/槽位模式、Gemini 結構化決策、429 重試等已驗證工程 pattern 可直接移植參考，但底層資料模型（Notion → 多租戶 PostgreSQL）需重新設計，漸進式改造既有 repo 的成本與風險都高於重新起步。

---

架構規劃草案，尚未進入實作階段。所有工期估算為基準，實際排程需依團隊規模與資源調整。
