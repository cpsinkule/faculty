# Teaching Inbox

The fair worker rotation includes one bounded Assistant preparation discovery
turn (`ASSISTANT`, `assistant/workflows.py`). It reads local projections only;
the web process's exclusive dispatcher performs inference. Disabled rules do no
work, and failure/uncertain receipts are never retried automatically.

Teaching Inbox is a transport-independent command service inside Faculty's existing
Flask application, with an independent Python worker and a SQLite/WAL queue. The
HTTP request retains the submission and queues work. It does not execute handlers.
No message prose, attachment text, AI output, or filename can select arbitrary code.

## Available now

- **Teaching Inbox** in the sidebar: upload, search, status filters, job details, original
  downloads, results, review/correction, cancellation, retry, and audit history.
- `document.archive`: retain one or more documents under an explicit course,
  term, or section owner. The retained original attachment is the artifact;
  sharing it means referring to its ID, not copying it into descendant stores.
  Course Home shows course-wide and selected-term documents separately; section
  tabs show only their section's documents. Each file links to its submission history.
- `roster.sync`: import one CSV/XLSX export through the existing roster service.
  This uses its add/update policy, retained UIN prefix, and private enrollment IDs.
- `scan.receive`: a scan that arrives from the scanner (email or the scanner folder) or is
  uploaded in the workspace is retained, then read by `document_identity`'s `ScanService`
  (Data Matrix page labels, papers, review). It is not in `handlers.HANDLERS`.
- `accommodations.sync`, `accommodations.exams.sync` and `aim.form_capture`: workspace-scoped
  submissions with no course destination. They keep an accommodations export, a DR exam
  workbook and an AIM form's structure.
- Opaque, single-use submission codes, QR images, expiration, and revocation.
  Codes bind an available action, immutable metadata, and one permanent owner.
- API keys for scripts, plus the local command client below.
- A signed email gateway contract and deterministic parser (`POST /api/inbox/email`, 503
  until an email policy is configured). Mail reaches it through the deployed Cloudflare
  gateway (see "Email delivery" below); another provider needs only its own adapter.

Quiz, exam, grade, conversion, and announcement action names are declared in
`protocol.ACTIONS`. `handlers.HANDLERS` has no handler for them yet. Their submissions stay
in `needs_review` with the originals retained. They never return fabricated output
or completion. Register a real implementation before explicitly retrying such jobs.

## Find a submission

