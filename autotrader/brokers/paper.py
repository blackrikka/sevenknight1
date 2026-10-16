"""모의 체결 브로커. 실제 시세로 가상의 돈을 굴린다 (수수료·매도세 반영)."""

from __future__ import annotations

import math
from pathlib import Path

from autotrader.brokers.base import BrokerError, OrderResult, Position
from autotrader.config import Costs
from autotrader.store import JsonStore


class PaperBroker:
    def __init__(self, name: str, state_dir: Path, starting_cash: int, costs: Costs, kinds: dict[str, str]):
        self.name = name
        self.costs = costs
        self.kinds = kinds
        self.store = JsonStore(state_dir / f"paper_{name}.json", {"cash": starting_cash, "positions": {}, "seq": 0})

    def cash(self) -> int:
        return int(self.store.load()["cash"])

    def positions(self) -> dict[str, Position]:
        return {
            code: Position(code=code, qty=p["qty"], avg_price=p["avg_price"])
            for code, p in self.store.load()["positions"].items()
        }

    def market_order(self, side: str, code: str, qty: int, ref_price: int) -> OrderResult:
        if qty <= 0:
            raise BrokerError("수량은 1 이상이어야 합니다")
        state = self.store.load()
        amount = ref_price * qty
        fee = math.floor(amount * self.costs.fee_rate)
        tax = 0
        positions = state["positions"]
        if side == "buy":
            if amount + fee > state["cash"]:
                raise BrokerError(f"[{self.name}] 잔고 부족: 필요 {amount + fee}원, 보유 {state['cash']}원")
            held = positions.get(code, {"qty": 0, "avg_price": 0.0})
            new_qty = held["qty"] + qty
            held["avg_price"] = (held["avg_price"] * held["qty"] + amount) / new_qty
            held["qty"] = new_qty
            positions[code] = held
            state["cash"] -= amount + fee
        elif side == "sell":
            held = positions.get(code)
            if not held or held["qty"] < qty:
                raise BrokerError(f"[{self.name}] {code} 보유 수량 부족")
            tax = math.floor(amount * self.costs.sell_tax_rate(self.kinds.get(code, "stock")))
            held["qty"] -= qty
            if held["qty"] == 0:
                del positions[code]
            state["cash"] += amount - fee - tax
        else:
            raise BrokerError(f"알 수 없는 주문 방향: {side}")
        state["seq"] += 1
        self.store.save(state)
        return OrderResult(code=code, side=side, qty=qty, ref_price=ref_price, order_id=f"paper-{state['seq']}", fee=fee, tax=tax)


class StaticQuotes:
    """테스트·오프라인용 고정 시세."""

    def __init__(self, prices: dict[str, int]):
        self.prices = prices

    def price(self, code: str) -> int:
        if code not in self.prices:
            raise BrokerError(f"{code} 시세 없음")
        return self.prices[code]

