"""台北所專區的「廠商維護」「班別維護」（2026-09-25 新增，見 HANDOFF.md「台北所(派遣組)／台北所(國際組)
專區」）。

待進人員的廠商、班別只能從這裡建好的選項下拉選，不讓同仁自由打字。每個部門各自一份（派遣組、國際組
互相看不到），只有主管（帳號職級副主任以上）能維護。

- 選項**不能改名、不能刪除**，只能停用：已經登記的待進人員存的是選項名稱，改名或刪掉會讓舊資料對不上。
  要改名就新增一個、把舊的停用。停用的不會出現在下拉選單，舊資料照樣顯示。
- 廠商可以選「對應主頁廠商」（`/vendors` 廠商管理的客戶名稱，可留空）：主頁廠商是以客戶為單位
  （「蝦皮」），這裡是客戶＋地點＋計薪＋身分（「蝦皮(台南)(時薪)-外籍」）。先存對應，之後可以用來
  自動帶出彙總表的「投保單位」、依客戶統計（還沒做）。存名稱不存 id，因為主頁廠商管理同一個客戶
  常有好幾筆（合約產生器每次送出都新增一筆）。
"""
import time
import uuid

import platform_accounts
from hr.db import insurance_options_ref

TYPE_VENDOR = "vendor"
TYPE_SHIFT = "shift"
TYPE_NAMES = {TYPE_VENDOR: "廠商", TYPE_SHIFT: "班別"}


def _with_id(snapshot) -> dict:
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def list_options(department: str, option_type: str, include_inactive: bool = True) -> list:
    options = [
        _with_id(s)
        for s in insurance_options_ref().where("department", "==", department).stream()
    ]
    options = [o for o in options if o.get("type") == option_type and (include_inactive or o.get("active", True))]
    options.sort(key=lambda o: (not o.get("active", True), o.get("name", "")))
    return options


def active_names(department: str, option_type: str) -> list:
    return [o["name"] for o in list_options(department, option_type, include_inactive=False)]


def get_option(option_id: str):
    snapshot = insurance_options_ref().document(option_id).get()
    return _with_id(snapshot) if snapshot.exists else None


def add_option(department: str, option_type: str, name: str, actor: dict, platform_vendor_name: str = "") -> str:
    """新增選項，回傳錯誤訊息（空字串＝成功）。同一個部門、同一種類不能重複名稱（停用的也算）。"""
    name = (name or "").strip()
    if option_type not in TYPE_NAMES:
        return "種類不正確。"
    if not name:
        return f"請填{TYPE_NAMES[option_type]}名稱。"
    if any(o.get("name") == name for o in list_options(department, option_type)):
        return f"「{name}」已經有了（如果是停用的，請直接重新啟用）。"
    insurance_options_ref().document(uuid.uuid4().hex).set({
        "department": department,
        "type": option_type,
        "name": name,
        "active": True,
        "platform_vendor_name": (platform_vendor_name or "").strip() if option_type == TYPE_VENDOR else "",
        "created_at": time.time(),
        "created_by": actor.get("username", ""),
        "created_by_name": actor.get("name", ""),
    })
    return ""


def set_active(option_id: str, department: str, active: bool) -> bool:
    option = get_option(option_id)
    if not option or option.get("department") != department:
        return False
    insurance_options_ref().document(option_id).set({"active": bool(active), "updated_at": time.time()}, merge=True)
    return True


def set_platform_vendor(option_id: str, department: str, platform_vendor_name: str) -> bool:
    option = get_option(option_id)
    if not option or option.get("department") != department or option.get("type") != TYPE_VENDOR:
        return False
    insurance_options_ref().document(option_id).set(
        {"platform_vendor_name": (platform_vendor_name or "").strip(), "updated_at": time.time()}, merge=True
    )
    return True


def platform_vendor_choices(department: str) -> list:
    """主頁「廠商管理」裡「服務部門」有勾這個部門的客戶名稱（同名只列一次）。"""
    import platform_vendors

    target = platform_accounts.normalize_department(department)
    names = {
        (v.get("name") or "").strip()
        for v in platform_vendors.list_vendors()
        if target in {platform_accounts.normalize_department(d) for d in v.get("service_departments") or []}
    }
    return sorted(n for n in names if n)
