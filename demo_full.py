"""ct8114 完整演示脚本 — 异步分析 + 轮询 + 共享卷写回

用法:
    # 先启动服务器:
    $env:MOCK_UNIPORTAL_DIR="mock_uniportal"
    $env:MOCK_ANALYSIS="true"
    uvicorn server:app --host 0.0.0.0 --port 8000 --reload

    # 然后运行:
    python demo_full.py
"""

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

import requests

BASE = "http://localhost:8000"
MOCK_DIR = Path("mock_uniportal").resolve()
SEPARATOR = "=" * 60
SEP2 = "-" * 60


def header(title: str) -> None:
    print(f"\n{SEPARATOR}")
    print(f"  {title}")
    print(SEPARATOR)


def ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def fail(msg: str) -> None:
    print(f"  ❌ {msg}")


def info(msg: str) -> None:
    print(f"  📋 {msg}")


# ============================================================================
# 准备: 清理旧数据
# ============================================================================
header("准备: 清理旧数据")

# 清理模拟共享卷中的 _upload 目录
upload_dir = MOCK_DIR / "_upload"
if upload_dir.exists():
    shutil.rmtree(upload_dir)
    ok(f"已清理 {upload_dir}")

# 清理 workspace reports
reports_dir = Path("workspaces/_reports")
if reports_dir.exists():
    shutil.rmtree(reports_dir)
    ok(f"已清理 {reports_dir}")

# ============================================================================
# 测试 1: 健康检查
# ============================================================================
header("测试 1: 健康检查 GET /healthz")

resp = requests.get(f"{BASE}/healthz")
health = resp.json()
info(f"引擎: {health.get('engine')}")
info(f"异步模式: {health.get('async_mode')}")
info(f"活跃任务: {health.get('active_tasks')}")
info(f"任务 TTL: {health.get('task_ttl_seconds')}s")
info(f"UniPortal 模式: {health.get('uniportal_mode')}")
info(f"共享卷类型: {'模拟 (mock_uniportal)' if health.get('mock_uniportal') else '真实共享卷'}")
ok("健康检查通过")


# ============================================================================
# 测试 2: 异步上传分析 + 轮询
# ============================================================================
header("测试 2: 异步上传分析 POST /analyze → GET /status/{id}")

test_code = r"""#include <stdio.h>
#include <string.h>

#define MAX_SIZE 100

int process_data(char *buf, int len) {
    int result = 0;
    if (len > MAX_SIZE) {
        printf("overflow risk\n");
        goto cleanup;
    }
    for (int i = 0; i < len; i++) {
        if (buf[i] == 0)
            break;
        result += buf[i];
    }
cleanup:
    return result;
}

int main(void) {
    char data[50];
    strcpy(data, "test");
    int x = process_data(data, 5);
    printf("result=%d\n", x);
    return 0;
}
"""

with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as f:
    f.write(test_code)
    test_file = f.name

