"""薪資補款改由平台處理（2026-09-26，GAS 搬家階段 2 第二段 PR 2）：收單、推核准卡片、核准／退回、寄信。

一律照 job-portal-gas-project `Project_Salary.js` 現在的做法搬過來（使用者確認的原則），逐項對照：
- 送出 `submit()` ＝ `processSalarySubmission()`：必填檢查 → 重複申請（員工姓名＋身分證＋補請款月份）→ 申請人
  有沒有完成 LINE 綁定 → 找主管（找不到退回系統管理員；都沒有就推警示卡片給申請人）→ 後端重算金額 → 單號
  `SAL-yyyyMMddHHmmss`（台北時間）→ 存照片 → 寫資料 → 推核准卡片給每位主管（一位都推不到就提醒申請人）。
- 核准卡片 `approval_card()` ＝ `SalaryFlexMessageBuilder.buildSalaryApprovalCard()`，postback 格式一樣
  （`action=review_salary&status=approve|reject&salary_id=…&applicant_id=…`）。唯一差別：照片不公開，卡片不放
  縮圖，改放「查看佐證照片」按鈕連到平台（要登入）——之前 HANDOFF 記錄過的照片公開連結疑慮就此解決。
- 核准／退回 `handle_review()` ＝ `processPostback()` 的權限檢查＋`handleSalaryPostback()`：只有該申請人的主管或
  系統管理員能按；同一張單只能審一次（Firestore `create()` 佔位，重複按、LINE 重送都擋得住）；核准寄信（附照片
  ＋PDF 存查單，內容見 `salary_repayment_report.py`）；回覆主管、通知申請人、通知其他主管，文字照 GAS。
- 退回：試算表整列刪除（跟 GAS 一樣不給會計看），平台資料保留但標記 `rejected`，我的專區不顯示（跟 GAS 一樣）。

資料以 Firestore 為正本（`salary_repayments`，`source="platform"`），同時照 GAS 格式寫一列到「薪資補款紀錄」
給財務的會計對帳表（`salary_repayment_sheet_writer.py`）；寫失敗會標記 `sheet_status="failed"`，搬家頁面可以補寫。

**開關**（`salary_repayment_meta/state.platform_mode`，搬家頁面第 7 區）：打開後「我的專區」送出的補款單改由
平台處理；關掉就回到轉給 GAS。平台建立的單不管開關都由平台處理核准（總機依單號判斷，舊單照樣轉給 GAS）。
"""
import re
from datetime import datetime, timezone

import requests

from config import (
    JOB_PORTAL_LINE_CHANNEL_ACCESS_TOKEN,
    SALARY_ADMIN_EMAILS,
    SALARY_ADMIN_LINE_USER_IDS,
    SERVICE_BASE_URL,
    TAIPEI_TZ,
)
from services import email_service
from services import salary_repayment_photos as photos
from services import salary_repayment_report as report
from services import salary_repayment_sheet_writer as sheet_writer
from services import salary_repayment_store as store

DEFAULT_BASE_URL = "https://recruitment-bot-412901869672.asia-east1.run.app"
REVIEWS_COLLECTION = "salary_repayment_reviews"  # 一張單只能審一次的佔位文件

STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED = "待審核", "已核准", "已退回"

# GAS 建分頁時寫的標準 22 欄（Project_Salary.js）；同步過就用實際表頭
GAS_HEADERS = ["補款單號", "申請時間", "申請人姓名", "申請人 LINE ID", "員工姓名", "身分證", "廠商/店家", "申請日",
               "付款日", "扣分鐘月份", "補請款月份", "是否可請款", "補款方式", "加項小計", "扣項小計", "實補總額", "備註",
               "審核狀態", "核准主管", "核准時間", "補款佐證(照片)", "匯費"]

C = report.COL


# ---------------------------------------------------------------- 開關

def is_enabled() -> bool:
    try:
        return bool(store.get_state().get("platform_mode"))
    except Exception:
        return False


