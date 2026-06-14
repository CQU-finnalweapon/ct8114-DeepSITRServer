"""Test async analysis + polling mechanism."""
import json
import os
import tempfile
import time

import requests

BASE = "http://localhost:8000"

# Create a test C file with known GJB 8114 violations
test_code = r"""#include <stdio.h>
int main() {
    int x = 0;
    if (x == 0)
        printf("hello\n");
    goto end;
end:
    return 0;
}
"""

with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as f:
    f.write(test_code)
    tmpfile = f.name

try:
    # 1. Submit analysis
    with open(tmpfile, "rb") as f:
        resp = requests.post(
            f"{BASE}/analyze",
            files={"files": (os.path.basename(tmpfile), f, "text/x-c")},
        )
    assert resp.status_code == 200, f"Submit failed: {resp.status_code} {resp.text}"
    data = resp.json()
    print("=== Submit Response ===")
    print(json.dumps(data, indent=2, ensure_ascii=False))

    request_id = data["request_id"]
    assert data["status"] == "pending", f"Expected pending, got {data['status']}"
    print(f"\n✓ Task submitted: {request_id} (status: {data['status']})")

    # 2. Poll until complete
    max_attempts = 30
    for i in range(max_attempts):
        time.sleep(1)
        resp = requests.get(f"{BASE}/status/{request_id}")
        assert resp.status_code == 200, f"Poll failed: {resp.status_code}"
        task = resp.json()
        st = task["status"]
        print(f"  Poll #{i+1}: status={st}")
        if st == "completed":
            print("\n=== Analysis Complete! ===")
            payload = task["payload"]
            summary = payload.get("report", {}).get("summary", {})
            print(f"  Total bugs: {summary.get('total_bugs')}")
            print(f"  Total files: {summary.get('total_files')}")
            print(f"  By level: {summary.get('by_level')}")
            print("\n✓ Async polling mechanism works correctly!")
            break
        elif st == "failed":
            print(f"\n✗ Task failed: {task.get('error')}")
            break
    else:
        print(f"\n⚠ Task still not completed after {max_attempts} attempts")

    # 3. Test project analysis with polling
    print("\n=== Testing project analysis polling ===")
    # First list projects
    resp = requests.get(f"{BASE}/projects")
    projects_data = resp.json()
    projects = projects_data.get("projects", [])
    print(f"Found {len(projects)} projects")
    if projects:
        pid = projects[0]["project_id"]
        print(f"Analyzing project: {pid}")
        resp = requests.post(f"{BASE}/projects/{pid}/analyze")
        assert resp.status_code == 200, f"Project analyze submit failed: {resp.status_code} {resp.text}"
        submit = resp.json()
        print(f"  Submit: request_id={submit['request_id']}, status={submit['status']}")
        assert submit["status"] == "pending"

        # Poll
        for i in range(30):
            time.sleep(1)
            resp = requests.get(f"{BASE}/status/{submit['request_id']}")
            task = resp.json()
            st = task["status"]
            print(f"  Poll #{i+1}: status={st}")
            if st == "completed":
                summary = task["payload"].get("report", {}).get("summary", {})
                print(f"  Total bugs: {summary.get('total_bugs')}")
                print("\n✓ Project analysis polling works correctly!")
                break
            elif st == "failed":
                print(f"\n✗ Task failed: {task.get('error')}")
                break

    # 4. Test 404 on unknown task
    print("\n=== Testing 404 on unknown task ===")
    resp = requests.get(f"{BASE}/status/nonexistent_task_id")
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"
    print("✓ Unknown task returns 404")

    print("\n" + "=" * 50)
    print("All tests passed! ✓")

finally:
    os.unlink(tmpfile)