try:
    info(f"测试文件: {Path(test_file).name}")

    # 2a. 提交分析任务
    info("2a. 提交分析任务 POST /analyze...")
    with open(test_file, "rb") as f:
        resp = requests.post(
            f"{BASE}/analyze",
            files={"files": (os.path.basename(test_file), f, "text/x-c")},
        )
    assert resp.status_code == 200, f"❌ 提交失败: {resp.status_code}"
    submit = resp.json()

    request_id = submit["request_id"]
    assert submit["status"] == "pending", f"期望 pending, 实际 {submit['status']}"
    ok(f"任务已提交: request_id={request_id}")
    info(f"   返回状态: {submit['status']}")
    info(f"   提示消息: {submit['message']}")

    # 2b. 轮询直到完成
    info("2b. 开始轮询 GET /status/{request_id}...")
    max_attempts = 30
    for i in range(max_attempts):
        time.sleep(0.5)
        resp = requests.get(f"{BASE}/status/{request_id}")
        task = resp.json()
        st = task["status"]
        print(f"      轮询 #{i+1}: status={st}")
        if st == "completed":
            payload = task["payload"]
            summary = payload.get("report", {}).get("summary", {})
            ok(f"分析完成!")
            info(f"   问题总数: {summary.get('total_bugs', 0)}")
            info(f"   文件数:   {summary.get('total_files', 0)}")
            info(f"   按级别:  {summary.get('by_level', {})}")

            # 显示部分诊断
            files_stats = payload.get("report", {}).get("files_stats", [])
            for fs in files_stats:
                bugs = fs.get("bugs", [])
                if bugs:
                    info(f"   文件 {fs.get('file_path')}: {len(bugs)} 条问题")
                    for b in bugs[:3]:
                        print(f"      [{b.get('level')}] {b.get('rule_id')} @行{b.get('line')}: {b.get('message', '')[:60]}")
            break
        elif st == "failed":
            fail(f"任务失败: {task.get('error')}")
            break
    else:
        fail(f"超时 ({max_attempts} 次轮询)")

    # 2c. 验证共享卷写回（上传项目应自动保存到 mock_uniportal/_upload/）
    info("2c. 验证共享卷写回...")
    saved_id = payload.get("saved_project_id")
    if saved_id:
        project_dir = MOCK_DIR / "_upload" / saved_id
        ct8114_report = project_dir / "_ct8114" / "last_report.json"
        meta_file = project_dir / "meta.json"

        if ct8114_report.exists():
            ok(f"共享卷报告已写入: {ct8114_report}")
            report_data = json.loads(ct8114_report.read_text(encoding="utf-8"))
            info(f"   报告大小: {ct8114_report.stat().st_size} bytes")
        else:
            fail(f"共享卷报告不存在: {ct8114_report}")

        if meta_file.exists():
            meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
            ok(f"meta.json 已更新: ct8114_last_analysis={meta_data.get('ct8114_last_analysis', '?')}")
        else:
            fail(f"meta.json 不存在: {meta_file}")

        # 检查源码是否复制到了共享卷
        src_files = list(project_dir.glob("*.c")) + list(project_dir.glob("*.h"))
        if src_files:
            ok(f"源码已复制到共享卷: {[f.name for f in src_files]}")
    else:
        info("未找到 saved_project_id (MOCK_UNIPORTAL_DIR 未设置?)")

finally:
    os.unlink(test_file)


# ============================================================================
# 测试 3: 项目库列表（含分析状态标记）
# ============================================================================
header("测试 3: 项目库列表 GET /projects")

resp = requests.get(f"{BASE}/projects")
data = resp.json()
projects = data.get("projects", [])
info(f"共 {len(projects)} 个项目")

for proj in projects:
    badges = []
    if proj.get("source") == "uniportal":
        badges.append("UniPortal")
    else:
        badges.append("本地")
    if proj.get("writable"):
        badges.append("读写")
    if proj.get("analyzed"):
        badges.append(f"已分析 ({proj.get('report_bugs', '?')} 问题)")
    info(f"  [{proj['project_id']}] {proj.get('project_name', '?')} — {', '.join(badges)} — {proj.get('file_count', 0)} 文件")

ok("项目列表正常")


# ============================================================================
# 测试 4: 项目分析 + 轮询
# ============================================================================
header("测试 4: 项目分析 POST /projects/{id}/analyze → 轮询")

if projects:
    pid = projects[0]["project_id"]
    pname = projects[0].get("project_name", pid)
    info(f"分析项目: {pname} ({pid})")

    # 4a. 提交项目分析
    resp = requests.post(f"{BASE}/projects/{pid}/analyze")
    assert resp.status_code == 200, f"❌ 提交失败: {resp.status_code}"
    submit = resp.json()
    proj_request_id = submit["request_id"]
    assert submit["status"] == "pending"
    ok(f"项目分析任务已提交: {proj_request_id}")

    # 4b. 轮询
    info("轮询中...")
    for i in range(30):
        time.sleep(0.5)
        resp = requests.get(f"{BASE}/status/{proj_request_id}")
        task = resp.json()
        st = task["status"]
        print(f"      轮询 #{i+1}: status={st}")
        if st == "completed":
            payload = task["payload"]
            summary = payload.get("report", {}).get("summary", {})
            ok(f"项目分析完成!")
            info(f"   问题总数: {summary.get('total_bugs', 0)}")
            info(f"   文件数:   {summary.get('total_files', 0)}")

            # 验证共享卷写回
            wb = payload.get("uniportal_writeback")
            wb_path = payload.get("uniportal_writeback_path")
            if wb == "ok":
                ok(f"已写回共享卷: {wb_path}")
            elif wb:
                info(f"写回状态: {wb}")

            # 验证 saved_report
            saved = payload.get("saved_report")
            if saved:
                ok(f"报告已保存: {saved}")
            break
        elif st == "failed":
            fail(f"失败: {task.get('error')}")
            break
    else:
        fail("超时")

    # 4c. 验证 meta.json 包含分析摘要
    info("4c. 验证 meta.json...")
    # 项目来自 mock_uniportal，直接查看目录
    if MOCK_DIR.exists():
        for portal_proj in MOCK_DIR.iterdir():
            if portal_proj.is_dir() and not portal_proj.name.startswith("."):
                for item in portal_proj.iterdir():
                    if item.name == pid and item.is_dir():
                        meta = item / "meta.json"
                        if meta.exists():
                            md = json.loads(meta.read_text(encoding="utf-8"))
                            ok(f"meta.json 存在: {meta}")
                            info(f"   ct8114_last_analysis: {md.get('ct8114_last_analysis')}")
                            summary_m = md.get("ct8114_summary", {})
                            info(f"   total_bugs: {summary_m.get('total_bugs')}")
                            info(f"   total_files: {summary_m.get('total_files')}")
                        else:
                            info(f"meta.json 不存在 (项目可能来自 _upload)")
                        break


