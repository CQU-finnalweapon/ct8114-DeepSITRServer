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
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
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
from routers_dsit import router as dsit_router


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
# 报告存储目录
REPORTS_DIR = Path(os.environ.get("REPORTS_DIR", "workspaces/_reports"))

# 模拟分析模式（本地测试用，无需 codetidy.exe）
MOCK_ANALYSIS = os.environ.get("MOCK_ANALYSIS", "").lower() == "true"

SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx"}
HEADER_SUFFIXES = {".h", ".hpp", ".hxx"}
CODE_SUFFIXES = SOURCE_SUFFIXES | HEADER_SUFFIXES

_TOOL_INTERNAL_DIRS = {"_ct8114", "_reports", "_dsit_reports", "__pycache__", ".git", ".idea", ".vscode"}

# =====================================================================
# 异步分析任务存储
# =====================================================================

# 任务状态: pending → running → completed / failed
_TASK_STORE: Dict[str, dict] = {}
_TASK_STORE_LOCK = threading.Lock()

# 任务过期时间 (秒), 超时自动清理
TASK_TTL_SECONDS = int(os.environ.get("TASK_TTL_SECONDS", "3600"))


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
            del _TASK_STORE[rid]
    return len(expired)


def _set_task_status(request_id: str, status: str, **extra) -> None:
    """线程安全地更新任务状态."""
    with _TASK_STORE_LOCK:
        if request_id in _TASK_STORE:
            _TASK_STORE[request_id]["status"] = status
            _TASK_STORE[request_id]["updated_at"] = time.time()
            _TASK_STORE[request_id].update(extra)


def _run_analysis_background(
    request_id: str,
    workdir: Path,
    target_files: List[Path],
    project_name: str = "",
    timeout: int = 300,
    keep: bool = False,
    save_report: bool = True,
    project_id: str = "",
    is_uniportal: bool = False,
    root: Optional[Path] = None,
    saved_paths: Optional[List[Path]] = None,
    extract_dir: Optional[Path] = None,
    all_code_files: Optional[List[Path]] = None,
    zip_uploads: bool = False,
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
        }

        # 保存报告到本地
        if save_report:
            try:
                out_dir = REPORTS_DIR / (project_id or request_id)
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / "last_report.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                payload["saved_report"] = str(out_dir / "last_report.json")
            except OSError as e:
                payload["save_report_error"] = str(e)

        # 模拟共享卷 — 上传项目自动保存
        if MOCK_UNIPORTAL_DIR and saved_paths:
            try:
                mock_root = Path(MOCK_UNIPORTAL_DIR).resolve()
                mock_root.mkdir(parents=True, exist_ok=True)
                portal_dir = mock_root / "_upload"
                portal_dir.mkdir(parents=True, exist_ok=True)

                if zip_uploads and extract_dir and all_code_files:
                    project_name_src = Path(saved_paths[0].name).stem if saved_paths else "project"
                else:
                    project_name_src = target_files[0].stem if target_files else "project"

                upload_pid = f"upload_{uuid.uuid4().hex[:8]}"
                project_dir = portal_dir / upload_pid
                project_dir.mkdir(parents=True, exist_ok=True)

                if zip_uploads and extract_dir and all_code_files:
                    proj_root = _find_project_root(extract_dir, all_code_files)
                    for f in proj_root.rglob("*"):
                        if f.is_file() and f.suffix.lower() in CODE_SUFFIXES:
                            rel = f.relative_to(proj_root)
                            dest = project_dir / rel
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(f, dest)
                else:
                    for f in saved_paths:
                        shutil.copy2(f, project_dir / f.name)

                wb_info = _write_back_to_uniportal(project_dir, upload_pid, payload)
                payload["uniportal_writeback"] = "ok"
                payload["uniportal_writeback_path"] = wb_info["report_path"]
                payload["uniportal_writeback_time"] = wb_info["last_analysis"]
                payload["saved_project_id"] = upload_pid
                payload["saved_project_path"] = str(project_dir)
            except OSError as e:
                payload["uniportal_writeback_error"] = str(e)

        # 共享卷写回 (项目分析)
        if is_uniportal and root and (UNIPORTAL_WRITABLE or bool(MOCK_UNIPORTAL_DIR)):
            try:
                wb_info = _write_back_to_uniportal(root, project_id, payload)
                payload["uniportal_writeback"] = "ok"
                payload["uniportal_writeback_path"] = wb_info["report_path"]
                payload["uniportal_writeback_time"] = wb_info["last_analysis"]
            except OSError as e:
                payload["uniportal_writeback_error"] = str(e)

        # 清理临时目录
        if not keep:
            shutil.rmtree(workdir, ignore_errors=True)

        _set_task_status(request_id, "completed", payload=payload)

    except HTTPException as e:
        if not keep:
            shutil.rmtree(workdir, ignore_errors=True)
        _set_task_status(
            request_id,
            "failed",
            error={"detail": e.detail, "status_code": e.status_code},
        )
    except Exception as e:
        if not keep:
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
    """Return the most useful project root inside an extracted zip.

    Common zip packages contain a single top-level directory. In that case use
    it as the project root; otherwise keep the extraction directory.
    """

    direct_children = [
        p for p in extract_dir.iterdir()
        if p.is_dir() and not p.name.startswith((".", "__MACOSX"))
    ]
    visible_files = [
        p for p in extract_dir.iterdir()
        if p.is_file() and not p.name.startswith(".")
    ]
    if len(direct_children) == 1 and not visible_files:
        child = direct_children[0]
        try:
            if all(p.resolve().is_relative_to(child.resolve()) for p in code_files):
                return child
        except AttributeError:
            child_resolved = str(child.resolve())
            if all(str(p.resolve()).startswith(child_resolved) for p in code_files):
                return child
    return extract_dir


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
    local = LOCAL_WORKSPACES_DIR / pid
    if local.is_dir():
        return local
    raise HTTPException(status_code=404, detail=f"项目 {pid!r} 未找到")


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


