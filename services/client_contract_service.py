"""合約產生器（/client-contracts）：材霈業務跟客戶公司簽的「人力派遣服務
合約書」，甲方（客戶公司）條件、乙方（材霈旗下派遣公司）資料、合約期間、
撤換條款、匯款日、費率都會隨每次簽約不同，這裡讓同仁填一次表單就套版
產生 Word 契約檔下載，同時把這次填的內容跟產出的檔案存進材霈平台自己的
Firestore／GCS。

**這份合約書跟「派遣契約產生器」（/dispatch-contracts）是完全不同的兩份
文件**：那份是材霈跟「個別派遣員工」的契約（範本裡有另一套簽署系統要讀的
``${...}`` 公式），這份是材霈（乙方，旗下某一家派遣公司）跟「客戶公司」
（甲方）之間的企業對企業服務合約，兩者的甲乙雙方定義完全不一樣，資料
互不相通。

**乙方資料**：從 `/companies` 公司主檔挑選，直接帶出名稱/代表人/統一編號/
電話/地址（`platform_companies.py`），材霈旗下有 10 家派遣公司牌照，不同
客戶合約可能用不同牌照公司簽約，所以做成下拉選單而不是寫死一家。

**甲方資料**：專員輸入客戶公司名稱或統一編號，依序查「經濟部商工開放
平台」「g0v 公司資料庫」兩個免費外部服務（`services/company_registry_
lookup.py`），查得到就自動帶入，查不到就手動輸入全部欄位——這兩個查詢
服務都是失敗容錯設計，不會擋住合約產生流程。

**合約版本**：目前有三個版本：
- ``hourly_flat_rate``（時薪一口價）跟 ``actual_paid``（實支實付）是同一種
  「人力派遣服務合約書」，甲乙雙方欄位/合約期間/撤換條款/匯款日這些主文
  完全共用同一套排版跟 Jinja 標籤，只有附件一報價表格的結構不一樣：
  時薪一口價是員工薪資／管理費由專員自行填入單一數值的簡單 3 欄費率表，
  實支實付是使用者提供的真實報價表格（薪資/加班費/法定項目/員工福利都
  固定寫「實支實付」，只有「服務費－全程派遣」那一格是空白的
  `service_fee` 欄位讓專員自行填寫，例如「人員薪資的15%」）。
- ``white_collar_referral``（白領代招，2026-09-12 新增）是完全不同的
  「人力代招服務合約書」，條文結構（第一條～第九條）、用字都跟前兩個
  版本不一樣，不是報價表格代換而已，所以主文也是獨立的一份 master
  template，不共用前兩者的 Jinja 標籤。這個版本也沒有獨立的「簽約日期」
  欄位——合約書末尾的簽署日期就是合約起始日期本身，不像前兩版另外有
  `sign_date`；也沒有撤換條款（`replace_notice_days`／`severance_payer`）
  這件事，只有 `remit_day`（匯款截止日，跟前兩版意義相同）跟這個版本
  獨有的 `fee_amount`（每人每月服務費，自由文字，例如「二千五百元整」）
  跟 `service_months`（附件一報價表「收取時間不超過幾個月」，預設12，
  可調整）。`CONTRACT_VERSIONS` 用 ``requires_sign_date``／
  ``requires_severance_clause`` 這兩個布林值標記每個版本各自需要哪些
  共用欄位，`render_contract_docx()`／`client_contract_routes.py` 的表單
  驗證都照這兩個旗標決定要不要收、要不要擋。
之後如果要再加新版本，一樣是在 `CONTRACT_VERSIONS` 加一個版本代碼＋
準備對應的 master template 檔案，不用改整個資料結構。

**權限與可見範圍**：模組代碼 `client_contracts`（`platform_accounts.
MODULES`），跟派遣契約產生器一樣，只有送出者本人、送出者的主管
（`manager_usernames`）、或全平台管理員看得到某筆紀錄。

**Word 排版預覽**：產生 Word 檔的同時，另外用 LibreOffice 轉一份 PDF
存起來（見 `services/docx_pdf_conversion.py`，跟派遣契約產生器共用同一支
轉檔工具），失敗容錯設計跟派遣契約產生器一致，轉檔失敗不影響 Word 檔案
本身的產生/下載/存檔。

**跟「專案合約維護」的串接**：這裡產生的合約紀錄可以被 `/project-contracts`
的表單挑選帶入（廠商名稱＝甲方公司名稱、合作類別預設「派遣」、簽約模式
依 `CONTRACT_VERSIONS` 對應），送出成功後這裡的紀錄會標記
`sent_to_project_contracts_at`，避免同仁不小心對同一份合約重複送出——
見 `project_contract_routes.py`。
"""
import io
import os
from datetime import date, datetime, timezone

from docxtpl import DocxTemplate

