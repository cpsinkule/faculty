/**
 * A course's lecture notes (AGENTS.md "Course notes"): `#course/<group>/home/notes` renders into [data-course-notes-host].
 *
 * Upload saves the PDF at once: the server finds each section (the PDF's bookmarks and the headings that open its pages) and keeps
 * every part as its own PDF, so this page only lists what the server recorded. A section opens its own PDF in the app's file viewer
 * (with a Download button on each section) and a topic opens it at the topic's page; the search filters sections and topics in the
 * browser. An earlier upload can be looked at (browser memory only); Remove takes the shown upload off the page (the server keeps
 * it, and Restore brings it back).
 */
import { actionMenuMarkup, bindActionMenu, calloutMarkup, dialogSession, disclosureMarkup, emptyMarkup, loadingMarkup, plural } from "./ui.js";
import { viewLink } from "./file-viewer.js";

const LONG = 300000; // a long PDF is read and cut in the document sandbox before the answer comes back

/** @param {{api: any, escape: (v: any) => string, icon: (name: string) => string, toast: any, showDialog: (html: string) => void,
 *  dialogHeader: (t: string, s?: string) => string, header?: any}} deps */
export function createCourseNotes({ api, escape, icon, toast, showDialog, dialogHeader, header = null }) {
  const e = escape;
  /** @type {any} */ let state = null; // {groupId, definitionId, code, termLabel, view, showing, query}
  let generation = 0;
  const host = () => /** @type {HTMLElement|null} */ (document.querySelector("[data-course-notes-host]"));
  const dialog = () => /** @type {HTMLDialogElement} */ (document.querySelector("#dialog"));
  const newId = (prefix) => `${prefix}-${crypto.randomUUID()}`;
  const day = (iso) => {
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  };
  const courseHref = (group) => `#course/${encodeURIComponent(group.id)}`;
  const active = (owner) => owner === state && owner.route === location.hash.split("?")[0] && Boolean(host()?.isConnected);

  /** The edition on screen: an earlier one the instructor chose to look at, else the one the course page shows. */
  function shown() {
    const view = state?.view;
    if (!view) return null;
    const all = [view.current, ...view.earlier, ...view.removed].filter(Boolean);
    return all.find((edition) => edition.id === state.showing) || view.current;
  }

  function setHeader(group, termLabel) {
    if (!active(state)) return;
    const edition = state?.view?.current;
    header?.set({
      route: location.hash,
      title: "Lecture notes",
      context: [group.code, "every term"].filter(Boolean).join(" · "),
      subtitle: edition ? `${edition.title} · ${plural(edition.sections, "section")}, each its own PDF.` : "Upload the notes once; each section is saved as its own PDF.",
      crumbs: [
        { label: "Home", href: "#overview" },
        { label: "Courses", href: "#current-courses" },
        { label: [group.code, termLabel].filter(Boolean).join(" · "), href: courseHref(group) },
        { label: "Lecture notes" },
      ],
      action: state?.view ? { label: edition ? "Upload new notes" : "Upload notes", icon: "plus", run: () => openUpload() } : null,
    });
  }

  /**
   * Mount for a course group (the course page calls this after drawing the host).
   * @param {any} group the course group ({id, definition_id, code}) @param {string} [termLabel] @param {boolean} [force]
   */
  async function render(group, termLabel = "", force = false) {
    const node = host();
    if (!node || !group) return;
    if (!group.definition_id) {
      header?.set({ route: location.hash, title: "Lecture notes", context: group.code || "", subtitle: "", action: null });
      node.innerHTML = emptyMarkup({ icon: "book", title: "This course has no course code yet", body: "Notes belong to a course code. Add one in the course settings first." });
      return;
    }
    const same = state?.definitionId === group.definition_id;
    if (!same) state = { groupId: group.id, definitionId: group.definition_id, code: group.code, termLabel, view: null, showing: null, query: "" };
    state.groupId = group.id;
    state.termLabel = termLabel;
    state.route = location.hash.split("?")[0];
    const owner = state;
    setHeader(group, termLabel);
    if (same && state.view && !force && node.firstElementChild && node.dataset.notesFor === group.definition_id) return;
    const run = ++generation;
    if (!state.view) node.innerHTML = loadingMarkup("Opening the notes…", 4);
    try {
      const view = await api(`/api/course-notes/definitions/${encodeURIComponent(group.definition_id)}`);
      if (run !== generation || !active(owner) || host() !== node) return;
      state.view = view;
      node.dataset.notesFor = group.definition_id;
      setHeader(group, termLabel);
      draw();
    } catch (error) {
      if (run !== generation || !active(owner) || host() !== node) return;
      node.innerHTML = `<div class="empty-state">${icon("info")}<h3>The notes could not be opened</h3><p role="alert">${e(error?.message || "Something went wrong.")}</p><button type="button" class="button" data-notes-retry>Try again</button></div>`;
      const retry = /** @type {HTMLButtonElement|null} */ (node.querySelector("[data-notes-retry]"));
      if (retry) retry.onclick = () => { void render(group, termLabel, true); };
    }
  }

  function topicLink(part, topic) {
    const page = topic.page - part.first_page + 1;
    return `<li><a ${viewLink(part.url, [part.number, part.title].filter(Boolean).join(" "), e, page)}>${e(topic.title)}</a></li>`;
  }

  function partRow(part) {
    const name = [part.number, part.title].filter(Boolean).join(" ");
    const words = [part.number, part.title, part.chapter_title, ...part.topics.map((topic) => topic.title)].join(" ").toLocaleLowerCase();
    const found = part.found_by === "heading" ? '<span class="chip" data-tone="neutral" title="The PDF has no bookmark for this section; it was found from the heading that opens its page.">From its heading</span>' : "";
    return `<li class="notes-section" data-search="${e(words)}">
      <div class="notes-section-line"><a class="notes-section-link" ${viewLink(part.url, name, e)} title="Open ${e(name)} (PDF)">${part.number ? `<span class="notes-number">${e(part.number)}</span>` : ""}<span class="notes-title">${e(part.title)}</span></a>
        <span class="notes-pages" title="Pages ${e(part.first_page)}–${e(part.last_page)} of the whole notes">${e(plural(part.pages, "page"))}</span>${found}
        <a class="icon-button notes-download" href="${e(part.url)}?download=1" download aria-label="Download ${e(name)}" title="Download">${icon("download")}</a></div>
      ${part.topics.length ? `<ul class="notes-topics" aria-label="Topics of ${e(name)}">${part.topics.map((topic) => topicLink(part, topic)).join("")}</ul>` : ""}
    </li>`;
  }

  /** Parts grouped by chapter, in page order: the front matter, then Chapter 1, 2 … */
  function chapters(edition) {
    const groups = [];
    for (const part of edition.parts) {
      const key = part.kind === "front" ? "front" : `chapter:${part.chapter}`;
      let group = groups.find((item) => item.key === key);
      if (!group) {
        group = { key, title: part.kind === "front" ? part.title : `Chapter ${part.chapter}`, subtitle: part.kind === "front" ? "" : part.chapter_title, parts: [] };
        groups.push(group);
      }
      group.parts.push(part);
    }
    return groups;
  }

  function editionLine(edition, { earlier = false } = {}) {
    const facts = [edition.source_name, plural(edition.pages, "page"), plural(edition.sections, "section"), `uploaded ${day(edition.uploaded_at)}`].filter(Boolean).join(" · ");
    const menu = earlier ? "" : actionMenuMarkup({ label: "More actions for these notes", items: [
      { label: "Download the whole notes", href: `${edition.original_url}?download=1` },
      { label: "Remove from the course page…", danger: true, data: { notesRemove: edition.id } },
    ] });
    return `<div class="notes-edition"><div class="notes-edition-text"><strong>${e(edition.title)}</strong><small>${e(facts)}</small></div>
      <div class="notes-edition-actions"><a class="button" ${viewLink(edition.original_url, edition.title || edition.source_name, e)}>${icon("paper")}Whole notes (PDF)</a>${menu}</div></div>`;
  }

  function otherEditions(list, kind) {
    if (!list.length) return "";
    const rows = list.map((edition) => `<li class="notes-other"><div class="notes-edition-text"><strong>${e(edition.title)}</strong><small>${e([edition.source_name, plural(edition.sections, "section"), `uploaded ${day(edition.uploaded_at)}`].join(" · "))}</small></div>
      <div class="notes-edition-actions"><button type="button" class="text-button" data-notes-show="${e(edition.id)}">Show its sections</button><a class="text-button" ${viewLink(edition.original_url, edition.title || edition.source_name, e)}>Whole notes (PDF)</a>${kind === "removed" ? `<button type="button" class="button" data-notes-restore="${e(edition.id)}">Restore</button>` : ""}</div></li>`).join("");
    return disclosureMarkup({ label: kind === "removed" ? "Removed uploads" : "Earlier uploads", hint: list.length, key: `course-notes:${kind}`, body: `<ul class="notes-others">${rows}</ul>` });
  }

  function draw() {
    const node = host();
    if (!node || !state?.view || !active(state)) return;
    const view = state.view;
    const edition = shown();
    if (!edition) {
      node.innerHTML = `<div class="notes-page">${emptyMarkup({ icon: "book", title: "No lecture notes yet", body: "Upload the notes as one PDF: each section is found and saved as its own PDF here.", action: { label: "Upload notes", primary: true, data: { notesUpload: "" } } })}${otherEditions(view.removed, "removed")}</div>`;
      bind(node);
      return;
    }
    const looking = edition.id !== view.current?.id;
    const banner = looking ? calloutMarkup({ tone: "info", title: edition.removed ? "A removed upload" : "An earlier upload", body: `You are looking at ${edition.source_name}, uploaded ${day(edition.uploaded_at)}.`, action: { label: view.current ? "Back to the current notes" : "Close it", data: { notesShow: view.current?.id || "" } } }) : "";
    const notes = edition.notes.length ? disclosureMarkup({ label: "How the sections were found", hint: edition.notes.length, key: "course-notes:found", body: `<p>Each section was found from the PDF’s bookmarks and the heading that opens its page; where they did not agree, it says so here.</p><ul class="notes-found">${edition.notes.map((line) => `<li>${e(line)}</li>`).join("")}</ul>` }) : "";
    // The front matter is one row of its own; each chapter is headed by its number and the title printed above its first section.
    const list = chapters(edition).map((group) => `<section class="notes-chapter" aria-label="${e([group.title, group.subtitle].filter(Boolean).join(" · "))}">${group.key === "front" ? "" : `<h3>${e(group.title)}${group.subtitle ? `<span class="notes-chapter-title">${e(group.subtitle)}</span>` : ""}</h3>`}<ul class="notes-sections">${group.parts.map(partRow).join("")}</ul></section>`).join("");
    node.innerHTML = `<div class="notes-page">${banner}${editionLine(edition, { earlier: looking })}${notes}
      <label class="field notes-search">Find a section or topic<input type="search" data-page-search data-notes-search placeholder="Number, title or topic" autocomplete="off" value="${e(state.query)}"></label>
      <div class="notes-chapters" data-notes-list>${list}</div>
      <div data-notes-none hidden>${emptyMarkup({ kind: "filtered", title: "No section or topic matches", body: "Clear the search to see every section.", action: { label: "Clear search", data: { notesClear: "" } } })}</div>
      ${looking ? "" : otherEditions(view.earlier, "earlier")}${looking ? "" : otherEditions(view.removed, "removed")}</div>`;
    bind(node);
    filter();
  }

  /** Adopt the server's reply for its owner; a late reply for a course the instructor has left must not bump the live generation. */
  function adopt(owner, view) {
    owner.view = view;
    owner.showing = null;
    if (owner === state) generation++;
  }

  /** Redraw the owner's page and header, only while it is still the one on screen. */
  function redraw(owner) {
    if (!active(owner)) return;
    draw();
    setHeader({ id: owner.groupId, definition_id: owner.definitionId, code: owner.code }, owner.termLabel);
  }

  function filter() {
    const node = host();
    if (!node) return;
    const words = state.query.toLocaleLowerCase().split(/\s+/).filter(Boolean);
    let visible = 0;
    node.querySelectorAll(".notes-chapter").forEach((chapter) => {
      let any = false;
      chapter.querySelectorAll(".notes-section").forEach((row) => {
        const text = /** @type {HTMLElement} */ (row).dataset.search || "";
        const match = words.every((word) => text.includes(word));
        /** @type {HTMLElement} */ (row).hidden = !match;
        if (match) { any = true; visible += 1; }
      });
      /** @type {HTMLElement} */ (chapter).hidden = !any;
    });
    const none = /** @type {HTMLElement|null} */ (node.querySelector("[data-notes-none]"));
    if (none) none.hidden = visible > 0;
  }

  function bind(node) {
    bindActionMenu(node);
    const search = /** @type {HTMLInputElement|null} */ (node.querySelector("[data-notes-search]"));
    if (search) search.oninput = () => { state.query = search.value; filter(); };
    node.onclick = (event) => {
      const target = /** @type {HTMLElement} */ (event.target);
      const button = /** @type {HTMLElement|null} */ (target.closest("[data-notes-upload],[data-notes-show],[data-notes-restore],[data-notes-remove],[data-notes-clear]"));
      if (!button) return;
      if (button.hasAttribute("data-notes-upload")) openUpload();
      else if (button.hasAttribute("data-notes-show")) { state.showing = button.dataset.notesShow || null; draw(); window.scrollTo({ top: 0 }); }
      else if (button.hasAttribute("data-notes-restore")) void restore(button.dataset.notesRestore || "");
      else if (button.hasAttribute("data-notes-remove")) confirmRemove(button.dataset.notesRemove || "");
      else if (button.hasAttribute("data-notes-clear")) {
        state.query = "";
        if (search) search.value = "";
        filter();
        search?.focus();
      }
    };
  }

  function fail(form, message) {
    const error = /** @type {HTMLElement|null} */ (form.querySelector(".form-error"));
    if (!error) return;
    error.textContent = message;
    error.hidden = !message;
  }

  function openUpload() {
    if (!state?.view) return;
    const owner = state;
    const requestId = newId("notes");
    const current = state.view.current;
    showDialog(`<form id="course-notes-upload" novalidate>${dialogHeader(current ? "Upload new notes" : "Upload lecture notes", e([state.code, "every term"].filter(Boolean).join(" · ")))}
      <div class="dialog-body">
        <p>Choose the notes as one PDF. Each section is found from the PDF’s bookmarks and the headings that open its pages, and saved as its own PDF on this page.</p>
        <label class="field">Notes PDF<input type="file" name="file" accept="application/pdf,.pdf" required><span class="field-hint">Up to 100 MB. From Word, choose File → Save As → PDF.</span></label>
        ${current ? `<p class="field-hint">The new notes replace ${e(current.source_name)} on this page; it stays under Earlier uploads.</p>` : ""}
        <p class="form-error" role="alert" hidden></p>
      </div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Upload and split</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (dialog().querySelector("#course-notes-upload"));
    const currentDialog = dialogSession(form, dialog());
    const input = /** @type {HTMLInputElement} */ (form.elements.namedItem("file"));
    form.onsubmit = async (event) => {
      event.preventDefault();
      if (form.dataset.saving === "true") return;
      const file = input.files?.[0];
      if (!file) { fail(form, "Choose the notes PDF."); return; }
      if (file.size > owner.view.max_bytes) { fail(form, "That file is larger than 100 MB."); return; }
      const data = new FormData();
      data.append("request_id", requestId);
      data.append("file", file, file.name);
      const buttons = [...form.querySelectorAll("button")];
      const submit = /** @type {HTMLButtonElement} */ (form.querySelector('button[type="submit"]'));
      fail(form, "");
      form.dataset.saving = "true";
      buttons.forEach((button) => { button.disabled = true; });
      submit.textContent = "Splitting the notes…";
      try {
        const view = await api(`/api/course-notes/definitions/${encodeURIComponent(owner.definitionId)}`, "POST", data, {}, LONG);
        adopt(owner, view);
        if (currentDialog()) dialog().close();
        const edition = view.current;
        toast(view.uploaded?.again || view.uploaded?.repeated
          ? "These notes were uploaded before; they are the ones shown again."
          : `Saved ${edition ? plural(edition.sections, "section") : "the notes"}, each as its own PDF.`);
        redraw(owner);
      } catch (cause) {
        if (form.isConnected) fail(form, /** @type {any} */ (cause).message); else toast(/** @type {any} */ (cause).message, true);
      } finally {
        form.dataset.saving = "false";
        buttons.forEach((button) => { button.disabled = false; });
        submit.textContent = "Upload and split";
      }
    };
  }

  function confirmRemove(notesId) {
    const owner = state;
    const edition = [state.view.current, ...state.view.earlier].find((item) => item?.id === notesId);
    if (!edition) return;
    showDialog(`<form id="course-notes-remove" novalidate>${dialogHeader("Remove these notes?", e(edition.source_name))}
      <div class="dialog-body"><p>${e(edition.title)} leaves the course page. The PDF and its sections are kept, and Restore under Removed uploads brings them back.</p>
      ${state.view.earlier.length ? `<p class="field-hint">The page then shows the upload before it.</p>` : ""}<p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button danger">Remove</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (dialog().querySelector("#course-notes-remove"));
    const currentDialog = dialogSession(form, dialog());
    const requestId = newId("notes-remove");
    form.onsubmit = async (event) => {
      event.preventDefault();
      if (form.dataset.saving === "true") return;
      form.dataset.saving = "true";
      const buttons = [...form.querySelectorAll("button")];
      buttons.forEach((button) => { button.disabled = true; });
      try {
        adopt(owner, await api(`/api/course-notes/${encodeURIComponent(notesId)}/remove`, "POST", { request_id: requestId, confirmation: "remove" }));
        if (currentDialog()) dialog().close();
        toast("The notes were removed from the course page.", { action: { label: "Restore", run: () => { void restore(notesId, owner); } } });
        redraw(owner);
      } catch (cause) {
        fail(form, /** @type {any} */ (cause).message);
      } finally {
        form.dataset.saving = "false";
        buttons.forEach((button) => { button.disabled = false; });
      }
    };
  }

  async function restore(notesId, owner = state) {
    try {
      adopt(owner, await api(`/api/course-notes/${encodeURIComponent(notesId)}/restore`, "POST", { request_id: newId("notes-restore") }));
      toast("The notes are back on the course page.");
      redraw(owner);
    } catch (cause) {
      toast(/** @type {any} */ (cause).message, true);
    }
  }

  /**
   * The course Home's Lecture notes line: how many sections the shown notes have, or that none are uploaded.
   * @param {any} group @param {HTMLElement} node
   */
  async function summary(group, node) {
    if (!group?.definition_id || !node) return;
    try {
      const view = await api(`/api/course-notes/definitions/${encodeURIComponent(group.definition_id)}?summary=1`);
      if (!node.isConnected) return;
      node.textContent = view.current ? `${plural(view.current.sections, "section")} · uploaded ${day(view.current.uploaded_at)}` : "Not uploaded yet";
    } catch {
      if (node.isConnected) node.textContent = "";
    }
  }

  return { render, summary, openUpload };
}
