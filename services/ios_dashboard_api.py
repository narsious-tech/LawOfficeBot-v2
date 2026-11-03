"""Authenticated, read-only API used by Ajay's iPhone Office Dashboard shortcut."""
from __future__ import annotations

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
    expected = os.getenv("IOS_DASHBOARD_TOKEN", "").strip()
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
        blueprint.logger.exception("iOS owner authorization failed")
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
        blueprint.logger.exception("iOS heavy-date forecast failed")
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
        blueprint.logger.exception("iOS heavy-date detail failed")
        return jsonify({"error": "temporarily_unavailable"}), 503

