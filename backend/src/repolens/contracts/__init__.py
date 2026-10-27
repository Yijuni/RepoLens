"""Dataclass-derived JSON schema and strict graph/evidence validation."""

from dataclasses import dataclass, fields, is_dataclass
import math
from typing import Literal, get_args, get_origin, get_type_hints

from repolens.errors import RepoLensError
from repolens.indexing import RepositoryIndex


@dataclass
class NodeDraft:
    id: str
    summary: str
    group: str


@dataclass
class EdgeDraft:
    source: str
    target: str
    type: Literal["import"]
    confidence: float


@dataclass
class GraphDraft:
    project_type: str
    summary: str
    nodes: list[NodeDraft]
    edges: list[EdgeDraft]
    incomplete: bool


def schema_for(model: type = GraphDraft) -> dict:
    origin = get_origin(model)
    if origin is list:
        return {"type": "array", "items": schema_for(get_args(model)[0]), "maxItems": 500}
    if origin is Literal:
        return {"type": "string", "enum": list(get_args(model))}
    if is_dataclass(model):
        hints = get_type_hints(model)
        return {"type": "object", "properties": {field.name: schema_for(hints[field.name]) for field in fields(model)},
                "required": [field.name for field in fields(model)], "additionalProperties": False}
    return {"type": {str: "string", float: "number", bool: "boolean"}[model]}


def _decode(model: type, value, path: str = "graph"):
    origin = get_origin(model)
    if origin is list:
        if not isinstance(value, list) or len(value) > 500:
            raise RepoLensError(f"{path} 必须是最多 500 项的列表。")
        return [_decode(get_args(model)[0], item, f"{path}[{index}]") for index, item in enumerate(value)]
    if origin is Literal:
        if value not in get_args(model):
            raise RepoLensError(f"{path} 使用了不支持的关系类型。")
        return value
    if is_dataclass(model):
        hints = get_type_hints(model)
        if not isinstance(value, dict) or set(value) != set(hints):
            raise RepoLensError(f"{path} 字段不匹配，必须包含且仅包含：{', '.join(hints)}。")
        return model(**{name: _decode(kind, value[name], f"{path}.{name}") for name, kind in hints.items()})
    if model is float:
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise RepoLensError(f"{path} 必须是 0 到 1 之间的数字。")
    elif model is bool:
        if type(value) is not bool:
            raise RepoLensError(f"{path} 必须是布尔值。")
    elif model is str:
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise RepoLensError(f"{path} 必须是非空且不超过 2000 字符的文本。")
    return value


def validate_graph(raw: dict, index: RepositoryIndex) -> dict:
    draft = _decode(GraphDraft, raw)
    seen: set[str] = set()
    nodes = []
    for node in draft.nodes:
        if node.id not in index.files or node.id in seen:
            raise RepoLensError(f"节点 ID 未在本次索引中出现或重复：{node.id[:80]}")
        if len(node.summary) > 280 or len(node.group) > 80:
            raise RepoLensError("节点摘要最多 280 字符，分组最多 80 字符。")
        seen.add(node.id)
        file = index.files[node.id]
        nodes.append({"id": file.id, "name": file.path.rsplit("/", 1)[-1],
                      "path": file.path, "start_line": 1, "end_line": file.line_count,
                      "summary": node.summary, "group": node.group, "entry": file.entry})
    if not nodes:
        raise RepoLensError("图至少需要一个真实索引节点。")
    edges = []
    edge_ids: set[tuple[str, str]] = set()
    for edge in draft.edges:
        pair = (edge.source, edge.target)
        if edge.source not in seen or edge.target not in seen or pair not in index.edges:
            raise RepoLensError("边必须连接已提交节点，且有本次索引的 import 证据。")
        if pair in edge_ids:
            raise RepoLensError("图中存在重复的 import 边。")
        edge_ids.add(pair)
        edges.append({"source": edge.source, "target": edge.target,
                      "type": edge.type, "confidence": edge.confidence})
    return {"schema_version": "0.1", "snapshot": index.snapshot,
            "project_type": draft.project_type, "summary": draft.summary,
            "nodes": nodes, "edges": edges, "incomplete": draft.incomplete or bool(index.warnings) or len(seen) < len(index.files) or len(edge_ids) < len(index.edges),
            "warnings": list(index.warnings)}


def dependency_preview(index: RepositoryIndex) -> dict:
    return validate_graph({
        "project_type": "Python · 本地依赖预览",
        "summary": "由本地 AST 索引生成，尚未调用 DeepSeek。边只表示可解析的文件 import，不代表运行时调用。",
        "nodes": [{"id": file.id, "summary": ("符号：" + ("、".join(file.symbols[:4]) or "模块顶层代码"))[:260],
                   "group": file.path.rpartition("/")[0] or "根目录"} for file in index.files.values()],
        "edges": [{"source": source, "target": target, "type": "import", "confidence": 1.0}
                  for source, target in sorted(index.edges)],
        "incomplete": bool(index.warnings),
    }, index)
