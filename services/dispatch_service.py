"""多所派遣媒合（/dispatch/{所別代碼}，2026-09-22 重構自
`taoyuan_dispatch_service.py`）：各所有自己獨立的派遣業務跟官方帳號，
人員是派遣人員（不是配送部的外送騎士），業務性質不一樣，所以獨立開一個
新模組，不掛在 delivery/ 底下、也不共用 delivery 既有的人員/騎士資料。

**多所共用架構（2026-09-22 重構）**：這支檔案原本叫
`taoyuan_dispatch_service.py`，只服務桃園所一個地點，寫死了「桃園所」
這個部門字串跟 `taoyuan_dispatch_*` 系列 Firestore 資料表名稱。使用者
確認之後還會陸續開其他所（先是高雄所），為了不用每開一個新所就複製
一份幾乎一樣的程式碼，改成所有所共用這一套程式碼＋同一份 Firestore
資料表，每筆資料多存一個 `site` 欄位（見 `dispatch_sites.py` 的
`code`），所有讀寫函式都用 `site` 參數篩選/標記。哪個所有哪些設定（
中文名稱、部門權限字串、LINE 憑證環境變數名稱）集中在
`dispatch_sites.py`，之後開新所只要在那份清單加一筆設定，不用動這支
檔案。重構當下桃園所這個功能還沒有任何真實資料在用，所以直接把資料表
改名（`taoyuan_dispatch_personnel` → `dispatch_personnel` 等），不需要
寫遷移腳本。

**權限模型**：這個模組不掛進 `platform_accounts.MODULES`（不是
`/accounts` 那種每個帳號勾選開放的模組權限），改成照「部門」卡權限——
帳號的 `department` 是這個所的部門名稱（`dispatch_sites.py` 的
`department`）、或是全平台管理員，才看得到卡片、進得去頁面。跟
`/contract-summary`（`contract_summary_routes.py` 的
`viewer_has_any_department_access()`）是同一種做法，理由也一樣：這個
功能天生就是「這個所自己的業務」，不需要另外開一個模組權限讓其他部門
的人也能勾選開放。

**人員管理**（含「人員資格」複選）、**地點管理**，都支援手動新增跟
CSV 批次匯入。**LINE 官方帳號綁定**（人員傳送「綁定+姓名+電話」訊息
比對既有人員資料）、**需求時段開單**（地點/時段/人數/所需人員資格）、
人員在 LINE 上查詢+報名、主管在網頁上審核核准/駁回、核准/駁回時主動
推播訊息給人員——這些業務邏輯所有所完全共用同一套，不分所別客製
（2026-09-22 使用者明確確認）。每個所自己有獨立的 Channel Token/Secret
（見 `dispatch_line.py`），webhook 直接打進這支 Cloud Run 服務（見
`dispatch_webhook_routes.py`），比照 `management/line_bot.py` 那組管理部
帳號的做法，不透過 delivery 模組那套 GAS 轉發機制。

**跨所資料隔離**：Firestore 文件 ID 本身是全域唯一的隨機字串，不會跨所
撞號，但為了避免「猜到／記錯另一個所的文件 ID」就意外讀到別所資料，
凡是「用文件 ID 直接查一筆」的函式（`get_personnel()`／`get_location()`
／`get_posting()`／`get_registration()`）都會再檢查那筆資料的 `site`
欄位是否跟呼叫端要求的所別一致，不一致視同「查無資料」回傳 None，不會
洩漏別所的資料內容。LINE 綁定（`bindings_ref()`）文件 ID 額外把所別代碼
併進去（`{site}__{line_user_id}`）——因為 LINE 的 `user_id` 在少數情況下
可能跨不同 Channel 重複，不能只靠 `line_user_id` 本身當文件 ID 保證不同
所之間不會互相覆蓋。
"""
import csv
import io
import time
from datetime import datetime

from google.cloud import firestore

