import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from database import Database


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.db = Database(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def test_create_update_and_history(self):
        task = self.db.create_task({"title": "Angebot", "assignee": "Müller", "due_date": "2026-10-03"})
        self.assertEqual(task["id"], 1)
        self.assertEqual(task["assignee"], "Müller")
        changed = self.db.update_task(task["id"], {"title": "Angebot prüfen"})
        self.assertEqual(changed["title"], "Angebot prüfen")
        self.assertEqual([x["action"] for x in self.db.history(1)], ["updated", "created"])

    def test_change_and_history_are_atomic(self):
        task = self.db.create_task({"title": "Alt"})
        with patch.object(self.db, "_history", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.db.update_task(task["id"], {"title": "Neu"})
        self.assertEqual(self.db.get_task(task["id"])["title"], "Alt")

    def test_complete_reopen_delete_restore(self):
        task = self.db.create_task()
        task = self.db.update_task(task["id"], {"completed": True})
        self.assertTrue(task["completed"])
        self.assertIsNotNone(task["completed_at"])
        task = self.db.update_task(task["id"], {"completed": False})
        self.assertFalse(task["completed"])
        self.assertIsNone(task["completed_at"])
        task = self.db.delete_task(task["id"])
        self.assertIsNotNone(task["deleted_at"])
        task = self.db.restore_task(task["id"])
        self.assertIsNone(task["deleted_at"])
        actions = [x["action"] for x in self.db.history(task["id"])]
        self.assertEqual(actions[:4], ["restored", "deleted", "reopened", "completed"])

    def test_normalized_values_and_tags(self):
        task = self.db.create_task({"assignee": "Ada", "project": "Website", "category": "Arbeit", "tags": ["Schnell", "Kunde"]})
        self.assertEqual(task["project"], "Website")
        self.assertEqual(task["category"], "Arbeit")
        self.assertEqual(task["tags"], ["Kunde", "Schnell"])
        lookups = self.db.lookups()
        self.assertEqual(lookups["assignees"][0]["name"], "Ada")
        self.assertTrue(lookups["assignees"][0]["color"].startswith("#"))
        self.db.update_task(task["id"], {"tags": ["Neu"]})
        self.assertEqual(self.db.get_task(task["id"])["tags"], ["Neu"])

    def test_dependencies_and_cycles(self):
        one = self.db.create_task({"title": "Eins"})
        two = self.db.create_task({"title": "Zwei"})
        three = self.db.create_task({"title": "Drei"})
        self.db.update_task(two["id"], {"dependencies": [one["id"]]})
        self.db.update_task(three["id"], {"dependencies": [one["id"], two["id"]]})
        self.assertEqual(self.db.get_task(three["id"])["dependencies"], [1, 2])
        with self.assertRaisesRegex(ValueError, "selbst"):
            self.db.update_task(one["id"], {"dependencies": [one["id"]]})
        with self.assertRaisesRegex(ValueError, "Zyklus"):
            self.db.update_task(one["id"], {"dependencies": [two["id"]]})
        with self.assertRaisesRegex(ValueError, "Zyklus"):
            self.db.update_task(one["id"], {"dependencies": [three["id"]]})
        self.assertEqual(self.db.get_task(one["id"])["dependencies"], [])

    def test_backup_contains_all_data(self):
        task = self.db.create_task({"title": "Gesichert", "tags": ["Backup"]})
        self.db.update_settings({"view": "timeline"})
        path = self.db.backup()
        try:
            copy = Database(path)
            self.assertEqual(copy.get_task(task["id"])["tags"], ["Backup"])
            self.assertEqual(copy.settings()["view"], "timeline")
            self.assertEqual(len(copy.history(task["id"])), 1)
        finally:
            os.unlink(path)

    def test_ids_are_not_reused(self):
        one = self.db.create_task()
        self.db.delete_task(one["id"])
        two = self.db.create_task()
        self.assertGreater(two["id"], one["id"])


if __name__ == "__main__":
    unittest.main()
