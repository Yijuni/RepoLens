"""Dataclass-derived JSON schema and strict graph/evidence validation."""

from dataclasses import dataclass, fields, is_dataclass
import hashlib
import math
from typing import Literal, get_args, get_origin, get_type_hints

from repolens.errors import RepoLensError
from repolens.indexing import RepositoryIndex


SEMANTIC_RELATION_TYPES = (
    "imports", "calls", "serves", "dispatches", "uses_cache", "configures",
    "supports", "contains", "external",
)
SEMANTIC_MODULE_KINDS = (
    "module", "service", "library", "worker", "storage", "adapter", "config", "external",
)
RENDER_SHAPES = ("module", "service", "library", "worker", "storage", "adapter", "external")
RENDER_ACCENTS = ("teal", "blue", "purple", "orange", "gray")
RENDER_EMPHASIS = ("low", "normal", "high")


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


@dataclass
class SemanticModuleDraft:
    module_key: str
    label: str
    kind: str
    summary: str
    group: str
    anchor_ids: list[str]
    evidence_refs: list[str]
    confidence: float
    render_spec: dict | None = None


@dataclass
class SemanticRelationDraft:
    source: str
    target: str
    type: str
    label: str
    confidence: float
    evidence_refs: list[str]


@dataclass
class SemanticGraphDraft:
    project_type: str
    summary: str
    modules: list[SemanticModuleDraft]
    relations: list[SemanticRelationDraft]
    incomplete: bool
    incomplete_regions: list[str]


def semantic_schema_for() -> dict:
    """Return the model-facing v0.2 semantic graph schema.

    Module keys are response-local aliases. The server derives stable IDs from
    the snapshot and real anchor IDs after validation.
    """
    render_spec = {
        "type": "object",
        "properties": {
            "shape": {"type": "string", "enum": list(RENDER_SHAPES)},
            "accent": {"type": "string", "enum": list(RENDER_ACCENTS)},
            "badge": {"type": "string", "maxLength": 24},
            "lane": {"type": "string", "maxLength": 80},
            "emphasis": {"type": "string", "enum": list(RENDER_EMPHASIS)},
        },
        "required": [], "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "project_type": {"type": "string", "maxLength": 240},
            "summary": {"type": "string", "maxLength": 2000},
            "modules": {"type": "array", "maxItems": 500, "items": {
                "type": "object", "properties": {
                    "module_key": {"type": "string", "maxLength": 80},
                    "label": {"type": "string", "maxLength": 120},
                    "kind": {"type": "string", "enum": list(SEMANTIC_MODULE_KINDS)},
                    "summary": {"type": "string", "maxLength": 280},
                    "group": {"type": "string", "maxLength": 80},
                    "anchor_ids": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 120}},
                    "evidence_refs": {"type": "array", "maxItems": 40, "items": {"type": "string", "maxLength": 120}},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "render_spec": render_spec,
                },
                "required": ["module_key", "label", "kind", "summary", "group", "anchor_ids", "evidence_refs", "confidence"],
                "additionalProperties": False,
            }},
            "relations": {"type": "array", "maxItems": 1200, "items": {
                "type": "object", "properties": {
                    "source": {"type": "string", "maxLength": 80},
                    "target": {"type": "string", "maxLength": 80},
                    "type": {"type": "string", "enum": list(SEMANTIC_RELATION_TYPES)},
                    "label": {"type": "string", "maxLength": 120},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence_refs": {"type": "array", "maxItems": 40, "items": {"type": "string", "maxLength": 120}},
                },
                "required": ["source", "target", "type", "label", "confidence", "evidence_refs"],
                "additionalProperties": False,
            }},
            "incomplete": {"type": "boolean"},
            "incomplete_regions": {"type": "array", "maxItems": 100, "items": {"type": "string", "maxLength": 240}},
        },
        "required": ["project_type", "summary", "modules", "relations", "incomplete", "incomplete_regions"],
        "additionalProperties": False,
    }


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


