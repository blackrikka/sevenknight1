import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from autotrader import risk
from autotrader.brokers.base import BrokerError, Position
from autotrader.brokers.kiwoom import KiwoomBroker, KiwoomClient, _num
from autotrader.brokers.paper import PaperBroker, StaticQuotes
from autotrader.brokers.http import RESPONSE_HEADERS
from autotrader.config import Asset, Costs, JevConfig, RiskConfig, ScreenConfig, load_config
from autotrader.engine import Runtime, run_cycle
from autotrader.jev import JevGate
from autotrader.screener import screen, to_universe_toml
from autotrader.store import DailyCounter, PriceHistory
from autotrader.strategy import Decision, decide

ROOT = Path(__file__).resolve().parent.parent
KST = ZoneInfo("Asia/Seoul")
WEEKDAY_OPEN = datetime(2026, 10, 15, 10, 0, tzinfo=KST)  # 목요일 10:00
COSTS = Costs(fee_rate=0.00015, stock_sell_tax_rate=0.002, etf_sell_tax_rate=0.0)
RISK = RiskConfig(max_order_krw=5000, max_orders_per_day=1, stop_loss_pct=0.07, trading_start="09:10", trading_end="15:10")
CHEAP = Asset("111111", "싼ETF", "etf")
PRICEY = Asset("222222", "비싼ETF", "etf")


def jev_cfg(**kw):
    base = dict(enabled=True, model="jev-latest", veto_threshold=0.6, max_calls_per_day=2,
                headlines_dir="news", rss_url_template="", max_headlines=15)
    return JevConfig(**{**base, **kw})


class FakeJev:
    def __init__(self, noul=0.1, score=2.0):
        self.calls = []
        self.noul, self.score = noul, score

    def system_one(self, state, questions, *, model=None):
        self.calls.append((state, questions, model))
        return SimpleNamespace(answers={
            "serious_negative": SimpleNamespace(noul=self.noul),
            "sentiment": SimpleNamespace(score=self.score),
        })


class StrategyTest(unittest.TestCase):
    def run_decide(self, prices, history, positions=None, cash=5000):
        return decide(universe=(CHEAP, PRICEY), prices=prices, history=history, positions=positions or {},
                      cash=cash, budget_krw=5000, ma_days=3, risk=RISK, costs=COSTS)

    def test_buys_affordable_asset_above_ma_and_skips_expensive(self):
        d = self.run_decide({"111111": 1100, "222222": 9000}, {"111111": [1000] * 3, "222222": [8000] * 3})
        self.assertEqual((d.action, d.code, d.qty), ("buy", "111111", 4))

    def test_holds_when_not_enough_history(self):
        d = self.run_decide({"111111": 1100}, {"111111": [1000]})
        self.assertEqual(d.action, "hold")
        self.assertIn("데이터 부족", d.reason)

    def test_sells_on_trend_break(self):
        d = self.run_decide({"111111": 950}, {"111111": [1000] * 3}, {"111111": Position("111111", 4, 960)})
        self.assertEqual((d.action, d.qty), ("sell", 4))

    def test_stop_loss(self):
        d = self.run_decide({"111111": 920}, {"111111": [900] * 3}, {"111111": Position("111111", 4, 1000)})
        self.assertEqual(d.action, "sell")
        self.assertIn("손절", d.reason)


