"""基于 FastAPI 的 clang-tidy (gjb8114) 分析服务 (UniPortal 双源接入版).

工作流程概览
------------

A. 即时上传分析 (与单机版完全一致, 保留兼容)::

    POST /analyze
        multipart files=<file1>&files=<file2>...
        ?entry=test.c&keep=false

   1. 为本次请求生成 UUID, 在系统临时目录下建立专用工作目录;
   2. 把上传的文件落盘到该目录, 调用 ``clang-tidy -export-fixes=fixes.yaml``;
   3. 解析生成的 YAML, 把诊断结果以 JSON 返回前端;
   4. 清理临时目录 (可通过 ``?keep=true`` 关闭, 便于调试).

B. UniPortal / 本工具私有项目分析::

    GET    /projects                         # 列出两个数据源的项目
    GET    /projects/{project_id}/files      # 列出项目内可分析的源文件
    POST   /projects/{project_id}/analyze    # 对项目跑一次 clang-tidy
    DELETE /projects/{project_id}            # 只能删私有卷里的项目

   读路径遵循 SUBTOOL_INTEGRATION_GUIDE 的双源约定:
       1. 先查 LOCAL_WORKSPACES_DIR/{project_id}/   (子工具自上传)
       2. 再查 UNIPORTAL_STORAGE_PATH/*/{project_id}/  (UniPortal 共享卷, 只读)
   写路径全部落在 LOCAL_WORKSPACES_DIR / TASKS_DIR, 不会触碰只读共享卷.

启动方式::

    uvicorn server:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from fixes_parser import parse_fixes_file
from routers_dsit import router as dsit_router


STATIC_DIR = Path(__file__).resolve().parent / "static"


CLANG_TIDY_BIN = os.environ.get("CLANG_TIDY_BIN", "clang-tidy")
GJB_PLUGIN_PATH = os.environ.get(
    "GJB_PLUGIN_PATH",
    "/usr/local/lib/libclang-tidy-gjb8114.so",
)
CHECKS = os.environ.get("CLANG_TIDY_CHECKS", "-*,gjb8114-*")

# 限制即时上传分析的文件总大小, 防止滥用 (默认 5MB)
MAX_TOTAL_BYTES = int(os.environ.get("MAX_TOTAL_BYTES", str(5 * 1024 * 1024)))
ALLOWED_SUFFIXES = {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hxx"}

# ---- UniPortal 双源接入相关配置 ----------------------------------------
# 共享卷 (只读): UniPortal 上传的项目, 目录结构 {portal_proj_id}/{item_id}/...
UNIPORTAL_STORAGE_PATH = os.environ.get("UNIPORTAL_STORAGE_PATH")
UNIPORTAL_MODE = bool(UNIPORTAL_STORAGE_PATH)
# 本工具私有读写卷: 自上传项目 + 分析报告
LOCAL_WORKSPACES_DIR = Path(
    os.environ.get("LOCAL_WORKSPACES_DIR", "/app/local_workspaces")
)
# 本工具私有读写卷: clang-tidy 沙盒 (写 fixes.yaml 用)
TASKS_DIR = Path(os.environ.get("TASKS_DIR", "/app/workspaces/_tasks"))

# 项目分析超时 (UniPortal 项目通常比单文件大, 给得宽一些)
PROJECT_ANALYZE_TIMEOUT = int(os.environ.get("PROJECT_ANALYZE_TIMEOUT", "300"))

SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx"}
HEADER_SUFFIXES = {".h", ".hpp", ".hxx"}
CODE_SUFFIXES = SOURCE_SUFFIXES | HEADER_SUFFIXES

# 子工具自己生成的目录, 在收集源文件 / 列项目时跳过
_TOOL_INTERNAL_DIRS = {"_ct8114", "__pycache__", ".git", ".idea", ".vscode"}


app = FastAPI(title="GJB8114 clang-tidy Service")

# 允许跨域, 方便前端独立部署调试
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


def _count_code_files(root: Path) -> int:
    return len(_collect_code_files(root))


# =====================================================================
# 双源解析: UniPortal 共享卷 + 本地私有卷
# =====================================================================

def _build_item_index() -> Dict[str, Path]:
    """遍历 UNIPORTAL_STORAGE_PATH/{portal_proj}/{item_id}/, 返回 {item_id: 绝对路径}.

    item_id 即子工具用作 project_id 的 UUID. 共享卷为空或环境变量未设置时返回 {}.
    """

    index: Dict[str, Path] = {}
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
    """

    pid = _safe_project_id(project_id)
    if UNIPORTAL_MODE:
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


