# 从仓库历史构建任务

本流程展示完整项目如何工作；重建新的候选集不保证生成与冻结 Full200 完全相同
的题集，因为仓库历史、模型生成的环境和人工选题可能发生变化。
重现已发布评测请直接使用冻结数据和镜像。

```text
仓库发现 → 历史版本对与旧实现提取 → 目标版本环境构建
       → 回插旧实现并验证行为差异 → 审查、选择、导出题集
       → Oracle 验证 → Agent 测评 → 独立验证与指标统计
```

## 1. 安装与配置

需要 Python 3.13、uv、Git、Docker、可访问的 GitHub 和构建模型接口。
Belta 可以自动管理本地 PostgreSQL 容器。必需的 FeatureFactory 及 SDK 源码已包含。

从 RemnantBench 根目录执行：

```bash
cp configs/construction.env.example belta/.env
chmod 600 belta/.env
# 编辑 belta/.env，填写 GitHub 和 LLM 凭据。
# 编辑 configs/construction.yaml，设置需要发现的仓库或筛选条件。
cd belta
uv sync --frozen --no-dev --default-index https://pypi.org/simple
set -a
source .env
set +a
```

示例配置使用 `featurefactory.root: ../FeatureFactory`，因此构建命令在 `belta/`
目录运行。凭据来自 Belta 自己的 `.env`，不会隐式读取 FeatureFactory 的 `.env`。
配置里的 PostgreSQL 用户名/密码为本地示例值，可以改用自己的数据库。

## 2. 仓库发现与 Candidate Pair Construction

```bash
uv run --no-sync belta candidate repos-discovery --config ../configs/construction.yaml
# 将上一步打印的 run_dir 填入下一行：
RUN_DIR=/absolute/path/to/belta/data/runs/candidate_...
uv run --no-sync belta candidate construct-pairs --run-dir "$RUN_DIR"
```

仓库发现可指定 `target_repositories`，或留空后设置语言、时间、stars 等筛选条件。
候选构建固定 new_commit，从历史中抽取实现删除片段，生成回插补丁和候选版本对。

## 3. Environment Construction 与 Retire Task Construction

```bash
uv run --no-sync belta candidate construct-environments --run-dir "$RUN_DIR"
uv run --no-sync belta candidate construct-retire-tasks --run-dir "$RUN_DIR"
```

环境阶段调用 FeatureFactory Stage2 构建目标版本环境、发现并确认通过的测试文件。
随后对同一环境比较 new_commit 和回插旧实现后的 task_base。只有目标版本通过、
回插后出现测试失败的 Pair，才进入主任务候选。阶段命令会产生计算和 API 开销。

可另开终端启动只读观察面板：

```bash
uv run --no-sync belta candidate featurefactory-ui --run-dir "$RUN_DIR"
```

它只用于观察构建，不需要在面板再次启动同一批任务。详细实现契约见
`belta/DESIGN.md`；概念说明见 `belta/PROJECT.md`。历史文件中的 Belta 指同一构建工具。

## 4. 选择、导出、Oracle 验证

Retire 技术有效候选不自动等同于冻结公开题集。检查各 Pair 的行为结果和补丁，
明确选择需要发布的任务 ID，再导出：

```bash
cd ../harbor
uv sync --frozen --no-dev --default-index https://pypi.org/simple
uv run --no-sync python -m adapters.belta.run_adapter \
  --run-dir "$RUN_DIR" --output-dir ../datasets/MyTasks
# 需要过滤时，追加重复的 --task-id PAIR_ID。
uv run --no-sync python -m adapters.belta.run_harbor \
  --path ../datasets/MyTasks --agent oracle --n-concurrent 2 \
  --job-name oracle-validation
```

冻结的 Full200 / Lite40 成员以根目录 `task-ids.json` 为准；Full200 是 200 题、
34 个仓库，不使用历史设计文档中的单仓库单题叙述替代这个实际成员表。
冻结计分使用对应 Gold 语料生成的共同 n-gram 配置；直接复现实验时保持现有材料不变。

## 5. 消融与测评

消融以同一冻结题集为输入，仅修改 `instruction.md`：

```bash
uv run --no-sync python -m adapters.belta.prepare_ablation \
  --source ../datasets/Lite40 --output ../datasets/Lite40-file-hints \
  --condition file-hints
uv run --no-sync python -m adapters.belta.prepare_ablation \
  --source ../datasets/Lite40 --output ../datasets/Lite40-neutral-repair \
  --condition neutral-repair
```

输出已存在时会拒绝覆盖。发布包已有两项消融，直接测评时无需重新生成。
之后使用根目录 `scripts/evaluate.py`；结果汇总见 `docs/metrics.md`。
新构建的自定义数据集可直接使用 `harbor/adapters/belta/run_harbor.py --path ...`。
