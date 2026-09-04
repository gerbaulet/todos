package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	_ "modernc.org/sqlite"
)

type appError struct {
	status int
	msg    string
}

func (e *appError) Error() string { return e.msg }
func badRequest(msg string) error { return &appError{status: 400, msg: msg} }
func notFound(msg string) error   { return &appError{status: 404, msg: msg} }

type Task struct {
	ID           int64    `json:"id"`
	Title        string   `json:"title"`
	AssigneeID   *int64   `json:"assignee_id"`
	DueDate      *string  `json:"due_date"`
	Completed    bool     `json:"completed"`
	CompletedAt  *string  `json:"completed_at"`
	ProjectID    *int64   `json:"project_id"`
	CategoryID   *int64   `json:"category_id"`
	Link         string   `json:"link"`
	CreatedAt    string   `json:"created_at"`
	UpdatedAt    string   `json:"updated_at"`
	DeletedAt    *string  `json:"deleted_at"`
	Assignee     *string  `json:"assignee"`
	Project      *string  `json:"project"`
	Category     *string  `json:"category"`
	Tags         []string `json:"tags"`
	Dependencies []int64  `json:"dependencies"`
}

type History struct {
	ID        int64   `json:"id"`
	TaskID    int64   `json:"task_id"`
	Timestamp string  `json:"timestamp"`
	Action    string  `json:"action"`
	Field     *string `json:"field"`
	OldValue  *string `json:"old_value"`
	NewValue  *string `json:"new_value"`
	Title     string  `json:"title"`
	Assignee  *string `json:"assignee"`
}

type Lookup struct {
	ID    int64  `json:"id"`
	Name  string `json:"name"`
	Color string `json:"color,omitempty"`
}

type Lookups struct {
	Assignees  []Lookup `json:"assignees"`
	Projects   []Lookup `json:"projects"`
	Categories []Lookup `json:"categories"`
	Tags       []Lookup `json:"tags"`
}

type Database struct {
	db   *sql.DB
	path string
}

func openDatabase(path string) (*Database, error) {
	abs, err := filepath.Abs(path)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(filepath.Dir(abs), 0755); err != nil {
		return nil, err
	}
	location := (&url.URL{Scheme: "file", Path: filepath.ToSlash(abs)}).String()
	dsn := location + "?_foreign_keys=on&_busy_timeout=10000&_txlock=immediate"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)
	store := &Database{db: db, path: abs}
	if _, err := db.Exec(schema); err != nil {
		db.Close()
		return nil, err
	}
	if err := store.withTx(context.Background(), func(tx *sql.Tx) error { return cleanupLookups(tx) }); err != nil {
		db.Close()
		return nil, err
	}
	return store, nil
}

func (d *Database) Close() error { return d.db.Close() }

func now() string {
	return time.Now().UTC().Format("2006-01-02T15:04:05.000000+00:00")
}

func (d *Database) withTx(ctx context.Context, fn func(*sql.Tx) error) error {
	tx, err := d.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	if err := fn(tx); err != nil {
		tx.Rollback()
		return err
	}
	return tx.Commit()
}

type queryer interface {
	QueryRowContext(context.Context, string, ...any) *sql.Row
	QueryContext(context.Context, string, ...any) (*sql.Rows, error)
}

const taskSelect = `SELECT t.id,t.title,t.assignee_id,t.due_date,t.completed,t.completed_at,
 t.project_id,t.category_id,t.link,t.created_at,t.updated_at,t.deleted_at,
 a.name,p.name,c.name FROM tasks t
 LEFT JOIN assignees a ON a.id=t.assignee_id
 LEFT JOIN projects p ON p.id=t.project_id
 LEFT JOIN categories c ON c.id=t.category_id`

func scanTask(row interface{ Scan(...any) error }) (*Task, error) {
	t := &Task{Tags: []string{}, Dependencies: []int64{}}
	var completed int
	err := row.Scan(&t.ID, &t.Title, &t.AssigneeID, &t.DueDate, &completed, &t.CompletedAt,
		&t.ProjectID, &t.CategoryID, &t.Link, &t.CreatedAt, &t.UpdatedAt, &t.DeletedAt,
		&t.Assignee, &t.Project, &t.Category)
	if err != nil {
		return nil, err
	}
	t.Completed = completed != 0
	return t, nil
}

