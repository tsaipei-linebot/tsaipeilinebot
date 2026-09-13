"""帳號權限管理（/accounts）：只有全平台管理員（老闆本人）看得到，可以幫
每組帳號指派橫跨各部門模組的權限，取代掉舊版配送部系統自己的「帳號管理」
頁面——那個只能設定單一模組的角色，多模組之後不夠用。

刻意不放進 delivery/ 或 management/ 底下：這是跨模組的東西，不屬於任何一個
部門，掛在根 app（main.py）上，用跟其他模組共用的同一顆 session cookie。

**2026-09-12 改版**：模組欄位從「每個模組各自選不開放/專員/主管」簡化成
單純勾選「開放/不開放」，模組裡算不算管理權限改由這個帳號的 `rank`
（公司職級）決定，不再是新增/編輯帳號時逐一指定——詳見 `platform_
accounts.py` 開頭的說明。同時「部門」改成從 `platform_departments.py`
（/departments 網頁維護）挑選、新增「職級」欄位，並且選了部門後「所屬
主管」會自動預帶這個部門裡目前職級副主任（含）以上的帳號（見
`_department_managers()`），你還是可以手動調整，不是強制套用。
"""
import itertools

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

import platform_accounts
import platform_departments
from platform_accounts import MODULE_ROLE_MAP, MODULES, RANKS
from platform_templating import templates

router = APIRouter()


def _modules_from_form(form_data) -> list:
    return [m["code"] for m in MODULES if form_data.get(f"module_{m['code']}")]


def _manager_usernames_from_form(form_data) -> list:
    return [u for u in form_data.getlist("manager_usernames") if u]


def _department_from_form(form_data) -> str:
    return (form_data.get("department") or "").strip()


def _rank_from_form(form_data) -> str:
    return (form_data.get("rank") or "").strip()


def _department_managers(all_accounts: list, exclude_username: str = None) -> dict:
    """回傳 {部門名稱: [職級副主任以上的帳號 username, ...]}，給新增/編輯
    帳號表單的 JS 用——選了部門之後自動預帶這個部門目前的主管人選，同一
    個單位不會有兩個同職級的人，所以這份清單裡的人選幾乎都是明確的（見
    HANDOFF.md／規格討論），編輯帳號時排除自己，避免自己被列為自己部門
    的主管候選人。"""
    result = {}
    for a in all_accounts:
        if a["username"] == exclude_username:
            continue
        if not a["department"] or not platform_accounts.is_manager_rank(a.get("rank", "")):
            continue
        result.setdefault(a["department"], []).append(a["username"])
    return result


def _account_form_context(request: Request, account: dict, error: str) -> dict:
    """新增/編輯帳號表單共用的 context：「所屬主管」下拉選單要列出除了自己
    以外的所有帳號可以選（可以選多個），編輯自己時排除自己，避免選到自己
    當自己的主管。"""
    all_accounts = platform_accounts.list_accounts()
    current_username = account["username"] if account else None
    manager_options = [a for a in all_accounts if a["username"] != current_username]
    return {
        "user": platform_accounts.current_account(request),
        "account": account,
        "modules": MODULES,
        "ranks": RANKS,
        "departments": platform_departments.list_departments(),
        "manager_options": manager_options,
        "department_managers": _department_managers(all_accounts, exclude_username=current_username),
        "error": error,
    }


