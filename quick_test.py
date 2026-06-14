"""Quick test: verify shared volume write-back persists on disk."""
import json
import os
import tempfile
import time
from pathlib import Path

import requests

BASE = "http://localhost:8000"

with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as f:
    f.write("#include <stdio.h>\nint main(){int x=0;if(x==0)printf(\"hi\");goto end;end:return 0;}\n")
    tf = f.name

try:
    with open(tf, "rb") as fh:
        r = requests.post(f"{BASE}/analyze", files={"files": (os.path.basename(tf), fh)})
    submit = r.json()
    print(f"Submit: status={submit['status']}, request_id={submit['request_id'][:20]}...")
    rid = submit["request_id"]

    for i in range(10):
        time.sleep(0.5)
        task = requests.get(f"{BASE}/status/{rid}").json()
        if task["status"] == "completed":
            p = task["payload"]
            summary = p["report"]["summary"]
            print(f"Completed! total_bugs={summary['total_bugs']}")
            print(f"saved_project_id: {p.get('saved_project_id')}")
            wp = p.get("uniportal_writeback_path", "")
            print(f"writeback_path: {wp}")

            if wp:
                wpp = Path(wp)
                print(f"File exists on disk: {wpp.exists()}")
                if wpp.exists():
                    print(f"  Size: {wpp.stat().st_size} bytes")
                    data = json.loads(wpp.read_text(encoding="utf-8"))
                    print(f"  Content report_id: {data.get('report',{}).get('report_id','?')}")
                else:
                    print("  FILE NOT FOUND on disk!")

            # Check meta.json too
            if p.get("saved_project_id"):
                proj_dir = Path(wp).parent.parent if wp else None
                if proj_dir:
                    meta = proj_dir / "meta.json"
                    print(f"meta.json exists: {meta.exists()}")
                    if meta.exists():
                        md = json.loads(meta.read_text(encoding="utf-8"))
                        print(f"  last_analysis: {md.get('ct8114_last_analysis')}")
            break

finally:
    os.unlink(tf)
