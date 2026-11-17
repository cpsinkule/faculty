// PICTURE-SEARCH (requirement, 2026-10-10): the Inbox search's strip of the pages and pictures
// nearest the words.
import assert from "node:assert/strict";
import test from "node:test";
import { describesAPicture, pictureStripMarkup } from "../static/picture-search.js";

const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const hit = (id, label = "Exam 1, page 2") => ({ source: "scan", kind: "Scanned page", id, label, where: "MATH 140 · Fall 2026 · scanned 2026-09-19",
  image: `/api/documents/messages/m1/pages/${id}/image.png`, thumb: `/api/documents/messages/m1/pages/${id}/image.png?width=300`,
  open: `/api/documents/messages/m1/pages/${id}/image.png?width=1700`, link: "#inbox/job/j1/pages", matched: "look", score: 0.41 });

test("words that describe something ask for pages; a job ID or a filename does not", () => {
  for (const query of ["supply and demand graph", "crossed out matrix", "blank answer box"]) assert.equal(describesAPicture(query), true, query);
  for (const query of ["", "q2", "3f2a9c1e-77aa-4b21-9c0e-0a1b2c3d4e5f", "deadbeef", "doc00048720260919100456.pdf", "roster.csv"]) assert.equal(describesAPicture(query), false, query);
});

test("each page opens in the viewer, links to its scan, and says where it is; nothing is drawn for nothing", () => {
  assert.equal(pictureStripMarkup(null, escape), "");
  assert.equal(pictureStripMarkup({ query: "a graph", results: [] }, escape), "");
  const strip = pictureStripMarkup({ query: "a graph", results: [hit("p1"), hit("p2", "Scan page 7")] }, escape, { open: true });
  assert.match(strip, /^<details class="disclosure picture-strip" data-disclosure-key="inbox:picture-strip" open>/);
  assert.match(strip, /Pages and pictures for “a graph”/);
  assert.match(strip, /2 nearest/);
  assert.equal((strip.match(/<figure /g) || []).length, 2);
  assert.match(strip, /data-view-file="\/api\/documents\/messages\/m1\/pages\/p1\/image\.png\?width=1700"/);
  assert.match(strip, /<img src="\/api\/documents\/messages\/m1\/pages\/p1\/image\.png\?width=300" alt="Exam 1, page 2" loading="lazy"/);
  assert.match(strip, /<a href="#inbox\/job\/j1\/pages">Scan page 7<\/a><small>MATH 140 · Fall 2026 · scanned 2026-09-19<\/small>/);
  assert.doesNotMatch(strip, /0\.41|score/);  // a likeness ranks the strip; it is not shown as a finding
  assert.doesNotMatch(pictureStripMarkup({ query: "a graph", results: [hit("p1")] }, escape), / open>/);
});

test("a page of a PDF opens at that page, and a student's file with no page of its own is not a link", () => {
  const notes = { source: "notes", id: "n1:3#2", label: "3.2 Linear Inequalities, page 2", where: "Course notes · MATH 140", image: "/api/course-notes/n1/parts/3/notes.pdf",
    pdf_page: 2, thumb: "/api/viewer/page.png?route=%2Fapi%2Fcourse-notes%2Fn1%2Fparts%2F3%2Fnotes.pdf&page=2", open: "/api/course-notes/n1/parts/3/notes.pdf", open_page: 2,
    link: "#course/g1/home/notes", matched: "words", score: 0.8 };
  const sent = { source: "mail", id: "m1:0#1", label: "Attachment a student sent", where: "Canvas message · sent 2026-10-01", image: "/api/communications/messages/m1/attachments/0",
    thumb: "/api/viewer/picture?route=%2Fapi%2Fcommunications%2Fmessages%2Fm1%2Fattachments%2F0", open: "/api/communications/messages/m1/attachments/0", link: "", matched: "look", score: 0.7 };
  const strip = pictureStripMarkup({ query: "inequalities", results: [notes, sent] }, escape, { open: true });
  assert.match(strip, /href="\/api\/course-notes\/n1\/parts\/3\/notes\.pdf#page=2" data-view-file="\/api\/course-notes\/n1\/parts\/3\/notes\.pdf" data-view-name="3\.2 Linear Inequalities, page 2" data-view-page="2"/);
  assert.match(strip, /<img src="\/api\/viewer\/page\.png\?route=%2Fapi%2Fcourse-notes%2Fn1%2Fparts%2F3%2Fnotes\.pdf&amp;page=2"/);
  assert.match(strip, /data-patch-key="notes:n1:3#2"/);
  assert.match(strip, /<figcaption><span>Attachment a student sent<\/span><small>Canvas message · sent 2026-10-01<\/small>/);
  assert.match(strip, /<img src="\/api\/viewer\/picture\?route=%2Fapi%2Fcommunications%2Fmessages%2Fm1%2Fattachments%2F0"/);
});

test("a printed page says how many scans of it there are, and links to the newest", () => {
  const printed = { source: "template", id: "t1#2", label: "Quiz 3, page 2", where: "Printed copy · MATH 140 · A", image: "/api/assignment-templates/t1/pages/2.png",
    thumb: "/api/assignment-templates/t1/pages/2/preview.webp", open: "/api/assignment-templates/t1/pages/2.png", link: "#documents?assignment=a1&step=details",
    matched: "words", score: 0.73, scans: { count: 311, link: "#inbox/job/j9/pages" } };
  const strip = pictureStripMarkup({ query: "inequalities", results: [printed, { ...printed, id: "t1#1", scans: { count: 1, link: "#inbox/job/j9/pages" } }, hit("p1")] }, escape, { open: true });
  assert.match(strip, /<small>Printed copy · MATH 140 · A<\/small><a class="picture-strip-scans" href="#inbox\/job\/j9\/pages">311 scans<\/a><\/figcaption>/);
  assert.match(strip, />1 scan<\/a>/);
  assert.equal((strip.match(/picture-strip-scans/g) || []).length, 2);  // a result with no copies says nothing
});

test("a search by a picture is headed by the file it was searched by", () => {
  const strip = pictureStripMarkup({ like: "photo <1>.jpg", query: "", results: [hit("p1")] }, escape, { open: true });
  assert.match(strip, /Pages and pictures like “photo &lt;1&gt;\.jpg”/);
  assert.doesNotMatch(strip, /Pages and pictures for/);
});

test("the words and the labels are escaped", () => {
  const strip = pictureStripMarkup({ query: '<img src=x onerror="1">', results: [hit("p1", "<b>Quiz</b>")] }, escape, { open: true });
  assert.doesNotMatch(strip, /<img src=x|<b>Quiz/);
  assert.match(strip, /&lt;b&gt;Quiz&lt;\/b&gt;/);
});
