from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class BrokerError(Exception):
    pass


@dataclass
class Position:
    code: str
    qty: int
    avg_price: float


@dataclass
class OrderResult:
    code: str
    side: str  # "buy" | "sell"
    qty: int
    ref_price: int  # 주문 시점 기준가 (실계좌에서는 실제 체결가와 다를 수 있음)
    order_id: str
    fee: int = 0
    tax: int = 0


class QuoteSource(Protocol):
    def price(self, code: str) -> int: ...


class Broker(Protocol):
    name: str

    def cash(self) -> int: ...

    def positions(self) -> dict[str, Position]: ...

    def market_order(self, side: str, code: str, qty: int, ref_price: int) -> OrderResult: ...
