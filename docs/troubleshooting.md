# 故障排除

## Discovery 函数未发现实例

**症状**: Discovery 返回 `{"discovered": 0}`，但控制台中有 RDS MySQL 实例。

**排查**:
1. 确认 RDS 实例引擎为 MySQL（Discovery 仅扫描 MySQL）
2. 确认审计日志参数已开启：控制台 → RDS → 实例 → 参数配置 → 检查 `rds@audit_log` 是否为 ON
3. 确认 IAM 委托已授予 `RDS FullAccess` 角色
4. 查看 Discovery 函数日志，检查 `hcloud RDS ListInstances` 是否返回实例列表

## Worker 函数拉取日志失败

**症状**: Worker 返回错误，日志中显示 RDS API 调用失败。

**排查**:
1. 确认目标 RDS 实例状态为「运行中」
2. 确认审计日志已生成（RDS 控制台 → 实例 → 审计日志页面）
3. 确认 IAM 委托已授予 `RDS FullAccess`
4. 确认函数超时设置足够（默认 300s，大量日志时可能需要增加）
5. 检查 `hcloud` CLI 是否在 FunctionGraph 运行时中可用

## OBS 上传失败

**症状**: Worker 日志显示 `OBS upload failed`。

**排查**:
1. 确认 OBS 桶名正确（环境变量 `OBS_BUCKET`）
2. 确认桶存在且可访问
3. 确认 IAM 委托已授予 `OBS OperateAccess`
4. 确认访问密钥有效（通过环境变量 `HW_ACCESS_KEY` / `HW_SECRET_KEY` 注入）

## GeminiDB 连接失败

**症状**: Worker 日志显示 GeminiDB 状态读写超时或连接拒绝。

**排查**:
1. 确认 GeminiDB 实例状态为「运行中」
2. 确认 `GEMINIDB_ENDPOINT` 环境变量指向正确的连接地址
3. 确认安全组规则允许 FunctionGraph 访问 GeminiDB 端口
4. 确认 GeminiDB 引擎为 DynamoDB 兼容模式

## DLI 查询返回空结果

**症状**: SQL 查询 `audit_log_db.audit_logs` 返回 0 行。

**排查**:
1. 确认 OBS 桶中有审计日志文件（`obsutil ls obs://<bucket>/audit-logs/`）
2. 确认 DLI 表 LOCATION 指向正确的 OBS 路径
3. 如果日志文件为 `.gz` 后缀，使用 `audit_logs_gz` 表而非 `audit_logs`
4. 确认 DLI 队列有足够资源执行查询

## Dispatcher 调用 Worker 失败

**症状**: Dispatcher 返回 `{"failed": N}`，N > 0。

**排查**:
1. 确认 `WORKER_FUNCTION` 环境变量指向正确的 Worker 函数 URN
2. 确认 Worker 函数已部署且状态正常
3. 确认 IAM 委托有 `FunctionGraph FullAccess` 权限（或可调用其他函数的权限）
4. 查看 Worker 函数自身的执行日志，定位具体错误

## cluster_config.json 不存在

**症状**: Dispatcher 日志显示 `Failed to load cluster config`。

**原因**: 首次部署时 Discovery 尚未运行，配置文件不存在。

**解决**: 手动触发一次 Discovery 函数，或手动创建并上传一个空的配置文件：

```json
{"clusters": []}
```

上传到 OBS 桶根目录，key 为 `cluster_config.json`。
