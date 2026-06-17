"""대신증권 CYBOS Plus 클라이언트.

CYBOS Plus 는 대신증권이 제공하는 Windows COM/ActiveX 기반 트레이딩 API 다.
REST 가 아니라 win32com.client.Dispatch 로 COM 객체를 생성해서 호출한다.

요구사항 (사전):
  1. Windows 7 이상
  2. **32-bit Python** (CYBOS Plus 가 32-bit COM 서버이므로)
  3. CYBOS Plus 설치 + HTS 로 로그인된 상태
  4. `pip install pywin32`  (Windows 에서만 설치됨)

연결 흐름:
  - HTS 로 사용자가 직접 로그인 → CYBOS 서버와 연결
  - 본 클라이언트는 별도 토큰 발급/인증 없이 COM 객체로 곧바로 호출
  - CpUtil.CpCybos.IsConnect == 1 인지로 연결 확인
  - CpTrade.CpTdUtil.TradeInit() 한 번 호출 후 거래 관련 TR 사용 가능

호출 패턴:
  obj = win32com.client.Dispatch("CpTrade.CpTd6033")
  obj.SetInputValue(0, account_no)
  obj.BlockRequest()                       # 동기 호출
  count = obj.GetHeaderValue(7)
  for i in range(count):
      v = obj.GetDataValue(field_idx, i)

요청 제한: 15초당 60회 (CpUtil.CpCybos.GetLimitRemainCount/GetLimitRemainTime).

이 모듈에서 사용하는 TR (CYBOS 매뉴얼 기준):
  - CpTrade.CpTd0314 : 일자별 매매내역 (체결)
  - CpTrade.CpTd0723 : 일자별 종목별 실현손익
  - CpTrade.CpTd6033 : 계좌별 잔고
  - CpTrade.CpTdNew5331A : 예수금/주문가능금액

응답 정규화 키 (analyzer.py 가 기대하는 키움 형식):
  - trades:                {"acnt_ord_cntr_prps_dtl": [...]}
  - realized_pl_per_stock: {"dt_stk_rlzt_pl": [...]}
  - balance:               {"acnt_evlt_remn_indv_tot": [...]}
  - deposit:               {"entr": "<정수>"}

주의: 본 클라이언트는 CYBOS Plus SDK 의 매뉴얼적 호출 패턴을 따르지만,
TR 별 GetDataValue 의 인덱스(field id) 는 SDK 버전에 따라 달라질 수 있다.
실제 운영 전에 _DASHIN_FIELDS 의 인덱스를 SDK 매뉴얼과 대조해서 확정할 것.
"""

from __future__ import annotations

import sys
import time
from collections import defaultdict
from typing import Any

from invest_retrospect.brokers.base import BrokerClient, BrokerError, BrokerInfo

# CYBOS 는 호스트 개념이 없지만 BrokerInfo 구조 유지를 위해 dummy 채움.
_LOCAL = "local://cybosplus"

INFO = BrokerInfo(
    id="daishin",
    label="대신증권 (CYBOS Plus)",
    hosts={"prod": _LOCAL, "mock": _LOCAL},
    required_keys=(),  # 별도 APP KEY 없음 — HTS 로그인으로 대체
    supports_mock=False,
)


class DaishinError(BrokerError):
    pass


class DaishinUnavailableError(DaishinError):
    """Windows + pywin32 + CYBOS Plus 가 갖춰지지 않은 환경에서 발생."""


# CYBOS GetDataValue 필드 인덱스 — SDK 매뉴얼 기준값.
# 매뉴얼 개정 시 본 dict 만 수정하면 응답 매핑이 따라감.
_FIELDS_TD0314 = {
    # CpTd0314: 신용/현금 일자별 거래내역
    "trade_date": 0,    # 거래일자 YYYYMMDD
    "trade_time": 1,    # 체결시각 HHMMSS
    "stk_code":   2,    # 종목코드 (A 접두 가능)
    "stk_name":   3,    # 종목명
    "side":       4,    # 매도/매수 구분 ('1' 매도 / '2' 매수)
    "ord_qty":    5,    # 주문수량
    "exec_qty":   6,    # 체결수량
    "ord_price":  7,    # 주문단가
    "exec_price": 8,    # 체결단가
    "ord_no":     9,    # 주문번호
    "market":     10,   # 거래소 구분
}

