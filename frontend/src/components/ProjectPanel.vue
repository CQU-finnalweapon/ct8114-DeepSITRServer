<template>
  <section class="tool-card">
    <div class="card-head">
      <div>
        <h2>项目库</h2>
        <p>读取 UniPortal 共享项目和本工具私有项目。</p>
      </div>
      <button
        class="btn btn-secondary"
        type="button"
        :disabled="loading"
        @click="loadProjects"
      >
        刷新
      </button>
    </div>

    <div class="card-body stack">
      <div v-if="projects.length === 0" class="empty-state">
        暂无项目，可使用直接上传或 DSIT 报告入口。
      </div>
      <button
        v-for="project in projects"
        v-else
        :key="project.project_id"
        class="list-item"
        :class="{ active: selectedId === project.project_id }"
        type="button"
        @click="selectedId = project.project_id"
      >
        <span class="item-main">
          <strong>{{ project.project_name || project.project_id }}</strong>
          <small>{{ project.project_id }}</small>
        </span>
        <span class="item-side">
          <span
            class="badge"
            :class="project.source === 'uniportal' ? 'badge-blue' : ''"
          >
            {{ project.source === "uniportal" ? "UniPortal" : "本地" }}
          </span>
          <span
            v-if="project.writable && project.source === 'uniportal'"
            class="badge badge-green"
            title="分析报告写回共享卷"
            >↔ 读写</span
          >
          <span
            v-if="project.analyzed"
            class="badge badge-green"
            :title="'最近分析: ' + (project.last_analysis || '未知')"
            >✓ 已分析</span
          >
          <span
            v-if="project.report_bugs != null"
            class="badge"
            :class="project.report_bugs > 0 ? 'badge-yellow' : 'badge-green'"
            >{{ project.report_bugs }} 问题</span
          >
          <span class="badge">{{ project.file_count || 0 }} 文件</span>
        </span>
      </button>

      <label class="field">
        <span>入口文件（可选）</span>
        <input
          v-model.trim="entry"
          class="input"
          placeholder="例如 src/main.c，留空则分析全部源文件"
        />
      </label>

      <button
        class="btn btn-primary btn-block"
        type="button"
        :disabled="!selectedId || analyzing"
        @click="runAnalyze"
      >
        {{ analyzing ? pollLabel : "开始分析项目" }}
      </button>

      <p class="status" :class="statusKind">{{ statusText }}</p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  analyzeProjectWithPolling,
  fetchProjects,
  type ProjectItem,
  toFriendlyError,
} from "../api/codeAnalysis";

const emit = defineEmits<{
  result: [raw: any, source: string];
}>();

const projects = ref<ProjectItem[]>([]);
const selectedId = ref("");
const entry = ref("");
const loading = ref(false);
const analyzing = ref(false);
const statusText = ref("请选择项目");
const statusKind = ref("");

function readPortalProjectId() {
  const urlValue = new URLSearchParams(window.location.search).get(
    "portal_project_id",
  );
  if (urlValue) {
    sessionStorage.setItem("ct8114.portalProjectId", urlValue);
    return urlValue;
  }
  return sessionStorage.getItem("ct8114.portalProjectId") || undefined;
}

async function loadProjects() {
  loading.value = true;
  statusKind.value = "";
  statusText.value = "正在加载项目库...";
  try {
    const data = await fetchProjects(readPortalProjectId());
    projects.value = data.projects || [];
    if (!projects.value.some((item) => item.project_id === selectedId.value))
      selectedId.value = "";
    statusText.value = projects.value.length
      ? `共 ${projects.value.length} 个项目，请选择后分析`
      : "暂无项目，可使用直接上传或 DSIT 报告入口";
  } catch (error) {
    statusKind.value = "error";
    statusText.value = toFriendlyError(error);
  } finally {
    loading.value = false;
  }
}

const pollCount = ref(0);
const pollLabel = computed(() => {
  if (!analyzing.value) return "开始分析项目";
  return `轮询中 (第 ${pollCount.value} 次)...`;
});

async function runAnalyze() {
  if (!selectedId.value) return;
  analyzing.value = true;
  pollCount.value = 0;
  statusKind.value = "";
  statusText.value = "正在提交分析任务...";
  try {
    const raw = await analyzeProjectWithPolling(
      selectedId.value,
      entry.value,
      1500,
      300_000,
      (count) => { pollCount.value = count; statusText.value = `分析任务已提交，正在轮询 (第 ${count} 次)...`; },
    );
    const project = projects.value.find(
      (item) => item.project_id === selectedId.value,
    );
    emit("result", raw, project?.project_name || selectedId.value);
    statusKind.value = "ok";
    statusText.value = "分析完成";
  } catch (error) {
    statusKind.value = "error";
    statusText.value = toFriendlyError(error);
  } finally {
    analyzing.value = false;
  }
}

onMounted(loadProjects);
</script>
