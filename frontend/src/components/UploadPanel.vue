<template>
  <section class="tool-card">
    <div class="card-head">
      <div>
        <h2>上传项目</h2>
        <p>上传 ZIP 工程包，保存到项目库后在右侧重新分析。</p>
      </div>
    </div>

    <div class="card-body stack">
      <label
        class="dropzone"
        :class="{ dragover }"
        @dragover.prevent="dragover = true"
        @dragleave="dragover = false"
        @drop.prevent="onDrop"
      >
        <input type="file" accept=".zip" @change="onPick" />
        <strong>选择或拖入 ZIP 工程包</strong>
        <span>.zip</span>
      </label>

      <div v-if="file" class="file-list">
        <div class="file-row">
          <span>
            <strong>{{ file.name }}</strong>
            <small>{{ formatSize(file.size) }}</small>
          </span>
          <button class="btn btn-danger" type="button" @click="removeFile">
            移除
          </button>
        </div>
      </div>

      <button
        class="btn btn-primary btn-block"
        type="button"
        :disabled="!file || uploading"
        @click="upload"
      >
        {{ uploading ? "上传中..." : "上传到项目库" }}
      </button>

      <p class="status" :class="statusKind">{{ statusText }}</p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { toFriendlyError, uploadProject } from "../api/codeAnalysis";

const emit = defineEmits<{
  uploaded: [];
}>();

const file = ref<File | null>(null);
const dragover = ref(false);
const uploading = ref(false);
const statusText = ref("请选择 ZIP 工程包");
const statusKind = ref("");

function setFile(nextFile: File | null) {
  if (!nextFile) return;
  if (!nextFile.name.toLowerCase().endsWith(".zip")) {
    file.value = null;
    statusKind.value = "error";
    statusText.value = "请选择 ZIP 工程包";
    return;
  }
  file.value = nextFile;
  statusKind.value = "";
  statusText.value = "已选择 " + nextFile.name;
}

function onPick(event: Event) {
  const input = event.target as HTMLInputElement;
  setFile(input.files?.[0] || null);
  input.value = "";
}

function onDrop(event: DragEvent) {
  dragover.value = false;
  setFile(event.dataTransfer?.files?.[0] || null);
}

function removeFile() {
  file.value = null;
  statusKind.value = "";
  statusText.value = "请选择 ZIP 工程包";
}

function formatSize(bytes: number) {
  if (bytes < 1024) return String(bytes) + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / 1024 / 1024).toFixed(2) + " MB";
}

async function upload() {
  if (!file.value) return;
  uploading.value = true;
  statusKind.value = "";
  statusText.value = "正在上传项目...";
  try {
    await uploadProject(file.value);
    file.value = null;
    statusKind.value = "ok";
    statusText.value = "项目已上传到项目库，请在右侧项目库中点击重新分析。";
    emit("uploaded");
  } catch (error) {
    statusKind.value = "error";
    statusText.value = toFriendlyError(error);
  } finally {
    uploading.value = false;
  }
}
</script>
