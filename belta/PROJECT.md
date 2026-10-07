# Retire Obsolete Implementation Benchmark

Belta 是一个 benchmark construction 项目，用来评估代码 agent 是否能够识别并清理真实开发者在仓库演进中淘汰的旧实现残留。

## 快速运行：使用 FF 面板观察环境构造

Environment Construction 仍由 Belta 命令启动。FeatureFactory 面板只连接
Belta 使用的 FF 数据库，用于查看每个 repo 的 Stage2 状态、运行轨迹、日志、
测试进度和错误；不需要在面板中点击“运行”或“重新运行”。
该命令会自动启用 FF observer mode：不执行启动恢复、容器清理、批量任务调度
或其他后台维护，并拒绝所有写请求，因此可以在环境构造开始前、运行中或结束后
随时启动。

先在远端服务器的一个独立终端或 tmux 会话中启动观察面板：

```bash
cd /path/to/belta

BELTA_ROOT="$PWD"
RUN="$BELTA_ROOT/data/runs/<run_id>"

uv run belta candidate featurefactory-ui \
  --run-dir "$RUN"
```

该命令默认监听 `127.0.0.1:18742`，并持续占用当前终端。使用 tmux 时可按
`Ctrl-b d` 退出会话但保留面板进程。

使用 VS Code Remote SSH 等支持自动端口转发的客户端时，在 `PORTS` 面板转发
远端端口 `18742`，或直接点击客户端识别出的面板 URL。若客户端没有自动转发，
再在本地电脑另开终端手动建立 SSH tunnel：

```bash
ssh -N -L 18742:127.0.0.1:18742 user@remote-host
```

转发建立后，在本地浏览器打开：

```text
http://127.0.0.1:18742
```

可以在启动面板前后运行实际环境构造：

```bash
cd /path/to/belta

BELTA_ROOT="$PWD"
RUN="$BELTA_ROOT/data/runs/<run_id>"
LOG="$RUN/timing/03-construct-environments-$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$RUN/timing"
set -a
source "$BELTA_ROOT/.env"
set +a
set -o pipefail

{
  /usr/bin/time \
    -f 'stage=construct-environments elapsed=%E user=%U sys=%S max_rss_kb=%M' \
    env \
      PYTHONUNBUFFERED=1 \
      uv run --no-sync belta candidate construct-environments \
        --run-dir "$RUN"
} 2>&1 | tee "$LOG"
```

正式构造命令不设置 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 或对应的小写变量。
运行机器应能直接访问所需服务；受限网络中的镜像和包源使用 FeatureFactory 原生的
`FEATURE_FACTORY_*` 镜像配置。`PYTHONUNBUFFERED=1` 只让 Python 输出立即进入终端
和日志，不改变 Environment Construction 语义。

命令显式加载 Belta `.env`，确保 Stage2 使用当前文件中的模型端点，而不是 tmux
会话中可能残留的旧环境变量。`construct-environments` 同一时间只允许一个 Belta
实例运行。进程异常中断后，下次执行同一命令会自动恢复 Belta 自己残留的
`queued/running` Stage2 run，并重试失败或未完成仓库；状态为 `completed` 且 commit
一致的环境仍直接复用，不会重新调用模型。FF UI 或手工创建的非 Belta Stage2 run
不在这个恢复范围内。

随后在面板中打开“Stage2 环境构造”。Belta 新建的 Stage2 run 会自动显示；
“批量任务”页面不会出现这批运行。`construct-environments` 已自动完成或复用
FF 公共运行资产预热，不要求另外执行 `prewarm-environment`。

环境构造结束后，可在面板终端按 `Ctrl+C` 停止面板；关闭本地浏览器页面或
停止 observer 后台不会停止环境构造。后续需要查看时重新启动即可。

