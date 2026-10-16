from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable

from autotrader.brokers.base import BrokerError

Transport = Callable[[str, dict[str, str], dict[str, Any]], dict[str, Any]]

# 연속조회(cont-yn/next-key)처럼 응답 헤더가 필요한 경우를 위해 본문 dict 에 함께 실어 보낸다
RESPONSE_HEADERS = "_response_headers"


def post_json(url: str, headers: dict[str, str], body: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
            data[RESPONSE_HEADERS] = {k.lower(): v for k, v in resp.headers.items()}
            return data
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        raise BrokerError(f"HTTP {e.code} {url}: {detail}") from e
    except urllib.error.URLError as e:
        raise BrokerError(f"연결 실패 {url}: {e.reason}") from e
