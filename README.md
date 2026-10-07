# RemnantBench

RemnantBench evaluates whether coding agents can remove obsolete implementations
reintroduced from real repository history while preserving the target version's
behavior. 本仓库包含任务构建、环境构建、Harbor 测评和结果汇总的完整源码。

- **代码与协作**：[GitHub](https://github.com/asyhyxcqdx/RemnantBench)
- **冻结数据与镜像**：[ModelScope / 魔搭](https://modelscope.cn/datasets/yinhuo/RemnantBench)
- **直接跑测评**：[评测指南](docs/evaluation.md)
- **完整构建流程**：[构建指南](docs/construction.md)
- **本轮模型分工与命令**：[协作运行说明](docs/collaboration.md)
- **指标与费用口径**：[结果说明](docs/metrics.md)

## 项目内容

| 目录 | 内容 |
| --- | --- |
| `belta/` | Repository Discovery、Candidate Pair Construction、Environment Construction、Retire Task Construction |
| `FeatureFactory/` | 被 Belta 调用的环境构建库，以及其固定版本 SDK 和 Harbor 依赖 |
| `harbor/` | 本项目正式评测使用的 Harbor，包括 Belta adapter、API 适配及计分代码 |
| `configs/` | 无密钥的构建配置、9 个模型预设、凭据文件模板 |
| `scripts/` | 参数化评测、镜像导入、数据校验、结果汇总 |
| `environment/` | 镜像标签、固定镜像 ID、下载包校验信息 |

源码直接包含在仓库中，无需访问内部服务器或递归拉取私有子模块。
`source-versions.json` 记录各组件的来源版本。内部 Python 包名、模块名和
镜像前缀继续使用 `belta`，对外项目名称统一为 RemnantBench。
`FeatureFactory/harbor` 是 FeatureFactory 的固定依赖；正式测评使用顶层 `harbor`。

冻结题集包含 **Full200：200 题、34 个仓库**；Lite40 是其中的 40 题。
文件位置提示、中性修复两项消融只改变 Lite40 的题目说明，其他材料保持一致。
数据和镜像通过魔搭下载，解压到本仓库对应目录；Git 不保存下载件、密钥或运行结果。

## 快速测评

需要 Linux x86_64、Python 3.13、uv、unzip、Docker Engine（含 Compose 和 Buildx），
以及使用 Docker 和加载 nftables 模块的权限。API 测评不要求 GPU。

```bash
git clone https://github.com/asyhyxcqdx/RemnantBench.git
cd RemnantBench

# 安装评测环境
cd harbor
uv sync --frozen --no-dev --default-index https://pypi.org/simple
cd ..
source harbor/.venv/bin/activate

# 下载现成题集和镜像，避免重新构建
uvx --from modelscope-hub==0.4.0 ms-hub download yinhuo/RemnantBench \
  --repo-type dataset --local-dir downloads \
  --include 'remnantbench-data-v1.zip' 'image-archive.json' 'images/*'
python3 scripts/verify.py --archive downloads/remnantbench-data-v1.zip
unzip downloads/remnantbench-data-v1.zip
python3 scripts/verify.py
python3 scripts/images.py load --directory downloads

cp configs/api.env.example model.env
chmod 600 model.env
# 编辑 model.env，填写自己的密钥。
python3 scripts/evaluate.py --model GLM-5.3 --experiment Full200 \
  --env-file model.env --concurrency 4 --step-limit 150
```

`--dry-run` 可先查看命令，不启动 Docker、不调用 API。模型、实验、并发、步数、
接口地址、推理档位都可用命令行参数指定；配置模板保留模型原有的默认设置。
结果直接写入 `belta-step150/<模型>/<实验>/`。

完整构建是另一条入口：从真实仓库历史生成新的任务，需要 GitHub 凭据、构建模型
API 和更多计算资源；使用现成题集跑评测不需要执行构建阶段。

## 版本与归属

各组件的原许可证保留在其目录内，见 [第三方说明](THIRD_PARTY_NOTICES.md)。
上游项目和数据来源记录在 `source-versions.json`、`repositories.json`。
本仓库不改变这些第三方材料的许可证。
