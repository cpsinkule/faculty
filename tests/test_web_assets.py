"""Page-scoped reads and immutable assets must preserve the private boundary."""
import gzip
import hashlib
import io
import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, jsonify, request, send_file

from app import create_app
from web_assets import GRACE_SECONDS, KEEP, Archive, forbidden, register


class WebAssetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = create_app(self.directory.name)
        self.client = self.app.test_client()

    def test_core_read_does_not_load_other_feature_lists(self):
        with patch.object(self.app.extensions["seating"], "list", side_effect=AssertionError("No full seating check")), \
             patch.object(self.app.extensions["rooms"], "list", side_effect=AssertionError("No full room list")):
            response = self.client.get("/api/state?include=core")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["counts"], {"rooms": 0, "seating": 0})
        self.assertIn("course_groups", response.json)
        self.assertNotIn("rooms", response.json)
        self.assertNotIn("seating_charts", response.json)
        self.assertNotIn("storage", response.json)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_feature_reads_are_explicit_and_full_state_stays_compatible(self):
        for include, expected in (("rooms", {"rooms"}), ("seating", {"seating_charts"}), ("storage", {"storage"})):
            self.assertEqual(set(self.client.get("/api/state?include=" + include).json), expected)
        complete = self.client.get("/api/state").json
        self.assertTrue({"rooms", "seating_charts", "storage", "settings", "courses", "tools"} <= complete.keys())
        for include in ("", "all", "core,secret", "core,,rooms"):
            self.assertEqual(self.client.get("/api/state?include=" + include).status_code, 400)

    def test_html_and_private_data_are_uncached_but_all_module_dependencies_are_versioned(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)
        self.assertNotIn("/static/", html)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', html)
        self.assertGreater(len(assets), 10)
        prefix = self.app.extensions["web_assets"]["prefix"]
        for asset in assets:
            self.assertTrue(asset.startswith(prefix))
            result = self.client.get(asset)
            self.assertEqual(result.status_code, 200)
            self.assertIn("immutable", result.headers["Cache-Control"])
        script = self.client.get(prefix + "app.js")
        for dependency in re.findall(r'from "\./([^"]+)"', script.get_data(as_text=True)):
            self.assertEqual(self.client.get(prefix + dependency).status_code, 200)
        cached = self.client.get(prefix + "app.js", headers={"If-None-Match": script.headers["ETag"]})
        self.assertEqual(cached.status_code, 304)
        for route in ("/static/app.js", "/api/state", "/api/workspace/overview", "/api/export"):
            with self.client.get(route) as result:
                self.assertEqual(result.headers["Cache-Control"], "no-store")
        for route in (prefix + "../app.py", prefix + "index.html", "/assets/old/app.js", prefix + "missing.js"):
            result = self.client.get(route)
            self.assertEqual(result.status_code, 404)
            self.assertEqual(result.headers["Cache-Control"], "no-store")

    def test_asset_snapshot_never_changes_bytes_under_an_existing_cache_key(self):
        directory = Path(self.directory.name) / "assets"
        directory.mkdir()
        (directory / "index.html").write_text('<script src="/static/app.js"></script>')
        (directory / "app.js").write_text('import "./other.js";')
        (directory / "other.js").write_text("old")
        old = Flask("old")
        register(old, directory)
        prefix = old.extensions["web_assets"]["prefix"]
        (directory / "other.js").write_text("new")
        self.assertEqual(old.test_client().get(prefix + "other.js").data, b"old")
        new = Flask("new")
        register(new, directory)
        self.assertNotEqual(prefix, new.extensions["web_assets"]["prefix"])
        self.assertEqual(new.test_client().get(prefix + "other.js").status_code, 404)

    def test_tabs_opened_before_a_restart_still_load_their_build(self):
        # AGENTS.md: features must work in existing tabs after restarts. A build's public files are archived when its page
        # is served, and a later process serves that older prefix (lazy imports of an open tab) from the archive.
        root = Path(self.directory.name)
        directory, cache = root / "site", root / "cache"
        directory.mkdir()
        (directory / "index.html").write_text('<script type="module" src="/static/app.js"></script>')
        (directory / "app.js").write_text('import("./lazy.js");')
        (directory / "lazy.js").write_text("export const build = 'old';")
        (directory / "notes.txt").write_text("not a public frontend source")
        old = Flask("old")
        register(old, directory, cache=cache)
        old_prefix = old.extensions["web_assets"]["prefix"]
        self.assertFalse(cache.exists(), "Nothing is archived before a page of the build is served.")
        old.add_url_rule("/", "index", lambda: "page")
        old.test_client().get("/")
        (directory / "lazy.js").write_text("export const build = 'new';")
        new = Flask("new")
        register(new, directory, cache=cache)
        new_prefix = new.extensions["web_assets"]["prefix"]
        self.assertNotEqual(old_prefix, new_prefix)
        client = new.test_client()
        response = client.get(old_prefix + "lazy.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"export const build = 'old';")
        self.assertIn("immutable", response.headers["Cache-Control"])
        self.assertEqual(client.get(new_prefix + "lazy.js").data, b"export const build = 'new';")
        # Only public sources, only by the archived manifest, only intact bytes.
        for route in (old_prefix + "notes.txt", old_prefix + "index.html", old_prefix + "../lazy.js", old_prefix + "missing.js",
                      "/assets/" + "0" * 24 + "/lazy.js", "/assets/../cache/lazy.js"):
            self.assertEqual(client.get(route).status_code, 404, route)
        archive = Archive(cache)
        manifest = (cache / "builds" / (old_prefix.split("/")[2] + ".json")).read_text()
        self.assertNotIn("notes.txt", manifest)
        self.assertNotIn("index.html", manifest)
        blob = next(path for path in (cache / "blobs").iterdir() if path.read_bytes() == b"export const build = 'old';")
        blob.write_bytes(b"tampered")
        self.assertIsNone(archive.read(old_prefix.split("/")[2], "lazy.js"))
        self.assertEqual(client.get(old_prefix + "lazy.js").status_code, 404)

    def test_archive_keeps_the_newest_builds_and_their_files_only(self):
        cache = Path(self.directory.name) / "cache"
        archive = Archive(cache)
        versions = [f"{index:024x}" for index in range(KEEP + 3)]
        for index, version in enumerate(versions):
            archive.save(version, {"app.js": f"build {index}".encode(), "shared.css": b"same bytes"})
            stamp = time.time() - (len(versions) - index) * 10
            os.utime(cache / "builds" / f"{version}.json", (stamp, stamp))
        # Saving again marks a build newest; the oldest beyond KEEP go, and unreferenced blobs go after their grace time.
        archive.save(versions[0], {"app.js": b"build 0", "shared.css": b"same bytes"})
        kept = archive.versions()
        self.assertEqual(len(kept), KEEP)
        self.assertIn(versions[0], kept)
        self.assertNotIn(versions[1], kept)
        self.assertEqual(archive.read(versions[-1], "shared.css"), b"same bytes")
        old = time.time() - GRACE_SECONDS - 60
        for blob in (cache / "blobs").iterdir():
            os.utime(blob, (old, old))
        archive.save(versions[-1], {"app.js": f"build {len(versions) - 1}".encode(), "shared.css": b"same bytes"})
        remaining = {path.read_bytes() for path in (cache / "blobs").iterdir()}
        self.assertNotIn(b"build 1", remaining)
        self.assertIn(b"build 0", remaining)
        self.assertIn(b"same bytes", remaining)

    def test_archive_never_uses_temporary_or_service_scratch_space(self):
        for path in ("/tmp/web-assets", "/mnt/nvme/Teaching/tmp/web/web-assets", "/mnt/nvme/Teaching/tmp/worker/x"):
            self.assertTrue(forbidden(path), path)
        for path in ("/mnt/nvme/Teaching/runtime/web-assets", "/mnt/nvme/Teaching/tmp/checks/run/data/web-assets"):
            self.assertFalse(forbidden(path), path)
        directory = Path(self.directory.name) / "site"
        directory.mkdir()
        (directory / "index.html").write_text("<p></p>")
        (directory / "app.js").write_text("")
        app = Flask("scratch")
        register(app, directory, cache="/mnt/nvme/Teaching/tmp/web/web-assets")
        with self.assertLogs("web_assets", level="WARNING"):
            self.assertIsNone(app.extensions["web_assets"]["archive"]())

    def test_bad_entries_in_an_old_manifest_do_not_break_archiving_a_new_build(self):
        # FRONTEND: a damaged runtime cache cannot prevent serving a new page or discard other valid old entries.
        cache = Path(self.directory.name) / "cache"
        archive = Archive(cache)
        old, new = "1" * 24, "2" * 24
        archive.save(old, {"kept.js": b"valid old source"})
        manifest = cache / "builds" / f"{old}.json"
        entries = json.loads(manifest.read_text())
        manifest.write_text(json.dumps({**entries, "broken.js": ["not a digest"], "also-broken.js": {"digest": "wrong"}}))
        for blob in (cache / "blobs").iterdir():
            stamp = time.time() - GRACE_SECONDS - 1
            os.utime(blob, (stamp, stamp))
        archive.save(new, {"app.js": b"new source"})
        self.assertEqual(archive.read(old, "kept.js"), b"valid old source")
        self.assertEqual(archive.read(new, "app.js"), b"new source")

    def test_workspace_default_archive_is_beside_its_data(self):
        with patch.dict(os.environ, {"FACULTY_WEB_ASSET_CACHE": ""}):
            self.client.get("/")
            archive = self.app.extensions["web_assets"]["archive"]()
            self.assertEqual(archive.root, Path(self.directory.name) / "web-assets")
            self.assertIn(self.app.extensions["web_assets"]["version"], archive.versions())

    def test_this_builds_text_sources_go_gzip_encoded_under_their_own_etag(self):
        directory = Path(self.directory.name) / "site"
        directory.mkdir()
        (directory / "index.html").write_text('<script type="module" src="/static/app.js"></script>')
        source = ("export const words = " + json.dumps(["compressible"] * 400) + ";\n").encode()
        (directory / "app.js").write_bytes(source)
        (directory / "tiny.js").write_bytes(b"x")
        (directory / "logo.png").write_bytes(b"\x89PNG" + b"\0" * 4000)
        site = Flask("site")
        register(site, directory)
        prefix = site.extensions["web_assets"]["prefix"]
        client = site.test_client()
        etag = hashlib.sha256(source).hexdigest()
        plain = client.get(prefix + "app.js")
        self.assertEqual(plain.data, source)
        self.assertNotIn("Content-Encoding", plain.headers)
        self.assertEqual(plain.headers["ETag"], f'"{etag}"')
        self.assertIn("Accept-Encoding", plain.headers["Vary"])
        packed = client.get(prefix + "app.js", headers={"Accept-Encoding": "gzip, deflate, br"})
        self.assertEqual(packed.headers["Content-Encoding"], "gzip")
        self.assertEqual(packed.headers["ETag"], f'"{etag}-gz"')
        self.assertIn("Accept-Encoding", packed.headers["Vary"])
        self.assertEqual(packed.headers["Cache-Control"], "public, max-age=31536000, immutable")
        self.assertEqual(gzip.decompress(packed.data), source)
        self.assertLess(len(packed.data), len(source) * 0.9)
        self.assertEqual(client.get(prefix + "app.js", headers={"Accept-Encoding": "gzip"}).data, packed.data)
        # Each encoding answers only its own validator.
        self.assertEqual(client.get(prefix + "app.js", headers={"Accept-Encoding": "gzip", "If-None-Match": f'"{etag}-gz"'}).status_code, 304)
        self.assertEqual(client.get(prefix + "app.js", headers={"If-None-Match": f'"{etag}"'}).status_code, 304)
        self.assertEqual(client.get(prefix + "app.js", headers={"Accept-Encoding": "gzip", "If-None-Match": f'"{etag}"'}).data, packed.data)
        refused = client.get(prefix + "app.js", headers={"Accept-Encoding": "gzip;q=0, identity"})
        self.assertEqual(refused.data, source)
        self.assertNotIn("Content-Encoding", refused.headers)
        # Too small to gain a tenth, or not text: sent as stored.
        for name in ("tiny.js", "logo.png"):
            response = client.get(prefix + name, headers={"Accept-Encoding": "gzip"})
            self.assertEqual(response.data, (directory / name).read_bytes(), name)
            self.assertNotIn("Content-Encoding", response.headers, name)
            self.assertEqual(response.headers["ETag"], f'"{hashlib.sha256(response.data).hexdigest()}"', name)

    def test_earlier_builds_come_from_the_archive_as_stored(self):
        root = Path(self.directory.name)
        directory, cache = root / "site", root / "cache"
        directory.mkdir()
        (directory / "index.html").write_text('<script type="module" src="/static/app.js"></script>')
        old_source = ("export const build = " + json.dumps(["old"] * 400) + ";").encode()
        (directory / "app.js").write_bytes(old_source)
        old = Flask("old")
        register(old, directory, cache=cache)
        old_prefix = old.extensions["web_assets"]["prefix"]
        old.add_url_rule("/", "index", lambda: "page")
        old.test_client().get("/")
        (directory / "app.js").write_bytes(old_source.replace(b"old", b"new"))
        new = Flask("new")
        register(new, directory, cache=cache)
        response = new.test_client().get(old_prefix + "app.js", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(response.data, old_source)
        self.assertNotIn("Content-Encoding", response.headers)
        self.assertEqual(response.headers["ETag"], f'"{hashlib.sha256(old_source).hexdigest()}"')
        self.assertIn("immutable", response.headers["Cache-Control"])


class JsonCompressionTests(unittest.TestCase):
    """Large JSON goes gzip-encoded only to clients that ask for it; everything else is sent as before."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = create_app(self.directory.name)
        self.payload = {"rows": [{"id": index, "name": f"row {index}"} for index in range(1000)]}
        self.app.add_url_rule("/api/test-large", "test_large", lambda: jsonify(self.payload))
        self.app.add_url_rule("/api/test-small", "test_small", lambda: jsonify(ok=True))
        self.app.add_url_rule("/api/test-created", "test_created", lambda: (jsonify(self.payload), 201))

        def download():
            response = jsonify(self.payload)
            response.headers["Content-Disposition"] = "attachment; filename=export.json"
            return response

        def conditional():
            response = jsonify(self.payload)
            response.set_etag("view-etag")
            return response.make_conditional(request)

        self.app.add_url_rule("/api/test-download", "test_download", download)
        self.app.add_url_rule("/api/test-file", "test_file", lambda: send_file(io.BytesIO(json.dumps(self.payload).encode()), mimetype="application/json"))
        self.app.add_url_rule("/api/test-conditional", "test_conditional", conditional)
        self.app.add_url_rule("/api/test-html", "test_html", lambda: "<p>" + "x" * 20000 + "</p>")
        self.client = self.app.test_client()

    def test_without_accept_encoding_the_body_is_identity(self):
        response = self.client.get("/api/test-large")
        self.assertNotIn("Content-Encoding", response.headers)
        self.assertEqual(response.json, self.payload)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.client.get("/api/test-large", headers={"Accept-Encoding": "gzip;q=0"}).json, self.payload)

    def test_with_gzip_the_body_decompresses_to_the_identical_json(self):
        plain = self.client.get("/api/test-large").data
        response = self.client.get("/api/test-large", headers={"Accept-Encoding": "gzip, deflate, br"})
        self.assertEqual(response.headers["Content-Encoding"], "gzip")
        self.assertIn("Accept-Encoding", response.headers["Vary"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(int(response.headers["Content-Length"]), len(response.data))
        self.assertEqual(gzip.decompress(response.data), plain)
        self.assertLess(len(response.data), len(plain))

    def test_small_other_status_download_file_html_and_head_responses_are_untouched(self):
        headers = {"Accept-Encoding": "gzip"}
        for route in ("/api/test-small", "/api/test-created", "/api/test-download", "/api/test-html"):
            response = self.client.get(route, headers=headers)
            self.assertNotIn("Content-Encoding", response.headers, route)
        with self.client.get("/api/test-file", headers=headers) as response:
            self.assertNotIn("Content-Encoding", response.headers)
            self.assertEqual(json.loads(response.data), self.payload)
        head = self.client.head("/api/test-large", headers=headers)
        self.assertNotIn("Content-Encoding", head.headers)
        self.assertEqual(int(head.headers["Content-Length"]), len(self.client.get("/api/test-large").data))
        # A view's own ETag stays as the view set it, and its 304 passes through without a body.
        response = self.client.get("/api/test-conditional", headers=headers)
        self.assertEqual(response.headers["Content-Encoding"], "gzip")
        self.assertEqual(response.headers["ETag"], '"view-etag"')
        cached = self.client.get("/api/test-conditional", headers={**headers, "If-None-Match": '"view-etag"'})
        self.assertEqual(cached.status_code, 304)
        self.assertEqual(cached.data, b"")
        self.assertNotIn("Content-Encoding", cached.headers)
