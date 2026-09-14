import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from file_type_sniff import (
    content_matches_claimed_extension,
    content_matches_claimed_type,
    is_allowed_upload,
    looks_like_image,
)

_PDF_BYTES = b"%PDF-1.7\n%rest of a real pdf..."
_JPEG_BYTES = b"\xff\xd8\xff\xe0rest of a real jpeg..."
_PNG_BYTES = b"\x89PNG\r\n\x1a\nrest of a real png..."
_DOCX_BYTES = b"PK\x03\x04rest of a real docx (zip)..."
_DOC_BYTES = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest of a real legacy doc..."
_MALICIOUS_BYTES = b"MZ\x90\x00this is actually an .exe pretending to be something else"


class ContentMatchesClaimedTypeTests(unittest.TestCase):
    def test_real_pdf_matches_pdf_content_type(self):
        self.assertTrue(content_matches_claimed_type(_PDF_BYTES, "application/pdf"))

    def test_real_jpeg_matches_jpeg_content_type(self):
        self.assertTrue(content_matches_claimed_type(_JPEG_BYTES, "image/jpeg"))

    def test_real_png_matches_png_content_type(self):
        self.assertTrue(content_matches_claimed_type(_PNG_BYTES, "image/png"))

    def test_real_docx_matches_docx_content_type(self):
        self.assertTrue(
            content_matches_claimed_type(
                _DOCX_BYTES,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        )

    def test_real_docx_bytes_also_match_xlsx_content_type(self):
        """新版 Office 都是 ZIP 容器，檔頭本身分不出 Word/Excel/PPT，這裡
        刻意只驗證「屬於同一種容器家族」，不細分子格式（見模組開頭說明）。"""
        self.assertTrue(
            content_matches_claimed_type(
                _DOCX_BYTES,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        )

    def test_real_legacy_doc_matches_doc_content_type(self):
        self.assertTrue(content_matches_claimed_type(_DOC_BYTES, "application/msword"))

    def test_disguised_executable_does_not_match_pdf_content_type(self):
        """把 .exe 改名/改 content_type 偽裝成 PDF 上傳——這是這次修復要
        擋下的核心情境。"""
        self.assertFalse(content_matches_claimed_type(_MALICIOUS_BYTES, "application/pdf"))

    def test_disguised_executable_does_not_match_jpeg_content_type(self):
        self.assertFalse(content_matches_claimed_type(_MALICIOUS_BYTES, "image/jpeg"))

    def test_unknown_content_type_never_matches(self):
        self.assertFalse(content_matches_claimed_type(_PDF_BYTES, "application/octet-stream"))

    def test_empty_content_does_not_match_anything(self):
        self.assertFalse(content_matches_claimed_type(b"", "application/pdf"))


class IsAllowedUploadTests(unittest.TestCase):
    _ALLOWED = {"application/pdf", "image/jpeg", "image/png"}

    def test_allowed_type_with_matching_content_passes(self):
        self.assertTrue(is_allowed_upload(_PDF_BYTES, "application/pdf", self._ALLOWED))

    def test_allowed_type_with_disguised_content_fails(self):
        self.assertFalse(is_allowed_upload(_MALICIOUS_BYTES, "application/pdf", self._ALLOWED))

    def test_content_type_not_in_allow_list_fails_even_if_content_matches(self):
        self.assertFalse(is_allowed_upload(_DOC_BYTES, "application/msword", self._ALLOWED))


class ContentMatchesClaimedExtensionTests(unittest.TestCase):
    def test_real_pdf_matches_pdf_extension(self):
        self.assertTrue(content_matches_claimed_extension(_PDF_BYTES, "合約.pdf"))

    def test_real_docx_matches_docx_extension(self):
        self.assertTrue(content_matches_claimed_extension(_DOCX_BYTES, "合約.docx"))

    def test_real_doc_matches_doc_extension(self):
        self.assertTrue(content_matches_claimed_extension(_DOC_BYTES, "合約.doc"))

    def test_disguised_executable_does_not_match_pdf_extension(self):
        self.assertFalse(content_matches_claimed_extension(_MALICIOUS_BYTES, "合約.pdf"))

    def test_extension_is_case_insensitive(self):
        self.assertTrue(content_matches_claimed_extension(_PDF_BYTES, "合約.PDF"))

    def test_unrecognized_extension_never_matches(self):
        self.assertFalse(content_matches_claimed_extension(_PDF_BYTES, "合約.exe"))


class LooksLikeImageTests(unittest.TestCase):
    def test_real_jpeg_passes(self):
        self.assertTrue(looks_like_image(_JPEG_BYTES))

    def test_real_png_passes(self):
        self.assertTrue(looks_like_image(_PNG_BYTES))

    def test_disguised_executable_fails(self):
        self.assertFalse(looks_like_image(_MALICIOUS_BYTES))

    def test_pdf_is_not_an_image(self):
        self.assertFalse(looks_like_image(_PDF_BYTES))


if __name__ == "__main__":
    unittest.main()
