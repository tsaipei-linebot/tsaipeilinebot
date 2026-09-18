"""人員 / 補款 / 病假登記的資料存取與「缺件狀況」計算邏輯。

缺件判斷刻意寫成不依賴 Firestore 的純函式（missing_documents / doc_status），
方便直接寫單元測試，不需要真的連線 GCP。
"""
import re
import time
from datetime import date, datetime, timedelta

from delivery.config import (
    ANNUAL_LEAVE_MAX_DAYS,
    DEFAULT_INCIDENT_STATUS,
    DEFAULT_PERSONNEL_STATUS,
    DEFAULT_TEST_DRIVE_STATUS,
    DEFAULT_VEHICLE_STATUS,
    DEFAULT_WHEEL_TYPE,
    DOC_TYPES,
    EQUIPMENT_ADMIN_ONLY_TRANSACTION_TYPES,
    EQUIPMENT_TRANSACTION_TYPE_MAP,
    EQUIPMENT_TRANSACTION_TYPES_REQUIRING_PERSONNEL,
    EQUIPMENT_TRANSACTION_TYPES_REQUIRING_TWO_LOCATIONS,
    HIDDEN_PERSONNEL_STATUSES,
    LEAVE_QUOTA_ALERT_RATIO,
    LEAVE_TYPE_LOOKUP,
    LEAVE_TYPES,
    LEGACY_PERSONNEL_STATUS,
    RISK_LEVELS,
    SELECTABLE_APPLICANT_STATUSES,
    WORKDAY_HOURS,
    TEST_DRIVE_REQUIRED_SHOPEE_COOPERATION_TYPES,
    TEST_DRIVE_REQUIRED_VENDORS,
    TEST_DRIVE_STATUS_MAP,
    VEHICLE_STATUS_MAP,
    VENDOR_MAP,
    WHEEL_TYPE_MAP,
)
from delivery.db import (
    announcements_ref,
    applicants_ref,
    cooperation_types_ref,
    equipment_debt_ref,
    equipment_items_ref,
    equipment_locations_ref,
    equipment_stock_ref,
    equipment_transactions_ref,
    get_db,
    incident_events_ref,
    personnel_ref,
    repayments_ref,
    sick_leaves_ref,
    vehicle_events_ref,
    vehicle_service_areas_ref,
    vehicles_ref,
)
from delivery.validators import is_valid_taiwan_id

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

TODAY_ISO = lambda: date.today().isoformat()  # noqa: E731


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def applicable_doc_types(vendor: str, cooperation_type: str, client: str = "") -> list:
    """依廠商 + 合作方式 + 負責客戶，篩出這個人實際需要檢查的應備項目清單。
    exclude_vendors 命中就整個排除；include_vendors 存在但對不上就排除（白名單，
    給只有特定廠商才有的項目用，例如 UD 專屬的 UBER系統/MOMO測驗/自拍照）；
    cooperation_types、clients 同理——存在但對不上（含這個維度根本還沒設定的
    情況）也排除，所以沒設合作方式/負責客戶的人，對應的項目不會出現在缺件清單裡，
    等設定好才開始追蹤。"""
    result = []
    for doc_type in DOC_TYPES:
        if vendor in (doc_type.get("exclude_vendors") or []):
            continue
        include_vendors = doc_type.get("include_vendors")
        if include_vendors is not None and vendor not in include_vendors:
            continue
        required_coop = doc_type.get("cooperation_types")
        if required_coop is not None and cooperation_type not in required_coop:
            continue
        required_clients = doc_type.get("clients")
        if required_clients is not None and client not in required_clients:
            continue
        result.append(doc_type)
    return result


def doc_status(doc_type: dict, personnel: dict) -> dict:
    """回傳單一應備項目的狀態。personnel 要是完整的人員資料（不只是 documents
    子物件），因為「身分證」這一項改成直接檢查 id_number 欄位格式合不合法，
    不是看有沒有上傳檔案。"""
    code = doc_type["code"]
    kind = doc_type["kind"]
    documents = personnel.get("documents") or {}
    entry = documents.get(code) or {}

    if kind == "id_number":
        id_number = (personnel.get("id_number") or "").strip()
        return {
            "code": code,
            "name": doc_type["name"],
            "kind": kind,
            "value": id_number,
            "missing": not is_valid_taiwan_id(id_number),
        }

    if kind == "email":
        email = (personnel.get("email") or "").strip()
        return {
            "code": code,
            "name": doc_type["name"],
            "kind": kind,
            "value": email,
            "missing": not bool(_EMAIL_PATTERN.match(email)),
        }

    if kind == "checkbox":
        checked = bool(entry.get("checked"))
        return {
            "code": code,
            "name": doc_type["name"],
            "kind": kind,
            "checked": checked,
            "missing": not checked,
        }

    if kind == "file":
        has_file = bool(entry.get("file_path"))
        return {
            "code": code,
            "name": doc_type["name"],
            "kind": kind,
            "has_file": has_file,
            "missing": not has_file,
            "file_path": entry.get("file_path") or "",
        }

    # kind == "file_expiry"
    has_file = bool(entry.get("file_path"))
    expired = False
    expiry = _parse_date(entry.get("expiry_date"))
    if expiry is not None and expiry < date.today():
        expired = True
    required = doc_type.get("required", True)
    return {
        "code": code,
        "name": doc_type["name"],
        "kind": kind,
        "has_file": has_file,
        "expiry_date": entry.get("expiry_date") or "",
        "expired": expired,
        "required": required,
        # 非必填的項目沒交不算缺件，但只要交了、過期了一樣算缺件要處理。
        "missing": expired or (required and not has_file),
        "file_path": entry.get("file_path") or "",
    }


def missing_documents(personnel: dict) -> list:
    """回傳缺件（依廠商+合作方式+負責客戶篩選過的應備項目裡，沒填/沒勾/沒上傳
    或已過期的）清單，供列表頁的「缺件狀況」顯示。"""
    doc_types = applicable_doc_types(personnel.get("vendor"), personnel.get("cooperation_type"), personnel.get("client"))
    statuses = [doc_status(dt, personnel) for dt in doc_types]
    return [s for s in statuses if s["missing"]]


def all_document_statuses(personnel: dict) -> list:
    doc_types = applicable_doc_types(personnel.get("vendor"), personnel.get("cooperation_type"), personnel.get("client"))
    return [doc_status(dt, personnel) for dt in doc_types]


# ==========================================
# 合作方式管理（2026-09-18 新增）
#
# 原本合作方式（二輪承攬/二輪雇傭/三輪雇傭）是 config.py 寫死的固定清單，
# 只給蝦皮三輪/蝦皮三輪速配倉這兩個廠商用。使用者要求其他廠商（UD/UC/
# 順豐...）也要能有自己的合作方式選項，而且要能自行新增/停用，不用再找
# 人改代碼——改成存 Firestore 的動態清單，比照裝備品項/車輛服務區域同一套
# 「停用不刪除，除非完全沒人在用」設計。
#
# 跟裝備品項/車輛服務區域不同的地方：**一個合作方式選項可以同時適用多個
# 廠商**（`vendors` 是一個廠商代碼的陣列，不是單一廠商）——這是刻意的，
# 因為蝦皮三輪跟蝦皮三輪速配倉目前就是共用同一份合作方式清單，而且下面
# DOC_TYPES 的保險規則判斷是直接比對「合作方式的值」，不是比對「廠商+
# 合作方式」的組合，所以這兩個廠商的人員選了同一個選項時，儲存的值必須
# 是同一個 Firestore 文件 ID，不能是兩個各自獨立、外觀相同但 ID 不同的
# 選項（不然其中一邊的保險判斷會抓不到）。
# ==========================================

def list_cooperation_types(vendor: str = "", include_inactive: bool = False) -> list:
    """回傳合作方式清單。有給 vendor 時只回傳「適用廠商包含這個代碼」的
    選項（用 Firestore 的 array_contains 查詢 `vendors` 欄位）；沒給就是
    全部選項，給合作方式管理頁面的總表使用。"""
    result = []
    query = cooperation_types_ref()
    if vendor:
        query = query.where("vendors", "array_contains", vendor)
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        data.setdefault("active", True)
        data.setdefault("vendors", [])
        if include_inactive or data["active"]:
            result.append(data)
    result.sort(key=lambda c: c.get("name", ""))
    return result


