"""在容器内运行真实 codetidy，验证 note 过滤修复"""
import subprocess
import sys
sys.path.insert(0, '/app')
from dsit_parser import _parse_codetidy_output
from pathlib import Path

# 运行真实 codetidy（两个测试文件）
proc = subprocess.run(
    ['/opt/dcab/core/codetidy', '/test/test_main.c', '/test/test_utils.c',
     '-checks=clang-analyzer-gjb*', '--',
     '-resource-dir=/usr/lib/llvm-19/lib/clang/19', '-std=c11', '-I/usr/include'],
    capture_output=True, text=True, timeout=60
)

# 用修复后的解析器解析
bugs = _parse_codetidy_output(proc.stdout, proc.stderr,
    [Path('/test/test_main.c'), Path('/test/test_utils.c')])

real = [b for b in bugs if b.force == '1']
noise = [b for b in bugs if b.force == '0']

print(f"Real violations: {len(real)}")
print(f"Note noise: {len(noise)}")
print(f"Total diagnostics: {len(bugs)}")
print()

for b in bugs:
    ck = (b.checker or "?")[:50]
    print(f"  [{b.level:7s}] {ck}")

if noise:
    print(f"\nFAIL: {len(noise)} note noise entries remain!")
    sys.exit(1)
else:
    print("\nPASS: All diagnostics are real violations, no note noise!")
