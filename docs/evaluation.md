# 使用冻结题集测评

先完成根目录 README 的安装、数据下载和镜像导入。无需运行 Belta 构建流程，
也无需连接我们原来的服务器。所有正式任务的题目、Gold、测试、镜像标识和
计分配置均已冻结；使用 `scripts/verify.py` 核查材料。

## 命令行参数

```bash
python3 scripts/evaluate.py \
  --model GPT-6.1-Sol \
  --experiment Full200 \
  --env-file sol.env \
  --base-url https://api.openlux.ai/v1 \
  --reasoning-effort high \
  --step-limit 150 \
  --concurrency 4
```

| 参数 | 含义 |
| --- | --- |
| `--model` | 预设名称，或自定义 `provider/model` |
| `--experiment` | `Full200`、`Lite40`、`Lite40-file-hints`、`Lite40-neutral-repair` |
| `--env-file` | 使用者自己的密钥文件，默认 `model.env` |
| `--base-url` | 同时覆盖 API 请求地址和 Agent 网络允许的主机 |
| `--reasoning-effort` | 覆盖预设推理档位；不会自动验证服务商是否实际执行该档位 |
| `--step-limit` | mini 的模型查询上限，默认 150；正式比较保持一致 |
| `--concurrency` | 同时运行的任务数，默认 1 |
| `--task` | 只跑指定任务 ID / Harbor glob，可以重复使用 |
| `--request-timeout` | 单次 API 请求超时秒数 |
| `--output-dir` | 输出根目录，默认仓库的 `belta-step150` |
| `--dry-run` | 打印最终命令，不执行测评 |

高级参数：`--provider-model` 替换预设内的 provider/model ID，
`--model-class litellm_response` 选择 Responses，
`--responses-system-as-instructions` 将原系统提示词发送到顶层 instructions。
自定义 `provider/model` 默认使用 mini 原生 Chat 工具调用，不套用预设的特殊行为。

预设保存于 `configs/models.json`。参数覆盖预设后，程序将结果作为一个内联
`--ak config=...` 传给 Harbor，避免 `config` 和 `config_file` 同时出现。
Harbor 自动在 Job/Trial 的 `config.json` 中记录实际运行配置。

## 预设与凭据

| 预设 | 推理档位 | 说明 |
| --- | --- | --- |
| GPT-6-Astra | xhigh | Responses，系统提示词只发一份 |
| GPT-6.1-Sol | high | Responses，系统提示词只发一份 |
| Claude-Opus-5.5 | max | Anthropic adaptive thinking |
| Gemini-3.8-Flash | high | Chat/native tools |
| Kimi-K3 | max | Chat/native tools |
| GLM-5.3 | max | 开启已知 OpenLux 服务错误的专项重试 |
| DeepSeek-V4.1-Flash | max | Chat/native tools |
| Qwen3.8-27B | 服务端/模型默认 | 本地模型服务；预设仅固定步数 |
| Qwen3.6-35B-A3B | 服务端/模型默认 | 本地模型服务；预设仅固定步数 |

API 预设来自本次 OpenLux 渠道试跑。其他供应商的模型 ID、参数和端点是否兼容，
需要按该供应商实际接口设置；预设不是任意供应商兼容性的保证。
Claude 预设端点为 `https://api.openlux.ai`，其他 API 预设为
`https://api.openlux.ai/v1`。不传 `--base-url` 时使用预设地址。

密钥写入私有 `.env` 文件的 `MSWEA_API_KEY`。不同模型使用不同渠道时分别建
`sol.env`、`glm.env` 等文件。Qwen 需要把 `LLM_BASE_URL` 和 `OPENAI_BASE_URL`
设置成容器可访问的模型服务地址，或传入 `--base-url`；不要用指向容器自身的
`localhost`。不把实际密钥写入预设、命令行或 Git。

## 结果、步数与重试

```text
belta-step150/<model>/<experiment>/
├── config.json
├── result.json
├── job.log
└── <trial>/
    ├── result.json
    ├── agent/trajectory.json
    ├── agent/mini-swe-agent.trajectory.json
    ├── artifacts/agent_changes.patch
    └── verifier/
```

已有同名输出目录会被入口拒绝，避免重复付费。要续跑时，在 `harbor/` 下使用
`uv run --no-sync harbor jobs resume -p /absolute/path/to/job`，先确认目录、原凭据
文件和配置仍存在。需要独立试跑时指定另一个 `--output-dir`。

150 步限制指 mini 的模型查询次数，达到上限原始轨迹记录 `LimitsExceeded`。
一轮查询内可能有多次传输重试，所以步数不等于 HTTP 请求次数。
ATIF 的总步骤还包含用户消息等，不能拿来代替这个上限。
Agent 每题时限 3600 秒，独立 Verifier 时限 1800 秒；已有补丁仍会进入验证。

网络/瞬时 API 异常保留 mini 原生 10 次总尝试，九次等待为
4、4、4、8、16、32、60、60、60 秒。已知 GLM HTTP-200 服务错误额外重试 5 次，
等待 2、4、8、16、32 秒；错误内容不进入模型上下文。格式错误仍走 mini 原生纠正流程。
整题重试固定为 0，避免将请求重试与重复跑整题混在一起。

先以 1–4 并发验证目标机器。Full200 表示任务总数，不能据此判断可运行 200 并发。
每题声明 2 CPU、8 GiB RAM，Agent 和独立 Verifier 各有执行环境；还需要镜像、
容器写层和日志空间。镜像压缩包大小小于解压后的 Docker 实际占用。
