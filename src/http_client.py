"""共用 HTTP 工具。

實測踩到的坑（務必保留這些處理）：
1. openFDA 在查詢 0 結果時回 HTTP 404，不是 200 帶空陣列。
   若不處理會被誤判為端點故障，讓整個模組降級。
2. openFDA 片語查詢語法：device_name:%22tracheal+tube+holder%22
   （%22 包住片語、+ 當空白）。用 raw 引號或 %20 會直接 404。
"""

from __future__ import annotations

import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_UA = "MedtechDevAssistant/1.0 (research; university incubation center)"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def get_json(url: str, timeout: float = 20.0, allow404: bool = False,
             ua: str = DEFAULT_UA) -> dict:
    """GET 並解析 JSON。allow404=True 時把 404/400 視為空結果。"""
    req = Request(url, headers={"User-Agent": ua, "Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))
    except HTTPError as exc:
        if allow404 and exc.code in (400, 404):
            return {"meta": {"results": {"total": 0}}, "results": [], "_empty": True}
        raise


def get_text(url: str, timeout: float = 30.0, ua: str = BROWSER_UA) -> str:
    req = Request(url, headers={"User-Agent": ua})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def get_with_retry(url: str, *, retries: int = 3, base_delay: float = 4.0,
                   min_bytes: int = 5000, timeout: float = 30.0,
                   ua: str = BROWSER_UA) -> str | None:
    """抓 HTML 並退避重試。

    有些站（如 FreePatentsOnline）會間歇回空頁或連線失敗；
    min_bytes 用來判斷是否為實質回應，過短視為失敗。
    """
    for attempt in range(retries):
        try:
            body = get_text(url, timeout=timeout, ua=ua)
            if len(body) >= min_bytes:
                return body
        except (HTTPError, URLError, TimeoutError, OSError):
            pass
        if attempt < retries - 1:
            time.sleep(base_delay * (attempt + 1))
    return None


def fda_search_term(field: str, phrase: str) -> str:
    """組 openFDA 查詢運算式。"""
    return f'{field}:%22' + phrase.replace(" ", "+") + '%22'
