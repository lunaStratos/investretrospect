"""시장 대시보드 — 네이버 금융 비공식 API/페이지 수집 (investApp 포팅).

코스피/코스닥 시장 현황을 한 화면에서: 종합지수 · 환율/금리 · 시가총액 순위 ·
외국인/기관 순매수·순매도 · 외국인 보유순위.

- JSON: m.stock.naver.com / polling.finance.naver.com (UTF-8)
- HTML: finance.naver.com 시세 페이지 (EUC-KR, BeautifulSoup 파싱)

비공식 엔드포인트라 예고 없이 바뀔 수 있으며, 각 항목은 개별 실패를 흡수한다.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

# 등락 방향 (한국 관례: 상승=빨강, 하락=파랑)
UP, DOWN, FLAT = "up", "down", "flat"

_UA = ("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/120.0 Mobile Safari/537.36")
_HEADERS = {"User-Agent": _UA, "Referer": "https://m.stock.naver.com/"}
_TIMEOUT = 10.0
RANK_SIZE = 20
MARKET_SUM_PAGES = 3
_INVESTOR_FOREIGN = "9000"
_INVESTOR_INSTITUTION = "1000"
_CODE_RE = re.compile(r"code=(\d{6})")


@dataclass(frozen=True)
class Market:
    label: str
    naver_category: str       # marketValue API category (KOSPI/KOSDAQ)
    sosok: str                # deal_rank sosok (01/02)
    market_sum_sosok: str     # market_sum sosok (0/1)


KOSPI = Market("코스피", "KOSPI", "01", "0")
KOSDAQ = Market("코스닥", "KOSDAQ", "02", "1")
MARKETS = (KOSPI, KOSDAQ)


@dataclass
class IndexQuote:
    name: str
    price: str
    change: str
    rate: str
    direction: str


@dataclass
class MarketIndexItem:
    name: str
    value: str
    change: str
    direction: str


@dataclass
class RankItem:
    rank: int
    name: str
    code: str
    price: str
    sub: str
    direction: str = FLAT
    qty: str = ""        # 순매매 전용
    amount: str = ""
    volume: str = ""


@dataclass
class DashboardData:
    indices: list[IndexQuote] = field(default_factory=list)
    market_index: list[MarketIndexItem] = field(default_factory=list)
    market_cap: list[RankItem] = field(default_factory=list)
    foreign_buy: list[RankItem] = field(default_factory=list)
    foreign_sell: list[RankItem] = field(default_factory=list)
    institution_buy: list[RankItem] = field(default_factory=list)
    institution_sell: list[RankItem] = field(default_factory=list)
    foreign_holding: list[RankItem] = field(default_factory=list)


# ── HTTP ─────────────────────────────────────────────────────────────────────
def _get_text(url: str, charset: str = "utf-8") -> str:
    import httpx
    r = httpx.get(url, headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)
    r.raise_for_status()
    return r.content.decode(charset, errors="replace")


def _get_json(url: str) -> Any:
    import json
    return json.loads(_get_text(url))


def _direction(obj: dict | None) -> str:
    # 네이버 코드 규약: 1=상한 2=상승 3=보합 4=하한 5=하락
    code = str((obj or {}).get("code", ""))
    if code in ("1", "2"):
        return UP
    if code in ("4", "5"):
        return DOWN
    return FLAT


# ── 1. 종합지수 (코스피/코스닥) ──────────────────────────────────────────────
def fetch_indices() -> list[IndexQuote]:
    data = _get_json("https://polling.finance.naver.com/api/realtime/domestic/index/KOSPI,KOSDAQ")
    out: list[IndexQuote] = []
    for o in (data.get("datas") or []):
        out.append(IndexQuote(
            name=o.get("stockName", ""),
            price=o.get("closePrice", ""),
            change=o.get("compareToPreviousClosePrice", ""),
            rate=o.get("fluctuationsRatio", ""),
            direction=_direction(o.get("compareToPreviousPrice")),
        ))
    return out


# ── 2. 시가총액 순위 ─────────────────────────────────────────────────────────
def fetch_market_cap(category: str) -> list[RankItem]:
    url = f"https://m.stock.naver.com/api/stocks/marketValue/{category}?page=1&pageSize={RANK_SIZE}"
    data = _get_json(url)
    out: list[RankItem] = []
    for i, o in enumerate(data.get("stocks") or []):
        out.append(RankItem(
            rank=i + 1,
            name=o.get("stockName", ""),
            code=o.get("itemCode", ""),
            price=o.get("closePrice", ""),
            sub=o.get("marketValueHangeul", ""),
            direction=_direction(o.get("compareToPreviousPrice")),
        ))
    return out


# ── 3. 환율·금리 ─────────────────────────────────────────────────────────────
_MARKET_INDEX_WANTED = (
    ("FX_USDKRW", "원/달러"),
    (".DXY", "달러인덱스"),
    ("KROCRT=ECIX", "한국 기준금리"),
    ("USFOMC=ECIX", "미국 기준금리"),
    ("KR10YT=RR", "국고채 10년"),
    ("US10YT=RR", "미국채 10년"),
)


def fetch_market_index() -> list[MarketIndexItem]:
    data = _get_json("https://m.stock.naver.com/front-api/marketIndex/majors?category=exchange")
    # result 는 {category: [items]} 형태(혹은 구버전 list). 모두 평탄화해 코드로 조회.
    result = data.get("result")
    items: list[dict] = []
    if isinstance(result, dict):
        for arr in result.values():
            if isinstance(arr, list):
                items.extend(arr)
    elif isinstance(result, list):
        items = result
    by_code: dict[str, dict] = {}
    for o in items:
        code = o.get("reutersCode", "")
        if code and code not in by_code:
            by_code[code] = o
    out: list[MarketIndexItem] = []
    for code, label in _MARKET_INDEX_WANTED:
        o = by_code.get(code)
        if not o:
            continue
        out.append(MarketIndexItem(
            name=label,
            value=o.get("closePrice", ""),
            change=o.get("fluctuations", ""),
            direction=_direction(o.get("fluctuationsType")),
        ))
    return out


# ── 4·5. 외국인/기관 순매수·순매도 순위 (HTML, EUC-KR) ───────────────────────
def fetch_deal_rank(sosok: str, investor: str, type_: str) -> list[RankItem]:
    from bs4 import BeautifulSoup
    url = ("https://finance.naver.com/sise/sise_deal_rank_iframe.naver"
           f"?sosok={sosok}&investor_gubun={investor}&type={type_}")
    soup = BeautifulSoup(_get_text(url, "euc-kr"), "html.parser")
    out: list[RankItem] = []
    for row in soup.select("table tr"):
        link = row.select_one("a.tltle")
        if not link:
            continue
        m = _CODE_RE.search(link.get("href", ""))
        if not m:
            continue
        numbers = [td.get_text(strip=True) for td in row.select("td.number")]
        out.append(RankItem(
            rank=len(out) + 1,
            name=link.get_text(strip=True),
            code=m.group(1),
            price="", sub="", direction=FLAT,
            qty=numbers[0] if len(numbers) > 0 else "",
            amount=numbers[1] if len(numbers) > 1 else "",
            volume=numbers[2] if len(numbers) > 2 else "",
        ))
        if len(out) >= RANK_SIZE:
            break
    return out


# ── 6. 외국인 보유순위 (외국인비율 기준, 시총상위 페이지 정렬) ────────────────
def fetch_foreign_holding(market_sum_sosok: str) -> list[RankItem]:
    from bs4 import BeautifulSoup
    collected: list[tuple[RankItem, float]] = []
    for page in range(1, MARKET_SUM_PAGES + 1):
        url = ("https://finance.naver.com/sise/sise_market_sum.naver"
               f"?sosok={market_sum_sosok}&page={page}")
        soup = BeautifulSoup(_get_text(url, "euc-kr"), "html.parser")
        for row in soup.select("table.type_2 tbody tr"):
            link = row.select_one("a.tltle")
            if not link:
                continue
            m = _CODE_RE.search(link.get("href", ""))
            if not m:
                continue
            tds = row.select("td")
            if len(tds) < 9:
                continue
            ratio_text = tds[8].get_text(strip=True)
            try:
                ratio = float(ratio_text.replace(",", ""))
            except ValueError:
                continue
            collected.append((RankItem(
                rank=0,
                name=link.get_text(strip=True),
                code=m.group(1),
                price=tds[2].get_text(strip=True),
                sub=f"외국인 {ratio_text}%",
                direction=FLAT,
            ), ratio))
    collected.sort(key=lambda t: t[1], reverse=True)
    out: list[RankItem] = []
    for idx, (item, _ratio) in enumerate(collected[:RANK_SIZE]):
        item.rank = idx + 1
        out.append(item)
    return out


# ── 전체 수집 (병렬, 개별 실패 흡수) ─────────────────────────────────────────
def _safe(fn: Callable[[], list]) -> list:
    try:
        return fn()
    except Exception:  # noqa: BLE001  비공식 엔드포인트 — 개별 실패는 빈 리스트
        return []


def load_all(market: Market) -> DashboardData:
    """대시보드 데이터를 병렬로 수집. 각 항목 실패는 빈 리스트로 흡수."""
    tasks: dict[str, Callable[[], list]] = {
        "indices": fetch_indices,
        "market_index": fetch_market_index,
        "market_cap": lambda: fetch_market_cap(market.naver_category),
        "foreign_buy": lambda: fetch_deal_rank(market.sosok, _INVESTOR_FOREIGN, "buy"),
        "foreign_sell": lambda: fetch_deal_rank(market.sosok, _INVESTOR_FOREIGN, "sell"),
        "institution_buy": lambda: fetch_deal_rank(market.sosok, _INVESTOR_INSTITUTION, "buy"),
        "institution_sell": lambda: fetch_deal_rank(market.sosok, _INVESTOR_INSTITUTION, "sell"),
        "foreign_holding": lambda: fetch_foreign_holding(market.market_sum_sosok),
    }
    results: dict[str, list] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {key: ex.submit(_safe, fn) for key, fn in tasks.items()}
        for key, fut in futs.items():
            results[key] = fut.result()
    return DashboardData(**results)
