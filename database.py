import json
import os
import re
import sqlite3
import tempfile
import calendar
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS assignees (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, color TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS tags (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL DEFAULT '',
  assignee_id INTEGER REFERENCES assignees(id),
  due_date TEXT CHECK(due_date IS NULL OR due_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  due_type TEXT CHECK(due_type IS NULL OR due_type IN ('exact','week','month','quarter','year')),
  due_value TEXT,
  completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0,1)),
  completed_at TEXT,
  project_id INTEGER REFERENCES projects(id),
  category_id INTEGER REFERENCES categories(id),
  link TEXT NOT NULL DEFAULT '',
  is_milestone INTEGER NOT NULL DEFAULT 0 CHECK(is_milestone IN (0,1)),
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS task_tags (
  task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  tag_id INTEGER NOT NULL REFERENCES tags(id), PRIMARY KEY(task_id, tag_id)
);
CREATE TABLE IF NOT EXISTS dependencies (
  task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  depends_on_task_id INTEGER NOT NULL REFERENCES tasks(id),
  offset_value INTEGER,
  offset_unit TEXT CHECK(offset_unit IS NULL OR offset_unit IN ('day','week','month','year')),
  PRIMARY KEY(task_id, depends_on_task_id), CHECK(task_id <> depends_on_task_id),
  CHECK((offset_value IS NULL) = (offset_unit IS NULL))
);
CREATE TABLE IF NOT EXISTS history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL REFERENCES tasks(id), timestamp TEXT NOT NULL,
  action TEXT NOT NULL, field TEXT, old_value TEXT, new_value TEXT
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS history_task_time ON history(task_id, timestamp DESC, id DESC);
CREATE INDEX IF NOT EXISTS tasks_due ON tasks(due_date);
"""

COLORS = ["#2563eb", "#dc2626", "#059669", "#7c3aed", "#d97706", "#0891b2", "#be185d", "#4d7c0f"]
FIELDS = {"title", "assignee", "due_date", "due_type", "due_value", "completed", "project", "category", "link", "tags", "dependencies", "is_milestone", "notes"}
DEFAULT_DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "todo.sqlite")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def due_start(due_type, due_value):
    """Return the first possible date for a stored due value."""
    if not due_type or not due_value:
        return None
    if due_type == "exact":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due_value):
            raise ValueError("Ungültiges Datum.")
        return date.fromisoformat(due_value)
    if due_type == "week":
        match = re.fullmatch(r"(\d{4})-W(\d{2})", due_value)
        if not match:
            raise ValueError("Ungültige Kalenderwoche.")
        return date.fromisocalendar(int(match[1]), int(match[2]), 1)
    if due_type == "month":
        match = re.fullmatch(r"(\d{4})-(\d{2})", due_value)
        if not match:
            raise ValueError("Ungültiger Monat.")
        return date(int(match[1]), int(match[2]), 1)
    if due_type == "quarter":
        match = re.fullmatch(r"(\d{4})-Q([1-4])", due_value)
        if not match:
            raise ValueError("Ungültiges Quartal.")
        return date(int(match[1]), (int(match[2]) - 1) * 3 + 1, 1)
    if due_type == "year" and re.fullmatch(r"\d{4}", due_value):
        return date(int(due_value), 1, 1)
    raise ValueError("Ungültiger Termintyp oder Terminwert.")


def due_end(due_type, due_value):
    start = due_start(due_type, due_value)
    if start is None or due_type == "exact":
        return start
    if due_type == "week":
        return start + timedelta(days=6)
    if due_type == "month":
        return date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
    if due_type == "quarter":
        month = start.month + 2
        return date(start.year, month, calendar.monthrange(start.year, month)[1])
    return date(start.year, 12, 31)


def format_due(due_type, due_value):
    if not due_type or not due_value:
        return ""
    start = due_start(due_type, due_value)
    if due_type == "exact":
        return start.strftime("%d.%m.%Y")
    if due_type == "week":
        year, week, _ = start.isocalendar()
        return f"KW {week} / {year}"
    if due_type == "month":
        names = ("", "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember")
        return f"{names[start.month]} {start.year}"
    if due_type == "quarter":
        return f"Q{(start.month - 1) // 3 + 1} {start.year}"
    return str(start.year)


def _add_months(value, months):
    index = value.year * 12 + value.month - 1 + months
    year, month0 = divmod(index, 12)
    target_last = calendar.monthrange(year, month0 + 1)[1]
    day = target_last if value.day == calendar.monthrange(value.year, value.month)[1] else min(value.day, target_last)
    return date(year, month0 + 1, day)


def shifted_due_range(due_type, due_value, offset_value, offset_unit):
    start, end = due_start(due_type, due_value), due_end(due_type, due_value)
    if start is None or offset_value is None or offset_unit is None:
        return None
    if offset_unit in ("day", "week"):
        delta = timedelta(days=offset_value * (7 if offset_unit == "week" else 1))
        return start + delta, end + delta
    months = offset_value * (12 if offset_unit == "year" else 1)
    return _add_months(start, months), _add_months(end, months)


class Database:
    def __init__(self, path=DEFAULT_DATABASE):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with self.read() as db:
            db.executescript(SCHEMA)
            self._migrate(db)
            self._cleanup_lookups(db)

    @staticmethod
    def _migrate(db):
        task_columns = {row["name"] for row in db.execute("PRAGMA table_info(tasks)")}
        if "due_type" not in task_columns:
            db.execute("ALTER TABLE tasks ADD COLUMN due_type TEXT")
        if "due_value" not in task_columns:
            db.execute("ALTER TABLE tasks ADD COLUMN due_value TEXT")
        if "is_milestone" not in task_columns:
            db.execute("ALTER TABLE tasks ADD COLUMN is_milestone INTEGER NOT NULL DEFAULT 0")
        if "notes" not in task_columns:
            db.execute("ALTER TABLE tasks ADD COLUMN notes TEXT NOT NULL DEFAULT ''")
        db.execute("UPDATE tasks SET due_type='exact',due_value=due_date WHERE due_date IS NOT NULL AND due_type IS NULL")
        dep_columns = {row["name"] for row in db.execute("PRAGMA table_info(dependencies)")}
        if "depends_on_task_id" not in dep_columns:
            db.execute("ALTER TABLE dependencies RENAME TO dependencies_legacy")
            db.execute("""CREATE TABLE dependencies (
              task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
              depends_on_task_id INTEGER NOT NULL REFERENCES tasks(id), offset_value INTEGER,
              offset_unit TEXT CHECK(offset_unit IS NULL OR offset_unit IN ('day','week','month','year')),
              PRIMARY KEY(task_id,depends_on_task_id), CHECK(task_id<>depends_on_task_id),
              CHECK((offset_value IS NULL)=(offset_unit IS NULL)))""")
            db.execute("INSERT INTO dependencies(task_id,depends_on_task_id) SELECT task_id,depends_on_id FROM dependencies_legacy")
            db.execute("DROP TABLE dependencies_legacy")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    @contextmanager
    def read(self):
        db = self.connect()
        try:
            yield db
            db.commit()
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _lookup(db, table, value, color=False):
        value = (value or "").strip()
        if not value:
            return None
        row = db.execute(f"SELECT id FROM {table} WHERE name = ? COLLATE NOCASE", (value,)).fetchone()
        if row:
            return row["id"]
        if color:
            count = db.execute("SELECT COUNT(*) FROM assignees").fetchone()[0]
            cur = db.execute("INSERT INTO assignees(name,color) VALUES (?,?)", (value, COLORS[count % len(COLORS)]))
        else:
            cur = db.execute(f"INSERT INTO {table}(name) VALUES (?)", (value,))
        return cur.lastrowid

    @staticmethod
    def _history(db, task_id, action, field=None, old=None, new=None):
        def text(value):
            if value is None:
                return None
            if isinstance(value, (list, dict)):
                return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            return str(value)
        db.execute(
            "INSERT INTO history(task_id,timestamp,action,field,old_value,new_value) VALUES (?,?,?,?,?,?)",
            (task_id, now(), action, field, text(old), text(new)),
        )

    @staticmethod
    def _cleanup_lookups(db):
        db.execute("DELETE FROM assignees WHERE NOT EXISTS (SELECT 1 FROM tasks WHERE tasks.assignee_id=assignees.id)")
        db.execute("DELETE FROM projects WHERE NOT EXISTS (SELECT 1 FROM tasks WHERE tasks.project_id=projects.id)")
        db.execute("DELETE FROM categories WHERE NOT EXISTS (SELECT 1 FROM tasks WHERE tasks.category_id=categories.id)")
        db.execute("DELETE FROM tags WHERE NOT EXISTS (SELECT 1 FROM task_tags WHERE task_tags.tag_id=tags.id)")

    @staticmethod
    def _validate_due(due_type, due_value):
        if due_type in (None, "") and due_value in (None, ""):
            return None, None
        if due_type not in {"exact", "week", "month", "quarter", "year"} or not isinstance(due_value, str):
            raise ValueError("Ungültiger Termin.")
        try:
            due_start(due_type, due_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Ungültiger Termin.") from exc
        return due_type, due_value

    @classmethod
    def _due_from_data(cls, data, old=None):
        if "due_type" in data or "due_value" in data:
            return cls._validate_due(data.get("due_type"), data.get("due_value"))
        if "due_date" in data:
            value = data.get("due_date")
            if value in (None, ""):
                return None, None
            try:
                return cls._validate_due("exact", value)
            except ValueError as exc:
                raise ValueError("Ungültiges Datum; erwartet wird YYYY-MM-DD.") from exc
        return (old["due_type"], old["due_value"]) if old else (None, None)

    def create_task(self, data=None):
        data = data or {}
        unknown = set(data) - FIELDS
        if unknown:
            raise ValueError("Unbekannte Felder: " + ", ".join(sorted(unknown)))
        stamp = now()
        with self.transaction() as db:
            assignee_id = self._lookup(db, "assignees", data.get("assignee"), True)
            project_id = self._lookup(db, "projects", data.get("project"))
            category_id = self._lookup(db, "categories", data.get("category"))
            completed = int(bool(data.get("completed", False)))
            due_type, due_value = self._due_from_data(data)
            is_milestone = bool(data.get("is_milestone", False))
            cur = db.execute(
                """INSERT INTO tasks(title,assignee_id,due_date,due_type,due_value,completed,completed_at,project_id,category_id,link,is_milestone,notes,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ((data.get("title") or "").strip(), assignee_id, due_value if due_type == "exact" else None, due_type, due_value, completed,
                 stamp if completed else None, project_id, category_id, (data.get("link") or "").strip(), int(is_milestone),
                 str(data.get("notes") or ""), stamp, stamp),
            )
            task_id = cur.lastrowid
            self._set_tags(db, task_id, data.get("tags", []))
            self._set_dependencies(db, task_id, data.get("dependencies", []))
            self._history(db, task_id, "created", None, None, None)
        return self.get_task(task_id)

    def _task_in_db(self, db, task_id):
        row = db.execute("""SELECT t.*, a.name assignee, p.name project, c.name category
            FROM tasks t LEFT JOIN assignees a ON a.id=t.assignee_id
            LEFT JOIN projects p ON p.id=t.project_id LEFT JOIN categories c ON c.id=t.category_id
            WHERE t.id=?""", (task_id,)).fetchone()
        if not row:
            raise KeyError("Task nicht gefunden.")
        result = dict(row)
        result["completed"] = bool(result["completed"])
        result["is_milestone"] = bool(result["is_milestone"])
        result["due_date"] = result["due_value"] if result["due_type"] == "exact" else None
        result["due_start"] = due_start(result["due_type"], result["due_value"]).isoformat() if result["due_type"] else None
        result["due_end"] = due_end(result["due_type"], result["due_value"]).isoformat() if result["due_type"] else None
        result["due_display"] = format_due(result["due_type"], result["due_value"])
        result["tags"] = [r[0] for r in db.execute("SELECT g.name FROM tags g JOIN task_tags x ON x.tag_id=g.id WHERE x.task_id=? ORDER BY g.name COLLATE NOCASE", (task_id,))]
        deps = [dict(r) for r in db.execute("""SELECT d.depends_on_task_id,d.offset_value,d.offset_unit,
            t.due_type predecessor_due_type,t.due_value predecessor_due_value
            FROM dependencies d JOIN tasks t ON t.id=d.depends_on_task_id
            WHERE d.task_id=? ORDER BY d.depends_on_task_id""", (task_id,))]
        result["dependencies"] = self._recommendations(deps, result)
        return result

    def get_task(self, task_id):
        with self.read() as db:
            return self._task_in_db(db, task_id)

    def list_tasks(self, include_deleted=True):
        with self.read() as db:
            where = "" if include_deleted else " WHERE t.deleted_at IS NULL"
            rows = db.execute("""SELECT t.*, a.name assignee, p.name project, c.name category
                FROM tasks t LEFT JOIN assignees a ON a.id=t.assignee_id
                LEFT JOIN projects p ON p.id=t.project_id LEFT JOIN categories c ON c.id=t.category_id"""
                + where + " ORDER BY t.id").fetchall()
            tasks = []
            by_id = {}
            for row in rows:
                task = dict(row)
                task["completed"] = bool(task["completed"])
                task["is_milestone"] = bool(task["is_milestone"])
                task["due_date"] = task["due_value"] if task["due_type"] == "exact" else None
                task["due_start"] = due_start(task["due_type"], task["due_value"]).isoformat() if task["due_type"] else None
                task["due_end"] = due_end(task["due_type"], task["due_value"]).isoformat() if task["due_type"] else None
                task["due_display"] = format_due(task["due_type"], task["due_value"])
                task["tags"] = []
                task["dependencies"] = []
                tasks.append(task)
                by_id[task["id"]] = task

            relation_where = "" if include_deleted else " WHERE t.deleted_at IS NULL"
            for row in db.execute("""SELECT x.task_id, g.name
                FROM task_tags x JOIN tags g ON g.id=x.tag_id JOIN tasks t ON t.id=x.task_id"""
                + relation_where + " ORDER BY x.task_id, g.name COLLATE NOCASE"):
                by_id[row["task_id"]]["tags"].append(row["name"])
            for row in db.execute("""SELECT d.task_id,d.depends_on_task_id,d.offset_value,d.offset_unit,
                p.due_type predecessor_due_type,p.due_value predecessor_due_value
                FROM dependencies d JOIN tasks t ON t.id=d.task_id JOIN tasks p ON p.id=d.depends_on_task_id"""
                + relation_where + " ORDER BY d.task_id,d.depends_on_task_id"):
                by_id[row["task_id"]]["dependencies"].append(dict(row))
            for task in tasks:
                task["dependencies"] = self._recommendations(task["dependencies"], task)
            return tasks

    @staticmethod
    def _recommendations(dependencies, task):
        own_start = due_start(task.get("due_type"), task.get("due_value"))
        own_end = due_end(task.get("due_type"), task.get("due_value"))
        result = []
        for dependency in dependencies:
            item = {key: dependency.get(key) for key in ("depends_on_task_id", "offset_value", "offset_unit")}
            shifted = shifted_due_range(dependency.get("predecessor_due_type"), dependency.get("predecessor_due_value"), item["offset_value"], item["offset_unit"])
            item["recommended_start"] = shifted[0].isoformat() if shifted else None
            item["recommended_end"] = shifted[1].isoformat() if shifted else None
            item["deviates"] = bool(shifted and own_start and (own_end < shifted[0] or own_start > shifted[1]))
            result.append(item)
        return result

    @staticmethod
    def _tag_names(data):
        if isinstance(data, str):
            data = data.split(",")
        return sorted({str(x).strip() for x in (data or []) if str(x).strip()}, key=str.casefold)

    def _set_tags(self, db, task_id, tags):
        names = self._tag_names(tags)
        db.execute("DELETE FROM task_tags WHERE task_id=?", (task_id,))
        for name in names:
            tag_id = self._lookup(db, "tags", name)
            db.execute("INSERT INTO task_tags(task_id,tag_id) VALUES (?,?)", (task_id, tag_id))
        return names

    @staticmethod
    def _dependencies(values):
        if isinstance(values, str):
            values = values.split(",")
        try:
            normalized = {}
            units = {"d": "day", "w": "week", "m": "month", "y": "year"}
            for value in values or []:
                if isinstance(value, dict):
                    target = int(value.get("depends_on_task_id"))
                    offset, unit = value.get("offset_value"), value.get("offset_unit")
                else:
                    match = re.fullmatch(r"\s*[#^]?(\d+)\s*(?:\+\s*(\d+)\s*([dwmy]))?\s*", str(value), re.I)
                    if not match:
                        raise ValueError
                    target = int(match[1])
                    offset = int(match[2]) if match[2] else None
                    unit = units.get((match[3] or "").lower())
                if (offset is None) != (unit is None) or unit not in {None, "day", "week", "month", "year"} or (offset is not None and (not isinstance(offset, int) or offset < 0)):
                    raise ValueError
                normalized[target] = {"depends_on_task_id": target, "offset_value": offset, "offset_unit": unit}
            return [normalized[key] for key in sorted(normalized)]
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError("Abhängigkeiten müssen Task-IDs mit optionalem Offset wie #123+2w sein.") from exc

    def _set_dependencies(self, db, task_id, values):
        dependencies = self._dependencies(values)
        targets = [item["depends_on_task_id"] for item in dependencies]
        if task_id in targets:
            raise ValueError("Ein Task kann nicht von sich selbst abhängen.")
        for target in targets:
            if not db.execute("SELECT 1 FROM tasks WHERE id=? AND deleted_at IS NULL", (target,)).fetchone():
                raise ValueError(f"Abhängiger Task #{target} wurde nicht gefunden.")
            cycle = db.execute("""WITH RECURSIVE reach(id) AS (
                SELECT depends_on_task_id FROM dependencies WHERE task_id=?
                UNION SELECT d.depends_on_task_id FROM dependencies d JOIN reach r ON d.task_id=r.id
              ) SELECT 1 FROM reach WHERE id=?""", (target, task_id)).fetchone()
            if cycle:
                raise ValueError("Diese Abhängigkeit würde einen Zyklus erzeugen.")
        db.execute("DELETE FROM dependencies WHERE task_id=?", (task_id,))
        db.executemany("INSERT INTO dependencies(task_id,depends_on_task_id,offset_value,offset_unit) VALUES (?,?,?,?)",
                       [(task_id, x["depends_on_task_id"], x["offset_value"], x["offset_unit"]) for x in dependencies])
        return dependencies

    def update_task(self, task_id, changes):
        unknown = set(changes) - FIELDS
        if unknown:
            raise ValueError("Unbekannte Felder: " + ", ".join(sorted(unknown)))
        with self.transaction() as db:
            old = self._task_in_db(db, task_id)
            scalar = {}
            for field in ("title", "link"):
                if field in changes:
                    scalar[field] = (changes[field] or "").strip()
            if "notes" in changes:
                scalar["notes"] = str(changes["notes"] or "")
            if {"due_date", "due_type", "due_value"} & set(changes):
                due_type, due_value = self._due_from_data(changes, old)
                scalar.update(due_type=due_type, due_value=due_value, due_date=due_value if due_type == "exact" else None)
            for field, table, column, color in (("assignee", "assignees", "assignee_id", True), ("project", "projects", "project_id", False), ("category", "categories", "category_id", False)):
                if field in changes:
                    scalar[column] = self._lookup(db, table, changes[field], color)
            if "completed" in changes:
                value = bool(changes["completed"])
                scalar["completed"] = int(value)
                scalar["completed_at"] = now() if value and not old["completed"] else (old["completed_at"] if value else None)
            if "is_milestone" in changes:
                scalar["is_milestone"] = int(bool(changes["is_milestone"]))
            if changes:
                scalar["updated_at"] = now()
            if scalar:
                db.execute("UPDATE tasks SET " + ",".join(f"{k}=?" for k in scalar) + " WHERE id=?", (*scalar.values(), task_id))
            if "tags" in changes:
                self._set_tags(db, task_id, changes["tags"])
            if "dependencies" in changes:
                self._set_dependencies(db, task_id, changes["dependencies"])
            current = self._task_in_db(db, task_id)
            history_fields = [field for field in changes if field not in {"due_date", "due_type", "due_value"}]
            if {"due_date", "due_type", "due_value"} & set(changes) and (old["due_type"], old["due_value"]) != (current["due_type"], current["due_value"]):
                self._history(db, task_id, "updated", "due", format_due(old["due_type"], old["due_value"]), format_due(current["due_type"], current["due_value"]))
            for field in history_fields:
                old_value, new_value = old[field], current[field]
                if field == "dependencies":
                    keys = ("depends_on_task_id", "offset_value", "offset_unit")
                    old_value = [{key: item[key] for key in keys} for item in old_value]
                    new_value = [{key: item[key] for key in keys} for item in new_value]
                if old_value != new_value:
                    action = "updated"
                    if field == "completed": action = "completed" if current[field] else "reopened"
                    self._history(db, task_id, action, field, old_value, new_value)
            self._cleanup_lookups(db)
            if changes and old == current:
                return current
        return self.get_task(task_id)

    def delete_task(self, task_id):
        with self.transaction() as db:
            task = self._task_in_db(db, task_id)
            if not task["deleted_at"]:
                stamp = now()
                db.execute("UPDATE tasks SET deleted_at=?,updated_at=? WHERE id=?", (stamp, stamp, task_id))
                self._history(db, task_id, "deleted", "deleted_at", None, stamp)
        return self.get_task(task_id)

    def restore_task(self, task_id):
        with self.transaction() as db:
            task = self._task_in_db(db, task_id)
            if task["deleted_at"]:
                stamp = now()
                db.execute("UPDATE tasks SET deleted_at=NULL,updated_at=? WHERE id=?", (stamp, task_id))
                self._history(db, task_id, "restored", "deleted_at", task["deleted_at"], None)
        return self.get_task(task_id)

    def history(self, task_id=None):
        with self.read() as db:
            sql = """SELECT h.*,t.title,a.name assignee FROM history h JOIN tasks t ON t.id=h.task_id
                     LEFT JOIN assignees a ON a.id=t.assignee_id"""
            args = ()
            if task_id is not None:
                sql += " WHERE h.task_id=?"; args = (task_id,)
            return [dict(r) for r in db.execute(sql + " ORDER BY h.timestamp DESC,h.id DESC", args)]

    def lookups(self):
        with self.read() as db:
            def rows(table, color=False):
                cols = "id,name,color" if color else "id,name"
                return [dict(r) for r in db.execute(f"SELECT {cols} FROM {table} ORDER BY name COLLATE NOCASE")]
            return {"assignees": rows("assignees", True), "projects": rows("projects"), "categories": rows("categories"), "tags": rows("tags")}

    def set_assignee_color(self, assignee_id, color):
        if not isinstance(color, str) or len(color) != 7 or color[0] != "#":
            raise ValueError("Ungültige Farbe.")
        int(color[1:], 16)
        with self.transaction() as db:
            if not db.execute("UPDATE assignees SET color=? WHERE id=?", (color, assignee_id)).rowcount:
                raise KeyError("Bearbeiter nicht gefunden.")

    def settings(self):
        with self.read() as db:
            result = {}
            for row in db.execute("SELECT key,value FROM settings"):
                try: result[row["key"]] = json.loads(row["value"])
                except json.JSONDecodeError: result[row["key"]] = row["value"]
            return result

    def update_settings(self, changes):
        if not isinstance(changes, dict):
            raise ValueError("Einstellungen müssen ein Objekt sein.")
        with self.transaction() as db:
            db.executemany("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           [(str(k), json.dumps(v, ensure_ascii=False)) for k, v in changes.items()])
        return self.settings()

    def backup(self):
        fd, path = tempfile.mkstemp(prefix="todo-backup-", suffix=".sqlite")
        os.close(fd)
        source = self.connect()
        target = sqlite3.connect(path)
        try:
            source.backup(target)
        finally:
            target.close(); source.close()
        return path
