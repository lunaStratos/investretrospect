#!/bin/bash
# 매매 회고 GUI 실행. Finder 에서 더블클릭으로 실행 가능.
# 최초 실행 시 .venv 를 만들고 패키지를 설치한다.

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# tkinter GUI 라 tkinter 가 import 되는 python 만 선택 (homebrew 는 python-tk 분리).
PYBIN=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        ver=$("$candidate" -c 'import sys, tkinter; print(sys.version_info >= (3,11))' 2>/dev/null || echo False)
        if [ "$ver" = "True" ]; then
            PYBIN="$candidate"
            break
        fi
    fi
done

if [ -z "$PYBIN" ]; then
    osascript -e 'display alert "tkinter 포함 Python 3.11+ 가 필요합니다" message "homebrew 사용 시 brew install python-tk@3.14 후 다시 실행하세요."'
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "[setup] .venv 생성 중..."
    "$PYBIN" -m venv .venv
    .venv/bin/pip install --upgrade pip >/dev/null
    .venv/bin/pip install -e . >/dev/null
fi

exec .venv/bin/python -m invest_retrospect
