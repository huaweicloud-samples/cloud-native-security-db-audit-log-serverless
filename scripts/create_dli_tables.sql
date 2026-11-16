-- ============================================================================
-- DLI 数据库与表创建脚本
-- ============================================================================
-- 数据湖探索（DLI）内的库表元数据由 Data Catalog 统一管理。
-- 部署 Terraform 后，在 DLI 控制台或使用 dli-client 执行以下 SQL。
--
-- 使用方式：
--   1. 登录华为云控制台 → 数据湖探索 (DLI)
--   2. 打开 SQL 编辑器，选择 default 队列
--   3. 依次执行以下语句
-- ============================================================================

-- 创建数据库
CREATE DATABASE IF NOT EXISTS audit_log_db
  COMMENT 'RDS MySQL 审计日志查询数据库';

-- 创建审计日志表（OBS 外表，对应审计日志文件）
CREATE TABLE IF NOT EXISTS audit_log_db.audit_logs (
  `timestamp`        STRING  COMMENT '日志时间戳',
  `server_host`      STRING  COMMENT '服务器主机名',
  `username`         STRING  COMMENT '连接用户名',
  `host`             STRING  COMMENT '客户端 IP',
  `connection_id`    BIGINT  COMMENT '连接 ID',
  `query_id`         BIGINT  COMMENT '查询 ID',
  `operation`        STRING  COMMENT '操作类型 (SELECT/INSERT/UPDATE/DELETE/...)',
  `database`         STRING  COMMENT '数据库名',
  `object`           STRING  COMMENT '操作对象',
  `retcode`          INT     COMMENT '返回码',
  `query`            STRING  COMMENT 'SQL 语句'
)
COMMENT 'RDS MySQL 审计日志表'
STORED AS TEXTFILE
LOCATION 'obs://<OBS_BUCKET_NAME>/audit-logs/'
TBLPROPERTIES (
  'skip.header.line.count' = '0',
  'fileformat' = 'textfile'
);

-- 如果审计日志启用了 gzip 压缩，使用以下版本
CREATE TABLE IF NOT EXISTS audit_log_db.audit_logs_gz (
  `timestamp`        STRING,
  `server_host`      STRING,
  `username`         STRING,
  `host`             STRING,
  `connection_id`    BIGINT,
  `query_id`         BIGINT,
  `operation`        STRING,
  `database`         STRING,
  `object`           STRING,
  `retcode`          INT,
  `query`            STRING
)
COMMENT 'RDS MySQL 审计日志表（gzip 压缩）'
STORED AS TEXTFILE
LOCATION 'obs://<OBS_BUCKET_NAME>/audit-logs/'
TBLPROPERTIES (
  'orc.compress' = 'GZIP'
);

-- 查询示例：最近 24 小时的 DELETE 操作
-- SELECT * FROM audit_log_db.audit_logs
-- WHERE operation = 'DELETE'
--   AND `timestamp` >= date_sub(current_date(), 1)
-- ORDER BY `timestamp` DESC
-- LIMIT 100;
