from __future__ import annotations

import argparse
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
from typing import Callable
from urllib.parse import parse_qs

from app.admin.services import AdminPaths, list_jobs, list_policy_users, list_skills, set_skill_enabled, set_user_skills


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATHS = AdminPaths(
    skills_dir=PROJECT_ROOT / "skills",
    policy_path=PROJECT_ROOT / "config" / "platform-policy.yaml",
    jobs_dir=PROJECT_ROOT / "data" / "platform" / "jobs",
)


def render_dashboard(paths: AdminPaths = DEFAULT_PATHS) -> str:
    skills = list_skills(paths.skills_dir)
    users = list_policy_users(paths.policy_path)
    jobs = list_jobs(paths, limit=20)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>M-Agent 管理后台</title>
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
    main {{
      width: min(1180px, calc(100% - 32px));
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
    <h1>M-Agent 管理后台</h1>
    <p>本机管理入口：Skill 开关、用户权限、最近任务记录。</p>
  </header>
  <main>
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
    return f"""<section>
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
    return f"""<section>
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
    return f"""<section>
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
