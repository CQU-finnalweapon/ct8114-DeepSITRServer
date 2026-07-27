from pathlib import Path
from typing import Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from source_utils import (
    build_report_indexes,
    load_report_files_stats,
    match_report_for_path,
    read_json_file,
    read_source_text,
    resolve_source_file,
    scan_source_files,
)


def _resolve_source_context(
    project_id: str,
    portal_project_id: str,
    uniportal_project_roots: Callable[[str], Dict[str, Path]],
    safe_project_id: Callable[[str], str],
    ct8114_output_dir: Callable[[Path], Path],
) -> dict:
    pid = safe_project_id(project_id)
    portal_pid = safe_project_id(portal_project_id)
    project_root = uniportal_project_roots(portal_pid).get(pid)
    if project_root is None or not project_root.is_dir():
        raise HTTPException(
            status_code=404,
            detail=f"project {pid!r} not found under UniPortal project {portal_pid!r}",
        )

    ct8114_dir = ct8114_output_dir(project_root)
    meta_path = ct8114_dir / "meta.json"
    if not meta_path.is_file():
        raise HTTPException(status_code=404, detail=f"meta.json not found: {meta_path}")

    try:
        meta = read_json_file(meta_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to read meta.json: {exc}") from exc

    raw_source_root = str(meta.get("dcab_source_root") or "").strip()
    if not raw_source_root:
        raise HTTPException(status_code=404, detail="dcab_source_root is missing in meta.json")

    source_root = Path(raw_source_root).resolve()
    if not source_root.is_dir():
        raise HTTPException(status_code=404, detail=f"dcab_source_root is not a directory: {source_root}")

    return {
        "project_id": pid,
        "portal_project_id": portal_pid,
        "source_root": source_root,
        "last_report_path": ct8114_dir / "last_report.json",
    }


def _load_report_indexes(last_report_path: Path, source_root: Path) -> tuple[Dict[str, dict], Dict[str, List[dict]]]:
    return build_report_indexes(load_report_files_stats(last_report_path), source_root)


def _count_report_items(report_item: dict, count_key: str, list_key: str) -> int:
    value = report_item.get(count_key)
    if isinstance(value, int):
        return value
    items = report_item.get(list_key)
    return len(items) if isinstance(items, list) else 0


def _count_report_list(report_item: dict, list_key: str) -> int:
    items = report_item.get(list_key)
    return len(items) if isinstance(items, list) else 0


def create_source_router(
    uniportal_project_roots: Callable[[str], Dict[str, Path]],
    safe_project_id: Callable[[str], str],
    ct8114_output_dir: Callable[[Path], Path],
) -> APIRouter:
    router = APIRouter()

    @router.get("/projects/{project_id}/source-files")
    def get_project_source_files(
        project_id: str,
        portal_project_id: str = Query(..., description="UniPortal project id"),
    ) -> JSONResponse:
        context = _resolve_source_context(
            project_id,
            portal_project_id,
            uniportal_project_roots,
            safe_project_id,
            ct8114_output_dir,
        )
        source_root: Path = context["source_root"]
        by_path, by_name = _load_report_indexes(context["last_report_path"], source_root)

        files = []
        for item in scan_source_files(source_root):
            report_item, match_error = match_report_for_path(item["file_path"], by_path, by_name)
            payload = {
                **item,
                "has_report": report_item is not None,
                "bug_count": _count_report_items(report_item, "bug_count", "bugs") if report_item else 0,
                "function_count": _count_report_list(report_item, "functions") if report_item else 0,
            }
            if match_error:
                payload["report_match_error"] = match_error
            files.append(payload)

        return JSONResponse(
            {
                "project_id": context["project_id"],
                "portal_project_id": context["portal_project_id"],
                "source_root": str(source_root),
                "files": files,
            }
        )

    @router.get("/projects/{project_id}/source")
    def get_project_source(
        project_id: str,
        portal_project_id: str = Query(..., description="UniPortal project id"),
        file_path: str = Query(..., description="source file path relative to dcab_source_root"),
        line: Optional[int] = Query(None, ge=1),
        column: Optional[int] = Query(None, ge=1),
    ) -> JSONResponse:
        context = _resolve_source_context(
            project_id,
            portal_project_id,
            uniportal_project_roots,
            safe_project_id,
            ct8114_output_dir,
        )
        source_root: Path = context["source_root"]

        try:
            source_file, returned_rel_path = resolve_source_file(source_root, file_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if not source_file.is_file():
            raise HTTPException(status_code=404, detail=f"source file not found: {returned_rel_path}")

        try:
            text, encoding = read_source_text(source_file)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"failed to read source file: {exc}") from exc

        by_path, by_name = _load_report_indexes(context["last_report_path"], source_root)
        report_item, match_error = match_report_for_path(returned_rel_path, by_path, by_name)
        bugs = report_item.get("bugs") if isinstance(report_item, dict) and isinstance(report_item.get("bugs"), list) else []
        functions = (
            report_item.get("functions")
            if isinstance(report_item, dict) and isinstance(report_item.get("functions"), list)
            else []
        )
        source_lines = text.splitlines()
        payload = {
            "project_id": context["project_id"],
            "portal_project_id": context["portal_project_id"],
            "file_path": returned_rel_path,
            "source_root": str(source_root),
            "absolute_path": str(source_file),
            "encoding": encoding,
            "line_count": len(source_lines),
            "lines": [
                {"line": index, "text": value}
                for index, value in enumerate(source_lines, start=1)
            ],
            "bugs": bugs,
            "functions": functions,
            "target": {
                "line": line if line is not None else 1,
                "column": column if column is not None else 1,
            },
        }
        if match_error:
            payload["report_match_error"] = match_error
        return JSONResponse(payload)

    return router


def register_source_routes(
    app,
    uniportal_project_roots: Callable[[str], Dict[str, Path]],
    safe_project_id: Callable[[str], str],
    ct8114_output_dir: Callable[[Path], Path],
) -> None:
    if getattr(app.state, "source_routes_registered", False):
        return
    app.include_router(
        create_source_router(
            uniportal_project_roots=uniportal_project_roots,
            safe_project_id=safe_project_id,
            ct8114_output_dir=ct8114_output_dir,
        )
    )
    app.state.source_routes_registered = True
