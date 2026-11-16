# ============================================================================
# 云原生安全 · 数据库审计日志 Serverless 采集方案
# ============================================================================
# 本 Terraform 配置在华为云上搭建 RDS MySQL / GaussDB MySQL 审计日志的
# Serverless 自动采集与查询方案。
# 架构：
#   FunctionGraph TIMER → Discovery 函数（每日发现审计实例）
#   FunctionGraph TIMER → Dispatcher 函数（定时扇出）→ Worker 函数（逐实例采集）
#   Worker → RDS API 拉取审计日志 → OBS 归档 → GeminiDB 记账状态
#   DLI（数据湖探索）→ 对 OBS 审计日志做 SQL 查询
# ============================================================================

terraform {
  required_version = ">= 1.5"

  required_providers {
    huaweicloud = {
      source  = "huaweicloud/huaweicloud"
      version = "~> 1.70"
    }
  }

  backend "local" {}
}

# ============================================================================
# Provider
# ============================================================================

provider "huaweicloud" {
  region = var.region
  # export HW_ACCESS_KEY="your-access-key"
  # export HW_SECRET_KEY="your-secret-key"
}

# ============================================================================
# 变量
# ============================================================================

variable "region" {
  description = "华为云区域"
  type        = string
  default     = "cn-north-4"
}

variable "project_name" {
  description = "项目名称，用于资源命名前缀"
  type        = string
  default     = "db-audit-log-serverless"
}

variable "obs_bucket_name" {
  description = "OBS 桶名（全局唯一，用于存储审计日志）"
  type        = string
  default     = "db-audit-log-serverless-archive"
}

variable "geminidb_password" {
  description = "GeminiDB 管理密码"
  type        = string
  sensitive   = true
  default     = "ChangeMe!2026"
}

variable "domain_name" {
  description = "华为云账号域名（IAM 委托归属域），需替换为实际域名"
  type        = string
  default     = "hw011993152"
}

variable "dispatcher_schedule" {
  description = "Dispatcher 定时触发表达式（Quartz Cron），默认每 5 分钟"
  type        = string
  default     = "0 */5 * * * ?"
}

variable "discovery_schedule" {
  description = "Discovery 定时触发表达式（Quartz Cron），默认每日 02:00"
  type        = string
  default     = "0 0 2 * * ?"
}

variable "worker_memory" {
  description = "Worker 函数内存上限（MB）"
  type        = number
  default     = 512
}

variable "worker_timeout" {
  description = "Worker 函数超时（秒）"
  type        = number
  default     = 300
}

variable "dispatcher_memory" {
  description = "Dispatcher 函数内存上限（MB）"
  type        = number
  default     = 256
}

variable "dispatcher_timeout" {
  description = "Dispatcher 函数超时（秒）"
  type        = number
  default     = 60
}

variable "discovery_memory" {
  description = "Discovery 函数内存上限（MB）"
  type        = number
  default     = 256
}

variable "discovery_timeout" {
  description = "Discovery 函数超时（秒）"
  type        = number
  default     = 120
}

variable "enable_gzip" {
  description = "是否对审计日志启用 gzip 压缩后上传 OBS"
  type        = bool
  default     = true
}

# ============================================================================
# Layer 1: IAM 委托
# ============================================================================

resource "huaweicloud_identity_agency" "functiongraph" {
  name                  = "${var.project_name}-fgs-agency"
  description           = "Agency for FunctionGraph to access OBS/GeminiDB/RDS"
  duration              = "FOREVER"
  delegated_domain_name = var.domain_name

  project_role {
    project = var.region
    roles = [
      "OBS OperateAccess",
      "RDS FullAccess",
      "GeminiDB FullAccess",
    ]
  }
}

# ============================================================================
# Layer 2: 数据与存储
# ============================================================================

# OBS 桶 — 审计日志归档
resource "huaweicloud_obs_bucket" "audit_archive" {
  bucket        = var.obs_bucket_name
  acl           = "private"
  force_destroy = true
  versioning    = true

  tags = {
    project = var.project_name
    purpose = "rds-audit-log-archive"
  }
}

# OBS 桶 — 函数代码包
resource "huaweicloud_obs_bucket" "function_code" {
  bucket        = "${var.obs_bucket_name}-code"
  acl           = "private"
  force_destroy = true

  tags = {
    project = var.project_name
    purpose = "function-code"
  }
}

