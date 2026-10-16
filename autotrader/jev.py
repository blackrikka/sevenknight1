"""Jev(TypeSafe System One)로 매수 직전 뉴스 리스크를 확인한다.

역할을 좁게 잡았다: 가격·수량·타이밍은 코드(전략)가 정하고, Jev 는 "오늘 이 종목 헤드라인에
사면 안 될 만한 악재가 있나?" 하나만 판단한다. 호출은 매수 후보가 생겼을 때만, 하루 최대
max_calls_per_day 번, 같은 날 같은 종목은 캐시를 쓴다 → API 비용이 거의 들지 않는다.
"""

from __future__ import annotations

import logging
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from autotrader.config import Asset, JevConfig
from autotrader.store import DailyCounter, JsonStore

log = logging.getLogger(__name__)


class SystemOneClient(Protocol):
    def system_one(self, state: Any, questions: Any, *, model: str | None = None) -> Any: ...


@dataclass(frozen=True)
class Verdict:
    veto: bool
    risk_probability: float | None
    sentiment: float | None
    reason: str


QUESTIONS_SPEC = {
    "serious_negative": {
        "type": "noul",
        "instructions": (
            "Based only on `headlines`, is there a serious negative development that would make buying "
            "`asset.name` (Korean listed `asset.kind`, code `asset.code`) today imprudent? "
            "Count issuer-specific events (fraud, accounting issues, trading halt, delisting risk, major lawsuit, "
            "large earnings shock, ETF liquidation) or a broad Korean market shock (crash, circuit breaker, crisis). "
            "Routine price moves, analyst chatter, ads and unrelated companies do not count."
        ),
        "criteria": {
            "true": "At least one headline reports such a serious negative development.",
            "false": "No headline reports a serious negative development; news is neutral, positive, routine, or unrelated.",
        },
    },
    "sentiment": {
        "type": "score",
        "instructions": "Overall tone of `headlines` toward `asset.name` for a short-term holder.",
        "criteria": [
            "Clearly negative: headlines mostly report problems or losses for it",
            "Somewhat negative",
            "Neutral or mixed, or headlines are not really about it",
            "Somewhat positive",
            "Clearly positive: headlines mostly report gains, strong results or inflows",
        ],
    },
}


def load_headlines(asset: Asset, cfg: JevConfig, base_dir: Path) -> list[str]:
    lines: list[str] = []
    local = base_dir / cfg.headlines_dir / f"{asset.code}.txt"
    if local.exists():
        lines += [l.strip() for l in local.read_text(encoding="utf-8").splitlines() if l.strip()]
    if cfg.rss_url_template:
        url = cfg.rss_url_template.format(name=urllib.parse.quote(asset.name), code=asset.code)
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                root = ET.fromstring(resp.read())
            lines += [t.text.strip() for t in root.iter("title") if t.text][1:]  # 첫 title 은 피드 이름
        except Exception as e:  # 뉴스 수집 실패는 매매를 막지 않는다
            log.warning("RSS 수집 실패 (%s): %s", asset.code, e)
    seen: set[str] = set()
    unique = [l for l in lines if not (l in seen or seen.add(l))]
    return unique[: cfg.max_headlines]


class JevGate:
    def __init__(self, cfg: JevConfig, client: SystemOneClient | None, state_dir: Path, base_dir: Path):
        self.cfg = cfg
        self.client = client
        self.base_dir = base_dir
        self.counter = DailyCounter(state_dir)
        self.cache = JsonStore(state_dir / "jev_cache.json", {})

    def check_buy(self, asset: Asset, date: str) -> Verdict:
        if not self.cfg.enabled or self.client is None:
            return Verdict(False, None, None, "Jev 꺼짐")
        cache = self.cache.load()
        key = f"{date}:{asset.code}"
        if key in cache:
            return Verdict(**cache[key])

        headlines = load_headlines(asset, self.cfg, self.base_dir)
        if not headlines:
            return Verdict(False, None, None, "헤드라인 없음 → 판단 생략")
        if self.counter.get(date, "jev_calls") >= self.cfg.max_calls_per_day:
            return Verdict(False, None, None, "오늘 Jev 호출 한도 소진 → 판단 생략")

        self.counter.incr(date, "jev_calls")
        try:
            resp = self.client.system_one(
                state={"asset": {"code": asset.code, "name": asset.name, "kind": asset.kind}, "date": date, "headlines": headlines},
                questions=build_questions(),
                model=self.cfg.model,
            )
        except Exception as e:
            log.warning("Jev 호출 실패: %s", e)
            return Verdict(False, None, None, f"Jev 호출 실패 → 판단 생략 ({type(e).__name__})")

        p = float(resp.answers["serious_negative"].noul)
        sentiment = float(resp.answers["sentiment"].score)
        veto = p >= self.cfg.veto_threshold
        verdict = Verdict(veto, p, sentiment, f"악재확률 {p:.2f} (기준 {self.cfg.veto_threshold}), 분위기 {sentiment:.1f}/4")
        cache = {k: v for k, v in cache.items() if k.startswith(date)}
        cache[key] = verdict.__dict__
        self.cache.save(cache)
        return verdict


def build_questions() -> dict[str, Any]:
    return {name: dict(spec) for name, spec in QUESTIONS_SPEC.items()}


def make_client(api_key: str) -> SystemOneClient:
    from typesafe_sdk import TypeSafeClient  # pip install typesafe-sdk

    return TypeSafeClient(api_key=api_key)
