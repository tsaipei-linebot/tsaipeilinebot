"""沛沛多輪對話考試（HANDOFF.md 第 81 項）。

照 scripts/conversation_eval_cases.json 裡寫好的對話劇本，一輪一輪跑「真正的沛沛流程」：
程式聽得懂的程式處理，聽不懂的問真的 Gemini（AI 需求單），真的篩職缺、組回覆，每一輪
檢查記住的條件、回覆、推的職缺對不對。同一段對話會用「原本的程式（off）」跟「開 AI 之後
（on）」各跑一次，直接比較。

不會發 LINE 給任何人、不會寫進 Firestore（對話記憶只存在這次考試裡）、不會寫 Notion，
也不影響線上的沛沛。職缺跟常見問答是從 Notion 讀最新的（讀取權限自動從 Cloud Run 的設定拿）。

在 Cloud Shell 執行（套件跟單句考試共用，裝過就不用再裝）：
    cd ~/tsaipeilinebot && git pull
    ~/peipei-venv/bin/python scripts/eval_conversations.py > ~/eval_conv.txt; tail -30 ~/eval_conv.txt

參數：
    --mode on／off／both（預設 both）
    --only 劇本名稱包含這段字才跑
    --workers 同時跑幾段對話（預設 6）
    --jobs-file 用本機的職缺 JSON 檔，不讀 Notion（Claude 的雲端環境用）
"""
import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
CASES_PATH = os.path.join(ROOT, "scripts", "conversation_eval_cases.json")

_ENV_NEEDED = ["NOTION_API_KEY", "NOTION_JOBS_DB_ID", "NOTION_FAQ_DB_ID"]
_SERVICE = ["recruitment-bot", "--region", "asia-east1", "--project", "tsaipei-505807"]


def _load_notion_env_from_cloud_run():
    """Notion 的讀取權限只放在 Cloud Run 的環境變數裡：用 gcloud 讀出來（只放進這次考試的記憶體）。"""
    missing = [k for k in _ENV_NEEDED if not os.getenv(k)]
    if not missing:
        return
    try:
        raw = subprocess.check_output(
            ["gcloud", "run", "services", "describe", *_SERVICE, "--format=json"], text=True, stderr=subprocess.DEVNULL)
    except Exception as e:
        sys.exit(f"讀不到 Cloud Run 的設定（{e}）。請確認是在 Cloud Shell 執行，而且專案是 tsaipei-505807。")
    env = json.loads(raw)["spec"]["template"]["spec"]["containers"][0].get("env", [])
    for item in env:
        name = item.get("name")
        if name not in missing:
            continue
        if "value" in item:
            os.environ[name] = item["value"]
        elif item.get("valueFrom", {}).get("secretKeyRef"):
            ref = item["valueFrom"]["secretKeyRef"]
            os.environ[name] = subprocess.check_output(
                ["gcloud", "secrets", "versions", "access", str(ref.get("key") or "latest"),
                 f"--secret={ref['name']}", "--project", "tsaipei-505807"], text=True).strip()
    still = [k for k in _ENV_NEEDED if not os.getenv(k)]
    if still:
        sys.exit(f"Cloud Run 設定裡找不到：{'、'.join(still)}")


def _setup(args):
    if args.jobs_file:
        sys.path.insert(0, ROOT)
        from tests import _env  # noqa: F401
        from tests import _stub_gcp
        _stub_gcp.install()
    else:
        _load_notion_env_from_cloud_run()


# ---------------- 對話環境：每段對話一個 user_id，記憶只存在這裡 ----------------

class Session:
    def __init__(self, user_id):
        self.user_id = user_id
        self.slots = dict(location="", category="", shift="", leave="", brand="", pay="", benefit="",
                          exclude="", worktype="", salary="", shown="")
        self.history = []
        self.understanding = []


SESSIONS = {}
_lock = threading.Lock()


