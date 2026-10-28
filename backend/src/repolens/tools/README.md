# 只读工具

`build_default_registry(index)` 提供 list_files、find_symbols、read_range、read_symbol、search_text、find_importers、inspect_manifest、entry_candidates。工具不会执行项目代码或命令。

调用 `schemas()` 获取结构化参数契约，`invoke(name, arguments)` 执行并验证类型、必填和未知字段。结果有输出上限，按完整条目/行截断。大符号先返回有限范围和 next_line；空文件可读取。浏览范围内的文件均可读取，不要求其语言已有静态解析器。

每次调用返回 evidence_id、snapshot、文件/符号锚点、指纹、读取范围与截断标记。`registry.evidence` 保存本轮来源元数据；正文不会写进架构文件。列表与符号发现仅为 metadata，不能替代正文证据。

扩展方式：在可信应用代码中构造 `ToolSpec(name, description, input_schema, handler)`，通过 `register()` 显式注册，再将 registry 传给 explore。schema 支持 object/array/string/integer/number/boolean、required、additionalProperties、enum 和长度上限。不会扫描或执行目标仓库插件。

常见 Key、密码赋值和带凭据 URL 在工具片段中遮盖；并非完整秘密检测器。未知工具和无效参数返回可恢复错误，由 Agent 决定下一步。

验证：ToolTests、ToolBoundaryTests、WorkflowTests 检查参数、范围、空文件、大符号、遮盖与工具错误修正。
