#!/usr/bin/env python3
"""
迁移脚本：将 subtitles_assessment.has_internal_chinese_sub
迁移到新的 subtitle_tracks.has_internal_chinese_sub
"""
import json
from pathlib import Path

root = Path("/home/david/NAS_NFS/Media/Video/Movie")
migrated = 0
already_ok = 0
errors = 0

for jf in root.rglob("*.json"):
    try:
        data = json.loads(jf.read_text())
        changed = False

        # 取旧字段值
        sa = data.get("subtitles_assessment", {})
        if isinstance(sa, list):
            sa = {}
            data["subtitles_assessment"] = sa
            changed = True
        
        old_val = sa.get("has_internal_chinese_sub")

        # 确保 subtitle_tracks 块存在且是字典
        if "subtitle_tracks" not in data or isinstance(data["subtitle_tracks"], list):
            data["subtitle_tracks"] = {
                "done": False,
                "has_internal_chinese_sub": None,
                "streams": [],
                "error": None
            }
            changed = True

        st = data["subtitle_tracks"]

        # 如果 subtitle_tracks 未设过，从旧字段迁移
        if st.get("has_internal_chinese_sub") is None and old_val is not None:
            st["has_internal_chinese_sub"] = old_val
            if old_val is not None:
                st["done"] = True
            changed = True
            migrated += 1
        else:
            already_ok += 1

        # 移除旧字段
        if "has_internal_chinese_sub" in sa:
            del sa["has_internal_chinese_sub"]
            changed = True

        if changed:
            jf.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    except Exception as e:
        import traceback
        print(f"ERROR: {jf}: {e}")
        traceback.print_exc()
        errors += 1

print(f"迁移完成: 迁移 {migrated} 个, 已是最新 {already_ok} 个, 错误 {errors} 个")
