# 云原生安全 · 数据库审计日志 Serverless 采集方案
[English](README.md)

![License: MIT No Attribution](https://img.shields.io/badge/License-MIT_No_Attribution-blue.svg)
![Huawei Cloud](https://img.shields.io/badge/Cloud-Huawei_Cloud-red.svg)
![Terraform](https://img.shields.io/badge/IaC-Terraform-purple.svg)

## 简介

本方案在华为云上搭建 **RDS MySQL / GaussDB MySQL 审计日志的 Serverless 自动采集与查询系统**。系统自动发现已开启审计日志的数据库实例，定时拉取审计日志并归档到 OBS，通过 DLI（数据湖探索）提供 SQL 查询能力——全程无服务器管理，按需付费。

适用于需要数据库审计合规、操作追溯、安全分析的场景。

## 目录

- [架构图](#架构图)
- [方案亮点](#方案亮点)
- [涉及云服务](#涉及云服务)
- [前置条件](#前置条件)
- [快速开始](#快速开始)
- [分步部署](#分步部署)
- [使用方法](#使用方法)
- [清理资源](#清理资源)
- [详细说明](#详细说明)
- [FAQ](#faq)
- [许可证](#许可证)
- [联系方式](#联系方式)

## 架构图

```
                    ┌─────────────────────┐
                    │  FunctionGraph      │
  TIMER (每日02:00) │  Discovery 函数     │
                    │  自动发现审计实例    │
                    └────────┬────────────┘
                             │ 更新 cluster_config.json
                             ▼
                    ┌─────────────────────┐
                    │  OBS 桶             │
                    │  cluster_config.json│
                    └────────┬────────────┘
                             │
                    ┌────────▼────────────┐
                    │  FunctionGraph      │
  TIMER (每5分钟)   │  Dispatcher 函数    │
                    │  读取配置 → 扇出    │
                    └────────┬────────────┘
                             │ 异步调用 × N
                    ┌────────▼────────────┐
                    │  FunctionGraph      │
                    │  Worker 函数 × N    │
                    │  拉取审计日志       │
                    └────────┬────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
     ┌────────────┐  ┌────────────┐  ┌────────────┐
     │  RDS API   │  │  OBS 归档  │  │  GeminiDB  │
     │  审计日志   │  │  审计日志  │  │  采集状态  │
     └────────────┘  └────────────┘  └────────────┘
                             │
                    ┌────────▼────────────┐
                    │  DLI 数据湖探索     │
                    │  SQL 查询审计日志   │
                    └─────────────────────┘
```

## 方案亮点

- **全 Serverless 架构**：无需管理服务器，FunctionGraph 按调用次数弹性伸缩
- **自动发现**：Discovery 函数每日扫描，自动识别新开启审计的数据库实例
- **断点续传**：GeminiDB 记账采集状态，Worker 每次从上次断点继续
- **弹性扇出**：Dispatcher 并发调用 Worker，支持多实例同时采集
- **SQL 查询**：DLI 直接对 OBS 上的审计日志做 SQL 查询，无需额外 ETL
- **gzip 压缩**：审计日志压缩后归档，减少存储占用

## 涉及云服务

| 服务 | 用途 |
|------|------|
| FunctionGraph | Serverless 函数运行时（Worker / Dispatcher / Discovery） |
| OBS | 审计日志归档存储 + 集群配置文件 + 函数代码包 |
| GeminiDB | DynamoDB 兼容接口，存储采集状态（断点续传） |
| RDS | 被审计的 MySQL 实例（用户已有，非本方案创建） |
| DLI | 数据湖探索，对 OBS 审计日志做 SQL 查询 |
| IAM | 委托授权 FunctionGraph 访问 OBS / GeminiDB / RDS |
| VPC | GeminiDB 实例所需网络 |

清理资源：执行 `terraform destroy` 即可释放全部本方案创建的资源。

## 前置条件

- 华为云账号，已开通 FunctionGraph / OBS / GeminiDB / DLI / RDS 服务
- 已安装 Terraform >= 1.5
- 已安装 hcloud CLI（FunctionGraph 运行时内置，本地调试需自行安装）
- 已配置访问密钥：`export HW_ACCESS_KEY="..."` 和 `export HW_SECRET_KEY="..."`
- 已知华为云账号域名（IAM 委托归属域）
- 目标 RDS MySQL 实例已开启审计日志参数

## 快速开始

```bash
# 1. 克隆仓库
git clone <repo-url>
cd cloud-native-security-db-audit-log-serverless

# 2. 配置凭据
export HW_ACCESS_KEY="your-access-key"
export HW_SECRET_KEY="your-secret-key"

# 3. 修改 infra/main.tf 中的 domain_name 变量
#    将 "your-huaweicloud-domain" 替换为你的华为云账号域名

# 4. 部署
cd infra
terraform init
terraform plan
terraform apply

# 5. 创建 DLI 表（在 DLI 控制台执行 scripts/create_dli_tables.sql）
#    将 SQL 中的 <OBS_BUCKET_NAME> 替换为 terraform output obs_audit_bucket 的值
```

## 分步部署

### 步骤 1：初始化 Terraform

```bash
cd infra
terraform init
```

预期输出：`Terraform has been successfully initialized!`

### 步骤 2：查看部署计划

```bash
terraform plan
```

预期输出：列出将创建的资源（OBS 桶 × 2、GeminiDB 实例、3 个 FunctionGraph 函数、2 个 TIMER 触发器、IAM 委托、VPC 等）。

### 步骤 3：执行部署

```bash
terraform apply
```

输入 `yes` 确认。部署完成后输出 OBS 桶名、GeminiDB 端点、函数名等。

### 步骤 4：上传函数代码包

```bash
# 打包 Worker 函数
cd src/functions
zip worker.zip worker_lambda.py cluster_config.py
zip dispatcher.zip dispatcher_lambda.py cluster_config.py
zip discovery.zip discovery_lambda.py cluster_config.py

# 上传到 OBS 代码桶（桶名 = terraform output obs_audit_bucket + "-code"）
obsutil cp worker.zip obs://<code-bucket>/function-code/worker.zip
obsutil cp dispatcher.zip obs://<code-bucket>/function-code/dispatcher.zip
obsutil cp discovery.zip obs://<code-bucket>/function-code/discovery.zip
```

### 步骤 5：创建 DLI 查询表

在 DLI 控制台 SQL 编辑器中执行 `scripts/create_dli_tables.sql`，将其中的 `<OBS_BUCKET_NAME>` 替换为步骤 3 输出的桶名。

## 使用方法

### 验证 Discovery 函数

在 FunctionGraph 控制台手动触发 `db-audit-log-serverless-discovery` 函数，查看日志输出：

```json
{"discovered": 2, "manual": 0, "total": 2}
```

表示发现了 2 个已开启审计的 RDS 实例。

### 验证 Dispatcher + Worker

在 FunctionGraph 控制台手动触发 `db-audit-log-serverless-dispatcher` 函数：

```json
{"total_clusters": 2, "invoked": 2, "failed": 0}
```

表示成功扇出调用 2 个 Worker。

### 查询审计日志

在 DLI 控制台执行 SQL：

```sql
-- 查看最近采集的审计日志
SELECT * FROM audit_log_db.audit_logs
ORDER BY `timestamp` DESC
LIMIT 10;

-- 查找特定用户的 DELETE 操作
SELECT * FROM audit_log_db.audit_logs
WHERE operation = 'DELETE' AND username = 'admin'
ORDER BY `timestamp` DESC;
```

## 清理资源

```bash
cd infra
terraform destroy
```

输入 `yes` 确认。这将删除所有本方案创建的资源（OBS 桶、GeminiDB、FunctionGraph 函数、触发器、IAM 委托、VPC）。

## 详细说明

### 工作流程

1. **Discovery**（每日 02:00）：扫描当前区域 RDS MySQL 实例，检查审计日志参数是否开启，将结果写入 OBS 上的 `cluster_config.json`
2. **Dispatcher**（每 5 分钟）：读取 `cluster_config.json`，对每个启用的实例异步调用 Worker
3. **Worker**（被 Dispatcher 调用）：从 RDS API 拉取审计日志，gzip 压缩后上传 OBS，在 GeminiDB 更新采集断点
4. **查询**：用户在 DLI 控制台对 OBS 上的审计日志执行 SQL 查询

### 配置项

| 配置 | 位置 | 默认值 | 说明 |
|------|------|--------|------|
| `dispatcher_schedule` | infra/main.tf | `0 */5 * * * ?` | Dispatcher 触发频率 |
| `discovery_schedule` | infra/main.tf | `0 0 2 * * ?` | Discovery 触发时间 |
| `enable_gzip` | infra/main.tf | `true` | 审计日志压缩 |
| `worker_memory` | infra/main.tf | `512` | Worker 内存 (MB) |
| `worker_timeout` | infra/main.tf | `300` | Worker 超时 (秒) |

### 集群配置格式

`cluster_config.json` 存储在 OBS 桶根目录，格式如下：

```json
{
  "clusters": [
    {
      "cluster_id": "rds-instance-id",
      "cluster_name": "my-mysql-db",
      "engine": "mysql",
      "region": "cn-north-4",
      "enabled": true,
      "auto_discovered": true
    }
  ]
}
```

可手动添加非自动发现的实例（设 `auto_discovered: false`），Discovery 不会覆盖手动条目。

## FAQ

**Q: 为什么 GeminiDB 而不用 RDS 存储状态？**
A: GeminiDB 提供 DynamoDB 兼容接口，用于状态存储，无需改写数据访问逻辑。

**Q: 为什么用 DLI 而不用 GaussDB(DWS)？**
A: DLI 是 Serverless SQL 查询引擎，直接对 OBS 上的文件做查询，无需维护数据库实例。

**Q: 如何添加手动管理的实例？**
A: 在 OBS 上的 `cluster_config.json` 中添加条目，设 `auto_discovered: false`，Discovery 函数不会覆盖它。

## 依赖

- Terraform >= 1.5
- huaweicloud Provider ~> 1.70
- Python 3.9（FunctionGraph 运行时）
- esdk-obs-python（OBS SDK，运行时内置）
- boto3（GeminiDB DynamoDB 兼容接口）

## 贡献指南

参见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

本实践采用 MIT No Attribution 许可证。详见 [LICENSE](LICENSE)。

## 联系方式

- 维护者: `<github-username>`
- 交流渠道: [GitHub Issues](https://github.com/<github-username>/cloud-native-security-db-audit-log-serverless/issues)
