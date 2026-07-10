import os
import json
from pathlib import Path

root = Path("/home/david/NAS_NFS/Media/Video/Movie")
video_exts = {".mp4", ".mkv", ".avi", ".ts", ".m2ts"}

count = 0
for backup_file in root.rglob("sound_track.json.backup"):
    d = backup_file.parent
    if not d.is_dir():
        continue
        
    # 统计该目录下的有效视频文件（>100M）
    videos = [
        f for f in d.iterdir() 
        if f.is_file() and f.suffix.lower() in video_exts and f.stat().st_size > 100 * 1024 * 1024
    ]
    
    # 只有当且仅有一个视频文件时才进行恢复
    if len(videos) == 1:
        video_file = videos[0]
        json_file = d / f"{video_file.stem}.json"
        
        if json_file.exists():
            try:
                with open(backup_file, "r", encoding="utf-8") as f:
                    backup_data = json.load(f)
                    
                old_audio = backup_data.get("audio_tracks", [])
                
                # 旧版的 audio_tracks 是一个 list，如果里面有内容，我们就进行恢复
                if old_audio and isinstance(old_audio, list) and len(old_audio) > 0:
                    with open(json_file, "r", encoding="utf-8") as f:
                        new_data = json.load(f)
                        
                    if "audio_tracks" not in new_data or not isinstance(new_data["audio_tracks"], dict):
                        new_data["audio_tracks"] = {}
                        
                    new_data["audio_tracks"]["done"] = True
                    new_data["audio_tracks"]["tracks"] = old_audio
                    
                    # 重新计算 is_chinese_audio 和 primary_language
                    is_zh = False
                    primary_lang = ""
                    for t in old_audio:
                        lang = t.get("lang", "") or t.get("language", "")
                        if lang.lower() in ("zh", "chi", "zho", "cmn"):
                            is_zh = True
                            primary_lang = "zh"
                            break
                    
                    if not primary_lang and old_audio:
                        primary_lang = old_audio[0].get("lang", "") or old_audio[0].get("language", "")
                        
                    new_data["audio_tracks"]["is_chinese_audio"] = is_zh
                    new_data["audio_tracks"]["primary_language"] = primary_lang
                    
                    # 保存回新的视频状态文件
                    with open(json_file, "w", encoding="utf-8") as f:
                        json.dump(new_data, f, indent=2, ensure_ascii=False)
                        
                    print(f"Recovered: {json_file.name}")
                    count += 1
            except Exception as e:
                print(f"Error processing {d.name}: {e}")

print(f"\n✅ 数据合并完毕，共成功恢复了 {count} 部电影的音轨数据。")
