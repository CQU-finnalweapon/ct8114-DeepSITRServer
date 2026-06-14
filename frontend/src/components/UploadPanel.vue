<template>
  <section class="tool-card">
    <div class="card-head">
      <div>
        <h2>直接上传</h2>
        <p>上传源码文件或工程 zip，并调用现有 /analyze 接口。</p>
      </div>
    </div>

    <div class="card-body stack">
      <label class="dropzone" :class="{ dragover }" @dragover.prevent="dragover = true" @dragleave="dragover = false" @drop.prevent="onDrop">
        <input type="file" multiple accept=".c,.h,.cc,.cpp,.cxx,.hpp,.hxx,.zip" @change="onPick" />
        <strong>选择或拖入源码文件 / 工程压缩包</strong>
        <span>.c / .h / .cpp / .hpp / .cc / .cxx / .hxx / .zip</span>
      </label>

      <div v-if="hasZip" class="notice">
        工程压缩包将由后端解压后按工程目录分析。zip 上传时请只选择一个 zip 文件。
      </div>

      <div v-if="files.length" class="file-list">
        <div v-for="(file, index) in files" :key="file.name + file.size" class="file-row">
          <span>
            <strong>{{ file.name }}</strong>
            <small>{{ formatSize(file.size) }}</small>
          </span>
          <button class="btn btn-danger" type="button" @click="removeFile(index)">移除</button>
        </div>
      </div>

      <label class="field">
        <span>入口文件（可选）</span>
        <input v-model.trim="entry" class="input" :placeholder="hasZip ? '例如 src/main.c，留空则递归分析工程源文件' : '例如 main.c'" />
      </label>

      <label class="check-row">
        <input v-model="keep" type="checkbox" />
        <span>保留服务端临时目录（调试）</span>
      </label>

      <button class="btn btn-primary btn-block" type="button" :disabled="files.length === 0 || analyzing" @click="runAnalyze">
        {{ analyzing ? "分析中..." : "开始分析" }}
      </button>

      <div class="debug-actions">
        <button class="btn btn-secondary" type="button" :disabled="debugging" @click="runDebugStart">DCAB 启动调试</button>
        <button class="btn btn-secondary" type="button" :disabled="debugging" @click="runDebugCheck">DCAB 检查调试</button>
      </div>

      <p class="status" :class="statusKind">{{ statusText }}</p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { analyzeUpload, debugDcabCheck, debugDcabStart, toFriendlyError } from "../api/codeAnalysis";

const emit = defineEmits<{
  result: [raw: any, source: string];
}>();

const files = ref<File[]>([]);
const entry = ref("");
const keep = ref(false);
const dragover = ref(false);
const analyzing = ref(false);
const debugging = ref(false);
const statusText = ref("请选择待分析源码文件或工程 zip");
const statusKind = ref("");

const hasZip = computed(() => files.value.some((file) => file.name.toLowerCase().endsWith(".zip")));

function addFiles(list: FileList | null) {
  const incoming = Array.from(list || []);
  const zipFiles = incoming.filter((file) => file.name.toLowerCase().endsWith(".zip"));
  if (zipFiles.length > 0) {
    files.value = [zipFiles[0]];
    statusKind.value = "warn";
    statusText.value = "已选择工程 zip，后端将解压后按工程目录分析";
    return;
  }

  if (hasZip.value) files.value = [];
  incoming.forEach((file) => {
    if (!files.value.some((item) => item.name === file.name && item.size === file.size)) {
      files.value.push(file);
    }
  });
  statusKind.value = "";
  statusText.value = files.value.length ? `已选择 ${files.value.length} 个文件` : "请选择待分析源码文件或工程 zip";
}

function removeFile(index: number) {
  files.value.splice(index, 1);
  statusKind.value = "";
  statusText.value = files.value.length ? `已选择 ${files.value.length} 个文件` : "请选择待分析源码文件或工程 zip";
}

function onPick(event: Event) {
  addFiles((event.target as HTMLInputElement).files);
}

function onDrop(event: DragEvent) {
  dragover.value = false;
  addFiles(event.dataTransfer?.files || null);
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

async function runAnalyze() {
  analyzing.value = true;
  statusKind.value = "";
  statusText.value = hasZip.value ? "正在上传工程 zip，后端解压后分析..." : "正在上传并分析...";
  try {
    const raw = await analyzeUpload(files.value, entry.value, keep.value);
    emit("result", raw, hasZip.value ? "工程 zip 上传" : "直接上传");
    statusKind.value = raw.status === "check_progress_empty" ? "warn" : "ok";
    statusText.value = raw.detection_id ? "已启动分析，等待结果查询" : "分析完成";
  } catch (error) {
    statusKind.value = "error";
    statusText.value = toFriendlyError(error);
  } finally {
    analyzing.value = false;
  }
}

async function runDebugStart() {
  debugging.value = true;
  statusKind.value = "";
  statusText.value = "正在调用 DCAB 启动调试接口...";
  try {
    const raw = await debugDcabStart();
    emit("result", raw, "DCAB 启动调试");
    statusKind.value = "ok";
    statusText.value = "DCAB 启动调试结果已返回";
  } catch (error) {
    statusKind.value = "error";
    statusText.value = toFriendlyError(error);
  } finally {
    debugging.value = false;
  }
}

async function runDebugCheck() {
  debugging.value = true;
  statusKind.value = "";
  statusText.value = "正在调用 DCAB 检查调试接口...";
  try {
    const raw = await debugDcabCheck();
    emit("result", raw, "DCAB 检查调试");
    statusKind.value = raw.status === "check_progress_empty" ? "warn" : "ok";
    statusText.value = "DCAB 检查调试结果已返回";
  } catch (error) {
    statusKind.value = "error";
    statusText.value = toFriendlyError(error);
  } finally {
    debugging.value = false;
  }
}
</script>
