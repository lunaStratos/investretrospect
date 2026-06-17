"""매매일지 생성 오케스트레이션. CLI / GUI 양쪽에서 사용."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from invest_retrospect.analyzer import DailyJournalData, build_daily
from invest_retrospect.brokers import BrokerClient, make_client
from invest_retrospect.config import Config
from invest_retrospect.renderer import render, render_pdf
from invest_retrospect.types import AICommentary

KST = timezone(timedelta(hours=9))
LogFn = Callable[[str], None]


def today_ymd() -> str:
    return datetime.now(KST).strftime("%Y%m%d")


def ymd_dashed(ymd: str) -> str:
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"


@dataclass
class JournalResult:
    json_path: Path
    md_path: Path | None
    pdf_path: Path | None
    data: DailyJournalData
    commentary: AICommentary | None


def _fetch_all(client: BrokerClient, account_no: str, ymd: str, log: LogFn) -> dict[str, Any]:
    payloads: dict[str, Any] = {
        "broker": getattr(client.info, "id", "unknown"),
        "account_no": account_no,
        "ymd": ymd,
        "_errors": {},
    }

    def safe(name: str, fn):
        try:
            payloads[name] = fn()
        except Exception as e:  # noqa: BLE001
            log(f"[warn] {name} 조회 실패: {e}")
            payloads[name] = None
            payloads["_errors"][name] = str(e)

    safe("trades", lambda: client.trades(account_no, ymd))
    safe("realized_pl", lambda: client.realized_pl_per_stock(account_no, ymd))
    safe("balance", lambda: client.balance(account_no))
    safe("deposit", lambda: client.deposit(account_no))
    safe("journal", lambda: client.daily_journal(account_no, ymd))
    return payloads


def _build_from_payloads(payloads: dict[str, Any]) -> DailyJournalData:
    return build_daily(
        date=ymd_dashed(payloads["ymd"]),
        account_no=payloads["account_no"],
        trades_payload=payloads.get("trades") or {},
        pl_payload=payloads.get("realized_pl"),
        balance_payload=payloads.get("balance"),
        deposit_payload=payloads.get("deposit"),
        journal_payload=payloads.get("journal"),
    )


def _maybe_ai(cfg: Config, data: DailyJournalData, log: LogFn) -> AICommentary | None:
    if cfg.ai_provider == "none":
        log("[info] AI 코멘트 생략 (provider=none)")
        return None
    if cfg.ai_provider == "gemini" and not cfg.gemini_api_key:
        log("[info] GEMINI_API_KEY 미설정 — AI 코멘트 생략")
        return None
    try:
        from invest_retrospect.ai import generate_commentary
        log(f"[info] AI 코멘트 생성 중 ({cfg.ai_provider})...")
        return generate_commentary(data, cfg)
    except Exception as e:  # noqa: BLE001
        log(f"[warn] AI 호출 실패 ({cfg.ai_provider}): {e}")
        return None


def _write_outputs(
    out_base: Path,
    data: DailyJournalData,
    commentary: AICommentary | None,
    fmt: str,
) -> tuple[Path | None, Path | None]:
    md_path: Path | None = None
    pdf_path: Path | None = None
    if fmt in ("md", "both"):
        md_path = out_base.with_suffix(".md")
        md_path.write_text(render(data, commentary), encoding="utf-8")
    if fmt in ("pdf", "both"):
        pdf_path = render_pdf(data, commentary, out_base.with_suffix(".pdf"))
    return md_path, pdf_path


def run_journal(
    cfg: Config,
    ymd: str,
    account_no: str,
    fmt: str = "md",
    log: LogFn | None = None,
) -> JournalResult:
    """매매일지 생성. ymd 는 YYYYMMDD, fmt 는 'md'|'pdf'|'both'."""
    log = log or (lambda _msg: None)

    if not account_no.strip():
        raise RuntimeError("계좌번호가 비어있습니다.")
    if fmt not in ("md", "pdf", "both"):
        raise RuntimeError(f"알 수 없는 형식: {fmt}")
    if len(ymd) != 8 or not ymd.isdigit():
        raise RuntimeError(f"날짜 형식 오류: {ymd} (YYYYMMDD 필요)")

    safe_acc = account_no.replace("-", "")
    out_base = cfg.journal_dir / f"{ymd}_{cfg.broker.value}_{safe_acc}"
    out_json = cfg.journal_dir / f"{ymd}_{cfg.broker.value}_{safe_acc}.json"

    creds = cfg.active_creds
    log(f"[1/4] {cfg.broker.display_name} 인증 ({'모의' if cfg.is_mock else '운영'})...")
    client = make_client(
        cfg.broker,
        host=cfg.host,
        app_key=creds.app_key,
        secret_key=creds.secret_key,
        is_mock=cfg.is_mock,
        **creds.extra,
    )
    with client:
        log(f"[2/4] {ymd_dashed(ymd)} 데이터 조회...")
        payloads = _fetch_all(client, account_no, ymd, log)

    out_json.write_text(json.dumps(payloads, ensure_ascii=False, indent=2), encoding="utf-8")

    data = _build_from_payloads(payloads)
    log(f"  → 체결 {data.trade_count}건, 실현손익 {data.total_realized_pl:,}원")

    log("[3/4] AI 코멘트 처리...")
    commentary = _maybe_ai(cfg, data, log)

    log(f"[4/4] 출력 생성 ({fmt})...")
    md_path, pdf_path = _write_outputs(out_base, data, commentary, fmt)

    log("완료.")
    return JournalResult(
        json_path=out_json,
        md_path=md_path,
        pdf_path=pdf_path,
        data=data,
        commentary=commentary,
    )
