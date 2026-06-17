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
    PhotoImage,
    StringVar,
    TclError,
    Tk,
    Toplevel,
    filedialog,
    messagebox,
    ttk,
)
from tkinter.scrolledtext import ScrolledText

from invest_retrospect import market, prices
from invest_retrospect.brokers import Broker
from invest_retrospect.core import JournalResult, run_journal, today_ymd
from invest_retrospect.manual import (
    Ledger,
    LedgerEntry,
    load_ledger,
    parse_bulk_entries,
    parse_excel_entries,
    run_manual_journal,
    save_ledger,
    write_sample_xlsx,
)
from invest_retrospect.settings_store import (
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

    def _build_scroll(self) -> None:
        """설정 내용이 길어 화면을 넘어가므로 세로 스크롤 캔버스에 담는다."""
        canvas = Canvas(self, highlightthickness=0)
        vsb = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.body = ttk.Frame(canvas, padding=PAD * 2)
        win = canvas.create_window((0, 0), window=self.body, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        self.body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

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


class ManualLedgerTab(ttk.Frame):
    """수동 주식 원장: 매수/매도 변화 기록 → 임의 날짜 매매일지 생성."""

    _COLS = ("check", "date", "market", "name", "code", "side", "qty", "price", "tag")
    _HEADS = {
        "check": "✓", "date": "거래일", "market": "시장", "name": "종목명", "code": "코드",
        "side": "구분", "qty": "수량", "price": "단가", "tag": "태그",
    }
    _COL_W = {"check": 34, "date": 80, "market": 64, "name": 130, "code": 70,
              "side": 50, "qty": 64, "price": 90, "tag": 70}
    _CHECK_ON, _CHECK_OFF = "☑", "☐"

    def __init__(self, master: ttk.Notebook, app: "App") -> None:
        super().__init__(master, padding=PAD * 2)
        self.app = app
        self.ledger: Ledger = load_ledger()
        self._result: JournalResult | None = None
        self._checked: set[int] = set()    # 체크된 항목 (id(entry) 기준 — 객체 식별)
        self._build()
        self._reload_tree()
        self._refresh_price_label()

    # ── UI ───────────────────────────────────────────────────────────────
    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

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
        ttk.Button(bar, text="엑셀 업로드", command=self._upload_excel).pack(side="left", padx=(PAD, 0))
        ttk.Button(bar, text="샘플 다운로드", command=self._download_sample).pack(side="left", padx=(PAD, 0))

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

        self.log = ScrolledText(self, height=8, font=("Menlo", 10))
        self.log.grid(row=5, column=0, sticky="nsew", pady=(PAD, 0))
        self.log.configure(state="disabled")

    # ── 원장 편집 ─────────────────────────────────────────────────────────
    def _reload_tree(self) -> None:
        # 사라진 항목의 체크 상태 정리
        self._checked &= {id(e) for e in self.ledger.entries}
        self.tree.delete(*self.tree.get_children())
        for i, e in enumerate(self.ledger.entries):
            mark = self._CHECK_ON if id(e) in self._checked else self._CHECK_OFF
            self.tree.insert(
                "", "end", iid=str(i),
                values=(mark, e.date, e.market, e.stk_nm, e.stk_cd, e.side,
                        f"{e.qty:,}", _fmt_price(e.price), e.tag),
            )

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
        save_ledger(self.ledger)
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
            save_ledger(self.ledger)
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
                save_ledger(self.ledger)
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
            save_ledger(self.ledger)
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

    def _delete_selected(self) -> None:
        sel = set(self.tree.selection())
        if not sel:
            return
        keep = [e for i, e in enumerate(self.ledger.entries) if str(i) not in sel]
        self.ledger.entries = keep
        save_ledger(self.ledger)
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
        save_ledger(self.ledger)
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
                cfg, ymd, fmt, do_fetch=do_fetch, entries=entries, log=self._log)
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
        self.geometry("880x760")
        self.minsize(720, 640)
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
        try:
            import sv_ttk
            sv_ttk.set_theme(name)
            self.update_idletasks()   # 테마 색이 스타일 DB에 반영되도록 한 박자 대기
            # 테마가 실제로 쓰는 색을 읽어 클래식 위젯과 정확히 맞춘다.
            pal.bg = st.lookup("TFrame", "background") or st.lookup(".", "background") or pal.bg
            pal.fg = st.lookup("TLabel", "foreground") or st.lookup(".", "foreground") or pal.fg
            pal.entry_bg = (st.lookup("TEntry", "fieldbackground")
                            or st.lookup(".", "fieldbackground") or pal.entry_bg)
            st.configure("Hint.TLabel", background=pal.bg, foreground=pal.hint)
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

    def _on_tab_changed(self, _event=None) -> None:
        try:
            current = self.nb.nametowidget(self.nb.select())
        except Exception:  # noqa: BLE001
            return
        self.market_tab.set_active(current is self.market_tab)

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
        """macOS 에서 Entry/Text 의 Cmd+C/V/X/A 가 안 먹는 문제 보정.

        Command(맥)·Control(타OS) 양쪽을 가상 이벤트(<<Copy>> 등)로 연결한다.
        """
        def gen(virtual: str):
            def handler(event):
                event.widget.event_generate(virtual)
                return "break"
            return handler

        def select_all_entry(event):
            event.widget.select_range(0, "end")
            event.widget.icursor("end")
            return "break"

        def select_all_text(event):
            event.widget.tag_add("sel", "1.0", "end-1c")
            return "break"

        for mod in ("Command", "Control"):
            for cls in ("TEntry", "Entry", "Text"):
                self.bind_class(cls, f"<{mod}-c>", gen("<<Copy>>"))
                self.bind_class(cls, f"<{mod}-v>", gen("<<Paste>>"))
                self.bind_class(cls, f"<{mod}-x>", gen("<<Cut>>"))
            for cls in ("TEntry", "Entry"):
                self.bind_class(cls, f"<{mod}-a>", select_all_entry)
            self.bind_class("Text", f"<{mod}-a>", select_all_text)


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
