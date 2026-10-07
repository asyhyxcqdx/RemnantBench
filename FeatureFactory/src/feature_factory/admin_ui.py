INDEX_HTML = """<!doctype html>
<html lang="zh-CN" data-theme="light">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>FeatureFactory</title>
  <link rel="icon" type="image/png" href="/static/feature_factory-logo.png">
  <script>
    (() => {
      const storageKey = "feature_factory:admin_theme";
      let theme = "light";
      try {
        const saved = localStorage.getItem(storageKey);
        if (saved === "light" || saved === "dark") {
          theme = saved;
        } else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
          theme = "dark";
        }
      } catch (error) {
        theme = "light";
      }
      document.documentElement.dataset.theme = theme;
    })();
  </script>
  <link rel="stylesheet" href="/static/admin.css">
</head>
<body>

  <div class="page">
    <header class="panel topbar">
      <div class="brand">
        <h1>FeatureFactory 管理后台</h1>
        <div class="meta" id="refresh-meta">最近刷新 加载中...</div>
      </div>
      <div class="topbar-controls">
        <nav class="tabs-shell" aria-label="FeatureFactory Stages">
          <button class="tab-button active" type="button" data-tab="batch">批量任务</button>
          <button class="tab-button" type="button" data-tab="data-pools">数据池</button>
          <button class="tab-button" type="button" data-tab="assets">镜像管理</button>
          <button class="tab-button" type="button" data-tab="stage1">Stage1 仓库抓取</button>
          <button class="tab-button" type="button" data-tab="stage2">Stage2 环境构造</button>
          <button class="tab-button" type="button" data-tab="stage3">Stage3 特征破坏</button>
          <button class="tab-button" type="button" data-tab="stage4">Stage4 Issue 生成</button>
        </nav>
        <button
          class="theme-toggle"
          id="theme-toggle"
          type="button"
          aria-label="切换深浅主题"
          aria-pressed="false"
        >
          <span class="theme-toggle-kicker">主题</span>
          <strong class="theme-toggle-icon" id="theme-toggle-value" aria-hidden="true">☀</strong>
        </button>
      </div>
    </header>

    <section class="pane active" id="pane-batch">
      <div class="stage-layout batch-layout" id="batch-layout">
        <div class="batch-left-shell" id="batch-left-shell">
          <div class="left-stack batch-left-stack">
          <section class="panel batch-create-panel" id="batch-create-panel">
            <div class="section-title">
              <h3>创建批量任务</h3>
              <div class="section-actions">
                <button
                  class="secondary tiny batch-header-toggle"
                  id="batch-layout-collapse-toggle"
                  type="button"
                  aria-controls="batch-create-panel"
                  aria-expanded="true"
                  aria-label="收起创建批量任务面板"
                  title="收起创建批量任务面板"
                >
                  <span class="batch-header-toggle-icon" aria-hidden="true">◂</span>
                  <span class="batch-header-toggle-label">收起</span>
                </button>
              </div>
            </div>
            <form id="batch-form">
              <div class="field-grid batch-top-grid">
                <label>
                  任务名
                  <input name="name" required>
                </label>
                <label>
                  流程终点
                  <select name="stop_after_stage">
                    <option value="stage4">完整流程（Stage2 → Stage3 → Stage4）</option>
                    <option value="stage2">仅环境构造（Stage2 完成后停止）</option>
                  </select>
                </label>
                <div class="batch-data-pool-anchor">
                  <label>
                    数据池
                    <select id="batch-data-pool-select" name="data_pool_id"></select>
                  </label>
                  <div class="batch-data-pool-popover" id="batch-data-pool-popover" hidden>
                    <div class="batch-data-pool-modal" role="dialog" aria-modal="true" aria-labelledby="batch-new-data-pool-title">
                      <div class="repo-filter-title" id="batch-new-data-pool-title">新建数据池</div>
                      <div class="field-grid">
                        <label>
                          新数据池名称
                          <input id="batch-new-data-pool-name" autocomplete="off" placeholder="例如 Python 数据池">
                        </label>
                        <label>
                          新数据池路径
                          <input id="batch-new-data-pool-root" autocomplete="off" placeholder="/absolute/path/to/data-pool">
                        </label>
                      </div>
                      <div class="action-row batch-data-pool-modal-actions">
                        <button id="batch-new-data-pool-create" type="button" class="batch-modal-action primary">创建并选择</button>
                        <button id="batch-new-data-pool-cancel" type="button" class="batch-modal-action secondary">取消</button>
                        <span class="muted" id="batch-new-data-pool-message"></span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <label class="full">
                备注
                <textarea name="note" rows="2"></textarea>
              </label>

              <div class="field-grid quad" data-batch-target-disabled>
                <div class="field-group">
                  <div class="group-title">语言</div>
                  <div class="selector-anchor" id="batch-language-anchor">
                    <button class="selector-toggle" id="batch-language-toggle" type="button" aria-expanded="false">
                      <span class="selector-summary" id="batch-language-summary">Python</span>
                      <span class="selector-caret">▾</span>
                    </button>
                    <div class="selector-popover" id="batch-language-popover" hidden>
                      <div class="selector-list">
                        <label class="selector-option"><input type="checkbox" data-batch-language value="Python" checked><span>Python</span></label>
                        <label class="selector-option"><input type="checkbox" data-batch-language value="TypeScript"><span>TypeScript</span></label>
                        <label class="selector-option"><input type="checkbox" data-batch-language value="JavaScript"><span>JavaScript</span></label>
                        <label class="selector-option"><input type="checkbox" data-batch-language value="Go"><span>Go</span></label>
                        <label class="selector-option"><input type="checkbox" data-batch-language value="Java"><span>Java</span></label>
                        <label class="selector-option"><input type="checkbox" data-batch-language value="Rust"><span>Rust</span></label>
                        <label class="selector-option"><input type="checkbox" data-batch-language value="C++"><span>C++</span></label>
                        <label class="selector-option"><input type="checkbox" data-batch-language value="C"><span>C</span></label>
                      </div>
                    </div>
                  </div>
                </div>
                <label>
                  最低 stars
                  <input name="stars_min" type="number" min="0" value="100" placeholder="可选">
                </label>
                <label>
                  最高 stars
                  <input name="stars_max" type="number" min="0" placeholder="可选">
                </label>
                <label>
                  仓库个数上限
                  <input name="repository_limit" type="number" min="1" placeholder="可选">
                </label>
              </div>
              <div class="checkrow" data-batch-target-disabled>
                <div class="check-item"><input name="include_forks" type="checkbox"><span>包含 fork</span></div>
                <div class="check-item"><input name="include_archived" type="checkbox"><span>包含 archived</span></div>
              </div>
              <div class="field-grid" data-batch-target-disabled>
                <label>
                  创建时间下界
                  <input name="created_after" type="datetime-local" required>
                </label>
                <label>
                  创建时间上界
                  <input name="created_before" type="datetime-local">
                </label>
              </div>
              <div class="field-grid" data-batch-target-disabled>
                <label>
                  最近 push 下界
                  <input name="pushed_after" type="datetime-local">
                </label>
                <label>
                  最近 push 上界
                  <input name="pushed_before" type="datetime-local">
                </label>
              </div>
              <div class="full field-group" data-batch-target-disabled>
                <div class="group-title">许可证白名单</div>
                <div class="selector-anchor" id="batch-license-anchor">
                  <button class="selector-toggle" id="batch-license-toggle" type="button" aria-expanded="false">
                    <span class="selector-summary" id="batch-license-summary">不限</span>
                    <span class="selector-caret">▾</span>
                  </button>
                  <div class="selector-popover" id="batch-license-popover" hidden>
                    <div class="selector-list compact-grid">
                      <label class="selector-option"><input type="checkbox" data-batch-license value="mit"><span>MIT</span></label>
                      <label class="selector-option"><input type="checkbox" data-batch-license value="apache-2.0"><span>Apache-2.0</span></label>
                      <label class="selector-option"><input type="checkbox" data-batch-license value="bsd-2-clause"><span>BSD-2-Clause</span></label>
                      <label class="selector-option"><input type="checkbox" data-batch-license value="bsd-3-clause"><span>BSD-3-Clause</span></label>
                      <label class="selector-option"><input type="checkbox" data-batch-license value="isc"><span>ISC</span></label>
                      <label class="selector-option"><input type="checkbox" data-batch-license value="mpl-2.0"><span>MPL-2.0</span></label>
                      <label class="selector-option"><input type="checkbox" data-batch-license value="lgpl-3.0"><span>LGPL-3.0</span></label>
                    </div>
                  </div>
                </div>
              </div>
              <label class="full" data-batch-target-disabled>
                附加关键词
                <input name="keywords" placeholder="pytest, tests, unit test">
              </label>
              <label class="full">
                抓取指定仓库
                <textarea name="target_repositories" rows="4" placeholder="每行一个 owner/repo"></textarea>
              </label>
              <div class="field-grid triple">
                <label>
                  Stage1 任务并发额度
                  <input name="max_concurrent_jobs" type="number" min="1" max="24">
                </label>
                <label>
                  单任务分片并发数
                  <input name="max_concurrent_partitions" type="number" min="1">
                </label>
                <label>
                  GitHub Token 模板
                  <select id="batch-stage1-template" name="stage1_template_id"></select>
                </label>
              </div>
              <div class="full field-group">
                <div class="group-title">临时 GitHub Token</div>
                <textarea name="github_tokens" rows="3" placeholder="选填，每行一个 token"></textarea>
              </div>
              <div class="field-grid triple">
                <label>
                  Stage2 模板
                  <select id="batch-stage2-template" name="stage2_template_id"></select>
                </label>
                <label>
                  Stage3 模板
                  <select id="batch-stage3-template" name="stage3_template_id"></select>
                </label>
                <label>
                  Stage4 模板
                  <select id="batch-stage4-template" name="stage4_template_id"></select>
                </label>
              </div>
              <details class="advanced-box">
                <summary>临时 Stage2 / Stage3 / Stage4 配置</summary>
                <div class="advanced-content">
                  <div class="group-title">Stage2</div>
                  <div class="stage2-config-section">Planner agent 配置</div>
                  <div class="field-grid triple">
                    <label>模型<input name="stage2_planner_model" type="text"></label>
                    <label>BaseURL<input name="stage2_planner_base_url" type="url"></label>
                    <label>APIKey<input name="stage2_planner_api_key" type="password" autocomplete="off"></label>
                    <label>OpenHands 预设<select name="stage2_planner_preset"><option value="gpt5">gpt5：patch 写文件</option><option value="default">default：默认文件编辑器</option></select></label>
                    <label>最大迭代步数<input name="stage2_planner_max_iterations" type="number" min="10" max="1000" step="1"></label>
                    <label>超时时限（秒）<input name="stage2_planner_timeout_seconds" type="number" min="30" max="14400" step="1"></label>
                  </div>
                  <div class="stage2-config-section">Worker agent 配置</div>
                  <div class="field-grid triple">
                    <label>模型<input name="stage2_worker_model" type="text"></label>
                    <label>BaseURL<input name="stage2_worker_base_url" type="url"></label>
                    <label>APIKey<input name="stage2_worker_api_key" type="password" autocomplete="off"></label>
                    <label>OpenHands 预设<select name="stage2_worker_preset"><option value="gpt5">gpt5：patch 写文件</option><option value="default">default：默认文件编辑器</option></select></label>
                    <label>最大迭代步数<input name="stage2_worker_max_iterations" type="number" min="10" max="1000" step="1"></label>
                    <label>超时时限（秒）<input name="stage2_worker_timeout_seconds" type="number" min="30" max="14400" step="1"></label>
                  </div>
                  <div class="stage2-config-section">超参数</div>
                  <div class="field-grid triple">
                    <label>任务并发额度<input name="stage2_max_concurrent_runs" type="number" min="1" max="24" step="1"></label>
                    <label>validate tool 调用次数上限<input name="stage2_max_worker_attempts" type="number" min="1" max="10" step="1"></label>
                    <label>快检抽样数量<input name="stage2_quickcheck_sample_size" type="number" min="1" max="100" step="1"></label>
                    <label>collect 超时上限（秒）<input name="stage2_collect_timeout_seconds" type="number" min="10" max="7200" step="1"></label>
                    <label>run 单个测试文件超时上限（秒）<input name="stage2_run_test_timeout_seconds" type="number" min="10" max="7200" step="1"></label>
                    <label>build 超时上限（秒）<input name="stage2_build_timeout_seconds" type="number" min="30" max="7200" step="1"></label>
                    <label>full validation 超时上限（秒）<input name="stage2_full_validation_timeout_seconds" type="number" min="30" max="28800" step="1"></label>
                    <label>入口文件测试点个数下限<input name="stage2_entry_file_test_count_min" type="number" min="-1" step="1"></label>
                    <label>P2P 集合文件数量上限<input name="stage2_p2p_file_count_limit" type="number" min="1" step="1" placeholder="不截断"></label>
                  </div>
                  <div class="group-title">Stage3</div>
                  <div class="stage2-config-section">Breaker agent 配置</div>
                  <div class="field-grid triple">
                    <label>模型<input name="stage3_breaker_model" type="text"></label>
                    <label>BaseURL<input name="stage3_breaker_base_url" type="url"></label>
                    <label>APIKey<input name="stage3_breaker_api_key" type="password" autocomplete="off"></label>
                    <label>OpenHands 预设<select name="stage3_breaker_preset"><option value="default">default：默认文件编辑器</option><option value="gpt5">gpt5：patch 写文件</option></select></label>
                    <label>最大迭代步数<input name="stage3_breaker_max_iterations" type="number" min="10" max="1000" step="1"></label>
                    <label>超时时限（秒）<input name="stage3_breaker_timeout_seconds" type="number" min="30" max="14400" step="1"></label>
                  </div>
                  <div class="stage2-config-section">超参数</div>
                  <div class="field-grid triple">
                    <label>任务并发额度<input name="stage3_max_concurrent_runs" type="number" min="1" max="24" step="1"></label>
                    <label>build 超时上限（秒）<input name="stage3_build_timeout_seconds" type="number" min="30" max="7200" step="1"></label>
                    <label>run 单个测试文件超时上限（秒）<input name="stage3_run_test_timeout_seconds" type="number" min="10" max="7200" step="1"></label>
                    <label>full validation 超时上限（秒）<input name="stage3_full_validation_timeout_seconds" type="number" min="30" max="28800" step="1"></label>
                    <label>入口文件最高允许通过率（0-1）<input name="stage3_entry_pass_rate_ceiling" type="number" min="0" max="1" step="0.01"></label>
                    <label>最少删除实现代码行数<input name="stage3_min_removed_code_lines" type="number" min="0" max="10000" step="1" value="10"></label>
                  </div>
                  <div class="group-title">Stage4</div>
                  <div class="stage2-config-section">Issuer agent 配置</div>
                  <div class="field-grid triple">
                    <label>模型<input name="stage4_issuer_model" type="text"></label>
                    <label>BaseURL<input name="stage4_issuer_base_url" type="url"></label>
                    <label>APIKey<input name="stage4_issuer_api_key" type="password" autocomplete="off"></label>
                    <label>OpenHands 预设<select name="stage4_issuer_preset"><option value="default">default：默认文件编辑器</option><option value="gpt5">gpt5：patch 写文件</option></select></label>
                    <label>最大迭代步数<input name="stage4_issuer_max_iterations" type="number" min="10" max="1000" step="1"></label>
                    <label>超时时限（秒）<input name="stage4_issuer_timeout_seconds" type="number" min="30" max="14400" step="1"></label>
                  </div>
                  <div class="stage2-config-section">生成参数</div>
                  <div class="field-grid triple">
                    <label>任务并发额度<input name="stage4_max_concurrent_runs" type="number" min="1" max="24" step="1"></label>
                    <label>build 超时上限（秒）<input name="stage4_build_timeout_seconds" type="number" min="30" max="7200" step="1"></label>
                  </div>
                </div>
              </details>
              <button class="primary" id="batch-submit-btn" type="submit">启动批量任务</button>
              <div class="muted" id="batch-form-message"></div>
            </form>
          </section>
          </div>
        </div>
        <div class="right-stack">
          <section class="panel">
            <div class="section-title">
              <h3>批量任务列表</h3>
              <div class="section-actions">
                <button
                  class="secondary tiny batch-header-toggle"
                  id="batch-layout-expand-toggle"
                  type="button"
                  aria-controls="batch-create-panel"
                  aria-expanded="false"
                  aria-label="展开任务创建"
                  title="展开任务创建"
                  hidden
                >
                  <span class="batch-header-toggle-icon" aria-hidden="true">▸</span>
                  <span class="batch-header-toggle-label">展开任务创建</span>
                </button>
                <button class="secondary tiny" id="batch-refresh-btn" type="button">刷新</button>
              </div>
            </div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>任务</th>
                    <th>状态</th>
                    <th>数据池</th>
                    <th>产出</th>
                    <th>创建时间</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody id="batch-tasks-body"></tbody>
              </table>
            </div>
          </section>
          <section class="panel" id="batch-detail-panel" hidden>
            <div class="section-title batch-detail-titlebar">
              <h3 id="batch-detail-title">批量任务详情</h3>
              <div class="batch-detail-error" id="batch-detail-error" hidden></div>
              <div class="section-actions">
                <button class="secondary tiny" id="batch-cancel-btn" type="button">取消任务</button>
                <button class="secondary tiny danger" id="batch-delete-btn" type="button">删除任务</button>
                <span class="muted" id="batch-detail-message"></span>
              </div>
            </div>
            <div class="detail-body" id="batch-detail-body"></div>
          </section>
        </div>
      </div>
      <div class="batch-data-pool-popover" id="batch-retry-popover" hidden>
        <div class="batch-data-pool-modal batch-retry-modal" role="dialog" aria-modal="true" aria-labelledby="batch-retry-title">
          <div>
            <div class="repo-filter-title" id="batch-retry-title">选择重试配置</div>
            <div class="muted" id="batch-retry-subtitle"></div>
          </div>
          <div class="batch-retry-options">
            <button id="batch-retry-original-btn" class="batch-retry-option" type="button" data-runtime-config-source="original">
              沿用原任务配置
            </button>
            <button id="batch-retry-current-btn" class="batch-retry-option" type="button" data-runtime-config-source="current">
              使用当前全局配置
            </button>
          </div>
          <div class="action-row batch-data-pool-modal-actions">
            <button id="batch-retry-cancel-btn" type="button" class="batch-modal-action secondary">取消</button>
            <span class="muted" id="batch-retry-message"></span>
          </div>
        </div>
      </div>
    </section>

    <section class="pane" id="pane-data-pools">
      <div class="stage-layout data-pool-layout detail-hidden" id="data-pool-layout">
        <div class="left-stack data-pool-list-stack">
          <section class="panel">
            <div class="section-title">
              <h3 id="data-pool-assets-title">默认数据池</h3>
              <div class="section-actions">
                <button class="secondary tiny" id="data-pool-prev-btn" type="button">上一页</button>
                <button class="secondary tiny" id="data-pool-next-btn" type="button">下一页</button>
                <span class="pagination-meta" id="data-pool-pagination-meta">-</span>
                <div class="popover-anchor" id="data-pool-selector-anchor">
                  <button class="secondary tiny filter-toggle" id="data-pool-selector-toggle" type="button">数据池</button>
                  <div class="repo-filter-popover" id="data-pool-selector-popover" hidden>
                    <div class="repo-filter-title">选择数据池</div>
                    <div class="data-pool-list" id="data-pool-selector-list"></div>
                  </div>
                </div>
                <div class="popover-anchor" id="data-pool-filter-anchor">
                  <button class="secondary tiny filter-toggle" id="data-pool-filter-toggle" type="button">筛选</button>
                  <div class="repo-filter-popover" id="data-pool-filter-popover" hidden>
                    <div class="repo-filter-title">筛选数据资产</div>
                    <div class="field-grid">
                      <label class="full">
                        全局关键词
                        <input id="data-pool-filter-query" placeholder="repo / entry / commit / run id">
                      </label>
                      <label>
                        Repo
                        <input id="data-pool-filter-repo" placeholder="owner/repo">
                      </label>
                      <label>
                        Commit
                        <input id="data-pool-filter-commit" placeholder="commit sha">
                      </label>
                      <label>
                        语言
                        <input id="data-pool-filter-language" placeholder="Python">
                      </label>
                      <label>
                        入口文件
                        <input id="data-pool-filter-entry-file" placeholder="tests/test_x.py">
                      </label>
                      <label>
                        Depth
                        <input id="data-pool-filter-depth" type="number" min="0">
                      </label>
                      <label>
                        Depth 下界
                        <input id="data-pool-filter-depth-min" type="number" min="0">
                      </label>
                      <label>
                        Depth 上界
                        <input id="data-pool-filter-depth-max" type="number" min="0">
                      </label>
                      <label>
                        Stars 下界
                        <input id="data-pool-filter-stars-min" type="number" min="0">
                      </label>
                      <label>
                        Stars 上界
                        <input id="data-pool-filter-stars-max" type="number" min="0">
                      </label>
                      <label>
                        Stage2 Run ID
                        <input id="data-pool-filter-stage2-run-id" placeholder="run id">
                      </label>
                      <label>
                        Stage3 Run ID
                        <input id="data-pool-filter-stage3-run-id" placeholder="run id">
                      </label>
                      <label>
                        Stage4 Run ID
                        <input id="data-pool-filter-stage4-run-id" placeholder="run id">
                      </label>
                      <label>
                        创建时间下界
                        <input id="data-pool-filter-created-after" type="datetime-local">
                      </label>
                      <label>
                        创建时间上界
                        <input id="data-pool-filter-created-before" type="datetime-local">
                      </label>
                    </div>
                    <div class="repo-filter-actions">
                      <button class="secondary tiny" id="data-pool-filter-reset" type="button">清空</button>
                      <button class="primary tiny" id="data-pool-filter-apply" type="button">应用筛选</button>
                    </div>
                  </div>
                </div>
                <button class="secondary tiny" id="data-pool-download-btn" type="button">下载 zip</button>
                <button class="secondary tiny" id="data-pool-delete-selected-btn" type="button">删除选中</button>
              </div>
            </div>
            <div class="action-row">
              <span class="muted" id="data-pool-message"></span>
            </div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th><input id="data-pool-select-page" type="checkbox" aria-label="选择当前筛选下全部资产"></th>
                    <th><button class="sort-button" type="button" data-data-pool-sort="repo" data-sort-label="REPO">REPO</button></th>
                    <th><button class="sort-button" type="button" data-data-pool-sort="commit" data-sort-label="COMMIT">COMMIT</button></th>
                    <th><button class="sort-button" type="button" data-data-pool-sort="language" data-sort-label="语言">语言</button></th>
                    <th><button class="sort-button" type="button" data-data-pool-sort="stars" data-sort-label="STARS">STARS</button></th>
                    <th><button class="sort-button" type="button" data-data-pool-sort="entry_file" data-sort-label="入口文件">入口文件</button></th>
                    <th><button class="sort-button" type="button" data-data-pool-sort="entry_pass_rate" data-sort-label="入口通过率">入口通过率</button></th>
                    <th><button class="sort-button" type="button" data-data-pool-sort="p2p_count" data-sort-label="P2P / F2P">P2P / F2P</button></th>
                    <th><button class="sort-button" type="button" data-data-pool-sort="diff_lines" data-sort-label="DIFF 行数">DIFF 行数</button></th>
                    <th><button class="sort-button" type="button" data-data-pool-sort="issue_count" data-sort-label="产出 ISSUE">产出 ISSUE</button></th>
                    <th><button class="sort-button" type="button" data-data-pool-sort="depth" data-sort-label="DEPTH">DEPTH</button></th>
                    <th><button class="sort-button" type="button" data-data-pool-sort="created_at" data-sort-label="创建时间">创建时间</button></th>
                  </tr>
                </thead>
                <tbody id="data-pool-assets-body"></tbody>
              </table>
            </div>
          </section>
        </div>
        <div class="right-stack data-pool-detail-stack">
          <section class="panel" id="data-pool-asset-detail-panel" hidden>
            <div class="section-title">
              <div class="detail-title-group">
                <button class="secondary tiny" id="data-pool-asset-detail-back-btn" type="button">返回列表</button>
                <h3 id="data-pool-asset-detail-title">资产详情</h3>
              </div>
              <div class="section-actions">
                <span class="muted" id="data-pool-asset-detail-message"></span>
              </div>
            </div>
            <div class="detail-body" id="data-pool-asset-detail-body"></div>
          </section>
        </div>
      </div>
      <div class="batch-data-pool-popover" id="data-pool-download-popover" hidden>
        <div class="batch-data-pool-modal data-pool-download-modal" role="dialog" aria-modal="true" aria-labelledby="data-pool-download-title">
          <div class="repo-filter-title" id="data-pool-download-title">下载选中数据</div>
          <section class="data-pool-download-option">
            <div class="data-pool-download-option-head">
              <div class="group-title">下载至浏览器</div>
              <div class="muted">将当前选中的资产打包为 zip，并直接下载到浏览器。</div>
            </div>
            <button id="data-pool-download-browser-btn" type="button" class="batch-modal-action primary">下载至浏览器</button>
          </section>
          <section class="data-pool-download-option">
            <div class="data-pool-download-option-head">
              <div class="group-title">下载命令</div>
              <div class="muted">在 FeatureFactory 根目录执行，支持 <span class="mono">--out</span> 参数。</div>
            </div>
            <textarea id="data-pool-download-command" class="data-pool-download-command" readonly spellcheck="false"></textarea>
            <div class="action-row batch-data-pool-modal-actions">
              <button id="data-pool-download-copy-btn" type="button" class="batch-modal-action secondary">复制命令</button>
              <button id="data-pool-download-close-btn" type="button" class="batch-modal-action secondary">关闭</button>
              <span class="muted" id="data-pool-download-message"></span>
            </div>
          </section>
        </div>
      </div>
      <div class="batch-data-pool-popover" id="data-pool-preview-popover" hidden>
        <div class="batch-data-pool-modal data-pool-preview-modal" role="dialog" aria-modal="true" aria-labelledby="data-pool-preview-title">
          <div class="data-pool-preview-head">
            <div>
              <div class="repo-filter-title" id="data-pool-preview-title">数据池 Preview</div>
              <div class="muted" id="data-pool-preview-subtitle"></div>
            </div>
            <button id="data-pool-preview-close-btn" type="button" class="batch-modal-action secondary">关闭</button>
          </div>
          <div class="muted" id="data-pool-preview-message"></div>
          <div class="data-pool-preview-body" id="data-pool-preview-body"></div>
        </div>
      </div>
    </section>

    <section class="pane" id="pane-stage1">
      <div class="stage-layout">
        <div class="left-stack">
          <section class="panel">
            <div class="section-title">
              <h3>创建抓取任务</h3>
            </div>
            <form id="crawl-form">
              <label class="full">
                任务名
                <input name="name" required>
              </label>

              <div class="field-grid quad" data-crawl-target-disabled>
                <div class="field-group">
                  <div class="group-title">语言</div>
                  <div class="selector-anchor" id="crawl-language-anchor">
                    <button class="selector-toggle" id="crawl-language-toggle" type="button" aria-expanded="false">
                      <span class="selector-summary" id="crawl-language-summary">Python</span>
                      <span class="selector-caret">▾</span>
                    </button>
                    <div class="selector-popover" id="crawl-language-popover" hidden>
                      <div class="selector-list">
                        <label class="selector-option"><input type="checkbox" data-crawl-language value="Python" checked><span>Python</span></label>
                        <label class="selector-option"><input type="checkbox" data-crawl-language value="TypeScript"><span>TypeScript</span></label>
                        <label class="selector-option"><input type="checkbox" data-crawl-language value="JavaScript"><span>JavaScript</span></label>
                        <label class="selector-option"><input type="checkbox" data-crawl-language value="Go"><span>Go</span></label>
                        <label class="selector-option"><input type="checkbox" data-crawl-language value="Java"><span>Java</span></label>
                        <label class="selector-option"><input type="checkbox" data-crawl-language value="Rust"><span>Rust</span></label>
                        <label class="selector-option"><input type="checkbox" data-crawl-language value="C++"><span>C++</span></label>
                        <label class="selector-option"><input type="checkbox" data-crawl-language value="C"><span>C</span></label>
                      </div>
                    </div>
                  </div>
                </div>
                <label>
                  最低 stars
                  <input name="stars_min" type="number" min="0" value="100" placeholder="可选">
                </label>
                <label>
                  最高 stars
                  <input name="stars_max" type="number" min="0" placeholder="可选">
                </label>
                <label>
                  仓库个数上限
                  <input name="repository_limit" type="number" min="1" placeholder="可选">
                </label>
              </div>

              <div class="checkrow" data-crawl-target-disabled>
                <div class="check-item"><input name="include_forks" type="checkbox"><span>包含 fork</span></div>
                <div class="check-item"><input name="include_archived" type="checkbox"><span>包含 archived</span></div>
              </div>

              <div class="field-grid" data-crawl-target-disabled>
                <label>
                  创建时间下界
                  <input name="created_after" type="datetime-local" required>
                </label>
                <label>
                  创建时间上界
                  <input name="created_before" type="datetime-local">
                </label>
              </div>

              <div class="field-grid" data-crawl-target-disabled>
                <label>
                  最近 push 下界
                  <input name="pushed_after" type="datetime-local">
                </label>
                <label>
                  最近 push 上界
                  <input name="pushed_before" type="datetime-local">
                </label>
              </div>

              <div class="full field-group" data-crawl-target-disabled>
                <div class="group-title">许可证白名单</div>
                <div class="selector-anchor" id="crawl-license-anchor">
                  <button class="selector-toggle" id="crawl-license-toggle" type="button" aria-expanded="false">
                    <span class="selector-summary" id="crawl-license-summary">不限</span>
                    <span class="selector-caret">▾</span>
                  </button>
                  <div class="selector-popover" id="crawl-license-popover" hidden>
                    <div class="selector-list compact-grid">
                      <label class="selector-option"><input type="checkbox" name="licenses" data-crawl-license value="mit"><span>MIT</span></label>
                      <label class="selector-option"><input type="checkbox" name="licenses" data-crawl-license value="apache-2.0"><span>Apache-2.0</span></label>
                      <label class="selector-option"><input type="checkbox" name="licenses" data-crawl-license value="bsd-2-clause"><span>BSD-2-Clause</span></label>
                      <label class="selector-option"><input type="checkbox" name="licenses" data-crawl-license value="bsd-3-clause"><span>BSD-3-Clause</span></label>
                      <label class="selector-option"><input type="checkbox" name="licenses" data-crawl-license value="isc"><span>ISC</span></label>
                      <label class="selector-option"><input type="checkbox" name="licenses" data-crawl-license value="mpl-2.0"><span>MPL-2.0</span></label>
                      <label class="selector-option"><input type="checkbox" name="licenses" data-crawl-license value="lgpl-3.0"><span>LGPL-3.0</span></label>
                    </div>
                  </div>
                </div>
              </div>

              <label class="full" data-crawl-target-disabled>
                附加关键词
                <input name="keywords" placeholder="pytest, tests, unit test">
              </label>

              <label class="full">
                抓取指定仓库
                <textarea name="target_repositories" rows="4" placeholder="每行一个 owner/repo"></textarea>
              </label>

              <details class="advanced-box">
                <summary>高级参数</summary>
                <div class="advanced-content">
                  <div class="field-grid">
                    <label>
                      <span class="stage2-field-head">
                        <span>并行抓取任务数</span>
                        <span class="stage2-field-note-inline" id="max-concurrent-jobs-note">（当前上限 24）</span>
                      </span>
                      <input id="max-concurrent-jobs" name="max_concurrent_jobs" type="number" min="1" max="24">
                    </label>
                    <label>
                      <span class="stage2-field-head">
                        <span>单任务分片并发数</span>
                        <span class="stage2-field-note-inline" id="max-concurrent-partitions-note">（当前上限 16）</span>
                      </span>
                      <input id="max-concurrent-partitions" name="max_concurrent_partitions" type="number" min="1">
                    </label>
                    <div class="full field-group">
                      <div class="stage1-template-head">
                        <div class="group-title">GitHub Tokens</div>
                        <div class="stage2-template-strip" id="stage1-template-strip"></div>
                        <button class="secondary tiny" id="save-runtime-btn" type="button">应用运行设置</button>
                        <button class="secondary tiny" id="stage1-template-save" type="button">保存为模板</button>
                      </div>
                      <textarea id="github-tokens" name="github_tokens" placeholder="每行一个 token"></textarea>
                    </div>
                  </div>
                  <div class="muted" id="runtime-message"></div>
                </div>
              </details>

              <button class="primary" id="submit-btn" type="submit">创建抓取任务</button>
              <div class="muted" id="form-message"></div>
            </form>
          </section>
        </div>

        <div class="right-stack">
          <section class="panel">
            <div class="section-title">
              <h3>抓取任务列表</h3>
            </div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>任务</th>
                    <th>状态</th>
                    <th>唯一 Repo</th>
                    <th>分片数</th>
                    <th>创建时间</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody id="jobs-body"></tbody>
              </table>
            </div>
          </section>

          <section class="panel" id="detail-panel" hidden>
            <div class="section-title">
              <h3>抓取任务详情</h3>
              <div class="hint" id="detail-subtitle"></div>
            </div>
            <div class="detail-body" id="detail-body"></div>
          </section>

          <section class="panel">
            <div class="section-title">
              <h3>已抓取仓库</h3>
              <div class="section-actions">
                <div class="popover-anchor" id="repo-filter-anchor">
                  <button class="secondary tiny filter-toggle" id="repo-filter-toggle" type="button">筛选</button>
                  <div class="repo-filter-popover" id="repo-filter-popover" hidden>
                    <div class="repo-filter-title">筛选已抓取仓库</div>
                    <div class="field-grid">
                      <label class="full">
                        仓库关键词
                        <input id="repo-filter-name-query" type="text" placeholder="仓库名、owner、描述">
                      </label>
                      <div class="field-group">
                        <div class="group-title">语言</div>
                        <div class="selector-anchor" id="repo-filter-language-anchor">
                          <button class="selector-toggle" id="repo-filter-language-toggle" type="button" aria-expanded="false">
                            <span class="selector-summary" id="repo-filter-language-summary">不限</span>
                            <span class="selector-caret">▾</span>
                          </button>
                          <div class="selector-popover" id="repo-filter-language-popover" hidden>
                            <div class="selector-list compact-grid">
                              <label class="selector-option"><input type="checkbox" data-repo-filter-language value="Python"><span>Python</span></label>
                              <label class="selector-option"><input type="checkbox" data-repo-filter-language value="TypeScript"><span>TypeScript</span></label>
                              <label class="selector-option"><input type="checkbox" data-repo-filter-language value="JavaScript"><span>JavaScript</span></label>
                              <label class="selector-option"><input type="checkbox" data-repo-filter-language value="Go"><span>Go</span></label>
                              <label class="selector-option"><input type="checkbox" data-repo-filter-language value="Java"><span>Java</span></label>
                              <label class="selector-option"><input type="checkbox" data-repo-filter-language value="Rust"><span>Rust</span></label>
                              <label class="selector-option"><input type="checkbox" data-repo-filter-language value="C++"><span>C++</span></label>
                              <label class="selector-option"><input type="checkbox" data-repo-filter-language value="C"><span>C</span></label>
                            </div>
                          </div>
                        </div>
                      </div>
                      <label>
                        最低 Star
                        <input id="repo-filter-stars-min" type="number" min="0" placeholder="可选">
                      </label>
                      <label>
                        最高 Star
                        <input id="repo-filter-stars-max" type="number" min="0" placeholder="可选">
                      </label>
                      <label>
                        创建时间下界
                        <input id="repo-filter-created-after" type="datetime-local">
                      </label>
                      <label>
                        创建时间上界
                        <input id="repo-filter-created-before" type="datetime-local">
                      </label>
                      <label>
                        最近更新下界
                        <input id="repo-filter-pushed-after" type="datetime-local">
                      </label>
                      <label>
                        最近更新上界
                        <input id="repo-filter-pushed-before" type="datetime-local">
                      </label>
                      <label>
                        入库时间下界
                        <input id="repo-filter-discovered-after" type="datetime-local">
                      </label>
                      <label>
                        入库时间上界
                        <input id="repo-filter-discovered-before" type="datetime-local">
                      </label>
                      <div class="field-group full">
                        <div class="group-title">许可证白名单</div>
                        <div class="selector-anchor" id="repo-filter-license-anchor">
                          <button class="selector-toggle" id="repo-filter-license-toggle" type="button" aria-expanded="false">
                            <span class="selector-summary" id="repo-filter-license-summary">不限</span>
                            <span class="selector-caret">▾</span>
                          </button>
                          <div class="selector-popover" id="repo-filter-license-popover" hidden>
                            <div class="selector-list compact-grid">
                              <label class="selector-option"><input type="checkbox" data-repo-filter-license value="mit"><span>MIT</span></label>
                              <label class="selector-option"><input type="checkbox" data-repo-filter-license value="apache-2.0"><span>Apache-2.0</span></label>
                              <label class="selector-option"><input type="checkbox" data-repo-filter-license value="bsd-2-clause"><span>BSD-2-Clause</span></label>
                              <label class="selector-option"><input type="checkbox" data-repo-filter-license value="bsd-3-clause"><span>BSD-3-Clause</span></label>
                              <label class="selector-option"><input type="checkbox" data-repo-filter-license value="isc"><span>ISC</span></label>
                              <label class="selector-option"><input type="checkbox" data-repo-filter-license value="mpl-2.0"><span>MPL-2.0</span></label>
                              <label class="selector-option"><input type="checkbox" data-repo-filter-license value="lgpl-3.0"><span>LGPL-3.0</span></label>
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                    <div class="repo-filter-actions">
                      <button class="secondary tiny" id="repo-filter-reset" type="button">清空</button>
                      <button class="primary tiny" id="repo-filter-apply" type="button">应用筛选</button>
                    </div>
                  </div>
                </div>
                <button class="secondary tiny" id="repos-prev-btn" type="button">上一页</button>
                <button class="secondary tiny" id="repos-next-btn" type="button">下一页</button>
                <div class="pagination-meta" id="repos-pagination-meta"></div>
              </div>
            </div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th><button class="sort-button" type="button" data-repo-sort="full_name">仓库</button></th>
                    <th><button class="sort-button" type="button" data-repo-sort="primary_language">语言</button></th>
                    <th><button class="sort-button" type="button" data-repo-sort="stargazers_count">Star 数</button></th>
                    <th><button class="sort-button" type="button" data-repo-sort="created_at_github">创建时间</button></th>
                    <th><button class="sort-button" type="button" data-repo-sort="pushed_at_github">最近更新</button></th>
                    <th><button class="sort-button" type="button" data-repo-sort="discovered_at">入库时间</button></th>
                  </tr>
                </thead>
                <tbody id="repos-body"></tbody>
              </table>
            </div>
          </section>
        </div>
      </div>
    </section>

    <section class="pane" id="pane-stage2">
      <div class="stage-layout stage2-layout" id="stage2-layout">
        <div class="left-stack stage2-list-stack" id="stage2-list-stack">
          <section class="panel stage2-config-strip" id="stage2-runtime-config">
            <div class="stage2-config-header">
              <div class="stage2-config-head-main">
                <button class="stage2-config-toggle" id="stage2-config-toggle" type="button" aria-expanded="true">
                  <span class="stage2-config-caret" id="stage2-config-caret">▾</span>
                  <span class="stage2-config-title">运行配置</span>
                </button>
                <div class="stage2-template-strip" id="stage2-template-strip"></div>
              </div>
              <div class="stage2-config-actions">
                <button class="secondary tiny" id="stage2-template-save" type="button">保存为模板</button>
                <button class="primary tiny" id="stage2-config-apply" type="button">应用</button>
                <span id="stage2-config-message"></span>
              </div>
            </div>
            <div class="stage2-config-grid" id="stage2-config-body">
              <div class="stage2-config-section">Planner agent 配置</div>
              <div class="stage2-agent-config-row">
                <label>
                  模型
                  <input id="stage2-planner-model" type="text">
                </label>
                <label>
                  BaseURL
                  <input id="stage2-planner-base-url" type="url">
                </label>
                <label>
                  APIKey
                  <input id="stage2-planner-api-key" type="password" autocomplete="off">
                </label>
                <label>
                  OpenHands 预设
                  <select id="stage2-planner-preset">
                    <option value="gpt5">gpt5：patch 写文件</option>
                    <option value="default">default：默认文件编辑器</option>
                  </select>
                </label>
                <label>
                  最大迭代步数
                  <input id="stage2-planner-max-iterations" type="number" min="10" max="1000" step="1">
                </label>
                <label>
                  超时时限（秒）
                  <input id="stage2-planner-timeout" type="number" min="30" max="14400" step="1">
                </label>
              </div>

              <div class="stage2-config-section">Worker agent 配置</div>
              <div class="stage2-agent-config-row">
                <label>
                  模型
                  <input id="stage2-worker-model" type="text">
                </label>
                <label>
                  BaseURL
                  <input id="stage2-worker-base-url" type="url">
                </label>
                <label>
                  APIKey
                  <input id="stage2-worker-api-key" type="password" autocomplete="off">
                </label>
                <label>
                  OpenHands 预设
                  <select id="stage2-worker-preset">
                    <option value="gpt5">gpt5：patch 写文件</option>
                    <option value="default">default：默认文件编辑器</option>
                  </select>
                </label>
                <label>
                  最大迭代步数
                  <input id="stage2-worker-max-iterations" type="number" min="10" max="1000" step="1">
                </label>
                <label>
                  超时时限（秒）
                  <input id="stage2-worker-timeout" type="number" min="30" max="14400" step="1">
                </label>
              </div>

              <div class="stage2-config-section">超参数</div>
              <div class="stage2-hyper-config-row">
                <label>
                  <span class="stage2-field-head">
                    <span>任务并发额度</span>
                    <span class="stage2-field-note-inline" id="stage2-max-concurrent-runs-note">（系统容量 24）</span>
                  </span>
                  <input id="stage2-max-concurrent-runs" type="number" min="1" max="24" step="1">
                </label>
                <label>
                  validate tool 调用次数上限
                  <input id="stage2-max-worker-attempts" type="number" min="1" max="10" step="1">
                </label>
                <label>
                  快检抽样数量
                  <input id="stage2-quickcheck-sample-size" type="number" min="1" max="100" step="1">
                </label>
                <label>
                  collect 超时上限（秒）
                  <input id="stage2-collect-timeout" type="number" min="10" max="7200" step="1">
                </label>
                <label>
                  run 单个测试文件超时上限（秒）
                  <input id="stage2-run-test-timeout" type="number" min="10" max="7200" step="1">
                </label>
                <label>
                  build 超时上限（秒）
                  <input id="stage2-build-timeout" type="number" min="30" max="7200" step="1">
                </label>
                <label>
                  full validation 超时上限（秒）
                  <input id="stage2-full-validation-timeout" type="number" min="30" max="28800" step="1">
                </label>
                <label>
                  入口文件测试点个数下限
                  <input id="stage2-entry-file-test-count-min" type="number" min="-1" step="1">
                </label>
                <label>
                  P2P 集合文件数量上限
                  <input id="stage2-p2p-file-count-limit" type="number" min="1" step="1" placeholder="不截断">
                </label>
              </div>
            </div>
          </section>

          <section class="panel">
            <div class="section-title">
              <h3>Repo 列表</h3>
              <div class="section-actions">
                <button class="secondary tiny filter-toggle" id="stage2-filter-non-pending" type="button">只看非待运行</button>
                <div class="popover-anchor" id="stage2-filter-anchor">
                  <button class="secondary tiny filter-toggle" id="stage2-filter-toggle" type="button">筛选</button>
                  <div class="repo-filter-popover" id="stage2-filter-popover" hidden>
                    <div class="repo-filter-title">筛选 Repo 列表</div>
                    <div class="field-grid">
                      <label class="full">
                        仓库关键词
                        <input id="stage2-filter-name-query" type="text" placeholder="仓库名、owner、描述">
                      </label>
                      <div class="field-group">
                        <div class="group-title">状态</div>
                        <div class="selector-anchor" id="stage2-filter-status-anchor">
                          <button class="selector-toggle" id="stage2-filter-status-toggle" type="button" aria-expanded="false">
                            <span class="selector-summary" id="stage2-filter-status-summary">不限</span>
                            <span class="selector-caret">▾</span>
                          </button>
                          <div class="selector-popover" id="stage2-filter-status-popover" hidden>
                            <div class="selector-list compact-grid">
                              <label class="selector-option"><input type="checkbox" data-stage2-filter-status value="pending"><span>未运行</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage2-filter-status value="queued"><span>排队中</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage2-filter-status value="running"><span>运行中</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage2-filter-status value="succeeded"><span>成功</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage2-filter-status value="abandoned"><span>废弃</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage2-filter-status value="defect"><span>有缺陷</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage2-filter-status value="failed"><span>失败</span></label>
                            </div>
                          </div>
                        </div>
                      </div>
                      <div class="field-group">
                        <div class="group-title">语言</div>
                        <div class="selector-anchor" id="stage2-filter-language-anchor">
                          <button class="selector-toggle" id="stage2-filter-language-toggle" type="button" aria-expanded="false">
                            <span class="selector-summary" id="stage2-filter-language-summary">不限</span>
                            <span class="selector-caret">▾</span>
                          </button>
                          <div class="selector-popover" id="stage2-filter-language-popover" hidden>
                            <div class="selector-list compact-grid">
                              <label class="selector-option"><input type="checkbox" data-stage2-filter-language value="Python"><span>Python</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage2-filter-language value="TypeScript"><span>TypeScript</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage2-filter-language value="JavaScript"><span>JavaScript</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage2-filter-language value="Go"><span>Go</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage2-filter-language value="Java"><span>Java</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage2-filter-language value="Rust"><span>Rust</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage2-filter-language value="C++"><span>C++</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage2-filter-language value="C"><span>C</span></label>
                            </div>
                          </div>
                        </div>
                      </div>
                      <label>
                        最低 Star
                        <input id="stage2-filter-stars-min" type="number" min="0" placeholder="可选">
                      </label>
                      <label>
                        最高 Star
                        <input id="stage2-filter-stars-max" type="number" min="0" placeholder="可选">
                      </label>
                      <label>
                        创建时间下界
                        <input id="stage2-filter-created-after" type="datetime-local">
                      </label>
                      <label>
                        创建时间上界
                        <input id="stage2-filter-created-before" type="datetime-local">
                      </label>
                      <label>
                        最近更新下界
                        <input id="stage2-filter-pushed-after" type="datetime-local">
                      </label>
                      <label>
                        最近更新上界
                        <input id="stage2-filter-pushed-before" type="datetime-local">
                      </label>
                      <label>
                        入库时间下界
                        <input id="stage2-filter-discovered-after" type="datetime-local">
                      </label>
                      <label>
                        入库时间上界
                        <input id="stage2-filter-discovered-before" type="datetime-local">
                      </label>
                      <div class="field-group full">
                        <div class="group-title">许可证白名单</div>
                        <div class="selector-anchor" id="stage2-filter-license-anchor">
                          <button class="selector-toggle" id="stage2-filter-license-toggle" type="button" aria-expanded="false">
                            <span class="selector-summary" id="stage2-filter-license-summary">不限</span>
                            <span class="selector-caret">▾</span>
                          </button>
                          <div class="selector-popover" id="stage2-filter-license-popover" hidden>
                            <div class="selector-list compact-grid">
                              <label class="selector-option"><input type="checkbox" data-stage2-filter-license value="mit"><span>MIT</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage2-filter-license value="apache-2.0"><span>Apache-2.0</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage2-filter-license value="bsd-2-clause"><span>BSD-2-Clause</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage2-filter-license value="bsd-3-clause"><span>BSD-3-Clause</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage2-filter-license value="isc"><span>ISC</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage2-filter-license value="mpl-2.0"><span>MPL-2.0</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage2-filter-license value="lgpl-3.0"><span>LGPL-3.0</span></label>
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                    <div class="repo-filter-actions">
                      <button class="secondary tiny" id="stage2-filter-reset" type="button">清空</button>
                      <button class="primary tiny" id="stage2-filter-apply" type="button">应用筛选</button>
                    </div>
                  </div>
                </div>
                <button class="secondary tiny" id="stage2-prev-btn" type="button">上一页</button>
                <button class="secondary tiny" id="stage2-next-btn" type="button">下一页</button>
                <div class="pagination-meta" id="stage2-pagination-meta"></div>
              </div>
            </div>
            <div class="table-wrap">
              <table class="stage2-repo-table">
                <colgroup>
                  <col class="stage2-repo-col-name">
                  <col class="stage2-repo-col-language">
                  <col class="stage2-repo-col-stars">
                  <col class="stage2-repo-col-created">
                  <col class="stage2-repo-col-updated">
                  <col class="stage2-repo-col-discovered">
                  <col class="stage2-repo-col-latest-action">
                  <col class="stage2-repo-col-status">
                  <col class="stage2-repo-col-action">
                </colgroup>
                <thead>
                  <tr>
                    <th><button class="sort-button" type="button" data-stage2-sort="full_name">仓库</button></th>
                    <th><button class="sort-button" type="button" data-stage2-sort="primary_language">语言</button></th>
                    <th><button class="sort-button" type="button" data-stage2-sort="stargazers_count">Star 数</button></th>
                    <th><button class="sort-button" type="button" data-stage2-sort="created_at_github">创建时间</button></th>
                    <th><button class="sort-button" type="button" data-stage2-sort="pushed_at_github">最近更新</button></th>
                    <th><button class="sort-button" type="button" data-stage2-sort="discovered_at">入库时间</button></th>
                    <th><button class="sort-button" type="button" data-stage2-sort="latest_operation_at">最近操作时间</button></th>
                    <th>状态</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody id="stage2-repos-body"></tbody>
              </table>
            </div>
          </section>
        </div>

        <div class="right-stack stage2-detail-stack" id="stage2-detail-stack">
          <section class="panel" id="stage2-detail-panel">
            <div class="section-title">
              <h3>Repo 详细</h3>
              <div class="section-actions">
                <div id="stage2-detail-action"></div>
                <button class="secondary tiny" id="stage2-detail-back-btn" type="button">返回列表</button>
                <div class="hint" id="stage2-detail-subtitle"></div>
              </div>
            </div>
            <div class="detail-body" id="stage2-detail-body"></div>
          </section>
        </div>
      </div>
    </section>

    <section class="pane" id="pane-stage3">
      <div class="stage-layout stage3-layout detail-hidden" id="stage3-layout">
        <div class="left-stack stage3-list-stack" id="stage3-list-stack">
          <section class="panel stage2-config-strip" id="stage3-runtime-config">
            <div class="stage2-config-header">
              <div class="stage2-config-head-main">
                <button class="stage2-config-toggle" id="stage3-config-toggle" type="button" aria-expanded="true">
                  <span class="stage2-config-caret" id="stage3-config-caret">▾</span>
                  <span class="stage2-config-title">运行配置</span>
                </button>
                <div class="stage2-template-strip" id="stage3-template-strip"></div>
              </div>
              <div class="stage2-config-actions">
                <button class="secondary tiny" id="stage3-template-save" type="button">保存为模板</button>
                <button class="primary tiny" id="stage3-config-apply" type="button">应用</button>
                <span id="stage3-config-message"></span>
              </div>
            </div>
            <div class="stage2-config-grid" id="stage3-config-body">
              <div class="stage2-config-section">Breaker agent 配置</div>
              <div class="stage2-agent-config-row">
                <label>
                  模型
                  <input id="stage3-breaker-model" type="text">
                </label>
                <label>
                  BaseURL
                  <input id="stage3-breaker-base-url" type="url">
                </label>
                <label>
                  APIKey
                  <input id="stage3-breaker-api-key" type="password" autocomplete="off">
                </label>
                <label>
                  OpenHands 预设
                  <select id="stage3-breaker-preset">
                    <option value="default">default：默认文件编辑器</option>
                    <option value="gpt5">gpt5：patch 写文件</option>
                  </select>
                </label>
                <label>
                  最大迭代步数
                  <input id="stage3-breaker-max-iterations" type="number" min="10" max="1000" step="1">
                </label>
                <label>
                  超时时限（秒）
                  <input id="stage3-breaker-timeout" type="number" min="30" max="14400" step="1">
                </label>
              </div>

              <div class="stage2-config-section">超参数</div>
              <div class="stage2-hyper-config-row stage3-hyper-config-row">
                <label>
                  <span class="stage2-field-head">
                    <span>任务并发额度</span>
                    <span class="stage2-field-note-inline" id="stage3-max-concurrent-runs-note">（系统容量 24）</span>
                  </span>
                  <input id="stage3-max-concurrent-runs" type="number" min="1" max="24" step="1">
                </label>
                <label>
                  build 超时上限（秒）
                  <input id="stage3-build-timeout" type="number" min="30" max="7200" step="1">
                </label>
                <label>
                  run 单个测试文件超时上限（秒）
                  <input id="stage3-run-test-timeout" type="number" min="10" max="7200" step="1">
                </label>
                <label>
                  full validation 超时上限（秒）
                  <input id="stage3-full-validation-timeout" type="number" min="30" max="28800" step="1">
                </label>
                <label>
                  入口文件最高允许通过率（0-1）
                  <input id="stage3-entry-pass-rate-ceiling" type="number" min="0" max="1" step="0.01">
                </label>
                <label>
                  最少删除实现代码行数
                  <input id="stage3-min-removed-code-lines" type="number" min="0" max="10000" step="1" value="10">
                </label>
              </div>
            </div>
          </section>

          <section class="panel">
            <div class="section-title">
              <h3>Repo 列表</h3>
              <div class="section-actions">
                <button class="secondary tiny filter-toggle" id="stage3-filter-non-pending" type="button" aria-pressed="false">只看非待运行</button>
                <div class="popover-anchor" id="stage3-filter-anchor">
                  <button class="secondary tiny filter-toggle" id="stage3-filter-toggle" type="button" aria-expanded="false">筛选</button>
                  <div class="repo-filter-popover" id="stage3-filter-popover" hidden>
                    <div class="repo-filter-title">Stage3 筛选条件</div>
                    <div class="field-grid">
                      <label class="full">
                        仓库名
                        <input id="stage3-filter-name-query" type="text" placeholder="owner/name 关键词">
                      </label>
                      <div class="field-group full">
                        <div class="group-title">状态</div>
                        <div class="selector-anchor" id="stage3-filter-status-anchor">
                          <button class="selector-toggle" id="stage3-filter-status-toggle" type="button" aria-expanded="false">
                            <span class="selector-summary" id="stage3-filter-status-summary">不限</span>
                            <span class="selector-caret">▾</span>
                          </button>
                          <div class="selector-popover" id="stage3-filter-status-popover" hidden>
                            <div class="selector-list compact-grid">
                              <label class="selector-option"><input type="checkbox" data-stage3-filter-status value="pending"><span>未运行</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage3-filter-status value="queued"><span>排队中</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage3-filter-status value="running"><span>运行中</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage3-filter-status value="succeeded"><span>已生成</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage3-filter-status value="failed"><span>失败</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage3-filter-status value="interrupted"><span>已中断</span></label>
                            </div>
                          </div>
                        </div>
                      </div>
                      <div class="field-group">
                        <div class="group-title">语言</div>
                        <div class="selector-anchor" id="stage3-filter-language-anchor">
                          <button class="selector-toggle" id="stage3-filter-language-toggle" type="button" aria-expanded="false">
                            <span class="selector-summary" id="stage3-filter-language-summary">不限</span>
                            <span class="selector-caret">▾</span>
                          </button>
                          <div class="selector-popover" id="stage3-filter-language-popover" hidden>
                            <div class="selector-list compact-grid">
                              <label class="selector-option"><input type="checkbox" data-stage3-filter-language value="Python"><span>Python</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage3-filter-language value="TypeScript"><span>TypeScript</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage3-filter-language value="JavaScript"><span>JavaScript</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage3-filter-language value="Go"><span>Go</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage3-filter-language value="Java"><span>Java</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage3-filter-language value="Rust"><span>Rust</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage3-filter-language value="C++"><span>C++</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage3-filter-language value="C"><span>C</span></label>
                            </div>
                          </div>
                        </div>
                      </div>
                      <label>
                        最低 Star
                        <input id="stage3-filter-stars-min" type="number" min="0" placeholder="可选">
                      </label>
                      <label>
                        最高 Star
                        <input id="stage3-filter-stars-max" type="number" min="0" placeholder="可选">
                      </label>
                      <label>
                        创建时间下界
                        <input id="stage3-filter-created-after" type="datetime-local">
                      </label>
                      <label>
                        创建时间上界
                        <input id="stage3-filter-created-before" type="datetime-local">
                      </label>
                      <label>
                        最近更新下界
                        <input id="stage3-filter-pushed-after" type="datetime-local">
                      </label>
                      <label>
                        最近更新上界
                        <input id="stage3-filter-pushed-before" type="datetime-local">
                      </label>
                      <label>
                        入库时间下界
                        <input id="stage3-filter-discovered-after" type="datetime-local">
                      </label>
                      <label>
                        入库时间上界
                        <input id="stage3-filter-discovered-before" type="datetime-local">
                      </label>
                    </div>
                    <div class="repo-filter-actions">
                      <button class="secondary tiny" id="stage3-filter-reset" type="button">清空</button>
                      <button class="primary tiny" id="stage3-filter-apply" type="button">应用筛选</button>
                    </div>
                  </div>
                </div>
                <button class="secondary tiny" id="stage3-prev-btn" type="button">上一页</button>
                <button class="secondary tiny" id="stage3-next-btn" type="button">下一页</button>
                <div class="pagination-meta" id="stage3-pagination-meta">-</div>
              </div>
            </div>
            <div class="table-wrap">
              <table class="stage3-repo-table">
                <colgroup>
                  <col class="stage3-repo-col-name">
                  <col class="stage3-repo-col-language">
                  <col class="stage3-repo-col-stars">
                  <col class="stage3-repo-col-commits">
                  <col class="stage3-repo-col-latest-action">
                  <col class="stage3-repo-col-produced-entries">
                  <col class="stage3-repo-col-produced-data">
                  <col class="stage3-repo-col-status">
                  <col class="stage3-repo-col-action">
                </colgroup>
                <thead>
                  <tr>
                    <th><button class="sort-button" type="button" data-stage3-sort="full_name">仓库</button></th>
                    <th><button class="sort-button" type="button" data-stage3-sort="primary_language">语言</button></th>
                    <th><button class="sort-button" type="button" data-stage3-sort="stargazers_count">Star 数</button></th>
                    <th><button class="sort-button" type="button" data-stage3-sort="eligible_commit_count">通过 commit 数量</button></th>
                    <th><button class="sort-button" type="button" data-stage3-sort="latest_operation_at">最近操作时间</button></th>
                    <th><button class="sort-button" type="button" data-stage3-sort="produced_entry_file_count">产出数据的入口文件数</button></th>
                    <th><button class="sort-button" type="button" data-stage3-sort="produced_data_count">产出数据条数</button></th>
                    <th><button class="sort-button" type="button" data-stage3-sort="status">状态</button></th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody id="stage3-repos-body"></tbody>
              </table>
            </div>
          </section>
        </div>

        <div class="right-stack stage3-detail-stack" id="stage3-detail-stack">
          <section class="panel" id="stage3-detail-panel">
            <div class="section-title">
              <h3 id="stage3-detail-title">Repo 详细</h3>
              <div class="section-actions">
                <button class="primary tiny" id="stage3-detail-primary-action-btn" type="button" hidden></button>
                <button class="secondary tiny" id="stage3-detail-back-btn" type="button">返回列表</button>
                <div class="hint" id="stage3-detail-subtitle">选择一个 repo，查看 commit 基线与入口文件</div>
              </div>
            </div>
            <div class="detail-body" id="stage3-detail-body"></div>
          </section>
        </div>
      </div>
    </section>

    <section class="pane" id="pane-stage4">
      <div class="stage-layout stage4-layout detail-hidden" id="stage4-layout">
        <div class="left-stack stage4-list-stack" id="stage4-list-stack">
          <section class="panel stage2-config-strip" id="stage4-runtime-config">
            <div class="stage2-config-header">
              <div class="stage2-config-head-main">
                <button class="stage2-config-toggle" id="stage4-config-toggle" type="button" aria-expanded="true">
                  <span class="stage2-config-caret" id="stage4-config-caret">▾</span>
                  <span class="stage2-config-title">运行配置</span>
                </button>
                <div class="stage2-template-strip" id="stage4-template-strip"></div>
              </div>
              <div class="stage2-config-actions">
                <button class="secondary tiny" id="stage4-template-save" type="button">保存为模板</button>
                <button class="primary tiny" id="stage4-config-apply" type="button">应用</button>
                <span id="stage4-config-message"></span>
              </div>
            </div>
            <div class="stage2-config-grid" id="stage4-config-body">
              <div class="stage2-config-section">Issuer agent 配置</div>
              <div class="stage2-agent-config-row">
                <label>
                  模型
                  <input id="stage4-issuer-model" type="text">
                </label>
                <label>
                  BaseURL
                  <input id="stage4-issuer-base-url" type="url">
                </label>
                <label>
                  APIKey
                  <input id="stage4-issuer-api-key" type="password" autocomplete="off">
                </label>
                <label>
                  OpenHands 预设
                  <select id="stage4-issuer-preset">
                    <option value="default">default：默认文件编辑器</option>
                    <option value="gpt5">gpt5：patch 写文件</option>
                  </select>
                </label>
                <label>
                  最大迭代步数
                  <input id="stage4-issuer-max-iterations" type="number" min="10" max="1000" step="1">
                </label>
                <label>
                  超时时限（秒）
                  <input id="stage4-issuer-timeout" type="number" min="30" max="14400" step="1">
                </label>
              </div>

              <div class="stage2-config-section">生成参数</div>
              <div class="stage2-hyper-config-row stage4-hyper-config-row">
                <label>
                  <span class="stage2-field-head">
                    <span>任务并发额度</span>
                    <span class="stage2-field-note-inline" id="stage4-max-concurrent-runs-note">（系统容量 24）</span>
                  </span>
                  <input id="stage4-max-concurrent-runs" type="number" min="1" max="24" step="1">
                </label>
                <label>
                  build 超时上限（秒）
                  <input id="stage4-build-timeout" type="number" min="30" max="7200" step="1">
                </label>
              </div>
            </div>
          </section>

          <section class="panel">
            <div class="section-title">
              <h3>Repo 列表</h3>
              <div class="section-actions">
                <button class="secondary tiny filter-toggle" id="stage4-filter-non-pending" type="button" aria-pressed="false">只看非待运行</button>
                <div class="popover-anchor" id="stage4-filter-anchor">
                  <button class="secondary tiny filter-toggle" id="stage4-filter-toggle" type="button" aria-expanded="false">筛选</button>
                  <div class="repo-filter-popover" id="stage4-filter-popover" hidden>
                    <div class="repo-filter-title">Stage4 筛选条件</div>
                    <div class="field-grid">
                      <label class="full">
                        仓库名
                        <input id="stage4-filter-name-query" type="text" placeholder="owner/name 关键词">
                      </label>
                      <div class="field-group">
                        <div class="group-title">语言</div>
                        <div class="selector-anchor" id="stage4-filter-language-anchor">
                          <button class="selector-toggle" id="stage4-filter-language-toggle" type="button" aria-expanded="false">
                            <span class="selector-summary" id="stage4-filter-language-summary">不限</span>
                            <span class="selector-caret">▾</span>
                          </button>
                          <div class="selector-popover" id="stage4-filter-language-popover" hidden>
                            <div class="selector-list compact-grid">
                              <label class="selector-option"><input type="checkbox" data-stage4-filter-language value="Python"><span>Python</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage4-filter-language value="TypeScript"><span>TypeScript</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage4-filter-language value="JavaScript"><span>JavaScript</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage4-filter-language value="Go"><span>Go</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage4-filter-language value="Java"><span>Java</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage4-filter-language value="Rust"><span>Rust</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage4-filter-language value="C++"><span>C++</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage4-filter-language value="C"><span>C</span></label>
                            </div>
                          </div>
                        </div>
                      </div>
                      <div class="field-group full">
                        <div class="group-title">状态</div>
                        <div class="selector-anchor" id="stage4-filter-status-anchor">
                          <button class="selector-toggle" id="stage4-filter-status-toggle" type="button" aria-expanded="false">
                            <span class="selector-summary" id="stage4-filter-status-summary">不限</span>
                            <span class="selector-caret">▾</span>
                          </button>
                          <div class="selector-popover" id="stage4-filter-status-popover" hidden>
                            <div class="selector-list compact-grid">
                              <label class="selector-option"><input type="checkbox" data-stage4-filter-status value="pending"><span>未运行</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage4-filter-status value="queued"><span>排队中</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage4-filter-status value="running"><span>运行中</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage4-filter-status value="generated"><span>已生成</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage4-filter-status value="failed"><span>失败</span></label>
                              <label class="selector-option"><input type="checkbox" data-stage4-filter-status value="interrupted"><span>已中断</span></label>
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                    <div class="repo-filter-actions">
                      <button class="secondary tiny" id="stage4-filter-reset" type="button">清空</button>
                      <button class="primary tiny" id="stage4-filter-apply" type="button">应用筛选</button>
                    </div>
                  </div>
                </div>
                <button class="secondary tiny" id="stage4-prev-btn" type="button">上一页</button>
                <button class="secondary tiny" id="stage4-next-btn" type="button">下一页</button>
                <div class="pagination-meta" id="stage4-pagination-meta">-</div>
              </div>
            </div>
            <div class="table-wrap">
              <table class="stage4-source-table">
                <colgroup>
                  <col class="stage4-source-col-repo">
                  <col class="stage4-source-col-commit">
                  <col class="stage4-source-col-language">
                  <col class="stage4-source-col-entry-count">
                  <col class="stage4-source-col-savepoint-count">
                  <col class="stage4-source-col-latest">
                  <col class="stage4-source-col-status">
                  <col class="stage4-source-col-action">
                </colgroup>
                <thead>
                  <tr>
                    <th><button class="sort-button" type="button" data-stage4-source-sort="repo" data-sort-label="仓库">仓库</button></th>
                    <th><button class="sort-button" type="button" data-stage4-source-sort="commit" data-sort-label="Commit">Commit</button></th>
                    <th><button class="sort-button" type="button" data-stage4-source-sort="language" data-sort-label="语言">语言</button></th>
                    <th><button class="sort-button" type="button" data-stage4-source-sort="entry_count" data-sort-label="入口文件数">入口文件数</button></th>
                    <th><button class="sort-button" type="button" data-stage4-source-sort="savepoint_count" data-sort-label="数据条数">数据条数</button></th>
                    <th><button class="sort-button" type="button" data-stage4-source-sort="latest_operation_at" data-sort-label="最近操作时间">最近操作时间</button></th>
                    <th><button class="sort-button" type="button" data-stage4-source-sort="status" data-sort-label="状态">状态</button></th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody id="stage4-sources-body"></tbody>
              </table>
            </div>
          </section>
        </div>

        <div class="right-stack stage4-detail-stack" id="stage4-detail-stack">
          <section class="panel" id="stage4-detail-panel">
            <div class="section-title">
              <h3 id="stage4-detail-title">Stage4 详情</h3>
              <div class="section-actions">
                <button class="primary tiny" id="stage4-detail-primary-action-btn" type="button" hidden></button>
                <button class="secondary tiny" id="stage4-detail-back-btn" type="button">返回列表</button>
                <span class="muted" id="stage4-detail-message">选择一个 repo/commit，查看 entry file 与 depth</span>
              </div>
            </div>
            <div class="detail-body" id="stage4-detail-body"></div>
          </section>
        </div>
      </div>
    </section>

    <section class="pane" id="pane-assets">
      <div id="asset-stage2-overview">
        <section class="panel">
          <div class="section-title">
            <div>
              <h3>software-agent-sdk 预热</h3>
            </div>
            <div class="section-actions">
              <span class="muted" id="asset-stage2-sdk-message"></span>
            </div>
          </div>
          <div class="asset-sdk-prewarm-grid" id="asset-stage2-sdk-body"></div>
        </section>
        <section class="panel">
          <div class="section-title">
            <div>
              <h3>base image 资产</h3>
              <div class="hint" id="asset-stage2-image-meta"></div>
            </div>
            <div class="section-actions">
              <span class="muted" id="asset-stage2-image-message"></span>
            </div>
          </div>
          <div class="table-wrap">
            <table class="asset-image-table">
              <colgroup>
                <col class="asset-image-col-base-ref">
                <col class="asset-image-col-base-status">
                <col class="asset-image-col-base-action">
                <col class="asset-image-col-agent-ref">
                <col class="asset-image-col-agent-status">
                <col class="asset-image-col-agent-action">
              </colgroup>
              <thead>
                <tr>
                  <th>Base Image</th>
                  <th>Base 状态</th>
                  <th>Base 操作</th>
                  <th>OpenHands 包装镜像</th>
                  <th>包装状态</th>
                  <th>包装操作</th>
                </tr>
              </thead>
              <tbody id="asset-stage2-image-body"></tbody>
            </table>
          </div>
        </section>
        <section class="panel">
          <div class="section-title">
            <div>
              <div class="asset-managed-image-title-row">
                <h3>镜像管理</h3>
                <span class="muted" id="asset-managed-image-total">共计 -</span>
              </div>
            </div>
            <div class="section-actions">
              <span class="muted" id="asset-managed-image-message"></span>
              <div class="popover-anchor" id="asset-managed-image-filter-anchor">
                <button class="secondary tiny filter-toggle" id="asset-managed-image-filter-toggle" type="button">筛选</button>
                <div class="repo-filter-popover managed-image-filter-popover" id="asset-managed-image-filter-popover" hidden>
                  <div class="field-grid">
                    <label>
                      镜像
                      <input id="asset-managed-image-filter-ref" placeholder="feature-factory/...">
                    </label>
                    <label>
                      大小下限（GB）
                      <input id="asset-managed-image-filter-size-min" type="number" min="0" step="0.01">
                    </label>
                    <label>
                      最后使用时间下界
                      <input id="asset-managed-image-filter-last-used-after" type="datetime-local">
                    </label>
                    <label>
                      最后使用时间上界
                      <input id="asset-managed-image-filter-last-used-before" type="datetime-local">
                    </label>
                    <label>
                      创建时间下界
                      <input id="asset-managed-image-filter-created-after" type="datetime-local">
                    </label>
                    <label>
                      创建时间上界
                      <input id="asset-managed-image-filter-created-before" type="datetime-local">
                    </label>
                    <label>
                      大小上限（GB）
                      <input id="asset-managed-image-filter-size-max" type="number" min="0" step="0.01">
                    </label>
                  </div>
                  <div class="action-row compact">
                    <button class="secondary tiny" id="asset-managed-image-filter-reset" type="button">清空</button>
                    <button class="primary tiny" id="asset-managed-image-filter-apply" type="button">应用筛选</button>
                  </div>
                </div>
              </div>
              <button class="secondary tiny danger" id="asset-managed-image-delete-selected" type="button" disabled>删除选中</button>
            </div>
          </div>
          <div class="table-wrap">
            <table class="managed-image-table">
              <colgroup>
                <col class="managed-image-col-select">
                <col class="managed-image-col-ref">
                <col class="managed-image-col-last-used">
                <col class="managed-image-col-created">
                <col class="managed-image-col-size">
                <col class="managed-image-col-job">
                <col class="managed-image-col-action">
              </colgroup>
              <thead>
                <tr>
                  <th><input id="asset-managed-image-select-all" type="checkbox" aria-label="选择全部镜像"></th>
                  <th><button class="sort-button" type="button" data-managed-image-sort="image_ref">镜像</button></th>
                  <th><button class="sort-button" type="button" data-managed-image-sort="last_used_at">最后使用时间</button></th>
                  <th><button class="sort-button" type="button" data-managed-image-sort="created_at">创建时间</button></th>
                  <th><button class="sort-button" type="button" data-managed-image-sort="size_bytes">大小</button></th>
                  <th><button class="sort-button" type="button" data-managed-image-sort="delete_job_status">任务状态</button></th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody id="asset-managed-image-body"></tbody>
            </table>
          </div>
        </section>
      </div>
      <section class="panel" id="asset-stage2-image-detail-panel" hidden>
        <div class="section-title">
          <div>
            <h3 id="asset-stage2-image-detail-title">base image 资产详细</h3>
            <div class="hint" id="asset-stage2-image-detail-subtitle">选择一个 base image 资产，查看后台下载进度与实时输出</div>
          </div>
          <div class="section-actions">
            <span class="muted" id="asset-stage2-image-detail-message"></span>
            <button class="secondary tiny" id="asset-stage2-image-detail-back" type="button">返回列表</button>
          </div>
        </div>
        <div class="detail-body" id="asset-stage2-image-detail-body"></div>
      </section>
    </section>
  </div>
  <script>
    window.__FEATURE_FACTORY_FRONTEND_SESSION_ID__ = "__FEATURE_FACTORY_FRONTEND_SESSION_ID__";
  </script>
  <script src="/static/admin.js?v=__FEATURE_FACTORY_ADMIN_JS_VERSION__"></script>
</body>
</html>
"""
