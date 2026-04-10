# Colosseum Project Specification

> 본 문서는 Colosseum 프로젝트의 전체 사양을 단일 파일로 정리한 것이다.
> 세부 운영 규칙은 `runtime-protocol.md`, `agent-governance.md`, `persona-authoring.md`에 위임한다.

---

## 1. 개요 (Overview)

**Colosseum**은 여러 AI 모델(gladiator)을 같은 입력 위에서 구조화된 토론·판정 워크플로우로 겨루게 하는 **Multi-Agent Debate Arena**다. 자유 채팅이 아니라 **바운드 된 라운드 기반 토론**을 수행하여, 동일 조건·동일 근거·동일 판정 기준으로 모델을 비교할 수 있게 한다.

### 1.1 핵심 가치

| 속성 | 설명 |
|------|------|
| **Fair** | 모든 에이전트가 동일한 Frozen Context 위에서 출발 |
| **Traceable** | 플랜, 라운드, 판정, 최종 리포트까지 모든 아티팩트 파일로 보존 |
| **Cost-Controlled** | 실측 토큰/비용 추적, Budget/Quota 기반 중단 |
| **Evidence-First** | 근거 없는 주장은 판정에서 페널티 |
| **Extensible** | CLI 기반 Provider 추상화로 어떤 모델이든 래핑 가능 |

### 1.2 해결하는 문제

1. 여러 모델을 **공정하게** 비교 (동일 컨텍스트, 동일 프롬프트 규약)
2. 자유 채팅 대신 **바운드 된 토론**으로 발산/비용 폭주 방지
3. **근거 기반 판정**으로 "말 잘하는 모델"이 아닌 "답이 맞는 모델"을 고름
4. 로컬 모델(Ollama/HF GGUF)과 원격 모델(Claude/Codex/Gemini)을 **동일 무대**에 올림
5. 디베이트 워크플로우를 재사용해 **코드 리뷰**까지 확장

---

## 2. 기술 스택

### 2.1 런타임

- **Python 3.11+** (strict)
- **FastAPI 0.115+** / **Uvicorn 0.35+** (웹 서버 & REST API)
- **Pydantic 2.10+** (도메인 모델 검증)
- **argparse** 기반 CLI (외부 CLI 파서 의존성 없음)
- **fpdf2 2.8+** (PDF 리포트)
- **httpx 0.28+** (비동기 HTTP)

### 2.2 외부 통합

- **Anthropic Claude CLI** (`claude:<model>`)
- **OpenAI Codex CLI** (`codex:<model>`)
- **Google Gemini CLI** (`gemini:<model>`)
- **Ollama** (로컬 LLM 데몬)
- **HuggingFace Hub** (GGUF 모델 검색/다운로드/변환)
- **llmfit** (GPU 메모리 적합성 검증)
- **llama.cpp** (safetensors → GGUF 변환, 선택적)
- **tmux** (라이브 모니터링 패널)

### 2.3 빌드 & 패키징

- `setuptools 68+` / `pyproject.toml`
- `pip install -e .` editable install
- 엔트리포인트: `colosseum.cli:main`
- 테스트: `pytest 8.3+`
- 린트/포맷: `ruff 0.11+`

### 2.4 규모

- 약 **54 Python 파일 / 11,400 LOC** (src)
- 웹 UI: JS/HTML5 (app.js 117KB, report.js 30KB, styles.css 53KB)
- 테스트: 15개 파일

---

## 3. 아키텍처

### 3.1 6-Layer 모델

```
┌─────────────────────────────────────────────────────────┐
│ 1. Interface Layer                                      │
│    colosseum.main / cli / api.* / monitor               │
│    FastAPI 앱, CLI 파서, tmux 모니터, SSE 라우트          │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│ 2. Application Layer (services.*)                       │
│    ColosseumOrchestrator · DebateEngine · JudgeService  │
│    ReportSynthesizer · ReviewOrchestrator               │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│ 3. Domain Model Layer (core.*)                          │
│    Pydantic 모델 · config · 가격표 · depth profile       │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│ 4. Infrastructure Layer                                 │
│    FileRunRepository · ProviderRuntimeService           │
│    ContextBundleService · BudgetManager · EventBus      │
│    LocalRuntimeService · ContextMediaService            │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│ 5. Report Generation Layer                              │
│    report_synthesizer · pdf_report · markdown_report    │
│    review_prompts                                       │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│ 6. Provider Layer (providers.*)                         │
│    BaseProvider · CliWrapperProvider · MockProvider     │
│    Claude/Codex/Gemini/Ollama/HuggingFace Adapters      │
└─────────────────────────────────────────────────────────┘
```

