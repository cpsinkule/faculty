// The assistant drawer's pure helpers (static/assistant.js): a model's Markdown becomes safe HTML, request bodies and previews are
// readable lists, and an Approve card names what it sends.
import assert from "node:assert/strict";
import { esc, markdown, valueMarkup, cardMarkup, filesMarkup, chatMarkup, isStep } from "../static/assistant.js";

assert.equal(esc(`<a href="x">'&`), "&lt;a href=&quot;x&quot;&gt;&#39;&amp;");
const html = markdown("# Title\n\nHello **world** and `code` <script>alert(1)</script>\n\n- one\n- [Gradebook](#gradebook)\n\n1. first\n2. [evil](javascript:alert(1))\n\n| A | B |\n|---|---|\n| 1 | <b>2</b> |\n\n```\n<raw>\n```");
assert.match(html, /<h4 class="assistant-heading">Title<\/h4>/);
assert.match(html, /<strong>world<\/strong>/);
assert.match(html, /<code>code<\/code>/);
assert.doesNotMatch(html, /<script>/);
assert.match(html, /&lt;script&gt;/);
assert.match(html, /<ul><li>one<\/li><li><a class="assistant-route" href="#gradebook">Gradebook<\/a><\/li><\/ul>/);
assert.doesNotMatch(html, /javascript:/);
assert.match(html, /<ol><li>first<\/li><li>evil\)?<\/li><\/ol>/);
// A column of numbers is marked for right-aligned tabular figures; a column with words is not.
assert.match(html, /<th data-num>A<\/th><th>B<\/th>/);
assert.match(html, /<td data-num>1<\/td><td>&lt;b&gt;2&lt;\/b&gt;<\/td>/);
assert.match(html, /<pre><code>&lt;raw&gt;<\/code><\/pre>/);
assert.doesNotMatch(markdown("x style=\"color:red\""), /style="/);
assert.match(markdown("[docs](https://example.com/a)"), /target="_blank" rel="noopener noreferrer"/);

// The message text (static/assistant-markdown.js): nested lists, block quotes, rules, upright emphasis and math kept as its source.
const { tablesTSV, messageTime } = await import("../static/assistant-markdown.js");
assert.equal(markdown("- a\n  - b\n  - c\n- d"), "<ul><li>a<ul><li>b</li><li>c</li></ul></li><li>d</li></ul>");
assert.equal(markdown("1. one\n   - inside\n2. two"), "<ol><li>one<ul><li>inside</li></ul></li><li>two</li></ol>");
assert.equal(markdown("- a\n1. b"), "<ul><li>a</li></ul><ol><li>b</li></ol>");
assert.equal(markdown("> Quoted **text**\n> second line\n\nAfter"), '<blockquote class="assistant-quote"><p>Quoted <strong>text</strong><br>second line</p></blockquote><p>After</p>');
assert.equal(markdown("Above\n\n---\n\nBelow"), '<p>Above</p><hr class="assistant-rule"><p>Below</p>');
assert.equal(markdown("An *important* point, 2 * 3 * 4 and 2*3*4."), "<p>An <em>important</em> point, 2 * 3 * 4 and 2*3*4.</p>");
assert.equal(markdown("Solve $x^2 < 4$ and \\(y\\)."), '<p>Solve <span class="assistant-math" data-math>$x^2 &lt; 4$</span> and <span class="assistant-math" data-math>\\(y\\)</span>.</p>');
// Money is not math: no space inside a pair, no digit after it (pandoc's rule).
assert.equal(markdown("It costs $5 to $10, or $5/$10."), "<p>It costs $5 to $10, or $5/$10.</p>");
assert.equal(markdown("`$x$` stays code"), "<p><code>$x$</code> stays code</p>");
assert.equal(markdown("Then\n$$\n\\frac{a}{b}\n$$\nso"), '<p>Then</p><div class="assistant-math" data-math data-math-display>$$\n\\frac{a}{b}\n$$</div><p>so</p>');
assert.equal(markdown("\\[ a <b \\]"), '<div class="assistant-math" data-math data-math-display>\\[ a &lt;b \\]</div>');
assert.equal(markdown("$$ never closed"), "<p>$$ never closed</p>");
const scores = markdown("| Student | Score | Of |\n|---|---|---|\n| Ada | 7.5 | 10 |\n| Ben | — | 10 |\n| Cy | 85% | 10 |");
assert.match(scores, /<th>Student<\/th><th data-num>Score<\/th><th data-num>Of<\/th>/);
assert.match(scores, /<td>Ben<\/td><td data-num>—<\/td><td data-num>10<\/td>/);
assert.doesNotMatch(markdown("| A | B |\n|---|---|\n| — | x |"), /data-num/);
// Copy table: every pipe table of the answer as tab-separated text, its inline marks dropped; none inside a code block.
assert.equal(tablesTSV("Scores:\n\n| Student | Score |\n|---|---|\n| **Ada** | 7.5 |\n| [Ben](#students) | `8` |\n\nand\n\n| A |\n|---|\n| 1 |"), "Student\tScore\nAda\t7.5\nBen\t8\n\nA\n1");
assert.equal(tablesTSV("No table here.\n```\n| A |\n|---|\n| 1 |\n```"), "");
// The time on a message is its moment on the reader's own clock, with the day when that is not today.
const noon = new Date("2026-10-09T17:00:00Z");
assert.equal(messageTime("2026-10-09T14:05:00-05:00", noon, "America/Chicago"), "2:05 PM");
assert.equal(messageTime("2026-10-08T00:07:00-05:00", noon, "America/Chicago"), "Oct 8, 12:07 AM");
assert.equal(messageTime("2026-10-09T12:00:00-05:00", noon, "America/Chicago"), "12:00 PM");
// The same moment read in another zone falls on another day there.
assert.equal(messageTime("2026-10-09T23:30:00-05:00", noon, "UTC"), "Oct 10, 4:30 AM");
assert.equal(messageTime("", noon), "");
assert.equal(messageTime("not a time", noon), "");

assert.match(valueMarkup({ to: ["a@tamu.edu"], subject: "<Hi>", count: 2, draft: false, note: "" }), /<dt>to<\/dt>.*a@tamu\.edu.*&lt;Hi&gt;.*<dd>2<\/dd>.*<dd>no<\/dd>/s);
assert.match(valueMarkup(Array.from({ length: 30 }, (_, i) => i)), /5 more/);

const pending = { id: "a1", pending: true, status: "proposed", seen: 7, method: "POST", path: "/api/mail/send", summary: "Email <507>",
  body: { subject: "Hi" }, confirmation: "send", preview: { data: { recipients: 30 } }, page: "#mail", reason: "" };
const card = cardMarkup(pending);
assert.match(card, /data-seen="7"/);
assert.match(card, /Email &lt;507&gt;/);
assert.match(card, /data-assistant-approve="a1"/);
assert.match(card, /“send”/);
assert.match(card, /href="#mail"/);
const done = cardMarkup({ ...pending, pending: false, status: "succeeded", tier: "auto", events: [{ detail: { status: 201 } }] });
assert.doesNotMatch(done, /data-assistant-approve/);
assert.match(done, /made by the assistant/);
// A mail draft (gmail_mail.send) is drawn as the message it would send, with every attachment from Faculty's own route.
const draft = cardMarkup({ ...pending, endpoint: "gmail_mail.send", summary: "Email Lee their Exam 1", preview: { data: {
  sender: "prof@tamu.edu", notes: ["It carries 2 attachments (0.1 MB)."], message: { to: ["lee@tamu.edu"], cc: [], bcc: Array.from({ length: 12 }, (_, i) => `s${i}@tamu.edu`),
    subject: "Exam <1>", text: "Hi Lee,\n<b>attached</b>", sender: "prof@tamu.edu", attachments: [
      { route: "/api/documents/papers/p1.pdf", name: "Exam 1.pdf", type: "application/pdf", size: 90000 },
      { route: "/api/documents/messages/m/pages/s/image.png", name: "Page 1.png", type: "image/png", size: 4000 },
      { route: "javascript:alert(1)", name: "Odd <file>", type: "application/pdf", size: 10 }] } } } });
assert.match(draft, /data-mail-draft/);
assert.match(draft, /<dt>To<\/dt><dd>lee@tamu\.edu<\/dd>/);
assert.doesNotMatch(draft, /<dt>Cc<\/dt>/);
assert.match(draft, /and 6 more/);
assert.match(draft, /Exam &lt;1&gt;/);
assert.match(draft, /Hi Lee,<br>&lt;b&gt;attached&lt;\/b&gt;/);
assert.match(draft, /3 attachments/);
assert.match(draft, /href="\/api\/documents\/papers\/p1\.pdf"[^>]*>Exam 1\.pdf<\/a>/);
assert.match(draft, /<img src="\/api\/documents\/messages\/m\/pages\/s\/image\.png"/);
assert.doesNotMatch(draft, /javascript:/);
assert.match(draft, /<li>Odd &lt;file&gt; /);
assert.match(draft, /It carries 2 attachments/);
assert.match(draft, /sends it from prof@tamu\.edu/);
assert.match(draft, />Send<\/button>/);
assert.match(draft, /data-assistant-approve="a1"/);
assert.match(draft, /href="#mail"/);
// Without the preview's message (a refused or missing preview), the ordinary card is drawn.
assert.doesNotMatch(cardMarkup({ ...pending, endpoint: "gmail_mail.send" }), /data-mail-draft/);
// MAIL-SEND-LATER (2026-11-07): a message to send later is the same card, saying when it goes, with Schedule instead of Send.
const scheduled = cardMarkup({ ...pending, endpoint: "gmail_mail.schedule", confirmation: "schedule", body: { send_at: "2026-11-10T08:00" },
  preview: { data: { sender: "prof@tamu.edu", notes: [], message: { to: ["lee@tamu.edu"], cc: [], bcc: [], subject: "Exam 2", text: "Thursday.", sender: "prof@tamu.edu" } } } });
assert.match(scheduled, /data-mail-draft/);
assert.match(scheduled, /Sends Tue, Nov 10, 8:00 AM \(campus time\)/);
assert.match(scheduled, /schedules it from prof@tamu\.edu \(“schedule”\)/);
assert.match(scheduled, />Schedule<\/button>/);
assert.doesNotMatch(draft, /campus time/);
assert.match(cardMarkup({ ...pending, endpoint: "gmail_mail.schedule", body: {}, preview: draft.preview || { data: { sender: "p", notes: [], message: { to: [], cc: [], bcc: [], subject: "s", text: "t" } } } }), /No send time given/);
// CANVAS MESSAGE CARD (requirement, 2026-10-12): a Canvas message to
// sections is read from the Canvas page's review as one line per course, never the names of everyone who gets it.
const course = (/** @type {number} */ number, /** @type {number} */ roster, /** @type {number} */ reached, extra = {}) => ({ course_id: String(number), name: `MATH 140 ${number}`,
  sections: [number], recipients: reached, only_canvas: 0, kind: "group", too_many_for_group: false, roster, roster_reached: reached,
  students: [...Array.from({ length: Math.min(reached, 60) }, (_, i) => `Student <${number}-${i}>`), "[25 more items not shown: read again with find=]"], ...extra });
const review = { subject: "Quiz <2>", body: "Hi everyone,\n<b>Bring</b> the quiz.", individual: false, recipients: 250, group_limit: 100, canvas: "https://canvas.tamu.edu",
  courses: [course(507, 85, 85), course(508, 84, 82), course(514, 80, 80, { only_canvas: 1, recipients: 81 }), { ...course(515, 2, 2), sections: [515, 516, 517] }],
  unlinked: [{ section: 520, students: 79 }], not_reached: [{ name: "Lee, <Ana>", section: 508, reason: "not in the section’s Canvas course" }],
  left_out: [{ name: "Kim, Bo", reason: "Canvas lists the enrollment as inactive", course: "MATH 140 508" }, { name: "Old, Sam", reason: "dropped from Section 507 in Faculty", course: "MATH 140 507" }],
  attachments: [{ route: "/api/assistant/uploads/u1", name: "Review.pdf", type: "application/pdf", size: 90000 }] };
const canvasCard = { ...pending, endpoint: "canvas.message_send", path: "/api/canvas/messages/send", summary: "Remind every section", page: "#canvas", preview: { data: review } };
// MAIL-SEND-LATER: the same card for a Canvas message sent later, saying when, with Schedule for its students.
const canvasLater = cardMarkup({ ...canvasCard, endpoint: "canvas.message_schedule", body: { send_at: "2026-11-10T08:00" } });
assert.match(canvasLater, /data-canvas-message/);
assert.match(canvasLater, /Sends Tue, Nov 10, 8:00 AM \(campus time\)/);
assert.match(canvasLater, />Schedule for 250 students<\/button>/);
assert.doesNotMatch(cardMarkup(canvasCard), /campus time/);
const message = cardMarkup(canvasCard);
assert.match(message, /data-canvas-message/);
assert.match(message, /<dt>To<\/dt><dd>All students in 507 \(85\)<br>82 of 84 students in 508<br>All students in 514 \(80\), and 1 student only Canvas lists<br>All students in 515, 516 and 517 \(2\)<\/dd>/);
assert.doesNotMatch(message, /Student &lt;|more items not shown/);  // nobody who gets it is listed, nor the note of a cut list
assert.match(message, /<dt>Subject<\/dt><dd>Quiz &lt;2&gt;<\/dd>/);
assert.match(message, /Hi everyone,<br>&lt;b&gt;Bring&lt;\/b&gt; the quiz\./);
assert.match(message, /1 attachment<\/h4>.*href="\/api\/assistant\/uploads\/u1"[^>]*>Review\.pdf<\/a>/s);
assert.match(message, /data-tone="warning"[^>]*><strong class="callout-title">1 student of your sections is not in their Canvas course<\/strong><p>They will not get it through Canvas: Lee, &lt;Ana&gt; \(508\)<\/p>/);
assert.match(message, /Section 520 has no Canvas course linked<\/strong><p>79 students there will not get it\./);
// 508 misses two and Canvas does not list only one of them: the other is among the left out, so that fold is open.
assert.match(message, /<details class="disclosure" open><summary><span>Left out on purpose<\/span><span class="disclosure-hint">2<\/span>.*<strong>Canvas lists the enrollment as inactive<\/strong> \(1\)<br>Kim, Bo/s);
assert.match(message, /One group conversation in each Canvas course: students see who else got it and can reply to everyone\. Approving sends it from your Canvas account at canvas\.tamu\.edu \(“send”\)/);
assert.match(message, /data-assistant-approve="a1">Send to 250 students<\/button>/);
assert.match(message, /href="#canvas"/);
assert.doesNotMatch(message, /What the page’s preview says/);
// Everyone reached and nobody left out: the lines, the words and Send; a review from before the counts says how many, never "All".
const whole = cardMarkup({ ...canvasCard, preview: { data: { ...review, individual: true, courses: [course(507, 85, 85, { kind: "individual" })], unlinked: [], not_reached: [], left_out: [], attachments: undefined, recipients: 85 } } });
assert.match(whole, /<dd>All students in 507 \(85\)<\/dd>/);
assert.doesNotMatch(whole, /disclosure|attachment/);
// GROUP BY DEFAULT (requirement, 2026-10-13): the box ticked is said on a warning and
// on Send itself, never only in the last line; a group message has neither.
assert.match(whole, /Individual messages, not a group conversation/);
assert.match(whole, /Each of the 85 students gets their own conversation, and each reply comes to you separately\./);
assert.match(whole, /data-assistant-approve="a1">Send 85 individual messages<\/button>/);
assert.doesNotMatch(whole, /Send to 85 students|One group conversation/);
assert.doesNotMatch(message, /Individual messages, not a group conversation|individual messages<\/button>/);
const { roster, roster_reached, ...older } = course(507, 85, 85, { only_canvas: 2, too_many_for_group: true, kind: "individual" });
const earlier = cardMarkup({ ...canvasCard, preview: { data: { ...review, courses: [older, course(508, 84, 84)], recipients: 169 } } });
assert.match(earlier, /<dd>85 students in 507 \(2 students only Canvas lists\) · individual messages \(Canvas allows at most 100 people in a group conversation\)<br>All students in 508 \(84\)<\/dd>/);
assert.match(earlier, /One group conversation in each Canvas course that Canvas allows:/);
assert.match(cardMarkup({ ...canvasCard, preview: { data: { ...review, courses: [older], recipients: 85 } } }), /Each student gets their own conversation\. Approving/);
// Nobody reached: nothing to send. A refused preview keeps the ordinary card.
const nobody = cardMarkup({ ...canvasCard, preview: { data: { ...review, courses: [], recipients: 0 } } });
assert.match(nobody, /Nobody in the sections you chose can be reached through Canvas/);
assert.match(nobody, /data-assistant-approve="a1" disabled>Nobody to send to<\/button>/);
assert.doesNotMatch(nobody, /Approving sends it/);
assert.doesNotMatch(cardMarkup({ ...canvasCard, preview: { error: "Sign in to Canvas.", data: null } }), /data-canvas-message/);
// The announcement and the coding-task cards are drawn from the same parts as the others: opener, quoted text, Decline, Review link.
const announcement = cardMarkup({ ...canvasCard, endpoint: "canvas.announcement_post", preview: { data: { canvas: "https://canvas.tamu.edu", title: "Quiz <2>", message: "Line one\n<b>two</b>",
  courses: [{ name: "MATH 140 507", sections: [507] }], unlinked: [] } } });
assert.match(announcement, /^<article class="assistant-card" data-patch-key="action-a1" data-action-id="a1" data-seen="7" data-canvas-announcement>/);
assert.match(announcement, /<blockquote class="assistant-request">Line one<br>&lt;b&gt;two&lt;\/b&gt;<\/blockquote>/);
assert.match(announcement, /data-assistant-decline="a1">Decline<\/button><a class="link-arrow" href="#canvas">Review on its page<\/a><\/div>/);
const coding = cardMarkup({ ...pending, endpoint: "assistant.code_task_start", body: { title: "Fix <it>", request: "First\nSecond", brief: "" } });
assert.match(coding, /^<article class="assistant-card" data-patch-key="action-a1" data-action-id="a1" data-seen="7">/);
assert.match(coding, /<blockquote class="assistant-request">First<br>Second<\/blockquote>/);
assert.match(coding, /Start the coding agent<\/button><button class="text-button" type="button" data-assistant-decline="a1">Decline<\/button><\/div>/);
console.log("assistant helpers ok");

// The Code tab (static/assistant-code.js): diffs are escaped and marked by kind; a task shows its state, report, migration warning
// and, only when ready and changed, Apply behind a confirmation.
const { diffMarkup, taskMarkup, applyingLabel } = await import("../static/assistant-code.js");
const diff = diffMarkup("diff --git a/x b/x\n@@ -1 +1 @@\n-old <b>\n+new\n context");
assert.match(diff, /data-kind="file"/);
assert.match(diff, /data-kind="hunk"/);
assert.match(diff, /data-kind="del">-old &lt;b&gt;/);
assert.match(diff, /data-kind="add">\+new/);
const task = { id: "t1", title: "Sort <scans>", state: "ready", coder: "claude", model: "", base: "abc", page: "#inbox", request: "Sort them", brief: "In **inbox.js**",
  seen: 4, head: "f".repeat(40), rounds: [{ number: 1, state: "finished", steps: ["Edit static/inbox.js"], summary: "Done **ok**", error: "" }],
  files: [{ path: "static/inbox.js", added: "3", removed: "1" }], commits: [{ id: "a1", message: "m" }], migrations: ["x/migrations/002_y.sql"], diff: "+a", events: [] };
const shown = taskMarkup(task);
assert.match(shown, /Sort &lt;scans&gt;/);
assert.match(shown, /Ready to apply/);
assert.match(shown, /Adds a migration/);
assert.match(shown, /data-code-confirm="t1"/);
assert.doesNotMatch(shown, /data-code-apply=/);
const confirming = taskMarkup(task, { confirming: true });
assert.match(confirming, /data-code-apply="t1" data-head="f{40}" data-seen="4"/);
assert.doesNotMatch(taskMarkup({ ...task, files: [] }), /data-code-confirm/);
assert.match(taskMarkup({ ...task, state: "running", rounds: [{ ...task.rounds[0], state: "running" }] }), /data-code-stop="t1"/);
// While Apply runs (`applying` from the server), one status line with its stage and time replaces Apply now, Discard and the
// follow-up form, even if the confirmation was still open: nothing invites a second click.
const now = Date.parse("2026-10-09T12:03:30Z");
const applying = { stage: "checking", started_at: "2026-10-09T12:00:00Z", modules: 9, scripts: 2, done: 1, commands: 3 };
assert.equal(applyingLabel(applying, now), "Applying · running checks 2 of 3 · 3 min");
assert.equal(applyingLabel({ stage: "rebasing", started_at: "2026-10-09T12:03:00Z" }, now), "Applying · bringing it up to date with main · under a minute");
const busy = taskMarkup({ ...task, applying }, { confirming: true });
assert.match(busy, /data-code-applying="t1"/);
assert.match(busy, /Applying · running checks/);
assert.match(busy, /9 Python and 2 JavaScript check modules selected/);
assert.match(busy, /<button class="button" type="button" disabled aria-disabled="true">Applying…<\/button>/);
assert.doesNotMatch(busy, /data-code-apply=|data-code-discard=|data-code-follow=/);
assert.match(confirming, /Apply is refused while Faculty is busy/);
const passed = taskMarkup({ ...task, events: [{ kind: "checks_passed", detail: { head: "a".repeat(40), not_run: ["test_x", "test_y"] } }] });
assert.match(passed, /Required checks passed for commit a{10}\. 2 more matching check modules were over the limit and did not run\./);
console.log("assistant code tab ok");

// "Prepare remaining setup" (grading checklist, assignment workspace) promises approval: both ask with reviewOnly, and the shell's
// wrappers pass the options through (2026-10-09 review).
const { readFileSync } = await import("node:fs");
const staticSource = (name) => readFileSync(new URL(`../static/${name}`, import.meta.url), "utf8");
assert.match(staticSource("grading-rubrics.js"), /Do not certify, send or publish\.`, \{reviewOnly: true\}\)/);
assert.match(staticSource("assignment-workspace.js"), /never send, certify or publish\.`, \{reviewOnly: true\}\)/);
assert.doesNotMatch(staticSource("app.js"), /ask: text => assistantView\.ask\(text\)/);
console.log("review-only preparation buttons ok");

