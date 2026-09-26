"""少凱業務開發專區（/salesdev）。

2026-09-24 改版（見 HANDOFF.md「業務開發整併」）：資料來源從 Google 試算表
改成 Firestore（`salesdev/repository.py`），職缺由平台自己每天抓
（`salesdev/pipeline.py`），同地點的職缺歸成一組（`salesdev/normalize.py`）。

- `/salesdev`：三個分頁——開發名單（一組一列）、非客戶線索（派遣公司徵
  自己內部員工的職缺）、新登記工廠；上方顯示最近一次自動抓取的結果
- `/salesdev/groups/{id}`：一組的詳細頁，看組裡每一筆職缺、填反查結果、
  備註、聯絡紀錄
- `/salesdev/export.xlsx`：下載 Excel
- `/salesdev/import-sheet`：一次性匯入舊試算表（模組管理員限定）
- `/salesdev?tab=taiwanjobs`：台灣就業通（2026-09-26 新增，每小時自動抓，見
  `salesdev/taiwanjobs_pipeline.py`；使用者要求跟其他來源分開，不合併）；
  `/salesdev/taiwanjobs/settings` 改關鍵字/郵遞區號、`/salesdev/taiwanjobs/refresh`
  立刻重抓職缺清單（管理員）
- `/salesdev/taiwanjobs/companies/{id}`：台灣就業通一間公司的寄信＋手動編輯 Email/電話
  （2026-09-26，寄信是開 Gmail 撰寫畫面，見 salesdev/mail_templates.py）；
  `/salesdev/templates`：信件範本專區（內文範本的新增、編輯、刪除，每個範本可上傳 PDF）；
  `/salesdev/gmail/*`：連結 gary@tsaipei.com 的 Gmail（OAuth），寄信頁可以直接建好含 PDF 的草稿
- `/salesdev?tab=hiring`：104 產線徵才公司（2026-09-25 新增，每週自動抓，見
  `salesdev/hiring_pipeline.py`）；`/salesdev/hiring/settings` 改搜尋條件（管理員）

跟 delivery/management/hr 不同，這個模組直接掛在根 app 上、複用同一顆
登入 session cookie（比照 portal_routes.py／accounts_routes.py 的做法）。
2026-09-26 起**只有全平台管理員（胡少凱本人）能進來**，/accounts 不能再勾選開放
給其他帳號（見 platform_accounts.PLATFORM_ADMIN_ONLY_MODULES）。
"""
import hmac
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

import platform_accounts
from platform_templating import templates
from config import SALESDEV_GMAIL_ACCOUNT
from salesdev import gmail_drafts, mail_templates, repository
from salesdev import taiwanjobs_repository as tj_repo
from salesdev.excel_export import SOURCE_LABELS, build_workbook
from salesdev.sheet_import import import_from_sheet

router = APIRouter()

MODULE_CODE = "salesdev"
TABS = ("leads", "internal", "hiring", "taiwanjobs", "factories")
TJ_EMAIL_FILTERS = ("yes", "no", "all")
TJ_SENT_FILTERS = ("all", "unsent", "sent")
HIRING_SIZE_FILTERS = ("big", "unknown", "small", "all")

STATUS_BADGES = {
    repository.STATUS_PENDING: "badge-pending",
    repository.STATUS_SELECTED: "badge-selected",
    repository.STATUS_DONE: "badge-ok",
    repository.STATUS_NOT_FOUND: "badge-resigned",
    repository.STATUS_SKIPPED: "badge-resigned",
}


def _require_access(request: Request):
    account = platform_accounts.current_account(request)
    if not account:
        return RedirectResponse(url=f"/login?next=/{MODULE_CODE}", status_code=303)
    if not platform_accounts.has_module_access(account, MODULE_CODE):
        return RedirectResponse(url="/portal", status_code=303)
    return None


def _username(request: Request) -> str:
    account = platform_accounts.current_account(request) or {}
    return account.get("name") or account.get("username") or ""


def _is_module_admin(request: Request) -> bool:
    account = platform_accounts.current_account(request)
    return platform_accounts.module_role(account, MODULE_CODE) == platform_accounts.ROLE_ADMIN


def _redirect(url: str, msg: str = "", err: str = "") -> RedirectResponse:
    sep = "&" if "?" in url else "?"
    if msg:
        url += f"{sep}msg={quote(msg)}"
    elif err:
        url += f"{sep}err={quote(err)}"
    return RedirectResponse(url=url, status_code=303)


