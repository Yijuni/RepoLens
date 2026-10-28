"""Explicit planning, bounded tool exploration and evidence-checked submission."""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Callable, Protocol

from repolens.config import AgentConfig, get_config
from repolens.contracts import semantic_schema_for, validate_semantic_graph
from repolens.diagnostics import get_logger
from repolens.errors import RepoLensError
from repolens.indexing import RepositoryIndex
from repolens.tools import ToolRegistry, build_default_registry, validate_arguments

PROMPTS = Path(__file__).resolve().parent.parent / "prompts" / "v2"
LOGGER = get_logger()
PLAN_SCHEMA = {"type": "object", "required": ["steps"], "additionalProperties": False,
               "properties": {"steps": {"type": "array", "maxItems": 8,
                                        "items": {"type": "string", "maxLength": 240}}}}


class AgentClient(Protocol):
    def complete(self, messages: list[dict], max_tokens: int, tools: list[dict] | None = None): ...


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _repository_map(index: RepositoryIndex) -> dict:
    """Compact initial map; files and code bodies are retrieved on demand."""
    folders: dict[str, int] = {}
    languages: dict[str, int] = {}
    docs = []
    for file in index.documents.values():
        top = file.path.partition("/")[0] if "/" in file.path else "(root)"
        folders[top] = folders.get(top, 0) + 1
        languages[file.language] = languages.get(file.language, 0) + 1
        if file.path.rsplit("/", 1)[-1].lower() in {"readme.md", "pyproject.toml", "package.json", "go.mod", "cmakelists.txt"}:
            docs.append({"file_id": file.id, "path": file.path})
    return {"name": index.root.name, "snapshot": index.snapshot, "files": len(index.documents),
            "analyzed_files": len(index.files), "languages": languages,
            "directories": dict(sorted(folders.items())[:60]), "documents": docs[:16],
            "incomplete": bool(index.warnings)}


def _compact(messages: list[dict], limit: int) -> None:
    """Discard old code bodies while retaining tool protocol and provenance."""
    if len(_json(messages)) <= limit:
        return
    for message in messages[2:-2]:
        if message["role"] == "tool":
            payload = json.loads(message["content"])
            if "result" in payload:
                payload["result"] = {"compacted": True, "message": "旧工具正文已移出上下文；需要时重新读取。"}
                message["content"] = _json(payload)
        if len(_json(messages)) <= limit:
            return
    # Remove complete turns only; retain matching tool call/response pairs.
    while len(_json(messages)) > limit and len(messages) > 4:
        boundary = next((i for i in range(3, len(messages)) if messages[i]["role"] in {"assistant", "user"}), None)
        if boundary is None:
            break
        del messages[2:boundary]
    if len(_json(messages)) > limit:
        raise RepoLensError("Agent 上下文达到上限，请缩小目标或提高 context_chars。")


