// @ts-check
/**
 * ATTACH IN CHAT (ASSISTANT; requirement, 2026-10-09). One paperclip for the Assistant drawer and the floating chat: each chosen file is uploaded at once (POST
 * /api/assistant/uploads, one file per request), listed under the message with Remove, and goes with the next message as its ID.
 * Files chosen together upload side by side, each showing how much of it has gone (requirement, 2026-10-20).
 * The server keeps a file a day (assistant/uploads.py); the Assistant looks at it with fetch_files and attaches it to an email or a
 * Canvas message by its route. Nothing is sent anywhere until the instructor approves a card.
 */
import { errorMessage } from "./ui.js";
import { viewLink } from "./file-viewer.js";
import { esc } from "./assistant-markdown.js";
import { uploadFileWithProgress } from "./request.js";

export const MAX_FILES = 10;          // assistant/uploads.py MAX_FILES: files one message carries
export const MAX_FILE = 25_000_000;   // assistant/uploads.py MAX_FILE: bytes one file may hold
export const MAX_EMAIL = 25_000_000;  // gmail_mail/attachments.py MAX_TOTAL: bytes of files one email carries

/** "240 KB", "1.4 MB". @param {number} n */
export const sizeText = (n) => n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`;

/** "Attaching…", then "Attaching… 4.1 of 12.8 MB" once bytes are going. Pure. @param {{size: number, sent?: number}} file */
export const progressText = (file) => file.sent ? `Attaching… ${sizeText(Math.min(file.sent, file.size))} of ${sizeText(file.size)}` : "Attaching…";

/** A slow link may take minutes for a large file (about half a megabyte a second from campus, 2026-10-20). */
const UPLOAD_TIMEOUT = 15 * 60 * 1000;

/**
 * One file to /api/assistant/uploads with its byte progress: the same POST and header as api() (uploadFileWithProgress, request.js).
 * Never retried here: attaching again is the instructor's choice.
 * @param {File} file @param {(sent: number) => void} onProgress @returns {Promise<any>}
 */
export function uploadFile(file, onProgress) {
  return uploadFileWithProgress("/api/assistant/uploads", file, {
    timeout: UPLOAD_TIMEOUT,
    timeoutMessage: "The upload took too long. Attach it again.",
    unreadableMessage: "The server response could not be read. Attach it again.",
    onProgress: (loaded, total) => onProgress(Math.round(loaded / total * file.size)),
  });
}

/** The paperclip: a button-styled label over a hidden file input. @param {(name: string) => string} icon */
export const attachButton = (icon) => `<label class="icon-button assistant-attach" title="Attach files">${icon("attach")}<span class="sr-only">Attach files</span><input type="file" multiple class="sr-only" data-assistant-attach-input></label>`;

/** TOO LARGE FOR AN EMAIL: what a waiting file's size says once it is more than one email carries. Pure. @param {number} size */
export const emailNote = (size) => size > MAX_EMAIL ? ` · too large to email (${MAX_EMAIL / 1e6} MB at most)` : "";

/**
 * The files waiting to go with the next message: each named and sized, with Remove; a file still uploading says so. One too large
 * for an email says so here, and so do files too large together (requirement, 2026-11-12). Pure.
 * @param {{name: string, size: number, uploading?: boolean, sent?: number}[]} files
 */
export function attachedMarkup(files) {
  if (!files.length) return "";
  const together = !files.some((file) => file.size > MAX_EMAIL) && files.reduce((sum, file) => sum + file.size, 0) > MAX_EMAIL
    ? `<li><small>Together these are too large for one email (${MAX_EMAIL / 1e6} MB at most).</small></li>` : "";
  return `<ul class="assistant-attached" aria-label="Files to send with your message">${files.map((file, index) => `<li><span class="assistant-attached-name">${esc(file.name)}</span><small data-assistant-progress="${index}">${esc(file.uploading ? progressText(file) : sizeText(file.size) + emailNote(file.size))}</small>${file.uploading ? "" : `<button type="button" class="text-button" data-assistant-detach="${index}" aria-label="Remove ${esc(file.name)}">Remove</button>`}</li>`).join("")}${together}</ul>`;
}

/** The files a sent message carried, as `filesMarkup` draws files (pictures inline, others as links). @param {any[]} files */
export const sentFiles = (files) => (files || []).map((file) => ({ url: file.route, open: file.route, name: file.name, type: file.type, size: file.size,
  kind: String(file.type || "").startsWith("image/") ? "image" : file.type === "application/pdf" ? "pdf" : "file" }));

/** Bytes in words ("1.2 MB"). @param {number} size */
export function bytes(size) {
  if (!(size > 0)) return "";
  return size < 1024 ? `${size} B` : size < 1024 ** 2 ? `${Math.round(size / 1024)} KB` : `${(size / 1024 ** 2).toFixed(1)} MB`;
}

/** Only Faculty's own file routes are ever drawn: the path Faculty fetched and checked (assistant/files.py). @param {unknown} url */
export const safeRoute = (url) => typeof url === "string" && /^\/api\/[A-Za-z0-9/_.~%:=&?-]+$/.test(url) && !url.includes("..");
/** Browsers other than Safari cannot draw a HEIC photo (an iPhone's): its thumbnail is the file viewer's re-encoded picture. @param {any} file */
const thumbnail = (file) => file.type === "image/heic" ? `/api/viewer/picture?route=${encodeURIComponent(file.url)}` : file.url;

/**
 * What `fetch_files` showed the instructor, or the files a sent message carried: pictures inline (each opens full size), anything
 * else as a link to the file.
 * @param {any[]} files
 */
export function filesMarkup(files) {
  const shown = (files || []).filter((file) => safeRoute(file?.url) && safeRoute(file?.open || file.url));
  if (!shown.length) return "";
  const caption = String(shown[0].caption ?? "");
  // Each opens in the app's file viewer (static/file-viewer.js), with Download there; a modifier-click keeps the browser's own way.
  const pictures = shown.filter((file) => file.kind === "image").map((file) => `<figure class="assistant-shown-picture"><a ${viewLink(file.open || file.url, file.name, esc)} title="Open in the viewer"><img src="${esc(thumbnail(file))}" alt="${esc(caption || file.name)}" loading="lazy"></a><figcaption>${esc(file.name)}</figcaption></figure>`).join("");
  const others = shown.filter((file) => file.kind !== "image").map((file) => `<li><a class="link-arrow" ${viewLink(file.url, file.name, esc)}>${esc(file.name)}</a> <span class="assistant-shown-size">${esc([file.kind === "pdf" ? "PDF" : String(file.type ?? "").split("/").pop()?.toUpperCase(), bytes(file.size)].filter(Boolean).join(" · "))}</span></li>`).join("");
  return `<div class="assistant-shown">${caption ? `<p class="assistant-shown-caption">${esc(caption)}</p>` : ""}${pictures ? `<div class="assistant-shown-pictures">${pictures}</div>` : ""}${others ? `<ul class="assistant-shown-files">${others}</ul>` : ""}</div>`;
}

/**
 * The paperclip's state for one composer: `host` shows the waiting files, `changed` redraws the composer (Send stays off while one
 * uploads; `upload` sends one file, `uploadFile` unless a check stands in for it).
 * @param {{toast: (m: string, e?: boolean) => void, host: HTMLElement, changed?: () => void,
 *   upload?: (file: File, onProgress: (sent: number) => void) => Promise<any>}} deps
 */
export function createAttachments({ toast, host, changed = () => {}, upload = uploadFile }) {
  /** @type {{name: string, size: number, id?: string, route?: string, type?: string, uploading?: boolean, sent?: number}[]} */
  let files = [];
  const draw = () => { host.innerHTML = attachedMarkup(files); host.hidden = !files.length; changed(); };
  host.addEventListener("click", (event) => {
    const button = /** @type {HTMLElement} */ (event.target).closest("[data-assistant-detach]");
    if (!button) return;
    files.splice(Number(button.getAttribute("data-assistant-detach")), 1);
    draw();
  });
  /** Its progress line alone is rewritten while bytes go, not the composer. @param {typeof files[number]} entry */
  const progress = (entry) => {
    const line = host.querySelector(`[data-assistant-progress="${files.indexOf(entry)}"]`);
    if (line) line.textContent = progressText(entry);
  };
  /** Uploads the chosen files side by side; each is listed at once and ready when its own upload ends. @param {FileList | File[]} chosen */
  async function add(chosen) {
    const started = [];
    for (const file of [...chosen]) {
      if (files.length >= MAX_FILES) { toast(`A message can carry at most ${MAX_FILES} files.`, true); break; }
      if (file.size > MAX_FILE) { toast(`${file.name} is larger than ${MAX_FILE / 1e6} MB.`, true); continue; }
      const entry = { name: file.name, size: file.size, uploading: true, sent: 0 };
      files.push(entry);
      started.push([file, entry]);
    }
    if (!started.length) return;
    draw();
    await Promise.all(started.map(async ([file, entry]) => {
      try {
        const answer = await upload(file, (sent) => { entry.sent = sent; progress(entry); });
        Object.assign(entry, answer.files[0], { uploading: false });
      } catch (cause) {
        files = files.filter((other) => other !== entry);
        toast(`${file.name} was not attached: ${errorMessage(cause)}`, true);
      }
      draw();
    }));
  }
  return {
    add,
    /** The IDs to send, or null while a file is still uploading. */
    ids: () => files.some((file) => file.uploading) ? null : files.map((file) => /** @type {string} */ (file.id)),
    count: () => files.length,
    busy: () => files.some((file) => file.uploading),
    clear: () => { files = []; draw(); },
    /** Puts back what a send that failed carried. @param {typeof files} kept */
    restore: (kept) => { files = kept; draw(); },
    snapshot: () => files.slice(),
  };
}