### 3.2 디렉토리 구조

```
src/colosseum/
├── main.py                     FastAPI 앱 팩토리
├── cli.py                      CLI 엔트리 (~118KB)
├── monitor.py                  tmux 라이브 모니터
├── bootstrap.py                DI 컨테이너 초기화
├── api/
│   ├── routes.py               라우터 컴포지션
│   ├── routes_runs.py          Run CRUD · SSE · 판정 액션
│   ├── routes_personas.py      페르소나 CRUD · 생성
│   ├── routes_quotas.py        Provider 쿼터 관리
│   ├── routes_hf.py            HuggingFace Hub 통합
│   ├── routes_setup.py         Setup · Discovery · Local runtime
│   ├── sse.py                  SSE 직렬화
│   └── validation.py           요청 검증
├── core/
│   ├── models.py               55+ Pydantic 모델
│   ├── config.py               Depth profile · Review phase
│   └── pricing.py              25+ 모델 가격표
├── providers/
│   ├── base.py                 BaseProvider 추상
│   ├── factory.py              provider 인스턴스화
│   ├── cli_wrapper.py          CLI 엔벨로프 파서 (실측 토큰)
│   ├── cli_adapters.py         Claude/Codex/Gemini/Ollama/HF 어댑터
│   ├── command.py              범용 CLI 래퍼
│   ├── mock.py                 결정론적 Mock provider
│   └── presets.py              모델 프리셋
├── services/
│   ├── orchestrator.py         Run 라이프사이클 컴포지션
│   ├── debate.py               DebateEngine · 라운드 실행
│   ├── judge.py                플랜 스코어링 · 어드저디케이션
│   ├── report_synthesizer.py   AI 합성 리포트
│   ├── review_orchestrator.py  6-phase 코드 리뷰
│   ├── repository.py           FileRunRepository
│   ├── provider_runtime.py     Provider 실행 · 쿼터 복구
│   ├── context_bundle.py       Frozen Context 구성
│   ├── context_media.py        이미지 추출 · 요약
│   ├── budget.py               BudgetManager
│   ├── local_runtime.py        Ollama 데몬 · GPU 감지
│   ├── event_bus.py            DebateEventBus (스트리밍)
│   ├── hf_hub.py               HuggingFace 통합
│   ├── chat_parser.py          응답 파싱
│   ├── chat_persona_generator.py  대화형 페르소나 생성
│   ├── persona_interview.py    페르소나 인터뷰
│   ├── pdf_report.py           PDF 익스포트
│   ├── markdown_report.py      Markdown 익스포트
│   └── review_prompts.py       코드 리뷰 프롬프트
├── personas/
│   ├── registry.py             PersonaRegistry
│   ├── loader.py               레거시 로더
│   ├── generator.py            페르소나 생성 서비스
│   ├── prompting.py            페르소나 프롬프트 빌더
│   ├── builtin/                내장 23 페르소나 (.md)
│   └── custom/                 사용자 정의
└── web/
    ├── index.html              아레나/세팅 화면
    ├── report.html             리포트 뷰어
    ├── app.js                  (117KB)
    ├── report.js               (30KB)
    └── styles.css              (53KB)
```

### 3.3 아티팩트 저장소

```
.colosseum/
├── runs/<run_id>/
│   ├── run.json                전체 Run 상태
│   ├── context_bundle.json     Frozen Context
│   ├── task.json               Task 스펙
│   ├── plans/<agent_id>.json   에이전트별 플랜
│   ├── debate/round_<i>.json   라운드 아티팩트
│   └── judge/verdict.json      최종 판정
├── reviews/<review_id>.md      코드 리뷰 리포트
├── personas/custom/            사용자 페르소나
└── state/
    ├── provider_quotas.json    Provider 쿼터 상태
    ├── local_runtime.json      GPU · 자동시작 설정
    ├── local_runtime.pid       관리 데몬 PID
    └── local_runtime.log       데몬 로그
```

