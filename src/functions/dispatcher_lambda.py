"""
Dispatcher Function — 审计日志采集调度函数（FunctionGraph）
=========================================

定时触发后，从 OBS 读取集群配置（cluster_config.json），
对每个已启用审计的数据库实例异步调用 Worker 函数。

审计日志采集调度函数。
"""

import json
import os
import logging

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

REGION = os.environ.get("REGION", "cn-north-4")
OBS_BUCKET = os.environ.get("OBS_BUCKET", "")
WORKER_FUNCTION = os.environ.get("WORKER_FUNCTION", "")
CLUSTER_CONFIG_KEY = os.environ.get("CLUSTER_CONFIG_KEY", "cluster_config.json")


# ============================================================================
# 集群配置管理
# ============================================================================

def load_cluster_config():
    """从 OBS 读取集群配置文件。"""
    resp = obs_client.getObject(OBS_BUCKET, CLUSTER_CONFIG_KEY)
    if resp.status < 300:
        config_data = resp.body.buffer.read().decode("utf-8")
        return json.loads(config_data)
    else:
        logger.error(f"Failed to load cluster config: {resp.errorMessage}")
        return {"clusters": []}


def save_cluster_config(config):
    """将集群配置写回 OBS。"""
    data = json.dumps(config, indent=2, ensure_ascii=False)
    resp = obs_client.putContent(OBS_BUCKET, CLUSTER_CONFIG_KEY, data)
    if resp.status >= 300:
        raise RuntimeError(f"Failed to save cluster config: {resp.errorMessage}")


# ============================================================================
# FunctionGraph 函数调用
# ============================================================================

def invoke_worker(cluster):
    """
    异步调用 Worker 函数处理单个集群实例。

    华为云 FunctionGraph 通过 SDK 或 CLI 调用，
    使用 hcloud CLI 在函数运行时内执行。
    """
    import subprocess

    event_payload = json.dumps({
        "cluster_id": cluster["cluster_id"],
        "cluster_name": cluster.get("cluster_name", cluster["cluster_id"]),
        "engine": cluster.get("engine", "mysql"),
        "region": cluster.get("region", REGION),
    })

    cmd = [
        "hcloud", "FunctionGraph", "InvokeFunction",
        "--function_urn", WORKER_FUNCTION,
        "--body", event_payload,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            logger.info(f"Invoked worker for {cluster['cluster_id']}")
            return True
        else:
            logger.error(f"Invoke failed for {cluster['cluster_id']}: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Invoke error for {cluster['cluster_id']}: {e}")
        return False


# ============================================================================
# 主处理函数
# ============================================================================

def handler(event, context):
    """
    Dispatcher 函数入口。

    由 FunctionGraph TIMER 触发器定时调用，
    event 为定时触发器事件（无业务参数）。
    """
    logger.info("Dispatcher triggered, loading cluster config...")

    config = load_cluster_config()
    clusters = config.get("clusters", [])

    # 仅处理已启用的集群
    enabled_clusters = [c for c in clusters if c.get("enabled", True)]
    logger.info(f"Found {len(enabled_clusters)} enabled clusters")

    if not enabled_clusters:
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "no enabled clusters",
                "invoked": 0,
            }),
        }

    # 扇出调用 Worker
    invoked_count = 0
    failed_count = 0

    for cluster in enabled_clusters:
        success = invoke_worker(cluster)
        if success:
            invoked_count += 1
        else:
            failed_count += 1

    logger.info(f"Dispatch complete: {invoked_count} invoked, {failed_count} failed")

    return {
        "statusCode": 200,
        "body": json.dumps({
            "total_clusters": len(enabled_clusters),
            "invoked": invoked_count,
            "failed": failed_count,
        }),
    }
