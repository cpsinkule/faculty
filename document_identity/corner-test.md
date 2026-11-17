# Test 2: smaller Data Matrix marks in opposite corners

Status: **PASSED for page identification in the received classroom run — 30/30 pages, 52/60 individual marks detected.**

The instructor requested half-size Data Matrix marks at top-right and bottom-left
after the original 30-page classroom run passed. This test halves each mark's
width and height from 20 mm to 10 mm, including its quiet zone. The two marks on
each page repeat one page token. The new profile is `faculty-corners-10mm-v1`;
`faculty-page-v1` and its issued Test 1 batch remain unchanged.

The test uses a new course-wide version under **Scanner qualification**, owned
by MATH 140, and a new section-owned print batch for Fall 2026 section 507. The
batch contains ten unassigned three-page copies: 30 distinct page identities,
60 physical marks. Each copy receives fresh tokens.

The live worker issued the validated print PDF at 21:07:44 UTC on 2026-09-13.

| Field | Test 2 |
| --- | --- |
| Version label | Test 2 - half-size corner codes |
| Version ID | `d9ae4a83-0f39-44f3-b7d7-1fe40c975c98` |
| Batch ID | `dbc805c7-8bb4-4513-bd94-5e2fa8202b8c` |
| Section ID | `3928abd7-1e75-4ea8-a2f5-8a7c4163e710` |
| Output | 30 Letter pages, 60 Data Matrix marks, 2,298,366 bytes |
| PDF SHA-256 | `f0c21e2f53bcee8f958e553ee4804cca76dd4c979d1161693654f84b2fdefff5` |
| Validation | Every page: two exact expected marks, correct placement and size, clear quiet zones at 200 DPI |
| Physical result | Received 2026-09-14 00:10 UTC: 30/30 exact pages; bottom-left 30/30, top-right 22/30 |

The live UI exposes this job through the usual download, email and drive export
controls. **Save PDF as…** is available. No export email was sent automatically.

## Physical result — 2026-09-14 UTC

The instructor reported completing Test 2. The scanner email arrived at
00:10:18 UTC; inspection completed at 00:10:22 UTC on its first attempt. All
30 distinct page identities match the exact issued Test 2 manifest and retained
course, term, section, version, copy and page owners. Ten three-page copies are
complete, with no missing pages, conflicts, duplicate stamps or manual recovery.

| Measurement | Result |
| --- | --- |
| Correct page identities | 30/30 |
| Complete copies | 10/10 |
| Top-right marks decoded | 22/30 |
| Bottom-left marks decoded | 30/30 |
| Both marks decoded | 22 pages |
| Only bottom-left decoded | 8 pages: source sheet positions 4, 5, 6, 17, 18, 22, 23, 27 |
| Total decoded physical marks | 52/60 |
| Inbox job | `18c8cfe7-cd77-44ee-8018-5c3e0e93d589` |
| Inbox message | `33bd2e30-51f6-446d-9eae-c890186482e6` |
| Input attachment | `Smath-bloc326091318060.pdf`, 248,716 bytes |
| Review gate | Still `needs_review`, `instructor_review_required=true` |

This is a **page-identification pass for this physical run**. The repeated mark
preserved identification on eight pages where the top-right mark was not decoded.
It does not establish that either corner alone succeeds on every page: top-right
alone was not sufficient for eight pages in the production inspection. The
reason those marks were not decoded is not established. On visually inspected
source sheet 4, both marks are present with no obvious cover, while only the
bottom-left mark was decoded.

The instructor's covering procedure and individual device settings have not yet
been confirmed. Do not assume Test 1's reported defaults apply to Test 2, or treat
the recommended covering procedure as having been performed. The received PDF
confirms 30 Letter pages (612 × 792 points), a `KONICA MINOLTA bizhub 650i` producer
and `math-bloc306b-copier` creator, and grayscale/mixed-resolution image encoding.
Those PDF properties do not establish scanner-panel DPI or printer scaling.

The authenticated, review-only email route retained the original email and all
61 artifacts: the input PDF, 30 extracted PDFs and 30 rasters. Every artifact
passed byte-length and SHA-256 verification through the existing storage API;
the original email contains the exact scan attachment and addresses
`scan@example.com`. No review, approval, correction or reprocessing was submitted.
The live browser showed 30 page cards, ten assemblies, the exact mark counts,
collapsed file groups, and the review-required notice with no JavaScript errors.

- Scan PDF SHA-256: `7fbb4b5bfdafaad80d7b5e4c219d54d1d2c6afcfe2a5d9d7e6166e1ff8abad5c`.
- Original email SHA-256: `4cffd5903a4009ae067ff4ce2ece19515d0e12fb89267d215a7225575b1eeed1`.
- Coordinated backup completed at 2026-09-14 00:13:00 UTC: five owners, no errors;
  catalog snapshot `20260914T001300656696Z`.
