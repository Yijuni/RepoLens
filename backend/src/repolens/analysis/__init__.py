"""Minimal bounded map-to-graph pipeline, not a full autonomous tool loop."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Callable, Protocol

from repolens.config import AgentConfig, get_config
from repolens.contracts import schema_for, validate_graph
from repolens.errors import RepoLensError
from repolens.indexing import RepositoryIndex
from repolens.llm import Completion

PROMPTS = Path(__file__).resolve().parent.parent / "prompts" / "v1"
_DEFAULT_AGENT = get_config().agent
# Compatibility aliases for callers that imported the prototype constants.
MAX_REQUESTS = min(2, _DEFAULT_AGENT.max_rounds)
MAX_OUTPUT = _DEFAULT_AGENT.max_output_tokens


class ModelClient(Protocol):
    def complete(self, messages: list[dict], max_tokens: int) -> Completion: ...


def analyze(index: RepositoryIndex, goal: str, client: ModelClient,
            progress: Callable[[str], None] = lambda _message: None,
            settings: AgentConfig | None = None) -> dict:
    settings = settings or get_config().agent
    settings = replace(settings, max_rounds=min(2, settings.max_rounds))
    if not isinstance(goal, str) or len(goal) > 1000:
        raise RepoLensError("学习目标最多 1000 字符。")
    index.assert_current()
    context = {"goal": goal.strip() or "理解项目整体结构", "repository": index.model_map(),
               "output_schema": schema_for()}
    context_json = json.dumps(context, ensure_ascii=False)
    if len(context_json) > settings.context_chars:
        raise RepoLensError("索引摘要超出上下文长度上限，请选择较小的子目录再分析。")
    messages = [{"role": "system", "content": (PROMPTS / "graph.md").read_text(encoding="utf-8")},
                {"role": "user", "content": context_json}]
    total_tokens = 0
    estimated = False
    for attempt in range(settings.max_rounds):
        progress("正在请求 DeepSeek 理解模块职责……" if attempt == 0
                 else f"正在修正图结构（第 {attempt + 1}/{settings.max_rounds} 次）……")
        completion = client.complete(messages, settings.max_output_tokens)
        total_tokens += completion.tokens
        estimated = estimated or completion.estimated
        progress("正在核对节点与依赖证据……")
        try:
            raw = json.loads(completion.content)
            graph = validate_graph(raw, index)
        except (json.JSONDecodeError, RepoLensError) as exc:
            if attempt + 1 == settings.max_rounds:
                attempts_text = "两次" if settings.max_rounds == 2 else f"{settings.max_rounds} 次"
                raise RepoLensError(f"模型连续{attempts_text}未通过图校验，本次未更新画布；可缩小目录或调整目标后重试。", 502) from None
            # Schema errors are safe local diagnostics; don't echo untrusted model text.
            issue = str(exc) if isinstance(exc, RepoLensError) else "输出不是合法 JSON。"
            messages.append({"role": "user", "content": (PROMPTS / "repair.md").read_text(encoding="utf-8") + "\n校验错误：" + issue})
            continue
        index.assert_current()
        graph["usage"] = {"total_tokens": total_tokens, "estimated": estimated,
                          "requests": attempt + 1}
        graph["mode"] = "deepseek"
        return graph
    raise AssertionError("unreachable")
