// A question's printed layout read for display (static/latex-text.js; FRONTEND.md "grading-math.js", 2026-10-05).
import assert from "node:assert/strict";
import test from "node:test";
import {cssLength, latexTree} from "../static/latex-text.js";

const kinds = nodes => nodes.map(n => n.t);
const texts = nodes => nodes.flatMap(n => n.t === "text" ? [n.v] : n.c ? texts(n.c) : []).join("");

test("prose and mathematics without layout commands keep the plain reading", () => {
  for (const text of ["plain words", "x ~ 5 and 1--3", String.raw`Check $x=\frac{1}{2}$ here.`, String.raw`x \le 5`, String.raw`\frac{1}{2}`,
    String.raw`\begin{bmatrix} 1 & 2 \end{bmatrix}`]) assert.equal(latexTree(text), null, text);
});

test("bold, answer blanks, gaps and line breaks of a printed question", () => {
  const tree = latexTree(String.raw`\textbf{This is the final tableau because:} \underline{\hspace{8.5cm}}\\[7mm]
$x=$\underline{\hspace{1.1cm}}\hfill $y=$\underline{\hspace{1.1cm}}`);
  assert.deepEqual(kinds(tree), ["style", "text", "blank", "par", "math", "blank", "gap", "math", "blank"]);
  assert.equal(tree[0].s, "strong");
  assert.equal(texts(tree[0].c), "This is the final tableau because:");
  assert.equal(tree[2].width, "8.5cm");
  assert.equal(tree[4].src, "x=");
  assert.equal(tree[4].raw, "$x=$");
  assert.equal(tree[5].width, "1.1cm");
  assert.ok(tree[6].fill);
});

test("escaped characters, typography and declarations inside a group", () => {
  const tree = latexTree(String.raw`Each chair earns \$40 --- or 50\% {\bf more}~now.`);
  assert.equal(texts(tree), "Each chair earns $40 — or 50% more now.");
  assert.equal(tree.find(n => n.t === "group").c[0].s, "strong");
});

test("a tabular answer grid with rules, column widths and a centred block", () => {
  const tree = latexTree(String.raw`\begin{center}\renewcommand{\arraystretch}{1.9}\begin{tabular}{|*{2}{>{\centering\arraybackslash}p{1.2cm}|}|l|}\hline a & $b$ & c\\ \hline\hline &&\\ \hline\end{tabular}\end{center}`);
  assert.deepEqual(kinds(tree), ["block"]);
  const table = tree[0].c.find(n => n.t === "table");
  assert.ok(table.ruled);
  assert.deepEqual(table.cols, [{align: "c", width: "1.2cm"}, {align: "c", width: "1.2cm"}, {align: "l", width: null}]);
  assert.equal(table.rows.length, 2);
  assert.equal(table.rows[0].rule, 1);
  assert.equal(table.rows[1].rule, 2);
  assert.equal(table.below, 1);
  assert.ok(table.rows[0].cells[1].c.some(n => n.t === "math"));
  assert.equal(table.rows[1].cells.length, 3);
});

test("minipage columns, struts, lists and figures", () => {
  const tree = latexTree(String.raw`\begin{minipage}[t]{0.58\linewidth}\vspace{0pt} \textbf{Variables:}\par\vspace{0.95in}\end{minipage}\hfill \begin{minipage}[t]{0.38\linewidth}\textbf{Subject to:}\end{minipage}
\begin{enumerate}\item[(a)] one \item two\end{enumerate}\rule{0pt}{0.8cm}\rule{3cm}{0.4pt}\begin{tikzpicture}\node at (0,0) { };\end{tikzpicture}`);
  const columns = tree.filter(n => n.t === "column");
  assert.deepEqual(columns.map(c => c.width), ["58%", "38%"]);
  const list = tree.find(n => n.t === "list");
  assert.equal(list.ordered, true);
  assert.deepEqual(list.items.map(i => [i.label, texts(i.c).trim()]), [["(a)", "one"], [null, "two"]]);
  assert.deepEqual(tree.filter(n => n.t === "blank").map(n => n.width), ["3cm"]);
  assert.ok(tree.some(n => n.t === "figure"));
});

test("a list's item spacing commands are not items", () => {
  const items = source => latexTree(source).find(n => n.t === "list").items.map(i => texts(i.c).trim());
  assert.deepEqual(items(String.raw`\textbf{Do:} \begin{itemize}\setlength{\itemsep}{0pt}\item First \item Second\end{itemize}`), ["First", "Second"]);
  assert.deepEqual(items(String.raw`\textbf{Do:} \begin{enumerate}\itemsep=0pt \item First \item Second\end{enumerate}`), ["First", "Second"]);
  // An inner list that opens with the command: the search past its end for the outer list's next item reads it the same way.
  const outer = latexTree(String.raw`\textbf{Do:} \begin{enumerate}\item One \begin{itemize}\setlength{\itemsep}{0pt}\item Inner\end{itemize} \item Two\end{enumerate}`).find(n => n.t === "list");
  assert.deepEqual(outer.items.map(i => texts(i.c).trim().split(/\s+/)[0]), ["One", "Two"]);
  assert.equal(outer.items[0].c.find(n => n.t === "list").items.length, 1);
});

test("an unknown command is offered to KaTeX with its arguments; unclosed input stays bounded", () => {
  const tree = latexTree(String.raw`\textbf{Bound:} \frac{43}{2} and \includegraphics{x.png}`);
  assert.deepEqual(tree.filter(n => n.t === "math").map(n => [n.src, n.loose]), [[String.raw`\frac{43}{2}`, true], [String.raw`\includegraphics{x.png}`, true]]);
  assert.ok(latexTree(String.raw`\textbf{never closed $x`));
  assert.equal(latexTree(String.raw`\textbf{`.repeat(30)), null);
  assert.equal(latexTree("\\toString{x} \\textbf{y}").find(n => n.t === "math").src, "\\toString{x}");
});

test("lengths become CSS lengths only from known units", () => {
  assert.equal(cssLength("8.5cm"), "8.5cm");
  assert.equal(cssLength(" 12bp "), "12pt");
  assert.equal(cssLength("0.58\\linewidth"), "58%");
  assert.equal(cssLength("\\textwidth"), "100%");
  for (const bad of ["", "0pt", "-1cm", "3cm;color:red", "url(x)", "2\\linewidth", "5000cm"]) assert.equal(cssLength(bad), null, bad);
});

test("display mathematics in a printed layout takes its own line, without the source's line breaks around it", () => {
  const tree = latexTree("Read the tableau.\n\\[\\begin{array}{c} 1 \\end{array}\\]\n\\textbf{Why:} \\underline{\\hspace{2cm}}");
  assert.deepEqual(kinds(tree), ["text", "math", "style", "text", "blank"]);
  assert.equal(tree[0].v, "Read the tableau.");
  assert.equal(tree[1].display, true);
  assert.equal(tree[1].raw, "\\[\\begin{array}{c} 1 \\end{array}\\]");
});
