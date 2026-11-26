from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_RATES: dict[str, float] = {
    "vcpu_hour": 0.035,
    "gib_hour": 0.004,
    "gpu_hour": 1.80,
    "storage_gib_month": 0.08,
    "vdi_hour": 0.12,
    "licence_day": 2.50,
}


def load_rate_card(version: str = "v1") -> dict[str, float]:
    path = Path(__file__).resolve().parents[1] / "config" / f"rate-card-{version}.json"
    if not path.exists():
        return dict(DEFAULT_RATES)
    payload: dict[str, Any] = json.loads(path.read_text())
    return {key: float(value) for key, value in payload.get("rates", {}).items()}


def estimate(resources: dict[str, float | int], hours: float, rates: dict[str, float]) -> dict[str, float]:
    """Apply a rate card to a requested resource profile.

    Amounts are simulated GBP for the POC; the adapter boundary allows a real
    cloud billing feed to replace the rate application later.
    """
    cpu = float(resources.get("cpu_vcpu", 0)) * hours * rates["vcpu_hour"]
    memory = float(resources.get("memory_gib", 0)) * hours * rates["gib_hour"]
    gpu = float(resources.get("gpu_count", 0)) * hours * rates["gpu_hour"]
    storage = float(resources.get("storage_gib", 0)) * rates["storage_gib_month"] / (730 / hours) if hours else 0.0
    vdi = float(resources.get("vdi_hours", 0)) * rates["vdi_hour"]
    licences = float(resources.get("licences", 0)) * (hours / 24) * rates["licence_day"]
    lines = {
        "cpu": round(cpu, 4),
        "memory": round(memory, 4),
        "gpu": round(gpu, 4),
        "storage": round(storage, 4),
        "vdi": round(vdi, 4),
        "licences": round(licences, 4),
    }
    lines["total"] = round(sum(lines.values()), 2)
    return lines
