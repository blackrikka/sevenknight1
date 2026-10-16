"""설정(config.toml)과 비밀값(.env / 환경변수) 로딩."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

LIVE_CONFIRM_ENV = "AUTOTRADER_LIVE_CONFIRM"
LIVE_CONFIRM_VALUE = "yes-i-accept-the-risk"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Asset:
    code: str
    name: str
    kind: str  # "etf" | "stock"


@dataclass(frozen=True)
class StrategyConfig:
    ma_days: int
    universe: tuple[Asset, ...]


@dataclass(frozen=True)
class RiskConfig:
    max_order_krw: int
    max_orders_per_day: int
    stop_loss_pct: float
    trading_start: str
    trading_end: str


@dataclass(frozen=True)
class Costs:
    fee_rate: float
    stock_sell_tax_rate: float
    etf_sell_tax_rate: float

    def sell_tax_rate(self, kind: str) -> float:
        return self.etf_sell_tax_rate if kind == "etf" else self.stock_sell_tax_rate


@dataclass(frozen=True)
class JevConfig:
    enabled: bool
    model: str
    veto_threshold: float
    max_calls_per_day: int
    headlines_dir: str
    rss_url_template: str
    max_headlines: int


@dataclass(frozen=True)
class AccountConfig:
    name: str
    broker: str  # "kiwoom" | "toss"
    enabled: bool
    budget_krw: int
    server: str = "mock"  # kiwoom 전용: "mock" | "real"


@dataclass(frozen=True)
class Config:
    mode: str  # "paper" | "live"
    state_dir: Path
    strategy: StrategyConfig
    risk: RiskConfig
    costs: Costs
    jev: JevConfig
    accounts: tuple[AccountConfig, ...]
    env: dict[str, str] = field(default_factory=dict)

    def secret(self, key: str) -> str:
        value = self.env.get(key) or os.environ.get(key)
        if not value:
            raise ConfigError(f"환경변수 {key} 가 설정되지 않았습니다 (.env 참고)")
        return value

    def has_secret(self, key: str) -> bool:
        return bool(self.env.get(key) or os.environ.get(key))

    @property
    def live_confirmed(self) -> bool:
        confirm = self.env.get(LIVE_CONFIRM_ENV) or os.environ.get(LIVE_CONFIRM_ENV)
        return self.mode == "live" and confirm == LIVE_CONFIRM_VALUE


def read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def load_config(path: Path, dotenv: Path | None = None) -> Config:
    if not path.exists():
        raise ConfigError(f"{path} 가 없습니다. config.example.toml 을 복사해서 만드세요.")
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    env = read_dotenv(dotenv or path.parent / ".env")

    mode = raw.get("mode", "paper")
    if mode not in ("paper", "live"):
        raise ConfigError("mode 는 'paper' 또는 'live' 여야 합니다")

    s = raw["strategy"]
    universe = tuple(Asset(code=str(a["code"]), name=a["name"], kind=a.get("kind", "etf")) for a in s["universe"])
    if not universe:
        raise ConfigError("strategy.universe 가 비어 있습니다")

    r, c, j = raw["risk"], raw["costs"], raw.get("jev", {})
    accounts = tuple(
        AccountConfig(
            name=name,
            broker=a["broker"],
            enabled=a.get("enabled", True),
            budget_krw=int(a["budget_krw"]),
            server=a.get("server", "mock"),
        )
        for name, a in raw.get("accounts", {}).items()
    )
    for a in accounts:
        if a.broker not in ("kiwoom", "toss"):
            raise ConfigError(f"accounts.{a.name}.broker 는 'kiwoom' 또는 'toss' 여야 합니다")
        if a.server not in ("mock", "real"):
            raise ConfigError(f"accounts.{a.name}.server 는 'mock' 또는 'real' 이어야 합니다")

    return Config(
        mode=mode,
        state_dir=(path.parent / raw.get("state_dir", "state")).resolve(),
        strategy=StrategyConfig(ma_days=int(s.get("ma_days", 20)), universe=universe),
        risk=RiskConfig(
            max_order_krw=int(r["max_order_krw"]),
            max_orders_per_day=int(r["max_orders_per_day"]),
            stop_loss_pct=float(r["stop_loss_pct"]),
            trading_start=r.get("trading_start", "09:10"),
            trading_end=r.get("trading_end", "15:10"),
        ),
        costs=Costs(
            fee_rate=float(c["fee_rate"]),
            stock_sell_tax_rate=float(c["stock_sell_tax_rate"]),
            etf_sell_tax_rate=float(c.get("etf_sell_tax_rate", 0.0)),
        ),
        jev=JevConfig(
            enabled=bool(j.get("enabled", False)),
            model=j.get("model", "jev-latest"),
            veto_threshold=float(j.get("veto_threshold", 0.6)),
            max_calls_per_day=int(j.get("max_calls_per_day", 3)),
            headlines_dir=j.get("headlines_dir", "news"),
            rss_url_template=j.get("rss_url_template", ""),
            max_headlines=int(j.get("max_headlines", 15)),
        ),
        accounts=accounts,
        env=env,
    )
