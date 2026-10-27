# Agent 探索

`explore(index, goal, client)` 实现用户确认后的有限 DeepSeek 工具探索：模型先接收索引摘要、工具 schema 和语义 IR schema，再通过注册的只读工具获取代码证据，最后提交经 `validate_semantic_graph` 校验的语义架构图。

入口不会在导入或启动时自动执行。轮数、工具调用数、读取行数、token、上下文和墙钟时间均由 `[agent]` 配置限制；工具失败、证据不足、预算耗尽或最终 IR 无效时不返回半成品图。当前只读工具不执行目标代码，也不支持任意模型生成脚本。

验证：AgentTests 使用模拟 tool-call 客户端检查工具结果进入上下文、证据引用校验、预算和旧图保护。真实 DeepSeek 联调需要用户在网页设置 Key 后显式点击生成。

服务入口：`POST /api/agent/analyze`。导入项目不会自动触发；浏览器工作台在发送前要求用户勾选范围并再次确认。当前状态仍使用轮询 `/api/state` 显示阶段，后续再迁移到 SSE。
