"""派遣契約產生器（/dispatch-contracts）：專員每次指派同仁去不同客戶工作時，
把會隨客戶變動的條件（工作地址/內容、班別薪資表格、休假/加班/服裝押金等
條文段落）填進表單，套進公司固定的派遣契約範本，產生一份 Word 檔給專員
下載，同時把這次填的內容跟產出的檔案存進材霈平台自己的 Firestore／GCS，
不寄信、不轉送外部系統。

**這個功能不處理「特定一位派遣員工」的資料**——範本裡原本就有的
``${com_full_name}``／``${cus_name}``／``${rec_name}``／``${rec_id}``／
``${rec_rdate}``／``${pap_sign}``／``${com_leader}``／``${com_id}``／
``${com_address}`` 這 9 個公司/客戶/員工欄位，是材霈另一套簽署系統的既有
公式，這裡完全不會去讀取或取代它們，套版時只處理下面這些「客戶條件」：

- 工作地址／工作內容／結薪週期：專員每次自由輸入的文字。
- 班別薪資表格：通用 5 欄（職稱／工作時間／時薪／工時獎金／加班），可以
  新增多列（同一客戶同時有好幾種班別、薪資都不同時使用）。專員可以勾選
  這次契約要用哪幾欄——**沒被勾選的欄位，產出的表格裡該欄一律顯示「－」，
  不會把整欄從表格拿掉**（動態拿掉欄位在 Word 表格裡做起來非常脆弱，
  用「－」表示不適用是同樣清楚、風險低很多的做法，這是刻意的簡化，如果
  之後真的需要動態拿掉整欄，再回來調整）。
- 條文段落：拆成「休假/請假制度」「加班/津貼/團保說明」「服裝儀容/門禁卡/
  押金規定」「福利說明」「到職準備事項」5 段，每段都有預設好的標準文字
  （見 ``CLAUSE_DEFAULTS``，抄自使用者提供的真實契約範本），專員可以直接
  用預設，也可以依這次客戶的需求修改。

套版技術用 ``docxtpl``（Jinja2 語法 ``{{ }}``／``{% %}``，跟既有的
``${...}`` 系統公式是完全不同的括號寫法，兩者不會互相誤判）。master
template 存在 ``assets/dispatch_contracts/master_template.docx``，是拿
使用者提供的 pchome 客戶範本重新整理過的版本——**固定法條文字、所有
``${...}`` 公式都跟原本一字不差**，只有「工作地址/工作內容/薪資班別表格/
條文段落」這個原本混雜排版的區塊，改成上下堆疊的清楚版面（原本是同一列
裡班別跟條文說明用合併儲存格交錯排在一起，沒辦法在「班別列數會變動」的
前提下安全維持，所以刻意重新排版，文字內容不變）。

**Word 排版預覽（2026-09-11 新增）**：產生 Word 檔的同時，另外用
LibreOffice（``soffice --convert-to pdf``，見 ``convert_docx_to_pdf()``）
轉一份 PDF 存起來，讓 `/dispatch-contracts` 列表頁可以直接內嵌預覽真正的
排版，不用下載就能看。**這一步刻意設計成失敗容錯**：轉檔需要 Cloud Run
的容器裡裝有 LibreOffice（見專案根目錄 `Dockerfile`），如果轉檔失敗（逾時、
找不到 ``soffice``、輸出檔案不存在等任何原因），``convert_docx_to_pdf()``
回傳 ``None``，呼叫端（`dispatch_contract_routes.py`）不會讓整個送出流程
失敗，只是那一筆紀錄沒有 PDF、看不到預覽，Word 檔案照樣正常產生/下載/
存檔。開發階段沒有在本機的沙盒環境驗證過這個轉檔步驟一定能成功（沙盒
環境本身的 LibreOffice 安裝有問題，跟這裡的程式碼無關），**上線後第一次
真的產生契約時，要麻煩使用者確認一下列表頁看不看得到預覽**，如果看不到、
但 Word 檔案下載正常，去 Cloud Run 的 log 找
`[派遣契約 PDF 轉檔失敗]` 開頭的訊息，會有實際的失敗原因。
"""
import io
import os
from datetime import datetime, timezone

from docxtpl import DocxTemplate

import platform_accounts
from platform_db import get_db
from services.docx_pdf_conversion import convert_docx_to_pdf as _convert_docx_to_pdf

CONTRACTS_COLLECTION = "dispatch_contracts"

# 用 __file__ 算出repo根目錄的絕對路徑，不依賴呼叫時的工作目錄（Cloud Run
# 用 Dockerfile 的 WORKDIR /app 執行沒差，但本地測試/其他呼叫方式的工作
# 目錄不一定固定，用絕對路徑比較保險）。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASTER_TEMPLATE_PATH = os.path.join(_REPO_ROOT, "assets", "dispatch_contracts", "master_template.docx")

