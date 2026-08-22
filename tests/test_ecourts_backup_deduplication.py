import unittest
import sys
import types

# Keep this unit test runnable without production-only database/Drive packages.
psycopg2 = types.ModuleType("psycopg2")
psycopg2_extras = types.ModuleType("psycopg2.extras")
psycopg2_extras.Json = object
psycopg2_extras.execute_values = lambda *args, **kwargs: None
psycopg2.extras = psycopg2_extras
sys.modules.setdefault("psycopg2", psycopg2)
sys.modules.setdefault("psycopg2.extras", psycopg2_extras)

googleapiclient = types.ModuleType("googleapiclient")
google_http = types.ModuleType("googleapiclient.http")
google_http.MediaIoBaseDownload = object
google_http.MediaIoBaseUpload = object
googleapiclient.http = google_http
sys.modules.setdefault("googleapiclient", googleapiclient)
sys.modules.setdefault("googleapiclient.http", google_http)

config = types.ModuleType("config")
config.DATABASE_URL = "postgresql://test"
sys.modules.setdefault("config", config)
drive = types.ModuleType("utils.drive")
drive.get_drive_service = lambda: None
drive.ROOT_FOLDER_ID = "test"
sys.modules.setdefault("utils.drive", drive)

from services.ecourts_backup_service import deduplicate_backup_records


def _record(cino, updated, next_date=None, purpose=None):
    return {
        "cino": cino,
        "source_kind": "DISTRICT",
        "next_hearing_date": next_date,
        "last_hearing_date": None,
        "decision_date": None,
        "purpose_name": purpose,
        "disposal_name": None,
        "court_designation": None,
        "petitioner_name": None,
        "respondent_name": None,
        "raw_payload": {"updated": updated},
    }


class ECourtsBackupDeduplicationTests(unittest.TestCase):
    def test_newest_duplicate_cnr_is_retained(self):
        old = _record("PBLD020092092019", "2026-08-20T08:00:00Z", "2026-08-11")
        new = _record("PBLD020092092019", "2026-08-21T08:00:00Z", "2026-09-15")

        rows, duplicate_count = deduplicate_backup_records([old, new])

        self.assertEqual(duplicate_count, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["next_hearing_date"], "2026-09-15")

    def test_later_more_complete_row_wins_without_timestamp(self):
        sparse = _record("PBLD020017252018", None)
        complete = _record(
            "PBLD020017252018", None, "2026-08-07", "Consideration"
        )

        rows, duplicate_count = deduplicate_backup_records([sparse, complete])

        self.assertEqual(duplicate_count, 1)
        self.assertEqual(rows, [complete])

    def test_distinct_cnrs_are_unchanged(self):
        first = _record("PBLD020017252018", None)
        second = _record("PBLD020092092019", None)

        rows, duplicate_count = deduplicate_backup_records([first, second])

        self.assertEqual(duplicate_count, 0)
        self.assertEqual(rows, [first, second])


if __name__ == "__main__":
    unittest.main()
