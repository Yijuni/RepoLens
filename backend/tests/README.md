# 验证

标准库 unittest，无需真实 Key：

```powershell
python -X utf8 -m unittest discover -s backend/tests -v
```

test_core.py 覆盖索引、契约、旧分析流程、传输、HTTP 和 Windows 凭据。test_workflow.py 覆盖正文证据、关系两端、规划探索、可恢复错误、取消、轮数/时间限制、大用量及多次工具调用、失败诊断与重试、后台任务、SSE、保存恢复与写入失败。临时仓库位于被忽略的 .repolens/tests，使用中文/空格路径，不执行目标代码。

可选浏览器检查需要 Node 22+、Chrome/Edge，以及仓库内的 .conda/python.exe：

```powershell
node backend/tests/browser_smoke.cjs
```

脚本在独立浏览器配置中连接 CDP，启动 browser_server.py 模拟模型并使用测试仓库。检查源码、同意确认、缩放、语义关系、多个锚点、路径保存恢复，以及模拟目录句柄的原地保存/恢复；另检查运行中 token 更新、超过旧配额、SSE 断线轮询、失败原因/用量保留与重新生成。截图位于 .repolens/preview.png。不会请求 DeepSeek 或读取真实 Key。可用 REPOLENS_BROWSER 环境变量指定 Chrome/Edge 的可执行路径。

原生操作系统目录弹窗、实际 Windows DPI 与大图性能仍需人工验收；模拟通过不能代表真实模型质量。
