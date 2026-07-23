import os
import json
import hmac
import hashlib
import urllib.parse
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Flask, request, jsonify, render_template, redirect

import requests
import psycopg2

from config import DATABASE_URL
from advocate_web import AdvocateWeb
from services.whatsapp_cloud import (
    process_webhook as process_whatsapp_webhook,
    verify_challenge as verify_whatsapp_challenge,
    verify_signature as verify_whatsapp_signature,
)


attendance_app = Flask(
    __name__,
    template_folder="templates"
)

@attendance_app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "law-office-attendance"}), 200

@attendance_app.get("/")
def attendance_root():
    return redirect("/attendance-app", code=302)


def _notify_whatsapp_inbound(item):
    destination = OFFICE_GROUP_CHAT_ID or ADMIN_CHAT_ID
    if not BOT_TOKEN or not destination:
        return
    text = (
        "📨 NEW CLIENT WHATSAPP\n\n"
        f"👤 {item.get('name') or 'Unknown contact'}\n"
        f"📱 +{item.get('phone') or '-'}\n"
        f"🔢 Case: {item.get('case_id') or 'Not matched'}\n"
        f"💬 {item.get('text') or '-'}\n\n"
        "Open /whatsappinbox in the bot to review."
    )
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": destination, "text": text},
        timeout=15,
    )


@attendance_app.get("/whatsapp/webhook")
def whatsapp_webhook_verify():
    challenge = verify_whatsapp_challenge(
        request.args.get("hub.mode", ""),
        request.args.get("hub.verify_token", ""),
        request.args.get("hub.challenge", ""),
    )
    return (challenge, 200) if challenge is not None else ("Forbidden", 403)


@attendance_app.post("/whatsapp/webhook")
def whatsapp_webhook_receive():
    raw = request.get_data(cache=True)
    if not verify_whatsapp_signature(
        raw, request.headers.get("X-Hub-Signature-256")
    ):
        return jsonify({"status": "invalid signature"}), 403
    payload = request.get_json(silent=True) or {}
    try:
        alerts = process_whatsapp_webhook(payload)
        for item in alerts:
            try:
                _notify_whatsapp_inbound(item)
            except Exception:
                attendance_app.logger.exception("WhatsApp Telegram alert failed")
        return jsonify({"status": "ok", "new_messages": len(alerts)}), 200
    except Exception:
        attendance_app.logger.exception("WhatsApp webhook processing failed")
        return jsonify({"status": "processing error"}), 500

BOT_TOKEN = os.getenv("TOKEN") or os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
OFFICE_GROUP_CHAT_ID = os.getenv("OFFICE_GROUP_CHAT_ID")

MAX_ACCURACY_METERS = float(
    os.getenv("ATTENDANCE_MAX_ACCURACY_METERS", "100")
)

DUPLICATE_MINUTES = int(
    os.getenv("ATTENDANCE_DUPLICATE_MINUTES", "10")
)

IST = ZoneInfo("Asia/Kolkata")


def now_ist_naive():
    return datetime.now(IST).replace(tzinfo=None)


def distance_in_meters(lat1, lon1, lat2, lon2):
    earth_radius = 6371000
    lat1_rad = math.radians(float(lat1))
    lat2_rad = math.radians(float(lat2))
    delta_lat = math.radians(float(lat2) - float(lat1))
    delta_lon = math.radians(float(lon2) - float(lon1))

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad)
        * math.cos(lat2_rad)
        * math.sin(delta_lon / 2) ** 2
    )

    c = 2 * math.atan2(
        math.sqrt(a),
        math.sqrt(1 - a)
    )

    return earth_radius * c


