"""基于 FastAPI 的 GJB 8114 代码分析服务 (DeepSITRServer/codetidy 引擎).

本服务使用 DeepSITRServer 内置的 codetidy.exe 作为唯一分析引擎，
完全替代了原有的 clang-tidy + 插件方案。

工作流程概览
------------

A. 即时上传分析::

    POST /analyze
        multipart files=<file1>&files=<file2>...
        ?entry=test.c&keep=false

   1. 为本次请求生成 UUID, 在系统临时目录下建立专用工作目录;
   2. 把上传的文件落盘到该目录, 调用 codetidy.exe 进行分析;
   3. 解析输出，以 DSIT 兼容格式 (JSON) 返回前端;
   4. 清理临时目录 (可通过 ``?keep=true`` 关闭).

B. UniPortal / 本工具私有项目分析::

    GET    /projects                         # 列出两个数据源的项目
    GET    /projects/{project_id}/files      # 列出项目内可分析的源文件
    POST   /projects/{project_id}/analyze    # 对项目运行 codetidy.exe
    DELETE /projects/{project_id}            # 只能删私有卷里的项目

C. DeepSITRServer 报告加载::

    POST   /dsit/upload-local               # 加载预生成的 DSIT 输出目录
    GET    /dsit/reports                     # 列出已加载报告
    GET    /dsit/report/{id}                 # 获取报告详情

启动方式::

    uvicorn server:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import copy
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from dsit_parser import (
    CODETIDY_NOT_FOUND_MESSAGE,
    DSITBug,
    DSITFileStats,
    DSITReport,
    analyze_with_codetidy,
    find_codetidy_bin,
    get_codetidy_search_paths,
    parse_output_dir,
)
from dcab_client import (
    DcabClientError,
    alternate_detection_id_format,
    check_progress,
    configured_rule_ids,
    controlled_dcab_raw,
    get_dcab_config,
    filter_report_by_rule_set,
    is_empty_check_response,
    load_rule_set_rule_ids,
    load_recent_xplusx_bugs_with_files,
    normalize_detection_id,
    normalize_rule_set,
    report_from_defect_list,
    report_from_xplusx_bugs,
    source_file_list_from_response,
    start_progress,
)
from routers_dsit import router as dsit_router
from source_routes import register_source_routes


logger = logging.getLogger(__name__)


STATIC_DIR = Path(__file__).resolve().parent / "static"

# ============================================================================
# 配置
# ============================================================================

# 限制即时上传分析的文件总大小 (默认 5MB)
MAX_TOTAL_BYTES = int(os.environ.get("MAX_TOTAL_BYTES", str(5 * 1024 * 1024)))
MAX_ZIP_BYTES = int(os.environ.get("MAX_ZIP_BYTES", str(50 * 1024 * 1024)))
MAX_ZIP_EXTRACT_BYTES = int(os.environ.get("MAX_ZIP_EXTRACT_BYTES", str(200 * 1024 * 1024)))
ALLOWED_SUFFIXES = {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hxx"}

# ---- UniPortal 双源接入相关配置 ----------------------------------------
UNIPORTAL_STORAGE_PATH = os.environ.get("UNIPORTAL_STORAGE_PATH")
UNIPORTAL_MODE = bool(UNIPORTAL_STORAGE_PATH)
# 共享卷是否可写（默认可写，兼容旧部署设置 :ro 时自动退化为只读）
UNIPORTAL_WRITABLE = os.environ.get("UNIPORTAL_WRITABLE", "true").lower() == "true"
# 本地模拟共享卷目录（用于开发/测试，无需真实 UniPortal）
MOCK_UNIPORTAL_DIR = os.environ.get("MOCK_UNIPORTAL_DIR", "")
LOCAL_WORKSPACES_DIR = Path(
    os.environ.get("LOCAL_WORKSPACES_DIR", "workspaces")
)
LOCAL_PROJECTS_DIR = LOCAL_WORKSPACES_DIR / "projects"
# 报告存储目录
REPORTS_DIR = Path(os.environ.get("REPORTS_DIR", "workspaces/_reports"))
DCAB_SAFE_ROOT = Path(os.environ.get("DCAB_SAFE_ROOT", "/tmp/ct8114/dcab_safe"))
ENABLE_PROJECT_DELETE = os.environ.get("ENABLE_PROJECT_DELETE", "false").lower() == "true"

# 模拟分析模式（本地测试用，无需 codetidy.exe）
MOCK_ANALYSIS = os.environ.get("MOCK_ANALYSIS", "").lower() == "true"
ANALYSIS_ENGINE = os.environ.get("ANALYSIS_ENGINE", "codetidy").strip().lower() or "codetidy"

SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx"}
HEADER_SUFFIXES = {".h", ".hpp", ".hxx"}
CODE_SUFFIXES = SOURCE_SUFFIXES | HEADER_SUFFIXES

_TOOL_INTERNAL_DIRS = {"ct8114", "_ct8114", "_reports", "_dsit_reports", "__pycache__", ".git", ".idea", ".vscode"}

# =====================================================================
# 异步分析任务存储
# =====================================================================

# 任务状态: pending → running → completed / failed
_TASK_STORE: Dict[str, dict] = {}
_TASK_STORE_LOCK = threading.Lock()
_DCAB_ANALYSIS_LOCK = threading.Lock()
_DCAB_AGGREGATE_REPORTS: Dict[str, DSITReport] = {}

# 任务过期时间 (秒), 超时自动清理
CODETIDY_TIMEOUT = int(os.environ.get("CODETIDY_TIMEOUT", "14400"))
TASK_TTL_SECONDS = int(os.environ.get("TASK_TTL_SECONDS", "21600"))
DCAB_POLL_INTERVAL_SECONDS = float(os.environ.get("DCAB_POLL_INTERVAL_SECONDS", "2"))


def _cleanup_expired_tasks() -> int:
    """清理过期任务，返回清理数量."""
    now = time.time()
    expired = []
    with _TASK_STORE_LOCK:
        for rid, task in _TASK_STORE.items():
            created = task.get("created_at", 0)
            if now - created > TASK_TTL_SECONDS:
                expired.append(rid)
        for rid in expired:
            task = _TASK_STORE.get(rid, {})
            if task.get("dcab_lock_held") and _DCAB_ANALYSIS_LOCK.locked():
                try:
                    _DCAB_ANALYSIS_LOCK.release()
                except RuntimeError:
                    pass
            del _TASK_STORE[rid]
            _DCAB_AGGREGATE_REPORTS.pop(rid, None)
    return len(expired)


def _set_task_status(request_id: str, status: str, **extra) -> None:
    """线程安全地更新任务状态."""
    with _TASK_STORE_LOCK:
        if request_id in _TASK_STORE:
            should_release_dcab = (
                status in {"completed", "failed"}
                and _TASK_STORE[request_id].get("dcab_lock_held")
            )
            _TASK_STORE[request_id]["status"] = status
            _TASK_STORE[request_id]["updated_at"] = time.time()
            _TASK_STORE[request_id].update(extra)
            if should_release_dcab:
                _TASK_STORE[request_id]["dcab_lock_held"] = False
                if _DCAB_ANALYSIS_LOCK.locked():
                    try:
                        _DCAB_ANALYSIS_LOCK.release()
                    except RuntimeError:
                        pass


def _build_report_payload(
    request_id: str,
    project_id: str,
    report: DSITReport,
) -> dict:
    report.report_id = request_id
    return {
        "request_id": request_id,
        "project_id": project_id or request_id,
        "report": report.to_dict(),
        "uniportal_writeback": "no",
        "uniportal_writeback_path": "",
        "uniportal_writeback_time": "",
        "saved_report": "",
    }


def _source_stats(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
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


def _find_source_for_report_path(file_path: str, roots: List[Path]) -> Optional[Path]:
    if not file_path:
        return None
    candidate = Path(file_path)
    if candidate.is_absolute() and candidate.is_file():
        return candidate
    normalized = file_path.replace("\\", "/").lstrip("/")
    for root in roots:
        if not root or not root.is_dir():
            continue
        direct = root / normalized
        if direct.is_file():
            return direct
        name_match = root / candidate.name
        if candidate.name and name_match.is_file():
            return name_match
    if candidate.name:
        for root in roots:
            if not root or not root.is_dir():
                continue
            try:
                for path in root.rglob(candidate.name):
                    if path.is_file() and path.suffix.lower() in CODE_SUFFIXES:
                        return path
            except OSError:
                continue
    return None



def _analysis_source_files(root: Path) -> List[Path]:
    return [path for path in _collect_code_files(root) if path.suffix.lower() in SOURCE_SUFFIXES]


def _report_parsed_files_count(report: DSITReport) -> int:
    seen: set[str] = set()
    for fs in report.files_stats:
        path = (fs.file_path or "").replace("\\", "/").strip()
        if not path:
            continue
        if Path(path).suffix.lower() in SOURCE_SUFFIXES:
            seen.add(path)
    return len(seen) or len(report.files_stats)


def _report_file_key(file_path: str, project_path: str = "") -> str:
    raw = (file_path or "").replace("\\", "/").strip()
    if not raw:
        return ""
    try:
        path = Path(raw)
        if path.is_absolute() and project_path:
            try:
                raw = path.resolve().relative_to(Path(project_path).resolve()).as_posix()
            except ValueError:
                raw = path.name
    except Exception:
        pass
    while raw.startswith("./"):
        raw = raw[2:]
    return raw.lstrip("/")


def _merge_dcab_aggregate_report(request_id: str, current_report: DSITReport) -> DSITReport:
    with _TASK_STORE_LOCK:
        aggregate = _DCAB_AGGREGATE_REPORTS.get(request_id)
        if aggregate is None:
            aggregate = DSITReport(
                report_id=current_report.report_id,
                project_name=current_report.project_name,
                project_path=current_report.project_path,
            )
            _DCAB_AGGREGATE_REPORTS[request_id] = aggregate
        else:
            aggregate.report_id = current_report.report_id
            aggregate.project_name = current_report.project_name
            aggregate.project_path = current_report.project_path

        by_path = {
            _report_file_key(fs.file_path, aggregate.project_path): fs
            for fs in aggregate.files_stats
            if _report_file_key(fs.file_path, aggregate.project_path)
        }
        for fs in current_report.files_stats:
            key = _report_file_key(fs.file_path, current_report.project_path)
            if not key:
                continue
            by_path[key] = copy.deepcopy(fs)
        aggregate.files_stats = [
            by_path[key]
            for key in sorted(by_path)
        ]
        aggregate_copy = copy.deepcopy(aggregate)
        aggregate_files_count = _report_parsed_files_count(aggregate)
        aggregate_bugs_count = aggregate.total_bugs
        aggregate_functions_count = sum(len(fs.functions) for fs in aggregate.files_stats)
        if request_id in _TASK_STORE:
            _TASK_STORE[request_id].update(
                {
                    "aggregate_files_count": aggregate_files_count,
                    "aggregate_bugs_count": aggregate_bugs_count,
                    "aggregate_functions_count": aggregate_functions_count,
                    "parsed_files_count": aggregate_files_count,
                    "updated_at": time.time(),
                }
            )
    return aggregate_copy


def _fill_missing_file_stats(report: DSITReport, roots: List[Path]) -> None:
    clean_roots = []
    seen = set()
    for root in roots:
        if not root:
            continue
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        clean_roots.append(root)
    for fs in report.files_stats:
        if any([fs.total_lines, fs.total_statements, fs.function_count, fs.comment_lines]):
            continue
        source = _find_source_for_report_path(fs.file_path, clean_roots)
        if not source:
            continue
        stats = _source_stats(source)
        fs.total_lines = stats.get("total_lines", fs.total_lines)
        fs.total_statements = stats.get("total_statements", fs.total_statements)
        fs.function_count = stats.get("function_count", fs.function_count)
        fs.comment_lines = stats.get("comment_lines", fs.comment_lines)


def _augment_payload_summary(payload: dict, enabled_rule_count: Optional[int]) -> None:
    summary = payload.get("report", {}).get("summary")
    if not isinstance(summary, dict):
        return
    by_rule = summary.get("by_rule") or {}
    if isinstance(by_rule, dict):
        summary["hit_rule_count"] = len(by_rule)
    if enabled_rule_count is not None:
        summary["enabled_rule_count"] = enabled_rule_count


def _task_payload_extras(task: dict) -> dict:
    keys = [
        "engine",
        "rule_standard",
        "selected_rule_set",
        "selected_rule_count_raw",
        "selected_rule_count",
        "engine_rule_count",
        "filtered_document_rule_count",
        "rule_ids_mode",
        "rule_ids_count",
        "dcab_source_root",
        "dcab_project_path",
        "expected_analysis_files",
        "submitted_analysis_files_count",
        "completed_analysis_files_count",
        "parsed_files_count",
        "dcab_progress_info",
        "raw_result_rule_sets",
        "result_rule_sets",
        "saved_project_id",
        "saved_project_path",
        "saved_project_root",
        "saved_project_meta",
        "saved_project_error",
    ]
    return {key: task[key] for key in keys if task.get(key) not in (None, "")}


def _save_report_payload(payload: dict, project_id: str) -> None:
    try:
        out_dir = REPORTS_DIR / (project_id or payload.get("request_id") or "unknown")
        out_dir.mkdir(parents=True, exist_ok=True)
        saved_report_path = out_dir / "last_report.json"
        payload["saved_report"] = str(saved_report_path)
        saved_report_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        payload["save_report_error"] = str(e)


def _complete_project_report_task(
    request_id: str,
    project_id: str,
    report: DSITReport,
    save_report: bool,
    is_uniportal: bool,
    root: Optional[Path],
    extra_payload: Optional[dict] = None,
    stat_roots: Optional[List[Path]] = None,
    dcab_source_root: Optional[Path] = None,
    existing_project: bool = False,
) -> dict:
    if extra_payload and extra_payload.get("selected_rule_set"):
        report, filter_stats = filter_report_by_rule_set(report, extra_payload["selected_rule_set"])
        extra_payload = dict(extra_payload)
        extra_payload.update(filter_stats)
    _fill_missing_file_stats(report, stat_roots or ([root] if root else []))
    parsed_files_count = _report_parsed_files_count(report)
    payload = _build_report_payload(request_id, project_id, report)
    payload["parsed_files_count"] = parsed_files_count
    if extra_payload:
        payload.update(extra_payload)
    _augment_payload_summary(payload, payload.get("rule_ids_count"))
    if save_report:
        _save_report_payload(payload, project_id)
    if is_uniportal and root and (UNIPORTAL_WRITABLE or bool(MOCK_UNIPORTAL_DIR)):
        try:
            wb_info = _write_back_to_uniportal(
                root,
                project_id,
                payload,
                dcab_source_root=dcab_source_root,
                existing_project=existing_project,
            )
            payload["uniportal_writeback"] = "ok"
            payload["uniportal_writeback_path"] = wb_info["report_path"]
            payload["uniportal_writeback_time"] = wb_info["last_analysis"]
        except OSError as e:
            payload["uniportal_writeback_error"] = str(e)
    if save_report and payload.get("saved_report"):
        try:
            Path(payload["saved_report"]).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            payload["save_report_error"] = str(e)
    _set_task_status(
        request_id,
        "completed",
        payload=payload,
        uniportal_writeback=payload.get("uniportal_writeback"),
        uniportal_writeback_path=payload.get("uniportal_writeback_path"),
        saved_project_id=payload.get("saved_project_id", ""),
        saved_project_path=payload.get("saved_project_path", ""),
        saved_project_root=payload.get("saved_project_root", ""),
        saved_report=payload.get("saved_report", ""),
    )
    with _TASK_STORE_LOCK:
        _DCAB_AGGREGATE_REPORTS.pop(request_id, None)
    return payload


def _run_analysis_background(
    request_id: str,
    workdir: Path,
    target_files: List[Path],
    project_name: str = "",
    timeout: int = CODETIDY_TIMEOUT,
    keep: bool = False,
    save_report: bool = True,
    project_id: str = "",
    is_uniportal: bool = False,
    root: Optional[Path] = None,
    saved_paths: Optional[List[Path]] = None,
    extract_dir: Optional[Path] = None,
    all_code_files: Optional[List[Path]] = None,
    zip_uploads: bool = False,
    save_as_project: bool = False,
    saved_project_id: str = "",
    saved_project_name: str = "",
    requested_project_name: str = "",
    original_filename: str = "",
    cleanup_workdir: bool = True,
    existing_project: bool = False,
) -> None:
    """后台线程执行 codetidy 分析，完成后更新 _TASK_STORE.

    此函数在独立线程中运行，通过 _set_task_status 更新任务状态，
    前端通过 GET /status/{request_id} 轮询获取结果.
    """
    try:
        _set_task_status(request_id, "running")

        # 执行分析
        report = _run_analysis(workdir, target_files, project_name, timeout)
        report.report_id = request_id

        report_dict = report.to_dict()
        payload: dict = {
            "request_id": request_id,
            "project_id": project_id or request_id,
            "report": report_dict,
            "uniportal_writeback": "no",
            "uniportal_writeback_path": "",
            "uniportal_writeback_time": "",
            "saved_report": "",
        }

        # 保存报告到本地
        if save_report:
            try:
                out_dir = REPORTS_DIR / (project_id or request_id)
                out_dir.mkdir(parents=True, exist_ok=True)
                saved_report_path = out_dir / "last_report.json"
                payload["saved_report"] = str(saved_report_path)
                saved_report_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError as e:
                payload["save_report_error"] = str(e)

        if save_as_project and saved_paths:
            try:
                saved = _save_uploaded_project(
                    request_id=request_id,
                    project_id=saved_project_id or project_id or request_id,
                    project_name=saved_project_name or project_name or project_id or request_id,
                    original_filename=original_filename,
                    zip_uploads=zip_uploads,
                    saved_paths=saved_paths,
                    extract_dir=extract_dir,
                    all_code_files=all_code_files,
                    destination_root=LOCAL_PROJECTS_DIR,
                    source="upload",
                    requested_project_name=requested_project_name,
                )
                payload.update(saved)
                saved_report = _write_uploaded_project_back_to_uniportal(
                    Path(saved.get("saved_project_root") or saved["saved_project_path"]),
                    saved["saved_project_id"],
                    payload,
                    None,
                    mark_uniportal=False,
                    source_dir=Path(saved["saved_project_path"]),
                )
                payload["saved_project_report"] = saved_report["report_path"]
            except OSError as e:
                payload["saved_project_error"] = str(e)

        # 共享卷写回 (项目分析)
        if is_uniportal and root and (UNIPORTAL_WRITABLE or bool(MOCK_UNIPORTAL_DIR)):
            try:
                wb_info = _write_back_to_uniportal(
                    root,
                    project_id,
                    payload,
                    existing_project=existing_project,
                )
                payload["uniportal_writeback"] = "ok"
                payload["uniportal_writeback_path"] = wb_info["report_path"]
                payload["uniportal_writeback_time"] = wb_info["last_analysis"]
            except OSError as e:
                payload["uniportal_writeback_error"] = str(e)

        if save_report and payload.get("saved_report"):
            try:
                Path(payload["saved_report"]).write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError as e:
                payload["save_report_error"] = str(e)

        # 清理临时目录
        if cleanup_workdir and not keep:
            shutil.rmtree(workdir, ignore_errors=True)

        _set_task_status(request_id, "completed", payload=payload)

    except HTTPException as e:
        if cleanup_workdir and not keep:
            shutil.rmtree(workdir, ignore_errors=True)
        detail = e.detail if isinstance(e.detail, str) else json.dumps(e.detail, ensure_ascii=False)
        _set_task_status(
            request_id,
            "failed",
            error={"detail": detail, "status_code": e.status_code},
        )
    except Exception as e:
        if cleanup_workdir and not keep:
            shutil.rmtree(workdir, ignore_errors=True)
        _set_task_status(
            request_id,
            "failed",
            error={"detail": str(e), "status_code": 500},
        )


app = FastAPI(title="GJB8114 Code Analysis Service (codetidy)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =====================================================================
# 通用工具
# =====================================================================

def _safe_filename(name: str) -> str:
    """剥离路径分隔符, 防止前端伪造路径写出工作目录."""

    base = os.path.basename(name or "")
    if not base or base in {".", ".."}:
        raise HTTPException(status_code=400, detail=f"非法文件名: {name!r}")
    return base


def _validate_suffix(name: str) -> None:
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {name!r} (允许: {sorted(ALLOWED_SUFFIXES)})",
        )


def _safe_project_id(project_id: str) -> str:
    pid = (project_id or "").strip()
    if not pid or pid in {".", ".."} or "/" in pid or "\\" in pid:
        raise HTTPException(status_code=400, detail=f"非法 project_id: {project_id!r}")
    return pid


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_project_slug(value: str, fallback: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", value or "").strip("_-")
    slug = re.sub(r"_+", "_", slug)[:48]
    return slug or fallback


def _make_upload_project_id(project_name: str, request_id: str) -> str:
    fallback = f"upload_{request_id.rsplit('_', 1)[-1]}"
    base = _safe_project_slug(project_name, fallback)
    suffix = uuid.uuid4().hex[:8]
    return _safe_project_id(f"{base}_{suffix}")


def _unique_project_id(base_project_id: str, destination_root: Path) -> str:
    base = _safe_project_slug(base_project_id, "uploaded_project")
    candidate = _safe_project_id(base)
    if not (destination_root / candidate).exists():
        return candidate
    for index in range(2, 1000):
        candidate = _safe_project_id(f"{base}_{index}")
        if not (destination_root / candidate).exists():
            return candidate
    raise HTTPException(status_code=409, detail=f"项目 {base!r} 已存在，无法生成唯一 project_id")


def _safe_project_dir_name(value: str, fallback: str) -> str:
    """Return a filesystem-safe business directory name without losing Chinese text."""

    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", (value or "").strip())
    name = name.strip(" .")
    return name or fallback


def _unique_uploaded_top_level_dir(extract_dir: Optional[Path]) -> Optional[Path]:
    if extract_dir is None:
        return None
    try:
        entries = [entry for entry in extract_dir.iterdir() if not entry.name.startswith(".")]
    except OSError:
        return None
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return None


def _copy_project_tree(source_root: Path, dest_root: Path) -> None:
    if dest_root.exists():
        raise OSError(f"project path already exists: {dest_root}")
    dest_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source_root,
        dest_root,
        ignore=shutil.ignore_patterns("ct8114", "_ct8114", "__pycache__", ".git", ".idea", ".vscode"),
    )


def _save_uploaded_project(
    *,
    request_id: str,
    project_id: str,
    project_name: str,
    original_filename: str,
    zip_uploads: bool,
    saved_paths: List[Path],
    extract_dir: Optional[Path],
    all_code_files: Optional[List[Path]],
    destination_root: Path,
    source: str,
    requested_project_name: str = "",
) -> dict:
    item_root = (destination_root / project_id).resolve()
    local_root = destination_root.resolve()
    try:
        item_root.relative_to(local_root)
    except ValueError as exc:
        raise OSError("local project path escaped workspace") from exc

    unique_top_dir = _unique_uploaded_top_level_dir(extract_dir) if zip_uploads else None
    actual_name_source = (
        requested_project_name
        or (unique_top_dir.name if unique_top_dir is not None else "")
        or Path(original_filename).stem
        or project_id
    )
    actual_name = _safe_project_dir_name(actual_name_source, project_id)
    project_dir = (item_root / actual_name).resolve()
    if not _path_is_relative_to(project_dir, item_root):
        raise OSError("actual project path escaped saved project root")

    if zip_uploads:
        if not extract_dir or not all_code_files:
            raise OSError("zip upload project source is unavailable")
        # Preserve the complete uploaded project. A unique wrapper directory is
        # the business project directory; otherwise the zip root is flat content.
        source_root = unique_top_dir or extract_dir
        _copy_project_tree(source_root, project_dir)
    else:
        project_dir.mkdir(parents=True, exist_ok=False)
        for src in saved_paths:
            if src.is_file():
                shutil.copy2(src, project_dir / src.name)

    code_count = _count_code_files(project_dir)
    meta = {
        "project_id": project_id,
        "project_name": actual_name,
        "source": source,
        "created_at": datetime.now().isoformat(),
        "original_filename": original_filename,
        "file_count": code_count,
        "saved_from_request_id": request_id,
    }
    ct8114_dir = item_root / "ct8114"
    ct8114_dir.mkdir(parents=True, exist_ok=True)
    (ct8114_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "saved_project_id": project_id,
        "saved_project_path": str(project_dir),
        "saved_project_root": str(item_root),
        "saved_project_meta": meta,
    }


def _rule_ids_observability(rule_set: Optional[str] = None) -> dict:
    if rule_set is not None:
        selected_rule_set = normalize_rule_set(rule_set)
        info = load_rule_set_rule_ids(selected_rule_set)
        return {
            "rule_standard": selected_rule_set,
            "selected_rule_set": selected_rule_set,
            "selected_rule_count_raw": info["raw_count"],
            "selected_rule_count": info["selected_count"],
            "engine_rule_count": len(info["rule_ids"]),
            "filtered_document_rule_count": info["filtered_document_count"],
            "rule_ids_mode": selected_rule_set,
            "rule_ids_count": len(info["rule_ids"]),
            "engine_rule_ids": info["rule_ids"],
            "rule_ids_loaded_from": info.get("loaded_from", ""),
        }
    raw = os.environ.get("DCAB_RULE_IDS", "").strip()
    mode = raw or "ALL"
    rule_ids = configured_rule_ids()
    return {
        "rule_standard": "GJB8114",
        "selected_rule_set": "GJB-8114",
        "selected_rule_count_raw": len(rule_ids),
        "selected_rule_count": len(rule_ids),
        "engine_rule_count": len(rule_ids),
        "filtered_document_rule_count": 0,
        "rule_ids_mode": mode,
        "rule_ids_count": len(rule_ids),
        "engine_rule_ids": rule_ids,
    }


def _public_rule_info(rule_info: dict) -> dict:
    return {
        key: value
        for key, value in rule_info.items()
        if key not in {"engine_rule_ids"}
    }


def _selected_rule_set_from_request(body: Optional[dict], query_rule_set: Optional[str]) -> str:
    body_rule_set = ""
    if isinstance(body, dict):
        body_rule_set = str(body.get("rule_set") or "").strip()
    query_text = query_rule_set.strip() if isinstance(query_rule_set, str) else ""
    return normalize_rule_set(body_rule_set or query_text or "GJB-8114")


def _collect_code_files(root: Path) -> List[Path]:
    """递归收集 .c/.h/.cc/.cpp/.cxx/.hpp/.hxx 文件, 跳过本工具内部目录."""

    result: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in _TOOL_INTERNAL_DIRS and not d.startswith(".")
        ]
        for fn in filenames:
            if Path(fn).suffix.lower() in CODE_SUFFIXES:
                result.append(Path(dirpath) / fn)
    return result


def _find_project_root(extract_dir: Path, code_files: List[Path]) -> Path:
    """Pick the source subtree DCAB should see as the project root.

    UniPortal items and zip uploads often wrap the real code in one or more
    business-named directories, for example ``MEMS陀螺软件/代码/*.c``. DCAB is
    sensitive to non-ASCII path segments, so prefer the deepest source-like
    directory that still covers the actual source set.
    """

    root = extract_dir.resolve()
    resolved_files: List[Path] = []
    for code_file in code_files:
        try:
            resolved = code_file.resolve()
            resolved.relative_to(root)
        except ValueError:
            continue
        resolved_files.append(resolved)

    if not resolved_files:
        return extract_dir

    preferred_names = {"代码", "code", "src", "source", "sources"}
    avoided_names = {
        "requirements",
        "unit-test-generate",
        "document-validator",
        "traceability_link_recovery",
        "traceability-link-recovery",
        "test",
        "tests",
        "__pycache__",
    }

    def path_parts_lower(path: Path) -> set[str]:
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            parts = path.parts
        return {part.lower() for part in parts}

    candidates: Dict[Path, dict] = {}
    for code_file in resolved_files:
        parent = code_file.parent
        for candidate in [parent, *parent.parents]:
            try:
                candidate.relative_to(root)
            except ValueError:
                break
            if not candidate.is_dir():
                continue
            if path_parts_lower(candidate) & avoided_names:
                continue
            stats = candidates.setdefault(candidate, {"code": 0, "source": 0})
            stats["code"] += 1
            if code_file.suffix.lower() in SOURCE_SUFFIXES:
                stats["source"] += 1
            if candidate == root:
                break

    if not candidates:
        return extract_dir

    def score(item: tuple[Path, dict]) -> tuple[int, int, int, int, int, str]:
        candidate, stats = item
        name = candidate.name.lower()
        depth = len(candidate.relative_to(root).parts) if candidate != root else 0
        preferred = 1 if name in preferred_names or candidate.name in preferred_names else 0
        root_penalty = 1 if candidate == root else 0
        try:
            child_dirs = sum(1 for child in candidate.iterdir() if child.is_dir())
        except OSError:
            child_dirs = 0
        return (
            int(stats["source"]),
            int(stats["code"]),
            preferred,
            depth,
            -root_penalty,
            -child_dirs,
            str(candidate),
        )

    best = max(candidates.items(), key=score)[0]
    try:
        if best != root and _collect_code_files(best):
            return best
    except OSError:
        pass
    return extract_dir


_SOURCE_ROOT_DIR_NAMES = {'代码', 'code', 'src', 'source', 'sources'}
_NON_BUSINESS_DIR_NAMES = {
    'ct8114',
    '_ct8114',
    'requirements',
    'unit-test-generate',
    'document-validator',
    'traceability_link_recovery',
}


def _find_actual_project_dir_without_source(root: Path) -> Path:
    resolved_root = root.resolve()
    try:
        candidates = []
        for entry in sorted(root.iterdir()):
            if (
                not entry.is_dir()
                or entry.name.startswith('.')
                or entry.name.lower() in _NON_BUSINESS_DIR_NAMES
            ):
                continue
            candidate = entry.resolve()
            if not _path_is_relative_to(candidate, resolved_root):
                logger.warning(
                    'Skipping project directory outside item root: %s -> %s',
                    entry,
                    candidate,
                )
                continue
            candidates.append(candidate)
    except OSError as exc:
        logger.warning('Unable to inspect item root %s: %s', root, exc)
        return resolved_root

    if len(candidates) == 1:
        only = candidates[0]
        return resolved_root if only.name.lower() in _SOURCE_ROOT_DIR_NAMES else only

    source_candidates = [
        candidate for candidate in candidates if _collect_code_files(candidate)
    ]
    if len(source_candidates) == 1:
        only = source_candidates[0]
        return resolved_root if only.name.lower() in _SOURCE_ROOT_DIR_NAMES else only

    logger.warning('Ambiguous project directories under %s; using item root', root)
    return resolved_root


def _find_actual_project_dir(
    item_root: Path,
    dcab_source_root: Optional[Path] = None,
) -> Path:
    root = item_root.resolve()
    if dcab_source_root is None:
        return _find_actual_project_dir_without_source(root)
    source_root = dcab_source_root.resolve()
    if not _path_is_relative_to(source_root, root):
        logger.warning('DCAB source root is outside item root: %s', source_root)
        return _find_actual_project_dir_without_source(root)
    if source_root == root:
        return root
    if source_root.name.lower() in _SOURCE_ROOT_DIR_NAMES:
        return source_root.parent
    return source_root


def _ct8114_output_dir(
    project_root: Path,
) -> Path:
    """Return the new ct8114 output directory at the project root."""

    return project_root.resolve() / 'ct8114'


def _legacy_ct8114_output_dir(
    project_root: Path,
    dcab_source_root: Optional[Path] = None,
) -> Path:
    """Return the old actual-project ct8114 directory for read-only fallback."""

    return _find_actual_project_dir(project_root, dcab_source_root) / 'ct8114'


def _safe_extract_zip(zip_path: Path, extract_dir: Path) -> None:
    """Safely extract a zip file while preventing zip-slip traversal."""

    extract_root = extract_dir.resolve()
    total_uncompressed = 0
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                if not name or name.endswith("/"):
                    continue
                target = (extract_root / name).resolve()
                try:
                    target.relative_to(extract_root)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail="zip 文件包含非法路径，已拒绝解压") from exc

                total_uncompressed += info.file_size
                if total_uncompressed > MAX_ZIP_EXTRACT_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"zip 解压后文件总大小超过限制 ({MAX_ZIP_EXTRACT_BYTES} bytes)",
                    )

                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info, "r") as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="无效的 zip 文件") from exc


def _count_code_files(root: Path) -> int:
    return len(_collect_code_files(root))



def _cleanup_dcab_runtime_dirs(runtime_project_dir: Optional[Path] = None) -> None:
    """Remove stale DCAB runtime outputs without touching source/report volumes."""

    root = (runtime_project_dir or Path(os.environ.get("DCAB_RUNTIME_PROJECT_DIR", "/opt/dcab/project"))).resolve()
    if str(root) in {"/", "", "."}:
        logger.warning("Skip unsafe DCAB runtime cleanup target: %s", root)
        return
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Unable to create DCAB runtime directory %s: %s", root, exc)
        return

    try:
        children = list(root.iterdir())
    except OSError as exc:
        logger.warning("Unable to inspect DCAB runtime directory %s: %s", root, exc)
        return

    for child in children:
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        except OSError as exc:
            logger.warning("Unable to remove stale DCAB runtime item %s: %s", child, exc)

    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Unable to ensure DCAB runtime directory %s: %s", root, exc)

def _prepare_dcab_safe_project_dir(source_root: Path, request_id: str) -> Path:
    safe_root = DCAB_SAFE_ROOT.resolve()
    task_dir = (safe_root / request_id).resolve()
    try:
        task_dir.relative_to(safe_root)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="DCAB safe workdir escaped safe root") from exc

    project_dir = task_dir / "project"
    if task_dir.exists():
        shutil.rmtree(task_dir, ignore_errors=True)
    task_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(source_root, project_dir)
    except OSError as exc:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"准备 DCAB 安全路径失败: {exc}") from exc
    return project_dir


# =====================================================================
# 双源解析: UniPortal 共享卷 + 本地私有卷
# =====================================================================

def _build_item_index() -> Dict[str, Path]:
    """遍历 UNIPORTAL_STORAGE_PATH/{portal_proj}/{item_id}/, 返回 {item_id: 绝对路径}.

    item_id 即子工具用作 project_id 的 UUID. 共享卷为空或环境变量未设置时返回 {}.

    支持两种模式:
      - 真实 UniPortal: UNIPORTAL_STORAGE_PATH=/data/uniportal
      - 本地模拟:      MOCK_UNIPORTAL_DIR=mock_uniportal/
        (模拟共享卷结构: mock_uniportal/{portal_proj_id}/{item_id}/)
    """

    index: Dict[str, Path] = {}

    # 优先检查模拟共享卷 (本地开发/测试)
    if MOCK_UNIPORTAL_DIR:
        mock_root = Path(MOCK_UNIPORTAL_DIR).resolve()
        if mock_root.is_dir():
            for portal_proj in mock_root.iterdir():
                if not portal_proj.is_dir() or portal_proj.name.startswith("."):
                    continue
                for item in portal_proj.iterdir():
                    if item.is_dir() and not item.name.startswith((".", "_")):
                        index[item.name] = item
            return index

    if not UNIPORTAL_STORAGE_PATH:
        return index
    root = Path(UNIPORTAL_STORAGE_PATH)
    if not root.is_dir():
        return index
    for portal_proj in root.iterdir():
        if not portal_proj.is_dir() or portal_proj.name.startswith("."):
            continue
        for item in portal_proj.iterdir():
            if item.is_dir() and not item.name.startswith((".", "_")):
                index[item.name] = item
    return index


def _resolve_project_path(project_id: str) -> Path:
    """优先共享卷, 再查私有卷. 找不到统一抛 404.

    优先级反过来是为了避免私有卷里的"空壳目录"遮挡: 上次分析时
    把报告写到 LOCAL_WORKSPACES_DIR/{item_id}/_ct8114/, 会留下一个
    没有源码的同名空壳; 先查私有就会拿到这个空壳, 导致 "没有可分析的源文件".
    UniPortal item_id 是纯 UUID, 跟未来私有上传的命名 (proj_xxxx) 不冲突.

    支持模拟共享卷 (MOCK_UNIPORTAL_DIR) 用于本地开发测试.
    """

    pid = _safe_project_id(project_id)
    # 检查 UniPortal 共享卷（含模拟）
    uniportal_active = UNIPORTAL_MODE or bool(MOCK_UNIPORTAL_DIR)
    if uniportal_active:
        item = _build_item_index().get(pid)
        if item and item.is_dir():
            return item
    for local in (LOCAL_PROJECTS_DIR / pid, LOCAL_WORKSPACES_DIR / pid):
        if local.is_dir():
            return local
    raise HTTPException(status_code=404, detail=f"项目 {pid!r} 未找到")




def _looks_like_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (TypeError, ValueError):
        return False


def _is_uniportal_path(path: Path) -> bool:
    roots = []
    if UNIPORTAL_STORAGE_PATH:
        roots.append(Path(UNIPORTAL_STORAGE_PATH))
    if MOCK_UNIPORTAL_DIR:
        roots.append(Path(MOCK_UNIPORTAL_DIR))
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path
    for root in roots:
        try:
            if _path_is_relative_to(resolved, root.resolve()):
                return True
        except Exception:
            continue
    return False

def _read_json_dict(path: Path) -> dict:
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}
    return {}


def _resolve_report_project_name(
    project_root: Path,
    actual_project_dir: Path,
    project_id: str,
    report: dict,
    *metas: dict,
) -> str:
    for meta in metas:
        name = meta.get("project_name") if isinstance(meta, dict) else None
        if isinstance(name, str) and name.strip() and not _looks_like_uuid(name.strip()):
            return name.strip()
    report_name = report.get("project_name") if isinstance(report, dict) else None
    if isinstance(report_name, str) and report_name.strip() and not _looks_like_uuid(report_name.strip()):
        return report_name.strip()
    actual_name = actual_project_dir.name if actual_project_dir else ""
    if actual_name and not _looks_like_uuid(actual_name):
        return actual_name
    root_name = project_root.name if project_root else ""
    if root_name and not _looks_like_uuid(root_name):
        return root_name
    return project_id


def _build_uniportal_meta(
    project_root: Path,
    actual_project_dir: Path,
    project_id: str,
    payload: dict,
    previous_meta: dict,
    dcab_source_root: Optional[Path],
    now: str,
) -> dict:
    report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    project_name = _resolve_report_project_name(
        project_root,
        actual_project_dir,
        project_id,
        report,
        previous_meta,
    )
    meta = {
        "project_id": project_id,
        "project_name": project_name,
        "actual_project_dir": str(actual_project_dir),
        "tool_name": "ct8114",
        "engine": payload.get("engine") or ANALYSIS_ENGINE,
        "rule_standard": payload.get("rule_standard") or "GJB8114",
        "last_analysis": now,
        "last_report": "ct8114/last_report.json",
    }
    for key in (
        "rule_ids_count",
        "selected_rule_set",
        "selected_rule_count_raw",
        "selected_rule_count",
        "engine_rule_count",
        "filtered_document_rule_count",
    ):
        if payload.get(key) is not None:
            meta[key] = payload.get(key)
    for key in ("created_at", "original_filename"):
        if previous_meta.get(key) not in (None, ""):
            meta[key] = previous_meta[key]
    for key in ("total_bugs", "total_files"):
        value = summary.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            meta[key] = value
    for key in (
        "dcab_source_root",
        "dcab_project_path",
        "expected_analysis_files",
        "submitted_analysis_files_count",
        "completed_analysis_files_count",
        "parsed_files_count",
        "dcab_progress_info",
        "raw_result_rule_sets",
        "result_rule_sets",
    ):
        value = payload.get(key)
        if value not in (None, ""):
            meta[key] = value
    if dcab_source_root is not None:
        try:
            source_root = dcab_source_root.resolve()
            if _path_is_relative_to(source_root, project_root.resolve()):
                meta["dcab_source_root"] = str(source_root)
        except Exception:
            pass
    return meta


_ALLOWED_RULE_SET_FILENAMES = ("GJB-8114", "GJB-5369", "CWE-C", "MISRA-2008", "MISRA-2012")


def _safe_report_rule_set(value: Optional[str]) -> str:
    try:
        rule_set = normalize_rule_set(value or "GJB-8114")
    except DcabClientError as exc:
        raise HTTPException(status_code=400, detail=f"unsupported rule_set: {value!r}") from exc
    if rule_set not in _ALLOWED_RULE_SET_FILENAMES:
        raise HTTPException(status_code=400, detail=f"unsupported rule_set: {value!r}")
    return rule_set


def _selected_rule_set_for_payload(payload: dict) -> str:
    raw = payload.get("selected_rule_set") or payload.get("rule_standard") or "GJB-8114"
    return _safe_report_rule_set(str(raw))


def _flat_rule_set_report_paths(output_dir: Path, rule_set: str) -> tuple[Path, Path]:
    safe_rule_set = _safe_report_rule_set(rule_set)
    return (
        output_dir / f"last_report_{safe_rule_set}.json",
        output_dir / f"meta_{safe_rule_set}.json",
    )


def _resolve_project_path_for_reports(project_id: str, portal_project_id: str = "") -> Path:
    pid = _safe_project_id(project_id)
    portal_pid = (portal_project_id or "").strip()
    if portal_pid:
        root = _uniportal_project_roots(portal_pid).get(pid)
        if root is None:
            raise HTTPException(
                status_code=404,
                detail=f"项目 {pid!r} 在 UniPortal 项目 {portal_pid!r} 下不存在",
            )
        return root
    return _resolve_project_path(pid)


def _rule_set_report_summary(output_dir: Path, rule_set: str) -> dict:
    report_path, meta_path = _flat_rule_set_report_paths(output_dir, rule_set)
    if not report_path.is_file():
        return {"exists": False}
    meta = _read_json_dict(meta_path)
    report_payload = _read_json_dict(report_path)
    report = report_payload.get("report") if isinstance(report_payload.get("report"), dict) else {}
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    return {
        "exists": True,
        "report_path": f"ct8114/{report_path.name}",
        "meta_path": f"ct8114/{meta_path.name}",
        "selected_rule_set": meta.get("selected_rule_set") or report_payload.get("selected_rule_set") or rule_set,
        "last_analysis": meta.get("last_analysis"),
        "total_bugs": meta.get("total_bugs", summary.get("total_bugs")),
        "total_files": meta.get("total_files", summary.get("total_files")),
        "engine_rule_count": meta.get("engine_rule_count", report_payload.get("engine_rule_count")),
    }


def _write_back_to_uniportal(
    item_root: Path,
    project_id: str,
    payload: dict,
    source_dir: Optional[Path] = None,
    dcab_source_root: Optional[Path] = None,
    existing_project: bool = False,
) -> dict:
    project_root = item_root.resolve()
    actual_project_dir = (source_dir.resolve() if source_dir is not None else _find_actual_project_dir(project_root, dcab_source_root))
    output_dir = _ct8114_output_dir(project_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "last_report.json"
    meta_path = output_dir / "meta.json"
    now = datetime.now().isoformat()

    previous_meta = {}
    previous_meta.update(_read_json_dict(project_root / "meta.json"))
    previous_meta.update(_read_json_dict(_legacy_ct8114_output_dir(project_root, dcab_source_root) / "meta.json"))
    previous_meta.update(_read_json_dict(meta_path))

    report = payload.get("report") if isinstance(payload.get("report"), dict) else None
    if report is not None:
        report["project_name"] = _resolve_report_project_name(
            project_root,
            actual_project_dir,
            project_id,
            report,
            previous_meta,
        )

    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    meta = _build_uniportal_meta(
        project_root,
        actual_project_dir,
        project_id,
        payload,
        previous_meta,
        dcab_source_root,
        now,
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    selected_rule_set = _selected_rule_set_for_payload(payload)
    flat_report_path, flat_meta_path = _flat_rule_set_report_paths(output_dir, selected_rule_set)
    flat_report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    flat_meta = dict(meta)
    flat_meta["current_rule_set_report"] = f"ct8114/{flat_report_path.name}"
    flat_meta_path.write_text(json.dumps(flat_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "report_path": str(report_path),
        "meta_path": str(meta_path),
        "rule_set_report_path": str(flat_report_path),
        "rule_set_meta_path": str(flat_meta_path),
        "last_analysis": now,
    }

def _find_project_meta_file(project_dir: Path) -> Optional[Path]:
    new_meta = _ct8114_output_dir(project_dir) / 'meta.json'
    if new_meta.exists():
        return new_meta
    legacy_actual_meta = _legacy_ct8114_output_dir(project_dir) / 'meta.json'
    if legacy_actual_meta.exists():
        return legacy_actual_meta
    legacy_meta = project_dir / 'meta.json'
    if legacy_meta.exists():
        return legacy_meta
    return None


def _display_name_from_meta(project_dir: Path) -> Optional[str]:
    meta = _find_project_meta_file(project_dir)
    if meta is None:
        return None
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except Exception:
        return None
    name = data.get("project_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    original = data.get("original_filename")
    if isinstance(original, str) and original.strip():
        stem = Path(original.strip()).stem
        if stem:
            return stem
    return None


def _project_display_name(item_path: Path, fallback: str) -> str:
    """共享卷项目的展示名: 取 item_id 下第一个非隐藏子目录名 (即 zip 解压出的文件夹名)."""

    try:
        for entry in sorted(item_path.iterdir()):
            if entry.is_dir() and not entry.name.startswith((".", "_")):
                return entry.name
    except Exception:
        pass
    return fallback


def _local_project_display_name(project_dir: Path) -> str:
    """私有卷项目的展示名: 优先读 meta.json, 否则用目录名."""

    meta = project_dir / "meta.json"
    if meta.exists():
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            name = data.get("project_name")
            if isinstance(name, str) and name:
                return name
        except Exception:
            pass
    return project_dir.name


def _find_last_report_file(project_path: Path, project_id: str) -> Optional[Path]:
    """按优先级查找历史报告文件.

    优先级:
      1. {project_path}/ct8114/last_report.json (新写回路径)
      2. {actual_project_dir}/ct8114/last_report.json (旧写回路径)
      3. {project_path}/_ct8114/last_report.json (旧写回路径)
      4. REPORTS_DIR/{project_id}/last_report.json (本地报告)
    """
    new_report = _ct8114_output_dir(project_path) / 'last_report.json'
    if new_report.exists():
        return new_report
    legacy_actual_report = _legacy_ct8114_output_dir(project_path) / 'last_report.json'
    if legacy_actual_report.exists():
        return legacy_actual_report
    legacy_report = project_path / '_ct8114' / 'last_report.json'
    if legacy_report.exists():
        return legacy_report
    local_report = REPORTS_DIR / project_id / 'last_report.json'
    if local_report.exists():
        return local_report
    return None

def _check_analysis_status(project_path: Path, project_id: str = "") -> dict:
    """检查项目是否已被 ct8114 分析过，返回分析状态信息.

    返回字段:
        analyzed: bool — 是否有分析报告
        last_analysis: str | None — 最近分析时间 (ISO 格式)
        report_bugs: int | None — 最近分析的问题总数
    """
    report_file = _find_last_report_file(project_path, project_id or project_path.name)
    if report_file is None:
        return {"analyzed": False, "last_analysis": None, "report_bugs": None}

    try:
        data = json.loads(report_file.read_text(encoding="utf-8"))
        summary = data.get("report", {}).get("summary", {})
        # last_analysis 优先从 meta.json 获取
        meta_file = _find_project_meta_file(project_path)
        last_analysis = None
        if meta_file is not None:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            last_analysis = meta.get('last_analysis') or meta.get('ct8114_last_analysis')
        if not last_analysis:
            last_analysis = data.get("uniportal_writeback_time") or None
        return {
            "analyzed": True,
            "last_analysis": last_analysis,
            "report_bugs": summary.get("total_bugs"),
        }
    except Exception:
        return {"analyzed": True, "last_analysis": None, "report_bugs": None}


# =====================================================================
# 即时上传分析
# =====================================================================

def _mock_analysis(target_files: List[Path], project_name: str) -> DSITReport:
    """模拟分析：生成伪造的 DSIT 报告，用于本地测试流程。

    报告包含针对每个源文件的模拟诊断结果，便于验证前端展示和共享卷写回。
    """
    import random
    from datetime import datetime

    # 模拟针对每个文件生成 1~3 条诊断
    bugs: list = []
    mock_rules = [
        ("GJB-R-1-8-2", "禁止使用 goto 语句", "Warning", "0", "naming"),
        ("GJB-R-1-3-8", "分支语句必须使用大括号", "Error", "1", "logic"),
        ("GJB-R-1-7-3", "禁止使用魔数，应定义为常量", "Warning", "0", "style"),
        ("GJB-R-1-5-1", "函数圈复杂度不应超过 10", "Warning", "0", "style"),
        ("GJB-R-1-7-7", "字符串操作应使用安全函数", "Error", "1", "security"),
    ]

    for f in target_files:
        fname = f.name
        num_bugs = random.randint(1, 3)
        for i in range(num_bugs):
            rule = random.choice(mock_rules)
            bugs.append(DSITBug(
                checker=f"mock-checker-{rule[0]}",
                file_path=fname,
                line=random.randint(3, 80),
                column=random.randint(1, 40),
                message=f"[MOCK] {rule[1]}",
                rule_id=rule[0],
                force=rule[2],
                type_code=rule[3],
                status="open",
            ))

    # 文件统计
    file_stats: list = []
    for f in target_files:
        lines = random.randint(20, 200)
        fbugs = [b for b in bugs if b.file_path == f.name]
        file_stats.append(DSITFileStats(
            file_path=str(f),
            total_lines=lines,
            total_statements=random.randint(5, lines // 2),
            total_declares=random.randint(1, 10),
            function_count=random.randint(1, 8),
            function_max_lines=random.randint(5, 50),
            function_max_depth=random.randint(1, 6),
            comment_lines=random.randint(5, 30),
            code_size=lines * 40,
            bugs=fbugs,
        ))

    total_bugs = len(bugs)
    by_level = {"Error": 0, "Warning": 0}
    for b in bugs:
        by_level[b.level] = by_level.get(b.level, 0) + 1

    return DSITReport(
        report_id=f"mock_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        project_name=project_name or "Mock Project",
        project_path=str(target_files[0].parent) if target_files else "",
        files_stats=file_stats,
    )


def _run_analysis(
    workdir: Path,
    target_files: List[Path],
    project_name: str = "",
    timeout: int = CODETIDY_TIMEOUT,
) -> DSITReport:
    """调用 codetidy.exe 分析源文件，返回 DSITReport。

    这是统一的内部分析入口，供 /analyze 和 /projects/{id}/analyze 共用。

    当 MOCK_ANALYSIS=true 时，跳过 codetidy 调用，返回模拟分析数据，
    用于本地开发测试共享卷读写等流程。
    """
    # 模拟分析模式（本地测试，无需 codetidy.exe）
    if MOCK_ANALYSIS:
        return _mock_analysis(target_files, project_name)

    try:
        return analyze_with_codetidy(
            source_files=target_files,
            project_name=project_name,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "message": CODETIDY_NOT_FOUND_MESSAGE,
                "checked_paths": get_codetidy_search_paths(),
                "error": str(exc),
            },
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(
            status_code=504,
            detail=f"codetidy 执行超时 ({exc.timeout}s)",
        ) from exc


# 挂载静态站点 (HTML/CSS/JS), 与后端 API 共享同源, 避免 CORS 与额外部署
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

# 注册 DeepSITRServer 集成路由
app.include_router(dsit_router)


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    """根路径重定向到静态首页 (static/index.html)."""

    return RedirectResponse(url="/static/index.html")


@app.post("/analyze")
async def analyze(
    files: List[UploadFile] = File(..., description="待分析的 C/C++ 源文件"),
    keep: bool = Query(False, description="调试用: 保留服务端临时目录"),
    entry: Optional[str] = Query(
        None,
        description="指定主分析入口文件名 (默认: 上传的所有 .c/.cc/.cpp/.cxx)",
    ),
    engine: Optional[str] = Query(None, description="显式兼容模式: engine=codetidy"),
    compatibility_mode: bool = Form(False, description="显式使用旧 codetidy 兼容路径"),
    save_as_project: bool = Form(False, description="分析完成后保存到本工具本地项目库"),
    project_name: Optional[str] = Form(None, description="保存到项目库时使用的项目名称"),
    portal_project_id: Optional[str] = Form(None, description="UniPortal 当前工程 ID"),
) -> JSONResponse:
    """上传源文件，使用 codetidy.exe 实时分析，返回 DSIT 格式报告."""
    if not files:
        raise HTTPException(status_code=400, detail="未收到任何文件")

    zip_uploads = [
        uf for uf in files
        if Path(uf.filename or "").suffix.lower() == ".zip"
    ]
    if zip_uploads and len(files) != 1:
        raise HTTPException(status_code=400, detail="工程 zip 上传时请只选择一个 zip 文件")

    requested_engine = (engine or "").strip().lower()
    if requested_engine and requested_engine not in {"dcab_http", "codetidy"}:
        raise HTTPException(status_code=400, detail=f"unsupported engine: {engine!r}")
    use_codetidy = compatibility_mode or requested_engine == "codetidy"
    request_id = f"codetidy_{uuid.uuid4().hex[:12]}" if use_codetidy else f"upload_{uuid.uuid4().hex[:12]}"
    base_tmp = Path(tempfile.gettempdir()) / "ct8114"
    base_tmp.mkdir(parents=True, exist_ok=True)
    workdir = base_tmp / request_id
    workdir.mkdir(parents=True, exist_ok=False)

    saved_paths: List[Path] = []
    extract_dir: Optional[Path] = None
    all_code_files: List[Path] = []
    project_root = workdir
    target_files: List[Path] = []
    total_bytes = 0
    try:
        # 1. 落盘上传文件
        for uf in files:
            name = _safe_filename(uf.filename or "")
            suffix = Path(name).suffix.lower()
            if suffix != ".zip":
                _validate_suffix(name)
            dest = workdir / name
            content = await uf.read()
            total_bytes += len(content)
            max_upload_bytes = MAX_ZIP_BYTES if suffix == ".zip" else MAX_TOTAL_BYTES
            if total_bytes > max_upload_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"上传文件总大小超过限制 ({MAX_TOTAL_BYTES} bytes)",
                )
            dest.write_bytes(content)
            saved_paths.append(dest)

        # 2. 确定分析目标
        if zip_uploads:
            zip_path = saved_paths[0]
            extract_dir = workdir / "_zip_project"
            extract_dir.mkdir(parents=True, exist_ok=True)
            _safe_extract_zip(zip_path, extract_dir)

            all_code_files = _collect_code_files(extract_dir)
            if not all_code_files:
                raise HTTPException(
                    status_code=400,
                    detail="zip 中未找到可分析的源码文件（支持 .c/.h/.cc/.cpp/.cxx/.hpp/.hxx）",
                )

            project_root = _find_project_root(extract_dir, all_code_files)
            if entry is not None:
                rel_entry = entry.strip().lstrip("/\\")
                if not rel_entry:
                    raise HTTPException(status_code=400, detail="entry 不能为空")
                if ".." in Path(rel_entry).parts:
                    raise HTTPException(status_code=400, detail=f"非法 entry: {entry!r}")
                entry_path = (project_root / rel_entry).resolve()
                try:
                    entry_path.relative_to(project_root.resolve())
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"entry 越界: {entry!r}")
                if not entry_path.exists():
                    raise HTTPException(status_code=400, detail=f"未找到入口文件 {entry!r}")
                if entry_path.suffix.lower() not in SOURCE_SUFFIXES:
                    raise HTTPException(status_code=400, detail=f"入口文件不是可分析源文件: {entry!r}")
                target_files = [entry_path]
            else:
                target_files = [
                    p for p in all_code_files
                    if p.suffix.lower() in SOURCE_SUFFIXES
                ]
                if not target_files:
                    raise HTTPException(
                        status_code=400,
                        detail="zip 中未找到 .c/.cc/.cpp/.cxx 源文件；仅头文件不能作为分析入口",
                    )
        elif entry is not None:
            entry_name = _safe_filename(entry)
            target_files = [workdir / entry_name]
            if not target_files[0].exists():
                raise HTTPException(
                    status_code=400,
                    detail=f"指定的 entry 文件 {entry_name!r} 未在上传列表中",
                )
        else:
            target_files = [
                p for p in saved_paths if p.suffix.lower() in SOURCE_SUFFIXES
            ]
            if not target_files:
                raise HTTPException(
                    status_code=400,
                    detail="上传的文件中没有可分析的源文件 (.c/.cc/.cpp/.cxx)",
                )

        # 3. 将文件落盘与验证完成后，启动后台线程执行 codetidy 分析
        #    前端通过 GET /status/{request_id} 轮询获取结果

        original_filename = saved_paths[0].name if saved_paths else ""
        default_project_name = (
            Path(original_filename).stem
            if zip_uploads and original_filename
            else f"upload_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        final_project_name = (project_name or "").strip() or default_project_name

        if not use_codetidy:
            rule_info = _rule_ids_observability()
            public_rule_info = _public_rule_info(rule_info)
            analysis_root = project_root
            is_uniportal = False
            saved_info: dict = {}
            save_project_error = ""
            if save_as_project and portal_project_id:
                portal_pid = _safe_project_id(portal_project_id)
                portal_base = Path(UNIPORTAL_STORAGE_PATH) if UNIPORTAL_STORAGE_PATH else None
                if portal_base is None and MOCK_UNIPORTAL_DIR:
                    portal_base = Path(MOCK_UNIPORTAL_DIR)
                if portal_base is None:
                    save_project_error = "portal_project_id provided but UniPortal storage is not configured"
                else:
                    saved_project_id = _make_upload_project_id(final_project_name, request_id)
                    saved_info = _save_uploaded_project(
                        request_id=request_id,
                        project_id=saved_project_id,
                        project_name=final_project_name,
                        original_filename=original_filename,
                        zip_uploads=bool(zip_uploads),
                        saved_paths=saved_paths,
                        extract_dir=extract_dir,
                        all_code_files=all_code_files if zip_uploads else None,
                        destination_root=portal_base / portal_pid,
                        source="uniportal",
                        requested_project_name=(project_name or "").strip(),
                    )
                    analysis_root = Path(saved_info.get("saved_project_root") or saved_info["saved_project_path"])
                    is_uniportal = True
            elif save_as_project:
                save_project_error = "portal_project_id is required to save uploaded code into the UniPortal project library"

            report_project_id = saved_info.get("saved_project_id", request_id)
            code_files = _collect_code_files(analysis_root)
            if not code_files:
                raise HTTPException(status_code=400, detail="椤圭洰鍐呮病鏈夊彲鍒嗘瀽鐨勬簮鏂囦欢")

            if not _DCAB_ANALYSIS_LOCK.acquire(blocking=False):
                raise HTTPException(
                    status_code=409,
                    detail="DCAB 姝ｅ湪鎵ц鍙︿竴涓垎鏋愪换鍔★紝璇风◢鍚庨噸璇?",
                )
            try:
                dcab_source_root = _find_project_root(analysis_root, code_files)
                expected_analysis_files = len(_analysis_source_files(dcab_source_root))
                dcab_project_path = _prepare_dcab_safe_project_dir(dcab_source_root, request_id)
                _cleanup_dcab_runtime_dirs()
                started = start_progress(str(dcab_project_path), rule_ids=rule_info["engine_rule_ids"])
            except DcabClientError as exc:
                if _DCAB_ANALYSIS_LOCK.locked():
                    try:
                        _DCAB_ANALYSIS_LOCK.release()
                    except RuntimeError:
                        pass
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            except Exception:
                if _DCAB_ANALYSIS_LOCK.locked():
                    try:
                        _DCAB_ANALYSIS_LOCK.release()
                    except RuntimeError:
                        pass
                raise

            detection_id = normalize_detection_id(started.get("detection_id", ""))
            _cleanup_expired_tasks()
            task_data = {
                "request_id": request_id,
                "status": "running",
                "engine": "dcab_http",
                "detection_id": detection_id,
                "project_id": report_project_id,
                "project_path": str(analysis_root),
                "upload_extract_root": str(project_root),
                "dcab_source_root": str(dcab_source_root),
                "dcab_project_path": str(dcab_project_path),
                "dcab_safe_task_dir": str(dcab_project_path.parent),
                "expected_analysis_files": expected_analysis_files,
                "project_name": final_project_name,
                "is_uniportal": is_uniportal,
                "save_report": True,
                "dcab_lock_held": True,
                **public_rule_info,
                **saved_info,
                "created_at": time.time(),
                "updated_at": time.time(),
                "dcab_start_response": started,
            }
            if save_project_error:
                task_data["saved_project_error"] = save_project_error

            task_data.update(_dcab_progress_status_fields({}))
            task_data["parsed_files_count"] = 0
            task_data["dcab_worker_started"] = False
            with _TASK_STORE_LOCK:
                _TASK_STORE[request_id] = task_data
            _start_dcab_task_worker(request_id)

            response_payload = {
                "request_id": request_id,
                "project_id": report_project_id,
                "status": "running",
                "engine": "dcab_http",
                "detection_id": detection_id,
                "expected_analysis_files": expected_analysis_files,
                **public_rule_info,
                **saved_info,
                "uniportal_writeback": "ok" if is_uniportal else "no",
                "message": "鍒嗘瀽浠诲姟宸叉彁浜わ紝璇疯疆璇?GET /status/{request_id} 鑾峰彇缁撴灉",
            }
            if save_project_error:
                response_payload["saved_project_error"] = save_project_error
            return JSONResponse(response_payload)

        saved_project_id = _make_upload_project_id(final_project_name, request_id) if save_as_project else ""
        report_project_id = saved_project_id or request_id

        _cleanup_expired_tasks()

        with _TASK_STORE_LOCK:
            _TASK_STORE[request_id] = {
                "request_id": request_id,
                "status": "pending",
                "engine": "codetidy",
                "project_id": report_project_id,
                "save_as_project": save_as_project,
                "saved_project_id": saved_project_id,
                "project_name": final_project_name,
                "created_at": time.time(),
                "updated_at": time.time(),
            }

        bg_kwargs = dict(
            request_id=request_id,
            workdir=workdir,
            target_files=target_files,
            project_name="",
            timeout=CODETIDY_TIMEOUT,
            keep=keep,
            save_report=True,
            project_id=report_project_id,
            is_uniportal=False,
            save_as_project=save_as_project,
            saved_project_id=saved_project_id,
            saved_project_name=final_project_name,
            requested_project_name=(project_name or "").strip(),
            original_filename=original_filename,
            saved_paths=saved_paths,
            extract_dir=extract_dir if zip_uploads else None,
            all_code_files=all_code_files if zip_uploads else None,
            zip_uploads=bool(zip_uploads),
        )

        thread = threading.Thread(
            target=_run_analysis_background,
            kwargs=bg_kwargs,
            daemon=True,
        )
        thread.start()

        return JSONResponse({
            "request_id": request_id,
            "project_id": report_project_id,
            "status": "pending",
            "engine": "codetidy",
            "saved_project_id": saved_project_id,
            "message": "分析任务已提交，请轮询 GET /status/{request_id} 获取结果",
        })
    except HTTPException:
        if not keep:
            shutil.rmtree(workdir, ignore_errors=True)
        raise
    except Exception as e:
        if not keep:
            shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"分析任务提交失败: {e}") from e



def _dcab_progress_counts(data: Optional[dict]) -> dict:
    progress_info = data.get("progress_info") if isinstance(data, dict) else None
    if not isinstance(progress_info, dict) and isinstance(data, dict) and isinstance(data.get("data"), dict):
        progress_info = data["data"].get("progress_info")
    if not isinstance(progress_info, dict):
        progress_info = {}
    result = dict(progress_info)
    for key in ("completed_count", "total_count"):
        try:
            if progress_info.get(key) is not None:
                result[key] = int(progress_info.get(key))
        except (TypeError, ValueError):
            result.pop(key, None)
    return result


def _progress_info_complete(data: Optional[dict]) -> bool:
    progress_info = _dcab_progress_counts(data)
    completed = progress_info.get("completed_count")
    total = progress_info.get("total_count")
    if completed is None or total is None:
        return False
    return total > 0 and completed >= total


def _dcab_report_ready_for_completion(report: DSITReport, task: dict, data: Optional[dict]) -> tuple[bool, dict]:
    expected = int(task.get("expected_analysis_files") or 0)
    parsed = _report_parsed_files_count(report)
    progress_info = _dcab_progress_counts(data)
    completed = progress_info.get("completed_count")
    diagnostics = {
        "expected_analysis_files": expected,
        "submitted_analysis_files_count": expected,
        "completed_analysis_files_count": completed if isinstance(completed, int) else None,
        "parsed_files_count": parsed,
        "dcab_progress_info": progress_info,
    }
    if expected > 0 and isinstance(completed, int) and completed >= expected and parsed < expected:
        diagnostics["dcab_aggregate_warning"] = (
            f"DCAB progress completed {completed}/{expected}, "
            f"but aggregate report contains {parsed} source files"
        )
    if expected > 0 and parsed >= expected:
        return True, diagnostics
    if expected > 0 and isinstance(completed, int) and completed >= expected:
        return True, diagnostics
    if _progress_info_complete(data):
        return True, diagnostics
    return False, diagnostics


def _expected_total_from_progress(data: Optional[dict]) -> int:
    progress_info = _dcab_progress_counts(data)
    try:
        return int(progress_info.get("total_count") or 0)
    except (TypeError, ValueError):
        return 0


def _build_empty_dcab_report(
    request_id: str,
    project_name: str,
    project_path: str,
    dcab_project_path: str,
    start_response: Optional[dict],
    data: Optional[dict],
) -> DSITReport:
    report = report_from_defect_list(
        [],
        report_id=request_id,
        project_name=project_name,
        project_path=dcab_project_path,
        source_file_list=source_file_list_from_response(data),
    )
    report.project_path = project_path
    return report


def _load_dcab_fallback_report(
    request_id: str,
    project_name: str,
    project_path: str,
    dcab_project_path: str,
    created_at: float,
    task: dict,
) -> Optional[DSITReport]:
    bugs, found_files = load_recent_xplusx_bugs_with_files(dcab_project_path, since=created_at or 0)
    if not bugs:
        safe_task_dir = task.get("dcab_safe_task_dir")
        if safe_task_dir:
            bugs, found_files = load_recent_xplusx_bugs_with_files(safe_task_dir, since=created_at or 0)
    if not bugs:
        return None
    report = report_from_xplusx_bugs(
        bugs,
        report_id=request_id,
        project_name=project_name,
        project_path=dcab_project_path,
    )
    report.project_path = project_path
    return report


def _check_progress_with_detection_fallback(detection_id: str) -> tuple[Optional[dict], str, bool]:
    data = check_progress(detection_id)
    if not is_empty_check_response(data):
        return data, detection_id, False
    alternate = alternate_detection_id_format(detection_id)
    if alternate and alternate != detection_id:
        alternate_data = check_progress(alternate)
        if not is_empty_check_response(alternate_data):
            return alternate_data, alternate, True
    return data, detection_id, False


def _mark_dcab_report_not_ready(
    request_id: str,
    detection_id: str,
    data: Optional[dict],
    diagnostics: dict,
    used_detection_id_fallback: bool = False,
) -> None:
    fields = _dcab_progress_status_fields(data)
    fields.update(diagnostics)
    _set_task_status(
        request_id,
        "running",
        detection_id=detection_id,
        dcab_last_check=data or {"status": "check_progress_empty"},
        dcab_detection_id_fallback=used_detection_id_fallback,
        **fields,
    )


def _dcab_start_has_no_sources(task: dict) -> bool:
    started = task.get("dcab_start_response")
    if not isinstance(started, dict):
        return False
    text = json.dumps(started, ensure_ascii=False).lower()
    return "no source" in text or "no file" in text or "未识别" in text


def _dcab_progress_message(progress_info: dict) -> str:
    completed = progress_info.get("completed_count")
    total = progress_info.get("total_count")
    if completed is not None and total is not None:
        return f"DCAB analyzing: {completed}/{total}"
    return "DCAB analyzing"


def _dcab_progress_status_fields(data: Optional[dict]) -> dict:
    progress_info = _dcab_progress_counts(data)
    return {
        "progress_info": progress_info,
        "dcab_progress_info": progress_info,
        "completed_count": progress_info.get("completed_count"),
        "total_count": progress_info.get("total_count"),
        "message": _dcab_progress_message(progress_info),
    }


def _start_dcab_task_worker(request_id: str) -> bool:
    with _TASK_STORE_LOCK:
        task = _TASK_STORE.get(request_id)
        if not task or task.get("dcab_worker_started"):
            return False
        task["dcab_worker_started"] = True
        task["updated_at"] = time.time()

    thread = threading.Thread(
        target=_run_dcab_task_worker,
        args=(request_id,),
        daemon=True,
    )
    thread.start()
    return True


def _run_dcab_task_worker(request_id: str) -> None:
    while True:
        with _TASK_STORE_LOCK:
            task = dict(_TASK_STORE.get(request_id) or {})
        if not task or task.get("status") != "running" or task.get("engine") != "dcab_http":
            return

        created_at = float(task.get("created_at") or time.time())
        if time.time() - created_at > CODETIDY_TIMEOUT:
            _set_task_status(
                request_id,
                "failed",
                error={"detail": f"DCAB analysis timed out after {CODETIDY_TIMEOUT}s", "status_code": 504},
                message="DCAB analysis timed out",
            )
            return

        try:
            updated = _poll_dcab_http_task(task)
        except Exception as exc:
            logger.exception("DCAB worker failed for %s", request_id)
            detail = str(exc)
            if isinstance(exc, DcabClientError) or any(
                marker in detail
                for marker in ("RemoteDisconnected", "Connection refused", "timed out", "closed the connection")
            ):
                detail = f"DCAB may have crashed or closed the connection: {detail}"
            _set_task_status(
                request_id,
                "failed",
                error={"detail": detail, "status_code": 502},
                message="DCAB analysis failed",
            )
            return

        status = updated.get("status")
        if status in {"completed", "failed"}:
            return
        time.sleep(max(0.1, DCAB_POLL_INTERVAL_SECONDS))


def _poll_dcab_http_task(task: dict) -> dict:
    request_id = task["request_id"]
    project_id = task.get("project_id", request_id)
    detection_id = normalize_detection_id(task.get("detection_id", ""))
    project_path = task.get("project_path", "")
    dcab_project_path = task.get("dcab_project_path") or project_path
    project_name = task.get("project_name") or Path(project_path).name or project_id
    root = Path(project_path) if project_path else None
    dcab_source_root = Path(task["dcab_source_root"]) if task.get("dcab_source_root") else None
    existing_project = bool(task.get("existing_project", False))
    stat_roots = [
        Path(value)
        for value in [
            task.get("dcab_source_root"),
            task.get("dcab_project_path"),
            task.get("dcab_safe_task_dir"),
            task.get("upload_extract_root"),
            task.get("saved_project_path"),
            task.get("saved_project_root"),
            project_path,
        ]
        if value
    ]
    payload_extras = _task_payload_extras(task)

    try:
        data, detection_id, used_detection_id_fallback = _check_progress_with_detection_fallback(detection_id)
    except DcabClientError as exc:
        _set_task_status(request_id, "failed", error={"detail": str(exc), "status_code": 502})
        with _TASK_STORE_LOCK:
            return dict(_TASK_STORE[request_id])

    _set_task_status(
        request_id,
        "running",
        detection_id=detection_id,
        dcab_last_check=data or {"status": "check_progress_empty"},
        dcab_detection_id_fallback=used_detection_id_fallback,
        **_dcab_progress_status_fields(data),
    )

    defect_list = data.get("defect_list") if isinstance(data, dict) else None
    completed_with_null_defects = defect_list is None and _progress_info_complete(data)
    should_try_fallback = is_empty_check_response(data) or completed_with_null_defects

    if should_try_fallback:
        report = _load_dcab_fallback_report(
            request_id=request_id,
            project_name=project_name,
            project_path=project_path,
            dcab_project_path=dcab_project_path,
            created_at=task.get("created_at", 0),
            task=task,
        )
        if report:
            ready, diagnostics = _dcab_report_ready_for_completion(report, task, data)
            if not ready:
                _mark_dcab_report_not_ready(
                    request_id,
                    detection_id,
                    data,
                    diagnostics,
                    used_detection_id_fallback=used_detection_id_fallback,
                )
                with _TASK_STORE_LOCK:
                    return dict(_TASK_STORE[request_id])
            completion_extras = dict(payload_extras)
            completion_extras.update(diagnostics)
            completion_extras["dcab_raw"] = controlled_dcab_raw(data)
            _complete_project_report_task(
                request_id=request_id,
                project_id=project_id,
                report=report,
                save_report=bool(task.get("save_report", True)),
                is_uniportal=bool(task.get("is_uniportal", False)),
                root=root,
                extra_payload=completion_extras,
                stat_roots=stat_roots,
                dcab_source_root=dcab_source_root,
                existing_project=existing_project,
            )
            with _TASK_STORE_LOCK:
                return dict(_TASK_STORE[request_id])

    if completed_with_null_defects:
        report = _build_empty_dcab_report(
            request_id,
            project_name,
            project_path,
            dcab_project_path,
            task.get("dcab_start_response"),
            data,
        )
        ready, diagnostics = _dcab_report_ready_for_completion(report, task, data)
        completion_extras = dict(payload_extras)
        completion_extras.update(diagnostics)
        completion_extras["dcab_raw"] = controlled_dcab_raw(data)
        _complete_project_report_task(
            request_id=request_id,
            project_id=project_id,
            report=report,
            save_report=bool(task.get("save_report", True)),
            is_uniportal=bool(task.get("is_uniportal", False)),
            root=root,
            extra_payload=completion_extras,
            stat_roots=stat_roots,
            dcab_source_root=dcab_source_root,
            existing_project=existing_project,
        )
        with _TASK_STORE_LOCK:
            return dict(_TASK_STORE[request_id])

    if isinstance(defect_list, list):
        report = report_from_defect_list(
            defect_list,
            report_id=request_id,
            project_name=project_name,
            project_path=dcab_project_path,
            source_file_list=source_file_list_from_response(data),
        )
        report.project_path = project_path
        aggregate_report = _merge_dcab_aggregate_report(request_id, report)
        ready, diagnostics = _dcab_report_ready_for_completion(aggregate_report, task, data)
        if not ready:
            _mark_dcab_report_not_ready(
                request_id,
                detection_id,
                data,
                diagnostics,
                used_detection_id_fallback=used_detection_id_fallback,
            )
            with _TASK_STORE_LOCK:
                return dict(_TASK_STORE[request_id])
        completion_extras = dict(payload_extras)
        completion_extras.update(diagnostics)
        completion_extras["dcab_raw"] = controlled_dcab_raw(data)
        _complete_project_report_task(
            request_id=request_id,
            project_id=project_id,
            report=aggregate_report,
            save_report=bool(task.get("save_report", True)),
            is_uniportal=bool(task.get("is_uniportal", False)),
            root=root,
            extra_payload=completion_extras,
            stat_roots=stat_roots,
            dcab_source_root=dcab_source_root,
            existing_project=existing_project,
        )
        with _TASK_STORE_LOCK:
            return dict(_TASK_STORE[request_id])

    if data is None and _dcab_start_has_no_sources(task):
        _set_task_status(
            request_id,
            "failed",
            error={
                "detail": "DCAB 未识别到可分析源文件，可能是项目根目录不正确或路径/目录名不兼容",
                "status_code": 422,
            },
            dcab_last_check={"status": "check_progress_empty"},
        )
        with _TASK_STORE_LOCK:
            return dict(_TASK_STORE[request_id])

    _set_task_status(
        request_id,
        "running",
        detection_id=detection_id,
        dcab_last_check=data or {"status": "check_progress_empty"},
        dcab_detection_id_fallback=used_detection_id_fallback,
        expected_analysis_files=int(task.get("expected_analysis_files") or 0),
        parsed_files_count=int(task.get("parsed_files_count") or 0),
        **_dcab_progress_status_fields(data),
    )
    with _TASK_STORE_LOCK:
        return dict(_TASK_STORE[request_id])


@app.post("/projects/upload")
async def upload_project(
    file: UploadFile = File(..., description="ZIP project archive"),
    project_id: str = Form("", description="Optional project id"),
    project_name: str = Form("", description="Optional project name"),
) -> JSONResponse:
    original_name = _safe_filename(file.filename or "project.zip")
    if Path(original_name).suffix.lower() != ".zip":
        raise HTTPException(status_code=400, detail="只支持上传 ZIP 工程包")

    portal_base = Path(MOCK_UNIPORTAL_DIR) if MOCK_UNIPORTAL_DIR else Path(UNIPORTAL_STORAGE_PATH or "/data/uniportal")
    destination_root = (portal_base / "local-upload").resolve()
    destination_root.mkdir(parents=True, exist_ok=True)

    name_source = project_id or project_name or Path(original_name).stem
    pid = _unique_project_id(name_source, destination_root)
    request_id = f"upload_{uuid.uuid4().hex}"
    tmp_root = Path(tempfile.mkdtemp(prefix=f"{request_id}_"))
    zip_path = tmp_root / original_name
    extract_dir = tmp_root / "extract"
    try:
        total = 0
        with zip_path.open("wb") as dst:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_ZIP_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"zip 文件超过大小限制 ({MAX_ZIP_BYTES} bytes)",
                    )
                dst.write(chunk)

        extract_dir.mkdir(parents=True, exist_ok=True)
        _safe_extract_zip(zip_path, extract_dir)
        code_files = _collect_code_files(extract_dir)
        if not code_files:
            raise HTTPException(status_code=400, detail="ZIP 工程包中未找到可分析源码文件")

        saved = _save_uploaded_project(
            request_id=request_id,
            project_id=pid,
            project_name=project_name or Path(original_name).stem,
            original_filename=original_name,
            zip_uploads=True,
            saved_paths=[],
            extract_dir=extract_dir,
            all_code_files=code_files,
            destination_root=destination_root,
            source="uniportal",
            requested_project_name=project_name,
        )
    except HTTPException:
        raise
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"保存项目失败: {exc}") from exc
    finally:
        await file.close()
        shutil.rmtree(tmp_root, ignore_errors=True)

    return JSONResponse(
        {
            "project_id": pid,
            "portal_project_id": "local-upload",
            "source": "uniportal",
            "message": "项目已上传到项目库",
            "project_path": saved.get("saved_project_root"),
        }
    )


@app.post("/projects/{project_id}/analyze")
def analyze_project(
    project_id: str,
    portal_project_id: str = Query("", description="UniPortal project id"),
    rule_set: Optional[str] = Query(None, description="规则集: GJB-8114/GJB-5369/CWE-C/MISRA-2008/MISRA-2012"),
    entry: Optional[str] = Query(
        None,
        description="入口源文件相对路径, 缺省时分析所有 .c/.cc/.cpp/.cxx",
    ),
    keep: bool = Query(False, description="保留工作目录, 便于调试"),
    save_report: bool = Query(
        True,
        description="把诊断报告落盘到 workspaces/_reports/{project_id}/",
    ),
    request_body: Optional[dict] = Body(None),
) -> JSONResponse:
    pid = _safe_project_id(project_id)
    portal_pid = (portal_project_id or "").strip()
    if not portal_pid and _build_item_index().get(pid) is not None:
        raise HTTPException(
            status_code=400,
            detail="缺少 portal_project_id，无法安全地重新分析 UniPortal 项目",
        )
    root = _resolve_project_path(pid)
    if portal_pid:
        portal_projects = _uniportal_project_roots(portal_pid)
        portal_root = portal_projects.get(pid)
        if portal_root is None or not portal_root.is_dir():
            raise HTTPException(
                status_code=404,
                detail=f"项目 {pid!r} 在 UniPortal 项目 {portal_pid!r} 下不存在",
            )
    is_uniportal = _is_uniportal_path(root)

    code_files = _collect_code_files(root)
    if not code_files:
        raise HTTPException(status_code=400, detail="项目内没有可分析的源文件")

    if entry is not None:
        rel_entry = entry.strip().lstrip("/\\")
        if not rel_entry:
            raise HTTPException(status_code=400, detail="entry 不能为空")
        if ".." in Path(rel_entry).parts:
            raise HTTPException(status_code=400, detail=f"非法 entry: {entry!r}")
        entry_path = (root / rel_entry).resolve()
        try:
            entry_path.relative_to(root.resolve())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"entry 越界: {entry!r}")
        if not entry_path.exists():
            raise HTTPException(status_code=400, detail=f"未找到入口文件: {entry!r}")
        target_files = [entry_path]
    else:
        target_files = [p for p in code_files if p.suffix.lower() in SOURCE_SUFFIXES]
        if not target_files:
            raise HTTPException(
                status_code=400,
                detail="项目内没有 .c/.cc/.cpp/.cxx 源文件, 请指定 entry 或上传含源文件的项目",
            )

    request_id = f"proj_{uuid.uuid4().hex[:12]}"

    if ANALYSIS_ENGINE == "dcab_http":
        selected_rule_set = _selected_rule_set_from_request(request_body, rule_set)
        rule_info = _rule_ids_observability(selected_rule_set)
        public_rule_info = _public_rule_info(rule_info)
        if not _DCAB_ANALYSIS_LOCK.acquire(blocking=False):
            raise HTTPException(
                status_code=409,
                detail="DCAB 正在执行另一个分析任务，请稍后重试",
            )
        try:
            dcab_source_root = _find_project_root(root, code_files)
            expected_analysis_files = len(_analysis_source_files(dcab_source_root))
            display_project_name = _display_name_from_meta(root) or root.name
            dcab_project_path = _prepare_dcab_safe_project_dir(dcab_source_root, request_id)
            _cleanup_dcab_runtime_dirs()
            started = start_progress(str(dcab_project_path), rule_ids=rule_info["engine_rule_ids"])
        except DcabClientError as exc:
            if _DCAB_ANALYSIS_LOCK.locked():
                try:
                    _DCAB_ANALYSIS_LOCK.release()
                except RuntimeError:
                    pass
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception:
            if _DCAB_ANALYSIS_LOCK.locked():
                try:
                    _DCAB_ANALYSIS_LOCK.release()
                except RuntimeError:
                    pass
            raise

        detection_id = normalize_detection_id(started.get("detection_id", ""))
        _cleanup_expired_tasks()
        task_data = {
            "request_id": request_id,
            "status": "running",
            "engine": "dcab_http",
            "detection_id": detection_id,
            "project_id": project_id,
            "project_path": str(root),
            "dcab_source_root": str(dcab_source_root),
            "dcab_project_path": str(dcab_project_path),
            "dcab_safe_task_dir": str(dcab_project_path.parent),
            "expected_analysis_files": expected_analysis_files,
            "project_name": display_project_name,
            "is_uniportal": is_uniportal,
            "existing_project": True,
            "save_report": save_report,
            "dcab_lock_held": True,
            **public_rule_info,
            "created_at": time.time(),
            "updated_at": time.time(),
            "dcab_start_response": started,
            "parsed_files_count": 0,
            "dcab_worker_started": False,
            **_dcab_progress_status_fields({}),
        }
        with _TASK_STORE_LOCK:
            _TASK_STORE[request_id] = task_data
        _start_dcab_task_worker(request_id)

        return JSONResponse({
            "request_id": request_id,
            "project_id": project_id,
            "status": "running",
            "engine": "dcab_http",
            "detection_id": detection_id,
            "expected_analysis_files": expected_analysis_files,
            **public_rule_info,
            "message": "分析任务已提交，请轮询 GET /status/{request_id} 获取结果",
        })

    _cleanup_expired_tasks()

    with _TASK_STORE_LOCK:
        _TASK_STORE[request_id] = {
            "request_id": request_id,
            "status": "pending",
            "created_at": time.time(),
            "updated_at": time.time(),
        }

    thread = threading.Thread(
        target=_run_analysis_background,
        kwargs=dict(
            request_id=request_id,
            workdir=root,
            target_files=target_files,
            project_name=root.name,
            timeout=CODETIDY_TIMEOUT,
            keep=keep,
            save_report=save_report,
            project_id=project_id,
            is_uniportal=is_uniportal,
            root=root,
            cleanup_workdir=False,
            existing_project=True,
        ),
        daemon=True,
    )
    thread.start()

    return JSONResponse({
        "request_id": request_id,
        "project_id": project_id,
        "status": "pending",
        "message": "分析任务已提交，请轮询 GET /status/{request_id} 获取结果",
    })

# =====================================================================
# 异步分析任务轮询
# =====================================================================

@app.get("/status/{request_id}")
def get_analysis_status(request_id: str) -> JSONResponse:
    """查询分析任务状态.

    GET /status/{request_id} is intentionally cache-only: it must not call
    DCAB check_progress, parse reports, or wait for the background worker.
    """
    _cleanup_expired_tasks()

    with _TASK_STORE_LOCK:
        task = _TASK_STORE.get(request_id)
        if task:
            task = dict(task)

    if not task:
        raise HTTPException(
            status_code=404,
            detail=f"任务 {request_id!r} 不存在或已过期（TTL={TASK_TTL_SECONDS}s）",
        )

    return JSONResponse(task)


@app.get("/status")
def list_all_statuses() -> JSONResponse:
    """列出所有活跃任务的状态（调试用）."""
    _cleanup_expired_tasks()

    with _TASK_STORE_LOCK:
        tasks = [
            {
                "request_id": rid,
                "status": t["status"],
                "created_at": t.get("created_at"),
                "updated_at": t.get("updated_at"),
            }
            for rid, t in _TASK_STORE.items()
        ]

    return JSONResponse({"tasks": tasks, "count": len(tasks)})



def _uniportal_project_roots(portal_project_id: str = "") -> Dict[str, Path]:
    roots: Dict[str, Path] = {}
    base_values = []
    if MOCK_UNIPORTAL_DIR:
        base_values.append(Path(MOCK_UNIPORTAL_DIR))
    elif UNIPORTAL_STORAGE_PATH:
        base_values.append(Path(UNIPORTAL_STORAGE_PATH))
    for base in base_values:
        root = base.resolve()
        if not root.is_dir():
            continue
        portal_dirs = []
        if portal_project_id:
            portal_dir = root / _safe_project_id(portal_project_id)
            if portal_dir.is_dir():
                portal_dirs.append(portal_dir)
        else:
            portal_dirs = [entry for entry in root.iterdir() if entry.is_dir() and not entry.name.startswith(".")]
        for portal_dir in portal_dirs:
            for item in portal_dir.iterdir():
                if item.is_dir() and not item.name.startswith((".", "_")):
                    roots[item.name] = item
    return roots


register_source_routes(
    app,
    uniportal_project_roots=_uniportal_project_roots,
    safe_project_id=_safe_project_id,
    ct8114_output_dir=_ct8114_output_dir,
)


def _project_item_payload(project_id: str, root: Path, source: str, writable: bool, portal_project_id: str = "") -> dict:
    status = _check_analysis_status(root, project_id)
    name = _display_name_from_meta(root)
    if not name:
        name = _project_display_name(root, project_id) if source == "uniportal" else _local_project_display_name(root)
    return {
        "project_id": project_id,
        "project_name": name,
        "file_count": _count_code_files(root),
        "status": "ready",
        "source": source,
        "portal_project_id": portal_project_id,
        "writable": writable,
        "analyzed": bool(status.get("analyzed")),
        "last_analysis": status.get("last_analysis"),
        "report_bugs": status.get("report_bugs"),
    }


@app.get("/projects")
def list_projects(
    portal_project_id: str = Query("", description="UniPortal project id"),
    include_local: bool = Query(True, description="Include local private projects"),
) -> JSONResponse:
    projects: List[dict] = []
    seen: set[str] = set()

    uniportal_roots = _uniportal_project_roots(portal_project_id)
    for pid, root in sorted(uniportal_roots.items()):
        projects.append(_project_item_payload(pid, root, "uniportal", UNIPORTAL_WRITABLE or bool(MOCK_UNIPORTAL_DIR), portal_project_id or root.parent.name))
        seen.add(pid)

    if include_local:
        for base in (LOCAL_PROJECTS_DIR, LOCAL_WORKSPACES_DIR):
            if not base.is_dir():
                continue
            for root in sorted([entry for entry in base.iterdir() if entry.is_dir() and not entry.name.startswith((".", "_"))]):
                pid = root.name
                if pid in seen:
                    continue
                projects.append(_project_item_payload(pid, root, "local", True))
                seen.add(pid)

    return JSONResponse({
        "projects": projects,
        "uniportal_mode": UNIPORTAL_MODE or bool(MOCK_UNIPORTAL_DIR),
        "portal_project_id": portal_project_id,
        "include_local": include_local,
    })


@app.get("/projects/{project_id}/reports")
def list_project_rule_set_reports(
    project_id: str,
    portal_project_id: str = Query("", description="UniPortal project id"),
) -> JSONResponse:
    pid = _safe_project_id(project_id)
    root = _resolve_project_path_for_reports(pid, portal_project_id)
    output_dir = _ct8114_output_dir(root)
    reports = {
        rule_set: _rule_set_report_summary(output_dir, rule_set)
        for rule_set in _ALLOWED_RULE_SET_FILENAMES
    }
    return JSONResponse(
        {
            "project_id": pid,
            "portal_project_id": portal_project_id,
            "reports": reports,
        }
    )


@app.get("/projects/{project_id}/reports/{rule_set}")
def get_project_rule_set_report(
    project_id: str,
    rule_set: str,
    portal_project_id: str = Query("", description="UniPortal project id"),
) -> JSONResponse:
    pid = _safe_project_id(project_id)
    selected_rule_set = _safe_report_rule_set(rule_set)
    root = _resolve_project_path_for_reports(pid, portal_project_id)
    report_path, _meta_path = _flat_rule_set_report_paths(_ct8114_output_dir(root), selected_rule_set)
    if not report_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"项目 {pid!r} 尚无 {selected_rule_set} 历史报告",
        )
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"读取规则集报告失败: {exc}") from exc
    return JSONResponse(data)


@app.get("/projects/{project_id}/files")
def get_project_files(project_id: str) -> JSONResponse:
    pid = _safe_project_id(project_id)
    root = _resolve_project_path(pid)
    files = []
    for path in _collect_code_files(root):
        try:
            files.append(path.resolve().relative_to(root.resolve()).as_posix())
        except ValueError:
            files.append(path.name)
    return JSONResponse({
        "project_id": pid,
        "project_path": str(root),
        "files": sorted(files),
    })

@app.get("/projects/{project_id}/last-report")
def get_project_last_report(project_id: str) -> JSONResponse:
    """读取项目上次分析结果.

    查找优先级:
      1. {actual_project_dir}/ct8114/last_report.json (新写回路径)
      2. {project_root}/_ct8114/last_report.json (旧写回路径)
      3. REPORTS_DIR/{project_id}/last_report.json (本地报告)

    供前端在不重跑分析的情况下直接展示历史报告.
    404 表示该项目从未被分析过.
    """
    pid = _safe_project_id(project_id)
    root = _resolve_project_path(pid)
    report_file = _find_last_report_file(root, pid)
    if report_file is None:
        raise HTTPException(
            status_code=404,
            detail=f"项目 {pid!r} 尚无历史报告，请先运行分析",
        )
    try:
        data = json.loads(report_file.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取历史报告失败: {e}") from e
    report = data.get('report') if isinstance(data.get('report'), dict) else None
    if report is not None and 'project_name' in report:
        actual_project_dir = _find_actual_project_dir(root)
        new_meta = _read_json_dict(_ct8114_output_dir(root) / 'meta.json')
        legacy_actual_meta = _read_json_dict(actual_project_dir / 'ct8114' / 'meta.json')
        legacy_meta = _read_json_dict(root / 'meta.json')
        report['project_name'] = _resolve_report_project_name(
            root,
            actual_project_dir,
            pid,
            report,
            new_meta or legacy_actual_meta,
            legacy_meta,
        )
    return JSONResponse(data)


@app.delete("/projects/{project_id}")
def delete_project(project_id: str) -> JSONResponse:
    """删除项目.

    权限规则:
      - 私有卷项目 (local): 始终可删
      - UniPortal 共享卷项目: 仅当 UNIPORTAL_WRITABLE=true 或 MOCK_UNIPORTAL_DIR 时可删
      - 模拟模式 (MOCK_UNIPORTAL_DIR): 始终可删（仅本地文件）
    """

    if not ENABLE_PROJECT_DELETE:
        raise HTTPException(status_code=403, detail="项目删除功能未启用")

    pid = _safe_project_id(project_id)
    item = _build_item_index().get(pid)
    if item and item.is_dir():
        raise HTTPException(status_code=403, detail="禁止通过本工具删除 UniPortal 共享项目")

    for local in (LOCAL_PROJECTS_DIR / pid, LOCAL_WORKSPACES_DIR / pid):
        if local.is_dir():
            resolved = local.resolve()
            allowed_roots = [LOCAL_PROJECTS_DIR.resolve(), LOCAL_WORKSPACES_DIR.resolve()]
            if resolved in allowed_roots:
                raise HTTPException(status_code=403, detail="禁止删除本地工作区根目录")
            if not any(_path_is_relative_to(resolved, root) for root in allowed_roots):
                raise HTTPException(status_code=403, detail="项目路径不在本地工作区内")
            shutil.rmtree(resolved, ignore_errors=True)
            return JSONResponse({"deleted": True, "project_id": pid, "source": "local"})
    raise HTTPException(status_code=404, detail=f"项目 {pid!r} 未找到")


def _codetidy_debug_payload(action: str) -> dict:
    if ANALYSIS_ENGINE == "dcab_http":
        config = get_dcab_config()
        return {
            "status": "ok",
            "action": action,
            "engine": "dcab_http",
            "dcab_base_url": config["base_url"],
            "dcab_start_path": config["start_path"],
            "dcab_check_path": config["check_path"],
            "deepsitr_workdir": config.get("workdir") or None,
            "message": "DCA HTTP engine configured",
        }
    codetidy = find_codetidy_bin()
    return {
        "status": "ok" if codetidy else "error",
        "action": action,
        "codetidy_bin": str(codetidy) if codetidy else None,
        "message": "codetidy.exe 路径解析成功" if codetidy else CODETIDY_NOT_FOUND_MESSAGE,
        "checked_paths": get_codetidy_search_paths(),
    }


@app.get("/debug/dcab/start")
def debug_dcab_start() -> JSONResponse:
    """Debug endpoint: resolve the codetidy/DCAB executable path."""

    payload = _codetidy_debug_payload("start")
    return JSONResponse(payload, status_code=200 if payload["status"] == "ok" else 500)


@app.get("/debug/dcab/check")
def debug_dcab_check() -> JSONResponse:
    """Debug endpoint: check the codetidy/DCAB executable path."""

    payload = _codetidy_debug_payload("check")
    return JSONResponse(payload, status_code=200 if payload["status"] == "ok" else 500)


@app.get("/healthz")
def healthz() -> dict:
    return {
        "status": "ok",
        "engine": ANALYSIS_ENGINE,
        "async_mode": True,
        "active_tasks": len(_TASK_STORE),
        "codetidy_timeout": CODETIDY_TIMEOUT,
        "task_ttl_seconds": TASK_TTL_SECONDS,
        "dcab": get_dcab_config() if ANALYSIS_ENGINE == "dcab_http" else None,
        "uniportal_mode": UNIPORTAL_MODE or bool(MOCK_UNIPORTAL_DIR),
        "uniportal_storage_path": UNIPORTAL_STORAGE_PATH or MOCK_UNIPORTAL_DIR or None,
        "uniportal_writable": UNIPORTAL_WRITABLE or bool(MOCK_UNIPORTAL_DIR),
        "mock_uniportal": bool(MOCK_UNIPORTAL_DIR),
        "local_workspaces_dir": str(LOCAL_WORKSPACES_DIR),
    }




































