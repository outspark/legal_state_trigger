# LST (Legal State Trigger) 분석 파이프라인

형사 수사 문서(고소장, 피의자신문조서, 수사보고서)로부터 **Legal State Trigger**를 자동 추출하고, Z3 SMT Solver로 논리적 일관성을 검증하며, LLM-as-a-Judge로 기망 고의성을 판정하는 LangGraph 기반 Multi-Agent 파이프라인입니다.

## 실행

```bash
cd lst_project
python -m src.lst_graph_app
```

---

## 파이프라인 워크플로우

```
START
  ├── process_complaint     ─┐
  ├── process_interrogation  ├── extraction
  └── process_evidence      ─┘       │
                                      ▼
                              causal_analysis
                            (인과 연쇄 + 시간별 그룹화)
                                      │
                                      ▼
                              z3_argumentation
                          (그룹별 중복·명제·충돌 + Z3)
                                      │
                                      ▼
                              intent_scoring
                          (그룹별 노드 고의성 평가)
                                      │
                                      ▼
                                   judge
                            (최종 고의성 판결)
                                      │
                              ┌───────┴───────┐
                              ▼               ▼
                        human_review      save_output
                        (Type 2 모호)         │
                              └───────────────┘
                                      ▼
                                     END
```

---

## 1단계: 병렬 문서 처리 + 필터링

| 노드 | 대상 문서 | 파일명 키워드 |
|---|---|---|
| `process_complaint` | 고소장 | `고소장` |
| `process_interrogation` | 신문조서, 진술서 | `조서`, `진술서` |
| `process_evidence` | 수사보고서 | `수사보고` |

세 노드가 **병렬(Parallelization)** 실행됩니다. 각 노드는 `filter_sentences`를 호출하여 5대 배제 규칙을 적용합니다:

1. 절차 메타데이터 (조사실 환경 등)
2. 단답 응답 (≤15어절)
3. 순수 신원 정보
4. 순수 감정 표현
5. 복수 LST 포함 시 분절 처리

**결과**: `filtered_sentences` (문서별 필터링된 문장 리스트)

---

## 2단계: LST 추출 (`extraction`)

필터링된 문장을 LLM에 전달하여 각 문서별로 LST 노드를 추출합니다.

**프롬프트**: `prompts/lst_extraction.md`

**처리**:
- 문서별로 LLM 호출 (Structured Output → `ExtractionResult`)
- 각 노드에 UUID 부여 (`uuid.uuid4()`)
- `doc_source`에 원본 파일명 기록

**추출 필드**: `id`, `doc_source`, `t`, `E`, `speaker`, `S0`, `L`, `S1`

**`speaker` (화자)**: 해당 사건을 진술·주장하는 주체의 역할. `피의자` / `피해자` / `증인` / `수사관` / `객관적증거` 중 하나. 동일 사건이라도 화자에 따라 증거적 신뢰도가 다르며, 이후 고의성 평가와 최종 판결에서 가중치로 반영됨.

**결과**: `extracted_lsts` (전체 LST_Node 리스트)

---

## 3단계: 인과 연쇄 분석 + 시간별 그룹화 (`causal_analysis`)

모든 노드를 LLM에 전달하여 인과 관계(`C_sources`)를 채우고, 시간별 그룹을 생성합니다.

**프롬프트**: `prompts/lst_aggregation.md`

**처리**:
1. LLM이 `S1(a)` → `S0(b)` 의미론적 연결을 분석하여 `C_sources` 채움
2. LLM이 `t` 기준으로 `temporal_groups` 생성 (없으면 프로그래밍적 폴백: `_group_by_time`)
3. 같은 시간대 내에서 **문서 순서(`doc_order`)** 기준으로 노드 정렬

**문서 순서**: 파일명 정렬 기반 (`1.1. 고소장` → `2. 피의자신문조서(1차)` → `5. 수사보고_금융거래정보` → ...)

**결과**: `causal_nodes` + `temporal_groups`

---

## 4단계: Z3 논증 분석 (`z3_argumentation`)

