"""测试 DCAB force 推导逻辑"""
import sys
sys.path.insert(0, '..')
from dcab_client import _derive_force

tests = [
    # (rule_id, dcab_force, expected_force)
    ("GJB-R-1-8-2", "0", "1"),
    ("GJB-A-1-1-1", "0", "0"),
    ("MISRA-C-2012:R-2-1:0", "0", "1"),
    ("GJB-R-1-3-9", "", "1"),
    ("GJB-R-1-7-7", "1", "1"),
    ("GJB-A-1-13-1", "0", "0"),
    ("UNKNOWN", "0", "0"),
    ("", "0", "0"),
]

all_ok = True
for rule_id, dcab_force, expected in tests:
    result = _derive_force(rule_id, dcab_force)
    status = "OK" if result == expected else f"FAIL (expected {expected})"
    if result != expected:
        all_ok = False
    print(f"  {rule_id:30s} in={dcab_force} -> {result}  {status}")

print()
print("All tests passed!" if all_ok else "SOME TESTS FAILED!")
