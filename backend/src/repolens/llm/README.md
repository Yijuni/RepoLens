# 模型传输

当前实现负责调用 DeepSeek，不读取仓库、不判断图是否合法。分析流程通过客户端协议调用模型，因此后续可在这里增加其他兼容接口，而不改动索引、图校验和页面流程。

接口：`DeepSeekClient(api_key, model, timeout=60)`，`complete(messages, max_tokens, tools=None)` 返回 `Completion(content, tokens, estimated, tool_calls)`。传入工具 schema 时使用 DeepSeek 兼容的 function tool-call 协议；不传工具时保留 JSON response format，兼容现有文件级分析流程。

当前请求 DeepSeek 官方 `https://api.deepseek.com/chat/completions`，使用 Bearer 认证、非流式 JSON 输出，默认模型 `deepseek-chat`，关闭 thinking。模型名可在页面修改。参数依据 [DeepSeek 文档](https://api-docs.deepseek.com/api/create-chat-completion)。

响应上限为 1 MB，禁止自动重定向，不自动重试 HTTP 错误。认证、余额、限流、超时和格式问题映射为可读提示，不暴露上游 body 或凭据。

调用 ID 必须非空且不能重复。上游缺少 usage 时以 UTF-8 字节数保守估算，包括工具参数；Agent 每轮将 timeout 限制为单次超时和剩余总时限的较小值。

验证：ClientTests 模拟 HTTP 检查请求、工具调用解析与错误脱敏；真实接口需要有效账户与 API Key。运行方式见 [服务说明](../../../../README.md)。当前尚未接入其他 provider、自定义端点或模型 token 流式展示。
