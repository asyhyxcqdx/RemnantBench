# 结果统计口径

```bash
python3 scripts/summarize.py belta-step150/GLM-5.3/Full200 --csv glm-full200.csv
```

脚本读取已结束的 Trial `result.json`、Verifier 明细及原始/ATIF 轨迹。
终端输出汇总 JSON，CSV 每行对应一个已结束 Trial；尚未结束的不计入分母。
有错误而缺指标的 Trial 会保留为未知，汇总同时给出缺失数，不能将其隐去后直接
当成完整 Full200 均值。不同重试/重复任务需要先明确每题最终采用哪条记录。

| 指标 | 来源和含义 |
| --- | --- |
| Reward | 独立 Verifier 的测试结果 |
| Score | `patch_code_similarity`，冻结配置下的 Patch-only CrystalBLEU |
| Coverage | `deletions.gold_coverage`，Gold 目标旧行的删除匹配率 |
| `agent_queries` | 原始 mini 轨迹的 `info.model_stats.api_calls`，150 步限制使用的口径 |
| `atif_steps` | ATIF 消息/步骤数，包含用户消息等，不等于模型查询次数 |
| 输入/输出 Token | Harbor 汇总的 API 报告总输入、总输出；输入已包含缓存输入 |
| 缓存 Token | 缺少任意调用的细分时保持未知；显式返回 0 才表示 0 |
| `reported_cost_usd` | 框架记录的估计费用；不能替代供应商实际账单 |

Coverage 不是测试覆盖率。替换旧行时，Git diff 同时有删除和新增，因此可能匹配
Gold 删除目标；Coverage 高不能独自证明采用了纯删除式修复。原始补丁保存在
`artifacts/agent_changes.patch`，可以检查具体新增/删除内容。

对没有完整缓存数据的 Trial，ATIF `final_metrics.extra` / Agent metadata 中的
`cache_usage_missing_calls` 与 `cache_tokens_reported` 保留缺失调用数、已报告缓存量。
跨 Trial 汇总也保持此规则：只要缺失，缓存总数为 null，同时展示已报告的部分和
缺失 Trial 数，不把部分缓存量冒充完整总量。

GLM 已识别的服务失败另存 `agent/service-failures.jsonl`，不进入对话；其返回的
Token 纳入总量。失败响应缺 usage 时会标记缺失。mini 的费用估计不包含专项重试
失败响应，因此最终花费仍以渠道账单为准。网络异常未返回 usage 的部分无法仅由
轨迹推算账单。
