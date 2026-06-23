#!/usr/bin/env python3.11
"""hook-test.sh — 测试完成后自动写 STATUS-REPORT.

用法:
  ./scripts/hook-test.sh <module> [test_output]
示例:
  ./scripts/hook-test.sh review "5 tests OK"
  ./scripts/hook-test.sh agent
"""
import sys, datetime, os
from pathlib import Path

def main():
    module = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    test_output = sys.argv[2] if len(sys.argv) > 2 else ""

    status_file = Path.home() / "Desktop" / "M-Agent" / "STATUS-REPORT.md"
    if not status_file.exists():
        print(f"⚠️  {status_file} not found, skipping.")
        return

    today = datetime.date.today().strftime("%Y-%m-%d")
    now = datetime.datetime.now().strftime("%H:%M")

    entry = f"**[测试通过]** {module}"
    if test_output:
        entry += f" | {test_output}"
    entry += f" | {now}"

    content = status_file.read_text(encoding="utf-8")

    today_marker = f"## 更新日期: {today}"

    if today_marker in content:
        # 找到今天的章节,在最后一个条目后追加
        idx = content.index(today_marker)
        # 找下一个 ## 更新日期 或文件末尾
        next_date = content.find("\n## 更新日期:", idx + len(today_marker))
        if next_date == -1:
            section_end = len(content)
        else:
            section_end = next_date

        section = content[idx:section_end].rstrip()

        last_item = section.rfind("\n**[")
        if last_item == -1:
            insert_pos = idx + len(section) + 1
        else:
            after_last = section[last_item:].find("\n")
            insert_pos = idx + last_item + after_last

        new_section = content[:insert_pos] + f"\n{entry}" + content[insert_pos:]
    else:
        # 没有今天的章节,新建
        new_entry = f"\n{today_marker}\n\n{entry}\n"
        content = content.rstrip() + new_entry
        new_section = content

    status_file.write_text(new_section, encoding="utf-8")
    print(f"✅ STATUS-REPORT updated: {entry}")

if __name__ == "__main__":
    main()
