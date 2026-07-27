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
        暂无项目，可使用左侧上传项目入口。
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
            {{ loadingFiles === project.project_id ? "加载中..." : "显示文件" }}
          </button>
        </span>
      </div>

      <div class="stack">
        <strong>分析配置</strong>
        <label class="field">
          <span>规则集</span>
          <select v-model="selectedRuleSet" class="input">
            <option
              v-for="ruleSet in ruleSetOptions"
              :key="ruleSet"
              :value="ruleSet"
            >
              {{ ruleSet }}
            </option>
          </select>
        </label>
        <label class="field">
          <span>入口文件（可选）</span>
          <input
            v-model.trim="entry"
            class="input"
            placeholder="e.g. src/main.c"
          />
        </label>
      </div>

      <div v-if="currentRuleSetHasReport" class="action-row">
        <button
          class="btn btn-secondary"
          type="button"
          :disabled="!selectedId || analyzing || loadingReport"
          @click="loadSelectedRuleSetReport"
        >
          {{ loadingReport ? "加载中..." : "查看结果" }}
        </button>
        <button
          class="btn btn-primary"
          type="button"
          :disabled="!selectedId || analyzing || loadingReport"
          @click="runAnalyze()"
        >
          {{ analyzing ? pollLabel : "重新分析" }}
        </button>
      </div>
      <button
        v-else
        class="btn btn-primary btn-block"
        type="button"
        :disabled="!selectedId || analyzing || loadingReport"
        @click="runAnalyze()"
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
  fetchProjectSourceFiles,
  fetchProjectRuleSetReport,
  fetchProjectRuleSetReports,
  fetchProjects,
  type ProjectSourceContext,
  type ProjectSourceFile,
  type ProjectItem,
  type ProjectRuleSetReports,
  type RuleSet,
  toFriendlyError,
} from "../api/codeAnalysis";

const emit = defineEmits<{
  result: [raw: any, source: string, context?: ProjectSourceContext];
  source: [request: ProjectSourceContext & { filePath: string }];
  sourceFiles: [payload: { context: ProjectSourceContext; files: ProjectSourceFile[] }];
}>();

const projects = ref<ProjectItem[]>([]);
const selectedId = ref("");
const entry = ref("");
const ruleSetOptions: RuleSet[] = ["GJB-8114", "GJB-5369", "CWE-C", "MISRA-2008", "MISRA-2012"];
const selectedRuleSet = ref<RuleSet>("GJB-8114");
const reportsByProject = ref<Record<string, Partial<ProjectRuleSetReports>>>({});
const loading = ref(false);
const loadingFiles = ref("");
const loadingReport = ref(false);
const visibleFiles = ref<ProjectSourceFile[]>([]);
const analyzing = ref(false);
const analyzingProjectId = ref("");
const statusText = ref("请选择项目");
const statusKind = ref("");
const activePortalProjectId = ref(readPortalProjectId() || "");
const currentRuleSetReport = computed(
  () => reportsByProject.value[selectedId.value]?.[selectedRuleSet.value],
);
const currentRuleSetHasReport = computed(() => currentRuleSetReport.value?.exists === true);

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
    const requestedPortalProjectId = readPortalProjectId();
    const data = await fetchProjects(requestedPortalProjectId);
    const projectMap = new Map<string, ProjectItem>();
    (data.projects || []).forEach((project) => {
      projectMap.set(project.project_id, project);
    });
    if (requestedPortalProjectId && requestedPortalProjectId !== "local-upload") {
      const uploadedData = await fetchProjects("local-upload").catch(() => null);
      (uploadedData?.projects || []).forEach((project) => {
        if (!projectMap.has(project.project_id)) {
          projectMap.set(project.project_id, project);
        }
      });
    }
    activePortalProjectId.value = data.portal_project_id || requestedPortalProjectId || "";
    if (activePortalProjectId.value) {
      sessionStorage.setItem("ct8114.portalProjectId", activePortalProjectId.value);
    }
    projects.value = Array.from(projectMap.values());
    if (!projects.value.some((item) => item.project_id === selectedId.value))
      selectedId.value = "";
    await refreshReportsForProjects(projects.value);
    statusText.value = projects.value.length
      ? "共 " + projects.value.length + " 个项目，请选择后分析"
      : "暂无项目，可使用左侧上传项目入口";
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
  return "轮询中（第 " + pollCount.value + " 次）...";
});

function projectPortalProjectIdFrom(project: ProjectItem | undefined, allowFallback = true) {
  const fromProject = project?.portalProjectId || project?.portal_project_id || "";
  if (fromProject) return fromProject;
  if (!allowFallback) return "";
  return (
    activePortalProjectId.value ||
    readPortalProjectId() ||
    ""
  );
}

async function refreshProjectReports(projectId: string) {
  const project = projects.value.find((item) => item.project_id === projectId);
  const portalProjectId = projectPortalProjectIdFrom(project);
  if (!portalProjectId) {
    reportsByProject.value = { ...reportsByProject.value, [projectId]: {} };
    return;
  }
  try {
    const data = await fetchProjectRuleSetReports(projectId, portalProjectId);
    reportsByProject.value = {
      ...reportsByProject.value,
      [projectId]: data.reports || {},
    };
  } catch {
    reportsByProject.value = { ...reportsByProject.value, [projectId]: {} };
  }
}

