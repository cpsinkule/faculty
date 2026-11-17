// Text-mode LaTeX around the mathematics, read for display only (FRONTEND.md "grading-math.js", 2026-10-05: a question's printed
// layout showed as source beside its rendered tableau). A question's stored source carries the commands that lay out its printed page:
// \textbf{…}, answer blanks (\underline{\hspace{8.5cm}}), \hfill, \\[7mm], tabular answer grids, minipage columns, \$. latexTree reads
// a bounded subset of them into plain nodes, which grading-math.js builds as DOM (text through text nodes, lengths through CSSOM).
// Mathematics keeps its own delimiters ($…$, \(…\), \[…\], $$…$$) and goes to KaTeX; an unknown command is offered to KaTeX with its
// arguments and otherwise stays literal. A text that uses none of these commands returns null and keeps the plain reading, so prose
// and transcripts ("~", "--") are never reinterpreted.

const MAX_NODES = 4000, MAX_DEPTH = 24;

// Environments KaTeX reads as mathematics even when the source wrote them without delimiters (answer keys: \begin{bmatrix}…).
const MATH_ENVS = new Set(["matrix", "pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix", "smallmatrix", "array", "cases", "aligned",
  "alignedat", "gathered", "split", "align", "align*", "alignat", "alignat*", "equation", "equation*", "gather", "gather*"]);
const DISPLAY_ENVS = new Set(["align", "align*", "alignat", "alignat*", "equation", "equation*", "gather", "gather*"]);
const STYLE = {textbf: "strong", emph: "em", textit: "em", textsl: "em", texttt: "code", underline: "u", uline: "u",
  text: "plain", textrm: "plain", textsf: "plain", textnormal: "plain", textup: "plain", textmd: "plain", textsc: "plain", mbox: "plain",
  fbox: "plain", phantom: "phantom", hphantom: "phantom"};
// Declarations that restyle the rest of their group: {\bf …}.
const SWITCH = {bf: "strong", bfseries: "strong", it: "em", itshape: "em", em: "em", sl: "em", slshape: "em", tt: "code",
  ttfamily: "code", rm: "plain", rmfamily: "plain", sffamily: "plain", normalfont: "plain", upshape: "plain", mdseries: "plain"};
const SYMBOL = {ldots: "…", dots: "…", textellipsis: "…", textendash: "–", textemdash: "—", textbackslash: "\\", textasciitilde: "~",
  textasciicircum: "^", textbar: "|", textless: "<", textgreater: ">", textdegree: "°", S: "§", copyright: "©", textbullet: "•",
  textperiodcentered: "·", textquoteleft: "‘", textquoteright: "’", textquotedblleft: "“", textquotedblright: "”", LaTeX: "LaTeX",
  TeX: "TeX", textunderscore: "_", textdollar: "$", textpercent: "%"};
// Commands with no effect on screen, followed by how many brace arguments they take.
const IGNORE = {noindent: 0, indent: 0, centering: 0, raggedright: 0, raggedleft: 0, arraybackslash: 0, normalsize: 0, small: 0,
  footnotesize: 0, scriptsize: 0, tiny: 0, large: 0, Large: 0, LARGE: 0, huge: 0, Huge: 0, hline: 0, relax: 0, protect: 0,
  nobreak: 0, allowbreak: 0, leavevmode: 0, strut: 0, selectfont: 0, null: 0, unskip: 0, ignorespaces: 0, newpage: 0, clearpage: 0,
  pagebreak: 0, nopagebreak: 0, vfill: 0, displaystyle: 0, vphantom: 1, vspace: 1, addvspace: 1, label: 1, pagestyle: 1,
  thispagestyle: 1, cline: 1, setlength: 2, addtolength: 2, setcounter: 2, renewcommand: 2, newcommand: 2, providecommand: 2};
