# STATE — P-PA：Pydantic AI Slim POC（#49 阶段 1）

更新：2026-11-24。分支 `it08-poc-pa`。

## 完成
- `spikes/pydantic-ai/`：独立 uv 项目（`pydantic-ai-slim[openai]` 2.71.0，可选组 `otel` = opentelemetry-sdk），
  自己的 `pyproject.toml` + `uv.lock`；`backend/` 未改。
- 15 项检查全部跑完，证据在 `spikes/pydantic-ai/results/`；报告 `docs/iterations/08-runtime/poc-pydantic-ai.md`。
- 测量：依赖 +16 个包，site-packages +38.9 MB（压缩 +7.2 MB），冷启动 import +0.3 s，首 token 1.03 s 对比现有 1.22 s
  （噪声内），取消到上游关闭 ≤2 ms（假服务器）/ 0.6 ms（真 DeepSeek）。
- 真模型花费：约 $0.03（结果文件记录 $0.0148，加调试运行），预算 $0.15 以内。

## 结论（详见报告）
技术上兼容；阶段 1 前五条在窄适配下满足；**"维护代码明显减少"不满足**（净减约 120 / 2237 行，约 5%，并新增五处适配）。

## 进行中
无。

## 下一步
- 协调者：对照 OpenAI Agents SDK 报告写 ADR。

## 开放决定（给协调者 / 用户）
- 是否采用 SDK（本 POC 建议不采用 Pydantic AI）。
- 若采用任一 SDK：同步线程在取消时被放弃（报告 §5），`Threads` + drain 必须保留。

## 如何验证
```
cd spikes/pydantic-ai && uv sync --group otel
uv run python poc/checks_fake.py                                     # 无花费
OCEAN_ENV_FILE=<main checkout>/.env uv run python poc/checks_real.py # 约 $0.01
./measure.sh
```
测试的源码状态：本分支 HEAD（基于 main `5ada4c5`）。
