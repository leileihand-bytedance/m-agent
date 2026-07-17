from __future__ import annotations

import argparse
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from app.admin.services import (
    AdminPaths,
    ProjectOverview,
    build_project_overview,
    list_jobs,
    list_policy_users,
    list_skills,
    set_skill_enabled,
    set_user_skills,
)
from app.platform.config import DEFAULT_ENV_PATH, parse_env_file
from app.platform.data_paths import DataPaths, configured_path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADMIN_STATIC_ROOT = Path(__file__).resolve().parent / "static"
VIS_NETWORK_ASSET = ADMIN_STATIC_ROOT / "vendor" / "vis-network.min.js"
_ENV_VALUES = parse_env_file(DEFAULT_ENV_PATH)
_DATA_PATHS = DataPaths.from_values(_ENV_VALUES, project_root=PROJECT_ROOT)
DEFAULT_PATHS = AdminPaths(
    skills_dir=PROJECT_ROOT / "skills",
    policy_path=PROJECT_ROOT / "config" / "platform-policy.yaml",
    jobs_dir=configured_path(
        _ENV_VALUES,
        "M_AGENT_PLATFORM_JOBS_DIR",
        _DATA_PATHS.writing_jobs,
        project_root=PROJECT_ROOT,
    ),
    project_root=PROJECT_ROOT,
    todo_path=PROJECT_ROOT / "docs" / "development" / "TODO.md",
    review_tasks_dir=_DATA_PATHS.review_tasks,
    heartbeat_dir=_DATA_PATHS.heartbeats,
    policy_db_path=_DATA_PATHS.policy_db,
    bank_db_path=_DATA_PATHS.bank_db,
    heartbeat_max_age_seconds=int(_ENV_VALUES.get("M_AGENT_OPS_HEARTBEAT_MAX_AGE_SECONDS", "180") or "180"),
)