# =====================================================================
# 即时上传分析 (与单机版兼容, 保留原有行为)
# =====================================================================

def _run_clang_tidy(workdir: Path, target_files: List[Path]) -> subprocess.CompletedProcess:
    fixes_path = workdir / "fixes.yaml"
    cmd = [
        CLANG_TIDY_BIN,
        *[str(p.relative_to(workdir)) for p in target_files],
        f"-checks={CHECKS}",
        f"-load={GJB_PLUGIN_PATH}",
        f"-export-fixes={fixes_path.name}",
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(workdir),
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"未找到 clang-tidy 可执行文件: {CLANG_TIDY_BIN}",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="clang-tidy 执行超时") from exc
    return result


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
    if not files:
        raise HTTPException(status_code=400, detail="未收到任何文件")

    request_id = str(uuid.uuid4())
    base_tmp = Path(tempfile.gettempdir()) / "ct8114"
    base_tmp.mkdir(parents=True, exist_ok=True)
    workdir = base_tmp / request_id
    workdir.mkdir(parents=True, exist_ok=False)

    saved_paths: List[Path] = []
    total_bytes = 0
    try:
        for uf in files:
            name = _safe_filename(uf.filename or "")
            _validate_suffix(name)
            dest = workdir / name
            content = await uf.read()
            total_bytes += len(content)
            if total_bytes > MAX_TOTAL_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"上传文件总大小超过限制 ({MAX_TOTAL_BYTES} bytes)",
                )
            dest.write_bytes(content)
            saved_paths.append(dest)

        if entry is not None:
            entry_name = _safe_filename(entry)
            target_files = [workdir / entry_name]
            if not target_files[0].exists():
                raise HTTPException(
                    status_code=400,
                    detail=f"指定的 entry 文件 {entry_name!r} 未在上传列表中",
                )
        else:
            # 默认分析所有源文件 (.c / .cc / .cpp / .cxx), 头文件作为依赖落盘
            target_files = [
                p for p in saved_paths if p.suffix.lower() in SOURCE_SUFFIXES
            ]
            if not target_files:
                raise HTTPException(
                    status_code=400,
                    detail="上传的文件中没有可分析的源文件 (.c/.cc/.cpp/.cxx)",
                )

        proc = _run_clang_tidy(workdir, target_files)
        fixes_path = workdir / "fixes.yaml"

        # clang-tidy 的 YAML 中记录的是工作目录内的绝对路径,
        # 把它映射回临时目录, 解析器才能读取源文件以补全行列号.
        path_remap = {
            str(workdir): str(workdir),
            f"/{workdir.name}": str(workdir),
        }

        if fixes_path.exists() and fixes_path.stat().st_size > 0:
            report = parse_fixes_file(fixes_path, path_remap=path_remap).to_dict()
        else:
            report = {
                "main_source_file": "",
                "diagnostics": [],
                "summary": {"total": 0, "by_check": {}, "by_level": {}},
            }

        payload = {
            "request_id": request_id,
            "workdir": str(workdir) if keep else None,
            "command": {
                "returncode": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            },
            "report": report,
        }
        return JSONResponse(payload)
    finally:
        if not keep:
            shutil.rmtree(workdir, ignore_errors=True)


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
    """

    items: List[dict] = []

    # 1. UniPortal 共享卷 (按工程隔离扫描)
    if UNIPORTAL_MODE and portal_project_id:
        pid = _safe_project_id(portal_project_id)  # 非法直接 400
        proj_path = Path(UNIPORTAL_STORAGE_PATH) / pid
        if proj_path.is_dir():
            for item in sorted(proj_path.iterdir()):
                if not item.is_dir() or item.name.startswith((".", "_")):
                    continue
                items.append({
                    "project_id": item.name,
                    "project_name": _project_display_name(item, item.name),
                    "file_count": _count_code_files(item),
                    "status": "available",
                    "source": "uniportal",
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


@app.post("/projects/{project_id}/analyze")
def analyze_project(
    project_id: str,
    entry: Optional[str] = Query(
        None,
        description="入口源文件相对路径, 缺省时分析所有 .c/.cc/.cpp/.cxx",
    ),
    keep: bool = Query(False, description="保留沙盒目录, 便于调试"),
    save_report: bool = Query(
        True,
        description="把诊断 JSON 落盘到 LOCAL_WORKSPACES_DIR/{project_id}/_ct8114/",
    ),
) -> JSONResponse:
    root = _resolve_project_path(project_id)

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

    request_id = str(uuid.uuid4())
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    workdir = TASKS_DIR / f"ct8114_{request_id}"
    workdir.mkdir(parents=True, exist_ok=False)

    try:
        fixes_path = workdir / "fixes.yaml"
        # 收集所有含头文件的目录作为 -I, 让 clang-tidy 能跨子目录解析 #include
        include_dirs = sorted({str(p.parent) for p in code_files})

        cmd = [
            CLANG_TIDY_BIN,
            *[str(p) for p in target_files],
            f"-checks={CHECKS}",
            f"-load={GJB_PLUGIN_PATH}",
            f"-export-fixes={fixes_path}",
            "--",
        ] + [f"-I{inc}" for inc in include_dirs]

        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(root),
                timeout=PROJECT_ANALYZE_TIMEOUT,
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"未找到 clang-tidy 可执行文件: {CLANG_TIDY_BIN}",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(status_code=504, detail="clang-tidy 执行超时") from exc

        if fixes_path.exists() and fixes_path.stat().st_size > 0:
            report = parse_fixes_file(fixes_path).to_dict()
        else:
            report = {
                "main_source_file": "",
                "diagnostics": [],
                "summary": {"total": 0, "by_check": {}, "by_level": {}},
            }

        payload = {
            "request_id": request_id,
            "project_id": project_id,
            "workdir": str(workdir) if keep else None,
            "command": {
                "returncode": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            },
            "report": report,
        }

        if save_report:
            try:
                # 写到 LOCAL_WORKSPACES_DIR/_reports/{project_id}/ 而不是顶层
                # {project_id}/_ct8114/. 顶层 _reports/ 以 _ 开头, 被 list_projects
                # 的扫描逻辑跳过, 不会再被错误识别为 "项目"; 也不会形成空壳目录
                # 遮挡 _resolve_project_path 对共享卷的查找.
                out_dir = LOCAL_WORKSPACES_DIR / "_reports" / project_id
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / "last_report.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                payload["saved_report"] = str(out_dir / "last_report.json")
            except OSError as e:
                # 落盘失败不影响主流程 (例如只读卷被错挂)
                payload["save_report_error"] = str(e)

        return JSONResponse(payload)
    finally:
        if not keep:
            shutil.rmtree(workdir, ignore_errors=True)


@app.delete("/projects/{project_id}")
def delete_project(project_id: str) -> JSONResponse:
    """只允许删除本工具私有卷里的项目. UniPortal 项目应回到 UniPortal 删."""

    pid = _safe_project_id(project_id)
    local = LOCAL_WORKSPACES_DIR / pid
    if local.is_dir():
        shutil.rmtree(local, ignore_errors=True)
        return JSONResponse({"deleted": True, "project_id": pid, "source": "local"})
    if UNIPORTAL_MODE and pid in _build_item_index():
        raise HTTPException(
            status_code=403,
            detail="UniPortal 来源的项目请到 UniPortal 删除",
        )
    raise HTTPException(status_code=404, detail=f"项目 {pid!r} 未找到")


@app.get("/healthz")
def healthz() -> dict:
    return {
        "status": "ok",
        "uniportal_mode": UNIPORTAL_MODE,
        "uniportal_storage_path": UNIPORTAL_STORAGE_PATH or None,
        "local_workspaces_dir": str(LOCAL_WORKSPACES_DIR),
        "tasks_dir": str(TASKS_DIR),
    }
