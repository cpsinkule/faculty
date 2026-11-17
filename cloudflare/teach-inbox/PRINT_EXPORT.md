# Print email export: deployment and operation

**Retired from Faculty on 2026-11-01.** Export by email now sends from the instructor's
connected Gmail (DOCUMENTS "PRINT EMAIL FROM GMAIL"), and Response Card and seating chart
mail default to Gmail since 2026-10-16, so Faculty no longer calls this Worker. Its last
send through Resend was on 2026-10-16. The record below is kept as history; the Worker
and its `RESEND_API_KEY` secret were left deployed.

Status: deployed and enabled in Faculty on 2026-09-13 at 17:53 UTC after explicit
instructor approval of the sending domain, authentication DNS and Worker. All
three approved instructor addresses and custom recipients are available. Live
authentication/configuration checks passed without sending test mail. The first
instructor-requested export to `instructor@tamu.edu` was accepted for delivery at
17:55:15 UTC; this confirms provider acceptance, not arrival in the mailbox.

## Deployment

| Resource | Configuration |
| --- | --- |
| Existing zone | `example.com` (`00000000000000000000000000000000`) |
| Sending domain | `print.example.com`, enabled; ID `00000000000000000000000000000000` |
| Mail authentication DNS | Cloudflare's generated bounce MX, SPF, DKIM, and DMARC records are ready with no reported errors |
| Sender | `faculty@print.example.com` |
| Transport Worker | `faculty-print-export`, configured by `wrangler.print-export.jsonc` with private `EXPORT_SECRET`; deployed version `00000000000000000000000000000000` |
| Public endpoint | `https://faculty-print-export.example.workers.dev/send`; every request requires a short-lived signature over its exact body |
| Faculty configuration | Relay URL and matching secret in `.config/faculty/print-email.env`, mode 0600, loaded by the private app and Inbox worker |

This separate transport has no catalog, inbound R2/queue bindings, or teaching
policy. Existing scanner routes, secrets, public port 3001 and email gateway remain
unchanged. The sender restriction is declared on the native email binding. Custom
recipients require the sending domain to be onboarded, rather than merely an
inbound routing destination.

Sent-message previews are disabled on the sending domain. Worker preview URLs
and invocation logs are disabled; trace sampling is zero. Application logs contain
only export IDs and fixed status events.

Cloudflare's connected tool cannot create an account API token with its current
permissions. The native email binding avoids storing an account API token in
Faculty. A dedicated random signing secret grants only this outbound transport.

## Runtime configuration

These settings belong to the workspace, not to any course or section. Keep the
file mode 0600 outside version control:

```dotenv
FACULTY_PRINT_EMAIL_APPROVED=instructor@tamu.edu,instructor@example.com,instructor@example.net
FACULTY_PRINT_EMAIL_URL=https://faculty-print-export.example.workers.dev/send
FACULTY_PRINT_EMAIL_SECRET=<dedicated-random-secret-at-least-32-characters>
```

Set the same secret through the Worker's secret-management API/stdin mechanism;
never put it in Wrangler configuration, command arguments, ordinary logs or source
control. The three addresses above are the existing approved instructor intake
addresses. Scanner device senders are not suggested export recipients. A custom
export address is used for that send only; it does not change intake authorization
or the approved-address configuration.

The app and `faculty-inbox.service` both load this file. Without a valid relay URL
and secret, email sending remains disabled, while downloads continue working.
Activation applies `002_print_email.sql` under the existing lifecycle lock. Keep a
verified catalog backup before migration and restart app/worker after configuration.

## Plain messages

Status: deployed on 2026-09-14 at 17:25 UTC and again at 22:30 UTC (version
etag `d3d86cdb…`, bundle SHA-256 `16b168d4…`) through the Cloudflare API with
`keep_bindings: ["secret_text"]`. Plain messages are sent as
`Professor Sinkule <exam@example.com>` (`MESSAGE_FROM`, `MESSAGE_FROM_NAME`);
print exports keep `faculty@print.example.com`, and the email binding allows
both senders. The apex `example.com` was onboarded as a sending domain on
2026-09-14 at 22:27 UTC (ID `00000000000000000000000000000000`, return path
`cf-bounce.example.com`); Cloudflare added its bounce MX, SPF, DKIM, and a
`_dmarc.example.com` `p=reject` record, all reported ready. The account's
Email Sending quota is 200 messages per day (`/email/sending/limits`), which
bounds one exam's invitations plus confirmations; a larger class needs a
quota increase or staggered confirmations. Live checks: GET `/message` 404,
unsigned POST 401, and one signed instructor-requested test message to the
instructor's own address was accepted by the provider. Should a relay without
this route ever answer 404 again, Faculty keeps queued student mail pending and
retries every five minutes rather than failing it.

