"""職缺維護（/job-listings）：新增/維護 Notion 職缺資料庫裡的職缺，跟
`/delivery`、`/hr` 等部門模組一樣，是否看得到這張卡片、能不能進來，由
`/accounts` 的權限設定決定（模組代碼 `job_listings`）。

跟 `/companies`、`/vendors`（只有全平台管理員能用）不同，這裡任何有
`job_listings` 模組權限的帳號（不分「專員」/「主管」角色，兩者體驗完全
一樣）都能進來——實際上「能不能改某一筆職缺」是由 GAS 那邊依「原刊登人
本人或其直屬主管」的邏輯逐筆判斷，不是這裡的模組權限在管，見
services/job_listing_submit_service.py 開頭的說明。

送出表單、查詢既有職缺清單，都是轉手給職缺維護表單背後那支 GAS 程式處理
（跟薪資補款送出表單同一套「方案 A」做法），這裡不重做 AI 文案潤飾、
Notion 讀寫、LINE 推播審核這些邏輯。
"""
import base64

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse

import platform_accounts
from platform_templating import templates
from services.job_listing_submit_service import (
    BRANCH_OPTIONS,
    CATEGORY_OPTIONS,
    FOREIGN_STUDENT_OPTIONS,
    INDUSTRY_OPTIONS,
    JOB_CYCLE_OPTIONS,
    JOB_TYPE_OPTIONS,
    LEAVE_TYPE_OPTIONS,
    PAY_METHOD_OPTIONS,
    SHIFT_OPTIONS,
    TAIWAN_CITY_DISTRICTS,
    build_submit_payload,
    compute_subordinate_names,
    fetch_maintainable_jobs,
    submit_job,
)

router = APIRouter()

MODULE_CODE = "job_listings"

# 跟薪資補款佐證照片同一個上限（20MB），避免同仁誤傳超大原始檔案時，
# 整包 base64 JSON 太大讓請求逾時或被 GAS 那邊拒絕。
_MAX_IMAGE_BYTES = 20 * 1024 * 1024

_MULTI_SELECT_FIELD_NAMES = [
    "industry", "category", "job_type", "foreign_student", "job_cycle",
    "city", "district", "branch", "shift", "leave_type", "pay_method",
]

# 原本這裡只照抄現有 Netlify 表單「畫面標 * 但其實沒真的擋」的行為，只有
# industry/category/job_cycle/city/district 五個欄位會真的擋。使用者
# 2026-09-09 明確要求：除了「備註說明」跟「職缺圖檔上傳」，其餘欄位都要
# 改成真的必填、沒填不能送出——這是使用者主動要求的行為變更，不是本系統
# 自己加的簡化，所以這裡改成涵蓋全部多選欄位。
_REQUIRED_MULTI_SELECT_FIELDS = list(_MULTI_SELECT_FIELD_NAMES)

_TEXT_FIELD_NAMES = [
    "vendor", "title", "internal_title", "external_title", "salary",
    "interview_method", "internal_desc", "external_desc", "notes",
]

# 現有表單裡這幾個文字欄位有 required 屬性（見 index_6.html 的
# jf_vendor/jf_title/... 這幾個 input/textarea），一樣照抄。
_REQUIRED_TEXT_FIELD_NAMES = [
    "vendor", "title", "internal_title", "external_title", "salary",
    "interview_method", "internal_desc", "external_desc",
]


def _require_access(request: Request):
    account = platform_accounts.current_account(request)
    if not account:
        return RedirectResponse(url=f"/login?next=/{MODULE_CODE.replace('_', '-')}", status_code=303)
    if not platform_accounts.has_module_access(account, MODULE_CODE):
        return RedirectResponse(url="/portal", status_code=303)
    return None


def _empty_form_values() -> dict:
    """GET 表單首次載入時要用的預設值——多選欄位一定要給空陣列，不能讓
    樣板拿到完全沒有這個 key 的字典，不然 Jinja2 對 Undefined 值做
    `opt in form.industry` 這種判斷會直接噴例外，把整頁弄壞。"""
    return {name: [] for name in _MULTI_SELECT_FIELD_NAMES}


def _dropdown_options_context() -> dict:
    return {
        "industry_options": INDUSTRY_OPTIONS,
        "category_options": CATEGORY_OPTIONS,
        "job_type_options": JOB_TYPE_OPTIONS,
        "foreign_student_options": FOREIGN_STUDENT_OPTIONS,
        "job_cycle_options": JOB_CYCLE_OPTIONS,
        "branch_options": BRANCH_OPTIONS,
        "shift_options": SHIFT_OPTIONS,
        "leave_type_options": LEAVE_TYPE_OPTIONS,
        "pay_method_options": PAY_METHOD_OPTIONS,
        "taiwan_cities": list(TAIWAN_CITY_DISTRICTS.keys()),
        "taiwan_city_districts": TAIWAN_CITY_DISTRICTS,
    }


