package main

import (
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"mime"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

//go:embed static/*
var staticFiles embed.FS

type server struct{ db *Database }

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(value); err != nil {
		log.Printf("JSON response: %v", err)
	}
}

func writeError(w http.ResponseWriter, err error) {
	status := http.StatusInternalServerError
	message := "Interner Fehler."
	var app *appError
	if errors.As(err, &app) {
		status, message = app.status, app.msg
	} else if sqliteClientError(err) {
		status, message = http.StatusBadRequest, err.Error()
	} else {
		log.Printf("Fehler: %v", err)
	}
	writeJSON(w, status, map[string]string{"error": message})
}

func decodeObject(w http.ResponseWriter, r *http.Request) (map[string]json.RawMessage, error) {
	defer r.Body.Close()
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20))
	var value map[string]json.RawMessage
	if err := decoder.Decode(&value); err != nil {
		if errors.Is(err, io.EOF) {
			return map[string]json.RawMessage{}, nil
		}
		return nil, badRequest("Ungültiges JSON.")
	}
	if value == nil {
		return nil, badRequest("JSON-Inhalt muss ein Objekt sein.")
	}
	var extra any
	if err := decoder.Decode(&extra); !errors.Is(err, io.EOF) {
		return nil, badRequest("Ungültiges JSON.")
	}
	return value, nil
}

func parseID(value string) (int64, error) {
	id, err := strconv.ParseInt(value, 10, 64)
	if err != nil || id < 1 {
		return 0, notFound("Nicht gefunden.")
	}
	return id, nil
}

func (s *server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	path := strings.Trim(r.URL.Path, "/")
	parts := []string{}
	if path != "" {
		parts = strings.Split(path, "/")
	}
	ctx := r.Context()
	var value any
	var err error
	status := http.StatusOK

	switch {
	case r.Method == http.MethodGet && path == "healthz":
		writeJSON(w, status, map[string]bool{"ok": true})
		return
	case r.Method == http.MethodGet && path == "api/tasks":
		value, err = s.db.ListTasks(ctx, true)
	case r.Method == http.MethodPost && path == "api/tasks":
		var data map[string]json.RawMessage
		data, err = decodeObject(w, r)
		if err == nil {
			value, err = s.db.CreateTask(ctx, data)
			status = http.StatusCreated
		}
	case len(parts) == 3 && parts[0] == "api" && parts[1] == "tasks":
		var id int64
		id, err = parseID(parts[2])
		if err != nil {
			break
		}
		switch r.Method {
		case http.MethodGet:
			value, err = s.db.GetTask(ctx, id)
		case http.MethodPatch:
			var data map[string]json.RawMessage
			data, err = decodeObject(w, r)
			if err == nil {
				value, err = s.db.UpdateTask(ctx, id, data)
			}
		case http.MethodDelete:
			value, err = s.db.DeleteTask(ctx, id)
		default:
			err = notFound("Nicht gefunden.")
		}
	case len(parts) == 4 && parts[0] == "api" && parts[1] == "tasks" && parts[3] == "history" && r.Method == http.MethodGet:
		var id int64
		id, err = parseID(parts[2])
		if err == nil {
			value, err = s.db.History(ctx, &id)
		}
	case len(parts) == 4 && parts[0] == "api" && parts[1] == "tasks" && parts[3] == "restore" && r.Method == http.MethodPost:
		var id int64
		id, err = parseID(parts[2])
		if err == nil {
			value, err = s.db.RestoreTask(ctx, id)
		}
	case r.Method == http.MethodGet && path == "api/history":
		value, err = s.db.History(ctx, nil)
	case r.Method == http.MethodGet && path == "api/lookups":
		value, err = s.db.Lookups(ctx)
	case r.Method == http.MethodGet && path == "api/settings":
		value, err = s.db.Settings(ctx)
	case r.Method == http.MethodPatch && path == "api/settings":
		var data map[string]json.RawMessage
		data, err = decodeObject(w, r)
		if err == nil {
			value, err = s.db.UpdateSettings(ctx, data)
		}
	case len(parts) == 3 && parts[0] == "api" && parts[1] == "assignees" && r.Method == http.MethodPatch:
		var id int64
		id, err = parseID(parts[2])
		var data map[string]json.RawMessage
		if err == nil {
			data, err = decodeObject(w, r)
		}
		if err == nil {
			raw, ok := data["color"]
			if !ok {
				err = badRequest("Ungültige Farbe.")
			} else {
				var color string
				if json.Unmarshal(raw, &color) != nil {
					err = badRequest("Ungültige Farbe.")
				} else {
					err = s.db.SetAssigneeColor(ctx, id, color)
					value = map[string]bool{"ok": true}
				}
			}
		}
	case r.Method == http.MethodPost && path == "api/backup":
		s.serveBackup(w, r)
		return
	case r.Method == http.MethodGet:
		s.serveStatic(w, r)
		return
	default:
		err = notFound("Nicht gefunden.")
	}
	if err != nil {
		writeError(w, err)
		return
	}
	writeJSON(w, status, value)
}

func (s *server) serveStatic(w http.ResponseWriter, r *http.Request) {
	names := map[string]string{"/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/favicon.ico": "favicon.ico", "/favicon-32x32.png": "favicon-32x32.png", "/favicon-16x16.png": "favicon-16x16.png", "/apple-touch-icon.png": "apple-touch-icon.png"}
	name, ok := names[r.URL.Path]
	if !ok {
		writeJSON(w, 404, map[string]string{"error": "Nicht gefunden."})
		return
	}
	body, err := staticFiles.ReadFile("static/" + name)
	if err != nil {
		writeError(w, err)
		return
	}
	contentType := mime.TypeByExtension(filepath.Ext(name))
	if name == "app.js" {
		contentType = "text/javascript; charset=utf-8"
	}
	if name == "index.html" {
		contentType = "text/html; charset=utf-8"
	}
	if name == "style.css" {
		contentType = "text/css; charset=utf-8"
	}
	w.Header().Set("Content-Type", contentType)
	w.Header().Set("Content-Length", strconv.Itoa(len(body)))
	_, _ = w.Write(body)
}

func (s *server) serveBackup(w http.ResponseWriter, r *http.Request) {
	path, err := s.db.Backup(r.Context())
	if err != nil {
		writeError(w, err)
		return
	}
	defer os.Remove(path)
	file, err := os.Open(path)
	if err != nil {
		writeError(w, err)
		return
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		writeError(w, err)
		return
	}
	filename := "todo-backup-" + time.Now().Format("2006-01-02-150405") + ".sqlite"
	w.Header().Set("Content-Type", "application/vnd.sqlite3")
	w.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s"`, filename))
	w.Header().Set("Content-Length", strconv.FormatInt(info.Size(), 10))
	_, _ = io.Copy(w, file)
}
