# 原项目的计分与结果字段

评分实现沿用原项目：

- [Belta 设计说明](../belta/DESIGN.md)
- [Harbor adapter 的 Verifier 与指标定义](../harbor/adapters/belta/README.md)
- [Verifier 实现](../harbor/adapters/belta/adapter.py)
- [验证超时处理补丁](../harbor/src/harbor/plugins/belta_result.py)

| 指标 | 原始来源 |
| --- | --- |
| Reward | `verifier/belta_results.json.reward`；正常验证时与 Harbor reward 一致 |
| Score | `tests.metrics.regression_gated_recovery`，无回归修复率 |
| F2P | `tests.fixed.count` |
| P2F | `tests.regressed.count` |
| Coverage | `deletions.gold_coverage`，目标旧行的删除匹配率 |
| 补丁相似度 | `patch_code_similarity`，独立于 Score |
| 模型查询次数 | 原 mini 轨迹的 `info.model_stats.api_calls`，150 步上限使用此口径 |
| Token | Trial `result.json` 的 `agent_result` 及轨迹中的 usage |

完整 Verifier 超时继续执行已有补丁规则：F2P=0、Reward=0，Score 按原公式为 0；
Coverage、补丁相似度等仍从实际补丁计算。超时状态与 Harbor 原始 reward 缺失值
保留用于审计，整套实验的分析评分采用补丁后的 `belta_results.json`。
其他错误按原项目行为处理。

缓存细分缺失保持未知；显式返回 0 才表示 0。原 Harbor 的相关元数据和字段说明见
[API transport 文档](../harbor/adapters/belta/README.md#mini-swe-agent-api-transport)。
