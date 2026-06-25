"""Yahoo Finance 비공식 시세 조회 (수동 원장용 현재가·환율).

증권사 인증 없이 국내(.KS/.KQ)·해외(US 등) 현재가와 환율을 가져온다.
비공식 endpoint 라서 차단/오류가 날 수 있으므로 **모든 함수는 실패 시 None/{}**
를 반환하고, 호출 측은 수동 입력값으로 폴백한다.
"""

from __future__ import annotations

from typing import Callable

LogFn = Callable[[str], None]

# market -> (currency, yahoo_suffix). suffix 가 "" 이면 ticker 를 그대로 사용(미국 등).
MARKETS: dict[str, tuple[str, str]] = {
    "KOSPI": ("KRW", ".KS"),
    "KOSDAQ": ("KRW", ".KQ"),
    "NASDAQ": ("USD", ""),
    "NYSE": ("USD", ""),
    "AMEX": ("USD", ""),
    "기타": ("KRW", ".KS"),
}
DEFAULT_MARKET = "KOSPI"

_HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_TIMEOUT = 6.0


def market_names() -> list[str]:
    return list(MARKETS.keys())


def currency_of(market: str) -> str:
    return MARKETS.get(market, MARKETS[DEFAULT_MARKET])[0]


def yahoo_symbol(stk_cd: str, market: str) -> str:
    """종목코드 + 시장 → Yahoo 심볼. 국내는 접미사(.KS/.KQ), 해외는 ticker 그대로."""
    code = (stk_cd or "").strip().upper()
    # 키움 국내코드 'A005930' → '005930' (해외 티커 AAPL 등은 보존)
    if len(code) > 1 and code[0] == "A" and code[1:].isdigit():
        code = code[1:]
    suffix = MARKETS.get(market, MARKETS[DEFAULT_MARKET])[1]
    if suffix and not code.endswith(suffix):
        return f"{code}{suffix}"
    return code


