package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
)

func (d *Database) CreateTask(ctx context.Context, data map[string]json.RawMessage) (*Task, error) {
	if data == nil {
		data = map[string]json.RawMessage{}
	}
	if err := validateFields(data); err != nil {
		return nil, err
	}
	var taskID int64
	err := d.withTx(ctx, func(tx *sql.Tx) error {
		title, err := rawString(data["title"])
		if err != nil {
			return err
		}
		link, err := rawString(data["link"])
		if err != nil {
			return err
		}
		date, err := rawDate(data["due_date"])
		if err != nil {
			return err
		}
		completed := false
		if raw, ok := data["completed"]; ok {
			completed, err = rawBool(raw)
			if err != nil {
				return err
			}
		}
		assignee, err := rawString(data["assignee"])
		if err != nil {
			return err
		}
		project, err := rawString(data["project"])
		if err != nil {
			return err
		}
		category, err := rawString(data["category"])
		if err != nil {
			return err
		}
		assigneeID, err := lookup(ctx, tx, "assignees", assignee, true)
		if err != nil {
			return err
		}
		projectID, err := lookup(ctx, tx, "projects", project, false)
		if err != nil {
			return err
		}
		categoryID, err := lookup(ctx, tx, "categories", category, false)
		if err != nil {
			return err
		}
		stamp := now()
		var completedAt *string
		if completed {
			completedAt = &stamp
		}
		result, err := tx.ExecContext(ctx, `INSERT INTO tasks(title,assignee_id,due_date,completed,completed_at,
 project_id,category_id,link,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)`,
			title, assigneeID, date, completed, completedAt, projectID, categoryID, link, stamp, stamp)
		if err != nil {
			return err
		}
		taskID, err = result.LastInsertId()
		if err != nil {
			return err
		}
		tags, err := rawStrings(data["tags"])
		if err != nil {
			return err
		}
		if err := setTags(ctx, tx, taskID, tags); err != nil {
			return err
		}
		deps, err := rawIDs(data["dependencies"])
		if err != nil {
			return err
		}
		if err := setDependencies(ctx, tx, taskID, deps); err != nil {
			return err
		}
		return addHistory(ctx, tx, taskID, "created", nil, nil, nil)
	})
	if err != nil {
		return nil, err
	}
	return d.GetTask(ctx, taskID)
}

