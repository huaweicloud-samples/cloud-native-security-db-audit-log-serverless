# 控制台部署指南

> 适用于不便使用 Terraform 的场景，通过华为云控制台手动部署本方案。

## 1. 创建 OBS 桶

1. 登录华为云控制台 → 对象存储服务 OBS
2. 创建两个桶：
   - `db-audit-log-serverless-archive`（审计日志归档，开启版本管理）
   - `db-audit-log-serverless-archive-code`（函数代码包）
3. 桶 ACL 设为「私有」

## 2. 创建 GeminiDB 实例

1. 控制台 → GeminiDB
2. 创建实例：
   - 引擎：GeminiDB（DynamoDB 兼容协议）
   - 模式：集群
   - 可用区：3 AZ（如 cn-north-4a/b/c）
   - 规格：geminidb-nosql.xlarge.arm × 3 节点
   - 存储：100GB ultra
3. 记录连接地址（lb_ip_address），后续函数环境变量需要

## 3. 创建 IAM 委托

1. 控制台 → 统一身份认证 IAM → 委托
2. 创建委托：
   - 名称：`db-audit-log-serverless-fgs-agency`
   - 委托类型：普通账号
   - 持续时间：永久
   - 授权角色：OBS OperateAccess、GeminiDB FullAccess、RDS FullAccess

## 4. 创建 FunctionGraph 函数

### 4.1 打包并上传代码

```bash
cd src/functions
zip worker.zip worker_lambda.py cluster_config.py
zip dispatcher.zip dispatcher_lambda.py cluster_config.py
zip discovery.zip discovery_lambda.py cluster_config.py

obsutil cp worker.zip obs://db-audit-log-serverless-archive-code/function-code/worker.zip
obsutil cp dispatcher.zip obs://db-audit-log-serverless-archive-code/function-code/dispatcher.zip
obsutil cp discovery.zip obs://db-audit-log-serverless-archive-code/function-code/discovery.zip
```

### 4.2 创建 Worker 函数

1. 控制台 → 函数工作流 FunctionGraph
2. 创建函数：
   - 名称：`db-audit-log-serverless-worker`
   - 运行时：Python3.9
   - handler：`worker_lambda.handler`
   - 内存：512MB，超时：300s
   - 代码类型：OBS，URL 指向 worker.zip
   - 委托：选择上一步创建的委托
3. 配置环境变量：
   - `OBS_BUCKET` = `db-audit-log-serverless-archive`
   - `GEMINIDB_ENDPOINT` = GeminiDB 连接地址
   - `REGION` = `cn-north-4`
   - `ENABLE_GZIP` = `true`
   - `STATE_TABLE_NAME` = `AuditLogState`

### 4.3 创建 Dispatcher 函数

同上，参数：
- 名称：`db-audit-log-serverless-dispatcher`
- handler：`dispatcher_lambda.handler`
- 内存：256MB，超时：60s
- 环境变量增加：`WORKER_FUNCTION` = Worker 函数 URN

### 4.4 创建 Discovery 函数

同上，参数：
- 名称：`db-audit-log-serverless-discovery`
- handler：`discovery_lambda.handler`
- 内存：256MB，超时：120s

## 5. 创建定时触发器

1. 在 Dispatcher 函数详情页 → 触发器 → 创建触发器
   - 类型：TIMER
   - 名称：`dispatcher-schedule`
   - 调度类型：CRON
   - 表达式：`0 */5 * * * ?`（每 5 分钟）

2. 在 Discovery 函数详情页 → 触发器 → 创建触发器
   - 类型：TIMER
   - 名称：`discovery-schedule`
   - 调度类型：CRON
   - 表达式：`0 0 2 * * ?`（每日 02:00）

## 6. 创建 DLI 查询表

1. 控制台 → 数据湖探索 DLI → SQL 编辑器
2. 执行 `scripts/create_dli_tables.sql` 中的 SQL
3. 将 SQL 中的 `<OBS_BUCKET_NAME>` 替换为 `db-audit-log-serverless-archive`

## 7. 验证

1. 手动触发 Discovery 函数，检查日志输出发现到的实例数量
2. 手动触发 Dispatcher 函数，检查 Worker 调用结果
3. 在 DLI SQL 编辑器执行查询验证审计日志可查
