# 示例项目

一个不含私有数据的四文件 Python 项目，供首次体验与集成验证。RepoLens 只解析它，不执行它。

预期 import 关系：main.py → service.py；service.py → storage.py、formatter.py，共 4 个节点与 3 条边。

main 是入口；build_report 组合服务；normalize_items 整理条目；format_report 生成文本。在页面点击“使用示例”即可重新导入。

验证：核对图的节点和边、点击源码；配置有效 Key 后再分析模块职责。