from config import TAIPEI_TZ
from platform_db import get_db

PERSONNEL_COLLECTION = "dispatch_personnel"
LOCATIONS_COLLECTION = "dispatch_locations"
BINDINGS_COLLECTION = "dispatch_line_bindings"
POSTINGS_COLLECTION = "dispatch_postings"
REGISTRATIONS_COLLECTION = "dispatch_registrations"

# 人員資格：複選，開需求時段時勾選「這筆需求要哪些資格才看得到」，人員
# 要至少符合其中一項才看得到、能報名這筆需求。清單本身先寫死在這裡，
# 不像合作方式管理那樣做成動態清單——使用者目前只提出這 4 種固定類別，
# 之後真的需要再開放自訂；所有所共用同一套，不分所別客製。
QUALIFICATION_RESTOCKING = "restocking"
QUALIFICATION_OPERATOR = "operator"
QUALIFICATION_FOOD_WITH_CHECKUP = "food_with_checkup"
QUALIFICATION_FOOD_WITHOUT_CHECKUP = "food_without_checkup"
QUALIFICATIONS = [
    {"code": QUALIFICATION_RESTOCKING, "name": "理貨"},
    {"code": QUALIFICATION_OPERATOR, "name": "作業員"},
    {"code": QUALIFICATION_FOOD_WITH_CHECKUP, "name": "餐飲（有體檢）"},
    {"code": QUALIFICATION_FOOD_WITHOUT_CHECKUP, "name": "餐飲（無體檢）"},
]
QUALIFICATION_CODES = {q["code"] for q in QUALIFICATIONS}
QUALIFICATION_MAP = {q["code"]: q["name"] for q in QUALIFICATIONS}

POSTING_STATUS_OPEN = "open"
POSTING_STATUS_CLOSED = "closed"

REGISTRATION_STATUS_PENDING = "pending"
REGISTRATION_STATUS_APPROVED = "approved"
REGISTRATION_STATUS_REJECTED = "rejected"


def has_dispatch_access(account: dict, site: str) -> bool:
    """全平台管理員，或帳號部門是這個所的部門名稱，才看得到卡片／進得去
    頁面。跟 `/contract-summary` 的部門權限判斷是同一種做法（見本檔案
    開頭說明）。所別代碼不存在時一律回傳 False。"""
    if not account:
        return False
    if account.get("is_platform_admin"):
        return True
    from dispatch_sites import get_site

    site_config = get_site(site)
    if not site_config:
        return False
    return account.get("department") == site_config["department"]


# ==========================================
# 人員管理
# ==========================================
def personnel_ref():
    return get_db().collection(PERSONNEL_COLLECTION)


def _to_personnel(doc_id: str, data: dict) -> dict:
    return {
        "id": doc_id,
        "site": data.get("site", "") or "",
        "name": data.get("name", "") or "",
        "phone": data.get("phone", "") or "",
        "qualifications": data.get("qualifications") or [],
        "active": data.get("active", True),
        "created_at": data.get("created_at", 0) or 0,
    }


def create_personnel(site: str, name: str, phone: str, qualifications: list, created_by: str = "") -> str:
    now = time.time()
    doc_ref = personnel_ref().document()
    doc_ref.set(
        {
            "site": site,
            "name": name,
            "phone": phone,
            "qualifications": [q for q in (qualifications or []) if q in QUALIFICATION_CODES],
            "active": True,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }
    )
    return doc_ref.id


def list_personnel(site: str, include_inactive: bool = True) -> list:
    result = [
        _to_personnel(s.id, s.to_dict() or {})
        for s in personnel_ref().where("site", "==", site).stream()
    ]
    if not include_inactive:
        result = [p for p in result if p["active"]]
    result.sort(key=lambda p: p["name"])
    return result


def get_personnel(site: str, personnel_id: str):
    snapshot = personnel_ref().document(personnel_id).get()
    if not snapshot.exists:
        return None
    personnel = _to_personnel(snapshot.id, snapshot.to_dict() or {})
    if personnel["site"] != site:
        return None
    return personnel


