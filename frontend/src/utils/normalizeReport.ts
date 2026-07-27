export type BusinessSeverity = "advisory" | "required" | "other" | "unknown";

export interface NormalizedDiagnostic {
  id: string;
  ruleId: string;
  checker: string;
  level: string;
  severity: BusinessSeverity;
  severityLabel: string;
  severitySource: string;
  force: string;
  typeCode: string;
  status: string;
  rawSeverity?: string;
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
  uniportalWriteback?: string;
  summary: {
    total: number;
    warning: number;
    error: number;
    advisory: number;
    required: number;
    other: number;
    unknown: number;
    fileCount: number;
    ruleCount: number;
  };
  diagnostics: NormalizedDiagnostic[];
  raw: any;
}

const SEVERITY_LABELS: Record<BusinessSeverity, string> = {
  advisory: "Advisory",
  required: "Required",
  other: "Other",
  unknown: "Unknown",
};

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

function asText(value: any): string {
  return value === undefined || value === null ? "" : String(value);
}

function normalizeLevel(value: any): string {
  const text = String(value ?? "").trim();
  if (text === "1" || /^error$/i.test(text) || /严重|错误/.test(text))
    return "Error";
  if (text === "0" || /^warn/i.test(text) || /警告/.test(text)) return "Warning";
  if (/^other$/i.test(text)) return "Other";
  if (/^unknown$/i.test(text)) return "Unknown";
  if (/^note$/i.test(text)) return "Other";
  return text || "Unknown";
}

function normalizeStandardSeverity(value: any): BusinessSeverity | "" {
  const text = String(value ?? "").trim().toLowerCase();
  if (!text) return "";
  if (
    ["required", "must_fix", "mandatory", "error", "1"].includes(text) ||
    /必须|强制/.test(text)
  )
    return "required";
  if (
    ["advisory", "warning", "warn", "0"].includes(text) ||
    /普通|警告|推荐/.test(text)
  )
    return "advisory";
  if (["other", "note", "info"].includes(text)) return "other";
  if (text === "unknown") return "unknown";
  return "other";
}

function normalizeSeverity(item: any) {
  const severityValue = firstValue(item?.severity, item?.raw_severity);
  const standard = normalizeStandardSeverity(severityValue);
  if (standard) {
    return {
      severity: standard,
      severityLabel: asText(item?.severity_label) || SEVERITY_LABELS[standard],
      severitySource: asText(item?.severity_source) || "dcab.severity",
      rawSeverity: asText(severityValue) || undefined,
    };
  }

  const force = asText(item?.force).trim();
  if (force === "1") {
    return {
      severity: "required" as BusinessSeverity,
      severityLabel: SEVERITY_LABELS.required,
      severitySource: "dcab.force",
      rawSeverity: undefined,
    };
  }
  if (force === "0") {
    return {
      severity: "advisory" as BusinessSeverity,
      severityLabel: SEVERITY_LABELS.advisory,
      severitySource: "dcab.force",
      rawSeverity: undefined,
    };
  }

  if (asText(firstValue(item?.type_code, item?.type)).trim() || asText(item?.status).trim()) {
    return {
      severity: "other" as BusinessSeverity,
      severityLabel: SEVERITY_LABELS.other,
      severitySource: "dcab.type/status",
      rawSeverity: undefined,
    };
  }

  return {
    severity: "unknown" as BusinessSeverity,
    severityLabel: SEVERITY_LABELS.unknown,
    severitySource: "fallback",
    rawSeverity: undefined,
  };
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
  const severityInfo = normalizeSeverity(item);
  const level = normalizeLevel(
    firstValue(
      item?.level,
      severityInfo.severity === "required" ? "Error" : undefined,
      severityInfo.severity === "advisory" ? "Warning" : undefined,
      severityInfo.severity === "other" ? "Other" : undefined,
      severityInfo.severity === "unknown" ? "Unknown" : undefined,
      item?.force,
    ),
  );
  const force = asText(item?.force);
  const typeCode = asText(firstValue(item?.type_code, item?.type));
  const status = asText(item?.status);

  return {
    id: `${ruleId}-${filePath}-${line}-${column}-${index}`,
    ruleId,
    checker,
    level,
    severity: severityInfo.severity,
    severityLabel: severityInfo.severityLabel,
    severitySource: severityInfo.severitySource,
    force,
    typeCode,
    status,
    rawSeverity: severityInfo.rawSeverity,
    message,
    filePath,
    line,
    column,
    raw: item,
  };
}

function fileStatBugs(raw: any): any[] {
  const report = raw?.report || raw || {};
  if (!Array.isArray(report?.files_stats)) return [];
  return report.files_stats.flatMap((file: any) =>
    Array.isArray(file?.bugs)
      ? file.bugs.map((bug: any) => ({ ...bug, file_path: firstValue(bug?.file_path, file?.file_path) }))
      : [],
  );
}

function collectDiagnostics(raw: any): NormalizedDiagnostic[] {
  const report = raw?.report || raw || {};
  const list = firstArray(
    report?.diagnostics,
    report?.defect_list,
    report?.summary?.bugs,
    fileStatBugs(raw),
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
  const bySeverity = rawSummary?.by_severity || {};
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
  const advisory = toNumber(
    firstValue(
      bySeverity.advisory,
      diagnostics.filter((item) => item.severity === "advisory").length,
    ),
  );
  const required = toNumber(
    firstValue(
      bySeverity.required,
      diagnostics.filter((item) => item.severity === "required").length,
    ),
  );
  const other = toNumber(
    firstValue(
      bySeverity.other,
      diagnostics.filter((item) => item.severity === "other").length,
    ),
  );
  const unknown = toNumber(
    firstValue(
      bySeverity.unknown,
      diagnostics.filter((item) => item.severity === "unknown").length,
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
  const uniportalWritebackError = firstValue(
    raw?.uniportal_writeback_error,
    raw?.report?.uniportal_writeback_error,
  );
  const uniportalWriteback = firstValue(
    uniportalWritebackError,
    raw?.uniportal_writeback,
    raw?.report?.uniportal_writeback,
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
      advisory,
      required,
      other,
      unknown,
      fileCount: inferFileCount(raw, diagnostics),
      ruleCount: Object.keys(rawSummary?.by_rule || {}).length || rules.size,
    },
    diagnostics,
    raw,
  };
}