def missing_requirements() -> list:
    """打開開關前要先設好的東西，回傳還缺的項目（白話）。"""
    from services import job_portal_line_relay as relay

    missing = []
    if not JOB_PORTAL_LINE_CHANNEL_ACCESS_TOKEN:
        missing.append("LINE Channel access token（JOB_PORTAL_LINE_CHANNEL_ACCESS_TOKEN）")
    if not relay.is_configured():
        missing.append("LINE 總機（第 4 區）")
    if not email_service.is_configured():
        missing.append("寄信用的 SMTP 設定")
    if not report.SALARY_HR_ACCOUNTING_EMAILS:
        missing.append("財會收件信箱（SALARY_HR_ACCOUNTING_EMAILS）")
    if not photos.SALARY_PHOTO_GCS_BUCKET:
        missing.append("存照片的 Cloud Storage（DELIVERY_GCS_BUCKET）")
    if not store.get_state().get("last_synced_at"):
        missing.append("第 1 區「從試算表同步到平台」")
    return missing


def set_enabled(enabled: bool, actor: dict) -> None:
    store.meta_ref().set(
        {
            "platform_mode": bool(enabled),
            "platform_mode_changed_at": datetime.now(timezone.utc),
            "platform_mode_changed_by": actor.get("name") or actor.get("username") or "",
        },
        merge=True,
    )


# ---------------------------------------------------------------- LINE

def _line_post(path: str, body: dict) -> bool:
    if not JOB_PORTAL_LINE_CHANNEL_ACCESS_TOKEN:
        return False
    try:
        resp = requests.post(
            f"https://api.line.me/v2/bot/message/{path}",
            json=body,
            headers={"Authorization": f"Bearer {JOB_PORTAL_LINE_CHANNEL_ACCESS_TOKEN}"},
            timeout=10,
        )
    except requests.RequestException as e:
        print(f"[SALARY_PLATFORM] LINE {path} 失敗：{e}")
        return False
    if resp.status_code != 200:
        print(f"[SALARY_PLATFORM] LINE {path} 回應 {resp.status_code}：{resp.text[:200]}")
        return False
    return True


def push(to: str, messages: list) -> bool:
    return bool(to) and _line_post("push", {"to": to, "messages": messages})


def reply(reply_token: str, text: str, fallback_to: str = "") -> bool:
    """用 replyToken 回覆（免費）；token 過期或失敗時改用 push 發給同一個人。"""
    message = [{"type": "text", "text": text}]
    if reply_token and _line_post("reply", {"replyToken": reply_token, "messages": message}):
        return True
    return push(fallback_to, message)


def _text(t: str) -> dict:
    return {"type": "text", "text": t}


# ---------------------------------------------------------------- 組織表／主管

def load_org() -> report.Org:
    """員工主管組織表：優先即時讀試算表（綁定登記還在 GAS，組織表隨時會變），讀不到才用第 1 步的平台副本。"""
    from services.salary_repayment_service import fetch_sheet_values, rows_to_dicts

    org_values, _, error = fetch_sheet_values()
    if not error and org_values:
        return report.Org(rows_to_dicts(org_values), [str(h) for h in org_values[0]])
    org_rows, _, _ = store.load_rows()
    return report.Org(org_rows, store.get_state().get("org_headers") or [])


def admin_line_ids() -> list:
    ids = [re.sub(r"[^a-zA-Z0-9_-]", "", s) for s in report.split_multi(SALARY_ADMIN_LINE_USER_IDS)]
    return [i for i in ids if report._LINE_ID.match(i)]


def default_supervisors() -> list:
    """GAS `getDefaultSupervisors()`：系統管理員。"""
    emails = [e for e in report.split_multi(SALARY_ADMIN_EMAILS) if e]
    return [
        {"name": "系統管理員", "line_id": lid, "email": emails[k] if k < len(emails) else (emails[0] if emails else "")}
        for k, lid in enumerate(admin_line_ids())
    ]


def supervisors_for(org: report.Org, applicant_line_id: str, applicant_name: str) -> list:
    return org.supervisors(applicant_line_id, applicant_name) or default_supervisors()


# ---------------------------------------------------------------- 資料

def headers() -> list:
    saved = store.get_state().get("record_headers") or []
    return saved if len(saved) >= len(GAS_HEADERS) else GAS_HEADERS


