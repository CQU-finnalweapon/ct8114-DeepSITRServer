"""ct8114 + DeepSITRServer 集成自测脚本 (纯标准库, 无需安装依赖).

用法:
    cd ct8114-main/ct8114-main
    python test_dsit_integration.py

自动执行: 启动服务 → 加载 DeepSITRServer Test2 输出 → 验证 API → 输出浏览器链接
"""

import os
import sys
import time
import json
import subprocess
import urllib.request
import urllib.parse
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

DSIT_DIR = Path(os.environ.get(
    "DSIT_TEST_DIR",
    r"E:\北航项目\DeepSITRServer-2026-6-9\DeepSITRServer\Test2"
))

SERVER_URL = "http://localhost:8000"


def _req(method, path, data=None, timeout=30):
    url = f"{SERVER_URL}{path}"
    body = None
    if data:
        body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def http_get(path, timeout=10):
    return _req("GET", path, timeout=timeout)

def http_post(path, data=None, timeout=30):
    return _req("POST", path, data=data, timeout=timeout)


def hdr(title):
    print(f"\n{'='*55}")
    print(f"  {title}")
    print(f"{'='*55}")


def main():
    hdr("ct8114 + DeepSITRServer 集成自检")
    print(f"  DSIT 目录: {DSIT_DIR}")
    print(f"  存在: {DSIT_DIR.is_dir()}")
    if not DSIT_DIR.is_dir():
        print("  ERROR: 目录不存在!")
        sys.exit(1)

    # ---- 1. 测试解析器 ----
    hdr("1. 测试 dsit_parser.py")
    from dsit_parser import parse_output_dir
    report = parse_output_dir(DSIT_DIR, report_id="test")
    print(f"  文件数: {report.total_files}")
    print(f"  诊断数: {report.total_bugs}")
    s = report.summary()
    print(f"  按级别: {s['by_level']}")
    print(f"  按规则: {dict(list(s['by_rule'].items())[:5])}")
    assert report.total_files > 0
    assert report.total_bugs > 0
    print("  PASS")

    # ---- 2. 启动服务 ----
    hdr("2. 启动 ct8114 服务")
    p = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server:app",
         "--host", "127.0.0.1", "--port", "8000"],
        cwd=str(BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ready = False
    for i in range(20):
        try:
            code, _ = http_get("/healthz", timeout=2)
            if code == 200:
                print(f"  服务就绪 ({i+1}s)")
                ready = True
                break
        except Exception:
            pass
        time.sleep(1)
    if not ready:
        print("  启动超时!")
        p.kill()
        sys.exit(1)

    try:
        # ---- 3. 测试 /healthz ----
        hdr("3. 测试 /healthz")
        code, data = http_get("/healthz")
        print(f"  {code}: {data}")
        assert code == 200
        print("  PASS")

        # ---- 4. 测试 upload-local ----
        hdr("4. 测试 POST /dsit/upload-local")
        code, data = http_post("/dsit/upload-local", {
            "local_path": str(DSIT_DIR),
            "report_name": "Test2-GJB8114",
        })
        print(f"  status={data.get('status')}")
        report_id = data.get("report_id")
        s = data.get("summary", {})
        print(f"  report_id={report_id}")
        print(f"  bugs={s.get('total_bugs')}, files={s.get('total_files')}")
        assert code == 200 and report_id
        print("  PASS")

        # ---- 5. 测试报告获取 ----
        hdr("5. 测试 GET /dsit/report/{id}")
        code, data = http_get(f"/dsit/report/{report_id}")
        bugs = data.get("summary", {}).get("bugs", [])
        print(f"  诊断明细数: {len(bugs)}")
        for i, bug in enumerate(bugs[:5], 1):
            print(f"    {i}. [{bug.get('level')}] {bug.get('rule_id','')[:50]}")
            print(f"       {bug.get('file_path','?')}:{bug.get('line','?')}:{bug.get('column','?')}")
        assert len(bugs) > 0
        print("  PASS")

        # ---- 6. 测试报告列表 ----
        hdr("6. 测试 GET /dsit/reports")
        code, data = http_get("/dsit/reports")
        items = data.get("reports", [])
        for item in items:
            print(f"    {item['report_name']} ({item['total_bugs']}诊断)")
        print("  PASS")

        # ---- 完成 ----
        hdr("ALL TESTS PASSED")
        print(f"""
  浏览器打开: {SERVER_URL}/static/index.html

  操作步骤:
    1. 打开页面 → 点击顶部「DSIT 报告」标签
    2. 在「本地路径」输入框粘贴:
       {DSIT_DIR}
    3. 点击「从本地路径加载」
    4. 在报告列表点击报告名称查看诊断详情
""")
    finally:
        p.kill()
        print("  服务已停止")


if __name__ == "__main__":
    main()
