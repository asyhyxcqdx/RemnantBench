# Belta Design

## Candidate Construction

### Repository Discovery

Belta 是唯一入口。用户通过 Belta 配置文件给出仓库筛选条件，Belta 后台以 library mode 调用 FeatureFactory Stage1，完成 GitHub repository discovery。用户不需要打开 FeatureFactory UI，也不需要手动启动 FeatureFactory server。

FeatureFactory Stage1 负责 GitHub Search API 查询、created 时间切窗、分页抓取、去重和落库。Belta 在 FF Stage1 job 完成后，把该 job 抓到的 repo 导出成自己的 `repositories.jsonl`。后续 Belta 阶段只消费这个文件，不再直接依赖 FF 数据库。

#### 配置方式

一次 Belta run 对应一个配置文件，也对应一次 FF Stage1 repo discovery。

Repo discovery 支持两种模式。

#### 模式 A：搜索模式

当 `target_repositories` 为空时，Belta 使用 GitHub Search 筛选仓库。

```yaml
project:
  run_name: python-mit

featurefactory:
  mode: library
  root: ../FeatureFactory
  database_url: postgresql+psycopg://belta_ff:belta_ff@127.0.0.1:55433/belta_ff
  auto_manage_postgres: true
  postgres_container_name: belta-ff-postgres
  postgres_data_dir: data/cache/featurefactory/postgres

repo_discovery:
  backend: featurefactory_stage1
  name: python-mit
  target_repositories: []

  languages: ["Python"]
  licenses: ["mit"]
  keywords: []
  created_after: "2020-01-01T00:00:00Z"
  created_before: "2024-01-01T00:00:00Z"
  pushed_after: null
  pushed_before: null
  stars_min: 50
  stars_max: null
  exclude_forks: true
  exclude_archived: true
  public_only: true
  sort: updated
  order: desc
  max_concurrent_partitions: 4

github:
  token: "..."
```

搜索模式下，FF Stage1 会将这些字段组合成 GitHub Search query。比如：

```text
public_only: true      -> is:public
exclude_forks: true    -> fork:false
exclude_archived: true -> archived:false
```

#### 模式 B：指定仓库模式

当 `target_repositories` 非空时，Belta 直接抓指定 repo。

指定仓库模式只需要替换 `repo_discovery` 配置：

```yaml
repo_discovery:
  backend: featurefactory_stage1
  name: debug-targets
  target_repositories:
    - pallets/flask
    - psf/requests

  max_concurrent_partitions: 4
```

指定仓库模式下，Belta/FF Stage1 不走 GitHub Search，不使用 `languages`、`licenses`、`created_after`、`stars_min`、`public_only` 等搜索筛选条件，而是直接调用 GitHub repository API：

```text
GET /repos/pallets/flask
GET /repos/psf/requests
```

本地运行时，token 可以直接写在配置里。`config.yaml` 和 `data/` 都放进 `.gitignore`。后续如果要发布配置模板，再改成环境变量或脱敏示例。

#### Run ID

Belta 自动生成 `run_id`：

```text
candidate_<YYYYMMDD_HHMMSS>_<run_name>
```

例如：

```text
candidate_20260526_213012_python-mit
```

`run_name` 来自配置文件。Belta 不支持手动传入 `--run-id`。`run_id` 始终由系统根据 `run_name` 和当前时间自动生成。如果目标目录已存在，直接报错，不覆盖。

#### 输出目录

本阶段只保留必要产物：

```text
data/
  runs/
    candidate_20260526_213012_python-mit/
      config.yaml
      repo_discovery/
        ff_job.json
        repositories.jsonl
```

`config.yaml` 是本次运行使用的配置副本。

`repo_discovery/ff_job.json` 记录 FF Stage1 job 的最小元信息：

```json
{
  "ff_job_id": "uuid...",
  "status": "completed",
  "repository_count": 875
}
```

`repo_discovery/repositories.jsonl` 是后续 Belta 阶段唯一消费的 repo 清单，一行一个 repo：

```json
{"github_repo_id":123,"full_name":"pallets/flask","html_url":"https://github.com/pallets/flask","clone_url":"https://github.com/pallets/flask.git","default_branch":"main","language":"Python","license":"mit","stars":69000,"forks":16000,"pushed_at":"..."}
```

#### 执行流程

1. Belta 读取配置文件。
2. Belta 根据 `project.run_name` 生成 `run_id`。
3. Belta 创建 `data/runs/<run_id>/`。
4. Belta 复制本次配置到 `data/runs/<run_id>/config.yaml`。
5. Belta 初始化 `FeatureFactoryStage1Backend`。
6. Belta 检查 PostgreSQL；如果未启动且 `auto_manage_postgres=true`，则自动启动本地 PostgreSQL 容器。
7. Belta 初始化 FeatureFactory 数据库表。
8. Belta 将 `repo_discovery` 配置转换为 FF `CrawlFilters`。
9. Belta 调用 FF Stage1 创建 crawl job，得到 `ff_job_id`。
10. FF Stage1 按模式执行 repo discovery：
    - 如果 `target_repositories` 为空，走 GitHub Search。
    - 如果 `target_repositories` 非空，逐个调用 GitHub repository API。
11. FF Stage1 把结果写入 PostgreSQL。
12. Belta 等待 FF Stage1 job 完成。
13. Belta 根据 `ff_job_id` 从 FF 数据库读取本次 job 抓到的 repo。
14. Belta 写出 `repo_discovery/ff_job.json`。
15. Belta 写出 `repo_discovery/repositories.jsonl`。
16. 后续 commit pair construction 只读取 `repositories.jsonl`。

#### 当前约束

- 一次 run 只对应一个 repo discovery 配置。
- 多个筛选方案用多个配置文件、多次 run 表达。
- 用户只运行 Belta；Belta 在后台调用 FF Stage1。
- 不引入复杂日志、`resolved.json` 或多 source merge。
- `repositories.jsonl` 是 repo discovery 和后续 Belta 流程之间的稳定边界。

### Commit Pair Construction

Commit Pair Construction 负责从 `repo_discovery/repositories.jsonl` 生成候选 old/new commit pair。本阶段只做 Git 历史扫描、diff 解析、路径分类、reinsert target extraction 和 Candidate Filtering，不运行测试，不调用 agent。

本阶段产物是可能进入 Retire Task Construction 的 candidate pair。一个 pair 只有在可插回旧实现目标足够、插回源码规模足够，并且成功生成 `obsolete_reinsert.patch` 时，才进入 `accepted_candidates.jsonl`。`obsolete_reinsert.patch` 的实际 apply 验证发生在后续 Retire Task Construction。

#### 配置

```yaml
candidate_pairs:
  offset_ranges:
    - start: 1
      stop: 100
      step: 1
    - start: 125
      stop: 750
      step: 25
  max_concurrent_repos: 4
  max_concurrent_repo_downloads: 4

  min_implementation_units: 1
  min_reinsert_source_lines: 5
  max_implementation_units: null
  max_reinsert_source_lines: null
```

`C0` 表示本 repo 在当前 run 首次处理时，增量刷新本地 bare cache 后
`refs/heads/<default_branch>` 指向的 commit，也就是本 repo 的
`new_commit`。该 SHA 一旦写入当前 run，后续阶段和同一 run 的续跑都固定使用它，
不会因远端分支继续变化而中途切换版本。

Belta 按 `offset_ranges` 声明的顺序，沿默认分支一阶主线历史枚举
`old_commit`。上面的配置先密集检查近期历史，再稀疏检查更早历史：

```text
C1   -> C0
C2   -> C0
...
C100 -> C0
C125 -> C0
C150 -> C0
...
C750 -> C0
```

字段含义：

- `offset_ranges`：一个或多个扫描区间。每段包含 `start`、`stop` 和
  `step`，区间包含两端；所有区间必须产生严格递增且不重复的 offset。
  默认值为 `[{start: 1, stop: 200, step: 1}]`。
- `min_implementation_units`：一个 pair 至少需要涉及多少个 function / method implementation units，默认 `1`。
- `min_reinsert_source_lines`：一个 pair 至少需要插回多少行旧实现源码，默认 `5`。
- `max_implementation_units`：一个 pair 最多允许涉及多少个 function / method implementation units。默认 `null`，表示不设置上限。
- `max_reinsert_source_lines`：一个 pair 最多允许插回多少行旧实现源码。默认 `null`，表示不设置上限。

如果某段配置为 `start=1, stop=16, step=5`，则 Belta 只枚举：

```text
C1  -> C0
C6  -> C0
C11 -> C0
C16 -> C0
...
```

没有被 `offset_ranges` 枚举到的 pair 不进入 Candidate Filtering，也不会写入
`accepted_candidates.jsonl` 或 `rejected_candidates.jsonl`。这不是 skipped
candidate，而是配置本身没有要求扫描这些 offset。

如果某个 repo 的主线历史在到达后续 offset 前已经耗尽，Belta 停止扫描该
repo。只要 Git/cache/history 过程没有出错，该 repo 仍然记为 `completed`。

#### Repo Cache 输入

Candidate Pair Construction 从 `repo_discovery/repositories.jsonl` 读取以下字段：

```json
{
  "github_repo_id": 589831718,
  "full_name": "Comfy-Org/ComfyUI",
  "clone_url": "https://github.com/Comfy-Org/ComfyUI.git",
  "default_branch": "master"
}
```

字段用途：

- `github_repo_id`：用于后续在 FF 数据库中查找 `github_repositories.id`，并贯穿写入 candidate pair 产物。
- `full_name`：用于生成 `repo_key` 和 cache 路径。
- `clone_url`：仅在本地 cache 缺失或无效时用于 `git clone`。
- `default_branch`：用于 clone 默认分支、验证本地分支 ref，并读取本地主线 commit 历史。

Repo Cache 子步骤只使用：

```text
full_name
clone_url
default_branch
```

`github_repo_id` 不参与 Git 同步，但必须透传到 `candidate_pairs/accepted_candidates.jsonl`，供 Environment Construction 使用。

如果上述任一字段缺失或为空，该 repo 不进入 candidate pair 生成，只在 `candidate_pairs/repo_results.jsonl` 中记录 repo 级结果。

#### Repo Cache 路径

Belta 根据 `full_name` 生成 `repo_key`：

```text
Comfy-Org/ComfyUI -> Comfy-Org__ComfyUI
```

并生成 cache 路径：

```text
data/cache/repos/<owner>/<repo>.git
```

例如：

```text
data/cache/repos/Comfy-Org/ComfyUI.git
```

Repo Cache 是跨 run 复用的本地 Git object store，不放在
`data/runs/<run_id>/` 里面。每个新 run 首次处理 repo 时增量刷新默认分支，
并在 `candidate_pairs/repo_results.jsonl` 中固定记录本轮选中的 commit SHA。

第一次扫描时：

```bash
git clone --bare --single-branch --no-tags --branch <default_branch> <clone_url> <cache_dir>
```

后续新 run 复用 cache 时，先验证 bare repo 和 `origin`，再增量 fetch 远端
默认分支并更新本地默认分支 ref。若 fetch 失败，本 repo 记为
`failed/git_cache_failed`，保留 cache 供以后重试，但不得用旧 ref 冒充本轮最新版。
同一 run 已完成的 repo 直接复用已落盘结果，不再 fetch。

bare cache 只用于 Git 历史、commit、diff 查询。本阶段不 checkout 工作区源码。

#### Commit Pair 生成

对每个 repo：

1. 从 `repo_discovery/repositories.jsonl` 读取 repo 元数据。
2. 准备或复用 `data/cache/repos/<owner>/<repo>.git` 本地快照。
3. 读取默认分支一阶主线 commit 历史。
4. 设本地默认分支头 commit 为 `C0`。
5. 按 `offset_ranges` 生成严格递增的 offset 序列。
6. 对每个存在的 `C<offset>` 生成候选 pair：`old_commit=C<offset>`，`new_commit=C0`。
7. 对每个候选 pair 读取文件状态，先按路径排除 ignored paths，只对 Python implementation candidates 读取必要版本源码并做 AST 确认。
8. 将同一 pair 的全部 `M implementation_code` 路径交给一次组合 histogram diff，并按文件拆分结果后执行 reinsert target extraction。
9. 生成 `reinsert_targets.jsonl`、`implementation_units.jsonl` 和筛选统计。
10. 应用 Candidate Filtering 的轻量阈值过滤。
11. 只有满足阈值的 pair 才生成 Git 审计 patch 和 `obsolete_reinsert.patch`。
12. 满足 Candidate Filtering 的 pair 写入 `candidate_pairs/accepted_candidates.jsonl`。
13. 不满足 Candidate Filtering 的 pair 写入 `candidate_pairs/rejected_candidates.jsonl`。
14. 如果主线历史耗尽，停止扫描该 repo。
15. 每个输入 repo 的最终处理概况写入 `candidate_pairs/repo_results.jsonl`。

主线 commit 历史只看默认分支的一阶主线历史，避免 merge commit 引入旁支历史。`C0` 是本地 cache 默认分支头，`C1` 是 `C0` 的前一个主线 commit。

#### 并行执行与续跑

Candidate Pair Construction 按 repo 粒度执行。每个 repo 的 Git cache 准备、commit history 扫描、diff 解析、路径分类、reinsert target extraction 和 Candidate Filtering 都可以独立完成。

为提高效率和增强中断恢复能力，本阶段采用 repo 级并行和 repo 级落盘。

配置增加：

```yaml
candidate_pairs:
  max_concurrent_repos: 4
  max_concurrent_repo_downloads: 4
```

字段含义：

- `max_concurrent_repos`：同时运行多少个 repo 处理流程。默认 `4`。
- `max_concurrent_repo_downloads`：其中最多允许多少个 repo 同时执行远程
  Git 操作，包括已有 cache 的增量 fetch，以及缺失或无效 cache 的 clone。
  默认 `4`。

这两个限制形成流水线，而不是两个必须串行完成的阶段：

```text
有效 bare cache
  -> 等待下载名额
  -> 增量 fetch 及远程重试
  -> 释放下载名额
  -> 构造 candidate pairs

缺失或无效 bare cache
  -> 等待下载名额
  -> clone 及远程重试
  -> 释放下载名额
  -> 立即构造 candidate pairs
```

因此远程 Git 操作和本地 pair 分析可以重叠执行，但同时进行的 fetch/clone
不会超过 `max_concurrent_repo_downloads`。一次远程操作的全部代理重试共用
同一个下载名额。

##### repo 级输出

除最终三个汇总文件外，Belta 会为每个 repo 写一份独立输出：

```text
data/runs/<run_id>/
  candidate_pairs/
    repo_outputs/
      <repo_key>/
        repo_result.json
        accepted_candidates.jsonl
        rejected_candidates.jsonl
```

含义：

- `repo_result.json`：该 repo 的 repo 级处理结果，字段与最终 `repo_results.jsonl` 中该 repo 的一行一致。
- `accepted_candidates.jsonl`：该 repo 满足 Candidate Filtering 的 candidate pair。
- `rejected_candidates.jsonl`：该 repo 不满足 Candidate Filtering 的 candidate pair。

##### 单 repo 写入语义

单个 repo 的结果不边筛边追加写入。

Belta 先在内存中完成该 repo 的全部处理：

