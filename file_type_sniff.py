"""驗證上傳檔案「宣稱的類型」（瀏覽器回報的 content_type，或使用者自己
打的副檔名）跟「檔案開頭真正的內容」是否相符，取代各處原本只看瀏覽器
回報值就放行的做法——那個值是使用者的瀏覽器自己說的，理論上可以把
任何檔案偽裝成任何類型上傳上來。

只看檔案開頭幾個位元組的「檔頭簽章」，不需要額外安裝套件（python-magic
需要系統另外裝 libmagic，Cloud Run 環境不確定有沒有裝，用檔頭判斷維護
成本低，對這裡要擋的風險——「副檔名/content_type 講的是允許類型，裡面
其實塞別的東西」——已經足夠）。新版 Office（.docx/.xlsx/.pptx）都是
ZIP 容器格式、舊版 Office（.doc/.xls/.ppt）都是微軟複合文件格式，檔頭
本身分不出裡面到底是 Word 還是 Excel，這裡只要求「屬於同一種容器
家族」，不細分到子格式。
"""

_PDF_SIGNATURE = b"%PDF-"
_JPEG_SIGNATURE = b"\xff\xd8\xff"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_ZIP_SIGNATURE = b"PK\x03\x04"
_OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# 新版 Office（ZIP 容器）／舊版 Office（OLE 複合文件）各自共用同一種檔頭。
_ZIP_OFFICE_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
_OLE_OFFICE_CONTENT_TYPES = {
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
}
_SIMPLE_CONTENT_TYPE_SIGNATURES = {
    "application/pdf": _PDF_SIGNATURE,
    "image/jpeg": _JPEG_SIGNATURE,
    "image/png": _PNG_SIGNATURE,
}


def content_matches_claimed_type(content: bytes, claimed_content_type: str) -> bool:
    """檔案開頭的位元組是否跟瀏覽器回報的 content_type 相符。沒有登記過
    檔頭簽章的類型一律視為不通過，不會因為不認得的類型就誤放行。"""
    if claimed_content_type in _ZIP_OFFICE_CONTENT_TYPES:
        return content.startswith(_ZIP_SIGNATURE)
    if claimed_content_type in _OLE_OFFICE_CONTENT_TYPES:
        return content.startswith(_OLE_SIGNATURE)
    signature = _SIMPLE_CONTENT_TYPE_SIGNATURES.get(claimed_content_type)
    if signature is None:
        return False
    return content.startswith(signature)


def is_allowed_upload(content: bytes, content_type: str, allowed_content_types) -> bool:
    """統一給「先檢查 content_type 是否在允許清單裡」這種既有寫法呼叫：
    content_type 要在允許清單裡，「而且」檔案開頭真正的內容也要符合
    宣稱的類型，兩個條件都成立才算通過。"""
    return content_type in allowed_content_types and content_matches_claimed_type(content, content_type)


# 副檔名 → 判斷檔頭該符合哪個簽章家族，給只看檔名副檔名（沒有另外檢查
# content_type）的上傳流程使用（例如合約產生器的廠商版本合約上傳）。
_EXTENSION_SIGNATURE_FAMILIES = {
    ".pdf": (_PDF_SIGNATURE,),
    ".doc": (_OLE_SIGNATURE,),
    ".xls": (_OLE_SIGNATURE,),
    ".ppt": (_OLE_SIGNATURE,),
    ".docx": (_ZIP_SIGNATURE,),
    ".xlsx": (_ZIP_SIGNATURE,),
    ".pptx": (_ZIP_SIGNATURE,),
    ".jpg": (_JPEG_SIGNATURE,),
    ".jpeg": (_JPEG_SIGNATURE,),
    ".png": (_PNG_SIGNATURE,),
}


def content_matches_claimed_extension(content: bytes, filename: str) -> bool:
    """檔案開頭的位元組是否跟檔名副檔名宣稱的類型相符。副檔名不在已知
    清單裡（呼叫端理論上已經先用允許的副檔名清單擋過一次）一律視為
    不通過。"""
    lower_filename = (filename or "").lower()
    for extension, signatures in _EXTENSION_SIGNATURE_FAMILIES.items():
        if lower_filename.endswith(extension):
            return any(content.startswith(sig) for sig in signatures)
    return False


def looks_like_image(content: bytes) -> bool:
    """給只接受圖片、但沒有另外檢查 content_type 允許清單的上傳流程用
    （例如職缺圖檔、薪資補款佐證照片，這兩處會把檔案轉成 base64 直接
    轉送給外部系統，本身不落地存檔，所以只用最基本的 JPEG/PNG 檔頭
    判斷，跟系統裡其他地方允許的圖片格式一致）。"""
    return content.startswith(_JPEG_SIGNATURE) or content.startswith(_PNG_SIGNATURE)
