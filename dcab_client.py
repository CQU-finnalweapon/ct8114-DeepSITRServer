"""HTTP client and result adapters for DCA/DeepSITRServer progress APIs."""

from __future__ import annotations

import http.client

import copy
import json
import os
import re
import socket
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

from dsit_parser import DSITBug, DSITFileStats, DSITReport


DEFAULT_RULE_IDS = [
    "GJB-8114:A-1-10-1:0",
    "GJB-8114:A-1-10-2:0",
    "GJB-8114:R-1-8-1:0",
    "GJB-8114:R-1-8-2:0",
]

CFG_DIR_CANDIDATES = [
    Path("/opt/dcab/cfg"),
    Path("/app/DeepSITRServer/cfg"),
    Path(__file__).resolve().parent / "DeepSITRServer" / "cfg",
]

RULE_SET_CONFIGS = {
    "GJB-8114": {
        "filenames": ("gjb8114-rules-zh_CN.xml", "gjb8114-rules.xml"),
        "env": "DCAB_GJB8114_RULES_FILE",
    },
    "GJB-5369": {"filenames": ("gjb5369-rules-zh_CN.xml", "gjb5369-rules.xml")},
    "CWE-C": {"filenames": ("cwe-c-rules-zh_CN.xml", "cwe-c-rules.xml")},
    "MISRA-2008": {"filenames": ("misra2008-rules-zh_CN.xml", "misra2008-rules.xml")},
    "MISRA-2012": {"filenames": ("misra2012-rules-zh_CN.xml", "misra2012-rules.xml")},
}

RULE_SET_ORDER = list(RULE_SET_CONFIGS)

RULE_CONFIG_CANDIDATES = [
    str(path)
    for path in [
        Path("/opt/dcab/cfg/gjb8114-rules-zh_CN.xml"),
        Path("/opt/dcab/cfg/gjb8114-rules.xml"),
        Path(__file__).resolve().parent / "DeepSITRServer" / "cfg" / "gjb8114-rules-zh_CN.xml",
        Path(__file__).resolve().parent / "DeepSITRServer" / "cfg" / "gjb8114-rules.xml",
    ]
]

_RULE_NAME_RE = re.compile(r"^[RAMD]-\d+(?:-\d+)*$")
_ENGINE_RULE_ID_RE = re.compile(
    r"^(GJB-8114|GJB-5369|CWE-C|MISRA-2008|MISRA-2012):([RAMD]-\d+(?:-\d+)*)(?::\d+)?$",
    re.IGNORECASE,
)
_ALL_RULE_IDS_CACHE: Optional[List[str]] = None
_RULE_SET_CACHE: Dict[str, Dict[str, Any]] = {}


class DcabClientError(RuntimeError):
    """Raised when the DCA HTTP service cannot be called successfully."""


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def _as_text_or_empty(value: Any) -> str:
    return "" if value in (None, "") else str(value)


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


def normalize_rule_set(rule_set: Any) -> str:
    text = str(rule_set or "").strip().upper().replace("_", "-")
    aliases = {
        "": "GJB-8114",
        "GJB8114": "GJB-8114",
        "GJB-8114": "GJB-8114",
        "GJB5369": "GJB-5369",
        "GJB-5369": "GJB-5369",
        "CWEC": "CWE-C",
        "CWE-C": "CWE-C",
        "MISRA2008": "MISRA-2008",
        "MISRA-2008": "MISRA-2008",
        "MISRA2012": "MISRA-2012",
        "MISRA-2012": "MISRA-2012",
    }
    normalized = aliases.get(text)
    if not normalized:
        raise DcabClientError(f"Unsupported rule_set: {rule_set!r}")
    return normalized


def _rule_set_file_candidates(rule_set: str) -> List[Path]:
    config = RULE_SET_CONFIGS[rule_set]
    candidates: List[Path] = []
    env_name = config.get("env")
    if env_name:
        env_path = os.environ.get(str(env_name), "").strip()
        if env_path:
            candidates.append(Path(env_path))
    for cfg_dir in CFG_DIR_CANDIDATES:
        for filename in config["filenames"]:
            candidates.append(cfg_dir / filename)
    return candidates


def _rule_names_from_xml(path: Path) -> List[str]:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError, UnicodeDecodeError):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        return [
            match.group(1).strip()
            for match in re.finditer(r"<rule\s+[^>]*name=[\"']([^\"']+)[\"']", text)
        ]
    return [
        (elem.attrib.get("name") or "").strip()
        for elem in root.iter("rule")
    ]


def format_engine_rule_id(rule_id: str) -> str:
    value = str(rule_id or "").strip()
    match = _ENGINE_RULE_ID_RE.match(value)
    if not match:
        return value
    return f"{match.group(1).upper()}:{match.group(2).upper()}:0"