```text
Git cache 准备或复用
commit history 读取
candidate pair 枚举
diff 解析
路径分类
reinsert target extraction
Candidate Filtering
```

然后一次性写入：

```text
candidate_pairs/repo_outputs/<repo_key>/
  repo_result.json
  accepted_candidates.jsonl
  rejected_candidates.jsonl
```

因此，`repo_outputs/<repo_key>/` 表示该 repo 有一份可检查的 repo 级输出。但续跑时不能只看目录是否存在，必须检查文件完整性和 `status`。

##### 续跑规则

续跑使用同一个命令：

```bash
uv run belta candidate construct-pairs --run-dir data/runs/<run_id>
```

Belta 对每个 repo 检查：

```text
candidate_pairs/repo_outputs/<repo_key>/repo_result.json
candidate_pairs/repo_outputs/<repo_key>/accepted_candidates.jsonl
candidate_pairs/repo_outputs/<repo_key>/rejected_candidates.jsonl
```

只有同时满足以下条件，才跳过该 repo：

```text
repo_result.json 存在
accepted_candidates.jsonl 存在
rejected_candidates.jsonl 存在
repo_result.json 能解析成合法 JSON
repo_result.status 是 completed 或 skipped
如果 repo_result.status 是 completed，repo_result 中记录的 offset/min/max candidate_pairs 配置与当前 config 一致
```

`max_concurrent_repos` 和 `max_concurrent_repo_downloads` 只控制调度，不改变
candidate 语义，因此不参与 completed repo 输出的复用匹配。

含义：

```text
completed:
  repo 已经按当前 candidate_pairs 配置完整完成 candidate pair construction，不重跑。

skipped:
  repo 输入字段缺失，无法开始处理；重跑通常不会改变结果，因此不重跑。
```

以下情况会删除该 repo 的输出目录并重跑：

```text
repo_outputs/<repo_key>/ 不存在
repo_outputs/<repo_key>/ 中缺少任一必要文件
repo_result.json 不是合法 JSON
repo_result.status 是 failed
repo_result.status 不是 completed/skipped/failed
```

如果用户想强制重跑某个已经 completed 或 skipped 的 repo，可以手动删除该 repo 输出目录：

```bash
rm -rf data/runs/<run_id>/candidate_pairs/repo_outputs/<repo_key>
```

然后重新执行：

```bash
uv run belta candidate construct-pairs --run-dir data/runs/<run_id>
```

##### Git cache 处理

Git cache 位于：

```text
data/cache/repos/<owner>/<repo>.git
```

Git cache 是跨 run 复用的本地 Git object store，不属于某一次 run。每个新 run 首次处理某个 repo 时会增量刷新默认分支；同一 run 已完成的 repo 直接复用已经落盘的 commit SHA，不会再次刷新。

对每个 repo，Belta 按下面流程处理 cache：

```text
如果 cache 不存在:
  执行 git clone --bare。
  如果 clone 成功:
    使用该 cache。
  如果 clone 失败:
    该 repo 写入 failed/git_cache_failed，等待下次续跑。

如果 cache 存在:
  检查 cache 是否可继续使用。
  检查包括:
    1. 是否是 bare repository。
    2. 是否存在 origin remote。

  如果检查通过:
    增量 fetch 远端默认分支。
    将 refs/heads/<default_branch> 更新为本次 fetch 的结果。
    将 bare repo HEAD 指向 refs/heads/<default_branch>。
    C0 取刷新后的 refs/heads/<default_branch> commit，并写入本轮结果。

  如果 fetch 失败:
    保留既有 cache，不回退使用旧 commit。
    该 repo 写入 failed/git_cache_failed，等待本 run 续跑或下个 run 重试。

  如果检查失败:
    删除该 repo cache。
    重新执行 git clone --bare。
    如果 clone 成功:
      使用该 cache。
    如果 clone 失败:
      该 repo 写入 failed/git_cache_failed，等待下次续跑。
```

基本结构检查使用：

```bash
git --git-dir=<cache_dir> rev-parse --is-bare-repository
git --git-dir=<cache_dir> remote get-url origin
```

已有 cache 的增量刷新使用：

```bash
git --git-dir=<cache_dir> fetch --prune --no-tags <remote_url> \
  +refs/heads/<default_branch>:refs/heads/<default_branch>
git --git-dir=<cache_dir> remote set-url origin <clone_url>
git --git-dir=<cache_dir> show-ref refs/heads/<default_branch>
git --git-dir=<cache_dir> symbolic-ref HEAD refs/heads/<default_branch>
```

显式 `+` 允许远端默认分支发生 force-push 后，cache 仍准确跟随远端。fetch 只下载本地尚未拥有的 Git objects，不会重新下载整个仓库。

clone 使用：

```bash
git clone --bare --single-branch --no-tags --branch <default_branch> <remote_url> <cache_dir>
git --git-dir=<cache_dir> remote set-url origin <clone_url>
git --git-dir=<cache_dir> symbolic-ref HEAD refs/heads/<default_branch>
```

`clone_url` 是 GitHub 官方 canonical URL，来自 `repo_discovery/repositories.jsonl`。Belta 始终把 cache 的 `origin` 记录为 `clone_url`。

`remote_url` 是本次 clone 实际访问的 URL。默认等于 `clone_url`。如果本机 `.env` 设置了 `BELTA_GITHUB_PROXY_PREFIX`，并且 `clone_url` 是公开 HTTPS GitHub URL，则：

```text
remote_url = 当前代理前缀 + clone_url
```

例如：

```text
clone_url  = https://github.com/sanic-org/sanic.git
remote_url = https://ghfast.top/https://github.com/sanic-org/sanic.git
```

`BELTA_GITHUB_PROXY_PREFIX` 接受单个 URL、逗号分隔列表或 JSON 字符串列表，例如：

```dotenv
BELTA_GITHUB_PROXY_PREFIX='["https://ghfast.top/","https://gh-proxy.com/","https://ghproxy.vip/"]'
```

配置顺序就是每个 repo 独立使用的代理优先级。每次 clone 或 fetch 在开始时读取一份固定代理列表，遇到可重试网络错误时按顺序切换；代理 URL 返回 HTTP 403 或 Git dumb-http 不兼容错误时，也只把当前代理视为失败并继续。不同 repo 的失败不会改变彼此的代理顺序。每个代理最多尝试一次；代理尝试全部失败后，最后使用官方 GitHub URL 直连一次。

这表示 cache 记录和仓库身份始终使用官方 GitHub URL，但实际网络下载优先按配置顺序使用代理。代理 URL 不写入 `origin`。

Git remote 操作使用有限 retry。环境变量：

```text
BELTA_GITHUB_PROXY_PREFIX
BELTA_GIT_REMOTE_MAX_ATTEMPTS
BELTA_GIT_REMOTE_RETRY_DELAY_SECONDS
BELTA_GIT_REMOTE_TIMEOUT_SECONDS
```

默认值：

```text
BELTA_GITHUB_PROXY_PREFIX 未设置 -> 直连 GitHub
BELTA_GIT_REMOTE_MAX_ATTEMPTS 未设置 -> 3
BELTA_GIT_REMOTE_RETRY_DELAY_SECONDS 未设置 -> 3
BELTA_GIT_REMOTE_TIMEOUT_SECONDS 未设置 -> 600
```

retry 和单次超时用于首次 clone、无效 cache 的重新 clone，以及已有 cache 的增量 fetch。没有配置代理时，最多尝试 `BELTA_GIT_REMOTE_MAX_ATTEMPTS` 次直连；配置代理时，按配置顺序将每个代理最多尝试一次，之后再进行一次直连。常见网络错误可以 retry，例如 `SSL_ERROR_SYSCALL`、`RPC failed`、`early EOF`、`unexpected disconnect`、`Failed to connect`、`Connection reset` 和 timeout。远程操作最终仍然必须成功；Belta 不因为 retry、timeout 或 proxy 改变 Git cache 成功/失败语义。

每次远程尝试都会向当前运行日志写一行：

```text
git_remote repo=<owner/repo> route=<proxy-or-direct> attempt=<n/total> status=<status> elapsed=<seconds>
```

该日志只用于比较代理成功率和耗时，不新增 candidate 或 pair 产物字段。

每次 clone 失败或超时后都会立即删除该次尝试留下的无效半成品目录，避免下一次 `git clone` 因目标目录非空而立即失败；最后一次尝试失败也不遗留半成品。已有 cache 的 fetch 失败时不会删除 cache，但该 repo 也不会使用旧分支指针继续构造 Candidate。

如果 clone 失败，该 repo 写入：

```json
{
  "status": "failed",
  "status_code": "git_cache_failed",
  "error": "fatal: ..."
}
```

`error` 保存最后一次 Git 尝试的真实错误。默认每次远程操作最多运行 600 秒；超时后按同一 retry 规则处理，避免少量远程仓库无限拖住整个 run。

如果已有 cache 的任一检查失败，说明该目录不能作为当前 repo 的完整本地快照继续使用。Belta 会删除它并重新 clone；不会对无效 cache 执行 fetch 修复。

有效 cache 在每个新 run 首次处理 repo 时都会增量访问远端，刷新默认分支后
确定 `new_commit=C0`。当前 run 一旦记录该 SHA，Candidate、Environment、Retire
和后续评测都固定使用它；下一个新 run 才会再次 fetch 并可能选择更新的 commit。

最终原则：

```text
当前 run 已完成 repo -> 复用已落盘 commit，不 fetch。
新 run 首次遇到有效 cache -> 增量 fetch；失败则 failed/git_cache_failed，不使用旧 ref。
缺失 cache -> clone。
无效 cache -> 删除后重新 clone。
clone 最终失败 -> failed/git_cache_failed。
```

##### 最终汇总

所有 repo 处理结束后，Belta 从 repo 级输出汇总生成最终三个文件：

```text
candidate_pairs/
  repo_results.jsonl
  accepted_candidates.jsonl
  rejected_candidates.jsonl
```

这三个文件每次命令结束时都会全量重建并覆盖，不做 append。

汇总规则：

```text
repo_results.jsonl:
  按 repositories.jsonl 中 repo 顺序汇总每个 repo_outputs/<repo_key>/repo_result.json。

accepted_candidates.jsonl:
  按 repositories.jsonl 中 repo 顺序拼接每个 repo_outputs/<repo_key>/accepted_candidates.jsonl。

rejected_candidates.jsonl:
  按 repositories.jsonl 中 repo 顺序拼接每个 repo_outputs/<repo_key>/rejected_candidates.jsonl。
```

最终语义：

```text
repo_outputs/ 是续跑状态源。
repo_results.jsonl、accepted_candidates.jsonl、rejected_candidates.jsonl 是最终汇总视图。
```

因此最终主产物仍然和本阶段原设计一致，只是增加了 repo 级中间产物用于并行执行和续跑。

#### Diff 生成规则

Belta 会使用两类 diff：

1. 状态 diff：用于判断哪些文件发生变化及其 A/D/M/T 状态，并筛出 `implementation_code_paths`。
2. patch diff：用于生成 `reinsert_extraction.patch` 和 `obsolete_reinsert.patch`。真正构造 `task_base` 时使用 `obsolete_reinsert.patch`。

两类 diff 必须使用同一套语义：

```text
old_commit -> new_commit
--no-renames
repo-relative path
```

##### 统计/分类 diff

统计和路径分类阶段使用：

```bash
git --git-dir=<cache_dir> diff --name-status -z --no-renames <old_commit> <new_commit>
```

`--name-status` 用于读取文件状态：

```text
A = added，新文件
D = deleted，删除文件
M = modified，修改文件
T = type changed，文件类型变化
```

`-z` 表示使用 NUL 分隔输出，避免路径里包含空格、tab、中文等字符时解析出错。

Belta 不额外运行 `git diff --numstat`。Git diff 的总新增/删除行数不参与当前 Candidate Filtering；当前行数阈值使用 reinsert target extraction 后得到的 `reinsert_source_lines`，它只统计实际准备插回 task base 的旧实现源码。

#### Git 源码与 Python 分析上下文

同一 repo 的所有 candidate pair 共享固定的 `new_commit=C0`。Belta 为每个并行 repo worker 创建独立的 `RepoAnalysisContext`，并让该 repo 的全部 offset 共享源码和 Python 分析结果。

读取源码时，Belta 为该 repo 保持两个长期运行的 Git 进程：

```text
git cat-file --batch-check
  将 commit:path 解析为 Git blob ID。

git cat-file --batch
  只读取尚未分析过的 blob 内容。
```

`RepoAnalysisContext` 按 blob ID 缓存源码、AST parseability、function / method / class 对象摘要和 `setup.py` 判断。同一内容即使出现在不同 commit 或 path 中，也只读取和解析一次；一次 AST parse 同时产生上述全部 Python 分析结果。

`PairAnalysisContext` 只限制当前 pair 可以访问的 `old_commit` / `new_commit`，不再维护独立的 old-version 源码缓存。repo 完成后关闭两个 Git 进程并释放整个 `RepoAnalysisContext`。

该运行时分析上下文与磁盘上的 `data/cache/repos/<owner>/<repo>.git` bare cache 不同：bare cache 长期保存 Git objects；分析上下文只存在于当前 Python 进程内，不新增持久化产物。不同 repo 不共享分析缓存。

##### patch diff

后续生成 patch 时使用 Git 生成，不手写 patch。

完整 `old_commit -> new_commit` diff 不作为标准产物落盘。需要临时审计时，可由 pair metadata 中的 commit SHA 和 bare cache 按需生成：

```bash
git --git-dir=<cache_dir> diff --binary --no-renames <old_commit> <new_commit>
```

完整实现侧 diff 同样不作为标准产物落盘。需要临时审计时，可按 `implementation_code_paths` 生成：

```bash
git --git-dir=<cache_dir> diff --binary --no-renames <old_commit> <new_commit> -- <implementation_paths...>
```

`--binary` 让 Git patch 能表达 binary 变化，但 Belta 不主动收 binary 任务。binary 和非 Python 路径在 implementation path 筛选中直接归为 `ignored`。

##### rename / move

Belta 不使用 Git 的 rename 识别，而是统一使用 `--no-renames`。

这样 rename/move 会被展开成 delete + add。

例如：

```text
src/helper.py -> tests/helper.py
```

会被视为：

```text
D  src/helper.py
A  tests/helper.py
```

然后分别分类：

```text
D src/helper.py    -> ignored
A tests/helper.py  -> ignored
```

这样更符合 `old_commit -> new_commit` 语义，也避免跨类别 rename 难以归类。

##### 路径分类规则

对每个 diff path：

```text
A 新增文件：只确认 new side，且不产生 reinsert target。
D 删除文件：直接 ignored，不读取源码，不产生 reinsert target。
M 修改文件：确认 old/new 两边，可产生 removed_object / removed_range target。
T 类型变化：直接 ignored，不读取源码，不产生 reinsert target。
```

输出路径统一使用 repo-relative path。

#### Implementation Path 筛选

Belta 当前只构造 Python 任务。Candidate Pair Construction 不再把 changed paths 细分为 `test_related`、`environment` 和 `excluded`，而是只判断路径是否为：

```text
implementation_code
ignored
```

只有 `implementation_code` 进入 reinsert target extraction。对外产物只记录：

```json
{
  "implementation_code_paths": [
    "src/example/app.py",
    "src/example/utils.py"
  ]
}
```

