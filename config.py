import os
from datetime import time
import pytz
from dotenv import load_dotenv

load_dotenv()


def _int_env(name: str, default: int) -> int:
    """安全讀取整數型環境變數。os.getenv(name, default) 只有在變數完全沒設定時
    才會用到預設值——如果在 Cloud Run 主控台把值清空但沒刪掉那一列，變數會是
    空字串，直接 int() 會拋例外、讓整個服務（跟招募機器人共用同一個 Cloud Run
    服務的配送部/管理部/人資等子系統也會一起）啟動失敗。這裡改成值缺漏或格式
    錯誤時都安全退回預設值，只印警告，不讓服務掛掉。"""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"[config.py 警告] 環境變數 {name} 的值「{raw}」不是合法整數，改用預設值 {default}")
        return default


def _float_env(name: str, default: float) -> float:
    """同 _int_env()，只是轉型成浮點數。"""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"[config.py 警告] 環境變數 {name} 的值「{raw}」不是合法數字，改用預設值 {default}")
        return default


# ==========================================
# 1. LINE 官方帳號設定
# ==========================================
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

# 測試環境 LINE 設定
TEST_LINE_CHANNEL_ACCESS_TOKEN = os.getenv("TEST_LINE_CHANNEL_ACCESS_TOKEN", LINE_CHANNEL_ACCESS_TOKEN)
TEST_LINE_CHANNEL_SECRET = os.getenv("TEST_LINE_CHANNEL_SECRET", LINE_CHANNEL_SECRET)

# ==========================================
# 2. 金鑰與 Notion 資料庫 ID
# ==========================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_JOBS_DB_ID = os.getenv("NOTION_JOBS_DB_ID")
NOTION_FAQ_DB_ID = os.getenv("NOTION_FAQ_DB_ID")
# 求職者提問追蹤資料庫（選填）：求職者問到 FAQ 沒收錄的問題時，除了寫進上面
# NOTION_FAQ_DB_ID（給未來的求職者累積常見問答庫用，會去重），也會在這個獨立
# 資料庫留一筆「這次是誰問的」的紀錄（不去重，每個人都要各自留一筆），讓招募
# 專員能回頭去 LINE 官方帳號後台找到這個人手動回覆。沒設定時只會印 log 跳過，
# 不影響其他功能（見 HANDOFF.md）。
NOTION_UNRESOLVED_QUESTIONS_DB_ID = os.getenv("NOTION_UNRESOLVED_QUESTIONS_DB_ID", "")
OFFICIAL_WEBSITE_BASE = os.getenv("OFFICIAL_WEBSITE_BASE", "https://tsaipei.netlify.app")
# 履歷點擊紀錄資料庫（選填）：職缺卡片「填寫線上履歷」按鈕點下去，記錄是誰
# 點的，方便招募專員追蹤誰對哪個職缺有興趣。沒設定時這個功能會安全跳過（按鈕
# 一樣能正常導去履歷網站，只是不會留紀錄），不影響其他功能。
NOTION_RESUME_CLICK_LOG_DB_ID = os.getenv("NOTION_RESUME_CLICK_LOG_DB_ID", "")
# 這支服務自己的對外網址（例如 Cloud Run 的 https://recruitment-bot-xxxxx-xx.a.run.app）。
# LINE 的「uri」類型按鈕點下去完全不會觸發 webhook，機器人原本沒辦法知道誰點了
# 「填寫線上履歷」——設定這個變數後，卡片上的按鈕會先連到我們自己這支服務的
# /apply-click 端點記錄點擊，再 302 轉址到真正的履歷網站，求職者感覺不出差異。
# 沒設定時（空字串）維持原本行為：按鈕直接連到履歷網站，不會記錄點擊。
SERVICE_BASE_URL = os.getenv("SERVICE_BASE_URL", "")

# ==========================================
# 3. 快取與 Session 設定
# ==========================================
SESSION_TTL = 7 * 24 * 3600  # 7 天對話記憶 (秒)
CACHE_TTL = 30               # Notion 快取 30 秒