def _common_context(request: Request) -> dict:
    return {
        "user": platform_accounts.current_account(request),
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "status_badges": STATUS_BADGES,
        "source_labels": SOURCE_LABELS,
    }


@router.get("/salesdev")
def salesdev_home(
    request: Request,
    tab: str = "leads",
    status: str = "",
    size: str = "big",
    email: str = "yes",
    dispatch: str = "show",
    sent: str = "all",
    redirect=Depends(_require_access),
):
    if redirect:
        return redirect
    tab = tab if tab in TABS else "leads"
    context = _common_context(request)
    context.update(
        {
            "tab": tab,
            "status_filter": status if status in repository.REVIEW_STATUSES else "",
            "review_statuses": repository.REVIEW_STATUSES,
            "latest_run": None,
            "status_counts": {},
            "groups": [],
            "internal_jobs": [],
            "factories": [],
            "hiring_companies": [],
            "hiring_counts": {},
            "hiring_size": size if size in HIRING_SIZE_FILTERS else "big",
            "hiring_settings": repository.default_hiring_settings(),
            "latest_hiring_run": None,
            "tj_companies": [],
            "tj_counts": {},
            "tj_email": email if email in TJ_EMAIL_FILTERS else "yes",
            "tj_hide_dispatch": dispatch == "hide",
            "tj_settings": tj_repo.default_settings(),
            "tj_run": None,
            "tj_job_counts": {},
            "tj_email_source": tj_repo.email_source,
            "tj_sent": sent if sent in TJ_SENT_FILTERS else "all",
            "tj_emails_of": tj_repo.effective_emails,
            "tj_phones_of": tj_repo.effective_phones,
            "load_error": "",
            "is_module_admin": _is_module_admin(request),
        }
    )
    try:
        context["latest_run"] = repository.latest_run()
        if tab == "leads":
            groups = repository.list_groups()
            context["status_counts"] = {
                s: sum(1 for g in groups if g.get("review_status") == s) for s in repository.REVIEW_STATUSES
            }
            if context["status_filter"]:
                groups = [g for g in groups if g.get("review_status") == context["status_filter"]]
            context["groups"] = groups
        elif tab == "internal":
            context["internal_jobs"] = repository.list_internal_jobs()
        elif tab == "hiring":
            settings = repository.get_hiring_settings()
            companies = repository.list_hiring_companies()
            buckets = [(c, repository.hiring_size_bucket(c, settings["min_employees"])) for c in companies]
            context["hiring_counts"] = {
                key: sum(1 for _, b in buckets if b == key) for key in ("big", "unknown", "small")
            }
            context["hiring_counts"]["all"] = len(companies)
            size_filter = context["hiring_size"]
            context["hiring_companies"] = [c for c, b in buckets if size_filter == "all" or b == size_filter]
            context["hiring_settings"] = settings
            context["latest_hiring_run"] = repository.latest_hiring_run()
        elif tab == "taiwanjobs":
            companies = tj_repo.list_companies()
            if context["tj_hide_dispatch"]:
                companies = [c for c in companies if not c.get("is_dispatch")]
            if context["tj_sent"] == "sent":
                companies = [c for c in companies if c.get("last_sent_date")]
            elif context["tj_sent"] == "unsent":
                companies = [c for c in companies if not c.get("last_sent_date")]
            with_email = [c for c in companies if tj_repo.effective_emails(c)]
            context["tj_counts"] = {"yes": len(with_email), "no": len(companies) - len(with_email), "all": len(companies)}
            if context["tj_email"] == "yes":
                companies = with_email
            elif context["tj_email"] == "no":
                companies = [c for c in companies if not tj_repo.effective_emails(c)]
            context["tj_companies"] = companies
            context["tj_settings"] = tj_repo.get_settings()
            context["tj_run"] = tj_repo.latest_run()
            context["tj_job_counts"] = tj_repo.count_jobs_by_status()
        else:
            context["factories"] = repository.list_factories()
    except Exception as exc:
        print(f"[業務開發] 讀取資料失敗：{exc}")
        context["load_error"] = f"讀取資料時發生錯誤：{exc}"
    return templates.TemplateResponse(request, "salesdev_home.html", context)