本项目的核心不是让 agent 从 `old_commit` 直接实现 `new_commit`，也不是让 agent 对着未来测试写补丁。Belta 从真实的 `old_commit -> new_commit` 演进中抽取被淘汰的实现对象，并构造一个新旧实现共存的代码清理任务。

核心问题是：

```text
在真实仓库演进中，开发者会移除旧实现对象或旧实现片段。
代码 agent 是否能识别这些已被真实开发者淘汰的旧实现残留，
并在保持目标版本行为的前提下将其退役？
```

Belta 使用一种可控的 benchmark intervention 来构造任务：

```text
1. 从真实 old_commit -> new_commit 演进中抽取 reinsert targets。
2. 将 reinsert targets 对应的旧实现对象或片段重新注入 new_commit，形成 task_base。
3. 如果 task_base 相比 new_commit 出现 hidden test 失败，
   说明这些旧实现残留会导致目标版本行为偏离。
4. agent 需要清理这些旧实现残留，使仓库恢复目标版本行为。
```

在 Belta 中，obsolete implementation 指真实开发者在 `old_commit -> new_commit` 演进中从 `implementation_code` 中移除的旧实现残留。Candidate Pair Construction 用 `reinsert_targets.jsonl` 记录这些候选旧实现残留，包括 function / method 内 pure deletion 片段，以及仍然存在的 Python 文件中被完整删除的 function、method 或 class。整个文件被删除的 `D` 路径不进入候选池。

Belta 主 benchmark 聚焦 behavior-sensitive obsolete implementation：这些旧实现残留被重新注入 `new_commit` 后，会导致 hidden tests 失败。

这里的 hidden tests 来自 FeatureFactory 在目标版本环境中“文件级确认通过”的测试文件集合。Belta 将该集合称为 `validation_test_universe`：它是当前任务的可复现验证测试全集，但不声称覆盖仓库理论上的全部测试、全部平台或全部配置组合。

因此，主任务不是单纯静态删除代码，而是在隐藏测试约束下退役旧实现残留并恢复目标版本行为。Belta 会验证同一 repo 的全部 accepted candidate pairs：重新插入后仍然通过测试的 pair 不输出为正式 task，出现测试失败的 pair 分别输出为正式 task，因此一个 repo 可以产生多个 behavior-sensitive tasks。

## 1. Benchmark 目标

Belta 关注三类能力：

```text
1. 理解目标版本上下文
   agent 需要理解 new_commit 中已经存在的当前实现、代码结构和行为边界。

2. 识别旧实现残留
   task_base 中被重新插入了一批旧实现对象或片段。
   agent 需要判断哪些旧实现残留已经不属于目标版本，不应继续保留。

3. 退役旧实现并保持行为正确
   agent 需要清理这些 obsolete implementation residue，同时不破坏目标版本行为。
```

Belta 不要求 agent 从 `old_commit` 开始实现整个 `new_commit`。Belta 给 agent 的 `task_base` 已经基于 `new_commit` 构造，包含目标版本的主要实现和项目上下文；同时，Belta 会把真实开发者在 `old_commit -> new_commit` 中移除的旧实现对象或片段重新插入进去。Agent 的任务是识别并清理这些旧实现残留。

## 2. 任务定义

每个 benchmark 题目对应一个真实版本对：

```text
old_commit -> new_commit
```

当前代码和 `DESIGN.md` 中，Candidate Pair Construction 已经为每个候选版本对生成 `pair_id`：

```text
pair_id = <repo_key>__<old_commit[:12]>__<new_commit[:12]>
```

其中：

```text
repo_key
  由 full_name 生成，例如 example/repo -> example__repo。

old_commit / new_commit
  分别是候选版本对的完整 commit sha。
```

在 Retire Obsolete Implementation 任务阶段，如果一个 candidate pair 只生成一个最终任务，则：

```text
task_id = pair_id
```

如果后续一个 candidate pair 生成多个任务变体，再在 `pair_id` 后追加稳定后缀。

一个任务至少继承 candidate pair 的这些字段：

