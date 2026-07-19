"""HTTP client and result adapters for DCA/DeepSITRServer progress APIs."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

from dsit_parser import DSITBug, DSITFileStats, DSITFunction, DSITReport


DEFAULT_RULE_IDS = [
    "GJB-8114:A-1-10-1:0",
    "GJB-8114:A-1-10-2:0",
    "GJB-8114:R-1-8-1:0",
    "GJB-8114:R-1-8-2:0",
]

RULE_CONFIG_CANDIDATES = [
    "/opt/dcab/cfg/gjb8114-rules-zh_CN.xml",
    "/opt/dcab/cfg/gjb8114-rules.xml",
    str(Path(__file__).resolve().parent / "DeepSITRServer" / "cfg" / "gjb8114-rules-zh_CN.xml"),
    str(Path(__file__).resolve().parent / "DeepSITRServer" / "cfg" / "gjb8114-rules.xml"),
]

_RULE_NAME_RE = re.compile(r"^[AR]-\d+-\d+-\d+$")
_ALL_RULE_IDS_CACHE: Optional[List[str]] = None


class DcabClientError(RuntimeError):
    """Raised when the DCA HTTP service cannot be called successfully."""


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def get_dcab_config() -> Dict[str, Any]:
    return {
        "base_url": _env("DCAB_BASE_URL", "http://127.0.0.1:8080").rstrip("/"),
        "start_path": _env("DCAB_START_PATH", "/start_progress"),
        "check_path": _env("DCAB_CHECK_PATH", "/check_progress"),
        "start_method": _env("DCAB_START_METHOD", "POST").upper(),
        "check_method": _env("DCAB_CHECK_METHOD", "POST").upper(),
        "timeout": float(_env("DCAB_REQUEST_TIMEOUT", "15")),
        "workdir": os.environ.get("DEEPSITR_WORKDIR", "").strip(),
    }


def configured_rule_ids() -> List[str]:
    raw = os.environ.get("DCAB_RULE_IDS", "").strip()
    if not raw or raw.upper() == "ALL":
        return load_all_gjb8114_rule_ids()
    if raw.upper() in {"DEMO", "DEFAULT", "DEFAULT_RULE_IDS"}:
        return list(DEFAULT_RULE_IDS)
    return [_format_rule_id(item) for item in raw.split(",") if item.strip()]


def load_all_gjb8114_rule_ids() -> List[str]:
    global _ALL_RULE_IDS_CACHE
    if _ALL_RULE_IDS_CACHE is not None:
        return list(_ALL_RULE_IDS_CACHE)

    candidates = []
    env_path = os.environ.get("DCAB_GJB8114_RULES_FILE", "").strip()
    if env_path:
        candidates.append(env_path)
    candidates.extend(RULE_CONFIG_CANDIDATES)

    seen = set()
    rule_ids: List[str] = []
    for candidate in candidates:
        path = Path(candidate)
        if not path.exists():
            continue
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            continue
        for elem in root.iter("rule"):
            name = (elem.attrib.get("name") or "").strip()
            if not _RULE_NAME_RE.match(name) or name in seen:
                continue
            seen.add(name)
            rule_ids.append(_format_rule_id(name))
        if rule_ids:
            break

    if not rule_ids:
        raise DcabClientError(
            "No GJB8114 rule ids found. Set DCAB_GJB8114_RULES_FILE to a valid "
            "gjb8114-rules XML file, or set DCAB_RULE_IDS=DEMO for a small debug scan."
        )

    _ALL_RULE_IDS_CACHE = rule_ids
    return list(_ALL_RULE_IDS_CACHE)


def _format_rule_id(value: str) -> str:
    value = value.strip()
    if value.startswith("GJB-8114:"):
        return value
    if _RULE_NAME_RE.match(value):
        return f"GJB-8114:{value}:0"
    return value


def strip_detection_braces(detection_id: Any) -> str:
    return str(detection_id or "").strip().strip("{}")


def normalize_detection_id(detection_id: Any) -> str:
    return str(detection_id or "").strip()


def alternate_detection_id_format(detection_id: Any) -> str:
    value = normalize_detection_id(detection_id)
    if not value:
        return ""
    if value.startswith("{") and value.endswith("}"):
        return value[1:-1]
    return "{" + value + "}"


def start_progress(project_path: str, excluded_paths: Optional[List[str]] = None) -> Dict[str, Any]:
    config = get_dcab_config()
    body = {
        "project_path": project_path,
        "rule_ids": configured_rule_ids(),
        "excluded_paths": excluded_paths or [],
    }
    data = _request_json(
        config["base_url"] + config["start_path"],
        config["start_method"],
        body,
        config["timeout"],
    )
    detection_id = _extract_detection_id(data)
    if not detection_id:
        raise DcabClientError(f"start_progress did not return detection_id: {data!r}")
    data["detection_id"] = normalize_detection_id(detection_id)
    return data


def check_progress(detection_id: str) -> Optional[Dict[str, Any]]:
    config = get_dcab_config()
    return _request_json(
        config["base_url"] + config["check_path"],
        config["check_method"],
        {"detection_id": normalize_detection_id(detection_id)},
        config["timeout"],
        allow_empty=True,
    )


def _request_json(
    url: str,
    method: str,
    body: Dict[str, Any],
    timeout: float,
    allow_empty: bool = False,
) -> Optional[Dict[str, Any]]:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise DcabClientError(f"{method} {url} failed: HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise DcabClientError(f"{method} {url} failed: {exc.reason}") from exc

    if not raw or not raw.strip():
        return None if allow_empty else {}
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise DcabClientError(f"{method} {url} returned non-JSON body") from exc
    if data in ({}, [], None):
        return None if allow_empty else {}
    if not isinstance(data, dict):
        return {"value": data}
    return data


def _extract_detection_id(data: Optional[Dict[str, Any]]) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("detection_id", "detect_id", "id", "request_id", "task_id"):
        value = data.get(key)
        if value:
            return str(value)
    nested = data.get("data")
    if isinstance(nested, dict):
        return _extract_detection_id(nested)
    if isinstance(nested, str):
        return nested
    return ""


def is_empty_check_response(data: Optional[Dict[str, Any]]) -> bool:
    if not data:
        return True
    if data == {}:
        return True
    defect_list = data.get("defect_list") if isinstance(data, dict) else None
    # Only non-empty when DCAB has returned an actual list (even []).
    # defect_list: null means analysis still in progress or no results yet —
    # treat as empty so the xplusx file fallback can be attempted.
    return not isinstance(defect_list, list)


def report_from_defect_list(
    defect_list: List[Dict[str, Any]],
    report_id: str,
    project_name: str,
    project_path: str,
    functions_map: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> DSITReport:
    by_file: Dict[str, Dict[str, Any]] = {}
    for defect in defect_list:
        if not isinstance(defect, dict):
            continue
        traces = defect.get("tracking_path_list") or []
        first = traces[0] if traces and isinstance(traces[0], dict) else {}
        loc = first.get("location_start") or {}
        source_path = str(first.get("file_path") or defect.get("file_path") or "")
        file_path = _project_relative_path(source_path, project_path)
        checker = str(defect.get("checker") or defect.get("rule_id") or "")
        message = str(first.get("descript") or defect.get("message") or "")
        rule_id = _normalize_rule_id(
            defect.get("rule_id"),
            checker,
            message,
            str(defect.get("standard") or ""),
        )
        bug = DSITBug(
            checker=checker,
            file_path=file_path,
            line=_to_int(loc.get("line"), -1),
            column=_to_int(loc.get("column"), -1),
            message=message,
            rule_id=rule_id,
            force=_derive_force(rule_id, str(defect.get("force") or "0")),
            type_code=str(first.get("type") or defect.get("type") or "0"),
            status=str(defect.get("status") or "0"),
        )
        bucket = by_file.setdefault(
            file_path or "unknown",
            {"source_path": source_path, "bugs": []},
        )
        bucket["bugs"].append(bug)

    report = DSITReport(report_id=report_id, project_name=project_name, project_path=project_path)
    for file_path, item in sorted(by_file.items()):
        file_functions = []
        if functions_map:
            raw_fns = functions_map.get(file_path) or functions_map.get(item["source_path"]) or []
            if isinstance(raw_fns, list):
                file_functions = [
                    DSITFunction(
                        name=str(fn.get("name", "")),
                        start_line=int(fn.get("start_line", 0)),
                        start_column=int(fn.get("start_column", 0)),
                        end_line=int(fn.get("end_line", 0)),
                        end_column=int(fn.get("end_column", 0)),
                    )
                    for fn in raw_fns if isinstance(fn, dict)
                ]
        report.files_stats.append(_build_file_stats(file_path, item["source_path"], item["bugs"], file_functions))
    return report


def report_from_xplusx_bugs(
    bugs_raw: List[Dict[str, Any]],
    report_id: str,
    project_name: str,
    project_path: str,
    functions_map: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> DSITReport:
    by_file: Dict[str, Dict[str, Any]] = {}
    for item in bugs_raw:
        if not isinstance(item, dict):
            continue
        loc = item.get("location_start") or {}
        source_path = str(item.get("path") or item.get("file_path") or "")
        file_path = _project_relative_path(source_path, project_path)
        message = str(item.get("message") or "")
        standard = str(item.get("standard") or "")
        checker = str(item.get("checker") or "")
        rule_id = _normalize_rule_id(standard, checker, message)
        force = _derive_force(rule_id, str(item.get("force") or "0"))
        bug = DSITBug(
            checker=checker,
            file_path=file_path,
            line=_to_int(loc.get("line"), -1),
            column=_to_int(loc.get("column"), -1),
            message=message,
            rule_id=rule_id,
            force=force,
            type_code=str(item.get("type") or "0"),
            status=str(item.get("status") or "0"),
        )
        bucket = by_file.setdefault(
            file_path or "unknown",
            {"source_path": source_path, "bugs": []},
        )
        bucket["bugs"].append(bug)

    report = DSITReport(report_id=report_id, project_name=project_name, project_path=project_path)
    for file_path, item in sorted(by_file.items()):
        file_functions = []
        if functions_map:
            raw_fns = functions_map.get(file_path) or functions_map.get(item["source_path"]) or []
            if isinstance(raw_fns, list):
                file_functions = [
                    DSITFunction(
                        name=str(fn.get("name", "")),
                        start_line=int(fn.get("start_line", 0)),
                        start_column=int(fn.get("start_column", 0)),
                        end_line=int(fn.get("end_line", 0)),
                        end_column=int(fn.get("end_column", 0)),
                    )
                    for fn in raw_fns if isinstance(fn, dict)
                ]
        report.files_stats.append(_build_file_stats(file_path, item["source_path"], item["bugs"], file_functions))
    return report


def load_recent_xplusx_bugs_with_files(
    workdir: str | Path,
    since: float = 0,
) -> tuple[List[Dict[str, Any]], List[str]]:
    root = Path(workdir)
    if not root.is_dir():
        return [], []
    result: List[Dict[str, Any]] = []
    found_files: List[str] = []
    for path in root.rglob("*.xplusx.err"):
        try:
            if since and path.stat().st_mtime + 5 < since:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        bugs = data.get("bugs") if isinstance(data, dict) else None
        if isinstance(bugs, list):
            found_files.append(str(path))
            result.extend([bug for bug in bugs if isinstance(bug, dict)])
    return result, found_files


def load_recent_xplusx_bugs(workdir: str | Path, since: float = 0) -> List[Dict[str, Any]]:
    bugs, _ = load_recent_xplusx_bugs_with_files(workdir, since)
    return bugs


def _derive_force(rule_id: str, dcab_force: str) -> str:
    """根据规则 ID 推导强制级别，弥补 DCAB 未正确返回 force 字段的问题。

    GJB 8114 规则体系中：
    - GJB-R-*（Required）→ force="1" → Error
    - GJB-A-*（Advisory）→ force="0" → Warning
    - MISRA *:R-*（Required）→ force="1" → Error
    """
    if dcab_force == "1":
        return "1"
    if not rule_id:
        return dcab_force or "0"
    rid = rule_id.upper()
    # GJB Required 规则: GJB-R-*
    if re.search(r'\bGJB[-:]R\b', rid):
        return "1"
    # MISRA Required 规则: MISRA*:R-*
    if re.search(r'MISRA[^:]*:R[-]', rid):
        return "1"
    return dcab_force or "0"


def _normalize_rule_id(*values: Any) -> str:
    for value in values:
        rule_id = _extract_rule_id_from_text(str(value or ""))
        if rule_id:
            return rule_id
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return "UNKNOWN_RULE"


def _extract_rule_id_from_text(text: str) -> str:
    match = re.search(
        r"(GJB[-:][AR]-\d+-\d+-\d+|GJB-8114:[AR]-\d+-\d+-\d+(?::\d+)?|MISRA[-:][A-Z]-\d+(?:-\d+)+)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return ""
    value = match.group(1).upper()
    gjb8114 = re.match(r"GJB-8114:([AR]-\d+-\d+-\d+)(?::\d+)?$", value)
    if gjb8114:
        return "GJB-" + gjb8114.group(1)
    return value.replace("GJB:", "GJB-")


def _project_relative_path(file_path: str, project_path: str) -> str:
    if not file_path:
        return ""
    path = Path(file_path)
    try:
        rel = path.resolve().relative_to(Path(project_path).resolve())
        return rel.as_posix()
    except Exception:
        return path.as_posix()


def _build_file_stats(
    display_path: str,
    source_path: str,
    bugs: List[DSITBug],
    functions: Optional[List[DSITFunction]] = None,
) -> DSITFileStats:
    stats = _lightweight_source_stats(source_path)
    fn_count = len(functions) if functions else stats["function_count"]
    return DSITFileStats(
        file_path=display_path,
        total_lines=stats["total_lines"],
        total_statements=stats["total_statements"],
        function_count=fn_count,
        function_max_depth=0,
        comment_lines=stats["comment_lines"],
        bugs=bugs,
        functions=functions or [],
    )


def _lightweight_source_stats(source_path: str) -> Dict[str, int]:
    path = Path(source_path)
    if not path.is_file():
        return {
            "total_lines": 0,
            "total_statements": 0,
            "function_count": 0,
            "comment_lines": 0,
        }
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {
            "total_lines": 0,
            "total_statements": 0,
            "function_count": 0,
            "comment_lines": 0,
        }

    lines = text.splitlines()
    comment_lines = 0
    in_block = False
    for line in lines:
        stripped = line.strip()
        if in_block:
            comment_lines += 1
            if "*/" in stripped:
                in_block = False
            continue
        if stripped.startswith("//"):
            comment_lines += 1
        if "/*" in stripped:
            comment_lines += 1
            if "*/" not in stripped[stripped.find("/*") + 2:]:
                in_block = True

    function_pattern = re.compile(
        r"^\s*(?:[A-Za-z_][\w:<>~*&\s]+)\s+[A-Za-z_]\w*\s*\([^;{}]*\)\s*\{",
        re.MULTILINE,
    )
    return {
        "total_lines": len(lines),
        "total_statements": text.count(";"),
        "function_count": len(function_pattern.findall(text)),
        "comment_lines": comment_lines,
    }


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
