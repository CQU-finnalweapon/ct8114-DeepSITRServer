<template>
  <section class="result-stack">
    <template v-if="resultView === 'defects'">
    <div class="tool-card">
      <div class="card-head">
        <div>
          <h2>分析汇总</h2>
          <p>{{ report?.message || "等待分析结果" }}</p>
        </div>
        <div class="report-actions">
          <button
            class="btn btn-secondary"
            type="button"
            :disabled="!report"
            @click="exportPdf"
          >
            打印PDF报告
          </button>
          <button
            class="btn btn-primary"
            type="button"
            :disabled="!report"
            @click="exportJson"
          >
            导出 JSON 原始结果
          </button>
        </div>
      </div>
      <div class="card-body stack">
        <div class="stats-grid">
          <div class="stat-card">
            <span>问题总数</span>
            <strong>{{ report?.summary.total ?? "-" }}</strong>
          </div>
          <div class="stat-card warn">
            <span>Warning</span>
            <strong>{{ report?.summary.warning ?? "-" }}</strong>
          </div>
          <div class="stat-card error">
            <span>Error</span>
            <strong>{{ report?.summary.error ?? "-" }}</strong>
          </div>
          <div class="stat-card">
            <span>其他/未知</span>
            <strong>{{ otherUnknownCount }}</strong>
          </div>
          <div class="stat-card">
            <span>规则种类</span>
            <strong>{{ report?.summary.ruleCount ?? "-" }}</strong>
          </div>
          <div class="stat-card">
            <span>文件数量</span>
            <strong>{{ report?.summary.fileCount ?? "-" }}</strong>
          </div>
        </div>

        <div class="result-meta">
          <span class="badge">状态：{{ report?.status || "未开始" }}</span>
          <span v-if="report?.requestId" class="badge">request_id：{{ report.requestId }}</span>
          <span v-if="selectedRuleSet" class="badge badge-blue">已选规则集：{{ selectedRuleSet }}</span>
          <span v-if="selectedRuleCountRaw" class="badge">规则总数：{{ selectedRuleCountRaw }}</span>
          <span v-if="engineRuleCount" class="badge">实际下发规则：{{ engineRuleCount }}</span>
          <span v-if="filteredDocumentRuleCount" class="badge">D 类过滤：{{ filteredDocumentRuleCount }}</span>
          <span v-if="analysisFilesText" class="badge">分析文件：{{ analysisFilesText }}</span>
          <span v-if="resultRuleSetsText" class="badge">结果规则集：{{ resultRuleSetsText }}</span>
          <span v-if="hitRuleCount" class="badge">实际命中规则：{{ hitRuleCount }}</span>
          <span v-if="isDebug && report?.detectionId" class="badge badge-blue">detection_id：{{ report.detectionId }}</span>
          <span
            v-if="report?.uniportalWriteback === 'ok'"
            class="badge badge-green"
            title="分析报告已写回 UniPortal 共享卷"
          >已写回共享卷</span>
          <span
            v-else-if="report?.uniportalWriteback === 'no'"
            class="badge"
            title="直接上传属于临时分析，不写回 UniPortal 共享卷"
          >临时分析，不写回共享卷</span>
          <span
            v-else-if="report?.uniportalWriteback"
            class="badge badge-red"
            :title="report.uniportalWriteback"
          >共享卷写回失败</span>
          <span
            v-if="report?.raw?.uniportal_writeback_path"
            class="badge"
            :title="report.raw.uniportal_writeback_path"
          >{{ shortWritebackPath(report.raw.uniportal_writeback_path) }}</span>
          <span
            v-if="report?.raw?.saved_project_id"
            class="badge badge-blue"
            title="上传项目已加入本工具项目库，刷新项目库可见"
          >已加入项目库：{{ report.raw.saved_project_id }}</span>
        </div>

        <div class="filters">
          <label class="field">
            <span>搜索</span>
            <input
              v-model.trim="keyword"
              class="input"
              placeholder="搜索规则、检查器、文件、严重程度或描述"
            />
          </label>
          <label class="field">
            <span>严重程度筛选</span>
            <select v-model="severityFilter" class="input">
              <option value="">全部</option>
              <option value="Warning">Warning</option>
              <option value="Error">Error</option>
              <option value="OtherUnknown">其他/未知</option>
            </select>
          </label>
          <label class="field">
            <span>规则筛选</span>
            <select v-model="ruleFilter" class="input">
              <option value="">全部规则</option>
              <option v-for="rule in rules" :key="rule" :value="rule">
                {{ rule }}
              </option>
            </select>
          </label>
        </div>
      </div>
    </div>

    <div v-if="report" class="tool-card">
      <div class="card-head">
        <div>
          <h2>Report summary</h2>
          <p>total_bugs={{ rawSummary.total_bugs ?? report.summary.total }}, total_files={{ rawSummary.total_files ?? report.summary.fileCount }}</p>
        </div>
      </div>
      <div class="card-body summary-grid">
        <div>
          <h3>by_severity</h3>
          <div v-if="bySeverityEntries.length" class="mini-list">
            <span v-for="[key, value] in bySeverityEntries" :key="key">
              <strong>{{ severityName(key) }}</strong>
              <em>{{ value }}</em>
            </span>
          </div>
          <div v-else class="empty-inline">No severity summary</div>
        </div>
        <div>
          <h3>by_level</h3>
          <div v-if="byLevelEntries.length" class="mini-list">
            <span v-for="[key, value] in byLevelEntries" :key="key">
              <strong>{{ key }}</strong>
              <em>{{ value }}</em>
            </span>
          </div>
          <div v-else class="empty-inline">No level summary</div>
        </div>
        <div>
          <h3>by_rule</h3>
          <div v-if="byRuleEntries.length" class="mini-list">
            <span v-for="[key, value] in visibleByRuleEntries" :key="key">
              <strong>{{ key }}</strong>
              <em>{{ value }}</em>
            </span>
            <button
              v-if="byRuleEntries.length > 10"
              class="btn btn-secondary btn-small"
              type="button"
              @click="showAllRules = !showAllRules"
            >
              {{ showAllRules ? "收起" : `展开全部 ${byRuleEntries.length} 项` }}
            </button>
          </div>
          <div v-else class="empty-inline">No rule summary</div>
        </div>
      </div>
    </div>

    <div v-if="report" class="tool-card">
      <div class="card-head">
        <div>
          <h2>files_stats</h2>
          <p>{{ filesStats.length }} files</p>
        </div>
      </div>
      <div class="card-body">
        <div v-if="!filesStats.length" class="empty-state">No file stats</div>
        <div v-else class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>file_path</th>
                <th>lines</th>
                <th>statements</th>
                <th>functions</th>
                <th>comments</th>
                <th>bugs</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="file in filesStats" :key="asText(file.file_path)">
                <td class="path-cell" :title="asText(file.file_path)">{{ asText(file.file_path) || '-' }}</td>
                <td class="mono">{{ statValue(file.total_lines) }}</td>
                <td class="mono">{{ statValue(file.total_statements) }}</td>
                <td class="mono">{{ statValue(file.function_count) }}</td>
                <td class="mono">{{ statValue(file.comment_lines) }}</td>
                <td class="mono">{{ file.bug_count ?? file.bugs?.length ?? 0 }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <div class="tool-card">
      <div class="card-head">
        <div>
          <h2>缺陷列表</h2>
          <p>当前显示 {{ filteredDiagnostics.length }} 条</p>
        </div>
      </div>
      <div class="card-body">
        <div v-if="!report" class="empty-state">
          暂无结果，请先从左侧入口开始分析或加载报告。
        </div>
        <div v-else-if="report.diagnostics.length === 0" class="empty-state">
          {{ report.message || "分析完成，未发现缺陷" }}
        </div>
        <div v-else-if="filteredDiagnostics.length === 0" class="empty-state">
          没有匹配的缺陷，请调整搜索或筛选条件。
        </div>
        <div v-else class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>严重程度</th>
                <th>规则</th>
                <th>文件</th>
                <th>行/列</th>
                <th>描述</th>
                <th>检查器</th>
                <th v-if="sourceContext">源码</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in filteredDiagnostics" :key="item.id">
                <td>
                  <span class="badge" :class="severityBadgeClass(item.severity)">{{ item.level }}</span>
                  <div class="mono compact-meta">
                    force={{ item.force || "-" }} / type={{ item.typeCode || "-" }} / status={{ item.status || "-" }}
                  </div>
                </td>
                <td class="mono">{{ item.ruleId }}</td>
                <td class="path-cell" :title="item.filePath">
                  {{ shortPath(item.filePath) || "-" }}
                </td>
                <td class="mono">
                  {{ item.line || "?" }}:{{ item.column || "?" }}
                </td>
                <td>{{ item.message || "-" }}</td>
                <td class="mono">{{ item.checker || "-" }}</td>
                <td v-if="sourceContext">
                  <button
                    class="btn btn-secondary btn-small"
                    type="button"
                    :disabled="!item.filePath"
                    @click="openSourceForDiagnostic(item)"
                  >
                    定位源码
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <details v-if="isDebug" class="raw-json">
      <summary>原始 JSON</summary>
      <pre>{{ prettyRaw }}</pre>
    </details>
    </template>

    <template v-else>
      <div class="tool-card source-workspace-card">
        <div class="source-toolbar">
          <button class="btn btn-secondary" type="button" @click="returnToDefects">
            返回缺陷列表
          </button>
          <div class="source-toolbar-main">
            <h2>{{ sourcePayload?.file_path || sourceTitle || "源码查看" }}</h2>
            <p>
              <span v-if="targetLine">第 {{ targetLine }} 行，第 {{ targetColumn || 1 }} 列</span>
              <span v-else>未定位到具体行列</span>
              <span> / bugs {{ sourcePayload?.bugs.length || 0 }}</span>
              <span> / functions {{ sourcePayload?.functions.length || 0 }}</span>
            </p>
            <p v-if="targetLine && matchedFunction" class="source-function-meta">
              所在函数：{{ matchedFunction.name || "(anonymous)" }}，{{ matchedFunction.start_line }}-{{ matchedFunction.end_line }} 行
            </p>
            <p v-else-if="targetLine" class="source-function-meta">
              未匹配到函数范围，可能位于全局变量、宏、文件头或函数外部。
            </p>
          </div>
        </div>

        <div class="source-workspace">
          <aside class="source-sidebar source-file-sidebar">
            <div class="source-sidebar-head">
              <strong>源码文件</strong>
              <span>{{ sourceFiles.length }} files</span>
            </div>
            <button
              v-for="file in sourceFiles"
              :key="file.file_path"
              class="source-file-item"
              :class="{ active: sourcePayload?.file_path === file.file_path, 'has-bugs': (file.bug_count || 0) > 0 }"
              type="button"
              :title="file.file_path"
              @click="openSource(file.file_path)"
            >
              <span :title="file.file_path">{{ file.file_path }}</span>
              <em>{{ file.bug_count || 0 }} / {{ file.function_count || 0 }}</em>
            </button>
          </aside>

          <main class="source-main">
            <p v-if="sourceLoading" class="status">正在加载源码...</p>
            <p v-else-if="sourceError" class="status error">{{ sourceError }}</p>
            <div v-else-if="sourcePayload" ref="sourcePanelRef" class="source-viewer source-viewer-large">
              <div
                v-for="line in sourcePayload.lines"
                :key="line.line"
                class="source-line"
                :class="sourceLineClass(line.line)"
                :data-line="line.line"
              >
                <span class="source-gutter">
                  <span v-if="line.line === targetLine" class="source-target-label">定位</span>
                  <span v-else-if="bugLineMap.has(line.line)" class="source-bug-dot"></span>
                  <span v-else></span>
                  <span>{{ line.line }}</span>
                </span>
                <code>{{ line.text || " " }}</code>
              </div>
            </div>
            <div v-else class="empty-state">请选择源码文件。</div>
          </main>

          <aside class="source-sidebar source-info-sidebar">
            <div class="source-sidebar-head">
              <strong>当前文件</strong>
              <span>{{ sourcePayload?.encoding || "" }}</span>
            </div>
            <div class="source-info-block">
              <h3>缺陷</h3>
              <div class="source-bug-filters">
                <input
                  v-model.trim="sourceBugKeyword"
                  class="input"
                  placeholder="搜索 rule / line / message"
                />
                <select v-model="sourceBugLevelFilter" class="input">
                  <option value="">全部</option>
                  <option value="Error">Error</option>
                  <option value="Warning">Warning</option>
                </select>
              </div>
              <button
                v-for="bug in filteredSourceBugs"
                :key="bugKey(bug)"
                class="source-info-item"
                type="button"
                @click="focusBug(bug)"
              >
                <span>{{ bug.line || "?" }}:{{ bug.column || "?" }} {{ bug.level || "" }}</span>
                <small>{{ bug.rule_id || bug.checker || bug.message || "-" }}</small>
              </button>
              <p v-if="!sourceBugs.length" class="empty-inline">无缺陷</p>
              <p v-else-if="!filteredSourceBugs.length" class="empty-inline">无匹配缺陷</p>
            </div>
            <div class="source-info-block">
              <h3>函数</h3>
              <button
                v-for="fn in sourceFunctions"
                :key="functionKey(fn)"
                class="source-info-item"
                :class="{ active: activeFunction === fn }"
                type="button"
                @click="focusFunction(fn)"
              >
                <span>{{ fn.name || "(anonymous)" }}</span>
                <small>{{ fn.start_line }}-{{ fn.end_line }} 行</small>
              </button>
              <p v-if="!sourceFunctions.length" class="empty-inline">无函数信息</p>
            </div>
          </aside>
        </div>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import {
  fetchProjectSource,
  fetchProjectSourceFiles,
  type ProjectSourceContext,
  type ProjectSourceFile,
  type ProjectSourcePayload,
} from "../api/codeAnalysis";
import type { NormalizedDiagnostic, NormalizedReport } from "../utils/normalizeReport";

const props = defineProps<{
  report: NormalizedReport | null;
  sourceName?: string;
  sourceContext?: ProjectSourceContext | null;
  sourceRequest?: (ProjectSourceContext & { filePath: string; line?: number; column?: number; key: number }) | null;
  sourceFiles?: ProjectSourceFile[];
}>();

const keyword = ref("");
const severityFilter = ref<"" | "Warning" | "Error" | "OtherUnknown">("");
const ruleFilter = ref("");
const showAllRules = ref(false);
const isDebug = new URLSearchParams(window.location.search).get("debug") === "1";
const sourcePanelRef = ref<HTMLElement | null>(null);
const sourceLoading = ref(false);
const sourceError = ref("");
const sourcePayload = ref<ProjectSourcePayload | null>(null);
const sourceTitle = ref("");
const resultView = ref<"defects" | "source">("defects");
const targetLine = ref<number | null>(null);
const targetColumn = ref<number | null>(null);
const activeFunction = ref<any | null>(null);
const loadedSourceFiles = ref<ProjectSourceFile[]>([]);
const sourceBugKeyword = ref("");
const sourceBugLevelFilter = ref<"" | "Error" | "Warning">("");

watch(
  () => props.report,
  () => {
    keyword.value = "";
    severityFilter.value = "";
    ruleFilter.value = "";
    showAllRules.value = false;
    sourcePayload.value = null;
    sourceError.value = "";
    sourceTitle.value = "";
    resultView.value = "defects";
    targetLine.value = null;
    targetColumn.value = null;
    activeFunction.value = null;
    loadedSourceFiles.value = [];
    sourceBugKeyword.value = "";
    sourceBugLevelFilter.value = "";
  },
);

watch(
  () => props.sourceFiles,
  (files) => {
    loadedSourceFiles.value = files || [];
  },
  { immediate: true },
);

watch(
  () => props.sourceRequest,
  (request) => {
    if (!request) return;
    void openSource(request.filePath, request.line, request.column);
  },
);

const rules = computed(() => {
  const set = new Set(
    (props.report?.diagnostics || [])
      .map((item) => item.ruleId)
      .filter(Boolean),
  );
  return [...set].sort();
});

const filteredDiagnostics = computed(() => {
  const kw = keyword.value.toLowerCase();
  return (props.report?.diagnostics || []).filter((item) => {
    if (severityFilter.value === "Warning" && item.level !== "Warning") return false;
    if (severityFilter.value === "Error" && item.level !== "Error") return false;
    if (severityFilter.value === "OtherUnknown" && item.level !== "Other" && item.level !== "Unknown") return false;
    if (ruleFilter.value && item.ruleId !== ruleFilter.value) return false;
    if (!kw) return true;
    return [
      item.ruleId,
      item.checker,
      item.message,
      item.filePath,
      item.level,
      item.severity,
      item.severityLabel,
      item.force,
      item.typeCode,
      item.status,
    ]
      .join(" ")
      .toLowerCase()
      .includes(kw);
  });
});

const prettyRaw = computed(() =>
  props.report ? JSON.stringify(props.report.raw, null, 2) : "{}",
);

const reportBody = computed(() => props.report?.raw?.report || {});
const rawSummary = computed(() => reportBody.value?.summary || {});
const filesStats = computed<any[]>(() =>
  Array.isArray(reportBody.value?.files_stats)
    ? reportBody.value.files_stats
    : [],
);
const bySeverityEntries = computed<[string, number][]>(() =>
  Object.entries(rawSummary.value?.by_severity || {}).map(([key, value]) => [
    key,
    Number(value) || 0,
  ]),
);
const byLevelEntries = computed<[string, number][]>(() =>
  Object.entries(rawSummary.value?.by_level || {}).map(([key, value]) => [
    key,
    Number(value) || 0,
  ]),
);
const byRuleEntries = computed<[string, number][]>(() =>
  Object.entries(rawSummary.value?.by_rule || {})
    .map(([key, value]) => [key, Number(value) || 0] as [string, number])
    .sort((a, b) => b[1] - a[1]),
);
const visibleByRuleEntries = computed<[string, number][]>(() =>
  showAllRules.value ? byRuleEntries.value : byRuleEntries.value.slice(0, 10),
);
const selectedRuleSet = computed(() =>
  String(props.report?.raw?.selected_rule_set || props.report?.raw?.rule_standard || ""),
);
const selectedRuleCountRaw = computed(() =>
  Number(props.report?.raw?.selected_rule_count_raw || 0),
);
const engineRuleCount = computed(() =>
  Number(
    props.report?.raw?.engine_rule_count ||
      props.report?.raw?.rule_ids_count ||
      rawSummary.value?.enabled_rule_count ||
      0,
  ),
);
const filteredDocumentRuleCount = computed(() =>
  Number(props.report?.raw?.filtered_document_rule_count || 0),
);
const analysisFilesText = computed(() => {
  const submitted = props.report?.raw?.submitted_analysis_files_count;
  const completed = props.report?.raw?.completed_analysis_files_count;
  if (submitted == null && completed == null) return "";
  return `${completed ?? "-"} / ${submitted ?? "-"}`;
});
const resultRuleSetsText = computed(() => {
  const value = props.report?.raw?.result_rule_sets;
  return Array.isArray(value) ? value.join(", ") : "";
});
const hitRuleCount = computed(() =>
  Number(rawSummary.value?.hit_rule_count || props.report?.summary.ruleCount || 0),
);
const otherUnknownCount = computed(() => {
  if (!props.report) return "-";
  return props.report.summary.other + props.report.summary.unknown;
});
const bugLineMap = computed(() => {
  const lines = new Map<number, string>();
  (sourcePayload.value?.bugs || []).forEach((bug) => {
    const line = Number(bug?.line);
    if (!Number.isFinite(line) || line <= 0) return;
    const level = String(bug?.level || "").toLowerCase();
    lines.set(line, level.includes("error") ? "error" : "warning");
  });
  return lines;
});
const sourceFiles = computed(() => loadedSourceFiles.value);
const sourceBugs = computed(() => sourcePayload.value?.bugs || []);
const filteredSourceBugs = computed(() => {
  const keywordText = sourceBugKeyword.value.toLowerCase();
  return sourceBugs.value.filter((bug) => {
    const level = String(bug?.level || "");
    if (sourceBugLevelFilter.value && level !== sourceBugLevelFilter.value) {
      return false;
    }
    if (!keywordText) return true;
    return [
      bug?.rule_id,
      bug?.checker,
      bug?.message,
      bug?.line,
      bug?.column,
      bug?.level,
    ]
      .join(" ")
      .toLowerCase()
      .includes(keywordText);
  });
});
const sourceFunctions = computed(() => sourcePayload.value?.functions || []);
const matchedFunction = computed(() => {
  if (activeFunction.value) return activeFunction.value;
  if (!targetLine.value) return null;
  return sourceFunctions.value
    .filter((fn) => {
      const start = Number(fn?.start_line);
      const end = Number(fn?.end_line);
      return Number.isFinite(start) && Number.isFinite(end) && start <= targetLine.value! && targetLine.value! <= end;
    })
    .sort((a, b) => (Number(a.end_line) - Number(a.start_line)) - (Number(b.end_line) - Number(b.start_line)))[0] || null;
});

function asText(value: unknown) {
  if (typeof value === "string") return value;
  if (value === undefined || value === null) return "";
  return JSON.stringify(value);
}

function statValue(value: unknown) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? String(n) : "-";
}