def verify_telegram_init_data(init_data):
    if not BOT_TOKEN:
        raise Exception("BOT_TOKEN/TOKEN is not configured")

    parsed = dict(
        urllib.parse.parse_qsl(
            init_data,
            keep_blank_values=True
        )
    )

    received_hash = parsed.pop("hash", None)

    if not received_hash:
        return None

    data_check_string = "\n".join(
        f"{key}={value}"
        for key, value in sorted(parsed.items())
    )

    secret_key = hmac.new(
        key=b"WebAppData",
        msg=BOT_TOKEN.encode(),
        digestmod=hashlib.sha256
    ).digest()

    calculated_hash = hmac.new(
        key=secret_key,
        msg=data_check_string.encode(),
        digestmod=hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        return None

    user_json = parsed.get("user")

    if not user_json:
        return None

    return json.loads(user_json)


def send_attendance_notification(text):
    chat_id = OFFICE_GROUP_CHAT_ID or ADMIN_CHAT_ID

    if not chat_id or not BOT_TOKEN:
        return

    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True
            },
            timeout=10
        )
    except Exception as exc:
        print(
            "ATTENDANCE NOTIFICATION FAILED: "
            f"{type(exc).__name__}: {exc}"
        )


def get_nearest_allowed_office(cur, latitude, longitude, action):
    if action == "CHECKIN":
        permission_column = "allow_checkin"

    elif action == "CHECKOUT":
        permission_column = "allow_checkout"

    else:
        # MOVE is permitted to any active attendance office.
        permission_column = None

    if permission_column:
        cur.execute(f"""
            SELECT
                id,
                office_name,
                latitude,
                longitude,
                allowed_radius_meters
            FROM attendance_offices
            WHERE is_active = TRUE
              AND {permission_column} = TRUE
            ORDER BY id ASC
        """)

    else:
        cur.execute("""
            SELECT
                id,
                office_name,
                latitude,
                longitude,
                allowed_radius_meters
            FROM attendance_offices
            WHERE is_active = TRUE
            ORDER BY id ASC
        """)
    offices = cur.fetchall()
    print("\n===== OFFICES FROM DATABASE =====")

    for office in offices:
        print(office)

    print("===============================\n")
    
    if not offices:
        raise Exception(
            "No active attendance office is configured "
            f"for {action.lower()}."
        )

    nearest = None

    for (
        office_id,
        office_name,
        office_latitude,
        office_longitude,
        allowed_radius_meters
    ) in offices:
        distance = distance_in_meters(
            latitude,
            longitude,
            office_latitude,
            office_longitude
        )

        candidate = {
            "id": office_id,
            "office_name": office_name,
            "allowed_radius_meters": int(allowed_radius_meters or 300),
            "distance_meters": float(distance)
        }

        if nearest is None or candidate["distance_meters"] < nearest["distance_meters"]:
            nearest = candidate

    return nearest
    print("\n===== NEAREST OFFICE =====")
    print(nearest)
    print("==========================\n")

def get_attendance_session(cur, telegram_user_id, attendance_date):
    cur.execute("""
        SELECT
            id,
            checkin_time,
            checkin_office_id,
            checkin_office_name,
            checkout_time,
            checkout_office_id,
            checkout_office_name,
            status
        FROM attendance_sessions
        WHERE telegram_user_id = %s
          AND attendance_date = %s
        ORDER BY id DESC
        LIMIT 1
    """, (
        telegram_user_id,
        attendance_date
    ))

    return cur.fetchone()


