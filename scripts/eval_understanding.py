"""AI 需求單準確率考試（HANDOFF.md 第 81 項）。

用 scripts/understanding_eval_cases.json 裡整理好的求職者說法（第四～八輪測試抓到、
程式判斷錯的句子）去問真正的 Gemini，比對 AI 填的需求單（加上程式不需要職缺資料
就能做的檢查，例如「新竹竹北」只算竹北）對不對，印出通過率跟每句
等了幾秒。只會呼叫 Gemini，不會傳 LINE 訊息、不會改任何資料。

在 Cloud Shell 執行（第一次要先裝套件）：
    cd ~/tsaipeilinebot && git pull
    python3 -m venv ~/peipei-venv
    ~/peipei-venv/bin/pip install -q google-genai python-dotenv pytz requests line-bot-sdk google-cloud-firestore
    ~/peipei-venv/bin/python scripts/eval_understanding.py

可以加參數比較 Gemini「先想一想」的額度（越大越準、也越慢）：
    ~/peipei-venv/bin/python scripts/eval_understanding.py --thinking 512
在 Claude 的雲端環境：有設環境變數 PEIPEI_TEST_GEMINI_API_KEY（AI Studio 的鑰匙）時自動改用它。
只跑某幾題（題目名稱包含這段字）：
    ~/peipei-venv/bin/python scripts/eval_understanding.py --only 地區
"""
import argparse
import json
import os
import re
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.ai_service as ai_service  # noqa: E402
from services.understanding_service import understand_message, drop_county_before_district  # noqa: E402

# Claude 的雲端測試環境沒有 GCP 權限，改用 AI Studio 的 Gemini 鑰匙（環境變數
# PEIPEI_TEST_GEMINI_API_KEY，只給測試用，線上的沛沛照樣用 Cloud Run 的 Vertex AI）。
# 兩邊是同一個 Gemini 模型；有設這個鑰匙就用鑰匙，沒設（Cloud Shell）照舊用 Vertex AI。
_TEST_KEY = os.getenv("PEIPEI_TEST_GEMINI_API_KEY", "").strip()
if _TEST_KEY:
    from google import genai  # noqa: E402
    ai_service.ai_client = genai.Client(api_key=_TEST_KEY)

CASES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "understanding_eval_cases.json")
_KEEP_FULL = {"新竹縣", "新竹市", "嘉義縣", "嘉義市"}


def _norm_place(value: str) -> str:
    v = str(value).replace("臺", "台").strip()
    m = re.match(r"^(..[縣市])(.+[區鄉鎮市])$", v)
    if m:
        v = m.group(2)
    if v in _KEEP_FULL:
        return v[:2]
    if len(v) > 2 and v[-1] in "縣市區鄉鎮":
        v = v[:-1]
    return v


def _values(form: dict, field: str):
    value = form.get(field)
    if field in ("locations", "exclude_locations"):
        return {_norm_place(v) for v in value or []}
    if isinstance(value, list):
        return set(value)
    return value


def check(case: dict, form: dict) -> list:
    """回傳沒通過的原因（空清單＝通過）。"""
    if form is None:
        return ["AI 沒有回應"]
    problems = []
    if case.get("intent_in") and form["intent"] not in case["intent_in"]:
        problems.append(f"intent={form['intent']}，應該是 {'/'.join(case['intent_in'])}")
    if case.get("intent_not") and form["intent"] in case["intent_not"]:
        problems.append(f"intent 不應該是 {form['intent']}")
    for field, expected in (case.get("expect") or {}).items():
        got = _values(form, field)
        want = {_norm_place(v) for v in expected} if field in ("locations", "exclude_locations") else (
            set(expected) if isinstance(expected, list) else expected)
        if got != want:
            problems.append(f"{field}={sorted(got) if isinstance(got, set) else got!r}，應該是 {sorted(want) if isinstance(want, set) else want!r}")
    for field, expected in (case.get("has") or {}).items():
        got = _values(form, field)
        want = {_norm_place(v) for v in expected} if field in ("locations", "exclude_locations") else set(expected)
        if not want <= got:
            problems.append(f"{field}={sorted(got)}，應該包含 {sorted(want)}")
    for field, expected in (case.get("any") or {}).items():
        if not _values(form, field) & set(expected):
            problems.append(f"{field}={sorted(_values(form, field))}，應該至少有 {'/'.join(expected)} 其中一個")
    for field, expected in (case.get("forbid") or {}).items():
        want = {_norm_place(v) for v in expected} if field in ("locations", "exclude_locations") else set(expected)
        bad = _values(form, field) & want
        if bad:
            problems.append(f"{field} 不應該有 {sorted(bad)}")
    for field in case.get("nonempty") or []:
        if not form.get(field):
            problems.append(f"{field} 不應該是空的")
    return problems


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--thinking", type=int, default=0, help="Gemini 先想一想的額度（0＝不想，預設）")
    parser.add_argument("--only", default="", help="只跑題目名稱包含這段字的題目")
    args = parser.parse_args()

    cases = [c for c in json.load(open(CASES_PATH, encoding="utf-8")) if args.only in c["id"]]
    latencies, failed = [], []
    for case in cases:
        history = [{"role": "招募顧問沛沛", "text": case["last_bot"]}] if case.get("last_bot") else []
        start = time.monotonic()
        form = understand_message(case["text"], case.get("slots") or {}, history, thinking_budget=args.thinking)
        if form:
            # 正式上線時程式會再檢查一次需求單，考試也照同樣的規則整理地區（例如「新竹竹北」只算竹北）
            form["locations"] = drop_county_before_district(form["locations"], case["text"])
        latencies.append(time.monotonic() - start)
        problems = check(case, form)
        mark = "✅" if not problems else "❌"
        print(f"{mark} {case['id']}：「{case['text']}」 {latencies[-1]:.1f} 秒")
        if problems:
            failed.append(case["id"])
            for p in problems:
                print(f"      {p}")
            shown = {k: v for k, v in (form or {}).items() if v}
            print(f"      AI 填的需求單：{json.dumps(shown, ensure_ascii=False)}")

    passed = len(cases) - len(failed)
    print("\n==============================")
    print(f"通過 {passed} / {len(cases)} 題（{passed / max(1, len(cases)):.0%}），thinking={args.thinking}")
    if latencies:
        ordered = sorted(latencies)
        print(f"每題等待：中位數 {statistics.median(latencies):.1f} 秒，最慢的 5% 約 {ordered[int(len(ordered) * 0.95) - 1]:.1f} 秒")
    if failed:
        print("沒通過的題目：" + "、".join(failed))


if __name__ == "__main__":
    main()