def _values(doc: dict, hdrs: list) -> list:
    fields = doc.get("fields") or {}
    return [str(fields.get(h, "") or "") for h in hdrs]


def is_platform_doc(doc: dict) -> bool:
    return (doc or {}).get("source") == store.SOURCE_PLATFORM


def get_doc(doc_id: str):
    """讀不到（Firestore 出問題）一律當成沒有，呼叫端會退回走 GAS 的舊流程。"""
    try:
        snapshot = store.records_ref().document(doc_id).get()
    except Exception:
        return None
    return snapshot.to_dict() if snapshot.exists else None


def normalize_month(value) -> str:
    """GAS `normalizeMonthValue()`：統一成 yyyy-MM 再比對（試算表可能存成 2026/9/1、2026-09…）。"""
    text = str(value or "").strip()
    match = re.match(r"^(\d{4})[-/.](\d{1,2})", text)
    return f"{match.group(1)}-{int(match.group(2)):02d}" if match else text


def find_duplicate(employee_name: str, id_card: str, compensate_month: str):
    """回傳重複的補款單號，沒有回傳 None。退回的單不算（GAS 那邊退回會整列刪除）。"""
    hdrs = headers()
    target = (employee_name.strip(), id_card.strip().upper(), compensate_month.strip())
    for snap in store.records_ref().stream():
        doc = snap.to_dict() or {}
        if doc.get("rejected"):
            continue
        row = _values(doc, hdrs)
        key = (row[C["name"]].strip(), row[C["id_card"]].strip().upper(), normalize_month(row[C["compensate_month"]]))
        if key == target:
            return row[C["salary_id"]] or snap.id
    return None


def _money_text(value: float) -> str:
    return str(int(value)) if value == int(value) else str(value)


def _new_salary_id(now: datetime) -> str:
    base = "SAL-" + now.astimezone(TAIPEI_TZ).strftime("%Y%m%d%H%M%S")
    salary_id, n = base, 1
    while get_doc(salary_id) is not None:  # 同一秒兩張單（GAS 沒處理，這裡補上）
        n += 1
        salary_id = f"{base}-{n}"
    return salary_id


def base_url() -> str:
    return (SERVICE_BASE_URL or DEFAULT_BASE_URL).rstrip("/")


def photo_page_url(doc_id: str) -> str:
    return f"{base_url()}/me/salary-repayment/{doc_id}/photo"


def mask_id_card(id_card: str) -> str:
    """GAS `maskIdCard()`：前 3 後 3，中間打星號。"""
    clean = (id_card or "").strip()
    if not clean:
        return "-"
    if len(clean) <= 6:
        return clean[:1] + "*" * max(len(clean) - 1, 0)
    return clean[:3] + "*" * (len(clean) - 6) + clean[-3:]


# ---------------------------------------------------------------- 卡片

def _row(label, value, color="#1e293b", bold=False) -> dict:
    """GAS `SharedFlexBuilder.createRow()`。"""
    text = str(value).strip() if value not in (None, "") and str(value).strip() else "-"
    return {
        "type": "box", "layout": "horizontal",
        "contents": [
            {"type": "text", "text": str(label or "-"), "size": "xs", "color": "#64748b", "flex": 4},
            {"type": "text", "text": text, "size": "xs", "color": color, "align": "end",
             "weight": "bold" if bold else "regular", "flex": 6, "wrap": True},
        ],
    }


