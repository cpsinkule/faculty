// @ts-check
// The Assistant's message text (ASSISTANT "THE PANEL"): a model's Markdown as safe HTML, a table as tab-separated text for a
// spreadsheet, and the time on a message. Pure: no DOM, no imports. Everything is escaped first; no raw HTML survives, links are
// only Faculty routes (#...) or https in a new tab, and no style attribute is ever written (the page's CSP forbids them).

/** @param {unknown} value */
const text = (value) => String(value ?? "");

/** HTML escaping for text put into markup. @param {unknown} value */
export function esc(value) {
  return text(value).replace(/[&<>"']/g, (c) => /** @type {Record<string, string>} */ ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

/** Escaped text with each line break as a `<br>`: a request or message quoted on a card. @param {unknown} value */
export const multiline = (value) => esc(value).replace(/\n/g, "<br>");

/**
 * Inline Markdown on already escaped text: code, math, bold, emphasis and links (Faculty routes, and https links opened in a new
 * tab). Code and math are set aside first, so nothing inside them is read as markup. Math keeps its source as the element's text
 * (`data-math`): static/grading-math.js draws it with KaTeX, and it stays legible as written until then or if that fails. A lone
 * dollar sign follows pandoc's rule (no space inside the pair, no digit after it), so "$5 to $10" stays text.
 */
function inline(/** @type {string} */ escaped) {
  const kept = /** @type {string[]} */ ([]);
  const keep = (/** @type {string} */ markup) => { kept.push(markup); return `\u0000${kept.length - 1}\u0000`; };
  let out = escaped.replace(/`([^`]+)`/g, (_, code) => keep(`<code>${code}</code>`));
  out = out.replace(/\\\((.+?)\\\)|\$(?=[^\s$])([^$\n]*?[^\s$\\]|[^\s$\\])\$(?!\d)/g, (whole) => keep(`<span class="assistant-math" data-math>${whole}</span>`));
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  // Emphasis is upright and heavier (DESIGN-LANGUAGE: no italics except quoted email); a lone "*" between spaces is a product sign.
  out = out.replace(/(^|[^*\w])\*(?=[^\s*])([^*\n]*?[^\s*]|[^\s*])\*(?![*\w])/g, "$1<em>$2</em>");
  out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (whole, label, href) => {
    const raw = href.replace(/&amp;/g, "&");
    if (/^#[A-Za-z0-9/_?=&%.:-]*$/.test(raw)) return `<a class="assistant-route" href="${esc(raw)}">${label}</a>`;
    if (/^https:\/\/[^\s<>"']+$/.test(raw)) return `<a href="${esc(raw)}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    return label;
  });
  return out.replace(/\u0000(\d+)\u0000/g, (_, index) => kept[Number(index)]);
}

const BULLET = /^(\s*)[-*]\s+(.*)$/, NUMBERED = /^(\s*)\d+[.)]\s+(.*)$/;
const RULE = /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/;
const ROW = /^\s*\|.*\|\s*$/;
const MAX_NESTING = 32;

/** A list line: its indent, kind and words; null for any other line. @param {string | undefined} line */
function listLine(line) {
  if (line === undefined || RULE.test(line)) return null;
  const bullet = line.match(BULLET), numbered = bullet ? null : line.match(NUMBERED);
  const found = bullet || numbered;
  return found ? { indent: found[1].replace(/\t/g, "  ").length, ordered: !bullet, words: found[2] } : null;
}

/** The cells of a pipe-table row, trimmed. @param {string} row */
const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());

/** A cell that is a number as an instructor writes one: 7, 7.5, 1,204, 85%, 7/10, -2, $12.50; a dash or nothing counts with them. */
const NUMBER = /^(?:[-−+]?\$?\d[\d,]*(?:\.\d+)?%?(?:\s*\/\s*\d[\d,]*(?:\.\d+)?)?|[-–—]?)$/;

