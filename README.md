# 매매 회고 (invest-retrospect)

**키움증권 / 한국투자증권(한투) / LS증권** REST API 로 일일 매매 데이터를 받아
**Markdown / PDF 매매일지**를 만들고, **Gemini** 또는 **Ollama** 로 AI 코멘트
(오늘 리뷰 + 내일 전략)를 덧붙이는 **데스크톱 앱** 입니다.

- 🖥 데스크톱 GUI + 더블클릭 실행
- 🏦 멀티 증권사: 키움 / 한투(KIS) / LS — 한 앱에서 라디오로 전환
- 📦 단독 실행파일 `.app` (Python 미설치 환경 OK)
- 🤖 AI 코멘트: Gemini (클라우드) 또는 Ollama (로컬)
- 📄 출력: Markdown / PDF / 둘 다
- 📅 임의 날짜 지정 가능
- 💾 설정 자동 저장 (입력 즉시 디스크 반영)
- ⚙️ CLI 모드도 별도로 사용 가능

---

## 빠른 시작

### 옵션 A. 단독 실행파일 (`.app`) — 가장 간단

```bash
./build_app.sh                 # 처음 한 번만 (~5분)
open dist/                     # Finder 에서 결과 확인
```

빌드된 `dist/InvestRetrospect.app` 을 더블클릭하면 창이 뜹니다. 원하면 `/Applications` 으로 복사해도 됩니다.

```bash
cp -R dist/InvestRetrospect.app /Applications/
```

> 미서명 .app 입니다. 첫 실행 시 macOS Gatekeeper 가 차단할 수 있어요. **우클릭 → 열기 → "열기"** 한 번 누르면 이후엔 그냥 더블클릭으로 열립니다.

### 옵션 B. 소스에서 실행 — 개발 또는 빠르게 시도

`run.command` 를 Finder 에서 더블클릭하면 자동으로 `.venv` 를 만들고 GUI 가 뜹니다.

수동:
```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/invest-retrospect-gui     # GUI 실행
```

---

## 사용 흐름

### 1) 처음 실행 — 설정

앱을 처음 열면 **[설정]** 탭이 자동으로 활성화됩니다.

