"""문장 필터: 5대 배제 규칙 및 길이 기준 전처리."""
import re
import logging
from typing import List

logger = logging.getLogger(__name__)


def filter_sentences(text: str) -> List[str]:
    """
    5대 배제 규칙 및 10자 미만 제외 규칙 기반 전처리.
    - 특수문자 제외 10자 미만 문장 제외
    - 단순 예/아니오 등 짧은 확인 문장 제외
    - 메타데이터·인적사항·감정만 있는 문장 제외
    """
    valid = []
    for s in text.split("\n"):
        s = s.strip()
        if not s:
            continue

        pure_text = re.sub(r"[^a-zA-Z0-9가-힣]", "", s)
        if len(pure_text) < 10:
            continue

        if len(s.split()) <= 15 and any(
            kw in s for kw in ["예", "아니오", "맞습니다", "모르겠습니다"]
        ):
            continue
        if any(kw in s for kw in ["본 진술서는", "조사실에서", "년생입니다", "거주하고"]):
            continue
        if any(kw in s for kw in ["너무 억울", "정말 나쁜"]):
            continue
        valid.append(s)
    return valid