`ignored` 只表示该路径不参与 Candidate Pair Construction 的实现侧抽取，不写入 candidate row 或 pair metadata。完整 old-to-new 变化不重复物化到每个 pair 目录；需要审计时可由 bare cache 和 `old_commit` / `new_commit` 按需生成。

测试文件不由本阶段的路径规则发现。Environment Construction 将 repo 和固定 `new_commit` 交给 FF Stage2；FF 输出 `test_results.jsonl`，Retire Task Construction 再将其中 `status=passed` 的测试文件组成 `validation_test_universe`。因此删除 candidate-stage 的 `test_related` 输出不会影响 hidden tests。

##### 路径优先过滤

Belta 必须先根据 repo-relative path 排除明显不可能是 Python 实现源码的路径，再决定是否读取 Git blob。以下路径直接归为 `ignored`，不得为分类而执行 `git show <commit>:<path>`：

```text
测试目录和明确测试文件
docs / examples / CI / vendor / generated / cache / build output
依赖、打包、构建和运行环境配置
图片、binary、文档和非 Python 源码后缀
```

当前明确忽略的测试侧路径包括：

```text
tests/
Tests/
test/
testing/
unittest/
unittests/
unit_tests/
module_testing/
spec/
specs/
__tests__/
fixtures/
fixture/
testdata/
snapshots/
snapshot/
golden/
expected/

test_*.py
*_test.py
*Tests.py
test.py
tests.py
conftest.py
pytest.ini
tox.ini
noxfile.py
```

测试路径匹配先统一大小写，并只在 `/`、`_`、`-` 或 CamelCase 形成的明确词边界上识别 `test` / `tests` / `testing` / `fixture` 角色。无分隔符但以 `test` 开头的文件或目录也按保守策略忽略。因此 `test_utils/`、`test_repos/`、`beartype_test/`、`testutil.py`、`testclient.py` 和 `ActionTests.py` 都不会进入 target extraction。分类器禁止搜索任意字符串子串，所以 `contest.py`、`backtest_config.py`、`latest/`、`litestar/`、`shortest_paths/`、`primetest.py` 和 `stubtest.py` 不会仅因字符组合中出现 `test` 而被误排。

示例、演示和 benchmark 角色只按完整目录名或完整文件 stem 忽略：

```text
sample / samples
example / examples
demo / demos
benchmark / benchmarks
tutorial / tutorials
```

因此 `samples/train.py` 和 `examples.py` 归为 `ignored`，而 `sample_rate.py` 不会仅因复合名称中出现 `sample` 而被排除。

当前明确忽略的其他噪声规则继续由实现中的路径常量维护，包括 `docs/`、`.github/`、`vendor/`、`generated/`、`node_modules/`、`dist/`、`build/`、README/CHANGELOG/LICENSE、`*.md`、`*.rst`、binary 后缀，以及当前 Python-only benchmark 不处理的其他语言源码。

项目级环境文件也直接忽略，例如：

```text
pyproject.toml
setup.cfg
MANIFEST.in
requirements*.txt
Pipfile / Pipfile.lock
poetry.lock
uv.lock
environment.yml / environment.yaml
Makefile
CMakeLists.txt
Dockerfile
docker-compose.yml / docker-compose.yaml
```

##### 需要内容确认的候选

只有以下路径需要从 bare Git cache 读取必要版本源码：

```text
普通 .py 文件
任意位置的 setup.py
无后缀 Python shebang 候选
```

`setup.py` 使用 AST 判断是否调用来自 `setuptools` 或 `distutils.core` 的 packaging `setup(...)`。命中 packaging 入口时归为 `ignored`；普通项目源码中的 `setup` 函数、`setup_logging()` 或 `server.setup()` 不触发该规则。

测试和其他明确非实现路径在读取 Git blob 和执行 reinsert target extraction 之前就归为 `ignored`。Belta 不先生成 target 再删除可疑 target；`implementation_units_count`、`reinsert_source_lines` 和 target 文件数只对首次进入 extractor 的干净 `implementation_code` 路径计算一次。

无后缀文件必须同时满足 Python shebang 和 AST 可解析，例如：

```text
#!/usr/bin/env python
#!/usr/bin/env python3
#!/usr/bin/python
#!/usr/bin/python3
```

##### Git 状态与 AST 确认

Git 文件状态继续保留，但不再决定非实现路径的细分类别：

```text
A added
  只读取 new_commit:path。
  AST parse 成功后可记录为 implementation_code，但不产生 reinsert target。

D deleted
  直接归为 ignored，不读取源码，不产生 reinsert target。

M modified
  读取 old_commit:path 和 new_commit:path。
  两边 AST parse 都成功时记录为 implementation_code，并进入 removed_object / removed_range extraction。

T type changed
  直接归为 ignored，不读取源码，不产生 reinsert target。
```

AST 在本步骤只确认文件是否为 Belta 当前可处理的 Python 源码；具体旧实现片段的语义归属由 Reinsert Target Extraction 完成。Belta 不使用 `def`、`class` 或 `import` 字符串搜索代替 Python AST。

#### Reinsert Target Extraction

完成 diff 生成和路径分类后，Belta 对 `implementation_code` diff 做 reinsert target extraction。

本步骤的目的，是在进入 Environment Construction 前过滤掉无法构造 Retire Obsolete Implementation task 的 pair，并生成后续构造 task_base 所需的核心输入 `obsolete_reinsert.patch`。同时，Belta 也会落盘 `reinsert_extraction.patch`、`reinsert_targets.jsonl` 和 `implementation_units.jsonl`，用于人工审查旧实现插回内容是否来自真实实现侧演进。

`reinsert_targets.jsonl` 记录的是 old_commit -> new_commit 中可重新插回 `new_commit` 的旧实现候选目标。Candidate Pair Construction 阶段只称为 reinsert targets；Retire Task Construction 会用这些材料构造 task_base；最终 oracle/reference answer 是 `gold_cleanup.patch`。

本阶段统一记录两类 reinsert target：

```text
removed_object
  文件在 new_commit 中仍存在。
  old_commit -> new_commit 中完整删除了一个 Python AST 对象。
  支持完整 function / method / class 删除。
  Belta 后续把该对象的完整 source_text 插回同一路径的新版本文件。

removed_range
  文件在 new_commit 中仍存在。
  old_commit -> new_commit 中 function / method 内出现 pure deletion hunk。
  Belta 后续把这些删除片段插回同一路径的新版本文件。
```

生成 `reinsert_targets.jsonl` 不需要 checkout 工作区，也不需要运行测试。Belta 只从 bare repo cache 读取已经通过 implementation path 筛选的必要源码内容；明显 ignored paths 不执行以下读取：

```bash
git --git-dir=<cache_dir> show <old_commit>:<path>
git --git-dir=<cache_dir> show <new_commit>:<path>
```

其中：

```text
A 新增文件:
  不产生 reinsert target。

M 修改文件:
  读取 old_commit:path 和 new_commit:path。
  先从 pure deletion hunk 中抽取完整删除的 removed_object target。
  已被 removed_object 覆盖的删除行不再生成 removed_range。
  剩余删除行如果落在 function / method 内，才抽取 removed_range target。

D 删除文件:
  直接 ignored，不读取源码，不产生 reinsert target。

T 类型变化:
  直接 ignored，不产生 reinsert target。
```

pure deletion hunk 的定义：

```text
hunk 中有 old_commit 侧删除行。
hunk 中没有 new_commit 侧新增行。
```

`removed_range` 抽取将同一 pair 的全部 `M implementation_code` 路径交给一次组合 histogram diff：

```bash
git --git-dir=<cache_dir> -c core.quotePath=false diff \
  --histogram --unified=0 --no-renames \
  <old_commit> <new_commit> -- <modified_implementation_paths...>
```

Git 对每个文件独立计算 diff，并以 `diff --git a/<path> b/<path>` 分隔文件 section。Belta 将组合输出按已知 repo-relative path 拆分，同一文件的 hunk 不会与其他文件混合。空格、Unicode 和 Git C-style quoted path 必须由解析器正确处理。

`--histogram` 用于减少重复行、相似函数和大块 replacement 被拆成局部 pure deletion hunk 的情况；`--unified=0` 用于让删除行范围尽量精确。一个 pair 正常只启动一次 histogram Git 子进程。同一份组合 diff 一方面用于解析 pure deletion hunk 和抽取 reinsert targets，另一方面在 pair 通过轻量阈值后原样写入 `reinsert_extraction.patch`。

如果同一个 hunk 中既有 `-` 行又有 `+` 行，则认为是 replacement / update hunk，不产生 reinsert target。这样避免把 `old_logic()` 和 `new_logic()` 同时放回同一位置，构造出不自然的目标版本状态。

`removed_object` / `removed_range` 抽取流程：

```text
1. 从 M 文件的 implementation_code diff hunk 中找到 pure deletion hunk。
2. 读取 old_commit 中对应 Python 文件源码。
3. 使用 Python AST 找到 pure deletion hunk 完整覆盖的 function / method / class。
4. 如果完整覆盖 AST 对象，优先写入 removed_object target，并从 old_commit 源码中截取完整 source_text。
5. 完整对象范围包含 decorators。对象起始行取 node.lineno 和 node.decorator_list 中最早 decorator.lineno 的最小值。
6. 已经被 removed_object 覆盖的删除行，不再参与 removed_range 抽取。
7. 剩余删除行使用 Python AST 定位到 function / method target。
8. removed_range 只保留仍能在 new_commit 同路径中找到同名 function / method，且插入位置仍落在该 function / method 范围内的片段。
9. 同一个 function / method AST 对象被多处 pure deletion 删除行命中时，只写一条 removed_range target，并在该记录的 removed_ranges 中保留所有删除片段。
10. 删除行无法定位到 function / method target，或 new_commit 中缺少对应上下文时，不写入 reinsert_targets.jsonl。
11. 根据删除行范围，从 old_commit 源码中原样截取每个删除片段，写入 removed_ranges[].source_text。
```

function / method 规则：

```text
顶层 function:
  记录为 function。

class method:
  记录为 method，qualified_name 带 class 名，例如 Runner.run。

nested function / inner method:
  记录为 function / method，qualified_name 带外层路径，例如 outer.inner 或 Runner.run.helper。

class 本身:
  不记录为 implementation unit。
  class 只作为 method qualified_name 的父级。

class 声明、class body 字段/常量、import-only、constant-only、comment/docstring/空行-only 删除:
  不产生 reinsert target。
```

implementation unit 的身份由以下字段共同确定：

```text
source_target_type
repo-relative path
object_kind
qualified_name
object_start_line
object_end_line
```

因此，同一个 function / method 内命中的多个局部删除片段只算一个 implementation unit；同路径、同 qualified name 但源码范围不同的 AST 定义分别计数，例如 `@property` getter 和对应的 `@name.setter`。

对每个候选 pair，Belta 生成 pair 级输出目录。所有 pair 都有轻量材料；Git 审计 patch 只为通过轻量阈值的 pair 生成：

```text
candidate_pairs/
  pair_outputs/
    <pair_id>/
      metadata.json
      reinsert_targets.jsonl
      implementation_units.jsonl
      reinsert_extraction.patch     # 仅通过轻量阈值后生成
      obsolete_reinsert.patch      # 通过轻量阈值且构造成功时生成
```

字段含义：

```text
metadata.json
  pair 级元数据，包括 repo、commit、offset、implementation_code_paths、candidate_status、reject_reason、reject_detail，以及 reinsert target 统计字段。
  pair_id、repo_key、old_commit、new_commit 等上下文只在这里记录，reinsert_targets.jsonl 不逐行重复。
  candidate_status 为 accepted 或 rejected。
  reject_reason / reject_detail 在 accepted 时为 null；在 rejected 时记录失败类型和原因。
  candidate_status 为 accepted 的 pair 目录中应存在 obsolete_reinsert.patch；rejected pair 不保证存在该 patch。

reinsert_extraction.patch
  由一次组合 histogram diff 生成，只包含 M implementation_code 路径的 old_commit -> new_commit section。
  参数与 reinsert target extraction 使用的 diff 完全一致：
  git -c core.quotePath=false diff --histogram --unified=0 --no-renames
  它用于审计 Belta 为什么抽出某些 source_text，不直接用于构造 task_base。

reinsert_targets.jsonl
  从 implementation_code diff 中抽取出的 reinsert targets。
  一行一个 target，target_type 为 removed_object 或 removed_range。
  该文件包含构造 task_base 所需的 source_text。

implementation_units.jsonl
  合并后的 function / method 清单。
  来源包括 removed_object 内的 function / method，以及 removed_range target 本身命中的 function / method。
  不复制大段 source_text，只用于审查、统计和后续分析。

obsolete_reinsert.patch
  由 Belta 根据 reinsert_targets.jsonl 构造 task_base_full 后，再由 Git 生成的 patch。
  它表示 new_commit -> task_base_full 的变化。
  只有通过轻量阈值过滤并成功生成 task_base_full diff 的 pair 才会写出该文件。
```

完整 old-to-new diff 和完整 implementation-code diff 都不在本阶段持久化；如需审计，使用前述 bare-cache 命令按需生成。

`reinsert_extraction.patch` 和 `obsolete_reinsert.patch` 语义不同：

```text
reinsert_extraction.patch
  old_commit -> new_commit 的实现侧零上下文 diff。
  参数与 reinsert target extraction 完全一致。
  它用于审计 source_text 的抽取依据，不是 task_base 构造输入。

obsolete_reinsert.patch
  new_commit -> task_base_full 的 patch。
  它表达把 reinsert targets 对应的旧实现残留重新放回目标版本。
  它是 Retire Task Construction 构造 task_base 的输入。
```

`obsolete_reinsert.patch` 是正式 task_base_full 构造输入之一：

```text
new_commit + obsolete_reinsert.patch = task_base_full
```

Belta 生成 `obsolete_reinsert.patch` 时，需要基于 `new_commit` 准备临时工作区或等价临时 tree，根据 `reinsert_targets.jsonl` 构造 task_base_full，然后由 Git 生成 `new_commit -> task_base_full` patch。该 diff 首先使用 `--histogram --binary --no-renames`；若 Git 因重复空白行或相似代码对齐产生正文删除行，则改用 `--patience --binary --no-renames` 重新生成。accepted pair 的 `obsolete_reinsert.patch` 必须是 add-only，即除 `---` 文件头外不得包含正文 `-` 行；两种算法都不能生成 add-only patch 时，该 pair 以 `obsolete_reinsert_failed` 拒绝。diff 算法只改变 patch 的文本对齐表达，不改变已构造的 task_base_full。

临时工作区只导出生成 patch 所必需的文件：

```text
removed_object:
  checkout new_commit 中对应 path 的文件，然后插回 removed_object.source_text。

removed_range:
  checkout new_commit 中对应 path 的文件，然后插回 removed_ranges[].source_text。
```

因此本阶段不 checkout 整个仓库。生成 diff 时也只限定在 reinsert targets 涉及的 repo-relative paths，确保 `obsolete_reinsert.patch` 只表达旧实现插回内容。

构造规则：

```text
target_type = removed_object:
  将 source_text 插回 new_commit 中仍存在的同一路径文件。
  如果 removed_object 是 class，class 本身作为插回构造单位；class 内的 function / method 写入 implementation_units.jsonl。

target_type = removed_range:
  将 removed_ranges[].source_text 插回 new_commit 中仍存在的同一路径文件。
```

