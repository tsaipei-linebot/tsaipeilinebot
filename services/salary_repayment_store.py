"""薪資補款搬離 GAS 階段 2 的第 1 步（2026-09-25）：把試算表資料原樣搬進 Firestore。

使用者 2026-09-25 決定匯入方式是 **(a) 原樣照搬**：試算表每一格顯示什麼就存什麼（Sheets API
預設的「格式化後文字」），不修、不轉型別、不擋資料；空白或重複的補款單號也照搬，只在同步結果裡
列出來給使用者知道。

- `salary_repayments`：「薪資補款紀錄」分頁，一列一份文件。文件 id 用補款單號；單號空白、含 `/`
  或重複的列改用 `row-{列號}`（重複的第一筆仍用單號），確保每一列都有地方放、一列都不少。
  文件內容：`fields`（表頭 → 儲存格文字，原樣）、`row_number`（試算表第幾列，讀取時照這個
  排序，跟試算表順序一樣）、`source`（`"sheet"`＝從試算表同步來的）、`synced_at`。
- `salary_repayment_org`：「員工主管組織表」分頁，同樣原樣照搬，只用來把「核准主管」的 LINE ID
  換成姓名（跟讀試算表時一樣）。文件 id 一律 `row-{列號}`。
- `salary_repayment_meta/state`：讀取來源開關（`read_source`：`sheet`／`firestore`）、最後一次
  同步的時間／人／結果、兩個分頁的表頭順序。

**同步＝讓平台資料跟試算表一模一樣**：新的列新增、內容變了的更新、試算表已經沒有的刪掉（GAS 退回
補款單時會直接刪試算表那一列）。只刪 `source == "sheet"` 的文件——之後第 4 步平台自己收的補款單
不會被同步刪掉。第 4 步上線、平台接手寫資料之後，這個同步就要停用（到時候在那個 PR 處理）。

讀取來源預設是試算表；開關切到「平台資料」之後，`/me`、`/finance` 改讀這裡，隨時可以切回去。
"""
from datetime import datetime, timezone

from platform_db import get_db

RECORDS_COLLECTION = "salary_repayments"
ORG_COLLECTION = "salary_repayment_org"
META_COLLECTION = "salary_repayment_meta"
META_DOC = "state"

SOURCE_SHEET = "sheet"
SOURCE_FIRESTORE = "firestore"
SOURCE_NAMES = {SOURCE_SHEET: "Google 試算表", SOURCE_FIRESTORE: "平台資料"}

ID_COLUMN = "補款單號"
_BATCH_LIMIT = 400  # Firestore 一個 batch 最多 500 個操作，留一點餘裕


def records_ref():
    return get_db().collection(RECORDS_COLLECTION)


def org_ref():
    return get_db().collection(ORG_COLLECTION)


def meta_ref():
    return get_db().collection(META_COLLECTION).document(META_DOC)


def get_state() -> dict:
    snapshot = meta_ref().get()
    data = snapshot.to_dict() if snapshot.exists else None
    return data if isinstance(data, dict) else {}


def read_source() -> str:
    """讀不到設定（或 Firestore 出問題）一律當成試算表——維持原本的行為。"""
    try:
        value = get_state().get("read_source")
    except Exception:
        return SOURCE_SHEET
    return SOURCE_FIRESTORE if value == SOURCE_FIRESTORE else SOURCE_SHEET


def set_read_source(source: str, actor: dict) -> None:
    if source not in SOURCE_NAMES:
        raise ValueError(source)
    meta_ref().set(
        {
            "read_source": source,
            "read_source_changed_at": datetime.now(timezone.utc),
            "read_source_changed_by": actor.get("name") or actor.get("username") or "",
        },
        merge=True,
    )


def _rows(values: list) -> tuple:
    """回傳 (headers, [(列號, {表頭: 文字}), ...])。整列空白的跳過（試算表範圍尾端常有空列）。"""
    if not values:
        return [], []
    # 表頭空白的欄 Firestore 存不了（欄位名稱不能是空字串），改叫「（第 N 欄）」
    headers = [str(h) if str(h).strip() else f"（第 {i} 欄）" for i, h in enumerate(values[0], start=1)]
    rows = []
    for index, row in enumerate(values[1:], start=2):
        cells = [str(c) for c in row] + [""] * (len(headers) - len(row))
        if not any(c.strip() for c in cells):
            continue
        rows.append((index, dict(zip(headers, cells[: len(headers)]))))
    return headers, rows


