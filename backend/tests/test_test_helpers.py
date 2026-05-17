import pytest

from tests.helpers.assertions import assert_no_forbidden_investment_vocabulary


def test_forbidden_vocabulary_helper_allows_neutral_payload() -> None:
    assert_no_forbidden_investment_vocabulary(
        {
            "status": "completed",
            "message": "strategy variant diagnostics",
        }
    )


def test_forbidden_vocabulary_helper_catches_english_term() -> None:
    with pytest.raises(AssertionError):
        assert_no_forbidden_investment_vocabulary({"message": "buy"})


def test_forbidden_vocabulary_helper_catches_russian_term() -> None:
    with pytest.raises(AssertionError):
        assert_no_forbidden_investment_vocabulary({"message": "покупать"})


def test_forbidden_vocabulary_helper_catches_project_banned_term() -> None:
    with pytest.raises(AssertionError):
        assert_no_forbidden_investment_vocabulary({"message": "threshold"})
