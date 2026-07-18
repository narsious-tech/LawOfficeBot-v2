import os
import tempfile
import hashlib
from typing import Optional, Tuple

import psycopg2
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import ContextTypes, ConversationHandler
from utils.drive import (
    drive_service,
    get_drive_service,
    get_or_create_case_folder,
    get_or_create_subfolder,
    drive_is_connected,
    get_drive_error,
)

from config import DATABASE_URL
from services.activity_logger import (
    log_activity,
    log_activity_with_cursor,
)


# Keep this value identical to the ConversationHandler state used in bot.py.
WAITING_FILE = 10
CONFIRM_DUPLICATE_UPLOAD = 11

DOCUMENT_CATEGORIES = {
    "PLEADINGS": "Pleadings",
    "ORDERS": "Orders",
    "EVIDENCE": "Evidence",
    "JUDGMENTS": "Judgments",
    "CORRESPONDENCE": "Correspondence",
    "MISCELLANEOUS": "Miscellaneous"
}


CATEGORY_ALIASES = {
    "pleading": "PLEADINGS",
    "pleadings": "PLEADINGS",

    "order": "ORDERS",
    "orders": "ORDERS",

    "evidence": "EVIDENCE",
    "evidences": "EVIDENCE",

    "judgment": "JUDGMENTS",
    "judgments": "JUDGMENTS",
    "judgement": "JUDGMENTS",
    "judgements": "JUDGMENTS",

    "correspondence": "CORRESPONDENCE",
    "letter": "CORRESPONDENCE",
    "letters": "CORRESPONDENCE",

    "misc": "MISCELLANEOUS",
    "miscellaneous": "MISCELLANEOUS"
}


def normalize_document_category(
    value: str
) -> str:
    if not value:
        return "MISCELLANEOUS"

    normalized = value.strip().lower()

    return CATEGORY_ALIASES.get(
        normalized,
        "MISCELLANEOUS"
    )


def category_folder_name(
    category: str
) -> str:
    return DOCUMENT_CATEGORIES.get(
        category,
        "Miscellaneous"
    )

ROOT_FOLDER_ID = os.getenv(
    "ROOT_FOLDER_ID",
    "1WIeKgxvHFylvQ8Um1l48TkqZGvxr65mS"
)



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

