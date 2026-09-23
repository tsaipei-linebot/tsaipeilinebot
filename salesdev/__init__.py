"""少凱業務開發專區（/salesdev）的資料層：抓派遣公司職缺、去重歸併、
存 Firestore（2026-09-24 從外部 repo `tsaipei-linebot-recruitment-leads-scraper`
整併進來，原因與整體計畫見 HANDOFF.md「業務開發整併」那節）。

- `normalize.py`：文字修復、地址拆解、歸併鍵（純函式，不碰網路/資料庫）
- `classify.py`：派遣公司判斷、派遣公司「徵自己內部員工」判斷
- `scrapers/`：104、1111、小雞上工三個來源
- `repository.py`：Firestore 讀寫
- `pipeline.py`：每日抓取主流程（由 Cloud Scheduler 觸發）
- `sheet_import.py`：一次性把舊試算表資料匯入 Firestore
- `excel_export.py`：「下載 Excel」
"""
