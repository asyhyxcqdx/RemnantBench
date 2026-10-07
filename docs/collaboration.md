# 本轮协作运行分工

本轮 API 任务共 1560 次。所有 Full200 使用同一冻结 200 题；消融使用同一 Lite40。
默认 mini 查询上限 150、Agent 3600 秒、整题重试 0。

| 执行方 | Full200 | 消融 | 次数 |
| --- | --- | --- | --- |
| 项目方的 234、88 服务器 | GPT-6-Astra、GPT-6.1-Sol、Claude-Opus-5.5、Gemini-3.8-Flash | Sol 的文件位置提示、中性修复 | 880 |
| 学长自己的服务器 | GLM-5.3、Kimi-K3、DeepSeek-V4.1-Flash | DeepSeek 的文件位置提示、中性修复 | 680 |

两台项目服务器之间的具体分配可以自由安排，同一模型/实验只安排一份正式运行。
Qwen 使用此前单独安排的本地评测，不包含在这 1560 次 API 任务中。
以下命令逐条执行，不会自动并行启动所有模型。

## 学长

按根 README 克隆代码、下载魔搭数据和镜像，并准备对应渠道的私有 `glm.env`、
`kimi.env`、`deepseek.env`，然后从仓库根目录执行：

```bash
python3 scripts/evaluate.py --model GLM-5.3 --experiment Full200 --env-file glm.env --concurrency 4
python3 scripts/evaluate.py --model Kimi-K3 --experiment Full200 --env-file kimi.env --concurrency 4
python3 scripts/evaluate.py --model DeepSeek-V4.1-Flash --experiment Full200 --env-file deepseek.env --concurrency 4
python3 scripts/evaluate.py --model DeepSeek-V4.1-Flash --experiment Lite40-file-hints --env-file deepseek.env --concurrency 4
python3 scripts/evaluate.py --model DeepSeek-V4.1-Flash --experiment Lite40-neutral-repair --env-file deepseek.env --concurrency 4
```

## 项目方

```bash
python3 scripts/evaluate.py --model GPT-6-Astra --experiment Full200 --env-file astra.env --concurrency 4
python3 scripts/evaluate.py --model GPT-6.1-Sol --experiment Full200 --env-file sol.env --concurrency 4
python3 scripts/evaluate.py --model Claude-Opus-5.5 --experiment Full200 --env-file opus.env --concurrency 4
python3 scripts/evaluate.py --model Gemini-3.8-Flash --experiment Full200 --env-file gemini.env --concurrency 4
python3 scripts/evaluate.py --model GPT-6.1-Sol --experiment Lite40-file-hints --env-file sol.env --concurrency 4
python3 scripts/evaluate.py --model GPT-6.1-Sol --experiment Lite40-neutral-repair --env-file sol.env --concurrency 4
```

每条命令默认 150 步。首次可追加 `--task TASK_ID` 与独立的 `--output-dir pilot-results`
验证接口，或追加 `--dry-run` 只查看请求配置。正式开跑前确认各模型 ID 和渠道密钥。

交回每个模型/实验的结果目录及 `scripts/summarize.py` 生成的汇总。
轨迹、补丁、Verifier 明细保留在原 Trial 目录，便于复查。
服务商账单另附，汇总中的 `reported_cost_usd` 是运行框架估计值。
运行记录可能包含请求配置，公开分享前检查并移除其中的实际凭据。