**시간 그룹별**로 LLM + Z3 검증을 수행합니다. 전체 노드를 한번에 보내지 않고 그룹 단위로 처리합니다.

**프롬프트**: `prompts/lst_argumentation.md`

각 시간 그룹에 대해 3단계 분석:

### 4-1. 중복 그룹 식별 (Duplicate Detection)
- `S1`이 의미론적으로 동일한 노드를 `duplicate_groups`로 묶음
- 서로 다른 문서에서 같은 사실을 묘사하는 경우

### 4-2. 원자 명제 추출 (Atomic Proposition Extraction)
- 시간 그룹 내 **모든 노드**에 대해 원자 명제를 추출
- 동일 사실을 논하는 노드는 동일한 명제 이름 공유 필수
- 명제 형식: `기망행위_발생`, `환불_이행`, `투자금_2000만원` 등

### 4-3. 충돌 그룹 식별 (Conflict Detection)
- 동일 명제에 대해 `value`가 상반되는 노드를 `conflict_groups`로 묶음
- 금액 불일치, 행위 이행 여부 불일치 등 포함

### Z3 SMT Solver 검증
충돌 그룹 내 모든 노드 쌍에 대해:
- **UNSAT → Attack**: 논리적 모순 → 두 노드 `Contested`
- **SAT → Support**: 일관성 확인

**Evidence 우선 규칙**: Evidence가 Interrogation을 Attack → Interrogation 노드 `Refuted`, Evidence 노드 `Accepted`

**Duplicate 엣지**: Attack 관계는 보존하며 Duplicate 부여 (Attack > Duplicate > Support > None)

**결과**: `assembled_nodes` (모든 엣지/상태 반영), `z3_proof_log` (SMT-LIB2 재현 스크립트)

---

## 5단계: 노드별 고의성 평가 (`intent_scoring`)

**시간 그룹별**로 LLM-as-a-Judge가 각 노드의 `S1`이 기망 고의를 시사하는 정도를 평가합니다.

**프롬프트**: `prompts/lst_intent_scoring.md`

**점수 기준**:

| 범위 | Type | 의미 |
|---|---|---|
| 0.90 ~ 0.95 | Type 0 (명시적 고의) | 피의자 직접 인정, 녹취·메시지 등 확정적 고의 증거 |
| 0.60 ~ 0.89 | Type 1 (강정황 고의) | 복수의 강한 정황 Trigger가 수렴하여 고의를 강하게 시사 |
| 0.45 ~ 0.59 | Type 2 (모호 고의) | 고의 존재가 불확정, 추가 수사 필요 |
| 0.00 ~ 0.44 | Type 3 (불인정) | 현재 증거로 고의 추론 불가능 또는 고의 부재 |

**고의성 상향 요인**: 핵심 구성요건 L (`기망행위`, `착오 유발`, `재산처분행위`, `재산상 이익 취득`), `speaker`가 `객관적증거`/`수사관`(높은 증거적 신뢰도), `speaker`가 `피의자`이면서 자백 성격(자기불리 진술), `Accepted` 상태, 동일 시간대 행위–결과 패턴 수렴, 복수 정황 Trigger 동일 방향 수렴

**고의성 하향 요인**: `구성요건 외` L, `Refuted` 상태, `speaker`가 `피의자`(자기변명)이면서 객관적 반박 증거 존재, 합리적 대안 설명 가능(계약 실패, 단순 과실)

**처리**: `AggregationResult`를 갱신하고, `graph_context`에 노드별 `intent_confidence`와 `intent_reasoning`을 포함시켜 다음 판결 단계로 전달

**결과**: 각 노드에 `intent_confidence` (0.0~1.0), `intent_reasoning` 부여

---

## 6단계: 최종 고의성 판결 (`judge`)

노드별 고의성 점수가 반영된 `graph_context`를 분석하여 글로벌 고의성 점수를 산출합니다.

**프롬프트**: `prompts/llm_judge.md`

