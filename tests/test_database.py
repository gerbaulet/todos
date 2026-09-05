import os
import sqlite3
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

from database import Database, due_end, due_start, format_due, shifted_due_range


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

    def test_milestones_support_optional_due_with_each_precision(self):
        self.assertFalse(self.db.create_task({"title": "Normal"})["is_milestone"])
        cases = [("exact", "2026-10-15"), ("week", "2026-W42"), ("month", "2026-10"),
                 ("quarter", "2026-Q4"), ("year", "2027")]
        for due_type, due_value in cases:
            with self.subTest(due_type=due_type):
                task = self.db.create_task({"title": due_type, "is_milestone": True,
                                            "due_type": due_type, "due_value": due_value})
                self.assertTrue(task["is_milestone"])
        undated = self.db.create_task({"title": "Ohne", "is_milestone": True})
        self.assertTrue(undated["is_milestone"])
        self.assertIsNone(undated["due_type"])

    def test_milestone_conversion_due_removal_and_history(self):
        undated = self.db.create_task({"title": "Ohne Termin"})
        undated = self.db.update_task(undated["id"], {"is_milestone": True})
        self.assertTrue(undated["is_milestone"])
        dated = self.db.create_task({"title": "Mit Termin", "due_type": "quarter", "due_value": "2027-Q2"})
        dated = self.db.update_task(dated["id"], {"is_milestone": True})
        self.assertTrue(dated["is_milestone"])
        dated = self.db.update_task(dated["id"], {"due_type": None, "due_value": None})
        self.assertTrue(dated["is_milestone"])
        dated = self.db.update_task(dated["id"], {"is_milestone": False})
        self.assertFalse(dated["is_milestone"])
        self.assertIsNone(dated["due_type"])
        milestone_changes = [x for x in self.db.history(dated["id"]) if x["field"] == "is_milestone"]
        self.assertEqual([(x["old_value"], x["new_value"]) for x in reversed(milestone_changes)], [("False", "True"), ("True", "False")])

    def test_notes_history_and_lifecycle(self):
        full_old = "Erste Zeile\nZweite Zeile"
        full_new = "Abstimmung mit Legal abgeschlossen\nFreigabe folgt"
        task = self.db.create_task({"title": "Vertrag prüfen", "notes": full_old})
        self.assertEqual(task["notes"], full_old)
        task = self.db.update_task(task["id"], {"notes": full_new})
        change = next(x for x in self.db.history(task["id"]) if x["field"] == "notes")
        self.assertEqual((change["old_value"], change["new_value"]), (full_old, full_new))
        self.db.delete_task(task["id"])
        self.db.restore_task(task["id"])
        self.assertEqual(self.db.get_task(task["id"])["notes"], full_new)
        cleared = self.db.update_task(task["id"], {"notes": ""})
        self.assertEqual(cleared["notes"], "")
        milestone = self.db.create_task({"title": "M", "notes": "Notiz", "is_milestone": True,
                                         "due_type": "year", "due_value": "2028"})
        self.assertEqual(milestone["notes"], "Notiz")

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

    def test_list_tasks_loads_relations_in_three_queries(self):
        one = self.db.create_task({"title": "Eins", "tags": ["Beta", "alpha"]})
        two = self.db.create_task({"title": "Zwei", "dependencies": [one["id"]]})
        deleted = self.db.create_task({"title": "Gelöscht", "tags": ["Archiv"]})
        self.db.delete_task(deleted["id"])

        statements = []
        original_connect = self.db.connect

        def traced_connect():
            db = original_connect()
            db.set_trace_callback(statements.append)
            return db

        with patch.object(self.db, "connect", side_effect=traced_connect):
            tasks = self.db.list_tasks(include_deleted=False)

        self.assertEqual([task["id"] for task in tasks], [one["id"], two["id"]])
        self.assertEqual(tasks[0]["tags"], ["alpha", "Beta"])
        self.assertEqual([x["depends_on_task_id"] for x in tasks[1]["dependencies"]], [one["id"]])
        selects = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]
        self.assertEqual(len(selects), 3)

    def test_dependencies_and_cycles(self):
        one = self.db.create_task({"title": "Eins"})
        two = self.db.create_task({"title": "Zwei"})
        three = self.db.create_task({"title": "Drei"})
        self.db.update_task(two["id"], {"dependencies": [one["id"]]})
        self.db.update_task(three["id"], {"dependencies": [one["id"], two["id"]]})
        self.assertEqual([x["depends_on_task_id"] for x in self.db.get_task(three["id"])["dependencies"]], [1, 2])
        with self.assertRaisesRegex(ValueError, "selbst"):
            self.db.update_task(one["id"], {"dependencies": [one["id"]]})
        with self.assertRaisesRegex(ValueError, "Zyklus"):
            self.db.update_task(one["id"], {"dependencies": [two["id"]]})
        with self.assertRaisesRegex(ValueError, "Zyklus"):
            self.db.update_task(one["id"], {"dependencies": [three["id"]]})
        self.assertEqual(self.db.get_task(one["id"])["dependencies"], [])

    def test_due_ranges_and_formatting(self):
        cases = [
            ("exact", "2027-09-15", "2027-09-15", "2027-09-15", "15.09.2027"),
            ("week", "2027-W33", "2027-08-16", "2027-08-22", "KW 33 / 2027"),
            ("month", "2027-09", "2027-09-01", "2027-09-30", "September 2027"),
            ("quarter", "2027-Q2", "2027-04-01", "2027-06-30", "Q2 2027"),
            ("year", "2027", "2027-01-01", "2027-12-31", "2027"),
            ("month", "2028-02", "2028-02-01", "2028-02-29", "Februar 2028"),
            ("week", "2020-W53", "2020-12-28", "2021-01-03", "KW 53 / 2020"),
        ]
        for kind, value, start, end, label in cases:
            with self.subTest(value=value):
                self.assertEqual(due_start(kind, value).isoformat(), start)
                self.assertEqual(due_end(kind, value).isoformat(), end)
                self.assertEqual(format_due(kind, value), label)

    def test_shifted_due_ranges_keep_the_original_precision_window(self):
        cases = [
            ("exact", "2027-09-15", 3, "day", "2027-09-18", "2027-09-18"),
            ("exact", "2027-09-15", 2, "week", "2027-09-29", "2027-09-29"),
            ("week", "2027-W33", 1, "week", "2027-08-23", "2027-08-29"),
            ("month", "2026-09", 2, "week", "2026-09-15", "2026-10-14"),
            ("month", "2027-01", 1, "month", "2027-02-01", "2027-02-28"),
            ("quarter", "2027-Q2", 1, "month", "2027-05-01", "2027-07-31"),
            ("year", "2027", 1, "year", "2028-01-01", "2028-12-31"),
        ]
        for kind, value, offset, unit, start, end in cases:
            with self.subTest(value=value, unit=unit):
                actual = shifted_due_range(kind, value, offset, unit)
                self.assertEqual(tuple(x.isoformat() for x in actual), (start, end))

    def test_dependency_offsets_recommendations_and_history_are_separate(self):
        predecessor = self.db.create_task({"title": "Vorgänger", "due_type": "month", "due_value": "2026-09"})
        child = self.db.create_task({"title": "Kind"})
        child = self.db.update_task(child["id"], {"dependencies": [{"depends_on_task_id": predecessor["id"], "offset_value": 2, "offset_unit": "week"}]})
        self.assertIsNone(child["due_type"])
        self.assertEqual(child["dependencies"][0]["recommended_start"], "2026-09-15")
        self.assertEqual(child["dependencies"][0]["recommended_end"], "2026-10-14")
        with self.db.read() as raw:
            columns = {row["name"] for row in raw.execute("PRAGMA table_info(dependencies)")}
            stored = dict(raw.execute("SELECT * FROM dependencies WHERE task_id=?", (child["id"],)).fetchone())
        self.assertNotIn("recommended_start", columns)
        self.assertEqual((stored["offset_value"], stored["offset_unit"]), (2, "week"))
        before = {key: child[key] for key in ("due_type", "due_value", "updated_at")}
        child_history_count = len(self.db.history(child["id"]))
        self.db.update_task(predecessor["id"], {"due_type": "month", "due_value": "2026-10"})
        changed = self.db.get_task(child["id"])
        self.assertEqual({key: changed[key] for key in before}, before)
        self.assertEqual(len(self.db.history(child["id"])), child_history_count)
        self.assertEqual(changed["dependencies"][0]["recommended_start"], "2026-10-15")
        self.assertEqual(changed["dependencies"][0]["recommended_end"], "2026-11-14")

    def test_due_and_dependency_changes_are_historized(self):
        predecessor = self.db.create_task({"due_type": "year", "due_value": "2027"})
        child = self.db.create_task({"due_type": "month", "due_value": "2027-09"})
        self.db.update_task(child["id"], {"dependencies": [{"depends_on_task_id": predecessor["id"], "offset_value": 1, "offset_unit": "year"}]})
        self.db.update_task(child["id"], {"dependencies": [{"depends_on_task_id": predecessor["id"], "offset_value": 2, "offset_unit": "year"}]})
        self.db.update_task(child["id"], {"dependencies": []})
        self.db.update_task(child["id"], {"due_type": "quarter", "due_value": "2027-Q4"})
        fields = [item["field"] for item in self.db.history(child["id"])]
        self.assertEqual(fields.count("dependencies"), 3)
        self.assertIn("due", fields)

    def test_legacy_schema_migrates_dates_and_dependencies(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite"); os.close(fd)
        try:
            raw = sqlite3.connect(path)
            raw.executescript("""CREATE TABLE tasks(id INTEGER PRIMARY KEY,title TEXT NOT NULL DEFAULT '',assignee_id INTEGER,due_date TEXT,completed INTEGER NOT NULL DEFAULT 0,completed_at TEXT,project_id INTEGER,category_id INTEGER,link TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,deleted_at TEXT);
                CREATE TABLE dependencies(task_id INTEGER NOT NULL,depends_on_id INTEGER NOT NULL,PRIMARY KEY(task_id,depends_on_id));
                INSERT INTO tasks VALUES(1,'Alt',NULL,'2027-09-15',0,NULL,NULL,NULL,'','x','x',NULL);
                INSERT INTO tasks VALUES(2,'Kind',NULL,NULL,0,NULL,NULL,NULL,'','x','x',NULL);
                INSERT INTO dependencies VALUES(2,1);""")
            raw.commit(); raw.close()
            migrated = Database(path)
            self.assertEqual((migrated.get_task(1)["due_type"], migrated.get_task(1)["due_value"]), ("exact", "2027-09-15"))
            self.assertFalse(migrated.get_task(1)["is_milestone"])
            self.assertEqual(migrated.get_task(1)["notes"], "")
            dependency = migrated.get_task(2)["dependencies"][0]
            self.assertEqual((dependency["depends_on_task_id"], dependency["offset_value"]), (1, None))
        finally:
            os.unlink(path)

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

    def test_unused_lookups_are_removed_but_deleted_task_references_count(self):
        first = self.db.create_task({"assignee": "Falsch", "project": "Alt", "category": "Alt", "tags": ["Tippfeler"]})
        second = self.db.create_task({"assignee": "Falsch", "project": "Falsch", "category": "Alt", "tags": ["Tippfeler"]})
        self.db.delete_task(second["id"])
        self.db.update_task(first["id"], {"assignee": "Richtig", "project": "Richtig", "category": "Neu", "tags": ["Korrekt"]})
        lookups = self.db.lookups()
        self.assertEqual([x["name"] for x in lookups["assignees"]], ["Falsch", "Richtig"])
        self.assertEqual([x["name"] for x in lookups["projects"]], ["Falsch", "Richtig"])
        self.assertEqual([x["name"] for x in lookups["categories"]], ["Alt", "Neu"])
        self.assertEqual([x["name"] for x in lookups["tags"]], ["Korrekt", "Tippfeler"])
        self.db.update_task(second["id"], {"assignee": "Richtig", "project": "Richtig", "category": "Neu", "tags": ["Korrekt"]})
        lookups = self.db.lookups()
        self.assertEqual([x["name"] for x in lookups["assignees"]], ["Richtig"])
        self.assertEqual([x["name"] for x in lookups["projects"]], ["Richtig"])
        self.assertEqual([x["name"] for x in lookups["categories"]], ["Neu"])
        self.assertEqual([x["name"] for x in lookups["tags"]], ["Korrekt"])


if __name__ == "__main__":
    unittest.main()