```json
{
  "task_id": "example__repo__7a1b2c3d4e5f__3f9a8b7c6d5e",
  "pair_id": "example__repo__7a1b2c3d4e5f__3f9a8b7c6d5e",
  "repo_key": "example__repo",
  "full_name": "example/repo",
  "github_repo_id": 123456789,
  "clone_url": "https://github.com/example/repo.git",
  "default_branch": "main",
  "old_commit": "7a1b2c3d4e5f678901112233445566778899aabb",
  "new_commit": "3f9a8b7c6d5e4f32100112233445566778899abc"
}
```

这些字段来自 `candidate_pairs/accepted_candidates.jsonl`。其中：

```text
github_repo_id
  用于后续在 FeatureFactory 数据库中查找对应的 github_repositories 记录。

new_commit
  作为 FeatureFactory Stage2 的 target_commit_sha，用于构造目标版本环境。

clone_url / default_branch
  用于 Belta 本地记录、Git cache 和 sanity check。
```

从这个版本对中，Belta 构造：

```text
R_old
  old_commit 对应的旧版本仓库。用于从 diff 中抽取 reinsert targets。

R_new
  new_commit 对应的新版本仓库。提供目标版本项目上下文。

task_base_full
  Belta 内部验证用仓库状态。
  它由 new_commit 加上重新插入的旧实现残留构成，仍包含测试文件。

gold_cleanup.patch
  oracle/reference cleanup patch，表示从 task_base_full 清理旧实现残留并恢复到 new_commit 的参考补丁。

validation_test_universe
  evaluator 后台真正运行和比较的测试入口文件集合。
  它来自 FF 在 `new_commit` 环境中已经文件级成功运行并通过的测试文件集合。

validation_test_snapshots
  evaluator 后台恢复测试时需要的验证测试入口文件快照。
  它对应 validation_test_universe 中的测试入口文件。
  Belta 当前不额外扫描全仓库 conftest.py、fixtures、testdata、snapshots、golden、expected outputs 等测试辅助材料；这些辅助材料如果原本存在于 repo 中，会作为普通项目上下文保留。
```

## 3. Agent 输入

对每个最终任务，Belta 先构造内部验证状态 `task_base_full`：

```text
task_base_full = new_commit + reinserted obsolete implementation residue
```

Retire 不持久化完整 agent repo 副本。Harbor adapter 使用 FF Dockerfile 和
`obsolete_reinsert.patch` 重建 task base，再隐藏验证测试入口和私有评测材料：

```text
agent input = task_base_full - validation test entry files - private/evaluation materials
```

含义是：

```text
new_commit
  目标版本行为基准。Belta 使用 new_commit 的源码上下文、运行环境和测试结果定义目标版本状态。

reinserted obsolete implementation residue
  将 old->new diff 中真实开发者移除的旧实现对象或片段重新插入到目标版本源码上下文中。

hidden tests
  测试文件默认不暴露给 agent，只在 evaluator 后台使用。
```

因此，agent 看到的是：

```text
目标版本项目上下文
+ 被重新插入的旧实现残留
- 测试文件和评测材料
```

agent 的任务是输出 patch，清理这些真实历史中被淘汰的旧实现残留。

删除后，agent 输出应保持与 `new_commit` 一致的目标版本行为。

为了让主 benchmark 更清晰、更有行为依据，Belta 主数据集只保留 behavior-sensitive obsolete implementation：

```text
new_commit 在 validation_test_universe 上全部通过
task_base 在同一批测试上出现失败
```

也就是说，重新插入的旧实现残留必须让目标版本行为发生可测试的偏离。若 `new_commit` 和 `task_base` 在 `validation_test_universe` 上都全部通过，则该 pair 不输出为正式 task。

## 4. Agent 可见 / 不可见内容

### Agent 可见

Agent 可以访问：

