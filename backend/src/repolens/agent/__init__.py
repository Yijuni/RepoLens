"""Bounded DeepSeek tool exploration and semantic graph submission."""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Callable, Protocol

from repolens.config import AgentConfig, get_config
from repolens.contracts import semantic_schema_for, validate_semantic_graph
from repolens.errors import RepoLensError
from repolens.indexing import RepositoryIndex
from repolens.tools import ToolRegistry, build_default_registry


PROMPTS = Path(__file__).resolve().parent.parent / "prompts" / "v1"


class AgentClient(Protocol):
    def complete(self, messages: list[dict], max_tokens: int,
                 tools: list[dict] | None = None): ...


def _tool_payload(schemas: list[dict]) -> list[dict]:
    return [{"name": schema["name"], "description": schema["description"],
             "parameters": schema["input_schema"]} for schema in schemas]


def _bounded_context(messages: list[dict], limit: int) -> None:
    size = len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))
    if size > limit:
        raise RepoLensError("Agent 上下文已达到配置上限，请缩小仓库范围或提高 context_chars。")


def explore(index: RepositoryIndex, goal: str, client: AgentClient,
            progress: Callable[[str], None] = lambda _message: None,
            settings: AgentConfig | None = None,
            registry: ToolRegistry | None = None) -> dict:
    """Run a bounded plan/explore/submit loop and return a validated semantic IR.

    Importing a repository never calls this function; the server invokes it only
    after an explicit user confirmation. Tool outputs are evidence-bound and the
    final model JSON is accepted only when all anchors and evidence references
    pass local validation.
    """
    settings = settings or get_config().agent
    if not isinstance(goal, str) or len(goal) > 1_000:
        raise RepoLensError("学习目标最多 1000 字符。")
    index.assert_current()
    registry = registry or build_default_registry(index)
    registry.set_read_limit(settings.max_read_lines)
    schemas = registry.schemas()
    messages = [
        {"role": "system", "content": (PROMPTS / "agent.md").read_text(encoding="utf-8")},
        {"role": "user", "content": json.dumps({
            "goal": goal.strip() or "理解项目整体架构",
            "repository": index.model_map(),
            "tools": schemas,
            "output_schema": semantic_schema_for(),
            "limits": {"max_rounds": settings.max_rounds, "max_tool_calls": settings.max_tool_calls,
                       "max_read_lines": settings.max_read_lines},
        }, ensure_ascii=False)},
    ]
    evidence_ids: set[str] = set()
    tool_calls = 0
    total_tokens = 0
    estimated = False
    started = time.monotonic()
    tool_definitions = _tool_payload(schemas)

    for round_number in range(settings.max_rounds):
        if time.monotonic() - started > settings.wall_time_seconds:
            raise RepoLensError("Agent 已达到墙钟时间上限，本次未更新架构图。", 408)
        input_bound = len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) + 512
        available = settings.token_budget - total_tokens - input_bound
        if available < 512:
            raise RepoLensError("Agent 已达到 token 预算，请缩小范围或提高 token_budget。")
        output_limit = min(settings.max_output_tokens, available)
        progress(f"Agent 正在探索项目（第 {round_number + 1}/{settings.max_rounds} 轮）……")
        _bounded_context(messages, settings.context_chars)
        completion = client.complete(messages, output_limit, tools=tool_definitions)
        total_tokens += completion.tokens
        estimated = estimated or completion.estimated
        if total_tokens > settings.token_budget:
            raise RepoLensError("Agent 用量超出本次预算，已停止。", 502)

        calls = list(getattr(completion, "tool_calls", ()) or ())
        if calls:
            if tool_calls + len(calls) > settings.max_tool_calls:
                raise RepoLensError("Agent 已达到工具调用上限，本次未提交架构图。", 408)
            assistant_message = {"role": "assistant", "content": completion.content or None,
                                 "tool_calls": [{"id": call.get("id", ""), "type": "function",
                                                 "function": {"name": call["name"], "arguments": call["arguments"]}}
                                                for call in calls]}
            messages.append(assistant_message)
            for call in calls:
                name = call.get("name", "")
                try:
                    arguments = json.loads(call.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    raise RepoLensError(f"Agent 工具 {name} 参数不是合法 JSON。", 502) from None
                progress(f"Agent 调用只读工具：{name}……")
                result = registry.invoke(name, arguments)
                evidence_ids.add(result["evidence"]["evidence_id"])
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                 "name": name, "content": json.dumps(result, ensure_ascii=False)})
                tool_calls += 1
            continue

        if not completion.content.strip():
            raise RepoLensError("Agent 没有提交架构图或工具调用。", 502)
        progress("Agent 已提交语义架构图，正在进行本地证据校验……")
        try:
            raw = json.loads(completion.content)
            graph = validate_semantic_graph(raw, index, evidence_ids)
        except (json.JSONDecodeError, RepoLensError) as exc:
            if round_number + 1 >= settings.max_rounds:
                raise RepoLensError("Agent 未能在预算内提交通过证据校验的架构图。", 502) from None
            issue = str(exc) if isinstance(exc, RepoLensError) else "输出不是合法 JSON。"
            messages.append({"role": "user", "content": (PROMPTS / "agent_repair.md").read_text(encoding="utf-8") + "\n校验错误：" + issue})
            continue
        index.assert_current()
        graph["usage"] = {"total_tokens": total_tokens, "estimated": estimated,
                          "requests": round_number + 1, "tool_calls": tool_calls,
                          "budget": settings.token_budget}
        return graph
    raise RepoLensError("Agent 达到轮数上限但未提交架构图。", 408)
