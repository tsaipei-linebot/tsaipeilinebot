# 材霈內部系統開發

材霈有限公司內部系統，包含 LINE 招募客服機器人（Python / FastAPI）、配送部
系統、管理部系統，部署在 Google Cloud Run（服務名稱 `recruitment-bot`，
GCP 專案 `tsaipei-505807`，region `asia-east1`）。

## 相關文件

- **`SYSTEM_OVERVIEW.md`**：目前系統現況的快照——系統由哪些子系統組成、
  各自負責什麼、還缺哪些設定，想快速掌握全貌先看這份。
- **`CLAUDE.md`**：給 Claude 的操作說明（使用者背景、跨 repo 協作方式）。
- **`HANDOFF.md`**：完整的變更紀錄與架構說明，新增功能、踩過的雷、上線前
  需要的設定步驟都記錄在裡面，是最主要的知識來源。

## 相關但不在這個 repo 裡的專案

- **[`tsaipei-linebot/delivery-gas-project`](https://github.com/tsaipei-linebot/delivery-gas-project)**：
  配送部系統背後另外兩個獨立 LINE 官方帳號（車輛回報、意外事件回報）的
  轉發邏輯，Google Apps Script 專案，透過 `clasp` 同步。
