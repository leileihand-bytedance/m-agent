from __future__ import annotations

import argparse
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
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
from app.platform.delivery_recovery import (
    DeliveryRecoveryCandidate,
    DeliveryRecoveryService,
    build_default_service as build_delivery_recovery_service,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
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
    delivery_recoveries: tuple[DeliveryRecoveryCandidate, ...] = (),
    delivery_recovery_error: bool = False,
    csrf_token: str = "",
) -> str:
    overview = build_project_overview(paths)
    skills = list_skills(paths.skills_dir)
    users = list_policy_users(paths.policy_path) if show_sensitive else {}
    jobs = list_jobs(paths, limit=20) if show_sensitive else []
    sensitive_nav = (
        '<a href="#users">权限</a><a href="#jobs">任务</a>' if show_sensitive else ""
    )
    sensitive_sections = (
        _render_users_section(users, csrf_token) + _render_jobs_section(jobs)
        if show_sensitive
        else ""
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
    .architecture-overview-head {{
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      gap: 18px;
      padding: 4px 0 16px;
    }}
    .architecture-subsection {{
      min-width: 0;
      margin-bottom: 20px;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: #fff;
    }}
    .architecture-subsection-head {{
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      gap: 16px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
    }}
    .architecture-subsection-head h3 {{ margin: 0; font-size: 16px; }}
    .architecture-toolbar {{
      min-width: 0;
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      gap: 14px;
      margin-bottom: 2px;
    }}
    .architecture-status-content {{
      min-width: 0;
      padding: 0 16px 4px;
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
    .architecture-graph-layout {{ min-width: 0; background: #fff; }}
    .architecture-diagram-scroll {{
      min-width: 0;
      overflow-x: auto;
      background: #f8fafc;
      scrollbar-gutter: stable;
    }}
    .architecture-diagram {{
      position: relative;
      isolation: isolate;
      width: 100%;
      min-width: 1120px;
      padding: 18px;
      background: #f8fafc;
    }}
    .architecture-flow-svg {{
      position: absolute;
      inset: 0;
      z-index: 5;
      width: 100%;
      height: 100%;
      overflow: visible;
      pointer-events: none;
    }}
    .architecture-edge {{
      fill: none;
      stroke-width: 1.5;
      stroke-linecap: round;
      stroke-linejoin: round;
      vector-effect: non-scaling-stroke;
    }}
    .architecture-edge--runtime {{ stroke: #64748b; }}
    .architecture-edge--writing {{ stroke: #0f766e; }}
    .architecture-edge--review {{ stroke: #2563eb; }}
    .architecture-edge--flow {{
      stroke-width: 2.5;
      stroke-dasharray: 9 13;
      animation: architecture-information-flow 1.8s linear infinite;
    }}
    .architecture-edge--flow-runtime {{ stroke: #64748b; }}
    .architecture-edge--flow-writing {{ stroke: #0f766e; }}
    .architecture-edge--flow-review {{ stroke: #2563eb; }}
    .architecture-diagram.is-motion-paused .architecture-edge--flow {{
      animation-play-state: paused;
    }}
    @keyframes architecture-information-flow {{
      to {{ stroke-dashoffset: -44; }}
    }}
    .architecture-plane {{
      position: relative;
      z-index: 1;
      border: 1px solid #cbd5e1;
      border-radius: 7px;
      background: rgba(255, 255, 255, 0.9);
    }}
    .architecture-plane + .architecture-plane {{ margin-top: 14px; }}
    .architecture-plane--runtime {{ padding: 14px 16px 16px; border-color: #a8c0ef; }}
    .architecture-plane--governance {{ padding: 14px 16px 16px; background: #f5f7fa; }}
    .architecture-plane-heading {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 18px;
      margin-bottom: 14px;
    }}
    .architecture-plane-title {{
      margin: 0;
      color: #344054;
      font-size: 12px;
      font-weight: 750;
    }}
    .architecture-plane-caption {{ margin: 3px 0 0; color: #667085; font-size: 10px; }}
    .architecture-plane-badges {{ display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px; }}
    .architecture-plane-badges span {{
      padding: 3px 7px;
      border: 1px solid #cbd5e1;
      border-radius: 4px;
      background: #fff;
      color: #475467;
      font-size: 10px;
      font-weight: 700;
    }}
    .architecture-main-flow {{
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 26px;
      padding: 4px 40px 8px;
    }}
    .architecture-flow-column {{
      display: flex;
      width: min(240px, 100%);
      min-width: 0;
      flex-direction: column;
      gap: 8px;
    }}
    .architecture-phase {{
      display: block;
      color: #667085;
      font-size: 9px;
      font-weight: 800;
      text-align: center;
      text-transform: uppercase;
    }}
    .architecture-agent-core {{
      position: relative;
      z-index: 3;
      width: min(560px, 100%);
      min-width: 0;
      padding: 12px;
      border: 1px solid #9eb4df;
      border-radius: 7px;
      background: #f3f6fc;
    }}
    .architecture-agent-core-head {{
      display: grid;
      grid-template-columns: auto 1fr;
      column-gap: 8px;
      margin-bottom: 10px;
    }}
    .architecture-agent-core-head strong {{ font-size: 12px; }}
    .architecture-agent-core-head span {{ color: #31589b; font-size: 10px; font-weight: 800; }}
    .architecture-agent-core-head small {{ grid-column: 2; color: #667085; font-size: 9px; }}
    .architecture-platform-stack {{ display: grid; gap: 9px; }}
    .architecture-platform-stage {{ position: relative; }}
    .architecture-capability-domains {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
      width: min(920px, 100%);
    }}
    .architecture-domain-card {{
      position: relative;
      z-index: 3;
      min-width: 0;
      padding: 10px;
      border: 1px solid;
      border-radius: 7px;
    }}
    .architecture-domain-card--writing {{ background: #ecfdf3; border-color: #66a983; }}
    .architecture-domain-card--review {{ background: #eff6ff; border-color: #7da0e6; }}
    .architecture-domain-index {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 7px;
      color: #667085;
      font-size: 9px;
      font-weight: 800;
    }}
    .architecture-domain-capabilities {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 6px;
      margin-top: 8px;
    }}
    .architecture-control-gates {{
      display: grid;
      grid-template-columns: 134px repeat(5, minmax(0, 1fr));
      margin-top: 14px;
      border-radius: 6px;
      background: #17202a;
      color: #fff;
      overflow: hidden;
    }}
    .architecture-control-gates-title {{
      display: flex;
      flex-direction: column;
      justify-content: center;
      padding: 10px 12px;
      color: #e2e8f0;
      font-size: 10px;
      font-weight: 800;
    }}
    .architecture-control-gates-title small {{ margin-top: 3px; color: #98a2b3; font-size: 9px; font-weight: 500; }}
    .architecture-control-gate {{ padding: 10px 11px; border-left: 1px solid #344054; }}
    .architecture-control-gate strong {{ display: block; font-size: 10px; }}
    .architecture-control-gate span {{ display: block; margin-top: 3px; color: #b9c2d0; font-size: 9px; line-height: 1.4; }}
    .architecture-foundation {{
      display: grid;
      grid-template-columns: 150px repeat(2, minmax(0, 1fr));
      gap: 12px;
      align-items: stretch;
      margin-top: 14px;
      padding-top: 14px;
      border-top: 1px solid #d9dee7;
    }}
    .architecture-foundation-intro {{ display: flex; flex-direction: column; justify-content: center; }}
    .architecture-foundation-intro strong {{ font-size: 11px; }}
    .architecture-foundation-intro span {{ margin-top: 4px; color: #667085; font-size: 9px; line-height: 1.45; }}
    .architecture-foundation-group {{
      min-width: 0;
      padding: 10px;
      border: 1px solid #d9dee7;
      border-radius: 7px;
      background: #fff;
    }}
    .architecture-foundation-group--tools {{ background: #fffdf2; border-color: #ded3a2; }}
    .architecture-foundation-group--knowledge {{ background: #fdf7fb; border-color: #dfb7ca; }}
    .architecture-foundation-label {{ display: block; margin-bottom: 7px; color: #475467; font-size: 9px; font-weight: 800; }}
    .architecture-foundation-nodes {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; }}
    .architecture-governance-grid {{ display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; }}
    .architecture-node {{
      position: relative;
      z-index: 6;
      display: block;
      width: 100%;
      min-width: 0;
      min-height: 48px;
      padding: 9px 10px;
      border: 1px solid #98a2b3;
      border-radius: 6px;
      background: #fff;
      color: #17202a;
      text-align: left;
      letter-spacing: 0;
      cursor: pointer;
      transition: border-color 150ms ease, box-shadow 150ms ease, transform 150ms ease;
    }}
    .architecture-node:hover {{ border-color: #475467; transform: translateY(-1px); }}
    .architecture-node:focus-visible {{ outline: 2px solid #2563eb; outline-offset: 2px; }}
    .architecture-node.is-active {{ border-color: #2563eb; box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.14); }}
    .architecture-node[data-architecture-group="entry"] {{ background: #fff7ed; border-color: #b45309; }}
    .architecture-node[data-architecture-group="platform"] {{ background: #eff6ff; border-color: #5b82d6; }}
    .architecture-domain-card--writing .architecture-node {{ background: #dff7e9; border-color: #0f766e; }}
    .architecture-domain-card--review .architecture-node {{ background: #dceafe; border-color: #2563eb; }}
    .architecture-node[data-architecture-group="services"] {{ background: #fefce8; border-color: #ad8c2a; }}
    .architecture-node[data-architecture-group="knowledge"] {{ background: #fdf2f8; border-color: #be4b7d; }}
    .architecture-node[data-architecture-group="governance"] {{ background: #f2f4f7; border-color: #7f8b9e; }}
    .architecture-node--main {{ min-height: 74px; text-align: center; }}
    .architecture-node--stage {{ min-height: 58px; padding: 8px 9px; }}
    .architecture-node--domain {{ min-height: 52px; }}
    .architecture-node--child {{ min-height: 38px; padding: 7px 8px; }}
    .architecture-node--micro {{ min-height: 36px; padding: 6px 7px; }}
    .architecture-node--governance {{ min-height: 62px; padding: 8px 9px; }}
    .architecture-node-name {{ display: block; font-size: 12px; font-weight: 750; line-height: 1.35; }}
    .architecture-node--child .architecture-node-name,
    .architecture-node--micro .architecture-node-name,
    .architecture-node--governance .architecture-node-name {{ font-size: 10px; }}
    .architecture-node-note {{ display: block; margin-top: 4px; color: #667085; font-size: 10px; line-height: 1.35; }}
    .architecture-detail {{
      display: grid;
      grid-template-columns: 170px minmax(240px, 1fr) minmax(240px, 1fr);
      gap: 18px;
      padding: 14px 16px;
      border-top: 1px solid var(--line);
      background: #fff;
    }}
    .architecture-detail-kicker {{ color: var(--muted); font-size: 11px; font-weight: 700; }}
    .architecture-detail h3 {{ margin: 6px 0 0; font-size: 17px; }}
    .architecture-detail p {{ margin: 10px 0 0; color: #344054; font-size: 13px; }}
    .architecture-detail-row {{ margin-top: 0; }}
    .architecture-detail-label {{ display: block; margin-bottom: 4px; color: var(--muted); font-size: 11px; }}
    .architecture-detail-value {{ color: #344054; font-size: 12px; word-break: break-word; }}
    .architecture-detail-value + .architecture-detail-label {{ margin-top: 8px; }}
    .architecture-graph-meta {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 14px;
      border-top: 1px solid var(--line);
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
    .architecture-layer-key .architecture-group-entry {{ background: #fff7ed; border-color: #b45309; }}
    .architecture-layer-key .architecture-group-platform {{ background: #eff6ff; border-color: #2563eb; }}
    .architecture-layer-key .architecture-group-capabilities {{ background: #ecfdf3; border-color: #0f8a56; }}
    .architecture-layer-key .architecture-group-services {{ background: #fefce8; border-color: #a16207; }}
    .architecture-layer-key .architecture-group-knowledge {{ background: #fdf2f8; border-color: #be185d; }}
    .architecture-layer-key .architecture-group-governance {{ background: #f2f4f7; border-color: #667085; }}
    .architecture-layer-key .architecture-flow-key--main {{ background: #f8fafc; border-color: #64748b; }}
    .architecture-layer-key .architecture-flow-key--writing {{ background: #ecfdf3; border-color: #0f766e; }}
    .architecture-layer-key .architecture-flow-key--review {{ background: #eff6ff; border-color: #2563eb; }}
    .architecture-motion-toggle {{
      width: 34px;
      height: 34px;
      padding: 0;
      font-size: 14px;
      font-weight: 750;
    }}
    @media (prefers-reduced-motion: reduce) {{
      .architecture-edge--flow {{ animation: none; stroke-dasharray: none; }}
      .architecture-node {{ transition: none; }}
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
    .execution-indicator {{
      display: inline-flex;
      align-items: center;
      margin-top: 7px;
      margin-right: 6px;
      padding: 3px 7px;
      border: 1px solid var(--line);
      border-radius: 4px;
      font-size: 11px;
      font-weight: 700;
      background: #fff;
    }}
    .execution-indicator.persistent {{ color: var(--ok); border-color: #a6d5b5; background: #f2fbf5; }}
    .execution-indicator.realtime {{ color: #475467; background: #f9fafb; }}
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
    button.danger {{
      border-color: #f0b4ae;
      color: var(--danger);
    }}
    .action-cluster {{ display: flex; flex-wrap: wrap; gap: 6px; }}
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
      .architecture-overview-head, .architecture-toolbar, .architecture-subsection-head {{ display: block; }}
      .architecture-status-content {{ padding: 0 12px 2px; }}
      .architecture-filters {{ margin-top: 12px; width: 100%; max-width: 100%; }}
      .architecture-detail {{ grid-template-columns: 1fr; gap: 10px; }}
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
    <a href="#todos">待办</a>
    <a href="#runtime">运行</a>
    <a href="#delivery-recovery">交付恢复</a>
    <a href="#changes">更新</a>
    <a href="#skills">Skills</a>
    {sensitive_nav}
  </nav>
  <main>
    {_render_architecture_section(overview)}
    {_render_modules_section(overview)}
    {_render_todos_section(overview)}
    {_render_runtime_section(overview)}
    {_render_delivery_recovery_section(delivery_recoveries, delivery_recovery_error, csrf_token)}
    {_render_changes_section(overview)}
    {_render_skills_section(skills, csrf_token)}
    <div class="sensitive-access">{sensitive_toggle}</div>
    {sensitive_sections}
  </main>
  <script>
    (() => {{
      const filters = Array.from(document.querySelectorAll("[data-component-filter]"));
      const listNodes = Array.from(document.querySelectorAll("[data-component-status]"));
      const groups = Array.from(document.querySelectorAll("[data-component-group]"));
      const diagram = document.getElementById("architecture-diagram");
      const flowSvg = document.getElementById("architecture-flow-svg");
      const graphDataElement = document.getElementById("architecture-graph-data");
      const motionToggle = document.getElementById("architecture-motion-toggle");
      const architectureNodeElements = Array.from(document.querySelectorAll("[data-architecture-node]"));
      let graphRecords = [];
      let graphEdges = [];
      const svgNamespace = "http://www.w3.org/2000/svg";
      const primaryPairStyles = new Map([
        ["business_entry>platform_access", "runtime"],
        ["platform_access>platform_orchestration", "runtime"],
        ["platform_orchestration>agent_runtime", "runtime"],
        ["agent_runtime>writing_domain", "writing"],
        ["agent_runtime>review_domain", "review"],
        ["writing_domain>result_delivery", "writing"],
        ["review_domain>result_delivery", "review"],
      ]);
      const branchPairStyles = new Set([
        "agent_runtime>writing_domain",
        "agent_runtime>review_domain",
        "writing_domain>result_delivery",
        "review_domain>result_delivery",
      ]);

      const roundedOrthogonalPath = (start, end, orientation, laneOffset = 0) => {{
        const radius = 9;
        if (orientation === "horizontal") {{
          if (Math.abs(start.y - end.y) < 2) return `M ${{start.x}} ${{start.y}} H ${{end.x}}`;
          const horizontalDirection = end.x >= start.x ? 1 : -1;
          const verticalDirection = end.y >= start.y ? 1 : -1;
          const middleX = (start.x + end.x) / 2 + laneOffset;
          const usableRadius = Math.min(radius, Math.abs(end.y - start.y) / 2, Math.abs(middleX - start.x) / 2);
          return [
            `M ${{start.x}} ${{start.y}}`,
            `H ${{middleX - horizontalDirection * usableRadius}}`,
            `Q ${{middleX}} ${{start.y}} ${{middleX}} ${{start.y + verticalDirection * usableRadius}}`,
            `V ${{end.y - verticalDirection * usableRadius}}`,
            `Q ${{middleX}} ${{end.y}} ${{middleX + horizontalDirection * usableRadius}} ${{end.y}}`,
            `H ${{end.x}}`,
          ].join(" ");
        }}
        if (Math.abs(start.x - end.x) < 2) return `M ${{start.x}} ${{start.y}} V ${{end.y}}`;
        const verticalDirection = end.y >= start.y ? 1 : -1;
        const horizontalDirection = end.x >= start.x ? 1 : -1;
        const middleY = (start.y + end.y) / 2 + laneOffset;
        const usableRadius = Math.min(radius, Math.abs(end.x - start.x) / 2, Math.abs(middleY - start.y) / 2);
        return [
          `M ${{start.x}} ${{start.y}}`,
          `V ${{middleY - verticalDirection * usableRadius}}`,
          `Q ${{start.x}} ${{middleY}} ${{start.x + horizontalDirection * usableRadius}} ${{middleY}}`,
          `H ${{end.x - horizontalDirection * usableRadius}}`,
          `Q ${{end.x}} ${{middleY}} ${{end.x}} ${{middleY + verticalDirection * usableRadius}}`,
          `V ${{end.y}}`,
        ].join(" ");
      }};

      const nodeBounds = (nodeId) => {{
        const element =
          document.querySelector(`[data-architecture-edge-box="${{nodeId}}"]`) ||
          document.querySelector(`[data-architecture-node="${{nodeId}}"]`);
        if (!element || !diagram) return null;
        const elementRect = element.getBoundingClientRect();
        const diagramRect = diagram.getBoundingClientRect();
        return {{
          left: elementRect.left - diagramRect.left,
          right: elementRect.right - diagramRect.left,
          top: elementRect.top - diagramRect.top,
          bottom: elementRect.bottom - diagramRect.top,
          centerX: elementRect.left - diagramRect.left + elementRect.width / 2,
          centerY: elementRect.top - diagramRect.top + elementRect.height / 2,
        }};
      }};

      const relationPath = (relation) => {{
        const source = nodeBounds(relation.source_id);
        const target = nodeBounds(relation.target_id);
        if (!source || !target) return "";
        const pair = relation.source_id + ">" + relation.target_id;
        const start = {{ x: source.centerX, y: source.bottom }};
        const end = {{ x: target.centerX, y: target.top }};
        if (branchPairStyles.has(pair)) {{
          return roundedOrthogonalPath(start, end, "vertical");
        }}
        if (Math.abs(start.x - end.x) < 2) return `M ${{start.x}} ${{start.y}} V ${{end.y}}`;
        return roundedOrthogonalPath(start, end, "vertical");
      }};

      const appendEdge = (relation, style) => {{
        if (!flowSvg) return;
        const pathData = relationPath(relation);
        if (!pathData) return;
        const path = document.createElementNS(svgNamespace, "path");
        path.setAttribute("d", pathData);
        path.setAttribute("class", `architecture-edge architecture-edge--${{style}}`);
        path.setAttribute("marker-end", `url(#architecture-arrow-${{style}})`);
        const title = document.createElementNS(svgNamespace, "title");
        title.textContent = relation.label;
        path.appendChild(title);
        flowSvg.appendChild(path);
        const movingPath = document.createElementNS(svgNamespace, "path");
        movingPath.setAttribute("d", pathData);
        movingPath.setAttribute(
          "class",
          `architecture-edge architecture-edge--flow architecture-edge--flow-${{style}}`,
        );
        flowSvg.appendChild(movingPath);
      }};

      const drawArchitectureEdges = () => {{
        if (!diagram || !flowSvg) return;
        flowSvg.querySelectorAll(".architecture-edge").forEach((edge) => edge.remove());
        flowSvg.setAttribute("viewBox", `0 0 ${{diagram.clientWidth}} ${{diagram.clientHeight}}`);
        graphEdges.forEach((relation) => {{
          const pair = relation.source_id + ">" + relation.target_id;
          const style = primaryPairStyles.get(pair);
          if (style) appendEdge(relation, style);
        }});
      }};

      const showNodeDetail = (nodeId) => {{
        const record = graphRecords.find((item) => item.id === nodeId);
        if (!record) return;
        document.getElementById("architecture-detail-plane").textContent = record.plane_name;
        document.getElementById("architecture-detail-name").textContent = record.name;
        document.getElementById("architecture-detail-description").textContent = record.description;
        document.getElementById("architecture-detail-group").textContent = record.group_name;
        document.getElementById("architecture-detail-evidence").textContent = record.evidence;
        for (const element of architectureNodeElements) {{
          element.classList.toggle("is-active", element.dataset.architectureNode === nodeId);
        }}
      }};
      const initializeArchitecture = () => {{
        if (!graphDataElement) return;
        const graphData = JSON.parse(graphDataElement.textContent || "{{}}");
        graphRecords = Array.isArray(graphData.nodes) ? graphData.nodes : [];
        graphEdges = Array.isArray(graphData.edges) ? graphData.edges : [];
        for (const element of architectureNodeElements) {{
          element.addEventListener("click", () => showNodeDetail(element.dataset.architectureNode));
        }}
        if (graphRecords.length) showNodeDetail("business_entry");
        window.requestAnimationFrame(drawArchitectureEdges);
        if (window.ResizeObserver && diagram) {{
          new ResizeObserver(() => window.requestAnimationFrame(drawArchitectureEdges)).observe(diagram);
        }} else {{
          window.addEventListener("resize", drawArchitectureEdges);
        }}
      }};

      motionToggle.addEventListener("click", () => {{
        const paused = diagram.classList.toggle("is-motion-paused");
        motionToggle.textContent = paused ? "▶" : "Ⅱ";
        motionToggle.setAttribute("aria-pressed", paused ? "true" : "false");
        motionToggle.setAttribute("aria-label", paused ? "继续信息流动效" : "暂停信息流动效");
        motionToggle.setAttribute("title", paused ? "继续信息流动效" : "暂停信息流动效");
      }});
      for (const filter of filters) {{
        filter.addEventListener("click", () => {{
          const selectedStatus = filter.dataset.componentFilter;
          for (const item of filters) item.classList.toggle("is-active", item === filter);
          for (const node of listNodes) {{
            node.hidden = selectedStatus !== "all" && node.dataset.componentStatus !== selectedStatus;
          }}
          for (const group of groups) {{
            group.hidden = !Array.from(group.querySelectorAll("[data-component-status]")).some(node => !node.hidden);
          }}
        }});
      }}
      initializeArchitecture();
    }})();
  </script>
</body>
</html>"""


def create_handler(
    paths: AdminPaths = DEFAULT_PATHS,
    *,
    recovery_service_factory: Callable[[], DeliveryRecoveryService] | None = None,
    csrf_token: str | None = None,
) -> type[BaseHTTPRequestHandler]:
    recovery_factory = recovery_service_factory or (
        lambda: build_delivery_recovery_service(project_root=PROJECT_ROOT)
    )
    expected_csrf_token = csrf_token or secrets.token_urlsafe(32)

    class AdminHandler(BaseHTTPRequestHandler):
        server_version = "MAgentAdmin/0.1"

        def do_GET(self) -> None:
            request = urlsplit(self.path)
            if request.path not in {"/", "/index.html"}:
                self._send_text("Not found", HTTPStatus.NOT_FOUND)
                return
            query = parse_qs(request.query, keep_blank_values=True)
            show_sensitive = query.get("show_sensitive") == ["1"]
            recovery_error = False
            try:
                recoveries = recovery_factory().list_pending()
            except Exception:  # noqa: BLE001
                recoveries = ()
                recovery_error = True
            self._send_html(
                render_dashboard(
                    paths,
                    show_sensitive=show_sensitive,
                    delivery_recoveries=recoveries,
                    delivery_recovery_error=recovery_error,
                    csrf_token=expected_csrf_token,
                )
            )

        def do_POST(self) -> None:
            handlers: dict[str, Callable[[dict[str, list[str]]], None]] = {
                "/skills/toggle": self._handle_skill_toggle,
                "/users/update": self._handle_user_update,
                "/delivery/recover": self._handle_delivery_recovery,
            }
            handler = handlers.get(self.path)
            if handler is None:
                self._send_text("Not found", HTTPStatus.NOT_FOUND)
                return

            try:
                form = self._read_form()
                _require_csrf(form, expected_csrf_token)
                handler(form)
            except Exception as exc:  # noqa: BLE001
                self._send_text(f"操作失败：{exc}", HTTPStatus.BAD_REQUEST)
                return
            self.send_response(HTTPStatus.SEE_OTHER)
            if self.path == "/users/update":
                redirect = "/?show_sensitive=1#users"
            elif self.path == "/delivery/recover":
                redirect = "/#delivery-recovery"
            else:
                redirect = "/"
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

        def _handle_delivery_recovery(self, form: dict[str, list[str]]) -> None:
            task_id = _one(form, "task_id").strip()
            action = _one(form, "action").strip()
            if not task_id:
                raise ValueError("处理编号不能为空")
            if action not in {"retry", "confirm-delivered", "close"}:
                raise ValueError("不支持的交付恢复操作")
            confirm_unknown = form.get("confirm_unknown_not_delivered") == ["true"]
            recovery_factory().recover(
                task_id,
                action=action,
                confirm_unknown_not_delivered=confirm_unknown,
                operator="admin-console",
            )

        def _read_form(self) -> dict[str, list[str]]:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 16 * 1024:
                raise ValueError("请求大小无效")
            body = self.rfile.read(length).decode("utf-8")
            return parse_qs(body, keep_blank_values=True)

        def _send_html(self, html: str) -> None:
            data = html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "form-action 'self'; frame-ancestors 'none'",
            )
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_text(self, text: str, status: HTTPStatus) -> None:
            data = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
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
    counts = overview.component_status_counts
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
        f'data-component-filter="{escape(status)}">{escape(label)} {count}</button>'
        for status, label, count in filters
    )
    groups = "\n".join(
        _render_component_group(group) for group in overview.component_groups
    )
    group_key = (
        '<span class="architecture-flow-key--main">通用主流程</span>'
        '<span class="architecture-flow-key--writing">写作路线</span>'
        '<span class="architecture-flow-key--review">审核路线</span>'
    )
    diagram = _render_architecture_diagram(overview)
    graph_data = _architecture_graph_json(overview)
    return f"""<section id="architecture" class="architecture-section">
  <div class="architecture-overview-head">
    <div>
      <h2>项目总览</h2>
      <p class="hint">上半部分展示受控 Agent 的完整运行结构、约束和治理；下半部分逐项展示功能与模块状态。</p>
    </div>
  </div>
  <div class="architecture-subsection">
    <div class="architecture-subsection-head">
      <div>
        <h3>受控 Agent 运行架构</h3>
        <p class="hint">展示一次任务如何经过接入、理解、编排、授权执行、业务处理、质量约束和确认交付。</p>
      </div>
      <button type="button" id="architecture-motion-toggle" class="architecture-motion-toggle" aria-label="暂停信息流动效" title="暂停信息流动效" aria-pressed="false">Ⅱ</button>
    </div>
    <div class="architecture-graph-layout">
      <div class="architecture-diagram-scroll">{diagram}</div>
      <aside class="architecture-detail" aria-live="polite">
        <div>
          <div class="architecture-detail-kicker" id="architecture-detail-plane">节点详情</div>
          <h3 id="architecture-detail-name">点击节点查看详情</h3>
        </div>
        <p id="architecture-detail-description">主链表示请求和结果的传递方向，写作与审核使用不同颜色。</p>
        <div class="architecture-detail-row">
          <span class="architecture-detail-label">架构分区</span>
          <div class="architecture-detail-value" id="architecture-detail-group">-</div>
          <span class="architecture-detail-label">事实依据</span>
          <div class="architecture-detail-value" id="architecture-detail-evidence">-</div>
        </div>
      </aside>
    </div>
    <div class="architecture-graph-meta">
      <div class="architecture-layer-key">{group_key}</div>
      <span>动态线只表示主干信息流；工具、知识和治理通过固定分区表达。</span>
    </div>
  </div>
  <div class="architecture-subsection">
    <div class="architecture-subsection-head architecture-toolbar">
      <div>
        <h3>功能与模块状态</h3>
        <p class="hint">建设成熟度、Bot 在线状态和持久队列迁移分别显示，数据来自代码、任务注册、心跳和待办。</p>
      </div>
      <div class="architecture-filters" role="group" aria-label="按建设状态筛选功能">
        {filter_html}
      </div>
    </div>
    <div class="architecture-status-content">
      <div class="architecture-flow">{groups}</div>
    </div>
  </div>
  <script id="architecture-graph-data" type="application/json">{graph_data}</script>
</section>"""


def _render_architecture_diagram(overview: ProjectOverview) -> str:
    nodes = {node.id: node for node in overview.architecture_nodes}

    def node_button(node_id: str, modifier: str = "", note: str = "") -> str:
        node = nodes[node_id]
        classes = "architecture-node"
        if modifier:
            classes += f" architecture-node--{modifier}"
        note_html = (
            f'<span class="architecture-node-note">{escape(note)}</span>' if note else ""
        )
        return (
            f'<button type="button" class="{classes}" '
            f'data-architecture-node="{escape(node.id)}" '
            f'data-architecture-group="{escape(node.group)}" '
            f'aria-label="查看{escape(node.name)}详情">'
            f'<span class="architecture-node-name">{escape(node.name)}</span>'
            f"{note_html}</button>"
        )

    return f"""<div id="architecture-diagram" class="architecture-diagram" aria-label="M-Agent 业务运行与管理治理架构图">
  <svg id="architecture-flow-svg" class="architecture-flow-svg" aria-hidden="true">
    <defs>
      <marker id="architecture-arrow-runtime" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="strokeWidth">
        <path d="M 0 0 L 8 4 L 0 8 Z" fill="#64748b"></path>
      </marker>
      <marker id="architecture-arrow-writing" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="strokeWidth">
        <path d="M 0 0 L 8 4 L 0 8 Z" fill="#0f766e"></path>
      </marker>
      <marker id="architecture-arrow-review" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="strokeWidth">
        <path d="M 0 0 L 8 4 L 0 8 Z" fill="#2563eb"></path>
      </marker>
    </defs>
  </svg>
  <div class="architecture-plane architecture-plane--runtime">
    <div class="architecture-plane-heading">
      <div>
        <p class="architecture-plane-title">业务运行面</p>
        <p class="architecture-plane-caption">从请求进入到结果交付，每一步都有明确职责、状态和安全边界。</p>
      </div>
      <div class="architecture-plane-badges" aria-label="架构特征">
        <span>受控能力范围</span><span>任务级隔离</span><span>可恢复执行</span><span>可追溯交付</span>
      </div>
    </div>
    <div class="architecture-main-flow architecture-main-flow--vertical">
      <div class="architecture-flow-column architecture-flow-column--entry">
        <span class="architecture-phase">01 · 请求入口</span>
        {node_button("business_entry", "main", "企业微信 / 本地入口")}
      </div>
      <div class="architecture-agent-core">
        <div class="architecture-agent-core-head">
          <span>02-04</span>
          <strong>受控 Agent 内核</strong>
          <small>理解需求、确定任务关系、授权并可靠执行</small>
        </div>
        <div class="architecture-platform-stack">
          <div class="architecture-platform-stage">
            {node_button("platform_access", "stage", "消息标准化 · 身份映射 · 权限校验")}
          </div>
          <div class="architecture-platform-stage">
            {node_button("platform_orchestration", "stage", "意图路由 · 多任务关系 · 材料组装 · 持久队列")}
          </div>
          <div class="architecture-platform-stage">
            {node_button("agent_runtime", "stage", "Pydantic AI · Skill 合约 · ToolGateway · 结构化输出")}
          </div>
        </div>
      </div>
      <div class="architecture-capability-domains">
        <div class="architecture-domain-card architecture-domain-card--writing" data-architecture-edge-box="writing_domain">
          <div class="architecture-domain-index"><span>05A · 写作域</span><span>Skill 驱动</span></div>
          {node_button("writing_domain", "domain", "成稿、改稿与专题内容生产")}
          <div class="architecture-domain-capabilities">
            {node_button("direct_report", "micro")}
            {node_button("brief_writing", "micro")}
            {node_button("rewrite", "micro")}
            {node_button("thematic_content", "micro")}
          </div>
        </div>
        <div class="architecture-domain-card architecture-domain-card--review" data-architecture-edge-box="review_domain">
          <div class="architecture-domain-index"><span>05B · 审核域</span><span>规则与证据</span></div>
          {node_button("review_domain", "domain", "通用、专项、格式与跨文件审核")}
          <div class="architecture-domain-capabilities">
            {node_button("general_review", "micro")}
            {node_button("special_review", "micro")}
            {node_button("format_review", "micro")}
            {node_button("multi_file_review", "micro")}
          </div>
        </div>
      </div>
      <div class="architecture-flow-column architecture-flow-column--delivery">
        <span class="architecture-phase">06 · 结果交付</span>
        {node_button("result_delivery", "main", "文字 / 附件 / 三态回执")}
      </div>
    </div>
    <div class="architecture-control-gates" aria-label="贯穿任务全程的五道运行约束">
      <div class="architecture-control-gates-title">全程运行约束<small>不是只靠 Prompt</small></div>
      <div class="architecture-control-gate"><strong>01 · 身份隔离</strong><span>用户、会话、任务和文件按入口隔离</span></div>
      <div class="architecture-control-gate"><strong>02 · 工具白名单</strong><span>Skill 只能调用配置中声明的受限工具</span></div>
      <div class="architecture-control-gate"><strong>03 · 结构化输出</strong><span>Pydantic 合约、规则校验与原文证据</span></div>
      <div class="architecture-control-gate"><strong>04 · 幂等与恢复</strong><span>队列、租约、fencing token 和检查点</span></div>
      <div class="architecture-control-gate"><strong>05 · 交付确认</strong><span>已送达、未送达、送达未知与运维告警</span></div>
    </div>
    <div class="architecture-foundation">
      <div class="architecture-foundation-intro">
        <strong>共享执行底座</strong>
        <span>工具和知识不直接暴露给用户，只通过授权工作流调用。</span>
      </div>
      <div class="architecture-foundation-group architecture-foundation-group--tools">
        <span class="architecture-foundation-label">受限工具层</span>
        <div class="architecture-foundation-nodes">
          {node_button("document_service", "child")}
          {node_button("web_retrieval", "child")}
        </div>
      </div>
      <div class="architecture-foundation-group architecture-foundation-group--knowledge">
        <span class="architecture-foundation-label">可信知识层</span>
        <div class="architecture-foundation-nodes">
          {node_button("policy_knowledge", "child")}
          {node_button("bank_knowledge", "child")}
        </div>
      </div>
    </div>
  </div>
  <div class="architecture-plane architecture-plane--governance">
    <div class="architecture-plane-heading">
      <div>
        <p class="architecture-plane-title">管理与治理面</p>
        <p class="architecture-plane-caption">不参与业务内容生成，负责观察、约束、维护和持续交付。</p>
      </div>
    </div>
    <div class="architecture-governance-grid">
      {node_button("admin_console", "governance")}
      {node_button("ops_observability", "governance")}
      {node_button("data_governance", "governance")}
      {node_button("engineering_governance", "governance")}
      {node_button("knowledge_governance", "governance")}
    </div>
  </div>
</div>"""


def _architecture_graph_json(overview: ProjectOverview) -> str:
    nodes = [
        {
            "id": node.id,
            "name": node.name,
            "description": node.description,
            "plane": node.plane,
            "plane_name": node.plane_name,
            "group": node.group,
            "group_name": node.group_name,
            "evidence": node.evidence,
            "x": node.x,
            "y": node.y,
        }
        for node in overview.architecture_nodes
    ]
    edges = [
        {
            "source_id": relation.source_id,
            "target_id": relation.target_id,
            "label": relation.label,
            "relation_type": relation.relation_type,
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


def _render_component_group(group: object) -> str:
    capabilities = "\n".join(
        _render_capability_node(capability) for capability in getattr(group, "capabilities")
    )
    return f"""<div class="architecture-layer" data-component-group="{escape(str(getattr(group, "key")))}">
  <div class="layer-heading">
    <span class="layer-order">{escape(str(getattr(group, "order")))}</span>
    <h3>{escape(str(getattr(group, "name")))}</h3>
    <p>{escape(str(getattr(group, "description")))}</p>
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
    execution_mode = str(getattr(capability, "execution_mode"))
    execution_mode_label = str(getattr(capability, "execution_mode_label"))
    execution_html = ""
    if execution_mode and execution_mode_label:
        execution_html = (
            f'<div class="execution-indicator {escape(execution_mode)}">'
            f'{escape(execution_mode_label)}</div>'
        )
    todo_id = str(getattr(capability, "todo_id"))
    next_action = str(getattr(capability, "next_action"))
    next_html = ""
    if todo_id and next_action:
        next_html = (
            f'<div class="capability-next"><strong>{escape(todo_id)}</strong>：{escape(next_action)}</div>'
        )
    return f"""<article class="capability-node capability-state-{escape(status)}" data-component-status="{escape(status)}" data-execution-mode="{escape(execution_mode)}">
  <div class="capability-node-head">
    <h4>{escape(str(getattr(capability, "name")))}</h4>
    <span class="capability-status {escape(status)}">{escape(str(getattr(capability, "status_label")))}</span>
  </div>
  <p class="capability-description">{escape(str(getattr(capability, "description")))}</p>
  {runtime_html}
  {execution_html}
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


def _render_delivery_recovery_section(
    candidates: tuple[DeliveryRecoveryCandidate, ...],
    load_failed: bool,
    csrf_token: str,
) -> str:
    if load_failed:
        body = '<div class="empty">交付状态读取失败，请查看管理台服务日志。</div>'
    elif not candidates:
        body = '<div class="empty">当前没有需要人工处理的交付任务。</div>'
    else:
        rows = "\n".join(
            _render_delivery_recovery_row(candidate, csrf_token)
            for candidate in candidates
        )
        body = f"""<table>
  <thead>
    <tr>
      <th style="width: 18%">处理编号</th>
      <th style="width: 12%">来源</th>
      <th style="width: 16%">交付状态</th>
      <th style="width: 18%">更新时间</th>
      <th>人工操作</th>
    </tr>
  </thead>
  <tbody>{rows}</tbody>
</table>"""
    return f"""<section id="delivery-recovery">
  <div class="section-head">
    <div>
      <h2>交付恢复</h2>
      <p class="hint">只处理已经生成、但企业微信交付失败或状态未知的结果；不会重新调用模型。送达未知必须先人工核实。</p>
    </div>
  </div>
  {body}
</section>"""


def _render_delivery_recovery_row(
    candidate: DeliveryRecoveryCandidate,
    csrf_token: str,
) -> str:
    status_labels = {
        "confirmed_not_delivered": "明确未送达",
        "delivery_unknown": "送达未知",
    }
    source_labels = {"writing": "写作", "review": "审核"}
    status = status_labels.get(candidate.delivery_status, candidate.delivery_status)
    source = source_labels.get(candidate.source, candidate.source)
    hidden = (
        _csrf_input(csrf_token)
        + f'<input type="hidden" name="task_id" value="{escape(candidate.task_id)}">'
    )
    if candidate.delivery_status == "confirmed_not_delivered":
        actions = f"""<form class="inline" method="post" action="/delivery/recover" onsubmit="return confirm('确认按原结果重新发送？系统不会重新生成内容。')">
  {hidden}<input type="hidden" name="action" value="retry">
  <button class="primary" type="submit">重新发送</button>
</form>"""
    else:
        actions = f"""<div class="action-cluster">
<form class="inline" method="post" action="/delivery/recover" onsubmit="return confirm('已向用户核实确实没有收到结果，并重新发送？')">
  {hidden}<input type="hidden" name="action" value="retry"><input type="hidden" name="confirm_unknown_not_delivered" value="true">
  <button class="primary" type="submit">确认未收到并重发</button>
</form>
<form class="inline" method="post" action="/delivery/recover" onsubmit="return confirm('确认用户实际已经收到结果？')">
  {hidden}<input type="hidden" name="action" value="confirm-delivered">
  <button type="submit">确认已送达</button>
</form>
<form class="inline" method="post" action="/delivery/recover" onsubmit="return confirm('关闭后系统不会再自动处理这项交付，确认继续？')">
  {hidden}<input type="hidden" name="action" value="close">
  <button class="danger" type="submit">关闭未知状态</button>
</form>
</div>"""
    return f"""<tr>
  <td data-label="处理编号"><strong>{escape(candidate.task_id)}</strong><br><span class="muted">{escape(candidate.task_type)}</span></td>
  <td data-label="来源">{escape(source)}<br><span class="muted">{candidate.item_count} 个交付项</span></td>
  <td data-label="交付状态"><span class="status status-focus">{escape(status)}</span><br><code>{escape(candidate.safe_error_code)}</code></td>
  <td data-label="更新时间">{escape(candidate.updated_at)}</td>
  <td data-label="人工操作">{actions}</td>
</tr>"""


def _render_skills_section(skills: object, csrf_token: str = "") -> str:
    skill_list = list(skills)
    if not skill_list:
        body = '<div class="empty">暂无 Skill</div>'
    else:
        rows = "\n".join(_render_skill_row(skill, csrf_token) for skill in skill_list)
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


def _render_skill_row(skill: object, csrf_token: str = "") -> str:
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
      {_csrf_input(csrf_token)}
      <input type="hidden" name="skill_id" value="{escape(str(getattr(skill, "id")))}">
      <input type="hidden" name="enabled" value="{next_enabled}">
      <button type="submit">{action_text}</button>
    </form>
  </td>
</tr>"""


def _render_users_section(users: dict[str, list[str]], csrf_token: str = "") -> str:
    if not users:
        body = '<div class="empty">暂无用户权限配置</div>'
    else:
        rows = "\n".join(
            _render_user_row(userid, skills, csrf_token)
            for userid, skills in sorted(users.items())
        )
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


def _render_user_row(userid: str, skills: list[str], csrf_token: str = "") -> str:
    skills_text = ", ".join(skills)
    form_id = f"user-form-{_html_id(userid)}"
    return f"""<tr>
  <td data-label="用户 ID">{escape(userid)}</td>
  <td data-label="允许使用的 Skill">
    <input form="{form_id}" type="text" name="allowed_skills" value="{escape(skills_text)}" aria-label="allowed skills for {escape(userid)}">
  </td>
  <td data-label="操作">
    <form id="{form_id}" method="post" action="/users/update">
      {_csrf_input(csrf_token)}
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


def _csrf_input(token: str) -> str:
    return f'<input type="hidden" name="csrf_token" value="{escape(token)}">'


def _require_csrf(form: dict[str, list[str]], expected_token: str) -> None:
    provided = form.get("csrf_token", [""])[0]
    if not expected_token or not secrets.compare_digest(provided, expected_token):
        raise ValueError("页面令牌已失效，请刷新后重试")


def _html_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-") or "item"


if __name__ == "__main__":
    main()