def approval_card(record: dict, applicant_line_id: str, has_photo: bool, doc_id: str) -> dict:
    sid, name = record["salary_id"], record["name"]
    base = f"salary_id={sid}&applicant_id={applicant_line_id}"
    contents = [
        _row("申請同仁", record["applicant_name"] or "未提供", "#0284c7", True),
        _row("身分證字號", mask_id_card(record["id_card"])),
        _row("補請月份", record["compensate_month"] or "-"),
        _row("補款方式", record["pay_type"] or "-"),
        _row("備註說明", record["notes"] or "-", "#b91c1c", True),
        _row("應領小計 (+)", f"NT$ {report.format_money(record['total_earnings'])}", "#059669"),
        _row("應扣小計 (-)", f"NT$ {report.format_money(record['total_deductions'])}", "#e11d48"),
        {"type": "box", "layout": "horizontal", "contents": [
            {"type": "text", "text": "實補總額", "size": "sm", "weight": "bold", "color": "#0f172a"},
            {"type": "text", "text": f"NT$ {report.format_money(record['net_total'])}", "size": "md", "weight": "bold",
             "color": "#d97706", "align": "end"},
        ]},
    ]
    if has_photo:
        contents.append(_row("補款佐證", "已附圖檔（點下方按鈕查看）", "#0284c7", True))
    body = [
        {"type": "text", "text": f"補款員工：{name or '-'}", "weight": "bold", "size": "lg", "wrap": True},
        {"type": "text", "text": f"廠商：{record['vendor'] or '-'} | 付款日：{record['pay_date'] or '未指定'}",
         "size": "xs", "color": "#64748b", "margin": "xs"},
        {"type": "separator", "margin": "md"},
        {"type": "box", "layout": "vertical", "margin": "md", "spacing": "sm", "contents": contents},
    ]
    if has_photo:
        body.append({"type": "button", "style": "link", "height": "sm", "margin": "md",
                     "action": {"type": "uri", "label": "查看佐證照片（需登入）", "uri": photo_page_url(doc_id)}})
    return {
        "type": "flex",
        "altText": f"[薪資補款審核] {name or '員工'} - NT$ {report.format_money(record['net_total'])}",
        "contents": {
            "type": "bubble",
            "header": {"type": "box", "layout": "vertical", "backgroundColor": "#ecfdf5", "contents": [
                {"type": "text", "text": "💰 薪資補款審核申請", "size": "sm", "color": "#065f46", "weight": "bold"}]},
            "body": {"type": "box", "layout": "vertical", "contents": body},
            "footer": {"type": "box", "layout": "horizontal", "spacing": "md", "contents": [
                {"type": "button", "style": "primary", "color": "#059669", "action": {
                    "type": "postback", "label": "核准發信", "data": f"action=review_salary&status=approve&{base}",
                    "displayText": f"核准薪資補款單：{name}"}},
                {"type": "button", "style": "secondary", "color": "#f43f5e", "action": {
                    "type": "postback", "label": "退回", "data": f"action=review_salary&status=reject&{base}",
                    "displayText": f"退回薪資補款單：{name}"}},
            ]},
        },
    }


def no_supervisor_card(applicant_name: str, item_title: str) -> dict:
    """GAS `SharedFlexBuilder.buildNoSupervisorWarningCard()`。"""
    return {
        "type": "flex",
        "altText": "⚠️ 【送審未成功】尚未指派審核主管",
        "contents": {
            "type": "bubble",
            "header": {"type": "box", "layout": "vertical", "backgroundColor": "#fff1f2", "contents": [
                {"type": "text", "text": "⚠️ 送審作業未成功", "size": "sm", "color": "#e11d48", "weight": "bold"}]},
            "body": {"type": "box", "layout": "vertical", "spacing": "md", "contents": [
                {"type": "text", "text": f"{applicant_name or '同仁'} 您好：", "weight": "bold", "size": "md", "color": "#0f172a"},
                {"type": "text", "text": "您提交的【薪資補款申請】未能完成送審，原因是組織表中尚未為您指派「審核主管」。",
                 "size": "xs", "color": "#475569", "wrap": True},
                {"type": "separator", "margin": "sm"},
                {"type": "box", "layout": "vertical", "spacing": "xs", "contents": [
                    _row("申請項目", "薪資補款申請"),
                    _row("標的內容", item_title or "-", "#0284c7", True),
                    _row("目前狀態", "未指派主管 / 無法簽核", "#e11d48", True),
                ]},
                {"type": "box", "layout": "vertical", "backgroundColor": "#f8fafc", "paddingAll": "md", "cornerRadius": "md",
                 "contents": [{"type": "text", "size": "xxs", "color": "#64748b", "wrap": True,
                               "text": "💡 處理方式：\n請聯繫「系統管理員」，請管理員至公司【員工主管組織表】填入您的直屬主管姓名與主管 LINE ID，完成設定後即可正常送審。"}]},
            ]},
        },
    }


