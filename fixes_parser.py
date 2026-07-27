"""解析 clang-tidy `-export-fixes` 生成的 YAML 文件。

主要能力:
1. 加载 YAML 文件并标准化为 Python dict / dataclass;
2. 将 ``FileOffset`` (字节偏移) 转换为人类可读的 ``line:column``;
3. 在转换坐标时附带源文件中的代码片段, 方便前端展示;
4. 提供 ``parse_fixes_file`` / ``parse_fixes_text`` 两个对外接口.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


@dataclass
class SourceLocation:
    file_path: str
    file_offset: int
    line: Optional[int] = None
    column: Optional[int] = None
    length: Optional[int] = None
    snippet: Optional[str] = None


@dataclass
class Replacement:
    file_path: str
    offset: int
    length: int
    replacement_text: str
    line: Optional[int] = None
    column: Optional[int] = None


@dataclass
class Note:
    message: str
    location: SourceLocation
    replacements: List[Replacement] = field(default_factory=list)


@dataclass
class Diagnostic:
    name: str
    level: str
    message: str
    build_directory: str
    location: SourceLocation
    ranges: List[SourceLocation] = field(default_factory=list)
    replacements: List[Replacement] = field(default_factory=list)
    notes: List[Note] = field(default_factory=list)


@dataclass
class FixesReport:
    main_source_file: str
    diagnostics: List[Diagnostic] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "main_source_file": self.main_source_file,
            "diagnostics": [asdict(d) for d in self.diagnostics],
            "summary": self.summary(),
        }

    def summary(self) -> Dict[str, Any]:
        by_check: Dict[str, int] = {}
        by_level: Dict[str, int] = {}
        for diag in self.diagnostics:
            by_check[diag.name] = by_check.get(diag.name, 0) + 1
            by_level[diag.level] = by_level.get(diag.level, 0) + 1
        return {
            "total": len(self.diagnostics),
            "by_check": by_check,
            "by_level": by_level,
        }


class _SourceIndex:
    """根据源文件字节内容构建偏移 -> (line, column) 的映射.

    clang-tidy 的 FileOffset 是 **字节** 偏移, 因此必须以二进制方式读取
    文件再编码; 否则在含有多字节字符 (如本项目的中文注释) 的源文件中,
    line/column 的换算会出错.
    """

    def __init__(self, raw_bytes: bytes) -> None:
        self._raw = raw_bytes
        # 记录每一行起始的字节偏移, 利用 bisect 做 O(log n) 查询
        self._line_starts: List[int] = [0]
        for idx, byte in enumerate(raw_bytes):
            if byte == 0x0A:  # '\n'
                self._line_starts.append(idx + 1)

    def locate(self, offset: int) -> tuple[int, int]:
        if offset < 0:
            offset = 0
        if offset > len(self._raw):
            offset = len(self._raw)
        # bisect_right - 1 即为不大于 offset 的最近一行起始
        line_idx = bisect.bisect_right(self._line_starts, offset) - 1
        line_start = self._line_starts[line_idx]
        column_bytes = self._raw[line_start:offset]
        try:
            column = len(column_bytes.decode("utf-8", errors="replace"))
        except Exception:
            column = offset - line_start
        return line_idx + 1, column + 1

    def snippet(self, offset: int, length: Optional[int] = None) -> str:
        if length is None or length <= 0:
            length = 0
        # 取所在行的全行作为 snippet, 便于前端展示
        line_idx = bisect.bisect_right(self._line_starts, offset) - 1
        line_start = self._line_starts[line_idx]
        if line_idx + 1 < len(self._line_starts):
            line_end = self._line_starts[line_idx + 1] - 1
        else:
            line_end = len(self._raw)
        return self._raw[line_start:line_end].decode("utf-8", errors="replace").rstrip("\r")


class _SourceCache:
    """避免对同一源文件重复读取与构建索引."""

    def __init__(self, path_remap: Optional[Dict[str, str]] = None) -> None:
        self._cache: Dict[str, Optional[_SourceIndex]] = {}
        self._path_remap = path_remap or {}

    def _resolve(self, path: str) -> Optional[Path]:
        if path in self._path_remap:
            candidate = Path(self._path_remap[path])
            if candidate.exists():
                return candidate
        p = Path(path)
        if p.exists():
            return p
        # 尝试以文件名在 remap 的根目录下寻找
        for src, dst in self._path_remap.items():
            base = Path(dst)
            if base.is_dir():
                candidate = base / Path(path).name
                if candidate.exists():
                    return candidate
        return None

    def get(self, path: str) -> Optional[_SourceIndex]:
        if path in self._cache:
            return self._cache[path]
        resolved = self._resolve(path)
        if resolved is None:
            self._cache[path] = None
            return None
        try:
            raw = resolved.read_bytes()
        except OSError:
            self._cache[path] = None
            return None
        index = _SourceIndex(raw)
        self._cache[path] = index
        return index


def _build_location(
    raw: Dict[str, Any],
    cache: _SourceCache,
    length: Optional[int] = None,
) -> SourceLocation:
    file_path = raw.get("FilePath", "")
    offset = int(raw.get("FileOffset", 0))
    loc = SourceLocation(file_path=file_path, file_offset=offset, length=length)
    index = cache.get(file_path)
    if index is not None:
        loc.line, loc.column = index.locate(offset)
        loc.snippet = index.snippet(offset, length)
    return loc


def _build_replacement(raw: Dict[str, Any], cache: _SourceCache) -> Replacement:
    file_path = raw.get("FilePath", "")
    offset = int(raw.get("Offset", 0))
    length = int(raw.get("Length", 0))
    text = raw.get("ReplacementText", "")
    rep = Replacement(
        file_path=file_path,
        offset=offset,
        length=length,
        replacement_text=text,
    )
    index = cache.get(file_path)
    if index is not None:
        rep.line, rep.column = index.locate(offset)
    return rep


def _build_diagnostic(raw: Dict[str, Any], cache: _SourceCache) -> Diagnostic:
    message_block = raw.get("DiagnosticMessage", {}) or {}
    length: Optional[int] = None
    ranges_raw = message_block.get("Ranges") or []
    # 优先用第一个 Range 的长度来截取片段, 方便高亮
    if ranges_raw:
        try:
            length = int(ranges_raw[0].get("Length", 0)) or None
        except (TypeError, ValueError):
            length = None

    location = _build_location(message_block, cache, length=length)
    replacements = [
        _build_replacement(r, cache) for r in (message_block.get("Replacements") or [])
    ]
    ranges = [
        _build_location(r, cache, length=int(r.get("Length", 0)) or None)
        for r in ranges_raw
    ]

    notes: List[Note] = []
    for note_raw in raw.get("Notes") or []:
        note_loc = _build_location(note_raw, cache)
        note_replacements = [
            _build_replacement(r, cache) for r in (note_raw.get("Replacements") or [])
        ]
        notes.append(
            Note(
                message=note_raw.get("Message", ""),
                location=note_loc,
                replacements=note_replacements,
            )
        )

    return Diagnostic(
        name=raw.get("DiagnosticName", ""),
        level=raw.get("Level", ""),
        message=message_block.get("Message", ""),
        build_directory=raw.get("BuildDirectory", ""),
        location=location,
        ranges=ranges,
        replacements=replacements,
        notes=notes,
    )


def parse_fixes_text(
    yaml_text: str,
    path_remap: Optional[Dict[str, str]] = None,
) -> FixesReport:
    """解析 YAML 文本内容并返回 ``FixesReport``.

    :param yaml_text: clang-tidy 导出的 YAML 内容
    :param path_remap: ``{yaml 中记录的路径: 本地实际路径}`` 的映射.
        clang-tidy 在容器/沙箱中运行时, YAML 中的 ``FilePath`` 通常是容器内
        的绝对路径 (例如 ``/tc8114/test.c``), 需要把它映射回本地路径才能
        读取源文件以补全 line/column/snippet.
    """

    data = yaml.safe_load(yaml_text) or {}
    cache = _SourceCache(path_remap=path_remap)

    diagnostics_raw = data.get("Diagnostics") or []
    diagnostics = [_build_diagnostic(d, cache) for d in diagnostics_raw]
    return FixesReport(
        main_source_file=data.get("MainSourceFile", ""),
        diagnostics=diagnostics,
    )


def parse_fixes_file(
    yaml_path: str | Path,
    path_remap: Optional[Dict[str, str]] = None,
) -> FixesReport:
    """从磁盘路径读取并解析 YAML."""

    text = Path(yaml_path).read_text(encoding="utf-8", errors="replace")
    return parse_fixes_text(text, path_remap=path_remap)


if __name__ == "__main__":
    import json
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "fixes.yaml"
    remap: Dict[str, str] = {}
    if len(sys.argv) > 2:
        # 命令行格式: python fixes_parser.py fixes.yaml /tc8114=.
        for item in sys.argv[2:]:
            if "=" in item:
                k, v = item.split("=", 1)
                remap[k] = v
    report = parse_fixes_file(target, path_remap=remap)
    json.dump(report.to_dict(), sys.stdout, ensure_ascii=False, indent=2)