# 班別薪資表格的通用欄位——順序就是表格欄位順序。
SHIFT_COLUMNS = [
    {"code": "title", "label": "職稱"},
    {"code": "hours", "label": "工作時間"},
    {"code": "wage", "label": "時薪"},
    {"code": "bonus", "label": "工時獎金"},
    {"code": "overtime", "label": "加班"},
]
SHIFT_COLUMN_CODES = [c["code"] for c in SHIFT_COLUMNS]

_BLANK_CELL = "－"

# 條文段落的預設標準文字，抄自使用者提供的真實契約範本（pchome 客戶那份），
# 專員可以直接沿用或依這次客戶需求修改。
CLAUSE_DEFAULTS = {
    "leave_policy": (
        "排休。"
        "※請假需依乙方規範辦理：\n"
        "1.事假-須於三日前向丙方及乙方申請，並依照丙方規範完成簽核。\n"
        "2.病假-需於至少上班前一小時內自行完成請假流程，並於下一次上班日完成請假簽核。\n"
        "3.未完成請假流程：視為無故未出勤、失聯，將以曠職論。當月有無故曠職或請假超過16小時，"
        "將取消當月所有獎金！\n"
        "※以下行為無法提供獎金：未滿三日離職未告知未完成離職程序。"
    ),
    "overtime_allowance_note": (
        "▲加班依訂單狀況(需要配合加班)。\n"
        "▲津貼依公司公告為主(每個檔期、活動、時段都不一樣)。\n"
        "團保：自負額210元/月(因應薪資及意外事件，理賠費率會有調整，若有調整會通知)。\n"
        "※本契約所列有關要派單位提供津貼/獎金之計算方式,依據要派單位於派遣人員實際出勤/工作表現"
        "等綜合績效評估結果發放。"
    ),
    "dress_deposit_note": (
        "臨時綠色背心/反光背心/臨時門禁卡/二合一卡/正式門禁卡/郵政感應卡/Etag鎖匙圈/停車證-"
        "汽、機車/紅色Polo衫/藍色Polo/衫冬季黑色背心/置物櫃鑰匙，自薪資給付押金1000元，"
        "離職歸還無破損退回全額1000元。※若有損壞無法退還押金。"
    ),
    "benefits_note": "※福利：依法投保(勞保/團保/勞退)，油資/保養補貼/推薦獎金600元。",
    "onboarding_note": (
        "報到需備雙證件、駕照，薪資一律匯入【永豐銀行】薪資帳戶(其他銀行自行負擔匯款手續費30元)。"
        "次月十號發薪，遇假日順延，如有其他因素提早/延後發薪，會另行通知。"
        "連續長達五天無班者，確認離職日期者請主動告知。"
    ),
}
CLAUSE_LABELS = {
    "leave_policy": "休假/請假制度",
    "overtime_allowance_note": "加班/津貼/團保說明",
    "dress_deposit_note": "服裝儀容/門禁卡/押金規定",
    "benefits_note": "福利說明",
    "onboarding_note": "到職準備事項",
}
CLAUSE_ORDER = ["leave_policy", "overtime_allowance_note", "dress_deposit_note", "benefits_note", "onboarding_note"]


def contracts_ref():
    return get_db().collection(CONTRACTS_COLLECTION)


def build_shift_rows(raw_rows: list, enabled_columns: list) -> list:
    """把表單送出的班別列資料（每列一個 dict，key 是 SHIFT_COLUMN_CODES）
    整理成套版用的資料：沒被勾選使用的欄位一律填「－」，忽略完全空白
    （所有欄位都沒填）的列。"""
    enabled = set(enabled_columns) & set(SHIFT_COLUMN_CODES)
    rows = []
    for raw in raw_rows:
        row = {}
        has_value = False
        for code in SHIFT_COLUMN_CODES:
            value = (raw.get(code) or "").strip()
            if code not in enabled:
                row[code] = _BLANK_CELL
            elif value:
                row[code] = value
                has_value = True
            else:
                row[code] = _BLANK_CELL
        if has_value:
            rows.append(row)
    return rows


def render_contract_docx(*, work_address: str, work_content: str, pay_cycle: str, shifts: list, clauses: dict) -> bytes:
    """套版產生 Word 檔內容（bytes）。clauses 只需要包含這次要覆蓋預設值的
    段落，沒給的段落用 CLAUSE_DEFAULTS 補上——呼叫端（routes）通常會先把
    表單送出的完整 5 段都準備好再呼叫這裡，這個 fallback 主要是保險，避免
    表單漏了某個段落時整份文件缺一段文字。"""
    context = {
        "work_address": work_address,
        "work_content": work_content,
        "pay_cycle": pay_cycle,
        "shifts": shifts,
    }
    for key in CLAUSE_ORDER:
        context[key] = clauses.get(key) or CLAUSE_DEFAULTS[key]

    tpl = DocxTemplate(MASTER_TEMPLATE_PATH)
    tpl.render(context)
    buffer = io.BytesIO()
    tpl.save(buffer)
    return buffer.getvalue()


