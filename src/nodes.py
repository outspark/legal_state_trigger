"""워크플로우 노드: 병렬 필터, 추출, 인과분석, Z3 논증, 판결, 저장, human review."""
import json
import os
import re
import uuid
import datetime
import logging
from typing import Dict, List
from collections import defaultdict

from langchain_core.messages import HumanMessage
from langgraph.types import interrupt
from langsmith import traceable

from .schemas import (
    InvestigationState,
    LST_Node,
    ExtractionResult,
    CausalChainResult,
    TemporalGroup,
    PropositionExtractionResult,
    TemporalGroupLog,
    IntentScoringResult,
    AggregationResult,
    GlobalIntentEvaluation,
)
from .filter_sentences import filter_sentences
from .z3_eval import evaluate_conflict_group, mark_duplicate_group
from .prompts import (
    LST_EXTRACTION_PROMPT,
    LST_AGGREGATION_PROMPT,
    LST_ARGUMENTATION_PROMPT,
    LST_INTENT_SCORING_PROMPT,
    LLM_JUDGE_PROMPT,
)
from .config import config, llm

logger = logging.getLogger(__name__)


# ── 시간별 그룹화 유틸 ──
def _normalize_time_key(t: str) -> str:
    """한국어 시점 문자열을 그룹화 키로 정규화."""
    if not t:
        return "불명"
    y = re.search(r"(\d{4})년", t)
    m = re.search(r"(\d{1,2})월", t)
    d = re.search(r"(\d{1,2})일", t)
    year = y.group(1) if y else "?"
    month = m.group(1).zfill(2) if m else "?"
    if d:
        return f"{year}년 {month}월 {d.group(1).zfill(2)}일"
    if "초" in t:
        return f"{year}년 {month}월 초"
    if "중순" in t or "중" in t:
        return f"{year}년 {month}월 중순"
    if "말" in t:
        return f"{year}년 {month}월 말"
    return f"{year}년 {month}월"


def _group_by_time(
    nodes: List[LST_Node], doc_order: Dict[str, int] = None,
) -> List[TemporalGroup]:
    """시간별 노드 그룹화. 같은 시간대 내에서는 doc_order 순으로 정렬."""
    doc_order = doc_order or {}
    doc_of = {n.id: n.doc_source for n in nodes}
    groups: Dict[str, List[str]] = defaultdict(list)
    for n in nodes:
        groups[_normalize_time_key(n.t)].append(n.id)
    for ids in groups.values():
        ids.sort(key=lambda nid: doc_order.get(doc_of.get(nid, ""), 999))
    return [
        TemporalGroup(time_label=k, node_ids=v)
        for k, v in sorted(groups.items())
    ]


def _sort_tg_by_doc(
    tg_list: List[TemporalGroup],
    nodes: List[LST_Node],
    doc_order: Dict[str, int],
) -> None:
    """기존 temporal_groups 내 node_ids를 doc_order 순으로 재정렬 (in-place)."""
    doc_of = {n.id: n.doc_source for n in nodes}
    for tg in tg_list:
        tg.node_ids.sort(key=lambda nid: doc_order.get(doc_of.get(nid, ""), 999))


# ── 병렬 필터 노드 ──
def parallel_process_complaint(state: InvestigationState) -> dict:
    filtered_results = {}
    for filename, content in state["documents"].items():
        if "고소장" in filename:
            filtered_results[filename] = filter_sentences(content)
    return {"filtered_sentences": filtered_results}


def parallel_process_interrogation(state: InvestigationState) -> dict:
    filtered_results = {}
    for filename, content in state["documents"].items():
        if "조서" in filename or "진술서" in filename:
            filtered_results[filename] = filter_sentences(content)
    return {"filtered_sentences": filtered_results}


def parallel_process_evidence(state: InvestigationState) -> dict:
    filtered_results = {}
    for filename, content in state["documents"].items():
        if "수사보고" in filename:
            filtered_results[filename] = filter_sentences(content)
    return {"filtered_sentences": filtered_results}


# ── 추출 노드 ──
@traceable(name="Extract Document")
def _extract_single_doc(structured_llm, doc_source: str, sentences: List[str]) -> List[LST_Node]:
    prompt = LST_EXTRACTION_PROMPT.format(
        doc_source=doc_source,
        content="\n".join(sentences),
    )
    res: ExtractionResult = structured_llm.invoke([HumanMessage(content=prompt)])
    old_to_uuid: Dict[str, str] = {}
    for node in res.lsts:
        old_id = node.id
        node.id = str(uuid.uuid4())
        old_to_uuid[old_id] = node.id
        node.doc_source = doc_source
    for node in res.lsts:
        node.C_sources = [old_to_uuid.get(cid, cid) for cid in node.C_sources]
    return res.lsts