def update_personnel_qualifications(site: str, personnel_id: str, qualifications: list) -> bool:
    if not get_personnel(site, personnel_id):
        return False
    personnel_ref().document(personnel_id).update(
        {
            "qualifications": [q for q in (qualifications or []) if q in QUALIFICATION_CODES],
            "updated_at": time.time(),
        }
    )
    return True


def update_personnel_info(site: str, personnel_id: str, name: str, phone: str) -> bool:
    if not get_personnel(site, personnel_id):
        return False
    personnel_ref().document(personnel_id).update({"name": name, "phone": phone, "updated_at": time.time()})
    return True


def set_personnel_active(site: str, personnel_id: str, active: bool) -> bool:
    if not get_personnel(site, personnel_id):
        return False
    personnel_ref().document(personnel_id).update({"active": active, "updated_at": time.time()})
    return True


def find_personnel_by_name_and_phone(site: str, name: str, phone: str):
    """CSV 批次匯入用：同一個所裡，同一個「姓名+電話」已經有資料時回傳
    該筆，讓匯入跳過重複，不會重複建立——跟配送部人員批次匯入的重複
    判斷同一種做法。姓名、電話兩個欄位都要有值才會查。"""
    name = (name or "").strip()
    phone = (phone or "").strip()
    if not name or not phone:
        return None
    query = (
        personnel_ref()
        .where("site", "==", site)
        .where("name", "==", name)
        .where("phone", "==", phone)
        .limit(1)
    )
    for snapshot in query.stream():
        return _to_personnel(snapshot.id, snapshot.to_dict() or {})
    return None


# ==========================================
# 地點管理
# ==========================================
def locations_ref():
    return get_db().collection(LOCATIONS_COLLECTION)


def _to_location(doc_id: str, data: dict) -> dict:
    return {
        "id": doc_id,
        "site": data.get("site", "") or "",
        "name": data.get("name", "") or "",
        "lat": data.get("lat"),
        "lng": data.get("lng"),
        "active": data.get("active", True),
    }


def create_location(site: str, name: str, lat: float, lng: float, created_by: str = "") -> str:
    now = time.time()
    doc_ref = locations_ref().document()
    doc_ref.set(
        {
            "site": site,
            "name": name,
            "lat": lat,
            "lng": lng,
            "active": True,
            "created_by": created_by,
            "created_at": now,
        }
    )
    return doc_ref.id


def list_locations(site: str, include_inactive: bool = True) -> list:
    result = [
        _to_location(s.id, s.to_dict() or {})
        for s in locations_ref().where("site", "==", site).stream()
    ]
    if not include_inactive:
        result = [loc for loc in result if loc["active"]]
    result.sort(key=lambda loc: loc["name"])
    return result


def get_location(site: str, location_id: str):
    snapshot = locations_ref().document(location_id).get()
    if not snapshot.exists:
        return None
    location = _to_location(snapshot.id, snapshot.to_dict() or {})
    if location["site"] != site:
        return None
    return location


def set_location_active(site: str, location_id: str, active: bool) -> bool:
    if not get_location(site, location_id):
        return False
    locations_ref().document(location_id).update({"active": active})
    return True


def find_location_by_name(site: str, name: str):
    name = (name or "").strip()
    if not name:
        return None
    for loc in list_locations(site, include_inactive=False):
        if loc["name"] == name:
            return loc
    return None


# ==========================================
# CSV 批次匯入（純函式，不碰 Firestore、不需要知道所別，方便寫單元測試；
# 是否真的寫入交給呼叫端 dispatch_routes.py 決定）
# ==========================================
_PERSONNEL_REQUIRED_HEADERS = {"姓名", "電話"}
_LOCATION_REQUIRED_HEADERS = {"地點", "緯度", "經度"}


