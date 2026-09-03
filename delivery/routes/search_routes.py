from fastapi import APIRouter, Depends, Request

from delivery import repository
from delivery.auth import current_user, login_required
from delivery.config import VENDOR_MAP
from delivery.templating import templates

router = APIRouter()


@router.get("/search")
def search_page(request: Request, keyword: str = "", redirect=Depends(login_required)):
    if redirect:
        return redirect
    results = []
    if keyword.strip():
        results = [
            {
                "person": p,
                "missing": repository.missing_documents(p.get("documents")),
                "vendor_name": VENDOR_MAP.get(p.get("vendor"), p.get("vendor")),
            }
            for p in repository.search_personnel(keyword)
        ]
    return templates.TemplateResponse(
        request,
        "search.html",
        {"user": current_user(request), "keyword": keyword, "results": results},
    )
