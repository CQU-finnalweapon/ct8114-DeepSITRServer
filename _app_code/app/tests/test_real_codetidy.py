"""用真实 codetidy 输出验证解析器"""
import sys
sys.path.insert(0, '..')
from dsit_parser import _parse_codetidy_output, _DIAG_LINE_RE
from pathlib import Path

# 真实 codetidy 输出（从 Docker 容器中获取）
# test_main.c → 1 个 CodeUnreachableBranch warning
# test_utils.c → 3 个 warning（1个 memset, 2个 CodeUnreachableBranch）
real_stdout = (
    "/test/test_main.c:12:15: warning: GJB-R-1-8-2 : Prohibit unreachable branches "
    "[clang-analyzer-gjb.statement.CodeUnreachableBranch]\n"
    "/test/test_main.c:21:18: note: Calling 'process_data'\n"
    "/test/test_main.c:12:15: note: GJB-R-1-8-2 : Prohibit unreachable branches\n"
    "/test/test_utils.c:7:5: warning: Call to function 'memset' is insecure "
    "[clang-analyzer-security.insecureAPI.DeprecatedOrUnsafeBufferHandling]\n"
    "/test/test_utils.c:7:5: note: Call to function 'memset'\n"
    "/test/test_utils.c:11:14: warning: GJB-R-1-8-2 : Prohibit unreachable branches "
    "[clang-analyzer-gjb.statement.CodeUnreachableBranch]\n"
    "/test/test_utils.c:11:14: note: GJB-R-1-8-2 : Prohibit unreachable branches\n"
    "/test/test_utils.c:21:9: warning: GJB-R-1-8-2 : Prohibit unreachable branches "
    "[clang-analyzer-gjb.statement.CodeUnreachableBranch]\n"
    "/test/test_utils.c:22:5: warning: Call to function 'strncpy' is insecure "
    "[clang-analyzer-security.insecureAPI.DeprecatedOrUnsafeBufferHandling]\n"
)

source_files = [Path('/test/test_main.c'), Path('/test/test_utils.c')]
bugs = _parse_codetidy_output(real_stdout, '', source_files)

print("=" * 65)
print("真实 codetidy 输出 vs 解析器")
print("=" * 65)

# 逐行展示正则匹配
print("\n--- 正则逐行匹配 ---")
for line in real_stdout.strip().splitlines():
    m = _DIAG_LINE_RE.match(line.strip())
    if m:
        lvl = m.group(4)
        checker = m.group(6) or "(none)"
        print(f"  [MATCH] {lvl:7s} | checker={checker}")
    else:
        print(f"  [SKIP]  {line[:70]}")

print(f"\n--- 解析结果 ({len(bugs)} 条) ---")
real_bugs = [b for b in bugs if b.force == '1']
note_bugs = [b for b in bugs if b.force == '0']

for b in bugs:
    tag = "REAL" if b.force == '1' else "NOISE"
    ck = (b.checker or "(no checker)")[:45]
    print(f"  [{tag:5s}] {b.level:7s} | {ck:45s} | {b.message[:50]}")

print(f"\n--- 统计 ---")
print(f"  真实违规 (warning/error → force=1): {len(real_bugs)} 条")
print(f"  note 噪音 (note → force=0):         {len(note_bugs)} 条")
print()

if note_bugs:
    print("⚠️  发现 {0} 条 note 噪音！note 行是 clang-tidy 的上下文补充信息，".format(len(note_bugs)))
    print("   不是真正的规则违规，应该过滤掉。")
