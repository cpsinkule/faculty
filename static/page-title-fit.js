// @ts-check
/* The page title on the header band's one line (DESIGN-LANGUAGE §3, §5): a long title steps down from 28 px toward 22 px,
   then wraps to two lines at most (.page-plate[data-title-wrap="true"], header-band.css). It measures only the title's own
   box, and it sets the size through CSSOM (--page-title-size), which the CSP allows. Kept from the retired plates.js, whose
   photographs and arc are gone (2026-10-04). */

/** The title's smallest and largest size, in px. */
export const TITLE_SIZE = [22, 28];

/**
 * Fits the title now. `title` is #page-title and `band` the .page-plate around it.
 * @param {HTMLElement} title @param {HTMLElement} band
 */
export function fitTitle(title, band) {
  const [min, max] = TITLE_SIZE;
  const room = title.clientWidth || band.getBoundingClientRect().width - 80;
  let size = max;
  title.style.setProperty("--page-title-size", `${size}px`);
  band.dataset.titleWrap = "false";
  while (title.scrollWidth > room + 1 && size > min) {
    size = Math.max(min, Math.floor(size * 0.92));
    title.style.setProperty("--page-title-size", `${size}px`);
  }
  if (title.scrollWidth > room + 1) band.dataset.titleWrap = "true";
}

/**
 * Keeps the title fitted as the band resizes, the title changes and the fonts arrive. `fit()` is also called after every
 * header paint; it does nothing when the band's width, the title and the needs-you chip beside it (whose width the title's
 * room depends on) are unchanged, so polls measure nothing.
 * @param {{doc?: Document}} [options]
 */
export function mountTitleFit({ doc = globalThis.document } = {}) {
  const band = /** @type {HTMLElement | null} */ (doc.querySelector(".page-plate"));
  const title = /** @type {HTMLElement | null} */ (band?.querySelector("#page-title") || null);
  const status = /** @type {HTMLElement | null} */ (band?.querySelector("#page-status") || null);
  let fitted = "";
  const fit = (force = false) => {
    if (!band || !title) return;
    const chip = status && !status.hidden ? status.querySelector(".page-subtitle-lead")?.textContent || "" : "";
    const key = `${Math.round(band.getBoundingClientRect().width)}|${title.textContent}|${chip}`;
    if (!force && key === fitted) return;
    fitted = key;
    fitTitle(title, band);
  };
  if (band && typeof ResizeObserver === "function") new ResizeObserver(() => fit()).observe(band);
  if (title && typeof MutationObserver === "function") {
    new MutationObserver(() => fit()).observe(title, { childList: true, characterData: true, subtree: true });
  }
  void doc.fonts?.ready?.then(() => fit(true));
  return { fit };
}
