export interface NormalizedDiagnostic {
  id: string;
  ruleId: string;
  checker: string;
  level: string;
  message: string;
  filePath: string;
  line: number;
  column: number;
  raw: any;
}

export interface NormalizedReport {
  requestId: string;
  status: string;
  message?: string;
  detectionId?: string;
  uniportalWriteback?: string; // "ok" 表示已写回共享卷, 含错误信息则表示写回失败
  summary: {
    total: number;
    warning: number;
    error: number;
    fileCount: number;
    ruleCount: number;
  };
  diagnostics: NormalizedDiagnostic[];
  raw: any;
}

function get(obj: any, path: string): any {
  return path
    .split(".")
    .reduce((acc, key) => (acc == null ? undefined : acc[key]), obj);
}

function firstValue(...values: any[]) {
  return values.find(
    (value) => value !== undefined && value !== null && value !== "",
  );
}

function firstArray(...values: any[]) {
  return values.find(Array.isArray) || [];
}

function toNumber(value: any): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function normalizeLevel(value: any): string {
  const text = String(value ?? "Warning").trim();
  if (text === "1" || /^error$/i.test(text) || /严重|错误/.test(text))
    return "Error";
  if (/^warn/i.test(text) || /警告/.test(text)) return "Warning";
  if (/^note$/i.test(text)) return "Note";
  return text || "Warning";
}

function normalizeDiagnostic(item: any, index: number): NormalizedDiagnostic {
  const track = Array.isArray(item?.tracking_path_list)
    ? item.tracking_path_list[0]
    : undefined;
  const location = item?.location || {};
  const trackLocation = track?.location_start || track?.location || {};
  const ruleId = String(
    firstValue(
      item?.rule_id,
      item?.ruleId,
      item?.check,
      item?.checker,
      item?.rule,
      item?.id,
      track?.rule_id,
      "(unknown)",
    ),
  );
  const checker = String(
    firstValue(item?.checker, item?.check, item?.name, item?.type, ruleId, ""),
  );
  const message = String(
    firstValue(
      item?.message,
      item?.description,
      item?.descript,
      track?.descript,
      track?.description,
      "",
    ),
  );
  const filePath = String(
    firstValue(
      item?.file,
      item?.file_path,
      item?.filePath,
      item?.path,
      location?.file,
      location?.file_path,
      location?.filePath,
      track?.file_path,
      track?.file,
      "",
    ),
  );
  const line = toNumber(
    firstValue(item?.line, location?.line, trackLocation?.line),
  );
  const column = toNumber(
    firstValue(item?.column, location?.column, trackLocation?.column),
  );
  const level = normalizeLevel(
    firstValue(item?.level, item?.severity, item?.force, item?.type, "Warning"),
  );

  return {
    id: `${ruleId}-${filePath}-${line}-${column}-${index}`,
    ruleId,
    checker,
    level,
    message,
    filePath,
    line,
    column,
    raw: item,
  };
}

function collectDiagnostics(raw: any): NormalizedDiagnostic[] {
  const report = raw?.report || raw || {};
  const list = firstArray(
    report?.diagnostics,
    report?.defect_list,
    report?.summary?.bugs,
    report?.dcab_raw?.check_progress?.defect_list,
    raw?.defect_list,
    raw?.summary?.bugs,
    raw?.dcab_raw?.check_progress?.defect_list,
  );
  return list.map((item: any, index: number) =>
    normalizeDiagnostic(item, index),
  );
}

function inferFileCount(raw: any, diagnostics: NormalizedDiagnostic[]) {
  const report = raw?.report || raw || {};
  const summary = report?.summary || raw?.summary || {};
  return toNumber(
    firstValue(
      summary?.total_files,
      summary?.file_count,
      report?.total_files,
      report?.file_count,
      Array.isArray(report?.files_stats)
        ? report.files_stats.length
        : undefined,
      new Set(diagnostics.map((item) => item.filePath).filter(Boolean)).size,
    ),
  );
}

function makeMessage(raw: any, status: string, count: number) {
  if (raw?.detection_id) return "已启动分析，等待结果查询";
  if (status === "dcab_started") return "DCAB 分析已启动，等待后续检查结果";
  if (status === "check_progress_empty")
    return "check_progress 暂无缺陷数据，可展开原始 JSON 排查";
  if (status === "completed" && count === 0) return "分析完成，未发现缺陷";
  if (count === 0) return "当前结果未发现可展示缺陷";
  return `已解析 ${count} 条缺陷`;
}

export function normalizeReport(raw: any): NormalizedReport {
  const diagnostics = collectDiagnostics(raw);
  const report = raw?.report || raw || {};
  const rawSummary = report?.summary || raw?.summary || {};
  const byLevel = rawSummary?.by_level || {};
  const warning = toNumber(
    firstValue(
      byLevel.Warning,
      byLevel.WARNING,
      diagnostics.filter((item) => /^warn/i.test(item.level)).length,
    ),
  );
  const error = toNumber(
    firstValue(
      byLevel.Error,
      byLevel.ERROR,
      diagnostics.filter((item) => /^error/i.test(item.level)).length,
    ),
  );
  const rules = new Set(diagnostics.map((item) => item.ruleId).filter(Boolean));
  const status = String(
    firstValue(
      raw?.status,
      raw?.report?.status,
      raw?.state,
      raw?.report?.state,
      diagnostics.length ? "completed" : "unknown",
    ),
  );
  const detectionId = firstValue(
    raw?.detection_id,
    raw?.detectionId,
    raw?.report?.detection_id,
    raw?.report?.detectionId,
  );
  const requestId = String(
    firstValue(
      raw?.request_id,
      raw?.requestId,
      raw?.report?.request_id,
      raw?.report?.report_id,
      detectionId,
      "",
    ),
  );
  // 共享卷写回状态: "ok" = 成功, 字符串 = 错误信息, undefined = 非 UniPortal 项目
  const uniportalWriteback = firstValue(
    raw?.uniportal_writeback,
    raw?.report?.uniportal_writeback,
    raw?.uniportal_writeback_error,
    raw?.report?.uniportal_writeback_error,
  );

  return {
    requestId,
    status,
    message: makeMessage(raw, status, diagnostics.length),
    detectionId: detectionId ? String(detectionId) : undefined,
    uniportalWriteback: uniportalWriteback
      ? String(uniportalWriteback)
      : undefined,
    summary: {
      total: toNumber(firstValue(rawSummary?.total_bugs, diagnostics.length)),
      warning,
      error,
      fileCount: inferFileCount(raw, diagnostics),
      ruleCount: Object.keys(rawSummary?.by_rule || {}).length || rules.size,
    },
    diagnostics,
    raw,
  };
}