class PaperBrokerTest(unittest.TestCase):
    def test_round_trip_charges_fee_and_stock_tax(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = PaperBroker("t", Path(tmp), 10000, COSTS, {"A": "stock"})
            b.market_order("buy", "A", 2, 4000)
            self.assertEqual(b.cash(), 10000 - 8000 - 1)
            r = b.market_order("sell", "A", 2, 4000)
            self.assertEqual((r.fee, r.tax), (1, 16))
            self.assertEqual(b.cash(), 1999 + 8000 - 1 - 16)
            self.assertEqual(b.positions(), {})

    def test_rejects_overspend(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = PaperBroker("t", Path(tmp), 5000, COSTS, {})
            with self.assertRaises(BrokerError):
                b.market_order("buy", "A", 2, 3000)


class RiskTest(unittest.TestCase):
    def check(self, now=WEEKDAY_OPEN, decision=Decision("buy", "111111", 2, 1000), state_dir=None, counter=None):
        state_dir = state_dir or Path(tempfile.mkdtemp())
        return risk.check(decision, account="k", now=now, risk=RISK, counter=counter or DailyCounter(state_dir),
                          state_dir=state_dir, allowed_codes={"111111"})

    def test_passes_normal_order(self):
        self.assertIsNone(self.check())

    def test_blocks(self):
        self.assertIn("주말", self.check(now=datetime(2026, 10, 17, 10, 0, tzinfo=KST)))
        self.assertIn("거래 시간", self.check(now=datetime(2026, 10, 15, 15, 25, tzinfo=KST)))
        self.assertIn("한도 초과", self.check(decision=Decision("buy", "111111", 6, 1000)))
        self.assertIn("허용 목록", self.check(decision=Decision("buy", "999999", 1, 1000)))

    def test_kill_switch_and_daily_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            counter = DailyCounter(d)
            counter.incr("2026-10-15", "orders:k")
            self.assertIn("하루 주문 한도", self.check(state_dir=d, counter=counter))
            risk.kill_switch_path(d).touch()
            self.assertIn("킬스위치", self.check(state_dir=d, counter=counter))


class JevGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "news").mkdir()
        (self.tmp / "news" / "111111.txt").write_text("운용사 상장폐지 검토\n", encoding="utf-8")

    def test_veto_on_high_risk_and_caches(self):
        fake = FakeJev(noul=0.9)
        gate = JevGate(jev_cfg(), fake, self.tmp / "state", self.tmp)
        v = gate.check_buy(CHEAP, "2026-10-15")
        self.assertTrue(v.veto)
        self.assertTrue(gate.check_buy(CHEAP, "2026-10-15").veto)
        self.assertEqual(len(fake.calls), 1)
        state, questions, model = fake.calls[0]
        self.assertEqual(state["headlines"], ["운용사 상장폐지 검토"])
        self.assertEqual(questions["serious_negative"]["type"], "noul")
        self.assertEqual(model, "jev-latest")

    def test_no_headlines_or_budget_spent_skips_call(self):
        fake = FakeJev()
        gate = JevGate(jev_cfg(max_calls_per_day=0), fake, self.tmp / "state", self.tmp)
        self.assertFalse(gate.check_buy(CHEAP, "2026-10-15").veto)
        self.assertFalse(gate.check_buy(PRICEY, "2026-10-15").veto)
        self.assertEqual(fake.calls, [])


class KiwoomTest(unittest.TestCase):
    def test_num(self):
        self.assertEqual((_num("+00070000"), _num("-1234"), _num("")), (70000, 1234, 0))

    def test_order_request_shape(self):
        sent = []

        def transport(url, headers, body):
            sent.append((url, headers, body))
            if url.endswith("/oauth2/token"):
                return {"token": "T", "return_code": 0}
            return {"ord_no": "0001", "return_code": 0}

        broker = KiwoomBroker("k", KiwoomClient("ak", "sk", server="mock", transport=transport))
        r = broker.market_order("buy", "111111", 3, 1000)
        self.assertEqual(r.order_id, "0001")
        url, headers, body = sent[-1]
        self.assertEqual(url, "https://mockapi.kiwoom.com/api/dostk/ordr")
        self.assertEqual((headers["api-id"], headers["authorization"]), ("kt10000", "Bearer T"))
        self.assertEqual((body["stk_cd"], body["ord_qty"], body["trde_tp"]), ("111111", "3", "3"))

    def test_error_code_raises(self):
        client = KiwoomClient("ak", "sk", transport=lambda u, h, b: {"token": "T"} if "oauth2" in u else {"return_code": 1, "return_msg": "x"})
        with self.assertRaises(BrokerError):
            client.price("111111")


class KiwoomPagingTest(unittest.TestCase):
    def test_follows_next_key(self):
        sent = []

        def transport(url, headers, body):
            sent.append(headers)
            if "oauth2" in url:
                return {"token": "T"}
            if "next-key" not in headers:
                return {"list": [{"code": "1"}], RESPONSE_HEADERS: {"cont-yn": "Y", "next-key": "K2"}}
            return {"list": [{"code": "2"}], RESPONSE_HEADERS: {"cont-yn": "N"}}

        rows = KiwoomClient("a", "s", transport=transport).listed("8")
        self.assertEqual([r["code"] for r in rows], ["1", "2"])
        self.assertEqual((sent[-1]["cont-yn"], sent[-1]["next-key"], sent[-1]["api-id"]), ("Y", "K2", "ka10099"))


class FakeListing:
    def __init__(self, listings, quotes):
        self.listings, self.quotes = listings, quotes

    def listed(self, market):
        return self.listings.get(market, [])

    def quote(self, code):
        return self.quotes[code]


class ScreenerTest(unittest.TestCase):
    CFG = ScreenConfig(max_price_krw=5000, markets=("etf", "kospi"), exclude_keywords=("인버스", "레버리지"),
                       min_market_cap_krw=100_000_000_000, min_trade_value_krw=100_000_000, max_quote_checks=10)

    def test_filters_and_ranks(self):
        etf = [
            {"code": "E1", "name": "좋은ETF", "lastPrice": "4500", "orderWarning": "0"},
            {"code": "E2", "name": "KODEX 인버스", "lastPrice": "3000", "orderWarning": "0"},
            {"code": "E3", "name": "비싼ETF", "lastPrice": "12000", "orderWarning": "0"},
            {"code": "E4", "name": "한산ETF", "lastPrice": "2000", "orderWarning": "0"},
        ]
        stocks = [
            {"code": "S1", "name": "큰회사", "lastPrice": "3000", "listCount": "200000000", "orderWarning": "0", "auditInfo": "정상"},
            {"code": "S2", "name": "작은회사", "lastPrice": "3000", "listCount": "1000000", "orderWarning": "0"},
            {"code": "S3", "name": "관리회사", "lastPrice": "3000", "listCount": "900000000", "orderWarning": "0", "auditInfo": "관리종목"},
            {"code": "S4", "name": "경고회사", "lastPrice": "3000", "listCount": "900000000", "orderWarning": "3"},
        ]
        quotes = {"E1": {"price": 4500, "volume": 100_000}, "E4": {"price": 2000, "volume": 10},
                  "S1": {"price": 3100, "volume": 1_000_000}}
        result = screen(FakeListing({"8": etf, "0": stocks}, quotes), self.CFG, sleep=0)
        self.assertEqual([(c.code, c.kind) for c in result.candidates], [("S1", "stock"), ("E1", "etf")])
        self.assertEqual(result.scanned, 8)
        self.assertEqual(result.rejected["관리·경고·정지 종목"], 2)
        self.assertEqual(result.rejected["거래대금 부족"], 1)
        self.assertIn('{ code = "E1", name = "좋은ETF", kind = "etf" }', to_universe_toml(result.candidates))


class EngineTest(unittest.TestCase):
    def test_full_cycle_buys_in_paper_mode(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "config.toml").write_text((ROOT / "config.example.toml").read_text(encoding="utf-8").replace(
            '{ code = "069500", name = "KODEX 200", kind = "etf" }', '{ code = "111111", name = "싼ETF", kind = "etf" }'
        ).replace('rss_url_template = "https', 'rss_url_template = "" # "https'), encoding="utf-8")
        cfg = load_config(tmp / "config.toml")
        history = PriceHistory(cfg.state_dir)
        for i in range(25):
            history.record("111111", f"2026-09-{i + 1:02d}", 1000)

        brokers = {a.name: PaperBroker(a.name, cfg.state_dir, a.budget_krw, cfg.costs, {"111111": "etf"}) for a in cfg.accounts}
        rt = Runtime(quotes=StaticQuotes({"111111": 1100}), brokers=brokers,
                     jev=JevGate(cfg.jev, FakeJev(), cfg.state_dir, tmp))
        events = run_cycle(cfg, rt, WEEKDAY_OPEN)

        orders = [e for e in events if e["event"] == "order"]
        self.assertEqual(sorted(e["account"] for e in orders), ["kiwoom", "toss"])
        self.assertEqual(brokers["kiwoom"].positions()["111111"].qty, 4)
        self.assertTrue((cfg.state_dir / "events.jsonl").exists())

        # 같은 날 다시 돌려도 보유 중이라 추가 매수 없음
        again = run_cycle(cfg, rt, WEEKDAY_OPEN)
        self.assertFalse([e for e in again if e["event"] == "order"])


if __name__ == "__main__":
    unittest.main()
