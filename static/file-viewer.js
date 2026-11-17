// The in-app file viewer (AGENTS.md "Faculty assistant", VIEWER; requirement, 2026-10-01). A link marked data-view-file="<Faculty file
// route>" opens here: a PDF as its pages (pictures the server renders in the document sandbox, each loaded as it scrolls into view), a
// picture, a text, or for anything else its name with Download. Download is the route itself. A link may name the page to open at
// (data-view-page). A click with a modifier key (new tab, save) keeps the browser's own behaviour. A table the page built itself
// (the Response Card answers) opens with `viewTable`: nothing is fetched, and Download saves the page's own file. A PDF Faculty made (the server says
// `native`) opens in the browser's own PDF viewer instead (requirement, 2026-10-01): search, selection, zoom and printing are the browser's. Not on a phone or tablet, whose browsers show only the first page
// of a framed PDF, and never for a file from a student, which keeps the pictures.
import { errorMessage, loadingMarkup } from "./ui.js";

const BASE = "/api/viewer";
const TABLE_ROWS = 5000;  // as the server's (file_viewer/service.py)
/** @type {{openTable: (table: {name: string, rows: unknown[][], text: string, saveAs: string, type?: string}) => void} | null} */
let viewer = null;

/**
 * A table the page built itself, in the viewer: its rows shown (the first is the header), Download saving `text` as `saveAs`.
 * Without the viewer (a page outside the app), the file is saved as before.
 * @param {{name: string, rows: unknown[][], text: string, saveAs: string, type?: string}} table
 */
