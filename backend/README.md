# Python 服务

提供仓库索引、DeepSeek 分析、图校验和本地网页。运行仅依赖 Python 3.11 标准库，无需 pip 或 npm 安装。

从仓库根目录启动：

    python -X utf8 backend/run.py --open

可选参数：--repo 指定源码仓库，--port 指定端口（默认随机），--open 打开浏览器。服务仅监听 127.0.0.1。

模块调用方向：server 组合 indexing、agent、persistence 和 platform；agent 使用 tools、llm、contracts 与 prompts。analysis 兼容早期文件级流程。源码根包为 src/repolens，各模块目录附有 README。

运行测试：

    python -X utf8 -m unittest discover -s backend/tests -v

当前为本地单用户原型，不包含桌面壳、数据库或安装程序。