---

## 4. 도메인 모델

### 4.1 핵심 엔티티 (core/models.py)

| 엔티티 | 역할 |
|--------|------|
| `ExperimentRun` | Run 전체 상태 (run_id, status, agents, judge, budget, plans, rounds, verdict, usage) |
| `AgentConfig` | Gladiator 정의 (provider, model, specialty, optional persona) |
| `PersonaDefinition` | 페르소나 메타 + 바디 (YAML frontmatter + Markdown) |
| `TaskSpec` | 토론 주제 (title, problem_statement, task_type, success_criteria, constraints) |
| `FrozenContextBundle` | 결정론적 컨텍스트 번들 (sources, checksum, summary) |
| `PlanDocument` | 에이전트 플랜 (summary, evidence, architecture, risks, weaknesses, trade-offs) |
| `DebateRound` | 라운드 (type, agenda, agent_messages, adjudication, usage) |
| `JudgeDecision` / `JudgeVerdict` | 판정 (CONTINUE / FINALIZE / REVISION / HUMAN 및 WINNER / MERGED / REVISION / NO_DECISION) |
| `ReviewReport` | 코드 리뷰 결과 (phase_results, findings, summary) |
| `BudgetPolicy` / `BudgetLedger` | 토큰·라운드·비용 예산 |
| `ProviderQuotaState` / `PaidProviderPolicy` | Provider 쿼터 (FAIL / SWITCH_TO_FREE / WAIT_FOR_RESET) |

### 4.2 Run 라이프사이클

```
1. RunCreateRequest           사용자 요청 (task, agents, judge, budget)
         ↓
2. Context Freeze             FrozenContextBundle 생성 + checksum
         ↓
3. Parallel Plan Generation   모든 에이전트가 동일 컨텍스트로 PlanDocument 생성
         ↓
4. Plan Scoring               JudgeService가 플랜을 스코어링
         ↓
5. Debate Rounds              agenda 주도 라운드 반복
         (CRITIQUE → REBUTTAL → SYNTHESIS → FINAL_COMPARISON → TARGETED_REVISION)
         ↓  (중단 조건: budget / max_rounds / convergence / novelty_collapse)
6. Judge Finalize             JudgeVerdict 도출
         ↓
7. Report Synthesis           AI 합성 FinalReport (Markdown/PDF)
         ↓
8. Persistence                .colosseum/runs/<run_id>/ 에 전체 아티팩트 저장
```

### 4.3 코어 불변식 (Invariants)

1. **Context 결정성**: 같은 입력은 같은 bundle_id, 같은 checksum을 생산해야 한다.
2. **Evidence-First**: 판정은 frozen bundle 또는 명시 근거를 인용한 주장만 긍정 점수를 준다.
3. **Bounded Debate**: 모든 라운드는 agenda(한 번에 하나의 이슈)와 중단 규칙 하에 실행된다.
4. **Real Usage**: 토큰/비용은 provider 출력에서 실측한다. 추정 필드와 실측 필드를 섞지 않는다.
5. **Artifact Completeness**: Run 종료 시 plan/round/verdict/report가 모두 디스크에 남아야 한다.

---

## 5. CLI 인터페이스

엔트리: `colosseum.cli:main` (argparse 기반)

### 5.1 명령 목록

| 명령 | 설명 |
|------|------|
| `colosseum setup [providers...]` | CLI provider 설치/인증 |
| `colosseum serve` | 웹 UI 서버 기동 |
| `colosseum debate -t "..." -g A B` | 터미널에서 디베이트 실행 |
| `colosseum review -t "..." -g A B` | 멀티페이즈 코드 리뷰 |
| `colosseum monitor [run_id]` | tmux 라이브 모니터 패널 |
| `colosseum models` | 사용 가능 모델 목록 |
| `colosseum personas` | 페르소나 목록 |
| `colosseum history` | 과거 런 목록 |
| `colosseum show <run_id>` | 과거 런 결과 표시 |
| `colosseum delete <run_id\|all>` | 런 삭제 |
| `colosseum check` | CLI 도구 가용성 검증 |
| `colosseum local-runtime status` | Ollama 런타임 상태 |

