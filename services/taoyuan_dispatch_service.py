"""桃園所派遣媒合（/taoyuan-dispatch，2026-09-21 新增）：桃園所有自己獨立
的派遣業務跟官方帳號，人員是派遣人員（不是配送部的外送騎士），業務性質
不一樣，所以獨立開一個新模組，不掛在 delivery/ 底下、也不共用 delivery
既有的人員/騎士資料。

**權限模型**：這個模組不掛進 `platform_accounts.MODULES`（不是 `/accounts`
那種每個帳號勾選開放的模組權限），改成照「部門」卡權限——帳號的
`department` 是「桃園所」、或是全平台管理員，才看得到卡片、進得去頁面。
跟 `/contract-summary`（`contract_summary_routes.py` 的
`viewer_has_any_department_access()`）是同一種做法，理由也一樣：這個功能
天生就是「桃園所自己的業務」，不需要另外開一個模組權限讓其他部門的人
也能勾選開放。

**第一階段（2026-09-21）**：先做人員管理（含「人員資格」複選，之後開
需求時段會用這個欄位篩選誰看得到）、地點管理，都支援手動新增跟 CSV
批次匯入。**LINE 官方帳號綁定、需求時段開單、人員報名、主管審核、核准/
駁回推播通知，都還沒做**，是下一階段的 PR（這次官方帳號會直接在 Cloud
Run／Python 這邊處理，比照招募主帳號 `main.py` 的 `/callback` 做法，不
透過 delivery 模組那套 GAS 轉發機制——因為桃園所是全新帳號，沒有沿用
GAS 既有機制的包袱，直接在 Python 這邊處理，主動推播訊息也更單純）。
"""
import csv
import io
import time

from platform_db import get_db

PERSONNEL_COLLECTION = "taoyuan_dispatch_personnel"
LOCATIONS_COLLECTION = "taoyuan_dispatch_locations"

# 人員資格（2026-09-21 新增）：複選，之後開需求時段時勾選「這筆需求要
# 哪些資格才看得到」，人員要至少符合其中一項才看得到、能報名這筆需求
# （下一階段實作）。清單本身先寫死在這裡，不像合作方式管理那樣做成
# 動態清單——使用者目前只提出這 4 種固定類別，之後真的需要再開放自訂。
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

ALLOWED_DEPARTMENTS = {"桃園所"}


def has_taoyuan_access(account: dict) -> bool:
    """全平台管理員，或帳號部門是桃園所，才看得到卡片／進得去頁面。跟
    `/contract-summary` 的部門權限判斷是同一種做法（見本檔案開頭說明）。"""
    if not account:
        return False
    if account.get("is_platform_admin"):
        return True
    return account.get("department") in ALLOWED_DEPARTMENTS


# ==========================================
# 人員管理
# ==========================================
def personnel_ref():
    return get_db().collection(PERSONNEL_COLLECTION)


def _to_personnel(doc_id: str, data: dict) -> dict:
    return {
        "id": doc_id,
        "name": data.get("name", "") or "",
        "phone": data.get("phone", "") or "",
        "qualifications": data.get("qualifications") or [],
        "active": data.get("active", True),
        "created_at": data.get("created_at", 0) or 0,
    }


def create_personnel(name: str, phone: str, qualifications: list, created_by: str = "") -> str:
    now = time.time()
    doc_ref = personnel_ref().document()
    doc_ref.set(
        {
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


def list_personnel(include_inactive: bool = True) -> list:
    result = [_to_personnel(s.id, s.to_dict() or {}) for s in personnel_ref().stream()]
    if not include_inactive:
        result = [p for p in result if p["active"]]
    result.sort(key=lambda p: p["name"])
    return result


def get_personnel(personnel_id: str):
    snapshot = personnel_ref().document(personnel_id).get()
    if not snapshot.exists:
        return None
    return _to_personnel(snapshot.id, snapshot.to_dict() or {})


def update_personnel_qualifications(personnel_id: str, qualifications: list) -> bool:
    ref = personnel_ref().document(personnel_id)
    if not ref.get().exists:
        return False
    ref.update(
        {
            "qualifications": [q for q in (qualifications or []) if q in QUALIFICATION_CODES],
            "updated_at": time.time(),
        }
    )
    return True


def update_personnel_info(personnel_id: str, name: str, phone: str) -> bool:
    ref = personnel_ref().document(personnel_id)
    if not ref.get().exists:
        return False
    ref.update({"name": name, "phone": phone, "updated_at": time.time()})
    return True


def set_personnel_active(personnel_id: str, active: bool) -> bool:
    ref = personnel_ref().document(personnel_id)
    if not ref.get().exists:
        return False
    ref.update({"active": active, "updated_at": time.time()})
    return True


def find_personnel_by_name_and_phone(name: str, phone: str):
    """CSV 批次匯入用：同一個「姓名+電話」已經有資料時回傳該筆，讓匯入
    跳過重複，不會重複建立——跟配送部人員批次匯入的重複判斷同一種做法。
    兩個欄位都要有值才會查。"""
    name = (name or "").strip()
    phone = (phone or "").strip()
    if not name or not phone:
        return None
    query = personnel_ref().where("name", "==", name).where("phone", "==", phone).limit(1)
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
        "name": data.get("name", "") or "",
        "lat": data.get("lat"),
        "lng": data.get("lng"),
        "active": data.get("active", True),
    }


def create_location(name: str, lat: float, lng: float, created_by: str = "") -> str:
    now = time.time()
    doc_ref = locations_ref().document()
    doc_ref.set(
        {"name": name, "lat": lat, "lng": lng, "active": True, "created_by": created_by, "created_at": now}
    )
    return doc_ref.id


def list_locations(include_inactive: bool = True) -> list:
    result = [_to_location(s.id, s.to_dict() or {}) for s in locations_ref().stream()]
    if not include_inactive:
        result = [loc for loc in result if loc["active"]]
    result.sort(key=lambda loc: loc["name"])
    return result


def get_location(location_id: str):
    snapshot = locations_ref().document(location_id).get()
    if not snapshot.exists:
        return None
    return _to_location(snapshot.id, snapshot.to_dict() or {})


def set_location_active(location_id: str, active: bool) -> bool:
    ref = locations_ref().document(location_id)
    if not ref.get().exists:
        return False
    ref.update({"active": active})
    return True


def find_location_by_name(name: str):
    name = (name or "").strip()
    if not name:
        return None
    for loc in list_locations(include_inactive=False):
        if loc["name"] == name:
            return loc
    return None


# ==========================================
# CSV 批次匯入（純函式，不碰 Firestore，方便寫單元測試；是否真的寫入
# 交給呼叫端 taoyuan_dispatch_routes.py 決定）
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
