"""解析 DeepSITRServer 输出文件的模块。

DeepSITRServer 每分析一个 .c/.cpp 文件，会在输出目录中生成以下后缀文件：
    .xplusx.err  ─ JSON 格式的诊断结果（核心输出）
    .err         ─ 文本格式的 clang-tidy 风格警告
    .sta         ─ 文件统计信息（行数、函数数、复杂度等）
    .rst         ─ 项目级 XML 元数据
    .cgp         ─ 调用图转储
    .cgf         ─ 检查器配置

本模块提供：
    parse_xplusx_err(filepath) -> List[Dict]     解析 .xplusx.err JSON
    parse_sta(filepath)        -> Dict           解析 .sta 文件统计
    parse_output_dir(dirpath)  -> DSITReport    递归扫描整个输出目录
    DSITReport.dataclass       -> 统一数据模型（兼容前端展示）
"""

from __future__ import annotations

import json
import os
import re
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
# CLI 测试入口
# ============================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python dsit_parser.py <output_dir>")
        print("Example: python dsit_parser.py ../DeepSITRServer-2026-6-9/DeepSITRServer/Test2")
        sys.exit(1)

    target = sys.argv[1]
    report = parse_output_dir(target, report_id="cli_test")
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    print(f"\n=== 摘要 ===")
    s = report.summary()
    print(f"  文件数: {report.total_files}")
    print(f"  诊断总数: {report.total_bugs}")
    print(f"  按级别: {s['by_level']}")
    print(f"  按规则: {s['by_rule']}")