@traceable(name="Extract LSTs from Documents")
def extract_lsts(state: InvestigationState) -> dict:
    """필터링된 문장들에서 LST 노드 추출."""
    filtered_docs = state["filtered_sentences"]
    all_lsts = []
    structured_llm = llm.with_structured_output(ExtractionResult, method="function_calling")

    for doc_source, sentences in filtered_docs.items():
        if not sentences:
            continue
        try:
            lsts = _extract_single_doc(structured_llm, doc_source, sentences)
            all_lsts.extend(lsts)
            logger.info("[Extraction] %s: %d개 노드 추출", doc_source, len(lsts))
        except Exception as e:
            logger.warning("%s 추출 실패: %s", doc_source, e)

    return {"extracted_lsts": all_lsts}


# ── 인과 연쇄 노드 ──
def llm_causal_analysis(state: InvestigationState) -> dict:
    """Task 1 — LLM 기반 인과 연쇄 분석 + 시간별 그룹화."""
    lsts: List[LST_Node] = state.get("extracted_lsts") or []
    doc_order: Dict[str, int] = state.get("doc_order") or {}
    if not lsts:
        logger.info("[Causal] 추출된 노드 없음, 건너뜀")
        return {"causal_nodes": [], "temporal_groups": []}

    nodes_json = json.dumps(
        [node.model_dump() for node in lsts],
        ensure_ascii=False,
        indent=2,
    )
    prompt = LST_AGGREGATION_PROMPT.format(
        nodes_json=nodes_json,
        node_count=len(lsts),
    )
    structured_llm = llm.with_structured_output(CausalChainResult, method="function_calling")
    tg: List[TemporalGroup] = []
    try:
        result: CausalChainResult = structured_llm.invoke([HumanMessage(content=prompt)])
        causal_nodes = result.nodes_with_causality
        tg = result.temporal_groups or []
        logger.info("[Causal] 인과 연쇄 완성: %d개 노드", len(causal_nodes))
    except Exception as e:
        logger.warning("인과 분석 실패: %s — 원본 노드 유지", e)
        causal_nodes = lsts

    if not tg:
        tg = _group_by_time(causal_nodes, doc_order)
    else:
        _sort_tg_by_doc(tg, causal_nodes, doc_order)
    logger.info("[Causal] 시간별 그룹: %d개 (%s)", len(tg), ", ".join(f"{g.time_label}({len(g.node_ids)})" for g in tg))

    return {"causal_nodes": causal_nodes, "temporal_groups": tg}