const GAP = {quad: "1em", qquad: "2em", enspace: "0.5em", enskip: "0.5em", thinspace: "0.17em"};
const FILL = new Set(["hfill", "hfil", "hss", "dotfill"]);
const SKIP = new Set(["par", "smallskip", "medskip", "bigskip"]);
const HEADING = new Set(["section", "subsection", "subsubsection", "paragraph"]);
const DELIMITED = new Set(["left", "right", "middle", "big", "Big", "bigg", "Bigg", "bigl", "bigr", "Bigl", "Bigr", "biggl", "biggr"]);
const DELIMITER = {langle: "⟨", rangle: "⟩", lbrace: "{", rbrace: "}", lvert: "|", rvert: "|", vert: "|", lfloor: "⌊", rfloor: "⌋",
  lceil: "⌈", rceil: "⌉"};

// A LaTeX length as a CSS length: absolute units as written, a fraction of \linewidth as a percentage; anything else is no width.
export function cssLength(value) {
  const text = String(value || "").trim();
  let m = text.match(/^(\d*\.?\d+)\s*(cm|mm|in|pt|pc|em|ex|bp|px)$/);
  if (m) {
    const n = Number(m[1]);
    return n > 0 && n <= 1000 ? `${n}${m[2] === "bp" ? "pt" : m[2]}` : null;
  }
  m = text.match(/^(\d*\.?\d+)?\s*\\(linewidth|textwidth|columnwidth|hsize)$/);
  if (!m) return null;
  const f = m[1] === undefined ? 1 : Number(m[1]);
  return f > 0 && f <= 1 ? `${Math.round(f * 1000) / 10}%` : null;
}

const typography = text => text.replaceAll("---", "—").replaceAll("--", "–").replaceAll("``", "“").replaceAll("''", "”").replaceAll("~", " ");
const escapedAt = (s, k) => { let n = 0; while (k > 0 && s[--k] === "\\") n++; return n % 2 === 1; };

// The matching close of the brace at `i` (escaped braces skipped), or `to` when it never closes.
function braceEnd(s, i, to) {
  let depth = 0;
  for (let k = i; k < to; k++) {
    if (s[k] === "\\") { k++; continue; }
    if (s[k] === "{") depth++;
    else if (s[k] === "}" && --depth === 0) return k;
  }
  return to;
}

function bracketEnd(s, i, to) {
  let depth = 0;
  for (let k = i + 1; k < to; k++) {
    if (s[k] === "\\") { k++; continue; }
    if (s[k] === "{") depth++;
    else if (s[k] === "}") depth--;
    else if (s[k] === "]" && depth <= 0) return k;
  }
  return -1;
}

// Mathematics opening at `i`, with the same delimiter rules as grading-math.js mathParts: a single dollar is not followed by a space
// and its closing dollar not preceded by one; an unclosed delimiter is not mathematics.
function mathAt(s, i, to) {
  const pairs = [["$$", "$$", true], ["\\(", "\\)", false], ["\\[", "\\]", true], ["$", "$", false]];
  const pair = pairs.find(([left]) => s.startsWith(left, i));
  if (!pair || (pair[0] === "$" && /\s/.test(s[i + 1] || " "))) return null;
  const [left, right, display] = pair;
  for (let k = i + left.length; k < to; k++) {
    if (s.startsWith(right, k) && !escapedAt(s, k) && (right !== "$" || !/\s/.test(s[k - 1]))) {
      if (k + right.length > to) return null;
      return {from: i + left.length, to: k, next: k + right.length, display};
    }
  }
  return null;
}

// The \end{name} matching a \begin{name} whose body starts at `i`: {bodyEnd, next}, or null when it never closes.
function envEnd(s, name, i, to) {
  const open = `\\begin{${name}}`, close = `\\end{${name}}`;
  let depth = 1;
  for (let k = i; k < to; k++) {
    if (s.startsWith(open, k)) { depth++; k += open.length - 1; }
    else if (s.startsWith(close, k) && --depth === 0) return {bodyEnd: k, next: k + close.length};
  }
  return null;
}

