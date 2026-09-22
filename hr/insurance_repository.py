"""每日加退保彙總（2026-09-22 新增，/hr/insurance）：7 個部門（見
`hr.config.INSURANCE_UPLOAD_DEPARTMENTS`）每天上傳加退保 Excel，人資
（部門字串 = `hr.config.INSURANCE_COLLECTOR_DEPARTMENT`）在「每日加退
彙總」彙整、收單、下載成一份總表。跟使用者討論確認的規劃全程記錄在
HANDOFF.md，這裡只記重點：

**權限判斷不是走 hr 模組本身的 admin/staff 兩層**（見 hr/auth.py），而是
比照 `services/contract_summary_service.py` 的作法，直接看帳號的
`department` 字串——是 7 個上傳部門之一就能上傳/查自己歷史，是「人資
部門」（或全平台管理員）就能看全部/收單/下載，兩者互斥但底層都只是
`department` 字串比對，不需要另外設計角色欄位。

**上傳只存檔案本身，不解析內容進 Firestore**（跟 hr/repository.py 其他
彙整功能同一種做法）——Firestore 只記錄「哪個部門、哪一天、上傳了哪個
檔案」，下載彙總表時才即時打開每個部門的檔案讀取內容組表（見
`hr/insurance_excel.py`），這樣即使之後彙總表格式要調整，也不用重新
處理已經存好的歷史資料。

**同一個部門、同一天重複上傳＝直接覆蓋，不比對 Excel 內容裡的資料列**
（2026-09-22 使用者明確選擇的簡化做法）——Firestore 文件 id 直接用
「日期__部門」組成固定 key（`_upload_doc_id()`），重新上傳就是覆寫同一筆
文件。舊檔案在 GCS 上會變成孤兒（不主動刪除，避免刪除時機跟另一個
請求同時讀取那個檔案衝突）——孤兒檔案不影響功能，之後如果真的要清理
再另外處理，不是這次範圍。

**收單只鎖住 7 個部門，人資自己不受限**——收單只是把「這一天」的
`hr_insurance_day_locks` 文件標成 `closed: True`，`can_upload_for_date()`
只擋 `can_upload()` 那 7 個部門的帳號，不擋 `is_collector()`，人資收單後
仍可以補上傳/覆蓋任一部門那天的資料。

**這次範圍不包含** UC加退保／蝦皮假日班加退保／材霈_離店與實習通報／
E-learning 這幾種檔案（2026-09-22 使用者確認之後如果需要再另外處理）。
"""
import time

from hr.config import INSURANCE_COLLECTOR_DEPARTMENT, INSURANCE_UPLOAD_DEPARTMENTS
from hr.db import insurance_day_locks_ref, insurance_uploads_ref


def can_upload(account: dict) -> bool:
    return (account.get("department") or "") in INSURANCE_UPLOAD_DEPARTMENTS


def is_collector(account: dict) -> bool:
    if account.get("is_platform_admin"):
        return True
    return (account.get("department") or "") == INSURANCE_COLLECTOR_DEPARTMENT


def has_insurance_access(account: dict) -> bool:
    return can_upload(account) or is_collector(account)


def _upload_doc_id(department: str, work_date: str) -> str:
    return f"{work_date}__{department}"


def get_upload(department: str, work_date: str):
    snapshot = insurance_uploads_ref().document(_upload_doc_id(department, work_date)).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def save_upload(
    department: str, work_date: str, blob_path: str, filename: str,
    uploaded_by: str, uploaded_by_name: str,
) -> None:
    """新增或覆蓋「這個部門、這一天」的上傳紀錄——文件 id 是固定的
    「日期__部門」組合，所以同一天重傳就是覆寫同一筆，不會累積多筆。"""
    ref = insurance_uploads_ref().document(_upload_doc_id(department, work_date))
    ref.set({
        "department": department,
        "work_date": work_date,
        "blob_path": blob_path,
        "filename": filename,
        "uploaded_by": uploaded_by,
        "uploaded_by_name": uploaded_by_name,
        "uploaded_at": time.time(),
    })


def list_department_history(department: str) -> list:
    """這個部門自己上傳過的紀錄，依日期新到舊排序——部門同仁的「查詢
    歷史」用。"""
    result = [
        {**(s.to_dict() or {}), "id": s.id}
        for s in insurance_uploads_ref().where("department", "==", department).stream()
    ]
    result.sort(key=lambda r: r.get("work_date", ""), reverse=True)
    return result


def list_all_history(start_date: str = "", end_date: str = "") -> list:
    """人資看全部部門的歷史紀錄，依日期新到舊、同日期依部門排序；
    `start_date`/`end_date` 有值的話只回傳區間內（含頭尾）的紀錄。也是
    下載彙總表（`hr/insurance_excel.py`）挑選要合併哪些檔案用的依據。"""
    result = [{**(s.to_dict() or {}), "id": s.id} for s in insurance_uploads_ref().stream()]
    if start_date:
        result = [r for r in result if r.get("work_date", "") >= start_date]
    if end_date:
        result = [r for r in result if r.get("work_date", "") <= end_date]
    result.sort(key=lambda r: (r.get("work_date", ""), r.get("department", "")), reverse=True)
    return result


def is_day_closed(work_date: str) -> bool:
    snapshot = insurance_day_locks_ref().document(work_date).get()
    if not snapshot.exists:
        return False
    return bool((snapshot.to_dict() or {}).get("closed"))


def can_upload_for_date(account: dict, work_date: str) -> bool:
    """這個帳號現在能不能傳「這一天」的資料——7 個部門的帳號在收單後
    不能再傳當天的，人資／全平台管理員不受收單影響。"""
    if is_collector(account):
        return True
    if not can_upload(account):
        return False
    return not is_day_closed(work_date)


def close_day(work_date: str, closed_by: str, closed_by_name: str) -> None:
    insurance_day_locks_ref().document(work_date).set({
        "work_date": work_date,
        "closed": True,
        "closed_by": closed_by,
        "closed_by_name": closed_by_name,
        "closed_at": time.time(),
    })


def summary_for_date(work_date: str) -> list:
    """人資「每日加退彙總」頁用：7 個部門這一天各自傳了沒，依
    `INSURANCE_UPLOAD_DEPARTMENTS` 固定順序（跟 `/departments` 主檔清單
    一致）列出，每個部門帶出它今天的上傳紀錄（沒傳過是 None）。"""
    return [
        {"department": department, "upload": get_upload(department, work_date)}
        for department in INSURANCE_UPLOAD_DEPARTMENTS
    ]
