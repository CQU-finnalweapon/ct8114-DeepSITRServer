import http.client
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import dcab_client
import server


class UniPortalReportPathTests(unittest.TestCase):
    def test_actual_project_dir_uses_parent_of_named_source_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            item_root = Path(tmp) / 'item'
            source_root = item_root / 'BusinessProject' / 'code'
            source_root.mkdir(parents=True)

            actual = server._find_actual_project_dir(item_root, source_root)

            self.assertEqual(actual, source_root.parent.resolve())
            self.assertEqual(
                server._ct8114_output_dir(item_root),
                item_root.resolve() / 'ct8114',
            )
            self.assertEqual(
                server._legacy_ct8114_output_dir(item_root, source_root),
                source_root.parent.resolve() / 'ct8114',
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

    def test_collect_code_files_ignores_tool_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            item_root = Path(tmp) / 'item'
            src = item_root / 'BusinessProject'
            tool = item_root / 'ct8114'
            legacy_tool = item_root / '_ct8114'
            src.mkdir(parents=True)
            tool.mkdir()
            legacy_tool.mkdir()
            (src / 'main.c').write_text('int main(void) {}', encoding='utf-8')
            (tool / 'generated.c').write_text('int x;', encoding='utf-8')
            (legacy_tool / 'old.c').write_text('int y;', encoding='utf-8')

            files = [path.name for path in server._collect_code_files(item_root)]

            self.assertEqual(files, ['main.c'])

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
            except OSError:
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
            legacy_actual_dir = project_dir / 'ct8114'
            legacy_dir = item_root / '_ct8114'
            local_dir = base / 'reports' / 'project-id'
            output_dir.mkdir(parents=True)
            legacy_actual_dir.mkdir(parents=True)
            legacy_dir.mkdir()
            local_dir.mkdir(parents=True)
            new_report = output_dir / 'last_report.json'
            legacy_actual_report = legacy_actual_dir / 'last_report.json'
            legacy_report = legacy_dir / 'last_report.json'
            local_report = local_dir / 'last_report.json'
            report_data = {
                'report': {'summary': {'total_bugs': 7, 'total_files': 2}},
                'uniportal_writeback_time': 'report-time',
            }
            for path in (new_report, legacy_actual_report, legacy_report, local_report):
                path.write_text(json.dumps(report_data), encoding='utf-8')
            (output_dir / 'meta.json').write_text(
                json.dumps({'project_name': 'new-name', 'last_analysis': 'meta-time'}),
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
                    legacy_actual_report,
                )
                legacy_actual_report.unlink()
                self.assertEqual(
                    server._find_last_report_file(item_root, 'project-id'),
                    legacy_report,
                )
                legacy_report.unlink()
                self.assertEqual(
                    server._find_last_report_file(item_root, 'project-id'),
                    local_report,
                )

    def test_existing_project_writeback_uses_project_root_and_keeps_legacy_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            item_root = Path(tmp) / 'item'
            source_root = item_root / 'BusinessProject' / 'code'
            source_root.mkdir(parents=True)
            (source_root / 'main.c').write_text('int main(void) {}', encoding='utf-8')
            legacy_dir = item_root / '_ct8114'
            legacy_dir.mkdir()
            (legacy_dir / 'last_report.json').write_text('{}', encoding='utf-8')
            (item_root / 'meta.json').write_text(
                json.dumps(
                    {
                        'project_name': 'BusinessProject',
                        'original_filename': 'BusinessProject.zip',
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
                    'project_name': 'BusinessProject',
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
            self.assertEqual(meta['project_name'], 'BusinessProject')
            self.assertEqual(meta['original_filename'], 'BusinessProject.zip')
            self.assertEqual(meta['total_bugs'], 3)
            self.assertEqual(meta['total_files'], 1)
            self.assertEqual(meta['rule_ids_count'], 12)
            self.assertEqual(meta['dcab_source_root'], str(source_root.resolve()))
            self.assertTrue((item_root / 'meta.json').exists())
            self.assertTrue(legacy_dir.exists())
            self.assertFalse((source_root.parent / 'ct8114' / 'last_report.json').exists())

    def test_writeback_also_writes_flat_rule_set_report_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            item_root = Path(tmp) / 'item'
            source_root = item_root / 'BusinessProject' / 'code'
            source_root.mkdir(parents=True)
            payload = {
                'engine': 'dcab_http',
                'selected_rule_set': 'CWE-C',
                'engine_rule_count': 130,
                'report': {
                    'project_name': 'BusinessProject',
                    'summary': {'total_bugs': 4, 'total_files': 8},
                },
            }

            server._write_back_to_uniportal(
                item_root,
                'project-id',
                payload,
                dcab_source_root=source_root,
                existing_project=True,
            )

            output_dir = item_root / 'ct8114'
            self.assertTrue((output_dir / 'last_report.json').is_file())
            self.assertTrue((output_dir / 'meta.json').is_file())
            self.assertTrue((output_dir / 'last_report_CWE-C.json').is_file())
            self.assertTrue((output_dir / 'meta_CWE-C.json').is_file())
            self.assertEqual(
                json.loads((output_dir / 'last_report_CWE-C.json').read_text(encoding='utf-8')),
                json.loads((output_dir / 'last_report.json').read_text(encoding='utf-8')),
            )
            meta = json.loads((output_dir / 'meta_CWE-C.json').read_text(encoding='utf-8'))
            self.assertEqual(meta['selected_rule_set'], 'CWE-C')
            self.assertEqual(meta['engine_rule_count'], 130)
            self.assertEqual(meta['current_rule_set_report'], 'ct8114/last_report_CWE-C.json')

    def test_project_rule_set_reports_routes_list_and_read_flat_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            portal = Path(tmp) / 'local-upload'
            item_root = portal / 'MEMS'
            output_dir = item_root / 'ct8114'
            output_dir.mkdir(parents=True)
            report = {
                'selected_rule_set': 'GJB-8114',
                'report': {'summary': {'total_bugs': 866, 'total_files': 8}},
                'engine_rule_count': 204,
            }
            meta = {
                'selected_rule_set': 'GJB-8114',
                'last_analysis': '2026-07-26T00:00:00',
                'total_bugs': 866,
                'total_files': 8,
                'engine_rule_count': 204,
            }
            (output_dir / 'last_report_GJB-8114.json').write_text(json.dumps(report), encoding='utf-8')
            (output_dir / 'meta_GJB-8114.json').write_text(json.dumps(meta), encoding='utf-8')

            with patch.object(server, 'MOCK_UNIPORTAL_DIR', tmp), \
                patch.object(server, 'UNIPORTAL_STORAGE_PATH', ''), \
                patch.object(server, 'UNIPORTAL_MODE', False):
                client = TestClient(server.app)
                listed = client.get('/projects/MEMS/reports?portal_project_id=local-upload')
                loaded = client.get('/projects/MEMS/reports/GJB-8114?portal_project_id=local-upload')
                missing = client.get('/projects/MEMS/reports/CWE-C?portal_project_id=local-upload')
                bad = client.get('/projects/MEMS/reports/BAD-RULE?portal_project_id=local-upload')

        self.assertEqual(listed.status_code, 200)
        body = listed.json()
        self.assertTrue(body['reports']['GJB-8114']['exists'])
        self.assertEqual(body['reports']['GJB-8114']['report_path'], 'ct8114/last_report_GJB-8114.json')
        self.assertEqual(body['reports']['GJB-8114']['total_bugs'], 866)
        self.assertFalse(body['reports']['CWE-C']['exists'])
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(loaded.json(), report)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(bad.status_code, 400)

    def test_existing_project_without_meta_uses_actual_directory_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_id = 'fd236878-9793-4c05-a3b0-9df1121a53b1'
            item_root = Path(tmp) / project_id
            source_root = item_root / 'BusinessProjectNew' / 'code'
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
            self.assertEqual(meta['project_name'], 'BusinessProjectNew')

            report_path = item_root / 'ct8114' / 'last_report.json'
            written = json.loads(report_path.read_text(encoding='utf-8'))
            self.assertEqual(written['report']['project_name'], source_root.parent.name)
            self.assertFalse(
                server._looks_like_uuid(written['report']['project_name'])
            )
            self.assertFalse((source_root.parent / 'ct8114' / 'last_report.json').exists())

    def test_last_report_response_replaces_project_id_with_display_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_id = 'fd236878-9793-4c05-a3b0-9df1121a53b1'
            item_root = Path(tmp) / project_id
            actual_project_dir = item_root / 'MEMS-project-new'
            output_dir = item_root / 'ct8114'
            output_dir.mkdir(parents=True)
            actual_project_dir.mkdir()
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
            self.assertEqual(returned['report']['project_name'], 'MEMS-project-new')
            self.assertNotEqual(returned['report']['project_name'], project_id)

    def test_direct_upload_writeback_uses_project_root_ct8114_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / 'saved-id'
            actual_project_dir = project_root / 'upload'
            actual_project_dir.mkdir(parents=True)
            (actual_project_dir / 'ct8114').mkdir()
            (actual_project_dir / 'ct8114' / 'meta.json').write_text(
                json.dumps({'project_name': 'upload'}),
                encoding='utf-8',
            )
            payload = {'report': {'summary': {'total_bugs': 0, 'total_files': 0}}}

            server._write_back_to_uniportal(
                project_root,
                'saved-id',
                payload,
                source_dir=actual_project_dir,
            )

            self.assertTrue((project_root / 'ct8114' / 'last_report.json').is_file())
            self.assertTrue((project_root / 'ct8114' / 'meta.json').is_file())
            self.assertFalse((actual_project_dir / 'ct8114' / 'last_report.json').exists())
            self.assertTrue((actual_project_dir / 'ct8114' / 'meta.json').is_file())
            self.assertFalse((project_root / 'meta.json').exists())
            self.assertFalse((project_root / '_ct8114').exists())

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
                project_name='UploadedProject',
                original_filename='UploadedProject.zip',
                zip_uploads=True,
                saved_paths=[],
                extract_dir=extract_dir,
                all_code_files=code_files,
                destination_root=destination,
                source='uniportal',
            )

            actual = destination / 'saved-id' / 'UploadedProject'
            self.assertEqual(Path(saved['saved_project_path']), actual.resolve())
            self.assertEqual(Path(saved['saved_project_root']), (destination / 'saved-id').resolve())
            self.assertTrue((actual / 'cj_spi.c').is_file())
            self.assertTrue((destination / 'saved-id' / 'ct8114' / 'meta.json').is_file())
            self.assertFalse((actual / 'ct8114' / 'meta.json').exists())
            self.assertFalse((destination / 'saved-id' / 'meta.json').exists())

    def test_save_zip_unique_top_directory_preserves_business_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            extract_dir = base / 'extract'
            project_source = extract_dir / 'def'
            code_dir = project_source / 'code'
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
                project_name='UploadedProject',
                original_filename='UploadedProject.zip',
                zip_uploads=True,
                saved_paths=[],
                extract_dir=extract_dir,
                all_code_files=[code_file],
                destination_root=destination,
                source='uniportal',
            )

            actual = destination / 'saved-id' / 'def'
            self.assertEqual(Path(saved['saved_project_path']), actual.resolve())
            self.assertTrue((actual / 'code' / 'main.c').is_file())
            self.assertTrue((actual / 'requirements' / 'req.md').is_file())
            self.assertTrue((destination / 'saved-id' / 'ct8114' / 'meta.json').is_file())
            self.assertFalse((actual / 'ct8114' / 'meta.json').exists())

            renamed = server._save_uploaded_project(
                request_id='request-id-2',
                project_id='saved-id-2',
                project_name='abc',
                requested_project_name='abc',
                original_filename='UploadedProject.zip',
                zip_uploads=True,
                saved_paths=[],
                extract_dir=extract_dir,
                all_code_files=[code_file],
                destination_root=destination,
                source='uniportal',
            )
            renamed_actual = destination / 'saved-id-2' / 'abc'
            self.assertEqual(Path(renamed['saved_project_path']), renamed_actual.resolve())
            self.assertTrue((renamed_actual / 'code' / 'main.c').is_file())
            self.assertTrue((destination / 'saved-id-2' / 'ct8114' / 'meta.json').is_file())
            self.assertFalse((renamed_actual / 'ct8114' / 'meta.json').exists())

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
            self.assertEqual(Path(saved['saved_project_root']), (destination / 'saved-id').resolve())
            self.assertTrue((actual / 'main.c').is_file())
            self.assertTrue((destination / 'saved-id' / 'ct8114' / 'meta.json').is_file())
            self.assertFalse((actual / 'ct8114' / 'meta.json').exists())


    def test_cleanup_dcab_runtime_dirs_only_clears_runtime_project_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            runtime_project = base / 'opt' / 'dcab' / 'project'
            runtime_project.mkdir(parents=True)
            (runtime_project / 'old.err').write_text('stale', encoding='utf-8')
            stale_dir = runtime_project / 'stale-dir'
            stale_dir.mkdir()
            (stale_dir / 'old.txt').write_text('stale', encoding='utf-8')
            dcab_program = base / 'opt' / 'dcab' / 'DeepSITRServer'
            dcab_program.write_text('binary', encoding='utf-8')
            uniportal = base / 'data' / 'uniportal'
            uniportal.mkdir(parents=True)
            report = uniportal / 'project' / 'ct8114' / 'last_report.json'
            report.parent.mkdir(parents=True)
            report.write_text('{}', encoding='utf-8')

            server._cleanup_dcab_runtime_dirs(runtime_project)

            self.assertTrue(runtime_project.is_dir())
            self.assertEqual(list(runtime_project.iterdir()), [])
            self.assertTrue(dcab_program.is_file())
            self.assertTrue(report.is_file())

    def test_start_progress_calls_are_preceded_by_runtime_cleanup(self):
        source = Path(server.__file__).read_text(encoding='utf-8')
        needle = '_cleanup_dcab_runtime_dirs()\n                started = start_progress(str(dcab_project_path))'
        project_needle = '_cleanup_dcab_runtime_dirs()\n            started = start_progress(str(dcab_project_path))'
        self.assertIn(needle, source.replace('\r\n', '\n'))
        self.assertIn(project_needle, source.replace('\r\n', '\n'))

    def test_project_analyze_cleans_runtime_before_start_progress(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'project'
            root.mkdir()
            (root / 'main.c').write_text('int main(void) { return 0; }', encoding='utf-8')

            def cleanup_stub():
                calls.append('cleanup')

            def start_stub(project_path):
                calls.append('start')
                return {'detection_id': 'detect-1'}

            with patch.object(server, 'ANALYSIS_ENGINE', 'dcab_http'), \
                patch.object(server, '_resolve_project_path', return_value=root), \
                patch.object(server, 'DCAB_SAFE_ROOT', Path(tmp) / 'safe'), \
                patch.object(server, '_cleanup_dcab_runtime_dirs', side_effect=cleanup_stub), \
                patch.object(server, 'start_progress', side_effect=start_stub):
                response = server.analyze_project('project-id', entry=None)

            if server._DCAB_ANALYSIS_LOCK.locked():
                server._DCAB_ANALYSIS_LOCK.release()
            self.assertEqual(calls[:2], ['cleanup', 'start'])
            body = json.loads(response.body.decode('utf-8'))
            self.assertEqual(body['status'], 'running')

    def test_start_progress_wraps_remote_disconnected(self):
        with patch.object(dcab_client, 'configured_rule_ids', return_value=['GJB-8114:R-1-8-1:0']), \
            patch('dcab_client.urllib.request.urlopen', side_effect=http.client.RemoteDisconnected('closed')):
            with self.assertRaises(dcab_client.DcabClientError) as ctx:
                dcab_client.start_progress('/tmp/project')

        self.assertIn('DCAB start_progress failed; DCAB may have crashed or closed the connection', str(ctx.exception))
    def test_dcab_source_file_functions_enter_report(self):
        report = dcab_client.report_from_defect_list(
            [],
            report_id='request-id',
            project_name='MEMS',
            project_path='/tmp/project',
            source_file_list=[
                {
                    'file_path': '/tmp/project/src/main.c',
                    'file_type': 'source',
                    'functions': [
                        {
                            'name': 'main',
                            'start_line': 10,
                            'start_column': 1,
                            'end_line': 12,
                            'end_column': 2,
                        }
                    ],
                }
            ],
        )

        payload = report.to_dict()
        self.assertEqual(payload['files_stats'][0]['file_path'], 'src/main.c')
        self.assertEqual(
            payload['files_stats'][0]['functions'],
            [
                {
                    'name': 'main',
                    'start_line': 10,
                    'start_column': 1,
                    'end_line': 12,
                    'end_column': 2,
                }
            ],
        )
        self.assertEqual(payload['files_stats'][0]['bug_count'], 0)

    def test_dcab_source_file_functions_null_is_compatible(self):
        report = dcab_client.report_from_defect_list(
            [],
            report_id='request-id',
            project_name='MEMS',
            project_path='/tmp/project',
            source_file_list=[{'file_path': '/tmp/project/src/main.c', 'functions': None}],
        )

        payload = report.to_dict()
        self.assertEqual(payload['files_stats'][0]['file_path'], 'src/main.c')
        self.assertEqual(payload['files_stats'][0]['functions'], [])

    def test_dcab_bugs_and_functions_merge_by_file(self):
        report = dcab_client.report_from_defect_list(
            [
                {
                    'checker': 'GJB-R-1-1-1',
                    'message': 'Required issue',
                    'required_advisory': 'Required',
                    'tracking_path_list': [
                        {
                            'file_path': '/tmp/project/src/main.c',
                            'location_start': {'line': 11, 'column': 3},
                            'descript': 'bad code',
                        }
                    ],
                }
            ],
            report_id='request-id',
            project_name='MEMS',
            project_path='/tmp/project',
            source_file_list=[
                {
                    'file_path': '/tmp/project/src/main.c',
                    'functions': [{'name': 'main', 'start_line': 10}],
                }
            ],
        )

        payload = report.to_dict()
        self.assertEqual(len(payload['files_stats']), 1)
        file_stat = payload['files_stats'][0]
        self.assertEqual(file_stat['bug_count'], 1)
        self.assertEqual(file_stat['functions'][0]['name'], 'main')

    def test_dcab_required_and_advisory_map_to_error_warning(self):
        report = dcab_client.report_from_defect_list(
            [
                {
                    'checker': 'required-check',
                    'required_advisory': 'Required',
                    'tracking_path_list': [
                        {'file_path': '/tmp/project/a.c', 'location_start': {'line': 1, 'column': 1}}
                    ],
                },
                {
                    'checker': 'advisory-check',
                    'rule': {'level': 'Advisory'},
                    'tracking_path_list': [
                        {'file_path': '/tmp/project/b.c', 'location_start': {'line': 2, 'column': 1}}
                    ],
                },
            ],
            report_id='request-id',
            project_name='MEMS',
            project_path='/tmp/project',
        )

        bugs = report.summary()['bugs']
        by_checker = {bug['checker']: bug for bug in bugs}
        self.assertEqual(by_checker['required-check']['level'], 'Error')
        self.assertEqual(by_checker['required-check']['severity'], 'required')
        self.assertEqual(by_checker['required-check']['severity_label'], 'Required')
        self.assertEqual(by_checker['required-check']['raw_severity'], 'Required')
        self.assertEqual(by_checker['required-check']['severity_source'], 'dcab.required_advisory')
        self.assertEqual(by_checker['advisory-check']['level'], 'Warning')
        self.assertEqual(by_checker['advisory-check']['severity'], 'advisory')
        self.assertEqual(by_checker['advisory-check']['severity_label'], 'Advisory')
        self.assertEqual(by_checker['advisory-check']['raw_severity'], 'Advisory')
        self.assertEqual(by_checker['advisory-check']['severity_source'], 'dcab.required_advisory')
        self.assertEqual(report.summary()['by_level'], {'Error': 1, 'Warning': 1})
        self.assertEqual(report.summary()['by_severity'], {'required': 1, 'advisory': 1})
        self.assertEqual(report.summary()['by_severity_label'], {'Required': 1, 'Advisory': 1})

    def test_dcab_severity_falls_back_to_force(self):
        report = dcab_client.report_from_defect_list(
            [
                {
                    'checker': 'force-required',
                    'force': '1',
                    'tracking_path_list': [{'file_path': '/tmp/project/a.c'}],
                },
                {
                    'checker': 'force-advisory',
                    'force': '0',
                    'tracking_path_list': [{'file_path': '/tmp/project/b.c'}],
                },
            ],
            report_id='request-id',
            project_name='MEMS',
            project_path='/tmp/project',
        )

        by_checker = {bug['checker']: bug for bug in report.summary()['bugs']}
        self.assertEqual(by_checker['force-required']['level'], 'Error')
        self.assertEqual(by_checker['force-required']['severity'], 'required')
        self.assertEqual(by_checker['force-required']['severity_source'], 'dcab.force')
        self.assertEqual(by_checker['force-advisory']['level'], 'Warning')
        self.assertEqual(by_checker['force-advisory']['severity'], 'advisory')
        self.assertEqual(by_checker['force-advisory']['severity_source'], 'dcab.force')

    def test_dcab_gjb_rule_id_prefix_maps_to_error_warning(self):
        report = dcab_client.report_from_defect_list(
            [
                {
                    'checker': 'required-rule',
                    'rule_id': 'GJB-R-1-8-5',
                    'force': '',
                    'tracking_path_list': [{'file_path': '/tmp/project/a.c'}],
                },
                {
                    'checker': 'advisory-rule',
                    'rule_id': 'GJB-A-1-1-6',
                    'force': '',
                    'tracking_path_list': [{'file_path': '/tmp/project/b.c'}],
                },
            ],
            report_id='request-id',
            project_name='MEMS',
            project_path='/tmp/project',
        )

        by_checker = {bug['checker']: bug for bug in report.summary()['bugs']}
        self.assertEqual(by_checker['required-rule']['level'], 'Error')
        self.assertEqual(by_checker['required-rule']['severity'], 'required')
        self.assertEqual(by_checker['required-rule']['raw_severity'], 'Required')
        self.assertEqual(by_checker['required-rule']['severity_source'], 'dcab.rule_id_prefix')
        self.assertEqual(by_checker['advisory-rule']['level'], 'Warning')
        self.assertEqual(by_checker['advisory-rule']['severity'], 'advisory')
        self.assertEqual(by_checker['advisory-rule']['raw_severity'], 'Advisory')
        self.assertEqual(by_checker['advisory-rule']['severity_source'], 'dcab.rule_id_prefix')
        self.assertEqual(report.summary()['by_level'], {'Error': 1, 'Warning': 1})
        self.assertEqual(report.summary()['by_severity'], {'required': 1, 'advisory': 1})
        self.assertEqual(report.summary()['by_severity_label'], {'Required': 1, 'Advisory': 1})

    def test_dcab_gjb_message_prefix_maps_to_error_warning(self):
        report = dcab_client.report_from_defect_list(
            [
                {
                    'checker': 'message-required',
                    'message': 'violates GJB-R-1-8-5 rule',
                    'tracking_path_list': [{'file_path': '/tmp/project/a.c'}],
                },
                {
                    'checker': 'message-advisory',
                    'message': 'violates GJB-A-1-1-6 rule',
                    'tracking_path_list': [{'file_path': '/tmp/project/b.c'}],
                },
            ],
            report_id='request-id',
            project_name='MEMS',
            project_path='/tmp/project',
        )

        by_checker = {bug['checker']: bug for bug in report.summary()['bugs']}
        self.assertEqual(by_checker['message-required']['level'], 'Error')
        self.assertEqual(by_checker['message-required']['severity_source'], 'dcab.rule_id_prefix')
        self.assertEqual(by_checker['message-advisory']['level'], 'Warning')
        self.assertEqual(by_checker['message-advisory']['severity_source'], 'dcab.rule_id_prefix')

    def test_dcab_unknown_without_force_type_status_is_not_warning(self):
        report = dcab_client.report_from_defect_list(
            [
                {
                    'checker': 'unknown-check',
                    'rule_id': 'OTHER-1',
                    'message': 'no known severity marker',
                    'tracking_path_list': [{'file_path': '/tmp/project/a.c'}],
                }
            ],
            report_id='request-id',
            project_name='MEMS',
            project_path='/tmp/project',
        )

        bug = report.summary()['bugs'][0]
        self.assertEqual(bug['level'], 'Unknown')
        self.assertEqual(bug['severity'], 'unknown')
        self.assertEqual(bug['severity_label'], 'Unknown')
        self.assertEqual(report.summary()['by_level'], {'Unknown': 1})
        self.assertEqual(report.summary()['by_severity'], {'unknown': 1})
        self.assertEqual(report.summary()['by_severity_label'], {'Unknown': 1})
    def test_dcab_progress_zero_of_zero_is_not_complete(self):
        self.assertFalse(
            server._progress_info_complete(
                {'success': True, 'progress_info': {'completed_count': 0, 'total_count': 0}}
            )
        )
        self.assertFalse(
            server._progress_info_complete(
                {'progress_info': {'completed_count': 1, 'total_count': 0}}
            )
        )
        self.assertTrue(
            server._progress_info_complete(
                {'progress_info': {'completed_count': 8, 'total_count': 8}}
            )
        )

    def test_partial_dcab_report_is_not_ready_before_expected_files(self):
        report = server.DSITReport(
            report_id='request-id',
            project_name='MEMS',
            project_path='MEMS',
            files_stats=[server.DSITFileStats(file_path='cj_uart.c')],
        )
        task = {'expected_analysis_files': 8}
        ready, diagnostics = server._dcab_report_ready_for_completion(
            report,
            task,
            {'success': True, 'progress_info': {'completed_count': 0, 'total_count': 0}},
        )

        self.assertFalse(ready)
        self.assertEqual(diagnostics['expected_analysis_files'], 8)
        self.assertEqual(diagnostics['parsed_files_count'], 1)
        self.assertEqual(diagnostics['dcab_progress_info']['completed_count'], 0)
        self.assertEqual(diagnostics['dcab_progress_info']['total_count'], 0)

    def test_partial_dcab_report_with_unknown_expected_is_not_ready_on_zero_progress(self):
        report = server.DSITReport(
            report_id='request-id',
            project_name='MEMS',
            project_path='MEMS',
            files_stats=[server.DSITFileStats(file_path='cj_uart.c')],
        )
        ready, diagnostics = server._dcab_report_ready_for_completion(
            report,
            {'expected_analysis_files': 0},
            {'success': True, 'progress_info': {'completed_count': 0, 'total_count': 0}},
        )

        self.assertFalse(ready)
        self.assertEqual(diagnostics['expected_analysis_files'], 0)
        self.assertEqual(diagnostics['parsed_files_count'], 1)

    def test_dcab_report_ready_when_completed_reaches_expected_files(self):
        report = server.DSITReport(
            report_id='request-id',
            project_name='MEMS',
            project_path='MEMS',
            files_stats=[server.DSITFileStats(file_path='cj_uart.c')],
        )
        task = {'expected_analysis_files': 8}
        ready, diagnostics = server._dcab_report_ready_for_completion(
            report,
            task,
            {'progress_info': {'completed_count': 8, 'total_count': 0}},
        )

        self.assertTrue(ready)
        self.assertEqual(diagnostics['parsed_files_count'], 1)

    def test_writeback_meta_keeps_dcab_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            item_root = Path(tmp) / 'item'
            source_root = item_root / 'BusinessProject' / 'code'
            source_root.mkdir(parents=True)
            (source_root / 'main.c').write_text('int main(void) {}', encoding='utf-8')
            payload = {
                'engine': 'dcab_http',
                'dcab_source_root': str(source_root.resolve()),
                'dcab_project_path': '/tmp/dcab-safe/project',
                'expected_analysis_files': 8,
                'parsed_files_count': 8,
                'dcab_progress_info': {'completed_count': 8, 'total_count': 8},
                'report': {
                    'project_name': 'BusinessProject',
                    'summary': {'total_bugs': 0, 'total_files': 8},
                },
            }

            server._write_back_to_uniportal(
                item_root,
                'project-id',
                payload,
                dcab_source_root=source_root,
                existing_project=True,
            )

            meta = json.loads((item_root / 'ct8114' / 'meta.json').read_text(encoding='utf-8'))
            self.assertEqual(meta['expected_analysis_files'], 8)
            self.assertEqual(meta['parsed_files_count'], 8)
            self.assertEqual(meta['dcab_progress_info']['completed_count'], 8)
            self.assertEqual(meta['dcab_project_path'], '/tmp/dcab-safe/project')

    def test_projects_routes_are_registered(self):
        route_paths = {getattr(route, 'path', '') for route in server.app.routes}
        self.assertIn('/projects', route_paths)
        self.assertIn('/projects/{project_id}/files', route_paths)
        self.assertIn('/projects/{project_id}/analyze', route_paths)
        self.assertIn('/status/{request_id}', route_paths)

    def test_get_projects_with_portal_project_id_returns_200_and_projects_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            portal_root = Path(tmp) / 'localtest-ct8114-v6'
            project_root = portal_root / 'mems_project_001' / 'MEMS'
            project_root.mkdir(parents=True)
            (project_root / 'main.c').write_text('int main(void) { return 0; }', encoding='utf-8')
            with patch.object(server, 'MOCK_UNIPORTAL_DIR', tmp), \
                patch.object(server, 'UNIPORTAL_STORAGE_PATH', ''), \
                patch.object(server, 'UNIPORTAL_MODE', False):
                response = TestClient(server.app).get('/projects?portal_project_id=localtest-ct8114-v6')

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('projects', data)
        self.assertEqual(data['portal_project_id'], 'localtest-ct8114-v6')
        self.assertTrue(any(item['project_id'] == 'mems_project_001' for item in data['projects']))

    def test_get_project_files_route_returns_source_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'project'
            root.mkdir()
            (root / 'main.c').write_text('int main(void) { return 0; }', encoding='utf-8')
            with patch.object(server, '_resolve_project_path', return_value=root):
                response = TestClient(server.app).get('/projects/project-id/files')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['files'], ['main.c'])

    def test_source_files_route_scans_source_root_and_merges_report_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            portal = Path(tmp) / 'portal-a'
            item = portal / 'mems_project_001'
            source_root = item / 'MEMS' / 'code'
            ct8114 = item / 'ct8114'
            source_root.mkdir(parents=True)
            ct8114.mkdir(parents=True)
            (source_root / 'main.c').write_text('int main(void) { return 0; }', encoding='utf-8')
            (source_root / 'clean.h').write_text('#pragma once\n', encoding='utf-8')
            (ct8114 / 'meta.json').write_text(
                json.dumps({'dcab_source_root': str(source_root.resolve())}),
                encoding='utf-8',
            )
            (ct8114 / 'last_report.json').write_text(
                json.dumps(
                    {
                        'report': {
                            'files_stats': [
                                {
                                    'file_path': 'main.c',
                                    'bug_count': 1,
                                    'function_count': 1,
                                    'bugs': [{'line': 1, 'column': 1}],
                                    'functions': [{'name': 'main'}],
                                }
                            ]
                        }
                    }
                ),
                encoding='utf-8',
            )

            with patch.object(server, 'MOCK_UNIPORTAL_DIR', tmp), \
                patch.object(server, 'UNIPORTAL_STORAGE_PATH', ''), \
                patch.object(server, 'UNIPORTAL_MODE', False):
                response = TestClient(server.app).get(
                    '/projects/mems_project_001/source-files?portal_project_id=portal-a'
                )

        self.assertEqual(response.status_code, 200)
        files = {item['file_path']: item for item in response.json()['files']}
        self.assertEqual(set(files), {'clean.h', 'main.c'})
        self.assertFalse(files['clean.h']['has_report'])
        self.assertEqual(files['main.c']['bug_count'], 1)
        self.assertEqual(files['main.c']['function_count'], 1)

    def test_source_route_returns_lines_report_data_and_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            portal = Path(tmp) / 'portal-a'
            item = portal / 'mems_project_001'
            source_root = item / 'MEMS' / 'code'
            ct8114 = item / 'ct8114'
            source_root.mkdir(parents=True)
            ct8114.mkdir(parents=True)
            (source_root / 'main.c').write_text('int main(void) {\n  return 0;\n}\n', encoding='utf-8')
            (ct8114 / 'meta.json').write_text(
                json.dumps({'dcab_source_root': str(source_root.resolve())}),
                encoding='utf-8',
            )
            bug = {
                'checker': 'GJB-8114:R-1-11-2:0',
                'rule_id': 'GJB-R-1-11-2',
                'level': 'Error',
                'message': 'message',
                'line': 2,
                'column': 3,
                'type_code': 'outbreak_point',
            }
            function = {
                'name': 'main',
                'start_line': 1,
                'start_column': 1,
                'end_line': 3,
                'end_column': 1,
            }
            (ct8114 / 'last_report.json').write_text(
                json.dumps({'report': {'files_stats': [{'file_path': './main.c', 'bugs': [bug], 'functions': [function]}]}}),
                encoding='utf-8',
            )

            with patch.object(server, 'MOCK_UNIPORTAL_DIR', tmp), \
                patch.object(server, 'UNIPORTAL_STORAGE_PATH', ''), \
                patch.object(server, 'UNIPORTAL_MODE', False):
                response = TestClient(server.app).get(
                    '/projects/mems_project_001/source'
                    '?portal_project_id=portal-a&file_path=main.c&line=2&column=3'
                )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['file_path'], 'main.c')
        self.assertEqual(data['encoding'], 'utf-8')
        self.assertEqual(data['target'], {'line': 2, 'column': 3})
        self.assertEqual(data['lines'][0], {'line': 1, 'text': 'int main(void) {'})
        self.assertEqual(data['bugs'], [bug])
        self.assertEqual(data['functions'], [function])

    def test_source_route_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            portal = Path(tmp) / 'portal-a'
            item = portal / 'mems_project_001'
            source_root = item / 'MEMS' / 'code'
            ct8114 = item / 'ct8114'
            source_root.mkdir(parents=True)
            ct8114.mkdir(parents=True)
            (ct8114 / 'meta.json').write_text(
                json.dumps({'dcab_source_root': str(source_root.resolve())}),
                encoding='utf-8',
            )

            with patch.object(server, 'MOCK_UNIPORTAL_DIR', tmp), \
                patch.object(server, 'UNIPORTAL_STORAGE_PATH', ''), \
                patch.object(server, 'UNIPORTAL_MODE', False):
                response = TestClient(server.app).get(
                    '/projects/mems_project_001/source'
                    '?portal_project_id=portal-a&file_path=../secret.c'
                )

        self.assertEqual(response.status_code, 400)

    def test_status_running_returns_cached_task_without_polling_dcab(self):
        request_id = 'async-running-cache'
        server._TASK_STORE[request_id] = {
            'request_id': request_id,
            'status': 'running',
            'engine': 'dcab_http',
            'progress_info': {'completed_count': 1, 'total_count': 8},
            'created_at': time.time(),
            'updated_at': time.time(),
        }
        try:
            with patch.object(server, '_poll_dcab_http_task', side_effect=AssertionError('status must be cache-only')):
                started = time.time()
                response = server.get_analysis_status(request_id)
                elapsed = time.time() - started
            body = json.loads(response.body.decode('utf-8'))
            self.assertEqual(body['status'], 'running')
            self.assertEqual(body['progress_info']['completed_count'], 1)
            self.assertLess(elapsed, 1)
        finally:
            server._TASK_STORE.pop(request_id, None)

    def test_dcab_worker_polls_and_updates_progress(self):
        request_id = 'async-worker-progress'
        server._TASK_STORE[request_id] = {
            'request_id': request_id,
            'status': 'running',
            'engine': 'dcab_http',
            'created_at': time.time(),
            'updated_at': time.time(),
        }
        calls = []

        def poll_stub(task):
            calls.append(task['request_id'])
            if len(calls) == 1:
                server._set_task_status(
                    request_id,
                    'running',
                    progress_info={'completed_count': 1, 'total_count': 2},
                    dcab_progress_info={'completed_count': 1, 'total_count': 2},
                    completed_count=1,
                    total_count=2,
                    message='DCAB analyzing: 1/2',
                )
            else:
                server._set_task_status(request_id, 'completed', payload={'ok': True})
            return dict(server._TASK_STORE[request_id])

        old_interval = server.DCAB_POLL_INTERVAL_SECONDS
        server.DCAB_POLL_INTERVAL_SECONDS = 0.01
        try:
            with patch.object(server, '_poll_dcab_http_task', side_effect=poll_stub):
                server._run_dcab_task_worker(request_id)
            self.assertGreaterEqual(len(calls), 2)
            self.assertEqual(server._TASK_STORE[request_id]['status'], 'completed')
            self.assertEqual(server._TASK_STORE[request_id]['payload'], {'ok': True})
        finally:
            server.DCAB_POLL_INTERVAL_SECONDS = old_interval
            server._TASK_STORE.pop(request_id, None)

    def test_start_dcab_task_worker_starts_only_once_per_request_id(self):
        request_id = 'async-worker-once'
        server._TASK_STORE[request_id] = {
            'request_id': request_id,
            'status': 'running',
            'engine': 'dcab_http',
            'created_at': time.time(),
            'updated_at': time.time(),
            'dcab_worker_started': False,
        }
        starts = []

        class FakeThread:
            def __init__(self, target, args, daemon):
                self.target = target
                self.args = args
                self.daemon = daemon

            def start(self):
                starts.append((self.target, self.args, self.daemon))

        try:
            with patch.object(server.threading, 'Thread', FakeThread):
                self.assertTrue(server._start_dcab_task_worker(request_id))
                self.assertFalse(server._start_dcab_task_worker(request_id))
            self.assertEqual(len(starts), 1)
            self.assertIs(starts[0][0], server._run_dcab_task_worker)
        finally:
            server._TASK_STORE.pop(request_id, None)

    def test_status_completed_returns_payload_without_polling(self):
        request_id = 'async-completed-cache'
        server._TASK_STORE[request_id] = {
            'request_id': request_id,
            'status': 'completed',
            'engine': 'dcab_http',
            'payload': {'report': {'summary': {'total_bugs': 0}}},
            'created_at': time.time(),
            'updated_at': time.time(),
        }
        try:
            with patch.object(server, '_poll_dcab_http_task', side_effect=AssertionError('status must be cache-only')):
                response = server.get_analysis_status(request_id)
            body = json.loads(response.body.decode('utf-8'))
            self.assertEqual(body['status'], 'completed')
            self.assertEqual(body['payload']['report']['summary']['total_bugs'], 0)
        finally:
            server._TASK_STORE.pop(request_id, None)

    def test_status_failed_returns_clear_error_without_polling(self):
        request_id = 'async-failed-cache'
        server._TASK_STORE[request_id] = {
            'request_id': request_id,
            'status': 'failed',
            'engine': 'dcab_http',
            'error': {'detail': 'DCAB may have crashed or closed the connection', 'status_code': 502},
            'created_at': time.time(),
            'updated_at': time.time(),
        }
        try:
            with patch.object(server, '_poll_dcab_http_task', side_effect=AssertionError('status must be cache-only')):
                response = server.get_analysis_status(request_id)
            body = json.loads(response.body.decode('utf-8'))
            self.assertEqual(body['status'], 'failed')
            self.assertIn('DCAB may have crashed', body['error']['detail'])
        finally:
            server._TASK_STORE.pop(request_id, None)

    def test_timeout_configs_exist_and_are_env_overridable(self):
        env = os.environ.copy()
        env.update({
            'CODETIDY_TIMEOUT': '5',
            'TASK_TTL_SECONDS': '9',
            'DCAB_POLL_INTERVAL_SECONDS': '0.25',
            'DCAB_REQUEST_TIMEOUT': '7',
        })
        script = (
            'import json, server, dcab_client; '
            'print(json.dumps({'
            '"codetidy": server.CODETIDY_TIMEOUT, '
            '"ttl": server.TASK_TTL_SECONDS, '
            '"poll": server.DCAB_POLL_INTERVAL_SECONDS, '
            '"request": dcab_client.get_dcab_config()["timeout"]'
            '}))'
        )
        output = subprocess.check_output(
            [sys.executable, '-c', script],
            cwd=str(Path(server.__file__).resolve().parent),
            env=env,
        )
        config = json.loads(output.decode('utf-8'))
        self.assertEqual(config, {'codetidy': 5, 'ttl': 9, 'poll': 0.25, 'request': 7})
if __name__ == '__main__':
    unittest.main()





