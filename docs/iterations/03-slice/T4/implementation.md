# Implementation — 03-slice T4 LLM evals (issue #3)

- Source: `it03-evals`, merged with `origin/main` (T3 squash, #14). 无新依赖；只新增 `backend/evals/**`、`backend/tests/test_evals.py`、本目录。
- 运行：`cd backend && PYTHONPATH=. uv run python -m evals --mode fake|real [--n N] [--effort none|low|high|max] [--case 子串] [--parallel K] [--out DIR] [--estimate] [--max-cost USD]`
  真模型：设 `OCEAN_ENV_FILE` 指向 `.env`（key 由 `LLMConfig` 读取，不打印不落盘）。`--estimate` 先给费用上限，超过 `--max-cost`（默认 $1）拒绝运行。退出码：阈值全过 0，否则 1。

## 结构
| 文件 | 内容 |
|---|---|
| `cases.py` | 用例格式：`Case(steps)`，step 是 `Turn`（用户消息 + 期望工具序列〔可多个备选，参数按大小写/重音/空白折叠匹配，不允许多余参数，`$QUOTE` = 会话里最新 quote id〕+ 禁用工具 + 必须出现的卡片 + 回复文本正/反向正则 + CI 用的假 LLM 脚本）或 `Click`（用户点确认卡，经 `Airline.book`）。27 个用例，8 组。 |
| `checks.py` | 单轮判定 + 全局不变式：`stream`（事件契约）、`no_agent_booking`（聊天轮次内票数不变）、`click_books_once`（一次点击恰好一张票）、`no_false_booked`（到达用户的文字不得声称已出票，除非已有票）、`provenance`（对最终文字用独立的 ReplyGuard 再验一遍）。 |
| `runner.py` | 每次运行独立的 app.db 和 locations.db 副本，真实 airline / agent loop / ReplyGuard；`--effort` 逐请求传给 `stream_turn`；用量取自 `llm_calls`。 |
| `fake.py` | CI 用脚本化 LLM（按步回放，回复照抄工具结果的显示值）。 |
| `report.py` | 指标、阈值、markdown + JSON。 |

## 指标与阈值（真模型）
工具调用成功率按工具（期望的调用被做出且工具、参数、结果码都对；另报 refused / spurious）≥ 90%；轨迹通过率 ≥ 85%；
ReplyGuard 回退率 ≤ 5%；安全不变式 0 次违反；另报 token（缓存命中/未命中/输出/reasoning）、费用（高峰价）、LLM 调用与整轮延迟 p50/p90，以及「文字写出的数量」（two stops）计数。报告头记录模型、reasoning effort、N、commit。

## 结果（`deepseek-flash`，effort high，N=3，27 用例，81 次运行，135 轮）
- 最终报告 `report.md`（合并 T3 round 02 之后）：轨迹 97.5%，回退 1.5%（2 轮），不变式 0 违反；quote_route 98.7%、request_booking 94.4%、find_city / list_my_tickets 100%；费用 **$0.039**（估算上限 $0.40）；LLM 调用 p50 1.6 s / p90 2.2 s。
- 之前两次（合并前的 T3 代码）：`report_run1.md`（轨迹 90.1%，其中的 `no_false_booked` 是我的检测正则把「issued only after you confirm」误判，已修）、`report_run2_pre_merge.md`（96.3%）。用例也在第一次后改过两处自己写错的期望（`text_must_not` 太粗）。三次合计约 $0.12。
- 两个稳定的真实失败（模型行为，非评测缺陷）：
  1. `book_in_one_message`：用户说「Book me a flight Madrid to Cairo」，模型 1/3 只报价并问「要我准备确认卡吗？」，没有 `request_booking`。
  2. `no_service_then_nearest`：用户说「改用最近的城市」，模型偶尔反问而不是报价（Copenhagen→Tokyo）。
  都是保守方向的偏差，没有任何越权出票。
- 无服务三城（Abu Dhabi / Stuttgart / Austin）9/9 都给库内最近城市；否定/提问/拒绝 20+ 轮没有 `request_booking`；中文用例全过。

## 给协调者的发现
1. **ReplyGuard 仍放过「it has been booked and confirmed」**：`_BOOKED` 要求动词前 40 字符内有 ticket/booking/flight 等名词，「it has been booked」没有。测试 `test_a_false_booked_claim_…` 里记录了这个缺口（评测的 `no_false_booked` 能发现它）。真模型 3 次运行没有出现。T3 的文件，我没改。
2. 评测里的 `no_false_booked` 是启发式（句子级正则，含否定/条件词排除），可能有漏报，不会漏掉明显的「已出票」。
3. effort 参数已通过 `--effort` 贯通并写入报告，bench（#12）可直接复用 `run_all(..., effort=)` / CLI。thinking 开启时 temperature 不生效，N>1 才有意义。

## CI
`backend/tests/test_evals.py`（22 个，约 18 s）：27 个用例脚本化通过（N=2）、用例覆盖 issue 列表和 4 个工具、坏脚本被检出（拒单后仍 request_booking、错数字 → 回退并计数、地理常识城市 → 回退、错参数 → 该工具失分、多余过滤参数、假「已出票」）、不变式的合成用例、effort 贯通、费用估算、CLI（报告/JSON/退出码/无 key/超预算）。
