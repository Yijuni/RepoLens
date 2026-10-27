# 图契约

为模型输出定义结构，并把模型节点关联到实际源码。

NodeDraft、EdgeDraft 和 GraphDraft 为 dataclass 模型。schema_for() 根据类型注解生成 JSON Schema；validate_graph(raw, index) 检查字段、类型、唯一 ID、置信度和 import 证据，再从索引补入路径和行号。

模型不能提供自己的锚点或坐标。未知节点、伪造边、重复项和非法类型会被拒绝；遗漏节点/依赖或索引存在警告时自动标记 incomplete。

dependency_preview(index) 生成无模型的本地 import 预览，界面会明确标识。

验证：ContractTests 覆盖 ID、锚点、依赖、布尔类型、NaN 与 schema 派生。运行方式见 [服务说明](../../../../README.md)。当前仅支持文件级 import 图。
