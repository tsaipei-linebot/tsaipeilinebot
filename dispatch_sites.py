"""多所派遣媒合共用的「所別」設定清單（2026-09-22 重構自桃園所專區，
新增高雄所）。這個功能原本叫 taoyuan_dispatch_*，只服務桃園所一個地點；
使用者確認之後還會陸續開其他所（先是高雄所），所以把「這是哪個所」抽成
一份設定清單，程式碼本身（`services/dispatch_service.py`、
`dispatch_bot.py`、`dispatch_line.py`、`dispatch_webhook_routes.py`、
`dispatch_routes.py`）完全不寫死所別名稱，之後開新所只要在這裡加一筆
設定＋申請一組新的 LINE 官方帳號，不用複製程式碼。

**2026-09-22 重構時桃園所這個功能還沒有任何真實資料在用**（人員/地點/
LINE 綁定都是空的），所以直接把 Firestore 資料表改名
（`taoyuan_dispatch_*` → `dispatch_*`，見 `services/dispatch_service.py`）、
網址改名（`/taoyuan-dispatch/...` → `/dispatch/{所別代碼}/...`），不需要
寫遷移腳本。

**業務邏輯（報名資格清單、LINE 綁定指令格式、報名/審核/推播流程）所有
所都完全共用同一套，不分所別客製**——2026-09-22 使用者明確確認。權限
判斷維持「帳號部門＝該所的部門名稱」字串比對（跟 `department` 欄位一樣
是字串比對，不是外鍵，見 `platform_departments.py` 的說明）。

`code` 用在網址路徑（`/dispatch/{code}/...`）跟 Firestore 資料的 `site`
欄位裡，刻意用英文代碼不用中文「桃園所」，避免網址裡出現中文字要
URL-encode 的問題；`name` 才是畫面上顯示給使用者看的中文名稱。
"""
SITE_TAOYUAN = "taoyuan"
SITE_KAOHSIUNG = "kaohsiung"

DISPATCH_SITES = {
    SITE_TAOYUAN: {
        "code": SITE_TAOYUAN,
        "name": "桃園所",
        "department": "桃園所",
        "line_token_env": "TAOYUAN_DISPATCH_LINE_CHANNEL_ACCESS_TOKEN",
        "line_secret_env": "TAOYUAN_DISPATCH_LINE_CHANNEL_SECRET",
    },
    SITE_KAOHSIUNG: {
        "code": SITE_KAOHSIUNG,
        "name": "高雄所",
        "department": "高雄所",
        "line_token_env": "KAOHSIUNG_DISPATCH_LINE_CHANNEL_ACCESS_TOKEN",
        "line_secret_env": "KAOHSIUNG_DISPATCH_LINE_CHANNEL_SECRET",
    },
}


def list_sites() -> list:
    """依這份設定的順序回傳所有所別設定，/portal 卡片、後台選單等要
    列出「有哪些所」的地方共用這個順序。"""
    return list(DISPATCH_SITES.values())


def get_site(code: str):
    return DISPATCH_SITES.get(code or "")


def site_exists(code: str) -> bool:
    return (code or "") in DISPATCH_SITES


def site_for_department(department: str):
    """帳號的部門字串反查對應的所別設定，找不到回傳 None——
    `services/dispatch_service.has_dispatch_access()`、/portal 卡片用。"""
    import platform_accounts

    normalized = platform_accounts.normalize_department(department)
    for site in DISPATCH_SITES.values():
        if platform_accounts.normalize_department(site["department"]) == normalized:
            return site
    return None
