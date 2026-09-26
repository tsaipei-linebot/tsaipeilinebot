"""薪資補款核准信＋PDF 存查單由平台產生（2026-09-26，GAS 搬家階段 2 第二段的 PR 1）。

內容逐項照抄 job-portal-gas-project `Project_Salary.js`：
- `EmailService.sendSalaryCompensationReport()`：信件主旨、HTML、收件人（財會信箱＋審核主管＋申請人本人）。
- `EmailService.buildSalaryPdfBlob()`：PDF 存查單（GAS 用 Google Docs 組版再匯出 PDF；這裡用 python-docx
  組一樣的版面，再用容器裡的 LibreOffice 轉 PDF，見 `services/docx_pdf_conversion.py`）。
- `formatMinguoDate()`（民國年 115.09.14／115.09）、`toLocaleString()`（千分位）。

**欄位一律照 GAS 的讀法用「第幾欄」取值**（GAS 是 `data[i][4]` 這樣讀的），不靠表頭文字——試算表
表頭跟 GAS 預設標題列不一定一字不差（例如照片欄曾經以為叫「佐證照片網址」，實際是「補款佐證(照片)」）。
欄位順序來自第 1 步同步時存下的表頭順序（`salary_repayment_meta/state.record_headers`）。

跟 GAS 刻意不一樣的地方只有一個：GAS 的「核准主管」直接印試算表 S 欄的 LINE User ID（一串英數字），
這裡跟 `/me` 一樣先用員工主管組織表換成姓名，換不到才印原值。

這個 PR 只提供「產生」，不寄信、不改任何資料；`/finance/migration` 可以挑已核准的舊單預覽，跟 GAS 寄過
的比對。第二段 PR 2（平台處理核准）才會真的拿來寄信。
"""
import html
import io
import re
from datetime import datetime

from config import SALARY_HR_ACCOUNTING_EMAILS

# GAS SalarySheetService 讀「薪資補款紀錄」的欄位位置（0 起算）
COL = {
    "salary_id": 0, "apply_timestamp": 1, "applicant_name": 2, "applicant_line_id": 3, "name": 4,
    "id_card": 5, "vendor": 6, "apply_date": 7, "pay_date": 8, "deduct_month": 9,
    "compensate_month": 10, "is_claimable": 11, "pay_type": 12, "total_earnings": 13,
    "total_deductions": 14, "net_total": 15, "notes": 16, "review_status": 17,
    "approved_supervisor": 18, "approved_time": 19, "image_url": 20, "remit_fee": 21,
}
# 「員工主管組織表」欄位位置（GAS OrgService）
ORG_NAME, ORG_LINE_ID, ORG_SUP_NAMES, ORG_SUP_LINE_IDS, ORG_SUP_EMAILS, ORG_EMP_EMAIL = 0, 1, 2, 3, 4, 9

_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_LINE_ID = re.compile(r"^[a-zA-Z0-9_-]{10,64}$")


def split_multi(value) -> list:
    """GAS `splitMultiValue()`：逗號（全半形）、頓號、斜線、空白、換行都當分隔。"""
    return [s.strip() for s in re.split(r"[,，、/\\\s]+", str(value or ""))]


def _row(fields: dict, headers: list) -> list:
    return [str(fields.get(h, "") or "") for h in headers]


def _cell(row: list, index: int) -> str:
    return row[index].strip() if index < len(row) else ""


def parse_number(value) -> float:
    text = str(value or "").replace(",", "").replace("NT$", "").strip()
    try:
        return float(text)
    except ValueError:
        return 0.0


def format_money(value) -> str:
    """JavaScript `Number(x).toLocaleString()`：千分位、整數不帶小數。"""
    number = parse_number(value)
    if number == int(number):
        return f"{int(number):,}"
    return f"{number:,.3f}".rstrip("0").rstrip(".")


def format_minguo(value, include_day: bool) -> str:
    """GAS `formatMinguoDate()`：115.09.14（含日）或 115.09（月份）；解析不了照原樣。"""
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y-%m", "%Y/%m"):
        try:
            d = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    else:
        return text
    return f"{d.year - 1911}.{d.month:02d}.{d.day:02d}" if include_day else f"{d.year - 1911}.{d.month:02d}"


