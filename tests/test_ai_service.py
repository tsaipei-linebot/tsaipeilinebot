import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "dummy")

from services import ai_service as ai


class BuildGenerationConfigTests(unittest.TestCase):
    def test_no_schema_returns_none(self):
        # 沒有帶 schema 時要維持原本自由文字輸出行為（例如職缺詳情美化排版），
        # 不能因為這次加了結構化輸出支援就意外改變既有呼叫端的行為
        self.assertIsNone(ai._build_generation_config(None))
        self.assertIsNone(ai._build_generation_config({}))

    def test_schema_enables_structured_json_output(self):
        schema = {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING"},
                "reply": {"type": "STRING"},
            },
            "required": ["action", "reply"],
        }
        config = ai._build_generation_config(schema)
        self.assertIsNotNone(config)
        self.assertEqual(config.response_mime_type, "application/json")
        self.assertEqual(config.response_schema, schema)


if __name__ == "__main__":
    unittest.main()