function shortPath(path: string) {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.slice(-2).join("/") || path;
}

function shortWritebackPath(path: string) {
  const parts = path.split(/[\\/]/).filter(Boolean);
  const key = parts.slice(-3).join("/");
  return key.length > 40 ? "..." + key.slice(-37) : key;
}

function severityName(value: string) {
  if (value === "advisory") return "Warning";
  if (value === "required") return "Error";
  if (value === "other") return "Other";
  if (value === "unknown") return "Unknown";
  return value;
}

function severityBadgeClass(value: string) {
  if (value === "required") return "badge-red";
  if (value === "advisory") return "badge-yellow";
  if (value === "other") return "badge-blue";
  return "";
}

function returnToDefects() {
  resultView.value = "defects";
}

async function openSource(filePath: string, line?: number, column?: number) {
  if (!props.sourceContext) return;
  resultView.value = "source";
  sourceLoading.value = true;
  sourceError.value = "";
  sourceTitle.value = filePath;
  targetLine.value = line && line > 0 ? line : null;
  targetColumn.value = column && column > 0 ? column : null;
  activeFunction.value = null;
  try {
    if (!loadedSourceFiles.value.length) {
      const filesData = await fetchProjectSourceFiles(
        props.sourceContext.projectId,
        props.sourceContext.portalProjectId,
      );
      loadedSourceFiles.value = filesData.files || [];
    }
    sourcePayload.value = await fetchProjectSource(
      props.sourceContext.projectId,
      props.sourceContext.portalProjectId,
      filePath,
      line,
      column,
    );
    activeFunction.value = matchedFunction.value;
    await nextTick();
    scrollToSourceLine(targetLine.value || undefined);
  } catch (error) {
    sourcePayload.value = null;
    sourceError.value = error instanceof Error ? error.message : String(error);
  } finally {
    sourceLoading.value = false;
  }
}

