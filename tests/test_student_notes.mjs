// Student notes (static/student-notes.js): a note's day is shown on the campus wall clock, with its year only when it is not this year.
import assert from "node:assert/strict";
import { noteDate } from "../static/student-notes.js";

assert.equal(noteDate("2026-10-01", "2026-10-01"), "Thu, Oct 1");
assert.equal(noteDate("2026-09-28", "2026-10-01"), "Mon, Sep 28");
assert.equal(noteDate("2025-12-31", "2026-01-02"), "Wed, Dec 31, 2025");
console.log("student notes: dates ok");
