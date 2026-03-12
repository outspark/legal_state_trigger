"""Z3 기반 논증 관계 검증: Attack / Support / Duplicate 판별 및 Evidence 우선 규칙."""
import logging
from typing import List, Dict
from z3 import Bool, Solver, Not, unsat

from .schemas import AtomicProposition, ConflictGroup, DuplicateGroup, LST_Node, Z3PairResult

logger = logging.getLogger(__name__)


def check_z3_pair(
    props_a: List[AtomicProposition],
    props_b: List[AtomicProposition],
) -> str:
    """
    두 노드의 명제 집합을 Z3로 검증하여 관계 유형을 반환합니다.

    반환값:
        "Attack"  — 논리적 모순 (UNSAT)
        "Support" — 공유 명제가 일치하고 모순 없음 (SAT + 동방향 명제)
        "None"    — 관계 없음
    """
    solver = Solver()

    for p in props_a:
        var = Bool(p.name)
        solver.add(var if p.value else Not(var))

    for p in props_b:
        var = Bool(p.name)
        solver.add(var if p.value else Not(var))

    result = solver.check()

    if result == unsat:
        return "Attack"

    a_dict = {p.name: p.value for p in props_a}
    b_dict = {p.name: p.value for p in props_b}
    shared = set(a_dict.keys()) & set(b_dict.keys())
    if shared and any(a_dict[n] == b_dict[n] for n in shared):
        return "Support"

    return "None"


def build_smt_lib2(
    props_a: List[AtomicProposition],
    props_b: List[AtomicProposition],
    id_a: str,
    id_b: str,
    src_a: str = "",
    src_b: str = "",
) -> str:
    """두 노드의 명제를 SMT-LIB2 형식 스크립트로 변환."""
    lines = [
        f"; === Z3 Consistency Check ===",
        f"; Node A: {id_a} ({src_a})",
        f"; Node B: {id_b} ({src_b})",
        "(set-logic QF_UF)",
    ]
    all_names = sorted({p.name for p in props_a} | {p.name for p in props_b})
    for name in all_names:
        lines.append(f"(declare-const {name} Bool)")

    lines.append(f"\n; --- Node A: {id_a} ---")
    for p in props_a:
        expr = p.name if p.value else f"(not {p.name})"
        lines.append(f"(assert {expr})  ; {p.name} = {str(p.value).lower()}")

    lines.append(f"\n; --- Node B: {id_b} ---")
    for p in props_b:
        expr = p.name if p.value else f"(not {p.name})"
        lines.append(f"(assert {expr})  ; {p.name} = {str(p.value).lower()}")

    lines.append("\n(check-sat)")
    lines.append("; unsat → Attack (모순), sat → Support or None")
    lines.append("(exit)")
    return "\n".join(lines)


def get_source_category(filename: str) -> str:
    """파일명 기반 소스 유형 분류 (Evidence 우선 규칙용)."""
    if any(kw in filename for kw in ["수사보고", "증거", "내역", "확인서"]):
        return "Evidence"
    if any(kw in filename for kw in ["조서", "진술서", "피의자"]):
        return "Interrogation"
    if "고소장" in filename:
        return "Complaint"
    return "Other"


def evaluate_conflict_group(
    group: ConflictGroup,
    prop_map: Dict[str, List[AtomicProposition]],
    source_map: Dict[str, str],
    node_map: Dict[str, LST_Node],
) -> List[Z3PairResult]:
    """
    충돌 그룹 내 모든 노드 쌍에 대해 Z3 검증 후 argumentative_edges, v_status 갱신.
    Evidence가 Interrogation을 Attack하면 해당 Interrogation 노드는 Refuted.
    각 쌍의 Z3PairResult(SMT-LIB2 포함)를 반환.
    """
    ids = group.node_ids
    logger.info("Z3 그룹 분석: %s - '%s'", ids, group.event_summary)
    results: List[Z3PairResult] = []

    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            id_a, id_b = ids[i], ids[j]
            props_a = prop_map.get(id_a, [])
            props_b = prop_map.get(id_b, [])

            if not props_a or not props_b:
                logger.debug("  %s vs %s: 명제 없음, 건너뜀", id_a, id_b)
                continue

            relation = check_z3_pair(props_a, props_b)
            src_a = source_map.get(id_a, "?")
            src_b = source_map.get(id_b, "?")
            smt_script = build_smt_lib2(props_a, props_b, id_a, id_b, src_a, src_b)
            results.append(Z3PairResult(
                node_a=id_a, node_b=id_b, relation=relation, smt_lib2=smt_script,
            ))

            logger.info("  %s (%s) vs %s (%s): %s", id_a, src_a, id_b, src_b, relation)

            if relation == "None":
                continue

            node_a = node_map.get(id_a)
            node_b = node_map.get(id_b)
            if not node_a or not node_b:
                continue

            if relation == "Attack":
                node_a.argumentative_edges[id_b] = "Attack"
                node_b.argumentative_edges[id_a] = "Attack"
                node_a.v_status = "Contested"
                node_b.v_status = "Contested"

                cat_a = get_source_category(node_a.doc_source)
                cat_b = get_source_category(node_b.doc_source)
                if cat_a == "Evidence" and cat_b == "Interrogation":
                    node_b.v_status = "Refuted"
                    node_a.v_status = "Accepted"
                elif cat_b == "Evidence" and cat_a == "Interrogation":
                    node_a.v_status = "Refuted"
                    node_b.v_status = "Accepted"

            elif relation == "Support":
                node_a.argumentative_edges[id_b] = "Support"
                node_b.argumentative_edges[id_a] = "Support"

    return results


def mark_duplicate_group(
    group: DuplicateGroup,
    node_map: Dict[str, LST_Node],
) -> None:
    """S1이 의미론적으로 동일한 노드 그룹에 Duplicate 엣지 부여. Attack은 보존."""
    ids = [nid for nid in group.node_ids if nid in node_map]
    if len(ids) < 2:
        return
    logger.info("Duplicate 그룹: %s - '%s'", ids, group.shared_S1_summary)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            node_a = node_map[ids[i]]
            node_b = node_map[ids[j]]
            if node_a.argumentative_edges.get(ids[j]) != "Attack":
                node_a.argumentative_edges[ids[j]] = "Duplicate"
            if node_b.argumentative_edges.get(ids[i]) != "Attack":
                node_b.argumentative_edges[ids[i]] = "Duplicate"
