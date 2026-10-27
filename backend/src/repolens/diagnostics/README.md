# 运行诊断

提供本地服务器的脱敏运行日志，帮助定位导入、源码读取、模型请求和接口错误。

日志同时输出到启动终端，并写入 `%LOCALAPPDATA%\\RepoLens\\logs\\server.log`；非 Windows 开发环境回退到用户目录下的 `.repolens/logs/server.log`。文件按 2 MB 滚动，最多保留 3 个备份。

日志只记录路由、阶段、耗时、模型名、数量、状态码和错误类型，不记录 API Key、请求 body、源码正文或上游响应正文。日志文件属于本地运行数据，不应提交 Git。

验证：启动 `python -X utf8 backend/run.py`，访问页面并导入示例，终端应出现 `http.request`、`repository.import` 等事件；关闭服务后可在日志目录查看同样记录。
