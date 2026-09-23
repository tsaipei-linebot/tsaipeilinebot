"""三個爬蟲共用的資料結構與 HTTP client。"""
import time
from dataclasses import dataclass, field

import requests

REQUEST_TIMEOUT = 15
# 每次請求至少間隔 1.5 秒，避免對平台造成負擔（跟原 repo 一樣，不要調得更激進）
REQUEST_DELAY_SECONDS = 1.5
MAX_PAGES_PER_SITE = 3
SEARCH_KEYWORDS = ["人力派遣", "人力仲介", "人力資源", "人才派遣", "人力銀行"]
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass
class JobLead:
    source: str
    job_id: str
    job_title: str
    company_name: str
    job_url: str
    company_url: str = ""
    area: str = ""
    work_address: str = ""
    update_date: str = ""
    # 從職缺內文抓到的聯絡資訊：這是「刊登的派遣公司」的，不是要派公司的
    phone: str = ""
    phone_ext: str = ""
    email: str = ""
    matched_keyword: str = ""
    extra: dict = field(default_factory=dict)


class DeadlineReached(Exception):
    """時間上限到了，呼叫端收到後停止抓取、保留已經抓到的資料。"""


class HttpClient:
    def __init__(self, delay: float = REQUEST_DELAY_SECONDS, retries: int = 2, deadline: float = None):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.delay = delay
        self.retries = retries
        self.deadline = deadline
        self._last_request_ts = 0.0

    def _check_deadline(self):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise DeadlineReached()

    def _throttle(self):
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)

    def get(self, url: str, **kwargs) -> requests.Response:
        last_exc = None
        for attempt in range(self.retries + 1):
            self._check_deadline()
            self._throttle()
            try:
                resp = self.session.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
                self._last_request_ts = time.monotonic()
                if resp.status_code == 200:
                    return resp
                last_exc = RuntimeError(f"HTTP {resp.status_code} for {url}")
                if 400 <= resp.status_code < 500:
                    # 4xx（例如 1111 的 403）重試也不會成功，直接放棄
                    raise last_exc
            except requests.RequestException as exc:
                last_exc = exc
            time.sleep(1.0 * (attempt + 1))
        raise last_exc
