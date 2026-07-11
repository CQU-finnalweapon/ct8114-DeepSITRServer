"""快速验证共享卷写回路径的脚本。

模拟 UniPortal 共享卷结构，调用 _write_back_to_uniportal 后检查文件位置。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

# 设置 mock 环境变量
os.environ["MOCK_UNIPORTAL_DIR"] = ""  # 不依赖 mock，直接用临时目录模拟
os.environ["DCAB_RULE_IDS"] = "DEMO"   # 跳过规则文件加载

# 添加 app 目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parent))

import server


def test_existing_project_writeback():
    """测试：已有项目重扫的写回路径是否为 item_root/ct8114/"""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        item_root = base / "portal-proj-001" / "proj-uuid-123"
        # 模拟项目源码目录
        source_dir = item_root / "MEMS陀螺软件-new" / "代码"
        source_dir.mkdir(parents=True)
        (source_dir / "main.c").write_text("int main(void) {}", encoding="utf-8")
        
        # 模拟旧路径残留
        legacy_dir = item_root / "_ct8114"
        legacy_dir.mkdir()
        (legacy_dir / "last_report.json").write_text("{}", encoding="utf-8")
        (item_root / "meta.json").write_text(
            json.dumps({"project_name": "MEMS项目", "original_filename": "MEMS.zip"}),
            encoding="utf-8",
        )

        payload = {
            "engine": "codetidy",
            "rule_standard": "GJB8114",
            "rule_ids_count": 5,
            "report": {
                "project_name": "MEMS项目",
                "summary": {"total_bugs": 3, "total_files": 1},
            },
        }

        print("=" * 60)
        print("测试1: 已有项目重扫写回")
        print(f"  item_root: {item_root}")
        print(f"  source_dir: {source_dir}")

        result = server._write_back_to_uniportal(
            item_root,
            "proj-uuid-123",
            payload,
            dcab_source_root=source_dir,
            existing_project=True,
        )

        # ★ 验证：写回路径是 item_root/ct8114/ 而非 source_dir/../ct8114/
        expected_dir = item_root / "ct8114"
        report_path = Path(result["report_path"])
        meta_path = Path(result["meta_path"])

        print(f"  期望写回目录: {expected_dir}")
        print(f"  实际报告路径: {report_path}")
        print(f"  实际meta路径: {meta_path}")

        checks = []

        # 检查1: 报告在 item_root/ct8114/ 下
        ok1 = report_path.parent == expected_dir
        checks.append(("报告在 item_root/ct8114/ 下", ok1))
        print(f"  {'✅' if ok1 else '❌'} 报告位于 item_root/ct8114/")

        # 检查2: meta 也在 item_root/ct8114/ 下
        ok2 = meta_path.parent == expected_dir
        checks.append(("meta在 item_root/ct8114/ 下", ok2))
        print(f"  {'✅' if ok2 else '❌'} meta 位于 item_root/ct8114/")

        # 检查3: 文件确实存在
        ok3 = report_path.is_file()
        checks.append(("报告文件存在", ok3))
        print(f"  {'✅' if ok3 else '❌'} last_report.json 存在")

        ok4 = meta_path.is_file()
        checks.append(("meta文件存在", ok4))
        print(f"  {'✅' if ok4 else '❌'} meta.json 存在")

        # 检查4: 不在旧路径 source_dir.parent/ct8114/
        old_path = source_dir.parent / "ct8114"
        ok5 = not old_path.exists()
        checks.append(("不在源码子目录下", ok5))
        print(f"  {'✅' if ok5 else '❌'} 不在 {old_path}")

        # 检查5: 旧 _ct8114 已清除
        ok6 = not legacy_dir.exists()
        checks.append(("旧 _ct8114 已清除", ok6))
        print(f"  {'✅' if ok6 else '❌'} 旧 _ct8114 已清除")

        # 检查6: 旧 meta.json 已清除
        ok7 = not (item_root / "meta.json").exists()
        checks.append(("旧 meta.json 已清除", ok7))
        print(f"  {'✅' if ok7 else '❌'} 旧 meta.json 已清除")

        # 检查7: 报告内容正确
        written = json.loads(report_path.read_text(encoding="utf-8"))
        ok8 = written.get("uniportal_writeback") == "ok"
        checks.append(("报告含 writeback 标记", ok8))
        print(f"  {'✅' if ok8 else '❌'} uniportal_writeback=ok")

        # 检查8: meta 内容正确
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        ok9 = meta.get("project_name") == "MEMS项目"
        checks.append(("meta 项目名正确", ok9))
        print(f"  {'✅' if ok9 else '❌'} meta.project_name=MEMS项目")

        all_ok = all(c[1] for c in checks)
        print(f"\n  结果: {'🎉 全部通过!' if all_ok else '❌ 有失败项'}")
        return all_ok


def test_uploaded_project_writeback():
    """测试：直接上传项目的写回路径"""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        item_root = base / "portal-proj-001" / "upload-uuid-456"

        payload = {
            "engine": "codetidy",
            "rule_standard": "GJB8114",
            "rule_ids_count": 5,
            "report": {
                "project_name": "上传项目",
                "summary": {"total_bugs": 0, "total_files": 1},
            },
        }

        print("\n" + "=" * 60)
        print("测试2: 直接上传项目写回")
        print(f"  item_root: {item_root}")

        result = server._write_back_to_uniportal(
            item_root,
            "upload-uuid-456",
            payload,
            existing_project=False,
        )

        expected_dir = item_root / "ct8114"
        report_path = Path(result["report_path"])

        print(f"  期望写回目录: {expected_dir}")
        print(f"  实际报告路径: {report_path}")

        ok = report_path.parent == expected_dir and report_path.is_file()
        print(f"  {'✅' if ok else '❌'} 报告位于 item_root/ct8114/")

        return ok


def test_read_report():
    """测试：读取报告时正确查找新路径"""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        item_root = base / "portal-proj-001" / "read-uuid-789"
        source_dir = item_root / "MyProject"
        source_dir.mkdir(parents=True)
        (source_dir / "main.c").write_text("int main(void) {}", encoding="utf-8")

        # 在新的位置写入报告
        output_dir = item_root / "ct8114"
        output_dir.mkdir(parents=True)
        report_data = {
            "report": {"project_name": "MyProject", "summary": {"total_bugs": 5, "total_files": 2}},
            "uniportal_writeback": "ok",
            "uniportal_writeback_time": "2026-07-12T10:00:00",
        }
        (output_dir / "last_report.json").write_text(
            json.dumps(report_data), encoding="utf-8"
        )
        (output_dir / "meta.json").write_text(
            json.dumps({"project_name": "MyProject", "last_analysis": "2026-07-12T10:00:00"}),
            encoding="utf-8",
        )

        print("\n" + "=" * 60)
        print("测试3: 读取新路径报告")
        print(f"  item_root: {item_root}")

        # 测试 _find_last_report_file
        found = server._find_last_report_file(item_root, "read-uuid-789")
        ok1 = found is not None
        print(f"  {'✅' if ok1 else '❌'} 找到报告文件: {found}")

        # 测试 _check_analysis_status
        status = server._check_analysis_status(item_root, "read-uuid-789")
        ok2 = status.get("analyzed") == True and status.get("report_bugs") == 5
        print(f"  {'✅' if ok2 else '❌'} 分析状态: {status}")

        # 测试 _find_project_meta_file
        meta_file = server._find_project_meta_file(item_root)
        ok3 = meta_file is not None and meta_file.parent.name == "ct8114"
        print(f"  {'✅' if ok3 else '❌'} meta文件位于: {meta_file}")

        return all([ok1, ok2, ok3])


def test_directory_structure():
    """测试：验证最终目录结构是否符合预期"""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        item_root = base / "portal-proj-001" / "struct-test-uuid"
        source_dir = item_root / "MEMS陀螺软件-new" / "代码"
        source_dir.mkdir(parents=True)
        (source_dir / "main.c").write_text("int main(void) {}", encoding="utf-8")

        # 模拟其他工具目录
        (item_root / "configuration-test-case-generate").mkdir(parents=True)
        (item_root / "document-validator").mkdir(parents=True)
        (item_root / "document-validator" / "requirement.json").write_text("{}", encoding="utf-8")

        payload = {
            "engine": "codetidy",
            "rule_standard": "GJB8114",
            "rule_ids_count": 5,
            "report": {
                "project_name": "MEMS陀螺软件-new",
                "summary": {"total_bugs": 3, "total_files": 1},
            },
        }

        server._write_back_to_uniportal(
            item_root,
            "struct-test-uuid",
            payload,
            dcab_source_root=source_dir,
            existing_project=True,
        )

        print("\n" + "=" * 60)
        print("测试4: 最终目录结构")
        print(f"  {item_root}/")

        all_ok = True
        for child in sorted(item_root.iterdir()):
            is_dir = child.is_dir()
            marker = "/" if is_dir else ""
            extra = ""
            if child.name == "ct8114":
                has_report = (child / "last_report.json").is_file()
                has_meta = (child / "meta.json").is_file()
                extra = f"  ← ct8114 写回目录 (report:{'✅' if has_report else '❌'}, meta:{'✅' if has_meta else '❌'})"
                if not (has_report and has_meta):
                    all_ok = False
            print(f"    {child.name}{marker}{extra}")

        print(f"\n  结果: {'🎉 结构正确!' if all_ok else '❌ 有问题'}")
        return all_ok


if __name__ == "__main__":
    results = []
    results.append(test_existing_project_writeback())
    results.append(test_uploaded_project_writeback())
    results.append(test_read_report())
    results.append(test_directory_structure())

    print("\n" + "=" * 60)
    print(f"总结: {sum(results)}/{len(results)} 通过")
    if all(results):
        print("🎉 共享卷写回路径验证全部通过！")
    else:
        print("❌ 存在失败项，请检查")
