"""批次匯入上傳檔案的共用「讀檔層」（2026-09-23 新增）。

**為什麼要有這支**：原本四個批次匯入（配送部人員、配送部報班地點、
派遣所人員、派遣所地點）都是各自「解碼成文字 → `csv.DictReader`」，
同仁匯入的姓名常常出現 `?`。查下來**不是我們讀錯，是檔案存的時候就
壞了**：同仁在 Excel 按「另存新檔 → CSV（逗號分隔）」時，Windows 繁體
中文版用 **Big5(cp950)** 編碼存檔，而 Big5 放不下所有中文字（「堃」
「喆」「峯」「陞」這類姓名用字都沒有）。Excel 遇到存不下的字**直接換成
一個 `?` 寫進檔案**，那個字在檔案送到我們手上之前就已經沒了，怎麼解碼
都救不回來。

`.xlsx` 內部固定用 UTF-8 存文字，**沒有編碼可以選、也沒有選錯的機會**，
所以範本改成 Excel 就整類問題消失。順便還解決 CSV 的另外兩個老毛病：
儲存格內容有逗號會被切錯欄、以及電話開頭的 0 會不見（Excel 範本可以把
那一欄預先設成文字格式，CSV 沒有格式的概念所以做不到）。

**兩種格式都收**是刻意的：舊的、已經填好的 CSV 檔案要繼續能用，不能因為
改版就讓同仁手上的檔案突然匯不進去。

這支只負責「把上傳的檔案變成一列一列的字典」，每個匯入各自的欄位檢查與
轉換邏輯完全不動，仍然留在原本的 `parse_*_csv()` 裡。
"""
import csv
import datetime
import io

# xlsx 其實是一個 zip 檔，開頭固定是 PK\x03\x04。用內容判斷而不是看副檔名，
# 因為副檔名可能被改過，而且有些瀏覽器上傳時給的檔名不完整。
_XLSX_MAGIC = b"PK\x03\x04"


def looks_like_xlsx(content: bytes) -> bool:
    return bool(content) and content[:4] == _XLSX_MAGIC


def decode_text(content: bytes) -> str:
    """CSV 用：先試 UTF-8（含 BOM），解碼失敗（表示不是合法 UTF-8）再退回
    cp950；兩者都失敗就用 UTF-8 容錯模式，至少不會整個匯入功能直接掛掉。

    這是原本散在四個匯入檔案裡的 `_decode()`，集中到這裡共用。"""
    for encoding in ("utf-8-sig", "cp950"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def cell_to_text(value) -> str:
    """把 openpyxl 讀到的儲存格值轉成字串，轉成跟同仁在 CSV 裡「會打成
    什麼樣子」一致的寫法，這樣後面每個匯入原本的解析邏輯都不用改：

    - 日期時間 → ``2024-01-31 09:00``；純日期 → ``2024-01-31``
      （正好是各匯入本來就認得的格式）
    - 整數值的數字 → ``3`` 而不是 ``3.0``（Excel 的數字一律是浮點數，
      不處理的話「需求人數」會變成 `3.0` 而解析失敗）
    - 小數照原樣（緯經度要保留小數，所以只有「整數值」才去掉小數點）
    - 空白 → 空字串
    """
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        # 時分秒都是 0 的話視為「只有日期」——Excel 的純日期儲存格讀出來
        # 也是 datetime，午夜零點跟「沒填時間」在這裡無法區分，而各匯入的
        # 日期欄位本來就只要日期，所以取日期比較貼近實際用法。
        if (value.hour, value.minute, value.second) == (0, 0, 0):
            return value.strftime("%Y-%m-%d")
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, datetime.date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, datetime.time):
        return value.strftime("%H:%M")
    if isinstance(value, bool):
        # 先擋在 int 前面：Python 的 bool 是 int 的子類別，不特別處理會變成 1/0
        return "是" if value else "否"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _rows_from_xlsx(content: bytes):
    """讀 .xlsx 的第一個工作表，第 1 列當表頭、第 2 列起當資料。
    回傳 (rows, header_error)，格式跟 `csv.DictReader` 的結果一致。"""
    try:
        import openpyxl

        # data_only=True 取「算好的值」而不是公式本身。範本填一填的檔案
        # 不會有公式，這裡是為了萬一同仁用公式算過內容。
        workbook = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    except Exception as err:
        print(f"[匯入讀檔] Excel 檔案讀取失敗：{err}")
        return [], "Excel 檔案打不開，請確認檔案沒有損毀，或改用範本重新填寫。"

    try:
        worksheet = workbook.active
        if worksheet is None:
            return [], "檔案是空的或無法辨識表頭"

        row_iter = worksheet.iter_rows(values_only=True)
        header_row = next(row_iter, None)
        if header_row is None:
            return [], "檔案是空的或無法辨識表頭"
        headers = [cell_to_text(cell) for cell in header_row]
        if not any(headers):
            return [], "檔案是空的或無法辨識表頭"

        rows = []
        for raw_row in row_iter:
            if raw_row is None:
                continue
            values = [cell_to_text(cell) for cell in raw_row]
            # 欄位數比表頭少（Excel 尾端沒填的儲存格常常直接不回傳）就補空字串，
            # 不讓某一欄漏填變成整份解析失敗。
            values += [""] * (len(headers) - len(values))
            rows.append({header: values[i] for i, header in enumerate(headers) if header})
        return rows, None
    finally:
        # read_only 模式會開著檔案控制代碼，用完一定要關。
        workbook.close()


