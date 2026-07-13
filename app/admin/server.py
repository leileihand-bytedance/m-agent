from __future__ import annotations

import argparse
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
from typing import Callable
from urllib.parse import parse_qs

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


def render_dashboard(paths: AdminPaths = DEFAULT_PATHS) -> str:
    overview = build_project_overview(paths)
    skills = list_skills(paths.skills_dir)
    users = list_policy_users(paths.policy_path)
    jobs = list_jobs(paths, limit=20)

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
      gap: 20px;
    }}
    section {{
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
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      padding: 16px 18px 18px;
    }}
    .metric {{
      min-height: 112px;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 14px;
      background: #fff;
    }}
    .metric-label {{ color: var(--muted); font-size: 13px; }}
    .metric-value {{ margin-top: 7px; font-size: 25px; font-weight: 750; line-height: 1.2; }}
    .metric-meta {{ margin-top: 8px; color: #475467; font-size: 12px; }}
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
      .summary-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .metric-value {{ font-size: 21px; }}
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
    <p>查看各板块进度、下一步待办、运行健康，并管理 Skill 与用户权限。</p>
  </header>
  <nav aria-label="控制台导航">
    <a href="#overview">总览</a>
    <a href="#modules">板块</a>
    <a href="#todos">待办</a>
    <a href="#runtime">运行</a>
    <a href="#changes">更新</a>
    <a href="#skills">Skills</a>
    <a href="#users">权限</a>
    <a href="#jobs">任务</a>
  </nav>
  <main>
    {_render_overview_section(overview)}
    {_render_modules_section(overview)}
    {_render_todos_section(overview)}
    {_render_runtime_section(overview)}
    {_render_changes_section(overview)}
    {_render_skills_section(skills)}
    {_render_users_section(users)}
    {_render_jobs_section(jobs)}
  </main>
</body>
</html>"""


def create_handler(paths: AdminPaths = DEFAULT_PATHS) -> type[BaseHTTPRequestHandler]:
    class AdminHandler(BaseHTTPRequestHandler):
        server_version = "MAgentAdmin/0.1"

        def do_GET(self) -> None:
            if self.path not in {"/", "/index.html"}:
                self._send_text("Not found", HTTPStatus.NOT_FOUND)
                return
            self._send_html(render_dashboard(paths))

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
            self.send_header("Location", "/")
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


def _render_overview_section(overview: ProjectOverview) -> str:
    repository = overview.repository
    git_meta_parts = [part for part in (repository.branch, repository.short_commit) if part]
    if repository.dirty_count:
        git_meta_parts.append(f"{repository.dirty_count} 项未提交变更")
    git_meta = " · ".join(git_meta_parts) or "暂无仓库信息"
    return f"""<section id="overview">
  <div class="section-head">
    <div>
      <h2>项目总览</h2>
      <p class="hint">自动汇总核心配置、TODO、运行数据和本地 Git 记录。更新时间：{escape(overview.generated_at)}</p>
    </div>
  </div>
  <div class="summary-grid">
    {_render_metric("已启用能力", f"{overview.enabled_skill_count} / {overview.total_skill_count}", "已启用 Skill / 已安装 Skill")}
    {_render_metric("开放待办", str(overview.open_todo_count), f"其中 P0/P1 共 {overview.urgent_todo_count} 项")}
    {_render_metric("累计任务", str(overview.writing_job_count + overview.review_task_count), f"写作 {overview.writing_job_count} · 审核 {overview.review_task_count}")}
    {_render_metric("Git 状态", repository.sync_label, git_meta)}
  </div>
</section>"""


def _render_metric(label: str, value: str, meta: str) -> str:
    return f"""<div class="metric">
  <div class="metric-label">{escape(label)}</div>
  <div class="metric-value">{escape(value)}</div>
  <div class="metric-meta">{escape(meta)}</div>
</div>"""


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