# ---------------------------------------------------------------- 送出

def submit(info: dict, earnings: dict, deductions: dict, image: dict = None) -> dict:
    """info 的 key 跟 GAS payload.info 一樣（applicant_name、name、id_card…）。image＝{"content","content_type"}。
    回傳跟 GAS 一樣的 {status, message, salaryId}。"""
    applicant_name = str(info.get("applicant_name") or "").strip()
    employee_name = str(info.get("name") or "").strip()
    if not applicant_name:
        return {"status": "unauthorized", "message": "請選取「申請同仁姓名」，以利系統核對您的送審權限。"}
    if not employee_name:
        return {"status": "error", "message": "請填寫「員工姓名」（實際補款對象）。"}
    if not str(info.get("notes") or "").strip():
        return {"status": "error", "message": "請填寫「備註說明」（必填）。"}

    compensate_month = str(info.get("compensate_month") or "").strip()
    duplicate = find_duplicate(employee_name, str(info.get("id_card") or ""), compensate_month)
    if duplicate:
        return {"status": "error", "message": (
            f"系統偵測到員工【{employee_name}】在補請款月份【{compensate_month}】已有一筆補款申請紀錄（單號：{duplicate}），"
            "請勿重複申請！如需修改請確認原申請單的處理狀況，或聯繫系統管理員協助處理。")}

    org = load_org()
    applicant_line_id = org.binding(applicant_name)
    if not applicant_line_id:
        return {"status": "unauthorized", "message": (
            f"申請同仁【{applicant_name}】尚未完成 LINE 身分綁定！\n"
            f"請先至 LINE 官方帳號發送「綁定+{applicant_name}+4位PIN碼」完成綁定後再進行補款申請。")}

    sups = supervisors_for(org, applicant_line_id, applicant_name)
    if not sups:
        push(applicant_line_id, [no_supervisor_card(applicant_name, f"補款員工：{employee_name} ({info.get('vendor') or '廠商'})")])
        return {"status": "supervisor_unassigned", "message": (
            f"【送審失敗】組織表中尚未為申請同仁【{applicant_name}】設定審核主管！\n"
            "系統已發送 LINE 通知給您，請聯繫系統管理員協助於後台組織表指派主管。")}

    total_earnings = sum(float(v or 0) for v in (earnings or {}).values())
    total_deductions = sum(float(v or 0) for v in (deductions or {}).values())
    now = datetime.now(timezone.utc)
    salary_id = _new_salary_id(now)

    photo_fields = {}
    image_url = ""
    if image and image.get("content"):
        try:
            blob = photos.upload_photo(salary_id, image["content"], image.get("content_type") or "image/jpeg")
            image_url = photo_page_url(salary_id)
            photo_fields = {"photo_blob": blob, "photo_content_type": image.get("content_type") or "image/jpeg",
                            "photo_source_url": image_url, "photo_copied_at": now}
        except Exception as e:  # 跟 GAS 一樣：照片存不了不擋送出
            print(f"[SALARY_PLATFORM] 存佐證照片失敗：{e}")

    values = [""] * len(GAS_HEADERS)
    values[C["salary_id"]] = salary_id
    values[C["apply_timestamp"]] = now.astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    values[C["applicant_name"]] = applicant_name
    values[C["applicant_line_id"]] = applicant_line_id
    values[C["name"]] = employee_name
    values[C["id_card"]] = str(info.get("id_card") or "")
    values[C["vendor"]] = str(info.get("vendor") or "")
    values[C["apply_date"]] = str(info.get("apply_date") or "")
    values[C["pay_date"]] = str(info.get("pay_date") or "")
    values[C["deduct_month"]] = str(info.get("deduct_month") or "")
    values[C["compensate_month"]] = compensate_month
    values[C["is_claimable"]] = str(info.get("is_claimable") or "")
    values[C["pay_type"]] = str(info.get("pay_type") or "")
    values[C["total_earnings"]] = _money_text(total_earnings)
    values[C["total_deductions"]] = _money_text(total_deductions)
    values[C["net_total"]] = _money_text(total_earnings - total_deductions)
    values[C["notes"]] = str(info.get("notes") or "")
    values[C["review_status"]] = STATUS_PENDING
    values[C["image_url"]] = image_url
    values[C["remit_fee"]] = _money_text(float((deductions or {}).get("remit_fee") or 0))
    hdrs = headers()
    fields = dict(zip(hdrs, values + [""] * (len(hdrs) - len(values))))

    ref = store.records_ref().document(salary_id)
    ref.set({
        "fields": fields,
        "row_number": _next_row_number(),
        "source": store.SOURCE_PLATFORM,
        "created_at": now,
        "earnings": earnings or {},
        "deductions": deductions or {},
        "supervisors": sups,
        "sheet_status": "pending",
        **photo_fields,
    })
    _write_sheet_append(salary_id, fields)

    record = report.build_record(fields, hdrs, org)
    card = approval_card(record, applicant_line_id, bool(photo_fields), salary_id)
    pushed = sum(1 for s in sups if push(s["line_id"], [card]))
    if pushed == 0:
        push(applicant_line_id, [_text(
            f"⚠️ 【系統警告】補款單 [{salary_id}] 已成功建立，但系統無法推播給您的主管！\n\n"
            "可能原因：主管尚未完成 LINE 綁定或已封鎖官方帳號。\n👉 請主動聯繫主管進行審核。")])
    return {"status": "success", "message": "薪資補款單已成功建立並送出審核", "salaryId": salary_id}


