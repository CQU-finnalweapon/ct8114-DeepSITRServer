export interface ProjectItem {
  project_id: string;
  project_name?: string;
  file_count?: number;
  status?: string;
  source?: string;
  writable?: boolean;
  analyzed?: boolean;
  last_analysis?: string | null;
  report_bugs?: number | null;
}

export interface DsitReportItem {
  report_id: string;
  report_name?: string;
  project_path?: string;
  total_files?: number;
  total_bugs?: number;
  by_level?: Record<string, number>;
}

async function readJson(response: Response): Promise<any> {
  const text = await response.text();
  let data: any = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { raw: text };
  }
  if (!response.ok) {
    throw new Error(toFriendlyError(data, response.status));
  }
  return data;
}

export function toFriendlyError(error: unknown, status?: number): string {
  // 统一提取错误文本，确保始终返回可读字符串
  try {
    if (error instanceof Error) {
      // Error 对象：优先取 message，但跳过已经是 JSON 的二次包装
      const msg = error.message || String(error);
      // 如果 message 本身是 JSON 字符串，尝试解析后提取 detail
      if (msg.startsWith("{") && msg.endsWith("}")) {
        try {
          const parsed = JSON.parse(msg);
          return toFriendlyError(parsed, status);
        } catch {
          /* not JSON, use as-is */
        }
      }
      return msg || `请求失败：HTTP ${status || "unknown"}`;
    }

    if (typeof error === "object" && error !== null) {
      const value = error as Record<string, any>;
      // 处理 FastAPI HTTPException 格式: { detail: "..." } 或 { detail: {...} }
      const detail = value.detail;
      if (detail !== undefined) {
        if (typeof detail === "string") return detail;
        if (typeof detail === "object") {
          // 提取嵌套消息
          return detail.message || detail.error || JSON.stringify(detail);
        }
        return String(detail);
      }
      // 其他对象格式
      return value.message || value.error || JSON.stringify(value);
    }

    if (typeof error === "string") return error;
    return `请求失败：HTTP ${status || "unknown"}`;
  } catch {
    return `请求失败：HTTP ${status || "unknown"}`;
  }
}

function buildQuery(params: Record<string, string | boolean | undefined>) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== "" && value !== false)
      query.set(key, String(value));
  });
  const text = query.toString();
  return text ? `?${text}` : "";
}

export async function fetchProjects(portalProjectId?: string) {
  const url = `/projects${buildQuery({ portal_project_id: portalProjectId })}`;
  return readJson(await fetch(url)) as Promise<{
    projects: ProjectItem[];
    uniportal_mode?: boolean;
  }>;
}

export async function fetchProjectFiles(projectId: string) {
  return readJson(
    await fetch(`/projects/${encodeURIComponent(projectId)}/files`),
  ) as Promise<{
    project_id: string;
    files: string[];
  }>;
}

export async function analyzeProject(projectId: string, entry?: string) {
  const url = `/projects/${encodeURIComponent(projectId)}/analyze${buildQuery({ entry })}`;
  return readJson(await fetch(url, { method: "POST" }));
}

/** 轮询分析任务状态，直到完成或失败.
 *
 * @param requestId  — POST /analyze 或 POST /projects/{id}/analyze 返回的 request_id
 * @param intervalMs — 轮询间隔 (毫秒), 默认 1500ms
 * @param timeoutMs  — 超时时间 (毫秒), 默认 300000ms (5 分钟)
 * @returns 任务完成后的完整 payload (同原同步接口返回格式)
 * @throws  超时或任务失败时抛出错误
 */