@router.get("/salesdev/help")
def salesdev_help(request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "salesdev_help.html", {"user": platform_accounts.current_account(request)})


@router.post("/salesdev/select")
async def salesdev_select(request: Request, redirect=Depends(_require_access)):
    """勾選「待審查」的組送出 →「已勾選待反查」。勾選框數量不固定，所以
    直接讀 request.form() 的同名多值。"""
    if redirect:
        return redirect
    form = await request.form()
    group_ids = [g for g in form.getlist("group_ids") if g]
    if not group_ids:
        return _redirect("/salesdev", err="請至少勾選一個地點再送出。")
    count = repository.select_groups_for_lookup(group_ids, _username(request))
    return _redirect("/salesdev", msg=f"已送出 {count} 個地點，狀態改成「已勾選待反查」。")


@router.get("/salesdev/groups/{group_id}")
def salesdev_group_detail(request: Request, group_id: str, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    group = repository.get_group(group_id)
    if not group:
        return _redirect("/salesdev", err="找不到這個地點，可能已經被重新整理過，請回列表重新點選。")
    context = _common_context(request)
    context.update(
        {
            "group": group,
            "jobs": repository.list_jobs_in_group(group_id),
            "review_statuses": repository.REVIEW_STATUSES,
        }
    )
    return templates.TemplateResponse(request, "salesdev_group.html", context)


@router.post("/salesdev/groups/{group_id}/review")
async def salesdev_group_review(request: Request, group_id: str, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    form = await request.form()
    lookup = {field: str(form.get(field, "")) for field in repository.LOOKUP_FIELDS}
    ok = repository.update_group_review(group_id, str(form.get("review_status", "")), lookup, _username(request))
    url = f"/salesdev/groups/{group_id}"
    return _redirect(url, msg="已儲存反查結果。") if ok else _redirect(url, err="儲存失敗：狀態不正確或找不到這個地點。")


@router.post("/salesdev/groups/{group_id}/note")
async def salesdev_group_note(request: Request, group_id: str, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    form = await request.form()
    ok = repository.update_group_note(group_id, str(form.get("note", "")), _username(request))
    url = f"/salesdev/groups/{group_id}"
    return _redirect(url, msg="已儲存備註。") if ok else _redirect(url, err="儲存失敗：找不到這個地點。")


@router.post("/salesdev/groups/{group_id}/contact-log")
async def salesdev_group_contact_log(request: Request, group_id: str, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    form = await request.form()
    ok = repository.add_contact_log(group_id, str(form.get("text", "")), _username(request))
    url = f"/salesdev/groups/{group_id}"
    return _redirect(url, msg="已新增聯絡紀錄。") if ok else _redirect(url, err="請先輸入聯絡內容再送出。")


@router.post("/salesdev/jobs/{job_id}/internal")
async def salesdev_job_internal(request: Request, job_id: str, redirect=Depends(_require_access)):
    """人工修正「是不是派遣公司內部職缺」。value=1 移到非客戶線索、
    value=0 放回開發名單。"""
    if redirect:
        return redirect
    form = await request.form()
    internal = str(form.get("value", "")) == "1"
    back = str(form.get("back", ""))
    job = repository.get_job(job_id)
    if not job:
        return _redirect("/salesdev?tab=internal", err="找不到這筆職缺。")
    original_group = job.get("group_id", "")
    new_group = repository.set_job_internal(job_id, internal, _username(request))
    if internal:
        url = f"/salesdev/groups/{original_group}" if back == "group" and original_group else "/salesdev?tab=internal"
        return _redirect(url, msg="已移到「非客戶線索」。")
    return _redirect(f"/salesdev/groups/{new_group}" if new_group else "/salesdev", msg="已放回開發名單。")


@router.get("/salesdev/export.xlsx")
def salesdev_export(request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    content = build_workbook(
        repository.list_groups(),
        repository.list_all_jobs(),
        repository.list_factories(),
        repository.list_hiring_companies(),
        tj_repo.list_companies(),
    )
    filename = f"業務開發名單_{repository.today_str()}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.post("/salesdev/import-sheet")
def salesdev_import_sheet(request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    if not _is_module_admin(request):
        return _redirect("/salesdev", err="只有這個專區的管理員可以匯入舊試算表資料。")
    stats = import_from_sheet(_username(request))
    if stats["error"]:
        return _redirect("/salesdev", err=stats["error"])
    return _redirect(
        "/salesdev",
        msg=(
            f"匯入完成：試算表職缺 {stats['jobs_read']} 筆（新增 {stats['new_jobs']}、已存在更新 {stats['updated_jobs']}），"
            f"歸成新地點 {stats['new_groups']} 個、非客戶線索 {stats['internal_jobs']} 筆、沿用已勾選 {stats['selected_groups']} 個；"
            f"新登記工廠 {stats['factories_read']} 筆（新增 {stats['new_factories']}）。"
        ),
    )


_KEYWORD_SPLIT_RE = re.compile(r"[\s,，、;；]+")


@router.post("/salesdev/hiring/settings")
async def salesdev_hiring_settings(request: Request, redirect=Depends(_require_access)):
    """104 產線徵才公司的搜尋條件（管理員限定）。下一次每週自動抓取才會套用。"""
    if redirect:
        return redirect
    back = "/salesdev?tab=hiring"
    if not _is_module_admin(request):
        return _redirect(back, err="只有這個專區的管理員可以修改搜尋條件。")
    form = await request.form()
    keywords = []
    for keyword in _KEYWORD_SPLIT_RE.split(str(form.get("keywords", ""))):
        if keyword and keyword not in keywords:
            keywords.append(keyword)
    try:
        min_employees = int(str(form.get("min_employees", "")).strip())
        max_pages = int(str(form.get("max_pages", "")).strip())
    except ValueError:
        return _redirect(back, err="員工人數門檻、每個關鍵字抓幾頁都要填數字。")
    if not keywords:
        return _redirect(back, err="請至少填一個搜尋關鍵字。")
    if len(keywords) > 10:
        return _redirect(back, err="關鍵字最多 10 個（每個關鍵字都要花時間搜尋，太多會來不及在時間內抓完）。")
    if min_employees < 1 or not 1 <= max_pages <= 10:
        return _redirect(back, err="員工人數門檻要大於 0；每個關鍵字抓幾頁要在 1～10 之間。")
    repository.save_hiring_settings(keywords, min_employees, max_pages, _username(request))
    return _redirect(back, msg="已儲存搜尋條件，下一次每週自動抓取會照新的條件搜尋。")


_ZIP_SPLIT_RE = re.compile(r"[^0-9]+")


@router.post("/salesdev/taiwanjobs/settings")
async def salesdev_taiwanjobs_settings(request: Request, redirect=Depends(_require_access)):
    """台灣就業通的關鍵字、郵遞區號（管理員限定）。下一次抓職缺清單才會套用。"""
    if redirect:
        return redirect
    back = "/salesdev?tab=taiwanjobs"
    if not _is_module_admin(request):
        return _redirect(back, err="只有這個專區的管理員可以修改搜尋條件。")
    form = await request.form()
    keywords = []
    for keyword in _KEYWORD_SPLIT_RE.split(str(form.get("keywords", ""))):
        if keyword and keyword not in keywords:
            keywords.append(keyword)
    zipcodes = []
    for zipno in _ZIP_SPLIT_RE.split(str(form.get("zipcodes", ""))):
        if len(zipno) == 3 and zipno not in zipcodes:
            zipcodes.append(zipno)
    if not keywords:
        return _redirect(back, err="請至少填一個關鍵字。")
    if not zipcodes:
        return _redirect(back, err="請至少填一個 3 碼郵遞區號。")
    if len(zipcodes) > 150:
        return _redirect(back, err="郵遞區號最多 150 個。")
    tj_repo.save_settings(keywords, zipcodes, _username(request))
    return _redirect(back, msg="已儲存搜尋條件，下一次抓職缺清單（約每天一次）會照新的條件。")


@router.post("/salesdev/taiwanjobs/refresh")
def salesdev_taiwanjobs_refresh(request: Request, redirect=Depends(_require_access)):
    """管理員：下一次每小時執行就重抓職缺清單，不用等一天。"""
    if redirect:
        return redirect
    back = "/salesdev?tab=taiwanjobs"
    if not _is_module_admin(request):
        return _redirect(back, err="只有這個專區的管理員可以重抓職缺清單。")
    tj_repo.request_listing_refresh()
    return _redirect(back, msg="好的，下一次每小時自動執行時會重抓職缺清單。")


# ---------------------------------------------------------------------------
# 台灣就業通：一間公司的寄信＋手動編輯（2026-09-26）
# ---------------------------------------------------------------------------

def _pick(templates_list: list, wanted_id: str):
    for template in templates_list:
        if template["id"] == wanted_id:
            return template
    return templates_list[0] if templates_list else None


@router.get("/salesdev/taiwanjobs/companies/{company_id}")
def salesdev_tj_company(request: Request, company_id: str, t: str = "", redirect=Depends(_require_access)):
    if redirect:
        return redirect
    company = tj_repo.get_company(company_id)
    if not company:
        return _redirect("/salesdev?tab=taiwanjobs", err="找不到這間公司。")
    mail_templates.ensure_seeded(_username(request))
    bodies = mail_templates.list_templates()
    # 沒指定就用這個瀏覽器上次選的（存在 cookie），再沒有就用第一個
    body = _pick(bodies, t or request.cookies.get("salesdev_tpl_body", ""))
    rendered = mail_templates.render(body, company)
    last_sent = tj_repo.last_sent_by_email(company)
    emails = [
        {
            "email": email,
            "last_sent": last_sent.get(email.lower(), ""),
            "recent": tj_repo.sent_recently(last_sent.get(email.lower(), "")),
            "source": tj_repo.email_source(company, email),
            "manual": email.lower() in [m.lower() for m in company.get("manual_emails") or []],
        }
        for email in tj_repo.effective_emails(company)
    ]
    context = _common_context(request)
    context.update(
        {
            "company": company,
            "emails": emails,
            "hidden_emails": company.get("hidden_emails") or [],
            "phones": tj_repo.effective_phones(company),
            "manual_phones": company.get("manual_phones") or [],
            "hidden_phones": company.get("hidden_phones") or [],
            "bodies": bodies,
            "body": body,
            "rendered": rendered,
            "gmail_account": SALESDEV_GMAIL_ACCOUNT,
            "gmail": gmail_drafts.connection_status(),
            "resend_days": tj_repo.RESEND_WARNING_DAYS,
            "send_log": list(reversed(company.get("send_log") or [])),
        }
    )
    response = templates.TemplateResponse(request, "salesdev_tj_company.html", context)
    if body:
        response.set_cookie("salesdev_tpl_body", body["id"], max_age=180 * 86400, httponly=True, samesite="lax")
    return response


@router.post("/salesdev/taiwanjobs/companies/{company_id}/contacts")
async def salesdev_tj_company_contacts(request: Request, company_id: str, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    form = await request.form()
    kind, action = str(form.get("kind", "")), str(form.get("action", ""))
    error = tj_repo.update_contact(company_id, kind, action, str(form.get("value", "")))
    if str(form.get("back", "")) == "list":
        # 從列表「沒有 Email」那一列直接填的（使用者要求：存好後自動跳到「有 Email」、停在這間公司）
        dispatch = "&dispatch=hide" if str(form.get("dispatch", "")) == "hide" else ""
        if error:
            return RedirectResponse(
                url=f"/salesdev?tab=taiwanjobs&email=no{dispatch}&err={quote(error)}#c-{company_id}", status_code=303
            )
        company = tj_repo.get_company(company_id) or {}
        name = company.get("company_name", "")
        return RedirectResponse(
            url=f"/salesdev?tab=taiwanjobs&email=yes{dispatch}&msg={quote(f'已儲存，{name} 已經移到「有 Email」。')}#c-{company_id}",
            status_code=303,
        )
    back = f"/salesdev/taiwanjobs/companies/{company_id}"
    return _redirect(back, err=error) if error else _redirect(back, msg="已更新。")


@router.post("/salesdev/taiwanjobs/companies/{company_id}/sent")
async def salesdev_tj_company_sent(request: Request, company_id: str, redirect=Depends(_require_access)):
    """寄信畫面按「開啟 Gmail」時，瀏覽器在背景呼叫這支記下寄送紀錄（JSON）。"""
    if redirect:
        return JSONResponse({"ok": False, "error": "請重新登入。"}, status_code=403)
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "資料格式不正確。"}, status_code=400)
    company = tj_repo.get_company(company_id)
    email = str(payload.get("email", "")).strip()
    if not company or email.lower() not in [e.lower() for e in tj_repo.effective_emails(company)]:
        return JSONResponse({"ok": False, "error": "找不到這間公司或這個信箱。"}, status_code=400)
    body = mail_templates.get_template(str(payload.get("template_id", "")))
    entry = tj_repo.record_send(company_id, email, _username(request), (body or {}).get("name", ""))
    return JSONResponse({"ok": True, "date": entry["date"]})


@router.post("/salesdev/taiwanjobs/companies/{company_id}/draft")
async def salesdev_tj_company_draft(request: Request, company_id: str, redirect=Depends(_require_access)):
    """做法二：在 gary@tsaipei.com 的 Gmail 建一封草稿（含範本的 PDF），回傳打開草稿的網址。
    建好才記寄送紀錄。畫面上的主旨/內文（使用者可能小改過）由瀏覽器一起送來。"""
    if redirect:
        return JSONResponse({"ok": False, "error": "請重新登入。"}, status_code=403)
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "資料格式不正確。"}, status_code=400)
    company = tj_repo.get_company(company_id)
    email = str(payload.get("email", "")).strip()
    if not company or email.lower() not in [e.lower() for e in tj_repo.effective_emails(company)]:
        return JSONResponse({"ok": False, "error": "找不到這間公司或這個信箱。"}, status_code=400)
    body = mail_templates.get_template(str(payload.get("template_id", "")))
    subject = str(payload.get("subject", "")).strip()
    content = str(payload.get("content", ""))
    if not body or not subject or not content.strip():
        return JSONResponse({"ok": False, "error": "範本、主旨、內文都要有。"}, status_code=400)
    attachment = None
    if body.get("attachment_blob"):
        try:
            data = gmail_drafts.download_attachment(body["attachment_blob"])
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"讀不到範本的 PDF：{exc}"}, status_code=500)
        if not data:
            return JSONResponse({"ok": False, "error": "範本的 PDF 不見了，請到「信件範本」重新上傳。"}, status_code=400)
        attachment = (body.get("attachment_name") or "材霈公司簡介.pdf", data)
    try:
        draft = gmail_drafts.create_draft(email, subject, content, attachment)
    except gmail_drafts.GmailNotConnected as exc:
        return JSONResponse({"ok": False, "error": str(exc), "need_connect": True}, status_code=400)
    except Exception as exc:
        print(f"[業務開發寄信] 建立 Gmail 草稿失敗：{exc}")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)
    entry = tj_repo.record_send(company_id, email, _username(request), f"{body.get('name', '')}（草稿）")
    return JSONResponse({"ok": True, "date": entry["date"], "url": draft["url"]})


