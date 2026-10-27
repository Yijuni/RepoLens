"""Read-only tool registry for bounded Agent exploration."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Callable

from repolens.errors import RepoLensError
from repolens.indexing import RepositoryIndex


MAX_TOOL_OUTPUT_CHARS = 16_000
MAX_SEARCH_RESULTS = 100


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], dict]
    category: str = "exploration"
    read_only: bool = True
    max_output_chars: int = MAX_TOOL_OUTPUT_CHARS


class ToolRegistry:
    """Explicit registry; target repositories cannot register tools implicitly."""

    def __init__(self, index: RepositoryIndex):
        self.index = index
        self._tools: dict[str, ToolSpec] = {}
        self.read_limit = 200

    def set_read_limit(self, limit: int) -> None:
        """Apply the active Agent read budget to the built-in read tools."""
        if type(limit) is not int or not 20 <= limit <= 1_000:
            raise RepoLensError("工具读取行数上限无效。")
        self.read_limit = limit

    def register(self, spec: ToolSpec) -> None:
        if not isinstance(spec, ToolSpec) or not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", spec.name):
            raise RepoLensError("工具名称无效。")
        if not spec.read_only:
            raise RepoLensError("当前只允许注册只读工具。")
        if spec.name in self._tools:
            raise RepoLensError(f"工具已注册：{spec.name}")
        self._tools[spec.name] = spec

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def schemas(self) -> list[dict]:
        return [{"name": spec.name, "description": spec.description,
                 "category": spec.category, "read_only": spec.read_only,
                 "input_schema": spec.input_schema} for spec in self._tools.values()]

    def invoke(self, name: str, arguments: dict | None = None) -> dict:
        spec = self._tools.get(name)
        if spec is None:
            raise RepoLensError(f"未注册的工具：{name}", 404)
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise RepoLensError("工具参数必须是 JSON 对象。")
        try:
            result = spec.handler(arguments)
        except RepoLensError:
            raise
        except Exception as exc:
            raise RepoLensError(f"工具 {name} 执行失败：{type(exc).__name__}", 500) from None
        if not isinstance(result, dict):
            raise RepoLensError(f"工具 {name} 返回格式无效。", 500)
        evidence_id = "ev_" + hashlib.sha256((self.index.snapshot + "|" + name + "|" +
                                               json.dumps(arguments, ensure_ascii=False, sort_keys=True) + "|" +
                                               json.dumps(result, ensure_ascii=False, sort_keys=True)).encode("utf-8")).hexdigest()[:20]
        bounded = json.loads(json.dumps(result, ensure_ascii=False))
        serialized = json.dumps(bounded, ensure_ascii=False)
        truncated = len(serialized) > spec.max_output_chars
        if truncated:
            bounded = {"truncated": True, "preview": serialized[:spec.max_output_chars]}
        return {"tool": name, "evidence": {"evidence_id": evidence_id, "tool": name,
                "snapshot": self.index.snapshot, "truncated": truncated}, "result": bounded}


def _text(args: dict, name: str, default: str = "", limit: int = 240) -> str:
    value = args.get(name, default)
    if not isinstance(value, str) or len(value) > limit:
        raise RepoLensError(f"工具参数 {name} 无效。")
    return value.strip()


def _integer(args: dict, name: str, default: int, minimum: int, maximum: int) -> int:
    value = args.get(name, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise RepoLensError(f"工具参数 {name} 必须在 {minimum} 到 {maximum} 之间。")
    return value


def _file(index: RepositoryIndex, file_id: str):
    file = index.documents.get(file_id)
    if file is None:
        raise RepoLensError("文件 ID 不在当前索引中。", 404)
    return file


def _source_lines(index: RepositoryIndex, file_id: str) -> tuple[dict, list[str]]:
    source = index.source(file_id)
    return source, source["content"].splitlines()


def build_default_registry(index: RepositoryIndex) -> ToolRegistry:
    registry = ToolRegistry(index)

    def list_files(args: dict) -> dict:
        prefix = _text(args, "prefix", limit=500).replace("\\", "/").strip("/")
        query = _text(args, "query", limit=160).casefold()
        offset = _integer(args, "offset", 0, 0, len(index.documents))
        limit = _integer(args, "limit", 80, 1, 200)
        items = []
        for file in sorted(index.documents.values(), key=lambda item: item.path):
            if prefix and not (file.path == prefix or file.path.startswith(prefix + "/")):
                continue
            if query and query not in file.path.casefold():
                continue
            items.append({"file_id": file.id, "path": file.path, "language": getattr(file, "language", "文本"),
                          "analyzed": file.id in index.files, "line_count": file.line_count})
        return {"items": items[offset:offset + limit], "offset": offset, "next_offset": offset + limit if offset + limit < len(items) else None,
                "total": len(items), "snapshot": index.snapshot}

    def find_symbols(args: dict) -> dict:
        query = _text(args, "query", limit=160).casefold()
        language = _text(args, "language", limit=40).casefold()
        limit = _integer(args, "limit", 80, 1, 200)
        items = []
        for file in index.files.values():
            if language and file.language.casefold() != language:
                continue
            for symbol in file.definitions:
                if query and query not in symbol.qualified_name.casefold():
                    continue
                items.append({"symbol_id": symbol.id, "file_id": file.id, "path": file.path,
                              "language": file.language, "name": symbol.qualified_name,
                              "kind": symbol.kind, "start_line": symbol.start_line, "end_line": symbol.end_line})
                if len(items) >= limit:
                    return {"items": items, "truncated": True, "snapshot": index.snapshot}
        return {"items": items, "truncated": False, "snapshot": index.snapshot}

    def read_range(args: dict) -> dict:
        file_id = _text(args, "file_id", limit=120)
        start = _integer(args, "start_line", 1, 1, 1_000_000)
        end = _integer(args, "end_line", start, start, 1_000_000)
        if end - start + 1 > registry.read_limit:
            raise RepoLensError(f"单次最多读取 {registry.read_limit} 行。")
        source, lines = _source_lines(index, file_id)
        if start > len(lines):
            raise RepoLensError("起始行超过文件范围。")
        end = min(end, len(lines))
        return {"file_id": source["id"], "path": source["path"], "language": source["language"],
                "start_line": start, "end_line": end, "content": "\n".join(lines[start - 1:end]),
                "fingerprint": index.documents[file_id].fingerprint, "truncated": end < int(args.get("end_line", end))}

    def read_symbol(args: dict) -> dict:
        symbol_id = _text(args, "symbol_id", limit=120)
        for file in index.files.values():
            for symbol in file.definitions:
                if symbol.id == symbol_id:
                    return read_range({"file_id": file.id, "start_line": symbol.start_line, "end_line": symbol.end_line}) | {
                        "symbol_id": symbol.id, "symbol_name": symbol.qualified_name, "kind": symbol.kind}
        raise RepoLensError("符号 ID 不在当前索引中。", 404)

    def search_text(args: dict) -> dict:
        query = _text(args, "query", limit=240)
        if not query:
            raise RepoLensError("search_text 需要非空 query。")
        case_sensitive = args.get("case_sensitive", False)
        if type(case_sensitive) is not bool:
            raise RepoLensError("case_sensitive 必须是布尔值。")
        limit = _integer(args, "limit", 40, 1, MAX_SEARCH_RESULTS)
        needle = query if case_sensitive else query.casefold()
        matches = []
        for file in sorted(index.documents.values(), key=lambda item: item.path):
            source, lines = _source_lines(index, file.id)
            for number, line in enumerate(lines, 1):
                haystack = line if case_sensitive else line.casefold()
                if needle in haystack:
                    matches.append({"file_id": file.id, "path": file.path, "line": number,
                                    "text": line[:240], "language": source["language"]})
                    if len(matches) >= limit:
                        return {"query": query, "matches": matches, "truncated": True, "snapshot": index.snapshot}
        return {"query": query, "matches": matches, "truncated": False, "snapshot": index.snapshot}

    def find_importers(args: dict) -> dict:
        file_id = _text(args, "file_id", limit=120)
        _file(index, file_id)
        paths = []
        for source_id, target_id in sorted(index.edges):
            if target_id == file_id:
                source = index.files[source_id]
                paths.append({"file_id": source.id, "path": source.path, "language": source.language})
        return {"file_id": file_id, "importers": paths, "snapshot": index.snapshot}

    def inspect_manifest(args: dict) -> dict:
        names = {"pyproject.toml", "package.json", "go.mod", "cargo.toml", "cmakelists.txt", "pom.xml", "build.gradle", "requirements.txt"}
        requested = _text(args, "path", limit=240).casefold()
        for file in index.documents.values():
            if file.path.casefold() == requested or (not requested and file.path.rsplit("/", 1)[-1].casefold() in names):
                source, lines = _source_lines(index, file.id)
                return {"file_id": file.id, "path": file.path, "language": source["language"],
                        "start_line": 1, "end_line": min(len(lines), 160), "content": "\n".join(lines[:160]),
                        "truncated": len(lines) > 160}
        return {"items": [], "message": "未找到请求的项目清单文件。", "snapshot": index.snapshot}

    def entry_candidates(_args: dict) -> dict:
        items = [{"file_id": file.id, "path": file.path, "language": file.language, "reason": "索引入口候选"}
                 for file in sorted(index.files.values(), key=lambda item: item.path) if file.entry]
        return {"items": items, "snapshot": index.snapshot}

    registry.register(ToolSpec("list_files", "分页列出已授权项目中的文件。", {"type": "object", "properties": {"prefix": {"type": "string"}, "query": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}}, "additionalProperties": False}, list_files))
    registry.register(ToolSpec("find_symbols", "按名称查询索引中的类、函数、方法和接口候选。", {"type": "object", "properties": {"query": {"type": "string"}, "language": {"type": "string"}, "limit": {"type": "integer"}}, "additionalProperties": False}, find_symbols))
    registry.register(ToolSpec("read_range", "读取已索引文件的有限行范围。", {"type": "object", "required": ["file_id", "start_line", "end_line"], "properties": {"file_id": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, "additionalProperties": False}, read_range, category="reading"))
    registry.register(ToolSpec("read_symbol", "读取已索引符号的定义范围。", {"type": "object", "required": ["symbol_id"], "properties": {"symbol_id": {"type": "string"}}, "additionalProperties": False}, read_symbol, category="reading"))
    registry.register(ToolSpec("search_text", "在受限文本源码中搜索线索并返回真实行号。", {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}, "case_sensitive": {"type": "boolean"}, "limit": {"type": "integer"}}, "additionalProperties": False}, search_text, category="evidence"))
    registry.register(ToolSpec("find_importers", "查找本地索引确认的静态反向依赖。", {"type": "object", "required": ["file_id"], "properties": {"file_id": {"type": "string"}}, "additionalProperties": False}, find_importers, category="relations"))
    registry.register(ToolSpec("inspect_manifest", "读取项目清单文件的受限内容和行号。", {"type": "object", "properties": {"path": {"type": "string"}}, "additionalProperties": False}, inspect_manifest, category="evidence"))
    registry.register(ToolSpec("entry_candidates", "列出静态索引标记的入口候选。", {"type": "object", "properties": {}, "additionalProperties": False}, entry_candidates))
    return registry
