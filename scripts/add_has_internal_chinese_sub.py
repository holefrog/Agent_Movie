#!/usr/bin/env python3
"""
一次性脚本：递归搜索视频 JSON 文件，在 subtitles_assessment 中添加 has_internal_chinese_sub 字段（默认 None）
"""

import json
from pathlib import Path

SEARCH_DIR = "/home/david/NAS_NFS/Media/Video/Movie"

updated = 0
skipped = 0
errors = 0

for json_path in Path(SEARCH_DIR).rglob("*.json"):
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if "subtitles_assessment" not in data:
            skipped += 1
            continue

        if "has_internal_chinese_sub" in data["subtitles_assessment"]:
            skipped += 1
            continue

        data["subtitles_assessment"]["has_internal_chinese_sub"] = None

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"✅ {json_path.name}")
        updated += 1

    except Exception as e:
        print(f"❌ {json_path}: {e}")
        errors += 1

print(f"\n完成：更新 {updated} 个，跳过 {skipped} 个，错误 {errors} 个")
