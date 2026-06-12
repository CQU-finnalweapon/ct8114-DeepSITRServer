"""DeepSITRServer 输出解析 + codetidy.exe 分析引擎模块。

本模块是 ct8114 的核心分析层，提供两大功能：

A. DeepSITRServer 输出解析（兼容已有 DSIT 输出目录）：
    parse_xplusx_err(filepath) -> List[Dict]     解析 .xplusx.err JSON
    parse_sta(filepath)        -> Dict           解析 .sta 文件统计
    parse_output_dir(dirpath)  -> DSITReport    递归扫描整个输出目录

B. codetidy.exe 实时分析（替代 clang-tidy，作为唯一分析引擎）：
    analyze_with_codetidy()    -> DSITReport    运行 codetidy.exe 分析源码并返回报告
    run_codetidy()             -> CompletedProcess  底层 codetidy.exe 调用

数据模型：
    DSITReport  ─ 一次分析的完整报告（统计 + 文件明细 + 诊断汇总）
    DSITBug     ─ 单条诊断结果
    DSITFileStats ─ 单文件统计信息
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


# ============================================================================
# 数据模型
# ============================================================================

@dataclass
class DSITBug:
    """单条诊断结果，与前端 diag 卡片字段对齐."""
    checker: str           # 检查器名称, 如 clang-analyzer-gjb.statement.CodeUnreachableBranch
    file_path: str         # 源文件路径
    line: int              # 行号
    column: int            # 列号
    message: str           # 诊断消息（含规则编号）
    rule_id: str           # 规则编号, 如 GJB-R-1-8-2
    force: str             # 强制级别: "1"=强制, "0"=推荐
    type_code: str         # 类型代码: "2"=warning, "1"=error
    status: str            # 状态

    @property
    def level(self) -> str:
        """映射为前端兼容的级别."""
        if self.force == "1":
            return "Error"
        return "Warning"


@dataclass
class DSITFileStats:
    """单文件统计信息."""
    file_path: str                    # 源文件相对/绝对路径
    total_lines: int = 0
    total_statements: int = 0
    total_declares: int = 0
    function_count: int = 0
    function_max_lines: int = 0
    function_max_depth: int = 0
    comment_lines: int = 0
    code_size: int = 0
    bugs: List[DSITBug] = field(default_factory=list)


@dataclass
class DSITReport:
    """一次 DeepSITRServer 分析的完整报告."""
    report_id: str
    project_name: str                  # 项目名称
    project_path: str                  # 原始项目路径
    files_stats: List[DSITFileStats] = field(default_factory=list)

    @property
    def total_bugs(self) -> int:
        return sum(len(fs.bugs) for fs in self.files_stats)

    @property
    def total_files(self) -> int:
        return len(self.files_stats)

    def summary(self) -> Dict[str, Any]:
        """生成给前端展示的聚合摘要."""
        by_checker: Dict[str, int] = {}
        by_level: Dict[str, int] = {}
        by_file: Dict[str, int] = {}
        by_rule: Dict[str, int] = {}
        all_bugs: List[Dict] = []

        for fs in self.files_stats:
            by_file[fs.file_path] = len(fs.bugs)
            for bug in fs.bugs:
                by_checker[bug.checker] = by_checker.get(bug.checker, 0) + 1
                by_level[bug.level] = by_level.get(bug.level, 0) + 1
                by_rule[bug.rule_id] = by_rule.get(bug.rule_id, 0) + 1
                all_bugs.append({
                    "checker": bug.checker,
                    "file_path": bug.file_path,
                    "line": bug.line,
                    "column": bug.column,
                    "message": bug.message,
                    "rule_id": bug.rule_id,
                    "level": bug.level,
                    "force": bug.force,
                    "type_code": bug.type_code,
                })

        return {
            "total_bugs": self.total_bugs,
            "total_files": self.total_files,
            "by_checker": by_checker,
            "by_level": by_level,
            "by_file": by_file,
            "by_rule": by_rule,
            "bugs": all_bugs,
        }

    def to_dict(self) -> Dict[str, Any]:
        """完整报告序列化为 dict."""
        return {
            "report_id": self.report_id,
            "project_name": self.project_name,
            "project_path": self.project_path,
            "files_stats": [
                {
                    "file_path": fs.file_path,
                    "total_lines": fs.total_lines,
                    "total_statements": fs.total_statements,
                    "function_count": fs.function_count,
                    "function_max_depth": fs.function_max_depth,
                    "comment_lines": fs.comment_lines,
                    "bug_count": len(fs.bugs),
                    "bugs": [
                        {
                            "checker": b.checker,
                            "file_path": b.file_path,
                            "line": b.line,
                            "column": b.column,
                            "message": b.message,
                            "rule_id": b.rule_id,
                            "level": b.level,
                            "force": b.force,
                        }
                        for b in fs.bugs
                    ],
                }
                for fs in self.files_stats
            ],
            "summary": self.summary(),
        }


# ============================================================================
# 解析器函数
# ============================================================================

def parse_xplusx_err(filepath: str | Path) -> List[Dict[str, Any]]:
    """解析 .xplusx.err JSON 文件，返回 bug 列表."""
    path = Path(filepath)
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []

    bugs = data.get("bugs", [])
    if not isinstance(bugs, list):
        return []

    result = []
    for bug in bugs:
        if not isinstance(bug, dict):
            continue
        loc_start = bug.get("location_start", {})
        try:
            line = int(loc_start.get("line", -1))
        except (TypeError, ValueError):
            line = -1
        try:
            column = int(loc_start.get("column", -1))
        except (TypeError, ValueError):
            column = -1

        result.append({
            "checker": bug.get("checker", ""),
            "file_path": bug.get("path", ""),
            "line": line,
            "column": column,
            "message": bug.get("message", "").strip(),
            "rule_id": _extract_rule_id(bug),
            "force": str(bug.get("force", "0")),
            "type_code": str(bug.get("type", "0")),
            "status": str(bug.get("status", "0")),
        })
    return result


def _extract_rule_id(bug: Dict) -> str:
    """从 bug 记录中提取 GJB/MISRA 规则编号."""
    standard = bug.get("standard", "")
    if standard:
        std = standard.strip()
        # 提取规则编号部分, 如 "GJB-R-1-8-2 : Prohibit ..." → "GJB-R-1-8-2"
        match = re.match(r'(GJB-[AR]-\d+-\d+-\d+|MISRA[^:\s]*[A-Z]?-\d+[^:\s]*)', std)
        if match:
            return match.group(1)
        return std
    message = bug.get("message", "")
    match = re.search(r'(GJB-[AR]-\d+-\d+-\d+|MISRA[^:\s]*[A-Z]?-\d+[^:\s]*)', message)
    if match:
        return match.group(1)
    return ""


def parse_sta(filepath: str | Path) -> Dict[str, int]:
    """解析 .sta 文本文件，返回统计字典."""
    path = Path(filepath)
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}

    stats: Dict[str, int] = {}
    patterns = {
        "total_lines": r"Total Lines\s*:\s*(\d+)",
        "total_statements": r"Total Statements\s*:\s*(\d+)",
        "total_declares": r"Total Declares\s*:\s*(\d+)",
        "function_count": r"Function Count\s*:\s*(\d+)",
        "function_max_lines": r"Function Max Lines\s*:\s*(\d+)",
        "function_max_depth": r"Function Max Depth\s*:\s*(\d+)",
        "comment_lines": r"Comment Lines\s*:\s*(\d+)",
        "code_size": r"Code Size\s*:\s*(\d+)",
    }
    for key, pattern in patterns.items():
        m = re.search(pattern, text)
        if m:
            stats[key] = int(m.group(1))
    return stats


def parse_rst(filepath: str | Path) -> Dict[str, str]:
    """解析 .rst XML 文件，返回项目元数据."""
    path = Path(filepath)
    if not path.exists():
        return {}
    try:
        tree = ET.parse(str(path))
        root = tree.getroot()
        project_el = root.find("project")
        files_el = root.find("files")
        time_open = root.find("time_open")
        return {
            "project_path": project_el.text.strip() if project_el is not None and project_el.text else "",
            "files_list": files_el.text.strip() if files_el is not None and files_el.text else "",
            "time_open": _format_xml_time(time_open) if time_open is not None else "",
        }
    except (ET.ParseError, OSError):
        return {}


def _format_xml_time(el: ET.Element) -> str:
    """格式化 XML 时间元素."""
    y = el.get("year", "")
    m = el.get("month", "")
    d = el.get("day", "")
    h = el.get("hour", "")
    mi = el.get("minute", "")
    s = el.get("second", "")
    return f"{y}-{m}-{d} {h}:{mi}:{s}"


def parse_output_dir(
    dirpath: str | Path,
    report_id: str = "",
) -> DSITReport:
    """递归扫描 DeepSITRServer 输出目录，生成完整报告.

    目录结构约定（DeepSITRServer 典型布局）::

        output_dir/
        ├── file1.cpp.xplusx.err   ← JSON 诊断
        ├── file1.cpp.sta           ← 文件统计
        ├── file1.cpp.cgp           ← 调用图（暂不解析）
        ├── file1.cpp.err           ← 文本格式诊断
        ├── file1.cpp.cgf           ← 检查器配置
        ├── file2.cpp.xplusx.err
        ├── ...
        └── output.rst              ← 项目级元数据（可选）

    特殊处理：DeepSITRServer 的输出目录可能包含多个子目录
    （如 SACarCam/, StdDOC/, Test2/），递归扫描所有文件。
    """
    root = Path(dirpath)
    if not root.is_dir():
        return DSITReport(report_id=report_id, project_name=root.name, project_path=str(root))

    # 收集所有 .xplusx.err 文件
    xplusx_files: Dict[str, Path] = {}
    sta_files: Dict[str, Path] = {}
    rst_files: List[Path] = []

    for filepath in root.rglob("*"):
        if not filepath.is_file():
            continue
        name = filepath.name.lower()
        if name.endswith(".xplusx.err"):
            # key = 去掉 .xplusx.err 后的基础名
            base = filepath.name[:-len(".xplusx.err")]
            xplusx_files[base] = filepath
        elif name.endswith(".sta"):
            base = filepath.name[:-len(".sta")]
            sta_files[base] = filepath
        elif name.endswith(".rst"):
            rst_files.append(filepath)

    # 读取项目元数据
    project_name = root.name
    project_path = str(root)
    if rst_files:
        meta = parse_rst(rst_files[0])
        project_path = meta.get("project_path", str(root))
        # 从路径中提取项目名
        pp = meta.get("project_path", "")
        if pp:
            project_name = Path(pp).name or root.name

    # 构建报告
    report = DSITReport(
        report_id=report_id,
        project_name=project_name,
        project_path=project_path,
    )

    # 遍历所有找到的 xplusx 文件
    for base, xplusx_path in sorted(xplusx_files.items()):
        bugs_raw = parse_xplusx_err(xplusx_path)
        stats = parse_sta(sta_files[base]) if base in sta_files else {}

        bugs = [
            DSITBug(
                checker=b.get("checker", ""),
                file_path=b.get("file_path", ""),
                line=b.get("line", -1),
                column=b.get("column", -1),
                message=b.get("message", ""),
                rule_id=b.get("rule_id", ""),
                force=b.get("force", "0"),
                type_code=b.get("type_code", "0"),
                status=b.get("status", "0"),
            )
            for b in bugs_raw
        ]

        # file_path 取 bugs 中的路径，否则用 base 作为显示名
        display_path = bugs[0].file_path if bugs else str(xplusx_path)
        # 只保留文件名部分便于展示
        short_path = Path(display_path).name or base

        report.files_stats.append(DSITFileStats(
            file_path=short_path,
            total_lines=stats.get("total_lines", 0),
            total_statements=stats.get("total_statements", 0),
            total_declares=stats.get("total_declares", 0),
            function_count=stats.get("function_count", 0),
            function_max_lines=stats.get("function_max_lines", 0),
            function_max_depth=stats.get("function_max_depth", 0),
            comment_lines=stats.get("comment_lines", 0),
            code_size=stats.get("code_size", 0),
            bugs=bugs,
        ))

    return report


# ============================================================================
# codetidy.exe 实时分析引擎（替代 clang-tidy，作为唯一分析引擎）
# ============================================================================

# codetidy.exe 路径 — 默认为 DeepSITRServer 自带的引擎
_CODETIDY_BIN = os.environ.get(
    "CODETIDY_BIN",
    r"E:\北航项目\DeepSITRServer-2026-6-9\DeepSITRServer\core\codetidy.exe",
)

# 默认启用的 GJB 检查规则
_CODETIDY_CHECKS = os.environ.get("CODETIDY_CHECKS", "clang-analyzer-gjb*")

# 分析超时（秒）
_CODETIDY_TIMEOUT = int(os.environ.get("CODETIDY_TIMEOUT", "300"))


def _find_codetidy() -> Path:
    """查找 codetidy.exe 可执行文件路径."""
    bin_path = Path(_CODETIDY_BIN)
    if bin_path.is_file():
        return bin_path

    # 尝试在 PATH 中查找
    which = shutil.which("codetidy.exe") or shutil.which("codetidy")
    if which:
        return Path(which)

    raise FileNotFoundError(
        f"未找到 codetidy.exe，请设置 CODETIDY_BIN 环境变量。"
        f" 当前值: {_CODETIDY_BIN}"
    )


def run_codetidy(
    source_files: List[Path],
    workdir: Path,
    *,
    checks: str = "",
    extra_args: Optional[List[str]] = None,
    timeout: int = 0,
) -> subprocess.CompletedProcess:
    """对一组源文件运行 codetidy.exe。

    Args:
        source_files: 待分析的源文件路径列表
        workdir: 工作目录（codetidy 在此目录下运行）
        checks: 启用的检查规则（为空则使用默认 GJB 规则）
        extra_args: 额外的编译器参数（如 -std=c++11 -I./include）
        timeout: 超时秒数（0 使用默认值）

    Returns:
        subprocess.CompletedProcess 对象
    """
    codetidy = _find_codetidy()
    timeout = timeout or _CODETIDY_TIMEOUT
    effective_checks = checks or _CODETIDY_CHECKS

    # 构建命令: codetidy.exe <files> -checks=<...> -- <compiler-flags>
    cmd = [
        str(codetidy),
        *[str(f) for f in source_files],
        f"-checks={effective_checks}",
        "--",
    ]
    if extra_args:
        cmd.extend(extra_args)
    else:
        cmd.append("-std=c++11")

    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(workdir),
        timeout=timeout,
    )


# 正则：解析 clang-tidy 风格的标准输出诊断行
# 格式: <file>:<line>:<col>: <level>: <message> [checker-name]
_DIAG_LINE_RE = re.compile(
    r"^(.+?):(\d+):(\d+):\s+(warning|error|note):\s+(.+?)(?:\s+\[(.+?)\])?\s*$"
)


def _parse_codetidy_output(
    stdout: str,
    stderr: str,
    source_files: List[Path],
) -> List[DSITBug]:
    """解析 codetidy.exe 的 stdout/stderr 输出，提取诊断列表。

    兼容 clang-tidy 标准输出格式，将每条诊断映射为 DSITBug。
    """
    bugs: List[DSITBug] = []
    # 合并 stdout 和 stderr 进行解析
    combined = (stdout + "\n" + stderr).splitlines()

    # 建立文件名 → 完整路径的快速映射
    file_map: Dict[str, str] = {}
    for sf in source_files:
        file_map[sf.name] = str(sf)
        file_map[str(sf)] = str(sf)

    for line in combined:
        line = line.strip()
        if not line:
            continue

        m = _DIAG_LINE_RE.match(line)
        if not m:
            continue

        file_ref = m.group(1)
        try:
            line_num = int(m.group(2))
        except (ValueError, TypeError):
            line_num = -1
        try:
            col_num = int(m.group(3))
        except (ValueError, TypeError):
            col_num = -1
        level_str = m.group(4)      # warning / error / note
        message = m.group(5).strip()
        checker = (m.group(6) or "").strip()

        # 解析文件路径：优先用完整路径匹配
        file_path = file_ref
        if file_ref in file_map:
            file_path = file_map[file_ref]
        else:
            # 尝试按文件名匹配
            for sf in source_files:
                if sf.name == file_ref or str(sf).endswith(file_ref):
                    file_path = str(sf)
                    break

        # 映射级别
        if level_str == "error":
            force = "1"
        elif level_str == "warning":
            force = "1"  # GJB 中 warning 也算强制
        else:
            force = "0"

        rule_id = _extract_rule_id_from_checker(checker) or _extract_rule_id_from_message(message)

        bugs.append(DSITBug(
            checker=checker,
            file_path=file_path,
            line=line_num,
            column=col_num,
            message=message,
            rule_id=rule_id,
            force=force,
            type_code="2" if level_str == "warning" else "1",
            status="0",
        ))

    return bugs


def _extract_rule_id_from_checker(checker: str) -> str:
    """从 checker 名称中推导 GJB 规则编号.

    例如: clang-analyzer-gjb.statement.CodeUnreachableBranch → GJB-statement-CodeUnreachableBranch
    """
    if not checker:
        return ""
    # 提取 gjb 或 gjb05 后面的部分
    m = re.search(r'gjb\d*\.(.+)$', checker, re.IGNORECASE)
    if m:
        return f"GJB-{m.group(1)}"
    return checker


def _extract_rule_id_from_message(message: str) -> str:
    """从诊断消息中提取 GJB/MISRA 规则编号."""
    if not message:
        return ""
    m = re.search(r'(GJB-[AR]-\d+-\d+-\d+|MISRA[^:\s]*[A-Z]?-\d+[^:\s]*)', message)
    if m:
        return m.group(1)
    return ""


def analyze_with_codetidy(
    source_files: List[Path],
    *,
    project_name: str = "",
    checks: str = "",
    extra_args: Optional[List[str]] = None,
    timeout: int = 0,
    report_id: str = "",
) -> DSITReport:
    """使用 codetidy.exe 分析源文件并返回 DSITReport。

    这是 ct8114 的核心分析入口，替代了原来的 clang-tidy + fixes_parser 流程。

    Args:
        source_files: 待分析的 C/C++ 源文件路径列表
        project_name: 项目名称（用于报告展示）
        checks: 启用的检查规则（默认使用 GJB 规则）
        extra_args: 编译器额外参数
        timeout: 超时秒数
        report_id: 报告 ID（自动生成）

    Returns:
        DSITReport 完整报告对象
    """
    if not source_files:
        return DSITReport(
            report_id=report_id or str(uuid.uuid4()),
            project_name=project_name or "empty",
            project_path="",
        )

    # 确定工作目录：使用第一个源文件的父目录
    workdir = source_files[0].parent.resolve()

    # 收集 include 目录
    include_dirs = sorted({
        str(p.parent.resolve())
        for p in source_files
        if p.parent.resolve() != workdir
    })
    if not extra_args:
        extra_args = ["-std=c++11"]
    for inc in include_dirs:
        extra_args.append(f"-I{inc}")

    try:
        proc = run_codetidy(
            source_files,
            workdir,
            checks=checks,
            extra_args=extra_args,
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise FileNotFoundError(
            f"未找到 codetidy.exe。请确认 DeepSITRServer 已部署，"
            f"或设置 CODETIDY_BIN 环境变量。\n{ e}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise subprocess.TimeoutExpired(
            cmd=e.cmd, timeout=e.timeout,
            output=e.output, stderr=e.stderr,
        ) from e

    # 解析输出
    bugs = _parse_codetidy_output(proc.stdout, proc.stderr, source_files)

    # 按文件分组
    file_bugs: Dict[str, List[DSITBug]] = {}
    for bug in bugs:
        file_bugs.setdefault(bug.file_path, []).append(bug)

    # 构建报告
    report = DSITReport(
        report_id=report_id or f"codetidy_{uuid.uuid4().hex[:12]}",
        project_name=project_name or workdir.name,
        project_path=str(workdir),
    )

    for file_path, file_bug_list in sorted(file_bugs.items()):
        short_path = Path(file_path).name
        report.files_stats.append(DSITFileStats(
            file_path=short_path,
            bugs=file_bug_list,
        ))

    # 如果某些源文件没有诊断，也加入（无 bug）
    analyzed_names = {Path(b.file_path).name for b in bugs}
    for sf in source_files:
        if sf.name not in analyzed_names:
            report.files_stats.append(DSITFileStats(
                file_path=sf.name,
                bugs=[],
            ))

    return report


# ============================================================================
# CLI 测试入口
# ============================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python dsit_parser.py <output_dir_or_source_file>")
        print("  DeepSITRServer output dir: python dsit_parser.py ../DeepSITRServer/Test2")
        print("  Source file analysis:      python dsit_parser.py --analyze file1.cpp file2.cpp")
        sys.exit(1)

    if sys.argv[1] == "--analyze":
        source_paths = [Path(p) for p in sys.argv[2:]]
        report = analyze_with_codetidy(source_paths)
    else:
        target = sys.argv[1]
        report = parse_output_dir(target, report_id="cli_test")

    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    print(f"\n=== 摘要 ===")
    s = report.summary()
    print(f"  文件数: {report.total_files}")
    print(f"  诊断总数: {report.total_bugs}")
    print(f"  按级别: {s['by_level']}")
    print(f"  按规则: {s['by_rule']}")
