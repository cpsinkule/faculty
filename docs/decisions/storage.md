## Roster identity and writes

Rule ID: **STORAGE**. Current binding module contract. Read root `AGENTS.md` first.

- `student_identities` is a workspace-wide indexed UIN-to-prefix registry. Keep
  UINs as nine-digit strings, including leading zeros. Prefixes never change.
- `student_enrollments.id` is a random, globally unique ten-digit string owned by
  one UIN and course term. It retains the student's prefix and randomizes the last
  five digits for each different course/term. Reimports and additional sections in
  that term reuse the full ID. Future student records reference this enrollment ID.
- `roster_memberships` links section owners to those enrollments. Exported fields
  are section-specific and stored with the enrollment ID in the term payload.
  Imports upsert; missing students and existing fields are not silently removed.
- All course mutations, roster access, term moves, and coordinated backups acquire
  the roster lifecycle lock before owner locks and catalog transactions. Imports
  commit ID reservations before atomic payload publication. A failed publication
  can leave unused reservations, which retries reuse; never recycle those IDs.
  `RosterService.lock()` is reentrant only within the same thread and service
  instance, retaining the outer filesystem lock through nested service calls.
  Other threads/processes still acquire that lock in both storage modes.
- Identity/section edits cannot detach an imported section from its final visible
  offering. Archiving via the current checkbox preserves its accessible identity.

Current data uses `/mnt/nvme/Teaching`; verified backups use
`/mnt/bulkdata2/Teaching`. The instructor wants all working data, scratch and
caches on the `/mnt` drives: the 200 GB root disk is small and has filled up.
The web and Inbox worker services run with `TMPDIR` under
`/mnt/nvme/Teaching/tmp/<web|worker>/` (systemd drop-ins in `deploy/*.service.d/`),
cleared by each service at its own start. That folder is runtime scratch, not a
storage owner: nothing there is retained, verified or backed up, and the storage
manager never enumerates its root. Use `tempfile` (which honours `TMPDIR`), never
a hard-coded `/tmp`, and do not stage teaching payloads on the root disk. Preserve the nightly backup and verified-final-copy
before-purge rules. For future shared owners, keep the NVMe copy while any
current descendant needs it: another current term or section must not lose a
shared resource when one is archived. All file access must coordinate with the
storage manager's locks and verification, rather than writing to drive folders
directly.

The nightly backup (`CourseStorage.backup_all`) holds the lifecycle lock only for a
consistent capture (2026-09-27: copying every current owner under it took 20 minutes
and froze the app). First, with no lifecycle lock, each current owner either keeps its
recorded snapshot, when that snapshot's manifest `files` and `source_files` both equal
the NVMe inventory and the snapshot reads back from the HDD, or gets a new verified
snapshot that is published but not recorded. Then, under the lifecycle lock and each
owner lock, every working copy is hashed again (`check_source` runs on that inventory)
and a snapshot is recorded only if it holds exactly that copy; otherwise one is copied
there and then. The catalog is copied to NVMe staging inside the same hold and is
integrity-checked and published to the HDD after it. A recorded snapshot that fails
verification is reported and never reused or substituted. Copies are flushed with
one `syncfs`, or with per-file fsync where syncfs is unavailable. Only a copy of a source
that changed while being copied is discarded; other failed staging copies are kept.

Retention (instructor's rule, 2026-09-27; `CourseStorage.retention_plan`/`prune`,
`manage_storage.py prune`). Catalog backups: every one for 14 days, then the newest of
each ISO week until about three months (92 days), and the newest of each month for good.
Payload snapshots: every one that the live catalog or a kept catalog backup records,
and every one younger than 14 days. A catalog backup restores only with the snapshots
it records, so the two are pruned together. Deletion always follows a shown dry run:
`prune` lists the plan and deletes nothing, and only `prune --apply <fingerprint>` of that
exact plan deletes. Prune runs only after a complete nightly backup, never during one,
and never automatically. Catalog backups go first, once the newest one reads back. An
error removing catalog backups stops that prune before any payload snapshot is removed:
an undeleted catalog still needs every snapshot it records. An
owner's snapshots go under its owner lock (no lifecycle lock), once the snapshot the
live catalog records for it reads back; otherwise that owner is skipped and reported.
A kept catalog backup that cannot be read stops the plan. Names that are not Faculty's
own timestamps are never touched. Each run is appended to the HDD's
`retention-log.jsonl`. Do not loosen this without the instructor.

Owners of the verified storage engine (`CourseStorage.owner_managers`, `OWNER_TABLES`; each has its directory on both drives
and is covered by reconcile, the nightly backup, status, retention and `manage_storage.py verify`): saved offerings, terms,
the Inbox, course definitions, sections, `comm_bundles` (directory `communications/`, COMMUNICATIONS) and, since 2026-11-12,
`recording_bundles` (directory `recordings/`, RECORDINGS "STORAGE"; registered by `recordings/storage.py` `RecordingFiles`).
A recording bundle is the files one fetch of one recording kept (transcript, outline, marks and Plaud's notes, copied from the Plaud intake's archive, and, in a bundle kept before 2026-11-15, the audio), written
at once and never changed, and never current: staged on NVMe (`staging/rec-<id>` until published, then `recordings/<id>`),
backed up and verified by the nightly coordinator, then kept on the HDD alone and read from the verified snapshot;
`check_source` checks every `recording_files` checksum and every read checks it again. Audio (about 15 MB an hour) is no longer copied
into a bundle (requirement, 2026-11-15, "Play from archive"): it is the Plaud intake's archive's, in `/mnt/nvme/plaud-intake/`
with its own nightly verified copy in `/mnt/bulkdata2/plaud-intake/`, which are the intake's and not this engine's, and Faculty
never writes them. The audio Faculty already holds stays where it is until the instructor removes it, each file once it matches the
archive's checksum, from a displayed dry run, as this contract requires of any prune.

ERASING A RECORDING THAT IS NOT KEPT (requirement, 2026-11-14: "the ones that don't belong shouldn't be kept"; asked whether
one Faculty had already kept is erased when they say it does not belong: "Erase it"; RECORDINGS "ONLY WHAT IS FACULTY'S IS
KEPT"). This is the ONE removal of kept files outside the retention rule above, and it is not a prune: it removes one
recording's bundles, on the instructor's own Don't keep (or of a recording an earlier version had marked Not Faculty's, by
their choice of 2026-11-14), never by a schedule and never for any other owner. `RecordingStorage.erase` removes the
bundle's NVMe folder and its HDD folder with every snapshot, under the roster lifecycle lock and the bundle's owner lock,
a redirected folder refused before anything is removed. It never runs while the nightly backup does (it asks for the
`nightly-backup` lock without waiting and removes nothing when the backup holds it: `prepare_backup` copies a bundle to the
HDD under no lock of the bundle's, and would write a removed folder back). With one drive away it removes what is on the
other, keeps the bundle named in `recording_erased_bundles` and is tried again each round; the Recordings page says, with
this engine's reason, that files are still to be removed. The bundle's rows have left the live catalog in the transaction before it, so the engine no longer lists it. NO
FINAL COPY is made first and nothing is verified first, by intent: the original stays at Plaud and in the Plaud intake's archive. Catalog backups made before
it still name the bundle and hold its rows until `prune` retires them; a catalog restored from one of those finds its files
gone, which is the instructor's choice and not damage.

Do not build test banks or other future modules merely because they are examples
in these conventions. Implement them when requested. The question bank was requested
(2026-10-01) and is owned by the course definition (QUESTION-BANK).
