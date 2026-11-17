# Faculty Cloudflare email gateway

Deployed for `example.com` on 2026-09-13. This Worker transports raw mail to the
existing Teaching Inbox; it does not parse assessments or execute course actions.
The workspace owns intake policies and unclassified receipts. Reviewed documents
use permanent course definition, term, and section owners through existing Inbox
storage. No new course/student identities or copies of shared resources are created.

## Deployment

- Scanner destination: `scan@example.com` (an exact, review-only rule).
- Existing scanner and personal opaque aliases at `inbox.example.com` remain
  enabled. Neither domain has a catch-all.
- Worker: `faculty-email-gateway`; HTTP workers.dev and preview URLs disabled.
- Private Standard R2 bucket: `faculty-email-buffer`.
- Delivery queue: `faculty-email-deliveries`, 14-day retention, batches of one,
  maximum concurrency one, five retries at five-minute intervals.
- Dead-letter queue: `faculty-email-failed`, 14-day retention.
- Recovery cron: every 15 minutes. Requeues pending R2 originals older than one hour,
  walking up to 100 objects per invocation with a persistent pagination cursor.
- Tunnel: `faculty-email-intake`, ID `00000000-0000-0000-0000-000000000000`.
- Public listener: `https://intake.example.com/api/inbox/email` → dedicated
  loopback listener `127.0.0.1:3001`. Both Tunnel ingress and WSGI middleware restrict
  the path; middleware additionally requires POST without query parameters.
- Existing Faculty UI remains on its private listener, accessed through Tailscale.

The user services are `faculty-email-gateway.service` and
`faculty-email-tunnel.service`. Their source units are in `deploy/`. Cloudflared is
installed at `.tools/cloudflared`; its pinned release version and the official release
asset SHA-256 digest it was verified against are recorded in
`.tools/cloudflared-release.json`. Its credential is read from a file, never a command
argument.

Private files, outside version control:

- `~/.config/faculty/email.env`: gateway HMAC and hashed intake policies.
- `~/.config/faculty/email-addresses.json`: addresses to save in the scanner and
  personal email address books. The short scanner address is public; the older
  opaque aliases are limited submission capabilities.
- `~/.config/faculty/tunnel-token`: Tunnel connector credential.
- `.tools/email-bootstrap.json`: protected local deployment configuration used to
  install the matching Cloudflare secrets. Treat this as a secret file.

`INTAKE_POLICIES` and `TEACH_INBOX_WEBHOOK_SECRET` are Cloudflare Worker secrets.
The former maps SHA-256 hashes of lowercase recipients to sender lists, labels,
and optional `review_only: true`; secret aliases are only in Email Routing rules and address-book
configuration. Cloudflare's own mail logs may retain envelope information.
Application logs contain receipt IDs, event names, and HTTP status codes only.
Automatic invocation logs are disabled and trace sampling is zero to avoid
copying mail metadata into application traces; structured operation logs remain on.

## Intake behavior

There are separate saved addresses for the department scanner and instructor mail.
The scanner policy permits `dept-scanner@tamu.edu`,
`dept-copier@tamu.edu` and `dept-bw@tamu.edu` (the BLOC 316A copier
also sends from the latter; the Worker refused a quiz scan from it, and an email the
Worker refuses is not retained; requirement, 2026-11-05). All use the same saved scanner destination.
`scan@example.com` also permits the instructor's own scanners' cloud senders,
`noreply@print.epsonconnect.com` (Epson Connect) and `FromBrotherDevice@brother.com`
(Brother) (requirement, 2026-10-17).
Instructor intake permits
`instructor@example.com`, `instructor@example.net`, and `instructor@tamu.edu`.

The Worker checks the exact configured destination and SMTP envelope sender. The
server independently checks that destination and both the signed envelope sender
and MIME From address. This narrows submission access; it is not proof of who used
a shared scanner, and no arbitrary Authentication-Results header is trusted.

All intake addresses accept empty-body submissions and create **Needs review** jobs with
fixed action `scan.receive`. Subjects, YAML, filenames, and printed QR content cannot
choose an operation. In the private Inbox, review the job to choose an existing
action and permanent destination. Consequential actions retain separate approval.
Unrecognized sender/authentication failures cannot be approved or executed.

`scan@example.com` is deliberately easy to type, and is not a secret credential.
Its policy sets `review_only: true`. The workspace-owned receipt records
`instructor_review_required=1`; only the instructor's review in the private workspace,
or the worker's automatic review of an authenticated scan with nothing open about the
whole scan (SCANNER-INTAKE; DOCUMENTS), clears it. API-key review/retry, direct
approval, and worker execution of a job still cannot clear it, and sender matching
alone never does. A review audit retains the exact chosen command. The additive catalog
migration runs under the roster lifecycle lock and preserves older receipt policies.
Future QR processing must honor this gate until stronger sender authorization is
implemented; sender address matching alone must never enable automatic execution.

The existing v1 authored-email protocol remains supported when separately
configured, including its separate command secret. No authored-command routes
were enabled by this deployment. No outbound email is sent automatically.

Data Matrix document registration and inspection are implemented (SCANNER-INTAKE,
DOCUMENTS); QR recognition and scanner cover-sheet generation remain future work.
The existing one-time `ti:` upload codes are not document markers.

Cloudflare limits the complete incoming email to 25 MiB. Faculty accepts up to 20
attachments with a combined 25 MiB limit; each may be up to 25 MiB on a scanner intake
address and up to 10 MiB on an authored command address (SCANNER-INTAKE "A SCAN
EMAIL'S PDF MAY BE AS LARGE AS THE EMAIL": the B&W copier's 278- and 284-page scans of
2026-11-14 were 10.2 and 11.1 MiB). Oversized or malformed MIME submissions are retained
as original emails for audit, without separately extracted attachments, and their job
says what refused them.

