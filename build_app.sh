#!/bin/bash
# 단독 실행파일 (.app) 빌드. PyInstaller 로 dist/InvestRetrospect.app 생성.
# 결과물은 Python 설치 없이 Finder 에서 더블클릭으로 실행 가능.

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# 이 앱은 tkinter GUI 라 빌드 python 에 tkinter 가 반드시 있어야 한다.
# homebrew python 은 tkinter 가 분리돼 있으므로(예: python-tk@3.x) import 가능 여부까지 확인.
PYBIN=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys, tkinter; assert sys.version_info >= (3,11)' 2>/dev/null; then
            PYBIN="$candidate"
            break
        fi
    fi
done
if [ -z "$PYBIN" ]; then
    echo "tkinter 가 포함된 Python 3.11+ 가 필요합니다." >&2
    echo "  homebrew 사용 시: brew install python-tk@3.14  (또는 사용 중인 버전에 맞게)" >&2
    exit 1
fi
echo "[setup] 빌드 Python: $("$PYBIN" --version) ($PYBIN)"

if [ ! -d ".venv" ]; then
    echo "[setup] .venv 생성 중..."
    "$PYBIN" -m venv .venv
fi

.venv/bin/pip install --upgrade pip >/dev/null
.venv/bin/pip install -e . >/dev/null
.venv/bin/pip install 'pyinstaller>=6.0' >/dev/null

# icon.png → icon.icns (PNG 가 ICNS 보다 새거나 ICNS 가 없으면 재생성)
if [ -f icon.png ] && { [ ! -f icon.icns ] || [ icon.png -nt icon.icns ]; }; then
    if command -v sips >/dev/null && command -v iconutil >/dev/null; then
        echo "[icon] icon.png → icon.icns 변환..."
        ICONSET=$(mktemp -d)/icon.iconset
        mkdir -p "$ICONSET"
        for spec in "16:icon_16x16" "32:icon_16x16@2x" "32:icon_32x32" \
                    "64:icon_32x32@2x" "128:icon_128x128" "256:icon_128x128@2x" \
                    "256:icon_256x256" "512:icon_256x256@2x" \
                    "512:icon_512x512" "1024:icon_512x512@2x"; do
            size="${spec%%:*}"
            name="${spec##*:}"
            sips -z "$size" "$size" icon.png --out "$ICONSET/$name.png" >/dev/null
        done
        iconutil -c icns -o icon.icns "$ICONSET"
        rm -rf "$(dirname "$ICONSET")"
    else
        echo "[icon] sips/iconutil 없음 — icns 변환 생략 (.app 아이콘 없음)" >&2
    fi
fi

echo "[build] 이전 산출물 정리..."
rm -rf build dist

echo "[build] PyInstaller 실행 (수 분 소요)..."
.venv/bin/pyinstaller InvestRetrospect.spec --noconfirm

if [ -d "dist/InvestRetrospect.app" ]; then
    echo ""
    echo "✓ 빌드 완료: dist/InvestRetrospect.app"
    echo "  Finder 에서 dist 폴더 열기: open dist"
else
    echo "빌드 실패 — 로그 확인" >&2
    exit 1
fi
