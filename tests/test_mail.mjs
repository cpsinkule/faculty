// The Mail page's pure helpers (static/mail.js): times, sizes, address lists and who a reply is written to.
import assert from "node:assert/strict";
import { whenText, sizeText, addressList, addAddresses, replyTarget, attachedMarkup, libraryMarkup } from "../static/mail.js";

const noon = Date.parse("2026-09-28T17:00:00Z");
assert.match(whenText("2026-09-28T17:05:00Z", noon), /\d/);
assert.equal(whenText("nonsense", noon), "");
assert.match(whenText("2025-01-02T12:00:00Z", noon), /2025/);
assert.doesNotMatch(whenText("2026-01-02T12:00:00Z", noon), /2026/);

assert.deepEqual([0, 512, 2048, 5 * 1048576].map(sizeText), ["", "512 B", "2 KB", "5.0 MB"]);

assert.deepEqual(addressList("a@tamu.edu, B@tamu.edu;\n a@TAMU.edu ,, "), ["a@tamu.edu", "B@tamu.edu"]);
assert.equal(addAddresses("a@tamu.edu", ["b@tamu.edu", "A@tamu.edu"]), "a@tamu.edu, b@tamu.edu");
assert.equal(addAddresses("", ["b@tamu.edu"]), "b@tamu.edu");

const alex = { name: "Alex", email: "alex@tamu.edu" }, me = { name: "", email: "prof@tamu.edu" };
assert.equal(replyTarget({ messages: [{ mine: false, from: [alex], to: [me] }, { mine: true, from: [me], to: [alex] }] }), "alex@tamu.edu");
assert.equal(replyTarget({ messages: [{ mine: true, from: [me], to: [alex] }] }), "alex@tamu.edu");
assert.equal(replyTarget({ messages: [] }), "");
const canvas = { name: "Alex", email: "notifications@instructure.com" }, canvasReply = { name: "Alex via Canvas", email: "reply+1-2~3@notifications.canvaslms.com" };
assert.equal(replyTarget({ messages: [{ mine: false, from: [canvas], reply_to: [canvasReply], to: [me] }] }), canvasReply.email);  // MAIL-REPLY-TO
assert.equal(replyTarget({ messages: [{ mine: false, from: [canvas], reply_to: [alex], to: [me] }] }), "alex@tamu.edu");  // MAIL-CANVAS-REPLY: the server gives the roster address
assert.equal(replyTarget({ messages: [{ mine: false, from: [alex], reply_to: [{ name: "?", email: "" }], to: [me] }] }), "alex@tamu.edu");
console.log("mail helpers ok");

// The compose window's attachments (MAIL-COMPOSE-ATTACH): each named, sized and removable; a Faculty file opens its own route in the
// app's file viewer (VIEWER), and only such a route.
const attached = attachedMarkup([{ name: "Notes <1>.pdf", size: 2048, file: {} }, { name: "Exam 1 - Lee.pdf", size: 1048576 * 2, route: "/api/documents/assemblies/r1/download" },
  { name: "odd", route: "javascript:alert(1)" }]);
assert.match(attached, /Notes &lt;1&gt;\.pdf<\/span><span class="field-hint">from your computer · 2 KB/);
assert.match(attached, /<a href="\/api\/documents\/assemblies\/r1\/download" data-view-file="\/api\/documents\/assemblies\/r1\/download" data-view-name="Exam 1 - Lee\.pdf">Exam 1 - Lee\.pdf<\/a>/);
assert.doesNotMatch(attached, /javascript:/);
assert.match(attached, /data-mail-detach="2"/);
assert.doesNotMatch(attachedMarkup([{ name: "a.pdf", size: 10, file: {} }], { removable: false }), /Remove/);
assert.match(attachedMarkup([{ name: "big.pdf", size: 26_000_000, file: {} }]), /at most 25 MB/);
assert.equal(attachedMarkup([]), "");

const library = libraryMarkup({ groups: [{ label: "Lee, Ann · MATH 140 507", items: [
  { route: "/api/documents/assemblies/r1/download", name: "Exam 1 - Lee, Ann.pdf", title: "Exam 1", detail: "Version A · reviewed 2026-09-30" },
  { route: null, name: "Quiz 2 - Lee, Ann.pdf", title: "Quiz 2", detail: "not reviewed yet" }] }, { label: "Kim", items: [], note: "No paper of this student was printed and scanned." }], note: "" },
  new Set(["/api/documents/assemblies/r1/download"]));
assert.match(library, /<h4 class="mail-library-group">Lee, Ann · MATH 140 507<\/h4>/);
assert.match(library, /data-mail-pick="0:0" disabled>Attached</);
assert.doesNotMatch(library, /data-mail-pick="0:1"/);  // a paper not reviewed yet cannot be attached
assert.match(library, /not reviewed yet/);
assert.match(library, /No paper of this student/);
assert.match(libraryMarkup({ groups: [{ label: "x", items: [{ route: "/api/x", name: "x.pdf", size: 900 }] }] }, new Set()), /data-mail-pick="0:0">Attach</);
console.log("mail attachments ok");
