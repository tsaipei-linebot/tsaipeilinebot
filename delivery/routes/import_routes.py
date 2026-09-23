from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import Response

from delivery import repository
from delivery.auth import current_user, login_required
from delivery.config import MAX_UPLOAD_BYTES, VENDOR_MAP
from delivery.csv_import import parse_personnel_csv
from delivery.templating import templates
from services import tabular_upload

router = APIRouter()

# 範本 2026-09-23 從 CSV 改成 Excel：同仁用 Excel 另存成 CSV 時 Windows 會
# 用 Big5 存檔，Big5 放不下的姓名用字（堃、喆、峯…）會被 Excel 直接換成
# 「?」寫進檔案，救不回來。完整說明見 services/tabular_upload.py 開頭。
TEMPLATE_HEADERS = ["廠商", "姓名", "身分證字號", "電話", "到職日期"]
TEMPLATE_SAMPLE_ROW = ["蝦皮三輪", "王小明", "A123456789", "0912345678", "2024-01-31"]
# 電話要設成文字格式，不然 Excel 會當成數字、開頭的 0 直接不見。
TEMPLATE_TEXT_COLUMNS = ("身分證字號", "電話")


@router.get("/import")
def import_form(request: Request, redirect=Depends(login_required)):
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "import_form.html", {"user": current_user(request), "result": None})


@router.get("/import/template.xlsx")
def import_template(redirect=Depends(login_required)):
    if redirect:
        return redirect
    return Response(
        tabular_upload.build_template_xlsx(TEMPLATE_HEADERS, TEMPLATE_SAMPLE_ROW, TEMPLATE_TEXT_COLUMNS),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=personnel_template.xlsx"},
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