import platform_accounts
from platform_db import get_db
from services.docx_pdf_conversion import convert_docx_to_pdf as _convert_docx_to_pdf

CONTRACTS_COLLECTION = "client_contracts"

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ASSETS_DIR = os.path.join(_REPO_ROOT, "assets", "client_contracts")

# 合約版本代碼 -> 顯示名稱／對應「專案合約維護」的簽約模式跟合作類別
# 選項／各自的 master template 檔案／這個版本需不需要「簽約日期」跟
# 「撤換條款」這兩組共用欄位。時薪一口價跟實支實付兩個版本的合約主文
# （甲乙雙方欄位、合約期間、撤換條款、匯款日那幾條）完全共用同一套排版跟
# Jinja 標籤，只有附件一報價表格的結構不一樣（時薪一口價是簡單 3 欄，
# 實支實付是使用者提供的複雜報價表格，見 HANDOFF.md 的說明），白領代招則
# 是條文結構完全不同的另一份合約書，三個版本各自獨立一個 docx 檔案，不是
# 同一份範本裡切換段落。之後如果要再加新版本，一樣是在這裡加一筆＋準備
# 對應的 master template。
CONTRACT_VERSIONS = {
    "hourly_flat_rate": {
        "label": "時薪一口價",
        "project_contract_mode": "一口價",
        "project_contract_coop_category": "派遣",
        "template_path": os.path.join(_ASSETS_DIR, "master_template_hourly_flat_rate.docx"),
        "requires_sign_date": True,
        "requires_severance_clause": True,
    },
    "actual_paid": {
        "label": "實支實付",
        "project_contract_mode": "實支實付",
        "project_contract_coop_category": "派遣",
        "template_path": os.path.join(_ASSETS_DIR, "master_template_actual_paid.docx"),
        "requires_sign_date": True,
        "requires_severance_clause": True,
    },
    "white_collar_referral": {
        "label": "白領代招",
        "project_contract_mode": "一口價",
        "project_contract_coop_category": "代招",
        "template_path": os.path.join(_ASSETS_DIR, "master_template_white_collar_referral.docx"),
        "requires_sign_date": False,
        "requires_severance_clause": False,
    },
}
DEFAULT_CONTRACT_VERSION = "hourly_flat_rate"
DEFAULT_SERVICE_MONTHS = "12"

SEVERANCE_PAYER_OPTIONS = ["甲方", "乙方"]
DEFAULT_REPLACE_NOTICE_DAYS = "3"
DEFAULT_REMIT_DAY = "10"


def contracts_ref():
    return get_db().collection(CONTRACTS_COLLECTION)


def roc_date_string(d: date) -> str:
    """西元日期轉民國紀年字串，例如 date(2026, 1, 1) -> '115年01月01日'。"""
    return f"{d.year - 1911}年{d.month:02d}月{d.day:02d}日"


def default_contract_end_date(today: date = None) -> date:
    """合約結束日預設是「使用這個功能當下」那一年的 12/31，同仁可以再手動
    改——2026-09-12 使用者明確要求的預設值。"""
    today = today or date.today()
    return date(today.year, 12, 31)


def render_contract_docx(
    *,
    party_a: dict,
    party_b: dict,
    contract_start_date: date,
    contract_end_date: date,
    remit_day: str,
    sign_date: date = None,
    replace_notice_days: str = "",
    severance_payer: str = "",
    contract_version: str = DEFAULT_CONTRACT_VERSION,
    hourly_wage: str = "",
    management_fee: str = "",
    service_fee: str = "",
    fee_amount: str = "",
    service_months: str = "",
) -> bytes:
    """套版產生 Word 檔內容（bytes）。party_a／party_b 都是
    ``{"name", "representative", "address", "tax_id", "phone"}`` 這個形狀
    的 dict——party_a 是專員填的/查到的甲方資料，party_b 是從 `/companies`
    選出來的公司資料。``contract_version`` 決定套哪一份 master template
    （見 `CONTRACT_VERSIONS`）：``hourly_flat_rate`` 用 `hourly_wage`／
    `management_fee`，``actual_paid`` 用 `service_fee`，``white_collar_
    referral`` 用 `fee_amount`／`service_months`，不屬於當次版本的參數會
    被忽略（呼叫端只要照表單實際欄位傳就好，不用自己篩選）。``sign_date``
    只有 `CONTRACT_VERSIONS[contract_version]["requires_sign_date"]` 是
    True 的版本才會用到，不需要的版本傳 None 即可（模板裡不會引用
    `sign_date_roc` 這個變數，傳了也不影響套版結果）。"""
    context = {
        "party_a_name": party_a["name"],
        "party_a_representative": party_a["representative"],
        "party_a_address": party_a["address"],
        "party_a_tax_id": party_a["tax_id"],
        "party_a_phone": party_a["phone"],
        "party_b_name": party_b["name"],
        "party_b_representative": party_b["representative"],
        "party_b_address": party_b["address"],
        "party_b_tax_id": party_b["tax_id"],
        "party_b_phone": party_b["phone"],
        "sign_date_roc": roc_date_string(sign_date) if sign_date else "",
        "contract_start_date_roc": roc_date_string(contract_start_date),
        "contract_end_date_roc": roc_date_string(contract_end_date),
        "replace_notice_days": replace_notice_days,
        "severance_payer": severance_payer,
        "remit_day": remit_day,
        "hourly_wage": hourly_wage,
        "management_fee": management_fee,
        "service_fee": service_fee,
        "fee_amount": fee_amount,
        "service_months": service_months,
    }
    template_path = CONTRACT_VERSIONS[contract_version]["template_path"]
    tpl = DocxTemplate(template_path)
    tpl.render(context)
    buffer = io.BytesIO()
    tpl.save(buffer)
    return buffer.getvalue()