def render_dashboard(
    paths: AdminPaths = DEFAULT_PATHS,
    *,
    show_sensitive: bool = False,
) -> str:
    overview = build_project_overview(paths)
    skills = list_skills(paths.skills_dir)
    users = list_policy_users(paths.policy_path) if show_sensitive else {}
    jobs = list_jobs(paths, limit=20) if show_sensitive else []
    sensitive_nav = (
        '<a href="#users">权限</a><a href="#jobs">任务</a>' if show_sensitive else ""
    )
    sensitive_sections = (
        _render_users_section(users) + _render_jobs_section(jobs) if show_sensitive else ""
    )
    sensitive_toggle = (
        '<a class="sensitive-access-link" href="/">隐藏用户权限与任务记录</a>'
        if show_sensitive
        else '<a class="sensitive-access-link" href="/?show_sensitive=1#users">显示用户权限与任务记录</a>'
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>M-Agent 项目控制台</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #667085;
      --line: #d9dee7;
      --accent: #2563eb;
      --ok: #0f8a56;
      --off: #8a3b12;
      --warn: #b45309;
      --danger: #b42318;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.5;
    }}
    header {{
      background: #101828;
      color: #fff;
      padding: 20px 28px;
    }}
    header h1 {{ margin: 0 0 4px; font-size: 22px; }}
    header p {{ margin: 0; color: #cbd5e1; }}
    nav {{
      position: sticky;
      top: 0;
      z-index: 10;
      display: flex;
      gap: 6px;
      overflow-x: auto;
      padding: 9px max(16px, calc((100% - 1240px) / 2));
      background: rgba(255, 255, 255, 0.97);
      border-bottom: 1px solid var(--line);
    }}
    nav a {{
      color: #344054;
      text-decoration: none;
      font-size: 13px;
      font-weight: 600;
      padding: 7px 10px;
      white-space: nowrap;
    }}
    nav a:hover {{ color: var(--accent); }}
    main {{
      width: min(1240px, calc(100% - 32px));
      margin: 24px auto 48px;
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: 20px;
    }}
    section {{
      min-width: 0;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }}
    .section-head {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      padding: 16px 18px;
      border-bottom: 1px solid var(--line);
      align-items: flex-start;
    }}
    h2 {{ margin: 0; font-size: 18px; }}
    .hint {{ margin: 4px 0 0; color: var(--muted); font-size: 13px; }}
    .architecture-section {{
      width: 100%;
      min-width: 0;
      background: transparent;
      border: 0;
      border-radius: 0;
      overflow: visible;
    }}
    .architecture-section > .section-head {{ padding: 4px 0 16px; }}
    .architecture-toolbar {{
      min-width: 0;
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      gap: 14px;
      margin-bottom: 2px;
    }}
    .architecture-filters {{
      display: flex;
      width: fit-content;
      max-width: 100%;
      overflow-x: auto;
      border: 1px solid #b8c2d6;
      border-radius: 6px;
      background: #fff;
    }}
    .architecture-flow, .architecture-layer, .capability-grid {{ min-width: 0; }}
    .capability-filter {{
      flex: 0 0 auto;
      border: 0;
      border-right: 1px solid #d9dee7;
      border-radius: 0;
      padding: 8px 11px;
      white-space: nowrap;
      color: #475467;
    }}
    .capability-filter:last-child {{ border-right: 0; }}
    .capability-filter.is-active {{ background: #17202a; color: #fff; }}
    .architecture-viewbar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin: 14px 0 10px;
    }}
    .architecture-view-switch {{
      display: inline-flex;
      border: 1px solid #b8c2d6;
      border-radius: 6px;
      overflow: hidden;
      background: #fff;
    }}
    .architecture-view-button {{
      border: 0;
      border-right: 1px solid #d9dee7;
      border-radius: 0;
      min-width: 86px;
    }}
    .architecture-view-button:last-child {{ border-right: 0; }}
    .architecture-view-button.is-active {{ background: #17202a; color: #fff; }}
    .architecture-graph-panel[hidden], .architecture-list-panel[hidden] {{ display: none; }}
    .architecture-graph-layout {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 284px;
      min-height: 650px;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: #fff;
    }}
    .architecture-network-wrap {{ position: relative; min-width: 0; background: #f9fafb; }}
    #architecture-network {{ width: 100%; height: 650px; }}
    .architecture-graph-fallback {{
      position: absolute;
      inset: 0;
      display: grid;
      place-items: center;
      padding: 24px;
      color: var(--muted);
      text-align: center;
      background: #f9fafb;
    }}
    .architecture-graph-fallback[hidden] {{ display: none; }}
    .architecture-detail {{
      padding: 18px;
      border-left: 1px solid var(--line);
      background: #fff;
    }}
    .architecture-detail-kicker {{ color: var(--muted); font-size: 11px; font-weight: 700; }}
    .architecture-detail h3 {{ margin: 6px 0 0; font-size: 17px; }}
    .architecture-detail p {{ margin: 10px 0 0; color: #344054; font-size: 13px; }}
    .architecture-detail-row {{ margin-top: 14px; }}
    .architecture-detail-label {{ display: block; margin-bottom: 4px; color: var(--muted); font-size: 11px; }}
    .architecture-detail-value {{ color: #344054; font-size: 12px; word-break: break-word; }}
    .architecture-graph-meta {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-top: 9px;
      color: var(--muted);
      font-size: 11px;
    }}
    .architecture-layer-key {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .architecture-layer-key span {{
      padding: 3px 6px;
      border: 1px solid var(--line);
      border-radius: 4px;
      background: #fff;
      white-space: nowrap;
    }}
    .architecture-flow {{ position: relative; }}
    .architecture-layer {{
      position: relative;
      display: grid;
      grid-template-columns: 176px minmax(0, 1fr);
      gap: 18px;
      padding: 20px 0 24px;
      border-top: 1px solid var(--line);
    }}
    .architecture-layer::before {{
      content: "";
      position: absolute;
      left: 20px;
      top: 0;
      width: 2px;
      height: 20px;
      background: #98a2b3;
    }}
    .architecture-layer:first-child::before {{ display: none; }}
    .layer-heading {{ padding: 1px 8px 0 0; }}
    .layer-order {{
      display: block;
      margin-bottom: 6px;
      color: #667085;
      font-size: 12px;
      font-weight: 700;
    }}
    .layer-heading h3 {{ margin: 0; font-size: 16px; }}
    .layer-heading p {{ margin: 7px 0 0; color: var(--muted); font-size: 12px; }}
    .capability-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }}
    .capability-node {{
      min-width: 0;
      min-height: 154px;
      padding: 13px 14px 12px;
      border: 1px solid var(--line);
      border-left: 4px solid #98a2b3;
      border-radius: 7px;
      background: #fff;
    }}
    .capability-node[hidden], .architecture-layer[hidden] {{ display: none; }}
    .capability-node.capability-state-stable {{ border-left-color: var(--ok); }}
    .capability-node.capability-state-optimizing {{ border-left-color: var(--accent); }}
    .capability-node.capability-state-building {{ border-left-color: var(--warn); }}
    .capability-node.capability-state-planned {{ border-left-color: #667085; }}
    .capability-node.capability-state-paused, .capability-node.capability-state-disabled {{ border-left-color: #7f5632; }}
    .capability-node-head {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 8px;
    }}
    .capability-node h4 {{ margin: 0; font-size: 14px; }}
    .capability-description {{ margin: 8px 0 10px; color: #344054; font-size: 12px; }}
    .capability-evidence, .capability-next {{
      margin-top: 7px;
      color: var(--muted);
      font-size: 11px;
      word-break: break-word;
    }}
    .capability-next {{ color: #475467; }}
    .capability-status {{
      flex: 0 0 auto;
      font-size: 11px;
      font-weight: 700;
      white-space: nowrap;
    }}
    .capability-status.stable {{ color: var(--ok); }}
    .capability-status.optimizing {{ color: var(--accent); }}
    .capability-status.building {{ color: var(--warn); }}
    .capability-status.planned {{ color: #667085; }}
    .capability-status.paused, .capability-status.disabled {{ color: #7f5632; }}
    .runtime-indicator {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
      margin-top: 7px;
      font-size: 11px;
      font-weight: 700;
    }}
    .runtime-indicator::before {{ content: ""; width: 7px; height: 7px; border-radius: 50%; background: currentColor; }}
    .runtime-indicator.healthy {{ color: var(--ok); }}
    .runtime-indicator.stale, .runtime-indicator.missing {{ color: var(--danger); }}
    table {{
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 12px 14px;
      text-align: left;
      vertical-align: top;
      word-break: break-word;
      font-size: 14px;
    }}
    th {{ background: #f9fafb; color: #475467; font-weight: 600; }}
    tr:last-child td {{ border-bottom: 0; }}
    .status {{
      display: inline-flex;
      align-items: center;
      min-width: 54px;
      justify-content: center;
      border-radius: 999px;
      padding: 2px 9px;
      font-size: 12px;
      font-weight: 700;
      border: 1px solid currentColor;
    }}
    .status-on {{ color: var(--ok); }}
    .status-off {{ color: var(--off); }}
    .status-healthy, .status-stable {{ color: var(--ok); }}
    .status-stale, .status-building {{ color: var(--warn); }}
    .status-missing, .status-focus {{ color: var(--danger); }}
    .priority {{ font-weight: 750; }}
    .priority-P0 {{ color: var(--danger); }}
    .priority-P1 {{ color: var(--warn); }}
    .priority-P2, .priority-P3 {{ color: #475467; }}
    .change-list {{ margin: 0; padding: 4px 18px 12px; list-style: none; }}
    .change-list li {{
      display: grid;
      grid-template-columns: 78px 92px minmax(0, 1fr);
      gap: 12px;
      padding: 10px 0;
      border-bottom: 1px solid var(--line);
      font-size: 14px;
    }}
    .change-list li:last-child {{ border-bottom: 0; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
    button {{
      appearance: none;
      border: 1px solid #b8c2d6;
      background: #fff;
      color: var(--text);
      border-radius: 6px;
      padding: 7px 10px;
      font-size: 13px;
      cursor: pointer;
    }}
    button.primary {{
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }}
    input[type="text"] {{
      width: 100%;
      min-height: 34px;
      border: 1px solid #b8c2d6;
      border-radius: 6px;
      padding: 7px 9px;
      font-size: 14px;
    }}
    form.inline {{ display: inline; }}
    .sensitive-access {{ display: flex; justify-content: flex-end; }}
    .sensitive-access-link {{
      display: inline-flex;
      align-items: center;
      min-height: 34px;
      border: 1px solid #b8c2d6;
      border-radius: 6px;
      padding: 7px 10px;
      background: #fff;
      color: #344054;
      font-size: 13px;
      font-weight: 600;
      text-decoration: none;
    }}
    .sensitive-access-link:hover {{ color: var(--accent); border-color: var(--accent); }}
    .muted {{ color: var(--muted); }}
    .empty {{ padding: 22px 18px; color: var(--muted); }}
    .skill-preview {{
      max-height: 92px;
      overflow: auto;
      margin-top: 8px;
      padding: 8px;
      background: #f9fafb;
      border: 1px solid var(--line);
      border-radius: 6px;
      white-space: pre-wrap;
      color: #344054;
      font-size: 12px;
    }}
    @media (max-width: 760px) {{
      header {{ padding: 16px; }}
      main {{ width: calc(100% - 20px); }}
      .architecture-toolbar {{ display: block; }}
      .architecture-filters {{ margin-top: 12px; width: 100%; max-width: 100%; }}
      .architecture-viewbar {{ align-items: stretch; flex-direction: column; }}
      .architecture-view-switch {{ width: 100%; }}
      .architecture-view-button {{ flex: 1 1 50%; }}
      .architecture-graph-layout {{ grid-template-columns: 1fr; min-height: 0; }}
      #architecture-network {{ height: 520px; }}
      .architecture-detail {{ border-left: 0; border-top: 1px solid var(--line); }}
      .architecture-graph-meta {{ align-items: flex-start; flex-direction: column; }}
      .architecture-layer {{ grid-template-columns: 1fr; gap: 12px; }}
      .architecture-layer::before {{ left: 12px; }}
      .capability-grid {{ grid-template-columns: 1fr; }}
      .capability-node {{ min-height: 0; }}
      .change-list li {{ grid-template-columns: 64px 1fr; }}
      .change-list li span:last-child {{ grid-column: 1 / -1; }}
      table, thead, tbody, th, td, tr {{ display: block; }}
      thead {{ display: none; }}
      tr {{ border-bottom: 1px solid var(--line); }}
      td {{ border: 0; padding: 8px 14px; }}
      td::before {{
        content: attr(data-label);
        display: block;
        color: var(--muted);
        font-size: 12px;
        margin-bottom: 2px;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>M-Agent 项目控制台</h1>
    <p>查看各板块进度、下一步待办、运行健康，并管理 Skill。</p>
  </header>
  <nav aria-label="控制台导航">
    <a href="#architecture">项目总览</a>
    <a href="#modules">板块</a>
    <a href="#review-statistics">审核统计</a>
    <a href="#todos">待办</a>
    <a href="#runtime">运行</a>
    <a href="#changes">更新</a>
    <a href="#skills">Skills</a>
    {sensitive_nav}
  </nav>
  <main>
    {_render_architecture_section(overview)}
    {_render_modules_section(overview)}
    {_render_review_statistics_section(overview)}
    {_render_todos_section(overview)}
    {_render_runtime_section(overview)}
    {_render_changes_section(overview)}
    {_render_skills_section(skills)}
    <div class="sensitive-access">{sensitive_toggle}</div>
    {sensitive_sections}
  </main>
  <script src="/static/vendor/vis-network.min.js"></script>
  <script>
    (() => {{
      const filters = Array.from(document.querySelectorAll("[data-capability-filter]"));
      const listNodes = Array.from(document.querySelectorAll("[data-capability-status]"));
      const layers = Array.from(document.querySelectorAll("[data-architecture-layer]"));
      const viewButtons = Array.from(document.querySelectorAll("[data-architecture-view]"));
      const viewPanels = Array.from(document.querySelectorAll("[data-architecture-panel]"));
      const fitButton = document.getElementById("architecture-fit");
      const graphElement = document.getElementById("architecture-network");
      const graphFallback = document.getElementById("architecture-graph-fallback");
      const graphDataElement = document.getElementById("architecture-graph-data");
      let selectedStatus = "all";
      let network = null;
      let graphNodeData = null;
      let graphEdgeData = null;
      let graphRecords = [];
      let graphEdges = [];

      const palette = {{
        stable: {{ background: "#ecfdf3", border: "#0f8a56", highlight: {{ background: "#d1fadf", border: "#087443" }} }},
        optimizing: {{ background: "#eff6ff", border: "#2563eb", highlight: {{ background: "#dbeafe", border: "#1d4ed8" }} }},
        building: {{ background: "#fff7ed", border: "#b45309", highlight: {{ background: "#ffedd5", border: "#92400e" }} }},
        planned: {{ background: "#f2f4f7", border: "#667085", highlight: {{ background: "#e4e7ec", border: "#475467" }} }},
        paused: {{ background: "#fff8eb", border: "#7f5632", highlight: {{ background: "#fef0c7", border: "#69421f" }} }},
        disabled: {{ background: "#f9fafb", border: "#98a2b3", highlight: {{ background: "#f2f4f7", border: "#667085" }} }},
      }};

      const toGraphNode = (record) => ({{
        id: record.id,
        label: record.name,
        level: record.level,
        group: record.status,
      }});

      const toGraphEdge = (record, index) => ({{
        id: record.source_id + "-" + record.target_id + "-" + index,
        from: record.source_id,
        to: record.target_id,
        label: record.label,
      }});

      const showNodeDetail = (nodeId) => {{
        const record = graphRecords.find((item) => item.id === nodeId);
        if (!record) return;
        document.getElementById("architecture-detail-layer").textContent = record.layer_order + " · " + record.layer_name;
        document.getElementById("architecture-detail-name").textContent = record.name;
        document.getElementById("architecture-detail-description").textContent = record.description;
        document.getElementById("architecture-detail-status").textContent = record.status_label;
        document.getElementById("architecture-detail-runtime").textContent = record.runtime_label || "不适用";
        document.getElementById("architecture-detail-evidence").textContent = record.evidence;
        const nextText = record.todo_id && record.next_action
          ? record.todo_id + "：" + record.next_action
          : "暂无开放待办";
        document.getElementById("architecture-detail-next").textContent = nextText;
      }};

      const fitGraph = () => {{
        if (!network) return;
        network.fit({{ animation: {{ duration: 320, easingFunction: "easeInOutQuad" }} }});
      }};

      const applyGraphFilter = () => {{
        if (!network || !graphNodeData || !graphEdgeData) return;
        const visibleRecords = graphRecords.filter(
          (record) => selectedStatus === "all" || record.status === selectedStatus
        );
        const visibleIds = new Set(visibleRecords.map((record) => record.id));
        graphNodeData.clear();
        graphNodeData.add(visibleRecords.map(toGraphNode));
        graphEdgeData.clear();
        graphEdgeData.add(
          graphEdges
            .filter((edge) => visibleIds.has(edge.source_id) && visibleIds.has(edge.target_id))
            .map(toGraphEdge)
        );
        const firstRecord = visibleRecords[0];
        if (firstRecord) {{
          network.selectNodes([firstRecord.id]);
          showNodeDetail(firstRecord.id);
        }}
        window.setTimeout(fitGraph, 60);
      }};

      const setView = (view) => {{
        if (view === "graph" && !network) return;
        for (const button of viewButtons) {{
          const active = button.dataset.architectureView === view;
          button.classList.toggle("is-active", active);
          button.setAttribute("aria-selected", active ? "true" : "false");
        }}
        for (const panel of viewPanels) panel.hidden = panel.dataset.architecturePanel !== view;
        fitButton.hidden = view !== "graph";
        if (view === "graph") window.setTimeout(fitGraph, 60);
      }};

      const initializeGraph = () => {{
        if (!window.vis || !window.vis.Network || !window.vis.DataSet || !graphDataElement) {{
          if (graphFallback) graphFallback.hidden = false;
          return;
        }}
        try {{
          const graphData = JSON.parse(graphDataElement.textContent || "{{}}");
          graphRecords = Array.isArray(graphData.nodes) ? graphData.nodes : [];
          graphEdges = Array.isArray(graphData.edges) ? graphData.edges : [];
          graphNodeData = new window.vis.DataSet(graphRecords.map(toGraphNode));
          graphEdgeData = new window.vis.DataSet(graphEdges.map(toGraphEdge));
          network = new window.vis.Network(
            graphElement,
            {{ nodes: graphNodeData, edges: graphEdgeData }},
            {{
              autoResize: true,
              groups: Object.fromEntries(
                Object.entries(palette).map(([status, color]) => [status, {{ color }}])
              ),
              nodes: {{
                shape: "box",
                borderWidth: 1.5,
                borderWidthSelected: 2.5,
                margin: {{ top: 10, right: 12, bottom: 10, left: 12 }},
                widthConstraint: {{ minimum: 118, maximum: 168 }},
                font: {{ color: "#17202a", size: 12, face: "-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif" }},
              }},
              edges: {{
                arrows: {{ to: {{ enabled: true, scaleFactor: 0.55 }} }},
                color: {{ color: "#98a2b3", highlight: "#2563eb", hover: "#667085" }},
                width: 1,
                selectionWidth: 1.5,
                font: {{ size: 9, color: "#667085", background: "rgba(249,250,251,0.9)", strokeWidth: 0, align: "horizontal" }},
                smooth: {{ enabled: true, type: "cubicBezier", forceDirection: "vertical", roundness: 0.35 }},
              }},
              layout: {{
                hierarchical: {{
                  enabled: true,
                  direction: "UD",
                  sortMethod: "directed",
                  levelSeparation: 175,
                  nodeSpacing: 170,
                  treeSpacing: 220,
                  blockShifting: true,
                  edgeMinimization: true,
                  parentCentralization: true,
                }},
              }},
              physics: false,
              interaction: {{ hover: true, keyboard: true, multiselect: false, tooltipDelay: 200 }},
            }}
          );
          network.on("beforeDrawing", (context) => {{
            context.save();
            context.setTransform(1, 0, 0, 1, 0, 0);
            context.fillStyle = "#f9fafb";
            context.fillRect(0, 0, context.canvas.width, context.canvas.height);
            context.restore();
          }});
          network.on("click", (params) => {{
            if (params.nodes.length) showNodeDetail(params.nodes[0]);
          }});
          if (graphRecords.length) {{
            network.selectNodes([graphRecords[0].id]);
            showNodeDetail(graphRecords[0].id);
          }}
          setView("graph");
        }} catch (error) {{
          network = null;
          if (graphFallback) graphFallback.hidden = false;
        }}
      }};

      for (const button of viewButtons) {{
        button.addEventListener("click", () => setView(button.dataset.architectureView));
      }}
      fitButton.addEventListener("click", fitGraph);
      for (const filter of filters) {{
        filter.addEventListener("click", () => {{
          selectedStatus = filter.dataset.capabilityFilter;
          for (const item of filters) item.classList.toggle("is-active", item === filter);
          for (const node of listNodes) {{
            node.hidden = selectedStatus !== "all" && node.dataset.capabilityStatus !== selectedStatus;
          }}
          for (const layer of layers) {{
            layer.hidden = !Array.from(layer.querySelectorAll("[data-capability-status]")).some(node => !node.hidden);
          }}
          applyGraphFilter();
        }});
      }}
      initializeGraph();
    }})();
  </script>
</body>
</html>"""


def create_handler(paths: AdminPaths = DEFAULT_PATHS) -> type[BaseHTTPRequestHandler]:
    class AdminHandler(BaseHTTPRequestHandler):
        server_version = "MAgentAdmin/0.1"

        def do_GET(self) -> None:
            request = urlsplit(self.path)
            if request.path == "/static/vendor/vis-network.min.js":
                self._send_javascript(VIS_NETWORK_ASSET)
                return
            if request.path not in {"/", "/index.html"}:
                self._send_text("Not found", HTTPStatus.NOT_FOUND)
                return
            query = parse_qs(request.query, keep_blank_values=True)
            show_sensitive = query.get("show_sensitive") == ["1"]
            self._send_html(render_dashboard(paths, show_sensitive=show_sensitive))

        def do_POST(self) -> None:
            handlers: dict[str, Callable[[dict[str, list[str]]], None]] = {
                "/skills/toggle": self._handle_skill_toggle,
                "/users/update": self._handle_user_update,
            }
            handler = handlers.get(self.path)
            if handler is None:
                self._send_text("Not found", HTTPStatus.NOT_FOUND)
                return

            try:
                form = self._read_form()
                handler(form)
            except Exception as exc:  # noqa: BLE001
                self._send_text(f"操作失败：{exc}", HTTPStatus.BAD_REQUEST)
                return
            self.send_response(HTTPStatus.SEE_OTHER)
            redirect = "/?show_sensitive=1#users" if self.path == "/users/update" else "/"
            self.send_header("Location", redirect)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

        def _handle_skill_toggle(self, form: dict[str, list[str]]) -> None:
            skill_id = _one(form, "skill_id")
            enabled = _one(form, "enabled") == "true"
            set_skill_enabled(paths.skills_dir, skill_id, enabled)

        def _handle_user_update(self, form: dict[str, list[str]]) -> None:
            userid = _one(form, "userid").strip()
            if not userid:
                raise ValueError("userid 不能为空")
            raw_skills = _one(form, "allowed_skills")
            allowed_skills = [item.strip() for item in raw_skills.split(",") if item.strip()]
            set_user_skills(paths.policy_path, userid, allowed_skills)

        def _read_form(self) -> dict[str, list[str]]:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            return parse_qs(body, keep_blank_values=True)

        def _send_html(self, html: str) -> None:
            data = html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_javascript(self, asset_path: Path) -> None:
            if not asset_path.is_file():
                self._send_text("Not found", HTTPStatus.NOT_FOUND)
                return
            data = asset_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_text(self, text: str, status: HTTPStatus) -> None:
            data = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return AdminHandler


def run(host: str = "127.0.0.1", port: int = 8787, paths: AdminPaths = DEFAULT_PATHS) -> None:
    server = ThreadingHTTPServer((host, port), create_handler(paths))
    print(f"M-Agent 管理后台已启动：http://{host}:{port}")
    print("只监听本机地址。按 Ctrl+C 停止。")
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="M-Agent local admin console")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    run(host=args.host, port=args.port)


def _render_architecture_section(overview: ProjectOverview) -> str:
    counts = overview.capability_status_counts
    total = sum(counts.values())
    filters = (
        ("all", "全部", total),
        ("stable", "稳定运行", counts.get("stable", 0)),
        ("optimizing", "上线优化中", counts.get("optimizing", 0)),
        ("building", "建设中", counts.get("building", 0)),
        ("planned", "待建设", counts.get("planned", 0)),
        ("paused", "已暂缓", counts.get("paused", 0)),
        ("disabled", "已关闭", counts.get("disabled", 0)),
    )
    filter_html = "".join(
        f'<button type="button" class="capability-filter{" is-active" if status == "all" else ""}" '
        f'data-capability-filter="{escape(status)}">{escape(label)} {count}</button>'
        for status, label, count in filters
    )
    layers = "\n".join(_render_architecture_layer(layer) for layer in overview.architecture_layers)
    layer_key = "".join(
        f'<span>{escape(layer.order)} {escape(layer.name)}</span>'
        for layer in overview.architecture_layers
    )
    graph_data = _architecture_graph_json(overview)
    return f"""<section id="architecture" class="architecture-section">
  <div class="architecture-toolbar">
    <div>
      <h2>项目总览</h2>
      <p class="hint">建设成熟度来自现有代码、Skill 配置和 TODO；Bot 是否在线另看节点心跳。</p>
    </div>
    <div class="architecture-filters" role="group" aria-label="按建设状态筛选功能">
      {filter_html}
    </div>
  </div>
  <div class="architecture-viewbar">
    <div class="architecture-view-switch" role="tablist" aria-label="架构展示方式">
      <button type="button" class="architecture-view-button" data-architecture-view="graph" role="tab" aria-selected="false">关系图</button>
      <button type="button" class="architecture-view-button is-active" data-architecture-view="list" role="tab" aria-selected="true">状态清单</button>
    </div>
    <button type="button" id="architecture-fit" title="将全部节点缩放到可见区域" hidden>适应画布</button>
  </div>
  <div class="architecture-graph-panel" data-architecture-panel="graph" hidden>
    <div class="architecture-graph-layout">
      <div class="architecture-network-wrap">
        <div id="architecture-network" role="img" aria-label="M-Agent 五层架构能力关系图"></div>
        <div id="architecture-graph-fallback" class="architecture-graph-fallback" hidden>关系图组件未能加载，已保留状态清单供查看。</div>
      </div>
      <aside class="architecture-detail" aria-live="polite">
        <div class="architecture-detail-kicker" id="architecture-detail-layer">节点详情</div>
        <h3 id="architecture-detail-name">点击节点查看详情</h3>
        <p id="architecture-detail-description">节点颜色表示建设成熟度，箭头表示真实调用或数据关系。</p>
        <div class="architecture-detail-row">
          <span class="architecture-detail-label">建设状态</span>
          <div class="architecture-detail-value" id="architecture-detail-status">-</div>
        </div>
        <div class="architecture-detail-row">
          <span class="architecture-detail-label">运行状态</span>
          <div class="architecture-detail-value" id="architecture-detail-runtime">不适用</div>
        </div>
        <div class="architecture-detail-row">
          <span class="architecture-detail-label">事实依据</span>
          <div class="architecture-detail-value" id="architecture-detail-evidence">-</div>
        </div>
        <div class="architecture-detail-row">
          <span class="architecture-detail-label">下一步</span>
          <div class="architecture-detail-value" id="architecture-detail-next">暂无开放待办</div>
        </div>
      </aside>
    </div>
    <div class="architecture-graph-meta">
      <div class="architecture-layer-key">{layer_key}</div>
      <span>鼠标滚轮或双指缩放，拖动画布查看；筛选会同时作用于关系图和清单。</span>
    </div>
  </div>
  <div class="architecture-flow architecture-list-panel" data-architecture-panel="list">{layers}</div>
  <script id="architecture-graph-data" type="application/json">{graph_data}</script>
</section>"""


def _architecture_graph_json(overview: ProjectOverview) -> str:
    nodes: list[dict[str, object]] = []
    for level, layer in enumerate(overview.architecture_layers):
        for capability in layer.capabilities:
            nodes.append(
                {
                    "id": capability.id,
                    "name": capability.name,
                    "status": capability.status,
                    "status_label": capability.status_label,
                    "layer": layer.key,
                    "layer_name": layer.name,
                    "layer_order": layer.order,
                    "level": level,
                    "description": capability.description,
                    "evidence": capability.evidence,
                    "todo_id": capability.todo_id,
                    "next_action": capability.next_action,
                    "runtime_status": capability.runtime_status,
                    "runtime_label": capability.runtime_label,
                }
            )
    edges = [
        {
            "source_id": relation.source_id,
            "target_id": relation.target_id,
            "label": relation.label,
        }
        for relation in overview.architecture_relations
    ]
    data = json.dumps(
        {"nodes": nodes, "edges": edges},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        data.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _render_architecture_layer(layer: object) -> str:
    capabilities = "\n".join(
        _render_capability_node(capability) for capability in getattr(layer, "capabilities")
    )
    return f"""<div class="architecture-layer" data-architecture-layer="{escape(str(getattr(layer, "key")))}">
  <div class="layer-heading">
    <span class="layer-order">{escape(str(getattr(layer, "order")))}</span>
    <h3>{escape(str(getattr(layer, "name")))}</h3>
    <p>{escape(str(getattr(layer, "description")))}</p>
  </div>
  <div class="capability-grid">{capabilities}</div>
</div>"""


def _render_capability_node(capability: object) -> str:
    status = str(getattr(capability, "status"))
    runtime_status = str(getattr(capability, "runtime_status"))
    runtime_label = str(getattr(capability, "runtime_label"))
    runtime_html = ""
    if runtime_status and runtime_label:
        runtime_html = (
            f'<div class="runtime-indicator {escape(runtime_status)}">{escape(runtime_label)}</div>'
        )
    todo_id = str(getattr(capability, "todo_id"))
    next_action = str(getattr(capability, "next_action"))
    next_html = ""
    if todo_id and next_action:
        next_html = (
            f'<div class="capability-next"><strong>{escape(todo_id)}</strong>：{escape(next_action)}</div>'
        )
    return f"""<article class="capability-node capability-state-{escape(status)}" data-capability-status="{escape(status)}">
  <div class="capability-node-head">
    <h4>{escape(str(getattr(capability, "name")))}</h4>
    <span class="capability-status {escape(status)}">{escape(str(getattr(capability, "status_label")))}</span>
  </div>
  <p class="capability-description">{escape(str(getattr(capability, "description")))}</p>
  {runtime_html}
  <div class="capability-evidence">依据：{escape(str(getattr(capability, "evidence")))}</div>
  {next_html}
</article>"""


def _render_modules_section(overview: ProjectOverview) -> str:
    rows = "\n".join(_render_module_row(module) for module in overview.modules)
    return f"""<section id="modules">
  <div class="section-head">
    <div>
      <h2>板块进展</h2>
      <p class="hint">当前能力来自代码与运行数据；最新更新来自对应目录的最近 Git 提交；首要待办来自 TODO 归属。</p>
    </div>
  </div>
  <table>
    <thead><tr>
      <th style="width: 12%">板块</th>
      <th style="width: 12%">状态</th>
      <th style="width: 31%">当前情况</th>
      <th style="width: 25%">最新更新</th>
      <th>下一步</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
</section>"""


def _render_module_row(module: object) -> str:
    status = str(getattr(module, "status"))
    status_class = {
        "重点推进": "status-focus",
        "建设中": "status-building",
        "持续优化": "status-building",
        "稳定": "status-stable",
    }.get(status, "status-off")
    todo_id = str(getattr(module, "next_todo_id"))
    todo_title = str(getattr(module, "next_todo_title"))
    next_text = f"{todo_id} · {todo_title}" if todo_id else todo_title
    return f"""<tr>
  <td data-label="板块"><strong>{escape(str(getattr(module, "name")))}</strong></td>
  <td data-label="状态"><span class="status {status_class}">{escape(status)}</span></td>
  <td data-label="当前情况">{escape(str(getattr(module, "current_summary")))}</td>
  <td data-label="最新更新">{escape(str(getattr(module, "latest_change")))}</td>
  <td data-label="下一步">{escape(next_text)}</td>
</tr>"""


def _render_review_statistics_section(overview: ProjectOverview) -> str:
    rows = "\n".join(
        _render_review_statistics_row(statistic)
        for statistic in overview.review_capability_stats
    )
    return f"""<section id="review-statistics">
  <div class="section-head">
    <div>
      <h2>审核模块统计</h2>
      <p class="hint">八类审核分别统计真实任务。这里只读取不含材料正文的状态和运行指标；平均耗时只计算已记录新指标的任务。</p>
    </div>
  </div>
  <table>
    <thead><tr>
      <th>审核模块</th><th>任务</th><th>完成</th><th>失败</th><th>进行中</th>
      <th>已交付</th><th>交付失败</th><th>平均耗时</th><th>模型调用</th><th>模型失败</th><th>问题数</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
</section>"""


def _render_review_statistics_row(statistic: object) -> str:
    average_elapsed_ms = float(getattr(statistic, "average_elapsed_ms"))
    elapsed_text = (
        f"{average_elapsed_ms / 1000:.1f} 秒"
        if int(getattr(statistic, "elapsed_sample_count")) > 0
        else "暂无"
    )
    values = (
        ("审核模块", f"<strong>{escape(str(getattr(statistic, 'capability_name')))}</strong>"),
        ("任务", str(getattr(statistic, "total"))),
        ("完成", str(getattr(statistic, "completed"))),
        ("失败", str(getattr(statistic, "failed"))),
        ("进行中", str(getattr(statistic, "incomplete"))),
        ("已交付", str(getattr(statistic, "delivered"))),
        ("交付失败", str(getattr(statistic, "delivery_failed"))),
        ("平均耗时", escape(elapsed_text)),
        ("模型调用", str(getattr(statistic, "model_calls"))),
        ("模型失败", str(getattr(statistic, "model_failures"))),
        ("问题数", str(getattr(statistic, "finding_count"))),
    )
    cells = "".join(
        f'<td data-label="{label}">{value}</td>' for label, value in values
    )
    return f"<tr>{cells}</tr>"


def _render_todos_section(overview: ProjectOverview) -> str:
    open_todos = [todo for todo in overview.todos if todo.is_open]
    if not open_todos:
        body = '<div class="empty">当前没有开放待办</div>'
    else:
        rows = "\n".join(_render_todo_row(todo) for todo in open_todos)
        body = f"""<table>
  <thead><tr>
    <th style="width: 13%">编号</th>
    <th style="width: 29%">事项</th>
    <th style="width: 11%">状态</th>
    <th style="width: 9%">优先级</th>
    <th style="width: 19%">归属</th>
    <th>下一动作</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>"""
    return f"""<section id="todos">
  <div class="section-head">
    <div>
      <h2>下一步待办</h2>
      <p class="hint">直接读取 docs/development/TODO.md，已完成、已取消和已暂缓事项不在此列表展示。</p>
    </div>
  </div>
  {body}
</section>"""


def _render_todo_row(todo: object) -> str:
    priority = str(getattr(todo, "priority"))
    return f"""<tr>
  <td data-label="编号"><code>{escape(str(getattr(todo, "todo_id")))}</code></td>
  <td data-label="事项"><strong>{escape(str(getattr(todo, "title")))}</strong></td>
  <td data-label="状态">{escape(str(getattr(todo, "status")))}</td>
  <td data-label="优先级"><span class="priority priority-{escape(priority)}">{escape(priority)}</span></td>
  <td data-label="归属">{escape(str(getattr(todo, "owner")))}</td>
  <td data-label="下一动作">{escape(str(getattr(todo, "next_action")))}</td>
</tr>"""


def _render_runtime_section(overview: ProjectOverview) -> str:
    rows = "\n".join(_render_service_row(service) for service in overview.services)
    return f"""<section id="runtime">
  <div class="section-head">
    <div>
      <h2>运行状态</h2>
      <p class="hint">只读取运维心跳摘要。正常表示最近一次心跳未超过配置阈值，不代表单次模型调用一定成功。</p>
    </div>
  </div>
  <table>
    <thead><tr><th style="width: 25%">服务</th><th style="width: 18%">状态</th><th style="width: 24%">最后心跳</th><th>距今</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</section>"""


def _render_service_row(service: object) -> str:
    status = str(getattr(service, "status"))
    label = {"healthy": "正常", "stale": "心跳超时", "missing": "未运行"}.get(status, status)
    age_seconds = getattr(service, "age_seconds")
    age_text = _format_age(age_seconds)
    updated_at = str(getattr(service, "updated_at")) or "无记录"
    return f"""<tr>
  <td data-label="服务"><strong>{escape(str(getattr(service, "name")))}</strong><br><span class="muted">{escape(str(getattr(service, "service")))}</span></td>
  <td data-label="状态"><span class="status status-{escape(status)}">{escape(label)}</span></td>
  <td data-label="最后心跳">{escape(updated_at)}</td>
  <td data-label="距今">{escape(age_text)}</td>
</tr>"""


def _format_age(age_seconds: object) -> str:
    if not isinstance(age_seconds, int):
        return "无心跳"
    if age_seconds < 60:
        return f"{age_seconds} 秒"
    if age_seconds < 3600:
        return f"{age_seconds // 60} 分钟"
    return f"{age_seconds // 3600} 小时"


def _render_changes_section(overview: ProjectOverview) -> str:
    if not overview.recent_changes:
        body = '<div class="empty">暂无 Git 更新记录</div>'
    else:
        items = "\n".join(
            f'<li><code>{escape(change.commit)}</code><span class="muted">{escape(change.date)}</span><span>{escape(change.subject)}</span></li>'
            for change in overview.recent_changes
        )
        body = f'<ol class="change-list">{items}</ol>'
    return f"""<section id="changes">
  <div class="section-head">
    <div>
      <h2>最近更新</h2>
      <p class="hint">最近 8 个本地 Git 提交摘要；控制台不会自动联网拉取远端。</p>
    </div>
  </div>
  {body}
</section>"""


def _render_skills_section(skills: object) -> str:
    skill_list = list(skills)
    if not skill_list:
        body = '<div class="empty">暂无 Skill</div>'
    else:
        rows = "\n".join(_render_skill_row(skill) for skill in skill_list)
        body = f"""<table>
  <thead>
    <tr>
      <th style="width: 20%">Skill</th>
      <th style="width: 13%">状态</th>
      <th style="width: 23%">触发词</th>
      <th style="width: 18%">工具</th>
      <th style="width: 16%">工作流</th>
      <th style="width: 10%">操作</th>
    </tr>
  </thead>
  <tbody>{rows}</tbody>
</table>"""
    return f"""<section id="skills">
  <div class="section-head">
    <div>
      <h2>Skill 管理</h2>
      <p class="hint">开启后才会被底座路由候选命中；关闭不会删除配置。</p>
    </div>
  </div>
  {body}
</section>"""


def _render_skill_row(skill: object) -> str:
    enabled = bool(getattr(skill, "enabled"))
    status_class = "status-on" if enabled else "status-off"
    status_text = "开启" if enabled else "关闭"
    next_enabled = "false" if enabled else "true"
    action_text = "关闭" if enabled else "开启"
    triggers = ", ".join(getattr(skill, "triggers"))
    tools = ", ".join(getattr(skill, "allowed_tools"))
    return f"""<tr>
  <td data-label="Skill"><strong>{escape(str(getattr(skill, "name")))}</strong><br><span class="muted">{escape(str(getattr(skill, "id")))}</span><br>{escape(str(getattr(skill, "description")))}<div class="skill-preview">{escape(str(getattr(skill, "skill_preview")))}</div></td>
  <td data-label="状态"><span class="status {status_class}">{status_text}</span></td>
  <td data-label="触发词">{escape(triggers) or '<span class="muted">未配置</span>'}</td>
  <td data-label="工具">{escape(tools) or '<span class="muted">未配置</span>'}</td>
  <td data-label="工作流">{escape(str(getattr(skill, "workflow")))}</td>
  <td data-label="操作">
    <form class="inline" method="post" action="/skills/toggle">
      <input type="hidden" name="skill_id" value="{escape(str(getattr(skill, "id")))}">
      <input type="hidden" name="enabled" value="{next_enabled}">
      <button type="submit">{action_text}</button>
    </form>
  </td>
</tr>"""


def _render_users_section(users: dict[str, list[str]]) -> str:
    if not users:
        body = '<div class="empty">暂无用户权限配置</div>'
    else:
        rows = "\n".join(_render_user_row(userid, skills) for userid, skills in sorted(users.items()))
        body = f"""<table>
  <thead>
    <tr>
      <th style="width: 26%">用户 ID</th>
      <th>允许使用的 Skill</th>
      <th style="width: 120px">操作</th>
    </tr>
  </thead>
  <tbody>{rows}</tbody>
</table>"""
    return f"""<section id="users">
  <div class="section-head">
    <div>
      <h2>用户权限</h2>
      <p class="hint">用英文逗号分隔多个 skill_id，例如 direct_report, writer1。</p>
    </div>
  </div>
  {body}
</section>"""


def _render_user_row(userid: str, skills: list[str]) -> str:
    skills_text = ", ".join(skills)
    form_id = f"user-form-{_html_id(userid)}"
    return f"""<tr>
  <td data-label="用户 ID">{escape(userid)}</td>
  <td data-label="允许使用的 Skill">
    <input form="{form_id}" type="text" name="allowed_skills" value="{escape(skills_text)}" aria-label="allowed skills for {escape(userid)}">
  </td>
  <td data-label="操作">
    <form id="{form_id}" method="post" action="/users/update">
      <input type="hidden" name="userid" value="{escape(userid)}">
      <button class="primary" type="submit">保存</button>
    </form>
  </td>
</tr>"""


def _render_jobs_section(jobs: object) -> str:
    job_list = list(jobs)
    if not job_list:
        body = '<div class="empty">暂无任务记录</div>'
    else:
        rows = "\n".join(_render_job_row(job) for job in job_list)
        body = f"""<table>
  <thead>
    <tr>
      <th style="width: 17%">任务</th>
      <th style="width: 14%">用户</th>
      <th style="width: 13%">Skill</th>
      <th style="width: 20%">标题</th>
      <th>消息</th>
    </tr>
  </thead>
  <tbody>{rows}</tbody>
</table>"""
    return f"""<section id="jobs">
  <div class="section-head">
    <div>
      <h2>最近任务</h2>
      <p class="hint">只展示任务摘要，不展示密钥或完整正文。</p>
    </div>
  </div>
  {body}
</section>"""


def _render_job_row(job: object) -> str:
    job_id = str(getattr(job, "job_id"))
    created_at = str(getattr(job, "created_at"))
    needs_clarification = bool(getattr(job, "needs_clarification"))
    message = str(getattr(job, "message"))
    if needs_clarification:
        message = f"需追问：{message}"
    return f"""<tr>
  <td data-label="任务"><strong>{escape(job_id)}</strong><br><span class="muted">{escape(created_at)}</span></td>
  <td data-label="用户">{escape(str(getattr(job, "sender_userid")))}</td>
  <td data-label="Skill">{escape(str(getattr(job, "skill_id")))}</td>
  <td data-label="标题">{escape(str(getattr(job, "title"))) or '<span class="muted">无标题</span>'}</td>
  <td data-label="消息">{escape(message)}<br><span class="muted">{escape(str(getattr(job, "message_preview")))}</span></td>
</tr>"""


def _one(form: dict[str, list[str]], key: str) -> str:
    values = form.get(key)
    if not values:
        raise ValueError(f"缺少参数：{key}")
    return values[0]


def _html_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-") or "item"


if __name__ == "__main__":
    main()
