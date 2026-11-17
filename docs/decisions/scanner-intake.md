## Cloudflare scanner intake

Rule ID: **SCANNER-INTAKE**. Current binding module contract. Read root `AGENTS.md` first.

- Follow `cloudflare/teach-inbox/README.md` for the deployed email gateway and
  `teach_inbox/CLOUDFLARE_EMAIL_PLAN.md` for the wider design. Scanner intake and
  Data Matrix document registration/inspection are implemented; classroom physical
  qualification passed for `faculty-page-v1` on 2026-09-13 (30/30 pages, classroom
  defaults). The separate `faculty-corners-10mm-v1` classroom run received on
  2026-09-14 UTC resolved 30/30 pages (bottom-left 30/30, top-right 22/30). This
  qualifies page identification for that run; either corner alone on every page
  is not established. See `document_identity/corner-test.md` for limitations.
  Since 2026-10-01 the corner layout is the class default for new paper
  (DOCUMENTS "TWO CORNER CODES BY DEFAULT"), on that qualification.
  Changed layouts require separate qualification. QR support is deferred unless needed.
- The department scanner can save an address and scan QR-marked documents or
  cover sheets. It sends attachments without a user-written message. Never make
  scanner workflows require a typed secret, subject, YAML, or course identifier.
- Gateway authentication, scanner submission permission, and document identity
  are distinct. A saved opaque alias grants bounded intake access; neither sender
  address nor a copied printed reference authorizes privileged actions.
- The short scanner address `scan@example.com` uses `review_only: true`.
  Workspace-owned `inbox_messages.instructor_review_required` must be honored by
  all workers and future classifiers. Only the instructor's review in the private
  workspace, or the worker's automatic review of an authenticated scan with nothing
  open about the whole scan (documents.md; requirement, 2026-09-27, confirmed
  2026-10-01), clears it; matching a sender address is not proof of identity.
- Printed document-instance references are separate from single-use `ti:` upload
  codes and the private ten-digit student enrollment IDs. Resolve permanent course,
  term, section, and enrollment owners on the server. Missing/conflicting references
  require review; AI cannot select new executable actions.
- A PAGE IN NO PAPER IS SET ASIDE, NEVER DROPPED (requirement, 2026-10-02: "for a stray unlabeled sheet (a TA's section header,
  a scratch page). Today it holds the scan forever and blocks certification. The page stays kept and listed, with your reason;
  undo is possible"). An unidentified page the instructor sets aside, with a reason, stays retained and listed but no longer
  flags its scan. Only the instructor's explicit **Not part of any paper…** (on Papers, since 2026-10-18, DOCUMENTS "STRAY SHEET") does it,
  save one case Faculty settles itself (requirement, 2026-11-14): an empty back inside one copy's paper on paper labeled on every
  page, or the back of a paper's last sheet before the next paper (A PAPER'S LAST BACK), set aside with actor `automatic`, a reason naming that paper, the same review state, event and Undo, and never again once
  the instructor takes it back (DOCUMENTS "A PAGE BETWEEN ITS OWN LABELS"). The instructor's own: POST
  `/api/documents/scans/<scan page>/set-aside` (`ScanService.set_aside`: `reason`, `undo`, `request_id`), for a written page with
  no label read and no identity, in no grouped paper, no reviewed paper and no bubble sheet, on a scan still in the Inbox. Under
  the lifecycle lock, then the scan's owner lock, its review state becomes `ignored` (the state blank pages carry); nothing is
  deleted or moved, and nothing new is owned: the page and its events stay the source Inbox message's. Each change is an Inbox event naming the actor (`request_actor`) and the reason (`page_set_aside`,
  `page_set_aside_undone`); undo restores the state the page had, and the same request ID changes nothing twice. The scan lists
  such pages with their reasons (`set_aside` on its page review, on Papers and in `recognition`, only when there are
  any) and no longer counts them among its pages without a label. A label read later (alias recovery, a registration) still
  identifies the page.
- A SCAN EMAIL'S PDF MAY BE AS LARGE AS THE EMAIL (requirement, 2026-11-14: two B&W copier scans of 284 and 278 pages, 11.1
  and 10.2 MiB, were refused by the 10 MB attachment limit and listed as "Sender authentication failed"; "the cap should be
  25MB to match cloudflare limit"). On a scanner intake address each attachment may be up to 25 MiB
  (`protocol.MAX_INTAKE_FILE`, the gateway's own `MAX_RAW`); the total stays 25 MiB, and authored command email keeps 10 MB
  per attachment. Cloudflare's 25 MiB limit on the whole encoded email is the real bound (about 18 MiB of PDF). An email
  Faculty cannot read (too large, malformed, no single sender) is still kept with its original, stays unauthenticated and
  cannot be reviewed or approved, and its job says what refused it ("… This email could not be read, so this submission
  cannot be approved or executed."); "Sender authentication failed" names only a sender check that ran and failed.
- Keep Cloudflare responsible for raw-message transport and durable retries, and
  Faculty responsible for teaching policy and processing. `scan.receive` has no
  handler: empty-body intake waits for review (the instructor's, or the automatic one above). Implement future processors
  only when requested. Preserve R2 originals until the exact-body durable receipt
  is acknowledged; recovery must survive queue expiry and enqueue failures.
- The dedicated public listener is `email_gateway.py` on loopback port 3001. Never
  point the public tunnel at the full private app on port 3000. Credentials and
  opaque address-book aliases stay outside version control under `.config/faculty`.
- SCANNER FOLDER (requirement, 2026-10-18: "they both offer scan to network file. I want to set that up so that I can scan
  directly into the inbox of faculty"). The home network scanners (Epson, Brother) save PDFs into `scanner-folder/` on NVMe
  (`<nvme_root>/scanner-folder`, `FACULTY_SCANNER_FOLDER` overrides; workspace-owned spool, not a drive folder), one
  subfolder per scanner whose name is the arrival's sender (`scanner-folder/<name>`). The Inbox worker
  (`teach_inbox/scan_folder.py`, one file a turn) takes a file only once it is unchanged for 10 s across two looks and ends
  with a PDF trailer, and retains it exactly as `manage_inbox.py scan` does: `scan.receive`, `instructor_review_required`,
  content-keyed idempotency, so it then follows the same automatic review and pipeline as any scan. The folder's copy is
  removed only after the Inbox has retained it (or already had the same bytes). A file it refuses (not a PDF, too large,
  damaged, or no trailer after 30 minutes) moves to `not-accepted/` beside a `.txt` note with the reason; nothing is
  deleted unseen. Dot and `~` files and symlinks are ignored. The folder is reached over the home LAN only, through an SMB
  share with its own scanner-only login (`deploy/scanner-folder/`); it is never exposed on the tunnel or Tailscale Funnel.
