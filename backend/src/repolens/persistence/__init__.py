"""Explicit save/load of validated semantic graphs in a target repository."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import os
import tempfile

from repolens.contracts import validate_semantic_graph
from repolens.errors import RepoLensError
from repolens.indexing import RepositoryIndex, _linked


ARCHITECTURE_RELATIVE = Path(".repolens") / "architecture.json"
FORMAT_VERSION = "2"
MAX_ARCHITECTURE_BYTES = 4_000_000


def architecture_path(root: str | Path) -> Path:
    root = Path(root).resolve()
    path = root / ARCHITECTURE_RELATIVE
    for candidate in (path.parent, path):
        if candidate.exists() or candidate.is_symlink():
            if _linked(candidate):
                raise RepoLensError("架构保存路径不能是符号链接或目录联接。")
            candidate.resolve().relative_to(root)
    return path


def _draft_from_graph(graph: dict) -> dict:
    if (not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list) or
            any(not isinstance(item, dict) for item in graph["nodes"] + graph["edges"])):
        raise RepoLensError("架构图节点或关系格式损坏。")
    modules = []
    for node in graph.get("nodes", ()):
        anchors = node.get("anchors", ())
        if not isinstance(anchors, list) or any(not isinstance(anchor, dict) for anchor in anchors):
            raise RepoLensError("架构图源码锚点格式损坏。")
        modules.append({
            "module_key": node.get("module_key", node.get("id", "")),
            "label": node.get("name", ""),
            "kind": node.get("kind", "module"),
            "summary": node.get("summary", ""),
            "group": node.get("group", ""),
            "anchor_ids": [anchor.get("symbol_id", anchor.get("file_id", "")) for anchor in anchors],
            "evidence_refs": node.get("evidence_refs", []),
            "confidence": node.get("confidence", 0),
            "render_spec": node.get("render_spec"),
        })
    modules_by_id = {node.get("id"): node.get("module_key", node.get("id", "")) for node in graph.get("nodes", ())}
    relations = []
    for edge in graph.get("edges", ()):
        relations.append({
            "source": modules_by_id.get(edge.get("source"), edge.get("source", "")),
            "target": modules_by_id.get(edge.get("target"), edge.get("target", "")),
            "type": edge.get("type", ""), "label": edge.get("label", ""),
            "confidence": edge.get("confidence", 0),
            "evidence_refs": edge.get("evidence_refs", []),
        })
    return {"project_type": graph.get("project_type", ""), "summary": graph.get("summary", ""),
            "modules": modules, "relations": relations,
            "incomplete": graph.get("incomplete"),
            "incomplete_regions": list(graph.get("incomplete_regions", ())),}


def _persisted_graph(payload: dict, index: RepositoryIndex) -> dict:
    if not isinstance(payload, dict) or payload.get("format_version") != FORMAT_VERSION:
        raise RepoLensError("架构文件版本不受支持。")
    if payload.get("snapshot") != index.snapshot:
        raise RepoLensError("架构文件对应旧索引，需要重新生成。", 409)
    graph = payload.get("graph")
    if not isinstance(graph, dict) or graph.get("snapshot") != index.snapshot or graph.get("schema_version") != "0.2":
        raise RepoLensError("架构文件缺少图数据。")
    return validate_semantic_graph(_draft_from_graph(graph), index, graph.get("evidence"))


def encode_graph(index: RepositoryIndex, graph: dict, *, model: str = "unknown",
                 goal: str = "", usage: dict | None = None) -> bytes:
    """Return a bounded, source-free file for either server or browser writes."""
    if not isinstance(model, str) or len(model) > 100:
        raise RepoLensError("模型名称无效。")
    index.assert_current()
    if graph.get("mode") not in {"deepseek-agent", "saved-agent"}:
        raise RepoLensError("只有通过校验的语义架构图可以保存。")
    validated = _persisted_graph({"format_version": FORMAT_VERSION, "snapshot": index.snapshot, "graph": graph}, index)
    payload = {"format_version": FORMAT_VERSION, "schema_version": "0.2", "snapshot": index.snapshot,
               "saved_at": datetime.now(timezone.utc).isoformat(), "model": model[:100],
               "goal": goal[:1000] if isinstance(goal, str) else "",
               "usage": {key: value for key, value in (usage or {}).items()
                         if key in {"requests", "tool_calls", "total_tokens", "estimated", "budget", "elapsed_seconds"}},
               "graph": validated}
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(encoded) > MAX_ARCHITECTURE_BYTES:
        raise RepoLensError("架构文件超过 4 MB 上限。")
    return encoded


def decode_graph(index: RepositoryIndex, data: bytes) -> dict:
    if len(data) > MAX_ARCHITECTURE_BYTES:
        raise RepoLensError("架构文件超过 4 MB 上限。")
    try:
        payload = json.loads(data.decode("utf-8-sig"))
        graph = _persisted_graph(payload, index)
    except (UnicodeError, ValueError, TypeError, KeyError):
        raise RepoLensError("架构文件格式损坏，请重新生成。") from None
    graph["mode"] = "saved-agent"
    graph["saved_at"] = payload.get("saved_at")
    return graph


def load_graph(index: RepositoryIndex) -> tuple[dict | None, str | None]:
    """Load only a current, schema-valid graph; stale files are ignored safely."""
    try:
        path = architecture_path(index.root)
        if not path.is_file():
            return None, None
        with path.open("rb") as stream:
            data = stream.read(MAX_ARCHITECTURE_BYTES + 1)
        return decode_graph(index, data), None
    except RepoLensError as exc:
        return None, str(exc)
    except (OSError, ValueError):
        return None, "无法读取架构文件，请检查文件权限。"


def save_graph(index: RepositoryIndex, graph: dict, *, model: str | None = None,
               goal: str = "", usage: dict | None = None) -> Path:
    if graph.get("schema_version") != "0.2" or graph.get("mode") not in {"deepseek-agent", "saved-agent"}:
        raise RepoLensError("只有通过语义图校验的 Agent 结果可以保存。")
    encoded = encode_graph(index, graph, model=model or "unknown", goal=goal, usage=usage)
    path = architecture_path(index.root)
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix="architecture.", suffix=".tmp", dir=parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, path)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    except OSError as exc:
        raise RepoLensError(f"无法保存架构图：{type(exc).__name__}。", 500) from None
    return path
