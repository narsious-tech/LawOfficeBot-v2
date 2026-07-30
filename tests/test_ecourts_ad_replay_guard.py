from datetime import date

from services.ecourts_orchestration_service import _ad_write_safety_error


def test_admin_acceptance_is_required():
    result = _ad_write_safety_error(
        next_date=date(2099, 1, 1),
        review_decision=None,
        verification_status=None,
    )
    assert result and result[0] == "BLOCKED_NOT_APPROVED"


def test_historical_next_date_is_rejected():
    result = _ad_write_safety_error(
        next_date=date(2020, 1, 1),
        review_decision="ACCEPT_ECOURTS",
        verification_status="ECOURTS_ACCEPTED",
    )
    assert result and result[0] == "HISTORICAL_SKIPPED"


def test_future_accepted_date_is_allowed():
    result = _ad_write_safety_error(
        next_date=date(2099, 1, 1),
        review_decision="ACCEPT_ECOURTS",
        verification_status="ECOURTS_ACCEPTED",
    )
    assert result is None


def test_disposed_case_is_never_written_to_advocate_diaries():
    result = _ad_write_safety_error(
        next_date=date(2099, 1, 1),
        review_decision="ACCEPT_ECOURTS",
        verification_status="ECOURTS_ACCEPTED",
        case_status="Disposed",
    )
    assert result and result[0] == "DISPOSED_CASE_SKIPPED"


def test_active_case_remains_eligible():
    result = _ad_write_safety_error(
        next_date=date(2099, 1, 1),
        review_decision="ACCEPT_ECOURTS",
        verification_status="ECOURTS_ACCEPTED",
        case_status="Pending",
    )
    assert result is None
