# ValueScope

AI-powered company valuation and peer analysis platform — 고객사와 동종업계 기업의 공시·재무·주가·수주 데이터를 수집하고, **근거(공식 공시·수치·계산 과정)를 연결해** 기업가치와 고평가·저평가 여부를 분석하는 Windows 로컬 웹 애플리케이션입니다.

> ⚠️ 본 프로그램의 결과는 투자 권유가 아닙니다. 확인되지 않은 지표는 0이 아니라 ‘데이터 없음/확인 불가/평가 보류’로 표시하며, 실제 데이터가 없으면 분석값을 만들어내지 않습니다.

## 현재 구현 상태

| PHASE | 내용 | 상태 |
|---|---|---|
| 0 | 저장소·폴더 구성, FastAPI/React 초기화, PostgreSQL·Alembic, 보안 설정(gitleaks, hooks) | ✅ |
| 1 | 고객사 등록·중복 방지, 프로젝트 카드, DART 기업 검색, 동종업계 탐색·분류·근거·적합성 점수, 편집, 비교군 버전 확정 | ✅ (실데이터는 `DART_API_KEY` 필요) |
| 2 | DART·KRX 재무/주가/공시/수주 증분 수집, 가치평가 엔진, AI 분석 | ⏳ 예정 |
| 3 | 결과 대시보드·ECharts 그래프·근거 기반 보고서 | ⏳ 예정 |
| 4 | Snapshot·이력 비교·재분석 | ⏳ 예정 |

## 기술 스택

- **Backend**: Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL, Pydantic, Pandas/NumPy, Statsmodels
- **Frontend**: React, TypeScript, Vite, Tailwind CSS, Apache ECharts, React Router, TanStack Query
- **AI**: Provider Adapter (`anthropic` API / `claude_cli` / `none`). 수치 계산은 Python, LLM은 공시 해석·근거 작성·검증 담당

## Windows 설치 및 실행

### 1) 사전 준비
- Git, Python 3.12+, Node.js 20+
- PostgreSQL (로컬 설치) **또는** Docker Desktop (`docker-compose.yml` 제공)
- (권장) Gitleaks: `winget install Gitleaks.Gitleaks`

### 2) 저장소 받기 및 보안 훅 활성화
```powershell
git clone https://github.com/leejunsung99/value-scope.git
cd value-scope
git config core.hooksPath .githooks   # 커밋/푸시 전 민감정보 자동 검사
```

### 3) 환경변수 설정
```powershell
Copy-Item .env.example .env
notepad .env    # DART_API_KEY, (선택) KRX_API_KEY, AI_PROVIDER/AI_API_KEY 입력
```

