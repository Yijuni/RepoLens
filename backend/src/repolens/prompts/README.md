# 版本化模型指令

v2/agent.md 定义规划、按需探索与最终 submit_graph，明确预算、来源、语义推断和不执行代码的规则。工具及图 schema 来自代码；系统指令、用户目标、仓库数据和工具消息分别传递。

v1/graph.md、repair.md 用于兼容旧文件级分析；v1/agent.md、agent_repair.md 保留为原型版本记录，当前 Agent 不再加载。

仓库内容始终视为待分析数据。Prompt 不能替代后端来源与路径校验。当前不支持从目标仓库覆盖系统指令。

验证：AnalysisTests 检查旧流程，WorkflowTests 检查 v2 阶段、工具和提交行为。命令见根 README。