### 5.2 주요 플래그

**debate**

| 플래그 | 설명 |
|--------|------|
| `-t, --topic` | 토론 주제 (필수) |
| `-g` | `provider:model` 형식 2개 이상 (e.g. `claude:sonnet-4-6 gemini:gemini-2.5-pro`) |
| `-j, --judge` | 판정자 (`provider:model` 또는 `human`) |
| `-d, --depth` | 1–5 (기본 3, depth profile 매핑) |
| `--dir` | 컨텍스트 디렉토리 |
| `-f` | 특정 파일 |
| `--mock` | Mock provider (무료 테스트) |
| `--monitor` | tmux 모니터 자동 기동 |
| `--timeout` | 페이즈당 타임아웃 |

**review**

| 플래그 | 설명 |
|--------|------|
| `-t, --topic` | 리뷰 대상 설명 |
| `-g` | 리뷰어 에이전트 |
| `--phases` | 실행 페이즈 (A-F, 기본 A-E) |
| `-j, --judge` | 판정 모델 |
| `-d, --depth` | 페이즈당 디베이트 깊이 |
| `--dir` / `-f` | 컨텍스트 |
| `--diff` | 최근 git diff 포함 |
| `--lang` | 응답 언어 (ko, en, ja 등) |
| `--rules` | 프로젝트 규칙 파일 경로 |
| `--timeout` | 페이즈당 타임아웃 |

### 5.3 라이브 UX

- 에이전트 상태 메시지 ("Agent X planning…", "Agent Y debating…")
- 플랜 평가 스코어 바
- 라운드 요약 (채택된 주장 / 미해결 이슈)
- 최종 판정 출력 (winner / merged / targeted revision)
- 에이전트별 토큰·비용 브레이크다운 (always-on)

---

## 6. 핵심 기능

### 6.1 Debate System

- **Gladiators**: 2명 이상의 AI 에이전트가 Frozen Context에서 독립적으로 플랜 생성
- **Plan Phase**: 구조화된 플랜 문서 생산 (가정, 근거, 아키텍처, 리스크 등)
- **Judge Modes**:
  - `AUTOMATED` — 휴리스틱 기반 판정
  - `AI` — 임의 모델을 판정자로 사용
  - `HUMAN` — pause/resume 가능한 사람 판정
- **Round Sequence**: `CRITIQUE → REBUTTAL → SYNTHESIS → FINAL_COMPARISON → TARGETED_REVISION`
- **Stop Rules**: `token_budget_exhausted`, `maximum_rounds_reached`, `convergence_detected`, `novelty_collapsed`
- **Verdict Types**: `WINNER`, `MERGED`, `TARGETED_REVISION`, `NO_DECISION`

### 6.2 Code Review System (6 Phases)

| Phase | 주제 | 필수 여부 |
|-------|------|-----------|
| **A** | Project Rules — 컨벤션, 네이밍, 린터/포맷터 | 기본 |
| **B** | Implementation — 정확성, 엣지 케이스, 에러 처리 | 기본 |
| **C** | Architecture — 디자인 패턴, 모듈 분리, 의존성 | 기본 |
| **D** | Security/Performance — 취약점, 메모리, 동시성, 최적화 | 기본 |
| **E** | Test Coverage — 단위/통합 테스트 구조 | 기본 |
| **F** | Red Team — 적대적 입력, 인증 우회, 권한 상승 | opt-in |

각 페이즈는 리뷰어 간 미니 디베이트로 실행되고, 결과는 Markdown/PDF 리포트로 집계된다.

### 6.3 Persona System

- **23개 내장 페르소나** (Andrew Ng, Elon Musk, Karpathy, Demis Hassabis, 아티스트, 운동선수 등)
- **파일 포맷**: YAML frontmatter (id, name, description, version, tags, active) + Markdown 바디
  - 바디: role, debating style, voice signals, signature moves, speech patterns, vocabulary, sample sentences
