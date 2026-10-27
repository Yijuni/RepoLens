"""Minimal bounded map-to-graph pipeline, not a full autonomous tool loop."""

import json
from pathlib import Path
from typing import Callable, Protocol

from repolens.contracts import schema_for, validate_graph
from repolens.errors import RepoLensError
from repolens.indexing import RepositoryIndex
from repolens.llm import Completion

PROMPTS = Path(__file__).resolve().parent.parent / "prompts" / "v1"
MAX_REQUESTS = 2
TOKEN_BUDGET = 32_000
MAX_OUTPUT = 4096


class ModelClient(Protocol):
    def complete(self, messages: list[dict], max_tokens: int) -> Completion: ...


def analyze(index: RepositoryIndex, goal: str, client: ModelClient,
            progress: Callable[[str], None] = lambda _message: None) -> dict:
    if not isinstance(goal, str) or len(goal) > 1000:
        raise RepoLensError("学习目标最多 1000 字符。")
    index.assert_current()
    context = {"goal": goal.strip() or "理解项目整体结构", "repository": index.model_map(),
               "output_schema": schema_for()}
    context_json = json.dumps(context, ensure_ascii=False)
    if len(context_json) > 24_000:
        raise RepoLensError("索引摘要超出首版预算，请选择较小的子目录再分析。")
    messages = [{"role": "system", "content": (PROMPTS / "graph.md").read_text(encoding="utf-8")},
                {"role": "user", "content": context_json}]
    total_tokens = 0
    estimated = False
    for attempt in range(MAX_REQUESTS):
        input_bound = len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) + 512
        available = TOKEN_BUDGET - total_tokens - input_bound
        if available < 512:
            raise RepoLensError("已达到本次分析预算，请缩小范围后重试。")
        output_limit = min(MAX_OUTPUT, available)
        progress("正在请求 DeepSeek 理解模块职责……" if attempt == 0 else "正在修正图结构（最后一次尝试）……")
        completion = client.complete(messages, output_limit)
        total_tokens += completion.tokens
        estimated = estimated or completion.estimated
        if total_tokens > TOKEN_BUDGET:
            raise RepoLensError("模型报告的用量超出本次预算，已停止后续请求。", 502)
        progress("正在核对节点与依赖证据……")
        try:
            raw = json.loads(completion.content)
            graph = validate_graph(raw, index)
        except (json.JSONDecodeError, RepoLensError) as exc:
            if attempt + 1 == MAX_REQUESTS:
                raise RepoLensError("模型连续两次未通过图校验，本次未更新画布；可缩小目录或调整目标后重试。", 502) from None
            # Schema errors are safe local diagnostics; don't echo untrusted model text.
            issue = str(exc) if isinstance(exc, RepoLensError) else "输出不是合法 JSON。"
            messages.append({"role": "user", "content": (PROMPTS / "repair.md").read_text(encoding="utf-8") + "\n校验错误：" + issue})
            continue
        index.assert_current()
        graph["usage"] = {"total_tokens": total_tokens, "estimated": estimated,
                          "requests": attempt + 1, "budget": TOKEN_BUDGET}
        graph["mode"] = "deepseek"
        return graph
    raise AssertionError("unreachable")
