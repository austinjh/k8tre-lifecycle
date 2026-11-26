from __future__ import annotations

import os
from typing import Iterator

os.environ.setdefault("LIFECYCLE_DATABASE_URL", "sqlite:///:memory:")

import pytest
from fastapi.testclient import TestClient

from app.db import get_engine, init_db, metadata
from app.main import app
from app.rate_card import estimate


@pytest.fixture()
def client() -> Iterator[TestClient]:
    metadata.drop_all(get_engine())
    init_db()
    with TestClient(app) as test_client:
        yield test_client


def headers(actor: str, *roles: str) -> dict[str, str]:
    return {
        "x-lifecycle-actor": actor,
        "x-lifecycle-roles": ",".join(roles),
    }


def assessment_answers(**overrides: str) -> dict[str, str]:
    answers = {
        "data_category": "PERSONAL",
        "data_volume": "SMALL",
        "vulnerable_groups": "NO",
        "systematic_monitoring": "NO",
        "data_matching": "NO",
        "new_technology": "NO",
        "legal_basis": "YES",
        "international_transfer": "NO",
        "retention_defined": "YES",
        "security_controls": "BASIC",
        "data_owner_assigned": "YES",
        "egress_review": "YES",
    }
    answers.update(overrides)
    return answers


def create_study_and_project(client: TestClient) -> tuple[str, str]:
    study = client.post(
        "/v1/studies",
        headers=headers("alice", "researcher"),
        json={"title": "Lifecycle demo", "description": "POC study", "owner": "alice"},
    ).json()
    project = client.post(
        "/v1/projects",
        headers=headers("alice", "researcher"),
        json={
            "study_id": study["id"],
            "name": "demo-project",
            "description": "Demonstration project",
            "owner": "alice",
        },
    ).json()
    return study["id"], project["id"]