# ---------------------------------------------------------------------------
# 信件範本專區（2026-09-26）：內文範本的新增、編輯、刪除
# ---------------------------------------------------------------------------

@router.get("/salesdev/templates")
def salesdev_templates(request: Request, edit: str = "", redirect=Depends(_require_access)):
    if redirect:
        return redirect
    mail_templates.ensure_seeded(_username(request))
    context = _common_context(request)
    context.update(
        {
            "bodies": mail_templates.list_templates(),
            "editing": mail_templates.get_template(edit),
            "placeholders": mail_templates.PLACEHOLDERS,
            "gmail": gmail_drafts.connection_status(),
            "gmail_account": SALESDEV_GMAIL_ACCOUNT,
            "gmail_redirect_uri": _gmail_redirect_uri(request),
            "max_attachment_mb": gmail_drafts.MAX_ATTACHMENT_BYTES // (1024 * 1024),
        }
    )
    return templates.TemplateResponse(request, "salesdev_templates.html", context)


@router.post("/salesdev/templates/save")
async def salesdev_templates_save(request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    form = await request.form()
    template_id = str(form.get("id", ""))
    back = f"/salesdev/templates?edit={quote(template_id)}#form" if template_id else "/salesdev/templates#form"
    upload = form.get("attachment_file")
    pdf = b""
    filename = ""
    if upload is not None and not isinstance(upload, str) and getattr(upload, "filename", ""):
        pdf = await upload.read()
        filename = upload.filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if not gmail_drafts.looks_like_pdf(pdf):
            return _redirect(back, err="附件只能上傳 PDF 檔。")
        if len(pdf) > gmail_drafts.MAX_ATTACHMENT_BYTES:
            return _redirect(back, err=f"PDF 太大了，最多 {gmail_drafts.MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB。")
    try:
        saved_id = mail_templates.save_template(
            template_id,
            str(form.get("name", "")),
            str(form.get("subject", "")),
            str(form.get("content", "")),
            filename or str(form.get("attachment_name", "")),
            _username(request),
        )
    except ValueError as exc:
        return _redirect(back, err=str(exc))
    try:
        if pdf:
            blob = gmail_drafts.upload_attachment(saved_id, pdf)
            mail_templates.set_attachment(saved_id, blob, filename, len(pdf), _username(request))
        elif str(form.get("remove_attachment", "")) == "1":
            mail_templates.set_attachment(saved_id, "", "", 0, _username(request))
    except Exception as exc:
        print(f"[業務開發寄信] 上傳範本附件失敗：{exc}")
        return _redirect(f"/salesdev/templates?edit={quote(saved_id)}#form", err=f"範本已儲存，但 PDF 上傳失敗：{exc}")
    return _redirect("/salesdev/templates", msg="已儲存範本。")


@router.get("/salesdev/templates/{template_id}/attachment")
def salesdev_templates_attachment(request: Request, template_id: str, redirect=Depends(_require_access)):
    """下載範本的 PDF，讓使用者確認傳的是哪一份。"""
    if redirect:
        return redirect
    template = mail_templates.get_template(template_id)
    data = gmail_drafts.download_attachment((template or {}).get("attachment_blob", ""))
    if not data:
        return _redirect("/salesdev/templates", err="這個範本沒有 PDF，或檔案不見了。")
    filename = template.get("attachment_name") or "attachment.pdf"
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}"},
    )


