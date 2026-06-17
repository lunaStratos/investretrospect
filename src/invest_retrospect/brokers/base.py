"""증권사(broker) 클라이언트 공통 인터페이스.

각 증권사 클라이언트는 응답을 키움 형식 dict로 정규화해서 반환한다.
이렇게 하면 analyzer.py 가 broker 와 무관하게 한 가지 스키마만 다루면 된다.

정규화된 응답 키 (analyzer.py 가 기대하는 형식):
  - trades:              {"acnt_ord_cntr_prps_dtl": [ ... 체결 row ... ]}
      row 필드: ord_no, stk_cd, stk_nm, trde_tp("1"매도|"2"매수),
                ord_qty, ord_uv, cntr_qty, cntr_uv, cntr_tm, dmst_stex_tp
  - realized_pl_per_stock: {"dt_stk_rlzt_pl": [ ... 종목별 row ... ]}
      row 필드: stk_cd, stk_nm, buy_amt, sell_amt, rlzt_pl, prft_rt
  - balance:             {"acnt_evlt_remn_indv_tot": [ ... 보유종목 row ... ]}
      row 필드: stk_cd, stk_nm, rmnd_qty, pur_pric, cur_prc,
                evlt_amt, evltv_prft, prft_rt
  - deposit:             {"entr": "<예수금 정수>"}
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class BrokerError(RuntimeError):
    """모든 증권사 API 에러의 공통 베이스."""


@dataclass(frozen=True)
class BrokerInfo:
    id: str                             # 'kiwoom' | 'kis' | 'ls'
    label: str                          # 사용자 표시명
    hosts: dict[str, str]               # {'prod': '...', 'mock': '...'}
    required_keys: tuple[str, ...]      # 인증에 필요한 Settings 필드명
    supports_mock: bool = True


class BrokerClient(ABC):
    """증권사 REST API 추상 클라이언트.

    모든 메서드는 키움 형식 dict 로 정규화된 응답을 반환한다.
    데이터가 제공되지 않는 broker 는 None 또는 빈 dict 를 반환할 수 있다.
    """

    info: BrokerInfo

    def __enter__(self) -> "BrokerClient":
        self.authenticate()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @abstractmethod
    def authenticate(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def trades(self, account_no: str, ymd: str) -> dict[str, Any]: ...

    @abstractmethod
    def realized_pl_per_stock(self, account_no: str, ymd: str) -> dict[str, Any]: ...

    @abstractmethod
    def balance(self, account_no: str) -> dict[str, Any]: ...

    @abstractmethod
    def deposit(self, account_no: str) -> dict[str, Any]: ...

    def daily_journal(self, account_no: str, ymd: str) -> dict[str, Any] | None:
        """키움 전용 — 다른 broker 는 미지원."""
        return None

    def current_price(self, stk_cd: str) -> int:
        """국내 종목 현재가 조회 (수동 원장 평가용). 미지원 broker 는 예외."""
        raise BrokerError(f"{type(self).__name__} 는 현재가 조회를 지원하지 않습니다.")