function openSourceForDiagnostic(item: NormalizedDiagnostic) {
  void openSource(item.filePath, item.line, item.column);
}

function scrollToSourceLine(line?: number) {
  if (!line || !sourcePanelRef.value) return;
  const target = sourcePanelRef.value.querySelector<HTMLElement>(
    `[data-line="${line}"]`,
  );
  target?.scrollIntoView({ block: "center" });
}

function sourceLineClass(line: number) {
  const fn = matchedFunction.value;
  const inFunction =
    fn &&
    Number(fn.start_line) <= line &&
    line <= Number(fn.end_line);
  return {
    "source-line-target": targetLine.value === line,
    "source-line-function": !!inFunction,
    "source-line-bug": bugLineMap.value.has(line),
    "source-line-error": bugLineMap.value.get(line) === "error",
  };
}

function focusBug(bug: any) {
  const line = Number(bug?.line);
  const column = Number(bug?.column);
  targetLine.value = Number.isFinite(line) && line > 0 ? line : null;
  targetColumn.value = Number.isFinite(column) && column > 0 ? column : null;
  activeFunction.value = null;
  activeFunction.value = matchedFunction.value;
  void nextTick(() => scrollToSourceLine(targetLine.value || undefined));
}

function focusFunction(fn: any) {
  activeFunction.value = fn;
  const start = Number(fn?.start_line);
  if (Number.isFinite(start) && start > 0) {
    void nextTick(() => scrollToSourceLine(start));
  }
}

