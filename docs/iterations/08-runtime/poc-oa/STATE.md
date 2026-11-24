# STATE — P-OA：OpenAI Agents SDK POC（#49 阶段 1）

更新：2026-11-24。分支 `it08-poc-oa`，worker：Opus 5.5。

## 已完成
- `spikes/openai-agents/`：独立 uv 项目（`openai-agents==0.25.0`，自己的 `pyproject.toml` + `uv.lock`），复用 backend 的四个工具、airline、store、ReplyGuard、`build_context`；`backend/` 一行没改。
- 15 条检查全部有脚本和输出（`spikes/openai-agents/out/*.txt`）；测量：依赖、镜像增量估算、import、首 token、取消延迟、每 chunk CPU、迁移行数估算。
- 报告：`docs/iterations/08-runtime/poc-openai-agents.md`（结尾有阶段 1 通过条件逐条结论和风险清单）。
- 真模型花费合计 **$0.0046**（上限 $0.15）。

## 结论摘要
- 功能：DeepSeek 上流式、并行 + 串行工具、取消（TCP 层关闭中位 0.4 ms，之后 0 请求 0 工具）、ReplyGuard 先于输出、预算（窄适配）、trace 替换 + 自定义 processor、受信上下文、app.db 历史 —— 全部满足。
- 代码量：应用代码净减约 100 行（−5%；`llm.py + loop.py` −15%）；依赖 23 → 46 包，镜像约 +56 MB，import +0.45 s。"明显减少维护代码"这一条需协调者在 ADR 里权衡。
- 危险默认值：trace 默认发往 api.openai.com 且默认含敏感数据；工具默认并发；未知工具默认结束整轮；reasoning 回放按模型名判断。

## 进行中
- 无。

## 下一步（协调者）
- 读本报告和 P-PA 报告，写 ADR（`docs/design/adr-001-agent-runtime.md`）。
- 若采用：迁移 worker 按报告第 4 节清单做窄适配。

## 开放决定
- 采用与否（依赖体积 vs. #48 / #35 少写的代码）。
- 是否继续支持内联 `<think>` 的服务器（SDK 不处理）。

## 如何验证
```bash
cd spikes/openai-agents && uv sync --frozen && cd poc
../.venv/bin/python f_cancel.py && ../.venv/bin/python f_errors.py && ../.venv/bin/python f_trace.py   # 免费
OCEAN_ENV_FILE=<main checkout>/.env ../.venv/bin/python r1_real.py   # 真 DeepSeek，约 $0.002
```
测试的源码状态：本分支 HEAD（见 PR）；backend 未改动，`tool/review_lint.py` 通过。
