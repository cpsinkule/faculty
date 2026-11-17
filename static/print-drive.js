// The print artifact remains section-owned on the server. Only the chosen
// directory handle is remembered here, in this browser/origin's IndexedDB.
const DATABASE = "faculty-print-destination";

function preference(operation, handle) {
  return new Promise((resolve, reject) => {
    const opening = indexedDB.open(DATABASE, 1);
    opening.onupgradeneeded = () => opening.result.createObjectStore("preferences");
    opening.onerror = () => reject(opening.error);
    opening.onblocked = () => reject(new Error("Browser storage is unavailable."));
    opening.onsuccess = () => {
      const db = opening.result;
      db.onversionchange = () => db.close();
      try {
        const transaction = db.transaction("preferences", operation === "get" ? "readonly" : "readwrite");
        const records = transaction.objectStore("preferences");
        const request = operation === "get" ? records.get("directory") : operation === "put" ? records.put(handle, "directory") : records.delete("directory");
        transaction.oncomplete = () => { db.close(); resolve(request.result); };
        transaction.onabort = transaction.onerror = () => { db.close(); reject(transaction.error); };
      } catch (error) { db.close(); reject(error); }
    };
  });
}

async function digest(bytes) {
  return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), value => value.toString(16).padStart(2, "0")).join("");
}

async function matches(file, size, expected) {
  return file.size === size && await digest(await file.arrayBuffer()) === expected;
}

async function saveVerified(directory, batch, bytes, progress) {
  const baseName = `print-${batch.id}.pdf`;
  let filename = baseName;
  try {
    const existing = await directory.getFileHandle(filename);
    if (await matches(await existing.getFile(), bytes.byteLength, batch.output_sha256)) {
      return {filename, existing: true};
    }
    // Preserve an unrelated or damaged file; never overwrite it automatically.
    filename = `print-${batch.id}-${crypto.randomUUID()}.pdf`;
  } catch (error) {
    if (error.name !== "NotFoundError") throw error;
  }
  let file = await directory.getFileHandle(filename, {create: true});
  // Recheck after creation in case another export finished concurrently.
  const current = await file.getFile();
  if (current.size) {
    if (await matches(current, bytes.byteLength, batch.output_sha256)) return {filename, existing: true};
    filename = `print-${batch.id}-${crypto.randomUUID()}.pdf`;
    file = await directory.getFileHandle(filename, {create: true});
  }
  return writeVerifiedFile(file, batch, bytes, progress);
}

async function writeVerifiedFile(file, batch, bytes, progress) {
  const current = await file.getFile();
  if (await matches(current, bytes.byteLength, batch.output_sha256)) return {filename: file.name, existing: true};
  if (current.size) throw new Error("A different file already uses that name. Choose a new filename to preserve it.");
  let writable;
  try {
    writable = await file.createWritable({mode: "exclusive"});
    await writable.write(bytes);
    await writable.close();
    writable = null;
    progress("Verifying saved PDF…");
    if (!await matches(await file.getFile(), bytes.byteLength, batch.output_sha256)) {
      throw new Error("The saved PDF could not be verified. Reconnect the drive and export again.");
    }
    return {filename: file.name, existing: false};
  } catch (error) {
    if (writable) await writable.abort().catch(() => {});
    throw error;
  }
}

function explain(error, stage, canSaveAs) {
  const fallback = canSaveAs ? "Try Save PDF as… to select a file on your USB drive, or download the PDF and move it there in Finder." : "Download the PDF and move it to your USB drive in Finder.";
  if (error.name === "AbortError" && stage === "folder-picker") return `The browser did not grant access to that folder. The picker may have been closed or folder access blocked. Try a folder inside the USB drive. ${fallback}`;
  if (error.name === "AbortError" && stage === "file-picker") return "No PDF destination was granted. The save dialog may have been closed or file access blocked. Try again, or download the PDF and move it to the USB drive in Finder.";
  if (error.name === "AbortError") return "The export was interrupted. No completed export was confirmed. Try again, or download the PDF and move it to your USB drive.";
  if (["NotAllowedError", "SecurityError"].includes(error.name)) return `File or folder access was not granted. Allow access when prompted. ${fallback}`;
  if (["NotFoundError", "InvalidStateError", "NotReadableError"].includes(error.name)) return "The selected drive or folder is unavailable. Connect the USB drive, then choose its folder again.";
  if (["QuotaExceededError", "NoModificationAllowedError", "UnknownError"].includes(error.name)) return "The PDF could not be saved. Check that the drive has free space, is writable, and is connected, then try again.";
  if (error.name === "TimeoutError" || error instanceof TypeError) return "The export could not finish. Check your connection and USB drive, then try again.";
  return error.message || "The export could not be completed. Try again.";
}