func loadTask(ctx context.Context, q queryer, id int64) (*Task, error) {
	t, err := scanTask(q.QueryRowContext(ctx, taskSelect+" WHERE t.id=?", id))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, notFound("Task nicht gefunden.")
	}
	if err != nil {
		return nil, err
	}
	rows, err := q.QueryContext(ctx, `SELECT g.name FROM tags g JOIN task_tags x ON x.tag_id=g.id
 WHERE x.task_id=? ORDER BY g.name COLLATE NOCASE`, id)
	if err != nil {
		return nil, err
	}
	for rows.Next() {
		var name string
		if err := rows.Scan(&name); err != nil {
			rows.Close()
			return nil, err
		}
		t.Tags = append(t.Tags, name)
	}
	if err := rows.Close(); err != nil {
		return nil, err
	}
	rows, err = q.QueryContext(ctx, "SELECT depends_on_id FROM dependencies WHERE task_id=? ORDER BY depends_on_id", id)
	if err != nil {
		return nil, err
	}
	for rows.Next() {
		var dep int64
		if err := rows.Scan(&dep); err != nil {
			rows.Close()
			return nil, err
		}
		t.Dependencies = append(t.Dependencies, dep)
	}
	if err := rows.Close(); err != nil {
		return nil, err
	}
	return t, nil
}

func (d *Database) GetTask(ctx context.Context, id int64) (*Task, error) {
	return loadTask(ctx, d.db, id)
}

func (d *Database) ListTasks(ctx context.Context, includeDeleted bool) ([]*Task, error) {
	where := ""
	if !includeDeleted {
		where = " WHERE t.deleted_at IS NULL"
	}
	rows, err := d.db.QueryContext(ctx, taskSelect+where+" ORDER BY t.id")
	if err != nil {
		return nil, err
	}
	tasks := []*Task{}
	byID := map[int64]*Task{}
	for rows.Next() {
		t, err := scanTask(rows)
		if err != nil {
			rows.Close()
			return nil, err
		}
		tasks = append(tasks, t)
		byID[t.ID] = t
	}
	if err := rows.Close(); err != nil {
		return nil, err
	}
	relationWhere := ""
	if !includeDeleted {
		relationWhere = " WHERE t.deleted_at IS NULL"
	}
	rows, err = d.db.QueryContext(ctx, `SELECT x.task_id,g.name FROM task_tags x
 JOIN tags g ON g.id=x.tag_id JOIN tasks t ON t.id=x.task_id`+relationWhere+` ORDER BY x.task_id,g.name COLLATE NOCASE`)
	if err != nil {
		return nil, err
	}
	for rows.Next() {
		var id int64
		var name string
		if err := rows.Scan(&id, &name); err != nil {
			rows.Close()
			return nil, err
		}
		byID[id].Tags = append(byID[id].Tags, name)
	}
	if err := rows.Close(); err != nil {
		return nil, err
	}
	rows, err = d.db.QueryContext(ctx, `SELECT d.task_id,d.depends_on_id FROM dependencies d
 JOIN tasks t ON t.id=d.task_id`+relationWhere+` ORDER BY d.task_id,d.depends_on_id`)
	if err != nil {
		return nil, err
	}
	for rows.Next() {
		var id, dep int64
		if err := rows.Scan(&id, &dep); err != nil {
			rows.Close()
			return nil, err
		}
		byID[id].Dependencies = append(byID[id].Dependencies, dep)
	}
	if err := rows.Close(); err != nil {
		return nil, err
	}
	return tasks, nil
}

func validateFields(data map[string]json.RawMessage) error {
	unknown := []string{}
	for key := range data {
		if !allowedFields[key] {
			unknown = append(unknown, key)
		}
	}
	if len(unknown) > 0 {
		sort.Strings(unknown)
		return badRequest("Unbekannte Felder: " + strings.Join(unknown, ", "))
	}
	return nil
}