# GeminiDB 实例 — DynamoDB 兼容，存储采集状态
resource "huaweicloud_geminidb_instance" "state_store" {
  name              = "${var.project_name}-state"
  mode              = "Cluster"
  password          = var.geminidb_password
  availability_zone = "${var.region}a,${var.region}b,${var.region}c"
  vpc_id            = huaweicloud_vpc.main.id
  subnet_id         = huaweicloud_vpc_subnet.main.id
  security_group_id = huaweicloud_networking_secgroup.main.id

  datastore {
    type           = "cassandra"
    storage_engine = "rocksDB"
    version        = "3.11"
  }

  flavor {
    num       = 3
    size      = 100
    spec_code = "geminidb.cassandra.large.4"
    storage   = "ULTRAHIGH"
  }

  tags = {
    project = var.project_name
    purpose = "audit-log-state"
  }
}

# ============================================================================
# Layer 1: 网络（GeminiDB 需要网络）
# ============================================================================

resource "huaweicloud_vpc" "main" {
  name = "${var.project_name}-vpc"
  cidr = "10.0.0.0/16"
}

resource "huaweicloud_vpc_subnet" "main" {
  name       = "${var.project_name}-subnet"
  vpc_id     = huaweicloud_vpc.main.id
  cidr       = "10.0.1.0/24"
  gateway_ip = "10.0.1.1"
}

resource "huaweicloud_networking_secgroup" "main" {
  name        = "${var.project_name}-sg"
  description = "Security group for ${var.project_name}"
}

# ============================================================================
# Layer 3: FunctionGraph 函数
# ============================================================================

# --- Worker 函数：拉取 RDS 审计日志 → OBS + GeminiDB ---
resource "huaweicloud_fgs_function" "worker" {
  name        = "${var.project_name}-worker"
# agency      = huaweicloud_identity_agency.functiongraph.name
  handler     = "worker_lambda.handler"
  memory_size = var.worker_memory
  timeout     = var.worker_timeout
  runtime     = "Python3.9"

  code_type = "inline"
  func_code = "UEsDBBQAAAAIAKZ9TF2nQ8mDcAAAAIQAAAAQABwAd29ya2VyX2xhbWJkYS5weVVUCQADCJDMagiQzGp1eAsAAQQAAAAABAAAAAAljMEKg0AMRO/7FSEnBSnSo1e/RE2k1ppINlsV8d+70rnN481My6rm8I4qgXiEVyf0YSv4y+IVDCrOu5dNgBxjTyZwYvTOU2yVGBt41nUF2CsdudxHD0rLGosTxySDTyqZ46Y2s2E2/+Ob6YxXeYUfUEsBAh4DFAAAAAgApn1MXadDyYNwAAAAhAAAABAAGAAAAAAAAQAAAKSBAAAAAHdvcmtlcl9sYW1iZGEucHlVVAUAAwiQzGp1eAsAAQQAAAAABAAAAABQSwUGAAAAAAEAAQBWAAAAugAAAAAA"
  code_filename = "worker.zip"
  app           = "default"

  user_data = jsonencode({
    OBS_BUCKET          = huaweicloud_obs_bucket.audit_archive.bucket
    GEMINIDB_ENDPOINT   = huaweicloud_geminidb_instance.state_store.lb_ip_address
    REGION              = var.region
    ENABLE_GZIP         = tostring(var.enable_gzip)
    STATE_TABLE_NAME    = "AuditLogState"
  })
}

