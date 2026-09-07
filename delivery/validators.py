"""欄位格式驗證（純函式，不碰 Firestore，方便寫單元測試）。"""

_TAIWAN_ID_LETTER_VALUES = {
    "A": 10, "B": 11, "C": 12, "D": 13, "E": 14, "F": 15, "G": 16, "H": 17, "I": 34, "J": 18,
    "K": 19, "L": 20, "M": 21, "N": 22, "O": 35, "P": 23, "Q": 24, "R": 25, "S": 26, "T": 27,
    "U": 28, "V": 29, "W": 32, "X": 30, "Y": 31, "Z": 33,
}
_TAIWAN_ID_WEIGHTS = [1, 9, 8, 7, 6, 5, 4, 3, 2, 1, 1]


def is_valid_taiwan_id(id_number: str) -> bool:
    """驗證中華民國國民身分證統一編號的格式是否合法（標準檢查碼演算法：
    1 個英文字母 + 9 碼數字，最後一碼是檢查碼）。只驗證格式，不代表這組字號
    真的有對應到一個人，也不處理外籍人士居留證（那是不同的檢查碼規則）。"""
    if not id_number:
        return False
    id_number = id_number.strip().upper()
    if len(id_number) != 10:
        return False
    if not id_number[0].isalpha() or not id_number[1:].isdigit():
        return False

    letter_value = _TAIWAN_ID_LETTER_VALUES.get(id_number[0])
    if letter_value is None:
        return False

    digits = [letter_value // 10, letter_value % 10] + [int(c) for c in id_number[1:]]
    total = sum(d * w for d, w in zip(digits, _TAIWAN_ID_WEIGHTS))
    return total % 10 == 0
