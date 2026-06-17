# 매매 회고 사용법

키움 REST API로 일일 체결·손익 데이터를 받아 Markdown / PDF 매매일지를 만들고, 선택적으로 Gemini 또는 Ollama LLM으로 매매 리뷰와 다음 거래일 전략 코멘트를 붙여 주는 데스크톱 앱입니다.

---

## 1. 빠른 시작 (3가지 실행 방법)

### A. 단독 실행파일 (Python 설치 없이) — 권장

이미 빌드된 [`dist/InvestRetrospect.app`](dist/InvestRetrospect.app)를 더블클릭.

처음 실행 시 macOS Gatekeeper 경고가 뜨면 → **Finder에서 우클릭 → "열기"** → "열기" 한 번만 허용.

### B. 더블클릭 스크립트 (Python 설치 필요)

[`run.command`](run.command)를 Finder에서 더블클릭. 최초 1회는 `.venv` 자동 생성 + 패키지 설치(수십 초), 이후엔 바로 실행.

### C. 터미널

```bash
.venv/bin/invest-retrospect-gui          # GUI
.venv/bin/invest-retrospect journal ...  # CLI
```

---

## 2. 초기 설정 (창에서 키 입력)

앱을 열면 키가 비어 있을 경우 **[설정]** 탭이 먼저 보입니다.

