/**
 * "From the schedule" (SCHEDULING "CLASS SCHEDULE", pre-fill): a form about to create work (a quiz approval, a new assignment, a
 * gradebook column) offers the planned rows of the term's class schedule that no record follows yet. Choosing one fills the form's
 * date while that field is empty or still holds a planned row's date; a date the instructor typed is never replaced. The form
 * reads back which row was chosen, so the row can follow the new record. With no open rows the form is exactly as it was.
 */
import { dateFor, matchRow } from "./class-schedule-model.js";
import { wallDate } from "./ui.js";

const GIVEN = ["quiz", "exam", "final"];
const base = (/** @type {string} */ termId) => `/api/scheduling/terms/${encodeURIComponent(termId)}/class-schedule`;

/**
 * The term's open planned rows a form may offer: work given on a day (`purpose` "given") or anything with a due date ("due").
 * [] when there are none or they cannot be read. @param {any} api @param {string} termId @param {"given"|"due"} [purpose]
 */
export async function openRows(api, termId, purpose = "given") {
  if (!termId) return [];
  try {
    const found = await api(`${base(termId)}/open`);
    return (found.items || []).filter((/** @type {any} */ row) => (purpose === "given" ? GIVEN.includes(row.kind) : row.kind !== "other"));
  } catch { return []; }
}

/** A planned row's date for a form: the day it is given, or the day it is due. @param {any} row @param {string[]} sections @param {"given"|"due"} purpose */
export function plannedDate(row, sections, purpose) {
  if (purpose === "given" || row.kind === "homework") return dateFor(row, sections, "date");
  return dateFor(row, sections, "due_date") || dateFor(row, sections, "date");
}

/**
 * Records that a planned row follows the record a form just made. A failure never undoes that record: the row stays planned, and
 * the schedule page offers the link again. @param {any} api @param {string} termId @param {string} item
 * @param {{assignment_id?: string, gradebook_item?: string}} target @returns {Promise<boolean>}
 */
export async function followRecord(api, termId, item, target) {
  if (!termId || !item) return false;
  try {
    await api(`${base(termId)}/links`, "POST", { request_id: crypto.randomUUID(), item, ...target });
    return true;
  } catch { return false; }
}

/**
 * Puts the control before `before` (the form's date field) and keeps the date in step with the chosen row.
 * @param {{rows: any[], before: Element|null, dateInput: HTMLInputElement|null, title?: () => string, sections?: () => string[],
 *   purpose?: "given"|"due", escape: (value: any) => string, chosen?: string}} spec
 * @returns {{chosen: () => string, refresh: () => void}}
 */
export function attachPrefill({ rows, before, dateInput, title = () => "", sections = () => [], purpose = "given", escape, chosen = "" }) {
  if (!rows.length || !before || !dateInput) return { chosen: () => "", refresh: () => {} };
  const label = document.createElement("label");
  label.className = "field";
  label.dataset.schedulePrefill = "";
  const describe = (/** @type {any} */ row) => {
    const day = plannedDate(row, sections(), purpose);
    const own = Object.keys(row.section_values || {}).length > 0;
    return `${row.title} · ${day ? wallDate(day) : own ? "sections differ" : "no date yet"}`;
  };
  label.innerHTML = `From the schedule<select name="schedule_item"><option value="">None</option>${rows.map((row) => `<option value="${escape(row.item)}">${escape(describe(row))}</option>`).join("")}</select>`;
  before.before(label);
  const select = /** @type {HTMLSelectElement} */ (label.querySelector("select"));
  let byHand = Boolean(chosen), filled = "";
  const apply = () => {
    const row = rows.find((entry) => entry.item === select.value);
    const day = row ? plannedDate(row, sections(), purpose) : "";
    // Only an empty field, or one still holding a date this control put there, is filled or cleared.
    if (dateInput.value !== "" && dateInput.value !== filled) return;
    if (dateInput.value === day) { filled = day; return; }
    dateInput.value = day;
    filled = day;
    dateInput.dispatchEvent(new Event("input", { bubbles: true }));
  };
  const refresh = () => {
    for (const option of select.options) {
      const row = rows.find((entry) => entry.item === option.value);
      if (row) option.textContent = describe(row);
    }
    if (!byHand) select.value = matchRow(title(), rows)?.item || "";
    apply();
  };
  select.addEventListener("change", (event) => { event.stopPropagation(); byHand = true; apply(); });
  select.addEventListener("input", (event) => event.stopPropagation());
  if (chosen && rows.some((row) => row.item === chosen)) select.value = chosen;
  refresh();
  return { chosen: () => select.value, refresh };
}