def assign_record_ids(rows: list) -> tuple:
    """回傳 ([(doc_id, 列號, fields), ...], 空白單號的列號, 重複單號 [{"id": 單號, "rows": [列號...]}])。
    重複單號的「rows」只列第二筆之後（第一筆照樣用單號當文件 id）。"""
    used, blank_rows, duplicates, assigned = set(), [], {}, []
    for row_number, fields in rows:
        salary_id = (fields.get(ID_COLUMN) or "").strip()
        # Firestore 文件 id 不能有 /、不能是 . 或 ..、不能是 __xxx__；row- 開頭留給沒辦法用單號的列
        usable = (
            salary_id
            and "/" not in salary_id
            and salary_id not in (".", "..")
            and not (salary_id.startswith("__") and salary_id.endswith("__"))
            and not salary_id.startswith("row-")
            and len(salary_id.encode("utf-8")) <= 1500
        )
        if not salary_id:
            blank_rows.append(row_number)
        if usable and salary_id not in used:
            doc_id = salary_id
        else:
            doc_id = f"row-{row_number}"
            if salary_id and salary_id in used:
                duplicates.setdefault(salary_id, []).append(row_number)
        used.add(doc_id)
        assigned.append((doc_id, row_number, fields))
    return assigned, blank_rows, [{"id": k, "rows": v} for k, v in duplicates.items()]


def _existing(collection) -> dict:
    return {snap.id: snap.to_dict() or {} for snap in collection.stream()}


def _write(collection, sets: dict, deletes: list) -> None:
    db = get_db()
    ops = [(doc_id, data) for doc_id, data in sets.items()] + [(doc_id, None) for doc_id in deletes]
    for start in range(0, len(ops), _BATCH_LIMIT):
        batch = db.batch()
        for doc_id, data in ops[start : start + _BATCH_LIMIT]:
            ref = collection.document(doc_id)
            if data is None:
                batch.delete(ref)
            else:
                batch.set(ref, data)
        batch.commit()


def _sync_collection(collection, assigned: list, now) -> dict:
    existing = _existing(collection)
    sets, counts = {}, {"added": 0, "updated": 0, "unchanged": 0, "deleted": 0}
    for doc_id, row_number, fields in assigned:
        old = existing.get(doc_id)
        if old and old.get("fields") == fields and old.get("row_number") == row_number:
            counts["unchanged"] += 1
            continue
        counts["updated" if old else "added"] += 1
        sets[doc_id] = {"fields": fields, "row_number": row_number, "source": SOURCE_SHEET, "synced_at": now}
    keep = {doc_id for doc_id, _, _ in assigned}
    deletes = [doc_id for doc_id, data in existing.items() if doc_id not in keep and data.get("source") == SOURCE_SHEET]
    counts["deleted"] = len(deletes)
    _write(collection, sets, deletes)
    counts["total"] = len(assigned)
    return counts


def sync_from_sheet(org_values: list, record_values: list, actor: dict) -> dict:
    """把兩個分頁的原始值（Sheets API values，含表頭列）同步進 Firestore，回傳同步結果。"""
    now = datetime.now(timezone.utc)
    record_headers, record_rows = _rows(record_values)
    org_headers, org_rows = _rows(org_values)
    assigned, blank_rows, duplicates = assign_record_ids(record_rows)
    result = {
        "records": _sync_collection(records_ref(), assigned, now),
        "org": _sync_collection(org_ref(), [(f"row-{n}", n, f) for n, f in org_rows], now),
        "blank_id_rows": blank_rows,
        "duplicate_ids": duplicates,
    }
    meta_ref().set(
        {
            "last_synced_at": now,
            "last_synced_by": actor.get("name") or actor.get("username") or "",
            "last_result": result,
            "record_headers": record_headers,
            "org_headers": org_headers,
        },
        merge=True,
    )
    return result


def _load(collection) -> list:
    docs = sorted((snap.to_dict() or {} for snap in collection.stream()), key=lambda d: d.get("row_number") or 0)
    return [dict(d.get("fields") or {}) for d in docs]


def load_rows() -> tuple:
    """回傳 (org_rows, record_rows, error)，格式跟讀試算表的 `_fetch_sheet_rows()` 一樣。"""
    try:
        return _load(org_ref()), _load(records_ref()), None
    except Exception as e:
        return [], [], f"讀取平台的薪資補款資料時發生錯誤：{e}"
