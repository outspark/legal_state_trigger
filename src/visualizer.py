"""
LST 결과 JSON → vis.js Network 기반 self-contained HTML 시각화.

사용법 (프로젝트 루트에서):
    python -m src.visualizer [결과JSON경로] [출력HTML경로]
"""

import json
import os
import sys
import yaml
from pathlib import Path

# 프로젝트 루트 기준 config (src/ 에서 실행 시)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_PATH = os.path.join(_ROOT, "configs", "app_config.yaml")
with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

data_dir = config["paths"]["data_dir"]

INPUT_JSON  = sys.argv[1] if len(sys.argv) > 1 else os.path.join(data_dir, "lst_analysis_result.json")
OUTPUT_HTML = sys.argv[2] if len(sys.argv) > 2 else os.path.join(data_dir, "lst_visualization.html")


# ── 노드/엣지 색상 맵 ──────────────────────────────────────
SOURCE_COLOR = {
    "Complaint":    {"background": "#4A90D9", "border": "#2C6BAC"},
    "Interrogation":{"background": "#E8923A", "border": "#B5621A"},
    "Evidence":     {"background": "#5AB56E", "border": "#357A45"},
}
STATUS_BORDER = {
    "Accepted":  None,           # 기본(doc_source 색상 유지)
    "Contested": "#F4D03F",      # 노란 테두리
    "Refuted":   "#E74C3C",      # 빨간 테두리
}
EDGE_STYLE = {
    "causal":   {"color": "#95A5A6", "dashes": False, "label": "causes"},
    "Attack":   {"color": "#E74C3C", "dashes": True,  "label": "Attack"},
    "Support":  {"color": "#27AE60", "dashes": False, "label": "Support"},
}


def build_vis_data(assembled_nodes: list) -> tuple[list, list]:
    """vis.js nodes / edges 데이터를 구성합니다."""
    vis_nodes = []
    vis_edges = []
    edge_seen: set = set()

    for node in assembled_nodes:
        nid      = node["id"]
        src      = node.get("doc_source", "Unknown")
        status   = node.get("v_status", "Accepted")
        colors   = SOURCE_COLOR.get(src, {"background": "#BDC3C7", "border": "#7F8C8D"})
        
        # v_status가 Contested / Refuted이면 테두리 색상 덮어쓰기
        border_override = STATUS_BORDER.get(status)
        border_color = border_override if border_override else colors["border"]

        # 툴팁 HTML
        tooltip = (
            f"<b>[{nid}]</b> {src} &nbsp;|&nbsp; <i>{status}</i><br>"
            f"<b>t:</b> {node.get('t','')}<br>"
            f"<b>E:</b> {node.get('E','')}<br>"
            f"<b>L:</b> {node.get('L','')}<br>"
            f"<b>S0:</b> {node.get('S0','')}<br>"
            f"<b>S1:</b> {node.get('S1','')}"
        )

        vis_nodes.append({
            "id":    nid,
            "label": f"{nid}\n[{src}]",
            "title": tooltip,
            "color": {
                "background": colors["background"],
                "border":     border_color,
                "highlight":  {"background": colors["background"], "border": "#F39C12"},
            },
            "font":        {"color": "#FFFFFF", "size": 13, "face": "Inter, sans-serif"},
            "borderWidth": 3 if border_override else 1,
            "shape":       "box",
            "margin":      8,
            "group":       src,
        })

        # 인과 엣지 (C_sources)
        for src_id in node.get("C_sources", []):
            key = f"causal_{src_id}_{nid}"
            if key not in edge_seen:
                edge_seen.add(key)
                s = EDGE_STYLE["causal"]
                vis_edges.append({
                    "from":   src_id,
                    "to":     nid,
                    "label":  s["label"],
                    "dashes": s["dashes"],
                    "color":  {"color": s["color"], "highlight": s["color"]},
                    "arrows": "to",
                    "font":   {"size": 10, "color": "#7F8C8D"},
                    "width":  1.5,
                })

        # 논증 엣지 (argumentative_edges)
        for target_id, rel_type in node.get("argumentative_edges", {}).items():
            # 양방향 Attack은 한 번만 추가
            key = f"arg_{'_'.join(sorted([nid, target_id]))}_{rel_type}"
            if key not in edge_seen:
                edge_seen.add(key)
                s = EDGE_STYLE.get(rel_type, EDGE_STYLE["Attack"])
                vis_edges.append({
                    "from":   nid,
                    "to":     target_id,
                    "label":  s["label"],
                    "dashes": s["dashes"],
                    "color":  {"color": s["color"], "highlight": s["color"]},
                    "arrows": "to;from" if rel_type == "Attack" else "to",
                    "font":   {"size": 10, "color": s["color"]},
                    "width":  2,
                })

    return vis_nodes, vis_edges


