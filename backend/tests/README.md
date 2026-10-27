# 测试

使用标准库 unittest 验证索引、契约、分析流程、传输与本地接口，不需要真实 token。

在仓库根目录执行：

    python -X utf8 -m unittest discover -s backend/tests -v

测试仓库创建于已忽略的 .repolens/tests，使用中文和空格路径，正常结束自动清理。测试不执行目标仓库代码。

IndexTests 检查索引；ContractTests 检查图和证据；AnalysisTests 使用模拟模型；ClientTests 模拟 HTTP；ServerTests 测试本地接口。Windows 下 CredentialTests 使用随机独立目标实测虚拟凭据存取，并清理。

模拟测试不代表真实 API 可用。真实模型验证应使用有效 Key 在页面发起。