def _decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "cp950"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _parse_qualifications_cell(raw: str):
    """回傳 (資格代碼 list, 是否有看不懂的值)。儲存格允許填中文名稱或
    代碼，多個用頓號/逗號分隔，例如「理貨、作業員」或
    「restocking,operator」。看不懂的值列出來但不擋這一列（那一項資格
    直接略過，比整列失敗更貼近「先匯進來、資格之後再手動補」的實際
    使用情境）。"""
    raw = (raw or "").strip()
    if not raw:
        return [], []
    name_to_code = {q["name"]: q["code"] for q in QUALIFICATIONS}
    parts = [p.strip() for p in raw.replace("、", ",").split(",") if p.strip()]
    codes = []
    unknown = []
    for part in parts:
        if part in QUALIFICATION_CODES:
            codes.append(part)
        elif part in name_to_code:
            codes.append(name_to_code[part])
        else:
            unknown.append(part)
    return codes, unknown


def parse_personnel_csv(content: bytes):
    """回傳 (rows, header_error)。欄位：姓名、電話（必填），人員資格
    （選填，逗號/頓號分隔，填中文名稱或代碼皆可）。"""
    text = _decode(content)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return [], "檔案是空的或無法辨識表頭"
    headers = {h.strip() for h in reader.fieldnames if h}
    missing = _PERSONNEL_REQUIRED_HEADERS - headers
    if missing:
        return [], f"缺少必要欄位：{'、'.join(sorted(missing))}"

    rows = []
    for i, raw in enumerate(reader, start=2):
        name = (raw.get("姓名") or "").strip()
        phone = (raw.get("電話") or "").strip()
        if not name and not phone:
            continue
        if not name:
            rows.append({"row": i, "ok": False, "error": "姓名為空", "name": name, "phone": phone})
            continue
        if not phone:
            rows.append({"row": i, "ok": False, "error": "電話為空", "name": name, "phone": phone})
            continue
        qualifications, unknown = _parse_qualifications_cell(raw.get("人員資格") or "")
        rows.append(
            {
                "row": i,
                "ok": True,
                "name": name,
                "phone": phone,
                "qualifications": qualifications,
                "unknown_qualifications": unknown,
            }
        )
    return rows, None


def parse_location_csv(content: bytes):
    """回傳 (rows, header_error)。欄位：地點、緯度、經度（皆必填）。"""
    text = _decode(content)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return [], "檔案是空的或無法辨識表頭"
    headers = {h.strip() for h in reader.fieldnames if h}
    missing = _LOCATION_REQUIRED_HEADERS - headers
    if missing:
        return [], f"缺少必要欄位：{'、'.join(sorted(missing))}"

    rows = []
    for i, raw in enumerate(reader, start=2):
        name = (raw.get("地點") or "").strip()
        lat_raw = (raw.get("緯度") or "").strip()
        lng_raw = (raw.get("經度") or "").strip()
        if not name and not lat_raw and not lng_raw:
            continue
        if not name:
            rows.append({"row": i, "ok": False, "error": "地點名稱為空", "name": name})
            continue
        try:
            lat = float(lat_raw)
            lng = float(lng_raw)
        except ValueError:
            rows.append({"row": i, "ok": False, "error": "緯度/經度要填數字", "name": name})
            continue
        rows.append({"row": i, "ok": True, "name": name, "lat": lat, "lng": lng})
    return rows, None


# ==========================================
# LINE 官方帳號綁定
# ==========================================
def bindings_ref():
    return get_db().collection(BINDINGS_COLLECTION)


def _binding_doc_id(site: str, line_user_id: str) -> str:
    return f"{site}__{line_user_id}"


