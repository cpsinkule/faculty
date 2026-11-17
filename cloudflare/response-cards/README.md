# Faculty Response Cards public service

This Worker owns the public, immutable recipient card snapshots, access-token
hashes, drafts, immutable submissions, and ordered synchronization events. Faculty
owns templates, versions, course/term/section/enrollment associations, issuance,
and its synchronized copies. The student form continues to work while Faculty
is offline. This service never calls the private Faculty server, sends email,
dispatches Inbox commands, or treats an answer as authenticated student identity.

The target is `respond.example.com`, Worker `faculty-response-cards`, and the
dedicated D1 database configured in `wrangler.jsonc`. The existing email Worker,
intake tunnel, and private Faculty app retain their separate access boundaries.

Deployed on 2026-09-14; public resource IDs and bundle/migration checksums are
retained in `deployment.json`. `verification.json` records the executed checks.
HTTPS, authenticated D1 reads, and Faculty's recurring reconciliation are live.
The production Turnstile widget still needs one human completion check; automated
browser tests exercise the complete Worker/D1 flow with mocked challenges.

## Build and tests

Use Node 22.12+ and the committed lockfile:

```sh
npm ci
npm run types
npm run check
npm test
npm run build
```

`build` is a Wrangler dry run. It changes no deployed resources. Tests run the
Worker in Cloudflare's local runtime with temporary D1 storage and a mocked
Siteverify response. They cover exact-hostname/action verification, fail-closed
errors, transaction races, replay/idempotency, immutable history, conditional
validation, expiry/revocation/unlock, privacy headers, and bounded event pages.

`test/browser-smoke.mjs` exercises the bundled Worker and real local D1 in
Chromium. It checks restored drafts, conditional controls, successful submission,
flagged resubmission, retained history, and mobile overflow. The only mocked
services are the Turnstile widget and Siteverify; every browser request is
intercepted locally. Install Playwright in a test environment or point
`RESPONSE_CARDS_PLAYWRIGHT_MODULE` to its installed module, then run:

```sh
node test/browser-smoke.mjs
```

Optional variables: `RESPONSE_CARDS_CHROMIUM_PATH` for an installed Chromium,
`RESPONSE_CARDS_SCREENSHOT` for the output image (default
`faculty-response-card-browser.png` in the configured temporary directory). A browser test with mocked Turnstile
does not establish that the live widget, hostname routing, or secret is configured
correctly; those require a separate live deployment check.

## Deploy and configure

The D1 database ID, custom-domain route, public sitekey, and exact production
hostname belong in `wrangler.jsonc`. Regenerate types after modifying bindings.
Apply `migrations/0001_response_cards.sql` to the dedicated database before
publishing cards. Do not migrate the existing email database or Faculty catalog.

