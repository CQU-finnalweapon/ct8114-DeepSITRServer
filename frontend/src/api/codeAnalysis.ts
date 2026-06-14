export interface ProjectItem {
  project_id: string;
  project_name?: string;
  file_count?: number;
  status?: string;
  source?: string;
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
  const value = typeof error === "object" && error !== null ? error as Record<string, any> : {};
  const detail = value.detail ?? value.message ?? value.error ?? error;
  const text = typeof detail === "string" ? detail : JSON.stringify(detail || value);
  if (/codetidy\.exe|CODETIDY_BIN|未找到\s*codetidy|not found/i.test(text)) {
    return "后端分析程序路径未配置或不存在";
  }
  if (!text || text === "{}") return status ? `请求失败：HTTP ${status}` : "请求失败";
  return text;
}

function buildQuery(params: Record<string, string | boolean | undefined>) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== "" && value !== false) query.set(key, String(value));
  });
  const text = query.toString();
  return text ? `?${text}` : "";
}

export async function fetchProjects(portalProjectId?: string) {
  const url = `/projects${buildQuery({ portal_project_id: portalProjectId })}`;
  return readJson(await fetch(url)) as Promise<{ projects: ProjectItem[]; uniportal_mode?: boolean }>;
}

export async function analyzeProject(projectId: string, entry?: string) {
  const url = `/projects/${encodeURIComponent(projectId)}/analyze${buildQuery({ entry })}`;
  return readJson(await fetch(url, { method: "POST" }));
}

export async function analyzeUpload(files: File[], entry?: string, keep?: boolean) {
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
  return readJson(await fetch("/dsit/reports")) as Promise<{ reports: DsitReportItem[] }>;
}

export async function fetchDsitReport(reportId: string) {
  const [report, summary] = await Promise.all([
    readJson(await fetch(`/dsit/report/${encodeURIComponent(reportId)}`)),
    fetch(`/dsit/report/${encodeURIComponent(reportId)}/summary`).then(readJson).catch(() => null)
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
