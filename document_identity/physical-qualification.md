# Classroom qualification record

Status: **PASSED — 30/30 physical classroom pages resolved on 2026-09-13.**

The instructor printed the 30-page job and scanned it to `scan@example.com`.
The email arrived at 20:43:16 UTC; automatic inspection finished at 20:43:20 UTC
on its first attempt. Every page resolved to its exact issued stamp, printed
copy, page number, assignment/version, course, term and section. The Inbox shows
ten complete three-page copies, with no missing pages, duplicate stamps,
conflicting observations or manual recoveries. The original `scan.receive` job
remains `needs_review` with `instructor_review_required=true`. No assembly was
approved and no teaching handler was dispatched.

The instructor used the printer/scanner defaults and did not check individual
settings. The received PDF confirms 30 Letter-sized pages and grayscale content.
Its producer identifies a KONICA MINOLTA bizhub 650i. This qualifies the physical
route and defaults used in this run; exact printer scaling and scanner-panel DPI
were not independently recorded. It does not establish other hardware/settings.

The separate compact-corner **Test 2** also resolved 30/30 physical pages in the
scan received on 2026-09-14 UTC, with 52/60 individual marks detected. See
[`corner-test.md`](corner-test.md) for its per-corner results and limitations;
the remainder of this record describes the original Test 1.

## Results and retained evidence

| Field | Recorded result |
| --- | --- |
| Date / operator | 2026-09-13 / instructor, workspace owner |
| Printer make/model | Not recorded; optional troubleshooting detail |
| Scanner make/model | PDF producer: `KONICA MINOLTA bizhub 650i`; creator: `math-bloc306b-copier` |
| Paper / scaling / copies / sidedness | Operator used defaults. PDF pages are Letter, 612 × 792 points. Printer scaling, copy setting and sidedness not independently recorded. |
| Scanner settings | Operator used defaults. One grayscale PDF with 1275 × 1650 eight-bit backgrounds and separate one-bit foregrounds. Mixed-resolution encoding does not establish the selected scan DPI. |
| Course section | MATH 140 · Fall 2026 · 507; `3928abd7-1e75-4ea8-a2f5-8a7c4163e710` |
| Assignment / version | Scanner qualification / Test 1 |
| Batch / layout | `8c5817f8-032f-40f1-ab0f-77d7f6b030aa` / `faculty-page-v1`, Data Matrix ECC200 |
| Inbox message | `64906507-e368-4b08-8a76-284e382b2d64` |
| Inbox job | `a1251757-bf34-4412-bf0b-50fe3c6d5d99` |
| Expected / resolved pages | 30 / 30, all distinct issued stamps from the expected batch |
| Complete copies | 10, each containing its own pages 1–3 |
| Missing / conflicts / manual recovery | 0 / 0 / 0 |
| Instructor review required | Yes; no review or approval submitted during verification |
| Verified retained files | Original email plus 61 artifacts: received PDF, 30 extracted PDFs, 30 page rasters |
| Decision | Pass for this classroom run using reported defaults |

The original email contains the exact retained scan attachment and is addressed
to `scan@example.com`. The existing authenticated gateway and short-address
review-only policy were used. Scan identities match the verified print manifest
and authenticated catalog lookups. No new receipt/payload owner was created.

- Print PDF SHA-256: `7b59d0a1edd4a0e63282588d28d51ae42303a74b15b705760e959cfd26bf438b`.
- Received `Smath-bloc326091314390.pdf` SHA-256: `2017ab61db0989ce5abfe9ef06c119f8dd6b71a3c00a7993b8168786950b8cf1`.
- Original email SHA-256: `06b47efd9c5fb74299cbc165f2da44e0a48dbabcec1f02cef74d8efe3d2ac0ab`.

The live browser displayed 30 page cards, 10 assemblies and the instructor-review
notice with no JavaScript errors. Machine-readable mappings, artifact hashes,
observed PDF properties and backup evidence are in
[`physical-qualification-results.json`](physical-qualification-results.json).
Original files remain in managed Inbox storage, not this repository.

The coordinated backup finished at 20:48:05 UTC: five owners, no errors. Catalog
snapshot: `20260913T204805233186Z`. Inbox snapshot:
`20260913T204801848133Z-335ec681`, verified at 20:48:04 UTC. The unclassified
message followed the existing verified-final-copy archival rule and remains
available for review with all originals and extracted files retained.

## Preparation and deployment history

The updated live app, Inbox worker, and email gateway were activated on 2026-09-13.
The backup timer is active, and the post-migration coordinated backup completed
without errors. Pre-migration catalog snapshot: `20260913T160714005026Z`;
post-migration snapshot: `20260913T160715105196Z`.

The audited corrections were activated and health-checked at 16:21 UTC on the
same date. All three services and the backup timer remain active. See
`acceptance-audit.md` for the software checks; they do not satisfy this physical test.

The instructor added MATH 140, Math for Business and Social Science, Fall 2026.
The first section (507) owns the qualification batch. On 2026-09-13 at 17:10 UTC,
the live worker produced a validated Letter PDF with 10 unassigned copies of the
three-page **Scanner qualification / Test 1** fixture. All 30 distinct page tokens
resolved to their exact issued batch/copy/page and section; placement and quiet
zones passed output validation. Physical printing and scanner delivery were still pending at that stage;
the real run below now supplies the physical result.

Download in the private app: **Assignments & printing → Scanner qualification ·
Test 1 → Download print PDF**. Print all 30 pages at actual size/100%, Letter,
single-sided, with printer copies set to 1. Scan all 30 pages through the actual
classroom scanner as a single PDF at 300 DPI, grayscale, single-sided, to the
saved `scan@example.com` entry. No typed subject/body or secret is needed.

Validated print PDF SHA-256:
`7b59d0a1edd4a0e63282588d28d51ae42303a74b15b705760e959cfd26bf438b`.
The authenticated download matched that hash. A coordinated backup including the
new identities and referenced artifacts completed without errors at 17:12 UTC;
catalog snapshot: `20260913T171221614517Z`.
