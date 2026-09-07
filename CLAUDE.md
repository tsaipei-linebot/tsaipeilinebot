# 給 Claude 的操作說明

## 使用者背景（重要，每次都要記得）

這個 repo 的使用者（材霈有限公司）**沒有程式背景**。這代表：

- 每次實作完一個功能，**一定要明確列出後續需要使用者手動處理的步驟**，不要假設
  對方知道怎麼做——包括但不限於：設定 Cloud Run 環境變數、跑 `gcloud` 指令、
  申請/設定 LINE 官方帳號、Cloud Scheduler 排程、Google Sheets 權限分享、
  Apps Script 的指令碼屬性設定、`clasp push` 部署等。
- 步驟要寫成**可以直接複製貼上執行的指令**，並簡短說明「這一步在做什麼」跟
  「怎麼確認做對了」，不要只講抽象的技術概念。
- 遇到使用者回報錯誤訊息時，優先假設是操作步驟或環境設定的問題（例如目錄
  跑錯、忘記重新部署、環境變數打錯），逐步排查，不要只丟一堆可能原因。
- 不要假設對方懂 git、shell、程式碼——解釋時用類比或白話文，但技術指令本身
  要精確、不要簡化到錯誤。

## 專案架構

詳見 `HANDOFF.md`——這是這個專案一路以來的完整變更紀錄與架構說明，新增
功能、踩過的雷、上線前需要的設定步驟都記錄在裡面，是最主要的知識來源。
每次完成一個新功能或修正，都要更新 `HANDOFF.md` 對應章節。

## 這個 repo 之外，還有其他相關專案

材霈的內部系統不是只有這個 repo，還有幾個獨立但互相串接的專案，**不在這個
git repo 裡**：

- **`tsaipei-linebot/delivery-gas-project`**（GitHub repo，已經用 `clasp`
  跟真正的 Google Apps Script 專案同步）：配送部系統背後另外兩個獨立 LINE
  官方帳號（車輛回報、意外事件回報）的轉發邏輯，都是這支 GAS 專案在處理，
  轉發到這個 repo 的 `/delivery/api/*` webhook。如果使用者提到「車輛回報」
  「意外事件回報」「群組推播」這些配送部 LINE 群組相關的行為異常，要往這個
  repo 找，不是只看 `delivery/` 底下的 Python 程式碼。可以用
  `add_repo`（或 GitHub 搜尋 `tsaipei-linebot` 底下的 repo）把它加進當前
  session。**改完程式碼只是 git push，真正要生效還需要使用者（或有 `clasp`
  登入權限的人）額外執行 `git pull && clasp push`，這件事一定要主動提醒，
  不要漏掉。**
- **職缺維護及表單系統（GAS 後端）**：跟這個 repo 之間透過 `job_portal_sso.py`
  做免登入銜接（詳見 `HANDOFF.md`）。程式碼已經納入版控，就在**這個 repo**
  的 `gas/` 目錄下，但開發分支跟這個 recruitment-bot 專案不同，是獨立的
  `claude/google-apps-script-setup-7appxk` 分支（不要跟目前分支搞混，兩者
  是同一個 GitHub repo 底下平行開發的兩個獨立系統）。切換過去可以看
  `gas/README.md` 了解完整架構、Google Apps Script 專案設定、部署流程
  （`clasp push -f && clasp deploy -i <固定部署ID>`）。前端表單頁面
  `gas/index.html` 託管在 Netlify，使用者刻意不將其納入版控，改動需手動
  下載重新上傳。

## 部署與環境

- Cloud Run 服務：`recruitment-bot`，GCP 專案 `tsaipei-505807`，region
  `asia-east1`。
- 使用者慣用 GCP Cloud Shell 操作 `gcloud`／`git`，不是本機終端機。
