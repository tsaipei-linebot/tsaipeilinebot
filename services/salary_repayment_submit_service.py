"""薪資補款「送出」表單（/me/salary-repayment/new）：材霈平台這邊只負責收
表單，送出時把資料原封不動轉手給職缺維護表單背後那支 GAS 程式的 Web App
（type=SUBMIT_SALARY），後續所有邏輯——後端重新加總金額（不信任前端算好
的總額）、檢查申請人是否已完成 LINE 綁定、找核准主管、組 LINE Flex 卡片
推播、寫入「薪資補款紀錄」試算表、核准/拒絕、發信——完全由那支 GAS 程式
繼續處理，材霈平台這邊不重做、也不另外存一份佐證照片。

這是刻意選擇的「方案 A」（相對於之後可能做的「方案 B：核准流程整段搬進
這個 repo」），完整的方案 A/B 差異、為什麼先做 A、還有照片公開連結的已知
限制，記錄在 HANDOFF.md 的「薪資補款送出表單」章節，不要在這裡重複貼一次，
之後有調整以 HANDOFF.md 為準。

跟 GAS 那支程式的請求/回應格式，都是照 Project_Salary.gs 的
SalaryWorkflowService.processSalarySubmission() 現有的樣子照抄，沒有另外
新增或修改對方看得懂的欄位。
"""
import re

import requests

from config import JOB_PORTAL_GAS_WEBAPP_URL as GAS_WEBAPP_URL

_REQUEST_TIMEOUT_SECONDS = 30

# 加項/扣項明細固定十個項目、下拉選單選項、英文代號，全部照抄使用者
# 2026-09-09 提供的現有 Netlify 表單原始碼（index_6.html）——包含
# handleSalarySubmit() 組 earnings/deductions 物件時實際用的英文 key
# 名稱（例如 work_hours、labor_ins），不是我方自己另外取名，這樣送去
# GAS 的 payload 才會跟現有表單完全一致，不只是畫面看起來一樣。
# 每個 tuple 是 (英文代號，同時是表單欄位 name 屬性跟送給 GAS 的
# earnings/deductions 物件 key, 畫面上顯示的中文名稱)。
EARNING_FIELDS = [
    ("work_hours", "工時/天數"),
    ("salary", "薪資"),
    ("hours_bonus", "工時獎金"),
    ("uniform_refund", "制服退費"),
    ("labor_refund", "勞保退費"),
    ("health_refund", "健保退費"),
    ("referral_bonus", "推薦獎金"),
    ("severance", "資遣費"),
    ("annual_leave", "年假代金"),
    ("other", "其他加項"),
]

DEDUCTION_FIELDS = [
    ("labor_ins", "勞保費"),
    ("health_ins", "健保費"),
    ("dependents", "眷屬健保"),
    ("health_2nd", "二代健保"),
    ("group_ins", "團保費"),
    ("deposit", "補扣押金"),
    ("court", "法扣"),
    ("debt", "欠款"),
    ("remit_fee", "匯費"),
    ("other", "其他扣項"),
]

IS_CLAIMABLE_OPTIONS = ["可", "不可"]
PAY_TYPE_OPTIONS = ["立即補款", "同次月薪"]

# 台灣身分證字號檢查碼演算法，照抄現有表單 index_6.html 的
# validateTaiwanId()：現有表單送出前會先擋掉格式錯誤的身分證字號，這裡
# 照做同樣的檢查，讓材霈平台這邊的表單體驗（什麼時候會被擋、擋下來的
# 訊息時機）跟同仁原本熟悉的一致，不是為了額外加驗證。
_TAIWAN_ID_LETTERS = "ABCDEFGHJKLMNPQRSTUVXYWZIO"
_TAIWAN_ID_PATTERN = re.compile(r"^[A-Z][1289]\d{8}$")


def validate_taiwan_id(id_card: str) -> bool:
    id_card = (id_card or "").strip().upper()
    if not _TAIWAN_ID_PATTERN.match(id_card):
        return False
    letter_code = _TAIWAN_ID_LETTERS.index(id_card[0]) + 10
    d0, d1 = divmod(letter_code, 10)
    total = d0 + d1 * 9
    for i in range(1, 9):
        total += int(id_card[i]) * (9 - i)
    total += int(id_card[9])
    return total % 10 == 0


def build_payload(
    *,
    applicant_name: str,
    employee_name: str,
    id_card: str,
    vendor: str,
    apply_date: str,
    pay_date: str,
    deduct_month: str,
    compensate_month: str,
    is_claimable: str,
    pay_type: str,
    notes: str,
    earnings: dict,
    deductions: dict,
    image_base64: str = "",
    image_filename: str = "",
) -> dict:
    """組出送給 GAS SUBMIT_SALARY 端點的請求內容。這裡也會把 earnings／
    deductions 加總填進 summary，但只是給 GAS 比對用——GAS 自己一定會照
    明細重新加總一次，兩邊算出來的總額如果對不上，它只會記一筆警告、不會
    擋下這筆申請，所以這裡算錯或漏算都不影響申請本身送不送得出去。"""
    payload = {
        "type": "SUBMIT_SALARY",
        "applicant": {"displayName": applicant_name},
        "info": {
            "applicant_name": applicant_name,
            "name": employee_name,
            "id_card": id_card,
            "vendor": vendor,
            "apply_date": apply_date,
            "pay_date": pay_date,
            "deduct_month": deduct_month,
            "compensate_month": compensate_month,
            "is_claimable": is_claimable,
            "pay_type": pay_type,
            "notes": notes,
        },
        "earnings": earnings,
        "deductions": deductions,
        "summary": {
            "total_earnings": sum(earnings.values()),
            "total_deductions": sum(deductions.values()),
            "net_total": sum(earnings.values()) - sum(deductions.values()),
        },
    }
    if image_base64:
        payload["image"] = {"base64": image_base64, "filename": image_filename}
    return payload


def submit_salary_repayment(payload: dict) -> dict:
    """把 payload 轉送給 GAS 的 SUBMIT_SALARY 端點，回傳 GAS 回應的
    dict（成功時至少有 "status": "success" 跟 "salaryId"；GAS 那邊擋下來的
    情況，例如尚未完成 LINE 綁定、找不到核准主管，也會回傳結構一樣的
    dict，只是 status 不是 "success"，message 是可以直接顯示給同仁看的
    中文說明）。連線失敗、逾時、回應不是預期的 JSON 格式，都在這裡轉成
    同樣結構的 dict 回傳，呼叫端不需要另外接例外。"""
    if not GAS_WEBAPP_URL:
        return {
            "status": "error",
            "message": "尚未設定 JOB_PORTAL_GAS_WEBAPP_URL 環境變數，請聯絡系統管理員設定後再試一次。",
        }
    try:
        response = requests.post(GAS_WEBAPP_URL, json=payload, timeout=_REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as e:
        return {"status": "error", "message": f"連線到職缺維護系統失敗，請稍後再試：{e}"}

    try:
        data = response.json()
    except ValueError:
        return {
            "status": "error",
            "message": f"職缺維護系統回應格式異常（HTTP {response.status_code}），請稍後再試或聯絡系統管理員。",
        }
    if not isinstance(data, dict) or "status" not in data:
        return {"status": "error", "message": "職缺維護系統回應格式異常，請稍後再試或聯絡系統管理員。"}
    return data
