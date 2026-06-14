# ct8114 — GJB 8114 代码分析服务

基于 **FastAPI** + **codetidy.exe (DeepSITRServer)** 的在线静态代码分析服务，对 C/C++ 代码进行 **GJB 8114** 军用编码规范合规检测。

---

## 快速开始

### 1. 配置 codetidy 引擎

```powershell
# 推荐：设置 DeepSITRServer 安装目录
$env:DEEPSITR_ROOT="E:\path\to\DeepSITRServer"

# 或直接指定 exe 路径
$env:CODETIDY_BIN="E:\path\to\DeepSITRServer\core\codetidy.exe"
```

> 搜索优先级: `DEEPSITR_ROOT` → `CODETIDY_BIN` → `./DeepSITRServer/core/codetidy.exe` → 递归搜索

### 2. 启动服务

```bash
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

访问 `http://localhost:8000`

### 3. 模拟共享卷（本地测试）

```powershell
$env:MOCK_UNIPORTAL_DIR="mock_uniportal"
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

---

## 异步分析架构

```
POST /analyze ──→ FastAPI ──→ codetidy.exe (后台线程)
     │                 │
     └── {request_id}  └── _TASK_STORE {pending→running→completed/failed}
                            ↑
GET /status/{request_id} ───┘  (前端轮询 2s 间隔)
```

- 任务 TTL: 3600s（过期自动清理）
- 轮询间隔: 2s，超时: 300s

---

## API 概览

| 端点                     | 方法   | 说明                             |
| ------------------------ | ------ | -------------------------------- |
| `/analyze`               | POST   | 上传文件并提交异步分析           |
| `/status/{request_id}`   | GET    | 轮询任务状态与结果               |
| `/status`                | GET    | 列出所有活跃任务（调试）         |
| `/projects`              | GET    | 获取项目列表（UniPortal + 本地） |
| `/projects/{id}/analyze` | POST   | 对项目执行异步分析               |
| `/projects/{id}`         | DELETE | 删除项目                         |
| `/dsit/*`                | 多种   | 加载/查看/删除 DSIT 报告         |
| `/healthz`               | GET    | 健康检查                         |

---

## 环境变量

| 变量                     | 默认值                | 说明                              |
| ------------------------ | --------------------- | --------------------------------- |
| `DEEPSITR_ROOT`          | —                     | DeepSITRServer 安装根目录（推荐） |
| `CODETIDY_BIN`           | —                     | codetidy.exe 完整路径             |
| `CODETIDY_CHECKS`        | `clang-analyzer-gjb*` | 检查规则                          |
| `CODETIDY_TIMEOUT`       | `300`                 | 分析超时（秒）                    |
| `UNIPORTAL_STORAGE_PATH` | —                     | UniPortal 共享卷路径              |
| `UNIPORTAL_WRITABLE`     | `true`                | 共享卷是否可写                    |
| `MOCK_UNIPORTAL_DIR`     | —                     | 本地模拟共享卷路径                |
| `MOCK_ANALYSIS`          | —                     | `true` 时跳过 codetidy（测试用）  |
| `TASK_TTL_SECONDS`       | `3600`                | 异步任务过期时间（秒）            |
| `MAX_TOTAL_BYTES`        | `5MB`                 | 上传文件大小限制                  |

---

## 项目结构

```
ct8114-DeepSITRServer/
├── server.py              # FastAPI 主服务
├── dsit_parser.py         # codetidy 调用封装 + 输出解析
├── routers_dsit.py        # DSIT 报告管理路由
├── fixes_parser.py        # 修复建议解析器（兼容保留）
├── requirements.txt       # Python 依赖
├── .env.example           # 环境变量模板
├── dockerfile             # Docker 构建
├── docker-compose.yml     # Docker Compose 编排
├── docker-compose.override.yml  # 本地开发覆盖
├── scripts/               # 构建/运行辅助脚本
│   ├── build.sh
│   └── run.sh
├── frontend/              # Vue 3 + Vite 前端源码
│   ├── src/
│   │   ├── App.vue
│   │   ├── components/
│   │   ├── api/
│   │   └── utils/
│   ├── vite.config.ts
│   └── package.json
├── static/                # 构建输出（纯 HTML 前端）
├── mock_uniportal/        # 本地模拟共享卷
├── workspaces/            # 项目工作空间 + 报告存储
└── OUTPUT_SPEC.md         # 输出规范文档
```

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

### 异步轮询测试

```bash
cd ct8114-DeepSITRServer/ct8114-DeepSITRServer
python test_async_polling.py
```

该脚本自动完成：

1. 上传 C 源码 → 验证立即返回 `{request_id, status:"pending"}`
2. 轮询 `GET /status/{request_id}` → 验证 `completed` 状态及完整报告
3. 项目分析 → 同上流程
4. 验证 404 未知任务

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

> **总结**: `ct8114` 是一个使用 DeepSITRServer 内置 codetidy.exe 引擎的 GJB 8114 代码合规性 Web 分析服务。v2.1 版本引入**异步分析 + 前端轮询**架构，将耗时的 codetidy 执行与 HTTP 请求解耦，支持即时上传分析、UniPortal 项目分析、预生成报告加载和任务状态轮询四种工作模式。
