// @ts-check
/**
 * One room's exam-day packet (AGENTS.md "Exam-day packet"), as the chart page previews and prints it before the seat chart: a
 * cover (the room, the date, who sits here, the copies of each exam version and the extras, the proctors), the proctor
 * instructions, then the room roster and DR list exactly as Print room roster shows them. Pure markup from
 * GET /api/exam-day/charts/<chart>/packet; every text is escaped here. Printing it creates nothing.
 */
import { roomRosterMarkup } from "./seating-roster.js";
import { wallDate } from "./ui.js";

/** @param {number} count @param {string} one @param {string} [many] */
const plural = (count, one, many = `${one}s`) => `${count.toLocaleString()} ${count === 1 ? one : many}`;

/**
 * @param {any} packet the packet read
 * @param {(value: unknown) => string} escape
 * @param {{print?: boolean}} [options] print: the roster keeps Print room roster's print class (hidden on screen)
 */
export function packetMarkup(packet, escape, { print = false } = {}) {
  const e = (/** @type {unknown} */ value) => escape(String(value ?? ""));
  const sections = `Section${packet.sections.length === 1 ? "" : "s"} ${packet.sections.join(", ")}`;
  const when = packet.exam_date ? [wallDate(packet.exam_date, { year: true }), packet.exam_time].filter(Boolean).join(" · ") : "Not recorded on the seating chart";
  const facts = [["Room", packet.room_name], ["Date and time", when], ["Students in this room", packet.seated.toLocaleString()],
    ["DR-center testers (not in this room)", packet.dr_count.toLocaleString()]]
    .map(([label, value]) => `<div><dt>${e(label)}</dt><dd>${e(value)}</dd></div>`).join("");
  const printed = packet.printing;
  const row = (/** @type {string} */ label, /** @type {string} */ value, className = "") => `<tr${className ? ` class="${className}"` : ""}><th scope="row">${e(label)}</th><td>${e(value)}</td></tr>`;
  const copies = [
    ...(packet.versions
      ? packet.versions.counts.map((/** @type {any} */ item) => row(`Version ${item.version}`, item.count.toLocaleString()))
      : [row("Exam copies for seated students", packet.seated.toLocaleString())]),
    packet.versions?.unassigned ? row("Seated without a version letter", packet.versions.unassigned.toLocaleString()) : "",
    row("Extras (printed for no student)", printed ? printed.extras.toLocaleString() : "Not known: no print jobs yet"),
    printed ? row("Total printed", printed.copies.toLocaleString(), "packet-total") : "",
  ].join("");
  const jobs = printed ? `<p class="packet-note">Print jobs: ${printed.sections.map((/** @type {any} */ item) =>
    `Section ${e(item.section_number)} · ${e(plural(item.copies, "copy", "copies"))} (${e(plural(item.extras, "extra"))})`).join("; ")}.</p>` : "";
  const warnings = packet.warnings.length ? `<ul class="packet-warnings">${packet.warnings.map((/** @type {string} */ text) => `<li>${e(text)}</li>`).join("")}</ul>` : "";
  const proctors = packet.proctors.length
    ? `<table class="packet-table packet-proctors"><thead><tr><th scope="col">Proctor</th><th scope="col">Phone</th><th scope="col">Email</th></tr></thead><tbody>${packet.proctors.map((/** @type {any} */ person) =>
      `<tr><td>${e(person.name)}</td><td>${e(person.phone || "—")}</td><td>${e(person.email || "—")}</td></tr>`).join("")}</tbody></table>`
    : '<p class="packet-note">No proctors assigned to this room yet.</p>';
  const cover = `<section class="packet-cover"><header><p class="packet-eyebrow">Exam-day packet · ${e(packet.room_name)}</p><h2>${e(packet.title)}</h2><p>${e(packet.code)} · ${e(packet.term_label)} · ${e(sections)}</p></header>
    <dl class="packet-facts">${facts}</dl>
    <h3>Exam copies for this room</h3><table class="packet-table packet-copies"><tbody>${copies}</tbody></table>${jobs}${warnings}
    <h3>Proctors</h3>${proctors}</section>`;
  const instructions = `<section class="packet-instructions"><h2>Proctor instructions</h2><p class="packet-note">${e(packet.title)} · ${e(packet.room_name)} · ${e(when)}</p>${packet.instructions
    ? `<div class="packet-instructions-text">${e(packet.instructions)}</div>` : '<p class="packet-note">No instructions were saved for this exam.</p>'}</section>`;
  const roster = `<section class="packet-roster${print ? " seating-room-roster" : ""}">${roomRosterMarkup(packet.roster, escape)}</section>`;
  return `${cover}${instructions}${roster}`;
}
