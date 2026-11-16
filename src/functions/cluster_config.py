"""
集群配置数据模型
================

定义 ClusterConfig 数据结构，用于序列化/反序列化 cluster_config.json。
本模块在 FunctionGraph 运行时中被 worker/dispatcher/discovery 引用。
"""

import json
from dataclasses import dataclass, field, asdict
from typing import List, Optional


@dataclass
class ClusterEntry:
    """单个数据库集群/实例的配置。"""
    cluster_id: str
    cluster_name: str = ""
    engine: str = "mysql"
    region: str = ""
    enabled: bool = True
    auto_discovered: bool = False

    def __post_init__(self):
        if not self.cluster_name:
            self.cluster_name = self.cluster_id


@dataclass
class ClusterConfig:
    """集群配置集合。"""
    clusters: List[ClusterEntry] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "ClusterConfig":
        clusters = []
        for c in data.get("clusters", []):
            clusters.append(ClusterEntry(
                cluster_id=c["cluster_id"],
                cluster_name=c.get("cluster_name", ""),
                engine=c.get("engine", "mysql"),
                region=c.get("region", ""),
                enabled=c.get("enabled", True),
                auto_discovered=c.get("auto_discovered", False),
            ))
        return cls(clusters=clusters)

    @classmethod
    def from_json(cls, json_str: str) -> "ClusterConfig":
        return cls.from_dict(json.loads(json_str))

    def to_dict(self) -> dict:
        return {
            "clusters": [asdict(c) for c in self.clusters],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    def get_enabled(self) -> List[ClusterEntry]:
        return [c for c in self.clusters if c.enabled]

    def add(self, entry: ClusterEntry):
        self.clusters.append(entry)

    def remove(self, cluster_id: str):
        self.clusters = [c for c in self.clusters if c.cluster_id != cluster_id]