- **Custom 페르소나**: API로 생성, `.colosseum/personas/custom/`에 저장
- **Chat-to-Persona**: 대화형 설문으로 페르소나 자동 생성
- **Voice Differentiation**: 각 페르소나는 말투, 어휘, 감정 톤이 구별됨
- **Judge/Report 커스터마이징**: 판정자와 리포트 작성자에도 페르소나 적용 가능

자세한 스펙: [`persona-authoring.md`](./persona-authoring.md)

### 6.4 HuggingFace 통합

- **Search**: HF Hub에서 GGUF 모델 키워드 검색
- **Pull**: `ollama pull hf.co/<org>/<model>` 로 다운로드
- **Register**: 로컬 GGUF 파일 임포트, safetensors/PyTorch → GGUF 변환
- **Fit Check**: `llmfit`로 GPU VRAM 적합성 검증
- **Conversion**: llama.cpp convert script로 자동 변환 (선택적)
- 구성: `LLAMA_CPP_CONVERT_SCRIPT` 환경 변수

### 6.5 Provider 지원

| Provider | Type | 대표 모델 | 전제 |
|----------|------|-----------|------|
| Claude | CLI | opus-4-6, sonnet-4-6, haiku-4-5 | `claude` CLI |
| Codex | CLI | gpt-5.4, o3, o4-mini | `codex` CLI |
| Gemini | CLI | 2.5-pro, 3.1-pro, 3-flash | `gemini` CLI |
| Ollama | Local | llama3.3, qwen2.5, mistral, deepseek-r1 | 관리 데몬 |
| HuggingFace | Local | Any GGUF | Ollama 백엔드 |
| Mock | Built-in | mock-default | 테스트 |
| Custom | CLI | User-defined | BYO |

### 6.6 실측 비용 추적

- Provider 출력에서 실측 prompt/completion 토큰 추출
- `core/pricing.py`의 25+ 모델 가격표 기반 계산
- CLI 결과에 에이전트별 비용 브레이크다운 상시 표시
- 라운드별 토큰 장부(BudgetLedger) 관리
- 무료/유료 쿼터 정책 분리

### 6.7 Report Generation

- AI 합성 최종 리포트 (구조화 분석, 핵심 결론)
- 근거 인용이 있는 판정 설명
- 라운드 하이라이트 (채택된 주장, 미해결 이슈)
- **PDF** (fpdf2) / **Markdown** 익스포트
- Run 단위 custom report instruction 지원

---

## 7. 구성 (Configuration)

### 7.1 구성 소스

1. **pyproject.toml** — 메타데이터, 의존성, 엔트리포인트
2. **core/config.py**
   - `ARTIFACT_ROOT`, `REVIEW_REPORT_ROOT`, `STATE_ROOT`
   - `DEPTH_PROFILES` (1–5, 판정자 행동 매핑)
   - `REVIEW_PHASE_CONFIG` (6 phases with criteria)
   - Evidence policy (frozen-only vs internet search)
   - Prompt budget 상한 (28K chars)
   - Round sequence 정의
3. **core/pricing.py** — 모델별 토큰 가격
4. **환경 변수**
   - `COLOSSEUM_DISABLE_STARTUP_PROBE` — 기동 시 백그라운드 모델 probing 비활성
   - `OLLAMA_HOST` — 기본 `127.0.0.1:11435`
   - `COLOSSEUM_LOCAL_RUNTIME_MANAGED` — local runtime service가 설정
   - `LLAMA_CPP_CONVERT_SCRIPT` — HF→GGUF 변환기 경로
5. **퍼시스턴트 상태**
   - `.colosseum/state/provider_quotas.json`
   - `.colosseum/state/local_runtime.json`
   - `.colosseum/state/local_runtime.pid` / `.log`

### 7.2 런타임 구성 객체

- `BudgetPolicy` — `total_token_budget`, `per_round_token_limit`, `max_rounds`, 라운드 타임아웃
- `ProviderConfig` — provider type, model, CLI command, 환경, 가격
- `JudgeConfig` — 모드 (AUTOMATED/AI/HUMAN), provider 옵션
- `PaidProviderPolicy` — 쿼터 관리, 소진 시 액션