_FIELDS_TD0723 = {
    # CpTd0723: 일자별 종목별 실현손익
    "stk_code": 0,
    "stk_name": 1,
    "buy_amt":  2,
    "sell_amt": 3,
    "rlzt_pl":  4,
    "prft_rt":  5,
}

_FIELDS_TD6033 = {
    # CpTd6033: 계좌별 잔고
    "stk_code":   12,
    "stk_name":   0,
    "rmnd_qty":   7,    # 잔고수량
    "pur_price":  17,   # 평균단가
    "cur_price":  10,   # 현재가
    "evlt_amt":   9,    # 평가금액
    "evltv_prft": 11,   # 평가손익
    "prft_rt":    13,   # 수익률 %
}


def _safe_int(v: Any) -> int:
    if v is None or v == "":
        return 0
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _safe_float(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _norm_side(v: Any) -> str:
    """대신/키움 모두 1=매도, 2=매수 로 통일."""
    s = str(v).strip()
    if s in ("1", "매도", "sell", "S"):
        return "1"
    if s in ("2", "매수", "buy", "B"):
        return "2"
    return s


def _import_win32com() -> Any:
    """Lazy import — Windows 외 환경에서 모듈 import 자체가 깨지지 않게."""
    if sys.platform != "win32":
        raise DaishinUnavailableError(
            "대신증권 CYBOS Plus 는 Windows 환경에서만 동작합니다 "
            f"(현재 플랫폼: {sys.platform})."
        )
    try:
        import win32com.client  # type: ignore[import-not-found]
    except ImportError as e:
        raise DaishinUnavailableError(
            "pywin32 가 설치돼 있지 않습니다. `pip install pywin32` 후 재시도."
        ) from e
    return win32com.client


class DaishinClient(BrokerClient):
    """CYBOS Plus 기반 클라이언트.

    Windows 환경에서만 인스턴스화 시도해야 함. 다른 OS 에서는 ``authenticate()``
    호출 시 ``DaishinUnavailableError`` 가 발생한다.

    생성자가 ``app_key``/``app_secret`` 를 받지만 사용하지 않는다 — 다른 broker
    클라이언트와 시그니처를 맞춰 make_client() 분기를 단순하게 유지하기 위함.
    """

    info = INFO

    def __init__(
        self,
        host: str,
        app_key: str = "",
        app_secret: str = "",
        *,
        is_mock: bool = False,
        account_pwd: str = "",
    ) -> None:
        # host / app_key / app_secret / is_mock 는 사용하지 않음.
        # CYBOS 는 HTS 로그인으로 인증되며 모의투자 별도 호스트가 없음.
        self._account_pwd = account_pwd
        self._client_mod: Any = None
        self._cybos: Any = None
        self._td_util: Any = None

    # ------------------------------------------------------------ 연결

    def authenticate(self) -> None:
        """CYBOS 연결 상태 점검 + 거래 init."""
        self._client_mod = _import_win32com()
        self._cybos = self._client_mod.Dispatch("CpUtil.CpCybos")
        if int(self._cybos.IsConnect) != 1:
            raise DaishinError(
                "CYBOS Plus 가 연결돼 있지 않습니다. HTS 로 로그인 후 재시도하세요."
            )
        self._td_util = self._client_mod.Dispatch("CpTrade.CpTdUtil")
        rc = int(self._td_util.TradeInit(0))
        if rc != 0:
            raise DaishinError(
                f"CpTdUtil.TradeInit 실패 (rc={rc}). HTS 공인인증/거래 비밀번호 확인 필요."
            )

    def close(self) -> None:
        # COM 객체는 GC 에 맡김.
        self._td_util = None
        self._cybos = None

    # ------------------------------------------------------------ 호출 한도

    def _wait_for_quota(self) -> None:
        """15초당 60회 한도. 잔여가 부족하면 남은 시간만큼 대기."""
        if self._cybos is None:
            return
        try:
            remain = int(self._cybos.GetLimitRemainCount(1))
            if remain <= 0:
                wait_ms = int(self._cybos.GetLimitRemainTime())
                time.sleep(max(wait_ms, 100) / 1000.0 + 0.1)
        except Exception:  # noqa: BLE001 — COM 호출 실패는 그냥 무시하고 진행
            pass

    def _dispatch(self, prog_id: str) -> Any:
        if self._client_mod is None:
            self.authenticate()
        assert self._client_mod is not None
        return self._client_mod.Dispatch(prog_id)

    def _block_request(self, obj: Any, tr_label: str) -> None:
        self._wait_for_quota()
        rc = int(obj.BlockRequest())
        if rc != 0:
            # CYBOS 는 0 = 정상, 1=통신요청실패, 2=주문거부, 3=계좌없음, 4=주문가격오류 등
            raise DaishinError(f"CYBOS {tr_label} BlockRequest 실패 (rc={rc}).")
        # DibStatus: 0 정상, 그 외 메시지 포함
        try:
            status = int(obj.GetDibStatus())
            if status != 0:
                msg = obj.GetDibMsg1()
                raise DaishinError(f"CYBOS {tr_label} DibStatus={status}: {msg}")
        except AttributeError:
            pass

    # ------------------------------------------------------------ 매매일지 API

    def trades(self, account_no: str, ymd: str) -> dict[str, Any]:
        """CpTd0314 — 일자별 매매내역.

        SetInputValue 인덱스 (CYBOS SDK 기준):
          0 계좌번호, 1 상품관리구분, 2 시작일자, 3 종료일자, 4 매매구분('0'전체)
        """
        obj = self._dispatch("CpTrade.CpTd0314")
        obj.SetInputValue(0, account_no)
        obj.SetInputValue(1, "")        # 상품관리구분 — 빈값 = 전체
        obj.SetInputValue(2, ymd)       # 시작일자
        obj.SetInputValue(3, ymd)       # 종료일자
        obj.SetInputValue(4, "0")       # 0 전체, 1 매도, 2 매수

        rows: list[dict[str, Any]] = []
        max_pages = 50
        for _ in range(max_pages):
            self._block_request(obj, "CpTd0314")
            count = int(obj.GetHeaderValue(7) or 0)
            f = _FIELDS_TD0314
            for i in range(count):
                exec_qty = _safe_int(obj.GetDataValue(f["exec_qty"], i))
                if exec_qty <= 0:
                    continue
                rows.append({
                    "ord_no": str(obj.GetDataValue(f["ord_no"], i) or "").strip(),
                    "stk_cd": str(obj.GetDataValue(f["stk_code"], i) or "").lstrip("A").strip(),
                    "stk_nm": str(obj.GetDataValue(f["stk_name"], i) or "").strip(),
                    "trde_tp": _norm_side(obj.GetDataValue(f["side"], i)),
                    "ord_qty": _safe_int(obj.GetDataValue(f["ord_qty"], i)),
                    "ord_uv": _safe_int(obj.GetDataValue(f["ord_price"], i)),
                    "cntr_qty": exec_qty,
                    "cntr_uv": _safe_int(obj.GetDataValue(f["exec_price"], i)),
                    "cntr_tm": str(obj.GetDataValue(f["trade_time"], i) or "").strip(),
                    "dmst_stex_tp": str(obj.GetDataValue(f["market"], i) or "KRX").strip() or "KRX",
                })
            # 연속조회 여부
            try:
                cont = bool(obj.Continue)
            except AttributeError:
                cont = False
            if not cont:
                break
        return {"acnt_ord_cntr_prps_dtl": rows}

    def realized_pl_per_stock(self, account_no: str, ymd: str) -> dict[str, Any]:
        """CpTd0723 — 일자별 종목별 실현손익.

        실패 시 trades() 폴백 집계 (다른 broker 와 동일한 안전망).
        """
        try:
            obj = self._dispatch("CpTrade.CpTd0723")
            obj.SetInputValue(0, account_no)
            obj.SetInputValue(1, "")        # 상품관리구분
            obj.SetInputValue(2, ymd)       # 시작일자
            obj.SetInputValue(3, ymd)       # 종료일자
        except DaishinError:
            return self._fallback_pl_from_trades(account_no, ymd)

        rows: list[dict[str, Any]] = []
        agg: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"buy": 0, "sell": 0, "pl": 0, "rate": 0.0, "name": ""}
        )
        for _ in range(50):
            try:
                self._block_request(obj, "CpTd0723")
            except DaishinError:
                return self._fallback_pl_from_trades(account_no, ymd)
            count = int(obj.GetHeaderValue(0) or 0)
            f = _FIELDS_TD0723
            for i in range(count):
                code = str(obj.GetDataValue(f["stk_code"], i) or "").lstrip("A").strip()
                if not code:
                    continue
                agg[code]["buy"] += _safe_int(obj.GetDataValue(f["buy_amt"], i))
                agg[code]["sell"] += _safe_int(obj.GetDataValue(f["sell_amt"], i))
                agg[code]["pl"] += _safe_int(obj.GetDataValue(f["rlzt_pl"], i))
                agg[code]["name"] = (
                    agg[code]["name"]
                    or str(obj.GetDataValue(f["stk_name"], i) or "").strip()
                )
                agg[code]["rate"] = _safe_float(obj.GetDataValue(f["prft_rt"], i))
            try:
                cont = bool(obj.Continue)
            except AttributeError:
                cont = False
            if not cont:
                break

        for code, v in agg.items():
            rows.append({
                "stk_cd": code,
                "stk_nm": v["name"],
                "buy_amt": v["buy"],
                "sell_amt": v["sell"],
                "rlzt_pl": v["pl"],
                "prft_rt": v["rate"],
            })
        return {"dt_stk_rlzt_pl": rows}

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
        """CpTd6033 — 계좌별 잔고."""
        obj = self._dispatch("CpTrade.CpTd6033")
        obj.SetInputValue(0, account_no)
        obj.SetInputValue(1, "")        # 상품관리구분
        obj.SetInputValue(2, 50)        # 요청 건수 (최대 50)
        obj.SetInputValue(3, "1")       # 1: 단가구분 = 평균단가

        rows: list[dict[str, Any]] = []
        for _ in range(50):
            self._block_request(obj, "CpTd6033")
            count = int(obj.GetHeaderValue(7) or 0)
            f = _FIELDS_TD6033
            for i in range(count):
                qty = _safe_int(obj.GetDataValue(f["rmnd_qty"], i))
                if qty <= 0:
                    continue
                rows.append({
                    "stk_cd": str(obj.GetDataValue(f["stk_code"], i) or "").lstrip("A").strip(),
                    "stk_nm": str(obj.GetDataValue(f["stk_name"], i) or "").strip(),
                    "rmnd_qty": qty,
                    "pur_pric": _safe_int(obj.GetDataValue(f["pur_price"], i)),
                    "cur_prc": _safe_int(obj.GetDataValue(f["cur_price"], i)),
                    "evlt_amt": _safe_int(obj.GetDataValue(f["evlt_amt"], i)),
                    "evltv_prft": _safe_int(obj.GetDataValue(f["evltv_prft"], i)),
                    "prft_rt": _safe_float(obj.GetDataValue(f["prft_rt"], i)),
                })
            try:
                cont = bool(obj.Continue)
            except AttributeError:
                cont = False
            if not cont:
                break
        return {"acnt_evlt_remn_indv_tot": rows}

    def deposit(self, account_no: str) -> dict[str, Any]:
        """CpTdNew5331A — 예수금/주문가능금액."""
        obj = self._dispatch("CpTrade.CpTdNew5331A")
        obj.SetInputValue(0, account_no)
        obj.SetInputValue(1, "")        # 상품관리구분
        self._block_request(obj, "CpTdNew5331A")
        # GetHeaderValue 인덱스는 SDK 매뉴얼 기준:
        #   47 = 예수금 (D+0), 다양한 잔여금 필드 존재
        deposit_amt = _safe_int(obj.GetHeaderValue(47))
        return {"entr": deposit_amt}
