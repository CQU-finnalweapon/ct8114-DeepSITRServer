# ct8114 — GJB 8114 代码分析服务 (DeepSITRServer / codetidy 引擎)

> 基于 **FastAPI** + **Vue 3** 的 **codetidy.exe (DeepSITRServer)** 在线静态代码分析服务  
> 支持即时上传分析、UniPortal 项目分析和预生成报告加载

---

## 项目概述

`ct8114` 是一个基于 Python **FastAPI** 构建的 Web 服务，前端使用 **Vue 3 + Vite + TypeScript**，后端使用 DeepSITRServer 内置的 **codetidy.exe** 作为唯一分析引擎，对 C/C++ 代码进行**军用编码规范 (GJB 8114)** 合规检测。

> **v2 架构变更**: 本版本已完全移除 clang-tidy + 插件方案，统一使用 DeepSITRServer 的 codetidy.exe 引擎，分析结果以 DSIT 兼容格式（`.xplusx.err` JSON）输出。

### 三种工作模式

| 模式                   | 说明                                                                    | 入口            |
| ---------------------- | ----------------------------------------------------------------------- | --------------- |
| **即时上传分析**       | 浏览器上传 C/C++ 文件，codetidy 实时分析，返回 DSIT 格式诊断            | `POST /analyze` |
| **UniPortal 项目分析** | 接入 UniPortal 平台，对共享卷中的项目进行批量分析（双源接入）           | `/projects/*`   |
| **加载已有报告**       | 加载 DeepSITRServer 预生成的输出目录（`.xplusx.err` / `.sta` / `.rst`） | `/dsit/*`       |

---

## 技术栈

| 层级             | 技术                                                                              |
| ---------------- | --------------------------------------------------------------------------------- |
| **后端框架**     | **FastAPI** (Python 3) + Uvicorn ASGI                                             |
| **前端框架**     | **Vue 3** + **Vite** + **TypeScript**                                             |
| **静态分析引擎** | **codetidy.exe** (DeepSITRServer 内置, clang-tidy + GJB 8114 规则)                |
| **报告解析**     | `dsit_parser.py` — 解析 `.xplusx.err` JSON + codetidy 实时输出                    |
| **容器化**       | **Docker** + **docker-compose**（基于 `ghcr.io/gjb8114/clang-tidy-gjb8114` 镜像） |
| **代码规范**     | GJB 8114 军用软件编码规范                                                         |

### Python 依赖

```
fastapi
uvicorn[standard]
python-multipart
pyyaml
```

### 前端依赖

```
vue 3.5
vite 6.3
typescript 5.8
vue-tsc 2.2
```

---

## 项目结构

```
ct8114-DeepSITRServer/
├── server.py              # FastAPI 主服务（路由、中间件、配置）
├── routers_dsit.py        # DSIT 报告管理 API 路由
├── dsit_parser.py         # codetidy 调用封装 + DSIT 输出解析
├── fixes_parser.py        # 修复建议解析器
├── requirements.txt       # Python 依赖
├── dockerfile             # Docker 镜像构建
├── docker-compose.yml     # Docker Compose 编排
├── docker-compose.override.yml  # 本地开发覆盖（模拟共享卷）
├── .env.example           # 本地开发环境变量示例
├── build.sh               # 构建脚本
├── run.sh                 # 运行脚本
├── mock_uniportal/        # 本地模拟 UniPortal 共享卷（测试用）
│   ├── proj_001/          #   模拟 UniPortal 工程
│   │   └── demo_project/  #     模拟项目（含测试源码）
│   └── proj_002/          #   模拟 UniPortal 工程
│       └── hello_world/   #     模拟项目（含测试源码）
├── static/                # 静态资源（旧版纯 HTML 首页）
├── frontend/              # Vue 3 + Vite 前端项目
│   ├── src/
│   │   ├── App.vue        # 根组件
│   │   ├── main.ts        # 入口
│   │   ├── components/    # 通用组件
│   │   ├── api/           # API 调用层
│   │   ├── utils/         # 工具函数
│   │   └── styles.css     # 全局样式
│   ├── index.html         # HTML 模板
│   ├── vite.config.ts     # Vite 配置
│   └── package.json       # 前端依赖
├── workspaces/            # 项目工作空间（本地项目 + 报告存储）
└── test_*.py              # 测试脚本
```

---

## 快速开始

### 本地运行

```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 启动服务
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

服务启动后访问：`http://localhost:8000`

### Docker 部署

