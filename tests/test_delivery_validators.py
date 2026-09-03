import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401
from tests import _stub_gcp
_stub_gcp.install()

from delivery.validators import is_valid_taiwan_id


class IsValidTaiwanIdTests(unittest.TestCase):
    def test_valid_id_passes(self):
        # A -> letter value 10 -> [1,0]；[1,0,1,2,3,4,5,6,7,8,9] 與權重
        # [1,9,8,7,6,5,4,3,2,1,1] 內積 = 130，130 % 10 == 0，是合法檢查碼。
        self.assertTrue(is_valid_taiwan_id("A123456789"))

    def test_lowercase_letter_is_accepted(self):
        self.assertTrue(is_valid_taiwan_id("a123456789"))

    def test_wrong_check_digit_fails(self):
        self.assertFalse(is_valid_taiwan_id("A123456780"))

    def test_wrong_length_fails(self):
        self.assertFalse(is_valid_taiwan_id("A12345678"))
        self.assertFalse(is_valid_taiwan_id("A1234567890"))

    def test_non_alpha_first_char_fails(self):
        self.assertFalse(is_valid_taiwan_id("1123456789"))

    def test_non_digit_remainder_fails(self):
        self.assertFalse(is_valid_taiwan_id("A12345678X"))

    def test_empty_or_none_fails(self):
        self.assertFalse(is_valid_taiwan_id(""))
        self.assertFalse(is_valid_taiwan_id(None))

    def test_whitespace_is_stripped(self):
        self.assertTrue(is_valid_taiwan_id("  A123456789  "))


if __name__ == "__main__":
    unittest.main()
