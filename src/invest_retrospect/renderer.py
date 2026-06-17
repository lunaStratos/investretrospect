"""DailyJournalData → Markdown / PDF 일지 변환."""

from __future__ import annotations

from pathlib import Path

from invest_retrospect.analyzer import DailyJournalData
from invest_retrospect.types import AICommentary


def _money(v: int) -> str:
    sign = "+" if v > 0 else ""
    return f"{sign}{v:,}"


def _pct(v: float) -> str:
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.2f}%"


def _format_time(hms: str) -> str:
    s = hms.zfill(6)
    if len(s) >= 6 and s.isdigit():
        return f"{s[0:2]}:{s[2:4]}:{s[4:6]}"
    return hms or "-"


def _ccy_suffix(ccy: str) -> str:
    """통화별 금액 접미사: KRW 는 '원', 그 외는 ' USD' 식."""
    return "원" if ccy == "KRW" else f" {ccy}"


def _summary_lines(data: DailyJournalData) -> list[str]:
    """헤더 요약. 단일 통화면 기존 출력과 동일, 다중 통화면 통화별로 분리."""
    tbc = data.totals_by_currency()
    multi = len(tbc) > 1
    out: list[str] = [
        f"- 계좌번호: `{data.account_no}`",
        f"- 체결 건수: **{data.trade_count}건**",
    ]
    if not multi:
        ccy = next(iter(tbc))
        t = tbc[ccy]
        sfx = _ccy_suffix(ccy)
        turnover = t["buy_amt"] + t["sell_amt"]
        out.append(f"- 회전금액: **{turnover:,}{sfx}** (매수 {t['buy_amt']:,} / 매도 {t['sell_amt']:,})")
        out.append(f"- 실현손익: **{_money(t['realized_pl'])}{sfx}**")
        out.append(f"- 승/패: **{data.win_count}승 {data.lose_count}패** (승률 {data.win_rate:.1f}%)")
        out.append(f"- 평가금액: **{t['eval_amt']:,}{sfx}**, 평가손익 {_money(t['eval_pl'])}{sfx}")
        out.append(f"- 예수금: **{data.deposit:,}원**")
    else:
        out.append(f"- 승/패: **{data.win_count}승 {data.lose_count}패** (승률 {data.win_rate:.1f}%)")
        for ccy, t in tbc.items():
            sfx = _ccy_suffix(ccy)
            turnover = t["buy_amt"] + t["sell_amt"]
            out.append(
                f"- **[{ccy}]** 회전 {turnover:,}{sfx} · 실현손익 **{_money(t['realized_pl'])}{sfx}** · "
                f"평가 {t['eval_amt']:,}{sfx} (평가손익 {_money(t['eval_pl'])}{sfx})"
            )
        if data.deposit:
            out.append(f"- 예수금: **{data.deposit:,}원**")
    fx = (data.raw or {}).get("fx") or {}
    for ccy, rate in fx.items():
        out.append(f"- 환율(참고): {ccy}/KRW {rate:,.2f}")
    return out


def _mermaid_label(name: str) -> str:
    """mermaid pie 라벨용: 큰따옴표/개행 제거."""
    return (name or "-").replace('"', "").replace("\n", " ").strip() or "-"


def _render_pie_md(data: DailyJournalData) -> list[str]:
    """포트폴리오 비중 — 통화별 Mermaid pie 블록. 보유가 없으면 빈 리스트."""
    groups = data.holding_weights_by_currency()
    if not groups:
        return []
    multi = len(groups) > 1
    out: list[str] = ["## 포트폴리오 비중", ""]
    for ccy, weights in groups.items():
        title = f"포트폴리오 비중 ({ccy})" if (multi or ccy != "KRW") else "포트폴리오 비중"
        out.append("```mermaid")
        out.append(f"pie showData title {title}")
        for name, value, _pct_v in weights:
            out.append(f'    "{_mermaid_label(name)}" : {value}')
        out.append("```")
        out.append("")
    return out


