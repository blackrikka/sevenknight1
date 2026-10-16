"""추세 추종 전략: 이동평균 위에 있으면 보유, 아래로 깨지거나 손절선에 닿으면 현금.

계좌 예산이 수천 원이라 한 번에 한 종목만 들고, 하루 한 번 판단한다.
자주 사고팔수록 수수료·세금이 수익을 갉아먹기 때문에 일부러 느리게 만들었다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from autotrader.brokers.base import Position
from autotrader.config import Asset, Costs, RiskConfig


@dataclass(frozen=True)
class Decision:
    action: str  # "buy" | "sell" | "hold"
    code: str | None = None
    qty: int = 0
    price: int = 0
    reason: str = ""


def moving_average(closes: list[int], days: int) -> float | None:
    if days <= 0 or len(closes) < days:
        return None
    return sum(closes[-days:]) / days


def decide(
    *,
    universe: tuple[Asset, ...],
    prices: dict[str, int],
    history: dict[str, list[int]],
    positions: dict[str, Position],
    cash: int,
    budget_krw: int,
    ma_days: int,
    risk: RiskConfig,
    costs: Costs,
) -> Decision:
    # 1) 보유 중이면 청산 조건부터 본다
    for code, pos in positions.items():
        price = prices.get(code)
        if price is None:
            continue
        if pos.avg_price > 0 and price <= pos.avg_price * (1 - risk.stop_loss_pct):
            return Decision("sell", code, pos.qty, price, f"손절: {price}원 ≤ 평단 {pos.avg_price:.0f}원 -{risk.stop_loss_pct:.0%}")
        ma = moving_average(history.get(code, []), ma_days)
        if ma is not None and price < ma:
            return Decision("sell", code, pos.qty, price, f"추세 이탈: {price}원 < {ma_days}일선 {ma:.0f}원")
    if positions:
        return Decision("hold", reason="보유 유지")

    # 2) 현금이면 이동평균 위에 있는 종목 중 가장 강한 것을 산다
    invested = sum(p.qty * p.avg_price for p in positions.values())
    spendable = min(cash, budget_krw - int(invested), risk.max_order_krw)
    candidates: list[tuple[float, Asset, int, float]] = []
    skipped: list[str] = []
    for asset in universe:
        price = prices.get(asset.code)
        ma = moving_average(history.get(asset.code, []), ma_days)
        if price is None or ma is None:
            skipped.append(f"{asset.name}: 데이터 부족")
            continue
        if price <= ma:
            continue
        if price * (1 + costs.fee_rate) > spendable:
            skipped.append(f"{asset.name}: 1주 {price}원 > 쓸 수 있는 돈 {spendable}원")
            continue
        candidates.append((price / ma, asset, price, ma))

    if not candidates:
        detail = "; ".join(skipped) if skipped else "이동평균 위에 있는 종목 없음"
        return Decision("hold", reason=f"매수 후보 없음 ({detail})")

    strength, asset, price, ma = max(candidates, key=lambda c: c[0])
    qty = math.floor(spendable / (price * (1 + costs.fee_rate)))
    return Decision("buy", asset.code, qty, price, f"{asset.name} {price}원 > {ma_days}일선 {ma:.0f}원 (+{strength - 1:.1%})")