def calculate_sha256(
    file_path: str
) -> str:
    digest = hashlib.sha256()

    with open(file_path, "rb") as file_handle:
        while True:
            chunk = file_handle.read(
                1024 * 1024
            )

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def clear_upload_session(
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data.pop(
        "upload_case_id",
        None
    )

    context.user_data.pop(
        "upload_case_folder_id",
        None
    )

    context.user_data.pop(
        "upload_folder_id",
        None
    )

    context.user_data.pop(
        "upload_category",
        None
    )

    context.user_data.pop(
        "upload_category_name",
        None
    )

    context.user_data.pop(
        "pending_duplicate_upload",
        None
    )


def delete_temp_file(
    file_path: str
):
    if not file_path:
        return

    try:
        if os.path.exists(file_path):
            os.remove(file_path)

    except Exception as exc:
        print(
            "TEMP FILE CLEANUP FAILED: "
            f"{type(exc).__name__}: {exc}"
        )
        
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

            log_activity_with_cursor(
                cur,
                case_value=display_case,
                event_code="DRIVE_FOLDER_CREATED",
                details=(
                    f"Google Drive folder linked.\n"
                    f"Folder: {folder_link or folder_id}"
                ),
                source_module="DRIVE_FOLDER",
                source_id=folder_id,
                user_id=update.effective_user.id,
                metadata={
                    "folder_id": folder_id,
                    "folder_link": folder_link,
                }
            )

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
            "/upload CASE_NUMBER CATEGORY\n\n"
            "Examples:\n"
            "/upload CS/3528/2026 pleadings\n"
            "/upload CS/3528/2026 orders\n"
            "/upload CS/3528/2026 evidence\n"
            "/upload CS/3528/2026 judgments\n\n"
            "Category is optional. Without a category, "
            "the file goes to Miscellaneous."
        )
        return ConversationHandler.END

    case_value = normalize_case_value(
        context.args[0]
    )

    category_input = (
        context.args[1]
        if len(context.args) > 1
        else "miscellaneous"
    )

    category = normalize_document_category(
        category_input
    )

    folder_name = category_folder_name(
        category
    )

    case_row = find_case_record(
        case_value
    )

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
        case_folder_id,
        case_folder_link
    ) = case_row

    canonical_case_id = (
        case_id
        or case_number
        or case_value
    )

    if not case_folder_id:
        await update.effective_message.reply_text(
            f"❌ Google Drive folder not found for "
            f"{canonical_case_id}.\n\n"
            f"Use /casefolder {canonical_case_id} first."
        )
        return ConversationHandler.END

    try:
        target_folder_id, target_folder_link = (
            get_or_create_subfolder(
                case_folder_id,
                folder_name
            )
        )

    except Exception as exc:
        await update.effective_message.reply_text(
            "❌ Could not prepare the document "
            "category folder:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return ConversationHandler.END

    context.user_data[
        "upload_case_id"
    ] = canonical_case_id

    context.user_data[
        "upload_case_folder_id"
    ] = case_folder_id

    context.user_data[
        "upload_folder_id"
    ] = target_folder_id

    context.user_data[
        "upload_category"
    ] = category

    context.user_data[
        "upload_category_name"
    ] = folder_name

    message = (
        f"📂 Case found: {canonical_case_id}\n"
    )

    if case_title:
        message += f"⚖️ {case_title}\n"

    if client_name:
        message += f"👤 Client: {client_name}\n"

    message += (
        f"🗂 Category: {folder_name}\n\n"
        "Now send the document, PDF, Word file, "
        "or photo to upload."
    )

    await update.effective_message.reply_text(
        message,
        disable_web_page_preview=True
    )

    return WAITING_FILE


async def complete_drive_upload(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    local_path: str,
    case_id: str,
    folder_id: str,
    filename: str,
    mime_type: str,
    category: str,
    category_name: str,
    file_size: int,
    sha256_hash: str,
    telegram_file_unique_id: str = None
):
    service = get_drive_service()

    if service is None:
        await update.effective_message.reply_text(
            "❌ Google Drive is currently disconnected."
        )

        delete_temp_file(local_path)
        clear_upload_session(context)

        return ConversationHandler.END

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
            service
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
            "Google Drive."
        )

        delete_temp_file(local_path)
        clear_upload_session(context)

        return ConversationHandler.END

    except Exception as exc:
        await update.effective_message.reply_text(
            "❌ Google Drive upload failed:\n"
            f"{type(exc).__name__}: {exc}"
        )

        delete_temp_file(local_path)
        clear_upload_session(context)

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
                uploaded_by,
                category,
                drive_folder_id,
                file_size,
                sha256_hash,
                telegram_file_unique_id
            )
            VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            )
            RETURNING id, uploaded_at
        """, (
            case_id,
            filename,
            uploaded.get("id"),
            uploaded.get("webViewLink"),
            update.effective_user.id,
            category,
            folder_id,
            file_size,
            sha256_hash,
            telegram_file_unique_id
        ))

        inserted = cur.fetchone()

        file_record_id = (
            inserted[0]
            if inserted
            else None
        )

        duplicate_override = bool(
            context.user_data.get(
                "pending_duplicate_upload"
            )
        )

        log_activity_with_cursor(
            cur,
            case_value=case_id,
            event_code=(
                "DOCUMENT_DUPLICATE_UPLOADED"
                if duplicate_override
                else "DOCUMENT_UPLOADED"
            ),
            details=(
                f"File: {filename}\n"
                f"Category: {category_name}\n"
                f"Size: {file_size:,} bytes\n"
                f"Drive: {uploaded.get('webViewLink') or '-'}"
            ),
            source_module="CASE_FILE",
            source_id=file_record_id,
            user_id=update.effective_user.id,
            metadata={
                "file_id": file_record_id,
                "file_name": filename,
                "category": category,
                "category_name": category_name,
                "drive_file_id": uploaded.get("id"),
                "drive_file_link": uploaded.get("webViewLink"),
                "file_size": file_size,
                "sha256_hash": sha256_hash,
                "duplicate_override": duplicate_override,
            }
        )

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
        delete_temp_file(local_path)

    clear_upload_session(context)

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
        f"🗂 Category: {category_name}\n"
        f"📄 File: {filename}\n"
        f"📦 Size: {file_size:,} bytes\n"
        f"🕒 Uploaded: "
        f"{format_datetime(uploaded_at)}\n"
        f"🔗 Link:\n"
        f"{uploaded.get('webViewLink')}",
        disable_web_page_preview=True
    )

    return ConversationHandler.END

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

    category = context.user_data.get(
        "upload_category",
        "MISCELLANEOUS"
    )

    category_name = context.user_data.get(
        "upload_category_name",
        "Miscellaneous"
    )

    if not case_id or not folder_id:
        await update.effective_message.reply_text(
            "❌ Upload session expired.\n"
            "Start again with /upload CASE_NUMBER."
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

    telegram_file_unique_id = None

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

        telegram_file_unique_id = (
            document.file_unique_id
        )

    elif photo:
        tg_file = await photo.get_file()

        filename = (
            f"{case_id}_photo.jpg"
        )

        mime_type = "image/jpeg"

        telegram_file_unique_id = (
            photo.file_unique_id
        )

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

        file_size = os.path.getsize(
            local_path
        )

        sha256_hash = calculate_sha256(
            local_path
        )

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
                        cf.category,
                        'MISCELLANEOUS'
                    ) AS category,
                    COALESCE(
                        sa.staff_name,
                        'Admin / Unlinked User'
                    ) AS uploaded_by_name,
                    CASE
                        WHEN cf.sha256_hash = %s
                            THEN 'EXACT_HASH'

                        WHEN cf.telegram_file_unique_id = %s
                            THEN 'TELEGRAM_FILE'

                        WHEN LOWER(TRIM(cf.file_name))
                             = LOWER(TRIM(%s))
                            THEN 'SAME_FILENAME'

                        ELSE 'POSSIBLE_DUPLICATE'
                    END AS match_type

                FROM case_files cf

                LEFT JOIN staff_accounts sa
                    ON sa.telegram_user_id
                       = cf.uploaded_by

                WHERE LOWER(TRIM(cf.case_id))
                      = LOWER(TRIM(%s))

                  AND (
                        cf.sha256_hash = %s

                        OR (
                            %s IS NOT NULL
                            AND cf.telegram_file_unique_id = %s
                        )

                        OR LOWER(TRIM(cf.file_name))
                           = LOWER(TRIM(%s))
                      )

                ORDER BY
                    CASE
                        WHEN cf.sha256_hash = %s THEN 1
                        WHEN cf.telegram_file_unique_id = %s THEN 2
                        ELSE 3
                    END,
                    cf.id DESC

                LIMIT 1
            """, (
                sha256_hash,
                telegram_file_unique_id,
                filename,

                case_id,

                sha256_hash,

                telegram_file_unique_id,
                telegram_file_unique_id,

                filename,

                sha256_hash,
                telegram_file_unique_id
            ))
            duplicate = cur.fetchone()

        finally:
            cur.close()
            conn.close()

        if duplicate:
            (
                existing_file_id,
                existing_filename,
                existing_link,
                existing_uploaded_at,
                existing_category,
                existing_uploader,
                match_type
            ) = duplicate

            context.user_data[
                "pending_duplicate_upload"
            ] = {
                "local_path": local_path,
                "case_id": case_id,
                "folder_id": folder_id,
                "filename": filename,
                "mime_type": mime_type,
                "category": category,
                "category_name": category_name,
                "file_size": file_size,
                "sha256_hash": sha256_hash,
                "telegram_file_unique_id": (
                    telegram_file_unique_id
                )
            }

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬆️ Upload Anyway",
                        callback_data=(
                            "duplicate_upload:yes"
                        )
                    ),
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data=(
                            "duplicate_upload:no"
                        )
                    )
                ]
            ])

            await update.effective_message.reply_text(
                "⚠️ POSSIBLE DUPLICATE DOCUMENT\n\n"
                f"📄 New File: {filename}\n"
                f"📦 Size: {file_size:,} bytes\n\n"
                f"Existing File ID: "
                f"#{existing_file_id}\n"
                f"📄 Existing File: "
                f"{existing_filename}\n"
                f"🗂 Existing Category: "
                f"{category_folder_name(existing_category)}\n"
                f"👤 Uploaded By: "
                f"{existing_uploader}\n"
                f"🕒 Uploaded: "
                f"{format_datetime(existing_uploaded_at)}\n"
                f"🔗 {existing_link or '-'}\n\n"
                "This appears to be the exact same file.\n"
                "Upload it again?",
                reply_markup=keyboard,
                disable_web_page_preview=True
            )

            return CONFIRM_DUPLICATE_UPLOAD

        return await complete_drive_upload(
            update,
            context,
            local_path=local_path,
            case_id=case_id,
            folder_id=folder_id,
            filename=filename,
            mime_type=mime_type,
            category=category,
            category_name=category_name,
            file_size=file_size,
            sha256_hash=sha256_hash,
            telegram_file_unique_id=(
                telegram_file_unique_id
            )
        )

    except Exception as exc:
        delete_temp_file(local_path)

        await update.effective_message.reply_text(
            "❌ Upload processing failed:\n"
            f"{type(exc).__name__}: {exc}"
        )

        return ConversationHandler.END