def get_cooperation_type(type_id: str):
    if not type_id:
        return None
    snapshot = cooperation_types_ref().document(type_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    data.setdefault("active", True)
    data.setdefault("vendors", [])
    return data


def create_cooperation_type(name: str, vendors: list, type_id: str = "", created_by: str = "") -> str:
    """新增一個合作方式選項。`type_id` 留空時用 Firestore 自動產生的文件
    ID（主管在網頁上新增走這條路）；有指定時直接用它當文件 ID，只給
    `scripts/seed_cooperation_types.py` 那支一次性遷移腳本使用，讓蝦皮
    三輪/速配倉既有人員存的舊代碼（"two_wheel_contract"…）可以原封不動
    對應到新建的選項，不需要搬移人員資料，DOC_TYPES 的保險規則判斷也
    完全不受影響。"""
    now = time.time()
    doc_ref = cooperation_types_ref().document(type_id) if type_id else cooperation_types_ref().document()
    doc_ref.set(
        {
            "name": name,
            "vendors": vendors or [],
            "active": True,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }
    )
    return doc_ref.id


def update_cooperation_type(type_id: str, name: str, vendors: list) -> bool:
    ref = cooperation_types_ref().document(type_id)
    if not ref.get().exists:
        return False
    ref.update({"name": name, "vendors": vendors or [], "updated_at": time.time()})
    return True


def set_cooperation_type_active(type_id: str, active: bool) -> bool:
    ref = cooperation_types_ref().document(type_id)
    if not ref.get().exists:
        return False
    ref.update({"active": active, "updated_at": time.time()})
    return True


def cooperation_type_has_history(type_id: str) -> bool:
    """判斷有沒有任何人員或應徵者的合作方式指到這個 ID——有的話不能真的
    刪除，只能停用。"""
    personnel_hit = next(personnel_ref().where("cooperation_type", "==", type_id).limit(1).stream(), None)
    if personnel_hit is not None:
        return True
    return next(applicants_ref().where("cooperation_type", "==", type_id).limit(1).stream(), None) is not None


def delete_cooperation_type(type_id: str) -> bool:
    if cooperation_type_has_history(type_id):
        return False
    cooperation_types_ref().document(type_id).delete()
    return True


# ==========================================
# 人員 CRUD
# ==========================================
def create_personnel(
    name: str,
    id_number: str,
    phone: str,
    vendor: str,
    created_by: str,
    cooperation_type: str = "",
    client: str = "",
    employment_status: str = "",
    hire_date: str = "",
) -> str:
    now = time.time()
    doc_ref = personnel_ref().document()
    doc_ref.set(
        {
            "name": name,
            "id_number": id_number,
            "phone": phone,
            "vendor": vendor,
            "cooperation_type": cooperation_type or "",
            "client": client or "",
            "employment_status": employment_status or DEFAULT_PERSONNEL_STATUS,
            "hire_date": hire_date or "",
            "status": "active",
            "documents": {},
            "created_at": now,
            "updated_at": now,
            "created_by": created_by,
        }
    )
    return doc_ref.id


def personnel_employment_status(personnel: dict) -> str:
    """回傳人員的報到/在職狀態代碼。這個功能上線前就存在的舊資料沒有
    employment_status 欄位，當作「在職」，不會被誤判成剛建立、還沒報到。"""
    return personnel.get("employment_status") or LEGACY_PERSONNEL_STATUS


def update_personnel_employment_status(personnel_id: str, employment_status: str):
    personnel_ref().document(personnel_id).update(
        {"employment_status": employment_status, "updated_at": time.time()}
    )


def get_personnel(personnel_id: str):
    snapshot = personnel_ref().document(personnel_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def delete_personnel(personnel_id: str):
    """整筆刪除人員紀錄（2026-09-13 新增，主管專用）——真的從 Firestore
    刪掉，不是標記隱藏，沒有回收機制，跟合約產生器／派遣契約產生器既有的
    刪除功能是同一種做法。呼叫端（routes/vendor_routes.py 的刪除路由）
    要負責在這之前先呼叫 storage.delete_entity_files() 清掉上傳過的檔案，
    這裡只處理 Firestore 那筆文件本身。"""
    personnel_ref().document(personnel_id).delete()


def list_personnel_by_vendor(vendor: str) -> list:
    query = personnel_ref().where("vendor", "==", vendor).where("status", "==", "active")
    result = []
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        result.append(data)
    result.sort(key=lambda p: p.get("name", ""))
    return result


def personnel_matches_filters(
    personnel: dict,
    missing: list,
    name_keyword: str = "",
    phone_keyword: str = "",
    status_filter: str = "",
    missing_filter: str = "",
) -> bool:
    """判斷這個人要不要出現在廠商人員清單裡（純函式，missing 需已經算好傳入）。

    人員狀態：預設（沒有明確篩選狀態）不顯示「離職」「放棄報到」的人，跟應徵
    名單「放棄」預設隱藏一樣；主動搜尋姓名、或直接篩選狀態為這兩項才會顯示。
    缺件狀態：預設（沒有明確篩選、也沒搜尋姓名）不顯示缺件狀況「齊全」的人，
    避免洗版；主動搜尋姓名，或直接篩選「缺件」「無缺件」都可以覆蓋這個預設。
    """
    if name_keyword and name_keyword not in (personnel.get("name") or ""):
        return False
    if phone_keyword and phone_keyword not in (personnel.get("phone") or ""):
        return False

    employment_status = personnel_employment_status(personnel)
    if status_filter:
        if employment_status != status_filter:
            return False
    elif employment_status in HIDDEN_PERSONNEL_STATUSES and not name_keyword:
        return False

    if missing_filter == "missing":
        if not missing:
            return False
    elif missing_filter == "complete":
        if missing:
            return False
    elif not missing and not name_keyword:
        return False

    return True


def search_personnel(keyword: str = "", vendor: str = "", employment_status: str = "") -> list:
    """簡易查詢：抓全部在職人員後在應用程式端比對姓名/身分證字號/廠商/報到
    狀態（人數規模小，不需要為此另外接全文檢索服務）。

    2026-09-13 新增 vendor／employment_status 兩個篩選條件：「人員狀況」
    （/delivery/vendor/{廠商}）預設會隱藏「缺件齊全」跟「離職／放棄報到」
    的人，如果一個廠商底下的人都已經備齊文件，畫面上就會整個空白，沒有
    地方能單純看「這個廠商目前有哪些人」。這裡刻意**不**套用那些預設
    隱藏規則——呼叫端（search_routes.py）就是要讓同仁能看到完整名單，
    包不包含離職/放棄報到的人，交給 employment_status 這個篩選條件決定，
    不是內建的預設行為。三個條件都是「有給值才篩」，同時給多個條件是
    AND 的關係（例如選了廠商又打了關鍵字，就是在那個廠商裡搜姓名）。"""
    keyword = (keyword or "").strip()
    vendor = (vendor or "").strip()
    employment_status = (employment_status or "").strip()
    result = []
    for snapshot in personnel_ref().where("status", "==", "active").stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        if keyword and keyword not in data.get("name", "") and keyword not in data.get("id_number", ""):
            continue
        if vendor and data.get("vendor") != vendor:
            continue
        if employment_status and personnel_employment_status(data) != employment_status:
            continue
        result.append(data)
    result.sort(key=lambda p: p.get("name", ""))
    return result


def find_active_personnel_by_name_and_phone(name: str, phone: str):
    """批次匯入用：同一個「姓名+手機號碼」組合已經有在職人員資料時回傳該筆，
    讓呼叫端可以跳過重複匯入，而不是每次匯入都建出重複的人員記錄。
    兩個欄位都要有值才會查（單靠姓名或單靠電話都不足以判定是同一人）。"""
    if not name or not phone:
        return None
    query = (
        personnel_ref()
        .where("name", "==", name)
        .where("phone", "==", phone)
        .where("status", "==", "active")
        .limit(1)
    )
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        return data
    return None


def update_personnel_document(personnel_id: str, doc_type_code: str, file_path: str = None, expiry_date: str = None):
    """用於 kind="file_expiry" 的項目（強制險/公會加保證明/營業用第三責任險/
    良民證）。expiry_date 有變動時順便清掉 last_reminded_at，讓到期提醒的
    「最近提醒過」判斷用新的到期日重新算，不會因為舊到期日剛提醒過就把新到期日
    的提醒也跳過。"""
    ref = personnel_ref().document(personnel_id)
    snapshot = ref.get()
    if not snapshot.exists:
        return
    data = snapshot.to_dict() or {}
    documents = data.get("documents") or {}
    entry = dict(documents.get(doc_type_code) or {})
    if file_path is not None:
        entry["file_path"] = file_path
    if expiry_date is not None:
        entry["expiry_date"] = expiry_date
        entry.pop("last_reminded_at", None)
    documents[doc_type_code] = entry
    ref.update({"documents": documents, "updated_at": time.time()})


def update_personnel_checkbox(personnel_id: str, doc_type_code: str, checked: bool):
    """用於 kind="checkbox" 的項目（駕照、合約簽定）。"""
    ref = personnel_ref().document(personnel_id)
    snapshot = ref.get()
    if not snapshot.exists:
        return
    data = snapshot.to_dict() or {}
    documents = data.get("documents") or {}
    entry = dict(documents.get(doc_type_code) or {})
    entry["checked"] = checked
    documents[doc_type_code] = entry
    ref.update({"documents": documents, "updated_at": time.time()})


def update_personnel_id_number(personnel_id: str, id_number: str):
    """用於 kind="id_number" 的項目（身分證）。格式驗證交給呼叫端
    （validators.is_valid_taiwan_id）先擋一次，這裡單純負責寫入。"""
    personnel_ref().document(personnel_id).update({"id_number": id_number, "updated_at": time.time()})


def update_personnel_email(personnel_id: str, email: str):
    """用於 kind="email" 的項目。格式檢查交給呼叫端／doc_status，這裡單純負責寫入。"""
    personnel_ref().document(personnel_id).update({"email": email, "updated_at": time.time()})


def update_personnel_cooperation_type(personnel_id: str, cooperation_type: str):
    personnel_ref().document(personnel_id).update({"cooperation_type": cooperation_type, "updated_at": time.time()})


def update_personnel_vendor(personnel_id: str, vendor: str):
    """修改人員所屬廠商（2026-09-13 新增）——人員建立後原本沒有地方可以
    再改廠商，蝦皮廠商拆分成 4 個代碼之後，既有蝦皮人員要靠這個功能手動
    重新分類到正確的新代碼。改了廠商不會連動清掉 cooperation_type／client／
    documents 這些欄位——新廠商用不到的欄位就只是不會顯示在畫面上，跟
    改變 cooperation_type 後其他廠商專屬欄位一樣維持原值不特別清除。"""
    personnel_ref().document(personnel_id).update({"vendor": vendor, "updated_at": time.time()})


def update_personnel_client(personnel_id: str, client: str):
    personnel_ref().document(personnel_id).update({"client": client, "updated_at": time.time()})


def list_expiring_documents(days_ahead: int, resend_interval_days: int) -> list:
    """掃過全部在職人員，回傳需要發到期提醒的 (人員, 文件) 配對：到期日在
    「今天~今天+days_ahead 天」之間、或已經過期，而且沒有在最近
    resend_interval_days 天內提醒過。只掃 kind="file_expiry" 的項目（強制險/
    公會加保證明/營業用第三責任險/良民證），身分證、駕照、合約簽定沒有到期日
    不適用。"""
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)
    result = []
    for snapshot in personnel_ref().where("status", "==", "active").stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        documents = data.get("documents") or {}
        doc_types = applicable_doc_types(data.get("vendor"), data.get("cooperation_type"), data.get("client"))
        for doc_type in doc_types:
            if doc_type["kind"] != "file_expiry":
                continue
            entry = documents.get(doc_type["code"]) or {}
            expiry = _parse_date(entry.get("expiry_date"))
            if expiry is None or expiry > cutoff:
                continue
            last_reminded = _parse_date(entry.get("last_reminded_at"))
            if last_reminded is not None and (today - last_reminded).days < resend_interval_days:
                continue
            result.append(
                {
                    "personnel_id": data["id"],
                    "personnel_name": data.get("name"),
                    "vendor": data.get("vendor"),
                    "doc_code": doc_type["code"],
                    "doc_name": doc_type["name"],
                    "expiry_date": entry.get("expiry_date"),
                    "expired": expiry < today,
                }
            )
    return result


def mark_documents_reminded(items: list):
    """items 是 list_expiring_documents() 回傳的那種 dict，LINE 推播成功後呼叫，
    記錄提醒時間，避免同一份文件短時間內被重複提醒。"""
    today_iso = date.today().isoformat()
    by_personnel = {}
    for item in items:
        by_personnel.setdefault(item["personnel_id"], []).append(item["doc_code"])

    batch = get_db().batch()
    for personnel_id, doc_codes in by_personnel.items():
        snapshot = personnel_ref().document(personnel_id).get()
        if not snapshot.exists:
            continue
        data = snapshot.to_dict() or {}
        documents = data.get("documents") or {}
        for doc_code in doc_codes:
            entry = dict(documents.get(doc_code) or {})
            entry["last_reminded_at"] = today_iso
            documents[doc_code] = entry
        batch.update(personnel_ref().document(personnel_id), {"documents": documents})
    batch.commit()


# ==========================================
# 補款登記
# ==========================================
def create_repayment(personnel_id: str, personnel_name: str, vendor: str, amount: float, reason: str, occurred_date: str, created_by: str) -> str:
    doc_ref = repayments_ref().document()
    doc_ref.set(
        {
            "personnel_id": personnel_id,
            "personnel_name": personnel_name,
            "vendor": vendor,
            "amount": amount,
            "reason": reason,
            "occurred_date": occurred_date,
            "approved": False,
            "created_by": created_by,
            "created_at": time.time(),
        }
    )
    return doc_ref.id


def repayment_matches_filters(record: dict, name_keyword: str = "", vendor_filter: str = "", month_filter: str = "") -> bool:
    """判斷這筆補款登記要不要出現在「補款記錄」清單裡（純函式）。month_filter
    是 "YYYY-MM" 格式（對應 <input type="month">），比對 occurred_date 開頭。"""
    if name_keyword and name_keyword not in (record.get("personnel_name") or ""):
        return False
    if vendor_filter and record.get("vendor") != vendor_filter:
        return False
    if month_filter and not (record.get("occurred_date") or "").startswith(month_filter):
        return False
    return True


def list_repayments(name_keyword: str = "", vendor_filter: str = "", month_filter: str = "") -> list:
    name_keyword = (name_keyword or "").strip()
    vendor_filter = (vendor_filter or "").strip()
    month_filter = (month_filter or "").strip()

    result = []
    for snapshot in repayments_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        data["approved"] = bool(data.get("approved"))
        if repayment_matches_filters(data, name_keyword, vendor_filter, month_filter):
            result.append(data)
    result.sort(key=lambda r: r.get("occurred_date", ""), reverse=True)
    return result


def bulk_approve_repayments(repayment_ids: list) -> None:
    """把指定的補款登記標記為已核准。核准是單向的——這裡只會把 approved 設成
    True，沒有讓它變回 False 的路徑；已經核准過的重複送出沒有副作用。"""
    if not repayment_ids:
        return
    batch = get_db().batch()
    for repayment_id in repayment_ids:
        batch.update(repayments_ref().document(repayment_id), {"approved": True})
    batch.commit()


def get_repayment(repayment_id: str):
    snapshot = repayments_ref().document(repayment_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    data["approved"] = bool(data.get("approved"))
    return data


def update_repayment(
    repayment_id: str, vendor: str, personnel_name: str, amount: float, reason: str, occurred_date: str
) -> bool:
    """修正一筆既有的補款登記（例如金額、日期打錯字）。2026-09-15 新增，
    比照意外事件編輯的做法：只改登記內容本身，不動 approved／created_by／
    created_at 這幾個欄位——核准狀態有自己的操作入口（見
    bulk_approve_repayments），不該被這裡的編輯表單意外洗掉；已經核准的
    登記一樣可以修正內容，核准狀態不受影響（核准本身仍然是單向操作，
    沒有取消核准的路徑）。登記不存在回傳 False、不會寫入。"""
    ref = repayments_ref().document(repayment_id)
    if not ref.get().exists:
        return False
    ref.update(
        {
            "vendor": vendor,
            "personnel_name": personnel_name,
            "amount": amount,
            "reason": reason,
            "occurred_date": occurred_date,
        }
    )
    return True


def delete_repayment(repayment_id: str) -> bool:
    """刪除一筆補款登記，只限管理員操作（路由層擋，這裡不重複判斷角色）。
    **已核准的登記不能刪除**——核准代表這筆金額可能已經對過帳、算進薪資
    發放，刪掉會讓帳對不起來；如果是核准錯了，應該先確認清楚再處理，
    不是直接刪除證據。回傳 False 代表沒有刪除成功（登記不存在，或已經
    核准）。"""
    record = get_repayment(repayment_id)
    if not record or record.get("approved"):
        return False
    repayments_ref().document(repayment_id).delete()
    return True


# ==========================================
# 假別登記
# 2026-09-11 起改成「一天一筆、記時數」（leave_date + hours），取代原本
# 「起訖日期、沒有時數」的 start_date/end_date。**這個功能上線前既有的
# 舊資料只有 start_date/end_date，沒有 leave_date/hours**——這裡刻意不去
# 改寫/搬遷舊資料（無法回推當初到底請了幾小時），畫面顯示、篩選都對兩種
# 格式做相容處理，但額度累積計算只會採計「有 leave_date/hours 的新格式
# 紀錄」，這是新功能上線後才開始準確累計的已知限制，見 HANDOFF.md。
# ==========================================
def create_sick_leave(
    personnel_id: str,
    personnel_name: str,
    vendor: str,
    leave_date: str,
    hours: float,
    reason: str,
    receipt_file_path: str,
    created_by: str,
    leave_type: str = "",
) -> str:
    doc_ref = sick_leaves_ref().document()
    doc_ref.set(
        {
            "personnel_id": personnel_id,
            "personnel_name": personnel_name,
            "vendor": vendor,
            "leave_type": leave_type or "",
            "leave_date": leave_date,
            "hours": hours,
            "reason": reason,
            "receipt_file_path": receipt_file_path,
            "approved": False,
            "created_by": created_by,
            "created_at": time.time(),
        }
    )
    return doc_ref.id


def get_sick_leave(sick_leave_id: str):
    snapshot = sick_leaves_ref().document(sick_leave_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def update_sick_leave(sick_leave_id: str, data: dict) -> bool:
    """管理員在假別查詢頁修正既有登記內容（例如假別選錯、時數打錯）。
    只更新登記本身的欄位，`approved`／`created_by`／`created_at` 不受
    影響——核准狀態有自己的操作入口（見 bulk_approve_sick_leaves），不該
    被這裡的編輯表單意外洗掉。紀錄不存在回傳 False、不會寫入。"""
    ref = sick_leaves_ref().document(sick_leave_id)
    if not ref.get().exists:
        return False
    payload = {
        "personnel_name": data.get("personnel_name", ""),
        "vendor": data.get("vendor", ""),
        "leave_type": data.get("leave_type", ""),
        "leave_date": data.get("leave_date", ""),
        "hours": data.get("hours", 0),
        "reason": data.get("reason", ""),
    }
    ref.update(payload)
    return True


def delete_sick_leave(sick_leave_id: str) -> bool:
    """刪除一筆假別登記，只限管理員操作。比照 delete_repayment()：**已核准
    的登記不能刪除**——已核准的假別已經算進年度額度累積（見
    leave_quota_summary_for_person()），刪掉會讓額度試算跟實際請假天數
    對不起來。回傳 False 代表沒有刪除成功（登記不存在，或已經核准）。"""
    record = get_sick_leave(sick_leave_id)
    if not record or record.get("approved"):
        return False
    sick_leaves_ref().document(sick_leave_id).delete()
    return True


def sick_leave_record_date(record: dict) -> str:
    """取這筆紀錄「用來篩選/排序/顯示」的日期字串：新格式用 leave_date，
    上線前的舊格式（沒有 leave_date）退回用 start_date。"""
    return record.get("leave_date") or record.get("start_date") or ""


def sick_leave_matches_filters(
    record: dict,
    name_keyword: str = "",
    vendor_filter: str = "",
    month_filter: str = "",
    leave_type_filter: str = "",
) -> bool:
    """判斷這筆假別登記要不要出現在「假別查詢」清單裡（純函式）。month_filter
    是 "YYYY-MM" 格式，比對這筆紀錄的日期（見 sick_leave_record_date()）開頭。"""
    if name_keyword and name_keyword not in (record.get("personnel_name") or ""):
        return False
    if vendor_filter and record.get("vendor") != vendor_filter:
        return False
    if month_filter and not sick_leave_record_date(record).startswith(month_filter):
        return False
    if leave_type_filter and record.get("leave_type") != leave_type_filter:
        return False
    return True


def list_sick_leaves(
    name_keyword: str = "", vendor_filter: str = "", month_filter: str = "", leave_type_filter: str = ""
) -> list:
    name_keyword = (name_keyword or "").strip()
    vendor_filter = (vendor_filter or "").strip()
    month_filter = (month_filter or "").strip()
    leave_type_filter = (leave_type_filter or "").strip()

    result = []
    for snapshot in sick_leaves_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        data["approved"] = bool(data.get("approved"))
        if sick_leave_matches_filters(data, name_keyword, vendor_filter, month_filter, leave_type_filter):
            result.append(data)
    result.sort(key=sick_leave_record_date, reverse=True)
    return result


def bulk_approve_sick_leaves(sick_leave_ids: list) -> None:
    """把指定的假別登記標記為已核准，一樣是單向的（見 bulk_approve_repayments）。"""
    if not sick_leave_ids:
        return
    batch = get_db().batch()
    for sick_leave_id in sick_leave_ids:
        batch.update(sick_leaves_ref().document(sick_leave_id), {"approved": True})
    batch.commit()


# ==========================================
# 假別額度計算（2026-09-11 新增）
# 特休依「到職週年」計算，其餘假別依「曆年」計算，見 config.py 的
# LEAVE_TYPES 說明。純函式部分（不連 Firestore）方便直接寫單元測試。
# ==========================================
def compute_annual_leave_days(years_of_service: float) -> int:
    """依勞基法第38條的年資級距，回傳特休天數。years_of_service 是到職
    當下累積的年資（例如 1.5 代表滿1年半），0.5~1年這個級距需要知道有沒有
    滿半年，所以用浮點數而不是只看整數年。10年以上每滿1年加1天，最高
    30天：第10年（10.0~10.9）比照5~10年級距是15天，滿第11個完整年度
    （years_of_service>=11）開始才+1天，以此類推。"""
    if years_of_service < 0.5:
        return 0
    if years_of_service < 1:
        return 3
    floor_years = int(years_of_service)
    if floor_years < 2:
        return 7
    if floor_years < 3:
        return 10
    if floor_years < 5:
        return 14
    if floor_years < 10:
        return 15
    return min(15 + (floor_years - 9), ANNUAL_LEAVE_MAX_DAYS)


def _add_years(base_date: date, years: int) -> date:
    """base_date 往後加 years 年，處理 2/29 加到非閏年的邊界情況（退到
    2/28，不是進位到 3/1）。"""
    try:
        return base_date.replace(year=base_date.year + years)
    except ValueError:
        return base_date.replace(year=base_date.year + years, day=28)


def years_of_service_at(hire_date: date, on_date: date) -> float:
    """on_date 這一天，距離 hire_date 累積了幾年年資。未滿一年時，用實際
    天數（>=182天）概算「有沒有滿半年」，滿一年以上就回傳整數年（用月/日
    比對，不是用「相減天數 / 365」概算，避免閏年造成的誤差）。"""
    if on_date < hire_date:
        return 0.0
    years = on_date.year - hire_date.year
    if (on_date.month, on_date.day) < (hire_date.month, hire_date.day):
        years -= 1
    if years >= 1:
        return float(years)
    days = (on_date - hire_date).days
    return 0.5 if days >= 182 else 0.0


def leave_period_and_quota(leave_type_code: str, hire_date, as_of: date = None):
    """回傳 (period_start, period_end, quota_days) 這個假別「目前所在的
    額度週期」跟法定上限天數：
    - 特休（quota_basis="anniversary"）：週期是「到職週年」；hire_date
      是 None（同仁還沒補到職日期）時算不出週期，回傳 (None, None, None)。
    - 曆年制的假別：週期固定是當年 1/1~12/31。
    - 沒有固定額度的假別（quota_basis 是 None）：週期一樣給曆年（方便
      畫面上還是能顯示「今年用了幾天」），但 quota_days 是 None，呼叫端
      不應該拿 None 去算百分比。
    """
    as_of = as_of or date.today()
    leave_type = LEAVE_TYPE_LOOKUP.get(leave_type_code) or {}
    basis = leave_type.get("quota_basis")

    if basis == "anniversary":
        if hire_date is None:
            return None, None, None
        years = years_of_service_at(hire_date, as_of)
        start_years = int(years) if years >= 1 else 0
        period_start = _add_years(hire_date, start_years)
        period_end = _add_years(hire_date, start_years + 1) - timedelta(days=1)
        return period_start, period_end, compute_annual_leave_days(years)

    period_start, period_end = date(as_of.year, 1, 1), date(as_of.year, 12, 31)
    return period_start, period_end, leave_type.get("quota_days")


def _quota_pool_codes(leave_type_code: str) -> list:
    """回傳跟這個假別共用同一包額度的所有假別代碼（含自己）——目前只有
    家庭照顧假的用量要「額外」算進事假的額度消耗裡（見 config.py
    LEAVE_TYPES 的 shares_quota_with 說明）。"""
    codes = [leave_type_code]
    for t in LEAVE_TYPES:
        if t.get("shares_quota_with") == leave_type_code:
            codes.append(t["code"])
    return codes


def leave_quota_summary_for_person(records: list, hire_date, as_of: date = None) -> list:
    """算這個人「每一種有固定額度的假別」目前週期內的累積使用狀況。records
    是這個人全部的假別登記紀錄（不限假別、不限期間，這裡自己篩）；沒有
    leave_date/hours 的舊格式紀錄不會被算進累積（見本節開頭的說明）。
    公假、其他、育嬰留職停薪這種沒有固定額度的假別不會出現在回傳清單裡。"""
    as_of = as_of or date.today()
    summary = []
    for leave_type in LEAVE_TYPES:
        code = leave_type["code"]
        if leave_type.get("quota_basis") is None:
            continue
        period_start, period_end, quota_days = leave_period_and_quota(code, hire_date, as_of)
        if period_start is None:
            continue
        pool_codes = _quota_pool_codes(code)
        hours_used = 0.0
        for r in records:
            if r.get("leave_type") not in pool_codes:
                continue
            record_date = _parse_date(r.get("leave_date"))
            if record_date is None or not (period_start <= record_date <= period_end):
                continue
            hours_used += r.get("hours") or 0
        days_used = round(hours_used / WORKDAY_HOURS, 2)
        percent_used = round(days_used / quota_days * 100, 1) if quota_days else None
        summary.append(
            {
                "leave_type": code,
                "leave_type_name": leave_type["name"],
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "hours_used": hours_used,
                "days_used": days_used,
                "quota_days": quota_days,
                "percent_used": percent_used,
            }
        )
    return summary


def find_personnel_by_name_vendor(vendor: str, name: str):
    """假別登記表單只填廠商+姓名（自由文字，沒有連到人員資料的
    personnel_id，見本節開頭說明），算年度額度時要靠這個反查對應的人員
    資料（拿到職日期）。同一廠商同名同姓會抓到「其中一筆」在職人員，這是
    現有系統本來就有的限制（假別登記從一開始就沒有存 personnel_id），
    不是這次新增功能造成的。"""
    query = personnel_ref().where("vendor", "==", vendor).where("name", "==", name).where("status", "==", "active")
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        return data
    return None


def list_leave_quota_alerts(as_of: date = None) -> list:
    """掃過全部在職人員，抓出「有固定額度的假別，累積使用達 90% 以上」的
    (人員, 假別) 配對，給排程端點（routes/reminder_routes.py）推播 LINE
    提醒用。每次呼叫都是重新掃描全部資料，沒有「已提醒過」的排除邏輯——
    使用者要求只要仍然在 90% 以上，每次排程都要提醒，不用像文件到期提醒
    那樣記錄「最近提醒過」。"""
    as_of = as_of or date.today()
    all_records = list_sick_leaves()
    records_by_key = {}
    for r in all_records:
        key = (r.get("vendor"), r.get("personnel_name"))
        records_by_key.setdefault(key, []).append(r)

    alerts = []
    for snapshot in personnel_ref().where("status", "==", "active").stream():
        person = snapshot.to_dict() or {}
        person["id"] = snapshot.id
        if personnel_employment_status(person) != "employed":
            continue
        key = (person.get("vendor"), person.get("name"))
        person_records = records_by_key.get(key, [])
        hire_date = _parse_date(person.get("hire_date"))
        summary = leave_quota_summary_for_person(person_records, hire_date, as_of)
        for row in summary:
            if row["quota_days"] and row["percent_used"] is not None and row["percent_used"] >= LEAVE_QUOTA_ALERT_RATIO * 100:
                alerts.append(
                    {
                        "personnel_name": person.get("name"),
                        "vendor": person.get("vendor"),
                        "leave_type_name": row["leave_type_name"],
                        "days_used": row["days_used"],
                        "quota_days": row["quota_days"],
                        "percent_used": row["percent_used"],
                    }
                )
    return alerts


def update_personnel_hire_date(personnel_id: str, hire_date: str):
    """到職日期只影響特休額度試算，跟其他文件欄位一樣在人員詳細頁的
    「一鍵全部更新」表單裡一起送出（見 routes/vendor_routes.py 的
    bulk_update_personnel()）。"""
    personnel_ref().document(personnel_id).update({"hire_date": hire_date, "updated_at": time.time()})


# ==========================================
# 應徵名單（Google 表單 webhook 寫入，錄取後轉正式人員）
# ==========================================
_SELECTABLE_STATUS_CODES = {s["code"] for s in SELECTABLE_APPLICANT_STATUSES}


def normalize_applicant_status(data: dict) -> str:
    """新資料一律直接存 status 欄位；這裡額外相容改版前只有
    interviewed/hired/withdrawn 三個布林欄位的舊資料，讓舊紀錄不用手動搬移
    也能正確顯示狀態。"""
    status = data.get("status")
    if status:
        return status
    if data.get("hired"):
        return "hired"
    if data.get("withdrawn"):
        return "withdrawn"
    if data.get("interviewed"):
        return "interviewed"
    return "not_interviewed"


def applicant_matches_filters(
    data: dict,
    name_keyword: str = "",
    phone_keyword: str = "",
    status_filter: str = "",
    vendor_filter: str = "",
) -> bool:
    """判斷這筆應徵資料要不要出現在清單裡（純函式，data 需已經算好 status）。

    預設（沒指定狀態篩選、也沒搜尋姓名）不顯示「已錄取」「放棄」的紀錄，
    避免洗版——這兩種狀態都已經走完流程，平常盤點應徵名單時不需要一直
    看到；只要主動搜尋姓名、或直接篩選狀態為「已錄取」／「放棄」，就會
    顯示，方便事後回頭查（2026-09-15 使用者要求把「已錄取」也比照「放棄」
    預設隱藏）。廠商正常顯示，不特別隱藏「未指定廠商」的紀錄。
    """
    if name_keyword and name_keyword not in (data.get("name") or ""):
        return False
    if phone_keyword and phone_keyword not in (data.get("phone") or ""):
        return False
    if vendor_filter and (data.get("vendor") or "") != vendor_filter:
        return False

    status = data.get("status") or normalize_applicant_status(data)
    if status_filter:
        return status == status_filter
    if status in ("withdrawn", "hired") and not name_keyword:
        return False
    return True


def applicant_needs_test_drive(vendor: str, cooperation_type: str) -> bool:
    """判斷這個應徵者需不需要試駕：UD、UC 一律需要；合作方式是「三輪雇傭」
    （目前只有蝦皮／蝦皮三輪速配倉會用到這個合作方式）才需要（二輪承攬/
    二輪雇傭不用）；其他廠商不需要。"""
    if vendor in TEST_DRIVE_REQUIRED_VENDORS:
        return True
    if cooperation_type in TEST_DRIVE_REQUIRED_SHOPEE_COOPERATION_TYPES:
        return True
    return False


def find_applicant_by_name_and_phone(name: str, phone: str):
    """兩者都要有值才會查（單靠姓名或單靠電話都不足以判定是同一人）。"""
    if not name or not phone:
        return None
    query = applicants_ref().where("name", "==", name).where("phone", "==", phone).limit(1)
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        return data
    return None


def upsert_applicant(name: str, phone: str, answers: dict, vendor: str = "", cooperation_type: str = "") -> str:
    """姓名+電話相同視為同一人重複投遞表單：覆蓋既有應徵紀錄的回覆內容（含
    廠商、合作方式），並把處理狀態清空回到「未面試」，不會疊加成新的一筆。
    姓名+電話對不到既有紀錄（含兩者缺一的情況）時直接新增一筆。

    試駕狀態、備註刻意不隨表單重投而重置——那些是同仁自己操作/記錄的結果，
    不是表單填寫的內容，重複投遞表單不該把已經記錄的內容洗掉。"""
    existing = find_applicant_by_name_and_phone(name, phone)
    payload = {
        "name": name,
        "phone": phone,
        "answers": answers or {},
        "vendor": vendor or "",
        "cooperation_type": cooperation_type or "",
        "test_drive": (existing or {}).get("test_drive") or DEFAULT_TEST_DRIVE_STATUS,
        "note": (existing or {}).get("note") or "",
        "status": "not_interviewed",
        "converted_personnel_id": None,
        "created_at": time.time(),
    }
    if existing:
        applicants_ref().document(existing["id"]).set(payload)
        return existing["id"]

    doc_ref = applicants_ref().document()
    doc_ref.set(payload)
    return doc_ref.id


def _normalize_applicant(data: dict) -> dict:
    data["status"] = normalize_applicant_status(data)
    data["vendor"] = data.get("vendor") or ""
    data["cooperation_type"] = data.get("cooperation_type") or ""
    data["test_drive"] = data.get("test_drive") or DEFAULT_TEST_DRIVE_STATUS
    data["note"] = data.get("note") or ""
    return data


def list_applicants(
    name_keyword: str = "", phone_keyword: str = "", status_filter: str = "", vendor_filter: str = ""
) -> list:
    name_keyword = (name_keyword or "").strip()
    phone_keyword = (phone_keyword or "").strip()
    status_filter = (status_filter or "").strip()
    vendor_filter = (vendor_filter or "").strip()

    result = []
    query = applicants_ref().order_by("created_at", direction="DESCENDING")
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        data = _normalize_applicant(data)
        if applicant_matches_filters(data, name_keyword, phone_keyword, status_filter, vendor_filter):
            result.append(data)
    return result


def get_applicant(applicant_id: str):
    snapshot = applicants_ref().document(applicant_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return _normalize_applicant(data)


def bulk_update_applicants(updates: dict) -> None:
    """一次更新多筆應徵紀錄的狀態/廠商/合作方式/試駕/備註，配合前端「一鍵
    全部更新」。每個欄位獨立驗證，只有合法值才會真的寫入；「已錄取」狀態
    一樣不能透過這裡設定，只能透過「錄取並建立人員」那個流程。

    updates 格式：{applicant_id: {"status": ..., "vendor": ..., "cooperation_type": ..., "test_drive": ..., "note": ...}}，
    每個 applicant 底下的欄位都可以缺，缺的就不動。「備註」是同仁自由輸入
    的文字，不像其他欄位有固定選項可以比對，只要有帶這個鍵就整段存入
    （含清空成空字串），不做內容限制。"""
    batch = get_db().batch()
    has_writes = False
    for applicant_id, fields in updates.items():
        patch = {}
        status = fields.get("status")
        if status is not None and status in _SELECTABLE_STATUS_CODES:
            patch["status"] = status
        vendor = fields.get("vendor")
        if vendor is not None and (vendor == "" or vendor in VENDOR_MAP):
            patch["vendor"] = vendor
        cooperation_type = fields.get("cooperation_type")
        if cooperation_type is not None and (cooperation_type == "" or get_cooperation_type(cooperation_type)):
            patch["cooperation_type"] = cooperation_type
        test_drive = fields.get("test_drive")
        if test_drive is not None and test_drive in TEST_DRIVE_STATUS_MAP:
            patch["test_drive"] = test_drive
        note = fields.get("note")
        if note is not None:
            patch["note"] = note.strip()
        if patch:
            batch.update(applicants_ref().document(applicant_id), patch)
            has_writes = True
    if has_writes:
        batch.commit()


def mark_applicant_hired(applicant_id: str, personnel_id: str):
    applicants_ref().document(applicant_id).update({"status": "hired", "converted_personnel_id": personnel_id})


def delete_applicant(applicant_id: str):
    """整筆刪除一筆應徵紀錄（2026-09-15 新增），純粹是清掉「應徵名單」
    這份列表上的紀錄，不影響已經錄取建立的正式人員資料（`personnel_ref()`
    是完全獨立的另一份 Firestore 集合，`converted_personnel_id` 只是
    單向記錄「當初是哪一筆應徵紀錄轉過來的」，刪掉應徵紀錄不會連動刪除
    人員）。應徵紀錄本身沒有另外上傳的檔案，不用像刪除人員那樣額外清
    Cloud Storage 裡的檔案。"""
    applicants_ref().document(applicant_id).delete()


# ==========================================
# 車輛管理（LINE 群組回報領車/還車 + 網頁手動管理）
# ==========================================
def _normalize_vehicle_no(value: str) -> str:
    """車號統一轉大寫＋去頭尾空白再當 Firestore 文件 ID：LINE 回報時同仁常常
    不會特別注意大小寫（例如把 ERV-2360 打成 erv-2360），沒有這層正規化的話
    會查不到明明已經存在的車輛、回覆誤導性的「查不到這台車」。"""
    return (value or "").strip().upper()


def create_vehicle(
    vehicle_no: str,
    vendor: str,
    created_by: str,
    wheel_type: str = DEFAULT_WHEEL_TYPE,
    service_area: str = "",
) -> bool:
    """新增車輛，車號當文件 ID、全公司唯一。已經存在就回傳 False、不會覆蓋
    既有資料；成功新增回傳 True。wheel_type 沒特別指定時預設三輪；
    service_area 沒有通用預設值，沒特別指定就存空字串（見 config.py 的
    說明，報告裡會歸類到「未分區」）。"""
    vehicle_no = _normalize_vehicle_no(vehicle_no)
    ref = vehicles_ref().document(vehicle_no)
    if ref.get().exists:
        return False
    ref.set(
        {
            "vehicle_no": vehicle_no,
            "vendor": vendor,
            "wheel_type": wheel_type or DEFAULT_WHEEL_TYPE,
            "service_area": service_area,
            "status": DEFAULT_VEHICLE_STATUS,
            "current_holder": "",
            "current_location": "",
            "last_event_at": None,
            "created_by": created_by,
            "created_at": time.time(),
        }
    )
    return True


def get_vehicle(vehicle_no: str):
    vehicle_no = _normalize_vehicle_no(vehicle_no)
    snapshot = vehicles_ref().document(vehicle_no).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["vehicle_no"] = snapshot.id
    # 這兩個欄位是陸續才新增的，舊資料的 Firestore 文件裡沒有；wheel_type
    # 統一當成三輪，service_area 沒有通用預設值，統一當成空字串（見
    # config.py 的說明）。
    data.setdefault("wheel_type", DEFAULT_WHEEL_TYPE)
    data.setdefault("service_area", "")
    data.setdefault("current_holder_phone", "")
    data.setdefault("current_note", "")
    return data


def vehicle_matches_filters(
    vehicle: dict,
    vendor_filter: str = "",
    status_filter: str = "",
    vehicle_no_filter: str = "",
    wheel_type_filter: str = "",
    service_area_filter: str = "",
) -> bool:
    """判斷這台車要不要出現在車輛清單裡（純函式）。"""
    if vendor_filter and vehicle.get("vendor") != vendor_filter:
        return False
    if status_filter and vehicle.get("status") != status_filter:
        return False
    if vehicle_no_filter and vehicle_no_filter.upper() not in (vehicle.get("vehicle_no") or "").upper():
        return False
    if wheel_type_filter and vehicle.get("wheel_type", DEFAULT_WHEEL_TYPE) != wheel_type_filter:
        return False
    if service_area_filter and vehicle.get("service_area", "") != service_area_filter:
        return False
    return True


def list_vehicles(
    vendor_filter: str = "",
    status_filter: str = "",
    vehicle_no_filter: str = "",
    wheel_type_filter: str = "",
    service_area_filter: str = "",
) -> list:
    vendor_filter = (vendor_filter or "").strip()
    status_filter = (status_filter or "").strip()
    vehicle_no_filter = (vehicle_no_filter or "").strip()
    wheel_type_filter = (wheel_type_filter or "").strip()
    service_area_filter = (service_area_filter or "").strip()

    result = []
    for snapshot in vehicles_ref().stream():
        data = snapshot.to_dict() or {}
        data["vehicle_no"] = snapshot.id
        data.setdefault("wheel_type", DEFAULT_WHEEL_TYPE)
        data.setdefault("service_area", "")
        data.setdefault("current_holder_phone", "")
        data.setdefault("current_note", "")
        if vehicle_matches_filters(
            data, vendor_filter, status_filter, vehicle_no_filter, wheel_type_filter, service_area_filter
        ):
            result.append(data)
    result.sort(key=lambda v: v.get("vehicle_no", ""))
    return result


def resolve_vehicle_rider_cooperation_type(vehicle: dict):
    """車輛管理清單頁「騎手身份」欄位用（2026-09-18 新增）：車輛主檔的
    current_holder 是自由輸入的文字欄位，沒有連到人員資料的 personnel_id，
    要顯示這台車目前使用人的合作方式，只能靠姓名反查對應的人員資料。

    優先用「姓名+電話」比對（find_active_personnel_by_name_and_phone），
    比對到的人員是唯一的，不會有同名同姓混淆的問題；車輛主檔沒有填
    current_holder_phone 時，才退而用「姓名+廠商」比對
    （find_personnel_by_name_vendor）——這個比對方式如果剛好同廠商有
    同名同姓的人員，可能會抓到錯的人，這是自由輸入文字欄位先天的限制，
    不是這次新增功能造成的（假別登記反查人員資料也有一樣的限制，見
    find_personnel_by_name_vendor() 的說明）。

    找不到對應的人員、或對應的人員沒有設定合作方式時，回傳 None（畫面上
    顯示成沒有騎手身份資料，不是查詢錯誤）。"""
    name = (vehicle.get("current_holder") or "").strip()
    if not name:
        return None
    phone = (vehicle.get("current_holder_phone") or "").strip()
    if phone:
        person = find_active_personnel_by_name_and_phone(name, phone)
    else:
        vendor = vehicle.get("vendor") or ""
        person = find_personnel_by_name_vendor(vendor, name) if vendor else None
    if not person:
        return None
    return get_cooperation_type(person.get("cooperation_type") or "")


def list_vehicle_events(vehicle_no: str) -> list:
    vehicle_no = _normalize_vehicle_no(vehicle_no)
    result = []
    for snapshot in vehicle_events_ref().where("vehicle_no", "==", vehicle_no).stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        # 電話/備註/待維修是 2026-09-16 才新增的欄位，這之前寫入的舊紀錄
        # 沒有這三個 key，統一補上空字串／False，畫面上顯示空白即可。
        data.setdefault("phone", "")
        data.setdefault("note", "")
        data.setdefault("needs_maintenance", False)
        result.append(data)
    result.sort(key=lambda e: e.get("created_at", 0), reverse=True)
    return result


def set_vehicle_status(vehicle_no: str, status: str) -> bool:
    """網頁上手動調整車輛狀態用（例如標記/解除待維修）。只接受合法的狀態
    代碼，車輛不存在或狀態不合法都回傳 False、不會寫入。"""
    if status not in VEHICLE_STATUS_MAP:
        return False
    vehicle_no = _normalize_vehicle_no(vehicle_no)
    ref = vehicles_ref().document(vehicle_no)
    if not ref.get().exists:
        return False
    ref.update({"status": status})
    return True


def set_vehicle_vendor(vehicle_no: str, vendor: str) -> bool:
    """網頁上手動修正車輛所屬的廠商（原本只有新增車輛當下能設定，之後
    沒有地方可以改）。只接受合法的廠商代碼，車輛不存在或代碼不合法都
    回傳 False、不會寫入。"""
    if vendor not in VENDOR_MAP:
        return False
    vehicle_no = _normalize_vehicle_no(vehicle_no)
    ref = vehicles_ref().document(vehicle_no)
    if not ref.get().exists:
        return False
    ref.update({"vendor": vendor})
    return True


def set_vehicle_wheel_type(vehicle_no: str, wheel_type: str) -> bool:
    """網頁上手動修正車輛的輪別（三輪／二輪）。只接受合法的代碼，車輛不
    存在或代碼不合法都回傳 False、不會寫入。"""
    if wheel_type not in WHEEL_TYPE_MAP:
        return False
    vehicle_no = _normalize_vehicle_no(vehicle_no)
    ref = vehicles_ref().document(vehicle_no)
    if not ref.get().exists:
        return False
    ref.update({"wheel_type": wheel_type})
    return True


def set_vehicle_service_area(vehicle_no: str, service_area: str) -> bool:
    """網頁上手動設定/修正車輛的服務區域。空字串代表「未分區」，一樣接受
    （等於清空這個欄位）；有填就要是存在的服務區域 ID（不限啟用中，
    停用的服務區域底下如果還有車輛，一樣可以繼續顯示/選擇，只是新增
    畫面的下拉選單不會再列出來，見 list_vehicle_service_areas()）。車輛
    不存在或 ID 不存在都回傳 False、不會寫入。"""
    if service_area and not get_vehicle_service_area(service_area):
        return False
    vehicle_no = _normalize_vehicle_no(vehicle_no)
    ref = vehicles_ref().document(vehicle_no)
    if not ref.get().exists:
        return False
    ref.update({"service_area": service_area})
    return True


# ---------- 車輛服務區域管理 ----------

def list_vehicle_service_areas(include_inactive: bool = False) -> list:
    result = []
    for snapshot in vehicle_service_areas_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        data.setdefault("active", True)
        if include_inactive or data["active"]:
            result.append(data)
    result.sort(key=lambda a: a.get("name", ""))
    return result


def get_vehicle_service_area(area_id: str):
    if not area_id:
        return None
    snapshot = vehicle_service_areas_ref().document(area_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    data.setdefault("active", True)
    return data


def create_vehicle_service_area(name: str, area_id: str = "", created_by: str = "") -> str:
    """新增一個服務區域。`area_id` 留空時用 Firestore 自動產生的文件 ID
    （主管在網頁上新增走這條路）；有指定時直接用它當文件 ID，只給
    `scripts/seed_vehicle_service_areas.py` 那支一次性遷移腳本使用，讓既有
    車輛存的舊代碼（"taipei"…）可以原封不動對應到新建的服務區域文件，
    不需要另外搬移車輛資料。"""
    now = time.time()
    doc_ref = vehicle_service_areas_ref().document(area_id) if area_id else vehicle_service_areas_ref().document()
    doc_ref.set({"name": name, "active": True, "created_by": created_by, "created_at": now, "updated_at": now})
    return doc_ref.id


def set_vehicle_service_area_active(area_id: str, active: bool) -> bool:
    ref = vehicle_service_areas_ref().document(area_id)
    if not ref.get().exists:
        return False
    ref.update({"active": active, "updated_at": time.time()})
    return True


def vehicle_service_area_has_history(area_id: str) -> bool:
    """判斷有沒有任何車輛的服務區域指到這個 ID——有的話不能真的刪除
    （車輛清單/報告會找不到名稱），只能停用。"""
    return next(vehicles_ref().where("service_area", "==", area_id).limit(1).stream(), None) is not None


def delete_vehicle_service_area(area_id: str) -> bool:
    if vehicle_service_area_has_history(area_id):
        return False
    vehicle_service_areas_ref().document(area_id).delete()
    return True


def vehicle_event_error(vehicle, vendor: str, event_type: str) -> str:
    """判斷一筆領車/還車事件套用到這台車目前的狀態合不合理（純函式，vehicle
    需已經查好、找不到就傳 None）。回傳空字串代表可以記錄；非空字串是擋下的
    錯誤代碼：
    - "vehicle_not_found"：車號不存在，要先在網頁新增這台車。
    - "vendor_mismatch"：回報的廠商跟這台車登記的廠商不一樣。
    - "not_available"：領車時車輛目前是使用中，不能再派車。
    - "already_maintenance"：領車時車輛目前已經是待維修狀態，不能再派車；
      跟 "not_available" 分開是因為原因不一樣（一個是被別人領走了，一個是
      車子本來就已經在等維修），錯誤訊息也要分開講清楚。
    - "not_in_use"：還車時車輛目前不是使用中，沒有領用中的紀錄可以還。
    """
    if vehicle is None:
        return "vehicle_not_found"
    if vehicle.get("vendor") != vendor:
        return "vendor_mismatch"
    status = vehicle.get("status", DEFAULT_VEHICLE_STATUS)
    if event_type == "checkout":
        if status == "maintenance":
            return "already_maintenance"
        if status == "in_use":
            return "not_available"
    if event_type == "return" and status != "in_use":
        return "not_in_use"
    return ""


def record_vehicle_event(
    vehicle_no: str,
    vendor: str,
    personnel_name: str,
    event_type: str,
    event_date: str,
    location: str,
    source: str,
    reported_by: str = "",
    phone: str = "",
    note: str = "",
    needs_maintenance: bool = False,
) -> tuple:
    """驗證通過（見 vehicle_event_error）才會真的寫入事件紀錄、同步更新車輛
    主檔的狀態/使用人/地點。回傳 (True, "") 代表成功；(False, 錯誤代碼) 代表
    被擋下，呼叫端可以把錯誤代碼轉成對應的訊息（LINE 回覆或網頁錯誤提示）。
    source 是 "line" 或 "manual"，用來區分這筆事件是 LINE 群組回報還是網頁
    手動補登的。

    needs_maintenance=True（回報時「待維修」填「是」）代表同仁發現車輛故障，
    不管這筆是領車還是還車，車輛最終狀態都直接變成「待維修」，不走原本
    領車→使用中／還車→可用的轉換，同仁不用再另外進系統點一次「標記待維修」。
    這種情況下「目前使用人」統一清空，不填領車人姓名——因為車子其實沒有真的
    被騎走，這裡填了人名畫面上容易讓人誤以為車在他手上（詳見 2026-09-16
    HANDOFF.md 的討論）。"""
    vehicle_no = _normalize_vehicle_no(vehicle_no)
    vehicle = get_vehicle(vehicle_no)
    error = vehicle_event_error(vehicle, vendor, event_type)
    if error:
        return False, error

    now = time.time()
    vehicle_events_ref().document().set(
        {
            "vehicle_no": vehicle_no,
            "vendor": vendor,
            "personnel_name": personnel_name,
            "event_type": event_type,
            "event_date": event_date,
            "location": location,
            "source": source,
            "reported_by": reported_by,
            "phone": phone,
            "note": note,
            "needs_maintenance": needs_maintenance,
            "created_at": now,
        }
    )

    if needs_maintenance:
        new_status = "maintenance"
        new_holder = ""
    else:
        new_status = "in_use" if event_type == "checkout" else "available"
        new_holder = personnel_name if event_type == "checkout" else ""
    vehicles_ref().document(vehicle_no).update(
        {
            "status": new_status,
            "current_holder": new_holder,
            "current_holder_phone": phone if new_holder else "",
            "current_location": location,
            "current_note": note,
            "last_event_at": now,
        }
    )
    return True, ""


def get_vehicle_event(event_id: str):
    snapshot = vehicle_events_ref().document(event_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    data.setdefault("phone", "")
    data.setdefault("note", "")
    data.setdefault("needs_maintenance", False)
    return data


def update_vehicle_event(
    event_id: str,
    vendor: str,
    personnel_name: str,
    event_type: str,
    event_date: str,
    location: str,
    phone: str = "",
    note: str = "",
    needs_maintenance: bool = False,
) -> bool:
    """網頁上修正一筆既有的領還紀錄（例如日期、地點打錯）。只接受合法的
    事件類型，事件不存在回傳 False、不會寫入。

    如果這筆剛好是這台車目前反映的最新一筆事件（用建立時間戳記 created_at
    跟車輛主檔的 last_event_at 比對——兩者是同一次 record_vehicle_event()
    呼叫寫入的同一個時間戳，可以直接比對是否相等），連動更新車輛主檔目前
    的使用人／地點／狀態，避免歷史紀錄改完之後跟主檔顯示的「目前狀態」
    兜不起來；車輛目前是「待維修」時跳過這個連動，因為待維修狀態可能是
    管理員另外手動標記的（跟這筆事件無關），也可能就是這筆事件自己造成的
    ——不管哪一種，都不該被這裡的編輯悄悄覆寫掉（例如把「待維修」改回
    「使用中」）；如果同仁是想撤銷這筆事件造成的待維修狀態，要另外到車輛
    詳細頁按「解除待維修」，不是透過編輯歷史紀錄改。修正比較舊的一筆歷史
    紀錄，或車輛目前本來就已經是待維修，都完全不影響車輛主檔目前狀態，
    純粹只是改歷史紀錄本身。"""
    if event_type not in ("checkout", "return"):
        return False
    ref = vehicle_events_ref().document(event_id)
    snapshot = ref.get()
    if not snapshot.exists:
        return False
    existing = snapshot.to_dict() or {}
    vehicle_no = existing.get("vehicle_no", "")

    ref.update(
        {
            "vendor": vendor,
            "personnel_name": personnel_name,
            "event_type": event_type,
            "event_date": event_date,
            "location": location,
            "phone": phone,
            "note": note,
            "needs_maintenance": needs_maintenance,
        }
    )

    vehicle = get_vehicle(vehicle_no)
    is_latest_event = vehicle and vehicle.get("last_event_at") == existing.get("created_at")
    if is_latest_event and vehicle.get("status") != "maintenance":
        if needs_maintenance:
            new_status = "maintenance"
            new_holder = ""
        else:
            new_status = "in_use" if event_type == "checkout" else "available"
            new_holder = personnel_name if event_type == "checkout" else ""
        vehicles_ref().document(vehicle_no).update(
            {
                "status": new_status,
                "current_holder": new_holder,
                "current_holder_phone": phone if new_holder else "",
                "current_location": location,
                "current_note": note,
            }
        )
    return True


def delete_vehicle_event(event_id: str) -> bool:
    """刪除一筆領還車歷史紀錄，只限管理員操作。**刻意不去反推、連動更新
    車輛主檔目前的狀態/使用人/地點**——跟 update_vehicle_event() 不同，
    刪除這個動作沒有「新的值」可以拿來同步，就算刪的剛好是目前反映在
    主檔上的最新一筆事件，車輛主檔也不會自動改變（避免猜錯方向，憑空
    把車輛狀態改成不知道對不對的值）；如果刪除後發現車輛主檔顯示的
    狀態/使用人不對，請直接在車輛詳細頁用「標記待維修」／更新廠商等
    既有功能手動修正，或者新增一筆正確的事件蓋過去。事件不存在回傳
    False。"""
    ref = vehicle_events_ref().document(event_id)
    if not ref.get().exists:
        return False
    ref.delete()
    return True


# ==========================================
# 意外事件回報
# 跟車輛回報同一個 LINE 群組，但資料完全獨立的一份 collection。風險等級
# （risk_level）跟結案狀態（status）都不是回報當下填的，是管理員事後在
# 網頁上評估／操作，所以新增時一律是空風險等級 + 未結案。
# ==========================================
_INCIDENT_FIELDS = (
    "vendor",
    "identity_type",
    "personnel_name",
    "occurred_at",
    "location",
    "duty_status",
    "police_called",
    "injury",
    "family_contacted",
    "third_party_involved",
    "description",
    # 2026-09-15 新增，選填、只有網站表單有這個欄位（LINE 群組回報範本
    # 沒有這一項，見 delivery/incident_report.py），LINE 回報進來的資料
    # 這裡一律用預設值空字串補上。
    "license_plate",
)


def _find_incident_event_by_key(personnel_name: str, occurred_at: str):
    """依「人員名稱＋發生時間」找既有的意外事件回報，找不到回傳 None。
    這組合視為同一起事件的識別鍵（見 create_incident_event() 的說明），
    兩者缺一不比對，避免空字串互相誤判成同一筆。"""
    if not personnel_name or not occurred_at:
        return None
    query = (
        incident_events_ref()
        .where("personnel_name", "==", personnel_name)
        .where("occurred_at", "==", occurred_at)
        .limit(1)
    )
    for snapshot in query.stream():
        return snapshot.id
    return None


def create_incident_event(data: dict) -> tuple:
    """新增一筆意外事件回報，回傳 (incident_id, created)：
    - 如果「人員名稱＋發生時間」跟既有紀錄完全相同，視為同仁在回報同一起
      事件（例如手滑重傳、或發現打錯字重新回報修正），直接覆寫既有那筆
      的回報內容（_INCIDENT_FIELDS 這 11 個 LINE 必填欄位），不會多開一筆
      重複紀錄，created 回傳 False。
    - 找不到既有紀錄才真的新建一筆，風險等級／結案狀態用預設值（不接受
      呼叫端指定），created 回傳 True。

    刻意不覆寫既有紀錄的 risk_level／status／created_at——那是管理員事後
    才會填的欄位，同仁重傳同一起事件的內容更新，不該把管理員已經做的
    風險評估／結案狀態洗掉。data 需含 _INCIDENT_FIELDS 這 11 個 LINE 必填
    欄位（見 delivery.incident_report.parse_incident_report 的回傳值）。

    license_plate（選填、只有網站表單會填）特別處理：LINE 群組回報的
    data 完全不會帶這個 key（LINE 範本沒有這一項），如果同仁事後在 LINE
    重傳同一起事件（覆寫既有紀錄的情境），不能因為這次的 data 沒有這個
    key 就把先前網站上補登的車牌號碼洗成空白——只有 data 真的有帶這個
    key（來自網站表單，包含表單裡刻意清空送出的情況）才會覆寫既有值。"""
    payload = {key: data.get(key, "") for key in _INCIDENT_FIELDS if key != "license_plate"}

    existing_id = _find_incident_event_by_key(data.get("personnel_name", ""), data.get("occurred_at", ""))
    if existing_id:
        if "license_plate" in data:
            payload["license_plate"] = data["license_plate"]
        incident_events_ref().document(existing_id).update(payload)
        return existing_id, False

    payload["license_plate"] = data.get("license_plate", "")
    ref = incident_events_ref().document()
    payload["risk_level"] = ""
    payload["status"] = DEFAULT_INCIDENT_STATUS
    payload["created_at"] = time.time()
    ref.set(payload)
    return ref.id, True


def get_incident_event(incident_id: str):
    snapshot = incident_events_ref().document(incident_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def incident_matches_filters(
    incident: dict,
    vendor_filter: str = "",
    status_filter: str = "",
    risk_level_filter: str = "",
    personnel_name_filter: str = "",
) -> bool:
    """判斷這筆意外事件要不要出現在清單裡（純函式）。"""
    if vendor_filter and incident.get("vendor") != vendor_filter:
        return False
    if status_filter and incident.get("status") != status_filter:
        return False
    if risk_level_filter and incident.get("risk_level") != risk_level_filter:
        return False
    if personnel_name_filter and personnel_name_filter not in (incident.get("personnel_name") or ""):
        return False
    return True


def list_incident_events(
    vendor_filter: str = "",
    status_filter: str = "",
    risk_level_filter: str = "",
    personnel_name_filter: str = "",
) -> list:
    vendor_filter = (vendor_filter or "").strip()
    status_filter = (status_filter or "").strip()
    risk_level_filter = (risk_level_filter or "").strip()
    personnel_name_filter = (personnel_name_filter or "").strip()

    result = []
    for snapshot in incident_events_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        if incident_matches_filters(data, vendor_filter, status_filter, risk_level_filter, personnel_name_filter):
            result.append(data)
    result.sort(key=lambda i: i.get("created_at", 0), reverse=True)
    return result


def list_open_incident_events() -> list:
    """未結案案件清單，給每週一群組提醒跟系統登入提醒用。"""
    result = []
    for snapshot in incident_events_ref().where("status", "==", "open").stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        result.append(data)
    result.sort(key=lambda i: i.get("created_at", 0), reverse=True)
    return result


def set_incident_risk_level(incident_id: str, risk_level: str) -> bool:
    """管理員在詳細頁設定風險等級，只接受合法的等級代碼。"""
    if risk_level not in RISK_LEVELS:
        return False
    ref = incident_events_ref().document(incident_id)
    if not ref.get().exists:
        return False
    ref.update({"risk_level": risk_level})
    return True


def close_incident_event(incident_id: str) -> bool:
    """標記結案，單向操作（跟補款/假別核准一樣，沒有重新打開的路徑）。"""
    ref = incident_events_ref().document(incident_id)
    if not ref.get().exists:
        return False
    ref.update({"status": "closed"})
    return True


def update_incident_event(incident_id: str, data: dict) -> bool:
    """管理員在意外事件詳細頁修正原始回報內容（例如地點打錯字、經過描述
    要補充）。只更新 _INCIDENT_FIELDS 這 11 個回報欄位，風險等級／結案
    狀態不受影響——那兩個欄位各自有自己的操作入口（見
    set_incident_risk_level／close_incident_event），不該被這裡的編輯
    表單意外洗掉。事件不存在回傳 False、不會寫入。"""
    ref = incident_events_ref().document(incident_id)
    if not ref.get().exists:
        return False
    payload = {key: data.get(key, "") for key in _INCIDENT_FIELDS}
    ref.update(payload)
    return True


def delete_incident_event(incident_id: str) -> bool:
    """刪除一筆意外事件回報，只限管理員操作。不管風險等級／結案狀態，
    管理員都可以刪除——跟補款/假別不同，這裡沒有「已核准」之類會被其他
    地方引用/對帳的欄位，刪除單純是移除這筆回報本身。事件不存在回傳
    False。"""
    ref = incident_events_ref().document(incident_id)
    if not ref.get().exists:
        return False
    ref.delete()
    return True


# ==========================================
# 裝備借還管理（2026-09-17 新增）
#
# 品項、放置點是主管可以自己在網頁上新增/停用的動態清單（不像廠商/假別
# 寫死在 config.py），庫存跟尚欠都是「流水帳自動結算」的概念：每一筆
# 異動（借用/歸還/轉倉/採購新增/買斷/核銷）都會即時更新對應的庫存文件
# 跟尚欠文件，不需要每次都重新掃描全部歷史紀錄加總。
# ==========================================

# ---------- 品項管理 ----------

def list_equipment_items(include_inactive: bool = False) -> list:
    result = []
    for snapshot in equipment_items_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        data.setdefault("active", True)
        data.setdefault("buyout_unit_price", None)
        if include_inactive or data["active"]:
            result.append(data)
    result.sort(key=lambda i: i.get("name", ""))
    return result


def get_equipment_item(item_id: str):
    snapshot = equipment_items_ref().document(item_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    data.setdefault("active", True)
    data.setdefault("buyout_unit_price", None)
    return data


def create_equipment_item(name: str, buyout_unit_price=None, created_by: str = "") -> str:
    now = time.time()
    doc_ref = equipment_items_ref().document()
    doc_ref.set(
        {
            "name": name,
            "buyout_unit_price": buyout_unit_price,
            "active": True,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }
    )
    return doc_ref.id


def update_equipment_item(item_id: str, name: str, buyout_unit_price=None) -> bool:
    ref = equipment_items_ref().document(item_id)
    if not ref.get().exists:
        return False
    ref.update({"name": name, "buyout_unit_price": buyout_unit_price, "updated_at": time.time()})
    return True


def set_equipment_item_active(item_id: str, active: bool) -> bool:
    ref = equipment_items_ref().document(item_id)
    if not ref.get().exists:
        return False
    ref.update({"active": active, "updated_at": time.time()})
    return True


def equipment_item_has_history(item_id: str) -> bool:
    """判斷這個品項有沒有任何異動紀錄過——有的話不能真的刪除（會讓歷史
    紀錄查不到品項名稱、帳也對不起來），只能停用。"""
    return next(equipment_transactions_ref().where("item_id", "==", item_id).limit(1).stream(), None) is not None


def delete_equipment_item(item_id: str) -> bool:
    """真的從 Firestore 刪除，只有完全沒有異動紀錄過的品項才允許（例如
    剛新增打錯字想刪掉重打）；有歷史紀錄的品項只能停用
    （set_equipment_item_active(item_id, False)），不能刪除。"""
    if equipment_item_has_history(item_id):
        return False
    equipment_items_ref().document(item_id).delete()
    return True


# ---------- 放置點管理 ----------

def list_equipment_locations(include_inactive: bool = False) -> list:
    result = []
    for snapshot in equipment_locations_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        data.setdefault("active", True)
        if include_inactive or data["active"]:
            result.append(data)
    result.sort(key=lambda loc: loc.get("name", ""))
    return result


def get_equipment_location(location_id: str):
    snapshot = equipment_locations_ref().document(location_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    data.setdefault("active", True)
    return data


def create_equipment_location(name: str, created_by: str = "") -> str:
    now = time.time()
    doc_ref = equipment_locations_ref().document()
    doc_ref.set({"name": name, "active": True, "created_by": created_by, "created_at": now, "updated_at": now})
    return doc_ref.id


def set_equipment_location_active(location_id: str, active: bool) -> bool:
    ref = equipment_locations_ref().document(location_id)
    if not ref.get().exists:
        return False
    ref.update({"active": active, "updated_at": time.time()})
    return True


def equipment_location_has_history(location_id: str) -> bool:
    from_hit = next(
        equipment_transactions_ref().where("from_location_id", "==", location_id).limit(1).stream(), None
    )
    if from_hit is not None:
        return True
    return next(equipment_transactions_ref().where("to_location_id", "==", location_id).limit(1).stream(), None) is not None


def delete_equipment_location(location_id: str) -> bool:
    if equipment_location_has_history(location_id):
        return False
    equipment_locations_ref().document(location_id).delete()
    return True


# ---------- 庫存 ----------

def _equipment_stock_doc_id(location_id: str, item_id: str) -> str:
    return f"{location_id}__{item_id}"


def get_equipment_stock(location_id: str, item_id: str) -> dict:
    """回傳這個放置點/品項的庫存資料；沒有異動過的組合當作數量0、門檻0，
    不會回傳 None，呼叫端不用另外判斷有沒有這筆紀錄。"""
    snapshot = equipment_stock_ref().document(_equipment_stock_doc_id(location_id, item_id)).get()
    if not snapshot.exists:
        return {"location_id": location_id, "item_id": item_id, "quantity": 0, "warning_threshold": 0}
    data = snapshot.to_dict() or {}
    data.setdefault("quantity", 0)
    data.setdefault("warning_threshold", 0)
    return data


def list_equipment_stock(location_id: str = "") -> list:
    """庫存總覽用：回傳目前有紀錄的「放置點 x 品項」庫存列。從沒異動過的
    組合不會出現在這裡（視為庫存0），畫面上用品項/放置點清單自己補齊
    顯示0的組合，這裡不強求回傳完整矩陣。"""
    result = []
    query = equipment_stock_ref()
    if location_id:
        query = query.where("location_id", "==", location_id)
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        data.setdefault("quantity", 0)
        data.setdefault("warning_threshold", 0)
        result.append(data)
    return result


def set_equipment_stock_threshold(location_id: str, item_id: str, threshold: int) -> None:
    """設定第一層警戒值門檻（純提醒用，不擋借用）。用 set(merge=True) 而不是
    update()，因為這個放置點/品項組合可能還沒有庫存紀錄（數量是0、只是
    想先把門檻設好），update() 對不存在的文件會丟例外。"""
    doc_ref = equipment_stock_ref().document(_equipment_stock_doc_id(location_id, item_id))
    doc_ref.set(
        {"location_id": location_id, "item_id": item_id, "warning_threshold": threshold, "updated_at": time.time()},
        merge=True,
    )


def equipment_stock_below_threshold(location_id: str, item_id: str) -> bool:
    """門檻是0代表「沒設定/不提醒」，永遠回傳False。"""
    stock = get_equipment_stock(location_id, item_id)
    threshold = stock.get("warning_threshold", 0)
    return threshold > 0 and stock.get("quantity", 0) < threshold


def _adjust_equipment_stock(location_id: str, item_id: str, delta: int) -> None:
    """異動庫存數量（正數增加、負數減少）。跟系統其他地方一致，用「讀出
    現有值再寫回去」而不是 Firestore 的原子遞增——配送部整體流量小，
    暫時不需要處理併發衝突覆寫的問題。"""
    doc_ref = equipment_stock_ref().document(_equipment_stock_doc_id(location_id, item_id))
    current = get_equipment_stock(location_id, item_id)
    doc_ref.set(
        {
            "location_id": location_id,
            "item_id": item_id,
            "quantity": current["quantity"] + delta,
            "warning_threshold": current["warning_threshold"],
            "updated_at": time.time(),
        },
        merge=True,
    )


# ---------- 尚欠 ----------

def _equipment_debt_doc_id(personnel_id: str, item_id: str) -> str:
    return f"{personnel_id}__{item_id}"


def get_equipment_debt(personnel_id: str, item_id: str) -> dict:
    snapshot = equipment_debt_ref().document(_equipment_debt_doc_id(personnel_id, item_id)).get()
    if not snapshot.exists:
        return {"personnel_id": personnel_id, "item_id": item_id, "quantity_owed": 0}
    data = snapshot.to_dict() or {}
    data.setdefault("quantity_owed", 0)
    return data


def list_equipment_debt(personnel_id: str = "", only_outstanding: bool = True) -> list:
    """尚欠總表用：每個人在每個品項的尚欠數量。只有借用/歸還/買斷/核銷
    會異動這裡，轉倉、採購新增不影響任何人的尚欠。"""
    result = []
    query = equipment_debt_ref()
    if personnel_id:
        query = query.where("personnel_id", "==", personnel_id)
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        data.setdefault("quantity_owed", 0)
        if only_outstanding and data["quantity_owed"] <= 0:
            continue
        result.append(data)
    result.sort(key=lambda d: d.get("quantity_owed", 0), reverse=True)
    return result


def _adjust_equipment_debt(personnel_id: str, item_id: str, delta: int) -> None:
    doc_ref = equipment_debt_ref().document(_equipment_debt_doc_id(personnel_id, item_id))
    current = get_equipment_debt(personnel_id, item_id)
    doc_ref.set(
        {
            "personnel_id": personnel_id,
            "item_id": item_id,
            "quantity_owed": current["quantity_owed"] + delta,
            "updated_at": time.time(),
        },
        merge=True,
    )


# ---------- 異動登記 ----------

def equipment_transaction_error(
    transaction_type: str,
    personnel,
    from_stock: dict,
    quantity,
    debt: dict = None,
    override_stock_check: bool = False,
) -> str:
    """純函式：判斷一筆裝備異動能不能記錄（vehicle_event_error 的做法，
    Firestore 讀取跟驗證邏輯分開，方便直接寫單元測試）。回傳空字串代表
    可以記錄；非空字串是擋下的錯誤代碼：
    - "invalid_quantity"：數量不是大於0的整數。
    - "personnel_not_found"：這個異動類型需要選騎士，但沒查到這個人。
    - "personnel_missing_documents"：這個人缺件資料還沒補齊，不能借用
      （沿用 missing_documents() 既有的缺件判斷，人員缺件清單用同一套
      規則，不另外維護一份）。
    - "insufficient_stock"：借用/轉倉時，來源放置點庫存不夠這次數量
      （override_stock_check=True 時略過，給主管特批例外用，但其餘檢查
      照樣要過）。
    - "insufficient_debt"：歸還/買斷的數量超過這個人目前實際尚欠的數量。
    """
    if not isinstance(quantity, int) or quantity <= 0:
        return "invalid_quantity"

    if transaction_type in EQUIPMENT_TRANSACTION_TYPES_REQUIRING_PERSONNEL:
        if personnel is None:
            return "personnel_not_found"
        if missing_documents(personnel):
            return "personnel_missing_documents"

    if transaction_type in ("borrow", "transfer") and not override_stock_check:
        available = (from_stock or {}).get("quantity", 0)
        if available < quantity:
            return "insufficient_stock"

    if transaction_type in ("return", "buyout"):
        owed = (debt or {}).get("quantity_owed", 0)
        if quantity > owed:
            return "insufficient_debt"

    return ""


def _apply_equipment_transaction_effect(
    transaction_type: str,
    item_id: str,
    quantity: int,
    from_location_id: str,
    to_location_id: str,
    personnel_id: str,
    sign: int = 1,
) -> None:
    """實際異動庫存/尚欠的共用邏輯，被 record_equipment_transaction()（新增，
    sign=1）跟 update_equipment_transaction()／delete_equipment_transaction()
    （復原舊效果，sign=-1）共用，確保「反向」永遠是同一份邏輯的鏡像，
    不會有兩邊各自維護、改一邊忘了改另一邊的風險。sign=-1 時每個 delta
    直接反過來，不需要另外為每種異動類型各寫一次反向規則。writeoff（核銷）
    不經過這裡——它的效果只有「尚欠歸零」，用自己的邏輯處理（見
    record_equipment_writeoff() 跟 delete_equipment_transaction()）。"""
    q = quantity * sign
    if transaction_type == "borrow":
        _adjust_equipment_stock(from_location_id, item_id, -q)
        _adjust_equipment_debt(personnel_id, item_id, q)
    elif transaction_type == "return":
        _adjust_equipment_stock(from_location_id, item_id, q)
        _adjust_equipment_debt(personnel_id, item_id, -q)
    elif transaction_type == "transfer":
        _adjust_equipment_stock(from_location_id, item_id, -q)
        _adjust_equipment_stock(to_location_id, item_id, q)
    elif transaction_type == "purchase":
        _adjust_equipment_stock(to_location_id, item_id, q)
    elif transaction_type == "buyout":
        _adjust_equipment_debt(personnel_id, item_id, -q)


def record_equipment_transaction(
    transaction_type: str,
    item_id: str,
    quantity: int,
    from_location_id: str = "",
    to_location_id: str = "",
    personnel_id: str = "",
    unit_price=None,
    payment_received: bool = False,
    reported_by: str = "",
    override_stock_check: bool = False,
) -> tuple:
    """驗證通過（見 equipment_transaction_error）才會真的寫入一筆裝備異動
    紀錄，同步更新庫存／尚欠。回傳 (True, "") 代表成功；(False, 錯誤代碼)
    代表被擋下。

    各異動類型實際影響：
    - 借用（borrow）：來源放置點庫存 -quantity，這個人在這個品項的尚欠
      +quantity。
    - 歸還（return）：來源放置點庫存 +quantity，尚欠 -quantity。
    - 轉倉（transfer）：來源放置點庫存 -quantity、目的放置點庫存
      +quantity，一筆紀錄同時處理「轉出」跟「轉入」，不影響任何人的
      尚欠。
    - 採購新增（purchase）：目的放置點庫存 +quantity，不影響尚欠。
    - 買斷（buyout）：尚欠 -quantity，但**不**加回任何放置點庫存——裝備
      留在騎士手上，沒有實體歸還這回事。
    """
    personnel = get_personnel(personnel_id) if personnel_id else None
    from_stock = get_equipment_stock(from_location_id, item_id) if from_location_id else None
    debt = get_equipment_debt(personnel_id, item_id) if personnel_id else None

    error = equipment_transaction_error(
        transaction_type, personnel, from_stock, quantity, debt=debt, override_stock_check=override_stock_check
    )
    if error:
        return False, error

    now = time.time()
    equipment_transactions_ref().document().set(
        {
            "type": transaction_type,
            "item_id": item_id,
            "quantity": quantity,
            "from_location_id": from_location_id,
            "to_location_id": to_location_id,
            "personnel_id": personnel_id,
            "unit_price": unit_price,
            "total_amount": (unit_price * quantity) if unit_price is not None else None,
            "payment_received": payment_received,
            "override_stock_check": override_stock_check,
            "reported_by": reported_by,
            "created_at": now,
        }
    )

    _apply_equipment_transaction_effect(
        transaction_type, item_id, quantity, from_location_id, to_location_id, personnel_id, sign=1
    )

    return True, ""


def record_equipment_writeoff(personnel_id: str, item_id: str, reason: str, operated_by: str) -> tuple:
    """核銷：把這個人在這個品項的尚欠直接歸零，公司自己吸收成本。限主管
    操作（路由層擋，這裡不重複判斷角色）。回傳 (True, "") 或
    (False, 錯誤代碼)：
    - "no_outstanding_debt"：這個人這項裝備目前沒有尚欠，沒什麼好核銷的。
    - "reason_required"：核銷一定要填原因，方便事後追查。
    """
    debt = get_equipment_debt(personnel_id, item_id)
    owed = debt.get("quantity_owed", 0)
    if owed <= 0:
        return False, "no_outstanding_debt"
    if not (reason or "").strip():
        return False, "reason_required"

    now = time.time()
    equipment_transactions_ref().document().set(
        {
            "type": "writeoff",
            "item_id": item_id,
            "quantity": owed,
            "from_location_id": "",
            "to_location_id": "",
            "personnel_id": personnel_id,
            "unit_price": None,
            "total_amount": None,
            "payment_received": False,
            "reason": reason.strip(),
            "reported_by": operated_by,
            "created_at": now,
        }
    )
    _adjust_equipment_debt(personnel_id, item_id, -owed)
    return True, ""


def update_equipment_transaction(
    transaction_id: str,
    quantity: int,
    from_location_id: str = "",
    to_location_id: str = "",
    personnel_id: str = "",
    unit_price=None,
    payment_received: bool = False,
    reason: str = "",
    override_stock_check: bool = False,
) -> tuple:
    """修正一筆既有的裝備異動登記（例如數量、騎士選錯）。限主管操作（路由層
    擋）。做法是「先把舊的效果復原，用復原後的庫存/尚欠狀態驗證新的值，
    通過才套用新效果」——不能只是單純改欄位，因為庫存/尚欠是每筆異動當下
    即時累加的流水帳（見 repository.py 開頭「裝備借還管理」那節的說明），
    改掉一筆歷史異動的數量，庫存/尚欠也要跟著調整，不然帳會對不起來。
    驗證沒過會把復原的效果加回去（rollback），不會留下「復原了但沒套用
    新效果」的中間狀態。

    **核銷（writeoff）不吃這套邏輯**——它的「數量」是核銷當下的實際尚欠，
    不是使用者填的值，改動量沒有意義；這裡只讓核銷改「原因」欄位，其他
    參數會被忽略。

    回傳 (True, "") 或 (False, 錯誤代碼)：
    - "not_found"：這筆異動紀錄不存在。
    - 其餘錯誤代碼跟 equipment_transaction_error() 一致。
    """
    ref = equipment_transactions_ref().document(transaction_id)
    snapshot = ref.get()
    if not snapshot.exists:
        return False, "not_found"
    old = snapshot.to_dict() or {}
    transaction_type = old.get("type", "")

    if transaction_type == "writeoff":
        ref.update({"reason": (reason or "").strip()})
        return True, ""

    item_id = old.get("item_id", "")
    _apply_equipment_transaction_effect(
        transaction_type,
        item_id,
        old.get("quantity", 0),
        old.get("from_location_id", ""),
        old.get("to_location_id", ""),
        old.get("personnel_id", ""),
        sign=-1,
    )

    personnel = get_personnel(personnel_id) if personnel_id else None
    from_stock = get_equipment_stock(from_location_id, item_id) if from_location_id else None
    debt = get_equipment_debt(personnel_id, item_id) if personnel_id else None
    error = equipment_transaction_error(
        transaction_type, personnel, from_stock, quantity, debt=debt, override_stock_check=override_stock_check
    )
    if error:
        _apply_equipment_transaction_effect(
            transaction_type,
            item_id,
            old.get("quantity", 0),
            old.get("from_location_id", ""),
            old.get("to_location_id", ""),
            old.get("personnel_id", ""),
            sign=1,
        )
        return False, error

    _apply_equipment_transaction_effect(
        transaction_type, item_id, quantity, from_location_id, to_location_id, personnel_id, sign=1
    )
    ref.update(
        {
            "quantity": quantity,
            "from_location_id": from_location_id,
            "to_location_id": to_location_id,
            "personnel_id": personnel_id,
            "unit_price": unit_price,
            "total_amount": (unit_price * quantity) if unit_price is not None else None,
            "payment_received": payment_received,
            "reason": (reason or "").strip(),
            "override_stock_check": override_stock_check,
        }
    )
    return True, ""


def delete_equipment_transaction(transaction_id: str) -> bool:
    """刪除一筆裝備異動登記，限主管操作（路由層擋）。刪除前先把這筆紀錄
    造成的庫存/尚欠效果復原（核銷是把核銷掉的尚欠加回去；其餘類型復用
    _apply_equipment_transaction_effect 的反向邏輯），確保刪除歷史紀錄後
    庫存/尚欠總表仍然正確，不會殘留這筆已刪除紀錄的影響。紀錄不存在
    回傳 False。"""
    ref = equipment_transactions_ref().document(transaction_id)
    snapshot = ref.get()
    if not snapshot.exists:
        return False
    data = snapshot.to_dict() or {}
    transaction_type = data.get("type", "")
    if transaction_type == "writeoff":
        _adjust_equipment_debt(data.get("personnel_id", ""), data.get("item_id", ""), data.get("quantity", 0))
    else:
        _apply_equipment_transaction_effect(
            transaction_type,
            data.get("item_id", ""),
            data.get("quantity", 0),
            data.get("from_location_id", ""),
            data.get("to_location_id", ""),
            data.get("personnel_id", ""),
            sign=-1,
        )
    ref.delete()
    return True


def get_equipment_transaction(transaction_id: str):
    snapshot = equipment_transactions_ref().document(transaction_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def list_equipment_transactions(
    location_id: str = "", item_id: str = "", personnel_id: str = "", transaction_type: str = ""
) -> list:
    """歷史紀錄查詢。location_id 篩選會同時比對來源／目的放置點——轉倉
    一筆紀錄橫跨兩個放置點，篩某個放置點時，不管它是轉出方還是轉入方
    都要看得到這筆。"""
    result = []
    for snapshot in equipment_transactions_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        if item_id and data.get("item_id") != item_id:
            continue
        if personnel_id and data.get("personnel_id") != personnel_id:
            continue
        if transaction_type and data.get("type") != transaction_type:
            continue
        if location_id and location_id not in (data.get("from_location_id"), data.get("to_location_id")):
            continue
        result.append(data)
    result.sort(key=lambda t: t.get("created_at", 0), reverse=True)
    return result


# ==========================================
# 公告管理（2026-09-18 新增）
#
# 主頁公告欄：主管可以自行發佈公告（例如系統維護時間、新功能上線通知），
# 不用找工程師改網頁。跟裝備品項/服務區域/合作方式那些動態清單一樣走
# Firestore，差別在這裡沒有「有沒有歷史紀錄」的刪除保護——公告本來就是
# 用完即丟的內容，沒有其他資料會引用到某一則公告的 ID，可以隨時刪除。
#
# 「到期自動下架」是用 expires_at 這個時間戳記比對目前時間算出來的，
# 不是排程去改資料库——list_active_announcements() 每次查詢都重新算一次
# 「還沒過期」，超過期限的公告不用另外清除，只是查不到而已（管理頁面
# 用 list_announcements() 撈全部，包含已過期的，讓主管可以回顧/手動
# 提前刪除）。active 這個欄位是給主管「不用等到期，想馬上下架」用的
# 手動開關，跟其他清單一致（set_X_active 的命名/行為同一套）。
# ==========================================

ANNOUNCEMENT_DEFAULT_DAYS = 7


def list_active_announcements() -> list:
    """主頁顯示用：只回傳「還在啟用中，而且還沒過期」的公告，新到舊排序。"""
    now = time.time()
    result = []
    for snapshot in announcements_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        data.setdefault("active", True)
        if not data["active"]:
            continue
        if data.get("expires_at", 0) <= now:
            continue
        result.append(data)
    result.sort(key=lambda a: a.get("created_at", 0), reverse=True)
    return result


def list_announcements() -> list:
    """公告管理頁用：回傳全部公告（含已停用、已過期的），新到舊排序，
    讓主管可以回顧之前發過什麼公告。"""
    now = time.time()
    result = []
    for snapshot in announcements_ref().stream():
        data = snapshot.to_dict() or {}
        data["id"] = snapshot.id
        data.setdefault("active", True)
        data["expired"] = data.get("expires_at", 0) <= now
        result.append(data)
    result.sort(key=lambda a: a.get("created_at", 0), reverse=True)
    return result


def get_announcement(announcement_id: str):
    if not announcement_id:
        return None
    snapshot = announcements_ref().document(announcement_id).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    data.setdefault("active", True)
    return data


def create_announcement(title: str, content: str, created_by: str = "", days: int = ANNOUNCEMENT_DEFAULT_DAYS) -> str:
    now = time.time()
    doc_ref = announcements_ref().document()
    doc_ref.set(
        {
            "title": title,
            "content": content,
            "active": True,
            "created_by": created_by,
            "created_at": now,
            "expires_at": now + days * 86400,
        }
    )
    return doc_ref.id


def set_announcement_active(announcement_id: str, active: bool) -> bool:
    ref = announcements_ref().document(announcement_id)
    if not ref.get().exists:
        return False
    ref.update({"active": active})
    return True


def delete_announcement(announcement_id: str) -> bool:
    ref = announcements_ref().document(announcement_id)
    if not ref.get().exists:
        return False
    ref.delete()
    return True
