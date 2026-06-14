# ct8114 — GJB 8114 代码分析服务 输出说明

> 本文档描述 ct8114 子工具写入 UniPortal 共享卷的输出格式、字段定义与数据卷约定。

---

## 数据卷约定

ct8114 在 UniPortal 共享卷中使用的输出文件夹名称为 **`_ct8114`**（位于每个项目根目录下）。

```
{UNIPORTAL_STORAGE_PATH}/
└── {portal_project_id}/
    └── {project_id}/                     # 项目源码目录
        ├── src/                          # C/C++ 源文件
        │   ├── main.c
        │   └── utils.h
        ├── _ct8114/                      # ← ct8114 输出目录
        │   └── last_report.json          # 分析报告
        └── meta.json                     # 项目元信息（含 ct8114 分析摘要）
```

> **注意**: `_ct8114` 以下划线开头，表示该目录由子工具自动生成，不会被 UniPortal 当作项目源码扫描。

---

## 输出文件

**输出文件名**：`last_report.json`

**位置**：`{project_dir}/_ct8114/last_report.json`

---

## 字段定义表

### 顶层字段

| 字段路径 | 类型 | 必填 | 说明 | 举例 |
|----------|------|------|------|------|
| `request_id` | 字符串 | 是 | 本次分析请求的唯一标识 | `"proj_a1b2c3d4e5f6"` |
| `project_id` | 字符串 | 是 | 项目唯一标识（与 UniPortal item_id 对应） | `"demo_project"` |
| `report` | 对象 | 是 | 分析报告主体 | 见下方 |
| `uniportal_writeback` | 字符串 | 是 | 写回状态：`"ok"` 成功，其他值表示失败 | `"ok"` |
| `uniportal_writeback_path` | 字符串 | 否 | 写回文件的绝对路径 | `"/data/uniportal/.../last_report.json"` |
| `uniportal_writeback_time` | 字符串 | 否 | 写回时间（ISO 8601） | `"2026-06-14T16:57:08"` |
| `saved_report` | 字符串 | 否 | 本地报告存储路径 | `"workspaces/_reports/demo_project/last_report.json"` |

### `report` 对象

| 字段路径 | 类型 | 必填 | 说明 | 举例 |
|----------|------|------|------|------|
| `report.report_id` | 字符串 | 是 | 报告唯一标识 | `"mock_20260614_163504"` |
| `report.project_name` | 字符串 | 是 | 项目名称 | `"demo_project"` |
| `report.project_path` | 字符串 | 是 | 项目源码根目录路径 | `"/data/uniportal/proj_001/demo_project"` |
| `report.files_stats` | 数组 | 是 | 每个源文件的统计信息 | 见下方 |
| `report.summary` | 对象 | 是 | 分析结果摘要 | 见下方 |

### `report.files_stats[]` — 文件统计

| 字段路径 | 类型 | 必填 | 说明 | 举例 |
|----------|------|------|------|------|
| `files_stats[].file_path` | 字符串 | 是 | 源文件路径 | `"src/main.c"` |
| `files_stats[].total_lines` | 整数 | 是 | 文件总行数 | `156` |
| `files_stats[].total_statements` | 整数 | 是 | 语句总数 | `42` |
| `files_stats[].function_count` | 整数 | 是 | 函数数量 | `3` |
| `files_stats[].function_max_depth` | 整数 | 是 | 函数最大嵌套深度 | `4` |
| `files_stats[].comment_lines` | 整数 | 是 | 注释行数 | `18` |
| `files_stats[].bug_count` | 整数 | 是 | 该文件违规数 | `5` |
| `files_stats[].bugs` | 数组 | 是 | 违规诊断列表 | 见下方 |

### `files_stats[].bugs[]` — 诊断条目

| 字段路径 | 类型 | 必填 | 说明 | 举例 |
|----------|------|------|------|------|
| `bugs[].checker` | 字符串 | 是 | 检查器名称 | `"clang-analyzer-gjb.statement.CodeUnreachable"` |
| `bugs[].file_path` | 字符串 | 是 | 违规所在文件 | `"src/main.c"` |
| `bugs[].line` | 整数 | 是 | 违规所在行号 | `42` |
| `bugs[].column` | 整数 | 是 | 违规所在列号 | `15` |
| `bugs[].message` | 字符串 | 是 | 诊断消息（含规则说明） | `"GJB-R-1-8-1: This statement is never executed"` |
| `bugs[].rule_id` | 字符串 | 是 | GJB 8114 规则编号 | `"GJB-R-1-8-1"` |
| `bugs[].level` | 字符串 | 是 | 违规级别：`"Error"` 或 `"Warning"` | `"Warning"` |
| `bugs[].force` | 字符串 | 是 | 强制级别：`"1"`=强制，`"0"`=推荐 | `"1"` |

### `report.summary` — 摘要