**분석 방법론**:
1. 논증망 구조 파악: `Refuted` / `Contested` / `Accepted` 노드 확인 + 각 노드의 `speaker` 역할에 따른 증거적 가중치 판단 (예: `객관적증거`가 `피의자` 노드를 Refute → 강력한 고의 증거)
2. 인과 연쇄 완성도: `C_sources`를 따라 사기죄 4대 구성요건(`기망행위` → `착오 유발` → `재산처분행위` → `손해 발생`) 연결 여부 평가
3. 점수 산정

| Type | 점수 | 조건 |
|---|---|---|
| Type 0 (명시적 고의) | 0.90~1.00 | 피의자 명시적 인정 또는 반박 불가 직접 증거 |
| Type 1 (강정황 고의) | 0.60~0.85 | 핵심 변명 Refuted + 구성요건 연쇄 상당 연결 |
| Type 2 (모호 고의) | 0.45~0.59 | Contested 충돌은 있으나 완전한 Refute 없음 |
| Type 3 (불인정) | 0.00~0.44 | 고의성 입증 근거 부족 또는 피의자 주장 Support |

**결과**: `verified_intent` (`intent_score`, `intent_type`, `justification`)

**Type 2 판정 시** → `human_review_node`로 분기 (`interrupt()`로 인간 판단 요청)

---

## 7단계: 저장 (`save_output`)

| 출력 파일 | 내용 |
|---|---|
| `output/lst_analysis_result.json` | LST 노드(intent_confidence 포함), 논증 관계, verified_intent 판결 |
| `output/z3_proof_log.json` | 시간 그룹별 원자 명제 + SMT-LIB2 스크립트 (메인 JSON에서 분리) |
| `output/lst_run.log` | 파이프라인 실행 로그 (DEBUG 레벨) |

**`lst_analysis_result.json` 구조**:
- `aggregated_graph.assembled_nodes[]`: 각 노드에 `intent_confidence`, `intent_reasoning`, `argumentative_edges`, `v_status` 포함
- `verified_intent`: `intent_score`, `intent_type`, `justification`
- `requires_human_review`, `human_feedback`

**`z3_proof_log.json`**: `AggregationResult`에서 `z3_proof_log`를 분리 저장. 각 `smt_lib2` 값을 `.smt2` 파일로 저장하면 Z3/CVC5로 직접 실행 가능

---

## 시각화

| 파일 | 용도 |
|---|---|
| `lst_viewer.html` | vis-timeline 기반 문서별 타임라인 (중복 클러스터링, 충돌 표시) |
| `lst_trigger_viz.html` | D3.js 시간 기반 계단형 상태 전이 차트 |
| `lst_state_diagram.html` | D3.js 상태 중심 계단형 다이어그램 (X: t, Y: intent_confidence, L 필터) |

---

## 프로젝트 구조

```
lst_project/
├── src/
│   ├── lst_graph_app.py    # LangGraph 그래프 빌드 및 엔트리포인트
│   ├── nodes.py            # 워크플로우 노드 (필터, 추출, 인과, Z3, 고의성, 판결, 저장)
│   ├── schemas.py          # Pydantic 모델 및 InvestigationState 정의
│   ├── z3_eval.py          # Z3 SMT Solver 검증 + SMT-LIB2 생성
│   ├── filter_sentences.py # 5대 배제 규칙 기반 문장 필터링
│   ├── prompts.py          # 프롬프트 파일 로더
│   └── config.py           # 설정 로더 (app_config.yaml)
├── prompts/
│   ├── lst_extraction.md       # LST 노드 추출 프롬프트
│   ├── lst_aggregation.md      # 인과 연쇄 + 시간 그룹화 프롬프트
│   ├── lst_argumentation.md    # 중복·명제·충돌 식별 프롬프트
│   ├── lst_intent_scoring.md   # 노드별 고의성 평가 프롬프트
│   └── llm_judge.md            # 최종 판결 프롬프트
├── configs/
│   └── app_config.yaml     # 경로, 임계값, LLM 설정
├── lst_viewer.html         # vis-timeline 시각화
├── lst_trigger_viz.html    # D3.js 시간 기반 시각화
└── lst_state_diagram.html  # D3.js 상태 중심 시각화
```
