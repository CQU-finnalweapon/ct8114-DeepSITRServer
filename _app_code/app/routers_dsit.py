"""DeepSITRServer 集成 API 路由。

提供上传 DeepSITRServer 输出目录、查看报告、浏览历史等功能。

端点：
    POST   /dsit/upload        — 上传输出目录 zip 或从本地路径加载
    POST   /dsit/upload-local  — 直接从本地路径加载分析结果
    GET    /dsit/reports       — 列出所有已加载的报告
    GET    /dsit/report/{id}   — 获取完整报告
    GET    /dsit/report/{id}/summary  — 获取报告摘要
    DELETE /dsit/report/{id}   — 删除报告
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from dsit_parser import parse_output_dir

router = APIRouter(prefix="/dsit", tags=["dsit"])

# 存储目录
DSIT_REPORTS_DIR = Path(os.environ.get("DSIT_REPORTS_DIR", "workspaces/_dsit_reports"))
DSIT_REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _report_dir(report_id: str) -> Path:
    return DSIT_REPORTS_DIR / report_id


def _safe_report_id(report_id: str) -> str:
    rid = (report_id or "").strip()
    if not rid or "/" in rid or "\\" in rid or rid in {".", ".."}:
        raise HTTPException(status_code=400, detail=f"非法 report_id: {report_id!r}")
    return rid


# ============================================================================
# API 端点
# ============================================================================

@router.post("/upload")
async def upload_dsit_output(
    file: UploadFile = File(..., description="DeepSITRServer 输出目录的 .zip 压缩包"),
    report_name: str = Form("", description="报告名称（可选）"),
) -> JSONResponse:
    """上传 DeepSITRServer 输出目录的 zip，解析并返回报告。"""
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="请上传 .zip 文件")

    report_id = f"dsit_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    workdir = _report_dir(report_id)
    workdir.mkdir(parents=True, exist_ok=True)

    # 解压
    zip_path = workdir / "upload.zip"
    extract_dir = workdir / "output"
    try:
        content = await file.read()
        zip_path.write_bytes(content)

        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
    except zipfile.BadZipFile:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="无效的 zip 文件")
    finally:
        if zip_path.exists():
            zip_path.unlink()

    # 解析输出目录
    name = report_name or file.filename.replace(".zip", "")
    report = parse_output_dir(extract_dir, report_id=report_id)
    report.project_name = name or report.project_name

    # 保存报告 JSON
    report_json = report.to_dict()
    (workdir / "report.json").write_text(
        json.dumps(report_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return JSONResponse({
        "status": "ok",
        "report_id": report_id,
        "report_name": report.project_name,
        "summary": report.summary(),
    })


@router.post("/upload-local")
async def load_dsit_local(
    local_path: str = Form(..., description="DeepSITRServer 输出目录的本地绝对路径"),
    report_name: str = Form("", description="报告名称（可选）"),
) -> JSONResponse:
    """直接从本地路径加载 DeepSITRServer 输出目录（用于开发/测试）。"""
    src = Path(local_path)
    if not src.is_dir():
        raise HTTPException(status_code=400, detail=f"目录不存在: {local_path!r}")

    report_id = f"dsit_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    workdir = _report_dir(report_id)
    workdir.mkdir(parents=True, exist_ok=True)

    # 解析
    name = report_name or src.name
    report = parse_output_dir(src, report_id=report_id)
    report.project_name = name or report.project_name

    # 保存报告
    report_json = report.to_dict()
    (workdir / "report.json").write_text(
        json.dumps(report_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # 记录来源路径
    (workdir / "source.txt").write_text(str(src), encoding="utf-8")

    return JSONResponse({
        "status": "ok",
        "report_id": report_id,
        "report_name": report.project_name,
        "source_path": str(src),
        "summary": report.summary(),
    })


@router.get("/reports")
def list_reports() -> JSONResponse:
    """列出所有已加载的 DSIT 报告."""
    items = []
    if DSIT_REPORTS_DIR.is_dir():
        for sub in sorted(DSIT_REPORTS_DIR.iterdir(), reverse=True):
            if not sub.is_dir():
                continue
            report_file = sub / "report.json"
            if not report_file.exists():
                continue
            try:
                data = json.loads(report_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            items.append({
                "report_id": data.get("report_id", sub.name),
                "report_name": data.get("project_name", sub.name),
                "project_path": data.get("project_path", ""),
                "total_files": data.get("summary", {}).get("total_files", 0),
                "total_bugs": data.get("summary", {}).get("total_bugs", 0),
                "by_level": data.get("summary", {}).get("by_level", {}),
            })
    return JSONResponse({"reports": items})


@router.get("/report/{report_id}")
def get_report(report_id: str) -> JSONResponse:
    """获取完整报告."""
    rid = _safe_report_id(report_id)
    report_file = _report_dir(rid) / "report.json"
    if not report_file.exists():
        raise HTTPException(status_code=404, detail=f"报告 {rid!r} 未找到")
    return JSONResponse(json.loads(report_file.read_text(encoding="utf-8")))


@router.get("/report/{report_id}/summary")
def get_report_summary(report_id: str) -> JSONResponse:
    """只返回报告摘要."""
    rid = _safe_report_id(report_id)
    report_file = _report_dir(rid) / "report.json"
    if not report_file.exists():
        raise HTTPException(status_code=404, detail=f"报告 {rid!r} 未找到")
    data = json.loads(report_file.read_text(encoding="utf-8"))
    return JSONResponse(data.get("summary", {}))


@router.delete("/report/{report_id}")
def delete_report(report_id: str) -> JSONResponse:
    """删除报告."""
    rid = _safe_report_id(report_id)
    d = _report_dir(rid)
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
        return JSONResponse({"deleted": True, "report_id": rid})
    raise HTTPException(status_code=404, detail=f"报告 {rid!r} 未找到")
