<template>
  <section class="result-stack">
    <div class="tool-card">
      <div class="card-head">
        <div>
          <h2>分析汇总</h2>
          <p>{{ report?.message || "等待分析结果" }}</p>
        </div>
        <button
          class="btn btn-primary"
          type="button"
          :disabled="!report"
          @click="exportJson"
        >
          导出 JSON
        </button>
      </div>
      <div class="card-body stack">
        <div class="stats-grid">
          <div class="stat-card">
            <span>问题总数</span
            ><strong>{{ report?.summary.total ?? "-" }}</strong>
          </div>
          <div class="stat-card warn">
            <span>WARNING</span
            ><strong>{{ report?.summary.warning ?? "-" }}</strong>
          </div>
          <div class="stat-card error">
            <span>ERROR</span
            ><strong>{{ report?.summary.error ?? "-" }}</strong>
          </div>
          <div class="stat-card">
            <span>检查项种类</span
            ><strong>{{ report?.summary.ruleCount ?? "-" }}</strong>
          </div>
          <div class="stat-card">
            <span>文件数量</span
            ><strong>{{ report?.summary.fileCount ?? "-" }}</strong>
          </div>
        </div>

        <div class="result-meta">
          <span class="badge">状态：{{ report?.status || "未开始" }}</span>
          <span v-if="report?.requestId" class="badge"
            >request_id：{{ report.requestId }}</span
          >
          <span v-if="report?.detectionId" class="badge badge-blue"
            >detection_id：{{ report.detectionId }}</span
          >
          <span
            v-if="report?.uniportalWriteback === 'ok'"
            class="badge badge-green"
            title="分析报告已写回 UniPortal 共享卷"
            >✅ 已写回共享卷</span
          >
          <span
            v-else-if="report?.uniportalWriteback"
            class="badge badge-red"
            :title="report.uniportalWriteback"
            >⚠️ 共享卷写回失败</span
          >
          <span
            v-if="report?.raw?.uniportal_writeback_path"
            class="badge"
            :title="report.raw.uniportal_writeback_path"
            >📁
            {{ shortWritebackPath(report.raw.uniportal_writeback_path) }}</span
          >
          <span
            v-if="report?.raw?.saved_project_id"
            class="badge badge-blue"
            title="上传项目已持久化到模拟共享卷，刷新项目库可见"
            >💾 已保存: {{ report.raw.saved_project_id }}</span
          >
        </div>

        <div class="filters">
          <label class="field">
            <span>搜索</span>
            <input
              v-model.trim="keyword"
              class="input"
              placeholder="搜索规则、检查器、文件或描述"
            />
          </label>
          <label class="field">
            <span>级别筛选</span>
            <select v-model="levelFilter" class="input">
              <option value="">全部</option>
              <option value="Error">ERROR</option>
              <option value="Warning">WARNING</option>
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
                <th>级别</th>
                <th>规则</th>
                <th>文件</th>
                <th>行:列</th>
                <th>描述</th>
                <th>检查器</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in filteredDiagnostics" :key="item.id">
                <td>
                  <span
                    class="badge"
                    :class="
                      item.level === 'Error' ? 'badge-red' : 'badge-yellow'
                    "
                    >{{ item.level }}</span
                  >
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
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <details class="raw-json">
      <summary>原始 JSON</summary>
      <pre>{{ prettyRaw }}</pre>
    </details>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { NormalizedReport } from "../utils/normalizeReport";

const props = defineProps<{
  report: NormalizedReport | null;
  sourceName?: string;
}>();

const keyword = ref("");
const levelFilter = ref("");
const ruleFilter = ref("");

watch(
  () => props.report,
  () => {
    keyword.value = "";
    levelFilter.value = "";
    ruleFilter.value = "";
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
    if (levelFilter.value && item.level !== levelFilter.value) return false;
    if (ruleFilter.value && item.ruleId !== ruleFilter.value) return false;
    if (!kw) return true;
    return [item.ruleId, item.checker, item.message, item.filePath, item.level]
      .join(" ")
      .toLowerCase()
      .includes(kw);
  });
});

const prettyRaw = computed(() =>
  props.report ? JSON.stringify(props.report.raw, null, 2) : "{}",
);

function shortPath(path: string) {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.slice(-2).join("/") || path;
}

function shortWritebackPath(path: string) {
  // 显示共享卷写回路径的最后几段，便于确认
  const parts = path.split(/[\\/]/).filter(Boolean);
  const key = parts.slice(-3).join("/");
  return key.length > 40 ? "..." + key.slice(-37) : key;
}

function exportJson() {
  if (!props.report) return;
  const safeName = (
    props.sourceName ||
    props.report.requestId ||
    "report"
  ).replace(/[^\w.-]+/g, "_");
  const blob = new Blob([JSON.stringify(props.report.raw, null, 2)], {
    type: "application/json",
  });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `ct8114_${safeName}_${Date.now()}.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
}
</script>
