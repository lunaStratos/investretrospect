"""증권사별 일일 매매일지 CLI.

사용 예:
  invest-retrospect journal --account 8012345611                    # 오늘자 MD (기본 broker)
  invest-retrospect journal --broker kis --account 12345678-01      # 한국투자증권
  invest-retrospect journal --broker ls  --account 1234567890       # LS증권
  invest-retrospect journal --date 20260514 --account ...           # 특정일
  invest-retrospect journal --format pdf --account ...              # PDF 만
  invest-retrospect journal --format both --account ...             # MD + PDF
  invest-retrospect journal --provider ollama --account ...         # Ollama 사용
  invest-retrospect journal --no-ai --account ...                   # AI 코멘트 생략
  invest-retrospect dump --date 20260514 --account ...              # API 응답을 JSON으로만
  invest-retrospect render path/to/dump.json                        # 저장된 JSON으로 재생성
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from invest_retrospect.brokers import Broker, make_client
from invest_retrospect.config import Config, load_config
from invest_retrospect.core import (
    _build_from_payloads,
    _fetch_all,
    _maybe_ai,
    _write_outputs,
    run_journal,
    today_ymd,
)


def _stderr_log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _apply_overrides(
    cfg: Config,
    *,
    provider: str | None,
    no_ai: bool,
) -> Config:
    if no_ai:
        return replace(cfg, ai_provider="none")
    if provider:
        return replace(cfg, ai_provider=provider)
    return cfg


def _resolve_account(args: argparse.Namespace, cfg: Config) -> str:
    return (args.account or cfg.account_no or "").strip()


def cmd_journal(args: argparse.Namespace, cfg: Config) -> int:
    cfg = _apply_overrides(cfg, provider=args.provider, no_ai=args.no_ai)
    ymd = args.date or today_ymd()
    account_no = _resolve_account(args, cfg)
    if not account_no:
        _stderr_log(
            f"[error] --account 또는 .env {cfg.broker.value.upper()}_ACCOUNT_NO 로 "
            f"계좌번호를 지정해주세요."
        )
        return 2
    try:
        result = run_journal(cfg, ymd, account_no, args.format, log=_stderr_log)
    except RuntimeError as e:
        _stderr_log(f"[error] {e}")
        return 1
    if result.md_path:
        print(f"[ok] 매매일지: {result.md_path}")
    if result.pdf_path:
        print(f"[ok] 매매일지: {result.pdf_path}")
    print(f"[ok] 원본 JSON: {result.json_path}")
    return 0


def cmd_dump(args: argparse.Namespace, cfg: Config) -> int:
    ymd = args.date or today_ymd()
    account_no = _resolve_account(args, cfg)
    if not account_no:
        _stderr_log("[error] --account 필요")
        return 2

    safe_acc = account_no.replace("-", "")
    out_json = cfg.journal_dir / f"{ymd}_{cfg.broker.value}_{safe_acc}.json"
    creds = cfg.active_creds
    client = make_client(
        cfg.broker,
        host=cfg.host,
        app_key=creds.app_key,
        secret_key=creds.secret_key,
        is_mock=cfg.is_mock,
        **creds.extra,
    )
    with client:
        payloads = _fetch_all(client, account_no, ymd, _stderr_log)
    out_json.write_text(json.dumps(payloads, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] {out_json}")
    return 0


def cmd_render(args: argparse.Namespace, cfg: Config) -> int:
    cfg = _apply_overrides(cfg, provider=args.provider, no_ai=args.no_ai)
    src = Path(args.input).expanduser().resolve()
    if not src.is_file():
        _stderr_log(f"[error] 파일을 찾을 수 없음: {src}")
        return 2
    payloads = json.loads(src.read_text(encoding="utf-8"))
    data = _build_from_payloads(payloads)
    ai = _maybe_ai(cfg, data, _stderr_log)
    out_base = Path(args.output).with_suffix("") if args.output else src.with_suffix("")
    try:
        md_path, pdf_path = _write_outputs(out_base, data, ai, args.format)
    except RuntimeError as e:
        _stderr_log(f"[error] {e}")
        return 1
    for path in (md_path, pdf_path):
        if path:
            print(f"[ok] {path}")
    return 0


def cmd_manual(args: argparse.Namespace, cfg: Config) -> int:
    """수동 원장 → 매매일지. cfg 는 main() 에서 BROKER=manual 로 구성됨."""
    from invest_retrospect.manual import run_manual_journal
    cfg = _apply_overrides(cfg, provider=args.provider, no_ai=args.no_ai)
    ymd = args.date or today_ymd()
    try:
        result = run_manual_journal(
            cfg, ymd, args.format, do_fetch=not args.no_fetch, log=_stderr_log
        )
    except RuntimeError as e:
        _stderr_log(f"[error] {e}")
        return 1
    if result.md_path:
        print(f"[ok] 매매일지: {result.md_path}")
    if result.pdf_path:
        print(f"[ok] 매매일지: {result.pdf_path}")
    print(f"[ok] 원장 스냅샷: {result.json_path}")
    return 0


def _add_ai_args(sub: argparse.ArgumentParser) -> None:
    sub.add_argument(
        "--provider", choices=["gemini", "ollama", "none"], default=None,
        help="AI 코멘트 제공자 (기본: .env AI_PROVIDER)"
    )
    sub.add_argument("--no-ai", action="store_true", help="AI 코멘트 생략 (= --provider none)")


def _add_format_arg(sub: argparse.ArgumentParser) -> None:
    sub.add_argument(
        "--format", choices=["md", "pdf", "both"], default="md",
        help="출력 형식 (기본: md). pdf 는 reportlab 으로 생성."
    )


def _add_broker_args(sub: argparse.ArgumentParser) -> None:
    sub.add_argument(
        "--broker", choices=[b.value for b in Broker if b is not Broker.MANUAL], default=None,
        help="증권사 선택 (기본: .env BROKER)"
    )
    sub.add_argument(
        "--env", choices=["mock", "prod"], default=None,
        help="환경 (기본: .env <BROKER>_ENV)"
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="invest-retrospect",
        description="증권사별 일일 매매일지 생성기 (키움/한투/LS)",
    )
    sp = p.add_subparsers(dest="command", required=True)

    j = sp.add_parser("journal", help="API 조회 → JSON 저장 → 일지 생성")
    j.add_argument("--date", help="조회 일자 YYYYMMDD (기본: 오늘 KST)")
    j.add_argument("--account", required=False, help="계좌번호 (기본: .env <BROKER>_ACCOUNT_NO)")
    _add_broker_args(j)
    _add_format_arg(j)
    _add_ai_args(j)
    j.set_defaults(func=cmd_journal)

    d = sp.add_parser("dump", help="API 응답을 JSON으로만 저장 (디버깅용)")
    d.add_argument("--date", help="조회 일자 YYYYMMDD")
    d.add_argument("--account", required=False)
    _add_broker_args(d)
    d.set_defaults(func=cmd_dump)

    r = sp.add_parser("render", help="저장된 JSON으로 일지 재생성")
    r.add_argument("input", help="입력 JSON 경로")
    r.add_argument("--output", help="출력 경로 (확장자 제외, 기본: 입력 파일명)")
    _add_format_arg(r)
    _add_ai_args(r)
    r.set_defaults(func=cmd_render)

    m = sp.add_parser("manual", help="수동 원장(~/.invest-retrospect/manual_ledger.json) → 일지")
    m.add_argument("--date", help="기준 일자 YYYYMMDD (기본: 오늘 KST)")
    m.add_argument(
        "--price-api", choices=["yahoo", "kiwoom", "kis"], default=None,
        help="국내 시세 조회 API (기본: .env MANUAL_DOMESTIC_API). 해외는 항상 yahoo"
    )
    m.add_argument("--no-fetch", action="store_true", help="자동 시세 조회 생략 (수동값/원가 사용)")
    _add_format_arg(m)
    _add_ai_args(m)
    m.set_defaults(func=cmd_manual)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    # --broker/--env 는 .env 보다 우선. load_config 가 이를 보고 active broker
    # 의 키와 ACCOUNT_NO 를 올바르게 잡는다.
    if args.command == "manual":
        os.environ["BROKER"] = "manual"
        if getattr(args, "price_api", None):
            os.environ["MANUAL_DOMESTIC_API"] = args.price_api
    if getattr(args, "broker", None):
        os.environ["BROKER"] = args.broker
    if getattr(args, "env", None):
        os.environ["BROKER_ENV"] = args.env
    try:
        cfg = load_config()
    except RuntimeError as e:
        _stderr_log(f"[error] {e}")
        sys.exit(2)
    rc = args.func(args, cfg)
    sys.exit(rc)


if __name__ == "__main__":
    main()
