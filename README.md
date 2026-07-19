# ct8114 — GJB 8114 在线静态分析服务

基于 **DeepSITRServer / codetidy** 引擎的 GJB 8114 代码静态分析平台，提供 Web 界面与 REST API，支持 Docker 容器化部署。

---

## 版本历史

| 版本     | 日期       | 说明                                                             |
| -------- | ---------- | ---------------------------------------------------------------- |
| **v6.3** | 2026-07-19 | 修复 DCAB 路径 force 值推导：根据 GJB-R/A 规则区分 Error/Warning |
| v6.2     | 2026-07-19 | 修复 codetidy note 行误判为 Warning 的问题                       |
| v6.1     | 2026-07-18 | 写回路径改为 item_root/ct8114/，修复中文乱码                     |
| v5.1     | 2026-07-11 | 新 DCAB 集成：函数列表定位、Required/Advisory 强制级别           |
| v5.0     | 2026-07    | 初始 DCAB 集成，codetidy 引擎替代 clang-tidy                     |
| v4.x     | 2025       | 基于 clang-tidy + GJB 插件方案                                   |

---

## v6.3 修复

- **DCAB force 值推导**：DCAB 服务端未正确返回 `force` 字段时，根据规则 ID 推导强制级别：
  - `GJB-R-*` / `MISRA*:R-*` → force=1 → Error（Required 强制规则）
  - `GJB-A-*` / 其他 → force=0 → Warning（Advisory 推荐规则）

## v6.2 修复

- **codetidy note 行过滤**：codetidy 输出的 `note` 级别行（clang-tidy 上下文补充）不再被当作 Warning 诊断。修复前 9 行输出中有 4 条 note 噪音，修复后仅保留 5 条真实违规。

## v6.1 新特性

### 函数列表与定位

每条分析报告新增 `functions` 字段，记录源文件中所有函数的名称及起止行列号：

```json
{
  "functions": [
    {
      "name": "check_range",
      "start_line": 42,
      "start_column": 5,
      "end_line": 58,
      "end_column": 1
    }
  ]
}
```

前端支持按函数折叠/展开，点击函数名可快速定位到对应代码位置。

### Required / Advisory 强制级别

DCAB 新版规则区分两种强制级别：

| force 值 | 级别                     | 前端显示    | 含义                         |
| -------- | ------------------------ | ----------- | ---------------------------- |
| `1`      | **Required**（强制规则） | `Error` ¹   | 必须有逻辑错误，一般要求改正 |
| `0`      | **Advisory**（推荐规则） | `Warning` ² | 潜在问题，不强制修复         |

### 支持的规则标准

- **GJB 8114** — 军用软件 C 语言安全规范
- **MISRA C:2023** — 汽车电子 C 语言标准
- **CWE** — 通用弱点枚举
- **CERT C** — 卡内基梅隆安全编码标准
- **AUTOSAR C++14** — 汽车开放系统架构 C++ 标准
- **JSF AV C++** — 联合攻击战斗机 C++ 编码标准
- **FKFG** — 附加规则集

---

## 项目结构

```
ct8114-docker-v6.1/
├── _app_code/
│   ├── app/
│   │   ├── server.py            # FastAPI 主服务
│   │   ├── dsit_parser.py       # 数据模型 & DSIT 输出解析
│   │   ├── dcab_client.py       # DCAB HTTP 客户端
│   │   ├── routers_dsit.py      # DSIT 报告加载路由
│   │   ├── static/
│   │   │   ├── index.html       # Vue.js 前端 SPA
│   │   │   └── ct8114-enhance.js # 前端增强脚本（函数面板/级别标注）
│   │   ├── tests/               # 测试用例
│   │   └── start.sh             # 容器启动脚本
│   ├── opt/
│   │   └── dcab/                # DeepSITRServer + codetidy 二进制 & 规则配置
│   ├── tmp/                     # 临时文件
│   └── var/                     # 持久化数据
├── blobs/                       # Docker 镜像层
├── manifest.json                # Docker 镜像清单
└── index.json                   # OCI 索引
```

---

## 快速开始

### 环境要求

- Python 3.10+
- FastAPI + uvicorn
- DeepSITRServer 运行时（`_app_code/opt/dcab/`）

### 本地开发运行

```bash
cd _app_code/app

# 安装依赖
pip install fastapi uvicorn python-multipart

# 启动服务（默认端口 8000）
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

### Mock 模式（无需 DCAB 运行时）

用于前端开发与功能测试：

```bash
cd _app_code/app
python run_mock.py
```

Mock 模式使用内置模拟数据，可验证完整前后端流程。

### Docker 部署

```bash
# 导入镜像
docker load -i ct8114-docker-v6.1.tar

# 运行容器
docker run -d -p 8000:8000 ct8114:v6.1
```

---

## API 接口

### 即时上传分析

```
POST /analyze?entry=main.c&keep=false
Content-Type: multipart/form-data

files: <源文件1>, <源文件2>, ...
```

上传源文件即时分析，返回 DSIT 兼容 JSON 报告。

### 项目管理

| 方法     | 路径                     | 说明           |
| -------- | ------------------------ | -------------- |
| `GET`    | `/projects`              | 列出可用项目   |
| `GET`    | `/projects/{id}/files`   | 列出项目源文件 |
| `POST`   | `/projects/{id}/analyze` | 对项目运行分析 |
| `DELETE` | `/projects/{id}`         | 删除私有项目   |

### DSIT 报告加载

| 方法   | 路径                 | 说明                       |
| ------ | -------------------- | -------------------------- |
| `POST` | `/dsit/upload-local` | 加载预生成的 DSIT 输出目录 |
| `GET`  | `/dsit/reports`      | 列出已加载报告             |
| `GET`  | `/dsit/report/{id}`  | 获取报告详情               |

---

## 报告 JSON 结构

```json
{
  "report_id": "uuid",
  "created_at": "2026-07-11T10:00:00",
  "summary": {
    "total_files": 3,
    "total_bugs": 12,
    "total_functions": 45
  },
  "files_stats": [
    {
      "file_path": "src/main.c",
      "total_lines": 200,
      "total_statements": 150,
      "functions": [
        {
          "name": "main",
          "start_line": 10,
          "start_column": 1,
          "end_line": 50,
          "end_column": 1
        }
      ],
      "bugs": [
        {
          "checker": "codetidy-gjb.statement.XXX",
          "file_path": "src/main.c",
          "line": 25,
          "column": 10,
          "message": "[GJB-R-1-8-2] 存在不可达分支",
          "rule_id": "GJB-R-1-8-2",
          "force": "1",
          "type_code": "2",
          "level": "Error"
        }
      ]
    }
  ]
}
```

### 关键字段说明

| 字段        | 类型                    | 说明                                         |
| ----------- | ----------------------- | -------------------------------------------- |
| `force`     | `"1"` / `"0"`           | DCAB 强制级别：1=Required，0=Advisory        |
| `level`     | `"Error"` / `"Warning"` | 前端兼容级别：force=1→Error，force=0→Warning |
| `type_code` | `"1"` / `"2"`           | DCAB 原始类型：1=error，2=warning            |
| `functions` | Array                   | 文件内函数列表（名称 + 行列定位）            |

---

## 技术栈

- **后端**: Python 3.10+ / FastAPI / uvicorn
- **前端**: Vue.js 3 / 原生 JavaScript 增强
- **分析引擎**: DeepSITRServer + codetidy (C/C++)
- **容器化**: Docker / OCI 格式

---

## License

内部项目，仅供授权使用。
