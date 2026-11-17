// ASSISTANT: ephemeral navigation hints. No grades, draft text or form values are copied.
import { leaveMessage } from "./ui.js";

let selected = null;
let installed = false;

/** @param {string} address */
export function routeContext(address) {
  const [path, search = ""] = address.split("?");
  const parts = path.split("/");
  const query = new URLSearchParams(search);
  const record = {};
  if (parts[0] === "#assignments" && parts[2]) Object.assign(record, {assignment_id: parts[1], term_id: parts[2]});
  if (["#grading", "#teaching-review", "#grade-delivery"].includes(parts[0]) && parts[1]) record.plan_id = parts[1];
  if (parts[0] === "#exams" && parts[1]) record.event_id = parts[1];
  if (parts[0] === "#inbox" && parts[1] === "job") record.job_id = parts[2];
  if (parts[0] === "#gradebook" && parts[1]) record.term_id = parts[1];
  for (const [name, key] of [["question", "question_id"], ["response", "response_id"], ["item", "item_id"], ["assignment", "assignment_id"], ["section", "section_id"]]) {
    if (query.get(name)) record[key] = query.get(name);
  }
  return record;
}

export function installContextCapture() {
  if (installed) return;
  installed = true;
  const capture = (event) => {
    const element = event.target instanceof Element ? event.target : null;
    if (!element || element.closest("#assistant-panel")) return;
    const cell = element.closest("[data-cell][data-row], [data-student]");
    if (cell) selected = {address: location.hash, element: cell};
  };
  document.addEventListener("focusin", capture);
  document.addEventListener("pointerdown", capture);
  window.addEventListener("hashchange", () => { selected = null; });
}

export function recordContext() {
  const record = routeContext(location.hash);
  // Only a review on screen: the Follow-up section keeps its last card while hidden on other pages (2026-10-09 review).
  const support = [...document.querySelectorAll(".support-review[data-assistant-enrollment]")].find((node) => !node.closest("[hidden]"));
  if (support) Object.assign(record, {enrollment_id: support.getAttribute("data-assistant-enrollment"),
    source_key: support.getAttribute("data-assistant-source")});
  if (selected?.address === location.hash && selected.element.isConnected) {
    const cell = selected.element, table = cell.closest("[data-assistant-term]");
    const row = cell.getAttribute("data-row") || cell.getAttribute("data-student");
    if (table && row) {
      record.term_id = table.getAttribute("data-assistant-term");
      record.row = row;
      const split = row.indexOf(":");
      if (split > 0) Object.assign(record, {section_id: row.slice(0, split), enrollment_id: row.slice(split + 1)});
      if (cell.getAttribute("data-item")) record.item_id = cell.getAttribute("data-item");
    }
  }
  return {record, unsaved: Boolean(leaveMessage())};
}

/** Record hints for the content actually inside a selection, independent of the last focused cell. */
export function selectedRecord(element) {
  const record = routeContext(location.hash);
  const support = element.closest('.support-review[data-assistant-enrollment]');
  if (support) Object.assign(record, {enrollment_id: support.dataset.assistantEnrollment, source_key: support.dataset.assistantSource});
  const cell = element.closest('[data-cell][data-row], [data-student]');
  const table = cell?.closest('[data-assistant-term]');
  if (table) {
    const row = cell.getAttribute('data-row') || cell.getAttribute('data-student');
    Object.assign(record, {term_id: table.dataset.assistantTerm, row});
    const split = row.indexOf(':');
    if (split > 0) Object.assign(record, {section_id: row.slice(0, split), enrollment_id: row.slice(split + 1)});
    if (cell.dataset.item) record.item_id = cell.dataset.item;
  }
  return record;
}
