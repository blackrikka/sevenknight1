"""키움증권 REST API 클라이언트 (openapi.kiwoom.com).

api-id 와 필드명은 키움 REST API 문서/공식 예제 기준으로 작성했지만, 이 저장소를 만든 환경에서는
공식 문서에 직접 접속해 재확인하지 못했다. 실계좌(server="real")로 넘어가기 전에 반드시
모의투자(server="mock")에서 `python -m autotrader check` 로 응답 필드를 확인할 것.
"""

from __future__ import annotations

from typing import Any

from autotrader.brokers.base import BrokerError, OrderResult, Position
from autotrader.brokers.http import RESPONSE_HEADERS, Transport, post_json

HOSTS = {"real": "https://api.kiwoom.com", "mock": "https://mockapi.kiwoom.com"}

# 매매구분(trde_tp): "0" 보통(지정가), "3" 시장가
MARKET = "3"


def _num(value: Any) -> int:
    """키움 숫자 문자열("+00070000", "-1234", "")을 정수로. 부호는 등락 표시라 버린다."""
    s = str(value or "0").strip().lstrip("+-").replace(",", "")
    return int(float(s)) if s else 0


class KiwoomClient:
    def __init__(self, appkey: str, secretkey: str, server: str = "mock", transport: Transport = post_json):
        self.base = HOSTS[server]
        self.appkey = appkey
        self.secretkey = secretkey
        self.transport = transport
        self._token: str | None = None

    def _auth(self) -> str:
        if self._token is None:
            data = self.transport(
                f"{self.base}/oauth2/token",
                {"Content-Type": "application/json;charset=UTF-8"},
                {"grant_type": "client_credentials", "appkey": self.appkey, "secretkey": self.secretkey},
            )
            if not data.get("token"):
                raise BrokerError(f"키움 토큰 발급 실패: {data.get('return_msg') or data}")
            self._token = data["token"]
        return self._token

    def call(self, path: str, api_id: str, body: dict[str, Any], extra_headers: dict[str, str] | None = None) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self._auth()}",
            "api-id": api_id,
            **(extra_headers or {}),
        }
        data = self.transport(f"{self.base}{path}", headers, body)
        if str(data.get("return_code", "0")) != "0":
            raise BrokerError(f"키움 {api_id} 오류: {data.get('return_msg') or data}")
        return data

    def call_pages(self, path: str, api_id: str, body: dict[str, Any], list_key: str, max_pages: int = 30) -> list[dict]:
        """연속조회: 응답 헤더 cont-yn=Y 이면 next-key 로 다음 페이지를 이어 받는다."""
        rows: list[dict] = []
        extra: dict[str, str] = {}
        for _ in range(max_pages):
            data = self.call(path, api_id, body, extra)
            rows += data.get(list_key) or []
            resp_headers = data.get(RESPONSE_HEADERS) or {}
            if resp_headers.get("cont-yn") != "Y" or not resp_headers.get("next-key"):
                break
            extra = {"cont-yn": "Y", "next-key": resp_headers["next-key"]}
        return rows

    # --- 시세 ---
    def quote(self, code: str) -> dict[str, int]:
        data = self.call("/api/dostk/stkinfo", "ka10001", {"stk_cd": code})  # 주식기본정보요청
        price = _num(data.get("cur_prc"))
        if price <= 0:
            raise BrokerError(f"{code} 현재가를 읽지 못했습니다: {data}")
        return {"price": price, "volume": _num(data.get("trde_qty"))}

    def price(self, code: str) -> int:
        return self.quote(code)["price"]

    def listed(self, market: str) -> list[dict]:
        """종목정보 리스트(ka10099). market: "0" 코스피, "10" 코스닥, "8" ETF."""
        return self.call_pages("/api/dostk/stkinfo", "ka10099", {"mrkt_tp": market}, "list")

    def daily_closes(self, code: str, today: str) -> dict[str, int]:
        """일봉 종가 {YYYYMMDD: 가격}. 이동평균 계산을 처음부터 시작할 수 있게 해준다."""
        data = self.call(
            "/api/dostk/chart",
            "ka10081",  # 주식일봉차트조회요청
            {"stk_cd": code, "base_dt": today, "upd_stkpc_tp": "1"},
        )
        rows = data.get("stk_dt_pole_chart_qry") or []
        return {r["dt"]: _num(r["cur_prc"]) for r in rows if r.get("dt") and _num(r.get("cur_prc")) > 0}


class KiwoomBroker:
    def __init__(self, name: str, client: KiwoomClient):
        self.name = name
        self.client = client

    def cash(self) -> int:
        data = self.client.call("/api/dostk/acnt", "kt00001", {"qry_tp": "3"})  # 예수금상세현황요청
        return _num(data.get("ord_alow_amt"))

    def positions(self) -> dict[str, Position]:
        data = self.client.call(
            "/api/dostk/acnt", "kt00018", {"qry_tp": "1", "dmst_stex_tp": "KRX"}  # 계좌평가잔고내역요청
        )
        result: dict[str, Position] = {}
        for row in data.get("acnt_evlt_remn_indv_tot") or []:
            code = str(row.get("stk_cd", "")).lstrip("A")
            qty = _num(row.get("rmnd_qty"))
            if code and qty > 0:
                result[code] = Position(code=code, qty=qty, avg_price=float(_num(row.get("pur_pric"))))
        return result

    def market_order(self, side: str, code: str, qty: int, ref_price: int) -> OrderResult:
        api_id = {"buy": "kt10000", "sell": "kt10001"}[side]
        data = self.client.call(
            "/api/dostk/ordr",
            api_id,
            {"dmst_stex_tp": "KRX", "stk_cd": code, "ord_qty": str(qty), "ord_uv": "", "trde_tp": MARKET, "cond_uv": ""},
        )
        return OrderResult(code=code, side=side, qty=qty, ref_price=ref_price, order_id=str(data.get("ord_no", "")))