def save_attendance_location(
    cur,
    *,
    staff_name,
    telegram_user_id,
    action,
    latitude,
    longitude,
    map_link,
    office,
    accuracy_value
):
    cur.execute("""
        INSERT INTO attendance_locations
        (
            staff_name,
            telegram_user_id,
            action,
            latitude,
            longitude,
            map_link,
            office_id,
            office_name,
            distance_meters,
            accuracy_meters
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        staff_name,
        telegram_user_id,
        action,
        str(latitude),
        str(longitude),
        map_link,
        office["id"],
        office["office_name"],
        office["distance_meters"],
        accuracy_value
    ))


@attendance_app.route("/attendance-app")
def attendance_page():
    return render_template("attendance.html")


@attendance_app.route("/api/attendance", methods=["POST"])
def attendance_api():
    data = request.get_json(silent=True) or {}

    init_data = data.get("init_data")
    action = (data.get("action") or "").upper()
    latitude = data.get("latitude")
    longitude = data.get("longitude")
    accuracy = data.get("accuracy")

    if not init_data:
        return jsonify({
            "success": False,
            "message": "Telegram verification data missing."
        }), 400

    verified_user = verify_telegram_init_data(init_data)

    if not verified_user:
        return jsonify({
            "success": False,
            "message": "Telegram verification failed."
        }), 403

    telegram_user_id = verified_user.get("id")

    if not telegram_user_id:
        return jsonify({
            "success": False,
            "message": "Telegram user not identified."
        }), 400

    if action not in [
    "CHECKIN",
    "CHECKOUT",
    "MOVE"
    ]:
        return jsonify({
            "success": False,
            "message": "Invalid attendance action."
        }), 400

    if latitude is None or longitude is None:
        return jsonify({
            "success": False,
            "message": "Location not received."
        }), 400

    try:
        latitude = float(latitude)
        longitude = float(longitude)
        accuracy_value = float(accuracy) if accuracy is not None else None
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "message": "Invalid GPS coordinates."
        }), 400

    if accuracy_value is None:
        return jsonify({
            "success": False,
            "message": "GPS accuracy was not received."
        }), 400

    if accuracy_value > MAX_ACCURACY_METERS:
        return jsonify({
            "success": False,
            "message": (
                f"GPS accuracy is too low: {round(accuracy_value)} metres.\n"
                f"Required accuracy: within {round(MAX_ACCURACY_METERS)} metres."
            )
        }), 400

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                staff_name,
                ad_email,
                ad_password
            FROM staff_accounts
            WHERE telegram_user_id = %s
              AND is_active = TRUE
            LIMIT 1
        """, (
            telegram_user_id,
        ))

        staff = cur.fetchone()

        if not staff:
            return jsonify({
                "success": False,
                "message": "Staff account is not linked."
            }), 403

        staff_name, ad_email, ad_password = staff

        office = get_nearest_allowed_office(
            cur,
            latitude,
            longitude,
            action
        )

        if office["distance_meters"] > office["allowed_radius_meters"]:
            return jsonify({
                "success": False,
                "message": (
                    "You are outside all approved attendance areas.\n"
                    f"Nearest office: {office['office_name']}\n"
                    f"Distance: {round(office['distance_meters'])} metres.\n"
                    f"Allowed radius: {round(office['allowed_radius_meters'])} metres."
                )
            }), 403

        current_time = now_ist_naive()
        attendance_date = current_time.date()
        duplicate_cutoff = current_time - timedelta(minutes=DUPLICATE_MINUTES)

        cur.execute("""
            SELECT id, created_at
            FROM attendance_locations
            WHERE telegram_user_id = %s
              AND action = %s
              AND created_at >= %s
            ORDER BY created_at DESC
            LIMIT 1
        """, (
            telegram_user_id,
            action,
            duplicate_cutoff
        ))

        duplicate = cur.fetchone()

        if duplicate:
            return jsonify({
                "success": False,
                "message": (
                    f"This {action.lower()} was already recorded recently.\n"
                    f"Please wait {DUPLICATE_MINUTES} minutes."
                )
            }), 409

        session = get_attendance_session(
            cur,
            telegram_user_id,
            attendance_date
        )

        session_id = None
        current_office_id = None
        current_office_name = None

        if action == "CHECKIN":
            if session and session[7] == "OPEN" and not session[4]:
                return jsonify({
                    "success": False,
                    "message": (
                        "You are already checked in today.\n"
                        f"Check-in office: {session[3] or '-'}\n"
                        f"Check-in time: "
                        f"{session[1].strftime('%I:%M %p') if session[1] else '-'}"
                    )
                }), 409

        elif action == "CHECKOUT":
            if not session:
                return jsonify({
                    "success": False,
                    "message": (
                        "No check-in was found for today.\n"
                        "Please check in before checking out."
                    )
                }), 409

            if session[7] == "CLOSED" or session[4]:
                return jsonify({
                    "success": False,
                    "message": (
                        "You have already checked out today."
                    )
                }), 409

            session_id = session[0]

        else:
            if not session:
                return jsonify({
                    "success": False,
                    "message": (
                        "No active attendance session was found.\n"
                        "Please check in before moving offices."
                    )
                }), 409

            if session[7] != "OPEN" or session[4]:
                return jsonify({
                    "success": False,
                    "message": (
                        "Your attendance session is already closed."
                    )
                }), 409

            session_id = session[0]

            cur.execute("""
                SELECT
                    COALESCE(
                        current_office_id,
                        checkin_office_id
                    ),
                    COALESCE(
                        current_office_name,
                        checkin_office_name
                    )
                FROM attendance_sessions
                WHERE id = %s
            """, (
                session_id,
            ))

            current_office = cur.fetchone()

            current_office_id = (
                current_office[0]
                if current_office
                else session[2]
            )

            current_office_name = (
                current_office[1]
                if current_office
                else session[3]
            )

            if current_office_id == office["id"]:
                return jsonify({
                    "success": False,
                    "message": (
                        f"You are already marked at "
                        f"{office['office_name']}."
                    )
                }), 409

        map_link = (
            "https://www.google.com/maps?q="
            f"{latitude},{longitude}"
        )

        if action == "MOVE":
            response = None
            action_text = "Office transfer"
            icon = "🔄"

        else:
            staff_web = AdvocateWeb(
                email=ad_email.strip(),
                password=ad_password.strip()
            )

            login_ok, login_result = (
                staff_web.test_login()
            )

            if not login_ok:
                conn.rollback()

                return jsonify({
                    "success": False,
                    "message": (
                        "Advocate Diaries login failed."
                    )
                }), 401

            if action == "CHECKIN":
                response = staff_web.punch_in()
                action_text = "Check-in"
                icon = "🟢"

            else:
                response = staff_web.punch_out()
                action_text = "Check-out"
                icon = "🔴"

        if (
            response is not None
            and response.status_code != 200
        ):
            conn.rollback()

            return jsonify({
                "success": False,
                "message": (
                    f"{action_text} failed. "
                    f"Status: {response.status_code}"
                )
            }), 500

        save_attendance_location(
            cur,
            staff_name=staff_name,
            telegram_user_id=telegram_user_id,
            action=action,
            latitude=latitude,
            longitude=longitude,
            map_link=map_link,
            office=office,
            accuracy_value=accuracy_value
        )

        working_minutes = None

        if action == "MOVE":
            cur.execute("""
                INSERT INTO attendance_movements
                (
                    attendance_session_id,
                    telegram_user_id,
                    staff_name,
                    from_office_id,
                    from_office_name,
                    to_office_id,
                    to_office_name,
                    latitude,
                    longitude,
                    accuracy_meters,
                    distance_meters,
                    map_link,
                    moved_at
                )
                VALUES (
                    %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s, %s
                )
            """, (
                session_id,
                telegram_user_id,
                staff_name,
                current_office_id,
                current_office_name,
                office["id"],
                office["office_name"],
                latitude,
                longitude,
                accuracy_value,
                office["distance_meters"],
                map_link,
                current_time
            ))

            cur.execute("""
                UPDATE attendance_sessions
                SET
                    current_office_id = %s,
                    current_office_name = %s,
                    updated_at = %s
                WHERE id = %s
            """, (
                office["id"],
                office["office_name"],
                current_time,
                session_id
            ))

        elif action == "CHECKIN":
            cur.execute("""
                INSERT INTO attendance_sessions
                (
                    telegram_user_id,
                    staff_name,
                    attendance_date,
                    checkin_time,
                    checkin_office_id,
                    checkin_office_name,
                    checkout_time,
                    checkout_office_id,
                    checkout_office_name,
                    status,
                    working_minutes,
                    current_office_id,
                    current_office_name,
                    created_at,
                    updated_at
                )
                VALUES (
                    %s, %s, %s,
                    %s, %s, %s,
                    NULL, NULL, NULL,
                    'OPEN', NULL,
                    %s, %s,
                    %s, %s
                )
                ON CONFLICT (
                    telegram_user_id,
                    attendance_date
                )
                DO UPDATE SET
                    staff_name =
                        EXCLUDED.staff_name,
                    checkin_time =
                        EXCLUDED.checkin_time,
                    checkin_office_id =
                        EXCLUDED.checkin_office_id,
                    checkin_office_name =
                        EXCLUDED.checkin_office_name,
                    checkout_time = NULL,
                    checkout_office_id = NULL,
                    checkout_office_name = NULL,
                    status = 'OPEN',
                    working_minutes = NULL,
                    current_office_id =
                        EXCLUDED.current_office_id,
                    current_office_name =
                        EXCLUDED.current_office_name,
                    updated_at =
                        EXCLUDED.updated_at
            """, (
                telegram_user_id,
                staff_name,
                attendance_date,
                current_time,
                office["id"],
                office["office_name"],
                office["id"],
                office["office_name"],
                current_time,
                current_time
            ))

        else:
            checkin_time = session[1]

            if not checkin_time:
                raise Exception(
                    "Attendance session has no check-in time."
                )

            working_minutes = max(
                0,
                int(
                    (
                        current_time
                        - checkin_time
                    ).total_seconds()
                    // 60
                )
            )

            cur.execute("""
                UPDATE attendance_sessions
                SET
                    checkout_time = %s,
                    checkout_office_id = %s,
                    checkout_office_name = %s,
                    current_office_id = %s,
                    current_office_name = %s,
                    status = 'CLOSED',
                    working_minutes = %s,
                    updated_at = %s
                WHERE id = %s
            """, (
                current_time,
                office["id"],
                office["office_name"],
                office["id"],
                office["office_name"],
                working_minutes,
                current_time,
                session_id
            ))

        conn.commit()

        if action == "MOVE":
            notification = (
                "🔄 STAFF OFFICE TRANSFER\n\n"
                f"👤 Staff: {staff_name}\n"
                f"🏢 From: "
                f"{current_office_name or '-'}\n"
                f"🏢 To: "
                f"{office['office_name']}\n"
                f"📍 Location recorded\n"
                f"🎯 Accuracy: "
                f"{round(accuracy_value)} metres\n"
                f"📏 Distance from destination: "
                f"{round(office['distance_meters'])} metres\n"
                f"🗺 Map: {map_link}"
            )

            success_message = (
                "Office transfer recorded successfully.\n"
                f"Current office: "
                f"{office['office_name']}."
            )

        else:
            notification = (
                f"{icon} STAFF {action_text.upper()}\n\n"
                f"👤 Staff: {staff_name}\n"
                f"🏢 Office: "
                f"{office['office_name']}\n"
                f"📍 Location recorded\n"
                f"🎯 Accuracy: "
                f"{round(accuracy_value)} metres\n"
                f"📏 Distance from office: "
                f"{round(office['distance_meters'])} metres\n"
            )

            if (
                action == "CHECKOUT"
                and working_minutes is not None
            ):
                hours = working_minutes // 60
                minutes = working_minutes % 60

                notification += (
                    f"⏱ Working time: "
                    f"{hours}h {minutes}m\n"
                )

            notification += (
                f"🗺 Map: {map_link}"
            )

            success_message = (
                f"{action_text} completed successfully "
                f"at {office['office_name']}."
            )

        send_attendance_notification(
            notification
        )

        payload = {
            "success": True,
            "staff_name": staff_name,
            "action": action_text,
            "office_name": office[
                "office_name"
            ],
            "map_link": map_link,
            "accuracy": round(
                accuracy_value
            ),
            "distance_from_office": round(
                office["distance_meters"]
            ),
            "message": success_message
        }

        if working_minutes is not None:
            payload[
                "working_minutes"
            ] = working_minutes

        if action == "MOVE":
            payload[
                "from_office_name"
            ] = current_office_name

            payload[
                "to_office_name"
            ] = office["office_name"]

        return jsonify(
            payload
        )

    except Exception as exc:
        conn.rollback()
        return jsonify({
            "success": False,
            "message": f"{type(exc).__name__}: {exc}"
        }), 500

    finally:
        cur.close()
        conn.close()


def run_attendance_app():
    port = int(os.getenv("PORT", "8080"))

    attendance_app.run(
        host="0.0.0.0",
        port=port,
        threaded=True,
        use_reloader=False
    )
