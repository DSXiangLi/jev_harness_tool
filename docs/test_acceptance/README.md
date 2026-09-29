# 验收文档（test_acceptance）

本目录存放各能力域的验收标准与验收结论。验收分两级：

- **P0（必须通过）**：真实 API 语义正确、错误纪律（不重试不重发）、fail-closed。
- **P1（应当通过）**：阈值参数化与回显、usage 透传、批量分批、margin 判据、空输入零请求等工程约束。

## 运行方式

```bash
pytest -m "not real"   # mock 套件（217 用例）：本地 HTTP mock + 真实 client 端到端，不烧 credits
pytest -m real         # 真实 API 套件（14 用例）：需项目根目录 .env 中的 JV_API_KEY，产生计费
```

## 条目

| 文档 | 范围 | 状态 |
|---|---|---|
| [mcp_tools_acceptance.md](mcp_tools_acceptance.md) | 9 个 MCP 决策工具（M1–M4） | 2026-09-29 验收通过（217 mock + 14 real 全绿） |