class Org:
    """員工主管組織表（照 GAS OrgService 的欄位位置讀）。rows 是 dict（表頭→值），headers 是表頭順序。"""

    def __init__(self, rows: list, headers: list):
        self.rows = [_row(r, headers) for r in rows]

    def supervisors(self, applicant_line_id: str, applicant_name: str) -> list:
        """GAS `getSupervisorsByApplicantUserId()`（不含「組織表沒設定就退回系統管理員」那一段）。"""
        line_map = {}
        for row in self.rows:
            name, line_id = _cell(row, ORG_NAME), _cell(row, ORG_LINE_ID)
            if name and line_id and _LINE_ID.match(line_id):
                line_map[name] = line_id
        uid, uname = (applicant_line_id or "").strip().upper(), (applicant_name or "").strip()
        for row in self.rows:
            if (uid and _cell(row, ORG_LINE_ID).upper() == uid) or (uname and _cell(row, ORG_NAME) == uname):
                names = [n for n in split_multi(_cell(row, ORG_SUP_NAMES)) if n]
                line_ids = [n for n in split_multi(_cell(row, ORG_SUP_LINE_IDS)) if n]
                emails = [n for n in split_multi(_cell(row, ORG_SUP_EMAILS)) if n]
                result = []
                for k, raw in enumerate(line_ids):
                    lid = re.sub(r"[^a-zA-Z0-9_-]", "", raw)
                    if _LINE_ID.match(lid):
                        result.append({
                            "name": names[k] if k < len(names) else (names[0] if names else "審核主管"),
                            "line_id": lid,
                            "email": emails[k] if k < len(emails) else (emails[0] if emails else ""),
                        })
                if not result:
                    for k, sup_name in enumerate(names):
                        if sup_name in line_map:
                            result.append({
                                "name": sup_name,
                                "line_id": line_map[sup_name],
                                "email": emails[k] if k < len(emails) else (emails[0] if emails else ""),
                            })
                return result
        return []

    def binding(self, applicant_name: str) -> str:
        """GAS `getEmployeeBindingByName()`：組織表第一列同名的人，LINE ID 格式正確才算已綁定。回傳 LINE ID 或空字串。"""
        name = (applicant_name or "").strip()
        for row in self.rows:
            if name and _cell(row, ORG_NAME) == name:
                line_id = _cell(row, ORG_LINE_ID)
                return line_id if _LINE_ID.match(line_id) else ""
        return ""

    def applicant_email(self, applicant_name: str, applicant_line_id: str) -> str:
        """GAS `findApplicantEmail()` 的三段順序：員工 Email 欄 → 申請人當主管時的主管 Email → 同列任何 Email。"""
        uname, uid = (applicant_name or "").strip(), (applicant_line_id or "").strip().upper()

        def mine(row):
            return (uname and _cell(row, ORG_NAME) == uname) or (uid and _cell(row, ORG_LINE_ID).upper() == uid)

        for row in self.rows:
            if mine(row):
                own = _cell(row, ORG_EMP_EMAIL)
                if _EMAIL.match(own):
                    return own
                break
        for row in self.rows:
            names, emails = split_multi(_cell(row, ORG_SUP_NAMES)), split_multi(_cell(row, ORG_SUP_EMAILS))
            if _cell(row, ORG_SUP_NAMES) and _cell(row, ORG_SUP_EMAILS):
                for k, n in enumerate(names):
                    if n == uname and k < len(emails) and _EMAIL.match(emails[k]):
                        return emails[k]
        for row in self.rows:
            if mine(row):
                for c, value in enumerate(row):
                    if c not in (ORG_SUP_EMAILS, ORG_EMP_EMAIL) and _EMAIL.match(value.strip()):
                        return value.strip()
        return ""

    def name_of(self, line_id: str) -> str:
        lid = (line_id or "").strip()
        for row in self.rows:
            if lid and _cell(row, ORG_LINE_ID) == lid:
                return _cell(row, ORG_NAME)
        return ""


