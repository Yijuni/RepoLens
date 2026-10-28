# 图契约

`semantic_schema_for()` 导出 IR v0.2 schema。`validate_semantic_graph(raw, index, evidence_records)` 校验模块、关系和来源，补入真实路径与行号。模型不能提交坐标、任意绘图代码或未见过的锚点。

模块使用工具返回的文件/符号 ID，可以聚合多个来源。模块 ID 由排序后的锚点生成，修改无关文件不改变身份；快照单独记录。受限 render_spec 指定形状、五种配色、徽标、泳道提示和强调级别。当前布局以 group 作为容器。

校验要求每个模块来源有正文读取证据，关系证据覆盖两端；imports 另需静态依赖。其他关系标为 inferred，置信度最多 0.85。证据指纹和行范围必须符合当前索引。读取两端不能从算法上证明语义关系成立，模型解释仍需人工核对。

`dependency_preview(index)` 不请求模型；v0.1 文件 import 图继续兼容。文本项目没有静态解析器时显示无依赖预览。

验证：ContractTests、EvidenceTests 覆盖虚假锚点/证据、范围、关系两端、稳定 ID、受限视觉参数与保存重校验。命令见根 README。