```text
task_base repo
  由 new_commit 项目上下文加上重新插入的 obsolete implementation residue 构成。
  测试入口、pair 级审计材料、oracle patch 和 evaluator 相关文件不对 agent 暴露。

任务描述 / 代码清理说明
```

任务描述使用 `src/belta/retire_task_construction.py` 中的 `TASK_INSTRUCTION`
固定模板，完整文本见 `DESIGN.md` 的 Task Instruction 节。Agent 结合现有源码、
调用关系和可用测试识别并清理旧实现残留，保留目标版本仍需使用的代码和行为。

### Agent 不可见

Agent 不可见：

```text
R_old 完整仓库状态
R_new 干净目标版本仓库状态
reinsert_extraction.patch
reinsert_targets.jsonl
obsolete_reinsert.patch
gold_cleanup.patch
hidden tests
evaluator
```

其中：

```text
reinsert_extraction.patch / reinsert_targets.jsonl / obsolete_reinsert.patch
  Belta 构造任务时使用的 pair 级审计、构造和答案相关材料。
  它们会泄露哪些旧实现残留被重新插入，不进入 agent workspace。

gold_cleanup.patch
  Belta 用于评估 agent 是否清理正确旧实现残留的 oracle/reference cleanup patch，不给 agent。

evaluator
  Belta 的后台评测器，用于运行 hidden tests、检查 agent 输出并计算结果。
  它属于评测基础设施，不进入 agent workspace。
```

## 5. 为什么不使用 old_commit 作为主输入

使用 `old_commit` 作为 agent 输入在历史上更真实，但它会把很多问题混在一起：

```text
新功能实现
环境迁移
依赖变化
测试框架变化
文档和配置变化
项目结构变化
旧实现退役
```

Belta 当前任务更聚焦：

```text
在目标版本上下文中，新实现已经存在；
agent 需要识别并清理被重新插入的旧实现残留。
```

这能把评估目标集中在 Retire Obsolete Implementation，而不是把任务变成完整版本迁移。

## 6. 为什么不使用 old_commit + added diff

Belta 不使用：

```text
old_commit + added implementation diff
```

作为主输入状态。

原因是 commit diff 经常是 line-level 修改，例如：

```diff
- old logic
+ new logic
```

如果只把新增行应用到 `old_commit`，可能得到一个不自然的状态：

```text
旧逻辑和新逻辑混在同一个函数或文件中
语法可能不稳定
语义可能不清楚
```

Belta 更倾向于从 `new_commit` 出发，重新插入真实演进中被移除的旧实现删除片段。

## 7. 为什么隐藏测试

Belta 不把测试文件暴露给 agent。

如果未来测试可见，agent 可能直接根据测试断言写 patch，而不是识别真实项目演进中被淘汰的旧实现残留。这会把任务变成 test-fitting，而不是软件演进理解。

测试只用于 evaluator 后台检查：

```text
agent 清理旧实现残留后，是否仍然保持目标版本行为。
```

这里的“测试”不是仓库理论上的全部测试，而是 FeatureFactory Stage2 在 `new_commit` 目标版本环境中确认通过的文件级测试结果集合：

```text
validation_test_universe =
  FF full validation 中文件级 status == passed 的测试文件
  即 full_report.file_results[].result.status == "passed"
  或等价导出的 test_results.jsonl 行级 status == "passed"
```

注意，这里的 passed 是文件级结果，不是 FF full_report 顶层 `status == "passed"`。一个 FF full validation 的顶层结果可以是 passed，但其中仍可能有文件级 failed 记录，例如测试文件被 collected 后全部 skipped。Belta 不把这类文件纳入 `validation_test_universe`。

后续 `new_commit` / `task_base` 的行为比较都限定在这批文件级 passed 的可复现测试上。

注意：测试不是本任务唯一正确性信号；旧实现残留清理仍然是核心目标。但主数据集要求旧实现残留能造成可测试的行为偏离，这样 agent 清理旧实现残留的同时，也是在恢复目标版本行为。

