import os

# ==========================================
# 管理部系統設定
# 跟配送部系統一樣是獨立掛載的子系統，共用同一個 GCP 專案（Firestore /
# Cloud Storage）跟同一批帳號（見根目錄 platform_accounts.py），但資料表
# 前綴改成 management_，避免互相汙染。
# ==========================================

# 網頁登入 session 的簽章密鑰。刻意跟配送部系統共用同一個環境變數/同一組
# secret key，而且 SessionMiddleware 的 cookie 名稱也要跟配送部一致
# （見 management/app.py），這樣同仁登入一次，兩個部門的 session 才會共用
# 同一顆瀏覽器 cookie，不用分別登入。
SESSION_SECRET_KEY = os.getenv("DELIVERY_SESSION_SECRET_KEY", "dev-only-insecure-secret-change-me")

# 公告/會議記錄/文件庫上傳的附件檔案，沿用跟配送部系統同一個 GCS bucket
# （同一份環境變數），只是 blob 路徑前綴改成 management/，不用另外申請一個
# bucket。
GCS_BUCKET_NAME = os.getenv("DELIVERY_GCS_BUCKET", "")

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20MB，規章/SOP 文件通常比配送部的證件影本大一些
ALLOWED_UPLOAD_CONTENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

# 文件庫分類（規章制度/SOP/教育訓練……），純粹是畫面上篩選跟顯示用的自由
# 分類，不影響任何存取權限邏輯。
DOCUMENT_CATEGORIES = [
    {"code": "regulation", "name": "規章制度"},
    {"code": "sop", "name": "SOP流程"},
    {"code": "training", "name": "教育訓練"},
    {"code": "template", "name": "範本文件"},
    {"code": "other", "name": "其他"},
]
DOCUMENT_CATEGORY_MAP = {c["code"]: c["name"] for c in DOCUMENT_CATEGORIES}