def build_record(fields: dict, headers: list, org: Org) -> dict:
    row = _row(fields, headers)
    record = {key: _cell(row, index) for key, index in COL.items()}
    sups = org.supervisors(record["applicant_line_id"], record["applicant_name"])
    record["supervisor_email"] = ",".join(s["email"] for s in sups if s["email"])
    record["applicant_email"] = org.applicant_email(record["applicant_name"], record["applicant_line_id"])
    record["approved_supervisor_name"] = org.name_of(record["approved_supervisor"]) or record["approved_supervisor"]
    return record


def recipients(record: dict, hr_emails: str = None) -> list:
    """財會信箱（`SALARY_HR_ACCOUNTING_EMAILS`，對應 GAS 的 HR_ACCOUNTING_EMAILS）＋主管＋申請人，去重複。"""
    hr_emails = SALARY_HR_ACCOUNTING_EMAILS if hr_emails is None else hr_emails
    result = []
    for source in (hr_emails, record.get("supervisor_email"), record.get("applicant_email")):
        for email in split_multi(source):
            if email and _EMAIL.match(email) and email not in result:
                result.append(email)
    return result


def subject(record: dict) -> str:
    return f"【薪資補款單 - 審核通過】{record['name']} - {record['vendor']} (單號: {record['salary_id']})"


_STYLE = """
        body { font-family: "Microsoft JhengHei", "PingFang TC", Arial, sans-serif; background-color: #f8fafc; color: #1e293b; margin: 0; padding: 20px; }
        .container { max-width: 720px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); }
        .header { background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%); color: #ffffff; padding: 24px; text-align: center; }
        .header h1 { margin: 0 0 6px 0; font-size: 20px; font-weight: bold; letter-spacing: 1px; }
        .header p { margin: 0; font-size: 12px; opacity: 0.9; }
        .content { padding: 24px; }
        .section-title { font-size: 14px; font-weight: bold; color: #0f172a; margin: 18px 0 10px 0; border-left: 4px solid #0284c7; padding-left: 8px; }
        .info-table { width: 100%; border-collapse: collapse; margin-bottom: 20px; font-size: 13px; }
        .info-table th { background-color: #f1f5f9; color: #475569; padding: 8px 12px; text-align: left; width: 25%; border: 1px solid #e2e8f0; }
        .info-table td { padding: 8px 12px; border: 1px solid #e2e8f0; color: #1e293b; }
        .summary-box { background-color: #0f172a; color: #ffffff; border-radius: 6px; padding: 16px; margin: 20px 0; display: table; width: 100%; box-sizing: border-box; }
        .summary-cell { display: table-cell; width: 33.33%; text-align: center; vertical-align: middle; }
        .summary-label { font-size: 11px; color: #94a3b8; margin-bottom: 4px; }
        .summary-val { font-size: 16px; font-weight: bold; }
        .val-earn { color: #34d399; }
        .val-deduct { color: #fb7185; }
        .val-net { color: #fcd34d; font-size: 20px; }
        .footer { background-color: #f8fafc; padding: 16px; text-align: center; font-size: 11px; color: #64748b; border-top: 1px solid #e2e8f0; }
"""


