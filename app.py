import argparse
import json
import os
import re
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from database import DEFAULT_DATABASE, Database


ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
DB = None


class Handler(BaseHTTPRequestHandler):
    server_version = "TodoLocal/1.0"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def json(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        length = int(self.headers.get("Content-Length", "0"))
        try:
            value = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            raise ValueError("Ungültiges JSON.") from exc
        if not isinstance(value, dict):
            raise ValueError("JSON-Inhalt muss ein Objekt sein.")
        return value

    def route(self):
        return urlparse(self.path).path

    def do_GET(self):
        try:
            path = self.route()
            if path == "/api/tasks": return self.json(200, DB.list_tasks())
            if path == "/api/history": return self.json(200, DB.history())
            if path == "/api/lookups": return self.json(200, DB.lookups())
            if path == "/api/settings": return self.json(200, DB.settings())
            match = re.fullmatch(r"/api/tasks/(\d+)/history", path)
            if match: return self.json(200, DB.history(int(match.group(1))))
            match = re.fullmatch(r"/api/tasks/(\d+)", path)
            if match: return self.json(200, DB.get_task(int(match.group(1))))
            self.static(path)
        except Exception as exc:
            self.error(exc)

    def do_POST(self):
        try:
            path = self.route()
            if path == "/api/tasks": return self.json(201, DB.create_task(self.body()))
            if path == "/api/backup": return self.download_backup()
            match = re.fullmatch(r"/api/tasks/(\d+)/restore", path)
            if match: return self.json(200, DB.restore_task(int(match.group(1))))
            self.json(404, {"error": "Nicht gefunden."})
        except Exception as exc:
            self.error(exc)

    def do_PATCH(self):
        try:
            path = self.route()
            if path == "/api/settings": return self.json(200, DB.update_settings(self.body()))
            match = re.fullmatch(r"/api/tasks/(\d+)", path)
            if match: return self.json(200, DB.update_task(int(match.group(1)), self.body()))
            match = re.fullmatch(r"/api/assignees/(\d+)", path)
            if match:
                DB.set_assignee_color(int(match.group(1)), self.body().get("color"))
                return self.json(200, {"ok": True})
            self.json(404, {"error": "Nicht gefunden."})
        except Exception as exc:
            self.error(exc)

    def do_DELETE(self):
        try:
            match = re.fullmatch(r"/api/tasks/(\d+)", self.route())
            if match: return self.json(200, DB.delete_task(int(match.group(1))))
            self.json(404, {"error": "Nicht gefunden."})
        except Exception as exc:
            self.error(exc)

    def error(self, exc):
        if isinstance(exc, BrokenPipeError): return
        if isinstance(exc, KeyError): status = 404
        elif isinstance(exc, (ValueError, sqlite_error())): status = 400
        else:
            status = 500
            print("Fehler:", repr(exc))
        message = exc.args[0] if exc.args else "Interner Fehler."
        self.json(status, {"error": str(message)})

    def static(self, path):
        names = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css")}
        if path not in names:
            return self.json(404, {"error": "Nicht gefunden."})
        name, mime = names[path]
        with open(os.path.join(STATIC, name), "rb") as handle:
            body = handle.read()
        self.send_response(200)
        self.send_header("Content-Type", mime + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def download_backup(self):
        path = DB.backup()
        filename = "todo-backup-" + datetime.now().strftime("%Y-%m-%d-%H%M%S") + ".sqlite"
        try:
            with open(path, "rb") as handle: body = handle.read()
        finally:
            os.unlink(path)
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.sqlite3")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def sqlite_error():
    import sqlite3
    return sqlite3.Error


def make_server(database=DEFAULT_DATABASE, host="127.0.0.1", port=8765):
    global DB
    DB = Database(database)
    return ThreadingHTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description="Lokale To-do-Anwendung")
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = make_server(args.database, host=args.host, port=args.port)
    print(f"To-do-App läuft unter http://127.0.0.1:{args.port} (Strg+C beendet)")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == "__main__":
    main()
