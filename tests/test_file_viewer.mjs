// The in-app file viewer's pure helpers (static/file-viewer.js; AGENTS.md "Faculty assistant", VIEWER): which routes open, the link
// that opens one, and what the viewer draws for a PDF, a picture, a text and any other file.
import assert from "node:assert/strict";
import { viewableRoute, viewLink, viewerBodyMarkup, printableRoute } from "../static/file-viewer.js";

const esc = (/** @type {unknown} */ value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] || c);

assert.equal(viewableRoute("/api/documents/assemblies/r1/download"), true);
assert.equal(viewableRoute("/api/documents/messages/m/pages/p/image.png?width=1700"), true);
for (const bad of ["javascript:alert(1)", "https://example.com/a.pdf", "/api/../x", "/static/app.js", '/api/x"onerror="1', 7, null]) assert.equal(viewableRoute(bad), false, String(bad));
assert.equal(viewLink("/api/a?b=1&c=2", "Exam <1>.pdf", esc), 'href="/api/a?b=1&amp;c=2" data-view-file="/api/a?b=1&amp;c=2" data-view-name="Exam &lt;1&gt;.pdf"');
// A topic of the lecture notes opens its section at the topic's page; a modifier-click opens the browser's own viewer there.
assert.equal(viewLink("/api/course-notes/n/parts/4/x.pdf", "2.1 Functions", esc, 2), 'href="/api/course-notes/n/parts/4/x.pdf#page=2" data-view-file="/api/course-notes/n/parts/4/x.pdf" data-view-name="2.1 Functions" data-view-page="2"');
assert.doesNotMatch(viewLink("/api/x.pdf", "x", esc, 1), /page/);

