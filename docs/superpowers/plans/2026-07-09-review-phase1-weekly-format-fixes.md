# Review Phase1 And Weekly Format Fixes Implementation Plan

> 状态：已实施。本文件保留为阶段性计划，当前行为以 `app/review/README.md` 和代码测试为准。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复内参周报第一阶段正文误截断导致的“内容不完整”误报，以及格式审核范围和正文缩进规则错误。

**Architecture:** 保持现有两阶段审核结构不变，只收敛第一阶段输入上下文和内参周报正文格式识别逻辑。优先基于现有 `reviewer.py` 扩展辅助函数和回归测试，不改动企业微信入口和审核主流程。

**Tech Stack:** Python, pytest, python-docx

---

### Task 1: Phase1 正文上下文回归

**Files:**
- Modify: `tests/test_review_main_flow_optimization.py`
- Modify: `app/review/reviewer.py`

- [ ] **Step 1: 写失败测试**
- [ ] **Step 2: 跑单测确认失败**
- [ ] **Step 3: 最小修改 phase1 上下文构造**
- [ ] **Step 4: 重跑单测确认通过**

### Task 2: 内参正文格式范围和缩进回归

**Files:**
- Modify: `tests/test_reviewer.py`
- Modify: `app/review/reviewer.py`

- [ ] **Step 1: 写失败测试**
- [ ] **Step 2: 跑单测确认失败**
- [ ] **Step 3: 最小修改正文起始识别和缩进规则**
- [ ] **Step 4: 重跑单测确认通过**

### Task 3: 验证与文档

**Files:**
- Modify: `app/review/README.md`

- [ ] **Step 1: 跑相关测试集**
- [ ] **Step 2: 更新审核说明和核心文档**
- [ ] **Step 3: 再跑一次最终验证**