# ── Z3 논증 노드 ──
@traceable(name="Z3 Argumentation Analysis")
def z3_argumentation(state: InvestigationState) -> dict:
    """Task 2 — 시간별 그룹 단위로 LLM 명제 추출 + Z3 논리 검증."""
    lsts: List[LST_Node] = state.get("causal_nodes") or []
    doc_order: Dict[str, int] = state.get("doc_order") or {}
    if not lsts:
        empty = AggregationResult(assembled_nodes=[], graph_summary="분석할 노드가 없습니다.")
        return {"aggregated_graph": empty, "graph_context": empty.graph_summary}

    temporal_groups: List[TemporalGroup] = state.get("temporal_groups") or []
    if not temporal_groups:
        temporal_groups = _group_by_time(lsts, doc_order)

    node_map: Dict[str, LST_Node] = {n.id: n.model_copy(deep=True) for n in lsts}
    structured_llm = llm.with_structured_output(
        PropositionExtractionResult, method="function_calling"
    )
    proof_logs: List[TemporalGroupLog] = []

    for tg in temporal_groups:
        group_node_ids = [nid for nid in tg.node_ids if nid in node_map]
        group_nodes = [node_map[nid] for nid in group_node_ids]
        if len(group_nodes) < 2:
            logger.info("[Z3] '%s': 노드 %d개 — 비교 대상 없어 건너뜀", tg.time_label, len(group_nodes))
            continue

        logger.info("[Z3] 시간 그룹 '%s': %d개 노드 분석 시작", tg.time_label, len(group_nodes))
        group_json = json.dumps(
            [n.model_dump() for n in group_nodes],
            ensure_ascii=False,
            indent=2,
        )
        prompt = LST_ARGUMENTATION_PROMPT.format(
            nodes_json=group_json,
            node_count=len(group_nodes),
        )
        try:
            prop_result: PropositionExtractionResult = structured_llm.invoke(
                [HumanMessage(content=prompt)]
            )
            logger.info(
                "[Z3] '%s': 중복 %d개, 충돌 %d개, 명제 %d개 노드",
                tg.time_label,
                len(prop_result.duplicate_groups),
                len(prop_result.conflict_groups),
                len(prop_result.node_propositions),
            )
        except Exception as e:
            logger.warning("[Z3] '%s' 분석 실패: %s — 건너뜀", tg.time_label, e)
            continue

        prop_map = {np.node_id: np.propositions for np in prop_result.node_propositions}
        source_map = {np.node_id: np.doc_source for np in prop_result.node_propositions}

        z3_evals = []
        for group in prop_result.conflict_groups:
            z3_evals.extend(evaluate_conflict_group(group, prop_map, source_map, node_map))

        for dup_group in prop_result.duplicate_groups:
            mark_duplicate_group(dup_group, node_map)

        proof_logs.append(TemporalGroupLog(
            time_label=tg.time_label,
            node_ids=group_node_ids,
            node_propositions=list(prop_result.node_propositions),
            duplicate_groups=list(prop_result.duplicate_groups),
            conflict_groups=list(prop_result.conflict_groups),
            z3_evaluations=z3_evals,
        ))

    assembled = list(node_map.values())
    summary_parts = []
    refuted = [n.id for n in assembled if n.v_status == "Refuted"]
    if refuted:
        summary_parts.append(f"Refuted 노드: {', '.join(refuted)}")
    attack_pairs = [
        (n.id, t) for n in assembled for t, r in n.argumentative_edges.items() if r == "Attack"
    ]
    if attack_pairs:
        summary_parts.append(f"Attack 관계: {len(attack_pairs)}쌍")
    dup_edges = sum(1 for n in assembled for r in n.argumentative_edges.values() if r == "Duplicate")
    if dup_edges:
        summary_parts.append(f"Duplicate 관계: {dup_edges // 2}쌍")
    graph_summary = " | ".join(summary_parts) if summary_parts else "논증 충돌 없음"
    result = AggregationResult(
        assembled_nodes=assembled, graph_summary=graph_summary, z3_proof_log=proof_logs,
    )

    context_lines = [f"### 조립된 LST 논증망\n요약: {graph_summary}\n"]
    for node in assembled:
        context_lines.append(f"- [{node.id} | {node.doc_source} | Status: {node.v_status}]")
        context_lines.append(f"  t: {node.t}")
        context_lines.append(f"  E: {node.E}")
        context_lines.append(f"  L: {node.L}")
        context_lines.append(f"  S0: {node.S0} → S1: {node.S1}")
        context_lines.append(f"  Causal: {node.C_sources}")
        context_lines.append(f"  Edges: {node.argumentative_edges}\n")

    return {
        "aggregated_graph": result,
        "graph_context": "\n".join(context_lines),
    }


# ── 노드별 고의성 평가 ──
@traceable(name="Node Intent Confidence Scoring")
def score_node_intent(state: InvestigationState) -> dict:
    """시간 그룹 단위로 각 노드의 S1 고의성 신뢰도를 LLM-as-a-Judge로 평가."""
    aggregated = state.get("aggregated_graph")
    if not aggregated or not aggregated.assembled_nodes:
        return {}

    doc_order: Dict[str, int] = state.get("doc_order") or {}
    temporal_groups: List[TemporalGroup] = state.get("temporal_groups") or []
    node_map: Dict[str, LST_Node] = {n.id: n for n in aggregated.assembled_nodes}

    if not temporal_groups:
        temporal_groups = _group_by_time(list(node_map.values()), doc_order)

    structured_llm = llm.with_structured_output(
        IntentScoringResult, method="function_calling"
    )

    for tg in temporal_groups:
        group_nodes = [node_map[nid] for nid in tg.node_ids if nid in node_map]
        if not group_nodes:
            continue

        logger.info("[Intent] 시간 그룹 '%s': %d개 노드 평가", tg.time_label, len(group_nodes))
        group_json = json.dumps(
            [n.model_dump() for n in group_nodes],
            ensure_ascii=False,
            indent=2,
        )
        prompt = LST_INTENT_SCORING_PROMPT.format(
            time_label=tg.time_label,
            nodes_json=group_json,
            node_count=len(group_nodes),
        )
        try:
            result: IntentScoringResult = structured_llm.invoke(
                [HumanMessage(content=prompt)]
            )
            for score in result.scores:
                node = node_map.get(score.node_id)
                if node:
                    node.intent_confidence = max(0.0, min(1.0, score.intent_confidence))
                    node.intent_reasoning = score.reasoning
            logger.info(
                "[Intent] '%s': %d개 노드 평가 완료",
                tg.time_label,
                len(result.scores),
            )
        except Exception as e:
            logger.warning("[Intent] '%s' 평가 실패: %s", tg.time_label, e)

    scored_nodes = list(node_map.values())
    updated_agg = AggregationResult(
        assembled_nodes=scored_nodes,
        graph_summary=aggregated.graph_summary,
        z3_proof_log=aggregated.z3_proof_log,
    )

    context_lines = [f"### 조립된 LST 논증망\n요약: {aggregated.graph_summary}\n"]
    for node in scored_nodes:
        context_lines.append(f"- [{node.id} | {node.doc_source} | Status: {node.v_status} | Intent: {node.intent_confidence:.2f}]")
        context_lines.append(f"  t: {node.t}")
        context_lines.append(f"  E: {node.E}")
        context_lines.append(f"  L: {node.L}")
        context_lines.append(f"  S0: {node.S0} → S1: {node.S1}")
        context_lines.append(f"  Intent근거: {node.intent_reasoning}\n")

    return {
        "aggregated_graph": updated_agg,
        "graph_context": "\n".join(context_lines),
    }