/**
 * The pipe table that starts at `lines[index]`: a row, a separator line (dashes, colons, pipes) and the rows that follow; null when
 * none starts there. `next` is the index of the first line after it. The table drawn and the table Copy table copies are this one.
 * @param {string[]} lines @param {number} index
 */
function tableAt(lines, index) {
  if (!ROW.test(lines[index]) || index + 1 >= lines.length || !/^\s*\|?[\s:|-]+\|?\s*$/.test(lines[index + 1]) || !lines[index + 1].includes("-")) return null;
  const head = cells(lines[index]);
  let next = index + 2;
  const rows = /** @type {string[][]} */ ([]);
  while (next < lines.length && ROW.test(lines[next])) rows.push(cells(lines[next++]));
  return { head, rows, next };
}

/**
 * A model's answer as safe HTML: headings, nested lists, tables (number columns marked `data-num`), block quotes, rules, code
 * blocks, display math, and the inline forms above.
 * @param {string} source
 */
export function markdown(source) {
  return renderMarkdown(source, 0);
}

/** Deep model-generated nesting stays readable as escaped text instead of exhausting the browser's stack. */
function renderMarkdown(/** @type {string} */ source, /** @type {number} */ depth) {
  if (depth >= MAX_NESTING) return `<pre>${esc(source)}</pre>`;
  const lines = text(source).replace(/\r\n?/g, "\n").split("\n");
  const out = /** @type {string[]} */ ([]);
  let index = 0;
  /** One list from `index` on: items of its first line's kind at its indent; a deeper line starts a list inside the last item. */
  const list = (nesting = depth) => {
    const first = /** @type {NonNullable<ReturnType<typeof listLine>>} */ (listLine(lines[index]));
    if (nesting >= MAX_NESTING) {
      const raw = [];
      while (index < lines.length && (listLine(lines[index])?.indent ?? -1) >= first.indent) raw.push(lines[index++]);
      return `<pre>${esc(raw.join("\n"))}</pre>`;
    }
    const items = /** @type {string[]} */ ([]);
    for (;;) {
      const line = listLine(lines[index]);
      if (!line || line.indent < first.indent) break;
      if (line.indent > first.indent + 1 && items.length) { items[items.length - 1] += list(nesting + 1); continue; }
      if (line.ordered !== first.ordered) break;
      items.push(inline(esc(line.words)));
      index += 1;
    }
    const tag = first.ordered ? "ol" : "ul";
    return `<${tag}>${items.map((item) => `<li>${item}</li>`).join("")}</${tag}>`;
  };
  while (index < lines.length) {
    const line = lines[index];
    if (/^```/.test(line)) {
      const body = [];
      index += 1;
      while (index < lines.length && !/^```/.test(lines[index])) body.push(lines[index++]);
      index += 1;
      out.push(`<pre><code>${esc(body.join("\n"))}</code></pre>`);
      continue;
    }
    // Display math: $$ ... $$ or \[ ... \], on one line or over several; an unclosed opener is ordinary text.
    const opener = line.match(/^\s*(\$\$|\\\[)/);
    if (opener) {
      const close = opener[1] === "$$" ? "$$" : "\\]";
      let end = index;
      const closed = (/** @type {number} */ at) => lines[at].indexOf(close, at === index ? line.indexOf(opener[1]) + opener[1].length : 0) >= 0;
      while (end < lines.length && !closed(end) && end - index < 60) end += 1;
      if (end < lines.length && closed(end) && lines[end].trim().endsWith(close)) {
        out.push(`<div class="assistant-math" data-math data-math-display>${esc(lines.slice(index, end + 1).join("\n").trim())}</div>`);
        index = end + 1;
        continue;
      }
    }
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      out.push(`<h${Math.min(6, heading[1].length + 3)} class="assistant-heading">${inline(esc(heading[2]))}</h${Math.min(6, heading[1].length + 3)}>`);
      index += 1;
      continue;
    }
    const found = tableAt(lines, index);
    if (found) {
      const { head, rows } = found;
      index = found.next;
      // A column of numbers is set right-aligned in tabular figures (assistant.css [data-num]).
      const numeric = head.map((_, column) => rows.some((row) => /\d/.test(row[column] || "")) && rows.every((row) => NUMBER.test(row[column] || "")));
      const cell = (/** @type {string} */ tag, /** @type {string} */ words, /** @type {number} */ column) => `<${tag}${numeric[column] ? " data-num" : ""}>${inline(esc(words))}</${tag}>`;
      out.push(`<div class="assistant-table"><table><thead><tr>${head.map((c, column) => cell("th", c, column)).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c, column) => cell("td", c, column)).join("")}</tr>`).join("")}</tbody></table></div>`);
      continue;
    }
    if (RULE.test(line)) { out.push('<hr class="assistant-rule">'); index += 1; continue; }
    if (/^\s*>/.test(line)) {
      const quoted = [];
      while (index < lines.length && /^\s*>/.test(lines[index])) quoted.push(lines[index++].replace(/^\s*>\s?/, ""));
      out.push(`<blockquote class="assistant-quote">${renderMarkdown(quoted.join("\n"), depth + 1)}</blockquote>`);
      continue;
    }
    if (listLine(line)) { out.push(list()); continue; }
    if (!line.trim()) { index += 1; continue; }
    const buffer = [];
    while (index < lines.length && lines[index].trim() && !/^(```|#{1,6}\s|\s*>|\s*\$\$|\s*\\\[)/.test(lines[index]) && !listLine(lines[index]) && !RULE.test(lines[index]) && !ROW.test(lines[index])) buffer.push(lines[index++]);
    if (!buffer.length) buffer.push(lines[index++]);
    out.push(`<p>${buffer.map((words) => inline(esc(words))).join("<br>")}</p>`);
  }
  return out.join("");
}

