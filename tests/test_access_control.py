import os
import unittest
from unittest.mock import patch

from ai.config import AIConfig
from services.access_policy import (
    required_level_for_callback,
    required_level_for_command,
    resolve_identity,
)


class AccessPolicyTests(unittest.TestCase):
    def test_public_bootstrap_commands_remain_available(self):
        for command in ("/start", "/office", "/linkstaff", "/cancel"):
            self.assertEqual(required_level_for_command(command), "unlinked")

    def test_legacy_writes_are_supervisor_only(self):
        for command in (
            "/closecase", "/addpayment", "/addnote", "/assignwork",
            "/addtimeline", "/casefolder",
        ):
            self.assertEqual(required_level_for_command(command), "supervisor")

    def test_external_sync_and_diagnostics_are_admin_only(self):
        for command in (
            "/synccases", "/synctimeline", "/ecourts", "/testweb",
            "/debugcasejson",
        ):
            self.assertEqual(required_level_for_command(command), "admin")

    def test_sensitive_callbacks_are_gated(self):
        self.assertEqual(required_level_for_callback("ecr:sync"), "admin")
        self.assertEqual(required_level_for_callback("ejg:review:1"), "admin")
        self.assertEqual(required_level_for_callback("los:status"), "supervisor")
        self.assertEqual(required_level_for_callback("comm:api:4"), "supervisor")
        self.assertEqual(required_level_for_callback("s13:works:all"), "supervisor")
        self.assertEqual(required_level_for_callback("s13:finance:4"), "supervisor")
        self.assertEqual(required_level_for_callback("s13:complete:4"), "staff")
        self.assertEqual(required_level_for_callback("pfs:3:BROUGHT"), "staff")

    @patch.dict(os.environ, {"ADMIN_USER_ID": "5676006099"}, clear=False)
    def test_configured_admin_does_not_depend_on_database_profile(self):
        identity = resolve_identity(5676006099)
        self.assertEqual(identity.level, "admin")
        self.assertTrue(identity.linked)

    @patch.dict(
        os.environ,
        {
            "ADMIN_USER_ID": "5676006099",
            "AI_ADMIN_USER_IDS": "",
            "ADMIN_CHAT_ID": "-100999",
        },
        clear=False,
    )
    def test_ai_uses_sender_admin_id_not_group_chat_id(self):
        config = AIConfig.from_env()
        self.assertIn(5676006099, config.admin_user_ids)
        self.assertNotIn(-100999, config.admin_user_ids)


if __name__ == "__main__":
    unittest.main()