```bash
# 构建镜像
docker build -t ct8114-server -f dockerfile .

# 或使用 docker-compose
docker-compose up -d
```

### 前端开发

```bash
cd frontend
npm install
npm run dev        # 开发模式
npm run build      # 生产构建 → ../static/
```

---

## API 接口详情

### A. 即时上传分析

```
POST /analyze
Content-Type: multipart/form-data

参数:
  files: 上传的 C/C++ 源文件（可多个）
  entry: 指定主入口文件名（可选）
  keep:  调试用，保留服务端临时文件（可选，默认 false）
```

**流程**：

1. 生成 UUID，在系统临时目录下建立工作目录
2. 上传文件落盘，调用 `codetidy.exe` 进行分析
3. 解析输出，以 **DSIT 兼容 JSON** 格式返回前端
4. 默认清理临时目录（可通过 `?keep=true` 保留）

### B. UniPortal 项目分析（双源接入 — 可读写共享卷）

| API                      | 方法     | 说明                                          |
| ------------------------ | -------- | --------------------------------------------- |
| `/projects`              | `GET`    | 列出两个数据源的项目列表                      |
| `/projects/{id}/files`   | `GET`    | 列出项目内可分析的源文件                      |
| `/projects/{id}/analyze` | `POST`   | 对项目执行 codetidy 分析并写回报告            |
| `/projects/{id}`         | `DELETE` | 删除项目（共享卷可写时支持删 UniPortal 项目） |

**双源数据约定**：

1. 先查 `UNIPORTAL_STORAGE_PATH/{portal_proj_id}/{project_id}/`（UniPortal 共享卷，可读写）
2. 再查 `LOCAL_WORKSPACES_DIR/{project_id}/`（子工具自上传，读写）

**共享卷读写机制**：

当 `UNIPORTAL_WRITABLE=true`（默认）或使用模拟卷时，分析完成后会自动写回：

- **报告文件**: `{project_dir}/_ct8114/last_report.json`
- **元信息**: `{project_dir}/meta.json`（含最近分析时间、报告摘要）

```
UniPortal 共享卷目录结构:
{portal_proj_id}/
└── {project_id}/              # 项目源码目录
    ├── src/                   # C/C++ 源码
    ├── _ct8114/               # ct8114 写回目录
    │   └── last_report.json   # 最新分析报告
    └── meta.json              # 项目元信息（含分析摘要）
```

### C. 加载已有报告 (DSIT)

| API                         | 方法     | 说明                    |
| --------------------------- | -------- | ----------------------- |
| `/dsit/upload`              | `POST`   | 上传 DSIT 输出目录 .zip |
| `/dsit/upload-local`        | `POST`   | 从本地路径加载输出目录  |
| `/dsit/reports`             | `GET`    | 列出已加载报告          |
| `/dsit/report/{id}`         | `GET`    | 获取完整报告            |
| `/dsit/report/{id}/summary` | `GET`    | 获取报告摘要            |
| `/dsit/report/{id}`         | `DELETE` | 删除报告                |

### API 一览

| 端点                     | 方法     | 说明                     |
| ------------------------ | -------- | ------------------------ |
| `/`                      | `GET`    | 根路径，重定向到前端首页 |
| `/analyze`               | `POST`   | 即时上传分析（codetidy） |
| `/projects`              | `GET`    | 获取项目列表（双源合并） |
| `/projects/{id}/files`   | `GET`    | 获取项目内源文件列表     |
| `/projects/{id}/analyze` | `POST`   | 对项目执行 codetidy 分析 |
| `/projects/{id}`         | `DELETE` | 删除私有项目             |
| `/dsit/*`                | 多种     | 加载/查看/删除 DSIT 报告 |

---

## 环境变量

| 变量                     | 默认值                     | 说明                                                         |
| ------------------------ | -------------------------- | ------------------------------------------------------------ |
| `MAX_TOTAL_BYTES`        | `5242880` (5MB)            | 即时上传文件总大小限制                                       |
| `MAX_ZIP_BYTES`          | `52428800` (50MB)          | ZIP 上传大小限制                                             |
| `MAX_ZIP_EXTRACT_BYTES`  | `209715200` (200MB)        | ZIP 解压后大小限制                                           |
| `UNIPORTAL_STORAGE_PATH` | —                          | UniPortal 共享卷路径（Docker: `/data/uniportal`）            |
| `UNIPORTAL_WRITABLE`     | `true`                     | 共享卷是否可写（`true`=可读写, `false`=只读）                |
| `MOCK_UNIPORTAL_DIR`     | —                          | 本地模拟共享卷路径（设置后启用模拟模式，无需真实 UniPortal） |
| `LOCAL_WORKSPACES_DIR`   | `workspaces`               | 本地项目存储目录                                             |
| `REPORTS_DIR`            | `workspaces/_reports`      | 分析报告存储目录                                             |
| `DSIT_REPORTS_DIR`       | `workspaces/_dsit_reports` | DSIT 报告存储目录                                            |