def bind_line_user(site: str, line_user_id: str, personnel_id: str, personnel_name: str) -> None:
    """把 LINE 使用者跟人員資料綁定——文件 ID 用「所別__LINE user_id」
    組成（見本檔案開頭「跨所資料隔離」說明），webhook 收到訊息時可以
    直接用所別＋event.source.user_id 查文件。同一個 LINE 帳號在同一個所
    重新傳送綁定訊息會直接覆蓋成最新對應的人員。"""
    bindings_ref().document(_binding_doc_id(site, line_user_id)).set(
        {
            "site": site,
            "line_user_id": line_user_id,
            "personnel_id": personnel_id,
            "personnel_name": personnel_name,
            "bound_at": time.time(),
        }
    )


def get_binding(site: str, line_user_id: str):
    if not line_user_id:
        return None
    snapshot = bindings_ref().document(_binding_doc_id(site, line_user_id)).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["line_user_id"] = line_user_id
    return data


def get_bound_personnel(site: str, line_user_id: str):
    """回傳這個 LINE 使用者在這個所目前綁定的人員最新資料（即時查
    personnel_ref()，不吃綁定當下存的快照）——人員資格之後可能被管理員
    修改，要用當下的資料判斷看得到哪些需求，跟配送部
    rider_repository.py `rider_feature_category()` 即時查、不信任綁定
    快照的考量一樣。綁定存在但對應的人員資料已被刪除（或被停用）時
    回傳 None。"""
    binding = get_binding(site, line_user_id)
    if not binding:
        return None
    personnel = get_personnel(site, binding["personnel_id"])
    if not personnel or not personnel.get("active", True):
        return None
    return personnel


# ==========================================
# 需求時段：管理員開出地點/時段/人數/需要的人員資格，人員報名，主管審核
# 核准/駁回。跟配送部報班（delivery/rider_repository.py 的
# create_shift_posting/register_shift）是同一套「人工審核制、報名當下不
# 檢查名額」做法，差異是這裡多了「人員資格」欄位做可視範圍篩選（沒有
# GPS 半徑篩選——這些所的地點只是參考用途，不像配送部要即時比對騎士的
# 所在位置）。
# ==========================================
def postings_ref():
    return get_db().collection(POSTINGS_COLLECTION)


def registrations_ref():
    return get_db().collection(REGISTRATIONS_COLLECTION)


def _to_posting(doc_id: str, data: dict) -> dict:
    return {
        "id": doc_id,
        "site": data.get("site", "") or "",
        "short_code": data.get("short_code", "") or "",
        "location_name": data.get("location_name", "") or "",
        "start_time": data.get("start_time", 0) or 0,
        "end_time": data.get("end_time", 0) or 0,
        "headcount": data.get("headcount", 0) or 0,
        "required_qualifications": data.get("required_qualifications") or [],
        "status": data.get("status", POSTING_STATUS_OPEN) or POSTING_STATUS_OPEN,
        "created_by": data.get("created_by", "") or "",
        "created_at": data.get("created_at", 0) or 0,
    }


def create_posting(
    site: str,
    location_name: str,
    start_time: float,
    end_time: float,
    headcount: int,
    required_qualifications: list,
    created_by: str = "",
) -> str:
    now = time.time()
    ref = postings_ref().document()
    # short_code：文件 ID 最後 6 碼轉大寫，人員在 LINE 上用「報名 <代碼>」
    # 報名時打這個就好，比整串 Firestore 文件 ID 好打很多；建立文件前
    # ref.id 就已經產生，一次 set() 直接存進去，不用事後再補一次寫入。
    # 不同所各自的 short_code 落在同一個全域文件 ID 亂數空間，理論上不會
    # 跨所撞號，find_posting_by_short_code() 仍會用 site 篩選以防萬一。
    ref.set(
        {
            "site": site,
            "location_name": location_name,
            "start_time": start_time,
            "end_time": end_time,
            "headcount": headcount,
            "required_qualifications": [q for q in (required_qualifications or []) if q in QUALIFICATION_CODES],
            "status": POSTING_STATUS_OPEN,
            "short_code": ref.id[-6:].upper(),
            "created_by": created_by,
            "created_at": now,
        }
    )
    return ref.id


