"""키움/한투/LS/메리츠/대신 매매일지 데스크톱 앱 (Tkinter).

설정은 입력 즉시 자동 저장되며 (~/.invest-retrospect/settings.json),
창 닫기 시에도 한 번 더 동기 저장된다.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import traceback
import webbrowser
from dataclasses import fields, replace
from pathlib import Path
from tkinter import (
    BooleanVar,
    Canvas,
    Menu,
    PhotoImage,
    StringVar,
    TclError,
    Tk,
    Toplevel,
    filedialog,
    messagebox,
    simpledialog,
    ttk,
)
from tkinter.scrolledtext import ScrolledText

from invest_retrospect import ledger_db, market, prices
from invest_retrospect.brokers import Broker
from invest_retrospect.core import JournalResult, run_journal, today_ymd
from invest_retrospect.manual import (
    DEFAULT_ACCOUNT,
    Ledger,
    LedgerBook,
    LedgerEntry,
    export_book,
    holdings_totals,
    import_book,
    load_book,
    parse_bulk_entries,
    parse_excel_entries,
    resolve_current_prices,
    run_manual_journal,
    save_book,
    write_sample_xlsx,
)
from invest_retrospect.settings_store import (
    MANUAL_LEDGER_PATH,
    SETTINGS_PATH,
    Settings,
    config_from_settings,
    default_journal_dir,
    export_settings,
    import_settings,
    load_settings,
    save_settings,
)

# 증권사 API 설정 포털 (한투/키움)
_BROKER_PORTALS: dict[Broker, str] = {
    Broker.KIS: "https://apiportal.koreainvestment.com/",
    Broker.KIWOOM: "https://openapi.kiwoom.com/",
}

PAD = 6
AUTOSAVE_DELAY_MS = 500
_MARKET_REFRESH_SEC = 20


# ── 테마 팔레트 ────────────────────────────────────────────────────────────
class _Palette:
    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


# 한국 증시 색상(상승/하락/보합)은 테마별로 명도를 달리한다 (다크에서는 밝게).
_LIGHT = _Palette(
    bg="#f4f4f4", fg="#1a1a1a", entry_bg="#ffffff", disabled_fg="#9a9a9a",
    select_bg="#cfe2ff", select_fg="#1a1a1a", border="#bfbfbf",
    button="#e6e6e6", button_active="#d7d7d7", tab_bg="#dedede",
    trough="#d0d0d0", heading_bg="#e6e6e6", hint="#888888",
    up="#d60000", down="#0044cc", flat="#555555",
)
_DARK = _Palette(
    bg="#1e1e1e", fg="#e4e4e4", entry_bg="#2b2b2b", disabled_fg="#6a6a6a",
    select_bg="#3a5f8a", select_fg="#ffffff", border="#3c3c3c",
    button="#333333", button_active="#454545", tab_bg="#2a2a2a",
    trough="#2b2b2b", heading_bg="#333333", hint="#9a9a9a",
    up="#ff6b6b", down="#5a9bff", flat="#9a9a9a",
)
_PALETTES = {"light": _LIGHT, "dark": _DARK}

# 현재 활성 팔레트 (라벨/트리 색상 함수가 참조). 테마 적용 시 갱신된다.
_CUR = _LIGHT


def _dir_color(direction: str) -> str:
    return {market.UP: _CUR.up, market.DOWN: _CUR.down}.get(direction, _CUR.flat)


def _sign_dir(s: str) -> str:
    """부호 문자열 → 방향(순매매 금액/수량 색상용)."""
    t = (s or "").strip()
    if t.startswith("-"):
        return market.DOWN
    if t and any(c.isdigit() and c != "0" for c in t):
        return market.UP
    return market.FLAT


# broker 별 (label, secret_key 표시 여부, 추가 필드 표시 여부)
_BROKER_BOXES: dict[Broker, dict[str, str | tuple[str, ...]]] = {
    Broker.KIWOOM: {
        "label": "키움증권 REST API",
        "fields": (("APP KEY", "kiwoom_app_key", ""),
                   ("SECRET KEY", "kiwoom_secret_key", "•"),
                   ("계좌번호", "kiwoom_account_no", "")),
    },
    Broker.KIS: {
        "label": "한국투자증권(KIS / 한투) Open API",
        "fields": (("APP KEY", "kis_app_key", ""),
                   ("APP SECRET", "kis_app_secret", "•"),
                   ("계좌번호 (예: 12345678-01)", "kis_account_no", "")),
    },
    Broker.LS: {
        "label": "LS증권 Open API",
        "fields": (("APP KEY", "ls_app_key", ""),
                   ("APP SECRET", "ls_app_secret", "•"),
                   ("계좌번호", "ls_account_no", ""),
                   ("계좌 비밀번호", "ls_account_pwd", "•")),
    },
    Broker.MERITZ: {
        "label": "메리츠증권 Open API (스캐폴드)",
        "fields": (("APP KEY", "meritz_app_key", ""),
                   ("APP SECRET", "meritz_app_secret", "•"),
                   ("계좌번호", "meritz_account_no", ""),
                   ("계좌 비밀번호", "meritz_account_pwd", "•")),
    },
    Broker.DAISHIN: {
        # CYBOS Plus 는 HTS 로그인이 인증을 대신하므로 APP KEY/SECRET 가 없음.
        # 다른 broker 박스와 시각적 일관성을 위해 동일 4필드 구조를 유지하되
        # 키/시크릿 칸은 사용 안내 문구로 대체.
        "label": "대신증권 (CYBOS Plus, Windows 전용)",
        "fields": (("HTS 사용자 ID (선택)", "daishin_app_key", ""),
                   ("미사용 (CYBOS 자동 인증)", "daishin_app_secret", ""),
                   ("계좌번호", "daishin_account_no", ""),
                   ("계좌 비밀번호 (4자리)", "daishin_account_pwd", "•")),
    },
}

# broker 별 계좌번호 setting key
_ACCOUNT_KEY: dict[Broker, str] = {
    Broker.KIWOOM: "kiwoom_account_no",
    Broker.KIS: "kis_account_no",
    Broker.LS: "ls_account_no",
    Broker.MERITZ: "meritz_account_no",
    Broker.DAISHIN: "daishin_account_no",
}


def _resource_path(name: str) -> Path:
    """리소스 파일 경로 — PyInstaller 번들과 개발 환경 양쪽에서 동작."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / name
    # 개발 환경: src/invest_retrospect/gui.py → 프로젝트 루트
    return Path(__file__).resolve().parents[2] / name


