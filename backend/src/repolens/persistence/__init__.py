"""Explicit save/load of validated semantic graphs in a target repository."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import os
import tempfile

from repolens.contracts import validate_semantic_graph
from repolens.errors import RepoLensError
from repolens.indexing import RepositoryIndex


ARCHITECTURE_RELATIVE = Path(".repolens") / "architecture.json"
FORMAT_VERSION = "1"


def architecture_path(root: str | Path) -> Path:
    return Path(root).resolve() / ARCHITECTURE_RELATIVE


def _draft_from_graph(graph: dict) -> dict:
    modules = []
    for node in graph.get("nodes", ()):
        anchors = node.get("anchors", ())
        modules.append({
            "module_key": node.get("module_key", node.get("id", "")),
            "label": node.get("name", ""),
            "kind": node.get("kind", "module"),
            "summary": node.get("summary", ""),
            "group": node.get("group", ""),
            "anchor_ids": [anchor.get("symbol_id", anchor.get("file_id", "")) for anchor in anchors],
            "evidence_refs": node.get("evidence_refs", ["ev_persisted"]),
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
            "evidence_refs": edge.get("evidence_refs", ["ev_persisted"]),
        })
    return {"project_type": graph.get("project_type", ""), "summary": graph.get("summary", ""),
            "modules": modules, "relations": relations,
            "incomplete": bool(graph.get("incomplete")),
            "incomplete_regions": list(graph.get("incomplete_regions", ())),}


def _persisted_graph(payload: dict, index: RepositoryIndex) -> dict:
    if not isinstance(payload, dict) or payload.get("format_version") != FORMAT_VERSION:
        raise RepoLensError("架构文件版本不受支持。")
    if payload.get("snapshot") != index.snapshot:
        raise RepoLensError("架构文件对应旧索引，需要重新生成。", 409)
    graph = payload.get("graph")
    if not isinstance(graph, dict):
        raise RepoLensError("架构文件缺少图数据。")
    return validate_semantic_graph(_draft_from_graph(graph), index)


def load_graph(index: RepositoryIndex) -> tuple[dict | None, str | None]:
    """Load only a current, schema-valid graph; stale files are ignored safely."""
    path = architecture_path(index.root)
    if not path.is_file():
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        graph = _persisted_graph(payload, index)
        graph["mode"] = "saved-agent"
        graph["saved_at"] = payload.get("saved_at")
        return graph, None
    except (OSError, UnicodeError, json.JSONDecodeError, RepoLensError) as exc:
        return None, str(exc)


def save_graph(index: RepositoryIndex, graph: dict, *, model: str | None = None,
               goal: str = "", usage: dict | None = None) -> Path:
    if graph.get("schema_version") != "0.2" or graph.get("mode") not in {"deepseek-agent", "saved-agent"}:
        raise RepoLensError("只有通过语义图校验的 Agent 结果可以保存。")
    # Revalidate before writing, without trusting browser-provided graph fields.
    validated = _persisted_graph({"format_version": FORMAT_VERSION, "snapshot": index.snapshot, "graph": graph}, index)
    path = architecture_path(index.root)
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format_version": FORMAT_VERSION,
            "schema_version": "0.2",
            "snapshot": index.snapshot,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "model": model or "unknown",
            "goal": goal[:1000] if isinstance(goal, str) else "",
            "usage": {key: value for key, value in (usage or {}).items()
                      if key in {"requests", "tool_calls", "total_tokens", "estimated", "budget"}},
            "graph": validated,
        }
        fd, temporary_name = tempfile.mkstemp(prefix="architecture.", suffix=".tmp", dir=parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
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
