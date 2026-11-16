# 架构详解

## 整体架构

本方案采用 **定时调度的 Serverless 数据管道** 架构，由三个 FunctionGraph 函数协作完成 RDS MySQL 审计日志的自动采集、归档与查询。

```
┌─────────────────────────────────────────────────────────────────┐
│                        华为云                                    │
│                                                                  │
│  ┌──────────────┐    TIMER (每日 02:00)                         │
│  │  FunctionGraph│    ┌───────────────┐                        │
│  │  Discovery    │◄───│  TIMER 触发器  │                        │
│  │  函数         │    └───────────────┘                        │
│  └──────┬───────┘                                              │
│         │ 1. 扫描 RDS MySQL 实例                                │
│         │ 2. 检查审计日志参数                                    │
│         │ 3. 更新 cluster_config.json                           │
│         ▼                                                       │
│  ┌──────────────┐                                              │
│  │  OBS 桶      │  cluster_config.json                          │
│  │  (配置+归档) │  audit-logs/<instance>/<date>/<log>           │
│  └──────┬───────┘                                              │
│         │ 读取配置                                               │
│  ┌──────▼───────┐    TIMER (每 5 分钟)                         │
│  │  FunctionGraph│    ┌───────────────┐                        │
│  │  Dispatcher   │◄───│  TIMER 触发器  │                        │
│  │  函数         │    └───────────────┘                        │
│  └──────┬───────┘                                              │
│         │ 异步调用 × N                                          │
│  ┌──────▼───────┐                                              │
│  │  FunctionGraph│  ┌──────────┐  ┌──────────┐  ┌──────────┐ │
│  │  Worker × N   │─►│  RDS API │  │  OBS     │  │ GeminiDB │ │
│  │  函数         │  │  审计日志 │  │  归档    │  │  状态    │ │
│  └──────────────┘  └──────────┘  └──────────┘  └──────────┘ │
│                                                                  │
│  ┌──────────────┐                                              │
│  │  DLI         │  SQL 查询 OBS 上的审计日志                    │
│  │  数据湖探索  │  audit_log_db.audit_logs                      │
│  └──────────────┘                                              │
└─────────────────────────────────────────────────────────────────┘
```

## 数据流

### 1. Discovery 流程（每日一次）

```
TIMER 触发 → Discovery 函数启动
  → hcloud RDS ListInstances（列出所有 MySQL 实例）
  → 逐实例 hcloud RDS ShowInstanceConfiguration（检查审计参数）
  → 筛选出审计已开启的实例
  → 合并到现有 cluster_config.json（保留手动添加的条目）
  → ObsClient putContent（写回 OBS）
```

### 2. Dispatcher 流程（每 5 分钟）

```
TIMER 触发 → Dispatcher 函数启动
  → ObsClient getObject（读取 cluster_config.json）
  → 筛选 enabled=true 的集群
  → 逐集群 hcloud FGS InvokeFunction（异步调用 Worker）
  → 返回调用统计
```

### 3. Worker 流程（被 Dispatcher 调用）

```
Dispatcher 调用 → Worker 函数启动（event 含 cluster_id）
  → GeminiDB get_item（查询该实例上次采集断点）
  → hcloud RDS ListAuditLogs（拉取审计日志列表）
  → 过滤已处理的日志（按 begin_time > last_timestamp）
  → 逐日志：
      → hcloud RDS GetAuditLogDownloadLink（获取下载链接）
      → requests.get（下载日志内容）
      → gzip.compress（可选压缩）
      → ObsClient putContent（上传到 OBS）
  → GeminiDB put_item（更新采集断点）
  → 返回采集统计
```

### 4. 查询流程（用户手动）

```
用户 → DLI 控制台 SQL 编辑器
  → SELECT * FROM audit_log_db.audit_logs WHERE ...
  → DLI 读取 OBS 上的审计日志文件
  → 返回查询结果
```

## 组件职责

| 组件 | 职责 | 触发方式 | 频率 |
|------|------|---------|------|
| Discovery 函数 | 自动发现审计实例，更新配置 | TIMER | 每日 02:00 |
| Dispatcher 函数 | 读取配置，扇出调度 | TIMER | 每 5 分钟 |
| Worker 函数 | 拉取日志，上传 OBS，更新状态 | FGS invoke | 被 Dispatcher 调用 |
| OBS 桶 | 审计日志归档 + 配置文件存储 | — | — |
| GeminiDB | 采集状态（断点续传） | — | — |
| DLI | SQL 查询审计日志 | 用户手动 | 按需 |

## 断点续传机制

Worker 函数在 GeminiDB 中为每个数据库实例维护一条状态记录：

```json
{
  "instance_id": "rds-xxx",
  "last_timestamp": "2026-10-12T10:30:00",
  "logs_collected": 15,
  "updated_at": "2026-10-12T10:35:00Z"
}
```

每次采集时，Worker 从 `last_timestamp` 之后的日志开始拉取，确保不重复不遗漏。如果 Worker 中途失败，下次 Dispatcher 调用时从同一断点继续。

## gzip 压缩

当 `ENABLE_GZIP=true` 时，Worker 对审计日志内容做 gzip 压缩后上传 OBS，文件名追加 `.gz` 后缀。DLI 查询时使用 `audit_logs_gz` 表（指定 GZIP 压缩格式）。

切换压缩策略时，OBS 中可能残留未压缩的旧日志文件。可用 DLI SQL 查询 OBS 桶中 `.gz` 和非 `.gz` 文件，或使用 obsutil 批量处理。
