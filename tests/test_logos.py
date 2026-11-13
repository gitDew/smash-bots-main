"""Logo storage and admin-only uploads, without Docker or image dependencies."""

import base64
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from server.app import create_admin_app, create_team_app
from server.db import Database
from server.event import Event
from server.hub import Hub
from server.logos import MAX_LOGO_BYTES, logo_media_type


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a+WQAAAAASUVORK5CYII="
)
HEADERS = {"X-Smash-Admin": "1"}


class LogoTests(unittest.TestCase):
    def setUp(self):
        connect = sqlite3.connect
        with patch("server.db.sqlite3.connect", lambda *a, **kw: connect(*a, check_same_thread=False, **kw)):
            self.db = Database(":memory:")
        self.addCleanup(self.db.conn.close)
        self.team = self.db.add_team("Thunder", "FOX")
        self.other = self.db.add_team("Lightning", "MARTH")
        self.event = Event(self.db, Hub(), SimpleNamespace(remove=AsyncMock()), None)
        self.admin = TestClient(create_admin_app(self.event, port=8001), base_url="http://127.0.0.1:8001")
        self.teams = TestClient(create_team_app(self.event))
        self.path = f"/api/teams/{self.team['id']}/logo"
        self.public = f"/t/{self.team['token']}/logo"

    def upload(self, content=PNG):
        return self.admin.put(self.path, content=content, headers=HEADERS)

    def test_upload_and_read_with_detected_type(self):
        self.assertEqual(self.admin.get(self.path).status_code, 404)
        self.assertEqual(self.upload().status_code, 200)
        for client, path in ((self.admin, self.path), (self.teams, self.public)):
            response = client.get(path)
            self.assertEqual(response.content, PNG)
            self.assertEqual(response.headers["content-type"], "image/png")
            self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        state = self.event.admin_state()
        self.assertTrue(state["teams"][0]["logo"].startswith(self.path + "?v="))
        self.assertIsNone(state["teams"][1]["logo"])
        self.assertTrue(self.event.team_state(self.team)["team"]["logo"].startswith(self.public + "?v="))
        self.assertIsNone(self.db.logo(self.other["id"]))

    def test_upload_is_local_admin_only(self):
        self.assertEqual(self.admin.put(self.path, content=PNG).status_code, 403)
        self.assertEqual(self.teams.put(self.path, content=PNG, headers=HEADERS).status_code, 404)
        self.assertEqual(self.teams.put(self.public, content=PNG, headers=HEADERS).status_code, 405)
        spoofed = TestClient(create_admin_app(self.event, port=8001), base_url="http://evil.example")
        self.assertEqual(spoofed.put(self.path, content=PNG, headers=HEADERS).status_code, 403)
        self.assertIsNone(self.db.logo(self.team["id"]))

    def test_invalid_or_oversized_upload_preserves_previous_logo(self):
        self.upload()
        revision = self.db.logo_revision(self.team["id"])
        for content in (b"", b"not an image", b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'):
            self.assertEqual(self.upload(content).status_code, 415)
        self.assertEqual(self.upload(b"x" * (MAX_LOGO_BYTES + 1)).status_code, 413)
        self.assertEqual(self.db.logo(self.team["id"])["content"], PNG)
        self.assertEqual(self.db.logo_revision(self.team["id"]), revision)
        self.assertEqual(self.admin.put("/api/teams/999/logo", content=PNG, headers=HEADERS).status_code, 404)

    def test_replace_remove_and_team_deletion(self):
        self.upload()
        old_url = self.event.admin_state()["teams"][0]["logo"]
        # JPEG framing suffices for format detection; the host never decodes it.
        jpeg = b"\xff\xd8\xff\xe0test\xff\xd9"
        self.assertEqual(self.upload(jpeg).status_code, 200)
        self.assertNotEqual(self.event.admin_state()["teams"][0]["logo"], old_url)
        self.assertEqual(self.admin.get(self.path).headers["content-type"], "image/jpeg")
        self.assertEqual(self.admin.delete(self.path, headers=HEADERS).status_code, 200)
        self.assertEqual(self.teams.get(self.public).status_code, 404)
        self.assertIsNone(self.event.team_state(self.team)["team"]["logo"])
        self.upload()
        self.assertEqual(self.admin.delete(f"/api/teams/{self.team['id']}", headers=HEADERS).status_code, 200)
        self.assertIsNone(self.db.logo(self.team["id"]))

    def test_token_rotation_revokes_old_logo_access(self):
        self.upload()
        self.db.regenerate_token(self.team["id"])
        self.assertEqual(self.teams.get(self.public).status_code, 404)
        team = self.db.team(self.team["id"])
        self.assertEqual(self.teams.get(f"/t/{team['token']}/logo").content, PNG)

    def test_updates_are_broadcast(self):
        with self.teams.websocket_connect(f"/ws/{self.team['token']}") as team_socket:
            team_socket.receive_json()
            self.assertIsNone(team_socket.receive_json()["team"]["logo"])
            with self.admin.websocket_connect("ws://127.0.0.1:8001/ws", headers={"origin": "http://127.0.0.1:8001"}) as admin_socket:
                admin_socket.receive_json()
                admin_socket.receive_json()
                self.upload()
                self.assertIsNotNone(team_socket.receive_json()["team"]["logo"])
                self.assertIsNotNone(admin_socket.receive_json()["teams"][0]["logo"])
                self.admin.delete(self.path, headers=HEADERS)
                self.assertIsNone(team_socket.receive_json()["team"]["logo"])
                self.assertIsNone(admin_socket.receive_json()["teams"][0]["logo"])

    def test_webp_header_detection(self):
        webp = b"RIFF" + (16).to_bytes(4, "little") + b"WEBPVP8L" + b"\0" * 8
        self.assertEqual(logo_media_type(webp), "image/webp")
        self.assertIsNone(logo_media_type(webp[:-1]))


class LogoPersistenceTests(unittest.TestCase):
    def test_restart_and_reset_keep_logo(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "event.sqlite3"
            db = Database(path)
            team = db.add_team("Thunder", "FOX")
            db.save_logo(team["id"], PNG, "image/png", "revision")
            db.conn.close()
            db = Database(path)
            try:
                db.reset_event()
                self.assertEqual(db.logo(team["id"])["content"], PNG)
                self.assertEqual(db.logo_revision(team["id"]), "revision")
                db.delete_team(team["id"])
                self.assertIsNone(db.logo(team["id"]))
            finally:
                db.conn.close()
