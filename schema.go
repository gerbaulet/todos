package main

const schema = `
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
`

var colors = []string{"#2563eb", "#dc2626", "#059669", "#7c3aed", "#d97706", "#0891b2", "#be185d", "#4d7c0f"}

var allowedFields = map[string]bool{
	"title": true, "assignee": true, "due_date": true, "completed": true,
	"project": true, "category": true, "link": true, "tags": true, "dependencies": true,
}
