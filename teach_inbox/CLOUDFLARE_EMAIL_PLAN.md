# Cloudflare email gateway and scanner intake

Status: the email gateway and bounded scanner/personal intake were implemented and
deployed on 2026-09-13 for `inbox.example.com`, with the shorter scanner destination
`scan@example.com` added after the scanner's address-entry limits were reported. See the
[deployment and operations notes](../cloudflare/teach-inbox/README.md). Scanner
intake requires no typed message and starts in Needs review. Data Matrix document
registration, page-label reading, and scan processing (`scan.receive`, read by
`document_identity`'s `ScanService`) have since been built (SCANNER-INTAKE). QR
resolution and cover-sheet generation are not built; QR support is deferred unless
needed. The design below records the full workflow, including the parts not built.
The public short address requires explicit instructor workspace review before
execution (or the worker's automatic review of an authenticated scan with nothing open
about it); automatic QR processing there will need stronger sender authorization.

The user confirmed that the department scanner can save a destination address and
that documents or cover sheets can include QR codes. A scanner sends attachments
without a user-written subject or body. No workflow may require typing a secret,
YAML, course identifier, or other message at the scanner.

## User workflow

1. Save one destination under a recognizable contact such as **Teaching Inbox**.
2. Scan documents that have document-instance QR codes, or put a generated cover
   sheet in front of a batch with one shared destination and operation.
3. Select that saved contact and send. Faculty retains the originals, resolves the
   document references, and queues only permitted, unambiguous operations.
4. Missing, unreadable, unknown, expired, or conflicting references go to
   **Needs review**. Never silently select the current semester or infer an
   executable operation from prose or an LLM.

A scanner address scoped to a particular operation/course term is an optional
fallback for documents without QR codes. A general scanner address alone cannot
identify the course, term, section, assessment, or version of arbitrary scans.

## Separate three responsibilities

| Responsibility | Mechanism |
| --- | --- |
| Authenticate gateway delivery | HMAC from a secret stored in Cloudflare and on the home server |
| Permit scanner intake | An explicitly configured sender filter and either an opaque alias capability or a short address restricted to instructor-reviewed submissions |
| Identify document and permitted processing | Server lookup of an opaque document-instance QR reference, bounded by the scanner policy |

The gateway HMAC proves which integration delivered the bytes; it does not prove
who used a shared department scanner. Neither the MIME From header nor the SMTP
MAIL FROM value by itself establishes the instructor's identity. A destination
saved in a shared scanner is visible to other users or administrators and must be
treated as a limited submission credential, never an administrative credential.

Scanner intake must have its own policy instead of requiring the existing
`command_token` in the email body. Keep the existing authored-email authentication
path unless explicitly replaced. In particular, the Worker must not add the
global instructor command secret to arbitrary incoming mail to make it pass.

Use a cryptographically random, revocable alias such as
`scan-<opaque-token>@inbox.<domain>`, saved once under a friendly scanner contact.
Where the scanner cannot accept that address, use the deployed short address with
`review_only: true`. This records a durable instructor-review gate on the receipt;
API keys and automatic workers cannot clear it. The short address is not a secret.
Restrict it to expected sender addresses and allowed ingestion operations. Check
actual institutional sender authentication when onboarding the scanner: an
attacker's passing SPF or DKIM result for an unrelated domain is insufficient.
Authentication results must come from a verified receiving-system boundary or
independent verification, not an arbitrary `Authentication-Results` header in the
original email. If the provider API cannot supply suitable evidence, do not claim
that sender authentication was verified; rely on the bounded alias capability and
retain the item for review where stronger authorization is required.

The scanner policy permits receipt and defined processing only. It does not grant
permission to publish grades, release exams, delete records, or select arbitrary
handlers. Consequential operations retain the separate instructor approval step.

## Printed references and ownership

Use a separate namespace for durable printed document-instance references. Do not
reuse the existing expiring, single-use `ti:` web-upload authorization codes.
The printed reference is an opaque lookup identifier, not a secret or approval.
It must not expose a UIN, the instructor's private ten-digit enrollment ID, or an
API key. A reference can be copied, so processing must not treat possession as
proof of who submitted or completed the document.

Resolve each document to the existing permanent owners:

1. `course_definitions.id`: the shared source/template, when applicable.
2. `course_terms.id`: the specific term/year instance of that resource.
3. `course_sections.id`: the section instance, when applicable.
4. `student_enrollments.id`: the student in that course term, when applicable;
   this link stays on the server and preserves existing roster identity rules.

Store the allowed processing profile, assessment/version, and page identity with
the document instance. References to a shared source do not transfer ownership
of term- or student-specific results back to the shared source.

Prefer a document/page marker on each generated page for assessments. A cover
sheet can route a homogeneous packet, but does not prove that every following
page belongs to it. Mixed courses, sections, students, duplicate pages, missing
pages, and conflicting codes need explicit batch rules and validation before
automatic processing. A student QR alone cannot identify an assessment/version.

The scan processor (`document_identity`'s `ScanService`) resolves these references
locally; the validated child jobs for the registered handlers that this design
describes are not built. A general scanner route selects a fixed scan-intake pipeline
(`scan.receive`), but a printed code or an LLM cannot invent or authorize a new
operation. OCR/AI can propose fields after this boundary; uncertainty and conflicts
require review.

## Gateway and delivery reliability

```text
Department scanner: saved contact + attachments
    → Cloudflare Email Routing
    → Email Worker: preserve raw RFC822 bytes and SMTP envelope
    → durable transport buffer and retry queue
    → signed POST /api/inbox/email through a restricted gateway
    → retained Teaching Inbox receipt
    → local document-reference/scan processor
    → validated course/term/section jobs
```

Cloudflare owns delivery, buffering, and retries. Faculty owns course identities,
document registration, command policy, job execution, and the permanent audit.

Buffer raw email in private R2 storage and queue only an opaque reference, so a
home-server outage does not discard a scanner submission. Persist one delivery
ID with each buffered receipt and reuse it, the same envelope, and the same body
on retries; create a fresh signature timestamp for each delivery attempt. An
untrusted Message-ID alone must not be the unique identity. A deliberate rescan
is a new receipt and requires document-level duplicate handling separately.

Keep failed/dead-letter deliveries visible and retained until resolved. Remove a
transport copy only after Faculty confirms durable acceptance. The permanent raw
email and catalog are first flushed to NVMe; acknowledgment does not trigger an HDD
copy. The existing nightly coordinator (4:00 am Central time) serially backs up staged Inbox
payloads and the NVMe catalog, verifying final copies before non-current NVMe purge.
The raw
message, input attachments, route/authorization decision, command changes, output
artifacts, and audit remain under Faculty's backup rules. Do not log raw email,
student data, secret aliases, authorization codes, or secrets in Worker logs.

Retain the existing exact-body HMAC contract:

```text
HMAC-SHA256(secret, timestamp + "." + delivery_id + "." + request_body)
```

Version any envelope additions needed for the actual SMTP sender or trusted
authentication evidence; they must be covered by that signature. Do not mutate
the original raw email or silently change the existing version-one contract.

Use Cloudflare Tunnel with a dedicated gateway listener that permits only
`POST /api/inbox/email`. All other methods and paths must fail closed at that
listener. A hostname pointing at the whole Faculty service is not sufficient
isolation. Keep the instructor UI, rosters, artifacts, job controls, and API-key
management private through the existing Tailscale/SSH boundary.

Course-owned payloads use the existing storage manager and lifecycle locks.
Current data remains on NVMe with nightly HDD snapshots and verified final-copy
before purge. Initial unclassified receipts are workspace audit records; explicit
resolution assigns scoped resources. When child jobs share retained raw data,
account for every current descendant before releasing its NVMe copy.

## Implementation sequence and open setup details

1. Add the scanner-specific intake policy and tests with synthetic empty-body
   emails. Verify restricted routes, spoofed headers, replay behavior, rejection
   of privileged operations, and review of unresolved context.
2. Build and test the transport Worker, durable buffering/retries, and isolated
   home-server gateway. Do not assume a thrown Email Worker exception guarantees
   a later delivery attempt.
3. Select/onboard the user's domain, configure the narrow ingress and scanner
   alias, provision secrets privately, and validate with an actual scanner sample.
4. Document registration, printable page labels, page resolution, and scan
   processing are built (`document_identity`); cover sheets and the actual teaching
   processors for the declared actions wait until requested. A scan is reviewed by the
   instructor, or automatically when it is authenticated with nothing open about it
   (SCANNER-INTAKE); actions without a handler are retained for review, not falsely
   marked processed.

The initial design inspection found no domains and provisioned no resources.
Later on 2026-09-13 the user registered `example.com` and enabled the required
services. The gateway, tunnel, bucket, queues, and scanner/personal routing rules
are now deployed. A real scanner sample is still needed to validate actual SMTP
sender behavior, authentication, message size, and page structure.

Cloudflare currently documents a 25 MiB inbound message limit, including MIME
overhead. Faculty currently limits each imported attachment to 10 MB. Validate
the scanner's actual PDF size and the whole gateway pipeline before activation;
increasing only the provider limit or only the HTTP limit is insufficient.

## Sources checked

- [Email Worker interface](https://developers.cloudflare.com/email-service/api/route-emails/email-handler/)
- [Inbound lifecycle](https://developers.cloudflare.com/email-service/concepts/email-lifecycle/)
- [Mail authentication requirements](https://developers.cloudflare.com/email-service/reference/postmaster/)
- [Email Routing setup](https://developers.cloudflare.com/email-service/get-started/route-emails/)
- [Email limits](https://developers.cloudflare.com/email-service/platform/limits/)

The design above is the teaching stack's proposed policy, not a claim that
Cloudflare supplies document routing, scanner identity, or exactly-once execution.