def _rule_type(rule_id: str) -> str:
    match = re.search(r":([A-Z])-|\b([A-Z])-", rule_id)
    return (match.group(1) or match.group(2)).upper() if match else ""


def load_rule_set_rule_ids(rule_set: Any) -> Dict[str, Any]:
    selected_rule_set = normalize_rule_set(rule_set)
    if selected_rule_set in _RULE_SET_CACHE:
        return copy.deepcopy(_RULE_SET_CACHE[selected_rule_set])

    seen = set()
    raw_rule_ids: List[str] = []
    loaded_from = ""
    for path in _rule_set_file_candidates(selected_rule_set):
        if not path.exists():
            continue
        names = _rule_names_from_xml(path)
        for name in names:
            if not _RULE_NAME_RE.match(name):
                continue
            full = f"{selected_rule_set}:{name.upper()}"
            if full in seen:
                continue
            seen.add(full)
            raw_rule_ids.append(full)
        if raw_rule_ids:
            loaded_from = str(path)
            break

    if not raw_rule_ids:
        raise DcabClientError(f"No rule ids found for rule_set {selected_rule_set}")

    selected = [rule_id for rule_id in raw_rule_ids if _rule_type(rule_id) != "D"]
    info = {
        "rule_set": selected_rule_set,
        "raw_rule_ids": raw_rule_ids,
        "rule_ids": [format_engine_rule_id(rule_id) for rule_id in selected],
        "raw_count": len(raw_rule_ids),
        "selected_count": len(selected),
        "filtered_document_count": len(raw_rule_ids) - len(selected),
        "loaded_from": loaded_from,
    }
    _RULE_SET_CACHE[selected_rule_set] = copy.deepcopy(info)
    return copy.deepcopy(info)


def infer_rule_prefix_from_checker(checker: Any) -> str:
    text = str(checker or "").strip().upper()
    for rule_set in RULE_SET_ORDER:
        if text.startswith(rule_set + ":"):
            return rule_set
    return ""


def _ordered_rule_sets(values: set[str]) -> List[str]:
    return [rule_set for rule_set in RULE_SET_ORDER if rule_set in values]