async def duplicate_upload_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    decision = query.data.split(
        ":",
        1
    )[1]

    pending = context.user_data.get(
        "pending_duplicate_upload"
    )

    if not pending:
        await query.edit_message_text(
            "❌ Duplicate-upload session expired.\n"
            "Start again with /upload."
        )

        return ConversationHandler.END

    if decision == "no":
        delete_temp_file(
            pending.get("local_path")
        )

        clear_upload_session(
            context
        )

        await query.edit_message_text(
            "❌ Duplicate upload cancelled.\n"
            "The existing document was retained."
        )

        return ConversationHandler.END

    await query.edit_message_text(
        "⏳ Uploading duplicate document..."
    )

    return await complete_drive_upload(
        update,
        context,
        local_path=pending["local_path"],
        case_id=pending["case_id"],
        folder_id=pending["folder_id"],
        filename=pending["filename"],
        mime_type=pending["mime_type"],
        category=pending["category"],
        category_name=pending[
            "category_name"
        ],
        file_size=pending["file_size"],
        sha256_hash=pending["sha256_hash"],
        telegram_file_unique_id=pending.get(
            "telegram_file_unique_id"
        )
    )

async def cancel_upload(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    pending = context.user_data.get(
        "pending_duplicate_upload"
    )

    if pending:
        delete_temp_file(
            pending.get("local_path")
        )

    clear_upload_session(
        context
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
                COALESCE(
                    cf.category,
                    'MISCELLANEOUS'
                ) AS category,
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
            category,
            file_link,
            uploaded_at,
            uploaded_by_name
        ) in file_rows:

            message += (
                f"🆔 File #{file_id}\n"
                f"🗂 Category: {category_folder_name(category)}\n"
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

async def openfile(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/openfile FILE_ID\n\n"
            "Example:\n"
            "/openfile 8"
        )
        return

    file_id_text = context.args[0].strip()

    if not file_id_text.isdigit():
        await update.effective_message.reply_text(
            "❌ FILE_ID must be a number.\n"
            "Example: /openfile 8"
        )
        return

    file_id = int(file_id_text)

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
                    cf.category,
                    'MISCELLANEOUS'
                ) AS category,
                COALESCE(
                    sa.staff_name,
                    'Admin / Unlinked User'
                ) AS uploaded_by_name,
                c.case_number,
                c.case_title,
                c.client_name
            FROM case_files cf

            LEFT JOIN staff_accounts sa
                ON sa.telegram_user_id
                   = cf.uploaded_by

            LEFT JOIN LATERAL (
                SELECT
                    case_number,
                    case_title,
                    client_name
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

            WHERE cf.id = %s
            LIMIT 1
        """, (
            file_id,
        ))

        row = cur.fetchone()

    finally:
        cur.close()
        conn.close()

    if not row:
        await update.effective_message.reply_text(
            f"❌ File not found: #{file_id}"
        )
        return

    (
        stored_file_id,
        case_id,
        file_name,
        file_link,
        uploaded_at,
        category,
        uploaded_by_name,
        case_number,
        case_title,
        client_name
    ) = row

    category_name = category_folder_name(
        category
    )

    message = (
        "📄 FILE DETAILS\n\n"
        f"🆔 File #{stored_file_id}\n"
        f"🔢 Case: {case_number or case_id}\n"
    )

    if case_title:
        message += f"⚖️ {case_title}\n"

    if client_name:
        message += f"👤 Client: {client_name}\n"

    message += (
        f"🗂 Category: {category_name}\n"
        f"📄 File: {file_name}\n"
        f"👤 Uploaded By: {uploaded_by_name}\n"
        f"🕒 Uploaded: "
        f"{format_datetime(uploaded_at)}\n\n"
        f"🔗 Open Document:\n"
        f"{file_link or '-'}"
    )

    await update.effective_message.reply_text(
        message,
        disable_web_page_preview=True
    )

async def findfile(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/findfile KEYWORD\n"
            "/findfile CASE_NUMBER | KEYWORD\n\n"
            "Examples:\n"
            "/findfile judgment\n"
            "/findfile order 7 rule 11\n"
            "/findfile CS/3528/2026 | lawfinder"
        )
        return

    raw_query = " ".join(
        context.args
    ).strip()

    case_filter = None
    keyword = raw_query

    if "|" in raw_query:
        left, right = raw_query.split(
            "|",
            1
        )

        case_filter = left.strip()
        keyword = right.strip()

    if not keyword:
        await update.effective_message.reply_text(
            "❌ Search keyword is missing."
        )
        return

    search_pattern = f"%{keyword}%"

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        if case_filter:
            cur.execute("""
                SELECT
                    cf.id,
                    cf.case_id,
                    cf.file_name,
                    cf.drive_file_link,
                    cf.uploaded_at,
                    COALESCE(
                        cf.category,
                        'MISCELLANEOUS'
                    ) AS category,
                    COALESCE(
                        sa.staff_name,
                        'Admin / Unlinked User'
                    ) AS uploaded_by_name,
                    c.case_number,
                    c.case_title,
                    c.client_name
                FROM case_files cf

                LEFT JOIN staff_accounts sa
                    ON sa.telegram_user_id
                       = cf.uploaded_by

                LEFT JOIN LATERAL (
                    SELECT
                        case_number,
                        case_title,
                        client_name
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

                WHERE (
                    LOWER(TRIM(cf.case_id))
                        = LOWER(TRIM(%s))
                    OR
                    LOWER(TRIM(COALESCE(c.case_number, '')))
                        = LOWER(TRIM(%s))
                )
                  AND (
                    cf.file_name ILIKE %s
                    OR COALESCE(cf.category, '') ILIKE %s
                    OR COALESCE(c.case_title, '') ILIKE %s
                    OR COALESCE(c.client_name, '') ILIKE %s
                  )

                ORDER BY
                    cf.uploaded_at DESC,
                    cf.id DESC

                LIMIT 30
            """, (
                case_filter,
                case_filter,
                search_pattern,
                search_pattern,
                search_pattern,
                search_pattern
            ))

        else:
            cur.execute("""
                SELECT
                    cf.id,
                    cf.case_id,
                    cf.file_name,
                    cf.drive_file_link,
                    cf.uploaded_at,
                    COALESCE(
                        cf.category,
                        'MISCELLANEOUS'
                    ) AS category,
                    COALESCE(
                        sa.staff_name,
                        'Admin / Unlinked User'
                    ) AS uploaded_by_name,
                    c.case_number,
                    c.case_title,
                    c.client_name
                FROM case_files cf

                LEFT JOIN staff_accounts sa
                    ON sa.telegram_user_id
                       = cf.uploaded_by

                LEFT JOIN LATERAL (
                    SELECT
                        case_number,
                        case_title,
                        client_name
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

                WHERE
                    cf.file_name ILIKE %s
                    OR COALESCE(cf.category, '') ILIKE %s
                    OR COALESCE(cf.case_id, '') ILIKE %s
                    OR COALESCE(c.case_number, '') ILIKE %s
                    OR COALESCE(c.case_title, '') ILIKE %s
                    OR COALESCE(c.client_name, '') ILIKE %s

                ORDER BY
                    cf.uploaded_at DESC,
                    cf.id DESC

                LIMIT 30
            """, (
                search_pattern,
                search_pattern,
                search_pattern,
                search_pattern,
                search_pattern,
                search_pattern
            ))

        rows = cur.fetchall()

    except Exception as exc:
        await update.effective_message.reply_text(
            "❌ File search failed:\n"
            f"{type(exc).__name__}: {exc}"
        )
        return

    finally:
        cur.close()
        conn.close()

    if not rows:
        filter_text = (
            f" in {case_filter}"
            if case_filter
            else ""
        )

        await update.effective_message.reply_text(
            f"🔍 No files found for "
            f"“{keyword}”{filter_text}."
        )
        return

    message = (
        "🔍 FILE SEARCH RESULTS\n\n"
        f"Search: {keyword}\n"
    )

    if case_filter:
        message += (
            f"Case Filter: {case_filter}\n"
        )

    message += (
        f"Results: {len(rows)}\n\n"
    )

    for (
        file_id,
        case_id,
        file_name,
        file_link,
        uploaded_at,
        category,
        uploaded_by_name,
        case_number,
        case_title,
        client_name
    ) in rows:

        message += (
            f"🆔 File #{file_id}\n"
            f"🔢 Case: {case_number or case_id}\n"
        )

        if case_title:
            message += f"⚖️ {case_title}\n"

        if client_name:
            message += (
                f"👤 Client: {client_name}\n"
            )

        message += (
            f"🗂 Category: "
            f"{category_folder_name(category)}\n"
            f"📄 {file_name}\n"
            f"👤 Uploaded By: "
            f"{uploaded_by_name}\n"
            f"🕒 "
            f"{format_datetime(uploaded_at)}\n"
            f"🔗 {file_link or '-'}\n"
            f"/openfile {file_id}\n\n"
            "──────────────\n\n"
        )

    await send_long_message(
        update,
        message
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