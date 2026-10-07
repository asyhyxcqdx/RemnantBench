# 使用原 Harbor 和本批配置测评

先按根 README 下载数据、导入镜像、安装 Harbor。以下命令在 `RemnantBench/harbor`
目录执行。原入口与原项目说明分别为
[`run_harbor.py`](../harbor/adapters/belta/run_harbor.py) 和
[adapter README](../harbor/adapters/belta/README.md)。

## 直接运行示例

```bash
uv run --no-sync python -m adapters.belta.run_harbor \
  --path ../datasets/Full200 \
  --env-file ../sol.env \
  --agent mini-swe-agent \
  --model openai/gpt-6.1-sol \
  --n-concurrent 4 --n-attempts 1 \
  --job-name Full200 \
  -- --jobs-dir ../belta-step150/GPT-6.1-Sol --max-retries 0 \
  --ak config_file=../configs/connection-configs/gpt-6.1-sol.yaml \
  --ak reasoning_effort=high \
  --ak responses_system_as_instructions=true
```

`sol.env` 从 `configs/api.env.example` 复制并填入对应渠道的密钥。
`config_file` 读取原模型连接配置；`agent.step_limit: 150` 已写在该 YAML 内。
`--ak` 是 Harbor 的 `--agent-kwarg` 简写。已有 `config_file` 时，不再同时传 `config`。

| 模型 | `--model` | `config_file` 文件名 | `--ak reasoning_effort=` | 额外的原有开关 |
| --- | --- | --- | --- | --- |
| GPT-6-Astra | `openai/gpt-6-astra` | `gpt-6-astra.yaml` | `xhigh` | `--ak responses_system_as_instructions=true` |
| GPT-6.1-Sol | `openai/gpt-6.1-sol` | `gpt-6.1-sol.yaml` | `high` | `--ak responses_system_as_instructions=true` |
| Claude-Opus-5.5 | `anthropic/claude-opus-5-5` | `claude-opus-5-5.yaml` | `max` | — |
| Gemini-3.8-Flash | `litellm_proxy/gemini-3.8-flash` | `gemini-3.8-flash.yaml` | `high` | — |
| Kimi-K3 | `litellm_proxy/kimi-k3` | `kimi-k3.yaml` | `max` | — |
| GLM-5.3 | `litellm_proxy/glm-5.3` | `glm-5.3.yaml` | `max` | `--ak retry_service_failures=true` |
| DeepSeek-V4.1-Flash | `litellm_proxy/deepseek-v4.1-flash` | `deepseek-v4.1-flash.yaml` | `max` | — |

所有 YAML 位于 `configs/connection-configs/`。模型、档位与特殊开关来自这批原有
连接配置和实际试跑记录；完整逐条命令见 [分工说明](collaboration.md)。
修改渠道地址时，同时修改私有 env 的地址与 YAML 的 `api_base`，沿用原入口读取方式。

## 题集与输出路径

通过 `--path ../datasets/Full200`、`../datasets/Lite40`、
`../datasets/Lite40-file-hints` 或 `../datasets/Lite40-neutral-repair` 选择题集。
使用对应实验名作为 `--job-name`，通过 `--jobs-dir ../belta-step150/<模型>` 指定父目录。
Harbor 直接生成：

```text
belta-step150/<模型>/<实验>/
├── config.json
├── result.json
├── job.log
└── <trial>/
    ├── result.json
    ├── agent/trajectory.json
    ├── agent/mini-swe-agent.trajectory.json
    ├── artifacts/logs/artifacts/agent_changes.patch
    └── verifier/belta_results.json
```

单题试跑使用原入口的 `--include-task-name TASK_ID`（放在 `--` 前），并指定独立的
`--job-name`。全新 Job 中断后可使用 Harbor 原有的
`uv run --no-sync harbor jobs resume -p /absolute/path/to/job`。
Qwen 已迁移并复用旧记录的特殊 Job 继续按原来的管理方式处理。

## 已有预算与错误处理

mini 的 `agent.step_limit: 150` 限制模型查询次数；达到上限，原轨迹记录
`LimitsExceeded`。单次查询内的传输重试不等于新的 Agent 步数。
Agent 3600 秒、Verifier 1800 秒来自冻结任务的 `task.toml`。
`--max-retries 0` 限制的是整题重跑；请求级重试仍按原 Harbor 适配补丁执行。
各重试条件、次数和退避间隔见原 [adapter README](../harbor/adapters/belta/README.md#mini-swe-agent-api-transport)。

并发通过原 `--n-concurrent` 设置，上面以 4 为例。实际并发按运行机器的资源安排。
结果、计分与诊断沿用原 Harbor 及 Belta Result Plugin，字段来源见 [指标说明](metrics.md)。