class CardMarker:
    """代替 LINE 的職缺卡片：記住這張卡片列了哪些職缺。"""
    def __init__(self, jobs):
        self.jobs = list(jobs or [])


def _install_patches(h, matcher, jobs, faqs):
    def merge(uid, **kw):
        s = SESSIONS[uid]
        for k, v in kw.items():
            if v == h.CLEAR_SLOT:
                s.slots[k] = ""
            elif v:
                s.slots[k] = v
        return dict(s.slots)

    def clear(uid):
        s = SESSIONS[uid]
        for k in s.slots:
            s.slots[k] = ""

    def log_understanding(message, form, route, latency, mode, canonical=""):
        uid = getattr(threading.current_thread(), "eval_uid", None)
        if uid in SESSIONS:
            SESSIONS[uid].understanding.append(dict(route=route, canonical=canonical, latency=round(latency, 2)))

    h.fetch_jobs_data = lambda: jobs
    h.fetch_faqs_data = lambda: faqs
    h.get_user_history = lambda uid: list(SESSIONS[uid].history)
    h.append_user_history = lambda uid, role, text: SESSIONS[uid].history.append({"role": role, "text": text})
    h.get_user_slots = lambda uid: dict(SESSIONS[uid].slots)
    matcher.get_user_slots = lambda uid: dict(SESSIONS[uid].slots)
    h.update_user_slots = merge
    h.clear_user_slots = clear
    h.create_job_flex_card = lambda jobs_, *a, **k: CardMarker(jobs_)
    h._is_staffed_hours = lambda *a, **k: False
    h.log_ai_decision_event = lambda *a, **k: None
    h.append_unresolved_question_for_followup = lambda *a, **k: None
    h.append_unresolved_faq_to_notion = lambda *a, **k: None
    h.format_full_job_detail_with_ai = lambda job, loc: f"📋【職缺名稱：{job.get('職缺名稱', '')}】"
    h.log_understanding = log_understanding
    h.AI_DECISION_SYNC_TIMEOUT_SECONDS = 60


class FakeLineApi:
    def __init__(self):
        self.messages = []
        self.event = threading.Event()

    def reply_message(self, token, messages):
        self.messages.extend(messages if isinstance(messages, list) else [messages])
        self.event.set()

    def push_message(self, uid, messages):
        self.reply_message(None, messages)

    def get_profile(self, uid):
        class P:
            display_name = "考試"
        return P()


class FakeEvent:
    def __init__(self, uid, text):
        self.reply_token = "eval-token"
        self.source = type("Src", (), {"user_id": uid, "type": "user", "group_id": None})()
        self.message = type("Msg", (), {"text": text})()


def _say(h, session, text):
    threading.current_thread().eval_uid = session.user_id
    session.understanding = []
    api = FakeLineApi()
    start = time.monotonic()
    h.process_user_message(FakeEvent(session.user_id, text), api)
    texts, buttons, jobs = [], [], []
    for m in api.messages:
        if isinstance(m, CardMarker):
            jobs.extend(m.jobs)
            continue
        t = getattr(m, "text", None)
        if isinstance(t, str):
            texts.append(t)
        qr = getattr(m, "quick_reply", None)
        for it in (getattr(qr, "items", None) or []):
            buttons.append(it.action.text)
    return dict(
        texts=texts, buttons=buttons, jobs=jobs, latency=time.monotonic() - start,
        slots={k: v for k, v in session.slots.items() if v and k != "shown"},
        ai=list(session.understanding),
    )


# ---------------- 檢查 ----------------

_KEEP_FULL = {"新竹縣", "新竹市", "嘉義縣", "嘉義市"}


def _norm_place(value):
    v = str(value).replace("臺", "台").strip()
    m = re.match(r"^(..[縣市])(.+[區鄉鎮市])$", v)
    if m:
        v = m.group(2)
    if v in _KEEP_FULL:
        return v[:2]
    if len(v) > 2 and v[-1] in "縣市區鄉鎮":
        v = v[:-1]
    return v


