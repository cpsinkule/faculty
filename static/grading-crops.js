// The split desk uses an inline reader; other crops and Full screen keep the review form intact in a native dialog.
export const cropButton = (url, label, h) => `<button type="button" class="grading-crop-open" data-crop-zoom="${h.e(url)}" aria-label="Enlarge ${h.e(label)}"><img loading="lazy" src="${h.e(url)}" alt="${h.e(label)}"><span class="grading-crop-hint" aria-hidden="true">Enlarge ↗</span></button>`;

// GRADING: a browser-session viewing preference, never a change to an image or its evidence coordinates.
let inlineScale = null;
function inlineReader(button, frame, img) {
  const reader = document.createElement('div');
  reader.className = 'grading-inline-reader';
  reader.innerHTML = '<div class="grading-inline-tools" role="group" aria-label="Image zoom"><button type="button" class="text-button" data-fit>Fit</button><button type="button" class="text-button" data-actual>100%</button><button type="button" class="icon-button" aria-label="Zoom out" data-out>−</button><output aria-live="polite"></output><button type="button" class="icon-button" aria-label="Zoom in" data-in>+</button><button type="button" class="text-button" data-full>Full screen</button></div><div class="grading-inline-viewport" tabindex="0" aria-label="Response image; drag or scroll to pan"></div>';
  frame.before(reader);
  const viewport = reader.querySelector('.grading-inline-viewport');
  viewport.append(frame);
  const fitScale = () => img.naturalWidth ? 100 * viewport.clientWidth / img.naturalWidth : 100;
  const set = (scale, remember = true) => {
    const value = scale === null ? null : Math.max(10, Math.min(400, scale));
    if (remember) inlineScale = value;
    frame.style.width = value === null ? '100%' : `${img.naturalWidth * value / 100}px`;
    frame.style.maxWidth = 'none';
    reader.querySelector('output').textContent = value === null ? 'Fit width' : `${Math.round(value)}%`;
  };
  reader.querySelector('[data-fit]').onclick = () => set(null);
  reader.querySelector('[data-actual]').onclick = () => set(100);
  reader.querySelector('[data-in]').onclick = () => set((inlineScale ?? fitScale()) * 1.25);
  reader.querySelector('[data-out]').onclick = () => set((inlineScale ?? fitScale()) / 1.25);
  reader.querySelector('[data-full]').onclick = () => openCrop(button);
  let drag = null, moved = false;
  viewport.onpointerdown = event => {
    if (event.button !== 0) return;
    drag = {x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop}; moved = false;
  };
  viewport.onpointermove = event => {
    if (!drag || !event.buttons) return;
    const x = event.clientX - drag.x, y = event.clientY - drag.y;
    if (Math.abs(x) + Math.abs(y) < 4) return;
    moved = true; event.preventDefault();
    viewport.scrollLeft = drag.left - x; viewport.scrollTop = drag.top - y;
  };
  viewport.onpointerup = viewport.onpointerleave = () => { drag = null; };
  // A click pops the crop out to full size (requirement, 2026-10-17); the
  // reader's own buttons zoom in place, and a drag only scrolls.
  button.onclick = () => { if (!moved) openCrop(button); moved = false; };
  button.ondragstart = event => event.preventDefault();
  img.addEventListener('load', () => set(inlineScale, false), {once: true});
  if (img.complete) set(inlineScale, false);
}

// A crop that failed to load is asked for again, a few times, by itself (requirement, 2026-10-17). The address is
// the same with a retry number added, so the browser does not answer from its memory of the failure.
const RETRY_AFTER = [1000, 3000, 8000];
function retryWhenBroken(img) {
  if (!img || img.dataset.cropRetry !== undefined) return;
  img.dataset.cropRetry = "0";
  const again = () => {
    const tries = Number(img.dataset.cropRetry);
    if (tries >= RETRY_AFTER.length) return;
    img.dataset.cropRetry = String(tries + 1);
    setTimeout(() => {
      if (!img.isConnected) return;
      const url = new URL(img.getAttribute("src"), location.href);
      url.searchParams.set("retry", String(tries + 1));
      img.src = `${url.pathname}${url.search}`;
    }, RETRY_AFTER[tries]);
  };
  img.addEventListener("error", again);
  if (img.complete && img.getAttribute("src") && !img.naturalWidth) again();
}

