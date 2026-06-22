"""GUI 가 사용하는 설정 영구 저장소.

CLI 가 사용하는 .env 와는 분리된 경로 (~/.invest-retrospect/settings.json) 에 저장한다.
GUI 에서 broker 별 키를 모두 입력해두면 이후 실행 시 자동 로드된다.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from invest_retrospect.brokers import Broker, parse_broker
from invest_retrospect.config import BrokerCreds, Config

SETTINGS_DIR = Path.home() / ".invest-retrospect"
SETTINGS_PATH = SETTINGS_DIR / "settings.json"
# 수동 원장(매수/매도 변화 + 수동 현재가) 저장 경로
MANUAL_LEDGER_PATH = SETTINGS_DIR / "manual_ledger.json"
# DB 모드 보조 상태(활성 계좌·계좌 순서·수동 현재가) 로컬 저장 경로.
# 매매 항목 자체는 외부 DB 테이블에 두되, UI 메타데이터는 로컬에 둔다.
MANUAL_LEDGER_DBAUX_PATH = SETTINGS_DIR / "manual_ledger_dbaux.json"

# 구버전(KiwoomToday) 설정 경로. 최초 실행 시 새 경로로 1회 자동 마이그레이션한다.
_LEGACY_SETTINGS_DIR = Path.home() / ".kiwoom-today"
_LEGACY_SETTINGS_PATH = _LEGACY_SETTINGS_DIR / "settings.json"


def _migrate_legacy_settings() -> None:
    """~/.kiwoom-today/settings.json 이 있고 새 경로가 비어 있으면 복사해 온다."""
    if SETTINGS_PATH.is_file() or not _LEGACY_SETTINGS_PATH.is_file():
        return
    try:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(
            _LEGACY_SETTINGS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
    except OSError:
        pass  # 마이그레이션 실패해도 기본 설정으로 계속 동작


def default_journal_dir() -> Path:
    return Path.home() / "Documents" / "invest-retrospect"


@dataclass
class Settings:
    # 어느 증권사를 쓸지
    broker: str = "kiwoom"               # 'kiwoom' | 'kis' | 'ls'
    env: str = "mock"                    # 'mock' | 'prod'

    # 화면 테마: 'light' | 'dark'
    theme: str = "light"
    # 테마 엔진: 'auto'(sv-ttk, Win11 룩) | 'lite'(clam, 리사이즈 빠름)
    theme_engine: str = "auto"

    # 키움
    kiwoom_app_key: str = ""
    kiwoom_secret_key: str = ""
    kiwoom_account_no: str = ""

    # 한국투자증권 (KIS / 한투)
    kis_app_key: str = ""
    kis_app_secret: str = ""
    kis_account_no: str = ""             # "12345678-01" 또는 "1234567801"

    # LS증권
    ls_app_key: str = ""
    ls_app_secret: str = ""
    ls_account_no: str = ""
    ls_account_pwd: str = ""             # 잔고/체결조회 body 의 passwd 필드

    # 메리츠증권 (스캐폴드 — 실제 API 연동은 brokers/meritz.py 의 TODO 참고)
    meritz_app_key: str = ""
    meritz_app_secret: str = ""
    meritz_account_no: str = ""
    meritz_account_pwd: str = ""

    # 대신증권 (스캐폴드 — 실제 API 연동은 brokers/daishin.py 의 TODO 참고)
    daishin_app_key: str = ""
    daishin_app_secret: str = ""
    daishin_account_no: str = ""
    daishin_account_pwd: str = ""

    # AI
    ai_provider: str = "none"            # 'gemini' | 'ollama' | 'none'
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"

    # 수동 원장 국내 시세 조회 API: 'yahoo' | 'kiwoom' | 'kis' (해외는 항상 yahoo)
    manual_domestic_api: str = "yahoo"

    # 수동 원장 저장 모드: 'offline'(로컬 JSON 파일) | 'db'(외부 DB 테이블)
    manual_ledger_mode: str = "offline"
    # DB 모드 접속 정보 (manual_ledger_mode == 'db' 일 때만 사용)
    manual_db_kind: str = "mysql"        # 'mysql'(MariaDB 포함) | 'postgresql'
    manual_db_host: str = ""             # 'host' 또는 'host:port'
    manual_db_name: str = ""             # 데이터베이스(스키마) 이름
    manual_db_user: str = ""
    manual_db_password: str = ""
    manual_db_table: str = "manual_ledger"
    manual_db_ssl: str = "1"             # '1'=SSL 사용(require) · ''=사용 안 함

    # 출력
    journal_dir: str = ""                # 빈 값이면 default_journal_dir() 사용


def load_settings() -> Settings:
    _migrate_legacy_settings()
    if not SETTINGS_PATH.is_file():
        return Settings()
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Settings()
    allowed = {f.name for f in fields(Settings)}
    return Settings(**{k: v for k, v in data.items() if k in allowed and isinstance(v, str)})


def save_settings(s: Settings) -> Path:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(
        json.dumps(asdict(s), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return SETTINGS_PATH


def export_settings(s: Settings, dest: Path) -> Path:
    """현재 설정을 지정 경로에 JSON 으로 백업한다."""
    dest = Path(dest)
    if dest.parent and not dest.parent.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(asdict(s), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return dest


def import_settings(src: Path) -> Settings:
    """백업 JSON 파일에서 설정을 읽어 Settings 로 복원한다.

    알 수 없는 키는 무시하고 load_settings 와 동일한 검증 규칙을 적용한다.
    파일이 없거나(OSError) JSON 형식이 아니면(JSONDecode/ValueError) 예외를 올린다.
    """
    data = json.loads(Path(src).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("올바른 설정 백업 파일이 아닙니다.")
    allowed = {f.name for f in fields(Settings)}
    return Settings(**{k: v for k, v in data.items() if k in allowed and isinstance(v, str)})


def account_no_for(s: Settings, broker: Broker) -> str:
    if broker is Broker.KIWOOM:
        return s.kiwoom_account_no
    if broker is Broker.KIS:
        return s.kis_account_no
    if broker is Broker.LS:
        return s.ls_account_no
    if broker is Broker.MERITZ:
        return s.meritz_account_no
    if broker is Broker.DAISHIN:
        return s.daishin_account_no
    return ""


def set_account_no(s: Settings, broker: Broker, value: str) -> None:
    if broker is Broker.KIWOOM:
        s.kiwoom_account_no = value
    elif broker is Broker.KIS:
        s.kis_account_no = value
    elif broker is Broker.LS:
        s.ls_account_no = value
    elif broker is Broker.MERITZ:
        s.meritz_account_no = value
    elif broker is Broker.DAISHIN:
        s.daishin_account_no = value


def config_from_settings(s: Settings) -> Config:
    """Settings → Config. 선택된 broker 의 키가 비어 있으면 RuntimeError."""
    broker = parse_broker(s.broker or "kiwoom")
    env = (s.env or "mock").strip().lower()
    if env not in ("mock", "prod"):
        env = "mock"

    creds: dict[Broker, BrokerCreds] = {
        Broker.KIWOOM: BrokerCreds(
            app_key=s.kiwoom_app_key.strip(),
            secret_key=s.kiwoom_secret_key.strip(),
        ),
        Broker.KIS: BrokerCreds(
            app_key=s.kis_app_key.strip(),
            secret_key=s.kis_app_secret.strip(),
        ),
        Broker.LS: BrokerCreds(
            app_key=s.ls_app_key.strip(),
            secret_key=s.ls_app_secret.strip(),
            extra={"account_pwd": s.ls_account_pwd.strip()},
        ),
        Broker.MERITZ: BrokerCreds(
            app_key=s.meritz_app_key.strip(),
            secret_key=s.meritz_app_secret.strip(),
            extra={"account_pwd": s.meritz_account_pwd.strip()},
        ),
        Broker.DAISHIN: BrokerCreds(
            app_key=s.daishin_app_key.strip(),
            secret_key=s.daishin_app_secret.strip(),
            extra={"account_pwd": s.daishin_account_pwd.strip()},
        ),
    }
    active = creds.get(broker, BrokerCreds())
    # 대신증권(CYBOS Plus HTS 로그인)·수동 입력은 APP KEY/SECRET 불필요.
    if broker not in (Broker.DAISHIN, Broker.MANUAL) and (not active.app_key or not active.secret_key):
        raise RuntimeError(
            f"{broker.display_name} APP KEY / SECRET KEY 가 설정되지 않았습니다."
        )

    domestic_api = (s.manual_domestic_api or "yahoo").strip().lower()
    if domestic_api not in ("yahoo", "kiwoom", "kis"):
        domestic_api = "yahoo"
    # 수동 모드에서 국내 시세를 키움/한투로 받으려면 해당 증권사 키가 필수.
    if broker is Broker.MANUAL and domestic_api in ("kiwoom", "kis"):
        qb = Broker.KIWOOM if domestic_api == "kiwoom" else Broker.KIS
        qc = creds.get(qb, BrokerCreds())
        if not qc.app_key or not qc.secret_key:
            raise RuntimeError(
                f"국내 시세 조회 API 로 {qb.display_name} 을(를) 선택하려면 "
                f"{qb.display_name} APP KEY / SECRET 를 먼저 입력하세요."
            )

    journal_dir = (
        Path(s.journal_dir).expanduser().resolve()
        if s.journal_dir.strip()
        else default_journal_dir()
    )
    journal_dir.mkdir(parents=True, exist_ok=True)

    provider = s.ai_provider if s.ai_provider in ("gemini", "ollama", "none") else "none"

    return Config(
        broker=broker,
        env=env,
        account_no=account_no_for(s, broker).strip(),
        creds=creds,
        ai_provider=provider,
        gemini_api_key=s.gemini_api_key.strip() or None,
        gemini_model=s.gemini_model.strip() or "gemini-2.5-flash",
        ollama_host=s.ollama_host.strip().rstrip("/") or "http://localhost:11434",
        ollama_model=s.ollama_model.strip() or "llama3.1",
        journal_dir=journal_dir,
        manual_domestic_api=domestic_api,
    )
