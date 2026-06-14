"""检查 codetidy.exe 是否可用"""
import os, sys
os.environ["MOCK_UNIPORTAL_DIR"] = "mock_uniportal"
sys.path.insert(0, ".")
from dsit_parser import find_codetidy_bin, get_codetidy_search_paths

print("=== codetidy 搜索路径 ===")
for p in get_codetidy_search_paths():
    exists = os.path.exists(p)
    print(f"  {p}  -> {'EXISTS' if exists else 'NOT FOUND'}")

bin_path = find_codetidy_bin()
print(f"\n=== 结果 ===")
print(f"  find_codetidy_bin() -> {bin_path}")
if bin_path:
    print(f"  codetidy 可用!")
else:
    print(f"  codetidy 未找到 - 分析功能不可用")