export function mountCropZoom(root) {
  root.querySelectorAll("[data-crop-zoom]").forEach(button => {
    if (button.closest('.grading-inline-reader')) return;
    button.onclick = () => openCrop(button);
    const img = button.querySelector("img"), frame = button.closest(".grading-image");
    retryWhenBroken(img);
    if (frame?.closest('.grading-review-desk')) { inlineReader(button, frame, img); return; }
    // Keep long regions compact without distorting the image or evidence boxes.
    const size = () => {
      if (frame && img.naturalHeight) frame.style.maxWidth = `${320 * img.naturalWidth / img.naturalHeight}px`;
    };
    img.addEventListener("load", size, {once: true});
    if (img.complete) size();
  });
}

function openCrop(trigger) {
  document.querySelector(".grading-crop-dialog")?.close();
  const dialog = document.createElement("dialog");
  dialog.className = "grading-crop-dialog";
  dialog.setAttribute("aria-label", "Enlarged response crop");
  dialog.innerHTML = `<header><strong></strong><button type="button" class="button" data-close autofocus>Close</button></header><div class="grading-crop-toolbar"><button type="button" class="button" data-fit>Fit width</button><button type="button" class="button" data-actual>Actual pixels</button><button type="button" class="button" data-out aria-label="Zoom out">−</button><label>Zoom <input type="range" min="5" max="400" step="5" value="100" aria-label="Crop zoom"></label><button type="button" class="button" data-in aria-label="Zoom in">+</button><output aria-live="polite"></output></div><div class="grading-crop-viewport" tabindex="0" aria-label="Enlarged crop; scroll to see the whole image"></div>`;
  const img = document.createElement('img'), viewport = dialog.querySelector(".grading-crop-viewport"), slider = dialog.querySelector("input");
  img.alt = trigger.querySelector("img").alt;
  dialog.querySelector("header strong").textContent = img.alt;
  let zoom = 100;
  const resize = value => {
    zoom = Math.max(5, Math.min(400, value));
    slider.value = String(zoom);
    img.style.width = `${img.naturalWidth * zoom / 100}px`;
    dialog.querySelector("output").textContent = `${Math.round(zoom)}%`;
  };
  const fit = () => resize(Math.min(100, 100 * (viewport.clientWidth - 24) / img.naturalWidth));
  img.onload = fit;
  img.src = trigger.dataset.cropZoom;
  // GRADING FLAG REVIEW "THE SPOT TO LOOK AT": a crop's outlines come along to its full-size view, placed as they are on the crop.
  const spots = [...(trigger.closest(".grading-image")?.querySelectorAll(".grading-flag-spot") || [])];
  if (spots.length) {
    const frame = document.createElement("div");
    frame.className = "grading-crop-frame";
    frame.append(img, ...spots.map(spot => spot.cloneNode(true)));
    viewport.append(frame);
  } else viewport.append(img);
  dialog.querySelector("[data-close]").onclick = () => dialog.close();
  dialog.querySelector("[data-fit]").onclick = fit;
  dialog.querySelector("[data-actual]").onclick = () => resize(100);
  dialog.querySelector("[data-out]").onclick = () => resize(zoom - 25);
  dialog.querySelector("[data-in]").onclick = () => resize(zoom + 25);
  slider.oninput = () => resize(Number(slider.value));
  const close = () => dialog.close();
  const parentDialog = trigger.closest("dialog");
  parentDialog?.addEventListener("close", close);
  window.addEventListener("hashchange", close);
  dialog.addEventListener("close", () => {
    window.removeEventListener("hashchange", close);
    parentDialog?.removeEventListener("close", close);
    dialog.remove();
    if (trigger.isConnected) trigger.focus({preventScroll: true});
  }, {once: true});
  document.body.append(dialog);
  dialog.showModal();
}