# ==========================================
# 4. Notion 讀取白名單 (完整納入 休假方式、系統廠商名稱 與 職缺名稱)
# ==========================================
ALLOWED_PROPERTIES = {
    "職缺名稱", "職缺名稱(對外)", "職務類別", "縣市", "行政區", "行業別",
    "全/兼職", "班別", "薪資", "休假方式", "領薪方式", "工作內容(對外)", "狀態",
    "精華亮點", "排版工作說明", "系統廠商名稱", "福利"
}

# ==========================================
# 5. 精準履歷路由網址（可用環境變數覆蓋，未設定時沿用原本的預設值）
# ==========================================
DEFAULT_RESUME_URLS = {
    "Spx": os.getenv(
        "RESUME_URL_SPX",
        "https://resume.tsaipei.com.tw/eyJEYXRhTm8iOiIiLCJVc2VyTm8iOiI0ODIiLCJSZXN1bWVLaW5kIjoiU3B4IiwiU3lzdGVtIjoiWWVzIn0=?openExternalBrowser=1"
    ),
    "Service": os.getenv(
        "RESUME_URL_SERVICE",
        "https://resume.tsaipei.com.tw/eyJEYXRhTm8iOiIiLCJVc2VyTm8iOiI0ODIiLCJSZXN1bWVLaW5kIjoiU2VydmljZSIsIlN5c3RlbSI6IlllcyJ9?openExternalBrowser=1"
    ),
    "Manufacture": os.getenv(
        "RESUME_URL_MANUFACTURE",
        "https://resume.tsaipei.com.tw/eyJEYXRhTm8iOiIiLCJVc2VyTm8iOiI0ODIiLCJSZXN1bWVLaW5kIjoiTWFudWZhY3R1cmUiLCJTeXN0ZW0iOiJZZXMifQ==?openExternalBrowser=1"
    )
}

# ==========================================
# 6. Google Cloud / Vertex AI 設定
# ==========================================
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "tsaipei-505807")
GCP_LOCATION = os.getenv("GCP_LOCATION", "global")

# ==========================================
# 7. 壓力測試專用（僅供內部壓力測試腳本使用，預設關閉）
# main.py 的 /internal/load-test-message 端點需要這組密鑰才會受理請求；
# 沒有設定（空字串）時該端點一律回傳 403，等同完全關閉。
# ==========================================
LOAD_TEST_SECRET = os.getenv("LOAD_TEST_SECRET", "")

# ==========================================
# 8. 白天／晚間回覆時段（日夜接力，見 HANDOFF.md）
# 同仁上班時間為每天 10:00–19:00（含週末，班表相同）。這裡刻意抓 10 分鐘
# 交接緩衝：機器人比同仁實際下班時間（19:00）提早 10 分鐘啟動、比同仁實際
# 上班時間（10:00）延後 10 分鐘才停止——目的是寧可偶爾跟同仁重複回覆，
# 也不要讓求職者在交接空檔完全沒有任何一邊回覆。
#
# STAFFED_HOURS_START～STAFFED_HOURS_END 這段時間內，沛沛完全不主動回覆，
# 交給真人專員在 LINE 聊天模式手動處理（見 handlers/message_handler.py
# 的 _is_staffed_hours()）。
#
# STAFFED_HOURS_GUARD_ENABLED：這個機制的總開關，預設關閉（不管幾點都照舊
# 回覆，等同這個功能還沒上線）。還在測試頻道、LINE 官方帳號後台的「回應時間
# 設定」排程還沒設好之前，開著這個守門邏輯會讓白天測試時機器人看起來像故障
# （完全不回覆），所以刻意讓程式碼合併進 main 後不會立刻生效。等正式要切換
# 到「白天真人、晚上沛沛」的運作模式時，才去 Cloud Run 設定環境變數
# STAFFED_HOURS_GUARD_ENABLED=true 打開，不需要再改程式碼、重新部署一次即可。
# ==========================================
STAFFED_HOURS_GUARD_ENABLED = os.getenv("STAFFED_HOURS_GUARD_ENABLED", "false").strip().lower() in ("1", "true", "yes")
STAFFED_HOURS_START = time(10, 10)
STAFFED_HOURS_END = time(18, 50)
TAIPEI_TZ = pytz.timezone("Asia/Taipei")

