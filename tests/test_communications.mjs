// A student's communications record, the timeline's pure helpers (static/communications.js): which filter an entry is under, search,
// each channel's status line and tone, who wrote an entry, and the only links an entry may open.
import assert from "node:assert/strict";
import { channelGroup, matches, channelLine, channelTone, authorText, mailHref, outsideHref, massText, massRuns, firstName, linkedNote, linksDrive } from "../static/communications.js";

const email = { channel: "gmail", channel_label: "Email", author_role: "student", author: { name: "Example, Alice", email: "alice@tamu.edu" },
  recipients: [{ name: "", email: "prof@tamu.edu", kind: "to" }], subject: "Exam 1 question", text: "Can I take the exam later?",
  context: { thread_id: "c1000000" } };
const comment = { channel: "canvas_comment", channel_label: "Canvas comment", author_role: "instructor", author: { name: "Pat" }, recipients: [],
  subject: "Comment on Homework 3", text: "Fixed.", context: { url: "https://canvas.tamu.edu/courses/1/assignments/2/submissions/3" } };
const card = { channel: "response_card", channel_label: "Response Card", author_role: "student", author: { name: "Example, Alice" }, recipients: [],
  subject: "Exam 1 · your seat", text: "Confirm your seat\nA5", context: { source: "Response Card answer" } };

assert.deepEqual([email, comment, card, { channel: "gradescope_regrade" }, { channel: "faculty_mail" }, { channel: "gmail", context: { canvas_notice: true } }].map(channelGroup),
  ["email", "canvas", "faculty", "gradescope", "faculty", "canvas"]);
assert.ok(matches(email, "all", ""));
assert.ok(matches(email, "email", "exam later"));
assert.ok(matches(email, "all", "LATER alice"));          // any order, any case, names too
assert.ok(!matches(email, "canvas", ""));
assert.ok(!matches(email, "all", "exam homework"));
assert.ok(matches(card, "faculty", "seat a5"));
assert.ok(matches(card, "all", "response card answer")); // what the entry is also counts

const now = Date.parse("2026-09-30T17:00:00Z");
assert.equal(channelLine({ ready: false, setup: "Connect Gmail for reading on the Mail page." }, now), "Connect Gmail for reading on the Mail page.");
assert.equal(channelLine({ ready: true, requested: true }, now), "Capturing now…");
assert.equal(channelLine({ ready: true, status: "waiting", detail: "Sign in to Canvas from a section page." }, now), "Sign in to Canvas from a section page.");
assert.equal(channelLine({ ready: true, status: "ok", last_success_at: null }, now), "Not captured yet");
assert.equal(channelLine({ ready: true, status: "ok", kept: 12, last_success_at: "2026-09-30T16:55:00Z" }, now), "12 kept · captured 5 min ago");
assert.deepEqual([{ ready: false }, { ready: true, status: "failed" }, { ready: true, status: "ok", last_success_at: "x" }, { ready: true, status: "ok" }].map(channelTone),
  ["warning", "danger", "success", "neutral"]);

assert.equal(authorText(email, "Student"), "Example, Alice");            // another student's message: the name it carries
assert.equal(authorText({ ...email, author: { name: "Ally E." }, roles: ["author"] }, "Example, Alice"), "Example, Alice");  // their own: the roster's
assert.equal(authorText(comment, "Student"), "You");
assert.equal(authorText({ channel: "faculty_mail", author_role: "instructor" }, "Student"), "Faculty, for you");
assert.equal(authorText({ channel: "gradescope_regrade", author_role: "staff", author: { name: "Terry TA" } }, "Student"), "Terry TA");

// Links: Mail's own route for a hex Gmail thread, https only for Canvas and Gradescope; anything else is no link.
assert.equal(mailHref(email), "#mail?thread=c1000000");
assert.equal(mailHref({ context: { thread_id: "c1\"><script>" } }), "");
assert.equal(mailHref({ context: { gmail: { thread_id: "abc" } } }), "#mail?thread=abc");
assert.equal(outsideHref(comment), comment.context.url);
assert.equal(outsideHref({ context: { url: "javascript:alert(1)" } }), "");
assert.equal(outsideHref({ context: { url: "http://canvas.tamu.edu/x" } }), "");
assert.equal(outsideHref({ context: {} }), "");
assert.equal(massText({ mass: { students: 85 } }), "85 students");
assert.equal(massText({ mass: { students: 1 } }), "1 student");
// Consecutive class-wide messages fold into one run (two or more); one alone stays an entry; order is kept (newest first).
const own = (key) => ({ key, mass: null }), wide = (key) => ({ key, mass: { students: 339 } });
assert.deepEqual(massRuns([]), []);
assert.deepEqual(massRuns([own("a"), wide("b"), own("c")]), [{ entry: own("a") }, { entry: wide("b") }, { entry: own("c") }]);
assert.deepEqual(massRuns([wide("a"), wide("b"), own("c"), wide("d"), wide("e"), wide("f")]),
  [{ run: [wide("a"), wide("b")] }, { entry: own("c") }, { run: [wide("d"), wide("e"), wide("f")] }]);
assert.deepEqual(massRuns([own("a"), wide("b"), wide("c")]), [{ entry: own("a") }, { run: [wide("b"), wide("c")] }]);
assert.deepEqual(massRuns([wide("a")]), [{ entry: wide("a") }]);
// "Write to Alice…": the given name of "Family, Given", else the first word.
assert.equal(firstName("Example, Alice"), "Alice");
assert.equal(firstName("Example, Mary Ann"), "Mary");
assert.equal(firstName("Alice Example"), "Alice");
assert.equal(firstName(" "), "");
assert.equal(firstName("Example,"), "Example");
console.log("communications helpers ok");

// LINKED DRIVE FILES: a file the student linked from Google Drive says where it came from, or why it was not fetched; an
// attachment the message carried says nothing more.
assert.equal(linkedNote({ name: "quiz.pdf", kept: true }), "");
assert.equal(linkedNote({ name: "note.pdf", kept: false, reason: "Canvas gave no address" }), "");
assert.equal(linkedNote({ name: "2nd Dr Note.pdf", kept: true, linked: "google_drive" }), "from Google Drive");
assert.equal(linkedNote({ name: "2nd Dr Note.pdf", kept: false, linked: "google_drive", wait: "permission", reason: "Faculty may not read your Google Drive yet" }),
  "not fetched from Google Drive: Faculty may not read your Google Drive yet");
assert.equal(linkedNote({ name: "Google Drive file", kept: false, linked: "google_drive" }), "not fetched from Google Drive");
assert.equal(linksDrive("see\n<https://urldefense.com/v3/__https://drive.google.com/file/d/1mIt3EgEPVD_DVM5TgXxKM9zLR__LNRB9__;!!tok$ >"), true);
assert.equal(linksDrive("my letter https://docs.google.com/document/d/1DocOfAliceLetterToHerProfessor_abc/edit"), true);
assert.equal(linksDrive("I use Google Drive a lot; see https://www.google.com/"), false);
assert.equal(linksDrive(null), false);