def _check_analysis_status(project_path: Path) -> dict:
    """检查项目是否已被 ct8114 分析过，返回分析状态信息.

    返回字段:
        analyzed: bool — 是否有分析报告
        last_analysis: str | None — 最近分析时间 (ISO 格式)
        report_bugs: int | None — 最近分析的问题总数
    """
    report_file = project_path / "_ct8114" / "last_report.json"
    if not report_file.exists():
        return {"analyzed": False, "last_analysis": None, "report_bugs": None}

    try:
        data = json.loads(report_file.read_text(encoding="utf-8"))
        summary = data.get("report", {}).get("summary", {})
        # last_analysis 优先从 meta.json 获取
        meta_file = project_path / "meta.json"
        last_analysis = None
        if meta_file.exists():
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            last_analysis = meta.get("ct8114_last_analysis")
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
    timeout: int = 300,
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

    request_id = f"codetidy_{uuid.uuid4().hex[:12]}"
    base_tmp = Path(tempfile.gettempdir()) / "ct8114"
    base_tmp.mkdir(parents=True, exist_ok=True)
    workdir = base_tmp / request_id
    workdir.mkdir(parents=True, exist_ok=False)

    saved_paths: List[Path] = []
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

        _cleanup_expired_tasks()

        with _TASK_STORE_LOCK:
            _TASK_STORE[request_id] = {
                "request_id": request_id,
                "status": "pending",
                "created_at": time.time(),
                "updated_at": time.time(),
            }

        # 后台线程参数
        bg_kwargs = dict(
            request_id=request_id,
            workdir=workdir,
            target_files=target_files,
            project_name="",
            timeout=120,
            keep=keep,
            save_report=True,
            project_id=request_id,
            is_uniportal=False,
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
            "status": "pending",
            "message": "分析任务已提交，请轮询 GET /status/{request_id} 获取结果",
        })
    except Exception:
        if not keep:
            shutil.rmtree(workdir, ignore_errors=True)
        raise


# =====================================================================
# UniPortal / 本地项目接口 (双源)
# =====================================================================