def test_health(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_transition_requires_role(client: TestClient) -> None:
    _, project_id = create_study_and_project(client)
    response = client.post(
        f"/v1/projects/{project_id}/transitions",
        headers=headers("dpo-user", "dpo"),
        json={"action": "submit"},
    )
    assert response.status_code == 403


def test_researcher_can_submit_assessment(client: TestClient) -> None:
    """The demo researcher completes the questionnaire; the DPO approves it later."""
    _, project_id = create_study_and_project(client)
    for action in ("submit", "start_assessment"):
        response = client.post(
            f"/v1/projects/{project_id}/transitions",
            headers=headers("alice", "researcher"),
            json={"action": action},
        )
        assert response.status_code == 200, response.text

    assessment = client.post(
        f"/v1/projects/{project_id}/assessments",
        headers=headers("alice", "researcher"),
        json={"version": "2026-11-v1", "answers": assessment_answers()},
    )
    assert assessment.status_code == 201, assessment.text
    assert assessment.json()["created_by"] == "alice"

    governance = client.post(
        f"/v1/projects/{project_id}/transitions",
        headers=headers("alice", "researcher"),
        json={"action": "request_governance"},
    )
    assert governance.status_code == 200, governance.text


def test_full_lifecycle_with_audit(client: TestClient) -> None:
    _, project_id = create_study_and_project(client)

    def transition(action: str, actor: str, *roles: str, reason: str | None = None) -> None:
        response = client.post(
            f"/v1/projects/{project_id}/transitions",
            headers=headers(actor, *roles),
            json={"action": action, "reason": reason},
        )
        assert response.status_code == 200, response.text

    transition("submit", "alice", "researcher")
    transition("start_assessment", "dpo-user", "dpo")

    assessment = client.post(
        f"/v1/projects/{project_id}/assessments",
        headers=headers("dpo-user", "dpo"),
        json={"answers": assessment_answers()},
    )
    assert assessment.status_code == 201, assessment.text
    assert assessment.json()["classification"] == "INTERNAL"
    assert assessment.json()["control_tier"] == "TIER_1"
    assert "security_controls" in assessment.json()["evidence_required"]

    transition("request_governance", "alice", "researcher")
    approval = client.post(
        f"/v1/projects/{project_id}/approvals",
        headers=headers("dpo-user", "dpo"),
        json={
            "kind": "governance",
            "decision": "approved",
            "decided_by": "dpo-user",
            "rationale": "Mitigations acceptable",
        },
    )
    assert approval.status_code == 201
    transition("approve_governance", "dpo-user", "dpo")

    quote = client.post(
        f"/v1/projects/{project_id}/quotes",
        headers=headers("finance-user", "finance"),
        json={
            "resources": {
                "cpu_vcpu": 4,
                "memory_gib": 16,
                "gpu_count": 0,
                "storage_gib": 100,
            },
            "hours": 160,
            "valid_days": 14,
        },
    )
    assert quote.status_code == 201
    quote_id = quote.json()["id"]
    assert quote.json()["total_amount"] > 0
    assert client.post(
        f"/v1/quotes/{quote_id}/accept",
        headers=headers("finance-user", "finance"),
        json={"rationale": "Quote agreed"},
    ).status_code == 200

    budget = client.post(
        f"/v1/projects/{project_id}/budgets",
        headers=headers("finance-user", "finance"),
        json={"quote_id": quote_id, "committed_amount": quote.json()["total_amount"]},
    )
    assert budget.status_code == 201
    transition("approve_budget", "finance-user", "finance")

    allocation = client.post(
        f"/v1/projects/{project_id}/allocations",
        headers=headers("operator", "tre_operator"),
        json={"cpu_vcpu": 4, "memory_gib": 16, "storage_gib": 100},
    )
    assert allocation.status_code == 201
    transition("provision", "operator", "tre_operator")

    evidence = client.post(
        f"/v1/projects/{project_id}/evidence",
        headers=headers("operator", "tre_operator"),
        json={"kind": "provisioning", "object_key": "evidence/demo/provisioning.json"},
    )
    assert evidence.status_code == 201
    transition("activate", "operator", "tre_operator")

    client.post(
        f"/v1/projects/{project_id}/evidence",
        headers=headers("alice", "researcher"),
        json={"kind": "retention", "object_key": "evidence/demo/retention.pdf"},
    )
    transition("close", "alice", "project_manager")
    transition("archive", "operator", "tre_operator")

    project = client.get(f"/v1/projects/{project_id}").json()
    assert project["state"] == "ARCHIVED"
    assert project["control_tier"] == "TIER_1"

    audit = client.get(f"/v1/projects/{project_id}/audit").json()
    actions = {event["action"] for event in audit}
    assert {"submit", "approve_governance", "approve_budget", "activate", "archive"} <= actions

    outbox = client.get("/v1/outbox").json()
    assert len(outbox) >= len(audit)
    assert any(event["topic"] == "project.archive" for event in outbox)


def test_governance_requires_assessment(client: TestClient) -> None:
    _, project_id = create_study_and_project(client)
    client.post(
        f"/v1/projects/{project_id}/transitions",
        headers=headers("alice", "researcher"),
        json={"action": "submit"},
    )
    client.post(
        f"/v1/projects/{project_id}/transitions",
        headers=headers("dpo-user", "dpo"),
        json={"action": "start_assessment"},
    )
    response = client.post(
        f"/v1/projects/{project_id}/transitions",
        headers=headers("alice", "researcher"),
        json={"action": "request_governance"},
    )
    assert response.status_code == 409


def test_rate_card_estimate() -> None:
    rates = {"vcpu_hour": 1, "gib_hour": 1, "gpu_hour": 1, "storage_gib_month": 1, "vdi_hour": 1, "licence_day": 1}
    line = estimate({"cpu_vcpu": 2, "memory_gib": 4, "gpu_count": 1, "storage_gib": 0, "vdi_hours": 0, "licences": 0}, 3, rates)
    assert line["cpu"] == 6
    assert line["memory"] == 12
    assert line["gpu"] == 3
    assert line["total"] == 21


def test_rate_card_and_estimate_endpoints(client: TestClient) -> None:
    rate_card = client.get("/v1/rate-card").json()
    assert rate_card["currency"] == "GBP"
    assert rate_card["rates"]["vcpu_hour"] > 0

    estimate_response = client.post(
        "/v1/quotes/estimate",
        headers=headers("planner", "project_manager"),
        json={"resources": {"cpu_vcpu": 4, "memory_gib": 16}, "hours": 100},
    )
    assert estimate_response.status_code == 200
    lines = estimate_response.json()["lines"]
    assert lines["total"] > 0
    assert client.get("/v1/quotes").json() == []


def test_global_list_endpoints(client: TestClient) -> None:
    _, project_id = create_study_and_project(client)
    client.post(
        f"/v1/projects/{project_id}/assessments",
        headers=headers("dpo-user", "dpo"),
        json={"answers": assessment_answers()},
    )
    client.post(
        f"/v1/projects/{project_id}/data-requests",
        headers=headers("alice", "researcher"),
        json={
            "direction": "egress",
            "dataset_name": "aggregate-results.csv",
            "classification": "INTERNAL",
            "owner": "alice",
            "criteria": ["no direct identifiers"],
        },
    )
    assert len(client.get("/v1/assessments").json()) == 1
    assert len(client.get("/v1/data-requests").json()) == 1
    assert client.get("/v1/quotes").json() == []
    assert client.get("/v1/ledger-entries").json() == []


def test_assessment_template(client: TestClient) -> None:
    template = client.get("/v1/assessment-template").json()
    assert template["version"] == "2026-11-v1"
    assert len(template["questions"]) == 12
    assert all("key" in question and "options" in question for question in template["questions"])


def test_assessment_score_is_server_side(client: TestClient) -> None:
    _, project_id = create_study_and_project(client)
    response = client.post(
        f"/v1/projects/{project_id}/assessments",
        headers=headers("dpo-user", "dpo"),
        json={
            "answers": assessment_answers(
                data_category="SPECIAL_CATEGORY",
                data_volume="LARGE",
                vulnerable_groups="YES",
                security_controls="NONE",
                legal_basis="NO",
                retention_defined="NO",
                data_owner_assigned="NO",
                egress_review="NO",
            ),
            "risk_score": 0,
            "classification": "PUBLIC",
            "control_tier": "TIER_0",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["risk_score"] == 100
    assert body["classification"] == "RESTRICTED"
    assert body["control_tier"] == "TIER_3"
    assert "DPIA" in " ".join(body["mitigations"])
    assert "retention_schedule" in body["evidence_required"]


def test_assessment_requires_all_answers(client: TestClient) -> None:
    _, project_id = create_study_and_project(client)
    answers = assessment_answers()
    answers.pop("legal_basis")
    response = client.post(
        f"/v1/projects/{project_id}/assessments",
        headers=headers("dpo-user", "dpo"),
        json={"answers": answers},
    )
    assert response.status_code == 400
    assert "legal_basis" in response.json()["detail"]