def _open_path(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif sys.platform.startswith("win"):
        subprocess.Popen(["explorer", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _open_url(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass


def _fmt_price(v: float) -> str:
    """단가 표시: 정수면 콤마 정수, 소수면 콤마 + 소수 2자리 (지수표기 방지)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{int(round(f)):,}" if f == int(f) else f"{f:,.2f}"


class SettingsTab(ttk.Frame):
    GEMINI_KEYS = ("gemini_api_key", "gemini_model")
    OLLAMA_KEYS = ("ollama_host", "ollama_model")

    def __init__(self, master: ttk.Notebook, app: "App") -> None:
        super().__init__(master)
        self.app = app
        self._entries: dict[str, ttk.Entry] = {}
        self._labels: dict[str, ttk.Label] = {}
        self._reveal_checks: dict[str, ttk.Checkbutton] = {}
        self._broker_boxes: dict[Broker, ttk.LabelFrame] = {}
        self._build_scroll()       # self.body (스크롤되는 내부 프레임) 생성
        self._build()
        self._update_ai_state()
        self._update_broker_state()
        self._update_ledger_mode_state()

    def _build_scroll(self) -> None:
        """설정 내용이 길어 화면을 넘어가므로 세로 스크롤 캔버스에 담는다."""
        canvas = Canvas(self, highlightthickness=0)
        vsb = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.body = ttk.Frame(canvas, padding=PAD * 2)
        win = canvas.create_window((0, 0), window=self.body, anchor="nw")

        # 리사이즈 성능: 드래그 중 매 픽셀마다 bbox("all")(전체 위젯 O(N))를 다시
        # 계산하고 본문 폭을 바꿔 재배치 피드백을 일으키면 느려진다. 폭은 실제로
        # 변할 때만 반영하고, 스크롤 영역 재계산은 60ms 디바운스로 한 번만 한다.
        self._scroll_after: str | None = None
        self._canvas_w = -1

        def _sync_region() -> None:
            self._scroll_after = None
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(e) -> None:
            if e.width != self._canvas_w:
                self._canvas_w = e.width
                canvas.itemconfigure(win, width=e.width)

        def _on_body_configure(_e) -> None:
            if self._scroll_after is not None:
                self.after_cancel(self._scroll_after)
            self._scroll_after = self.after(60, _sync_region)

        canvas.bind("<Configure>", _on_canvas_configure)
        self.body.bind("<Configure>", _on_body_configure)

        # 포인터가 설정 영역 위에 있을 때만 휠 스크롤 (다른 탭/트리와 충돌 방지)
        def _wheel(e):
            canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", _wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

    def _row(self, parent: ttk.Frame, r: int, label: str, key: str, *, show: str = "") -> ttk.Entry:
        lbl = ttk.Label(parent, text=label)
        lbl.grid(row=r, column=0, sticky="w", padx=PAD, pady=2)
        ent = ttk.Entry(parent, textvariable=self.app.setting_vars[key], width=46, show=show)
        ent.grid(row=r, column=1, sticky="we", padx=PAD, pady=2)
        self._entries[key] = ent
        self._labels[key] = lbl
        # 마스킹 필드(secret/비밀번호)에는 평문 표시 토글 체크박스를 추가한다.
        if show:
            reveal_var = BooleanVar(value=False)

            def _toggle(v=reveal_var, e=ent, s=show) -> None:
                e.configure(show="" if v.get() else s)

            chk = ttk.Checkbutton(parent, text="표시", variable=reveal_var, command=_toggle)
            chk.grid(row=r, column=2, sticky="w", padx=(0, PAD), pady=2)
            self._reveal_checks[key] = chk
        return ent

    def _build(self) -> None:
        self.body.columnconfigure(0, weight=1)
        row = 0

        # ── 증권사 + 환경 선택 ────────────────────────────────────────────
        top = ttk.LabelFrame(self.body, text="증권사 / 환경", padding=PAD)
        top.grid(row=row, column=0, sticky="we", pady=(0, PAD))
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="증권사").grid(row=0, column=0, sticky="w", padx=PAD, pady=2)
        bf = ttk.Frame(top)
        bf.grid(row=0, column=1, sticky="w")
        broker_var = self.app.setting_vars["broker"]
        broker_var.trace_add("write", lambda *_: self._update_broker_state())
        for b in Broker:
            ttk.Radiobutton(bf, text=b.display_name, variable=broker_var, value=b.value).pack(
                side="left", padx=(0, PAD)
            )

        ttk.Label(top, text="환경").grid(row=1, column=0, sticky="w", padx=PAD, pady=2)
        ef = ttk.Frame(top)
        ef.grid(row=1, column=1, sticky="w")
        env_var = self.app.setting_vars["env"]
        ttk.Radiobutton(ef, text="모의 (mock)", variable=env_var, value="mock").pack(side="left")
        ttk.Radiobutton(ef, text="운영 (prod)", variable=env_var, value="prod").pack(
            side="left", padx=(PAD, 0)
        )
        row += 1

        # ── 화면 테마 ─────────────────────────────────────────────────────
        vbox = ttk.LabelFrame(self.body, text="화면", padding=PAD)
        vbox.grid(row=row, column=0, sticky="we", pady=(0, PAD))
        self._dark_var = BooleanVar(value=self.app.setting_vars["theme"].get() == "dark")
        ttk.Checkbutton(
            vbox, text="다크 모드", variable=self._dark_var,
            command=lambda: self.app.set_theme("dark" if self._dark_var.get() else "light"),
        ).grid(row=0, column=0, sticky="w", padx=PAD, pady=2)
        self._lite_var = BooleanVar(
            value=self.app.setting_vars["theme_engine"].get() == "lite")
        ttk.Checkbutton(
            vbox, text="가벼운 테마 (리사이즈·반응 빠름)", variable=self._lite_var,
            command=lambda: self.app.set_theme_engine(self._lite_var.get()),
        ).grid(row=0, column=1, sticky="w", padx=PAD, pady=2)
        ttk.Label(vbox, text="화려한 Win11 룩 대신 가벼운 테마로 — 위젯이 많은 화면의 끊김 완화",
                  style="Hint.TLabel").grid(row=1, column=0, columnspan=2, sticky="w",
                                            padx=PAD, pady=(0, 2))
        row += 1

        # ── broker 별 키 입력 박스 (전부 표시, 선택된 것만 강조) ──────────
        for b, spec in _BROKER_BOXES.items():
            box = ttk.LabelFrame(self.body, text=str(spec["label"]), padding=PAD)
            box.grid(row=row, column=0, sticky="we", pady=(0, PAD))
            box.columnconfigure(1, weight=1)
            self._broker_boxes[b] = box
            nfields = 0
            for i, (label, key, show) in enumerate(spec["fields"]):  # type: ignore[arg-type]
                self._row(box, i, label, key, show=show)
                nfields = i + 1
            portal = _BROKER_PORTALS.get(b)
            if portal:
                ttk.Button(
                    box, text="API 설정 페이지 열기",
                    command=lambda u=portal: _open_url(u),
                ).grid(row=nfields, column=1, sticky="w", padx=PAD, pady=(2, 0))
            row += 1

        # ── 수동 원장: 국내 시세 조회 API (수동 원장 탭과 동일 값, 동기화) ──
        pbox = ttk.LabelFrame(self.body, text="수동 원장 — 국내 시세 조회 API", padding=PAD)
        pbox.grid(row=row, column=0, sticky="we", pady=(0, PAD))
        api_var = self.app.setting_vars["manual_domestic_api"]
        pf = ttk.Frame(pbox)
        pf.grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(pf, text="야후 (Yahoo)", variable=api_var, value="yahoo").pack(side="left")
        ttk.Radiobutton(pf, text="한투 (KIS)", variable=api_var, value="kis").pack(side="left", padx=(PAD, 0))
        ttk.Radiobutton(pf, text="키움", variable=api_var, value="kiwoom").pack(side="left", padx=(PAD, 0))
        ttk.Label(
            pbox,
            text="한투/키움 선택 시 해당 증권사 APP KEY/SECRET 필수 · 해외주식은 항상 Yahoo",
            style="Hint.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        row += 1

        # ── 수동 원장: 저장 모드 (오프라인 파일 / 외부 DB) ──────────────────
        sbox = ttk.LabelFrame(self.body, text="수동 원장 — 저장 모드", padding=PAD)
        sbox.grid(row=row, column=0, sticky="we", pady=(0, PAD))
        sbox.columnconfigure(0, weight=1)
        mode_var = self.app.setting_vars["manual_ledger_mode"]
        mode_var.trace_add("write", lambda *_: self._update_ledger_mode_state())
        mf = ttk.Frame(sbox)
        mf.grid(row=0, column=0, sticky="w", padx=PAD, pady=2)
        ttk.Radiobutton(mf, text="오프라인 (로컬 파일)", variable=mode_var, value="offline").pack(side="left")
        ttk.Radiobutton(mf, text="DB (외부 데이터베이스)", variable=mode_var, value="db").pack(
            side="left", padx=(PAD, 0))

        # DB 접속 상세 (DB 모드일 때만 활성)
        dbf = ttk.Frame(sbox)
        dbf.grid(row=1, column=0, sticky="we", pady=(PAD, 0))
        dbf.columnconfigure(1, weight=1)
        ttk.Label(dbf, text="DB 종류").grid(row=0, column=0, sticky="w", padx=PAD, pady=2)
        kindf = ttk.Frame(dbf)
        kindf.grid(row=0, column=1, sticky="w")
        kind_var = self.app.setting_vars["manual_db_kind"]
        self._db_kind_radios: list[ttk.Radiobutton] = []
        for txt, val in (("MariaDB/MySQL", "mysql"), ("PostgreSQL", "postgresql")):
            rb = ttk.Radiobutton(kindf, text=txt, variable=kind_var, value=val)
            rb.pack(side="left", padx=(0, PAD))
            self._db_kind_radios.append(rb)
        self._row(dbf, 1, "호스트 (host 또는 host:port)", "manual_db_host")
        self._row(dbf, 2, "DB 이름", "manual_db_name")
        self._row(dbf, 3, "사용자", "manual_db_user")
        self._row(dbf, 4, "비밀번호", "manual_db_password", show="•")
        self._row(dbf, 5, "테이블", "manual_db_table")
        self._db_ssl_chk = ttk.Checkbutton(
            dbf, text="SSL 사용 (require) — Neon 등 매니지드 DB 필수",
            variable=self.app.setting_vars["manual_db_ssl"], onvalue="1", offvalue="")
        self._db_ssl_chk.grid(row=6, column=1, sticky="w", padx=PAD, pady=2)
        self._db_test_btn = ttk.Button(dbf, text="연결 테스트", command=self._test_db)
        self._db_test_btn.grid(row=7, column=1, sticky="w", padx=PAD, pady=(4, 0))
        ttk.Label(
            sbox,
            text="DB 모드: 매매 항목을 외부 DB 테이블 한 곳에 저장 — 여러 PC 공유 가능 "
                 "(활성 계좌·수동 현재가는 로컬 보관). 테이블이 없으면 자동 생성됩니다.",
            style="Hint.TLabel",
        ).grid(row=2, column=0, sticky="w", padx=PAD, pady=(2, 0))
        self._db_keys = ("manual_db_host", "manual_db_name", "manual_db_user",
                         "manual_db_password", "manual_db_table")
        row += 1

        # ── AI 박스 ───────────────────────────────────────────────────────
        abox = ttk.LabelFrame(self.body, text="AI 코멘트", padding=PAD)
        abox.grid(row=row, column=0, sticky="we", pady=(0, PAD))
        abox.columnconfigure(1, weight=1)

        ttk.Label(abox, text="제공자").grid(row=0, column=0, sticky="w", padx=PAD, pady=2)
        provider_frame = ttk.Frame(abox)
        provider_frame.grid(row=0, column=1, sticky="w")
        prov_var = self.app.setting_vars["ai_provider"]
        prov_var.trace_add("write", lambda *_: self._update_ai_state())
        ttk.Radiobutton(provider_frame, text="사용 안 함", variable=prov_var, value="none").pack(side="left")
        ttk.Radiobutton(provider_frame, text="Gemini", variable=prov_var, value="gemini").pack(side="left", padx=(PAD, 0))
        ttk.Radiobutton(provider_frame, text="Ollama", variable=prov_var, value="ollama").pack(side="left", padx=(PAD, 0))

        self._row(abox, 1, "GEMINI API KEY", "gemini_api_key", show="•")
        self._row(abox, 2, "Gemini 모델", "gemini_model")
        self._row(abox, 3, "Ollama 호스트", "ollama_host")
        self._row(abox, 4, "Ollama 모델", "ollama_model")
        row += 1

        # ── 출력 ──────────────────────────────────────────────────────────
        obox = ttk.LabelFrame(self.body, text="출력", padding=PAD)
        obox.grid(row=row, column=0, sticky="we", pady=(0, PAD))
        obox.columnconfigure(1, weight=1)
        ttk.Label(obox, text="저장 디렉토리").grid(row=0, column=0, sticky="w", padx=PAD, pady=2)
        dir_var = self.app.setting_vars["journal_dir"]
        if not dir_var.get():
            dir_var.set(str(default_journal_dir()))
        ttk.Entry(obox, textvariable=dir_var, width=40).grid(row=0, column=1, sticky="we", padx=PAD)
        ttk.Button(obox, text="찾아보기...", command=self._pick_dir).grid(row=0, column=2, padx=PAD)
        row += 1

        # ── 백업 / 복구 ───────────────────────────────────────────────────
        bkbox = ttk.LabelFrame(self.body, text="설정 백업 / 복구", padding=PAD)
        bkbox.grid(row=row, column=0, sticky="we", pady=(0, PAD))
        ttk.Button(bkbox, text="설정 백업...", command=self._backup_settings).grid(
            row=0, column=0, sticky="w", padx=PAD, pady=2
        )
        ttk.Button(bkbox, text="설정 복구...", command=self._restore_settings).grid(
            row=0, column=1, sticky="w", padx=PAD, pady=2
        )
        ttk.Label(
            bkbox,
            text="모든 증권사 키·계좌·AI·출력 설정을 JSON 파일로 내보내거나 불러옵니다.",
            style="Hint.TLabel",
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=PAD, pady=(2, 0))
        row += 1

        # 하단: 자동 저장 안내
        ttk.Label(
            self.body,
            text=f"저장 위치: {SETTINGS_PATH}  ·  변경 시 자동 저장",
            style="Hint.TLabel",
        ).grid(row=row, column=0, sticky="w", pady=(PAD, 0))

    def _update_ai_state(self) -> None:
        provider = self.app.setting_vars["ai_provider"].get()
        gemini_on = provider == "gemini"
        ollama_on = provider == "ollama"

        def apply(keys: tuple[str, ...], enabled: bool) -> None:
            state = "normal" if enabled else "disabled"
            for k in keys:
                if k in self._entries:
                    self._entries[k].configure(state=state)
                if k in self._labels:
                    self._labels[k].configure(foreground="" if enabled else "#999")
                if k in self._reveal_checks:
                    self._reveal_checks[k].configure(state=state)

        apply(self.GEMINI_KEYS, gemini_on)
        apply(self.OLLAMA_KEYS, ollama_on)

    def _update_ledger_mode_state(self) -> None:
        """DB 모드일 때만 DB 접속 입력을 활성화한다."""
        on = self.app.setting_vars["manual_ledger_mode"].get() == "db"
        state = "normal" if on else "disabled"
        for k in self._db_keys:
            if k in self._entries:
                self._entries[k].configure(state=state)
            if k in self._labels:
                self._labels[k].configure(foreground="" if on else "#999")
            if k in self._reveal_checks:
                self._reveal_checks[k].configure(state=state)
        for rb in self._db_kind_radios:
            rb.configure(state=state)
        self._db_ssl_chk.configure(state=state)
        self._db_test_btn.configure(state=state)

    def _test_db(self) -> None:
        """현재 입력된 DB 접속을 백그라운드로 시험한다 (GUI 멈춤 방지).

        워커 스레드는 결과만 속성에 담고, Tk 위젯 갱신·메시지박스는 메인 스레드
        폴링(_poll_db_test)에서 처리한다 — Tk 는 메인 스레드에서만 호출해야 안전.
        """
        self.app.flush_save()
        db = ledger_db.DbSettings.from_settings(self.app.settings)
        self._db_test_btn.configure(state="disabled", text="테스트 중...")
        self._db_test_result: tuple[str | None, str | None] | None = None

        def work() -> None:
            try:
                self._db_test_result = (ledger_db.test_connection(db), None)
            except Exception as e:  # noqa: BLE001  접속/드라이버/SQL 오류 전반
                self._db_test_result = (None, str(e))

        threading.Thread(target=work, daemon=True).start()
        self.after(100, self._poll_db_test)

    def _poll_db_test(self) -> None:
        if self._db_test_result is None:        # 아직 진행 중 → 다시 폴링
            self.after(100, self._poll_db_test)
            return
        msg, err = self._db_test_result
        self._db_test_btn.configure(state="normal", text="연결 테스트")
        if err:
            messagebox.showerror("연결 실패", err)
        else:
            messagebox.showinfo("연결 성공", msg or "OK")

    def _update_broker_state(self) -> None:
        """선택되지 않은 broker 박스의 입력은 비활성화 + 회색."""
        cur = self.app.setting_vars["broker"].get()
        for b, box in self._broker_boxes.items():
            active = b.value == cur
            spec = _BROKER_BOXES[b]
            label_text = str(spec["label"])
            box.configure(text=label_text + ("  (현재 선택)" if active else ""))
            for _label, key, _show in spec["fields"]:  # type: ignore[arg-type]
                state = "normal" if active else "disabled"
                if key in self._entries:
                    self._entries[key].configure(state=state)
                if key in self._labels:
                    self._labels[key].configure(foreground="" if active else "#999")
                if key in self._reveal_checks:
                    self._reveal_checks[key].configure(state=state)

    def _pick_dir(self) -> None:
        var = self.app.setting_vars["journal_dir"]
        current = var.get() or str(default_journal_dir())
        chosen = filedialog.askdirectory(initialdir=current, title="저장 디렉토리 선택")
        if chosen:
            var.set(chosen)

    def _backup_settings(self) -> None:
        """현재 입력 중인 모든 설정을 JSON 파일로 내보낸다."""
        self.app.flush_save()
        path = filedialog.asksaveasfilename(
            title="설정 백업 저장",
            defaultextension=".json",
            initialfile="invest-retrospect-settings.json",
            filetypes=[("JSON 파일", "*.json"), ("모든 파일", "*.*")],
        )
        if not path:
            return
        try:
            current = Settings(**{k: v.get() for k, v in self.app.setting_vars.items()})
            export_settings(current, Path(path))
        except OSError as e:
            messagebox.showerror("백업 실패", str(e))
            return
        messagebox.showinfo("백업 완료", f"설정을 백업했습니다.\n{path}")

    def _restore_settings(self) -> None:
        """백업 JSON 파일을 읽어 모든 설정을 덮어쓴다."""
        path = filedialog.askopenfilename(
            title="설정 복구 — 백업 파일 선택",
            filetypes=[("JSON 파일", "*.json"), ("모든 파일", "*.*")],
        )
        if not path:
            return
        try:
            restored = import_settings(Path(path))
        except OSError as e:
            messagebox.showerror("복구 실패", f"파일을 읽을 수 없습니다.\n{e}")
            return
        except ValueError as e:
            # json.JSONDecodeError 포함 — 형식이 올바르지 않은 파일
            messagebox.showerror("복구 실패", f"올바른 설정 백업 파일이 아닙니다.\n{e}")
            return
        if not messagebox.askyesno(
            "설정 복구",
            "현재 설정을 백업 파일 내용으로 덮어씁니다. 계속할까요?",
        ):
            return
        self.app.apply_settings(restored)
        messagebox.showinfo("복구 완료", "설정을 복구했습니다.")


class JournalTab(ttk.Frame):
    def __init__(self, master: ttk.Notebook, app: "App") -> None:
        super().__init__(master, padding=PAD * 2)
        self.app = app
        self._result: JournalResult | None = None
        self._build()
        # broker 가 바뀌면 계좌번호 입력란이 가리키는 StringVar 도 바꾼다
        self.app.setting_vars["broker"].trace_add("write", lambda *_: self._sync_account_entry())
        self._sync_account_entry()

    def _current_broker(self) -> Broker:
        try:
            return Broker(self.app.setting_vars["broker"].get())
        except ValueError:
            return Broker.KIWOOM

    def _sync_account_entry(self) -> None:
        b = self._current_broker()
        if b is Broker.MANUAL:
            self.account_entry.configure(state="disabled")
            self.run_btn.configure(state="disabled")
            self.broker_label_var.set("증권사: 수동 입력 — [수동 원장] 탭에서 일지를 생성하세요")
            return
        key = _ACCOUNT_KEY[b]
        self.account_entry.configure(state="normal", textvariable=self.app.setting_vars[key])
        self.run_btn.configure(state="normal")
        self.broker_label_var.set(f"증권사: {b.display_name}")

    def _build(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(5, weight=1)

        self.broker_label_var = StringVar(value="증권사: -")
        ttk.Label(self, textvariable=self.broker_label_var, style="Hint.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", padx=PAD, pady=(0, PAD)
        )

        ttk.Label(self, text="날짜 (YYYYMMDD)").grid(row=1, column=0, sticky="w", padx=PAD, pady=2)
        self.date_var = StringVar(value=today_ymd())
        ttk.Entry(self, textvariable=self.date_var, width=14).grid(row=1, column=1, sticky="w", padx=PAD)

        ttk.Label(self, text="계좌번호").grid(row=2, column=0, sticky="w", padx=PAD, pady=2)
        # 초기 textvariable 은 _sync_account_entry 에서 교체됨
        self.account_entry = ttk.Entry(self, width=22)
        self.account_entry.grid(row=2, column=1, sticky="w", padx=PAD)

        ttk.Label(self, text="형식").grid(row=3, column=0, sticky="w", padx=PAD, pady=2)
        fmt_frame = ttk.Frame(self)
        fmt_frame.grid(row=3, column=1, sticky="w")
        self.fmt_var = StringVar(value="md")
        ttk.Radiobutton(fmt_frame, text="Markdown", variable=self.fmt_var, value="md").pack(side="left")
        ttk.Radiobutton(fmt_frame, text="PDF", variable=self.fmt_var, value="pdf").pack(side="left", padx=(PAD, 0))
        ttk.Radiobutton(fmt_frame, text="둘 다", variable=self.fmt_var, value="both").pack(side="left", padx=(PAD, 0))

        action_frame = ttk.Frame(self)
        action_frame.grid(row=4, column=0, columnspan=2, sticky="we", pady=(PAD, 0))
        self.run_btn = ttk.Button(action_frame, text="매매일지 생성", command=self._on_run)
        self.run_btn.pack(side="left")
        self.open_md_btn = ttk.Button(action_frame, text="MD 열기", command=lambda: self._open("md"), state="disabled")
        self.open_md_btn.pack(side="left", padx=(PAD, 0))
        self.open_pdf_btn = ttk.Button(action_frame, text="PDF 열기", command=lambda: self._open("pdf"), state="disabled")
        self.open_pdf_btn.pack(side="left", padx=(PAD, 0))
        self.open_dir_btn = ttk.Button(action_frame, text="폴더 열기", command=self._open_dir, state="disabled")
        self.open_dir_btn.pack(side="left", padx=(PAD, 0))

        self.log = ScrolledText(self, height=14, font=("Menlo", 11))
        self.log.grid(row=5, column=0, columnspan=2, sticky="nsew", pady=(PAD, 0))
        self.log.configure(state="disabled")

    def _append_log(self, msg: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _log(self, msg: str) -> None:
        self.after(0, self._append_log, msg)

    def _on_run(self) -> None:
        self.app.flush_save()
        try:
            cfg = config_from_settings(self.app.settings)
        except RuntimeError as e:
            messagebox.showerror("설정 누락", f"{e}\n\n[설정] 탭에서 키를 입력하세요.")
            return

        ymd = self.date_var.get().strip()
        b = self._current_broker()
        account = self.app.setting_vars[_ACCOUNT_KEY[b]].get().strip()
        fmt = self.fmt_var.get()

        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.run_btn.configure(state="disabled")
        self.open_md_btn.configure(state="disabled")
        self.open_pdf_btn.configure(state="disabled")
        self.open_dir_btn.configure(state="disabled")
        self._result = None

        threading.Thread(
            target=self._run_worker, args=(cfg, ymd, account, fmt), daemon=True
        ).start()

    def _run_worker(self, cfg, ymd, account, fmt) -> None:
        try:
            result = run_journal(cfg, ymd, account, fmt, log=self._log)
            self.after(0, self._on_done, result)
        except Exception as e:  # noqa: BLE001
            self._log(f"[error] {e}")
            self._log(traceback.format_exc())
            self.after(0, self._on_error, e)

    def _on_done(self, result: JournalResult) -> None:
        self._result = result
        self.run_btn.configure(state="normal")
        self.open_dir_btn.configure(state="normal")
        if result.md_path:
            self.open_md_btn.configure(state="normal")
        if result.pdf_path:
            self.open_pdf_btn.configure(state="normal")

    def _on_error(self, exc: Exception) -> None:
        self.run_btn.configure(state="normal")
        messagebox.showerror("생성 실패", str(exc))

    def _open(self, which: str) -> None:
        if not self._result:
            return
        path = self._result.md_path if which == "md" else self._result.pdf_path
        if path and path.is_file():
            _open_path(path)

    def _open_dir(self) -> None:
        if self._result:
            _open_path(self._result.json_path.parent)


class AccountLedgerFrame(ttk.Frame):
    """한 계좌의 수동 주식 원장: 매수/매도 변화 기록 → 임의 날짜 매매일지 생성."""

    _COLS = ("check", "date", "market", "name", "code", "side", "qty", "price", "cur", "tag")
    _HEADS = {
        "check": "✓", "date": "거래일", "market": "시장", "name": "종목명", "code": "코드",
        "side": "구분", "qty": "수량", "price": "단가", "cur": "현재가", "tag": "태그",
    }
    _COL_W = {"check": 34, "date": 80, "market": 64, "name": 130, "code": 70,
              "side": 50, "qty": 64, "price": 90, "cur": 90, "tag": 70}
    _CHECK_ON, _CHECK_OFF = "☑", "☐"

    def __init__(self, master: ttk.Notebook, app: "App",
                 manual_tab: "ManualLedgerTab", name: str, ledger: Ledger) -> None:
        super().__init__(master, padding=PAD * 2)
        self.app = app
        self.manual_tab = manual_tab
        self.account_name = name
        self.ledger: Ledger = ledger
        self._result: JournalResult | None = None
        self._checked: set[int] = set()    # 체크된 항목 (id(entry) 기준 — 객체 식별)
        self._cur_prices: dict[str, float] = {}   # 조회된 현재가 {코드: 가격} (목록 표시용)
        self._build()
        self._reload_tree()
        self._refresh_price_label()

    def _persist(self) -> None:
        """원장 변경을 묶음 전체 파일에 저장 (컨테이너 경유)."""
        self.manual_tab._save_book()

    # ── UI ───────────────────────────────────────────────────────────────
    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        # 목록 칸만 늘어나므로 minsize 로 바닥을 깔아 둔다 — 창이 짧아도
        # 목록+버튼 줄(엑셀 업로드/샘플 다운로드 포함)이 0으로 찌그러지지 않게.
        self.rowconfigure(2, weight=1, minsize=170)

        # 입력 폼
        form = ttk.LabelFrame(self, text="원장 항목 추가 (보유수량 변화)", padding=PAD)
        form.grid(row=0, column=0, sticky="we")
        self.in_date = StringVar(value=today_ymd())
        self.in_market = StringVar(value=prices.DEFAULT_MARKET)
        self.in_name = StringVar()
        self.in_code = StringVar()
        self.in_side = StringVar(value="매수")
        self.in_qty = StringVar()
        self.in_price = StringVar()
        self.in_tag = StringVar()

        ttk.Label(form, text="거래일").grid(row=0, column=0, padx=2, sticky="w")
        ttk.Entry(form, textvariable=self.in_date, width=10).grid(row=0, column=1, padx=2)
        ttk.Label(form, text="시장").grid(row=0, column=2, padx=2, sticky="w")
        ttk.Combobox(form, textvariable=self.in_market, values=prices.market_names(),
                     width=8, state="readonly").grid(row=0, column=3, padx=2)
        ttk.Label(form, text="구분").grid(row=0, column=4, padx=2, sticky="w")
        ttk.Combobox(form, textvariable=self.in_side, values=("매수", "매도"),
                     width=5, state="readonly").grid(row=0, column=5, padx=2)
        ttk.Label(form, text="태그").grid(row=0, column=6, padx=2, sticky="w")
        ttk.Entry(form, textvariable=self.in_tag, width=10).grid(row=0, column=7, padx=2)

        ttk.Label(form, text="종목명").grid(row=1, column=0, padx=2, sticky="w")
        ttk.Entry(form, textvariable=self.in_name, width=12).grid(row=1, column=1, padx=2)
        ttk.Label(form, text="코드/티커").grid(row=1, column=2, padx=2, sticky="w")
        ttk.Entry(form, textvariable=self.in_code, width=10).grid(row=1, column=3, padx=2)
        ttk.Label(form, text="수량").grid(row=1, column=4, padx=2, sticky="w")
        ttk.Entry(form, textvariable=self.in_qty, width=8).grid(row=1, column=5, padx=2)
        ttk.Label(form, text="단가").grid(row=1, column=6, padx=2, sticky="w")
        ttk.Entry(form, textvariable=self.in_price, width=10).grid(row=1, column=7, padx=2)
        ttk.Button(form, text="추가", command=self._add_entry).grid(row=1, column=8, padx=(PAD, 2))
        ttk.Button(form, text="여러 줄 붙여넣기", command=self._bulk_paste).grid(
            row=0, column=8, padx=(PAD, 2)
        )

        # 원장 목록 + 삭제
        list_frame = ttk.Frame(self)
        list_frame.grid(row=2, column=0, sticky="nsew", pady=(PAD, 0))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(list_frame, columns=self._COLS, show="headings", height=8,
                                 selectmode="extended")
        for c in self._COLS:
            self.tree.heading(c, text=self._HEADS[c])
            anchor = "w" if c in ("name", "tag") else "center"
            self.tree.column(c, width=self._COL_W[c], anchor=anchor,
                             stretch=(c in ("name", "tag")))
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", self._on_tree_double)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        bar = ttk.Frame(list_frame)
        bar.grid(row=1, column=0, columnspan=2, sticky="we", pady=(2, 0))
        ttk.Button(bar, text="전체 체크", command=lambda: self._check_all(True)).pack(side="left")
        ttk.Button(bar, text="전체 해제", command=lambda: self._check_all(False)).pack(side="left", padx=(PAD, 0))
        ttk.Button(bar, text="선택 항목 수정", command=self._edit_selected).pack(side="left", padx=(PAD, 0))
        ttk.Button(bar, text="선택 항목 삭제", command=self._delete_selected).pack(side="left", padx=(PAD, 0))
        self.fetch_px_btn = ttk.Button(bar, text="현재가 조회", command=self._fetch_prices)
        self.fetch_px_btn.pack(side="left", padx=(PAD, 0))
        # 업로드/샘플은 오른쪽 끝에 고정 — 버튼이 많아도 화면 밖으로 잘리지 않도록.
        ttk.Button(bar, text="샘플 다운로드", command=self._download_sample).pack(side="right")
        ttk.Button(bar, text="엑셀 업로드", command=self._upload_excel).pack(side="right", padx=(0, PAD))

        # 현재가 조회 후 보유 평가총액·매입총액·평가손익 요약 (통화별, 손익은 등락색)
        self.summary_frame = ttk.Frame(list_frame)
        self.summary_frame.grid(row=2, column=0, columnspan=2, sticky="we", pady=(2, 0))

        # 수동 현재가(폴백)
        pf = ttk.LabelFrame(self, text="수동 현재가 (Yahoo/증권사 조회 실패 시 폴백)", padding=PAD)
        pf.grid(row=3, column=0, sticky="we", pady=(PAD, 0))
        self.px_code = StringVar()
        self.px_value = StringVar()
        ttk.Label(pf, text="코드").grid(row=0, column=0, padx=2)
        ttk.Entry(pf, textvariable=self.px_code, width=10).grid(row=0, column=1, padx=2)
        ttk.Label(pf, text="현재가").grid(row=0, column=2, padx=2)
        ttk.Entry(pf, textvariable=self.px_value, width=10).grid(row=0, column=3, padx=2)
        ttk.Button(pf, text="저장", command=self._set_price).grid(row=0, column=4, padx=(PAD, 2))
        self.px_label = ttk.Label(pf, text="", style="Hint.TLabel")
        self.px_label.grid(row=0, column=5, padx=PAD)

        # 생성
        gen = ttk.LabelFrame(self, text="매매일지 생성", padding=PAD)
        gen.grid(row=4, column=0, sticky="we", pady=(PAD, 0))
        self.gen_date = StringVar(value=today_ymd())
        self.gen_fmt = StringVar(value="md")
        self.gen_fetch = BooleanVar(value=True)

        # 국내 시세 조회 API (해외는 항상 Yahoo) — 즉시 자동 저장되는 공용 설정에 바인딩
        api_var = self.app.setting_vars["manual_domestic_api"]
        ttk.Label(gen, text="국내 시세 API").grid(row=0, column=0, padx=2, sticky="w")
        apif = ttk.Frame(gen)
        apif.grid(row=0, column=1, columnspan=5, sticky="w")
        ttk.Radiobutton(apif, text="야후", variable=api_var, value="yahoo").pack(side="left")
        ttk.Radiobutton(apif, text="한투(KIS)", variable=api_var, value="kis").pack(side="left", padx=(PAD, 0))
        ttk.Radiobutton(apif, text="키움", variable=api_var, value="kiwoom").pack(side="left", padx=(PAD, 0))
        ttk.Label(apif, text="(한투/키움은 [설정]에서 키 입력 · 해외는 항상 Yahoo)",
                  style="Hint.TLabel").pack(side="left", padx=(PAD, 0))

        ttk.Label(gen, text="기준일 (YYYYMMDD)").grid(row=1, column=0, padx=2, sticky="w")
        ttk.Entry(gen, textvariable=self.gen_date, width=10).grid(row=1, column=1, padx=2)
        ttk.Radiobutton(gen, text="MD", variable=self.gen_fmt, value="md").grid(row=1, column=2, padx=2)
        ttk.Radiobutton(gen, text="PDF", variable=self.gen_fmt, value="pdf").grid(row=1, column=3, padx=2)
        ttk.Radiobutton(gen, text="둘 다", variable=self.gen_fmt, value="both").grid(row=1, column=4, padx=2)
        ttk.Checkbutton(gen, text="자동 시세조회", variable=self.gen_fetch).grid(
            row=1, column=5, padx=(PAD, 2)
        )
        self.gen_btn = ttk.Button(gen, text="전체 생성", command=lambda: self._on_generate(False))
        self.gen_btn.grid(row=2, column=0, sticky="w", pady=(PAD, 0))
        self.gen_checked_btn = ttk.Button(gen, text="체크 항목만 생성",
                                          command=lambda: self._on_generate(True))
        self.gen_checked_btn.grid(row=2, column=1, sticky="w", padx=2, pady=(PAD, 0))
        self.open_md_btn = ttk.Button(gen, text="MD 열기", command=lambda: self._open("md"), state="disabled")
        self.open_md_btn.grid(row=2, column=2, padx=2, pady=(PAD, 0))
        self.open_pdf_btn = ttk.Button(gen, text="PDF 열기", command=lambda: self._open("pdf"), state="disabled")
        self.open_pdf_btn.grid(row=2, column=3, padx=2, pady=(PAD, 0))
        self.open_dir_btn = ttk.Button(gen, text="폴더 열기", command=self._open_dir, state="disabled")
        self.open_dir_btn.grid(row=2, column=4, padx=2, pady=(PAD, 0))

        self.log = ScrolledText(self, height=5, font=("Menlo", 10))
        self.log.grid(row=5, column=0, sticky="nsew", pady=(PAD, 0))
        self.log.configure(state="disabled")

    # ── 원장 편집 ─────────────────────────────────────────────────────────
    def _reload_tree(self) -> None:
        # 사라진 항목의 체크 상태 정리
        self._checked &= {id(e) for e in self.ledger.entries}
        self.tree.delete(*self.tree.get_children())
        for i, e in enumerate(self.ledger.entries):
            mark = self._CHECK_ON if id(e) in self._checked else self._CHECK_OFF
            cur = self._cur_prices.get(e.stk_cd)
            cur_txt = _fmt_price(cur) if cur is not None else ""
            self.tree.insert(
                "", "end", iid=str(i),
                values=(mark, e.date, e.market, e.stk_nm, e.stk_cd, e.side,
                        f"{e.qty:,}", _fmt_price(e.price), cur_txt, e.tag),
            )
        self._render_summary()

    def _render_summary(self) -> None:
        """보유 종목 통화별 평가/매입/손익 요약을 목록 아래에 표시. 손익은 한국식
        등락색(이익=빨강, 손실=파랑)으로 칠한다. 현재가가 없으면(조회 전) 비운다."""
        for w in self.summary_frame.winfo_children():
            w.destroy()
        if not self._cur_prices:
            return
        totals = holdings_totals(self.ledger.entries, self._cur_prices, today_ymd())
        row = 0
        for ccy in sorted(totals, key=lambda c: (c != "KRW", c)):  # 원화 먼저
            t = totals[ccy]
            if not t.priced:
                continue
            dec = 0 if ccy == "KRW" else 2
            sym = {"KRW": "₩", "USD": "$"}.get(ccy, f"{ccy} ")
            gain = t.pl >= 0
            sign = "+" if gain else "−"
            prefix = (f"[{ccy}] 평가 {sym}{t.eval_amt:,.{dec}f} · "
                      f"매입 {sym}{t.priced_cost:,.{dec}f} · 손익 ")
            pl_txt = f"{sign}{sym}{abs(t.pl):,.{dec}f} ({sign}{abs(t.pl_pct):.2f}%)"
            ttk.Label(self.summary_frame, text=prefix, style="Hint.TLabel").grid(
                row=row, column=0, sticky="w")
            ttk.Label(self.summary_frame, text=pl_txt,
                      style="Gain.TLabel" if gain else "Loss.TLabel").grid(
                row=row, column=1, sticky="w")
            if t.priced != t.count:
                ttk.Label(self.summary_frame, text=f"  ※{t.count - t.priced}종목 현재가 없음",
                          style="Hint.TLabel").grid(row=row, column=2, sticky="w")
            row += 1

    def _on_tree_click(self, event) -> None:
        """체크(✓) 열 클릭 시 해당 항목 체크 토글."""
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != "#1":   # 첫 열 = check
            return
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        entry = self.ledger.entries[int(iid)]
        eid = id(entry)
        if eid in self._checked:
            self._checked.discard(eid)
        else:
            self._checked.add(eid)
        self.tree.set(iid, "check",
                      self._CHECK_ON if eid in self._checked else self._CHECK_OFF)
        return "break"

    def _check_all(self, on: bool) -> None:
        self._checked = {id(e) for e in self.ledger.entries} if on else set()
        self._reload_tree()

    def _checked_entries(self) -> list:
        return [e for e in self.ledger.entries if id(e) in self._checked]

    def _refresh_price_label(self) -> None:
        self.px_label.configure(text=f"등록된 수동 현재가: {len(self.ledger.prices)}건")

    def _make_entry(self, date, market, name, code, side, qty_s, price_s, tag,
                    *, parent=None) -> "LedgerEntry | None":
        """입력값 검증 후 LedgerEntry 생성 (추가·수정 공용). 오류 시 메시지 후 None."""
        p = parent or self
        date = str(date).strip()
        if len(date) != 8 or not date.isdigit():
            messagebox.showerror("입력 오류", "거래일은 YYYYMMDD 형식이어야 합니다.", parent=p)
            return None
        name = str(name).strip()
        code = str(code).strip()
        if not code and not name:
            messagebox.showerror("입력 오류", "종목명 또는 코드를 입력하세요.", parent=p)
            return None
        try:
            qty = int(str(qty_s).strip().replace(",", ""))
            price = float(str(price_s).strip().replace(",", ""))
        except ValueError:
            messagebox.showerror("입력 오류", "수량/단가는 숫자여야 합니다.", parent=p)
            return None
        if qty <= 0 or price < 0:
            messagebox.showerror("입력 오류", "수량은 1 이상, 단가는 0 이상이어야 합니다.", parent=p)
            return None
        return LedgerEntry(
            date=date, stk_cd=code or name, stk_nm=name or code,
            side=str(side).strip() or "매수", qty=qty, price=price,
            market=str(market).strip() or prices.DEFAULT_MARKET, tag=str(tag).strip(),
        )

    def _add_entry(self) -> None:
        new = self._make_entry(
            self.in_date.get(), self.in_market.get(), self.in_name.get(), self.in_code.get(),
            self.in_side.get(), self.in_qty.get(), self.in_price.get(), self.in_tag.get())
        if new is None:
            return
        self.ledger.entries.append(new)
        self._persist()
        self._reload_tree()
        self.in_name.set(""); self.in_code.set(""); self.in_qty.set(""); self.in_price.set("")

    def _edit_selected(self) -> None:
        sel = self.tree.selection()
        if len(sel) != 1:
            messagebox.showinfo("수정", "수정할 항목 한 개를 선택하세요.")
            return
        self._edit_entry(int(sel[0]))

    def _on_tree_double(self, event) -> None:
        if self.tree.identify_column(event.x) == "#1":   # 체크 열은 토글 전용
            return
        iid = self.tree.identify_row(event.y)
        if iid:
            self._edit_entry(int(iid))

    def _edit_entry(self, idx: int) -> None:
        if not (0 <= idx < len(self.ledger.entries)):
            return
        e = self.ledger.entries[idx]
        win = Toplevel(self)
        win.title("항목 수정")
        win.transient(self.winfo_toplevel())
        win.resizable(False, False)
        v = {
            "date": StringVar(value=e.date), "market": StringVar(value=e.market),
            "name": StringVar(value=e.stk_nm), "code": StringVar(value=e.stk_cd),
            "side": StringVar(value=e.side), "qty": StringVar(value=str(e.qty)),
            "price": StringVar(value=_fmt_price(e.price)), "tag": StringVar(value=e.tag),
        }
        fields = [
            ("거래일 (YYYYMMDD)", "date", None),
            ("시장", "market", prices.market_names()),
            ("종목명", "name", None),
            ("코드/티커", "code", None),
            ("구분", "side", ("매수", "매도")),
            ("수량", "qty", None),
            ("단가", "price", None),
            ("태그", "tag", None),
        ]
        win.columnconfigure(1, weight=1)
        for i, (lbl, key, combo) in enumerate(fields):
            ttk.Label(win, text=lbl).grid(row=i, column=0, sticky="w", padx=PAD, pady=3)
            if combo is not None:
                ttk.Combobox(win, textvariable=v[key], values=combo, state="readonly",
                             width=16).grid(row=i, column=1, padx=PAD, pady=3, sticky="we")
            else:
                ttk.Entry(win, textvariable=v[key], width=18).grid(
                    row=i, column=1, padx=PAD, pady=3, sticky="we")

        def save() -> None:
            new = self._make_entry(
                v["date"].get(), v["market"].get(), v["name"].get(), v["code"].get(),
                v["side"].get(), v["qty"].get(), v["price"].get(), v["tag"].get(), parent=win)
            if new is None:
                return
            # 기존 객체를 in-place 수정 → 체크 상태(id 기준) 보존
            e.date, e.stk_cd, e.stk_nm, e.side, e.qty, e.price, e.market, e.tag = (
                new.date, new.stk_cd, new.stk_nm, new.side, new.qty, new.price, new.market, new.tag)
            self._persist()
            self._reload_tree()
            win.destroy()

        btns = ttk.Frame(win)
        btns.grid(row=len(fields), column=0, columnspan=2, sticky="e", padx=PAD, pady=PAD)
        ttk.Button(btns, text="저장", command=save).pack(side="right")
        ttk.Button(btns, text="취소", command=win.destroy).pack(side="right", padx=(0, PAD))
        self.app._recolor_classic(win)
        win.grab_set()

    def _bulk_paste(self) -> None:
        """여러 행을 한 번에 붙여넣어 원장에 추가 (엑셀/스프레드시트 복사 지원)."""
        win = Toplevel(self)
        win.title("여러 줄 붙여넣기")
        win.transient(self.winfo_toplevel())
        win.geometry("640x420")
        ttk.Label(
            win,
            text="한 줄당 한 항목 · 컬럼 순서: 거래일  시장  종목명  코드  구분  수량  단가  (태그=선택)\n"
                 "탭 또는 콤마로 구분 (엑셀에서 복사하면 탭 구분).\n"
                 "예) 20260115\tKOSPI\t삼성전자\t005930\t매수\t10\t70000\t장투",
            justify="left", style="Hint.TLabel",
        ).pack(anchor="w", padx=PAD, pady=(PAD, 2))
        txt = ScrolledText(win, height=14, font=("Menlo", 11))
        txt.pack(fill="both", expand=True, padx=PAD, pady=2)
        try:  # 클립보드 내용 미리 채우기
            txt.insert("1.0", self.clipboard_get())
        except Exception:  # noqa: BLE001  클립보드 비었거나 텍스트 아님
            pass
        txt.focus_set()

        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=PAD, pady=(2, PAD))

        def do_import() -> None:
            entries, errs = parse_bulk_entries(txt.get("1.0", "end"))
            if entries:
                self.ledger.entries.extend(entries)
                self._persist()
                self._reload_tree()
            msg = f"{len(entries)}건 추가됨."
            if errs:
                shown = "\n".join(errs[:12])
                more = f"\n… 외 {len(errs) - 12}건" if len(errs) > 12 else ""
                msg += f"\n\n건너뛴 {len(errs)}건:\n{shown}{more}"
            messagebox.showinfo("일괄 추가", msg, parent=win)
            if entries and not errs:
                win.destroy()

        ttk.Button(btns, text="가져오기", command=do_import).pack(side="right")
        ttk.Button(btns, text="닫기", command=win.destroy).pack(side="right", padx=(0, PAD))
        self.app._recolor_classic(win)

    def _upload_excel(self) -> None:
        """.xlsx/.csv 파일을 골라 원장에 일괄 추가."""
        path = filedialog.askopenfilename(
            title="원장 파일 선택 (.xlsx / .csv)",
            filetypes=[("엑셀/CSV", "*.xlsx *.xls *.csv"), ("모든 파일", "*.*")],
        )
        if not path:
            return
        try:
            entries, errs = parse_excel_entries(path)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("업로드 실패", str(e))
            return
        if entries:
            self.ledger.entries.extend(entries)
            self._persist()
            self._reload_tree()
        msg = f"{len(entries)}건 추가됨."
        if errs:
            shown = "\n".join(errs[:12])
            more = f"\n… 외 {len(errs) - 12}건" if len(errs) > 12 else ""
            msg += f"\n\n건너뛴 {len(errs)}건:\n{shown}{more}"
        elif not entries:
            msg = "추가할 항목이 없습니다. 파일 형식/컬럼을 확인하세요."
        messagebox.showinfo("엑셀 업로드", msg)

    def _download_sample(self) -> None:
        """업로드용 샘플 .xlsx 저장."""
        path = filedialog.asksaveasfilename(
            title="샘플 저장",
            defaultextension=".xlsx",
            initialfile="invest_retrospect_원장_샘플.xlsx",
            filetypes=[("엑셀", "*.xlsx")],
        )
        if not path:
            return
        try:
            out = write_sample_xlsx(path)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("샘플 생성 실패", str(e))
            return
        if messagebox.askyesno("샘플 저장 완료", f"{out}\n\n파일을 여시겠습니까?"):
            _open_path(out)

    # ── 현재가 조회 ────────────────────────────────────────────────────────
    def _fetch_prices(self) -> None:
        """원장의 모든 종목 현재가를 조회해 '현재가' 열에 표시 (해외=Yahoo, 국내=설정 API)."""
        self.app.flush_save()
        if not self.ledger.entries:
            messagebox.showinfo("원장 비어 있음", "먼저 원장 항목을 추가하세요.")
            return
        try:  # broker 를 manual 로 강제 → 활성 증권사 키 검증 우회, 국내 API 검증만 적용
            cfg = config_from_settings(replace(self.app.settings, broker="manual"))
        except RuntimeError as e:
            messagebox.showerror("설정 오류", str(e))
            return
        # 원장에 등장하는 모든 (코드, 시장) — resolve_current_prices 가 중복 제거.
        symbols = [(e.stk_cd, e.market) for e in self.ledger.entries if e.stk_cd]
        self.fetch_px_btn.configure(state="disabled")
        self._log("[현재가] 조회 시작...")
        threading.Thread(
            target=self._fetch_prices_worker, args=(cfg, symbols), daemon=True
        ).start()

    def _fetch_prices_worker(self, cfg, symbols) -> None:
        try:
            prices_map = resolve_current_prices(
                cfg, symbols, self.ledger.prices, do_fetch=True, log=self._log)
        except Exception as e:  # noqa: BLE001
            self._log(f"[error] 현재가 조회 실패: {e}")
            self.after(0, self._on_fetch_prices_done, None, e)
            return
        self.after(0, self._on_fetch_prices_done, prices_map, None)

    def _on_fetch_prices_done(self, prices_map, exc) -> None:
        self.fetch_px_btn.configure(state="normal")
        if exc is not None:
            messagebox.showerror("현재가 조회 실패", str(exc))
            return
        self._cur_prices = prices_map or {}
        self._reload_tree()
        # 빈칸이 조용히 남지 않도록 성공/실패 종목 수를 명시한다.
        codes = {e.stk_cd for e in self.ledger.entries if e.stk_cd}
        got = sum(1 for c in codes if c in self._cur_prices)
        miss = len(codes) - got
        if codes and got == 0:
            self._log("[현재가] 한 건도 가져오지 못했습니다 — 로그의 [warn] 원인을 확인하세요.")
            messagebox.showwarning(
                "현재가 조회 실패",
                "시세를 한 건도 가져오지 못했습니다.\n\n"
                "· 사내망/프록시가 Yahoo Finance 접속을 차단하거나\n"
                "· 증권사(키움/한투) 인증이 실패했을 수 있습니다.\n\n"
                "[매매일지 생성]의 '국내 시세 API'를 '야후'로 바꾸거나, "
                "'수동 현재가'에 직접 입력해 보세요. 자세한 원인은 로그 창을 확인하세요.")
        else:
            msg = f"[현재가] {got}/{len(codes)}종목 조회 완료."
            if miss:
                msg += f" ({miss}종목 실패 → 빈칸/수동값)"
            self._log(msg)

    def _delete_selected(self) -> None:
        sel = set(self.tree.selection())
        if not sel:
            return
        keep = [e for i, e in enumerate(self.ledger.entries) if str(i) not in sel]
        self.ledger.entries = keep
        self._persist()
        self._reload_tree()

    def _set_price(self) -> None:
        code = self.px_code.get().strip()
        if not code:
            return
        try:
            self.ledger.prices[code] = float(self.px_value.get().strip().replace(",", ""))
        except ValueError:
            messagebox.showerror("입력 오류", "현재가는 숫자여야 합니다.")
            return
        self._persist()
        self._refresh_price_label()
        self.px_code.set(""); self.px_value.set("")

    # ── 생성 ─────────────────────────────────────────────────────────────
    def _append_log(self, msg: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _log(self, msg: str) -> None:
        self.after(0, self._append_log, msg)

    def _on_generate(self, checked_only: bool = False) -> None:
        self.app.flush_save()
        if not self.ledger.entries:
            messagebox.showinfo("원장 비어 있음", "먼저 원장 항목을 추가하세요.")
            return
        entries = None
        if checked_only:
            entries = self._checked_entries()
            if not entries:
                messagebox.showinfo("선택 없음", "체크된 항목이 없습니다. ✓ 열을 클릭해 선택하세요.")
                return
        try:  # broker 를 manual 로 강제 → 활성 증권사 키 검증 우회, 국내 API 검증만 적용
            cfg = config_from_settings(replace(self.app.settings, broker="manual"))
        except RuntimeError as e:
            messagebox.showerror("설정 오류", str(e))
            return
        ymd = self.gen_date.get().strip()
        fmt = self.gen_fmt.get()
        do_fetch = self.gen_fetch.get()

        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        if checked_only:
            self._log(f"[info] 체크된 {len(entries)}개 항목만 사용")
        for btn in (self.gen_btn, self.gen_checked_btn, self.open_md_btn,
                    self.open_pdf_btn, self.open_dir_btn):
            btn.configure(state="disabled")
        self._result = None
        threading.Thread(
            target=self._worker, args=(cfg, ymd, fmt, do_fetch, entries), daemon=True
        ).start()

    def _worker(self, cfg, ymd, fmt, do_fetch, entries) -> None:
        try:
            result = run_manual_journal(
                cfg, ymd, fmt, do_fetch=do_fetch, entries=entries,
                ledger=self.ledger,
                out_label=self.manual_tab.out_label_for(self.account_name),
                account_no=f"수동 원장 · {self.account_name}",
                log=self._log)
            self.after(0, self._on_done, result)
        except Exception as e:  # noqa: BLE001
            self._log(f"[error] {e}")
            self._log(traceback.format_exc())
            self.after(0, self._on_error, e)

    def _on_done(self, result: JournalResult) -> None:
        self._result = result
        self.gen_btn.configure(state="normal")
        self.gen_checked_btn.configure(state="normal")
        self.open_dir_btn.configure(state="normal")
        if result.md_path:
            self.open_md_btn.configure(state="normal")
        if result.pdf_path:
            self.open_pdf_btn.configure(state="normal")

    def _on_error(self, exc: Exception) -> None:
        self.gen_btn.configure(state="normal")
        self.gen_checked_btn.configure(state="normal")
        messagebox.showerror("생성 실패", str(exc))

    def _open(self, which: str) -> None:
        if not self._result:
            return
        path = self._result.md_path if which == "md" else self._result.pdf_path
        if path and path.is_file():
            _open_path(path)

    def _open_dir(self) -> None:
        if self._result:
            _open_path(self._result.json_path.parent)


class ManualLedgerTab(ttk.Frame):
    """다계좌 수동 원장 컨테이너 — 계좌별 탭(＋로 추가) + 전 계좌 일괄 생성.

    각 계좌 탭은 AccountLedgerFrame 으로, 자기 계좌만 생성한다. 컨테이너는
    계좌 추가/이름변경/삭제, 전체 백업/복구, 전 계좌 개별·합산 생성을 담당한다.
    """

    _PLUS = "＋"

    def __init__(self, master: ttk.Notebook, app: "App") -> None:
        super().__init__(master, padding=PAD)
        self.app = app
        self.frames: dict[str, AccountLedgerFrame] = {}
        self._building = False
        self._last_dir: Path | None = None
        self._db_ok = True                       # DB 모드에서 마지막 로드 성공 여부
        self._source_key = self._current_source_key()
        self.book: LedgerBook = self._load_source_book()
        self._build()
        self._rebuild_tabs()

    # ── 저장소 모드 ─────────────────────────────────────────────────────────
    def _db_settings(self) -> "ledger_db.DbSettings | None":
        """DB 모드면 접속정보, 오프라인이면 None."""
        if (self.app.settings.manual_ledger_mode or "offline") != "db":
            return None
        return ledger_db.DbSettings.from_settings(self.app.settings)

    def _current_source_key(self) -> tuple:
        db = self._db_settings()
        return ("db", *db.key()) if db else ("offline",)

    def _load_source_book(self) -> LedgerBook:
        db = self._db_settings()
        if db is None:
            self._db_ok = True
            return load_book()
        try:
            book = ledger_db.load_book(db)
        except Exception as e:  # noqa: BLE001  접속/드라이버/SQL 오류 전반
            self._db_ok = False
            messagebox.showerror(
                "DB 불러오기 실패",
                f"DB 에서 원장을 불러오지 못했습니다.\n{e}\n\n"
                "[설정] 탭에서 접속 정보를 확인한 뒤 '다시 불러오기' 하세요.")
            return LedgerBook()
        self._db_ok = True
        return book

    def reload_if_source_changed(self) -> None:
        """수동 원장 탭이 보일 때 호출 — 저장 모드/DB 설정이 바뀌었으면 다시 로드."""
        self.app.flush_save()                    # 디바운스 중인 설정 즉시 반영
        key = self._current_source_key()
        if key == self._source_key and self._db_ok:
            return
        self._reload_now()

    def _reload_now(self) -> None:
        self.app.flush_save()
        self._source_key = self._current_source_key()
        self.book = self._load_source_book()
        self._rebuild_tabs()

    # ── 영속화 ─────────────────────────────────────────────────────────────
    def _save_book(self) -> None:
        db = self._db_settings()
        if db is None:
            try:
                save_book(self.book)
            except OSError as e:
                messagebox.showerror("저장 실패", str(e))
            return
        if not self._db_ok:
            # 로드 실패 상태에서 저장하면 원격 데이터를 빈 값으로 덮어쓸 수 있어 막는다.
            messagebox.showwarning(
                "DB 저장 보류",
                "DB 에서 원장을 정상적으로 불러오지 못해 저장을 보류합니다.\n"
                "[설정] 탭에서 접속을 확인한 뒤 '다시 불러오기' 하세요.")
            return
        try:
            ledger_db.save_book(self.book, db)
        except Exception as e:  # noqa: BLE001  접속/드라이버/SQL 오류 전반
            messagebox.showerror("DB 저장 실패", str(e))

    def out_label_for(self, name: str) -> str:
        """계좌명 → 출력 파일명 접미사(파일명 안전 문자만)."""
        safe = "".join(c if c.isalnum() else "_" for c in name).strip("_")
        return f"manual_{safe or 'acct'}"

    # ── UI ─────────────────────────────────────────────────────────────────
    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # 계좌 관리 툴바
        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="we", pady=(0, PAD))
        ttk.Label(bar, text="계좌").pack(side="left", padx=(0, PAD))
        ttk.Button(bar, text="＋ 계좌 추가", command=self._add_account).pack(side="left")
        ttk.Button(bar, text="이름변경", command=self._rename_account).pack(side="left", padx=(PAD, 0))
        ttk.Button(bar, text="계좌 삭제", command=self._delete_account).pack(side="left", padx=(PAD, 0))
        ttk.Button(bar, text="전체 백업...", command=self._backup_book).pack(side="left", padx=(PAD * 2, 0))
        ttk.Button(bar, text="전체 복구...", command=self._restore_book).pack(side="left", padx=(PAD, 0))
        ttk.Button(bar, text="🔄 다시 불러오기", command=self._reload_now).pack(side="left", padx=(PAD, 0))

        # 계좌 탭 (＋ 탭 포함)
        self.nb = ttk.Notebook(self)
        self.nb.grid(row=1, column=0, sticky="nsew")
        self.nb.bind("<<NotebookTabChanged>>", self._on_sub_tab)

        # 전 계좌 일괄 생성
        gen = ttk.LabelFrame(self, text="전 계좌 매매일지 생성", padding=PAD)
        gen.grid(row=2, column=0, sticky="we", pady=(PAD, 0))
        self.gen_date = StringVar(value=today_ymd())
        self.gen_fmt = StringVar(value="md")
        self.gen_fetch = BooleanVar(value=True)
        ttk.Label(gen, text="기준일(YYYYMMDD)").grid(row=0, column=0, padx=2, sticky="w")
        ttk.Entry(gen, textvariable=self.gen_date, width=10).grid(row=0, column=1, padx=2)
        ttk.Radiobutton(gen, text="MD", variable=self.gen_fmt, value="md").grid(row=0, column=2, padx=2)
        ttk.Radiobutton(gen, text="PDF", variable=self.gen_fmt, value="pdf").grid(row=0, column=3, padx=2)
        ttk.Radiobutton(gen, text="둘 다", variable=self.gen_fmt, value="both").grid(row=0, column=4, padx=2)
        ttk.Checkbutton(gen, text="자동 시세조회", variable=self.gen_fetch).grid(row=0, column=5, padx=(PAD, 2))
        self.batch_indiv_btn = ttk.Button(
            gen, text="전 계좌 개별생성", command=lambda: self._generate_all(False))
        self.batch_indiv_btn.grid(row=1, column=0, columnspan=2, sticky="w", pady=(PAD, 0))
        self.batch_combined_btn = ttk.Button(
            gen, text="전 계좌 합산생성", command=lambda: self._generate_all(True))
        self.batch_combined_btn.grid(row=1, column=2, columnspan=2, sticky="w", padx=2, pady=(PAD, 0))
        self.open_dir_btn = ttk.Button(gen, text="폴더 열기", command=self._open_dir, state="disabled")
        self.open_dir_btn.grid(row=1, column=4, padx=2, pady=(PAD, 0))
        ttk.Label(gen, text="개별=계좌마다 별도 일지 · 합산=모든 계좌를 합쳐 1개(같은 종목은 합산)",
                  style="Hint.TLabel").grid(row=2, column=0, columnspan=6, sticky="w", pady=(2, 0))

        # 공용 로그
        self.log = ScrolledText(self, height=6, font=("Menlo", 10))
        self.log.grid(row=3, column=0, sticky="we", pady=(PAD, 0))
        self.log.configure(state="disabled")

    # ── 탭 관리 ────────────────────────────────────────────────────────────
    def _rebuild_tabs(self) -> None:
        self._building = True
        for tab in list(self.nb.tabs()):
            self.nb.forget(tab)
        self.frames.clear()
        for name, ledger in self.book.accounts.items():
            fr = AccountLedgerFrame(self.nb, self.app, self, name, ledger)
            self.frames[name] = fr
            self.nb.add(fr, text=name)
        self._plus_frame = ttk.Frame(self.nb)
        self.nb.add(self._plus_frame, text=self._PLUS)
        self._building = False
        self._select_account(self.book.active)

    def _select_account(self, name: str) -> None:
        fr = self.frames.get(name)
        if fr is not None:
            self.nb.select(fr)

    def _on_sub_tab(self, _event=None) -> None:
        if self._building:
            return
        try:
            cur = self.nb.nametowidget(self.nb.select())
        except Exception:  # noqa: BLE001
            return
        if cur is getattr(self, "_plus_frame", None):
            self._add_account()
            return
        for name, fr in self.frames.items():
            if fr is cur:
                if self.book.active != name:
                    self.book.active = name
                    self._save_book()
                break

    def _ask_name(self, title: str, initial: str = "") -> str | None:
        name = simpledialog.askstring(title, "계좌 이름:", initialvalue=initial, parent=self)
        if name is None:
            return None
        name = name.strip()
        if not name:
            return None
        if name == self._PLUS or name in self.book.accounts:
            messagebox.showerror(title, "이미 있거나 사용할 수 없는 이름입니다.")
            return None
        return name

    def _add_account(self) -> None:
        name = self._ask_name("계좌 추가")
        if name is None:
            self._select_account(self.book.active)   # 취소·실패 → 원래 탭 복귀
            return
        self.book.accounts[name] = Ledger()
        self.book.active = name
        self._save_book()
        self._rebuild_tabs()

    def _rename_account(self) -> None:
        old = self.book.active
        name = self._ask_name("계좌 이름변경", initial=old)
        if name is None:
            return
        # 삽입 순서(탭 순서) 보존하며 키만 교체
        self.book.accounts = {
            (name if k == old else k): v for k, v in self.book.accounts.items()
        }
        self.book.active = name
        self._save_book()
        self._rebuild_tabs()

    def _delete_account(self) -> None:
        if len(self.book.accounts) <= 1:
            messagebox.showinfo("계좌 삭제", "마지막 계좌는 삭제할 수 없습니다.")
            return
        name = self.book.active
        if not messagebox.askyesno("계좌 삭제", f"'{name}' 계좌와 원장을 삭제할까요?"):
            return
        del self.book.accounts[name]
        self.book.active = next(iter(self.book.accounts))
        self._save_book()
        self._rebuild_tabs()

    # ── 전체 백업/복구 ──────────────────────────────────────────────────────
    def _backup_book(self) -> None:
        path = filedialog.asksaveasfilename(
            title="원장 전체 백업 저장",
            defaultextension=".json",
            initialfile="invest-retrospect-ledger.json",
            filetypes=[("JSON 파일", "*.json"), ("모든 파일", "*.*")],
        )
        if not path:
            return
        try:
            export_book(self.book, Path(path))
        except OSError as e:
            messagebox.showerror("백업 실패", str(e))
            return
        n_ent = sum(len(l.entries) for l in self.book.accounts.values())
        messagebox.showinfo(
            "백업 완료",
            f"계좌 {len(self.book.accounts)}개 · 거래 {n_ent}건을 백업했습니다.\n{path}",
        )

    def _restore_book(self) -> None:
        path = filedialog.askopenfilename(
            title="원장 전체 복구 — 백업 파일 선택",
            filetypes=[("JSON 파일", "*.json"), ("모든 파일", "*.*")],
        )
        if not path:
            return
        try:
            restored = import_book(Path(path))
        except OSError as e:
            messagebox.showerror("복구 실패", f"파일을 읽을 수 없습니다.\n{e}")
            return
        except ValueError as e:
            # json.JSONDecodeError 포함 — 형식이 올바르지 않은 파일
            messagebox.showerror("복구 실패", f"올바른 원장 백업 파일이 아닙니다.\n{e}")
            return
        cur_ent = sum(len(l.entries) for l in self.book.accounts.values())
        new_ent = sum(len(l.entries) for l in restored.accounts.values())
        if not messagebox.askyesno(
            "원장 전체 복구",
            f"현재 전체 원장(계좌 {len(self.book.accounts)}개·거래 {cur_ent}건)을\n"
            f"백업 내용(계좌 {len(restored.accounts)}개·거래 {new_ent}건)으로 덮어씁니다.\n"
            "기존 원장은 자동 백업본으로 보존됩니다. 계속할까요?",
        ):
            return
        # 덮어쓰기 전 자동 백업 — 타임스탬프로 누적 보존
        if any(l.entries or l.prices for l in self.book.accounts.values()):
            from datetime import datetime
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            auto = MANUAL_LEDGER_PATH.with_name(f"manual_ledger.{stamp}.bak.json")
            try:
                export_book(self.book, auto)
            except OSError as e:
                if not messagebox.askyesno(
                    "자동 백업 실패",
                    f"기존 원장 자동 백업에 실패했습니다.\n{e}\n\n"
                    "문제가 생겨도 기존 원장을 되돌리기 어려울 수 있습니다. "
                    "그래도 복구를 계속할까요?",
                ):
                    return
        self.book = restored
        self._save_book()
        self._rebuild_tabs()
        messagebox.showinfo(
            "복구 완료",
            f"계좌 {len(restored.accounts)}개 · 거래 {new_ent}건을 복구했습니다.",
        )

    # ── 전 계좌 생성 ───────────────────────────────────────────────────────
    def _append_log(self, msg: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _log(self, msg: str) -> None:
        self.after(0, self._append_log, msg)

    def _set_batch_state(self, state: str) -> None:
        self.batch_indiv_btn.configure(state=state)
        self.batch_combined_btn.configure(state=state)

    def _generate_all(self, combined: bool) -> None:
        self.app.flush_save()
        accounts = [(n, l) for n, l in self.book.accounts.items() if l.entries]
        if not accounts:
            messagebox.showinfo("원장 비어 있음", "거래가 있는 계좌가 없습니다.")
            return
        try:  # broker 를 manual 로 강제 → 활성 증권사 키 검증 우회
            cfg = config_from_settings(replace(self.app.settings, broker="manual"))
        except RuntimeError as e:
            messagebox.showerror("설정 오류", str(e))
            return
        ymd = self.gen_date.get().strip()
        fmt = self.gen_fmt.get()
        do_fetch = self.gen_fetch.get()
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self._set_batch_state("disabled")
        self.open_dir_btn.configure(state="disabled")
        threading.Thread(
            target=self._batch_worker,
            args=(cfg, ymd, fmt, do_fetch, combined, accounts),
            daemon=True,
        ).start()

    def _batch_worker(self, cfg, ymd, fmt, do_fetch, combined, accounts) -> None:
        try:
            if combined:
                merged = Ledger()
                for _n, l in accounts:
                    merged.entries.extend(l.entries)
                    merged.prices.update(l.prices)
                self._log(f"[합산] {len(accounts)}개 계좌 · 거래 {len(merged.entries)}건 통합 생성...")
                run_manual_journal(
                    cfg, ymd, fmt, do_fetch=do_fetch, ledger=merged,
                    out_label="manual_all", account_no="수동 원장 · 전체 합산",
                    log=self._log)
                self._log("[합산] 완료")
            else:
                total = len(accounts)
                for i, (name, ledger) in enumerate(accounts, 1):
                    self._log(f"[{i}/{total}] '{name}' 계좌 생성...")
                    run_manual_journal(
                        cfg, ymd, fmt, do_fetch=do_fetch, ledger=ledger,
                        out_label=self.out_label_for(name),
                        account_no=f"수동 원장 · {name}", log=self._log)
                self._log("[개별] 전 계좌 완료")
            # 공유 상태(_last_dir)·위젯 변경은 메인 스레드에서만 수행
            self.after(0, self._on_batch_done, cfg.journal_dir)
        except Exception as e:  # noqa: BLE001
            self._log(f"[error] {e}")
            self._log(traceback.format_exc())
            self.after(0, lambda err=e: self._on_batch_error(err))

    def _on_batch_done(self, journal_dir: Path) -> None:
        self._last_dir = journal_dir
        self._set_batch_state("normal")
        self.open_dir_btn.configure(state="normal")

    def _on_batch_error(self, exc: Exception) -> None:
        self._set_batch_state("normal")
        messagebox.showerror("생성 실패", str(exc))

    def _open_dir(self) -> None:
        target = self._last_dir or self.app.settings.journal_dir
        if target:
            _open_path(Path(target))


class MarketTab(ttk.Frame):
    """시장 대시보드 — 네이버 금융 데이터 (코스피/코스닥, 20초 자동 갱신)."""

    _RANK_DEFS = (
        ("시가총액", "marketcap", "market_cap"),
        ("외국인 순매수", "deal", "foreign_buy"),
        ("외국인 순매도", "deal", "foreign_sell"),
        ("기관 순매수", "deal", "institution_buy"),
        ("기관 순매도", "deal", "institution_sell"),
        ("외국인 보유", "holding", "foreign_holding"),
    )
    _RANK_COLS = {
        "marketcap": ("순위", "종목", "코드", "현재가", "시가총액"),
        "deal": ("순위", "종목", "코드", "수량", "금액", "거래량"),
        "holding": ("순위", "종목", "코드", "현재가", "외국인비율"),
    }

    def __init__(self, master: ttk.Notebook, app: "App") -> None:
        super().__init__(master, padding=PAD)
        self.app = app
        self.market_var = StringVar(value="kospi")
        self.count_var = StringVar(value="")
        self.status_var = StringVar(value="대기 중")
        self._active = False
        self._loading = False
        self._after_id: str | None = None
        self._remaining = _MARKET_REFRESH_SEC
        self._rank_trees: dict[str, tuple[ttk.Treeview, str]] = {}
        self._build()

    # ── UI ───────────────────────────────────────────────────────────────
    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(2, weight=1)

        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, PAD))
        ttk.Label(bar, text="시장").pack(side="left", padx=(0, PAD))
        for val, lbl in (("kospi", "코스피"), ("kosdaq", "코스닥")):
            ttk.Radiobutton(bar, text=lbl, value=val, variable=self.market_var,
                            command=self._refresh).pack(side="left")
        ttk.Button(bar, text="새로고침", command=self._refresh).pack(side="left", padx=(PAD, 0))
        ttk.Label(bar, textvariable=self.count_var, style="Hint.TLabel").pack(side="left", padx=(PAD, 0))
        ttk.Label(bar, textvariable=self.status_var, style="Hint.TLabel").pack(side="right")

        # 종합지수 카드
        self.idx_box = ttk.LabelFrame(self, text="종합지수", padding=PAD)
        self.idx_box.grid(row=1, column=0, sticky="nwe", padx=(0, PAD // 2), pady=(0, PAD))
        self.idx_body = ttk.Frame(self.idx_box)
        self.idx_body.pack(fill="both", expand=True)

        # 환율·금리 카드
        self.mi_box = ttk.LabelFrame(self, text="환율 · 금리", padding=PAD)
        self.mi_box.grid(row=1, column=1, sticky="nwe", padx=(PAD // 2, 0), pady=(0, PAD))
        self.mi_body = ttk.Frame(self.mi_box)
        self.mi_body.pack(fill="both", expand=True)

        # 순위 노트북 (시총/순매매/보유)
        nb = ttk.Notebook(self)
        nb.grid(row=2, column=0, columnspan=2, sticky="nsew")
        for title, kind, key in self._RANK_DEFS:
            frame = ttk.Frame(nb, padding=2)
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(0, weight=1)
            cols = self._RANK_COLS[kind]
            tree = ttk.Treeview(frame, columns=cols, show="headings", height=12)
            for c in cols:
                tree.heading(c, text=c)
                anchor = "w" if c == "종목" else ("center" if c in ("순위", "코드") else "e")
                width = 150 if c == "종목" else (120 if c in ("시가총액",) else 80)
                tree.column(c, width=width, anchor=anchor)
            tree.tag_configure("up", foreground=_CUR.up)
            tree.tag_configure("down", foreground=_CUR.down)
            tree.grid(row=0, column=0, sticky="nsew")
            sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            sb.grid(row=0, column=1, sticky="ns")
            tree.configure(yscrollcommand=sb.set)
            nb.add(frame, text=title)
            self._rank_trees[key] = (tree, kind)

    def apply_theme(self) -> None:
        """테마 변경 시 트리의 상승/하락 색상을 갱신 (기존 행도 즉시 반영)."""
        for tree, _kind in self._rank_trees.values():
            tree.tag_configure("up", foreground=_CUR.up)
            tree.tag_configure("down", foreground=_CUR.down)
        if self._active:   # 보이는 상태면 카드 색도 새로 그린다
            self._refresh()

    # ── 가시성/자동 갱신 ───────────────────────────────────────────────────
    def set_active(self, active: bool) -> None:
        """노트북에서 이 탭이 선택됐을 때만 자동 갱신 루프를 돌린다."""
        if active and not self._active:
            self._active = True
            self._refresh()
            self._remaining = _MARKET_REFRESH_SEC
            self._schedule_tick()
        elif not active and self._active:
            self._active = False
            if self._after_id is not None:
                self.after_cancel(self._after_id)
                self._after_id = None
            self.count_var.set("")

    def _schedule_tick(self) -> None:
        self._after_id = self.after(1000, self._tick)

    def _tick(self) -> None:
        if not self._active:
            return
        self._remaining -= 1
        if self._remaining <= 0:
            self._remaining = _MARKET_REFRESH_SEC
            self._refresh()
        self.count_var.set(f"다음 갱신 {self._remaining}s")
        self._schedule_tick()

    # ── 데이터 ─────────────────────────────────────────────────────────────
    def _refresh(self) -> None:
        if self._loading:
            return
        self._loading = True
        self.status_var.set("불러오는 중…")
        mkt = market.KOSPI if self.market_var.get() == "kospi" else market.KOSDAQ
        threading.Thread(target=self._worker, args=(mkt,), daemon=True).start()

    def _worker(self, mkt: "market.Market") -> None:
        try:
            data = market.load_all(mkt)
            self.after(0, self._apply, data)
        except Exception as e:  # noqa: BLE001
            self.after(0, self._on_error, e)

    def _on_error(self, exc: Exception) -> None:
        self._loading = False
        self.status_var.set(f"실패: {exc}")

    def _apply(self, data: "market.DashboardData") -> None:
        self._loading = False
        self._render_indices(data.indices)
        self._render_mindex(data.market_index)
        for key, (tree, kind) in self._rank_trees.items():
            self._render_rank(tree, kind, getattr(data, key))
        from datetime import datetime
        self.status_var.set("갱신 " + datetime.now().strftime("%H:%M:%S"))

    def _render_indices(self, items: list) -> None:
        for w in self.idx_body.winfo_children():
            w.destroy()
        if not items:
            ttk.Label(self.idx_body, text="데이터 없음", foreground="#999").grid(row=0, column=0)
            return
        for r, q in enumerate(items):
            color = _dir_color(q.direction)
            ttk.Label(self.idx_body, text=q.name, width=8).grid(row=r, column=0, sticky="w", pady=1)
            ttk.Label(self.idx_body, text=q.price, foreground=color, width=12, anchor="e").grid(
                row=r, column=1, sticky="e", padx=PAD)
            ttk.Label(self.idx_body, text=f"{q.change} ({q.rate}%)", foreground=color).grid(
                row=r, column=2, sticky="w")

    def _render_mindex(self, items: list) -> None:
        for w in self.mi_body.winfo_children():
            w.destroy()
        if not items:
            ttk.Label(self.mi_body, text="데이터 없음", foreground="#999").grid(row=0, column=0)
            return
        for r, m in enumerate(items):
            color = _dir_color(m.direction)
            ttk.Label(self.mi_body, text=m.name, width=12).grid(row=r, column=0, sticky="w", pady=1)
            ttk.Label(self.mi_body, text=m.value, foreground=color, width=10, anchor="e").grid(
                row=r, column=1, sticky="e", padx=PAD)
            ttk.Label(self.mi_body, text=m.change, foreground=color).grid(row=r, column=2, sticky="w")

    def _render_rank(self, tree: ttk.Treeview, kind: str, items: list) -> None:
        tree.delete(*tree.get_children())
        for it in items:
            if kind == "marketcap":
                vals = (it.rank, it.name, it.code, it.price, it.sub)
                tag = it.direction
            elif kind == "deal":
                vals = (it.rank, it.name, it.code, it.qty, it.amount, it.volume)
                tag = _sign_dir(it.amount)
            else:  # holding
                vals = (it.rank, it.name, it.code, it.price, it.sub)
                tag = market.FLAT
            tree.insert("", "end", values=vals, tags=(tag,))


class App(Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("매매 회고 (키움 / 한투 / LS / 메리츠 / 대신 / 수동)")
        self.geometry("880x840")
        self.minsize(720, 700)
        self._apply_icon()
        self._enable_clipboard_shortcuts()
        # 테마 엔진: sv-ttk(Sun Valley) 우선, 실패 시 clam 폴백 (_apply_theme 참고)
        self._style = ttk.Style(self)

        self.settings = load_settings()
        # 모든 Settings 필드를 공유 StringVar 로 (탭 간 동기화 + 자동 저장 대상)
        self.setting_vars: dict[str, StringVar] = {
            f.name: StringVar(value=getattr(self.settings, f.name))
            for f in fields(Settings)
        }

        self._save_after_id: str | None = None
        self.status_var = StringVar(value="✓ 저장됨")
        for var in self.setting_vars.values():
            var.trace_add("write", lambda *_: self._on_setting_changed())

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=PAD, pady=(PAD, 0))
        self.nb = nb
        self.journal_tab = JournalTab(nb, self)
        self.manual_tab = ManualLedgerTab(nb, self)
        self.market_tab = MarketTab(nb, self)
        self.settings_tab = SettingsTab(nb, self)
        nb.add(self.journal_tab, text="매매일지")
        nb.add(self.manual_tab, text="수동 원장")
        nb.add(self.market_tab, text="시장")
        nb.add(self.settings_tab, text="설정")
        # 시장 탭은 보일 때만 자동 갱신 (불필요한 네트워크 요청 방지)
        nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        status_bar = ttk.Frame(self)
        status_bar.pack(fill="x", padx=PAD, pady=(0, PAD))
        ttk.Label(status_bar, textvariable=self.status_var, style="Hint.TLabel").pack(side="right")

        # 선택된 broker 의 키가 비어 있으면 설정 탭으로 시작
        if not self._active_broker_ready():
            nb.select(self.settings_tab)

        # 저장된 테마 적용 (모든 위젯 생성 후 — 클래식 위젯 재색칠 포함)
        initial = self.settings.theme if self.settings.theme in _PALETTES else "light"
        self._apply_theme(initial)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _apply_theme(self, name: str) -> None:
        """Sun Valley(sv-ttk) 테마 적용 + 클래식 Tk 위젯 색 동기화.

        sv-ttk 가 ttk 위젯을 Windows 11 Fluent 룩으로 그린다. 그 실제 색을
        style.lookup 으로 읽어 ttk 가 아닌 위젯(Canvas/Text/Toplevel)에도 맞춘다.
        sv-ttk 임포트 실패 시엔 clam 기반 폴백으로 동작한다.
        """
        global _CUR
        if name not in _PALETTES:
            name = "light"
        pal = _PALETTES[name]
        st = self._style
        # 'lite' 엔진이면 sv-ttk(이미지 기반, 리사이즈 무거움)를 건너뛰고
        # 가벼운 clam 으로 직행한다.
        lite = (self.setting_vars["theme_engine"].get() or "auto") == "lite"
        try:
            if lite:
                raise RuntimeError("lite theme engine")
            import sv_ttk
            sv_ttk.set_theme(name)
            self.update_idletasks()   # 테마 색이 스타일 DB에 반영되도록 한 박자 대기
            # 테마가 실제로 쓰는 색을 읽어 클래식 위젯과 정확히 맞춘다.
            pal.bg = st.lookup("TFrame", "background") or st.lookup(".", "background") or pal.bg
            pal.fg = st.lookup("TLabel", "foreground") or st.lookup(".", "foreground") or pal.fg
            pal.entry_bg = (st.lookup("TEntry", "fieldbackground")
                            or st.lookup(".", "fieldbackground") or pal.entry_bg)
            st.configure("Hint.TLabel", background=pal.bg, foreground=pal.hint)
            st.configure("Gain.TLabel", background=pal.bg, foreground=pal.up)
            st.configure("Loss.TLabel", background=pal.bg, foreground=pal.down)
        except Exception:  # noqa: BLE001  sv-ttk 미설치 등 — clam 폴백
            self._apply_clam_theme(pal)

        # 콤보박스 드롭다운(클래식 Listbox) 색
        self.option_add("*TCombobox*Listbox.background", pal.entry_bg)
        self.option_add("*TCombobox*Listbox.foreground", pal.fg)
        self.option_add("*TCombobox*Listbox.selectBackground", pal.select_bg)
        self.option_add("*TCombobox*Listbox.selectForeground", pal.select_fg)

        _CUR = pal
        self.configure(bg=pal.bg)
        self._recolor_classic(self)
        if hasattr(self, "market_tab"):
            self.market_tab.apply_theme()

    def _apply_clam_theme(self, pal: "_Palette") -> None:
        """sv-ttk 를 못 쓸 때의 폴백 — clam 테마를 팔레트 색으로 직접 칠한다."""
        st = self._style
        try:
            st.theme_use("clam")
        except TclError:
            return
        st.configure(".", background=pal.bg, foreground=pal.fg,
                     fieldbackground=pal.entry_bg, bordercolor=pal.border,
                     lightcolor=pal.border, darkcolor=pal.border,
                     troughcolor=pal.trough, insertcolor=pal.fg)
        st.configure("TFrame", background=pal.bg)
        st.configure("TLabel", background=pal.bg, foreground=pal.fg)
        st.configure("Hint.TLabel", background=pal.bg, foreground=pal.hint)
        st.configure("Gain.TLabel", background=pal.bg, foreground=pal.up)
        st.configure("Loss.TLabel", background=pal.bg, foreground=pal.down)
        st.configure("TLabelframe", background=pal.bg, bordercolor=pal.border)
        st.configure("TLabelframe.Label", background=pal.bg, foreground=pal.fg)
        st.configure("TButton", background=pal.button, foreground=pal.fg,
                     bordercolor=pal.border, focuscolor=pal.border)
        st.map("TButton",
               background=[("pressed", pal.button_active), ("active", pal.button_active),
                           ("disabled", pal.bg)],
               foreground=[("disabled", pal.disabled_fg)])
        for w in ("TCheckbutton", "TRadiobutton"):
            st.configure(w, background=pal.bg, foreground=pal.fg, indicatorcolor=pal.entry_bg)
            st.map(w, background=[("active", pal.bg)],
                   foreground=[("disabled", pal.disabled_fg)],
                   indicatorcolor=[("selected", pal.fg), ("disabled", pal.bg)])
        st.configure("TEntry", fieldbackground=pal.entry_bg, foreground=pal.fg,
                     insertcolor=pal.fg, bordercolor=pal.border)
        st.map("TEntry", fieldbackground=[("disabled", pal.bg), ("readonly", pal.bg)],
               foreground=[("disabled", pal.disabled_fg)])
        st.configure("TCombobox", fieldbackground=pal.entry_bg, foreground=pal.fg,
                     background=pal.button, arrowcolor=pal.fg, bordercolor=pal.border)
        st.map("TCombobox",
               fieldbackground=[("readonly", pal.entry_bg), ("disabled", pal.bg)],
               foreground=[("disabled", pal.disabled_fg)],
               selectbackground=[("readonly", pal.entry_bg)],
               selectforeground=[("readonly", pal.fg)])
        st.configure("TScrollbar", background=pal.button, troughcolor=pal.trough,
                     bordercolor=pal.border, arrowcolor=pal.fg)
        st.map("TScrollbar", background=[("active", pal.button_active)])
        st.configure("TNotebook", background=pal.bg, bordercolor=pal.border)
        st.configure("TNotebook.Tab", background=pal.tab_bg, foreground=pal.fg,
                     padding=[10, 4], bordercolor=pal.border)
        st.map("TNotebook.Tab", background=[("selected", pal.bg)],
               foreground=[("selected", pal.fg), ("disabled", pal.disabled_fg)])
        st.configure("Treeview", background=pal.entry_bg, fieldbackground=pal.entry_bg,
                     foreground=pal.fg, bordercolor=pal.border)
        st.map("Treeview", background=[("selected", pal.select_bg)],
               foreground=[("selected", pal.select_fg)])
        st.configure("Treeview.Heading", background=pal.heading_bg, foreground=pal.fg,
                     bordercolor=pal.border)
        st.map("Treeview.Heading", background=[("active", pal.button_active)])

    def _recolor_classic(self, w) -> None:
        """ttk 가 아닌 클래식 Tk 위젯(Canvas/Text/Toplevel)을 팔레트 색으로 칠한다."""
        cls = w.winfo_class()
        try:
            if cls == "Canvas":
                w.configure(bg=_CUR.bg, highlightthickness=0)
            elif cls in ("Text", "Listbox"):
                w.configure(bg=_CUR.entry_bg, fg=_CUR.fg, insertbackground=_CUR.fg,
                            selectbackground=_CUR.select_bg, selectforeground=_CUR.select_fg,
                            highlightthickness=0)
            elif cls in ("Toplevel", "Tk"):
                w.configure(bg=_CUR.bg)
        except TclError:
            pass
        for c in w.winfo_children():
            self._recolor_classic(c)

    def set_theme(self, name: str) -> None:
        """설정 탭 토글에서 호출 — 테마를 적용하고 설정에 저장(자동 저장 트리거)."""
        self.setting_vars["theme"].set(name)
        self._apply_theme(name)

    def set_theme_engine(self, lite: bool) -> None:
        """테마 엔진 전환(sv-ttk ↔ clam) — 자동 저장 트리거 후 즉시 재적용."""
        self.setting_vars["theme_engine"].set("lite" if lite else "auto")
        self._apply_theme(self.setting_vars["theme"].get())

    def _on_tab_changed(self, _event=None) -> None:
        try:
            current = self.nb.nametowidget(self.nb.select())
        except Exception:  # noqa: BLE001
            return
        self.market_tab.set_active(current is self.market_tab)
        if current is self.manual_tab:
            self.manual_tab.reload_if_source_changed()

    def _active_broker_ready(self) -> bool:
        b = self.settings.broker or "kiwoom"
        if b == "manual":
            return True   # 수동 입력은 키 불필요 — [수동 원장] 탭 사용
        if b == "kiwoom":
            return bool(self.settings.kiwoom_app_key and self.settings.kiwoom_secret_key)
        if b == "kis":
            return bool(self.settings.kis_app_key and self.settings.kis_app_secret)
        if b == "ls":
            return bool(self.settings.ls_app_key and self.settings.ls_app_secret)
        if b == "meritz":
            return bool(self.settings.meritz_app_key and self.settings.meritz_app_secret)
        if b == "daishin":
            # CYBOS Plus 는 HTS 로그인이 인증을 대신 — 계좌번호만 있으면 OK.
            return bool(self.settings.daishin_account_no)
        return False

    def _on_setting_changed(self) -> None:
        self.status_var.set("● 수정됨")
        if self._save_after_id is not None:
            self.after_cancel(self._save_after_id)
        self._save_after_id = self.after(AUTOSAVE_DELAY_MS, self._do_save)

    def _do_save(self) -> None:
        self._save_after_id = None
        new = Settings(**{k: v.get() for k, v in self.setting_vars.items()})
        try:
            save_settings(new)
        except OSError as e:
            self.status_var.set(f"✗ 저장 실패: {e}")
            return
        self.settings = new
        self.status_var.set("✓ 저장됨")

    def apply_settings(self, new: Settings) -> None:
        """복구 등으로 받은 Settings 를 모든 입력 위젯에 반영하고 즉시 저장한다."""
        for name, var in self.setting_vars.items():
            var.set(getattr(new, name))
        self.flush_save()

    def flush_save(self) -> None:
        if self._save_after_id is not None:
            self.after_cancel(self._save_after_id)
            self._save_after_id = None
            self._do_save()

    def _on_close(self) -> None:
        self.flush_save()
        self.destroy()

    def _apply_icon(self) -> None:
        path = _resource_path("icon.png")
        if not path.is_file():
            return
        try:
            self._icon_image = PhotoImage(file=str(path))  # GC 방지용 self 참조
            self.iconphoto(True, self._icon_image)
        except Exception:  # noqa: BLE001
            pass

    def _enable_clipboard_shortcuts(self) -> None:
        """한글 IME 상태에서도 복사/붙여넣기가 동작하도록 보정 + 우클릭 메뉴.

        Tk 기본 단축키는 keysym(c/v/x/a) 기준이라 한글 입력기가 켜져 있으면
        keysym 이 한글 자모로 바뀌어 먹지 않는다. 또 기본 동작은 플랫폼/테마에
        따라 동작이 들쭉날쭉하다. 그래서 클립보드 동작을 직접 구현하고,
        단축키(keysym/keycode 양쪽 매칭)와 우클릭 메뉴 어느 쪽으로도 호출되게
        한다. Entry(ttk 포함)와 Text 위젯 모두 지원한다.
        """
        def _is_text(w) -> bool:
            try:
                return w.winfo_class() == "Text"
            except TclError:
                return False

        def _selected_text(w):
            try:
                if _is_text(w):
                    return w.get("sel.first", "sel.last")
                if w.selection_present():
                    return w.get()[int(w.index("sel.first")):int(w.index("sel.last"))]
            except TclError:
                pass
            return None

        def _delete_sel(w) -> None:
            try:
                w.delete("sel.first", "sel.last")
            except TclError:
                pass

        def _copy(w) -> None:
            sel = _selected_text(w)
            if sel:
                w.clipboard_clear()
                w.clipboard_append(sel)

        def _cut(w) -> None:
            sel = _selected_text(w)
            if not sel:
                return
            w.clipboard_clear()
            w.clipboard_append(sel)
            _delete_sel(w)

        def _paste(w) -> None:
            try:
                text = w.clipboard_get()
            except TclError:
                return
            _delete_sel(w)
            try:
                w.insert("insert", text)
            except TclError:
                pass

        def _select_all(w) -> None:
            try:
                if _is_text(w):
                    w.tag_add("sel", "1.0", "end-1c")
                else:
                    w.select_range(0, "end")
                    w.icursor("end")
            except TclError:
                pass

        # ── 키보드 단축키 ────────────────────────────────────────────────
        # keycode(물리 키)는 IME 영향을 받지 않지만 플랫폼마다 값이 다르다.
        if sys.platform == "darwin":
            codes = {"c": 8, "v": 9, "x": 7, "a": 0}
        elif sys.platform == "win32":
            codes = {"c": 67, "v": 86, "x": 88, "a": 65}
        else:  # linux / X11
            codes = {"c": 54, "v": 55, "x": 53, "a": 38}
        ops = {"c": _copy, "v": _paste, "x": _cut, "a": _select_all}

        def _key(event):
            ks = event.keysym.lower()
            key = next(
                (k for k in codes if ks == k or event.keycode == codes[k]), None)
            if key is None:
                return None  # 그 외 모디파이어 조합은 기본 동작에 맡긴다
            ops[key](event.widget)
            return "break"

        for mod in ("Command", "Control"):
            for cls in ("TEntry", "Entry", "Text"):
                self.bind_class(cls, f"<{mod}-KeyPress>", _key)

        # ── 우클릭 컨텍스트 메뉴 ─────────────────────────────────────────
        menu = Menu(self, tearoff=0)
        menu.add_command(label="잘라내기",
                         command=lambda: _cut(self._ctx_target))
        menu.add_command(label="복사",
                         command=lambda: _copy(self._ctx_target))
        menu.add_command(label="붙여넣기",
                         command=lambda: _paste(self._ctx_target))
        menu.add_separator()
        menu.add_command(label="전체 선택",
                         command=lambda: _select_all(self._ctx_target))
        self._ctx_target = None

        def _popup(event):
            self._ctx_target = event.widget
            try:
                event.widget.focus_set()
            except TclError:
                pass
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
            return "break"

        # 우클릭 버튼 번호는 플랫폼마다 다르다 (맥은 Button-2/Ctrl-클릭도 포함).
        if sys.platform == "darwin":
            buttons = ("<Button-2>", "<Button-3>", "<Control-Button-1>")
        else:
            buttons = ("<Button-3>",)
        for cls in ("TEntry", "Entry", "Text"):
            for btn in buttons:
                self.bind_class(cls, btn, _popup)


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
