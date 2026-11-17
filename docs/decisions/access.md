## Single-user access

Rule ID: **ACCESS**. Current binding module contract. Read root `AGENTS.md` first.

- Faculty has no application login or browser-session requirement. The instructor
  explicitly removed the `faculty_workspace` cookie gate. All app features must
  work directly, including in existing tabs after service restarts. Do not add
  a "private workspace" confirmation or a prerequisite visit to the home page.
- Retain the existing network boundary, same-origin write checks, and public
  email-only listener. Email authentication, command approvals and explicit
  Send email actions are separate from browser access and remain in place.
