# 图契约

为模型输出定义结构，并把模型节点关联到实际源码。

NodeDraft、EdgeDraft 和 GraphDraft 为兼容原型的文件级模型。`SemanticGraphDraft` 对应 IR v0.2：模型提交模块别名、真实文件/符号锚点、证据引用、语义分组和受限关系类型；`validate_semantic_graph(raw, index, evidence_ids)` 负责生成稳定模块/边 ID、补入真实路径和行号，并拒绝伪造锚点、证据、关系和绘图参数。`semantic_schema_for()` 返回模型可见的严格 schema。

模型不能提供自己的锚点或坐标。未知节点、伪造边、重复项和非法类型会被拒绝；遗漏节点/依赖或索引存在警告时自动标记 incomplete。

dependency_preview(index) 生成无模型的本地 import 预览，界面会明确标识。

验证：ContractTests 覆盖文件级和语义图的 ID、锚点、证据、依赖、布尔类型、NaN、受限 render_spec 与 schema。运行方式见 [服务说明](../../../../README.md)。语义图尚未接入模型循环，当前页面仍使用文件级静态预览。