// Where `\\` or `&` separate a tabular's rows or cells, outside braces, mathematics and nested environments.
function splitTop(s, from, to, separator) {
  const pieces = [];
  let start = from, depth = 0;
  for (let k = from; k < to; k++) {
    const math = depth === 0 && (s[k] === "$" || (s[k] === "\\" && (s[k + 1] === "(" || s[k + 1] === "["))) ? mathAt(s, k, to) : null;
    if (math) { k = math.next - 1; continue; }
    if (s.startsWith("\\begin{", k)) {
      const name = s.slice(k + 7, braceEnd(s, k + 6, to));
      const end = envEnd(s, name, braceEnd(s, k + 6, to) + 1, to);
      if (end) { k = end.next - 1; continue; }
    }
    if (separator === "row" && depth === 0 && s.startsWith("\\\\", k)) {
      pieces.push([start, k]);
      let j = k + 2;
      if (s[j] === "*") j++;
      if (s[j] === "[") { const e = bracketEnd(s, j, to); if (e > -1) j = e + 1; }
      start = j; k = j - 1; continue;
    }
    if (s[k] === "\\") { k++; continue; }
    if (s[k] === "{") depth++;
    else if (s[k] === "}") depth--;
    else if (separator === "cell" && depth === 0 && s[k] === "&") { pieces.push([start, k]); start = k + 1; }
  }
  pieces.push([start, to]);
  return pieces;
}

// A tabular column specification: {|*{6}{>{\centering\arraybackslash}p{1.2cm}|}|l|} → its columns and whether it draws rules.
function columns(spec, depth = 0) {
  const cols = [];
  let before = "";
  const group = k => { const e = braceEnd(spec, k, spec.length); return {text: spec.slice(k + 1, e), next: e + 1}; };
  for (let k = 0; k < spec.length && depth < 4;) {
    const c = spec[k];
    if (c === "*" && spec[k + 1] === "{") {
      const count = group(k + 1), body = group(count.next);
      const inner = columns(body.text, depth + 1);
      for (let n = 0; n < Math.min(Number(count.text) || 0, 40); n++) cols.push(...inner);
      k = body.next; continue;
    }
    if ((c === ">" || c === "<" || c === "@" || c === "!") && spec[k + 1] === "{") {
      const g = group(k + 1);
      if (c === ">") before += g.text;
      k = g.next; continue;
    }
    if ("lcr".includes(c)) { cols.push({align: c, width: null}); before = ""; k++; continue; }
    if ("pmbX".includes(c)) {
      const g = c === "X" ? {text: "", next: k + 1} : group(k + 1);
      cols.push({align: before.includes("\\centering") ? "c" : before.includes("\\raggedleft") ? "r" : "l", width: cssLength(g.text)});
      before = ""; k = g.next; continue;
    }
    k++;
  }
  return cols.slice(0, 40);
}