func (d *Database) UpdateTask(ctx context.Context, id int64, data map[string]json.RawMessage) (*Task, error) {
	if data == nil {
		return nil, badRequest("JSON-Inhalt muss ein Objekt sein.")
	}
	if err := validateFields(data); err != nil {
		return nil, err
	}
	var current *Task
	err := d.withTx(ctx, func(tx *sql.Tx) error {
		old, err := loadTask(ctx, tx, id)
		if err != nil {
			return err
		}
		if raw, ok := data["title"]; ok {
			value, err := rawString(raw)
			if err != nil {
				return err
			}
			if _, err = tx.ExecContext(ctx, "UPDATE tasks SET title=? WHERE id=?", value, id); err != nil {
				return err
			}
		}
		if raw, ok := data["link"]; ok {
			value, err := rawString(raw)
			if err != nil {
				return err
			}
			if _, err = tx.ExecContext(ctx, "UPDATE tasks SET link=? WHERE id=?", value, id); err != nil {
				return err
			}
		}
		if raw, ok := data["due_date"]; ok {
			value, err := rawDate(raw)
			if err != nil {
				return err
			}
			if _, err = tx.ExecContext(ctx, "UPDATE tasks SET due_date=? WHERE id=?", value, id); err != nil {
				return err
			}
		}
		for _, item := range []struct {
			field, table, column string
			color                bool
		}{
			{"assignee", "assignees", "assignee_id", true}, {"project", "projects", "project_id", false}, {"category", "categories", "category_id", false},
		} {
			if raw, ok := data[item.field]; ok {
				value, err := rawString(raw)
				if err != nil {
					return err
				}
				lookupID, err := lookup(ctx, tx, item.table, value, item.color)
				if err != nil {
					return err
				}
				if _, err = tx.ExecContext(ctx, "UPDATE tasks SET "+item.column+"=? WHERE id=?", lookupID, id); err != nil {
					return err
				}
			}
		}
		if raw, ok := data["completed"]; ok {
			value, err := rawBool(raw)
			if err != nil {
				return err
			}
			var completedAt *string
			if value && !old.Completed {
				stamp := now()
				completedAt = &stamp
			} else if value {
				completedAt = old.CompletedAt
			}
			if _, err := tx.ExecContext(ctx, "UPDATE tasks SET completed=?,completed_at=? WHERE id=?", value, completedAt, id); err != nil {
				return err
			}
		}
		if raw, ok := data["tags"]; ok {
			tags, err := rawStrings(raw)
			if err != nil {
				return err
			}
			if err := setTags(ctx, tx, id, tags); err != nil {
				return err
			}
		}
		if raw, ok := data["dependencies"]; ok {
			deps, err := rawIDs(raw)
			if err != nil {
				return err
			}
			if err := setDependencies(ctx, tx, id, deps); err != nil {
				return err
			}
		}
		if len(data) > 0 {
			if _, err := tx.ExecContext(ctx, "UPDATE tasks SET updated_at=? WHERE id=?", now(), id); err != nil {
				return err
			}
		}
		current, err = loadTask(ctx, tx, id)
		if err != nil {
			return err
		}
		for field := range data {
			oldValue, newValue := fieldValue(old, field), fieldValue(current, field)
			if reflect.DeepEqual(oldValue, newValue) {
				continue
			}
			action := "updated"
			if field == "completed" {
				if current.Completed {
					action = "completed"
				} else {
					action = "reopened"
				}
			}
			fieldCopy := field
			if err := addHistory(ctx, tx, id, action, &fieldCopy, oldValue, newValue); err != nil {
				return err
			}
		}
		return cleanupLookups(tx)
	})
	return current, err
}

func (d *Database) DeleteTask(ctx context.Context, id int64) (*Task, error) {
	err := d.withTx(ctx, func(tx *sql.Tx) error {
		task, err := loadTask(ctx, tx, id)
		if err != nil {
			return err
		}
		if task.DeletedAt != nil {
			return nil
		}
		stamp := now()
		if _, err := tx.ExecContext(ctx, "UPDATE tasks SET deleted_at=?,updated_at=? WHERE id=?", stamp, stamp, id); err != nil {
			return err
		}
		field := "deleted_at"
		return addHistory(ctx, tx, id, "deleted", &field, nil, stamp)
	})
	if err != nil {
		return nil, err
	}
	return d.GetTask(ctx, id)
}

func (d *Database) RestoreTask(ctx context.Context, id int64) (*Task, error) {
	err := d.withTx(ctx, func(tx *sql.Tx) error {
		task, err := loadTask(ctx, tx, id)
		if err != nil {
			return err
		}
		if task.DeletedAt == nil {
			return nil
		}
		stamp := now()
		if _, err := tx.ExecContext(ctx, "UPDATE tasks SET deleted_at=NULL,updated_at=? WHERE id=?", stamp, id); err != nil {
			return err
		}
		field := "deleted_at"
		return addHistory(ctx, tx, id, "restored", &field, *task.DeletedAt, nil)
	})
	if err != nil {
		return nil, err
	}
	return d.GetTask(ctx, id)
}

func (d *Database) History(ctx context.Context, taskID *int64) ([]History, error) {
	query := `SELECT h.id,h.task_id,h.timestamp,h.action,h.field,h.old_value,h.new_value,t.title,a.name
 FROM history h JOIN tasks t ON t.id=h.task_id LEFT JOIN assignees a ON a.id=t.assignee_id`
	args := []any{}
	if taskID != nil {
		query += " WHERE h.task_id=?"
		args = append(args, *taskID)
	}
	query += " ORDER BY h.timestamp DESC,h.id DESC"
	rows, err := d.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []History{}
	for rows.Next() {
		var h History
		if err := rows.Scan(&h.ID, &h.TaskID, &h.Timestamp, &h.Action, &h.Field, &h.OldValue, &h.NewValue, &h.Title, &h.Assignee); err != nil {
			return nil, err
		}
		result = append(result, h)
	}
	return result, rows.Err()
}