def _slot_set(slot, value):
    value = str(value or "")
    if not value:
        return set()
    parts = value.split(";") if slot == "exclude" else value.split("|")
    if slot == "location":
        return {_norm_place(p) for p in parts}
    if slot == "exclude":
        return {p.split(":", 1)[0] + ":" + _norm_place(p.split(":", 1)[1]) if p.startswith("location:") else p for p in parts}
    return set(parts)


def _want_set(slot, values):
    if isinstance(values, str):
        values = [values] if values else []
    joined = (";" if slot == "exclude" else "|").join(values)
    return _slot_set(slot, joined)


def check_turn(turn, got):
    problems = []
    for slot, expected in (turn.get("slots") or {}).items():
        if _slot_set(slot, got["slots"].get(slot)) != _want_set(slot, expected):
            problems.append(f"{slot}＝「{got['slots'].get(slot, '')}」，應該是「{expected if isinstance(expected, str) else '|'.join(expected)}」")
    for slot, expected in (turn.get("slots_has") or {}).items():
        want = _want_set(slot, expected)
        if not want <= _slot_set(slot, got["slots"].get(slot)):
            problems.append(f"{slot}＝「{got['slots'].get(slot, '')}」，應該包含 {sorted(want)}")
    for slot, expected in (turn.get("slots_not") or {}).items():
        bad = _want_set(slot, expected) & _slot_set(slot, got["slots"].get(slot))
        if bad:
            problems.append(f"{slot}＝「{got['slots'].get(slot, '')}」，不應該有 {sorted(bad)}")
    everything = "\n".join(got["texts"] + got["buttons"])
    if turn.get("reply_has_any") and not any(w in everything for w in turn["reply_has_any"]):
        problems.append(f"回覆裡應該要有「{'／'.join(turn['reply_has_any'])}」其中之一")
    for w in turn.get("reply_not") or []:
        if w in everything:
            problems.append(f"回覆裡不應該有「{w}」")
    if turn.get("jobs") is True and not got["jobs"]:
        problems.append("應該要列出職缺卡片")
    if turn.get("jobs") is False and got["jobs"]:
        problems.append(f"不應該列出職缺卡片（列了 {len(got['jobs'])} 筆）")
    if not got["texts"] and not got["jobs"]:
        problems.append("沛沛沒有回覆")
    return problems


def run_conversation(h, case, mode):
    uid = f"eval-{mode}-{case['id']}"
    with _lock:
        SESSIONS[uid] = Session(uid)
    session = SESSIONS[uid]
    turns = []
    for turn in case["turns"]:
        try:
            got = _say(h, session, turn["say"])
            problems = check_turn(turn, got)
        except Exception as e:  # 程式當掉也要記下來，不能讓整份考試停掉
            got = dict(texts=[], buttons=[], jobs=[], latency=0, slots={}, ai=[])
            problems = [f"程式出錯：{type(e).__name__}: {e}"]
        turns.append(dict(say=turn["say"], problems=problems, got=got))
    return turns