def render(data: DailyJournalData, ai: AICommentary | None = None) -> str:
    p: list[str] = []
    multi_ccy = len(data.totals_by_currency()) > 1

    p.append(f"# 매매일지 — {data.date}")
    p.append("")
    p.extend(_summary_lines(data))
    p.append("")

    p.append("## 종목별 실현손익")
    if data.stock_pls:
        p.append("")
        ch = " 통화 |" if multi_ccy else ""
        cs = "------|" if multi_ccy else ""
        p.append(f"| 종목 | 코드 |{ch} 매수금액 | 매도금액 | 실현손익 | 수익률 |")
        p.append(f"|------|------|{cs}---------:|---------:|---------:|-------:|")
        for s in data.stock_pls:
            cc = f" {s.currency} |" if multi_ccy else ""
            p.append(
                f"| {s.stk_nm} | {s.stk_cd} |{cc} {s.buy_amt:,} | {s.sell_amt:,} | "
                f"**{_money(s.realized_pl)}** | {_pct(s.return_rate)} |"
            )
    else:
        p.append("")
        p.append("_데이터 없음_")
    p.append("")

    p.append("## 보유 종목 (장 마감 기준)")
    if data.holdings:
        p.append("")
        ch = " 통화 |" if multi_ccy else ""
        cs = "------|" if multi_ccy else ""
        p.append(f"| 종목 | 코드 |{ch} 수량 | 평단가 | 현재가 | 평가금액 | 평가손익 | 수익률 |")
        p.append(f"|------|------|{cs}----:|------:|------:|---------:|---------:|-------:|")
        for h in data.holdings:
            cc = f" {h.currency} |" if multi_ccy else ""
            p.append(
                f"| {h.stk_nm} | {h.stk_cd} |{cc} {h.qty:,} | {h.avg_price:,} | "
                f"{h.cur_price:,} | {h.eval_amt:,} | **{_money(h.pl_amt)}** | {_pct(h.return_rate)} |"
            )
    else:
        p.append("")
        p.append("_보유 종목 없음_")
    p.append("")

    p.extend(_render_pie_md(data))

    p.append("## 체결 내역")
    if data.trades:
        p.append("")
        ch = " 통화 |" if multi_ccy else ""
        cs = "------|" if multi_ccy else ""
        p.append(f"| 시간 | 종목 | 코드 | 구분 |{ch} 체결수량 | 체결단가 | 체결금액 | 거래소 |")
        p.append(f"|------|------|------|------|{cs}--------:|--------:|---------:|--------|")
        for t in data.trades:
            cc = f" {t.currency} |" if multi_ccy else ""
            p.append(
                f"| {_format_time(t.cntr_time)} | {t.stk_nm} | {t.stk_cd} | "
                f"{t.side} |{cc} {t.cntr_qty:,} | {t.cntr_price:,} | {t.amount:,} | {t.venue or '-'} |"
            )
    else:
        p.append("")
        p.append("_체결 내역 없음_")
    p.append("")

    if ai:
        p.append("## AI 매매 리뷰")
        p.append("")
        p.append(ai.review or "_생성 실패_")
        p.append("")
        p.append("## 다음 거래일 전략")
        p.append("")
        p.append(ai.strategy or "_생성 실패_")
        p.append("")

    if ai and (ai.provider or ai.model):
        tag = " / ".join(x for x in (ai.provider, ai.model) if x)
        p.append(f"_AI: {tag}_")
        p.append("")

    p.append("---")
    p.append("")
    p.append(f"_생성 시각: {data.date} / invest-retrospect_")
    return "\n".join(p)


_PDF_BODY_FONT = "HYSMyeongJo-Medium"   # CID 한글 명조 (뷰어 내장)
_PDF_HEAD_FONT = "HYGothic-Medium"      # CID 한글 고딕 (뷰어 내장)
_PDF_FONTS_REGISTERED = False


def _ensure_pdf_fonts() -> None:
    global _PDF_FONTS_REGISTERED
    if _PDF_FONTS_REGISTERED:
        return
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    pdfmetrics.registerFont(UnicodeCIDFont(_PDF_BODY_FONT))
    pdfmetrics.registerFont(UnicodeCIDFont(_PDF_HEAD_FONT))
    _PDF_FONTS_REGISTERED = True


