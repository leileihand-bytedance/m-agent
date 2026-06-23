#!/bin/bash
# hook-test.sh — 测试完成后自动写 STATUS-REPORT
# 用法: ./scripts/hook-test.sh <module> [test_output]
# 示例: ./scripts/hook-test.sh review "5 tests OK"
exec "$(dirname "$0")/hook-test.py" "${1:-unknown}" "${2:-}"