async function refreshReportsForProjects(items: ProjectItem[]) {
  await Promise.allSettled(items.map((project) => refreshProjectReports(project.project_id)));
}

async function viewFiles(projectId: string) {
  loadingFiles.value = projectId;
  selectedId.value = projectId;
  visibleFiles.value = [];
  statusKind.value = "";
  statusText.value = "正在加载源码文件...";
  try {
    const portalProjectId = projectPortalProjectId(projectId);
    if (!portalProjectId) throw new Error("缺少 portal_project_id，无法读取项目库源码");
    const data = await fetchProjectSourceFiles(projectId, portalProjectId);
    visibleFiles.value = data.files || [];
    const context = projectSourceContext(projectId);
    if (context) {
      emit("sourceFiles", { context, files: visibleFiles.value });
      if (visibleFiles.value[0]) emit("source", { ...context, filePath: visibleFiles.value[0].file_path });
    }
    statusText.value = visibleFiles.value.length
      ? "已加载 " + visibleFiles.value.length + " 个源码文件"
      : "未找到源码文件";
  } catch (error) {
    statusKind.value = "error";
    statusText.value = toFriendlyError(error);
  } finally {
    loadingFiles.value = "";
  }
}

function projectPortalProjectId(projectId: string, allowFallback = true) {
  const project = projects.value.find((item) => item.project_id === projectId);
  return projectPortalProjectIdFrom(project, allowFallback);
}

function projectSourceContext(projectId: string): ProjectSourceContext | undefined {
  const project = projects.value.find((item) => item.project_id === projectId);
  const portalProjectId = projectPortalProjectId(projectId);
  if (!portalProjectId || project?.source !== "uniportal") return undefined;
  return { projectId, portalProjectId };
}

async function loadSelectedRuleSetReport() {
  if (!selectedId.value) return;
  const projectId = selectedId.value;
  const project = projects.value.find((item) => item.project_id === projectId);
  const portalProjectId = projectPortalProjectId(projectId, false);
  if (!portalProjectId) {
    statusKind.value = "error";
    statusText.value = "项目库缺少 portal_project_id，无法读取规则集报告。";
    return;
  }
  loadingReport.value = true;
  statusKind.value = "";
  statusText.value = "正在加载 " + selectedRuleSet.value + " 最近结果...";
  try {
    const raw = await fetchProjectRuleSetReport(projectId, portalProjectId, selectedRuleSet.value);
    emit("result", raw, project?.project_name || projectId, projectSourceContext(projectId));
    statusKind.value = "ok";
    statusText.value = "已加载 " + selectedRuleSet.value + " 最近结果";
  } catch (error) {
    statusKind.value = "error";
    statusText.value = toFriendlyError(error);
  } finally {
    loadingReport.value = false;
  }
}

async function runAnalyze(projectId?: string) {
  if (projectId) selectedId.value = projectId;
  if (!selectedId.value) return;
  const currentProject = projects.value.find((item) => item.project_id === selectedId.value);
  const portalProjectId = projectPortalProjectId(selectedId.value, false);
  if (currentProject?.source === "uniportal" && !portalProjectId) {
    statusKind.value = "error";
    statusText.value = "项目库缺少 portal_project_id，无法重新分析。";
    return;
  }
  if (currentProject?.source !== "uniportal") {
    statusKind.value = "error";
    statusText.value = "本轮只允许重新分析 UniPortal 项目库项目。";
    return;
  }
  analyzing.value = true;
  analyzingProjectId.value = selectedId.value;
  pollCount.value = 0;
  statusKind.value = "";
  statusText.value = "正在提交分析任务...";
  try {
    const raw = await analyzeProjectWithPolling(
      selectedId.value,
      portalProjectId,
      entry.value,
      selectedRuleSet.value,
      1500,
      300_000,
      (count, _status, task) => {
        pollCount.value = count;
        const progress = task?.progress_info || task?.dcab_progress_info || {};
        if (progress.completed_count != null && progress.total_count != null) {
          statusText.value = "分析任务已提交，正在轮询（第 " + count + " 次），已完成 " + progress.completed_count + "/" + progress.total_count;
        } else {
          statusText.value = "分析任务已提交，正在轮询（第 " + count + " 次）...";
        }
      },
    );
    const project = projects.value.find(
      (item) => item.project_id === selectedId.value,
    );
    await refreshProjectReports(selectedId.value);
    emit("result", raw, project?.project_name || selectedId.value, projectSourceContext(selectedId.value));
    statusKind.value = "ok";
    statusText.value = "分析完成";
  } catch (error) {
    statusKind.value = "error";
    statusText.value = toFriendlyError(error);
  } finally {
    analyzing.value = false;
    analyzingProjectId.value = "";
  }
}

onMounted(loadProjects);

defineExpose({ loadProjects });
</script>