def _pdf_escape(text: str) -> str:
    """reportlab Paragraph 마크업 이스케이프."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_pdf(data: DailyJournalData, ai: AICommentary | None, out_path: Path) -> Path:
    """DailyJournalData → PDF. reportlab + CID 한글 폰트 사용 (시스템 의존성 없음)."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as e:
        raise RuntimeError(
            "PDF 생성에는 reportlab 이 필요합니다: pip install 'invest-retrospect[pdf]'"
        ) from e

    _ensure_pdf_fonts()

    h1 = ParagraphStyle("h1", fontName=_PDF_HEAD_FONT, fontSize=18,
                        spaceAfter=8, textColor=colors.HexColor("#111111"))
    h2 = ParagraphStyle("h2", fontName=_PDF_HEAD_FONT, fontSize=13,
                        spaceBefore=14, spaceAfter=6,
                        textColor=colors.HexColor("#222222"))
    body = ParagraphStyle("body", fontName=_PDF_BODY_FONT, fontSize=10,
                          leading=14, spaceAfter=2)
    small = ParagraphStyle("small", fontName=_PDF_BODY_FONT, fontSize=8,
                           leading=10, textColor=colors.HexColor("#666666"))

    def b(text: str) -> str:
        return f"<b>{_pdf_escape(text)}</b>"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=16 * mm, rightMargin=16 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"매매일지 {data.date}", author="invest-retrospect",
    )
    story: list = []

    tbc = data.totals_by_currency()
    multi_ccy = len(tbc) > 1

    story.append(Paragraph(f"매매일지 — {_pdf_escape(data.date)}", h1))
    header_lines = [
        f"계좌번호: {_pdf_escape(data.account_no or '-')}",
        f"체결 건수: {b(f'{data.trade_count}건')}",
        f"승/패: {b(f'{data.win_count}승 {data.lose_count}패')} (승률 {data.win_rate:.1f}%)",
    ]
    for ccy, t in tbc.items():
        sfx = _ccy_suffix(ccy)
        turnover = t["buy_amt"] + t["sell_amt"]
        tag = f"[{ccy}] " if multi_ccy else ""
        buy_amt, sell_amt = t["buy_amt"], t["sell_amt"]
        eval_amt, eval_pl, realized = t["eval_amt"], t["eval_pl"], t["realized_pl"]
        header_lines.append(
            f"{tag}회전금액: {b(f'{turnover:,}{sfx}')} (매수 {buy_amt:,} / 매도 {sell_amt:,})"
        )
        header_lines.append(f"{tag}실현손익: {b(_money(realized) + sfx)}")
        header_lines.append(
            f"{tag}평가금액: {b(f'{eval_amt:,}{sfx}')}, 평가손익 {_money(eval_pl)}{sfx}"
        )
    if not multi_ccy:
        header_lines.append(f"예수금: {b(f'{data.deposit:,}원')}")
    for ccy, rate in ((data.raw or {}).get("fx") or {}).items():
        header_lines.append(f"환율(참고): {ccy}/KRW {rate:,.2f}")
    for line in header_lines:
        story.append(Paragraph(f"• {line}", body))

    story.append(Paragraph("종목별 실현손익", h2))
    if data.stock_pls:
        head = ["종목", "코드"] + (["통화"] if multi_ccy else []) + ["매수", "매도", "실현손익", "수익률"]
        rows = [head]
        for s in data.stock_pls:
            rows.append(
                [s.stk_nm, s.stk_cd] + ([s.currency] if multi_ccy else [])
                + [f"{s.buy_amt:,}", f"{s.sell_amt:,}", _money(s.realized_pl), _pct(s.return_rate)]
            )
        base = 2 + (1 if multi_ccy else 0)
        story.append(_pdf_table(rows, num_cols=tuple(range(base, base + 4))))
    else:
        story.append(Paragraph("<i>데이터 없음</i>", body))

    story.append(Paragraph("보유 종목 (장 마감 기준)", h2))
    if data.holdings:
        head = ["종목", "코드"] + (["통화"] if multi_ccy else []) + ["수량", "평단", "현재가", "평가금액", "평가손익", "수익률"]
        rows = [head]
        for h in data.holdings:
            rows.append(
                [h.stk_nm, h.stk_cd] + ([h.currency] if multi_ccy else [])
                + [f"{h.qty:,}", f"{h.avg_price:,}", f"{h.cur_price:,}", f"{h.eval_amt:,}",
                   _money(h.pl_amt), _pct(h.return_rate)]
            )
        base = 2 + (1 if multi_ccy else 0)
        story.append(_pdf_table(rows, num_cols=tuple(range(base, base + 6))))
    else:
        story.append(Paragraph("<i>보유 종목 없음</i>", body))

    # 포트폴리오 비중 — 통화별 파이차트
    groups = data.holding_weights_by_currency()
    if groups:
        story.append(Paragraph("포트폴리오 비중", h2))
        multi = len(groups) > 1
        for ccy, weights in groups.items():
            if multi or ccy != "KRW":
                story.append(Paragraph(f"통화: {ccy}", body))
            story.append(_pdf_pie(weights))

    story.append(Paragraph("체결 내역", h2))
    if data.trades:
        head = ["시간", "종목", "코드", "구분"] + (["통화"] if multi_ccy else []) + ["수량", "단가", "금액", "거래소"]
        rows = [head]
        for t in data.trades:
            rows.append(
                [_format_time(t.cntr_time), t.stk_nm, t.stk_cd, t.side]
                + ([t.currency] if multi_ccy else [])
                + [f"{t.cntr_qty:,}", f"{t.cntr_price:,}", f"{t.amount:,}", t.venue or "-"]
            )
        base = 4 + (1 if multi_ccy else 0)
        story.append(_pdf_table(rows, num_cols=tuple(range(base, base + 3))))
    else:
        story.append(Paragraph("<i>체결 내역 없음</i>", body))

    if ai:
        story.append(Paragraph("AI 매매 리뷰", h2))
        for line in (ai.review or "_생성 실패_").splitlines():
            if line.strip():
                story.append(Paragraph(_pdf_escape(line), body))
        story.append(Paragraph("다음 거래일 전략", h2))
        for line in (ai.strategy or "_생성 실패_").splitlines():
            if line.strip():
                story.append(Paragraph(_pdf_escape(line), body))

    story.append(Spacer(1, 12))
    footer_bits = [f"생성: {data.date} / invest-retrospect"]
    if ai and (ai.provider or ai.model):
        footer_bits.append("AI: " + " / ".join(x for x in (ai.provider, ai.model) if x))
    story.append(Paragraph(" · ".join(footer_bits), small))

    doc.build(story)
    return out_path


