"""测试 UniPortal 共享卷读写功能（模拟模式）"""
import os
import sys
import json

os.environ["MOCK_UNIPORTAL_DIR"] = "mock_uniportal"
sys.path.insert(0, ".")

from server import _build_item_index, _resolve_project_path, _write_back_to_uniportal

# Test 1: _build_item_index with mock
print("=== Test 1: _build_item_index ===")
index = _build_item_index()
for k, v in sorted(index.items()):
    print(f"  {k} -> {v}")
print(f"  Total: {len(index)} items")
assert len(index) >= 2, f"Expected >= 2 items, got {len(index)}"
print("  PASSED")

# Test 2: _resolve_project_path
print("\n=== Test 2: _resolve_project_path ===")
for pid in ["demo_project", "hello_world"]:
    path = _resolve_project_path(pid)
    assert path.is_dir(), f"{pid} not found"
    print(f"  {pid} -> exists=True")
print("  PASSED")

# Test 3: _write_back_to_uniportal
print("\n=== Test 3: _write_back_to_uniportal ===")
demo_path = index.get("demo_project")
assert demo_path, "demo_project not in index"

payload = {
    "request_id": "test_001",
    "project_id": "demo_project",
    "report": {
        "summary": {
            "total_files": 2,
            "total_bugs": 5,
            "by_level": {"error": 2, "warning": 3},
        }
    },
}
_write_back_to_uniportal(demo_path, "demo_project", payload)

# Verify write-back
report_file = demo_path / "_ct8114" / "last_report.json"
meta_file = demo_path / "meta.json"
assert report_file.exists(), "report.json not written"
assert meta_file.exists(), "meta.json not written"

meta = json.loads(meta_file.read_text(encoding="utf-8"))
assert "ct8114_last_analysis" in meta
assert meta.get("ct8114_summary", {}).get("total_bugs") == 5
print(f"  report.json: {report_file.exists()}")
print(f"  meta.json: {meta_file.exists()}")
print(f"  meta summary: {meta.get('ct8114_summary', {})}")
print("  PASSED")

# Test 4: Verify no side effects on nonexistent
print("\n=== Test 4: Error handling ===")
try:
    _resolve_project_path("nonexistent")
    print("  FAILED: should have raised 404")
except Exception as e:
    assert "404" in str(e) or "未找到" in str(e)
    print(f"  404 for nonexistent: OK")

print("\n=== ALL TESTS PASSED ===")