func rawString(raw json.RawMessage) (string, error) {
	if len(raw) == 0 || string(raw) == "null" {
		return "", nil
	}
	var value string
	if err := json.Unmarshal(raw, &value); err != nil {
		return "", badRequest("Textwert erwartet.")
	}
	return strings.TrimSpace(value), nil
}

func rawBool(raw json.RawMessage) (bool, error) {
	var value bool
	if err := json.Unmarshal(raw, &value); err != nil {
		return false, badRequest("Boolescher Wert erwartet.")
	}
	return value, nil
}

func rawDate(raw json.RawMessage) (*string, error) {
	value, err := rawString(raw)
	if err != nil {
		return nil, err
	}
	if value == "" {
		return nil, nil
	}
	parsed, err := time.Parse("2006-01-02", value)
	if err != nil || parsed.Format("2006-01-02") != value {
		return nil, badRequest("Ungültiges Datum; erwartet wird YYYY-MM-DD.")
	}
	return &value, nil
}

func rawStrings(raw json.RawMessage) ([]string, error) {
	if len(raw) == 0 || string(raw) == "null" {
		return []string{}, nil
	}
	var values []string
	if raw[0] == '"' {
		var value string
		if err := json.Unmarshal(raw, &value); err != nil {
			return nil, badRequest("Ungültige Tags.")
		}
		values = strings.Split(value, ",")
	} else if err := json.Unmarshal(raw, &values); err != nil {
		return nil, badRequest("Ungültige Tags.")
	}
	seen := map[string]string{}
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value != "" {
			seen[strings.ToLower(value)] = value
		}
	}
	values = values[:0]
	for _, value := range seen {
		values = append(values, value)
	}
	sort.Slice(values, func(i, j int) bool { return strings.ToLower(values[i]) < strings.ToLower(values[j]) })
	return values, nil
}

func rawIDs(raw json.RawMessage) ([]int64, error) {
	if len(raw) == 0 || string(raw) == "null" {
		return []int64{}, nil
	}
	values := []int64{}
	if raw[0] == '"' {
		var value string
		if err := json.Unmarshal(raw, &value); err != nil {
			return nil, badRequest("Abhängigkeiten müssen Task-IDs sein.")
		}
		for _, part := range strings.Split(strings.ReplaceAll(value, "#", ""), ",") {
			if strings.TrimSpace(part) == "" {
				continue
			}
			id, err := strconv.ParseInt(strings.TrimSpace(part), 10, 64)
			if err != nil {
				return nil, badRequest("Abhängigkeiten müssen Task-IDs sein.")
			}
			values = append(values, id)
		}
	} else if err := json.Unmarshal(raw, &values); err != nil {
		return nil, badRequest("Abhängigkeiten müssen Task-IDs sein.")
	}
	seen := map[int64]bool{}
	unique := values[:0]
	for _, id := range values {
		if !seen[id] {
			seen[id] = true
			unique = append(unique, id)
		}
	}
	sort.Slice(unique, func(i, j int) bool { return unique[i] < unique[j] })
	return unique, nil
}

func lookup(ctx context.Context, tx *sql.Tx, table, value string, color bool) (*int64, error) {
	value = strings.TrimSpace(value)
	if value == "" {
		return nil, nil
	}
	var id int64
	err := tx.QueryRowContext(ctx, "SELECT id FROM "+table+" WHERE name=? COLLATE NOCASE", value).Scan(&id)
	if err == nil {
		return &id, nil
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return nil, err
	}
	var result sql.Result
	if color {
		var count int
		if err := tx.QueryRowContext(ctx, "SELECT COUNT(*) FROM assignees").Scan(&count); err != nil {
			return nil, err
		}
		result, err = tx.ExecContext(ctx, "INSERT INTO assignees(name,color) VALUES (?,?)", value, colors[count%len(colors)])
	} else {
		result, err = tx.ExecContext(ctx, "INSERT INTO "+table+"(name) VALUES (?)", value)
	}
	if err != nil {
		return nil, err
	}
	id, err = result.LastInsertId()
	return &id, err
}