// What fetch_files showed: pictures inline, other files as links, each opening in the app's file viewer (VIEWER) rather than as a
// download; only Faculty's own /api/ routes are drawn.
const fetched = filesMarkup([
  { url: "/api/documents/messages/m1/pages/p1/image.png", open: "/api/documents/messages/m1/pages/p1/image.png?width=1700", kind: "image", name: "image.png", caption: "Alice <Exam 1>" },
  { url: "/api/documents/assemblies/r1/download", kind: "pdf", name: "reviewed-r1.pdf", size: 1258291, type: "application/pdf" },
  { url: "javascript:alert(1)", kind: "image", name: "evil" },
  { url: "https://example.com/x.png", kind: "image", name: "far" },
  { url: "/api/x\"onerror=\"alert(1)", kind: "image", name: "quote" },
]);
assert.match(fetched, /<img src="\/api\/documents\/messages\/m1\/pages\/p1\/image\.png" alt="Alice &lt;Exam 1&gt;"/);
assert.match(fetched, /href="\/api\/documents\/messages\/m1\/pages\/p1\/image\.png\?width=1700" data-view-file="\/api\/documents\/messages\/m1\/pages\/p1\/image\.png\?width=1700" data-view-name="image\.png"/);
assert.match(fetched, /<a class="link-arrow" href="\/api\/documents\/assemblies\/r1\/download" data-view-file="\/api\/documents\/assemblies\/r1\/download" data-view-name="reviewed-r1\.pdf">/);
assert.doesNotMatch(fetched, /target="_blank"/);
assert.match(fetched, /reviewed-r1\.pdf<\/a> <span class="assistant-shown-size">PDF · 1\.2 MB/);
assert.doesNotMatch(fetched, /javascript:|example\.com|onerror/);
// A HEIC photo's thumbnail is the viewer's re-encoded picture (most browsers cannot draw HEIC); it still opens its own route.
const heic = filesMarkup([{ url: "/api/absences/a1/documents/d1", kind: "image", type: "image/heic", name: "IMG_5001.HEIC" }]);
assert.match(heic, /<img src="\/api\/viewer\/picture\?route=%2Fapi%2Fabsences%2Fa1%2Fdocuments%2Fd1"/);
assert.match(heic, /data-view-file="\/api\/absences\/a1\/documents\/d1"/);
assert.equal(filesMarkup([]), "");
assert.equal(filesMarkup(undefined), "");