Belta 在本阶段只负责生成 `obsolete_reinsert.patch`，不对每个 pair 额外准备第二个干净工作区做二次 apply 验证。原因是该 patch 本身由 `new_commit -> task_base_full` 的 Git diff 生成；真正使用该 patch 构造 task_base_full 的 apply 验证，放到后续 Retire Task Construction 执行。如果本阶段无法生成 patch 或生成结果为空，则该 pair 不进入 `accepted_candidates.jsonl`，对应 `reject_reason=obsolete_reinsert_failed`。

Belta 可以额外记录 task_base Python compile 诊断字段，但本阶段不把 compile 结果作为 hard filter，也不为了 compile 诊断额外执行二次 apply。这里的 compile 是 Python 解释器加载源码前的内部编译检查，比单独 `ast.parse` 更严格，可以发现重复 keyword argument 等编译期错误：

```json
{
  "task_base_compile": "passed",
  "task_base_compile_error": null
}
```

如果 task_base 中相关 Python 文件无法被 Python 编译，则记录：

```json
{
  "task_base_compile": "failed",
  "task_base_compile_error": "SyntaxError: ..."
}
```

`removed_object` 记录示例：

```json
{
  "target_type": "removed_object",
  "path": "src/example/resources.py",
  "object_kind": "class",
  "qualified_name": "NotFoundHandler",
  "object_start_line": 101,
  "object_end_line": 103,
  "source_text": "class NotFoundHandler(BaseHandler):\n    def get(self, *args, **kwargs):\n        raise HTTPError(404)\n",
  "contained_targets": [
    {
      "object_kind": "method",
      "qualified_name": "NotFoundHandler.get",
      "start_line": 2,
      "end_line": 3,
      "source_text": "    def get(self, *args, **kwargs):\n        raise HTTPError(404)\n"
    }
  ]
}
```

`removed_range` 记录示例：

```json
{
  "target_type": "removed_range",
  "path": "src/example/runner.py",
  "object_kind": "method",
  "qualified_name": "Runner.run",
  "object_start_line": 20,
  "object_end_line": 55,
  "removed_ranges": [
    {
      "old_start_line": 31,
      "old_end_line": 33,
      "source_text": "        legacy_result = self._legacy_run()\n        if legacy_result is not None:\n            return legacy_result\n"
    }
  ]
}
```

字段含义：

```text
target_type
  removed_object 或 removed_range。

path
  repo-relative path。

object_kind / qualified_name
  removed_object 或 removed_range 命中的对象类型和限定名。

source_text
  removed_object 中被整体插回的旧实现对象源码。

removed_ranges
  removed_range 中被插回同一路径文件的旧实现删除片段列表。

contained_targets
  removed_object class 中通过 Python AST 抽取出的 function / method 清单。
  只用于语义审查、统计和后续评估，不会被单独插回。
```

`implementation_units.jsonl` 记录示例：

```json
{"source_target_type":"removed_object","path":"src/example/resources.py","object_kind":"method","qualified_name":"NotFoundHandler.get","object_start_line":7,"object_end_line":8}
{"source_target_type":"removed_range","path":"src/example/runner.py","object_kind":"method","qualified_name":"Runner.run","object_start_line":20,"object_end_line":55}
```

Candidate 阶段不执行评分。`reinsert_targets.jsonl` 只记录被插回旧实现残留的
构造材料、语义归属和原文内容，供私有审查使用；正式 Evaluation 使用
`private_materials/gold_cleanup.patch`、Agent patch 和测试结果计算后文定义的测试
增量、删除行重合与 Patch 代码相似度，不把 function / method identity 作为唯一
评分依据。

#### Candidate Filtering

Candidate Filtering 在文件状态解析、路径分类和 reinsert target extraction 之后先执行轻量阈值过滤。只有满足阈值的 pair 才生成 `reinsert_extraction.patch` 和 `obsolete_reinsert.patch`，从而避免为明显不合格的 pair 计算并写出重量级 patch。

候选 pair 必须满足：

```text
implementation_units_count >= min_implementation_units
reinsert_source_lines >= min_reinsert_source_lines
implementation_units_count <= max_implementation_units, if configured
reinsert_source_lines <= max_reinsert_source_lines, if configured
obsolete_reinsert.patch can be generated
```

字段来源：

```text
implementation_units_count
  从 implementation_units.jsonl 的记录数得到。
  表示该 pair 涉及的 function / method 数量。
  同一 AST 对象内的多个 removed_range 删除片段只计一次。
  同名但 object_start_line / object_end_line 不同的 AST 定义分别计数。

reinsert_source_lines
  表示最终会插回 task_base 的旧源码行数。
  removed_object 统计 source_text 行数。
  removed_range 统计 removed_ranges[].source_text 行数。
```

Candidate Filtering 负责过滤：

```text
涉及的 function / method 数量不足
插回源码行数不足
涉及的 function / method 数量过多
插回源码行数过多
obsolete_reinsert.patch 无法生成
```

对应 `reject_reason`：

```text
not_enough_implementation_units
not_enough_reinsert_source_lines
too_many_implementation_units
too_many_reinsert_source_lines
obsolete_reinsert_failed
```

其中：

```text
not_enough_implementation_units
  表示 implementation_units.jsonl 中 function / method 数量小于 min_implementation_units。

not_enough_reinsert_source_lines
  表示 reinsert_source_lines 小于 min_reinsert_source_lines。

too_many_implementation_units
  表示 implementation_units.jsonl 中 function / method 数量大于 max_implementation_units。

too_many_reinsert_source_lines
  表示 reinsert_source_lines 大于 max_reinsert_source_lines。

obsolete_reinsert_failed
  表示 reinsert target 数量和源码行数满足要求，但无法生成 task_base patch。
```

如果已经抽取出 reinsert targets，但 `obsolete_reinsert.patch` 生成失败，该 pair 仍然 rejected，`reject_reason=obsolete_reinsert_failed`。`rejected_candidates.jsonl` 中记录真实的统计字段，并在 `reject_detail.error` 中记录失败原因。

#### 后续 Retire Task 语义

后续任务构造时：

```text
reinsert_extraction.patch = Git 生成的 old_commit -> new_commit 实现侧零上下文 diff，用于审计 source_text 抽取依据
reinsert_targets.jsonl = 从 implementation_code diff 中抽取出的 reinsert target candidates
implementation_units.jsonl = 合并后的 function / method 清单，用于审查和统计
obsolete_reinsert.patch = new_commit -> task_base_full 的可 apply patch
gold_cleanup.patch = 进入最终 task 后的 oracle/reference cleanup patch
hidden tests = evaluator 后台使用的测试，不进入 agent workspace
```

ignored paths 不作为 `gold_cleanup.patch` 的来源；测试入口由 FF Stage2 的 `validation_test_universe` 独立确定。

Reinsert target extraction 和后续对象级审查主要基于：

```text
implementation_code diff
```

#### 输出

```text
data/runs/<run_id>/
  candidate_pairs/
      pair_outputs/
        <pair_id>/
          metadata.json
          reinsert_targets.jsonl
          implementation_units.jsonl
          reinsert_extraction.patch     # 仅通过轻量阈值后生成
          obsolete_reinsert.patch      # 通过轻量阈值且构造成功时生成
    repo_results.jsonl
    accepted_candidates.jsonl
    rejected_candidates.jsonl
```

本阶段主输出职责：

```text
pair_outputs/
  一组 pair 级审计材料和对象抽取结果，供人工检查和 Retire Task Construction 复用。
  未通过轻量阈值过滤的 rejected pair 只保留 metadata.json、reinsert_targets.jsonl 和 implementation_units.jsonl，不生成 patch。
  通过轻量阈值但 obsolete_reinsert.patch 构造失败的 rejected pair，可以保留已经生成的 reinsert_extraction.patch。
```

```text
repo_results.jsonl
  一行一个 repo，记录 repo 级处理结果和扫描概况。

accepted_candidates.jsonl
  一行一个满足 Candidate Filtering 的 candidate pair。

rejected_candidates.jsonl
  一行一个不满足 Candidate Filtering 的 candidate pair。
```

##### repo_results.jsonl

`repo_results.jsonl` 一行一个输入 repo，记录该 repo 在 Candidate Pair Construction 阶段的处理结果。

它只记录 repo 级结果和扫描概况，不记录具体 pair 的 Candidate Filtering 拒绝原因。pair 级拒绝原因写入 `rejected_candidates.jsonl`。

`status` 可选值：

```text
completed
skipped
failed
```

含义：

- `completed`：repo 完成 Git cache 准备、commit history 读取和 offset 扫描。即使最终没有保留 accepted candidate，也仍然是 completed。
- `skipped`：repo 输入字段不完整，无法开始处理。
- `failed`：repo 处理过程中发生 Git/cache/history 级错误。

`status_code` 可选值：

```text
null
missing_required_field
git_cache_failed
```

含义：

- `null`：repo 级处理没有错误。
- `missing_required_field`：repo 输入缺少 `github_repo_id` / `full_name` / `clone_url` / `default_branch`。
- `git_cache_failed`：Git cache clone、有效性检查或主线历史读取失败。

示例：

```json
{
  "repo_key": "example__repo",
  "full_name": "example/repo",
  "github_repo_id": 123456789,
  "clone_url": "https://github.com/example/repo.git",
  "default_branch": "main",
  "cache_dir": "data/cache/repos/example/repo.git",
  "head_commit": "3f9a8b7c6d5e4f32100112233445566778899abc",
  "status": "completed",
  "status_code": null,
  "offset_ranges": [
    {"start": 1, "stop": 100, "step": 1},
    {"start": 125, "stop": 750, "step": 25}
  ],
  "min_implementation_units": 1,
  "min_reinsert_source_lines": 5,
  "max_implementation_units": null,
  "max_reinsert_source_lines": null,
  "offsets_examined": 37,
  "accepted_count": 4,
  "rejected_count": 33
}
```

如果 repo 级输入字段缺失：

```json
{
  "repo_key": null,
  "full_name": "Comfy-Org/ComfyUI",
  "github_repo_id": 589831718,
  "clone_url": null,
  "default_branch": "master",
  "cache_dir": null,
  "head_commit": null,
  "status": "skipped",
  "status_code": "missing_required_field",
  "offset_ranges": [
    {"start": 1, "stop": 100, "step": 1},
    {"start": 125, "stop": 750, "step": 25}
  ],
  "min_implementation_units": 1,
  "min_reinsert_source_lines": 5,
  "max_implementation_units": null,
  "max_reinsert_source_lines": null,
  "offsets_examined": 0,
  "accepted_count": 0,
  "rejected_count": 0
}
```

如果 Git cache 准备失败：

```json
{
  "repo_key": "Comfy-Org__ComfyUI",
  "full_name": "Comfy-Org/ComfyUI",
  "github_repo_id": 589831718,
  "clone_url": "https://github.com/Comfy-Org/ComfyUI.git",
  "default_branch": "master",
  "cache_dir": "data/cache/repos/Comfy-Org/ComfyUI.git",
  "head_commit": null,
  "status": "failed",
  "status_code": "git_cache_failed",
  "offset_ranges": [
    {"start": 1, "stop": 100, "step": 1},
    {"start": 125, "stop": 750, "step": 25}
  ],
  "min_implementation_units": 1,
  "min_reinsert_source_lines": 5,
  "max_implementation_units": null,
  "max_reinsert_source_lines": null,
  "offsets_examined": 0,
  "accepted_count": 0,
  "rejected_count": 0
}
```

##### accepted_candidates.jsonl

`accepted_candidates.jsonl` 一行一个满足 Candidate Filtering 的 candidate pair。它是 Environment Construction 的输入，也是 Retire Task Construction 的 candidate manifest。每条记录包含 Environment Construction 需要的 repo/new_commit 元数据、筛选统计和 `implementation_code_paths`。pair 级详细材料通过 `pair_id` 定位：

```text
candidate_pairs/pair_outputs/<pair_id>/
```

示例：

```json
{
  "pair_id": "example__repo__7a1b2c3d4e5f__3f9a8b7c6d5e",
  "repo_key": "example__repo",
  "full_name": "example/repo",
  "github_repo_id": 123456789,
  "clone_url": "https://github.com/example/repo.git",
  "default_branch": "main",
  "old_commit": "7a1b2c3d4e5f678901112233445566778899aabb",
  "new_commit": "3f9a8b7c6d5e4f32100112233445566778899abc",
  "offset": 17,
  "removed_object_targets_count": 1,
  "removed_range_targets_count": 2,
  "implementation_units_count": 4,
  "reinsert_source_lines": 37,
  "task_base_compile": "passed",
  "task_base_compile_error": null,
  "implementation_code_paths": [
    "src/example/runner.py",
    "src/example/state.py"
  ]
}
```

##### rejected_candidates.jsonl

`rejected_candidates.jsonl` 一行一个已经生成 pair、但不满足 Candidate Filtering 的 candidate pair。

`rejected_candidates.jsonl` 同样只写入 `implementation_code_paths`。ignored paths 不在总表中展开；如果需要审计完整变化，使用 bare cache 和 pair 的两个 commit SHA 按需运行完整 Git diff。

`reject_reason` 可选值：

```text
not_enough_implementation_units
not_enough_reinsert_source_lines
too_many_implementation_units
too_many_reinsert_source_lines
obsolete_reinsert_failed
```

含义：

- `not_enough_implementation_units`：涉及的 function / method 数量小于 `min_implementation_units`。
- `not_enough_reinsert_source_lines`：插回源码行数小于 `min_reinsert_source_lines`。
- `too_many_implementation_units`：涉及的 function / method 数量大于 `max_implementation_units`。
- `too_many_reinsert_source_lines`：插回源码行数大于 `max_reinsert_source_lines`。
- `obsolete_reinsert_failed`：reinsert target 数量和源码行数满足要求，但无法生成 `obsolete_reinsert.patch`，或生成结果为空。

示例：

```json
{
  "pair_id": "example__repo__9a9b9c9d9e9f__3f9a8b7c6d5e",
  "repo_key": "example__repo",
  "full_name": "example/repo",
  "github_repo_id": 123456789,
  "old_commit": "9a9b9c9d9e9f0000111122223333444455556666",
  "new_commit": "3f9a8b7c6d5e4f32100112233445566778899abc",
  "offset": 3,
  "removed_object_targets_count": 0,
  "removed_range_targets_count": 0,
  "implementation_units_count": 0,
  "reinsert_source_lines": 2,
  "task_base_compile": null,
  "task_base_compile_error": null,
  "reject_reason": "not_enough_implementation_units",
  "reject_detail": {
    "implementation_units_count": 0,
    "min_implementation_units": 1
  },
  "implementation_code_paths": [
    "src/example/runner.py"
  ]
}
```

最终语义：

```text
accepted = 生成了 pair，并满足 Candidate Filtering
rejected = 生成了 pair，但不满足 Candidate Filtering
```

### Environment Construction

Environment Construction 负责为每个 repo 的 `new_commit` 获取 FeatureFactory Stage2 环境产物。Belta 只接受 FF Stage2 中 `completed + passed` 的 run。

