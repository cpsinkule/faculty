/* Browser-local wall clock; independent of server polling and teaching dates. Ledger (requirement, 2026-10-04): "Sun, Oct 4 · 2:31 PM", no year, no seconds and no time-zone name, so nothing ticks in the corner of the eye; it
   moves once a minute, on the minute. */
const date = document.querySelector("#workspace-date");
const clock = document.querySelector("#workspace-time");
const headingDate = document.querySelector("#today");
const dateFormat = new Intl.DateTimeFormat(undefined, {
  weekday: "short", month: "short", day: "numeric",
});
const timeFormat = new Intl.DateTimeFormat(undefined, {
  hour: "numeric", minute: "2-digit",
});
const headingFormat = new Intl.DateTimeFormat(undefined, {
  weekday: "long", month: "long", day: "numeric",
});
let timer;

function update() {
  clearTimeout(timer);
  if (document.hidden || !date || !clock) return;
  const now = new Date();
  const localDay = [now.getFullYear(), String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0")].join("-");
  // Only touch the date nodes when the browser's local day changes.
  if (date.dateTime !== localDay) {
    date.dateTime = localDay;
    date.textContent = dateFormat.format(now);
    if (headingDate) headingDate.textContent = headingFormat.format(now).toUpperCase();
  }
  clock.dateTime = now.toISOString();
  clock.textContent = timeFormat.format(now);
  timer = setTimeout(update, 60000 - (now.getSeconds() * 1000 + now.getMilliseconds()));
}

document.addEventListener("visibilitychange", update);
window.addEventListener("pagehide", () => clearTimeout(timer));
window.addEventListener("pageshow", update);
update();
