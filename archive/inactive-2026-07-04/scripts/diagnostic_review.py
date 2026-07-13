#!/usr/bin/env python3
"""诊断性审核脚本 - 完整打印 LLM 输出"""

import sys
import json
import os
from pathlib import Path

# 设置 Python 路径
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ============================================================
# Step 1: 读取 .env 设置环境变量
# ============================================================
print("=" * 60)
print("STEP 1: 加载环境变量")
print("=" * 60)

env_path = Path(__file__).resolve().parents[1] / ".env"
for line in env_path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        key, _, value = line.partition("=")
        if key in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "MODEL_NAME", "MODEL_PROVIDER"):
            os.environ[key] = value
            print(f"  加载: {key} = {value[:10]}..." if len(value) > 10 else f"  加载: {key} = {value}")

api_key = os.environ.get("ANTHROPIC_API_KEY", "")
base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic")
model_name = os.environ.get("MODEL_NAME", "MiniMax-M2.7")
print(f"\n  API Key: {api_key[:10]}..." if api_key else "  API Key: 未设置!")
print(f"  Base URL: {base_url}")
print(f"  Model: {model_name}")

# ============================================================
# Step 2: 解析 docx 文件
# ============================================================
print("\n" + "=" * 60)
print("STEP 2: 解析 docx 文件")
print("=" * 60)

from app.review.parser import parse_docx

docx_path = Path("/Users/op04/Desktop/M-Agent/data/reviews/20260622-001/source/微众银行信息内参周报2026年第24期.docx")
parsed = parse_docx(docx_path)

print(f"  文件: {docx_path.name}")
print(f"  段落数: {parsed.total_paragraphs}")
print(f"  总字符数: {parsed.total_chars}")
print(f"  前3段预览:")
for i, p in enumerate(parsed.paragraphs[:3]):
    print(f"    [段 {i}]: {p[:80]}...")

# ============================================================
# Step 3: 加载规则
# ============================================================
print("\n" + "=" * 60)
print("STEP 3: 加载规则")
print("=" * 60)

from app.review.rule_loader import load_rules

rules_text = load_rules('app/data/rules.md')
print(f"  规则文件: app/data/rules.md")
print(f"  规则总长度: {len(rules_text)} 字符")
print(f"  规则前500字预览:\n{rules_text[:500]}")

# ============================================================
# Step 4: 获取 Anthropic Client
# ============================================================
print("\n" + "=" * 60)
print("STEP 4: 获取 Anthropic Client")
print("=" * 60)

from app.review.reviewer import _get_anthropic_client

client, actual_model = _get_anthropic_client()
print(f"  Client: {client}")
print(f"  Model: {actual_model}")

# ============================================================
# Step 5: 构造 Prompt
# ============================================================
print("\n" + "=" * 60)
print("STEP 5: 构造 Prompt")
print("=" * 60)

from app.review.reviewer import _build_prompt

prompt = _build_prompt(rules_text, parsed.paragraphs, docx_path.name)
print(f"  Prompt 总长度: {len(prompt)} 字符")
print(f"  Prompt 前1000字:")
print(prompt[:1000])

# ============================================================
# Step 6: 发送给 MiniMax-M2.7
# ============================================================
print("\n" + "=" * 60)
print("STEP 6: 发送请求到 MiniMax-M2.7")
print("=" * 60)

import anthropic

print(f"  max_tokens: 8192")
print(f"  timeout: 90s")
print("  正在等待响应...")

try:
    message = client.messages.create(
        model=actual_model,
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
        timeout=90.0,
    )
    print(f"\n  响应成功!")
    print(f"  stop_reason: {message.stop_reason}")
    print(f"  model: {message.model}")
    print(f"  usage: input_tokens={message.usage.input_tokens}, output_tokens={message.usage.output_tokens}")
except Exception as e:
    print(f"  请求失败: {e}")
    sys.exit(1)

# ============================================================
# Step 7: 打印所有 Content Blocks 的类型和长度
# ============================================================
print("\n" + "=" * 60)
print("STEP 7: 打印所有 message.content blocks")
print("=" * 60)

print(f"\n  共 {len(message.content)} 个 content blocks:\n")
for i, block in enumerate(message.content):
    block_type = type(block).__name__
    if hasattr(block, 'text') and block.text:
        content_len = len(block.text)
    elif hasattr(block, 'thinking') and block.thinking:
        content_len = len(block.thinking)
    else:
        content_len = 0
    print(f"  Block {i}: 类型={block_type}, 内容长度={content_len}")

# ============================================================
# Step 7b: 打印完整的 ThinkingBlock 内容
# ============================================================
print("\n" + "=" * 60)
print("STEP 7b: 完整 ThinkingBlock 内容 (即使很长也要打印)")
print("=" * 60)

thinking_blocks = [b for b in message.content if hasattr(b, 'thinking')]
if thinking_blocks:
    for i, block in enumerate(thinking_blocks):
        print(f"\n--- ThinkingBlock {i} (长度: {len(block.thinking)}) ---")
        print(block.thinking)
        print("--- END OF THINKING BLOCK ---")
else:
    print("  无 ThinkingBlock")

# ============================================================
# Step 7c: 打印完整的 TextBlock 内容
# ============================================================
print("\n" + "=" * 60)
print("STEP 7c: 完整 TextBlock 内容 (不是摘要)")
print("=" * 60)

text_blocks = [b for b in message.content if hasattr(b, 'text') and b.text]
for i, block in enumerate(text_blocks):
    print(f"\n--- TextBlock {i} (长度: {len(block.text)}) ---")
    print(block.text)
    print("--- END OF TEXT BLOCK ---")

# ============================================================
# Step 8: 解析并打印所有 findings
# ============================================================
print("\n" + "=" * 60)
print("STEP 8: 解析并打印所有 findings")
print("=" * 60)

from app.review.reviewer import _parse_llm_output

# 收集所有 text 内容
all_text = "\n".join(b.text for b in message.content if hasattr(b, 'text') and b.text)

findings = _parse_llm_output(all_text, parsed.paragraphs)

print(f"\n  共解析出 {len(findings)} 条 findings:\n")
for i, f in enumerate(findings):
    print(f"  Finding {i+1}:")
    print(f"    rule_id: {f.rule_id}")
    print(f"    paragraph_index: {f.paragraph_index}")
    print(f"    line_number: {f.line_number}")
    print(f"    description: {f.description}")
    print(f"    original_text: {f.original_text[:100]}..." if len(f.original_text) > 100 else f"    original_text: {f.original_text}")
    print()

print("\n" + "=" * 60)
print("诊断完成!")
print("=" * 60)
