"""런타임 설정.

CLI는 .env 에서 읽고, GUI 는 settings_store.Settings 에서 변환된 Config 를 받는다.
브로커별 인증 키는 모두 한 Config 에 들어 있고, 선택된 broker 에 따라 사용된다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from invest_retrospect.brokers import Broker, broker_info, parse_broker

# 하위 호환: 기존 코드에서 import 하던 키움 호스트 상수
from invest_retrospect.brokers.kiwoom import MOCK_HOST, PROD_HOST  # noqa: F401

_VALID_PROVIDERS = ("gemini", "ollama", "none")
_VALID_ENVS = ("prod", "mock")


@dataclass(frozen=True)
class BrokerCreds:
    """단일 증권사 인증 정보 묶음."""
    app_key: str = ""
    secret_key: str = ""
    extra: dict[str, str] = field(default_factory=dict)  # LS account_pwd 등


@dataclass(frozen=True)
class Config:
    broker: Broker                     # 현재 사용할 broker
    env: str                           # 'prod' | 'mock'
    account_no: str                    # 선택된 broker 의 계좌번호
    creds: dict[Broker, BrokerCreds]   # broker 별 인증 정보

    ai_provider: str
    gemini_api_key: str | None
    gemini_model: str
    ollama_host: str
    ollama_model: str
    journal_dir: Path

    # 수동 원장 국내 시세 조회 API: 'yahoo' | 'kiwoom' | 'kis' (해외는 항상 yahoo)
    manual_domestic_api: str = "yahoo"

    @property
    def is_mock(self) -> bool:
        return self.env == "mock"

    @property
    def host(self) -> str:
        return broker_info(self.broker).hosts[self.env]

    @property
    def active_creds(self) -> BrokerCreds:
        return self.creds.get(self.broker, BrokerCreds())

    # ── 기존 코드 호환용 ───────────────────────────────────────────────
    # 이전에는 Config 가 키움 전용이라 kiwoom_app_key 등으로 접근했다.
    # 새 코드에서는 active_creds 또는 creds[Broker.X] 를 쓴다.
    @property
    def kiwoom_host(self) -> str:
        """키움이 선택돼 있을 때만 의미 있음. 호환용."""
        return broker_info(Broker.KIWOOM).hosts[self.env]


def _parse_env(value: str) -> str:
    v = (value or "mock").strip().lower()
    if v not in _VALID_ENVS:
        raise RuntimeError(
            f"환경 값이 올바르지 않습니다: '{value}' (허용: {', '.join(_VALID_ENVS)})"
        )
    return v


def load_config() -> Config:
    """CLI 진입점. .env 기반으로 Config 를 구성한다."""
    load_dotenv()

    broker_str = os.getenv("BROKER", "kiwoom").strip().lower()
    broker = parse_broker(broker_str)

    env = _parse_env(os.getenv(f"{broker.value.upper()}_ENV") or os.getenv("BROKER_ENV") or "mock")

    creds: dict[Broker, BrokerCreds] = {
        Broker.KIWOOM: BrokerCreds(
            app_key=os.getenv("KIWOOM_APP_KEY", "").strip(),
            secret_key=os.getenv("KIWOOM_SECRET_KEY", "").strip(),
        ),
        Broker.KIS: BrokerCreds(
            app_key=os.getenv("KIS_APP_KEY", "").strip(),
            secret_key=os.getenv("KIS_APP_SECRET", "").strip(),
        ),
        Broker.LS: BrokerCreds(
            app_key=os.getenv("LS_APP_KEY", "").strip(),
            secret_key=os.getenv("LS_APP_SECRET", "").strip(),
            extra={"account_pwd": os.getenv("LS_ACCOUNT_PWD", "").strip()},
        ),
        Broker.MERITZ: BrokerCreds(
            app_key=os.getenv("MERITZ_APP_KEY", "").strip(),
            secret_key=os.getenv("MERITZ_APP_SECRET", "").strip(),
            extra={"account_pwd": os.getenv("MERITZ_ACCOUNT_PWD", "").strip()},
        ),
        Broker.DAISHIN: BrokerCreds(
            app_key=os.getenv("DAISHIN_APP_KEY", "").strip(),
            secret_key=os.getenv("DAISHIN_APP_SECRET", "").strip(),
            extra={"account_pwd": os.getenv("DAISHIN_ACCOUNT_PWD", "").strip()},
        ),
    }

    active = creds.get(broker, BrokerCreds())
    # 대신증권(CYBOS Plus HTS 로그인)·수동 입력은 APP KEY/SECRET 불필요.
    if broker not in (Broker.DAISHIN, Broker.MANUAL) and (not active.app_key or not active.secret_key):
        raise RuntimeError(
            f"{broker.display_name} 의 APP KEY / SECRET KEY 가 .env 에 설정되지 않았습니다. "
            f"({broker.value.upper()}_APP_KEY, {broker.value.upper()}_APP_SECRET 또는 _SECRET_KEY)"
        )

    # 계좌번호: BROKER 별 우선, 없으면 KIWOOM_ACCOUNT_NO (구버전 호환)
    account_no = (
        os.getenv(f"{broker.value.upper()}_ACCOUNT_NO", "").strip()
        or os.getenv("KIWOOM_ACCOUNT_NO", "").strip()
    )

    journal_dir = Path(os.getenv("JOURNAL_DIR", "./journals")).expanduser().resolve()
    journal_dir.mkdir(parents=True, exist_ok=True)

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip() or None
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

    ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434").strip().rstrip("/")
    ollama_model = os.getenv("OLLAMA_MODEL", "llama3.1").strip()

    provider = os.getenv("AI_PROVIDER", "").strip().lower()
    if not provider:
        provider = "gemini" if gemini_key else "none"
    if provider not in _VALID_PROVIDERS:
        raise RuntimeError(
            f"AI_PROVIDER 값이 올바르지 않습니다: '{provider}' (허용: {', '.join(_VALID_PROVIDERS)})"
        )

    domestic_api = os.getenv("MANUAL_DOMESTIC_API", "yahoo").strip().lower()
    if domestic_api not in ("yahoo", "kiwoom", "kis"):
        domestic_api = "yahoo"
    if broker is Broker.MANUAL and domestic_api in ("kiwoom", "kis"):
        qb = Broker.KIWOOM if domestic_api == "kiwoom" else Broker.KIS
        qc = creds[qb]
        if not qc.app_key or not qc.secret_key:
            raise RuntimeError(
                f"국내 시세 API 로 {qb.display_name} 선택 시 "
                f"{qb.value.upper()}_APP_KEY / {qb.value.upper()}_APP_SECRET 가 .env 에 필요합니다."
            )

    return Config(
        broker=broker,
        env=env,
        account_no=account_no,
        creds=creds,
        ai_provider=provider,
        gemini_api_key=gemini_key,
        gemini_model=gemini_model,
        ollama_host=ollama_host,
        ollama_model=ollama_model,
        journal_dir=journal_dir,
        manual_domestic_api=domestic_api,
    )