# --- Dispatcher 函数：读集群配置 → 扇出调用 Worker ---
resource "huaweicloud_fgs_function" "dispatcher" {
  name        = "${var.project_name}-dispatcher"
# agency      = huaweicloud_identity_agency.functiongraph.name
  handler     = "dispatcher_lambda.handler"
  memory_size = var.dispatcher_memory
  timeout     = var.dispatcher_timeout
  runtime     = "Python3.9"

  code_type = "inline"
  func_code = "UEsDBBQAAAAIAKZ9TF2rRS3ycwAAAIgAAAAUABwAZGlzcGF0Y2hlcl9sYW1iZGEucHlVVAkAAwiQzGoIkMxqdXgLAAEEAAAAAAQAAAAAJYxBCoMwEEX3OcUwKwUp0qXbniTNjJi2zoRkUlrEuxvx7/7j8eKaNBu8ioojnmHxQh/OHX9ZbICgYvyzfnLQltlqFtiwmLdaHkqME9zHcQB8Kv3bOUM3qmsq3YZzlWBRpXGkWJK3sHDGZl+Bk+sb9353B1BLAQIeAxQAAAAIAKZ9TF2rRS3ycwAAAIgAAAAUABgAAAAAAAEAAACkgQAAAABkaXNwYXRjaGVyX2xhbWJkYS5weVVUBQADCJDManV4CwABBAAAAAAEAAAAAFBLBQYAAAAAAQABAFoAAADBAAAAAAA="
  code_filename = "dispatcher.zip"
  app           = "default"

  user_data = jsonencode({
    OBS_BUCKET        = huaweicloud_obs_bucket.audit_archive.bucket
    GEMINIDB_ENDPOINT = huaweicloud_geminidb_instance.state_store.lb_ip_address
    REGION            = var.region
    WORKER_FUNCTION   = huaweicloud_fgs_function.worker.name
    CLUSTER_CONFIG_KEY = "cluster_config.json"
  })
}

# --- Discovery 函数：自动发现已开启审计的 RDS/GaussDB 实例 ---
resource "huaweicloud_fgs_function" "discovery" {
  name        = "${var.project_name}-discovery"
# agency      = huaweicloud_identity_agency.functiongraph.name
  handler     = "discovery_lambda.handler"
  memory_size = var.discovery_memory
  timeout     = var.discovery_timeout
  runtime     = "Python3.9"

  code_type = "inline"
  func_code = "UEsDBBQAAAAIAKZ9TF0AVJ1mcwAAAIcAAAATABwAZGlzY292ZXJ5X2xhbWJkYS5weVVUCQADCJDMagiQzGp1eAsAAQQAAAAABAAAAAAljMEKg0AMRO/7FSEnBSnSo9d+id1Eum1NZJMVRfz3rnRu83gzaV40O7xNJRBP8BqFvpwbXlm8g6jivHk7BKjJ7CULHGg+erGHEuMA977vAJ9Key3X0Y3KvFhz4FQkelKpHClZ1JXzjlX+7y+sHzzbM/wAUEsBAh4DFAAAAAgApn1MXQBUnWZzAAAAhwAAABMAGAAAAAAAAQAAAKSBAAAAAGRpc2NvdmVyeV9sYW1iZGEucHlVVAUAAwiQzGp1eAsAAQQAAAAABAAAAABQSwUGAAAAAAEAAQBZAAAAwAAAAAAA"
  code_filename = "discovery.zip"
  app           = "default"

  user_data = jsonencode({
    OBS_BUCKET        = huaweicloud_obs_bucket.audit_archive.bucket
    REGION            = var.region
    CLUSTER_CONFIG_KEY = "cluster_config.json"
  })
}

# ============================================================================
# Layer 4: 定时触发器 → 见 triggers.tf
# Layer 5: DLI 数据库（Athena/Glue → DLI）
# ============================================================================
# DLI 数据库和表通过 SQL 脚本在部署后创建（provider 对 DLI DDL 支持有限）
# 详见 scripts/create_dli_tables.sql

# ============================================================================
# 输出
# ============================================================================

output "obs_audit_bucket" {
  description = "审计日志归档 OBS 桶名"
  value       = huaweicloud_obs_bucket.audit_archive.bucket
}

output "geminidb_endpoint" {
  description = "GeminiDB（DynamoDB 兼容）端点"
  value       = huaweicloud_geminidb_instance.state_store.lb_ip_address
  sensitive   = true
}

output "worker_function_name" {
  description = "Worker 函数名"
  value       = huaweicloud_fgs_function.worker.name
}

output "dispatcher_function_name" {
  description = "Dispatcher 函数名"
  value       = huaweicloud_fgs_function.dispatcher.name
}

output "discovery_function_name" {
  description = "Discovery 函数名"
  value       = huaweicloud_fgs_function.discovery.name
}

output "dli_database_name" {
  description = "DLI 数据库名（需手动执行 scripts/create_dli_tables.sql 创建）"
  value       = "audit_log_db"
}
