#!/usr/bin/env python3
"""沛沛招募機器人 - 內部壓力測試腳本。

用法範例：
    python3 scripts/load_test.py \\
        --url https://recruitment-bot-xxxxxxxx.a.run.app \\
        --secret 你設定的LOAD_TEST_SECRET \\
        --concurrency 5 --total 30

這支腳本會打 main.py 新增的 /internal/load-test-message 端點，該端點會執行
「跟正式流量完全一樣」的 Notion 讀取／Firestore session 讀寫／Gemini 決策邏輯，
唯一的差別是最後一步不會真的把回覆送給任何真實 LINE 使用者（用一個假的
LineBotApi 頂替掉，只記錄下來，不對外發送任何請求）。

事前準備（部署端）：
    1. 去 Cloud Run 服務的環境變數，設定 LOAD_TEST_SECRET 為一組你自己選的
       隨機字串（不要用這個腳本裡的範例值，也不要 commit 進 git）。
    2. 重新部署，讓新程式碼跟這個環境變數生效。
    3. 跑這支腳本時，用 --secret 帶入同一組字串。

安全提醒：
    - 這支腳本會真的呼叫 Vertex AI Gemini（花錢、佔配額），請先從小併發量、
      小總數開始（預設 concurrency=5、total=30），觀察 Cloud Run / Vertex AI
      的 metrics 跟帳單沒問題後，再視情況往上加。
    - 免費試用帳戶的配額比較低，避免一次衝太大量。
    - 每個模擬使用者的 user_id 都會加上 loadtest- 前綴，方便測完之後在
      Firestore 主控台批次清除這些測試用的 session 文件。
"""
import argparse
import random
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# 涵蓋這個 repo 這幾輪測試過的真實情境（地區/品牌跨區退讓/FAQ/類別/轉折語氣/
# 領薪方式等），比起重複打同一句話更能反映真實使用者的訊息多樣性。
DEFAULT_SAMPLE_MESSAGES = [
    "五股有工作嗎",
    "有美光的工作嗎",
    "新莊",
    "早班",
    "有理貨的工作嗎",
    "發薪日是什麼時候",
    "謝謝，不過還想問薪資怎麼算",
    "有沒有日領的工作",
    "換個條件",
    "都給我看看",
    "桃園門市",
    "有momo的工作嗎",
]


def send_one(base_url: str, secret: str, user_id: str, text: str, timeout: float) -> dict:
    start = time.monotonic()
    try:
        resp = requests.post(
            f"{base_url.rstrip('/')}/internal/load-test-message",
            json={"user_id": user_id, "text": text},
            headers={"X-Load-Test-Secret": secret},
            timeout=timeout,
        )
        wall_seconds = time.monotonic() - start
        result = {
            "user_id": user_id,
            "text": text,
            "status_code": resp.status_code,
            "wall_seconds": wall_seconds,
        }
        if resp.status_code == 200:
            body = resp.json()
            result["server_elapsed_seconds"] = body.get("elapsed_seconds")
            result["reply"] = body.get("reply")
        else:
            result["error_body"] = resp.text[:300]
        return result
    except requests.exceptions.RequestException as e:
        return {
            "user_id": user_id,
            "text": text,
            "status_code": None,
            "wall_seconds": time.monotonic() - start,
            "error_body": str(e)[:300],
        }


def percentile(values: list, pct: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    idx = min(len(values) - 1, int(len(values) * pct))
    return values[idx]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True, help="Cloud Run 服務的 base URL（不含路徑），例如 https://recruitment-bot-xxxx.a.run.app")
    parser.add_argument("--secret", required=True, help="要跟 Cloud Run 環境變數 LOAD_TEST_SECRET 一致")
    parser.add_argument("--concurrency", type=int, default=5, help="同時並發請求數（預設 5，建議先從小量開始）")
    parser.add_argument("--total", type=int, default=30, help="總共發送的請求數（預設 30）")
    parser.add_argument("--distinct-users", type=int, default=20, help="模擬幾個不同的求職者（會循環重複使用，模擬部分回頭客的 session 累積效果）")
    parser.add_argument("--timeout", type=float, default=60.0, help="單一請求逾時秒數（預設 60，因為 Gemini 單輪偶爾會到 10 秒以上）")
    parser.add_argument("--yes", action="store_true", help="略過開始前的確認提示，直接執行")
    args = parser.parse_args()

    print(f"目標：{args.url}")
    print(f"併發：{args.concurrency}　總數：{args.total}　模擬使用者數：{args.distinct_users}")
    print("此測試會真的呼叫 Vertex AI Gemini（花錢、佔配額），請確認金額/配額在可接受範圍內。")
    if not args.yes:
        confirm = input("確定要開始嗎？(y/N): ").strip().lower()
        if confirm != "y":
            print("已取消。")
            sys.exit(0)

    jobs = []
    for i in range(args.total):
        user_id = f"loadtest-{i % args.distinct_users:04d}"
        text = random.choice(DEFAULT_SAMPLE_MESSAGES)
        jobs.append((user_id, text))

    results = []
    start_all = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [executor.submit(send_one, args.url, args.secret, uid, text, args.timeout) for uid, text in jobs]
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            status = result["status_code"]
            wall = result["wall_seconds"]
            print(f"[{i}/{len(jobs)}] status={status} wall={wall:.2f}s user={result['user_id']} text={result['text']!r}")
    total_wall = time.monotonic() - start_all

    ok_results = [r for r in results if r["status_code"] == 200]
    error_results = [r for r in results if r["status_code"] != 200]
    wall_times = [r["wall_seconds"] for r in ok_results]
    server_times = [r["server_elapsed_seconds"] for r in ok_results if r.get("server_elapsed_seconds") is not None]

    print("\n" + "=" * 60)
    print(f"總耗時：{total_wall:.2f}s　總請求數：{len(results)}　成功：{len(ok_results)}　失敗：{len(error_results)}")

    if wall_times:
        print("\n[整趟請求時間（含網路）]")
        print(f"  min={min(wall_times):.2f}s  p50={percentile(wall_times, 0.5):.2f}s  "
              f"p95={percentile(wall_times, 0.95):.2f}s  p99={percentile(wall_times, 0.99):.2f}s  max={max(wall_times):.2f}s")

    if server_times:
        print("\n[伺服器端純處理時間（process_user_message 內部，不含網路）]")
        print(f"  min={min(server_times):.2f}s  p50={percentile(server_times, 0.5):.2f}s  "
              f"p95={percentile(server_times, 0.95):.2f}s  p99={percentile(server_times, 0.99):.2f}s  max={max(server_times):.2f}s")
        over_line_limit = [t for t in server_times if t > 25]
        if over_line_limit:
            print(f"  ⚠️  有 {len(over_line_limit)} 筆超過 25 秒，逼近 LINE 30 秒 reply token 逾時上限，建議留意")

    if error_results:
        print(f"\n[錯誤明細，前 10 筆]")
        for r in error_results[:10]:
            print(f"  status={r['status_code']} user={r['user_id']} error={r.get('error_body', '')}")

    print("\n測試完成。如果要清除這次測試留在 Firestore 的 session 資料，")
    print("去 Firestore 主控台的 user_sessions collection，刪除文件 ID 開頭是 loadtest- 的紀錄即可。")


if __name__ == "__main__":
    main()
