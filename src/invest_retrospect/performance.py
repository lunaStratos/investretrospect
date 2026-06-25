"""성과 대시보드 집계 엔진 (GUI 비의존, CLI/테스트 재사용 가능).

수동 원장/DB 의 매매 이벤트(`LedgerEntry`)를 날짜축으로 재생(replay)하여:

- **자산 추이**(원화 환산 일별 평가금액) — 미실현 포함 총평가금액
- **시간가중수익률 지수**(TWR base100) — 입출금(매수/매도 현금흐름)에 의한 왜곡을
  제거해 벤치마크(KOSPI/S&P500 등)와 같은 축에서 비교 가능
- **누적 실현손익**(원화)
- **요약 통계** — 총수익률, MDD, 승률, 벤치마크 대비 초과수익

시세 시계열은 `prices.fetch_history` / `prices.fetch_fx_history` (Yahoo 비공식)에서
받으며, 실패 시 빈 결과로 폴백한다. 해외주식은 일별 환율로 원화 환산한다.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Callable

from invest_retrospect import prices
from invest_retrospect.manual import LedgerEntry, _replay

LogFn = Callable[[str], None]

# 표시명 → Yahoo 지수 심볼. 벤치마크 비교선용.
BENCHMARKS: dict[str, str] = {
    "KOSPI": "^KS11",
    "KOSDAQ": "^KQ11",
    "S&P500": "^GSPC",
    "NASDAQ": "^IXIC",
}


# ── 시계열 조회 헬퍼 (테스트 시 주입 가능) ───────────────────────────────────
@dataclass
class _Asof:
    """정렬된 (날짜, 값) 시계열에서 'd 이하 가장 최근 값'(forward-fill)을 준다."""
    dates: list[str] = field(default_factory=list)
    values: list[float] = field(default_factory=list)

    @classmethod
    def from_map(cls, m: dict[str, float]) -> "_Asof":
        dates = sorted(m)
        return cls(dates=dates, values=[m[d] for d in dates])

    def at(self, ymd: str) -> float | None:
        if not self.dates:
            return None
        i = bisect.bisect_right(self.dates, ymd)
        return self.values[i - 1] if i > 0 else None


@dataclass
class EquitySeries:
    """성과 곡선 묶음. 모든 리스트는 `dates` 와 같은 길이·같은 날짜축."""
    dates: list[str] = field(default_factory=list)          # YYYYMMDD
    equity_krw: list[float] = field(default_factory=list)    # 일별 평가금액(원화)
    return_index: list[float] = field(default_factory=list)  # TWR base100
    cum_realized_krw: list[float] = field(default_factory=list)  # 누적 실현손익(원화)


@dataclass
class Stats:
    total_return_pct: float = 0.0   # 시간가중 총수익률(%)
    cum_realized_krw: float = 0.0   # 누적 실현손익(원화)
    mdd_pct: float = 0.0            # 최대낙폭(%) — return_index 기준, 음수
    win_rate_pct: float = 0.0      # 승률(%) = 이익 실현 건 / 전체 실현 건
    win_count: int = 0
    trade_count: int = 0           # 실현(매도) 건수


# ── 실현손익(매도 건별) ──────────────────────────────────────────────────────
def realized_events(entries: list[LedgerEntry]) -> list[tuple[str, float]]:
    """매도 건별 실현손익 [(date, realized), ...] (날짜 오름차순).

    종목별 이동평균 평단을 추적해 매도 시점의 실현손익을 산출한다. `manual._replay`
    의 매도 로직과 동일한 방식(보유 초과분은 보유수량으로 클램프).
    """
    avg_cost: dict[str, float] = {}   # 잔여 취득원가 (qty*평단)
    qty: dict[str, int] = {}
    out: list[tuple[str, float]] = []
    for e in sorted((x for x in entries if x.date), key=lambda x: x.date):
        code = e.stk_cd or e.stk_nm
        q = qty.get(code, 0)
        c = avg_cost.get(code, 0.0)
        if e.side == "매수":
            qty[code] = q + e.qty
            avg_cost[code] = c + e.qty * e.price
        else:  # 매도
            avg = (c / q) if q > 0 else e.price
            sell_qty = min(e.qty, q) if q > 0 else 0
            realized = sell_qty * (e.price - avg)
            qty[code] = q - sell_qty
            avg_cost[code] = c - sell_qty * avg
            out.append((e.date, realized))
    return out


# ── 환산 헬퍼 ────────────────────────────────────────────────────────────────
def _fx_asof_factory(
    currencies: set[str], *, do_fetch: bool, log: LogFn
) -> dict[str, _Asof]:
    """통화별 → KRW 일별 환율 asof. 시계열 실패 시 단일 환율을 상수로 폴백."""
    out: dict[str, _Asof] = {}
    for ccy in currencies:
        if not ccy or ccy == "KRW":
            continue
        hist = prices.fetch_fx_history(ccy, "KRW") if do_fetch else {}
        if hist:
            out[ccy] = _Asof.from_map(hist)
            continue
        spot = prices.fetch_fx(ccy, "KRW") if do_fetch else None
        if spot:
            out[ccy] = _Asof(dates=["00000000"], values=[float(spot)])
            log(f"[warn] {ccy}/KRW 환율 시계열 실패 → 현재 환율 {spot:.2f} 상수 적용")
        else:
            out[ccy] = _Asof(dates=["00000000"], values=[1.0])
            log(f"[warn] {ccy}/KRW 환율 조회 실패 → 1.0 으로 처리(원화 취급)")
    return out


def _krw_factor(fx_asof: dict[str, _Asof], ccy: str, ymd: str) -> float:
    if ccy == "KRW":
        return 1.0
    a = fx_asof.get(ccy)
    v = a.at(ymd) if a else None
    return v if v is not None else 1.0


# ── 자산 추이 곡선 ───────────────────────────────────────────────────────────
def build_equity_curve(
    entries: list[LedgerEntry],
    start_ymd: str,
    end_ymd: str,
    *,
    do_fetch: bool = True,
    log: LogFn | None = None,
) -> EquitySeries:
    """원장 → 성과 곡선(EquitySeries). [start_ymd, end_ymd] 범위.

    1. 보유에 등장하는 전 종목의 일별 종가 + 필요한 통화의 환율 시계열 조회.
    2. 날짜축(거래일 ∪ 입력일) 각 날짜에 `_replay` 로 보유수량을 얻어
       Σ qty × 종가(forward-fill) × 환율(원화) = 일별 평가금액.
    3. 매수/매도 현금흐름을 제거한 시간가중수익률(TWR)을 base100 지수로 산출.
    """
    log = log or (lambda _m: None)
    entries = [e for e in entries if e.date]
    if not entries:
        return EquitySeries()

    # 종목별 (code, market, currency) 와 시세 시계열
    sym_market: dict[str, str] = {}
    for e in entries:
        code = e.stk_cd or e.stk_nm
        sym_market.setdefault(code, e.market or prices.DEFAULT_MARKET)

    close_asof: dict[str, _Asof] = {}
    for code, market in sym_market.items():
        hist = prices.fetch_history(prices.yahoo_symbol(code, market)) if do_fetch else {}
        if hist:
            close_asof[code] = _Asof.from_map(hist)
        else:
            log(f"[warn] {code} 종가 시계열 조회 실패 → 평가 제외(자산곡선 일부 누락)")
            close_asof[code] = _Asof()

    currencies = {prices.currency_of(m) for m in sym_market.values()}
    fx_asof = _fx_asof_factory(currencies, do_fetch=do_fetch, log=log)

    # 날짜축: 종가가 존재하는 거래일 ∪ 매매 입력일, 모두 [start,end] 로 클램프
    axis: set[str] = set()
    for a in close_asof.values():
        axis.update(d for d in a.dates if start_ymd <= d <= end_ymd)
    axis.update(e.date for e in entries if start_ymd <= e.date <= end_ymd)
    dates = sorted(axis)
    if not dates:
        return EquitySeries()

    # 일별 순현금흐름(원화): 매수 +, 매도 − (TWR 의 외부 유입/유출)
    flow_krw: dict[str, float] = {}
    for e in entries:
        if not (start_ymd <= e.date <= end_ymd):
            continue
        f = _krw_factor(fx_asof, e.currency, e.date)
        amt = e.qty * e.price * f
        flow_krw[e.date] = flow_krw.get(e.date, 0.0) + (amt if e.side == "매수" else -amt)

    realized = realized_events(entries)

    equity_krw: list[float] = []
    return_index: list[float] = []
    cum_realized: list[float] = []
    idx = 100.0
    prev_v: float | None = None
    started = False
    for d in dates:
        pos, _ = _replay(entries, d)
        v = 0.0
        for code, p in pos.items():
            if p.qty <= 0:
                continue
            close = close_asof[code].at(d)
            if close is None:
                continue
            ccy = prices.currency_of(p.market)
            v += p.qty * close * _krw_factor(fx_asof, ccy, d)
        equity_krw.append(v)

        # 시간가중수익률(TWR): r = (V_t - flow_t) / V_{t-1} - 1
        #   기말자산에서 당일 순현금흐름(매수 유입/매도 유출)을 빼 '순수 시장 변동'
        #   에 의한 수익률만 남긴다 → 입출금 규모에 무관하게 벤치마크와 비교 가능.
        #   전일 자산이 0(미보유)이면 수익률을 계산하지 않고 직전 지수를 유지한다.
        #   (전량 매도→재매수처럼 중간에 0이 되면 그 사이 구간은 지수가 보존됨)
        flow = flow_krw.get(d, 0.0)
        if prev_v and prev_v > 0:
            r = (v - flow) / prev_v - 1.0
            idx *= (1.0 + r)
            started = True
        return_index.append(idx if started else 100.0)
        prev_v = v

        cr = sum(amt for dt, amt in realized if dt <= d)
        cum_realized.append(cr)

    return EquitySeries(
        dates=dates,
        equity_krw=equity_krw,
        return_index=return_index,
        cum_realized_krw=cum_realized,
    )


# ── 벤치마크 ─────────────────────────────────────────────────────────────────
def benchmark_index(name: str, dates: list[str], *, do_fetch: bool = True) -> list[float] | None:
    """지정 날짜축에 정렬된 벤치마크 base100 지수. 데이터 없으면 None."""
    sym = BENCHMARKS.get(name)
    if not sym or not dates or not do_fetch:
        return None
    hist = prices.fetch_history(sym)
    if not hist:
        return None
    asof = _Asof.from_map(hist)
    base: float | None = None
    out: list[float] = []
    for d in dates:
        v = asof.at(d)
        if v is None:
            out.append(base if base is not None else 100.0)
            continue
        if base is None:
            base = v
        out.append(v / base * 100.0 if base else 100.0)
    return out if base is not None else None


# ── 통계 ─────────────────────────────────────────────────────────────────────
def compute_stats(series: EquitySeries, entries: list[LedgerEntry]) -> Stats:
    s = Stats()
    if series.return_index:
        s.total_return_pct = series.return_index[-1] - 100.0
        peak = series.return_index[0]
        mdd = 0.0
        for v in series.return_index:
            peak = max(peak, v)
            if peak > 0:
                mdd = min(mdd, v / peak - 1.0)
        s.mdd_pct = mdd * 100.0
    if series.cum_realized_krw:
        s.cum_realized_krw = series.cum_realized_krw[-1]
    evs = realized_events(entries)
    s.trade_count = len(evs)
    s.win_count = sum(1 for _d, r in evs if r > 0)
    s.win_rate_pct = (s.win_count / s.trade_count * 100.0) if s.trade_count else 0.0
    return s