const pdf = viewerBodyMarkup({ route: "/api/documents/assemblies/r1/download", name: "Exam 1.pdf", kind: "pdf", pages: 3, page_size: [1275, 1651], sha256: "ab".repeat(32) }, esc);
assert.equal((pdf.match(/<figure class="file-viewer-page" data-page="\d">/g) || []).length, 3);
assert.match(pdf, /<figure class="file-viewer-page" data-page="1"><img src="\/api\/viewer\/page\.png\?route=%2Fapi%2Fdocuments%2Fassemblies%2Fr1%2Fdownload&amp;page=1&amp;sha256=(ab){32}" width="1275" height="1651" loading="eager" alt="Page 1 of 3">/);
assert.match(pdf, /page=3&amp;sha256=(ab){32}" width="1275" height="1651" loading="lazy"/);
assert.match(pdf, /<figcaption>Page 2 of 3<\/figcaption>/);

const picture = viewerBodyMarkup({ route: "/api/inbox/artifacts/c1/download", name: "Crop <q3>", kind: "image" }, esc);
assert.match(picture, /<img src="\/api\/viewer\/picture\?route=%2Fapi%2Finbox%2Fartifacts%2Fc1%2Fdownload" alt="Crop &lt;q3&gt;">/);

const text = viewerBodyMarkup({ route: "/api/x.csv", name: "x.csv", kind: "text", text: "a,b\n<script>", truncated: true }, esc);
assert.match(text, /<pre class="file-viewer-text">a,b\n&lt;script&gt;<\/pre>/);
assert.match(text, /Only the beginning is shown/);

assert.match(viewerBodyMarkup({ route: "/api/x.docx", name: "x.docx", kind: "file" }, esc), /no preview here\. Download it to open it/);

// A PDF Faculty made, where the browser shows PDFs itself: the browser's own viewer, framed from /api/viewer/pdf, at the page asked.
const own = { route: "/api/course-notes/n/parts/4/x.pdf", name: "2.1 Functions.pdf", kind: "pdf", native: true, sha256: "cd".repeat(32) };
assert.equal(viewerBodyMarkup(own, esc, { native: true, page: 2 }),
  '<iframe class="file-viewer-frame" src="/api/viewer/pdf?route=%2Fapi%2Fcourse-notes%2Fn%2Fparts%2F4%2Fx.pdf&amp;sha256=' + "cd".repeat(32) + '#page=2" title="2.1 Functions.pdf"></iframe>');
assert.doesNotMatch(viewerBodyMarkup(own, esc, { native: true }), /#page/);
// Not where the browser cannot (a phone), and never for a PDF the server did not call Faculty's own: the pictures.
assert.doesNotMatch(viewerBodyMarkup({ ...own, pages: 2 }, esc, { native: false }), /iframe/);
assert.doesNotMatch(viewerBodyMarkup({ ...own, native: undefined, pages: 2 }, esc, { native: true }), /iframe/);

// A CSV export: a table whose first row is the header, every cell escaped.
const table = viewerBodyMarkup({ route: "/api/gradebook/terms/t/export.csv", kind: "table", rows: [["Name", "Score"], ["Lee <b>", "9"]], truncated: true }, esc);
assert.match(table, /<thead><tr><th>Name<\/th><th>Score<\/th><\/tr><\/thead><tbody><tr><td>Lee &lt;b&gt;<\/td><td>9<\/td><\/tr><\/tbody>/);
assert.match(table, /Only the first rows and columns are shown/);

// An email: its headers, its text and its attachments by name, all escaped.
const mail = viewerBodyMarkup({ route: "/api/inbox/messages/m/original", kind: "email", headers: [["From", "Scanner <s@tamu.edu>"], ["Subject", "Exam <1>"]],
  text: "Pages <b>attached</b>", attachments: [{ name: "scan<1>.pdf", type: "application/pdf", size: 2048 }] }, esc);
assert.match(mail, /<dt>From<\/dt><dd>Scanner &lt;s@tamu\.edu&gt;<\/dd><dt>Subject<\/dt><dd>Exam &lt;1&gt;<\/dd>/);
assert.match(mail, /<pre class="file-viewer-text">Pages &lt;b&gt;attached&lt;\/b&gt;<\/pre>/);
assert.match(mail, /<li>scan&lt;1&gt;\.pdf <span class="field-hint">application\/pdf · 2 KB<\/span><\/li>/);
assert.doesNotMatch(viewerBodyMarkup({ route: "/api/x", kind: "email", headers: [], text: "" }, esc), /Attachments/);
assert.match(viewerBodyMarkup({ route: "/api/x.pdf", name: "x.pdf", kind: "file", note: "The pages could not be shown (bad)." }, esc), /could not be shown \(bad\)/);
console.log("file viewer helpers ok");

// Print at Home in the viewer (DOCUMENTS "SEND TO PRINTER"; requirement, 2026-10-18):
// a job's, a stack's or a room's print PDF can be sent; any other file cannot.
assert.deepEqual(printableRoute("/api/documents/batches/b%2F1/download"), { kind: "job", batchIds: ["b/1"], chartId: null });
assert.deepEqual(printableRoute("/api/documents/stack?batches=a%2Cb"), { kind: "stack", batchIds: ["a", "b"], chartId: null });
assert.deepEqual(printableRoute("/api/seating/c1/room-export/print?batches=a%2Cb"), { kind: "room", batchIds: ["a", "b"], chartId: "c1" });
assert.equal(printableRoute("/api/seating/c1/room-export/labels?media=dk-1201&batches=a"), null);
assert.equal(printableRoute("/api/documents/batches/b/labels?media=dk-1201"), null);
assert.equal(printableRoute("/api/documents/batches/b/download?manifest=1"), null);
assert.equal(printableRoute("https://elsewhere.test/api/documents/batches/b/download"), null);
// ASSISTANT VIEWER: malformed percent-encoding is not a printable route and must not crash the viewer's frame.
assert.equal(printableRoute("/api/documents/batches/%E0%A4%A/download"), null);
assert.equal(printableRoute("/api/seating/%/room-export/print?batches=a"), null);
