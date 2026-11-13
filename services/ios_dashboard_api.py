"""Authenticated, read-only API used by Ajay's iPhone Office Dashboard shortcut."""
from __future__ import annotations

import logging
import hmac
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request

from services.role_intelligence_service import role_dashboard


blueprint = Blueprint("ios_dashboard", __name__)
IST = ZoneInfo("Asia/Kolkata")
OWNER_ROLES = {"admin", "owner", "principal"}


def _authorize() -> tuple[dict | None, tuple | None]:
    expected = (os.getenv("IOS_DASHBOARD_TOKEN") or os.getenv("IOS_OFFICE_DASHBOARD_TOKEN") or "").strip()
    admin_id = os.getenv("ADMIN_USER_ID", "").strip()
    supplied = request.headers.get("Authorization", "")
    if not expected or not admin_id:
        return None, (jsonify({"error": "not_configured"}), 503)
    if not supplied.startswith("Bearer ") or not hmac.compare_digest(supplied[7:], expected):
        return None, (jsonify({"error": "unauthorized"}), 401)
    try:
        dashboard = role_dashboard(int(admin_id))
        profile = dashboard.get("profile") or {}
        role = str(profile.get("role") or "").strip().casefold()
        if not profile.get("is_active", True) or role not in OWNER_ROLES:
            return None, (jsonify({"error": "forbidden"}), 403)
        return dashboard, None
    except Exception:
        logging.getLogger(__name__).exception("iOS owner authorization failed")
        return None, (jsonify({"error": "temporarily_unavailable"}), 503)


@blueprint.get("/api/ios/dashboard")
def ios_dashboard():
    dashboard, error = _authorize()
    if error:
        return error
    profile = dashboard["profile"]
    works, files = dashboard["works"], dashboard["files"]
    return jsonify({
        "generated_at": datetime.now(IST).isoformat(),
        "staff": profile.get("staff_name"),
        "works": {key: int(works.get(key) or 0) for key in ("pending", "due_today", "overdue")},
        "files": {key: int(files.get(key) or 0) for key in ("pending", "brought", "not_found", "attention")},
        "pending_hearing_updates": int(dashboard.get("pending_updates") or 0),
        "heavy_dates_url": "/api/ios/heavy-dates",
    }), 200


@blueprint.get("/api/ios/heavy-dates")
def ios_heavy_dates():
    _, error = _authorize()
    if error:
        return error
    from services.heavy_dates_service import get_heavy_dates, heavy_date_settings, render_heavy_dates

    settings = heavy_date_settings()
    raw_days = str(request.args.get("days") or settings["lookahead_days"]).strip()
    try:
        days = max(1, min(365, int(raw_days)))
    except ValueError:
        return jsonify({"error": "invalid_days", "message": "Days must be from 1 to 365."}), 400
    try:
        items = get_heavy_dates(lookahead_days=days)
        text = render_heavy_dates(items, lookahead_days=days)
        return jsonify({
            "generated_at": datetime.now(IST).isoformat(),
            "forecast_days": days,
            "heavy_date_count": len(items),
            "display_text": text,
            "dates": [
                {
                    "date": item["date"].isoformat(),
                    "display_date": item["date"].strftime("%d-%m-%Y"),
                    "severity": item["severity"],
                    "unique_matters": item["total"],
                    "purpose_missing": item.get("missing_purpose", 0),
                }
                for item in items
            ],
        }), 200
    except Exception:
        logging.getLogger(__name__).exception("iOS heavy-date forecast failed")
        return jsonify({"error": "temporarily_unavailable"}), 503


@blueprint.get("/api/ios/heavy-date")
def ios_heavy_date_detail():
    _, error = _authorize()
    if error:
        return error
    from services.heavy_dates_service import (
        get_heavy_dates, parse_hearing_date, render_heavy_date_detail,
    )

    target = parse_hearing_date(request.args.get("date"))
    if not target:
        return jsonify({"error": "invalid_date", "message": "Use DD-MM-YYYY."}), 400
    today = datetime.now(IST).date()
    if target < today or (target - today).days > 365:
        return jsonify({"error": "date_out_of_range"}), 400
    try:
        days = max(45, (target - today).days + 1)
        items = get_heavy_dates(lookahead_days=days)
        item = next((row for row in items if row["date"] == target), None)
        if not item:
            return jsonify({
                "error": "not_heavy",
                "display_text": "No heavy hearing load is detected for this date.",
            }), 404
        return jsonify({
            "generated_at": datetime.now(IST).isoformat(),
            "date": target.isoformat(),
            "unique_matters": item["total"],
            "purpose_missing": item.get("missing_purpose", 0),
            "display_text": render_heavy_date_detail(item),
        }), 200
    except Exception:
        logging.getLogger(__name__).exception("iOS heavy-date detail failed")
        return jsonify({"error": "temporarily_unavailable"}), 503



# Compatibility routes for the existing Law Office Command Centre shortcuts.
def _shortcut_response(text):
    return jsonify({"summary": text, "display_text": text,
                    "generated_at": datetime.now(IST).isoformat()}), 200


def _office_text(dashboard):
    works, files = dashboard["works"], dashboard["files"]
    return (f"🏢 OFFICE STATUS\nStaff: {dashboard['profile'].get('staff_name', 'Ajay')}"
            f"\n\n📝 WORK\nPending: {works.get('pending', 0)}"
            f"\nDue today: {works.get('due_today', 0)}\nOverdue: {works.get('overdue', 0)}"
            f"\n\n📁 FILES\nSelected/Pending: {files.get('pending', 0)}"
            f"\nBrought: {files.get('brought', 0)}\nNot found: {files.get('not_found', 0)}"
            f"\nNeeds attention: {files.get('attention', 0)}"
            f"\n\nPending hearing updates: {dashboard.get('pending_updates', 0)}")


@blueprint.get("/api/ios/office-dashboard")
@blueprint.get("/api/ios/office-status")
def ios_office_status():
    dashboard, error = _authorize()
    if error:
        return error
    return _shortcut_response(_office_text(dashboard))


@blueprint.get("/api/ios/morning-dashboard")
def ios_morning_dashboard():
    _, error = _authorize()
    if error:
        return error
    try:
        from services.whatsapp_dashboard_service import owner_morning_dashboard
        return _shortcut_response(owner_morning_dashboard())
    except Exception:
        logging.getLogger(__name__).exception("iOS morning dashboard failed")
        return jsonify({"error": "temporarily_unavailable", "summary": "Morning dashboard temporarily unavailable."}), 503


@blueprint.get("/api/ios/who-in-office")
def ios_who_in_office():
    _, error = _authorize()
    if error:
        return error
    try:
        from services.whatsapp_owner_companion import _owner_attendance
        from services.role_intelligence_service import _connect
        from psycopg2.extras import RealDictCursor
        with _connect() as con, con.cursor(cursor_factory=RealDictCursor) as cur:
            return _shortcut_response(_owner_attendance(cur))
    except Exception:
        logging.getLogger(__name__).exception("iOS attendance failed")
        return jsonify({"error": "temporarily_unavailable", "summary": "Attendance temporarily unavailable."}), 503


@blueprint.get("/api/ios/deadlines")
@blueprint.get("/api/ios/work-deadlines")
def ios_deadlines():
    dashboard, error = _authorize()
    if error:
        return error
    works = dashboard["works"]
    return _shortcut_response(
        f"⏰ WORK DEADLINES\nDue today: {works.get('due_today', 0)}"
        f"\nOverdue: {works.get('overdue', 0)}\nPending: {works.get('pending', 0)}")