## Signed envelope v2

```json
{
  "schema_version": 2,
  "recipient": "<actual SMTP envelope recipient>",
  "sender": "<actual SMTP envelope sender>",
  "received_at": "<UTC time of first receipt at the Worker>",
  "raw_email_base64": "<unchanged RFC822 bytes>"
}
```

The original exact-body HMAC scheme is unchanged:
`HMAC-SHA256(secret, timestamp + "." + delivery_id + "." + body)`.
The UUID delivery ID, envelope, and received_at are persisted in R2 and reused on
all HTTP retries. Fresh SMTP receipt/rescan events get new delivery IDs; an
untrusted Message-ID does not deduplicate separate deliveries.

Incoming email uses a `FixedLengthStream(rawSize)` for R2, since EmailEvent.raw has
no intrinsic content length. Receipt waits for both the stream and R2 write; a
size mismatch or storage failure never publishes a queue reference. See the
[Cloudflare stream contract](https://developers.cloudflare.com/workers/runtime-apis/streams/transformstream/#fixedlengthstream).
Two streaming R2 reads generate identical JSON: one computes the HMAC and body
SHA-256, the other sends the bytes. This avoids buffering multiple copies of a
large base64 message in Worker memory. The server retains the raw original and
logs the envelope sender, hashed recipient, transport body hash, and chosen policy.

A 202 acknowledgment follows flushed NVMe file publication and a committed NVMe
catalog transaction, including unclassified scans. HDD backup is deferred to the
nightly coordinator (4:00 am Central time). Non-current Inbox payloads are removed from NVMe
only after that run verifies their final HDD snapshot; current payloads stay hot.
The acknowledgment includes the matching `delivery_id`, `body_sha256`, and Inbox
job ID. Only that acknowledgment permits removal of the temporary R2 original.
Network errors, redirects, server errors, and malformed acknowledgments retain the
original and retry. Inbox idempotency protects against a lost success response.

## Recovery and operations

- **Home server offline:** originals remain at `pending/<delivery-id>` in R2.
  Queues retry; scheduled recovery can requeue even after queue expiry or initial
  enqueue failure. The R2 bucket has no automatic deletion lifecycle.
- **Permanent HTTP rejection:** a `blocked/<delivery-id>` object records the status
  and time; the original stays in `pending/`. Inspect the configuration or original,
  fix the cause, then remove **only that blocked marker** in the private R2 dashboard.
  Recovery will requeue the original. Never delete pending originals to clear errors.
- **Configuration repair:** restart `faculty-email-gateway.service` after editing
  `email.env`; also restart `faculty.service` to update its readiness display.
  Keep Worker and server policies/secrets in sync.
- **Alias rotation:** create a new random alias and hashed policy on both ends,
  update the scanner contact, then disable the old Email Routing rule and policy.
- **Monitor:** Cloudflare Worker logs show `storage_failed`, `queue_recovery_pending`,
  `delivery_retry`, `delivery_needs_attention`, and `recovery_failed`. Inspect the
  dead-letter queue and pending/blocked R2 objects. No email alerting is enabled.
- **Backups:** accepted originals and attachments stage on NVMe. InboxStorage
  publishes verified HDD snapshots only through the coordinated nightly job, which
  also backs up the live NVMe catalog. Current scoped resources
  retain NVMe copies under the existing lifecycle. Run `manage_storage.py backup`
  or `faculty-backup.service` only for an explicitly requested on-demand backup;
  normal delivery must not trigger them. The nightly timer remains in place.

## Build, test and redeploy

Use Node 22.12+ and the committed npm lockfile:

```sh
npm ci
npm run types
npm run check
npm test
npm run build
```

Tests run in a local Cloudflare runtime with isolated R2 and synthetic mail.
The initial deployment used the authenticated Cloudflare MCP API to upload the
Wrangler-built module and matching bindings. For later Wrangler deployment, sign
in to the same Cloudflare account and use `npx wrangler deploy`; existing Worker
secrets are retained. Set replacement secrets with `wrangler secret put` via
private stdin. Do not put values in shell arguments or committed config.

Queue retention and the exact Email Routing rules are account resources configured
through the API, not recreated by `wrangler deploy`. Preserve them when deploying.
`wrangler.jsonc` owns Worker bindings, consumer configuration, cron, and limits.

Validation on 2026-09-13: 110 Python tests, ten Worker runtime tests, generated
binding types, TypeScript check and Wrangler dry-run passed. Public HTTPS checks
rejected private routes and unsigned requests. One synthetic signed submission
verified durable HDD receipt, identical retry and changed-body rejection; its job
was cancelled with the audit/original retained. The first personal forward reached
Cloudflare but failed before R2 storage; a regression test reproduced the unknown-length
stream failure and the fixed-length adapter resolves it. A re-sent personal email
with a PDF completed the real SMTP → R2 → queue → server path at 13:16 UTC; the
original and attachment are retained in Needs review. A separate chunked signed
HTTP check verified the short address's review gate; its synthetic job was cancelled.
Actual scanner-origin delivery to the short address still needs a scanner sample.

The NVMe staging update moved the live catalog and lock files to NVMe, retained
the pre-migration HDD catalog, and restored the received personal email's working
copy to NVMe. A live signed intake/cancellation check left the entire HDD file
inventory unchanged. The nightly backup timer was preserved; no additional HDD
backup was run during this update. Concurrent intake, deferred processing outputs,
nightly verification failures, and offline catalog migration recovery are tested.
