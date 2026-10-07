# 本轮分工与原 Harbor 命令

API 计划共 1560 次：你在 234、88 服务器跑 Astra、Sol、Opus、Gemini 的 Full200，
以及 Sol 的两项 Lite40 消融（880 次）；学长跑 Kimi、GLM、DeepSeek 的 Full200，
以及 DeepSeek 的两项 Lite40 消融（680 次）。Qwen 沿用已单独安排的本地评测。

所有密钥存放在根目录同一个私有 `.env`，使用 `configs/api.env.example` 中的变量名。
在 `RemnantBench/harbor/` 下先执行 `source ../.env`，再逐条运行下面的命令。
每条命令通过临时环境变量选择密钥，仍使用原 Harbor 的 `--env-file ../.env`。
这些命令直接调用原 `run_harbor`，连接 YAML 的唯一增量为已约定的 150 步上限。
每个模型/实验只安排一份正式运行。下面并发以 4 为例。

## 学长

```bash
MSWEA_API_KEY="${GLM_API_KEY:?请先填写 GLM_API_KEY}" \
uv run --no-sync python -m adapters.belta.run_harbor \
  --path ../datasets/Full200 \
  --env-file ../.env \
  --agent mini-swe-agent \
  --model litellm_proxy/glm-5.3 \
  --n-concurrent 4 --n-attempts 1 \
  --job-name Full200 \
  -- --jobs-dir ../belta-step150/GLM-5.3 --max-retries 0 \
  --ak config_file=../configs/connection-configs/glm-5.3.yaml \
  --ak reasoning_effort=max \
  --ak retry_service_failures=true

MSWEA_API_KEY="${KIMI_API_KEY:?请先填写 KIMI_API_KEY}" \
uv run --no-sync python -m adapters.belta.run_harbor \
  --path ../datasets/Full200 \
  --env-file ../.env \
  --agent mini-swe-agent \
  --model litellm_proxy/kimi-k3 \
  --n-concurrent 4 --n-attempts 1 \
  --job-name Full200 \
  -- --jobs-dir ../belta-step150/Kimi-K3 --max-retries 0 \
  --ak config_file=../configs/connection-configs/kimi-k3.yaml \
  --ak reasoning_effort=max

MSWEA_API_KEY="${DEEPSEEK_API_KEY:?请先填写 DEEPSEEK_API_KEY}" \
uv run --no-sync python -m adapters.belta.run_harbor \
  --path ../datasets/Full200 \
  --env-file ../.env \
  --agent mini-swe-agent \
  --model litellm_proxy/deepseek-v4.1-flash \
  --n-concurrent 4 --n-attempts 1 \
  --job-name Full200 \
  -- --jobs-dir ../belta-step150/DeepSeek-V4.1-Flash --max-retries 0 \
  --ak config_file=../configs/connection-configs/deepseek-v4.1-flash.yaml \
  --ak reasoning_effort=max

MSWEA_API_KEY="${DEEPSEEK_API_KEY:?请先填写 DEEPSEEK_API_KEY}" \
uv run --no-sync python -m adapters.belta.run_harbor \
  --path ../datasets/Lite40-file-hints \
  --env-file ../.env \
  --agent mini-swe-agent \
  --model litellm_proxy/deepseek-v4.1-flash \
  --n-concurrent 4 --n-attempts 1 \
  --job-name Lite40-file-hints \
  -- --jobs-dir ../belta-step150/DeepSeek-V4.1-Flash --max-retries 0 \
  --ak config_file=../configs/connection-configs/deepseek-v4.1-flash.yaml \
  --ak reasoning_effort=max

MSWEA_API_KEY="${DEEPSEEK_API_KEY:?请先填写 DEEPSEEK_API_KEY}" \
uv run --no-sync python -m adapters.belta.run_harbor \
  --path ../datasets/Lite40-neutral-repair \
  --env-file ../.env \
  --agent mini-swe-agent \
  --model litellm_proxy/deepseek-v4.1-flash \
  --n-concurrent 4 --n-attempts 1 \
  --job-name Lite40-neutral-repair \
  -- --jobs-dir ../belta-step150/DeepSeek-V4.1-Flash --max-retries 0 \
  --ak config_file=../configs/connection-configs/deepseek-v4.1-flash.yaml \
  --ak reasoning_effort=max
```

