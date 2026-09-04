package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

func testServer(t *testing.T) *httptest.Server {
	t.Helper()
	db, err := openDatabase(filepath.Join(t.TempDir(), "todo.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	httpServer := httptest.NewServer(&server{db: db})
	t.Cleanup(func() { httpServer.Close(); _ = db.Close() })
	return httpServer
}

func request(t *testing.T, client *http.Client, method, url string, body any, status int) (map[string]any, http.Header) {
	t.Helper()
	var reader io.Reader
	if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		reader = bytes.NewReader(encoded)
	}
	req, err := http.NewRequest(method, url, reader)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	response, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != status {
		data, _ := io.ReadAll(response.Body)
		t.Fatalf("status=%d body=%s", response.StatusCode, data)
	}
	result := map[string]any{}
	if strings.Contains(response.Header.Get("Content-Type"), "json") {
		if err := json.NewDecoder(response.Body).Decode(&result); err != nil {
			t.Fatal(err)
		}
	}
	return result, response.Header
}

func TestHTTPAPI(t *testing.T) {
	httpServer := testServer(t)
	client := httpServer.Client()
	health, _ := request(t, client, "GET", httpServer.URL+"/healthz", nil, 200)
	if health["ok"] != true {
		t.Fatalf("health=%#v", health)
	}
	created, _ := request(t, client, "POST", httpServer.URL+"/api/tasks", map[string]any{"title": "API"}, 201)
	id := int(created["id"].(float64))
	loaded, _ := request(t, client, "GET", httpServer.URL+"/api/tasks/"+stringID(id), nil, 200)
	if loaded["title"] != "API" {
		t.Fatalf("loaded=%#v", loaded)
	}
	changed, _ := request(t, client, "PATCH", httpServer.URL+"/api/tasks/"+stringID(id), map[string]any{"due_date": "2026-10-03"}, 200)
	if changed["due_date"] != "2026-10-03" {
		t.Fatalf("changed=%#v", changed)
	}
	errorBody, _ := request(t, client, "PATCH", httpServer.URL+"/api/tasks/"+stringID(id), map[string]any{"due_date": "morgen"}, 400)
	if !strings.Contains(errorBody["error"].(string), "Ungültiges Datum") {
		t.Fatalf("error=%#v", errorBody)
	}
	request(t, client, "DELETE", httpServer.URL+"/api/tasks/"+stringID(id), nil, 200)
	restored, _ := request(t, client, "POST", httpServer.URL+"/api/tasks/"+stringID(id)+"/restore", nil, 200)
	if restored["deleted_at"] != nil {
		t.Fatalf("restored=%#v", restored)
	}
}

func TestStaticFilesAndBackup(t *testing.T) {
	httpServer := testServer(t)
	client := httpServer.Client()
	response, err := client.Get(httpServer.URL + "/")
	if err != nil {
		t.Fatal(err)
	}
	data, err := io.ReadAll(response.Body)
	response.Body.Close()
	if err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != 200 || !bytes.Contains(data, []byte("<!doctype html>")) {
		t.Fatalf("index status=%d", response.StatusCode)
	}
	response, err = client.Post(httpServer.URL+"/api/backup", "application/json", nil)
	if err != nil {
		t.Fatal(err)
	}
	backup, err := io.ReadAll(response.Body)
	response.Body.Close()
	if err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != 200 || len(backup) < 100 || !bytes.HasPrefix(backup, []byte("SQLite format 3")) {
		t.Fatalf("backup status=%d size=%d", response.StatusCode, len(backup))
	}
}

func stringID(id int) string { return strconv.Itoa(id) }
