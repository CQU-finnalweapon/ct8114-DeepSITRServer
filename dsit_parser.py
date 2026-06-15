"""DeepSITRServer 杈撳嚭瑙ｆ瀽 + codetidy.exe 鍒嗘瀽寮曟搸妯″潡銆?

鏈ā鍧楁槸 ct8114 鐨勬牳蹇冨垎鏋愬眰锛屾彁渚涗袱澶у姛鑳斤細

A. DeepSITRServer 杈撳嚭瑙ｆ瀽锛堝吋瀹瑰凡鏈?DSIT 杈撳嚭鐩綍锛夛細
    parse_xplusx_err(filepath) -> List[Dict]     瑙ｆ瀽 .xplusx.err JSON
    parse_sta(filepath)        -> Dict           瑙ｆ瀽 .sta 鏂囦欢缁熻
    parse_output_dir(dirpath)  -> DSITReport    閫掑綊鎵弿鏁翠釜杈撳嚭鐩綍

B. codetidy.exe 瀹炴椂鍒嗘瀽锛堟浛浠?clang-tidy锛屼綔涓哄敮涓€鍒嗘瀽寮曟搸锛夛細
    analyze_with_codetidy()    -> DSITReport    杩愯 codetidy.exe 鍒嗘瀽婧愮爜骞惰繑鍥炴姤鍛?
    run_codetidy()             -> CompletedProcess  搴曞眰 codetidy.exe 璋冪敤

鏁版嵁妯″瀷锛?
    DSITReport  鈹€ 涓€娆″垎鏋愮殑瀹屾暣鎶ュ憡锛堢粺璁?+ 鏂囦欢鏄庣粏 + 璇婃柇姹囨€伙級
    DSITBug     鈹€ 鍗曟潯璇婃柇缁撴灉
    DSITFileStats 鈹€ 鍗曟枃浠剁粺璁′俊鎭?
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
# 鏁版嵁妯″瀷
# ============================================================================

@dataclass
class DSITBug:
    """鍗曟潯璇婃柇缁撴灉锛屼笌鍓嶇 diag 鍗＄墖瀛楁瀵归綈."""
    checker: str           # 妫€鏌ュ櫒鍚嶇О, 濡?clang-analyzer-gjb.statement.CodeUnreachableBranch
    file_path: str         # 婧愭枃浠惰矾寰?
    line: int              # 琛屽彿
    column: int            # 鍒楀彿
    message: str           # 璇婃柇娑堟伅锛堝惈瑙勫垯缂栧彿锛?
    rule_id: str           # 瑙勫垯缂栧彿, 濡?GJB-R-1-8-2
    force: str             # 寮哄埗绾у埆: "1"=寮哄埗, "0"=鎺ㄨ崘
    type_code: str         # 绫诲瀷浠ｇ爜: "2"=warning, "1"=error
    status: str            # 鐘舵€?

    @property
    def level(self) -> str:
        """鏄犲皠涓哄墠绔吋瀹圭殑绾у埆."""
        if self.force == "1":
            return "Error"
        return "Warning"


@dataclass
class DSITFileStats:
    """鍗曟枃浠剁粺璁′俊鎭?"""
    file_path: str                    # 婧愭枃浠剁浉瀵?缁濆璺緞
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
    """涓€娆?DeepSITRServer 鍒嗘瀽鐨勫畬鏁存姤鍛?"""
    report_id: str
    project_name: str                  # 椤圭洰鍚嶇О
    project_path: str                  # 鍘熷椤圭洰璺緞
    files_stats: List[DSITFileStats] = field(default_factory=list)

    @property
    def total_bugs(self) -> int:
        return sum(len(fs.bugs) for fs in self.files_stats)

    @property
    def total_files(self) -> int:
        return len(self.files_stats)

    def summary(self) -> Dict[str, Any]:
        """鐢熸垚缁欏墠绔睍绀虹殑鑱氬悎鎽樿."""
        by_checker: Dict[str, int] = {}
        by_level: Dict[str, int] = {}
        by_file: Dict[str, int] = {}
        by_rule: Dict[str, int] = {}
        all_bugs: List[Dict] = []

        for fs in self.files_stats:
            fs_path = _relative_report_path(fs.file_path, self.project_path)
            by_file[fs_path] = len(fs.bugs)
            for bug in fs.bugs:
                bug_path = _relative_report_path(bug.file_path, self.project_path)
                rule_id = _safe_rule_id(bug.rule_id, bug.checker)
                by_checker[bug.checker] = by_checker.get(bug.checker, 0) + 1
                by_level[bug.level] = by_level.get(bug.level, 0) + 1
                by_rule[rule_id] = by_rule.get(rule_id, 0) + 1
                all_bugs.append({
                    "checker": bug.checker,
                    "file_path": bug_path,
                    "line": bug.line,
                    "column": bug.column,
                    "message": bug.message,
                    "rule_id": rule_id,
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
        """瀹屾暣鎶ュ憡搴忓垪鍖栦负 dict."""
        return {
            "report_id": self.report_id,
            "project_name": self.project_name,
            "project_path": self.project_path,
            "files_stats": [
                {
                    "file_path": _relative_report_path(fs.file_path, self.project_path),
                    "total_lines": fs.total_lines,
                    "total_statements": fs.total_statements,
                    "function_count": fs.function_count,
                    "function_max_depth": fs.function_max_depth,
                    "comment_lines": fs.comment_lines,
                    "bug_count": len(fs.bugs),
                    "bugs": [
                        {
                            "checker": b.checker,
                            "file_path": _relative_report_path(b.file_path, self.project_path),
                            "line": b.line,
                            "column": b.column,
                            "message": b.message,
                            "rule_id": _safe_rule_id(b.rule_id, b.checker),
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


def _safe_rule_id(rule_id: str, checker: str = "") -> str:
    rid = (rule_id or "").strip()
    if rid:
        return rid
    checker = (checker or "").strip()
    if checker:
        return checker
    return "UNKNOWN_RULE"


def _relative_report_path(file_path: str, project_path: str) -> str:
    if not file_path:
        return ""
    path = Path(file_path)
    if project_path:
        try:
            return path.resolve().relative_to(Path(project_path).resolve()).as_posix()
        except Exception:
            pass
    return path.as_posix()


# ============================================================================
# 瑙ｆ瀽鍣ㄥ嚱鏁?
# ============================================================================

def parse_xplusx_err(filepath: str | Path) -> List[Dict[str, Any]]:
    """瑙ｆ瀽 .xplusx.err JSON 鏂囦欢锛岃繑鍥?bug 鍒楄〃."""
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
    """浠?bug 璁板綍涓彁鍙?GJB/MISRA 瑙勫垯缂栧彿."""
    standard = bug.get("standard", "")
    if standard:
        std = standard.strip()
        # 鎻愬彇瑙勫垯缂栧彿閮ㄥ垎, 濡?"GJB-R-1-8-2 : Prohibit ..." 鈫?"GJB-R-1-8-2"
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
    """瑙ｆ瀽 .sta 鏂囨湰鏂囦欢锛岃繑鍥炵粺璁″瓧鍏?"""
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
    """瑙ｆ瀽 .rst XML 鏂囦欢锛岃繑鍥為」鐩厓鏁版嵁."""
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
    """鏍煎紡鍖?XML 鏃堕棿鍏冪礌."""
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
    """閫掑綊鎵弿 DeepSITRServer 杈撳嚭鐩綍锛岀敓鎴愬畬鏁存姤鍛?

    鐩綍缁撴瀯绾﹀畾锛圖eepSITRServer 鍏稿瀷甯冨眬锛?:

        output_dir/
        鈹溾攢鈹€ file1.cpp.xplusx.err   鈫?JSON 璇婃柇
        鈹溾攢鈹€ file1.cpp.sta           鈫?鏂囦欢缁熻
        鈹溾攢鈹€ file1.cpp.cgp           鈫?璋冪敤鍥撅紙鏆備笉瑙ｆ瀽锛?
        鈹溾攢鈹€ file1.cpp.err           鈫?鏂囨湰鏍煎紡璇婃柇
        鈹溾攢鈹€ file1.cpp.cgf           鈫?妫€鏌ュ櫒閰嶇疆
        鈹溾攢鈹€ file2.cpp.xplusx.err
        鈹溾攢鈹€ ...
        鈹斺攢鈹€ output.rst              鈫?椤圭洰绾у厓鏁版嵁锛堝彲閫夛級

    鐗规畩澶勭悊锛欴eepSITRServer 鐨勮緭鍑虹洰褰曞彲鑳藉寘鍚涓瓙鐩綍
    锛堝 SACarCam/, StdDOC/, Test2/锛夛紝閫掑綊鎵弿鎵€鏈夋枃浠躲€?
    """
    root = Path(dirpath)
    if not root.is_dir():
        return DSITReport(report_id=report_id, project_name=root.name, project_path=str(root))

    # 鏀堕泦鎵€鏈?.xplusx.err 鏂囦欢
    xplusx_files: Dict[str, Path] = {}
    sta_files: Dict[str, Path] = {}
    rst_files: List[Path] = []

    for filepath in root.rglob("*"):
        if not filepath.is_file():
            continue
        name = filepath.name.lower()
        if name.endswith(".xplusx.err"):
            # key = 鍘绘帀 .xplusx.err 鍚庣殑鍩虹鍚?
            base = filepath.name[:-len(".xplusx.err")]
            xplusx_files[base] = filepath
        elif name.endswith(".sta"):
            base = filepath.name[:-len(".sta")]
            sta_files[base] = filepath
        elif name.endswith(".rst"):
            rst_files.append(filepath)

    # 璇诲彇椤圭洰鍏冩暟鎹?
    project_name = root.name
    project_path = str(root)
    if rst_files:
        meta = parse_rst(rst_files[0])
        project_path = meta.get("project_path", str(root))
        # 浠庤矾寰勪腑鎻愬彇椤圭洰鍚?
        pp = meta.get("project_path", "")
        if pp:
            project_name = Path(pp).name or root.name

    # 鏋勫缓鎶ュ憡
    report = DSITReport(
        report_id=report_id,
        project_name=project_name,
        project_path=project_path,
    )

    # 閬嶅巻鎵€鏈夋壘鍒扮殑 xplusx 鏂囦欢
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

        # file_path 鍙?bugs 涓殑璺緞锛屽惁鍒欑敤 base 浣滀负鏄剧ず鍚?
        display_path = bugs[0].file_path if bugs else str(xplusx_path)
        # 鍙繚鐣欐枃浠跺悕閮ㄥ垎渚夸簬灞曠ず
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
# codetidy.exe 瀹炴椂鍒嗘瀽寮曟搸锛堟浛浠?clang-tidy锛屼綔涓哄敮涓€鍒嗘瀽寮曟搸锛?
# ============================================================================
# ╔════════════════════════════════════════════════════════════════════════════════════════════════════════════════════╗
# ║  DeepSITRServer / codetidy.exe 路径配置                                  ║
# ║                                                                          ║
# ║  🎯 推荐方式: 设置 DEEPSITR_ROOT 环境变量                                  ║
# ║      指向 DeepSITRServer 安装目录即可，程序会自动搜索 core/codetidy.exe     ║
# ║      PowerShell: $env:DEEPSITR_ROOT="E:\path\to\DeepSITRServer"          ║
# ║      Linux:      export DEEPSITR_ROOT=/opt/DeepSITRServer                 ║
# ║                                                                          ║
# ║  🔧 高级覆盖: 设置 CODETIDY_BIN 环境变量直接指定 codetidy.exe 的完整路径    ║
# ║                                                                          ║
# ║  📌 搜索优先级: DEEPSITR_ROOT → CODETIDY_BIN → 自动递归搜索                ║
# ╚═════════════════════════════════════════════════════════════════════════════════════════════════════════════════════╝
# ============================================================================

# DeepSITRServer 安装根目录（推荐设置）
# 程序会在此目录下自动查找 core/codetidy.exe
_DEEPSITR_ROOT = os.environ.get("DEEPSITR_ROOT", "")

# codetidy.exe 完整路径（高级覆盖选项）
_CODETIDY_BIN = os.environ.get("CODETIDY_BIN", "")

# 默认启用的 GJB 检查规则
_CODETIDY_CHECKS = os.environ.get("CODETIDY_CHECKS", "clang-analyzer-gjb*")

# 分析超时（秒）
_CODETIDY_TIMEOUT = int(os.environ.get("CODETIDY_TIMEOUT", "300"))


CODETIDY_NOT_FOUND_MESSAGE = (
    "\u540e\u7aef\u5206\u6790\u7a0b\u5e8f\u8def\u5f84\u672a\u914d\u7f6e\u6216\u4e0d\u5b58\u5728\uff0c"
    "\u8bf7\u8bbe\u7f6e DEEPSITR_ROOT \u6216 CODETIDY_BIN \u73af\u5883\u53d8\u91cf\uff0c"
    "\u6216\u5c06 codetidy.exe \u653e\u5230\u9879\u76ee\u76ee\u5f55\u4e0b\u7684 DeepSITRServer/core/ \u4e2d"
)


def _candidate_codetidy_paths() -> List[Path]:
    """Return candidate paths for codetidy.exe in priority order.

    Priority:
      1. DEEPSITR_ROOT env var (recommended) -> core/codetidy.exe
      2. CODETIDY_BIN env var (direct exe path override)
      3. Standard location: ./DeepSITRServer/core/codetidy.exe
      4. Parent/grandparent DeepSITRServer/core/codetidy.exe
      5. Recursive search in project_root, parent, grandparent
      6. PATH environment (shutil.which)
    """
    project_root = Path(__file__).resolve().parent
    parent = project_root.parent
    grandparent = parent.parent
    paths: List[Path] = []

    # Priority 1: DEEPSITR_ROOT (recommended - just point to DeepSITRServer dir)
    depsitr_root = os.environ.get("DEEPSITR_ROOT", "")
    if depsitr_root:
        paths.append(Path(depsitr_root) / "core" / "codetidy.exe")

    # Priority 2: CODETIDY_BIN (full path to codetidy.exe)
    env_path = os.environ.get("CODETIDY_BIN")
    if env_path:
        paths.append(Path(env_path))

    # Priority 3-4: Standard relative locations
    paths.extend([
        project_root / "DeepSITRServer" / "core" / "codetidy.exe",
        parent / "DeepSITRServer" / "core" / "codetidy.exe",
        grandparent / "DeepSITRServer" / "core" / "codetidy.exe",
    ])

    # Priority 5: Recursive search (catch-all for various layouts)
    for search_root in (project_root, parent, grandparent):
        try:
            for p in search_root.rglob("codetidy.exe"):
                if p.is_file():
                    paths.append(p)
        except OSError:
            pass

    # Priority 6: PATH lookup
    which = shutil.which("codetidy.exe") or shutil.which("codetidy")
    if which:
        paths.append(Path(which))

    # Deduplicate while preserving order
    unique: List[Path] = []
    seen = set()
    for path in paths:
        key = str(path.resolve()) if path.exists() else str(path)
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique
def get_codetidy_search_paths() -> List[str]:
    """Return the candidate paths checked when resolving codetidy.exe."""

    return [str(path) for path in _candidate_codetidy_paths()]


def find_codetidy_bin() -> Optional[Path]:
    """Resolve codetidy.exe without using a machine-specific hard-coded path."""

    for path in _candidate_codetidy_paths():
        if path.is_file():
            return path
    return None


def _find_codetidy() -> Path:
    """鏌ユ壘 codetidy.exe 鍙墽琛屾枃浠惰矾寰?"""
    codetidy = find_codetidy_bin()
    if codetidy:
        return codetidy

    checked = "\n".join(f"  - {path}" for path in get_codetidy_search_paths())
    raise FileNotFoundError(
        f"{CODETIDY_NOT_FOUND_MESSAGE}\n已检查候选路径:\n{checked}"
    )

def run_codetidy(
    source_files: List[Path],
    workdir: Path,
    *,
    checks: str = "",
    extra_args: Optional[List[str]] = None,
    timeout: int = 0,
) -> subprocess.CompletedProcess:
    """瀵逛竴缁勬簮鏂囦欢杩愯 codetidy.exe銆?

    Args:
        source_files: 寰呭垎鏋愮殑婧愭枃浠惰矾寰勫垪琛?
        workdir: 宸ヤ綔鐩綍锛坈odetidy 鍦ㄦ鐩綍涓嬭繍琛岋級
        checks: 鍚敤鐨勬鏌ヨ鍒欙紙涓虹┖鍒欎娇鐢ㄩ粯璁?GJB 瑙勫垯锛?
        extra_args: 棰濆鐨勭紪璇戝櫒鍙傛暟锛堝 -std=c++11 -I./include锛?
        timeout: 瓒呮椂绉掓暟锛? 浣跨敤榛樿鍊硷級

    Returns:
        subprocess.CompletedProcess 瀵硅薄
    """
    codetidy = _find_codetidy()
    timeout = timeout or _CODETIDY_TIMEOUT
    effective_checks = checks or _CODETIDY_CHECKS

    # 鏋勫缓鍛戒护: codetidy.exe <files> -checks=<...> -- <compiler-flags>
    cmd = [
        str(codetidy),
        *[str(f) for f in source_files],
        f"-checks={effective_checks}",
        "--",
    ]
    if extra_args:
        cmd.extend(extra_args)
    else:
        # 自动检测 C/C++ 选择合适的语言标准
        has_c_file = any(f.suffix.lower() == ".c" for f in source_files)
        cmd.append("-std=c11" if has_c_file else "-std=c++11")

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


# 姝ｅ垯锛氳В鏋?clang-tidy 椋庢牸鐨勬爣鍑嗚緭鍑鸿瘖鏂
# 鏍煎紡: <file>:<line>:<col>: <level>: <message> [checker-name]
_DIAG_LINE_RE = re.compile(
    r"^(.+?):(\d+):(\d+):\s+(warning|error|note):\s+(.+?)(?:\s+\[(.+?)\])?\s*$"
)


def _parse_codetidy_output(
    stdout: str,
    stderr: str,
    source_files: List[Path],
) -> List[DSITBug]:
    """瑙ｆ瀽 codetidy.exe 鐨?stdout/stderr 杈撳嚭锛屾彁鍙栬瘖鏂垪琛ㄣ€?

    鍏煎 clang-tidy 鏍囧噯杈撳嚭鏍煎紡锛屽皢姣忔潯璇婃柇鏄犲皠涓?DSITBug銆?
    """
    bugs: List[DSITBug] = []
    # 鍚堝苟 stdout 鍜?stderr 杩涜瑙ｆ瀽
    combined = (stdout + "\n" + stderr).splitlines()

    # 寤虹珛鏂囦欢鍚?鈫?瀹屾暣璺緞鐨勫揩閫熸槧灏?
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

        # 瑙ｆ瀽鏂囦欢璺緞锛氫紭鍏堢敤瀹屾暣璺緞鍖归厤
        file_path = file_ref
        if file_ref in file_map:
            file_path = file_map[file_ref]
        else:
            # 灏濊瘯鎸夋枃浠跺悕鍖归厤
            for sf in source_files:
                if sf.name == file_ref or str(sf).endswith(file_ref):
                    file_path = str(sf)
                    break

        # 鏄犲皠绾у埆
        if level_str == "error":
            force = "1"
        elif level_str == "warning":
            force = "1"  # GJB 涓?warning 涔熺畻寮哄埗
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
    """浠?checker 鍚嶇О涓帹瀵?GJB 瑙勫垯缂栧彿.

    渚嬪: clang-analyzer-gjb.statement.CodeUnreachableBranch 鈫?GJB-statement-CodeUnreachableBranch
    """
    if not checker:
        return ""
    # 鎻愬彇 gjb 鎴?gjb05 鍚庨潰鐨勯儴鍒?
    m = re.search(r'gjb\d*\.(.+)$', checker, re.IGNORECASE)
    if m:
        return f"GJB-{m.group(1)}"
    return checker


def _extract_rule_id_from_message(message: str) -> str:
    """浠庤瘖鏂秷鎭腑鎻愬彇 GJB/MISRA 瑙勫垯缂栧彿."""
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
    """浣跨敤 codetidy.exe 鍒嗘瀽婧愭枃浠跺苟杩斿洖 DSITReport銆?

    杩欐槸 ct8114 鐨勬牳蹇冨垎鏋愬叆鍙ｏ紝鏇夸唬浜嗗師鏉ョ殑 clang-tidy + fixes_parser 娴佺▼銆?

    Args:
        source_files: 寰呭垎鏋愮殑 C/C++ 婧愭枃浠惰矾寰勫垪琛?
        project_name: 椤圭洰鍚嶇О锛堢敤浜庢姤鍛婂睍绀猴級
        checks: 鍚敤鐨勬鏌ヨ鍒欙紙榛樿浣跨敤 GJB 瑙勫垯锛?
        extra_args: 缂栬瘧鍣ㄩ澶栧弬鏁?
        timeout: 瓒呮椂绉掓暟
        report_id: 鎶ュ憡 ID锛堣嚜鍔ㄧ敓鎴愶級

    Returns:
        DSITReport 瀹屾暣鎶ュ憡瀵硅薄
    """
    if not source_files:
        return DSITReport(
            report_id=report_id or str(uuid.uuid4()),
            project_name=project_name or "empty",
            project_path="",
        )

    # 纭畾宸ヤ綔鐩綍锛氫娇鐢ㄧ涓€涓簮鏂囦欢鐨勭埗鐩綍
    workdir = source_files[0].parent.resolve()

    # 鏀堕泦 include 鐩綍
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
            f"鏈壘鍒?codetidy.exe銆傝纭 DeepSITRServer 宸查儴缃诧紝"
            f"鎴栬缃?CODETIDY_BIN 鐜鍙橀噺銆俓n{ e}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise subprocess.TimeoutExpired(
            cmd=e.cmd, timeout=e.timeout,
            output=e.output, stderr=e.stderr,
        ) from e

    # 瑙ｆ瀽杈撳嚭
    bugs = _parse_codetidy_output(proc.stdout, proc.stderr, source_files)

    # 鎸夋枃浠跺垎缁?
    file_bugs: Dict[str, List[DSITBug]] = {}
    for bug in bugs:
        file_bugs.setdefault(bug.file_path, []).append(bug)

    # 鏋勫缓鎶ュ憡
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

    # 濡傛灉鏌愪簺婧愭枃浠舵病鏈夎瘖鏂紝涔熷姞鍏ワ紙鏃?bug锛?
    analyzed_names = {Path(b.file_path).name for b in bugs}
    for sf in source_files:
        if sf.name not in analyzed_names:
            report.files_stats.append(DSITFileStats(
                file_path=sf.name,
                bugs=[],
            ))

    return report


# ============================================================================
# CLI 娴嬭瘯鍏ュ彛
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
    print(f"\n=== 鎽樿 ===")
    s = report.summary()
    print(f"  鏂囦欢鏁? {report.total_files}")
    print(f"  璇婃柇鎬绘暟: {report.total_bugs}")
    print(f"  鎸夌骇鍒? {s['by_level']}")
    print(f"  鎸夎鍒? {s['by_rule']}")
