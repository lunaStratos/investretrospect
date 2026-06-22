# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for InvestRetrospect on Windows.

빌드: .\build_app.bat
산출: dist\InvestRetrospect.exe (단일 파일)
"""

import os
from PyInstaller.utils.hooks import collect_all

_all_datas = []
_all_binaries = []
_all_hidden = []
for pkg in (
    "sv_ttk",          # Sun Valley ttk 테마 (.tcl 데이터 파일 포함)
    "google.genai",
    "google.auth",
    "pydantic",
    "pydantic_core",
    "certifi",
    "httpx",
    "httpcore",
    "anyio",
    "sniffio",
    "websockets",
    "reportlab",
    "openpyxl",
    "bs4",
    "tksheet",         # 수동 원장 표(현재가 셀 등락색)
):
    try:
        d, b, h = collect_all(pkg)
        _all_datas += d
        _all_binaries += b
        _all_hidden += h
    except Exception as e:
        print(f"[spec] {pkg} 수집 실패 (무시): {e}")

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

_icon = "icon.ico" if os.path.isfile("icon.ico") else None

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="InvestRetrospect",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon,
)
