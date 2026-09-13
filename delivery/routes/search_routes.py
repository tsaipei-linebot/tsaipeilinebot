from fastapi import APIRouter, Depends, Request

from delivery import repository
from delivery.auth import current_user, login_required
from delivery.config import (
    PERSONNEL_STATUS_BADGE_CLASS,
    PERSONNEL_STATUS_MAP,
    PERSONNEL_STATUSES,
    VENDOR_MAP,
    VENDORS,
)
from delivery.templating import templates

router = APIRouter()


@router.get("/search")
def search_page(
    request: Request,
    keyword: str = "",
    vendor: str = "",
    status: str = "",
    redirect=Depends(login_required),
):
    if redirect:
        return redirect
    keyword = keyword.strip()
    vendor = vendor if vendor in VENDOR_MAP else ""
    status = status if status in PERSONNEL_STATUS_MAP else ""
    results = []
    # 姓名/身分證字號、廠商，只要有一個有給值就查——這是「查詢人員」跟
    # 「人員狀況」（/delivery/vendor/{廠商}）的分工：那邊預設隱藏缺件
    # 齊全跟離職/放棄報到的人，這裡選了廠商就是要看到完整名單，兩者都
    # 沒給就不查（不然等於一次撈出全公司所有人）。狀態單獨給、沒搭配
    # 關鍵字或廠商的話目前不會觸發查詢，只在已經有結果時用來篩選。
    if keyword or vendor:
        for p in repository.search_personnel(keyword, vendor=vendor, employment_status=status):
            employment_status = repository.personnel_employment_status(p)
            results.append(
                {
                    "person": p,
                    "missing": repository.missing_documents(p),
                    "vendor_name": VENDOR_MAP.get(p.get("vendor"), p.get("vendor")),
                    "employment_status_name": PERSONNEL_STATUS_MAP.get(employment_status, ""),
                    "employment_status_badge_class": PERSONNEL_STATUS_BADGE_CLASS.get(
                        employment_status, "badge-pending"
                    ),
                }
            )
    return templates.TemplateResponse(
        request,
        "search.html",
        {
            "user": current_user(request),
            "keyword": keyword,
            "filter_vendor": vendor,
            "filter_status": status,
            "vendors": VENDORS,
            "personnel_statuses": PERSONNEL_STATUSES,
            "results": results,
        },
    )
