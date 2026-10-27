# 本地服务

组合索引、模型、凭据与网页。基于 ThreadingHTTPServer，只监听 127.0.0.1，默认随机端口。

| 接口 | 作用 |
| --- | --- |
| GET / | 网页与本地会话 Cookie |
| GET /api/state | 索引、图、进度和凭据是否配置，不含 Key |
| GET /api/source?id= | 按索引 ID 查看经核对的源码 |
| POST /api/repository | 通过 path 导入 Python 目录 |
| POST /api/repository/upload | 接收浏览器目录选择的文本快照，写入本机临时目录后索引 |
| POST /api/repository/pick | 启动异步 Windows 目录选择；立即返回 pending |
| GET /api/repository/pick/status | 领取选择结果、取消或错误 |
| POST /api/example | 载入示例 |
| POST /api/key | 通过 api_key 和 remember 设置凭据 |
| POST /api/key/delete | 清除凭据 |
| POST /api/analyze | 通过 goal、model、confirmed 请求分析 |

接口要求 HttpOnly、SameSite=Strict 会话 Cookie，检查 Host、Origin 和 Fetch-Site，拒绝跨来源访问。POST 只接受最多 16 KB 的 JSON。页面采用 CSP。

任务串行执行，忙时返回 409；Web 工作台优先使用浏览器目录选择并上传文本快照到本机临时目录，手动路径仍可直接读取。旧的 `/api/repository/pick` 接口保留给本地运行时实验，不作为网页主入口。导入/分析失败保留旧数据。无持久索引、SSE 或取消接口；仅供本地单用户使用。

验证：ServerTests 使用真实 loopback HTTP 和模拟模型检查会话、同源、发送确认、源码及分析闭环。命令见 [服务说明](../../../../README.md)。