export function latexTree(source) {
  const s = String(source || "");
  let count = 0, layout = false;
  const node = value => { if (++count > MAX_NODES) throw new RangeError("too many nodes"); return value; };
  const skipSpaces = (i, to) => { while (i < to && (s[i] === " " || s[i] === "\t")) i++; return i; };
  const skipLine = (i, to) => { i = skipSpaces(i, to); return s[i] === "\n" && i < to ? i + 1 : i; };
  // A command's argument: a brace group, or else the next single token.
  const argument = (i, to) => {
    i = skipSpaces(i, to);
    if (s[i] === "{") { const e = braceEnd(s, i, to); return {from: i + 1, to: e, next: Math.min(e + 1, to)}; }
    if (s[i] === "\\") { let j = i + 1; while (j < to && /[a-zA-Z]/.test(s[j])) j++; j = Math.max(j, i + 2); return {from: i, to: j, next: j}; }
    return {from: i, to: Math.min(i + 1, to), next: Math.min(i + 1, to)};
  };
  const optional = (i, to) => {
    const j = skipSpaces(i, to);
    if (s[j] !== "[") return {text: null, next: i};
    const e = bracketEnd(s, j, to);
    return e < 0 ? {text: null, next: i} : {text: s.slice(j + 1, e), next: e + 1};
  };

  function table(name, i, to, bodyEnd, depth) {
    let j = optional(i, to).next;
    if (name !== "tabular") j = argument(j, to).next; // tabular*/tabularx: the width comes first
    const spec = argument(j, to);
    const cols = columns(s.slice(spec.from, spec.to));
    const rows = [];
    let below = 0;
    const pieces = splitTop(s, spec.next, bodyEnd, "row");
    pieces.forEach(([from, rowEnd], index) => {
      let k = from, rule = 0;
      for (;;) {
        while (k < rowEnd && /\s/.test(s[k])) k++;
        if (s.startsWith("\\hline", k)) { rule++; k += 6; continue; }
        if (s.startsWith("\\cline", k)) { k = argument(k + 6, rowEnd).next; continue; }
        break;
      }
      if (!s.slice(k, rowEnd).trim()) { if (index === pieces.length - 1) below = rule; else if (rule && rows.length) rows.at(-1).below = rule; return; }
      const cells = splitTop(s, k, rowEnd, "cell").map(([a, b]) => {
        const at = skipSpaces(a, b);
        if (s.startsWith("\\multicolumn", at)) {
          const span = argument(at + 12, b), colSpec = argument(span.next, b), body = argument(colSpec.next, b);
          return {span: Math.max(1, Math.min(40, Number(s.slice(span.from, span.to)) || 1)), align: columns(s.slice(colSpec.from, colSpec.to))[0]?.align, c: parse(body.from, body.to, depth + 1)};
        }
        return {span: 1, c: parse(a, b, depth + 1)};
      });
      rows.push(node({rule: Math.min(rule, 2), cells}));
    });
    return node({t: "table", ruled: s.slice(spec.from, spec.to).includes("|"), cols, rows, below: Math.min(below, 2)});
  }

  function environment(name, start, i, to, depth) {
    const end = envEnd(s, name, i, to);
    if (!end) return null;
    if (MATH_ENVS.has(name)) return {n: node({t: "math", src: s.slice(start, end.next), raw: s.slice(start, end.next), display: DISPLAY_ENVS.has(name)}), next: end.next};
    layout = true;
    if (name === "tabular" || name === "tabular*" || name === "tabularx") return {n: table(name, i, to, end.bodyEnd, depth), next: end.next};
    if (name === "center" || name === "flushleft" || name === "flushright" || name === "quote" || name === "quotation") {
      return {n: node({t: "block", align: name === "center" ? "c" : name === "flushright" ? "r" : "l", c: parse(skipLine(i, end.bodyEnd), end.bodyEnd, depth + 1)}), next: end.next};
    }
    if (name === "minipage") {
      let j = optional(i, end.bodyEnd).next;
      j = optional(j, end.bodyEnd).next;
      j = optional(j, end.bodyEnd).next;
      const width = argument(j, end.bodyEnd);
      return {n: node({t: "column", width: cssLength(s.slice(width.from, width.to)), c: parse(width.next, end.bodyEnd, depth + 1)}), next: end.next};
    }
    if (name === "itemize" || name === "enumerate" || name === "description") {
      const items = [];
      // \item itself, not \itemsep or \itemindent, which only share its first letters.
      const ITEM = /\\item(?![a-zA-Z])/g;
      const nextItem = (/** @type {number} */ from) => { ITEM.lastIndex = from; const found = ITEM.exec(s); return found ? found.index : -1; };
      let k = nextItem(i);
      while (k > -1 && k < end.bodyEnd) {
        const label = optional(k + 5, end.bodyEnd);
        let next = nextItem(label.next);
        // An \item of a nested list belongs to that list.
        for (let nested = s.indexOf("\\begin{", label.next); nested > -1 && next > -1 && nested < next;) {
          const inner = s.slice(nested + 7, braceEnd(s, nested + 6, end.bodyEnd));
          const close = envEnd(s, inner, braceEnd(s, nested + 6, end.bodyEnd) + 1, end.bodyEnd);
          if (!close) break;
          if (close.next > next) next = nextItem(close.next);
          nested = s.indexOf("\\begin{", close.next);
        }
        const stop = next > -1 && next < end.bodyEnd ? next : end.bodyEnd;
        items.push({label: label.text, c: parse(label.next, stop, depth + 1)});
        k = stop < end.bodyEnd ? stop : -1;
      }
      return {n: node({t: "list", ordered: name === "enumerate", items}), next: end.next};
    }
    if (name === "tikzpicture" || name === "picture") return {n: node({t: "figure"}), next: end.next};
    return {n: node({t: "group", c: parse(i, end.bodyEnd, depth + 1)}), next: end.next};
  }

  function parse(from, to, depth) {
    if (depth > MAX_DEPTH) throw new RangeError("too deep");
    const out = [];
    let text = "";
    const flush = () => { if (text) out.push(node({t: "text", v: text})); text = ""; };
    const push = value => { flush(); out.push(value); };
    // A block (display mathematics, a centred grid, a list) takes its own lines: the source's line breaks around it are not repeated.
    const BLOCKS = new Set(["block", "table", "list"]);
    const pushBlock = value => { text = text.replace(/[ \t]*\n[ \t]*$/, ""); push(value); };
    let i = from;
    while (i < to) {
      const c = s[i];
      const math = c === "$" || (c === "\\" && (s[i + 1] === "(" || s[i + 1] === "[")) ? mathAt(s, i, to) : null;
      // In a printed layout \[…\] and $$…$$ stand on their own centred line, as on the page.
      if (math && math.display) { pushBlock(node({t: "math", src: s.slice(math.from, math.to), raw: s.slice(i, math.next), display: true})); i = skipLine(math.next, to); continue; }
      if (math) { push(node({t: "math", src: s.slice(math.from, math.to), raw: s.slice(i, math.next), display: false})); i = math.next; continue; }
      if (c === "{") {
        const e = braceEnd(s, i, to);
        push(node({t: "group", c: parse(i + 1, e, depth + 1)}));
        i = Math.min(e + 1, to); continue;
      }
      if (c !== "\\") { text += c; i++; continue; }
      const next = s[i + 1];
      if (next === undefined) { text += c; i++; continue; }
      if (next === "\\") {
        layout = true;
        let j = i + 2;
        if (s[j] === "*") j++;
        const gap = s[j] === "[" && bracketEnd(s, j, to) > -1;
        if (gap) j = bracketEnd(s, j, to) + 1;
        push(node({t: gap ? "par" : "br"}));
        i = skipLine(j, to); continue;
      }
      if ("$%&#_{}".includes(next)) { layout = true; text += next; i += 2; continue; }
      if (next === " " || next === "\n") { text += " "; i += 2; continue; }
      if (next === "," || next === ";" || next === ":") { text += " "; i += 2; continue; }
      if (next === "!" || next === "-" || next === "/") { i += 2; continue; }
      if (!/[a-zA-Z]/.test(next)) { text += c + next; i += 2; continue; }
      let j = i + 1;
      while (j < to && /[a-zA-Z]/.test(s[j])) j++;
      const name = s.slice(i + 1, j);
      if (s[j] === "*") j++;
      const after = skipSpaces(j, to);
      if (Object.hasOwn(STYLE, name)) {
        // \text{…} and its kin also live in mathematics written without delimiters (an answer key's "x = \text{chairs}"), so alone they
        // do not make a text a printed layout.
        if (STYLE[name] !== "plain") layout = true;
        const arg = argument(j, to), c2 = parse(arg.from, arg.to, depth + 1);
        // An underline around nothing but space is an answer blank.
        const blank = STYLE[name] === "u" && c2.every(n => n.t === "gap" || n.t === "blank" || (n.t === "text" && !n.v.trim()));
        push(node(blank ? {t: "blank", width: c2.find(n => n.width)?.width || null} : {t: "style", s: STYLE[name], c: c2}));
        i = arg.next; continue;
      }
      if (Object.hasOwn(SWITCH, name)) { layout = true; push(node({t: "style", s: SWITCH[name], c: parse(after, to, depth + 1)})); i = to; continue; }
      if (Object.hasOwn(SYMBOL, name)) { layout = true; text += SYMBOL[name]; i = s[j] === "{" && s[j + 1] === "}" ? j + 2 : after; continue; }
      if (Object.hasOwn(GAP, name)) { layout = true; push(node({t: "gap", width: GAP[name]})); i = after; continue; }
      if (FILL.has(name)) { layout = true; push(node({t: "gap", fill: true})); i = after; continue; }
      if (name === "hrulefill") { layout = true; push(node({t: "blank", width: null})); i = after; continue; }
      if (name === "hspace") { layout = true; const arg = argument(j, to); push(node({t: "gap", width: cssLength(s.slice(arg.from, arg.to))})); i = arg.next; continue; }
      if (name === "newline" || name === "linebreak") { layout = true; push(node({t: "br"})); i = skipLine(optional(j, to).next, to); continue; }
      if (SKIP.has(name)) { layout = true; push(node({t: "par"})); i = skipLine(after, to); continue; }
      if (name === "rule") {
        layout = true;
        const raise = optional(j, to), width = argument(raise.next, to), height = argument(width.next, to);
        // A rule with width drawn at text height is an answer line; \rule{0pt}{0.8cm} is only a strut.
        const w = cssLength(s.slice(width.from, width.to));
        if (w && /^\s*0?\.\d+\s*pt\s*$|^\s*[0-2](\.\d+)?\s*pt\s*$/.test(s.slice(height.from, height.to))) push(node({t: "blank", width: w}));
        i = height.next; continue;
      }
      if (name === "vskip" || name === "hskip") { layout = true; i = after + (s.slice(after, to).match(/^-?\d*\.?\d+\s*[a-z]{2}/)?.[0].length || 0); continue; }
      if (Object.hasOwn(IGNORE, name)) {
        layout = true;
        let k = j;
        for (let n = 0; n < IGNORE[name]; n++) { k = optional(k, to).next; k = argument(k, to).next; }
        i = skipLine(k, to); continue;
      }
      if (HEADING.has(name)) {
        layout = true;
        const arg = argument(optional(j, to).next, to);
        push(node({t: "par"})); push(node({t: "style", s: "strong", c: parse(arg.from, arg.to, depth + 1)})); push(node({t: "par"}));
        i = skipLine(arg.next, to); continue;
      }
      if (name === "begin") {
        const arg = argument(j, to), env = s.slice(arg.from, arg.to).trim();
        const found = environment(env, i, arg.next, to, depth);
        if (found && (BLOCKS.has(found.n.t) || found.n.display)) { pushBlock(found.n); i = skipLine(found.next, to); continue; }
        if (found) { push(found.n); i = found.next; continue; }
        text += s.slice(i, arg.next); i = arg.next; continue;
      }
      if (name === "end") { layout = true; i = argument(j, to).next; continue; }
      if (name === "item") { layout = true; push(node({t: "br"})); text += "• "; i = optional(j, to).next; continue; }
      // \left( … \right) around prose: the delimiter alone, since KaTeX cannot read half of a pair.
      if (DELIMITED.has(name)) {
        let k = skipSpaces(j, to);
        const word = s.slice(k).match(/^\\(langle|rangle|lbrace|rbrace|lvert|rvert|vert|lfloor|rfloor|lceil|rceil)(?![a-zA-Z])/);
        if (word) { text += DELIMITER[word[1]]; k += word[0].length; }
        else if (s[k] === "\\" && "{}|".includes(s[k + 1] || "x")) { text += s[k + 1] === "|" ? "‖" : s[k + 1]; k += 2; }
        else if (s[k] === ".") k++;
        else if (k < to && "()[]|/<>".includes(s[k])) text += s[k++];
        i = k; continue;
      }
      // Anything else is offered to KaTeX with the brace groups that follow it (\le, \frac{43}{2}); literal when it cannot be read.
      let k = j;
      for (let n = 0; n < 3 && s[k] === "{"; n++) k = Math.min(braceEnd(s, k, to) + 1, to);
      push(node({t: "math", src: s.slice(i, k), display: false, loose: true}));
      i = k;
    }
    flush();
    return out;
  }

  try {
    const tree = parse(0, s.length, 0);
    if (!layout) return null;
    const polish = nodes => nodes.forEach(n => {
      if (n.t === "text") n.v = typography(n.v);
      for (const list of [n.c, ...(n.rows || []).flatMap(r => r.cells.map(cell => cell.c)), ...(n.items || []).map(item => item.c)]) if (list) polish(list);
    });
    polish(tree);
    return tree;
  } catch {
    return null;
  }
}