本阶段不为每个 pair 单独构造环境。同一个 repo 的多个 accepted candidate pair 共享同一个 `new_commit=C0`，因此共享同一个 FF Stage2 环境。

#### 输入

本阶段只读取：

```text
candidate_pairs/accepted_candidates.jsonl
```

不需要再读取 `repo_discovery/repositories.jsonl`，因为 `accepted_candidates.jsonl` 已经包含：

```text
repo_key
full_name
github_repo_id
clone_url
default_branch
new_commit
```

Belta 读取当前 run 的 `candidate_pairs/accepted_candidates.jsonl`，并按 `repo_key` 分组。

在同一个 `candidate_pairs/accepted_candidates.jsonl` 内，同一个 `repo_key` 下的所有 accepted candidate pair 必须共享同一个 `new_commit`。

如果当前 `candidate_pairs/accepted_candidates.jsonl` 中，同一个 `repo_key` 对应了多个不同的 `new_commit`，Belta 直接报错。因为在本设计中，一个 repo 在一次 run 内只允许对应一个 FF Stage2 环境，而 FF Stage2 环境绑定到唯一的 `target_commit_sha = new_commit`。

#### FF Stage2 配置来源

Belta 是运行入口，因此运行配置由 Belta 自己的 `.env` 和 YAML config 负责。Belta 不隐式读取 FeatureFactory 仓库自己的 `.env`。

调用 FF Stage2 时，Belta 显式构造 FF `Settings`，并传入：

```text
database_url
github_token
local_postgres_container_name
local_postgres_data_dir
stage2_workspace_dir
stage2_openhands_sdk_root
```

LLM 字段使用 FeatureFactory 原生 `Settings` 逻辑。为避免 Harbor 的模型配置
意外改变 Stage2，Belta `.env` 使用 Stage2 专用变量：

```text
FEATURE_FACTORY_STAGE2_LLM_MODEL
FEATURE_FACTORY_STAGE2_LLM_BASE_URL
FEATURE_FACTORY_STAGE2_LLM_API_KEY
```

Harbor Mini-SWE 使用同一 `.env` 中的 `LLM_MODEL`、`LLM_BASE_URL`、
`LLM_API_KEY` 以及相同值的 `OPENAI_BASE_URL`、`OPENAI_API_KEY`。FeatureFactory
虽然也支持这些通用变量作为回退，但 Stage2 专用变量优先，因此两条流程可以
使用不同模型而不会互相覆盖。

如果需要 planner / worker 使用不同配置，可以继续细分为
`FEATURE_FACTORY_STAGE2_PLANNER_LLM_MODEL`、
`FEATURE_FACTORY_STAGE2_WORKER_LLM_MODEL`、
`FEATURE_FACTORY_STAGE2_PLANNER_LLM_API_KEY` 和
`FEATURE_FACTORY_STAGE2_WORKER_LLM_API_KEY` 等原生变量。

Stage2 镜像源、包源、GitHub proxy、Docker registry mirror 等 FF runtime 配置，也应写在 Belta `.env` 中，使用 FeatureFactory 原生的 `FEATURE_FACTORY_*` 变量名。这样 Belta 可以作为独立项目运行，同时仍然把 FF Stage2 需要的完整运行配置传给 FF。

#### Stage2 / Retire 网络配置

正式 Environment Construction、Retire 和 Harbor 流程不设置或向容器注入宿主机
`HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 及其小写变量。复现环境应直接访问所需
服务；受限网络只使用 FeatureFactory 已有的 Docker registry、GitHub、Python 包源等
`FEATURE_FACTORY_*` 镜像配置。镜像配置属于构建和依赖获取策略，不改变 runner、
验证结果契约或 benchmark 任务内容。

`PYTHONUNBUFFERED=1` 是运行日志选项，只控制 Python 标准输出和标准错误是否立即
刷新，不参与任务语义或产物复用判断。

#### 使用 FF 前端观察 Belta Stage2

Belta 和普通 FeatureFactory 使用不同数据库。默认 FF 前端连接
`55432/feature_factory`，不会显示 Belta 写入 `55433/belta_ff` 的 Stage2 run。
在运行 Environment Construction 前，使用当前 run 的配置启动 Belta 专用前端：

```bash
uv run belta candidate featurefactory-ui \
  --run-dir data/runs/<run_id>
```

该命令默认监听 `127.0.0.1:18742`，自动读取当前 run 的
`featurefactory.database_url`、PostgreSQL 容器名、数据目录和 Belta `.env`，
不需要手动导出 FF 环境变量。命令保持运行后，在另一个终端执行
`construct-environments`。两者共享同一数据库，因此页面可实时查看每个 repo
的 Stage2 状态、Planner/Worker 过程、构建日志、测试进度、错误和耗时。

Belta 直接创建标准 FF Stage2 run，不创建 FF BatchTask。因此这批运行显示在
“Stage2 环境构造”页面，不显示在“批量任务”页面。环境构造仍由 Belta 命令
调度；前端仅作为观察入口使用。Belta 启动该前端时自动设置
`FEATURE_FACTORY_SERVER_OBSERVER_MODE=1`。observer mode 跳过 FF server
启动时的中断任务恢复、孤儿运行资产清理、BatchTask 恢复调度和后台镜像清理，
同时拒绝所有非读取 API 请求。

Environment Construction 自身不依赖 observer 执行恢复。命令在调度前持有一个
Belta 专用进程锁；若已有另一个 `construct-environments` 实例，第二个命令立即
失败。拿到锁后，命令只将 `trigger_kind=belta_environment` 且仍处于
`queued/running` 的旧 Stage2 run 标记为中断，再按正常续跑逻辑为对应仓库创建新
run。进程退出或被杀后文件锁由操作系统释放，因此下次运行无需手工清理数据库。
FF UI、手工 Stage2 run 和其他 trigger kind 不受影响；已有完整环境仍按 commit 和
产物契约直接复用。

VS Code Remote SSH 等客户端可以直接转发远端端口 `18742`。若客户端没有自动
转发，使用固定 SSH tunnel：

```bash
ssh -N -L 18742:127.0.0.1:18742 <user>@<server>
```

然后在本机浏览器打开 `http://127.0.0.1:18742`。专用 observer 前端可以在
`construct-environments` 开始前、运行中或结束后启动，环境构造结束后也可以
停止。浏览器页面和 observer 后台的关闭、刷新或重启均不影响 Belta 环境构造
进程。

#### FF Stage2 公共运行资产预热

`construct-environments` 会在处理任何缺失环境的 repo 前自动预热 FF Stage2 共用运行资产。也可以单独执行以下命令提前预热、查看完整构建日志或做环境验收：

```bash
uv run belta candidate prewarm-environment \
  --run-dir data/runs/<run_id>
```

该命令加载当前 run 的 `config.yaml` 和 Belta `.env`，通过 FeatureFactory 的公开 prewarm API 准备：

```text
host software-agent-sdk Python environment
planner OpenHands agent-server Docker image
```

prewarm 只准备所有 repo 共用的 FF Stage2 资产，不创建 repo 级 Stage2 run，不调用 planner / worker LLM，也不生成 Belta environment 产物。Docker 构建日志直接输出到终端；失败时命令立即退出并保留 FF 的失败状态和日志，不自动切换 registry mirror。

`construct-environments` 在查询 FF 历史结果、复用当前环境或调度任何新 Stage2 run 之前，统一调用一次 FF prewarm API。已就绪的资产直接复用并快速返回；缺失或因 SDK commit、SDK lockfile、agent-server Dockerfile、platform、mirror profile 变化而失效的资产会自动重建。预热成功后命令继续执行，不要求用户再手动启动一次 `construct-environments`。

prewarm 是 Environment Construction 的固定前置步骤：有可复用环境时，公共资产通常也已预热，因此只增加一次快速状态确认；没有可复用环境时，后续新 Stage2 本来就依赖这些公共资产。预热完成后，Belta 再逐 repo 复用当前 run 环境、复用 FF 数据库中的同 commit 成功结果，或为确实缺失的 repo 新建 Stage2 run。只有 prewarm 自身真实构建失败时命令才报错；此时尚未导出任何 repo，也未创建或调度任何新 run。

因此正式执行顺序为：

```text
construct-environments（内部自动完成或复用 prewarm）
  -> construct-retire-tasks
```

显式运行 `prewarm-environment` 是可选的提前准备步骤，不再是继续流水线所必需的手工门禁。

#### 传给 FF Stage2 的核心输入

FeatureFactory Stage2 内部需要 FF 数据库里的 repository id。

Belta 先检查本项目当前 run 下是否已经存在完整环境输出：

```text
data/runs/<run_id>/environments/<repo_key>/manifest.json
data/runs/<run_id>/environments/<repo_key>/Dockerfile
data/runs/<run_id>/environments/<repo_key>/run_script.sh
data/runs/<run_id>/environments/<repo_key>/full_report.json
data/runs/<run_id>/environments/<repo_key>/test_results.jsonl
```

如果 `manifest.json` 中：

```text
status == completed
target_commit_sha == new_commit
```

且必需文件存在，则复用 Belta 自己已经导出的环境目录，不再调用 FF。

如果当前 Belta run 下没有完整环境输出，Belta 使用 `github_repo_id` 在 FF 的 `github_repositories` 表里查找对应记录，得到 FF 内部的 `repository_id`。随后先按 `repository_id + new_commit` 查询历史 Stage2 run；如果存在 `status=completed` 且 `result=passed` 的精确 commit 结果，直接将该 run 已保存的 Dockerfile、run script、测试报告和文件级测试结果导出到当前 run 的 `environments/<repo_key>/`。

只有不存在可复用的历史成功 run 时，才使用：

```text
repository_id = FF github_repositories.id
target_commit_sha = candidate new_commit
```

调用 FF Stage2 service / runner 创建并运行新的 Stage2 run。

`full_name`、`clone_url`、`default_branch` 作为 Belta 本地记录和 sanity check 使用，不作为 FF Stage2 的主键。

#### 精确 commit checkout 与 Git LFS

FF 从共享 Git cache 物化 Stage2 工作区时先执行 `git clone --no-checkout`，
恢复仓库的官方 GitHub origin，然后只 checkout Belta 指定的 `new_commit`。这样
clone 不会先 checkout cache 的默认 HEAD，也不会在目标 commit 确定前触发 Git
LFS 下载。

目标 commit checkout 遇到 LFS pointer 时必须下载真实对象；本流程不设置
`GIT_LFS_SKIP_SMUDGE=1`，也不把约 100 字节的 pointer 文本当作项目文件。
GitHub LFS endpoint 使用标准形式：

```text
<current-proxy-prefix>https://github.com/<owner>/<repo>.git/info/lfs
```

checkout 复用 FF 的 Git 远程重试通道。LFS 网络错误会按
`FEATURE_FACTORY_STAGE2_GITHUB_PROXY_PREFIX` 的配置顺序轮换代理；只有真实 LFS
对象下载完成且 checkout 到精确 `new_commit` 后，FF 才进入 Planner / Worker 环境
构造。普通 Git objects 仍来自共享 cache，重试 LFS 不会重新下载完整仓库。

#### 成功条件

Belta 查询或等待 FF Stage2 run 时，必须满足：

```text
repository_id == FF github_repositories.id
target_commit_sha == new_commit
status == completed
result == passed
```

只有满足上述条件的 FF Stage2 run 才能导出。

```text
Belta environments/<repo_key>/ 已完整存在，且 target_commit_sha == new_commit
  -> 直接复用 Belta 自己的 environment 输出。

否则，FF 数据库存在同 repository_id、同 new_commit 的 completed + passed Stage2 run
  -> 将该历史 run 的环境和测试产物重新导出到当前 Belta run。

否则，FF 存在同 new_commit 的 active run
  -> 等待该 run 完成并导出。

否则
  -> create FF Stage2 run for target_commit_sha = new_commit
  -> schedule/run FF Stage2
  -> wait until completed
```

如果 FF Stage2 run 最终不是 `completed + passed`，Belta 记录该 repo 的 environment 失败，例如：

```text
status = failed
status_code = stage2_run_failed
```

无论是复用已有 run，还是自动创建新 run，最终都只接受 `completed + passed` 的结果。

#### 输出

```text
data/runs/<run_id>/
  environments/
    <repo_key>/
      manifest.json
      planner_guidance.md
      Dockerfile
      run_script.sh
      collect_report.json
      full_report.json
      test_results.jsonl
    environment_results.jsonl
```

`manifest.json` 示例：

```json
{
  "repo_key": "example__repo",
  "full_name": "example/repo",
  "github_repo_id": 123456789,
  "ff_repository_id": 123,
  "target_commit_sha": "3f9a8b7c6d5e4f32100112233445566778899abc",
  "target_branch": "main",
  "ff_stage2_run_id": "2f0b8d4a-6b4c-41a1-8fb1-8e19f7a12345",
  "ff_status": "completed",
  "ff_result": "passed",
  "status": "completed",
  "status_code": null,
  "summary": {
    "total_files": 128,
    "passed_files": 120,
    "failed_files": 8,
    "total_tests": 4200,
    "passed_tests": 4000,
    "failed_tests": 5,
    "error_tests": 0,
    "skipped_tests": 195
  },
  "test_file_count": 128,
  "validation_test_universe_file_count": 120
}
```

`planner_guidance.md` 是 FF Stage2 planner 产出的环境构造指导。

`Dockerfile` 是 FF Stage2 worker 产出的可复现环境定义。

`run_script.sh` 是 FF Stage2 worker 产出的测试运行接口，后续 Retire Task Construction 和 evaluator 会复用它。

`collect_report.json` 是 FF Stage2 调用：

```bash
/workspace/run_script.sh --action collect --out <path>
```

得到的测试发现结果，主要记录 FF 在该 repo 中识别到的测试文件。

`full_report.json` 是 FF Stage2 full validation 的完整报告。成功的 Stage2 run
仍可能包含少量文件级 `failed` 或 `error` 诊断；只有文件级 `passed` 的记录进入
后续 `validation_test_universe`。

`test_results.jsonl` 是从 FF Stage2 的 full validation 结果导出的文件级测试结果，一行一个测试文件。FF 的 `full` 由外层 Validator 收集全部测试文件后，循环调用 runner 的单文件 `run` 完成；`run_script.sh` 本身没有一次运行全部文件的模式。

示例：

```json
{
  "test_file": "Tests/test_app.py",
  "target_selector": "test_app.py",
  "status": "passed",
  "total_tests": 42,
  "passed_tests": 42,
  "failed_tests": 0,
  "error_tests": 0,
  "skipped_tests": 0,
  "exit_code": 0,
  "raw_result": {
    "action": "run",
    "status": "passed",
    "summary": {
      "collected": 42,
      "passed": 42,
      "failed": 0,
      "errors": 0,
      "skipped": 0
    }
  }
}
```

`test_file` 是相对于 repo 根目录的规范文件身份，用于快照、结果键和 F2P/P2F 对齐；`target_selector` 是实际传给 `run_script.sh --target-selector` 的路径。大多数仓库二者相同，但 runner 先进入子目录时可能不同。Environment Construction、Retire 和 Harbor 必须完整透传这两个字段，不能用 `test_file` 替代 `target_selector`。已有环境导出只有在每条测试记录均包含这两个字段时才可复用；否则从 FF 数据库中同 commit 的成功 Stage2 run 重新导出，不重新调用模型。

