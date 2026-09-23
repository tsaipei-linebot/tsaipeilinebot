import io
import os
import subprocess
import sys
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401

from services import pdf_to_image


def _png_bytes(width: int, height: int, color=(255, 0, 0)) -> bytes:
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (width, height), color).save(output, format="PNG")
    return output.getvalue()


def _fake_pdftoppm(pages: list):
    """假裝 pdftoppm 跑完、在它被指定的輸出目錄裡留下 page-1.png…。

    真正的 pdftoppm 輸出路徑是指令的最後一個參數（`<暫存目錄>/page`），
    所以這裡從參數反推目錄，跟正式環境的行為對得起來。"""

    def run(args, **kwargs):
        prefix = args[-1]
        for index, raw in enumerate(pages, start=1):
            with open(f"{prefix}-{index}.png", "wb") as f:
                f.write(raw)
        return mock.Mock(returncode=0)

    return run


class ConvertPdfToPngTests(unittest.TestCase):
    def test_empty_input_returns_none_without_running_anything(self):
        with mock.patch.object(pdf_to_image.subprocess, "run") as mock_run:
            self.assertIsNone(pdf_to_image.convert_pdf_to_png(b""))
        mock_run.assert_not_called()

    def test_missing_pdftoppm_returns_none_instead_of_raising(self):
        # 容器裡沒裝 poppler-utils 時要安靜地回 None，讓呼叫端退回 PDF，
        # 不是讓整個下載炸掉（見 pdf_to_image.py 開頭的說明）。
        with mock.patch.object(pdf_to_image.subprocess, "run", side_effect=FileNotFoundError("pdftoppm")):
            self.assertIsNone(pdf_to_image.convert_pdf_to_png(b"%PDF-fake"))

    def test_pdftoppm_failure_returns_none(self):
        with mock.patch.object(
            pdf_to_image.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "pdftoppm")
        ):
            self.assertIsNone(pdf_to_image.convert_pdf_to_png(b"%PDF-fake"))

    def test_timeout_returns_none(self):
        with mock.patch.object(
            pdf_to_image.subprocess, "run", side_effect=subprocess.TimeoutExpired("pdftoppm", 120)
        ):
            self.assertIsNone(pdf_to_image.convert_pdf_to_png(b"%PDF-fake"))

    def test_no_output_files_returns_none(self):
        with mock.patch.object(pdf_to_image.subprocess, "run", side_effect=_fake_pdftoppm([])):
            self.assertIsNone(pdf_to_image.convert_pdf_to_png(b"%PDF-fake"))

    def test_single_page_is_returned_untouched(self):
        """只有一頁就直接回傳 pdftoppm 的原始輸出，不重新編碼——存查單實務上
        就是一頁，這條路徑最常走，不該有任何品質損失。"""
        page = _png_bytes(100, 200)
        with mock.patch.object(pdf_to_image.subprocess, "run", side_effect=_fake_pdftoppm([page])):
            result = pdf_to_image.convert_pdf_to_png(b"%PDF-fake")
        self.assertEqual(result, page)

    def test_multiple_pages_are_stitched_into_one_tall_image(self):
        from PIL import Image

        pages = [_png_bytes(100, 200, (255, 0, 0)), _png_bytes(100, 150, (0, 0, 255))]
        with mock.patch.object(pdf_to_image.subprocess, "run", side_effect=_fake_pdftoppm(pages)):
            result = pdf_to_image.convert_pdf_to_png(b"%PDF-fake")
        stitched = Image.open(io.BytesIO(result))
        self.assertEqual(stitched.width, 100)
        self.assertEqual(stitched.height, 350)
        # 第一頁在上、第二頁在下，順序不能顛倒
        self.assertEqual(stitched.convert("RGB").getpixel((50, 10)), (255, 0, 0))
        self.assertEqual(stitched.convert("RGB").getpixel((50, 300)), (0, 0, 255))

    def test_pages_of_different_widths_are_centred_on_the_widest(self):
        from PIL import Image

        pages = [_png_bytes(100, 50, (255, 0, 0)), _png_bytes(60, 50, (0, 0, 255))]
        with mock.patch.object(pdf_to_image.subprocess, "run", side_effect=_fake_pdftoppm(pages)):
            result = pdf_to_image.convert_pdf_to_png(b"%PDF-fake")
        stitched = Image.open(io.BytesIO(result)).convert("RGB")
        self.assertEqual(stitched.width, 100)
        # 窄的那頁置中，兩側補白
        self.assertEqual(stitched.getpixel((2, 75)), (255, 255, 255))
        self.assertEqual(stitched.getpixel((50, 75)), (0, 0, 255))

    def test_dpi_is_passed_through_to_pdftoppm(self):
        captured = {}

        def run(args, **kwargs):
            captured["args"] = args
            return _fake_pdftoppm([_png_bytes(10, 10)])(args, **kwargs)

        with mock.patch.object(pdf_to_image.subprocess, "run", side_effect=run):
            pdf_to_image.convert_pdf_to_png(b"%PDF-fake", dpi=300)
        self.assertIn("300", captured["args"])
        self.assertEqual(pdf_to_image.DEFAULT_DPI, 200)


def _zip_of(entries: dict) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zf:
        for name, raw in entries.items():
            zf.writestr(name, raw)
    return output.getvalue()


def _entries_of(zip_bytes: bytes) -> list:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return [(info.filename, zf.read(info)) for info in zf.infolist()]


class ConvertPdfZipToPngZipTests(unittest.TestCase):
    def test_broken_zip_returns_none(self):
        self.assertIsNone(pdf_to_image.convert_pdf_zip_to_png_zip(b"this is not a zip"))

    def test_pdfs_become_pngs_with_same_stem_and_order(self):
        source = _zip_of({"薪資補款存查單_A001.pdf": b"%PDF-a", "薪資補款存查單_A002.pdf": b"%PDF-b"})
        page = _png_bytes(10, 10)
        with mock.patch.object(pdf_to_image, "convert_pdf_to_png", return_value=page):
            result = pdf_to_image.convert_pdf_zip_to_png_zip(source)
        entries = _entries_of(result)
        self.assertEqual([name for name, _ in entries], ["薪資補款存查單_A001.png", "薪資補款存查單_A002.png"])
        self.assertEqual([raw for _, raw in entries], [page, page])

    def test_failed_conversion_keeps_the_original_pdf_instead_of_dropping_it(self):
        """寧可讓財務拿到「29 張圖 + 1 份 PDF」，也不要整批下載不到。"""
        source = _zip_of({"好的.pdf": b"%PDF-ok", "壞的.pdf": b"%PDF-bad"})
        page = _png_bytes(10, 10)

        def convert(pdf_bytes, **kwargs):
            return None if pdf_bytes == b"%PDF-bad" else page

        with mock.patch.object(pdf_to_image, "convert_pdf_to_png", side_effect=convert):
            result = pdf_to_image.convert_pdf_zip_to_png_zip(source)
        entries = dict(_entries_of(result))
        self.assertEqual(entries["好的.png"], page)
        self.assertEqual(entries["壞的.pdf"], b"%PDF-bad")

    def test_non_pdf_entries_are_passed_through_untouched(self):
        source = _zip_of({"說明.txt": b"hello"})
        with mock.patch.object(pdf_to_image, "convert_pdf_to_png") as mock_convert:
            result = pdf_to_image.convert_pdf_zip_to_png_zip(source)
        mock_convert.assert_not_called()
        self.assertEqual(dict(_entries_of(result))["說明.txt"], b"hello")


if __name__ == "__main__":
    unittest.main()