export async function pollAnalysisStatus(
  requestId: string,
  intervalMs = 1500,
  timeoutMs = 300_000,
  onPoll?: (count: number, status: string) => void,
): Promise<any> {
  const startedAt = Date.now();
  let count = 0;

  while (true) {
    // 超时检查
    if (Date.now() - startedAt > timeoutMs) {
      throw new Error(`分析超时 (${timeoutMs / 1000}s)，请稍后重试`);
    }

    const resp = await fetch(`/status/${encodeURIComponent(requestId)}`);
    const task = await readJson(resp);
    count++;

    if (task.status === "completed") {
      return task.payload;
    }

    if (task.status === "failed") {
      const err = task.error || {};
      throw new Error(
        err.detail || err.message || JSON.stringify(err) || "分析任务失败",
      );
    }

    // 通知回调
    if (onPoll) onPoll(count, task.status || "unknown");

    // pending 或 running — 等待后继续轮询
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

/** 启动上传分析 + 自动轮询，返回完整结果.
 *
 * 封装了 POST /analyze → 轮询 GET /status/{request_id} 的完整流程.
 * 前端组件可直接 await 此函数，无需手动管理轮询.
 */
export async function analyzeUploadWithPolling(
  files: File[],
  entry?: string,
  keep?: boolean,
  pollIntervalMs = 1500,
  pollTimeoutMs = 300_000,
  onPoll?: (count: number) => void,
) {
  // 1. 提交分析任务
  const body = new FormData();
  files.forEach((file) => body.append("files", file));
  const url = `/analyze${buildQuery({ entry, keep: keep ? "true" : undefined })}`;
  const submitResp = await readJson(await fetch(url, { method: "POST", body }));
  const requestId = submitResp.request_id;
  if (!requestId) throw new Error("服务端未返回 request_id");

  // 2. 轮询等待结果
  return pollAnalysisStatus(requestId, pollIntervalMs, pollTimeoutMs, onPoll);
}

/** 启动项目分析 + 自动轮询，返回完整结果. */
export async function analyzeProjectWithPolling(
  projectId: string,
  entry?: string,
  pollIntervalMs = 1500,
  pollTimeoutMs = 300_000,
  onPoll?: (count: number) => void,
) {
  // 1. 提交分析任务
  const submitResp = await analyzeProject(projectId, entry);
  const requestId = submitResp.request_id;
  if (!requestId) throw new Error("服务端未返回 request_id");

  // 2. 轮询等待结果
  return pollAnalysisStatus(requestId, pollIntervalMs, pollTimeoutMs, onPoll);
}

export async function analyzeUpload(
  files: File[],
  entry?: string,
  keep?: boolean,
) {
  const body = new FormData();
  files.forEach((file) => body.append("files", file));
  const url = `/analyze${buildQuery({ entry, keep: keep ? "true" : undefined })}`;
  return readJson(await fetch(url, { method: "POST", body }));
}

export async function uploadDsitLocal(localPath: string, reportName?: string) {
  const body = new FormData();
  body.append("local_path", localPath);
  if (reportName) body.append("report_name", reportName);
  return readJson(await fetch("/dsit/upload-local", { method: "POST", body }));
}

export async function uploadDsitZip(file: File, reportName?: string) {
  const body = new FormData();
  body.append("file", file);
  if (reportName) body.append("report_name", reportName);
  return readJson(await fetch("/dsit/upload", { method: "POST", body }));
}

export async function fetchDsitReports() {
  return readJson(await fetch("/dsit/reports")) as Promise<{
    reports: DsitReportItem[];
  }>;
}

export async function fetchDsitReport(reportId: string) {
  const [report, summary] = await Promise.all([
    readJson(await fetch(`/dsit/report/${encodeURIComponent(reportId)}`)),
    fetch(`/dsit/report/${encodeURIComponent(reportId)}/summary`)
      .then(readJson)
      .catch(() => null),
  ]);
  if (summary && !report.summary) report.summary = summary;
  return report;
}

export async function fetchProjectLastReport(projectId: string) {
  return readJson(
    await fetch(`/projects/${encodeURIComponent(projectId)}/last-report`),
  );
}

export async function debugDcabStart() {
  return readJson(await fetch("/debug/dcab/start"));
}

export async function debugDcabCheck() {
  return readJson(await fetch("/debug/dcab/check"));
}