_PIE_COLORS = (
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
    "#edc948", "#b07aa1", "#ff9da7", "#9c755f",
)


def _pdf_pie(weights: list[tuple[str, int, float]]):
    """포트폴리오 비중 파이 + 범례 Drawing. weights: [(label, value, pct), ...]."""
    from reportlab.graphics.charts.legends import Legend
    from reportlab.graphics.charts.piecharts import Pie
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib import colors

    d = Drawing(440, 150)
    pie = Pie()
    pie.x, pie.y = 10, 10
    pie.width = pie.height = 130
    pie.data = [max(0.0001, float(v)) for _n, v, _p in weights]
    pie.labels = [f"{p:.1f}%" for _n, _v, p in weights]
    pie.sideLabels = False
    pie.slices.strokeWidth = 0.5
    pie.slices.fontName = _PDF_BODY_FONT
    pie.slices.fontSize = 7
    palette = [colors.HexColor(_PIE_COLORS[i % len(_PIE_COLORS)]) for i in range(len(weights))]
    for i, c in enumerate(palette):
        pie.slices[i].fillColor = c
    d.add(pie)

    legend = Legend()
    legend.x, legend.y = 165, 135
    legend.dx = legend.dy = 7
    legend.fontName = _PDF_BODY_FONT
    legend.fontSize = 8
    legend.deltay = 12
    legend.columnMaximum = len(weights)
    legend.colorNamePairs = [
        (palette[i], f"{n} ({p:.1f}%)") for i, (n, _v, p) in enumerate(weights)
    ]
    d.add(legend)
    return d


def _pdf_table(rows: list[list[str]], num_cols: tuple[int, ...] = ()):
    """공통 표 스타일. num_cols 에 지정된 컬럼은 우측 정렬."""
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    table = Table(rows, repeatRows=1)
    cmds = [
        ("FONT", (0, 0), (-1, -1), _PDF_BODY_FONT, 8.5),
        ("FONT", (0, 0), (-1, 0), _PDF_HEAD_FONT, 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for c in num_cols:
        cmds.append(("ALIGN", (c, 1), (c, -1), "RIGHT"))
    table.setStyle(TableStyle(cmds))
    return table