Two secret bindings are required: `FACULTY_API_SECRET` (at least 32 characters,
shared only with Faculty's configured Response Cards client) and
`TURNSTILE_SECRET` (the secret for the configured public sitekey). Use protected
stdin or a secret manager; never commit secrets or put them in command arguments.
The widget uses action `response_card_submit`. Siteverify must return success,
that exact action, and `respond.example.com`; development widget hostnames are
never accepted by this production backend. Missing configuration fails closed.

Deployment must preserve `workers_dev: false`, `preview_urls: false`, the
configured same-origin boundary, and log privacy. Do not turn on invocation URL
logging or trace sampling: magic-link paths contain bearer tokens. Application
errors log only a fixed event name, without paths, answers, secrets, or IDs.
Logs are enabled with invocation logs disabled; traces are enabled with a zero
sampling rate to retain the same privacy convention as the email gateway.

## Protocol

Faculty endpoints require `Authorization: Bearer <FACULTY_API_SECRET>` and
accept no arbitrary SQL or alternative public actions:

- `PUT /faculty-api/cards/<id>` publishes `{id, issue_id, token_hash, schema,
  policy, due_at, expires_at}`. Repeated identical snapshots return the same
  receipt. Reusing an ID or token hash for changed content returns 409. Only
  SHA-256 hashes of opaque 32–128-character base64url access tokens enter D1.
- `POST /faculty-api/cards/<id>/control` accepts `{request_id, action}`, where
  action is `revoke` or `unlock`. Controls are idempotent by card and request ID.
  Unlock allows one additional response; retrying a used unlock cannot grant
  another. Unlock does not reverse revocation or extend an expiry/deadline.
- `GET /faculty-api/events?after=<cursor>&limit=100` returns `{events, cursor,
  has_more}`. Every event has `{cursor, card_id, type, created_at, data}` and
  `data.status`. Submission events additionally contain `data.submission` with
  `{id, request_id, answers, verification, status, submitted_at}`. Pages are
  bounded below 2 MB and resume after the final returned event. The local client
  must atomically ingest the page before advancing its cursor.

Public routes are `GET /r/<token>`, `GET /api/cards/<token>`,
`PUT /api/cards/<token>/draft` with `{answers}`, and
`POST /api/cards/<token>/submit` with `{request_id, answers, turnstile_token}`.
Writes require the configured Origin and JSON content type. There is no CORS
grant. Draft and submit bodies are capped at 128 KiB; publish bodies at 512 KiB.
Responses carry no-store, no-referrer, frame denial and a restrictive CSP.
The browser uses DOM text nodes to display all supplied content.

Published snapshots carry no local enrollment IDs or recipient associations
beyond the opaque card and issue IDs. Personalized text and expected values are
resolved by Faculty before publication. Public reads strip all verification
configuration, including expected values. The Worker applies verification only
on the server. Answer history includes verification outcomes but never expected
values. A magic link grants access to its recipient card and is not proof that
the intended student completed it.

## Semantics and retained records

Canonical fields: static information, acknowledgment, short/long text,
confirmation, boolean, integer, decimal, date, time, single/multiple select,
radio, and typed attestation. Conditions refer to earlier response fields;
hidden answers are discarded and do not validate, verify, or persist. Supported
verification modes are exact, case-insensitive, whitespace/case-normalized,
compact (letters and digits only, case-folded), numeric, one-of, and uin-digest
(the answer's digits are hashed with the salt from the expected `salt:hex`
value; the Worker never receives the UIN itself). A typed UIN is never stored
either: drafts drop it and submissions record only `[UIN confirmed]`. The
compact and uin-digest modes and this redaction were deployed on 2026-09-14 at
17:43 UTC (etag `fbec5bf7…`, bundle SHA-256 `74fe80c3…`) through the Cloudflare
API with `keep_bindings: ["secret_text"]`; secrets, the D1 binding, the cron
trigger, and the custom domain were preserved. Mismatches may block, flag (the default), or be accepted.
No regex engine, custom code, uploads, signatures, or downstream automation runs.

Policies are `once`, `until_due` (requires a UTC deadline), `unlimited`, and
`instructor_unlock`. Each valid submission is a new immutable record. A single
guarded SQL insert rechecks the current card policy, revocation, and time window;
its trigger updates the current response, clears the draft, consumes an unlock,
and records events in the same D1 transaction. Concurrent single-use submissions
therefore cannot both commit. Retries with the same request ID and original
answers return the existing receipt without consuming another Turnstile token.
Changing answers under the same request ID returns 409.

Statuses are issued, opened, started, submitted, verified, needs_review, expired,
and revoked. Delivery status is separately owned by Faculty. A 15-minute cron
and event reads record elapsed expirations; every public access also checks
expiry immediately. Expiry/revocation closes public access, including history,
while Faculty retains all submitted answers and verification history. Public
history is limited to the latest 20 revisions; synchronization retains every
revision. Drafts are shared across browsers using the same magic link; the last
completed draft write wins. Browser writes are serialized within one tab.

No automatic deletion or retention purge is implemented. D1 data and immutable
events must remain available until the instructor establishes a separate
retention/export policy. Faculty's local synchronized copies are included in its
normal catalog backup; they are not a substitute for unsynchronized D1 data.

Implementation references verified against Cloudflare documentation:
[D1 batch transactions](https://developers.cloudflare.com/d1/worker-api/d1-database/)
and [server-side Turnstile validation](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/).
