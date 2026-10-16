"""사용법:
    python -m autotrader run        # 하루치 판단 1회 (cron 으로 장중 하루 1번)
    python -m autotrader status     # paper 계좌 잔고·수익률
    python -m autotrader check      # 키움 API 연결/응답 확인 (주문 없음)
    python -m autotrader screen     # 1주 5,000원 이하 매수 후보 찾기 (주문 없음)
    python -m autotrader stop       # 킬스위치 ON: 모든 주문 중단
    python -m autotrader resume     # 킬스위치 OFF
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from autotrader.brokers.base import BrokerError
from autotrader.brokers.kiwoom import KiwoomClient
from autotrader.brokers.paper import PaperBroker
from autotrader.config import ConfigError, load_config
from autotrader.engine import build_runtime, run_cycle
from autotrader.risk import kill_switch_path
from autotrader.screener import screen, to_universe_toml

KST = ZoneInfo("Asia/Seoul")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autotrader")
    parser.add_argument("command", choices=["run", "status", "check", "screen", "stop", "resume"])
    parser.add_argument("--config", default="config.toml")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    config_path = Path(args.config).resolve()
    try:
        cfg = load_config(config_path)
        if args.command == "stop":
            kill_switch_path(cfg.state_dir).parent.mkdir(parents=True, exist_ok=True)
            kill_switch_path(cfg.state_dir).touch()
            print("킬스위치 ON — 이후 주문이 나가지 않습니다.")
        elif args.command == "resume":
            kill_switch_path(cfg.state_dir).unlink(missing_ok=True)
            print("킬스위치 OFF")
        elif args.command == "status":
            print_status(cfg)
        elif args.command == "check":
            run_check(cfg)
        elif args.command == "screen":
            run_screen(cfg)
        else:
            rt = build_runtime(cfg, config_path.parent)
            run_cycle(cfg, rt, datetime.now(KST))
    except (ConfigError, BrokerError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1
    return 0


def print_status(cfg) -> None:
    kinds = {a.code: a.kind for a in cfg.strategy.universe}
    for acct in cfg.accounts:
        b = PaperBroker(acct.name, cfg.state_dir, acct.budget_krw, cfg.costs, kinds)
        positions = b.positions()
        cost = sum(p.qty * p.avg_price for p in positions.values())
        print(f"[{acct.name}] 시작 {acct.budget_krw:,}원 | 현금 {b.cash():,}원 | 보유 원가 {cost:,.0f}원")
        for p in positions.values():
            print(f"    {p.code} {p.qty}주 @ {p.avg_price:,.0f}원")


def kiwoom_client(cfg) -> KiwoomClient:
    server = next((a.server for a in cfg.accounts if a.broker == "kiwoom"), "mock")
    return KiwoomClient(cfg.secret("KIWOOM_APPKEY"), cfg.secret("KIWOOM_SECRETKEY"), server=server)


def run_screen(cfg) -> None:
    sc = cfg.screen
    print(f"검색 조건: {sc.max_price_krw:,}원 이하, 시장 {', '.join(sc.markets)}, 제외어 {', '.join(sc.exclude_keywords) or '-'}")
    result = screen(kiwoom_client(cfg), sc)
    print(f"\n전체 {result.scanned:,}개 중 후보 {len(result.candidates)}개")
    for reason, n in sorted(result.rejected.items(), key=lambda x: -x[1]):
        print(f"  탈락 {n:>5,}개  {reason}")
    if not result.candidates:
        print("\n조건을 통과한 종목이 없습니다. [screen] 기준을 완화하거나 예산을 한 계좌로 모으는 것을 고려하세요.")
        return
    print(f"\n{'코드':<8}{'구분':<6}{'현재가':>8}  {'거래대금':>14}  이름")
    for c in result.candidates:
        print(f"{c.code:<8}{c.kind:<6}{c.price:>8,}  {c.trade_value:>14,}  {c.name}")
    snippet = to_universe_toml(result.candidates)
    out = cfg.state_dir / "screen_universe.toml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(snippet + "\n", encoding="utf-8")
    print(f"\nconfig.toml 의 [strategy] universe 에 붙여넣을 내용 → {out}\n{snippet}")


def run_check(cfg) -> None:
    client = kiwoom_client(cfg)
    print(f"키움 서버: {client.base}")
    for asset in cfg.strategy.universe:
        print(f"  {asset.code} {asset.name}: {client.price(asset.code):,}원")
    print(f"  주문가능금액: {client.call('/api/dostk/acnt', 'kt00001', {'qry_tp': '3'}).get('ord_alow_amt')}")


if __name__ == "__main__":
    sys.exit(main())
