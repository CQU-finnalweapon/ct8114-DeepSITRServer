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

export async function analyzeProject(projectId: string, entry?: string) {
  const url = `/projects/${encodeURIComponent(projectId)}/analyze${buildQuery({ entry })}`;
  return readJson(await fetch(url, { method: "POST" }));
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

export async function debugDcabStart() {
  return readJson(await fetch("/debug/dcab/start"));
}

export async function debugDcabCheck() {
  return readJson(await fetch("/debug/dcab/check"));
}