def _next_row_number() -> int:
    numbers = [(s.to_dict() or {}).get("row_number") or 0 for s in store.records_ref().stream()]
    return (max(numbers) if numbers else 1) + 1


def _write_sheet_append(doc_id: str, fields: dict) -> bool:
    ok, message = sheet_writer.append_record(fields)
    store.records_ref().document(doc_id).set(
        {"sheet_status": "ok" if ok else "failed", "sheet_error": "" if ok else message}, merge=True)
    return ok


# ---------------------------------------------------------------- 核准／退回

def parse_postback(data: str) -> dict:
    result = {}
    for part in (data or "").split("&"):
        key, _, value = part.partition("=")
        if key:
            result[key] = requests.utils.unquote(value)
    return result


def owns_event(event: dict) -> bool:
    """總機用：這個 LINE 事件是不是「平台建立的補款單」的核准按鈕（是的話平台自己處理，不轉 GAS）。"""
    if event.get("type") != "postback":
        return False
    data = parse_postback((event.get("postback") or {}).get("data"))
    if data.get("action") != "review_salary" or not data.get("salary_id"):
        return False
    try:
        return is_platform_doc(get_doc(data["salary_id"]))
    except Exception:
        return False


def _claim_review(doc_id: str, status: str, operator: str) -> bool:
    """同一張單只能審一次：建立佔位文件，已經存在（別人先按了、LINE 重送）就失敗。"""
    try:
        store.get_db().collection(REVIEWS_COLLECTION).document(doc_id).create(
            {"status": status, "operator": operator, "at": datetime.now(timezone.utc)})
        return True
    except Exception:
        return False


def handle_review(event: dict) -> None:
    """處理一個 review_salary postback（背景工作，不拋例外）。"""
    try:
        _handle_review(event)
    except Exception as e:
        print(f"[SALARY_PLATFORM] 處理核准按鈕失敗：{e}")


