# 本地服务

ThreadingHTTPServer 组合索引、Agent、凭据与网页，只监听 127.0.0.1，默认随机端口。

从项目根目录运行 `python -X utf8 backend/run.py --open`，默认导入内置示例。可用 `--repo "samples\mini_project"` 指定现有项目目录；双引号同时兼容 CMD 和 PowerShell，相对路径以当前终端目录为基准。启动失败会写入 `server.start_failed` 日志；路径带单引号时会提示 CMD 的引号规则，不自动修改用户输入的路径。

| 接口 | 用途 |
| --- | --- |
| GET /、/api/state | 页面/会话 Cookie、当前仓库与图、凭据配置状态 |
| GET /api/source?id= | 校验指纹后按文件或符号 ID 读取源码 |
| POST /api/repository、/api/example | 路径导入/示例，不请求模型 |
| POST /api/repository/upload | 浏览器文本快照，隔离暂存并在成功后切换 |
| POST /api/repository/pick | 启动 Windows 目录选择 |
| GET /api/repository/pick/status | 选择结果、取消或错误 |
| POST /api/key、/api/key/delete | 设置或清除凭据，不回显 Key |
| POST /api/agent/analyze | confirmed=true 才创建任务，202 返回 id |
| GET /api/agent/run?id= | 小型任务状态，不重复传整图 |
| GET /api/agent/events?id= | SSE 阶段事件，终态关闭连接 |
| POST /api/agent/cancel | 按 id 请求取消，等待当前请求返回 |
| POST /api/architecture/save | 明确保存服务持有的已校验图 |
| POST /api/architecture/export | 输出同格式架构文件，供目录句柄保存/下载 |
| POST /api/analyze | 兼容早期文件级 JSON 分析 |

所有 API 检查会话、Host、Origin 和 Fetch-Site。普通请求上限 16 KB，上传上限按浏览配置计算。只允许一个导入/分析任务，忙时 409；取消接口在忙时仍可用。

AppState 管理运行快照；后台 Agent 仅成功后替换图，失败或取消保留旧图。浏览器上传使用独立会话目录，失败不破坏之前源码；服务关闭清理自己的临时快照。

运行状态和 SSE 包含实时累计 usage，失败或取消仍保留用量和明确的终态消息。`agent.stopped` 记录可读原因、阶段、请求/工具数和 token；未知异常记录类型和代码位置，不记录异常原始内容、局部变量或模型响应。失败后释放任务锁，可再次生成。

验证：ServerTests、JobTests 使用实际 loopback HTTP 和模拟客户端检查确认、SSE、取消、来源保护和保存恢复。命令见根 README。限制：本地单用户、内存索引和任务，服务重启不保留未保存图或生成任务。
