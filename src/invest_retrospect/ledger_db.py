"""수동 원장 DB 모드 저장소.

오프라인 모드가 `~/.invest-retrospect/manual_ledger.json` 한 파일에 전체 계좌
묶음을 저장하는 데 비해, DB 모드는 매매 항목(행)을 외부 DB(MariaDB/MySQL 또는
PostgreSQL)의 **테이블 하나**에 정규화해 저장한다. 행 한 줄 = 매수/매도 1건이며
`account` 컬럼으로 다계좌를 구분한다. 여러 PC 가 같은 테이블을 공유할 수 있다.

활성 계좌·계좌 순서·수동 현재가 같은 UI 메타데이터는 DB 테이블이 아니라 로컬
보조 파일(`MANUAL_LEDGER_DBAUX_PATH`)에 둔다 — 사용자가 요청한 "테이블 하나"
구성을 지키면서도 빈 계좌·탭 순서를 보존하기 위함이다.

SQLAlchemy 와 DB 드라이버(pymysql / psycopg)는 DB 모드를 실제로 쓸 때만 필요하다.
미설치 시 친절한 한국어 안내와 함께 RuntimeError 를 올린다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from invest_retrospect.manual import (
    DEFAULT_ACCOUNT,
    Ledger,
    LedgerBook,
    LedgerEntry,
)
from invest_retrospect.settings_store import MANUAL_LEDGER_DBAUX_PATH, SETTINGS_DIR

if TYPE_CHECKING:  # 런타임 import 회피 (순환·선택적 의존성)
    from invest_retrospect.settings_store import Settings

VALID_KINDS = ("mysql", "postgresql")
_DEFAULT_PORTS = {"mysql": 3306, "postgresql": 5432}
_DRIVERNAMES = {"mysql": "mysql+pymysql", "postgresql": "postgresql+psycopg"}
CONNECT_TIMEOUT_SEC = 6


@dataclass(frozen=True)
class DbSettings:
    """수동 원장 DB 접속 정보."""

    kind: str          # 'mysql' | 'postgresql'
    host: str          # 'host' 또는 'host:port'
    name: str          # 데이터베이스 이름
    user: str
    password: str
    table: str = "manual_ledger"
    ssl: bool = True   # SSL/TLS 사용 (Neon 등 매니지드 DB 는 필수)

    @classmethod
    def from_settings(cls, s: "Settings") -> "DbSettings":
        kind = (s.manual_db_kind or "mysql").strip().lower()
        if kind not in VALID_KINDS:
            kind = "mysql"
        return cls(
            kind=kind,
            host=(s.manual_db_host or "").strip(),
            name=(s.manual_db_name or "").strip(),
            user=(s.manual_db_user or "").strip(),
            password=s.manual_db_password or "",
            table=(s.manual_db_table or "manual_ledger").strip() or "manual_ledger",
            ssl=(s.manual_db_ssl or "").strip().lower() in ("1", "true", "on", "yes", "require"),
        )

    def validate(self) -> None:
        """필수 항목 누락 시 RuntimeError. 접속 시도 전에 호출."""
        missing = []
        if not self.host:
            missing.append("호스트")
        if not self.name:
            missing.append("DB 이름")
        if not self.user:
            missing.append("사용자")
        if not self.table:
            missing.append("테이블")
        if missing:
            raise RuntimeError(
                "DB 모드 설정이 비어 있습니다: " + ", ".join(missing)
                + " — [설정] 탭에서 입력하세요."
            )

    def _host_port(self) -> tuple[str, int]:
        host, port = self.host, _DEFAULT_PORTS[self.kind]
        if ":" in self.host:
            h, _, p = self.host.rpartition(":")
            if h and p.isdigit():
                host, port = h, int(p)
        return host, port

    def url(self) -> Any:
        """SQLAlchemy URL 객체 (비밀번호 특수문자 자동 이스케이프)."""
        from sqlalchemy import URL

        host, port = self._host_port()
        if self.kind == "mysql":
            query = {"charset": "utf8mb4"}            # MySQL SSL 은 connect_args 로 처리
        else:  # postgresql — libpq sslmode 로 SSL 강제
            query = {"sslmode": "require"} if self.ssl else {}
        return URL.create(
            _DRIVERNAMES[self.kind],
            username=self.user,
            password=self.password,
            host=host,
            port=port,
            database=self.name,
            query=query,
        )

    def key(self) -> tuple:
        """접속 대상이 바뀌었는지 비교용 (비밀번호 제외)."""
        return (self.kind, self.host, self.name, self.user, self.table, self.ssl)

    def connect_args(self) -> dict[str, Any]:
        """드라이버별 추가 접속 인자 (timeout + MySQL SSL 컨텍스트)."""
        args: dict[str, Any] = {"connect_timeout": CONNECT_TIMEOUT_SEC}
        if self.kind == "mysql" and self.ssl:
            import ssl as _ssl
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False        # 호스트/인증서 검증 없이 전송 암호화만
            ctx.verify_mode = _ssl.CERT_NONE
            args["ssl"] = ctx                 # pymysql 은 SSLContext 를 그대로 수용
        return args


def _require_sqlalchemy():
    try:
        import sqlalchemy  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "DB 모드에는 SQLAlchemy 가 필요합니다. "
            "`pip install sqlalchemy pymysql 'psycopg[binary]'` 후 다시 시도하세요."
        ) from e


def _require_driver(kind: str) -> None:
    mod, hint = (
        ("pymysql", "pymysql") if kind == "mysql" else ("psycopg", "'psycopg[binary]'")
    )
    try:
        __import__(mod)
    except ImportError as e:
        raise RuntimeError(
            f"{kind} 드라이버({mod})가 설치돼 있지 않습니다. "
            f"`pip install {hint}` 후 다시 시도하세요."
        ) from e


_engines: dict[Any, Any] = {}


def _engine(db: DbSettings):
    """접속 엔진 (URL 단위 캐시). 짧은 connect timeout 으로 GUI 멈춤 방지."""
    _require_sqlalchemy()
    _require_driver(db.kind)
    from sqlalchemy import create_engine

    url = db.url()
    cache_key = url.render_as_string(hide_password=False)
    eng = _engines.get(cache_key)
    if eng is None:
        eng = create_engine(
            url,
            pool_pre_ping=True,
            connect_args=db.connect_args(),
        )
        _engines[cache_key] = eng
    return eng


def _table(db: DbSettings, meta):
    from sqlalchemy import (
        BigInteger,
        Column,
        Double,
        Integer,
        String,
        Table,
    )

    return Table(
        db.table,
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("account", String(255), nullable=False),
        Column("seq", Integer, nullable=False),         # 계좌 내 입력 순서 보존
        Column("trade_date", String(8), nullable=False),  # YYYYMMDD
        Column("stk_cd", String(64)),
        Column("stk_nm", String(255)),
        Column("side", String(8)),                      # '매수' | '매도'
        Column("qty", BigInteger),
        Column("price", Double),                         # 배정밀도(원화 큰 단가·해외 소수)
        Column("market", String(32)),
        Column("tag", String(255)),
    )


def _ensure(db: DbSettings):
    """엔진과 테이블을 준비하고 (engine, table) 반환. 테이블 없으면 생성."""
    from sqlalchemy import MetaData

    eng = _engine(db)
    meta = MetaData()
    tbl = _table(db, meta)
    meta.create_all(eng)   # 없을 때만 생성 (기존 테이블은 건드리지 않음)
    return eng, tbl


# ── 보조 메타데이터(로컬) ─────────────────────────────────────────────────────
def _load_aux() -> dict[str, Any]:
    if not MANUAL_LEDGER_DBAUX_PATH.is_file():
        return {}
    try:
        data = json.loads(MANUAL_LEDGER_DBAUX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_aux(book: LedgerBook) -> None:
    aux = {
        "active": book.active,
        "accounts": list(book.accounts.keys()),   # 탭 순서 + 빈 계좌 보존
        "prices": {n: led.prices for n, led in book.accounts.items()},
    }
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    MANUAL_LEDGER_DBAUX_PATH.write_text(
        json.dumps(aux, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── 로드 / 저장 ───────────────────────────────────────────────────────────────
def load_book(db: DbSettings) -> LedgerBook:
    """DB 테이블 + 로컬 보조파일에서 전체 계좌 묶음을 복원한다."""
    db.validate()
    from sqlalchemy import select

    eng, tbl = _ensure(db)
    with eng.connect() as conn:
        rows = conn.execute(
            select(tbl).order_by(tbl.c.account, tbl.c.seq, tbl.c.id)
        ).mappings().all()

    aux = _load_aux()
    aux_order = [str(n) for n in (aux.get("accounts") or []) if isinstance(n, str)]
    aux_prices = aux.get("prices") if isinstance(aux.get("prices"), dict) else {}

    # 계좌 순서: 보조파일 순서를 우선하고, DB 에만 있는 계좌는 뒤에 덧붙인다.
    accounts: dict[str, Ledger] = {n: Ledger() for n in aux_order}
    for r in rows:
        acct = str(r["account"] or DEFAULT_ACCOUNT)
        led = accounts.setdefault(acct, Ledger())
        led.entries.append(LedgerEntry.from_dict({
            "date": r["trade_date"], "stk_cd": r["stk_cd"], "stk_nm": r["stk_nm"],
            "side": r["side"], "qty": r["qty"], "price": r["price"],
            "market": r["market"], "tag": r["tag"],
        }))
    if not accounts:
        accounts = {DEFAULT_ACCOUNT: Ledger()}

    # 수동 현재가(폴백) 복원
    for name, led in accounts.items():
        pm = aux_prices.get(name)
        if isinstance(pm, dict):
            led.prices = {str(k): float(v) for k, v in pm.items() if _is_num(v)}

    active = str(aux.get("active") or "")
    if active not in accounts:
        active = next(iter(accounts))
    return LedgerBook(accounts=accounts, active=active)


def save_book(book: LedgerBook, db: DbSettings) -> None:
    """전체 계좌 묶음을 DB 테이블에 저장(전체 교체) + 보조파일 갱신.

    한 트랜잭션에서 기존 행을 모두 지우고 현재 항목을 다시 삽입한다(오프라인
    JSON 의 '파일 통째 교체' 와 동일한 의미). 테이블은 이 기능 전용으로 가정한다.
    """
    db.validate()
    from sqlalchemy import delete, insert

    eng, tbl = _ensure(db)
    rows: list[dict[str, Any]] = []
    for acct, led in book.accounts.items():
        for i, e in enumerate(led.entries):
            rows.append({
                "account": acct, "seq": i, "trade_date": e.date,
                "stk_cd": e.stk_cd, "stk_nm": e.stk_nm, "side": e.side,
                "qty": e.qty, "price": e.price, "market": e.market, "tag": e.tag,
            })
    with eng.begin() as conn:
        conn.execute(delete(tbl))
        if rows:
            conn.execute(insert(tbl), rows)
    _save_aux(book)


def test_connection(db: DbSettings) -> str:
    """접속을 시험하고, 테이블이 없으면 새로 생성한다. 결과 문자열 반환(실패 시 예외).

    설정 화면의 '연결 테스트' 가 호출 — 이 시점에 테이블이 자동 생성된다.
    """
    db.validate()
    from sqlalchemy import func, inspect, select

    eng = _engine(db)
    existed = db.table in inspect(eng).get_table_names()   # 접속 + 존재 확인
    eng, tbl = _ensure(db)                                 # 없으면 생성
    with eng.connect() as conn:
        n = conn.execute(select(func.count()).select_from(tbl)).scalar_one()
    host, port = db._host_port()
    label = "MariaDB/MySQL" if db.kind == "mysql" else "PostgreSQL"
    state = "기존 테이블 사용" if existed else "테이블을 새로 생성했습니다"
    return (f"{label} {host}:{port}/{db.name} 접속 성공\n"
            f"· '{db.table}' {state} (현재 {n}행)")


def _is_num(v: Any) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


__all__ = ["DbSettings", "load_book", "save_book", "test_connection", "VALID_KINDS"]