## 你（234、88 服务器）

```bash
MSWEA_API_KEY="${ASTRA_API_KEY:?请先填写 ASTRA_API_KEY}" \
uv run --no-sync python -m adapters.belta.run_harbor \
  --path ../datasets/Full200 \
  --env-file ../.env \
  --agent mini-swe-agent \
  --model openai/gpt-6-astra \
  --n-concurrent 4 --n-attempts 1 \
  --job-name Full200 \
  -- --jobs-dir ../belta-step150/GPT-6-Astra --max-retries 0 \
  --ak config_file=../configs/connection-configs/gpt-6-astra.yaml \
  --ak reasoning_effort=xhigh \
  --ak responses_system_as_instructions=true

MSWEA_API_KEY="${SOL_API_KEY:?请先填写 SOL_API_KEY}" \
uv run --no-sync python -m adapters.belta.run_harbor \
  --path ../datasets/Full200 \
  --env-file ../.env \
  --agent mini-swe-agent \
  --model openai/gpt-6.1-sol \
  --n-concurrent 4 --n-attempts 1 \
  --job-name Full200 \
  -- --jobs-dir ../belta-step150/GPT-6.1-Sol --max-retries 0 \
  --ak config_file=../configs/connection-configs/gpt-6.1-sol.yaml \
  --ak reasoning_effort=high \
  --ak responses_system_as_instructions=true

MSWEA_API_KEY="${CLAUDE_API_KEY:?请先填写 CLAUDE_API_KEY}" \
uv run --no-sync python -m adapters.belta.run_harbor \
  --path ../datasets/Full200 \
  --env-file ../.env \
  --agent mini-swe-agent \
  --model anthropic/claude-opus-5-5 \
  --n-concurrent 4 --n-attempts 1 \
  --job-name Full200 \
  -- --jobs-dir ../belta-step150/Claude-Opus-5.5 --max-retries 0 \
  --ak config_file=../configs/connection-configs/claude-opus-5-5.yaml \
  --ak reasoning_effort=max

MSWEA_API_KEY="${GEMINI_API_KEY:?请先填写 GEMINI_API_KEY}" \
uv run --no-sync python -m adapters.belta.run_harbor \
  --path ../datasets/Full200 \
  --env-file ../.env \
  --agent mini-swe-agent \
  --model litellm_proxy/gemini-3.8-flash \
  --n-concurrent 4 --n-attempts 1 \
  --job-name Full200 \
  -- --jobs-dir ../belta-step150/Gemini-3.8-Flash --max-retries 0 \
  --ak config_file=../configs/connection-configs/gemini-3.8-flash.yaml \
  --ak reasoning_effort=high

MSWEA_API_KEY="${SOL_API_KEY:?请先填写 SOL_API_KEY}" \
uv run --no-sync python -m adapters.belta.run_harbor \
  --path ../datasets/Lite40-file-hints \
  --env-file ../.env \
  --agent mini-swe-agent \
  --model openai/gpt-6.1-sol \
  --n-concurrent 4 --n-attempts 1 \
  --job-name Lite40-file-hints \
  -- --jobs-dir ../belta-step150/GPT-6.1-Sol --max-retries 0 \
  --ak config_file=../configs/connection-configs/gpt-6.1-sol.yaml \
  --ak reasoning_effort=high \
  --ak responses_system_as_instructions=true

MSWEA_API_KEY="${SOL_API_KEY:?请先填写 SOL_API_KEY}" \
uv run --no-sync python -m adapters.belta.run_harbor \
  --path ../datasets/Lite40-neutral-repair \
  --env-file ../.env \
  --agent mini-swe-agent \
  --model openai/gpt-6.1-sol \
  --n-concurrent 4 --n-attempts 1 \
  --job-name Lite40-neutral-repair \
  -- --jobs-dir ../belta-step150/GPT-6.1-Sol --max-retries 0 \
  --ak config_file=../configs/connection-configs/gpt-6.1-sol.yaml \
  --ak reasoning_effort=high \
  --ak responses_system_as_instructions=true
```

交回完整的 Harbor Job 目录，包含配置、Trial 结果、轨迹、补丁和 Verifier 明细。
实际费用使用服务商账单。运行记录可能包含请求配置，分享前检查实际凭据。
