# Project Console Current-State Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update the local project console so its architecture map, runtime health, module summaries, and capability relationships match the M-Agent code and plans as of 2026-07-17.

**Architecture:** Keep the existing server-rendered console and `vis-network` graph. Extend the versioned capability specifications and heartbeat service list in `app/admin/services.py`, while retaining TODO, Git, skill configuration, and heartbeat files as the factual status sources. Add repository-level tests that fail when an installed skill is absent from the architecture map.

**Tech Stack:** Python 3.13, dataclasses, server-rendered HTML, vis-network 10.1.0, pytest, uv.

---

### Task 1: Lock the current project facts into failing tests

**Files:**
- Modify: `tests/test_admin_services.py`
- Modify: `tests/test_admin_server.py`

- [ ] Add a service-health assertion for `rewrite_bot`, including its readable label and heartbeat state.
- [ ] Add capability assertions for the independent rewrite Bot, research synthesis, Shenyinxie news, static HTML review, the live PPT first phase, and the planned shared review core.
- [ ] Add relationship assertions for the relevant Bot, runtime, document, search, knowledge, and delivery paths.
- [ ] Add a repository-level assertion that every `skills/*/config.yaml` id appears in at least one architecture capability's `skill_ids`.
- [ ] Run `uv run --locked pytest tests/test_admin_services.py tests/test_admin_server.py -v` and confirm the new assertions fail because the console model is stale.

### Task 2: Update the console's factual model

**Files:**
- Modify: `app/admin/services.py`

- [ ] Add `rewrite_bot` to the console heartbeat service list without changing the operations Bot's alert configuration.
- [ ] Update the entry layer: limit the writing Bot description to its actual scope and add the independent rewrite Bot with its own runtime heartbeat.
- [ ] Update the business layer with `research_synthesis`, `shenyinxie_news`, static HTML review, PPT first-phase wording/status, and `TODO-031` shared review core.
- [ ] Update capability relations so new entry points and skills connect to the actual runtime, document, search, knowledge, and attachment-delivery paths.
- [ ] Expand module Git path ownership so recent changes under all writing skills and `app/rewrite_bot/` are attributed correctly.
- [ ] Run the focused tests and confirm they pass.

### Task 3: Synchronize documentation and visually verify the console

**Files:**
- Modify: `app/admin/README.md`
- Modify: `docs/operations/admin-console.md`
- Modify: `docs/development/README.md`
- Modify: `docs/development/architecture.md`
- Modify: `docs/development/testing-and-delivery.md`

- [ ] Document the four heartbeat-producing Bots and the newly represented business capabilities.
- [ ] Document the repository-level skill-to-architecture coverage gate.
- [ ] Run `uv run --locked pytest tests/test_admin_services.py tests/test_admin_server.py -v`.
- [ ] Run the full deterministic suite excluding the real LLM test, then run `uv run --locked python scripts/project_docs.py check`.
- [ ] Restart the local console, reload `http://127.0.0.1:8787/`, verify desktop and mobile layouts, inspect browser errors, and leave the updated console open.
- [ ] Commit through Git and use the managed project push command, then confirm local/remote synchronization.
