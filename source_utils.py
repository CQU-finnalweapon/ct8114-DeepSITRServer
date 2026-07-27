import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SOURCE_FILE_SUFFIXES = {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hxx"}
SOURCE_FILE_ENCODINGS = ("utf-8", "utf-8-sig", "gbk", "gb18030", "latin-1")


def normalize_source_path(value: str) -> str:
    path = str(value or "").replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    return path.lstrip("/")


def source_file_allowed(path: Path) -> bool:
    return path.suffix.lower() in SOURCE_FILE_SUFFIXES


def path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def scan_source_files(source_root: Path) -> List[dict]:
    files: List[dict] = []
    resolved_root = source_root.resolve()
    for dirpath, dirnames, filenames in os.walk(resolved_root):
        dirnames[:] = [name for name in dirnames if not name.startswith(".")]
        for filename in filenames:
            path = Path(dirpath) / filename
            if not source_file_allowed(path):
                continue
            resolved_path = path.resolve()
            stat = resolved_path.stat()
            files.append(
                {
                    "file_path": resolved_path.relative_to(resolved_root).as_posix(),
                    "name": resolved_path.name,
                    "ext": resolved_path.suffix.lower(),
                    "size": stat.st_size,
                }
            )
    return sorted(files, key=lambda item: item["file_path"])


def read_json_file(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8-sig")
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    data = json.loads(text)
    return data if isinstance(data, dict) else {}


def load_report_files_stats(path: Path) -> List[dict]:
    if not path.is_file():
        return []
    data = read_json_file(path)
    report = data.get("report") if isinstance(data.get("report"), dict) else {}
    files_stats = report.get("files_stats") if isinstance(report, dict) else []
    return [item for item in files_stats if isinstance(item, dict)]


def _normalize_report_path(value: str, source_root: Optional[Path]) -> str:
    raw = str(value or "")
    if source_root is not None:
        try:
            path = Path(raw)
            if path.is_absolute():
                return path.resolve().relative_to(source_root).as_posix()
        except Exception:
            pass
    return normalize_source_path(raw)


def build_report_indexes(
    files_stats: List[dict],
    source_root: Optional[Path] = None,
) -> Tuple[Dict[str, dict], Dict[str, List[dict]]]:
    by_path: Dict[str, dict] = {}
    by_name: Dict[str, List[dict]] = {}
    for item in files_stats:
        rel = _normalize_report_path(str(item.get("file_path") or ""), source_root)
        if not rel:
            continue
        by_path[rel] = item
        by_name.setdefault(Path(rel).name, []).append(item)
    return by_path, by_name


def match_report_for_path(
    rel_path: str,
    by_path: Dict[str, dict],
    by_name: Dict[str, List[dict]],
) -> Tuple[Optional[dict], Optional[str]]:
    normalized = normalize_source_path(rel_path)
    if normalized in by_path:
        return by_path[normalized], None
    basename_matches = by_name.get(Path(normalized).name, [])
    if len(basename_matches) == 1:
        return basename_matches[0], None
    if len(basename_matches) > 1:
        return None, f"multiple report entries match basename {Path(normalized).name!r}"
    return None, None


def read_source_text(path: Path) -> Tuple[str, str]:
    raw = path.read_bytes()
    last_error: Optional[Exception] = None
    for encoding in SOURCE_FILE_ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return "", SOURCE_FILE_ENCODINGS[0]


def resolve_source_file(source_root: Path, file_path: str) -> Tuple[Path, str]:
    raw_file_path = str(file_path or "")
    if Path(raw_file_path).is_absolute() or raw_file_path.startswith(("/", "\\")):
        raise ValueError("path traversal is not allowed")

    rel_path = normalize_source_path(raw_file_path)
    if not rel_path:
        raise ValueError("file_path is required")

    resolved_root = source_root.resolve()
    source_file = (resolved_root / rel_path).resolve()
    if not path_is_relative_to(source_file, resolved_root):
        raise ValueError("path traversal is not allowed")
    if not source_file_allowed(source_file):
        raise ValueError(f"source suffix is not allowed: {source_file.suffix}")

    return source_file, source_file.relative_to(resolved_root).as_posix()