func lookupRows(ctx context.Context, db *sql.DB, table string, color bool) ([]Lookup, error) {
	columns := "id,name"
	if color {
		columns += ",color"
	}
	rows, err := db.QueryContext(ctx, "SELECT "+columns+" FROM "+table+" ORDER BY name COLLATE NOCASE")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []Lookup{}
	for rows.Next() {
		var value Lookup
		if color {
			err = rows.Scan(&value.ID, &value.Name, &value.Color)
		} else {
			err = rows.Scan(&value.ID, &value.Name)
		}
		if err != nil {
			return nil, err
		}
		result = append(result, value)
	}
	return result, rows.Err()
}

func (d *Database) Lookups(ctx context.Context) (Lookups, error) {
	var result Lookups
	var err error
	if result.Assignees, err = lookupRows(ctx, d.db, "assignees", true); err != nil {
		return result, err
	}
	if result.Projects, err = lookupRows(ctx, d.db, "projects", false); err != nil {
		return result, err
	}
	if result.Categories, err = lookupRows(ctx, d.db, "categories", false); err != nil {
		return result, err
	}
	if result.Tags, err = lookupRows(ctx, d.db, "tags", false); err != nil {
		return result, err
	}
	return result, nil
}

func (d *Database) SetAssigneeColor(ctx context.Context, id int64, color string) error {
	if len(color) != 7 || color[0] != '#' {
		return badRequest("Ungültige Farbe.")
	}
	for _, char := range color[1:] {
		if !strings.ContainsRune("0123456789abcdefABCDEF", char) {
			return badRequest("Ungültige Farbe.")
		}
	}
	return d.withTx(ctx, func(tx *sql.Tx) error {
		result, err := tx.ExecContext(ctx, "UPDATE assignees SET color=? WHERE id=?", color, id)
		if err != nil {
			return err
		}
		count, err := result.RowsAffected()
		if err != nil {
			return err
		}
		if count == 0 {
			return notFound("Bearbeiter nicht gefunden.")
		}
		return nil
	})
}

func (d *Database) Settings(ctx context.Context) (map[string]any, error) {
	rows, err := d.db.QueryContext(ctx, "SELECT key,value FROM settings")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := map[string]any{}
	for rows.Next() {
		var key, value string
		if err := rows.Scan(&key, &value); err != nil {
			return nil, err
		}
		var decoded any
		if json.Unmarshal([]byte(value), &decoded) == nil {
			result[key] = decoded
		} else {
			result[key] = value
		}
	}
	return result, rows.Err()
}

func (d *Database) UpdateSettings(ctx context.Context, changes map[string]json.RawMessage) (map[string]any, error) {
	if changes == nil {
		return nil, badRequest("Einstellungen müssen ein Objekt sein.")
	}
	err := d.withTx(ctx, func(tx *sql.Tx) error {
		for key, value := range changes {
			if _, err := tx.ExecContext(ctx, `INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value`, key, string(value)); err != nil {
				return err
			}
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	return d.Settings(ctx)
}

func (d *Database) Backup(ctx context.Context) (string, error) {
	file, err := os.CreateTemp(filepath.Dir(d.path), "todo-backup-*.sqlite")
	if err != nil {
		return "", err
	}
	path := file.Name()
	if err := file.Close(); err != nil {
		os.Remove(path)
		return "", err
	}
	if err := os.Remove(path); err != nil {
		return "", err
	}
	quoted := "'" + strings.ReplaceAll(path, "'", "''") + "'"
	if _, err := d.db.ExecContext(ctx, "VACUUM INTO "+quoted); err != nil {
		os.Remove(path)
		return "", err
	}
	return path, nil
}

func sqliteClientError(err error) bool {
	if err == nil {
		return false
	}
	var app *appError
	if errors.As(err, &app) {
		return true
	}
	return strings.Contains(strings.ToLower(err.Error()), "sqlite") || strings.Contains(strings.ToLower(err.Error()), "constraint")
}
