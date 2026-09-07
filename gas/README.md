# 材霈職缺維護及表單

材霈有限公司內部作業系統（Google Apps Script Web App），涵蓋：

- 職缺送審與 AI 文案美化／就業服務法合規檢查（含批次增強）
- 薪資補款申請與主管審核流程
- 專案／廠商合約提報與歸檔
- 員工 LINE 綁定與 PIN 登入、組織架構與主管從屬查詢

## 專案資訊

- Script ID：`1vGFAH_NUyg5ig6M0uS9NLXFfq0VWukOoleEiivx59wh_YqhkpBiSwgwL`
- Web App 部署 ID：`AKfycbwi-j_mbnUDRFPKyEvL7arPv9UzHqpJLoNf9xHMOZTIf2yPN-ob5gDyFvwxKU63mIhVIA`
- 前端表單頁面另外託管於 Netlify，不在此 repo 版本控制範圍內

## 檔案結構

| 檔案 | 說明 |
| --- | --- |
| `程式碼.js` | 共用設定、驗證/快取工具、組織架構服務、Vertex AI 驗證、LINE/Email/試算表服務等核心模組 |
| `Project_Job.js` | 職缺管理：AI 文案生成與合規審查、送審卡片、Notion 同步 |
| `Project_Salary.js` | 薪資補款申請、審核與報表發送 |
| `Project_BatchEnhance.js` | 既有職缺批次 AI 美化 |
| `ProjectWorkflowService.js` | 專案／廠商合約提報與歸檔 |
| `appsscript.json` | GAS 專案設定檔（Web App 執行身份與存取權限） |

透過 `clasp` 與此 repo 同步（`clasp push` / `clasp deploy -i <部署ID>`）。
