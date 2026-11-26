import pytest
from fastapi import HTTPException

from app.state_machine import assert_transition, next_state, transition_for_action


def test_valid_transition() -> None:
    transition = assert_transition("DRAFT", "submit", ("researcher",))
    assert transition.target == "SUBMITTED"


def test_wrong_role_is_rejected() -> None:
    with pytest.raises(HTTPException) as exc:
        assert_transition("DRAFT", "submit", ("dpo",))
    assert exc.value.status_code == 403


def test_wrong_state_is_rejected() -> None:
    with pytest.raises(HTTPException) as exc:
        assert_transition("ACTIVE", "submit", ("researcher",))
    assert exc.value.status_code == 409


def test_next_state_and_lookup() -> None:
    assert next_state("PROVISIONING") == "ACTIVE"
    assert transition_for_action("suspend").source == "ACTIVE"
