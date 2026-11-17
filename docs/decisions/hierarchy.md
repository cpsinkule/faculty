## Course data hierarchy

Rule ID: **HIERARCHY**. Current binding module contract. Read root `AGENTS.md` first.

The user's required hierarchy applies to all future teaching features:

1. **Course code**: attributes and resources shared across every term and section
   of that course. Resolve the canonical code to `course_definitions.id`.
2. **Course code + term/year**: resources specific to one teaching term, shared
   only by that term's sections. Include `term_other` when the term is Other.
3. **Course code + term/year + section number**: resources and records specific
   to that section. A section number alone never identifies a section.

Choose and document the owning level before adding a field, API, database table,
or file. Use explicit references to share data from a parent level. Do not copy
course-wide resources into each term or section or silently propagate edits from
a narrower level to its parent. Resource instances at a narrower level may
reference a shared source while retaining their own context and history.

The **Home** tab combines the course-wide area and an overview of the course's
current sections, with a separate selected-term area. Section tabs are scoped to
the selected course code, term/year, and section. Home is not exclusively a
term-level resource owner just because its URL contains a term group ID.
