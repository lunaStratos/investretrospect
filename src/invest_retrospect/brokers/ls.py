"""LS증권 (구 이베스트투자증권) OpenAPI 클라이언트.

문서: https://openapi.ls-sec.co.kr/

주요 사항
- 인증: POST /oauth2/token, x-www-form-urlencoded
    body: grant_type, appkey, appsecretkey, scope=oob
    response: { access_token, expires_in, ... }
- 데이터 호출: 모두 POST /stock/<카테고리>, body 는 InBlock 명명 규약
    예) {"CSPAQ12200InBlock1": {...}}  → {"CSPAQ12200OutBlock1": {...}, ...}
- 공통 헤더:
    content-type: application/json; charset=utf-8
    authorization: Bearer <token>
    tr_cd:        TR 코드
    tr_cont:      "N" (또는 연속조회 시 "Y")
    tr_cont_key:  연속조회키 (이전 응답값)
- 모의투자: 별도 호스트 + appkey 필요 (LS는 mock 호스트가 분리되어 있음)

이 모듈에서 사용하는 TR_CD:
  - CSPAQ22200 : 현물계좌별 주문체결내역조회 (일자 체결 리스트)
  - CDPCQ04700 : 일별 종목별 매매손익 (실전, 권한 필요)
  - t0424      : 주식잔고 / 평가
  - CSPAQ12200 : 현물계좌예수금 주문가능금액

응답을 키움 형식으로 정규화하여 반환:
  - trades:                {"acnt_ord_cntr_prps_dtl": [...]}
  - realized_pl_per_stock: {"dt_stk_rlzt_pl": [...]}
  - balance:               {"acnt_evlt_remn_indv_tot": [...]}
  - deposit:               {"entr": "<정수>"}
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import httpx

from invest_retrospect.brokers.base import BrokerClient, BrokerError, BrokerInfo

PROD_HOST = "https://openapi.ls-sec.co.kr:8080"
MOCK_HOST = "https://openapi.ls-sec.co.kr:29443"

INFO = BrokerInfo(
    id="ls",
    label="LS증권",
    hosts={"prod": PROD_HOST, "mock": MOCK_HOST},
    required_keys=("ls_app_key", "ls_app_secret"),
)


class LSError(BrokerError):
    pass


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
    """LS 계좌번호는 보통 10~11자리 숫자. 하이픈 제거 후 그대로 사용."""
    s = (account_no or "").replace("-", "").replace(" ", "").strip()
    if not s.isdigit():
        raise LSError(f"LS 계좌번호 형식 오류: '{account_no}' (숫자만)")
    return s


class LSClient(BrokerClient):
    info = INFO

    def __init__(
        self,
        host: str,
        app_key: str,
        app_secret: str,
        *,
        is_mock: bool,
        account_pwd: str = "0000",
    ) -> None:
        self._host = host.rstrip("/")
        self._app_key = app_key
        self._app_secret = app_secret
        self._is_mock = is_mock
        self._account_pwd = account_pwd or "0000"
        self._token: _Token | None = None
        self._http = httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0))

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------ 인증

    def authenticate(self) -> None:
        url = f"{self._host}/oauth2/token"
        # 주의: form-urlencoded
        body = {
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "appsecretkey": self._app_secret,
            "scope": "oob",
        }
        r = self._http.post(
            url,
            data=body,
            headers={"content-type": "application/x-www-form-urlencoded; charset=utf-8"},
        )
        if r.status_code >= 400:
            raise LSError(f"LS 인증 실패 HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        token = data.get("access_token")
        if not token:
            raise LSError(f"LS 인증 응답에 access_token 없음: {data}")
        expires_in = int(data.get("expires_in") or 3600)
        self._token = _Token(value=token, expires_at=time.time() + expires_in - 60)

    def _ensure_token(self) -> str:
        if self._token is None or time.time() >= self._token.expires_at:
            self.authenticate()
        assert self._token is not None
        return self._token.value

    def _headers(self, tr_cd: str, tr_cont: str = "N", tr_cont_key: str = "") -> dict[str, str]:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._ensure_token()}",
            "tr_cd": tr_cd,
            "tr_cont": tr_cont,
            "tr_cont_key": tr_cont_key,
        }

    # ------------------------------------------------------------ HTTP

    def _post(
        self,
        path: str,
        tr_cd: str,
        body: dict[str, Any],
        tr_cont: str = "N",
        tr_cont_key: str = "",
    ) -> tuple[dict[str, Any], dict[str, str]]:
        url = f"{self._host}{path}"
        r = self._http.post(
            url, json=body, headers=self._headers(tr_cd, tr_cont, tr_cont_key)
        )
        if r.status_code >= 400:
            raise LSError(f"LS {tr_cd} HTTP {r.status_code}: {r.text[:500]}")
        try:
            data = r.json()
        except ValueError as e:
            raise LSError(f"LS {tr_cd} JSON 파싱 실패: {r.text[:200]}") from e

        # LS 표준 에러 필드: rsp_cd, rsp_msg (00000 = 정상)
        rsp_cd = str(data.get("rsp_cd", ""))
        if rsp_cd and rsp_cd not in ("00000", "0"):
            raise LSError(
                f"LS {tr_cd} 실패 [{rsp_cd}]: {data.get('rsp_msg') or '<no message>'}"
            )
        return data, {k.lower(): v for k, v in r.headers.items()}

    def _post_paginated(
        self,
        path: str,
        tr_cd: str,
        body: dict[str, Any],
        list_key: str,
        max_pages: int = 50,
    ) -> dict[str, Any]:
        """tr_cont == 'Y' 면 tr_cont_key 와 함께 다시 호출."""
        data, headers = self._post(path, tr_cd, body)
        merged = list(data.get(list_key) or [])
        cont = (headers.get("tr_cont") or "N").upper()
        cont_key = headers.get("tr_cont_key") or ""
        pages = 1
        while cont == "Y" and cont_key and pages < max_pages:
            time.sleep(0.2)
            data, headers = self._post(path, tr_cd, body, tr_cont="Y", tr_cont_key=cont_key)
            merged.extend(data.get(list_key) or [])
            cont = (headers.get("tr_cont") or "N").upper()
            cont_key = headers.get("tr_cont_key") or ""
            pages += 1
        result = dict(data)
        result[list_key] = merged
        return result

    # ------------------------------------------------------------ 매매일지 API

    def trades(self, account_no: str, ymd: str) -> dict[str, Any]:
        """CSPAQ22200 — 현물계좌별 주문체결내역조회 (일자 체결 리스트)."""
        accno = _normalize_account(account_no)
        body = {
            "CSPAQ22200InBlock1": {
                "QryTp": "0",                 # 0 전체
                "QrySrtDt": ymd,
                "QryEndDt": ymd,
                "SrtNo": 0,
                "PdptnCode": "00",
                "IsuLgclssCode": "01",        # 01 주식
                "IsuNo": "",
                "ExecYn": "1",                # 1 체결만
                "OrdMktCode": "00",           # 00 통합
                "BkdnSrtTp": "0",
                "OrdPtnCode": "00",
                "OrdSrtTp": "0",
                "AcntNo": accno,
                "InptPwd": self._account_pwd,
            }
        }
        data = self._post_paginated(
            "/stock/accno", "CSPAQ22200", body, list_key="CSPAQ22200OutBlock3"
        )
        rows = []
        for r in data.get("CSPAQ22200OutBlock3") or []:
            qty = _to_int(r.get("ExecQty"))
            if qty <= 0:
                continue
            # LS BnsTpCode: 1 매도 / 2 매수 (키움과 동일)
            side = str(r.get("BnsTpCode") or "").strip()
            rows.append({
                "ord_no": str(r.get("OrdNo") or "").strip(),
                "stk_cd": str(r.get("IsuNo") or "").strip().lstrip("A"),
                "stk_nm": str(r.get("IsuNm") or "").strip(),
                "trde_tp": side,
                "ord_qty": _to_int(r.get("OrdQty")),
                "ord_uv": _to_int(r.get("OrdPrc")),
                "cntr_qty": qty,
                "cntr_uv": _to_int(r.get("ExecPrc")),
                "cntr_tm": str(r.get("ExecTime") or "").strip(),
                "dmst_stex_tp": str(r.get("MktNm") or "KRX").strip() or "KRX",
            })
        return {"acnt_ord_cntr_prps_dtl": rows, "_ls_raw": data}

    def realized_pl_per_stock(self, account_no: str, ymd: str) -> dict[str, Any]:
        """CDPCQ04700 — 일자별 종목별 매매손익 (실전 권한 필요).

        실패하거나 모의면 체결 내역으로 폴백 집계 (실현손익 0).
        """
        if self._is_mock:
            return self._fallback_pl_from_trades(account_no, ymd)

        accno = _normalize_account(account_no)
        body = {
            "CDPCQ04700InBlock1": {
                "RecCnt": 1,
                "AcntNo": accno,
                "Pwd": self._account_pwd,
                "QrySrtDt": ymd,
                "QryEndDt": ymd,
                "SrtTp": "1",          # 1 종목별
                "PdGrpCode": "00",
                "IsuLgclssCode": "01",
                "IsuNo": "",
            }
        }
        try:
            data = self._post_paginated(
                "/stock/accno", "CDPCQ04700", body, list_key="CDPCQ04700OutBlock3"
            )
        except LSError:
            return self._fallback_pl_from_trades(account_no, ymd)

        agg: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"buy": 0, "sell": 0, "pl": 0, "rate": 0.0, "name": ""}
        )
        for r in data.get("CDPCQ04700OutBlock3") or []:
            code = str(r.get("IsuNo") or "").strip().lstrip("A")
            if not code:
                continue
            agg[code]["buy"] += _to_int(r.get("BnsAmt") or r.get("BuyAmt"))
            agg[code]["sell"] += _to_int(r.get("SellAmt") or r.get("SlAmt"))
            agg[code]["pl"] += _to_int(r.get("PnlAmt") or r.get("RlzPnlAmt"))
            agg[code]["name"] = agg[code]["name"] or str(r.get("IsuNm") or "").strip()
            agg[code]["rate"] = _to_float(r.get("PnlRat") or r.get("ErnRat"))

        rows = [
            {
                "stk_cd": code,
                "stk_nm": v["name"],
                "buy_amt": v["buy"],
                "sell_amt": v["sell"],
                "rlzt_pl": v["pl"],
                "prft_rt": v["rate"],
            }
            for code, v in agg.items()
        ]
        return {"dt_stk_rlzt_pl": rows, "_ls_raw": data}

    def _fallback_pl_from_trades(self, account_no: str, ymd: str) -> dict[str, Any]:
        trades = self.trades(account_no, ymd).get("acnt_ord_cntr_prps_dtl", [])
        agg: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"buy": 0, "sell": 0, "name": ""}
        )
        for t in trades:
            code = t["stk_cd"]
            amt = t["cntr_qty"] * t["cntr_uv"]
            if t["trde_tp"] == "2":
                agg[code]["buy"] += amt
            elif t["trde_tp"] == "1":
                agg[code]["sell"] += amt
            agg[code]["name"] = agg[code]["name"] or t["stk_nm"]
        rows = [
            {
                "stk_cd": code,
                "stk_nm": v["name"],
                "buy_amt": v["buy"],
                "sell_amt": v["sell"],
                "rlzt_pl": 0,
                "prft_rt": 0.0,
            }
            for code, v in agg.items()
        ]
        return {"dt_stk_rlzt_pl": rows, "_fallback_from_trades": True}

    def balance(self, account_no: str) -> dict[str, Any]:
        """t0424 — 주식잔고."""
        accno = _normalize_account(account_no)
        body = {
            "t0424InBlock": {
                "accno": accno,
                "passwd": self._account_pwd,
                "prcgb": "1",     # 단가구분 1: 평균단가
                "chegb": "0",     # 체결구분
                "dangb": "0",     # 단일가구분
                "charge": "0",    # 제비용포함
                "cts_expcode": "",
            }
        }
        data = self._post_paginated(
            "/stock/accno", "t0424", body, list_key="t0424OutBlock1"
        )
        rows = []
        for r in data.get("t0424OutBlock1") or []:
            qty = _to_int(r.get("janqty") or r.get("mdposqt"))
            if qty <= 0:
                continue
            rows.append({
                "stk_cd": str(r.get("expcode") or "").strip().lstrip("A"),
                "stk_nm": str(r.get("hname") or "").strip(),
                "rmnd_qty": qty,
                "pur_pric": _to_int(r.get("pamt") or r.get("price")),
                "cur_prc": _to_int(r.get("price")),
                "evlt_amt": _to_int(r.get("appamt")),
                "evltv_prft": _to_int(r.get("dtsunik")),
                "prft_rt": _to_float(r.get("sunikrt")),
            })
        return {"acnt_evlt_remn_indv_tot": rows, "_ls_raw": data}

    def deposit(self, account_no: str) -> dict[str, Any]:
        """CSPAQ12200 — 현물계좌예수금 주문가능금액."""
        accno = _normalize_account(account_no)
        body = {
            "CSPAQ12200InBlock1": {
                "RecCnt": 1,
                "AcntNo": accno,
                "Pwd": self._account_pwd,
                "BalCreTp": "0",
                "CmsnAppTpCode": "0",
                "D2balBaseQryTp": "0",
                "UprcTpCode": "0",
            }
        }
        data, _ = self._post("/stock/accno", "CSPAQ12200", body)
        out2 = data.get("CSPAQ12200OutBlock2") or {}
        # OutBlock2 가 list 인 응답도 있으니 둘 다 처리
        if isinstance(out2, list):
            out2 = out2[0] if out2 else {}
        deposit_amt = _to_int(out2.get("Dps") or out2.get("DpsAmt") or out2.get("MnyOrdAbleAmt"))
        return {"entr": deposit_amt, "_ls_raw": data}