@router.get("/")
def accounts_list(request: Request, error: str = "", redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    accounts = platform_accounts.list_accounts()
    name_by_username = {a["username"]: a["name"] for a in accounts}
    for a in accounts:
        a["manager_names"] = [name_by_username.get(u, u) for u in a["manager_usernames"]]
        # 每個模組現在只存「開放了哪些模組」，模組裡算不算管理權限
        # （"admin"/"staff"）改由職級算出來，所以這裡另外算一份給列表頁
        # 畫徽章用，不是直接讀 a["modules"] 的值（那已經只是一份代碼
        # 清單，不再帶角色資訊）。
        a["module_roles"] = {code: platform_accounts.module_role(a, code) for code in a["modules"]}
    # list_accounts() 已經照部門排好序，這裡用 groupby 直接切成
    # [(部門, [帳號, ...]), ...] 給樣板畫部門標題列——同一部門內的順序
    # （拖曳排過的在前、其餘依姓名排在後）原封不動照 list_accounts() 給的
    # 順序，這裡不重新排序。
    grouped_accounts = [(dept, list(group)) for dept, group in itertools.groupby(accounts, key=lambda a: a["department"])]
    return templates.TemplateResponse(
        request,
        "accounts_list.html",
        {
            "user": platform_accounts.current_account(request),
            "grouped_accounts": grouped_accounts,
            "modules": MODULES,
            "rank_map": platform_accounts.RANK_MAP,
            "role_map": MODULE_ROLE_MAP,
            "error": error,
        },
    )


@router.post("/reorder")
async def reorder_accounts(request: Request, redirect=Depends(platform_accounts.require_platform_admin)):
    """帳號權限管理頁面拖曳同部門帳號順序後，前端用背景請求呼叫這支端點
    存檔——見 templates/accounts_list.html 的拖曳互動邏輯。"""
    if redirect:
        return redirect
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    department = (payload.get("department") or "").strip()
    usernames = payload.get("usernames")
    if not department or not isinstance(usernames, list):
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    platform_accounts.reorder_department(department, usernames)
    return JSONResponse({"status": "ok"})


@router.get("/new")
def new_account_form(request: Request, redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "account_form.html", _account_form_context(request, None, ""))


@router.post("/new")
async def create_account_submit(request: Request, redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    form = await request.form()
    username = (form.get("username") or "").strip()
    password = form.get("password") or ""
    name = (form.get("name") or "").strip()
    modules = _modules_from_form(form)
    manager_usernames = _manager_usernames_from_form(form)
    department = _department_from_form(form)
    rank = _rank_from_form(form)

    error = ""
    if not username or not password or not name:
        error = "帳號、密碼、姓名都要填。"
    elif not platform_departments.department_name_exists(department):
        error = "請選擇部門（如果清單裡沒有，先到「部門管理」新增）。"
    elif rank not in platform_accounts.RANK_ORDER:
        error = "請選擇職級。"
    elif platform_accounts.account_exists(username):
        error = "這個帳號已經存在，請換一個帳號名稱。"

    if error:
        return templates.TemplateResponse(
            request, "account_form.html", _account_form_context(request, None, error), status_code=400,
        )

    platform_accounts.create_account(
        username, password, name, modules,
        manager_usernames=manager_usernames, department=department, rank=rank,
    )
    return RedirectResponse(url="/accounts", status_code=303)


@router.get("/{username}/edit")
def edit_account_form(username: str, request: Request, redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    account = platform_accounts.get_account(username)
    if not account:
        return RedirectResponse(url="/accounts?error=not_found", status_code=303)
    return templates.TemplateResponse(request, "account_form.html", _account_form_context(request, account, ""))


@router.post("/{username}/edit")
async def edit_account_submit(username: str, request: Request, redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    account = platform_accounts.get_account(username)
    if not account:
        return RedirectResponse(url="/accounts?error=not_found", status_code=303)

    form = await request.form()
    name = (form.get("name") or "").strip()
    password = form.get("password") or ""
    modules = _modules_from_form(form)
    manager_usernames = _manager_usernames_from_form(form)
    department = _department_from_form(form)
    rank = _rank_from_form(form)

    error = ""
    if not name:
        error = "姓名不能空白。"
    elif not platform_departments.department_name_exists(department):
        error = "請選擇部門（如果清單裡沒有，先到「部門管理」新增）。"
    elif rank not in platform_accounts.RANK_ORDER:
        error = "請選擇職級。"
    if error:
        return templates.TemplateResponse(
            request, "account_form.html", _account_form_context(request, account, error), status_code=400,
        )

    platform_accounts.update_account(
        username, name, modules, password=password,
        manager_usernames=manager_usernames, department=department, rank=rank,
    )
    return RedirectResponse(url="/accounts", status_code=303)


@router.post("/{username}/impersonate")
def impersonate_account(username: str, request: Request, redirect=Depends(platform_accounts.require_platform_admin)):
    """全平台管理員切換視角看某個帳號看到的畫面（2026-09-13 新增）：把目前
    這組管理員帳號存進 session 的 `impersonator`，`user` 換成目標帳號，
    之後所有權限判斷都照舊只看 `session["user"]`，不用改任何其他程式碼。
    只有 `require_platform_admin` 擋得住這支路由，而切換視角後
    `current_account()` 回傳的就是目標帳號，不會再是管理員本人，所以
    「連續切換到別人視角底下的視角」這件事本來就進不了這支路由，不需要
    另外擋巢狀切換。目標帳號如果本身也是全平台管理員則不給切換（雖然
    目前只有一組管理員帳號用不到，但避免以後多組管理員互相切換）。"""
    if redirect:
        return redirect
    target = platform_accounts.get_account(username)
    if not target:
        return RedirectResponse(url="/accounts?error=not_found", status_code=303)
    if target.get("is_platform_admin"):
        return RedirectResponse(url="/accounts?error=cannot_impersonate_admin", status_code=303)
    request.session["impersonator"] = platform_accounts.current_account(request)
    request.session["user"] = target
    return RedirectResponse(url="/portal", status_code=303)


@router.post("/{username}/delete")
def delete_account_submit(username: str, request: Request, redirect=Depends(platform_accounts.require_platform_admin)):
    if redirect:
        return redirect
    target = platform_accounts.get_account(username)
    if not target:
        return RedirectResponse(url="/accounts?error=not_found", status_code=303)

    current = platform_accounts.current_account(request)
    error = platform_accounts.validate_account_deletion(
        username, current["username"], target["is_platform_admin"]
    )
    if error:
        return RedirectResponse(url=f"/accounts?error={error}", status_code=303)

    platform_accounts.delete_account(username)
    return RedirectResponse(url="/accounts", status_code=303)
