# RepoLens

一个帮助理解 Python 代码仓库的本地工具：查看文件依赖，用 DeepSeek 解释模块职责，点击图节点核对源码。

当前为早期原型，支持 Windows 和 Python 项目。

## 功能

- 只读索引 Python 文件、顶层类/函数与 import，不执行目标代码。
- 自动布局文件依赖图，支持节点选择、缩放与只读源码查看。
- 使用 DeepSeek 生成项目说明、模块摘要和语义分组。
- 按真实索引校验模型节点和依赖，拒绝编造的源码引用。
- 无 Key 也可使用本地依赖预览，内置四文件示例。
- Key 可仅在内存使用，或保存到 Windows 凭据管理器。

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

Windows 64 位可从 conda-win-64.lock.txt 恢复精确版本：

```powershell
conda create --prefix .\.conda --file conda-win-64.lock.txt
```

导入自己的项目：

```powershell
python -X utf8 backend/run.py --repo 'E:\projects\my-project' --open
```

## 使用 DeepSeek

1. 导入 Python 目录，检查文件列表和本地依赖预览。
2. 展开 API Key 设置，输入有效 Key 后点击“设置 Key”。
3. 填写学习目标，确认页面说明的发送范围。
4. 点击“用 DeepSeek 理解项目”，等待通过校验的模块图与说明。
5. 点击节点核对源码；目标文件修改后须重新导入。

默认模型 deepseek-flash，可根据账户修改。仅请求 DeepSeek 官方接口。每次最多 2 次请求、累计预算 32,000 token，单次超时 60 秒。

## 数据与隐私

模型接收学习目标、相对路径、顶层符号名、import 摘要和本地依赖 ID，不发送源码正文或绝对根目录。

Key 不写入项目文件或日志，不通过状态 API 返回。默认仅驻留服务内存，勾选保存后使用 Windows 凭据管理器。“清除”移除内存值与保存的凭据；重新设置临时 Key 也会删除此前保存的值。

服务只绑定 127.0.0.1，并检查会话与请求来源，不应暴露到公网。

## 代码结构

| 模块 | 职责 |
| --- | --- |
| [indexing](backend/src/repolens/indexing/README.md) | Python AST 索引与快照 |
| [contracts](backend/src/repolens/contracts/README.md) | 图 schema 与证据校验 |
| [analysis](backend/src/repolens/analysis/README.md) | 有预算限制的模型分析 |
| [llm](backend/src/repolens/llm/README.md) | DeepSeek 客户端 |
| [prompts](backend/src/repolens/prompts/README.md) | 版本化模型指令 |
| [platform](backend/src/repolens/platform/README.md) | Windows 凭据存储 |
| [server](backend/src/repolens/server/README.md) | 本地 API |
| [web](backend/src/repolens/web/README.md) | 图与源码工作台 |

## 测试

```powershell
python -X utf8 -m unittest discover -s backend/tests -v
```

测试使用模拟模型，不需要真实 Key。详见 [测试说明](backend/tests/README.md)。

## 当前限制

- 仅 Python，最多 40 个文件、单文件 100 KB、累计 1 MB；超限或解析失败时标记不完整。
- 图是文件级静态 import 关系，不代表运行时调用；不保证复杂包路径与动态导入解析。
- 模型根据元数据推测职责，尚无主动探索源码的完整 Agent、背包问答或增量索引。
- 通过本地浏览器运行，尚无桌面安装包、拖拽、折叠分组容器或语法高亮。
- 真实模型可用性和费用取决于账户与所选模型。