def render_html(data: dict) -> str:
    """vis.js 데이터를 임베드한 self-contained HTML 문자열을 반환합니다."""
    assembled   = data.get("aggregated_graph", {}).get("assembled_nodes", [])
    graph_sum   = data.get("aggregated_graph", {}).get("graph_summary", "")
    intent      = data.get("verified_intent") or {}
    ts          = data.get("run_timestamp", "")
    lst_count   = data.get("extracted_lsts_count", 0)

    vis_nodes, vis_edges = build_vis_data(assembled)

    nodes_json = json.dumps(vis_nodes, ensure_ascii=False)
    edges_json = json.dumps(vis_edges, ensure_ascii=False)

    intent_score = intent.get("intent_score", "N/A")
    intent_type  = intent.get("intent_type", "N/A")
    justification = intent.get("justification", "N/A")

    # intent_score 숫자이면 % 막대 계산
    try:
        score_pct = f"{float(intent_score)*100:.1f}%"
        score_val = float(intent_score)
    except (ValueError, TypeError):
        score_pct = "N/A"
        score_val = None

    bar_color = "#27AE60"
    if score_val is not None:
        if score_val >= 0.75:   bar_color = "#E74C3C"
        elif score_val >= 0.45: bar_color = "#F4D03F"

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>LST 논증망 시각화</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap" rel="stylesheet"/>
  <script src="https://unpkg.com/vis-network@9.1.9/dist/vis-network.min.js"></script>
  <link  href="https://unpkg.com/vis-network@9.1.9/dist/vis-network.min.css" rel="stylesheet"/>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Inter', sans-serif;
      background: #0D1117;
      color: #C9D1D9;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }}

    /* ── Header ── */
    header {{
      background: linear-gradient(135deg, #161B22 0%, #1C2333 100%);
      border-bottom: 1px solid #30363D;
      padding: 18px 28px;
      display: flex;
      align-items: center;
      gap: 14px;
    }}
    header h1 {{
      font-size: 1.25rem;
      font-weight: 700;
      letter-spacing: -0.02em;
      background: linear-gradient(90deg, #58A6FF, #BC8CFF);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }}
    header .meta {{
      margin-left: auto;
      font-size: 0.75rem;
      color: #6E7681;
    }}

    /* ── Layout ── */
    .layout {{
      display: grid;
      grid-template-columns: 320px 1fr;
      flex: 1;
      overflow: hidden;
      height: calc(100vh - 65px);
    }}

    /* ── Sidebar ── */
    aside {{
      background: #161B22;
      border-right: 1px solid #30363D;
      display: flex;
      flex-direction: column;
      overflow-y: auto;
      padding: 20px;
      gap: 18px;
    }}
    .card {{
      background: #1C2333;
      border: 1px solid #30363D;
      border-radius: 10px;
      padding: 16px;
    }}
    .card h2 {{
      font-size: 0.7rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: .08em;
      color: #8B949E;
      margin-bottom: 12px;
    }}
    .stat-row {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 5px 0;
      font-size: 0.85rem;
      border-bottom: 1px solid #21262D;
    }}
    .stat-row:last-child {{ border-bottom: none; }}
    .stat-val {{ font-weight: 600; color: #E6EDF3; }}

    /* Intent bar */
    .score-bar-wrap {{
      background: #21262D;
      border-radius: 6px;
      height: 10px;
      margin: 10px 0 6px;
      overflow: hidden;
    }}
    .score-bar {{
      height: 100%;
      border-radius: 6px;
      transition: width .6s ease;
      background: {bar_color};
      width: {score_pct if score_val is not None else "0%"};
    }}
    .score-label {{
      font-size: 0.75rem;
      color: #8B949E;
    }}

    .justification {{
      font-size: 0.78rem;
      line-height: 1.6;
      color: #8B949E;
      background: #0D1117;
      border-radius: 6px;
      padding: 10px;
      margin-top: 8px;
      max-height: 140px;
      overflow-y: auto;
    }}
    .summary {{
      font-size: 0.78rem;
      line-height: 1.6;
      color: #8B949E;
      background: #0D1117;
      border-radius: 6px;
      padding: 10px;
      margin-top: 6px;
      max-height: 110px;
      overflow-y: auto;
    }}

    /* Legend */
    .legend-item {{
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 0.78rem;
      padding: 3px 0;
    }}
    .dot {{
      width: 14px; height: 14px;
      border-radius: 3px;
      flex-shrink: 0;
    }}
    .line {{
      width: 24px; height: 3px;
      flex-shrink: 0;
      border-radius: 2px;
    }}

    /* Node detail panel */
    #node-detail {{
      background: #161B22;
      border-top: 1px solid #30363D;
      padding: 14px 18px;
      font-size: 0.8rem;
      min-height: 100px;
      color: #8B949E;
      line-height: 1.6;
    }}
    #node-detail .field {{ margin-bottom: 4px; }}
    #node-detail .field b {{ color: #C9D1D9; }}
    .status-badge {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 20px;
      font-size: 0.7rem;
      font-weight: 600;
    }}
    .badge-Accepted  {{ background: #1A4731; color: #27AE60; }}
    .badge-Contested {{ background: #3D3117; color: #F4D03F; }}
    .badge-Refuted   {{ background: #3D1717; color: #E74C3C; }}

    /* Graph canvas */
    #graph-wrap {{
      display: flex;
      flex-direction: column;
      flex: 1;
      overflow: hidden;
    }}
    #mynetwork {{
      flex: 1;
      height: 100%;
      min-height: 400px;
      background: radial-gradient(ellipse at center, #161B22 0%, #0D1117 100%);
    }}

    /* Controls */
    .controls {{
      position: absolute;
      right: 18px;
      top: 80px;
      display: flex;
      flex-direction: column;
      gap: 6px;
      z-index: 10;
    }}
    .ctrl-btn {{
      background: #1C2333;
      border: 1px solid #30363D;
      color: #C9D1D9;
      border-radius: 7px;
      padding: 7px 12px;
      font-size: 0.75rem;
      cursor: pointer;
      transition: background .2s;
    }}
    .ctrl-btn:hover {{ background: #2D333B; }}

    ::-webkit-scrollbar {{ width: 5px; }}
    ::-webkit-scrollbar-track {{ background: transparent; }}
    ::-webkit-scrollbar-thumb {{ background: #30363D; border-radius: 4px; }}
  </style>
</head>
<body>

<header>
  <svg width="28" height="28" viewBox="0 0 28 28" fill="none">
    <circle cx="14" cy="14" r="13" stroke="#58A6FF" stroke-width="2"/>
    <circle cx="14" cy="9"  r="3" fill="#BC8CFF"/>
    <circle cx="7"  cy="19" r="3" fill="#4A90D9"/>
    <circle cx="21" cy="19" r="3" fill="#5AB56E"/>
    <line x1="14" y1="9"  x2="7"  y2="19" stroke="#58A6FF" stroke-width="1.5"/>
    <line x1="14" y1="9"  x2="21" y2="19" stroke="#58A6FF" stroke-width="1.5"/>
    <line x1="7"  y1="19" x2="21" y2="19" stroke="#E74C3C" stroke-width="1.5" stroke-dasharray="3,2"/>
  </svg>
  <h1>LST 논증망 시각화</h1>
  <span class="meta">생성: {ts[:19].replace("T", " ") if ts else "N/A"}</span>
</header>

<div class="layout">
  <!-- 사이드바 -->
  <aside>

    <!-- 분석 요약 -->
    <div class="card">
      <h2>📊 분석 요약</h2>
      <div class="stat-row"><span>추출 LST 노드</span><span class="stat-val">{lst_count}개</span></div>
      <div class="stat-row"><span>조립된 노드</span><span class="stat-val">{len(assembled)}개</span></div>
      <div class="stat-row"><span>엣지 수</span><span class="stat-val">{len(vis_edges)}개</span></div>
    </div>

    <!-- 고의성 판결 -->
    <div class="card">
      <h2>⚖️ 고의성 판결</h2>
      <div class="stat-row"><span>점수</span><span class="stat-val">{score_pct}</span></div>
      <div class="stat-row"><span>유형</span><span class="stat-val">{intent_type}</span></div>
      <div class="score-bar-wrap"><div class="score-bar"></div></div>
      <div class="score-label">0.0 (불인정) &nbsp;←&nbsp; &nbsp;→&nbsp; 1.0 (명시적 고의)</div>
      <div class="justification">{justification}</div>
    </div>

    <!-- 논증망 요약 -->
    <div class="card">
      <h2>🔍 논증망 요약</h2>
      <div class="summary">{graph_sum}</div>
    </div>

    <!-- 범례 -->
    <div class="card">
      <h2>🎨 범례</h2>
      <div style="font-size:0.7rem;color:#6E7681;margin-bottom:8px;">노드 색상 = 출처</div>
      <div class="legend-item"><div class="dot" style="background:#4A90D9;"></div> Complaint (고소장)</div>
      <div class="legend-item"><div class="dot" style="background:#E8923A;"></div> Interrogation (조서)</div>
      <div class="legend-item"><div class="dot" style="background:#5AB56E;"></div> Evidence (증거)</div>
      <div style="font-size:0.7rem;color:#6E7681;margin:10px 0 8px;">테두리 색상 = 진술 지위</div>
      <div class="legend-item"><div class="dot" style="background:#E74C3C;border:2px solid #E74C3C;"></div> Refuted (반박됨)</div>
      <div class="legend-item"><div class="dot" style="background:#F4D03F;border:2px solid #F4D03F;"></div> Contested (충돌)</div>
      <div style="font-size:0.7rem;color:#6E7681;margin:10px 0 8px;">엣지 유형</div>
      <div class="legend-item"><div class="line" style="background:#95A5A6;"></div> Causal Chain (인과)</div>
      <div class="legend-item"><div class="line" style="background:#E74C3C;border-top:2px dashed #E74C3C;height:0;"></div> Attack (충돌)</div>
      <div class="legend-item"><div class="line" style="background:#27AE60;"></div> Support (지지)</div>
    </div>

  </aside>

  <!-- 그래프 + 노드 상세 -->
  <div id="graph-wrap" style="position:relative;">
    <div id="mynetwork"></div>
    <div class="controls">
      <button class="ctrl-btn" onclick="network.fit()">🔍 전체 보기</button>
      <button class="ctrl-btn" onclick="network.setOptions({{physics:{{enabled:true}}}});setTimeout(()=>network.setOptions({{physics:{{enabled:false}}}}),2000)">↺ 재배치</button>
    </div>
    <div id="node-detail">노드를 클릭하면 상세 정보가 표시됩니다.</div>
  </div>
</div>

<script>
  const nodesData = new vis.DataSet({nodes_json});
  const edgesData = new vis.DataSet({edges_json});

  const nodeMap = {{}};
  nodesData.forEach(n => nodeMap[n.id] = n);

  const container = document.getElementById("mynetwork");
  const options = {{
    nodes: {{
      shape: "box",
      borderWidth: 2,
      shadow: {{ enabled: true, color: "rgba(0,0,0,0.6)", size: 8, x: 2, y: 2 }},
    }},
    edges: {{
      smooth: {{ type: "curvedCW", roundness: 0.15 }},
      shadow: {{ enabled: true, color: "rgba(0,0,0,0.4)", size: 4 }},
      font: {{ align: "middle", strokeWidth: 0, vadjust: -6 }},
    }},
    physics: {{
      enabled: true,
      solver: "forceAtlas2Based",
      forceAtlas2Based: {{ gravitationalConstant: -55, springLength: 160, springConstant: 0.06 }},
      stabilization: {{ iterations: 200 }},
    }},
    interaction: {{
      hover: true,
      tooltipDelay: 150,
      navigationButtons: false,
      keyboard: true,
    }},
    layout: {{ randomSeed: 42 }},
  }};

  const network = new vis.Network(container, {{ nodes: nodesData, edges: edgesData }}, options);

  network.once("stabilized", () => {{
    network.setOptions({{ physics: {{ enabled: false }} }});
    network.fit({{ animation: {{ duration: 800, easingFunction: "easeInOutQuad" }} }});
  }});

  // 노드 클릭 → 상세 패널
  const statusBadge = {{ Accepted:"badge-Accepted", Contested:"badge-Contested", Refuted:"badge-Refuted" }};
  network.on("click", params => {{
    if (params.nodes.length === 0) return;
    const id = params.nodes[0];
    // 원본 JSON 데이터에서 풀 데이터 찾기
    const raw = {json.dumps(assembled, ensure_ascii=False)};
    const node = raw.find(n => n.id === id);
    if (!node) return;
    const badge = `<span class="status-badge ${{statusBadge[node.v_status] || ''}}">${{node.v_status}}</span>`;
    document.getElementById("node-detail").innerHTML = `
      <div class="field"><b>[${{node.id}} | ${{node.doc_source}}]</b></div>
      <div class="field" style="margin-bottom:8px;">${{badge}}</div>
      <div class="field"><b>t:</b> ${{node.t}}</div>
      <div class="field"><b>E (사건):</b> ${{node.E}}</div>
      <div class="field"><b>L (구성요건):</b> ${{node.L}}</div>
      <div class="field"><b>S0:</b> ${{node.S0}}</div>
      <div class="field"><b>S1:</b> ${{node.S1}}</div>
      <div class="field"><b>인과 선행:</b> ${{node.C_sources.length ? node.C_sources.join(", ") : "없음"}}</div>
      <div class="field"><b>논증 엣지:</b> ${{Object.keys(node.argumentative_edges).length ? JSON.stringify(node.argumentative_edges) : "없음"}}</div>
    `;
  }});
</script>
</body>
</html>"""


def main():
    if not os.path.exists(INPUT_JSON):
        print(f"[Error] JSON 파일을 찾을 수 없습니다: {INPUT_JSON}")
        print("  → 먼저 LangGraph 파이프라인을 실행하세요: python -m src.lst_graph_app")
        sys.exit(1)

    with open(INPUT_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    html = render_html(data)

    Path(OUTPUT_HTML).parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[OK] HTML 시각화 생성 완료 → {OUTPUT_HTML}")
    print(f"     브라우저에서 파일을 직접 열면 됩니다 (인터넷 연결 필요 - vis.js CDN 사용).")


if __name__ == "__main__":
    main()
