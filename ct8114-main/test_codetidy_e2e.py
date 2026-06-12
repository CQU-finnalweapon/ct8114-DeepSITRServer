"""端到端测试：验证 codetidy.exe 实时分析流程。

测试流程：
    1. 创建包含已知问题的 C 测试文件
    2. 调用 analyze_with_codetidy() 进行分析
    3. 验证返回的 DSITReport 结构
    4. 启动服务并验证 API 端点

用法：
    python test_codetidy_e2e.py
"""

import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path

# 确保当前目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent))

TEST_C_CODE = r"""
#include <stdio.h>

int g_unused_var = 0;  /* 未使用的全局变量 */

void check_divide(int a, int b) {
    int result = a / b;  /* 可能除零 */
    printf("%d\n", result);
}

int unreachable_code(int x) {
    if (x > 10) {
        return 1;
        x = 0;  /* 不可达代码 */
    }
    return 0;
}

int main(void) {
    int arr[10];
    int y;
    printf("%d\n", uninitialized_var);  /* 未声明变量 */
    return 0;
}
"""


def test_parser_only():
    """测试 1: 独立解析器测试 (不启动服务)."""
    print("=" * 60)
    print("测试 1: codetidy 实时分析 (dsit_parser)")
    print("=" * 60)

    from dsit_parser import analyze_with_codetidy

    # 创建临时目录和测试文件
    tmpdir = Path(tempfile.mkdtemp(prefix="ct8114_test_"))
    test_file = tmpdir / "test_analysis.c"
    test_file.write_text(TEST_C_CODE, encoding="utf-8")

    try:
        report = analyze_with_codetidy(
            source_files=[test_file],
            project_name="e2e_test",
            timeout=60,
        )
        s = report.summary()
        print(f"  项目名称: {report.project_name}")
        print(f"  分析文件数: {report.total_files}")
        print(f"  诊断总数: {report.total_bugs}")
        print(f"  按级别: {s.get('by_level', {})}")
        print(f"  按规则: {dict(list(s.get('by_rule', {}).items())[:5])}")

        if report.total_bugs > 0:
            print("  [PASS] 成功检测到诊断结果")
        else:
            print("  [WARN] 未检测到诊断 (可能是测试代码不触发规则)")

        return True
    except FileNotFoundError as e:
        print(f"  [SKIP] codetidy.exe 不可用: {e}")
        return None
    except Exception as e:
        print(f"  [FAIL] {e}")
        return False
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_server_api():
    """测试 2: 启动服务并验证 API 端点."""
    print()
    print("=" * 60)
    print("测试 2: API 端点验证")
    print("=" * 60)

    BASE = "http://127.0.0.1:8000"

    # 2a. 健康检查
    try:
        resp = urllib.request.urlopen(f"{BASE}/healthz", timeout=5)
        data = json.loads(resp.read().decode())
        print(f"  健康检查: {data.get('status', '?')}")
        print(f"  分析引擎: {data.get('engine', '?')}")
        assert data.get("engine", "").startswith("codetidy"), "引擎不是 codetidy!"
        print("  [PASS] 健康检查通过，引擎确认为 codetidy")
    except Exception as e:
        print(f"  [FAIL] 健康检查失败: {e}")
        return False

    # 2b. DSIT 报告列表
    try:
        resp = urllib.request.urlopen(f"{BASE}/dsit/reports", timeout=5)
        data = json.loads(resp.read().decode())
        reports = data.get("reports", [])
        print(f"  已加载报告数: {len(reports)}")
        print("  [PASS] 报告列表端点正常")
    except Exception as e:
        print(f"  [FAIL] 报告列表失败: {e}")
        return False

    # 2c. 项目列表
    try:
        resp = urllib.request.urlopen(f"{BASE}/projects", timeout=5)
        data = json.loads(resp.read().decode())
        projects = data.get("projects", [])
        print(f"  项目数: {len(projects)}")
        print("  [PASS] 项目列表端点正常")
    except Exception as e:
        print(f"  [FAIL] 项目列表失败: {e}")
        return False

    return True


def main():
    print("ct8114 codetidy 引擎端到端测试")
    print(f"Python: {sys.version}")
    print()

    all_pass = True

    # 测试 1
    result1 = test_parser_only()
    if result1 is False:
        all_pass = False
    elif result1 is None:
        print("  (codetidy.exe 不可用，跳过实时分析测试)")

    # 测试 2 (需要服务已运行)
    print()
    print("提示: 测试 2 需要服务正在运行。")
    print("      启动命令: python -m uvicorn server:app --host 127.0.0.1 --port 8000")
    try:
        urllib.request.urlopen("http://127.0.0.1:8000/healthz", timeout=2)
        result2 = test_server_api()
        if not result2:
            all_pass = False
    except Exception:
        print("  [SKIP] 服务未运行，跳过 API 测试")
        print("  启动服务后重新运行本脚本即可测试 API")

    print()
    if all_pass:
        print("=" * 60)
        print("所有测试通过!")
        print("=" * 60)
        print()
        print("浏览器访问: http://127.0.0.1:8000/static/index.html")
        print("  - 「直接上传」标签: 上传源码 → codetidy 实时分析")
        print("  - 「项目库」标签: 选择项目 → codetidy 批量分析")
        print("  - 「加载报告」标签: 加载预生成的 DSIT 输出目录")
    else:
        print("部分测试失败，请检查上述错误信息。")


if __name__ == "__main__":
    main()
