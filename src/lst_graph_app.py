"""
LST 분석 LangGraph 앱: 그래프 빌드 및 실행 엔트리포인트.
실행: 프로젝트 루트(lst_project)에서 python -m src.lst_graph_app
"""
import os
import glob
import logging
from langgraph.graph import StateGraph, START, END

from .schemas import InvestigationState
from .nodes import (
    parallel_process_complaint,
    parallel_process_interrogation,
    parallel_process_evidence,
    extract_lsts,
    llm_causal_analysis,
    z3_argumentation,
    score_node_intent,
    llm_judge,
    save_json_output,
    human_review_node,
    conditional_human_review,
)
from .config import config

# ── 로깅 설정 ──
_LOG_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DATE = "%Y-%m-%d %H:%M:%S"
logging.basicConfig(level=logging.INFO, format=_LOG_FMT, datefmt=_LOG_DATE)
logger = logging.getLogger(__name__)


# ── 그래프 빌드 ──
workflow = StateGraph(InvestigationState)

workflow.add_node("process_complaint", parallel_process_complaint)
workflow.add_node("process_interrogation", parallel_process_interrogation)
workflow.add_node("process_evidence", parallel_process_evidence)
workflow.add_node("extraction", extract_lsts)
workflow.add_node("causal_analysis", llm_causal_analysis)
workflow.add_node("z3_argumentation", z3_argumentation)
workflow.add_node("intent_scoring", score_node_intent)
workflow.add_node("judge", llm_judge)
workflow.add_node("human_review_node", human_review_node)
workflow.add_node("save_output", save_json_output)

workflow.add_edge(START, "process_complaint")
workflow.add_edge(START, "process_interrogation")
workflow.add_edge(START, "process_evidence")
workflow.add_edge("process_complaint", "extraction")
workflow.add_edge("process_interrogation", "extraction")
workflow.add_edge("process_evidence", "extraction")

workflow.add_edge("extraction", "causal_analysis")
workflow.add_edge("causal_analysis", "z3_argumentation")
workflow.add_edge("z3_argumentation", "intent_scoring")
workflow.add_edge("intent_scoring", "judge")

workflow.add_conditional_edges(
    "judge",
    conditional_human_review,
    {"human_review_node": "human_review_node", "save_output": "save_output"},
)
workflow.add_edge("human_review_node", "save_output")
workflow.add_edge("save_output", END)

app = workflow.compile()


# ── 실행 엔트리포인트 ──
if __name__ == "__main__":
    data_dir = config["paths"]["data_dir"]
    output_dir = config["paths"]["output_dir"]
    doc_inputs: dict = {}

    for p in glob.glob(os.path.join(data_dir, "*.txt")):
        fname = os.path.basename(p)
        with open(p, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if content:
                doc_inputs[fname] = content

    doc_order = {fname: i for i, fname in enumerate(sorted(doc_inputs.keys()))}
    logger.info("총 %d개의 문서 처리 준비 완료 (문서 순서: %s)", len(doc_inputs), list(doc_order.keys()))

    os.makedirs(output_dir, exist_ok=True)
    log_path = os.path.join(output_dir, "lst_run.log")
    file_handler = logging.FileHandler(log_path, encoding="utf-8", mode="w")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(_LOG_FMT, datefmt=_LOG_DATE))
    logging.getLogger().addHandler(file_handler)
    logger.info("로그 파일: %s", log_path)

    output_path = os.path.join(output_dir, "lst_analysis_result.json")
    initial_state: InvestigationState = {
        "documents": doc_inputs,
        "doc_order": doc_order,
        "filtered_sentences": {},
        "extracted_lsts": [],
        "causal_nodes": [],
        "temporal_groups": [],
        "aggregated_graph": None,
        "graph_context": "",
        "verified_intent": None,
        "requires_human_review": False,
        "human_feedback": None,
        "output_path": output_path,
    }

    logger.info("=" * 60)
    logger.info("  LST 분석 파이프라인 시작")
    logger.info("  병렬 라우팅 → 추출 → [인과 연쇄(LLM)] → [논증(Z3)] → [노드별 고의성] → 판결")
    logger.info("=" * 60)

    config_thread = {"configurable": {"thread_id": "LST_analysis_run"}}

    try:
        results = app.invoke(initial_state, config=config_thread)

        logger.info("=" * 60)
        logger.info("  [[ 최종 결과 ]]")
        logger.info("=" * 60)
        logger.info("1. 추출된 LST 수:  %d", len(results.get("extracted_lsts") or []))
        logger.info("2. 인과 분석 노드: %d", len(results.get("causal_nodes") or []))

        agg = results.get("aggregated_graph")
        if agg:
            logger.info("3. 조립 노드 수:   %d", len(agg.assembled_nodes))
            logger.info("4. 논증망 요약:    %s", agg.graph_summary)

        intent = results.get("verified_intent")
        if intent:
            logger.info("5. 고의성 점수:    %s", intent.intent_score)
            logger.info("6. 고의성 유형:    %s", intent.intent_type)
            logger.info("7. 논거:           %s", intent.justification)

        if results.get("requires_human_review"):
            logger.info("!!! HUMAN REVIEW REQUIRED: 고의성 모호 구간 (0.45~0.55)")

        logger.info("결과 저장: %s", output_path)

    except Exception as e:
        logger.exception("파이프라인 오류: %s", e)
        raise