def explore(index: RepositoryIndex, goal: str, client: AgentClient,
            progress: Callable[[str], None] = lambda _message: None,
            settings: AgentConfig | None = None, registry: ToolRegistry | None = None,
            cancelled: Callable[[], bool] = lambda: False,
            on_event: Callable[[dict], None] = lambda _event: None) -> dict:
    settings = settings or get_config().agent
    if not isinstance(goal, str) or len(goal) > 1_000:
        raise RepoLensError("学习目标最多 1000 字符。")
    if settings.max_rounds < 4:
        raise RepoLensError("工具探索至少需要 4 轮，请提高 agent.max_rounds 后重启。")
    started = time.monotonic()
    deadline = started + settings.wall_time_seconds
    total_tokens = tool_calls = repairs = requests = 0
    estimated = False
    def usage():
        return {"total_tokens": total_tokens, "estimated": estimated, "requests": requests,
                "tool_calls": tool_calls, "elapsed_seconds": round(time.monotonic() - started, 2)}
    def check():
        if cancelled():
            raise RepoLensError("已取消生成，原架构图保持不变。", 409)
        if time.monotonic() >= deadline:
            raise RepoLensError("Agent 达到时间上限，原架构图保持不变。", 408)
    phase = "IDLE"
    def emit(stage: str, message: str, **details):
        nonlocal phase
        phase = stage
        progress(message)
        on_event({"phase": stage, "message": message, "usage": usage(), **details})
        LOGGER.info("agent.event phase=%s round=%s tool=%s status=%s requests=%d tools=%d tokens=%d estimated=%s", stage,
                    details.get("round", "-"), details.get("tool", "-"), details.get("status", "-"),
                    requests, tool_calls, total_tokens, estimated)
    emit("INDEXING", "正在核对当前源码快照……")
    check()
    index.assert_current()
    registry = registry or build_default_registry(index)
    registry.set_read_limit(settings.max_read_lines)
    registry.evidence.clear()
    exploration_tools = [{"name": spec["name"], "description": spec["description"],
                          "parameters": spec["input_schema"]} for spec in registry.schemas()]
    plan_tool = {"name": "submit_plan", "description": "提交简短的探索步骤；此阶段不能产图。", "parameters": PLAN_SCHEMA}
    graph_tool = {"name": "submit_graph", "description": "提交最终语义架构图，所有锚点须有本轮读取证据。",
                  "parameters": semantic_schema_for()}
    messages = [{"role": "system", "content": (PROMPTS / "agent.md").read_text(encoding="utf-8")},
                {"role": "user", "content": _json({"goal": goal.strip() or "理解项目整体架构",
                 "repository": _repository_map(index), "limits": {"rounds": settings.max_rounds,
                 "read_lines": settings.max_read_lines}})}]
    planned = False
    emit("PLANNING", "Agent 正在规划需要查阅的文档与代码……")
    for round_number in range(1, settings.max_rounds + 1):
        check()
        tools = [plan_tool] if not planned else exploration_tools + [graph_tool]
        _compact(messages, settings.context_chars)
        if hasattr(client, "timeout"):
            client.timeout = max(.1, min(settings.request_timeout_seconds, deadline - time.monotonic()))
        requests += 1
        emit(phase, f"Agent 第 {round_number}/{settings.max_rounds} 轮，正在等待模型回复……", round=round_number, status="request")
        completion = client.complete(messages, settings.max_output_tokens, tools=tools)
        total_tokens += completion.tokens
        estimated = estimated or completion.estimated
        emit(phase, f"第 {round_number} 轮模型已回复，正在处理下一步。", round=round_number, status="response")
        check()
        calls = list(getattr(completion, "tool_calls", ()) or ())
        if not calls or len(calls) > 32:
            repairs += 1
            if repairs > settings.max_repairs:
                raise RepoLensError("Agent 未能提交有效的工具调用或架构图。", 502)
            messages.append({"role": "user", "content": "必须使用当前允许的工具。先 submit_plan，探索源码后使用 submit_graph。"})
            continue
        messages.append({"role": "assistant", "content": completion.content or None,
                         "tool_calls": [{"id": call["id"], "type": "function", "function": {
                             "name": call["name"], "arguments": call["arguments"]}} for call in calls]})
        submitted = None
        allowed = {tool["name"] for tool in tools}
        for call in calls:
            check()
            name = call["name"]
            try:
                if name not in allowed:
                    raise RepoLensError("该阶段不允许此工具，请使用当前提供的工具。")
                arguments = json.loads(call["arguments"])
                if name == "submit_plan":
                    validate_arguments(arguments, PLAN_SCHEMA)
                    if not arguments["steps"] or any(not item.strip() for item in arguments["steps"]):
                        raise RepoLensError("探索计划不能为空。")
                    planned = True
                    result = {"accepted": True, "steps": arguments["steps"]}
                    emit("EXPLORING", "规划完成，开始按需查阅项目文档和代码。", round=round_number)
                elif name == "submit_graph":
                    if len(calls) != 1:
                        raise RepoLensError("最终提交必须单独调用 submit_graph，不能与其他工具混用。")
                    emit("SUBMITTING", "正在校验模块锚点与关系证据……", round=round_number)
                    submitted = validate_semantic_graph(arguments, index, dict(registry.evidence))
                    result = {"accepted": True}
                else:
                    tool_calls += 1
                    emit("EXPLORING", f"正在查阅项目：{name}……", tool=name, round=round_number)
                    result = registry.invoke(name, arguments)
                check()
            except (json.JSONDecodeError, RepoLensError) as exc:
                if cancelled() or time.monotonic() >= deadline:
                    check()
                issue = str(exc) if isinstance(exc, RepoLensError) else "工具参数不是合法 JSON。"
                result = {"error": issue, "recoverable": True}
                emit(phase, "工具或提交校验未通过，已反馈给模型修正。", tool=name, status="rejected", round=round_number)
                if name in {"submit_graph", "submit_plan"}:
                    repairs += 1
                    if repairs > settings.max_repairs:
                        raise RepoLensError("Agent 未能在修正上限内提交通过校验的架构图。", 502) from None
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": _json(result)})
        if submitted is not None:
            check()
            index.assert_current()
            emit("RENDERING", "证据校验完成，正在准备架构图。")
            submitted["usage"] = usage()
            emit("IDLE", "生成完成。")
            return submitted
    raise RepoLensError("Agent 达到轮数上限，未能提交通过校验的架构图；原图保持不变。", 408)
