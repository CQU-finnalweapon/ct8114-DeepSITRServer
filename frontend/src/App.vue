<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand">
        <div class="brand-mark">G</div>
        <div>
          <h1>GJB8114 在线静态分析</h1>
          <p>代码安全分析工具 / UniPortal 子工具</p>
        </div>
      </div>
      <div class="top-pills">
        <span class="pill">Vue3 + Vite</span>
        <span class="pill">{{ activeLabel }}</span>
      </div>
    </header>

    <main class="layout">
      <section class="entry-grid">
        <UploadPanel @uploaded="handleProjectUploaded" />
        <ProjectPanel
          ref="projectPanelRef"
          @result="handleProjectResult"
          @source="handleSourceOpen"
          @source-files="handleSourceFiles"
        />
        <DsitReportPanel v-if="isDebug" @result="handleDsitResult" />
      </section>

      <ResultPanel
        :report="activeReport"
        :source-name="activeSourceName"
        :source-context="activeSourceContext"
        :source-request="activeSourceRequest"
        :source-files="activeSourceFiles"
      />
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import ProjectPanel from "./components/ProjectPanel.vue";
import UploadPanel from "./components/UploadPanel.vue";
import DsitReportPanel from "./components/DsitReportPanel.vue";
import ResultPanel from "./components/ResultPanel.vue";
import { normalizeReport, type NormalizedReport } from "./utils/normalizeReport";
import type { ProjectSourceContext, ProjectSourceFile } from "./api/codeAnalysis";

type TabKey = "projects" | "upload" | "dsit";

const isDebug = new URLSearchParams(window.location.search).get("debug") === "1";

const tabs = computed<Array<{ key: TabKey; label: string }>>(() => [
  { key: "upload", label: "上传项目" },
  { key: "projects", label: "项目库" },
  ...(isDebug ? [{ key: "dsit" as const, label: "DSIT 报告" }] : []),
]);

const activeTab = ref<TabKey>("upload");
const projectPanelRef = ref<InstanceType<typeof ProjectPanel> | null>(null);

const projectRequestId = ref("");
const projectResult = ref<any>(null);
const projectStatus = ref("");
const projectReport = ref<NormalizedReport | null>(null);
const projectSourceName = ref("");
const projectSourceContext = ref<ProjectSourceContext | null>(null);
const projectSourceRequest = ref<(ProjectSourceContext & { filePath: string; line?: number; column?: number; key: number }) | null>(null);
const projectSourceFiles = ref<ProjectSourceFile[]>([]);

const dsitReport = ref<NormalizedReport | null>(null);
const dsitSourceName = ref("");

const activeLabel = computed(() => tabs.value.find((tab) => tab.key === activeTab.value)?.label || "");
const activeReport = computed(() => {
  if (activeTab.value === "projects") return projectReport.value;
  if (activeTab.value === "dsit") return dsitReport.value;
  return null;
});
const activeSourceName = computed(() => {
  if (activeTab.value === "projects") return projectSourceName.value;
  if (activeTab.value === "dsit") return dsitSourceName.value;
  return "";
});
const activeSourceContext = computed(() =>
  activeTab.value === "projects" ? projectSourceContext.value : null,
);
const activeSourceRequest = computed(() =>
  activeTab.value === "projects" ? projectSourceRequest.value : null,
);
const activeSourceFiles = computed(() =>
  activeTab.value === "projects" ? projectSourceFiles.value : [],
);

function handleProjectResult(raw: any, source: string, context?: ProjectSourceContext) {
  activeTab.value = "projects";
  projectResult.value = raw;
  projectReport.value = normalizeReport(raw);
  projectRequestId.value = String(raw?.request_id || raw?.report?.request_id || raw?.report?.report_id || "");
  projectStatus.value = String(raw?.status || projectReport.value.status || "");
  projectSourceName.value = source;
  projectSourceContext.value = context || null;
  projectSourceRequest.value = null;
  projectSourceFiles.value = [];
}

function handleDsitResult(raw: any, source: string) {
  activeTab.value = "dsit";
  dsitReport.value = normalizeReport(raw);
  dsitSourceName.value = source;
}

function handleSourceOpen(request: ProjectSourceContext & { filePath: string; line?: number; column?: number }) {
  activeTab.value = "projects";
  projectSourceContext.value = {
    projectId: request.projectId,
    portalProjectId: request.portalProjectId,
  };
  projectSourceRequest.value = { ...request, key: Date.now() };
}

function handleSourceFiles(payload: { context: ProjectSourceContext; files: ProjectSourceFile[] }) {
  activeTab.value = "projects";
  projectSourceContext.value = payload.context;
  projectSourceFiles.value = payload.files;
}

async function handleProjectUploaded() {
  activeTab.value = "projects";
  await projectPanelRef.value?.loadProjects();
}
</script>