---

## Docker 架构

```
┌──────────────────────────────────────────────────┐
│                   ct8114 容器                      │
│                                                   │
│  FastAPI (Uvicorn) :8000                          │
│       │                                            │
│       ├── codetidy.exe (GJB 8114 分析)             │
│       ├── /app/local_workspaces/ (本地私有项目)     │
│       ├── /app/workspaces/_tasks/ (任务沙盒)       │
│       └── /data/uniportal/ (UniPortal 共享卷 ↔️)   │
│            ↑↓ 双向读写                              │
│            ├── 读取: 项目源码                       │
│            └── 写回: _ct8114/last_report.json      │
└──────────────────────────────────────────────────┘
```

### 本地开发测试（模拟共享卷）

无需真实 UniPortal，使用 `mock_uniportal/` 目录模拟共享卷：

```bash
# 方式一: 直接设置环境变量启动
$env:MOCK_UNIPORTAL_DIR="mock_uniportal"
uvicorn server:app --host 0.0.0.0 --port 8000 --reload

# 方式二: 使用 docker-compose.override.yml（已预置）
docker-compose up -d

# 方式三: Linux/macOS
MOCK_UNIPORTAL_DIR=mock_uniportal uvicorn server:app --host 0.0.0.0 --port 8000
```

启动后访问 `http://localhost:8000/projects` 即可看到模拟共享卷中的项目列表，运行分析后检查 `mock_uniportal/proj_001/demo_project/_ct8114/last_report.json` 确认写回成功。

---

## 支持的文件类型

| 类型       | 后缀                  |
| ---------- | --------------------- |
| C 源文件   | `.c`                  |
| C 头文件   | `.h`                  |
| C++ 源文件 | `.cc`, `.cpp`, `.cxx` |
| C++ 头文件 | `.hpp`, `.hxx`        |

---

## License

Internal use — GJB 8114 Military Software Coding Standards Compliance Tool.
| `/healthz` | `GET` | 健康检查（含引擎信息） |

---

## 核心文件说明

### `server.py` — 主服务程序

FastAPI 应用入口，包含全部 REST API 端点。核心逻辑：

- **环境变量配置**：
  - `CODETIDY_BIN` — codetidy.exe 路径（默认 DeepSITRServer 内置路径）
  - `CODETIDY_CHECKS` — 启用的检查规则（默认 `clang-analyzer-gjb*`）
  - `UNIPORTAL_STORAGE_PATH` — UniPortal 共享卷路径
  - `LOCAL_WORKSPACES_DIR` — 本地工作区目录
  - `MAX_TOTAL_BYTES` — 上传文件大小上限（默认 5MB）

- **文件过滤**：仅允许 `.c/.h/.cc/.cpp/.cxx/.hpp/.hxx` 后缀
- **安全机制**：文件名防路径穿越、文件大小限制、超时控制

### `dsit_parser.py` — 核心分析层

提供两大功能：

**A. codetidy 实时分析引擎**（替代 clang-tidy）：

- `analyze_with_codetidy()` — 对源文件运行 codetidy.exe 并返回 DSITReport
- `run_codetidy()` — 底层 codetidy.exe 调用
- 自动收集 include 目录、处理编码、解析诊断输出

**B. DeepSITRServer 输出解析**：

- `parse_xplusx_err()` — 解析 `.xplusx.err` JSON 诊断文件
- `parse_sta()` / `parse_rst()` — 解析统计/元数据文件
- `parse_output_dir()` — 递归扫描整个输出目录

**数据模型**：

- `DSITReport` — 完整分析报告
- `DSITBug` — 单条诊断（checker/rule_id/line/column/level/message）
- `DSITFileStats` — 单文件统计

### `routers_dsit.py` — DSIT 报告管理路由

独立的 FastAPI Router，提供 6 个端点管理预生成的 DeepSITRServer 报告。

### `fixes_parser.py` — 旧版 YAML 解析器（保留兼容）

解析 clang-tidy `-export-fixes` 输出的 YAML 文件。v2 中不再作为主要解析路径，保留用于可能的兼容需求。

