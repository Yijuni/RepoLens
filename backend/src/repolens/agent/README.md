# Agent

`explore(index, goal, client, ..., cancelled, on_event)` 返回通过校验的语义图；只在用户确认生成后调用。

状态顺序为 INDEXING → PLANNING → EXPLORING → SUBMITTING → RENDERING → IDLE。规划仅开放 `submit_plan`；探索使用显式注册的只读工具；最终通过 `submit_graph` 提交。非法参数和证据错误以工具结果反馈，提交修正受独立上限约束。

初始输入只有目标、语言/目录统计和少量文档入口。源码由工具按需读取。旧工具正文可从上下文移除，保留证据元数据；需要时重新读取。轮数、工具数、上下文、token、网络超时和总时限受统一配置限制，末尾预留提交轮次。不能通过校验时不返回半成品。

`on_event` 接收阶段、可读消息、轮次与工具名。日志只包含事件元数据。取消在调用前后及工具之间检查；进行中的 HTTP 请求等待返回或超时。

验证：运行后端 unittest；WorkflowTests 检查规划、多轮探索、工具错误反馈、提交修正、取消与预算。真实效果需通过网页配置 Key 后显式测试。
