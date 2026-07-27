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
  portal_project_id?: string;
  portalProjectId?: string;
}

export interface DsitReportItem {
  report_id: string;
  report_name?: string;
  project_path?: string;
  total_files?: number;
  total_bugs?: number;
  by_level?: Record<string, number>;
}

export interface ProjectSourceContext {
  projectId: string;
  portalProjectId: string;
}

export interface ProjectSourceFile {
  file_path: string;
  name?: string;
  ext?: string;
  size?: number;
  has_report?: boolean;
  bug_count?: number;
  function_count?: number;
}

export interface ProjectSourceLine {
  line: number;
  text: string;
}

export interface ProjectSourcePayload {
  project_id: string;
  portal_project_id: string;
  file_path: string;
  source_root?: string;
  absolute_path?: string;
  encoding?: string;
  line_count?: number;
  lines: ProjectSourceLine[];
  bugs: any[];
  functions: any[];
  target?: {
    line: number;
    column: number;
  };
}

export type RuleSet = "GJB-8114" | "GJB-5369" | "CWE-C" | "MISRA-2008" | "MISRA-2012";

export interface ProjectRuleSetReportInfo {
  exists: boolean;
  report_path?: string;
  meta_path?: string;
  selected_rule_set?: RuleSet | string;
  last_analysis?: string;
  total_bugs?: number;
  total_files?: number;
  engine_rule_count?: number;
}

export type ProjectRuleSetReports = Record<RuleSet, ProjectRuleSetReportInfo>;

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
  const data = await readJson(await fetch(url));
  const returnedPortalProjectId = String(
    data.portalProjectId || data.portal_project_id || portalProjectId || "",
  );
  const projects = Array.isArray(data.projects)
    ? data.projects.map((project: ProjectItem) => ({
        ...project,
        portalProjectId: String(
          project.portalProjectId ||
            project.portal_project_id ||
            returnedPortalProjectId ||
            "",
        ),
      }))
    : [];
  return { ...data, projects, portal_project_id: returnedPortalProjectId } as {
    projects: ProjectItem[];
    uniportal_mode?: boolean;
    portal_project_id?: string;
  };
}

export async function fetchProjectFiles(projectId: string) {
  return readJson(
    await fetch(`/projects/${encodeURIComponent(projectId)}/files`),
  ) as Promise<{
    project_id: string;
    files: string[];
  }>;
}

export async function fetchProjectSourceFiles(
  projectId: string,
  portalProjectId: string,
) {
  const url = `/projects/${encodeURIComponent(projectId)}/source-files${buildQuery({
    portal_project_id: portalProjectId,
  })}`;
  return readJson(await fetch(url)) as Promise<{
    project_id: string;
    portal_project_id: string;
    source_root?: string;
    files: ProjectSourceFile[];
  }>;
}

export async function fetchProjectSource(
  projectId: string,
  portalProjectId: string,
  filePath: string,
  line?: number,
  column?: number,
) {
  const url = `/projects/${encodeURIComponent(projectId)}/source${buildQuery({
    portal_project_id: portalProjectId,
    file_path: filePath,
    line: line && line > 0 ? String(line) : undefined,
    column: column && column > 0 ? String(column) : undefined,
  })}`;
  return readJson(await fetch(url)) as Promise<ProjectSourcePayload>;
}

export async function analyzeProject(
  projectId: string,
  portalProjectId: string,
  entry?: string,
  ruleSet: RuleSet = "GJB-8114",
) {
  const url = `/projects/${encodeURIComponent(projectId)}/analyze${buildQuery({
    portal_project_id: portalProjectId,
    entry,
  })}`;
  return readJson(
    await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rule_set: ruleSet }),
    }),
  );
}

export async function uploadProject(file: File) {
  const body = new FormData();
  body.append("file", file);
  return readJson(await fetch("/projects/upload", { method: "POST", body })) as Promise<{
    project_id: string;
    portal_project_id: string;
    source: string;
    message: string;
  }>;
}

/** 轮询分析任务状态，直到完成或失败。
 *
 * @param requestId  POST /analyze 或 POST /projects/{id}/analyze 返回的 request_id
 * @param intervalMs 轮询间隔（毫秒），默认 1500ms
 * @param timeoutMs  超时时间（毫秒），默认 300000ms（5 分钟）
 * @returns 任务完成后的完整 payload
 * @throws  超时或任务失败时抛出错误
 */
export async function pollAnalysisStatus(
  requestId: string,
  intervalMs = 1500,
  timeoutMs = 300_000,
  onPoll?: (count: number, status: string, task: any) => void,
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
    if (onPoll) onPoll(count, task.status || "unknown", task);

    // pending 或 running，等待后继续轮询
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

/** 启动项目分析 + 自动轮询，返回完整结果。 */
export async function analyzeProjectWithPolling(
  projectId: string,
  portalProjectId: string,
  entry?: string,
  ruleSet: RuleSet = "GJB-8114",
  pollIntervalMs = 1500,
  pollTimeoutMs = 300_000,
  onPoll?: (count: number, status: string, task: any) => void,
) {
  // 1. 提交分析任务
  const submitResp = await analyzeProject(projectId, portalProjectId, entry, ruleSet);
  const requestId = submitResp.request_id;
  if (!requestId) throw new Error("服务端未返回 request_id");

  // 2. 轮询等待结果
  return pollAnalysisStatus(requestId, pollIntervalMs, pollTimeoutMs, onPoll);
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

export async function fetchProjectRuleSetReports(
  projectId: string,
  portalProjectId: string,
) {
  const url = `/projects/${encodeURIComponent(projectId)}/reports${buildQuery({
    portal_project_id: portalProjectId,
  })}`;
  return readJson(await fetch(url)) as Promise<{
    project_id: string;
    portal_project_id?: string;
    reports: ProjectRuleSetReports;
  }>;
}

export async function fetchProjectRuleSetReport(
  projectId: string,
  portalProjectId: string,
  ruleSet: RuleSet,
) {
  const url = `/projects/${encodeURIComponent(projectId)}/reports/${encodeURIComponent(ruleSet)}${buildQuery({
    portal_project_id: portalProjectId,
  })}`;
  return readJson(await fetch(url));
}

export async function debugDcabStart() {
  return readJson(await fetch("/debug/dcab/start"));
}

export async function debugDcabCheck() {
  return readJson(await fetch("/debug/dcab/check"));
}

