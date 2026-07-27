# CT8114 / DCAB 静态分析工具 v7.2

本仓库用于 CT8114 / DCAB 静态分析工具 v7.2 的源码、前端代码、前端静态构建产物、测试文件和部署说明管理。

## 重要说明

本仓库 **不包含完整 DeepSITRServer / DCAB 运行环境**。

完整运行所需的底层分析引擎、二进制文件、运行库和完整规则目录已经封装在单独提供的 Docker 镜像中：

```text
ct8114-docker-v7.2.tar
```

因此，仅克隆本仓库不能直接完成完整工具运行。运行前需要先加载单独提供的 Docker 镜像包。

本仓库主要用于：

- 源码统一管理
- 前端与后端 wrapper 代码维护
- 部署配置与使用说明维护
- AtomGit 自动拉取与归档
- 后续版本迭代管理

## 一、版本信息

当前版本：

```text
v7.2
```

默认镜像名称：

```text
ct8114:v7.2
```

默认访问地址：

```text
http://127.0.0.1:8003/static/index.html
```

## 二、主要功能

v7.2 版本主要支持以下功能：

1. 多规则集选择分析

支持以下规则集：

- GJB-8114
- GJB-5369
- CWE-C
- MISRA-2008
- MISRA-2012

2. 项目库管理

支持上传 ZIP 工程包，保存到项目库，并对项目库中的工程发起静态分析。

3. 规则集历史结果查看

每个规则集保留最近一次分析结果。当前规则集已有历史报告时，前端显示：

```text
查看结果 / 重新分析
```

当前规则集无历史报告时，前端显示：

```text
开始分析项目
```

4. 源码查看与缺陷跳转

支持查看项目源码文件，并从缺陷列表跳转到对应源码位置。

5. 报告导出

支持：

- 导出 JSON 原始结果
- 打印PDF报告

PDF 和 JSON 均基于当前页面正在显示的分析结果生成。

## 三、仓库内容

仓库主要结构如下：

```text
.
├── server.py
├── dcab_client.py
├── dsit_parser.py
├── source_routes.py
├── source_utils.py
├── routers_dsit.py
├── fixes_parser.py
├── requirements.txt
├── start.sh
├── docker-compose.yml
├── OUTPUT_SPEC.md
├── 静态分析工具用户手册.md
├── frontend/
│   └── src/
├── static/
│   └── assets/
├── tests/
├── scripts/
├── VERSION
└── RELEASE_NOTES-v7.2.md
```

说明：

```text
frontend/
```

为前端源码。

```text
static/
```

为当前版本已构建完成的前端静态资源，最终镜像中直接使用该目录。

```text
tests/
```

为规则集加载、报告路径、结果保存等相关测试。

本仓库不包含：

```text
DeepSITRServer/
ct8114-docker-v7.2.tar
```

## 四、直接运行方式

运行前需要先获取单独提供的 Docker 镜像文件：

```text
ct8114-docker-v7.2.tar
```

加载镜像：

```cmd
docker load -i ct8114-docker-v7.2.tar
```

创建项目库共享卷：

```cmd
docker volume create uniportal_storage
```

启动服务：

```cmd
docker compose up -d
```

查看容器状态：

```cmd
docker ps
```

访问页面：

```text
http://127.0.0.1:8003/static/index.html
```

停止服务：

```cmd
docker compose down
```

## 五、Docker Compose

`docker-compose.yml` 默认使用镜像：

```text
ct8114:v7.2
```

默认端口映射：

```text
8003:8000
```

项目库数据位于容器内：

```text
/data/uniportal
```

## 六、分析结果输出

项目分析结果保存于：

```text
/data/uniportal/local-upload/{project_id}/ct8114/
```

其中：

```text
last_report.json
meta.json
```

表示该项目最近一次分析结果及元信息。

同时，v7.2 会按规则集保留该规则集的最近一次结果：

```text
last_report_GJB-8114.json
meta_GJB-8114.json

last_report_GJB-5369.json
meta_GJB-5369.json

last_report_CWE-C.json
meta_CWE-C.json

last_report_MISRA-2008.json
meta_MISRA-2008.json

last_report_MISRA-2012.json
meta_MISRA-2012.json
```

语义说明：

```text
last_report.json / meta.json
```

表示最近一次分析结果。

```text
last_report_{RuleSet}.json / meta_{RuleSet}.json
```

表示对应规则集的最近一次分析结果。

## 七、前端构建

如需重新构建前端：

```cmd
cd frontend
npm install
npm run build
```

构建完成后，将：

```text
frontend/dist
```

同步到仓库根目录：

```text
static
```

当前仓库已经包含 v7.2 对应的 `static` 构建产物。

## 八、运行边界说明

本仓库不是完整可构建镜像仓库。

若仅拉取本仓库并直接执行：

```cmd
docker compose up -d
```

在本机没有 `ct8114:v7.2` 镜像的情况下，服务无法启动。

完整运行流程必须包括：

```cmd
docker load -i ct8114-docker-v7.2.tar
docker volume create uniportal_storage
docker compose up -d
```

## 九、交付说明

本仓库用于源码维护和 AtomGit 自动同步。

完整运行镜像文件：

```text
ct8114-docker-v7.2.tar
```

请通过交付附件、网盘、Release 附件或指定共享目录单独获取。