def _handle_review(event: dict) -> None:
    data = parse_postback((event.get("postback") or {}).get("data"))
    doc_id, is_approve = data.get("salary_id", ""), data.get("status") == "approve"
    operator = ((event.get("source") or {}).get("userId") or "").strip()
    reply_token = event.get("replyToken", "")
    doc = get_doc(doc_id)
    if not doc:
        reply(reply_token, "❌ 操作失敗：找不到該薪資補款單資料。", operator)
        return
    hdrs = headers()
    row = _values(doc, hdrs)
    applicant_line_id = row[C["applicant_line_id"]] or data.get("applicant_id", "")

    org = load_org()
    sups = supervisors_for(org, applicant_line_id, "")
    authorized = {s["line_id"].upper() for s in sups} | {i.upper() for i in admin_line_ids()}
    if operator.upper() not in authorized:
        reply(reply_token, "❌ 操作失敗：您非此申請單之授權審核主管，無權限執行簽核。", operator)
        return

    current = row[C["review_status"]]
    status = STATUS_APPROVED if is_approve else STATUS_REJECTED
    if current in (STATUS_APPROVED, STATUS_REJECTED) or not _claim_review(doc_id, status, operator):
        reply(reply_token, f"⚠️ 操作無效：此單據已由主管完成審核 (目前狀態：{current or '處理中'})，無法重複簽核。", operator)
        return

    now_text = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    fields = dict(doc.get("fields") or {})
    for key, value in (("review_status", status), ("approved_supervisor", operator), ("approved_time", now_text)):
        if C[key] < len(hdrs):
            fields[hdrs[C[key]]] = value
    update = {"fields": fields, "reviewed_by": operator, "reviewed_at": datetime.now(timezone.utc)}
    if not is_approve:
        update["rejected"] = True
    ok, message = sheet_writer.update_review(doc_id, status, operator, now_text)
    update["sheet_review_status"] = "ok" if ok else "failed"
    update["sheet_review_error"] = "" if ok else message
    store.records_ref().document(doc_id).set(update, merge=True)

    mail_ok, mail_message = True, ""
    if is_approve:
        mail_ok, mail_message = send_approval_email(doc_id)

    if is_approve:
        reply_text = (f"✅ 薪資補款單 [{doc_id}] 審核完成：已核准！\n系統已自動寄出正式 HTML 薪資補款報表與佐證圖檔至財會、主管與同仁信箱。"
                      if mail_ok else
                      f"⚠️ 薪資補款單 [{doc_id}] 審核完成：已核准，但通知信寄送失敗（{mail_message or '原因不明'}）。請到系統使用「補寄信」功能重試，或聯絡系統管理員確認。")
        notify = (f"🎉 您提交的薪資補款申請單 [{doc_id}] 已通過主管核准！\n詳細補款報表與附件已同步發信通知。"
                  if mail_ok else
                  f"🎉 您提交的薪資補款申請單 [{doc_id}] 已通過主管核准！\n（通知信寄送發生問題，如需要書面報表請聯絡人資/財務協助補寄。）")
        sync = (f"✅ 【審核同步】薪資補款單 [{doc_id}] 已由其他主管核准！\n系統已自動寄出正式 HTML 薪資補款報表與佐證圖檔至財會、主管與同仁信箱。"
                if mail_ok else
                f"⚠️ 【審核同步】薪資補款單 [{doc_id}] 已由其他主管核准，但通知信寄送失敗，請到系統補寄或聯絡管理員。")
    else:
        reply_text = f"❌ 薪資補款單 [{doc_id}] 審核完成：已退回！"
        notify = f"⚠️ 您提交的薪資補款申請單 [{doc_id}] 已被主管退回，請確認資料後重新提出。"
        sync = f"⚠️ 【審核同步】薪資補款單 [{doc_id}] 已由其他主管退回。"
    reply(reply_token, reply_text, operator)
    if report._LINE_ID.match(applicant_line_id or ""):
        push(applicant_line_id, [_text(notify)])
    for sup in org.supervisors(applicant_line_id, ""):
        if sup["line_id"].upper() != operator.upper():
            push(sup["line_id"], [_text(sync)])


