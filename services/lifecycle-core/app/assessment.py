from __future__ import annotations

from typing import Any

from fastapi import HTTPException

QUESTIONNAIRE_VERSION = "2026-11-v1"

QUESTIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "data_category",
        "prompt": "What is the highest sensitivity of data in scope?",
        "type": "single",
        "options": ["ANONYMISED", "AGGREGATE", "PERSONAL", "SPECIAL_CATEGORY"],
    },
    {
        "key": "data_volume",
        "prompt": "What volume of records will be processed?",
        "type": "single",
        "options": ["SMALL", "MEDIUM", "LARGE"],
    },
    {
        "key": "vulnerable_groups",
        "prompt": "Does the project involve vulnerable participants or patients?",
        "type": "boolean",
        "options": ["YES", "NO"],
    },
    {
        "key": "systematic_monitoring",
        "prompt": "Does the project involve systematic monitoring or observation?",
        "type": "boolean",
        "options": ["YES", "NO"],
    },
    {
        "key": "data_matching",
        "prompt": "Will datasets be linked or matched across sources?",
        "type": "boolean",
        "options": ["YES", "NO"],
    },
    {
        "key": "new_technology",
        "prompt": "Does the project use novel or unproven technology?",
        "type": "boolean",
        "options": ["YES", "NO"],
    },
    {
        "key": "legal_basis",
        "prompt": "Is a lawful basis and purpose limitation documented?",
        "type": "boolean",
        "options": ["YES", "NO"],
    },
    {
        "key": "international_transfer",
        "prompt": "Will personal data leave the TRE or UK/EEA?",
        "type": "boolean",
        "options": ["YES", "NO"],
    },
    {
        "key": "retention_defined",
        "prompt": "Is a retention and deletion schedule defined?",
        "type": "boolean",
        "options": ["YES", "NO"],
    },
    {
        "key": "security_controls",
        "prompt": "Which baseline security controls are in place?",
        "type": "single",
        "options": ["NONE", "BASIC", "ENHANCED"],
    },
    {
        "key": "data_owner_assigned",
        "prompt": "Is a named data owner assigned?",
        "type": "boolean",
        "options": ["YES", "NO"],
    },
    {
        "key": "egress_review",
        "prompt": "Will every egress be reviewed against a release checklist?",
        "type": "boolean",
        "options": ["YES", "NO"],
    },
)

_OPTIONS = {question["key"]: set(question["options"]) for question in QUESTIONS}

_SCORES: dict[str, dict[str, int]] = {
    "data_category": {"ANONYMISED": 0, "AGGREGATE": 5, "PERSONAL": 15, "SPECIAL_CATEGORY": 30},
    "data_volume": {"SMALL": 0, "MEDIUM": 5, "LARGE": 10},
    "vulnerable_groups": {"YES": 10, "NO": 0},
    "systematic_monitoring": {"YES": 10, "NO": 0},
    "data_matching": {"YES": 8, "NO": 0},
    "new_technology": {"YES": 5, "NO": 0},
    "legal_basis": {"YES": 0, "NO": 15},
    "international_transfer": {"YES": 8, "NO": 0},
    "retention_defined": {"YES": 0, "NO": 8},
    "security_controls": {"NONE": 15, "BASIC": 5, "ENHANCED": 0},
    "data_owner_assigned": {"YES": 0, "NO": 8},
    "egress_review": {"YES": 0, "NO": 8},
}


def _yes(answers: dict[str, str], key: str) -> bool:
    return str(answers.get(key, "")).upper() == "YES"


def template() -> dict[str, Any]:
    return {"version": QUESTIONNAIRE_VERSION, "questions": list(QUESTIONS)}


def evaluate(version: str, answers: dict[str, Any]) -> dict[str, Any]:
    if version != QUESTIONNAIRE_VERSION:
        raise HTTPException(status_code=400, detail=f"Unsupported assessment version: {version}")

    normalised: dict[str, str] = {}
    for question in QUESTIONS:
        key = question["key"]
        value = answers.get(key)
        if value is None:
            raise HTTPException(status_code=400, detail=f"Missing assessment answer: {key}")
        value = str(value).upper()
        if value not in _OPTIONS[key]:
            raise HTTPException(status_code=400, detail=f"Invalid answer for {key}: {value}")
        normalised[key] = value

    score = min(100, sum(_SCORES[key][value] for key, value in normalised.items()))
    if score >= 65:
        classification, control_tier = "RESTRICTED", "TIER_3"
    elif score >= 40:
        classification, control_tier = "CONFIDENTIAL", "TIER_2"
    elif score >= 15:
        classification, control_tier = "INTERNAL", "TIER_1"
    else:
        classification, control_tier = "PUBLIC", "TIER_0"

    mitigations: list[str] = []
    evidence: list[str] = []
    if normalised["data_category"] in {"PERSONAL", "SPECIAL_CATEGORY"}:
        mitigations.append("Complete a DPIA and record the lawful basis before ingress.")
        evidence.append("dpia")
    if normalised["data_category"] == "SPECIAL_CATEGORY":
        mitigations.append("Obtain explicit data owner and DPO approval for special-category data.")
        evidence.append("data_owner_approval")
    if _yes(normalised, "vulnerable_groups"):
        mitigations.append("Apply enhanced minimisation and restrict the project cohort.")
    if _yes(normalised, "systematic_monitoring") or _yes(normalised, "data_matching"):
        mitigations.append("Document proportionality and prohibit unapproved record linkage.")
    if normalised["new_technology"] == "YES":
        mitigations.append("Run a threat model and operator review before activation.")
    if normalised["legal_basis"] == "NO":
        mitigations.append("Record purpose limitation and lawful basis in the study record.")
    if _yes(normalised, "international_transfer"):
        mitigations.append("Keep data inside the TRE; any transfer requires a separate governance decision.")
    if normalised["retention_defined"] == "NO":
        mitigations.append("Define a retention deadline and deletion evidence before closure.")
        evidence.append("retention_schedule")
    if normalised["security_controls"] != "ENHANCED":
        mitigations.append("Enable MFA, encryption at rest, namespace isolation and least-privilege RBAC.")
        evidence.append("security_controls")
    if normalised["data_owner_assigned"] == "NO":
        mitigations.append("Assign a named data owner before data ingress.")
        evidence.append("data_owner_approval")
    if normalised["egress_review"] == "NO":
        mitigations.append("Require an egress review checklist for every release.")
        evidence.append("egress_review")

    return {
        "risk_score": score,
        "classification": classification,
        "control_tier": control_tier,
        "mitigations": mitigations,
        "evidence_required": sorted(set(evidence)),
        "answers": normalised,
    }
