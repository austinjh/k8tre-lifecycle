from __future__ import annotations

from dataclasses import dataclass
from fastapi import HTTPException


@dataclass(frozen=True)
class Transition:
    action: str
    source: str
    target: str
    roles: tuple[str, ...]


TRANSITIONS: tuple[Transition, ...] = (
    Transition("submit", "DRAFT", "SUBMITTED", ("researcher", "project_manager")),
    Transition("start_assessment", "SUBMITTED", "ASSESSING", ("dpo", "tre_operator")),
    Transition("request_governance", "ASSESSING", "GOVERNANCE_REVIEW", ("researcher", "project_manager")),
    Transition("approve_governance", "GOVERNANCE_REVIEW", "COSTED", ("dpo", "data_owner")),
    Transition("approve_budget", "COSTED", "BUDGET_APPROVED", ("finance",)),
    Transition("provision", "BUDGET_APPROVED", "PROVISIONING", ("tre_operator",)),
    Transition("activate", "PROVISIONING", "ACTIVE", ("tre_operator",)),
    Transition("suspend", "ACTIVE", "SUSPENDED", ("tre_operator",)),
    Transition("resume", "SUSPENDED", "ACTIVE", ("tre_operator",)),
    Transition("close", "ACTIVE", "CLOSED", ("project_manager", "tre_operator")),
    Transition("archive", "CLOSED", "ARCHIVED", ("tre_operator",)),
)

_BY_ACTION = {t.action: t for t in TRANSITIONS}
_BY_STATE = {t.source: t for t in TRANSITIONS}


def transition_for_action(action: str) -> Transition:
    transition = _BY_ACTION.get(action)
    if not transition:
        raise HTTPException(status_code=400, detail=f"Unknown lifecycle action: {action}")
    return transition


def assert_transition(state: str, action: str, roles: tuple[str, ...]) -> Transition:
    """Validate that the action is legal in the current state and role set."""
    transition = transition_for_action(action)
    if transition.source != state:
        raise HTTPException(
            status_code=409,
            detail=f"Action {action} is not valid in state {state}; expected {transition.source}",
        )
    if not set(transition.roles).intersection(roles):
        raise HTTPException(
            status_code=403,
            detail=f"Action {action} requires one of roles: {', '.join(transition.roles)}",
        )
    return transition


def next_state(state: str) -> str | None:
    transition = _BY_STATE.get(state)
    return transition.target if transition else None
