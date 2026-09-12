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

**合約版本**：目前有兩個版本，甲乙雙方欄位/合約期間/撤換條款/匯款日這些
主文完全共用同一套排版跟 Jinja 標籤，只有附件一報價表格的結構不一樣，
所以是兩個獨立的 master template 檔案：
- ``hourly_flat_rate``（時薪一口價）：員工薪資／管理費由專員自行填入
  單一數值，簡單 3 欄費率表。
- ``actual_paid``（實支實付）：使用者提供的真實「實支實付」報價表格
  （薪資/加班費/法定項目/員工福利都固定寫「實支實付」，只有「服務費－
  全程派遣」那一格是空白的 `service_fee` 欄位讓專員自行填寫，例如
  「人員薪資的15%」）。
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

# 合約版本代碼 -> 顯示名稱／對應「專案合約維護」的簽約模式選項／各自的
# master template 檔案。兩個版本的合約主文（甲乙雙方欄位、合約期間、
# 撤換條款、匯款日那幾條）完全共用同一套排版跟 Jinja 標籤，只有附件一
# 報價表格的結構不一樣（時薪一口價是簡單 3 欄，實支實付是使用者提供的
# 複雜報價表格，見 HANDOFF.md 的說明），所以是兩個獨立的 docx 檔案，不是
# 同一份範本裡切換段落。之後如果要再加新版本，一樣是在這裡加一筆＋準備
# 對應的 master template。
CONTRACT_VERSIONS = {
    "hourly_flat_rate": {
        "label": "時薪一口價",
        "project_contract_mode": "一口價",
        "template_path": os.path.join(_ASSETS_DIR, "master_template_hourly_flat_rate.docx"),
    },
    "actual_paid": {
        "label": "實支實付",
        "project_contract_mode": "實支實付",
        "template_path": os.path.join(_ASSETS_DIR, "master_template_actual_paid.docx"),
    },
}
DEFAULT_CONTRACT_VERSION = "hourly_flat_rate"

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
    sign_date: date,
    contract_start_date: date,
    contract_end_date: date,
    replace_notice_days: str,
    severance_payer: str,
    remit_day: str,
    contract_version: str = DEFAULT_CONTRACT_VERSION,
    hourly_wage: str = "",
    management_fee: str = "",
    service_fee: str = "",
) -> bytes:
    """套版產生 Word 檔內容（bytes）。party_a／party_b 都是
    ``{"name", "representative", "address", "tax_id", "phone"}`` 這個形狀
    的 dict——party_a 是專員填的/查到的甲方資料，party_b 是從 `/companies`
    選出來的公司資料。``contract_version`` 決定套哪一份 master template
    （見 `CONTRACT_VERSIONS`）：``hourly_flat_rate`` 用 `hourly_wage`／
    `management_fee`，``actual_paid`` 用 `service_fee`，不屬於當次版本的
    參數會被忽略（呼叫端只要照表單實際欄位傳就好，不用自己篩選）。"""
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
        "sign_date_roc": roc_date_string(sign_date),
        "contract_start_date_roc": roc_date_string(contract_start_date),
        "contract_end_date_roc": roc_date_string(contract_end_date),
        "replace_notice_days": replace_notice_days,
        "severance_payer": severance_payer,
        "remit_day": remit_day,
        "hourly_wage": hourly_wage,
        "management_fee": management_fee,
        "service_fee": service_fee,
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
    sign_date: str,
    contract_start_date: str,
    contract_end_date: str,
    replace_notice_days: str,
    severance_payer: str,
    remit_day: str,
    blob_path: str,
    hourly_wage: str = "",
    management_fee: str = "",
    service_fee: str = "",
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
