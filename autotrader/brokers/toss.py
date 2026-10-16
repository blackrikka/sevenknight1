"""토스증권 Open API 자리.

토스증권은 2026년에 개인용 Open API(REST, OAuth2 client_credentials, 허용 IP 등록 필요)를
열었지만, 이 코드를 만든 환경에서는 공식 문서에 접속할 수 없어 엔드포인트와 응답 필드를 검증하지
못했다. 추측한 경로로 실제 돈이 걸린 주문을 보내지 않도록, 실거래 메서드는 일부러 비워 두었다.

paper 모드에서는 이 클래스를 쓰지 않는다 (PaperBroker + 키움 시세로 토스 계좌를 시뮬레이션).
토스 WTS > 설정 > Open API 에서 키를 발급받고, 공식 문서를 보며 아래 세 메서드를 채우면 live 로 쓸 수 있다.
"""

from __future__ import annotations

from autotrader.brokers.base import OrderResult, Position

_TODO = (
    "토스증권 Open API 공식 문서의 {what} 스펙으로 autotrader/brokers/toss.py 를 채워야 합니다. "
    "그 전까지 토스 계좌는 paper 모드로만 동작합니다."
)


class TossBroker:
    def __init__(self, name: str, client_id: str, client_secret: str):
        self.name = name
        self.client_id = client_id
        self.client_secret = client_secret

    def cash(self) -> int:
        raise NotImplementedError(_TODO.format(what="주문가능금액 조회"))

    def positions(self) -> dict[str, Position]:
        raise NotImplementedError(_TODO.format(what="잔고 조회"))

    def market_order(self, side: str, code: str, qty: int, ref_price: int) -> OrderResult:
        raise NotImplementedError(_TODO.format(what="국내주식 주문"))