def read_rows(content: bytes):
    """把上傳的檔案（Excel 或 CSV，自動判斷）讀成 (rows, header_error)。

    - `rows`：一列一個字典，key 是表頭文字、value 一律是字串（已經照
      `cell_to_text()` 正規化），跟 `csv.DictReader` 讀出來的形狀一致。
    - `header_error`：不是 None 時代表整份檔案讀不了（空檔案、表頭認不
      出來、Excel 檔壞掉），此時 `rows` 一定是空 list。**欄位缺不缺**不在
      這裡判斷，仍然由各匯入自己檢查，因為每個匯入的必填欄位不一樣。
    """
    if not content:
        return [], "檔案是空的或無法辨識表頭"
    if looks_like_xlsx(content):
        return _rows_from_xlsx(content)

    reader = csv.DictReader(io.StringIO(decode_text(content)))
    if not reader.fieldnames:
        return [], "檔案是空的或無法辨識表頭"
    return list(reader), None


def header_names(rows: list) -> set:
    """從讀出來的列取表頭名稱（去頭尾空白、去掉空欄名），給各匯入檢查
    必填欄位用。檔案只有表頭沒有資料列時 `rows` 是空的，這裡回傳空集合，
    呼叫端會因為「缺少必要欄位」而擋下來，訊息比「檔案是空的」精確。"""
    if not rows:
        return set()
    return {str(key).strip() for key in rows[0].keys() if str(key).strip()}


def build_template_xlsx(headers: list, sample_row: list, text_columns=()) -> bytes:
    """產生一份範本 .xlsx：第 1 列表頭、第 2 列一行範例資料。

    `text_columns` 裡的欄位名稱會被設成「文字」格式（`@`），**電話這種
    開頭有 0 的欄位一定要放進去**——不設的話 Excel 會把 `0912345678` 當
    數字、開頭的 0 直接不見，這是 Excel 自己的行為，跟檔案格式無關。
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "匯入範本"

    worksheet.append(list(headers))
    for cell in worksheet[1]:
        cell.font = Font(bold=True)

    worksheet.append([str(value) for value in sample_row])

    for index, header in enumerate(headers, start=1):
        column = get_column_letter(index)
        # 欄寬照表頭字數抓，中文字比較寬所以乘 2，至少 12 個字元寬，
        # 免得同仁打開看到一排 #### 還要自己拉欄寬。
        worksheet.column_dimensions[column].width = max(12, len(str(header)) * 2 + 4)
        if header in text_columns:
            # 設在「欄」上面而不是逐一設每個儲存格：逐格設會把那些儲存格
            # 實際建出來，檔案裡就多出上千列空白列（而且 append() 會接在
            # 它們後面，範例資料會被擠到第 1002 列）。設在欄上同仁往下打
            # 新的列也適用，而且檔案裡只有表頭跟範例兩列。
            column_dimension = worksheet.column_dimensions[column]
            column_dimension.number_format = "@"
            column_dimension.customFormat = True
            worksheet.cell(row=2, column=index).number_format = "@"

    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()
