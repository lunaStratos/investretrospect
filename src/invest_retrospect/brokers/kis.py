"""한국투자증권(KIS) Open API 클라이언트.

문서: https://apiportal.koreainvestment.com/

주요 사항
- 인증: POST /oauth2/tokenP (24h 유효)
- 헤더에 tr_id, appkey, appsecret, custtype 필수
- 모의투자는 tr_id 가 'V'로 시작 (실전은 'T' 또는 'C')
- 계좌번호: CANO(8자리) + ACNT_PRDT_CD(2자리, 보통 "01") 로 분리해서 전송
  → 본 클라이언트는 "12345678-01" 또는 "1234567801" 또는 "12345678" 모두 허용
- CTRP6548R (일별손익) 는 실전 전용 — 모의에서는 체결 내역으로 폴백 집계

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

PROD_HOST = "https://openapi.koreainvestment.com:9443"
MOCK_HOST = "https://openapivts.koreainvestment.com:29443"

INFO = BrokerInfo(
    id="kis",
    label="한국투자증권",
    hosts={"prod": PROD_HOST, "mock": MOCK_HOST},
    required_keys=("kis_app_key", "kis_app_secret"),
)


class KISError(BrokerError):
    pass


@dataclass
class _Token:
    value: str
    expires_at: float  # epoch seconds


def _split_account(account_no: str) -> tuple[str, str]:
    """계좌번호를 (CANO 8자리, ACNT_PRDT_CD 2자리) 로 분리.

    허용 입력: '12345678-01', '1234567801', '12345678' (PRDT 기본 '01')
    """
    s = (account_no or "").replace(" ", "").strip()
    if "-" in s:
        cano, prdt = s.split("-", 1)
    elif len(s) == 10 and s.isdigit():
        cano, prdt = s[:8], s[8:]
    elif len(s) == 8 and s.isdigit():
        cano, prdt = s, "01"
    else:
        raise KISError(f"KIS 계좌번호 형식 오류: '{account_no}' (예: 12345678-01)")
    if len(cano) != 8 or not cano.isdigit():
        raise KISError(f"KIS CANO(앞 8자리)가 올바르지 않음: '{cano}'")
    if len(prdt) != 2 or not prdt.isdigit():
        raise KISError(f"KIS ACNT_PRDT_CD(뒤 2자리)가 올바르지 않음: '{prdt}'")
    return cano, prdt


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


class KISClient(BrokerClient):
    info = INFO

    def __init__(self, host: str, app_key: str, app_secret: str, *, is_mock: bool) -> None:
        self._host = host.rstrip("/")
        self._app_key = app_key
        self._app_secret = app_secret
        self._is_mock = is_mock
        self._token: _Token | None = None
        self._http = httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0))

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------ 인증

    def authenticate(self) -> None:
        url = f"{self._host}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
        }
        r = self._http.post(url, json=body, headers={"content-type": "application/json"})
        if r.status_code >= 400:
            raise KISError(f"KIS 인증 실패 HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        token = data.get("access_token")
        if not token:
            raise KISError(f"KIS 인증 응답에 access_token 없음: {data}")
        # expires_in 은 초 단위. 약간 여유 두고 만료 시각 저장.
        expires_in = int(data.get("expires_in") or 3600)
        self._token = _Token(value=token, expires_at=time.time() + expires_in - 60)

    def _ensure_token(self) -> str:
        if self._token is None or time.time() >= self._token.expires_at:
            self.authenticate()
        assert self._token is not None
        return self._token.value

    def _tr_id(self, prod_tr: str, mock_tr: str | None = None) -> str:
        """실전/모의 TR_ID 분기. 모의 미지원이면 mock_tr=None → 실전 그대로."""
        if self._is_mock and mock_tr is not None:
            return mock_tr
        return prod_tr

    def _headers(self, tr_id: str, tr_cont: str = "") -> dict[str, str]:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._ensure_token()}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
            "tr_cont": tr_cont,
            "custtype": "P",  # 개인
        }

    # ------------------------------------------------------------ HTTP

    def _get(
        self,
        path: str,
        tr_id: str,
        params: dict[str, Any],
        tr_cont: str = "",
    ) -> tuple[dict[str, Any], dict[str, str]]:
        url = f"{self._host}{path}"
        r = self._http.get(url, params=params, headers=self._headers(tr_id, tr_cont))
        if r.status_code >= 400:
            raise KISError(f"{tr_id} HTTP {r.status_code}: {r.text[:500]}")
        try:
            data = r.json()
        except ValueError as e:
            raise KISError(f"{tr_id} JSON 파싱 실패: {r.text[:200]}") from e
        rt_cd = str(data.get("rt_cd", ""))
        if rt_cd and rt_cd != "0":
            raise KISError(f"{tr_id} 실패 [{rt_cd}]: {data.get('msg1') or data.get('msg_cd')}")
        return data, {k.lower(): v for k, v in r.headers.items()}

    def _get_paginated(
        self,
        path: str,
        tr_id: str,
        params: dict[str, Any],
        list_keys: tuple[str, ...] = ("output", "output1"),
        max_pages: int = 50,
    ) -> dict[str, Any]:
        """KIS 연속조회: 응답 헤더 tr_cont 가 'F'/'M' 이면 ctx_area 를 다음 요청에 넣고 tr_cont='N' 로 호출."""
        data, headers = self._get(path, tr_id, params)
        merged: dict[str, list] = {}
        for k in list_keys:
            v = data.get(k)
            if isinstance(v, list):
                merged[k] = list(v)
        cont = (headers.get("tr_cont") or "").upper()
        pages = 1
        while cont in ("F", "M") and pages < max_pages:
            time.sleep(0.2)
            next_params = dict(params)
            next_params["CTX_AREA_FK100"] = data.get("ctx_area_fk100", "")
            next_params["CTX_AREA_NK100"] = data.get("ctx_area_nk100", "")
            data, headers = self._get(path, tr_id, next_params, tr_cont="N")
            for k, lst in merged.items():
                v = data.get(k)
                if isinstance(v, list):
                    lst.extend(v)
            cont = (headers.get("tr_cont") or "").upper()
            pages += 1
        result = dict(data)
        result.update(merged)
        return result

    # ------------------------------------------------------------ 트레이드 변환

    @staticmethod
    def _kis_side_to_kiwoom(sll_buy_dvsn_cd: str, name: str = "") -> str:
        """KIS sll_buy_dvsn_cd 01=매도 02=매수 → 키움 trde_tp 1=매도 2=매수."""
        s = (sll_buy_dvsn_cd or "").strip()
        if s == "01":
            return "1"
        if s == "02":
            return "2"
        if "매도" in name:
            return "1"
        if "매수" in name:
            return "2"
        return ""

    # ------------------------------------------------------------ 매매일지 API

    def trades(self, account_no: str, ymd: str) -> dict[str, Any]:
        """주식 일별 주문 체결 조회 (TTTC8001R / VTTC8001R)."""
        cano, prdt = _split_account(account_no)
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": prdt,
            "INQR_STRT_DT": ymd,
            "INQR_END_DT": ymd,
            "SLL_BUY_DVSN_CD": "00",   # 00 전체, 01 매도, 02 매수
            "INQR_DVSN": "00",         # 00 역순
            "PDNO": "",
            "CCLD_DVSN": "01",         # 01 체결, 02 미체결, 00 전체
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        tr_id = self._tr_id("TTTC8001R", "VTTC8001R")
        data = self._get_paginated(
            "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            tr_id, params, list_keys=("output1",),
        )

        rows = []
        for r in data.get("output1") or []:
            tot_ccld_qty = _to_int(r.get("tot_ccld_qty"))
            if tot_ccld_qty <= 0:
                continue
            avg = _to_int(r.get("avg_prvs"))     # 평균가
            tot_amt = _to_int(r.get("tot_ccld_amt"))
            cntr_uv = avg if avg > 0 else (tot_amt // tot_ccld_qty if tot_ccld_qty else 0)
            rows.append({
                "ord_no": str(r.get("odno") or "").strip(),
                "stk_cd": str(r.get("pdno") or "").strip(),
                "stk_nm": str(r.get("prdt_name") or "").strip(),
                "trde_tp": self._kis_side_to_kiwoom(
                    str(r.get("sll_buy_dvsn_cd") or ""),
                    str(r.get("sll_buy_dvsn_cd_name") or ""),
                ),
                "ord_qty": _to_int(r.get("ord_qty")),
                "ord_uv": _to_int(r.get("ord_unpr")),
                "cntr_qty": tot_ccld_qty,
                "cntr_uv": cntr_uv,
                "cntr_tm": str(r.get("ord_tmd") or "").strip(),
                "dmst_stex_tp": "KRX",
            })
        return {"acnt_ord_cntr_prps_dtl": rows, "_kis_raw": data}

    def realized_pl_per_stock(self, account_no: str, ymd: str) -> dict[str, Any]:
        """일별 종목별 실현손익 (CTRP6548R, 실전 전용).

        모의투자는 미지원 — 체결 내역이 있으면 매수/매도 합으로 폴백 집계 (실현손익은 0).
        """
        cano, prdt = _split_account(account_no)

        if self._is_mock:
            return self._fallback_pl_from_trades(account_no, ymd)

        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": prdt,
            "SORT_DVSN": "00",
            "PDNO": "",
            "INQR_STRT_DT": ymd,
            "INQR_END_DT": ymd,
            "CBLC_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        try:
            data = self._get_paginated(
                "/uapi/domestic-stock/v1/trading/inquire-period-trade-profit",
                "CTRP6548R", params, list_keys=("output1",),
            )
        except KISError:
            # 실전이라도 권한 없으면 폴백
            return self._fallback_pl_from_trades(account_no, ymd)

        # 같은 종목이 여러 row 로 나올 수 있음 → 종목 단위로 합산
        agg: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"buy": 0, "sell": 0, "pl": 0, "rate": 0.0, "name": ""}
        )
        for r in data.get("output1") or []:
            code = str(r.get("pdno") or "").strip()
            if not code:
                continue
            agg[code]["buy"] += _to_int(r.get("buy_amt"))
            agg[code]["sell"] += _to_int(r.get("sll_amt"))
            agg[code]["pl"] += _to_int(r.get("rlzt_pfls"))
            agg[code]["name"] = agg[code]["name"] or str(r.get("prdt_name") or "").strip()
            agg[code]["rate"] = _to_float(r.get("pfls_rt"))

        rows = []
        for code, v in agg.items():
            rows.append({
                "stk_cd": code,
                "stk_nm": v["name"],
                "buy_amt": v["buy"],
                "sell_amt": v["sell"],
                "rlzt_pl": v["pl"],
                "prft_rt": v["rate"],
            })
        return {"dt_stk_rlzt_pl": rows, "_kis_raw": data}

    def _fallback_pl_from_trades(self, account_no: str, ymd: str) -> dict[str, Any]:
        trades = self.trades(account_no, ymd).get("acnt_ord_cntr_prps_dtl", [])
        agg: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"buy": 0, "sell": 0, "name": ""}
        )
        for t in trades:
            code = t["stk_cd"]
            amt = t["cntr_qty"] * t["cntr_uv"]
            if t["trde_tp"] == "2":   # 매수
                agg[code]["buy"] += amt
            elif t["trde_tp"] == "1": # 매도
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

    def _inquire_balance_raw(self, account_no: str) -> dict[str, Any]:
        cano, prdt = _split_account(account_no)
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": prdt,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",          # 02 종목별
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        tr_id = self._tr_id("TTTC8434R", "VTTC8434R")
        return self._get_paginated(
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            tr_id, params, list_keys=("output1",),
        )

    def balance(self, account_no: str) -> dict[str, Any]:
        data = self._inquire_balance_raw(account_no)
        rows = []
        for r in data.get("output1") or []:
            qty = _to_int(r.get("hldg_qty"))
            if qty <= 0:
                continue
            rows.append({
                "stk_cd": str(r.get("pdno") or "").strip(),
                "stk_nm": str(r.get("prdt_name") or "").strip(),
                "rmnd_qty": qty,
                "pur_pric": _to_int(r.get("pchs_avg_pric")),
                "cur_prc": _to_int(r.get("prpr")),
                "evlt_amt": _to_int(r.get("evlu_amt")),
                "evltv_prft": _to_int(r.get("evlu_pfls_amt")),
                "prft_rt": _to_float(r.get("evlu_pfls_rt") or r.get("evlu_erng_rt")),
            })
        return {"acnt_evlt_remn_indv_tot": rows, "_kis_raw": data}

    def current_price(self, stk_cd: str) -> int:
        """주식현재가 시세 (FHKST01010100). output.stck_prpr = 현재가."""
        code = (stk_cd or "").strip().lstrip("A")
        if not code:
            raise KISError("종목코드가 비어 있습니다.")
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
        data, _ = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100", params,
        )
        out = data.get("output") or {}
        return _to_int(out.get("stck_prpr"))

    def deposit(self, account_no: str) -> dict[str, Any]:
        """잔고 응답의 output2.dnca_tot_amt (예수금총금액) 을 사용."""
        data = self._inquire_balance_raw(account_no)
        out2 = data.get("output2") or []
        # output2 는 보통 단일 dict 또는 1-원소 list
        summary = out2[0] if isinstance(out2, list) and out2 else out2 if isinstance(out2, dict) else {}
        return {"entr": _to_int(summary.get("dnca_tot_amt")), "_kis_raw": data}
