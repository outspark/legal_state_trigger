"""프롬프트 로드: 프로젝트 루트 prompts/ 디렉토리의 마크다운 파일을 읽어 문자열로 제공."""
import os

# src/ 기준 상위(프로젝트 루트)의 prompts/
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROMPTS_DIR = os.path.join(_ROOT, "prompts")

_PROMPT_FILES = {
    "lst_extraction": "lst_extraction.md",
    "lst_aggregation": "lst_aggregation.md",
    "lst_argumentation": "lst_argumentation.md",
    "lst_intent_scoring": "lst_intent_scoring.md",
    "llm_judge": "llm_judge.md",
}


def load_prompts() -> dict:
    """prompts/ 내 md 파일을 읽어 딕셔너리로 반환."""
    out = {}
    for key, filename in _PROMPT_FILES.items():
        path = os.path.join(_PROMPTS_DIR, filename)
        with open(path, "r", encoding="utf-8") as f:
            out[key] = f.read()
    return out


PROMPTS = load_prompts()
LST_EXTRACTION_PROMPT = PROMPTS["lst_extraction"]
LST_AGGREGATION_PROMPT = PROMPTS["lst_aggregation"]
LST_ARGUMENTATION_PROMPT = PROMPTS["lst_argumentation"]
LST_INTENT_SCORING_PROMPT = PROMPTS["lst_intent_scoring"]
LLM_JUDGE_PROMPT = PROMPTS["llm_judge"]
