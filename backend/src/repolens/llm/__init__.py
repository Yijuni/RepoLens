"""Current DeepSeek transport; keep the client boundary provider-extensible."""

from dataclasses import dataclass
import json
import socket
from urllib import error, request

from repolens.errors import RepoLensError

DEFAULT_MODEL = "deepseek-chat"
ENDPOINT = "https://api.deepseek.com/chat/completions"


@dataclass(frozen=True)
class Completion:
    content: str
    tokens: int
    estimated: bool = False
    tool_calls: tuple[dict, ...] = ()


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_key(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        raise RepoLensError("请输入有效的 DeepSeek API Key。")
    value = value.strip()
    if any(ord(character) < 33 or ord(character) > 126 for character in value):
        raise RepoLensError("API Key 不能包含空白或非 ASCII 字符。")
    return value


class DeepSeekClient:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, timeout: float = 60):
        self._api_key = validate_key(api_key)
        if not isinstance(model, str) or not model.strip() or len(model) > 100:
            raise RepoLensError("模型名称无效。")
        self.model = model.strip()
        self.timeout = timeout

    def complete(self, messages: list[dict], max_tokens: int, tools: list[dict] | None = None) -> Completion:
        request_payload = {
            "model": self.model, "messages": messages, "max_tokens": max_tokens,
            "stream": False,
            "thinking": {"type": "disabled"},
            **({"tools": [{"type": "function", "function": tool} for tool in tools]} if tools else {}),
        }
        if not tools:
            request_payload["response_format"] = {"type": "json_object"}
        payload = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(ENDPOINT, data=payload, method="POST", headers={
            "Authorization": "Bearer " + self._api_key,
            "Content-Type": "application/json", "Accept": "application/json",
        })
        try:
            with request.build_opener(_NoRedirect).open(req, timeout=self.timeout) as response:
                body = response.read(1_000_001)
            if len(body) > 1_000_000:
                raise RepoLensError("DeepSeek 返回内容过大，已拒绝读取。", 502)
            result = json.loads(body)
            choice = result["choices"][0]
            finish_reason = choice.get("finish_reason")
            if finish_reason not in {"stop", "tool_calls"}:
                raise RepoLensError("模型未完整生成结果；请选更小的目录后重试。", 502)
            message = choice["message"]
            content = message.get("content") or ""
            if not isinstance(content, str) or not content.strip():
                if finish_reason == "stop":
                    raise ValueError("empty content")
            tokens = (result.get("usage") or {}).get("total_tokens")
            estimated = type(tokens) is not int or tokens < 0
            if estimated:
                # UTF-8 byte count is intentionally conservative, not a claimed tokenizer.
                tokens = len(payload) + len(json.dumps(message, ensure_ascii=False).encode("utf-8"))
            calls = []
            for call in message.get("tool_calls", ()) or ():
                function = call.get("function") or {}
                name = function.get("name")
                arguments = function.get("arguments", "{}")
                if isinstance(name, str) and isinstance(arguments, str) and isinstance(call.get("id"), str) and call["id"]:
                    calls.append({"id": call.get("id", ""), "name": name, "arguments": arguments})
                else:
                    raise ValueError("invalid tool call")
            if len({call["id"] for call in calls}) != len(calls):
                raise ValueError("duplicate tool id")
            return Completion(content, tokens, estimated, tuple(calls))
        except error.HTTPError as exc:
            messages_by_status = {
                401: "DeepSeek 认证失败，请检查 API Key。",
                402: "DeepSeek 余额不足，请检查账户。",
                429: "DeepSeek 请求受限，请稍后重试。",
            }
            raise RepoLensError(messages_by_status.get(exc.code, f"DeepSeek 请求失败（HTTP {exc.code}），请检查模型名称或稍后重试。"), 502) from None
        except (error.URLError, TimeoutError, socket.timeout, OSError):
            raise RepoLensError("无法连接 DeepSeek 或请求超时，请检查网络后重试。", 502) from None
        except (json.JSONDecodeError, UnicodeError, KeyError, IndexError, TypeError, ValueError):
            raise RepoLensError("DeepSeek 返回格式异常，请稍后重试。", 502) from None