def email_html(record: dict, image_src: str = "", image_link: str = "") -> str:
    """image_src：信件內嵌照片的來源（寄信時是 `cid:salaryProofImg`，預覽時是平台照片網址）；空字串＝沒有照片。
    image_link：「開啟高畫質原圖」按鈕連到哪裡（舊單是 Drive 網址；沒有就不放按鈕）。"""
    e = {k: html.escape(str(v or "")) for k, v in record.items()}
    image_section = ""
    if image_src:
        link = ""
        if image_link:
            link = f"""
              <div style="margin-top: 10px;">
                <a href="{html.escape(image_link)}" target="_blank" style="display: inline-block; padding: 6px 14px; background-color: #0284c7; color: #ffffff; text-decoration: none; border-radius: 4px; font-size: 12px; font-weight: bold;">
                  🔗 開啟 Google 雲端檢視高畫質原圖
                </a>
              </div>"""
        image_section = f"""
            <div class="section-title">二、補款佐證單據與圖檔</div>
            <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; text-align: center; margin-bottom: 20px;">
              <img src="{html.escape(image_src)}" alt="補款佐證圖檔" style="max-width: 100%; max-height: 480px; border-radius: 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.08);" />{link}
            </div>"""
    pay_date = format_minguo(record["pay_date"], True) if record["pay_date"] else "尚未指定"
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>{_STYLE}</style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>材霈有限公司 - 薪資補款審核通過通知</h1>
      <p>補款單號：{e['salary_id']} ｜ 簽核狀態：已核准</p>
    </div>
    <div class="content">
      <div class="section-title">一、基本資料與請款明細</div>
      <table class="info-table">
        <tr><th>廠商 / 店家</th><td><b style="color:#0284c7;">{e['vendor']}</b></td><th>補款員工姓名</th><td><b>{e['name']}</b></td></tr>
        <tr><th>身分證字號</th><td>{e['id_card']}</td><th>駐廠姓名</th><td>{e['applicant_name'] or '同仁'}</td></tr>
        <tr><th>補款方式</th><td><b>{e['pay_type']}</b></td><th>是否可請款</th><td><span style="color:#059669; font-weight:bold;">{e['is_claimable']}</span></td></tr>
        <tr><th>申請日期</th><td>{html.escape(format_minguo(record['apply_date'], True))}</td><th>付款日期</th><td>{html.escape(pay_date)}</td></tr>
        <tr><th>匯費</th><td>NT$ {format_money(record['remit_fee'])}</td><th>補請款月份</th><td>{html.escape(format_minguo(record['compensate_month'], False))}</td></tr>
        <tr><th>備註說明</th><td colspan="3" style="color:#b91c1c; font-weight:600;">{e['notes'] or '無'}</td></tr>
      </table>
      <div class="summary-box">
        <div class="summary-cell">
          <div class="summary-label">應領小計 (加項總額)</div>
          <div class="summary-val val-earn">NT$ {format_money(record['total_earnings'])}</div>
        </div>
        <div class="summary-cell" style="border-left: 1px solid #334155; border-right: 1px solid #334155;">
          <div class="summary-label">應扣小計 (扣項總額)</div>
          <div class="summary-val val-deduct">NT$ {format_money(record['total_deductions'])}</div>
        </div>
        <div class="summary-cell">
          <div class="summary-label">實補金額 (撥款總計)</div>
          <div class="summary-val val-net">NT$ {format_money(record['net_total'])}</div>
        </div>
      </div>
      {image_section}
      <div style="margin-top: 16px; font-size: 11px; color: #64748b;">
        核准主管：{e['approved_supervisor_name'] or '系統管理者'} ｜ 核准時間：{e['approved_time']}
      </div>
    </div>
    <div class="footer">
      此信件由 Tsaipei 材霈招募與薪資管理系統自動發出，請勿直接回覆。
    </div>
  </div>
