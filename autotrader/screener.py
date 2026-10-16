"""1주 가격이 예산 이하인 매수 후보를 키움 종목 리스트에서 골라낸다.

주문은 내지 않는다. 결과를 보고 사람이 config.toml 의 universe 에 옮겨 넣는 구조다.
싼 종목일수록 위험한 종목(인버스·레버리지, 소형주, 관리·경고 종목)이 섞이기 쉬워서 거르는 단계를 둔다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from autotrader.brokers.base import BrokerError
from autotrader.brokers.kiwoom import _num
from autotrader.config import ScreenConfig

MARKETS = {"etf": "8", "kospi": "0", "kosdaq": "10"}


class ListingSource(Protocol):
    def listed(self, market: str) -> list[dict]: ...

    def quote(self, code: str) -> dict[str, int]: ...


@dataclass(frozen=True)
class Candidate:
    code: str
    name: str
    kind: str  # "etf" | "stock"
    price: int
    market_cap: int
    trade_value: int  # 오늘 거래대금 (현재가 × 거래량)


@dataclass
class ScreenResult:
    candidates: list[Candidate]
    rejected: dict[str, int]  # 탈락 사유별 개수
    scanned: int


def _warned(row: dict) -> bool:
    """관리종목, 투자경고·위험, 거래정지, 정리매매 등은 제외."""
    if str(row.get("orderWarning", "0")).strip() not in ("", "0"):
        return True
    text = f"{row.get('auditInfo', '')} {row.get('state', '')}"
    return any(word in text for word in ("관리", "정지", "정리매매", "투자경고", "투자위험", "환기"))


def screen(source: ListingSource, cfg: ScreenConfig, *, sleep: float = 0.25) -> ScreenResult:
    rejected: dict[str, int] = {}

    def reject(reason: str) -> None:
        rejected[reason] = rejected.get(reason, 0) + 1

    pool: list[Candidate] = []
    scanned = 0
    for market in cfg.markets:
        kind = "etf" if market == "etf" else "stock"
        for row in source.listed(MARKETS[market]):
            scanned += 1
            code, name = str(row.get("code", "")).strip(), str(row.get("name", "")).strip()
            price = _num(row.get("lastPrice"))
            if not code or price <= 0:
                reject("가격 정보 없음")
            elif price > cfg.max_price_krw:
                reject(f"{cfg.max_price_krw:,}원 초과")
            elif any(k.lower() in name.lower() for k in cfg.exclude_keywords):
                reject("제외 키워드 (인버스·레버리지 등)")
            elif _warned(row):
                reject("관리·경고·정지 종목")
            elif kind == "stock" and price * _num(row.get("listCount")) < cfg.min_market_cap_krw:
                reject("시가총액 미달")
            else:
                pool.append(Candidate(code, name, kind, price, price * _num(row.get("listCount")), 0))

    # 거래대금은 종목마다 시세 조회가 필요하므로 시총 큰 순으로 상위 일부만 확인한다 (호출 한도 보호)
    pool.sort(key=lambda c: c.market_cap, reverse=True)
    candidates: list[Candidate] = []
    for c in pool[: cfg.max_quote_checks]:
        try:
            q = source.quote(c.code)
        except BrokerError:
            reject("시세 조회 실패")
            continue
        finally:
            time.sleep(sleep)
        value = q["price"] * q["volume"]
        if q["price"] > cfg.max_price_krw:
            reject(f"{cfg.max_price_krw:,}원 초과")
        elif value < cfg.min_trade_value_krw:
            reject("거래대금 부족")
        else:
            candidates.append(Candidate(c.code, c.name, c.kind, q["price"], c.market_cap, value))
    if len(pool) > cfg.max_quote_checks:
        rejected["시세 미확인 (max_quote_checks 초과)"] = len(pool) - cfg.max_quote_checks

    candidates.sort(key=lambda c: c.trade_value, reverse=True)
    return ScreenResult(candidates, rejected, scanned)


def to_universe_toml(candidates: list[Candidate]) -> str:
    lines = ["universe = ["]
    for c in candidates:
        name = c.name.replace("\\", "").replace('"', "")
        lines.append(f'  {{ code = "{c.code}", name = "{name}", kind = "{c.kind}" }},  # {c.price:,}원')
    lines.append("]")
    return "\n".join(lines)
