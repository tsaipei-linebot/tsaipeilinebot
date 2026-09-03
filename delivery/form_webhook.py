"""Google 表單 webhook 共用的小工具（純函式，不碰 Firestore，方便寫單元測試）。

Google Apps Script 的 onFormSubmit(e) 觸發器會把 e.namedValues（題目全名 →
回答）整包轉送過來，這裡刻意用「題目標題有沒有包含關鍵字」而不是要求完全比對
題目全名，這樣之後表單題目文字微調（例如「聯絡電話」改成「手機號碼」但仍包含
「電話」兩個字），不需要跟著改程式碼或 Apps Script。
"""

NAME_KEYWORD = "姓名"
PHONE_KEYWORD = "電話"


def extract_answer(answers: dict, keyword: str) -> str:
    for key, value in (answers or {}).items():
        if keyword in key:
            return (value or "").strip()
    return ""


def other_answers(answers: dict) -> dict:
    """回傳排除姓名/電話欄位後的其餘回覆，用於應徵名單頁面顯示參考資訊
    （可配合天數、配送縣市、行政區熟悉度等，不特別解析結構，原樣顯示）。"""
    result = {}
    for key, value in (answers or {}).items():
        if NAME_KEYWORD in key or PHONE_KEYWORD in key:
            continue
        result[key] = value
    return result