### `static/index.html` — 前端页面

纯静态 HTML 页面，提供三个模式标签：

- **「直接上传」** — 拖拽/选择文件 → codetidy 实时分析 → DSIT 格式诊断卡片
- **「项目库」** — UniPortal + 本地项目浏览 → codetidy 批量分析
- **「加载报告」** — 上传/加载预生成的 DeepSITRServer 输出

功能包括：统计面板、规则筛选 chip、搜索过滤、诊断卡片（规则编号 + 文件路径 + 行列号 + 错误描述）

### `test.c` — 测试用例

一个简单的 C 源码文件，包含 `byte_array` 结构体的实现，用于验证分析功能是否正常工作。

### `run.sh` — 启动脚本

```bash
docker run -d --restart=always \
  --name ct8114 \
  -p 8006:8006 \
  ct8114:v1 \
  uvicorn server:app --host 0.0.0.0 --port 8006
```

默认运行在 `8006` 端口，可通过 `PORT` 环境变量自定义。

### `build.sh` — 构建脚本

```bash
docker build -t ct8114:v1 .
```

### `dockerfile` — Docker 构建文件

基于 `ghcr.io/gjb8114/clang-tidy-gjb8114:latest` 镜像构建，该镜像预装了含 GJB 8114 规则的自定义 clang-tidy。

### `docker-compose.yml` — Docker Compose 配置

定义服务和持久化卷挂载。

### DSIT 集成文件（新增）

| 文件                       | 说明                                                                 |
| :------------------------- | :------------------------------------------------------------------- |
| `dsit_parser.py`           | DeepSITRServer 输出解析器，支持 `.xplusx.err` JSON / `.sta` / `.rst` |
| `routers_dsit.py`          | DSIT API 路由（6 个端点：上传/列表/报告/摘要/删除）                  |
| `test_dsit_integration.py` | 自测脚本（全自动启动→加载→验证→输出）                                |

---

## 运行方式

### Docker 方式（推荐）

```bash
# 1. 构建镜像
bash build.sh

# 2. 启动服务
bash run.sh
```

### 开发模式