在数据筛选上，Belta 会比较 `new_commit` 与 `task_base` 在 `validation_test_universe` 上的通过情况：

```text
new_commit
  目标版本行为基准，应在 FF 环境中通过 validation_test_universe。

task_base
  在 new_commit 上重新插入 obsolete implementation residue 后的输入状态。
```

如果 `task_base` 相比 `new_commit` 出现测试失败，说明重新插入的旧实现残留暴露了行为差异，这类样本进入主数据集。

如果 `new_commit` 和 `task_base` 都全部通过测试，说明当前测试没有暴露旧实现残留的行为影响。Belta 跳过该 pair；如果同一 repo 还有后续 accepted pair，则继续尝试后续 pair。

## 8. 语义对象

语义对象表示 Python 仓库中的独立实现单元。

Belta 当前聚焦 Python，因此对象抽取也围绕 Python 源码结构定义。

当前 `DESIGN.md` 中的可实现主线围绕 Python AST 对象和删除片段展开：

```text
function
method
class as a removed_object construction unit
```

完整删除 class 时，Belta 可以把整个 class 作为 `removed_object` reinsert target 插回，以保持旧实现残留的语义边界。class 本身不作为 implementation unit；class 内的 function / method 会写入 `implementation_units.jsonl`。

整个文件被删除时，该 `D` 路径不进入 reinsert target 候选池。Belta 当前只处理在 `new_commit` 中仍然存在的文件内被完整删除的对象或局部删除片段。

概念上，后续也可以扩展到 `module-level variable / constant`、`import block` 等对象类型，但这些不属于当前实现主线。当前以 `DESIGN.md` 的 function / method reinsert target 规则为准。

对象标识用于描述一个实现对象在仓库中的位置和身份。概念上，它至少包含：

```text
object_id := (
  repo-relative path,
  object kind,
  qualified name,
  object start line,
  object end line
)
```

例如：

```text
src/example/runner.py :: method :: Runner.run :: 20-55
src/example/config.py :: function :: load_config :: 8-31
```

具体 AST 解析方式、边界处理和对象字段格式由 `DESIGN.md` 定义。

## 9. 对象抽取范围

Belta 基于 `old_commit -> new_commit` diff 抽取可重新插回目标版本的旧实现候选，而不是对整个仓库做全量对象匹配。

高层流程是：

```text
1. 从 old_commit -> new_commit diff 中只筛出 Python implementation_code 路径；测试、环境、文档和其他语言路径统一忽略，不在 candidate row 中细分。
2. 对 M 文件，先从 pure deletion hunk 抽取完整删除的 removed_object reinsert target。
3. 对 M 文件中未被 removed_object 覆盖的剩余删除行，只从 function / method 内抽取 removed_range reinsert target。
4. 对 D 文件直接忽略，不读取旧文件源码，也不产生 reinsert target。
5. Candidate Pair Construction 将抽取结果写入 reinsert_targets.jsonl，并将涉及的 function / method AST 对象合并写入 implementation_units.jsonl；同一对象中的多个删除片段只计一个 unit，同名但源码范围不同的定义分别计数。
6. Retire Task Construction 基于 reinsert_targets.jsonl 和 obsolete_reinsert.patch 生成 task_base，并在测试表现筛选后生成最终 oracle/reference cleanup patch。
```

Candidate Pair Construction 只输出 `implementation_code_paths`。测试文件由 FF Stage2 在固定 `new_commit` 上发现和运行；其中通过的测试入口组成后续 `validation_test_universe`，不依赖 candidate-stage 路径分类。

这样可以避免全仓库对象过多，也更符合当前任务目标：关注真实 diff 中被开发者移除、且重新注入后会影响目标版本行为的旧实现残留。

## 10. Oracle Patch

Oracle patch 表示正式任务中需要清理的旧实现残留：

