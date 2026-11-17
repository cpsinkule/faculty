"""The Google Calendar API as Faculty uses it: the instructor's calendar list, events in a date range, and Faculty's own events
(insert, update, delete) on their main calendar. Every call goes to https://www.googleapis.com only, follows no redirect, and
carries the access token of Faculty's one Google connection (response_cards/gmail.py). Nothing here logs event text.

`GoogleCalendarClient.http` is replaced by tests (tests/test_google_calendar.py); nothing else reaches Google.
"""
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from response_cards.gmail import USER_AGENT, DeliveryError, _describe, _reason, open_json

API = "https://www.googleapis.com/calendar/v3"
HOST = "www.googleapis.com"
PAUSE = 300           # a busy or unreachable Google: the next round waits this long
RECONNECT = 1800      # a connection that must be made again: checked this often until it is
MAX_BODY = 8 * 1024 * 1024


class CalendarError(Exception):
    """A call Google did not carry out. `retry_after` pauses the whole sync (busy, unreachable, not permitted); `event_only`
    means Google refused this one event and the others go on."""

    def __init__(self, message, status=0, retry_after=None, event_only=False, gone=False, duplicate=False):
        super().__init__(message)
        self.status, self.retry_after, self.event_only = status, retry_after, event_only
        self.gone, self.duplicate = gone, duplicate


def http_request(method, url, body=None, headers=None, timeout=30, limit=MAX_BODY):
    """One HTTPS call. Returns (status, parsed JSON object or {}); HTTP error statuses are returned, not raised.
    Connection failures propagate so the caller can tell "never answered" apart."""
    headers = {"User-Agent": USER_AGENT, **(headers or {})}
    data = None
    if isinstance(body, bytes):  # an upload: the caller names its Content-Type
        data = body
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    return open_json(request, timeout, limit)


def _timed_out(error):
    return isinstance(error, (TimeoutError, socket.timeout)) or isinstance(getattr(error, "reason", None), (TimeoutError, socket.timeout))


