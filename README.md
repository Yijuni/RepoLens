# RepoLens

在浏览器中理解代码仓库：查看项目结构，让模型按需查阅代码并归纳架构，再沿着交互图核对真实源码。

当前是 Windows 本地 Web 原型，模型接口支持 DeepSeek。

## 能做什么

- 左侧浏览目录、文件和符号，中间只读查看源码，右侧查看可交互的模块图。
- 无需 Key 即可查看本地静态依赖预览。支持 Python，以及 C/C++、Go、Java、JS/TS、Rust 的常见静态线索；其他可浏览文本也可通过模型工具探索。
- 用户确认后，Agent 先规划，再通过文件、搜索、源码读取等只读工具逐步理解项目，提交带证据的语义模块和关系。
- 模块支持多个文件/类/函数入口；关系区分依赖、调用、异步分发、缓存、配置和辅助交互。支持分组折叠、拖拽、平移与缩放。
- 生成时显示阶段进度，可请求取消。失败或取消保留原图。
- 手动保存架构图到项目的 `.repolens/architecture.json`。下次导入先校验并恢复，无需再次请求模型。

模型决定模块含义、名称、分组和关系，布局由本地计算。应用不会执行目标项目或模型生成的脚本。

## 启动

需要 Python 3.11+。运行依赖均为标准库，无需安装 pip/npm 包。

```powershell
git clone https://github.com/Yijuni/RepoLens.git
cd RepoLens
python -X utf8 backend/run.py --open
```

服务只监听 `127.0.0.1` 的随机端口；Ctrl+C 停止。可指定启动时导入的项目：

```powershell
python -X utf8 backend/run.py --repo "samples\mini_project" --open
```

上面的命令导入内置示例。分析自己的项目时，将 `samples\mini_project` 替换为本机已存在的项目目录；相对路径以当前终端目录为基准。路径使用双引号，兼容 Windows CMD 和 PowerShell。也可省略 `--repo`，启动后在网页中选择项目。

使用 Conda：

```powershell
conda env create --prefix .\.conda --file environment.yml
conda activate .\.conda
python -X utf8 backend/run.py --open
```

Windows 精确环境也可用 `conda create --prefix .\.conda --file conda-win-64.lock.txt` 恢复。

## 使用

1. 点击“打开项目文件夹”，或使用“输入路径”。导入只进行本地索引，不请求模型。
2. 在“模型设置”填写 DeepSeek Key 和账户中支持工具调用的模型。
3. 填写理解目标，勾选发送范围，点击“生成语义图”并确认。
4. 点击模块查看源码，在模块下方切换文件或符号；连线提示区分语义推断。
5. 点击“保存架构图”明确保存。重新打开同一项目时，有效的已保存图会直接恢复。

Chrome/Edge 的目录访问 API 可在获得写入权限后保存回所选目录。路径导入由本地服务保存。不支持目录写入的浏览器提供下载：将文件放到目标项目的 `.repolens/architecture.json` 后重新导入。源码变化或架构文件无效时，保留静态预览并提示重新生成。

## 配置和数据

复制 `config.example.toml` 为 `config.toml`，修改后重启。可配置日志、浏览/静态分析文件数、Agent 轮数、工具数、读取行数、token、修正次数与超时。Key 不属于配置文件。

默认预算：20 轮模型请求、40 次工具调用、最多两次提交修正、累计 128,000 token、单次读取 200 行、总时限 180 秒。达到预算时尝试提交有证据的部分图；无法合法提交则保留原图。取消会等待当前网络调用返回或超时，不会启动下一轮。

确认生成后，DeepSeek 会接收目标、索引摘要以及工具按需返回的有限源码与文档；不自动上传整个仓库。工具遮盖常见凭据字面量，这不能替代用户选择合适的项目和内容。保存文件包含图、来源锚点/指纹及生成信息，不包含工具读取的源码正文或 Key。

Key 只留在服务内存，或按选择保存到 Windows 凭据管理器；页面和状态 API 不回显 Key。服务使用同源校验与会话 Cookie，仅供本机单用户使用。

日志输出到终端和 `logs/server.log`。可通过 `server.log_dir`、`REPOLENS_LOG_DIR` 或 `--log-dir` 修改目录；日志不记录 Key、模型请求/响应正文或工具源码。

## 开发导航

| 模块 | 职责 |
| --- | --- |
| [indexing](backend/src/repolens/indexing/README.md) | 多语言索引、文件与符号锚点 |
| [tools](backend/src/repolens/tools/README.md) | 只读工具注册、参数校验与证据 |
| [agent](backend/src/repolens/agent/README.md) | 规划、工具探索、预算与提交 |
| [contracts](backend/src/repolens/contracts/README.md) | 语义图 schema 与来源校验 |
| [persistence](backend/src/repolens/persistence/README.md) | 架构文件保存与恢复 |
| [llm](backend/src/repolens/llm/README.md) | DeepSeek 传输适配 |
| [prompts](backend/src/repolens/prompts/README.md) | 版本化模型指令 |
| [server](backend/src/repolens/server/README.md) | 本地 API、后台任务与进度流 |
| [web](backend/src/repolens/web/README.md) | 工作台、图布局与源码交互 |
| [config](backend/src/repolens/config/README.md) | 统一运行配置 |
| [platform](backend/src/repolens/platform/README.md) | Windows 凭据存储 |
| [diagnostics](backend/src/repolens/diagnostics/README.md) | 运行日志 |
| [analysis](backend/src/repolens/analysis/README.md) | 兼容早期文件级分析 |

## 验证与限制

```powershell
python -X utf8 -m unittest discover -s backend/tests -v
```

[测试说明](backend/tests/README.md) 包含可选浏览器检查。自动化使用模拟模型，不代表真实 DeepSeek 的效果或可用性。

- 默认浏览 5000 个文本文件、64 MB；静态解析 200 个源码文件、8 MB。Agent 可读取浏览范围内未静态解析的文件，上限可配置。
- Python 使用 AST，其他语言主要依靠轻量规则。动态导入、复杂宏和运行时关系可能不完整。
- 来源校验证明模型引用了真实材料；语义关系仍是模型推断，不能当作精确运行时调用图。
- 尚无背包问答、增量索引、其他 provider、安装程序；大项目性能和真实模型效果仍需验证。