자세한 운영 규약: [`runtime-protocol.md`](./runtime-protocol.md)

---

## 8. 테스트

- 프레임워크: **pytest 8.3+**
- 경로: `tests/`
- 주요 테스트:
  - `test_orchestrator.py` — 런 라이프사이클, 플랜 생성, 디베이트 플로우
  - `test_persona_*.py` — 페르소나 로딩/레지스트리/생성
  - `test_hf_hub.py` — HuggingFace 통합
  - `test_paid_quotas.py` — 쿼터 관리/소진
  - `test_qa_fixes.py` — CLI 버그 및 플래그 회귀
  - `test_runtime_guards.py` — 프롬프트 컨트랙트 가드레일
  - `test_evidence_policy.py` — 근거 우선 판정
  - `test_chat_parser.py` — 응답 파싱
  - `test_local_runtime_*.py` — Ollama 데몬/GPU 감지
  - `test_ui.py` — 웹 UI 라우트
  - `test_monitor.py` — tmux 모니터링
  - `test_vlm_support.py` — 이미지/VLM

---

## 9. 확장 포인트

| 확장 대상 | 방법 |
|-----------|------|
| 새 Provider | `providers/cli_adapters.py`에 CLI 어댑터 추가 후 `factory.py` 등록 |
| 새 Judge 전략 | `services/judge.py`에 신규 스코어러/에이전다 선택기 플러그인 |
| 새 Review Phase | `core/config.REVIEW_PHASE_CONFIG`에 엔트리 추가, `services/review_prompts.py`에 프롬프트 추가 |
| 새 Persona | `.colosseum/personas/custom/<id>.md` 생성 또는 API `/personas` 사용 |
| 새 Report Format | `services/`에 `xxx_report.py` 추가 후 `report_synthesizer`에서 호출 |
| 새 Depth Profile | `core/config.DEPTH_PROFILES`에 프로필 추가 |

---

## 10. 주요 아키텍처 결정 요약

| 결정 | 이유 |
|------|------|
| **Frozen Context** | 모델 간 공정 비교 + 재현성 보장 |
| **Agenda-Driven Rounds** | 토론 발산 방지, 라운드마다 단일 이슈 집중 |
| **Evidence-First Judging** | "말 잘하는 모델"이 이기는 문제 방지 |
| **CLI Wrapper Provider** | SDK 없는 모델도 동일 방식으로 통합 |
| **Managed Ollama Daemon** | 로컬/원격 모델을 무대에서 동일하게 취급 |
| **File-Backed Repository** | DB 없이도 완전한 재현·감사 가능 |
| **Pydantic Core Models** | 런타임 검증 + JSON 직렬화 일원화 |
| **Event Bus (SSE)** | 라이브 모니터링·웹 UI 스트리밍 지원 |
| **Code Review as Debate** | 디베이트 엔진 재사용으로 리뷰 기능 가속 |

---

## 11. 이상적인 사용 사례

- **아키텍처 결정** (마이크로서비스 vs 모놀리스, DB 선택 등)
- **코드 리뷰 스케일링** (멀티페이즈 자동 리뷰 + AI 판정)
- **모델 벤치마킹** (Claude vs Gemini vs 로컬 모델 병렬 비교)
- **페르소나 기반 리서치** (다른 관점의 전문가 관점)
- **Human-in-the-loop 워크플로우** (최종 결정만 사람이)

---

## 12. 참조 문서

- [`../../README.md`](../../README.md) — 제품 개요, 퀵스타트, CLI/API 레퍼런스
- [`../architecture/overview.md`](../architecture/overview.md) — 아키텍처 개요
- [`../architecture/design-philosophy.md`](../architecture/design-philosophy.md) — 설계 철학
- [`./runtime-protocol.md`](./runtime-protocol.md) — 런 라이프사이클, 스트리밍 컨트랙트, depth 프로필
- [`./agent-governance.md`](./agent-governance.md) — Agent · Persona · Provider 경계
- [`./persona-authoring.md`](./persona-authoring.md) — 페르소나 파일 포맷과 검증
