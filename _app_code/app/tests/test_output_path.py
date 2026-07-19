"""实测共享卷输出目录结构"""
import sys
import tempfile
import json
from pathlib import Path

sys.path.insert(0, r'E:\北航项目\ct8114-docker-v5\_app_code\app')
import server

with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)

    # 覆盖配置：模拟共享卷
    server.UNIPORTAL_STORAGE_PATH = str(base)
    server.UNIPORTAL_MODE = True
    server.MOCK_UNIPORTAL_DIR = ''

    portal_id = 'b5b923e2-4c46-4da4-87fe-5ecf5ebbdf20'
    item_id = 'fd236878-9793-4c05-a3b0-9df1121a53b1'
    project_name = 'MEMS陀螺软件-new'

    # 创建共享卷目录结构
    item_root = base / portal_id / item_id
    project_dir = item_root / project_name
    project_dir.mkdir(parents=True)
    (project_dir / 'main.c').write_text('int main(void){}')

    print('=== 1. _build_item_index() ===')
    index = server._build_item_index()
    root = index.get(item_id)
    print(f'  root: {root}')
    print(f'  root == item_root: {root == item_root}')
    assert root == item_root, f'预期 item_root, 实际 {root}'

    print('\n=== 2. _resolve_project_path() ===')
    resolved = server._resolve_project_path(item_id)
    print(f'  resolved: {resolved}')
    print(f'  resolved == item_root: {resolved == item_root}')
    assert resolved == item_root

    print('\n=== 3. _ct8114_output_dir() ===')
    output_dir = server._ct8114_output_dir(resolved)
    expected = item_root / 'ct8114'
    print(f'  output_dir: {output_dir}')
    print(f'  期望: {expected}')
    print(f'  匹配: {output_dir == expected}')
    assert output_dir == expected

    print('\n=== 4. 完整模拟写回 ===')
    payload = {
        'engine': 'dcab_http',
        'rule_standard': 'GJB8114',
        'rule_ids_count': 204,
        'report': {
            'project_name': project_name,
            'summary': {'total_bugs': 10, 'total_files': 2},
        },
    }
    wb = server._write_back_to_uniportal(
        resolved, item_id, payload,
        existing_project=True,
    )
    report_path = Path(wb['report_path'])
    meta_path = Path(wb['meta_path'])
    print(f'  report_path: {report_path}')
    print(f'  meta_path: {meta_path}')
    print(f'  报告存在: {report_path.is_file()}')
    print(f'  meta存在: {meta_path.is_file()}')
    assert report_path.parent == output_dir
    assert meta_path.parent == output_dir

    print('\n=== 5. 最终目录结构 ===')
    print(f'  {base.name}/')
    print(f'    {portal_id}/')
    print(f'      {item_id}/')
    for child in sorted(item_root.iterdir()):
        if child.is_dir():
            print(f'        {child.name}/')
            for sub in sorted(child.iterdir()):
                if sub.is_file():
                    print(f'          {sub.name}')
        else:
            print(f'        {child.name}')

    print(f'\n=== 6. 结论 ===')
    print(f'  ct8114 写回目录: {output_dir}')
    print(f'    last_report.json: {output_dir / "last_report.json"}')
    print(f'    meta.json:       {output_dir / "meta.json"}')
    print(f'  (与项目源码 {project_name}/ 平级)')
    print(f'  ✅ 全部验证通过！')
