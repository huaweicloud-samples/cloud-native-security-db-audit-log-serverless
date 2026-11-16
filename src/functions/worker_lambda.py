"""
Worker Function — RDS 审计日志采集函数（FunctionGraph）
=====================================

从 RDS/GaussDB MySQL 实例拉取审计日志，上传到 OBS，并在 GeminiDB
（DynamoDB 兼容）中记录采集状态（断点续传）。

RDS 审计日志采集 Worker 函数。
"""

import json
import os
import time
import gzip
import logging
import hashlib
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ============================================================================
# 华为云 SDK 初始化
# ============================================================================

from obs import ObsClient

obs_client = ObsClient(
    access_key_id=os.environ.get("HW_ACCESS_KEY"),
    secret_access_key=os.environ.get("HW_SECRET_KEY"),
    server=f"https://obs.{os.environ.get('REGION', 'cn-north-4')}.myhuaweicloud.com",
)

# GeminiDB 兼容 DynamoDB 协议，使用 boto3 直连
import boto3

geminidb_endpoint = os.environ.get("GEMINIDB_ENDPOINT")
dynamodb = boto3.resource(
    "dynamodb",
    endpoint_url=f"http://{geminidb_endpoint}",
    aws_access_key_id=os.environ.get("HW_ACCESS_KEY"),
    aws_secret_access_key=os.environ.get("HW_SECRET_KEY"),
    region_name=os.environ.get("REGION", "cn-north-4"),
)

# 华为云 RDS SDK
import hmac
import hashlib as _hl
import requests

REGION = os.environ.get("REGION", "cn-north-4")
RDS_ENDPOINT = f"https://rds.{REGION}.myhuaweicloud.com"
OBS_BUCKET = os.environ.get("OBS_BUCKET", "")
STATE_TABLE = os.environ.get("STATE_TABLE_NAME", "AuditLogState")
ENABLE_GZIP = os.environ.get("ENABLE_GZIP", "true").lower() == "true"


# ============================================================================
# RDS 审计日志 API 调用
# ============================================================================

def get_rds_audit_logs(instance_id, marker=None, max_records=100):
    """
    通过华为云 RDS API 获取实例的审计日志列表。

    华为云 RDS MySQL 审计日志通过「查询审计日志」接口获取，
    对应 API: GET /v3/{project_id}/instances/{instance_id}/audit-log
    """
    # 使用 hcloud CLI 调用（FunctionGraph 运行时内置）
    import subprocess
    cmd = [
        "hcloud", "RDS", "ListAuditLogs",
        "--instance_id", instance_id,
        "--limit", str(max_records),
    ]
    if marker:
        cmd.extend(["--marker", marker])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        logger.error(f"RDS API error: {result.stderr}")
        return [], None

    data = json.loads(result.stdout)
    logs = data.get("audit_logs", [])
    next_marker = data.get("marker")
    return logs, next_marker


def download_audit_log(instance_id, log_name):
    """
    下载单个审计日志文件。
    华为云 RDS 审计日志下载链接通过 API 获取后用 requests 下载。
    """
    import subprocess
    cmd = [
        "hcloud", "RDS", "GetAuditLogDownloadLink",
        "--instance_id", instance_id,
        "--name", log_name,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to get download link: {result.stderr}")

    download_url = json.loads(result.stdout).get("download_link")
    resp = requests.get(download_url, timeout=300)
    resp.raise_for_status()
    return resp.content


# ============================================================================
# 状态管理（GeminiDB / DynamoDB 兼容）
# ============================================================================

def get_last_processed_timestamp(instance_id):
    """获取该实例上次采集到的时间戳（断点续传）。"""
    table = dynamodb.Table(STATE_TABLE)
    try:
        resp = table.get_item(Key={"instance_id": instance_id})
        item = resp.get("Item")
        if item:
            return item.get("last_timestamp", "")
        return ""
    except Exception as e:
        logger.warning(f"State lookup failed: {e}")
        return ""


def update_state(instance_id, last_timestamp, logs_collected):
    """更新采集状态。"""
    table = dynamodb.Table(STATE_TABLE)
    table.put_item(Item={
        "instance_id": instance_id,
        "last_timestamp": last_timestamp,
        "logs_collected": logs_collected,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


# ============================================================================
# OBS 上传
# ============================================================================

def upload_to_obs(data, object_key):
    """上传审计日志到 OBS，可选 gzip 压缩。"""
    if ENABLE_GZIP:
        data = gzip.compress(data)
        object_key += ".gz"
        content_type = "application/gzip"
    else:
        content_type = "text/plain"

    resp = obs_client.putContent(OBS_BUCKET, object_key, data, contentType=content_type)
    if resp.status < 300:
        logger.info(f"Uploaded {object_key} ({len(data)} bytes)")
        return object_key
    else:
        raise RuntimeError(f"OBS upload failed: {resp.errorMessage}")


# ============================================================================
# 主处理函数
# ============================================================================

def handler(event, context):
    """
    Worker 函数入口。

    event 结构（FunctionGraph 触发器或 Dispatcher 调用传入）:
    {
        "cluster_id": "rds-instance-id",
        "cluster_name": "my-mysql-db",
        "engine": "mysql",
        "region": "cn-north-4"
    }
    """
    cluster_id = event.get("cluster_id", "")
    cluster_name = event.get("cluster_name", cluster_id)
    engine = event.get("engine", "mysql")
    region = event.get("region", REGION)

    if not cluster_id:
        return {"statusCode": 400, "body": json.dumps({"error": "missing cluster_id"})}

    logger.info(f"Processing audit logs for instance: {cluster_id} ({cluster_name})")

    # 1. 获取上次采集断点
    last_timestamp = get_last_processed_timestamp(cluster_id)

    # 2. 拉取审计日志列表
    all_logs = []
    marker = None
    while True:
        logs, marker = get_rds_audit_logs(cluster_id, marker=marker)
        # 过滤已处理的日志（按时间戳）
        new_logs = [l for l in logs if l.get("begin_time", "") > last_timestamp]
        all_logs.extend(new_logs)
        if not marker or not new_logs:
            break

    if not all_logs:
        logger.info(f"No new audit logs for {cluster_id}")
        return {
            "statusCode": 200,
            "body": json.dumps({
                "instance_id": cluster_id,
                "logs_collected": 0,
                "message": "no new logs",
            }),
        }

    # 3. 逐个下载并上传到 OBS
    uploaded_count = 0
    latest_timestamp = last_timestamp
    for log_entry in all_logs:
        log_name = log_entry.get("name", "")
        log_time = log_entry.get("begin_time", "")

        try:
            raw_data = download_audit_log(cluster_id, log_name)
            date_str = log_time[:10] if log_time else datetime.now().strftime("%Y-%m-%d")
            object_key = f"audit-logs/{cluster_id}/{date_str}/{log_name}"
            final_key = upload_to_obs(raw_data, object_key)
            uploaded_count += 1
            if log_time > latest_timestamp:
                latest_timestamp = log_time
        except Exception as e:
            logger.error(f"Failed to process log {log_name}: {e}")
            continue

    # 4. 更新状态
    update_state(cluster_id, latest_timestamp, uploaded_count)

    logger.info(f"Completed: {uploaded_count} logs uploaded for {cluster_id}")

    return {
        "statusCode": 200,
        "body": json.dumps({
            "instance_id": cluster_id,
            "logs_collected": uploaded_count,
            "last_timestamp": latest_timestamp,
        }),
    }