- Machine-readable mappings, detected locations, hashes, observed PDF properties
  and backup evidence: [`corner-physical-qualification-results.json`](corner-physical-qualification-results.json).

Original files stay in their existing managed Inbox/section owners. This report
records the Test 2 result separately from the original layout's physical pass.

## Follow-up decoder diagnosis — 2026-09-14 UTC

The instructor observed that the scan looks clear. Read-only, isolated diagnostic
passes used the exact same retained scan PDF and verified every recovered payload
against its issued page stamp. Production observations and review state were not
changed.

- The production reader's 200-DPI raster and default local threshold reproduced
  52/60 marks (30/30 page identities).
- Rendering the same PDF at 300 DPI with the default reader recovered 54/60 marks.
- A fixed black/white threshold at 200 DPI recovered 55/60 marks. Combining all
  tested 200/300-DPI and local/global/fixed threshold observations recovered
  56/60 distinct expected marks. No unexpected candidate was returned.
- Top-right marks on source sheets 4, 5, 17 and 22 remained undecoded in these
  diagnostic passes. Their exact failure cause is not established.

Thus four of the original eight missed marks can be recovered by changing reader
settings without a new scan. The diagnostic union is not the production result
and does not establish full independent corner redundancy. See
[`corner-decoding-diagnostics.json`](corner-decoding-diagnostics.json).

A subsequent search-box check found all four remaining top-right symbols fully
inside the original corner region, with at least 89 pixels of clearance at
200 DPI. The production reader also searches the full page. Expanded crops and
tightly centered crops with four padding sizes were compared at 200/300 DPI,
using three threshold methods and page 3 as a positive control. Changing the
crop together with global thresholding recovered **page 22 at 200 DPI**, bringing
the diagnostic union to **57/60 marks**. Pages **4, 5 and 17** remained undecoded
at top-right. This supports crop/threshold sensitivity for page 22, not clipping
or a misaligned box; it does not establish the other three failure causes.
See [`corner-search-box-diagnostics.json`](corner-search-box-diagnostics.json).
The live observation count remains the original 52/60; no reprocessing, review
or approval was performed.

Per-page PDFs retain the original page content for downloads and assembly; PNGs
are deterministic grayscale raster inputs to the reader, currently at 200 DPI.
They are derived representations of one scan, not additional physical scans.

## Software evidence

- Full Python regression: 165 tests passed, including profile persistence,
  unchanged original batches, both-mark placement/quiet-zone checks, independent
  recovery from either corner, and retained Inbox review/backup behavior.
- Required degradation matrix: 168/168 exact token and database recoveries;
  zero incorrectly accepted identities. This includes 150/200/300 DPI, rotations,
  skew, light blur, JPEG compression, reduced contrast, and either corner alone
  at 200 DPI.
- Characterization: severe blur at 150 DPI recovered 0/12; JPEG quality 20 recovered
  12/12; top-right alone at 150 DPI recovered 12/12; bottom-left alone recovered
  10/12. Failed synthetic fixtures are retained in
  `test-results/document-corners-qualification/`.
- Machine-readable software results: `corners-qualification-results.json`.
- The full browser workflow passed: uploaded corner-layout source compiled into
  a new version, produced a private print job, and arrived in the test Inbox as
  six pages/two copies with two matching mark detections per page and review
  still required. The live job/export controls were also checked without sending.
- The original layout still passes all 144 required software cases, and its
  already-issued live Test 1 PDF retains the original hash.

## Repeat-test procedure

1. Open **Scanner qualification · Test 2 - half-size corner codes** and export the
   new 30-page print job. **Save PDF as…** can save it to the laptop's `ScanPrint`
   USB folder. Check the first sheet shows two smaller marks in opposite corners.
2. Print all 30 pages, printer copies set to **1**, preferably actual size/100%
   and single-sided. Keep track of the settings used; printer model is optional.
3. For a baseline test, scan all 30 sheets with both marks visible, using the same
   classroom scanner defaults as Test 1, to `scan@example.com`.
4. To test physical redundancy in that same run instead, leave the first ten
   sheets unchanged, cover only the top-right barcode on the next ten, and cover
   only the bottom-left barcode on the final ten. Keep the uncovered mark clear.
   These counts refer to sheet order, not the repeating printed page numbers 1–3.
5. Report which procedure was used. Verify all 30 pages match this new batch and
   form ten complete copies. Record matching-mark counts and any unreadable pages;
   review must remain required. Do not infer a physical pass from the software
   matrix or the previous larger-mark run.

The original print/scanned PDFs, page observations and reviewed revisions remain
in their existing managed owners. Never substitute the old print job for Test 2.

The coordinated backup finished at 2026-09-13T21:09:18.219056+00:00 with no errors.
Catalog snapshot: `20260913T210917887577Z`. Live job metadata and backup
evidence are in `corner-test-job.json`.