| 변수 | 설명 |
|---|---|
| `DATABASE_URL` | PostgreSQL 접속 URL (아래 스크립트가 자동 기록) |
| `DART_API_KEY` | [OpenDART](https://opendart.fss.or.kr/) 인증키 (무료). 기업 검색·동종업계 탐색에 필수 |
| `KRX_API_KEY` | [KRX Open API](https://openapi.krx.co.kr/) 인증키 (PHASE 2 주가 수집) |
| `NEWS_API_KEY` | 뉴스 검색 API (선택) |
| `AI_PROVIDER` | `claude_cli` (기본, 로컬 Claude Code CLI 로그인 사용) · `anthropic` (API 키 필요) · `none` |
| `CLAUDE_CLI_MODEL` | CLI 모델 별칭 `opus` · `sonnet` · `haiku` (CLI는 API 모델 ID를 받지 않음) |
| `AI_API_KEY` / `AI_MODEL` | Anthropic API 키 / 모델 ID (기본 `claude-opus-5-5`) |
| `APP_HOST` / `APP_PORT` | 기본 `127.0.0.1:8000` (localhost 외 바인딩은 차단) |

### 4) 데이터베이스 설정
로컬 PostgreSQL을 사용하는 경우 (관리자 비밀번호는 입력만 받고 저장하지 않습니다):
```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_db.ps1
```
→ `valuescope` 계정/DB와 테스트 DB를 만들고, 무작위 비밀번호로 `.env`의 `DATABASE_URL`을 기록한 뒤 마이그레이션을 적용합니다.

Docker를 사용하는 경우: `.env`에 `POSTGRES_PASSWORD`와 `DATABASE_URL`을 직접 입력하고 `docker compose up -d`.

### 5) 실행
```powershell
powershell -ExecutionPolicy Bypass -File start.ps1          # http://127.0.0.1:8000
powershell -ExecutionPolicy Bypass -File start.ps1 -Dev     # 개발모드 (Vite http://localhost:5173)
powershell -ExecutionPolicy Bypass -File start.ps1 -Rebuild # 프론트엔드 재빌드 후 실행
```
최초 실행 시 가상환경 생성, 의존성 설치, 마이그레이션, 프론트엔드 빌드를 자동으로 수행합니다.

## 사용 흐름 (PHASE 1)

1. **새 고객사 등록** — 기업명/종목코드로 OpenDART 기업목록 검색 → 선택 (이미 등록된 기업은 기존 프로젝트로 이동)
2. **STEP 1 동종업계 검색** — 버튼을 누를 때만 실행되며:
   - 고객사 **최신 사업보고서 ‘II. 사업의 내용’** 원문 확보 (같은 접수번호는 재다운로드하지 않음)
   - **초기 후보 목록**을 DART 기업목록·기업개황으로 검증 (종목코드·상장시장·사명 불일치 표시)
   - 사업보고서 본문에 **언급된 상장사**를 새 후보로 발견하고 원문 발췌를 근거로 저장
   - (AI 설정 시) 카테고리 A~E 분류·루브릭 평가. **AI 인용문은 공시 원문과 대조해 확인된 것만 근거로 채택**
   - 비교 적합성 점수: 사전 정의된 6개 항목 가중치(제품 30·공정 20·고객 15·매출구조 15·CAPEX 사이클 10·데이터 가용성 10). 근거 없는 항목은 ‘미평가’로 분모에서 제외, 커버리지 50% 미만이면 ‘평가 보류’
3. **편집** — 채택/제외/복원, 카테고리·활용 구분(적정가치 비교용/산업 참고용/기술 경쟁용) 변경, 직접 추가. 변경 즉시 DB 저장(임시 작업본)
4. **비교기업 확정** — 채택된 기업을 `비교군 vN`으로 고정. 이후 수정은 새 버전이 되며 과거 버전은 변경되지 않습니다. 비상장 기업(예: 해외 비상장사)은 ‘적정가치 비교용’으로 지정할 수 없습니다.

## 테스트

```powershell
cd backend
.venv\Scripts\python -m pytest -q                       # SQLite 인메모리
$env:TEST_DATABASE_URL = "<.env의 TEST_DATABASE_URL>"; .venv\Scripts\python -m pytest -q   # PostgreSQL
cd ..\frontend; npm test; npm run build
```
테스트 데이터는 모두 합성(가짜) 픽스처이며 실제 공시 데이터가 아닙니다.

## 보안 원칙 (Public 저장소)

- `.env`, DB 데이터·백업, `data/`(원본 공시·수집 데이터), 분석 결과·보고서, 로그는 `.gitignore`로 제외
- `.env.example`에는 변수 이름만 존재
- `scripts/check_secrets.ps1` + Gitleaks가 pre-commit(스테이징)·pre-push(전체 이력)에서 자동 검사
- API 키는 백엔드에서만 사용하며 프론트엔드로 전달되지 않음 (`/api/system/status`는 설정 여부만 반환)
- 서버는 `127.0.0.1`에만 바인딩

## 데이터 출처 및 이용 조건

- 공시 데이터: 금융감독원 OpenDART API — [이용약관](https://opendart.fss.or.kr/intro/terms.do)을 준수하며, 수집된 원본 데이터는 저장소에 포함하지 않습니다.
- 시장 데이터: KRX Open API (PHASE 2) — 이용 조건에 따라 재배포하지 않습니다.
- AI: `claude_cli` 모드는 사용자의 로컬 Claude Code 로그인을 사용합니다. 무인·자동 실행 용도는 해당 요금제 약관을 확인하세요. 안정적인 무인 실행에는 Anthropic API(`AI_PROVIDER=anthropic`)를 권장합니다.

## 디렉터리 구조

```
backend/   FastAPI 앱 (app/), Alembic 마이그레이션, Pytest
frontend/  React + Vite + Tailwind
scripts/   setup_db.ps1, check_secrets.ps1
.githooks/ pre-commit / pre-push 민감정보 검사
start.ps1  간편 실행
```
