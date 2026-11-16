"""
Discovery Function — 数据库实例自动发现函数（FunctionGraph）
===========================================

定时触发后，自动扫描当前区域内的 RDS MySQL / GaussDB MySQL 实例，
检查是否已开启审计日志，将结果写入 OBS 上的集群配置文件，
供 Dispatcher 函数后续扇出调用。

数据库实例自动发现函数。
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
CLUSTER_CONFIG_KEY = os.environ.get("CLUSTER_CONFIG_KEY", "cluster_config.json")


# ============================================================================
# RDS 实例发现
# ============================================================================

def list_rds_mysql_instances():
    """
    列出当前区域内所有 RDS MySQL 实例。

    使用 hcloud CLI 调用 RDS ListInstances API。
    """
    import subprocess

    cmd = [
        "hcloud", "RDS", "ListInstances",
        "--datastore_type", "MySQL",
        "--limit", "100",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        logger.error(f"RDS ListInstances failed: {result.stderr}")
        return []

    data = json.loads(result.stdout)
    instances = data.get("instances", [])
    return instances


def check_audit_log_enabled(instance_id):
    """
    检查 RDS 实例是否已开启审计日志。

    华为云 RDS MySQL 审计日志通过参数「rds@audit_log」控制。
    """
    import subprocess

    cmd = [
        "hcloud", "RDS", "ShowInstanceConfiguration",
        "--instance_id", instance_id,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return False

        config = json.loads(result.stdout)
        params = config.get("configuration_parameter", {}).get("parameters", [])

        for param in params:
            name = param.get("name", "")
            value = param.get("value", "")
            # 华为云 RDS MySQL 审计日志参数
            if "audit" in name.lower() and value.lower() in ("on", "true", "1"):
                return True
        return False
    except Exception as e:
        logger.warning(f"Failed to check audit config for {instance_id}: {e}")
        return False


# ============================================================================
# 集群配置管理
# ============================================================================

def load_existing_config():
    """从 OBS 读取现有集群配置（保留用户手动添加的条目）。"""
    resp = obs_client.getObject(OBS_BUCKET, CLUSTER_CONFIG_KEY)
    if resp.status < 300:
        data = resp.body.buffer.read().decode("utf-8")
        return json.loads(data)
    return {"clusters": []}


def save_cluster_config(config):
    """将集群配置写入 OBS。"""
    data = json.dumps(config, indent=2, ensure_ascii=False)
    resp = obs_client.putContent(OBS_BUCKET, CLUSTER_CONFIG_KEY, data)
    if resp.status >= 300:
        raise RuntimeError(f"Failed to save config: {resp.errorMessage}")


# ============================================================================
# 主处理函数
# ============================================================================

def handler(event, context):
    """
    Discovery 函数入口。

    由 FunctionGraph TIMER 触发器定时调用（默认每日一次）。
    自动发现已开启审计日志的 RDS MySQL 实例，更新集群配置。
    """
    logger.info(f"Starting discovery in region {REGION}")

    # 1. 列出所有 RDS MySQL 实例
    instances = list_rds_mysql_instances()
    logger.info(f"Found {len(instances)} RDS MySQL instances")

    # 2. 逐个检查是否开启审计日志
    discovered = []
    for inst in instances:
        instance_id = inst.get("id", "")
        instance_name = inst.get("name", instance_id)

        if not instance_id:
            continue

        audit_enabled = check_audit_log_enabled(instance_id)
        if audit_enabled:
            discovered.append({
                "cluster_id": instance_id,
                "cluster_name": instance_name,
                "engine": "mysql",
                "region": REGION,
                "enabled": True,
                "auto_discovered": True,
            })
            logger.info(f"Discovered audited instance: {instance_name} ({instance_id})")

    # 3. 合并到现有配置（保留手动添加的非自动发现条目）
    existing_config = load_existing_config()
    existing_clusters = existing_config.get("clusters", [])

    # 保留手动添加的条目（auto_discovered != True）
    manual_clusters = [c for c in existing_clusters if not c.get("auto_discovered", False)]

    # 合并：自动发现的 + 手动添加的
    all_clusters = discovered + manual_clusters

    new_config = {
        "clusters": all_clusters,
        "last_discovery": {
            "timestamp": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
            "total_discovered": len(discovered),
            "total_manual": len(manual_clusters),
        },
    }

    save_cluster_config(new_config)

    logger.info(f"Discovery complete: {len(discovered)} auto-discovered, "
                f"{len(manual_clusters)} manual, {len(all_clusters)} total")

    return {
        "statusCode": 200,
        "body": json.dumps({
            "discovered": len(discovered),
            "manual": len(manual_clusters),
            "total": len(all_clusters),
        }),
    }
