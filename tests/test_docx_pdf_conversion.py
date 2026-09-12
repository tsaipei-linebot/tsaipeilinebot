import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from services.docx_pdf_conversion import convert_docx_to_pdf


class ConvertDocxToPdfTests(unittest.TestCase):
    """convert_docx_to_pdf() 呼叫真正的 soffice 執行檔，開發/CI 環境不一定
    裝得起來或能正常運作（見 HANDOFF.md 的說明），所以這裡 mock 掉
    subprocess.run，只驗證函式本身的邏輯：失敗容錯回傳 None、成功時讀回
    產出的 PDF 內容。這支檔案被 dispatch_contract_service.py／
    client_contract_service.py 兩個契約產生器共用。"""

    def _fake_run_writing_pdf(self, pdf_content: bytes):
        """回傳一個可以當 subprocess.run 的 side_effect：從呼叫參數裡找出
        --outdir 的路徑，在那裡寫一個假的 input.pdf，模擬轉檔成功。"""
        def _run(args, **kwargs):
            outdir = args[args.index("--outdir") + 1]
            with open(os.path.join(outdir, "input.pdf"), "wb") as f:
                f.write(pdf_content)
            return mock.Mock(returncode=0)
        return _run

    def test_successful_conversion_returns_pdf_bytes(self):
        with mock.patch("services.docx_pdf_conversion.subprocess.run",
                         side_effect=self._fake_run_writing_pdf(b"%PDF-FAKE-CONTENT")):
            result = convert_docx_to_pdf(b"fake docx bytes")
        self.assertEqual(result, b"%PDF-FAKE-CONTENT")

    def test_called_process_error_returns_none(self):
        with mock.patch("services.docx_pdf_conversion.subprocess.run",
                         side_effect=subprocess.CalledProcessError(1, "soffice")):
            result = convert_docx_to_pdf(b"fake docx bytes")
        self.assertIsNone(result)

    def test_timeout_returns_none(self):
        with mock.patch("services.docx_pdf_conversion.subprocess.run",
                         side_effect=subprocess.TimeoutExpired("soffice", 60)):
            result = convert_docx_to_pdf(b"fake docx bytes")
        self.assertIsNone(result)

    def test_soffice_not_found_returns_none(self):
        with mock.patch("services.docx_pdf_conversion.subprocess.run",
                         side_effect=FileNotFoundError("soffice")):
            result = convert_docx_to_pdf(b"fake docx bytes")
        self.assertIsNone(result)

    def test_missing_output_file_returns_none(self):
        # subprocess.run 沒有丟例外（表面上「成功」），但沒有真的產生
        # input.pdf——例如 soffice 內部靜默失敗的情況（開發環境實測遇過）。
        with mock.patch("services.docx_pdf_conversion.subprocess.run",
                         return_value=mock.Mock(returncode=0)):
            result = convert_docx_to_pdf(b"fake docx bytes")
        self.assertIsNone(result)

    def test_default_log_prefix_is_used_when_not_specified(self):
        with mock.patch("services.docx_pdf_conversion.subprocess.run",
                         side_effect=FileNotFoundError("soffice")):
            with mock.patch("builtins.print") as mock_print:
                convert_docx_to_pdf(b"fake docx bytes")
        mock_print.assert_called_once()
        self.assertIn("[Word轉PDF失敗]", mock_print.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
