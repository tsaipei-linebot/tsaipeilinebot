"""把 Word (docx) 內容轉成 PDF 的共用小工具，給需要「產生 Word 檔的同時
順便存一份 PDF 方便網頁內嵌預覽」的功能共用（目前是
``dispatch_contract_service.py``、``client_contract_service.py`` 兩個
契約產生器在用）。獨立成這支檔案是因為兩邊的轉檔邏輯完全一樣、只是
呼叫端的「失敗時的中文訊息前綴」不同，避免同一段 subprocess 呼叫/暫存
目錄處理邏輯在兩個服務檔案裡各存一份、以後改一邊忘了改另一邊。

失敗容錯是刻意的設計，不是漏洞：轉檔需要 Cloud Run 容器裡裝有 LibreOffice
（見專案根目錄 `Dockerfile`），任何原因失敗（逾時、找不到 `soffice`、
輸出檔案不存在）都回傳 `None`，呼叫端不應該讓這一步的失敗擋住整個
契約產生流程，只是那一筆紀錄沒有 PDF、看不到預覽，Word 檔案照樣正常
產生/下載/存檔。
"""
import os
import subprocess
import tempfile
import uuid


def convert_docx_to_pdf(docx_bytes: bytes, *, log_prefix: str = "[Word轉PDF失敗]") -> bytes:
    """每次呼叫都用一個全新的暫存目錄當 LibreOffice 的 ``UserInstallation``
    （``-env:UserInstallation``），避免多個請求同時轉檔時搶用同一份使用者
    設定檔互相卡住。"""
    with tempfile.TemporaryDirectory(prefix="docx_pdf_") as tmpdir:
        docx_path = os.path.join(tmpdir, "input.docx")
        with open(docx_path, "wb") as f:
            f.write(docx_bytes)
        profile_dir = os.path.join(tmpdir, f"lo_profile_{uuid.uuid4().hex}")
        try:
            subprocess.run(
                [
                    "soffice", "--headless", "--norestore",
                    f"-env:UserInstallation=file://{profile_dir}",
                    "--convert-to", "pdf", "--outdir", tmpdir, docx_path,
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError) as err:
            print(f"{log_prefix} {err}")
            return None

        pdf_path = os.path.join(tmpdir, "input.pdf")
        if not os.path.exists(pdf_path):
            print(f"{log_prefix} LibreOffice 執行完成但找不到輸出的 PDF 檔案")
            return None
        with open(pdf_path, "rb") as f:
            return f.read()
