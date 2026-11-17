# Acceptance audit

Audited against `goal.md` on 2026-09-13. Software is implemented and active in
Faculty. **The original layout passed its physical qualification on 2026-09-13:**
30/30 actual classroom scan pages resolved into ten complete copies. The
instructor used defaults; unconfirmed settings are identified in the physical
record. Subsequent experimental layouts require their own physical test.

Access update (2026-09-14): the instructor removed the browser-session cookie
requirement. Document APIs now work directly through the existing app connection;
the public email-only listener and same-origin write checks remain in place. The
authentication row below records the original qualification configuration.

| Goal criterion | Evidence and result |
| --- | --- |
| 1. Canonical tokens, aliases, collisions and concurrency | `TokenTests` and allocation concurrency/collision tests pass. Tokens use OS randomness and retained database uniqueness. |
| 2. Permanent owners and 24 exact identities | The 24-token fixture resolves every owner and page. Cross-course, roster, page-bound and immutability constraint tests pass. |
| 3. Immutable canonical and physical placement | Canonical hash tests pass. Both paper profiles pass independent output checks for token, position, size and white quiet zones. Deliberately shifted, scaled, repeated and obstructed marks are rejected. |
| 4. Full persistence/render/decode/lookup round trip | Letter and A4, first/middle/last pages, two copies and 200/300 DPI pass. Supported rotated PDF geometry passes. Qualification now checks exact database provenance on every case. |
| 5. Required degradation matrix | 144/144 required cases recover exact tokens and provenance; zero incorrect accepted identities. Severe blur recovers 10/12, JPEG quality 20 recovers 12/12. Pinned versions and summary are in `qualification-results.json`; failed synthetic cases are retained under `test-results/document-qualification-audit/`. |
| 6. Classroom hardware and scan@ delivery | **Passed for the original layout using classroom defaults.** Received at 20:43 UTC on 2026-09-13: 30/30 pages resolved, ten complete copies, zero conflicts, review still required. Original email and 61 artifacts verified; coordinated backup completed without errors. Settings and retained IDs are in `physical-qualification.md`. |
| 7. Separate arrivals and exact-byte deduplication | Distinct delivery IDs retain separate messages and observations; event retries remain one message. Exact page bytes reuse a reviewed revision. Different bytes require review even when rasters are equal. |
| 8. Mixed, partial and multi-message assemblies | Mixed Letter/A4 versions, sections and copies remain separate across shuffled arrivals. Missing pages remain explicit. Foreign-copy selection is rejected; blank backs do not inherit cover identity. |
| 9. Unresolved identities and audited recovery | Classification, cancellation/supersede, unreadable/blank and exact-alias recovery tests pass. Original Inbox artifacts and correction events remain retained. |
| 10. Crash/retry, publication and issuance | Interrupted render/decode, partial publication, same-token retry, fresh-token reprint, concurrent intake and post-ready cancellation tests pass. |
| 11. Authentication and review gate | Anonymous document access returns 403; public email listener returns 404 for private document routes. Private-session requests work. Decoding never clears instructor review or dispatches a handler. |

Verification: the full 144-test suite plus two added mixed-section/version tests
passed (146 total); the browser print/download/scan/review workflow passed at
desktop and mobile widths. The original 110 regression tests remain included.
Storage tests cover current-descendant retention, nightly backup, final-copy
verification, archived writes and corruption handling. The compiler tests cover
reproducibility, isolation, intentional blank pages and a 100-file source upload
with retained class/build records.

The app, Inbox worker and email gateway were refreshed after the audit at
16:21 UTC. Health and private/public access checks passed; the nightly backup
timer remains active. The verified pre/post-migration catalog snapshot IDs are
recorded in `physical-qualification.md`. No schema or payload migration was
needed for the audit corrections; the live document tables were still empty.

The subsequent print-email export extension passed the expanded 162-test Python
suite, 15 Worker tests and the desktop/mobile browser workflow, including a lost
API response without duplicate sending. The private form and additive migration
were activated at 17:44 UTC with verified backups. The instructor approved the
sending subdomain and Worker, and outbound sending was enabled at 17:53 UTC.
Live signed non-sending probes and configuration checks passed; no real email
was sent. See `cloudflare/teach-inbox/PRINT_EXPORT.md` for deployment evidence.

The first instructor-requested email exposed a binary-attachment encoding defect
outside the original print/download path. The Worker was corrected and deployed
at 18:04 UTC. Its strengthened regression fails on the previous implementation;
the corrected binding receives the exact original PDF bytes. All 30 pages of the
captured attachment passed bounded rendering and token/layout validation. The
previous email remains unusable and requires an explicit new export; no automatic
resend was made.

The instructor's physical Test 1 arrived at 20:43 UTC and passed 30/30 exact page
identity checks with ten complete assemblies and instructor review still required.
The original email and all 61 scan artifacts passed checksum checks, followed by
a successful coordinated backup. Defaults were used; confirmed PDF properties
and unrecorded device settings are distinguished in `physical-qualification.md`.

The requested Test 2 adds a separate 10 mm top-right/bottom-left repeated-mark
profile. The expanded Python suite passes 165 tests, the original layout passes
144 required degradation cases, and the corner layout passes 168 required cases.
The browser upload/print/scan workflow and both mark counts pass. Test 2's actual
classroom scan arrived on 2026-09-14 at 00:10 UTC and resolved all 30 pages into ten
complete copies. Bottom-left decoded on 30/30, top-right on 22/30, for 52/60 marks.
This is a physical page-identification pass for the received run; either-corner-alone
recovery on every page is not established. The operator covering procedure and
settings remain unconfirmed. All 61 artifacts and the raw email were verified,
review remained required, and the coordinated backup passed. Evidence and limits
are in `corner-test.md` and `corner-physical-qualification-results.json`.