```text
oracle = gold_cleanup.patch
```

`gold_cleanup.patch` 来自 task_base_full 与 new_commit 的 Git diff。`reinsert_targets.jsonl` 保留为 private audit material，用于解释 Belta 插回了哪些旧实现残留。

Evaluation 首先判断 agent 输出能否通过验证测试，再使用
`gold_cleanup.patch` 与 agent patch 计算删除行重合和 Patch 代码相似度。
`reinsert_targets.jsonl` 只用于私有审查，不作为评分答案。

## 11. 评测结果

评测直接记录实际结果字段，不再另设抽象评分维度：

- 功能结果：最终状态、二进制 Reward、Baseline 与 Agent 的通过/失败测试文件数、
  F2P、F2F、P2P、P2F，以及无回归修复率。
- Patch 审计：Git 新增/删除行和变更文件数、Gold 目标删除行、Agent 有效删除行、
  与 Gold 匹配行、Gold 覆盖率、Agent 精确率、Overlap F1 和 Patch 相似度。

其中 Reward 和无回归修复率反映测试结果；Gold/Patch 指标用于分析 Agent 修改了
什么以及与参考清理补丁的重合程度，不能替代测试，也不表示程序语义等价。

## 12. 行为约束

Agent 应主要修改生产源码。

测试文件、pair 级审计材料、oracle patch 和 evaluator 不进入 agent workspace。正式评测中，agent 不应通过修改测试、评测脚本或隐藏评测材料来影响结果。

更细的违规检测、无关修改检测和质量分析，留到 evaluator / scoring 设计阶段再定义。

## 13. 数据集与任务构造

候选版本对来自真实仓库历史：

```text
old_commit -> new_commit
```

Belta 先复用 Candidate Pair Construction 生成的 `accepted_candidates.jsonl` 作为候选版本对来源。`accepted_candidates.jsonl` 中的 pair 已经完成 diff 解析、路径分类、reinsert target extraction，并满足 Candidate Filtering。

随后，Retire Task Construction 会在这些 candidate pair 上构造正式任务。只有 task_base 构造、环境验证和测试表现比较都满足条件的 pair，才会成为最终任务。

Retire task 过滤条件包括：

```text
Retire Task Construction 能将 obsolete_reinsert.patch apply 到 new_commit 工作区，得到 task_base_full
new_commit 环境和 validation_test_universe 可复现
new_commit 在 validation_test_universe 上全部通过
task_base 在同一批测试上失败
pair 不包含过大的环境迁移或不可控 generated/vendor 变化
```

如果 `task_base` 与 `new_commit` 在 `validation_test_universe` 上都全部通过，说明 obsolete implementation residue 在当前测试下没有暴露行为差异。该 pair 不输出为正式 task；如果同一 repo 还有后续 accepted pair，则继续尝试后续 pair。

可以按以下维度做分析或分桶：

```text
implementation unit 数量
跨文件跨度
涉及对象类型
diff 大小
是否跨模块
hidden test 覆盖情况
```

一个 repo 可以产生多个版本对，也可以产生多个 behavior-sensitive tasks。Retire Task Construction 会先保留所有满足测试表现条件的任务；同一 repo 的任务数量控制和近重复处理留到获得 F2P/P2P 行为结果后统一进行，避免少数大 repo 主导最终评测。

## 14. 语言范围

Belta 当前聚焦 Python 语言任务。

原因：

```text
Python AST 能稳定抽取函数、类、方法、import、常量等对象。
Python 生态的测试框架和仓库结构相对适合自动化环境构造。
FF 对 Python 环境覆盖相对成熟。
```

因此，当前任务定义、对象抽取、task_base 构造和评估流程都以 Python 仓库为主。

多语言不作为当前 benchmark 目标。当前版本不承诺覆盖其他语言；若未来单独扩展，需要重新定义对应语言的：

```text
对象类型
qualified name
signature 归一化
AST / 语法树表示
import / module 边界
```

