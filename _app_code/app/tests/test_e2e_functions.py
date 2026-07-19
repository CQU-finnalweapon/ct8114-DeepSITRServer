"""端到端测试：从 test.zip 提取源码 → 解析函数定位 → 生成 JSON 报告

验证:
  1. functions 字段是否正确写入最终 JSON
  2. warning/error 级别映射逻辑
"""
import json
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, r'E:\北航项目\ct8114-docker-v5\_app_code\app')

from dsit_parser import (
    DSITReport, DSITFileStats, DSITBug, DSITFunction,
    _parse_functions_from_source, _parse_codetidy_output,
    analyze_with_codetidy,
)

# ============================================================================
# 步骤 1: 解压 test.zip
# ============================================================================
zip_path = Path(r'E:\北航项目\test.zip')
tmp_dir = Path(tempfile.mkdtemp(prefix='ct8114_test_'))
print(f'[1] 解压 {zip_path.name} → {tmp_dir}')

with zipfile.ZipFile(zip_path, 'r') as zf:
    zf.extractall(tmp_dir)

# 收集所有 C 源文件
code_files = []
for p in sorted(tmp_dir.rglob('*')):
    if p.is_file() and p.suffix.lower() in {'.c', '.h'}:
        code_files.append(p)
        # 打印前几行
        content = p.read_text(encoding='utf-8', errors='replace')
        first_lines = content.strip().split('\n')[:5]
        print(f'  {p.relative_to(tmp_dir)}:')
        for line in first_lines:
            print(f'    {line}')
        print()

# ============================================================================
# 步骤 2: 模拟 codetidy 输出并解析（验证 warning/error 逻辑）
# ============================================================================
print('[2] 模拟 codetidy 诊断输出并验证级别映射')

# 模拟 codetidy 标准输出（clang-tidy 格式）
mock_stdout = """\
src/main.c:5:10: warning: 禁止使用 goto 语句 [clang-analyzer-gjb.statement.Goto]
src/main.c:12:5: error: 分支语句必须使用大括号 [clang-analyzer-gjb.branch.Brace]
src/main.c:20:15: warning: 禁止使用魔数 [clang-analyzer-gjb.style.MagicNumber]
src/control.c:8:3: note: 函数圈复杂度较高 [clang-analyzer-gjb.complexity.CycloCheck]
src/control.c:15:1: warning: 字符串操作应使用安全函数 [clang-analyzer-gjb.security.SafeString]
"""

bugs = _parse_codetidy_output(mock_stdout, '', code_files)
print(f'  解析到 {len(bugs)} 条诊断:')
for b in bugs:
    print(f'    {b.file_path}:{b.line}: {b.level} ({b.level}=Error ↔ force={b.force}) — {b.message[:50]}')

# 验证级别映射
print()
print('  === 级别映射规则验证 ===')
for b in bugs:
    expected_level = "Error" if b.force == "1" else "Warning"
    status = "✅" if b.level == expected_level else "❌"
    # 对于 codetidy: error→force=1→Error, warning→force=1→Error, note→force=0→Warning
    print(f'    {status} codetidy级别={b.type_code} force={b.force} → level={b.level} (期望={expected_level})')

# ============================================================================
# 步骤 3: 解析每个源文件的函数定位信息
# ============================================================================
print(f'\n[3] 解析函数定位信息')

all_functions: dict = {}
for cf in code_files:
    fns = _parse_functions_from_source(cf)
    rel_path = cf.relative_to(tmp_dir)
    all_functions[str(cf)] = fns
    print(f'  {rel_path}: {len(fns)} 个函数')
    for fn in fns:
        print(f'    {fn.name}: L{fn.start_line}-{fn.end_line}, C{fn.start_column}-{fn.end_column}')

# ============================================================================
# 步骤 4: 构建完整 DSITReport 并输出 JSON
# ============================================================================
print(f'\n[4] 构建完整报告 JSON')

report = DSITReport(
    report_id='test_report_001',
    project_name='gjb8114_demo_project',
    project_path=str(tmp_dir),
)

# 按文件分组 bugs
file_bugs: dict = {}
for b in bugs:
    file_bugs.setdefault(b.file_path, []).append(b)

for cf in code_files:
    short_name = cf.name
    cf_bugs = file_bugs.get(str(cf), [])
    cf_fns = all_functions.get(str(cf), [])
    report.files_stats.append(DSITFileStats(
        file_path=short_name,
        bugs=cf_bugs,
        functions=cf_fns,
    ))

# 序列化为 JSON
report_json = report.to_dict()
json_str = json.dumps(report_json, ensure_ascii=False, indent=2)

# 保存到文件
output_path = tmp_dir / 'test_report_output.json'
output_path.write_text(json_str, encoding='utf-8')
print(f'  报告已保存到: {output_path}')

# ============================================================================
# 步骤 5: 验证关键字段
# ============================================================================
print(f'\n[5] 验证 JSON 关键字段')

summary = report_json.get('summary', {})
print(f'  total_bugs: {summary.get("total_bugs")}')
print(f'  total_files: {summary.get("total_files")}')
print(f'  by_level: {summary.get("by_level")}')
print(f'  functions count: {len(summary.get("functions", []))}')

all_ok = True
for fs in report_json.get('files_stats', []):
    fname = fs.get('file_path', '?')
    fns = fs.get('functions', [])
    bugs_count = len(fs.get('bugs', []))
    print(f'\n  文件: {fname}')
    print(f'    诊断数: {bugs_count}')
    print(f'    函数数: {len(fns)}')

    if fns:
        for fn in fns:
            has_name = bool(fn.get('name'))
            has_start = fn.get('start_line', 0) > 0
            has_end = fn.get('end_line', 0) > 0
            ok = has_name and has_start and has_end
            if not ok:
                all_ok = False
            marker = '✅' if ok else '❌'
            print(f'      {marker} {fn["name"]}: L{fn["start_line"]}-{fn["end_line"]}')
    else:
        # header 文件可能没有函数定义，这是正常的
        if cf.suffix.lower() == '.h':
            print(f'      (头文件，无函数定义 — 正常)')
        else:
            print(f'      ⚠️ 无函数定义（请检查）')

print(f'\n{"="*60}')
print(f'整体结果: {"🎉 全部通过" if all_ok else "❌ 存在问题"}')
print(f'报告文件: {output_path}')
print(f'临时目录: {tmp_dir} (可手动删除)')
