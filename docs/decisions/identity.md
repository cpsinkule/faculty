## Existing identity and storage boundaries

Rule ID: **IDENTITY**. Current binding module contract. Read root `AGENTS.md` first.

- `course_definitions.id` is the retained course-wide identity, resolved by unique
  canonical `code`. Prefix case/spacing is normalized; leading zeros are retained.
- `course_groups.id` is a deterministic navigation projection for one code and
  term/year. `course_terms.id` is the permanent term owner, unique on definition,
  term, custom term identifier, and year. `course_sections.id` is the permanent
  section owner, unique on term ID plus section number. API section routes resolve
  navigation groups to these retained owners.
- `courses.id` identifies a saved offering entry, which can cover several
  sections. Entries can overlap. It is not the canonical course, term, or section
  identity and must not become the owner of new shared resources by default.
- `CourseStorage.course_files` manages saved-entry payloads. `TermStorage` reuses
  the verified lifecycle in separate `terms/<term-id>/` directories. The term's
  `data/roster.json` owns section records keyed by permanent section ID. Course-wide
  document payloads use `DefinitionStorage` under `definitions/<definition-id>/`;
  section print artifacts use `SectionStorage` under `sections/<section-id>/`.
  Both reuse the verified, NVMe-staged, nightly-only archival lifecycle. Inbox
  messages can also own course-wide artifacts by referencing the definition.
- Identity edits (code, term/year, or section) must preserve existing data and
  explicitly handle references. Do not silently orphan or reassign resources when
  a derived navigation ID changes.