@router.get("/job-listings")
def job_listing_form(
    request: Request, submitted: str = "", submit_unknown: str = "", redirect=Depends(_require_access)
):
    if redirect:
        return redirect
    context = {
        "user": platform_accounts.current_account(request),
        "error": "",
        "form": _empty_form_values(),
        "submitted": submitted,
        "submit_unknown": submit_unknown,
    }
    context.update(_dropdown_options_context())
    return templates.TemplateResponse(request, "job_listing_form.html", context)


@router.get("/job-listings/api/jobs")
def job_listing_search_api(request: Request, redirect=Depends(_require_access)):
    """給表單頁面的 JS 呼叫，回傳這個帳號可以維護的既有職缺清單（JSON），
    用來做「維護既有職缺」的搜尋建議跟選取後帶入表單欄位——資料直接來自
    GAS，材霈平台這邊不快取、不額外處理。"""
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    subordinates = compute_subordinate_names(account["username"], platform_accounts.list_accounts())
    jobs = fetch_maintainable_jobs(account["name"], subordinates)
    return JSONResponse({"jobs": jobs})


@router.post("/job-listings")
async def job_listing_submit(
    request: Request,
    mode: str = Form("create"),
    page_id: str = Form(""),
    update_action: str = Form("create"),
    existing_image_url: str = Form(""),
    image: UploadFile = File(None),
    redirect=Depends(_require_access),
):
    if redirect:
        return redirect
    account = platform_accounts.current_account(request)
    form = await request.form()

    text_values = {name: (form.get(name, "") or "").strip() for name in _TEXT_FIELD_NAMES}
    multi_values = {name: form.getlist(name) for name in _MULTI_SELECT_FIELD_NAMES}

    form_values = {
        **text_values,
        **multi_values,
        "mode": mode,
        "page_id": page_id,
        "update_action": update_action,
        "existing_image_url": existing_image_url,
    }

    if mode == "update" and not page_id:
        context = {"user": account, "error": "請先搜尋並選取一筆要維護的職缺。", "form": form_values}
        context.update(_dropdown_options_context())
        return templates.TemplateResponse(request, "job_listing_form.html", context, status_code=400)

    missing_text = [name for name in _REQUIRED_TEXT_FIELD_NAMES if not text_values[name]]
    missing_multi = [name for name in _REQUIRED_MULTI_SELECT_FIELDS if not multi_values[name]]
    if missing_text or missing_multi:
        context = {
            "user": account,
            "error": "還有必填欄位沒有填寫，請檢查表單上標示 * 的欄位。",
            "form": form_values,
        }
        context.update(_dropdown_options_context())
        return templates.TemplateResponse(request, "job_listing_form.html", context, status_code=400)

    image_base64, image_filename = "", ""
    if image is not None and image.filename:
        content = await image.read()
        if len(content) > _MAX_IMAGE_BYTES:
            context = {"user": account, "error": "圖檔超過 20MB 上限，請換一張檔案較小的圖片。", "form": form_values}
            context.update(_dropdown_options_context())
            return templates.TemplateResponse(request, "job_listing_form.html", context, status_code=400)
        image_base64 = base64.b64encode(content).decode("ascii")
        image_filename = image.filename

    payload = build_submit_payload(
        applicant_name=account["name"],
        mode=mode,
        page_id=page_id,
        update_action=update_action,
        vendor=text_values["vendor"],
        title=text_values["title"],
        internal_title=text_values["internal_title"],
        external_title=text_values["external_title"],
        salary=text_values["salary"],
        interview_method=text_values["interview_method"],
        internal_desc=text_values["internal_desc"],
        external_desc=text_values["external_desc"],
        notes=text_values["notes"],
        existing_image_url=existing_image_url,
        industry=multi_values["industry"],
        category=multi_values["category"],
        job_type=multi_values["job_type"],
        foreign_student=multi_values["foreign_student"],
        job_cycle=multi_values["job_cycle"],
        city=multi_values["city"],
        district=multi_values["district"],
        branch=multi_values["branch"],
        shift=multi_values["shift"],
        leave_type=multi_values["leave_type"],
        pay_method=multi_values["pay_method"],
        image_base64=image_base64,
        image_filename=image_filename,
    )
    result = submit_job(payload)

    if result.get("status") == "success":
        return RedirectResponse(url="/job-listings?submitted=1", status_code=303)

    if result.get("status") == "unknown":
        return RedirectResponse(url="/job-listings?submit_unknown=1", status_code=303)

    context = {"user": account, "error": result.get("message") or "送出失敗，請稍後再試。", "form": form_values}
    context.update(_dropdown_options_context())
    return templates.TemplateResponse(request, "job_listing_form.html", context, status_code=400)