/** A cell's words without its Markdown marks, for a spreadsheet. @param {string} cell */
const plain = (cell) => cell.replace(/`([^`]+)`/g, "$1").replace(/\*\*([^*]+)\*\*/g, "$1").replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, "$1").replace(/[\t\r\n]+/g, " ");

/**
 * The pipe tables of an answer as tab-separated text (rows on lines, tables apart by a blank line): what Copy table puts on the
 * clipboard, so a pasted table lands in a spreadsheet's cells. "" when the answer holds no table.
 * @param {string} source
 */
export function tablesTSV(source) {
  const lines = text(source).replace(/\r\n?/g, "\n").split("\n");
  const tables = /** @type {string[]} */ ([]);
  let fenced = false;
  for (let index = 0; index < lines.length; index += 1) {
    if (/^```/.test(lines[index])) { fenced = !fenced; continue; }
    const found = fenced ? null : tableAt(lines, index);
    if (!found) continue;
    index = found.next - 1;
    tables.push([found.head, ...found.rows].map((row) => row.map(plain).join("\t")).join("\n"));
  }
  return tables.join("\n\n");
}

/**
 * The time on a message: the moment its event carries ("2026-10-09T14:05:00-05:00") on the reader's own clock, as the header's
 * clock shows the time: "2:05 PM" on the same day as `now`, otherwise "Oct 8, 2:05 PM". "" for a stamp that cannot be read.
 * `timeZone` is for checks; the browser's own zone otherwise.
 * @param {unknown} at @param {Date} [now] @param {string} [timeZone]
 */
export function messageTime(at, now = new Date(), timeZone = undefined) {
  const moment = new Date(text(at));
  if (!text(at) || Number.isNaN(moment.getTime())) return "";
  const format = new Intl.DateTimeFormat("en-US", { timeZone, month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
  const parts = (/** @type {Date} */ date) => Object.fromEntries(format.formatToParts(date).map((part) => [part.type, part.value]));
  const then = parts(moment), today = parts(now);
  const time = `${then.hour}:${then.minute} ${then.dayPeriod}`;
  return then.year === today.year && then.month === today.month && then.day === today.day ? time : `${then.month} ${then.day}, ${time}`;
}
