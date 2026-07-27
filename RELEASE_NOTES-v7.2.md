# CT8114 v7.2 Release Notes

## 一、版本定位

CT8114 v7.2 是在 v7.1 多规则集版本基础上的交付增强版。

本版本主要完成以下内容：

- 多规则集分析链路完善
- 每个规则集最近一次结果的独立保存
- 当前规则集历史结果查看
- JSON 原始结果导出
- PDF 打印报告优化
- 项目卡片与分析配置区域的交互优化

## 二、主要更新

### 1. 多规则集支持

v7.2 支持以下规则集：

```text
GJB-8114
GJB-5369
CWE-C
MISRA-2008
MISRA-2012
```

前端分析配置区域提供规则集下拉选择。

默认规则集为：

```text
GJB-8114
```

### 2. 规则集结果保存

v7.2 保留原有最近一次分析结果文件：

```text
last_report.json
meta.json
```

同时新增按规则集保存最近一次分析结果的平铺式输出：

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

保存位置：

```text
/data/uniportal/local-upload/{project_id}/ct8114/
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

### 3. 新增规则集报告查询接口

后端新增规则集报告查询能力：

```http
GET /projects/{project_id}/reports?portal_project_id=local-upload
```

用于查询当前项目下各规则集是否已有历史报告。

后端新增指定规则集报告读取能力：

```http
GET /projects/{project_id}/reports/{rule_set}?portal_project_id=local-upload
```

用于读取指定规则集最近一次完整分析报告。

### 4. 前端交互优化

项目卡片右侧删除原有分析按钮，仅保留：

```text
显示文件
```

分析动作统一放在下方“分析配置”区域。

当前规则集已有历史报告时，显示：

```text
查看结果 / 重新分析
```

当前规则集无历史报告时，显示：

```text
开始分析项目
```

该调整避免了“上方可分析、下方选规则集”的语义冲突。

### 5. JSON 原始结果导出

前端支持导出当前页面显示结果对应的 JSON 原始数据。

JSON 导出基于当前页面持有的报告对象，不重新请求：

```text
last_report.json
```

因此，当前页面显示哪个规则集的结果，就导出哪个规则集的 JSON。

### 6. PDF 打印报告

前端支持打印PDF报告。

PDF 报告基于当前页面显示的分析结果生成，不重新读取磁盘上的最近一次结果。

报告内容包括：

- 报告题头
- 项目信息
- 分析配置摘要
- 分析结果概览
- 文件问题统计
- 规则命中统计
- 缺陷明细

### 7. PDF 缺陷明细优化

PDF 缺陷明细表由原多列结构：

```text
序号 / file_path / line / column / level / checker / rule_id / message
```

调整为：

```text
序号 / 等级 / 规则 / 位置 / 描述
```

其中“位置”字段格式为：

```text
file_path
第 X 行，第 Y 列
```

该调整减少了 A4 打印时字段被强制折行的问题，提高了缺陷明细的可读性。

### 8. PDF 缺陷排序

PDF 缺陷明细按照以下顺序排序：

1. Error
2. Warning
3. Other / Unknown
4. checker
5. file_path
6. line
7. column

该排序使错误级别更高的问题优先展示，并将同类规则命中的缺陷聚合在相近位置。

## 三、验证基线

### GJB-8114 / MEMS

验证结果：

```text
files_stats = 8
bugs = 866
Error = 161
Warning = 705
engine_rule_count = 204
selected_rule_set = GJB-8114
result_rule_sets = GJB-8114
```

### CWE-C / MEMS

验证结果：

```text
files_stats = 8
bugs = 4
Error = 4
engine_rule_count = 130
selected_rule_set = CWE-C
result_rule_sets = CWE-C
```

## 四、兼容性说明

v7.2 保留原有：

```text
last_report.json
meta.json
```

因此不会破坏原有前端读取最近一次报告的行为。

新增的规则集报告文件属于增强输出，不影响原有报告路径。

## 五、交付说明

本仓库仅包含源码、规则配置、前端构建产物和部署文件。

Docker 镜像文件：

```text
ct8114-docker-v7.2.tar
```

不纳入普通 Git 仓库，需要作为单独交付附件提供。

运行时加载镜像：

```cmd
docker load -i ct8114-docker-v7.2.tar
```

启动：

```cmd
docker compose up -d
```

访问：

```text
http://127.0.0.1:8003/static/index.html
```