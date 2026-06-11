# ct8114 — GJB 8114 Clang-Tidy 分析服务

> 基于 **FastAPI** 的 **Clang-Tidy (GJB 8114)** 在线静态代码分析服务  
> 支持即时上传分析和 UniPortal 双源项目分析

---

## 项目概述

`ct8114` 是一个基于 Python **FastAPI** 构建的 Web 服务，它封装了 **clang-tidy** 并加载 **GJB 8114** 自定义检查插件，对 C/C++ 代码进行**军用编码规范**合规检测。它提供两种工作模式：

1. **即时上传分析** — 通过浏览器上传文件，立即返回诊断结果
2. **UniPortal 项目分析** — 接入 UniPortal 平台，对共享卷中的项目进行批量分析

---

## 技术栈

| 组件         | 技术                                                        |
| ------------ | ----------------------------------------------------------- |
| Web 框架     | **FastAPI** (Python)                                        |
| 静态分析引擎 | **Clang-Tidy** + `libclang-tidy-gjb8114.so` 插件            |
| 前端界面     | 纯 **HTML/CSS** 静态页面（浅色蓝主题）                      |
| 依赖解析     | `fixes_parser.py` 解析 clang-tidy 的 YAML 输出              |
| 容器化       | **Docker** (基于 `ghcr.io/gjb8114/clang-tidy-gjb8114` 镜像) |
| 运行环境     | Python 3 + Uvicorn ASGI 服务器                              |

### Python 依赖

```
fastapi
uvicorn[standard]
python-multipart
pyyaml
```

---

## 工作模式

### A. 即时上传分析（保留单机兼容性）

```
POST /analyze
    上传文件: multipart files=<file1>&files=<file2>...
    参数:
      - entry: 指定主入口文件名（可选）
      - keep:  调试用，保留服务端临时文件（可选）
```

**流程**：

1. 生成 UUID，在系统临时目录下建立工作目录
2. 上传文件落盘，调用 `clang-tidy -export-fixes=fixes.yaml`
3. 解析 YAML 中的诊断结果，以 JSON 返回前端
4. 默认清理临时目录（可通过 `?keep=true` 保留）

### B. UniPortal 项目分析（双源接入）

| API                      | 方法     | 说明                           |
| ------------------------ | -------- | ------------------------------ |
| `/projects`              | `GET`    | 列出两个数据源的项目列表       |
| `/projects/{id}/files`   | `GET`    | 列出项目内可分析的源文件       |
| `/projects/{id}/analyze` | `POST`   | 对项目执行一次 clang-tidy 分析 |
| `/projects/{id}`         | `DELETE` | 删除私有卷中的项目             |

**双源数据约定**：

1. 先查 `LOCAL_WORKSPACES_DIR/{project_id}/`（子工具自上传，读写）
2. 再查 `UNIPORTAL_STORAGE_PATH/*/{project_id}/`（UniPortal 共享卷，只读）

---

## API 接口详情

| 端点                     | 方法     | 说明                     |
| ------------------------ | -------- | ------------------------ |
| `/`                      | `GET`    | 根路径重定向到静态首页   |
| `/analyze`               | `POST`   | 即时上传分析（多文件）   |
| `/projects`              | `GET`    | 获取项目列表（双源合并） |
| `/projects/{id}/files`   | `GET`    | 获取项目内源文件列表     |
| `/projects/{id}/analyze` | `POST`   | 对项目执行分析           |
| `/projects/{id}`         | `DELETE` | 删除私有项目             |
| `/healthz`               | `GET`    | 健康检查端点             |

---

## 核心文件说明

### `server.py` — 主服务程序

FastAPI 应用入口，包含全部 REST API 端点。核心逻辑：

- **环境变量配置**：
  - `CLANG_TIDY_BIN` — clang-tidy 可执行文件路径（默认 `clang-tidy`）
  - `GJB_PLUGIN_PATH` — GJB 8114 插件路径（默认 `/usr/local/lib/libclang-tidy-gjb8114.so`）
  - `CLANG_TIDY_CHECKS` — 启用的检查规则（默认 `-*,gjb8114-*`）
  - `UNIPORTAL_STORAGE_PATH` — UniPortal 共享卷路径
  - `LOCAL_WORKSPACES_DIR` — 本地工作区目录
  - `MAX_TOTAL_BYTES` — 上传文件大小上限（默认 5MB）

- **文件过滤**：仅允许 `.c/.h/.cc/.cpp/.cxx/.hpp/.hxx` 后缀
- **安全机制**：文件名防路径穿越、文件大小限制、超时控制

### `fixes_parser.py` — 诊断结果解析器

解析 clang-tidy `-export-fixes` 输出的 YAML 文件，转换为结构化数据。

**核心能力**：

- 加载 YAML 文件并标准化为 Python dataclass（`Diagnostic`、`Replacement`、`Note`、`FixesReport`）
- 将 `FileOffset`（字节偏移）转换为人类可读的 `line:column`
- 附带源文件中的代码片段（snippet），方便前端展示
- 提供诊断汇总统计（按检查项和严重级别分组）

**`_SourceIndex` 类** — 按字节偏移 -> 行号的 O(log n) 映射，支持中文等多字节字符

### `static/index.html` — 前端页面

纯静态 HTML 页面，提供完整的 Web 交互界面，功能包括：

- 文件拖拽/选择上传
- 项目列表浏览（双源数据展示）
- 分析结果展示（诊断卡片列表）
- 按严重级别（Error/Warning/Note）过滤
- 搜索诊断信息
- 汇总统计展示

采用浅色蓝主题 UI，与 Vue3 + Tailwind 前端色板保持一致。

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

| 文件 | 说明 |
| :--- | :--- |
| `dsit_parser.py` | DeepSITRServer 输出解析器，支持 `.xplusx.err` JSON / `.sta` / `.rst` |
| `routers_dsit.py` | DSIT API 路由（6 个端点：上传/列表/报告/摘要/删除） |
| `test_dsit_integration.py` | 自测脚本（全自动启动→加载→验证→输出） |

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

| 项目               | 关系                                                                                       |
| ------------------ | ------------------------------------------------------------------------------------------ |
| **DeepSITRServer** | 同为 GJB 8114 静态分析工具。本服务现已集成 DeepSITRServer 输出解析能力（见下方 DSIT 集成） |
| **UniPortal**      | ct8114 以子工具形式接入 UniPortal 平台，作为其代码分析流水线的一环                         |

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

> **总结**: `ct8114` 是一个轻量级、容器化的 GJB 8114 代码合规性 Web 分析服务，通过 clang-tidy 插件实现军用编码标准的自动化检测，支持单次上传和平台化项目分析两种方式。现已集成 DeepSITRServer 桌面工具的分析结果在线展示能力。