The sidebar opens **Current**: every unfinished submission, with what needs you first
(failed, then waiting for review) and then the worker's (running, then queued). Each
tile says its own state, progress or recorded reason, and a failed job offers
**Retry job** (a scan's in its **⋯ Scan actions** menu). **History** holds completed jobs, reviewed scans with nothing left to
do, and cancelled jobs; deleted submissions remain only in **Deleted**. The API's
`status` filters (`needs_review`, `pending`, `running`, `failed`, and `current` for
all four) are unchanged, and links to the former tabs open Current. History changes
navigation, not retained status, review gates, files or ownership. A scan's existing
overview decides whether it still needs attention.

The list's toolbar holds the Current, History and Deleted tabs, the search, the
worker's dot and a **More** menu (New submission, Use a submission code, Codes &
API access); on Current a status line counts what needs you, what is working and
what failed. Each scan card's **⋯ Scan actions** menu holds **Start over…**,
**Retry job** (failed scans, and cancelled scans already reviewed) and **Delete from Inbox…**, which uses the same
confirmation and reversible deletion as the detail page, preserving the current
view and search; the toast that follows offers **Restore**. **Cancel job…** is
only on the scan's own page, behind a confirmation, because a cancelled scan
not yet reviewed cannot be queued again. History folds runs of like submissions (same action,
scope and source, each within a minute of the last) into one row and shows
finished scans as compact cards. Scans of one grading assignment are one row too ("Exam 1 · 16 scans · 4,394 pages ·
338 papers", with how many need you, are working or have nothing to do): always in History and Archived, and in
Current once two or more share it. Until you open or close it yourself, the row opens by itself whenever one of them
needs you (and stays open); after that your choice stands. Two terms' rows with one title also name their term. Each
scan keeps its own card inside. The list reads itself every 4 s while anything listed is being worked on, every 15 s
when Current is idle and every minute on History, Archived and Deleted, and at once when you come back to the tab or to
the Inbox from another page; a link to
`#inbox?upload=1` opens **Upload scanned assignment**. Drop PDFs on the list to open **Upload scanned
assignment** with them chosen; scan pages take no drops. Processing scans must finish first; the server also
refuses scans used in reviewed assemblies. Deleted scans remain restorable from
**Deleted**, with their originals and audit history retained.

Each uploaded file is its own scan. When one scanner run came out as several
PDFs, or a late paper was scanned on its own, choose **Select scans to combine**
(Current or History), tick those scans and choose **Combine into one scan**. A new scan receives every PDF unchanged, each as
its own file (a paper never runs from one file into the next), and is inspected
from the start; work already done on the originals is not carried over. The
originals move to **Deleted** with a reason naming the new scan, and can be
restored. The dialog's request ID makes a repeated request finish the same
combination instead of making another.

Use **Search submissions** to find an older upload by filename, course destination
(including term and section), sender, source, action name, or job ID. Press Enter
or **Search**. Search covers all saved jobs, including later pages; every word
must match, and words can match different fields. Matching is case-insensitive,
and punctuation such as `%` and `_` is literal. Searches are limited to 200
characters.

View filters narrow the results, and their counts reflect the current search.
Attachment names appear in each row. Opening a job and returning to the Inbox
keeps the view, search and page; **Clear search** removes the text filter. History
can be bookmarked at `#inbox?status=history`; the sidebar always returns to Current. Background
updates preserve text you are still typing.

`GET /api/inbox/jobs?status=history` filters completed/cancelled items before
pagination, using the same derived scan state and deletion boundary. Existing
status filters and unfiltered API reads remain compatible; count keys remain the
original individual statuses, and the UI sums completed/cancelled for History.

Search is a workspace view over the existing Inbox catalog. It adds no resource
owner, copied payload, or persistent search history. It reads filenames and
submission details only, without opening retained files or searching file contents,
command metadata, or audit history.

## Command contract

A client supplies a versioned command, attachments, and an idempotency key:

```json
{
  "schema_version": 1,
  "action": "roster.sync",
  "target": {"level": "section", "id": "<permanent-course_sections-id>"},
  "metadata": {}
}
```

Use `GET /api/inbox/catalog` for targets and registered actions. A target's level
is `course`, `term`, or `section`. These resolve to `course_definitions.id`,
`course_terms.id`, and `course_sections.id`, respectively. IDs are retained UUIDs;
existing private ten-digit student enrollment IDs are unchanged.

Explicit human-readable context is also accepted instead of `target`:

```yaml
schema_version: 1
action: roster.sync
course: MATH140-501
term: Fall
year: 2026
metadata: {}
```

A shorthand must resolve to exactly one saved owner. Multiple possible years or
terms require instructor review; the service never chooses the current term by
assumption. For Other terms, provide `term: Other` and `term_other: <identifier>`.
The UI always supplies permanent targets and displays their full course context.

Metadata is bounded, JSON-compatible data. JSON or safe YAML is supported, with
duplicate keys, arbitrary object tags, anchors, aliases, excessive nesting, and
non-finite numbers rejected. Quote dates as strings. Quiz numbers, version labels,
and publish flags have explicit type checks. New handlers must validate their own
additional metadata schema before causing effects; AI may supply proposed fields,
but the allowed action registry and those validators remain authoritative.

The service assigns message/job/artifact IDs, source, authenticated sender identity,
received time, and software version. Callers cannot impersonate a transport by
setting `source` or `sender` in a command. Downloadable inputs and outputs are
referenced by opaque artifact IDs and checked against SHA-256 digests.

## HTTP API

Web controls use Faculty's existing same-origin header, with no login or browser
session cookie.
Machine clients use `Authorization: Bearer <Inbox API key>`. Create/revoke keys in
**Codes & API access**. Tokens are displayed once and only their SHA-256 hashes are
stored. Keys authorize Inbox submissions, reads, review, cancellation, and retry;
they cannot manage keys/codes or grant the separate consequential-action approval.
Faculty still relies on its existing private Tailscale/SSH access boundary.

`POST /api/inbox/submit` accepts either multipart form data:

- `command`: a JSON or YAML command string;
- repeated `files`: attachment uploads;
- `idempotency_key`, or the `Idempotency-Key` header.

Or JSON:

```json
{
  "command": {
    "action": "document.archive",
    "target": {"level": "course", "id": "<course-definition-id>"},
    "metadata": {"description": "Course handout"}
  },
  "attachments": [{"filename": "handout.txt", "content_base64": "SGVsbG8="}],
  "idempotency_key": "my-script-handout-2026-09-13"
}
```

An accepted request returns HTTP 202 with the retained job. Invalid commands that
can be retained enter `needs_review`; oversized/broken transport requests fail
before intake. At most 20 attachments are accepted, each at most 10 MB, totaling
25 MB; an email to a scanner intake address takes each attachment up to 25 MB. Commands are limited to 16 KB; raw gateway emails to 35 MB. The HTTP body
limit allows base64/multipart overhead. Existing course APIs retain their limits.

Retry identical requests with the **same idempotency key**. The same source and
principal receive their existing job; changed content under that key returns 409.
Email delivery IDs are unique across the gateway, independent of the From header.
There is no automatic content deduplication across different submission keys.

Other routes:

| Route | Purpose |
| --- | --- |
| `GET /api/inbox/jobs?q=handout&status=needs_review&offset=0` | Search before pagination; fifty jobs per page, matching counts, worker heartbeat; with `files=input` (the Inbox's own read) a scan row's `scan` adds `assignment` ({plan_id, title, code, term} or null), `pages` and `papers`; the default body is unchanged |
| `GET /api/inbox/jobs/<id>` | Command, status, files, result, audit history |
| `GET /api/inbox/course-groups/<id>/resources?section=501` | Completed archived documents, separated by course/term/section owner |
| `POST /api/inbox/jobs/<id>/review` | `{version, command}` correction before first execution |
| `POST /api/inbox/jobs/<id>/retry` | `{version}` explicit retry of the same command |
| `POST /api/inbox/jobs/<id>/cancel` | `{version}` cancel queued work; retain all files |
| `POST /api/inbox/jobs/<id>/approve` | `{version}` separate instructor approval |
| `GET /api/inbox/artifacts/<id>/download` | Verified attachment/result download |
| `GET /api/inbox/messages/<id>/original` | Exact original submission or raw email |
| `POST /api/inbox/codes` | `{command, label, hours}` one-time code plus QR SVG |
| `POST /api/inbox/codes/resolve` | `{token}` instructor view of bound command |
| `POST /api/inbox/keys` | `{label}` API key shown once |
| `GET /api/inbox/access` | Redacted code/key lists and email configuration status |
| `POST /api/inbox/access/codes/<id>/revoke` | Revoke a code |
| `POST /api/inbox/access/keys/<id>/revoke` | Revoke an API key |

The jobs route accepts optional `q` and `status` filters. `counts` includes each
status's count for `q` regardless of the selected status; `total` counts jobs
matching both filters. `offset` and `has_more` apply to that filtered result.
Without `q`, the route lists the full history as before.

A code encodes only `ti:<random-token>`. It can be scanned/pasted into **Use a
submission code**, or opened through the provided private submission link. QR
rendering is local; the server stores only the token hash. Codes last 7 days by
default (configurable 1–2160 hours), can be used once, and support implemented,
non-consequential actions only. POST with `X-Teaching-Submission-Code` accepts
attachments for that bound command; it cannot override the action or target.
A duplicate of its first identical request can return the original receipt, but
another request cannot consume it twice. The code itself authorizes upload only;
its response contains just job ID, status, and received time. The existing private
workspace is still required to inspect the full job. A camera/scanner application
is not included; the QR represents the code, not a general public upload portal.

## Worker, retries, and approval

The queue states are `pending`, `running`, `completed`, `failed`, `needs_review`,
and `cancelled`. SQLite transactions and an exclusive cross-process worker lock
prevent two workers from executing a job simultaneously. Receiving a job never
runs it in the HTTP request. The worker checks action, owner, metadata, authentication,
and any required approval again when it claims work.

A worker process exit leaves a durable `running` row. When the lock is available,
the next worker marks that row `needs_review` and records the interruption. It
requires an explicit retry; potentially consequential side effects are not blindly
replayed. Only the original command can be retried once execution has been attempted.
Changing an attempted job requires a new submission, preserving its audit history.

`roster.sync` uses the job ID as a persistent import request ID, so retries do not
repeat a committed roster import. Each handler result has a durable receipt tied
to the exact command hash, written before the job is marked completed. A restart
can finish from that receipt without repeating the handler. This is not a general
exactly-once guarantee for external systems: future handlers must use their own
idempotency keys/receipts and reconcile uncertain external outcomes.

Publishing/modifying grades, publishing quizzes, releasing exams, and any command
with `metadata.publish: true` require a separate instructor approval. The approved
hash covers the exact normalized action, permanent target, and metadata. Changing
or retrying the command clears approval. API keys and submission codes cannot
supply this approval. The current consequential actions have no real handlers, so
they cannot execute even if an approval is attempted. Approval does not substitute
for authentication; a failed sender-authentication job cannot be approved/retried.

## Storage and ownership

`inbox_messages` is a retained submission/payload owner. Its explicit foreign keys
point at the owning course definition and, when appropriate, its term and section.
`inbox_jobs`, `inbox_artifacts`, and append-only `inbox_events` reference that message
and job. Unclassified or unauthenticated submissions remain workspace-level audit
records staged on NVMe, without a guessed course owner. Review explicitly assigns or
corrects scope, records the previous command, and preserves the original bytes.

Managed storage uses:

- HDD `inbox/<message-id>/snapshots/<snapshot-id>/`: retained, verified originals,
  input files, and output receipts, published by the nightly backup;
- NVMe `inbox/<message-id>/data/`: initial intake and processor results for every
  scope, including unclassified mail; current scope owners keep this working copy;
- NVMe `catalog/faculty.sqlite3`: routing/index metadata, jobs, audit events, opaque-code
  hashes, API key hashes, and backup pointers.

These are under the configured `/mnt/bulkdata2/Teaching` and `/mnt/nvme/Teaching`
roots. `InboxStorage` reuses the existing snapshot/verification/purge engine.
Course-wide payloads stay hot while any offering of that code is current. Term
payloads stay hot while any section of that term is current. Section payloads use
that section's current status. All are included in nightly snapshots and catalog
backups. A final verified snapshot is committed before the NVMe copy is purged.
Failure retains the working copy. `staged` receipts await nightly backup even when
unclassified or non-current; normal reconciliation never snapshots them to HDD.
Archived edits and worker results restore/stage on NVMe until the next nightly run.
The nightly coordinator holds the common lifecycle lock and copies owners one at
a time; request threads do not launch Inbox HDD copies. The live catalog uses
SQLite WAL on NVMe, and coordination lock files are on NVMe too. A durable gateway
acknowledgment means the original, attachments, and catalog commit have been flushed
to NVMe; it does not mean an HDD backup has already run.

Course edits/removals cannot detach retained scoped Inbox files from their final
visible offering. Archive via the Current checkbox. Explicit Inbox command review
can correct ownership before execution, with the original command retained in the
audit history. All mutation/file access coordinates through the existing roster
lifecycle lock, then owner locks and catalog transactions. Jobs do not put new
shared resources under a saved `courses.id` entry.

Raw originals, input attachments, audit events, and outputs have no automatic
pruning. Cancellation does not delete them. File publication precedes catalog
references; a crash before the catalog commit can leave an unreferenced verified
snapshot or staging directory, which is retained for recovery rather than deleted.
The metadata JSON workspace export excludes Inbox contents; full server backups
include the queue, audit history, original files, and artifacts. A restored queue
still requires review of interrupted execution and external side effects.

## Local CLI and deployment

```bash
# Existing private server storage is used by default.
.venv/bin/python manage_inbox.py submit --command command.yaml --attach roster.csv --idempotency-key roster-fall-501
.venv/bin/python manage_inbox.py jobs
.venv/bin/python manage_inbox.py show <job-id>
.venv/bin/python manage_inbox.py worker --once
.venv/bin/python manage_inbox.py worker

# Optional isolated development workspace:
.venv/bin/python manage_inbox.py --data-dir /mnt/nvme/Teaching/tmp/inbox-demo jobs
```

The local CLI has the service user's filesystem authority. It uses the same submit
function with `source=cli`; a remote script should use a revocable bearer API key.
`manage_inbox.py api-key "Script label"` explicitly creates/prints a key once.

The native deployment adds `deploy/faculty-inbox.service` beside `faculty.service`
and `faculty-backup.timer`. Start it with:

```bash
cp deploy/faculty-inbox.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now faculty-inbox.service
journalctl --user -u faculty-inbox.service
```

Docker Compose includes an `inbox-worker` service sharing the development volume;
that optional workspace does not replace the configured HDD/NVMe deployment.

## Email delivery: signed gateway contract

The [deployed Cloudflare gateway](../cloudflare/teach-inbox/README.md) receives mail
for `inbox.example.com` and the short scanner destination `scan@example.com`.
Scanner and personal intake use configured addresses and allowed senders, with no
typed message or command secret. The public short address also requires a private
instructor workspace review before any execution; sender matching is only a filter.
Older opaque aliases remain available. All intake addresses enter
Needs review under `scan.receive`; QR recognition is future work described in the
[scanner design](CLOUDFLARE_EMAIL_PLAN.md). The contract below describes the
separately configured authored-command path, which remains supported.

`POST /api/inbox/email` returns 503 when no email policy is configured. To connect another
provider, write a small adapter that verifies that provider's native webhook
signature, obtains the actual SMTP envelope recipient and complete raw RFC822
message, and forwards this exact JSON body:

```json
{"recipient":"teach+roster@example.com","raw_email_base64":"<raw RFC822 bytes encoded as base64>"}
```

The adapter signs the **exact transmitted body bytes** with HMAC-SHA256:

```text
X-Teaching-Timestamp: <Unix seconds>
X-Teaching-Delivery: <stable unique provider delivery ID>
X-Teaching-Signature: <hex HMAC(secret, timestamp + "." + delivery_id + "." + body)>
```

Signatures must be no more than five minutes old. Retries keep the same delivery ID
and body, with a fresh timestamp/signature. Configure the Faculty API process with:

- `TEACH_INBOX_WEBHOOK_SECRET`: at least 32 characters, shared only with the adapter;
- `TEACH_INBOX_EMAIL_COMMAND_SECRET`: separate instructor command secret, at least 32 characters;
- `TEACH_INBOX_EMAIL_SENDERS`: comma-separated allowed sender addresses;
- `TEACH_INBOX_EMAIL_ROUTES`: JSON mapping exact envelope recipients to registry actions.

No example secrets are installed. The additional command secret is required
because a signed delivery and an allowed From address alone cannot establish that
the author is the instructor. It belongs in the explicit front matter:

```yaml
---
course: MATH140-501
term: Fall
year: 2026
action: roster.sync
command_token: <instructor command secret>
---
```

The verified envelope recipient determines the action. Front matter cannot override
it. Subject shorthand such as `MATH140-501 Quiz 7 B` supplies only deterministic
fields, with explicit front matter taking precedence; it never resolves ambiguous
years by guesswork. Arbitrary body prose and raw Authentication-Results headers
are not trusted. Validly signed deliveries that fail sender/command authentication
are retained for audit but cannot execute. Invalid gateway signatures are rejected
before disk intake. Keep this separate gateway route isolated from the private
instructor UI if a future provider needs public ingress.

The parser uses Python's [email parser](https://docs.python.org/3/library/email.parser.html),
[PyYAML's safe loader](https://pyyaml.org/wiki/PyYAMLDocumentation), and
[constant-time HMAC comparison](https://docs.python.org/3/library/hmac.html).
QR codes use the [qrcode SVG encoder](https://pypi.org/project/qrcode/8.2/).