Retire 从 Belta `.env` 读取 FF Stage2 的完整 validation 超时，当前为 1800 秒，
并将实际值作为 `full_validation_timeout_seconds` 写入 task manifest。Retire 不设置
固定的单文件超时；每次运行测试文件时使用整套 validation 的剩余时间，以便正在
运行的文件也会在统一截止时间终止。Harbor 导出直接继承这一完整超时，不提供第二套
超时覆盖。FeatureFactory 自身的 Stage2 超时策略不受影响。

后续 hidden tests 候选来自 `test_results.jsonl`。Belta 不把它理解为“仓库理论上的全部测试”，而是定义一个可复现的验证测试全集：

```text
validation_test_universe = test_results.jsonl 中文件级 status == passed 的测试文件
```

含义：

```text
validation_test_universe
  FF Stage2 在 new_commit 的目标版本环境中已经成功运行并通过的测试文件集合。
  这里的 passed 是文件级测试结果，不是 FF full_report 顶层 status。
  这是 Belta 当前任务的可复现测试基准。
  它不声称覆盖仓库所有可能测试、所有平台测试或所有配置组合。
```

原因是 FF Stage2 run 的顶层结果可能是 completed + passed，但 full validation 中仍然存在少量文件级未通过记录，例如某些测试文件 collected 后全部 skipped，FF 的 run_script 会把该文件记为 failed。Belta 不把这类文件纳入 `validation_test_universe`。

Retire Task Construction 使用同一批 `validation_test_universe` 比较 `new_commit` 和 `task_base` 的测试表现。也就是说，后续文档中提到的 hidden tests 或同一批测试，默认都是指这批 FF 已确认通过的测试文件。

Retire 将这些测试冻结为 hidden test snapshots，并保存本阶段真实
执行的 baseline 和 pair 逐文件结果，用于续跑校验、失败诊断和任务质量
审查。Harbor 导出时将 Pair 的逐文件 `task_base_status` 冻结进隐藏测试
manifest；发布冻结数据前可在最终 Harbor 环境另跑一次 Nop 做一致性审计，
但 Nop 不替换该冻结基线。Oracle、Nop 和模型 Agent 各自保存为独立 Harbor Job。

#### 本阶段边界

Environment Construction 只负责拿到 `new_commit` 的可用环境和测试基准，不选择最终 task。

### Retire Task Construction

Retire Task Construction 负责把 `candidate_pairs/accepted_candidates.jsonl` 中的 candidate pair 转换为 Retire Obsolete Implementation task。

本阶段是当前流程中的任务构造阶段。它复用 Candidate Pair Construction 生成的 pair 级材料和 Environment Construction 生成的测试环境，负责构造内部验证状态 `task_base_full`、生成 `gold_cleanup.patch` oracle/reference answer 和 hidden test snapshots，并根据 `new_commit` / `task_base_full` 的测试表现筛选最终任务。

#### 输入

本阶段读取：

```text
candidate_pairs/accepted_candidates.jsonl
environments/<repo_key>/
```

其中：

```text
accepted_candidates.jsonl
  提供 old_commit、new_commit、implementation_code_paths、implementation_units_count、reinsert_source_lines 等 candidate pair 总表信息。

candidate_pairs/pair_outputs/<pair_id>/
  提供 reinsert_extraction.patch、reinsert_targets.jsonl、implementation_units.jsonl、obsolete_reinsert.patch 等 pair 级私有材料和 task_base_full 构造输入。

environments/<repo_key>/
  提供 new_commit 的 FF Stage2 环境、run_script.sh 和 test_results.jsonl。
  其中 test_results.jsonl 中文件级 status == passed 的测试文件构成 validation_test_universe。
```

#### 核心流程

Belta 只接收 `environments/<repo_key>/manifest.json` 为 `completed` 且当前环境材料完整的仓库；FF 构造失败的仓库只保留在 Environment Construction 结果中，不进入 Retire。随后按 repo 组织任务验证。进入容器验证前，先在同一 `repo_key + new_commit`
内按 `obsolete_reinsert.patch` 的完整内容哈希精确去重。补丁相同的 candidate
只保留 `(offset, pair_id)` 最小的一条；补丁缺失或不可读的 candidate 不参与
合并，后续仍记录为确定性 `construction_failed`。

去重后，同一个 repo 的所有代表 candidate pairs 共享同一个 `new_commit`、FF
environment 和 `validation_test_universe`，但每个不同补丁仍独立构造和验证；
一个 repo 可以产出多个主数据 task。

本阶段将 repo baseline 和 pair 验证分为两个互不重叠的阶段：

```yaml
retire_task_construction:
  max_concurrent_repo_baselines: 32
  max_concurrent_pairs: 32
```

`max_concurrent_repo_baselines` 限制所有 repo 运行干净 `new_commit`
基线时的并发；`max_concurrent_pairs` 是随后所有 pair 动态验证的全局并发上限。
两个 worker pool 不同时工作。每个 baseline attempt 和每个 pair 都使用独立容器，
并在容器内按既定测试文件顺序调用
`run_script.sh`，不并行测试文件。

验证容器继承 FF Docker image 定义的 `HOME`，Belta 不再统一注入 `HOME=/tmp`。Retire 与最终 Harbor Verifier 都采用“每个 baseline/pair/trial 一个干净容器，容器内顺序执行文件”的语义；FF Stage2 造环境时的 full validation 虽然为每个文件启动新容器，但不会据此单独改变 Retire。若某 repo 在最终共享容器语义下出现跨文件污染，其干净 baseline 不会全部通过，因此按保守口径排除该 repo。

当前 baseline / pair 的整套验证最多运行 1800 秒，不设置固定单文件上限；每个文件
执行时使用整套验证的剩余时间。整套超时表示测试没有完整执行，当前文件和未启动
文件不伪造结果；对应 Baseline 或 Pair 的运行级状态写 `infra_error`，不能当作真实
测试失败或 F2P。Baseline 和 Pair 的 `infra_error` 都立即保存为可续跑 checkpoint，
同一次 Retire 命令内不自动重试，下次手动运行同一命令时重新执行。

处理顺序：

```text
1. 在同一 `repo_key + new_commit` 内按 `obsolete_reinsert.patch` 完整内容精确
   去重；相同补丁只保留 offset 最小的代表 candidate，再按 repo_key 分组。
2. 读取 `checkpoints/baselines/<repo_key>.json`，并以最多
   `max_concurrent_repo_baselines` 个 worker 完成所有需要初跑的 repo baseline：
   - `passed`：续跑时跳过 baseline。
   - `failed`：续跑时直接舍弃 repo。
   - `infra_error` 或文件不存在：运行一次完整 `new_commit` baseline。
   - 全部测试通过：原子写入 `passed`。
   - 至少一个测试真实失败：原子写入 `failed`，不重试。
   - 镜像、容器或 runner 未能完整运行：原子写入 `infra_error`，本次不重试。
3. 等全部 baseline attempt 结束后才启动 pair 阶段。只有 status 为 `passed`
   的 repo 才检查或复用 candidate checkpoints；需要实际执行的 pairs 进入最多
   `max_concurrent_pairs` 个 worker 的全局并行池。
4. 对没有终态 checkpoint 的当前 pair：
   - 根据 pair_id 定位 candidate_pairs/pair_outputs/<pair_id>/。
   - 读取 pair_outputs/<pair_id>/reinsert_targets.jsonl。
   - 从当前 repo 的 FF environment image 启动一个全新临时容器。
   - 通过 `docker exec --interactive` 的标准输入将 `pair_outputs/<pair_id>/obsolete_reinsert.patch` 交给容器内的 `git apply`，形成当前容器中的 task_base_full；patch 不写入镜像、容器文件或 Docker build context。
   - 在同一 FF 环境和 validation_test_universe 下验证 task_base_full。
     当前 pair 只启动一个容器，并在该容器中按测试入口文件顺序逐次调用 FF `run_script.sh`；
     每次调用单独写结果 JSON。测试文件共享当前 pair 的容器和 task_base_full 文件系统，
     但不同 pair 使用从同一基础 image 启动的不同容器，不共享运行状态。
     容器不从宿主机 bind mount repo，也不复制完整 task_base_full。
   - 当前 pair 的全部测试结束后删除临时容器；不构建 pair 专属 image。
   - 根据测试表现判断该 pair 是否成为 main task。
5. 如果当前 pair 成为 main task：
   - 通过独立临时 Git index 执行 `read-tree <new_commit>` 和 `checkout-index`，
     从 bare repo cache 逐字节物化 new_commit，再 apply obsolete_reinsert.patch，
     形成 task_base_full。这里不使用会执行 `.gitattributes` 中
     `export-ignore` / `export-subst` 规则的 `git archive`。
   - 从该临时工作区生成 `gold_cleanup.patch` 和 hidden test snapshots。
   - 只写出可重建任务所需的 Dockerfile、runner、patch、快照和元数据；不持久化完整 repo 副本。
   - 临时 task_base_full 在当前 pair 完成后释放。
   - 继续处理该 repo 的后续 accepted candidate pairs。
6. worker 完成时立即原子写入当前 pair checkpoint；最终汇总仍按去重后
   accepted candidates 的确定顺序输出，不受并发完成顺序影响。续跑时不属于
   当前代表集合的旧 Pair checkpoint 和 task 会被清理。
7. 处理完该 repo 的全部 accepted candidate pairs 后，如果没有任何 pair 成为 main task：
   - 该 repo 不输出正式 task。
```

因此，本阶段会检查每个 repo 的所有不同精确补丁，并保留其中所有
behavior-sensitive main tasks。这里仅消除补丁字节完全一致的重复工作，不执行
模糊相似度或行为近重复筛选；同一 repo 的不同补丁仍可产生多个任务，后续再结合
F2P/P2P 行为结果选择最终一题。

#### 测试表现分流

Belta 使用 `new_commit` 作为目标版本行为基准。

本阶段的“通过测试”均限定在 `validation_test_universe` 上：

```text
validation_test_universe
  Environment Construction 从 FF Stage2 full validation 中导出的文件级 status == passed 测试文件集合。
  该集合来自 full_report.file_results 或等价导出的 test_results.jsonl 行级结果。
  它不等同于 FF full_report 顶层 status == passed。
  Belta 用它作为当前 task 的 hidden test universe。
```

分流规则：

```text
new_commit 在 validation_test_universe 上全部通过，
task_base_full 在同一批测试上出现至少一个文件级 failed，
且没有文件级 error，整套测试也完整结束
  -> 当前 pair 成为 main task。

new_commit 在 validation_test_universe 上全部通过，
task_base_full 在同一批测试上也全部通过
  -> 当前 pair 不进入主数据，继续尝试该 repo 的下一个 pair。

new_commit 在单容器顺序验证中未能复现 FF Stage2 的文件级 passed 基准
  -> baseline 记录 failed；不执行其 candidate pairs，也不生成这些 Pair 的记录。

new_commit baseline 因镜像、容器或 runner 基础设施异常未完整运行
  -> 原子写入 infra_error，本次不执行其 candidate pairs；手动续跑时重新尝试一次。

task_base_full 的任一文件级结果为 error，或整套测试未完整运行
  -> pair 记录 infra_error，不将已观察到的 failed 当作可信 F2P；下次续跑重新执行。

Environment Construction 没有可用 FF 环境，
或 validation_test_universe 为空
  -> baseline 记录 infra_error，本次不执行该 repo 的 candidate pairs，续跑时重试。
```

注意：在 Belta 中，obsolete implementation 指真实开发者在 `old_commit -> new_commit` 演进中从仍然存在的 `implementation_code` 文件中移除的旧实现残留。Candidate Pair Construction 用 `reinsert_targets.jsonl` 记录完整删除的 Python 对象和 function / method 内 pure deletion 片段；整个文件被删除的 `D` 路径不进入候选池。Belta 主数据集只保留 behavior-sensitive obsolete implementation：`new_commit` 在 `validation_test_universe` 上通过，而 `task_base_full` 在同一批测试上失败。两边都通过的 pair 不进入主数据。无论当前 pair 是否进入主数据，Belta 都继续验证同一 repo 的其余 accepted candidate pairs。

Candidate Pair Construction 中记录的 `task_base_compile` 只是诊断字段，不作为 Retire Task Construction 的筛选条件。后续可以用它分析任务难度和失败类型，但本阶段是否成为 main task 只由 `validation_test_universe` 上的测试表现决定。

#### Pair ID 与 Harbor Task ID

Retire 阶段只使用 Candidate Pair Construction 已生成的 `pair_id`。一个 Pair
最多生成一个正式任务，不在 Retire 产物中增加第二套 `task_id`。Harbor 导出时直接使用：

```text
Harbor task ID = pair_id
```

#### Task Instruction

`task_instruction.md` 由 Belta 使用固定模板生成，不调用 agent 或 LLM 生成。

本阶段使用以下最终版通用任务说明，与 `TASK_INSTRUCTION` 保持一致：

```text
This repository is based on the target version, but remnants of obsolete implementations from an older version have been reintroduced. These remnants may be spread across multiple files, functions, and classes, and may involve independent behaviors.

These obsolete remnants may include, but are not limited to:

- branches, functions, classes, variables, or helper logic that are no longer used;
- old logic that duplicates or conflicts with the target-version implementation;
- old code that can still execute even though the target version no longer needs or expects it;
- historical logic that interferes with the target version’s control flow, data flow, or state management;
- obsolete imports, references, variables, or helper code left behind by the old implementation.

Please use the existing source code, call relationships, and available tests to identify and remove these obsolete implementations, bringing the affected behavior into alignment with the target version while preserving unaffected functionality.

Please follow these principles:

1. Use the target-version implementation already present in the repository as the basis for your work. Avoid redesigning or replacing existing implementations.
2. Remove the identified obsolete implementations and clean up related imports, references, variables, and helper code, taking care to preserve any parts still required by the target version.
3. Preserve code and behavior unrelated to these obsolete implementations, and avoid introducing unrelated changes.
4. Keep the final source changes clear and consistent with the surrounding code style and structure.

Repository and environment notes:

1. The project directory is `/workspace/repo`. Do not search other directories for another copy of the project’s source code.
2. External network access is unavailable in the current environment. Use the tools and dependencies already available, and do not attempt to access the network or install or update dependencies.
3. You may inspect and run the tests already present in the repository, and write additional tests as needed to help validate the changes.
4. Temporary diagnostic files may be placed under `/tmp`. When finished, remove temporary files and diagnostic artifacts, leaving only changes relevant to the task.
```

`task_instruction.md` 不包含 hidden tests、`gold_cleanup.patch`、`obsolete_reinsert.patch` 或其他答案材料。具体仓库和 Pair 元信息写入 `manifest.json`，不需要让 agent 通过任务说明看到答案。

#### 输出

本阶段输出目录固定为：

```text
data/runs/<run_id>/
  retire_task_construction/
    checkpoints/
      baselines/
        <repo_key>.json
      pairs/
        <pair_id>.json
    tasks/
      <pair_id>/
        manifest.json
        task_instruction.md
        eval_assets/
          Dockerfile
          run_script.sh
          validation_test_snapshots.jsonl
          validation_test_snapshots/
            <snapshot files>
        private_materials/
          pair_metadata.json
          reinsert_extraction.patch
          reinsert_targets.jsonl
          implementation_units.jsonl
          obsolete_reinsert.patch
          gold_cleanup.patch
    results/
      baseline_test_results/
        <repo_key>.jsonl
      pair_test_results/
        <pair_id>.jsonl
      pairs.jsonl
      repos.jsonl
      summary.json
```

