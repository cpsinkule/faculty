// @ts-check
// Student notes (student_notes/, AGENTS.md "Student notes"): your own notes about a student in a term, such as "scheduled one-on-one
// office hours today and didn't show", each with the day it is about. A student's card (the roster's student window, the gradebook's
// record) lists them, newest day first, and adds, edits and deletes them in place (a delete offers Undo in the card itself: the
// window is modal, so a toast's button outside it cannot be clicked); the palette's "Write a note about a student" picks the student
// first. The card's editor is not a <form>: the gradebook's record already is one, and a form
// cannot hold another. Nothing here sends anything, and no address ever carries a student.
import { captureDialogIntent, dialogSession, errorMessage, localToday, runFormAction, wallDate } from "./ui.js";
import { studentPicker } from "./student-picker.js";

const BASE = "/api/student-notes";
const PLACEHOLDER = "e.g. Scheduled one-on-one office hours today and didn't show.";

/** "Thu, Oct 1" this year, "Thu, Oct 1, 2025" another year. @param {string} date @param {string} [today] */
export function noteDate(date, today = localToday()) {
  return wallDate(date, { year: String(date).slice(0, 4) !== today.slice(0, 4) });
}

/**
 * @param {{api: Function, escape: (value: unknown) => string, toast: Function, showDialog: (markup: string) => void,
 *   dialogHeader: (title: string, subtitle?: string) => string}} deps
 */