</body>
</html>
"""


def pdf_filename(record: dict) -> str:
    return f"薪資補款存查單_{record['salary_id']}.pdf"


def build_docx(record: dict) -> bytes:
    """GAS `buildSalaryPdfBlob()` 的版面：標題、一、基本資料（4 欄表）＋備註、二、金額明細（3 格）、三、簽核紀錄。"""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    def rgb(hex_color):
        return RGBColor.from_string(hex_color.lstrip("#").upper())

    def shade(cell, hex_color):
        tc_pr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), hex_color.lstrip("#").upper())
        tc_pr.append(shd)

    def para(container, text, size, color, bold=False, before=0, after=4, align=None):
        p = container.add_paragraph()
        run = p.add_run(text)
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = rgb(color)
        run.font.name = "Noto Sans CJK TC"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "Noto Sans CJK TC")
        p.paragraph_format.space_before = Pt(before)
        p.paragraph_format.space_after = Pt(after)
        if align:
            p.alignment = align
        return p

    def fill(cell, text, size, color, bold=False, bg=None, align=None):
        cell.text = ""
        p = cell.paragraphs[0]
        run = p.add_run(text)
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = rgb(color)
        run.font.name = "Noto Sans CJK TC"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "Noto Sans CJK TC")
        if align:
            p.alignment = align
        if bg:
            shade(cell, bg)
        return p

    label_bg, label_text, value_text, notes_color = "#f1f5f9", "#475569", "#0f172a", "#b91c1c"
    doc = Document()
    for section in doc.sections:
        section.top_margin = section.bottom_margin = Pt(36)
        section.left_margin = section.right_margin = Pt(40)

    first = doc.paragraphs[0] if doc.paragraphs else doc.add_paragraph()
    first.text = ""
    run = first.add_run("材霈有限公司")
    run.font.size = Pt(10)
    run.font.color.rgb = rgb("#64748b")
    run.font.name = "Noto Sans CJK TC"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Noto Sans CJK TC")
    para(doc, "薪資補款申請存查單", 20, "#0f172a", bold=True)
    para(doc, f"補款單號：{record['salary_id']}　｜　簽核狀態：{record['review_status'] or '已核准'}", 11, label_text)

    para(doc, "一、基本資料與請款明細", 13, "#0f172a", bold=True, before=14, after=6)
    pay_date = format_minguo(record["pay_date"], True) if record["pay_date"] else "尚未指定"
    info_rows = [
        ["廠商 / 店家", record["vendor"] or "-", "補款員工姓名", record["name"] or "-"],
        ["身分證字號", record["id_card"] or "-", "駐廠姓名", record["applicant_name"] or "同仁"],
        ["補款方式", record["pay_type"] or "-", "是否可請款", record["is_claimable"] or "-"],
        ["申請日期", format_minguo(record["apply_date"], True), "付款日期", pay_date],
        ["匯費", f"NT$ {format_money(record['remit_fee'])}", "補請款月份", format_minguo(record["compensate_month"], False)],
    ]
    table = doc.add_table(rows=len(info_rows), cols=4)
    table.style = "Table Grid"
    for r, cols in enumerate(info_rows):
        for c, text in enumerate(cols):
            label = c in (0, 2)
            fill(table.cell(r, c), str(text), 10.5, label_text if label else value_text, bg=label_bg if label else None)

    notes = doc.add_table(rows=1, cols=2)
    notes.style = "Table Grid"
    fill(notes.cell(0, 0), "備註說明", 10.5, label_text, bg=label_bg)
    fill(notes.cell(0, 1), record["notes"] or "無", 10.5, notes_color, bold=True)

    para(doc, "二、金額明細", 13, "#0f172a", bold=True, before=16, after=6)
    summary = doc.add_table(rows=1, cols=3)
    summary.style = "Table Grid"
    cells = [
        ("應領小計（加項總額）", record["total_earnings"], "#f0fdf7", "#059669"),
        ("應扣小計（扣項總額）", record["total_deductions"], "#fff5f6", "#e11d48"),
        ("實補金額（撥款總計）", record["net_total"], "#fffbeb", "#b45309"),
    ]
    for c, (label, value, bg, color) in enumerate(cells):
        cell = summary.cell(0, c)
        fill(cell, label, 9, "#64748b", bg=bg, align=WD_ALIGN_PARAGRAPH.CENTER)
        para(cell, f"NT$ {format_money(value)}", 14, color, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)

    para(doc, "三、簽核紀錄", 13, "#0f172a", bold=True, before=16, after=6)
    approval = doc.add_table(rows=1, cols=4)
    approval.style = "Table Grid"
    cols = ["核准主管", record["approved_supervisor_name"] or "系統管理者", "核准時間", record["approved_time"] or "-"]
    for c, text in enumerate(cols):
        label = c in (0, 2)
        fill(approval.cell(0, c), text, 10.5, label_text if label else value_text, bg=label_bg if label else None)

    para(doc, "此文件由系統於核准當下自動產生，作為薪資補款留底憑證，補款佐證圖檔請詳見核准信件附件。", 9, "#94a3b8", before=20)

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def build_pdf(record: dict):
    """回傳 PDF bytes；轉檔失敗回傳 None（寄信時跟 GAS 一樣：少一個附件，信照寄）。"""
    from services.docx_pdf_conversion import convert_docx_to_pdf

    return convert_docx_to_pdf(build_docx(record), log_prefix="[薪資補款存查單轉PDF失敗]")
