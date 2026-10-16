"""주문 직전의 마지막 안전장치. 전략이 무엇을 원하든 여기서 막히면 주문은 나가지 않는다."""

from __future__ import annotations

from datetime import datetime, time
from pathlib import Path

from autotrader.config import RiskConfig
from autotrader.store import DailyCounter
from autotrader.strategy import Decision


def kill_switch_path(state_dir: Path) -> Path:
    return state_dir / "STOP"


def check(
    decision: Decision,
    *,
    account: str,
    now: datetime,
    risk: RiskConfig,
    counter: DailyCounter,
    state_dir: Path,
    allowed_codes: set[str],
) -> str | None:
    """막아야 하면 사유 문자열, 통과면 None."""
    if kill_switch_path(state_dir).exists():
        return "킬스위치(state/STOP) 활성화됨"
    if now.weekday() >= 5:
        return "주말"
    start, end = time.fromisoformat(risk.trading_start), time.fromisoformat(risk.trading_end)
    if not (start <= now.time() <= end):
        return f"거래 시간 외 ({risk.trading_start}~{risk.trading_end})"
    if decision.qty <= 0:
        return "수량 0"
    if decision.code not in allowed_codes:
        return f"{decision.code} 는 허용 목록에 없음"
    if decision.action == "buy" and decision.qty * decision.price > risk.max_order_krw:
        return f"1회 주문 한도 초과 ({decision.qty * decision.price}원 > {risk.max_order_krw}원)"
    if counter.get(now.strftime("%Y-%m-%d"), f"orders:{account}") >= risk.max_orders_per_day:
        return f"하루 주문 한도({risk.max_orders_per_day}회) 도달"
    return None