export function createPrintDriveExporter({escape, showDialog, dialogHeader}) {
  let directory = null;
  let remembered = true;
  const restored = preference("get").then(handle => { directory = handle || null; }).catch(() => { remembered = false; });

  return async function exportToDrive(batch, back) {
    const supported = window.isSecureContext && typeof window.showDirectoryPicker === "function" && !!crypto.subtle;
    const canSaveAs = window.isSecureContext && typeof window.showSaveFilePicker === "function" && !!crypto.subtle;
    const download = `/api/documents/batches/${encodeURIComponent(batch.id)}/download`;
    // One sentence, one primary action (the remembered folder, else choosing one); how it works is folded away. Every
    // verification, permission and preserve-existing rule below is unchanged.
    showDialog(`${dialogHeader("Export to external drive", `${escape(batch.title)} · ${escape(batch.version_label)}`)}<div class="dialog-body" id="document-drive-export">
      <p class="section-lead"><strong>Print PDF</strong> · ${batch.quantity * batch.page_count} pages · ${escape(batch.code)} · ${escape(batch.term === "Other" ? batch.term_other : batch.term)} ${batch.year} · ${batch.section_id ? `Section ${batch.section_number}` : "One stack for the term"}</p>
      ${supported ? "" : `<p>This browser cannot save directly to a chosen folder. ${canSaveAs ? "Use Save PDF as… to choose a PDF destination on your USB drive." : "Download the PDF and save or move it to your USB drive."}</p>`}
      <p id="document-drive-destination"></p>
      <p role="alert" class="form-error"></p><p role="status" id="document-drive-status">${supported ? "Loading saved destination…" : "No file has been exported to a drive."}</p>
      <div class="inbox-job-actions document-drive-actions">
        ${supported ? '<button class="button primary" id="document-drive-choose" disabled>Choose USB folder & export</button><button class="button" id="document-drive-save" hidden>Export to this folder</button>' : ""}
        ${canSaveAs ? '<button class="button" id="document-drive-save-as" disabled>Save PDF as…</button>' : ""}
        <a class="text-button" href="${download}" download>Download PDF instead</a>${supported ? '<button class="text-button" id="document-drive-forget" hidden>Forget folder</button>' : ""}<button class="button" id="document-drive-back">Back to print job</button>
      </div>
      <details class="disclosure document-drive-help"><summary><span>How this works</span></summary><div class="disclosure-body">
        ${supported ? "<p>Choose a folder on the USB drive connected to this laptop. Later exports use that folder while the browser keeps its access.</p>" : ""}
        <p class="field-hint" id="document-drive-memory"></p>
        ${canSaveAs ? "<p>Save PDF as… saves this one export to a file you choose, without access to a whole folder.</p>" : ""}
        <p>The PDF is checked against the print job before it is written and again after the file is closed; a different file with the same name is kept.</p>
      </div></details></div>`);
    const host = document.querySelector("#document-drive-export");
    const alive = () => host.isConnected && host.closest("dialog").open;
    host.querySelector("#document-drive-back").onclick = back;
    if (!supported && !canSaveAs) return;
    const choose = host.querySelector("#document-drive-choose");
    const save = host.querySelector("#document-drive-save");
    const forget = host.querySelector("#document-drive-forget");
    const saveAs = host.querySelector("#document-drive-save-as");
    const status = host.querySelector("#document-drive-status");
    const errorBox = host.querySelector('[role="alert"]');
    let busy = false;
    let complete = false;
    let needsPermission = false;
    let stage = "export";
    let target = "folder";
    function controls() {
      if (supported) {
        choose.disabled = busy;
        choose.textContent = directory ? "Choose another folder & export" : "Choose USB folder & export";
        // The one primary: exporting to the remembered folder, else choosing one.
        choose.classList.toggle("primary", !directory);
        save.classList.toggle("primary", Boolean(directory));
        save.hidden = forget.hidden = !directory;
        save.disabled = busy || complete;
        save.textContent = busy && target === "folder" ? "Exporting…" : complete ? "Export verified" : needsPermission ? "Allow access & export" : "Export to this folder";
        save.setAttribute("aria-busy", String(busy && target === "folder"));
        forget.disabled = busy;
        host.querySelector("#document-drive-destination").textContent = directory ? `Destination: ${directory.name}` : "No USB folder selected.";
        host.querySelector("#document-drive-memory").textContent = remembered ? "The selected folder is remembered only in this browser." : "This browser can remember the folder only until this page is reloaded.";
      }
      if (saveAs) {
        saveAs.disabled = busy;
        saveAs.textContent = busy && target === "file" ? "Saving PDF…" : "Save PDF as…";
        saveAs.setAttribute("aria-busy", String(busy && target === "file"));
      }
    }
    const progress = text => { if (alive()) status.textContent = text; };
    async function write(file = null) {
      if (!alive()) return;
      if (batch.status !== "ready" || batch.superseded_at || !/^[a-f0-9]{64}$/.test(batch.output_sha256)) throw new Error("Reopen a ready print job before exporting.");
      stage = "download"; progress("Downloading print PDF…");
      const response = await fetch(download, {credentials: "same-origin", cache: "no-store", redirect: "error", signal: AbortSignal.timeout(120000)});
      if (!response.ok) {
        const details = await response.json().catch(() => ({}));
        throw new Error(details.error || "The print PDF is unavailable. Reopen the print job and try again.");
      }
      const bytes = await response.arrayBuffer();
      if (!response.headers.get("Content-Type")?.includes("application/pdf") || new TextDecoder().decode(bytes.slice(0, 5)) !== "%PDF-" || await digest(bytes) !== batch.output_sha256) {
        throw new Error("The downloaded PDF failed verification. Nothing was written to the drive; reopen the print job and try again.");
      }
      if (!alive()) return;
      stage = "write"; progress(`Saving to ${file ? file.name : directory.name}…`);
      const result = file ? await writeVerifiedFile(file, batch, bytes, progress) : await saveVerified(directory, batch, bytes, progress);
      complete = !file;
      progress(`${result.existing ? "Already saved and verified" : "Exported and verified"}: ${file ? result.filename : `${directory.name} / ${result.filename}`}. ${file ? "If you saved to the USB drive, you can now eject it using your laptop's eject control." : "You can now safely eject the drive using your laptop's eject control."}`);
    }
    async function perform(action, destination = "folder") {
      if (busy || !alive()) return;
      busy = true; complete = false; stage = "export"; target = destination; errorBox.textContent = ""; controls();
      try { await action(); }
      catch (error) {
        if (alive()) { errorBox.textContent = explain(error, stage, canSaveAs); status.textContent = "Export not completed."; }
      } finally { busy = false; controls(); }
    }
    if (choose) choose.onclick = () => perform(async () => {
      // Must open the native picker directly from this click, before fetching.
      stage = "folder-picker"; progress("Choose a folder on your USB drive…");
      const selected = await window.showDirectoryPicker({id: "faculty-print-drive", mode: "readwrite"});
      if (!alive()) return;
      directory = selected; needsPermission = false;
      try { await preference("put", directory); remembered = true; } catch { remembered = false; }
      controls();
      await write();
    });
    if (save) save.onclick = () => perform(async () => {
      // Permission requests also require a fresh user gesture.
      stage = "permission";
      const permission = await directory.requestPermission({mode: "readwrite"});
      if (permission !== "granted") throw new DOMException("Folder access denied", "NotAllowedError");
      needsPermission = false;
      await write();
    });
    if (saveAs) saveAs.onclick = () => perform(async () => {
      // A new click is required; never launch this picker automatically after
      // a folder-picker rejection. Do not change the remembered directory.
      stage = "file-picker"; progress("Choose where to save the PDF…");
      const file = await window.showSaveFilePicker({
        id: "faculty-print-file", suggestedName: `print-${batch.id}.pdf`,
        types: [{description: "PDF document", accept: {"application/pdf": [".pdf"]}}],
        excludeAcceptAllOption: true,
      });
      await write(file);
    }, "file");
    if (forget) forget.onclick = () => perform(async () => {
      // Remove only the preference, never anything from the user's drive.
      await preference("delete"); directory = null; needsPermission = false;
      progress("Folder forgotten. No files were changed.");
    });
    await restored;
    if (!alive()) return;
    controls();
    if (!supported) { progress("Choose Save PDF as… to export the PDF."); return; }
    if (!directory) { progress("Choose your USB folder to export the PDF."); return; }
    try {
      needsPermission = await directory.queryPermission({mode: "readwrite"}) !== "granted";
      if (!alive()) return;
      controls();
      if (needsPermission) progress("Allow browser access to export to the remembered folder.");
      else await perform(write);
    } catch (error) { if (alive()) { errorBox.textContent = explain(error, "permission", canSaveAs); progress("Choose your USB folder again."); } }
  };
}
