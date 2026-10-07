# 本批评测配置来源

`connection-configs/*.yaml` 复制自原评测的
`belta-openlux-20261006/compatibility/connection-configs/`，保留原 model 配置的全部
字段和值。唯一的配置增量是此前已约定的大规模评测上限：

```yaml
agent:
  step_limit: 150
```

这些文件仍由原 Harbor 的 `--ak config_file=...` 读取。
模型 ID、`reasoning_effort`、Responses 和 GLM 开关沿用原
`harbor-agent-options.json` 及实际试跑命令，列在 [评测说明](../docs/evaluation.md)。
这里的 YAML 不含密钥。将 `api.env.example` 复制到仓库根目录的 `.env`，集中填写
需要运行的模型密钥。命名变量由 shell 读取，每条命令将选中的密钥作为原 Harbor
已有的 `MSWEA_API_KEY` 环境变量传入；`--env-file` 统一指向该 `.env`。
Qwen 的既有本地评测继续使用原任务配置与记录。
