## TA contacts

Rule ID: **TA-CONTACTS**. Current binding module contract. Read root `AGENTS.md` first.

- The instructor asked (2026-09-28): "For each section in the course page, I want to be able to add the contact information for
  my TA's so I can easily email them when needed". `teaching_assistants/` (service, `/api/teaching-assistants/sections/<id>`,
  migration namespace `teaching_assistants`) keeps each TA's name, email, phone and note. OWNER: the SECTION
  (`course_sections.id`): `section_ta_contacts` holds immutable revisions of a section's whole list (append-only, the highest
  revision counts, earlier ones retained). A save names `base_revision` (409 when stale) and a request ID (the same ID and list
  returns what it did; another list is a 409); an identical list records nothing; blank rows are dropped. Emails are checked to
  a plain `name@domain` form so they go into a `mailto:` link as they are, and are unique per section.
- The section tab's work list has a TAs row: each name opens an email to that TA in the instructor's own mail program
  (`mailto:`, subject "<course code> Section <n>"), plus Email all, Copy emails and Edit TAs…. FACULTY SENDS NOTHING here.
  A TA helping several sections is listed on each: the dialog's Copy from Section N only fills the form, and Save records this
  section's own revision; nothing propagates between sections or terms. A section's list stays with its permanent ID through
  offering edits (like its Canvas link) and never blocks one. Catalog data: in the nightly catalog backup, not in the generic
  workspace export.
