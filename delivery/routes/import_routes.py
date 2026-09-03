from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import PlainTextResponse

from delivery import repository
from delivery.auth import current_user, login_required
from delivery.config import MAX_UPLOAD_BYTES, VENDOR_MAP
from delivery.csv_import import parse_personnel_csv
from delivery.templating import templates

router = APIRouter()

TEMPLATE_CSV = "廠商,姓名,身分證字號,電話\n蝦皮,王小明,A123456789,0912345678\n"


@router.get("/import")
def import_form(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "import_form.html", {"user": current_user(request), "result": None})


@router.get("/import/template.csv")
def import_template(redirect=Depends(login_required)):
    if redirect:
        return redirect
    return PlainTextResponse(
        TEMPLATE_CSV,
        media_type="text/csv",
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

            existing = repository.find_active_personnel_by_id_number(row["id_number"]) if row["id_number"] else None
            if existing:
                existing_vendor = VENDOR_MAP.get(existing.get("vendor"), existing.get("vendor"))
                result["skipped"].append(
                    {**row, "reason": f"身分證字號已存在（{existing_vendor} - {existing.get('name')}）"}
                )
                continue

            repository.create_personnel(row["name"], row["id_number"], row["phone"], row["vendor"], user["username"])
            result["created"].append({**row, "vendor_name": VENDOR_MAP.get(row["vendor"], row["vendor"])})

    return templates.TemplateResponse(request, "import_form.html", {"user": user, "result": result})