### 키움 REST API
| 항목 | 설명 |
|---|---|
| **APP KEY** | [openapi.kiwoom.com](https://openapi.kiwoom.com)에서 발급 |
| **SECRET KEY** | 위와 같이 발급 (마스킹 입력) |
| **환경** | 처음엔 **모의(mock)** 로 동작 확인, 검증 후 **운영(prod)** 으로 전환 |
| **기본 계좌번호** | 10자리 계좌번호. [매매일지] 탭의 계좌번호와 자동 연동 |

### AI 코멘트
| 제공자 | 필요한 값 |
|---|---|
| **사용 안 함** | 없음. 일지에 AI 섹션을 넣지 않음 |
| **Gemini** | API KEY ([aistudio.google.com](https://aistudio.google.com) 무료) + 모델 (기본 `gemini-2.5-flash`) |
| **Ollama** | 호스트 (기본 `http://localhost:11434`) + 모델 (예: `llama3.1`). 사전에 `ollama pull <모델>` 필요 |

제공자를 바꾸면 관련 입력만 활성화되고 나머지는 자동 비활성화됩니다.

### 출력
| 항목 | 설명 |
|---|---|
| **저장 디렉토리** | 매매일지(.md/.pdf) + 원본(.json)이 저장되는 폴더. 기본값 `~/Documents/invest-retrospect` |

### 설정 저장
입력하면 **자동으로** `~/.invest-retrospect/settings.json`에 저장됩니다 (창 우측 하단에 `✓ 저장됨` 표시). 별도 저장 버튼 없음. 앱을 다시 켜도 입력값이 그대로 유지됩니다.

---

## 3. 매매일지 생성

**[매매일지]** 탭으로 이동.

| 항목 | 기본값 |
|---|---|
| **날짜** | 오늘 (YYYYMMDD). 다른 날 일지가 필요하면 직접 입력 |
| **계좌번호** | 설정에서 등록한 기본 계좌. 변경 시 설정에도 즉시 반영 |
| **형식** | Markdown / PDF / 둘 다 |

**[매매일지 생성]** 클릭 → 로그가 실시간으로 표시됩니다.

```
[1/4] 키움 인증 (모의)...
[2/4] 2026-05-14 데이터 조회...
  → 체결 12건, 실현손익 +85,300원
[3/4] AI 코멘트 생성 중 (gemini)...
[4/4] 출력 생성 (md)...
완료.
```

완료되면 **[MD 열기] / [PDF 열기] / [폴더 열기]** 버튼이 활성화됩니다.

### 생성되는 파일
저장 디렉토리에 다음 3종이 만들어집니다:

```
20260514_8012345611.md      # 매매일지
20260514_8012345611.pdf     # (형식 PDF 또는 둘 다 일 때)
20260514_8012345611.json    # 키움 원본 응답 (재가공/디버깅용)
```

---

## 4. PDF 출력

PDF는 `weasyprint`와 `markdown` 패키지가 필요합니다.

**현재 빌드된 `.app` 에는 PDF가 포함되어 있지 않습니다.** PDF를 쓰려면:

```bash
.venv/bin/pip install weasyprint markdown
brew install pango        # macOS, weasyprint 의 시스템 의존
```

설치 후 `.venv/bin/invest-retrospect-gui` 로 실행하면 PDF 출력이 동작합니다. `.app`에도 포함하려면 [`InvestRetrospect.spec`](InvestRetrospect.spec)의 `excludes`에서 weasyprint/markdown 항목 코멘트 풀고 `./build_app.sh` 재실행.

---

## 5. 설정 파일 위치

- **GUI 설정**: `~/.invest-retrospect/settings.json` — 자동 저장/로드
- **CLI 설정**: 프로젝트 루트의 `.env` (선택, [`.env.example`](.env.example) 참고)

GUI와 CLI는 설정 소스가 분리되어 있습니다. 둘 다 쓰면 각자 자기 설정만 사용합니다.

---

## 6. CLI 사용 (선택)

GUI 외에 스크립팅 / 크론 자동화가 필요하면:

```bash
# 오늘자 MD
.venv/bin/invest-retrospect journal --account 8012345611

# 특정일, PDF + MD 둘 다
.venv/bin/invest-retrospect journal --date 20260514 --account 8012345611 --format both

# Ollama 사용
.venv/bin/invest-retrospect journal --account 8012345611 --provider ollama

# AI 끄기
.venv/bin/invest-retrospect journal --account 8012345611 --no-ai

# 저장된 JSON 으로 다시 렌더링 (API 호출 안 함)
.venv/bin/invest-retrospect render journals/20260514_8012345611.json --format pdf
```

CLI는 `.env` 파일 또는 환경 변수에서 키를 읽습니다. 자세한 키 이름은 [.env.example](.env.example) 참고.

---

## 7. 단독 실행파일(.app) 빌드

소스 변경 후 또는 PDF 포함 빌드를 만들고 싶을 때:

```bash
./build_app.sh
```

- 결과: `dist/InvestRetrospect.app` (약 56MB, Apple Silicon 전용)
- 다른 Mac으로 배포하려면 `.app` 폴더를 통째로 압축해서 전달
- Intel 지원이 필요하면 [`InvestRetrospect.spec`](InvestRetrospect.spec) 에서 `target_arch="universal2"` 로 변경 (universal2 Python 빌드 필요)

---

## 8. 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| `.app` 더블클릭 시 "확인되지 않은 개발자" | 우클릭 → 열기 → 열기 (최초 1회) |
| `KIWOOM_APP_KEY / SECRET_KEY 가 비어있습니다` | [설정] 탭에서 키 입력 후 자동 저장 확인 |
| `kt00009 실패 [...]` 류 키움 오류 | 모의/운영 환경 mismatch — 모의 키는 mockapi 에만, 운영 키는 api 에만 동작 |
| `Ollama 서버에 연결할 수 없습니다` | `ollama serve` 실행 중인지 확인. 모델 미설치 시 `ollama pull <모델>` |
| AI 코멘트가 빈 칸 | API 키 오류 / 모델명 오타 / 요금 한도 초과 — 로그에 원인이 찍힘 |
| `weasyprint` ImportError | `pip install weasyprint markdown` + `brew install pango` |
| 빈 일지 (체결/잔고가 다 0) | 그 날짜에 실제 매매가 없었거나, 계좌번호가 틀림 |

---

## 9. 주요 파일

| 경로 | 역할 |
|---|---|
| [src/invest_retrospect/gui.py](src/invest_retrospect/gui.py) | Tkinter 창 (탭, 자동 저장, AI 입력 토글) |
| [src/invest_retrospect/core.py](src/invest_retrospect/core.py) | `run_journal()` — broker 호출 → 정규화 → AI → 렌더링 |
| [src/invest_retrospect/brokers/](src/invest_retrospect/brokers/) | 증권사별 REST 클라이언트 (kiwoom / kis / ls) |
| [src/invest_retrospect/ai.py](src/invest_retrospect/ai.py) | Gemini / Ollama 호출 |
| [src/invest_retrospect/renderer.py](src/invest_retrospect/renderer.py) | Markdown / PDF 렌더링 |
| [src/invest_retrospect/settings_store.py](src/invest_retrospect/settings_store.py) | GUI 설정 영속화 |
| [src/invest_retrospect/cli.py](src/invest_retrospect/cli.py) | 터미널 CLI |
| [run.command](run.command) | 더블클릭 실행 스크립트 (auto-venv) |
| [build_app.sh](build_app.sh) | PyInstaller 빌드 자동화 |
| [InvestRetrospect.spec](InvestRetrospect.spec) | PyInstaller 설정 |