export function createStudentNotes({ api, escape, toast, showDialog, dialogHeader }) {
  const e = (/** @type {unknown} */ value) => escape(String(value ?? ""));
  const dialog = () => /** @type {HTMLDialogElement} */ (document.getElementById("dialog"));
  // What is typed in a card's editor and not saved, by `<enrollment id>:<"new" or the note's id>`: Escape, Close or another window
  // opened from the card (Communications…, Record an absence…) replaces the card, and the words used to be lost. Kept in this tab's
  // memory only (never stored or sent, gone after a reload); Save and Cancel drop it, and the card's editor opens with it again.
  /** @type {Map<string, {date: string, text: string}>} */
  const drafts = new Map();

  /** The editor for a new note (`note` null) or one being changed, filled from an unsaved draft when there is one.
   * @param {any} note @param {{date: string, text: string} | null} [draft] */
  function editorMarkup(note, draft = null) {
    const id = note ? note.id : "new";
    return `<div class="student-note-editor" role="group" aria-label="${note ? "Edit the note" : "New note"}" data-note-editor="${e(id)}">
      <label class="field">Date<input type="date" name="note_date" value="${e(draft ? draft.date : note ? note.date : localToday())}" required></label>
      <label class="field">Note<textarea name="note_text" rows="3" maxlength="5000" required placeholder="${e(PLACEHOLDER)}">${e(draft ? draft.text : note ? note.text : "")}</textarea></label>
      <p class="form-error" role="alert" hidden></p>${draft ? '<p class="field-hint" data-note-kept>Your unsaved note was kept.</p>' : ""}
      <div class="student-note-editor-actions"><button type="button" class="button" data-note-cancel>Cancel</button>
        <button type="button" class="button primary" data-note-save>${note ? "Save changes" : "Save note"}</button></div></div>`;
  }

  /** @param {any} note @param {string} today */
  function noteMarkup(note, today) {
    return `<li class="student-note" data-note="${e(note.id)}"><div class="student-note-head"><span class="student-note-date">${e(noteDate(note.date, today))}</span>${note.edited ? '<small class="student-note-edited">edited</small>' : ""}
      <span class="student-note-actions"><button type="button" class="text-button" data-note-edit aria-label="${e(`Edit the note of ${noteDate(note.date, today)}`)}">Edit</button><button type="button" class="text-button danger" data-note-delete aria-label="${e(`Delete the note of ${noteDate(note.date, today)}`)}">Delete</button></span></div>
      <p class="student-note-text">${e(note.text)}</p></li>`;
  }

  /**
   * The Notes part of a student's card: their notes in this term, Add a note…, and each note's Edit and Delete. Reads once when the
   * card opens; each write answers with the student's notes, drawn again in place.
   * @param {HTMLElement} host @param {{enrollmentId: string, name?: string}} who
   */
  async function panel(host, who) {
    const route = `${BASE}/students/${encodeURIComponent(who.enrollmentId)}`;
    let view = /** @type {any} */ (null), editing = "", requestId = "";
    let undo = /** @type {{id: string, revision: number, date: string} | null} */ (null);  // the note just deleted
    const key = (/** @type {string} */ id) => `${who.enrollmentId}:${id}`;
    host.classList.add("student-notes");
    host.innerHTML = '<p class="field-hint">Reading notes…</p>';

    function draw(focus = "") {
      if (!host.isConnected || !view) return;
      const today = localToday();
      const notes = view.notes || [];
      const draft = editing ? drafts.get(key(editing)) || null : null;
      host.innerHTML = `${undo ? `<p class="student-note-undo" role="status">${e(`Note of ${noteDate(undo.date, today)} deleted.`)} <button type="button" class="text-button" data-note-undo>Undo</button></p>` : ""}
        ${editing === "new" ? editorMarkup(null, draft) : '<button type="button" class="button" data-note-new>Add a note…</button>'}
        ${notes.length ? `<ul class="student-note-list">${notes.map((/** @type {any} */ note) => note.id === editing ? `<li class="student-note">${editorMarkup(note, draft)}</li>` : noteMarkup(note, today)).join("")}</ul>`
          : editing === "new" ? "" : '<p class="field-hint">No notes yet. Students never see them.</p>'}`;
      const target = focus ? host.querySelector(focus) : null;
      if (target instanceof HTMLElement) target.focus();
    }

    /** @param {string} next @param {string} [focus] */
    function edit(next, focus = "") {
      editing = next;
      requestId = "";
      undo = null;
      draw(focus || (next ? "[name=note_text]" : "[data-note-new]"));
    }

    /** @param {HTMLElement} editor */
    async function save(editor) {
      await runFormAction(/** @type {any} */ (editor), async () => {
        const date = /** @type {HTMLInputElement} */ (editor.querySelector("[name=note_date]")).value;
        const text = /** @type {HTMLTextAreaElement} */ (editor.querySelector("[name=note_text]")).value;
        if (!date) throw new Error("Give the note's date.");
        if (!text.trim()) throw new Error("Write the note.");
        requestId ||= crypto.randomUUID();
        const which = editing;  // another note's Edit may be clicked while this save is on its way
        const note = (view.notes || []).find((/** @type {any} */ item) => item.id === which);
        view = which === "new"
          ? await api(BASE, "POST", { enrollment_id: who.enrollmentId, date, text, request_id: requestId })
          : await api(`${BASE}/${encodeURIComponent(which)}`, "POST", { base_revision: note?.revision, date, text, request_id: requestId });
        drafts.delete(key(which));
        toast(which === "new" ? "Note saved" : "Note changed");
        if (editing === which) edit("");
      });
    }

    /** @param {string} noteId */
    async function remove(noteId) {
      const note = (view.notes || []).find((/** @type {any} */ item) => item.id === noteId);
      if (!note) return;
      try {
        const answer = await api(`${BASE}/${encodeURIComponent(noteId)}/remove`, "POST", { base_revision: note.revision, request_id: crypto.randomUUID() });
        view = answer;
        drafts.delete(key(noteId));
        if (editing === noteId) editing = "";
        undo = { id: noteId, revision: answer.note_revision, date: note.date };
        if (!editing) draw("[data-note-undo]");  // an open editor keeps what is being typed
      } catch (cause) { toast(errorMessage(cause), true); }
    }

    async function restore() {
      if (!undo) return;
      const { id, revision } = undo;
      try {
        view = await api(`${BASE}/${encodeURIComponent(id)}/restore`, "POST", { base_revision: revision, request_id: crypto.randomUUID() });
        undo = null;
        toast("Note restored");
        if (!editing) draw("[data-note-new]");
      } catch (cause) { toast(errorMessage(cause), true); }
    }

    host.onclick = (event) => {
      const target = /** @type {HTMLElement} */ (event.target);
      if (target.closest("[data-note-undo]")) void restore();
      else if (target.closest("[data-note-new]")) edit("new");
      else if (target.closest("[data-note-cancel]")) { drafts.delete(key(editing)); edit(""); }
      else if (target.closest("[data-note-save]")) void save(/** @type {HTMLElement} */ (target.closest("[data-note-editor]")));
      else if (target.closest("[data-note-edit]")) edit(target.closest("[data-note]")?.getAttribute("data-note") || "");
      else if (target.closest("[data-note-delete]")) void remove(target.closest("[data-note]")?.getAttribute("data-note") || "");
    };
    host.oninput = (event) => {
      requestId = "";  // changed words are a new request; a retry of the same words reuses its ID
      // What is typed outlives the card (Escape, Close, another window opened from it) until it is saved or cancelled.
      const editor = /** @type {HTMLElement} */ (event.target).closest?.("[data-note-editor]");
      if (!(editor instanceof HTMLElement) || !editing) return;
      const date = /** @type {HTMLInputElement} */ (editor.querySelector("[name=note_date]")).value;
      const text = /** @type {HTMLTextAreaElement} */ (editor.querySelector("[name=note_text]")).value;
      // Nothing to keep when a new note's words were all removed, or an edit was put back to the saved note.
      const note = (view?.notes || []).find((/** @type {any} */ item) => item.id === editing);
      const unchanged = editing === "new" ? !text.trim() : Boolean(note) && note.text === text && note.date === date;
      if (unchanged) drafts.delete(key(editing));
      else drafts.set(key(editing), { date, text });
    };
    host.onkeydown = (event) => {
      const editor = /** @type {HTMLElement} */ (event.target).closest?.("[data-note-editor]");
      if (!(editor instanceof HTMLElement)) return;
      // Ctrl/⌘ Enter saves; Enter in the date box must not submit the gradebook record's own form.
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey || /** @type {HTMLElement} */ (event.target).matches("input"))) {
        event.preventDefault();
        void save(editor);
      }
    };

    try {
      view = await api(route);
      // A note typed here before and never saved opens again in its editor: a new one first, else an edit of a note that stands.
      if (drafts.has(key("new"))) editing = "new";
      else editing = (view.notes || []).find((/** @type {any} */ note) => drafts.has(key(note.id)))?.id || "";
      draw();
    } catch (cause) {
      if (host.isConnected) host.innerHTML = `<p class="field-hint">${e(errorMessage(cause))}</p>`;
    }
  }

  /** The palette's note form, also prefilled for an appointment; always reviewed before saving.
   * @param {{enrollmentId?: string, date?: string, text?: string}} [prefill] */
  async function openNew(prefill = {}) {
    const intended = captureDialogIntent(dialog());
    let groups = [];
    try { groups = (await api(`${BASE}/students`)).groups || []; } catch (cause) { toast(errorMessage(cause), true); return; }
    if (!intended()) return;
    const picker = studentPicker(escape, {
      id: "student-note-student", name: "enrollment_id", label: "Student", value: prefill.enrollmentId || "",
      groups: groups.map((/** @type {any} */ group) => ({ label: `${group.code} · ${group.term_label}`,
        options: group.students.map((/** @type {any} */ student) => ({ value: student.enrollment_id,
          text: `${student.name} · section ${student.sections.join(", ")}`, note: student.dropped ? "dropped" : "" })) })),
      search: async (digits) => {
        const found = await api(`${BASE}/students/search`, "POST", { digits });
        return (found.students || []).map((/** @type {any} */ s) => ({ value: s.enrollment_id, text: `${s.name} · section ${s.section_number}`, note: `${Math.round(s.similarity * 100)}% match` }));
      },
    });
    showDialog(`<form class="student-note-form" id="student-note-form" novalidate>${dialogHeader("Write a note about a student", "Students never see your notes.")}
      <div class="dialog-body"><label class="field">Student${picker.markup}</label>
        ${groups.length ? "" : '<p class="field-hint">No current course has students on its roster yet.</p>'}
        <label class="field">Date<input type="date" name="date" value="${e(prefill.date || localToday())}" required></label>
        <label class="field">Note<textarea name="text" rows="4" maxlength="5000" required placeholder="${e(PLACEHOLDER)}">${e(prefill.text || "")}</textarea></label>
        <p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Save note</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (document.getElementById("student-note-form"));
    const session = dialogSession(form, dialog());
    picker.bind(form);
    let requestId = "";
    form.addEventListener("input", () => { requestId = ""; });
    form.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); form.requestSubmit(); }
    });
    form.onsubmit = (event) => {
      event.preventDefault();
      void runFormAction(form, async () => {
        const fields = /** @type {any} */ (form.elements);
        if (!fields.enrollment_id.value) throw new Error("Choose the student.");
        if (!fields.date.value) throw new Error("Give the note's date.");
        if (!fields.text.value.trim()) throw new Error("Write the note.");
        requestId ||= crypto.randomUUID();
        await api(BASE, "POST", { enrollment_id: fields.enrollment_id.value, date: fields.date.value, text: fields.text.value, request_id: requestId });
        toast("Note saved");
        if (session()) dialog().close();
      });
    };
    /** @type {HTMLElement | null} */ (form.querySelector("[data-picker-text]"))?.focus();
  }

  return { panel, openNew };
}
