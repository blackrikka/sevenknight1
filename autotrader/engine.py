"""한 번 실행 = 하루치 판단 한 번. cron/작업 스케줄러로 장중에 하루 1회 돌리는 것을 전제로 한다."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from autotrader import risk
from autotrader.brokers.base import Broker, BrokerError, QuoteSource
from autotrader.brokers.kiwoom import KiwoomBroker, KiwoomClient
from autotrader.brokers.paper import PaperBroker
from autotrader.brokers.toss import TossBroker
from autotrader.config import LIVE_CONFIRM_ENV, LIVE_CONFIRM_VALUE, Config, ConfigError
from autotrader.jev import JevGate, make_client
from autotrader.store import DailyCounter, PriceHistory
from autotrader.strategy import decide

log = logging.getLogger(__name__)


@dataclass
class Runtime:
    quotes: QuoteSource
    brokers: dict[str, Broker]
    jev: JevGate
    bootstrap: Callable[[str, str], dict[str, int]] | None = None  # (code, YYYYMMDD) -> 일봉 종가


def build_runtime(cfg: Config, base_dir: Path) -> Runtime:
    if cfg.mode == "live" and not cfg.live_confirmed:
        raise ConfigError(
            f"mode=live 는 환경변수 {LIVE_CONFIRM_ENV}={LIVE_CONFIRM_VALUE} 가 있어야 실행됩니다. "
            "실제 돈이 나갑니다."
        )
    kiwoom_accounts = [a for a in cfg.accounts if a.broker == "kiwoom" and a.enabled]
    server = kiwoom_accounts[0].server if kiwoom_accounts else "mock"
    # 시세는 두 계좌 모두 키움 API 에서 가져온다 (토스 시세 API 는 아직 미구현)
    kiwoom = KiwoomClient(cfg.secret("KIWOOM_APPKEY"), cfg.secret("KIWOOM_SECRETKEY"), server=server)

    kinds = {a.code: a.kind for a in cfg.strategy.universe}
    brokers: dict[str, Broker] = {}
    for acct in cfg.accounts:
        if not acct.enabled:
            continue
        if cfg.mode == "paper":
            brokers[acct.name] = PaperBroker(acct.name, cfg.state_dir, acct.budget_krw, cfg.costs, kinds)
        elif acct.broker == "kiwoom":
            brokers[acct.name] = KiwoomBroker(acct.name, kiwoom)
        else:
            brokers[acct.name] = TossBroker(acct.name, cfg.secret("TOSS_CLIENT_ID"), cfg.secret("TOSS_CLIENT_SECRET"))

    jev_client = make_client(cfg.secret("TYPESAFE_API_KEY")) if cfg.jev.enabled and cfg.has_secret("TYPESAFE_API_KEY") else None
    if cfg.jev.enabled and jev_client is None:
        log.warning("TYPESAFE_API_KEY 가 없어 Jev 없이 실행합니다")
    jev = JevGate(cfg.jev, jev_client, cfg.state_dir, base_dir)
    return Runtime(quotes=kiwoom, brokers=brokers, jev=jev, bootstrap=kiwoom.daily_closes)


def run_cycle(cfg: Config, rt: Runtime, now: datetime) -> list[dict]:
    date = now.strftime("%Y-%m-%d")
    history = PriceHistory(cfg.state_dir)
    counter = DailyCounter(cfg.state_dir)
    events: list[dict] = []

    def emit(**event) -> None:
        event = {"time": now.isoformat(timespec="seconds"), "mode": cfg.mode, **event}
        events.append(event)
        log.info(json.dumps(event, ensure_ascii=False))

    # 1) 시세 수집 + 이동평균용 기록
    prices: dict[str, int] = {}
    for asset in cfg.strategy.universe:
        try:
            prices[asset.code] = rt.quotes.price(asset.code)
        except BrokerError as e:
            emit(event="quote_error", code=asset.code, error=str(e))
            continue
        if rt.bootstrap and len(history.closes(asset.code)) < cfg.strategy.ma_days:
            try:
                daily = rt.bootstrap(asset.code, now.strftime("%Y%m%d"))
                history.merge(asset.code, {f"{d[:4]}-{d[4:6]}-{d[6:]}": p for d, p in daily.items() if d < now.strftime("%Y%m%d")})
            except BrokerError as e:
                emit(event="bootstrap_error", code=asset.code, error=str(e))
        history.record(asset.code, date, prices[asset.code])
    # 이동평균은 "어제까지의 종가"로 계산해야 오늘 가격과 비교가 의미 있다
    past = {code: history.closes(code)[:-1] for code in prices}
    by_code = {a.code: a for a in cfg.strategy.universe}

    # 2) 계좌별 판단 → Jev 확인 → 리스크 확인 → 주문
    for acct in cfg.accounts:
        broker = rt.brokers.get(acct.name)
        if broker is None:
            continue
        try:
            cash, positions = broker.cash(), broker.positions()
            for code in positions:
                if code not in prices:
                    prices[code] = rt.quotes.price(code)
            decision = decide(
                universe=cfg.strategy.universe,
                prices=prices,
                history=past,
                positions=positions,
                cash=cash,
                budget_krw=acct.budget_krw,
                ma_days=cfg.strategy.ma_days,
                risk=cfg.risk,
                costs=cfg.costs,
            )
            emit(event="decision", account=acct.name, cash=cash, action=decision.action, code=decision.code,
                 qty=decision.qty, price=decision.price, reason=decision.reason)
            if decision.action == "hold":
                continue

            if decision.action == "buy":
                verdict = rt.jev.check_buy(by_code[decision.code], date)
                emit(event="jev", account=acct.name, code=decision.code, veto=verdict.veto, reason=verdict.reason)
                if verdict.veto:
                    continue

            blocked = risk.check(
                decision, account=acct.name, now=now, risk=cfg.risk, counter=counter,
                state_dir=cfg.state_dir, allowed_codes=set(by_code) | set(positions),
            )
            if blocked:
                emit(event="blocked", account=acct.name, reason=blocked)
                continue

            result = broker.market_order(decision.action, decision.code, decision.qty, decision.price)
            counter.incr(date, f"orders:{acct.name}")
            emit(event="order", account=acct.name, side=result.side, code=result.code, qty=result.qty,
                 ref_price=result.ref_price, order_id=result.order_id, fee=result.fee, tax=result.tax)
        except (BrokerError, NotImplementedError) as e:
            emit(event="account_error", account=acct.name, error=str(e))

    log_path = cfg.state_dir / "events.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    return events
