# repolens

应用根包，包含索引、分析、模型传输、契约、安全存储和网页服务。

入口为 server.main()，由 backend/run.py 调用。errors.py 定义可直接展示的 RepoLensError；错误信息不得包含凭据或上游响应正文。

运行与测试命令见 [服务说明](../../README.md)。各功能目录 README 说明其职责、接口和边界。