@app.get("/projects")
def list_projects(
    portal_project_id: Optional[str] = Query(
        None,
        description=(
            "UniPortal 当前工程 ID. 仅列出该工程下的 item; "
            "未传时不返回任何 UniPortal 项目 (安全默认, 防止跨工程数据泄露)."
        ),
    ),
) -> JSONResponse:
    """合并两个数据源, 给前端做项目列表. 每条记录带 source 字段区分来源.

    工程隔离 (见 SUBTOOL_INTEGRATION_GUIDE §5):
      * 带 portal_project_id: 共享卷只扫该工程目录下的 item
      * 未带: 完全不返回 UniPortal 项目 (子工具被直接访问时的安全默认)
    私有上传始终展示, 不受 portal_project_id 影响.

    模拟模式: 当 MOCK_UNIPORTAL_DIR 设置时, portal_project_id 参数无效,
    直接列出模拟共享卷所有项目.
    """

    items: List[dict] = []

    # 1. UniPortal 共享卷 (按工程隔离扫描)
    uniportal_active = UNIPORTAL_MODE or bool(MOCK_UNIPORTAL_DIR)

    if uniportal_active:
        if MOCK_UNIPORTAL_DIR:
            # 模拟模式: 列出所有模拟共享卷项目
            index = _build_item_index()
            for item_id, item_path in sorted(index.items()):
                analysis_info = _check_analysis_status(item_path)
                items.append({
                    "project_id": item_id,
                    "project_name": _project_display_name(item_path, item_id),
                    "file_count": _count_code_files(item_path),
                    "status": "available",
                    "source": "uniportal",
                    "writable": True,
                    **analysis_info,
                })
        elif portal_project_id:
            pid = _safe_project_id(portal_project_id)  # 非法直接 400
            proj_path = Path(UNIPORTAL_STORAGE_PATH) / pid
            if proj_path.is_dir():
                for item in sorted(proj_path.iterdir()):
                    if not item.is_dir() or item.name.startswith((".", "_")):
                        continue
                    analysis_info = _check_analysis_status(item)
                    items.append({
                        "project_id": item.name,
                        "project_name": _project_display_name(item, item.name),
                        "file_count": _count_code_files(item),
                        "status": "available",
                        "source": "uniportal",
                        "writable": UNIPORTAL_WRITABLE,
                        **analysis_info,
                    })

    # 2. 本工具私有卷 (读写) — 跳过本工具内部目录 (_ct8114 等)
    #    并过滤"空壳目录": 对 UniPortal item 跑过分析后会在私有卷里留下
    #    {item_id}/_ct8114/last_report.json, 这种目录没有源码 (file_count==0),
    #    不应作为"私有项目"展示, 否则会跟真正的私有上传混淆.
    if LOCAL_WORKSPACES_DIR.is_dir():
        for sub in sorted(LOCAL_WORKSPACES_DIR.iterdir()):
            if not sub.is_dir() or sub.name.startswith((".", "_")):
                continue
            if sub.name in _TOOL_INTERNAL_DIRS:
                continue
            file_count = _count_code_files(sub)
            if file_count == 0:
                continue  # 没源码的空壳 (通常是分析报告副产物), 不展示
            items.append({
                "project_id": sub.name,
                "project_name": _local_project_display_name(sub),
                "file_count": file_count,
                "status": "available",
                "source": "local",
            })

    return JSONResponse({
        "projects": items,
        "uniportal_mode": UNIPORTAL_MODE,
        "portal_project_id": portal_project_id,  # 回显给前端确认
    })


@app.get("/projects/{project_id}/files")
def list_project_files(project_id: str) -> JSONResponse:
    """列出项目内全部可分析的源文件 (相对路径)."""

    root = _resolve_project_path(project_id)
    files: List[str] = []
    for p in _collect_code_files(root):
        try:
            rel = p.relative_to(root).as_posix()
        except ValueError:
            continue
        files.append(rel)
    return JSONResponse({"project_id": project_id, "files": sorted(files)})


def _write_back_to_uniportal(root: Path, project_id: str, payload: dict) -> dict:
    """将分析报告写回 UniPortal 共享卷项目目录.

    写入路径: {root}/_ct8114/last_report.json
    同时更新 meta.json 记录最近分析时间.

    这是 ct8114 子工具与 UniPortal 一体化平台双向通信的关键:
    分析结果写回共享卷后, UniPortal 可在项目详情页直接展示.

    Returns:
        dict with write-back info for API response
    """
    ct8114_dir = root / "_ct8114"
    ct8114_dir.mkdir(parents=True, exist_ok=True)

    report_path = ct8114_dir / "last_report.json"
    # 保存完整报告
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 更新元信息
    from datetime import datetime
    meta_path = root / "meta.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    now = datetime.now().isoformat()
    meta["ct8114_last_analysis"] = now
    meta["ct8114_report_path"] = str(report_path)
    summary = payload.get("report", {}).get("summary", {})
    if summary:
        meta["ct8114_summary"] = summary

    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "report_path": str(report_path),
        "meta_path": str(meta_path),
        "last_analysis": now,
    }


