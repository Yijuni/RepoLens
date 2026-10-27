# Python 索引

只读遍历目录，以 AST 提取顶层类/函数、import、入口候选和本地文件依赖。不导入模块、不执行目标代码。

接口：scan_repository(path) 返回 RepositoryIndex；model_map() 返回模型可见的元数据；source(file_id) 返回经指纹核对的源码；assert_current() 检查索引是否过期。

文件 ID 由相对路径生成，快照随内容变化。源码行号从 1 开始；当前锚点覆盖整个文件。模型地图不含源码正文或绝对路径。

上限为 40 个文件、单文件 100 KB、累计 1 MB 和 2000 个目录。忽略隐藏/环境/构建目录、secrets.py、credentials.py、符号链接和 junction。超限与解析失败产生不完整警告。

支持普通、包内相对 import 与常见 src 布局；不保证动态导入、复杂命名空间包和外部依赖解析。

验证：运行后端测试中的 IndexTests，覆盖中文空格路径、相对导入、稳定 ID、过期检测、忽略规则与文件限制。命令见 [服务说明](../../../../README.md)。
