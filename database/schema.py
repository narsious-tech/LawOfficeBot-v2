"""Idempotent database bootstrap for Law Office Bot v2.

The SQL in this module was extracted from the original 4,000-line bot.py.
Running it repeatedly is safe because the original CREATE/ALTER statements are
idempotent.
"""

from __future__ import annotations

import logging
import os

import psycopg2

from config import DATABASE_URL

logger = logging.getLogger(__name__)


def initialize_database() -> None:
    """Create/upgrade required tables, indexes, office records and staff seeds."""
    logger.info("Starting database initialization")
    conn = psycopg2.connect(
        DATABASE_URL,
        connect_timeout=15,
        application_name="law-office-bot-v2-schema",
    )
    cur = conn.cursor()

    try:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS cases (
            id SERIAL PRIMARY KEY,
            case_id TEXT,
            client_name TEXT,
            mobile TEXT,
            case_type TEXT,
            court_name TEXT,
            opposite_party TEXT,
            hearing_date TEXT,
            fee_agreed TEXT,
            advance_received TEXT
        )
        """)

        cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS court_name TEXT")
        cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS opposite_party TEXT")
        cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS hearing_date TEXT")
        cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS fee_agreed TEXT")
        cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS advance_received TEXT")
        cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'OPEN'")
        cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS notes TEXT")
        cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS client_id INTEGER")
        cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS ad_client_id TEXT")
        cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS client_verification_status TEXT DEFAULT 'NOT_SENT'")
        cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS client_verification_sent_at TIMESTAMP")
        cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS client_verified_at TIMESTAMP")
        cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS client_correction_note TEXT")

        conn.commit()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS staff (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE,
            role TEXT
        )
        """)

        cur.execute("ALTER TABLE staff ADD COLUMN IF NOT EXISTS attendance TEXT")
        conn.commit()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id SERIAL PRIMARY KEY,
            staff_name TEXT,
            date TEXT,
            status TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS attendance_notifications (
            attendance_id TEXT PRIMARY KEY,
            staff_name TEXT,
            attendance_date TEXT,
            in_time TEXT,
            out_time TEXT,
            approval_status TEXT,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY,
            case_number TEXT,
            assigned_to TEXT,
            task TEXT,
            deadline TEXT,
            status TEXT DEFAULT 'PENDING'
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS fee_installments (
            id SERIAL PRIMARY KEY,
            case_number TEXT,
            amount TEXT,
            date TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS case_responsibility (
            id SERIAL PRIMARY KEY,
            case_number TEXT,
            staff_name TEXT,
            responsibility TEXT
        )
        """)

        cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS drive_folder_id TEXT")
        cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS drive_folder_link TEXT")
        conn.commit()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS staff_accounts (
            telegram_user_id BIGINT PRIMARY KEY,
            staff_name TEXT,
            ad_email TEXT NOT NULL,
            ad_password TEXT NOT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()
        staff_data = [
            ("Preet", "Office Manager / Law Student"),
            ("Happy", "Final Year Law Student"),
            ("Priya", "Personal Assistant"),
            ("Jimmy", "Clerk")
        ]
        for s in staff_data:
            cur.execute(
                "INSERT INTO staff (name, role) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                s
            )

        cur.execute("""
        CREATE TABLE IF NOT EXISTS attendance_locations (
            id SERIAL PRIMARY KEY,
            staff_name TEXT,
            telegram_user_id BIGINT,
            action TEXT,
            latitude TEXT,
            longitude TEXT,
            map_link TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS attendance_offices (
            id SERIAL PRIMARY KEY,
            office_name TEXT NOT NULL UNIQUE,
            latitude DOUBLE PRECISION NOT NULL,
            longitude DOUBLE PRECISION NOT NULL,
            allowed_radius_meters INTEGER DEFAULT 300,
            allow_checkin BOOLEAN DEFAULT TRUE,
            allow_checkout BOOLEAN DEFAULT TRUE,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        cur.execute("""
        ALTER TABLE attendance_locations
        ADD COLUMN IF NOT EXISTS office_id INTEGER
        """)

        cur.execute("""
        ALTER TABLE attendance_locations
        ADD COLUMN IF NOT EXISTS office_name TEXT
        """)

        cur.execute("""
        ALTER TABLE attendance_locations
        ADD COLUMN IF NOT EXISTS distance_meters DOUBLE PRECISION
        """)

        cur.execute("""
        ALTER TABLE attendance_locations
        ADD COLUMN IF NOT EXISTS accuracy_meters DOUBLE PRECISION
        """)
        cur.execute("""
        INSERT INTO attendance_offices
        (
            office_name,
            latitude,
            longitude,
            allowed_radius_meters,
            allow_checkin,
            allow_checkout,
            is_active
        )
        VALUES (%s, %s, %s, %s, TRUE, TRUE, TRUE)
        ON CONFLICT (office_name)
        DO UPDATE SET
            latitude = EXCLUDED.latitude,
            longitude = EXCLUDED.longitude,
            allowed_radius_meters =
                EXCLUDED.allowed_radius_meters,
            allow_checkin = TRUE,
            allow_checkout = TRUE,
            is_active = TRUE
        """, (
            "Court Chamber Office",
            30.8999606,
            75.8346954,
            300
        ))
        cur.execute("""
        INSERT INTO attendance_offices
        (
            office_name,
            latitude,
            longitude,
            allowed_radius_meters,
            allow_checkin,
            allow_checkout,
            is_active
        )
        VALUES (%s, %s, %s, %s, TRUE, TRUE, TRUE)
        ON CONFLICT (office_name)
        DO UPDATE SET
            latitude = EXCLUDED.latitude,
            longitude = EXCLUDED.longitude,
            allowed_radius_meters =
                EXCLUDED.allowed_radius_meters,
            allow_checkin = TRUE,
            allow_checkout = TRUE,
            is_active = TRUE
        """, (
            "Evening Office",
            30.913241,
            75.838635,
            300
        ))
        conn.commit()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS attendance_sessions (
            id SERIAL PRIMARY KEY,

            telegram_user_id BIGINT NOT NULL,
            staff_name TEXT NOT NULL,
            attendance_date DATE NOT NULL,

            checkin_time TIMESTAMP,
            checkin_office_id INTEGER,
            checkin_office_name TEXT,

            checkout_time TIMESTAMP,
            checkout_office_id INTEGER,
            checkout_office_name TEXT,

            status TEXT DEFAULT 'OPEN',
            working_minutes INTEGER,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS
        attendance_sessions_user_date_idx
        ON attendance_sessions
        (
            telegram_user_id,
            attendance_date
        )
        """)
        conn.commit()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS attendance_movements (
            id SERIAL PRIMARY KEY,
            attendance_session_id INTEGER,
            telegram_user_id BIGINT NOT NULL,
            staff_name TEXT NOT NULL,

            from_office_id INTEGER,
            from_office_name TEXT,

            to_office_id INTEGER NOT NULL,
            to_office_name TEXT NOT NULL,

            latitude DOUBLE PRECISION,
            longitude DOUBLE PRECISION,
            accuracy_meters DOUBLE PRECISION,
            distance_meters DOUBLE PRECISION,
            map_link TEXT,

            moved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        cur.execute("""
        ALTER TABLE attendance_sessions
        ADD COLUMN IF NOT EXISTS current_office_id INTEGER
        """)

        cur.execute("""
        ALTER TABLE attendance_sessions
        ADD COLUMN IF NOT EXISTS current_office_name TEXT
        """)

        conn.commit()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS case_files (
            id SERIAL PRIMARY KEY,
            case_id TEXT,
            file_name TEXT,
            drive_file_id TEXT,
            drive_file_link TEXT,
            uploaded_by BIGINT,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS office_profile (
            id SERIAL PRIMARY KEY,
            office_name TEXT NOT NULL,
            office_whatsapp TEXT,
            office_phone TEXT,
            office_email TEXT,
            court_office_address TEXT,
            evening_office_address TEXT,
            office_hours TEXT,
            website TEXT,
            court_maps_link TEXT,
            evening_maps_link TEXT,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS
        office_profile_one_active_idx
        ON office_profile ((is_active))
        WHERE is_active = TRUE
        """)

        cur.execute("""
        INSERT INTO office_profile
        (
            office_name,
            office_whatsapp,
            office_phone,
            office_email,
            court_office_address,
            evening_office_address,
            office_hours,
            website,
            court_maps_link,
            evening_maps_link,
            is_active
        )
        SELECT
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE
        WHERE NOT EXISTS (
            SELECT 1
            FROM office_profile
            WHERE is_active = TRUE
        )
        """, (
            os.getenv("OFFICE_NAME", "Law Office of Ajay Chawla"),
            os.getenv("OFFICE_WHATSAPP_NUMBER"),
            os.getenv("OFFICE_PHONE_NUMBER"),
            os.getenv("OFFICE_EMAIL"),
            os.getenv("COURT_OFFICE_ADDRESS", "District Courts, Ludhiana"),
            os.getenv("EVENING_OFFICE_ADDRESS"),
            os.getenv("OFFICE_HOURS", "Monday-Saturday, 9:30 AM-6:30 PM"),
            os.getenv("OFFICE_WEBSITE"),
            os.getenv("COURT_OFFICE_MAPS_LINK"),
            os.getenv("EVENING_OFFICE_MAPS_LINK")
        ))

        cur.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id SERIAL PRIMARY KEY,
            ad_client_id TEXT UNIQUE,
            client_name TEXT NOT NULL,
            mobile TEXT,
            whatsapp_number TEXT,
            email TEXT,
            address TEXT,
            verification_status TEXT DEFAULT 'NOT_SENT',
            verification_sent_at TIMESTAMP,
            verified_at TIMESTAMP,
            correction_note TEXT,
            ad_sync_status TEXT DEFAULT 'PENDING',
            ad_synced_at TIMESTAMP,
            ad_sync_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        cur.execute("""
        CREATE INDEX IF NOT EXISTS clients_mobile_idx
        ON clients (mobile)
        """)

        cur.execute("""
        CREATE INDEX IF NOT EXISTS clients_name_idx
        ON clients (LOWER(TRIM(client_name)))
        """)

        # Legacy table is retained only for backward compatibility with older commands.
        cur.execute("""
        CREATE TABLE IF NOT EXISTS client_contacts (
            id SERIAL PRIMARY KEY,
            case_id TEXT NOT NULL,
            client_name TEXT,
            whatsapp_number TEXT NOT NULL,
            consent_status TEXT DEFAULT 'UNKNOWN',
            is_primary BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS
        client_contacts_case_primary_idx
        ON client_contacts (LOWER(TRIM(case_id)))
        WHERE is_primary = TRUE
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS client_messages (
            id SERIAL PRIMARY KEY,
            case_id TEXT NOT NULL,
            client_name TEXT,
            phone_number TEXT NOT NULL,
            channel TEXT DEFAULT 'WHATSAPP',
            message_type TEXT DEFAULT 'CASE_STATUS',
            message_text TEXT NOT NULL,
            sent_by BIGINT,
            delivery_status TEXT DEFAULT 'DRAFT',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sent_at TIMESTAMP
        )
        """)

        cur.execute("ALTER TABLE client_messages ADD COLUMN IF NOT EXISTS client_id INTEGER")
        cur.execute("ALTER TABLE client_messages ADD COLUMN IF NOT EXISTS ad_client_id TEXT")
        cur.execute("ALTER TABLE client_messages ADD COLUMN IF NOT EXISTS communication_ref TEXT")
        cur.execute("ALTER TABLE client_messages ADD COLUMN IF NOT EXISTS template_name TEXT")
        cur.execute("ALTER TABLE client_messages ADD COLUMN IF NOT EXISTS related_case_id TEXT")
        cur.execute("ALTER TABLE client_messages ADD COLUMN IF NOT EXISTS provider_message_id TEXT")
        cur.execute("ALTER TABLE client_messages ADD COLUMN IF NOT EXISTS reply_status TEXT")

        cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS
        client_messages_communication_ref_idx
        ON client_messages (communication_ref)
        WHERE communication_ref IS NOT NULL
        """)

        conn.commit()

        cur.execute("""
            ALTER TABLE case_files
            ADD COLUMN IF NOT EXISTS category
            TEXT DEFAULT 'MISCELLANEOUS'
        """)

        cur.execute("""
            ALTER TABLE case_files
            ADD COLUMN IF NOT EXISTS drive_folder_id
            TEXT
        """)

        conn.commit()

        cur.execute("""
            ALTER TABLE case_files
            ADD COLUMN IF NOT EXISTS file_size BIGINT
        """)

        cur.execute("""
            ALTER TABLE case_files
            ADD COLUMN IF NOT EXISTS sha256_hash TEXT
        """)

        cur.execute("""
            ALTER TABLE case_files
            ADD COLUMN IF NOT EXISTS telegram_file_unique_id TEXT
        """)

        conn.commit()

        cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS case_number TEXT")
        conn.commit()

        cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS due_at TIMESTAMP")
        conn.commit()

        cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS source_type TEXT DEFAULT 'manual'")
        cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS source_work_id TEXT")
        cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS assigned_by BIGINT")
        cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP")
        cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS notes TEXT")
        conn.commit()

        cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS ad_sync_status TEXT")
        cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS ad_created_at TIMESTAMP")
        cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS ad_sync_message TEXT")
        conn.commit()

        cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS ad_case_id TEXT")
        cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS case_title TEXT")
        cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS judge_name TEXT")
        cur.execute("ALTER TABLE cases ADD COLUMN IF NOT EXISTS next_hearing TEXT")
        conn.commit()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS sync_logs (
            id SERIAL PRIMARY KEY,
            sync_type TEXT,
            total_fetched INTEGER,
            added_count INTEGER,
            updated_count INTEGER,
            folders_created INTEGER,
            folders_reused INTEGER,
            skipped_count INTEGER,
            status TEXT,
            message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()
        logger.info("Database initialization completed")
    except Exception:
        conn.rollback()
        logger.exception("Database initialization failed")
        raise
    finally:
        cur.close()
        conn.close()
