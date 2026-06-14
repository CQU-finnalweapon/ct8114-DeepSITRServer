"""测试模拟分析模式"""
import os, sys
os.environ["MOCK_UNIPORTAL_DIR"] = "mock_uniportal"
os.environ["MOCK_ANALYSIS"] = "true"
sys.path.insert(0, ".")

from server import _run_analysis, _resolve_project_path, _collect_code_files, _write_back_to_uniportal

root = _resolve_project_path("demo_project")
files = _collect_code_files(root)
src_files = [f for f in files if f.suffix in (".c", ".cc", ".cpp", ".cxx")]
print(f"Target files: {len(src_files)}")

report = _run_analysis(root, src_files, "demo_project")
print(f"Report ID: {report.report_id}")
print(f"Bugs: {report.total_bugs}")

s = report.summary()
print(f"Summary: bugs={s['total_bugs']}, files={s['total_files']}")

# Test write-back
payload = {"request_id": report.report_id, "project_id": "demo_project", "report": report.to_dict()}
_write_back_to_uniportal(root, "demo_project", payload)
wb = root / "_ct8114" / "last_report.json"
print(f"Write-back: {wb.exists()} -> {wb}")
print("ALL PASSED!")
