# DeepSeek 传输

仅负责调用 DeepSeek，不读取仓库、不判断图是否合法。

接口：DeepSeekClient(api_key, model, timeout=60)，complete(messages, max_tokens) 返回 Completion(content, tokens, estimated)。

固定请求官方 https://api.deepseek.com/chat/completions，使用 Bearer 认证、非流式 JSON 输出，默认模型 deepseek-flash，关闭 thinking。模型名可在页面修改。参数依据 [DeepSeek 文档](https://api-docs.deepseek.com/api/create-chat-completion)。

响应上限为 1 MB，禁止自动重定向，不自动重试 HTTP 错误。认证、余额、限流、超时和格式问题映射为可读提示，不暴露上游 body 或凭据。

验证：ClientTests 模拟 HTTP 检查请求与错误脱敏；真实接口需要有效账户与 API Key。运行方式见 [服务说明](../../../../README.md)。当前无其他 provider、自定义端点或模型 token 流式展示。