def get_posting(site: str, posting_id: str):
    snapshot = postings_ref().document(posting_id).get()
    if not snapshot.exists:
        return None
    posting = _to_posting(snapshot.id, snapshot.to_dict() or {})
    if posting["site"] != site:
        return None
    return posting


def find_posting_by_short_code(site: str, short_code: str):
    short_code = (short_code or "").strip().upper()
    if not short_code:
        return None
    query = postings_ref().where("site", "==", site).where("short_code", "==", short_code).limit(1)
    for snapshot in query.stream():
        return _to_posting(snapshot.id, snapshot.to_dict() or {})
    return None


def list_postings(site: str, location_name: str = "", date_str: str = "") -> list:
    """管理後台清單，附上每筆的報名/待審人數——跟配送部
    `list_shift_postings()` 一樣直接整包 `stream()` 下來在 Python 這邊
    篩選，這個規模不需要另外建立 Firestore 複合索引。"""
    items = [_to_posting(s.id, s.to_dict() or {}) for s in postings_ref().where("site", "==", site).stream()]
    if location_name:
        items = [p for p in items if p["location_name"] == location_name]
    if date_str:
        items = [
            p
            for p in items
            if p["start_time"]
            and datetime.fromtimestamp(p["start_time"], TAIPEI_TZ).strftime("%Y-%m-%d") == date_str
        ]
    for p in items:
        p["registered_count"] = count_registrations(site, p["id"])
        p["pending_count"] = count_registrations(site, p["id"], REGISTRATION_STATUS_PENDING)
    items.sort(key=lambda p: p["start_time"], reverse=True)
    return items


def set_posting_status(site: str, posting_id: str, status: str) -> bool:
    if status not in (POSTING_STATUS_OPEN, POSTING_STATUS_CLOSED):
        return False
    if not get_posting(site, posting_id):
        return False
    postings_ref().document(posting_id).update({"status": status})
    return True


def list_open_postings_for_personnel(site: str, personnel_id: str, limit: int = 10) -> list:
    """人員在 LINE 上查詢需求列表：只看得到自己「人員資格」符合其中一項
    的開放中需求，而且還沒報名過的（已經報名過的改用「我的報名」查）。"""
    personnel = get_personnel(site, personnel_id)
    if not personnel:
        return []
    worker_quals = set(personnel.get("qualifications") or [])
    already_registered = {
        r["posting_id"] for r in list_registrations_by_personnel(site, personnel_id, limit=None)
    }
    items = []
    for snapshot in postings_ref().where("site", "==", site).where("status", "==", POSTING_STATUS_OPEN).stream():
        posting = _to_posting(snapshot.id, snapshot.to_dict() or {})
        if posting["id"] in already_registered:
            continue
        required = set(posting["required_qualifications"])
        if required and not (required & worker_quals):
            continue
        items.append(posting)
    items.sort(key=lambda p: p["start_time"])
    return items[:limit] if limit else items


# ---- 報名 ----
def _to_registration(doc_id: str, data: dict) -> dict:
    return {
        "id": doc_id,
        "site": data.get("site", "") or "",
        "posting_id": data.get("posting_id", "") or "",
        "personnel_id": data.get("personnel_id", "") or "",
        "personnel_name": data.get("personnel_name", "") or "",
        "line_user_id": data.get("line_user_id", "") or "",
        "status": data.get("status", REGISTRATION_STATUS_PENDING) or REGISTRATION_STATUS_PENDING,
        "registered_at": data.get("registered_at", 0) or 0,
        "decided_at": data.get("decided_at", 0) or 0,
    }


def count_registrations(site: str, posting_id: str, status: str = None) -> int:
    count = 0
    for snapshot in registrations_ref().where("site", "==", site).where("posting_id", "==", posting_id).stream():
        data = snapshot.to_dict() or {}
        if status and data.get("status") != status:
            continue
        count += 1
    return count


