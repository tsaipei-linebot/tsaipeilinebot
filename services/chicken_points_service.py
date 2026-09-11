"""小雞點數自費申請（/chicken-points）：全新的內部功能，**完全不經過**職缺
維護表單背後那支外部 GAS 系統——材霈平台自己收表單、自己存資料，沒有審核
流程（原本紙本單子上的「主任簽名」「副理確認」「會計確認」三欄，使用者
2026-09-11 明確要求只留「本人簽名」，其餘拿掉），送出後直接把表單內容跟
簽名合成的一張圖存進材霈平台自己的 Firestore，會計自己登入 `/chicken-points`
查看清單即可，不寄信、不用 LINE 通知任何人。

跟 job_listing_submit_service.py／project_contract_submit_service.py 這些
「方案 A」服務不一樣：那些是轉送資料給外部 GAS 處理；這裡完全是材霈平台
自己的邏輯，跟 platform_db.py／hr/db.py 的 Firestore 存取寫法一致。

表單+簽名合成一張圖這件事是在**瀏覽器端**用 `<canvas>` 完成（見
templates/chicken_points_form.html 的 composeSignedImage()），送到這裡的
`signed_image_base64` 已經是最終合成好的 PNG（data URL），這裡只負責存檔，
不再另外用 Pillow 之類的套件在伺服器端合成一次——避免多加一個套件相依，
瀏覽器端 canvas 就能做到一樣的效果。
"""
from datetime import datetime, timezone

from platform_db import get_db

REQUESTS_COLLECTION = "chicken_point_requests"

# 申請部門選單，沿用職缺維護「負責所別」的既有所別清單（同一套組織架構），
# 見 services/job_listing_submit_service.py 的 BRANCH_OPTIONS——這裡故意
# 另外複製一份而不是直接 import 共用，因為這是兩個獨立模組各自的選項
# 清單，語意不同（一個是「負責所別」、一個是「申請部門」），沒有規定兩邊
# 一定要永遠同步異動。
DEPARTMENT_OPTIONS = ["台北所(派遣組)", "新北所(派遣組)", "桃園所", "台中所", "高雄所", "新北所(配送組)"]

# 點數換算金額的固定比例（元/點），使用者 2026-09-11 確認過的數字
# （範例：3250 元 = 5000 點）。
POINT_RATE = 0.65


def requests_ref():
    return get_db().collection(REQUESTS_COLLECTION)


def compute_amount(points: int) -> int:
    """點數換算成應扣金額，四捨五入到整數元（沒有角分的必要）。"""
    return round(points * POINT_RATE)


def save_request(
    *,
    applicant_username: str,
    applicant_name: str,
    department: str,
    purchase_month: str,
    points: int,
    signed_image_base64: str,
) -> dict:
    data = {
        "applicant_username": applicant_username,
        "applicant_name": applicant_name,
        "department": department,
        "purchase_month": purchase_month,
        "points": points,
        "amount": compute_amount(points),
        "signed_image_base64": signed_image_base64,
        "created_at": datetime.now(timezone.utc),
    }
    doc_ref = requests_ref().document()
    doc_ref.set(data)
    data["id"] = doc_ref.id
    return data


def _doc_to_dict(doc) -> dict:
    data = doc.to_dict() or {}
    data["id"] = doc.id
    return data


def list_all_requests() -> list:
    """給有「主管」角色的帳號（例如會計）看全部同仁的申請紀錄，依送出時間
    新到舊排序。"""
    docs = requests_ref().order_by("created_at", direction="DESCENDING").stream()
    return [_doc_to_dict(d) for d in docs]


def list_requests_by_username(username: str) -> list:
    """給「專員」角色的帳號看自己送出過的申請紀錄——查詢條件只用單一
    where，不额外加 order_by，避免需要額外建立 Firestore 複合索引；
    排序改成拿到資料後在 Python 端做。"""
    docs = requests_ref().where("applicant_username", "==", username).stream()
    results = [_doc_to_dict(d) for d in docs]
    results.sort(key=lambda r: r.get("created_at") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return results
