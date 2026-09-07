# 視訊面試中心 — Phase 1 POC 骨架

> **暫存說明**：這個資料夾目前暫放在 `tsaipeilinebot` repo 底下，純粹是因為建立獨立 repo 的 GitHub App 權限還沒開通，先求不遺失。架構上它是**跟 `tsaipeilinebot` 完全獨立的服務**（六個官方帳號共用，不是沛沛的一部分），權限開通後應該拆成自己的 repo，屆時把這個資料夾整個搬過去即可，程式碼本身不需要改動。

這是「視訊面試中心」專案技術規格裡 **第一階段（SDK POC，W1–3）** 的程式骨架，用來實測 Agora 與 LiveKit 在 LINE LIFF 內開視訊的體驗與延遲手感。刻意獨立於 `tsaipeilinebot` 這個現有 repo，架構上是六個官方帳號共用的獨立服務。

## 這個骨架做了什麼 / 沒做什麼

**有：**
- 一個全中文、可在 LINE 內建瀏覽器開啟的 LIFF 測試頁
- 後端用 API 動態核發 Agora 或 LiveKit 的加入 token（改 `.env` 一個變數就能切換測試哪一家）
- 一個固定的測試房間，多人打開連結即可互相看到彼此，用來測延遲與加入體驗

**沒有（之後階段才做，見技術規格文件）：**
- LIFF 身分綁定表單、四項比對邏輯（Phase 2）
- 候位佇列、一對一拆房（Phase 3）
- 各團隊官方帳號的 webhook 與招募系統整合（Phase 2–3）
- LIFF ID Token 的伺服器端驗證（目前僅信任前端 `liff.getProfile()`，正式環境要加上後端驗證）

## 前置準備

1. **LINE LIFF**：到 [LINE Developers Console](https://developers.line.biz/console/) 建立一個 LINE Login Channel，新增一個 LIFF app（Endpoint URL 填你部署後的網址，本機測試可先用 ngrok 之類的工具開一個公開網址，LIFF 不支援直接開 `localhost`），取得 **LIFF ID**。
2. **Agora**：到 [console.agora.io](https://console.agora.io) 建立免費專案，記下 **App ID** 與 **App Certificate**。
3. **LiveKit**：到 [cloud.livekit.io](https://cloud.livekit.io) 建立免費專案，記下 **API Key**、**API Secret**、**WebSocket URL**。

兩家都申請起來，之後改 `.env` 的 `VIDEO_SDK_PROVIDER` 就能切換測試，不用重寫程式。

## 安裝與啟動

```bash
cd video-interview-hub
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# 打開 .env，填入 LIFF_ID，以及 Agora 或 LiveKit 的金鑰

uvicorn backend.main:app --reload --port 8000
```

用 ngrok（或你們慣用的內網穿透工具）把 8000 埠開成公開 HTTPS 網址，填回 LIFF app 的 Endpoint URL，之後在 LINE 裡打開這個 LIFF 連結即可測試。**建議至少用兩支手機／兩個 LINE 帳號同時加入，才能驗證雙方視訊是否正常收得到彼此。**

## 目錄結構

```
video-interview-hub/
├── backend/
│   ├── main.py              # FastAPI 進入點，/api/config、/api/token
│   ├── config.py            # 讀取 .env
│   └── providers/
│       ├── agora_provider.py
│       └── livekit_provider.py
├── static/
│   ├── index.html           # LIFF 測試頁
│   ├── app.js                # LIFF 登入 + 動態載入 SDK + 加入房間
│   └── style.css
├── requirements.txt
└── .env.example
```

## 這一階段要驗證的問題

- 從 LINE 聊天室點連結，到看到雙方視訊畫面，中間卡不卡、要等多久？
- Agora 跟 LiveKit 在台灣的實測延遲、掉線頻率，手感上差多少？
- 全中文自建 UI 的操作流程，求職者角度會不會卡在哪一步？

驗證結果與下一步（身分綁定、招募系統整合）請對照「視訊面試中心」技術規格文件的第 10 節時程規劃。