def _chart_price(symbol: str) -> float | None:
    """Yahoo chart endpoint 에서 regularMarketPrice 한 개를 가져온다 (실패 시 None)."""
    try:
        import httpx
    except ImportError:
        return None
    for host in _HOSTS:
        try:
            r = httpx.get(
                f"{host}/v8/finance/chart/{symbol}",
                params={"interval": "1d", "range": "1d"},
                headers={"User-Agent": _UA},
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
            result = (((r.json() or {}).get("chart") or {}).get("result")) or []
            if not result:
                continue
            price = (result[0].get("meta") or {}).get("regularMarketPrice")
            if price is not None:
                return float(price)
        except Exception:  # noqa: BLE001  네트워크/JSON 오류 → 다음 호스트/폴백
            continue
    return None


def fetch_quote(symbol: str) -> float | None:
    s = (symbol or "").strip()
    return _chart_price(s) if s else None


def fetch_fx(base: str, quote: str = "KRW") -> float | None:
    """1 base 통화당 quote 통화 환율 (예: USD→KRW). 같은 통화면 1.0."""
    base = (base or "").strip().upper()
    quote = (quote or "KRW").strip().upper()
    if not base or base == quote:
        return 1.0
    return _chart_price(f"{base}{quote}=X")


# ── 시계열(일별 종가) ─────────────────────────────────────────────────────────
# KST(UTC+9). Yahoo 의 epoch 타임스탬프를 한국 날짜(YYYYMMDD)로 환산하는 데 쓴다.
def _kst_ymd(epoch: int | float) -> str:
    from datetime import datetime, timedelta, timezone
    kst = timezone(timedelta(hours=9))
    return datetime.fromtimestamp(float(epoch), kst).strftime("%Y%m%d")


def _chart_series(symbol: str, range_: str, interval: str) -> dict[str, float]:
    """Yahoo chart endpoint 에서 일별 종가 시계열을 가져온다 (실패 시 {}).

    `_chart_price` 와 같은 호스트·헤더·폴백을 쓰되, range 를 늘려(예: '2y')
    `timestamp[]` + `indicators.quote[0].close[]` 를 {YYYYMMDD(KST): 종가} 로 만든다.
    종가가 None(휴장·결측)인 날은 건너뛴다.
    """
    try:
        import httpx
    except ImportError:
        return {}
    for host in _HOSTS:
        try:
            r = httpx.get(
                f"{host}/v8/finance/chart/{symbol}",
                params={"interval": interval, "range": range_},
                headers={"User-Agent": _UA},
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
            result = (((r.json() or {}).get("chart") or {}).get("result")) or []
            if not result:
                continue
            res0 = result[0]
            stamps = res0.get("timestamp") or []
            quote = (((res0.get("indicators") or {}).get("quote")) or [{}])[0]
            closes = quote.get("close") or []
            out: dict[str, float] = {}
            for ts, close in zip(stamps, closes):
                if close is None:
                    continue
                out[_kst_ymd(ts)] = float(close)   # 같은 날 중복 시 마지막 값
            if out:
                return out
        except Exception:  # noqa: BLE001  네트워크/JSON 오류 → 다음 호스트/폴백
            continue
    return {}


def fetch_history(symbol: str, *, range_: str = "2y", interval: str = "1d") -> dict[str, float]:
    """종목/지수 심볼의 일별 종가 시계열 {YYYYMMDD: 종가}. 실패 시 {}."""
    s = (symbol or "").strip()
    return _chart_series(s, range_, interval) if s else {}


def fetch_fx_history(base: str, quote: str = "KRW", *, range_: str = "2y") -> dict[str, float]:
    """환율(예: USD→KRW)의 일별 시계열. 같은 통화면 빈 dict(호출 측에서 1.0 처리)."""
    base = (base or "").strip().upper()
    quote = (quote or "KRW").strip().upper()
    if not base or base == quote:
        return {}
    return _chart_series(f"{base}{quote}=X", range_, "1d")


def resolve_prices(
    symbols: list[tuple[str, str]],
    manual_prices: dict[str, float] | None = None,
    *,
    do_fetch: bool = True,
    log: LogFn | None = None,
) -> tuple[dict[str, float], list[str]]:
    """보유 종목 현재가 해석.

    symbols: [(stk_cd, market), ...] (중복 무방). manual_prices: {stk_cd: 현재가}.
    반환: (prices{stk_cd: float}, errors[str]). Yahoo 값 우선, 실패 시 manual 폴백.
    """
    log = log or (lambda _m: None)
    manual_prices = manual_prices or {}
    prices: dict[str, float] = {}
    errors: list[str] = []
    seen: dict[str, str] = {}
    for code, market in symbols:
        code = (code or "").strip()
        if code and code not in seen:
            seen[code] = market

    for code, market in seen.items():
        fetched: float | None = None
        if do_fetch:
            sym = yahoo_symbol(code, market)
            fetched = fetch_quote(sym)
            if fetched is None:
                errors.append(f"{code}({sym}) 시세 조회 실패")
                log(f"[warn] {code} Yahoo 시세 실패 → 수동값 사용")
        if fetched is not None:
            prices[code] = fetched
        elif code in manual_prices:
            prices[code] = float(manual_prices[code])
    return prices, errors


def resolve_fx(
    currencies: list[str],
    *,
    do_fetch: bool = True,
    log: LogFn | None = None,
) -> dict[str, float]:
    """비-KRW 통화별 → KRW 환율 (참고용 표시). 실패 시 해당 통화 생략."""
    log = log or (lambda _m: None)
    out: dict[str, float] = {}
    if not do_fetch:
        return out
    for ccy in currencies:
        if not ccy or ccy == "KRW":
            continue
        rate = fetch_fx(ccy, "KRW")
        if rate:
            out[ccy] = rate
        else:
            log(f"[warn] {ccy}/KRW 환율 조회 실패")
    return out