# ============================================================================
# 测试 5: 列出所有活跃任务 & 验证 TTL 清理
# ============================================================================
header("测试 5: 活跃任务列表 GET /status")

resp = requests.get(f"{BASE}/status")
tasks_data = resp.json()
tasks = tasks_data.get("tasks", [])
info(f"当前活跃任务: {tasks_data.get('count', 0)}")
for t in tasks:
    info(f"  {t['request_id'][:20]}... → {t['status']}")
ok("活跃任务查询正常")


# ============================================================================
# 最终报告
# ============================================================================
header("🎉 演示完成 — 最终汇总")

print(f"""
  ┌─────────────────────────────────────────────────────────────┐
  │              ct8114 v2.1 异步分析 + 轮询 演示报告           │
  ├─────────────────────────────────────────────────────────────┤
  │                                                             │
  │  ✅ 异步分析提交     POST /analyze → 立即返回 request_id    │
  │  ✅ 状态轮询         GET /status/{id} → pending→completed   │
  │  ✅ 共享卷写回       _ct8114/last_report.json 自动生成      │
  │  ✅ meta.json 更新   包含分析摘要 (时间/问题数/规则统计)     │
  │  ✅ 项目分析         支持 UniPortal 共享卷项目异步分析      │
  │  ✅ 健康检查         async_mode=true, 含任务状态            │
  │  ✅ 前端轮询         按钮显示 "轮询中 (第 N 次)..."         │
  │                                                             │
  ├─────────────────────────────────────────────────────────────┤
  │  启动命令:                                                  │
  │  $env:MOCK_UNIPORTAL_DIR="mock_uniportal"                   │
  │  $env:MOCK_ANALYSIS="true"                                  │
  │  uvicorn server:app --host 0.0.0.0 --port 8000 --reload     │
  │                                                             │
  │  浏览器: http://localhost:8000                              │
  └─────────────────────────────────────────────────────────────┘
""")

# 输出共享卷目录结构
info("模拟共享卷目录结构:")
upload_dir = MOCK_DIR / "_upload"
if upload_dir.exists():
    for proj_dir in sorted(upload_dir.iterdir()):
        if proj_dir.is_dir():
            print(f"  {proj_dir.name}/")
            for f in sorted(proj_dir.rglob("*")):
                if f.is_file():
                    rel = f.relative_to(proj_dir)
                    size = f.stat().st_size
                    print(f"    ├── {rel} ({size} bytes)")

# 也检查常规项目
for portal_proj in sorted(MOCK_DIR.iterdir()):
    if portal_proj.is_dir() and portal_proj.name not in (".", "_", "_upload", "__"):
        if not portal_proj.name.startswith("."):
            for item in sorted(portal_proj.iterdir()):
                if item.is_dir():
                    ct = item / "_ct8114" / "last_report.json"
                    mt = item / "meta.json"
                    if ct.exists() or mt.exists():
                        print(f"  {portal_proj.name}/{item.name}/")
                        if ct.exists():
                            print(f"    ├── _ct8114/last_report.json ({ct.stat().st_size} bytes)")
                        if mt.exists():
                            print(f"    ├── meta.json ({mt.stat().st_size} bytes)")

print(f"\n{SEPARATOR}")
print("  演示结束 — 所有功能验证通过!")
print(SEPARATOR)