func cleanupLookups(tx *sql.Tx) error {
	queries := []string{
		"DELETE FROM assignees WHERE NOT EXISTS (SELECT 1 FROM tasks WHERE tasks.assignee_id=assignees.id)",
		"DELETE FROM projects WHERE NOT EXISTS (SELECT 1 FROM tasks WHERE tasks.project_id=projects.id)",
		"DELETE FROM categories WHERE NOT EXISTS (SELECT 1 FROM tasks WHERE tasks.category_id=categories.id)",
		"DELETE FROM tags WHERE NOT EXISTS (SELECT 1 FROM task_tags WHERE task_tags.tag_id=tags.id)",
	}
	for _, query := range queries {
		if _, err := tx.Exec(query); err != nil {
			return err
		}
	}
	return nil
}

func historyText(value any) *string {
	if value == nil {
		return nil
	}
	var text string
	switch v := value.(type) {
	case bool:
		if v {
			text = "True"
		} else {
			text = "False"
		}
	case []string, []int64:
		encoded, _ := json.Marshal(v)
		text = string(encoded)
	default:
		text = fmt.Sprint(v)
	}
	return &text
}

func addHistory(ctx context.Context, tx *sql.Tx, taskID int64, action string, field *string, old, new any) error {
	_, err := tx.ExecContext(ctx, `INSERT INTO history(task_id,timestamp,action,field,old_value,new_value)
 VALUES (?,?,?,?,?,?)`, taskID, now(), action, field, historyText(old), historyText(new))
	return err
}

func setTags(ctx context.Context, tx *sql.Tx, taskID int64, tags []string) error {
	if _, err := tx.ExecContext(ctx, "DELETE FROM task_tags WHERE task_id=?", taskID); err != nil {
		return err
	}
	for _, name := range tags {
		id, err := lookup(ctx, tx, "tags", name, false)
		if err != nil {
			return err
		}
		if _, err := tx.ExecContext(ctx, "INSERT INTO task_tags(task_id,tag_id) VALUES (?,?)", taskID, id); err != nil {
			return err
		}
	}
	return nil
}

func setDependencies(ctx context.Context, tx *sql.Tx, taskID int64, deps []int64) error {
	for _, target := range deps {
		if target == taskID {
			return badRequest("Ein Task kann nicht von sich selbst abhängen.")
		}
		var exists int
		if err := tx.QueryRowContext(ctx, "SELECT 1 FROM tasks WHERE id=? AND deleted_at IS NULL", target).Scan(&exists); errors.Is(err, sql.ErrNoRows) {
			return badRequest(fmt.Sprintf("Abhängiger Task #%d wurde nicht gefunden.", target))
		} else if err != nil {
			return err
		}
		var cycle int
		err := tx.QueryRowContext(ctx, `WITH RECURSIVE reach(id) AS (
 SELECT depends_on_id FROM dependencies WHERE task_id=?
 UNION SELECT d.depends_on_id FROM dependencies d JOIN reach r ON d.task_id=r.id
) SELECT 1 FROM reach WHERE id=?`, target, taskID).Scan(&cycle)
		if err == nil {
			return badRequest("Diese Abhängigkeit würde einen Zyklus erzeugen.")
		}
		if !errors.Is(err, sql.ErrNoRows) {
			return err
		}
	}
	if _, err := tx.ExecContext(ctx, "DELETE FROM dependencies WHERE task_id=?", taskID); err != nil {
		return err
	}
	for _, dep := range deps {
		if _, err := tx.ExecContext(ctx, "INSERT INTO dependencies(task_id,depends_on_id) VALUES (?,?)", taskID, dep); err != nil {
			return err
		}
	}
	return nil
}

func fieldValue(t *Task, field string) any {
	switch field {
	case "title":
		return t.Title
	case "assignee":
		if t.Assignee == nil {
			return nil
		}
		return *t.Assignee
	case "due_date":
		if t.DueDate == nil {
			return nil
		}
		return *t.DueDate
	case "completed":
		return t.Completed
	case "project":
		if t.Project == nil {
			return nil
		}
		return *t.Project
	case "category":
		if t.Category == nil {
			return nil
		}
		return *t.Category
	case "link":
		return t.Link
	case "tags":
		return t.Tags
	case "dependencies":
		return t.Dependencies
	}
	return nil
}