# ── 판결 노드 ──
@traceable(name="LLM Intent Judgment")
def llm_judge(state: InvestigationState) -> dict:
    """최종 고의성 판결 에이전트."""
    prompt = LLM_JUDGE_PROMPT.format(graph_context=state["graph_context"])
    structured_llm = llm.with_structured_output(
        GlobalIntentEvaluation, method="function_calling"
    )
    review_needed = False
    try:
        evaluation: GlobalIntentEvaluation = structured_llm.invoke(
            [HumanMessage(content=prompt)]
        )
        score = evaluation.intent_score
        lower = config["thresholds"]["human_review_lower"]
        upper = config["thresholds"]["human_review_upper"]
        if lower <= score <= upper:
            review_needed = True
        logger.info(
            "[Judge] 점수: %s (%s), 인간검토: %s",
            score,
            evaluation.intent_type,
            review_needed,
        )
        return {"verified_intent": evaluation, "requires_human_review": review_needed}
    except Exception as e:
        logger.warning("Judge 실패: %s", e)
        return {"requires_human_review": True}


# ── 저장 노드 ──
def save_json_output(state: InvestigationState) -> dict:
    """최종 결과를 JSON 파일로 저장. Z3 증명 로그는 별도 파일."""
    output_path = state.get("output_path") or "output_result.json"
    out_dir = os.path.dirname(os.path.abspath(str(output_path)))
    os.makedirs(out_dir, exist_ok=True)

    aggregated = state.get("aggregated_graph")
    intent = state.get("verified_intent")
    timestamp = datetime.datetime.now().isoformat()

    agg_dump = aggregated.model_dump() if aggregated else None
    proof_log = None
    if agg_dump:
        proof_log = agg_dump.pop("z3_proof_log", None)

    payload = {
        "run_timestamp": timestamp,
        "extracted_lsts_count": len(state.get("extracted_lsts") or []),
        "aggregated_graph": agg_dump,
        "verified_intent": intent.model_dump() if intent else None,
        "requires_human_review": state.get("requires_human_review", False),
        "human_feedback": state.get("human_feedback"),
    }
    with open(str(output_path), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info("[Output] 분석 결과 저장 → %s", output_path)

    if proof_log:
        proof_path = os.path.join(out_dir, "z3_proof_log.json")
        proof_payload = {
            "run_timestamp": timestamp,
            "description": "Z3 SMT-LIB2 증명 로그. 각 smt_lib2 값을 .smt2 파일로 저장 후 z3/cvc5로 실행 가능.",
            "temporal_group_logs": proof_log,
        }
        with open(proof_path, "w", encoding="utf-8") as f:
            json.dump(proof_payload, f, ensure_ascii=False, indent=2)
        logger.info("[Output] Z3 증명 로그 저장 → %s", proof_path)

    return {}


# ── Human review ──
def conditional_human_review(state: InvestigationState) -> str:
    if state.get("requires_human_review", False):
        return "human_review_node"
    return "save_output"


def human_review_node(state: InvestigationState) -> dict:
    """Human-in-the-loop: interrupt()로 런타임 중지 후 사용자 판단 요청."""
    intent = state.get("verified_intent")
    justification = intent.justification if intent else "판결 없음"
    human_verdict = interrupt(
        f"고의성 점수 모호 구간 (Type 2). 최종 판단을 입력하세요.\n논거: {justification}"
    )
    return {"human_feedback": human_verdict}
