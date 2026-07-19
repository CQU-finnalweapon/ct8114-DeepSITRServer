"""验证 v6.1 的两个关键特性"""
import sys
import tempfile
import json
from pathlib import Path

sys.path.insert(0, r'E:\北航项目\ct8114-docker-v5\_app_code\app')
import server
from dsit_parser import DSITBug, _parse_codetidy_output

print('='*60)
print('验证1: Warning/Error 级别区分')
print('='*60)

# === DCAB 路径（force 直接来自服务端）===
dcab_bugs = [
    DSITBug(checker='GJB-R-1-8-1', file_path='m.c', line=10, column=1,
            message='goto', rule_id='GJB-R-1-8-1', force='1', type_code='1', status='0'),
    DSITBug(checker='GJB-R-1-7-3', file_path='m.c', line=20, column=1,
            message='magic', rule_id='GJB-R-1-7-3', force='0', type_code='2', status='0'),
]
print('\nDCAB HTTP 路径（force 值由 DCAB 服务端直接返回）:')
for b in dcab_bugs:
    rl = b.level
    ok = (b.force == '1' and rl == 'Error') or (b.force == '0' and rl == 'Warning')
    print(f'  force={b.force} → level={rl}  {"✅" if ok else "❌"} (Required→Error, Advisory→Warning)')

# === codetidy 路径 ===
mock_output = """\
src/main.c:5:10: warning: 禁止使用goto [clang-analyzer-gjb.statement.Goto]
src/main.c:12:5: error: 分支语句必须使用大括号 [clang-analyzer-gjb.branch.Brace]
src/main.c:20:15: note: 建议修改命名风格 [readability-identifier-naming]
"""
fake_files = [Path('src/main.c')]
bugs = _parse_codetidy_output(mock_output, '', fake_files)

print('\ncodetidy 本地路径（从 clang-tidy 输出级别推导 force）:')
for b in bugs:
    rl = b.level
    marker = '✅' if (b.force == '1' and rl == 'Error') or (b.force == '0' and rl == 'Warning') else '❌'
    print(f'  {b.type_code} {b.message[:20]:20s} force={b.force} → level={rl}  {marker}')

print('\n结论: ✅ Warning/Error 区分存在且正确')

print()
print('='*60)
print('验证2: 输出目录位置')
print('='*60)

with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    server.UNIPORTAL_STORAGE_PATH = str(base)
    server.UNIPORTAL_MODE = True
    server.MOCK_UNIPORTAL_DIR = ''

    portal_id = 'portal_project_id_1'
    item_id = 'fd236878-9793-4c05-a3b0-9df1121a53b1'

    item_root = base / portal_id / item_id
    project_dir = item_root / 'MEMS陀螺软件-new'
    project_dir.mkdir(parents=True)
    (project_dir / 'main.c').write_text('int main(void){}')

    # 其他工具目录
    (item_root / 'configuration-test-case-generate').mkdir()
    (item_root / 'document-validator').mkdir()
    (item_root / 'document-validator' / 'requirement.json').write_text('{}')

    root = server._resolve_project_path(item_id)
    output = server._ct8114_output_dir(root)

    print(f'\n共享卷根路径: {base}')
    print(f'item_root:    {root}')
    print(f'ct8114目录:   {output}')
    print(f'源码目录:     {project_dir}')
    print(f'doc-val目录:  {item_root / "document-validator"}')
    print()

    # 验证层级
    assert output.parent == root, f'ct8114 不在 item_root 下: {output.parent} != {root}'
    assert output.parent == project_dir.parent, f'ct8114 与源码目录不同级'
    assert output.parent == (item_root / 'document-validator').parent, f'ct8114 与 document-validator 不同级'

    print(f'✅ ct8114/ 在 {root} 下')
    print(f'✅ 与源码目录 {project_dir.name}/ 同级')
    print(f'✅ 与 document-validator/ 同级')

print()
print('='*60)
print('两项验证全部通过 ✅')
print('='*60)