The same Worker accepts `POST /message` with the same short-lived signature over
the exact body, for Faculty's response card invitations and confirmations. The
envelope has exactly `mail_id` (UUID), `recipient`, `subject` (no line breaks,
at most 300 characters), `text` (at most 20,000), `html` (at most 40,000), and
`reply_to` (an address or empty); bodies above 200,000 bytes are refused. It
sends from the fixed sender with the optional Reply-To and returns
`{mail_id, status: "accepted", message_id}` with 202. Provider rejections are
400 and unconfirmed deliveries 502, with the same fixed log events
(`message_email_accepted|rejected|uncertain`) and no addresses or content in
logs. Faculty queues these messages in `response_card_mail` and sends them from
the Inbox worker; see `response_cards/README.md`.

## Resend outbound delivery

**Activated and verified at 2026-09-15T02:13:41Z.** The live
`faculty-print-export` Worker runs version
`00000000-0000-0000-0000-000000000000`, deployment
`00000000-0000-0000-0000-000000000000`, with `EMAIL_PROVIDER=resend`.
The `RESEND_API_KEY` secret is present and the existing `EXPORT_SECRET` was
retained. The dedicated key is named **Faculty outbound production** and has
`sending_access` permission. After activation, the temporary full-access setup
key was revoked through Resend and its local file was removed.

**Corrected at 2026-09-15T02:43:24Z** (version etag `a66eb28a…`, bundle
SHA-256 `3e656760…`, uploaded through the Cloudflare API with
`keep_bindings: ["secret_text"]`). Every send through version `0d9bcd13`
returned "Delivery unconfirmed": its Resend call passed `redirect: "error"`,
which the Workers runtime rejects with a `TypeError` before any request leaves,
although the runtime docs list the value. The call now uses `redirect:
"manual"` (a redirect comes back as a 3xx and stays unconfirmed), sends
`User-Agent: Faculty-Print-Export/1.0` because Resend's edge bans clients by
browser signature, and an unclassified provider outcome logs
`provider_status` and `provider_reason` (the provider's error identifier only,
never an address or payload) beside the `*_uncertain` event. A signed test
message to the instructor's address was accepted (`202`, Resend message id)
immediately after the upload. The nine TEST 101 invitations attempted through
the broken version remain `unconfirmed` and are not resent; recreate the test
chart to repeat the end-to-end check.

Both `example.com` and `print.example.com` are verified in Resend, including
DKIM and SPF. Sending is enabled, receiving is disabled, and open/click tracking
is off. The Faculty and Inbox processes have
`FACULTY_OUTBOUND_EMAIL_PROVIDER=resend`; health checks passed, and the private
mail API reports provider `resend`, sender
`Professor Sinkule <exam@example.com>`, and Reply-To `instructor@tamu.edu`.

The pre-cutover catalog snapshot `20260915T021311579757Z` was verified. Both
`/message` and `/send` returned 404 for GET, 401 for unsigned POST, and 400 for a
correctly signed invalid `{}` POST, confirming authentication and validation
without invoking delivery. All 15 prior card receipts and both accepted print
records were retained, with no pending mail queue. **No real email was sent
during activation verification.**

Both `/message` (response-card invitations and submission receipts) and `/send`
(instructor-approved PDF exports) use `https://api.resend.com/emails` when
`EMAIL_PROVIDER=resend`. The private `RESEND_API_KEY` belongs in Worker secrets;
the existing `EXPORT_SECRET` continues to authenticate Faculty. An unset provider
retains native Cloudflare delivery, `cloudflare` explicitly selects it, and an
unknown value fails closed. There is no automatic fallback between providers.

For future activation or reconfiguration:

1. Verify `example.com` and `print.example.com` in the instructor's Resend
   account, with sending enabled. Preserve existing incoming-mail DNS and use
   the exact DKIM and return-path records Resend supplies. Keep open/click tracking
   disabled for student card links.
2. Install a dedicated sending-only API key as the Worker's `RESEND_API_KEY`
   secret, through a secret input or a private file/stdin. Never paste it into
   source code, a command argument or a log. The key must allow both sender domains.
3. Complete the Worker tests, type check and dry-run build. Deploy the relay
   with `EMAIL_PROVIDER=resend`, preserving `EXPORT_SECRET` and both senders.
4. Set `FACULTY_OUTBOUND_EMAIL_PROVIDER=resend` in the private workspace email
   environment loaded by both Faculty services, then restart them. This makes
   Resend take precedence over the optional Gmail connection. Startup migrates
   the existing mail queue with `not_before`; records and receipts are retained.
5. Verify the live provider setting, domain readiness and unsigned-request
   rejection without sending. A real test message still requires an explicit
   instructor request or the **Send a test** action.

Resend receives the existing sender, recipient, text, HTML and Reply-To. Print
PDF bytes are verified before their base64 is sent as an attachment. Stable
`faculty-message-<mail_id>` and `faculty-send-<export_id>` idempotency keys protect
repeated requests during Resend's 24-hour key retention; they do not authorize
automatic retries of uncertain deliveries. The existing receipt format retains
Resend's accepted message ID.

Only documented pre-acceptance quota/rate rejections become the exact HTTP 429
response documented in `response_cards/README.md`; the response-card queue then
waits and resumes after its saved deadline. Print exports retain their explicit
retry behavior. Validation/authentication failures are rejected; network errors,
5xx, conflicting/in-flight idempotency keys, and malformed acknowledgements remain
unconfirmed. Logs contain fixed events and local mail/export IDs only.

