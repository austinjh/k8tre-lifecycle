from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from kubernetes import client, config

from ..rate_card import estimate, load_rate_card

_GIB = 1024 ** 3


def parse_cpu(value: str | None) -> float:
    if not value:
        return 0.0
    text = str(value)
    if text.endswith("m"):
        return float(text[:-1]) / 1000
    return float(text)


def parse_memory_gib(value: str | None) -> float:
    if not value:
        return 0.0
    text = str(value)
    multipliers = {
        "Ki": 1024,
        "Mi": 1024 ** 2,
        "Gi": 1024 ** 3,
        "Ti": 1024 ** 4,
        "K": 1000,
        "M": 1000 ** 2,
        "G": 1000 ** 3,
        "T": 1000 ** 4,
    }
    match = re.fullmatch(r"([0-9.]+)([A-Za-z]*)", text)
    if not match:
        return float(text) / _GIB
    number, suffix = match.groups()
    multiplier = multipliers.get(suffix, 1)
    return float(number) * multiplier / _GIB


def _kube_clients() -> tuple[client.CoreV1Api, Any]:
    try:
        config.load_incluster_config()
    except Exception:
        config.load_kube_config()
    return client.CoreV1Api(), client.VERSION


def collect(hours: float = 1.0, rate_card_version: str = "v1") -> dict[str, Any]:
    """Read Kubernetes requests/capacity and produce simulated metered usage."""
    core, _ = _kube_clients()
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)

    namespaces = core.list_namespace(label_selector="k8tre.io/resource-type=project-namespace").items
    project_namespaces = {
        ns.metadata.name: ns.metadata.name.removeprefix("project-")
        for ns in namespaces
    }

    capacity: dict[str, float] = {"cpu": 0.0, "memory_gib": 0.0, "gpu_count": 0}
    for node in core.list_node().items:
        allocatable = (node.status.allocatable or {}) if node.status else {}
        capacity["cpu"] += parse_cpu(allocatable.get("cpu"))
        capacity["memory_gib"] += parse_memory_gib(allocatable.get("memory"))
        capacity["gpu_count"] += int(allocatable.get("nvidia.com/gpu", 0))

    projects: dict[str, dict[str, float]] = {}
    for namespace, project in project_namespaces.items():
        usage = {"cpu_vcpu": 0.0, "memory_gib": 0.0, "gpu_count": 0.0}
        for pod in core.list_namespaced_pod(namespace).items:
            if not pod.status or pod.status.phase != "Running":
                continue
            for container in pod.spec.containers or []:
                requests = (container.resources.requests or {}) if container.resources else {}
                usage["cpu_vcpu"] += parse_cpu(requests.get("cpu"))
                usage["memory_gib"] += parse_memory_gib(requests.get("memory"))
                usage["gpu_count"] += parse_cpu(requests.get("nvidia.com/gpu"))
        lines = estimate(usage, hours, load_rate_card(rate_card_version))
        projects[project] = {
            "namespace": namespace,
            "cpu_request_vcpu_hours": round(usage["cpu_vcpu"] * hours, 2),
            "memory_request_gib_hours": round(usage["memory_gib"] * hours, 2),
            "gpu_hours": round(usage["gpu_count"] * hours, 2),
            "amount": lines["total"],
        }

    return {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "hours": hours,
        "rate_card_version": rate_card_version,
        "node_capacity": capacity,
        "projects": projects,
        "total_amount": round(sum(item["amount"] for item in projects.values()), 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect simulated Kubernetes usage")
    parser.add_argument("--hours", type=float, default=float(os.environ.get("LIFECYCLE_COLLECTION_HOURS", "1")))
    parser.add_argument("--rate-card", default=os.environ.get("LIFECYCLE_RATE_CARD", "v1"))
    parser.add_argument("--post", metavar="URL", help="POST each project record to lifecycle-core")
    args = parser.parse_args()

    snapshot = collect(hours=args.hours, rate_card_version=args.rate_card)
    print(json.dumps(snapshot, indent=2))

    if args.post:
        import httpx

        token = os.environ.get("LIFECYCLE_INTERNAL_TOKEN", "")
        headers = {
            "x-lifecycle-actor": "usage-collector",
            "x-lifecycle-roles": "tre_operator",
        }
        if token:
            headers["x-lifecycle-token"] = token
        with httpx.Client(timeout=30) as http:
            id_by_name = {
                item["name"]: item["id"]
                for item in http.get(f"{args.post}/v1/projects").json()
            }
            for project, usage in snapshot["projects"].items():
                project_id = id_by_name.get(project)
                if not project_id:
                    print(f"skip: no lifecycle project named {project}", flush=True)
                    continue
                response = http.post(
                    f"{args.post}/v1/projects/{project_id}/usage-records",
                    headers=headers,
                    json={
                        "period_start": snapshot["period_start"],
                        "period_end": snapshot["period_end"],
                        "cpu_request_vcpu_hours": usage["cpu_request_vcpu_hours"],
                        "memory_request_gib_hours": usage["memory_request_gib_hours"],
                        "gpu_hours": usage["gpu_hours"],
                        "node_capacity": snapshot["node_capacity"],
                        "rate_card_version": snapshot["rate_card_version"],
                        "amount": usage["amount"],
                    },
                )
                response.raise_for_status()


if __name__ == "__main__":
    main()
