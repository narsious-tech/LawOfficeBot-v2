import os
import tempfile
from typing import Optional, Tuple

import psycopg2
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from config import DATABASE_URL


# Keep this value identical to the ConversationHandler state used in bot.py.
WAITING_FILE = 10

ROOT_FOLDER_ID = os.getenv(
    "ROOT_FOLDER_ID",
    "1WIeKgxvHFylvQ8Um1l48TkqZGvxr65mS"
)

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN")

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"


def build_drive_service():
    if not (
        GOOGLE_CLIENT_ID
        and GOOGLE_CLIENT_SECRET
        and GOOGLE_REFRESH_TOKEN
    ):
        print(
            "Google Drive disabled in commands/files.py: "
            "OAuth variables are incomplete."
        )
        return None

    credentials = Credentials(
        None,
        refresh_token=GOOGLE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET
    )

    try:
        credentials.refresh(Request())

        service = build(
            "drive",
            "v3",
            credentials=credentials,
            cache_discovery=False
        )

        print("Google Drive connected in commands/files.py.")
        return service

    except RefreshError as exc:
        print(
            "Google Drive disabled in commands/files.py: "
            f"{type(exc).__name__}: {exc}"
        )
        return None

    except Exception as exc:
        print(
            "Google Drive setup failed in commands/files.py: "
            f"{type(exc).__name__}: {exc}"
        )
        return None


drive_service = build_drive_service()


def get_db_connection():
    return psycopg2.connect(DATABASE_URL)


def normalize_case_value(value: str) -> str:
    return (value or "").strip()


def escape_drive_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def format_datetime(value) -> str:
    if not value:
        return "-"

    if hasattr(value, "strftime"):
        return value.strftime("%d-%m-%Y %I:%M %p")

    return str(value)


async def send_long_message(
    update: Update,
    message: str
):
    max_length = 3800
    remaining = message

    while remaining:
        if len(remaining) <= max_length:
            chunk = remaining
            remaining = ""
        else:
            split_at = remaining.rfind(
                "\n\n",
                0,
                max_length
            )

            if split_at == -1:
                split_at = max_length

            chunk = remaining[:split_at]
            remaining = remaining[split_at:].lstrip()

        await update.effective_message.reply_text(
            chunk,
            disable_web_page_preview=True
        )


