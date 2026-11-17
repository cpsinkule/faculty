"""Sign in to Canvas (requirement, 2026-09-27): the AIM sign-in window (`aim_import.AimBrowser`) signing in to Canvas instead.

TAMU's Canvas sits behind the same Microsoft sign-in and Duo as AIM, with the same NetID and password (the saved Gradescope
sign-in, as for AIM). The private browser starts at Canvas, the driver goes through the NetID, password and Duo steps exactly
as it does for AIM, and once a Canvas page shows it asks Canvas's API who is signed in. When Canvas answers, the browser's
Canvas cookies are KEPT (`canvas.web.save`, 0600, the instructor's choice of a kept session so Duo is asked only occasionally)
and the browser and its profile are discarded. The password is held only until typed, as for AIM; nothing else of the sign-in
is written or logged."""
from __future__ import annotations

import logging
import time
from urllib.parse import urlsplit

from flask import Blueprint

import aim_import as aim

from . import web

log = logging.getLogger(__name__)


class CanvasBrowser(aim.AimBrowser):
    TARGET, NAME, FINAL = "canvas", "Canvas", ("done",)
    ENDED = "This Canvas sign-in has ended. Choose Sign in to Canvas to start again."
    SERVICE = None  # the CanvasService whose settings (and kept session) this browser signs in for

    @property
    def SETTINGS(self):
        return self.SERVICE.settings

    def __init__(self, portal, netid=None, password=None):
        self._checked = 0.0
        super().__init__(portal, netid, password, kinds=())

    def _classify(self, url, text, inputs):
        parts = urlsplit(url)
        if (parts.hostname or "").lower() == self.SETTINGS.host:
            # Canvas's own pages: its login pages hand over to TAMU's sign-in; any other page means Canvas let the browser in.
            return "working" if parts.path.lower().startswith("/login") else "canvas"
        return aim.classify(url, text, inputs, "https://aim.invalid/")  # TAMU's and Microsoft's and Duo's steps, as for AIM

    def _on_canvas(self, frame, text, inputs, now):
        """Canvas says who is signed in: keep the session and close the browser. Asked at most every two seconds."""
        if now - self._checked < 2:
            return
        self._checked = now
        try:
            answer = self._context.request.get(f"{self.SETTINGS.base}/api/v1/users/self",
                                               headers={"Accept": "application/json"}, max_redirects=0, timeout=30000)
            if answer.status != 200:
                return
            import json
            user = json.loads(web.strip(answer.body()))
        except Exception as error:
            log.info("Canvas did not answer who is signed in yet (%s)", type(error).__name__)
            return
        cookies = {c["name"]: c["value"] for c in self._context.cookies([self.SETTINGS.base + "/"])}
        if not cookies:
            return
        name = str(user.get("name") or user.get("short_name") or "")
        web.save(self.SETTINGS, cookies, user={"name": name})
        with self._lock:
            self.state, self.place, self.finished = "done", "Signed in to Canvas", time.monotonic()
            self.result = {"user": name}
        self._closing.set()  # the browser and its profile go now; the kept session is the only thing kept

    def status(self):
        found = super().status()
        found["user"] = (self.result or {}).get("user", "") if self.state == "done" else ""
        return found


def register(app, service):
    """`/api/canvas/signin...`: the AIM window's routes for this browser (its own single sign-in at a time)."""
    browser = type("KeptCanvasBrowser", (CanvasBrowser,), {"SERVICE": service})
    sessions = app.extensions.setdefault("canvas_sessions", aim.AimSessions(browser, CanvasBrowser.ENDED))
    api = Blueprint("canvas_signin", __name__, url_prefix="/api/canvas")
    aim.sign_in_routes(api, "/signin", sessions, lambda: service.settings.base + "/")
    app.register_blueprint(api)
