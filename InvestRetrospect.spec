# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for InvestRetrospect (단독 실행파일).

빌드: ./build_app.sh   (또는: pyinstaller InvestRetrospect.spec --noconfirm)
산출: dist/InvestRetrospect.app  (macOS 더블클릭 실행)
"""

import os
from PyInstaller.utils.hooks import collect_all

# google-genai 는 동적 import + 네이티브 의존성이 많아 의존 패키지까지 명시 수집해야
# 번들 안에서 "marshal data too short" / 파라미터 누락 같은 로드 오류가 안 난다.
_all_datas = []
_all_binaries = []
_all_hidden = []
for pkg in (
    "google.genai",
    "google.auth",
    "pydantic",
    "pydantic_core",     # Rust 확장 (.so) — 누락 시 marshal 오류
    "certifi",           # HTTPS 인증서 번들
    "httpx",
    "httpcore",
    "anyio",
    "sniffio",
    "websockets",
    "reportlab",         # PDF 생성: CMap (CID 폰트) 데이터 파일 포함
    "openpyxl",          # 수동 원장 엑셀 업로드/샘플
    "bs4",               # 시장 대시보드 HTML 파싱
    "tksheet",           # 수동 원장 표(현재가 셀 등락색)
):
    try:
        d, b, h = collect_all(pkg)
        _all_datas += d
        _all_binaries += b
        _all_hidden += h
    except Exception as e:
        print(f"[spec] {pkg} 수집 실패 (무시): {e}")

# 아이콘: 런타임에 PhotoImage 로 로드하므로 datas 에 포함
if os.path.isfile("icon.png"):
    _all_datas.append(("icon.png", "."))

a = Analysis(
    ["src/invest_retrospect/__main__.py"],
    pathex=["src"],
    binaries=_all_binaries,
    datas=_all_datas,
    hiddenimports=[
        "invest_retrospect.gui",
        "invest_retrospect.cli",
        "invest_retrospect.core",
        "invest_retrospect.ai",
        "invest_retrospect.analyzer",
        "invest_retrospect.config",
        "invest_retrospect.brokers",
        "invest_retrospect.brokers.base",
        "invest_retrospect.brokers.kiwoom",
        "invest_retrospect.brokers.kis",
        "invest_retrospect.brokers.ls",
        "invest_retrospect.renderer",
        "invest_retrospect.settings_store",
        "invest_retrospect.manual",
        "invest_retrospect.prices",
        "invest_retrospect.market",
        "invest_retrospect.types",
        *_all_hidden,
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "pytest",
        "test",
        "tests",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="InvestRetrospect",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,             # 창 모드 (터미널 없이 GUI 만)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="InvestRetrospect",
)

app = BUNDLE(
    coll,
    name="InvestRetrospect.app",
    icon="icon.icns" if os.path.isfile("icon.icns") else None,
    bundle_identifier="com.lunastratos.invest-retrospect",
    info_plist={
        "CFBundleDisplayName": "매매 회고",
        "CFBundleName": "InvestRetrospect",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
    },
)
