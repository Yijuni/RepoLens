# RepoLens

一个帮助理解 Python 代码仓库的本地工具：查看文件依赖，用模型解释模块职责，点击图节点核对源码。

当前为早期原型，支持 Windows 和 Python 项目。

## 功能

- 只读索引 Python、C/C++、Go、Java、JavaScript/TypeScript 和 Rust 等源码，提取可验证的符号与静态依赖线索，不执行目标代码。
- 自动布局文件依赖图，支持分组折叠、节点拖拽、画布平移/缩放与只读源码查看。
- 浏览器可通过目录选择器导入文本项目快照；左侧文件树可展开目录、文件、类和函数，点击后定位真实源码行。也可输入本机完整路径直接索引。
- 当前使用 DeepSeek 生成项目说明、模块摘要和语义分组；模型接入层预留后续扩展其他模型。
- 按真实索引校验模型节点和依赖，拒绝编造的源码引用。
- 无 Key 也可使用本地依赖预览，内置四文件示例。
- Key 可仅在内存使用，或保存到 Windows 凭据管理器。
- 导入项目后不会自动请求模型；用户确认后，Agent 才会按需读取有限代码证据并生成语义架构图。路径导入的项目可手动保存到 `.repolens/architecture.json`，下次导入时按快照校验后恢复。

## 快速开始

需要 Python 3.11 或更新版本。应用仅使用标准库，无需安装 pip/npm 依赖。

```powershell
git clone https://github.com/Yijuni/RepoLens.git
cd RepoLens
python -X utf8 backend/run.py --open
```

浏览器将打开本机随机端口上的工作台，默认展示示例。按 Ctrl+C 停止服务。

使用 Conda 时，可创建独立环境：

```powershell
conda env create --prefix .\.conda --file environment.yml
conda activate .\.conda
python -X utf8 backend/run.py --open
```

需要调整日志目录、文件范围或模型预算时，复制 `config.example.toml` 为仓库根目录的 `config.toml` 后编辑；该文件默认被 Git 忽略，不应写入 API Key。也可以用 `--log-dir` 或 `REPOLENS_LOG_DIR` 临时覆盖日志目录。

Windows 64 位可从 conda-win-64.lock.txt 恢复精确版本：

```powershell
conda create --prefix .\.conda --file conda-win-64.lock.txt
```

导入自己的项目：

```powershell
python -X utf8 backend/run.py --repo 'E:\projects\my-project' --open
```

## 使用模型（当前支持 DeepSeek）

1. 导入 Python 目录，检查文件列表和本地依赖预览。
2. 展开 API Key 设置，输入有效 Key 后点击“设置 Key”。
3. 填写学习目标，确认页面说明的发送范围。
4. 点击“用 DeepSeek 理解项目”，等待通过校验的模块图与说明。
5. 点击节点核对源码；目标文件修改后须重新导入。

当前接入 DeepSeek，默认模型为 `deepseek-chat`；可在模型设置中填写账户可用的其它模型。模型请求轮数、token 预算、文件范围和超时可在 `config.toml` 中统一调整，默认最多 2 轮、累计 32,000 token、单次超时 60 秒。后续模型接入会复用同一分析流程和图校验机制。

## 数据与隐私

模型接收学习目标、相对路径、顶层符号名、import 摘要和本地依赖 ID，不发送源码正文或绝对根目录。

Key 不写入项目文件或日志，不通过状态 API 返回。默认仅驻留服务内存，勾选保存后使用 Windows 凭据管理器。“清除”移除内存值与保存的凭据；重新设置临时 Key 也会删除此前保存的值。

服务只绑定 127.0.0.1，并检查会话与请求来源，不应暴露到公网。

服务器运行日志同时输出到启动终端，并默认写入仓库根目录 `logs/server.log`；可在 `config.toml` 的 `server.log_dir`、环境变量 `REPOLENS_LOG_DIR` 或 `--log-dir` 中覆盖。若配置目录不可写，会回退到被忽略的 `.repolens/logs/server.log`。日志经过脱敏，不包含 API Key、源码正文或请求内容。

## 代码结构

| 模块 | 职责 |
| --- | --- |
| [indexing](backend/src/repolens/indexing/README.md) | Python AST 索引与快照 |
| [contracts](backend/src/repolens/contracts/README.md) | 图 schema 与证据校验 |
| [tools](backend/src/repolens/tools/README.md) | 显式只读工具注册表与证据 |
| [agent](backend/src/repolens/agent/README.md) | 用户确认后执行的 DeepSeek 工具探索 |
| [persistence](backend/src/repolens/persistence/README.md) | 显式保存与恢复语义架构图 |
| [analysis](backend/src/repolens/analysis/README.md) | 有预算限制的模型分析 |
| [llm](backend/src/repolens/llm/README.md) | 当前模型客户端与后续 provider 扩展入口 |
| [prompts](backend/src/repolens/prompts/README.md) | 版本化模型指令 |
| [platform](backend/src/repolens/platform/README.md) | Windows 凭据存储 |
| [server](backend/src/repolens/server/README.md) | 本地 API |
| [diagnostics](backend/src/repolens/diagnostics/README.md) | 脱敏运行日志 |
| [config](backend/src/repolens/config/README.md) | 统一运行配置 |
| [web](backend/src/repolens/web/README.md) | 图与源码工作台 |

## 测试

```powershell
python -X utf8 -m unittest discover -s backend/tests -v
```

测试使用模拟模型，不需要真实 Key。详见 [测试说明](backend/tests/README.md)。

## 当前限制

- 默认最多浏览 5000 个文本文件、总计 64 MB；静态分析默认最多 200 个源码文件、总计 8 MB。上限可在 `config.toml` 调整，超限或解析失败时标记不完整。
- 多语言静态解析当前覆盖 Python、C/C++、Go、Java、JavaScript/TypeScript 和 Rust；复杂宏、动态导入、生成代码和其他语言先提供只读浏览，关系覆盖会标记不完整。
- 语义 IR v0.2、只读工具注册表和 DeepSeek tool-call Agent 已建立；当前仍在完善多轮探索和关系样式，且不会因导入项目自动调用模型。
- 当前已提供 DeepSeek tool-call Agent 和语义图保存接口；仍在逐步完善 SSE 进度、取消、更多关系样式和大项目性能。
- 无 Key 时图是文件级静态依赖关系，不代表运行时调用；复杂包路径与动态导入解析可能不完整。配置 DeepSeek 并确认生成后，图可切换为带证据的语义模块关系。
- Agent 当前已支持有限只读工具探索和语义图提交，尚未实现完整 SSE 事件流、背包问答或增量索引。
- 当前通过本地浏览器运行，尚无 Tauri 桌面安装包；代码着色是轻量 Python 词法着色，不替代 Monaco 语言服务。
- 真实模型可用性和费用取决于账户与所选模型。