这些不进入当前版本主线。

## 15. 环境与测试基准

Belta 使用 FeatureFactory Stage2 为 `new_commit` 构造可复现测试环境。

`new_commit` 是目标版本行为基准。Belta 使用 FF Stage2 发现并验证的新版本测试作为 hidden tests 候选。

Belta 将 FF Stage2 full validation 中已经文件级通过的测试文件定义为：

```text
validation_test_universe
```

这是 Belta 当前任务使用的可复现测试全集，不等同于仓库所有可能测试，也不等同于 FF full_report 顶层 `status == "passed"` 覆盖到的全部 file_results。

对每个 candidate task，Belta 在同一套 FF 环境和 `validation_test_universe` 下比较：

```text
new_commit
task_base
```

如果 `new_commit` 在 `validation_test_universe` 上全部通过，而 `task_base` 在同一批测试上出现失败，说明重新插入的 obsolete implementation 暴露了行为差异，这类样本进入主数据集。

如果 `new_commit` 和 `task_base` 都通过测试，该 pair 不输出为正式 task；如果同一 repo 还有后续 accepted pair，则继续尝试后续 pair。

如果 FF Stage2 无法为某个 repo 构造可复现环境，或者 `new_commit` 本身无法通过验证，该 repo 不进入正式任务。

## 16. 核心流程与阶段命名

