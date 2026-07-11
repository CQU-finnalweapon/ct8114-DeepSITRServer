import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class UniPortalReportPathTests(unittest.TestCase):
    def test_actual_project_dir_uses_parent_of_named_source_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            item_root = Path(tmp) / 'item'
            source_root = item_root / 'MEMS陀螺软件-new' / '代码'
            source_root.mkdir(parents=True)

            actual = server._find_actual_project_dir(item_root, source_root)

            self.assertEqual(actual, source_root.parent.resolve())
            self.assertEqual(
                server._ct8114_output_dir(item_root, source_root),
                item_root.resolve() / 'ct8114',
            )

    def test_actual_project_dir_ignores_tool_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            item_root = Path(tmp) / 'item'
            (item_root / 'ct8114').mkdir(parents=True)
            (item_root / '_ct8114').mkdir()
            business = item_root / 'BusinessProject'
            business.mkdir()

            self.assertEqual(
                server._find_actual_project_dir(item_root),
                business.resolve(),
            )

    def test_actual_project_dir_skips_symlink_outside_item_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            item_root = base / 'item'
            outside = base / 'outside'
            item_root.mkdir()
            outside.mkdir()
            link = item_root / 'BusinessProject'
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                # Windows commonly requires an elevated token for symlink
                # creation. Simulate the same resolved target so the boundary
                # check still executes in restricted CI.
                link.mkdir()
                original_resolve = Path.resolve
                resolved_link = link.absolute()
                resolved_outside = outside.resolve()

                def resolve_with_external_target(path, *args, **kwargs):
                    if path.absolute() == resolved_link:
                        return resolved_outside
                    return original_resolve(path, *args, **kwargs)

                with patch.object(Path, 'resolve', resolve_with_external_target):
                    actual = server._find_actual_project_dir(item_root)
            else:
                actual = server._find_actual_project_dir(item_root)

            self.assertEqual(actual, item_root.resolve())
            self.assertNotEqual(actual, outside.resolve())

    def test_report_and_meta_read_new_then_legacy_then_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            item_root = base / 'item'
            project_dir = item_root / 'BusinessProject'
            output_dir = item_root / 'ct8114'
            legacy_dir = item_root / '_ct8114'
            local_dir = base / 'reports' / 'project-id'
            output_dir.mkdir(parents=True)
            project_dir.mkdir(parents=True)
            legacy_dir.mkdir()
            local_dir.mkdir(parents=True)
            new_report = output_dir / 'last_report.json'
            legacy_report = legacy_dir / 'last_report.json'
            local_report = local_dir / 'last_report.json'
            report_data = {
                'report': {'summary': {'total_bugs': 7, 'total_files': 2}},
                'uniportal_writeback_time': 'report-time',
            }
            for path in (new_report, legacy_report, local_report):
                path.write_text(json.dumps(report_data), encoding='utf-8')
            (output_dir / 'meta.json').write_text(
                json.dumps(
                    {'project_name': 'new-name', 'last_analysis': 'meta-time'}
                ),
                encoding='utf-8',
            )
            (item_root / 'meta.json').write_text(
                json.dumps({'project_name': 'legacy-name'}),
                encoding='utf-8',
            )

            with patch.object(server, 'REPORTS_DIR', base / 'reports'):
                self.assertEqual(
                    server._find_last_report_file(item_root, 'project-id'),
                    new_report,
                )
                self.assertEqual(server._display_name_from_meta(item_root), 'new-name')
                self.assertEqual(
                    server._check_analysis_status(item_root, 'project-id'),
                    {
                        'analyzed': True,
                        'last_analysis': 'meta-time',
                        'report_bugs': 7,
                    },
                )
                new_report.unlink()
                self.assertEqual(
                    server._find_last_report_file(item_root, 'project-id'),
                    legacy_report,
                )
                legacy_report.unlink()
                self.assertEqual(
                    server._find_last_report_file(item_root, 'project-id'),
                    local_report,
                )


    def test_existing_project_writeback_moves_metadata_and_cleans_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            item_root = Path(tmp) / 'item'
            source_root = item_root / 'MEMS陀螺软件-new' / '代码'
            source_root.mkdir(parents=True)
            (source_root / 'main.c').write_text('int main(void) {}', encoding='utf-8')
            legacy_dir = item_root / '_ct8114'
            legacy_dir.mkdir()
            (legacy_dir / 'last_report.json').write_text('{}', encoding='utf-8')
            (item_root / 'meta.json').write_text(
                json.dumps(
                    {
                        'project_name': 'MEMS项目',
                        'original_filename': 'MEMS.zip',
                        'created_at': '2026-01-01T00:00:00',
                    }
                ),
                encoding='utf-8',
            )
            payload = {
                'engine': 'dcab_http',
                'rule_standard': 'GJB8114',
                'rule_ids_count': 12,
                'report': {
                    'project_name': 'MEMS项目',
                    'summary': {'total_bugs': 3, 'total_files': 1},
                },
            }

            result = server._write_back_to_uniportal(
                item_root,
                'project-id',
                payload,
                dcab_source_root=source_root,
                existing_project=True,
            )

            output_dir = item_root / 'ct8114'
            self.assertEqual(Path(result['report_path']), output_dir / 'last_report.json')
            meta = json.loads((output_dir / 'meta.json').read_text(encoding='utf-8'))
            self.assertEqual(meta['project_name'], 'MEMS项目')
            self.assertEqual(meta['original_filename'], 'MEMS.zip')
            self.assertEqual(meta['total_bugs'], 3)
            self.assertEqual(meta['total_files'], 1)
            self.assertEqual(meta['rule_ids_count'], 12)
            self.assertEqual(meta['dcab_source_root'], str(source_root.resolve()))
            self.assertFalse((item_root / 'meta.json').exists())
            self.assertFalse(legacy_dir.exists())

    def test_existing_project_without_meta_uses_actual_directory_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_id = 'fd236878-9793-4c05-a3b0-9df1121a53b1'
            item_root = Path(tmp) / project_id
            source_root = item_root / 'MEMS陀螺软件-new' / '代码'
            source_root.mkdir(parents=True)
            (source_root / 'main.c').write_text('int main(void) {}', encoding='utf-8')
            payload = {
                'report': {
                    'project_name': project_id,
                    'summary': {'total_bugs': 0, 'total_files': 1},
                },
            }

            server._write_back_to_uniportal(
                item_root,
                project_id,
                payload,
                dcab_source_root=source_root,
                existing_project=True,
            )

            meta_path = item_root / 'ct8114' / 'meta.json'
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
            self.assertEqual(meta['project_name'], 'MEMS陀螺软件-new')

            report_path = item_root / 'ct8114' / 'last_report.json'
            written = json.loads(report_path.read_text(encoding='utf-8'))
            self.assertEqual(written['report']['project_name'], source_root.parent.name)
            self.assertFalse(
                server._looks_like_uuid(written['report']['project_name'])
            )

    def test_last_report_response_replaces_project_id_with_display_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_id = 'fd236878-9793-4c05-a3b0-9df1121a53b1'
            item_root = Path(tmp) / project_id
            actual_project_dir = item_root / 'MEMS-project-new'
            output_dir = item_root / 'ct8114'
            output_dir.mkdir(parents=True)
            actual_project_dir.mkdir(parents=True)
            (output_dir / 'meta.json').write_text(
                json.dumps({'project_name': 'MEMS-project-new'}),
                encoding='utf-8',
            )
            (output_dir / 'last_report.json').write_text(
                json.dumps(
                    {
                        'report': {
                            'project_name': project_id,
                            'summary': {'total_bugs': 0, 'total_files': 1},
                        }
                    }
                ),
                encoding='utf-8',
            )

            with patch.object(server, '_resolve_project_path', return_value=item_root):
                response = server.get_project_last_report(project_id)

            returned = json.loads(response.body.decode('utf-8'))
            self.assertEqual(
                returned['report']['project_name'],
                'MEMS-project-new',
            )
            self.assertNotEqual(returned['report']['project_name'], project_id)

    def test_direct_upload_writeback_uses_actual_project_ct8114_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            actual_project_dir = Path(tmp) / 'saved-id' / 'upload'
            actual_project_dir.mkdir(parents=True)
            (actual_project_dir / 'ct8114').mkdir()
            (actual_project_dir / 'ct8114' / 'meta.json').write_text(
                json.dumps({'project_name': 'upload'}),
                encoding='utf-8',
            )
            payload = {'report': {'summary': {'total_bugs': 0, 'total_files': 0}}}

            server._write_back_to_uniportal(actual_project_dir, 'saved-id', payload)

            self.assertTrue((actual_project_dir / 'ct8114' / 'last_report.json').is_file())
            self.assertTrue((actual_project_dir / 'ct8114' / 'meta.json').is_file())
            self.assertFalse((actual_project_dir / 'meta.json').exists())
            self.assertFalse((actual_project_dir / '_ct8114').exists())

    def test_save_flat_zip_creates_named_actual_project_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            extract_dir = base / 'extract'
            destination = base / 'portal'
            extract_dir.mkdir()
            code_files = []
            for name in ('cj_spi.c', 'cj_uart.c', 'function.c'):
                path = extract_dir / name
                path.write_text('int value;', encoding='utf-8')
                code_files.append(path)

            saved = server._save_uploaded_project(
                request_id='request-id',
                project_id='saved-id',
                project_name='项目名',
                original_filename='项目名.zip',
                zip_uploads=True,
                saved_paths=[],
                extract_dir=extract_dir,
                all_code_files=code_files,
                destination_root=destination,
                source='uniportal',
            )

            actual = destination / 'saved-id' / '项目名'
            self.assertEqual(Path(saved['saved_project_path']), actual.resolve())
            self.assertTrue((actual / 'cj_spi.c').is_file())
            self.assertTrue((actual / 'ct8114' / 'meta.json').is_file())
            self.assertFalse((destination / 'saved-id' / 'meta.json').exists())

    def test_save_zip_unique_top_directory_preserves_business_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            extract_dir = base / 'extract'
            project_source = extract_dir / 'def'
            code_dir = project_source / '代码'
            requirements = project_source / 'requirements'
            destination = base / 'portal'
            code_dir.mkdir(parents=True)
            requirements.mkdir()
            code_file = code_dir / 'main.c'
            code_file.write_text('int main(void) {}', encoding='utf-8')
            (requirements / 'req.md').write_text('requirement', encoding='utf-8')

            saved = server._save_uploaded_project(
                request_id='request-id',
                project_id='saved-id',
                project_name='项目名',
                original_filename='项目名.zip',
                zip_uploads=True,
                saved_paths=[],
                extract_dir=extract_dir,
                all_code_files=[code_file],
                destination_root=destination,
                source='uniportal',
            )

            actual = destination / 'saved-id' / 'def'
            self.assertEqual(Path(saved['saved_project_path']), actual.resolve())
            self.assertTrue((actual / '代码' / 'main.c').is_file())
            self.assertTrue((actual / 'requirements' / 'req.md').is_file())
            self.assertTrue((actual / 'ct8114' / 'meta.json').is_file())

            renamed = server._save_uploaded_project(
                request_id='request-id-2',
                project_id='saved-id-2',
                project_name='abc',
                requested_project_name='abc',
                original_filename='项目名.zip',
                zip_uploads=True,
                saved_paths=[],
                extract_dir=extract_dir,
                all_code_files=[code_file],
                destination_root=destination,
                source='uniportal',
            )
            renamed_actual = destination / 'saved-id-2' / 'abc'
            self.assertEqual(Path(renamed['saved_project_path']), renamed_actual.resolve())
            self.assertTrue((renamed_actual / '代码' / 'main.c').is_file())
            self.assertTrue((renamed_actual / 'ct8114' / 'meta.json').is_file())

    def test_save_single_file_creates_actual_project_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            upload = base / 'upload' / 'main.c'
            upload.parent.mkdir()
            upload.write_text('int main(void) {}', encoding='utf-8')
            destination = base / 'portal'

            saved = server._save_uploaded_project(
                request_id='request-id',
                project_id='saved-id',
                project_name='main',
                original_filename='main.c',
                zip_uploads=False,
                saved_paths=[upload],
                extract_dir=None,
                all_code_files=None,
                destination_root=destination,
                source='uniportal',
            )

            actual = destination / 'saved-id' / 'main'
            self.assertEqual(Path(saved['saved_project_path']), actual.resolve())
            self.assertTrue((actual / 'main.c').is_file())
            self.assertTrue((actual / 'ct8114' / 'meta.json').is_file())


if __name__ == '__main__':
    unittest.main()
