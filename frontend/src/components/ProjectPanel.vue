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
      <div
        v-for="project in projects"
        v-else
        :key="project.project_id"
        class="list-item"
        :class="{ active: selectedId === project.project_id }"
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
            {{ project.source === "uniportal" ? "UniPortal" : "Local" }}
          </span>
          <span
            v-if="project.writable && project.source === 'uniportal'"
            class="badge badge-green"
            title="Report can be written back to the shared volume"
            >RW</span
          >
          <span
            v-if="project.analyzed"
            class="badge badge-green"
            :title="'Last analysis: ' + (project.last_analysis || 'unknown')"
            >Analyzed</span
          >
          <span
            v-if="project.report_bugs != null"
            class="badge"
            :class="project.report_bugs > 0 ? 'badge-yellow' : 'badge-green'"
            >{{ project.report_bugs }} issues</span
          >
          <span class="badge">{{ project.file_count || 0 }} files</span>
        </span>
        <span class="item-actions">
          <button
            class="btn btn-secondary"
            type="button"
            :disabled="loadingFiles === project.project_id"
            @click.stop="viewFiles(project.project_id)"
          >
            查看文件
          </button>
          <button
            v-if="project.analyzed"
            class="btn btn-secondary"
            type="button"
            :disabled="loadingReport === project.project_id"
            @click.stop="loadLastReport(project.project_id, project.project_name)"
          >
            {{ loadingReport === project.project_id ? '加载中...' : '查看历史报告' }}
          </button>
          <button
            class="btn btn-primary"
            type="button"
            :disabled="analyzing"
            @click.stop="runAnalyze(project.project_id)"
          >
            重新分析
          </button>
        </span>
      </div>

      <div v-if="visibleFiles.length" class="file-list compact-list">
        <div class="file-row" v-for="file in visibleFiles" :key="file">
          <span><strong>{{ file }}</strong></span>
        </div>
      </div>

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
        @click="runAnalyze()"
      >
        {{ analyzing ? pollLabel : "重新分析项目" }}
      </button>

      <p class="status" :class="statusKind">{{ statusText }}</p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  analyzeProjectWithPolling,
  fetchProjectFiles,
  fetchProjectLastReport,
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
const loadingFiles = ref("");
const loadingReport = ref("");
const visibleFiles = ref<string[]>([]);
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

async function viewFiles(projectId: string) {
  loadingFiles.value = projectId;
  selectedId.value = projectId;
  visibleFiles.value = [];
  statusKind.value = "";
  statusText.value = "Loading project files...";
  try {
    const data = await fetchProjectFiles(projectId);
    visibleFiles.value = data.files || [];
    statusText.value = visibleFiles.value.length
      ? `Loaded ${visibleFiles.value.length} source files`
      : "No source files found";
  } catch (error) {
    statusKind.value = "error";
    statusText.value = toFriendlyError(error);
  } finally {
    loadingFiles.value = "";
  }
}

async function loadLastReport(projectId: string, projectName?: string) {
  loadingReport.value = projectId;
  selectedId.value = projectId;
  statusKind.value = "";
  statusText.value = "正在加载历史报告...";
  try {
    const raw = await fetchProjectLastReport(projectId);
    emit("result", raw, projectName || projectId);
    statusKind.value = "ok";
    statusText.value = "已加载历史报告";
  } catch (error) {
    statusKind.value = "error";
    statusText.value = toFriendlyError(error);
  } finally {
    loadingReport.value = "";
  }
}

async function runAnalyze(projectId?: string) {
  if (projectId) selectedId.value = projectId;
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
      (count) => {
        pollCount.value = count;
        statusText.value = `分析任务已提交，正在轮询 (第 ${count} 次)...`;
      },
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
