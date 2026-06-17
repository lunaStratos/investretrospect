@echo off
setlocal enabledelayedexpansion

rem Windows build script for InvestRetrospect using PyInstaller.
rem Requires Python 3.11+ with tkinter installed.

set "DIR=%~dp0"
cd /d "%DIR%"

echo [setup] Checking Python environment...
rem py 런처로 tkinter 포함 3.11+ 인터프리터를 먼저 찾고, 없으면 python으로 폴백.
set "PY="
where py >nul 2>nul && (
    for %%V in (3.13 3.12 3.11) do (
        if not defined PY (
            py -%%V -c "import tkinter" 2>nul && set "PY=py -%%V"
        )
    )
)
if not defined PY (
    python -c "import sys, tkinter; assert sys.version_info >= (3,11)" 2>nul && set "PY=python"
)
if not defined PY (
    echo.
    echo tkinter 포함 Python 3.11 이상이 필요합니다.
    echo Windows에서는 Python 설치 시 "tcl/tk and IDLE" 옵션이 활성화되어 있어야 합니다.
    exit /b 1
)
echo [setup] Python version:
%PY% --version

if not exist ".venv\Scripts\activate.bat" (
    echo [setup] Creating .venv...
    %PY% -m venv .venv
)

echo [setup] Installing dependencies...
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -e .
.venv\Scripts\python -m pip install "pyinstaller>=6.0"

echo [build] Cleaning previous build artifacts...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist __pycache__ rmdir /s /q __pycache__

echo [build] Running PyInstaller...
.venv\Scripts\pyinstaller InvestRetrospect.win.spec --noconfirm

if exist "dist\InvestRetrospect.exe" (
    echo.
    echo ✓ 빌드 완료: dist\InvestRetrospect.exe ^(단일 파일^)
    echo   Explorer에서 dist 폴더 열기: start .\dist
) else (
    echo.
    echo 빌드 실패 — 로그를 확인하세요.
    exit /b 1
)
endlocal
