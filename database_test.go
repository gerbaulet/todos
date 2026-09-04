package main

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func testDatabase(t *testing.T) *Database {
	t.Helper()
	db, err := openDatabase(filepath.Join(t.TempDir(), "todo.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = db.Close() })
	return db
}

func input(t *testing.T, value map[string]any) map[string]json.RawMessage {
	t.Helper()
	result := map[string]json.RawMessage{}
	for key, item := range value {
		data, err := json.Marshal(item)
		if err != nil {
			t.Fatal(err)
		}
		result[key] = data
	}
	return result
}

func deref(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}

func TestCreateUpdateAndHistory(t *testing.T) {
	ctx := context.Background()
	db := testDatabase(t)
	task, err := db.CreateTask(ctx, input(t, map[string]any{"title": "Angebot", "assignee": "Müller", "due_date": "2026-10-03"}))
	if err != nil {
		t.Fatal(err)
	}
	if task.ID != 1 || deref(task.Assignee) != "Müller" {
		t.Fatalf("unexpected task: %#v", task)
	}
	task, err = db.UpdateTask(ctx, task.ID, input(t, map[string]any{"title": "Angebot prüfen"}))
	if err != nil {
		t.Fatal(err)
	}
	if task.Title != "Angebot prüfen" {
		t.Fatalf("title=%q", task.Title)
	}
	history, err := db.History(ctx, &task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if got := []string{history[0].Action, history[1].Action}; !reflect.DeepEqual(got, []string{"updated", "created"}) {
		t.Fatalf("actions=%v", got)
	}
}

func TestCompleteDeleteAndRestore(t *testing.T) {
	ctx := context.Background()
	db := testDatabase(t)
	task, err := db.CreateTask(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	task, err = db.UpdateTask(ctx, task.ID, input(t, map[string]any{"completed": true}))
	if err != nil {
		t.Fatal(err)
	}
	if !task.Completed || task.CompletedAt == nil {
		t.Fatal("task not completed")
	}
	task, err = db.UpdateTask(ctx, task.ID, input(t, map[string]any{"completed": false}))
	if err != nil {
		t.Fatal(err)
	}
	if task.Completed || task.CompletedAt != nil {
		t.Fatal("task not reopened")
	}
	task, err = db.DeleteTask(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if task.DeletedAt == nil {
		t.Fatal("task not deleted")
	}
	task, err = db.RestoreTask(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if task.DeletedAt != nil {
		t.Fatal("task not restored")
	}
	history, err := db.History(ctx, &task.ID)
	if err != nil {
		t.Fatal(err)
	}
	actions := []string{}
	for _, item := range history {
		actions = append(actions, item.Action)
	}
	want := []string{"restored", "deleted", "reopened", "completed", "created"}
	if !reflect.DeepEqual(actions, want) {
		t.Fatalf("actions=%v", actions)
	}
}

func TestTagsLookupsAndCleanup(t *testing.T) {
	ctx := context.Background()
	db := testDatabase(t)
	first, err := db.CreateTask(ctx, input(t, map[string]any{"assignee": "Falsch", "project": "Alt", "category": "Alt", "tags": []string{"Schnell", "Kunde"}}))
	if err != nil {
		t.Fatal(err)
	}
	second, err := db.CreateTask(ctx, input(t, map[string]any{"assignee": "Falsch", "project": "Falsch", "category": "Alt", "tags": []string{"Schnell"}}))
	if err != nil {
		t.Fatal(err)
	}
	if _, err = db.DeleteTask(ctx, second.ID); err != nil {
		t.Fatal(err)
	}
	first, err = db.UpdateTask(ctx, first.ID, input(t, map[string]any{"assignee": "Richtig", "project": "Richtig", "category": "Neu", "tags": []string{"Neu"}}))
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(first.Tags, []string{"Neu"}) {
		t.Fatalf("tags=%v", first.Tags)
	}
	lookups, err := db.Lookups(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(lookups.Assignees) != 2 || lookups.Assignees[0].Color == "" {
		t.Fatalf("lookups=%#v", lookups)
	}
	if _, err = db.UpdateTask(ctx, second.ID, input(t, map[string]any{"assignee": "Richtig", "project": "Richtig", "category": "Neu", "tags": []string{"Neu"}})); err != nil {
		t.Fatal(err)
	}
	lookups, err = db.Lookups(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(lookups.Assignees) != 1 || len(lookups.Projects) != 1 || len(lookups.Categories) != 1 || len(lookups.Tags) != 1 {
		t.Fatalf("cleanup failed: %#v", lookups)
	}
}

func TestDependenciesAndCycles(t *testing.T) {
	ctx := context.Background()
	db := testDatabase(t)
	one, _ := db.CreateTask(ctx, input(t, map[string]any{"title": "Eins"}))
	two, _ := db.CreateTask(ctx, input(t, map[string]any{"title": "Zwei"}))
	three, _ := db.CreateTask(ctx, input(t, map[string]any{"title": "Drei"}))
	if _, err := db.UpdateTask(ctx, two.ID, input(t, map[string]any{"dependencies": []int64{one.ID}})); err != nil {
		t.Fatal(err)
	}
	three, err := db.UpdateTask(ctx, three.ID, input(t, map[string]any{"dependencies": []int64{one.ID, two.ID}}))
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(three.Dependencies, []int64{one.ID, two.ID}) {
		t.Fatalf("deps=%v", three.Dependencies)
	}
	if _, err = db.UpdateTask(ctx, one.ID, input(t, map[string]any{"dependencies": []int64{one.ID}})); err == nil || !strings.Contains(err.Error(), "selbst") {
		t.Fatalf("self error=%v", err)
	}
	if _, err = db.UpdateTask(ctx, one.ID, input(t, map[string]any{"dependencies": []int64{two.ID}})); err == nil || !strings.Contains(err.Error(), "Zyklus") {
		t.Fatalf("cycle error=%v", err)
	}
	loaded, err := db.GetTask(ctx, one.ID)
	if err != nil {
		t.Fatal(err)
	}
	if len(loaded.Dependencies) != 0 {
		t.Fatalf("transaction was not rolled back: %v", loaded.Dependencies)
	}
}

func TestListFilterSettingsBackupAndIDs(t *testing.T) {
	ctx := context.Background()
	db := testDatabase(t)
	one, err := db.CreateTask(ctx, input(t, map[string]any{"title": "Gesichert", "tags": []string{"Backup"}}))
	if err != nil {
		t.Fatal(err)
	}
	if _, err = db.UpdateSettings(ctx, input(t, map[string]any{"view": "timeline", "filters": map[string]any{"status": "active"}})); err != nil {
		t.Fatal(err)
	}
	if _, err = db.DeleteTask(ctx, one.ID); err != nil {
		t.Fatal(err)
	}
	two, err := db.CreateTask(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	if two.ID <= one.ID {
		t.Fatal("id reused")
	}
	active, err := db.ListTasks(ctx, false)
	if err != nil {
		t.Fatal(err)
	}
	if len(active) != 1 || active[0].ID != two.ID {
		t.Fatalf("active=%#v", active)
	}
	path, err := db.Backup(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(path)
	copyDB, err := openDatabase(path)
	if err != nil {
		t.Fatal(err)
	}
	defer copyDB.Close()
	copied, err := copyDB.GetTask(ctx, one.ID)
	if err != nil {
		t.Fatal(err)
	}
	if copied.Title != "Gesichert" || !reflect.DeepEqual(copied.Tags, []string{"Backup"}) {
		t.Fatalf("copy=%#v", copied)
	}
	settings, err := copyDB.Settings(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if settings["view"] != "timeline" {
		t.Fatalf("settings=%#v", settings)
	}
}

func TestValidation(t *testing.T) {
	ctx := context.Background()
	db := testDatabase(t)
	if _, err := db.CreateTask(ctx, input(t, map[string]any{"unknown": true})); err == nil || !strings.Contains(err.Error(), "Unbekannte") {
		t.Fatalf("unknown field error=%v", err)
	}
	if _, err := db.CreateTask(ctx, input(t, map[string]any{"due_date": "morgen"})); err == nil || !strings.Contains(err.Error(), "Ungültiges Datum") {
		t.Fatalf("date error=%v", err)
	}
}