def send_approval_email(doc_id: str) -> tuple:
    """寄核准通知信（附佐證照片＋PDF 存查單），回傳 (ok, 訊息)。補寄信也用這支。"""
    doc = get_doc(doc_id)
    if not doc:
        return False, "找不到這筆補款單。"
    hdrs = headers()
    org = load_org()
    record = report.build_record(doc.get("fields") or {}, hdrs, org)
    if record["review_status"] != STATUS_APPROVED:
        return False, "這筆補款單還沒核准，不能寄核准信。"
    to = report.recipients(record)
    if not to:
        return False, "沒有配置任何有效的收件人信箱（財會/主管/申請人信箱皆無法取得）"
    attachments, inline, image_src = [], [], ""
    if photos.photo_status(doc) == photos.STATUS_DONE:
        content, content_type = photos.download_photo(doc.get("photo_blob"))
        if content:
            image_src = "cid:salaryProofImg"
            name = f"補款佐證_{record['salary_id']}.jpg"
            inline.append({"content_id": "salaryProofImg", "content": content, "mime_type": content_type, "filename": name})
            attachments.append({"content": content, "mime_type": content_type, "filename": name})
    try:
        pdf = report.build_pdf(record)
    except Exception as e:
        print(f"[SALARY_PLATFORM] 產生 PDF 存查單失敗（不影響信件寄送）：{e}")
        pdf = None
    if pdf:
        attachments.append({"content": pdf, "mime_type": "application/pdf", "filename": report.pdf_filename(record)})
    old_link = record["image_url"] if "googleusercontent" in record["image_url"] else ""
    ok, message = email_service.send_email(to, report.subject(record), report.email_html(record, image_src, old_link),
                                           attachments=attachments, inline_images=inline)
    store.records_ref().document(doc_id).set(
        {"mail_status": "ok" if ok else "failed", "mail_error": "" if ok else message,
         "mail_at": datetime.now(timezone.utc), "mail_to": to}, merge=True)
    return ok, message if not ok else "、".join(to)


def sync_from_sheet_quietly() -> None:
    """把試算表最新狀態同步回平台（舊單由 GAS 審核、或還有人用舊表單直接送給 GAS 時用）。不拋例外。"""
    from services.salary_repayment_service import fetch_sheet_values

    try:
        org_values, record_values, error = fetch_sheet_values()
        if error or not record_values:
            print(f"[SALARY_PLATFORM] 自動同步略過：{error or '試算表是空的'}")
            return
        store.sync_from_sheet(org_values, record_values, {"name": "系統自動同步"})
    except Exception as e:
        print(f"[SALARY_PLATFORM] 自動同步失敗：{e}")


# ---------------------------------------------------------------- 搬家頁面：試算表補寫

def sheet_failures() -> list:
    result = []
    for snap in store.records_ref().stream():
        doc = snap.to_dict() or {}
        if not is_platform_doc(doc):
            continue
        if doc.get("sheet_status") == "failed" or doc.get("sheet_review_status") == "failed":
            result.append({"doc_id": snap.id, "error": doc.get("sheet_error") or doc.get("sheet_review_error") or "",
                           "kind": "新增" if doc.get("sheet_status") == "failed" else "審核狀態"})
    return result


def retry_sheet(doc_id: str) -> tuple:
    doc = get_doc(doc_id)
    if not is_platform_doc(doc):
        return False, "找不到這筆平台建立的補款單。"
    if doc.get("sheet_status") == "failed":
        # 新增那一列從來沒寫進去：直接寫目前的內容（已經審過的話狀態也在裡面）；退回的單本來就不留在試算表
        if doc.get("rejected"):
            store.records_ref().document(doc_id).set({"sheet_status": "ok", "sheet_review_status": "ok"}, merge=True)
            return True, "這筆已退回，試算表本來就不保留，不用補寫。"
        if not _write_sheet_append(doc_id, doc.get("fields") or {}):
            return False, "還是寫不進試算表，請稍後再試。"
        store.records_ref().document(doc_id).set({"sheet_review_status": "ok", "sheet_review_error": ""}, merge=True)
        return True, "已補寫進試算表。"
    if doc.get("sheet_review_status") == "failed":
        row = _values(doc, headers())
        ok, message = sheet_writer.update_review(doc_id, row[C["review_status"]], row[C["approved_supervisor"]],
                                                 row[C["approved_time"]])
        store.records_ref().document(doc_id).set(
            {"sheet_review_status": "ok" if ok else "failed", "sheet_review_error": "" if ok else message}, merge=True)
        if not ok:
            return False, message
    return True, "已補寫進試算表。"

