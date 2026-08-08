"""Declared resource capacity for an environment."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ResourcePool:
    capacity: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "ResourcePool":
        raw = d.get("resources", d) if isinstance(d, dict) else {}
        return cls(capacity={str(k): float(v) for k, v in (raw or {}).items()})

    @classmethod
    def from_yaml(cls, path) -> "ResourcePool":
        return cls.from_dict(yaml.safe_load(Path(path).read_text()) or {})


def load_pool(repo_root) -> ResourcePool:
    path = Path(repo_root) / ".coscience" / "resources.yaml"
    if not path.is_file():
        return ResourcePool()
    return ResourcePool.from_yaml(path)


WORKER_KEY = "workers"


def effective_requirement(required: dict[str, float], pool: ResourcePool) -> dict[str, float]:
    """What a sprint actually consumes. When the pool declares a worker cap, every
    sprint costs one worker slot on top of what it declares — that is what bounds
    the number of agent processes running at once. A pool with no `workers` key is
    uncapped, exactly as before."""
    if WORKER_KEY not in pool.capacity:
        return dict(required)
    return {**required, WORKER_KEY: 1.0}
