"""증권사별 REST API 클라이언트.

지원: 키움증권 / 한국투자증권(한투, KIS) / LS증권 /
      메리츠증권(스캐폴드) / 대신증권(스캐폴드).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from invest_retrospect.brokers.base import BrokerClient, BrokerError, BrokerInfo


class Broker(str, Enum):
    KIWOOM = "kiwoom"
    KIS = "kis"
    LS = "ls"
    MERITZ = "meritz"
    DAISHIN = "daishin"
    MANUAL = "manual"          # 증권사 연동 없이 수동 원장으로 일지 생성

    @property
    def display_name(self) -> str:
        return _DISPLAY[self]

    @property
    def is_manual(self) -> bool:
        return self is Broker.MANUAL


_DISPLAY = {
    Broker.KIWOOM: "키움증권",
    Broker.KIS: "한국투자증권",
    Broker.LS: "LS증권",
    Broker.MERITZ: "메리츠증권",
    Broker.DAISHIN: "대신증권",
    Broker.MANUAL: "수동 입력",
}


def parse_broker(value: str) -> Broker:
    v = (value or "").strip().lower()
    for b in Broker:
        if v == b.value:
            return b
    raise BrokerError(f"알 수 없는 broker: {value!r} (허용: {[b.value for b in Broker]})")


def make_client(
    broker: Broker | str,
    *,
    host: str,
    app_key: str,
    secret_key: str,
    is_mock: bool = False,
    **extra: Any,
) -> BrokerClient:
    """broker 식별자에 맞는 클라이언트 인스턴스 생성.

    extra: broker 별 추가 인자.
      - LS: account_pwd (str) — 일부 조회에서 요구할 수 있음.
    """
    b = parse_broker(broker) if isinstance(broker, str) else broker
    if b is Broker.KIWOOM:
        from invest_retrospect.brokers.kiwoom import KiwoomClient
        return KiwoomClient(host, app_key, secret_key)
    if b is Broker.KIS:
        from invest_retrospect.brokers.kis import KISClient
        return KISClient(host, app_key, secret_key, is_mock=is_mock)
    if b is Broker.LS:
        from invest_retrospect.brokers.ls import LSClient
        return LSClient(
            host, app_key, secret_key,
            is_mock=is_mock,
            account_pwd=extra.get("account_pwd", ""),
        )
    if b is Broker.MERITZ:
        from invest_retrospect.brokers.meritz import MeritzClient
        return MeritzClient(
            host, app_key, secret_key,
            is_mock=is_mock,
            account_pwd=extra.get("account_pwd", ""),
        )
    if b is Broker.DAISHIN:
        from invest_retrospect.brokers.daishin import DaishinClient
        return DaishinClient(
            host, app_key, secret_key,
            is_mock=is_mock,
            account_pwd=extra.get("account_pwd", ""),
        )
    raise BrokerError(f"미구현 broker: {b}")


def broker_info(broker: Broker) -> BrokerInfo:
    if broker is Broker.KIWOOM:
        from invest_retrospect.brokers.kiwoom import INFO
        return INFO
    if broker is Broker.KIS:
        from invest_retrospect.brokers.kis import INFO
        return INFO
    if broker is Broker.LS:
        from invest_retrospect.brokers.ls import INFO
        return INFO
    if broker is Broker.MERITZ:
        from invest_retrospect.brokers.meritz import INFO
        return INFO
    if broker is Broker.DAISHIN:
        from invest_retrospect.brokers.daishin import INFO
        return INFO
    raise BrokerError(f"미구현 broker: {broker}")


__all__ = [
    "Broker",
    "BrokerClient",
    "BrokerError",
    "BrokerInfo",
    "broker_info",
    "make_client",
    "parse_broker",
]