# ==========================================
# 9. 每週新工廠登記監控設定
# 資料源：政府資料開放平台《登記工廠名錄》(經濟部產業發展署，dataset id 6569)
# ==========================================
FACTORY_OPENDATA_DATASET_ID = os.getenv("FACTORY_OPENDATA_DATASET_ID", "6569")
FACTORY_WATCH_LOOKBACK_DAYS = _int_env("FACTORY_WATCH_LOOKBACK_DAYS", 10)
FACTORY_WATCH_SHEET_ID = os.getenv("FACTORY_WATCH_SHEET_ID", "")
FACTORY_WATCH_SHEET_NAME = os.getenv("FACTORY_WATCH_SHEET_NAME", "新登記工廠")
# 目前尚未決定要推播給哪個 LINE 帳號/群組，先留空；設定後即可自動開始推播
FACTORY_WATCH_LINE_TARGET_ID = os.getenv("FACTORY_WATCH_LINE_TARGET_ID", "")
# Cloud Scheduler 呼叫 /internal/factory-watch/run 時要帶的共用密鑰，避免端點被任意觸發
FACTORY_WATCH_TRIGGER_SECRET = os.getenv("FACTORY_WATCH_TRIGGER_SECRET", "")

# ==========================================
# 10. 少凱業務開發專區（/salesdev）唯讀顯示的 Google Sheet
# 預設值是原本 /portal 首頁卡片直接連去編輯的那份「派遣客戶開發名單、
# 新登記工廠監控彙整」試算表；改用別的試算表時可以用環境變數覆蓋，不用
# 改程式碼。這份試算表需要分享「檢視者」權限給 Cloud Run 服務帳戶才讀得到
# （見 services/salesdev_sheet_service.py）。
# ==========================================
SALESDEV_SHEET_ID = os.getenv("SALESDEV_SHEET_ID", "1DdkW0eOP8PrvXlioVYY6LYzpswHTrIJotgvCMe-oS-Q")

# ==========================================
# 11. 我的專區（/me）：薪資補款紀錄
# 資料來源是「職缺維護表單」（Netlify + Apps Script，跟這個 repo 完全獨立，
# 見 CLAUDE.md）背後的 Google Sheet，這裡只讀，不寫回。
# 「員工主管組織表」分頁：同仁姓名對到主管姓名（可能是逗號分隔的多個主管），
# 用來判斷誰能看到誰的補款紀錄。「薪資補款紀錄」分頁：實際送出的申請，
# 「申請人姓名」是送出申請的同仁本人（不是被補款的配送人員，配送人員記在
# 「員工姓名」欄位）。
# ==========================================
SALARY_REPAYMENT_SHEET_ID = os.getenv(
    "SALARY_REPAYMENT_SHEET_ID", "1rys_WkW2qZmqm9NFovlDWb_PXL_80seDTxelFTd9xSk"
)
SALARY_REPAYMENT_ORG_SHEET_NAME = os.getenv("SALARY_REPAYMENT_ORG_SHEET_NAME", "員工主管組織表")
SALARY_REPAYMENT_RECORDS_SHEET_NAME = os.getenv("SALARY_REPAYMENT_RECORDS_SHEET_NAME", "薪資補款紀錄")

# 薪資補款「送出」表單（/me/salary-repayment/new，2026-09 新增，見 HANDOFF.md
# 「方案 A」）：材霈平台這邊只收表單，送出時原封不動轉手給職缺維護表單背後
# 那支 GAS 程式的 Web App 網址（type=SUBMIT_SALARY），推播/核准/寫紀錄/發信
# 完全由那支 GAS 程式繼續處理，這裡沒有另外存一份。這個網址要到那個 Google
# Apps Script 專案的「部署」>「管理部署作業」裡複製「網頁應用程式」的網址
# （不是 Apps Script 編輯器本身的網址），沒設定時這個表單會直接顯示錯誤訊息、
# 不會讓同仁誤以為送出成功了。
JOB_PORTAL_GAS_WEBAPP_URL = os.getenv("JOB_PORTAL_GAS_WEBAPP_URL", "")

