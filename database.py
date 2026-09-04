import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone


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
  completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0,1)),
  completed_at TEXT,
  project_id INTEGER REFERENCES projects(id),
  category_id INTEGER REFERENCES categories(id),
  link TEXT NOT NULL DEFAULT '',
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
  depends_on_id INTEGER NOT NULL REFERENCES tasks(id),
  PRIMARY KEY(task_id, depends_on_id), CHECK(task_id <> depends_on_id)
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
FIELDS = {"title", "assignee", "due_date", "completed", "project", "category", "link", "tags", "dependencies"}
DEFAULT_DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "todo.sqlite")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Database:
    def __init__(self, path=DEFAULT_DATABASE):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with self.read() as db:
            db.executescript(SCHEMA)
            self._cleanup_lookups(db)

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
    def _validate_date(value):
        if value in (None, ""):
            return None
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except (TypeError, ValueError) as exc:
            raise ValueError("Ungültiges Datum; erwartet wird YYYY-MM-DD.") from exc
        return value

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
            cur = db.execute(
                """INSERT INTO tasks(title,assignee_id,due_date,completed,completed_at,project_id,category_id,link,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                ((data.get("title") or "").strip(), assignee_id, self._validate_date(data.get("due_date")), completed,
                 stamp if completed else None, project_id, category_id, (data.get("link") or "").strip(), stamp, stamp),
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
        result["tags"] = [r[0] for r in db.execute("SELECT g.name FROM tags g JOIN task_tags x ON x.tag_id=g.id WHERE x.task_id=? ORDER BY g.name COLLATE NOCASE", (task_id,))]
        result["dependencies"] = [r[0] for r in db.execute("SELECT depends_on_id FROM dependencies WHERE task_id=? ORDER BY depends_on_id", (task_id,))]
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
                task["tags"] = []
                task["dependencies"] = []
                tasks.append(task)
                by_id[task["id"]] = task

            relation_where = "" if include_deleted else " WHERE t.deleted_at IS NULL"
            for row in db.execute("""SELECT x.task_id, g.name
                FROM task_tags x JOIN tags g ON g.id=x.tag_id JOIN tasks t ON t.id=x.task_id"""
                + relation_where + " ORDER BY x.task_id, g.name COLLATE NOCASE"):
                by_id[row["task_id"]]["tags"].append(row["name"])
            for row in db.execute("""SELECT d.task_id, d.depends_on_id
                FROM dependencies d JOIN tasks t ON t.id=d.task_id"""
                + relation_where + " ORDER BY d.task_id, d.depends_on_id"):
                by_id[row["task_id"]]["dependencies"].append(row["depends_on_id"])
            return tasks

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
    def _dependency_ids(values):
        if isinstance(values, str):
            values = values.replace("#", "").split(",")
        try:
            return sorted({int(x) for x in (values or []) if str(x).strip()})
        except (TypeError, ValueError) as exc:
            raise ValueError("Abhängigkeiten müssen Task-IDs sein.") from exc

    def _set_dependencies(self, db, task_id, values):
        targets = self._dependency_ids(values)
        if task_id in targets:
            raise ValueError("Ein Task kann nicht von sich selbst abhängen.")
        for target in targets:
            if not db.execute("SELECT 1 FROM tasks WHERE id=? AND deleted_at IS NULL", (target,)).fetchone():
                raise ValueError(f"Abhängiger Task #{target} wurde nicht gefunden.")
            cycle = db.execute("""WITH RECURSIVE reach(id) AS (
                SELECT depends_on_id FROM dependencies WHERE task_id=?
                UNION SELECT d.depends_on_id FROM dependencies d JOIN reach r ON d.task_id=r.id
              ) SELECT 1 FROM reach WHERE id=?""", (target, task_id)).fetchone()
            if cycle:
                raise ValueError("Diese Abhängigkeit würde einen Zyklus erzeugen.")
        db.execute("DELETE FROM dependencies WHERE task_id=?", (task_id,))
        db.executemany("INSERT INTO dependencies(task_id,depends_on_id) VALUES (?,?)", [(task_id, x) for x in targets])
        return targets

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
            if "due_date" in changes:
                scalar["due_date"] = self._validate_date(changes["due_date"])
            for field, table, column, color in (("assignee", "assignees", "assignee_id", True), ("project", "projects", "project_id", False), ("category", "categories", "category_id", False)):
                if field in changes:
                    scalar[column] = self._lookup(db, table, changes[field], color)
            if "completed" in changes:
                value = bool(changes["completed"])
                scalar["completed"] = int(value)
                scalar["completed_at"] = now() if value and not old["completed"] else (old["completed_at"] if value else None)
            if changes:
                scalar["updated_at"] = now()
            if scalar:
                db.execute("UPDATE tasks SET " + ",".join(f"{k}=?" for k in scalar) + " WHERE id=?", (*scalar.values(), task_id))
            if "tags" in changes:
                self._set_tags(db, task_id, changes["tags"])
            if "dependencies" in changes:
                self._set_dependencies(db, task_id, changes["dependencies"])
            current = self._task_in_db(db, task_id)
            for field in changes:
                if old[field] != current[field]:
                    action = "updated"
                    if field == "completed": action = "completed" if current[field] else "reopened"
                    self._history(db, task_id, action, field, old[field], current[field])
            self._cleanup_lookups(db)
            if changes and not any(old[f] != current[f] for f in changes):
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