Provider references: [Send Email](https://resend.com/docs/api-reference/emails/send-email),
[idempotency](https://resend.com/docs/dashboard/emails/idempotency-keys),
[limits](https://resend.com/docs/knowledge-base/account-quotas-and-limits).

## Delivery behavior

The private API freezes batch ID, PDF SHA-256 and one recipient in an approval
hash. Only a ready, non-superseded job can queue; the worker rechecks the retained
PDF before sending. Native API keys and printed references cannot grant access.
The worker uses the existing cross-process document execution lock and releases
catalog/storage locks before network I/O.

Cloudflare accepts a bounded signed JSON envelope and sends one PDF attachment
using its [native email binding](https://developers.cloudflare.com/email-service/api/send-emails/workers-api/).
Base64 is used only in that JSON envelope. The Worker decodes and verifies it,
then supplies binary PDF bytes to the binding's attachment content. The binding
performs MIME encoding; supplying the base64 string as file content would attach
text that a PDF reader cannot open. See the provider's
[binary attachment examples](https://developers.cloudflare.com/email-service/examples/email-sending/email-attachments/).
It returns the export ID, PDF hash and provider message ID. Faculty durably commits
that acknowledgement before recording completion. An interrupted completion
recovers from its receipt; an interrupted network request without a receipt becomes
**Delivery unconfirmed** and is never automatically resent. Explicitly sending
again creates a new export after the instructor checks the mailbox.

The relay accepts at most 4,800,000 request bytes and 3,500,000 PDF bytes. This
allows base64/MIME overhead within Cloudflare's
[5 MiB message limit](https://developers.cloudflare.com/email-service/platform/limits/).
Oversized PDFs stay available through authenticated download; they are never
replaced with a public link.

## Verification

Use the installed compatible Node runtime and this directory's locked dependencies:

```sh
npm run types
npm run check
npm test
npm run build:print-export
```

The build command uses `--dry-run` and does not deploy. Worker tests replace the
email binding, and Python/browser tests replace the mail transport. No verification
command sends a real message. Python tests are in `tests/test_print_email.py`;
the existing `tests/browser_documents.py` covers default/approved/custom choices,
the exact attachment, a lost-response retry, and mobile layouts.

After deployment changes, verify DNS/domain readiness, binding configuration,
and rejection of unsigned requests. A real email send requires the instructor's
explicit Send email action (or a separate request to send a specific test).

### Activation evidence

The complete Python suite passed (162 tests), both Worker suites passed (15
tests), type checking and the outbound dry-run build passed, and the browser
workflow passed with default/approved/custom addresses and mocked delivery.
The browser check includes lost-response idempotency and desktop/mobile layouts.

Live activation preserved the prepared 30-page qualification PDF and its SHA-256.
At initial activation the export API required a browser session. The instructor
removed that requirement on 2026-09-14: exports now work directly through the app
connection, with the same-origin write checks and explicit Send email action.
The public email listener still has no export route (404). All three Faculty services and the nightly
backup timer remain active. The configured app and worker were checked against
the same retained signing secret without displaying it. The export history was
empty at enablement. The instructor's first subsequent export is retained as
accepted, with no reported error.

The live Worker rejected GET with 404 and unsigned POST with 401. A signed empty
object reached export validation and returned 400, confirming the exact-body
signature and endpoint without invoking the email binding. The native client
identifies itself as `Faculty-Print-Export/1.0`; the provider rejected Python's
generic default client identifier. All 16 email tests passed again after this
HTTP-header correction. Enablement evidence is retained at
`/tmp/faculty-print-email-enabled.json`.

### Binary attachment correction

The first instructor export revealed an encoding defect: the original Worker
passed the transport's base64 text into the native binding instead of the decoded
PDF bytes. Provider acceptance did not validate the attachment as a readable PDF.
The correction was deployed at 18:04 UTC on 2026-09-13, preserving the existing
secret, sender restriction, and transport endpoint. Existing email attachments
cannot be repaired; the instructor must explicitly export again to receive the
corrected attachment. No automatic resend was performed.

The strengthened binary regression failed against the old code and passed after
the fix, including non-UTF-8 bytes. Both Worker suites passed (15 tests), along with
type checking and the build. Running the compiled Worker against the actual
30-page PDF with a captured email binding preserved all 1,426,033 bytes and SHA-256
`7b59d0a1edd4a0e63282588d28d51ae42303a74b15b705760e959cfd26bf438b`.
The bounded PDF processor rendered and validated all 30 resulting pages at 200
DPI, including retained tokens, placement and quiet zones. The local capture does
not constitute a mailbox-delivery check; Cloudflare's local email simulator has a
[documented binary attachment limitation](https://developers.cloudflare.com/email-service/local-development/sending/#binary-attachments).

Verified pre-migration catalog snapshot: `20260913T174414394688Z`.
Verified coordinated post-migration snapshot: `20260913T174415887257Z`
(four owners backed up, no errors). Activation metadata is retained locally at
`/tmp/faculty-print-email-activation.json`.
