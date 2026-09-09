import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import _env  # noqa: F401  (匯入即副作用：見 _env.py 說明)

import config


class IntEnvTests(unittest.TestCase):
    """_int_env() 是上線前盤點時新增的安全讀取函式：os.getenv(name, default) 只有
    在變數完全「沒設定」時才會用到預設值，如果使用者在 Cloud Run 主控台把值
    清空但沒刪掉那一列，變數會是空字串，直接 int() 會拋例外、讓整個服務（跟
    招募機器人共用 Cloud Run 的其他子系統也會一起）啟動失敗。"""

    def test_returns_default_when_completely_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("UNIT_TEST_NONEXISTENT_INT_VAR", None)
            self.assertEqual(config._int_env("UNIT_TEST_NONEXISTENT_INT_VAR", 15), 15)

    def test_returns_default_when_empty_string(self):
        with patch.dict(os.environ, {"UNIT_TEST_INT_VAR": ""}):
            self.assertEqual(config._int_env("UNIT_TEST_INT_VAR", 15), 15)

    def test_returns_default_when_not_a_valid_integer(self):
        with patch.dict(os.environ, {"UNIT_TEST_INT_VAR": "abc"}):
            self.assertEqual(config._int_env("UNIT_TEST_INT_VAR", 15), 15)

    def test_parses_valid_integer(self):
        with patch.dict(os.environ, {"UNIT_TEST_INT_VAR": "20"}):
            self.assertEqual(config._int_env("UNIT_TEST_INT_VAR", 15), 20)


class FloatEnvTests(unittest.TestCase):
    def test_returns_default_when_empty_string(self):
        with patch.dict(os.environ, {"UNIT_TEST_FLOAT_VAR": ""}):
            self.assertEqual(config._float_env("UNIT_TEST_FLOAT_VAR", 12.0), 12.0)

    def test_returns_default_when_not_a_valid_number(self):
        with patch.dict(os.environ, {"UNIT_TEST_FLOAT_VAR": "not-a-number"}):
            self.assertEqual(config._float_env("UNIT_TEST_FLOAT_VAR", 12.0), 12.0)

    def test_parses_valid_float(self):
        with patch.dict(os.environ, {"UNIT_TEST_FLOAT_VAR": "13.5"}):
            self.assertEqual(config._float_env("UNIT_TEST_FLOAT_VAR", 12.0), 13.5)


if __name__ == "__main__":
    unittest.main()
