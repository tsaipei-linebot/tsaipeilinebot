from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import PlainTextResponse

from delivery import repository
from delivery.auth import current_user, login_required
from delivery.config import MAX_UPLOAD_BYTES, VENDOR_MAP
from delivery.csv_import import parse_personnel_csv
from delivery.templating import templates

router = APIRouter()

TEMPLATE_CSV = (
    "廠商,姓名,身分證字號,電話,到職日期\n"
    "蝦皮,王小明,A123456789,0912345678,2024-01-31\n"
)


@router.get("/import")
def import_form(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "import_form.html", {"user": current_user(request), "result": None})


@router.get("/import/template.csv")
def import_template(redirect=Depends(login_required)):
    if redirect:
        return redirect
    # 存成 UTF-8 但不加 BOM 的話，Windows 版 Excel 直接雙擊開啟時會用系統的
    # 中文編碼（Big5/cp950）去猜，猜錯就整個表頭跟範例資料變亂碼——所以這裡
    # 要編碼成 utf-8-sig（開頭多幾個看不到的位元組，Excel 看到這個才會知道
    # 這份檔案是 UTF-8）。上傳解析那邊（delivery/csv_import.py 的 _decode）
    # 本來就有處理 utf-8-sig，所以這裡加了 BOM 不會影響「下載範本填完再
    # 上傳」這個流程。
    return PlainTextResponse(
        TEMPLATE_CSV.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=personnel_template.csv"},
    )


@router.post("/import")
async def import_submit(request: Request, file: UploadFile = File(...), redirect=Depends(login_required)):
    if redirect:
        return redirect
    user = current_user(request)

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        result = {"header_error": "檔案超過 10MB 上限", "created": [], "skipped": [], "failed": []}
        return templates.TemplateResponse(request, "import_form.html", {"user": user, "result": result})

    rows, header_error = parse_personnel_csv(content)
    result = {"header_error": header_error, "created": [], "skipped": [], "failed": []}

    if not header_error:
        for row in rows:
            if not row["ok"]:
                result["failed"].append(row)
                continue

            existing = repository.find_active_personnel_by_name_and_phone(row["name"], row["phone"])
            if existing:
                existing_vendor = VENDOR_MAP.get(existing.get("vendor"), existing.get("vendor"))
                result["skipped"].append(
                    {**row, "reason": f"姓名+手機號碼已存在（{existing_vendor} - {existing.get('name')}）"}
                )
                continue

            repository.create_personnel(
                row["name"], row["id_number"], row["phone"], row["vendor"], user["username"],
                hire_date=row.get("hire_date", ""),
            )
            result["created"].append({**row, "vendor_name": VENDOR_MAP.get(row["vendor"], row["vendor"])})

    return templates.TemplateResponse(request, "import_form.html", {"user": user, "result": result})
