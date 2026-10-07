    const FRONTEND_SESSION_ID = window.__FEATURE_FACTORY_FRONTEND_SESSION_ID__ || "feature_factory";
    const THEME_STORAGE_KEY = "feature_factory:admin_theme";
    const STAGE3_DEFAULT_MIN_REMOVED_CODE_LINES = 10;
    const state = {
      selectedJobId: null,
      selectedStage2RepositoryId: null,
      selectedStage2RunId: null,
      selectedStage3RepositoryId: null,
      selectedStage3SnapshotId: null,
      selectedStage3EntryFileId: null,
      selectedStage3RunId: null,
      selectedStage2AssetKey: null,
      selectedStage3AssetKey: null,
      pendingStage2RunOpenDefaults: false,
      pendingStage3RunOpenDefaults: false,
      selectedStage2AssetVersionByKey: {},
      selectedStage3AssetVersionByKey: {},
      selectedStage2CompletionFileByAssetKey: {},
      selectedStage3CompletionFileByAssetKey: {},
      selectedStage4CompletionFileByAssetKey: {},
      stage2CompletionFileCache: {},
      stage3CompletionFileCache: {},
      stage4CompletionFileCache: {},
      stage2RepositoryDetailPayload: null,
      jobs: [],
      stage2Repos: [],
      stage3Repos: [],
      stage4Sources: [],
      stage4SourceGroups: [],
      stage4SourceDetailPayload: null,
      stage4RunDetailPayload: null,
      stage4Runs: [],
      batchTasks: [],
      selectedBatchTaskId: null,
      batchTaskDetailPayload: null,
      batchTaskDetailLoading: false,
      batchTaskDetailRequestSerial: 0,
      batchSelectedDataPoolId: null,
      batchDataPoolSelectPreviousId: null,
      batchForm: {
        languages: ["Python"],
        licenses: [],
      },
      batchFormCollapsed: false,
      batchRepositoryList: {
        page: 1,
        pageSize: 15,
        filters: {
          query: "",
          language: "",
          starsMin: "",
          starsMax: "",
          status: "",
          stage2Status: "",
          stage3Status: "",
          stage4Status: "",
          hasErrors: false,
          nonPending: false,
        },
      },
      dataPools: [],
      defaultDataPoolId: null,
      selectedDataPoolId: null,
      dataPoolAssets: [],
      selectedDataPoolAssetId: null,
      dataPoolAssetDetail: null,
      dataPoolPreviewPoolId: null,
      dataPoolPreviewPayload: null,
      dataPoolPreviewRepoPage: 1,
      dataPoolPreviewRepoPageSize: 10,
      selectedDataPoolDetailAssetKey: null,
      selectedDataPoolCompletionFileByAssetKey: {},
      dataPoolCompletionFileCache: {},
      selectedDataPoolIssueIndex: 1,
      dataPoolDetailVisible: false,
      dataPoolDownloadSelectionId: null,
      selectedDataPoolAssetIds: new Set(),
      dataPoolList: {
        page: 1,
        pageSize: 25,
        sortField: "created_at",
        sortOrder: "desc",
        total: 0,
        totalPages: 1,
        filters: {
          query: "",
          repo: "",
          commit: "",
          language: "",
          entryFile: "",
          depth: "",
          depthMin: "",
          depthMax: "",
          starsMin: "",
          starsMax: "",
          stage2RunId: "",
          stage3RunId: "",
          stage4RunId: "",
          createdAfter: "",
          createdBefore: "",
        },
      },
      stage4Runtime: null,
      stage4SourcesLoading: false,
      stage4RunsLoading: false,
      selectedStage4RunId: null,
      selectedStage4SourceSavepointId: null,
      selectedStage4SourceGroupKey: null,
      selectedStage4AssetKey: null,
      selectedStage4IssueIndex: 1,
      selectedStage4ReceiveFeedbackIndex: 1,
      pendingStage4RunOpenDefaults: false,
      stage3RepoBulkRunInFlight: {},
      stage4GroupRunInFlight: {},
      stage3RepoImagePrewarmInFlight: {},
      stage3RepositoryImagePrewarm: null,
      stage3RepositoryImagePrewarmLoading: false,
      stage3RepositoryImagePrewarmLogScrollState: {},
      stage2ImageAssets: null,
      stage2ImageAssetsLoading: false,
      selectedStage2ImageCatalogAssetKey: null,
      stage2ImageAssetDetail: null,
      stage2ImageAssetDetailLoading: false,
      stage2ImageAssetLogScrollState: {},
      managedImages: null,
      managedImagesLoading: false,
      managedImageDeleteInFlight: {},
      managedImageSelectedRefs: {},
      managedImageFilters: {
        image: "",
        lastUsedAfter: "",
        lastUsedBefore: "",
        createdAfter: "",
        createdBefore: "",
        sizeMinGb: "",
        sizeMaxGb: "",
      },
      managedImageSort: {
        field: "last_used_at",
        order: "desc",
      },
      stage2SdkPrewarm: null,
      stage2SdkPrewarmLoading: false,
      stage2SdkPrewarmBuilding: false,
      stage2SdkPrewarmBuildingTarget: null,
      stage2SdkPrewarmCancelingTarget: null,
      stage2SdkPrewarmLogScrollState: {},
      stage2ImageAssetCancelInFlight: {},
      stage3RepoImagePrewarmCancelInFlight: {},
      activeTab: "batch",
      lastRefresh: null,
      runtime: null,
      stage1CrawlTemplates: [],
      selectedStage1CrawlTemplateId: null,
      selectedBatchStage1TemplateId: null,
      stage2Runtime: null,
      stage3Runtime: null,
      stage4RuntimeTemplates: [],
      stage2RuntimeTemplates: [],
      stage3RuntimeTemplates: [],
      selectedStage2RuntimeTemplateId: null,
      selectedStage3RuntimeTemplateId: null,
      selectedStage4RuntimeTemplateId: null,
      stage2RuntimeDraftSecrets: {
        planner_api_key: null,
        worker_api_key: null,
      },
      stage3RuntimeDraftSecrets: {
        breaker_api_key: null,
      },
      stage4RuntimeDraftSecrets: {
        issuer_api_key: null,
      },
      stage2RunRuntimeEditor: {
        runId: null,
        draft: null,
        plannerApiKeyPreview: "",
        workerApiKeyPreview: "",
        message: "",
        saving: false,
      },
      stage3RunRuntimeEditor: {
        runId: null,
        draft: null,
        breakerApiKeyPreview: "",
        message: "",
        saving: false,
      },
      stage4RunRuntimeEditor: {
        runId: null,
        draft: null,
        issuerApiKeyPreview: "",
        message: "",
        saving: false,
      },
      stage2ConfigExpanded: true,
      stage3ConfigExpanded: true,
      stage4ConfigExpanded: true,
      stage2DetailSections: {
        runtimeConfig: false,
      },
      stage3DetailSections: {
        runtimeConfig: false,
      },
      stage4DetailSections: {
        runtimeConfig: false,
      },
      detailSections: {
        queryAudit: false,
        repositories: false,
      },
      crawlForm: {
        languages: ["Python"],
        licenses: [],
      },
      repoList: {
        page: 1,
        pageSize: 15,
        sortField: "discovered_at",
        sortOrder: "desc",
        total: 0,
        totalPages: 1,
        filters: {
          nameQuery: "",
          statuses: [],
          languages: [],
          licenses: [],
          starsMin: "",
          starsMax: "",
          createdAfter: "",
          createdBefore: "",
          pushedAfter: "",
          pushedBefore: "",
          discoveredAfter: "",
          discoveredBefore: "",
        },
      },
      stage2List: {
        page: 1,
        pageSize: 15,
        sortField: "latest_operation_at",
        sortOrder: "desc",
        detailVisible: false,
        total: 0,
        totalPages: 1,
        filters: {
          nameQuery: "",
          statuses: [],
          languages: [],
          licenses: [],
          starsMin: "",
          starsMax: "",
          createdAfter: "",
          createdBefore: "",
          pushedAfter: "",
          pushedBefore: "",
          discoveredAfter: "",
          discoveredBefore: "",
        },
      },
      stage3RepositoryDetailPayload: null,
      stage3List: {
        page: 1,
        pageSize: 15,
        sortField: "latest_operation_at",
        sortOrder: "desc",
        detailVisible: false,
        entryDetailVisible: false,
        total: 0,
        totalPages: 1,
        filters: {
          nameQuery: "",
          statuses: [],
          languages: [],
          starsMin: "",
          starsMax: "",
          createdAfter: "",
          createdBefore: "",
          pushedAfter: "",
          pushedBefore: "",
          discoveredAfter: "",
          discoveredBefore: "",
        },
      },
      stage3EntryList: {
        sortField: "test_file_path",
        sortOrder: "asc",
        filters: {
          statuses: [],
          originalTestsMin: "",
          originalTestsMax: "",
          originalPassRateMin: "",
          originalPassRateMax: "",
          dataCountMin: "",
          dataCountMax: "",
        },
      },
      stage4List: {
        page: 1,
        pageSize: 15,
        detailVisible: false,
        detailMode: "list",
        sourceSortField: "latest_operation_at",
        sourceSortOrder: "desc",
        savepointSortField: "depth",
        savepointSortOrder: "desc",
        total: 0,
        totalPages: 1,
        sourceFilters: {
          nameQuery: "",
          languages: [],
          statuses: [],
        },
        savepointFilters: {
          entryQuery: "",
          statuses: [],
          testCountMin: "",
          testCountMax: "",
          entryPassRateMin: "",
          entryPassRateMax: "",
          diffLinesMin: "",
          diffLinesMax: "",
        },
      },
    };
    const REPO_FILTERS_STORAGE_KEY = `feature_factory:${FRONTEND_SESSION_ID}:repo_filters`;
    const STAGE2_FILTERS_STORAGE_KEY = `feature_factory:${FRONTEND_SESSION_ID}:stage2_filters`;
    const STAGE3_FILTERS_STORAGE_KEY = `feature_factory:${FRONTEND_SESSION_ID}:stage3_filters`;
    const STAGE3_ENTRY_VIEWS_STORAGE_KEY = `feature_factory:${FRONTEND_SESSION_ID}:stage3_entry_views`;
    const CRAWL_FORM_STORAGE_KEY = `feature_factory:${FRONTEND_SESSION_ID}:crawl_form`;
    const BATCH_FORM_COLLAPSED_STORAGE_KEY = `feature_factory:${FRONTEND_SESSION_ID}:batch_form_collapsed`;
    const AUTO_REFRESH_JOB_STATUSES = new Set(["queued", "planning", "running"]);
    const STAGE2_NON_PENDING_STATUSES = ["queued", "running", "succeeded", "abandoned", "defect", "failed"];
    const STAGE3_NON_PENDING_STATUSES = ["queued", "running", "succeeded", "failed", "interrupted"];
    const STAGE4_NON_PENDING_STATUSES = ["queued", "running", "generated", "failed", "interrupted"];
    const STAGE4_SAVEPOINT_NON_PENDING_STATUSES = ["queued", "running", "generated", "failed", "interrupted"];
    let refreshInFlight = false;
    let assetImageRefreshInFlight = false;
    let managedImageRefreshInFlight = false;
    let sdkPrewarmRefreshInFlight = false;
    let stage1JobDetailEventSource = null;
    let stage1JobDetailStreamJobId = null;
    let stage2RepositoryDetailEventSource = null;
    let stage2RepositoryDetailStreamRepositoryId = null;
    let stage2RepositoryDetailStreamRunId = null;
    let stage3RepositoryDetailEventSource = null;
    let stage3RepositoryDetailStreamRepositoryId = null;
    let stage3RepositoryDetailStreamSnapshotId = null;
    let stage3RepositoryDetailStreamEntryFileId = null;
    let stage3RepositoryDetailStreamRunId = null;
    let stage4SourceDetailEventSource = null;
    let stage4SourceDetailStreamRepositoryId = null;
    let stage4SourceDetailStreamSnapshotId = null;
    let stage4SourceDetailStreamQuery = null;
    let stage4RunDetailEventSource = null;
    let stage4RunDetailStreamRunId = null;
    let batchTaskDetailEventSource = null;
    let batchTaskDetailStreamKey = null;
    let pendingBatchTaskDetailPayload = null;
    let pendingStage1JobDetailPayload = null;
    let pendingStage2RepositoryDetailPayload = null;
    let pendingStage3RepositoryDetailPayload = null;
    let pendingStage3RepositoryDetailRerender = false;
    let pendingStage4SourceDetailPayload = null;
    let pendingStage4RunDetailPayload = null;

    const $ = (selector) => document.querySelector(selector);

    function resolveActiveTheme() {
      return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
    }

    function hasSavedThemePreference() {
      try {
        const saved = localStorage.getItem(THEME_STORAGE_KEY);
        return saved === "light" || saved === "dark";
      } catch (error) {
        return false;
      }
    }

    function syncThemeToggle(theme = resolveActiveTheme()) {
      const toggle = $("#theme-toggle");
      const value = $("#theme-toggle-value");
      if (!toggle || !value) {
        return;
      }
      const isDark = theme === "dark";
      value.textContent = isDark ? "☾" : "☀";
      toggle.setAttribute("aria-pressed", String(isDark));
      toggle.setAttribute("aria-label", isDark ? "当前深色主题，点击切换到浅色主题" : "当前浅色主题，点击切换到深色主题");
      toggle.title = isDark ? "切换到浅色主题" : "切换到深色主题";
    }

    function applyTheme(theme, { persist = true } = {}) {
      const nextTheme = theme === "dark" ? "dark" : "light";
      document.documentElement.dataset.theme = nextTheme;
      if (persist) {
        try {
          localStorage.setItem(THEME_STORAGE_KEY, nextTheme);
        } catch (error) {
          // Ignore storage failures so the UI still works in constrained environments.
        }
      }
      syncThemeToggle(nextTheme);
    }

    function toggleTheme() {
      applyTheme(resolveActiveTheme() === "dark" ? "light" : "dark");
    }

    function defaultRepoFilters() {
      return {
        nameQuery: "",
        languages: [],
        licenses: [],
        starsMin: "",
        starsMax: "",
        createdAfter: "",
        createdBefore: "",
        pushedAfter: "",
        pushedBefore: "",
        discoveredAfter: "",
        discoveredBefore: "",
      };
    }

    function defaultStage2Filters() {
      return {
        nameQuery: "",
        statuses: [],
        languages: [],
        licenses: [],
        starsMin: "",
        starsMax: "",
        createdAfter: "",
        createdBefore: "",
        pushedAfter: "",
        pushedBefore: "",
        discoveredAfter: "",
        discoveredBefore: "",
      };
    }

    function defaultStage3Filters() {
      return {
        nameQuery: "",
        statuses: [],
        languages: [],
        starsMin: "",
        starsMax: "",
        createdAfter: "",
        createdBefore: "",
        pushedAfter: "",
        pushedBefore: "",
        discoveredAfter: "",
        discoveredBefore: "",
      };
    }

    function defaultStage3EntryFilters() {
      return {
        statuses: [],
        originalTestsMin: "",
        originalTestsMax: "",
        originalPassRateMin: "",
        originalPassRateMax: "",
        dataCountMin: "",
        dataCountMax: "",
      };
    }

    function defaultStage3EntryListState() {
      return {
        sortField: "test_file_path",
        sortOrder: "asc",
        filters: defaultStage3EntryFilters(),
      };
    }

    function readSessionJson(key) {
      try {
        const raw = window.sessionStorage.getItem(key);
        return raw ? JSON.parse(raw) : null;
      } catch {
        return null;
      }
    }

    function writeSessionJson(key, value) {
      try {
        window.sessionStorage.setItem(key, JSON.stringify(value));
      } catch {
        // Ignore storage failures and keep the UI functional.
      }
    }

    function removeSessionJson(key) {
      try {
        window.sessionStorage.removeItem(key);
      } catch {
        // Ignore storage failures and keep the UI functional.
      }
    }

    function normalizeStringArray(value) {
      if (!Array.isArray(value)) {
        return [];
      }
      return value
        .map((item) => String(item || "").trim())
        .filter(Boolean);
    }

    function normalizeRepoFilters(value) {
      const fallback = defaultRepoFilters();
      if (!value || typeof value !== "object") {
        return fallback;
      }
      return {
        nameQuery: String(value.nameQuery || "").trim(),
        languages: normalizeStringArray(value.languages),
        licenses: normalizeStringArray(value.licenses),
        starsMin: String(value.starsMin || "").trim(),
        starsMax: String(value.starsMax || "").trim(),
        createdAfter: String(value.createdAfter || ""),
        createdBefore: String(value.createdBefore || ""),
        pushedAfter: String(value.pushedAfter || ""),
        pushedBefore: String(value.pushedBefore || ""),
        discoveredAfter: String(value.discoveredAfter || ""),
        discoveredBefore: String(value.discoveredBefore || ""),
      };
    }

    function normalizeStage2Filters(value) {
      const fallback = defaultStage2Filters();
      if (!value || typeof value !== "object") {
        return fallback;
      }
      return {
        nameQuery: String(value.nameQuery || "").trim(),
        statuses: normalizeStringArray(value.statuses),
        languages: normalizeStringArray(value.languages),
        licenses: normalizeStringArray(value.licenses),
        starsMin: String(value.starsMin || "").trim(),
        starsMax: String(value.starsMax || "").trim(),
        createdAfter: String(value.createdAfter || ""),
        createdBefore: String(value.createdBefore || ""),
        pushedAfter: String(value.pushedAfter || ""),
        pushedBefore: String(value.pushedBefore || ""),
        discoveredAfter: String(value.discoveredAfter || ""),
        discoveredBefore: String(value.discoveredBefore || ""),
      };
    }

    function normalizeStage3Filters(value) {
      const fallback = defaultStage3Filters();
      if (!value || typeof value !== "object") {
        return fallback;
      }
      return {
        nameQuery: String(value.nameQuery || "").trim(),
        statuses: normalizeStringArray(value.statuses),
        languages: normalizeStringArray(value.languages),
        starsMin: String(value.starsMin || "").trim(),
        starsMax: String(value.starsMax || "").trim(),
        createdAfter: String(value.createdAfter || ""),
        createdBefore: String(value.createdBefore || ""),
        pushedAfter: String(value.pushedAfter || ""),
        pushedBefore: String(value.pushedBefore || ""),
        discoveredAfter: String(value.discoveredAfter || ""),
        discoveredBefore: String(value.discoveredBefore || ""),
      };
    }

    function normalizeStage3EntryFilters(value) {
      const fallback = defaultStage3EntryFilters();
      if (!value || typeof value !== "object") {
        return fallback;
      }
      return {
        statuses: normalizeStringArray(value.statuses),
        originalTestsMin: String(value.originalTestsMin || "").trim(),
        originalTestsMax: String(value.originalTestsMax || "").trim(),
        originalPassRateMin: String(value.originalPassRateMin || "").trim(),
        originalPassRateMax: String(value.originalPassRateMax || "").trim(),
        dataCountMin: String(value.dataCountMin || "").trim(),
        dataCountMax: String(value.dataCountMax || "").trim(),
      };
    }

    function normalizeStage3EntryListState(value) {
      const fallback = defaultStage3EntryListState();
      if (!value || typeof value !== "object") {
        return fallback;
      }
      const sortField = String(value.sortField || fallback.sortField);
      const normalizedSortField = new Set([
        "test_file_path",
        "baseline_total_tests",
        "baseline_pass_rate",
        "latest_operation_at",
        "savepoint_count",
        "status",
      ]).has(sortField) ? sortField : fallback.sortField;
      const sortOrder = String(value.sortOrder || fallback.sortOrder) === "desc" ? "desc" : "asc";
      return {
        sortField: normalizedSortField,
        sortOrder,
        filters: normalizeStage3EntryFilters(value.filters),
      };
    }

    function persistRepoFilters() {
      removeSessionJson(REPO_FILTERS_STORAGE_KEY);
    }

    function persistStage2Filters() {
      writeSessionJson(STAGE2_FILTERS_STORAGE_KEY, state.stage2List.filters);
    }

    function persistStage3Filters() {
      writeSessionJson(STAGE3_FILTERS_STORAGE_KEY, state.stage3List.filters);
    }

    function restoreFilterState() {
      state.repoList.filters = defaultRepoFilters();
      removeSessionJson(REPO_FILTERS_STORAGE_KEY);
      state.stage2List.filters = normalizeStage2Filters(readSessionJson(STAGE2_FILTERS_STORAGE_KEY));
      state.stage3List.filters = normalizeStage3Filters(readSessionJson(STAGE3_FILTERS_STORAGE_KEY));
    }

    function stage3EntryViewStorageKey(repositoryId, snapshotId) {
      const repoKey = String(repositoryId || "").trim();
      const snapshotKey = String(snapshotId || "").trim();
      if (!repoKey || !snapshotKey) {
        return "";
      }
      return `${repoKey}:${snapshotKey}`;
    }

    function readStage3EntryViewsStore() {
      const raw = readSessionJson(STAGE3_ENTRY_VIEWS_STORAGE_KEY);
      return raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
    }

    function writeStage3EntryViewsStore(store) {
      writeSessionJson(STAGE3_ENTRY_VIEWS_STORAGE_KEY, store);
    }

    function restoreStage3EntryListState(repositoryId, snapshotId) {
      const key = stage3EntryViewStorageKey(repositoryId, snapshotId);
      if (!key) {
        state.stage3EntryList = defaultStage3EntryListState();
        return state.stage3EntryList;
      }
      const store = readStage3EntryViewsStore();
      state.stage3EntryList = normalizeStage3EntryListState(store[key]);
      return state.stage3EntryList;
    }

    function persistStage3EntryListState(repositoryId, snapshotId) {
      const key = stage3EntryViewStorageKey(repositoryId, snapshotId);
      if (!key) {
        return;
      }
      const store = readStage3EntryViewsStore();
      store[key] = normalizeStage3EntryListState(state.stage3EntryList);
      writeStage3EntryViewsStore(store);
    }

    function normalizeOptionalNumber(value) {
      if (value == null || value === "") {
        return null;
      }
      const number = Number(value);
      return Number.isFinite(number) ? number : null;
    }

    function normalizeCrawlFormDraft(value) {
      if (!value || typeof value !== "object") {
        return null;
      }
      return {
        name: String(value.name || ""),
        languages: normalizeStringArray(value.languages),
        licenses: normalizeStringArray(value.licenses),
        starsMin: String(value.starsMin || ""),
        starsMax: String(value.starsMax || ""),
        repositoryLimit: String(value.repositoryLimit || ""),
        includeForks: Boolean(value.includeForks),
        includeArchived: Boolean(value.includeArchived),
        createdAfter: String(value.createdAfter || ""),
        createdBefore: String(value.createdBefore || ""),
        pushedAfter: String(value.pushedAfter || ""),
        pushedBefore: String(value.pushedBefore || ""),
        keywords: String(value.keywords || ""),
        targetRepositories: String(value.targetRepositories || ""),
        maxConcurrentJobs: String(value.maxConcurrentJobs || ""),
        maxConcurrentPartitions: String(value.maxConcurrentPartitions || ""),
        githubTokens: String(value.githubTokens || ""),
      };
    }

    function readCrawlFormDraft() {
      const form = $("#crawl-form");
      return {
        name: form.elements.name.value,
        languages: readSelectedCrawlLanguages(),
        licenses: readSelectedCrawlLicenses(),
        starsMin: form.elements.stars_min.value,
        starsMax: form.elements.stars_max.value,
        repositoryLimit: form.elements.repository_limit.value,
        includeForks: form.elements.include_forks.checked,
        includeArchived: form.elements.include_archived.checked,
        createdAfter: form.elements.created_after.value,
        createdBefore: form.elements.created_before.value,
        pushedAfter: form.elements.pushed_after.value,
        pushedBefore: form.elements.pushed_before.value,
        keywords: form.elements.keywords.value,
        targetRepositories: form.elements.target_repositories?.value || "",
        maxConcurrentJobs: $("#max-concurrent-jobs").value,
        maxConcurrentPartitions: $("#max-concurrent-partitions").value,
        githubTokens: $("#github-tokens").value,
      };
    }

    function applyCrawlFormDraft(value) {
      const draft = normalizeCrawlFormDraft(value);
      if (!draft) {
        return false;
      }
      const form = $("#crawl-form");
      form.elements.name.value = draft.name;
      form.elements.stars_min.value = draft.starsMin;
      form.elements.stars_max.value = draft.starsMax;
      form.elements.repository_limit.value = draft.repositoryLimit;
      form.elements.include_forks.checked = draft.includeForks;
      form.elements.include_archived.checked = draft.includeArchived;
      form.elements.created_after.value = draft.createdAfter;
      form.elements.created_before.value = draft.createdBefore;
      form.elements.pushed_after.value = draft.pushedAfter;
      form.elements.pushed_before.value = draft.pushedBefore;
      form.elements.keywords.value = draft.keywords;
      if (form.elements.target_repositories) {
        form.elements.target_repositories.value = draft.targetRepositories;
      }
      if (draft.maxConcurrentJobs) {
        $("#max-concurrent-jobs").value = draft.maxConcurrentJobs;
      }
      $("#max-concurrent-partitions").value = draft.maxConcurrentPartitions;
      $("#github-tokens").value = draft.githubTokens;
      state.crawlForm.languages = draft.languages;
      state.crawlForm.licenses = draft.licenses;
      syncCrawlLanguageInputs();
      updateCrawlLanguageToggle();
      syncCrawlLicenseInputs();
      updateCrawlLicenseToggle();
      return true;
    }

    function persistCrawlFormDraft() {
      writeSessionJson(CRAWL_FORM_STORAGE_KEY, readCrawlFormDraft());
    }

    function restoreCrawlFormDraft() {
      return applyCrawlFormDraft(readSessionJson(CRAWL_FORM_STORAGE_KEY));
    }

    function readBatchLayoutPreference() {
      try {
        return window.localStorage.getItem(BATCH_FORM_COLLAPSED_STORAGE_KEY) === "1";
      } catch (error) {
        return false;
      }
    }

    function persistBatchLayoutPreference() {
      try {
        window.localStorage.setItem(BATCH_FORM_COLLAPSED_STORAGE_KEY, state.batchFormCollapsed ? "1" : "0");
      } catch (error) {
        // Ignore storage failures so the UI can still toggle in constrained environments.
      }
    }

    function githubTokensFromStage1TemplateSnapshot(snapshot) {
      const source = snapshot && typeof snapshot === "object" ? snapshot : {};
      return Array.isArray(source.github_tokens)
        ? source.github_tokens.join("\n")
        : String(source.github_tokens || "");
    }

    function readStage1TemplatePayloadFromCrawlForm(name) {
      return {
        name,
        github_tokens: newlineValues($("#github-tokens")?.value || ""),
      };
    }

    function applyStage1TemplateToCrawlForm(snapshot) {
      $("#github-tokens").value = githubTokensFromStage1TemplateSnapshot(snapshot);
      persistCrawlFormDraft();
    }

    function isoFromLocal(value) {
      if (!value) return null;
      const date = new Date(value);
      return Number.isNaN(date.getTime()) ? null : date.toISOString();
    }

    function localFromISO(value) {
      if (!value) return "";
      const date = new Date(value);
      const pad = (v) => String(v).padStart(2, "0");
      return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
    }

    function formatDate(value) {
      if (!value) return "-";
      return new Date(value).toLocaleString();
    }

    function formatAuditMetric(value) {
      if (value == null) {
        return `<span class="muted">历史未记</span>`;
      }
      return String(value);
    }

    function formatQueryPageProgress(query) {
      const currentPage = query?.current_page;
      const totalPages = query?.total_pages;
      if (currentPage == null && totalPages == null) {
        return "-";
      }
      if (currentPage != null && totalPages != null) {
        return `${currentPage} / ${totalPages}`;
      }
      if (currentPage != null) {
        return `第 ${currentPage} 页`;
      }
      return `共 ${totalPages} 页`;
    }

    function escapeHtml(value) {
      return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }

    function formatRange(start, end) {
      if (!start && !end) return null;
      const from = start ? formatDate(start) : "不设下界";
      const to = end ? formatDate(end) : "不设上界";
      return `${from} → ${to}`;
    }

    function formatListPreview(items, { limit = 20, suffixLabel = "项" } = {}) {
      if (!Array.isArray(items) || items.length === 0) return "";
      const visibleItems = items.slice(0, limit);
      if (items.length <= limit) {
        return visibleItems.join("\n");
      }
      const omittedCount = items.length - limit;
      return `${visibleItems.join("\n")}\n…（已省略 ${omittedCount} ${suffixLabel}，共 ${items.length} ${suffixLabel}）`;
    }

    function formatFilterDisplay(filters) {
      const result = {};
      const languages = Array.isArray(filters.languages) && filters.languages.length > 0
        ? filters.languages
        : (filters.language ? [filters.language] : []);
      if (languages.length > 0) {
        result["语言"] = languages.join(", ");
      }
      const createdRange = formatRange(filters.created_after, filters.created_before);
      if (createdRange) {
        result["创建时间"] = createdRange;
      }
      const pushedRange = formatRange(filters.pushed_after, filters.pushed_before);
      if (pushedRange) {
        result["最近更新"] = pushedRange;
      }
      if (filters.stars_min != null || filters.stars_max != null) {
        const min = filters.stars_min != null ? String(filters.stars_min) : "不限";
        const max = filters.stars_max != null ? String(filters.stars_max) : "不限";
        result["Star 数范围"] = `${min} ~ ${max}`;
      }
      if (filters.repository_limit != null) {
        result["仓库个数上限"] = String(filters.repository_limit);
      }
      result["包含 fork"] = filters.exclude_forks ? "否" : "是";
      result["包含 archived"] = filters.exclude_archived ? "否" : "是";
      if (Array.isArray(filters.licenses) && filters.licenses.length > 0) {
        result["许可证白名单"] = filters.licenses.join(", ");
      }
      if (Array.isArray(filters.keywords) && filters.keywords.length > 0) {
        result["附加关键词"] = filters.keywords.join(", ");
      }
      if (Array.isArray(filters.target_repositories) && filters.target_repositories.length > 0) {
        result["抓取指定仓库"] = formatListPreview(filters.target_repositories, {
          limit: 20,
          suffixLabel: "个仓库",
        });
      }
      return result;
    }

    function formatJobStatsDisplay(stats) {
      const statusLabels = {
        pending: "待执行",
        running: "执行中",
        completed: "已完成",
        skipped: "已跳过",
        overflow: "超限",
        failed: "失败",
      };
      const result = {};
      if (stats.total_partitions != null) {
        result["时间分片总数"] = stats.total_partitions;
      }
      if (stats.max_concurrent_partitions != null) {
        result["单任务分片并发数"] = stats.max_concurrent_partitions;
      }
      if (stats.partition_status_counts && Object.keys(stats.partition_status_counts).length > 0) {
        const translatedCounts = {};
        Object.entries(stats.partition_status_counts).forEach(([status, count]) => {
          translatedCounts[statusLabels[status] || status] = count;
        });
        result["分片状态统计"] = translatedCounts;
      }
      if (stats.repository_hits != null) {
        result["GitHub 返回结果数"] = stats.repository_hits;
      }
      if (stats.unique_repositories != null) {
        result["去重后仓库数"] = stats.unique_repositories;
      }
      return result;
    }

    function renderPlanningProgress(progress, options = {}) {
      if (!progress) {
        return "";
      }
      const standaloneCard = options.card !== false;
      const ratio = Math.max(0, Math.min(1, Number(progress.progress_ratio) || 0));
      const percent = Math.round(ratio * 100);
      const eventLabels = {
        starting: "准备开始规划",
        counting: "正在探测当前查询",
        presplit: "时间范围过大，先预切",
        split: "命中太多，继续切分",
        planned: "已生成一个可抓取分片",
        empty: "当前时间窗口无结果",
      };
      const currentRange = formatRange(progress.current_range_start, progress.current_range_end) || "当前还没有时间窗口";
      const currentQueryMeta = progress.current_query_total
        ? `第 ${progress.current_query_index || 0} / ${progress.current_query_total} 条 query`
        : "当前没有活跃 query";
      const updatedAt = progress.updated_at ? formatDate(progress.updated_at) : "-";
      return `
        <div class="${standaloneCard ? "detail-card detail-section " : ""}planning-progress-card">
          <h4>规划进度</h4>
          <div class="planning-progress-meta">
            <div>${escapeHtml(eventLabels[progress.event] || "正在规划")}</div>
            <div>上次更新：${escapeHtml(updatedAt)}</div>
          </div>
          <div class="planning-progress-bar" aria-hidden="true">
            <span style="width: ${percent}%"></span>
          </div>
          <div class="planning-progress-meta">
            <div>当前已发现 ${progress.discovered_windows ?? 0} 个时间窗口，已处理 ${progress.processed_windows ?? 0} 个</div>
            <div>进度 ${percent}%</div>
          </div>
          <div class="planning-progress-grid">
            <div class="planning-progress-item">
              <div class="planning-progress-label">待探测窗口</div>
              <strong>${progress.queued_windows ?? 0}</strong>
            </div>
            <div class="planning-progress-item">
              <div class="planning-progress-label">已生成分片</div>
              <strong>${progress.planned_partitions ?? 0}</strong>
            </div>
            <div class="planning-progress-item">
              <div class="planning-progress-label">Count 调用</div>
              <strong>${progress.count_query_calls ?? 0}</strong>
            </div>
          </div>
          <div class="planning-progress-note">
            <div>当前时间窗口：${escapeHtml(currentRange)}</div>
            <div>当前深度：${progress.current_depth ?? 0}</div>
            <div>当前查询：${escapeHtml(currentQueryMeta)}</div>
            <div>当前 query 内容：</div>
            <div class="query-text">${progress.current_query_string ? escapeHtml(progress.current_query_string) : "当前没有正在探测的 query"}</div>
            <div>最近一次估计结果数：${progress.last_estimated_count ?? "-"}</div>
          </div>
        </div>
      `;
    }

    function renderPlanningProgressSummary(progress) {
      if (!progress) {
        return "";
      }
      const ratio = Math.max(0, Math.min(1, Number(progress.progress_ratio) || 0));
      const percent = Math.round(ratio * 100);
      const processed = Number(progress.processed_windows ?? 0);
      const discovered = Number(progress.discovered_windows ?? 0);
      return `
        <div class="muted">规划进度 ${percent}% · 窗口 ${processed}/${discovered}</div>
      `;
    }

    function statusPill(status) {
      const labels = {
        queued: "排队中",
        pending: "待执行",
        planning: "规划中",
        running: "执行中",
        paused: "已暂停",
        completed: "已完成",
        partial: "部分完成",
        failed: "失败",
      };
      return `<span class="status ${status}">${labels[status] || status}</span>`;
    }

    function formatDuration(seconds) {
      if (seconds == null || Number.isNaN(Number(seconds))) return "-";
      const total = Math.max(0, Math.round(Number(seconds)));
      if (total < 60) return `${total}s`;
      if (total < 3600) return `${Math.floor(total / 60)}m ${total % 60}s`;
      const hours = Math.floor(total / 3600);
      const minutes = Math.floor((total % 3600) / 60);
      return `${hours}h ${minutes}m`;
    }

    function formatBytes(bytes) {
      const value = Number(bytes);
      if (!Number.isFinite(value) || value < 0) return "-";
      if (value < 1024) return `${Math.round(value)} B`;
      if (value < 1024 * 1024) return `${Math.round(value / 102.4) / 10} KB`;
      if (value < 1024 * 1024 * 1024) return `${Math.round(value / 1024 / 102.4) / 10} MB`;
      if (value < 1024 * 1024 * 1024 * 1024) return `${Math.round(value / 1024 / 1024 / 102.4) / 10} GB`;
      return `${Math.round(value / 1024 / 1024 / 1024 / 102.4) / 10} TB`;
    }

    function formatPercent(value) {
      const number = Number(value);
      if (!Number.isFinite(number)) return "-";
      return `${(number * 100).toFixed(1)}%`;
    }

    function formatAverage(value) {
      if (!Number.isFinite(value)) return "-";
      const rounded = Math.round(value * 10) / 10;
      return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
    }

    function stage2StatusLabel(status) {
      const labels = {
        pending: "未运行",
        queued: "排队中",
        running: "运行中",
        succeeded: "成功",
        abandoned: "废弃",
        defect: "缺陷",
        failed: "失败",
        schedule_failed: "调度失败",
        planner_failed: "planner 失败",
        worker_failed: "未执行到 attempt1",
        full_test_error: "full test 出错",
        interrupted: "已中断",
      };
      const resumePendingMatch = String(status || "").match(/^worker_failed_before_attempt(\d+)$/);
      if (resumePendingMatch) {
        return `未执行到 attempt${resumePendingMatch[1]}`;
      }
      const attemptMatch = String(status || "").match(/^attempt(\\d+)_failed$/);
      if (attemptMatch) {
        return `attempt${attemptMatch[1]} 已执行`;
      }
      return labels[status] || status;
    }

    function stage2StatusPill(status, label = null) {
      return `<span class="status ${status}">${escapeHtml(String(label == null ? stage2StatusLabel(status) : label))}</span>`;
    }

    function stage3RunningStateLabel(run) {
      const phase = String(run?.phase || "");
      if (phase === "bootstrap" || phase === "created" || phase === "lifecycle") {
        return "初始化中";
      }
      if (phase === "preparing_workspace") {
        return "准备工作区中";
      }
      if (phase === "baseline_prepare" || phase === "host_planner" || phase === "resume") {
        return "准备基线仓库中";
      }
      if (phase === "breaker_running" || phase === "breaker_agent") {
        return "breaker agent 进行中";
      }
      if (phase === "savepoint_validation" || phase === "savepoint") {
        return "savepoint validation 进行中";
      }
      if (phase === "cleanup") {
        return "收尾中";
      }
      return "运行中";
    }

    function stage3PhaseLabel(phase) {
      const normalizedPhase = String(phase || "");
      const labels = {
        bootstrap: "初始化",
        created: "已创建",
        queued: "排队中",
        lifecycle: "生命周期",
        preparing_workspace: "准备工作区",
        baseline_prepare: "准备基线仓库",
        host_planner: "准备基线仓库",
        resume: "恢复 checkpoint",
        breaker_running: "breaker agent",
        breaker_agent: "breaker agent",
        savepoint_validation: "savepoint validation",
        savepoint: "savepoint validation",
        cleanup: "收尾",
        completed: "已完成",
        interrupt: "中断",
        failed: "失败",
        interrupted: "已中断",
      };
      return labels[normalizedPhase] || normalizedPhase || "-";
    }

    function stage3StatusLabel(run) {
      const displayStatus = String(run?.display_status || run?.status || "pending");
      if (displayStatus === "running") {
        return stage3RunningStateLabel(run);
      }
      const labels = {
        pending: "未运行",
        queued: "排队中",
        running: "运行中",
        generated: "已生成",
        succeeded: "已生成",
        failed: "失败",
        interrupted: "已中断",
        schedule_failed: "调度失败",
        completed: "已完成",
      };
      return labels[displayStatus] || displayStatus;
    }

    function stage3StatusClass(run) {
      const displayStatus = String(run?.display_status || run?.status || "pending");
      if (displayStatus === "succeeded") {
        return "completed";
      }
      if (displayStatus === "failed" || displayStatus === "interrupted" || displayStatus === "schedule_failed") {
        return "failed";
      }
      return displayStatus;
    }

    function stage3StatusPill(run) {
      return `<span class="status ${stage3StatusClass(run)}">${escapeHtml(stage3StatusLabel(run))}</span>`;
    }

    function stage3RepositoryStatusLabel(status) {
      const labels = {
        pending: "未运行",
        queued: "排队中",
        running: "运行中",
        generated: "已生成",
        succeeded: "已生成",
        failed: "失败",
        interrupted: "已中断",
      };
      return labels[String(status || "pending")] || String(status || "pending");
    }

    function stage3RepositoryStatusClass(status) {
      const normalized = String(status || "pending");
      if (normalized === "generated") return "succeeded";
      if (normalized === "succeeded") return "succeeded";
      if (normalized === "interrupted") return "failed";
      if (normalized === "failed") return "failed";
      if (normalized === "running") return "running";
      if (normalized === "queued") return "queued";
      return "pending";
    }

    function stage3RepositoryStatusPill(status) {
      return `<span class="status ${stage3RepositoryStatusClass(status)}">${escapeHtml(stage3RepositoryStatusLabel(status))}</span>`;
    }

    function stage3EntryFileStatusLabel(status) {
      const normalized = String(status || "pending");
      if (normalized === "pending") return "未运行";
      if (normalized === "queued") return "排队中";
      if (normalized === "running") return "运行中";
      if (normalized === "succeeded") return "成功";
      if (normalized === "interrupted") return "已中断";
      if (normalized === "failed") return "失败";
      return normalized;
    }

    function stage3EntryFileStatusPill(status) {
      return `<span class="status ${stage3RepositoryStatusClass(status)}">${escapeHtml(stage3EntryFileStatusLabel(status))}</span>`;
    }

    function imageAssetStatusClass(status, overrideLabel = "") {
      if (overrideLabel === "下载中") return "running";
      if (overrideLabel === "已取消") return "failed";
      if (status?.in_progress) return "running";
      if (["queued", "running"].includes(String(status?.last_job_status || ""))) return "running";
      if (status?.last_job_status === "cancelled") return "failed";
      if (!status?.present) return "pending";
      if (status.needs_update) return "queued";
      return "succeeded";
    }

    function imageAssetStatusLabel(status, overrideLabel = "") {
      if (overrideLabel) return overrideLabel;
      if (status?.in_progress) return "下载中";
      if (["queued", "running"].includes(String(status?.last_job_status || ""))) return "下载中";
      if (status?.last_job_status === "cancelled") return "已取消";
      if (!status?.present) return "未下载";
      if (status.needs_update) return "需要更新";
      return "已下载";
    }

    function imageAssetStatusPill(status, overrideLabel = "") {
      return `<span class="status ${imageAssetStatusClass(status, overrideLabel)}">${escapeHtml(imageAssetStatusLabel(status, overrideLabel))}</span>`;
    }

    function sdkPrewarmPill(label) {
      const normalized = String(label || "未预热");
      const className = normalized === "已预热"
        ? "succeeded"
        : (normalized === "预热中" ? "running" : (normalized === "已取消" ? "failed" : "pending"));
      return `<span class="status ${className}">${escapeHtml(normalized)}</span>`;
    }

    function managedImageDeleteJobLabel(job) {
      if (!job) return "-";
      if (job.in_progress || ["queued", "running"].includes(String(job.last_status || ""))) return "删除中";
      if (job.last_status === "succeeded") return "已删除";
      if (job.last_status === "failed") return "删除失败";
      return "-";
    }

    function managedImageDeleteJobPill(job) {
      const label = managedImageDeleteJobLabel(job);
      if (label === "-") return `<span class="muted">-</span>`;
      const className = label === "删除中"
        ? "running"
        : (label === "已删除" ? "succeeded" : "failed");
      return `<span class="status ${className}">${escapeHtml(label)}</span>`;
    }

    function managedImageSortDefaultOrder(field) {
      return ["last_used_at", "created_at", "size_bytes"].includes(String(field || ""))
        ? "desc"
        : "asc";
    }

    function managedImageSortValue(row, field) {
      if (field === "size_bytes") return Number(row?.size_bytes || 0);
      if (field === "last_used_at" || field === "created_at") {
        const time = Date.parse(row?.[field] || "");
        return Number.isFinite(time) ? time : 0;
      }
      if (field === "delete_job_status") {
        return managedImageDeleteJobLabel(row?.delete_job || {});
      }
      return String(row?.image_ref || "").toLowerCase();
    }

    function compareManagedImageRows(left, right) {
      const field = state.managedImageSort.field || "last_used_at";
      const descending = state.managedImageSort.order === "desc";
      const leftValue = managedImageSortValue(left, field);
      const rightValue = managedImageSortValue(right, field);
      let comparison = 0;
      if (typeof leftValue === "number" && typeof rightValue === "number") {
        comparison = leftValue - rightValue;
      } else {
        comparison = String(leftValue).localeCompare(String(rightValue), "zh-CN", {
          numeric: true,
          sensitivity: "base",
        });
      }
      if (comparison === 0 && field !== "image_ref") {
        comparison = String(left?.image_ref || "").localeCompare(String(right?.image_ref || ""), "zh-CN", {
          numeric: true,
          sensitivity: "base",
        });
      }
      return descending ? -comparison : comparison;
    }

    function updateManagedImageSortButtons() {
      document.querySelectorAll("[data-managed-image-sort]").forEach((button) => {
        const field = String(button.dataset.managedImageSort || "");
        const active = field === state.managedImageSort.field;
        button.classList.toggle("active", active);
        const arrow = active ? (state.managedImageSort.order === "asc" ? " ↑" : " ↓") : "";
        button.textContent = `${button.textContent.replace(/[ ↑↓]+$/, "")}${arrow}`;
      });
    }

    function hostSdkPrewarmLabel(hostEnvironment) {
      if (state.stage2SdkPrewarmBuildingTarget === "host") return "预热中";
      if (hostEnvironment?.in_progress) return "预热中";
      if (hostEnvironment?.last_status === "cancelled") return "已取消";
      return hostEnvironment?.prewarmed ? "已预热" : "未预热";
    }

    function plannerSdkPrewarmLabel(planner) {
      if (state.stage2SdkPrewarmBuildingTarget === "planner") return "预热中";
      if (planner?.in_progress) return "预热中";
      if (planner?.last_status === "cancelled") return "已取消";
      return planner?.prewarmed ? "已预热" : "未预热";
    }

    function imageAssetStatusMeta(status) {
      if (!status) return "";
      const parts = [];
      if (status.in_progress) {
        parts.push("后台任务进行中");
      }
      if (status.created_at) {
        parts.push(`创建 ${formatDate(status.created_at)}`);
      }
      if (status.dockerfile_modified_at) {
        parts.push(`Dockerfile ${formatDate(status.dockerfile_modified_at)}`);
      }
      if (status.dependency_needs_update) {
        parts.push("依赖 base image 需要更新");
      } else if (status.dockerfile_needs_update) {
        parts.push("Dockerfile 晚于本地镜像");
      }
      if (!status.present && status.inspect_error) {
        parts.push(status.inspect_error);
      }
      if (status.last_job_status === "failed" && status.last_job_error) {
        parts.push(`最近失败：${status.last_job_error}`);
      }
      return parts.join(" · ");
    }

    function setAssetImageMessage(text = "") {
      const normalized = String(text || "");
      const overviewMessage = $("#asset-stage2-image-message");
      const detailMessage = $("#asset-stage2-image-detail-message");
      if (overviewMessage) {
        overviewMessage.textContent = normalized;
      }
      if (detailMessage) {
        detailMessage.textContent = normalized;
      }
    }

    function setManagedImageMessage(text = "") {
      const message = $("#asset-managed-image-message");
      if (message) {
        message.textContent = String(text || "");
      }
    }

    function managedImageFilterValuesFromForm() {
      return {
        image: $("#asset-managed-image-filter-ref")?.value.trim() || "",
        lastUsedAfter: $("#asset-managed-image-filter-last-used-after")?.value || "",
        lastUsedBefore: $("#asset-managed-image-filter-last-used-before")?.value || "",
        createdAfter: $("#asset-managed-image-filter-created-after")?.value || "",
        createdBefore: $("#asset-managed-image-filter-created-before")?.value || "",
        sizeMinGb: $("#asset-managed-image-filter-size-min")?.value.trim() || "",
        sizeMaxGb: $("#asset-managed-image-filter-size-max")?.value.trim() || "",
      };
    }

    function applyManagedImageFiltersToForm(filters = state.managedImageFilters) {
      const values = {
        "#asset-managed-image-filter-ref": filters.image,
        "#asset-managed-image-filter-last-used-after": filters.lastUsedAfter,
        "#asset-managed-image-filter-last-used-before": filters.lastUsedBefore,
        "#asset-managed-image-filter-created-after": filters.createdAfter,
        "#asset-managed-image-filter-created-before": filters.createdBefore,
        "#asset-managed-image-filter-size-min": filters.sizeMinGb,
        "#asset-managed-image-filter-size-max": filters.sizeMaxGb,
      };
      Object.entries(values).forEach(([selector, value]) => {
        const input = $(selector);
        if (input) input.value = value || "";
      });
    }

    function managedImageFilterActive(filters = state.managedImageFilters) {
      return Object.values(filters || {}).some((value) => String(value || "").trim());
    }

    function managedImageDateFilterValue(value) {
      const time = Date.parse(value || "");
      return Number.isFinite(time) ? time : null;
    }

    function managedImageSizeFilterValue(value) {
      const normalized = String(value ?? "").trim();
      if (!normalized) return null;
      const size = Number(normalized);
      return Number.isFinite(size) && size >= 0 ? size * 1024 * 1024 * 1024 : null;
    }

    function managedImageRowTime(row, field) {
      const time = Date.parse(row?.[field] || "");
      return Number.isFinite(time) ? time : null;
    }

    function managedImageQueryTerms(value) {
      return String(value || "")
        .trim()
        .toLowerCase()
        .split(/[^a-z0-9]+/)
        .map((term) => term.trim())
        .filter(Boolean);
    }

    function compactManagedImageQuery(value) {
      return String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
    }

    function managedImageTextMatchesQuery(haystack, query) {
      const normalizedHaystack = String(haystack || "").toLowerCase();
      const normalizedQuery = String(query || "").trim().toLowerCase();
      if (!normalizedQuery) return true;
      if (normalizedHaystack.includes(normalizedQuery)) return true;
      const compactHaystack = compactManagedImageQuery(normalizedHaystack);
      const compactQuery = compactManagedImageQuery(normalizedQuery);
      if (compactQuery && compactHaystack.includes(compactQuery)) return true;
      const terms = managedImageQueryTerms(normalizedQuery);
      return terms.length > 0 && terms.every((term) => normalizedHaystack.includes(term));
    }

    function managedImageRowMatchesFilters(row) {
      const filters = state.managedImageFilters || {};
      const imageQuery = String(filters.image || "").trim();
      if (imageQuery) {
        const haystack = [
          row?.image_ref,
          row?.repository,
          row?.tag,
          row?.image_id,
        ].map((value) => String(value || "")).join("\n");
        if (!managedImageTextMatchesQuery(haystack, imageQuery)) return false;
      }

      const lastUsedAfter = managedImageDateFilterValue(filters.lastUsedAfter);
      const lastUsedBefore = managedImageDateFilterValue(filters.lastUsedBefore);
      if (lastUsedAfter !== null || lastUsedBefore !== null) {
        const lastUsedAt = managedImageRowTime(row, "last_used_at");
        if (lastUsedAt === null) return false;
        if (lastUsedAfter !== null && lastUsedAt < lastUsedAfter) return false;
        if (lastUsedBefore !== null && lastUsedAt > lastUsedBefore) return false;
      }

      const createdAfter = managedImageDateFilterValue(filters.createdAfter);
      const createdBefore = managedImageDateFilterValue(filters.createdBefore);
      if (createdAfter !== null || createdBefore !== null) {
        const createdAt = managedImageRowTime(row, "created_at");
        if (createdAt === null) return false;
        if (createdAfter !== null && createdAt < createdAfter) return false;
        if (createdBefore !== null && createdAt > createdBefore) return false;
      }

      const sizeMin = managedImageSizeFilterValue(filters.sizeMinGb);
      const sizeMax = managedImageSizeFilterValue(filters.sizeMaxGb);
      const sizeBytes = Number(row?.size_bytes || 0);
      if (sizeMin !== null && sizeBytes < sizeMin) return false;
      if (sizeMax !== null && sizeBytes > sizeMax) return false;
      return true;
    }

    function currentManagedImageRows() {
      const rows = Array.isArray(state.managedImages?.rows) ? state.managedImages.rows : [];
      const visibleRows = managedImageFilterActive() ? rows.filter(managedImageRowMatchesFilters) : rows.slice();
      return visibleRows.sort(compareManagedImageRows);
    }

    function managedImageSelectedRefs() {
      const knownRefs = new Set(
        (Array.isArray(state.managedImages?.rows) ? state.managedImages.rows : [])
          .map((row) => String(row?.image_ref || ""))
          .filter(Boolean),
      );
      return Object.keys(state.managedImageSelectedRefs || {})
        .filter((imageRef) => state.managedImageSelectedRefs[imageRef] && knownRefs.has(imageRef));
    }

    function setManagedImageSelected(imageRef, selected) {
      const normalizedRef = String(imageRef || "").trim();
      if (!normalizedRef) return;
      if (selected) {
        state.managedImageSelectedRefs[normalizedRef] = true;
      } else {
        delete state.managedImageSelectedRefs[normalizedRef];
      }
    }

    function updateManagedImageFilterButton() {
      const button = $("#asset-managed-image-filter-toggle");
      if (button) {
        button.classList.toggle("active", managedImageFilterActive());
      }
    }

    function updateManagedImageSelectionControls(rows = currentManagedImageRows()) {
      const checkbox = $("#asset-managed-image-select-all");
      const deleteButton = $("#asset-managed-image-delete-selected");
      const visibleRefs = rows.map((row) => String(row?.image_ref || "")).filter(Boolean);
      const selectedVisibleCount = visibleRefs.filter((imageRef) => state.managedImageSelectedRefs[imageRef]).length;
      if (checkbox) {
        checkbox.checked = visibleRefs.length > 0 && selectedVisibleCount === visibleRefs.length;
        checkbox.indeterminate = selectedVisibleCount > 0 && selectedVisibleCount < visibleRefs.length;
        checkbox.disabled = visibleRefs.length === 0;
      }
      if (deleteButton) {
        deleteButton.disabled = managedImageSelectedRefs().length === 0;
      }
      updateManagedImageFilterButton();
    }

    function closeManagedImageFilterPopover() {
      const popover = $("#asset-managed-image-filter-popover");
      if (popover) popover.hidden = true;
    }

    function findStage2ImageAssetRow(assetKey) {
      const normalizedAssetKey = String(assetKey || "").trim();
      const rows = Array.isArray(state.stage2ImageAssets?.rows) ? state.stage2ImageAssets.rows : [];
      return rows.find((row) => String(row?.asset_key || row?.image_id || "").trim() === normalizedAssetKey) || null;
    }

    function updateStage2ImageAssetLayout() {
      const overview = $("#asset-stage2-overview");
      const detailPanel = $("#asset-stage2-image-detail-panel");
      const hasDetail = Boolean(state.selectedStage2ImageCatalogAssetKey);
      if (overview) {
        overview.hidden = hasDetail;
      }
      if (detailPanel) {
        detailPanel.hidden = !hasDetail;
      }
    }

    function captureStage2ImageAssetLogScrollState() {
      const snapshot = {};
      document.querySelectorAll("[data-stage2-image-detail-log-target]").forEach((element) => {
        const target = String(element.dataset.stage2ImageDetailLogTarget || "").trim();
        if (!target) {
          return;
        }
        const maxScrollTop = Math.max(0, element.scrollHeight - element.clientHeight);
        snapshot[target] = {
          scrollTop: element.scrollTop,
          stickToBottom: maxScrollTop - element.scrollTop <= 24,
        };
      });
      state.stage2ImageAssetLogScrollState = snapshot;
    }

    function restoreStage2ImageAssetLogScrollState() {
      const snapshot = state.stage2ImageAssetLogScrollState || {};
      document.querySelectorAll("[data-stage2-image-detail-log-target]").forEach((element) => {
        const target = String(element.dataset.stage2ImageDetailLogTarget || "").trim();
        const saved = snapshot[target];
        if (!saved) {
          return;
        }
        const maxScrollTop = Math.max(0, element.scrollHeight - element.clientHeight);
        element.scrollTop = saved.stickToBottom ? maxScrollTop : Math.min(saved.scrollTop, maxScrollTop);
      });
    }

    function renderStage2SdkPrewarm() {
      const payload = state.stage2SdkPrewarm;
      const body = $("#asset-stage2-sdk-body");
      if (!body) {
        return;
      }
      captureStage2SdkPrewarmLogScrollState();
      if (!payload) {
        body.innerHTML = `<div class="muted">加载中...</div>`;
        return;
      }
      const hostEnvironment = payload.host_environment || {};
      const planner = payload.planner || {};
      const hostLogTail = hostEnvironment.log_tail || "暂无预热输出";
      const plannerLogTail = planner.log_tail || "暂无预热输出";
      const hostButtonDisabled = (state.stage2SdkPrewarmBuildingTarget === "host" || hostEnvironment.in_progress) ? " disabled" : "";
      const plannerButtonDisabled = (state.stage2SdkPrewarmBuildingTarget === "planner" || planner.in_progress) ? " disabled" : "";
      const hostCancelDisabled = state.stage2SdkPrewarmCancelingTarget === "host" ? " disabled" : "";
      const plannerCancelDisabled = state.stage2SdkPrewarmCancelingTarget === "planner" ? " disabled" : "";
      body.innerHTML = `
        <article class="asset-sdk-prewarm-block">
          <div class="asset-sdk-prewarm-head">
            <div>
              <h4>Host Python 环境</h4>
            </div>
            <div class="asset-sdk-prewarm-head-actions">
              ${sdkPrewarmPill(hostSdkPrewarmLabel(hostEnvironment))}
              <button class="secondary tiny" type="button" data-stage2-sdk-prewarm-target="host"${hostButtonDisabled}>预热</button>
              ${hostEnvironment.in_progress ? `<button class="danger tiny" type="button" data-stage2-sdk-prewarm-cancel-target="host"${hostCancelDisabled}>取消</button>` : ""}
            </div>
          </div>
          <pre class="asset-sdk-prewarm-log" data-stage2-sdk-prewarm-log-target="host">${escapeHtml(hostLogTail)}</pre>
        </article>
        <article class="asset-sdk-prewarm-block">
          <div class="asset-sdk-prewarm-head">
            <div>
              <h4>Planner agent-server 镜像</h4>
            </div>
            <div class="asset-sdk-prewarm-head-actions">
              ${sdkPrewarmPill(plannerSdkPrewarmLabel(planner))}
              <button class="secondary tiny" type="button" data-stage2-sdk-prewarm-target="planner"${plannerButtonDisabled}>预热</button>
              ${planner.in_progress ? `<button class="danger tiny" type="button" data-stage2-sdk-prewarm-cancel-target="planner"${plannerCancelDisabled}>取消</button>` : ""}
            </div>
          </div>
          <pre class="asset-sdk-prewarm-log" data-stage2-sdk-prewarm-log-target="planner">${escapeHtml(plannerLogTail)}</pre>
        </article>
      `;
      queueMicrotask(() => restoreStage2SdkPrewarmLogScrollState());
    }

    function captureStage2SdkPrewarmLogScrollState() {
      const snapshot = {};
      document.querySelectorAll("[data-stage2-sdk-prewarm-log-target]").forEach((element) => {
        const target = String(element.dataset.stage2SdkPrewarmLogTarget || "").trim();
        if (!target) {
          return;
        }
        const maxScrollTop = Math.max(0, element.scrollHeight - element.clientHeight);
        snapshot[target] = {
          scrollTop: element.scrollTop,
          stickToBottom: maxScrollTop - element.scrollTop <= 24,
        };
      });
      state.stage2SdkPrewarmLogScrollState = snapshot;
    }

    function restoreStage2SdkPrewarmLogScrollState() {
      const snapshot = state.stage2SdkPrewarmLogScrollState || {};
      document.querySelectorAll("[data-stage2-sdk-prewarm-log-target]").forEach((element) => {
        const target = String(element.dataset.stage2SdkPrewarmLogTarget || "").trim();
        const saved = snapshot[target];
        if (!saved) {
          return;
        }
        const maxScrollTop = Math.max(0, element.scrollHeight - element.clientHeight);
        element.scrollTop = saved.stickToBottom ? maxScrollTop : Math.min(saved.scrollTop, maxScrollTop);
      });
    }

    async function loadStage2SdkPrewarm({ silent = false } = {}) {
      state.stage2SdkPrewarmLoading = true;
      const message = $("#asset-stage2-sdk-message");
      if (message && !silent) {
        message.textContent = "加载中...";
      }
      try {
        state.stage2SdkPrewarm = await api("/api/assets/stage2/sdk-prewarm");
        renderStage2SdkPrewarm();
        if (message && !silent) {
          message.textContent = "";
        }
      } catch (error) {
        if ($("#asset-stage2-sdk-body") && !silent) {
          $("#asset-stage2-sdk-body").innerHTML = `<div class="muted">加载失败：${escapeHtml(error.message || String(error))}</div>`;
        }
        if (message && !silent) {
          message.textContent = `加载失败：${error.message || String(error)}`;
        }
      } finally {
        state.stage2SdkPrewarmLoading = false;
      }
    }

    async function prewarmStage2SdkTarget(target) {
      const normalizedTarget = String(target || "").trim();
      if (!["host", "planner"].includes(normalizedTarget)) {
        return;
      }
      const message = $("#asset-stage2-sdk-message");
      state.stage2SdkPrewarmBuilding = true;
      state.stage2SdkPrewarmBuildingTarget = normalizedTarget;
      renderStage2SdkPrewarm();
      if (message) {
        message.textContent = normalizedTarget === "host" ? "Host Python 环境预热中..." : "Planner agent-server 镜像预热中...";
      }
      try {
        const payload = await api(`/api/assets/stage2/sdk-prewarm/${normalizedTarget}`, {
          method: "POST",
          body: JSON.stringify({ force: false }),
        });
        state.stage2SdkPrewarm = payload.status || null;
        state.stage2SdkPrewarmBuildingTarget = null;
        renderStage2SdkPrewarm();
        if (message) {
          message.textContent = normalizedTarget === "host" ? "Host Python 环境预热完成" : "Planner agent-server 镜像预热完成";
        }
        setRefreshMeta();
      } catch (error) {
        try {
          state.stage2SdkPrewarm = await api("/api/assets/stage2/sdk-prewarm");
          state.stage2SdkPrewarmBuildingTarget = null;
          renderStage2SdkPrewarm();
        } catch (_refreshError) {
          // Keep the original prewarm failure visible.
        }
        if (message) {
          message.textContent = `预热失败：${error.message || String(error)}`;
        }
      } finally {
        state.stage2SdkPrewarmBuilding = false;
        state.stage2SdkPrewarmBuildingTarget = null;
        renderStage2SdkPrewarm();
      }
    }

    async function cancelStage2SdkPrewarmTarget(target) {
      const normalizedTarget = String(target || "").trim();
      if (!["host", "planner"].includes(normalizedTarget)) {
        return;
      }
      const message = $("#asset-stage2-sdk-message");
      state.stage2SdkPrewarmCancelingTarget = normalizedTarget;
      renderStage2SdkPrewarm();
      if (message) {
        message.textContent = normalizedTarget === "host" ? "正在取消 Host Python 环境预热..." : "正在取消 Planner agent-server 镜像预热...";
      }
      try {
        const payload = await api(`/api/assets/stage2/sdk-prewarm/${normalizedTarget}/cancel`, {
          method: "POST",
        });
        state.stage2SdkPrewarm = payload.status || null;
        renderStage2SdkPrewarm();
        if (message) {
          message.textContent = payload.cancelled ? "取消请求已发送" : "当前没有可取消的预热任务";
        }
      } catch (error) {
        if (message) {
          message.textContent = `取消失败：${error.message || String(error)}`;
        }
      } finally {
        state.stage2SdkPrewarmCancelingTarget = null;
        renderStage2SdkPrewarm();
      }
    }

    function renderStage2ImageAssets() {
      const payload = state.stage2ImageAssets;
      const body = $("#asset-stage2-image-body");
      const meta = $("#asset-stage2-image-meta");
      if (!body || !meta) {
        return;
      }
      if (!payload) {
        body.innerHTML = `<tr><td colspan="6" class="muted">加载中...</td></tr>`;
        meta.textContent = "";
        return;
      }
      const rows = Array.isArray(payload.rows) ? payload.rows : [];
      meta.textContent = "";
      if (!rows.length) {
        body.innerHTML = `<tr><td colspan="6" class="muted">暂无 base image 资产。</td></tr>`;
        return;
      }
      body.innerHTML = rows.map((row) => {
        const baseStatus = row.base_image || {};
        const agentStatus = row.agent_server_image || {};
        const assetKey = row.asset_key || row.image_id || "";
        const rowJob = row.build_job || {};
        const rowBusy = Boolean(rowJob.in_progress);
        const baseBusy = Boolean(baseStatus.in_progress);
        const agentBusy = Boolean(agentStatus.in_progress);
        const cancelInFlight = Boolean(state.stage2ImageAssetCancelInFlight[String(assetKey)]);
        const baseMeta = imageAssetStatusMeta(baseStatus);
        const agentMeta = imageAssetStatusMeta(agentStatus);
        const baseActionLabel = baseBusy ? "取消" : (!baseStatus.present ? "下载" : (baseStatus.needs_update ? "更新" : "已下载"));
        const baseActionEnabled = !rowBusy && (!baseStatus.present || baseStatus.needs_update);
        const agentActionLabel = agentBusy ? "取消" : (!agentStatus.present ? "下载" : "已下载");
        const agentActionEnabled = !rowBusy && !agentStatus.present && baseStatus.present && !baseStatus.needs_update;
        const agentDisabledTitle = !rowBusy && !agentStatus.present && (!baseStatus.present || baseStatus.needs_update)
          ? "需要先下载或更新 Base Image"
          : (agentBusy ? "取消后台下载任务" : (rowBusy ? "该 base image 资产已有后台任务正在执行" : ""));
        const baseDisabledTitle = baseBusy ? "取消后台下载任务" : (rowBusy ? "该 base image 资产已有后台任务正在执行" : "");
        const baseButtonAttrs = baseBusy
          ? `data-stage2-image-cancel-id="${escapeHtml(assetKey)}" data-stage2-image-cancel-kind="base"`
          : `data-stage2-image-build-kind="base" data-stage2-image-build-id="${escapeHtml(assetKey)}" data-stage2-image-force="${baseStatus.needs_update ? "1" : "0"}"`;
        const agentButtonAttrs = agentBusy
          ? `data-stage2-image-cancel-id="${escapeHtml(assetKey)}" data-stage2-image-cancel-kind="agent"`
          : `data-stage2-image-build-kind="agent" data-stage2-image-build-id="${escapeHtml(assetKey)}" data-stage2-image-force="0"`;
        const baseButtonEnabled = baseBusy ? !cancelInFlight : baseActionEnabled;
        const agentButtonEnabled = agentBusy ? !cancelInFlight : agentActionEnabled;
        const baseButtonClass = baseBusy ? "danger tiny" : "secondary tiny";
        const agentButtonClass = agentBusy ? "danger tiny" : "secondary tiny";
        return `
          <tr class="asset-image-row" data-stage2-image-asset-key="${escapeHtml(assetKey)}">
            <td>
              <strong class="mono">${escapeHtml(row.image_ref || "-")}</strong>
            </td>
            <td title="${escapeHtml(baseMeta)}">
              ${imageAssetStatusPill(baseStatus, baseBusy ? "下载中" : "")}
            </td>
            <td>
              <button
                class="${baseButtonClass}"
                type="button"
                ${baseButtonAttrs}
                title="${escapeHtml(baseDisabledTitle)}"
                ${baseButtonEnabled ? "" : "disabled"}
              >${escapeHtml(baseActionLabel)}</button>
            </td>
            <td>
              <code>${escapeHtml(agentStatus.image_ref || "-")}</code>
            </td>
            <td title="${escapeHtml(agentMeta)}">
              ${imageAssetStatusPill(agentStatus, agentBusy ? "下载中" : "")}
            </td>
            <td>
              <button
                class="${agentButtonClass}"
                type="button"
                ${agentButtonAttrs}
                title="${escapeHtml(agentDisabledTitle)}"
                ${agentButtonEnabled ? "" : "disabled"}
              >${escapeHtml(agentActionLabel)}</button>
            </td>
          </tr>
        `;
      }).join("");
    }

    async function loadStage2ImageAssets({ silent = false } = {}) {
      state.stage2ImageAssetsLoading = true;
      if (!silent) {
        setAssetImageMessage("加载中...");
      }
      try {
        state.stage2ImageAssets = await api("/api/assets/stage2/images");
        renderStage2ImageAssets();
        if (
          state.selectedStage2ImageCatalogAssetKey
          && !findStage2ImageAssetRow(state.selectedStage2ImageCatalogAssetKey)
        ) {
          closeStage2ImageAssetDetail();
        }
        if (!silent) {
          setAssetImageMessage("");
        }
      } catch (error) {
        if ($("#asset-stage2-image-body") && !silent) {
          $("#asset-stage2-image-body").innerHTML = `<tr><td colspan="6" class="muted">加载失败：${escapeHtml(error.message || String(error))}</td></tr>`;
        }
        if (!silent) {
          setAssetImageMessage(`加载失败：${error.message || String(error)}`);
        }
      } finally {
        state.stage2ImageAssetsLoading = false;
      }
    }

    function renderStage2ImageAssetDetailTarget({
      assetKey,
      target,
      title,
      imageRef,
      status,
      actionLabel,
      actionEnabled,
      actionForce,
      actionTitle,
    }) {
      const metaItems = [
        { label: "当前状态", value: imageAssetStatusLabel(status) },
        { label: "开始于", value: status?.last_job_started_at ? formatDate(status.last_job_started_at) : "-" },
        { label: "结束于", value: status?.last_job_finished_at ? formatDate(status.last_job_finished_at) : "-" },
      ];
      const logText = String(status?.job_log_text || status?.job_log_tail || "").trim();
      const logDisplay = logText || "暂无输出";
      const errorText = String(status?.last_job_error || "").trim();
      const inProgress = Boolean(status?.in_progress);
      const cancelInFlight = Boolean(state.stage2ImageAssetCancelInFlight[String(assetKey)]);
      const buttonAttrs = inProgress
        ? `data-stage2-image-detail-cancel-id="${escapeHtml(assetKey)}" data-stage2-image-detail-cancel-kind="${escapeHtml(target)}"`
        : `data-stage2-image-detail-build-kind="${escapeHtml(target)}" data-stage2-image-detail-build-id="${escapeHtml(assetKey)}" data-stage2-image-detail-force="${actionForce ? "1" : "0"}"`;
      const buttonEnabled = inProgress ? !cancelInFlight : actionEnabled;
      const buttonClass = inProgress ? "danger tiny" : "secondary tiny";
      const buttonLabel = inProgress ? "取消" : actionLabel;
      return `
        <section class="asset-image-detail-block">
          <div class="asset-image-detail-head">
            <div class="asset-image-detail-head-main">
              <h4>${escapeHtml(title)}</h4>
              <code>${escapeHtml(imageRef || "-")}</code>
            </div>
            <div class="asset-image-detail-actions">
              ${imageAssetStatusPill(status)}
              <button
                class="${buttonClass}"
                type="button"
                ${buttonAttrs}
                title="${escapeHtml(actionTitle || "")}"
                ${buttonEnabled ? "" : "disabled"}
              >${escapeHtml(buttonLabel)}</button>
            </div>
          </div>
          <div class="asset-image-detail-meta">
            ${metaItems.map((item) => `
              <div class="asset-image-detail-meta-item">
                <span>${escapeHtml(item.label)}</span>
                <strong>${escapeHtml(item.value)}</strong>
              </div>
            `).join("")}
          </div>
          ${errorText ? `<div class="asset-image-detail-error">${escapeHtml(errorText)}</div>` : ""}
          <div class="asset-image-detail-log-shell">
            <div class="asset-image-detail-log-title">实时输出</div>
            <pre class="asset-image-detail-log${logText ? "" : " empty"}" data-stage2-image-detail-log-target="${escapeHtml(target)}">${escapeHtml(logDisplay)}</pre>
          </div>
        </section>
      `;
    }

    function renderStage2ImageAssetDetail() {
      updateStage2ImageAssetLayout();
      const title = $("#asset-stage2-image-detail-title");
      const subtitle = $("#asset-stage2-image-detail-subtitle");
      const body = $("#asset-stage2-image-detail-body");
      if (!title || !subtitle || !body) {
        return;
      }
      if (!state.selectedStage2ImageCatalogAssetKey) {
        title.textContent = "base image 资产详细";
        subtitle.textContent = "选择一个 base image 资产，查看后台下载进度与实时输出";
        body.innerHTML = `<div class="muted">请选择一个 base image 资产。</div>`;
        return;
      }
      if (!state.stage2ImageAssetDetail) {
        title.textContent = "base image 资产详细";
        subtitle.textContent = state.stage2ImageAssetDetailLoading ? "加载中..." : "暂无详细信息";
        body.innerHTML = `<div class="muted">${state.stage2ImageAssetDetailLoading ? "加载中..." : "暂无详细信息。"}</div>`;
        return;
      }
      const row = state.stage2ImageAssetDetail.row || {};
      const baseStatus = row.base_image || {};
      const agentStatus = row.agent_server_image || {};
      const rowJob = row.build_job || {};
      const rowBusy = Boolean(rowJob.in_progress);
      const assetKey = String(row.asset_key || row.image_id || state.selectedStage2ImageCatalogAssetKey || "");
      const baseActionLabel = !baseStatus.present ? "下载" : (baseStatus.needs_update ? "更新" : "已下载");
      const baseActionEnabled = !rowBusy && (!baseStatus.present || baseStatus.needs_update);
      const agentActionLabel = !agentStatus.present ? "下载" : "已下载";
      const agentActionEnabled = !rowBusy && !agentStatus.present && baseStatus.present && !baseStatus.needs_update;
      const sharedBusyTitle = rowBusy ? "该 base image 资产已有后台任务正在执行" : "";
      captureStage2ImageAssetLogScrollState();
      title.textContent = row.image_ref || "base image 资产详细";
      subtitle.textContent = "";
      body.innerHTML = `
        <div class="asset-image-detail-grid">
          ${renderStage2ImageAssetDetailTarget({
            assetKey,
            target: "base",
            title: "Base Image 下载任务",
            imageRef: row.image_ref || "-",
            status: baseStatus,
            actionLabel: baseActionLabel,
            actionEnabled: baseActionEnabled,
            actionForce: Boolean(baseStatus.needs_update),
            actionTitle: sharedBusyTitle,
          })}
          ${renderStage2ImageAssetDetailTarget({
            assetKey,
            target: "agent",
            title: "OpenHands 包装镜像下载任务",
            imageRef: agentStatus.image_ref || "-",
            status: agentStatus,
            actionLabel: agentActionLabel,
            actionEnabled: agentActionEnabled,
            actionForce: false,
            actionTitle: agentActionEnabled ? "" : (sharedBusyTitle || "需要先准备好 Base Image"),
          })}
        </div>
      `;
      restoreStage2ImageAssetLogScrollState();
    }

    async function loadStage2ImageAssetDetail({ silent = false } = {}) {
      const assetKey = String(state.selectedStage2ImageCatalogAssetKey || "").trim();
      const body = $("#asset-stage2-image-detail-body");
      if (!assetKey) {
        return;
      }
      state.stage2ImageAssetDetailLoading = true;
      if (!silent) {
        setAssetImageMessage("正在加载 base image 资产详细...");
        renderStage2ImageAssetDetail();
      }
      try {
        state.stage2ImageAssetDetail = await api(`/api/assets/stage2/images/detail?asset_key=${encodeURIComponent(assetKey)}`);
        renderStage2ImageAssetDetail();
        if (!silent) {
          setAssetImageMessage("");
        }
      } catch (error) {
        state.stage2ImageAssetDetail = null;
        renderStage2ImageAssetDetail();
        if (body) {
          body.innerHTML = `<div class="muted">加载失败：${escapeHtml(error.message || String(error))}</div>`;
        }
        if (!silent) {
          setAssetImageMessage(`加载失败：${error.message || String(error)}`);
        }
      } finally {
        state.stage2ImageAssetDetailLoading = false;
      }
    }

    async function openStage2ImageAssetDetail(assetKey) {
      state.selectedStage2ImageCatalogAssetKey = String(assetKey || "").trim() || null;
      state.stage2ImageAssetDetail = null;
      state.stage2ImageAssetLogScrollState = {};
      renderStage2ImageAssetDetail();
      if (!state.selectedStage2ImageCatalogAssetKey) {
        return;
      }
      await loadStage2ImageAssetDetail();
    }

    function closeStage2ImageAssetDetail() {
      state.selectedStage2ImageCatalogAssetKey = null;
      state.stage2ImageAssetDetail = null;
      state.stage2ImageAssetDetailLoading = false;
      state.stage2ImageAssetLogScrollState = {};
      setAssetImageMessage("");
      renderStage2ImageAssetDetail();
    }

    async function buildStage2ImageAsset(assetKey, { kind, force = false } = {}) {
      const normalizedAssetKey = String(assetKey || "").trim();
      const normalizedKind = kind === "agent" ? "agent" : "base";
      if (!normalizedAssetKey) {
        setAssetImageMessage("缺少 base image 资产标识。");
        return;
      }
      try {
        const payload = await api("/api/assets/stage2/images/build", {
          method: "POST",
          body: JSON.stringify({
            image_ids: [normalizedAssetKey],
            include_base_image: normalizedKind === "base",
            include_agent_server_image: normalizedKind === "agent",
            force,
          }),
        });
        state.stage2ImageAssets = payload.status || null;
        renderStage2ImageAssets();
        if (state.selectedStage2ImageCatalogAssetKey === normalizedAssetKey) {
          await loadStage2ImageAssetDetail({ silent: true });
        }
        const startedEntries = Array.isArray(payload.started) ? payload.started : [];
        const didStart = startedEntries.some((entry) => entry?.asset_key === normalizedAssetKey && entry?.started);
        if (didStart) {
          setAssetImageMessage(
            normalizedKind === "base"
              ? (force ? "Base Image 后台更新任务已提交" : "Base Image 后台下载任务已提交")
              : "OpenHands 包装镜像后台下载任务已提交"
          );
        } else {
          setAssetImageMessage(`${normalizedKind === "base" ? "Base Image" : "OpenHands 包装镜像"}后台任务已在执行`);
        }
        setRefreshMeta();
      } catch (error) {
        setAssetImageMessage(`${normalizedKind === "base" ? "Base Image" : "OpenHands 包装镜像"}操作失败：${error.message || String(error)}`);
      }
    }

    async function cancelStage2ImageAsset(assetKey, { kind } = {}) {
      const normalizedAssetKey = String(assetKey || "").trim();
      const normalizedKind = kind === "agent" ? "OpenHands 包装镜像" : "Base Image";
      if (!normalizedAssetKey) {
        setAssetImageMessage("缺少 base image 资产标识。");
        return;
      }
      state.stage2ImageAssetCancelInFlight = {
        ...state.stage2ImageAssetCancelInFlight,
        [normalizedAssetKey]: true,
      };
      renderStage2ImageAssets();
      renderStage2ImageAssetDetail();
      try {
        const payload = await api("/api/assets/stage2/images/cancel", {
          method: "POST",
          body: JSON.stringify({ asset_key: normalizedAssetKey }),
        });
        state.stage2ImageAssets = payload.status || null;
        renderStage2ImageAssets();
        if (state.selectedStage2ImageCatalogAssetKey === normalizedAssetKey) {
          await loadStage2ImageAssetDetail({ silent: true });
        }
        setAssetImageMessage(payload.cancelled ? `${normalizedKind} 取消请求已发送` : `${normalizedKind} 当前没有可取消的后台任务`);
        setRefreshMeta();
      } catch (error) {
        setAssetImageMessage(`${normalizedKind}取消失败：${error.message || String(error)}`);
      } finally {
        state.stage2ImageAssetCancelInFlight = {
          ...state.stage2ImageAssetCancelInFlight,
          [normalizedAssetKey]: false,
        };
        renderStage2ImageAssets();
        renderStage2ImageAssetDetail();
      }
    }

    function renderManagedImages() {
      const body = $("#asset-managed-image-body");
      const total = $("#asset-managed-image-total");
      if (!body) {
        return;
      }
      const payload = state.managedImages;
      if (!payload) {
        body.innerHTML = `<tr><td colspan="7" class="muted">加载中...</td></tr>`;
        if (total) {
          total.textContent = "共计 -";
        }
        updateManagedImageSortButtons();
        updateManagedImageSelectionControls([]);
        return;
      }
      const allRows = Array.isArray(payload.rows) ? payload.rows : [];
      const rows = currentManagedImageRows();
      if (total) {
        const filterSuffix = managedImageFilterActive()
          ? ` · 当前 ${rows.length} / ${allRows.length} 个`
          : "";
        total.textContent = `共计 ${formatBytes(payload.total_size_bytes)}${filterSuffix}`;
      }
      if (!rows.length) {
        const emptyText = managedImageFilterActive()
          ? "当前筛选下没有 FeatureFactory 镜像。"
          : "暂无 FeatureFactory 镜像。";
        body.innerHTML = `<tr><td colspan="7" class="muted">${emptyText}</td></tr>`;
        updateManagedImageSortButtons();
        updateManagedImageSelectionControls(rows);
        return;
      }
      body.innerHTML = rows.map((row) => {
        const imageRef = String(row.image_ref || "");
        const deleteJob = row.delete_job || {};
        const deleting = Boolean(deleteJob.in_progress || state.managedImageDeleteInFlight[imageRef]);
        const selected = Boolean(state.managedImageSelectedRefs[imageRef]);
        const deleteTitle = deleteJob.last_status === "failed" && deleteJob.last_error
          ? deleteJob.last_error
          : "";
        return `
          <tr>
            <td>
              <input
                type="checkbox"
                data-managed-image-select-ref="${escapeHtml(imageRef)}"
                aria-label="选择镜像 ${escapeHtml(imageRef)}"
                ${selected ? "checked" : ""}
                ${deleting ? "disabled" : ""}
              >
            </td>
            <td><code>${escapeHtml(imageRef || "-")}</code></td>
            <td>${escapeHtml(formatDate(row.last_used_at))}</td>
            <td>${escapeHtml(formatDate(row.created_at))}</td>
            <td>${escapeHtml(row.size || "-")}</td>
            <td title="${escapeHtml(deleteTitle)}">${managedImageDeleteJobPill(deleteJob)}</td>
            <td>
              <button
                class="secondary tiny"
                type="button"
                data-managed-image-delete-ref="${escapeHtml(imageRef)}"
                ${deleting ? "disabled" : ""}
              >${deleting ? "删除中" : "删除"}</button>
            </td>
          </tr>
        `;
      }).join("");
      updateManagedImageSortButtons();
      updateManagedImageSelectionControls(rows);
    }

    async function loadManagedImages({ silent = false } = {}) {
      state.managedImagesLoading = true;
      if (!silent) {
        setManagedImageMessage("加载中...");
      }
      try {
        state.managedImages = await api("/api/assets/images");
        renderManagedImages();
        if (!silent) {
          setManagedImageMessage("");
        }
      } catch (error) {
        if ($("#asset-managed-image-body") && !silent) {
          $("#asset-managed-image-body").innerHTML = `<tr><td colspan="7" class="muted">加载失败：${escapeHtml(error.message || String(error))}</td></tr>`;
        }
        if (!silent) {
          setManagedImageMessage(`加载失败：${error.message || String(error)}`);
        }
      } finally {
        state.managedImagesLoading = false;
      }
    }

    async function deleteManagedImage(imageRef) {
      const normalizedRef = String(imageRef || "").trim();
      if (!normalizedRef) {
        setManagedImageMessage("缺少镜像标识。");
        return;
      }
      state.managedImageDeleteInFlight[normalizedRef] = true;
      renderManagedImages();
      try {
        const payload = await api("/api/assets/images/delete", {
          method: "POST",
          body: JSON.stringify({ image_ref: normalizedRef }),
        });
        state.managedImages = payload.status || null;
        renderManagedImages();
        setManagedImageMessage(payload.started ? "删除任务已提交" : "删除任务已在执行");
        setRefreshMeta();
      } catch (error) {
        setManagedImageMessage(`删除失败：${error.message || String(error)}`);
      } finally {
        delete state.managedImageDeleteInFlight[normalizedRef];
        await loadManagedImages({ silent: true });
      }
    }

    async function deleteSelectedManagedImages() {
      const selectedRefs = managedImageSelectedRefs();
      if (!selectedRefs.length) {
        setManagedImageMessage("请先选择要删除的镜像。");
        return;
      }
      if (!window.confirm(`确认删除选中的 ${selectedRefs.length} 个镜像吗？`)) {
        return;
      }
      selectedRefs.forEach((imageRef) => {
        state.managedImageDeleteInFlight[imageRef] = true;
      });
      renderManagedImages();
      try {
        const payload = await api("/api/assets/images/delete-selected", {
          method: "POST",
          body: JSON.stringify({ image_refs: selectedRefs }),
        });
        selectedRefs.forEach((imageRef) => {
          delete state.managedImageSelectedRefs[imageRef];
        });
        state.managedImages = payload.status || null;
        renderManagedImages();
        const started = Number(payload.started || 0);
        const alreadyRunning = Number(payload.already_running || 0);
        const parts = [];
        if (started) parts.push(`${started} 个删除任务已提交`);
        if (alreadyRunning) parts.push(`${alreadyRunning} 个已在删除中`);
        setManagedImageMessage(parts.join("，") || "删除任务已提交");
        setRefreshMeta();
      } catch (error) {
        setManagedImageMessage(`删除失败：${error.message || String(error)}`);
      } finally {
        selectedRefs.forEach((imageRef) => {
          delete state.managedImageDeleteInFlight[imageRef];
        });
        await loadManagedImages({ silent: true });
      }
    }

    async function refreshAssetImagesIfNeeded() {
      if (
        state.activeTab !== "assets"
        || state.stage2ImageAssetsLoading
        || assetImageRefreshInFlight
      ) {
        return;
      }
      assetImageRefreshInFlight = true;
      try {
        await loadStage2ImageAssets({ silent: true });
      } finally {
        assetImageRefreshInFlight = false;
      }
    }

    async function refreshManagedImagesIfNeeded() {
      if (
        state.activeTab !== "assets"
        || state.managedImagesLoading
        || managedImageRefreshInFlight
        || hasActiveTextSelectionWithin("#asset-managed-image-body")
      ) {
        return;
      }
      managedImageRefreshInFlight = true;
      try {
        await loadManagedImages({ silent: true });
      } finally {
        managedImageRefreshInFlight = false;
      }
    }

    async function refreshAssetImageDetailIfNeeded() {
      if (
        state.activeTab !== "assets"
        || !state.selectedStage2ImageCatalogAssetKey
        || state.stage2ImageAssetDetailLoading
        || hasActiveTextSelectionWithin("#asset-stage2-image-detail-body")
      ) {
        return;
      }
      await loadStage2ImageAssetDetail({ silent: true });
    }

    async function refreshSdkPrewarmIfNeeded() {
      if (
        state.activeTab !== "assets"
        || state.stage2SdkPrewarmLoading
        || state.stage2SdkPrewarmBuilding
        || sdkPrewarmRefreshInFlight
        || hasActiveTextSelectionWithin("#asset-stage2-sdk-body")
      ) {
        return;
      }
      sdkPrewarmRefreshInFlight = true;
      try {
        await loadStage2SdkPrewarm({ silent: true });
      } finally {
        sdkPrewarmRefreshInFlight = false;
      }
    }

    async function refreshStage3RepositoryImagePrewarmIfNeeded() {
      if (
        state.activeTab !== "stage3"
        || !state.selectedStage3RepositoryId
        || !state.stage3List.detailVisible
        || state.stage3List.entryDetailVisible
        || state.stage3RepositoryImagePrewarmLoading
        || hasActiveTextSelectionWithin(".stage3-image-prewarm-section")
      ) {
        return;
      }
      try {
        await loadStage3RepositoryImagePrewarm(state.selectedStage3RepositoryId, { silent: true });
      } catch (error) {
        console.error("Failed to refresh Stage3 image prewarm state", error);
      }
    }

    function stage2LatestAttemptIndex(run) {
      const summaryAttemptIndex = Number(run?.summary?.latest_attempt_index);
      if (Number.isInteger(summaryAttemptIndex) && summaryAttemptIndex > 0) {
        return summaryAttemptIndex;
      }
      const attempts = Array.isArray(run?.validation_attempts) ? run.validation_attempts : [];
      if (attempts.length === 0) {
        return 0;
      }
      return attempts.reduce((maxValue, attempt) => {
        const attemptIndex = Number(attempt?.attempt_index);
        return Number.isInteger(attemptIndex) && attemptIndex > maxValue ? attemptIndex : maxValue;
      }, 0);
    }

    function stage2FullValidationProgress(run) {
      const events = Array.isArray(run?.events) ? run.events : [];
      for (let index = events.length - 1; index >= 0; index -= 1) {
        const event = events[index];
        const payload = event?.payload || {};
        const testFileIndex = Number(payload.test_file_index);
        const testFileTotal = Number(payload.test_file_total);
        if (!Number.isInteger(testFileIndex) || testFileIndex <= 0) {
          continue;
        }
        if (
          String(event?.phase || "") !== "validator_full"
          && !String(event?.title || "").startsWith("Host full test file")
        ) {
          continue;
        }
        return {
          index: testFileIndex,
          total: Number.isInteger(testFileTotal) && testFileTotal > 0 ? testFileTotal : null,
          path: String(payload.test_file_path || ""),
        };
      }
      return null;
    }

    function stage2FullValidationProgressLabel(run, { short = false } = {}) {
      const progress = stage2FullValidationProgress(run);
      if (!progress) {
        return "";
      }
      if (progress.total) {
        return short
          ? `${progress.index}/${progress.total}`
          : `第 ${progress.index}/${progress.total} 个文件`;
      }
      return short ? String(progress.index) : `第 ${progress.index} 个文件`;
    }

    function stage2RunningStateLabel(run) {
      const phase = String(run?.phase || "");
      if (phase === "host_planner") {
        return "planner agent 进行中";
      }
      if (phase === "container_worker" || phase === "validator_smoke") {
        return `worker agent 进行中，未执行到 attempt${stage2LatestAttemptIndex(run) + 1}`;
      }
      if (phase === "validator_full") {
        const progressLabel = stage2FullValidationProgressLabel(run);
        return progressLabel
          ? `full validation 进行中（${progressLabel}）`
          : "full validation 进行中";
      }
      if (phase === "cleanup") {
        return "收尾中";
      }
      return "运行中";
    }

    function stage2StateLabel(run) {
      const status = String(run?.status || "");
      if (status === "queued") {
        return stage2StatusLabel("queued");
      }
      if (status === "running") {
        return stage2RunningStateLabel(run);
      }
      return stage2StatusLabel(run?.display_result || run?.result || "-");
    }

    function stage2TokenDisplayText(run) {
      const aggregated = run?.token_usage_by_model;
      if (aggregated && typeof aggregated === "object" && !Array.isArray(aggregated)) {
        const entries = Object.entries(aggregated)
          .map(([model, tokens]) => [String(model || "unknown"), Number(tokens || 0)])
          .filter(([, tokens]) => Number.isFinite(tokens) && tokens > 0);
        if (entries.length > 0) {
          return entries
            .sort((left, right) => {
              if (right[1] !== left[1]) {
                return right[1] - left[1];
              }
              return left[0].localeCompare(right[0]);
            })
            .map(([model, tokens]) => `${model}: ${Math.round(tokens)}`)
            .join("\\n");
        }
      }
      const runtime = run?.runtime_snapshot || {};
      const entries = [
        {
          model: String(run?.planner_model || runtime?.planner?.model || "").trim(),
          tokens: Number(run?.planner_token_usage ?? 0),
        },
        {
          model: String(run?.worker_model || runtime?.worker?.model || "").trim(),
          tokens: Number(run?.worker_token_usage ?? 0),
        },
      ];
      const usageByModel = new Map();
      entries.forEach(({ model, tokens }) => {
        const normalizedTokens = Number.isFinite(tokens) ? Math.max(0, Math.round(tokens)) : 0;
        if (!model && normalizedTokens <= 0) {
          return;
        }
        const key = model || "unknown";
        usageByModel.set(key, (usageByModel.get(key) || 0) + normalizedTokens);
      });
      if (usageByModel.size === 0) {
        return "-";
      }
      return Array.from(usageByModel.entries())
        .map(([model, tokens]) => `${model}: ${tokens}`)
        .join("\\n");
    }

    function stage3TokenDisplayText(run) {
      const aggregated = run?.token_usage_by_model;
      if (!aggregated || typeof aggregated !== "object" || Array.isArray(aggregated)) {
        return "-";
      }
      const entries = Object.entries(aggregated)
        .map(([model, tokens]) => [String(model || "unknown"), Number(tokens || 0)])
        .filter(([, tokens]) => Number.isFinite(tokens) && tokens > 0);
      if (entries.length === 0) {
        return "-";
      }
      return entries
        .sort((left, right) => {
          if (right[1] !== left[1]) {
            return right[1] - left[1];
          }
          return left[0].localeCompare(right[0]);
        })
        .map(([model, tokens]) => `${model}: ${Math.round(tokens)}`)
        .join("\\n");
    }

    function stage2PhaseLabel(phase) {
      const labels = {
        host_planner: "planner",
        workspace: "planner",
        container_worker: "worker",
        breaker_agent: "breaker",
        validator_smoke: "validator_smoke",
        validator_full: "validator_full",
        cleanup: "cleanup",
      };
      return labels[phase] || phase || "-";
    }

    function summarizeStage2Statuses(values) {
      if (!values || values.length === 0) {
        return "不限";
      }
      if (values.length <= 2) {
        return values.map((value) => stage3RepositoryStatusLabel(value)).join("、");
      }
      return `已选 ${values.length} 个状态`;
    }

    function summarizeStage3Statuses(values) {
      if (!values || values.length === 0) {
        return "不限";
      }
      if (values.length <= 2) {
        return values.map((value) => stage3RepositoryStatusLabel(value)).join("、");
      }
      return `已选 ${values.length} 个状态`;
    }

    function summarizeStage3EntryStatuses(values) {
      if (!values || values.length === 0) {
        return "不限";
      }
      if (values.length <= 2) {
        return values.map((value) => stage3EntryFileStatusLabel(value)).join("、");
      }
      return `已选 ${values.length} 个状态`;
    }

    function getActiveStage3EntryFilterCount() {
      const filters = state.stage3EntryList.filters;
      return Object.values(filters).filter((value) => {
        if (Array.isArray(value)) {
          return value.length > 0;
        }
        return String(value || "").trim() !== "";
      }).length;
    }

    function isStage3EntryNonPendingFilterActive() {
      const statuses = state.stage3EntryList.filters.statuses || [];
      if (statuses.length !== STAGE3_NON_PENDING_STATUSES.length) {
        return false;
      }
      const selected = new Set(statuses);
      return STAGE3_NON_PENDING_STATUSES.every((status) => selected.has(status));
    }

    function updateStage3EntryFilterToggle() {
      const toggle = $("#stage3-entry-filter-toggle");
      const nonPendingToggle = $("#stage3-entry-filter-non-pending");
      const count = getActiveStage3EntryFilterCount();
      const nonPendingActive = isStage3EntryNonPendingFilterActive();
      if (toggle) {
        toggle.textContent = count > 0 ? `筛选（${count}）` : "筛选";
        toggle.classList.toggle("active", count > 0);
      }
      if (nonPendingToggle) {
        nonPendingToggle.classList.toggle("active", nonPendingActive);
        nonPendingToggle.setAttribute("aria-pressed", String(nonPendingActive));
      }
    }

    function syncStage3EntryFilterStatusInputs() {
      const selected = new Set(state.stage3EntryList.filters.statuses || []);
      document.querySelectorAll("[data-stage3-entry-filter-status]").forEach((input) => {
        input.checked = selected.has(input.value);
      });
    }

    function updateStage3EntryFilterStatusToggle(values = state.stage3EntryList.filters.statuses) {
      const summary = $("#stage3-entry-filter-status-summary");
      const toggle = $("#stage3-entry-filter-status-toggle");
      const popover = $("#stage3-entry-filter-status-popover");
      if (summary) {
        summary.textContent = summarizeStage3EntryStatuses(values || []);
      }
      if (toggle && popover) {
        toggle.setAttribute("aria-expanded", String(!popover.hidden));
      }
    }

    function readSelectedStage3EntryFilterStatuses() {
      return Array.from(document.querySelectorAll("[data-stage3-entry-filter-status]"))
        .filter((input) => input.checked)
        .map((input) => input.value);
    }

    function closeStage3EntryFilterStatusPopover({ flush = true } = {}) {
      const popover = $("#stage3-entry-filter-status-popover");
      if (!popover) {
        return;
      }
      popover.hidden = true;
      updateStage3EntryFilterStatusToggle(readSelectedStage3EntryFilterStatuses());
      if (flush) {
        flushPendingLiveDetailPayloads();
      }
    }

    function syncStage3EntryFilterForm() {
      const filters = state.stage3EntryList.filters;
      const testsMin = $("#stage3-entry-filter-original-tests-min");
      const testsMax = $("#stage3-entry-filter-original-tests-max");
      const passRateMin = $("#stage3-entry-filter-original-pass-rate-min");
      const passRateMax = $("#stage3-entry-filter-original-pass-rate-max");
      const dataCountMin = $("#stage3-entry-filter-data-count-min");
      const dataCountMax = $("#stage3-entry-filter-data-count-max");
      if (testsMin) testsMin.value = filters.originalTestsMin;
      if (testsMax) testsMax.value = filters.originalTestsMax;
      if (passRateMin) passRateMin.value = filters.originalPassRateMin;
      if (passRateMax) passRateMax.value = filters.originalPassRateMax;
      if (dataCountMin) dataCountMin.value = filters.dataCountMin;
      if (dataCountMax) dataCountMax.value = filters.dataCountMax;
      syncStage3EntryFilterStatusInputs();
      updateStage3EntryFilterStatusToggle();
    }

    function readStage3EntryFilterForm() {
      return {
        statuses: readSelectedStage3EntryFilterStatuses(),
        originalTestsMin: $("#stage3-entry-filter-original-tests-min")?.value.trim() || "",
        originalTestsMax: $("#stage3-entry-filter-original-tests-max")?.value.trim() || "",
        originalPassRateMin: $("#stage3-entry-filter-original-pass-rate-min")?.value.trim() || "",
        originalPassRateMax: $("#stage3-entry-filter-original-pass-rate-max")?.value.trim() || "",
        dataCountMin: $("#stage3-entry-filter-data-count-min")?.value.trim() || "",
        dataCountMax: $("#stage3-entry-filter-data-count-max")?.value.trim() || "",
      };
    }

    function closeStage3EntryFilterPopover() {
      const popover = $("#stage3-entry-filter-popover");
      if (popover) {
        popover.hidden = true;
      }
      closeStage3EntryFilterStatusPopover({ flush: false });
      const toggle = $("#stage3-entry-filter-toggle");
      if (toggle) {
        toggle.setAttribute("aria-expanded", "false");
      }
      flushPendingLiveDetailPayloads();
    }

    function isStage3EntryFilterInteractionActive() {
      const popover = $("#stage3-entry-filter-popover");
      const statusPopover = $("#stage3-entry-filter-status-popover");
      return Boolean(
        (popover && !popover.hidden)
        || (statusPopover && !statusPopover.hidden)
      );
    }

    function stage3EntrySortDefaultOrder(field) {
      return field === "test_file_path" || field === "status" ? "asc" : "desc";
    }

    function stage3EntryStatusSortValue(status) {
      const order = {
        pending: 0,
        queued: 1,
        running: 2,
        failed: 3,
        interrupted: 4,
        succeeded: 5,
      };
      return order[String(status || "pending")] ?? 99;
    }

    function stage3EntryNumericFilterValue(value) {
      if (value == null) {
        return null;
      }
      if (typeof value === "string" && value.trim() === "") {
        return null;
      }
      const number = Number(value);
      return Number.isFinite(number) ? number : null;
    }

    function stage3EntryMatchesFilters(entryFile) {
      const filters = state.stage3EntryList.filters;
      const statuses = Array.isArray(filters.statuses) ? filters.statuses : [];
      if (statuses.length > 0 && !statuses.includes(String(entryFile.status || "pending"))) {
        return false;
      }
      const originalTests = Number(entryFile.baseline_total_tests || 0);
      const originalPassRate = Number(entryFile.baseline_pass_rate || 0) * 100;
      const dataCount = Number(entryFile.savepoint_count || 0);
      const originalTestsMin = stage3EntryNumericFilterValue(filters.originalTestsMin);
      const originalTestsMax = stage3EntryNumericFilterValue(filters.originalTestsMax);
      const originalPassRateMin = stage3EntryNumericFilterValue(filters.originalPassRateMin);
      const originalPassRateMax = stage3EntryNumericFilterValue(filters.originalPassRateMax);
      const dataCountMin = stage3EntryNumericFilterValue(filters.dataCountMin);
      const dataCountMax = stage3EntryNumericFilterValue(filters.dataCountMax);
      if (originalTestsMin != null && originalTests < originalTestsMin) {
        return false;
      }
      if (originalTestsMax != null && originalTests > originalTestsMax) {
        return false;
      }
      if (originalPassRateMin != null && originalPassRate < originalPassRateMin) {
        return false;
      }
      if (originalPassRateMax != null && originalPassRate > originalPassRateMax) {
        return false;
      }
      if (dataCountMin != null && dataCount < dataCountMin) {
        return false;
      }
      if (dataCountMax != null && dataCount > dataCountMax) {
        return false;
      }
      return true;
    }

    function compareStage3EntryFiles(left, right) {
      const { sortField, sortOrder } = state.stage3EntryList;
      let comparison = 0;
      if (sortField === "baseline_total_tests") {
        comparison = Number(left?.baseline_total_tests || 0) - Number(right?.baseline_total_tests || 0);
      } else if (sortField === "baseline_pass_rate") {
        comparison = Number(left?.baseline_pass_rate || 0) - Number(right?.baseline_pass_rate || 0);
      } else if (sortField === "savepoint_count") {
        comparison = Number(left?.savepoint_count || 0) - Number(right?.savepoint_count || 0);
      } else if (sortField === "latest_operation_at") {
        comparison = String(left?.latest_operation_at || "").localeCompare(String(right?.latest_operation_at || ""));
      } else if (sortField === "status") {
        comparison = stage3EntryStatusSortValue(left?.status) - stage3EntryStatusSortValue(right?.status);
      } else {
        comparison = String(left?.test_file_path || "").localeCompare(String(right?.test_file_path || ""), undefined, {
          numeric: true,
          sensitivity: "base",
        });
      }
      if (comparison === 0) {
        comparison = String(left?.test_file_path || "").localeCompare(String(right?.test_file_path || ""), undefined, {
          numeric: true,
          sensitivity: "base",
        });
      }
      return sortOrder === "asc" ? comparison : -comparison;
    }

    function visibleStage3EntryFiles(entryFiles) {
      return (Array.isArray(entryFiles) ? entryFiles : [])
        .filter((entryFile) => stage3EntryMatchesFilters(entryFile))
        .sort((left, right) => compareStage3EntryFiles(left, right));
    }

    function renderStage3EntrySortButton(label, field) {
      const active = state.stage3EntryList.sortField === field;
      const arrow = active ? (state.stage3EntryList.sortOrder === "asc" ? " ↑" : " ↓") : "";
      return `
        <button
          class="sort-button${active ? " active" : ""}"
          type="button"
          data-stage3-entry-sort="${escapeHtml(field)}"
        >${escapeHtml(label)}${escapeHtml(arrow)}</button>
      `;
    }

    function renderJsonPre(value, emptyText = "暂无数据") {
      if (!value || (typeof value === "object" && Object.keys(value).length === 0)) {
        return `<div class="stage2-empty">${emptyText}</div>`;
      }
      return `<pre>${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
    }

    function markdownInlineHtml(value) {
      let html = escapeHtml(value || "");
      html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
      html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
      html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+|mailto:[^)\s]+)\)/g, (_, label, href) => (
        `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${label}</a>`
      ));
      return html;
    }

    function renderMarkdownBlock(text, emptyText = "当前没有 markdown 内容。") {
      const raw = String(text || "").trim();
      if (!raw) {
        return `<div class="stage2-empty">${escapeHtml(emptyText)}</div>`;
      }
      const lines = raw.replace(/\r\n/g, "\n").split("\n");
      const blocks = [];
      let paragraph = [];
      let listItems = [];
      let listTag = "ul";
      let inFence = false;
      let fenceLines = [];

      const flushParagraph = () => {
        if (!paragraph.length) return;
        blocks.push(`<p>${markdownInlineHtml(paragraph.join(" "))}</p>`);
        paragraph = [];
      };
      const flushList = () => {
        if (!listItems.length) return;
        blocks.push(`<${listTag}>${listItems.map((item) => `<li>${markdownInlineHtml(item)}</li>`).join("")}</${listTag}>`);
        listItems = [];
        listTag = "ul";
      };

      for (const line of lines) {
        const trimmed = line.trim();
        if (/^(```|~~~)/.test(trimmed)) {
          if (inFence) {
            blocks.push(`<pre><code>${escapeHtml(fenceLines.join("\n"))}</code></pre>`);
            fenceLines = [];
            inFence = false;
          } else {
            flushParagraph();
            flushList();
            inFence = true;
            fenceLines = [];
          }
          continue;
        }
        if (inFence) {
          fenceLines.push(line);
          continue;
        }
        if (!trimmed) {
          if (listItems.length) {
            continue;
          }
          flushParagraph();
          continue;
        }
        const heading = trimmed.match(/^(#{1,6})\s+(.+)$/);
        if (heading) {
          flushParagraph();
          flushList();
          const level = Math.min(6, heading[1].length);
          blocks.push(`<h${level}>${markdownInlineHtml(heading[2])}</h${level}>`);
          continue;
        }
        const unordered = trimmed.match(/^[-*+]\s+(.+)$/);
        if (unordered) {
          flushParagraph();
          if (listTag !== "ul") flushList();
          listTag = "ul";
          listItems.push(unordered[1]);
          continue;
        }
        const ordered = trimmed.match(/^\d+\\?[.)]\s+(.+)$/);
        if (ordered) {
          flushParagraph();
          if (listTag !== "ol") flushList();
          listTag = "ol";
          listItems.push(ordered[1]);
          continue;
        }
        flushList();
        paragraph.push(trimmed);
      }
      if (inFence) {
        blocks.push(`<pre><code>${escapeHtml(fenceLines.join("\n"))}</code></pre>`);
      }
      flushParagraph();
      flushList();
      return `<div class="markdown-rendered">${blocks.join("")}</div>`;
    }

    function batchStatusLabel(status) {
      return {
        pending: "等待中",
        running: "运行中",
        completed: "已完成",
        skipped: "已跳过",
        partial: "部分完成",
        failed: "失败",
        cancelled: "已取消",
        queued: "等待中",
        succeeded: "成功",
        abandoned: "废弃",
        defect: "有缺陷",
        unknown: "未知",
      }[String(status || "")] || String(status || "-");
    }

    function renderStatusPill(status) {
      const normalized = String(status || "unknown");
      return `<span class="status ${escapeHtml(normalized)}">${escapeHtml(batchStatusLabel(normalized))}</span>`;
    }

    function selectedOptions(select) {
      return Array.from(select?.selectedOptions || [])
        .map((option) => String(option.value || "").trim())
        .filter(Boolean);
    }

    function csvValues(value) {
      return String(value || "")
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
    }

    function newlineValues(value) {
      return String(value || "")
        .split(/\r?\n/)
        .map((item) => item.trim())
        .filter(Boolean);
    }

    function crawlTargetRepositories() {
      return newlineValues($("#crawl-form")?.elements?.target_repositories?.value || "");
    }

    function syncCrawlTargetRepositoryMode() {
      const form = $("#crawl-form");
      if (!form) return;
      const active = crawlTargetRepositories().length > 0;
      form.classList.toggle("crawl-target-mode", active);
      form.querySelectorAll("[data-crawl-target-disabled]").forEach((element) => {
        element.setAttribute("aria-disabled", active ? "true" : "false");
      });
      if (form.elements.created_after) {
        form.elements.created_after.required = !active;
      }
    }

    function batchTargetRepositories() {
      return newlineValues($("#batch-form")?.elements?.target_repositories?.value || "");
    }

    function syncBatchTargetRepositoryMode() {
      const form = $("#batch-form");
      if (!form) return;
      const active = batchTargetRepositories().length > 0;
      form.classList.toggle("batch-target-mode", active);
      form.querySelectorAll("[data-batch-target-disabled]").forEach((element) => {
        element.setAttribute("aria-disabled", active ? "true" : "false");
      });
      if (form.elements.created_after) {
        form.elements.created_after.required = !active;
      }
    }

    function readBatchRuntimeOverride(form, prefix, fields) {
      const payload = {};
      for (const field of fields) {
        const key = `${prefix}_${field.name}`;
        const rawValue = String(form.get(key) || "").trim();
        if (!rawValue) continue;
        payload[field.name] = field.type === "number" ? Number(rawValue) : rawValue;
      }
      return Object.keys(payload).length ? payload : null;
    }

    function isBatchRuntimeFieldName(name) {
      const value = String(name || "");
      return /^stage[234]_/.test(value) && !/^stage[234]_template_id$/.test(value);
    }

    function markBatchRuntimeFieldTouched(element) {
      if (!element || !isBatchRuntimeFieldName(element.name)) {
        return;
      }
      element.dataset.batchRuntimeTouched = "1";
    }

    function batchRuntimeField(name) {
      const form = $("#batch-form");
      return form?.elements?.[name] || null;
    }

    function setBatchRuntimeDefault(name, value, { placeholder = null, max = null } = {}) {
      const input = batchRuntimeField(name);
      if (!input) {
        return;
      }
      if (max != null) {
        input.max = String(max);
      }
      const normalized = value == null ? "" : String(value);
      if (input.dataset.batchRuntimeTouched !== "1") {
        input.value = normalized;
      }
      input.dataset.batchRuntimeDefaulted = "1";
      input.dataset.batchRuntimeDefaultValue = normalized;
      if (placeholder != null && input.dataset.batchRuntimeTouched !== "1") {
        input.placeholder = placeholder || "";
      }
    }

    function applyBatchStage2RuntimeDefaults(payload = state.stage2Runtime) {
      if (!payload) return;
      const concurrency = payload.concurrency || {};
      const planner = payload.planner || {};
      const worker = payload.worker || {};
      const hyperparameters = payload.hyperparameters || {};
      const systemCapacity = concurrency.system_capacity ?? 24;
      setBatchRuntimeDefault("stage2_max_concurrent_runs", concurrency.max_concurrent_runs, { max: systemCapacity });
      setBatchRuntimeDefault("stage2_planner_model", planner.model);
      setBatchRuntimeDefault("stage2_planner_base_url", planner.base_url);
      setBatchRuntimeDefault("stage2_planner_api_key", "", { placeholder: planner.api_key_preview || "" });
      setBatchRuntimeDefault("stage2_planner_preset", planner.preset || "default");
      setBatchRuntimeDefault("stage2_planner_max_iterations", planner.max_iterations);
      setBatchRuntimeDefault("stage2_planner_timeout_seconds", planner.timeout_seconds);
      setBatchRuntimeDefault("stage2_worker_model", worker.model);
      setBatchRuntimeDefault("stage2_worker_base_url", worker.base_url);
      setBatchRuntimeDefault("stage2_worker_api_key", "", { placeholder: worker.api_key_preview || "" });
      setBatchRuntimeDefault("stage2_worker_preset", worker.preset || "default");
      setBatchRuntimeDefault("stage2_worker_max_iterations", worker.max_iterations);
      setBatchRuntimeDefault("stage2_worker_timeout_seconds", worker.timeout_seconds);
      setBatchRuntimeDefault("stage2_max_worker_attempts", hyperparameters.max_worker_attempts);
      setBatchRuntimeDefault("stage2_quickcheck_sample_size", hyperparameters.quickcheck_sample_size);
      setBatchRuntimeDefault("stage2_collect_timeout_seconds", hyperparameters.collect_timeout_seconds ?? hyperparameters.command_timeout_seconds);
      setBatchRuntimeDefault("stage2_run_test_timeout_seconds", hyperparameters.run_test_timeout_seconds ?? hyperparameters.command_timeout_seconds);
      setBatchRuntimeDefault("stage2_build_timeout_seconds", hyperparameters.build_timeout_seconds);
      setBatchRuntimeDefault("stage2_full_validation_timeout_seconds", hyperparameters.full_validation_timeout_seconds);
      setBatchRuntimeDefault("stage2_entry_file_test_count_min", hyperparameters.entry_file_test_count_min);
      setBatchRuntimeDefault("stage2_p2p_file_count_limit", hyperparameters.p2p_file_count_limit);
    }

    function applyBatchStage3RuntimeDefaults(payload = state.stage3Runtime) {
      if (!payload) return;
      const concurrency = payload.concurrency || {};
      const breaker = payload.breaker || {};
      const hyperparameters = payload.hyperparameters || {};
      const systemCapacity = concurrency.system_capacity ?? 24;
      setBatchRuntimeDefault("stage3_max_concurrent_runs", concurrency.max_concurrent_runs, { max: systemCapacity });
      setBatchRuntimeDefault("stage3_breaker_model", breaker.model);
      setBatchRuntimeDefault("stage3_breaker_base_url", breaker.base_url);
      setBatchRuntimeDefault("stage3_breaker_api_key", "", { placeholder: breaker.api_key_preview || "" });
      setBatchRuntimeDefault("stage3_breaker_preset", breaker.preset || "default");
      setBatchRuntimeDefault("stage3_breaker_max_iterations", breaker.max_iterations);
      setBatchRuntimeDefault("stage3_breaker_timeout_seconds", breaker.timeout_seconds);
      setBatchRuntimeDefault("stage3_build_timeout_seconds", hyperparameters.build_timeout_seconds);
      setBatchRuntimeDefault("stage3_run_test_timeout_seconds", hyperparameters.run_test_timeout_seconds);
      setBatchRuntimeDefault("stage3_full_validation_timeout_seconds", hyperparameters.full_validation_timeout_seconds);
      setBatchRuntimeDefault("stage3_entry_pass_rate_ceiling", hyperparameters.entry_pass_rate_ceiling);
      setBatchRuntimeDefault(
        "stage3_min_removed_code_lines",
        stage3MinRemovedCodeLinesValue(hyperparameters.min_removed_code_lines),
      );
    }

    function applyBatchStage4RuntimeDefaults(payload = state.stage4Runtime) {
      if (!payload) return;
      const concurrency = payload.concurrency || {};
      const issuer = payload.issuer || {};
      const hyperparameters = payload.hyperparameters || {};
      const systemCapacity = concurrency.system_capacity ?? 24;
      setBatchRuntimeDefault("stage4_max_concurrent_runs", concurrency.max_concurrent_runs, { max: systemCapacity });
      setBatchRuntimeDefault("stage4_issuer_model", issuer.model);
      setBatchRuntimeDefault("stage4_issuer_base_url", issuer.base_url);
      setBatchRuntimeDefault("stage4_issuer_api_key", "", { placeholder: issuer.api_key_preview || "" });
      setBatchRuntimeDefault("stage4_issuer_preset", issuer.preset || "default");
      setBatchRuntimeDefault("stage4_issuer_max_iterations", issuer.max_iterations);
      setBatchRuntimeDefault("stage4_issuer_timeout_seconds", issuer.timeout_seconds);
      setBatchRuntimeDefault("stage4_build_timeout_seconds", hyperparameters.build_timeout_seconds);
    }

    function syncBatchGithubTokenMode() {
      const form = $("#batch-form");
      const select = form?.elements?.stage1_template_id || null;
      const textarea = form?.elements?.github_tokens || null;
      const usesTemplate = Boolean(String(select?.value || "").trim());
      if (!textarea) {
        return;
      }
      textarea.disabled = usesTemplate;
      textarea.closest(".field-group")?.classList.toggle("muted", usesTemplate);
    }

    function syncBatchRuntimeMode(stage) {
      const normalizedStage = String(stage || "");
      if (!["stage2", "stage3", "stage4"].includes(normalizedStage)) {
        return;
      }
      const form = $("#batch-form");
      const select = form?.elements?.[`${normalizedStage}_template_id`] || null;
      const usesTemplate = Boolean(String(select?.value || "").trim());
      const stopsAfterStage2 = String(form?.elements?.stop_after_stage?.value || "stage4") === "stage2";
      const stageDisabled = stopsAfterStage2 && normalizedStage !== "stage2";
      if (select) {
        select.disabled = stageDisabled;
        select.closest("label")?.classList.toggle("muted", stageDisabled);
      }
      document.querySelectorAll(`#batch-form [name^="${normalizedStage}_"]`).forEach((element) => {
        if (element.name === `${normalizedStage}_template_id`) {
          return;
        }
        element.disabled = usesTemplate || stageDisabled;
        element.closest("label")?.classList.toggle("muted", usesTemplate || stageDisabled);
      });
    }

    function syncBatchRuntimeModes() {
      syncBatchGithubTokenMode();
      syncBatchRuntimeMode("stage2");
      syncBatchRuntimeMode("stage3");
      syncBatchRuntimeMode("stage4");
    }

    function updateBatchLayout() {
      const layout = $("#batch-layout");
      const collapseToggle = $("#batch-layout-collapse-toggle");
      const expandToggle = $("#batch-layout-expand-toggle");
      if (!layout || !collapseToggle || !expandToggle) {
        return;
      }
      const collapsed = Boolean(state.batchFormCollapsed);
      layout.classList.toggle("form-collapsed", collapsed);
      collapseToggle.hidden = collapsed;
      collapseToggle.setAttribute("aria-expanded", String(!collapsed));
      collapseToggle.setAttribute("aria-label", "收起创建批量任务面板");
      collapseToggle.title = "收起创建批量任务面板";
      expandToggle.hidden = !collapsed;
      expandToggle.setAttribute("aria-expanded", String(!collapsed));
      expandToggle.setAttribute("aria-label", "展开任务创建");
      expandToggle.title = "展开任务创建";
    }

    function setBatchLayoutCollapsed(collapsed) {
      state.batchFormCollapsed = Boolean(collapsed);
      if (state.batchFormCollapsed) {
        closeBatchLanguagePopover();
        closeBatchLicensePopover();
        closeBatchDataPoolPopover({ restore: true });
      }
      persistBatchLayoutPreference();
      updateBatchLayout();
    }

    function toggleBatchLayout() {
      setBatchLayoutCollapsed(!state.batchFormCollapsed);
      if (state.batchFormCollapsed) {
        $("#batch-layout-expand-toggle")?.focus();
      } else {
        $("#batch-layout-collapse-toggle")?.focus();
      }
    }

    function batchAssetCount(stats) {
      const payload = stats || {};
      return Number(payload.asset_count || 0);
    }

    function renderBatchTasks() {
      const tbody = $("#batch-tasks-body");
      if (!tbody) return;
      const tasks = Array.isArray(state.batchTasks) ? state.batchTasks : [];
      if (!tasks.length) {
        tbody.innerHTML = `<tr><td colspan="6" class="muted">暂无批量任务。</td></tr>`;
        return;
      }
      tbody.innerHTML = tasks.map((task) => {
        const pool = task.data_pool || {};
        const stats = task.stats || {};
        const active = String(state.selectedBatchTaskId || "") === String(task.id || "");
        const actions = [];
        const canRetry = Boolean(task.can_retry);
        const retryTitle = canRetry ? "选择配置来源后原地重新运行这个任务" : "运行中的任务不能重试";
        actions.push(
          `<button class="secondary tiny" type="button" data-batch-retry-id="${escapeHtml(String(task.id || ""))}" title="${escapeHtml(retryTitle)}" ${canRetry ? "" : "disabled"}>重试</button>`,
        );
        if (task.can_cancel) {
          actions.push(
            `<button class="secondary tiny" type="button" data-batch-cancel-id="${escapeHtml(String(task.id || ""))}">取消</button>`,
          );
        }
        if (task.can_delete) {
          actions.push(
            `<button class="secondary tiny danger" type="button" data-batch-delete-id="${escapeHtml(String(task.id || ""))}">删除</button>`,
          );
        }
        return `
          <tr data-batch-task-id="${escapeHtml(String(task.id || ""))}" class="${active ? "active" : ""}">
            <td>
              <strong>${escapeHtml(task.name || "-")}</strong>
            </td>
            <td>${renderStatusPill(task.status)}</td>
            <td>${escapeHtml(pool.name || "-")}</td>
            <td>${batchAssetCount(stats)}</td>
            <td>${formatDate(task.created_at)}</td>
            <td>
              ${actions.join(" ")}
            </td>
          </tr>
        `;
      }).join("");
    }

    async function loadBatchTasks() {
      const payload = await api("/api/batch/tasks");
      state.batchTasks = Array.isArray(payload.tasks) ? payload.tasks : [];
      renderBatchTasks();
      return payload;
    }

    function renderStage1CrawlTemplates() {
      const container = $("#stage1-template-strip");
      if (!container) return;
      const templates = state.stage1CrawlTemplates || [];
      if (!templates.length) {
        container.innerHTML = `<span class="stage2-template-empty">暂无模板</span>`;
        return;
      }
      container.innerHTML = templates.map((template) => {
        const active = String(template.id) === String(state.selectedStage1CrawlTemplateId) ? " active" : "";
        const titleParts = [template.name];
        if (Number(template.github_token_count || 0) > 0) titleParts.push(`${template.github_token_count} 个 token`);
        return `
          <div class="stage2-template-chip-shell${active}" title="${escapeHtml(titleParts.join(" | "))}">
            <button
              class="stage2-template-chip"
              type="button"
              data-stage1-template-id="${escapeHtml(template.id)}"
            >${escapeHtml(template.name || template.id)}</button>
            <button
              class="stage2-template-chip-delete"
              type="button"
              aria-label="删除模板"
              title="删除模板"
              data-stage1-template-delete-id="${escapeHtml(template.id)}"
            >×</button>
          </div>
        `;
      }).join("");
    }

    async function loadStage1CrawlTemplates() {
      try {
        const payload = await api("/api/stage1/templates");
        state.stage1CrawlTemplates = payload.templates || [];
        renderStage1CrawlTemplates();
        renderBatchTemplateOptions();
        return payload;
      } catch (error) {
        $("#form-message").textContent = `模板加载失败: ${error.message}`;
        return null;
      }
    }

    async function applyStage1CrawlTemplate(templateId, target = "crawl") {
      try {
        const payload = await api(`/api/stage1/templates/${encodeURIComponent(templateId)}`);
        if (target === "batch") {
          state.selectedBatchStage1TemplateId = payload.id;
          syncBatchGithubTokenMode();
          $("#batch-form-message").textContent = `已选择 GitHub Token 模板「${payload.name}」`;
          return payload;
        }
        state.selectedStage1CrawlTemplateId = payload.id;
        applyStage1TemplateToCrawlForm(payload.snapshot || {});
        renderStage1CrawlTemplates();
        $("#form-message").textContent = `已应用 GitHub Token 模板「${payload.name}」`;
        return payload;
      } catch (error) {
        const selector = target === "batch" ? "#batch-form-message" : "#form-message";
        $(selector).textContent = `模板载入失败: ${error.message}`;
        return null;
      }
    }

    async function saveStage1CrawlTemplate() {
      const activeTemplate = (state.stage1CrawlTemplates || []).find(
        (template) => String(template.id) === String(state.selectedStage1CrawlTemplateId),
      );
      const name = window.prompt("模板名称", activeTemplate?.name || "");
      if (name == null) return null;
      const normalizedName = String(name).trim();
      if (!normalizedName) {
        $("#form-message").textContent = "模板名称不能为空";
        return null;
      }
      const requestBody = readStage1TemplatePayloadFromCrawlForm(normalizedName);
      if (!requestBody.github_tokens.length) {
        $("#form-message").textContent = "GitHub Tokens 不能为空";
        return null;
      }
      const saveButton = $("#stage1-template-save");
      if (saveButton) saveButton.disabled = true;
      $("#form-message").textContent = "正在保存模板...";
      try {
        const payload = await api("/api/stage1/templates", {
          method: "POST",
          body: JSON.stringify(requestBody),
        });
        state.selectedStage1CrawlTemplateId = payload.id;
        await loadStage1CrawlTemplates();
        $("#form-message").textContent = `已保存模板「${payload.name}」`;
        return payload;
      } catch (error) {
        $("#form-message").textContent = `模板保存失败: ${error.message}`;
        return null;
      } finally {
        if (saveButton) saveButton.disabled = false;
      }
    }

    async function deleteStage1CrawlTemplate(templateId) {
      const normalizedId = String(templateId || "").trim();
      if (!normalizedId) return null;
      $("#form-message").textContent = "正在删除模板...";
      try {
        const payload = await api(`/api/stage1/templates/${encodeURIComponent(normalizedId)}`, {
          method: "DELETE",
        });
        if (String(state.selectedStage1CrawlTemplateId) === normalizedId) {
          state.selectedStage1CrawlTemplateId = null;
        }
        if (String(state.selectedBatchStage1TemplateId) === normalizedId) {
          state.selectedBatchStage1TemplateId = null;
        }
        await loadStage1CrawlTemplates();
        $("#form-message").textContent = `已删除模板「${payload.name}」`;
        return payload;
      } catch (error) {
        $("#form-message").textContent = `模板删除失败: ${error.message}`;
        return null;
      }
    }

    function renderBatchTemplateOptions() {
      const stage1 = $("#batch-stage1-template");
      const stage2 = $("#batch-stage2-template");
      const stage3 = $("#batch-stage3-template");
      const stage4 = $("#batch-stage4-template");
      if (stage1) {
        const current = stage1.value;
        stage1.innerHTML = `<option value="">使用临时配置</option>${(state.stage1CrawlTemplates || []).map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name || item.id)}</option>`).join("")}`;
        if ([...stage1.options].some((option) => option.value === current)) {
          stage1.value = current;
        }
      }
      if (stage2) {
        const current = stage2.value;
        stage2.innerHTML = `<option value="">使用临时配置</option>${(state.stage2RuntimeTemplates || []).map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name || item.id)}</option>`).join("")}`;
        if ([...stage2.options].some((option) => option.value === current)) {
          stage2.value = current;
        }
      }
      if (stage3) {
        const current = stage3.value;
        stage3.innerHTML = `<option value="">使用临时配置</option>${(state.stage3RuntimeTemplates || []).map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name || item.id)}</option>`).join("")}`;
        if ([...stage3.options].some((option) => option.value === current)) {
          stage3.value = current;
        }
      }
      if (stage4) {
        const current = stage4.value;
        stage4.innerHTML = `<option value="">使用临时配置</option>${(state.stage4RuntimeTemplates || []).map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name || item.id)}</option>`).join("")}`;
        if ([...stage4.options].some((option) => option.value === current)) {
          stage4.value = current;
        }
      }
      syncBatchRuntimeModes();
    }

    function closeBatchTaskDetailStream() {
      if (batchTaskDetailEventSource) {
        batchTaskDetailEventSource.close();
      }
      batchTaskDetailEventSource = null;
      batchTaskDetailStreamKey = null;
    }

    function isBatchTaskRealtimeEligible(task) {
      return ["pending", "running"].includes(String(task?.status || "")) || Boolean(task?.is_running);
    }

    function batchTaskDetailParams() {
      const filters = state.batchRepositoryList.filters || defaultBatchRepositoryFilters();
      const params = new URLSearchParams({
        repo_page: String(Math.max(1, Number(state.batchRepositoryList.page || 1))),
        repo_page_size: String(Math.max(1, Number(state.batchRepositoryList.pageSize || 15))),
      });
      if (filters.query) params.set("repo_query", filters.query);
      if (filters.language) params.set("repo_language", filters.language);
      if (filters.starsMin !== "") params.set("repo_stars_min", filters.starsMin);
      if (filters.starsMax !== "") params.set("repo_stars_max", filters.starsMax);
      if (filters.status) params.set("repo_status", filters.status);
      if (filters.stage2Status) params.set("repo_stage2_status", filters.stage2Status);
      if (filters.stage3Status) params.set("repo_stage3_status", filters.stage3Status);
      if (filters.stage4Status) params.set("repo_stage4_status", filters.stage4Status);
      if (filters.hasErrors) params.set("repo_has_errors", "true");
      if (filters.nonPending) params.set("repo_non_pending", "true");
      return params;
    }

    function applyLiveBatchTaskDetailPayload(payload) {
      if (!payload || String(payload.id || "") !== String(state.selectedBatchTaskId || "")) {
        return;
      }
      if (state.activeTab !== "batch") {
        pendingBatchTaskDetailPayload = null;
        return;
      }
      if (state.batchTaskDetailLoading) {
        pendingBatchTaskDetailPayload = payload;
        return;
      }
      if (hasActiveTextSelection() || isBatchRepoFilterInteractionActive()) {
        pendingBatchTaskDetailPayload = payload;
        return;
      }
      pendingBatchTaskDetailPayload = null;
      state.batchTaskDetailPayload = payload;
      renderBatchTaskDetail();
    }

    function syncBatchTaskDetailStream() {
      const selected = state.batchTaskDetailPayload;
      if (state.activeTab !== "batch" || !selected || !isBatchTaskRealtimeEligible(selected)) {
        closeBatchTaskDetailStream();
        return;
      }
      const taskId = String(selected.id || "");
      if (!taskId) {
        closeBatchTaskDetailStream();
        return;
      }
      const query = batchTaskDetailParams().toString();
      const streamKey = `${taskId}?${query}`;
      if (batchTaskDetailEventSource && batchTaskDetailStreamKey === streamKey) {
        return;
      }
      closeBatchTaskDetailStream();
      const source = new EventSource(`/api/batch/tasks/${encodeURIComponent(taskId)}/events?${query}`);
      batchTaskDetailEventSource = source;
      batchTaskDetailStreamKey = streamKey;
      const isCurrentStream = () => (
        batchTaskDetailEventSource === source
        && batchTaskDetailStreamKey === streamKey
      );
      source.addEventListener("snapshot", (event) => {
        if (!isCurrentStream()) {
          return;
        }
        applyLiveBatchTaskDetailPayload(JSON.parse(event.data));
        loadBatchTasks().catch(() => {});
      });
      source.addEventListener("terminal", (event) => {
        if (!isCurrentStream()) {
          return;
        }
        applyLiveBatchTaskDetailPayload(JSON.parse(event.data));
        loadBatchTasks().catch(() => {});
        closeBatchTaskDetailStream();
      });
      source.addEventListener("deleted", () => {
        if (isCurrentStream()) {
          closeBatchTaskDetailStream();
        }
      });
      source.addEventListener("error", () => {
        if (isCurrentStream()) {
          closeBatchTaskDetailStream();
        }
      });
    }

    function hideBatchTaskDetail() {
      closeBatchTaskDetailStream();
      pendingBatchTaskDetailPayload = null;
      state.selectedBatchTaskId = null;
      state.batchTaskDetailPayload = null;
      const panel = $("#batch-detail-panel");
      const body = $("#batch-detail-body");
      const message = $("#batch-detail-message");
      const error = $("#batch-detail-error");
      if (panel) panel.hidden = true;
      if (body) body.innerHTML = "";
      if (message) message.textContent = "";
      if (error) {
        error.hidden = true;
        error.textContent = "";
        error.title = "";
      }
      renderBatchTasks();
    }

    function renderBatchStage1Detail(stage1Detail) {
      const job = stage1Detail?.job || {};
      const partitions = Array.isArray(stage1Detail?.partitions) ? stage1Detail.partitions : [];
      const repositories = Array.isArray(stage1Detail?.repositories) ? stage1Detail.repositories : [];
      const planningProgressSection = job.stats?.planning_progress
        ? renderPlanningProgress(job.stats.planning_progress, { card: false })
        : "";
      const partitionRows = partitions.map((partition) => `
        <tr>
          <td>${statusPill(partition.status)}</td>
          <td class="mono">${formatDate(partition.range_start)} → ${formatDate(partition.range_end)}</td>
          <td>${escapeHtml(String(partition.expected_count ?? 0))}</td>
          <td>${escapeHtml(String(partition.unique_count ?? 0))}</td>
          <td>${escapeHtml(String(partition.fetched_count ?? 0))}</td>
          <td>${escapeHtml(String((partition.queries || []).length))}</td>
        </tr>
      `).join("") || `<tr><td colspan="6" class="muted">Stage1 正在准备时间分片。</td></tr>`;
      const repoRows = repositories.slice(0, 12).map((repo) => `
        <tr>
          <td>${escapeHtml(repo.full_name || "-")}</td>
          <td>${escapeHtml(repo.primary_language || "-")}</td>
          <td>${escapeHtml(String(repo.stargazers_count || 0))}</td>
          <td>${formatDate(repo.job_discovered_at)}</td>
        </tr>
      `).join("") || `<tr><td colspan="4" class="muted">当前还没有命中 repo。</td></tr>`;
      return `
        <div class="detail-card detail-section">
          <h4>Stage1 抓取进度</h4>
          <div class="summary-grid">
            <div><span>状态</span><strong>${escapeHtml(batchStatusLabel(job.status))}</strong></div>
            <div><span>唯一 Repo</span><strong>${escapeHtml(String(job.stats?.unique_repositories || job.stats?.unique_count || 0))}</strong></div>
            <div><span>分片</span><strong>${escapeHtml(String(partitions.length))}</strong></div>
            <div><span>Job</span><strong class="mono">${escapeHtml(String(job.id || "-").slice(0, 8))}</strong></div>
          </div>
          ${job.error_message ? `<div class="stage2-error">${escapeHtml(job.error_message)}</div>` : ""}
          ${planningProgressSection}
          <div class="table-wrap stage4-nested-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>状态</th>
                  <th>时间范围</th>
                  <th>预估</th>
                  <th>唯一 Repo</th>
                  <th>Fetched</th>
                  <th>Query</th>
                </tr>
              </thead>
              <tbody>${partitionRows}</tbody>
            </table>
          </div>
          <div class="table-wrap stage4-nested-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Repo</th>
                  <th>语言</th>
                  <th>Stars</th>
                  <th>命中时间</th>
                </tr>
              </thead>
              <tbody>${repoRows}</tbody>
            </table>
          </div>
        </div>
      `;
    }

    function renderBatchStage1BypassDetail(task) {
      const targetRepositories = Array.isArray(task?.filters?.target_repositories)
        ? task.filters.target_repositories.filter((item) => String(item || "").trim())
        : [];
      const targetCount = targetRepositories.length;
      return `
        <div class="detail-card detail-section">
          <div class="batch-stage1-head">
            <h4>Stage1 抓取进度</h4>
          </div>
          <div class="summary-grid">
            <div><span>模式</span><strong>指定仓库</strong></div>
            <div><span>仓库数</span><strong>${escapeHtml(String(targetCount))}</strong></div>
            <div><span>状态</span><strong>已跳过搜索分片</strong></div>
          </div>
          <div class="stage2-empty">当前批量任务使用“抓取指定仓库”模式，Stage1 不会创建 crawl job，也不会产生分片进度表。</div>
        </div>
      `;
    }

    function renderBatchRunningCounts(stats) {
      const items = [
        ["Stage2", stats?.stage2_running_count],
        ["Stage3", stats?.stage3_running_count],
        ["Stage4", stats?.stage4_running_count],
      ];
      return `
        <span class="batch-running-counts" title="当前正在运行的 Stage run 数">
          ${items.map(([label, value]) => `
            <span class="batch-running-count">
              <span>${escapeHtml(label)}</span>
              <strong>${escapeHtml(String(Number(value || 0)))}</strong>
            </span>
          `).join("")}
        </span>
      `;
    }

    function defaultBatchRepositoryFilters() {
      return {
        query: "",
        language: "",
        starsMin: "",
        starsMax: "",
        status: "",
        stage2Status: "",
        stage3Status: "",
        stage4Status: "",
        hasErrors: false,
        nonPending: false,
      };
    }

    function closeBatchRepoFilterPopover({ flush = true } = {}) {
      const popover = $("#batch-repo-filter-popover");
      if (popover) popover.hidden = true;
      if (flush) {
        flushPendingLiveDetailPayloads();
      }
    }

    function isBatchRepoFilterInteractionActive() {
      const popover = $("#batch-repo-filter-popover");
      return Boolean(popover && !popover.hidden);
    }

    function readBatchRepoFiltersFromForm() {
      return {
        query: $("#batch-repo-filter-query")?.value.trim() || "",
        language: $("#batch-repo-filter-language")?.value.trim() || "",
        starsMin: $("#batch-repo-filter-stars-min")?.value.trim() || "",
        starsMax: $("#batch-repo-filter-stars-max")?.value.trim() || "",
        status: $("#batch-repo-filter-status")?.value || "",
        stage2Status: $("#batch-repo-filter-stage2-status")?.value || "",
        stage3Status: $("#batch-repo-filter-stage3-status")?.value || "",
        stage4Status: $("#batch-repo-filter-stage4-status")?.value || "",
        hasErrors: Boolean($("#batch-repo-filter-has-errors")?.checked),
        nonPending: Boolean($("#batch-repo-filter-non-pending")?.checked),
      };
    }

    function syncBatchRepoFilterForm() {
      const filters = state.batchRepositoryList.filters || defaultBatchRepositoryFilters();
      const values = {
        "#batch-repo-filter-query": filters.query,
        "#batch-repo-filter-language": filters.language,
        "#batch-repo-filter-stars-min": filters.starsMin,
        "#batch-repo-filter-stars-max": filters.starsMax,
        "#batch-repo-filter-status": filters.status,
        "#batch-repo-filter-stage2-status": filters.stage2Status,
        "#batch-repo-filter-stage3-status": filters.stage3Status,
        "#batch-repo-filter-stage4-status": filters.stage4Status,
      };
      Object.entries(values).forEach(([selector, value]) => {
        const input = $(selector);
        if (input) input.value = value || "";
      });
      const hasErrors = $("#batch-repo-filter-has-errors");
      if (hasErrors) hasErrors.checked = Boolean(filters.hasErrors);
      const nonPending = $("#batch-repo-filter-non-pending");
      if (nonPending) nonPending.checked = Boolean(filters.nonPending);
      updateBatchRepoFilterToggle();
    }

    function updateBatchRepoFilterToggle() {
      const button = $("#batch-repo-filter-toggle");
      if (!button) return;
      const filters = state.batchRepositoryList.filters || {};
      const count = Object.entries(filters).filter(([, value]) => {
        if (typeof value === "boolean") return value;
        return String(value || "").trim().length > 0;
      }).length;
      button.textContent = count ? `筛选 · ${count}` : "筛选";
      button.classList.toggle("active", count > 0);
    }

    function normalizeBatchProgressStatus(status) {
      const normalized = String(status || "").trim();
      if (normalized === "completed" || normalized === "succeeded") return "completed";
      if (normalized === "skipped") return "skipped";
      if (normalized === "partial") return "partial";
      if (normalized === "abandoned" || normalized === "defect") return normalized;
      if (normalized === "failed" || normalized === "cancelled") return "failed";
      if (normalized === "running") return "running";
      if (normalized === "pending" || normalized === "queued" || normalized === "planning" || normalized === "paused") {
        return "pending";
      }
      return "pending";
    }

    function batchRepositoryEntries(repo) {
      return Array.isArray(repo?.entries) ? repo.entries : [];
    }

    function batchRepositoryUnits(repo) {
      return batchRepositoryEntries(repo).flatMap((entry) => (Array.isArray(entry?.units) ? entry.units : []));
    }

    function batchRepositoryLatestStage3Run(repo) {
      const candidates = batchRepositoryEntries(repo)
        .filter((entry) => String(entry?.stage3_run_id || "").trim())
        .map((entry) => ({
          runId: String(entry.stage3_run_id),
          timestamp: Date.parse(entry.updated_at || entry.created_at || "") || 0,
        }))
        .sort((left, right) => right.timestamp - left.timestamp || right.runId.localeCompare(left.runId));
      return candidates[0]?.runId || null;
    }

    function batchRepositoryLatestStage4Run(repo) {
      const candidates = batchRepositoryUnits(repo)
        .filter((unit) => String(unit?.stage4_run_id || "").trim())
        .map((unit) => ({
          runId: String(unit.stage4_run_id),
          timestamp: Date.parse(unit.updated_at || unit.created_at || "") || 0,
        }))
        .sort((left, right) => right.timestamp - left.timestamp || right.runId.localeCompare(left.runId));
      return candidates[0]?.runId || null;
    }

    function batchRepositoryProgress(repo) {
      const entries = batchRepositoryEntries(repo);
      const units = batchRepositoryUnits(repo);
      const repoStats = repo?.stats || {};
      const stage2Status = normalizeBatchProgressStatus(repo?.stage2_status);
      let stage3Status = normalizeBatchProgressStatus(repo?.stage3_status);
      let stage4Status = normalizeBatchProgressStatus(repo?.stage4_status);
      const entryStatuses = entries.map((entry) => normalizeBatchProgressStatus(entry?.status));
      const unitStatuses = units.map((unit) => normalizeBatchProgressStatus(unit?.status));
      const entryCount = entries.length;
      const unitCount = units.length;
      const assetCount = Number(repoStats.asset_count || units.filter((unit) => unit?.data_pool_asset_id).length || 0);
      const failedUnitCount = Number(repoStats.failed_unit_count || units.filter((unit) => String(unit?.status || "") === "failed").length || 0);
      const stage2StopsPipeline = stage2Status === "abandoned" || stage2Status === "defect";

      if (stage2StopsPipeline) {
        stage3Status = "pending";
        stage4Status = "pending";
      } else if (entryStatuses.some((status) => status === "running")) {
        stage3Status = "running";
      } else if (entryStatuses.length && entryStatuses.every((status) => status === "completed")) {
        stage3Status = "completed";
      } else if (entryStatuses.some((status) => status === "partial")) {
        stage3Status = "partial";
      } else if (entryStatuses.some((status) => status === "failed")) {
        stage3Status = entryStatuses.some((status) => status === "completed") ? "partial" : "failed";
      } else if (entryStatuses.length && entryStatuses.some((status) => status === "pending")) {
        stage3Status = stage2Status === "completed" ? "pending" : stage3Status;
      }

      if (stage2StopsPipeline) {
        stage4Status = "pending";
      } else if (unitStatuses.some((status) => status === "running")) {
        stage4Status = "running";
      } else if (unitStatuses.length && unitStatuses.every((status) => status === "completed")) {
        stage4Status = "completed";
      } else if (unitStatuses.some((status) => status === "partial")) {
        stage4Status = "partial";
      } else if (unitStatuses.some((status) => status === "failed")) {
        stage4Status = unitStatuses.some((status) => status === "completed") ? "partial" : "failed";
      } else if (assetCount > 0 && unitCount === 0) {
        stage4Status = "completed";
      } else if (unitStatuses.length && unitStatuses.some((status) => status === "pending")) {
        stage4Status = "pending";
      }

      let overallStatus = normalizeBatchProgressStatus(repo?.status);
      const hasRunningStage = stage2Status === "running" || stage3Status === "running" || stage4Status === "running";
      if (overallStatus === "running" && !hasRunningStage) {
        overallStatus = "pending";
      }
      if (
        overallStatus === "failed"
        && (
          assetCount > 0
          || stage3Status === "partial"
          || stage4Status === "partial"
          || entryStatuses.some((status) => status === "completed" || status === "partial")
        )
      ) {
        overallStatus = "partial";
      }

      return {
        overallStatus,
        stage2: {
          status: stage2Status,
          repositoryId: repo?.repository_id || null,
          runId: repo?.source_stage2_run_id || repo?.stage2_run_id || null,
        },
        stage3: {
          status: stage3Status,
          repositoryId: stage2StopsPipeline ? null : (repo?.repository_id || null),
          snapshotId: stage2StopsPipeline ? null : (repo?.stage3_snapshot_id || null),
          runId: stage2StopsPipeline ? null : batchRepositoryLatestStage3Run(repo),
        },
        stage4: {
          status: stage4Status,
          repositoryId: stage2StopsPipeline ? null : (repo?.repository_id || null),
          snapshotId: stage2StopsPipeline ? null : (repo?.stage3_snapshot_id || null),
          runId: stage2StopsPipeline ? null : batchRepositoryLatestStage4Run(repo),
          hasSourceGroup: !stage2StopsPipeline && units.some((unit) => String(unit?.stage3_savepoint_id || "").trim()),
        },
        entryCount,
        unitCount,
        assetCount,
        failedUnitCount,
      };
    }

    function renderBatchProgressNode(label, config) {
      const status = normalizeBatchProgressStatus(config?.status);
      const dataset = String(config?.dataset || "").trim();
      const meta = String(config?.meta || "").trim();
      const disabled = !dataset;
      const tagName = disabled ? "span" : "button";
      const attrs = disabled ? "" : `type="button" ${dataset}`;
      const title = `${label} · ${batchStatusLabel(status)}${meta ? ` · ${meta}` : ""}${disabled ? " · 暂无可打开的详细过程" : ""}`;
      return `
        <${tagName}
          class="batch-progress-node ${escapeHtml(status)}${disabled ? " disabled" : ""}"
          ${attrs}
          title="${escapeHtml(title)}"
        >
          <span class="batch-progress-node-label">${escapeHtml(label)}</span>
          ${meta ? `<span class="batch-progress-node-meta">${escapeHtml(meta)}</span>` : ""}
        </${tagName}>
      `;
    }

    function batchProgressSegmentClass(leftStatus, rightStatus) {
      if (leftStatus === "failed" || leftStatus === "partial") return leftStatus;
      if (rightStatus === "running") return "running";
      if (rightStatus === "failed" || rightStatus === "partial") return rightStatus;
      if (rightStatus === "skipped") return "skipped";
      if (rightStatus === "pending") return "pending";
      if (rightStatus === "completed") return "completed";
      if (leftStatus === "completed") return "completed";
      return "pending";
    }

    function renderBatchRepositoryProgress(repo) {
      const progress = batchRepositoryProgress(repo);
      const stage2Dataset = progress.stage2.repositoryId
        && progress.stage2.runId
        ? `data-batch-stage2-repo-id="${escapeHtml(String(progress.stage2.repositoryId))}"${progress.stage2.runId ? ` data-batch-stage2-run-id="${escapeHtml(String(progress.stage2.runId))}"` : ""}`
        : "";
      const stage3Dataset = progress.stage3.repositoryId
        && progress.stage3.snapshotId
        ? `data-batch-stage3-repo-id="${escapeHtml(String(progress.stage3.repositoryId))}" data-batch-stage3-snapshot-id="${escapeHtml(String(progress.stage3.snapshotId))}"${progress.stage3.runId ? ` data-batch-stage3-run-id="${escapeHtml(String(progress.stage3.runId))}"` : ""}`
        : "";
      const stage4SourceGroupKey = progress.stage4.repositoryId && progress.stage4.snapshotId && progress.stage4.hasSourceGroup
        ? `${progress.stage4.repositoryId}::${progress.stage4.snapshotId}`
        : "";
      const stage4Dataset = progress.stage4.runId
        ? `data-batch-stage4-run-id="${escapeHtml(String(progress.stage4.runId))}"`
        : stage4SourceGroupKey
          ? `data-batch-stage4-source-group-key="${escapeHtml(stage4SourceGroupKey)}"`
          : "";
      return `
        <div class="batch-progress-track">
          ${renderBatchProgressNode("Stage2", { status: progress.stage2.status, dataset: stage2Dataset, meta: `${progress.entryCount} entry` })}
          <span class="batch-progress-segment ${escapeHtml(batchProgressSegmentClass(progress.stage2.status, progress.stage3.status))}"></span>
          ${renderBatchProgressNode("Stage3", { status: progress.stage3.status, dataset: stage3Dataset, meta: `${progress.unitCount} depth` })}
          <span class="batch-progress-segment ${escapeHtml(batchProgressSegmentClass(progress.stage3.status, progress.stage4.status))}"></span>
          ${renderBatchProgressNode("Stage4", { status: progress.stage4.status, dataset: stage4Dataset, meta: `${progress.assetCount} data` })}
        </div>
      `;
    }

    function renderBatchRepositoryTable(task) {
      const repositories = Array.isArray(task?.repositories) ? task.repositories : [];
      const pagination = task?.repository_pagination || {};
      const loading = Boolean(state.batchTaskDetailLoading);
      const payloadPage = Math.max(1, Number(pagination.page || state.batchRepositoryList.page || 1));
      const page = loading
        ? Math.max(1, Number(state.batchRepositoryList.page || payloadPage || 1))
        : payloadPage;
      const totalPages = Math.max(1, Number(pagination.total_pages || 1));
      const total = Math.max(0, Number(pagination.total || repositories.length || 0));
      if (!loading) {
        state.batchRepositoryList.page = page;
      }
      state.batchRepositoryList.pageSize = Math.max(1, Number(pagination.page_size || state.batchRepositoryList.pageSize || 15));
      const paginationMeta = `第 ${page} / ${totalPages} 页，共 ${total} 个 repo`;
      const rowsHtml = repositories.length ? repositories.map((repo) => {
        const progress = batchRepositoryProgress(repo);
        const retryAction = progress.overallStatus === "failed"
          ? `<button class="secondary tiny" type="button" data-batch-retry-repository-id="${escapeHtml(String(repo.id || ""))}">重试</button>`
          : "";
        return `
          <tr>
            <td>
              <strong>${escapeHtml(repo.repo_full_name || "-")}</strong>
            </td>
            <td>${escapeHtml(repo.language || "-")}</td>
            <td>${escapeHtml(String(repo.stars || 0))}</td>
            <td>
              <div class="batch-repo-status-actions">
                ${renderStatusPill(progress.overallStatus)}
                ${retryAction}
              </div>
            </td>
            <td>${renderBatchRepositoryProgress(repo)}</td>
            <td>${formatDate(repo.updated_at)}</td>
          </tr>
        `;
      }).join("") : `<tr><td colspan="6" class="muted">当前筛选下没有 repo。</td></tr>`;
      return `
        <div class="detail-card detail-section">
          <div class="batch-repo-table-head">
            <div class="batch-repo-table-title">Repo 进度</div>
            <div class="section-actions">
              <button class="secondary tiny" id="batch-repo-prev-btn" type="button" ${loading || page <= 1 ? "disabled" : ""}>上一页</button>
              <button class="secondary tiny" id="batch-repo-next-btn" type="button" ${loading || page >= totalPages ? "disabled" : ""}>下一页</button>
              <span class="pagination-meta" id="batch-repo-pagination-meta">${escapeHtml(paginationMeta)}</span>
              <div class="popover-anchor" id="batch-repo-filter-anchor">
                <button class="secondary tiny filter-toggle" id="batch-repo-filter-toggle" type="button">筛选</button>
                <div class="repo-filter-popover" id="batch-repo-filter-popover" hidden>
                  <div class="repo-filter-title">筛选 Repo</div>
                  <div class="field-grid">
                    <label class="full">
                      关键词
                      <input id="batch-repo-filter-query" placeholder="owner/repo">
                    </label>
                    <label>
                      语言
                      <input id="batch-repo-filter-language" placeholder="Python">
                    </label>
                    <label>
                      Stars 下界
                      <input id="batch-repo-filter-stars-min" type="number" min="0">
                    </label>
                    <label>
                      Stars 上界
                      <input id="batch-repo-filter-stars-max" type="number" min="0">
                    </label>
                    <label>
                      总状态
                      <select id="batch-repo-filter-status">
                        <option value="">全部</option>
                        <option value="pending">等待中</option>
                        <option value="running">运行中</option>
                        <option value="completed">已完成</option>
                        <option value="partial">部分完成</option>
                        <option value="failed">失败</option>
                        <option value="abandoned">废弃</option>
                        <option value="defect">有缺陷</option>
                      </select>
                    </label>
                    <label>
                      Stage2
                      <select id="batch-repo-filter-stage2-status">
                        <option value="">全部</option>
                        <option value="pending">等待中</option>
                        <option value="running">运行中</option>
                        <option value="completed">已完成</option>
                        <option value="partial">部分完成</option>
                        <option value="failed">失败</option>
                        <option value="abandoned">废弃</option>
                        <option value="defect">有缺陷</option>
                      </select>
                    </label>
                    <label>
                      Stage3
                      <select id="batch-repo-filter-stage3-status">
                        <option value="">全部</option>
                        <option value="pending">等待中</option>
                        <option value="running">运行中</option>
                        <option value="completed">已完成</option>
                        <option value="partial">部分完成</option>
                        <option value="failed">失败</option>
                      </select>
                    </label>
                    <label>
                      Stage4
                      <select id="batch-repo-filter-stage4-status">
                        <option value="">全部</option>
                        <option value="pending">等待中</option>
                        <option value="running">运行中</option>
                        <option value="completed">已完成</option>
                        <option value="partial">部分完成</option>
                        <option value="failed">失败</option>
                      </select>
                    </label>
                    <div class="batch-repo-filter-flags">
                      <label class="batch-repo-filter-flag">
                        <input id="batch-repo-filter-has-errors" type="checkbox">
                        <span>有错误</span>
                      </label>
                      <label class="batch-repo-filter-flag">
                        <input id="batch-repo-filter-non-pending" type="checkbox">
                        <span>非等待中</span>
                      </label>
                    </div>
                  </div>
                  <div class="repo-filter-actions">
                    <button class="secondary tiny" id="batch-repo-filter-reset" type="button">清空</button>
                    <button class="primary tiny" id="batch-repo-filter-apply" type="button">应用筛选</button>
                  </div>
                </div>
              </div>
            </div>
          </div>
          <div class="table-wrap">
            <table class="batch-repo-table">
              <thead>
                <tr>
                  <th>Repo</th>
                  <th>语言</th>
                  <th>Stars</th>
                  <th>状态</th>
                  <th>进度</th>
                  <th>最近更新</th>
                </tr>
              </thead>
              <tbody>${rowsHtml}</tbody>
            </table>
          </div>
        </div>
      `;
    }

    function renderBatchTaskDetail() {
      const panel = $("#batch-detail-panel");
      const body = $("#batch-detail-body");
      const title = $("#batch-detail-title");
      const error = $("#batch-detail-error");
      const message = $("#batch-detail-message");
      const cancelButton = $("#batch-cancel-btn");
      const deleteButton = $("#batch-delete-btn");
      const task = state.batchTaskDetailPayload;
      if (!panel || !body || !task) return;
      panel.hidden = false;
      if (title) title.textContent = task.name || "批量任务详情";
      const stats = task.stats || {};
      const stopAfterStage = String(task.runtime_snapshot?.pipeline?.stop_after_stage || "stage4");
      const pipelineScopeLabel = stopAfterStage === "stage2" ? "仅 Stage2" : "完整流程";
      if (error) {
        const errorMessage = String(task.error_message || "").trim();
        error.hidden = !errorMessage;
        error.textContent = errorMessage;
        error.title = errorMessage;
      }
      if (message) {
        message.innerHTML = `
          <span class="batch-detail-status-text">${escapeHtml(`${batchStatusLabel(task.status)} · ${task.phase || "-"}`)}</span>
          ${renderBatchRunningCounts(stats)}
        `;
      }
      if (cancelButton) cancelButton.hidden = !task.can_cancel;
      if (deleteButton) deleteButton.hidden = !task.can_delete;
      const repositories = Array.isArray(task.repositories) ? task.repositories : [];
      const repositoryCount = Number(stats.repository_count || 0);
      const usesTargetRepositories = Array.isArray(task.filters?.target_repositories)
        && task.filters.target_repositories.some((item) => String(item || "").trim());
      const showStage1Detail = Boolean(task.stage1_detail);
      const showStage1BypassDetail = !showStage1Detail && usesTargetRepositories;
      const stage1DetailHtml = showStage1Detail
        ? renderBatchStage1Detail(task.stage1_detail)
        : (showStage1BypassDetail ? renderBatchStage1BypassDetail(task) : "");
      const repoTableHtml = repositoryCount > 0 ? renderBatchRepositoryTable(task) : "";
      const emptyPipelineHtml = !repoTableHtml && !stage1DetailHtml
        ? `<div class="stage2-empty">Stage1 完成后这里会显示 repo 进度表。</div>`
        : "";
      body.innerHTML = `
        <div class="summary-grid">
          <div><span>运行范围</span><strong>${escapeHtml(pipelineScopeLabel)}</strong></div>
          <div><span>Repo</span><strong>${escapeHtml(String(stats.repository_count || 0))}</strong></div>
          <div><span>Entry</span><strong>${escapeHtml(String(stats.entry_file_count || 0))}</strong></div>
          <div><span>Depth</span><strong>${escapeHtml(String(stats.unit_count || 0))}</strong></div>
          <div><span>资产</span><strong>${escapeHtml(String(stats.asset_count || 0))}</strong></div>
        </div>
        ${repoTableHtml}
        ${stage1DetailHtml}
        ${emptyPipelineHtml}
      `;
      syncBatchRepoFilterForm();
      renderBatchTasks();
      if (!state.batchTaskDetailLoading) {
        syncBatchTaskDetailStream();
      }
    }

    async function loadBatchTaskDetail(taskId) {
      if (state.batchTaskDetailLoading) {
        return state.batchTaskDetailPayload;
      }
      const isTaskChanged = String(state.selectedBatchTaskId || "") !== String(taskId || "");
      if (isTaskChanged) {
        state.batchRepositoryList.page = 1;
        state.batchRepositoryList.filters = defaultBatchRepositoryFilters();
      }
      const requestSerial = state.batchTaskDetailRequestSerial + 1;
      state.batchTaskDetailRequestSerial = requestSerial;
      const query = batchTaskDetailParams().toString();
      state.batchTaskDetailLoading = true;
      renderBatchTaskDetail();
      try {
        const payload = await api(`/api/batch/tasks/${encodeURIComponent(taskId)}?${query}`);
        if (requestSerial !== state.batchTaskDetailRequestSerial) {
          return payload;
        }
        const repositoryFilters = payload.repository_filters || {};
        const repositoryPagination = payload.repository_pagination || {};
        state.selectedBatchTaskId = payload.id;
        state.batchTaskDetailPayload = payload;
        state.batchRepositoryList.filters = {
          query: repositoryFilters.query || "",
          language: repositoryFilters.language || "",
          starsMin: repositoryFilters.stars_min == null ? "" : String(repositoryFilters.stars_min),
          starsMax: repositoryFilters.stars_max == null ? "" : String(repositoryFilters.stars_max),
          status: repositoryFilters.status || "",
          stage2Status: repositoryFilters.stage2_status || "",
          stage3Status: repositoryFilters.stage3_status || "",
          stage4Status: repositoryFilters.stage4_status || "",
          hasErrors: Boolean(repositoryFilters.has_errors),
          nonPending: Boolean(repositoryFilters.non_pending),
        };
        state.batchRepositoryList.page = Math.max(1, Number(repositoryPagination.page || state.batchRepositoryList.page || 1));
        state.batchRepositoryList.pageSize = Math.max(1, Number(repositoryPagination.page_size || state.batchRepositoryList.pageSize || 15));
        return payload;
      } finally {
        if (requestSerial === state.batchTaskDetailRequestSerial) {
          state.batchTaskDetailLoading = false;
          renderBatchTaskDetail();
        }
      }
    }

    async function cancelBatchTask(taskId) {
      await api(`/api/batch/tasks/${encodeURIComponent(taskId)}/cancel`, { method: "POST" });
      await loadBatchTasks();
      await loadBatchTaskDetail(taskId);
      return state.batchTaskDetailPayload;
    }

    function setBatchRetryMessage(text) {
      const message = $("#batch-retry-message");
      if (message) message.textContent = text || "";
    }

    function setBatchRetrySubmitting(submitting) {
      const popover = $("#batch-retry-popover");
      if (popover) popover.dataset.submitting = submitting ? "true" : "false";
      ["#batch-retry-original-btn", "#batch-retry-current-btn", "#batch-retry-cancel-btn"].forEach((selector) => {
        const button = $(selector);
        if (button) button.disabled = Boolean(submitting);
      });
    }

    function openBatchRetryPopover(taskId) {
      const normalizedTaskId = String(taskId || "").trim();
      const popover = $("#batch-retry-popover");
      if (!normalizedTaskId || !popover) return;
      const task = (Array.isArray(state.batchTasks) ? state.batchTasks : []).find(
        (item) => String(item?.id || "") === normalizedTaskId,
      );
      const taskName = String(task?.name || normalizedTaskId).trim() || normalizedTaskId;
      const subtitle = $("#batch-retry-subtitle");
      if (subtitle) subtitle.textContent = `任务：${taskName}`;
      popover.dataset.taskId = normalizedTaskId;
      popover.hidden = false;
      setBatchRetrySubmitting(false);
      setBatchRetryMessage("");
      document.body.classList.add("modal-open");
      requestAnimationFrame(() => $("#batch-retry-original-btn")?.focus());
    }

    function closeBatchRetryPopover() {
      const popover = $("#batch-retry-popover");
      if (!popover || popover.dataset.submitting === "true") return;
      popover.hidden = true;
      delete popover.dataset.taskId;
      setBatchRetryMessage("");
      document.body.classList.remove("modal-open");
    }

    async function retryBatchTask(taskId, runtimeConfigSource = "original") {
      const normalizedSource = runtimeConfigSource === "current" ? "current" : "original";
      const payload = await api(`/api/batch/tasks/${encodeURIComponent(taskId)}/retry`, {
        method: "POST",
        body: JSON.stringify({ runtime_config_source: normalizedSource }),
      });
      await loadBatchTasks();
      await loadBatchTaskDetail(payload.id);
      return state.batchTaskDetailPayload;
    }

    async function retryFailedBatchRepository(taskId, taskRepositoryId) {
      const payload = await api(
        `/api/batch/tasks/${encodeURIComponent(taskId)}/repositories/${encodeURIComponent(taskRepositoryId)}/retry`,
        { method: "POST" },
      );
      await loadBatchTasks();
      await loadBatchTaskDetail(payload.id);
      return state.batchTaskDetailPayload;
    }

    async function submitBatchRetry(runtimeConfigSource) {
      const popover = $("#batch-retry-popover");
      const taskId = String(popover?.dataset.taskId || "").trim();
      if (!popover || !taskId || popover.dataset.submitting === "true") return;
      setBatchRetrySubmitting(true);
      setBatchRetryMessage("正在重试...");
      try {
        await retryBatchTask(taskId, runtimeConfigSource);
        setBatchRetrySubmitting(false);
        closeBatchRetryPopover();
        setRefreshMeta();
      } catch (error) {
        setBatchRetrySubmitting(false);
        setBatchRetryMessage(`重试失败: ${error.message || String(error)}`);
      }
    }

    async function deleteBatchTask(taskId) {
      const normalizedTaskId = String(taskId || "").trim();
      if (!normalizedTaskId) return null;
      const task = (Array.isArray(state.batchTasks) ? state.batchTasks : []).find(
        (item) => String(item?.id || "") === normalizedTaskId,
      ) || (
        state.batchTaskDetailPayload
        && String(state.batchTaskDetailPayload.id || "") === normalizedTaskId
          ? state.batchTaskDetailPayload
          : null
      );
      const taskName = String(task?.name || normalizedTaskId).trim() || normalizedTaskId;
      if (!window.confirm(
        `确认删除批量任务“${taskName}”吗？\n\n这会删除这次任务创建的 Stage1/Stage2/Stage3/Stage4 过程记录与运行目录。\n不会删除数据池里的产物。`,
      )) {
        return null;
      }
      if (!window.confirm(`这是不可恢复操作。确认继续删除“${taskName}”吗？`)) {
        return null;
      }
      const payload = await api(`/api/batch/tasks/${encodeURIComponent(normalizedTaskId)}`, { method: "DELETE" });
      const wasSelected = String(state.selectedBatchTaskId || "") === normalizedTaskId;
      await loadBatchTasks();
      if (wasSelected) {
        hideBatchTaskDetail();
      }
      if (payload.cleanup_warnings) {
        alert(`批量任务已删除，但部分运行资产清理失败，将在后续启动时重试清理：\n${formatApiErrorPayload(payload.cleanup_warnings)}`);
      }
      return payload;
    }

    function prepareBatchStage2DetailOpen(repositoryId, runId) {
      closeStage2RepositoryDetailStream();
      pendingStage2RepositoryDetailPayload = null;
      resetStage2RunRuntimeEditor();
      state.selectedStage2RepositoryId = repositoryId;
      state.selectedStage2RunId = String(runId || "").trim() || null;
      state.selectedStage2AssetKey = null;
      state.pendingStage2RunOpenDefaults = false;
      state.selectedStage2AssetVersionByKey = {};
      state.selectedStage2CompletionFileByAssetKey = {};
      state.stage2RepositoryDetailPayload = null;
      setStage2DetailVisible(true);
      $("#stage2-detail-subtitle").textContent = "正在加载...";
      $("#stage2-detail-action").innerHTML = "";
      $("#stage2-detail-body").innerHTML = `<div class="detail-card detail-section"><div class="stage2-empty">正在加载该 repo 的运行摘要...</div></div>`;
    }

    function prepareBatchStage3DetailOpen(repositoryId, snapshotId, runId) {
      closeStage3RepositoryDetailStream();
      pendingStage3RepositoryDetailPayload = null;
      pendingStage3RepositoryDetailRerender = false;
      state.selectedStage3RepositoryId = repositoryId;
      state.selectedStage3SnapshotId = String(snapshotId || "").trim() || null;
      state.selectedStage3EntryFileId = null;
      state.selectedStage3RunId = String(runId || "").trim() || null;
      state.pendingStage3RunOpenDefaults = false;
      state.stage3RepositoryImagePrewarm = null;
      state.stage3RepositoryImagePrewarmLogScrollState = {};
      resetStage3AssetBrowserState();
      state.stage3List.entryDetailVisible = false;
      setStage3RepositoryDetailPayload(null);
      setStage3DetailVisible(true);
      setStage3DetailHeader({
        title: "Repo 详细",
        backLabel: "返回列表",
        subtitle: "正在加载...",
      });
      $("#stage3-detail-body").innerHTML = `<div class="detail-card detail-section"><div class="stage2-empty">正在加载该 repo 的 Stage3 基线...</div></div>`;
    }

    async function openBatchStageDetail(target) {
      const stage = String(target?.stage || "");
      try {
        if (stage === "stage2") {
          const repositoryId = String(target.repositoryId || "").trim();
          if (!repositoryId) return;
          prepareBatchStage2DetailOpen(repositoryId, target.runId);
          switchTab("stage2");
          await loadStage2RepositoryDetail(repositoryId);
          return;
        }
        if (stage === "stage3") {
          const repositoryId = String(target.repositoryId || "").trim();
          if (!repositoryId) return;
          prepareBatchStage3DetailOpen(repositoryId, target.snapshotId, target.runId);
          switchTab("stage3");
          await loadStage3RepositoryDetail(repositoryId);
          return;
        }
        if (stage === "stage4") {
          const runId = String(target.runId || "").trim();
          const sourceGroupKey = String(target.sourceGroupKey || "").trim();
          if (!runId && !sourceGroupKey) return;
          switchTab("stage4");
          if (runId) {
            await loadStage4RunDetail(runId);
          } else {
            await loadStage4SourceDetailByKey(sourceGroupKey);
          }
        }
      } catch (error) {
        alert(`打开详细过程失败: ${error.message || String(error)}`);
      }
    }

    function readBatchTaskPayload(formElement) {
      const form = new FormData(formElement);
      state.batchForm.languages = readSelectedBatchLanguages();
      state.batchForm.licenses = readSelectedBatchLicenses();
      const languages = [...state.batchForm.languages];
      const selectedPoolId = String(form.get("data_pool_id") || "").trim();
      const selectedGithubTokenTemplateId = String(form.get("stage1_template_id") || "").trim();
      const selectedStage2TemplateId = String(form.get("stage2_template_id") || "").trim();
      const selectedStage3TemplateId = String(form.get("stage3_template_id") || "").trim();
      const selectedStage4TemplateId = String(form.get("stage4_template_id") || "").trim();
      const targetRepositories = newlineValues(form.get("target_repositories"));
      const usesTargetRepositories = targetRepositories.length > 0;
      const stage2Runtime = selectedStage2TemplateId ? null : readBatchRuntimeOverride(form, "stage2", [
        { name: "max_concurrent_runs", type: "number" },
        { name: "planner_model" },
        { name: "planner_base_url" },
        { name: "planner_api_key" },
        { name: "planner_preset" },
        { name: "planner_max_iterations", type: "number" },
        { name: "planner_timeout_seconds", type: "number" },
        { name: "worker_model" },
        { name: "worker_base_url" },
        { name: "worker_api_key" },
        { name: "worker_preset" },
        { name: "worker_max_iterations", type: "number" },
        { name: "worker_timeout_seconds", type: "number" },
        { name: "max_worker_attempts", type: "number" },
        { name: "quickcheck_sample_size", type: "number" },
        { name: "collect_timeout_seconds", type: "number" },
        { name: "run_test_timeout_seconds", type: "number" },
        { name: "build_timeout_seconds", type: "number" },
        { name: "full_validation_timeout_seconds", type: "number" },
        { name: "entry_file_test_count_min", type: "number" },
        { name: "p2p_file_count_limit", type: "number" },
      ]);
      const stage3Runtime = selectedStage3TemplateId ? null : readBatchRuntimeOverride(form, "stage3", [
        { name: "max_concurrent_runs", type: "number" },
        { name: "breaker_model" },
        { name: "breaker_base_url" },
        { name: "breaker_api_key" },
        { name: "breaker_preset" },
        { name: "breaker_max_iterations", type: "number" },
        { name: "breaker_timeout_seconds", type: "number" },
        { name: "build_timeout_seconds", type: "number" },
        { name: "run_test_timeout_seconds", type: "number" },
        { name: "full_validation_timeout_seconds", type: "number" },
        { name: "entry_pass_rate_ceiling", type: "number" },
        { name: "min_removed_code_lines", type: "number" },
      ]);
      const stage4Runtime = selectedStage4TemplateId ? null : readBatchRuntimeOverride(form, "stage4", [
        { name: "max_concurrent_runs", type: "number" },
        { name: "issuer_model" },
        { name: "issuer_base_url" },
        { name: "issuer_api_key" },
        { name: "issuer_preset" },
        { name: "issuer_max_iterations", type: "number" },
        { name: "issuer_timeout_seconds", type: "number" },
        { name: "build_timeout_seconds", type: "number" },
      ]);
      const payload = {
        name: form.get("name"),
        note: form.get("note") || null,
        stop_after_stage: form.get("stop_after_stage") || "stage4",
        filters: {
          language: usesTargetRepositories ? null : (languages.length === 1 ? languages[0] : null),
          languages: usesTargetRepositories ? [] : languages,
          created_after: usesTargetRepositories ? "1970-01-01T00:00:00Z" : isoFromLocal(form.get("created_after")),
          created_before: usesTargetRepositories ? null : isoFromLocal(form.get("created_before")),
          pushed_after: usesTargetRepositories ? null : isoFromLocal(form.get("pushed_after")),
          pushed_before: usesTargetRepositories ? null : isoFromLocal(form.get("pushed_before")),
          stars_min: usesTargetRepositories ? null : (form.get("stars_min") ? Number(form.get("stars_min")) : null),
          stars_max: usesTargetRepositories ? null : (form.get("stars_max") ? Number(form.get("stars_max")) : null),
          repository_limit: usesTargetRepositories ? null : (form.get("repository_limit") ? Number(form.get("repository_limit")) : null),
          exclude_forks: usesTargetRepositories ? false : !(form.get("include_forks") === "on"),
          exclude_archived: usesTargetRepositories ? false : !(form.get("include_archived") === "on"),
          licenses: usesTargetRepositories ? [] : [...state.batchForm.licenses],
          keywords: usesTargetRepositories ? [] : csvValues(form.get("keywords")),
          target_repositories: targetRepositories,
        },
        token_source: "temporary",
        max_concurrent_jobs: form.get("max_concurrent_jobs") ? Number(form.get("max_concurrent_jobs")) : null,
        max_concurrent_partitions: form.get("max_concurrent_partitions") ? Number(form.get("max_concurrent_partitions")) : null,
        github_token_template_id: selectedGithubTokenTemplateId || null,
        stage2_template_id: selectedStage2TemplateId || null,
      };
      const stopAfterStage = payload.stop_after_stage;
      if (stopAfterStage === "stage4") {
        payload.stage3_template_id = selectedStage3TemplateId || null;
        payload.stage4_template_id = selectedStage4TemplateId || null;
      }
      if (!selectedGithubTokenTemplateId) {
        payload.github_tokens = newlineValues(form.get("github_tokens"));
      }
      if (stage2Runtime) payload.stage2_runtime = stage2Runtime;
      if (stopAfterStage === "stage4" && stage3Runtime) payload.stage3_runtime = stage3Runtime;
      if (stopAfterStage === "stage4" && stage4Runtime) payload.stage4_runtime = stage4Runtime;
      if (selectedPoolId === "__new__") {
        throw new Error("请先完成新数据池创建并选择该数据池");
      }
      if (selectedPoolId) {
        payload.data_pool_id = selectedPoolId;
      }
      return payload;
    }

    async function createBatchTask(formElement) {
      const button = $("#batch-submit-btn");
      const message = $("#batch-form-message");
      if (button) button.disabled = true;
      if (message) message.textContent = "正在启动批量任务...";
      try {
        const payload = await api("/api/batch/tasks", {
          method: "POST",
          body: JSON.stringify(readBatchTaskPayload(formElement)),
        });
        if (message) message.textContent = `批量任务已启动: ${payload.id}`;
        await loadBatchTasks();
        await loadBatchTaskDetail(payload.id);
      } catch (error) {
        if (message) message.textContent = `启动失败: ${error.message || String(error)}`;
      } finally {
        if (button) button.disabled = false;
      }
    }

    function formatDataPoolPreviewNumber(value, digits = 0) {
      const number = Number(value);
      if (!Number.isFinite(number)) {
        return "-";
      }
      return number.toLocaleString(undefined, {
        maximumFractionDigits: digits,
        minimumFractionDigits: digits > 0 ? Math.min(digits, 2) : 0,
      });
    }

    function formatDataPoolPreviewStatLine(stats, formatter = (value) => formatDataPoolPreviewNumber(value, 0)) {
      const payload = stats || {};
      return `
        <div class="data-pool-preview-stat-line">
          <span>min ${escapeHtml(formatter(payload.min))}</span>
          <span>p50 ${escapeHtml(formatter(payload.p50))}</span>
          <span>p90 ${escapeHtml(formatter(payload.p90))}</span>
          <span>max ${escapeHtml(formatter(payload.max))}</span>
          <span>avg ${escapeHtml(formatter(payload.avg))}</span>
        </div>
      `;
    }

    function renderDataPoolPreviewBars(rows, { labelKey = "label", countKey = "asset_count", emptyText = "暂无数据" } = {}) {
      const normalizedRows = Array.isArray(rows) ? rows : [];
      if (!normalizedRows.length) {
        return `<div class="stage2-empty">${escapeHtml(emptyText)}</div>`;
      }
      const maxCount = Math.max(1, ...normalizedRows.map((row) => Number(row?.[countKey] || 0)));
      return `
        <div class="data-pool-preview-bars">
          ${normalizedRows.map((row) => {
            const count = Number(row?.[countKey] || 0);
            const label = row?.[labelKey] ?? row?.label ?? "-";
            const width = Math.max(2, Math.round((count / maxCount) * 100));
            return `
              <div class="data-pool-preview-bar-row">
                <div class="data-pool-preview-bar-label">${escapeHtml(String(label))}</div>
                <div class="data-pool-preview-bar-track" title="${escapeHtml(String(count))}">
                  <div class="data-pool-preview-bar-fill" style="width: ${width}%"></div>
                </div>
                <div class="data-pool-preview-bar-value">${escapeHtml(formatDataPoolPreviewNumber(count))}</div>
              </div>
            `;
          }).join("")}
        </div>
      `;
    }

    function renderDataPoolPreviewSummary(payload) {
      const summary = payload?.summary || {};
      return `
        <div class="summary-grid">
          <div><span>Repo</span><strong>${escapeHtml(formatDataPoolPreviewNumber(summary.repo_count))}</strong></div>
          <div><span>入口</span><strong>${escapeHtml(formatDataPoolPreviewNumber(summary.entry_count))}</strong></div>
          <div><span>数据</span><strong>${escapeHtml(formatDataPoolPreviewNumber(summary.asset_count))}</strong></div>
          <div><span>语言</span><strong>${escapeHtml(formatDataPoolPreviewNumber(summary.language_count))}</strong></div>
          <div><span>最大 Depth</span><strong>${escapeHtml(formatDataPoolPreviewNumber(summary.max_depth))}</strong></div>
          <div><span>Repo 平均数据</span><strong>${escapeHtml(formatDataPoolPreviewNumber(summary.avg_assets_per_repo, 1))}</strong></div>
        </div>
      `;
    }

    function renderDataPoolPreviewRepos(rows) {
      const repos = Array.isArray(rows) ? rows : [];
      if (!repos.length) {
        return `<div class="stage2-empty">暂无 repo 数据。</div>`;
      }
      const pageSize = Math.max(1, Number(state.dataPoolPreviewRepoPageSize || 10));
      const total = repos.length;
      const totalPages = Math.max(1, Math.ceil(total / pageSize));
      const page = Math.min(
        Math.max(1, Number(state.dataPoolPreviewRepoPage || 1)),
        totalPages,
      );
      state.dataPoolPreviewRepoPage = page;
      const pageRows = repos.slice((page - 1) * pageSize, page * pageSize);
      const paginationMeta = `第 ${page} / ${totalPages} 页，共 ${total} 个 repo`;
      return `
        <div class="data-pool-preview-table-head">
          <div class="batch-repo-table-title">Repo</div>
          <div class="section-actions">
            <button class="secondary tiny" type="button" data-data-pool-preview-repo-page="prev" ${page <= 1 ? "disabled" : ""}>上一页</button>
            <button class="secondary tiny" type="button" data-data-pool-preview-repo-page="next" ${page >= totalPages ? "disabled" : ""}>下一页</button>
            <span class="pagination-meta">${escapeHtml(paginationMeta)}</span>
          </div>
        </div>
        <div class="data-pool-preview-table-wrap">
          <table>
            <thead>
              <tr>
                <th>Repo</th>
                <th>语言</th>
                <th>数据</th>
                <th>入口</th>
                <th>Depth</th>
                <th>Stars</th>
              </tr>
            </thead>
            <tbody>
              ${pageRows.map((repo) => {
                const minDepth = repo.min_depth ?? "-";
                const maxDepth = repo.max_depth ?? "-";
                const depthText = minDepth === maxDepth ? String(minDepth) : `${minDepth} - ${maxDepth}`;
                return `
                  <tr>
                    <td><strong>${escapeHtml(repo.repo_full_name || "-")}</strong></td>
                    <td>${escapeHtml(repo.language || "-")}</td>
                    <td>${escapeHtml(formatDataPoolPreviewNumber(repo.asset_count))}</td>
                    <td>${escapeHtml(formatDataPoolPreviewNumber(repo.entry_count))}</td>
                    <td>${escapeHtml(depthText)}</td>
                    <td>${escapeHtml(formatDataPoolPreviewNumber(repo.stars))}</td>
                  </tr>
                `;
              }).join("")}
            </tbody>
          </table>
        </div>
      `;
    }

    function renderDataPoolPreview(payload) {
      const body = $("#data-pool-preview-body");
      const subtitle = $("#data-pool-preview-subtitle");
      const message = $("#data-pool-preview-message");
      if (!body) return;
      const pool = payload?.pool || {};
      if (subtitle) subtitle.textContent = `${pool.name || "-"} · ${pool.root_path || ""}`;
      if (message) message.textContent = "";
      body.innerHTML = `
        <section class="data-pool-preview-section wide">
          <h4>总览</h4>
          ${renderDataPoolPreviewSummary(payload)}
        </section>
        <div class="data-pool-preview-grid">
          <section class="data-pool-preview-section">
            <h4>Gold Patch 行数分布</h4>
            ${formatDataPoolPreviewStatLine(payload?.patch_distribution?.stats)}
            ${renderDataPoolPreviewBars(payload?.patch_distribution?.buckets)}
          </section>
          <section class="data-pool-preview-section">
            <h4>Gold Patch 文件数分布</h4>
            ${formatDataPoolPreviewStatLine(payload?.patch_file_distribution?.stats)}
            ${renderDataPoolPreviewBars(payload?.patch_file_distribution?.buckets)}
          </section>
          <section class="data-pool-preview-section">
            <h4>Depth 分布</h4>
            ${renderDataPoolPreviewBars(payload?.depth_distribution, { labelKey: "depth" })}
          </section>
          <section class="data-pool-preview-section">
            <h4>Repo 数据条数分布</h4>
            ${formatDataPoolPreviewStatLine(payload?.repo_asset_distribution?.stats)}
            ${renderDataPoolPreviewBars(payload?.repo_asset_distribution?.buckets, { countKey: "repo_count" })}
          </section>
          <section class="data-pool-preview-section">
            <h4>F2P 分布</h4>
            ${formatDataPoolPreviewStatLine(payload?.quality?.f2p_distribution?.stats)}
            ${renderDataPoolPreviewBars(payload?.quality?.f2p_distribution?.buckets)}
          </section>
          <section class="data-pool-preview-section">
            <h4>入口通过率分布</h4>
            ${formatDataPoolPreviewStatLine(payload?.quality?.entry_pass_rate_distribution?.stats, formatPercent)}
            ${renderDataPoolPreviewBars(payload?.quality?.entry_pass_rate_distribution?.buckets)}
          </section>
          <section class="data-pool-preview-section">
            <h4>语言分布</h4>
            ${renderDataPoolPreviewBars(payload?.language_distribution, { labelKey: "language" })}
          </section>
          <section class="data-pool-preview-section wide">
            <h4>Repo</h4>
            ${renderDataPoolPreviewRepos(payload?.repo_asset_distribution?.repos)}
          </section>
        </div>
      `;
    }

    async function openDataPoolPreview(poolId) {
      const normalizedPoolId = String(poolId || "").trim();
      if (!normalizedPoolId) return;
      const popover = $("#data-pool-preview-popover");
      const body = $("#data-pool-preview-body");
      const subtitle = $("#data-pool-preview-subtitle");
      const message = $("#data-pool-preview-message");
      if (!popover) return;
      state.dataPoolPreviewPoolId = normalizedPoolId;
      state.dataPoolPreviewPayload = null;
      state.dataPoolPreviewRepoPage = 1;
      popover.hidden = false;
      document.body.classList.add("modal-open");
      if (subtitle) subtitle.textContent = "正在加载...";
      if (message) message.textContent = "";
      if (body) body.innerHTML = `<div class="stage2-empty">正在生成数据池 preview...</div>`;
      try {
        const payload = await api(`/api/data-pools/${encodeURIComponent(normalizedPoolId)}/preview`);
        if (String(state.dataPoolPreviewPoolId || "") !== normalizedPoolId) {
          return;
        }
        state.dataPoolPreviewPayload = payload;
        renderDataPoolPreview(payload);
      } catch (error) {
        if (message) message.textContent = `加载失败: ${error.message || String(error)}`;
        if (body) body.innerHTML = `<div class="stage2-empty">Preview 加载失败。</div>`;
      }
    }

    function closeDataPoolPreview() {
      const popover = $("#data-pool-preview-popover");
      if (popover) popover.hidden = true;
      state.dataPoolPreviewPoolId = null;
      state.dataPoolPreviewPayload = null;
      state.dataPoolPreviewRepoPage = 1;
      document.body.classList.remove("modal-open");
    }

    function renderDataPools() {
      const list = $("#data-pool-selector-list");
      const selects = [$("#batch-data-pool-select")].filter(Boolean);
      const pools = Array.isArray(state.dataPools) ? state.dataPools : [];
      const availablePools = pools.filter((pool) => pool.is_available !== false);
      const unavailablePools = pools.filter((pool) => pool.is_available === false);
      selects.forEach((select) => {
        const defaultPool = availablePools.find((pool) => String(pool.id) === String(state.defaultDataPoolId || ""))
          || availablePools.find((pool) => pool.is_default);
        const currentCandidate = state.batchSelectedDataPoolId || select.value || "";
        const currentPool = availablePools.find((pool) => String(pool.id) === String(currentCandidate));
        const selectedPool = availablePools.find((pool) => String(pool.id) === String(state.selectedDataPoolId || ""));
        const fallback = defaultPool?.id || selectedPool?.id || availablePools[0]?.id || "__new__";
        const current = currentPool?.id || fallback;
        select.innerHTML = [
          ...availablePools.map((pool) => `<option value="${escapeHtml(pool.id)}">${escapeHtml(pool.name || pool.root_path || pool.id)}</option>`),
          ...unavailablePools.map((pool) => `<option value="${escapeHtml(pool.id)}" disabled>${escapeHtml(pool.name || pool.root_path || pool.id)}（路径丢失）</option>`),
          `<option value="__new__">新建数据池</option>`,
        ].join("");
        if ([...select.options].some((option) => option.value === current)) {
          select.value = current;
        } else if ([...select.options].some((option) => option.value === fallback)) {
          select.value = fallback;
        }
        if (select.value !== "__new__") {
          state.batchSelectedDataPoolId = select.value || null;
          state.batchDataPoolSelectPreviousId = select.value || null;
        }
      });
      if (!list) return;
      if (!pools.length) {
        list.innerHTML = `<div class="stage2-empty">暂无数据池。</div>`;
        return;
      }
      list.innerHTML = pools.map((pool) => {
        const active = String(pool.id) === String(state.selectedDataPoolId || "");
        const available = pool.is_available !== false;
        return `
          <div class="data-pool-card${active ? " active" : ""}${available ? "" : " missing"}" role="button" tabindex="0" data-data-pool-id="${escapeHtml(pool.id)}">
            <div class="data-pool-card-head">
              <strong>${escapeHtml(pool.name || "-")}${pool.is_default ? " · 默认" : ""}${available ? "" : " · 路径丢失"}</strong>
              <button class="data-pool-preview-link" type="button" data-data-pool-preview-id="${escapeHtml(pool.id)}"${available ? "" : " disabled"}>preview</button>
            </div>
            <span>${escapeHtml(pool.root_path || "")}</span>
            <small>${escapeHtml(String(pool.asset_count || 0))} 条资产 · ${escapeHtml(String(pool.repo_count || 0))} 个 repo</small>
            ${available ? "" : `<small class="danger-text">本地文件夹不存在或不是目录，不能用于批量任务。</small>`}
          </div>
        `;
      }).join("");
    }

    function closeDataPoolSelectorPopover() {
      const popover = $("#data-pool-selector-popover");
      if (popover) popover.hidden = true;
    }

    function setBatchDataPoolPopoverMessage(text) {
      const message = $("#batch-new-data-pool-message");
      if (message) message.textContent = text || "";
    }

    function restoreBatchDataPoolSelect() {
      const select = $("#batch-data-pool-select");
      if (!select) return;
      const availablePools = (Array.isArray(state.dataPools) ? state.dataPools : []).filter((pool) => pool.is_available !== false);
      const availableById = (id) => availablePools.find((pool) => String(pool.id) === String(id || ""))?.id || null;
      const fallback = availableById(state.batchDataPoolSelectPreviousId)
        || availableById(state.batchSelectedDataPoolId)
        || availableById(state.defaultDataPoolId)
        || availablePools.find((pool) => String(pool.id) === String(state.selectedDataPoolId || ""))?.id
        || availablePools[0]?.id
        || "";
      if (fallback && [...select.options].some((option) => option.value === fallback)) {
        select.value = fallback;
        state.batchSelectedDataPoolId = fallback;
        state.batchDataPoolSelectPreviousId = fallback;
      }
    }

    function openBatchDataPoolPopover() {
      const popover = $("#batch-data-pool-popover");
      if (!popover) return;
      popover.hidden = false;
      document.body.classList.add("modal-open");
      setBatchDataPoolPopoverMessage("");
      requestAnimationFrame(() => $("#batch-new-data-pool-name")?.focus());
    }

    function closeBatchDataPoolPopover({ restore = false, clear = false } = {}) {
      const popover = $("#batch-data-pool-popover");
      if (popover) popover.hidden = true;
      document.body.classList.remove("modal-open");
      setBatchDataPoolPopoverMessage("");
      if (clear) {
        const nameInput = $("#batch-new-data-pool-name");
        const rootInput = $("#batch-new-data-pool-root");
        if (nameInput) nameInput.value = "";
        if (rootInput) rootInput.value = "";
      }
      if (restore) restoreBatchDataPoolSelect();
    }

    async function createBatchDataPoolFromPopover() {
      const nameInput = $("#batch-new-data-pool-name");
      const rootInput = $("#batch-new-data-pool-root");
      const button = $("#batch-new-data-pool-create");
      const name = String(nameInput?.value || "").trim();
      const rootPath = String(rootInput?.value || "").trim();
      if (!name || !rootPath) {
        setBatchDataPoolPopoverMessage("请填写新数据池名称和路径");
        return;
      }
      if (button) button.disabled = true;
      setBatchDataPoolPopoverMessage("正在创建...");
      try {
        const payload = await api("/api/data-pools", {
          method: "POST",
          body: JSON.stringify({ name, root_path: rootPath }),
        });
        const poolId = payload.pool?.id;
        if (!poolId) throw new Error("创建成功但响应缺少数据池 id");
        state.batchSelectedDataPoolId = poolId;
        state.batchDataPoolSelectPreviousId = poolId;
        await loadDataPools();
        const select = $("#batch-data-pool-select");
        if (select) select.value = poolId;
        closeBatchDataPoolPopover({ clear: true });
        const message = $("#batch-form-message");
        if (message) message.textContent = "已创建并选择新数据池";
      } catch (error) {
        setBatchDataPoolPopoverMessage(`创建失败: ${error.message || String(error)}`);
      } finally {
        if (button) button.disabled = false;
      }
    }

    async function loadDataPools() {
      const payload = await api("/api/data-pools");
      state.dataPools = Array.isArray(payload.pools) ? payload.pools : [];
      const defaultPool = state.dataPools.find((pool) => pool.is_default)
        || state.dataPools.find((pool) => String(pool.id) === String(payload.default_pool_id || ""));
      state.defaultDataPoolId = defaultPool?.id || payload.default_pool_id || null;
      if (
        (!state.selectedDataPoolId || !state.dataPools.some((pool) => String(pool.id) === String(state.selectedDataPoolId)))
        && state.dataPools.length
      ) {
        state.selectedDataPoolId = state.defaultDataPoolId || state.dataPools[0].id;
      }
      renderDataPools();
      return payload;
    }

    function appendDataPoolFilterParams(params, filters = state.dataPoolList.filters || {}) {
      if (filters.query) params.set("query", filters.query);
      if (filters.repo) params.set("repo", filters.repo);
      if (filters.commit) params.set("commit", filters.commit);
      if (filters.language) params.set("language", filters.language);
      if (filters.entryFile) params.set("entry_file", filters.entryFile);
      if (filters.depth !== "") params.set("depth", filters.depth);
      if (filters.depthMin !== "") params.set("depth_min", filters.depthMin);
      if (filters.depthMax !== "") params.set("depth_max", filters.depthMax);
      if (filters.starsMin !== "") params.set("stars_min", filters.starsMin);
      if (filters.starsMax !== "") params.set("stars_max", filters.starsMax);
      if (filters.stage2RunId) params.set("stage2_run_id", filters.stage2RunId);
      if (filters.stage3RunId) params.set("stage3_run_id", filters.stage3RunId);
      if (filters.stage4RunId) params.set("stage4_run_id", filters.stage4RunId);
      if (filters.createdAfter) params.set("created_after", isoFromLocal(filters.createdAfter));
      if (filters.createdBefore) params.set("created_before", isoFromLocal(filters.createdBefore));
      return params;
    }

    function dataPoolAssetParams() {
      const { filters, sortField, sortOrder } = state.dataPoolList;
      const params = new URLSearchParams({
        page: String(state.dataPoolList.page || 1),
        page_size: String(state.dataPoolList.pageSize || 25),
        sort_by: String(sortField || "created_at"),
        sort_order: String(sortOrder || "desc"),
      });
      return appendDataPoolFilterParams(params, filters);
    }

    function defaultDataPoolFilters() {
      return {
        query: "",
        repo: "",
        commit: "",
        language: "",
        entryFile: "",
        depth: "",
        depthMin: "",
        depthMax: "",
        starsMin: "",
        starsMax: "",
        stage2RunId: "",
        stage3RunId: "",
        stage4RunId: "",
        createdAfter: "",
        createdBefore: "",
      };
    }

    function readDataPoolFiltersFromForm() {
      return {
        query: $("#data-pool-filter-query")?.value.trim() || "",
        repo: $("#data-pool-filter-repo")?.value.trim() || "",
        commit: $("#data-pool-filter-commit")?.value.trim() || "",
        language: $("#data-pool-filter-language")?.value.trim() || "",
        entryFile: $("#data-pool-filter-entry-file")?.value.trim() || "",
        depth: $("#data-pool-filter-depth")?.value.trim() || "",
        depthMin: $("#data-pool-filter-depth-min")?.value.trim() || "",
        depthMax: $("#data-pool-filter-depth-max")?.value.trim() || "",
        starsMin: $("#data-pool-filter-stars-min")?.value.trim() || "",
        starsMax: $("#data-pool-filter-stars-max")?.value.trim() || "",
        stage2RunId: $("#data-pool-filter-stage2-run-id")?.value.trim() || "",
        stage3RunId: $("#data-pool-filter-stage3-run-id")?.value.trim() || "",
        stage4RunId: $("#data-pool-filter-stage4-run-id")?.value.trim() || "",
        createdAfter: $("#data-pool-filter-created-after")?.value || "",
        createdBefore: $("#data-pool-filter-created-before")?.value || "",
      };
    }

    function syncDataPoolFilterForm() {
      const filters = state.dataPoolList.filters || defaultDataPoolFilters();
      const values = {
        "#data-pool-filter-query": filters.query,
        "#data-pool-filter-repo": filters.repo,
        "#data-pool-filter-commit": filters.commit,
        "#data-pool-filter-language": filters.language,
        "#data-pool-filter-entry-file": filters.entryFile,
        "#data-pool-filter-depth": filters.depth,
        "#data-pool-filter-depth-min": filters.depthMin,
        "#data-pool-filter-depth-max": filters.depthMax,
        "#data-pool-filter-stars-min": filters.starsMin,
        "#data-pool-filter-stars-max": filters.starsMax,
        "#data-pool-filter-stage2-run-id": filters.stage2RunId,
        "#data-pool-filter-stage3-run-id": filters.stage3RunId,
        "#data-pool-filter-stage4-run-id": filters.stage4RunId,
        "#data-pool-filter-created-after": filters.createdAfter,
        "#data-pool-filter-created-before": filters.createdBefore,
      };
      Object.entries(values).forEach(([selector, value]) => {
        const input = $(selector);
        if (input) input.value = value || "";
      });
      updateDataPoolFilterToggle();
    }

    function closeDataPoolFilterPopover() {
      const popover = $("#data-pool-filter-popover");
      if (popover) popover.hidden = true;
    }

    function updateDataPoolLayout() {
      const layout = $("#data-pool-layout");
      if (!layout) return;
      layout.classList.toggle("detail-visible", Boolean(state.dataPoolDetailVisible));
      layout.classList.toggle("detail-hidden", !state.dataPoolDetailVisible);
    }

    function dataPoolSortDefaultOrder(field) {
      return ["repo", "commit", "language", "entry_file"].includes(String(field || "")) ? "asc" : "desc";
    }

    function setDataPoolDownloadModalMessage(text) {
      const message = $("#data-pool-download-message");
      if (message) message.textContent = text || "";
    }

    function shellQuote(value) {
      return `'${String(value || "").replace(/'/g, `'\"'\"'`)}'`;
    }

    function defaultDataPoolDownloadOut(pool, selectedOnly = false) {
      const basename = String(pool?.name || "data-pool")
        .trim()
        .replace(/[^A-Za-z0-9._-]+/g, "_")
        .replace(/^_+|_+$/g, "")
        || "data-pool";
      return `./${basename}${selectedOnly ? "-selected" : ""}.zip`;
    }

    function selectedDataPoolAssetIdsList() {
      return Array.from(state.selectedDataPoolAssetIds)
        .map((assetId) => String(assetId || "").trim())
        .filter(Boolean)
        .sort((left, right) => left.localeCompare(right));
    }

    function currentDataPoolDownloadCommand() {
      const pool = state.dataPools.find((row) => String(row.id) === String(state.selectedDataPoolId || ""));
      const origin = String(window.location.origin || "http://127.0.0.1:8000").replace(/\/+$/, "");
      const selectionId = String(state.dataPoolDownloadSelectionId || "").trim();
      return [
        "uv run python -m feature_factory.data_pool_download",
        `--base-url ${shellQuote(origin)}`,
        `--pool-id ${shellQuote(String(state.selectedDataPoolId || ""))}`,
        ...(selectionId ? [`--selection-id ${shellQuote(selectionId)}`] : []),
        `--out ${shellQuote(defaultDataPoolDownloadOut(pool, true))}`,
      ].join(" ");
    }

    async function createDataPoolDownloadSelection() {
      if (!state.selectedDataPoolId) {
        throw new Error("请先选择数据池。");
      }
      const assetIds = selectedDataPoolAssetIdsList();
      if (!assetIds.length) {
        throw new Error("你还没有选中任何的数据，不能下载。");
      }
      const payload = await api(`/api/data-pools/${encodeURIComponent(state.selectedDataPoolId)}/download-selections`, {
        method: "POST",
        body: JSON.stringify({ asset_ids: assetIds }),
      });
      state.dataPoolDownloadSelectionId = String(payload.id || "");
      return payload;
    }

    async function openDataPoolDownloadPopover() {
      if (!state.selectedDataPoolId) return;
      await createDataPoolDownloadSelection();
      const popover = $("#data-pool-download-popover");
      const command = $("#data-pool-download-command");
      if (popover) popover.hidden = false;
      if (command) command.value = currentDataPoolDownloadCommand();
      setDataPoolDownloadModalMessage("");
      document.body.classList.add("modal-open");
      requestAnimationFrame(() => $("#data-pool-download-browser-btn")?.focus());
    }

    function closeDataPoolDownloadPopover() {
      const popover = $("#data-pool-download-popover");
      if (popover) popover.hidden = true;
      setDataPoolDownloadModalMessage("");
      document.body.classList.remove("modal-open");
    }

    async function selectAllDataPoolAssetsForCurrentFilter() {
      if (!state.selectedDataPoolId) return;
      const params = appendDataPoolFilterParams(new URLSearchParams());
      const payload = await api(`/api/data-pools/${encodeURIComponent(state.selectedDataPoolId)}/assets/selection?${params.toString()}`);
      state.selectedDataPoolAssetIds = new Set(
        (Array.isArray(payload.asset_ids) ? payload.asset_ids : []).map((id) => String(id || ""))
      );
      renderDataPoolAssets();
    }

    function parseDownloadFilename(contentDisposition, fallback) {
      const raw = String(contentDisposition || "");
      const quotedMatch = raw.match(/filename="([^"]+)"/i);
      if (quotedMatch?.[1]) {
        return quotedMatch[1];
      }
      const utfMatch = raw.match(/filename\*=UTF-8''([^;]+)/i);
      if (utfMatch?.[1]) {
        try {
          return decodeURIComponent(utfMatch[1]);
        } catch (_) {
          return utfMatch[1];
        }
      }
      return fallback;
    }

    async function startBrowserDataPoolDownload() {
      if (!state.selectedDataPoolId) {
        throw new Error("请先选择数据池。");
      }
      const selectionId = String(state.dataPoolDownloadSelectionId || "").trim();
      if (!selectionId) {
        throw new Error("你还没有选中任何的数据，不能下载。");
      }
      const response = await fetch(
        `/api/data-pools/${encodeURIComponent(state.selectedDataPoolId)}/download-selections/${encodeURIComponent(selectionId)}.zip`,
      );
      if (!response.ok) {
        let detail = `下载失败 (${response.status})`;
        try {
          const payload = await response.json();
          detail = payload?.detail || detail;
        } catch (_) {
          const text = await response.text();
          if (text) detail = text;
        }
        throw new Error(detail);
      }
      const pool = state.dataPools.find((row) => String(row.id) === String(state.selectedDataPoolId || ""));
      const filename = parseDownloadFilename(
        response.headers.get("content-disposition"),
        defaultDataPoolDownloadOut(pool, true).replace(/^\.\//, ""),
      );
      const blob = await response.blob();
      const blobUrl = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = blobUrl;
      link.download = filename;
      link.style.display = "none";
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => window.URL.revokeObjectURL(blobUrl), 1000);
    }

    function dataPoolJsonAvailable(value) {
      if (!value) return false;
      if (Array.isArray(value)) return value.length > 0;
      if (typeof value === "object") return Object.keys(value).length > 0;
      return Boolean(String(value).trim());
    }

    function dataPoolTextAvailable(value) {
      return Boolean(String(value || "").trim());
    }

    function dataPoolTextLineCount(value) {
      const normalized = String(value || "").replace(/\s+$/, "");
      if (!normalized) return 0;
      return normalized.split(/\r?\n/).length;
    }

    function dataPoolCompletionRoleTitle(role) {
      return {
        planner: "Planner 原始 LLM completion",
        worker: "Worker 原始 LLM completion",
        breaker: "Breaker 原始 LLM completion",
        issuer: "Issuer 原始 LLM completion",
        swe: "SWE issue agent 原始 LLM completion",
        fb: "FB issue agent 原始 LLM completion",
        hint: "Hint 风格 issue agent 原始 LLM completion",
      }[String(role || "")] || `${String(role || "LLM")} 原始 completion`;
    }

    function buildDataPoolDetailAssets(detail) {
      const files = detail?.files || {};
      const issues = Array.isArray(files.issues) ? files.issues : [];
      const originalP2PFiles = Array.isArray(files.original_p2p_files) ? files.original_p2p_files : [];
      const savepointFullValidation = Array.isArray(files.savepoint_full_validation) ? files.savepoint_full_validation : [];
      const rawSavepointFeedback = files.savepoint_feedback && typeof files.savepoint_feedback === "object"
        ? files.savepoint_feedback
        : {};
      const structuredSavepointFeedback = (
        rawSavepointFeedback.feedback
        || rawSavepointFeedback.summary
        || rawSavepointFeedback.collateral
        || rawSavepointFeedback.savepoint_id
      ) ? {
          savepoint_id: rawSavepointFeedback.savepoint_id || null,
          feedback: rawSavepointFeedback.feedback || {},
          collateral: rawSavepointFeedback.collateral || {},
          summary: rawSavepointFeedback.summary || {},
        } : {
          feedback: rawSavepointFeedback || {},
          collateral: {},
          summary: {
            entry_file_path: detail?.entry_file_path || "",
            entry_pass_rate: detail?.entry_pass_rate,
            p2p_count: detail?.p2p_count,
            f2p_count: detail?.f2p_count,
            checkpoint_status: "-",
            reusable_checkpoint_ready: false,
            diff_stats: {
              total_changed_lines: Number(detail?.diff_stats?.lines_changed || 0),
              added_lines: Number(detail?.diff_stats?.lines_added || 0),
              removed_lines: Number(detail?.diff_stats?.lines_deleted || 0),
            },
          },
        };
      const completionRoles = Object.entries(detail?.llm_completions || {}).sort(([left], [right]) => left.localeCompare(right));
      return [
        {
          key: "manifest",
          title: "Manifest",
          kind: "json",
          available: dataPoolJsonAvailable(files.manifest),
          meta: dataPoolJsonAvailable(files.manifest) ? "JSON" : "暂无",
          emptyText: "当前没有 manifest。",
          value: files.manifest || {},
        },
        {
          key: "issues",
          title: "Issue List",
          kind: "issues",
          available: issues.length > 0,
          meta: issues.length > 0 ? `${issues.length} 条 issue` : "暂无",
          emptyText: "当前没有 issue list。",
          value: issues,
        },
        {
          key: "dockerfile",
          title: "Dockerfile",
          kind: "text",
          available: dataPoolTextAvailable(files.dockerfile),
          meta: dataPoolTextAvailable(files.dockerfile) ? `${dataPoolTextLineCount(files.dockerfile)} 行` : "暂无",
          emptyText: "当前没有 Dockerfile。",
          value: files.dockerfile || "",
        },
        {
          key: "run_script",
          title: "run_script.sh",
          kind: "text",
          available: dataPoolTextAvailable(files.run_script),
          meta: dataPoolTextAvailable(files.run_script) ? `${dataPoolTextLineCount(files.run_script)} 行` : "暂无",
          emptyText: "当前没有 run_script.sh。",
          value: files.run_script || "",
        },
        {
          key: "gold_patch",
          title: "Gold Patch",
          kind: "text",
          available: dataPoolTextAvailable(files.gold_patch),
          meta: dataPoolTextAvailable(files.gold_patch) ? stage4DiffStatsText(detail?.diff_stats || {}) : "暂无",
          emptyText: "当前没有 gold patch。",
          value: files.gold_patch || "",
        },
        {
          key: "original_p2p_files",
          title: "原始 P2P 集合表",
          kind: "table_p2p",
          available: originalP2PFiles.length > 0,
          meta: originalP2PFiles.length > 0 ? `${originalP2PFiles.length} 行` : "暂无",
          emptyText: "当前没有原始 P2P 集合表。",
          value: originalP2PFiles,
        },
        {
          key: "savepoint_feedback",
          title: "savepoint feedback",
          kind: "savepoint_feedback",
          available: dataPoolJsonAvailable(files.savepoint_feedback),
          meta: dataPoolJsonAvailable(files.savepoint_feedback) ? "已产出" : "暂无",
          emptyText: "当前没有 savepoint feedback。",
          value: structuredSavepointFeedback,
        },
        {
          key: "file_results",
          title: "savepoint full validation 表",
          kind: "table_full_validation",
          available: savepointFullValidation.length > 0,
          meta: savepointFullValidation.length > 0 ? `${savepointFullValidation.length} 行` : "暂无",
          emptyText: "当前还没有 savepoint full validation 表。",
          value: savepointFullValidation,
        },
        ...completionRoles.map(([role, entries]) => {
          const filesForRole = Array.isArray(entries) ? entries : [];
          return {
            key: `llm_${role}`,
            title: dataPoolCompletionRoleTitle(role),
            kind: "completion_archive",
            available: filesForRole.length > 0,
            meta: filesForRole.length > 0 ? `${filesForRole.length} 个文件` : "暂无",
            emptyText: `当前没有 ${dataPoolCompletionRoleTitle(role)}。`,
            files: filesForRole.map((entry) => ({
              path: String(entry?.path || ""),
              size_bytes: Number(entry?.size_bytes || 0),
              modified_at: entry?.modified_at || null,
            })),
            assetId: detail?.id || null,
            archiveRole: role,
          };
        }),
      ];
    }

    function currentSelectedDataPoolDetailAsset(detail = state.dataPoolAssetDetail) {
      const assets = buildDataPoolDetailAssets(detail);
      const validKeys = new Set(assets.map((asset) => asset.key));
      if (!validKeys.has(state.selectedDataPoolDetailAssetKey)) {
        state.selectedDataPoolDetailAssetKey = (assets.find((asset) => asset.available) || assets[0] || {}).key || null;
      }
      return assets.find((asset) => asset.key === state.selectedDataPoolDetailAssetKey) || assets[0] || null;
    }

    function selectedDataPoolIssue(detail = state.dataPoolAssetDetail) {
      const issues = Array.isArray(detail?.files?.issues) ? detail.files.issues : [];
      if (!issues.length) return null;
      let selected = issues.find((item) => Number(item.variant_index) === Number(state.selectedDataPoolIssueIndex));
      if (!selected) {
        selected = issues[0];
        state.selectedDataPoolIssueIndex = Number(selected.variant_index || 1);
      }
      return selected;
    }

    function selectedDataPoolCompletionFile(asset) {
      const files = Array.isArray(asset?.files) ? asset.files : [];
      if (!files.length) {
        return null;
      }
      const selectedPath = state.selectedDataPoolCompletionFileByAssetKey[asset.key];
      return files.find((file) => String(file.path || "") === String(selectedPath || "")) || files[0];
    }

    function dataPoolCompletionFileCacheKey(asset, filePath) {
      return [
        asset?.assetId || "",
        asset?.archiveRole || "",
        filePath || "",
      ].join("::");
    }

    function renderDataPoolCompletionArchivePreview(asset) {
      if (!asset?.available) {
        return `<div class="stage2-empty">${escapeHtml(asset?.emptyText || "当前没有可预览的 completion 内容。")}</div>`;
      }
      const files = Array.isArray(asset.files) ? asset.files : [];
      if (!files.length) {
        return `<div class="stage2-empty">当前没有可预览的 completion 文件。</div>`;
      }
      const selectedFile = selectedDataPoolCompletionFile(asset);
      const selectedPath = String(selectedFile?.path || "");
      const cacheKey = dataPoolCompletionFileCacheKey(asset, selectedPath);
      const cached = state.dataPoolCompletionFileCache[cacheKey];
      let preview = `<div class="stage2-empty">正在加载 ${escapeHtml(selectedPath || "completion 文件")}...</div>`;
      if (cached?.error) {
        preview = `<div class="stage2-empty">读取失败：${escapeHtml(cached.error)}</div>`;
      } else if (cached?.payload) {
        if (cached.payload.kind === "json") {
          preview = `<pre>${escapeHtml(JSON.stringify(cached.payload.value, null, 2))}</pre>`;
        } else {
          preview = `<pre>${escapeHtml(cached.payload.text || "")}</pre>`;
        }
      }
      return `
        <div class="stage2-completion-browser">
          <div class="stage2-completion-file-list">
            ${files.map((file) => {
              const path = String(file.path || "");
              const fileName = path.split("/").filter(Boolean).pop() || path;
              return `
                <button
                  class="stage2-completion-file-item${path === selectedPath ? " active" : ""}"
                  type="button"
                  data-data-pool-completion-file-path="${escapeHtml(path)}"
                >
                  <div class="stage2-completion-file-name">${escapeHtml(fileName)}</div>
                  <div class="stage2-completion-file-meta">${escapeHtml(formatBytes(file.size_bytes))} · ${escapeHtml(file.modified_at ? formatDate(file.modified_at) : path)}</div>
                </button>
              `;
            }).join("")}
          </div>
          <div class="stage2-completion-preview">
            ${preview}
          </div>
        </div>
      `;
    }

    function stage4VariantMarkdownText(variant) {
      if (!variant || typeof variant !== "object") {
        return "";
      }
      return String(variant.issue_markdown || "").trim();
    }

    function renderStage4PublicVariantMarkdown(variant, emptyText) {
      const markdown = stage4VariantMarkdownText(variant);
      const title = String(variant?.title || "").trim();
      const descriptor = String(variant?.style || "").trim();
      return `
        <div class="stage4-public-markdown-asset">
          ${(title || descriptor) ? `
            <div class="stage4-public-asset-head">
              ${title ? `<h5>${escapeHtml(title)}</h5>` : ""}
              ${descriptor ? `<div class="stage4-public-asset-meta">${escapeHtml(descriptor)}</div>` : ""}
            </div>
          ` : ""}
          ${renderMarkdownBlock(markdown, emptyText)}
        </div>
      `;
    }

    function renderDataPoolIssueAsset(detail) {
      const issues = Array.isArray(detail?.files?.issues) ? detail.files.issues : [];
      if (!issues.length) {
        return `<div class="stage2-empty">当前还没有 issue list。</div>`;
      }
      const selected = selectedDataPoolIssue(detail);
      return renderStage4PublicVariantMarkdown(selected, "当前没有 issue markdown。");
    }

    function renderDataPoolAssetVersions(asset, detail) {
      if (!asset || !detail) {
        return "";
      }
      if (asset.kind === "issues") {
        const issues = Array.isArray(detail?.files?.issues) ? detail.files.issues : [];
        if (!issues.length) {
          return "";
        }
        const selected = selectedDataPoolIssue(detail);
        return `
          <div class="stage2-asset-version-strip">
            ${issues.map((variant) => `
              <button
                class="stage2-asset-version-chip${Number(variant.variant_index) === Number(selected?.variant_index) ? " active" : ""}"
                type="button"
                data-data-pool-issue-select-index="${escapeHtml(String(variant.variant_index))}"
              >${escapeHtml(`Issue ${variant.variant_index} · ${variant.style || "-"}`)}</button>
            `).join("")}
          </div>
        `;
      }
      return "";
    }

    function renderDataPoolDetailAssetPreview(asset, detail = state.dataPoolAssetDetail) {
      if (!asset?.available) {
        return `<div class="stage2-empty">${escapeHtml(asset?.emptyText || "当前没有可预览内容。")}</div>`;
      }
      if (asset.kind === "text") {
        return `<pre>${escapeHtml(String(asset.value || ""))}</pre>`;
      }
      if (asset.kind === "json") {
        return renderJsonPre(asset.value, asset.emptyText);
      }
      if (asset.kind === "issues") {
        return renderDataPoolIssueAsset(detail);
      }
      if (asset.kind === "savepoint_feedback") {
        return renderStage3SavepointFeedbackSummary(asset.value, asset.emptyText);
      }
      if (asset.kind === "table_p2p") {
        return renderStage3OriginalP2PTable(asset.value);
      }
      if (asset.kind === "table_full_validation") {
        return renderStage3FileResultsTable(asset.value);
      }
      if (asset.kind === "completion_archive") {
        return renderDataPoolCompletionArchivePreview(asset);
      }
      return `<div class="stage2-empty">当前没有可预览内容。</div>`;
    }

    function dataPoolCompletionAssetCopyText(asset) {
      if (!asset?.available || asset.kind !== "completion_archive") {
        return "";
      }
      const selectedFile = selectedDataPoolCompletionFile(asset);
      if (!selectedFile) {
        return "";
      }
      const cacheKey = dataPoolCompletionFileCacheKey(asset, selectedFile.path || "");
      const cached = state.dataPoolCompletionFileCache[cacheKey];
      if (!cached?.payload) {
        return "";
      }
      if (cached.payload.kind === "json") {
        return JSON.stringify(cached.payload.value, null, 2);
      }
      return String(cached.payload.text || "");
    }

    function dataPoolAssetCopyText(asset) {
      if (!asset?.available) {
        return "";
      }
      if (asset.kind === "text") {
        return String(asset.value || "");
      }
      if (asset.kind === "json") {
        return JSON.stringify(asset.value || {}, null, 2);
      }
      if (asset.kind === "issues") {
        return stage4VariantMarkdownText(selectedDataPoolIssue());
      }
      if (asset.kind === "savepoint_feedback" || asset.kind === "table_p2p" || asset.kind === "table_full_validation") {
        return JSON.stringify(asset.value || {}, null, 2);
      }
      if (asset.kind === "completion_archive") {
        return dataPoolCompletionAssetCopyText(asset);
      }
      return "";
    }

    function renderDataPoolAssetCopyAction(asset) {
      if (!dataPoolAssetCopyText(asset)) {
        return "";
      }
      return `
        <button
          class="secondary tiny stage2-asset-copy"
          type="button"
          data-data-pool-asset-copy
        >复制</button>
      `;
    }

    async function copySelectedDataPoolAsset(button) {
      const asset = currentSelectedDataPoolDetailAsset();
      const text = dataPoolAssetCopyText(asset);
      if (!text) {
        return;
      }
      const originalText = button.textContent;
      try {
        await copyTextToClipboard(text);
        button.textContent = "已复制";
        window.setTimeout(() => {
          if (button.isConnected) {
            button.textContent = originalText || "复制";
          }
        }, 1200);
      } catch (error) {
        alert(`复制失败: ${error.message || String(error)}`);
      }
    }

    async function ensureDataPoolCompletionFileLoaded(asset) {
      if (!asset || asset.kind !== "completion_archive" || !asset.available) {
        return;
      }
      const selectedFile = selectedDataPoolCompletionFile(asset);
      if (!selectedFile?.path || !asset.assetId || !asset.archiveRole || !state.selectedDataPoolId) {
        return;
      }
      const cacheKey = dataPoolCompletionFileCacheKey(asset, selectedFile.path);
      const cached = state.dataPoolCompletionFileCache[cacheKey];
      if (cached?.loading || cached?.payload) {
        return;
      }
      state.dataPoolCompletionFileCache[cacheKey] = { loading: true };
      try {
        const params = new URLSearchParams({ path: selectedFile.path });
        const payload = await api(
          `/api/data-pools/${encodeURIComponent(state.selectedDataPoolId)}/assets/${encodeURIComponent(asset.assetId)}/llm-completions/${encodeURIComponent(asset.archiveRole)}/files?${params.toString()}`,
        );
        state.dataPoolCompletionFileCache[cacheKey] = { payload };
      } catch (error) {
        state.dataPoolCompletionFileCache[cacheKey] = { error: error.message || String(error) };
      }
      const currentAsset = currentSelectedDataPoolDetailAsset();
      const currentFile = currentAsset ? selectedDataPoolCompletionFile(currentAsset) : null;
      if (
        currentAsset?.kind === "completion_archive"
        && dataPoolCompletionFileCacheKey(currentAsset, currentFile?.path || "") === cacheKey
      ) {
        rerenderDataPoolDetailPreservingScroll();
      }
    }

    function ensureSelectedDataPoolCompletionFileLoaded() {
      ensureDataPoolCompletionFileLoaded(currentSelectedDataPoolDetailAsset());
    }

    function renderDataPoolAssetBrowser(detail) {
      const assets = buildDataPoolDetailAssets(detail);
      const selectedAsset = currentSelectedDataPoolDetailAsset(detail);
      const availableCount = assets.filter((asset) => asset.available).length;
      if (!selectedAsset) {
        return `<div class="stage2-empty">当前没有输出资产。</div>`;
      }
      return `
        <div class="stage2-asset-browser">
          <div class="stage2-asset-panel">
            <div class="stage2-asset-panel-head">
              <h5>输出资产</h5>
              <div class="stage2-asset-panel-note">${assets.length} 个资产 · ${availableCount} 个已生成</div>
            </div>
            <div class="stage2-asset-list">
              ${assets.map((asset) => `
                <button
                  class="stage2-asset-item${asset.key === state.selectedDataPoolDetailAssetKey ? " active" : ""}"
                  type="button"
                  data-data-pool-detail-asset-key="${escapeHtml(asset.key)}"
                >
                  <div class="stage2-asset-item-title">${escapeHtml(asset.title)}</div>
                  <div class="stage2-asset-item-meta">${escapeHtml(asset.meta)}</div>
                </button>
              `).join("")}
            </div>
          </div>
          <div class="stage2-asset-panel">
            <div class="stage2-asset-panel-head">
              <div class="stage2-asset-preview-main">
                <div class="stage2-asset-preview-title">${escapeHtml(selectedAsset.title)}</div>
                ${renderDataPoolAssetVersions(selectedAsset, detail)}
                ${renderDataPoolAssetCopyAction(selectedAsset)}
              </div>
              <div class="stage2-asset-panel-note">${escapeHtml(selectedAsset.meta || "")}</div>
            </div>
            <div class="stage2-asset-preview-body">
              ${renderDataPoolDetailAssetPreview(selectedAsset, detail)}
            </div>
          </div>
        </div>
      `;
    }

    function renderDataPoolAssetDetailBody(asset) {
      const body = $("#data-pool-asset-detail-body");
      if (!body) {
        return;
      }
      body.innerHTML = `
        <div class="summary-grid">
          <div><span>Repo</span><strong>${escapeHtml(asset.repo_full_name || "-")}</strong></div>
          <div><span>语言</span><strong>${escapeHtml(asset.language || "-")}</strong></div>
          <div><span>入口</span><strong class="mono">${escapeHtml(asset.entry_file_path || "-")}</strong></div>
          <div><span>Depth</span><strong>${escapeHtml(String(asset.depth))}</strong></div>
          <div><span>入口通过率</span><strong>${escapeHtml(formatPercent(asset.entry_pass_rate))}</strong></div>
          <div><span>P2P / F2P</span><strong>${escapeHtml(`${Number(asset.p2p_count || 0)} / ${Number(asset.f2p_count || 0)}`)}</strong></div>
          <div><span>diff 行数</span><strong>${escapeHtml(stage4DiffStatsText(asset.diff_stats || {}))}</strong></div>
          <div><span>产出 issue</span><strong>${escapeHtml(String(Number(asset.issue_count || 0)))}</strong></div>
        </div>
        ${renderDataPoolAssetBrowser(asset)}
      `;
      ensureSelectedDataPoolCompletionFileLoaded();
    }

    function captureDataPoolDetailScrollState() {
      const detailBody = $("#data-pool-asset-detail-body");
      return {
        windowScrollX: window.scrollX,
        windowScrollY: window.scrollY,
        detailScrollTop: detailBody?.scrollTop ?? 0,
        detailScrollLeft: detailBody?.scrollLeft ?? 0,
        ...captureStage2AssetBrowserScrollState(detailBody),
      };
    }

    function restoreDataPoolDetailScrollState(scrollState) {
      if (!scrollState) {
        return;
      }
      requestAnimationFrame(() => {
        const detailBody = $("#data-pool-asset-detail-body");
        if (detailBody) {
          detailBody.scrollTop = scrollState.detailScrollTop ?? 0;
          detailBody.scrollLeft = scrollState.detailScrollLeft ?? 0;
        }
        restoreStage2AssetBrowserScrollState(scrollState, detailBody);
        window.scrollTo(scrollState.windowScrollX ?? 0, scrollState.windowScrollY ?? 0);
      });
    }

    function rerenderDataPoolDetailPreservingScroll() {
      if (!state.dataPoolAssetDetail) {
        return;
      }
      const scrollState = captureDataPoolDetailScrollState();
      renderDataPoolAssetDetailBody(state.dataPoolAssetDetail);
      restoreDataPoolDetailScrollState(scrollState);
    }

    function hideDataPoolAssetDetail({ clearSelection = false } = {}) {
      state.dataPoolDetailVisible = false;
      state.selectedDataPoolDetailAssetKey = null;
      state.selectedDataPoolCompletionFileByAssetKey = {};
      state.dataPoolCompletionFileCache = {};
      state.selectedDataPoolIssueIndex = 1;
      if (clearSelection) {
        state.selectedDataPoolAssetId = null;
        state.dataPoolAssetDetail = null;
      }
      updateDataPoolLayout();
      const panel = $("#data-pool-asset-detail-panel");
      const body = $("#data-pool-asset-detail-body");
      const title = $("#data-pool-asset-detail-title");
      const message = $("#data-pool-asset-detail-message");
      if (panel) panel.hidden = true;
      if (body) body.innerHTML = "";
      if (title) title.textContent = "资产详情";
      if (message) message.textContent = "";
      renderDataPoolAssets();
    }

    function showDataPoolAssetDetailShell(assetId, loadingText = "正在加载资产详情...") {
      state.dataPoolDetailVisible = true;
      state.dataPoolAssetDetail = null;
      if (assetId) {
        if (String(state.selectedDataPoolAssetId || "") !== String(assetId || "")) {
          state.selectedDataPoolDetailAssetKey = null;
          state.selectedDataPoolCompletionFileByAssetKey = {};
          state.dataPoolCompletionFileCache = {};
          state.selectedDataPoolIssueIndex = 1;
        }
        state.selectedDataPoolAssetId = String(assetId);
      }
      updateDataPoolLayout();
      const panel = $("#data-pool-asset-detail-panel");
      const body = $("#data-pool-asset-detail-body");
      const title = $("#data-pool-asset-detail-title");
      const message = $("#data-pool-asset-detail-message");
      if (panel) panel.hidden = false;
      if (title) title.textContent = "资产详情";
      if (message) message.textContent = loadingText;
      if (body) {
        body.innerHTML = `<div class="detail-card detail-section"><div class="stage2-empty">${escapeHtml(loadingText)}</div></div>`;
      }
      renderDataPoolAssets();
    }

    function updateDataPoolFilterToggle() {
      const button = $("#data-pool-filter-toggle");
      if (!button) return;
      const filters = state.dataPoolList.filters || {};
      const count = Object.values(filters).filter((value) => String(value || "").trim()).length;
      button.textContent = count ? `筛选 · ${count}` : "筛选";
      button.classList.toggle("active", count > 0);
    }

    function renderDataPoolAssets() {
      const tbody = $("#data-pool-assets-body");
      const meta = $("#data-pool-pagination-meta");
      const title = $("#data-pool-assets-title");
      const selectorToggle = $("#data-pool-selector-toggle");
      const selectedPool = state.dataPools.find((pool) => String(pool.id) === String(state.selectedDataPoolId));
      if (title) title.textContent = selectedPool ? `${selectedPool.name}` : "数据池";
      if (selectorToggle) selectorToggle.textContent = "数据池";
      if (meta) meta.textContent = `第 ${state.dataPoolList.page} / ${state.dataPoolList.totalPages} 页，共 ${state.dataPoolList.total} 条`;
      const prev = $("#data-pool-prev-btn");
      const next = $("#data-pool-next-btn");
      if (prev) prev.disabled = state.dataPoolList.page <= 1;
      if (next) next.disabled = state.dataPoolList.page >= state.dataPoolList.totalPages;
      if (!tbody) return;
      const assets = Array.isArray(state.dataPoolAssets) ? state.dataPoolAssets : [];
      const columnCount = 12;
      if (!state.selectedDataPoolId) {
        tbody.innerHTML = `<tr><td colspan="${columnCount}" class="muted">请选择数据池。</td></tr>`;
        return;
      }
      if (!assets.length) {
        tbody.innerHTML = `<tr><td colspan="${columnCount}" class="muted">当前筛选下没有资产。</td></tr>`;
        return;
      }
      tbody.innerHTML = assets.map((asset) => {
        const checked = state.selectedDataPoolAssetIds.has(String(asset.id));
        const active = String(asset.id) === String(state.selectedDataPoolAssetId || "");
        const p2pCount = Number(asset.p2p_count || 0);
        const f2pCount = Number(asset.f2p_count || 0);
        const issueCount = Number(asset.issue_count || 0);
        return `
          <tr data-data-pool-asset-id="${escapeHtml(asset.id)}" class="${active ? "active" : ""}">
            <td><input type="checkbox" data-data-pool-asset-check="${escapeHtml(asset.id)}" ${checked ? "checked" : ""}></td>
            <td>${escapeHtml(asset.repo_full_name || "-")}</td>
            <td class="mono">${escapeHtml(String(asset.source_commit_sha || "").slice(0, 12) || "-")}</td>
            <td>${escapeHtml(asset.language || "-")}</td>
            <td>${escapeHtml(String(asset.stars || 0))}</td>
            <td class="mono">${escapeHtml(asset.entry_file_path || "-")}</td>
            <td>${escapeHtml(formatPercent(asset.entry_pass_rate))}</td>
            <td>${escapeHtml(`${p2pCount} / ${f2pCount}`)}</td>
            <td>${escapeHtml(stage4DiffStatsText(asset.diff_stats || {}))}</td>
            <td>${escapeHtml(String(issueCount))}</td>
            <td>${escapeHtml(String(asset.depth))}</td>
            <td>${formatDate(asset.created_at)}</td>
          </tr>
        `;
      }).join("");
      const pageCheck = $("#data-pool-select-page");
      if (pageCheck) {
        const allSelected = state.dataPoolList.total > 0
          && state.selectedDataPoolAssetIds.size >= state.dataPoolList.total;
        pageCheck.checked = allSelected;
        pageCheck.indeterminate = !allSelected && state.selectedDataPoolAssetIds.size > 0;
        pageCheck.disabled = false;
      }
      document.querySelectorAll("[data-data-pool-sort]").forEach((button) => {
        const active = button.dataset.dataPoolSort === state.dataPoolList.sortField;
        button.classList.toggle("active", active);
        const label = button.dataset.sortLabel || button.textContent.replace(/[ ↑↓]+$/, "");
        const arrow = active ? (state.dataPoolList.sortOrder === "asc" ? " ↑" : " ↓") : "";
        button.textContent = `${label}${arrow}`;
      });
    }

    async function loadDataPoolAssets() {
      if (!state.selectedDataPoolId) {
        state.dataPoolAssets = [];
        hideDataPoolAssetDetail({ clearSelection: true });
        renderDataPoolAssets();
        return null;
      }
      const payload = await api(`/api/data-pools/${encodeURIComponent(state.selectedDataPoolId)}/assets?${dataPoolAssetParams().toString()}`);
      state.dataPoolAssets = Array.isArray(payload.assets) ? payload.assets : [];
      const pagination = payload.pagination || {};
      state.dataPoolList.page = Number(pagination.page || 1);
      state.dataPoolList.pageSize = Number(pagination.page_size || 25);
      state.dataPoolList.total = Number(pagination.total || 0);
      state.dataPoolList.totalPages = Number(pagination.total_pages || 1);
      updateDataPoolFilterToggle();
      renderDataPoolAssets();
      if (state.selectedDataPoolAssetId) {
        const selectedVisible = state.dataPoolAssets.some(
          (asset) => String(asset.id || "") === String(state.selectedDataPoolAssetId || ""),
        );
        if (!selectedVisible) {
          hideDataPoolAssetDetail({ clearSelection: true });
        } else if (state.dataPoolDetailVisible) {
          await loadDataPoolAssetDetail(state.selectedDataPoolAssetId);
        }
      }
      return payload;
    }

    async function loadDataPoolAssetDetail(assetId) {
      if (!state.selectedDataPoolId || !assetId) return null;
      showDataPoolAssetDetailShell(assetId);
      const panel = $("#data-pool-asset-detail-panel");
      const body = $("#data-pool-asset-detail-body");
      const title = $("#data-pool-asset-detail-title");
      const message = $("#data-pool-asset-detail-message");
      const payload = await api(`/api/data-pools/${encodeURIComponent(state.selectedDataPoolId)}/assets/${encodeURIComponent(assetId)}`);
      const asset = payload.asset || {};
      state.selectedDataPoolAssetId = asset.id;
      state.dataPoolAssetDetail = asset;
      if (panel) panel.hidden = false;
      if (title) title.textContent = `${asset.repo_full_name || "资产"} · depth ${asset.depth}`;
      if (message) message.textContent = "";
      renderDataPoolAssetDetailBody(asset);
      renderDataPoolAssets();
      return asset;
    }

    async function deleteDataPoolAssets({ filtered = false } = {}) {
      if (!state.selectedDataPoolId) return;
      const message = $("#data-pool-message");
      const body = filtered
        ? Object.fromEntries(
          Object.entries(state.dataPoolList.filters)
            .filter(([, value]) => value !== "" && value != null)
            .map(([key, value]) => {
              const apiKey = {
                entryFile: "entry_file",
                depthMin: "depth_min",
                depthMax: "depth_max",
                starsMin: "stars_min",
                starsMax: "stars_max",
                stage2RunId: "stage2_run_id",
                stage3RunId: "stage3_run_id",
                stage4RunId: "stage4_run_id",
                createdAfter: "created_after",
                createdBefore: "created_before",
              }[key] || key;
              const numericFields = new Set(["depth", "depth_min", "depth_max", "stars_min", "stars_max"]);
              const datetimeFields = new Set(["created_after", "created_before"]);
              if (numericFields.has(apiKey)) return [apiKey, Number(value)];
              if (datetimeFields.has(apiKey)) return [apiKey, isoFromLocal(value)];
              return [apiKey, value];
            }),
        )
        : { asset_ids: Array.from(state.selectedDataPoolAssetIds) };
      if (!filtered && !body.asset_ids.length) {
        if (message) message.textContent = "请先选择要删除的资产。";
        return;
      }
      if (message) message.textContent = "正在删除...";
      const payload = await api(`/api/data-pools/${encodeURIComponent(state.selectedDataPoolId)}/assets/delete`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      state.selectedDataPoolAssetIds.clear();
      state.dataPoolDownloadSelectionId = null;
      hideDataPoolAssetDetail({ clearSelection: true });
      if (message) message.textContent = `已删除 ${payload.deleted_count || 0} 条资产`;
      await loadDataPools();
      await loadDataPoolAssets();
    }

    function renderStage2Action(repository) {
      const stage2 = repository.stage2 || {};
      if (stage2.status === "pending" && stage2.can_run) {
        return `<button class="primary tiny stage2-run-inline" type="button" data-stage2-repo-id="${repository.id}">运行</button>`;
      }
      if ((stage2.status === "succeeded" || stage2.status === "abandoned" || stage2.status === "defect" || stage2.status === "failed") && stage2.can_rerun) {
        return `<button class="secondary tiny stage2-rerun-inline" type="button" data-stage2-repo-id="${repository.id}">重新运行</button>`;
      }
      if (stage2.status === "queued" || stage2.status === "running") {
        return `<span class="muted">${stage2.status === "queued" ? "排队中" : "运行中"}</span>`;
      }
      return `<span class="muted">-</span>`;
    }

    function renderStage2DetailAction(repository) {
      const stage2 = repository?.stage2 || {};
      if (stage2.status === "pending" && stage2.can_run) {
        return `<button class="primary tiny stage2-run-inline" type="button" data-stage2-repo-id="${repository.id}">运行</button>`;
      }
      if ((stage2.status === "succeeded" || stage2.status === "abandoned" || stage2.status === "defect" || stage2.status === "failed") && stage2.can_rerun) {
        return `<button class="secondary tiny stage2-rerun-inline" type="button" data-stage2-repo-id="${repository.id}">重新运行</button>`;
      }
      if (stage2.status === "queued" || stage2.status === "running") {
        const hasHistory = Number(stage2.history_count || 0) > 0;
        const label = hasHistory ? "重新运行" : "运行";
        const title = stage2.status === "queued"
          ? `当前 repo 已有任务排队中，暂时不能${label}。`
          : `当前 repo 正在运行，暂时不能${label}。`;
        return `<button class="secondary tiny" type="button" disabled title="${escapeHtml(title)}">${label}</button>`;
      }
      return "";
    }

    function bindStage2RunActionButtons(root) {
      root.querySelectorAll(".stage2-run-inline").forEach((button) => {
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          await withTemporaryButtonProgress(button, "提交中...", () => (
            createStage2Run(button.dataset.stage2RepoId, { rerun: false })
          ));
        });
      });
      root.querySelectorAll(".stage2-rerun-inline").forEach((button) => {
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          await withTemporaryButtonProgress(button, "提交中...", () => (
            createStage2Run(button.dataset.stage2RepoId, { rerun: true })
          ));
        });
      });
    }

    async function withTemporaryButtonProgress(button, label, callback) {
      if (!(button instanceof HTMLButtonElement) || button.disabled) {
        return callback();
      }
      const previousText = button.textContent;
      button.disabled = true;
      button.textContent = label;
      try {
        return await callback();
      } finally {
        if (button.isConnected) {
          button.disabled = false;
          button.textContent = previousText;
        }
      }
    }

    function renderStage2RuntimeReadonlyField(label, value, options = {}) {
      const {
        mono = false,
      } = options;
      const displayValue = value == null || value === "" ? "-" : String(value);
      return `
        <label>
          <span>${escapeHtml(label)}</span>
          <div class="stage2-config-readonly${mono ? " mono" : ""}">${escapeHtml(displayValue)}</div>
        </label>
      `;
    }

    function resetStage2RunRuntimeEditor() {
      state.stage2RunRuntimeEditor = {
        runId: null,
        draft: null,
        plannerApiKeyPreview: "",
        workerApiKeyPreview: "",
        message: "",
        saving: false,
      };
    }

    function stage2RuntimeSnapshotToDraft(snapshot) {
      const planner = snapshot.planner || {};
      const worker = snapshot.worker || {};
      const hyperparameters = snapshot.hyperparameters || {};
      return {
        planner_model: planner.model || "",
        planner_base_url: planner.base_url || "",
        planner_api_key: null,
        planner_preset: planner.preset || "default",
        planner_max_iterations: planner.max_iterations ?? null,
        planner_timeout_seconds: planner.timeout_seconds ?? null,
        worker_model: worker.model || "",
        worker_base_url: worker.base_url || "",
        worker_api_key: null,
        worker_preset: worker.preset || "default",
        worker_max_iterations: worker.max_iterations ?? null,
        worker_timeout_seconds: worker.timeout_seconds ?? null,
        max_worker_attempts: hyperparameters.max_worker_attempts ?? null,
        quickcheck_sample_size: hyperparameters.quickcheck_sample_size ?? null,
        collect_timeout_seconds: hyperparameters.collect_timeout_seconds ?? hyperparameters.command_timeout_seconds ?? null,
        run_test_timeout_seconds: hyperparameters.run_test_timeout_seconds ?? hyperparameters.command_timeout_seconds ?? null,
        build_timeout_seconds: hyperparameters.build_timeout_seconds ?? null,
        full_validation_timeout_seconds: hyperparameters.full_validation_timeout_seconds ?? null,
        entry_file_test_count_min: hyperparameters.entry_file_test_count_min ?? null,
        p2p_file_count_limit: hyperparameters.p2p_file_count_limit ?? null,
      };
    }

    function isEditingStage2RunRuntime(runId) {
      return String(state.stage2RunRuntimeEditor.runId || "") === String(runId || "");
    }

    function startStage2RunRuntimeEdit(run) {
      const runtime = run.runtime_snapshot || {};
      state.stage2RunRuntimeEditor = {
        runId: String(run.id),
        draft: stage2RuntimeSnapshotToDraft(runtime),
        plannerApiKeyPreview: String(runtime?.planner?.api_key_preview || ""),
        workerApiKeyPreview: String(runtime?.worker?.api_key_preview || ""),
        message: "",
        saving: false,
      };
      renderStage2RepositoryDetail(currentStage2RepositoryDetailPayload());
    }

    function cancelStage2RunRuntimeEdit(runId) {
      if (!isEditingStage2RunRuntime(runId)) {
        return;
      }
      resetStage2RunRuntimeEditor();
      renderStage2RepositoryDetail(currentStage2RepositoryDetailPayload());
    }

    function updateStage2RunRuntimeEditorDraft(field, value) {
      if (!state.stage2RunRuntimeEditor.draft) {
        return;
      }
      state.stage2RunRuntimeEditor.draft = {
        ...state.stage2RunRuntimeEditor.draft,
        [field]: value,
      };
      state.stage2RunRuntimeEditor.message = "";
    }

    function readStage2RunRuntimeConfigForm() {
      return {
        planner_model: $("#stage2-run-planner-model").value.trim() || null,
        planner_base_url: $("#stage2-run-planner-base-url").value.trim() || null,
        planner_api_key: $("#stage2-run-planner-api-key").value.trim() || null,
        planner_preset: $("#stage2-run-planner-preset").value || null,
        planner_max_iterations: readOptionalNumber("#stage2-run-planner-max-iterations"),
        planner_timeout_seconds: readOptionalNumber("#stage2-run-planner-timeout"),
        worker_model: $("#stage2-run-worker-model").value.trim() || null,
        worker_base_url: $("#stage2-run-worker-base-url").value.trim() || null,
        worker_api_key: $("#stage2-run-worker-api-key").value.trim() || null,
        worker_preset: $("#stage2-run-worker-preset").value || null,
        worker_max_iterations: readOptionalNumber("#stage2-run-worker-max-iterations"),
        worker_timeout_seconds: readOptionalNumber("#stage2-run-worker-timeout"),
        max_worker_attempts: readOptionalNumber("#stage2-run-max-worker-attempts"),
        quickcheck_sample_size: readOptionalNumber("#stage2-run-quickcheck-sample-size"),
        collect_timeout_seconds: readOptionalNumber("#stage2-run-collect-timeout"),
        run_test_timeout_seconds: readOptionalNumber("#stage2-run-run-test-timeout"),
        build_timeout_seconds: readOptionalNumber("#stage2-run-build-timeout"),
        full_validation_timeout_seconds: readOptionalNumber("#stage2-run-full-validation-timeout"),
        entry_file_test_count_min: readOptionalNumber("#stage2-run-entry-file-test-count-min"),
        p2p_file_count_limit: readOptionalNumber("#stage2-run-p2p-file-count-limit"),
      };
    }

    async function saveStage2RunRuntimeConfig(repositoryId, runId) {
      if (!repositoryId || !runId) {
        return null;
      }
      const saveButton = document.querySelector(`[data-stage2-run-runtime-save-id="${CSS.escape(String(runId))}"]`);
      if (saveButton) {
        saveButton.disabled = true;
      }
      state.stage2RunRuntimeEditor.saving = true;
      state.stage2RunRuntimeEditor.message = "正在保存...";
      try {
        const payload = await api(
          `/api/stage2/repos/${encodeURIComponent(repositoryId)}/runs/${encodeURIComponent(runId)}/runtime`,
          {
            method: "PATCH",
            body: JSON.stringify(readStage2RunRuntimeConfigForm()),
          },
        );
        resetStage2RunRuntimeEditor();
        mergeStage2RepositorySummary(payload.repository);
        setStage2RepositoryDetailPayload(payload);
        renderStage2RepositoryDetail(currentStage2RepositoryDetailPayload());
        await loadStage2Repos();
        syncStage2RepositoryDetailStream();
        setRefreshMeta();
        return payload;
      } catch (error) {
        state.stage2RunRuntimeEditor.saving = false;
        state.stage2RunRuntimeEditor.message = `保存失败: ${error.message}`;
        renderStage2RepositoryDetail(currentStage2RepositoryDetailPayload());
        syncStage2RepositoryDetailStream();
        return null;
      } finally {
        if (saveButton) {
          saveButton.disabled = false;
        }
      }
    }

    function renderStage2RunRuntimeEditableField(label, controlHtml, options = {}) {
      const {
        noteHtml = "",
      } = options;
      return `
        <label>
          ${noteHtml ? `<span class="stage2-field-head"><span>${escapeHtml(label)}</span>${noteHtml}</span>` : `<span>${escapeHtml(label)}</span>`}
          ${controlHtml}
        </label>
      `;
    }

    function renderStage2RunRuntimeEditor(run) {
      const editor = state.stage2RunRuntimeEditor;
      const draft = editor.draft || stage2RuntimeSnapshotToDraft(run.runtime_snapshot || {});
      const plannerApiKeyPlaceholder = editor.plannerApiKeyPreview || "保留当前 Key";
      const workerApiKeyPlaceholder = editor.workerApiKeyPreview || "保留当前 Key";
      return `
        <div class="stage2-config-grid">
          <div class="stage2-config-section">Planner agent 配置</div>
          <div class="stage2-agent-config-row">
            ${renderStage2RunRuntimeEditableField("模型", `<input id="stage2-run-planner-model" data-stage2-run-runtime-field="planner_model" type="text" value="${escapeHtml(String(draft.planner_model || ""))}">`)}
            ${renderStage2RunRuntimeEditableField("BaseURL", `<input id="stage2-run-planner-base-url" data-stage2-run-runtime-field="planner_base_url" type="url" value="${escapeHtml(String(draft.planner_base_url || ""))}">`)}
            ${renderStage2RunRuntimeEditableField("APIKey", `<input id="stage2-run-planner-api-key" data-stage2-run-runtime-field="planner_api_key" type="password" autocomplete="off" placeholder="${escapeHtml(plannerApiKeyPlaceholder)}" value="${escapeHtml(String(draft.planner_api_key || ""))}">`)}
            ${renderStage2RunRuntimeEditableField("OpenHands 预设", `
              <select id="stage2-run-planner-preset" data-stage2-run-runtime-field="planner_preset">
                <option value="gpt5"${draft.planner_preset === "gpt5" ? " selected" : ""}>gpt5：patch 写文件</option>
                <option value="default"${draft.planner_preset === "default" ? " selected" : ""}>default：默认文件编辑器</option>
              </select>
            `)}
            ${renderStage2RunRuntimeEditableField("最大迭代步数", `<input id="stage2-run-planner-max-iterations" data-stage2-run-runtime-field="planner_max_iterations" type="number" min="10" max="1000" step="1" value="${escapeHtml(draft.planner_max_iterations == null ? "" : String(draft.planner_max_iterations))}">`)}
            ${renderStage2RunRuntimeEditableField("超时时限（秒）", `<input id="stage2-run-planner-timeout" data-stage2-run-runtime-field="planner_timeout_seconds" type="number" min="30" max="14400" step="1" value="${escapeHtml(draft.planner_timeout_seconds == null ? "" : String(draft.planner_timeout_seconds))}">`)}
          </div>
          <div class="stage2-config-section">Worker agent 配置</div>
          <div class="stage2-agent-config-row">
            ${renderStage2RunRuntimeEditableField("模型", `<input id="stage2-run-worker-model" data-stage2-run-runtime-field="worker_model" type="text" value="${escapeHtml(String(draft.worker_model || ""))}">`)}
            ${renderStage2RunRuntimeEditableField("BaseURL", `<input id="stage2-run-worker-base-url" data-stage2-run-runtime-field="worker_base_url" type="url" value="${escapeHtml(String(draft.worker_base_url || ""))}">`)}
            ${renderStage2RunRuntimeEditableField("APIKey", `<input id="stage2-run-worker-api-key" data-stage2-run-runtime-field="worker_api_key" type="password" autocomplete="off" placeholder="${escapeHtml(workerApiKeyPlaceholder)}" value="${escapeHtml(String(draft.worker_api_key || ""))}">`)}
            ${renderStage2RunRuntimeEditableField("OpenHands 预设", `
              <select id="stage2-run-worker-preset" data-stage2-run-runtime-field="worker_preset">
                <option value="gpt5"${draft.worker_preset === "gpt5" ? " selected" : ""}>gpt5：patch 写文件</option>
                <option value="default"${draft.worker_preset === "default" ? " selected" : ""}>default：默认文件编辑器</option>
              </select>
            `)}
            ${renderStage2RunRuntimeEditableField("最大迭代步数", `<input id="stage2-run-worker-max-iterations" data-stage2-run-runtime-field="worker_max_iterations" type="number" min="10" max="1000" step="1" value="${escapeHtml(draft.worker_max_iterations == null ? "" : String(draft.worker_max_iterations))}">`)}
            ${renderStage2RunRuntimeEditableField("超时时限（秒）", `<input id="stage2-run-worker-timeout" data-stage2-run-runtime-field="worker_timeout_seconds" type="number" min="30" max="14400" step="1" value="${escapeHtml(draft.worker_timeout_seconds == null ? "" : String(draft.worker_timeout_seconds))}">`)}
          </div>
          <div class="stage2-config-section">超参数</div>
          <div class="stage2-hyper-config-row">
            ${renderStage2RunRuntimeEditableField("validate tool 调用次数上限", `<input id="stage2-run-max-worker-attempts" data-stage2-run-runtime-field="max_worker_attempts" type="number" min="1" max="10" step="1" value="${escapeHtml(draft.max_worker_attempts == null ? "" : String(draft.max_worker_attempts))}">`)}
            ${renderStage2RunRuntimeEditableField("快检抽样数量", `<input id="stage2-run-quickcheck-sample-size" data-stage2-run-runtime-field="quickcheck_sample_size" type="number" min="1" max="100" step="1" value="${escapeHtml(draft.quickcheck_sample_size == null ? "" : String(draft.quickcheck_sample_size))}">`)}
            ${renderStage2RunRuntimeEditableField("collect 超时上限（秒）", `<input id="stage2-run-collect-timeout" data-stage2-run-runtime-field="collect_timeout_seconds" type="number" min="10" max="7200" step="1" value="${escapeHtml(draft.collect_timeout_seconds == null ? "" : String(draft.collect_timeout_seconds))}">`)}
            ${renderStage2RunRuntimeEditableField("run 单个测试文件超时上限（秒）", `<input id="stage2-run-run-test-timeout" data-stage2-run-runtime-field="run_test_timeout_seconds" type="number" min="10" max="7200" step="1" value="${escapeHtml(draft.run_test_timeout_seconds == null ? "" : String(draft.run_test_timeout_seconds))}">`)}
            ${renderStage2RunRuntimeEditableField("build 超时上限（秒）", `<input id="stage2-run-build-timeout" data-stage2-run-runtime-field="build_timeout_seconds" type="number" min="30" max="7200" step="1" value="${escapeHtml(draft.build_timeout_seconds == null ? "" : String(draft.build_timeout_seconds))}">`)}
            ${renderStage2RunRuntimeEditableField("full validation 超时上限（秒）", `<input id="stage2-run-full-validation-timeout" data-stage2-run-runtime-field="full_validation_timeout_seconds" type="number" min="30" max="28800" step="1" value="${escapeHtml(draft.full_validation_timeout_seconds == null ? "" : String(draft.full_validation_timeout_seconds))}">`)}
            ${renderStage2RunRuntimeEditableField("入口文件测试点个数下限", `<input id="stage2-run-entry-file-test-count-min" data-stage2-run-runtime-field="entry_file_test_count_min" type="number" min="-1" step="1" value="${escapeHtml(draft.entry_file_test_count_min == null ? "" : String(draft.entry_file_test_count_min))}">`)}
            ${renderStage2RunRuntimeEditableField("P2P 集合文件数量上限", `<input id="stage2-run-p2p-file-count-limit" data-stage2-run-runtime-field="p2p_file_count_limit" type="number" min="1" step="1" placeholder="不截断" value="${escapeHtml(draft.p2p_file_count_limit == null ? "" : String(draft.p2p_file_count_limit))}">`)}
          </div>
        </div>
        <div class="stage2-runtime-panel-message">${escapeHtml(editor.message || "")}</div>
      `;
    }

    function renderStage2RunRuntimePanel(run) {
      const runtime = run.runtime_snapshot || {};
      const planner = runtime.planner || {};
      const worker = runtime.worker || {};
      const hyperparameters = runtime.hyperparameters || {};
      const runtimeLabel = stage2ModelRuntimeLabel(runtime);
      const panelOpenAttr = state.stage2DetailSections.runtimeConfig ? " open" : "";
      const plannerApiKeyValue = planner.api_key_preview || (planner.api_key ? apiKeyPreview(planner.api_key) : "未归档");
      const workerApiKeyValue = worker.api_key_preview || (worker.api_key ? apiKeyPreview(worker.api_key) : "未归档");
      const isEditing = isEditingStage2RunRuntime(run.id);
      const canEdit = !run.is_active;
      const headerActions = isEditing
        ? `
          <div class="stage2-runtime-panel-actions">
            <button class="secondary tiny" type="button" data-stage2-run-runtime-cancel-id="${escapeHtml(String(run.id))}">取消</button>
            <button class="primary tiny" type="button" data-stage2-run-runtime-save-id="${escapeHtml(String(run.id))}" ${state.stage2RunRuntimeEditor.saving ? "disabled" : ""}>${state.stage2RunRuntimeEditor.saving ? "保存中..." : "保存"}</button>
          </div>
        `
        : `<button class="secondary tiny" type="button" data-stage2-run-runtime-edit-id="${escapeHtml(String(run.id))}" ${canEdit ? "" : "disabled"}>编辑</button>`;
      return `
        <details class="detail-card detail-section collapsible-card" data-stage2-detail-section="runtimeConfig"${panelOpenAttr}>
          <summary class="collapsible-summary">
            <div class="stage2-runtime-panel-head">
              <div class="stage2-runtime-panel-head-left">
                <h4>运行配置</h4>
                ${headerActions}
              </div>
              <div class="stage2-runtime-panel-summary">${escapeHtml(runtimeLabel || "展开查看该次运行冻结的 agent 与超参数配置")}</div>
            </div>
            <span class="collapse-toggle">⌃</span>
          </summary>
          <div class="collapsible-content">
            ${isEditing ? renderStage2RunRuntimeEditor(run) : `
              <div class="stage2-config-grid">
                <div class="stage2-config-section">Planner agent 配置</div>
                <div class="stage2-agent-config-row">
                  ${renderStage2RuntimeReadonlyField("模型", planner.model, { mono: true })}
                  ${renderStage2RuntimeReadonlyField("BaseURL", planner.base_url, { mono: true })}
                  ${renderStage2RuntimeReadonlyField("APIKey", plannerApiKeyValue, { mono: true })}
                  ${renderStage2RuntimeReadonlyField("OpenHands 预设", planner.preset)}
                  ${renderStage2RuntimeReadonlyField("最大迭代步数", planner.max_iterations)}
                  ${renderStage2RuntimeReadonlyField("超时时限（秒）", planner.timeout_seconds)}
                </div>
                <div class="stage2-config-section">Worker agent 配置</div>
                <div class="stage2-agent-config-row">
                  ${renderStage2RuntimeReadonlyField("模型", worker.model, { mono: true })}
                  ${renderStage2RuntimeReadonlyField("BaseURL", worker.base_url, { mono: true })}
                  ${renderStage2RuntimeReadonlyField("APIKey", workerApiKeyValue, { mono: true })}
                  ${renderStage2RuntimeReadonlyField("OpenHands 预设", worker.preset)}
                  ${renderStage2RuntimeReadonlyField("最大迭代步数", worker.max_iterations)}
                  ${renderStage2RuntimeReadonlyField("超时时限（秒）", worker.timeout_seconds)}
                </div>
                <div class="stage2-config-section">超参数</div>
                <div class="stage2-hyper-config-row">
                  ${renderStage2RuntimeReadonlyField("validate tool 调用次数上限", hyperparameters.max_worker_attempts)}
                  ${renderStage2RuntimeReadonlyField("快检抽样数量", hyperparameters.quickcheck_sample_size)}
                  ${renderStage2RuntimeReadonlyField("collect 超时上限（秒）", hyperparameters.collect_timeout_seconds ?? hyperparameters.command_timeout_seconds)}
                  ${renderStage2RuntimeReadonlyField("run 单个测试文件超时上限（秒）", hyperparameters.run_test_timeout_seconds ?? hyperparameters.command_timeout_seconds)}
                  ${renderStage2RuntimeReadonlyField("build 超时上限（秒）", hyperparameters.build_timeout_seconds)}
                  ${renderStage2RuntimeReadonlyField("full validation 超时上限（秒）", hyperparameters.full_validation_timeout_seconds)}
                  ${renderStage2RuntimeReadonlyField("入口文件测试点个数下限", hyperparameters.entry_file_test_count_min)}
                  ${renderStage2RuntimeReadonlyField("P2P 集合文件数量上限", hyperparameters.p2p_file_count_limit)}
                </div>
              </div>
            `}
          </div>
        </details>
      `;
    }

    function renderStage2SummaryGrid(run) {
      const summary = run.summary || {};
      const passedFiles = Number(summary.passed_files);
      const passedTests = Number(summary.passed_tests);
      const averagePassedTestsPerPassedFile = (
        Number.isFinite(passedFiles)
        && passedFiles > 0
        && Number.isFinite(passedTests)
      )
        ? formatAverage(passedTests / passedFiles)
        : "-";
      const metrics = [
        { label: "状态", value: stage2StateLabel(run) },
        { label: "耗时", value: formatDuration(run.duration_seconds) },
        { label: "TOKEN", value: stage2TokenDisplayText(run), multiline: true },
        { label: "Base Image", value: run.base_image || "-" },
        { label: "单元测试文件数量", value: summary.total_files ?? "-" },
        { label: "单元测试通过数", value: summary.passed_files ?? "-" },
        { label: "通过的测试点总数", value: summary.passed_tests ?? "-" },
        { label: "平均单个通过的单元测试的测试点数量", value: averagePassedTestsPerPassedFile },
      ];
      return `
        <div class="stage2-summary-grid">
          ${metrics.map((metric) => `
            <div class="stage2-metric">
              <div class="label">${escapeHtml(metric.label)}</div>
              <strong${metric.multiline ? ' class="multiline"' : ""}>${escapeHtml(String(metric.value))}</strong>
            </div>
          `).join("")}
        </div>
      `;
    }

    function renderStage2RunOverview(run) {
      return `
        <div class="stage2-summary-stack">
          ${renderStage2RunRuntimePanel(run)}
          ${renderStage2SummaryGrid(run)}
        </div>
      `;
    }

    const STAGE2_EVENT_TAIL_LIMIT = 12;
    const STAGE2_PROGRESS_OPERATION_BY_PARENT_TITLE = {
      "Repository cache clone started": "cache_clone",
      "Repository cache refresh started": "cache_refresh",
      "Requested commit fetch started": "requested_commit_fetch",
      "Repository checkout materializing": "checkout_materialize",
      "Planner sandbox preparing": "openhands_bridge_planner",
      "Worker base image preparing": "openhands_bridge_worker",
      "Worker sandbox preparing": "openhands_bridge_worker",
      "Breaker sandbox starting": "openhands_bridge_breaker",
      "OpenHands bridge initialized": "openhands_bridge_breaker",
      "Stage3 runtime image preparation started": "stage3_host_setup_work",
      "Stage3 runtime image building": "stage3_runtime_image",
      "Breaker agent-server image preparation started": "stage3_host_setup_work",
    };

    function isStage2GitProgressEvent(event) {
      const title = String(event?.title || "");
      return title.endsWith(" progress") && Boolean(event?.payload?.operation);
    }

    function isStage2GitRetryEvent(event) {
      const title = String(event?.title || "");
      return title.endsWith(" retrying") && Boolean(event?.payload?.operation);
    }

    function isStage2BridgeOutputEvent(event) {
      return event?.title === "OpenHands bridge output" && Boolean(event?.payload?.operation);
    }

    function isStage2ValidateOutputEvent(event) {
      return Boolean(event?.payload?.operation) && Array.isArray(event?.payload?.tail_lines);
    }

    function isStage2FoldedProgressEvent(event) {
      return (
        isStage2GitProgressEvent(event)
        || isStage2GitRetryEvent(event)
        || isStage2BridgeOutputEvent(event)
        || isStage2ValidateOutputEvent(event)
      );
    }

    function stage2ProgressTailLines(event) {
      const payload = event.payload || {};
      if (isStage2GitRetryEvent(event)) {
        return [`retry ${payload.next_attempt || "?"}/${payload.max_attempts || "?"}: ${event.message || ""}`];
      }
      if (Array.isArray(payload.tail_lines)) {
        return payload.tail_lines.map((line) => String(line || "")).filter(Boolean);
      }
      return [payload.raw_line || event.message || ""].filter(Boolean);
    }

    function stage2ProgressTailLabel(operation) {
      const value = String(operation || "");
      if (value.startsWith("openhands_llm_retry")) {
        return "最近 LLM 重试";
      }
      if (value.startsWith("openhands_bridge") || value.startsWith("validate_")) {
        return "最近运行输出";
      }
      return "最近进度";
    }

    function isHiddenSdkEventForUi(event) {
      const sdkEventType = String(event?.payload?.sdk_event_type || "");
      return (
        sdkEventType === "ConversationStateUpdateEvent" ||
        sdkEventType === "LLMCompletionLogEvent"
      );
    }

    function displayStage2Events(events) {
      const displayEvents = [];
      const latestParentByOperation = new Map();
      for (const event of events || []) {
        if (isHiddenSdkEventForUi(event)) {
          continue;
        }
        const operation = event?.payload?.operation;
        if (isStage2FoldedProgressEvent(event)) {
          const parent = latestParentByOperation.get(operation);
          if (parent) {
            parent.progressTail.push(...stage2ProgressTailLines(event));
            parent.progressTailLabel = stage2ProgressTailLabel(operation);
            if (parent.progressTail.length > STAGE2_EVENT_TAIL_LIMIT) {
              parent.progressTail = parent.progressTail.slice(-STAGE2_EVENT_TAIL_LIMIT);
            }
            continue;
          }
        }
        const displayEvent = {
          ...event,
          progressTail: [],
          progressTailLabel: "",
        };
        displayEvents.push(displayEvent);
        const parentOperation = event?.payload?.parent_operation || STAGE2_PROGRESS_OPERATION_BY_PARENT_TITLE[event.title];
        if (parentOperation) {
          latestParentByOperation.set(parentOperation, displayEvent);
        }
      }
      return displayEvents;
    }

    function renderStage2EventPayload(event) {
      if (!event.payload || Object.keys(event.payload).length === 0) {
        return "";
      }
      const payload = { ...event.payload };
      delete payload.operation;
      delete payload.parent_operation;
      delete payload.tail_lines;
      delete payload.stdout_log;
      delete payload.stderr_log;
      if (Object.keys(payload).length === 0) {
        return "";
      }
      return `<pre>${escapeHtml(JSON.stringify(payload, null, 2))}</pre>`;
    }

    function renderStage2EventTail(event) {
      if (!event.progressTail || event.progressTail.length === 0) {
        return "";
      }
      const label = event.progressTailLabel || "最近进度";
      return `<div class="stage2-event-tail"><div class="stage2-event-tail-head">${escapeHtml(label)}</div><code>${escapeHtml(event.progressTail.join("\n"))}</code></div>`;
    }

    function stage2EventKey(event, index) {
      return String(event?.id ?? `${event?.created_at || ""}:${event?.title || ""}:${index}`);
    }

    function stage2EventSignature(event) {
      return JSON.stringify({
        id: event?.id ?? null,
        title: event?.title || "",
        message: event?.message || "",
        progressTailLabel: event?.progressTailLabel || "",
        progressTail: event?.progressTail || [],
        tokenUsageTotal: event?.payload?.token_usage_total ?? null,
      });
    }

    function renderStage2EventItem(event, index, { phaseLabel = stage2PhaseLabel } = {}) {
      const eventKey = stage2EventKey(event, index);
      return `
        <div class="stage2-event" data-stage2-event-key="${escapeHtml(eventKey)}">
          <div class="stage2-event-head">
            <div class="stage2-event-title">${escapeHtml(event.title)}</div>
            <div class="stage2-event-meta">${escapeHtml(event.actor || "-")} · ${escapeHtml(phaseLabel(event.phase))} · ${escapeHtml(formatDate(event.created_at))}</div>
          </div>
          <div>${escapeHtml(event.message || "")}</div>
          ${renderStage2EventTail(event)}
          ${renderStage2EventPayload(event)}
        </div>
      `;
    }

    function renderStage2EventItems(displayEvents, options = {}) {
      return displayEvents.map((event, index) => renderStage2EventItem(event, index, options)).join("");
    }

    function renderStage2Events(events, options = {}) {
      if (!events || events.length === 0) {
        return `<div class="stage2-empty">当前还没有运行轨迹。</div>`;
      }
      const displayEvents = displayStage2Events(events);
      if (displayEvents.length === 0) {
        return `<div class="stage2-empty">当前还没有可展示的运行轨迹。</div>`;
      }
      return `
        <div class="stage2-event-shell">
          <div class="stage2-event-list">
            ${renderStage2EventItems(displayEvents, options)}
          </div>
        </div>
      `;
    }

    function renderStage2TestResults(results) {
      if (!results || results.length === 0) {
        return `<div class="stage2-empty">当前没有文件级全面跑结果。</div>`;
      }
      return `
        <table>
          <thead>
            <tr>
              <th>测试文件</th>
              <th>状态</th>
              <th>总数</th>
              <th>通过</th>
              <th>失败</th>
              <th>Error</th>
              <th>Skipped</th>
            </tr>
          </thead>
          <tbody>
            ${results.map((item) => `
              <tr>
                <td class="mono">${escapeHtml(item.test_file_path)}</td>
                <td>${statusPill(item.status)}</td>
                <td>${item.total_tests}</td>
                <td>${item.passed_tests}</td>
                <td>${item.failed_tests}</td>
                <td>${item.error_tests}</td>
                <td>${item.skipped_tests}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
    }

    function hasStage2JsonContent(value) {
      return Boolean(value) && typeof value === "object" && Object.keys(value).length > 0;
    }

    function buildStage2AssetVersions(attempts, field, { kind, emptyText, draftAttempt = null }) {
      const versions = attempts.map((attempt) => {
        const value = attempt[field];
        const available = kind === "json" ? hasStage2JsonContent(value) : Boolean(value);
        return {
          key: String(attempt.attempt_index),
          attemptIndex: attempt.attempt_index,
          label: `版本 ${attempt.attempt_index}`,
          value: kind === "json" ? (value || {}) : (value || ""),
          available,
          emptyText,
          validatorStatus: attempt.validator_status || "",
          createdAt: attempt.created_at || null,
          isDraft: false,
        };
      });
      if (draftAttempt) {
        const draftValue = draftAttempt[field];
        const draftAvailable = kind === "json" ? hasStage2JsonContent(draftValue) : Boolean(draftValue);
        versions.push({
          key: `draft:${field}`,
          attemptIndex: draftAttempt.attempt_index || null,
          label: String(draftAttempt.label || "临时文件"),
          value: kind === "json" ? (draftValue || {}) : (draftValue || ""),
          available: draftAvailable,
          emptyText,
          validatorStatus: draftAttempt.validator_status || "",
          createdAt: draftAttempt.updated_at || draftAttempt.created_at || null,
          isDraft: true,
        });
      }
      return versions;
    }

    function makeVersionedStage2Asset({ key, title, kind, emptyText, versions }) {
      const stableAvailableCount = versions.filter((version) => version.available && !version.isDraft).length;
      const draftAvailable = versions.some((version) => version.available && version.isDraft);
      let meta = "暂无";
      if (stableAvailableCount <= 0 && draftAvailable) {
        meta = "临时文件";
      } else if (stableAvailableCount === 1 && !draftAvailable) {
        meta = "已产出";
      } else if (stableAvailableCount > 1 && !draftAvailable) {
        meta = `已产出 ${stableAvailableCount} 个 version`;
      } else if (stableAvailableCount > 0 && draftAvailable) {
        meta = `已产出 ${stableAvailableCount} 个 version + 临时文件`;
      }
      return {
        key,
        title,
        kind,
        available: stableAvailableCount > 0 || draftAvailable,
        meta,
        emptyText,
        versions,
      };
    }

    function selectedStage2AssetVersion(asset) {
      if (!asset.versions || asset.versions.length === 0) {
        return null;
      }
      const latestVersion = asset.versions[asset.versions.length - 1];
      const selectedKey = state.selectedStage2AssetVersionByKey[asset.key];
      const selectedVersion = asset.versions.find((version) => String(version.key) === String(selectedKey));
      if (selectedVersion) {
        return selectedVersion;
      }
      return latestVersion;
    }

    function stage2AssetPreviewModel(asset) {
      const selectedVersion = selectedStage2AssetVersion(asset);
      if (!selectedVersion) {
        return asset;
      }
      return {
        ...asset,
        available: selectedVersion.available,
        meta: selectedVersion.label || `版本 ${selectedVersion.attemptIndex}`,
        value: selectedVersion.value,
        emptyText: selectedVersion.emptyText || asset.emptyText,
        selectedVersion,
      };
    }

    function renderStage2AssetVersions(asset) {
      if (!asset.versions || asset.versions.length === 0) {
        return "";
      }
      const selectedVersion = selectedStage2AssetVersion(asset);
      return `
        <div class="stage2-asset-version-strip">
          ${asset.versions.map((version) => `
            <button
              class="stage2-asset-version-chip${String(version.key) === String(selectedVersion?.key) ? " active" : ""}"
              type="button"
              data-stage2-asset-version="${escapeHtml(version.key)}"
            >${escapeHtml(version.label || `版本 ${String(version.attemptIndex)}`)}</button>
          `).join("")}
        </div>
      `;
    }

    function stage2CompletionFiles(asset) {
      return [...(Array.isArray(asset?.files) ? asset.files : [])].sort((left, right) => {
        const leftTime = Date.parse(left.modified_at || "") || 0;
        const rightTime = Date.parse(right.modified_at || "") || 0;
        if (leftTime !== rightTime) {
          return rightTime - leftTime;
        }
        return String(right.path || "").localeCompare(String(left.path || ""));
      });
    }

    function selectedStage2CompletionFile(asset) {
      const files = stage2CompletionFiles(asset);
      if (files.length === 0) {
        return null;
      }
      const selectedPath = state.selectedStage2CompletionFileByAssetKey[asset.key];
      return files.find((file) => String(file.path) === String(selectedPath)) || files[0];
    }

    function stage2CompletionFileCacheKey(asset, filePath) {
      return [
        asset?.repositoryId || "",
        asset?.runId || "",
        asset?.archiveRole || "",
        filePath || "",
      ].join("::");
    }

    function currentSelectedStage2Asset() {
      const payload = currentStage2RepositoryDetailPayload();
      const run = payload.selected_run;
      if (!run) {
        return null;
      }
      const assets = buildStage2RunAssets(run);
      const selectedKey = state.selectedStage2AssetKey || assets[0]?.key || null;
      return assets.find((asset) => asset.key === selectedKey) || assets[0] || null;
    }

    function stage2CompletionArchiveAsset(run, role, title) {
      const archives = run.llm_completion_archives || {};
      const archive = archives[role] || {};
      const fileCount = Number(archive.file_count || 0);
      const files = Array.isArray(archive.files) ? archive.files : [];
      const repository = currentStage2RepositoryDetailPayload()?.repository;
      const repositoryId = repository?.id;
      const runId = run?.id;
      return {
        key: `${role}_llm_completions`,
        title,
        kind: "completion_archive",
        available: fileCount > 0,
        meta: fileCount > 0 ? `${fileCount} 个文件` : "暂无",
        emptyText: `当前还没有 ${title}。`,
        value: fileCount > 0 ? archive : {},
        files,
        repositoryId,
        runId,
        archiveRole: role,
        downloadUrl: (fileCount > 0 && repositoryId && runId)
          ? `/api/stage2/repos/${encodeURIComponent(repositoryId)}/runs/${encodeURIComponent(runId)}/llm-completions/${role}.zip`
          : "",
      };
    }

    function buildStage2RunAssets(run) {
      const results = Array.isArray(run.test_results) ? run.test_results : [];
      const attempts = Array.isArray(run.validation_attempts) ? run.validation_attempts : [];
      const draftAttempt = run.draft_validation_attempt && typeof run.draft_validation_attempt === "object"
        ? run.draft_validation_attempt
        : null;
      const plannerUpdDockerfile = String(run.planner_upd_dockerfile || "");
      return [
        {
          key: "planner_guidance",
          title: "顶层指导",
          kind: "text",
          available: Boolean(run.planner_guidance),
          meta: run.planner_guidance ? "已产出" : "暂无",
          emptyText: "当前没有 planner guidance。",
          value: run.planner_guidance || "",
        },
        ...(plannerUpdDockerfile ? [{
          key: "planner_upd_dockerfile",
          title: "建议 Base Image Dockerfile",
          kind: "text",
          available: true,
          meta: "已产出",
          emptyText: "当前没有 base image 提案 Dockerfile。",
          value: plannerUpdDockerfile,
        }] : []),
        makeVersionedStage2Asset({
          key: "dockerfile_text",
          title: "Dockerfile",
          kind: "text",
          emptyText: "当前没有 Dockerfile 产物。",
          versions: buildStage2AssetVersions(attempts, "dockerfile_text", {
            kind: "text",
            emptyText: "该版本没有 Dockerfile 产物。",
            draftAttempt,
          }),
        }),
        makeVersionedStage2Asset({
          key: "run_script_text",
          title: "run_script.sh",
          kind: "text",
          emptyText: "当前没有 run_script.sh 产物。",
          versions: buildStage2AssetVersions(attempts, "run_script_text", {
            kind: "text",
            emptyText: "该版本没有 run_script.sh 产物。",
            draftAttempt,
          }),
        }),
        makeVersionedStage2Asset({
          key: "collect_report",
          title: "collect 报告",
          kind: "json",
          emptyText: "当前还没有 collect 报告。",
          versions: buildStage2AssetVersions(attempts, "collect_report", {
            kind: "json",
            emptyText: "该版本没有 collect 报告。",
            draftAttempt,
          }),
        }),
        makeVersionedStage2Asset({
          key: "smoke_report",
          title: "smoke 报告",
          kind: "json",
          emptyText: "当前还没有 smoke 报告。",
          versions: buildStage2AssetVersions(attempts, "smoke_report", {
            kind: "json",
            emptyText: "该版本没有 smoke 报告。",
            draftAttempt,
          }),
        }),
        {
          key: "full_report",
          title: "full validation 报告",
          kind: "json",
          available: hasStage2JsonContent(run.full_report),
          meta: hasStage2JsonContent(run.full_report) ? "已产出" : "暂无",
          emptyText: "当前还没有 full validation 报告。",
          value: run.full_report || {},
        },
        {
          key: "test_results",
          title: "full validation 表格",
          kind: "table",
          available: results.length > 0,
          meta: results.length > 0 ? "已产出" : "暂无",
          emptyText: "当前没有文件级全面跑结果。",
          value: results,
        },
        stage2CompletionArchiveAsset(run, "planner", "Planner 原始 LLM completion"),
        stage2CompletionArchiveAsset(run, "worker", "Worker 原始 LLM completion"),
      ];
    }

    function renderStage2CompletionArchivePreview(asset) {
      if (!asset.available) {
        return `<div class="stage2-empty">${escapeHtml(asset.emptyText)}</div>`;
      }
      const files = stage2CompletionFiles(asset);
      if (files.length === 0) {
        return `<div class="stage2-empty">当前没有可预览的 completion JSON 文件。</div>`;
      }
      const selectedFile = selectedStage2CompletionFile(asset);
      const selectedPath = selectedFile?.path || "";
      const cacheKey = stage2CompletionFileCacheKey(asset, selectedPath);
      const cached = state.stage2CompletionFileCache[cacheKey];
      let preview = `<div class="stage2-empty">正在加载 ${escapeHtml(selectedFile?.path || "JSON 文件")}...</div>`;
      if (cached?.error) {
        preview = `<div class="stage2-empty">读取失败：${escapeHtml(cached.error)}</div>`;
      } else if (cached?.payload) {
        if (cached.payload.kind === "json") {
          preview = `<pre>${escapeHtml(JSON.stringify(cached.payload.value, null, 2))}</pre>`;
        } else {
          preview = `<pre>${escapeHtml(cached.payload.text || "")}</pre>`;
        }
      }
      return `
        <div class="stage2-completion-browser">
          <div class="stage2-completion-file-list">
            ${files.map((file) => {
              const path = String(file.path || "");
              const fileName = path.split("/").filter(Boolean).pop() || path;
              return `
                <button
                  class="stage2-completion-file-item${path === selectedPath ? " active" : ""}"
                  type="button"
                  data-stage2-completion-file-path="${escapeHtml(path)}"
                >
                  <div class="stage2-completion-file-name">${escapeHtml(fileName)}</div>
                  <div class="stage2-completion-file-meta">${escapeHtml(formatBytes(file.size_bytes))} · ${escapeHtml(formatDate(file.modified_at))}</div>
                </button>
              `;
            }).join("")}
          </div>
          <div class="stage2-completion-preview">
            ${preview}
          </div>
        </div>
      `;
    }

    function renderStage2AssetPreview(asset) {
      if (asset.kind === "text") {
        return asset.available
          ? `<pre>${escapeHtml(asset.value)}</pre>`
          : `<div class="stage2-empty">${escapeHtml(asset.emptyText)}</div>`;
      }
      if (asset.kind === "json") {
        return renderJsonPre(asset.value, asset.emptyText);
      }
      if (asset.kind === "table") {
        return renderStage2TestResults(asset.value);
      }
      if (asset.kind === "completion_archive") {
        return renderStage2CompletionArchivePreview(asset);
      }
      return `<div class="stage2-empty">当前没有可预览的资产内容。</div>`;
    }

    async function ensureStage2CompletionFileLoaded(asset) {
      if (!asset || asset.kind !== "completion_archive" || !asset.available) {
        return;
      }
      const selectedFile = selectedStage2CompletionFile(asset);
      if (!selectedFile?.path || !asset.repositoryId || !asset.runId || !asset.archiveRole) {
        return;
      }
      const cacheKey = stage2CompletionFileCacheKey(asset, selectedFile.path);
      const cached = state.stage2CompletionFileCache[cacheKey];
      if (cached?.loading || cached?.payload) {
        return;
      }
      state.stage2CompletionFileCache[cacheKey] = { loading: true };
      try {
        const params = new URLSearchParams({ path: selectedFile.path });
        const payload = await api(
          `/api/stage2/repos/${encodeURIComponent(asset.repositoryId)}/runs/${encodeURIComponent(asset.runId)}/llm-completions/${encodeURIComponent(asset.archiveRole)}/files?${params.toString()}`,
        );
        state.stage2CompletionFileCache[cacheKey] = { payload };
      } catch (error) {
        state.stage2CompletionFileCache[cacheKey] = { error: error.message || String(error) };
      }
      const currentAsset = currentSelectedStage2Asset();
      const currentFile = currentAsset ? selectedStage2CompletionFile(currentAsset) : null;
      if (
        currentAsset?.kind === "completion_archive"
        && stage2CompletionFileCacheKey(currentAsset, currentFile?.path || "") === cacheKey
      ) {
        rerenderStage2RepositoryDetailPreservingScroll();
      }
    }

    function ensureSelectedStage2CompletionFileLoaded() {
      ensureStage2CompletionFileLoaded(currentSelectedStage2Asset());
    }

    function stage2CompletionAssetCopyText(asset) {
      if (!asset || asset.kind !== "completion_archive" || !asset.available) {
        return "";
      }
      const selectedFile = selectedStage2CompletionFile(asset);
      if (!selectedFile?.path) {
        return "";
      }
      const cacheKey = stage2CompletionFileCacheKey(asset, selectedFile.path);
      const cached = state.stage2CompletionFileCache[cacheKey];
      if (!cached?.payload) {
        return "";
      }
      if (cached.payload.kind === "json") {
        return JSON.stringify(cached.payload.value, null, 2);
      }
      return String(cached.payload.text || "");
    }

    function stage2AssetCopyText(asset) {
      if (!asset?.available) {
        return "";
      }
      if (asset.kind === "text") {
        return String(asset.value || "");
      }
      if (asset.kind === "json") {
        return JSON.stringify(asset.value || {}, null, 2);
      }
      if (asset.kind === "completion_archive") {
        return stage2CompletionAssetCopyText(asset);
      }
      return "";
    }

    async function copyTextToClipboard(text) {
      const value = String(text || "");
      if (navigator.clipboard?.writeText && window.isSecureContext) {
        await navigator.clipboard.writeText(value);
        return;
      }
      const textarea = document.createElement("textarea");
      textarea.value = value;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.left = "-9999px";
      textarea.style.top = "0";
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      try {
        if (!document.execCommand("copy")) {
          throw new Error("document.execCommand copy returned false");
        }
      } finally {
        textarea.remove();
      }
    }

    async function copySelectedStage2Asset(button) {
      const asset = currentSelectedStage2Asset();
      const previewAsset = asset ? stage2AssetPreviewModel(asset) : null;
      const text = stage2AssetCopyText(previewAsset);
      if (!text) {
        return;
      }
      const originalText = button.textContent;
      try {
        await copyTextToClipboard(text);
        button.textContent = "已复制";
        window.setTimeout(() => {
          if (button.isConnected) {
            button.textContent = originalText || "复制";
          }
        }, 1200);
      } catch (error) {
        alert(`复制失败: ${error.message || String(error)}`);
      }
    }

    function renderStage2AssetDownloadAction(asset) {
      if (!asset?.available || !asset.downloadUrl) {
        return "";
      }
      return `
        <a
          class="secondary tiny stage2-asset-download"
          href="${escapeHtml(asset.downloadUrl)}"
          download
        >下载</a>
      `;
    }

    function renderStage2AssetCopyAction(asset) {
      if (!stage2AssetCopyText(asset)) {
        return "";
      }
      return `
        <button
          class="secondary tiny stage2-asset-copy"
          type="button"
          data-stage2-asset-copy
        >复制</button>
      `;
    }

    function renderStage2AssetBrowser(run) {
      const assets = buildStage2RunAssets(run);
      const validKeys = new Set(assets.map((asset) => asset.key));
      if (!validKeys.has(state.selectedStage2AssetKey)) {
        state.selectedStage2AssetKey = (assets.find((asset) => asset.available) || assets[0] || {}).key || null;
      }
      const selectedAsset = assets.find((asset) => asset.key === state.selectedStage2AssetKey) || assets[0] || null;
      const previewAsset = selectedAsset ? stage2AssetPreviewModel(selectedAsset) : null;
      const availableCount = assets.filter((asset) => asset.available).length;
      if (!selectedAsset) {
        return `<div class="stage2-empty">当前没有输出资产。</div>`;
      }
      return `
        <div class="stage2-asset-browser">
          <div class="stage2-asset-panel">
            <div class="stage2-asset-panel-head">
              <h5>输出资产</h5>
              <div class="stage2-asset-panel-note">${assets.length} 个资产 · ${availableCount} 个已生成</div>
            </div>
            <div class="stage2-asset-list">
              ${assets.map((asset) => `
                <button
                  class="stage2-asset-item${asset.key === state.selectedStage2AssetKey ? " active" : ""}"
                  type="button"
                  data-stage2-asset-key="${asset.key}"
                >
                  <div class="stage2-asset-item-title">${escapeHtml(asset.title)}</div>
                  <div class="stage2-asset-item-meta">${escapeHtml(asset.meta)}</div>
                </button>
              `).join("")}
            </div>
          </div>
          <div class="stage2-asset-panel">
            <div class="stage2-asset-panel-head">
              <div class="stage2-asset-preview-main">
                <div class="stage2-asset-preview-title">${escapeHtml(selectedAsset.title)}</div>
                ${renderStage2AssetVersions(selectedAsset)}
                ${renderStage2AssetCopyAction(previewAsset)}
                ${renderStage2AssetDownloadAction(previewAsset)}
              </div>
              <div class="stage2-asset-panel-note">${escapeHtml(previewAsset.meta)}</div>
            </div>
            <div class="stage2-asset-preview-body">
              ${renderStage2AssetPreview(previewAsset)}
            </div>
          </div>
        </div>
      `;
    }

    function buildStage2RunNumberById(runs) {
      const sortedRuns = [...(runs || [])].sort((left, right) => {
        const leftTime = Date.parse(left.created_at || "") || 0;
        const rightTime = Date.parse(right.created_at || "") || 0;
        if (leftTime !== rightTime) {
          return leftTime - rightTime;
        }
        return String(left.id || "").localeCompare(String(right.id || ""));
      });
      return new Map(sortedRuns.map((run, index) => [String(run.id), index + 1]));
    }

    function buildStage3RunNumberById(runs) {
      return buildStage2RunNumberById(runs);
    }

    function buildStage4RunNumberById(runs) {
      return buildStage2RunNumberById(runs);
    }

    const STAGE2_RUN_CARD_ACTION_SELECTOR = [
      "[data-stage2-run-delete-id]",
      "[data-stage2-run-interrupt-id]",
      "[data-stage2-run-resume-id]",
      "[data-stage2-run-rerun-id]",
    ].join(",");

    function renderStage2RunCard(run, runNumber) {
      const active = String(run.id) === String(state.selectedStage2RunId);
      const runDisplayStatus = run.display_status || run.status || "failed";
      const shortCommit = run.target_commit_sha ? run.target_commit_sha.slice(0, 7) : "-";
      const runId = escapeHtml(String(run.id));
      const deleteDisabled = run.is_active ? 'disabled title="运行中不能删除"' : 'title="删除这次运行的所有存档"';
      const interruptDisabled = run.is_active ? 'title="中断这次正在运行的任务"' : 'disabled title="只有运行中或排队中的任务可以中断"';
      const resumeDisabled = run.is_active
        ? 'disabled title="运行中不能续跑"'
        : (run.can_resume_worker
          ? (run.resume_kind === "full_validation"
            ? 'title="从 smoke 通过后的保存点继续 full test"'
            : 'title="从最近一个 validate 保存点续跑 worker"')
          : (run.can_rerun_worker
            ? 'title="复用这次运行的 planner 结果，重新启动 worker 阶段"'
            : 'disabled title="这次运行没有可用的续跑上下文"'));
      const rerunDisabled = run.is_active
        ? 'disabled title="运行中不能基于该 commit 重新运行"'
        : (!run.target_commit_sha
          ? 'disabled title="这次运行没有记录 commit"'
          : 'title="基于这张卡片的 commit 创建一次新的运行"');
      return `
        <div
          class="stage2-history-card${active ? " active" : ""}"
          data-stage2-run-id="${runId}"
          role="button"
          tabindex="0"
        >
          <div class="stage2-history-card-main">
            <div class="stage2-history-card-title-row">
              <div class="stage2-history-card-title">运行 ${runNumber}</div>
              <div class="stage2-history-display-status ${escapeHtml(runDisplayStatus)}">${escapeHtml(stage2StatusLabel(runDisplayStatus))}</div>
            </div>
            <div class="stage2-history-card-meta">
              <div>创建于 ${escapeHtml(formatDate(run.created_at))}</div>
              <div>commit：<span class="mono">${escapeHtml(shortCommit)}</span></div>
              <div>耗时：${escapeHtml(formatDuration(run.duration_seconds))}</div>
            </div>
          </div>
          <div class="stage2-history-card-actions">
            <button
              class="stage2-history-action stage2-history-interrupt"
              type="button"
              data-stage2-run-interrupt-id="${runId}"
              ${interruptDisabled}
            >中断</button>
            <button
              class="stage2-history-action stage2-history-resume"
              type="button"
              data-stage2-run-resume-id="${runId}"
              ${resumeDisabled}
            >续跑</button>
            <button
              class="stage2-history-action stage2-history-rerun"
              type="button"
              data-stage2-run-rerun-id="${runId}"
              ${rerunDisabled}
            >重新运行</button>
            <button
              class="stage2-history-action stage2-history-delete"
              type="button"
              data-stage2-run-delete-id="${runId}"
              ${deleteDisabled}
            >删除</button>
          </div>
        </div>
      `;
    }

    function renderStage2HistoryStrip(runs, runNumberById) {
      return `
        <div class="stage2-history-strip">
          ${runs.map((run, index) => (
            renderStage2RunCard(run, runNumberById.get(String(run.id)) ?? index + 1)
          )).join("")}
        </div>
      `;
    }

    function renderStage2RunError(run) {
      return run.error_message ? `
        <div class="stage2-section">
          <h5>错误信息</h5>
          <pre>${escapeHtml(run.error_message)}</pre>
        </div>
      ` : "";
    }

    function renderStage2Run(run, runNumber) {
      return `
        <section class="detail-card detail-section stage2-run" data-stage2-run-panel-id="${escapeHtml(String(run.id))}">
          <div class="stage2-run-header">
            <div>
              <h4>运行 ${runNumber}</h4>
            </div>
            <div class="muted mono">${escapeHtml(run.id)}</div>
          </div>

          <div data-stage2-summary-slot>
            ${renderStage2RunOverview(run)}
          </div>

          <div class="stage2-section" data-stage2-assets-slot>
            ${renderStage2AssetBrowser(run)}
          </div>

          <div class="stage2-section">
            <h5>运行轨迹</h5>
            <div data-stage2-events-slot>
              ${renderStage2Events(run.events)}
            </div>
          </div>

          <div data-stage2-error-slot>
            ${renderStage2RunError(run)}
          </div>
        </section>
      `;
    }

    function setRefreshMeta() {
      const now = new Date();
      state.lastRefresh = now;
      $("#refresh-meta").textContent = `最近刷新 ${now.toLocaleString()}`;
    }

    function isBatchTaskListRealtimeEligible() {
      return (state.batchTasks || []).some((task) => isBatchTaskRealtimeEligible(task));
    }

    function isStage4SourceRealtimeEligible(group) {
      const status = String(group?.status || "");
      return status === "queued" || status === "running";
    }

    function shouldAutoRefresh() {
      if (hasActiveTextSelection()) {
        return false;
      }
      switch (state.activeTab) {
        case "batch":
          return isBatchTaskListRealtimeEligible() || isBatchTaskRealtimeEligible(state.batchTaskDetailPayload);
        case "stage1":
          return state.jobs.some((job) => isStage1JobRealtimeEligible(job));
        case "stage2":
          return state.stage2Repos.some((repo) => isStage2RepositoryRealtimeEligible(repo))
            || isStage2RepositoryRealtimeEligible(currentStage2RepositoryDetailPayload().repository);
        case "stage3": {
          const selectedStage3Run = currentStage3RepositoryDetailPayload().selected_run;
          const selectedStage3Busy = ["queued", "running"].includes(String(selectedStage3Run?.status || ""));
          return state.stage3Repos.some((repo) => isStage3RepositoryRealtimeEligible(repo)) || selectedStage3Busy;
        }
        case "stage4": {
          const sourceDetailPayload = currentStage4SourceDetailPayload();
          const sourceDetailBusy = Array.isArray(sourceDetailPayload?.sources)
            && sourceDetailPayload.sources.some((source) => {
              const status = stage4RunStatusFilterKey((stage4SourceRowSummary(source).savepoint || {}).latest_stage4_run);
              return status === "queued" || status === "running";
            });
          return state.stage4Sources.some((group) => isStage4SourceRealtimeEligible(group))
            || isStage4RunRealtimeEligible(currentStage4RunDetail())
            || sourceDetailBusy;
        }
        case "data-pools":
          return isBatchTaskListRealtimeEligible() || state.stage4Sources.some((group) => isStage4SourceRealtimeEligible(group));
        default:
          return false;
      }
    }

    function hasActiveTextSelection() {
      const selection = window.getSelection ? window.getSelection() : null;
      if (selection && !selection.isCollapsed && selection.toString().trim()) {
        return true;
      }
      const activeElement = document.activeElement;
      if (activeElement instanceof HTMLInputElement || activeElement instanceof HTMLTextAreaElement) {
        const { selectionStart, selectionEnd } = activeElement;
        if (
          typeof selectionStart === "number"
          && typeof selectionEnd === "number"
          && selectionStart !== selectionEnd
        ) {
          return true;
        }
      }
      return false;
    }

    function hasActiveTextSelectionWithin(target) {
      const container = typeof target === "string" ? $(target) : target;
      if (!(container instanceof Element)) {
        return false;
      }
      const selection = window.getSelection ? window.getSelection() : null;
      if (selection && !selection.isCollapsed && selection.toString().trim()) {
        if (container.contains(selection.anchorNode) || container.contains(selection.focusNode)) {
          return true;
        }
        for (let index = 0; index < selection.rangeCount; index += 1) {
          const range = selection.getRangeAt(index);
          try {
            if (range.intersectsNode(container)) {
              return true;
            }
          } catch (_error) {
            // Ignore detached/intermediate ranges and keep checking.
          }
        }
      }
      const activeElement = document.activeElement;
      if (
        (activeElement instanceof HTMLInputElement || activeElement instanceof HTMLTextAreaElement)
        && container.contains(activeElement)
      ) {
        const { selectionStart, selectionEnd } = activeElement;
        if (
          typeof selectionStart === "number"
          && typeof selectionEnd === "number"
          && selectionStart !== selectionEnd
        ) {
          return true;
        }
      }
      return false;
    }

    function mergeJobSummary(nextJob) {
      if (!nextJob) {
        return;
      }
      state.jobs = state.jobs.map((job) => (
        String(job.id) === String(nextJob.id)
          ? { ...job, ...nextJob }
          : job
      ));
    }

    function mergeStage2RepositorySummary(nextRepository) {
      if (!nextRepository) {
        return;
      }
      state.stage2Repos = state.stage2Repos.map((repository) => (
        String(repository.id) === String(nextRepository.id)
          ? { ...repository, ...nextRepository }
          : repository
      ));
    }

    function mergeStage3RepositorySummary(nextRepository) {
      if (!nextRepository) {
        return;
      }
      state.stage3Repos = state.stage3Repos.map((repository) => (
        String(repository.id) === String(nextRepository.id)
          ? { ...repository, ...nextRepository }
          : repository
      ));
    }

    function preserveStage3RepositoryPrewarmSummaries(repositories) {
      const previousSummaryByRepositoryId = new Map(
        (state.stage3Repos || []).map((repository) => [
          String(repository?.id || ""),
          repository?.stage3?.image_prewarm || null,
        ]),
      );
      return (repositories || []).map((repository) => {
        const repositoryId = String(repository?.id || "");
        const nextStage3 = repository?.stage3 || {};
        const nextSummary = nextStage3?.image_prewarm;
        const previousSummary = previousSummaryByRepositoryId.get(repositoryId);
        const latestSnapshotId = String(nextStage3?.latest_snapshot?.id || "");
        const previousSnapshotId = String(previousSummary?.snapshot_id || "");
        const snapshotMatches = !latestSnapshotId || !previousSnapshotId || latestSnapshotId === previousSnapshotId;
        if (!repositoryId || nextSummary || !previousSummary || !snapshotMatches) {
          return repository;
        }
        return {
          ...repository,
          stage3: {
            ...nextStage3,
            image_prewarm: previousSummary,
          },
        };
      });
    }

    function summarizeStage3RepositoryImagePrewarmPayload(payload) {
      const runtimeStatus = payload?.runtime_image || {};
      const agentStatus = payload?.agent_server_image || {};
      const targetStatuses = [runtimeStatus, agentStatus];
      const isRunning = (status) => Boolean(status?.in_progress)
        || ["queued", "running"].includes(String(status?.last_job_status || ""));
      const isReady = (status) => Boolean(status?.present) && !Boolean(status?.needs_update);
      const anyRunning = targetStatuses.some((status) => isRunning(status));
      const allReady = targetStatuses.every((status) => isReady(status));
      const anyCancelled = targetStatuses.some((status) => String(status?.last_job_status || "") === "cancelled");
      const anyFailed = targetStatuses.some((status) => String(status?.last_job_status || "") === "failed");
      const anyNeedsUpdate = targetStatuses.some((status) => Boolean(status?.needs_update));
      if (anyRunning) {
        return {
          snapshot_id: String(payload?.snapshot_id || ""),
          status: "running",
          label: "预热中",
          can_cancel: true,
        };
      }
      if (allReady) {
        return {
          snapshot_id: String(payload?.snapshot_id || ""),
          status: "succeeded",
          label: "已预热",
          can_cancel: false,
        };
      }
      if (anyCancelled) {
        return {
          snapshot_id: String(payload?.snapshot_id || ""),
          status: "cancelled",
          label: "已取消",
          can_cancel: false,
        };
      }
      if (anyFailed) {
        return {
          snapshot_id: String(payload?.snapshot_id || ""),
          status: "failed",
          label: "失败",
          can_cancel: false,
        };
      }
      if (anyNeedsUpdate) {
        return {
          snapshot_id: String(payload?.snapshot_id || ""),
          status: "queued",
          label: "需要更新",
          can_cancel: false,
        };
      }
      return {
        snapshot_id: String(payload?.snapshot_id || ""),
        status: "pending",
        label: "未预热",
        can_cancel: false,
      };
    }

    function syncStage3RepositoryPrewarmSummary(repositoryId, payload) {
      const normalizedRepositoryId = String(repositoryId || "").trim();
      if (!normalizedRepositoryId || !payload) {
        return;
      }
      const summary = summarizeStage3RepositoryImagePrewarmPayload(payload);
      let updatedRepository = null;
      state.stage3Repos = state.stage3Repos.map((repository) => {
        if (String(repository?.id || "") !== normalizedRepositoryId) {
          return repository;
        }
        updatedRepository = {
          ...repository,
          stage3: {
            ...(repository.stage3 || {}),
            image_prewarm: summary,
          },
        };
        return updatedRepository;
      });
      if (!updatedRepository) {
        return;
      }
      const row = document.querySelector(`#stage3-repos-body tr[data-stage3-repo-id="${CSS.escape(normalizedRepositoryId)}"]`);
      const actionCell = row?.querySelector(".stage3-repo-action-cell");
      if (actionCell) {
        actionCell.innerHTML = renderStage3RepositoryAction(updatedRepository);
        actionCell.querySelectorAll("[data-stage3-repo-run-all-id]").forEach((button) => {
          button.addEventListener("click", async (event) => {
            event.stopPropagation();
            event.preventDefault();
            await runAllStage3RepositoryEntries(button.dataset.stage3RepoRunAllId);
          });
        });
        actionCell.querySelectorAll("[data-stage3-repo-prewarm-id]").forEach((button) => {
          button.addEventListener("click", async (event) => {
            event.stopPropagation();
            event.preventDefault();
            await prewarmStage3RepositoryImages(button.dataset.stage3RepoPrewarmId);
          });
        });
        actionCell.querySelectorAll("[data-stage3-repo-prewarm-cancel-id]").forEach((button) => {
          button.addEventListener("click", async (event) => {
            event.stopPropagation();
            event.preventDefault();
            await cancelStage3RepositoryImagePrewarm(button.dataset.stage3RepoPrewarmCancelId);
          });
        });
      }
    }

    function buildStage2RepositoryDetailPayload(repository, runs, selectedRun = null) {
      return {
        repository: repository || null,
        runs: Array.isArray(runs) ? runs : [],
        selected_run: selectedRun || null,
      };
    }

    function setStage2RepositoryDetailPayload(payload) {
      state.stage2RepositoryDetailPayload = buildStage2RepositoryDetailPayload(
        payload?.repository,
        payload?.runs,
        payload?.selected_run,
      );
    }

    function currentStage2RepositoryDetailPayload() {
      return state.stage2RepositoryDetailPayload || buildStage2RepositoryDetailPayload(null, [], null);
    }

    function htmlToElement(html) {
      const template = document.createElement("template");
      template.innerHTML = html.trim();
      return template.content.firstElementChild;
    }

    function bindStage2DetailInteractiveHandlers(root) {
      root.querySelectorAll("[data-stage2-detail-section]").forEach((section) => {
        if (section.dataset.stage2DetailSectionBound === "1") {
          return;
        }
        section.dataset.stage2DetailSectionBound = "1";
        section.addEventListener("toggle", () => {
          state.stage2DetailSections[section.dataset.stage2DetailSection] = section.open;
        });
      });
      root.querySelectorAll("[data-stage2-run-delete-id]").forEach((button) => {
        if (button.dataset.stage2DeleteBound === "1") {
          return;
        }
        button.dataset.stage2DeleteBound = "1";
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          await deleteStage2Run(button.dataset.stage2RunDeleteId);
        });
      });
      root.querySelectorAll("[data-stage2-run-interrupt-id]").forEach((button) => {
        if (button.dataset.stage2InterruptBound === "1") {
          return;
        }
        button.dataset.stage2InterruptBound = "1";
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          await interruptStage2Run(button.dataset.stage2RunInterruptId);
        });
      });
      root.querySelectorAll("[data-stage2-run-resume-id]").forEach((button) => {
        if (button.dataset.stage2ResumeBound === "1") {
          return;
        }
        button.dataset.stage2ResumeBound = "1";
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          await resumeStage2WorkerRun(button.dataset.stage2RunResumeId);
        });
      });
      root.querySelectorAll("[data-stage2-run-runtime-edit-id]").forEach((button) => {
        if (button.dataset.stage2RunRuntimeEditBound === "1") {
          return;
        }
        button.dataset.stage2RunRuntimeEditBound = "1";
        button.addEventListener("click", (event) => {
          event.stopPropagation();
          const run = currentStage2RepositoryDetailPayload().selected_run;
          if (!run || String(run.id) !== String(button.dataset.stage2RunRuntimeEditId)) {
            return;
          }
          startStage2RunRuntimeEdit(run);
        });
      });
      root.querySelectorAll("[data-stage2-run-runtime-cancel-id]").forEach((button) => {
        if (button.dataset.stage2RunRuntimeCancelBound === "1") {
          return;
        }
        button.dataset.stage2RunRuntimeCancelBound = "1";
        button.addEventListener("click", (event) => {
          event.stopPropagation();
          cancelStage2RunRuntimeEdit(button.dataset.stage2RunRuntimeCancelId);
        });
      });
      root.querySelectorAll("[data-stage2-run-runtime-save-id]").forEach((button) => {
        if (button.dataset.stage2RunRuntimeSaveBound === "1") {
          return;
        }
        button.dataset.stage2RunRuntimeSaveBound = "1";
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          await saveStage2RunRuntimeConfig(
            state.selectedStage2RepositoryId,
            button.dataset.stage2RunRuntimeSaveId,
          );
        });
      });
      root.querySelectorAll("[data-stage2-run-runtime-field]").forEach((input) => {
        if (input.dataset.stage2RunRuntimeFieldBound === "1") {
          return;
        }
        input.dataset.stage2RunRuntimeFieldBound = "1";
        const syncValue = () => {
          const field = input.dataset.stage2RunRuntimeField;
          if (!field) {
            return;
          }
          let value = input.value;
          if (input.type === "number") {
            value = value.trim() === "" ? null : Number(value);
          } else if (input.type === "password") {
            value = value.trim() || null;
          } else {
            value = value.trim();
          }
          updateStage2RunRuntimeEditorDraft(field, value);
        };
        input.addEventListener("input", syncValue);
        input.addEventListener("change", syncValue);
      });
      root.querySelectorAll("[data-stage2-run-rerun-id]").forEach((button) => {
        if (button.dataset.stage2RerunBound === "1") {
          return;
        }
        button.dataset.stage2RerunBound = "1";
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          await withTemporaryButtonProgress(button, "提交中...", () => (
            rerunStage2RunCommit(button.dataset.stage2RunRerunId)
          ));
        });
      });
      root.querySelectorAll("[data-stage2-run-id]").forEach((card) => {
        if (card.dataset.stage2Bound === "1") {
          return;
        }
        card.dataset.stage2Bound = "1";
        card.addEventListener("click", async (event) => {
          if (event.target.closest(STAGE2_RUN_CARD_ACTION_SELECTOR)) {
            return;
          }
          await selectStage2Run(card.dataset.stage2RunId);
        });
        card.addEventListener("keydown", async (event) => {
          if (event.target.closest(STAGE2_RUN_CARD_ACTION_SELECTOR)) {
            return;
          }
          if (event.key !== "Enter" && event.key !== " ") {
            return;
          }
          event.preventDefault();
          await selectStage2Run(card.dataset.stage2RunId);
        });
      });
      root.querySelectorAll("[data-stage2-asset-key]").forEach((button) => {
        if (button.dataset.stage2Bound === "1") {
          return;
        }
        button.dataset.stage2Bound = "1";
        button.addEventListener("click", () => {
          state.selectedStage2AssetKey = button.dataset.stage2AssetKey;
          rerenderStage2RepositoryDetailPreservingScroll();
        });
      });
      root.querySelectorAll("[data-stage2-completion-file-path]").forEach((button) => {
        if (button.dataset.stage2Bound === "1") {
          return;
        }
        button.dataset.stage2Bound = "1";
        button.addEventListener("click", () => {
          if (!state.selectedStage2AssetKey) {
            return;
          }
          state.selectedStage2CompletionFileByAssetKey[state.selectedStage2AssetKey] = button.dataset.stage2CompletionFilePath;
          rerenderStage2RepositoryDetailPreservingScroll();
        });
      });
      root.querySelectorAll("[data-stage2-asset-version]").forEach((button) => {
        if (button.dataset.stage2Bound === "1") {
          return;
        }
        button.dataset.stage2Bound = "1";
        button.addEventListener("click", () => {
          if (!state.selectedStage2AssetKey) {
            return;
          }
          state.selectedStage2AssetVersionByKey[state.selectedStage2AssetKey] = button.dataset.stage2AssetVersion;
          rerenderStage2RepositoryDetailPreservingScroll();
        });
      });
      root.querySelectorAll("[data-stage2-asset-copy]").forEach((button) => {
        if (button.dataset.stage2Bound === "1") {
          return;
        }
        button.dataset.stage2Bound = "1";
        button.addEventListener("click", async (event) => {
          event.preventDefault();
          await copySelectedStage2Asset(button);
        });
      });
      ensureSelectedStage2CompletionFileLoaded();
    }

    function captureStage2AssetBrowserScrollState(root = $("#stage2-detail-body")) {
      const assetList = root?.querySelector(".stage2-asset-list");
      const assetVersionStrip = root?.querySelector(".stage2-asset-version-strip");
      const assetPreviewBody = root?.querySelector(".stage2-asset-preview-body");
      const completionFileList = root?.querySelector(".stage2-completion-file-list");
      const completionPreview = root?.querySelector(".stage2-completion-preview");
      return {
        assetListScrollTop: assetList?.scrollTop ?? 0,
        assetListScrollLeft: assetList?.scrollLeft ?? 0,
        assetVersionStripScrollTop: assetVersionStrip?.scrollTop ?? 0,
        assetVersionStripScrollLeft: assetVersionStrip?.scrollLeft ?? 0,
        assetPreviewScrollTop: assetPreviewBody?.scrollTop ?? 0,
        assetPreviewScrollLeft: assetPreviewBody?.scrollLeft ?? 0,
        completionFileListScrollTop: completionFileList?.scrollTop ?? 0,
        completionFileListScrollLeft: completionFileList?.scrollLeft ?? 0,
        completionPreviewScrollTop: completionPreview?.scrollTop ?? 0,
        completionPreviewScrollLeft: completionPreview?.scrollLeft ?? 0,
      };
    }

    function restoreStage2AssetBrowserScrollState(
      scrollState,
      root = $("#stage2-detail-body"),
    ) {
      if (!scrollState) {
        return;
      }
      const assetList = root?.querySelector(".stage2-asset-list");
      const assetVersionStrip = root?.querySelector(".stage2-asset-version-strip");
      const assetPreviewBody = root?.querySelector(".stage2-asset-preview-body");
      const completionFileList = root?.querySelector(".stage2-completion-file-list");
      const completionPreview = root?.querySelector(".stage2-completion-preview");
      if (assetList) {
        assetList.scrollTop = scrollState.assetListScrollTop ?? 0;
        assetList.scrollLeft = scrollState.assetListScrollLeft ?? 0;
      }
      if (assetVersionStrip) {
        assetVersionStrip.scrollTop = scrollState.assetVersionStripScrollTop ?? 0;
        assetVersionStrip.scrollLeft = scrollState.assetVersionStripScrollLeft ?? 0;
      }
      if (assetPreviewBody) {
        assetPreviewBody.scrollTop = scrollState.assetPreviewScrollTop ?? 0;
        assetPreviewBody.scrollLeft = scrollState.assetPreviewScrollLeft ?? 0;
      }
      if (completionFileList) {
        completionFileList.scrollTop = scrollState.completionFileListScrollTop ?? 0;
        completionFileList.scrollLeft = scrollState.completionFileListScrollLeft ?? 0;
      }
      if (completionPreview) {
        completionPreview.scrollTop = scrollState.completionPreviewScrollTop ?? 0;
        completionPreview.scrollLeft = scrollState.completionPreviewScrollLeft ?? 0;
      }
    }

    function captureStage2DetailScrollState() {
      const detailBody = $("#stage2-detail-body");
      const eventShell = detailBody?.querySelector(".stage2-event-shell");
      const historyStrip = detailBody?.querySelector(".stage2-history-strip-wrap");
      const eventBottomGap = eventShell
        ? eventShell.scrollHeight - eventShell.clientHeight - eventShell.scrollTop
        : null;
      return {
        windowScrollX: window.scrollX,
        windowScrollY: window.scrollY,
        detailScrollTop: detailBody?.scrollTop ?? 0,
        detailScrollLeft: detailBody?.scrollLeft ?? 0,
        eventScrollTop: eventShell?.scrollTop ?? 0,
        eventScrollLeft: eventShell?.scrollLeft ?? 0,
        eventWasNearBottom: eventBottomGap != null && eventBottomGap <= 24,
        historyScrollLeft: historyStrip?.scrollLeft ?? 0,
        ...captureStage2AssetBrowserScrollState(detailBody),
      };
    }

    function restoreStage2DetailScrollState(scrollState) {
      if (!scrollState) {
        return;
      }
      requestAnimationFrame(() => {
        const detailBody = $("#stage2-detail-body");
        const eventShell = detailBody?.querySelector(".stage2-event-shell");
        const historyStrip = detailBody?.querySelector(".stage2-history-strip-wrap");
        if (detailBody) {
          detailBody.scrollTop = scrollState.detailScrollTop;
          detailBody.scrollLeft = scrollState.detailScrollLeft;
        }
        if (eventShell) {
          eventShell.scrollTop = scrollState.eventWasNearBottom
            ? eventShell.scrollHeight
            : scrollState.eventScrollTop;
          eventShell.scrollLeft = scrollState.eventScrollLeft;
        }
        if (historyStrip) {
          historyStrip.scrollLeft = scrollState.historyScrollLeft;
        }
        restoreStage2AssetBrowserScrollState(scrollState, detailBody);
        window.scrollTo(scrollState.windowScrollX, scrollState.windowScrollY);
      });
    }

    function applyPendingStage2RunOpenDefaults() {
      if (!state.pendingStage2RunOpenDefaults) {
        return;
      }
      requestAnimationFrame(() => {
        if (!state.pendingStage2RunOpenDefaults) {
          return;
        }
        const detailBody = $("#stage2-detail-body");
        const runPanel = detailBody?.querySelector("[data-stage2-run-panel-id]");
        if (!runPanel) {
          return;
        }
        const eventShell = runPanel.querySelector(".stage2-event-shell");
        if (eventShell) {
          eventShell.scrollTop = eventShell.scrollHeight;
        }
        state.pendingStage2RunOpenDefaults = false;
      });
    }

    function applyPendingStage3RunOpenDefaults() {
      if (!state.pendingStage3RunOpenDefaults) {
        return;
      }
      requestAnimationFrame(() => {
        if (!state.pendingStage3RunOpenDefaults) {
          return;
        }
        const detailBody = $("#stage3-detail-body");
        const runPanel = detailBody?.querySelector("[data-stage3-run-panel-id]");
        if (!runPanel) {
          return;
        }
        const eventShell = runPanel.querySelector(".stage2-event-shell");
        if (eventShell) {
          eventShell.scrollTop = eventShell.scrollHeight;
        }
        state.pendingStage3RunOpenDefaults = false;
      });
    }

    function applyPendingStage4RunOpenDefaults() {
      if (!state.pendingStage4RunOpenDefaults) {
        return;
      }
      requestAnimationFrame(() => {
        if (!state.pendingStage4RunOpenDefaults) {
          return;
        }
        const detailBody = $("#stage4-detail-body");
        const runPanel = detailBody?.querySelector("[data-stage4-run-panel-id]");
        if (!runPanel) {
          return;
        }
        const eventShell = runPanel.querySelector(".stage2-event-shell");
        if (eventShell) {
          eventShell.scrollTop = eventShell.scrollHeight;
        }
        state.pendingStage4RunOpenDefaults = false;
      });
    }

    function rerenderStage3DetailPreservingScroll() {
      const scrollState = captureStage3DetailScrollState();
      renderCurrentStage3Detail(currentStage3RepositoryDetailPayload());
      restoreStage3DetailScrollState(scrollState);
    }

    function rerenderStage2RepositoryDetailPreservingScroll() {
      const scrollState = captureStage2DetailScrollState();
      renderStage2RepositoryDetail(currentStage2RepositoryDetailPayload());
      restoreStage2DetailScrollState(scrollState);
    }

    function syncStage2EventShell(events) {
      const detailBody = $("#stage2-detail-body");
      const eventsSlot = detailBody?.querySelector("[data-stage2-events-slot]");
      if (!eventsSlot) {
        return false;
      }
      if (!events || events.length === 0) {
        eventsSlot.innerHTML = renderStage2Events(events);
        return true;
      }
      let eventShell = eventsSlot.querySelector(".stage2-event-shell");
      let eventList = eventsSlot.querySelector(".stage2-event-list");
      if (!eventShell || !eventList) {
        eventsSlot.innerHTML = renderStage2Events(events);
        return true;
      }

      const wasNearBottom = eventShell.scrollHeight - eventShell.clientHeight - eventShell.scrollTop <= 24;
      const previousScrollTop = eventShell.scrollTop;
      const previousScrollLeft = eventShell.scrollLeft;
      const displayEvents = displayStage2Events(events);
      const existingByKey = new Map();
      eventList.querySelectorAll("[data-stage2-event-key]").forEach((element) => {
        existingByKey.set(element.dataset.stage2EventKey, element);
      });
      const nextKeys = new Set();

      displayEvents.forEach((event, index) => {
        const eventKey = stage2EventKey(event, index);
        const signature = stage2EventSignature(event);
        nextKeys.add(eventKey);
        const existing = existingByKey.get(eventKey);
        if (existing) {
          if (existing.dataset.stage2EventSignature !== signature) {
            const replacement = htmlToElement(renderStage2EventItem(event, index));
            replacement.dataset.stage2EventSignature = signature;
            existing.replaceWith(replacement);
          }
          return;
        }
        const element = htmlToElement(renderStage2EventItem(event, index));
        element.dataset.stage2EventSignature = signature;
        eventList.appendChild(element);
      });

      eventList.querySelectorAll("[data-stage2-event-key]").forEach((element) => {
        if (!nextKeys.has(element.dataset.stage2EventKey)) {
          element.remove();
        }
      });
      eventShell.scrollTop = wasNearBottom ? eventShell.scrollHeight : previousScrollTop;
      eventShell.scrollLeft = previousScrollLeft;
      return true;
    }

    function findStage2RunPanel(runId) {
      return Array.from(document.querySelectorAll("[data-stage2-run-panel-id]")).find(
        (element) => String(element.dataset.stage2RunPanelId) === String(runId),
      ) || null;
    }

    function replaceInnerHtmlIfChanged(element, nextHtml) {
      if (!element) {
        return false;
      }
      if (element.innerHTML === nextHtml) {
        return false;
      }
      element.innerHTML = nextHtml;
      return true;
    }

    function patchStage2RepositoryDetail(payload) {
      const scrollState = captureStage2DetailScrollState();
      const repository = payload.repository;
      const runs = payload.runs || [];
      if (!repository || runs.length === 0) {
        return false;
      }
      if (!runs.some((run) => String(run.id) === String(state.selectedStage2RunId))) {
        state.selectedStage2RunId = runs[0].id;
        return false;
      }
      const selectedRun = (
        payload.selected_run && String(payload.selected_run.id) === String(state.selectedStage2RunId)
      ) ? payload.selected_run : null;
      if (!selectedRun) {
        return false;
      }
      const runPanel = findStage2RunPanel(selectedRun.id);
      if (!runPanel) {
        return false;
      }

      const runNumberById = buildStage2RunNumberById(runs);
      $("#stage2-detail-subtitle").textContent = `${repository.full_name} · ${repository.stage2?.history_count ?? 0} 次运行`;
      $("#stage2-detail-action").innerHTML = renderStage2DetailAction(repository);
      bindStage2RunActionButtons($("#stage2-detail-action"));

      const historyStripWrap = $("#stage2-detail-body")?.querySelector(".stage2-history-strip-wrap");
      if (historyStripWrap) {
        const historyScrollLeft = historyStripWrap.scrollLeft;
        historyStripWrap.innerHTML = renderStage2HistoryStrip(runs, runNumberById);
        historyStripWrap.scrollLeft = historyScrollLeft;
        bindStage2DetailInteractiveHandlers(historyStripWrap);
      }

      const summarySlot = runPanel.querySelector("[data-stage2-summary-slot]");
      if (summarySlot) {
        const nextSummaryHtml = renderStage2RunOverview(selectedRun);
        if (replaceInnerHtmlIfChanged(summarySlot, nextSummaryHtml)) {
          bindStage2DetailInteractiveHandlers(summarySlot);
        }
      }

      const assetsSlot = runPanel.querySelector("[data-stage2-assets-slot]");
      if (assetsSlot) {
        const nextAssetsHtml = renderStage2AssetBrowser(selectedRun);
        if (replaceInnerHtmlIfChanged(assetsSlot, nextAssetsHtml)) {
          bindStage2DetailInteractiveHandlers(assetsSlot);
        }
      }

      const errorSlot = runPanel.querySelector("[data-stage2-error-slot]");
      if (errorSlot) {
        replaceInnerHtmlIfChanged(errorSlot, renderStage2RunError(selectedRun));
      }

      const synced = syncStage2EventShell(selectedRun.events);
      restoreStage2DetailScrollState(scrollState);
      applyPendingStage2RunOpenDefaults();
      return synced;
    }

    function captureStage3DetailScrollState() {
      const detailBody = $("#stage3-detail-body");
      const eventShell = detailBody?.querySelector("[data-stage3-events-slot] .stage2-event-shell");
      const historyStrip = detailBody?.querySelector(".stage2-history-strip-wrap");
      const eventBottomGap = eventShell
        ? eventShell.scrollHeight - eventShell.clientHeight - eventShell.scrollTop
        : null;
      return {
        windowScrollX: window.scrollX,
        windowScrollY: window.scrollY,
        detailScrollTop: detailBody?.scrollTop ?? 0,
        detailScrollLeft: detailBody?.scrollLeft ?? 0,
        eventScrollTop: eventShell?.scrollTop ?? 0,
        eventScrollLeft: eventShell?.scrollLeft ?? 0,
        eventWasNearBottom: eventBottomGap != null && eventBottomGap <= 24,
        historyScrollLeft: historyStrip?.scrollLeft ?? 0,
        ...captureStage2AssetBrowserScrollState(detailBody),
      };
    }

    function restoreStage3DetailScrollState(scrollState) {
      if (!scrollState) {
        return;
      }
      requestAnimationFrame(() => {
        const detailBody = $("#stage3-detail-body");
        const eventShell = detailBody?.querySelector("[data-stage3-events-slot] .stage2-event-shell");
        const historyStrip = detailBody?.querySelector(".stage2-history-strip-wrap");
        if (detailBody) {
          detailBody.scrollTop = scrollState.detailScrollTop;
          detailBody.scrollLeft = scrollState.detailScrollLeft;
        }
        if (eventShell) {
          eventShell.scrollTop = scrollState.eventWasNearBottom
            ? eventShell.scrollHeight
            : scrollState.eventScrollTop;
          eventShell.scrollLeft = scrollState.eventScrollLeft;
        }
        if (historyStrip) {
          historyStrip.scrollLeft = scrollState.historyScrollLeft;
        }
        restoreStage2AssetBrowserScrollState(scrollState, detailBody);
        window.scrollTo(scrollState.windowScrollX, scrollState.windowScrollY);
      });
    }

    function syncStage3EventShell(events) {
      const detailBody = $("#stage3-detail-body");
      const eventsSlot = detailBody?.querySelector("[data-stage3-events-slot]");
      const eventOptions = { phaseLabel: stage3PhaseLabel };
      if (!eventsSlot) {
        return false;
      }
      if (!events || events.length === 0) {
        eventsSlot.innerHTML = renderStage2Events(events, eventOptions);
        return true;
      }
      let eventShell = eventsSlot.querySelector(".stage2-event-shell");
      let eventList = eventsSlot.querySelector(".stage2-event-list");
      if (!eventShell || !eventList) {
        eventsSlot.innerHTML = renderStage2Events(events, eventOptions);
        return true;
      }

      const wasNearBottom = eventShell.scrollHeight - eventShell.clientHeight - eventShell.scrollTop <= 24;
      const previousScrollTop = eventShell.scrollTop;
      const previousScrollLeft = eventShell.scrollLeft;
      const displayEvents = displayStage2Events(events);
      const existingByKey = new Map();
      eventList.querySelectorAll("[data-stage2-event-key]").forEach((element) => {
        existingByKey.set(element.dataset.stage2EventKey, element);
      });
      const nextKeys = new Set();

      displayEvents.forEach((event, index) => {
        const eventKey = stage2EventKey(event, index);
        const signature = stage2EventSignature(event);
        nextKeys.add(eventKey);
        const existing = existingByKey.get(eventKey);
        if (existing) {
          if (existing.dataset.stage2EventSignature !== signature) {
            const replacement = htmlToElement(renderStage2EventItem(event, index, eventOptions));
            replacement.dataset.stage2EventSignature = signature;
            existing.replaceWith(replacement);
          }
          return;
        }
        const element = htmlToElement(renderStage2EventItem(event, index, eventOptions));
        element.dataset.stage2EventSignature = signature;
        eventList.appendChild(element);
      });

      eventList.querySelectorAll("[data-stage2-event-key]").forEach((element) => {
        if (!nextKeys.has(element.dataset.stage2EventKey)) {
          element.remove();
        }
      });
      eventShell.scrollTop = wasNearBottom ? eventShell.scrollHeight : previousScrollTop;
      eventShell.scrollLeft = previousScrollLeft;
      return true;
    }

    function findStage3RunPanel(runId) {
      return Array.from(document.querySelectorAll("[data-stage3-run-panel-id]")).find(
        (element) => String(element.dataset.stage3RunPanelId) === String(runId),
      ) || null;
    }

    function patchStage3EntryFileDetail(payload) {
      if (!state.stage3List.entryDetailVisible) {
        return false;
      }
      const scrollState = captureStage3DetailScrollState();
      const repository = payload.repository;
      const entryFile = payload.selected_entry_file;
      const runs = Array.isArray(payload.runs) ? payload.runs : [];
      if (!repository || !entryFile || runs.length === 0) {
        return false;
      }
      if (String(entryFile.id) !== String(state.selectedStage3EntryFileId)) {
        return false;
      }
      if (!runs.some((run) => String(run.id) === String(state.selectedStage3RunId))) {
        state.selectedStage3RunId = payload.selected_run?.id || runs[0]?.id || null;
        return false;
      }
      const selectedRun = (
        payload.selected_run && String(payload.selected_run.id) === String(state.selectedStage3RunId)
      ) ? payload.selected_run : runs.find((run) => String(run.id) === String(state.selectedStage3RunId));
      if (!selectedRun) {
        return false;
      }
      const runPanel = findStage3RunPanel(selectedRun.id);
      if (!runPanel) {
        return false;
      }

      state.selectedStage3SnapshotId = payload.selected_snapshot?.id || state.selectedStage3SnapshotId;
      state.selectedStage3EntryFileId = entryFile.id;
      state.selectedStage3RunId = selectedRun.id;
      setStage3RepositoryDetailPayload({
        ...payload,
        selected_run: selectedRun,
      });

      const entryFileSubtitle = [
        repository.full_name,
        entryFile.test_file_path || "-",
        `原始测试点 ${String(entryFile.baseline_total_tests || 0)}`,
        `原始通过率 ${formatPercent(entryFile.baseline_pass_rate)}`,
        `最近操作 ${formatDate(entryFile.latest_operation_at || null)}`,
        `数据条数 ${String(entryFile.savepoint_count || 0)}`,
      ].join(" · ");
      const primaryAction = stage3EntryActionDescriptor(entryFile);
      setStage3DetailHeader({
        title: "入口文件详细",
        backLabel: "返回 Repo",
        subtitle: entryFileSubtitle,
        primaryActionLabel: primaryAction.visible ? primaryAction.label : "",
        primaryActionKind: primaryAction.visible ? "create-stage3-run" : "",
        primaryActionId: String(entryFile.id),
        primaryActionExistingRunId: primaryAction.existingRunId,
        primaryActionMode: primaryAction.mode,
        primaryActionDisabled: primaryAction.disabled,
      });

      const runNumberById = buildStage3RunNumberById(runs);
      const historyStripWrap = $("#stage3-detail-body")?.querySelector(".stage2-history-strip-wrap");
      if (historyStripWrap) {
        const historyScrollLeft = historyStripWrap.scrollLeft;
        historyStripWrap.innerHTML = `
          <div class="stage2-history-strip">
            ${runs.map((run) => renderStage3RunCard(run, runNumberById.get(String(run.id)) || 1)).join("")}
          </div>
        `;
        historyStripWrap.scrollLeft = historyScrollLeft;
        bindStage3DetailHandlers(historyStripWrap);
      }

      const summarySlot = runPanel.querySelector("[data-stage3-summary-slot]");
      if (summarySlot && !isEditingStage3RunRuntime(selectedRun.id)) {
        const nextSummaryHtml = renderStage3RunSummaryStack(selectedRun);
        if (replaceInnerHtmlIfChanged(summarySlot, nextSummaryHtml)) {
          bindStage3DetailHandlers(summarySlot);
        }
      }

      const assetsSlot = runPanel.querySelector("[data-stage3-assets-slot]");
      if (assetsSlot) {
        const nextAssetsHtml = renderStage3RunOutputAssets(currentStage3RepositoryDetailPayload(), selectedRun);
        if (replaceInnerHtmlIfChanged(assetsSlot, nextAssetsHtml)) {
          bindStage3DetailHandlers(assetsSlot);
        }
      }

      const errorSlot = runPanel.querySelector("[data-stage3-error-slot]");
      if (errorSlot) {
        replaceInnerHtmlIfChanged(errorSlot, renderStage3RunError(selectedRun));
      }

      const synced = syncStage3EventShell(selectedRun.events);
      restoreStage3DetailScrollState(scrollState);
      applyPendingStage3RunOpenDefaults();
      return synced;
    }

    function setStage2RepositorySelectedRunDetail(selectedRun) {
      const currentPayload = currentStage2RepositoryDetailPayload();
      setStage2RepositoryDetailPayload({
        repository: currentPayload.repository,
        runs: currentPayload.runs,
        selected_run: selectedRun || null,
      });
    }

    function closeStage1JobDetailStream() {
      if (stage1JobDetailEventSource) {
        stage1JobDetailEventSource.close();
      }
      stage1JobDetailEventSource = null;
      stage1JobDetailStreamJobId = null;
    }

    function closeStage2RepositoryDetailStream() {
      if (stage2RepositoryDetailEventSource) {
        stage2RepositoryDetailEventSource.close();
      }
      stage2RepositoryDetailEventSource = null;
      stage2RepositoryDetailStreamRepositoryId = null;
      stage2RepositoryDetailStreamRunId = null;
    }

    function closeStage3RepositoryDetailStream() {
      if (stage3RepositoryDetailEventSource) {
        stage3RepositoryDetailEventSource.close();
      }
      stage3RepositoryDetailEventSource = null;
      stage3RepositoryDetailStreamRepositoryId = null;
      stage3RepositoryDetailStreamSnapshotId = null;
      stage3RepositoryDetailStreamEntryFileId = null;
      stage3RepositoryDetailStreamRunId = null;
    }

    function closeStage4RunDetailStream() {
      if (stage4RunDetailEventSource) {
        stage4RunDetailEventSource.close();
      }
      stage4RunDetailEventSource = null;
      stage4RunDetailStreamRunId = null;
    }

    function isStage1JobRealtimeEligible(job) {
      return Boolean(job && (job.is_running || AUTO_REFRESH_JOB_STATUSES.has(job.status)));
    }

    function isStage2RepositoryRealtimeEligible(repository) {
      return repository?.stage2?.status === "queued" || repository?.stage2?.status === "running";
    }

    function isStage3RepositoryRealtimeEligible(repository) {
      return repository?.stage3?.status === "queued" || repository?.stage3?.status === "running";
    }

    function isStage4RunRealtimeEligible(run) {
      return Boolean(run && (run.is_active || ["queued", "running"].includes(String(run.status || ""))));
    }

    function applyLiveStage1JobDetailPayload(payload) {
      if (!payload || String(payload.job?.id) !== String(state.selectedJobId)) {
        return;
      }
      if (state.activeTab !== "stage1") {
        pendingStage1JobDetailPayload = null;
        return;
      }
      if (hasActiveTextSelection()) {
        pendingStage1JobDetailPayload = payload;
        return;
      }
      pendingStage1JobDetailPayload = null;
      mergeJobSummary(payload.job);
      renderJobDetail(payload);
      setRefreshMeta();
    }

    function applyLiveStage2RepositoryDetailPayload(payload) {
      if (!payload || String(payload.repository?.id) !== String(state.selectedStage2RepositoryId)) {
        return;
      }
      if (state.activeTab !== "stage2" || !state.stage2List.detailVisible) {
        pendingStage2RepositoryDetailPayload = null;
        return;
      }
      if (hasActiveTextSelection()) {
        pendingStage2RepositoryDetailPayload = payload;
        return;
      }
      pendingStage2RepositoryDetailPayload = null;
      mergeStage2RepositorySummary(payload.repository);
      setStage2RepositoryDetailPayload(payload);
      if (!patchStage2RepositoryDetail(payload)) {
        rerenderStage2RepositoryDetailPreservingScroll();
      }
      setRefreshMeta();
    }

    function flushPendingLiveDetailPayloads() {
      if (hasActiveTextSelection()) {
        return;
      }
      if (pendingBatchTaskDetailPayload) {
        if (String(pendingBatchTaskDetailPayload.id || "") !== String(state.selectedBatchTaskId || "")) {
          pendingBatchTaskDetailPayload = null;
        } else if (state.activeTab !== "batch") {
          pendingBatchTaskDetailPayload = null;
        } else if (!isBatchRepoFilterInteractionActive()) {
          const payload = pendingBatchTaskDetailPayload;
          pendingBatchTaskDetailPayload = null;
          state.batchTaskDetailPayload = payload;
          renderBatchTaskDetail();
          setRefreshMeta();
        }
      }
      if (pendingStage1JobDetailPayload && String(pendingStage1JobDetailPayload.job?.id) === String(state.selectedJobId)) {
        if (state.activeTab !== "stage1") {
          pendingStage1JobDetailPayload = null;
        } else {
          const payload = pendingStage1JobDetailPayload;
          pendingStage1JobDetailPayload = null;
          mergeJobSummary(payload.job);
          renderJobDetail(payload);
          setRefreshMeta();
        }
      }
      if (
        pendingStage2RepositoryDetailPayload
        && String(pendingStage2RepositoryDetailPayload.repository?.id) === String(state.selectedStage2RepositoryId)
      ) {
        if (state.activeTab !== "stage2" || !state.stage2List.detailVisible) {
          pendingStage2RepositoryDetailPayload = null;
        } else {
          const payload = pendingStage2RepositoryDetailPayload;
          pendingStage2RepositoryDetailPayload = null;
          mergeStage2RepositorySummary(payload.repository);
          setStage2RepositoryDetailPayload(payload);
          if (!patchStage2RepositoryDetail(payload)) {
            rerenderStage2RepositoryDetailPreservingScroll();
          }
          setRefreshMeta();
        }
      }
      if (
        pendingStage3RepositoryDetailPayload
        && String(pendingStage3RepositoryDetailPayload.repository?.id) === String(state.selectedStage3RepositoryId)
      ) {
        if (state.activeTab !== "stage3" || !state.stage3List.detailVisible) {
          pendingStage3RepositoryDetailPayload = null;
          pendingStage3RepositoryDetailRerender = false;
        } else if (!isStage3EntryFilterInteractionActive()) {
          const payload = pendingStage3RepositoryDetailPayload;
          pendingStage3RepositoryDetailPayload = null;
          if (stage3LivePayloadMatchesCurrentDetailSelection(payload)) {
            mergeStage3RepositorySummary(payload.repository);
            setStage3RepositoryDetailPayload(payload);
            if (!patchStage3EntryFileDetail(payload)) {
              rerenderStage3DetailPreservingScroll();
            }
            pendingStage3RepositoryDetailRerender = false;
            setRefreshMeta();
          }
        }
      }
      if (
        pendingStage3RepositoryDetailRerender
      ) {
        if (state.activeTab !== "stage3" || !state.selectedStage3RepositoryId || !state.stage3List.detailVisible || state.stage3List.entryDetailVisible) {
          pendingStage3RepositoryDetailRerender = false;
        } else if (!isStage3EntryFilterInteractionActive()) {
          pendingStage3RepositoryDetailRerender = false;
          rerenderStage3DetailPreservingScroll();
          setRefreshMeta();
        }
      }
      if (
        pendingStage4SourceDetailPayload
        && String((pendingStage4SourceDetailPayload.group || {}).key || "") === String(state.selectedStage4SourceGroupKey || "")
      ) {
        if (state.activeTab !== "stage4" || !state.stage4List.detailVisible || state.stage4List.detailMode !== "source") {
          pendingStage4SourceDetailPayload = null;
        } else if (!isStage4SavepointFilterInteractionActive()) {
          const payload = pendingStage4SourceDetailPayload;
          pendingStage4SourceDetailPayload = null;
          applyLiveStage4SourceDetailPayload(payload);
          setRefreshMeta();
        }
      }
      if (
        pendingStage4RunDetailPayload
        && String(pendingStage4RunDetailPayload.id || "") === String(state.selectedStage4RunId || "")
      ) {
        if (state.activeTab !== "stage4" || !state.stage4List.detailVisible || state.stage4List.detailMode !== "run") {
          pendingStage4RunDetailPayload = null;
        } else {
          const payload = pendingStage4RunDetailPayload;
          pendingStage4RunDetailPayload = null;
          updateStage4RunCaches(payload);
          if (!patchStage4RunDetail(payload)) {
            rerenderStage4RunDetailPreservingScroll(payload);
          }
          setRefreshMeta();
        }
      }
    }

    function getActiveRepoFilterCount() {
      const filters = state.repoList.filters;
      return Object.values(filters).filter((value) => {
        if (Array.isArray(value)) {
          return value.length > 0;
        }
        return String(value || "").trim() !== "";
      }).length;
    }

    function updateRepoFilterToggle() {
      const count = getActiveRepoFilterCount();
      const toggle = $("#repo-filter-toggle");
      toggle.textContent = count > 0 ? `筛选（${count}）` : "筛选";
      toggle.classList.toggle("active", count > 0);
    }

    function summarizeLanguages(values) {
      if (values.length === 0) {
        return "不限";
      }
      if (values.length <= 2) {
        return values.join(", ");
      }
      return `${values.slice(0, 2).join(", ")} +${values.length - 2}`;
    }

    function summarizeLicenses(values) {
      if (values.length === 0) {
        return "不限";
      }
      if (values.length <= 2) {
        return values.join(", ");
      }
      return `${values.slice(0, 2).join(", ")} +${values.length - 2}`;
    }

    function syncCrawlLanguageInputs() {
      const selected = new Set(state.crawlForm.languages);
      document.querySelectorAll("[data-crawl-language]").forEach((input) => {
        input.checked = selected.has(input.value);
      });
    }

    function updateCrawlLanguageToggle() {
      $("#crawl-language-summary").textContent = summarizeLanguages(state.crawlForm.languages);
      $("#crawl-language-toggle").setAttribute("aria-expanded", String(!$("#crawl-language-popover").hidden));
    }

    function readSelectedCrawlLanguages() {
      return Array.from(document.querySelectorAll("[data-crawl-language]"))
        .filter((input) => input.checked)
        .map((input) => input.value);
    }

    function closeCrawlLanguagePopover() {
      $("#crawl-language-popover").hidden = true;
      updateCrawlLanguageToggle();
    }

    function syncCrawlLicenseInputs() {
      const selected = new Set(state.crawlForm.licenses);
      document.querySelectorAll("[data-crawl-license]").forEach((input) => {
        input.checked = selected.has(input.value);
      });
    }

    function updateCrawlLicenseToggle() {
      $("#crawl-license-summary").textContent = summarizeLicenses(state.crawlForm.licenses);
      $("#crawl-license-toggle").setAttribute("aria-expanded", String(!$("#crawl-license-popover").hidden));
    }

    function readSelectedCrawlLicenses() {
      return Array.from(document.querySelectorAll("[data-crawl-license]"))
        .filter((input) => input.checked)
        .map((input) => input.value);
    }

    function closeCrawlLicensePopover() {
      $("#crawl-license-popover").hidden = true;
      updateCrawlLicenseToggle();
    }

    function syncBatchLanguageInputs() {
      const selected = new Set(state.batchForm.languages);
      document.querySelectorAll("[data-batch-language]").forEach((input) => {
        input.checked = selected.has(input.value);
      });
    }

    function updateBatchLanguageToggle() {
      $("#batch-language-summary").textContent = summarizeLanguages(state.batchForm.languages);
      $("#batch-language-toggle").setAttribute("aria-expanded", String(!$("#batch-language-popover").hidden));
    }

    function readSelectedBatchLanguages() {
      return Array.from(document.querySelectorAll("[data-batch-language]"))
        .filter((input) => input.checked)
        .map((input) => input.value);
    }

    function closeBatchLanguagePopover() {
      $("#batch-language-popover").hidden = true;
      updateBatchLanguageToggle();
    }

    function syncBatchLicenseInputs() {
      const selected = new Set(state.batchForm.licenses);
      document.querySelectorAll("[data-batch-license]").forEach((input) => {
        input.checked = selected.has(input.value);
      });
    }

    function updateBatchLicenseToggle() {
      $("#batch-license-summary").textContent = summarizeLicenses(state.batchForm.licenses);
      $("#batch-license-toggle").setAttribute("aria-expanded", String(!$("#batch-license-popover").hidden));
    }

    function readSelectedBatchLicenses() {
      return Array.from(document.querySelectorAll("[data-batch-license]"))
        .filter((input) => input.checked)
        .map((input) => input.value);
    }

    function closeBatchLicensePopover() {
      $("#batch-license-popover").hidden = true;
      updateBatchLicenseToggle();
    }

    function syncRepoFilterLanguageInputs() {
      const selected = new Set(state.repoList.filters.languages);
      document.querySelectorAll("[data-repo-filter-language]").forEach((input) => {
        input.checked = selected.has(input.value);
      });
    }

    function updateRepoFilterLanguageToggle(values = state.repoList.filters.languages) {
      $("#repo-filter-language-summary").textContent = summarizeLanguages(values);
      $("#repo-filter-language-toggle").setAttribute("aria-expanded", String(!$("#repo-filter-language-popover").hidden));
    }

    function readSelectedRepoFilterLanguages() {
      return Array.from(document.querySelectorAll("[data-repo-filter-language]"))
        .filter((input) => input.checked)
        .map((input) => input.value);
    }

    function closeRepoFilterLanguagePopover() {
      $("#repo-filter-language-popover").hidden = true;
      updateRepoFilterLanguageToggle(readSelectedRepoFilterLanguages());
    }

    function syncRepoFilterLicenseInputs() {
      const selected = new Set(state.repoList.filters.licenses);
      document.querySelectorAll("[data-repo-filter-license]").forEach((input) => {
        input.checked = selected.has(input.value);
      });
    }

    function updateRepoFilterLicenseToggle(values = state.repoList.filters.licenses) {
      $("#repo-filter-license-summary").textContent = summarizeLicenses(values);
      $("#repo-filter-license-toggle").setAttribute("aria-expanded", String(!$("#repo-filter-license-popover").hidden));
    }

    function readSelectedRepoFilterLicenses() {
      return Array.from(document.querySelectorAll("[data-repo-filter-license]"))
        .filter((input) => input.checked)
        .map((input) => input.value);
    }

    function closeRepoFilterLicensePopover() {
      $("#repo-filter-license-popover").hidden = true;
      updateRepoFilterLicenseToggle(readSelectedRepoFilterLicenses());
    }

    function syncRepoFilterForm() {
      const filters = state.repoList.filters;
      $("#repo-filter-name-query").value = filters.nameQuery;
      $("#repo-filter-stars-min").value = filters.starsMin;
      $("#repo-filter-stars-max").value = filters.starsMax;
      $("#repo-filter-created-after").value = filters.createdAfter;
      $("#repo-filter-created-before").value = filters.createdBefore;
      $("#repo-filter-pushed-after").value = filters.pushedAfter;
      $("#repo-filter-pushed-before").value = filters.pushedBefore;
      $("#repo-filter-discovered-after").value = filters.discoveredAfter;
      $("#repo-filter-discovered-before").value = filters.discoveredBefore;
      syncRepoFilterLanguageInputs();
      updateRepoFilterLanguageToggle();
      syncRepoFilterLicenseInputs();
      updateRepoFilterLicenseToggle();
    }

    function readRepoFilterForm() {
      return {
        nameQuery: $("#repo-filter-name-query").value.trim(),
        languages: readSelectedRepoFilterLanguages(),
        licenses: readSelectedRepoFilterLicenses(),
        starsMin: $("#repo-filter-stars-min").value.trim(),
        starsMax: $("#repo-filter-stars-max").value.trim(),
        createdAfter: $("#repo-filter-created-after").value,
        createdBefore: $("#repo-filter-created-before").value,
        pushedAfter: $("#repo-filter-pushed-after").value,
        pushedBefore: $("#repo-filter-pushed-before").value,
        discoveredAfter: $("#repo-filter-discovered-after").value,
        discoveredBefore: $("#repo-filter-discovered-before").value,
      };
    }

    function closeRepoFilterPopover() {
      $("#repo-filter-popover").hidden = true;
    }

    function setDateParam(params, key, value) {
      const iso = isoFromLocal(value);
      if (iso) {
        params.set(key, iso);
      }
    }

    function buildRepoListParams() {
      const { page, pageSize, sortField, sortOrder, filters } = state.repoList;
      const params = new URLSearchParams({
        page: String(page),
        page_size: String(pageSize),
        sort_by: sortField,
        sort_order: sortOrder,
      });
      if (filters.nameQuery) params.set("name_query", filters.nameQuery);
      filters.languages.forEach((language) => params.append("languages", language));
      filters.licenses.forEach((license) => params.append("licenses", license));
      if (filters.starsMin) params.set("stars_min", filters.starsMin);
      if (filters.starsMax) params.set("stars_max", filters.starsMax);
      setDateParam(params, "created_after", filters.createdAfter);
      setDateParam(params, "created_before", filters.createdBefore);
      setDateParam(params, "pushed_after", filters.pushedAfter);
      setDateParam(params, "pushed_before", filters.pushedBefore);
      setDateParam(params, "discovered_after", filters.discoveredAfter);
      setDateParam(params, "discovered_before", filters.discoveredBefore);
      return params;
    }

    function getActiveStage2FilterCount() {
      const filters = state.stage2List.filters;
      return Object.values(filters).filter((value) => {
        if (Array.isArray(value)) {
          return value.length > 0;
        }
        return String(value || "").trim() !== "";
      }).length;
    }

    function isStage2NonPendingFilterActive() {
      const statuses = state.stage2List.filters.statuses || [];
      if (statuses.length !== STAGE2_NON_PENDING_STATUSES.length) {
        return false;
      }
      const selected = new Set(statuses);
      return STAGE2_NON_PENDING_STATUSES.every((status) => selected.has(status));
    }

    function updateStage2FilterToggle() {
      const count = getActiveStage2FilterCount();
      const toggle = $("#stage2-filter-toggle");
      toggle.textContent = count > 0 ? `筛选（${count}）` : "筛选";
      toggle.classList.toggle("active", count > 0);
      const nonPendingToggle = $("#stage2-filter-non-pending");
      const nonPendingActive = isStage2NonPendingFilterActive();
      nonPendingToggle.classList.toggle("active", nonPendingActive);
      nonPendingToggle.setAttribute("aria-pressed", String(nonPendingActive));
    }

    function syncStage2FilterLanguageInputs() {
      const selected = new Set(state.stage2List.filters.languages);
      document.querySelectorAll("[data-stage2-filter-language]").forEach((input) => {
        input.checked = selected.has(input.value);
      });
    }

    function syncStage2FilterStatusInputs() {
      const selected = new Set(state.stage2List.filters.statuses || []);
      document.querySelectorAll("[data-stage2-filter-status]").forEach((input) => {
        input.checked = selected.has(input.value);
      });
    }

    function updateStage2FilterStatusToggle(values = state.stage2List.filters.statuses) {
      $("#stage2-filter-status-summary").textContent = summarizeStage2Statuses(values || []);
      $("#stage2-filter-status-toggle").setAttribute("aria-expanded", String(!$("#stage2-filter-status-popover").hidden));
    }

    function readSelectedStage2FilterStatuses() {
      return Array.from(document.querySelectorAll("[data-stage2-filter-status]"))
        .filter((input) => input.checked)
        .map((input) => input.value);
    }

    function closeStage2FilterStatusPopover() {
      $("#stage2-filter-status-popover").hidden = true;
      updateStage2FilterStatusToggle(readSelectedStage2FilterStatuses());
    }

    function updateStage2FilterLanguageToggle(values = state.stage2List.filters.languages) {
      $("#stage2-filter-language-summary").textContent = summarizeLanguages(values);
      $("#stage2-filter-language-toggle").setAttribute("aria-expanded", String(!$("#stage2-filter-language-popover").hidden));
    }

    function readSelectedStage2FilterLanguages() {
      return Array.from(document.querySelectorAll("[data-stage2-filter-language]"))
        .filter((input) => input.checked)
        .map((input) => input.value);
    }

    function closeStage2FilterLanguagePopover() {
      $("#stage2-filter-language-popover").hidden = true;
      updateStage2FilterLanguageToggle(readSelectedStage2FilterLanguages());
    }

    function syncStage2FilterLicenseInputs() {
      const selected = new Set(state.stage2List.filters.licenses);
      document.querySelectorAll("[data-stage2-filter-license]").forEach((input) => {
        input.checked = selected.has(input.value);
      });
    }

    function updateStage2FilterLicenseToggle(values = state.stage2List.filters.licenses) {
      $("#stage2-filter-license-summary").textContent = summarizeLicenses(values);
      $("#stage2-filter-license-toggle").setAttribute("aria-expanded", String(!$("#stage2-filter-license-popover").hidden));
    }

    function readSelectedStage2FilterLicenses() {
      return Array.from(document.querySelectorAll("[data-stage2-filter-license]"))
        .filter((input) => input.checked)
        .map((input) => input.value);
    }

    function closeStage2FilterLicensePopover() {
      $("#stage2-filter-license-popover").hidden = true;
      updateStage2FilterLicenseToggle(readSelectedStage2FilterLicenses());
    }

    function syncStage2FilterForm() {
      const filters = state.stage2List.filters;
      $("#stage2-filter-name-query").value = filters.nameQuery;
      syncStage2FilterStatusInputs();
      updateStage2FilterStatusToggle();
      $("#stage2-filter-stars-min").value = filters.starsMin;
      $("#stage2-filter-stars-max").value = filters.starsMax;
      $("#stage2-filter-created-after").value = filters.createdAfter;
      $("#stage2-filter-created-before").value = filters.createdBefore;
      $("#stage2-filter-pushed-after").value = filters.pushedAfter;
      $("#stage2-filter-pushed-before").value = filters.pushedBefore;
      $("#stage2-filter-discovered-after").value = filters.discoveredAfter;
      $("#stage2-filter-discovered-before").value = filters.discoveredBefore;
      syncStage2FilterLanguageInputs();
      updateStage2FilterLanguageToggle();
      syncStage2FilterLicenseInputs();
      updateStage2FilterLicenseToggle();
    }

    function readStage2FilterForm() {
      return {
        nameQuery: $("#stage2-filter-name-query").value.trim(),
        statuses: readSelectedStage2FilterStatuses(),
        languages: readSelectedStage2FilterLanguages(),
        licenses: readSelectedStage2FilterLicenses(),
        starsMin: $("#stage2-filter-stars-min").value.trim(),
        starsMax: $("#stage2-filter-stars-max").value.trim(),
        createdAfter: $("#stage2-filter-created-after").value,
        createdBefore: $("#stage2-filter-created-before").value,
        pushedAfter: $("#stage2-filter-pushed-after").value,
        pushedBefore: $("#stage2-filter-pushed-before").value,
        discoveredAfter: $("#stage2-filter-discovered-after").value,
        discoveredBefore: $("#stage2-filter-discovered-before").value,
      };
    }

    function closeStage2FilterPopover() {
      $("#stage2-filter-popover").hidden = true;
    }

    function getActiveStage3FilterCount() {
      const filters = state.stage3List.filters;
      return Object.values(filters).filter((value) => {
        if (Array.isArray(value)) {
          return value.length > 0;
        }
        return String(value || "").trim() !== "";
      }).length;
    }

    function isStage3NonPendingFilterActive() {
      const statuses = state.stage3List.filters.statuses || [];
      if (statuses.length !== STAGE3_NON_PENDING_STATUSES.length) {
        return false;
      }
      const selected = new Set(statuses);
      return STAGE3_NON_PENDING_STATUSES.every((status) => selected.has(status));
    }

    function updateStage3FilterToggle() {
      const count = getActiveStage3FilterCount();
      const toggle = $("#stage3-filter-toggle");
      toggle.textContent = count > 0 ? `筛选（${count}）` : "筛选";
      toggle.classList.toggle("active", count > 0);
      const nonPendingToggle = $("#stage3-filter-non-pending");
      const nonPendingActive = isStage3NonPendingFilterActive();
      nonPendingToggle.classList.toggle("active", nonPendingActive);
      nonPendingToggle.setAttribute("aria-pressed", String(nonPendingActive));
    }

    function syncStage3FilterLanguageInputs() {
      const selected = new Set(state.stage3List.filters.languages);
      document.querySelectorAll("[data-stage3-filter-language]").forEach((input) => {
        input.checked = selected.has(input.value);
      });
    }

    function syncStage3FilterStatusInputs() {
      const selected = new Set(state.stage3List.filters.statuses || []);
      document.querySelectorAll("[data-stage3-filter-status]").forEach((input) => {
        input.checked = selected.has(input.value);
      });
    }

    function updateStage3FilterStatusToggle(values = state.stage3List.filters.statuses) {
      $("#stage3-filter-status-summary").textContent = summarizeStage3Statuses(values || []);
      $("#stage3-filter-status-toggle").setAttribute("aria-expanded", String(!$("#stage3-filter-status-popover").hidden));
    }

    function readSelectedStage3FilterStatuses() {
      return Array.from(document.querySelectorAll("[data-stage3-filter-status]"))
        .filter((input) => input.checked)
        .map((input) => input.value);
    }

    function closeStage3FilterStatusPopover() {
      $("#stage3-filter-status-popover").hidden = true;
      updateStage3FilterStatusToggle(readSelectedStage3FilterStatuses());
    }

    function updateStage3FilterLanguageToggle(values = state.stage3List.filters.languages) {
      $("#stage3-filter-language-summary").textContent = summarizeLanguages(values);
      $("#stage3-filter-language-toggle").setAttribute("aria-expanded", String(!$("#stage3-filter-language-popover").hidden));
    }

    function readSelectedStage3FilterLanguages() {
      return Array.from(document.querySelectorAll("[data-stage3-filter-language]"))
        .filter((input) => input.checked)
        .map((input) => input.value);
    }

    function closeStage3FilterLanguagePopover() {
      $("#stage3-filter-language-popover").hidden = true;
      updateStage3FilterLanguageToggle(readSelectedStage3FilterLanguages());
    }

    function syncStage3FilterForm() {
      const filters = state.stage3List.filters;
      $("#stage3-filter-name-query").value = filters.nameQuery;
      syncStage3FilterStatusInputs();
      updateStage3FilterStatusToggle();
      $("#stage3-filter-stars-min").value = filters.starsMin;
      $("#stage3-filter-stars-max").value = filters.starsMax;
      $("#stage3-filter-created-after").value = filters.createdAfter;
      $("#stage3-filter-created-before").value = filters.createdBefore;
      $("#stage3-filter-pushed-after").value = filters.pushedAfter;
      $("#stage3-filter-pushed-before").value = filters.pushedBefore;
      $("#stage3-filter-discovered-after").value = filters.discoveredAfter;
      $("#stage3-filter-discovered-before").value = filters.discoveredBefore;
      syncStage3FilterLanguageInputs();
      updateStage3FilterLanguageToggle();
    }

    function readStage3FilterForm() {
      return {
        nameQuery: $("#stage3-filter-name-query").value.trim(),
        statuses: readSelectedStage3FilterStatuses(),
        languages: readSelectedStage3FilterLanguages(),
        starsMin: $("#stage3-filter-stars-min").value.trim(),
        starsMax: $("#stage3-filter-stars-max").value.trim(),
        createdAfter: $("#stage3-filter-created-after").value,
        createdBefore: $("#stage3-filter-created-before").value,
        pushedAfter: $("#stage3-filter-pushed-after").value,
        pushedBefore: $("#stage3-filter-pushed-before").value,
        discoveredAfter: $("#stage3-filter-discovered-after").value,
        discoveredBefore: $("#stage3-filter-discovered-before").value,
      };
    }

    function closeStage3FilterPopover() {
      $("#stage3-filter-popover").hidden = true;
    }

    function updateStage2Layout() {
      const detailVisible = Boolean(state.stage2List.detailVisible);
      $("#stage2-layout").classList.toggle("detail-hidden", !detailVisible);
      $("#stage2-layout").classList.toggle("detail-visible", detailVisible);
    }

    function setStage2DetailVisible(visible) {
      state.stage2List.detailVisible = Boolean(visible);
      updateStage2Layout();
    }

    function updateStage3Layout() {
      const detailVisible = Boolean(state.stage3List.detailVisible);
      $("#stage3-layout").classList.toggle("detail-hidden", !detailVisible);
      $("#stage3-layout").classList.toggle("detail-visible", detailVisible);
    }

    function setStage3DetailVisible(visible) {
      state.stage3List.detailVisible = Boolean(visible);
      updateStage3Layout();
    }

    function buildStage2ListParams() {
      const { page, pageSize, sortField, sortOrder, filters } = state.stage2List;
      const statuses = Array.isArray(filters.statuses) ? filters.statuses : [];
      const languages = Array.isArray(filters.languages) ? filters.languages : [];
      const licenses = Array.isArray(filters.licenses) ? filters.licenses : [];
      const params = new URLSearchParams({
        page: String(page),
        page_size: String(pageSize),
        sort_by: sortField,
        sort_order: sortOrder,
      });
      if (filters.nameQuery) params.set("name_query", filters.nameQuery);
      statuses.forEach((status) => params.append("statuses", status));
      languages.forEach((language) => params.append("languages", language));
      licenses.forEach((license) => params.append("licenses", license));
      if (filters.starsMin) params.set("stars_min", filters.starsMin);
      if (filters.starsMax) params.set("stars_max", filters.starsMax);
      setDateParam(params, "created_after", filters.createdAfter);
      setDateParam(params, "created_before", filters.createdBefore);
      setDateParam(params, "pushed_after", filters.pushedAfter);
      setDateParam(params, "pushed_before", filters.pushedBefore);
      setDateParam(params, "discovered_after", filters.discoveredAfter);
      setDateParam(params, "discovered_before", filters.discoveredBefore);
      return params;
    }

    function buildStage3ListParams() {
      const { page, pageSize, sortField, sortOrder, filters } = state.stage3List;
      const statuses = Array.isArray(filters.statuses) ? filters.statuses : [];
      const languages = Array.isArray(filters.languages) ? filters.languages : [];
      const params = new URLSearchParams({
        page: String(page),
        page_size: String(pageSize),
        sort_by: sortField,
        sort_order: sortOrder,
      });
      if (filters.nameQuery) params.set("name_query", filters.nameQuery);
      statuses.forEach((status) => params.append("statuses", status));
      languages.forEach((language) => params.append("languages", language));
      if (filters.starsMin) params.set("stars_min", filters.starsMin);
      if (filters.starsMax) params.set("stars_max", filters.starsMax);
      setDateParam(params, "created_after", filters.createdAfter);
      setDateParam(params, "created_before", filters.createdBefore);
      setDateParam(params, "pushed_after", filters.pushedAfter);
      setDateParam(params, "pushed_before", filters.pushedBefore);
      setDateParam(params, "discovered_after", filters.discoveredAfter);
      setDateParam(params, "discovered_before", filters.discoveredBefore);
      return params;
    }

    function buildStage4SourceListParams() {
      const { page, pageSize, sourceSortField, sourceSortOrder, sourceFilters } = state.stage4List;
      const params = new URLSearchParams({
        page: String(page),
        page_size: String(pageSize),
        sort_by: sourceSortField,
        sort_order: sourceSortOrder,
      });
      if (sourceFilters.nameQuery) params.set("name_query", sourceFilters.nameQuery);
      (Array.isArray(sourceFilters.languages) ? sourceFilters.languages : []).forEach((language) => {
        params.append("languages", language);
      });
      (Array.isArray(sourceFilters.statuses) ? sourceFilters.statuses : []).forEach((status) => {
        params.append("statuses", status);
      });
      return params;
    }

    function buildStage4SourceDetailParams(options = {}) {
      const { includeFilters = true } = options || {};
      const { savepointSortField, savepointSortOrder, savepointFilters } = state.stage4List;
      const params = new URLSearchParams({
        sort_by: savepointSortField,
        sort_order: savepointSortOrder,
      });
      if (!includeFilters) {
        return params;
      }
      if (savepointFilters.entryQuery) params.set("entry_query", savepointFilters.entryQuery);
      (Array.isArray(savepointFilters.statuses) ? savepointFilters.statuses : []).forEach((status) => {
        params.append("statuses", status);
      });
      if (savepointFilters.testCountMin) params.set("test_count_min", savepointFilters.testCountMin);
      if (savepointFilters.testCountMax) params.set("test_count_max", savepointFilters.testCountMax);
      if (savepointFilters.entryPassRateMin) params.set("entry_pass_rate_min", savepointFilters.entryPassRateMin);
      if (savepointFilters.entryPassRateMax) params.set("entry_pass_rate_max", savepointFilters.entryPassRateMax);
      if (savepointFilters.diffLinesMin) params.set("diff_lines_min", savepointFilters.diffLinesMin);
      if (savepointFilters.diffLinesMax) params.set("diff_lines_max", savepointFilters.diffLinesMax);
      return params;
    }

    function formatApiErrorPayload(payload) {
      const detail = (
        payload
        && typeof payload === "object"
        && !Array.isArray(payload)
        && Object.prototype.hasOwnProperty.call(payload, "detail")
      )
        ? payload.detail
        : payload;
      if (typeof detail === "string") {
        return detail;
      }
      if (Array.isArray(detail)) {
        const lines = detail
          .map((item) => {
            if (item && typeof item === "object") {
              const location = Array.isArray(item.loc) ? item.loc.join(".") : "";
              const message = String(item.msg || "").trim();
              if (location && message) {
                return `${location}: ${message}`;
              }
              if (message) {
                return message;
              }
            }
            return String(item || "").trim();
          })
          .filter(Boolean);
        return lines.join("\n");
      }
      if (detail && typeof detail === "object") {
        const lines = [];
        const message = String(detail.message || "").trim();
        if (message) {
          lines.push(message);
        }
        const structuredLists = [
          ["failed_paths", "失败路径"],
          ["failed_repo_checkout_paths", "失败 repo checkout 路径"],
          ["failed_checkpoint_docker_image_refs", "失败 checkpoint 镜像"],
          ["failed_validator_image_refs", "失败 validator 镜像"],
        ];
        structuredLists.forEach(([key, label]) => {
          const values = Array.isArray(detail[key])
            ? detail[key].map((item) => String(item || "").trim()).filter(Boolean)
            : [];
          if (!values.length) {
            return;
          }
          lines.push(`${label}:`);
          values.forEach((value) => lines.push(`- ${value}`));
        });
        if (lines.length) {
          return lines.join("\n");
        }
        try {
          return JSON.stringify(detail);
        } catch (error) {
          return String(detail);
        }
      }
      return detail == null ? "" : String(detail);
    }

    async function api(path, options = {}) {
      const response = await fetch(path, {
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
        ...options,
      });
      if (!response.ok) {
        const text = await response.text();
        let message = "";
        if (text) {
          try {
            message = formatApiErrorPayload(JSON.parse(text));
          } catch (error) {
            message = "";
          }
        }
        throw new Error(message || text || `HTTP ${response.status}`);
      }
      return response.headers.get("content-type")?.includes("application/json")
        ? response.json()
        : response.text();
    }

    function switchTab(tabName) {
      state.activeTab = tabName;
      document.querySelectorAll(".tab-button").forEach((button) => {
        button.classList.toggle("active", button.dataset.tab === tabName);
      });
      document.querySelectorAll(".pane").forEach((pane) => {
        pane.classList.toggle("active", pane.id === `pane-${tabName}`);
      });
      syncStage1JobDetailStream();
      syncStage2RepositoryDetailStream();
      syncStage3RepositoryDetailStream();
      syncStage4SourceDetailStream();
      syncStage4RunDetailStream();
      syncBatchTaskDetailStream();
      if (["stage1", "stage2", "stage3", "stage4"].includes(tabName)) {
        refreshAll().catch((error) => {
          $("#refresh-meta").textContent = `最近刷新失败 ${error.message || String(error)}`;
        });
      }
      if (tabName === "batch") {
        updateBatchLayout();
        loadDataPools();
        loadBatchTasks();
      }
      if (tabName === "data-pools") {
        loadDataPools().then(() => loadDataPoolAssets()).catch((error) => {
          const message = $("#data-pool-message");
          if (message) message.textContent = `加载失败: ${error.message || String(error)}`;
        });
      }
      if (tabName === "assets" && !state.stage2ImageAssets && !state.stage2ImageAssetsLoading) {
        loadStage2ImageAssets();
      }
      if (tabName === "assets" && !state.managedImages && !state.managedImagesLoading) {
        loadManagedImages();
      }
      if (tabName === "assets" && !state.stage2SdkPrewarm && !state.stage2SdkPrewarmLoading) {
        loadStage2SdkPrewarm();
      }
      if (tabName === "stage2") {
        loadStage2Repos();
      }
      if (tabName === "stage4") {
        if (!state.stage4Runtime) {
          loadStage4RuntimeConfig();
        }
        loadStage4Sources();
        if (
          state.stage4List.detailVisible
          && state.stage4List.detailMode === "source"
          && state.selectedStage4SourceGroupKey
        ) {
          loadStage4SourceDetailByKey(state.selectedStage4SourceGroupKey);
        }
        const selectedStage4Run = state.selectedStage4RunId ? currentStage4RunDetail() : null;
        if (
          state.selectedStage4RunId
          && (!selectedStage4Run || isStage4RunRealtimeEligible(selectedStage4Run))
        ) {
          loadStage4RunDetail(state.selectedStage4RunId);
        }
      }
      if (tabName === "stage3") {
        const hasCachedPrewarmSummary = (state.stage3Repos || []).some(
          (repository) => Boolean(repository?.stage3?.image_prewarm),
        );
        if (!hasCachedPrewarmSummary) {
          $("#stage3-repos-body").innerHTML = `<tr><td colspan="9" class="muted">正在加载镜像预热状态...</td></tr>`;
        }
        loadStage3Repos();
      }
    }

    function renderJobAction(job) {
      const actions = [];
      actions.push(`<button class="secondary tiny refresh-inline" type="button" data-job-id="${job.id}">刷新</button>`);
      if (job.can_resume) {
        actions.push(`<button class="secondary tiny resume-inline" type="button" data-job-id="${job.id}">继续执行</button>`);
      }
      if (job.can_pause) {
        actions.push(`<button class="secondary tiny pause-inline" type="button" data-job-id="${job.id}">暂停</button>`);
      }
      if (job.can_delete) {
        actions.push(`<button class="secondary tiny delete-inline" type="button" data-job-id="${job.id}">删除</button>`);
      }
      if (actions.length > 0) {
        return `<div class="action-row">${actions.join("")}</div>`;
      }
      if (job.is_running) {
        return `<span class="muted">${job.pause_requested ? "正在停止" : "执行中"}</span>`;
      }
      if (job.status === "completed") {
        return `<span class="muted">已完成</span>`;
      }
      return `<span class="muted">-</span>`;
    }

    function hideJobDetail() {
      closeStage1JobDetailStream();
      pendingStage1JobDetailPayload = null;
      $("#detail-panel").hidden = true;
      $("#detail-subtitle").textContent = "";
      $("#detail-body").innerHTML = "";
    }

    function clearSelectedJob() {
      state.selectedJobId = null;
      hideJobDetail();
      $("#jobs-body").querySelectorAll("tr[data-job-id]").forEach((row) => {
        row.classList.remove("active");
      });
    }

    async function loadRuntimeConfig() {
      const payload = await api("/api/stage1/runtime");
      state.runtime = payload;
      const jobsInput = $("#max-concurrent-jobs");
      if (jobsInput) {
        jobsInput.value = String(payload.max_concurrent_jobs);
        jobsInput.max = String(payload.max_concurrent_jobs_cap);
        jobsInput.min = "1";
      }
      const partitionInput = $("#max-concurrent-partitions");
      if (partitionInput) {
        partitionInput.value = String(payload.max_concurrent_partitions_default);
        partitionInput.max = String(payload.max_concurrent_partitions_cap);
        partitionInput.min = "1";
      }
      applyRuntimeLimits(payload);
      applyStage1RuntimeDefaultsToBatchForm(payload);
      restoreCrawlFormDraft();
      return payload;
    }

    function applyRuntimeLimits(payload) {
      $("#max-concurrent-jobs-note").textContent = `（当前上限 ${payload.max_concurrent_jobs_cap}）`;
      $("#max-concurrent-partitions-note").textContent = `（当前上限 ${payload.max_concurrent_partitions_cap}）`;
      $("#runtime-message").textContent = `并行抓取任务数 ${payload.max_concurrent_jobs}/${payload.max_concurrent_jobs_cap}，单任务默认分片并发 ${payload.max_concurrent_partitions_default}/${payload.max_concurrent_partitions_cap}，已载入 ${payload.github_token_count} 个 token`;
    }

    function applyStage1RuntimeDefaultsToBatchForm(payload) {
      const form = $("#batch-form");
      if (!form || !payload) return;
      const jobsInput = form.elements.max_concurrent_jobs;
      const partitionsInput = form.elements.max_concurrent_partitions;
      if (jobsInput) {
        if (!String(jobsInput.value || "").trim()) {
          jobsInput.value = String(payload.max_concurrent_jobs);
        }
        jobsInput.min = "1";
        jobsInput.max = String(payload.max_concurrent_jobs_cap);
      }
      if (partitionsInput) {
        if (!String(partitionsInput.value || "").trim()) {
          partitionsInput.value = String(payload.max_concurrent_partitions_default);
        }
        partitionsInput.min = "1";
        partitionsInput.max = String(payload.max_concurrent_partitions_cap);
      }
    }

    async function saveRuntimeConfig() {
      const input = $("#max-concurrent-jobs");
      const tokenInput = $("#github-tokens");
      const nextValue = Number(input.value);
      const rawTokens = String(tokenInput.value || "")
        .split(/\r?\n/)
        .map((value) => value.trim())
        .filter(Boolean);
      if (!nextValue && rawTokens.length === 0) {
        return state.runtime || null;
      }
      const payloadBody = {};
      if (nextValue) {
        payloadBody.max_concurrent_jobs = nextValue;
      }
      if (rawTokens.length > 0) {
        payloadBody.github_tokens = rawTokens;
      }
      $("#save-runtime-btn").disabled = true;
      $("#runtime-message").textContent = "正在应用运行设置...";
      try {
        const payload = await api("/api/stage1/runtime", {
          method: "PATCH",
          body: JSON.stringify(payloadBody),
        });
        state.runtime = payload;
        input.value = String(payload.max_concurrent_jobs);
        input.max = String(payload.max_concurrent_jobs_cap);
        $("#max-concurrent-partitions").max = String(payload.max_concurrent_partitions_cap);
        applyStage1RuntimeDefaultsToBatchForm(payload);
        if (rawTokens.length > 0) {
          tokenInput.value = "";
        }
        applyRuntimeLimits(payload);
        persistCrawlFormDraft();
        return payload;
      } catch (error) {
        $("#runtime-message").textContent = `应用失败: ${error.message}`;
        throw error;
      } finally {
        $("#save-runtime-btn").disabled = false;
      }
    }

    function setFormValue(selector, value) {
      $(selector).value = value == null ? "" : String(value);
    }

    function readOptionalNumber(selector) {
      const raw = $(selector).value.trim();
      if (!raw) {
        return null;
      }
      const value = Number(raw);
      return Number.isFinite(value) ? value : null;
    }

    function apiKeyPreview(value) {
      if (!value) return "";
      return `${String(value).slice(0, 6)}...`;
    }

    function stage2ModelRuntimeLabel(runtime) {
      const plannerModel = String(runtime?.planner?.model || "").trim();
      const workerModel = String(runtime?.worker?.model || "").trim();
      if (plannerModel && workerModel && plannerModel === workerModel) {
        return plannerModel;
      }
      const parts = [];
      if (plannerModel) {
        parts.push(`Planner: ${plannerModel}`);
      }
      if (workerModel) {
        parts.push(`Worker: ${workerModel}`);
      }
      return parts.join("；");
    }

    function stage2RuntimeStatusMessage(prefix, runtime) {
      const label = stage2ModelRuntimeLabel(runtime);
      return label ? `${prefix}：${label}` : `${prefix}：`;
    }

    function renderStage2RuntimeTemplates() {
      const container = $("#stage2-template-strip");
      const templates = state.stage2RuntimeTemplates || [];
      if (!templates.length) {
        container.innerHTML = `<span class="stage2-template-empty">暂无模板</span>`;
        return;
      }
      container.innerHTML = templates.map((template) => {
        const active = String(template.id) === String(state.selectedStage2RuntimeTemplateId) ? " active" : "";
        const titleParts = [template.name];
        if (template.planner_model) titleParts.push(`Planner: ${template.planner_model}`);
        if (template.worker_model) titleParts.push(`Worker: ${template.worker_model}`);
        return `
          <div
            class="stage2-template-chip-shell${active}"
            title="${escapeHtml(titleParts.join(" | "))}"
          >
            <button
              class="stage2-template-chip"
              type="button"
              data-stage2-template-id="${template.id}"
            >${escapeHtml(template.name)}</button>
            <button
              class="stage2-template-chip-delete"
              type="button"
              aria-label="删除模板"
              title="删除模板"
              data-stage2-template-delete-id="${template.id}"
            >×</button>
          </div>
        `;
      }).join("");
    }

    function applyStage2RuntimeSnapshotToForm(snapshot, { plannerSecret = null, workerSecret = null, selectedTemplateId = null } = {}) {
      const concurrency = snapshot.concurrency || {};
      const planner = snapshot.planner || {};
      const worker = snapshot.worker || {};
      const hyperparameters = snapshot.hyperparameters || {};
      const systemCapacity = concurrency.system_capacity ?? state.stage2Runtime?.concurrency?.system_capacity ?? 24;
      setFormValue("#stage2-max-concurrent-runs", concurrency.max_concurrent_runs);
      $("#stage2-max-concurrent-runs").max = String(systemCapacity);
      $("#stage2-max-concurrent-runs-note").textContent = `（系统容量 ${systemCapacity}）`;
      setFormValue("#stage2-planner-model", planner.model);
      setFormValue("#stage2-planner-base-url", planner.base_url);
      setFormValue("#stage2-planner-preset", planner.preset || "default");
      setFormValue("#stage2-planner-max-iterations", planner.max_iterations);
      setFormValue("#stage2-planner-timeout", planner.timeout_seconds);
      setFormValue("#stage2-worker-model", worker.model);
      setFormValue("#stage2-worker-base-url", worker.base_url);
      setFormValue("#stage2-worker-preset", worker.preset || "default");
      setFormValue("#stage2-worker-max-iterations", worker.max_iterations);
      setFormValue("#stage2-worker-timeout", worker.timeout_seconds);
      setFormValue("#stage2-max-worker-attempts", hyperparameters.max_worker_attempts);
      setFormValue("#stage2-quickcheck-sample-size", hyperparameters.quickcheck_sample_size);
      setFormValue("#stage2-collect-timeout", hyperparameters.collect_timeout_seconds ?? hyperparameters.command_timeout_seconds);
      setFormValue("#stage2-run-test-timeout", hyperparameters.run_test_timeout_seconds ?? hyperparameters.command_timeout_seconds);
      setFormValue("#stage2-build-timeout", hyperparameters.build_timeout_seconds);
      setFormValue("#stage2-full-validation-timeout", hyperparameters.full_validation_timeout_seconds);
      setFormValue("#stage2-entry-file-test-count-min", hyperparameters.entry_file_test_count_min);
      setFormValue("#stage2-p2p-file-count-limit", hyperparameters.p2p_file_count_limit);

      state.stage2RuntimeDraftSecrets.planner_api_key = plannerSecret || null;
      state.stage2RuntimeDraftSecrets.worker_api_key = workerSecret || null;
      state.selectedStage2RuntimeTemplateId = selectedTemplateId;
      const plannerKeyInput = $("#stage2-planner-api-key");
      const workerKeyInput = $("#stage2-worker-api-key");
      plannerKeyInput.value = "";
      workerKeyInput.value = "";
      plannerKeyInput.placeholder = planner.api_key_preview || apiKeyPreview(plannerSecret);
      workerKeyInput.placeholder = worker.api_key_preview || apiKeyPreview(workerSecret);
      renderStage2RuntimeTemplates();
      $("#stage2-config-message").textContent = "";
      updateStage2ConfigExpansion();
    }

    function renderStage2RuntimeConfig(payload) {
      applyStage2RuntimeSnapshotToForm(
        {
          concurrency: payload.concurrency || {},
          planner: payload.planner || {},
          worker: payload.worker || {},
          hyperparameters: payload.hyperparameters || {},
        },
        {
          plannerSecret: null,
          workerSecret: null,
          selectedTemplateId: null,
        },
      );
      applyBatchStage2RuntimeDefaults(payload);
    }

    async function loadStage2RuntimeConfig() {
      $("#stage2-config-message").textContent = "正在加载配置...";
      try {
        const payload = await api("/api/stage2/runtime");
        state.stage2Runtime = payload;
        renderStage2RuntimeConfig(payload);
        $("#stage2-config-message").textContent = stage2RuntimeStatusMessage("当前应用", payload);
        return payload;
      } catch (error) {
        $("#stage2-config-message").textContent = `加载失败: ${error.message}`;
        return null;
      }
    }

    async function loadStage2RuntimeTemplates() {
      try {
        const payload = await api("/api/stage2/runtime/templates");
        state.stage2RuntimeTemplates = payload.templates || [];
        renderStage2RuntimeTemplates();
        renderBatchTemplateOptions();
        return payload;
      } catch (error) {
        $("#stage2-config-message").textContent = `模板加载失败: ${error.message}`;
        return null;
      }
    }

    async function applyStage2RuntimeTemplate(templateId) {
      try {
        const payload = await api(`/api/stage2/runtime/templates/${encodeURIComponent(templateId)}`);
        const snapshot = payload.snapshot || {};
        applyStage2RuntimeSnapshotToForm(snapshot, {
          plannerSecret: snapshot.planner?.api_key || null,
          workerSecret: snapshot.worker?.api_key || null,
          selectedTemplateId: payload.id,
        });
        $("#stage2-config-message").textContent = `已将模板「${payload.name}」载入编辑区，点击“应用”即可生效`;
        return payload;
      } catch (error) {
        $("#stage2-config-message").textContent = `模板载入失败: ${error.message}`;
        return null;
      }
    }

    async function saveStage2RuntimeTemplate() {
      const activeTemplate = (state.stage2RuntimeTemplates || []).find(
        (template) => String(template.id) === String(state.selectedStage2RuntimeTemplateId),
      );
      const defaultName = activeTemplate?.name || "";
      const name = window.prompt("模板名称", defaultName);
      if (name == null) {
        return null;
      }
      const normalizedName = String(name).trim();
      if (!normalizedName) {
        $("#stage2-config-message").textContent = "模板名称不能为空";
        return null;
      }
      const saveButton = $("#stage2-template-save");
      saveButton.disabled = true;
      $("#stage2-config-message").textContent = "正在保存模板...";
      try {
        const payload = await api("/api/stage2/runtime/templates", {
          method: "POST",
          body: JSON.stringify({
            name: normalizedName,
            ...readStage2RuntimeConfigForm(),
          }),
        });
        state.selectedStage2RuntimeTemplateId = payload.id;
        await loadStage2RuntimeTemplates();
        $("#stage2-config-message").textContent = `已保存模板「${payload.name}」`;
        return payload;
      } catch (error) {
        $("#stage2-config-message").textContent = `模板保存失败: ${error.message}`;
        return null;
      } finally {
        saveButton.disabled = false;
      }
    }

    async function deleteStage2RuntimeTemplate(templateId) {
      const normalizedId = String(templateId || "").trim();
      if (!normalizedId) {
        return null;
      }
      $("#stage2-config-message").textContent = "正在删除模板...";
      try {
        const payload = await api(`/api/stage2/runtime/templates/${encodeURIComponent(normalizedId)}`, {
          method: "DELETE",
        });
        if (String(state.selectedStage2RuntimeTemplateId) === normalizedId) {
          state.selectedStage2RuntimeTemplateId = null;
        }
        await loadStage2RuntimeTemplates();
        $("#stage2-config-message").textContent = `已删除模板「${payload.name}」`;
        return payload;
      } catch (error) {
        $("#stage2-config-message").textContent = `模板删除失败: ${error.message}`;
        return null;
      }
    }

    function updateStage2ConfigExpansion() {
      const expanded = Boolean(state.stage2ConfigExpanded);
      $("#stage2-runtime-config").classList.toggle("collapsed", !expanded);
      $("#stage2-config-body").hidden = !expanded;
      $("#stage2-config-toggle").setAttribute("aria-expanded", String(expanded));
      $("#stage2-config-caret").textContent = expanded ? "▾" : "▸";
    }

    function toggleStage2ConfigPanel() {
      state.stage2ConfigExpanded = !state.stage2ConfigExpanded;
      updateStage2ConfigExpansion();
    }

    function readStage2RuntimeConfigForm() {
      return {
        max_concurrent_runs: readOptionalNumber("#stage2-max-concurrent-runs"),
        planner_model: $("#stage2-planner-model").value.trim() || null,
        planner_base_url: $("#stage2-planner-base-url").value.trim() || null,
        planner_api_key: $("#stage2-planner-api-key").value.trim() || state.stage2RuntimeDraftSecrets.planner_api_key || null,
        planner_preset: $("#stage2-planner-preset").value || null,
        planner_max_iterations: readOptionalNumber("#stage2-planner-max-iterations"),
        planner_timeout_seconds: readOptionalNumber("#stage2-planner-timeout"),
        worker_model: $("#stage2-worker-model").value.trim() || null,
        worker_base_url: $("#stage2-worker-base-url").value.trim() || null,
        worker_api_key: $("#stage2-worker-api-key").value.trim() || state.stage2RuntimeDraftSecrets.worker_api_key || null,
        worker_preset: $("#stage2-worker-preset").value || null,
        worker_max_iterations: readOptionalNumber("#stage2-worker-max-iterations"),
        worker_timeout_seconds: readOptionalNumber("#stage2-worker-timeout"),
        max_worker_attempts: readOptionalNumber("#stage2-max-worker-attempts"),
        quickcheck_sample_size: readOptionalNumber("#stage2-quickcheck-sample-size"),
        collect_timeout_seconds: readOptionalNumber("#stage2-collect-timeout"),
        run_test_timeout_seconds: readOptionalNumber("#stage2-run-test-timeout"),
        build_timeout_seconds: readOptionalNumber("#stage2-build-timeout"),
        full_validation_timeout_seconds: readOptionalNumber("#stage2-full-validation-timeout"),
        entry_file_test_count_min: readOptionalNumber("#stage2-entry-file-test-count-min"),
        p2p_file_count_limit: readOptionalNumber("#stage2-p2p-file-count-limit"),
      };
    }

    async function saveStage2RuntimeConfig() {
      const applyButton = $("#stage2-config-apply");
      applyButton.disabled = true;
      $("#stage2-config-message").textContent = "正在应用...";
      try {
        const payload = await api("/api/stage2/runtime", {
          method: "PATCH",
          body: JSON.stringify(readStage2RuntimeConfigForm()),
        });
        state.stage2Runtime = payload;
        renderStage2RuntimeConfig(payload);
        $("#stage2-config-message").textContent = stage2RuntimeStatusMessage("已应用", payload);
        return payload;
      } catch (error) {
        $("#stage2-config-message").textContent = `应用失败: ${error.message}`;
        return null;
      } finally {
        applyButton.disabled = false;
      }
    }

    function stage3RuntimeStatusMessage(prefix, runtime) {
      const model = String(runtime?.breaker?.model || "").trim();
      return model ? `${prefix}：${model}` : `${prefix}：`;
    }

    function formatStage3EntryPassRateCeiling(value) {
      const numeric = Number(value);
      if (!Number.isFinite(numeric)) {
        return "-";
      }
      return `${numeric.toFixed(3)} (${formatPercent(numeric)})`;
    }

    function stage3MinRemovedCodeLinesValue(value) {
      if (value == null || value === "") {
        return STAGE3_DEFAULT_MIN_REMOVED_CODE_LINES;
      }
      const numeric = Number(value);
      if (!Number.isFinite(numeric) || numeric < 0) {
        return STAGE3_DEFAULT_MIN_REMOVED_CODE_LINES;
      }
      return Math.trunc(numeric);
    }

    function renderStage3RuntimeTemplates() {
      const container = $("#stage3-template-strip");
      const templates = state.stage3RuntimeTemplates || [];
      if (!templates.length) {
        container.innerHTML = `<span class="stage2-template-empty">暂无模板</span>`;
        return;
      }
      container.innerHTML = templates.map((template) => {
        const active = String(template.id) === String(state.selectedStage3RuntimeTemplateId) ? " active" : "";
        const titleParts = [template.name];
        if (template.breaker_model) titleParts.push(`Breaker: ${template.breaker_model}`);
        return `
          <div
            class="stage2-template-chip-shell${active}"
            title="${escapeHtml(titleParts.join(" | "))}"
          >
            <button
              class="stage2-template-chip"
              type="button"
              data-stage3-template-id="${template.id}"
            >${escapeHtml(template.name)}</button>
            <button
              class="stage2-template-chip-delete"
              type="button"
              aria-label="删除模板"
              title="删除模板"
              data-stage3-template-delete-id="${template.id}"
            >×</button>
          </div>
        `;
      }).join("");
    }

    function applyStage3RuntimeSnapshotToForm(snapshot, { breakerSecret = null, selectedTemplateId = null } = {}) {
      const concurrency = snapshot?.concurrency || {};
      const breaker = snapshot?.breaker || {};
      const hyperparameters = snapshot?.hyperparameters || {};
      const systemCapacity = concurrency.system_capacity ?? state.stage3Runtime?.concurrency?.system_capacity ?? 24;
      setFormValue("#stage3-max-concurrent-runs", concurrency.max_concurrent_runs);
      $("#stage3-max-concurrent-runs").max = String(systemCapacity);
      $("#stage3-max-concurrent-runs-note").textContent = `（系统容量 ${systemCapacity}）`;
      setFormValue("#stage3-breaker-model", breaker.model);
      setFormValue("#stage3-breaker-base-url", breaker.base_url);
      setFormValue("#stage3-breaker-preset", breaker.preset || "default");
      setFormValue("#stage3-breaker-max-iterations", breaker.max_iterations);
      setFormValue("#stage3-breaker-timeout", breaker.timeout_seconds);
      setFormValue("#stage3-build-timeout", hyperparameters.build_timeout_seconds);
      setFormValue("#stage3-run-test-timeout", hyperparameters.run_test_timeout_seconds);
      setFormValue("#stage3-full-validation-timeout", hyperparameters.full_validation_timeout_seconds);
      setFormValue("#stage3-entry-pass-rate-ceiling", hyperparameters.entry_pass_rate_ceiling);
      setFormValue(
        "#stage3-min-removed-code-lines",
        stage3MinRemovedCodeLinesValue(hyperparameters.min_removed_code_lines),
      );
      state.stage3RuntimeDraftSecrets.breaker_api_key = breakerSecret || null;
      state.selectedStage3RuntimeTemplateId = selectedTemplateId;
      const breakerKeyInput = $("#stage3-breaker-api-key");
      breakerKeyInput.value = "";
      breakerKeyInput.placeholder = breaker.api_key_preview || apiKeyPreview(breakerSecret);
      renderStage3RuntimeTemplates();
      updateStage3ConfigExpansion();
    }

    function renderStage3RuntimeConfig(payload) {
      applyStage3RuntimeSnapshotToForm(
        {
          concurrency: payload?.concurrency || {},
          breaker: payload?.breaker || {},
          hyperparameters: payload?.hyperparameters || {},
        },
        {
          breakerSecret: null,
          selectedTemplateId: null,
        },
      );
      applyBatchStage3RuntimeDefaults(payload);
    }

    async function loadStage3RuntimeConfig() {
      $("#stage3-config-message").textContent = "正在加载配置...";
      try {
        const payload = await api("/api/stage3/runtime");
        state.stage3Runtime = payload;
        renderStage3RuntimeConfig(payload);
        $("#stage3-config-message").textContent = stage3RuntimeStatusMessage("当前应用", payload);
        return payload;
      } catch (error) {
        $("#stage3-config-message").textContent = `加载失败: ${error.message}`;
        return null;
      }
    }

    async function loadStage3RuntimeTemplates() {
      try {
        const payload = await api("/api/stage3/runtime/templates");
        state.stage3RuntimeTemplates = payload.templates || [];
        renderStage3RuntimeTemplates();
        renderBatchTemplateOptions();
        return payload;
      } catch (error) {
        $("#stage3-config-message").textContent = `模板加载失败: ${error.message}`;
        return null;
      }
    }

    async function applyStage3RuntimeTemplate(templateId) {
      try {
        const payload = await api(`/api/stage3/runtime/templates/${encodeURIComponent(templateId)}`);
        const snapshot = payload.snapshot || {};
        applyStage3RuntimeSnapshotToForm(snapshot, {
          breakerSecret: snapshot.breaker?.api_key || null,
          selectedTemplateId: payload.id,
        });
        $("#stage3-config-message").textContent = `已将模板「${payload.name}」载入编辑区，点击“应用”即可生效`;
        return payload;
      } catch (error) {
        $("#stage3-config-message").textContent = `模板载入失败: ${error.message}`;
        return null;
      }
    }

    async function saveStage3RuntimeTemplate() {
      const activeTemplate = (state.stage3RuntimeTemplates || []).find(
        (template) => String(template.id) === String(state.selectedStage3RuntimeTemplateId),
      );
      const defaultName = activeTemplate?.name || "";
      const name = window.prompt("模板名称", defaultName);
      if (name == null) {
        return null;
      }
      const normalizedName = String(name).trim();
      if (!normalizedName) {
        $("#stage3-config-message").textContent = "模板名称不能为空";
        return null;
      }
      const saveButton = $("#stage3-template-save");
      saveButton.disabled = true;
      $("#stage3-config-message").textContent = "正在保存模板...";
      try {
        const payload = await api("/api/stage3/runtime/templates", {
          method: "POST",
          body: JSON.stringify({
            name: normalizedName,
            ...readStage3RuntimeConfigForm(),
          }),
        });
        state.selectedStage3RuntimeTemplateId = payload.id;
        await loadStage3RuntimeTemplates();
        $("#stage3-config-message").textContent = `已保存模板「${payload.name}」`;
        return payload;
      } catch (error) {
        $("#stage3-config-message").textContent = `模板保存失败: ${error.message}`;
        return null;
      } finally {
        saveButton.disabled = false;
      }
    }

    async function deleteStage3RuntimeTemplate(templateId) {
      const normalizedId = String(templateId || "").trim();
      if (!normalizedId) {
        return null;
      }
      $("#stage3-config-message").textContent = "正在删除模板...";
      try {
        const payload = await api(`/api/stage3/runtime/templates/${encodeURIComponent(normalizedId)}`, {
          method: "DELETE",
        });
        if (String(state.selectedStage3RuntimeTemplateId) === normalizedId) {
          state.selectedStage3RuntimeTemplateId = null;
        }
        await loadStage3RuntimeTemplates();
        $("#stage3-config-message").textContent = `已删除模板「${payload.name}」`;
        return payload;
      } catch (error) {
        $("#stage3-config-message").textContent = `模板删除失败: ${error.message}`;
        return null;
      }
    }

    function updateStage3ConfigExpansion() {
      const expanded = Boolean(state.stage3ConfigExpanded);
      $("#stage3-runtime-config").classList.toggle("collapsed", !expanded);
      $("#stage3-config-body").hidden = !expanded;
      $("#stage3-config-toggle").setAttribute("aria-expanded", String(expanded));
      $("#stage3-config-caret").textContent = expanded ? "▾" : "▸";
    }

    function toggleStage3ConfigPanel() {
      state.stage3ConfigExpanded = !state.stage3ConfigExpanded;
      updateStage3ConfigExpansion();
    }

    function readStage3RuntimeConfigForm() {
      return {
        max_concurrent_runs: readOptionalNumber("#stage3-max-concurrent-runs"),
        breaker_model: $("#stage3-breaker-model").value.trim() || null,
        breaker_base_url: $("#stage3-breaker-base-url").value.trim() || null,
        breaker_api_key: $("#stage3-breaker-api-key").value.trim() || state.stage3RuntimeDraftSecrets.breaker_api_key || null,
        breaker_preset: $("#stage3-breaker-preset").value || null,
        breaker_max_iterations: readOptionalNumber("#stage3-breaker-max-iterations"),
        breaker_timeout_seconds: readOptionalNumber("#stage3-breaker-timeout"),
        build_timeout_seconds: readOptionalNumber("#stage3-build-timeout"),
        run_test_timeout_seconds: readOptionalNumber("#stage3-run-test-timeout"),
        full_validation_timeout_seconds: readOptionalNumber("#stage3-full-validation-timeout"),
        entry_pass_rate_ceiling: readOptionalNumber("#stage3-entry-pass-rate-ceiling"),
        min_removed_code_lines: readOptionalNumber("#stage3-min-removed-code-lines"),
      };
    }

    async function saveStage3RuntimeConfig() {
      const applyButton = $("#stage3-config-apply");
      applyButton.disabled = true;
      $("#stage3-config-message").textContent = "正在应用...";
      try {
        const payload = await api("/api/stage3/runtime", {
          method: "PATCH",
          body: JSON.stringify(readStage3RuntimeConfigForm()),
        });
        state.stage3Runtime = payload;
        renderStage3RuntimeConfig(payload);
        $("#stage3-config-message").textContent = stage3RuntimeStatusMessage("已应用", payload);
        return payload;
      } catch (error) {
        $("#stage3-config-message").textContent = `应用失败: ${error.message}`;
        return null;
      } finally {
        applyButton.disabled = false;
      }
    }

    function stage4RuntimeStatusMessage(prefix, runtime) {
      const model = String(runtime?.issuer?.model || "").trim();
      return model ? `${prefix}：${model}` : `${prefix}：`;
    }

    function renderStage4RuntimeTemplates() {
      const container = $("#stage4-template-strip");
      const templates = state.stage4RuntimeTemplates || [];
      if (!templates.length) {
        container.innerHTML = `<span class="stage2-template-empty">暂无模板</span>`;
        return;
      }
      container.innerHTML = templates.map((template) => {
        const active = String(template.id) === String(state.selectedStage4RuntimeTemplateId) ? " active" : "";
        const titleParts = [template.name];
        if (template.issuer_model) titleParts.push(`Issuer: ${template.issuer_model}`);
        return `
          <div
            class="stage2-template-chip-shell${active}"
            title="${escapeHtml(titleParts.join(" | "))}"
          >
            <button
              class="stage2-template-chip"
              type="button"
              data-stage4-template-id="${template.id}"
            >${escapeHtml(template.name)}</button>
            <button
              class="stage2-template-chip-delete"
              type="button"
              aria-label="删除模板"
              title="删除模板"
              data-stage4-template-delete-id="${template.id}"
            >×</button>
          </div>
        `;
      }).join("");
    }

    function applyStage4RuntimeSnapshotToForm(snapshot, { issuerSecret = null, selectedTemplateId = null } = {}) {
      const concurrency = snapshot?.concurrency || {};
      const issuer = snapshot?.issuer || {};
      const hyperparameters = snapshot?.hyperparameters || {};
      const systemCapacity = concurrency.system_capacity ?? state.stage4Runtime?.concurrency?.system_capacity ?? 24;
      setFormValue("#stage4-max-concurrent-runs", concurrency.max_concurrent_runs);
      $("#stage4-max-concurrent-runs").max = String(systemCapacity);
      $("#stage4-max-concurrent-runs-note").textContent = `（系统容量 ${systemCapacity}）`;
      setFormValue("#stage4-issuer-model", issuer.model);
      setFormValue("#stage4-issuer-base-url", issuer.base_url);
      setFormValue("#stage4-issuer-preset", issuer.preset || "default");
      setFormValue("#stage4-issuer-max-iterations", issuer.max_iterations);
      setFormValue("#stage4-issuer-timeout", issuer.timeout_seconds);
      setFormValue("#stage4-build-timeout", hyperparameters.build_timeout_seconds);
      state.stage4RuntimeDraftSecrets.issuer_api_key = issuerSecret || null;
      state.selectedStage4RuntimeTemplateId = selectedTemplateId;
      const issuerKeyInput = $("#stage4-issuer-api-key");
      issuerKeyInput.value = "";
      issuerKeyInput.placeholder = issuer.api_key_preview || apiKeyPreview(issuerSecret);
      renderStage4RuntimeTemplates();
      updateStage4ConfigExpansion();
    }

    function renderStage4RuntimeConfig(payload) {
      applyStage4RuntimeSnapshotToForm(
        {
          concurrency: payload?.concurrency || {},
          issuer: payload?.issuer || {},
          hyperparameters: payload?.hyperparameters || {},
          backend: payload?.backend || {},
        },
        { issuerSecret: null, selectedTemplateId: null },
      );
      applyBatchStage4RuntimeDefaults(payload);
    }

    async function loadStage4RuntimeConfig() {
      $("#stage4-config-message").textContent = "正在加载配置...";
      try {
        const payload = await api("/api/stage4/runtime");
        state.stage4Runtime = payload;
        renderStage4RuntimeConfig(payload);
        $("#stage4-config-message").textContent = stage4RuntimeStatusMessage("当前应用", payload);
        return payload;
      } catch (error) {
        $("#stage4-config-message").textContent = `加载失败: ${error.message}`;
        return null;
      }
    }

    async function loadStage4RuntimeTemplates() {
      try {
        const payload = await api("/api/stage4/runtime/templates");
        state.stage4RuntimeTemplates = payload.templates || [];
        renderStage4RuntimeTemplates();
        renderBatchTemplateOptions();
        return payload;
      } catch (error) {
        $("#stage4-config-message").textContent = `模板加载失败: ${error.message}`;
        return null;
      }
    }

    async function applyStage4RuntimeTemplate(templateId) {
      try {
        const payload = await api(`/api/stage4/runtime/templates/${encodeURIComponent(templateId)}`);
        const snapshot = payload.snapshot || {};
        applyStage4RuntimeSnapshotToForm(snapshot, {
          issuerSecret: snapshot.issuer?.api_key || null,
          selectedTemplateId: payload.id,
        });
        $("#stage4-config-message").textContent = `已将模板「${payload.name}」载入编辑区，点击“应用”即可生效`;
        return payload;
      } catch (error) {
        $("#stage4-config-message").textContent = `模板载入失败: ${error.message}`;
        return null;
      }
    }

    async function saveStage4RuntimeTemplate() {
      const activeTemplate = (state.stage4RuntimeTemplates || []).find(
        (template) => String(template.id) === String(state.selectedStage4RuntimeTemplateId),
      );
      const defaultName = activeTemplate?.name || "";
      const name = window.prompt("模板名称", defaultName);
      if (name == null) {
        return null;
      }
      const normalizedName = String(name).trim();
      if (!normalizedName) {
        $("#stage4-config-message").textContent = "模板名称不能为空";
        return null;
      }
      const saveButton = $("#stage4-template-save");
      saveButton.disabled = true;
      $("#stage4-config-message").textContent = "正在保存模板...";
      try {
        const payload = await api("/api/stage4/runtime/templates", {
          method: "POST",
          body: JSON.stringify({
            name: normalizedName,
            ...readStage4RuntimeConfigForm(),
          }),
        });
        state.selectedStage4RuntimeTemplateId = payload.id;
        await loadStage4RuntimeTemplates();
        $("#stage4-config-message").textContent = `已保存模板「${payload.name}」`;
        return payload;
      } catch (error) {
        $("#stage4-config-message").textContent = `模板保存失败: ${error.message}`;
        return null;
      } finally {
        saveButton.disabled = false;
      }
    }

    async function deleteStage4RuntimeTemplate(templateId) {
      const normalizedId = String(templateId || "").trim();
      if (!normalizedId) {
        return null;
      }
      $("#stage4-config-message").textContent = "正在删除模板...";
      try {
        const payload = await api(`/api/stage4/runtime/templates/${encodeURIComponent(normalizedId)}`, {
          method: "DELETE",
        });
        if (String(state.selectedStage4RuntimeTemplateId) === normalizedId) {
          state.selectedStage4RuntimeTemplateId = null;
        }
        await loadStage4RuntimeTemplates();
        $("#stage4-config-message").textContent = `已删除模板「${payload.name}」`;
        return payload;
      } catch (error) {
        $("#stage4-config-message").textContent = `模板删除失败: ${error.message}`;
        return null;
      }
    }

    function updateStage4ConfigExpansion() {
      const expanded = Boolean(state.stage4ConfigExpanded);
      $("#stage4-runtime-config").classList.toggle("collapsed", !expanded);
      $("#stage4-config-body").hidden = !expanded;
      $("#stage4-config-toggle").setAttribute("aria-expanded", String(expanded));
      $("#stage4-config-caret").textContent = expanded ? "▾" : "▸";
    }

    function toggleStage4ConfigPanel() {
      state.stage4ConfigExpanded = !state.stage4ConfigExpanded;
      updateStage4ConfigExpansion();
    }

    function readStage4RuntimeConfigForm() {
      return {
        max_concurrent_runs: readOptionalNumber("#stage4-max-concurrent-runs"),
        issuer_model: $("#stage4-issuer-model").value.trim() || null,
        issuer_base_url: $("#stage4-issuer-base-url").value.trim() || null,
        issuer_api_key: $("#stage4-issuer-api-key").value.trim() || state.stage4RuntimeDraftSecrets.issuer_api_key || null,
        issuer_preset: $("#stage4-issuer-preset").value || null,
        issuer_max_iterations: readOptionalNumber("#stage4-issuer-max-iterations"),
        issuer_timeout_seconds: readOptionalNumber("#stage4-issuer-timeout"),
        build_timeout_seconds: readOptionalNumber("#stage4-build-timeout"),
      };
    }

    async function saveStage4RuntimeConfig() {
      const applyButton = $("#stage4-config-apply");
      applyButton.disabled = true;
      $("#stage4-config-message").textContent = "正在应用...";
      try {
        const payload = await api("/api/stage4/runtime", {
          method: "PATCH",
          body: JSON.stringify(readStage4RuntimeConfigForm()),
        });
        state.stage4Runtime = payload;
        renderStage4RuntimeConfig(payload);
        $("#stage4-config-message").textContent = stage4RuntimeStatusMessage("已应用", payload);
        return payload;
      } catch (error) {
        $("#stage4-config-message").textContent = `应用失败: ${error.message}`;
        return null;
      } finally {
        applyButton.disabled = false;
      }
    }

    async function loadJobs() {
      const payload = await api("/api/stage1/jobs?limit=30");
      state.jobs = payload.jobs;
      if (state.selectedJobId && !payload.jobs.some((job) => job.id === state.selectedJobId)) {
        state.selectedJobId = null;
        hideJobDetail();
      }
      syncStage1JobDetailStream();
      const tbody = $("#jobs-body");
      tbody.innerHTML = payload.jobs.map((job) => {
        const activeClass = job.id === state.selectedJobId ? "task-row active" : "task-row";
        return `
          <tr class="${activeClass}" data-job-id="${job.id}">
            <td>
              <strong>${job.name}</strong><br>
              <span class="muted mono">${job.id.slice(0, 8)}</span>
            </td>
            <td>${statusPill(job.status)}${job.stats?.planning_progress ? renderPlanningProgressSummary(job.stats.planning_progress) : ""}${job.is_running ? `<div class="muted">${job.pause_requested ? "已请求暂停，当前请求结束后立即停止" : "后台线程执行中"}</div>` : ""}</td>
            <td>${job.stats.unique_repositories ?? 0}</td>
            <td>${job.stats.total_partitions ?? 0}</td>
            <td>${formatDate(job.created_at)}</td>
            <td>${renderJobAction(job)}</td>
          </tr>
        `;
      }).join("");

      tbody.querySelectorAll("tr[data-job-id]").forEach((row) => {
        row.addEventListener("click", (event) => {
          if (event.target.closest("button")) return;
          if (String(state.selectedJobId) === String(row.dataset.jobId)) {
            clearSelectedJob();
            return;
          }
          selectJob(row.dataset.jobId);
        });
      });
      tbody.querySelectorAll(".resume-inline").forEach((button) => {
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          await resumeJob(button.dataset.jobId);
        });
      });
      tbody.querySelectorAll(".refresh-inline").forEach((button) => {
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          await refreshJob(button.dataset.jobId);
        });
      });
      tbody.querySelectorAll(".pause-inline").forEach((button) => {
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          await pauseJob(button.dataset.jobId);
        });
      });
      tbody.querySelectorAll(".delete-inline").forEach((button) => {
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          await deleteJob(button.dataset.jobId);
        });
      });
      return payload.jobs;
    }

    async function loadRepos() {
      const payload = await api(`/api/stage1/repos?${buildRepoListParams().toString()}`);
      state.repoList.total = payload.pagination.total;
      state.repoList.totalPages = payload.pagination.total_pages;
      $("#repos-body").innerHTML = payload.repositories.map((repo) => `
        <tr>
          <td><a href="${repo.html_url}" target="_blank" rel="noreferrer">${repo.full_name}</a></td>
          <td>${repo.primary_language || "-"}</td>
          <td>${repo.stargazers_count}</td>
          <td>${formatDate(repo.created_at_github)}</td>
          <td>${formatDate(repo.pushed_at_github)}</td>
          <td>${formatDate(repo.discovered_at)}</td>
        </tr>
      `).join("") || `<tr><td colspan="6" class="muted">当前没有仓库记录。</td></tr>`;
      $("#repos-pagination-meta").textContent = `第 ${payload.pagination.page} / ${payload.pagination.total_pages} 页，共 ${payload.pagination.total} 条`;
      $("#repos-prev-btn").disabled = payload.pagination.page <= 1;
      $("#repos-next-btn").disabled = payload.pagination.page >= payload.pagination.total_pages;
      updateRepoFilterToggle();
      document.querySelectorAll("[data-repo-sort]").forEach((button) => {
        const active = button.dataset.repoSort === state.repoList.sortField;
        button.classList.toggle("active", active);
        const arrow = active ? (state.repoList.sortOrder === "asc" ? " ↑" : " ↓") : "";
        button.textContent = `${button.textContent.replace(/[ ↑↓]+$/, "")}${arrow}`;
      });
      return payload.repositories;
    }

    function renderJobDetail(payload) {
      $("#detail-panel").hidden = false;
      $("#detail-subtitle").textContent = `${payload.job.name} · ${payload.job.id}`;
      const queryAuditOpenAttr = state.detailSections.queryAudit ? " open" : "";
      const repositoriesOpenAttr = state.detailSections.repositories ? " open" : "";

      const partitionsRows = payload.partitions.map((partition) => `
        <tr>
          <td>${statusPill(partition.status)}</td>
          <td class="mono">${formatDate(partition.range_start)} → ${formatDate(partition.range_end)}</td>
          <td>${partition.expected_count}</td>
          <td>${partition.unique_count}</td>
          <td>${partition.fetched_count}</td>
          <td>${partition.queries.length}</td>
        </tr>
      `).join("") || `<tr><td colspan="6" class="muted">当前还没有生成时间分片。</td></tr>`;

      const auditRows = payload.partitions.map((partition, index) => {
        const partitionLabel = `分片 ${index + 1} · ${formatDate(partition.range_start)} → ${formatDate(partition.range_end)}`;
        if (!partition.queries.length) {
          return `
            <tr>
              <td class="mono">${escapeHtml(partitionLabel)}</td>
              <td>-</td>
              <td>-</td>
              <td class="muted">该时间分片还没有 query 审计记录。</td>
              <td>-</td>
              <td>-</td>
              <td>-</td>
              <td>-</td>
              <td>-</td>
            </tr>
          `;
        }
        return partition.queries.map((query) => `
          <tr>
            <td class="mono">${escapeHtml(partitionLabel)}</td>
            <td>${query.query_index + 1}</td>
            <td>${statusPill(query.status)}</td>
            <td><div class="query-text">${escapeHtml(query.query_string)}</div></td>
            <td>${formatAuditMetric(query.reported_total_count)}</td>
            <td>${escapeHtml(formatQueryPageProgress(query))}</td>
            <td>${formatAuditMetric(query.fetched_count)}</td>
            <td>${query.unique_count ?? 0}</td>
            <td>${query.error_message ? `<div class="query-text">${escapeHtml(query.error_message)}</div>` : "-"}</td>
          </tr>
        `).join("");
      }).join("") || `<tr><td colspan="9" class="muted">当前还没有可展示的 query 审计记录。</td></tr>`;

      const repoRows = payload.repositories.map((repo) => `
        <tr>
          <td><a href="${repo.html_url}" target="_blank" rel="noreferrer">${escapeHtml(repo.full_name)}</a></td>
          <td>${repo.primary_language || "-"}</td>
          <td>${repo.stargazers_count}</td>
          <td>
            <div class="source-stack">
              <div class="muted">命中时间：${escapeHtml(formatDate(repo.job_discovered_at))}</div>
              <div class="muted">时间分片：${escapeHtml(formatDate(repo.source_range_start))} → ${escapeHtml(formatDate(repo.source_range_end))}</div>
              <div class="query-text">${escapeHtml(repo.source_query)}</div>
            </div>
          </td>
        </tr>
      `).join("") || `<tr><td colspan="4" class="muted">该任务当前还没有发现仓库。</td></tr>`;

      const planningProgressSection = payload.job.stats?.planning_progress
        ? renderPlanningProgress(payload.job.stats.planning_progress)
        : "";

      $("#detail-body").innerHTML = `
        <div class="detail-grid">
          <div class="detail-card">
            <h4>筛选条件</h4>
            <pre>${escapeHtml(JSON.stringify(formatFilterDisplay(payload.job.filters), null, 2))}</pre>
          </div>
          <div class="detail-card">
            <h4>统计信息</h4>
            <pre>${escapeHtml(JSON.stringify(formatJobStatsDisplay(payload.job.stats), null, 2))}</pre>
          </div>
        </div>
        ${planningProgressSection}
        <div class="detail-card detail-section">
          <h4>时间分片</h4>
          <table>
            <thead>
              <tr>
                <th>状态</th>
                <th>时间范围</th>
                <th>预估</th>
                <th>唯一 Repo</th>
                <th>Fetched</th>
                <th>实际 Query</th>
              </tr>
            </thead>
            <tbody>${partitionsRows}</tbody>
          </table>
        </div>
        <details class="detail-card detail-section collapsible-card" data-detail-section="queryAudit"${queryAuditOpenAttr}>
          <summary class="collapsible-summary">
            <h4>实际 Query 审计</h4>
            <span class="collapse-toggle" aria-hidden="true">▾</span>
          </summary>
          <div class="collapsible-content">
            <table>
              <thead>
                <tr>
                  <th>时间分片</th>
                  <th>#</th>
                  <th>状态</th>
                  <th>实际 Query</th>
                  <th>搜到的</th>
                  <th>页进度</th>
                  <th>抓到的</th>
                  <th>唯一 Repo</th>
                  <th>错误</th>
                </tr>
              </thead>
              <tbody>${auditRows}</tbody>
            </table>
          </div>
        </details>
        <details class="detail-card detail-section collapsible-card" data-detail-section="repositories"${repositoriesOpenAttr}>
          <summary class="collapsible-summary">
            <h4>该任务命中的仓库</h4>
            <span class="collapse-toggle" aria-hidden="true">▾</span>
          </summary>
          <div class="collapsible-content">
            <table>
              <thead>
                  <tr>
                    <th>仓库</th>
                    <th>语言</th>
                    <th>Star 数</th>
                    <th>来源</th>
                  </tr>
              </thead>
              <tbody>${repoRows}</tbody>
            </table>
          </div>
        </details>
        ${payload.job.error_message ? `<div class="detail-card detail-section"><h4>错误信息</h4><pre>${escapeHtml(payload.job.error_message)}</pre></div>` : ""}
      `;
      $("#detail-body").querySelectorAll("[data-detail-section]").forEach((section) => {
        section.addEventListener("toggle", () => {
          state.detailSections[section.dataset.detailSection] = section.open;
        });
      });
    }

    async function loadJobDetail(jobId) {
      const payload = await api(`/api/stage1/jobs/${jobId}`);
      if (state.activeTab !== "stage1" || String(state.selectedJobId || "") !== String(jobId || "")) {
        return payload;
      }
      renderJobDetail(payload);
      syncStage1JobDetailStream();
      return payload;
    }

    function syncStage1JobDetailStream() {
      const selectedJobId = state.selectedJobId;
      if (state.activeTab !== "stage1" || !selectedJobId || typeof EventSource === "undefined") {
        closeStage1JobDetailStream();
        return;
      }
      const selectedJob = state.jobs.find((job) => String(job.id) === String(selectedJobId));
      if (!isStage1JobRealtimeEligible(selectedJob)) {
        closeStage1JobDetailStream();
        return;
      }
      if (
        stage1JobDetailEventSource
        && String(stage1JobDetailStreamJobId) === String(selectedJobId)
      ) {
        return;
      }
      closeStage1JobDetailStream();
      const streamJobId = String(selectedJobId);
      const source = new EventSource(`/api/stage1/jobs/${encodeURIComponent(streamJobId)}/events`);
      stage1JobDetailEventSource = source;
      stage1JobDetailStreamJobId = streamJobId;

      const isCurrentStream = () => (
        stage1JobDetailEventSource === source
        && String(stage1JobDetailStreamJobId) === streamJobId
      );
      const handlePayload = (payload, { terminal = false } = {}) => {
        if (!isCurrentStream()) {
          return;
        }
        mergeJobSummary(payload.job);
        applyLiveStage1JobDetailPayload(payload);
        if (terminal) {
          closeStage1JobDetailStream();
        }
      };
      source.addEventListener("snapshot", (event) => {
        handlePayload(JSON.parse(event.data));
      });
      source.addEventListener("terminal", (event) => {
        handlePayload(JSON.parse(event.data), { terminal: true });
      });
      source.addEventListener("deleted", () => {
        if (!isCurrentStream()) {
          return;
        }
        if (String(state.selectedJobId) === streamJobId) {
          state.selectedJobId = null;
          hideJobDetail();
        } else {
          closeStage1JobDetailStream();
        }
      });
      source.onerror = () => {
        if (!isCurrentStream()) {
          return;
        }
        closeStage1JobDetailStream();
      };
    }

    async function selectJob(jobId) {
      if (String(state.selectedJobId) === String(jobId)) {
        clearSelectedJob();
        return;
      }
      state.selectedJobId = jobId;
      await loadJobs();
      if (String(state.selectedJobId || "") !== String(jobId || "")) {
        return;
      }
      await loadJobDetail(jobId);
    }

    async function refreshJob(jobId) {
      state.selectedJobId = jobId;
      await refreshAll();
    }

    async function resumeJob(jobId) {
      try {
        const payload = readExecutionOptionsFromForm();
        await api(`/api/stage1/jobs/${jobId}/resume`, {
          method: "POST",
          body: JSON.stringify(payload),
        });
        await refreshAll();
        if (state.selectedJobId === jobId) {
          await loadJobDetail(jobId);
        }
      } catch (error) {
        alert(`继续执行失败: ${error.message}`);
      }
    }

    async function pauseJob(jobId) {
      try {
        await api(`/api/stage1/jobs/${jobId}/pause`, { method: "POST" });
        await refreshAll();
        if (state.selectedJobId === jobId) {
          await loadJobDetail(jobId);
        }
      } catch (error) {
        alert(`暂停失败: ${error.message}`);
      }
    }

    async function deleteJob(jobId) {
      const targetJob = state.jobs.find((job) => job.id === jobId);
      const targetName = targetJob?.name || jobId;
      if (!window.confirm(`确认删除抓取任务「${targetName}」吗？相关分片、发现记录，以及仅被该任务引用的仓库记录都会被删除。`)) {
        return;
      }
      try {
        if (targetJob?.is_running) {
          await api(`/api/stage1/jobs/${jobId}/pause`, { method: "POST" });
          let latest = targetJob;
          const deadline = Date.now() + 15000;
          while (Date.now() < deadline) {
            await refreshAll();
            latest = state.jobs.find((job) => job.id === jobId);
            if (!latest || !latest.is_running) {
              break;
            }
            await new Promise((resolve) => setTimeout(resolve, 400));
          }
          if (latest?.is_running) {
            throw new Error("停止还没有完成，请稍后再删除");
          }
        }
        await api(`/api/stage1/jobs/${jobId}`, { method: "DELETE" });
        if (state.selectedJobId === jobId) {
          state.selectedJobId = null;
          hideJobDetail();
        }
        await refreshAll();
      } catch (error) {
        alert(`删除失败: ${error.message}`);
      }
    }

    function hideStage2Detail() {
      closeStage2RepositoryDetailStream();
      pendingStage2RepositoryDetailPayload = null;
      state.stage2RepositoryDetailPayload = null;
      state.selectedStage2RepositoryId = null;
      state.selectedStage2RunId = null;
      resetStage2RunRuntimeEditor();
      state.selectedStage2AssetKey = null;
      state.pendingStage2RunOpenDefaults = false;
      state.selectedStage2AssetVersionByKey = {};
      state.selectedStage2CompletionFileByAssetKey = {};
      state.stage2List.detailVisible = false;
      updateStage2Layout();
      $("#stage2-detail-subtitle").textContent = "选择左侧 repo 查看历史运行与实时轨迹";
      $("#stage2-detail-action").innerHTML = "";
      $("#stage2-detail-body").innerHTML = `<div class="detail-card detail-section"><div class="stage2-empty">当前还没有选中 repo。</div></div>`;
      $("#stage2-repos-body")?.querySelectorAll("[data-stage2-repo-id]").forEach((row) => {
        row.classList.remove("active");
      });
    }

    async function loadStage2Repos() {
      const params = buildStage2ListParams();
      const payload = await api(`/api/stage2/repos?${params.toString()}`);
      state.stage2Repos = payload.repositories;
      state.stage2List.total = payload.pagination.total;
      state.stage2List.totalPages = payload.pagination.total_pages;
      if (
        state.selectedStage2RepositoryId
        && !payload.repositories.some((repo) => String(repo.id) === String(state.selectedStage2RepositoryId))
        && !state.stage2List.detailVisible
      ) {
        state.selectedStage2RepositoryId = null;
        hideStage2Detail();
      }
      syncStage2RepositoryDetailStream();

      $("#stage2-repos-body").innerHTML = payload.repositories.map((repository) => {
        const activeClass = String(repository.id) === String(state.selectedStage2RepositoryId) ? "task-row active" : "task-row";
        const latestRun = repository.stage2?.latest_run;
        const latestActionLabel = latestRun?.updated_at ? formatDate(latestRun.updated_at) : "-";
        const repoStage2Status = repository.stage2?.status || "pending";
        const stage2StatusText = latestRun ? stage2StateLabel(latestRun) : stage2StatusLabel(repoStage2Status);
        return `
          <tr class="${activeClass}" data-stage2-repo-id="${repository.id}">
            <td class="stage2-repo-name-cell">
              <strong>${escapeHtml(repository.full_name)}</strong>
            </td>
            <td>${escapeHtml(repository.primary_language || "-")}</td>
            <td>${repository.stargazers_count}</td>
            <td>${escapeHtml(formatDate(repository.created_at_github))}</td>
            <td>${escapeHtml(formatDate(repository.pushed_at_github))}</td>
            <td>${escapeHtml(formatDate(repository.discovered_at))}</td>
            <td>${escapeHtml(latestActionLabel)}</td>
            <td class="stage2-repo-status-cell">
              ${stage2StatusPill(repoStage2Status, stage2StatusText)}
              <div class="muted">历史 ${repository.stage2?.history_count ?? 0} 次</div>
            </td>
            <td>${renderStage2Action(repository)}</td>
          </tr>
        `;
      }).join("") || `<tr><td colspan="9" class="muted">当前没有可运行的 repo 记录。</td></tr>`;
      $("#stage2-pagination-meta").textContent = `第 ${payload.pagination.page} / ${payload.pagination.total_pages} 页，共 ${payload.pagination.total} 条`;
      $("#stage2-prev-btn").disabled = payload.pagination.page <= 1;
      $("#stage2-next-btn").disabled = payload.pagination.page >= payload.pagination.total_pages;
      updateStage2FilterToggle();
      document.querySelectorAll("[data-stage2-sort]").forEach((button) => {
        const active = button.dataset.stage2Sort === state.stage2List.sortField;
        button.classList.toggle("active", active);
        const arrow = active ? (state.stage2List.sortOrder === "asc" ? " ↑" : " ↓") : "";
        button.textContent = `${button.textContent.replace(/[ ↑↓]+$/, "")}${arrow}`;
      });

      $("#stage2-repos-body").querySelectorAll("tr[data-stage2-repo-id]").forEach((row) => {
        row.addEventListener("click", (event) => {
          if (event.target.closest("button")) return;
          selectStage2Repository(row.dataset.stage2RepoId);
        });
      });
      bindStage2RunActionButtons($("#stage2-repos-body"));
      return payload.repositories;
    }

    function renderStage2RepositoryDetail(payload) {
      const repository = payload.repository;
      const runs = payload.runs || [];
      if (runs.length === 0) {
        state.selectedStage2RunId = null;
      } else if (!runs.some((run) => String(run.id) === String(state.selectedStage2RunId))) {
        state.selectedStage2RunId = runs[0].id;
      }
      const selectedRun = (
        payload.selected_run && String(payload.selected_run.id) === String(state.selectedStage2RunId)
      ) ? payload.selected_run : null;
      const selectedRunIndex = selectedRun ? runs.findIndex((run) => String(run.id) === String(selectedRun.id)) : -1;
      const runNumberById = buildStage2RunNumberById(runs);
      const selectedRunNumber = selectedRun
        ? (runNumberById.get(String(selectedRun.id)) ?? selectedRunIndex + 1)
        : 0;
      setStage2DetailVisible(true);
      $("#stage2-detail-subtitle").textContent = `${repository.full_name} · ${repository.stage2?.history_count ?? 0} 次运行`;
      $("#stage2-detail-action").innerHTML = renderStage2DetailAction(repository);
      bindStage2RunActionButtons($("#stage2-detail-action"));
      $("#stage2-detail-body").innerHTML = `
        ${runs.length > 0 ? `
          <div class="detail-card detail-section">
            <div class="stage2-history-strip-wrap">
              ${renderStage2HistoryStrip(runs, runNumberById)}
            </div>
          </div>
          <div class="stage2-run-stack">
            ${selectedRun
              ? renderStage2Run(selectedRun, selectedRunNumber)
              : `<div class="detail-card detail-section"><div class="stage2-empty">正在加载该次运行的详细存档...</div></div>`}
          </div>
        ` : `<div class="detail-card detail-section"><div class="stage2-empty">该 repo 还没有任何 stage2 历史运行。</div></div>`}
      `;
      bindStage2DetailInteractiveHandlers($("#stage2-detail-body"));
      applyPendingStage2RunOpenDefaults();
    }

    async function loadStage2RepositorySummary(repositoryId) {
      return api(`/api/stage2/repos/${repositoryId}`);
    }

    async function loadStage2RunDetail(repositoryId, runId) {
      return api(`/api/stage2/repos/${repositoryId}/runs/${encodeURIComponent(runId)}`);
    }

    async function loadStage2RepositoryDetail(repositoryId) {
      const summaryPayload = await loadStage2RepositorySummary(repositoryId);
      if (
        String(state.selectedStage2RepositoryId) !== String(repositoryId)
        || state.activeTab !== "stage2"
        || !state.stage2List.detailVisible
      ) {
        return summaryPayload;
      }
      mergeStage2RepositorySummary(summaryPayload.repository);
      const runs = summaryPayload.runs || [];
      if (runs.length === 0) {
        state.selectedStage2RunId = null;
        setStage2RepositoryDetailPayload({
          repository: summaryPayload.repository,
          runs,
          selected_run: null,
        });
        renderStage2RepositoryDetail(currentStage2RepositoryDetailPayload());
        syncStage2RepositoryDetailStream();
        return currentStage2RepositoryDetailPayload();
      }
      if (!runs.some((run) => String(run.id) === String(state.selectedStage2RunId))) {
        state.selectedStage2RunId = runs[0].id;
      }
      setStage2RepositoryDetailPayload({
        repository: summaryPayload.repository,
        runs,
        selected_run: null,
      });
      renderStage2RepositoryDetail(currentStage2RepositoryDetailPayload());
      if (
        isStage2RepositoryRealtimeEligible(summaryPayload.repository)
        && state.activeTab === "stage2"
        && state.stage2List.detailVisible
        && typeof EventSource !== "undefined"
      ) {
        syncStage2RepositoryDetailStream();
        return currentStage2RepositoryDetailPayload();
      }
      const selectedRunId = state.selectedStage2RunId;
      const detailPayload = await loadStage2RunDetail(repositoryId, selectedRunId);
      if (
        String(state.selectedStage2RepositoryId) !== String(repositoryId)
        || String(state.selectedStage2RunId) !== String(selectedRunId)
        || state.activeTab !== "stage2"
        || !state.stage2List.detailVisible
      ) {
        return currentStage2RepositoryDetailPayload();
      }
      setStage2RepositorySelectedRunDetail(detailPayload.run);
      renderStage2RepositoryDetail(currentStage2RepositoryDetailPayload());
      syncStage2RepositoryDetailStream();
      return currentStage2RepositoryDetailPayload();
    }

    function syncStage2RepositoryDetailStream() {
      const selectedRepositoryId = state.selectedStage2RepositoryId;
      const selectedRunId = state.selectedStage2RunId ? String(state.selectedStage2RunId) : null;
      if (
        state.activeTab !== "stage2"
        || !selectedRepositoryId
        || !state.stage2List.detailVisible
        || typeof EventSource === "undefined"
      ) {
        closeStage2RepositoryDetailStream();
        return;
      }
      const selectedRepository = (
        state.stage2Repos.find((repo) => String(repo.id) === String(selectedRepositoryId))
        || currentStage2RepositoryDetailPayload().repository
      );
      if (!isStage2RepositoryRealtimeEligible(selectedRepository)) {
        closeStage2RepositoryDetailStream();
        return;
      }
      if (
        stage2RepositoryDetailEventSource
        && String(stage2RepositoryDetailStreamRepositoryId) === String(selectedRepositoryId)
        && String(stage2RepositoryDetailStreamRunId || "") === String(selectedRunId || "")
      ) {
        return;
      }
      closeStage2RepositoryDetailStream();
      const streamRepositoryId = String(selectedRepositoryId);
      const streamParams = new URLSearchParams();
      if (selectedRunId) {
        streamParams.set("selected_run_id", selectedRunId);
      }
      const streamQuery = streamParams.toString();
      const source = new EventSource(
        `/api/stage2/repos/${encodeURIComponent(streamRepositoryId)}/events${streamQuery ? `?${streamQuery}` : ""}`,
      );
      stage2RepositoryDetailEventSource = source;
      stage2RepositoryDetailStreamRepositoryId = streamRepositoryId;
      stage2RepositoryDetailStreamRunId = selectedRunId;

      const isCurrentStream = () => (
        stage2RepositoryDetailEventSource === source
        && String(stage2RepositoryDetailStreamRepositoryId) === streamRepositoryId
        && String(stage2RepositoryDetailStreamRunId || "") === String(selectedRunId || "")
      );
      const handlePayload = (payload, { terminal = false } = {}) => {
        if (!isCurrentStream()) {
          return;
        }
        mergeStage2RepositorySummary(payload.repository);
        applyLiveStage2RepositoryDetailPayload(payload);
        if (terminal) {
          closeStage2RepositoryDetailStream();
        }
      };
      source.addEventListener("snapshot", (event) => {
        handlePayload(JSON.parse(event.data));
      });
      source.addEventListener("terminal", (event) => {
        handlePayload(JSON.parse(event.data), { terminal: true });
      });
      source.addEventListener("deleted", () => {
        if (!isCurrentStream()) {
          return;
        }
        if (String(state.selectedStage2RepositoryId) === streamRepositoryId) {
          state.selectedStage2RepositoryId = null;
          hideStage2Detail();
        } else {
          closeStage2RepositoryDetailStream();
        }
      });
      source.onerror = () => {
        if (!isCurrentStream()) {
          return;
        }
        closeStage2RepositoryDetailStream();
      };
    }

    async function selectStage2Run(runId) {
      if (!state.selectedStage2RepositoryId || String(state.selectedStage2RunId) === String(runId)) {
        return;
      }
      resetStage2RunRuntimeEditor();
      state.selectedStage2RunId = runId;
      state.selectedStage2AssetKey = "planner_guidance";
      state.pendingStage2RunOpenDefaults = true;
      state.selectedStage2AssetVersionByKey = {};
      state.selectedStage2CompletionFileByAssetKey = {};
      closeStage2RepositoryDetailStream();
      setStage2RepositorySelectedRunDetail(null);
      renderStage2RepositoryDetail(currentStage2RepositoryDetailPayload());
      const currentRepository = currentStage2RepositoryDetailPayload().repository;
      if (
        isStage2RepositoryRealtimeEligible(currentRepository)
        && state.activeTab === "stage2"
        && state.stage2List.detailVisible
        && typeof EventSource !== "undefined"
      ) {
        syncStage2RepositoryDetailStream();
        return;
      }
      try {
        const payload = await loadStage2RunDetail(state.selectedStage2RepositoryId, runId);
        if (
          String(state.selectedStage2RepositoryId) !== String(currentRepository?.id)
          || String(state.selectedStage2RunId) !== String(runId)
        ) {
          return;
        }
        setStage2RepositorySelectedRunDetail(payload.run);
        renderStage2RepositoryDetail(currentStage2RepositoryDetailPayload());
        syncStage2RepositoryDetailStream();
      } catch (error) {
        alert(`加载运行详情失败: ${error.message}`);
      }
    }

    async function selectStage2Repository(repositoryId) {
      closeStage2RepositoryDetailStream();
      pendingStage2RepositoryDetailPayload = null;
      resetStage2RunRuntimeEditor();
      state.selectedStage2RepositoryId = repositoryId;
      state.selectedStage2RunId = null;
      state.selectedStage2AssetKey = null;
      state.pendingStage2RunOpenDefaults = false;
      state.selectedStage2AssetVersionByKey = {};
      state.selectedStage2CompletionFileByAssetKey = {};
      state.stage2RepositoryDetailPayload = null;
      setStage2DetailVisible(true);
      $("#stage2-detail-subtitle").textContent = "正在加载...";
      $("#stage2-detail-action").innerHTML = "";
      $("#stage2-detail-body").innerHTML = `<div class="detail-card detail-section"><div class="stage2-empty">正在加载该 repo 的运行摘要...</div></div>`;
      await loadStage2RepositoryDetail(repositoryId);
    }

    async function deleteStage2Run(runId) {
      const repositoryId = state.selectedStage2RepositoryId;
      if (!repositoryId || !runId) {
        return;
      }
      const currentPayload = currentStage2RepositoryDetailPayload();
      const runNumberById = buildStage2RunNumberById(currentPayload.runs || []);
      const run = (currentPayload.runs || []).find((item) => String(item.id) === String(runId));
      if (run?.is_active) {
        alert("运行中或排队中的任务不能删除。");
        return;
      }
      const runLabel = runNumberById.get(String(runId)) || "";
      const suffix = runLabel ? `运行 ${runLabel}` : "这次运行";
      if (!window.confirm(`确认删除「${suffix}」的所有存档数据吗？此操作不可恢复。`)) {
        return;
      }
      try {
        closeStage2RepositoryDetailStream();
        pendingStage2RepositoryDetailPayload = null;
        const deleteParams = new URLSearchParams();
        if (state.selectedStage2RunId) {
          deleteParams.set("selected_run_id", state.selectedStage2RunId);
        }
        const deleteQuery = deleteParams.toString();
        const payload = await api(
          `/api/stage2/repos/${encodeURIComponent(repositoryId)}/runs/${encodeURIComponent(runId)}${deleteQuery ? `?${deleteQuery}` : ""}`,
          { method: "DELETE" },
        );
        if (String(state.selectedStage2RepositoryId) !== String(repositoryId)) {
          return;
        }
        if (String(state.selectedStage2RunId) === String(runId)) {
          resetStage2RunRuntimeEditor();
          state.selectedStage2RunId = payload.runs?.[0]?.id || null;
          state.selectedStage2AssetKey = null;
          state.selectedStage2AssetVersionByKey = {};
          state.selectedStage2CompletionFileByAssetKey = {};
        }
        mergeStage2RepositorySummary(payload.repository);
        setStage2RepositoryDetailPayload(payload);
        renderStage2RepositoryDetail(currentStage2RepositoryDetailPayload());
        await loadStage2Repos();
        syncStage2RepositoryDetailStream();
        setRefreshMeta();
        if (payload.cleanup_warnings) {
          alert(`运行存档已删除，但部分资产清理失败，将在后续启动时重试清理：\n${formatApiErrorPayload(payload.cleanup_warnings)}`);
        }
      } catch (error) {
        alert(`删除运行存档失败: ${error.message}`);
        syncStage2RepositoryDetailStream();
      }
    }

    async function interruptStage2Run(runId) {
      const repositoryId = state.selectedStage2RepositoryId;
      if (!repositoryId || !runId) {
        return;
      }
      const currentPayload = currentStage2RepositoryDetailPayload();
      const runNumberById = buildStage2RunNumberById(currentPayload.runs || []);
      const run = (currentPayload.runs || []).find((item) => String(item.id) === String(runId));
      if (!run?.is_active) {
        alert("只有运行中或排队中的任务可以中断。");
        return;
      }
      const runLabel = runNumberById.get(String(runId)) || "";
      const suffix = runLabel ? `运行 ${runLabel}` : "这次运行";
      if (!window.confirm(`确认中断「${suffix}」吗？中断后该 run 会以失败归档，正在运行的 agent 会被停止。`)) {
        return;
      }
      try {
        closeStage2RepositoryDetailStream();
        pendingStage2RepositoryDetailPayload = null;
        const interruptParams = new URLSearchParams();
        if (state.selectedStage2RunId) {
          interruptParams.set("selected_run_id", state.selectedStage2RunId);
        }
        const interruptQuery = interruptParams.toString();
        const payload = await api(
          `/api/stage2/repos/${encodeURIComponent(repositoryId)}/runs/${encodeURIComponent(runId)}/interrupt${interruptQuery ? `?${interruptQuery}` : ""}`,
          { method: "POST" },
        );
        if (String(state.selectedStage2RepositoryId) !== String(repositoryId)) {
          return;
        }
        mergeStage2RepositorySummary(payload.repository);
        setStage2RepositoryDetailPayload(payload);
        renderStage2RepositoryDetail(currentStage2RepositoryDetailPayload());
        await loadStage2Repos();
        syncStage2RepositoryDetailStream();
        setRefreshMeta();
      } catch (error) {
        alert(`中断运行失败: ${error.message}`);
        syncStage2RepositoryDetailStream();
      }
    }

    async function rerunStage2RunCommit(runId) {
      const repositoryId = state.selectedStage2RepositoryId;
      if (!repositoryId || !runId) {
        return;
      }
      const currentPayload = currentStage2RepositoryDetailPayload();
      const runNumberById = buildStage2RunNumberById(currentPayload.runs || []);
      const run = (currentPayload.runs || []).find((item) => String(item.id) === String(runId));
      if (!run?.target_commit_sha) {
        alert("这次运行没有记录 commit，无法基于该版本重新运行。");
        return;
      }
      if (run.is_active) {
        alert("运行中或排队中的任务不能基于该 commit 重新运行。");
        return;
      }
      const runLabel = runNumberById.get(String(runId)) || "";
      const suffix = runLabel ? `运行 ${runLabel}` : "这次运行";
      const shortCommit = run.target_commit_sha.slice(0, 7);
      if (!window.confirm(`确认基于「${suffix}」的 commit ${shortCommit} 创建一次新的 stage2 运行吗？`)) {
        return;
      }
      try {
        closeStage2RepositoryDetailStream();
        pendingStage2RepositoryDetailPayload = null;
        $("#stage2-detail-subtitle").textContent = `正在基于 commit ${shortCommit} 创建新的 stage2 运行...`;
        $("#stage2-detail-action").innerHTML = "";
        $("#stage2-detail-body").innerHTML = `<div class="detail-card detail-section"><div class="stage2-empty">重新运行请求已提交，正在等待后端创建并排队...</div></div>`;
        const payload = await api(
          `/api/stage2/repos/${encodeURIComponent(repositoryId)}/runs/${encodeURIComponent(runId)}/rerun`,
          { method: "POST" },
        );
        if (String(state.selectedStage2RepositoryId) !== String(repositoryId)) {
          return;
        }
        state.selectedStage2RunId = payload.created_run_id || payload.selected_run?.id || null;
        state.selectedStage2AssetKey = null;
        state.selectedStage2AssetVersionByKey = {};
        state.selectedStage2CompletionFileByAssetKey = {};
        mergeStage2RepositorySummary(payload.repository);
        setStage2RepositoryDetailPayload(payload);
        renderStage2RepositoryDetail(currentStage2RepositoryDetailPayload());
        await loadStage2Repos();
        syncStage2RepositoryDetailStream();
        setRefreshMeta();
      } catch (error) {
        alert(`基于历史 commit 重新运行失败: ${error.message}`);
        if (String(state.selectedStage2RepositoryId) === String(repositoryId)) {
          try {
            await loadStage2RepositoryDetail(repositoryId);
          } catch (_loadError) {
            // Keep the original action error visible; the next refresh can recover the detail panel.
          }
        }
        syncStage2RepositoryDetailStream();
      }
    }

    async function resumeStage2WorkerRun(runId) {
      const repositoryId = state.selectedStage2RepositoryId;
      if (!repositoryId || !runId) {
        return;
      }
      const currentPayload = currentStage2RepositoryDetailPayload();
      const runNumberById = buildStage2RunNumberById(currentPayload.runs || []);
      const run = (currentPayload.runs || []).find((item) => String(item.id) === String(runId));
      if (!run) {
        return;
      }
      if (run.is_active) {
        alert("运行中或排队中的任务不能续跑。");
        return;
      }
      if (!run.can_resume_worker && !run.can_rerun_worker) {
        alert("这次运行没有可用的续跑上下文。");
        return;
      }
      const runLabel = runNumberById.get(String(runId)) || "";
      const suffix = runLabel ? `运行 ${runLabel}` : "这次运行";
      let endpoint = "resume-worker";
      let resumeMessage = run.resume_kind === "full_validation"
        ? `确认基于「${suffix}」smoke 通过后的保存点继续 full test 吗？`
        : `确认基于「${suffix}」最近一次 validate 保存点创建一次新的 worker 续跑吗？`;
      if (!run.can_resume_worker && run.can_rerun_worker) {
        endpoint = "rerun-worker";
        resumeMessage = `确认基于「${suffix}」已完成的 planner 结果创建一次新的 worker 续跑吗？`;
      }
      if (!window.confirm(resumeMessage)) {
        return;
      }
      try {
        closeStage2RepositoryDetailStream();
        pendingStage2RepositoryDetailPayload = null;
        const payload = await api(
          `/api/stage2/repos/${encodeURIComponent(repositoryId)}/runs/${encodeURIComponent(runId)}/${endpoint}`,
          { method: "POST" },
        );
        if (String(state.selectedStage2RepositoryId) !== String(repositoryId)) {
          return;
        }
        state.selectedStage2RunId = payload.created_run_id || payload.selected_run?.id || null;
        state.selectedStage2AssetKey = null;
        state.selectedStage2AssetVersionByKey = {};
        state.selectedStage2CompletionFileByAssetKey = {};
        mergeStage2RepositorySummary(payload.repository);
        setStage2RepositoryDetailPayload(payload);
        renderStage2RepositoryDetail(currentStage2RepositoryDetailPayload());
        await loadStage2Repos();
        syncStage2RepositoryDetailStream();
        setRefreshMeta();
      } catch (error) {
        alert(`续跑失败: ${error.message}`);
        syncStage2RepositoryDetailStream();
      }
    }

    async function createStage2Run(repositoryId, { rerun }) {
      const repository = (
        state.stage2Repos.find((item) => String(item.id) === String(repositoryId))
        || (String(state.stage2RepositoryDetailPayload?.repository?.id) === String(repositoryId)
          ? state.stage2RepositoryDetailPayload.repository
          : null)
      );
      const name = repository?.full_name || `repo-${repositoryId}`;
      if (rerun && !window.confirm(`确认重新运行「${name}」的 stage2 吗？若远端默认分支最新 commit 与最近一次成功运行的 commit 相同，系统会拒绝本次重新运行。`)) {
        return;
      }
      try {
        closeStage2RepositoryDetailStream();
        pendingStage2RepositoryDetailPayload = null;
        state.selectedStage2RepositoryId = repositoryId;
        state.selectedStage2RunId = null;
        state.selectedStage2AssetKey = null;
        state.selectedStage2AssetVersionByKey = {};
        state.selectedStage2CompletionFileByAssetKey = {};
        state.stage2RepositoryDetailPayload = null;
        setStage2DetailVisible(true);
        $("#stage2-detail-subtitle").textContent = `${rerun ? "正在提交重新运行请求" : "正在提交运行请求"}...`;
        $("#stage2-detail-action").innerHTML = "";
        $("#stage2-detail-body").innerHTML = `<div class="detail-card detail-section"><div class="stage2-empty">请求已提交，正在等待后端创建并排队...</div></div>`;
        const payload = await api(`/api/stage2/repos/${repositoryId}/runs`, { method: "POST" });
        await Promise.all([
          loadStage2RepositoryDetail(repositoryId),
          loadStage2Repos(),
        ]);
        return payload;
      } catch (error) {
        alert(`${rerun ? "重新运行" : "运行"}失败: ${error.message}`);
        if (String(state.selectedStage2RepositoryId) === String(repositoryId)) {
          try {
            await loadStage2RepositoryDetail(repositoryId);
          } catch (_loadError) {
            // Keep the original action error visible; the next refresh can recover the detail panel.
          }
        }
      }
    }

    function buildStage3RepositoryDetailPayload(payload) {
      return {
        repository: payload?.repository || null,
        commit_snapshots: Array.isArray(payload?.commit_snapshots) ? payload.commit_snapshots : [],
        selected_snapshot: payload?.selected_snapshot || null,
        entry_files: Array.isArray(payload?.entry_files) ? payload.entry_files : [],
        selected_entry_file: payload?.selected_entry_file || null,
        runs: Array.isArray(payload?.runs) ? payload.runs : [],
        selected_run: payload?.selected_run || null,
      };
    }

    function currentStage3RepositoryDetailPayload() {
      return state.stage3RepositoryDetailPayload || buildStage3RepositoryDetailPayload(null);
    }

    function resetStage3AssetBrowserState() {
      state.selectedStage3AssetKey = null;
      state.selectedStage3AssetVersionByKey = {};
      state.selectedStage3CompletionFileByAssetKey = {};
    }

    function setStage3RepositoryDetailPayload(payload) {
      state.stage3RepositoryDetailPayload = buildStage3RepositoryDetailPayload(payload);
    }

    function setStage3DetailHeader({
      title,
      backLabel,
      subtitle,
      primaryActionLabel = "",
      primaryActionKind = "",
      primaryActionId = "",
      primaryActionExistingRunId = "",
      primaryActionMode = "",
      primaryActionDisabled = false,
    }) {
      const titleElement = $("#stage3-detail-title");
      if (titleElement) {
        titleElement.textContent = title || "Repo 详细";
      }
      const primaryActionButton = $("#stage3-detail-primary-action-btn");
      if (primaryActionButton) {
        const hasPrimaryAction = Boolean(primaryActionLabel && primaryActionKind);
        primaryActionButton.hidden = !hasPrimaryAction;
        primaryActionButton.textContent = primaryActionLabel || "";
        primaryActionButton.dataset.stage3DetailPrimaryAction = hasPrimaryAction ? primaryActionKind : "";
        primaryActionButton.dataset.stage3PrimaryActionId = hasPrimaryAction ? String(primaryActionId || "") : "";
        primaryActionButton.dataset.stage3PrimaryExistingRunId = hasPrimaryAction ? String(primaryActionExistingRunId || "") : "";
        primaryActionButton.dataset.stage3PrimaryActionMode = hasPrimaryAction ? String(primaryActionMode || "") : "";
        primaryActionButton.disabled = Boolean(primaryActionDisabled);
      }
      const backButton = $("#stage3-detail-back-btn");
      if (backButton) {
        backButton.textContent = backLabel || "返回列表";
      }
      $("#stage3-detail-subtitle").textContent = subtitle || "";
    }

    function renderCurrentStage3Detail(payload = currentStage3RepositoryDetailPayload()) {
      if (state.stage3List.entryDetailVisible) {
        renderStage3EntryFileDetail(payload);
        return;
      }
      renderStage3RepositoryDetail(payload);
    }

    function applyStage3RepositoryDetailPayloadNow(payload) {
      state.selectedStage3SnapshotId = payload.selected_snapshot?.id || null;
      state.selectedStage3EntryFileId = payload.selected_entry_file?.id || null;
      state.selectedStage3RunId = payload.selected_run?.id || null;
      setStage3RepositoryDetailPayload(payload);
      renderCurrentStage3Detail(currentStage3RepositoryDetailPayload());
    }

    function applyLiveStage3RepositoryDetailPayload(payload) {
      if (!payload || String(payload.repository?.id) !== String(state.selectedStage3RepositoryId)) {
        return;
      }
      if (!stage3LivePayloadMatchesCurrentDetailSelection(payload)) {
        return;
      }
      if (state.activeTab !== "stage3" || !state.stage3List.detailVisible) {
        pendingStage3RepositoryDetailPayload = null;
        pendingStage3RepositoryDetailRerender = false;
        return;
      }
      if (hasActiveTextSelection() || isStage3EntryFilterInteractionActive()) {
        pendingStage3RepositoryDetailPayload = payload;
        return;
      }
      pendingStage3RepositoryDetailPayload = null;
      mergeStage3RepositorySummary(payload.repository);
      if (patchStage3EntryFileDetail(payload)) {
        setRefreshMeta();
        return;
      }
      state.selectedStage3SnapshotId = payload.selected_snapshot?.id || null;
      state.selectedStage3EntryFileId = payload.selected_entry_file?.id || null;
      state.selectedStage3RunId = payload.selected_run?.id || null;
      setStage3RepositoryDetailPayload(payload);
      rerenderStage3DetailPreservingScroll();
      setRefreshMeta();
    }

    function stage3LivePayloadMatchesCurrentDetailSelection(payload) {
      if (!state.stage3List.entryDetailVisible) {
        return true;
      }
      const selectedEntryFileId = String(state.selectedStage3EntryFileId || "");
      if (!selectedEntryFileId) {
        return true;
      }
      const payloadEntryFileId = String(payload?.selected_entry_file?.id || "");
      return payloadEntryFileId === selectedEntryFileId;
    }

    function hideStage3Detail() {
      closeStage3RepositoryDetailStream();
      pendingStage3RepositoryDetailPayload = null;
      pendingStage3RepositoryDetailRerender = false;
      state.stage3RepositoryDetailPayload = null;
      state.selectedStage3RepositoryId = null;
      state.selectedStage3SnapshotId = null;
      state.selectedStage3EntryFileId = null;
      state.selectedStage3RunId = null;
      state.stage3RepositoryImagePrewarm = null;
      state.stage3RepositoryImagePrewarmLogScrollState = {};
      resetStage3AssetBrowserState();
      state.stage3List.entryDetailVisible = false;
      state.stage3List.detailVisible = false;
      updateStage3Layout();
      setStage3DetailHeader({
        title: "Repo 详细",
        backLabel: "返回列表",
        subtitle: "选择一个 repo，查看 commit 基线与入口文件",
      });
      $("#stage3-detail-body").innerHTML = `<div class="detail-card detail-section"><div class="stage2-empty">当前还没有选中 repo。</div></div>`;
      $("#stage3-repos-body")?.querySelectorAll("[data-stage3-repo-id]").forEach((row) => {
        row.classList.remove("active");
      });
    }

    function renderStage3ProducedEntryProgress(repository) {
      const stage3 = repository.stage3 || {};
      const produced = Number(stage3.produced_entry_file_count || 0);
      const total = Number(stage3.entry_file_count || 0);
      const ratio = total > 0 ? Math.max(0, Math.min(1, produced / total)) : 0;
      return `
        <div class="stage3-progress-inline">
          <div class="stage3-progress-track">
            <div class="stage3-progress-fill" style="width:${(ratio * 100).toFixed(1)}%"></div>
          </div>
          <span class="stage3-progress-meta">(${produced}/${total})</span>
        </div>
      `;
    }

    function renderStage3RepositoryStatus(repository) {
      const stage3 = repository.stage3 || {};
      return stage3RepositoryStatusPill(stage3.status || "pending");
    }

    function isStage3RepositoryBulkRunInFlight(repositoryId) {
      return Boolean(state.stage3RepoBulkRunInFlight[String(repositoryId || "")]);
    }

    function setStage3RepositoryBulkRunInFlight(repositoryId, inFlight) {
      const key = String(repositoryId || "");
      if (!key) {
        return;
      }
      state.stage3RepoBulkRunInFlight = {
        ...state.stage3RepoBulkRunInFlight,
        [key]: Boolean(inFlight),
      };
    }

    function isStage3RepositoryImagePrewarmInFlight(repositoryId) {
      return Boolean(state.stage3RepoImagePrewarmInFlight[String(repositoryId || "")]);
    }

    function setStage3RepositoryImagePrewarmInFlight(repositoryId, inFlight) {
      const key = String(repositoryId || "");
      if (!key) {
        return;
      }
      state.stage3RepoImagePrewarmInFlight = {
        ...state.stage3RepoImagePrewarmInFlight,
        [key]: Boolean(inFlight),
      };
    }

    function stage3ImagePrewarmStatusClass(status) {
      if (status?.in_progress) return "running";
      if (["queued", "running"].includes(String(status?.last_job_status || ""))) return "running";
      if (status?.last_job_status === "cancelled") return "failed";
      if (status?.needs_update) return "queued";
      if (status?.present) return "succeeded";
      if (status?.last_job_status === "failed") return "failed";
      return "pending";
    }

    function stage3ImagePrewarmStatusLabel(status) {
      if (status?.in_progress) return "预热中";
      if (["queued", "running"].includes(String(status?.last_job_status || ""))) return "预热中";
      if (status?.last_job_status === "cancelled") return "已取消";
      if (status?.needs_update) return "需要更新";
      if (status?.present) return "已预热";
      if (status?.last_job_status === "failed") return "失败";
      return "未预热";
    }

    function stage3ImagePrewarmStatusPill(status) {
      return `<span class="status ${stage3ImagePrewarmStatusClass(status)}">${escapeHtml(stage3ImagePrewarmStatusLabel(status))}</span>`;
    }

    function stage3RepositoryPrewarmSummary(repository) {
      return repository?.stage3?.image_prewarm || null;
    }

    function stage3RepositoryPrewarmStatusClass(summary) {
      const normalized = String(summary?.status || "").trim();
      if (normalized === "running") return "running";
      if (normalized === "succeeded") return "succeeded";
      if (normalized === "cancelled" || normalized === "failed") return "failed";
      if (normalized === "queued") return "queued";
      return "pending";
    }

    function stage3RepositoryPrewarmStatusLabel(summary) {
      return String(summary?.label || "").trim() || "未预热";
    }

    function stage3RepositoryPrewarmStatusPill(summary) {
      return `<span class="status ${stage3RepositoryPrewarmStatusClass(summary)}">${escapeHtml(stage3RepositoryPrewarmStatusLabel(summary))}</span>`;
    }

    function captureStage3ImagePrewarmLogScrollState() {
      const snapshot = {};
      document.querySelectorAll("[data-stage3-image-prewarm-log-target]").forEach((element) => {
        const target = String(element.dataset.stage3ImagePrewarmLogTarget || "").trim();
        if (!target) {
          return;
        }
        const maxScrollTop = Math.max(0, element.scrollHeight - element.clientHeight);
        snapshot[target] = {
          scrollTop: element.scrollTop,
          stickToBottom: maxScrollTop - element.scrollTop <= 24,
        };
      });
      state.stage3RepositoryImagePrewarmLogScrollState = snapshot;
    }

    function restoreStage3ImagePrewarmLogScrollState() {
      const snapshot = state.stage3RepositoryImagePrewarmLogScrollState || {};
      document.querySelectorAll("[data-stage3-image-prewarm-log-target]").forEach((element) => {
        const target = String(element.dataset.stage3ImagePrewarmLogTarget || "").trim();
        const saved = snapshot[target];
        if (!saved) {
          return;
        }
        const maxScrollTop = Math.max(0, element.scrollHeight - element.clientHeight);
        element.scrollTop = saved.stickToBottom ? maxScrollTop : Math.min(saved.scrollTop, maxScrollTop);
      });
    }

    function renderStage3RepositoryAction(repository) {
      const stage3 = repository.stage3 || {};
      const repositoryId = String(repository.id || "");
      const runInFlight = isStage3RepositoryBulkRunInFlight(repositoryId);
      const prewarmInFlight = isStage3RepositoryImagePrewarmInFlight(repositoryId);
      const prewarmCancelInFlight = Boolean(state.stage3RepoImagePrewarmCancelInFlight[repositoryId]);
      const prewarmSummary = stage3RepositoryPrewarmSummary(repository);
      const effectivePrewarmSummary = prewarmInFlight
        ? { status: "running", label: "预热中", can_cancel: false }
        : prewarmSummary;
      const prewarmBusy = Boolean(effectivePrewarmSummary?.can_cancel);
      const canRun = Number(stage3.entry_file_count || 0) > 0 && !runInFlight;
      const prewarmButtonLabel = prewarmBusy ? (prewarmCancelInFlight ? "取消中..." : "取消") : (
        prewarmInFlight ? "提交中..." : "预热镜像"
      );
      return `
        <div class="stage3-repo-action-stack">
          <div class="stage3-repo-prewarm-control">
            ${stage3RepositoryPrewarmStatusPill(effectivePrewarmSummary)}
            <button
              class="${prewarmBusy ? "danger" : "secondary"} tiny"
              type="button"
              ${prewarmBusy
                ? `data-stage3-repo-prewarm-cancel-id="${escapeHtml(repositoryId)}"`
                : `data-stage3-repo-prewarm-id="${escapeHtml(repositoryId)}"`}
              ${(prewarmInFlight || prewarmCancelInFlight) ? "disabled" : ""}
            >${escapeHtml(prewarmButtonLabel)}</button>
          </div>
          <button
            class="secondary tiny"
            type="button"
            data-stage3-repo-run-all-id="${escapeHtml(repositoryId)}"
            ${canRun ? "" : "disabled"}
          >${escapeHtml(runInFlight ? "运行中..." : "运行")}</button>
        </div>
      `;
    }

    function renderStage3SnapshotCard(snapshot) {
      const active = String(snapshot.id) === String(state.selectedStage3SnapshotId);
      const shortCommit = snapshot.source_commit_sha ? snapshot.source_commit_sha.slice(0, 7) : "-";
      return `
        <div
          class="stage3-commit-card${active ? " active" : ""}"
          data-stage3-snapshot-id="${escapeHtml(String(snapshot.id))}"
          role="button"
          tabindex="0"
        >
          <div class="stage3-commit-card-head">
            <div class="stage3-commit-card-title">commit ${escapeHtml(shortCommit)}</div>
          </div>
          <div class="stage3-commit-card-meta">
            <div>入口文件：${escapeHtml(String(snapshot.entry_file_count || 0))}</div>
            <div>入库时间：${escapeHtml(formatDate(snapshot.created_at))}</div>
          </div>
        </div>
      `;
    }

    function renderStage3EntryAction(entryFile) {
      const action = stage3EntryActionDescriptor(entryFile);
      if (!action.visible) {
        return "";
      }
      return `
        <button
          class="primary tiny"
          type="button"
          data-stage3-entry-run-id="${escapeHtml(String(entryFile?.id || ""))}"
          data-stage3-entry-existing-run-id="${escapeHtml(action.existingRunId)}"
          data-stage3-entry-action-mode="${escapeHtml(action.mode)}"
          ${action.disabled ? "disabled" : ""}
          title="${escapeHtml(action.title)}"
        >${escapeHtml(action.label)}</button>
      `;
    }

    function stage3EntryActionDescriptor(entryFile) {
      const status = String(entryFile?.status || "pending");
      const active = status === "queued" || status === "running";
      if (active) {
        return {
          visible: false,
          disabled: true,
          label: "",
          existingRunId: "",
          mode: "",
          title: "这个入口文件已有运行中或排队中的 Stage3 run",
        };
      }
      const latestRunId = String(entryFile?.latest_run_id || "");
      if ((status === "succeeded" || status === "failed") && latestRunId) {
        return {
          visible: true,
          disabled: false,
          label: "重新运行",
          existingRunId: latestRunId,
          mode: "rerun-existing",
          title: "基于最近一条 Stage3 run 的冻结基线，使用当前全局配置重新运行",
        };
      }
      const pendingRunId = status === "pending" ? latestRunId : "";
      return {
        visible: true,
        disabled: false,
        label: status === "succeeded" || status === "failed" ? "重新运行" : "运行",
        existingRunId: pendingRunId,
        mode: pendingRunId ? "start-existing" : "create",
        title: pendingRunId ? "启动这条未运行的 Stage3 run" : "创建并运行这个入口文件的 Stage3 破坏任务",
      };
    }

    function renderStage3EntryFiles(entryFiles) {
      if (!entryFiles || entryFiles.length === 0) {
        return `<div class="stage2-empty">这个 commit 还没有入口文件基线。</div>`;
      }
      return `
        <table class="stage3-entry-table">
          <colgroup>
            <col class="stage3-entry-col-path">
            <col class="stage3-entry-col-tests">
            <col class="stage3-entry-col-pass-rate">
            <col class="stage3-entry-col-latest-operation">
            <col class="stage3-entry-col-data">
            <col class="stage3-entry-col-status">
            <col class="stage3-entry-col-action">
          </colgroup>
          <thead>
            <tr>
              <th>${renderStage3EntrySortButton("入口文件", "test_file_path")}</th>
              <th>${renderStage3EntrySortButton("入口文件测试点数", "baseline_total_tests")}</th>
              <th>${renderStage3EntrySortButton("原始通过率", "baseline_pass_rate")}</th>
              <th>${renderStage3EntrySortButton("最近操作时间", "latest_operation_at")}</th>
              <th>${renderStage3EntrySortButton("数据条数", "savepoint_count")}</th>
              <th>${renderStage3EntrySortButton("状态", "status")}</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            ${entryFiles.map((entryFile) => `
              <tr
                class="${String(entryFile.id) === String(state.selectedStage3EntryFileId) ? "task-row active" : ""}"
                data-stage3-entry-file-id="${escapeHtml(String(entryFile.id))}"
              >
                <td class="mono">${escapeHtml(entryFile.test_file_path || "-")}</td>
                <td>${escapeHtml(String(entryFile.baseline_total_tests || 0))}</td>
                <td>${escapeHtml(formatPercent(entryFile.baseline_pass_rate))}</td>
                <td>${escapeHtml(formatDate(entryFile.latest_operation_at || null))}</td>
                <td>${escapeHtml(String(entryFile.savepoint_count || 0))}</td>
                <td>${stage3EntryFileStatusPill(entryFile.status || "pending")}</td>
                <td class="stage3-entry-action-cell">${renderStage3EntryAction(entryFile)}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
    }

    function renderStage3RunCard(run, runNumber) {
      const runStatus = String(run?.status || "");
      const commitSha = String(run?.source_commit_sha || run?.baseline_commit_sha || "");
      const shortCommit = commitSha ? commitSha.slice(0, 7) : "-";
      const interruptDisabled = run.can_interrupt ? 'title="中断这条 Stage3 run"' : 'disabled title="只有排队中或运行中的 run 可以中断"';
      const resumeDisabled = run.can_resume ? 'title="从最近一次 savepoint 继续续跑"' : 'disabled title="当前 run 没有可续跑的 checkpoint"';
      const isPending = runStatus === "pending";
      const rerunDisabled = isPending
        ? (run.can_start ? 'title="启动这条未运行的 Stage3 run"' : 'disabled title="当前 run 不能启动"')
        : (run.can_rerun ? 'title="基于这条 run 的冻结基线，使用当前全局配置重新拉起一条新的 Stage3 run"' : 'disabled title="当前 run 不能重跑"');
      const rerunActionAttr = isPending
        ? `data-stage3-run-start-id="${escapeHtml(String(run.id))}"`
        : `data-stage3-run-rerun-id="${escapeHtml(String(run.id))}"`;
      const rerunActionLabel = isPending ? "运行" : "重跑";
      const deleteDisabled = run.can_delete ? 'title="删除这次 Stage3 run"' : 'disabled title="当前 run 不能删除"';
      return `
        <div
          class="stage2-history-card stage3-history-card${String(run.id) === String(state.selectedStage3RunId) ? " active" : ""}"
          data-stage3-run-id="${escapeHtml(String(run.id))}"
          role="button"
          tabindex="0"
        >
          <div class="stage2-history-card-main">
            <div class="stage2-history-card-title-row">
              <div class="stage2-history-card-title">运行 ${runNumber}</div>
              <div class="stage2-history-display-status ${escapeHtml(stage3StatusClass(run))}">${escapeHtml(stage3StatusLabel(run))}</div>
            </div>
            <div class="stage2-history-card-meta">
              <div>创建于 ${escapeHtml(formatDate(run.created_at))}</div>
              <div>commit：<span class="mono">${escapeHtml(shortCommit)}</span></div>
              <div>耗时：${escapeHtml(formatDuration(run.duration_seconds))}</div>
            </div>
          </div>
          <div class="stage2-history-card-actions">
            <button
              class="stage2-history-action stage2-history-interrupt"
              type="button"
              data-stage3-run-interrupt-id="${escapeHtml(String(run.id))}"
              ${interruptDisabled}
            >中断</button>
            <button
              class="stage2-history-action stage2-history-resume"
              type="button"
              data-stage3-run-resume-id="${escapeHtml(String(run.id))}"
              ${resumeDisabled}
            >续跑</button>
            <button
              class="stage2-history-action stage2-history-rerun"
              type="button"
              ${rerunActionAttr}
              ${rerunDisabled}
            >${rerunActionLabel}</button>
            <button
              class="stage2-history-action stage2-history-delete"
              type="button"
              data-stage3-run-delete-id="${escapeHtml(String(run.id))}"
              ${deleteDisabled}
            >删除</button>
          </div>
        </div>
      `;
    }

    function stage3ResultLabel(result) {
      const labels = {
        unknown: "-",
        archived: "已归档",
        interrupted: "已中断",
        failed: "失败",
      };
      return labels[String(result || "unknown")] || String(result || "-");
    }

    function stage3RuntimeSnapshot(run) {
      const snapshot = run?.runtime_snapshot || {};
      const stage3 = snapshot.stage3 || {};
      return stage3.runtime || snapshot.runtime || {};
    }

    function stage3RunFrozenAssetSnapshot(payload, run) {
      const runtimeStage3 = run?.runtime_snapshot?.stage3 || {};
      const selectedSnapshot = payload?.selected_snapshot || {};
      return {
        dockerfile_text: runtimeStage3.dockerfile_text || selectedSnapshot.dockerfile_text || "",
        run_script_text: runtimeStage3.run_script_text || selectedSnapshot.run_script_text || "",
        original_p2p_files: Array.isArray(runtimeStage3.original_p2p_files)
          ? runtimeStage3.original_p2p_files
          : (Array.isArray(selectedSnapshot.original_p2p_files) ? selectedSnapshot.original_p2p_files : []),
      };
    }

    function stage3HasJsonContent(value) {
      return Boolean(value) && typeof value === "object" && Object.keys(value).length > 0;
    }

    function stage3AssetValueAvailable(kind, value) {
      if (kind === "text") {
        return Boolean(value);
      }
      if (kind === "json") {
        return stage3HasJsonContent(value);
      }
      if (kind === "savepoint_feedback") {
        return stage3HasJsonContent(value);
      }
      if (kind === "table") {
        return Array.isArray(value) && value.length > 0;
      }
      return false;
    }

    function buildStage3SavepointVersions(savepoints, projector, { kind, emptyText, draftSavepoint = null }) {
      const versions = (Array.isArray(savepoints) ? savepoints : []).map((savepoint) => {
        const value = projector(savepoint);
        return {
          key: `depth:${String(savepoint.depth || 0)}`,
          depth: Number(savepoint.depth || 0),
          label: `深度 ${String(savepoint.depth || 0)}`,
          value,
          available: stage3AssetValueAvailable(kind, value),
          emptyText,
          createdAt: savepoint.updated_at || savepoint.created_at || null,
          isDraft: false,
        };
      });
      if (draftSavepoint && typeof draftSavepoint === "object") {
        const value = projector(draftSavepoint);
        versions.push({
          key: "draft",
          depth: Number(draftSavepoint.depth || 0),
          label: String(draftSavepoint.label || `深度 ${String(draftSavepoint.depth || 0)} + 临时版本`),
          value,
          available: stage3AssetValueAvailable(kind, value),
          emptyText,
          createdAt: draftSavepoint.updated_at || draftSavepoint.created_at || null,
          isDraft: true,
        });
      }
      return versions;
    }

    function makeVersionedStage3Asset({ key, title, kind, emptyText, versions }) {
      const stableAvailableCount = versions.filter((version) => version.available && !version.isDraft).length;
      const draftAvailable = versions.some((version) => version.available && version.isDraft);
      let meta = "暂无";
      if (stableAvailableCount <= 0 && draftAvailable) {
        meta = "临时版本";
      } else if (stableAvailableCount === 1 && !draftAvailable) {
        meta = "已产出";
      } else if (stableAvailableCount > 1 && !draftAvailable) {
        meta = `已产出 ${stableAvailableCount} 个深度版本`;
      } else if (stableAvailableCount > 0 && draftAvailable) {
        meta = `已产出 ${stableAvailableCount} 个深度版本 + 临时版本`;
      }
      return {
        key,
        title,
        kind,
        available: stableAvailableCount > 0 || draftAvailable,
        meta,
        emptyText,
        versions,
      };
    }

    function selectedStage3AssetVersion(asset) {
      if (!asset?.versions || asset.versions.length === 0) {
        return null;
      }
      const latestVersion = asset.versions[asset.versions.length - 1];
      const selectedKey = state.selectedStage3AssetVersionByKey[asset.key];
      const selectedVersion = asset.versions.find((version) => String(version.key) === String(selectedKey));
      return selectedVersion || latestVersion;
    }

    function stage3AssetPreviewModel(asset) {
      const selectedVersion = selectedStage3AssetVersion(asset);
      if (!selectedVersion) {
        return asset;
      }
      return {
        ...asset,
        available: selectedVersion.available,
        meta: selectedVersion.label || asset.meta,
        value: selectedVersion.value,
        emptyText: selectedVersion.emptyText || asset.emptyText,
        selectedVersion,
      };
    }

    function renderStage3AssetVersions(asset) {
      if (!asset?.versions || asset.versions.length === 0) {
        return "";
      }
      const selectedVersion = selectedStage3AssetVersion(asset);
      return `
        <div class="stage2-asset-version-strip">
          ${asset.versions.map((version) => `
            <button
              class="stage2-asset-version-chip${String(version.key) === String(selectedVersion?.key) ? " active" : ""}"
              type="button"
              data-stage3-asset-version="${escapeHtml(version.key)}"
            >${escapeHtml(version.label || "")}</button>
          `).join("")}
        </div>
      `;
    }

    function stage3CompletionArchiveAsset(payload, run) {
      const archives = run?.llm_completion_archives || {};
      const archive = archives.breaker || {};
      const fileCount = Number(archive.file_count || 0);
      const files = Array.isArray(archive.files) ? archive.files : [];
      const repositoryId = payload?.repository?.id;
      const runId = run?.id;
      return {
        key: "breaker_llm_completions",
        title: "Breaker 原始 LLM completion",
        kind: "completion_archive",
        available: fileCount > 0,
        meta: fileCount > 0 ? `${fileCount} 个文件` : "暂无",
        emptyText: "当前还没有 Breaker 原始 LLM completion。",
        value: fileCount > 0 ? archive : {},
        files,
        repositoryId,
        runId,
        archiveRole: "breaker",
        downloadUrl: (fileCount > 0 && repositoryId && runId)
          ? `/api/stage3/repos/${encodeURIComponent(repositoryId)}/runs/${encodeURIComponent(runId)}/llm-completions/breaker.zip`
          : "",
      };
    }

    function selectedStage3CompletionFile(asset) {
      const files = stage2CompletionFiles(asset);
      if (files.length === 0) {
        return null;
      }
      const selectedPath = state.selectedStage3CompletionFileByAssetKey[asset.key];
      return files.find((file) => String(file.path) === String(selectedPath)) || files[0];
    }

    function stage3CompletionFileCacheKey(asset, filePath) {
      return [
        asset?.repositoryId || "",
        asset?.runId || "",
        asset?.archiveRole || "",
        filePath || "",
      ].join("::");
    }

    function currentSelectedStage3Asset() {
      const payload = currentStage3RepositoryDetailPayload();
      const run = payload?.selected_run || null;
      if (!run) {
        return null;
      }
      const assets = buildStage3RunAssets(payload, run);
      const selectedKey = state.selectedStage3AssetKey || assets[0]?.key || null;
      return assets.find((asset) => asset.key === selectedKey) || assets[0] || null;
    }

    function renderStage3OriginalP2PTable(rows) {
      if (!Array.isArray(rows) || rows.length === 0) {
        return `<div class="stage2-empty">当前还没有原始 P2P 集合表。</div>`;
      }
      return `
        <table>
          <thead>
            <tr>
              <th>测试文件</th>
              <th>原始总数</th>
              <th>原始通过</th>
              <th>原始失败</th>
              <th>Error</th>
              <th>Skipped</th>
              <th>原始通过率</th>
            </tr>
          </thead>
          <tbody>
            ${rows.map((item) => `
              <tr>
                <td class="mono">${escapeHtml(String(item.path || item.test_file_path || "-"))}</td>
                <td>${escapeHtml(String(item.baseline_total_tests || 0))}</td>
                <td>${escapeHtml(String(item.baseline_passed_tests || 0))}</td>
                <td>${escapeHtml(String(item.baseline_failed_tests || 0))}</td>
                <td>${escapeHtml(String(item.baseline_error_tests || 0))}</td>
                <td>${escapeHtml(String(item.baseline_skipped_tests || 0))}</td>
                <td>${escapeHtml(formatPercent(item.baseline_pass_rate || 0))}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
    }

    function renderStage3FileResultsTable(results) {
      if (!Array.isArray(results) || results.length === 0) {
        return `<div class="stage2-empty">当前还没有 savepoint full validation 表。</div>`;
      }
      return `
        <table>
          <thead>
            <tr>
              <th>测试文件</th>
              <th>入口</th>
              <th>状态</th>
              <th>总数</th>
              <th>通过</th>
              <th>失败</th>
              <th>Error</th>
              <th>Skipped</th>
              <th>通过率</th>
            </tr>
          </thead>
          <tbody>
            ${results.map((item) => `
              <tr>
                <td class="mono">${escapeHtml(String(item.test_file_path || "-"))}</td>
                <td>${item.is_entry_file ? "是" : "-"}</td>
                <td>${statusPill(item.status || "-")}</td>
                <td>${escapeHtml(String(item.total_tests || 0))}</td>
                <td>${escapeHtml(String(item.passed_tests || 0))}</td>
                <td>${escapeHtml(String(item.failed_tests || 0))}</td>
                <td>${escapeHtml(String(item.error_tests || 0))}</td>
                <td>${escapeHtml(String(item.skipped_tests || 0))}</td>
                <td>${escapeHtml(formatPercent(item.pass_rate || 0))}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
    }

    function renderStage3SavepointFeedbackSummary(value, emptyText) {
      if (!stage3HasJsonContent(value)) {
        return `<div class="stage2-empty">${escapeHtml(emptyText || "当前还没有 savepoint feedback。")}</div>`;
      }
      const feedback = value?.feedback && typeof value.feedback === "object" ? value.feedback : {};
      const collateral = value?.collateral && typeof value.collateral === "object" ? value.collateral : {};
      const summary = value?.summary && typeof value.summary === "object" ? value.summary : {};
      const accepted = Boolean(feedback.accepted);
      const milestoneSummary = String(summary.milestone_summary || "").trim();
      const rationale = String(summary.rationale || "").trim();
      const evaluationMessage = String(feedback.message || "").trim();
      const entryFilePath = String(feedback.entry_file_path || summary.entry_file_path || "-");
      const previousEntryPassRate = Number(feedback.previous_entry_pass_rate);
      const currentEntryPassRate = Number(feedback.entry_pass_rate ?? summary.entry_pass_rate);
      const hasPreviousPassRate = Number.isFinite(previousEntryPassRate);
      const hasCurrentPassRate = Number.isFinite(currentEntryPassRate);
      const passRateDisplay = hasPreviousPassRate && hasCurrentPassRate
        ? `${formatPercent(previousEntryPassRate)} -> ${formatPercent(currentEntryPassRate)}`
        : (hasCurrentPassRate ? formatPercent(currentEntryPassRate) : "-");
      const entryTotalTests = Number(feedback.entry_total_tests || 0);
      const entryPassedTests = Number(feedback.entry_passed_tests || 0);
      const entryFailedTests = Number(feedback.entry_failed_tests || 0);
      const entryErrorTests = Number(feedback.entry_error_tests || 0);
      const entrySkippedTests = Number(feedback.entry_skipped_tests || 0);
      const p2pCount = Number(feedback.p2p_count ?? summary.p2p_count ?? 0);
      const f2pCount = Number(feedback.f2p_count ?? summary.f2p_count ?? 0);
      const collateralCount = Number(collateral.non_entry_failed_count || 0);
      const checkpointStatus = String(summary.checkpoint_status || value.checkpoint_status || "-").trim() || "-";
      const reusableCheckpointReady = summary.reusable_checkpoint_ready ?? value.reusable_checkpoint_ready;
      const collateralFiles = Array.isArray(collateral.non_entry_failed_files) ? collateral.non_entry_failed_files : [];
      const diffStats = stage3DiffStatsSummary(summary);
      const diffLinesDisplay = diffStats.total > 0
        ? `${diffStats.total} 行 (+${diffStats.added} / -${diffStats.removed})`
        : "0 行";
      const metrics = [
        { label: "归档结果", value: accepted ? "已接受" : "未接受" },
        { label: "入口文件", value: entryFilePath, multiline: true },
        { label: "通过率变化", value: passRateDisplay },
        { label: "入口测试点", value: `${entryPassedTests}/${entryTotalTests} 通过` },
        { label: "失败 / Error / Skipped", value: `${entryFailedTests} / ${entryErrorTests} / ${entrySkippedTests}` },
        { label: "P2P / F2P", value: `${p2pCount} / ${f2pCount}` },
        { label: "Collateral", value: collateralCount > 0 ? `${collateralCount} 个文件` : "无" },
        { label: "diff 行数", value: diffLinesDisplay },
        {
          label: "检查点",
          value: reusableCheckpointReady ? `${checkpointStatus} · 可续跑` : checkpointStatus,
        },
      ];
      return `
        <div class="stage3-savepoint-feedback">
          <div class="stage2-section">
            <h5>里程碑摘要</h5>
            <div class="stage3-savepoint-feedback-prose">
              <p>${escapeHtml(milestoneSummary || "未填写")}</p>
            </div>
          </div>
          <div class="stage2-section">
            <h5>归档理由</h5>
            <div class="stage3-savepoint-feedback-prose">
              <p>${escapeHtml(rationale || "未填写")}</p>
            </div>
          </div>
          <div class="stage2-section">
            <h5>评估结论</h5>
            <div class="stage3-savepoint-feedback-prose">
              <p>${escapeHtml(evaluationMessage || "-")}</p>
              <div class="stage3-savepoint-feedback-code">反馈码：${escapeHtml(String(feedback.code || "-"))}</div>
            </div>
          </div>
          <div class="stage2-summary-grid">
            ${metrics.map((metric) => `
              <div class="stage2-metric">
                <div class="label">${escapeHtml(metric.label)}</div>
                <strong${metric.multiline ? ' class="multiline"' : ""}>${escapeHtml(String(metric.value || "-"))}</strong>
              </div>
            `).join("")}
          </div>
          <div class="stage2-section">
            <h5>Collateral 文件</h5>
            ${collateralFiles.length > 0 ? `
              <div class="stage3-savepoint-feedback-list">
                ${collateralFiles.map((item) => `
                  <div class="stage3-savepoint-feedback-list-item">
                    <div class="stage3-savepoint-feedback-list-path mono">${escapeHtml(String(item.test_file_path || "-"))}</div>
                    <div class="stage3-savepoint-feedback-list-meta">
                      <span>${escapeHtml(String(item.status || "-"))}</span>
                      <span>通过率 ${escapeHtml(formatPercent(item.pass_rate || 0))}</span>
                    </div>
                  </div>
                `).join("")}
              </div>
            ` : `<div class="stage2-empty">当前没有非入口文件 collateral。</div>`}
          </div>
        </div>
      `;
    }

    function stage3DiffStatsSummary(summary) {
      const diffStats = summary?.diff_stats && typeof summary.diff_stats === "object" ? summary.diff_stats : {};
      const added = Number(diffStats.added_lines || 0);
      const removed = Number(diffStats.removed_lines || 0);
      const total = Number(diffStats.total_changed_lines || (added + removed));
      return { added, removed, total };
    }

    function buildStage3RunAssets(payload, run) {
      const snapshot = stage3RunFrozenAssetSnapshot(payload, run);
      const savepoints = Array.isArray(run?.savepoints) ? run.savepoints : [];
      const draftSavepoint = run?.draft_savepoint && typeof run.draft_savepoint === "object"
        ? run.draft_savepoint
        : null;
      return [
        {
          key: "dockerfile_text",
          title: "Dockerfile",
          kind: "text",
          available: Boolean(snapshot.dockerfile_text),
          meta: snapshot.dockerfile_text ? "已产出" : "暂无",
          emptyText: "当前没有 Dockerfile 资产。",
          value: snapshot.dockerfile_text || "",
        },
        {
          key: "run_script_text",
          title: "run_script.sh",
          kind: "text",
          available: Boolean(snapshot.run_script_text),
          meta: snapshot.run_script_text ? "已产出" : "暂无",
          emptyText: "当前没有 run_script.sh 资产。",
          value: snapshot.run_script_text || "",
        },
        {
          key: "original_p2p_files",
          title: "原始 P2P 集合表",
          kind: "table",
          available: Array.isArray(snapshot.original_p2p_files) && snapshot.original_p2p_files.length > 0,
          meta: Array.isArray(snapshot.original_p2p_files) && snapshot.original_p2p_files.length > 0 ? "已产出" : "暂无",
          emptyText: "当前没有原始 P2P 集合表。",
          value: Array.isArray(snapshot.original_p2p_files) ? snapshot.original_p2p_files : [],
        },
        makeVersionedStage3Asset({
          key: "gold_patch_text",
          title: "gold patch",
          kind: "text",
          emptyText: "当前还没有 gold patch。",
          versions: buildStage3SavepointVersions(savepoints, (savepoint) => String(savepoint.gold_patch_text || ""), {
            kind: "text",
            emptyText: "该版本还没有 gold patch。",
            draftSavepoint,
          }),
        }),
        makeVersionedStage3Asset({
          key: "feedback_and_collateral",
          title: "savepoint feedback",
          kind: "savepoint_feedback",
          emptyText: "当前还没有 savepoint feedback。",
          versions: buildStage3SavepointVersions(savepoints, (savepoint) => ({
            savepoint_id: savepoint.id,
            depth: savepoint.depth,
            feedback: savepoint.feedback || {},
            collateral: savepoint.collateral || {},
            summary: savepoint.summary || {},
          }), {
            kind: "savepoint_feedback",
            emptyText: "该版本还没有 savepoint feedback。",
            draftSavepoint,
          }),
        }),
        makeVersionedStage3Asset({
          key: "file_results",
          title: "savepoint full validation 表",
          kind: "table",
          emptyText: "当前还没有 savepoint full validation 表。",
          versions: buildStage3SavepointVersions(savepoints, (savepoint) => (
            Array.isArray(savepoint.file_results) ? savepoint.file_results : []
          ), {
            kind: "table",
            emptyText: "该版本还没有 savepoint full validation 表。",
            draftSavepoint,
          }),
        }),
        stage3CompletionArchiveAsset(payload, run),
      ];
    }

    function renderStage3CompletionArchivePreview(asset) {
      if (!asset.available) {
        return `<div class="stage2-empty">${escapeHtml(asset.emptyText)}</div>`;
      }
      const files = stage2CompletionFiles(asset);
      if (files.length === 0) {
        return `<div class="stage2-empty">当前没有可预览的 completion JSON 文件。</div>`;
      }
      const selectedFile = selectedStage3CompletionFile(asset);
      const selectedPath = selectedFile?.path || "";
      const cacheKey = stage3CompletionFileCacheKey(asset, selectedPath);
      const cached = state.stage3CompletionFileCache[cacheKey];
      let preview = `<div class="stage2-empty">正在加载 ${escapeHtml(selectedFile?.path || "JSON 文件")}...</div>`;
      if (cached?.error) {
        preview = `<div class="stage2-empty">读取失败：${escapeHtml(cached.error)}</div>`;
      } else if (cached?.payload) {
        if (cached.payload.kind === "json") {
          preview = `<pre>${escapeHtml(JSON.stringify(cached.payload.value, null, 2))}</pre>`;
        } else {
          preview = `<pre>${escapeHtml(cached.payload.text || "")}</pre>`;
        }
      }
      return `
        <div class="stage2-completion-browser">
          <div class="stage2-completion-file-list">
            ${files.map((file) => {
              const path = String(file.path || "");
              const fileName = path.split("/").filter(Boolean).pop() || path;
              return `
                <button
                  class="stage2-completion-file-item${path === selectedPath ? " active" : ""}"
                  type="button"
                  data-stage3-completion-file-path="${escapeHtml(path)}"
                >
                  <div class="stage2-completion-file-name">${escapeHtml(fileName)}</div>
                  <div class="stage2-completion-file-meta">${escapeHtml(formatBytes(file.size_bytes))} · ${escapeHtml(formatDate(file.modified_at))}</div>
                </button>
              `;
            }).join("")}
          </div>
          <div class="stage2-completion-preview">
            ${preview}
          </div>
        </div>
      `;
    }

    function renderStage3AssetPreview(asset) {
      if (asset.kind === "text") {
        return asset.available
          ? `<pre>${escapeHtml(asset.value)}</pre>`
          : `<div class="stage2-empty">${escapeHtml(asset.emptyText)}</div>`;
      }
      if (asset.kind === "json") {
        return renderJsonPre(asset.value, asset.emptyText);
      }
      if (asset.kind === "savepoint_feedback") {
        return renderStage3SavepointFeedbackSummary(asset.value, asset.emptyText);
      }
      if (asset.kind === "table") {
        if (asset.key === "original_p2p_files") {
          return renderStage3OriginalP2PTable(asset.value);
        }
        return renderStage3FileResultsTable(asset.value);
      }
      if (asset.kind === "completion_archive") {
        return renderStage3CompletionArchivePreview(asset);
      }
      return `<div class="stage2-empty">当前没有可预览的资产内容。</div>`;
    }

    async function ensureStage3CompletionFileLoaded(asset) {
      if (!asset || asset.kind !== "completion_archive" || !asset.available) {
        return;
      }
      const selectedFile = selectedStage3CompletionFile(asset);
      if (!selectedFile?.path || !asset.repositoryId || !asset.runId || !asset.archiveRole) {
        return;
      }
      const cacheKey = stage3CompletionFileCacheKey(asset, selectedFile.path);
      const cached = state.stage3CompletionFileCache[cacheKey];
      if (cached?.loading || cached?.payload) {
        return;
      }
      state.stage3CompletionFileCache[cacheKey] = { loading: true };
      try {
        const params = new URLSearchParams({ path: selectedFile.path });
        const payload = await api(
          `/api/stage3/repos/${encodeURIComponent(asset.repositoryId)}/runs/${encodeURIComponent(asset.runId)}/llm-completions/${encodeURIComponent(asset.archiveRole)}/files?${params.toString()}`,
        );
        state.stage3CompletionFileCache[cacheKey] = { payload };
      } catch (error) {
        state.stage3CompletionFileCache[cacheKey] = { error: error.message || String(error) };
      }
      const currentAsset = currentSelectedStage3Asset();
      const currentFile = currentAsset ? selectedStage3CompletionFile(currentAsset) : null;
      if (
        currentAsset?.kind === "completion_archive"
        && stage3CompletionFileCacheKey(currentAsset, currentFile?.path || "") === cacheKey
      ) {
        rerenderStage3DetailPreservingScroll();
      }
    }

    function ensureSelectedStage3CompletionFileLoaded() {
      ensureStage3CompletionFileLoaded(currentSelectedStage3Asset());
    }

    function stage3CompletionAssetCopyText(asset) {
      if (!asset || asset.kind !== "completion_archive" || !asset.available) {
        return "";
      }
      const selectedFile = selectedStage3CompletionFile(asset);
      if (!selectedFile?.path) {
        return "";
      }
      const cacheKey = stage3CompletionFileCacheKey(asset, selectedFile.path);
      const cached = state.stage3CompletionFileCache[cacheKey];
      if (!cached?.payload) {
        return "";
      }
      if (cached.payload.kind === "json") {
        return JSON.stringify(cached.payload.value, null, 2);
      }
      return String(cached.payload.text || "");
    }

    function stage3AssetCopyText(asset) {
      if (!asset?.available) {
        return "";
      }
      if (asset.kind === "text") {
        return String(asset.value || "");
      }
      if (asset.kind === "json") {
        return JSON.stringify(asset.value || {}, null, 2);
      }
      if (asset.kind === "savepoint_feedback") {
        const feedback = asset?.value?.feedback && typeof asset.value.feedback === "object" ? asset.value.feedback : {};
        const collateral = asset?.value?.collateral && typeof asset.value.collateral === "object" ? asset.value.collateral : {};
        const summary = asset?.value?.summary && typeof asset.value.summary === "object" ? asset.value.summary : {};
        const collateralFiles = Array.isArray(collateral.non_entry_failed_files) ? collateral.non_entry_failed_files : [];
        const diffStats = stage3DiffStatsSummary(summary);
        return [
          `里程碑摘要: ${String(summary.milestone_summary || "未填写")}`,
          `归档理由: ${String(summary.rationale || "未填写")}`,
          `评估结论: ${String(feedback.message || "-")}`,
          `反馈码: ${String(feedback.code || "-")}`,
          `入口文件: ${String(feedback.entry_file_path || summary.entry_file_path || "-")}`,
          `通过率变化: ${formatPercent(feedback.previous_entry_pass_rate || 0)} -> ${formatPercent(feedback.entry_pass_rate || summary.entry_pass_rate || 0)}`,
          `入口测试点: ${Number(feedback.entry_passed_tests || 0)}/${Number(feedback.entry_total_tests || 0)} 通过, ${Number(feedback.entry_failed_tests || 0)} 失败, ${Number(feedback.entry_error_tests || 0)} error, ${Number(feedback.entry_skipped_tests || 0)} skipped`,
          `P2P/F2P: ${Number(feedback.p2p_count ?? summary.p2p_count ?? 0)} / ${Number(feedback.f2p_count ?? summary.f2p_count ?? 0)}`,
          `Collateral: ${Number(collateral.non_entry_failed_count || 0)} 个文件`,
          `diff 行数: ${diffStats.total} 行 (+${diffStats.added} / -${diffStats.removed})`,
          `检查点: ${String(summary.checkpoint_status || asset.value?.checkpoint_status || "-")}${summary.reusable_checkpoint_ready ?? asset.value?.reusable_checkpoint_ready ? " · 可续跑" : ""}`,
          collateralFiles.length > 0
            ? `Collateral 文件:\n${collateralFiles.map((item) => `- ${String(item.test_file_path || "-")} (${String(item.status || "-")}, ${formatPercent(item.pass_rate || 0)})`).join("\n")}`
            : "Collateral 文件: 无",
        ].join("\n");
      }
      if (asset.kind === "completion_archive") {
        return stage3CompletionAssetCopyText(asset);
      }
      return "";
    }

    function renderStage3AssetDownloadAction(asset) {
      return renderStage2AssetDownloadAction(asset);
    }

    function renderStage3AssetCopyAction(asset) {
      if (!stage3AssetCopyText(asset)) {
        return "";
      }
      return `
        <button
          class="secondary tiny stage3-asset-copy"
          type="button"
          data-stage3-asset-copy
        >复制</button>
      `;
    }

    async function copySelectedStage3Asset(button) {
      const payload = currentStage3RepositoryDetailPayload();
      const run = payload?.selected_run || null;
      const asset = run ? buildStage3RunAssets(payload, run).find((item) => item.key === state.selectedStage3AssetKey) : null;
      const previewAsset = asset ? stage3AssetPreviewModel(asset) : null;
      const text = stage3AssetCopyText(previewAsset);
      if (!text) {
        return;
      }
      const originalText = button.textContent;
      try {
        await copyTextToClipboard(text);
        button.textContent = "已复制";
        window.setTimeout(() => {
          if (button.isConnected) {
            button.textContent = originalText || "复制";
          }
        }, 1200);
      } catch (error) {
        alert(`复制失败: ${error.message || String(error)}`);
      }
    }

    function renderStage3AssetBrowser(payload, run) {
      const assets = buildStage3RunAssets(payload, run);
      const validKeys = new Set(assets.map((asset) => asset.key));
      if (!validKeys.has(state.selectedStage3AssetKey)) {
        state.selectedStage3AssetKey = (assets.find((asset) => asset.available) || assets[0] || {}).key || null;
      }
      const selectedAsset = assets.find((asset) => asset.key === state.selectedStage3AssetKey) || assets[0] || null;
      const previewAsset = selectedAsset ? stage3AssetPreviewModel(selectedAsset) : null;
      const availableCount = assets.filter((asset) => asset.available).length;
      if (!selectedAsset) {
        return `<div class="stage2-empty">当前没有输出资产。</div>`;
      }
      return `
        <div class="stage2-asset-browser">
          <div class="stage2-asset-panel">
            <div class="stage2-asset-panel-head">
              <h5>输出资产</h5>
              <div class="stage2-asset-panel-note">${assets.length} 个资产 · ${availableCount} 个已生成</div>
            </div>
            <div class="stage2-asset-list">
              ${assets.map((asset) => `
                <button
                  class="stage2-asset-item${asset.key === state.selectedStage3AssetKey ? " active" : ""}"
                  type="button"
                  data-stage3-asset-key="${asset.key}"
                >
                  <div class="stage2-asset-item-title">${escapeHtml(asset.title)}</div>
                  <div class="stage2-asset-item-meta">${escapeHtml(asset.meta)}</div>
                </button>
              `).join("")}
            </div>
          </div>
          <div class="stage2-asset-panel">
            <div class="stage2-asset-panel-head">
              <div class="stage2-asset-preview-main">
                <div class="stage2-asset-preview-title">${escapeHtml(selectedAsset.title)}</div>
                ${renderStage3AssetVersions(selectedAsset)}
                ${renderStage3AssetCopyAction(previewAsset)}
                ${renderStage3AssetDownloadAction(previewAsset)}
              </div>
              <div class="stage2-asset-panel-note">${escapeHtml(previewAsset.meta)}</div>
            </div>
            <div class="stage2-asset-preview-body">
              ${renderStage3AssetPreview(previewAsset)}
            </div>
          </div>
        </div>
      `;
    }

    function resetStage3RunRuntimeEditor() {
      state.stage3RunRuntimeEditor = {
        runId: null,
        draft: null,
        breakerApiKeyPreview: "",
        message: "",
        saving: false,
      };
    }

    function stage3RunRuntimeSnapshotToDraft(runtime) {
      const breaker = runtime.breaker || {};
      const hyperparameters = runtime.hyperparameters || {};
      return {
        breaker_model: breaker.model || "",
        breaker_base_url: breaker.base_url || "",
        breaker_api_key: null,
        breaker_preset: breaker.preset || "default",
        breaker_max_iterations: breaker.max_iterations ?? null,
        breaker_timeout_seconds: breaker.timeout_seconds ?? null,
        build_timeout_seconds: hyperparameters.build_timeout_seconds ?? null,
        run_test_timeout_seconds: hyperparameters.run_test_timeout_seconds ?? null,
        full_validation_timeout_seconds: hyperparameters.full_validation_timeout_seconds ?? null,
        entry_pass_rate_ceiling: hyperparameters.entry_pass_rate_ceiling ?? null,
        min_removed_code_lines: stage3MinRemovedCodeLinesValue(hyperparameters.min_removed_code_lines),
      };
    }

    function isEditingStage3RunRuntime(runId) {
      return String(state.stage3RunRuntimeEditor.runId || "") === String(runId || "");
    }

    function startStage3RunRuntimeEdit(run) {
      const runtime = stage3RuntimeSnapshot(run);
      state.stage3RunRuntimeEditor = {
        runId: String(run.id),
        draft: stage3RunRuntimeSnapshotToDraft(runtime),
        breakerApiKeyPreview: String(runtime?.breaker?.api_key_preview || ""),
        message: "",
        saving: false,
      };
      renderCurrentStage3Detail(currentStage3RepositoryDetailPayload());
    }

    function cancelStage3RunRuntimeEdit(runId) {
      if (!isEditingStage3RunRuntime(runId)) {
        return;
      }
      resetStage3RunRuntimeEditor();
      renderCurrentStage3Detail(currentStage3RepositoryDetailPayload());
    }

    function updateStage3RunRuntimeEditorDraft(field, value) {
      if (!state.stage3RunRuntimeEditor.draft) {
        return;
      }
      state.stage3RunRuntimeEditor.draft = {
        ...state.stage3RunRuntimeEditor.draft,
        [field]: value,
      };
      state.stage3RunRuntimeEditor.message = "";
    }

    function readStage3RunRuntimeConfigForm() {
      return {
        breaker_model: $("#stage3-run-breaker-model").value.trim() || null,
        breaker_base_url: $("#stage3-run-breaker-base-url").value.trim() || null,
        breaker_api_key: $("#stage3-run-breaker-api-key").value.trim() || null,
        breaker_preset: $("#stage3-run-breaker-preset").value || null,
        breaker_max_iterations: readOptionalNumber("#stage3-run-breaker-max-iterations"),
        breaker_timeout_seconds: readOptionalNumber("#stage3-run-breaker-timeout"),
        build_timeout_seconds: readOptionalNumber("#stage3-run-build-timeout"),
        run_test_timeout_seconds: readOptionalNumber("#stage3-run-run-test-timeout"),
        full_validation_timeout_seconds: readOptionalNumber("#stage3-run-full-validation-timeout"),
        entry_pass_rate_ceiling: readOptionalNumber("#stage3-run-entry-pass-rate-ceiling"),
        min_removed_code_lines: readOptionalNumber("#stage3-run-min-removed-code-lines"),
      };
    }

    async function saveStage3RunRuntimeConfig(repositoryId, runId) {
      if (!repositoryId || !runId) {
        return null;
      }
      const saveButton = document.querySelector(`[data-stage3-run-runtime-save-id="${CSS.escape(String(runId))}"]`);
      if (saveButton) {
        saveButton.disabled = true;
      }
      state.stage3RunRuntimeEditor.saving = true;
      state.stage3RunRuntimeEditor.message = "正在保存...";
      try {
        const payload = await api(
          `/api/stage3/repos/${encodeURIComponent(repositoryId)}/runs/${encodeURIComponent(runId)}/runtime`,
          {
            method: "PATCH",
            body: JSON.stringify(readStage3RunRuntimeConfigForm()),
          },
        );
        resetStage3RunRuntimeEditor();
        setStage3RepositoryDetailPayload(payload);
        state.selectedStage3SnapshotId = payload.selected_snapshot?.id || state.selectedStage3SnapshotId;
        state.selectedStage3EntryFileId = payload.selected_entry_file?.id || state.selectedStage3EntryFileId;
        state.selectedStage3RunId = payload.selected_run?.id || runId;
        resetStage3AssetBrowserState();
        renderCurrentStage3Detail(currentStage3RepositoryDetailPayload());
        await loadStage3Repos();
        setRefreshMeta();
        return payload;
      } catch (error) {
        state.stage3RunRuntimeEditor.saving = false;
        state.stage3RunRuntimeEditor.message = `保存失败: ${error.message}`;
        renderCurrentStage3Detail(currentStage3RepositoryDetailPayload());
        return null;
      } finally {
        if (saveButton) {
          saveButton.disabled = false;
        }
      }
    }

    function renderStage3RunRuntimeEditor(run) {
      const editor = state.stage3RunRuntimeEditor;
      const draft = editor.draft || stage3RunRuntimeSnapshotToDraft(stage3RuntimeSnapshot(run));
      const breakerApiKeyPlaceholder = editor.breakerApiKeyPreview || "保留当前 Key";
      return `
        <div class="stage2-config-grid">
          <div class="stage2-config-section">Breaker agent 配置</div>
          <div class="stage2-agent-config-row">
            ${renderStage2RunRuntimeEditableField("模型", `<input id="stage3-run-breaker-model" data-stage3-run-runtime-field="breaker_model" type="text" value="${escapeHtml(String(draft.breaker_model || ""))}">`)}
            ${renderStage2RunRuntimeEditableField("BaseURL", `<input id="stage3-run-breaker-base-url" data-stage3-run-runtime-field="breaker_base_url" type="url" value="${escapeHtml(String(draft.breaker_base_url || ""))}">`)}
            ${renderStage2RunRuntimeEditableField("APIKey", `<input id="stage3-run-breaker-api-key" data-stage3-run-runtime-field="breaker_api_key" type="password" autocomplete="off" placeholder="${escapeHtml(breakerApiKeyPlaceholder)}" value="${escapeHtml(String(draft.breaker_api_key || ""))}">`)}
            ${renderStage2RunRuntimeEditableField("OpenHands 预设", `
              <select id="stage3-run-breaker-preset" data-stage3-run-runtime-field="breaker_preset">
                <option value="gpt5"${draft.breaker_preset === "gpt5" ? " selected" : ""}>gpt5：patch 写文件</option>
                <option value="default"${draft.breaker_preset === "default" ? " selected" : ""}>default：默认文件编辑器</option>
              </select>
            `)}
            ${renderStage2RunRuntimeEditableField("最大迭代步数", `<input id="stage3-run-breaker-max-iterations" data-stage3-run-runtime-field="breaker_max_iterations" type="number" min="10" max="1000" step="1" value="${escapeHtml(draft.breaker_max_iterations == null ? "" : String(draft.breaker_max_iterations))}">`)}
            ${renderStage2RunRuntimeEditableField("超时时限（秒）", `<input id="stage3-run-breaker-timeout" data-stage3-run-runtime-field="breaker_timeout_seconds" type="number" min="30" max="14400" step="1" value="${escapeHtml(draft.breaker_timeout_seconds == null ? "" : String(draft.breaker_timeout_seconds))}">`)}
          </div>
          <div class="stage2-config-section">超参数</div>
          <div class="stage2-hyper-config-row">
            ${renderStage2RunRuntimeEditableField("build 超时上限（秒）", `<input id="stage3-run-build-timeout" data-stage3-run-runtime-field="build_timeout_seconds" type="number" min="30" max="7200" step="1" value="${escapeHtml(draft.build_timeout_seconds == null ? "" : String(draft.build_timeout_seconds))}">`)}
            ${renderStage2RunRuntimeEditableField("run 单个测试文件超时上限（秒）", `<input id="stage3-run-run-test-timeout" data-stage3-run-runtime-field="run_test_timeout_seconds" type="number" min="10" max="7200" step="1" value="${escapeHtml(draft.run_test_timeout_seconds == null ? "" : String(draft.run_test_timeout_seconds))}">`)}
            ${renderStage2RunRuntimeEditableField("full validation 超时上限（秒）", `<input id="stage3-run-full-validation-timeout" data-stage3-run-runtime-field="full_validation_timeout_seconds" type="number" min="30" max="28800" step="1" value="${escapeHtml(draft.full_validation_timeout_seconds == null ? "" : String(draft.full_validation_timeout_seconds))}">`)}
            ${renderStage2RunRuntimeEditableField("入口文件最高允许通过率（0-1）", `<input id="stage3-run-entry-pass-rate-ceiling" data-stage3-run-runtime-field="entry_pass_rate_ceiling" type="number" min="0" max="1" step="0.01" value="${escapeHtml(draft.entry_pass_rate_ceiling == null ? "" : String(draft.entry_pass_rate_ceiling))}">`)}
            ${renderStage2RunRuntimeEditableField("最少删除实现代码行数", `<input id="stage3-run-min-removed-code-lines" data-stage3-run-runtime-field="min_removed_code_lines" type="number" min="0" max="10000" step="1" value="${escapeHtml(draft.min_removed_code_lines == null ? "" : String(draft.min_removed_code_lines))}">`)}
          </div>
        </div>
        <div class="stage2-runtime-panel-message">${escapeHtml(editor.message || "")}</div>
      `;
    }

    function renderStage3RunRuntimePanel(run) {
      const runtime = stage3RuntimeSnapshot(run);
      const runtimeDraft = stage3RunRuntimeSnapshotToDraft(runtime);
      const breaker = runtime.breaker || {};
      const breakerApiKeyValue = breaker.api_key_preview || (breaker.api_key ? apiKeyPreview(breaker.api_key) : "未归档");
      const runtimeLabel = stage3RuntimeStatusMessage("Breaker", runtime);
      const panelOpenAttr = state.stage3DetailSections.runtimeConfig ? " open" : "";
      const isEditing = isEditingStage3RunRuntime(run.id);
      const canEdit = !run.is_active;
      const headerActions = isEditing
        ? `
          <div class="stage2-runtime-panel-actions">
            <button class="secondary tiny" type="button" data-stage3-run-runtime-cancel-id="${escapeHtml(String(run.id))}">取消</button>
            <button class="primary tiny" type="button" data-stage3-run-runtime-save-id="${escapeHtml(String(run.id))}" ${state.stage3RunRuntimeEditor.saving ? "disabled" : ""}>${state.stage3RunRuntimeEditor.saving ? "保存中..." : "保存"}</button>
          </div>
        `
        : `<button class="secondary tiny" type="button" data-stage3-run-runtime-edit-id="${escapeHtml(String(run.id))}" ${canEdit ? "" : "disabled"}>编辑</button>`;
      return `
        <details class="detail-card detail-section collapsible-card" data-stage3-detail-section="runtimeConfig"${panelOpenAttr}>
          <summary class="collapsible-summary">
            <div class="stage2-runtime-panel-head">
              <div class="stage2-runtime-panel-head-left">
                <h4>运行配置</h4>
                ${headerActions}
              </div>
              <div class="stage2-runtime-panel-summary">${escapeHtml(runtimeLabel || "展开查看该次运行冻结的 breaker agent 与超参数配置")}</div>
            </div>
            <span class="collapse-toggle">⌃</span>
          </summary>
          <div class="collapsible-content">
            ${isEditing ? renderStage3RunRuntimeEditor(run) : `
              <div class="stage2-config-grid">
                <div class="stage2-config-section">Breaker agent 配置</div>
                <div class="stage2-agent-config-row">
                  ${renderStage2RuntimeReadonlyField("模型", breaker.model, { mono: true })}
                  ${renderStage2RuntimeReadonlyField("BaseURL", breaker.base_url, { mono: true })}
                  ${renderStage2RuntimeReadonlyField("APIKey", breakerApiKeyValue, { mono: true })}
                  ${renderStage2RuntimeReadonlyField("OpenHands 预设", breaker.preset)}
                  ${renderStage2RuntimeReadonlyField("最大迭代步数", breaker.max_iterations)}
                  ${renderStage2RuntimeReadonlyField("超时时限（秒）", breaker.timeout_seconds)}
                </div>
                <div class="stage2-config-section">超参数</div>
                <div class="stage2-hyper-config-row">
                  ${renderStage2RuntimeReadonlyField("build 超时上限（秒）", runtimeDraft.build_timeout_seconds)}
                  ${renderStage2RuntimeReadonlyField("run 单个测试文件超时上限（秒）", runtimeDraft.run_test_timeout_seconds)}
                  ${renderStage2RuntimeReadonlyField("full validation 超时上限（秒）", runtimeDraft.full_validation_timeout_seconds)}
                  ${renderStage2RuntimeReadonlyField("入口文件最高允许通过率", formatStage3EntryPassRateCeiling(runtimeDraft.entry_pass_rate_ceiling))}
                  ${renderStage2RuntimeReadonlyField("最少删除实现代码行数", runtimeDraft.min_removed_code_lines)}
                </div>
              </div>
            `}
          </div>
        </details>
      `;
    }

    function stage3LatestAcceptedEntryPassRate(run) {
      const summary = run?.summary && typeof run.summary === "object" ? run.summary : {};
      const summaryRate = Number(summary.latest_entry_pass_rate);
      if (Number.isFinite(summaryRate)) {
        return summaryRate;
      }
      const savepoints = Array.isArray(run?.savepoints) ? run.savepoints : [];
      for (let index = savepoints.length - 1; index >= 0; index -= 1) {
        const savepoint = savepoints[index];
        const directRate = Number(savepoint?.entry_pass_rate);
        if (Number.isFinite(directRate)) {
          return directRate;
        }
        const feedbackRate = Number(savepoint?.feedback?.entry_pass_rate);
        if (Number.isFinite(feedbackRate)) {
          return feedbackRate;
        }
        const summaryRateValue = Number(savepoint?.summary?.entry_pass_rate);
        if (Number.isFinite(summaryRateValue)) {
          return summaryRateValue;
        }
      }
      const draftRate = Number(run?.draft_savepoint?.feedback?.entry_pass_rate);
      if (Number.isFinite(draftRate)) {
        return draftRate;
      }
      return null;
    }

    function stage3CurrentEntryPassRateDisplay(run) {
      const summary = run?.summary && typeof run.summary === "object" ? run.summary : {};
      const runtime = stage3RuntimeSnapshot(run);
      const hyperparameters = runtime?.hyperparameters && typeof runtime.hyperparameters === "object"
        ? runtime.hyperparameters
        : {};
      const latestEntryPassRate = stage3LatestAcceptedEntryPassRate(run);
      const ceiling = Number(
        Number.isFinite(Number(summary.entry_pass_rate_ceiling))
          ? summary.entry_pass_rate_ceiling
          : hyperparameters.entry_pass_rate_ceiling
      );
      if (Number.isFinite(latestEntryPassRate) && Number.isFinite(ceiling)) {
        return `${formatPercent(latestEntryPassRate)} (阈值: ${formatPercent(ceiling)})`;
      }
      if (Number.isFinite(latestEntryPassRate)) {
        return formatPercent(latestEntryPassRate);
      }
      if (Number.isFinite(ceiling)) {
        return `- (阈值: ${formatPercent(ceiling)})`;
      }
      return "-";
    }

    function renderStage3SummaryGrid(run) {
      const summary = run?.summary || {};
      const savepoints = Array.isArray(run?.savepoints) ? run.savepoints : [];
      const metrics = [
        { label: "状态", value: stage3StatusLabel(run) },
        { label: "耗时", value: formatDuration(run?.duration_seconds) },
        { label: "TOKEN", value: stage3TokenDisplayText(run), multiline: true },
        { label: "当前阶段", value: stage3PhaseLabel(run?.phase) },
        { label: "当前入口通过率", value: stage3CurrentEntryPassRateDisplay(run) },
        { label: "Depth", value: summary.latest_depth ?? "-" },
        { label: "数据生产条数", value: summary.savepoint_count ?? savepoints.length },
      ];
      return `
        <div class="stage2-summary-grid">
          ${metrics.map((metric) => `
            <div class="stage2-metric">
              <div class="label">${escapeHtml(metric.label)}</div>
              <strong${metric.multiline ? ' class="multiline"' : (metric.mono ? ' class="mono"' : "")}>${escapeHtml(String(metric.value))}</strong>
            </div>
          `).join("")}
        </div>
      `;
    }

    function renderStage3RunSummaryStack(run) {
      return `
        <div class="stage2-summary-stack">
          ${renderStage3RunRuntimePanel(run)}
          ${renderStage3SummaryGrid(run)}
        </div>
      `;
    }

    function renderStage3RunOutputAssets(payload, run) {
      return renderStage3AssetBrowser(payload, run);
    }

    function renderStage3RunError(run) {
      return run?.error_message
        ? `<div class="stage2-section"><h5>错误信息</h5><pre>${escapeHtml(run.error_message)}</pre></div>`
        : "";
    }

    function renderStage3Run(payload, run, runNumber) {
      const runId = escapeHtml(String(run?.id || ""));
      return `
        <section class="detail-card detail-section stage2-run" data-stage3-run-panel-id="${runId}">
          <div class="stage2-run-header">
            <div>
              <h4>运行 ${escapeHtml(String(runNumber))}</h4>
            </div>
            <div class="button-row">
              <div class="muted mono">${runId}</div>
            </div>
          </div>

          <div data-stage3-summary-slot>
            ${renderStage3RunSummaryStack(run)}
          </div>

          <div class="stage2-section" data-stage3-assets-slot>
            ${renderStage3RunOutputAssets(payload, run)}
          </div>

          <div class="stage2-section">
            <h5>运行轨迹</h5>
            <div data-stage3-events-slot>
              ${renderStage2Events(run?.events || [], { phaseLabel: stage3PhaseLabel })}
            </div>
          </div>

          <div data-stage3-error-slot>
            ${renderStage3RunError(run)}
          </div>
        </section>
      `;
    }

    function renderStage3SelectedRunSection(payload) {
      const runs = Array.isArray(payload.runs) ? payload.runs : [];
      const selectedRun = payload.selected_run;
      if (runs.length === 0) {
        return `
          <div class="detail-card detail-section">
            <div class="section-actions">
              <h4>运行历史</h4>
            </div>
            <div class="stage2-empty">这个入口文件还没有任何 Stage3 run。点击右上角“新建运行”后会立即启动 breaker agent。</div>
          </div>
        `;
      }
      const runNumberById = buildStage3RunNumberById(runs);
      const selectedRunNumber = selectedRun
        ? (runNumberById.get(String(selectedRun.id)) || 1)
        : 1;
      return `
        <div class="detail-card detail-section">
          <div class="section-actions">
            <h4>运行历史</h4>
          </div>
          <div class="stage2-history-strip-wrap">
            <div class="stage2-history-strip">
              ${runs.map((run) => renderStage3RunCard(run, runNumberById.get(String(run.id)) || 1)).join("")}
            </div>
          </div>
        </div>
        ${renderStage3Run(payload, selectedRun, selectedRunNumber)}
      `;
    }

    function bindStage3DetailHandlers(root) {
      root.querySelectorAll("[data-stage3-detail-section]").forEach((section) => {
        if (section.dataset.stage3DetailSectionBound === "1") {
          return;
        }
        section.dataset.stage3DetailSectionBound = "1";
        section.addEventListener("toggle", () => {
          state.stage3DetailSections[section.dataset.stage3DetailSection] = section.open;
        });
      });
      const entryFilterToggle = root.querySelector("#stage3-entry-filter-toggle");
      if (entryFilterToggle && entryFilterToggle.dataset.stage3Bound !== "1") {
        entryFilterToggle.dataset.stage3Bound = "1";
        entryFilterToggle.addEventListener("click", (event) => {
          event.stopPropagation();
          const popover = $("#stage3-entry-filter-popover");
          if (!popover) {
            return;
          }
          if (popover.hidden) {
            syncStage3EntryFilterForm();
            popover.hidden = false;
            updateStage3EntryFilterStatusToggle();
            return;
          }
          closeStage3EntryFilterPopover();
        });
      }
      const entryFilterStatusToggle = root.querySelector("#stage3-entry-filter-status-toggle");
      if (entryFilterStatusToggle && entryFilterStatusToggle.dataset.stage3Bound !== "1") {
        entryFilterStatusToggle.dataset.stage3Bound = "1";
        entryFilterStatusToggle.addEventListener("click", (event) => {
          event.stopPropagation();
          const popover = $("#stage3-entry-filter-status-popover");
          if (!popover) {
            return;
          }
          popover.hidden = !popover.hidden;
          updateStage3EntryFilterStatusToggle(readSelectedStage3EntryFilterStatuses());
        });
      }
      root.querySelectorAll("[data-stage3-entry-filter-status]").forEach((input) => {
        if (input.dataset.stage3Bound === "1") {
          return;
        }
        input.dataset.stage3Bound = "1";
        input.addEventListener("change", () => {
          updateStage3EntryFilterStatusToggle(readSelectedStage3EntryFilterStatuses());
        });
      });
      const entryFilterApply = root.querySelector("#stage3-entry-filter-apply");
      if (entryFilterApply && entryFilterApply.dataset.stage3Bound !== "1") {
        entryFilterApply.dataset.stage3Bound = "1";
        entryFilterApply.addEventListener("click", () => {
          const payload = currentStage3RepositoryDetailPayload();
          state.stage3EntryList.filters = readStage3EntryFilterForm();
          persistStage3EntryListState(payload.repository?.id, payload.selected_snapshot?.id);
          closeStage3EntryFilterPopover();
          renderStage3RepositoryDetail(currentStage3RepositoryDetailPayload());
        });
      }
      const entryFilterReset = root.querySelector("#stage3-entry-filter-reset");
      if (entryFilterReset && entryFilterReset.dataset.stage3Bound !== "1") {
        entryFilterReset.dataset.stage3Bound = "1";
        entryFilterReset.addEventListener("click", () => {
          const payload = currentStage3RepositoryDetailPayload();
          state.stage3EntryList.filters = defaultStage3EntryFilters();
          persistStage3EntryListState(payload.repository?.id, payload.selected_snapshot?.id);
          closeStage3EntryFilterPopover();
          renderStage3RepositoryDetail(currentStage3RepositoryDetailPayload());
        });
      }
      const entryFilterNonPending = root.querySelector("#stage3-entry-filter-non-pending");
      if (entryFilterNonPending && entryFilterNonPending.dataset.stage3Bound !== "1") {
        entryFilterNonPending.dataset.stage3Bound = "1";
        entryFilterNonPending.addEventListener("click", () => {
          const payload = currentStage3RepositoryDetailPayload();
          state.stage3EntryList.filters = {
            ...state.stage3EntryList.filters,
            statuses: isStage3EntryNonPendingFilterActive() ? [] : [...STAGE3_NON_PENDING_STATUSES],
          };
          persistStage3EntryListState(payload.repository?.id, payload.selected_snapshot?.id);
          renderStage3RepositoryDetail(currentStage3RepositoryDetailPayload());
        });
      }
      root.querySelectorAll("[data-stage3-entry-sort]").forEach((button) => {
        if (button.dataset.stage3Bound === "1") {
          return;
        }
        button.dataset.stage3Bound = "1";
        button.addEventListener("click", () => {
          const payload = currentStage3RepositoryDetailPayload();
          const sortField = String(button.dataset.stage3EntrySort || "");
          if (!sortField) {
            return;
          }
          if (state.stage3EntryList.sortField === sortField) {
            state.stage3EntryList.sortOrder = state.stage3EntryList.sortOrder === "asc" ? "desc" : "asc";
          } else {
            state.stage3EntryList.sortField = sortField;
            state.stage3EntryList.sortOrder = stage3EntrySortDefaultOrder(sortField);
          }
          persistStage3EntryListState(payload.repository?.id, payload.selected_snapshot?.id);
          renderStage3RepositoryDetail(currentStage3RepositoryDetailPayload());
        });
      });
      root.querySelectorAll("[data-stage3-snapshot-id]").forEach((card) => {
        if (card.dataset.stage3Bound === "1") {
          return;
        }
        card.dataset.stage3Bound = "1";
        card.addEventListener("click", async () => {
          await selectStage3Snapshot(card.dataset.stage3SnapshotId);
        });
        card.addEventListener("keydown", async (event) => {
          if (event.key !== "Enter" && event.key !== " ") {
            return;
          }
          event.preventDefault();
          await selectStage3Snapshot(card.dataset.stage3SnapshotId);
        });
      });
      root.querySelectorAll("[data-stage3-entry-file-id]").forEach((row) => {
        if (row.dataset.stage3Bound === "1") {
          return;
        }
        row.dataset.stage3Bound = "1";
        row.addEventListener("click", async (event) => {
          if (event.target.closest("[data-stage3-entry-run-id]")) {
            return;
          }
          await selectStage3EntryFile(row.dataset.stage3EntryFileId);
        });
      });
      root.querySelectorAll("[data-stage3-entry-run-id]").forEach((button) => {
        if (button.dataset.stage3Bound === "1") {
          return;
        }
        button.dataset.stage3Bound = "1";
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          await runStage3EntryFile(
            button.dataset.stage3EntryRunId,
            button.dataset.stage3EntryExistingRunId || "",
            button.dataset.stage3EntryActionMode || "",
          );
        });
      });
      root.querySelectorAll("[data-stage3-run-id]").forEach((card) => {
        if (card.dataset.stage3Bound === "1") {
          return;
        }
        card.dataset.stage3Bound = "1";
        card.addEventListener("click", async (event) => {
          if (
            event.target.closest("[data-stage3-run-delete-id]")
            || event.target.closest("[data-stage3-run-interrupt-id]")
            || event.target.closest("[data-stage3-run-start-id]")
            || event.target.closest("[data-stage3-run-resume-id]")
            || event.target.closest("[data-stage3-run-rerun-id]")
          ) {
            return;
          }
          await selectStage3Run(card.dataset.stage3RunId);
        });
        card.addEventListener("keydown", async (event) => {
          if (
            event.target.closest("[data-stage3-run-delete-id]")
            || event.target.closest("[data-stage3-run-interrupt-id]")
            || event.target.closest("[data-stage3-run-start-id]")
            || event.target.closest("[data-stage3-run-resume-id]")
            || event.target.closest("[data-stage3-run-rerun-id]")
          ) {
            return;
          }
          if (event.key !== "Enter" && event.key !== " ") {
            return;
          }
          event.preventDefault();
          await selectStage3Run(card.dataset.stage3RunId);
        });
      });
      root.querySelectorAll("[data-stage3-create-run-id]").forEach((button) => {
        if (button.dataset.stage3Bound === "1") {
          return;
        }
        button.dataset.stage3Bound = "1";
        button.addEventListener("click", async () => {
          await runStage3EntryFile(button.dataset.stage3CreateRunId);
        });
      });
      root.querySelectorAll("[data-stage3-image-prewarm-download-target]").forEach((button) => {
        if (button.dataset.stage3Bound === "1") {
          return;
        }
        button.dataset.stage3Bound = "1";
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          await prewarmStage3RepositoryImages(state.selectedStage3RepositoryId);
        });
      });
      root.querySelectorAll("[data-stage3-image-prewarm-cancel-target]").forEach((button) => {
        if (button.dataset.stage3Bound === "1") {
          return;
        }
        button.dataset.stage3Bound = "1";
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          await cancelStage3RepositoryImagePrewarm(state.selectedStage3RepositoryId);
        });
      });
      root.querySelectorAll("[data-stage3-run-delete-id]").forEach((button) => {
        if (button.dataset.stage3Bound === "1") {
          return;
        }
        button.dataset.stage3Bound = "1";
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          await deleteStage3Run(button.dataset.stage3RunDeleteId);
        });
      });
      root.querySelectorAll("[data-stage3-run-interrupt-id]").forEach((button) => {
        if (button.dataset.stage3Bound === "1") {
          return;
        }
        button.dataset.stage3Bound = "1";
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          await interruptStage3Run(button.dataset.stage3RunInterruptId);
        });
      });
      root.querySelectorAll("[data-stage3-run-start-id]").forEach((button) => {
        if (button.dataset.stage3Bound === "1") {
          return;
        }
        button.dataset.stage3Bound = "1";
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          await startStage3Run(button.dataset.stage3RunStartId);
        });
      });
      root.querySelectorAll("[data-stage3-run-resume-id]").forEach((button) => {
        if (button.dataset.stage3Bound === "1") {
          return;
        }
        button.dataset.stage3Bound = "1";
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          await resumeStage3Run(button.dataset.stage3RunResumeId);
        });
      });
      root.querySelectorAll("[data-stage3-run-rerun-id]").forEach((button) => {
        if (button.dataset.stage3Bound === "1") {
          return;
        }
        button.dataset.stage3Bound = "1";
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          await rerunStage3Run(button.dataset.stage3RunRerunId);
        });
      });
      root.querySelectorAll("[data-stage3-asset-key]").forEach((button) => {
        if (button.dataset.stage3AssetBound === "1") {
          return;
        }
        button.dataset.stage3AssetBound = "1";
        button.addEventListener("click", () => {
          state.selectedStage3AssetKey = button.dataset.stage3AssetKey || null;
          rerenderStage3DetailPreservingScroll();
        });
      });
      root.querySelectorAll("[data-stage3-completion-file-path]").forEach((button) => {
        if (button.dataset.stage3CompletionFileBound === "1") {
          return;
        }
        button.dataset.stage3CompletionFileBound = "1";
        button.addEventListener("click", () => {
          if (!state.selectedStage3AssetKey) {
            return;
          }
          state.selectedStage3CompletionFileByAssetKey[state.selectedStage3AssetKey] = button.dataset.stage3CompletionFilePath;
          rerenderStage3DetailPreservingScroll();
        });
      });
      root.querySelectorAll("[data-stage3-asset-version]").forEach((button) => {
        if (button.dataset.stage3AssetVersionBound === "1") {
          return;
        }
        button.dataset.stage3AssetVersionBound = "1";
        button.addEventListener("click", () => {
          if (state.selectedStage3AssetKey) {
            state.selectedStage3AssetVersionByKey[state.selectedStage3AssetKey] = button.dataset.stage3AssetVersion;
            rerenderStage3DetailPreservingScroll();
          }
        });
      });
      root.querySelectorAll("[data-stage3-asset-copy]").forEach((button) => {
        if (button.dataset.stage3AssetCopyBound === "1") {
          return;
        }
        button.dataset.stage3AssetCopyBound = "1";
        button.addEventListener("click", async () => {
          await copySelectedStage3Asset(button);
        });
      });
      root.querySelectorAll("[data-stage4-create-from-stage3-savepoint-id]").forEach((button) => {
        if (button.dataset.stage4CreateBound === "1") {
          return;
        }
        button.dataset.stage4CreateBound = "1";
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          const payload = await createStage4Run(button.dataset.stage4CreateFromStage3SavepointId);
          if (payload?.id) {
            switchTab("stage4");
          }
        });
      });
      root.querySelectorAll("[data-stage3-run-runtime-edit-id]").forEach((button) => {
        if (button.dataset.stage3RunRuntimeEditBound === "1") {
          return;
        }
        button.dataset.stage3RunRuntimeEditBound = "1";
        button.addEventListener("click", (event) => {
          event.stopPropagation();
          const run = currentStage3RepositoryDetailPayload().selected_run;
          if (!run || String(run.id) !== String(button.dataset.stage3RunRuntimeEditId)) {
            return;
          }
          startStage3RunRuntimeEdit(run);
        });
      });
      root.querySelectorAll("[data-stage3-run-runtime-cancel-id]").forEach((button) => {
        if (button.dataset.stage3RunRuntimeCancelBound === "1") {
          return;
        }
        button.dataset.stage3RunRuntimeCancelBound = "1";
        button.addEventListener("click", (event) => {
          event.stopPropagation();
          cancelStage3RunRuntimeEdit(button.dataset.stage3RunRuntimeCancelId);
        });
      });
      root.querySelectorAll("[data-stage3-run-runtime-save-id]").forEach((button) => {
        if (button.dataset.stage3RunRuntimeSaveBound === "1") {
          return;
        }
        button.dataset.stage3RunRuntimeSaveBound = "1";
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          await saveStage3RunRuntimeConfig(
            state.selectedStage3RepositoryId,
            button.dataset.stage3RunRuntimeSaveId,
          );
        });
      });
      root.querySelectorAll("[data-stage3-run-runtime-field]").forEach((input) => {
        if (input.dataset.stage3RunRuntimeFieldBound === "1") {
          return;
        }
        input.dataset.stage3RunRuntimeFieldBound = "1";
        const syncValue = () => {
          const field = input.dataset.stage3RunRuntimeField;
          if (!field) {
            return;
          }
          let value = input.value;
          if (input.type === "number") {
            value = input.value === "" ? null : Number(input.value);
          }
          updateStage3RunRuntimeEditorDraft(field, value);
        };
        input.addEventListener("input", syncValue);
        input.addEventListener("change", syncValue);
      });
      ensureSelectedStage3CompletionFileLoaded();
    }

    function renderStage3EntryFileDetail(payload) {
      const repository = payload.repository;
      const entryFile = payload.selected_entry_file;
      if (!repository) {
        hideStage3Detail();
        return;
      }
      if (!entryFile) {
        state.stage3List.entryDetailVisible = false;
        renderStage3RepositoryDetail(payload);
        return;
      }
      setStage3DetailVisible(true);
      const entryFileSubtitle = [
        repository.full_name,
        entryFile.test_file_path || "-",
        `原始测试点 ${String(entryFile.baseline_total_tests || 0)}`,
        `原始通过率 ${formatPercent(entryFile.baseline_pass_rate)}`,
        `最近操作 ${formatDate(entryFile.latest_operation_at || null)}`,
        `数据条数 ${String(entryFile.savepoint_count || 0)}`,
      ].join(" · ");
      const primaryAction = stage3EntryActionDescriptor(entryFile);
      setStage3DetailHeader({
        title: "入口文件详细",
        backLabel: "返回 Repo",
        subtitle: entryFileSubtitle,
        primaryActionLabel: primaryAction.visible ? primaryAction.label : "",
        primaryActionKind: primaryAction.visible ? "create-stage3-run" : "",
        primaryActionId: String(entryFile.id),
        primaryActionExistingRunId: primaryAction.existingRunId,
        primaryActionMode: primaryAction.mode,
        primaryActionDisabled: primaryAction.disabled,
      });
      $("#stage3-detail-body").innerHTML = `
        <div class="stage2-run-stack">
          ${renderStage3SelectedRunSection(payload)}
        </div>
      `;
      bindStage3DetailHandlers($("#stage3-detail-body"));
      applyPendingStage3RunOpenDefaults();
    }

    function renderStage3RepositoryImagePrewarmTarget({
      title,
      imageRef,
      status,
      target,
    }) {
      const inProgress = Boolean(status?.in_progress)
        || ["queued", "running"].includes(String(status?.last_job_status || ""));
      const alreadyReady = Boolean(status?.present) && !Boolean(status?.needs_update);
      const cancelInFlight = Boolean(state.stage3RepoImagePrewarmCancelInFlight[String(state.selectedStage3RepositoryId || "")]);
      const actionLabel = inProgress ? "取消" : (alreadyReady ? "已下载" : "下载");
      const actionEnabled = inProgress ? !cancelInFlight : (!inProgress && !alreadyReady);
      const actionAttrs = inProgress
        ? `data-stage3-image-prewarm-cancel-target="${escapeHtml(target)}"`
        : `data-stage3-image-prewarm-download-target="${escapeHtml(target)}"`;
      const actionClass = inProgress ? "danger tiny" : "secondary tiny";
      const metaItems = [
        { label: "当前状态", value: stage3ImagePrewarmStatusLabel(status) },
        { label: "开始于", value: status?.last_job_started_at ? formatDate(status.last_job_started_at) : "-" },
        { label: "结束于", value: status?.last_job_finished_at ? formatDate(status.last_job_finished_at) : "-" },
        { label: "镜像创建于", value: status?.created_at ? formatDate(status.created_at) : "-" },
      ];
      const logText = String(status?.job_log_text || status?.job_log_tail || "").trim();
      const logDisplay = logText || "暂无输出";
      const errorText = String(status?.last_job_error || "").trim();
      return `
        <section class="asset-image-detail-block">
          <div class="asset-image-detail-head">
            <div class="asset-image-detail-head-main">
              <h4>${escapeHtml(title)}</h4>
              <code>${escapeHtml(imageRef || "-")}</code>
            </div>
            <div class="asset-image-detail-actions">
              <button
                class="${actionClass}"
                type="button"
                ${actionAttrs}
                ${actionEnabled ? "" : "disabled"}
              >${escapeHtml(actionLabel)}</button>
              ${stage3ImagePrewarmStatusPill(status)}
            </div>
          </div>
          <div class="asset-image-detail-meta">
            ${metaItems.map((item) => `
              <div class="asset-image-detail-meta-item">
                <span>${escapeHtml(item.label)}</span>
                <strong>${escapeHtml(item.value)}</strong>
              </div>
            `).join("")}
          </div>
          ${errorText ? `<div class="asset-image-detail-error">${escapeHtml(errorText)}</div>` : ""}
          <div class="asset-image-detail-log-shell">
            <div class="asset-image-detail-log-title">实时输出</div>
            <pre class="asset-image-detail-log${logText ? "" : " empty"}" data-stage3-image-prewarm-log-target="${escapeHtml(target)}">${escapeHtml(logDisplay)}</pre>
          </div>
        </section>
      `;
    }

    function renderStage3RepositoryImagePrewarmPanel() {
      const payload = state.stage3RepositoryImagePrewarm;
      if (!payload) {
        return `<div class="stage2-empty">${
          state.stage3RepositoryImagePrewarmLoading ? "正在加载镜像预热状态..." : "暂无镜像预热状态。"
        }</div>`;
      }
      captureStage3ImagePrewarmLogScrollState();
      const body = `
        <div class="asset-image-detail-grid">
          ${renderStage3RepositoryImagePrewarmTarget({
            title: "Stage2 Dockerfile 运行时镜像",
            imageRef: payload.runtime_image?.image_ref || "-",
            status: payload.runtime_image || {},
            target: "runtime",
          })}
          ${renderStage3RepositoryImagePrewarmTarget({
            title: "OpenHands 包装镜像",
            imageRef: payload.agent_server_image?.image_ref || "-",
            status: payload.agent_server_image || {},
            target: "agent",
          })}
        </div>
      `;
      queueMicrotask(() => restoreStage3ImagePrewarmLogScrollState());
      return body;
    }

    function renderStage3RepositoryDetail(payload) {
      const repository = payload.repository;
      if (!repository) {
        hideStage3Detail();
        return;
      }
      const snapshots = Array.isArray(payload.commit_snapshots) ? payload.commit_snapshots : [];
      const selectedSnapshot = payload.selected_snapshot;
      const selectedEntryFile = payload.selected_entry_file;
      restoreStage3EntryListState(repository.id, selectedSnapshot?.id);
      const visibleEntryRows = visibleStage3EntryFiles(payload.entry_files);
      const entryFilterCount = getActiveStage3EntryFilterCount();
      const entryFilterButtonLabel = entryFilterCount > 0 ? `筛选（${entryFilterCount}）` : "筛选";
      const entryNonPendingActive = isStage3EntryNonPendingFilterActive();
      if (!state.selectedStage3SnapshotId && selectedSnapshot?.id) {
        state.selectedStage3SnapshotId = selectedSnapshot.id;
      }
      if (!state.selectedStage3EntryFileId && selectedEntryFile?.id) {
        state.selectedStage3EntryFileId = selectedEntryFile.id;
      }
      setStage3DetailVisible(true);
      setStage3DetailHeader({
        title: "Repo 详细",
        backLabel: "返回列表",
        subtitle: `${repository.full_name}`,
      });
      $("#stage3-detail-body").innerHTML = `
        <div class="detail-card detail-section">
          <h4>COMMIT</h4>
          ${snapshots.length > 0 ? `
            <div class="stage3-commit-strip">
              ${snapshots.map((snapshot) => renderStage3SnapshotCard(snapshot)).join("")}
            </div>
          ` : `<div class="stage2-empty">当前还没有 Stage3 commit 基线。</div>`}
        </div>

        <div class="detail-card detail-section">
          <div class="section-actions">
            <h4>入口文件列表</h4>
            <div class="button-row">
              <button
                class="secondary tiny filter-toggle${entryNonPendingActive ? " active" : ""}"
                id="stage3-entry-filter-non-pending"
                type="button"
                aria-pressed="${entryNonPendingActive ? "true" : "false"}"
              >只看非未运行</button>
              <div class="popover-anchor" id="stage3-entry-filter-anchor">
                <button
                  class="secondary tiny filter-toggle${entryFilterCount > 0 ? " active" : ""}"
                  id="stage3-entry-filter-toggle"
                  type="button"
                  aria-expanded="false"
                >${escapeHtml(entryFilterButtonLabel)}</button>
                <div class="repo-filter-popover" id="stage3-entry-filter-popover" hidden>
                  <div class="repo-filter-title">入口文件筛选条件</div>
                  <div class="field-grid">
                    <div class="field-group full">
                      <div class="group-title">状态</div>
                      <div class="selector-anchor" id="stage3-entry-filter-status-anchor">
                        <button class="selector-toggle" id="stage3-entry-filter-status-toggle" type="button" aria-expanded="false">
                          <span class="selector-summary" id="stage3-entry-filter-status-summary">不限</span>
                          <span class="selector-caret">▾</span>
                        </button>
                        <div class="selector-popover" id="stage3-entry-filter-status-popover" hidden>
                          <div class="selector-list compact-grid">
                            <label class="selector-option"><input type="checkbox" data-stage3-entry-filter-status value="pending"><span>未运行</span></label>
                            <label class="selector-option"><input type="checkbox" data-stage3-entry-filter-status value="queued"><span>排队中</span></label>
                            <label class="selector-option"><input type="checkbox" data-stage3-entry-filter-status value="running"><span>运行中</span></label>
                            <label class="selector-option"><input type="checkbox" data-stage3-entry-filter-status value="succeeded"><span>成功</span></label>
                            <label class="selector-option"><input type="checkbox" data-stage3-entry-filter-status value="failed"><span>失败</span></label>
                            <label class="selector-option"><input type="checkbox" data-stage3-entry-filter-status value="interrupted"><span>已中断</span></label>
                          </div>
                        </div>
                      </div>
                    </div>
                    <label>
                      原始测试点下界
                      <input id="stage3-entry-filter-original-tests-min" type="number" min="0" placeholder="可选">
                    </label>
                    <label>
                      原始测试点上界
                      <input id="stage3-entry-filter-original-tests-max" type="number" min="0" placeholder="可选">
                    </label>
                    <label>
                      原始通过率下界（%）
                      <input id="stage3-entry-filter-original-pass-rate-min" type="number" min="0" max="100" step="0.1" placeholder="可选">
                    </label>
                    <label>
                      原始通过率上界（%）
                      <input id="stage3-entry-filter-original-pass-rate-max" type="number" min="0" max="100" step="0.1" placeholder="可选">
                    </label>
                    <label>
                      数据条数下界
                      <input id="stage3-entry-filter-data-count-min" type="number" min="0" placeholder="可选">
                    </label>
                    <label>
                      数据条数上界
                      <input id="stage3-entry-filter-data-count-max" type="number" min="0" placeholder="可选">
                    </label>
                  </div>
                  <div class="repo-filter-actions">
                    <button class="secondary tiny" id="stage3-entry-filter-reset" type="button">清空</button>
                    <button class="primary tiny" id="stage3-entry-filter-apply" type="button">应用筛选</button>
                  </div>
                </div>
              </div>
            </div>
          </div>
          ${visibleEntryRows.length > 0
            ? renderStage3EntryFiles(visibleEntryRows)
            : `<div class="stage2-empty">当前筛选条件下没有入口文件。</div>`}
        </div>

        <div class="detail-card detail-section stage3-image-prewarm-section">
          <div class="section-actions">
            <h4>镜像预热</h4>
          </div>
          ${renderStage3RepositoryImagePrewarmPanel()}
        </div>
      `;
      bindStage3DetailHandlers($("#stage3-detail-body"));
    }

    function renderStage3RepositoryDetailAfterBackgroundStateChange() {
      if (isStage3EntryFilterInteractionActive()) {
        pendingStage3RepositoryDetailRerender = true;
        return;
      }
      pendingStage3RepositoryDetailRerender = false;
      renderStage3RepositoryDetail(currentStage3RepositoryDetailPayload());
    }

    async function loadStage3Repos() {
      const params = buildStage3ListParams();
      if (state.activeTab === "stage3") {
        params.set("include_prewarm", "1");
      }
      const payload = await api(`/api/stage3/repos?${params.toString()}`);
      const repositories = preserveStage3RepositoryPrewarmSummaries(payload.repositories);
      state.stage3Repos = repositories;
      state.stage3List.total = payload.pagination.total;
      state.stage3List.totalPages = payload.pagination.total_pages;
      $("#stage3-repos-body").innerHTML = repositories.map((repository) => `
        <tr
          class="${String(repository.id) === String(state.selectedStage3RepositoryId) ? "task-row active" : ""}"
          data-stage3-repo-id="${repository.id}"
        >
          <td class="stage3-repo-name-cell">
            <strong>${escapeHtml(repository.full_name)}</strong>
          </td>
          <td>${escapeHtml(repository.primary_language || "-")}</td>
          <td>${escapeHtml(String(repository.stargazers_count || 0))}</td>
          <td>${escapeHtml(String(repository.stage3?.eligible_commit_count || 0))}</td>
          <td>${escapeHtml(formatDate(repository.stage3?.latest_operation_at || null))}</td>
          <td>${renderStage3ProducedEntryProgress(repository)}</td>
          <td>${escapeHtml(String(repository.stage3?.produced_data_count || 0))}</td>
          <td>${renderStage3RepositoryStatus(repository)}</td>
          <td class="stage3-repo-action-cell">${renderStage3RepositoryAction(repository)}</td>
        </tr>
      `).join("") || `<tr><td colspan="9" class="muted">当前还没有进入 Stage3 的 repo。</td></tr>`;
      $("#stage3-pagination-meta").textContent = `第 ${payload.pagination.page} / ${payload.pagination.total_pages} 页，共 ${payload.pagination.total} 条`;
      $("#stage3-prev-btn").disabled = payload.pagination.page <= 1;
      $("#stage3-next-btn").disabled = payload.pagination.page >= payload.pagination.total_pages;
      updateStage3FilterToggle();
      document.querySelectorAll("[data-stage3-sort]").forEach((button) => {
        const active = button.dataset.stage3Sort === state.stage3List.sortField;
        button.classList.toggle("active", active);
        const arrow = active ? (state.stage3List.sortOrder === "asc" ? " ↑" : " ↓") : "";
        button.textContent = `${button.textContent.replace(/[ ↑↓]+$/, "")}${arrow}`;
      });
      $("#stage3-repos-body").querySelectorAll("tr[data-stage3-repo-id]").forEach((row) => {
        row.addEventListener("click", () => {
          selectStage3Repository(row.dataset.stage3RepoId);
        });
      });
      $("#stage3-repos-body").querySelectorAll("[data-stage3-repo-run-all-id]").forEach((button) => {
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          event.preventDefault();
          await runAllStage3RepositoryEntries(button.dataset.stage3RepoRunAllId);
        });
      });
      $("#stage3-repos-body").querySelectorAll("[data-stage3-repo-prewarm-id]").forEach((button) => {
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          event.preventDefault();
          await prewarmStage3RepositoryImages(button.dataset.stage3RepoPrewarmId);
        });
      });
      $("#stage3-repos-body").querySelectorAll("[data-stage3-repo-prewarm-cancel-id]").forEach((button) => {
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          event.preventDefault();
          await cancelStage3RepositoryImagePrewarm(button.dataset.stage3RepoPrewarmCancelId);
        });
      });
      return repositories;
    }

    async function loadStage3RepositoryImagePrewarm(repositoryId, { silent = false } = {}) {
      if (!repositoryId) {
        state.stage3RepositoryImagePrewarm = null;
        return null;
      }
      state.stage3RepositoryImagePrewarmLoading = true;
      try {
        const params = new URLSearchParams();
        if (state.selectedStage3SnapshotId) {
          params.set("selected_snapshot_id", state.selectedStage3SnapshotId);
        }
        const query = params.toString();
        const payload = await api(`/api/stage3/repos/${repositoryId}/image-prewarm${query ? `?${query}` : ""}`);
        syncStage3RepositoryPrewarmSummary(repositoryId, payload);
        if (String(state.selectedStage3RepositoryId) === String(repositoryId)) {
          state.stage3RepositoryImagePrewarm = payload;
          if (state.stage3List.detailVisible && !state.stage3List.entryDetailVisible) {
            renderStage3RepositoryDetailAfterBackgroundStateChange();
          }
        }
        return payload;
      } catch (error) {
        if (!silent && String(state.selectedStage3RepositoryId) === String(repositoryId)) {
          state.stage3RepositoryImagePrewarm = null;
          if (state.stage3List.detailVisible && !state.stage3List.entryDetailVisible) {
            renderStage3RepositoryDetailAfterBackgroundStateChange();
          }
        }
        throw error;
      } finally {
        state.stage3RepositoryImagePrewarmLoading = false;
      }
    }

    async function loadStage3RepositoryDetail(repositoryId) {
      const params = new URLSearchParams();
      if (state.selectedStage3SnapshotId) {
        params.set("selected_snapshot_id", state.selectedStage3SnapshotId);
      }
      if (state.selectedStage3EntryFileId) {
        params.set("selected_entry_file_id", state.selectedStage3EntryFileId);
      }
      if (state.selectedStage3RunId) {
        params.set("selected_run_id", state.selectedStage3RunId);
      }
      const query = params.toString();
      const payload = await api(`/api/stage3/repos/${repositoryId}${query ? `?${query}` : ""}`);
      if (String(state.selectedStage3RepositoryId) !== String(repositoryId)) {
        return payload;
      }
      state.stage3RepositoryImagePrewarmLoading = true;
      applyLiveStage3RepositoryDetailPayload(payload);
      syncStage3RepositoryDetailStream();
      loadStage3RepositoryImagePrewarm(repositoryId, { silent: true }).catch(() => {});
      return payload;
    }

    function syncStage3RepositoryDetailStream() {
      const selectedRepositoryId = state.selectedStage3RepositoryId;
      const selectedSnapshotId = state.selectedStage3SnapshotId ? String(state.selectedStage3SnapshotId) : null;
      const selectedEntryFileId = state.selectedStage3EntryFileId ? String(state.selectedStage3EntryFileId) : null;
      const selectedRunId = state.selectedStage3RunId ? String(state.selectedStage3RunId) : null;
      if (
        state.activeTab !== "stage3"
        || !selectedRepositoryId
        || !state.stage3List.detailVisible
        || typeof EventSource === "undefined"
      ) {
        closeStage3RepositoryDetailStream();
        return;
      }
      const selectedRepository = (
        state.stage3Repos.find((repo) => String(repo.id) === String(selectedRepositoryId))
        || currentStage3RepositoryDetailPayload().repository
      );
      if (!isStage3RepositoryRealtimeEligible(selectedRepository)) {
        closeStage3RepositoryDetailStream();
        return;
      }
      if (
        stage3RepositoryDetailEventSource
        && String(stage3RepositoryDetailStreamRepositoryId) === String(selectedRepositoryId)
        && String(stage3RepositoryDetailStreamSnapshotId || "") === String(selectedSnapshotId || "")
        && String(stage3RepositoryDetailStreamEntryFileId || "") === String(selectedEntryFileId || "")
        && String(stage3RepositoryDetailStreamRunId || "") === String(selectedRunId || "")
      ) {
        return;
      }
      closeStage3RepositoryDetailStream();
      const streamRepositoryId = String(selectedRepositoryId);
      const streamParams = new URLSearchParams();
      if (selectedSnapshotId) {
        streamParams.set("selected_snapshot_id", selectedSnapshotId);
      }
      if (selectedEntryFileId) {
        streamParams.set("selected_entry_file_id", selectedEntryFileId);
      }
      if (selectedRunId) {
        streamParams.set("selected_run_id", selectedRunId);
      }
      const streamQuery = streamParams.toString();
      const source = new EventSource(
        `/api/stage3/repos/${encodeURIComponent(streamRepositoryId)}/events${streamQuery ? `?${streamQuery}` : ""}`,
      );
      stage3RepositoryDetailEventSource = source;
      stage3RepositoryDetailStreamRepositoryId = streamRepositoryId;
      stage3RepositoryDetailStreamSnapshotId = selectedSnapshotId;
      stage3RepositoryDetailStreamEntryFileId = selectedEntryFileId;
      stage3RepositoryDetailStreamRunId = selectedRunId;

      const isCurrentStream = () => (
        stage3RepositoryDetailEventSource === source
        && String(stage3RepositoryDetailStreamRepositoryId) === streamRepositoryId
        && String(stage3RepositoryDetailStreamSnapshotId || "") === String(selectedSnapshotId || "")
        && String(stage3RepositoryDetailStreamEntryFileId || "") === String(selectedEntryFileId || "")
        && String(stage3RepositoryDetailStreamRunId || "") === String(selectedRunId || "")
      );
      const handlePayload = (payload, { terminal = false } = {}) => {
        if (!isCurrentStream()) {
          return;
        }
        mergeStage3RepositorySummary(payload.repository);
        applyLiveStage3RepositoryDetailPayload(payload);
        if (terminal) {
          closeStage3RepositoryDetailStream();
        }
      };
      source.addEventListener("snapshot", (event) => {
        handlePayload(JSON.parse(event.data));
      });
      source.addEventListener("terminal", (event) => {
        handlePayload(JSON.parse(event.data), { terminal: true });
      });
      source.addEventListener("deleted", () => {
        if (!isCurrentStream()) {
          return;
        }
        if (String(state.selectedStage3RepositoryId) === streamRepositoryId) {
          state.selectedStage3RepositoryId = null;
          hideStage3Detail();
        } else {
          closeStage3RepositoryDetailStream();
        }
      });
      source.onerror = () => {
        if (!isCurrentStream()) {
          return;
        }
        closeStage3RepositoryDetailStream();
      };
    }

    async function selectStage3Repository(repositoryId) {
      closeStage3RepositoryDetailStream();
      pendingStage3RepositoryDetailPayload = null;
      pendingStage3RepositoryDetailRerender = false;
      state.selectedStage3RepositoryId = repositoryId;
      state.selectedStage3SnapshotId = null;
      state.selectedStage3EntryFileId = null;
      state.selectedStage3RunId = null;
      state.pendingStage3RunOpenDefaults = false;
      state.stage3RepositoryImagePrewarm = null;
      state.stage3RepositoryImagePrewarmLogScrollState = {};
      resetStage3AssetBrowserState();
      state.stage3List.entryDetailVisible = false;
      setStage3RepositoryDetailPayload(null);
      setStage3DetailVisible(true);
      setStage3DetailHeader({
        title: "Repo 详细",
        backLabel: "返回列表",
        subtitle: "正在加载...",
      });
      $("#stage3-detail-body").innerHTML = `<div class="detail-card detail-section"><div class="stage2-empty">正在加载该 repo 的 Stage3 基线...</div></div>`;
      try {
        await Promise.all([
          loadStage3RepositoryDetail(repositoryId),
          loadStage3Repos(),
        ]);
      } catch (error) {
        setStage3DetailHeader({
          title: "Repo 详细",
          backLabel: "返回列表",
          subtitle: "加载失败",
        });
        $("#stage3-detail-body").innerHTML = `<div class="detail-card detail-section"><div class="stage2-empty">加载失败：${escapeHtml(error.message || String(error))}</div></div>`;
      }
    }

    async function selectStage3Snapshot(snapshotId) {
      if (!state.selectedStage3RepositoryId || String(state.selectedStage3SnapshotId) === String(snapshotId)) {
        return;
      }
      closeStage3RepositoryDetailStream();
      pendingStage3RepositoryDetailPayload = null;
      pendingStage3RepositoryDetailRerender = false;
      state.selectedStage3SnapshotId = snapshotId;
      state.selectedStage3EntryFileId = null;
      state.selectedStage3RunId = null;
      state.pendingStage3RunOpenDefaults = false;
      state.stage3RepositoryImagePrewarm = null;
      state.stage3RepositoryImagePrewarmLogScrollState = {};
      resetStage3AssetBrowserState();
      state.stage3List.entryDetailVisible = false;
      await loadStage3RepositoryDetail(state.selectedStage3RepositoryId);
    }

    async function selectStage3EntryFile(entryFileId) {
      if (!state.selectedStage3RepositoryId) {
        return;
      }
      const sameEntry = String(state.selectedStage3EntryFileId) === String(entryFileId);
      if (sameEntry && state.stage3List.entryDetailVisible) {
        return;
      }
      closeStage3RepositoryDetailStream();
      pendingStage3RepositoryDetailPayload = null;
      pendingStage3RepositoryDetailRerender = false;
      state.selectedStage3EntryFileId = entryFileId;
      state.selectedStage3RunId = null;
      state.pendingStage3RunOpenDefaults = false;
      resetStage3AssetBrowserState();
      state.stage3List.entryDetailVisible = true;
      setStage3DetailHeader({
        title: "入口文件详细",
        backLabel: "返回 Repo",
        subtitle: "正在加载...",
      });
      $("#stage3-detail-body").innerHTML = `<div class="detail-card detail-section"><div class="stage2-empty">正在加载该入口文件的 Stage3 run...</div></div>`;
      await loadStage3RepositoryDetail(state.selectedStage3RepositoryId);
    }

    async function selectStage3Run(runId) {
      if (!state.selectedStage3RepositoryId || String(state.selectedStage3RunId) === String(runId)) {
        return;
      }
      closeStage3RepositoryDetailStream();
      pendingStage3RepositoryDetailPayload = null;
      pendingStage3RepositoryDetailRerender = false;
      state.selectedStage3RunId = runId;
      state.pendingStage3RunOpenDefaults = true;
      resetStage3AssetBrowserState();
      state.stage3List.entryDetailVisible = true;
      await loadStage3RepositoryDetail(state.selectedStage3RepositoryId);
    }

    async function createStage3Run(entryFileId) {
      if (!state.selectedStage3RepositoryId || !entryFileId) {
        return null;
      }
      try {
        const payload = await api(
          `/api/stage3/repos/${encodeURIComponent(state.selectedStage3RepositoryId)}/entry-files/${encodeURIComponent(entryFileId)}/runs?auto_start=1`,
          { method: "POST" },
        );
        state.selectedStage3SnapshotId = payload.selected_snapshot?.id || state.selectedStage3SnapshotId;
        state.selectedStage3EntryFileId = payload.selected_entry_file?.id || entryFileId;
        state.selectedStage3RunId = payload.selected_run?.id || null;
        state.pendingStage3RunOpenDefaults = Boolean(payload.selected_run?.id);
        resetStage3AssetBrowserState();
        setStage3RepositoryDetailPayload(payload);
        renderCurrentStage3Detail(currentStage3RepositoryDetailPayload());
        await loadStage3Repos();
        syncStage3RepositoryDetailStream();
        setRefreshMeta();
        return payload;
      } catch (error) {
        alert(`创建 Stage3 run 失败: ${error.message}`);
        return null;
      }
    }

    async function runStage3EntryFile(entryFileId, existingRunId = "", mode = "") {
      closeStage3RepositoryDetailStream();
      state.selectedStage3EntryFileId = entryFileId;
      state.stage3List.entryDetailVisible = true;
      const normalizedExistingRunId = String(existingRunId || "").trim();
      const normalizedMode = String(mode || "").trim();
      if (normalizedExistingRunId) {
        state.selectedStage3RunId = normalizedExistingRunId;
        if (normalizedMode === "rerun-existing") {
          await rerunStage3Run(normalizedExistingRunId);
          return;
        }
        await startStage3Run(normalizedExistingRunId);
        return;
      }
      const payload = await createStage3Run(entryFileId);
      if (!payload?.selected_run?.id) {
        return;
      }
    }

    async function runAllStage3RepositoryEntries(repositoryId) {
      const normalizedRepositoryId = String(repositoryId || "").trim();
      if (!normalizedRepositoryId || isStage3RepositoryBulkRunInFlight(normalizedRepositoryId)) {
        return;
      }
      const selectedSnapshotId = (
        String(state.selectedStage3RepositoryId || "") === normalizedRepositoryId
        ? String(state.selectedStage3SnapshotId || "").trim()
        : ""
      );
      const confirmation = selectedSnapshotId
        ? "确认启动该 repo 当前选中 commit 下的全部入口文件吗？"
        : "确认启动该 repo 最新 commit 下的全部入口文件吗？";
      if (!window.confirm(confirmation)) {
        return;
      }

      setStage3RepositoryBulkRunInFlight(normalizedRepositoryId, true);
      try {
        await loadStage3Repos();
        const params = new URLSearchParams();
        if (selectedSnapshotId) {
          params.set("selected_snapshot_id", selectedSnapshotId);
        }
        const query = params.toString();
        const detailPayload = await api(
          `/api/stage3/repos/${encodeURIComponent(normalizedRepositoryId)}${query ? `?${query}` : ""}`
        );
        const snapshot = detailPayload.selected_snapshot || null;
        const entryFiles = Array.isArray(detailPayload.entry_files) ? detailPayload.entry_files : [];
        if (entryFiles.length === 0) {
          alert("当前 repo 没有可启动的入口文件。");
          return;
        }

        let createdCount = 0;
        let startedCount = 0;
        let skippedActiveCount = 0;
        let skippedPendingCount = 0;
        let failedCount = 0;
        const failedExamples = [];

        for (const entryFile of entryFiles) {
          const entryFileId = String(entryFile?.id || "").trim();
          const entryFilePath = String(entryFile?.test_file_path || "").trim() || "(unknown)";
          const entryStatus = String(entryFile?.status || "pending");
          const reusedPendingRun = entryStatus === "pending" && entryFile?.latest_run_id;
          if (!entryFileId) {
            failedCount += 1;
            failedExamples.push(`${entryFilePath}: missing entry file id`);
            continue;
          }
          if (entryStatus === "queued" || entryStatus === "running") {
            skippedActiveCount += 1;
            continue;
          }
          try {
            let runId = "";
            if (reusedPendingRun) {
              runId = String(entryFile.latest_run_id || "").trim();
            } else {
              const createPayload = await api(
                `/api/stage3/repos/${encodeURIComponent(normalizedRepositoryId)}/entry-files/${encodeURIComponent(entryFileId)}/runs?auto_start=1`,
                { method: "POST" },
              );
              runId = String(createPayload?.selected_run?.id || "").trim();
              if (runId) {
                createdCount += 1;
                startedCount += 1;
              }
            }
            if (!runId) {
              failedCount += 1;
              failedExamples.push(`${entryFilePath}: run id missing after create`);
              continue;
            }
            if (reusedPendingRun) {
              await api(
                `/api/stage3/repos/${encodeURIComponent(normalizedRepositoryId)}/runs/${encodeURIComponent(runId)}/start`,
                { method: "POST" },
              );
              startedCount += 1;
            }
          } catch (error) {
            failedCount += 1;
            failedExamples.push(`${entryFilePath}: ${error.message}`);
          }
        }

        if (String(state.selectedStage3RepositoryId || "") === normalizedRepositoryId) {
          await loadStage3RepositoryDetail(normalizedRepositoryId);
        }
        await loadStage3Repos();
        setRefreshMeta();

        const summaryParts = [];
        if (startedCount > 0) {
          summaryParts.push(`已启动 ${startedCount} 个入口文件`);
        }
        if (createdCount > 0) {
          summaryParts.push(`其中新建 ${createdCount} 个 run`);
        }
        if (skippedActiveCount > 0) {
          summaryParts.push(`跳过 ${skippedActiveCount} 个已在排队/运行中的入口文件`);
        }
        if (failedCount > 0) {
          summaryParts.push(`失败 ${failedCount} 个入口文件`);
        }
        const snapshotLabel = snapshot?.source_commit_sha
          ? `commit ${String(snapshot.source_commit_sha).slice(0, 7)}`
          : "当前快照";
        const failureDetail = failedExamples.length > 0
          ? `\n\n失败样例：\n${failedExamples.slice(0, 3).join("\n")}`
          : "";
        alert(`${snapshotLabel} 批量启动完成：${summaryParts.join("，") || "没有可执行变更"}${failureDetail}`);
      } catch (error) {
        alert(`批量启动 Stage3 入口文件失败: ${error.message}`);
      } finally {
        setStage3RepositoryBulkRunInFlight(normalizedRepositoryId, false);
        try {
          await loadStage3Repos();
        } catch (error) {
          console.error("Failed to refresh Stage3 repo list after bulk run", error);
        }
      }
    }

    async function prewarmStage3RepositoryImages(repositoryId) {
      const normalizedRepositoryId = String(repositoryId || "").trim();
      if (!normalizedRepositoryId || isStage3RepositoryImagePrewarmInFlight(normalizedRepositoryId)) {
        return;
      }
      const selectedSnapshotId = (
        String(state.selectedStage3RepositoryId || "") === normalizedRepositoryId
        ? String(state.selectedStage3SnapshotId || "").trim()
        : ""
      );
      setStage3RepositoryImagePrewarmInFlight(normalizedRepositoryId, true);
      try {
        const payload = await api(
          `/api/stage3/repos/${encodeURIComponent(normalizedRepositoryId)}/image-prewarm`,
          {
            method: "POST",
            body: JSON.stringify({
              selected_snapshot_id: selectedSnapshotId || null,
              force: false,
            }),
          },
        );
        if (String(state.selectedStage3RepositoryId || "") === normalizedRepositoryId) {
          state.stage3RepositoryImagePrewarm = payload.status || state.stage3RepositoryImagePrewarm;
          state.selectedStage3SnapshotId = payload.snapshot_id || state.selectedStage3SnapshotId;
          if (state.stage3List.detailVisible && !state.stage3List.entryDetailVisible) {
            renderStage3RepositoryDetailAfterBackgroundStateChange();
          }
        }
        setRefreshMeta();
        if (payload.started) {
          alert("镜像预热后台任务已提交。");
        } else {
          alert("当前快照的镜像预热任务已在后台执行。");
        }
      } catch (error) {
        alert(`提交 Stage3 镜像预热失败: ${error.message}`);
      } finally {
        setStage3RepositoryImagePrewarmInFlight(normalizedRepositoryId, false);
        try {
          await loadStage3Repos();
          if (String(state.selectedStage3RepositoryId || "") === normalizedRepositoryId) {
            await loadStage3RepositoryImagePrewarm(normalizedRepositoryId, { silent: true });
          }
        } catch (error) {
          console.error("Failed to refresh Stage3 repo image prewarm state", error);
        }
      }
    }

    async function cancelStage3RepositoryImagePrewarm(repositoryId) {
      const normalizedRepositoryId = String(repositoryId || "").trim();
      if (!normalizedRepositoryId || state.stage3RepoImagePrewarmCancelInFlight[normalizedRepositoryId]) {
        return;
      }
      const selectedSnapshotId = (
        String(state.selectedStage3RepositoryId || "") === normalizedRepositoryId
        ? String(state.selectedStage3SnapshotId || "").trim()
        : ""
      );
      state.stage3RepoImagePrewarmCancelInFlight = {
        ...state.stage3RepoImagePrewarmCancelInFlight,
        [normalizedRepositoryId]: true,
      };
      if (state.stage3List.detailVisible && !state.stage3List.entryDetailVisible) {
        renderStage3RepositoryDetailAfterBackgroundStateChange();
      }
      try {
        const payload = await api(
          `/api/stage3/repos/${encodeURIComponent(normalizedRepositoryId)}/image-prewarm/cancel`,
          {
            method: "POST",
            body: JSON.stringify({
              selected_snapshot_id: selectedSnapshotId || null,
              force: false,
            }),
          },
        );
        if (String(state.selectedStage3RepositoryId || "") === normalizedRepositoryId) {
          state.stage3RepositoryImagePrewarm = payload.status || state.stage3RepositoryImagePrewarm;
          state.selectedStage3SnapshotId = payload.snapshot_id || state.selectedStage3SnapshotId;
          if (state.stage3List.detailVisible && !state.stage3List.entryDetailVisible) {
            renderStage3RepositoryDetailAfterBackgroundStateChange();
          }
        }
        setRefreshMeta();
        alert(payload.cancelled ? "镜像预热取消请求已发送。" : "当前没有可取消的镜像预热任务。");
      } catch (error) {
        alert(`取消 Stage3 镜像预热失败: ${error.message}`);
      } finally {
        state.stage3RepoImagePrewarmCancelInFlight = {
          ...state.stage3RepoImagePrewarmCancelInFlight,
          [normalizedRepositoryId]: false,
        };
        try {
          if (String(state.selectedStage3RepositoryId || "") === normalizedRepositoryId) {
            await loadStage3RepositoryImagePrewarm(normalizedRepositoryId, { silent: true });
          }
        } catch (error) {
          console.error("Failed to refresh Stage3 repo image prewarm state after cancel", error);
        }
      }
    }

    async function startStage3Run(runId) {
      if (!state.selectedStage3RepositoryId || !runId) {
        return;
      }
      try {
        const payload = await api(
          `/api/stage3/repos/${encodeURIComponent(state.selectedStage3RepositoryId)}/runs/${encodeURIComponent(runId)}/start`,
          { method: "POST" },
        );
        setStage3RepositoryDetailPayload(payload);
        state.selectedStage3SnapshotId = payload.selected_snapshot?.id || state.selectedStage3SnapshotId;
        state.selectedStage3EntryFileId = payload.selected_entry_file?.id || state.selectedStage3EntryFileId;
        state.selectedStage3RunId = payload.selected_run?.id || runId;
        resetStage3AssetBrowserState();
        renderCurrentStage3Detail(currentStage3RepositoryDetailPayload());
        await loadStage3Repos();
        syncStage3RepositoryDetailStream();
        setRefreshMeta();
      } catch (error) {
        alert(`启动 Stage3 run 失败: ${error.message}`);
      }
    }

    async function interruptStage3Run(runId) {
      if (!state.selectedStage3RepositoryId || !runId) {
        return;
      }
      if (!window.confirm("确认中断这次 Stage3 run 吗？")) {
        return;
      }
      try {
        const payload = await api(
          `/api/stage3/repos/${encodeURIComponent(state.selectedStage3RepositoryId)}/runs/${encodeURIComponent(runId)}/interrupt`,
          { method: "POST" },
        );
        setStage3RepositoryDetailPayload(payload);
        state.selectedStage3SnapshotId = payload.selected_snapshot?.id || state.selectedStage3SnapshotId;
        state.selectedStage3EntryFileId = payload.selected_entry_file?.id || state.selectedStage3EntryFileId;
        state.selectedStage3RunId = payload.selected_run?.id || runId;
        renderCurrentStage3Detail(currentStage3RepositoryDetailPayload());
        await loadStage3Repos();
        syncStage3RepositoryDetailStream();
        setRefreshMeta();
      } catch (error) {
        alert(`中断 Stage3 run 失败: ${error.message}`);
      }
    }

    async function resumeStage3Run(runId) {
      if (!state.selectedStage3RepositoryId || !runId) {
        return;
      }
      if (!window.confirm("确认从最近一次 savepoint 续跑吗？")) {
        return;
      }
      try {
        const payload = await api(
          `/api/stage3/repos/${encodeURIComponent(state.selectedStage3RepositoryId)}/runs/${encodeURIComponent(runId)}/resume`,
          { method: "POST" },
        );
        setStage3RepositoryDetailPayload(payload);
        state.selectedStage3SnapshotId = payload.selected_snapshot?.id || state.selectedStage3SnapshotId;
        state.selectedStage3EntryFileId = payload.selected_entry_file?.id || state.selectedStage3EntryFileId;
        state.selectedStage3RunId = payload.selected_run?.id || null;
        resetStage3AssetBrowserState();
        renderCurrentStage3Detail(currentStage3RepositoryDetailPayload());
        await loadStage3Repos();
        syncStage3RepositoryDetailStream();
        setRefreshMeta();
      } catch (error) {
        alert(`续跑 Stage3 run 失败: ${error.message}`);
      }
    }

    async function rerunStage3Run(runId) {
      if (!state.selectedStage3RepositoryId || !runId) {
        return;
      }
      if (!window.confirm("确认从冻结基线重新运行这条 Stage3 run 吗？")) {
        return;
      }
      try {
        const payload = await api(
          `/api/stage3/repos/${encodeURIComponent(state.selectedStage3RepositoryId)}/runs/${encodeURIComponent(runId)}/rerun`,
          { method: "POST" },
        );
        setStage3RepositoryDetailPayload(payload);
        state.selectedStage3SnapshotId = payload.selected_snapshot?.id || state.selectedStage3SnapshotId;
        state.selectedStage3EntryFileId = payload.selected_entry_file?.id || state.selectedStage3EntryFileId;
        state.selectedStage3RunId = payload.selected_run?.id || null;
        resetStage3AssetBrowserState();
        renderCurrentStage3Detail(currentStage3RepositoryDetailPayload());
        await loadStage3Repos();
        syncStage3RepositoryDetailStream();
        setRefreshMeta();
      } catch (error) {
        alert(`重跑 Stage3 run 失败: ${error.message}`);
      }
    }

    async function deleteStage3Run(runId) {
      if (!state.selectedStage3RepositoryId || !runId) {
        return;
      }
      if (!window.confirm("确认删除这次 Stage3 run 吗？")) {
        return;
      }
      try {
        const payload = await api(
          `/api/stage3/repos/${encodeURIComponent(state.selectedStage3RepositoryId)}/runs/${encodeURIComponent(runId)}`,
          { method: "DELETE" },
        );
        state.selectedStage3SnapshotId = payload.selected_snapshot?.id || state.selectedStage3SnapshotId;
        state.selectedStage3EntryFileId = payload.selected_entry_file?.id || state.selectedStage3EntryFileId;
        state.selectedStage3RunId = payload.selected_run?.id || null;
        resetStage3AssetBrowserState();
        setStage3RepositoryDetailPayload(payload);
        renderCurrentStage3Detail(currentStage3RepositoryDetailPayload());
        await loadStage3Repos();
        syncStage3RepositoryDetailStream();
        setRefreshMeta();
        if (payload.cleanup_warnings) {
          alert(`Stage3 运行存档已删除，但部分资产清理失败，将在后续启动时重试清理：\n${formatApiErrorPayload(payload.cleanup_warnings)}`);
        }
      } catch (error) {
        alert(`删除 Stage3 run 失败: ${error.message}`);
      }
    }

    function renderPanelLoadError(selector, colspan, error) {
      $(selector).innerHTML = `
        <tr>
          <td colspan="${colspan}" class="muted">加载失败：${escapeHtml(error.message || String(error))}</td>
        </tr>
      `;
    }

    function stage4StatusLabel(run) {
      const result = String(run?.result || "");
      const status = String(run?.status || "");
      if (status === "completed" && result === "generated") return "已生成";
      if (status === "completed" && result === "failed") return "失败";
      if (status === "completed" && result === "interrupted") return "已中断";
      const labels = {
        pending: "待运行",
        queued: "排队中",
        running: "运行中",
        completed: "已完成",
      };
      return labels[status] || status || "-";
    }

    function stage4StatusClass(run) {
      const result = String(run?.result || "");
      const status = String(run?.status || "");
      if (status === "completed" && result === "generated") return "completed";
      if (status === "completed" && result === "failed") return "failed";
      if (status === "completed" && result === "interrupted") return "failed";
      return status || "pending";
    }

    function stage4StatusPill(run) {
      return `<span class="status ${stage4StatusClass(run)}">${escapeHtml(stage4StatusLabel(run))}</span>`;
    }

    function stage4PhaseLabel(phase) {
      const labels = {
        created: "已创建",
        queued: "排队中",
        preparing_workspace: "准备工作区",
        issuer_running: "Issuer 运行中",
        issuer_agent: "Issuer 运行中",
        completed: "已完成",
        failed: "失败",
        interrupted: "已中断",
      };
      return labels[phase] || phase || "-";
    }

    function stage4TokenDisplayText(run) {
      const aggregated = run?.token_usage_by_model;
      if (aggregated && typeof aggregated === "object" && !Array.isArray(aggregated)) {
        const entries = Object.entries(aggregated)
          .map(([model, tokens]) => [String(model || "unknown"), Number(tokens || 0)])
          .filter(([, tokens]) => Number.isFinite(tokens) && tokens > 0);
        if (entries.length > 0) {
          return entries
            .sort((left, right) => {
              if (right[1] !== left[1]) {
                return right[1] - left[1];
              }
              return left[0].localeCompare(right[0]);
            })
            .map(([model, tokens]) => `${model}: ${Math.round(tokens)}`)
            .join("\\n");
        }
      }
      const summary = run?.summary && typeof run.summary === "object" ? run.summary : {};
      const tokenDetails = summary.token_usage && typeof summary.token_usage === "object" ? summary.token_usage : {};
      const model = String(summary.issuer_model || "").trim();
      const total = Number(summary.issuer_token_usage ?? tokenDetails.total_tokens ?? 0);
      if (model && total > 0) {
        return `${model}: ${total}`;
      }
      return model || (total > 0 ? String(total) : "-");
    }

    function stage4ProducedIssueCountText(run) {
      const issues = Array.isArray(run?.issue_variants) ? run.issue_variants.length : 0;
      return String(issues);
    }

    function stage4DiffStatsText(diffStats) {
      const stats = diffStats && typeof diffStats === "object" ? diffStats : {};
      const added = Number(stats.lines_added || 0);
      const deleted = Number(stats.lines_deleted || 0);
      const total = Number(stats.lines_changed || added + deleted);
      return `${total} (+${added} / -${deleted})`;
    }

    function stage4RuntimeSnapshot(run) {
      const snapshot = run?.runtime_snapshot || {};
      const stage4 = snapshot.stage4 || {};
      return stage4.runtime || snapshot.runtime || {};
    }

    function stage4SourceRowSummary(source) {
      const repository = source?.repository || {};
      const snapshot = source?.snapshot || {};
      const entryFile = source?.entry_file || {};
      const savepoint = source?.savepoint || {};
      return { repository, snapshot, entryFile, savepoint };
    }

    function stage4SourceGroupKey(source) {
      const { repository, snapshot } = stage4SourceRowSummary(source || {});
      const repoKey = repository.id ?? repository.full_name ?? "repo";
      const commitKey = snapshot.id ?? snapshot.source_commit_sha ?? "commit";
      return `${repoKey}::${commitKey}`;
    }

    function parseStage4SourceGroupKey(groupKey) {
      const [repositoryId, snapshotId] = String(groupKey || "").split("::");
      const parsedRepositoryId = Number(repositoryId);
      const normalizedSnapshotId = String(snapshotId || "").trim();
      if (!Number.isFinite(parsedRepositoryId) || !normalizedSnapshotId) {
        return null;
      }
      return {
        repositoryId: parsedRepositoryId,
        snapshotId: normalizedSnapshotId,
      };
    }

    function stage4RunStatusFilterKey(run) {
      if (!run || !run.id) return "none";
      const status = String(run.status || "");
      const result = String(run.result || "");
      if (status === "completed" && result === "generated") return "generated";
      if (status === "completed" && result === "failed") return "failed";
      if (status === "completed" && result === "interrupted") return "interrupted";
      return status || "none";
    }

    function stage4GroupStatus(group) {
      const explicitStatus = String(group?.status || "").trim();
      if (explicitStatus) {
        return explicitStatus;
      }
      const sources = Array.isArray(group?.sources) ? group.sources : [];
      if (!sources.length) {
        return "pending";
      }
      let queuedCount = 0;
      let runningCount = 0;
      let generatedCount = 0;
      let failedCount = 0;
      let interruptedCount = 0;

      sources.forEach((source) => {
        const key = stage4RunStatusFilterKey(stage4SourceRowSummary(source).savepoint.latest_stage4_run);
        if (key === "running") {
          runningCount += 1;
        } else if (key === "queued") {
          queuedCount += 1;
        } else if (key === "generated") {
          generatedCount += 1;
        } else if (key === "failed") {
          failedCount += 1;
        } else if (key === "interrupted") {
          interruptedCount += 1;
        }
      });

      if (runningCount > 0) return "running";
      if (queuedCount > 0) return "queued";
      if (generatedCount > 0) return "generated";
      if (failedCount > 0) return "failed";
      if (interruptedCount > 0) return "interrupted";
      if (generatedCount === 0 && failedCount === 0 && interruptedCount === 0) {
        return "pending";
      }
      return "failed";
    }

    function stage4SavepointStatusLabel(status) {
      const labels = {
        none: "无 run",
        pending: "待运行",
        queued: "排队中",
        running: "运行中",
        generated: "已生成",
        failed: "失败",
        interrupted: "已中断",
      };
      return labels[String(status || "")] || String(status || "-");
    }

    function stage4SavepointStatusClass(status) {
      const normalized = String(status || "");
      if (normalized === "generated") return "completed";
      if (normalized === "failed" || normalized === "interrupted") return "failed";
      if (normalized === "queued" || normalized === "running" || normalized === "pending") return normalized;
      return "pending";
    }

    function stage4SavepointStatusPill(status) {
      return `<span class="status ${stage4SavepointStatusClass(status)}">${escapeHtml(stage4SavepointStatusLabel(status))}</span>`;
    }

    function stage4SavepointStatusSortValue(status) {
      const order = {
        none: 0,
        pending: 1,
        queued: 2,
        running: 3,
        failed: 4,
        interrupted: 5,
        generated: 6,
      };
      return order[String(status || "none")] ?? 99;
    }

    function summarizeStage4SavepointStatuses(values) {
      if (!values || values.length === 0) {
        return "不限";
      }
      if (values.length <= 2) {
        return values.map((value) => stage4SavepointStatusLabel(value)).join("、");
      }
      return `已选 ${values.length} 个状态`;
    }

    function stage4GroupStatusLabel(status) {
      const labels = {
        pending: "未运行",
        queued: "排队中",
        running: "运行中",
        generated: "已生成",
        succeeded: "已生成",
        failed: "失败",
        interrupted: "已中断",
      };
      return labels[String(status || "pending")] || String(status || "pending");
    }

    function stage4GroupStatusClass(status) {
      const normalized = String(status || "pending");
      if (normalized === "generated" || normalized === "succeeded") return "succeeded";
      if (normalized === "failed" || normalized === "interrupted") return "failed";
      if (normalized === "running") return "running";
      if (normalized === "queued") return "queued";
      return "pending";
    }

    function stage4GroupStatusPill(group) {
      const status = stage4GroupStatus(group);
      return `<span class="status ${stage4GroupStatusClass(status)}">${escapeHtml(stage4GroupStatusLabel(status))}</span>`;
    }

    function stage4GroupStatusSortValue(group) {
      const order = {
        pending: 0,
        queued: 1,
        running: 2,
        failed: 3,
        interrupted: 4,
        generated: 5,
        succeeded: 6,
      };
      return order[stage4GroupStatus(group)] ?? -1;
    }

    function stage4LatestRunTimestamp(run) {
      if (!run || !run.id) return 0;
      return Date.parse(run.updated_at || run.finished_at || run.started_at || run.created_at || "") || 0;
    }

    function stage4SourceTimestamp(source) {
      const { savepoint } = stage4SourceRowSummary(source || {});
      return Date.parse(savepoint.updated_at || savepoint.created_at || "") || 0;
    }

    function stage4SavepointProducedCounts(source) {
      const { savepoint } = stage4SourceRowSummary(source || {});
      return {
        issueCount: Math.max(0, Number(savepoint.produced_issue_variant_count || 0)),
      };
    }

    function stage4SavepointNumericFilterValue(value) {
      if (value == null) {
        return null;
      }
      if (typeof value === "string" && value.trim() === "") {
        return null;
      }
      const number = Number(value);
      return Number.isFinite(number) ? number : null;
    }

    function stage4CompareValues(left, right) {
      const leftNumber = Number(left);
      const rightNumber = Number(right);
      const bothNumeric = Number.isFinite(leftNumber) && Number.isFinite(rightNumber);
      if (bothNumeric) {
        return leftNumber - rightNumber;
      }
      return String(left ?? "").localeCompare(String(right ?? ""), undefined, { numeric: true, sensitivity: "base" });
    }

    function stage4SourceGroups() {
      return Array.isArray(state.stage4SourceGroups) ? state.stage4SourceGroups : [];
    }

    function currentStage4SourceGroup() {
      const payload = state.stage4SourceDetailPayload;
      if (!payload || !payload.group) {
        return null;
      }
      if (String(payload.group.key || "") !== String(state.selectedStage4SourceGroupKey || "")) {
        return null;
      }
      return payload.group;
    }

    function isStage4GroupRunInFlight(groupKey) {
      return Boolean(state.stage4GroupRunInFlight[String(groupKey || "")]);
    }

    function setStage4GroupRunInFlight(groupKey, inFlight) {
      const key = String(groupKey || "").trim();
      if (!key) {
        return;
      }
      state.stage4GroupRunInFlight = {
        ...state.stage4GroupRunInFlight,
        [key]: Boolean(inFlight),
      };
    }

    function stage4SourceGroupMatchesFilters(group) {
      const filters = state.stage4List.sourceFilters || {};
      const query = String(filters.nameQuery || "").trim().toLowerCase();
      if (query) {
        const haystack = String(group.repository?.full_name || "").toLowerCase();
        if (!haystack.includes(query)) return false;
      }
      const languages = Array.isArray(filters.languages) ? filters.languages : [];
      if (languages.length > 0) {
        const groupLanguages = new Set(group.languageList || []);
        if (!languages.some((language) => groupLanguages.has(language))) return false;
      }
      const statuses = Array.isArray(filters.statuses) ? filters.statuses : [];
      if (statuses.length > 0 && !statuses.includes(stage4GroupStatus(group))) {
        return false;
      }
      return true;
    }

    function stage4SourceSortValue(group, field) {
      if (field === "repo") return group.repository?.full_name || "";
      if (field === "commit") return group.snapshot?.source_commit_sha || "";
      if (field === "language") return (group.languageList || []).join(", ");
      if (field === "entry_count") return group.entryCount || 0;
      if (field === "savepoint_count") return group.savepointCount || 0;
      if (field === "latest_operation_at") return group.latestOperationAt || 0;
      if (field === "status") return stage4GroupStatusSortValue(group);
      return "";
    }

    function sortedStage4SourceGroups() {
      return stage4SourceGroups();
    }

    function stage4SavepointMatchesFilters(source) {
      const filters = state.stage4List.savepointFilters || {};
      const { entryFile, savepoint } = stage4SourceRowSummary(source);
      const query = String(filters.entryQuery || "").trim().toLowerCase();
      if (query && !String(entryFile.test_file_path || "").toLowerCase().includes(query)) {
        return false;
      }
      const statuses = Array.isArray(filters.statuses) ? filters.statuses : [];
      if (statuses.length > 0 && !statuses.includes(stage4RunStatusFilterKey(savepoint.latest_stage4_run))) {
        return false;
      }
      const testCount = Number(entryFile.baseline_total_tests || 0);
      const entryPassRate = Number(savepoint.entry_pass_rate || 0) * 100;
      const diffLines = Number(savepoint.diff_stats?.lines_changed || 0);
      const testCountMin = stage4SavepointNumericFilterValue(filters.testCountMin);
      const testCountMax = stage4SavepointNumericFilterValue(filters.testCountMax);
      const entryPassRateMin = stage4SavepointNumericFilterValue(filters.entryPassRateMin);
      const entryPassRateMax = stage4SavepointNumericFilterValue(filters.entryPassRateMax);
      const diffLinesMin = stage4SavepointNumericFilterValue(filters.diffLinesMin);
      const diffLinesMax = stage4SavepointNumericFilterValue(filters.diffLinesMax);
      if (testCountMin != null && testCount < testCountMin) {
        return false;
      }
      if (testCountMax != null && testCount > testCountMax) {
        return false;
      }
      if (entryPassRateMin != null && entryPassRate < entryPassRateMin) {
        return false;
      }
      if (entryPassRateMax != null && entryPassRate > entryPassRateMax) {
        return false;
      }
      if (diffLinesMin != null && diffLines < diffLinesMin) {
        return false;
      }
      if (diffLinesMax != null && diffLines > diffLinesMax) {
        return false;
      }
      return true;
    }

    function stage4SavepointSortValue(source, field) {
      const { entryFile, savepoint } = stage4SourceRowSummary(source);
      const status = stage4RunStatusFilterKey(savepoint.latest_stage4_run);
      if (field === "entry_file") return entryFile.test_file_path || "";
      if (field === "test_count") return Number(entryFile.baseline_total_tests || 0);
      if (field === "depth") return Number(savepoint.depth || 0);
      if (field === "entry_pass_rate") return Number(savepoint.entry_pass_rate || 0);
      if (field === "p2p_count") return Number(savepoint.p2p_count || 0);
      if (field === "f2p_count") return Number(savepoint.f2p_count || 0);
      if (field === "diff_lines") return Number(savepoint.diff_stats?.lines_changed || 0);
      if (field === "stage4_run_count") return Number(savepoint.stage4_run_count || 0);
      if (field === "status") return stage4SavepointStatusSortValue(status);
      if (field === "latest_operation_at") {
        return Math.max(stage4SourceTimestamp(source), stage4LatestRunTimestamp(savepoint.latest_stage4_run));
      }
      if (field === "produced_variant_count") {
        const counts = stage4SavepointProducedCounts(source);
        return counts.issueCount;
      }
      return "";
    }

    function sortedStage4SavepointSources(group) {
      return Array.isArray(group?.sources) ? group.sources : [];
    }

    function renderStage4SavepointSortButton(label, field) {
      const active = state.stage4List.savepointSortField === field;
      const arrow = active ? (state.stage4List.savepointSortOrder === "asc" ? " ↑" : " ↓") : "";
      return `
        <button
          class="sort-button${active ? " active" : ""}"
          type="button"
          data-stage4-savepoint-sort="${escapeHtml(field)}"
        >${escapeHtml(label + arrow)}</button>
      `;
    }

    function syncStage4SourceSortButtons() {
      document.querySelectorAll("[data-stage4-source-sort]").forEach((button) => {
        const field = button.dataset.stage4SourceSort || "";
        const label = button.dataset.sortLabel || button.textContent.replace(/[↑↓]/g, "").trim();
        const active = state.stage4List.sourceSortField === field;
        button.classList.toggle("active", active);
        button.textContent = `${label}${active ? (state.stage4List.sourceSortOrder === "asc" ? " ↑" : " ↓") : ""}`;
      });
    }

    function stage4ActiveFilterCount(filters) {
      return Object.values(filters || {}).reduce((count, value) => {
        if (Array.isArray(value)) return count + (value.length > 0 ? 1 : 0);
        return count + (String(value || "").trim() ? 1 : 0);
      }, 0);
    }

    function isStage4NonPendingFilterActive() {
      const statuses = Array.isArray(state.stage4List.sourceFilters?.statuses)
        ? [...state.stage4List.sourceFilters.statuses].sort()
        : [];
      return JSON.stringify(statuses) === JSON.stringify([...STAGE4_NON_PENDING_STATUSES].sort());
    }

    function updateStage4FilterToggle() {
      const toggle = $("#stage4-filter-toggle");
      if (toggle) {
        const count = stage4ActiveFilterCount(state.stage4List.sourceFilters || {});
        toggle.classList.toggle("active", count > 0);
        toggle.textContent = count > 0 ? `筛选（${count}）` : "筛选";
      }
      const nonPendingToggle = $("#stage4-filter-non-pending");
      if (nonPendingToggle) {
        const active = isStage4NonPendingFilterActive();
        nonPendingToggle.classList.toggle("active", active);
        nonPendingToggle.setAttribute("aria-pressed", String(active));
      }
    }

    function syncStage4FilterLanguageInputs() {
      const selected = new Set(state.stage4List.sourceFilters.languages || []);
      document.querySelectorAll("[data-stage4-filter-language]").forEach((input) => {
        input.checked = selected.has(input.value);
      });
    }

    function syncStage4FilterStatusInputs() {
      const selected = new Set(state.stage4List.sourceFilters.statuses || []);
      document.querySelectorAll("[data-stage4-filter-status]").forEach((input) => {
        input.checked = selected.has(input.value);
      });
    }

    function updateStage4FilterStatusToggle(values = state.stage4List.sourceFilters.statuses) {
      $("#stage4-filter-status-summary").textContent = summarizeStage3Statuses(values || []);
      $("#stage4-filter-status-toggle").setAttribute("aria-expanded", String(!$("#stage4-filter-status-popover").hidden));
    }

    function readSelectedStage4FilterStatuses() {
      return Array.from(document.querySelectorAll("[data-stage4-filter-status]"))
        .filter((input) => input.checked)
        .map((input) => input.value);
    }

    function closeStage4FilterStatusPopover() {
      $("#stage4-filter-status-popover").hidden = true;
      updateStage4FilterStatusToggle(readSelectedStage4FilterStatuses());
    }

    function updateStage4FilterLanguageToggle(values = state.stage4List.sourceFilters.languages) {
      $("#stage4-filter-language-summary").textContent = summarizeLanguages(values || []);
      $("#stage4-filter-language-toggle").setAttribute("aria-expanded", String(!$("#stage4-filter-language-popover").hidden));
    }

    function readSelectedStage4FilterLanguages() {
      return Array.from(document.querySelectorAll("[data-stage4-filter-language]"))
        .filter((input) => input.checked)
        .map((input) => input.value);
    }

    function closeStage4FilterLanguagePopover() {
      $("#stage4-filter-language-popover").hidden = true;
      updateStage4FilterLanguageToggle(readSelectedStage4FilterLanguages());
    }

    function closeStage4FilterPopover() {
      $("#stage4-filter-popover").hidden = true;
      $("#stage4-filter-toggle").setAttribute("aria-expanded", "false");
    }

    function syncStage4SourceFilterControls() {
      const filters = state.stage4List.sourceFilters || {};
      const queryInput = $("#stage4-filter-name-query");
      if (queryInput) queryInput.value = filters.nameQuery || "";
      syncStage4FilterLanguageInputs();
      syncStage4FilterStatusInputs();
      updateStage4FilterLanguageToggle();
      updateStage4FilterStatusToggle();
      updateStage4FilterToggle();
    }

    function readStage4SourceFiltersFromForm() {
      return {
        nameQuery: $("#stage4-filter-name-query")?.value.trim() || "",
        languages: readSelectedStage4FilterLanguages(),
        statuses: readSelectedStage4FilterStatuses(),
      };
    }

    function syncStage4SavepointFilterControls() {
      const filters = state.stage4List.savepointFilters || {};
      const queryInput = $("#stage4-savepoint-filter-entry-query");
      const testCountMin = $("#stage4-savepoint-filter-test-count-min");
      const testCountMax = $("#stage4-savepoint-filter-test-count-max");
      const entryPassRateMin = $("#stage4-savepoint-filter-entry-pass-rate-min");
      const entryPassRateMax = $("#stage4-savepoint-filter-entry-pass-rate-max");
      const diffLinesMin = $("#stage4-savepoint-filter-diff-lines-min");
      const diffLinesMax = $("#stage4-savepoint-filter-diff-lines-max");
      if (queryInput) queryInput.value = filters.entryQuery || "";
      if (testCountMin) testCountMin.value = filters.testCountMin || "";
      if (testCountMax) testCountMax.value = filters.testCountMax || "";
      if (entryPassRateMin) entryPassRateMin.value = filters.entryPassRateMin || "";
      if (entryPassRateMax) entryPassRateMax.value = filters.entryPassRateMax || "";
      if (diffLinesMin) diffLinesMin.value = filters.diffLinesMin || "";
      if (diffLinesMax) diffLinesMax.value = filters.diffLinesMax || "";
      syncStage4SavepointFilterStatusInputs();
      updateStage4SavepointFilterStatusToggle();
      updateStage4SavepointFilterToggle();
    }

    function isStage4SavepointNonPendingFilterActive() {
      const statuses = Array.isArray(state.stage4List.savepointFilters?.statuses)
        ? state.stage4List.savepointFilters.statuses
        : [];
      if (statuses.length !== STAGE4_SAVEPOINT_NON_PENDING_STATUSES.length) {
        return false;
      }
      const selected = new Set(statuses);
      return STAGE4_SAVEPOINT_NON_PENDING_STATUSES.every((status) => selected.has(status));
    }

    function updateStage4SavepointFilterToggle() {
      const filters = state.stage4List.savepointFilters || {};
      const toggle = $("#stage4-savepoint-filter-toggle");
      const nonPendingToggle = $("#stage4-savepoint-filter-non-pending");
      const count = stage4ActiveFilterCount(filters);
      const nonPendingActive = isStage4SavepointNonPendingFilterActive();
      if (toggle) {
        toggle.classList.toggle("active", count > 0);
        toggle.textContent = count > 0 ? `筛选（${count}）` : "筛选";
      }
      if (nonPendingToggle) {
        nonPendingToggle.classList.toggle("active", nonPendingActive);
        nonPendingToggle.setAttribute("aria-pressed", String(nonPendingActive));
      }
    }

    function syncStage4SavepointFilterStatusInputs() {
      const selected = new Set(state.stage4List.savepointFilters.statuses || []);
      document.querySelectorAll("[data-stage4-savepoint-filter-status]").forEach((input) => {
        input.checked = selected.has(input.value);
      });
    }

    function updateStage4SavepointFilterStatusToggle(values = state.stage4List.savepointFilters.statuses) {
      $("#stage4-savepoint-filter-status-summary").textContent = summarizeStage4SavepointStatuses(values || []);
      $("#stage4-savepoint-filter-status-toggle").setAttribute(
        "aria-expanded",
        String(!$("#stage4-savepoint-filter-status-popover").hidden),
      );
    }

    function readSelectedStage4SavepointFilterStatuses() {
      return Array.from(document.querySelectorAll("[data-stage4-savepoint-filter-status]"))
        .filter((input) => input.checked)
        .map((input) => input.value);
    }

    function closeStage4SavepointFilterStatusPopover({ flush = true } = {}) {
      const popover = $("#stage4-savepoint-filter-status-popover");
      if (!popover) {
        return;
      }
      popover.hidden = true;
      updateStage4SavepointFilterStatusToggle(readSelectedStage4SavepointFilterStatuses());
      if (flush) {
        flushPendingLiveDetailPayloads();
      }
    }

    function closeStage4SavepointFilterPopover({ flush = true } = {}) {
      closeStage4SavepointFilterStatusPopover({ flush: false });
      const popover = $("#stage4-savepoint-filter-popover");
      if (popover) {
        popover.hidden = true;
      }
      const toggle = $("#stage4-savepoint-filter-toggle");
      if (toggle) {
        toggle.setAttribute("aria-expanded", "false");
      }
      if (flush) {
        flushPendingLiveDetailPayloads();
      }
    }

    function isStage4SavepointFilterInteractionActive() {
      const popover = $("#stage4-savepoint-filter-popover");
      const statusPopover = $("#stage4-savepoint-filter-status-popover");
      return Boolean(
        (popover && !popover.hidden)
        || (statusPopover && !statusPopover.hidden)
      );
    }

    function readStage4SavepointFiltersFromForm() {
      return {
        entryQuery: $("#stage4-savepoint-filter-entry-query")?.value.trim() || "",
        statuses: readSelectedStage4SavepointFilterStatuses(),
        testCountMin: $("#stage4-savepoint-filter-test-count-min")?.value.trim() || "",
        testCountMax: $("#stage4-savepoint-filter-test-count-max")?.value.trim() || "",
        entryPassRateMin: $("#stage4-savepoint-filter-entry-pass-rate-min")?.value.trim() || "",
        entryPassRateMax: $("#stage4-savepoint-filter-entry-pass-rate-max")?.value.trim() || "",
        diffLinesMin: $("#stage4-savepoint-filter-diff-lines-min")?.value.trim() || "",
        diffLinesMax: $("#stage4-savepoint-filter-diff-lines-max")?.value.trim() || "",
      };
    }

    function updateStage4LayoutMode() {
      const layout = $("#stage4-layout");
      if (!layout) return;
      layout.classList.toggle("detail-visible", Boolean(state.stage4List.detailVisible));
      layout.classList.toggle("detail-hidden", !state.stage4List.detailVisible);
    }

    function showStage4Detail(mode = "run") {
      if (mode !== "source") {
        closeStage4SourceDetailStream();
        pendingStage4SourceDetailPayload = null;
      }
      if (mode !== "run") {
        closeStage4RunDetailStream();
        pendingStage4RunDetailPayload = null;
      }
      state.stage4List.detailVisible = true;
      state.stage4List.detailMode = mode;
      updateStage4LayoutMode();
    }

    function setStage4DetailHeader({
      title,
      backLabel,
      subtitle,
      primaryActionLabel = "",
      primaryActionKind = "",
      primaryActionId = "",
      primaryActionDisabled = false,
    }) {
      const titleElement = $("#stage4-detail-title");
      if (titleElement) {
        titleElement.textContent = title || "Stage4 详情";
      }
      const primaryActionButton = $("#stage4-detail-primary-action-btn");
      if (primaryActionButton) {
        const hasPrimaryAction = Boolean(primaryActionLabel && primaryActionKind);
        primaryActionButton.hidden = !hasPrimaryAction;
        primaryActionButton.textContent = primaryActionLabel || "";
        primaryActionButton.dataset.stage4DetailPrimaryAction = hasPrimaryAction ? primaryActionKind : "";
        primaryActionButton.dataset.stage4PrimaryActionId = hasPrimaryAction ? String(primaryActionId || "") : "";
        primaryActionButton.disabled = Boolean(primaryActionDisabled);
      }
      const backButton = $("#stage4-detail-back-btn");
      if (backButton) {
        backButton.textContent = backLabel || "返回列表";
      }
      const message = $("#stage4-detail-message");
      if (message) {
        message.textContent = subtitle || "";
      }
    }

    function hideStage4Detail() {
      closeStage4SourceDetailStream();
      closeStage4RunDetailStream();
      pendingStage4SourceDetailPayload = null;
      pendingStage4RunDetailPayload = null;
      state.pendingStage4RunOpenDefaults = false;
      state.stage4List.detailVisible = false;
      state.stage4List.detailMode = "list";
      state.selectedStage4RunId = null;
      state.selectedStage4SourceSavepointId = null;
      state.selectedStage4SourceGroupKey = null;
      state.stage4SourceDetailPayload = null;
      state.stage4RunDetailPayload = null;
      resetStage4AssetBrowserState();
      resetStage4RunRuntimeEditor();
      updateStage4LayoutMode();
      const body = $("#stage4-detail-body");
      if (body) {
        body.innerHTML = "";
      }
      setStage4DetailHeader({
        title: "Stage4 详情",
        backLabel: "返回列表",
        subtitle: "选择一个 repo/commit，查看 entry file 与 depth",
      });
      renderStage4Sources();
    }

    function resetStage4AssetBrowserState() {
      state.selectedStage4AssetKey = null;
      state.selectedStage4IssueIndex = 1;
      state.selectedStage4ReceiveFeedbackIndex = 1;
    }

    function stage4SourceActionDescriptor(source) {
      const { savepoint } = stage4SourceRowSummary(source || {});
      const latestRun = savepoint.latest_stage4_run || {};
      const latestRunId = String(latestRun.id || "");
      const latestRunStatus = String(latestRun.status || "");
      const savepointId = String(savepoint.id || "");
      if (latestRunStatus === "queued" || latestRunStatus === "running") {
        return {
          visible: false,
          disabled: true,
          label: "",
          kind: "",
          id: "",
        };
      }
      if (latestRunStatus === "pending" && latestRunId) {
        return {
          visible: false,
          disabled: true,
          label: "",
          kind: "",
          id: "",
        };
      }
      if (savepointId) {
        return {
          visible: true,
          disabled: false,
          label: latestRunId ? "重新运行" : "运行",
          kind: latestRunId ? "rerun-stage4-source" : "create-stage4-run",
          id: savepointId,
        };
      }
      return {
        visible: false,
        disabled: true,
        label: "",
        kind: "",
        id: "",
      };
    }

    function setStage4SourceDetailHeader(group, subtitleOverride = "") {
      setStage4DetailHeader({
        title: "Repo 详细",
        backLabel: "返回列表",
        subtitle: subtitleOverride || `${group?.repository?.full_name || "-"} · ${String(group?.snapshot?.source_commit_sha || "").slice(0, 12) || "-"}`,
      });
    }

    function stage4RunDetailActionDescriptor(run) {
      const runId = String(run?.id || "").trim();
      const sourceSavepointId = String(run?.source_savepoint_id || "").trim();
      const runStatus = String(run?.status || "");
      if (Boolean(run?.can_interrupt) && runId) {
        return {
          visible: true,
          disabled: false,
          label: "中断",
          kind: "interrupt-stage4-run",
          id: runId,
        };
      }
      if (sourceSavepointId && runStatus !== "queued" && runStatus !== "running" && runStatus !== "pending") {
        return {
          visible: true,
          disabled: false,
          label: "重新运行",
          kind: "rerun-stage4-source",
          id: sourceSavepointId,
        };
      }
      return {
        visible: false,
        disabled: true,
        label: "",
        kind: "",
        id: "",
      };
    }

    function setStage4RunDetailHeader(run, subtitleOverride = "") {
      const runSource = run?.source || {};
      const { repository, entryFile, savepoint } = stage4SourceRowSummary(runSource);
      const primaryAction = stage4RunDetailActionDescriptor(run);
      setStage4DetailHeader({
        title: "产出数据详细",
        backLabel: state.selectedStage4SourceGroupKey ? "返回产出数据" : "返回列表",
        subtitle: subtitleOverride || [
          repository.full_name || "-",
          entryFile.test_file_path || "-",
          `Depth ${String(savepoint.depth ?? "-")}`,
          `入口通过率 ${formatPercent(savepoint.entry_pass_rate)}`,
          `diff ${stage4DiffStatsText(savepoint.diff_stats)}`,
        ].join(" · "),
        primaryActionLabel: primaryAction.visible ? primaryAction.label : "",
        primaryActionKind: primaryAction.visible ? primaryAction.kind : "",
        primaryActionId: primaryAction.visible ? primaryAction.id : "",
        primaryActionDisabled: primaryAction.disabled,
      });
    }

    function renderStage4Sources() {
      const tbody = $("#stage4-sources-body");
      if (!tbody) return;
      const groups = sortedStage4SourceGroups();
      const page = Math.max(1, Number(state.stage4List.page || 1));
      const totalPages = Math.max(1, Number(state.stage4List.totalPages || 1));
      const total = Math.max(0, Number(state.stage4List.total || 0));
      syncStage4SourceSortButtons();
      syncStage4SourceFilterControls();
      const paginationMeta = $("#stage4-pagination-meta");
      if (paginationMeta) {
        paginationMeta.textContent = `第 ${page} / ${totalPages} 页，共 ${total} 条`;
      }
      const prevButton = $("#stage4-prev-btn");
      if (prevButton) prevButton.disabled = page <= 1;
      const nextButton = $("#stage4-next-btn");
      if (nextButton) nextButton.disabled = page >= totalPages;
      if (!groups.length) {
        tbody.innerHTML = `<tr><td colspan="8" class="muted">当前没有匹配条件的 repo/commit。</td></tr>`;
        return;
      }
      tbody.innerHTML = groups.map((group) => {
        const groupActive = String(group.key || "") === String(state.selectedStage4SourceGroupKey || "");
        const runInFlight = isStage4GroupRunInFlight(group.key);
        const rowClasses = ["task-row"];
        if (groupActive) rowClasses.push("active");
        return `
          <tr
            class="${rowClasses.join(" ")}"
            data-stage4-source-group-key="${escapeHtml(String(group.key || ""))}"
          >
            <td>
              <strong>${escapeHtml(group.repository?.full_name || "-")}</strong>
            </td>
            <td class="mono">${escapeHtml(String(group.snapshot?.source_commit_sha || "").slice(0, 8) || "-")}</td>
            <td>${escapeHtml((group.language_list || []).join("、") || "-")}</td>
            <td>${escapeHtml(String(group.entry_count || 0))}</td>
            <td>${escapeHtml(String(group.savepoint_count || 0))}</td>
            <td>${escapeHtml(group.latest_operation_at ? formatDate(group.latest_operation_at) : "-")}</td>
            <td>${stage4GroupStatusPill(group)}</td>
            <td class="stage4-source-action-cell">
              <div class="stage3-repo-action-stack">
                <button
                  class="secondary tiny"
                  type="button"
                  data-stage4-run-group-key="${escapeHtml(String(group.key || ""))}"
                  ${runInFlight ? "disabled" : ""}
                >${escapeHtml(runInFlight ? "运行中..." : "运行")}</button>
              </div>
            </td>
          </tr>
        `;
      }).join("");
    }

    async function loadStage4Sources() {
      if (state.stage4SourcesLoading) {
        return null;
      }
      state.stage4SourcesLoading = true;
      try {
        const payload = await api(`/api/stage4/sources?${buildStage4SourceListParams().toString()}`);
        state.stage4SourceGroups = payload.groups || [];
        const pagination = payload.pagination || {};
        state.stage4List.page = Number(pagination.page || state.stage4List.page || 1);
        state.stage4List.pageSize = Number(pagination.page_size || state.stage4List.pageSize || 15);
        state.stage4List.total = Number(pagination.total || 0);
        state.stage4List.totalPages = Number(pagination.total_pages || 1);
        renderStage4Sources();
        return payload;
      } catch (error) {
        state.stage4SourceGroups = [];
        renderPanelLoadError("#stage4-sources-body", 8, error);
        state.stage4List.total = 0;
        state.stage4List.totalPages = 1;
        state.stage4List.page = 1;
        const paginationMeta = $("#stage4-pagination-meta");
        if (paginationMeta) {
          paginationMeta.textContent = "第 1 / 1 页，共 0 条";
        }
        const prevButton = $("#stage4-prev-btn");
        if (prevButton) prevButton.disabled = true;
        const nextButton = $("#stage4-next-btn");
        if (nextButton) nextButton.disabled = true;
        return null;
      } finally {
        state.stage4SourcesLoading = false;
      }
    }

    function renderStage4SavepointFilterPanel() {
      const filterCount = stage4ActiveFilterCount(state.stage4List.savepointFilters || {});
      const filterButtonLabel = filterCount > 0 ? `筛选（${filterCount}）` : "筛选";
      return `
        <div class="popover-anchor" id="stage4-savepoint-filter-anchor">
          <button class="secondary tiny filter-toggle${filterCount > 0 ? " active" : ""}" id="stage4-savepoint-filter-toggle" type="button" aria-expanded="false">${escapeHtml(filterButtonLabel)}</button>
          <div class="repo-filter-popover" id="stage4-savepoint-filter-popover" hidden>
            <div class="repo-filter-title">产出数据筛选条件</div>
            <div class="field-grid">
              <label class="full">
                入口文件
                <input id="stage4-savepoint-filter-entry-query" type="text" placeholder="测试文件路径">
              </label>
              <div class="field-group full">
                <div class="group-title">状态</div>
                <div class="selector-anchor" id="stage4-savepoint-filter-status-anchor">
                  <button class="selector-toggle" id="stage4-savepoint-filter-status-toggle" type="button" aria-expanded="false">
                    <span class="selector-summary" id="stage4-savepoint-filter-status-summary">不限</span>
                    <span class="selector-caret">▾</span>
                  </button>
                  <div class="selector-popover" id="stage4-savepoint-filter-status-popover" hidden>
                    <div class="selector-list compact-grid">
                      <label class="selector-option"><input type="checkbox" data-stage4-savepoint-filter-status value="none"><span>无 run</span></label>
                      <label class="selector-option"><input type="checkbox" data-stage4-savepoint-filter-status value="pending"><span>待运行</span></label>
                      <label class="selector-option"><input type="checkbox" data-stage4-savepoint-filter-status value="queued"><span>排队中</span></label>
                      <label class="selector-option"><input type="checkbox" data-stage4-savepoint-filter-status value="running"><span>运行中</span></label>
                      <label class="selector-option"><input type="checkbox" data-stage4-savepoint-filter-status value="generated"><span>已生成</span></label>
                      <label class="selector-option"><input type="checkbox" data-stage4-savepoint-filter-status value="failed"><span>失败</span></label>
                      <label class="selector-option"><input type="checkbox" data-stage4-savepoint-filter-status value="interrupted"><span>已中断</span></label>
                    </div>
                  </div>
                </div>
              </div>
              <label>
                测试点数下界
                <input id="stage4-savepoint-filter-test-count-min" type="number" min="0" step="1" placeholder="可选">
              </label>
              <label>
                测试点数上界
                <input id="stage4-savepoint-filter-test-count-max" type="number" min="0" step="1" placeholder="可选">
              </label>
              <label>
                入口通过率下界（%）
                <input id="stage4-savepoint-filter-entry-pass-rate-min" type="number" min="0" max="100" step="0.1" placeholder="可选">
              </label>
              <label>
                入口通过率上界（%）
                <input id="stage4-savepoint-filter-entry-pass-rate-max" type="number" min="0" max="100" step="0.1" placeholder="可选">
              </label>
              <label>
                diff 行数下界
                <input id="stage4-savepoint-filter-diff-lines-min" type="number" min="0" step="1" placeholder="可选">
              </label>
              <label>
                diff 行数上界
                <input id="stage4-savepoint-filter-diff-lines-max" type="number" min="0" step="1" placeholder="可选">
              </label>
            </div>
            <div class="repo-filter-actions">
              <button class="secondary tiny" type="button" data-stage4-savepoint-filter-reset>清空</button>
              <button class="primary tiny" type="button" data-stage4-savepoint-filter-apply>应用筛选</button>
            </div>
          </div>
        </div>
      `;
    }

    function currentStage4SourceDetailPayload() {
      return state.stage4SourceDetailPayload || null;
    }

    function setStage4SourceDetailPayload(payload) {
      state.stage4SourceDetailPayload = payload || null;
      if (!payload?.group) {
        return;
      }
      state.selectedStage4SourceGroupKey = String(payload.group.key || "");
    }

    function closeStage4SourceDetailStream() {
      if (stage4SourceDetailEventSource) {
        stage4SourceDetailEventSource.close();
      }
      stage4SourceDetailEventSource = null;
      stage4SourceDetailStreamRepositoryId = null;
      stage4SourceDetailStreamSnapshotId = null;
      stage4SourceDetailStreamQuery = null;
    }

    function stage4SourceDetailRequestUrl(groupKey, options = {}) {
      const parsed = parseStage4SourceGroupKey(groupKey);
      if (!parsed) {
        return null;
      }
      const query = buildStage4SourceDetailParams(options).toString();
      return `/api/stage4/sources/${encodeURIComponent(String(parsed.repositoryId))}/snapshots/${encodeURIComponent(String(parsed.snapshotId))}${query ? `?${query}` : ""}`;
    }

    async function fetchStage4SourceDetailPayloadByKey(groupKey, options = {}) {
      const requestUrl = stage4SourceDetailRequestUrl(groupKey, options);
      if (!requestUrl) {
        return null;
      }
      return api(requestUrl);
    }

    async function loadStage4SourceDetailByKey(groupKey) {
      const normalizedGroupKey = String(groupKey || "").trim();
      const requestUrl = stage4SourceDetailRequestUrl(groupKey);
      if (!requestUrl) {
        hideStage4Detail();
        return null;
      }
      state.selectedStage4SourceGroupKey = normalizedGroupKey;
      showStage4Detail("source");
      setStage4DetailHeader({
        title: "Repo 详细",
        backLabel: "返回列表",
        subtitle: "正在加载...",
      });
      try {
        const payload = await api(requestUrl);
        applyLiveStage4SourceDetailPayload(payload);
        syncStage4SourceDetailStream();
        return payload;
      } catch (error) {
        if (
          state.activeTab !== "stage4"
          || !state.stage4List.detailVisible
          || state.stage4List.detailMode !== "source"
          || String(state.selectedStage4SourceGroupKey || "") !== normalizedGroupKey
        ) {
          return null;
        }
        setStage4DetailHeader({
          title: "Repo 详细",
          backLabel: "返回列表",
          subtitle: `加载失败: ${error.message}`,
        });
        closeStage4SourceDetailStream();
        return null;
      }
    }

    function rerenderStage4SourceDetailPreservingScroll(payload) {
      const detailBody = $("#stage4-detail-body");
      const scrollTop = detailBody?.scrollTop || 0;
      renderStage4SourceDetail(payload);
      if (detailBody) {
        detailBody.scrollTop = scrollTop;
      }
    }

    function applyLiveStage4SourceDetailPayload(payload) {
      if (!payload?.group) {
        return;
      }
      if (state.activeTab !== "stage4" || !state.stage4List.detailVisible || state.stage4List.detailMode !== "source") {
        pendingStage4SourceDetailPayload = null;
        return;
      }
      const incomingKey = String(payload.group.key || "");
      if (incomingKey !== String(state.selectedStage4SourceGroupKey || incomingKey)) {
        return;
      }
      if (hasActiveTextSelection() || isStage4SavepointFilterInteractionActive()) {
        pendingStage4SourceDetailPayload = payload;
        return;
      }
      setStage4SourceDetailPayload(payload);
      rerenderStage4SourceDetailPreservingScroll(payload);
    }

    function syncStage4SourceDetailStream() {
      const payload = currentStage4SourceDetailPayload();
      const group = payload?.group || null;
      const repositoryId = group?.repository?.id;
      const snapshotId = group?.snapshot?.id;
      if (
        state.activeTab !== "stage4"
        || !state.stage4List.detailVisible
        || state.stage4List.detailMode !== "source"
        || !repositoryId
        || !snapshotId
        || typeof EventSource === "undefined"
      ) {
        closeStage4SourceDetailStream();
        return;
      }
      const groupStatus = String(group?.status || "");
      const hasActiveSource = groupStatus === "queued"
        || groupStatus === "running"
        || (
          Array.isArray(payload?.sources)
        && payload.sources.some((source) => {
          const status = stage4RunStatusFilterKey((stage4SourceRowSummary(source).savepoint || {}).latest_stage4_run);
          return status === "queued" || status === "running";
        })
        );
      if (!hasActiveSource) {
        closeStage4SourceDetailStream();
        return;
      }
      if (
        stage4SourceDetailEventSource
        && String(stage4SourceDetailStreamRepositoryId || "") === String(repositoryId)
        && String(stage4SourceDetailStreamSnapshotId || "") === String(snapshotId)
        && String(stage4SourceDetailStreamQuery || "") === String(buildStage4SourceDetailParams().toString())
      ) {
        return;
      }
      closeStage4SourceDetailStream();
      const query = buildStage4SourceDetailParams().toString();
      const source = new EventSource(
        `/api/stage4/sources/${encodeURIComponent(String(repositoryId))}/snapshots/${encodeURIComponent(String(snapshotId))}/events${query ? `?${query}` : ""}`,
      );
      stage4SourceDetailEventSource = source;
      stage4SourceDetailStreamRepositoryId = String(repositoryId);
      stage4SourceDetailStreamSnapshotId = String(snapshotId);
      stage4SourceDetailStreamQuery = query;
      const isCurrentStream = () => (
        stage4SourceDetailEventSource === source
        && String(stage4SourceDetailStreamRepositoryId || "") === String(repositoryId)
        && String(stage4SourceDetailStreamSnapshotId || "") === String(snapshotId)
        && String(stage4SourceDetailStreamQuery || "") === String(query)
      );
      const handlePayload = (nextPayload, { terminal = false } = {}) => {
        if (!isCurrentStream()) {
          return;
        }
        applyLiveStage4SourceDetailPayload(nextPayload);
        if (terminal) {
          closeStage4SourceDetailStream();
          loadStage4Sources();
        }
      };
      source.addEventListener("snapshot", (event) => {
        handlePayload(JSON.parse(event.data));
      });
      source.addEventListener("terminal", (event) => {
        handlePayload(JSON.parse(event.data), { terminal: true });
      });
      source.onerror = () => {
        if (!isCurrentStream()) {
          return;
        }
        closeStage4SourceDetailStream();
      };
    }

    function renderStage4SourceDetail(payload) {
      const group = payload?.group || null;
      const body = $("#stage4-detail-body");
      if (!body || !group) {
        hideStage4Detail();
        return;
      }
      setStage4SourceDetailPayload(payload);
      state.selectedStage4RunId = null;
      state.stage4RunDetailPayload = null;
      state.pendingStage4RunOpenDefaults = false;
      resetStage4AssetBrowserState();
      closeStage4RunDetailStream();
      pendingStage4RunDetailPayload = null;
      showStage4Detail("source");
      setStage4SourceDetailHeader(group);
      const rows = sortedStage4SavepointSources(payload);
      const savepointNonPendingActive = isStage4SavepointNonPendingFilterActive();
      body.innerHTML = `
        <div class="stage2-run-stack">
          <section class="detail-card detail-section">
            <div class="stage2-run-header">
              <div>
                <h4>产出数据列表</h4>
              </div>
              <div class="section-actions">
                <div class="button-row">
                  <button
                    class="secondary tiny filter-toggle${savepointNonPendingActive ? " active" : ""}"
                    id="stage4-savepoint-filter-non-pending"
                    type="button"
                    aria-pressed="${savepointNonPendingActive ? "true" : "false"}"
                  >只看非待运行</button>
                  ${renderStage4SavepointFilterPanel()}
                </div>
              </div>
            </div>
            <div class="table-wrap stage4-nested-table-wrap">
              <table class="stage4-source-table stage4-savepoint-table">
                <colgroup>
                  <col class="stage4-savepoint-col-entry">
                  <col class="stage4-savepoint-col-tests">
                  <col class="stage4-savepoint-col-depth">
                  <col class="stage4-savepoint-col-rate">
                  <col class="stage4-savepoint-col-split">
                  <col class="stage4-savepoint-col-diff">
                  <col class="stage4-savepoint-col-status">
                  <col class="stage4-savepoint-col-latest">
                  <col class="stage4-savepoint-col-produced">
                  <col class="stage4-savepoint-col-action">
                </colgroup>
                <thead>
                  <tr>
                    <th>${renderStage4SavepointSortButton("Entry File", "entry_file")}</th>
                    <th>${renderStage4SavepointSortButton("测试点数", "test_count")}</th>
                    <th>${renderStage4SavepointSortButton("Depth", "depth")}</th>
                    <th>${renderStage4SavepointSortButton("入口通过率", "entry_pass_rate")}</th>
                    <th>${renderStage4SavepointSortButton("P2P / F2P", "p2p_count")}</th>
                    <th>${renderStage4SavepointSortButton("diff 行数", "diff_lines")}</th>
                    <th>${renderStage4SavepointSortButton("状态", "status")}</th>
                    <th>${renderStage4SavepointSortButton("最近操作时间", "latest_operation_at")}</th>
                    <th>${renderStage4SavepointSortButton("产出 issue", "produced_variant_count")}</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  ${rows.length ? rows.map((source) => {
                    const { entryFile, savepoint } = stage4SourceRowSummary(source);
                    const status = stage4RunStatusFilterKey(savepoint.latest_stage4_run);
                    const latestRunId = String(savepoint.latest_stage4_run?.id || "");
                    const latestRunStatus = String(savepoint.latest_stage4_run?.status || "");
                    const latestRunResult = String(savepoint.latest_stage4_run?.result || "");
                    const actionButton = (
                      latestRunStatus === "queued" || latestRunStatus === "running"
                    )
                      ? `<button class="secondary tiny" type="button" disabled>运行中</button>`
                      : (
                        latestRunStatus === "pending" && latestRunId
                      )
                        ? `<button class="secondary tiny" type="button" disabled title="旧的未启动 run 需要删除后重新生成">待运行</button>`
                        : `<button class="primary tiny" type="button" data-stage4-create-source-id="${escapeHtml(String(savepoint.id || ""))}">${latestRunStatus === "completed" && latestRunResult === "generated" ? "重新生成" : "生成"}</button>`;
                    const latestOperationAt = stage4SavepointSortValue(source, "latest_operation_at");
                    const producedCounts = stage4SavepointProducedCounts(source);
                    const sourceActive = String(savepoint.id || "") === String(state.selectedStage4SourceSavepointId || "");
                    return `
                      <tr
                        class="task-row${sourceActive ? " active" : ""}"
                        data-stage4-source-savepoint-id="${escapeHtml(String(savepoint.id || ""))}"
                        data-stage4-source-latest-run-id="${escapeHtml(latestRunId)}"
                      >
                        <td class="mono">${escapeHtml(entryFile.test_file_path || "-")}</td>
                        <td>${escapeHtml(String(entryFile.baseline_total_tests || 0))}</td>
                        <td>${escapeHtml(String(savepoint.depth ?? "-"))}</td>
                        <td>${escapeHtml(formatPercent(savepoint.entry_pass_rate))}</td>
                        <td>${escapeHtml(`${savepoint.p2p_count || 0} / ${savepoint.f2p_count || 0}`)}</td>
                        <td>${escapeHtml(stage4DiffStatsText(savepoint.diff_stats))}</td>
                        <td>${stage4SavepointStatusPill(status)}</td>
                        <td>${escapeHtml(latestOperationAt ? formatDate(latestOperationAt) : "-")}</td>
                        <td>${escapeHtml(String(producedCounts.issueCount))}</td>
                        <td class="stage4-source-action-cell">
                          <div class="stage3-repo-action-stack">
                            ${actionButton}
                          </div>
                        </td>
                      </tr>
                    `;
                  }).join("") : `<tr><td colspan="10" class="muted">当前没有匹配条件的产出数据。</td></tr>`}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      `;
      syncStage4SavepointFilterControls();
      syncStage4SourceDetailStream();
    }

    function renderStage4MarkdownBlock(text) {
      return `<pre class="stage4-markdown-preview">${escapeHtml(text || "")}</pre>`;
    }

    function renderStage4SourceSummary(source) {
      const { repository, snapshot, entryFile, savepoint } = stage4SourceRowSummary(source || {});
      const metrics = [
        { label: "Repo", value: repository.full_name || "-" },
        { label: "Commit", value: String(snapshot.source_commit_sha || "").slice(0, 12) || "-", mono: true },
        { label: "Entry File", value: entryFile.test_file_path || "-", mono: true },
        { label: "Depth", value: savepoint.depth ?? "-" },
        { label: "入口通过率", value: formatPercent(savepoint.entry_pass_rate) },
        { label: "P2P / F2P", value: `${savepoint.p2p_count || 0} / ${savepoint.f2p_count || 0}` },
        { label: "diff 行数", value: stage4DiffStatsText(savepoint.diff_stats) },
      ];
      return `
        <div class="stage2-summary-grid">
          ${metrics.map((metric) => `
            <div class="stage2-metric">
              <div class="label">${escapeHtml(metric.label)}</div>
              <strong${metric.mono ? ' class="mono multiline"' : ""}>${escapeHtml(String(metric.value))}</strong>
            </div>
          `).join("")}
        </div>
      `;
    }

    function renderStage4RunCard(run, runNumber) {
      const active = String(run?.id || "") === String(state.selectedStage4RunId || "");
      const runStatus = String(run?.status || "");
      const canRerun = runStatus !== "queued" && runStatus !== "running" && runStatus !== "pending";
      const canInterrupt = Boolean(run?.can_interrupt);
      const canDelete = Boolean(run?.can_delete);
      const runOrRerunActionAttr = `data-stage4-rerun-source-savepoint-id="${escapeHtml(String(run.source_savepoint_id || ""))}"`;
      const runOrRerunDisabled = canRerun
        ? ""
        : (
          runStatus === "pending"
            ? 'disabled title="旧的未启动 run 需要删除后重新生成"'
            : 'disabled title="排队中或运行中的 run 不能重跑"'
        );
      return `
        <div
          class="stage2-history-card stage4-history-card${active ? " active" : ""}"
          data-stage4-run-id="${escapeHtml(String(run.id || ""))}"
          data-stage4-open-run-id="${escapeHtml(String(run.id || ""))}"
          role="button"
          tabindex="0"
        >
          <div class="stage2-history-card-main">
            <div class="stage2-history-card-title-row">
              <div class="stage2-history-card-title">运行 ${runNumber}</div>
              <div class="stage2-history-display-status ${escapeHtml(stage4StatusClass(run))}">${escapeHtml(stage4StatusLabel(run))}</div>
            </div>
            <div class="stage2-history-card-meta">
              <div>创建于 ${escapeHtml(formatDate(run.created_at))}</div>
              <div>耗时：${escapeHtml(formatDuration(run.duration_seconds))}</div>
            </div>
          </div>
          <div class="stage2-history-card-actions">
            <button
              class="stage2-history-action stage2-history-interrupt"
              type="button"
              data-stage4-interrupt-run-id="${escapeHtml(String(run.id || ""))}"
              ${canInterrupt ? "" : 'disabled title="只有排队中或运行中的 run 可以中断"'}
            >中断</button>
            <button
              class="stage2-history-action stage2-history-rerun"
              type="button"
              ${runOrRerunActionAttr}
              ${runOrRerunDisabled}
            >重跑</button>
            <button
              class="stage2-history-action stage2-history-delete"
              type="button"
              data-stage4-delete-run-id="${escapeHtml(String(run.id || ""))}"
              ${canDelete ? "" : 'disabled title="排队中或运行中的 run 不能删除"'}
            >删除</button>
          </div>
        </div>
      `;
    }

    function stage4RunsForDisplay(run) {
      return (Array.isArray(run?.run_history) ? run.run_history : [])
        .slice()
        .sort((left, right) => {
          const leftTime = Date.parse(left.created_at || "") || 0;
          const rightTime = Date.parse(right.created_at || "") || 0;
          if (leftTime !== rightTime) {
            return rightTime - leftTime;
          }
          return String(right.id || "").localeCompare(String(left.id || ""));
        });
    }

    function renderStage4RunHistory(run) {
      const runs = stage4RunsForDisplay(run);
      if (!runs.length) {
        return `<div class="stage2-empty">当前 savepoint 还没有 Stage4 run 历史。</div>`;
      }
      const runNumberById = buildStage4RunNumberById(runs);
      return `
        <div class="stage2-history-strip-wrap">
          <div class="stage2-history-strip">
            ${runs.map((candidate) => renderStage4RunCard(candidate, runNumberById.get(String(candidate.id)) || 1)).join("")}
          </div>
        </div>
      `;
    }

    function resetStage4RunRuntimeEditor() {
      state.stage4RunRuntimeEditor = {
        runId: null,
        draft: null,
        issuerApiKeyPreview: "",
        message: "",
        saving: false,
      };
    }

    function stage4RunRuntimeSnapshotToDraft(run) {
      const runtime = stage4RuntimeSnapshot(run);
      const issuer = runtime.issuer || {};
      const hyperparameters = runtime.hyperparameters || {};
      return {
        issuer_model: issuer.model || "",
        issuer_base_url: issuer.base_url || "",
        issuer_api_key: null,
        issuer_preset: issuer.preset || "default",
        issuer_max_iterations: issuer.max_iterations ?? null,
        issuer_timeout_seconds: issuer.timeout_seconds ?? null,
        build_timeout_seconds: hyperparameters.build_timeout_seconds ?? null,
      };
    }

    function isEditingStage4RunRuntime(runId) {
      return String(state.stage4RunRuntimeEditor.runId || "") === String(runId || "");
    }

    function startStage4RunRuntimeEdit(run) {
      const runtime = stage4RuntimeSnapshot(run);
      state.stage4RunRuntimeEditor = {
        runId: String(run.id || ""),
        draft: stage4RunRuntimeSnapshotToDraft(run),
        issuerApiKeyPreview: String(runtime?.issuer?.api_key_preview || ""),
        message: "",
        saving: false,
      };
      renderStage4RunDetail(run);
    }

    async function cancelStage4RunRuntimeEdit(runId) {
      if (!isEditingStage4RunRuntime(runId)) {
        return;
      }
      resetStage4RunRuntimeEditor();
      await loadStage4RunDetail(runId);
    }

    function updateStage4RunRuntimeEditorDraft(field, value) {
      if (!state.stage4RunRuntimeEditor.draft) {
        return;
      }
      state.stage4RunRuntimeEditor.draft = {
        ...state.stage4RunRuntimeEditor.draft,
        [field]: value,
      };
      state.stage4RunRuntimeEditor.message = "";
    }

    function readStage4RunRuntimeConfigForm() {
      return {
        issuer_model: $("#stage4-run-issuer-model")?.value.trim() || null,
        issuer_base_url: $("#stage4-run-issuer-base-url")?.value.trim() || null,
        issuer_api_key: $("#stage4-run-issuer-api-key")?.value.trim() || null,
        issuer_preset: $("#stage4-run-issuer-preset")?.value || null,
        issuer_max_iterations: readOptionalNumber("#stage4-run-issuer-max-iterations"),
        issuer_timeout_seconds: readOptionalNumber("#stage4-run-issuer-timeout"),
        build_timeout_seconds: readOptionalNumber("#stage4-run-build-timeout"),
      };
    }

    async function saveStage4RunRuntimeConfig(runId) {
      if (!runId) {
        return null;
      }
      const saveButton = document.querySelector(`[data-stage4-run-runtime-save-id="${CSS.escape(String(runId))}"]`);
      if (saveButton) {
        saveButton.disabled = true;
      }
      state.stage4RunRuntimeEditor.saving = true;
      state.stage4RunRuntimeEditor.message = "正在保存...";
      try {
        const payload = await api(`/api/stage4/runs/${encodeURIComponent(runId)}/runtime`, {
          method: "PATCH",
          body: JSON.stringify(readStage4RunRuntimeConfigForm()),
        });
        resetStage4RunRuntimeEditor();
        await loadStage4Sources();
        renderStage4RunDetail(payload);
        setRefreshMeta();
        return payload;
      } catch (error) {
        state.stage4RunRuntimeEditor.saving = false;
        state.stage4RunRuntimeEditor.message = `保存失败: ${error.message}`;
        await loadStage4RunDetail(runId);
        return null;
      } finally {
        if (saveButton) {
          saveButton.disabled = false;
        }
      }
    }

    function renderStage4RunRuntimeEditor(run) {
      const editor = state.stage4RunRuntimeEditor;
      const draft = editor.draft || stage4RunRuntimeSnapshotToDraft(run);
      const issuerApiKeyPlaceholder = editor.issuerApiKeyPreview || "保留当前 Key";
      return `
        <div class="stage2-config-grid">
          <div class="stage2-config-section">Issuer agent 配置</div>
          <div class="stage2-agent-config-row">
            ${renderStage2RunRuntimeEditableField("模型", `<input id="stage4-run-issuer-model" data-stage4-run-runtime-field="issuer_model" type="text" value="${escapeHtml(String(draft.issuer_model || ""))}">`)}
            ${renderStage2RunRuntimeEditableField("BaseURL", `<input id="stage4-run-issuer-base-url" data-stage4-run-runtime-field="issuer_base_url" type="url" value="${escapeHtml(String(draft.issuer_base_url || ""))}">`)}
            ${renderStage2RunRuntimeEditableField("APIKey", `<input id="stage4-run-issuer-api-key" data-stage4-run-runtime-field="issuer_api_key" type="password" autocomplete="off" placeholder="${escapeHtml(issuerApiKeyPlaceholder)}" value="${escapeHtml(String(draft.issuer_api_key || ""))}">`)}
            ${renderStage2RunRuntimeEditableField("OpenHands 预设", `
              <select id="stage4-run-issuer-preset" data-stage4-run-runtime-field="issuer_preset">
                <option value="gpt5"${draft.issuer_preset === "gpt5" ? " selected" : ""}>gpt5：patch 写文件</option>
                <option value="default"${draft.issuer_preset === "default" ? " selected" : ""}>default：默认文件编辑器</option>
              </select>
            `)}
            ${renderStage2RunRuntimeEditableField("最大迭代步数", `<input id="stage4-run-issuer-max-iterations" data-stage4-run-runtime-field="issuer_max_iterations" type="number" min="10" max="1000" step="1" value="${escapeHtml(draft.issuer_max_iterations == null ? "" : String(draft.issuer_max_iterations))}">`)}
            ${renderStage2RunRuntimeEditableField("超时时限（秒）", `<input id="stage4-run-issuer-timeout" data-stage4-run-runtime-field="issuer_timeout_seconds" type="number" min="30" max="14400" step="1" value="${escapeHtml(draft.issuer_timeout_seconds == null ? "" : String(draft.issuer_timeout_seconds))}">`)}
          </div>
          <div class="stage2-config-section">超参数</div>
          <div class="stage2-hyper-config-row">
            ${renderStage2RunRuntimeEditableField("build 超时上限（秒）", `<input id="stage4-run-build-timeout" data-stage4-run-runtime-field="build_timeout_seconds" type="number" min="30" max="7200" step="1" value="${escapeHtml(draft.build_timeout_seconds == null ? "" : String(draft.build_timeout_seconds))}">`)}
          </div>
        </div>
        <div class="stage2-runtime-panel-message">${escapeHtml(editor.message || "")}</div>
      `;
    }

    function renderStage4SummaryGrid(run) {
      const metrics = [
        { label: "状态", value: stage4StatusLabel(run) },
        { label: "耗时", value: formatDuration(run?.duration_seconds) },
        { label: "TOKEN", value: stage4TokenDisplayText(run), multiline: true },
        { label: "当前阶段", value: stage4PhaseLabel(run?.phase) },
        { label: "Issue", value: stage4ProducedIssueCountText(run) },
      ];
      return `
        <div class="stage2-summary-grid">
          ${metrics.map((metric) => `
            <div class="stage2-metric">
              <div class="label">${escapeHtml(metric.label)}</div>
              <strong${metric.multiline ? ' class="multiline"' : (metric.mono ? ' class="mono multiline"' : "")}>${escapeHtml(String(metric.value))}</strong>
            </div>
          `).join("")}
        </div>
      `;
    }

    function renderStage4RunRuntimePanel(run) {
      const runtime = stage4RuntimeSnapshot(run);
      const issuer = runtime.issuer || {};
      const hyperparameters = runtime.hyperparameters || {};
      const issuerApiKeyValue = issuer.api_key_preview || (issuer.api_key ? apiKeyPreview(issuer.api_key) : "未归档");
      const runtimeLabel = issuer.model ? `Issuer：${String(issuer.model)}` : "展开查看该次运行冻结的 issuer agent 与超参数配置";
      const panelOpenAttr = state.stage4DetailSections.runtimeConfig ? " open" : "";
      const isEditing = isEditingStage4RunRuntime(run.id);
      const canEdit = !run.is_active;
      const headerActions = isEditing
        ? `
          <div class="stage2-runtime-panel-actions">
            <button class="secondary tiny" type="button" data-stage4-run-runtime-cancel-id="${escapeHtml(String(run.id || ""))}">取消</button>
            <button class="primary tiny" type="button" data-stage4-run-runtime-save-id="${escapeHtml(String(run.id || ""))}" ${state.stage4RunRuntimeEditor.saving ? "disabled" : ""}>${state.stage4RunRuntimeEditor.saving ? "保存中..." : "保存"}</button>
          </div>
        `
        : `<button class="secondary tiny" type="button" data-stage4-run-runtime-edit-id="${escapeHtml(String(run.id || ""))}" ${canEdit ? "" : "disabled"}>编辑</button>`;
      return `
        <details class="detail-card detail-section collapsible-card" data-stage4-detail-section="runtimeConfig"${panelOpenAttr}>
          <summary class="collapsible-summary">
            <div class="stage2-runtime-panel-head">
              <div class="stage2-runtime-panel-head-left">
                <h4>运行配置</h4>
                ${headerActions}
              </div>
              <div class="stage2-runtime-panel-summary">${escapeHtml(runtimeLabel)}</div>
            </div>
            <span class="collapse-toggle">⌃</span>
          </summary>
          <div class="collapsible-content">
            ${isEditing ? renderStage4RunRuntimeEditor(run) : `
              <div class="stage2-config-grid">
                <div class="stage2-config-section">Issuer agent 配置</div>
                <div class="stage2-agent-config-row">
                  ${renderStage2RuntimeReadonlyField("模型", issuer.model, { mono: true })}
                  ${renderStage2RuntimeReadonlyField("BaseURL", issuer.base_url, { mono: true })}
                  ${renderStage2RuntimeReadonlyField("APIKey", issuerApiKeyValue, { mono: true })}
                  ${renderStage2RuntimeReadonlyField("OpenHands 预设", issuer.preset)}
                  ${renderStage2RuntimeReadonlyField("最大迭代步数", issuer.max_iterations)}
                  ${renderStage2RuntimeReadonlyField("超时时限（秒）", issuer.timeout_seconds)}
                </div>
                <div class="stage2-config-section">超参数</div>
                <div class="stage2-hyper-config-row">
                  ${renderStage2RuntimeReadonlyField("build 超时上限（秒）", hyperparameters.build_timeout_seconds)}
                  ${renderStage2RuntimeReadonlyField("issue 数量", run?.issue_variant_count)}
                </div>
              </div>
            `}
          </div>
        </details>
      `;
    }

    function renderStage4RunSummaryStack(run) {
      return `
        <div class="stage2-summary-stack">
          ${renderStage4RunRuntimePanel(run)}
          ${renderStage4SummaryGrid(run)}
        </div>
      `;
    }

    function stage4SourceIssues(run) {
      const issues = Array.isArray(run?.issue_variants) ? run.issue_variants : [];
      return issues;
    }

    function stage4CompletionArchiveAssets(run) {
      const archives = run?.llm_completion_archives || {};
      const runId = run?.id;
      const configuredRoles = Array.isArray(run?.runtime_snapshot?.stage4?.generation_config?.enabled_styles)
        ? run.runtime_snapshot.stage4.generation_config.enabled_styles
        : [];
      const roles = [...new Set([...configuredRoles, ...Object.keys(archives)])];
      return roles.map((role) => {
        const archive = archives[role] || {};
        const fileCount = Number(archive.file_count || 0);
        const files = Array.isArray(archive.files) ? archive.files : [];
        const title = dataPoolCompletionRoleTitle(role);
        return {
          key: `${role}_llm_completions`,
          title,
          kind: "completion_archive",
          available: fileCount > 0,
          meta: fileCount > 0 ? `${fileCount} 个文件` : "暂无",
          emptyText: `当前还没有 ${title}。`,
          value: fileCount > 0 ? archive : {},
          files,
          runId,
          archiveRole: role,
          downloadUrl: (fileCount > 0 && runId)
            ? `/api/stage4/runs/${encodeURIComponent(runId)}/llm-completions/${role}.zip`
            : "",
        };
      });
    }

    function stage4GoldPatchAsset(run) {
      const savepoint = run?.source?.savepoint || {};
      const goldPatchText = String(savepoint.gold_patch_text || "");
      return {
        key: "gold_patch",
        title: "gold patch",
        kind: "gold_patch",
        available: Boolean(goldPatchText.trim()),
        meta: goldPatchText.trim() ? stage4DiffStatsText(savepoint.diff_stats || {}) : "暂无",
        emptyText: "当前没有 gold patch。",
        value: goldPatchText,
      };
    }

    function stage4ReceiveFeedbackEntries(run) {
      const events = Array.isArray(run?.events) ? run.events : [];
      const entries = events.flatMap((event) => {
        const title = String(event?.title || "");
        const payload = event?.payload && typeof event.payload === "object" ? event.payload : {};
        const rawResultPayload = payload?.result_payload && typeof payload.result_payload === "object"
          ? payload.result_payload
          : null;
        if (title === "Receive tool call processed") {
          const status = payload.final_failure ? "fatal" : payload.complete ? "complete" : "incomplete";
          const accepted = Array.isArray(payload.accepted_items) ? payload.accepted_items : [];
          const rejected = Array.isArray(payload.rejected_items) ? payload.rejected_items : [];
          const missing = payload.missing && typeof payload.missing === "object"
            ? payload.missing
            : {};
          return [{
            request_id: String(payload.request_id || ""),
            status,
            accepted,
            rejected,
            missing,
            message: String(event?.message || ""),
            created_at: String(event?.created_at || ""),
            event_id: Number(event?.id || 0),
            receive_call_index: Number(payload.receive_call_index || 0),
            raw_json: rawResultPayload || {
              request_id: String(payload.request_id || ""),
              complete: Boolean(payload.complete),
              final_failure: Boolean(payload.final_failure),
              pause_for_terminal: Boolean(payload.complete || payload.final_failure),
              message: String(event?.message || ""),
              feedback: {
                status,
                accepted,
                rejected,
                missing,
              },
            },
          }];
        }
        if (title === "Receive tool host failure" || title === "Receive tool failure publication recovered") {
          const message = String(event?.message || "");
          return [{
            request_id: String(payload.request_id || ""),
            status: "fatal",
            accepted: [],
            rejected: [],
            missing: {},
            message,
            created_at: String(event?.created_at || ""),
            event_id: Number(event?.id || 0),
            receive_call_index: Number(payload.receive_call_index || 0),
            error_type: String(payload.error_type || ""),
            request_path: String(payload.request_path || ""),
            raw_json: rawResultPayload || {
              request_id: String(payload.request_id || ""),
              complete: false,
              final_failure: true,
              pause_for_terminal: true,
              message,
              feedback: {
                status: "fatal",
                accepted: [],
                rejected: [],
                missing: {},
              },
            },
          }];
        }
        return [];
      });
      return entries
        .sort((left, right) => {
          const leftCall = Number(left.receive_call_index || 0);
          const rightCall = Number(right.receive_call_index || 0);
          if (leftCall > 0 && rightCall > 0 && leftCall !== rightCall) {
            return leftCall - rightCall;
          }
          const leftTime = Date.parse(left.created_at || "") || 0;
          const rightTime = Date.parse(right.created_at || "") || 0;
          if (leftTime !== rightTime) {
            return leftTime - rightTime;
          }
          return Number(left.event_id || 0) - Number(right.event_id || 0);
        })
        .map((entry, index) => ({
          ...entry,
          entry_index: Number(entry.receive_call_index || 0) || index + 1,
        }));
    }

    function selectedStage4CompletionFile(asset) {
      const files = stage2CompletionFiles(asset);
      if (files.length === 0) {
        return null;
      }
      const selectedPath = state.selectedStage4CompletionFileByAssetKey[asset.key];
      return files.find((file) => String(file.path) === String(selectedPath)) || files[0];
    }

    function stage4CompletionFileCacheKey(asset, filePath) {
      return [
        asset?.runId || "",
        asset?.archiveRole || "",
        filePath || "",
      ].join("::");
    }

    function buildStage4RunAssets(run) {
      const issues = stage4SourceIssues(run);
      const receiveFeedback = stage4ReceiveFeedbackEntries(run);
      return [
        {
          key: "source",
          title: "源 savepoint",
          kind: "source",
          available: Boolean(run?.source),
          meta: run?.source ? "已加载" : "暂无",
          emptyText: "当前没有源 savepoint 信息。",
        },
        {
          key: "issues",
          title: "issue variants",
          kind: "issues",
          available: issues.length > 0,
          meta: issues.length > 0 ? `${issues.length} 条` : "暂无",
          emptyText: "当前还没有 issue variants。",
        },
        {
          key: "receive_feedback",
          title: "receive feedback",
          kind: "receive_feedback",
          available: receiveFeedback.length > 0,
          meta: receiveFeedback.length > 0 ? `${receiveFeedback.length} 次` : "暂无",
          emptyText: "当前还没有 receive feedback。",
        },
        stage4GoldPatchAsset(run),
        ...stage4CompletionArchiveAssets(run),
      ];
    }

    function currentStage4Asset(run) {
      const assets = buildStage4RunAssets(run);
      const validKeys = new Set(assets.map((asset) => asset.key));
      if (!validKeys.has(state.selectedStage4AssetKey)) {
        state.selectedStage4AssetKey = (assets.find((asset) => asset.available) || assets[0] || {}).key || null;
      }
      return assets.find((asset) => asset.key === state.selectedStage4AssetKey) || assets[0] || null;
    }

    function selectedStage4Issue(run) {
      const issues = Array.isArray(run?.issue_variants) ? run.issue_variants : [];
      if (!issues.length) return null;
      let selected = issues.find((item) => Number(item.variant_index) === Number(state.selectedStage4IssueIndex));
      if (!selected) {
        selected = issues[0];
        state.selectedStage4IssueIndex = Number(selected.variant_index || 1);
      }
      return selected;
    }

    function selectedStage4ReceiveFeedback(run) {
      const entries = stage4ReceiveFeedbackEntries(run);
      if (!entries.length) return null;
      let selected = entries.find((item) => Number(item.entry_index) === Number(state.selectedStage4ReceiveFeedbackIndex));
      if (!selected) {
        selected = entries[0];
        state.selectedStage4ReceiveFeedbackIndex = Number(selected.entry_index || 1);
      }
      return selected;
    }

    function renderStage4AssetVersions(run, asset) {
      if (!run || !asset) {
        return "";
      }
      if (asset.kind === "issues") {
        const issues = Array.isArray(run.issue_variants) ? run.issue_variants : [];
        if (!issues.length) {
          return "";
        }
        const selected = selectedStage4Issue(run);
        return `
          <div class="stage2-asset-version-strip">
            ${issues.map((variant) => `
              <button
                class="stage2-asset-version-chip${Number(variant.variant_index) === Number(selected?.variant_index) ? " active" : ""}"
                type="button"
                data-stage4-issue-select-index="${escapeHtml(String(variant.variant_index))}"
              >${escapeHtml(`Issue ${variant.variant_index} · ${variant.style || "-"}`)}</button>
            `).join("")}
          </div>
        `;
      }
      if (asset.kind === "receive_feedback") {
        const entries = stage4ReceiveFeedbackEntries(run);
        if (!entries.length) {
          return "";
        }
        const selected = selectedStage4ReceiveFeedback(run);
        return `
          <div class="stage2-asset-version-strip">
            ${entries.map((entry) => `
              <button
                class="stage2-asset-version-chip${Number(entry.entry_index) === Number(selected?.entry_index) ? " active" : ""}"
                type="button"
                data-stage4-receive-feedback-select-index="${escapeHtml(String(entry.entry_index))}"
              >${escapeHtml(`Call ${entry.entry_index} · ${entry.status}`)}</button>
            `).join("")}
          </div>
        `;
      }
      return "";
    }

    function renderStage4IssueAsset(run) {
      const issues = Array.isArray(run?.issue_variants) ? run.issue_variants : [];
      if (!issues.length) {
        return `<div class="stage2-empty">当前还没有 issue variants。</div>`;
      }
      const selected = selectedStage4Issue(run);
      return renderStage4PublicVariantMarkdown(selected, "当前没有 issue markdown。");
    }

    function renderStage4ReceiveFeedbackAsset(run) {
      const entries = stage4ReceiveFeedbackEntries(run);
      if (!entries.length) {
        return `<div class="stage2-empty">当前还没有 receive feedback。</div>`;
      }
      const selected = selectedStage4ReceiveFeedback(run);
      return renderJsonPre(selected?.raw_json, "当前没有 receive feedback 数据。");
    }

    function renderStage4GoldPatchAsset(asset) {
      const text = String(asset?.value || "");
      if (!text.trim()) {
        return `<div class="stage2-empty">${escapeHtml(asset?.emptyText || "当前没有 gold patch。")}</div>`;
      }
      return `<pre>${escapeHtml(text)}</pre>`;
    }

    function renderStage4CompletionArchivePreview(asset) {
      if (!asset.available) {
        return `<div class="stage2-empty">${escapeHtml(asset.emptyText)}</div>`;
      }
      const files = stage2CompletionFiles(asset);
      if (files.length === 0) {
        return `<div class="stage2-empty">当前没有可预览的 completion JSON 文件。</div>`;
      }
      const selectedFile = selectedStage4CompletionFile(asset);
      const selectedPath = selectedFile?.path || "";
      const cacheKey = stage4CompletionFileCacheKey(asset, selectedPath);
      const cached = state.stage4CompletionFileCache[cacheKey];
      let preview = `<div class="stage2-empty">正在加载 ${escapeHtml(selectedFile?.path || "JSON 文件")}...</div>`;
      if (cached?.error) {
        preview = `<div class="stage2-empty">读取失败：${escapeHtml(cached.error)}</div>`;
      } else if (cached?.payload) {
        if (cached.payload.kind === "json") {
          preview = `<pre>${escapeHtml(JSON.stringify(cached.payload.value, null, 2))}</pre>`;
        } else {
          preview = `<pre>${escapeHtml(cached.payload.text || "")}</pre>`;
        }
      }
      return `
        <div class="stage2-completion-browser">
          <div class="stage2-completion-file-list">
            ${files.map((file) => {
              const path = String(file.path || "");
              const fileName = path.split("/").filter(Boolean).pop() || path;
              return `
                <button
                  class="stage2-completion-file-item${path === selectedPath ? " active" : ""}"
                  type="button"
                  data-stage4-completion-file-path="${escapeHtml(path)}"
                >
                  <div class="stage2-completion-file-name">${escapeHtml(fileName)}</div>
                  <div class="stage2-completion-file-meta">${escapeHtml(formatBytes(file.size_bytes))} · ${escapeHtml(formatDate(file.modified_at))}</div>
                </button>
              `;
            }).join("")}
          </div>
          <div class="stage2-completion-preview">
            ${preview}
          </div>
        </div>
      `;
    }

    function renderStage4SourceAsset(run) {
      return `
        <div class="stage2-summary-stack">
          ${renderStage4SourceSummary(run?.source || {})}
        </div>
      `;
    }

    function stage4PublicVariantPayload(variant) {
      if (!variant || typeof variant !== "object") {
        return null;
      }
      const payload = JSON.parse(JSON.stringify(variant));
      delete payload.issue_json;
      const stripFields = (value) => {
        if (Array.isArray(value)) {
          value.forEach(stripFields);
          return;
        }
        if (!value || typeof value !== "object") {
          return;
        }
        delete value.submission_mode;
        delete value.public_output_boundary;
        Object.values(value).forEach(stripFields);
      };
      stripFields(payload);
      return payload;
    }

    function stage4AssetCopyText(run, asset) {
      if (!asset?.available) return "";
      if (asset.kind === "issues") {
        return stage4VariantMarkdownText(selectedStage4Issue(run));
      }
      if (asset.kind === "receive_feedback") {
        const selected = selectedStage4ReceiveFeedback(run);
        if (!selected) {
          return "";
        }
        return JSON.stringify(selected.raw_json || {}, null, 2);
      }
      if (asset.kind === "gold_patch") {
        return String(asset.value || "");
      }
      if (asset.kind === "completion_archive") {
        const selectedFile = selectedStage4CompletionFile(asset);
        if (!selectedFile?.path) {
          return "";
        }
        const cacheKey = stage4CompletionFileCacheKey(asset, selectedFile.path);
        const cached = state.stage4CompletionFileCache[cacheKey];
        if (!cached?.payload) {
          return "";
        }
        if (cached.payload.kind === "json") {
          return JSON.stringify(cached.payload.value, null, 2);
        }
        return String(cached.payload.text || "");
      }
      return "";
    }

    function renderStage4AssetCopyAction(run, asset) {
      if (!stage4AssetCopyText(run, asset)) {
        return "";
      }
      return `<button class="secondary tiny" type="button" data-stage4-copy-current-asset>复制</button>`;
    }

    function renderStage4AssetDownloadAction(asset) {
      return renderStage2AssetDownloadAction(asset);
    }

    async function ensureStage4CompletionFileLoaded(asset) {
      if (!asset || asset.kind !== "completion_archive" || !asset.available) {
        return;
      }
      const selectedFile = selectedStage4CompletionFile(asset);
      if (!selectedFile?.path || !asset.runId || !asset.archiveRole) {
        return;
      }
      const cacheKey = stage4CompletionFileCacheKey(asset, selectedFile.path);
      const cached = state.stage4CompletionFileCache[cacheKey];
      if (cached?.loading || cached?.payload) {
        return;
      }
      state.stage4CompletionFileCache[cacheKey] = { loading: true };
      try {
        const params = new URLSearchParams({ path: selectedFile.path });
        const payload = await api(
          `/api/stage4/runs/${encodeURIComponent(asset.runId)}/llm-completions/${encodeURIComponent(asset.archiveRole)}/files?${params.toString()}`,
        );
        state.stage4CompletionFileCache[cacheKey] = { payload };
      } catch (error) {
        state.stage4CompletionFileCache[cacheKey] = { error: error.message || String(error) };
      }
      const run = currentStage4RunDetail();
      const currentAsset = run ? currentStage4Asset(run) : null;
      const currentFile = currentAsset ? selectedStage4CompletionFile(currentAsset) : null;
      if (
        currentAsset?.kind === "completion_archive"
        && stage4CompletionFileCacheKey(currentAsset, currentFile?.path || "") === cacheKey
      ) {
        rerenderStage4RunDetailPreservingScroll(run);
      }
    }

    function ensureSelectedStage4CompletionFileLoaded() {
      const run = currentStage4RunDetail();
      if (!run) {
        return;
      }
      ensureStage4CompletionFileLoaded(currentStage4Asset(run));
    }

    function renderStage4AssetPreview(run, asset) {
      if (!asset?.available && asset?.kind !== "events") {
        return `<div class="stage2-empty">${escapeHtml(asset?.emptyText || "当前没有可预览内容。")}</div>`;
      }
      if (asset.kind === "source") {
        return renderStage4SourceAsset(run);
      }
      if (asset.kind === "issues") {
        return renderStage4IssueAsset(run);
      }
      if (asset.kind === "receive_feedback") {
        return renderStage4ReceiveFeedbackAsset(run);
      }
      if (asset.kind === "gold_patch") {
        return renderStage4GoldPatchAsset(asset);
      }
      if (asset.kind === "completion_archive") {
        return renderStage4CompletionArchivePreview(asset);
      }
      return `<div class="stage2-empty">当前没有可预览内容。</div>`;
    }

    function renderStage4AssetBrowser(run) {
      const assets = buildStage4RunAssets(run);
      const asset = currentStage4Asset(run);
      const availableCount = assets.filter((item) => item.available).length;
      if (!asset) {
        return `<div class="stage2-empty">当前没有输出资产。</div>`;
      }
      return `
        <div class="stage2-asset-browser stage4-asset-browser">
          <div class="stage2-asset-panel">
            <div class="stage2-asset-panel-head">
              <h5>输出资产</h5>
              <div class="stage2-asset-panel-note">${assets.length} 个资产 · ${availableCount} 个已生成</div>
            </div>
            <div class="stage2-asset-list">
              ${assets.map((item) => `
                <button
                  class="stage2-asset-item${item.key === asset.key ? " active" : ""}"
                  type="button"
                  data-stage4-asset-key="${escapeHtml(item.key)}"
                >
                  <div class="stage2-asset-item-title">${escapeHtml(item.title)}</div>
                  <div class="stage2-asset-item-meta">${escapeHtml(item.meta)}</div>
                </button>
              `).join("")}
            </div>
          </div>
          <div class="stage2-asset-panel">
            <div class="stage2-asset-panel-head">
              <div class="stage2-asset-preview-main">
                <div class="stage2-asset-preview-title">${escapeHtml(asset.title)}</div>
                ${renderStage4AssetVersions(run, asset)}
                ${renderStage4AssetCopyAction(run, asset)}
                ${renderStage4AssetDownloadAction(asset)}
              </div>
              <div class="stage2-asset-panel-note">${escapeHtml(asset.meta)}</div>
            </div>
            <div class="stage2-asset-preview-body">
              ${renderStage4AssetPreview(run, asset)}
            </div>
          </div>
        </div>
      `;
    }

    function updateStage4RunCaches(run) {
      if (!run?.id) {
        return;
      }
      const normalizedRunId = String(run.id);
      const runs = Array.isArray(state.stage4Runs) ? [...state.stage4Runs] : [];
      const existingIndex = runs.findIndex((candidate) => String(candidate?.id || "") === normalizedRunId);
      if (existingIndex >= 0) {
        runs[existingIndex] = { ...runs[existingIndex], ...run };
      } else {
        runs.unshift(run);
      }
      state.stage4Runs = runs;
      if (state.stage4RunDetailPayload && String(state.stage4RunDetailPayload.id || "") === normalizedRunId) {
        state.stage4RunDetailPayload = {
          ...state.stage4RunDetailPayload,
          ...run,
          source: run.source || state.stage4RunDetailPayload.source,
          run_history: Array.isArray(run.run_history) ? run.run_history : state.stage4RunDetailPayload.run_history,
          runtime_snapshot: run.runtime_snapshot || state.stage4RunDetailPayload.runtime_snapshot,
          workspace_manifest: run.workspace_manifest || state.stage4RunDetailPayload.workspace_manifest,
          llm_completion_archives: run.llm_completion_archives || state.stage4RunDetailPayload.llm_completion_archives,
          issue_variants: Array.isArray(run.issue_variants) ? run.issue_variants : state.stage4RunDetailPayload.issue_variants,
          events: Array.isArray(run.events) ? run.events : state.stage4RunDetailPayload.events,
        };
      }

      const sourceSavepointId = String(run.source_savepoint_id || "");
      if (!sourceSavepointId) {
        return;
      }
      const runSummary = {
        id: run.id,
        source_savepoint_id: run.source_savepoint_id,
        status: run.status,
        result: run.result,
        phase: run.phase,
        issue_variant_count: run.issue_variant_count,
        summary: run.summary,
        error_message: run.error_message,
        created_at: run.created_at,
        updated_at: run.updated_at,
        started_at: run.started_at,
        finished_at: run.finished_at,
        duration_seconds: run.duration_seconds,
        is_active: run.is_active,
        can_interrupt: run.can_interrupt,
        can_delete: run.can_delete,
      };
      const sourceGroupKey = run.source ? stage4SourceGroupKey(run.source) : "";
      let nextGroupSummary = null;
      if (state.stage4SourceDetailPayload && Array.isArray(state.stage4SourceDetailPayload.sources)) {
        let touched = false;
        const nextSources = state.stage4SourceDetailPayload.sources.map((source) => {
          const { savepoint } = stage4SourceRowSummary(source);
          if (String(savepoint.id || "") !== sourceSavepointId) {
            return source;
          }
          touched = true;
          const latestRun = savepoint.latest_stage4_run || {};
          const nextProducedIssueCount = (
            run.status === "completed" && run.result === "generated"
          )
            ? Number(run.issue_variant_count || 0)
            : Number(savepoint.produced_issue_variant_count || 0);
          return {
            ...source,
            savepoint: {
              ...savepoint,
              produced_issue_variant_count: nextProducedIssueCount,
              latest_stage4_run: {
                ...latestRun,
                ...runSummary,
              },
            },
          };
        });
        if (touched) {
          const currentGroup = state.stage4SourceDetailPayload.group || {};
          nextGroupSummary = {
            key: String(currentGroup.key || sourceGroupKey || ""),
            latest_operation_at: run.updated_at || currentGroup.latest_operation_at,
            status: stage4GroupStatus({ ...currentGroup, sources: nextSources }),
          };
          state.stage4SourceDetailPayload = {
            ...state.stage4SourceDetailPayload,
            group: {
              ...currentGroup,
              latest_operation_at: nextGroupSummary.latest_operation_at,
              status: nextGroupSummary.status,
            },
            sources: nextSources,
          };
        }
      }
      const liveGroupStatus = run.status === "running"
        ? "running"
        : run.status === "queued"
          ? "queued"
          : "";
      state.stage4SourceGroups = (Array.isArray(state.stage4SourceGroups) ? state.stage4SourceGroups : []).map((group) => {
        const normalizedGroupKey = String(group?.key || "");
        const targetGroupKey = String(nextGroupSummary?.key || sourceGroupKey || "");
        if (!normalizedGroupKey || !targetGroupKey || normalizedGroupKey !== targetGroupKey) {
          return group;
        }
        return {
          ...group,
          latest_operation_at: nextGroupSummary?.latest_operation_at || run.updated_at || group.latest_operation_at,
          status: nextGroupSummary?.status || liveGroupStatus || group.status,
        };
      });
    }

    function removeStage4RunFromCaches(runId) {
      const normalizedRunId = String(runId || "");
      if (!normalizedRunId) {
        return;
      }
      state.stage4Runs = (Array.isArray(state.stage4Runs) ? state.stage4Runs : [])
        .filter((run) => String(run?.id || "") !== normalizedRunId);
      if (state.stage4RunDetailPayload && String(state.stage4RunDetailPayload.id || "") === normalizedRunId) {
        state.stage4RunDetailPayload = null;
      }
    }

    function currentStage4RunDetail() {
      const selectedRunId = String(state.selectedStage4RunId || "");
      if (!selectedRunId) {
        return null;
      }
      if (state.stage4RunDetailPayload && String(state.stage4RunDetailPayload.id || "") === selectedRunId) {
        return state.stage4RunDetailPayload;
      }
      return (Array.isArray(state.stage4Runs) ? state.stage4Runs : [])
        .find((run) => String(run?.id || "") === selectedRunId) || null;
    }

    async function rerenderCurrentStage4RunDetailOrLoad() {
      const run = currentStage4RunDetail();
      if (run) {
        if (!patchStage4RunDetail(run)) {
          rerenderStage4RunDetailPreservingScroll(run);
        }
        return run;
      }
      return loadStage4RunDetail(state.selectedStage4RunId);
    }

    function renderStage4RunError(run) {
      return run?.error_message
        ? `<div class="stage2-section"><h5>错误信息</h5><pre>${escapeHtml(run.error_message)}</pre></div>`
        : "";
    }

    function captureStage4DetailScrollState() {
      const detailBody = $("#stage4-detail-body");
      const eventShell = detailBody?.querySelector("[data-stage4-events-slot] .stage2-event-shell");
      const historyStrip = detailBody?.querySelector(".stage2-history-strip-wrap");
      const eventBottomGap = eventShell
        ? eventShell.scrollHeight - eventShell.clientHeight - eventShell.scrollTop
        : null;
      return {
        windowScrollX: window.scrollX,
        windowScrollY: window.scrollY,
        detailScrollTop: detailBody?.scrollTop ?? 0,
        detailScrollLeft: detailBody?.scrollLeft ?? 0,
        eventScrollTop: eventShell?.scrollTop ?? 0,
        eventScrollLeft: eventShell?.scrollLeft ?? 0,
        eventWasNearBottom: eventBottomGap != null && eventBottomGap <= 24,
        historyScrollLeft: historyStrip?.scrollLeft ?? 0,
        ...captureStage2AssetBrowserScrollState(detailBody),
      };
    }

    function restoreStage4DetailScrollState(scrollState) {
      if (!scrollState) {
        return;
      }
      requestAnimationFrame(() => {
        const detailBody = $("#stage4-detail-body");
        const eventShell = detailBody?.querySelector("[data-stage4-events-slot] .stage2-event-shell");
        const historyStrip = detailBody?.querySelector(".stage2-history-strip-wrap");
        if (detailBody) {
          detailBody.scrollTop = scrollState.detailScrollTop;
          detailBody.scrollLeft = scrollState.detailScrollLeft;
        }
        if (eventShell) {
          eventShell.scrollTop = scrollState.eventWasNearBottom
            ? eventShell.scrollHeight
            : scrollState.eventScrollTop;
          eventShell.scrollLeft = scrollState.eventScrollLeft;
        }
        if (historyStrip) {
          historyStrip.scrollLeft = scrollState.historyScrollLeft;
        }
        restoreStage2AssetBrowserScrollState(scrollState, detailBody);
        window.scrollTo(scrollState.windowScrollX, scrollState.windowScrollY);
      });
    }

    function syncStage4EventShell(events) {
      const detailBody = $("#stage4-detail-body");
      const eventsSlot = detailBody?.querySelector("[data-stage4-events-slot]");
      const eventOptions = { phaseLabel: stage4PhaseLabel };
      if (!eventsSlot) {
        return false;
      }
      if (!events || events.length === 0) {
        eventsSlot.innerHTML = renderStage2Events(events, eventOptions);
        return true;
      }
      let eventShell = eventsSlot.querySelector(".stage2-event-shell");
      let eventList = eventsSlot.querySelector(".stage2-event-list");
      if (!eventShell || !eventList) {
        eventsSlot.innerHTML = renderStage2Events(events, eventOptions);
        return true;
      }

      const wasNearBottom = eventShell.scrollHeight - eventShell.clientHeight - eventShell.scrollTop <= 24;
      const previousScrollTop = eventShell.scrollTop;
      const previousScrollLeft = eventShell.scrollLeft;
      const displayEvents = displayStage2Events(events);
      const existingByKey = new Map();
      eventList.querySelectorAll("[data-stage2-event-key]").forEach((element) => {
        existingByKey.set(element.dataset.stage2EventKey, element);
      });
      const nextKeys = new Set();

      displayEvents.forEach((event, index) => {
        const eventKey = stage2EventKey(event, index);
        const signature = stage2EventSignature(event);
        nextKeys.add(eventKey);
        const existing = existingByKey.get(eventKey);
        if (existing) {
          if (existing.dataset.stage2EventSignature !== signature) {
            const replacement = htmlToElement(renderStage2EventItem(event, index, eventOptions));
            replacement.dataset.stage2EventSignature = signature;
            existing.replaceWith(replacement);
          }
          return;
        }
        const element = htmlToElement(renderStage2EventItem(event, index, eventOptions));
        element.dataset.stage2EventSignature = signature;
        eventList.appendChild(element);
      });

      eventList.querySelectorAll("[data-stage2-event-key]").forEach((element) => {
        if (!nextKeys.has(element.dataset.stage2EventKey)) {
          element.remove();
        }
      });
      eventShell.scrollTop = wasNearBottom ? eventShell.scrollHeight : previousScrollTop;
      eventShell.scrollLeft = previousScrollLeft;
      return true;
    }

    function patchStage4RunDetail(run) {
      if (!run || String(run.id || "") !== String(state.selectedStage4RunId || "")) {
        return false;
      }
      if (!state.stage4List.detailVisible || state.stage4List.detailMode !== "run") {
        return false;
      }
      const runPanel = $("#stage4-detail-body")?.querySelector(`[data-stage4-run-panel-id="${CSS.escape(String(run.id || ""))}"]`);
      if (!runPanel) {
        return false;
      }
      const scrollState = captureStage4DetailScrollState();
      updateStage4RunCaches(run);

      const historySlot = $("#stage4-detail-body")?.querySelector("[data-stage4-history-slot]");
      if (historySlot) {
        const nextHistoryHtml = renderStage4RunHistory(run);
        if (replaceInnerHtmlIfChanged(historySlot, nextHistoryHtml)) {
          bindStage4RunDetailHandlers(historySlot);
        }
      }

      const summarySlot = runPanel.querySelector("[data-stage4-summary-slot]");
      if (summarySlot && !isEditingStage4RunRuntime(run.id)) {
        const nextSummaryHtml = renderStage4RunSummaryStack(run);
        if (replaceInnerHtmlIfChanged(summarySlot, nextSummaryHtml)) {
          bindStage4RunDetailHandlers(summarySlot);
        }
      }

      const assetsSlot = runPanel.querySelector("[data-stage4-assets-slot]");
      if (assetsSlot) {
        const nextAssetsHtml = renderStage4AssetBrowser(run);
        if (replaceInnerHtmlIfChanged(assetsSlot, nextAssetsHtml)) {
          bindStage4RunDetailHandlers(assetsSlot);
        }
      }

      const errorSlot = runPanel.querySelector("[data-stage4-error-slot]");
      if (errorSlot) {
        replaceInnerHtmlIfChanged(errorSlot, renderStage4RunError(run));
      }

      setStage4RunDetailHeader(run);
      const synced = syncStage4EventShell(run.events || []);
      renderStage4Sources();
      restoreStage4DetailScrollState(scrollState);
      applyPendingStage4RunOpenDefaults();
      return synced;
    }

    function rerenderStage4RunDetailPreservingScroll(run) {
      const scrollState = captureStage4DetailScrollState();
      renderStage4RunDetail(run);
      restoreStage4DetailScrollState(scrollState);
    }

    function renderStage4RunDetail(run) {
      const body = $("#stage4-detail-body");
      if (!run) {
        hideStage4Detail();
        return;
      }
      const previousRunId = String(state.selectedStage4RunId || "");
      const nextRunId = String(run.id || "");
      if (previousRunId !== nextRunId) {
        resetStage4AssetBrowserState();
        state.pendingStage4RunOpenDefaults = true;
      }
      state.stage4RunDetailPayload = run;
      updateStage4RunCaches(run);
      state.selectedStage4RunId = run.id;
      state.selectedStage4SourceSavepointId = String(run.source_savepoint_id || "");
      const runSourceGroupKey = run.source ? stage4SourceGroupKey(run.source) : "";
      const runSource = run.source || {};
      if (runSourceGroupKey) {
        state.selectedStage4SourceGroupKey = runSourceGroupKey;
      }
      showStage4Detail("run");
      setStage4RunDetailHeader(run);
      const runs = stage4RunsForDisplay(run);
      const runNumberById = buildStage4RunNumberById(runs);
      const selectedRunNumber = runNumberById.get(String(run.id || "")) || 1;
      body.innerHTML = `
        <div class="stage2-run-stack">
          <section class="detail-card detail-section">
            <div class="section-actions">
              <h4>运行历史</h4>
            </div>
            <div data-stage4-history-slot>
              ${renderStage4RunHistory(run)}
            </div>
          </section>
          <section class="detail-card detail-section stage2-run" data-stage4-run-panel-id="${escapeHtml(String(run.id || ""))}">
            <div class="stage2-run-header">
              <div>
                <h4>运行 ${escapeHtml(String(selectedRunNumber))}</h4>
              </div>
              <div class="button-row">
                <div class="muted mono">${escapeHtml(String(run.id || ""))}</div>
              </div>
            </div>
            <div data-stage4-summary-slot>
              ${renderStage4RunSummaryStack(run)}
            </div>
            <div class="stage2-section" data-stage4-assets-slot>
              ${renderStage4AssetBrowser(run)}
            </div>
            <div class="stage2-section">
              <h5>运行轨迹</h5>
              <div data-stage4-events-slot>
                ${renderStage2Events(run.events || [], { phaseLabel: stage4PhaseLabel })}
              </div>
            </div>
            <div data-stage4-error-slot>
              ${renderStage4RunError(run)}
            </div>
          </section>
        </div>
      `;
      bindStage4RunDetailHandlers(body);
      renderStage4Sources();
      syncStage4RunDetailStream(run);
      applyPendingStage4RunOpenDefaults();
    }

    function bindStage4RunDetailHandlers(root) {
      root.querySelectorAll("[data-stage4-detail-section]").forEach((section) => {
        if (section.dataset.stage4DetailSectionBound === "1") {
          return;
        }
        section.dataset.stage4DetailSectionBound = "1";
        section.addEventListener("toggle", () => {
          state.stage4DetailSections[section.dataset.stage4DetailSection] = section.open;
        });
      });
      root.querySelectorAll("[data-stage4-run-id]").forEach((card) => {
        if (card.dataset.stage4Bound === "1") {
          return;
        }
        card.dataset.stage4Bound = "1";
        card.addEventListener("keydown", async (event) => {
          if (
            event.target.closest("[data-stage4-delete-run-id]")
            || event.target.closest("[data-stage4-interrupt-run-id]")
            || event.target.closest("[data-stage4-rerun-source-savepoint-id]")
          ) {
            return;
          }
          if (event.key !== "Enter" && event.key !== " ") {
            return;
          }
          event.preventDefault();
          await loadStage4RunDetail(card.dataset.stage4RunId);
        });
      });
      ensureSelectedStage4CompletionFileLoaded();
    }

    async function loadStage4RunDetail(runId) {
      const normalizedRunId = String(runId || "").trim();
      if (!normalizedRunId) {
        renderStage4RunDetail(null);
        return null;
      }
      state.selectedStage4RunId = normalizedRunId;
      showStage4Detail("run");
      setStage4DetailHeader({
        title: "产出数据详细",
        backLabel: state.selectedStage4SourceGroupKey ? "返回产出数据" : "返回列表",
        subtitle: "正在加载...",
      });
      try {
        const payload = await api(`/api/stage4/runs/${encodeURIComponent(normalizedRunId)}`);
        if (
          state.activeTab !== "stage4"
          || !state.stage4List.detailVisible
          || state.stage4List.detailMode !== "run"
          || String(state.selectedStage4RunId || "") !== normalizedRunId
        ) {
          return payload;
        }
        renderStage4RunDetail(payload);
        syncStage4RunDetailStream(payload);
        return payload;
      } catch (error) {
        if (
          state.activeTab !== "stage4"
          || !state.stage4List.detailVisible
          || state.stage4List.detailMode !== "run"
          || String(state.selectedStage4RunId || "") !== normalizedRunId
        ) {
          return null;
        }
        setStage4DetailHeader({
          title: "产出数据详细",
          backLabel: state.selectedStage4SourceGroupKey ? "返回产出数据" : "返回列表",
          subtitle: `加载失败: ${error.message}`,
        });
        closeStage4RunDetailStream();
        return null;
      }
    }

    function applyLiveStage4RunDetailPayload(payload) {
      if (!payload || String(payload.id || "") !== String(state.selectedStage4RunId || "")) {
        return;
      }
      if (state.activeTab !== "stage4" || !state.stage4List.detailVisible || state.stage4List.detailMode !== "run") {
        pendingStage4RunDetailPayload = null;
        return;
      }
      if (hasActiveTextSelection()) {
        pendingStage4RunDetailPayload = payload;
        return;
      }
      pendingStage4RunDetailPayload = null;
      if (!patchStage4RunDetail(payload)) {
        rerenderStage4RunDetailPreservingScroll(payload);
      }
      setRefreshMeta();
    }

    function syncStage4RunDetailStream(currentRun = null) {
      const selectedRunId = state.selectedStage4RunId ? String(state.selectedStage4RunId) : null;
      if (
        state.activeTab !== "stage4"
        || !selectedRunId
        || !state.stage4List.detailVisible
        || state.stage4List.detailMode !== "run"
        || typeof EventSource === "undefined"
      ) {
        closeStage4RunDetailStream();
        return;
      }
      const selectedRun = currentRun || currentStage4RunDetail();
      if (!isStage4RunRealtimeEligible(selectedRun)) {
        closeStage4RunDetailStream();
        return;
      }
      if (
        stage4RunDetailEventSource
        && String(stage4RunDetailStreamRunId || "") === selectedRunId
      ) {
        return;
      }
      closeStage4RunDetailStream();
      const streamRunId = selectedRunId;
      const source = new EventSource(`/api/stage4/runs/${encodeURIComponent(streamRunId)}/events`);
      stage4RunDetailEventSource = source;
      stage4RunDetailStreamRunId = streamRunId;

      const isCurrentStream = () => (
        stage4RunDetailEventSource === source
        && String(stage4RunDetailStreamRunId || "") === streamRunId
      );
      const handlePayload = (payload, { terminal = false } = {}) => {
        if (!isCurrentStream()) {
          return;
        }
        updateStage4RunCaches(payload);
        applyLiveStage4RunDetailPayload(payload);
        if (terminal) {
          closeStage4RunDetailStream();
          loadStage4Sources();
        }
      };
      source.addEventListener("snapshot", (event) => {
        handlePayload(JSON.parse(event.data));
      });
      source.addEventListener("terminal", (event) => {
        handlePayload(JSON.parse(event.data), { terminal: true });
      });
      source.addEventListener("deleted", async () => {
        if (!isCurrentStream()) {
          return;
        }
        if (String(state.selectedStage4RunId || "") === streamRunId) {
          state.selectedStage4RunId = null;
          if (state.selectedStage4SourceGroupKey) {
            await loadStage4SourceDetailByKey(state.selectedStage4SourceGroupKey);
          } else {
            hideStage4Detail();
          }
        } else {
          closeStage4RunDetailStream();
        }
      });
      source.onerror = () => {
        if (!isCurrentStream()) {
          return;
        }
        closeStage4RunDetailStream();
      };
    }

    async function rerunStage4FromSourceSavepoint(sourceSavepointId) {
      const normalizedId = String(sourceSavepointId || "").trim();
      if (!normalizedId) {
        return null;
      }
      return createStage4Run(normalizedId);
    }

    async function runAllStage4GroupSavepoints(groupKey) {
      const normalizedGroupKey = String(groupKey || "").trim();
      if (!normalizedGroupKey || isStage4GroupRunInFlight(normalizedGroupKey)) {
        return;
      }
      let detailPayload = null;
      try {
        detailPayload = await fetchStage4SourceDetailPayloadByKey(normalizedGroupKey, { includeFilters: false });
      } catch (error) {
        alert(`加载 repo 详细失败: ${error.message}`);
        return;
      }
      if (!detailPayload?.group) {
        alert("当前 repo / commit 分组不存在。");
        return;
      }
      if (!window.confirm("确认启动该 repo 当前 commit 下的全部 entry file / depth 数据吗？")) {
        return;
      }

      setStage4GroupRunInFlight(normalizedGroupKey, true);
      renderStage4Sources();
      try {
        let createdCount = 0;
        let startedCount = 0;
        let skippedActiveCount = 0;
        let skippedPendingCount = 0;
        let failedCount = 0;
        const failedExamples = [];

        for (const source of detailPayload.sources || []) {
          const { entryFile, savepoint } = stage4SourceRowSummary(source);
          const savepointId = String(savepoint?.id || "").trim();
          const entryPath = String(entryFile?.test_file_path || "").trim() || "(unknown)";
          const latestRun = savepoint?.latest_stage4_run || null;
          const latestRunStatus = String(latestRun?.status || "");
          const latestRunId = String(latestRun?.id || "").trim();
          if (!savepointId) {
            failedCount += 1;
            failedExamples.push(`${entryPath} / depth ${savepoint?.depth ?? "-"}: missing savepoint id`);
            continue;
          }
          if (latestRunStatus === "queued" || latestRunStatus === "running") {
            skippedActiveCount += 1;
            continue;
          }
          if (latestRunId && latestRunStatus === "pending") {
            skippedPendingCount += 1;
            continue;
          }
          try {
            await api("/api/stage4/runs", {
              method: "POST",
              body: JSON.stringify({
                source_savepoint_id: Number(savepointId),
              }),
            });
            createdCount += 1;
            startedCount += 1;
          } catch (error) {
            failedCount += 1;
            failedExamples.push(`${entryPath} / depth ${savepoint?.depth ?? "-"}: ${error.message}`);
          }
        }

        await loadStage4Sources();
        if (state.stage4List.detailMode === "source" && state.selectedStage4SourceGroupKey === normalizedGroupKey) {
          await loadStage4SourceDetailByKey(normalizedGroupKey);
        }
        setRefreshMeta();

        const summaryParts = [];
        if (startedCount > 0) {
          summaryParts.push(`已启动 ${startedCount} 条数据`);
        }
        if (createdCount > 0) {
          summaryParts.push(`其中新建 ${createdCount} 个 run`);
        }
        if (skippedActiveCount > 0) {
          summaryParts.push(`跳过 ${skippedActiveCount} 条已在排队/运行中的数据`);
        }
        if (skippedPendingCount > 0) {
          summaryParts.push(`跳过 ${skippedPendingCount} 条旧的未启动 run，请删除后重新生成`);
        }
        if (failedCount > 0) {
          summaryParts.push(`失败 ${failedCount} 条数据`);
        }
        const failureDetail = failedExamples.length > 0
          ? `\n\n失败样例：\n${failedExamples.slice(0, 3).join("\n")}`
          : "";
        alert(`Stage4 批量启动完成：${summaryParts.join("，") || "没有可执行变更"}${failureDetail}`);
      } catch (error) {
        alert(`批量启动 Stage4 数据失败: ${error.message}`);
      } finally {
        setStage4GroupRunInFlight(normalizedGroupKey, false);
        renderStage4Sources();
      }
    }

    async function createStage4Run(sourceSavepointId) {
      const normalizedId = String(sourceSavepointId || "").trim();
      if (!normalizedId) {
        return null;
      }
      state.selectedStage4SourceSavepointId = normalizedId;
      try {
        const payload = await api("/api/stage4/runs", {
          method: "POST",
          body: JSON.stringify({
            source_savepoint_id: Number(normalizedId),
          }),
        });
        renderStage4RunDetail(payload);
        await loadStage4Sources();
        renderStage4RunDetail(payload);
        setRefreshMeta();
        return payload;
      } catch (error) {
        alert(`创建 Stage4 run 失败: ${error.message}`);
        return null;
      }
    }

    async function interruptStage4Run(runId) {
      if (!window.confirm("确认中断这次 Stage4 run 吗？")) {
        return null;
      }
      try {
        const payload = await api(`/api/stage4/runs/${encodeURIComponent(runId)}/interrupt`, { method: "POST" });
        renderStage4RunDetail(payload);
        await loadStage4Sources();
        renderStage4RunDetail(payload);
        return payload;
      } catch (error) {
        alert(`中断 Stage4 run 失败: ${error.message}`);
        return null;
      }
    }

    async function deleteStage4Run(runId) {
      if (!window.confirm("确认删除这次 Stage4 run 吗？")) {
        return null;
      }
      const sourceGroupKey = state.selectedStage4SourceGroupKey;
      try {
        const payload = await api(`/api/stage4/runs/${encodeURIComponent(runId)}`, { method: "DELETE" });
        removeStage4RunFromCaches(runId);
        await loadStage4Sources();
        if (String(state.selectedStage4RunId || "") === String(runId || "")) {
          state.selectedStage4RunId = null;
          if (sourceGroupKey) {
            await loadStage4SourceDetailByKey(sourceGroupKey);
          } else {
            hideStage4Detail();
        }
        } else if (state.stage4List.detailMode === "source" && sourceGroupKey) {
          await loadStage4SourceDetailByKey(sourceGroupKey);
        }
        if (payload.cleanup_warnings) {
          alert(`Stage4 run 已删除，但部分资产清理失败，将在后续启动时重试清理：\n${formatApiErrorPayload(payload.cleanup_warnings)}`);
        }
        return payload;
      } catch (error) {
        alert(`删除 Stage4 run 失败: ${error.message}`);
        return null;
      }
    }

    async function copyStage4VariantMarkdown(runId, variantIndex) {
      const run = String(runId || "") === String(state.selectedStage4RunId || "")
        ? (currentStage4RunDetail() || await loadStage4RunDetail(runId))
        : await loadStage4RunDetail(runId);
      if (!run) {
        return;
      }
      const variants = run.issue_variants || [];
      const variant = variants.find((item) => Number(item.variant_index) === Number(variantIndex));
      const text = variant?.issue_markdown;
      if (!text) {
        return;
      }
      try {
        await copyTextToClipboard(text);
      } catch (error) {
        alert(`复制失败: ${error.message || String(error)}`);
      }
    }

    async function copyCurrentStage4Asset(button) {
      const run = currentStage4RunDetail() || await loadStage4RunDetail(state.selectedStage4RunId);
      const asset = run ? currentStage4Asset(run) : null;
      const text = stage4AssetCopyText(run, asset);
      if (!text) return;
      const originalText = button.textContent;
      try {
        await copyTextToClipboard(text);
        button.textContent = "已复制";
        window.setTimeout(() => {
          if (button.isConnected) {
            button.textContent = originalText || "复制";
          }
        }, 1200);
      } catch (error) {
        alert(`复制失败: ${error.message || String(error)}`);
      }
    }

    async function refreshBatchView() {
      const [dataPoolsResult, tasksResult] = await Promise.allSettled([
        loadDataPools(),
        loadBatchTasks(),
      ]);
      if (tasksResult.status === "rejected") {
        renderPanelLoadError("#batch-tasks-body", 6, tasksResult.reason);
      }
      if (dataPoolsResult.status === "rejected") {
        const message = $("#batch-form-message");
        if (message) message.textContent = `数据池加载失败: ${dataPoolsResult.reason?.message || String(dataPoolsResult.reason)}`;
      }
      if (state.selectedBatchTaskId && !batchTaskDetailEventSource) {
        await loadBatchTaskDetail(state.selectedBatchTaskId);
      }
    }

    async function refreshStage1View() {
      const [jobsResult, reposResult] = await Promise.allSettled([
        loadJobs(),
        loadRepos(),
      ]);
      if (jobsResult.status === "rejected") {
        $("#jobs-body").innerHTML = `<tr><td colspan="6" class="muted">加载失败：${escapeHtml(jobsResult.reason?.message || String(jobsResult.reason))}</td></tr>`;
      }
      if (reposResult.status === "rejected") {
        renderPanelLoadError("#repos-body", 6, reposResult.reason);
      }
      const stage1StreamActive = (
        stage1JobDetailEventSource
        && String(stage1JobDetailStreamJobId) === String(state.selectedJobId)
      );
      if (state.selectedJobId) {
        if (!stage1StreamActive) {
          await loadJobDetail(state.selectedJobId);
        }
      } else {
        hideJobDetail();
      }
    }

    async function refreshStage2View() {
      const [stage2ReposResult] = await Promise.allSettled([loadStage2Repos()]);
      if (stage2ReposResult.status === "rejected") {
        renderPanelLoadError("#stage2-repos-body", 9, stage2ReposResult.reason);
      }
      const stage2StreamActive = (
        stage2RepositoryDetailEventSource
        && String(stage2RepositoryDetailStreamRepositoryId) === String(state.selectedStage2RepositoryId)
        && String(stage2RepositoryDetailStreamRunId || "") === String(state.selectedStage2RunId || "")
      );
      const selectedStage2Repository = state.selectedStage2RepositoryId
        ? (
          state.stage2Repos.find((repo) => String(repo.id) === String(state.selectedStage2RepositoryId))
          || currentStage2RepositoryDetailPayload().repository
        )
        : null;
      const selectedStage2RepositoryBusy = isStage2RepositoryRealtimeEligible(selectedStage2Repository);
      if (state.selectedStage2RepositoryId && state.stage2List.detailVisible) {
        if (!stage2StreamActive && selectedStage2RepositoryBusy) {
          await loadStage2RepositoryDetail(state.selectedStage2RepositoryId);
        }
      } else if (!state.selectedStage2RepositoryId) {
        hideStage2Detail();
      }
    }

    async function refreshStage3View() {
      const [stage3ReposResult] = await Promise.allSettled([loadStage3Repos()]);
      if (stage3ReposResult.status === "rejected") {
        renderPanelLoadError("#stage3-repos-body", 9, stage3ReposResult.reason);
      }
      const stage3StreamActive = (
        stage3RepositoryDetailEventSource
        && String(stage3RepositoryDetailStreamRepositoryId) === String(state.selectedStage3RepositoryId)
        && String(stage3RepositoryDetailStreamSnapshotId || "") === String(state.selectedStage3SnapshotId || "")
        && String(stage3RepositoryDetailStreamEntryFileId || "") === String(state.selectedStage3EntryFileId || "")
        && String(stage3RepositoryDetailStreamRunId || "") === String(state.selectedStage3RunId || "")
      );
      const selectedStage3Repository = state.selectedStage3RepositoryId
        ? (
          state.stage3Repos.find((repo) => String(repo.id) === String(state.selectedStage3RepositoryId))
          || currentStage3RepositoryDetailPayload().repository
        )
        : null;
      const selectedStage3RepositoryBusy = isStage3RepositoryRealtimeEligible(selectedStage3Repository);
      if (state.selectedStage3RepositoryId && state.stage3List.detailVisible) {
        if (!stage3StreamActive && selectedStage3RepositoryBusy) {
          await loadStage3RepositoryDetail(state.selectedStage3RepositoryId);
        }
      } else {
        hideStage3Detail();
      }
    }

    async function refreshStage4View() {
      const [stage4SourcesResult] = await Promise.allSettled([loadStage4Sources()]);
      if (stage4SourcesResult.status === "rejected") {
        renderPanelLoadError("#stage4-sources-body", 8, stage4SourcesResult.reason);
        return;
      }
      const stage4RunStreamActive = (
        stage4RunDetailEventSource
        && String(stage4RunDetailStreamRunId || "") === String(state.selectedStage4RunId || "")
      );
      const sourceDetailPayload = currentStage4SourceDetailPayload();
      const selectedSourceGroup = sourceDetailPayload?.group || null;
      const stage4SourceStreamActive = (
        stage4SourceDetailEventSource
        && String(stage4SourceDetailStreamRepositoryId || "") === String(selectedSourceGroup?.repository?.id || "")
        && String(stage4SourceDetailStreamSnapshotId || "") === String(selectedSourceGroup?.snapshot?.id || "")
      );
      const selectedStage4RunBeforeRefresh = state.selectedStage4RunId
        ? currentStage4RunDetail()
        : null;
      const selectedStage4Run = currentStage4RunDetail() || selectedStage4RunBeforeRefresh;
      const selectedStage4RunBusy = isStage4RunRealtimeEligible(selectedStage4Run);
      if (
        state.selectedStage4RunId
        && state.stage4List.detailVisible
        && state.stage4List.detailMode === "run"
        && !stage4RunStreamActive
        && selectedStage4RunBusy
      ) {
        await loadStage4RunDetail(state.selectedStage4RunId);
      }
      if (
        state.selectedStage4SourceGroupKey
        && state.stage4List.detailVisible
        && state.stage4List.detailMode === "source"
        && !stage4SourceStreamActive
      ) {
        await loadStage4SourceDetailByKey(state.selectedStage4SourceGroupKey);
      }
    }

    async function refreshDataPoolsView() {
      try {
        await loadDataPools();
      } catch (error) {
        const message = $("#data-pool-message");
        if (message) message.textContent = `数据池加载失败: ${error.message || String(error)}`;
        return;
      }
      try {
        await loadDataPoolAssets();
      } catch (error) {
        renderPanelLoadError("#data-pool-assets-body", 11, error);
      }
    }

    async function refreshAll() {
      if (state.activeTab === "batch") {
        await refreshBatchView();
      } else if (state.activeTab === "stage1") {
        await refreshStage1View();
      } else if (state.activeTab === "stage2") {
        await refreshStage2View();
      } else if (state.activeTab === "stage3") {
        await refreshStage3View();
      } else if (state.activeTab === "stage4") {
        await refreshStage4View();
      } else if (state.activeTab === "data-pools") {
        await refreshDataPoolsView();
      }
      flushPendingLiveDetailPayloads();
      setRefreshMeta();
    }

    async function refreshAllIfNeeded() {
      if (refreshInFlight || !shouldAutoRefresh()) {
        return;
      }
      refreshInFlight = true;
      try {
        await refreshAll();
      } finally {
        refreshInFlight = false;
      }
    }

    function readExecutionOptionsFromForm() {
      return {
        max_concurrent_partitions: $("#max-concurrent-partitions").value ? Number($("#max-concurrent-partitions").value) : null,
      };
    }

    $("#crawl-form").addEventListener("input", (event) => {
      if (event.target?.name === "target_repositories") {
        syncCrawlTargetRepositoryMode();
      }
      persistCrawlFormDraft();
    });
    $("#crawl-form").addEventListener("change", (event) => {
      if (event.target?.name === "target_repositories") {
        syncCrawlTargetRepositoryMode();
      }
      persistCrawlFormDraft();
    });
    $("#crawl-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      $("#submit-btn").disabled = true;
      $("#form-message").textContent = "正在创建抓取任务...";
      try {
        await saveRuntimeConfig();
        const executionOptions = readExecutionOptionsFromForm();
        const targetRepositories = newlineValues(form.get("target_repositories"));
        const usesTargetRepositories = targetRepositories.length > 0;
        const payload = {
          name: form.get("name"),
          run_in_background: true,
          ...executionOptions,
          filters: {
            language: usesTargetRepositories
              ? null
              : (state.crawlForm.languages.length === 1 ? state.crawlForm.languages[0] : null),
            languages: usesTargetRepositories ? [] : [...state.crawlForm.languages],
            created_after: usesTargetRepositories ? "1970-01-01T00:00:00Z" : isoFromLocal(form.get("created_after")),
            created_before: usesTargetRepositories ? null : isoFromLocal(form.get("created_before")),
            pushed_after: usesTargetRepositories ? null : isoFromLocal(form.get("pushed_after")),
            pushed_before: usesTargetRepositories ? null : isoFromLocal(form.get("pushed_before")),
            stars_min: usesTargetRepositories ? null : (form.get("stars_min") ? Number(form.get("stars_min")) : null),
            stars_max: usesTargetRepositories ? null : (form.get("stars_max") ? Number(form.get("stars_max")) : null),
            repository_limit: usesTargetRepositories ? null : (form.get("repository_limit") ? Number(form.get("repository_limit")) : null),
            exclude_forks: usesTargetRepositories ? false : !(form.get("include_forks") === "on"),
            exclude_archived: usesTargetRepositories ? false : !(form.get("include_archived") === "on"),
            licenses: usesTargetRepositories ? [] : [...state.crawlForm.licenses],
            keywords: usesTargetRepositories ? [] : String(form.get("keywords") || "").split(",").map((v) => v.trim()).filter(Boolean),
            target_repositories: targetRepositories,
          }
        };
        const response = await api("/api/stage1/jobs", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        $("#form-message").textContent = `抓取任务已创建: ${response.job.id}`;
        await refreshAll();
        await selectJob(response.job.id);
      } catch (error) {
        $("#form-message").textContent = `创建失败: ${error.message}`;
      } finally {
        $("#submit-btn").disabled = false;
      }
    });

    $("#save-runtime-btn").addEventListener("click", async () => {
      await saveRuntimeConfig();
    });
    $("#stage1-template-save")?.addEventListener("click", async () => {
      await saveStage1CrawlTemplate();
    });
    $("#stage2-template-save").addEventListener("click", async () => {
      await saveStage2RuntimeTemplate();
    });
    $("#stage3-template-save").addEventListener("click", async () => {
      await saveStage3RuntimeTemplate();
    });
    $("#stage4-template-save").addEventListener("click", async () => {
      await saveStage4RuntimeTemplate();
    });
    $("#stage2-config-apply").addEventListener("click", async () => {
      await saveStage2RuntimeConfig();
    });
    $("#stage2-config-toggle").addEventListener("click", () => {
      toggleStage2ConfigPanel();
    });
    $("#stage3-config-apply").addEventListener("click", async () => {
      await saveStage3RuntimeConfig();
    });
    $("#stage3-config-toggle").addEventListener("click", () => {
      toggleStage3ConfigPanel();
    });
    $("#stage4-config-apply").addEventListener("click", async () => {
      await saveStage4RuntimeConfig();
    });
    $("#stage4-config-toggle").addEventListener("click", () => {
      toggleStage4ConfigPanel();
    });
    $("#stage1-template-strip")?.addEventListener("click", async (event) => {
      const deleteButton = event.target.closest("[data-stage1-template-delete-id]");
      if (deleteButton) {
        event.stopPropagation();
        await deleteStage1CrawlTemplate(deleteButton.dataset.stage1TemplateDeleteId);
        return;
      }
      const button = event.target.closest("[data-stage1-template-id]");
      if (!button) return;
      await applyStage1CrawlTemplate(button.dataset.stage1TemplateId);
    });
    $("#batch-stage1-template")?.addEventListener("change", async (event) => {
      const templateId = String(event.currentTarget.value || "").trim();
      state.selectedBatchStage1TemplateId = templateId || null;
      syncBatchGithubTokenMode();
      if (!templateId) {
        $("#batch-form-message").textContent = "GitHub Token 使用临时配置";
        return;
      }
      await applyStage1CrawlTemplate(templateId, "batch");
    });
    ["stage2", "stage3", "stage4"].forEach((stage) => {
      $(`#batch-${stage}-template`)?.addEventListener("change", () => {
        syncBatchRuntimeMode(stage);
      });
    });
    $("#batch-layout-collapse-toggle")?.addEventListener("click", () => {
      toggleBatchLayout();
    });
    $("#batch-layout-expand-toggle")?.addEventListener("click", () => {
      toggleBatchLayout();
    });
    $("#stage2-template-strip").addEventListener("click", async (event) => {
      const deleteButton = event.target.closest("[data-stage2-template-delete-id]");
      if (deleteButton) {
        event.stopPropagation();
        await deleteStage2RuntimeTemplate(deleteButton.dataset.stage2TemplateDeleteId);
        return;
      }
      const button = event.target.closest("[data-stage2-template-id]");
      if (!button) {
        return;
      }
      await applyStage2RuntimeTemplate(button.dataset.stage2TemplateId);
    });
    $("#stage3-template-strip").addEventListener("click", async (event) => {
      const deleteButton = event.target.closest("[data-stage3-template-delete-id]");
      if (deleteButton) {
        event.stopPropagation();
        await deleteStage3RuntimeTemplate(deleteButton.dataset.stage3TemplateDeleteId);
        return;
      }
      const button = event.target.closest("[data-stage3-template-id]");
      if (!button) {
        return;
      }
      await applyStage3RuntimeTemplate(button.dataset.stage3TemplateId);
    });
    $("#stage4-template-strip").addEventListener("click", async (event) => {
      const deleteButton = event.target.closest("[data-stage4-template-delete-id]");
      if (deleteButton) {
        event.stopPropagation();
        await deleteStage4RuntimeTemplate(deleteButton.dataset.stage4TemplateDeleteId);
        return;
      }
      const button = event.target.closest("[data-stage4-template-id]");
      if (!button) {
        return;
      }
      await applyStage4RuntimeTemplate(button.dataset.stage4TemplateId);
    });
    $("#repos-prev-btn").addEventListener("click", async () => {
      if (state.repoList.page <= 1) return;
      state.repoList.page -= 1;
      await loadRepos();
      setRefreshMeta();
    });
    $("#repos-next-btn").addEventListener("click", async () => {
      if (state.repoList.page >= state.repoList.totalPages) return;
      state.repoList.page += 1;
      await loadRepos();
      setRefreshMeta();
    });
    $("#stage2-prev-btn").addEventListener("click", async () => {
      if (state.stage2List.page <= 1) return;
      state.stage2List.page -= 1;
      await loadStage2Repos();
      setRefreshMeta();
    });
    $("#stage2-next-btn").addEventListener("click", async () => {
      if (state.stage2List.page >= state.stage2List.totalPages) return;
      state.stage2List.page += 1;
      await loadStage2Repos();
      setRefreshMeta();
    });
    $("#stage3-prev-btn").addEventListener("click", async () => {
      if (state.stage3List.page <= 1) return;
      state.stage3List.page -= 1;
      await loadStage3Repos();
      setRefreshMeta();
    });
    $("#stage3-next-btn").addEventListener("click", async () => {
      if (state.stage3List.page >= state.stage3List.totalPages) return;
      state.stage3List.page += 1;
      await loadStage3Repos();
      setRefreshMeta();
    });
    $("#stage4-prev-btn").addEventListener("click", async () => {
      if (state.stage4List.page <= 1) return;
      state.stage4List.page -= 1;
      await loadStage4Sources();
      setRefreshMeta();
    });
    $("#stage4-next-btn").addEventListener("click", async () => {
      if (state.stage4List.page >= state.stage4List.totalPages) return;
      state.stage4List.page += 1;
      await loadStage4Sources();
      setRefreshMeta();
    });
    $("#stage2-detail-back-btn").addEventListener("click", () => {
      hideStage2Detail();
      setRefreshMeta();
    });
    $("#stage3-detail-back-btn").addEventListener("click", () => {
      if (state.stage3List.entryDetailVisible) {
        state.stage3List.entryDetailVisible = false;
        renderStage3RepositoryDetail(currentStage3RepositoryDetailPayload());
      } else {
        hideStage3Detail();
      }
      setRefreshMeta();
    });
    $("#stage3-detail-primary-action-btn").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      const action = String(button.dataset.stage3DetailPrimaryAction || "");
      if (action !== "create-stage3-run") {
        return;
      }
      const entryFileId = button.dataset.stage3PrimaryActionId || state.selectedStage3EntryFileId;
      if (!entryFileId) {
        return;
      }
      const existingRunId = button.dataset.stage3PrimaryExistingRunId || "";
      const mode = button.dataset.stage3PrimaryActionMode || "";
      button.disabled = true;
      try {
        await runStage3EntryFile(entryFileId, existingRunId, mode);
      } finally {
        button.disabled = false;
      }
    });
    $("#crawl-language-toggle").addEventListener("click", (event) => {
      event.stopPropagation();
      const popover = $("#crawl-language-popover");
      popover.hidden = !popover.hidden;
      updateCrawlLanguageToggle();
    });
    document.querySelectorAll("[data-crawl-language]").forEach((input) => {
      input.addEventListener("change", () => {
        state.crawlForm.languages = readSelectedCrawlLanguages();
        updateCrawlLanguageToggle();
      });
    });
    $("#crawl-license-toggle").addEventListener("click", (event) => {
      event.stopPropagation();
      const popover = $("#crawl-license-popover");
      popover.hidden = !popover.hidden;
      updateCrawlLicenseToggle();
    });
    document.querySelectorAll("[data-crawl-license]").forEach((input) => {
      input.addEventListener("change", () => {
        state.crawlForm.licenses = readSelectedCrawlLicenses();
        updateCrawlLicenseToggle();
      });
    });
    $("#batch-language-toggle")?.addEventListener("click", (event) => {
      event.stopPropagation();
      const popover = $("#batch-language-popover");
      popover.hidden = !popover.hidden;
      updateBatchLanguageToggle();
    });
    document.querySelectorAll("[data-batch-language]").forEach((input) => {
      input.addEventListener("change", () => {
        state.batchForm.languages = readSelectedBatchLanguages();
        updateBatchLanguageToggle();
      });
    });
    $("#batch-license-toggle")?.addEventListener("click", (event) => {
      event.stopPropagation();
      const popover = $("#batch-license-popover");
      popover.hidden = !popover.hidden;
      updateBatchLicenseToggle();
    });
    document.querySelectorAll("[data-batch-license]").forEach((input) => {
      input.addEventListener("change", () => {
        state.batchForm.licenses = readSelectedBatchLicenses();
        updateBatchLicenseToggle();
      });
    });
    $("#repo-filter-language-toggle").addEventListener("click", (event) => {
      event.stopPropagation();
      const popover = $("#repo-filter-language-popover");
      popover.hidden = !popover.hidden;
      updateRepoFilterLanguageToggle();
    });
    document.querySelectorAll("[data-repo-filter-language]").forEach((input) => {
      input.addEventListener("change", () => {
        updateRepoFilterLanguageToggle(readSelectedRepoFilterLanguages());
      });
    });
    $("#repo-filter-license-toggle").addEventListener("click", (event) => {
      event.stopPropagation();
      const popover = $("#repo-filter-license-popover");
      popover.hidden = !popover.hidden;
      updateRepoFilterLicenseToggle();
    });
    document.querySelectorAll("[data-repo-filter-license]").forEach((input) => {
      input.addEventListener("change", () => {
        updateRepoFilterLicenseToggle(readSelectedRepoFilterLicenses());
      });
    });
    $("#stage2-filter-language-toggle").addEventListener("click", (event) => {
      event.stopPropagation();
      const popover = $("#stage2-filter-language-popover");
      popover.hidden = !popover.hidden;
      updateStage2FilterLanguageToggle();
    });
    $("#stage2-filter-status-toggle").addEventListener("click", (event) => {
      event.stopPropagation();
      const popover = $("#stage2-filter-status-popover");
      popover.hidden = !popover.hidden;
      updateStage2FilterStatusToggle();
    });
    document.querySelectorAll("[data-stage2-filter-status]").forEach((input) => {
      input.addEventListener("change", () => {
        updateStage2FilterStatusToggle(readSelectedStage2FilterStatuses());
      });
    });
    document.querySelectorAll("[data-stage2-filter-language]").forEach((input) => {
      input.addEventListener("change", () => {
        updateStage2FilterLanguageToggle(readSelectedStage2FilterLanguages());
      });
    });
    $("#stage3-filter-language-toggle").addEventListener("click", (event) => {
      event.stopPropagation();
      const popover = $("#stage3-filter-language-popover");
      popover.hidden = !popover.hidden;
      updateStage3FilterLanguageToggle();
    });
    $("#stage3-filter-status-toggle").addEventListener("click", (event) => {
      event.stopPropagation();
      const popover = $("#stage3-filter-status-popover");
      popover.hidden = !popover.hidden;
      updateStage3FilterStatusToggle();
    });
    document.querySelectorAll("[data-stage3-filter-status]").forEach((input) => {
      input.addEventListener("change", () => {
        updateStage3FilterStatusToggle(readSelectedStage3FilterStatuses());
      });
    });
    document.querySelectorAll("[data-stage3-filter-language]").forEach((input) => {
      input.addEventListener("change", () => {
        updateStage3FilterLanguageToggle(readSelectedStage3FilterLanguages());
      });
    });
    $("#stage4-filter-language-toggle").addEventListener("click", (event) => {
      event.stopPropagation();
      const popover = $("#stage4-filter-language-popover");
      popover.hidden = !popover.hidden;
      updateStage4FilterLanguageToggle();
    });
    $("#stage4-filter-status-toggle").addEventListener("click", (event) => {
      event.stopPropagation();
      const popover = $("#stage4-filter-status-popover");
      popover.hidden = !popover.hidden;
      updateStage4FilterStatusToggle();
    });
    document.querySelectorAll("[data-stage4-filter-status]").forEach((input) => {
      input.addEventListener("change", () => {
        updateStage4FilterStatusToggle(readSelectedStage4FilterStatuses());
      });
    });
    document.querySelectorAll("[data-stage4-filter-language]").forEach((input) => {
      input.addEventListener("change", () => {
        updateStage4FilterLanguageToggle(readSelectedStage4FilterLanguages());
      });
    });
    $("#stage2-filter-license-toggle").addEventListener("click", (event) => {
      event.stopPropagation();
      const popover = $("#stage2-filter-license-popover");
      popover.hidden = !popover.hidden;
      updateStage2FilterLicenseToggle();
    });
    document.querySelectorAll("[data-stage2-filter-license]").forEach((input) => {
      input.addEventListener("change", () => {
        updateStage2FilterLicenseToggle(readSelectedStage2FilterLicenses());
      });
    });
    $("#repo-filter-toggle").addEventListener("click", (event) => {
      event.stopPropagation();
      const popover = $("#repo-filter-popover");
      if (popover.hidden) {
        syncRepoFilterForm();
        popover.hidden = false;
        return;
      }
      popover.hidden = true;
    });
    $("#stage2-filter-toggle").addEventListener("click", (event) => {
      event.stopPropagation();
      const popover = $("#stage2-filter-popover");
      if (popover.hidden) {
        syncStage2FilterForm();
        popover.hidden = false;
        return;
      }
      popover.hidden = true;
    });
    $("#stage3-filter-toggle").addEventListener("click", (event) => {
      event.stopPropagation();
      const popover = $("#stage3-filter-popover");
      if (popover.hidden) {
        syncStage3FilterForm();
        popover.hidden = false;
        return;
      }
      popover.hidden = true;
    });
    $("#stage4-filter-toggle").addEventListener("click", (event) => {
      event.stopPropagation();
      const popover = $("#stage4-filter-popover");
      if (popover.hidden) {
        syncStage4SourceFilterControls();
        popover.hidden = false;
        $("#stage4-filter-toggle").setAttribute("aria-expanded", "true");
        return;
      }
      closeStage4FilterPopover();
    });
    document.addEventListener("selectionchange", () => {
      flushPendingLiveDetailPayloads();
    });
    $("#repo-filter-apply").addEventListener("click", async () => {
      const previousFilters = {
        ...state.repoList.filters,
        languages: [...state.repoList.filters.languages],
        licenses: [...state.repoList.filters.licenses],
      };
      const nextFilters = readRepoFilterForm();
      state.repoList.filters = nextFilters;
      state.repoList.page = 1;
      try {
        await loadRepos();
        persistRepoFilters();
        closeRepoFilterPopover();
        setRefreshMeta();
      } catch (error) {
        state.repoList.filters = previousFilters;
        syncRepoFilterForm();
        alert(`筛选失败: ${error.message}`);
      }
    });
    $("#stage2-filter-apply").addEventListener("click", async () => {
      const previousFilters = {
        ...state.stage2List.filters,
        statuses: [...state.stage2List.filters.statuses],
        languages: [...state.stage2List.filters.languages],
        licenses: [...state.stage2List.filters.licenses],
      };
      const nextFilters = readStage2FilterForm();
      state.stage2List.filters = nextFilters;
      state.stage2List.page = 1;
      try {
        await loadStage2Repos();
        persistStage2Filters();
        closeStage2FilterPopover();
        setRefreshMeta();
      } catch (error) {
        state.stage2List.filters = previousFilters;
        syncStage2FilterForm();
        alert(`筛选失败: ${error.message}`);
      }
    });
    $("#stage2-filter-non-pending").addEventListener("click", async () => {
      const shouldClearNonPendingFilter = isStage2NonPendingFilterActive();
      const previousFilters = {
        ...state.stage2List.filters,
        statuses: [...state.stage2List.filters.statuses],
        languages: [...state.stage2List.filters.languages],
        licenses: [...state.stage2List.filters.licenses],
      };
      state.stage2List.filters = {
        ...state.stage2List.filters,
        statuses: shouldClearNonPendingFilter ? [] : [...STAGE2_NON_PENDING_STATUSES],
      };
      state.stage2List.page = 1;
      syncStage2FilterForm();
      try {
        await loadStage2Repos();
        persistStage2Filters();
        setRefreshMeta();
      } catch (error) {
        state.stage2List.filters = previousFilters;
        syncStage2FilterForm();
        updateStage2FilterToggle();
        alert(`筛选失败: ${error.message}`);
      }
    });
    $("#stage3-filter-apply").addEventListener("click", async () => {
      const previousFilters = {
        ...state.stage3List.filters,
        statuses: [...state.stage3List.filters.statuses],
        languages: [...state.stage3List.filters.languages],
      };
      const nextFilters = readStage3FilterForm();
      state.stage3List.filters = nextFilters;
      state.stage3List.page = 1;
      try {
        await loadStage3Repos();
        persistStage3Filters();
        closeStage3FilterPopover();
        setRefreshMeta();
      } catch (error) {
        state.stage3List.filters = previousFilters;
        syncStage3FilterForm();
        alert(`筛选失败: ${error.message}`);
      }
    });
    $("#stage3-filter-non-pending").addEventListener("click", async () => {
      const shouldClearNonPendingFilter = isStage3NonPendingFilterActive();
      const previousFilters = {
        ...state.stage3List.filters,
        statuses: [...state.stage3List.filters.statuses],
        languages: [...state.stage3List.filters.languages],
      };
      state.stage3List.filters = {
        ...state.stage3List.filters,
        statuses: shouldClearNonPendingFilter ? [] : [...STAGE3_NON_PENDING_STATUSES],
      };
      state.stage3List.page = 1;
      syncStage3FilterForm();
      try {
        await loadStage3Repos();
        persistStage3Filters();
        setRefreshMeta();
      } catch (error) {
        state.stage3List.filters = previousFilters;
        syncStage3FilterForm();
        updateStage3FilterToggle();
        alert(`筛选失败: ${error.message}`);
      }
    });
    $("#repo-filter-reset").addEventListener("click", async () => {
      state.repoList.filters = defaultRepoFilters();
      syncRepoFilterForm();
      state.repoList.page = 1;
      closeRepoFilterPopover();
      await loadRepos();
      persistRepoFilters();
      setRefreshMeta();
    });
    $("#stage2-filter-reset").addEventListener("click", async () => {
      state.stage2List.filters = defaultStage2Filters();
      syncStage2FilterForm();
      state.stage2List.page = 1;
      closeStage2FilterPopover();
      await loadStage2Repos();
      persistStage2Filters();
      setRefreshMeta();
    });
    $("#stage3-filter-reset").addEventListener("click", async () => {
      state.stage3List.filters = defaultStage3Filters();
      syncStage3FilterForm();
      state.stage3List.page = 1;
      closeStage3FilterPopover();
      await loadStage3Repos();
      persistStage3Filters();
      setRefreshMeta();
    });
    $("#stage4-filter-apply").addEventListener("click", async () => {
      const previousFilters = {
        ...state.stage4List.sourceFilters,
        languages: [...(state.stage4List.sourceFilters.languages || [])],
        statuses: [...(state.stage4List.sourceFilters.statuses || [])],
      };
      state.stage4List.sourceFilters = readStage4SourceFiltersFromForm();
      state.stage4List.page = 1;
      try {
        await loadStage4Sources();
        if (state.stage4List.detailMode === "source" && state.selectedStage4SourceGroupKey) {
          await loadStage4SourceDetailByKey(state.selectedStage4SourceGroupKey);
        }
        closeStage4FilterPopover();
        setRefreshMeta();
      } catch (error) {
        state.stage4List.sourceFilters = previousFilters;
        syncStage4SourceFilterControls();
        alert(`筛选失败: ${error.message}`);
      }
    });
    $("#stage4-filter-reset").addEventListener("click", async () => {
      state.stage4List.sourceFilters = { nameQuery: "", languages: [], statuses: [] };
      state.stage4List.page = 1;
      syncStage4SourceFilterControls();
      closeStage4FilterPopover();
      await loadStage4Sources();
      setRefreshMeta();
    });
    $("#stage4-filter-non-pending").addEventListener("click", async () => {
      state.stage4List.sourceFilters = {
        ...state.stage4List.sourceFilters,
        statuses: isStage4NonPendingFilterActive() ? [] : [...STAGE4_NON_PENDING_STATUSES],
      };
      state.stage4List.page = 1;
      syncStage4SourceFilterControls();
      await loadStage4Sources();
      setRefreshMeta();
    });
    document.querySelectorAll("[data-repo-sort]").forEach((button) => {
      button.addEventListener("click", async () => {
        const sortField = button.dataset.repoSort;
        if (state.repoList.sortField === sortField) {
          state.repoList.sortOrder = state.repoList.sortOrder === "asc" ? "desc" : "asc";
        } else {
          state.repoList.sortField = sortField;
          state.repoList.sortOrder = "desc";
        }
        state.repoList.page = 1;
        await loadRepos();
        setRefreshMeta();
      });
    });
    document.querySelectorAll("[data-stage2-sort]").forEach((button) => {
      button.addEventListener("click", async () => {
        const sortField = button.dataset.stage2Sort;
        if (state.stage2List.sortField === sortField) {
          state.stage2List.sortOrder = state.stage2List.sortOrder === "asc" ? "desc" : "asc";
        } else {
          state.stage2List.sortField = sortField;
          state.stage2List.sortOrder = "desc";
        }
        state.stage2List.page = 1;
        await loadStage2Repos();
        setRefreshMeta();
      });
    });
    document.querySelectorAll("[data-stage3-sort]").forEach((button) => {
      button.addEventListener("click", async () => {
        const sortField = button.dataset.stage3Sort;
        if (state.stage3List.sortField === sortField) {
          state.stage3List.sortOrder = state.stage3List.sortOrder === "asc" ? "desc" : "asc";
        } else {
          state.stage3List.sortField = sortField;
          state.stage3List.sortOrder = "desc";
        }
        state.stage3List.page = 1;
        await loadStage3Repos();
        setRefreshMeta();
      });
    });
    $("#batch-form")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      await createBatchTask(event.currentTarget);
      setRefreshMeta();
    });
    $("#batch-form")?.addEventListener("input", (event) => {
      markBatchRuntimeFieldTouched(event.target);
      if (event.target?.name === "target_repositories") {
        syncBatchTargetRepositoryMode();
      }
    });
    $("#batch-form")?.addEventListener("change", (event) => {
      markBatchRuntimeFieldTouched(event.target);
      if (event.target?.name === "target_repositories") {
        syncBatchTargetRepositoryMode();
      }
      if (event.target?.name === "stop_after_stage") {
        syncBatchRuntimeModes();
      }
    });
    $("#batch-data-pool-select")?.addEventListener("focus", async (event) => {
      try {
        await loadDataPools();
      } catch (error) {
        const message = $("#batch-form-message");
        if (message) message.textContent = `刷新数据池失败: ${error.message || String(error)}`;
      }
      const value = event.currentTarget.value;
      if (value && value !== "__new__") {
        state.batchDataPoolSelectPreviousId = value;
      }
    });
    $("#batch-data-pool-select")?.addEventListener("change", (event) => {
      const value = event.currentTarget.value;
      if (value === "__new__") {
        const availablePools = (Array.isArray(state.dataPools) ? state.dataPools : []).filter((pool) => pool.is_available !== false);
        const availableById = (id) => availablePools.find((pool) => String(pool.id) === String(id || ""))?.id || null;
        state.batchDataPoolSelectPreviousId = availableById(state.batchSelectedDataPoolId)
          || availableById(state.batchDataPoolSelectPreviousId)
          || availableById(state.defaultDataPoolId)
          || availablePools.find((pool) => String(pool.id) === String(state.selectedDataPoolId || ""))?.id
          || availablePools[0]?.id
          || null;
        openBatchDataPoolPopover();
        return;
      }
      state.batchSelectedDataPoolId = value || null;
      state.batchDataPoolSelectPreviousId = value || null;
      closeBatchDataPoolPopover();
    });
    $("#batch-new-data-pool-create")?.addEventListener("click", async () => {
      await createBatchDataPoolFromPopover();
      setRefreshMeta();
    });
    $("#batch-new-data-pool-cancel")?.addEventListener("click", () => {
      closeBatchDataPoolPopover({ restore: true, clear: true });
    });
    $("#batch-data-pool-popover")?.addEventListener("keydown", async (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeBatchDataPoolPopover({ restore: true });
      }
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        await createBatchDataPoolFromPopover();
        setRefreshMeta();
      }
    });
    $("#batch-data-pool-popover")?.addEventListener("click", (event) => {
      if (event.target === event.currentTarget) {
        closeBatchDataPoolPopover({ restore: true });
      }
    });
    $("#batch-retry-popover")?.addEventListener("click", async (event) => {
      const sourceButton = event.target.closest("[data-runtime-config-source]");
      if (sourceButton) {
        await submitBatchRetry(sourceButton.dataset.runtimeConfigSource);
        return;
      }
      if (event.target === event.currentTarget) {
        closeBatchRetryPopover();
      }
    });
    $("#batch-retry-popover")?.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeBatchRetryPopover();
      }
    });
    $("#batch-retry-cancel-btn")?.addEventListener("click", () => {
      closeBatchRetryPopover();
    });
    $("#batch-refresh-btn")?.addEventListener("click", async () => {
      try {
        await Promise.all([loadDataPools(), loadBatchTasks()]);
        if (state.selectedBatchTaskId) {
          await loadBatchTaskDetail(state.selectedBatchTaskId);
        }
        setRefreshMeta();
      } catch (error) {
        const message = $("#batch-detail-message");
        if (message) message.textContent = `刷新失败: ${error.message || String(error)}`;
      }
    });
    $("#batch-tasks-body")?.addEventListener("click", async (event) => {
      const retryButton = event.target.closest("[data-batch-retry-id]");
      if (retryButton) {
        event.stopPropagation();
        openBatchRetryPopover(retryButton.dataset.batchRetryId);
        return;
      }
      const deleteButton = event.target.closest("[data-batch-delete-id]");
      if (deleteButton) {
        event.stopPropagation();
        await deleteBatchTask(deleteButton.dataset.batchDeleteId);
        setRefreshMeta();
        return;
      }
      const cancelButton = event.target.closest("[data-batch-cancel-id]");
      if (cancelButton) {
        event.stopPropagation();
        if (!window.confirm("确认取消这个批量任务吗？")) return;
        await cancelBatchTask(cancelButton.dataset.batchCancelId);
        setRefreshMeta();
        return;
      }
      const row = event.target.closest("[data-batch-task-id]");
      if (row) {
        const taskId = String(row.dataset.batchTaskId || "");
        const panel = $("#batch-detail-panel");
        if (String(state.selectedBatchTaskId || "") === taskId && panel && !panel.hidden) {
          hideBatchTaskDetail();
        } else {
          await loadBatchTaskDetail(taskId);
        }
        setRefreshMeta();
      }
    });
    $("#batch-detail-body")?.addEventListener("click", async (event) => {
      const repoPrevButton = event.target.closest("#batch-repo-prev-btn");
      if (repoPrevButton) {
        event.stopPropagation();
        if (state.batchTaskDetailLoading) return;
        state.batchRepositoryList.page = Math.max(1, Number(state.batchRepositoryList.page || 1) - 1);
        if (state.selectedBatchTaskId) {
          await loadBatchTaskDetail(state.selectedBatchTaskId);
        }
        return;
      }
      const repoNextButton = event.target.closest("#batch-repo-next-btn");
      if (repoNextButton) {
        event.stopPropagation();
        if (state.batchTaskDetailLoading) return;
        state.batchRepositoryList.page = Math.max(1, Number(state.batchRepositoryList.page || 1) + 1);
        if (state.selectedBatchTaskId) {
          await loadBatchTaskDetail(state.selectedBatchTaskId);
        }
        return;
      }
      const repoFilterToggle = event.target.closest("#batch-repo-filter-toggle");
      if (repoFilterToggle) {
        event.stopPropagation();
        const popover = $("#batch-repo-filter-popover");
        if (popover?.hidden) {
          syncBatchRepoFilterForm();
          popover.hidden = false;
        } else {
          closeBatchRepoFilterPopover();
        }
        return;
      }
      const repoFilterApply = event.target.closest("#batch-repo-filter-apply");
      if (repoFilterApply) {
        event.stopPropagation();
        state.batchRepositoryList.filters = readBatchRepoFiltersFromForm();
        state.batchRepositoryList.page = 1;
        closeBatchRepoFilterPopover({ flush: false });
        if (state.selectedBatchTaskId) {
          await loadBatchTaskDetail(state.selectedBatchTaskId);
        }
        return;
      }
      const repoFilterReset = event.target.closest("#batch-repo-filter-reset");
      if (repoFilterReset) {
        event.stopPropagation();
        state.batchRepositoryList.filters = defaultBatchRepositoryFilters();
        state.batchRepositoryList.page = 1;
        closeBatchRepoFilterPopover({ flush: false });
        if (state.selectedBatchTaskId) {
          await loadBatchTaskDetail(state.selectedBatchTaskId);
        }
        return;
      }
      const retryRepositoryButton = event.target.closest("[data-batch-retry-repository-id]");
      if (retryRepositoryButton) {
        event.stopPropagation();
        const taskId = String(state.selectedBatchTaskId || "").trim();
        const taskRepositoryId = String(
          retryRepositoryButton.dataset.batchRetryRepositoryId || "",
        ).trim();
        if (
          !taskId
          || !taskRepositoryId
          || !window.confirm("只重新运行这个失败的 repo，保留其他 repo 的结果吗？")
        ) {
          return;
        }
        retryRepositoryButton.disabled = true;
        try {
          await retryFailedBatchRepository(taskId, taskRepositoryId);
          setRefreshMeta();
        } catch (error) {
          retryRepositoryButton.disabled = false;
          window.alert(`重试失败: ${error.message || String(error)}`);
        }
        return;
      }
      const stage2Button = event.target.closest("[data-batch-stage2-repo-id]");
      if (stage2Button) {
        event.stopPropagation();
        await openBatchStageDetail({
          stage: "stage2",
          repositoryId: stage2Button.dataset.batchStage2RepoId,
          runId: stage2Button.dataset.batchStage2RunId,
        });
        return;
      }
      const stage3Button = event.target.closest("[data-batch-stage3-repo-id]");
      if (stage3Button) {
        event.stopPropagation();
        await openBatchStageDetail({
          stage: "stage3",
          repositoryId: stage3Button.dataset.batchStage3RepoId,
          snapshotId: stage3Button.dataset.batchStage3SnapshotId,
          runId: stage3Button.dataset.batchStage3RunId,
        });
        return;
      }
      const stage4SourceButton = event.target.closest("[data-batch-stage4-source-group-key]");
      if (stage4SourceButton) {
        event.stopPropagation();
        await openBatchStageDetail({
          stage: "stage4",
          sourceGroupKey: stage4SourceButton.dataset.batchStage4SourceGroupKey,
        });
        return;
      }
      const stage4Button = event.target.closest("[data-batch-stage4-run-id]");
      if (stage4Button) {
        event.stopPropagation();
        await openBatchStageDetail({
          stage: "stage4",
          runId: stage4Button.dataset.batchStage4RunId,
        });
      }
    });
    $("#batch-cancel-btn")?.addEventListener("click", async () => {
      if (!state.selectedBatchTaskId || !window.confirm("确认取消这个批量任务吗？")) return;
      await cancelBatchTask(state.selectedBatchTaskId);
      setRefreshMeta();
    });
    $("#batch-delete-btn")?.addEventListener("click", async () => {
      if (!state.selectedBatchTaskId) return;
      await deleteBatchTask(state.selectedBatchTaskId);
      setRefreshMeta();
    });
    $("#data-pool-selector-list")?.addEventListener("click", async (event) => {
      const previewButton = event.target.closest("[data-data-pool-preview-id]");
      if (previewButton) {
        event.preventDefault();
        event.stopPropagation();
        await openDataPoolPreview(previewButton.dataset.dataPoolPreviewId);
        return;
      }
      const card = event.target.closest("[data-data-pool-id]");
      if (!card) return;
      state.selectedDataPoolId = card.dataset.dataPoolId;
      state.selectedDataPoolAssetIds.clear();
      state.dataPoolDownloadSelectionId = null;
      state.dataPoolList.page = 1;
      hideDataPoolAssetDetail({ clearSelection: true });
      closeDataPoolSelectorPopover();
      renderDataPools();
      await loadDataPoolAssets();
      setRefreshMeta();
    });
    $("#data-pool-selector-list")?.addEventListener("keydown", async (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      if (event.target.closest("[data-data-pool-preview-id]")) return;
      const card = event.target.closest("[data-data-pool-id]");
      if (!card) return;
      event.preventDefault();
      state.selectedDataPoolId = card.dataset.dataPoolId;
      state.selectedDataPoolAssetIds.clear();
      state.dataPoolDownloadSelectionId = null;
      state.dataPoolList.page = 1;
      hideDataPoolAssetDetail({ clearSelection: true });
      closeDataPoolSelectorPopover();
      renderDataPools();
      await loadDataPoolAssets();
      setRefreshMeta();
    });
    $("#data-pool-filter-apply")?.addEventListener("click", async () => {
      state.dataPoolList.filters = readDataPoolFiltersFromForm();
      state.selectedDataPoolAssetIds.clear();
      state.dataPoolDownloadSelectionId = null;
      state.dataPoolList.page = 1;
      closeDataPoolFilterPopover();
      await loadDataPoolAssets();
      updateDataPoolFilterToggle();
      setRefreshMeta();
    });
    $("#data-pool-filter-reset")?.addEventListener("click", async () => {
      state.dataPoolList.filters = defaultDataPoolFilters();
      state.selectedDataPoolAssetIds.clear();
      state.dataPoolDownloadSelectionId = null;
      syncDataPoolFilterForm();
      state.dataPoolList.page = 1;
      await loadDataPoolAssets();
      setRefreshMeta();
    });
    $("#data-pool-filter-toggle")?.addEventListener("click", (event) => {
      event.stopPropagation();
      const popover = $("#data-pool-filter-popover");
      if (popover) {
        popover.hidden = !popover.hidden;
        if (!popover.hidden) {
          syncDataPoolFilterForm();
        }
      }
    });
    $("#data-pool-selector-toggle")?.addEventListener("click", (event) => {
      event.stopPropagation();
      const popover = $("#data-pool-selector-popover");
      if (popover) {
        popover.hidden = !popover.hidden;
      }
    });
    $("#data-pool-filter-popover")?.addEventListener("keydown", async (event) => {
      if (event.key === "Escape") {
        closeDataPoolFilterPopover();
      }
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        state.dataPoolList.filters = readDataPoolFiltersFromForm();
        state.dataPoolList.page = 1;
        closeDataPoolFilterPopover();
        await loadDataPoolAssets();
        updateDataPoolFilterToggle();
        setRefreshMeta();
      }
    });
    $("#data-pool-prev-btn")?.addEventListener("click", async () => {
      if (state.dataPoolList.page <= 1) return;
      state.dataPoolList.page -= 1;
      await loadDataPoolAssets();
      setRefreshMeta();
    });
    $("#data-pool-next-btn")?.addEventListener("click", async () => {
      if (state.dataPoolList.page >= state.dataPoolList.totalPages) return;
      state.dataPoolList.page += 1;
      await loadDataPoolAssets();
      setRefreshMeta();
    });
    $("#data-pool-select-page")?.addEventListener("change", async (event) => {
      const checked = Boolean(event.currentTarget.checked);
      if (!checked) {
        state.selectedDataPoolAssetIds.clear();
        state.dataPoolDownloadSelectionId = null;
        renderDataPoolAssets();
        return;
      }
      try {
        await selectAllDataPoolAssetsForCurrentFilter();
      } catch (error) {
        const message = $("#data-pool-message");
        if (message) message.textContent = `全选失败: ${error.message || String(error)}`;
        event.currentTarget.checked = false;
      }
    });
    $("#data-pool-assets-body")?.addEventListener("click", async (event) => {
      const checkbox = event.target.closest("[data-data-pool-asset-check]");
      if (checkbox) {
        event.stopPropagation();
        const id = String(checkbox.dataset.dataPoolAssetCheck || "");
        if (checkbox.checked) {
          state.selectedDataPoolAssetIds.add(id);
        } else {
          state.selectedDataPoolAssetIds.delete(id);
        }
        state.dataPoolDownloadSelectionId = null;
        renderDataPoolAssets();
        return;
      }
      const row = event.target.closest("[data-data-pool-asset-id]");
      if (row) {
        try {
          await loadDataPoolAssetDetail(row.dataset.dataPoolAssetId);
          setRefreshMeta();
        } catch (error) {
          const detailPanel = $("#data-pool-asset-detail-panel");
          const detailBody = $("#data-pool-asset-detail-body");
          const detailMessage = $("#data-pool-asset-detail-message");
          const detailTitle = $("#data-pool-asset-detail-title");
          const message = $("#data-pool-message");
          const errorText = `打开资产详情失败: ${error.message || String(error)}`;
          state.dataPoolDetailVisible = true;
          updateDataPoolLayout();
          if (detailPanel) detailPanel.hidden = false;
          if (detailTitle) detailTitle.textContent = "资产详情";
          if (detailBody) detailBody.innerHTML = `<div class="muted">${escapeHtml(errorText)}</div>`;
          if (detailMessage) detailMessage.textContent = errorText;
          if (message) message.textContent = errorText;
        }
      }
    });
    $("#data-pool-asset-detail-body")?.addEventListener("click", async (event) => {
      const assetButton = event.target.closest("[data-data-pool-detail-asset-key]");
      if (assetButton) {
        state.selectedDataPoolDetailAssetKey = assetButton.dataset.dataPoolDetailAssetKey || null;
        if (state.dataPoolAssetDetail) {
          rerenderDataPoolDetailPreservingScroll();
        }
        return;
      }
      const issueButton = event.target.closest("[data-data-pool-issue-select-index]");
      if (issueButton) {
        state.selectedDataPoolIssueIndex = Number(issueButton.dataset.dataPoolIssueSelectIndex || 1);
        if (state.dataPoolAssetDetail) {
          rerenderDataPoolDetailPreservingScroll();
        }
        return;
      }
      const completionFileButton = event.target.closest("[data-data-pool-completion-file-path]");
      if (completionFileButton) {
        const asset = currentSelectedDataPoolDetailAsset();
        if (asset?.kind === "completion_archive") {
          state.selectedDataPoolCompletionFileByAssetKey[asset.key] = completionFileButton.dataset.dataPoolCompletionFilePath || "";
          if (state.dataPoolAssetDetail) {
            rerenderDataPoolDetailPreservingScroll();
          }
        }
        return;
      }
      const copyButton = event.target.closest("[data-data-pool-asset-copy]");
      if (copyButton) {
        await copySelectedDataPoolAsset(copyButton);
      }
    });
    $("#data-pool-asset-detail-back-btn")?.addEventListener("click", () => {
      hideDataPoolAssetDetail({ clearSelection: true });
      setRefreshMeta();
    });
    $("#data-pool-delete-selected-btn")?.addEventListener("click", async () => {
      if (!state.selectedDataPoolAssetIds.size) {
        const message = $("#data-pool-message");
        if (message) message.textContent = "请先选择要删除的资产。";
        return;
      }
      if (!window.confirm(`确认删除选中的 ${state.selectedDataPoolAssetIds.size} 条资产吗？`)) return;
      const deleteAllFiltered = state.dataPoolList.total > 0 && state.selectedDataPoolAssetIds.size >= state.dataPoolList.total;
      await deleteDataPoolAssets({ filtered: deleteAllFiltered });
      setRefreshMeta();
    });
    $("#data-pool-download-btn")?.addEventListener("click", () => {
      if (!state.selectedDataPoolId) {
        const message = $("#data-pool-message");
        if (message) message.textContent = "请先选择数据池。";
        return;
      }
      if (!state.selectedDataPoolAssetIds.size) {
        alert("你还没有选中任何的数据，不能下载。");
        return;
      }
      openDataPoolDownloadPopover().catch((error) => {
        const message = $("#data-pool-message");
        if (message) message.textContent = error.message || String(error);
      });
    });
    $("#data-pool-download-browser-btn")?.addEventListener("click", async () => {
      setDataPoolDownloadModalMessage("");
      try {
        await startBrowserDataPoolDownload();
        closeDataPoolDownloadPopover();
        setRefreshMeta();
      } catch (error) {
        setDataPoolDownloadModalMessage(error.message || String(error));
      }
    });
    $("#data-pool-download-copy-btn")?.addEventListener("click", async () => {
      const command = $("#data-pool-download-command")?.value || "";
      try {
        await copyTextToClipboard(command);
        setDataPoolDownloadModalMessage("下载命令已复制");
      } catch (error) {
        setDataPoolDownloadModalMessage(`复制失败: ${error.message || String(error)}`);
      }
    });
    $("#data-pool-download-close-btn")?.addEventListener("click", () => {
      closeDataPoolDownloadPopover();
    });
    $("#data-pool-download-popover")?.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeDataPoolDownloadPopover();
      }
    });
    $("#data-pool-download-popover")?.addEventListener("click", (event) => {
      if (event.target === event.currentTarget) {
        closeDataPoolDownloadPopover();
      }
    });
    $("#data-pool-preview-close-btn")?.addEventListener("click", () => {
      closeDataPoolPreview();
    });
    $("#data-pool-preview-popover")?.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeDataPoolPreview();
      }
    });
    $("#data-pool-preview-popover")?.addEventListener("click", (event) => {
      if (event.target === event.currentTarget) {
        closeDataPoolPreview();
      }
    });
    $("#data-pool-preview-body")?.addEventListener("click", (event) => {
      const repoPageButton = event.target.closest("[data-data-pool-preview-repo-page]");
      if (!repoPageButton) return;
      const payload = state.dataPoolPreviewPayload;
      const repos = payload?.repo_asset_distribution?.repos || [];
      const totalPages = Math.max(
        1,
        Math.ceil((Array.isArray(repos) ? repos.length : 0) / Math.max(1, Number(state.dataPoolPreviewRepoPageSize || 10))),
      );
      const direction = String(repoPageButton.dataset.dataPoolPreviewRepoPage || "");
      if (direction === "prev") {
        state.dataPoolPreviewRepoPage = Math.max(1, Number(state.dataPoolPreviewRepoPage || 1) - 1);
      } else if (direction === "next") {
        state.dataPoolPreviewRepoPage = Math.min(totalPages, Number(state.dataPoolPreviewRepoPage || 1) + 1);
      }
      if (payload) {
        renderDataPoolPreview(payload);
      }
    });
    document.querySelectorAll("[data-data-pool-sort]").forEach((button) => {
      button.addEventListener("click", async () => {
        const sortField = String(button.dataset.dataPoolSort || "");
        if (!sortField) return;
        if (state.dataPoolList.sortField === sortField) {
          state.dataPoolList.sortOrder = state.dataPoolList.sortOrder === "asc" ? "desc" : "asc";
        } else {
          state.dataPoolList.sortField = sortField;
          state.dataPoolList.sortOrder = dataPoolSortDefaultOrder(sortField);
        }
        state.dataPoolList.page = 1;
        await loadDataPoolAssets();
        setRefreshMeta();
      });
    });
    document.querySelectorAll(".tab-button").forEach((button) => {
      button.addEventListener("click", () => switchTab(button.dataset.tab));
    });
    document.querySelectorAll("[data-stage4-source-sort]").forEach((button) => {
      button.addEventListener("click", async () => {
        const sortField = button.dataset.stage4SourceSort;
        if (state.stage4List.sourceSortField === sortField) {
          state.stage4List.sourceSortOrder = state.stage4List.sourceSortOrder === "asc" ? "desc" : "asc";
        } else {
          state.stage4List.sourceSortField = sortField;
          state.stage4List.sourceSortOrder = sortField === "repo" || sortField === "commit" || sortField === "language" ? "asc" : "desc";
        }
        state.stage4List.page = 1;
        await loadStage4Sources();
        setRefreshMeta();
      });
    });
    $("#stage4-sources-body").addEventListener("click", async (event) => {
      const runButton = event.target.closest("[data-stage4-run-group-key]");
      if (runButton) {
        event.stopPropagation();
        await runAllStage4GroupSavepoints(runButton.dataset.stage4RunGroupKey);
        return;
      }
      if (event.target.closest("input, select, textarea, button, a")) {
        return;
      }
      const row = event.target.closest("[data-stage4-source-group-key]");
      const groupKey = String(row?.dataset.stage4SourceGroupKey || "").trim();
      if (groupKey) {
        await loadStage4SourceDetailByKey(groupKey);
      }
    });
    $("#stage4-detail-body").addEventListener("click", async (event) => {
      const createButton = event.target.closest("[data-stage4-create-source-id]");
      if (createButton) {
        event.stopPropagation();
        await createStage4Run(createButton.dataset.stage4CreateSourceId);
        return;
      }
      const runtimeEditButton = event.target.closest("[data-stage4-run-runtime-edit-id]");
      if (runtimeEditButton) {
        event.stopPropagation();
        const run = await loadStage4RunDetail(runtimeEditButton.dataset.stage4RunRuntimeEditId);
        if (run) {
          startStage4RunRuntimeEdit(run);
        }
        return;
      }
      const runtimeCancelButton = event.target.closest("[data-stage4-run-runtime-cancel-id]");
      if (runtimeCancelButton) {
        event.stopPropagation();
        await cancelStage4RunRuntimeEdit(runtimeCancelButton.dataset.stage4RunRuntimeCancelId);
        return;
      }
      const runtimeSaveButton = event.target.closest("[data-stage4-run-runtime-save-id]");
      if (runtimeSaveButton) {
        event.stopPropagation();
        await saveStage4RunRuntimeConfig(runtimeSaveButton.dataset.stage4RunRuntimeSaveId);
        return;
      }
      const savepointSortButton = event.target.closest("[data-stage4-savepoint-sort]");
      if (savepointSortButton) {
        event.stopPropagation();
        const sortField = savepointSortButton.dataset.stage4SavepointSort;
        if (state.stage4List.savepointSortField === sortField) {
          state.stage4List.savepointSortOrder = state.stage4List.savepointSortOrder === "asc" ? "desc" : "asc";
        } else {
          state.stage4List.savepointSortField = sortField;
          state.stage4List.savepointSortOrder = sortField === "entry_file" || sortField === "status" ? "asc" : "desc";
        }
        await loadStage4SourceDetailByKey(state.selectedStage4SourceGroupKey);
        return;
      }
      const savepointFilterNonPending = event.target.closest("#stage4-savepoint-filter-non-pending");
      if (savepointFilterNonPending) {
        event.stopPropagation();
        state.stage4List.savepointFilters = {
          ...state.stage4List.savepointFilters,
          statuses: isStage4SavepointNonPendingFilterActive() ? [] : [...STAGE4_SAVEPOINT_NON_PENDING_STATUSES],
        };
        await loadStage4SourceDetailByKey(state.selectedStage4SourceGroupKey);
        return;
      }
      const savepointFilterToggle = event.target.closest("#stage4-savepoint-filter-toggle");
      if (savepointFilterToggle) {
        event.stopPropagation();
        const popover = $("#stage4-savepoint-filter-popover");
        if (popover?.hidden) {
          popover.hidden = false;
          savepointFilterToggle.setAttribute("aria-expanded", String(!popover.hidden));
          syncStage4SavepointFilterControls();
        } else {
          closeStage4SavepointFilterPopover();
        }
        return;
      }
      const savepointFilterStatusToggle = event.target.closest("#stage4-savepoint-filter-status-toggle");
      if (savepointFilterStatusToggle) {
        event.stopPropagation();
        const popover = $("#stage4-savepoint-filter-status-popover");
        if (popover) {
          popover.hidden = !popover.hidden;
          updateStage4SavepointFilterStatusToggle(readSelectedStage4SavepointFilterStatuses());
        }
        return;
      }
      const savepointFilterApply = event.target.closest("[data-stage4-savepoint-filter-apply]");
      if (savepointFilterApply) {
        event.stopPropagation();
        state.stage4List.savepointFilters = readStage4SavepointFiltersFromForm();
        closeStage4SavepointFilterPopover({ flush: false });
        await loadStage4SourceDetailByKey(state.selectedStage4SourceGroupKey);
        return;
      }
      const savepointFilterReset = event.target.closest("[data-stage4-savepoint-filter-reset]");
      if (savepointFilterReset) {
        event.stopPropagation();
        state.stage4List.savepointFilters = {
          entryQuery: "",
          statuses: [],
          testCountMin: "",
          testCountMax: "",
          entryPassRateMin: "",
          entryPassRateMax: "",
          diffLinesMin: "",
          diffLinesMax: "",
        };
        closeStage4SavepointFilterPopover({ flush: false });
        await loadStage4SourceDetailByKey(state.selectedStage4SourceGroupKey);
        return;
      }
      const interruptButton = event.target.closest("[data-stage4-interrupt-run-id]");
      if (interruptButton) {
        event.stopPropagation();
        await interruptStage4Run(interruptButton.dataset.stage4InterruptRunId);
        return;
      }
      const deleteButton = event.target.closest("[data-stage4-delete-run-id]");
      if (deleteButton) {
        event.stopPropagation();
        await deleteStage4Run(deleteButton.dataset.stage4DeleteRunId);
        return;
      }
      const rerunButton = event.target.closest("[data-stage4-rerun-source-savepoint-id]");
      if (rerunButton) {
        event.stopPropagation();
        await rerunStage4FromSourceSavepoint(rerunButton.dataset.stage4RerunSourceSavepointId);
        return;
      }
      const openRunButton = event.target.closest("[data-stage4-open-run-id]");
      if (openRunButton) {
        event.stopPropagation();
        await loadStage4RunDetail(openRunButton.dataset.stage4OpenRunId);
        return;
      }
      const assetButton = event.target.closest("[data-stage4-asset-key]");
      if (assetButton) {
        state.selectedStage4AssetKey = assetButton.dataset.stage4AssetKey || null;
        await rerenderCurrentStage4RunDetailOrLoad();
        return;
      }
      const issueSelect = event.target.closest("[data-stage4-issue-select-index]");
      if (issueSelect) {
        state.selectedStage4IssueIndex = Number(issueSelect.dataset.stage4IssueSelectIndex || 1);
        await rerenderCurrentStage4RunDetailOrLoad();
        return;
      }
      const receiveFeedbackSelect = event.target.closest("[data-stage4-receive-feedback-select-index]");
      if (receiveFeedbackSelect) {
        state.selectedStage4ReceiveFeedbackIndex = Number(
          receiveFeedbackSelect.dataset.stage4ReceiveFeedbackSelectIndex || 1,
        );
        await rerenderCurrentStage4RunDetailOrLoad();
        return;
      }
      const completionFileButton = event.target.closest("[data-stage4-completion-file-path]");
      if (completionFileButton) {
        const run = currentStage4RunDetail();
        const asset = run ? currentStage4Asset(run) : null;
        if (asset?.kind === "completion_archive") {
          state.selectedStage4CompletionFileByAssetKey[asset.key] = completionFileButton.dataset.stage4CompletionFilePath || "";
          await ensureStage4CompletionFileLoaded(asset);
          await rerenderCurrentStage4RunDetailOrLoad();
        }
        return;
      }
      const copyCurrentAsset = event.target.closest("[data-stage4-copy-current-asset]");
      if (copyCurrentAsset) {
        await copyCurrentStage4Asset(copyCurrentAsset);
        return;
      }
      const copyIssueButton = event.target.closest("[data-stage4-copy-issue-index]");
      if (copyIssueButton) {
        await copyStage4VariantMarkdown(state.selectedStage4RunId, copyIssueButton.dataset.stage4CopyIssueIndex);
        return;
      }
      if (event.target.closest("input, select, textarea, button, a")) {
        return;
      }
      const savepointRow = event.target.closest("[data-stage4-source-latest-run-id]");
      const latestRunId = String(savepointRow?.dataset.stage4SourceLatestRunId || "").trim();
      if (latestRunId) {
        await loadStage4RunDetail(latestRunId);
      }
    });
    $("#stage4-detail-body").addEventListener("input", (event) => {
      const input = event.target.closest("[data-stage4-run-runtime-field]");
      if (!input) {
        return;
      }
      const field = input.dataset.stage4RunRuntimeField;
      if (!field) {
        return;
      }
      let value = input.value;
      if (input.type === "number") {
        value = input.value === "" ? null : Number(input.value);
      }
      updateStage4RunRuntimeEditorDraft(field, value);
    });
    $("#stage4-detail-body").addEventListener("change", (event) => {
      const input = event.target.closest("[data-stage4-run-runtime-field]");
      if (!input) {
        return;
      }
      const field = input.dataset.stage4RunRuntimeField;
      if (!field) {
        return;
      }
      let value = input.value;
      if (input.type === "number") {
        value = input.value === "" ? null : Number(input.value);
      }
      updateStage4RunRuntimeEditorDraft(field, value);
    });
    $("#stage4-detail-back-btn").addEventListener("click", async () => {
      if (state.stage4List.detailMode === "run" && state.selectedStage4SourceGroupKey) {
        await loadStage4SourceDetailByKey(state.selectedStage4SourceGroupKey);
      } else {
        hideStage4Detail();
      }
    });
    $("#stage4-detail-primary-action-btn").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      const action = String(button.dataset.stage4DetailPrimaryAction || "");
      const targetId = String(button.dataset.stage4PrimaryActionId || "").trim();
      if (!action || !targetId) {
        return;
      }
      button.disabled = true;
      try {
        if (action === "create-stage4-run") {
          await createStage4Run(targetId);
          return;
        }
        if (action === "interrupt-stage4-run") {
          await interruptStage4Run(targetId);
          return;
        }
        if (action === "rerun-stage4-source") {
          await rerunStage4FromSourceSavepoint(targetId);
        }
      } finally {
        button.disabled = false;
      }
    });
    $("#asset-stage2-sdk-body").addEventListener("click", async (event) => {
      const cancelButton = event.target.closest("[data-stage2-sdk-prewarm-cancel-target]");
      if (cancelButton) {
        await cancelStage2SdkPrewarmTarget(cancelButton.dataset.stage2SdkPrewarmCancelTarget);
        return;
      }
      const button = event.target.closest("[data-stage2-sdk-prewarm-target]");
      if (!button) {
        return;
      }
      await prewarmStage2SdkTarget(button.dataset.stage2SdkPrewarmTarget);
    });
    $("#asset-stage2-image-body").addEventListener("click", async (event) => {
      const cancelButton = event.target.closest("[data-stage2-image-cancel-id]");
      if (cancelButton) {
        event.stopPropagation();
        await cancelStage2ImageAsset(cancelButton.dataset.stage2ImageCancelId, {
          kind: cancelButton.dataset.stage2ImageCancelKind,
        });
        return;
      }
      const button = event.target.closest("[data-stage2-image-build-id]");
      if (button) {
        event.stopPropagation();
        await buildStage2ImageAsset(button.dataset.stage2ImageBuildId, {
          kind: button.dataset.stage2ImageBuildKind,
          force: button.dataset.stage2ImageForce === "1",
        });
        return;
      }
      const row = event.target.closest("[data-stage2-image-asset-key]");
      if (!row) {
        return;
      }
      await openStage2ImageAssetDetail(row.dataset.stage2ImageAssetKey);
    });
    $("#asset-stage2-image-detail-body").addEventListener("click", async (event) => {
      const cancelButton = event.target.closest("[data-stage2-image-detail-cancel-id]");
      if (cancelButton) {
        await cancelStage2ImageAsset(cancelButton.dataset.stage2ImageDetailCancelId, {
          kind: cancelButton.dataset.stage2ImageDetailCancelKind,
        });
        return;
      }
      const button = event.target.closest("[data-stage2-image-detail-build-id]");
      if (!button) {
        return;
      }
      await buildStage2ImageAsset(button.dataset.stage2ImageDetailBuildId, {
        kind: button.dataset.stage2ImageDetailBuildKind,
        force: button.dataset.stage2ImageDetailForce === "1",
      });
    });
    $("#asset-stage2-image-detail-back").addEventListener("click", () => {
      closeStage2ImageAssetDetail();
    });
    document.querySelectorAll("[data-managed-image-sort]").forEach((button) => {
      button.addEventListener("click", () => {
        const field = String(button.dataset.managedImageSort || "");
        if (!field) {
          return;
        }
        if (state.managedImageSort.field === field) {
          state.managedImageSort.order = state.managedImageSort.order === "asc" ? "desc" : "asc";
        } else {
          state.managedImageSort.field = field;
          state.managedImageSort.order = managedImageSortDefaultOrder(field);
        }
        renderManagedImages();
      });
    });
    $("#asset-managed-image-body").addEventListener("click", async (event) => {
      const selectCheckbox = event.target.closest("[data-managed-image-select-ref]");
      if (selectCheckbox) {
        setManagedImageSelected(selectCheckbox.dataset.managedImageSelectRef, selectCheckbox.checked);
        updateManagedImageSelectionControls();
        return;
      }
      const button = event.target.closest("[data-managed-image-delete-ref]");
      if (!button) {
        return;
      }
      await deleteManagedImage(button.dataset.managedImageDeleteRef);
    });
    $("#asset-managed-image-select-all")?.addEventListener("change", (event) => {
      const checked = Boolean(event.target.checked);
      currentManagedImageRows().forEach((row) => {
        const imageRef = String(row?.image_ref || "");
        if (!imageRef) return;
        const deleteJob = row.delete_job || {};
        const deleting = Boolean(deleteJob.in_progress || state.managedImageDeleteInFlight[imageRef]);
        if (!deleting) {
          setManagedImageSelected(imageRef, checked);
        }
      });
      renderManagedImages();
    });
    $("#asset-managed-image-delete-selected")?.addEventListener("click", async () => {
      await deleteSelectedManagedImages();
    });
    $("#asset-managed-image-filter-toggle")?.addEventListener("click", (event) => {
      event.stopPropagation();
      const popover = $("#asset-managed-image-filter-popover");
      if (popover) {
        popover.hidden = !popover.hidden;
        if (!popover.hidden) {
          applyManagedImageFiltersToForm();
        }
      }
    });
    $("#asset-managed-image-filter-apply")?.addEventListener("click", () => {
      state.managedImageFilters = managedImageFilterValuesFromForm();
      closeManagedImageFilterPopover();
      renderManagedImages();
    });
    $("#asset-managed-image-filter-reset")?.addEventListener("click", () => {
      state.managedImageFilters = {
        image: "",
        lastUsedAfter: "",
        lastUsedBefore: "",
        createdAfter: "",
        createdBefore: "",
        sizeMinGb: "",
        sizeMaxGb: "",
      };
      applyManagedImageFiltersToForm();
      renderManagedImages();
    });
    $("#asset-managed-image-filter-popover")?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        state.managedImageFilters = managedImageFilterValuesFromForm();
        closeManagedImageFilterPopover();
        renderManagedImages();
      }
      if (event.key === "Escape") {
        closeManagedImageFilterPopover();
      }
    });
    $("#theme-toggle").addEventListener("click", () => {
      toggleTheme();
    });
    document.addEventListener("click", (event) => {
      const repoFilterAnchor = $("#repo-filter-anchor");
      if (!repoFilterAnchor.contains(event.target)) {
        closeRepoFilterPopover();
      }
      const crawlLanguageAnchor = $("#crawl-language-anchor");
      if (!crawlLanguageAnchor.contains(event.target)) {
        closeCrawlLanguagePopover();
      }
      const crawlLicenseAnchor = $("#crawl-license-anchor");
      if (!crawlLicenseAnchor.contains(event.target)) {
        closeCrawlLicensePopover();
      }
      const batchLanguageAnchor = $("#batch-language-anchor");
      if (batchLanguageAnchor && !batchLanguageAnchor.contains(event.target)) {
        closeBatchLanguagePopover();
      }
      const batchLicenseAnchor = $("#batch-license-anchor");
      if (batchLicenseAnchor && !batchLicenseAnchor.contains(event.target)) {
        closeBatchLicensePopover();
      }
      const repoFilterLanguageAnchor = $("#repo-filter-language-anchor");
      if (!repoFilterLanguageAnchor.contains(event.target)) {
        closeRepoFilterLanguagePopover();
      }
      const repoFilterLicenseAnchor = $("#repo-filter-license-anchor");
      if (!repoFilterLicenseAnchor.contains(event.target)) {
        closeRepoFilterLicensePopover();
      }
      const stage2FilterAnchor = $("#stage2-filter-anchor");
      if (!stage2FilterAnchor.contains(event.target)) {
        closeStage2FilterPopover();
      }
      const stage2FilterStatusAnchor = $("#stage2-filter-status-anchor");
      if (!stage2FilterStatusAnchor.contains(event.target)) {
        closeStage2FilterStatusPopover();
      }
      const stage2FilterLanguageAnchor = $("#stage2-filter-language-anchor");
      if (!stage2FilterLanguageAnchor.contains(event.target)) {
        closeStage2FilterLanguagePopover();
      }
      const stage2FilterLicenseAnchor = $("#stage2-filter-license-anchor");
      if (!stage2FilterLicenseAnchor.contains(event.target)) {
        closeStage2FilterLicensePopover();
      }
      const stage3FilterAnchor = $("#stage3-filter-anchor");
      if (!stage3FilterAnchor.contains(event.target)) {
        closeStage3FilterPopover();
      }
      const stage3FilterStatusAnchor = $("#stage3-filter-status-anchor");
      if (!stage3FilterStatusAnchor.contains(event.target)) {
        closeStage3FilterStatusPopover();
      }
      const stage3FilterLanguageAnchor = $("#stage3-filter-language-anchor");
      if (!stage3FilterLanguageAnchor.contains(event.target)) {
        closeStage3FilterLanguagePopover();
      }
      const stage3EntryFilterAnchor = $("#stage3-entry-filter-anchor");
      if (stage3EntryFilterAnchor && !stage3EntryFilterAnchor.contains(event.target)) {
        closeStage3EntryFilterPopover();
      }
      const stage3EntryFilterStatusAnchor = $("#stage3-entry-filter-status-anchor");
      if (stage3EntryFilterStatusAnchor && !stage3EntryFilterStatusAnchor.contains(event.target)) {
        closeStage3EntryFilterStatusPopover();
      }
      const stage4FilterAnchor = $("#stage4-filter-anchor");
      if (stage4FilterAnchor && !stage4FilterAnchor.contains(event.target)) {
        closeStage4FilterPopover();
      }
      const stage4FilterStatusAnchor = $("#stage4-filter-status-anchor");
      if (stage4FilterStatusAnchor && !stage4FilterStatusAnchor.contains(event.target)) {
        closeStage4FilterStatusPopover();
      }
      const stage4FilterLanguageAnchor = $("#stage4-filter-language-anchor");
      if (stage4FilterLanguageAnchor && !stage4FilterLanguageAnchor.contains(event.target)) {
        closeStage4FilterLanguagePopover();
      }
      const stage4SavepointFilterAnchor = $("#stage4-savepoint-filter-anchor");
      if (stage4SavepointFilterAnchor && !stage4SavepointFilterAnchor.contains(event.target)) {
        closeStage4SavepointFilterPopover();
      }
      const dataPoolFilterAnchor = $("#data-pool-filter-anchor");
      if (dataPoolFilterAnchor && !dataPoolFilterAnchor.contains(event.target)) {
        closeDataPoolFilterPopover();
      }
      const dataPoolSelectorAnchor = $("#data-pool-selector-anchor");
      if (dataPoolSelectorAnchor && !dataPoolSelectorAnchor.contains(event.target)) {
        closeDataPoolSelectorPopover();
      }
      const batchRepoFilterAnchor = $("#batch-repo-filter-anchor");
      if (batchRepoFilterAnchor && !batchRepoFilterAnchor.contains(event.target)) {
        closeBatchRepoFilterPopover();
      }
      const managedImageFilterAnchor = $("#asset-managed-image-filter-anchor");
      if (managedImageFilterAnchor && !managedImageFilterAnchor.contains(event.target)) {
        closeManagedImageFilterPopover();
      }
    });

    function seedDefaultDates() {
      const start = new Date("2022-01-01T00:00:00");
      document.querySelectorAll("#batch-form input[name=created_after], #crawl-form input[name=created_after]").forEach((input) => {
        input.value = localFromISO(start.toISOString());
      });
      document.querySelectorAll("#batch-form input[name=created_before], #crawl-form input[name=created_before]").forEach((input) => {
        input.value = "";
      });
      document.querySelectorAll("#batch-form input[name=pushed_after], #crawl-form input[name=pushed_after]").forEach((input) => {
        input.value = localFromISO(start.toISOString());
      });
      document.querySelectorAll("#batch-form input[name=pushed_before], #crawl-form input[name=pushed_before]").forEach((input) => {
        input.value = "";
      });
    }

    syncThemeToggle();
    const systemThemeQuery = window.matchMedia
      ? window.matchMedia("(prefers-color-scheme: dark)")
      : null;
    if (systemThemeQuery) {
      const handleSystemThemeChange = (event) => {
        if (!hasSavedThemePreference()) {
          applyTheme(event.matches ? "dark" : "light", { persist: false });
        }
      };
      if (typeof systemThemeQuery.addEventListener === "function") {
        systemThemeQuery.addEventListener("change", handleSystemThemeChange);
      } else if (typeof systemThemeQuery.addListener === "function") {
        systemThemeQuery.addListener(handleSystemThemeChange);
      }
    }
    seedDefaultDates();
    restoreFilterState();
    restoreCrawlFormDraft();
    state.batchFormCollapsed = readBatchLayoutPreference();
    switchTab("batch");
    hideJobDetail();
    hideStage2Detail();
    hideStage3Detail();
    hideDataPoolAssetDetail({ clearSelection: true });
    syncCrawlLanguageInputs();
    updateCrawlLanguageToggle();
    syncCrawlLicenseInputs();
    updateCrawlLicenseToggle();
    syncCrawlTargetRepositoryMode();
    syncBatchLanguageInputs();
    updateBatchLanguageToggle();
    syncBatchLicenseInputs();
    updateBatchLicenseToggle();
    syncBatchTargetRepositoryMode();
    syncRepoFilterForm();
    updateRepoFilterToggle();
    syncStage2FilterForm();
    updateStage2FilterToggle();
    syncStage3FilterForm();
    updateStage3FilterToggle();
    updateBatchLayout();
    updateStage2Layout();
    updateStage2ConfigExpansion();
    updateStage3Layout();
    updateStage3ConfigExpansion();
    updateStage4ConfigExpansion();
    loadStage1CrawlTemplates();
    loadStage2RuntimeTemplates();
    loadStage3RuntimeTemplates();
    loadStage4RuntimeTemplates();
    loadRuntimeConfig();
    loadStage2RuntimeConfig();
    loadStage3RuntimeConfig();
    loadStage4RuntimeConfig();
    refreshAll().catch((error) => {
      $("#refresh-meta").textContent = `最近刷新失败 ${error.message || String(error)}`;
    });
    setInterval(refreshAllIfNeeded, 5000);
    setInterval(refreshSdkPrewarmIfNeeded, 5000);
    setInterval(refreshAssetImagesIfNeeded, 10000);
    setInterval(refreshManagedImagesIfNeeded, 10000);
    setInterval(refreshAssetImageDetailIfNeeded, 2000);
    setInterval(refreshStage3RepositoryImagePrewarmIfNeeded, 2000);
