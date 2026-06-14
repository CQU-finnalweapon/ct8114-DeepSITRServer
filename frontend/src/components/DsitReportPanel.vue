<template>
  <section class="tool-card">
    <div class="card-head">
      <div>
        <h2>DSIT 报告</h2>
        <p>加载 DeepSITRServer 已有输出，展示完整分析结果。</p>
      </div>
      <button class="btn btn-secondary" type="button" :disabled="loading" @click="loadReports">刷新</button>
    </div>

    <div class="card-body stack">
      <label class="field">
        <span>报告名称（可选）</span>
        <input v-model.trim="reportName" class="input" placeholder="例如 Test2-GJB8114" />
      </label>

      <label class="field">
        <span>服务器本地输出目录</span>
        <input v-model.trim="localPath" class="input" placeholder="例如 D:\DeepSITRServer\Test2" />
      </label>

      <button class="btn btn-secondary btn-block" type="button" :disabled="loading || !localPath" @click="loadLocal">
        从本地路径加载
      </button>

      <label class="dropzone">
        <input type="file" accept=".zip" @change="onZipPick" />
        <strong>上传 DSIT 输出 zip</strong>
        <span>解析后会进入报告列表</span>
      </label>

      <div v-if="reports.length === 0" class="empty-state">暂无报告，请上传 zip 或输入本地输出目录。</div>
      <button
        v-for="report in reports"
        v-else
        :key="report.report_id"
        class="list-item"
        type="button"
        @click="openReport(report.report_id)"
      >
        <span class="item-main">
          <strong>{{ report.report_name || report.report_id }}</strong>
          <small>{{ report.report_id }}</small>
        </span>
        <span class="badge">{{ report.total_files || 0 }} 文件 / {{ report.total_bugs || 0 }} 问题</span>
      </button>

      <p class="status" :class="statusKind">{{ statusText }}</p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { fetchDsitReport, fetchDsitReports, toFriendlyError, uploadDsitLocal, uploadDsitZip, type DsitReportItem } from "../api/codeAnalysis";

const emit = defineEmits<{
  result: [raw: any, source: string];
}>();

const reports = ref<DsitReportItem[]>([]);
const localPath = ref("");
const reportName = ref("");
const loading = ref(false);
const statusText = ref("可上传 zip 或输入本地输出目录");
const statusKind = ref("");

async function loadReports() {
  loading.value = true;
  statusKind.value = "";
  try {
    const data = await fetchDsitReports();
    reports.value = data.reports || [];
    statusText.value = reports.value.length ? `共 ${reports.value.length} 份报告` : "暂无报告，请上传 zip 或输入本地输出目录";
  } catch (error) {
    statusKind.value = "error";
    statusText.value = toFriendlyError(error);
  } finally {
    loading.value = false;
  }
}

async function openReport(reportId: string) {
  loading.value = true;
  statusText.value = "正在加载报告...";
  statusKind.value = "";
  try {
    const raw = await fetchDsitReport(reportId);
    emit("result", raw, raw.project_name || reportId);
    statusKind.value = "ok";
    statusText.value = "报告已加载";
  } catch (error) {
    statusKind.value = "error";
    statusText.value = toFriendlyError(error);
  } finally {
    loading.value = false;
  }
}

async function loadLocal() {
  loading.value = true;
  statusText.value = "正在解析本地 DSIT 输出...";
  statusKind.value = "";
  try {
    const data = await uploadDsitLocal(localPath.value, reportName.value);
    await loadReports();
    await openReport(data.report_id);
  } catch (error) {
    statusKind.value = "error";
    statusText.value = toFriendlyError(error);
  } finally {
    loading.value = false;
  }
}

async function onZipPick(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0];
  if (!file) return;
  loading.value = true;
  statusText.value = "正在上传并解析 DSIT zip...";
  statusKind.value = "";
  try {
    const data = await uploadDsitZip(file, reportName.value);
    await loadReports();
    await openReport(data.report_id);
  } catch (error) {
    statusKind.value = "error";
    statusText.value = toFriendlyError(error);
  } finally {
    loading.value = false;
  }
}

onMounted(loadReports);
</script>
