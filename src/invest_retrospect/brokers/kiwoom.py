"""키움 REST API 클라이언트.

엔드포인트는 모두 POST /api/dostk/<도메인>, body는 JSON.
요청 헤더에 api-id를 넣어서 어떤 TR을 호출하는지 구분한다.

매매일지에 쓰는 주요 api-id (계좌 도메인: /api/dostk/acnt):
  - kt00009 : 계좌별주문체결내역상세요청    (체결 리스트)
  - ka10170 : 당일매매일지요청              (키움이 만든 요약)
  - ka10074 : 일자별실현손익요청             (날짜별 총 실현손익)
  - ka10073 : 일자별종목별실현손익_일자      (종목별 실현손익)
  - kt00018 : 계좌평가잔고내역요청           (현재 보유)
  - kt00001 : 예수금상세현황요청             (예수금)

연속 조회: 응답 헤더의 cont-yn == 'Y' 면 next-key 를 다음 요청 헤더에 넣어
같은 api-id로 다시 호출. 본 모듈은 자동 페이지네이션을 처리한다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from invest_retrospect.brokers.base import BrokerClient, BrokerError, BrokerInfo

PROD_HOST = "https://api.kiwoom.com"
MOCK_HOST = "https://mockapi.kiwoom.com"

INFO = BrokerInfo(
    id="kiwoom",
    label="키움증권",
    hosts={"prod": PROD_HOST, "mock": MOCK_HOST},
    required_keys=("kiwoom_app_key", "kiwoom_secret_key"),
)


class KiwoomError(BrokerError):
    pass


@dataclass
class _Token:
    value: str
    expires_dt: str  # 키움이 주는 'YYYYMMDDHHMMSS' 형식


class KiwoomClient(BrokerClient):
    info = INFO

    def __init__(self, host: str, app_key: str, secret_key: str) -> None:
        self._host = host.rstrip("/")
        self._app_key = app_key
        self._secret_key = secret_key
        self._token: _Token | None = None
        self._http = httpx.Client(timeout=httpx.Timeout(10.0, connect=3.0))

    def close(self) -> None:
        self._http.close()

    def authenticate(self) -> None:
        url = f"{self._host}/oauth2/token"
        body = {
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "secretkey": self._secret_key,
        }
        r = self._http.post(url, json=body, headers={"Content-Type": "application/json;charset=UTF-8"})
        r.raise_for_status()
        data = r.json()
        if data.get("return_code") != 0:
            raise KiwoomError(f"키움 인증 실패: {data}")
        self._token = _Token(value=data["token"], expires_dt=data.get("expires_dt", ""))

    def _headers(self, api_id: str, cont_yn: str = "N", next_key: str = "") -> dict[str, str]:
        if self._token is None:
            self.authenticate()
        assert self._token is not None
        return {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self._token.value}",
            "api-id": api_id,
            "cont-yn": cont_yn,
            "next-key": next_key,
        }

    def _post(
        self,
        endpoint: str,
        api_id: str,
        body: dict[str, Any],
        cont_yn: str = "N",
        next_key: str = "",
    ) -> tuple[dict[str, Any], dict[str, str]]:
        url = f"{self._host}{endpoint}"
        r = self._http.post(url, json=body, headers=self._headers(api_id, cont_yn, next_key))
        if r.status_code >= 400:
            raise KiwoomError(
                f"{api_id} HTTP {r.status_code}: {r.text[:500] or '<empty>'} | req={body}"
            )
        try:
            data = r.json()
        except ValueError as e:
            raise KiwoomError(f"{api_id} JSON 파싱 실패: {r.text[:200]}") from e
        if isinstance(data, dict) and data.get("return_code") not in (None, 0):
            raise KiwoomError(
                f"{api_id} 실패 [{data.get('return_code')}]: "
                f"{data.get('return_msg') or '<no message>'} | req={body}"
            )
        return data, {k.lower(): v for k, v in r.headers.items()}

    def _post_paginated(
        self,
        endpoint: str,
        api_id: str,
        body: dict[str, Any],
        list_keys: tuple[str, ...] = (),
        max_pages: int = 50,
    ) -> dict[str, Any]:
        """연속조회를 자동 처리하여 list_keys 항목들을 누적 합친다."""
        data, headers = self._post(endpoint, api_id, body)
        if not list_keys:
            return data

        merged: dict[str, list] = {k: list(data.get(k, []) or []) for k in list_keys}
        cont_yn = headers.get("cont-yn", "N")
        next_key = headers.get("next-key", "")
        pages = 1
        while cont_yn == "Y" and next_key and pages < max_pages:
            time.sleep(0.25)  # 5 req/s 한도 고려
            data, headers = self._post(endpoint, api_id, body, cont_yn="Y", next_key=next_key)
            for k in list_keys:
                merged[k].extend(data.get(k, []) or [])
            cont_yn = headers.get("cont-yn", "N")
            next_key = headers.get("next-key", "")
            pages += 1

        result = dict(data)
        result.update(merged)
        return result

    def trades(self, account_no: str, ymd: str) -> dict[str, Any]:
        """kt00009 계좌별주문체결내역상세 — 특정일자 체결 내역."""
        body = {
            "ord_dt": ymd,
            "qry_tp": "1",
            "stk_bond_tp": "0",
            "mrkt_tp": "0",       # 시장구분: 0 통합, 1 KOSPI, 2 KOSDAQ
            "sell_tp": "0",
            "stk_cd": "",
            "fr_ord_no": "",
            "dmst_stex_tp": "%",
        }
        return self._post_paginated(
            "/api/dostk/acnt", "kt00009", body, list_keys=("acnt_ord_cntr_prps_dtl",)
        )

    def daily_journal(self, account_no: str, ymd: str) -> dict[str, Any]:
        """ka10170 당일매매일지요청."""
        body = {"base_dt": ymd, "ottks_tp": "1", "ch_crd_tp": "0"}
        return self._post("/api/dostk/acnt", "ka10170", body)[0]

    def realized_pl_per_stock(self, account_no: str, ymd: str) -> dict[str, Any]:
        """ka10073 일자별종목별실현손익_일자."""
        body = {"strt_dt": ymd, "end_dt": ymd}
        return self._post_paginated(
            "/api/dostk/acnt", "ka10073", body, list_keys=("dt_stk_rlzt_pl",)
        )

    def balance(self, account_no: str) -> dict[str, Any]:
        """kt00018 계좌평가잔고내역."""
        body = {"qry_tp": "1", "dmst_stex_tp": "KRX"}
        return self._post_paginated(
            "/api/dostk/acnt", "kt00018", body, list_keys=("acnt_evlt_remn_indv_tot",)
        )

    def deposit(self, account_no: str) -> dict[str, Any]:
        """kt00001 예수금상세현황요청."""
        return self._post("/api/dostk/acnt", "kt00001", {"qry_tp": "3"})[0]

    def current_price(self, stk_cd: str) -> int:
        """ka10001 주식기본정보요청 — cur_prc(부호 포함 문자열) 현재가."""
        code = (stk_cd or "").strip().lstrip("A")
        if not code:
            raise KiwoomError("종목코드가 비어 있습니다.")
        data, _ = self._post("/api/dostk/stkinfo", "ka10001", {"stk_cd": code})
        raw = str(data.get("cur_prc") or "").replace(",", "").strip()
        try:
            return abs(int(float(raw.lstrip("+-") or 0))) if raw else 0
        except ValueError:
            return 0
