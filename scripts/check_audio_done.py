import json
from pathlib import Path

root = Path("/home/david/NAS_NFS/Media/Video/Movie")
done_count = 0
not_done_count = 0
total = 0

for jf in root.rglob("*.json"):
    try:
        d = json.loads(jf.read_text())
        at = d.get("audio_tracks", {})
        total += 1
        if at.get("done"):
            done_count += 1
        else:
            not_done_count += 1
    except Exception:
        pass

print(f"总计: {total}, audio_tracks.done=True: {done_count}, done=False: {not_done_count}")