@router.post("/salesdev/templates/{template_id}/delete")
def salesdev_templates_delete(request: Request, template_id: str, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    if not mail_templates.delete_template(template_id):
        return _redirect("/salesdev/templates", err="找不到這個範本，可能已經被刪掉了。")
    return _redirect("/salesdev/templates", msg="已刪除範本（以前用它寄過的紀錄還在）。")


# ---------------------------------------------------------------------------
# 連結 Gmail（做法二：建草稿、自動夾 PDF；2026-09-26）
# ---------------------------------------------------------------------------

def _gmail_redirect_uri(request: Request) -> str:
    """OAuth 授權完回到平台的網址。用使用者現在開的網域（授權前把 state 存在登入 session，
    回來要是同一個網域才讀得到）。這個網址要原封不動貼到 Google Cloud 主控台 OAuth 用戶端的
    「已授權的重新導向 URI」，信件範本頁上會顯示。"""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"https://{host}/salesdev/gmail/callback"


@router.get("/salesdev/gmail/connect")
def salesdev_gmail_connect(request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    if not gmail_drafts.is_configured():
        return _redirect("/salesdev/templates", err="還沒設定 Google OAuth 用戶端（見信件範本頁的說明）。")
    state = gmail_drafts.new_state()
    request.session["salesdev_gmail_oauth_state"] = state
    return RedirectResponse(url=gmail_drafts.authorization_url(_gmail_redirect_uri(request), state), status_code=303)


@router.get("/salesdev/gmail/callback")
def salesdev_gmail_callback(request: Request, code: str = "", state: str = "", error: str = "", redirect=Depends(_require_access)):
    if redirect:
        return redirect
    expected = request.session.pop("salesdev_gmail_oauth_state", "")
    if error:
        return _redirect("/salesdev/templates", err=f"沒有完成 Gmail 授權（{error}）。")
    if not expected or not state or not hmac.compare_digest(state.encode(), expected.encode()):
        return _redirect("/salesdev/templates", err="授權驗證碼對不上，請再按一次「連結 Gmail」。")
    try:
        email = gmail_drafts.complete_authorization(code, _gmail_redirect_uri(request), _username(request))
    except Exception as exc:
        print(f"[業務開發寄信] Gmail 授權失敗：{exc}")
        return _redirect("/salesdev/templates", err=f"Gmail 授權失敗：{exc}")
    return _redirect("/salesdev/templates", msg=f"已連結 {email} 的 Gmail，寄信頁可以直接建立含附件的草稿了。")


@router.post("/salesdev/gmail/disconnect")
def salesdev_gmail_disconnect(request: Request, redirect=Depends(_require_access)):
    if redirect:
        return redirect
    gmail_drafts.disconnect()
    return _redirect("/salesdev/templates", msg="已中斷 Gmail 連結。")
