"""메리츠증권 OpenAPI 클라이언트 (스캐폴드).

문서: https://open.imeritz.com  (2026-05 기준 베타)

상태: 인증/HTTP 골격까지만 구현돼 있고, 실제 TR 호출 부분은
      엔드포인트 스펙이 확정되는 대로 채워 넣을 것. 호출 시
      ``MeritzNotImplementedError`` 가 발생하므로 GUI/CLI 에서
      메리츠를 선택해도 다른 broker 동작에는 영향이 없다.

채워야 할 항목 (TODO):
  1. PROD_HOST / MOCK_HOST 정확한 도메인·포트 확정
  2. /oauth2/token 의 정확한 grant_type, body 형식, 응답 필드명
  3. 각 TR 의 path, method, request body 스펙
  4. 응답을 키움 형식 dict 로 정규화 (다른 broker 모듈 참고)

응답 정규화 목표 (analyzer.py 가 기대하는 형식):
  - trades:                {"acnt_ord_cntr_prps_dtl": [...]}
  - realized_pl_per_stock: {"dt_stk_rlzt_pl": [...]}
  - balance:               {"acnt_evlt_remn_indv_tot": [...]}
  - deposit:               {"entr": "<정수>"}
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from invest_retrospect.brokers.base import BrokerClient, BrokerError, BrokerInfo

# TODO: 실제 호스트/포트로 교체
PROD_HOST = "https://openapi.imeritz.com"
MOCK_HOST = "https://openapi-mock.imeritz.com"

INFO = BrokerInfo(
    id="meritz",
    label="메리츠증권",
    hosts={"prod": PROD_HOST, "mock": MOCK_HOST},
    required_keys=("meritz_app_key", "meritz_app_secret"),
)


class MeritzError(BrokerError):
    pass


class MeritzNotImplementedError(MeritzError):
    """엔드포인트 스펙이 확정되지 않아 아직 호출할 수 없는 TR."""


@dataclass
class _Token:
    value: str
    expires_at: float  # epoch seconds


def _to_int(v: Any) -> int:
    if v is None or v == "":
        return 0
    s = str(v).replace(",", "").replace("+", "").strip()
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return 0


def _to_float(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    s = str(v).replace(",", "").replace("+", "").replace("%", "").strip()
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def _normalize_account(account_no: str) -> str:
    s = (account_no or "").replace("-", "").replace(" ", "").strip()
    if not s:
        raise MeritzError("메리츠 계좌번호가 비어 있습니다.")
    if not s.isdigit():
        raise MeritzError(f"메리츠 계좌번호 형식 오류: '{account_no}' (숫자만)")
    return s


class MeritzClient(BrokerClient):
    info = INFO

    def __init__(
        self,
        host: str,
        app_key: str,
        app_secret: str,
        *,
        is_mock: bool,
        account_pwd: str = "",
    ) -> None:
        self._host = host.rstrip("/")
        self._app_key = app_key
        self._app_secret = app_secret
        self._is_mock = is_mock
        self._account_pwd = account_pwd
        self._token: _Token | None = None
        self._http = httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0))

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------ 인증

    def authenticate(self) -> None:
        """OAuth2 client credentials flow (예상).

        TODO: 메리츠 OpenAPI 가이드와 비교해서
          - grant_type 값
          - body 가 form-urlencoded 인지 JSON 인지
          - 응답 토큰/만료 필드명
        을 확정.
        """
        url = f"{self._host}/oauth2/token"
        body = {
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
        }
        try:
            r = self._http.post(
                url,
                json=body,
                headers={"content-type": "application/json; charset=utf-8"},
            )
        except httpx.HTTPError as e:
            raise MeritzError(f"메리츠 인증 요청 실패: {e}") from e

        if r.status_code >= 400:
            raise MeritzError(f"메리츠 인증 실패 HTTP {r.status_code}: {r.text[:300]}")
        try:
            data = r.json()
        except ValueError as e:
            raise MeritzError(f"메리츠 인증 응답 JSON 파싱 실패: {r.text[:200]}") from e

        token = data.get("access_token") or data.get("token")
        if not token:
            raise MeritzError(f"메리츠 인증 응답에 access_token 없음: {data}")
        expires_in = int(data.get("expires_in") or 3600)
        self._token = _Token(value=token, expires_at=time.time() + expires_in - 60)

    def _ensure_token(self) -> str:
        if self._token is None or time.time() >= self._token.expires_at:
            self.authenticate()
        assert self._token is not None
        return self._token.value

    def _headers(self, tr_id: str) -> dict[str, str]:
        # TODO: tr_id, appkey, appsecret 등을 헤더로 보내는지 본문으로 보내는지 확정.
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._ensure_token()}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
        }

    def _post(
        self,
        path: str,
        tr_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        url = f"{self._host}{path}"
        r = self._http.post(url, json=body, headers=self._headers(tr_id))
        if r.status_code >= 400:
            raise MeritzError(f"메리츠 {tr_id} HTTP {r.status_code}: {r.text[:500]}")
        try:
            data = r.json()
        except ValueError as e:
            raise MeritzError(f"메리츠 {tr_id} JSON 파싱 실패: {r.text[:200]}") from e

        # TODO: 표준 에러 필드명 확정 (rt_cd / rsp_cd / msg_cd 등)
        rt_cd = str(data.get("rt_cd") or data.get("rsp_cd") or "")
        if rt_cd and rt_cd not in ("0", "00000"):
            raise MeritzError(
                f"메리츠 {tr_id} 실패 [{rt_cd}]: "
                f"{data.get('msg1') or data.get('rsp_msg') or '<no message>'}"
            )
        return data

    # ------------------------------------------------------------ 매매일지 API

    def trades(self, account_no: str, ymd: str) -> dict[str, Any]:
        """일자별 체결 내역.

        TODO: 메리츠 TR 코드/엔드포인트가 확정되면 _post() 호출로 대체하고,
        rows 를 다음 키로 정규화:
          ord_no, stk_cd, stk_nm, trde_tp("1"매도|"2"매수),
          ord_qty, ord_uv, cntr_qty, cntr_uv, cntr_tm, dmst_stex_tp
        """
        _normalize_account(account_no)
        raise MeritzNotImplementedError(
            "메리츠 trades(): 엔드포인트 스펙 확정 후 구현 예정."
        )

    def realized_pl_per_stock(self, account_no: str, ymd: str) -> dict[str, Any]:
        """일자별 종목별 실현손익.

        TODO: 실전 전용 TR 일 가능성 높음. 모의에서는 trades() 폴백 집계 패턴을
        ls.py / kis.py 와 동일하게 사용.
        """
        _normalize_account(account_no)
        raise MeritzNotImplementedError(
            "메리츠 realized_pl_per_stock(): 엔드포인트 스펙 확정 후 구현 예정."
        )

    def balance(self, account_no: str) -> dict[str, Any]:
        """현재 보유 종목 + 평가."""
        _normalize_account(account_no)
        raise MeritzNotImplementedError(
            "메리츠 balance(): 엔드포인트 스펙 확정 후 구현 예정."
        )

    def deposit(self, account_no: str) -> dict[str, Any]:
        """예수금."""
        _normalize_account(account_no)
        raise MeritzNotImplementedError(
            "메리츠 deposit(): 엔드포인트 스펙 확정 후 구현 예정."
        )