| 항목 | 설명 |
|---|---|
| **증권사** | 키움 / 한국투자증권(한투) / LS — 사용할 곳을 라디오로 선택 |
| **환경** | 처음에는 **모의 (mock)** 권장. 실거래는 **운영 (prod)** |
| **키움 APP KEY / SECRET KEY** | [openapi.kiwoom.com](https://openapi.kiwoom.com) 에서 발급 |
| **키움 계좌번호** | 10자리 |
| **한투 APP KEY / APP SECRET** | [apiportal.koreainvestment.com](https://apiportal.koreainvestment.com) 에서 발급 |
| **한투 계좌번호** | `12345678-01` 형식 (CANO 8자리 + ACNT_PRDT 2자리) |
| **LS APP KEY / APP SECRET** | [openapi.ls-sec.co.kr](https://openapi.ls-sec.co.kr) 에서 발급 |
| **LS 계좌번호 / 계좌 비밀번호** | 일부 TR(잔고/예수금)이 비밀번호 4자리를 요구 |
| **AI 제공자** | 사용 안 함 / Gemini / Ollama |
| **GEMINI API KEY** | [aistudio.google.com](https://aistudio.google.com) 에서 무료 발급 |
| **Gemini 모델** | 기본 `gemini-2.5-flash` |
| **Ollama 호스트 / 모델** | 기본 `http://localhost:11434`, `llama3.1` |
| **저장 디렉토리** | 매매일지 저장 위치 (기본 `~/Documents/invest-retrospect`) |

증권사 키는 **모두 입력해 두고 라디오로만 활성 broker 를 바꿀 수 있습니다.**
선택되지 않은 증권사 박스의 입력란은 비활성(회색)으로 표시되지만 값은 보존됩니다.

**입력 즉시 자동 저장됩니다.** 별도 "저장" 버튼 없음.
- 약 0.5초 디바운스 후 `~/.invest-retrospect/settings.json` 에 기록
- 우측 하단 상태표시: `● 수정됨` → `✓ 저장됨`
- 창 닫기 시에도 한 번 더 동기 저장 (안전망)

**AI 제공자 선택에 따라 무관한 입력은 자동 비활성화**:
- **사용 안 함** → Gemini / Ollama 입력 모두 비활성화
- **Gemini** → Gemini 입력만 활성화
- **Ollama** → Ollama 입력만 활성화

### 2) 매매일지 생성

**[매매일지]** 탭으로 이동.

| 항목 | 기본값 |
|---|---|
| **날짜 (YYYYMMDD)** | 오늘 (한국 시간) |
| **계좌번호** | 설정 탭과 동기화 (한쪽에서 바꾸면 양쪽 반영) |
| **형식** | Markdown |

**[매매일지 생성]** 클릭 → 백그라운드 스레드에서 진행, 로그 실시간 표시.

```
[1/4] 한국투자증권 인증 (모의)...
[2/4] 2026-05-14 데이터 조회...
  → 체결 12건, 실현손익 +85,200원
[3/4] AI 코멘트 처리...
[info] AI 코멘트 생성 중 (gemini)...
[4/4] 출력 생성 (md)...
완료.
```

완료 후 **[MD 열기] / [PDF 열기] / [폴더 열기]** 버튼이 활성화됩니다.

### 시장 대시보드 ([시장] 탭)

코스피/코스닥 시장 현황을 한 화면에서 봅니다(네이버 금융, **20초 자동 갱신** — 탭을 볼
때만 동작). 종합지수 · 환율/금리 · 시가총액 순위 · 외국인/기관 순매수·순매도 · 외국인
보유순위. 상단 라디오로 **코스피 ↔ 코스닥** 전환, [새로고침]으로 수동 갱신.
한국 관례 색상(상승=빨강, 하락=파랑). *비공식 엔드포인트라 예고 없이 바뀔 수 있습니다.*

### 3) 수동 원장 — 증권사 연동 없이 (국내/해외)

증권사 API 없이 **직접 보유 변화를 기록**해 매매일지를 만들 수 있습니다. 보유수량을 매일
적는 대신 **매수/매도 변화 이벤트만** 기록하면, 임의의 날짜 기준으로 그 시점까지를 재생해
보유·실현손익·체결을 산출합니다.

**[수동 원장]** 탭에서:

- **항목 추가**: 거래일 · 시장(KOSPI/KOSDAQ/NASDAQ/NYSE/AMEX) · 종목명 · 코드/티커 ·
  구분(매수/매도) · 수량 · 단가 → `~/.invest-retrospect/manual_ledger.json` 에 즉시 저장.
- **평가 현재가**는 자동 조회됩니다 — 해외주식은 **Yahoo Finance**, 국내주식은 탭 안의
  **국내 시세 API**(야후 / 한투(KIS) / 키움)에서 선택(한투·키움 선택 시 해당 증권사 키 필수).
  조회 실패 시 **수동 현재가**(폴백) 또는 원가 기준으로 평가.
- **기준일**과 형식(MD/PDF/둘 다)을 정하고 **[수동 매매일지 생성]**.

해외주식이 섞이면 통화별로 분리해 표시하며(환산하지 않음), 포트폴리오 비중 파이도 통화별로
하나씩 만들어집니다.

> 국내 시세 API 는 **[수동 원장] 탭**과 **[설정] 탭** 양쪽에서 선택할 수 있고 값은 자동
> 동기화됩니다. 한투/키움 키는 **[설정]** 탭에서 입력하며, 거기 **[API 설정 페이지 열기]**
> 버튼으로 발급 포털을 열 수 있습니다.

---

## 결과물

저장 디렉토리 (`~/Documents/invest-retrospect` 기본) 에 다음 파일들이 만들어집니다 (파일명은 `<날짜>_<broker>_<계좌>` 형식):

```
20260514_kiwoom_8012345611.md   # 매매일지 (Markdown)
20260514_kis_1234567801.pdf     # 한투 일지 (PDF, 형식=PDF/둘 다 선택 시)
20260514_ls_1234567890.json     # API 원본 응답 (재렌더/디버깅용)
```

`.md` 의 섹션 구성:

1. **요약** — 회전금액, 실현손익, 승률, 평가금액, 예수금
2. **종목별 실현손익** — 매수/매도/실현손익/수익률
3. **보유 종목 (장 마감 기준)** — 평단/현재가/평가손익
4. **체결 내역** — 시간·종목·구분·수량·단가·금액
5. **AI 매매 리뷰** — Gemini/Ollama 코멘트
6. **다음 거래일 전략** — 내일 액션 가이드

---

## PDF 출력 활성화 (선택)

PDF 는 기본 빌드에 포함되지 않습니다 (의존성 부피가 큼).

**소스 환경에서 쓰는 경우**:
```bash
.venv/bin/pip install '.[pdf]'
# macOS 에서 weasyprint 가 pango 를 필요로 함
brew install pango
```

**`.app` 에 PDF 포함하려면**:
1. `.venv` 에 위 패키지 설치
2. [InvestRetrospect.spec](InvestRetrospect.spec) 의 `excludes` 에서 `weasyprint`, `markdown` 코멘트 해제
3. `./build_app.sh` 재실행

---

## CLI 사용 (고급)

터미널에서도 동일 기능 사용 가능. CLI 는 `.env` 파일을 사용합니다 (GUI 의 `settings.json` 과 별개).

```bash
cp .env.example .env
# .env 에 BROKER, BROKER_ENV, <broker별> APP KEY/SECRET 등 입력
```

```bash
# 오늘자 일지 (MD) — .env 의 BROKER 사용
.venv/bin/invest-retrospect journal --account 8012345611

# broker 명시
.venv/bin/invest-retrospect journal --broker kis --account 12345678-01
.venv/bin/invest-retrospect journal --broker ls  --account 1234567890

# 특정 날짜 / PDF + MD 둘 다
.venv/bin/invest-retrospect journal --date 20260514 --account 8012345611 --format both

# AI 백엔드
.venv/bin/invest-retrospect journal --account 8012345611 --provider ollama
.venv/bin/invest-retrospect journal --account 8012345611 --no-ai

# 환경 강제 (mock/prod)
.venv/bin/invest-retrospect journal --broker kis --env prod --account 12345678-01

# API 응답만 JSON 으로 덤프 (디버깅)
.venv/bin/invest-retrospect dump --broker kis --date 20260514 --account 12345678-01

# 이전 JSON 으로 다시 렌더 (API 재호출 없음)
.venv/bin/invest-retrospect render journals/20260514_kiwoom_8012345611.json --format both

# 수동 원장 → 일지 (증권사 연동 없이). 국내 시세 API 선택, 자동 조회 생략 가능
.venv/bin/invest-retrospect manual --date 20260514 --format both
.venv/bin/invest-retrospect manual --price-api kis --date 20260514     # 국내 현재가는 한투로
.venv/bin/invest-retrospect manual --no-fetch                          # 수동값/원가만 사용
```

> `manual` 은 `~/.invest-retrospect/manual_ledger.json` 원장을 사용합니다. 국내 시세를
> 한투/키움으로 받으려면 `.env` 의 `MANUAL_DOMESTIC_API=kis|kiwoom` 과 해당 키가 필요합니다.

자세한 옵션은 `invest-retrospect --help`, `invest-retrospect journal --help` 참고.

### 평일 자동 실행 (cron)

```cron
# 평일 16:30 KST 에 자동 생성
30 16 * * 1-5 cd ~/kiwoomToday && .venv/bin/invest-retrospect journal --account 8012345611
```

---

## 파일/디렉토리 위치

| 경로 | 용도 |
|---|---|
| `~/.invest-retrospect/settings.json` | GUI 가 관리하는 설정 (자동 저장) |
| `~/Documents/invest-retrospect/` | 매매일지 출력 (기본값, 변경 가능) |
| `.env` | CLI 용 환경 변수 (옵션) |
| `dist/InvestRetrospect.app` | 단독 실행 .app 번들 (~56MB) |
| [src/invest_retrospect/](src/invest_retrospect/) | 소스 코드 |

---

## 프로젝트 구조

```
src/invest_retrospect/
├── gui.py             # Tkinter GUI (설정 탭 + 매매일지 탭 + 자동 저장)
├── cli.py             # argparse 엔트리포인트 (journal/dump/render)
├── core.py            # 매매일지 생성 오케스트레이션 (CLI/GUI 공용)
├── config.py          # .env 로드 → Config 객체
├── settings_store.py  # GUI 의 settings.json I/O + Settings → Config 변환
├── brokers/           # 증권사별 REST 클라이언트 (키움/한투/LS)
│   ├── base.py        # BrokerClient ABC + BrokerInfo
│   ├── kiwoom.py      # 키움 (/api/dostk/acnt, api-id 헤더)
│   ├── kis.py         # 한국투자증권 (/uapi/..., tr_id 헤더)
│   └── ls.py          # LS증권 (/stock/accno, tr_cd 헤더, InBlock envelope)
├── analyzer.py        # 응답(키움 형식) → Trade/StockPL/Holding 정규화 + 집계
├── ai.py              # Gemini / Ollama 통합 호출
├── renderer.py        # Markdown / PDF 렌더링
├── types.py           # AICommentary 등 공용 데이터타입
└── __main__.py        # `python -m invest_retrospect` 시 GUI 실행
```

---

## 사용한 증권사 TR

각 증권사 클라이언트는 응답을 **키움 스키마**(`acnt_ord_cntr_prps_dtl` / `dt_stk_rlzt_pl` / `acnt_evlt_remn_indv_tot` / `entr`)로 정규화해 반환합니다. 그래서 [analyzer.py](src/invest_retrospect/analyzer.py) 는 broker 를 모르고 한 가지 형식만 다룹니다.

### 키움증권 — `api-id` 헤더 (`/api/dostk/acnt`)

| api-id  | 설명 |
|---|---|
| `kt00009` | 계좌별주문체결내역상세 |
| `ka10170` | 당일매매일지요청 |
| `ka10073` | 일자별종목별실현손익 (일자) |
| `kt00018` | 계좌평가잔고내역 |
| `kt00001` | 예수금상세현황 |

### 한국투자증권(KIS) — `tr_id` 헤더 (`/uapi/domestic-stock/v1/trading/...`)

| tr_id (실전 / 모의) | 설명 |
|---|---|
| `TTTC8001R` / `VTTC8001R` | 일별주문체결조회 |
| `TTTC8434R` / `VTTC8434R` | 주식잔고조회 (예수금은 output2) |
| `CTRP6548R` (실전 only) | 일별 종목별 매매손익 (모의 미지원 → 체결로 폴백) |

### LS증권 — `tr_cd` 헤더 (`/stock/accno`, InBlock envelope)

| tr_cd | 설명 |
|---|---|
| `CSPAQ22200` | 현물계좌별 주문체결내역조회 |
| `CDPCQ04700` | 일자별 종목별 매매손익 |
| `t0424` | 주식잔고 (xingAPI 호환) |
| `CSPAQ12200` | 현물계좌예수금 주문가능금액 |

응답 필드명이 각 증권사 문서 개정에 따라 달라질 수 있습니다. 첫 실행 시 생성되는 `*.json` 덤프를 보면서 [brokers/](src/invest_retrospect/brokers/) 의 broker 모듈에서 키 이름만 보정하면 됩니다.

---

## 빌드 / 배포

```bash
./build_app.sh              # PyInstaller 빌드 (~5분, dist/InvestRetrospect.app)
```

산출물: **`dist/InvestRetrospect.app` (arm64, ~56MB)**

다른 Mac 에 배포하려면 .app 그대로 복사하거나 `.zip` 으로 압축. Intel Mac 호환이 필요하면 universal2 Python 환경에서 [InvestRetrospect.spec](InvestRetrospect.spec) 의 `target_arch='universal2'` 로 변경 후 재빌드.

---

## 트러블슈팅

**Q. 앱을 열었더니 "확인되지 않은 개발자" 경고가 떠요**
→ 미서명 .app 입니다. **우클릭 → 열기 → "열기"** 한 번만 누르면 이후엔 그냥 더블클릭으로 열립니다.

**Q. "<증권사명> APP KEY / SECRET KEY 가 설정되지 않았습니다" 오류**
→ [설정] 탭에서 활성 증권사의 키를 입력하세요. 자동 저장되니 별도 동작 필요 없음. 라디오로 다른 증권사를 골랐는지도 확인.

**Q. AI 코멘트가 생략됨**
→ 제공자가 "사용 안 함" 이거나, 선택된 제공자의 키/호스트가 비어있습니다. [설정] 탭에서 확인.

**Q. Ollama 사용 시 "연결할 수 없습니다" 오류**
→ Ollama 서버가 떠 있어야 합니다. 별도 터미널에서 `ollama serve` 또는 Ollama.app 실행.

**Q. 운영(prod) 환경에서 응답이 비어있어요**
→ 키움 API 는 발급 키마다 권한이 다릅니다. 모의에서 동작하는 키가 운영에선 안 될 수 있어요. 키움 개발자 센터에서 권한 확인.

**Q. 매매일지가 부분적으로만 채워졌어요**
→ 일부 TR (예: 잔고/예수금) 호출이 실패해도 일지는 만들어집니다. 로그의 `[warn] ... 조회 실패` 부분과 `.json` 원본 응답을 확인.

**Q. 설정이 안 저장되는 것 같아요**
→ 우측 하단 상태표시 (`✓ 저장됨` / `● 수정됨`) 를 확인. 직접 `~/.invest-retrospect/settings.json` 을 열어 내용 확인 가능.

**Q. journals 디렉토리에 계좌번호와 체결가가 평문으로 저장돼요**
→ 맞습니다. `.gitignore` 에 들어가 있긴 하지만 백업·공유 시 주의하세요.

---

## 사전 발급

### 키움 REST API
1. [openapi.kiwoom.com](https://openapi.kiwoom.com) → 회원가입 / 약정 동의
2. 앱 등록 후 **APP KEY / SECRET KEY** 발급
3. 모의투자도 같은 절차 (앱에서 환경 = 모의 선택)

### 한국투자증권(KIS / 한투) Open API
1. [apiportal.koreainvestment.com](https://apiportal.koreainvestment.com) → 회원가입 / 신청
2. **APP KEY / APP SECRET** 발급, 계좌번호는 **CANO(앞 8자리) + ACNT_PRDT_CD(뒤 2자리)** 형식
3. 모의투자는 별도 호스트(`openapivts...`) + 별도 키. 일부 TR(기간별 매매손익 등)은 실전 전용

### LS증권 Open API
1. [openapi.ls-sec.co.kr](https://openapi.ls-sec.co.kr) → 신청 / 승인
2. **APP KEY / APP SECRET** 발급
3. 잔고 / 예수금 등 일부 TR 은 **계좌 비밀번호 4자리** 가 본문에 필요 — 설정에서 함께 입력

### Gemini API (선택)
1. [aistudio.google.com](https://aistudio.google.com) → API key 발급 (무료, 카드 등록 불필요)
2. 무료 티어: `gemini-2.5-flash` 기준 일 200여 회 — 매매일지 한 장당 1콜이라 충분

### Ollama (선택, 로컬 LLM)
1. [ollama.com](https://ollama.com) 에서 설치
2. 모델 받기: `ollama pull llama3.1` 또는 `ollama pull qwen2.5:14b` 등
3. 키 발급 불필요, 인터넷도 불필요

---

## 라이선스 / 면책

개인 용도의 매매일지 자동화 도구입니다. AI 코멘트는 일반적인 매매 코칭일 뿐이며 **종목 추천이나 매수/매도 권유가 아닙니다**. 투자 손익은 본인 책임.