```text
Step 1: Repository Discovery
  使用 FeatureFactory Stage1 发现候选 GitHub repositories。
  输出 repo_discovery/repositories.jsonl。

Step 2: Candidate Pair Construction
  从真实 Git 历史中生成 old_commit -> new_commit candidate pairs。
  做 Git 状态解析、implementation path 筛选、reinsert target extraction 和 Candidate Filtering。
  为满足 Candidate Filtering 的 pair 生成 reinsert_extraction.patch、reinsert_targets.jsonl、implementation_units.jsonl 和 obsolete_reinsert.patch。
  输出 candidate_pairs/accepted_candidates.jsonl。

Step 3: Environment Construction
  为 new_commit 构造可复现测试环境。
  从共享 Git cache 执行 no-checkout clone，再 checkout 精确 new_commit；Git LFS
  使用当前 FF 代理下载真实对象，不跳过 smudge。
  从 FF full validation 中导出 validation_test_universe。
  同时保留规范 test_file 和 runner 实际使用的 target_selector。
  new_commit 是目标版本行为基准。

Step 4: Retire Task Construction
  从 accepted candidate pair 构造 Retire Obsolete Implementation task。
  本阶段包括：
    - 读取 Candidate Pair Construction 生成的 reinsert_targets.jsonl
    - 同 repo、同 new_commit、Patch 内容相同的 candidate 只保留 offset 最小的代表 Pair
    - 每个 repo 只构建或复用一个由完整 FF environment 构建上下文和平台确定的基础镜像
    - 每个 repo 先用一个容器顺序运行 validation_test_universe；完整通过后写入 repo baseline passed，作为 pair 执行和续跑的前提
    - 多个 repo 的 baseline 可以并行；baseline 通过后的 pairs 进入所有 repo 共享的全局并行池
    - 每个 Pair 从基础镜像启动全新容器，通过标准输入应用 obsolete_reinsert.patch，再逐文件调用 FF run_script.sh
    - 整套 validation timeout 为 1800 秒，不设置固定单文件 timeout；每个文件使用整套剩余时间，完整验证超时后 Baseline/Pair 运行级记为 infra_error，不计作 F2P
    - 对产生行为差异的 pair，在临时目录物化一次 task_base_full
    - 生成 gold_cleanup.patch 和验证测试快照后释放临时完整仓库，不持久化 agent repo 副本

  repo baseline 和 Pair 分别由 `max_concurrent_repo_baselines` 和
  `max_concurrent_pairs` 控制。单次 Retire 不自动重试；Baseline 和 Pair 的
  `infra_error` 在下次手动续跑时重新执行。并发只发生在不同 repo / Pair 之间；
  单个容器内的测试文件仍顺序执行，最终结果按去重后的 candidate 顺序汇总。

  分流规则：
    - new_commit 在 validation_test_universe 上全部通过，task_base 出现测试失败
        -> 保留为主数据候选，并继续验证同 repo 的后续 accepted pairs
    - new_commit 在 validation_test_universe 上全部通过，task_base 也全部通过
        -> 跳过该 pair；如果同 repo 还有后续 accepted pair，继续尝试
    - Environment Construction 没有可用 FF 环境，或 validation_test_universe 为空
        -> 拒绝该 candidate

Step 5: One-Task-per-Repo Quality Review
  Retire 保留全部技术有效 main-task Pair，不在运行时按 Patch 大小或编译状态自动选题。
  对每个 repo 的全部去重候选逐文件、逐目标块审查，再做同仓库横向比较。
  依据核心实现占比、修改边界、可做性、难度与价值选择一题；编译是否通过只作诊断。
  冻结最终题集后统计插回行数、实现单元数、文件数和集中度等分布。

Step 6: Harbor Export and Validation
  Harbor adapter 导出干净 FF 基础环境和私有 prepare 材料。
  每个新容器通过 healthcheck 后、Agent setup 前执行可信 prepare：应用 residue Patch、删除隐藏测试、重建单提交 Git，并归一化可见路径时间；prepare 材料随后删除。
  Retire Pair 逐文件状态作为冻结测试基线；发布冻结数据前独立运行完整 Oracle Job，确认 Gold Patch 在最终 Harbor 资源环境中可以恢复全部测试。
  需要时可独立运行 Nop，核对最终环境与 Retire 基线是否一致；Oracle、Nop 和模型 Agent 各自保存为独立 Harbor Job。

Step 7: Agent Run
  给 agent task_base 和任务描述。
  对最终审核决定保留的任务运行模型 Agent；任务范围直接使用 Harbor 原生任务选择参数控制。
  Agent 结束后由 Harbor collect hook 将 task_base 到最终仓库的 diff 保存为 artifact。

Step 8: Evaluation
  独立 Verifier 从干净 task base 应用 Agent patch artifact，再恢复并运行 hidden tests。
  记录修复测试、回归测试和无回归修复率；发生任何回归时该指标为 0，否则为修复测试数除以原始失败测试数。
  比较 Agent 与 Gold 删除的 task-base 非空源码行，记录 Gold 覆盖率、Agent 删除准确率和 F1。
  使用固定 Gold 语料高频 n-gram 表计算 Patch-only CrystalBLEU，作为不依赖模型和上下文的代码相似度辅助指标。
  Agent artifact 可由 Harbor regrade 重新评分，不需要重新运行 Agent。

Step 9: Exports
  导出最终 benchmark 数据集和评测所需 metadata。
```

其中，`Retire Task Construction` 是当前流程中的任务构造阶段。它复用 Candidate Pair Construction 生成的 pair 级材料和 Environment Construction 生成的测试环境，负责构造 task_base、生成 `gold_cleanup.patch` oracle/reference answer，并根据 `new_commit` / `task_base` 的测试表现筛选最终任务。

## 17. 论文表述

Belta 不应被表述为：

```text
Agents implement new_commit from old_commit.
```

也不应被表述为：

```text
Agents fix bugs by reading future tests.
```

更准确的表述是：

```text
Belta evaluates whether code agents can retire obsolete implementation residue
that real developers removed during repository evolution, while preserving
target-version behavior.
```

中文表述：

```text
Belta 从真实仓库演进中抽取被开发者淘汰的旧实现删除片段，
构造目标版本上下文中混入旧实现残留的代码清理任务，
评估 agent 是否能识别并清理这些过时实现，同时保持目标版本行为正确。
```
