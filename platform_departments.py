"""部門主檔（/departments）：跟 `platform_companies.py`（材霈旗下派遣公司
牌照主檔）同一種做法，是全平台共用的基礎資料，只有全平台管理員（老闆
本人）能維護，2026-09-12 新增。

**帳號的 `department` 欄位（`platform_accounts.py`）存的是這裡某一筆的
「名稱」文字，不是這裡的文件 id**——之所以用名稱字串而不是外鍵 id，是因為
`chicken_points_routes.py` 送出自費申請時會直接把 `account["department"]`
這個字串存進申請紀錄本身（給會計對帳用），維持這個既有行為最單純的做法
就是讓 `department` 全程都是同一份字串，不用另外多一層 id 對應名稱的解析。
**這代表如果之後在這裡把某個部門改名，已經設定成那個部門的帳號不會自動
跟著變**——需要另外到 `/accounts` 把那些帳號的部門重新選一次，這是刻意
的簡化取捨，換取跟既有 `chicken_points` 行為完全相容、不用動它的程式碼。

排序用 `sort_index`（新增時自動排在最後面），畫面上維護的是「部門管理」
這個清單本身的顯示順序，不是各部門底下的人員順序（那是 `/accounts` 自己
一組獨立的 `reorder_department()`，兩者無關）。
"""
from platform_db import departments_ref


def _to_department(department_id: str, data: dict) -> dict:
    return {
        "id": department_id,
        "name": data.get("name", "") or "",
        "sort_index": data.get("sort_index", 0) or 0,
    }


def list_departments() -> list:
    result = [_to_department(s.id, s.to_dict() or {}) for s in departments_ref().stream()]
    result.sort(key=lambda d: (d["sort_index"], d["name"]))
    return result


def list_department_names() -> list:
    """給帳號表單的部門下拉選單用，只要名稱字串，依畫面上的顯示順序。"""
    return [d["name"] for d in list_departments()]


def get_department(department_id: str):
    snapshot = departments_ref().document(department_id).get()
    if not snapshot.exists:
        return None
    return _to_department(department_id, snapshot.to_dict() or {})


def department_name_exists(name: str) -> bool:
    name = (name or "").strip()
    return any(d["name"] == name for d in list_departments())


def create_department(name: str) -> str:
    """新增一筆部門，排在目前清單最後面。回傳新文件的 id。"""
    existing = list_departments()
    next_index = (max((d["sort_index"] for d in existing), default=-1)) + 1
    ref = departments_ref().document()
    ref.set({"name": (name or "").strip(), "sort_index": next_index})
    return ref.id


def update_department_name(department_id: str, name: str):
    departments_ref().document(department_id).update({"name": (name or "").strip()})


def delete_department(department_id: str):
    departments_ref().document(department_id).delete()


def reorder_departments(ordered_ids: list) -> None:
    """儲存「部門管理」頁面拖曳排序後的結果：`ordered_ids` 是畫面上排好
    的新順序（部門文件 id），只會更新真的存在的部門，其餘忽略——跟
    `platform_accounts.reorder_department()` 同一種不完全信任前端輸入的
    寫法。"""
    valid_ids = {d["id"] for d in list_departments()}
    from platform_db import get_db

    batch = get_db().batch()
    index = 0
    for department_id in ordered_ids:
        if department_id not in valid_ids:
            continue
        batch.update(departments_ref().document(department_id), {"sort_index": index})
        index += 1
    batch.commit()


def validate_department_name(name: str, *, editing_id: str = "") -> str:
    """回傳空字串代表可以存；非空字串是不能存的錯誤訊息，直接顯示在
    畫面上。`editing_id` 是正在編輯的這筆自己的 id，檢查重複名稱時要
    排除自己，不然編輯一筆沒改名字的資料也會被自己擋下來。"""
    name = (name or "").strip()
    if not name:
        return "部門名稱不能空白。"
    for d in list_departments():
        if d["name"] == name and d["id"] != editing_id:
            return "已經有一個同名的部門了，請換一個名稱。"
    return ""


def count_accounts_using_department(name: str) -> int:
    """有多少帳號目前設定成這個部門——刪除部門前用來擋下「還有人在用」的
    情況，避免刪掉之後這些帳號的部門變成一個清單裡找不到的孤兒值。"""
    import platform_accounts

    return sum(1 for a in platform_accounts.list_accounts() if a["department"] == name)
