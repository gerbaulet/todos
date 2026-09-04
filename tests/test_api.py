import json
import os
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import app


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fd, cls.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        cls.server = app.make_server(cls.path, port=0)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()
        os.unlink(cls.path)

    def request(self, method, path, data=None, expected=200):
        body=json.dumps(data).encode() if data is not None else None
        req=Request(self.base+path, data=body, method=method, headers={"Content-Type":"application/json"})
        try:
            response=urlopen(req)
        except HTTPError as exc:
            response=exc
        self.assertEqual(response.status, expected)
        return json.loads(response.read())

    def test_crud_history_and_validation(self):
        task=self.request("POST", "/api/tasks", {"title":"API"}, 201)
        loaded=self.request("GET", f"/api/tasks/{task['id']}")
        self.assertEqual(loaded["title"], "API")
        changed=self.request("PATCH", f"/api/tasks/{task['id']}", {"due_date":"2026-10-03"})
        self.assertEqual(changed["due_date"], "2026-10-03")
        history=self.request("GET", f"/api/tasks/{task['id']}/history")
        self.assertEqual(len(history), 2)
        error=self.request("PATCH", f"/api/tasks/{task['id']}", {"due_date":"morgen"}, 400)
        self.assertIn("Ungültiges Datum", error["error"])
        deleted=self.request("DELETE", f"/api/tasks/{task['id']}")
        self.assertIsNotNone(deleted["deleted_at"])
        restored=self.request("POST", f"/api/tasks/{task['id']}/restore")
        self.assertIsNone(restored["deleted_at"])

    def test_cycle_error_is_meaningful(self):
        a=self.request("POST", "/api/tasks", {"title":"A"}, 201)
        b=self.request("POST", "/api/tasks", {"title":"B", "dependencies":[a["id"]]}, 201)
        error=self.request("PATCH", f"/api/tasks/{a['id']}", {"dependencies":[b["id"]]}, 400)
        self.assertIn("Zyklus", error["error"])

    def test_quick_add_payload_uses_create_cycle_validation(self):
        seed=self.request("POST", "/api/tasks", {"title":"Vorhanden"}, 201)
        error=self.request("POST", "/api/tasks", {"title":"Quick Add", "dependencies":[seed["id"]+1]}, 400)
        self.assertIn("selbst", error["error"])

    def test_list_and_global_history(self):
        self.assertIsInstance(self.request("GET", "/api/tasks"), list)
        self.assertIsInstance(self.request("GET", "/api/history"), list)


if __name__ == "__main__":
    unittest.main()