function bugKey(bug: any) {
  return [bug?.line, bug?.column, bug?.rule_id, bug?.checker, bug?.message].join("-");
}

function functionKey(fn: any) {
  return [fn?.name, fn?.start_line, fn?.end_line].join("-");
}

function valueOrDash(value: unknown) {
  if (value === undefined || value === null || value === "") return "-";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "-";
  return String(value);
}

function numberOrZero(value: unknown) {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? numberValue : 0;
}

function escapeHtml(value: unknown) {
  return valueOrDash(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function safeReportName() {
  return (
    props.sourceName ||
    props.report?.requestId ||
    "report"
  ).replace(/[^\w.-]+/g, "_");
}

function rawReportBody() {
  return props.report?.raw?.report || props.report?.raw || {};
}

function rawReportSummary() {
  return rawReportBody()?.summary || props.report?.raw?.summary || {};
}

function reportMetaValue(key: string, fallback?: unknown) {
  return props.report?.raw?.[key] ?? rawReportBody()?.[key] ?? fallback;
}

function tableRows(rows: string[][]) {
  return rows
    .map(
      (row) =>
        "<tr>" +
        row.map((cell) => `<td>${escapeHtml(cell)}</td>`).join("") +
        "</tr>",
    )
    .join("");
}

function levelRank(level: string) {
  const value = level.toLowerCase();
  if (value === "error") return 0;
  if (value === "warning") return 1;
  return 2;
}

function compareText(a: string, b: string) {
  return a.localeCompare(b, "zh-CN", { numeric: true, sensitivity: "base" });
}

function sortedDiagnostics() {
  return [...(props.report?.diagnostics || [])].sort((a, b) => {
    const levelDiff = levelRank(a.level || "") - levelRank(b.level || "");
    if (levelDiff) return levelDiff;
    const checkerDiff = compareText(a.checker || "", b.checker || "");
    if (checkerDiff) return checkerDiff;
    const fileDiff = compareText(a.filePath || "", b.filePath || "");
    if (fileDiff) return fileDiff;
    const lineDiff = numberOrZero(a.line) - numberOrZero(b.line);
    if (lineDiff) return lineDiff;
    return numberOrZero(a.column) - numberOrZero(b.column);
  });
}

function fileStatRows() {
  const body = rawReportBody();
  const summary = rawReportSummary();
  const byFile = summary?.by_file || body?.by_file || {};
  if (byFile && typeof byFile === "object" && !Array.isArray(byFile)) {
    const rows = Object.entries(byFile).map(([filePath, count]) => [
      filePath,
      String(numberOrZero(count)),
    ]);
    if (rows.length) return tableRows(rows);
  }

  if (filesStats.value.length) {
    return tableRows(
      filesStats.value.map((file) => [
        valueOrDash(file?.file_path),
        String(numberOrZero(file?.bug_count ?? file?.bugs?.length)),
      ]),
    );
  }

  const counts = new Map<string, number>();
  (props.report?.diagnostics || []).forEach((item) => {
    const filePath = item.filePath || "-";
    counts.set(filePath, (counts.get(filePath) || 0) + 1);
  });
  return tableRows([...counts.entries()].map(([filePath, count]) => [filePath, String(count)]));
}

function ruleStatRows() {
  const groups = new Map<string, { checker: string; ruleId: string; level: string; count: number }>();
  (props.report?.diagnostics || []).forEach((item) => {
    const checker = item.checker || "-";
    const ruleId = item.ruleId || "-";
    const level = item.level || "-";
    const key = `${checker}\n${ruleId}\n${level}`;
    const current = groups.get(key);
    if (current) {
      current.count += 1;
    } else {
      groups.set(key, { checker, ruleId, level, count: 1 });
    }
  });

  if (groups.size) {
    return tableRows(
      [...groups.values()]
        .sort((a, b) => b.count - a.count)
        .map((item) => [item.checker, item.ruleId, item.level, String(item.count)]),
    );
  }

  const summary = rawReportSummary();
  const byChecker = summary?.by_checker || rawReportBody()?.by_checker || {};
  const byRule = summary?.by_rule || {};
  const source = Object.keys(byChecker).length ? byChecker : byRule;
  return tableRows(
    Object.entries(source).map(([key, count]) => [
      key,
      key,
      "-",
      String(numberOrZero(count)),
    ]),
  );
}

function diagnosticRows() {
  return sortedDiagnostics()
    .map((item, index) => {
      const line = numberOrZero(item.line);
      const column = numberOrZero(item.column);
      const locationText = line
        ? column
          ? `第 ${line} 行，第 ${column} 列`
          : `第 ${line} 行`
        : "-";
      return `<tr>
        <td class="col-index">${escapeHtml(index + 1)}</td>
        <td class="col-level">${escapeHtml(item.level)}</td>
        <td class="col-rule">${escapeHtml(item.checker)}</td>
        <td class="col-location">
          <div class="file-path">${escapeHtml(item.filePath)}</div>
          <div class="line-position">${escapeHtml(locationText)}</div>
        </td>
        <td class="col-message">${escapeHtml(item.message)}</td>
      </tr>`;
    })
    .join("");
}

function makePrintReportHtml() {
  const report = props.report;
  if (!report) return "";

  const summary = rawReportSummary();
  const resultRuleSets = reportMetaValue("result_rule_sets");
  const generatedAt = new Date().toLocaleString();
  const projectName = props.sourceName || reportMetaValue("project_name", "-");
  const projectId = reportMetaValue(
    "project_id",
    reportMetaValue("saved_project_id", reportMetaValue("portal_project_id", "-")),
  );
  const selectedRuleSet = reportMetaValue("selected_rule_set", reportMetaValue("rule_standard", "-"));
  const totalBugs = summary?.total_bugs ?? report.summary.total;
  const totalFiles = summary?.total_files ?? report.summary.fileCount;
  const selectedRuleCount = reportMetaValue("selected_rule_count", "-");
  const selectedRuleCountRaw = reportMetaValue("selected_rule_count_raw", "-");
  const engineRuleCount = reportMetaValue(
    "engine_rule_count",
    reportMetaValue("rule_ids_count", summary?.enabled_rule_count ?? "-"),
  );
  const filteredDocumentRuleCount = reportMetaValue("filtered_document_rule_count", 0);
  const submittedFiles = reportMetaValue("submitted_analysis_files_count", "-");
  const completedFiles = reportMetaValue("completed_analysis_files_count", "-");
  const engine = reportMetaValue("engine", reportMetaValue("analysis_engine", reportMetaValue("dcab_engine", "-")));
  const hitRuleCountValue = summary?.hit_rule_count ?? report.summary.ruleCount;

  const configRows = tableRows([
    ["selected_rule_set", valueOrDash(selectedRuleSet)],
    ["selected_rule_count_raw", valueOrDash(selectedRuleCountRaw)],
    ["selected_rule_count", valueOrDash(selectedRuleCount)],
    ["engine_rule_count", valueOrDash(engineRuleCount)],
    ["filtered_document_rule_count", valueOrDash(filteredDocumentRuleCount)],
    ["submitted_analysis_files_count", valueOrDash(submittedFiles)],
    ["completed_analysis_files_count", valueOrDash(completedFiles)],
    ["result_rule_sets", valueOrDash(resultRuleSets)],
  ]);

  const overviewRows = tableRows([
    ["total_bugs", String(numberOrZero(totalBugs))],
    ["total_files", String(numberOrZero(totalFiles))],
    ["Error", String(numberOrZero(report.summary.error))],
    ["Warning", String(numberOrZero(report.summary.warning))],
    ["Other", String(numberOrZero(report.summary.other))],
    ["Unknown", String(numberOrZero(report.summary.unknown))],
    ["hit_rule_count", String(numberOrZero(hitRuleCountValue))],
  ]);

  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <title>静态分析报告</title>
  <style>
    @page { size: A4; margin: 16mm 14mm; }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: #111827;
      font: 11px/1.5 "Microsoft YaHei", "PingFang SC", "Segoe UI", Arial, sans-serif;
      background: #fff;
    }
    h1 { margin: 0 0 10px; font-size: 24px; }
    h2 { margin: 22px 0 8px; font-size: 16px; border-bottom: 1px solid #111827; padding-bottom: 5px; }
    p { margin: 4px 0; }
    .meta { display: grid; grid-template-columns: 110px 1fr; gap: 4px 12px; margin-top: 10px; }
    .label { color: #4b5563; font-weight: 700; }
    table { width: 100%; border-collapse: collapse; margin-top: 8px; page-break-inside: auto; table-layout: fixed; }
    thead { display: table-header-group; }
    tr { page-break-inside: avoid; page-break-after: auto; }
    th, td { border: 1px solid #9ca3af; padding: 5px 6px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }
    th { background: #f3f4f6; font-weight: 700; }
    .mono { font-family: Consolas, "SFMono-Regular", Menlo, monospace; }
    .muted { color: #6b7280; }
    .defect-table { font-size: 10px; }
    .defect-table .col-index { width: 7%; text-align: center; white-space: nowrap; }
    .defect-table .col-level { width: 9%; white-space: nowrap; }
    .defect-table .col-rule { width: 21%; overflow-wrap: break-word; word-break: normal; }
    .defect-table .col-location { width: 25%; }
    .defect-table .col-message { width: 38%; }
    .file-path { overflow-wrap: anywhere; }
    .line-position { margin-top: 3px; color: #4b5563; white-space: nowrap; }
  </style>
</head>
<body>
  <h1>静态分析报告</h1>
  <div class="meta">
    <span class="label">项目名称</span><span>${escapeHtml(projectName)}</span>
    <span class="label">项目 ID</span><span>${escapeHtml(projectId)}</span>
    <span class="label">分析工具</span><span>CT8114 静态分析工具</span>
    <span class="label">分析引擎</span><span>${escapeHtml(engine)}</span>
    <span class="label">规则集</span><span>${escapeHtml(selectedRuleSet)}</span>
    <span class="label">报告生成时间</span><span>${escapeHtml(generatedAt)}</span>
    <span class="label">request_id</span><span class="mono">${escapeHtml(report.requestId)}</span>
  </div>

  <h2>一、分析配置摘要</h2>
  <table><thead><tr><th>字段</th><th>值</th></tr></thead><tbody>${configRows}</tbody></table>

  <h2>二、分析结果概览</h2>
  <table><thead><tr><th>字段</th><th>数量</th></tr></thead><tbody>${overviewRows}</tbody></table>

  <h2>三、文件问题统计</h2>
  <table><thead><tr><th>file_path</th><th>bugs</th></tr></thead><tbody>${fileStatRows() || '<tr><td colspan="2" class="muted">无文件统计</td></tr>'}</tbody></table>

  <h2>四、规则命中统计</h2>
  <table><thead><tr><th>checker</th><th>rule_id</th><th>level</th><th>count</th></tr></thead><tbody>${ruleStatRows() || '<tr><td colspan="4" class="muted">无规则统计</td></tr>'}</tbody></table>

  <h2>五、缺陷明细</h2>
  <table class="defect-table">
    <thead><tr><th class="col-index">序号</th><th class="col-level">等级</th><th class="col-rule">规则</th><th class="col-location">位置</th><th class="col-message">描述</th></tr></thead>
    <tbody>${diagnosticRows() || '<tr><td colspan="5" class="muted">无缺陷明细</td></tr>'}</tbody>
  </table>
</body>
</html>`;
}

function printWithPopup(html: string) {
  const printWindow = window.open("", "_blank", "width=1024,height=768");
  if (!printWindow) {
    window.alert("浏览器阻止了打印窗口，请允许弹出窗口后重试。");
    return false;
  }
  printWindow.document.open();
  printWindow.document.write(html);
  printWindow.document.close();
  printWindow.focus();
  printWindow.setTimeout(() => {
    printWindow.print();
  }, 250);
  return true;
}

function printWithHiddenIframe(html: string) {
  const iframe = document.createElement("iframe");
  iframe.style.position = "fixed";
  iframe.style.right = "0";
  iframe.style.bottom = "0";
  iframe.style.width = "0";
  iframe.style.height = "0";
  iframe.style.border = "0";
  iframe.style.visibility = "hidden";
  document.body.appendChild(iframe);

  const iframeWindow = iframe.contentWindow;
  const iframeDocument = iframe.contentDocument || iframeWindow?.document;
  if (!iframeWindow || !iframeDocument) {
    iframe.remove();
    return false;
  }

  iframeDocument.open();
  iframeDocument.write(html);
  iframeDocument.close();

  const cleanup = () => {
    window.setTimeout(() => iframe.remove(), 1000);
  };
  const printFrame = () => {
    try {
      iframeWindow.focus();
      iframeWindow.print();
      window.setTimeout(cleanup, 3000);
    } catch {
      iframe.remove();
      printWithPopup(html);
    }
  };
  iframeWindow.onafterprint = cleanup;
  window.setTimeout(printFrame, 250);
  return true;
}

function exportPdf() {
  if (!props.report) return;
  const html = makePrintReportHtml();
  try {
    if (!printWithHiddenIframe(html)) {
      printWithPopup(html);
    }
  } catch {
    printWithPopup(html);
  }
}

function exportJson() {
  if (!props.report) return;
  const blob = new Blob([JSON.stringify(props.report.raw, null, 2)], {
    type: "application/json",
  });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `ct8114_${safeReportName()}_${Date.now()}.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
}
</script>
