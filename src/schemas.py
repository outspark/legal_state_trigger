"""LST 그래프 스키마: Pydantic 모델 및 상태 타입."""
import operator
from typing import List, Dict, Optional, Literal, Annotated
from typing_extensions import TypedDict
from pydantic import BaseModel, Field


def merge_dict(a: dict, b: dict) -> dict:
    """딕셔너리 병합용 리듀서."""
    return {**a, **b}


# ── 노드 및 추출 결과 ──
class LST_Node(BaseModel):
    id: str = Field(description="고유 노드 UUID (시스템 부여)")
    doc_source: str = Field(description="출처 문서 (Complaint, Interrogation, Evidence)")
    t: str = Field(description="발생 시점")
    E: str = Field(description="발생 사건 내용")
    speaker: Literal["피의자", "피해자", "증인", "수사관", "객관적증거"] = Field(
        default="수사관", description="해당 사건을 진술·주장하는 화자의 역할"
    )
    S0: str = Field(description="사건 전 법적/사실적 상태")
    L: str = Field(description="사기죄 구성요건 함수")
    S1: str = Field(description="사건 후 법적/사실적 상태")
    C_sources: List[str] = Field(default_factory=list)
    argumentative_edges: Dict[str, str] = Field(default_factory=dict)
    v_status: Literal["Accepted", "Contested", "Refuted"] = Field(default="Accepted")
    intent_confidence: float = Field(default=0.0, description="기망 고의성 신뢰도 (0.0~1.0)")
    intent_reasoning: str = Field(default="", description="고의성 판단 근거")


class ExtractionResult(BaseModel):
    lsts: List[LST_Node] = Field(description="추출된 LST 노드 목록")


# ── Task 1: 인과 연쇄 + 시간별 그룹화 ──
class TemporalGroup(BaseModel):
    time_label: str = Field(description="시간 그룹 라벨 (예: '2020년 2월 28일', '2020년 3월 중순')")
    node_ids: List[str] = Field(description="이 시간대에 속하는 노드 ID 목록")


class CausalChainResult(BaseModel):
    nodes_with_causality: List[LST_Node] = Field(description="C_sources가 채워진 노드 목록")
    temporal_groups: List[TemporalGroup] = Field(
        default_factory=list,
        description="시간별로 그룹화된 노드 목록",
    )


# ── Task 2: Z3 명제 추출 ──
class AtomicProposition(BaseModel):
    name: str = Field(
        description="정규화된 명제 이름 (예: 기망행위_발생). 같은 그룹 내 동일 이름 공유 필수."
    )
    value: bool = Field(
        description="이 노드가 해당 명제를 참(true)으로 주장하면 True, 거짓(false)으로 주장하면 False"
    )


class NodePropositions(BaseModel):
    node_id: str = Field(description="LST 노드 ID")
    doc_source: str = Field(description="노드 출처")
    propositions: List[AtomicProposition] = Field(description="이 노드가 주장하는 원자 명제 목록")


class ConflictGroup(BaseModel):
    node_ids: List[str] = Field(
        description="동일 사건을 다른 출처에서 묘사하되, 주장이 상충할 수 있는 노드 ID 목록 (최소 2개)"
    )
    event_summary: str = Field(description="해당 그룹이 묘사하는 사건 요약")


class DuplicateGroup(BaseModel):
    node_ids: List[str] = Field(
        description="S1이 의미론적으로 동일한 노드 ID 목록 (최소 2개)"
    )
    shared_S1_summary: str = Field(description="공유되는 S1 상태 요약")


class PropositionExtractionResult(BaseModel):
    node_propositions: List[NodePropositions] = Field(
        description="시간 그룹 내 모든 노드의 원자 명제 목록"
    )
    conflict_groups: List[ConflictGroup] = Field(description="동일 사건 충돌 그룹 목록")
    duplicate_groups: List[DuplicateGroup] = Field(
        default_factory=list,
        description="S1이 의미론적으로 동일한 노드 그룹 목록",
    )


# ── Z3 증명 로그 ──
class Z3PairResult(BaseModel):
    node_a: str = Field(description="첫번째 노드 ID")
    node_b: str = Field(description="두번째 노드 ID")
    relation: str = Field(description="Z3 판정 (Attack / Support / None)")
    smt_lib2: str = Field(description="SMT-LIB2 재현 스크립트")


class TemporalGroupLog(BaseModel):
    time_label: str = Field(description="시간 그룹 라벨")
    node_ids: List[str] = Field(description="그룹 내 노드 ID 목록")
    node_propositions: List[NodePropositions] = Field(
        default_factory=list, description="노드별 원자 명제"
    )
    duplicate_groups: List[DuplicateGroup] = Field(default_factory=list)
    conflict_groups: List[ConflictGroup] = Field(default_factory=list)
    z3_evaluations: List[Z3PairResult] = Field(default_factory=list)


# ── 최종 조립 결과 ──
class AggregationResult(BaseModel):
    assembled_nodes: List[LST_Node] = Field(description="모든 관계가 조립된 최종 LST 노드 목록")
    graph_summary: str = Field(description="논증망 핵심 구조 요약")
    z3_proof_log: List[TemporalGroupLog] = Field(
        default_factory=list,
        description="시간 그룹별 Z3 증명 로그 (SMT-LIB2 재현 가능)",
    )


# ── 노드별 고의성 평가 ──
class NodeIntentScore(BaseModel):
    node_id: str = Field(description="평가 대상 노드 ID")
    intent_confidence: float = Field(description="기망 고의성 신뢰도 (0.0~1.0)")
    reasoning: str = Field(description="판단 근거 (1~2문장)")


class IntentScoringResult(BaseModel):
    scores: List[NodeIntentScore] = Field(description="노드별 고의성 점수 목록")


class GlobalIntentEvaluation(BaseModel):
    intent_score: float = Field(description="기망 고의성 확률 (0.0 ~ 1.0)")
    intent_type: str = Field(description="Type 0 / Type 1 / Type 2 / Type 3")
    justification: str = Field(description="파괴된 변명망에 기반한 논거")


# ── 그래프 상태 ──
class InvestigationState(TypedDict):
    documents: Dict[str, str]
    doc_order: Dict[str, int]
    filtered_sentences: Annotated[Dict[str, List[str]], merge_dict]
    extracted_lsts: Annotated[List[LST_Node], operator.add]
    causal_nodes: List[LST_Node]
    temporal_groups: List
    aggregated_graph: Optional[AggregationResult]
    graph_context: str
    verified_intent: Optional[GlobalIntentEvaluation]
    requires_human_review: bool
    human_feedback: Optional[str]
    output_path: str