`checkpoints/` 只负责细粒度续跑，`results/` 只保存本次运行的最终有序视图，
`tasks/` 只保存正式任务。最终汇总文件不作为续跑依据。
输出目录不存在时创建当前结构；已存在时，顶层只允许
`checkpoints/`、`tasks/` 和 `results/`。出现任何其他条目时立即报错，
要求清空本阶段输出后重跑；程序不读取、转换或自动删除非当前结构。

文件职责：

```text
checkpoints/baselines/<repo_key>.json
  repo 级 new_commit 基线的原子续跑 checkpoint，固定记录 repo_key、
  status、input_fingerprint、test_summary 和 error。status 只能是
  passed、failed 或 infra_error。input_fingerprint 将结果绑定到当前
  Dockerfile、runner、环境 manifest、测试集合和实际超时配置；输入改变后不复用旧结果。
  test_summary 固定包含 new_commit_total_files、new_commit_passed_files、
  new_commit_failed_files 和 new_commit_error_files。error 只保存运行级
  基础设施异常；passed 和真实测试失败得到的 failed 均为 null，
  failed 的具体文件和 runner 输出在对应的 baseline JSONL 中。

checkpoints/pairs/<pair_id>.json
  单个代表 Pair 的原子 checkpoint。每个 Pair 完成动态验证或遇到失败后立即写入，
  不等待整个 run 结束。它记录 Pair 身份、输入 fingerprint 和 status。

  status == completed 时，selection_result 只能是：
    - main_task：task_base_full 至少产生一个 F2P 测试文件，且 tasks/<pair_id>/ 已完整写出。
    - no_f2p：task_base_full 在 validation_test_universe 上全部通过，不进入主数据。
    completed 结果同时记录测试摘要。

  status == construction_failed 表示必要材料缺失，或当前输入无法构造出符合任务定义的 Pair；
  它是确定性构造失败分类，但不作为可复用 checkpoint，续跑时会重新检查。

  status == infra_error 表示 Docker、容器或 runner 未能完整运行；续跑时重新执行。
  task_base 测试正常完成但存在失败属于 completed/main_task，不属于 failed 或 infra_error。
  所有状态都写 test_summary 和 error；尚未进入测试时 test_summary 可为
  null，成功 completed 时 error 必须为 null。只有 completed 写
  selection_result，不写另一套 status_code。

results/baseline_test_results/<repo_key>.jsonl
  一行一个实际运行的 new_commit 测试文件。未启动的文件不伪造结果行；
  容器级错误放在 baseline checkpoint 的 error 中。

results/pair_test_results/<pair_id>.jsonl
  一行一个实际运行的 task_base 测试文件。当前验证 attempt 受控结束后，
  一次性原子写入完整 JSONL，再写 Pair checkpoint。进程中断时不发布
  JSONL 或 checkpoint，续跑时重新执行整个 Pair。

  baseline 和 pair 的每行外层字段相同：test_file、target_selector、
  status、total_tests、passed_tests、failed_tests、error_tests、
  skipped_tests、exit_code 和 raw_result。Runner 给出的文件级 `failed` 直接记为
  失败，测试点计数只作诊断，不要求满足
  `collected = passed + failed + errors + skipped`。文件级 `passed` 使用严格条件：
  必须至少收集并通过一个测试点，failed/errors 必须为 0，且五项计数必须自洽。
  进程退出码只保留为诊断证据，不覆盖合法的 Runner 状态。合法 JSON 中的文件级
  `failed` 均作为 F2P 结果处理，包括 collected 为 0 或全部 collected 测试点均为
  skipped 的结果；这类结果的语义质量在后续 Benchmark 审查中判断，不在 Retire
  阶段升级为整道 Pair 的 construction_failed。
  status=error 表示文件级执行没有得到可信结论，例如结果 JSON 无效或 runner 未写
  结果。整套 validation 超时会终止当前文件，
  不为未完成文件伪造结果，并在 suite 结果中记录超时。exit_code 保存已完成测试进程的真实退出码。raw_result 保留 FF
  runner 的原始 JSON；Retire 捕获的 error 使用同一
  action/status/summary/stdout/stderr/message 形状。

results/pairs.jsonl
  按去重后 accepted candidate 的确定顺序汇总真正进入 Pair 处理的最终结果。
  每行保留 pair_id、repo_key、status、test_summary 和 error；completed 行再保留
  selection_result。精确重复 Patch 和 baseline 未通过 repo 下未执行的 Pair 不写入。

results/repos.jsonl
  一行一个 repo，只保存 baseline_status、三种 Pair status 的计数、
  main_task/no_f2p 计数和任务数。repo 不再使用容易误解的顶层 status；某个 Pair
  construction_failed 不会否定同 repo 已成功生成的任务。

results/summary.json
  保存与终端一致的全局统计：repo 数、baseline status 计数、Pair status 计数、
  selection 计数和正式任务数。

tasks/<pair_id>/manifest.json
  单个正式任务的完整元数据。Retire 内部只使用 pair_id；导出到 Harbor 时
  Harbor task ID 直接等于 pair_id，不维护第二套 task_id。
  它记录：
    - pair / repo / commit 基本信息
    - candidate pair 的统计和诊断字段
    - task_base_full 的构造方式
    - pair 级 patch 和 private materials
    - FF Stage2 run ID 和 validation test 数量
    - 测试表现分流结果和测试摘要

tasks/<pair_id>/task_instruction.md
  agent 可见的高层任务说明。
  由固定模板生成，不调用 agent 或 LLM。

tasks/<pair_id>/eval_assets/
  evaluator 后台使用的运行材料。
  它们包括 FF Stage2 产出的 Dockerfile、run_script.sh 和隐藏测试入口文件快照。

  validation_test_snapshots.jsonl 是 validation_test_snapshots/ 的 manifest，一行记录一个验证测试入口文件快照：
    - repo-relative path
    - storage path
    - sha256

  validation_test_snapshots/ 中保存后台验证需要恢复的测试入口文件快照。
  这些文件来自 validation_test_universe，也就是 FF Stage2 在 new_commit 环境中文件级 status == passed 的测试文件集合。

  Harbor adapter 从 Dockerfile、obsolete patch 和 hidden test path list 重建 agent 可见的 task base；不依赖 Retire 中持久化的完整 repo。Verifier 恢复这些 snapshots 后调用 run_script.sh。逐文件评分基线来自 Retire Pair 结果中冻结的 `task_base_status`；独立 Oracle Job 用于确认 Gold Patch 在 Harbor 最终资源环境中恢复全部测试。

tasks/<pair_id>/private_materials/
  当前正式 task 对应的 pair 级私有材料副本。
  这些文件来自 candidate_pairs/pair_outputs/<pair_id>/。
  它们不进入 agent workspace，但用于人工复查、task_base 构造和后续评分设计。
  其中 obsolete_reinsert.patch 是 task_base 构造输入：

    new_commit + private_materials/obsolete_reinsert.patch = task_base_full

  reinsert_targets.jsonl 是 private audit material，用于解释 Belta 插回了哪些旧实现残留；它不是最终 oracle。

  gold_cleanup.patch 是参考答案 patch：

    task_base_full + gold_cleanup.patch = new_commit

  它使用 `--minimal --binary --no-renames` diff 口径，并反向表示清理操作。`obsolete_reinsert.patch` 必须严格 add-only；`gold_cleanup.patch` 必须严格 delete-only，即除 `+++` 文件头外不得包含正文 `+` 行。生成结果违反该约束时，当前 pair 构造失败，不写出不符合任务定义的参考答案。`gold_cleanup.patch` 用于后续评分、人工审查和 Harbor export；不进入普通 agent 可见内容。
  生成该 patch 时，临时 Git index 必须先通过 `read-tree <new_commit>` 初始化。这样 diff 只包含 task_base_full 相对 new_commit 的旧实现残留，不会把目标版本中的正常文件误记为 oracle 内容。
```

#### Retire Task Construction 续跑

本阶段不再在命令启动时删除整个 `retire_task_construction/`。续跑状态源是：

```text
retire_task_construction/checkpoints/baselines/<repo_key>.json
retire_task_construction/checkpoints/pairs/<pair_id>.json
```

repo baseline 续跑规则：

```text
checkpoint 不存在或 input_fingerprint 与当前环境/测试输入不一致
  -> 基线没有完整结束，重新运行完整基线。

status == passed
  -> 基线已完整通过，不再运行；允许进入 pair checkpoint 复用/执行。

status == failed
  -> 基线已完整运行但没有全部通过；舍弃 repo，不处理任何 pair。

status == infra_error
  -> 上次未能完整运行；本次重新进入 baseline 初跑阶段。
```

baseline checkpoint 使用临时文件加原子 rename 写入。单次 Retire
命令不自动重试；本次 attempt 受控结束后立即写入 `passed`、
`failed` 或 `infra_error`。续跑自动重新执行 `infra_error`；要手动重验
`failed` 时，删除该 repo 的 baseline JSON 后再运行同一命令。

对材料完整的 pair，Belta 根据当前 candidate row、pair 级必要材料、
environment 必要材料和任务提示词计算输入 fingerprint。该 fingerprint
用于判断 `completed` checkpoint 能否复用；输入变化会使旧
`completed` checkpoint 失效并重新执行。

completed checkpoint 只有同时满足以下条件才可复用：

```text
status == completed
pair_id / repo_key / old_commit / new_commit 与当前 candidate 一致
input_fingerprint 与当前输入一致
selection_result == no_f2p
  或 selection_result == main_task 且 tasks/<pair_id>/ 必要产物完整
```

`status == construction_failed` 和 `status == infra_error` 都不是可复用终态。
续跑时会删除对应半成品 task，重新检查材料、构造逻辑或测试基础设施。
这两类操作都不调用模型；修复输入材料或构造代码后，旧失败不会永久阻止重跑。

对于 completed checkpoint，以下情况重新执行当前 pair：

```text
checkpoint 不存在
checkpoint 不完整或无法解析
input_fingerprint 不匹配
main_task checkpoint 对应的 tasks/<pair_id>/ 不完整
results/pair_test_results/<pair_id>.jsonl 缺失、无法解析，
或逐文件状态与 test_summary 不一致
```

checkpoint 使用临时文件加原子 rename 写入。程序在 pair 中途退出时，不会留下一个被误认为
终态的半成品 checkpoint。旧 task 只有在对应 completed/main_task checkpoint
可复用时才保留；其它情况会在重跑当前 pair 前删除。

#### Repo Docker Image 准备

同一 repo 的所有 candidate pairs 共享一个 FF environment Docker image。Belta 对每个 repo：

```text
1. 先完成所有需要执行的 repo baseline；在此之前不启动任何 pair。
2. 每个 baseline attempt 使用全新容器，同一次 Retire 命令内不自动重试。
3. baseline passed 后，只复用输入与详细结果都完整一致的 completed
   pair checkpoint；construction_failed、infra_error 和没有有效 checkpoint
   的 pairs 提交到全局 pair 池。
4. 如果 baseline 已在上次运行中 passed，续跑不重复基线；只有确实要执行新 pair 时才准备 Docker image。
5. baseline 的 image、容器或 runner 异常立即写 `infra_error`；Baseline 和 Pair
   的 `infra_error` 都在下次手动续跑时重新执行一次。
6. 完整 baseline 存在真实测试失败时直接写 failed，后续舍弃 repo。
```

FF environment image 内包含 `new_commit`、项目依赖和 FF Stage2 准备的运行环境。Belta 按环境构建上下文内容和构建平台生成稳定 image tag：先检查本机 image，缺失时获取构建锁并再次检查，仍缺失才执行 `docker build`。因此相同环境可以跨 run 直接复用，且并发任务不会重复构建。

对每个待验证 pair，Belta 从同一基础 image 启动一个全新容器：

```text
FF environment image
  new_commit + dependencies

Pair A 临时容器
  启动后应用 Patch A

Pair B 临时容器
  启动后应用 Patch B
```

`obsolete_reinsert.patch` 只通过标准输入送入对应容器，不保存到容器文件系统。这样每个 repo 只需要准备基础 environment image，不再为每个 pair 执行一次 Docker build。每个 pair 在容器内逐文件调用 FF `run_script.sh`；验证完成后删除容器。只有产生 F2P 的 main task 才在宿主机物化一次 task_base_full，用于生成 hidden test snapshots 和 gold cleanup patch。

不同 Pair 的容器名、输出目录、task 和 checkpoint 均相互独立。同一 repo 的 Pair 只读复用其基础 environment image；不同 repo 的 Pair 使用各自的基础 image。正式 task 工作区通过 bare cache 的只读 commit 导出构造，不切换或修改共享 bare cache 的 `HEAD`，因此可以安全并行物化。

`tasks/<pair_id>/manifest.json` 示例结构：

```json
{
  "pair_id": "pallets__flask__oldsha__newsha",
  "repo": {
    "repo_key": "pallets__flask",
    "full_name": "pallets/flask",
    "github_repo_id": 596892,
    "clone_url": "https://github.com/pallets/flask.git",
    "default_branch": "main"
  },
  "commits": {
    "old_commit": "oldsha...",
    "new_commit": "newsha..."
  },
  "candidate_stats": {
    "removed_object_targets_count": 12,
    "removed_range_targets_count": 5,
    "implementation_units_count": 42,
    "reinsert_source_lines": 980,
    "task_base_compile": "failed",
    "task_base_compile_error": "SyntaxError: ..."
  },
  "task_base_full": {
    "base_commit": "newsha...",
    "construction": "apply private_materials/obsolete_reinsert.patch to new_commit",
    "patch": "private_materials/obsolete_reinsert.patch",
    "agent_visible": false
  },
  "private_materials": {
    "source_pair_output_dir": "candidate_pairs/pair_outputs/pallets__flask__oldsha__newsha",
    "pair_metadata": "private_materials/pair_metadata.json",
    "reinsert_extraction_patch": "private_materials/reinsert_extraction.patch",
    "reinsert_targets": "private_materials/reinsert_targets.jsonl",
    "implementation_units": "private_materials/implementation_units.jsonl",
    "obsolete_reinsert_patch": "private_materials/obsolete_reinsert.patch",
    "gold_cleanup_patch": "private_materials/gold_cleanup.patch"
  },
  "eval_assets": {
    "dockerfile": "eval_assets/Dockerfile",
    "run_script": "eval_assets/run_script.sh",
    "validation_test_snapshots": "eval_assets/validation_test_snapshots.jsonl",
    "validation_test_snapshots_dir": "eval_assets/validation_test_snapshots",
    "ff_stage2_run_id": "561b124a-56a5-4147-943a-88aaab9eb155",
    "validation_test_universe_file_count": 22
  },
  "validation": {
    "full_validation_timeout_seconds": 1800
  },
  "selection": {
    "selection_result": "main_task",
    "reason": "Environment Construction passed validation_test_universe; task_base_full failed at least one file"
  },
  "test_summary": {
    "new_commit_total_files": 22,
    "new_commit_passed_files": 22,
    "task_base_total_files": 22,
    "task_base_passed_files": 20,
    "task_base_failed_files": 2,
    "task_base_error_files": 0
  },
  "paths": {
    "task_dir": "retire_task_construction/tasks/pallets__flask__oldsha__newsha",
    "manifest": "manifest.json",
    "task_instruction": "task_instruction.md"
  }
}
```