def _brief(got):
    reply = " / ".join(t.replace("\n", " ") for t in got["texts"])[:120]
    ai = got["ai"][-1] if got["ai"] else None
    ai_text = f"｜AI：{ai['route']}「{ai['canonical']}」" if ai else ""
    return f"回覆：{reply}｜條件：{json.dumps(got['slots'], ensure_ascii=False)}｜職缺 {len(got['jobs'])} 筆{ai_text}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="both", choices=["on", "off", "both"])
    parser.add_argument("--cases", default=CASES_PATH, help="考題檔（預設是 scripts/ 裡那份）")
    parser.add_argument("--only", default="")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--jobs-file", default="")
    parser.add_argument("--faqs-file", default="")
    parser.add_argument("--show-all", action="store_true", help="通過的對話也印出每一輪的回覆")
    args = parser.parse_args()
    _setup(args)

    import handlers.message_handler as h
    import services.matcher_service as matcher
    if args.jobs_file:
        jobs = json.load(open(args.jobs_file, encoding="utf-8"))
        faqs = json.load(open(args.faqs_file, encoding="utf-8")) if args.faqs_file else []
    else:
        from services.notion_service import fetch_jobs_data, fetch_faqs_data
        jobs, faqs = fetch_jobs_data(), fetch_faqs_data()
    if not jobs:
        sys.exit("讀不到職缺資料，請確認 Notion 權限。")
    sys.stderr.write(f"職缺 {len(jobs)} 筆、常見問答 {len(faqs)} 筆\n")
    _install_patches(h, matcher, jobs, faqs)

    cases = [c for c in json.load(open(args.cases, encoding="utf-8")) if args.only in c["id"]]
    modes = ["off", "on"] if args.mode == "both" else [args.mode]
    results = {}
    real_stdout = sys.stdout
    for mode in modes:
        h.AI_UNDERSTANDING_MODE = mode
        sys.stderr.write(f"\n開始跑 {mode}（{len(cases)} 段對話）…\n")
        sys.stdout = open(os.devnull, "w")  # 程式本身印的訊息太多，考試時先不顯示
        done = [0]

        def _job(case):
            r = run_conversation(h, case, mode)
            done[0] += 1
            if done[0] % 10 == 0:
                sys.stderr.write(f"  {mode}：{done[0]}/{len(cases)}\n")
            return case["id"], r
        try:
            with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
                results[mode] = dict(pool.map(_job, cases))
        finally:
            sys.stdout.close()
            sys.stdout = real_stdout

    # ---------------- 報告 ----------------
    by_id = {c["id"]: c for c in cases}
    print("=" * 40)
    for mode in modes:
        convs = results[mode]
        turn_total = sum(len(t) for t in convs.values())
        turn_fail = sum(1 for t in convs.values() for x in t if x["problems"])
        conv_pass = sum(1 for t in convs.values() if not any(x["problems"] for x in t))
        latencies = [x["got"]["latency"] for t in convs.values() for x in t if x["got"]["latency"]]
        ai_turns = sum(1 for t in convs.values() for x in t if x["got"]["ai"])
        lat = f"，每輪中位數 {statistics.median(latencies):.1f} 秒、最慢 {max(latencies):.1f} 秒" if latencies else ""
        print(f"【{mode}】整段全對 {conv_pass}/{len(convs)} 段；單輪 {turn_total - turn_fail}/{turn_total} 輪正確；"
              f"問了 AI 需求單 {ai_turns} 輪{lat}")
    if len(modes) == 2:
        off_ok = {i for i, t in results["off"].items() if not any(x["problems"] for x in t)}
        on_ok = {i for i, t in results["on"].items() if not any(x["problems"] for x in t)}
        print(f"開 AI 之後變好：{len(on_ok - off_ok)} 段；變差：{len(off_ok - on_ok)} 段")
        if off_ok - on_ok:
            print("變差的對話：" + "、".join(sorted(off_ok - on_ok)))
    print("=" * 40)

    for mode in modes:
        print(f"\n########## {mode} 沒通過的對話 ##########")
        for cid, turns in results[mode].items():
            failed = any(x["problems"] for x in turns)
            if not failed and not args.show_all:
                continue
            print(f"\n■ {cid}（{by_id[cid].get('note', '')}）")
            for i, x in enumerate(turns, 1):
                mark = "❌" if x["problems"] else "✅"
                print(f"  {mark} 第{i}輪「{x['say']}」")
                if x["problems"] or args.show_all:
                    for p in x["problems"]:
                        print(f"      ・{p}")
                    print(f"      {_brief(x['got'])}")


if __name__ == "__main__":
    main()
