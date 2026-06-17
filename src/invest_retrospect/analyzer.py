"""키움 응답 dict를 일지용 구조로 정규화/집계.

키움 응답의 숫자 필드는 문자열 + 부호('+', '-')가 섞여 들어오는 경우가 많아
모두 안전하게 파싱한다.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


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


@dataclass
class Trade:
    ord_no: str
    stk_cd: str
    stk_nm: str
    side: str            # '매수' | '매도'
    ord_qty: int
    ord_price: int
    cntr_qty: int
    cntr_price: int
    cntr_time: str       # HHMMSS
    venue: str           # KRX/NXT 등
    currency: str = "KRW"   # 해외주식 대응 (수동 원장에서 USD 등)

    @property
    def amount(self) -> int:
        return self.cntr_qty * self.cntr_price


@dataclass
class StockPL:
    stk_cd: str
    stk_nm: str
    buy_amt: int = 0
    sell_amt: int = 0
    realized_pl: int = 0
    return_rate: float = 0.0
    currency: str = "KRW"


@dataclass
class Holding:
    stk_cd: str
    stk_nm: str
    qty: int
    avg_price: int
    cur_price: int
    eval_amt: int
    pl_amt: int
    return_rate: float
    currency: str = "KRW"


@dataclass
class DailyJournalData:
    date: str                                # YYYY-MM-DD
    account_no: str
    trades: list[Trade] = field(default_factory=list)
    stock_pls: list[StockPL] = field(default_factory=list)
    holdings: list[Holding] = field(default_factory=list)
    total_realized_pl: int = 0
    total_buy_amt: int = 0
    total_sell_amt: int = 0
    total_eval_amt: int = 0
    total_eval_pl: int = 0
    deposit: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def turnover(self) -> int:
        return self.total_buy_amt + self.total_sell_amt

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    @property
    def win_count(self) -> int:
        return sum(1 for s in self.stock_pls if s.realized_pl > 0)

    @property
    def lose_count(self) -> int:
        return sum(1 for s in self.stock_pls if s.realized_pl < 0)

    @property
    def win_rate(self) -> float:
        n = self.win_count + self.lose_count
        return (self.win_count / n * 100.0) if n else 0.0

    def currencies(self) -> list[str]:
        """보유/체결/손익에 등장하는 통화 목록 (KRW 우선, 그 외 알파벳순)."""
        seen = {h.currency for h in self.holdings}
        seen |= {t.currency for t in self.trades}
        seen |= {s.currency for s in self.stock_pls}
        seen.discard("")
        ordered = (["KRW"] if "KRW" in seen else []) + sorted(seen - {"KRW"})
        return ordered or ["KRW"]

    @staticmethod
    def _weights(holdings: list["Holding"], top: int) -> list[tuple[str, int, float]]:
        items = [(h.stk_nm or h.stk_cd, h.eval_amt) for h in holdings if h.eval_amt > 0]
        if not items:  # 평가금액이 전부 0 이면 원가(qty*avg_price) 폴백
            items = [
                (h.stk_nm or h.stk_cd, h.qty * h.avg_price)
                for h in holdings
                if h.qty * h.avg_price > 0
            ]
        total = sum(v for _, v in items)
        if total <= 0:
            return []
        items.sort(key=lambda x: x[1], reverse=True)
        if len(items) > top:
            head = items[:top]
            rest = sum(v for _, v in items[top:])
            if rest > 0:
                head.append(("기타", rest))
            items = head
        return [(name, value, value / total * 100.0) for name, value in items]

    def holding_weights(self, top: int = 8) -> list[tuple[str, int, float]]:
        """보유 종목의 포트폴리오 비중 (label, value, pct%) — 통화 무시 단일 집계."""
        return self._weights(self.holdings, top)

    def holding_weights_by_currency(self, top: int = 8) -> dict[str, list[tuple[str, int, float]]]:
        """통화별 포트폴리오 비중. {통화: [(label, value, pct), ...]} (빈 그룹 제외)."""
        out: dict[str, list[tuple[str, int, float]]] = {}
        for ccy in self.currencies():
            w = self._weights([h for h in self.holdings if h.currency == ccy], top)
            if w:
                out[ccy] = w
        return out

    def totals_by_currency(self) -> dict[str, dict[str, int]]:
        """통화별 합계. 단일통화(증권사 KRW)면 {'KRW': {...}} 로 기존 스칼라와 동일."""
        out: dict[str, dict[str, int]] = {}
        for ccy in self.currencies():
            hs = [h for h in self.holdings if h.currency == ccy]
            ss = [s for s in self.stock_pls if s.currency == ccy]
            out[ccy] = {
                "realized_pl": sum(s.realized_pl for s in ss),
                "buy_amt": sum(s.buy_amt for s in ss),
                "sell_amt": sum(s.sell_amt for s in ss),
                "eval_amt": sum(h.eval_amt for h in hs),
                "eval_pl": sum(h.pl_amt for h in hs),
            }
        return out


_SIDE_MAP = {"1": "매도", "2": "매수"}


def _trade_side(raw_side: str) -> str:
    s = (raw_side or "").strip()
    if s in _SIDE_MAP:
        return _SIDE_MAP[s]
    if "매수" in s:
        return "매수"
    if "매도" in s:
        return "매도"
    return s or "?"


def parse_trades(payload: dict[str, Any]) -> list[Trade]:
    rows = payload.get("acnt_ord_cntr_prps_dtl") or []
    out: list[Trade] = []
    for r in rows:
        if _to_int(r.get("cntr_qty")) <= 0:
            continue
        out.append(
            Trade(
                ord_no=str(r.get("ord_no", "")).strip(),
                stk_cd=str(r.get("stk_cd", "")).strip().lstrip("A"),
                stk_nm=str(r.get("stk_nm", "")).strip(),
                side=_trade_side(str(r.get("trde_tp", ""))),
                ord_qty=_to_int(r.get("ord_qty")),
                ord_price=_to_int(r.get("ord_uv")),
                cntr_qty=_to_int(r.get("cntr_qty")),
                cntr_price=_to_int(r.get("cntr_uv")),
                cntr_time=str(r.get("cntr_tm", "")).strip().lstrip("0") or "",
                venue=str(r.get("dmst_stex_tp", "")).strip(),
            )
        )
    return out


def parse_stock_pl(payload: dict[str, Any]) -> list[StockPL]:
    rows = payload.get("dt_stk_rlzt_pl") or []
    out: list[StockPL] = []
    for r in rows:
        out.append(
            StockPL(
                stk_cd=str(r.get("stk_cd", "")).strip().lstrip("A"),
                stk_nm=str(r.get("stk_nm", "")).strip(),
                buy_amt=_to_int(r.get("buy_amt")),
                sell_amt=_to_int(r.get("sell_amt")),
                realized_pl=_to_int(r.get("rlzt_pl")),
                return_rate=_to_float(r.get("prft_rt")),
            )
        )
    return out


def parse_holdings(payload: dict[str, Any]) -> list[Holding]:
    rows = payload.get("acnt_evlt_remn_indv_tot") or []
    out: list[Holding] = []
    for r in rows:
        qty = _to_int(r.get("rmnd_qty") or r.get("hld_qty"))
        if qty <= 0:
            continue
        out.append(
            Holding(
                stk_cd=str(r.get("stk_cd", "")).strip().lstrip("A"),
                stk_nm=str(r.get("stk_nm", "")).strip(),
                qty=qty,
                avg_price=_to_int(r.get("pur_pric") or r.get("avg_pur_pric")),
                cur_price=_to_int(r.get("cur_prc")),
                eval_amt=_to_int(r.get("evlt_amt")),
                pl_amt=_to_int(r.get("evltv_prft")),
                return_rate=_to_float(r.get("prft_rt")),
            )
        )
    return out


def aggregate_from_trades(trades: list[Trade]) -> tuple[int, int]:
    """체결로부터 매수총액/매도총액 산출."""
    buy = sum(t.amount for t in trades if t.side == "매수")
    sell = sum(t.amount for t in trades if t.side == "매도")
    return buy, sell


def aggregate_pl_from_trades(trades: list[Trade]) -> list[StockPL]:
    """체결만으로 종목별 합산(실현손익은 키움 응답 우선, 없을 때 폴백)."""
    by_code: dict[str, StockPL] = {}
    sums: dict[str, dict[str, int]] = defaultdict(lambda: {"buy": 0, "sell": 0})
    names: dict[str, str] = {}
    for t in trades:
        names.setdefault(t.stk_cd, t.stk_nm)
        if t.side == "매수":
            sums[t.stk_cd]["buy"] += t.amount
        elif t.side == "매도":
            sums[t.stk_cd]["sell"] += t.amount
    for code, s in sums.items():
        by_code[code] = StockPL(
            stk_cd=code,
            stk_nm=names.get(code, ""),
            buy_amt=s["buy"],
            sell_amt=s["sell"],
            realized_pl=0,
            return_rate=0.0,
        )
    return list(by_code.values())


def build_daily(
    date: str,
    account_no: str,
    trades_payload: dict[str, Any],
    pl_payload: dict[str, Any] | None,
    balance_payload: dict[str, Any] | None,
    deposit_payload: dict[str, Any] | None,
    journal_payload: dict[str, Any] | None,
) -> DailyJournalData:
    trades = parse_trades(trades_payload)
    stock_pls = parse_stock_pl(pl_payload) if pl_payload else aggregate_pl_from_trades(trades)
    holdings = parse_holdings(balance_payload) if balance_payload else []
    buy, sell = aggregate_from_trades(trades)
    total_realized = sum(s.realized_pl for s in stock_pls)
    total_eval = sum(h.eval_amt for h in holdings)
    total_eval_pl = sum(h.pl_amt for h in holdings)
    deposit = _to_int((deposit_payload or {}).get("entr"))

    return DailyJournalData(
        date=date,
        account_no=account_no,
        trades=trades,
        stock_pls=sorted(stock_pls, key=lambda s: s.realized_pl, reverse=True),
        holdings=sorted(holdings, key=lambda h: h.eval_amt, reverse=True),
        total_realized_pl=total_realized,
        total_buy_amt=buy,
        total_sell_amt=sell,
        total_eval_amt=total_eval,
        total_eval_pl=total_eval_pl,
        deposit=deposit,
        raw={
            "trades": trades_payload,
            "realized_pl": pl_payload,
            "balance": balance_payload,
            "deposit": deposit_payload,
            "journal": journal_payload,
        },
    )