export function viewTable(table) {
  if (viewer) { viewer.openTable(table); return; }
  const url = URL.createObjectURL(new Blob([table.text], { type: table.type || "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url; link.download = table.saveAs; link.hidden = true;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
const KINDS = { pdf: "PDF", image: "Picture", text: "Text", table: "Table", email: "Email", file: "File" };

/** Only Faculty's own file routes are opened (the server checks them again). @param {unknown} route */
export const viewableRoute = (route) => typeof route === "string" && /^\/api\/[A-Za-z0-9/_.~%:=&?-]+$/.test(route) && !route.includes("..");

/** The browser shows PDFs itself (`navigator.pdfViewerEnabled`) and is not a touch-only device. */
export function browserShowsPdfs() {
  if (typeof navigator === "undefined" || navigator.pdfViewerEnabled !== true || typeof matchMedia !== "function") return false;
  return !(matchMedia("(pointer: coarse)").matches && !matchMedia("(any-pointer: fine)").matches);
}

/** "1.2 MB" for a byte count. @param {number} size */
function sizeText(size) {
  if (!(size > 0)) return "";
  return size < 1024 ? `${size} B` : size < 1048576 ? `${Math.round(size / 1024)} KB` : `${(size / 1048576).toFixed(1)} MB`;
}

/**
 * The attributes of a link that opens a file in the viewer (at `page`, for a PDF); its href stays the route, for a modifier-click.
 * @param {string} route @param {string} name @param {(value: unknown) => string} esc @param {number} [page]
 */
export function viewLink(route, name, esc, page = 0) {
  const at = Number.isInteger(page) && page > 1 ? page : 0;
  return `href="${esc(route)}${at ? `#page=${at}` : ""}" data-view-file="${esc(route)}" data-view-name="${esc(name)}"${at ? ` data-view-page="${at}"` : ""}`;
}

/**
 * What the viewer shows of a described file (GET /api/viewer/file).
 * @param {any} file @param {(value: unknown) => string} esc @param {{native?: boolean, page?: number}} [options] the browser shows
 * PDFs itself; the page to open at
 */
export function viewerBodyMarkup(file, esc, { native = false, page = 1 } = {}) {
  const query = (/** @type {Record<string, string>} */ extra) => esc(new URLSearchParams({ route: file.route, ...extra }).toString());
  if (file.kind === "pdf" && file.native && native) {
    const at = Number.isInteger(page) && page > 1 ? `#page=${page}` : "";
    return `<iframe class="file-viewer-frame" src="${BASE}/pdf?${query({ sha256: String(file.sha256 || "") })}${at}" title="${esc(file.name)}"></iframe>`;
  }
  if (file.kind === "image") return `<div class="file-viewer-picture"><img src="${BASE}/picture?${query({})}" alt="${esc(file.name)}"></div>`;
  if (file.kind === "pdf") {
    const [width, height] = Array.isArray(file.page_size) ? file.page_size : [1275, 1650];
    // The digest the file was opened with: pages already drawn come from memory without reading a long PDF again.
    return Array.from({ length: Number(file.pages) || 0 }, (_, index) => `<figure class="file-viewer-page" data-page="${index + 1}"><img src="${BASE}/page.png?${query({ page: String(index + 1), sha256: String(file.sha256 || "") })}" width="${Number(width)}" height="${Number(height)}" loading="${index < 2 ? "eager" : "lazy"}" alt="Page ${index + 1} of ${Number(file.pages)}"><figcaption>Page ${index + 1} of ${Number(file.pages)}</figcaption></figure>`).join("");
  }
  if (file.kind === "table") {
    // A CSV export: its first row is the header; every cell is text.
    const [head = [], ...rows] = Array.isArray(file.rows) ? file.rows : [];
    const cells = (/** @type {unknown[]} */ row, /** @type {string} */ tag) => row.map((cell) => `<${tag}>${esc(cell)}</${tag}>`).join("");
    return `<div class="file-viewer-table"><table><thead><tr>${cells(head, "th")}</tr></thead><tbody>${rows.map((/** @type {unknown[]} */ row) => `<tr>${cells(row, "td")}</tr>`).join("")}</tbody></table></div>${file.truncated ? '<p class="field-hint">Only the first rows and columns are shown: download the file for the rest.</p>' : ""}`;
  }
  if (file.kind === "email") {
    // An email as a mail program shows it: who, when, the subject, its text and its attachments (named; they are not opened here).
    const headers = (file.headers || []).map((/** @type {[string, string]} */ [name, value]) => `<dt>${esc(name)}</dt><dd>${esc(value)}</dd>`).join("");
    const files = (file.attachments || []).map((/** @type {any} */ item) => `<li>${esc(item.name)} <span class="field-hint">${esc([item.type, sizeText(item.size)].filter(Boolean).join(" · "))}</span></li>`).join("");
    return `<article class="file-viewer-email">${headers ? `<dl class="file-viewer-headers">${headers}</dl>` : ""}<pre class="file-viewer-text">${esc(file.text || "")}</pre>${file.truncated ? '<p class="field-hint">Only the beginning is shown: download the file for the rest.</p>' : ""}${files ? `<h3 class="file-viewer-subhead">Attachments</h3><ul class="file-viewer-attachments">${files}</ul>` : ""}</article>`;
  }
  if (file.kind === "text") {
    return `<pre class="file-viewer-text">${esc(file.text)}</pre>${file.truncated ? '<p class="field-hint">Only the beginning is shown: download the file for the rest.</p>' : ""}`;
  }
  return `<p class="file-viewer-none">${esc(file.note || "This kind of file has no preview here. Download it to open it.")}</p>`;
}

/**
 * The print file a route serves, when it is one Print at Home can send (DOCUMENTS "SEND TO PRINTER"; requirement, 2026-10-18): a job's PDF, a stack's interleaved PDF or a seating
 * chart's whole-room PDF. null for any other file. @param {string} route
 * @returns {{kind: "job"|"stack"|"room", batchIds: string[], chartId: string|null} | null}
 */
export function printableRoute(route) {
  try {
    const url = new URL(route, "http://faculty.local/");
    if (url.origin !== "http://faculty.local") return null;
    const ids = (url.searchParams.get("batches") || "").split(",").filter(Boolean);
    const job = url.pathname.match(/^\/api\/documents\/batches\/([^/]+)\/download$/);
    if (job && !url.search) return { kind: "job", batchIds: [decodeURIComponent(job[1])], chartId: null };
    if (url.pathname === "/api/documents/stack" && ids.length) return { kind: "stack", batchIds: ids, chartId: null };
    const room = url.pathname.match(/^\/api\/seating\/([^/]+)\/room-export\/print$/);
    if (room && ids.length) return { kind: "room", batchIds: ids, chartId: decodeURIComponent(room[1]) };
  } catch { return null; }
  return null;
}

/**
 * @param {{api: Function, escape: (value: unknown) => string, icon: (name: string) => string}} tools
 */
export function createFileViewer({ api, escape, icon }) {
  const dialog = document.createElement("dialog");
  dialog.className = "file-viewer";
  dialog.setAttribute("aria-labelledby", "file-viewer-title");
  document.body.append(dialog);
  let ticket = 0;
  let blobUrl = "";  // the Download of a table the page built, released when the viewer closes or shows something else
  let shownFile = /** @type {any} */ (null), initialPage = 1;
  let restoreSelection = /** @type {null | (() => void)} */ (null);
  const release = () => { if (blobUrl) URL.revokeObjectURL(blobUrl); blobUrl = ""; };
  // Print at Home for a print file (the Print page's window, given by the documents page once both exist).
  let printAtHome = /** @type {null | ((target: any, back: () => void) => Promise<void>)} */ (null);

  /** @param {string} route @param {string} name @param {string} detail @param {string} body @param {boolean} zoomable @param {string} [saveAs] */
  function frame(route, name, detail, body, zoomable, saveAs = name) {
    dialog.classList.remove("file-viewer-actual");
    dialog.innerHTML = `<header class="dialog-header file-viewer-head"><div><h2 id="file-viewer-title">${escape(name)}</h2>${detail ? `<p>${escape(detail)}</p>` : ""}</div>
      <div class="file-viewer-actions"><button type="button" class="icon-button" data-assistant-capture aria-label="Ask about an area" title="Ask about an area: click here, then drag a box over the pages (use this while the PDF has keyboard focus; elsewhere hold ⌘ and draw)">${icon("area")}</button>${zoomable ? '<button type="button" class="button" data-viewer-zoom aria-pressed="false">Actual size</button>' : ""}${printAtHome && printableRoute(route) ? '<button type="button" class="button" data-viewer-print-home>Print at Home</button>' : ""}<a class="button primary" href="${escape(route)}" download="${escape(saveAs)}">Download</a><button type="button" class="icon-button" data-viewer-close aria-label="Close the viewer">${icon("close")}</button></div></header>
      <div class="file-viewer-body">${body}</div>`;
  }

  /** @param {string} route @param {string} [name] @param {number} [page] the PDF page to open at */
  async function open(route, name = "", page = 1) {
    if (!viewableRoute(route)) return;
    restoreSelection?.();shownFile=null;initialPage=page;
    release();
    const mine = ++ticket;
    frame(route, name || "File", "", loadingMarkup("Opening the file…", 3), false);
    if (!dialog.open) dialog.showModal();
    try {
      const native = browserShowsPdfs();
      const file = await api(`${BASE}/file?${new URLSearchParams({ route, ...(native ? { native: "1" } : {}) })}`);
      if (mine !== ticket || !dialog.open) return;
      shownFile=file;
      const rows = file.kind === "table" && Array.isArray(file.rows) ? Math.max(0, file.rows.length - 1) : 0;
      const detail = [KINDS[/** @type {keyof typeof KINDS} */ (file.kind)] || "File", file.pages ? `${file.pages} page${file.pages === 1 ? "" : "s"}` : "",
        file.kind === "table" ? `${rows.toLocaleString()} row${rows === 1 ? "" : "s"}` : "", sizeText(file.size)].filter(Boolean).join(" · ");
      const own = file.kind === "pdf" && file.native && native;  // the browser's own PDF viewer: its zoom, not ours
      frame(route, name || file.name, detail, viewerBodyMarkup(file, escape, { native, page }), !own && (file.kind === "pdf" || file.kind === "image"), file.name);
      if (!own && file.kind === "pdf" && page > 1) dialog.querySelector(`[data-page="${Math.min(page, Number(file.pages) || 1)}"]`)?.scrollIntoView({ block: "start" });
    } catch (cause) {
      if (mine === ticket && dialog.open) frame(route, name || "File", "", `<p class="form-error">${escape(errorMessage(cause))}</p>`, false);
    }
  }

  // ASSISTANT: keep the native viewer mounted so its scroll, zoom and unsaved parent remain intact.
  async function prepareSelection() {
    const frame=dialog.querySelector('iframe.file-viewer-frame');
    if(!dialog.open || !(frame instanceof HTMLIFrameElement) || !shownFile)return null;
    restoreSelection?.();
    const mine=ticket,file=shownFile,body=dialog.querySelector('.file-viewer-body');
    if(!body)return null;
    const described=await api(`${BASE}/file?${new URLSearchParams({route:file.route})}`);
    if(mine!==ticket || !dialog.open)return null;
    if(described.sha256!==file.sha256)throw new Error('The PDF changed since it was opened. Open it again before selecting.');
    if(described.kind!=='pdf')throw new Error(described.note||'This PDF could not be prepared for selection.');
    const scroll={top:body.scrollTop,left:body.scrollLeft};
    const pages=document.createElement('div');pages.className='file-viewer-selection-pages';
    pages.innerHTML=viewerBodyMarkup(described,escape);
    frame.hidden=true;body.append(pages);
    let restored=false;
    const restore=()=>{if(restored)return;restored=true;pages.remove();frame.hidden=false;body.scrollTop=scroll.top;body.scrollLeft=scroll.left;if(restoreSelection===restore)restoreSelection=null;};
    restoreSelection=restore;
    const page=pages.querySelector(`[data-page="${Math.min(Math.max(1,initialPage),described.pages)}"]`);
    if(page)body.scrollTop+=page.getBoundingClientRect().top-body.getBoundingClientRect().top;
    return {root:pages,restore,scroll:(/** @type {number} */ top,/** @type {number} */ left)=>body.scrollBy({top,left,behavior:'instant'})};
  }

  dialog.addEventListener("click", (event) => {
    const target = /** @type {HTMLElement} */ (event.target);
    if (target === dialog || target.closest("[data-viewer-close]")) { dialog.close(); return; }  // the backdrop, or Close
    if (target.closest("[data-viewer-print-home]") && printAtHome) {
      const route = shownFile?.route || dialog.querySelector("a[download]")?.getAttribute("href") || "";
      const printable = printableRoute(route);
      if (!printable) return;
      const title = dialog.querySelector("#file-viewer-title")?.textContent || "Print file";
      dialog.close();
      // Back in the Print at Home window reopens this file here.
      void printAtHome({ ...printable, subtitle: title }, () => { void open(route, title); });
      return;
    }
    const zoom = /** @type {HTMLElement | null} */ (target.closest("[data-viewer-zoom]"));
    if (!zoom) return;
    const actual = zoom.getAttribute("aria-pressed") !== "true";
    zoom.setAttribute("aria-pressed", String(actual));
    zoom.textContent = actual ? "Fit to window" : "Actual size";
    dialog.classList.toggle("file-viewer-actual", actual);
  });
  /** @param {{name: string, rows: unknown[][], text: string, saveAs: string, type?: string}} table */
  function openTable({ name, rows, text, saveAs, type = "text/csv;charset=utf-8" }) {
    restoreSelection?.();shownFile=null;
    ticket += 1;
    release();
    blobUrl = URL.createObjectURL(new Blob([text], { type }));
    const shown = rows.slice(0, TABLE_ROWS + 1).map((row) => row.map((cell) => (cell === null || cell === undefined ? "" : String(cell))));
    const count = Math.max(0, rows.length - 1);
    const detail = ["Table", `${count.toLocaleString()} row${count === 1 ? "" : "s"}`, sizeText(new Blob([text]).size)].filter(Boolean).join(" · ");
    frame(blobUrl, name, detail, viewerBodyMarkup({ kind: "table", rows: shown, truncated: rows.length > TABLE_ROWS + 1 }, escape), false, saveAs);
    if (!dialog.open) dialog.showModal();
  }

  // Closing stops what is still loading: the pages go with the content.
  dialog.addEventListener("close", () => { ticket += 1; restoreSelection?.();shownFile=null;dialog.innerHTML = ""; release(); });
  document.addEventListener("click", (event) => {
    const link = /** @type {HTMLAnchorElement | null} */ (/** @type {HTMLElement} */ (event.target)?.closest?.("a[data-view-file]"));
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    // A route the viewer refuses keeps the browser's own link instead of a click that does nothing.
    if (!viewableRoute(link.dataset.viewFile)) return;
    event.preventDefault();
    void open(link.dataset.viewFile || "", link.dataset.viewName || "", Number(link.dataset.viewPage) || 1);
  });
  viewer = { openTable };
  /** @param {(target: any, back: () => void) => Promise<void>} handler */
  const setPrintAtHome = (handler) => { printAtHome = handler; };
  return { open, openTable, prepareSelection, setPrintAtHome };
}
