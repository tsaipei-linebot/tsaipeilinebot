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
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TEST_KEY = os.getenv("PEIPEI_TEST_GEMINI_API_KEY", "").strip()
if _TEST_KEY:
    # 雲端環境沒有 GCP 登入，Firestore 一載入就要登入會直接當掉；考試完全不讀寫
    # Firestore，給它一個「匿名」登入讓程式能載入就好。
    from google.auth.credentials import AnonymousCredentials  # noqa: E402
    from google.cloud import firestore  # noqa: E402
    _RealClient = firestore.Client
    firestore.Client = lambda *a, **kw: _RealClient(*a, credentials=AnonymousCredentials(), **kw)

import services.ai_service as ai_service  # noqa: E402
from services.understanding_service import understand_message, drop_county_before_district, cross_check_form  # noqa: E402

# Claude 的雲端測試環境沒有 GCP 權限，改用 AI Studio 的 Gemini 鑰匙（環境變數
# PEIPEI_TEST_GEMINI_API_KEY，只給測試用，線上的沛沛照樣用 Cloud Run 的 Vertex AI）。
# 兩邊是同一個 Gemini 模型；有設這個鑰匙就用鑰匙，沒設（Cloud Shell）照舊用 Vertex AI。
if _TEST_KEY:
    from google import genai  # noqa: E402
    import services.understanding_service as understanding_service  # noqa: E402
    ai_service.ai_client = genai.Client(api_key=_TEST_KEY)
    # AI Studio 不接受選項清單裡有「空字串」（Vertex AI 可以），考試時把空字串選項換成「無」，
    # AI 回「無」再換回空字串；線上程式不動。
    _BLANK = "無"
    _schema = json.loads(json.dumps(understanding_service.UNDERSTANDING_SCHEMA))
    for _prop in _schema["properties"].values():
        if "" in _prop.get("enum", []):
            _prop["enum"] = [_BLANK if v == "" else v for v in _prop["enum"]]
    understanding_service.UNDERSTANDING_SCHEMA = _schema
    _real_normalize = understanding_service.normalize_form

    def _normalize_with_blank(data):
        form = _real_normalize(data)
        for key in ("worktype", "salary_kind", "handoff_reason"):
            if form.get(key) == _BLANK:
                form[key] = ""
        return form
    understanding_service.normalize_form = _normalize_with_blank

CASES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "understanding_eval_cases.json")
_KEEP_FULL = {"新竹縣", "新竹市", "嘉義縣", "嘉義市"}
_PLACE_FIELDS = ("locations", "exclude_locations")


def _ambiguous_districts():
    """好幾個縣市都有的區名（東區、中山區…）：比對時要連縣市一起比，不然台南東區跟新竹東區算成一樣。"""
    from services.job_listing_submit_service import TAIWAN_CITY_DISTRICTS
    seen = {}
    for county, districts in TAIWAN_CITY_DISTRICTS.items():
        for full in districts:
            core = full[len(county):] if full.startswith(county) else full
            seen.setdefault(core[:-1] if len(core) > 2 else core, set()).add(county)
    return {k for k, v in seen.items() if len(v) > 1}


_AMBIGUOUS = _ambiguous_districts()


def _district_cores():
    from services.job_listing_submit_service import TAIWAN_CITY_DISTRICTS
    cores = set()
    for county, districts in TAIWAN_CITY_DISTRICTS.items():
        for full in districts:
            core = full[len(county):] if full.startswith(county) else full
            cores.add(core[:-1] if len(core) > 2 else core)
    return cores


_DISTRICT_CORES = _district_cores()
_SLOT_OF = {"locations": "location", "categories": "category", "shifts": "shift", "leaves": "leave",
            "pays": "pay", "worktype": "worktype", "brand": "brand"}


