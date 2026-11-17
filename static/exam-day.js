/**
 * An assessment's exam-day plan, `#exam-day/<assignment>/<term>` (AGENTS.md "Exam-day packet"; requirement, 2026-09-30). One list of the term's rooms for the assignment, each with its counts and its proctors (picked from the
 * TA lists or typed in), the proctor instructions every packet prints, and one Save. Each room's Print packet link opens its
 * seating chart (`#seating/<chart>?packet=1`), where the packet prints with the seat chart. Email proctors only builds a mailto:
 * link for the instructor's own mail program: Faculty sends nothing.
 *
 * GET/POST /api/exam-day/<assignment>/<term>: a save names the revision on screen (`base_revision`, 409 when stale) and a request
 * ID reused only while the plan being saved is unchanged (a lost answer is resent with the same ID by the next Save).
 */
import { patchChildren } from "./dom-patch.js";
import { calloutMarkup, emptyMarkup, loadingMarkup, registerLeaveGuard, wallDate } from "./ui.js";
import { recordedTime } from "./seating-state.js";

export const LIMIT = 8000;
const MAILTO_LIMIT = 1800;
// teaching_assistants.service.EMAIL: an address that goes into a mailto: link as it is.
const EMAIL = /^[A-Za-z0-9._+'-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$/;
const LEAVE_QUESTION = "Leave without saving the exam-day plan?";

/** `#exam-day/<assignment>/<term>` → its two IDs, or null. */
export function examDayRoute(hash) {
  const match = String(hash || "").split("?")[0].match(/^#exam-day\/([^/]+)\/([^/]+)$/);
  if (!match) return null;
  try {
    return { assignmentId: decodeURIComponent(match[1]), termId: decodeURIComponent(match[2]) };
  } catch {
    return null;
  }
}

/** A room's exam date and time on the campus clock, as the chart records them. */
export function roomWhen(room) {
  return room.exam_date ? [wallDate(room.exam_date), room.exam_time].filter(Boolean).join(" · ") : "No exam date on the chart";
}

/**
 * The Email proctors link: every proctor with an email address, once, and a body listing each room, its date and time and its
 * proctors, then the instructions. Past MAILTO_LIMIT characters the instructions are left to the printed packet; past that the
 * proctor names too. Null when no proctor has an email address.
 * @param {any} view the plan read @param {Record<string, any[]>} proctors @param {string} instructions
 */
export function proctorMail(view, proctors, instructions) {
  const emails = [];
  const seen = new Set();
  for (const room of view.rooms) {
    for (const person of proctors[room.chart_id] || []) {
      const key = String(person.email || "").toLowerCase();
      if (key && !seen.has(key)) { seen.add(key); emails.push(person.email); }
    }
  }
  if (!emails.length) return null;
  const subject = `${view.assignment.title} proctoring · ${view.assignment.code} · ${view.term.term_label}`;
  const heading = `${view.assignment.title} · ${view.assignment.code} · ${view.term.term_label}`;
  const room = (item, names) => {
    const line = `${item.room_name} · ${roomWhen(item)} · Section${item.sections.length === 1 ? "" : "s"} ${item.sections.join(", ")}`;
    if (!names) return line;
    const people = (proctors[item.chart_id] || []).map((person) => person.name);
    return `${line}\r\nProctors: ${people.length ? people.join(", ") : "none yet"}`;
  };
  const packet = "The proctor instructions are in each room's printed packet.";
  const bodies = [
    `${heading}\r\n\r\n${view.rooms.map((item) => room(item, true)).join("\r\n\r\n")}\r\n\r\nProctor instructions\r\n${instructions.replace(/\n/g, "\r\n")}`,
    `${heading}\r\n\r\n${view.rooms.map((item) => room(item, true)).join("\r\n\r\n")}\r\n\r\n${packet}`,
    `${heading}\r\n\r\n${view.rooms.map((item) => room(item, false)).join("\r\n")}\r\n\r\n${packet}`,
  ];
  const to = emails.map((email) => encodeURIComponent(email).replace(/%40/g, "@")).join(",");
  const links = bodies.map((body) => `mailto:${to}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`);
  const index = links.findIndex((href) => href.length <= MAILTO_LIMIT);
  const chosen = index === -1 ? links.length - 1 : index;
  return { href: links[chosen], recipients: emails.length, instructions: chosen === 0 };
}

/** The plan as the server stores it: rooms without proctors left out. */
function saved(proctors) {
  return Object.fromEntries(Object.entries(proctors).filter(([, people]) => people.length));
}

/**
 * Who a proctor is: the email if there is one, else the name, lower-cased. The picker's "(added)" mark and addProctor's refusal
 * share it, so they agree on who counts as already there (the server's `clean_proctors` keeps its own casefold key).
 * @param {{ email?: string, name: string }} person
 */
const proctorKey = (person) => (person.email ? `e:${person.email.toLowerCase()}` : `n:${person.name.toLowerCase()}`);

export function createExamDay({ api, escape, icon, toast, header = null }) {
  const e = (value) => escape(String(value ?? ""));
  const root = () => document.getElementById("exam-day-section");
  const onPage = () => Boolean(root() && !root().hidden);
  let key = "";
  let view = null;
  let error = "";
  let readAt = 0;
  let loading = null;
  let generation = 0;
  let draft = null;            // {instructions, proctors: {chart: [{name, email, phone}]}}
  let typed = new Map();       // chart -> an error for the "Someone else" form open in that room ("" when none)
  let fill = null;             // {label, previous}: what Start from or Reset replaced, for Undo
  let saving = false;
  let saveGeneration = 0;
  let saveError = "";
  let conflict = false;
  let pending = null;          // {signature, requestId}: a save whose answer never came, resent with the same ID

  registerLeaveGuard(() => (dirty() && onPage() ? LEAVE_QUESTION : null));
  // Leaving was confirmed (the leave guard stops the event otherwise): the unsaved plan is set aside, and a return reads again.
  window.addEventListener("hashchange", () => {
    if (examDayRoute(location.hash)) return;
    key = ""; view = null; draft = null; pending = null; fill = null; saveError = ""; conflict = false; typed = new Map();
    saving = false; saveGeneration++;
  });

  const fromView = (read) => ({ instructions: read.instructions, proctors: Object.fromEntries(read.rooms.map((room) => [room.chart_id, room.proctors.map((person) => ({ ...person }))])) });
  const signature = (value) => JSON.stringify({ instructions: value.instructions, proctors: saved(value.proctors) });
  function dirty() {
    return Boolean(view && draft && signature(draft) !== signature(fromView(view)));
  }
  const path = () => {
    const route = examDayRoute(location.hash);
    return `/api/exam-day/${encodeURIComponent(route.assignmentId)}/${encodeURIComponent(route.termId)}`;
  };

  function paintHeader() {
    const title = view?.assignment.title || "Exam-day packet";
    header?.set({
      route: location.hash,
      title,
      context: view ? [view.assignment.code, view.term.term_label, "Exam-day packet"].join(" · ") : "Exam-day packet",
      subtitle: "Proctors and instructions for each room; each room’s packet prints from its seating chart.",
      crumbs: [{ label: "Home", href: "#overview" }, { label: "Exams", href: "#exams" },
        ...(view ? [{ label: title, href: view.exam_href }] : []), { label: "Exam-day packet" }],
      tab: view ? `${title} exam day` : "Exam-day packet",
      action: null,
    });
  }

  async function render(force = false) {
    const node = root();
    const route = examDayRoute(location.hash);
    if (!node || !onPage()) return;
    if (!route) {
      header?.set({ route: location.hash, title: "Exam-day packet", action: null });
      node.innerHTML = emptyMarkup({ icon: "exam", title: "This exam-day plan address is incomplete", body: "Open it from an assessment’s Exams hub.", action: { label: "Exams", href: "#exams" } });
      return;
    }
    const wanted = `${route.assignmentId}/${route.termId}`;
    if (wanted !== key) {
      key = wanted; view = null; draft = null; error = ""; pending = null; fill = null; saveError = ""; conflict = false; typed = new Map();
      saving = false; saveGeneration++;
      node.innerHTML = loadingMarkup("Reading the exam-day plan…", 3);
    } else if (!force) {
      paintHeader();
      if (view && !node.querySelector(".exam-day")) draw();
      // The shell renders on every refresh: a plan being edited or saved, or read in the last half minute, is left as it is.
      if (loading?.key === wanted || (view && (dirty() || saving || Date.now() - readAt < 30000))) return;
    }
    paintHeader();
    await load(wanted, force);
  }

  async function load(wanted, reset = false) {
    const run = ++generation;
    const reading = (loading = { key: wanted });
    readAt = Date.now();
    try {
      const read = await api(path());
      if (run !== generation || key !== wanted) return;
      const same = view && JSON.stringify(read) === JSON.stringify(view);
      if (same && !reset && root()?.querySelector(".exam-day")) return;
      const keep = view && draft && dirty() && !reset;
      view = read;
      error = "";
      if (!keep) { draft = fromView(read); fill = null; }
    } catch (failure) {
      if (run !== generation || key !== wanted) return;
      error = failure.message;
    } finally {
      if (loading === reading) loading = null;
    }
    paintHeader();
    draw();
  }

  // ---- drawing -----------------------------------------------------------------------------------------------------

  function counts(room) {
    const versions = room.version_count
      ? Object.entries(room.version_totals).map(([letter, count]) => `${letter} ${count}`).join(" · ") + (room.unassigned_versions ? ` · ${room.unassigned_versions} without a letter` : "")
      : "One version";
    const stats = [["Seated", room.seated.toLocaleString()], ["DR center", room.dr_count.toLocaleString()], ["Versions", versions || "None assigned yet"],
      ["Extras", room.extras === null ? "Not printed yet" : room.extras.toLocaleString()]];
    return `<dl class="exam-day-stats">${stats.map(([label, value]) => `<div><dt>${e(label)}</dt><dd>${e(value)}</dd></div>`).join("")}</dl>`;
  }

  function pickMarkup(room) {
    const listed = new Set((draft.proctors[room.chart_id] || []).map(proctorKey));
    const option = (item, index) => {
      const added = listed.has(proctorKey(item));
      return `<option value="${index}"${added ? " disabled" : ""}>${e(item.name)} · Section${item.sections.length === 1 ? "" : "s"} ${e(item.sections.join(", "))}${added ? " (added)" : ""}</option>`;
    };
    const own = room.ta_options.map((item, index) => [item, index]).filter(([item]) => item.own);
    const other = room.ta_options.map((item, index) => [item, index]).filter(([item]) => !item.own);
    const groups = `${own.length ? `<optgroup label="TAs of ${e(`Section${room.sections.length === 1 ? "" : "s"} ${room.sections.join(", ")}`)}">${own.map(([item, index]) => option(item, index)).join("")}</optgroup>` : ""}${other.length ? `<optgroup label="Other TAs this term">${other.map(([item, index]) => option(item, index)).join("")}</optgroup>` : ""}`;
    const pick = `<label class="field exam-day-pick">Add a proctor<select data-pick="${e(room.chart_id)}"><option value="">${room.ta_options.length ? "Choose a TA…" : "No TA lists for this term"}</option>${groups}<option value="typed">Someone else (type their details)…</option></select></label>`;
    if (!typed.has(room.chart_id)) return pick;
    const problem = typed.get(room.chart_id);
    return `${pick}<fieldset class="exam-day-typed" data-typed="${e(room.chart_id)}"><legend>Someone else</legend><div class="exam-day-typed-fields"><label class="field">Name<input name="name" maxlength="120" autocomplete="off" required></label><label class="field">Email<span class="field-optional">optional</span><input name="email" type="email" maxlength="254" inputmode="email" spellcheck="false" autocapitalize="off" autocomplete="off"></label><label class="field">Phone<span class="field-optional">optional</span><input name="phone" type="tel" maxlength="40" autocomplete="off"></label></div>${problem ? `<p class="form-error" role="alert">${e(problem)}</p>` : ""}<div class="exam-day-typed-actions"><button type="button" class="button" data-typed-add="${e(room.chart_id)}">Add proctor</button><button type="button" class="text-button" data-typed-cancel="${e(room.chart_id)}">Cancel</button></div></fieldset>`;
  }

  function roomMarkup(room) {
    const people = draft.proctors[room.chart_id] || [];
    const chips = people.length
      ? `<ul class="exam-day-proctors" aria-label="${e(`Proctors in ${room.room_name}`)}">${people.map((person, index) => `<li class="chip" data-tone="neutral"><span class="exam-day-proctor-name">${e(person.name)}</span>${person.phone || person.email ? `<small>${e([person.phone, person.email].filter(Boolean).join(" · "))}</small>` : ""}<button type="button" class="exam-day-remove" data-remove="${e(room.chart_id)}" data-index="${index}" aria-label="${e(`Remove ${person.name} from ${room.room_name}`)}" title="Remove">${icon("close")}</button></li>`).join("")}</ul>`
      : '<p class="exam-day-none">No proctors yet.</p>';
    const state = room.confirmed ? ["neutral", "Confirmed for printing"] : ["info", "Not confirmed"];
    return `<article class="card exam-day-room" data-chart="${e(room.chart_id)}"><header class="card-head"><h3>${e(room.room_name)}</h3><span class="chip" data-tone="${state[0]}">${e(state[1])}</span>${room.check_ok ? "" : '<span class="chip" data-tone="warning">Seating to check</span>'}</header>
      <div class="card-body"><p class="exam-day-when">${e(roomWhen(room))} · ${e(`Section${room.sections.length === 1 ? "" : "s"} ${room.sections.join(", ")}`)}</p>${counts(room)}
        <h4 class="exam-day-subhead">Proctors</h4>${chips}${pickMarkup(room)}</div>
      <footer class="card-foot"><a class="link-arrow" href="#seating/${encodeURIComponent(room.chart_id)}?packet=1">Print packet</a><a class="text-button" href="#seating/${encodeURIComponent(room.chart_id)}">Seating chart</a></footer></article>`;
  }

  function savebarMarkup() {
    const changed = dirty();
    const state = saving ? "Saving…" : changed ? "Unsaved changes" : view.revision ? `Saved ${recordedTime(view.saved_at)}` : "Not saved yet · the default instructions are shown";
    const feedback = saveError ? `<div class="exam-day-feedback" role="alert"><p>${e(saveError)}</p>${conflict ? '<p class="exam-day-conflict"><button type="button" class="button" data-reload>Load the saved plan</button><small>Your unsaved changes here are set aside.</small></p>' : ""}</div>` : "";
    return `<div class="exam-day-savebar" data-state="${saving ? "saving" : changed ? "dirty" : "saved"}"><p class="exam-day-state" role="status">${e(state)}</p><div class="exam-day-actions">${changed && !saving ? '<button type="button" class="button" data-discard>Discard changes</button>' : ""}<button type="button" class="button primary" data-save${changed && !saving ? "" : " disabled"}>${saving ? "Saving…" : "Save plan"}</button></div>${feedback}</div>`;
  }

  function instructionsMarkup() {
    const others = view.others.length
      ? `<label class="field exam-day-start">Start from<select data-start-from><option value="">Another exam’s instructions…</option>${view.others.map((item, index) => `<option value="${index}">${e(`${item.title} · ${item.term_label}`)}</option>`).join("")}</select></label>` : "";
    const isDefault = draft.instructions === view.default_instructions;
    return `<section class="card exam-day-instructions" aria-labelledby="exam-day-instructions-title"><header class="card-head"><h2 id="exam-day-instructions-title">Proctor instructions</h2></header>
      <div class="card-body"><p class="field-hint">Printed in every room’s packet, after its cover.</p><div class="exam-day-tools">${others}<button type="button" class="button" data-reset${isDefault ? " disabled" : ""}>Reset to default</button></div>
        ${fill ? `<p class="exam-day-fill" role="status">${e(fill.label)} <button type="button" class="text-button" data-undo>Undo</button></p>` : ""}
        <textarea class="exam-day-text" data-instructions rows="16" maxlength="${LIMIT}" spellcheck="true" aria-labelledby="exam-day-instructions-title">${e(draft.instructions)}</textarea>
        <p class="field-hint" data-count>${draft.instructions.length.toLocaleString()} of ${LIMIT.toLocaleString()} characters</p></div></section>`;
  }

  function markup() {
    if (!view) return `<div class="exam-day">${calloutMarkup({ tone: "danger", title: "The exam-day plan could not be read", body: error || "Try again in a moment.", action: { label: "Try again", data: { examDayRetry: "" } } })}</div>`;
    const mail = proctorMail(view, draft.proctors, draft.instructions);
    const mailLink = mail ? `<a class="text-button" data-mail href="${e(mail.href)}">Email proctors (${mail.recipients})</a>` : '<span class="exam-day-none">Add a proctor’s email to email them</span>';
    const rooms = view.rooms.length
      ? `<div class="exam-day-room-list">${view.rooms.map(roomMarkup).join("")}</div>`
      : emptyMarkup({ icon: "grid", title: "No seating charts for this exam in this term", body: "Each room’s packet prints from its seating chart. Seat the exam first.", action: { label: "Seating charts", href: "#seating" } });
    return `<div class="exam-day">${error ? calloutMarkup({ tone: "warning", title: "The plan could not be read again", body: error }) : ""}
      <section class="exam-day-rooms" aria-labelledby="exam-day-rooms-title"><div class="exam-day-head"><h2 id="exam-day-rooms-title">Rooms and proctors</h2>${view.rooms.length ? mailLink : ""}</div>
        <p class="supporting-copy">Each room prints one packet from its seating chart: a cover with its counts and proctors, these instructions, the room roster with the DR list, and the seat chart.</p>${rooms}</section>
      ${instructionsMarkup()}${savebarMarkup()}</div>`;
  }

  function draw() {
    const host = root();
    if (!host) return;
    bind(host);
    patchChildren(host, markup());
    const text = host.querySelector("[data-instructions]");
    if (text && draft && text.value !== draft.instructions) text.value = draft.instructions;
    host.querySelectorAll("select[data-pick], select[data-start-from]").forEach((select) => { select.value = ""; });
  }

  // ---- editing -----------------------------------------------------------------------------------------------------

  function addProctor(chart, person) {
    const people = draft.proctors[chart] || (draft.proctors[chart] = []);
    if (people.some((item) => proctorKey(item) === proctorKey(person))) return `${person.name} is already a proctor in this room.`;
    if (people.length >= 12) return "A room can have at most 12 proctors.";
    people.push({ name: person.name, email: person.email || "", phone: person.phone || "" });
    return "";
  }

  function setInstructions(text, label) {
    fill = { label, previous: draft.instructions };
    draft.instructions = text;
    draw();
  }

  async function save() {
    if (saving || !dirty()) return;
    const body = { base_revision: view.revision, instructions: draft.instructions, proctors: saved(draft.proctors) };
    const signed = JSON.stringify(body);
    if (!pending || pending.signature !== signed) pending = { signature: signed, requestId: crypto.randomUUID() };
    const target = key;
    const run = ++saveGeneration, sent = signature(draft);
    saving = true; saveError = ""; conflict = false;
    draw();
    try {
      const result = await api(path(), "POST", { ...body, request_id: pending.requestId });
      if (target !== key || run !== saveGeneration) return;
      pending = null;
      const unchanged = signature(draft) === sent;
      view = result;
      if (unchanged) { draft = fromView(result); fill = null; }
      readAt = Date.now();
      toast("Exam-day plan saved.");
    } catch (failure) {
      // A definite answer ends this request; a save whose answer never came is sent again with the same request ID.
      if (target !== key || run !== saveGeneration) return;
      if (failure.status) pending = null;
      saveError = failure.message;
      conflict = failure.status === 409;
    } finally {
      if (target === key && run === saveGeneration) { saving = false; draw(); }
    }
  }

  function bind(host) {
    if (host.dataset.examDayBound) return;
    host.dataset.examDayBound = "1";
    host.addEventListener("click", (event) => {
      const target = event.target.closest("button, a");
      if (!target || !host.contains(target)) return;
      if (target.matches("[data-exam-day-retry]")) { void load(key, true); return; }
      if (!view || !draft) return;
      if (target.matches("[data-remove]")) {
        const people = draft.proctors[target.dataset.remove] || [];
        people.splice(Number(target.dataset.index), 1);
        draw();
        host.querySelector(`select[data-pick="${CSS.escape(target.dataset.remove)}"]`)?.focus();
      } else if (target.matches("[data-typed-add]")) {
        const chart = target.dataset.typedAdd;
        const form = host.querySelector(`[data-typed="${CSS.escape(chart)}"]`);
        const value = (name) => form.querySelector(`[name="${name}"]`).value.trim();
        const person = { name: value("name").split(/\s+/).join(" "), email: value("email"), phone: value("phone").split(/\s+/).join(" ") };
        const problem = !person.name ? "Give the proctor a name." : person.email && !EMAIL.test(person.email) ? "That email address does not look like one (name@tamu.edu)." : addProctor(chart, person);
        if (problem) { typed.set(chart, problem); draw(); form.querySelector(problem.includes("email") ? '[name="email"]' : '[name="name"]')?.focus(); return; }
        typed.delete(chart);
        draw();
        host.querySelector(`select[data-pick="${CSS.escape(chart)}"]`)?.focus();
      } else if (target.matches("[data-typed-cancel]")) {
        typed.delete(target.dataset.typedCancel);
        draw();
        host.querySelector(`select[data-pick="${CSS.escape(target.dataset.typedCancel)}"]`)?.focus();
      } else if (target.matches("[data-reset]")) {
        setInstructions(view.default_instructions, "The default instructions are back.");
      } else if (target.matches("[data-undo]") && fill) {
        draft.instructions = fill.previous;
        fill = null;
        draw();
        host.querySelector("[data-instructions]")?.focus();
      } else if (target.matches("[data-save]")) {
        void save();
      } else if (target.matches("[data-discard]")) {
        draft = fromView(view); fill = null; typed = new Map(); saveError = ""; conflict = false;
        draw();
      } else if (target.matches("[data-reload]")) {
        saveError = ""; conflict = false; pending = null;
        void load(key, true);
      }
    });
    host.addEventListener("change", (event) => {
      const select = event.target;
      if (!(select instanceof HTMLSelectElement) || !view || !draft || !select.value) return;
      if (select.matches("[data-pick]")) {
        const chart = select.dataset.pick;
        if (select.value === "typed") {
          typed.set(chart, "");
          draw();
          host.querySelector(`[data-typed="${CSS.escape(chart)}"] [name="name"]`)?.focus();
          return;
        }
        const room = view.rooms.find((item) => item.chart_id === chart);
        const option = room?.ta_options[Number(select.value)];
        const problem = option ? addProctor(chart, option) : "";
        draw();
        if (problem) toast(problem, true);
      } else if (select.matches("[data-start-from]")) {
        const other = view.others[Number(select.value)];
        if (other) setInstructions(other.instructions, `Filled from ${other.title} · ${other.term_label}. Save to keep it.`);
      }
    });
    host.addEventListener("input", (event) => {
      if (!event.target.matches?.("[data-instructions]") || !draft) return;
      draft.instructions = event.target.value;
      draw();
    });
    host.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || !event.target.closest?.("[data-typed]") || event.target.tagName !== "INPUT") return;
      event.preventDefault();
      event.target.closest("[data-typed]").querySelector("[data-typed-add]")?.click();
    });
  }

  return { render };
}
