"""AI 코멘트 생성. Gemini 또는 Ollama 사용.

config.ai_provider 값에 따라 백엔드를 선택한다.
"""

from __future__ import annotations

import httpx

from invest_retrospect.analyzer import DailyJournalData
from invest_retrospect.config import Config
from invest_retrospect.types import AICommentary


_PROMPT = """\
너는 한국 주식 단타/스윙 트레이더의 매매 코치다. 아래 매매일지 데이터를 보고
한국어로 두 섹션을 작성해라. 각 섹션은 마크다운 본문으로, 표나 코드블록 없이
3~6개의 짧은 불릿으로.

## 1) 오늘 매매 리뷰
- 잘한 점, 개선점, 반복되는 패턴(과도한 회전율, 손절 지연, 추격매수 등)
- 숫자를 인용해서 구체적으로

## 2) 다음 거래일 전략
- 보유 종목별 대응 가이드(있다면)
- 관심 가져볼 만한 액션 (확정적 종목 추천 금지, 일반론적 가이드만)
- 위험 관리 체크리스트

데이터:
{data}
"""


def generate_commentary(data: DailyJournalData, config: Config) -> AICommentary | None:
    if config.ai_provider == "none":
        return None

    prompt = _PROMPT.format(data=_summarize(data))

    if config.ai_provider == "gemini":
        text = _gemini_call(prompt, config.gemini_api_key, config.gemini_model)
        model = config.gemini_model
    elif config.ai_provider == "ollama":
        text = _ollama_call(prompt, config.ollama_host, config.ollama_model)
        model = config.ollama_model
    else:
        raise ValueError(f"알 수 없는 AI provider: {config.ai_provider}")

    review, strategy = _split_sections(text)
    return AICommentary(
        review=review, strategy=strategy, provider=config.ai_provider, model=model
    )


def _gemini_call(prompt: str, api_key: str | None, model: str) -> str:
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY 가 비어있습니다.")
    try:
        from google import genai
    except ImportError as e:
        raise RuntimeError(
            "google-genai 패키지가 설치돼 있지 않습니다. 'pip install google-genai' 후 재시도하세요."
        ) from e
    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(model=model, contents=prompt)
    return (resp.text or "").strip()


def _ollama_call(prompt: str, host: str, model: str) -> str:
    url = f"{host}/api/generate"
    body = {"model": model, "prompt": prompt, "stream": False}
    try:
        with httpx.Client(timeout=httpx.Timeout(600.0, connect=5.0)) as http:
            r = http.post(url, json=body)
            r.raise_for_status()
            data = r.json()
    except httpx.ConnectError as e:
        raise RuntimeError(
            f"Ollama 서버에 연결할 수 없습니다 ({host}). "
            "Ollama 가 실행 중인지 확인하세요. (https://ollama.com)"
        ) from e
    return (data.get("response") or "").strip()


def _summarize(data: DailyJournalData) -> str:
    lines: list[str] = []
    lines.append(f"날짜: {data.date}, 계좌: {data.account_no}")
    lines.append(
        f"체결 {data.trade_count}건, 매수 {data.total_buy_amt:,}원, "
        f"매도 {data.total_sell_amt:,}원, 회전 {data.turnover:,}원"
    )
    lines.append(
        f"실현손익 {data.total_realized_pl:,}원, "
        f"승 {data.win_count} 패 {data.lose_count} 승률 {data.win_rate:.1f}%"
    )
    lines.append(
        f"평가금액 {data.total_eval_amt:,}원, 평가손익 {data.total_eval_pl:,}원, "
        f"예수금 {data.deposit:,}원"
    )

    if data.stock_pls:
        lines.append("\n[종목별 실현손익 (상위/하위)]")
        ranked = sorted(data.stock_pls, key=lambda s: s.realized_pl, reverse=True)
        for s in ranked[:5]:
            lines.append(
                f"  + {s.stk_nm}({s.stk_cd}): {s.realized_pl:,}원 ({s.return_rate:+.2f}%)"
            )
        if len(ranked) > 5:
            for s in ranked[-3:]:
                lines.append(
                    f"  - {s.stk_nm}({s.stk_cd}): {s.realized_pl:,}원 ({s.return_rate:+.2f}%)"
                )

    if data.holdings:
        lines.append("\n[현재 보유 종목]")
        for h in data.holdings[:10]:
            lines.append(
                f"  · {h.stk_nm}({h.stk_cd}): {h.qty}주 평단 {h.avg_price:,} "
                f"현재가 {h.cur_price:,} 평가손익 {h.pl_amt:,}원 ({h.return_rate:+.2f}%)"
            )

    if data.trades:
        lines.append("\n[당일 체결 (최대 20건)]")
        for t in data.trades[:20]:
            lines.append(
                f"  · {t.cntr_time} {t.side} {t.stk_nm}({t.stk_cd}) "
                f"{t.cntr_qty}주 @ {t.cntr_price:,}"
            )

    return "\n".join(lines)


def _split_sections(text: str) -> tuple[str, str]:
    """모델이 섹션 헤더를 다양하게 적을 수 있어서 관대하게 분리."""
    markers_strategy = ("## 2)", "## 2", "## 다음", "다음 거래일", "내일 전략")
    idx = -1
    for m in markers_strategy:
        i = text.find(m)
        if i != -1 and (idx == -1 or i < idx):
            idx = i
    if idx == -1:
        return text, ""
    review = text[:idx].strip()
    strategy = text[idx:].strip()
    for prefix in ("## 1)", "## 1", "## 오늘"):
        if review.startswith(prefix):
            review = review[len(prefix):].lstrip(" )\n")
            break
    for prefix in ("## 2)", "## 2", "## 다음"):
        if strategy.startswith(prefix):
            strategy = strategy[len(prefix):].lstrip(" )\n")
            break
    return review, strategy
