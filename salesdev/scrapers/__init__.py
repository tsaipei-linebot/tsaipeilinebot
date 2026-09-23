"""三個職缺來源（104、1111、小雞上工）。每個模組都提供
`collect_leads(client, deadline) -> list[JobLead]`，`deadline` 是
`time.monotonic()` 的截止時間，時間到就停止翻頁、回傳已經抓到的部分
（見 salesdev/pipeline.py 為什麼需要時間上限）。

2026-09-24 從 `tsaipei-linebot-recruitment-leads-scraper` 搬進來，行為
刻意維持跟原本一樣（那邊已經用 GitHub Actions 實跑驗證過），只改了：
- 104：不再逐筆打職缺詳情 API——那支 API 一律回 404（原 repo 的 README
  也寫了），每筆白白等 1.8 秒，53 筆就浪費 1 分半；職缺編號改從網址取，
  跟舊試算表對得起來
- 小雞上工：強制用 UTF-8 解碼（修標題表情符號亂碼）、修地址被接錯
- 不再依賴 BeautifulSoup/lxml（這個 repo 沒裝），JSON-LD 改用正規表示式取出
"""