def list_registrations(site: str, posting_id: str) -> list:
    items = [
        _to_registration(s.id, s.to_dict() or {})
        for s in registrations_ref().where("site", "==", site).where("posting_id", "==", posting_id).stream()
    ]
    items.sort(key=lambda r: r["registered_at"])
    return items


def list_registrations_by_personnel(site: str, personnel_id: str, limit: int = 10) -> list:
    items = [
        _to_registration(s.id, s.to_dict() or {})
        for s in registrations_ref().where("site", "==", site).where("personnel_id", "==", personnel_id).stream()
    ]
    items.sort(key=lambda r: r["registered_at"], reverse=True)
    return items[:limit] if limit else items


def _evaluate_registration(posting_data: dict, existing_personnel_ids: list, personnel_id: str):
    """純函式，方便寫單元測試（不碰 Firestore）。回傳 (是否成功, 要回覆
    的文字)——跟配送部 `rider_repository._evaluate_registration()` 同一套
    「先收單、人工審核」邏輯：報名當下不檢查名額是否已滿，滿不滿額由
    管理員審核時自行判斷（人數只是給管理員參考用）。"""
    if posting_data.get("status") != POSTING_STATUS_OPEN:
        return False, "這筆需求已經關閉，無法報名。"
    if personnel_id in existing_personnel_ids:
        return False, "您已經報名過這筆需求了，可以傳「我的報名」查詢目前結果。"
    return True, "已收到您的報名！需等管理人員確認後才算報名成功，可以傳「我的報名」查詢目前結果。"


def register_for_posting(site: str, posting_id: str, personnel_id: str, personnel_name: str, line_user_id: str):
    """回傳 (是否成功, 要回覆的文字)。用 Firestore transaction 包住「查
    目前報名清單 + 寫入新報名」，避免同一人在極短時間內連續按兩次報名
    造成重複報名——寫法跟 `delivery/rider_repository.py` 的
    `register_shift()` 完全對應。"""
    posting_ref = postings_ref().document(posting_id)
    registration_ref = registrations_ref().document()
    transaction = get_db().transaction()

    @firestore.transactional
    def _txn(transaction):
        snapshot = posting_ref.get(transaction=transaction)
        if not snapshot.exists or (snapshot.to_dict() or {}).get("site") != site:
            return False, "找不到這筆需求，可能已經被下架。"
        posting_data = snapshot.to_dict() or {}
        existing_query = registrations_ref().where("site", "==", site).where("posting_id", "==", posting_id)
        existing_docs = list(transaction.get(existing_query))
        existing_personnel_ids = [(doc.to_dict() or {}).get("personnel_id") for doc in existing_docs]
        ok, message = _evaluate_registration(posting_data, existing_personnel_ids, personnel_id)
        if not ok:
            return False, message
        transaction.set(
            registration_ref,
            {
                "site": site,
                "posting_id": posting_id,
                "personnel_id": personnel_id,
                "personnel_name": personnel_name,
                "line_user_id": line_user_id,
                "registered_at": time.time(),
                "status": REGISTRATION_STATUS_PENDING,
            },
        )
        return True, message

    return _txn(transaction)


def get_registration(site: str, registration_id: str):
    snapshot = registrations_ref().document(registration_id).get()
    if not snapshot.exists:
        return None
    registration = _to_registration(snapshot.id, snapshot.to_dict() or {})
    if registration["site"] != site:
        return None
    return registration


def update_registration_status(site: str, registration_id: str, status: str) -> bool:
    if status not in (REGISTRATION_STATUS_PENDING, REGISTRATION_STATUS_APPROVED, REGISTRATION_STATUS_REJECTED):
        return False
    if not get_registration(site, registration_id):
        return False
    registrations_ref().document(registration_id).update({"status": status, "decided_at": time.time()})
    return True