// QUIET STEPS (ASSISTANT, 2026-10-08): reads on the way to an answer fold into one closed "How it got this" under it, with the
// evidence check and each source once; pages opened, files shown, failed steps and stops stay in view; only a contradiction warns.
const said = (/** @type {any} */ e) => e.kind === "user" ? `<you>${e.text}</you>` : e.kind === "assistant" ? `<answer>${e.text}</answer>` : `<shown>${e.label || e.text}</shown>`;
const source = (path) => ({ path, observed_at: "2026-10-08T12:00:00-05:00", fingerprint: `f-${path}`, fingerprint_of: "response" });
const turn = [
  { kind: "user", text: "Any scans waiting?" },
  { kind: "tool", tool: "read_docs", label: "Read assistant/guide.md" },
  { kind: "tool", tool: "inbox", label: "Read inbox", evidence: [source("/api/inbox")] },
  { kind: "tool", tool: "open_page", label: "Open Inbox", link: { link: "#inbox", label: "Open Inbox" } },
  { kind: "tool", tool: "inbox", label: "Read inbox", ok: false },
  { kind: "status", text: "Checking the answer against its sources…", step: true },
  { kind: "assistant", text: "Two scans wait." },
  { kind: "verification", status: "review", partial: true, detail: "Review the flagged claims against their sources.",
    checks: [{ paragraph: 1, verdict: "uncertain", text: "Two scans wait." }], sources: [source("/api/inbox")] },
];
assert.deepEqual(turn.map(isStep), [false, true, true, false, false, true, false, false]);
const quiet = chatMarkup(turn, false, said);
assert.match(quiet, /^<you>Any scans waiting\?<\/you><shown>Open Inbox<\/shown><shown>Read inbox<\/shown><answer>Two scans wait\.<\/answer><details class="disclosure assistant-steps">/);
assert.match(quiet, /<summary><span>How it got this<\/span><span class="disclosure-hint">2 steps<\/span><\/summary>/);
assert.doesNotMatch(quiet, /<details[^>]* open/);
assert.equal(quiet.match(/<details class="disclosure assistant-evidence">/g).length, 1);
assert.match(quiet, /Sources · 1</);
assert.match(quiet, /Claims to review · 1/);
assert.match(quiet, /covered only part/);
assert.doesNotMatch(quiet, /Checking the answer/);
assert.doesNotMatch(quiet, /contradicts what it read/);
// The answer check's flagged verdicts sit inside the fold, after the steps: nothing about them between the answer and the fold.
assert.ok(quiet.indexOf("Claims to review") > quiet.indexOf("How it got this"));
// While it works (2026-10-09): the steps it has finished and the line it is on, never the fold.
const working = chatMarkup(turn.slice(0, 3), true, said);
assert.match(working, /<ol class="assistant-live" aria-label="Steps so far"><li>Read assistant\/guide\.md<\/li><li>Read inbox<\/li><\/ol><p class="assistant-tool assistant-working" role="status">Working…<\/p>$/);
assert.doesNotMatch(working, /How it got this|Sources ·/);
assert.match(chatMarkup(turn.slice(0, 6), true, said), /role="status">Checking the answer against its sources…<\/p>$/);
assert.match(chatMarkup(turn.slice(0, 1), true, said), /role="status">Working…<\/p>$/);
// Earlier turns keep their fold while a new one runs; a confident contradiction is raised outside it, quoting the paragraph.
const contradicted = { ...turn[7], checks: [{ paragraph: 1, verdict: "contradicted", text: "Two scans <wait>." }] };
const two = chatMarkup([...turn.slice(0, 7), contradicted, { kind: "user", text: "Thanks" }], true, said);
assert.match(two, /<answer>Two scans wait\.<\/answer><div class="callout" data-tone="warning" role="status"><strong class="callout-title">Something in this answer contradicts what it read<\/strong><p>Paragraph 1: “Two scans &lt;wait&gt;\.”<\/p><\/div><details class="disclosure assistant-steps">/);
assert.match(two, /<you>Thanks<\/you><p class="assistant-tool assistant-working" role="status">Working…<\/p>$/);
// A turn without reads or a check has no fold; a turn that ran nothing yet but is busy shows the working line.
assert.equal(chatMarkup([{ kind: "user", text: "Hi" }, { kind: "assistant", text: "Hello." }], false, said), "<you>Hi</you><answer>Hello.</answer>");
assert.equal(chatMarkup([], true, said), '<p class="assistant-tool assistant-working" role="status">Working…</p>');