def find_case_record(
    case_value: str
) -> Optional[Tuple]:
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                id,
                case_id,
                case_number,
                case_title,
                client_name,
                drive_folder_id,
                drive_folder_link
            FROM cases
            WHERE LOWER(TRIM(COALESCE(case_id, '')))
                  = LOWER(TRIM(%s))
               OR LOWER(TRIM(COALESCE(case_number, '')))
                  = LOWER(TRIM(%s))
            ORDER BY id DESC
            LIMIT 1
        """, (
            case_value,
            case_value
        ))

        return cur.fetchone()

    finally:
        cur.close()
        conn.close()


def get_or_create_case_folder(
    case_value: str
) -> Tuple[str, Optional[str]]:
    if drive_service is None:
        raise RuntimeError(
            "Google Drive is currently disconnected."
        )

    safe_name = normalize_case_value(
        case_value
    ).replace("/", "-")

    escaped_name = escape_drive_query_value(
        safe_name
    )

    query = (
        f"name = '{escaped_name}' "
        "and mimeType = "
        "'application/vnd.google-apps.folder' "
        f"and '{ROOT_FOLDER_ID}' in parents "
        "and trashed = false"
    )

    results = drive_service.files().list(
        q=query,
        fields="files(id, name, webViewLink)",
        spaces="drive"
    ).execute()

    folders = results.get("files", [])

    if folders:
        folder = folders[0]

        return (
            folder["id"],
            folder.get("webViewLink")
        )

    folder_metadata = {
        "name": safe_name,
        "mimeType": (
            "application/vnd.google-apps.folder"
        ),
        "parents": [ROOT_FOLDER_ID]
    }

    folder = drive_service.files().create(
        body=folder_metadata,
        fields="id, webViewLink"
    ).execute()

    return (
        folder["id"],
        folder.get("webViewLink")
    )


async def casefolder(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/casefolder CASE_NUMBER\n\n"
            "Example:\n"
            "/casefolder CS/3528/2026"
        )
        return

    if drive_service is None:
        await update.effective_message.reply_text(
            "❌ Google Drive is currently disconnected.\n"
            "Please ask the administrator to reconnect it."
        )
        return

    case_value = normalize_case_value(
        " ".join(context.args)
    )

    case_row = find_case_record(case_value)

    if not case_row:
        await update.effective_message.reply_text(
            f"❌ Case not found: {case_value}"
        )
        return

    (
        case_db_id,
        case_id,
        case_number,
        case_title,
        client_name,
        existing_folder_id,
        existing_folder_link
    ) = case_row

    display_case = (
        case_number
        or case_id
        or case_value
    )

    if existing_folder_id:
        await update.effective_message.reply_text(
            "📁 Case folder already exists\n\n"
            f"🔢 Case: {display_case}\n"
            f"🔗 {existing_folder_link or 'Link not recorded'}",
            disable_web_page_preview=True
        )
        return

    try:
        folder_id, folder_link = (
            get_or_create_case_folder(
                display_case
            )
        )

        conn = get_db_connection()
        cur = conn.cursor()

        try:
            cur.execute("""
                UPDATE cases
                SET
                    drive_folder_id = %s,
                    drive_folder_link = %s
                WHERE id = %s
            """, (
                folder_id,
                folder_link,
                case_db_id
            ))

            conn.commit()

        finally:
            cur.close()
            conn.close()

        message = (
            "📁 Folder ready successfully\n\n"
            f"🔢 Case: {display_case}\n"
        )

        if case_title:
            message += f"⚖️ {case_title}\n"

        if client_name:
            message += f"👤 Client: {client_name}\n"

        message += (
            f"\n🔗 Google Drive Folder:\n"
            f"{folder_link or 'Link unavailable'}"
        )

        await update.effective_message.reply_text(
            message,
            disable_web_page_preview=True
        )

    except Exception as exc:
        await update.effective_message.reply_text(
            "❌ Folder creation failed:\n"
            f"{type(exc).__name__}: {exc}"
        )


async def upload_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/upload CASE_NUMBER\n\n"
            "Example:\n"
            "/upload CS/3528/2026"
        )
        return ConversationHandler.END

    case_value = normalize_case_value(
        " ".join(context.args)
    )

    case_row = find_case_record(case_value)

    if not case_row:
        await update.effective_message.reply_text(
            f"❌ Case not found: {case_value}"
        )
        return ConversationHandler.END

    (
        _case_db_id,
        case_id,
        case_number,
        case_title,
        client_name,
        folder_id,
        folder_link
    ) = case_row

    canonical_case_id = (
        case_id
        or case_number
        or case_value
    )

    if not folder_id:
        await update.effective_message.reply_text(
            f"❌ Google Drive folder not found for "
            f"{canonical_case_id}.\n\n"
            f"Use /casefolder {canonical_case_id} first."
        )
        return ConversationHandler.END

    context.user_data[
        "upload_case_id"
    ] = canonical_case_id

    context.user_data[
        "upload_folder_id"
    ] = folder_id

    message = (
        f"📂 Case found: {canonical_case_id}\n"
    )

    if case_title:
        message += f"⚖️ {case_title}\n"

    if client_name:
        message += f"👤 Client: {client_name}\n"

    message += (
        "\nNow send the document, PDF, Word file, "
        "or photo to upload."
    )

    await update.effective_message.reply_text(
        message,
        disable_web_page_preview=True
    )

    return WAITING_FILE


async def upload_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    case_id = context.user_data.get(
        "upload_case_id"
    )

    folder_id = context.user_data.get(
        "upload_folder_id"
    )

    if not case_id or not folder_id:
        await update.effective_message.reply_text(
            "❌ Upload session expired.\n"
            "Start again with /upload CASE_NUMBER."
        )
        return ConversationHandler.END

    if drive_service is None:
        await update.effective_message.reply_text(
            "❌ Google Drive is currently disconnected.\n"
            "Please ask the administrator to reconnect it."
        )
        return ConversationHandler.END

    document = (
        update.effective_message.document
    )

    photo = (
        update.effective_message.photo[-1]
        if update.effective_message.photo
        else None
    )

    if document:
        tg_file = await document.get_file()

        filename = (
            document.file_name
            or f"{case_id}_document"
        )

        mime_type = (
            document.mime_type
            or "application/octet-stream"
        )

    elif photo:
        tg_file = await photo.get_file()
        filename = f"{case_id}_photo.jpg"
        mime_type = "image/jpeg"

    else:
        await update.effective_message.reply_text(
            "❌ Please send a document, PDF, "
            "Word file, or photo."
        )
        return WAITING_FILE

    suffix = os.path.splitext(
        filename
    )[1]

    temp_handle = tempfile.NamedTemporaryFile(
        prefix="lawoffice_",
        suffix=suffix,
        delete=False
    )

    local_path = temp_handle.name
    temp_handle.close()

    try:
        await tg_file.download_to_drive(
            local_path
        )

        file_metadata = {
            "name": filename,
            "parents": [folder_id]
        }

        media = MediaFileUpload(
            local_path,
            mimetype=mime_type,
            resumable=True
        )

        try:
            uploaded = (
                drive_service
                .files()
                .create(
                    body=file_metadata,
                    media_body=media,
                    fields="id, webViewLink"
                )
                .execute()
            )

        except RefreshError:
            await update.effective_message.reply_text(
                "❌ Google Drive authorization "
                "has expired or been revoked.\n\n"
                "The administrator must reconnect "
                "Google Drive and update "
                "GOOGLE_REFRESH_TOKEN in Railway."
            )
            return ConversationHandler.END

        except Exception as exc:
            await update.effective_message.reply_text(
                "❌ Google Drive upload failed:\n"
                f"{type(exc).__name__}: {exc}"
            )
            return ConversationHandler.END

        conn = get_db_connection()
        cur = conn.cursor()

        try:
            cur.execute("""
                INSERT INTO case_files
                (
                    case_id,
                    file_name,
                    drive_file_id,
                    drive_file_link,
                    uploaded_by
                )
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, uploaded_at
            """, (
                case_id,
                filename,
                uploaded.get("id"),
                uploaded.get("webViewLink"),
                update.effective_user.id
            ))

            inserted = cur.fetchone()
            conn.commit()

        except Exception as exc:
            conn.rollback()

            await update.effective_message.reply_text(
                "⚠️ File was uploaded to Google Drive, "
                "but the database record failed.\n\n"
                f"{type(exc).__name__}: {exc}\n\n"
                f"Drive link:\n"
                f"{uploaded.get('webViewLink')}"
            )
            return ConversationHandler.END

        finally:
            cur.close()
            conn.close()

        context.user_data.pop(
            "upload_case_id",
            None
        )

        context.user_data.pop(
            "upload_folder_id",
            None
        )

        file_record_id = (
            inserted[0]
            if inserted
            else "-"
        )

        uploaded_at = (
            inserted[1]
            if inserted
            else None
        )

        await update.effective_message.reply_text(
            "✅ File uploaded successfully\n\n"
            f"🆔 File ID: {file_record_id}\n"
            f"🔢 Case: {case_id}\n"
            f"📄 File: {filename}\n"
            f"🕒 Uploaded: "
            f"{format_datetime(uploaded_at)}\n"
            f"🔗 Link:\n"
            f"{uploaded.get('webViewLink')}",
            disable_web_page_preview=True
        )

        return ConversationHandler.END

    except Exception as exc:
        await update.effective_message.reply_text(
            "❌ Upload processing failed:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return ConversationHandler.END

    finally:
        try:
            if os.path.exists(local_path):
                os.remove(local_path)

        except Exception as cleanup_error:
            print(
                "TEMP FILE CLEANUP FAILED: "
                f"{type(cleanup_error).__name__}: "
                f"{cleanup_error}"
            )


async def cancel_upload(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data.pop(
        "upload_case_id",
        None
    )

    context.user_data.pop(
        "upload_folder_id",
        None
    )

    await update.effective_message.reply_text(
        "❌ Upload cancelled."
    )

    return ConversationHandler.END


async def files(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/files CASE_NUMBER\n\n"
            "Example:\n"
            "/files CS/3528/2026"
        )
        return

    case_value = normalize_case_value(
        " ".join(context.args)
    )

    case_row = find_case_record(case_value)

    if not case_row:
        await update.effective_message.reply_text(
            f"❌ Case not found: {case_value}"
        )
        return

    (
        _case_db_id,
        case_id,
        case_number,
        case_title,
        client_name,
        _folder_id,
        folder_link
    ) = case_row

    identifiers = list({
        value.strip()
        for value in [
            case_id or "",
            case_number or "",
            case_value
        ]
        if value and value.strip()
    })

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                cf.id,
                cf.file_name,
                cf.drive_file_link,
                cf.uploaded_at,
                COALESCE(
                    sa.staff_name,
                    'Admin / Unlinked User'
                ) AS uploaded_by_name
            FROM case_files cf

            LEFT JOIN staff_accounts sa
                ON sa.telegram_user_id
                   = cf.uploaded_by

            WHERE LOWER(TRIM(cf.case_id))
                  = ANY(
                      SELECT LOWER(TRIM(value))
                      FROM UNNEST(%s::text[]) AS value
                  )

            ORDER BY
                cf.uploaded_at DESC,
                cf.id DESC
        """, (
            identifiers,
        ))

        file_rows = cur.fetchall()

    except Exception as exc:
        await update.effective_message.reply_text(
            "❌ Could not load case files:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return

    finally:
        cur.close()
        conn.close()

    display_case = (
        case_number
        or case_id
        or case_value
    )

    message = (
        "📂 CASE DOCUMENTS\n\n"
        f"🔢 Case: {display_case}\n"
    )

    if case_title:
        message += f"⚖️ {case_title}\n"

    if client_name:
        message += (
            f"👤 Client: {client_name}\n"
        )

    message += (
        f"📄 Total Files: "
        f"{len(file_rows)}\n\n"
    )

    if not file_rows:
        message += (
            "No uploaded documents are recorded "
            "for this case.\n\n"
        )

    else:
        for (
            file_id,
            file_name,
            file_link,
            uploaded_at,
            uploaded_by_name
        ) in file_rows:

            message += (
                f"🆔 File #{file_id}\n"
                f"📄 {file_name}\n"
                f"👤 Uploaded By: "
                f"{uploaded_by_name}\n"
                f"🕒 Uploaded: "
                f"{format_datetime(uploaded_at)}\n"
                f"🔗 {file_link or '-'}\n\n"
                "──────────────\n\n"
            )

    if folder_link:
        message += (
            "📁 Case Folder:\n"
            f"{folder_link}"
        )

    await send_long_message(
        update,
        message
    )


async def casefiles(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await files(
        update,
        context
    )


async def filehistory(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await files(
        update,
        context
    )


async def latestfiles(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    limit = 10

    if context.args:
        value = context.args[0].strip()

        if not value.isdigit():
            await update.effective_message.reply_text(
                "Usage:\n"
                "/latestfiles\n"
                "/latestfiles 20"
            )
            return

        limit = max(
            1,
            min(int(value), 30)
        )

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                cf.id,
                cf.case_id,
                cf.file_name,
                cf.drive_file_link,
                cf.uploaded_at,
                COALESCE(
                    sa.staff_name,
                    'Admin / Unlinked User'
                ) AS uploaded_by_name,
                c.case_title,
                c.client_name,
                c.case_number
            FROM case_files cf

            LEFT JOIN staff_accounts sa
                ON sa.telegram_user_id
                   = cf.uploaded_by

            LEFT JOIN LATERAL (
                SELECT
                    case_title,
                    client_name,
                    case_number
                FROM cases
                WHERE LOWER(TRIM(COALESCE(case_id, '')))
                      =
                      LOWER(TRIM(cf.case_id))
                   OR LOWER(TRIM(COALESCE(case_number, '')))
                      =
                      LOWER(TRIM(cf.case_id))
                ORDER BY id DESC
                LIMIT 1
            ) c
                ON TRUE

            ORDER BY
                cf.uploaded_at DESC,
                cf.id DESC

            LIMIT %s
        """, (
            limit,
        ))

        rows = cur.fetchall()

    except Exception as exc:
        await update.effective_message.reply_text(
            "❌ Could not load recent files:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return

    finally:
        cur.close()
        conn.close()

    if not rows:
        await update.effective_message.reply_text(
            "📂 No uploaded files found."
        )
        return

    message = (
        "🕒 LATEST CASE FILES\n\n"
        f"Showing latest {len(rows)} "
        "file(s)\n\n"
    )

    for (
        file_id,
        case_id,
        file_name,
        file_link,
        uploaded_at,
        uploaded_by_name,
        case_title,
        client_name,
        case_number
    ) in rows:

        message += (
            f"🆔 File #{file_id}\n"
            f"🔢 Case: "
            f"{case_number or case_id}\n"
        )

        if case_title:
            message += f"⚖️ {case_title}\n"

        if client_name:
            message += (
                f"👤 Client: {client_name}\n"
            )

        message += (
            f"📄 {file_name}\n"
            f"👤 Uploaded By: "
            f"{uploaded_by_name}\n"
            f"🕒 "
            f"{format_datetime(uploaded_at)}\n"
            f"🔗 {file_link or '-'}\n\n"
            "──────────────\n\n"
        )

    await send_long_message(
        update,
        message
    )


async def sharecasefolder(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/sharecasefolder CASE_NUMBER\n\n"
            "Example:\n"
            "/sharecasefolder CS/3528/2026"
        )
        return

    case_value = normalize_case_value(
        " ".join(context.args)
    )

    case_row = find_case_record(
        case_value
    )

    if not case_row:
        await update.effective_message.reply_text(
            f"❌ Case not found: {case_value}"
        )
        return

    (
        _case_db_id,
        case_id,
        case_number,
        case_title,
        client_name,
        _folder_id,
        folder_link
    ) = case_row

    if not folder_link:
        await update.effective_message.reply_text(
            "❌ This case does not have a Google "
            "Drive folder link recorded.\n\n"
            f"Use /casefolder "
            f"{case_number or case_id or case_value} first."
        )
        return

    message = (
        "📁 CASE FOLDER\n\n"
        f"🔢 Case: "
        f"{case_number or case_id}\n"
    )

    if case_title:
        message += f"⚖️ {case_title}\n"

    if client_name:
        message += (
            f"👤 Client: {client_name}\n"
        )

    message += (
        "\n🔗 Google Drive Folder:\n"
        f"{folder_link}"
    )

    await update.effective_message.reply_text(
        message,
        disable_web_page_preview=True
    )
