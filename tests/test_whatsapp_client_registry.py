import sys
import types
import unittest
from unittest.mock import patch


psycopg2 = types.ModuleType("psycopg2")
extras = types.ModuleType("psycopg2.extras")
extras.RealDictCursor = object
psycopg2.extras = extras
sys.modules.setdefault("psycopg2", psycopg2)
sys.modules.setdefault("psycopg2.extras", extras)

config = types.ModuleType("config")
config.DATABASE_URL = "postgresql://unused"
sys.modules.setdefault("config", config)

from services.whatsapp_client_registry import (  # noqa: E402
    list_registered_clients,
    mask_phone,
    normalize_registry_phone,
    registered_client_detail,
    registry_token,
)


ROWS = [
    {
        "phone": "919876543210", "token": registry_token("919876543210"),
        "client_name": "Ajay Client", "case_count": 2,
        "cases": [{"case_number": "CS/1", "case_title": "A v B"}],
        "ad_client_id": "ad-1",
    },
    {
        "phone": "919988877665", "token": registry_token("919988877665"),
        "client_name": "Second Client", "case_count": 1,
        "cases": [{"case_number": "CRM/2", "case_title": "State v C"}],
        "ad_client_id": None,
    },
]


class WhatsAppClientRegistryTests(unittest.TestCase):
    def test_number_normalization_and_masking(self):
        self.assertEqual(normalize_registry_phone("98765 43210"), "919876543210")
        self.assertEqual(mask_phone("919876543210"), "+91 ***** 3210")
        self.assertNotIn("98765", mask_phone("919876543210"))

    def test_registry_token_does_not_contain_phone(self):
        token = registry_token("919876543210")
        self.assertEqual(len(token), 16)
        self.assertNotIn("9876543210", token)

    def test_search_matches_client_case_and_phone(self):
        with patch(
            "services.whatsapp_client_registry._fetch_registry_rows",
            return_value=ROWS,
        ):
            self.assertEqual(list_registered_clients(search="Ajay")["total"], 1)
            self.assertEqual(list_registered_clients(search="CRM/2")["total"], 1)
            self.assertEqual(list_registered_clients(search="77665")["total"], 1)

    def test_pagination_is_bounded(self):
        with patch(
            "services.whatsapp_client_registry._fetch_registry_rows",
            return_value=ROWS,
        ):
            result = list_registered_clients(page=99, page_size=1)
            self.assertEqual(result["page"], 2)
            self.assertEqual(result["pages"], 2)

    def test_detail_requires_hashed_registry_token(self):
        with patch(
            "services.whatsapp_client_registry._fetch_registry_rows",
            return_value=ROWS,
        ):
            self.assertIsNone(registered_client_detail("919876543210"))
            self.assertEqual(
                registered_client_detail(registry_token("919876543210"))["client_name"],
                "Ajay Client",
            )


if __name__ == "__main__":
    unittest.main()
