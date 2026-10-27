# 本地服务

组合索引、模型、凭据与网页。基于 ThreadingHTTPServer，只监听 127.0.0.1，默认随机端口。

| 接口 | 作用 |
| --- | --- |
| GET / | 网页与本地会话 Cookie |
| GET /api/state | 索引、图、进度和凭据是否配置，不含 Key |
| GET /api/source?id= | 按索引 ID 查看经核对的源码 |
| POST /api/repository | 通过 path 导入代码目录 |
| POST /api/repository/upload | 接收浏览器目录选择的文本快照，写入本机临时目录后索引 |
| POST /api/repository/pick | 启动异步 Windows 目录选择；立即返回 pending |
| GET /api/repository/pick/status | 领取选择结果、取消或错误 |
| POST /api/example | 载入示例 |
| POST /api/key | 通过 api_key 和 remember 设置凭据 |
| POST /api/key/delete | 清除凭据 |
| POST /api/analyze | 通过 goal、model、confirmed 请求分析 |
| POST /api/agent/analyze | 用户确认后启动 DeepSeek 只读工具探索和语义图提交 |
| POST /api/architecture/save | 用户确认后保存已校验语义图到目标仓库 `.repolens/architecture.json` |

接口要求 HttpOnly、SameSite=Strict 会话 Cookie，检查 Host、Origin 和 Fetch-Site，拒绝跨来源访问。普通 POST 最多 16 KB；浏览器目录上传使用与配置浏览上限匹配的较大 JSON 请求体。页面采用 CSP。

任务串行执行，忙时返回 409；导入不会自动调用模型。用户明确确认后 `/api/agent/analyze` 才会调用只读工具循环。浏览器上传快照不能持久保存；通过本地路径导入的项目可以明确保存已校验图，重新导入时按 snapshot 尝试恢复。索引支持 Python、C/C++、Go、Java、JavaScript/TypeScript、Rust 的静态依赖线索；无持久索引、SSE 或取消接口，仅供本地单用户使用。

验证：ServerTests 使用真实 loopback HTTP 和模拟模型检查会话、同源、发送确认、源码及分析闭环。命令见 [服务说明](../../../../README.md)。