def _text(value, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise RepoLensError(f"{field} 必须是非空且不超过 {limit} 字符的文本。")
    return value.strip()


def _bounded_list(value, field: str, limit: int) -> list:
    if not isinstance(value, list) or len(value) > limit:
        raise RepoLensError(f"{field} 必须是最多 {limit} 项的列表。")
    return value


def _confidence(value, field: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise RepoLensError(f"{field} 必须是 0 到 1 之间的数字。")
    return float(value)


def _resolve_anchor(anchor_id: str, index: RepositoryIndex) -> dict:
    """Resolve a model-provided file/symbol ID to a server-owned source anchor."""
    if not isinstance(anchor_id, str) or not anchor_id.strip() or len(anchor_id) > 120:
        raise RepoLensError("源码锚点 ID 无效。")
    for file in index.documents.values():
        if file.id == anchor_id:
            return {"file_id": file.id, "path": file.path, "start_line": 1,
                    "end_line": file.line_count, "name": file.path}
    for file in index.files.values():
        for symbol in file.definitions:
            if symbol.id == anchor_id:
                return {"file_id": file.id, "symbol_id": symbol.id, "path": file.path,
                        "start_line": symbol.start_line, "end_line": symbol.end_line,
                        "name": symbol.qualified_name}
    raise RepoLensError(f"源码锚点不存在或不属于本次索引：{anchor_id[:80]}")


def _evidence_refs(value, field: str, evidence_ids: set[str] | frozenset[str] | None) -> list[str]:
    refs = _bounded_list(value, field, 40)
    if any(not isinstance(ref, str) or not ref.startswith("ev_") or len(ref) > 120 for ref in refs):
        raise RepoLensError(f"{field} 包含无效证据 ID。")
    if evidence_ids is not None:
        missing = [ref for ref in refs if ref not in evidence_ids]
        if missing:
            raise RepoLensError(f"{field} 引用了本轮未获得的证据：{missing[0]}")
    if not refs:
        raise RepoLensError(f"{field} 不能为空，语义图必须引用工具证据。")
    return list(dict.fromkeys(refs))


def _render_spec(value) -> dict:
    if value is None:
        return {"shape": "module", "accent": "teal", "badge": "", "lane": "", "emphasis": "normal"}
    if not isinstance(value, dict) or set(value) - {"shape", "accent", "badge", "lane", "emphasis"}:
        raise RepoLensError("render_spec 只能包含受支持的视觉参数。")
    shape = value.get("shape", "module")
    accent = value.get("accent", "teal")
    emphasis = value.get("emphasis", "normal")
    if shape not in RENDER_SHAPES or accent not in RENDER_ACCENTS or emphasis not in RENDER_EMPHASIS:
        raise RepoLensError("render_spec 使用了不支持的形状、颜色或强调级别。")
    badge = value.get("badge", "")
    lane = value.get("lane", "")
    if not isinstance(badge, str) or len(badge) > 24 or not isinstance(lane, str) or len(lane) > 80:
        raise RepoLensError("render_spec 的徽标或泳道文本过长。")
    return {"shape": shape, "accent": accent, "badge": badge, "lane": lane, "emphasis": emphasis}


def validate_semantic_graph(raw: dict, index: RepositoryIndex,
                           evidence_ids: set[str] | frozenset[str] | None = None) -> dict:
    """Validate v0.2 semantic graph and derive IDs/anchors locally.

    ``evidence_ids`` is supplied by the Agent run. Passing ``None`` only keeps
    the helper useful for schema fixtures; production submission should pass
    the exact evidence set so fabricated references are rejected.
    """
    if not isinstance(raw, dict):
        raise RepoLensError("语义图必须是 JSON 对象。")
    required = {"project_type", "summary", "modules", "relations", "incomplete", "incomplete_regions"}
    if set(raw) != required:
        raise RepoLensError("语义图字段不匹配，不能添加或遗漏字段。")
    project_type = _text(raw["project_type"], "project_type", 240)
    summary = _text(raw["summary"], "summary", 2000)
    if type(raw["incomplete"]) is not bool:
        raise RepoLensError("incomplete 必须是布尔值。")
    regions = _bounded_list(raw["incomplete_regions"], "incomplete_regions", 100)
    if any(not isinstance(item, str) or not item.strip() or len(item) > 240 for item in regions):
        raise RepoLensError("incomplete_regions 包含无效文本。")
    modules_raw = _bounded_list(raw["modules"], "modules", 500)
    modules = []
    module_ids: dict[str, str] = {}
    used_ids: set[str] = set()
    for position, item in enumerate(modules_raw):
        if not isinstance(item, dict):
            raise RepoLensError(f"modules[{position}] 必须是对象。")
        required_module = {"module_key", "label", "kind", "summary", "group", "anchor_ids", "evidence_refs", "confidence"}
        if set(item) - (required_module | {"render_spec"}) or not required_module <= set(item):
            raise RepoLensError(f"modules[{position}] 字段不匹配。")
        key = _text(item["module_key"], f"modules[{position}].module_key", 80)
        if key in module_ids:
            raise RepoLensError(f"模块别名重复：{key}")
        kind = _text(item["kind"], f"modules[{position}].kind", 30)
        if kind not in SEMANTIC_MODULE_KINDS:
            raise RepoLensError(f"modules[{position}].kind 不受支持。")
        anchors_raw = _bounded_list(item["anchor_ids"], f"modules[{position}].anchor_ids", 20)
        if not anchors_raw:
            raise RepoLensError(f"modules[{position}] 至少需要一个真实源码锚点。")
        anchors = [_resolve_anchor(anchor_id, index) for anchor_id in anchors_raw]
        anchor_keys = sorted({(anchor["symbol_id"] if "symbol_id" in anchor else anchor["file_id"]) for anchor in anchors})
        module_id = "module_" + hashlib.sha256((index.snapshot + "|" + "|".join(anchor_keys)).encode("utf-8")).hexdigest()[:16]
        if module_id in used_ids:
            raise RepoLensError("多个模块由完全相同的源码锚点组成，无法生成稳定 ID。")
        used_ids.add(module_id)
        module_ids[key] = module_id
        evidence_refs = _evidence_refs(item["evidence_refs"], f"modules[{position}].evidence_refs", evidence_ids)
        primary = anchors[0]
        modules.append({"id": module_id, "module_key": key, "name": _text(item["label"], f"modules[{position}].label", 120),
                        "kind": kind, "summary": _text(item["summary"], f"modules[{position}].summary", 280),
                        "group": _text(item["group"], f"modules[{position}].group", 80), "anchors": anchors,
                        "anchor_id": primary.get("symbol_id", primary["file_id"]),
                        "path": primary["path"], "start_line": primary["start_line"], "end_line": primary["end_line"],
                        "entry": False, "evidence_refs": evidence_refs,
                        "confidence": _confidence(item["confidence"], f"modules[{position}].confidence"),
                        "render_spec": _render_spec(item.get("render_spec"))})
    if not modules:
        raise RepoLensError("语义图至少需要一个模块。")
    relations_raw = _bounded_list(raw["relations"], "relations", 1200)
    relations = []
    edge_ids: set[str] = set()
    for position, item in enumerate(relations_raw):
        if not isinstance(item, dict):
            raise RepoLensError(f"relations[{position}] 必须是对象。")
        required_relation = {"source", "target", "type", "label", "confidence", "evidence_refs"}
        if set(item) != required_relation:
            raise RepoLensError(f"relations[{position}] 字段不匹配。")
        source_key = _text(item["source"], f"relations[{position}].source", 80)
        target_key = _text(item["target"], f"relations[{position}].target", 80)
        if source_key not in module_ids or target_key not in module_ids or source_key == target_key:
            raise RepoLensError(f"relations[{position}] 引用了不存在或相同的模块。")
        relation_type = _text(item["type"], f"relations[{position}].type", 30)
        if relation_type not in SEMANTIC_RELATION_TYPES:
            raise RepoLensError(f"relations[{position}].type 不受支持。")
        evidence_refs = _evidence_refs(item["evidence_refs"], f"relations[{position}].evidence_refs", evidence_ids)
        source = module_ids[source_key]
        target = module_ids[target_key]
        edge_id = "edge_" + hashlib.sha256((source + "|" + target + "|" + relation_type).encode("utf-8")).hexdigest()[:16]
        if edge_id in edge_ids:
            raise RepoLensError("语义图中存在重复关系。")
        edge_ids.add(edge_id)
        relations.append({"id": edge_id, "source": source, "target": target, "type": relation_type,
                          "label": _text(item["label"], f"relations[{position}].label", 120),
                          "confidence": _confidence(item["confidence"], f"relations[{position}].confidence"),
                          "evidence_refs": evidence_refs})
    return {"schema_version": "0.2", "snapshot": index.snapshot, "project_type": project_type,
            "summary": summary, "nodes": modules, "edges": relations,
            "incomplete": raw["incomplete"] or bool(index.warnings),
            "incomplete_regions": list(dict.fromkeys(regions + index.warnings)),
            "warnings": list(index.warnings), "mode": "deepseek-agent"}


def dependency_preview(index: RepositoryIndex) -> dict:
    return validate_graph({
        "project_type": "多语言 · 本地依赖预览",
        "summary": "由本地静态索引生成，尚未调用 DeepSeek。边只表示可解析的源码依赖，不代表运行时调用。",
        "nodes": [{"id": file.id, "summary": ("符号：" + ("、".join(file.symbols[:4]) or "模块顶层代码"))[:260],
                   "group": file.path.rpartition("/")[0] or "根目录"} for file in index.files.values()],
        "edges": [{"source": source, "target": target, "type": "import", "confidence": 1.0}
                  for source, target in sorted(index.edges)],
        "incomplete": bool(index.warnings),
    }, index)