@app.post("/projects/{project_id}/analyze")
def analyze_project(
    project_id: str,
    entry: Optional[str] = Query(
        None,
        description="入口源文件相对路径, 缺省时分析所有 .c/.cc/.cpp/.cxx",
    ),
    keep: bool = Query(False, description="保留工作目录, 便于调试"),
    save_report: bool = Query(
        True,
        description="把诊断报告落盘到 workspaces/_reports/{project_id}/",
    ),
) -> JSONResponse:
    """对项目运行 codetidy.exe 分析，返回 DSIT 格式报告.

    当共享卷可写时 (UNIPORTAL_WRITABLE=true 或 MOCK_UNIPORTAL_DIR 设置),
    分析报告同时写回共享卷项目目录下的 _ct8114/ 子目录,
    供 UniPortal 平台或其他子工具读取.
    """
    root = _resolve_project_path(project_id)

    # 判断项目是否来自共享卷
    uniportal_active = UNIPORTAL_MODE or bool(MOCK_UNIPORTAL_DIR)
    is_uniportal = uniportal_active and project_id in _build_item_index()

    code_files = _collect_code_files(root)
    if not code_files:
        raise HTTPException(status_code=400, detail="项目内没有可分析的源文件")

    if entry:
        rel_entry = entry.strip().lstrip("/\\")
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

    # 启动后台线程执行 codetidy 分析
    # 前端通过 GET /status/{request_id} 轮询获取结果

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
            timeout=300,
            keep=keep,
            save_report=save_report,
            project_id=project_id,
            is_uniportal=is_uniportal,
            root=root,
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

    前端在 POST /analyze 或 POST /projects/{id}/analyze 后，
    使用返回的 request_id 轮询此端点:

        轮询间隔建议: 1-2 秒
        超时建议: 300 秒 (与后端分析超时一致)

    状态说明:
        pending   — 任务已入队，等待执行
        running   — codetidy 正在分析中
        completed — 分析完成，payload 中包含完整报告
        failed    — 分析失败，error 中包含错误信息

    Returns:
        {
            "request_id": "...",
            "status": "pending|running|completed|failed",
            "payload": {...},        // 仅在 completed 时
            "error": {...},          // 仅在 failed 时
            "created_at": 123456.0,
            "updated_at": 123456.0,
        }
    """
    _cleanup_expired_tasks()

    with _TASK_STORE_LOCK:
        task = _TASK_STORE.get(request_id)

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


@app.delete("/projects/{project_id}")
def delete_project(project_id: str) -> JSONResponse:
    """删除项目.

    权限规则:
      - 私有卷项目 (local): 始终可删
      - UniPortal 共享卷项目: 仅当 UNIPORTAL_WRITABLE=true 或 MOCK_UNIPORTAL_DIR 时可删
      - 模拟模式 (MOCK_UNIPORTAL_DIR): 始终可删（仅本地文件）
    """

    pid = _safe_project_id(project_id)
    local = LOCAL_WORKSPACES_DIR / pid
    if local.is_dir():
        shutil.rmtree(local, ignore_errors=True)
        return JSONResponse({"deleted": True, "project_id": pid, "source": "local"})

    uniportal_active = UNIPORTAL_MODE or bool(MOCK_UNIPORTAL_DIR)
    if uniportal_active and pid in _build_item_index():
        if UNIPORTAL_WRITABLE or bool(MOCK_UNIPORTAL_DIR):
            item_path = _build_item_index()[pid]
            shutil.rmtree(item_path, ignore_errors=True)
            return JSONResponse({
                "deleted": True,
                "project_id": pid,
                "source": "uniportal",
            })
        raise HTTPException(
            status_code=403,
            detail="UniPortal 共享卷为只读模式，请到 UniPortal 删除项目",
        )
    raise HTTPException(status_code=404, detail=f"项目 {pid!r} 未找到")


def _codetidy_debug_payload(action: str) -> dict:
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
        "engine": "codetidy (DeepSITRServer)",
        "async_mode": True,
        "active_tasks": len(_TASK_STORE),
        "task_ttl_seconds": TASK_TTL_SECONDS,
        "uniportal_mode": UNIPORTAL_MODE or bool(MOCK_UNIPORTAL_DIR),
        "uniportal_storage_path": UNIPORTAL_STORAGE_PATH or MOCK_UNIPORTAL_DIR or None,
        "uniportal_writable": UNIPORTAL_WRITABLE or bool(MOCK_UNIPORTAL_DIR),
        "mock_uniportal": bool(MOCK_UNIPORTAL_DIR),
        "local_workspaces_dir": str(LOCAL_WORKSPACES_DIR),
    }
