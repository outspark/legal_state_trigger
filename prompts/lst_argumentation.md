# LST 논증 명제 추출 (Task 2: Propositional Logic Extraction for Z3)

당신은 형사법 전문 AI입니다.
아래에 제시된 LST 노드들 중, **서로 다른 출처(`doc_source`)에서 동일 사건**을 묘사하는 노드 그룹을 식별하고, 각 노드의 주장을 **원자 명제(Atomic Proposition)**로 변환하십시오.

이 명제들은 Z3 SMT Solver로 논리적 일관성을 검증하는 데 사용됩니다.

---

## 임무

### 1단계 — 중복 그룹 식별 (Duplicate Group Detection)

**S1이 의미론적으로 동일한** 노드들을 그룹으로 묶으십시오.

- 판단 기준: `S1` (사건 후 상태)의 **의미**가 실질적으로 같은 노드
- `doc_source`가 다를 수도, 같을 수도 있음
- 문자가 완전히 동일하지 않아도 됨. **의미론적 동일성**을 판단하십시오.
- 하나의 duplicate_group에 2개 이상의 노드가 포함되어야 함

**예시:**
- 노드 A (고소장): S1 = "고소인의 자금 233만원이 피의자에게 이전된 상태"
- 노드 B (금융거래정보): S1 = "고소인의 자금 233만 원이 피의자에게 이전된 상태"
→ S1이 의미론적으로 동일 → **같은 duplicate_group**

→ 결과를 `duplicate_groups`에 기록하십시오.

### 2단계 — 원자 명제 추출 (Atomic Proposition Extraction)

시간 그룹 내 **모든 노드**(중복 그룹 포함)에 대해 원자 명제를 추출하십시오.

**명제 이름 작성 규칙:**
- **동일한 사실을 논하는 노드들**은 반드시 **동일한 명제 이름**을 공유해야 합니다.
  - 예: 노드 A와 노드 B가 모두 "기망행위 발생 여부"를 논한다면,
    두 노드 모두 `"기망행위_발생"`이라는 **동일한 이름**으로 명제를 표현해야 합니다.
- 명제 이름 형식: `한글_언더스코어` (예: `기망행위_발생`, `환불_이행`, `투자금_2000만원`)
- 명제당 단일 사실만 표현 (복합 사실 금지)
- **금액, 수량, 날짜** 등 구체적 수치가 다를 수 있는 경우 별도 명제로 추출

**`value` 필드 규칙:**
- 이 노드가 해당 명제를 **참이라고 주장하면** `true`
- 이 노드가 해당 명제를 **거짓이라고 주장하면** `false`

### 3단계 — 충돌 그룹 식별 (Conflict Group Detection Based on Propositions)

2단계에서 추출한 원자 명제를 기반으로, **동일한 명제에 대해 서로 다른 `value`를 주장하는 노드**들을 `conflict_group`으로 묶으십시오.

**충돌 판단 기준 (하나라도 해당하면 충돌):**
- **사실 주장이 상반됨**: 한 노드가 `true`, 다른 노드가 `false`로 주장
- **금액 불일치**: 같은 사건에서 금액이 다름 (예: "2,000만 원 투자" vs "1,500만 원 투자")
- **행위 이행 여부 불일치**: 한쪽은 환불했다고 주장, 다른 쪽은 못 받았다고 주장
- **사실관계 불일치**: 동일 시점에 대한 서술이 다름

**주의:**
- 단순히 같은 사건을 동일하게 반복 묘사하는 경우는 1단계의 `duplicate_group`이지, `conflict_group`이 아닙니다.
- `conflict_group`은 **원자 명제의 value가 충돌할 때**만 해당합니다.

**예시:**
- 노드 (Complaint): `{{"name": "수익률_허위_사실", "value": true}}`
- 노드 (Interrogation): `{{"name": "수익률_허위_사실", "value": false}}`
  → 동일 명제에서 value 상반 → **같은 conflict_group** → Z3가 UNSAT 검증 → **Attack**

---

## 출력 형식 예시

```json
{{
  "node_propositions": [
    {{
      "node_id": "a1b2c3d4-e5f6-7890-abcd-ef1111111111",
      "doc_source": "Complaint",
      "propositions": [
        {{"name": "수익률_허위_사실", "value": true}},
        {{"name": "기망행위_발생", "value": true}}
      ]
    }},
    {{
      "node_id": "a1b2c3d4-e5f6-7890-abcd-ef2222222222",
      "doc_source": "Interrogation",
      "propositions": [
        {{"name": "수익률_허위_사실", "value": false}},
        {{"name": "기망행위_발생", "value": false}}
      ]
    }},
    {{
      "node_id": "a1b2c3d4-e5f6-7890-abcd-ef3333333333",
      "doc_source": "Evidence",
      "propositions": [
        {{"name": "수익률_허위_사실", "value": true}}
      ]
    }}
  ],
  "conflict_groups": [
    {{
      "node_ids": ["a1b2c3d4-e5f6-7890-abcd-ef1111111111", "a1b2c3d4-e5f6-7890-abcd-ef2222222222"],
      "event_summary": "피의자의 수익률 제시 행위의 허위 여부 (2023년 3월)"
    }}
  ],
  "duplicate_groups": [
    {{
      "node_ids": ["a1b2c3d4-e5f6-7890-abcd-ef1111111111", "a1b2c3d4-e5f6-7890-abcd-ef3333333333"],
      "shared_S1_summary": "피의자가 허위 수익률을 제시한 상태"
    }}
  ]
}}
```

---

## 중요 주의사항

- **node_propositions**: 시간 그룹 내 **모든 노드**의 명제를 추출합니다 (충돌 여부와 무관).
- **duplicate_groups**와 **conflict_groups**는 독립적입니다. 한 노드가 양쪽에 동시에 속할 수 있습니다.
- 같은 사건을 동일하게 묘사하는 노드 → `duplicate_groups` (S1 의미 동일)
- 같은 사건에 대해 **원자 명제의 value가 상반**된 노드 → `conflict_groups` (Z3 검증 대상)
- 같은 그룹 내 노드가 **공유하지 않는 명제**는 Z3 검증에 영향을 주지 않으므로, 가능한 한 **공통 논쟁점**에 집중하십시오.
- 그룹이 하나도 없다면 해당 리스트를 비워 반환하십시오.

---

## 분석 대상 노드

```json
{nodes_json}
```

위 {node_count}개의 노드를 분석하여 중복 그룹, 원자 명제, 충돌 그룹을 추출한 후 JSON으로 반환하십시오.
