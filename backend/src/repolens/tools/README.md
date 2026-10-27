# 工具注册表

工具注册表为 Agent 提供显式、结构化、只读的仓库探索工具。`build_default_registry(index)` 注册 `list_files`、`find_symbols`、`read_range`、`read_symbol`、`search_text`、`find_importers`、`inspect_manifest` 和 `entry_candidates`。

每次调用只在当前索引根目录内读取，返回受限 JSON，并生成与 snapshot 绑定的 `evidence_id`。工具不会执行目标项目代码、shell、构建脚本或仓库插件。自定义工具必须由应用代码显式构造 `ToolSpec` 后调用 `registry.register()`；当前不自动扫描插件目录。

验证：使用 `RepositoryIndex` 创建 registry，调用 `schemas()` 查看模型可见契约，再用 `invoke(name, arguments)` 检查证据、截断和越界错误。后续 Agent 循环将把 registry schema 和调用结果转换为 DeepSeek tool-call 消息。