def _same_as_remembered(case, form, field) -> bool:
    """AI 把「沒有要改」的條件照原樣再填一次（記住桃園，AI 又填桃園）：結果跟留空一樣，不算錯。"""
    slot = _SLOT_OF.get(field)
    remembered = str((case.get("slots") or {}).get(slot) or "")
    if not slot or not remembered:
        return False
    got = form.get(field)
    if field == "locations":
        return {_norm_place(v) for v in got or []} == {_norm_place(v) for v in remembered.split("|")}
    if isinstance(got, list):
        return bool(got) and set(got) == set(remembered.split("|"))
    return bool(got) and got == remembered


def _norm_place(value: str) -> str:
    v = str(value).replace("臺", "台").strip()
    m = re.match(r"^(..[縣市])(.+[區鄉鎮市])$", v)
    county = ""
    if m:
        county, v = m.group(1), m.group(2)
    else:
        # 「台中西屯」「台南永康」「新竹東區」：縣市簡稱＋區名連寫
        m2 = re.match(r"^(台北|新北|桃園|台中|台南|高雄|基隆|新竹|嘉義|苗栗|彰化|南投|雲林|屏東|宜蘭|花蓮|台東|澎湖)(.{2,4})$", v)
        if m2 and (m2.group(2) in _DISTRICT_CORES or m2.group(2)[:-1] in _DISTRICT_CORES):
            county, v = m2.group(1), m2.group(2)
    if v in _KEEP_FULL:
        return v[:2]
    if len(v) > 2 and v[-1] in "縣市區鄉鎮":
        v = v[:-1]
    if county and v in _AMBIGUOUS:
        return county[:2] + v
    return v


def place_match(got: str, want: str) -> bool:
    """地名比對：一樣就算對；考題只寫「東區」時，AI 寫得更精確（「新竹東區」）也算對；
    考題寫了「新竹東區」，AI 只寫「東區」就不夠精確、算錯。"""
    return got == want or (want in _AMBIGUOUS and got.endswith(want))


def places_equal(got: set, want: set) -> bool:
    return all(any(place_match(g, w) for g in got) for w in want) and all(any(place_match(g, w) for w in want) for g in got)


def places_has(got: set, want: set) -> bool:
    return all(any(place_match(g, w) for g in got) for w in want)


def places_overlap(got: set, want: set) -> set:
    return {w for w in want if any(place_match(g, w) for g in got)}


def _norm_brand(value) -> str:
    return re.sub(r"[\s　()（）]", "", str(value or "")).lower()


def _brand_match(got, want) -> bool:
    """廠商名稱：AI 常多寫後綴（「momo購物」「Uber Eats」），互相包含就算對。"""
    g, w = _norm_brand(got), _norm_brand(want)
    if not w:
        return not g
    return bool(g) and (w in g or g in w)


def _values(form: dict, field: str):
    value = form.get(field)
    if field in _PLACE_FIELDS:
        return {_norm_place(v) for v in value or []}
    if isinstance(value, list):
        return set(value)
    return value


def _want(field, expected):
    if field in _PLACE_FIELDS:
        return {_norm_place(v) for v in expected}
    return set(expected) if isinstance(expected, list) else expected


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
        if expected in ([], "") and _same_as_remembered(case, form, field):
            continue
        if field == "brand":
            if not _brand_match(form.get("brand"), expected):
                problems.append(f"brand={form.get('brand')!r}，應該是 {expected!r}")
            continue
        got, want = _values(form, field), _want(field, expected)
        ok = places_equal(got, want) if field in _PLACE_FIELDS else got == want
        if not ok:
            problems.append(f"{field}={sorted(got) if isinstance(got, set) else got!r}，應該是 {sorted(want) if isinstance(want, set) else want!r}")
    for field, expected in (case.get("has") or {}).items():
        got, want = _values(form, field), _want(field, expected)
        if not (places_has(got, want) if field in _PLACE_FIELDS else want <= got):
            problems.append(f"{field}={sorted(got)}，應該包含 {sorted(want)}")
    for field, expected in (case.get("any") or {}).items():
        got, want = _values(form, field), _want(field, expected)
        if not (places_overlap(got, want) if field in _PLACE_FIELDS else got & want):
            problems.append(f"{field}={sorted(got)}，應該至少有 {'/'.join(expected)} 其中一個")
    for field, expected in (case.get("forbid") or {}).items():
        got, want = _values(form, field), _want(field, expected)
        bad = places_overlap(got, want) if field in _PLACE_FIELDS else got & want
        if bad:
            problems.append(f"{field} 不應該有 {sorted(bad)}")
    for field in case.get("nonempty") or []:
        if not form.get(field):
            problems.append(f"{field} 不應該是空的")
    return problems