| 字段路径 | 类型 | 必填 | 说明 | 举例 |
|----------|------|------|------|------|
| `summary.total_bugs` | 整数 | 是 | 违规总数 | `11` |
| `summary.total_files` | 整数 | 是 | 分析文件总数 | `4` |
| `summary.by_level` | 对象 | 是 | 按级别统计 `{"Error": N, "Warning": M}` | `{"Error": 3, "Warning": 8}` |
| `summary.by_rule` | 对象 | 是 | 按规则统计 `{"GJB-R-x-x-x": N}` | `{"GJB-R-1-3-8": 2, "GJB-R-1-8-1": 1}` |
| `summary.by_checker` | 对象 | 是 | 按检查器统计 | `{"clang-analyzer-gjb.statement.CodeUnreachable": 1}` |
| `summary.by_file` | 对象 | 是 | 按文件统计 `{"file_path": N}` | `{"src/main.c": 5}` |
| `summary.bugs` | 数组 | 是 | 所有违规条目摘要（结构与 `files_stats[].bugs[]` 一致） | 见上方 bugs[] |

---

## 示例 JSON

```json
{
  "request_id": "proj_a1b2c3d4e5f6",
  "project_id": "demo_project",
  "report": {
    "report_id": "codetidy_20260614_163504",
    "project_name": "demo_project",
    "project_path": "/data/uniportal/proj_001/demo_project",
    "files_stats": [
      {
        "file_path": "src/main.c",
        "total_lines": 156,
        "total_statements": 42,
        "function_count": 3,
        "function_max_depth": 4,
        "comment_lines": 18,
        "bug_count": 2,
        "bugs": [
          {
            "checker": "clang-analyzer-gjb.statement.CodeUnreachableBranch",
            "file_path": "src/main.c",
            "line": 42,
            "column": 5,
            "message": "GJB-R-1-8-1 : This statement is never executed",
            "rule_id": "GJB-R-1-8-1",
            "level": "Warning",
            "force": "1"
          },
          {
            "checker": "clang-analyzer-gjb.statement.BranchNoBrace",
            "file_path": "src/main.c",
            "line": 18,
            "column": 9,
            "message": "GJB-R-1-3-8 : 分支语句必须使用大括号",
            "rule_id": "GJB-R-1-3-8",
            "level": "Error",
            "force": "1"
          }
        ]
      },
      {
        "file_path": "src/utils.h",
        "total_lines": 32,
        "total_statements": 5,
        "function_count": 0,
        "function_max_depth": 0,
        "comment_lines": 8,
        "bug_count": 0,
        "bugs": []
      }
    ],
    "summary": {
      "total_bugs": 2,
      "total_files": 2,
      "by_level": {
        "Error": 1,
        "Warning": 1
      },
      "by_rule": {
        "GJB-R-1-3-8": 1,
        "GJB-R-1-8-1": 1
      },
      "by_checker": {
        "clang-analyzer-gjb.statement.CodeUnreachableBranch": 1,
        "clang-analyzer-gjb.statement.BranchNoBrace": 1
      },
      "by_file": {
        "src/main.c": 2
      },
      "bugs": [
        {
          "checker": "clang-analyzer-gjb.statement.CodeUnreachableBranch",
          "file_path": "src/main.c",
          "line": 42,
          "column": 5,
          "message": "GJB-R-1-8-1 : This statement is never executed",
          "rule_id": "GJB-R-1-8-1",
          "level": "Warning",
          "force": "1"
        },
        {
          "checker": "clang-analyzer-gjb.statement.BranchNoBrace",
          "file_path": "src/main.c",
          "line": 18,
          "column": 9,
          "message": "GJB-R-1-3-8 : 分支语句必须使用大括号",
          "rule_id": "GJB-R-1-3-8",
          "level": "Error",
          "force": "1"
        }
      ]
    }
  },
  "uniportal_writeback": "ok",
  "uniportal_writeback_path": "/data/uniportal/proj_001/demo_project/_ct8114/last_report.json",
  "uniportal_writeback_time": "2026-06-14T16:57:08",
  "saved_report": "workspaces/_reports/demo_project/last_report.json"
}
```

---

## 辅助输出：`meta.json`

ct8114 同时更新项目根目录下的 `meta.json`，写入分析摘要，便于 UniPortal 快速展示：

```json
{
  "ct8114_last_analysis": "2026-06-14T16:57:08.141774",
  "ct8114_report_path": "/data/uniportal/proj_001/demo_project/_ct8114/last_report.json",
  "ct8114_summary": {
    "total_bugs": 2,
    "total_files": 2,
    "by_level": { "Error": 1, "Warning": 1 },
    "by_rule": { "GJB-R-1-3-8": 1, "GJB-R-1-8-1": 1 }
  }
}
```

| 字段路径 | 类型 | 必填 | 说明 |
|----------|------|------|------|
| `ct8114_last_analysis` | 字符串 | 是 | 最近分析时间（ISO 8601） |
| `ct8114_report_path` | 字符串 | 是 | 完整报告文件路径 |
| `ct8114_summary` | 对象 | 是 | 分析摘要（与 `report.summary` 子集一致） |
| `ct8114_summary.total_bugs` | 整数 | 是 | 违规总数 |
| `ct8114_summary.total_files` | 整数 | 是 | 分析文件数 |
| `ct8114_summary.by_level` | 对象 | 是 | 按级别统计 |
| `ct8114_summary.by_rule` | 对象 | 是 | 按规则统计 |