```bash
# 安装依赖
pip install -r requirements.txt

# 启动开发服务器
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

---

## 项目与 UniPortal 的关系

该项目是 **UniPortal 平台**下的一个**子工具**，遵循双源数据接入约定：

- **UniPortal 共享卷**（只读）：存放由 UniPortal 上传的项目文件
- **私有卷**（读写）：存放子工具自行上传的项目和分析报告

这种设计实现了**数据隔离**和**工程隔离**，确保 UniPortal 的项目数据不会被误修改，同时子工具可以在私有工作区自由读写分析结果。

---

## 与其他项目的关系

| 项目               | 关系                                                                            |
| ------------------ | ------------------------------------------------------------------------------- |
| **DeepSITRServer** | ct8114 使用其内置的 codetidy.exe 作为唯一分析引擎，同时支持加载 DSIT 预生成报告 |
| **UniPortal**      | ct8114 以子工具形式接入 UniPortal 平台，作为其代码分析流水线的一环              |

---

## DeepSITRServer 集成 (DSIT)

ct8114 现已支持加载和展示 **DeepSITRServer**（Qt5 桌面分析工具）产出的分析结果。

### 解析器 (`dsit_parser.py`)

解析 DeepSITRServer 输出目录中的文件：

| 后缀            | 格式 | 说明                                                              |
| :-------------- | :--- | :---------------------------------------------------------------- |
| `.xplusx.err`   | JSON | **核心**，结构化 bug 报告（checker/message/line/column/standard） |
| `.sta`          | 文本 | 文件统计（总行数/函数数/复杂度/注释行数等）                       |
| `.rst`          | XML  | 项目级元数据（项目路径/分析时间）                                 |
| `.cgp` / `.cgf` | 文本 | 调用图/检查器配置                                                 |

### DSIT API 端点

| 方法     | 路径                        | 说明                                              |
| :------- | :-------------------------- | :------------------------------------------------ |
| `POST`   | `/dsit/upload`              | 上传 DeepSITRServer 输出 .zip，自动解析并返回报告 |
| `POST`   | `/dsit/upload-local`        | 从服务器本地路径加载分析结果（开发/测试用）       |
| `GET`    | `/dsit/reports`             | 列出所有已加载的 DSIT 报告                        |
| `GET`    | `/dsit/report/{id}`         | 获取完整报告（JSON，含文件统计 + 诊断明细）       |
| `GET`    | `/dsit/report/{id}/summary` | 获取报告摘要（仅诊断统计）                        |
| `DELETE` | `/dsit/report/{id}`         | 删除报告                                          |

### 数据映射

DeepSITRServer `xplusx.err` → ct8114 前端诊断卡片的字段映射：

| DSIT 字段                   | 前端展示字段           | 说明                                               |
| :-------------------------- | :--------------------- | :------------------------------------------------- |
| `bug.checker`               | 检查器名称（蓝色标题） | 如 `clang-analyzer-gjb.statement.CodeUnreachable`  |
| `bug.rule_id`               | 规则编号（筛选标签）   | 如 `GJB-R-1-8-2`、`MISRA-R-2-1`                    |
| `bug.message`               | 诊断消息               | 含规则编号 + 违规描述                              |
| `bug.location_start.line`   | 行号                   |                                                    |
| `bug.location_start.column` | 列号                   |                                                    |
| `bug.path`                  | 源文件路径             |                                                    |
| `bug.force`                 | 严重级别               | `1` → Error（红色左框），`0` → Warning（黄色左框） |

### 报告 JSON 结构

```json
{
  "report_id": "dsit_20260612_010843_615b18",
  "project_name": "Test2-GJB8114",
  "files_stats": [{
    "file_path": "Test2.cpp",
    "total_lines": 40,
    "function_count": 4,
    "function_max_depth": 1,
    "bug_count": 1,
    "bugs": [{
      "checker": "clang-analyzer-gjb.statement.CodeUnreachableBranch",
      "rule_id": "GJB-R-1-8-2",
      "message": "GJB-R-1-8-2 : Prohibit unreachable branches",
      "file_path": "F:\\Code\\test2\\Test2\\Test2.cpp",
      "line": 7,
      "column": 15,
      "level": "Error",
      "force": "1"
    }]
  }],
  "summary": {
    "total_bugs": 24,
    "total_files": 12,
    "by_level": {"Error": 24},
    "by_rule": {"GJB-R-1-8-1": 6, "MISRA-R-2-1": 6, ...},
    "bugs": [...]
  }
}
```

---

## 测试方法

### 自动化集成测试

```bash
cd ct8114-main/ct8114-main
python test_dsit_integration.py
```

该脚本自动完成：

1. 解析器单元测试（独立验证 `dsit_parser.py`）
2. 启动 ct8114 服务（端口 8000）
3. 调用 `POST /dsit/upload-local` 加载预置测试数据
4. 验证 `GET /dsit/report/{id}`、`GET /dsit/reports` 返回正确
5. 输出浏览器访问链接

前置条件：DeepSITRServer 输出目录存在，默认路径为：

```
E:\北航项目\DeepSITRServer-2026-6-9\DeepSITRServer\Test2
```

可通过环境变量 `DSIT_TEST_DIR` 自定义。

### 手动测试（浏览器）

1. 启动服务：

   ```bash
   cd ct8114-main/ct8114-main
   python -m uvicorn server:app --host 127.0.0.1 --port 8000
   ```

2. 打开浏览器：`http://127.0.0.1:8000/static/index.html`

3. 点击顶部 **「DSIT 报告」** 标签

4. 在「本地路径」输入框中粘贴 DeepSITRServer 输出目录路径，例如：

   ```
   E:\北航项目\DeepSITRServer-2026-6-9\DeepSITRServer\Test2
   ```

5. 点击 **「从本地路径加载」**

6. 在下方报告列表点击报告名称，即可在右侧看到诊断卡片：
   - 汇总统计面板（问题总数 / Error / Warning / 检查项种类）
   - 可点击的规则筛选标签
   - 每条诊断的详细卡片（规则编号 + 文件路径 + 行列号 + 错误描述）
   - 搜索框支持按检查名/消息/文件名过滤

### 测试预期结果

- 加载 Test2 目录 → **12 个文件，24 条诊断**
- 诊断包含 GJB 规则（如 `GJB-R-1-8-1`）和 MISRA 规则（如 `MISRA-R-2-1`）
- 全部 24 条为 Error 级别（红色左边框）
- 9 种检查项种类可供筛选

---

> **总结**: `ct8114` 是一个使用 DeepSITRServer 内置 codetidy.exe 引擎的 GJB 8114 代码合规性 Web 分析服务。v2 版本已完全移除 clang-tidy 依赖，统一使用 codetidy + DSIT 格式，支持即时上传分析、UniPortal 项目分析和预生成报告加载三种工作模式。