def filter_report_by_rule_set(report: DSITReport, selected_rule_set: Any) -> tuple[DSITReport, Dict[str, Any]]:
    rule_set = normalize_rule_set(selected_rule_set)
    raw_sets: set[str] = set()
    result_sets: set[str] = set()
    removed = 0
    filtered_stats: List[DSITFileStats] = []

    for fs in report.files_stats:
        kept_bugs: List[DSITBug] = []
        for bug in fs.bugs:
            prefix = infer_rule_prefix_from_checker(bug.checker)
            if prefix:
                raw_sets.add(prefix)
            if prefix == rule_set:
                result_sets.add(prefix)
                kept_bugs.append(bug)
            else:
                removed += 1
        filtered_stats.append(
            DSITFileStats(
                file_path=fs.file_path,
                total_lines=fs.total_lines,
                total_statements=fs.total_statements,
                total_declares=fs.total_declares,
                function_count=fs.function_count,
                function_max_lines=fs.function_max_lines,
                function_max_depth=fs.function_max_depth,
                comment_lines=fs.comment_lines,
                code_size=fs.code_size,
                bugs=kept_bugs,
                functions=list(fs.functions),
            )
        )

    filtered = DSITReport(
        report_id=report.report_id,
        project_name=report.project_name,
        project_path=report.project_path,
        files_stats=filtered_stats,
    )
    return filtered, {
        "raw_result_rule_sets": _ordered_rule_sets(raw_sets),
        "result_rule_sets": _ordered_rule_sets(result_sets),
        "filtered_bug_count": removed,
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
        for name in _rule_names_from_xml(path):
            if not _RULE_NAME_RE.match(name) or name in seen:
                continue
            seen.add(name)
            rule_ids.append(format_engine_rule_id(f"GJB-8114:{name}"))
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
    formatted = format_engine_rule_id(value)
    if formatted != value:
        return formatted
    if value.startswith("GJB-8114:"):
        return format_engine_rule_id(value)
    if _RULE_NAME_RE.match(value):
        return format_engine_rule_id(f"GJB-8114:{value}")
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


def start_progress(
    project_path: str,
    rule_ids: Optional[List[str]] = None,
    excluded_paths: Optional[List[str]] = None,
) -> Dict[str, Any]:
    config = get_dcab_config()
    body = {
        "project_path": project_path,
        "rule_ids": list(rule_ids) if rule_ids else configured_rule_ids(),
        "excluded_paths": excluded_paths or [],
    }
    url = config["base_url"] + config["start_path"]
    try:
        data = _request_json(
            url,
            config["start_method"],
            body,
            config["timeout"],
        )
    except DcabClientError as exc:
        raise DcabClientError(
            "DCAB start_progress failed; DCAB may have crashed or closed the connection: "
            f"{exc}"
        ) from exc
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
    except (http.client.RemoteDisconnected, ConnectionRefusedError, socket.timeout, TimeoutError) as exc:
        raise DcabClientError(f"{method} {url} failed: {exc}") from exc

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


_REQUIRED_ADVISORY_KEYS = {
    "required_advisory",
    "requiredAdvisory",
    "severity",
    "rule_level",
    "ruleLevel",
    "level",
    "category",
    "priority",
    "checker",
    "message",
    "rule_id",
    "standard",
}


def _canonical_required_advisory(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in {"required", "advisory"}:
        return "Required" if lowered == "required" else "Advisory"
    match = re.search(r"(?<![-\\w])(required|advisory)(?![-\\w])", text, re.IGNORECASE)
    if match:
        return "Required" if match.group(1).lower() == "required" else "Advisory"
    return ""



def _extract_gjb_rule_prefix(*values: Any) -> str:
    for value in values:
        text = str(value or "")
        match = re.search(
            r"\b(?:GJB-8114|GJB-5369|CWE-C|MISRA-2008|MISRA-2012):([RAMD])-\d+(?:-\d+)*",
            text,
            re.IGNORECASE,
        )
        if match:
            kind = match.group(1).upper()
            if kind == "A":
                return "Advisory"
            if kind == "M":
                return "Mandatory"
            if kind == "D":
                return "Document"
            return "Required"
        legacy = re.search(r"\bGJB[-:]?(?:8114:)?([RA])-\d+-\d+-\d+", text, re.IGNORECASE)
        if legacy:
            return "Required" if legacy.group(1).upper() == "R" else "Advisory"
    return ""
def _extract_required_advisory(value: Any, depth: int = 0) -> str:
    if depth > 4:
        return ""
    if not isinstance(value, (dict, list)):
        direct = _canonical_required_advisory(value)
        if direct:
            return direct
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _REQUIRED_ADVISORY_KEYS:
                direct = _canonical_required_advisory(item)
                if direct:
                    return direct
        for item in value.values():
            nested = _extract_required_advisory(item, depth + 1)
            if nested:
                return nested
    elif isinstance(value, list):
        for item in value:
            nested = _extract_required_advisory(item, depth + 1)
            if nested:
                return nested
    return ""


def _clean_path_text(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/")


def _path_key(path: str) -> str:
    return _clean_path_text(path).lower()


def _to_optional_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_function(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    item = {
        "name": _as_text_or_empty(raw.get("name")),
        "start_line": _to_optional_int(raw.get("start_line")),
        "start_column": _to_optional_int(raw.get("start_column")),
        "end_line": _to_optional_int(raw.get("end_line")),
        "end_column": _to_optional_int(raw.get("end_column")),
    }
    if not item["name"] and all(item[key] is None for key in ("start_line", "start_column", "end_line", "end_column")):
        return None
    return item


def _source_file_entries(source_file_list: Any, project_path: str) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    if not isinstance(source_file_list, list):
        return result
    for item in source_file_list:
        source_path = ""
        raw_functions: Any = None
        if isinstance(item, str):
            source_path = item
        elif isinstance(item, dict):
            source_path = str(item.get("file_path") or item.get("path") or item.get("file") or "")
            raw_functions = item.get("functions")
        if not source_path:
            continue
        file_path = _project_relative_path(source_path, project_path)
        functions = []
        if isinstance(raw_functions, list):
            functions = [func for func in (_normalize_function(raw) for raw in raw_functions) if func]
        result[_path_key(file_path or source_path)] = {
            "file_path": file_path or _clean_path_text(source_path),
            "source_path": source_path,
            "functions": functions,
        }
    return result


def source_file_list_from_response(data: Optional[Dict[str, Any]]) -> List[Any]:
    if not isinstance(data, dict):
        return []
    raw = data.get("source_file_list")
    if raw is None and isinstance(data.get("data"), dict):
        raw = data["data"].get("source_file_list")
    return raw if isinstance(raw, list) else []


def controlled_dcab_raw(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    defect_list = data.get("defect_list")
    return {
        "source_file_list": source_file_list_from_response(data),
        "progress_info": data.get("progress_info") if isinstance(data.get("progress_info"), dict) else {},
        "defect_list_count": len(defect_list) if isinstance(defect_list, list) else None,
    }
def report_from_defect_list(
    defect_list: List[Dict[str, Any]],
    report_id: str,
    project_name: str,
    project_path: str,
    source_file_list: Optional[List[Any]] = None,
) -> DSITReport:
    by_file: Dict[str, Dict[str, Any]] = {}

    for entry in _source_file_entries(source_file_list, project_path).values():
        key = _path_key(entry["file_path"])
        by_file[key] = {
            "file_path": entry["file_path"],
            "source_path": entry["source_path"],
            "bugs": [],
            "functions": entry["functions"],
        }

    for defect in defect_list:
        if not isinstance(defect, dict):
            continue
        traces = defect.get("tracking_path_list") or []
        first = traces[0] if traces and isinstance(traces[0], dict) else {}
        loc = first.get("location_start") or {}
        source_path = str(first.get("file_path") or defect.get("file_path") or defect.get("path") or "")
        file_path = _project_relative_path(source_path, project_path)
        checker = str(defect.get("checker") or defect.get("rule_id") or "")
        message = str(first.get("descript") or defect.get("message") or "")
        rule_id = _normalize_rule_id(
            defect.get("rule_id"),
            checker,
            message,
            str(defect.get("standard") or ""),
        )
        rule_prefix_severity = _extract_gjb_rule_prefix(rule_id, defect.get("rule_id"), checker, message, defect.get("message"), str(defect.get("standard") or ""))
        required_advisory = _extract_required_advisory(defect)
        raw_severity = rule_prefix_severity or required_advisory or defect.get("severity")
        bug = DSITBug(
            checker=checker,
            file_path=file_path,
            line=_to_int(loc.get("line"), -1),
            column=_to_int(loc.get("column"), -1),
            message=message,
            rule_id=rule_id,
            force=_as_text_or_empty(defect.get("force")),
            type_code=_as_text_or_empty(first.get("type") or defect.get("type")),
            status=_as_text_or_empty(defect.get("status")),
            severity=_as_text_or_empty(raw_severity),
            raw_severity=raw_severity,
        )
        if rule_prefix_severity:
            bug.severity_source = "dcab.rule_id_prefix"
        elif required_advisory:
            bug.severity_source = "dcab.required_advisory"
        key = _path_key(file_path or source_path or "unknown")
        bucket = by_file.setdefault(
            key,
            {
                "file_path": file_path or "unknown",
                "source_path": source_path,
                "bugs": [],
                "functions": [],
            },
        )
        bucket["bugs"].append(bug)
        if source_path and not bucket.get("source_path"):
            bucket["source_path"] = source_path

    report = DSITReport(report_id=report_id, project_name=project_name, project_path=project_path)
    for item in sorted(by_file.values(), key=lambda value: value["file_path"]):
        report.files_stats.append(
            _build_file_stats(
                item["file_path"],
                item["source_path"],
                item["bugs"],
                item["functions"],
            )
        )
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
        force = _as_text_or_empty(item.get("force"))
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
            type_code=_as_text_or_empty(item.get("type")),
            status=_as_text_or_empty(item.get("status")),
            severity=_as_text_or_empty(item.get("severity")),
            raw_severity=item.get("severity"),
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
        r"(GJB[-:][AR]-\d+-\d+-\d+|GJB-8114:[RAMD]-\d+(?:-\d+)+(?::\d+)?|GJB-5369:[RAMD]-\d+(?:-\d+)+(?::\d+)?|CWE-C:[RAMD]-\d+(?:-\d+)+(?::\d+)?|MISRA-(?:2008|2012):[RAMD]-\d+(?:-\d+)+(?::\d+)?|MISRA[-:][A-Z]-\d+(?:-\d+)+)",
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
    cleaned = _clean_path_text(file_path)
    project_clean = _clean_path_text(project_path).rstrip("/")
    if project_clean and cleaned.lower().startswith(project_clean.lower() + "/"):
        return cleaned[len(project_clean) + 1:]
    path = Path(file_path)
    try:
        rel = path.resolve().relative_to(Path(project_path).resolve())
        return rel.as_posix()
    except Exception:
        return cleaned or path.as_posix()


def _build_file_stats(display_path: str, source_path: str, bugs: List[DSITBug], functions: Optional[List[Dict[str, Any]]] = None) -> DSITFileStats:
    stats = _lightweight_source_stats(source_path)
    return DSITFileStats(
        file_path=display_path,
        total_lines=stats["total_lines"],
        total_statements=stats["total_statements"],
        function_count=max(stats["function_count"], len(functions or [])),
        function_max_depth=0,
        comment_lines=stats["comment_lines"],
        bugs=bugs,
        functions=list(functions or []),
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