def convert_docx_to_pdf(docx_bytes: bytes) -> bytes:
    return _convert_docx_to_pdf(docx_bytes, log_prefix="[合約產生器 PDF 轉檔失敗]")


def save_submission(
    *,
    submitted_by: str,
    contract_version: str,
    party_a: dict,
    party_b_company_id: str,
    party_b: dict,
    contract_start_date: str,
    contract_end_date: str,
    remit_day: str,
    blob_path: str,
    sign_date: str = "",
    replace_notice_days: str = "",
    severance_payer: str = "",
    hourly_wage: str = "",
    management_fee: str = "",
    service_fee: str = "",
    fee_amount: str = "",
    service_months: str = "",
    pdf_blob_path: str = "",
) -> dict:
    data = {
        "submitted_by": submitted_by,
        "contract_version": contract_version,
        "party_a_name": party_a["name"],
        "party_a_representative": party_a["representative"],
        "party_a_address": party_a["address"],
        "party_a_tax_id": party_a["tax_id"],
        "party_a_phone": party_a["phone"],
        "party_b_company_id": party_b_company_id,
        "party_b_name": party_b["name"],
        "party_b_representative": party_b["representative"],
        "party_b_address": party_b["address"],
        "party_b_tax_id": party_b["tax_id"],
        "party_b_phone": party_b["phone"],
        "sign_date": sign_date,
        "contract_start_date": contract_start_date,
        "contract_end_date": contract_end_date,
        "replace_notice_days": replace_notice_days,
        "severance_payer": severance_payer,
        "remit_day": remit_day,
        "hourly_wage": hourly_wage,
        "management_fee": management_fee,
        "service_fee": service_fee,
        "fee_amount": fee_amount,
        "service_months": service_months,
        "blob_path": blob_path,
        "pdf_blob_path": pdf_blob_path,
        "sent_to_project_contracts_at": None,
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
    要給使用者看的清單一律要透過 `list_visible_submissions()` 做權限過濾，
    這個函式只給「本來就該看全部」的情境用（平台管理員）。"""
    docs = contracts_ref().order_by("created_at", direction="DESCENDING").limit(limit).stream()
    return [_doc_to_dict(d) for d in docs]


def can_view_submission(viewer_account: dict, record: dict) -> bool:
    """只有送出者本人、送出者的主管（`manager_usernames` 指向送出者的帳號
    才算）、或是全平台管理員可以看——跟派遣契約產生器同一套規則
    （services/dispatch_contract_service.py 的 can_view_submission()）。"""
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
    自己是送出者的主管、或是全平台管理員。"""
    if viewer_account.get("is_platform_admin"):
        return list_submissions(limit=limit)
    records = list_submissions(limit=limit)
    return [record for record in records if can_view_submission(viewer_account, record)]


def get_submission(submission_id: str):
    snapshot = contracts_ref().document(submission_id).get()
    if not snapshot.exists:
        return None
    return _doc_to_dict(snapshot)


def mark_sent_to_project_contracts(submission_id: str):
    """`/project-contracts` 成功把這筆紀錄的檔案送出後呼叫，存一個時間戳
    當作「已送出」標記，避免同仁不小心對同一份合約重複送出——見
    project_contract_routes.py。"""
    contracts_ref().document(submission_id).update({"sent_to_project_contracts_at": datetime.now(timezone.utc)})


def delete_submission(submission_id: str):
    """合約作廢時整筆刪掉——呼叫端（routes）要先用 `can_view_submission()`
    確認這個帳號真的看得到這筆紀錄才能呼叫這裡，這個函式本身不重複做
    權限檢查。只刪 Firestore 這筆文件，GCS 上的 Word/PDF 檔案由呼叫端
    另外呼叫 storage 那邊的刪除函式清掉，這裡不知道、也不需要知道
    儲存層的細節。"""
    contracts_ref().document(submission_id).delete()