# ==========================================
# 12. 每日健康報告／FAQ 週報（監控與告警機制，見 HANDOFF.md）
# 只有一個機制：Cloud Scheduler 每天呼叫一次 /internal/daily-report/run。
# 週報部分（FAQ 候選清單＋建議新增的職缺關鍵字）只在 FAQ_WEEKLY_REPORT_WEEKDAY
# 當天才會附加在當日報告後面，不是另外開一個排程。
# DAILY_REPORT_ENABLED 預設關閉：程式碼合併進 main 後不會立刻生效，等使用者
# 確定要切換到正式頻道才手動開啟（作法比照 STAFFED_HOURS_GUARD_ENABLED）。
# ==========================================
DAILY_REPORT_ENABLED = os.getenv("DAILY_REPORT_ENABLED", "false").strip().lower() in ("1", "true", "yes")
# Cloud Scheduler 呼叫 /internal/daily-report/run 時要帶的共用密鑰
DAILY_REPORT_TRIGGER_SECRET = os.getenv("DAILY_REPORT_TRIGGER_SECRET", "")
# 目前尚未決定要推播給哪個 LINE 群組，先留空；設定後即可自動開始推播（沒設定只會印 log）
DAILY_REPORT_LINE_TARGET_ID = os.getenv("DAILY_REPORT_LINE_TARGET_ID", "")
# 第二層門檻：過去 24（或週報時 7*24）小時內，任一固定時間區塊的 p95 延遲超過這個秒數就算變慢
DAILY_REPORT_LATENCY_P95_THRESHOLD_SECONDS = _float_env("DAILY_REPORT_LATENCY_P95_THRESHOLD_SECONDS", 12)
# 區塊大小（分鐘）：用固定區塊取代「任一 3 分鐘滑動窗口」，判斷邏輯簡單很多、效果差異不大
DAILY_REPORT_LATENCY_BUCKET_MINUTES = _int_env("DAILY_REPORT_LATENCY_BUCKET_MINUTES", 5)
# 每週報告要附加在哪一天的每日報告後面（0=一, 6=日，Python datetime.weekday() 定義）
FAQ_WEEKLY_REPORT_WEEKDAY = _int_env("FAQ_WEEKLY_REPORT_WEEKDAY", 0)
# 職缺類問句「這個類別/廠商本週被問幾次、但沒有專屬直達路徑」達到這個次數才列入建議清單
FAQ_CANDIDATE_KEYWORD_GAP_MIN_COUNT = _int_env("FAQ_CANDIDATE_KEYWORD_GAP_MIN_COUNT", 5)
# 上線初期使用：FAQ 候選清單／建議新增的職缺關鍵字原本只在 FAQ_WEEKLY_REPORT_WEEKDAY
# 那天出現（預設週一）。剛上線這段期間流量還小、需要密切觀察，開啟這個開關後，
# 不管星期幾，FAQ 候選清單都會每天出現在日報裡，方便招募專員更即時掌握求職者
# 問到哪些還沒收錄的問題。健康狀況檢查的時間窗口不受影響（只有真正的「週報日」
# 才會是過去 7 天，其餘每天都還是過去 24 小時），避免健康狀況因為視窗被拉長而
# 誤判。上線穩定後可以再把這個環境變數關掉，改回原本每週一次的頻率。
FAQ_REPORT_DAILY_MODE = os.getenv("FAQ_REPORT_DAILY_MODE", "false").strip().lower() in ("1", "true", "yes")

# ==========================================
# 13. AI 決策限時同步等待秒數（見 HANDOFF.md「監控與告警機制」壓測章節）
# 主執行緒最多同步等這麼多秒：時限內算完就用免費的 reply_token 回覆；超過
# 時限才先回「查詢中」的 ack、改用計費的 push_message 補發正式答案（見
# handlers/message_handler.py 的 _AI_DECISION_EXECUTOR 說明）。
# 這個數字離 LINE reply_token 30 秒硬性上限的緩衝要留夠——從 LINE 送出訊息
# 到我們的程式碼真正開始計時，中間可能已經有排隊等執行緒的延遲（尤其高併發
# 時），這段時間我們量不到，所以不能把這個數字設得太接近 30 秒，否則「查詢中」
# 這句安慰訊息本身都有可能因為 reply_token 已過期而送出失敗。原本是寫死在
# handlers/message_handler.py 裡的常數（預設 8），改成環境變數是因為這個數字
# 已經因為實測結果調整過不只一次，改用環境變數之後之後要再調整不用改程式碼、
# 重新部署。
# ==========================================
AI_DECISION_SYNC_TIMEOUT_SECONDS = _int_env("AI_DECISION_SYNC_TIMEOUT_SECONDS", 15)
