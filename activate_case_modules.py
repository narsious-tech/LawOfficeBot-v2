"""
One-time helper to activate modular case handlers in bot.py.

Run from the repository root:

    python activate_case_modules.py

The script creates bot.py.before_case_modules as a backup.
"""

from pathlib import Path
import shutil
import sys


BOT_PATH = Path("bot.py")
BACKUP_PATH = Path("bot.py.before_case_modules")

IMPORT_LINE = "from case_handlers import register_case_handlers\n"
CALL_LINE = "register_case_handlers(app)\n"


def main() -> int:
    if not BOT_PATH.exists():
        print("ERROR: bot.py was not found in the current folder.")
        return 1

    source = BOT_PATH.read_text(encoding="utf-8")

    if IMPORT_LINE.strip() not in source:
        marker = "from telegram.ext import CallbackQueryHandler\n"
        if marker in source:
            source = source.replace(
                marker,
                marker + IMPORT_LINE,
                1,
            )
        else:
            source = IMPORT_LINE + source

    if CALL_LINE.strip() not in source:
        possible_markers = (
            'app = ApplicationBuilder().token(TOKEN).build()\n',
            'app = Application.builder().token(TOKEN).build()\n',
        )

        inserted = False
        for marker in possible_markers:
            if marker in source:
                source = source.replace(
                    marker,
                    marker + "\n# Modular case handlers must be registered before legacy handlers.\n" + CALL_LINE,
                    1,
                )
                inserted = True
                break

        if not inserted:
            print(
                "ERROR: Could not locate the Telegram Application creation line. "
                "No changes were written."
            )
            return 1

    if not BACKUP_PATH.exists():
        shutil.copy2(BOT_PATH, BACKUP_PATH)

    BOT_PATH.write_text(source, encoding="utf-8")

    print("SUCCESS: Modular case handlers activated.")
    print(f"Backup created: {BACKUP_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
