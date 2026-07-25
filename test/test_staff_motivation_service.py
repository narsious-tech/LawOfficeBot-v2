from datetime import date, datetime

from services.staff_motivation_service import (
    accountability_snapshot,
    build_staff_motivation,
    daily_quote,
)


TODAY = date(2026, 7, 25)


def task(task_id, due, text="Prepare reply"):
    return {
        "id": task_id,
        "task": text,
        "parsed_deadline": datetime.combine(due, datetime.min.time()),
    }


def test_quote_is_stable_for_same_day_and_staff():
    assert daily_quote(TODAY, "morning", "Preet") == daily_quote(
        TODAY, "morning", "Preet"
    )


def test_empty_board_is_positive():
    message = build_staff_motivation("Priya", [], TODAY, "morning")
    assert "No pending work" in message


def test_due_today_names_task():
    message = build_staff_motivation(
        "Preet", [task(24, TODAY)], TODAY, "evening"
    )
    assert "Due today" in message
    assert "#24" in message


def test_overdue_message_is_firm_and_factual():
    message = build_staff_motivation(
        "Preet",
        [task(24, date(2026, 7, 20)), task(25, date(2026, 7, 24))],
        TODAY,
        "evening",
    )
    assert "Firm reminder" in message
    assert "#24" in message
    assert "#25" in message
    assert "completion status" in message


def test_blocker_suppresses_witty_criticism():
    message = build_staff_motivation(
        "Happy",
        [task(31, date(2026, 7, 20), "Draft blocked — awaiting client instructions")],
        TODAY,
        "evening",
    )
    assert "external dependency" in message
    assert "looking for parking" not in message
    assert "completion status" not in message


def test_snapshot_counts_overdue_and_today():
    snapshot = accountability_snapshot(
        [task(1, date(2026, 7, 24)), task(2, TODAY)],
        TODAY,
    )
    assert len(snapshot["overdue"]) == 1
    assert len(snapshot["due_today"]) == 1
