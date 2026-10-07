# RemnantBench

RemnantBench 的任务构建与评测源码来自现有 Belta、FeatureFactory 和 Harbor 项目。
正式评测直接使用 **`harbor/adapters/belta/run_harbor.py` + 原有连接 YAML 配置**。
源码版本见 [source-versions.json](source-versions.json)。

- [原项目 Harbor 评测说明](harbor/adapters/belta/README.md)
- [本批配置与运行命令](docs/evaluation.md)
- [234/88 服务器与学长分工](docs/collaboration.md)
- [原有指标字段](docs/metrics.md)
- [原项目完整流程说明](docs/construction.md)
- [魔搭数据集与镜像](https://modelscope.cn/datasets/yinhuo/RemnantBench)

| 目录 | 内容 |
| --- | --- |
| `belta/` | 原 Belta 任务构建源码、设计文档及测试 |
| `FeatureFactory/` | 原环境构建项目，含固定版本 SDK 和 Harbor 依赖 |
| `harbor/` | 正式评测使用的原 Harbor，包含已使用的 Belta、API 适配及重试补丁 |
| `configs/connection-configs/` | 7 个 API 模型的原连接配置，仅加入已约定的 `agent.step_limit: 150` |
| `environment/` | 已发布镜像的 ID、分片及校验清单 |

依赖子模块的源码已经包含在仓库内。正式评测使用顶层 `harbor/`；
`FeatureFactory/harbor/` 是环境构建项目的固定依赖。

## 下载和准备

使用 Linux x86_64、Docker Engine（Compose、Buildx）、uv、Python 3.12+、unzip，
并具备原 `run_harbor` 所需的 Docker 与 nftables 模块权限。

```bash
git clone https://github.com/asyhyxcqdx/RemnantBench.git
cd RemnantBench

uvx --from modelscope-hub==0.4.0 ms-hub download yinhuo/RemnantBench \
  --repo-type dataset --local-dir downloads
(cd downloads && sha256sum -c SHA256SUMS)
unzip -q downloads/remnantbench-data-v1.zip

# 110 个分片按编号拼接，直接使用 Docker 自己的导入命令。
set -o pipefail
cat downloads/images/remnantbench-images.tar.gz.part-{000..109} | gzip -dc | docker load

cd harbor
uv sync --frozen --no-dev
cd ..
cp configs/api.env.example .env
chmod 600 .env
# 编辑 .env，填入需要运行的模型所用密钥。
cd harbor
source ../.env
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
```

这是原项目的直接运行命令。`--path` 选择已经冻结的题集，`--ak config_file=...`
传入 mini 配置，`--jobs-dir` 与 `--job-name` 控制 Harbor 原生结果目录。
上述结果直接写入 `belta-step150/GLM-5.3/Full200/`。

冻结数据包括 Full200（200 题、34 个仓库）、Lite40（40 题）和两项 Lite40 提示词
消融。镜像共 35 个，包括 34 个项目镜像和 1 个网络隔离镜像。直接评测复用这些
材料；构建流程与接口见原项目文档。

各组件原许可证和归属见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