def _run_case(case, thinking):
    history = [{"role": "招募顧問沛沛", "text": case["last_bot"]}] if case.get("last_bot") else []
    start = time.monotonic()
    try:
        form = understand_message(case["text"], case.get("slots") or {}, history, thinking_budget=thinking)
    except Exception as e:  # 單題出錯不能讓整份考試停掉
        print(f"[考試] {case['id']} 出錯：{e}", file=sys.stderr)
        form = None
    if form:
        # 正式上線時程式會再檢查一次需求單，考試也照同樣的規則整理地區（例如「新竹竹北」只算竹北）
        form["locations"] = drop_county_before_district(form["locations"], case["text"])
        # 正式上線時的交叉檢查（沒根據的丟掉、程式認得但 AI 漏掉的補上、否定方向以程式為準）
        form = cross_check_form(form, case["text"], case.get("last_bot", ""), case.get("slots") or {}, None)
    return form, time.monotonic() - start


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--thinking", type=int, default=0, help="Gemini 先想一想的額度（0＝不想，預設）")
    parser.add_argument("--cases", default=CASES_PATH, help="考題檔（預設是 scripts/ 裡那份）")
    parser.add_argument("--only", default="", help="只跑題目名稱包含這段字的題目")
    parser.add_argument("--failures-only", action="store_true", help="只印沒通過的題目（題目多的時候用）")
    parser.add_argument("--workers", type=int, default=8, help="同時問幾題（預設 8）")
    args = parser.parse_args()

    cases = [c for c in json.load(open(args.cases, encoding="utf-8")) if args.only in c["id"]]
    done = [0]

    def _job(case):
        result = _run_case(case, args.thinking)
        done[0] += 1
        if done[0] % 50 == 0:
            print(f"  已完成 {done[0]}/{len(cases)} 題", file=sys.stderr)
        return result

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        results = list(pool.map(_job, cases))

    latencies, failed, by_prefix = [], [], {}
    for case, (form, latency) in zip(cases, results):
        latencies.append(latency)
        problems = check(case, form)
        prefix = case["id"].split("-")[0]
        stat = by_prefix.setdefault(prefix, [0, 0])
        stat[0] += 1
        if problems:
            stat[1] += 1
            failed.append(case["id"])
        if problems or not args.failures_only:
            mark = "✅" if not problems else "❌"
            print(f"{mark} {case['id']}：「{case['text']}」 {latency:.1f} 秒")
        if problems:
            if case.get("slots") or case.get("last_bot"):
                print(f"      上下文：記住的條件 {json.dumps(case.get('slots') or {}, ensure_ascii=False)}｜沛沛上一句「{case.get('last_bot', '')[:60]}」")
            for p in problems:
                print(f"      {p}")
            shown = {k: v for k, v in (form or {}).items() if v}
            print(f"      AI 填的需求單：{json.dumps(shown, ensure_ascii=False)}")

    passed = len(cases) - len(failed)
    print("\n==============================")
    print(f"通過 {passed} / {len(cases)} 題（{passed / max(1, len(cases)):.0%}），thinking={args.thinking}")
    for prefix, (total, bad) in sorted(by_prefix.items()):
        print(f"  {prefix}：{total - bad}/{total}")
    if latencies:
        ordered = sorted(latencies)
        print(f"每題等待：中位數 {statistics.median(latencies):.1f} 秒，最慢的 5% 約 {ordered[max(0, int(len(ordered) * 0.95) - 1)]:.1f} 秒")
    if failed:
        print("沒通過的題目：" + "、".join(failed))


if __name__ == "__main__":
    main()
