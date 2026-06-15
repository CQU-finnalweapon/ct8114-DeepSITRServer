"""HTTP client and result adapters for DCA/DeepSITRServer progress APIs."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from dsit_parser import DSITBug, DSITFileStats, DSITReport


DEFAULT_RULE_IDS = [
    "GJB-8114:A-1-10-1:0",
    "GJB-8114:A-1-10-2:0",
    "GJB-8114:R-1-8-1:0",
    "GJB-8114:R-1-8-2:0",
]


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
    raw = os.environ.get("DCAB_RULE_IDS", "")
    if not raw.strip():
        return list(DEFAULT_RULE_IDS)
    return [item.strip() for item in raw.split(",") if item.strip()]


def strip_detection_braces(detection_id: Any) -> str:
    return str(detection_id or "").strip().strip("{}")


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
    data["detection_id"] = strip_detection_braces(detection_id)
    return data


def check_progress(detection_id: str) -> Optional[Dict[str, Any]]:
    config = get_dcab_config()
    return _request_json(
        config["base_url"] + config["check_path"],
        config["check_method"],
        {"detection_id": strip_detection_braces(detection_id)},
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
    if "defect_list" in data:
        return False
    if data.get("defect_list") in (None, []) and len(data) == 1:
        return True
    return False


def report_from_defect_list(
    defect_list: List[Dict[str, Any]],
    report_id: str,
    project_name: str,
    project_path: str,
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
            force=str(defect.get("force") or "0"),
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
        report.files_stats.append(_build_file_stats(file_path, item["source_path"], item["bugs"]))
    return report


def report_from_xplusx_bugs(
    bugs_raw: List[Dict[str, Any]],
    report_id: str,
    project_name: str,
    project_path: str,
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
        force = str(item.get("force") or "0")
        checker = str(item.get("checker") or "")
        rule_id = _normalize_rule_id(standard, checker, message)
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
        report.files_stats.append(_build_file_stats(file_path, item["source_path"], item["bugs"]))
    return report


def load_recent_xplusx_bugs(workdir: str | Path, since: float = 0) -> List[Dict[str, Any]]:
    root = Path(workdir)
    if not root.is_dir():
        return []
    result: List[Dict[str, Any]] = []
    for path in root.rglob("*.xplusx.err"):
        try:
            if since and path.stat().st_mtime + 5 < since:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        bugs = data.get("bugs") if isinstance(data, dict) else None
        if isinstance(bugs, list):
            result.extend([bug for bug in bugs if isinstance(bug, dict)])
    return result


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


def _build_file_stats(display_path: str, source_path: str, bugs: List[DSITBug]) -> DSITFileStats:
    stats = _lightweight_source_stats(source_path)
    return DSITFileStats(
        file_path=display_path,
        total_lines=stats["total_lines"],
        total_statements=stats["total_statements"],
        function_count=stats["function_count"],
        function_max_depth=0,
        comment_lines=stats["comment_lines"],
        bugs=bugs,
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
