<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand">
        <div class="brand-mark">G</div>
        <div>
          <h1>GJB8114 在线静态分析</h1>
          <p>代码安全分析工具 · UniPortal 子工具</p>
        </div>
      </div>
      <div class="top-pills">
        <span class="pill">Vue3 + Vite</span>
        <span class="pill">{{ activeLabel }}</span>
      </div>
    </header>

    <main class="layout">
      <aside class="left-column">
        <nav class="tabbar" aria-label="分析入口">
          <button
            v-for="tab in tabs"
            :key="tab.key"
            type="button"
            :class="{ active: activeTab === tab.key }"
            @click="activeTab = tab.key"
          >
            {{ tab.label }}
          </button>
        </nav>

        <ProjectPanel v-if="activeTab === 'projects'" @result="handleResult" />
        <UploadPanel v-else-if="activeTab === 'upload'" @result="handleResult" />
        <DsitReportPanel v-else @result="handleResult" />
      </aside>

      <ResultPanel :report="report" :source-name="sourceName" />
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

type TabKey = "projects" | "upload" | "dsit";

const tabs: Array<{ key: TabKey; label: string }> = [
  { key: "projects", label: "项目库" },
  { key: "upload", label: "直接上传" },
  { key: "dsit", label: "DSIT 报告" }
];

const activeTab = ref<TabKey>("projects");
const report = ref<NormalizedReport | null>(null);
const sourceName = ref("");

const activeLabel = computed(() => tabs.find((tab) => tab.key === activeTab.value)?.label || "");

function handleResult(raw: any, source: string) {
  report.value = normalizeReport(raw);
  sourceName.value = source;
}
</script>
