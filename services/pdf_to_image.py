"""把 PDF 轉成圖片的共用小工具（2026-09-23 新增）。

**為什麼要有這支**：財務部專區的「一鍵下載」原本只給 PDF 存查單，但財務
同仁留底的實際動作是「一次全選、右鍵列印」——PDF 在檔案總管裡沒辦法多選
一起印，要一個一個開；圖片可以。所以下載時多給一個「圖片」格式選項，
內容跟 PDF 存查單一模一樣，只是換一種檔案格式（見 finance_routes.py）。

**為什麼用 `pdftoppm` 而不是 Python 的 PDF 套件**：跟這個專案既有的
`services/docx_pdf_conversion.py`（呼叫 LibreOffice 把 Word 轉 PDF）
同一種模式——呼叫容器裡裝好的系統工具、失敗就回傳 None 讓呼叫端容錯。
`pdftoppm` 來自 `poppler-utils`（見專案根目錄 `Dockerfile`）。另一個
常見選擇 PyMuPDF 裝起來更省事，但它是 AGPL 授權，公司內部服務要用得
先過法務，不值得為了省一行 Dockerfile 設定去踩。

**為什麼是 PNG 而不是 JPEG**：這份存查單裡**沒有照片**（佐證圖檔只在
核准信的附件裡，存查單最後一行有寫），整張是白底黑字加幾塊純色表格。
這種畫面 PNG 的壓縮效率比 JPEG 好，檔案更小、而且文字邊緣完全銳利
（JPEG 會在文字邊緣產生毛邊，印出來看得出來）。如果哪天存查單裡包進了
照片，再回來改成 JPEG 比較划算。

**多頁怎麼處理**：接成一張直向長圖。實務上這份存查單的版面是固定的
（基本資料 5 列、備註 1 列、金額小計 3 格、簽核紀錄 1 列），只有「備註」
是自由填寫會撐長，所以幾乎一定是一頁、走不到接圖那段；接圖邏輯純粹是
備而不用，避免備註寫很長時只印到第一頁。
"""
import io
import os
import subprocess
import tempfile
import zipfile

DEFAULT_DPI = 200

# 轉一頁 A4 在 200dpi 大約 0.2 秒，這裡抓非常寬鬆的上限，純粹避免
# 遇到壞掉的 PDF 時卡住整個請求。
_TIMEOUT_SECONDS = 120


def _stitch_vertically(page_bytes_list: list) -> bytes:
    """把多頁圖片由上而下接成一張長圖（寬度不同時以最寬的為準、其餘置中，
    空白處填白色，跟紙張留白看起來一致）。"""
    from PIL import Image

    pages = [Image.open(io.BytesIO(raw)).convert("RGB") for raw in page_bytes_list]
    width = max(page.width for page in pages)
    height = sum(page.height for page in pages)
    canvas = Image.new("RGB", (width, height), "white")
    offset_y = 0
    for page in pages:
        canvas.paste(page, ((width - page.width) // 2, offset_y))
        offset_y += page.height
    output = io.BytesIO()
    canvas.save(output, format="PNG")
    return output.getvalue()


def convert_pdf_to_png(pdf_bytes: bytes, *, dpi: int = DEFAULT_DPI, log_prefix: str = "[PDF轉圖片失敗]") -> bytes:
    """把一份 PDF 轉成一張 PNG（多頁會接成直向長圖），失敗回傳 ``None``。

    失敗容錯是刻意的設計：轉檔需要容器裡裝有 `poppler-utils`，任何原因
    失敗（找不到 `pdftoppm`、逾時、PDF 壞掉、沒有產出檔案）都回傳 None，
    呼叫端應該退回原本的 PDF 而不是讓整個下載失敗。"""
    if not pdf_bytes:
        return None
    with tempfile.TemporaryDirectory(prefix="pdf_png_") as tmpdir:
        pdf_path = os.path.join(tmpdir, "input.pdf")
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)
        try:
            subprocess.run(
                ["pdftoppm", "-png", "-r", str(dpi), pdf_path, os.path.join(tmpdir, "page")],
                check=True,
                capture_output=True,
                timeout=_TIMEOUT_SECONDS,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError) as err:
            print(f"{log_prefix} {err}")
            return None

        # pdftoppm 產出的檔名是 page-1.png、page-2.png…（頁數多的時候會
        # 補零成 page-01.png），直接照檔名排序就是正確的頁序。
        page_files = sorted(f for f in os.listdir(tmpdir) if f.startswith("page-") and f.endswith(".png"))
        if not page_files:
            print(f"{log_prefix} pdftoppm 執行完成但沒有產出任何圖片檔案")
            return None

        page_bytes_list = []
        for name in page_files:
            with open(os.path.join(tmpdir, name), "rb") as f:
                page_bytes_list.append(f.read())

    # 只有一頁就直接回傳 pdftoppm 的原始輸出，不經過任何重新編碼。
    if len(page_bytes_list) == 1:
        return page_bytes_list[0]
    try:
        return _stitch_vertically(page_bytes_list)
    except Exception as err:
        print(f"{log_prefix} 接合多頁長圖失敗：{err}")
        return None


def convert_pdf_zip_to_png_zip(zip_bytes: bytes, *, dpi: int = DEFAULT_DPI) -> bytes:
    """把「一包 PDF 的 ZIP」轉成「一包 PNG 的 ZIP」，檔名沿用（副檔名換成
    ``.png``）、順序不變。

    **單筆轉檔失敗就原樣放回那份 PDF**，不讓整包下載失敗——財務寧可拿到
    29 張圖加 1 份 PDF，也不要整批都下載不到。非 PDF 的檔案（正常情況
    不會有）也原樣保留。

    ZIP 本身壞掉才會回傳 ``None``，由呼叫端顯示錯誤。"""
    try:
        source = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except (zipfile.BadZipFile, OSError) as err:
        print(f"[財務下載轉圖片] 無法讀取職缺維護系統回傳的 ZIP：{err}")
        return None

    output = io.BytesIO()
    with source, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            if info.is_dir():
                continue
            raw = source.read(info)
            if not info.filename.lower().endswith(".pdf"):
                target.writestr(info.filename, raw)
                continue
            png_bytes = convert_pdf_to_png(raw, dpi=dpi, log_prefix=f"[財務下載轉圖片] {info.filename}")
            if png_bytes:
                target.writestr(info.filename[: -len(".pdf")] + ".png", png_bytes)
            else:
                target.writestr(info.filename, raw)
    return output.getvalue()
