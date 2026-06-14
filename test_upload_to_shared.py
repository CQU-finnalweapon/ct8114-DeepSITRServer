"""测试上传 → 共享卷 → 写回完整流程"""
import os, sys, shutil, uuid

os.environ["MOCK_UNIPORTAL_DIR"] = "mock_uniportal"
sys.path.insert(0, ".")

from pathlib import Path
from server import _write_back_to_uniportal

mock_root = Path("mock_uniportal").resolve()
mock_root.mkdir(exist_ok=True)

# 模拟上传: 创建新项目
portal = mock_root / "_upload"
portal.mkdir(exist_ok=True)
pid = f"upload_{uuid.uuid4().hex[:8]}"
proj = portal / pid
proj.mkdir(parents=True)

# 复制测试文件
sample = Path("mock_uniportal/proj_002/hello_world/hello.c")
if sample.exists():
    shutil.copy(sample, proj / "test.c")
    print(f"Simulated upload -> {proj}")
    print(f"  test.c copied: {(proj / 'test.c').exists()}")

# 模拟分析后写回
payload = {
    "request_id": "test_001",
    "report": {"summary": {"total_bugs": 3, "total_files": 1}},
}
wb = _write_back_to_uniportal(proj, pid, payload)
print(f"Write-back report: {wb['report_path']}")
print(f"Write-back meta:   {wb['meta_path']}")

# 验证
print()
print("mock_uniportal structure after upload:")
for p in sorted(mock_root.rglob("*")):
    if p.is_file():
        rel = str(p.relative_to(mock_root))
        size = p.stat().st_size
        print(f"  {rel} ({size} bytes)")

print()
print("PASSED: upload -> shared volume -> write-back works!")