def convert_docx_to_pdf(docx_bytes: bytes) -> bytes:
    """把 Word 檔內容轉成 PDF（給列表頁內嵌預覽用），失敗回傳 ``None``
    （逾時、找不到 ``soffice``、輸出檔案不存在都算失敗），呼叫端不應該讓
    這一步的失敗擋住整個契約產生流程——見本檔案開頭「Word 排版預覽」的
    說明。實際轉檔邏輯在 ``services/docx_pdf_conversion.py``（跟
    ``client_contract_service.py`` 共用），這裡只是保留原本的函式名稱/
    匯入路徑，呼叫端（``dispatch_contract_routes.py``、既有測試）不用改。"""
    return _convert_docx_to_pdf(docx_bytes, log_prefix="[派遣契約 PDF 轉檔失敗]")


def save_submission(*, submitted_by: str, client_name: str, work_address: str, work_content: str,
                     pay_cycle: str, enabled_columns: list, shifts: list, clauses: dict, blob_path: str,
                     pdf_blob_path: str = "") -> dict:
    data = {
        "submitted_by": submitted_by,
        "client_name": client_name,
        "work_address": work_address,
        "work_content": work_content,
        "pay_cycle": pay_cycle,
        "enabled_columns": enabled_columns,
        "shifts": shifts,
        "clauses": clauses,
        "blob_path": blob_path,
        "pdf_blob_path": pdf_blob_path,
        "created_at": datetime.now(timezone.utc),
    }
    doc_ref = contracts_ref().document()
    doc_ref.set(data)
    data["id"] = doc_ref.id
    return data


def _doc_to_dict(doc) -> dict:
    data = doc.to_dict() or {}
    data["id"] = doc.id
    return data


def list_submissions(limit: int = 200) -> list:
    """依送出時間新到舊列出「全部」紀錄，不分送出人——內部用的底層函式，
    不會過濾可見範圍。要給使用者看的清單一律要透過 `list_visible_submissions()`
    做權限過濾，這個函式只給「本來就該看全部」的情境用（例如平台管理員、
    或是 `list_recent_client_names()` 這種只是要抓客戶名稱、不會把整筆紀錄
    顯示出來的用途）。"""
    docs = contracts_ref().order_by("created_at", direction="DESCENDING").limit(limit).stream()
    return [_doc_to_dict(d) for d in docs]


def can_view_submission(viewer_account: dict, record: dict) -> bool:
    """判斷 viewer_account 能不能看到這筆紀錄（列表頁、下載、預覽都要走這個
    檢查）：只有送出者本人、送出者的主管（`manager_usernames` 指向送出者的
    帳號才算）、或是全平台管理員可以看——2026-09-11 依使用者要求收斂權限，
    原本是任何有這個模組權限的帳號都能看到全部紀錄。"""
    if viewer_account.get("is_platform_admin"):
        return True
    viewer_username = viewer_account.get("username")
    submitted_by = record.get("submitted_by")
    if submitted_by == viewer_username:
        return True
    submitter = platform_accounts.get_account(submitted_by) if submitted_by else None
    if submitter and viewer_username in (submitter.get("manager_usernames") or []):
        return True
    return False


def list_visible_submissions(viewer_account: dict, limit: int = 200) -> list:
    """依送出時間新到舊列出 viewer_account 有權限看到的紀錄：自己送出的、
    自己是送出者的主管、或是全平台管理員——不符合以上任何一種身分的話，
    其他同仁送出的紀錄一律看不到（2026-09-11 使用者要求收斂權限，原本是
    任何有這個模組權限的帳號都能看到全部紀錄，方便同仁互相接手客戶）。"""
    if viewer_account.get("is_platform_admin"):
        return list_submissions(limit=limit)
    records = list_submissions(limit=limit)
    return [record for record in records if can_view_submission(viewer_account, record)]


def get_submission(submission_id: str):
    snapshot = contracts_ref().document(submission_id).get()
    if not snapshot.exists:
        return None
    return _doc_to_dict(snapshot)


def list_recent_client_names(limit: int = 500) -> list:
    """給表單「客戶名稱」欄位的自動完成用：從最近的紀錄裡取不重複的客戶
    名稱，新到舊排序——刻意不另外建一份客戶主檔，先用歷史紀錄反查就夠用，
    真的需要更完整的客戶管理再回來加。"""
    seen = []
    for record in list_submissions(limit=limit):
        name = record.get("client_name")
        if name and name not in seen:
            seen.append(name)
    return seen


def delete_submission(submission_id: str):
    """契約作廢時整筆刪掉——跟 client_contract_service.py 的 delete_
    submission() 同一種寫法：呼叫端（routes）要先用 `can_view_submission()`
    確認這個帳號真的看得到這筆紀錄才能呼叫這裡，這個函式本身不重複做
    權限檢查。只刪 Firestore 這筆文件，GCS 上的 Word/PDF 檔案由呼叫端
    另外呼叫 storage 那邊的刪除函式清掉，這裡不知道、也不需要知道
    儲存層的細節。"""
    contracts_ref().document(submission_id).delete()