class GoogleCalendarClient:
    def __init__(self, connection):
        self.connection = connection  # response_cards.gmail.GmailConnection: the one Google connection
        self.http = http_request

    # -- The call ------------------------------------------------------------------------------------------------------
    def _token(self, force=False):
        try:
            return self.connection.access_token(force=force)
        except DeliveryError as error:
            if "expired or was revoked" in str(error) or "not connected" in str(error):
                raise CalendarError("Faculty's Google connection has expired or was revoked. Connect Google Calendar again from the "
                                    "Calendar.", retry_after=RECONNECT) from None
            raise CalendarError("Google could not be reached; the calendar will sync again shortly.", retry_after=error.retry_after or PAUSE) from None

    def call(self, method, path, params=None, body=None, timeout=30):
        """(status, payload) of a call Google answered with 2xx, 404/409/410 included for the caller to read; anything else raises."""
        url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        if urllib.parse.urlsplit(url).hostname != HOST:
            raise CalendarError("Refusing to send a calendar request anywhere but Google.")
        token = self._token()
        for attempt in range(2):
            try:
                status, payload = self.http(method, url, body, {"Authorization": f"Bearer {token}"}, timeout)
            except (urllib.error.URLError, OSError) as error:
                raise CalendarError("Google did not answer in time; the calendar will sync again shortly." if _timed_out(error) else
                                    "Google could not be reached; the calendar will sync again shortly.",
                                    retry_after=PAUSE) from None
            if status == 401 and attempt == 0:
                token = self._token(force=True)
                continue
            break
        if 200 <= status < 300 or status in (404, 409, 410):
            return status, payload
        raise self.failure(status, payload)

    def failure(self, status, payload):
        reason, said = _reason(payload), _describe(payload)
        project = self.connection.project
        if status == 401 or reason in ("authError",):
            return CalendarError("Faculty's Google connection has expired or was revoked. Connect Google Calendar again from the Calendar.",
                                 status, retry_after=RECONNECT)
        if reason in ("accessNotConfigured", "SERVICE_DISABLED") or "has not been used in project" in said or "is disabled" in said:
            where = f"https://console.cloud.google.com/apis/library/calendar-json.googleapis.com?project={project}" if project else \
                "https://console.cloud.google.com/apis/library/calendar-json.googleapis.com"
            return CalendarError(f"The Google Calendar API is not turned on for Faculty's Google Cloud project. Turn it on at {where}, "
                                 "then choose Sync now.", status, retry_after=RECONNECT)
        if reason in ("insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT", "forbidden") and status == 403 and "scope" in said.lower():
            return CalendarError("Faculty's Google connection has no calendar permission. Connect Google Calendar again from the Calendar.",
                                 status, retry_after=RECONNECT)
        if status == 429 or reason in ("rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "RESOURCE_EXHAUSTED"):
            return CalendarError("Google asked Faculty to slow down; the calendar will sync again in a few minutes.", status, retry_after=PAUSE)
        if status == 403:
            return CalendarError(f"Google refused the calendar request ({reason or said or 'forbidden'}). Check the Google Workspace "
                                 "policy or connect Google Calendar again.", status, retry_after=RECONNECT)
        if status >= 500:
            return CalendarError("Google Calendar is not answering right now; the calendar will sync again shortly.", status, retry_after=PAUSE)
        return CalendarError(f"Google refused this event ({said or status}).", status, event_only=True)

    # -- Reading --------------------------------------------------------------------------------------------------------
    def calendars(self):
        """The instructor's calendars: [{id, name, primary, access, color}], main calendar first."""
        items, token = [], None
        for _ in range(20):
            params = {"maxResults": 250, "minAccessRole": "reader", **({"pageToken": token} if token else {})}
            status, payload = self.call("GET", "/users/me/calendarList", params)
            if status != 200:
                raise CalendarError("Google did not list the calendars.", status, retry_after=PAUSE)
            items += [item for item in payload.get("items") or [] if isinstance(item, dict) and isinstance(item.get("id"), str)]
            token = payload.get("nextPageToken")
            if not token:
                break
        calendars = [{"id": item["id"], "name": str(item.get("summaryOverride") or item.get("summary") or item["id"])[:200],
                      "primary": bool(item.get("primary")), "access": str(item.get("accessRole") or ""),
                      "color": str(item.get("backgroundColor") or "")[:20]} for item in items]
        return sorted(calendars, key=lambda item: (not item["primary"], item["name"].casefold()))

    def events(self, calendar_id, time_min, time_max, limit=5000):
        """Every event of one calendar starting before `time_max` and ending after `time_min` (RFC 3339), recurring ones expanded."""
        items, token, seen = [], None, set()
        path = "/calendars/" + urllib.parse.quote(calendar_id, safe="") + "/events"
        for _ in range(20):
            params = {"timeMin": time_min, "timeMax": time_max, "singleEvents": "true", "showDeleted": "false", "maxResults": 2500,
                      **({"pageToken": token} if token else {})}
            status, payload = self.call("GET", path, params)
            if status in (404, 410):
                raise CalendarError("That Google calendar is no longer available to this account.", status, event_only=True, gone=True)
            items += [item for item in payload.get("items") or [] if isinstance(item, dict)]
            token = payload.get("nextPageToken")
            # CALENDAR: only a complete read can replace the retained calendar copy.
            if len(items) > limit or (token and len(items) == limit):
                raise CalendarError("Google's calendar has more events than Faculty can read in one round.", retry_after=PAUSE)
            if not token:
                return items
            if not isinstance(token, str) or token in seen:
                raise CalendarError("Google repeated an unreadable calendar page; the retained events were kept.", retry_after=PAUSE)
            seen.add(token)
        raise CalendarError("Google's calendar listing did not finish; the retained events were kept.", retry_after=PAUSE)

    # -- Faculty's own events (main calendar only) --------------------------------------------------------------------------
    def insert(self, event):
        """Creates the event with its own ID. 409 (that ID exists, possibly deleted in Google) is `duplicate`, for an update."""
        status, payload = self.call("POST", "/calendars/primary/events", {"sendUpdates": "none"}, event)
        if status == 409:
            raise CalendarError("That event already exists.", status, event_only=True, duplicate=True)
        if status in (404, 410):
            raise CalendarError("Google could not find the main calendar.", status, retry_after=RECONNECT)
        return payload

    def update(self, event_id, event):
        """Replaces Faculty's event (restoring it if it was deleted in Google). 404/410 is `gone`: that ID can never be used again."""
        status, payload = self.call("PUT", "/calendars/primary/events/" + urllib.parse.quote(event_id, safe=""), {"sendUpdates": "none"}, event)
        if status in (404, 410):
            raise CalendarError("That event is gone from Google.", status, event_only=True, gone=True)
        if status == 409:
            raise CalendarError("Google refused to replace that event.", status, event_only=True)
        return payload

    def delete(self, event_id):
        """Deletes Faculty's event; one already gone counts as deleted. Returns True when Google had it."""
        status, _ = self.call("DELETE", "/calendars/primary/events/" + urllib.parse.quote(event_id, safe=""), {"sendUpdates": "none"})
        if status == 409:
            raise CalendarError("Google refused to delete that event.", status, event_only=True)
        return status not in (404, 410)