#### 本阶段边界

Retire Task Construction 只负责从 accepted candidate pair 构造最终 task 候选并完成任务级筛选。

正式 benchmark 在进入 Harbor 前还要执行一次独立的数据质量冻结：对每个拥有
`main_task` 的 repo，逐文件、逐目标块审查全部去重候选，再在同仓库内横向比较并
选择一题。编译状态、Patch 大小和测试失败数量只作为诊断，不是自动硬筛选条件。
冻结后再统计插回行数、实现单元数、文件数、跨文件分散度和最大单文件占比等分布。
该步骤使用 Retire 产物做审查，不改变 Candidate、Environment、Retire checkpoint
或原始任务材料。

Agent 执行和 agent 输出评分发生在后续 Evaluation 阶段。

Harbor Evaluation 使用分阶段网络策略，避免 agent 通过上游仓库历史直接定位
`old_commit -> new_commit` 的真实删除提交：

```text
agent setup:
  允许联网，仅用于安装所选 agent 运行时。

agent run:
  默认禁止公网访问；运行时只将当前模型 API hostname 加入 allowlist。
  不允许访问 GitHub、代码搜索、上游 raw files 或其他外部答案来源。

verifier:
  允许联网；它只负责运行测试和汇总结果，不参与 agent 解题。
```

模型 API hostname 由运行命令根据 `LLM_BASE_URL` 提取，并通过 Harbor
`--allow-agent-host` 注入；它不写死在 task 数据中。Belta Harbor task 的
`[agent]` 使用 `network_mode = "no-network"`，`[verifier]` 和
`[environment]` 使用 `network_mode = "public"`。Mini-SWE-Agent 运行时只放行
模型 API hostname；Oracle 不调用模型，也不放行任何公网主机。

本地 Docker Evaluation 统一通过 Belta Harbor runner 启动。runner 直接读取
Belta `.env`，固定使用 Docker Engine 的 default builder，并在运行前检查 Harbor
egress sidecar 所需的 `nft_fib_inet`、`nft_redir` 和 `nft_reject_inet` Linux
内核模块。模块文件存在但尚未加载时，runner 使用仅授予 `SYS_MODULE` 能力的一次性
helper container 加载模块；加载失败则在 agent run 前终止，不将基础设施错误记为
agent 结果。egress sidecar 只放行容器 `/etc/resolv.conf` 中明确配置的 DNS
resolver，不开放任意 DNS 或其他公网目标。

Harbor export 对每个 `task_id` 只维护一个当前任务目录。重新导出同一任务时直接
替换当前目录，不保留并行旧版本。Retire 已在 Pair 构造阶段对完整冻结测试集合运行
task base；导出器逐项核对 `results/pair_test_results/<pair_id>.jsonl` 的文件顺序和
文件级状态，并将每个文件的 `task_base_status` 写入 Harbor task 的
`tests/manifest.json`。发布冻结数据前运行完整 Oracle Job，确认 Gold Patch 在最终
Harbor 环境中可以恢复全部测试；需要时可独立运行 Nop，核对最终环境与 Retire 基线，
但 Nop 不替换冻结基线，也不参与 reward。

Oracle、Nop 和模型 Agent 是彼此独立的 Harbor Job。模型 Job 不读取 Oracle 结果；
每次运行的结果和异常均保留在对应 Harbor Job 的
原生 Trial 目录中。最终发布数据集只包含完成审核并决定保留的任务目录；Harbor 直接
扫描这些目录运行，临时子集使用原生 `--include-task-name` / `--exclude-task-name`
选择，不复制或软链接第二份数据集。任务重新导出后，应对新材料重新运行 Oracle。
`tests/manifest.json` 是唯一测试索引；每行记录测试路径、快照路径、SHA-256 和
`task_base_status`。

Harbor 直接使用 Retire 已构建的仓库级干净 `new_commit` 环境镜像，不在 Docker build
中应用 Pair Patch，也不为每题构建 Agent 包装镜像。Agent diff 采集代码直接内嵌在
Harbor verifier collect hook 中，不由 prepare 安装文件。每个 Agent、Oracle 或独立
Verifier 容器启动并通过 healthcheck 后，Harbor 才执行 task 的可信 runtime prepare：

```text
上传临时 prepare 材料
→ 应用 obsolete_reinsert.patch
→ 删除 hidden tests 和旧 .git
→ 将整个可见仓库重新物化到新目录
→ 归一化全部可见路径的 atime / mtime
→ 初始化唯一的 belta-task-base commit
→ 审计无 remote、无 unreachable objects、无 hidden tests
→ 删除临时 prepare 材料
→ Agent setup
```

完整仓库重新物化使所有可见文件在同一轮创建，避免被插回文件形成独立的
ctime / inode birth-time 簇；固定 atime / mtime 避免 Patch 目标通过文件时间被直接
定位。Prepare 目录从不作为持久 mount 或 image layer 暴露给 Agent。

Retire 环境镜像使用冻结环境目录和 Docker platform 的内容身份。本机镜像存在时校验
stage 与 build-context 标签后直接启动；缺失时由 Belta 启动器使用冻结 Dockerfile
执行一次 `docker build`。同一环境构建身份的任务复用一个仓库级环境镜像；正式导出前
必须人工审查 Dockerfile 的 `COPY/ADD` 和构建产物，确认 Agent 可见环境不包含答案
副本。通过审查的 Dockerfile 是完整重建输入。Harbor 使用默认容器清理行为；Agent 与 Verifier 的
uv/pip/Hugging Face/npm/yarn 缓存分别通过 host bind mount 复用，二者宿主目录隔离。

Agent run 结束后，Harbor 的 verifier collect hook 在 agent 容器中生成：

```text
agent_changes.patch = diff(task_base agent-visible baseline, agent output repo)
```

`agent_changes.patch` 使用与 `obsolete_reinsert.patch` / `gold_cleanup.patch` 一致的
`--minimal --binary --no-renames` diff 口径，避免 Git 因重复或相似代码对齐产生非必要的
删除再新增。它作为 Harbor artifact 落到 trial 的
`artifacts/logs/artifacts/agent_changes.patch`，不进入 agent workspace。Agent 容器随后
停止；Verifier 从干净 task base 启动独立容器，只应用该 artifact，再恢复 hidden tests
并评分。该结构支持 Harbor 官方 regrade：固定 Agent artifact，只重新运行 Verifier，
无需再次调用模型。

Verifier 在每个测试文件完成后，将所有已完成文件原子写入
`verifier/test_results.jsonl`，外层字段与 Retire 一致。Runner 给出的文件级
`status=failed` 直接记为失败，非负测试点计数只作诊断，不要求与 `collected`
  相加一致。`status=passed` 必须同时满足至少收集并通过一个测试点、failed/errors
  为 0 且五项计数自洽；进程退出码只作诊断。无效或缺失结果 JSON、未知状态和 runner
执行失败表示基础设施错误，本次不产生有效 Harbor 评分。未实际完成的文件不伪造
`test_results.jsonl` 结果行。

Verifier 在测试前先原子写入仅含删除行和 Patch 相似度的临时
`verifier/belta_patch_metrics.json`。正常完成时生成 `belta_results.json` 和
`reward.json`，随后删除临时文件。若 Harbor 因完整 Verifier 超过总预算而记录精确的
`VerifierTimeoutError`，Belta Result Plugin 保留该异常和缺失的 Harbor reward，使用
冻结 baseline 与临时 Patch 指标按“全部测试文件失败”口径补写
`belta_results.json`，再删除临时文件。其他基础设施异常不转换，也不生成
`belta_results.json` 或 `reward.json`。

Verifier 将 Belta 成功评分的汇总明细写入
`verifier/belta_results.json`：

```text
fixed      = Retire task_base failed -> Agent passed 的测试文件
regressed  = Retire task_base passed -> Agent failed 的测试文件

regression_gated_recovery =
    0,                                if regressed > 0
    fixed / task_base failed,         otherwise
```

因此，`regression_gated_recovery = 1` 当且仅当所有原始失败测试文件均已修复，
并且没有原始通过测试文件发生回归。Agent 阶段超时本身不改变该指标；只要 Patch
已被收集且 Verifier 完整执行，仍按实际测试文件状态计算。完整 Verifier 超时时没有
有效 Harbor reward，插件补写的 Belta 分析指标按既定全失败口径全部记为 0。

Harbor 主评分文件 `reward.json` 只写二进制 `reward`：全部冻结测试文件通过为 1，
任一文件失败为 0。上述恢复率、删除行和 Patch 相似度只保留在
`belta_results.json`，不作为额外 Harbor reward 字段。

若测试执行因其他基础设施错误而不完整，本次 Verifier 不写 reward，Harbor 在 Trial
结果中保留异常。已完成文件的真实状态和错误诊断继续保留，但不转换成模型失败指标。

删除行指标在同一 task base 上比较 Gold 和 Agent 实际删除的非空源码行。每行用
`repo path + task-base 原始行号 + 精确源码文本` 标识：

```text
gold_coverage  = matched_lines / gold_lines
agent_precision = matched_lines / agent_lines
overlap_f1     = gold_coverage 与 agent_precision 的调和平均
```

`patch_code_similarity` 使用 Patch-only CrystalBLEU。只读取 Gold / Agent Patch 中
非空的正文 `+` / `-` 行，不读取 diff header、hunk header 或上下文；使用 Python
tokenizer（不可 token 化时回退到确定性词法切分），并为每个 token 加上 ADD / DEL
方向。比较 1 到 4 token n-gram 时，从最终冻结的一 repo 一题 Gold Patch 语料中
统计并忽略频率最高的 500 个 n-gram；该固定表随 task 导出到
`tests/common_ngrams.json`，后续 Agent run 与 regrade 共用同一份表。完全相同的
非空 Patch 得分为 1，空答案得分为 0。该指标是无需模型、无需源码上下文的确定性
代码近似度，不替代测试恢复或删除行精确重合。

Harbor 的 `reward` 仍为“全部保留测试通过”的二值结果；测试增量、删除行指标和
`patch_code_similarity` 分别报告，不提前合并为单一总分。

当前 Harbor 数据集只将 `[verifier].timeout_sec` 设置为整次验证 1800 秒，不设置
固定单文件上限。完整 Verifier 超时不写有效 Harbor reward，Harbor 保留
`VerifierTimeoutError`；Belta Result Plugin 另按既定全失败政策补齐分析指标，不修改
Harbor 原始 Trial 结果。

#### Harbor Job 产物目录

每次 `harbor run` 在 Harbor checkout 的 `jobs/` 下创建一个 job 目录。一个 task 的一次
Agent 尝试对应其中一个 trial 子目录；trial 名由 task ID 和 Harbor 生成的唯一后缀组成。

```text
jobs/<job_name>/
  config.json
  lock.json
  job.log
  result.json
  <trial_name>/
    config.json
    lock.json
    trial.log
    result.json
    agent/
      mini-swe-agent.txt
      mini-swe-agent.trajectory.json
      trajectory.json
    artifacts/
      manifest.json
      logs/artifacts/
        agent_changes.patch
    verifier/
      test-stdout.txt
      test-stderr.txt
      test_results.jsonl
      belta_results.json
      reward.json
```

Agent 运行期间，`mini-swe-agent.txt` 和 `mini-swe-agent.trajectory.json` 持续更新，
可直接用于实时检查。Agent 结束后，Harbor 再生成标准 `trajectory.json`、Artifact
清单和 `agent_changes.patch`。Verifier 完成后写出逐文件测试与删除行明细、评分指标、
trial `result.json` 和 job 级 `result.json`。Gold 始终位于 task 数据集的
`solution/gold.patch`，不会复制到 Agent 工作区。

Trial `result.json` 记录 `environment_setup`、`task_preparation`、`agent_setup`、
`agent_execution` 和 `verifier` 的开始/结束时间；稳定 image 的 build/reuse 决策写入
`job.log` / `trial.log`。这些产物用于区分环境启动、可信 task 构造、Agent 安装、
模型执行和评分时间，不依赖终端输出。

### Data 文件夹结构

```text
data/
  cache/
    repos/
      <owner>/
        <repo>.git

  runtime/
    featurefactory/
      stage2/
        runs/
        cache/

  runs/
    <run_id>/
      config.yaml

      repo_discovery/
        ff_job.json
        repositories.jsonl

      candidate_pairs/
        repo_outputs/
          <repo_key>/
            repo_result.json
            accepted_candidates.jsonl
            rejected_candidates.jsonl
        pair_outputs/
          <pair_id>/
            metadata.json
            reinsert_extraction.patch
            reinsert_targets.jsonl
            implementation_units.jsonl
            obsolete_reinsert.patch
        repo_results.jsonl
        accepted_candidates.jsonl
        rejected_candidates.jsonl

      environments/
        <repo_key>/
          manifest.json
          planner_guidance.md
          Dockerfile
          run_script.sh
          collect_report.json
          full_report.json
          test_results.jsonl

      retire_task_construction/
        checkpoints/
          baselines/
            <repo_key>.json
          pairs/
            <pair_id>.json
        tasks/
          <pair_id>/
            manifest.json
            task_instruction.md
            eval_assets/
              Dockerfile
              run_script.sh
              validation_test_snapshots.jsonl
              validation_test_snapshots/
                <snapshot files>
            private_materials/
              pair_metadata.json
              reinsert_extraction.patch
              reinsert_targets.jsonl
              implementation_units.jsonl
              obsolete_reinsert.patch
              gold_cleanup.patch
        results/
          baseline_test_results/
            <repo_key>.jsonl
          pair_test_results/
            <pair_id>.jsonl
          pairs.jsonl
          repos.jsonl
          summary.json
```

含义：

- `data/cache/repos/`：跨 run 复用的 bare git cache，不属于某一次 run。
- `data/runtime/featurefactory/stage2/`：Belta 调用 FeatureFactory Stage2 时使用的运行时工作区和 FF 内部 cache，包括 Stage2 run workspace、repo checkout 和 FF Stage2 自己的 cache。它不是 Belta 的正式 run 产物。
- `data/runs/<run_id>/`：一次 Belta run 的所有产物。
- `config.yaml`：这次 run 使用的配置副本。
- `repo_discovery/`：FF Stage1 抓到的 repo 清单。
- `candidate_pairs/`：从 repo 历史里生成的 candidate pair、repo 级输出、pair 级实现侧 diff / 对象抽取产物，以及 accepted/rejected 汇总结果。
- `environments/`：每个 repo 的 FF Stage2 成功环境产物。
- `retire_task_construction/`：从 accepted candidate pair 构造 Retire Obsolete Implementation task 的阶段产物。`checkpoints/` 用于续跑，`results/` 用于统计，`tasks/` 只保留进入主数据集的正式任务。

Belta run 目录当前不额外创建 `evaluation/` 或 `exports/`。Harbor Evaluation 的
任务数据、job、trial 和 Verifier 产物按前文 Harbor 目录约定写入 Harbor checkout。
