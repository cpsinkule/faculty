import assert from "node:assert/strict";
import test from "node:test";

// dom-patch.js (FRONTEND.md "Quiet refresh contract"): a poll's patch keeps what the instructor opened and typed.
const { patchChildren } = await import(new URL("../static/dom-patch.js", import.meta.url).href);

// Enough of a DOM for dom-patch.js: elements keep their attributes, children and parent, and a
// template parses the plain markup these tests write (tags, quoted attributes, text).
class FakeNode {
  constructor() { this.parentNode = null; this.connected = false; }
  get isConnected() { return this.connected || Boolean(this.parentNode?.isConnected); }
  remove() { if (this.parentNode) this.parentNode.childNodes.splice(this.parentNode.childNodes.indexOf(this), 1); this.parentNode = null; }
  replaceWith(node) {
    const parent = this.parentNode;
    node.remove();
    parent.childNodes.splice(parent.childNodes.indexOf(this), 1, node);
    node.parentNode = parent;
    this.parentNode = null;
  }
}
class FakeText extends FakeNode {
  constructor(value) { super(); this.nodeType = 3; this.nodeName = "#text"; this.nodeValue = value; }
}
class FakeElement extends FakeNode {
  constructor(name) { super(); this.nodeType = 1; this.nodeName = name.toUpperCase(); this.values = new Map(); this.childNodes = []; }
  get attributes() { return [...this.values].map(([name, value]) => ({ name, localName: name, namespaceURI: null, value })); }
  getAttribute(name) { return this.values.get(name) ?? null; }
  hasAttribute(name) { return this.values.has(name); }
  hasAttributeNS(namespace, name) { return this.values.has(name); }
  getAttributeNS(namespace, name) { return this.values.get(name) ?? null; }
  setAttributeNS(namespace, name, value) { this.values.set(name, value); }
  removeAttributeNS(namespace, name) { this.values.delete(name); }
  toggleAttribute(name, on) { if (on) this.values.set(name, ""); else this.values.delete(name); }
  get classList() { return { contains: (name) => (this.values.get("class") || "").split(" ").includes(name) }; }
  getElementsByClassName(name) {
    return this.childNodes.filter((child) => child.nodeType === 1)
      .flatMap((child) => [child, ...child.getElementsByClassName(name)]).filter((child) => child.classList.contains(name));
  }
  appendChild(node) { node.remove(); node.parentNode = this; this.childNodes.push(node); return node; }
  insertBefore(node, before) {
    if (!before) return this.appendChild(node);
    node.remove();
    node.parentNode = this;
    this.childNodes.splice(this.childNodes.indexOf(before), 0, node);
    return node;
  }
  matches() { return false; }
}
class FakeTemplate extends FakeElement {
  constructor() { super("template"); this.content = new FakeElement("#document-fragment"); }
  set innerHTML(html) {
    let parent = this.content;
    for (const [, close, open, attributes, empty, text] of html.matchAll(/<\/([\w-]+)>|<([\w-]+)((?:\s+[\w:-]+="[^"]*")*)\s*(\/?)>|([^<]+)/g)) {
      if (close) parent = parent.parentNode;
      else if (open) {
        const element = parent.appendChild(new FakeElement(open));
        for (const [, name, value] of attributes.matchAll(/([\w:-]+)="([^"]*)"/g)) element.setAttributeNS(null, name, value);
        if (!empty) parent = element;
      } else parent.appendChild(new FakeText(text));
    }
  }
}

test("a patch never closes a disclosure the instructor opened, unless another one now stands in its place", () => {
  const saved = { document: globalThis.document, Node: globalThis.Node };
  Object.assign(globalThis, { document: { createElement: () => new FakeTemplate() }, Node: { ELEMENT_NODE: 1 } });
  try {
    // "Named (2)" while names are read: the instructor opens it, and the next poll changes only its count.
    const fold = (count, extra = "") => `<details class="disclosure"${extra}><summary><span>Named (${count})</span></summary><div class="disclosure-body"><p>Rows</p></div></details>`;
    const page = new FakeElement("section");
    patchChildren(page, fold(2));
    const [details] = page.childNodes;
    details.setAttributeNS(null, "open", "");
    patchChildren(page, fold(3));
    assert.equal(page.childNodes[0], details, "the same element is kept");
    assert.ok(details.hasAttribute("open"), "the poll did not close what the instructor opened");
    assert.equal(details.childNodes[0].childNodes[0].childNodes[0].nodeValue, "Named (3)");
    // A patch may open one (the server's markup says open)...
    const closed = new FakeElement("section");
    patchChildren(closed, fold(1));
    patchChildren(closed, fold(1, ' open=""'));
    assert.ok(closed.childNodes[0].hasAttribute("open"));
    // ...and a list that moved puts another item's fold at this position: that one follows the new markup, closed.
    const list = new FakeElement("section");
    patchChildren(list, fold(1, ' data-group="a"') + fold(1, ' data-group="b"'));
    list.childNodes[0].setAttributeNS(null, "open", "");
    patchChildren(list, fold(1, ' data-group="b"') + fold(1, ' data-group="a" open=""'));
    assert.ok(!list.childNodes[0].hasAttribute("open"), "group b, now first, is not opened by group a's state");
    assert.ok(list.childNodes[1].hasAttribute("open"));
    // Other elements still lose an attribute the server no longer sends.
    const note = new FakeElement("section");
    patchChildren(note, '<p data-note="old">Text</p>');
    patchChildren(note, "<p>Text</p>");
    assert.equal(note.childNodes[0].getAttribute("data-note"), null);
  } finally {
    Object.assign(globalThis, saved);
  }
});

test("a keyed item that leaves a list takes nothing with it: an open, typed form below keeps its own key and value", () => {
  const saved = { document: globalThis.document, Node: globalThis.Node };
  Object.assign(globalThis, { document: { createElement: () => new FakeTemplate() }, Node: { ELEMENT_NODE: 1 } });
  try {
    // "Pages that need a look": page W, X and Y, each with its recovery form. The instructor opens X's form and types an alias.
    const card = (id) => `<article class="document-scan-page" data-patch-key="${id}"><h4>Attachment page ${id}</h4><details>`
      + `<summary>Recover using printed alias</summary><form data-recover="${id}"><input name="alias"/></form></details></article>`;
    const list = new FakeElement("div");
    patchChildren(list, card("w") + card("x") + card("y"));
    const [w, x, y] = list.childNodes;
    const fold = x.childNodes[1], form = fold.childNodes[1], input = form.childNodes[0];
    fold.setAttributeNS(null, "open", "");
    input.value = "A1-XXXXXXXXXX";
    // W is identified and leaves the list: X's own element moves up with its fold open and its typing, still posting to X.
    patchChildren(list, card("x") + card("y"));
    assert.deepEqual(list.childNodes, [x, y]);
    assert.equal(w.parentNode, null, "the page that left is removed, not re-used for the next one");
    assert.equal(form.getAttribute("data-recover"), "x");
    assert.ok(fold.hasAttribute("open"));
    assert.equal(input.value, "A1-XXXXXXXXXX");
    assert.ok(!y.childNodes[1].hasAttribute("open"), "Y's fold is still its own, closed");
    // A page that arrives goes in where the markup puts it; the others keep their elements in their new order.
    patchChildren(list, card("v") + card("y") + card("x"));
    assert.equal(list.childNodes[0].getAttribute("data-patch-key"), "v");
    assert.deepEqual(list.childNodes.slice(1), [y, x]);
    assert.equal(input.value, "A1-XXXXXXXXXX");
    // Plain markup never takes a keyed item's element.
    patchChildren(list, "<p>Nothing needs a look.</p>");
    assert.equal(list.childNodes.length, 1);
    assert.equal(list.childNodes[0].nodeName, "P");
    assert.equal(x.parentNode, null);
  } finally {
    Object.assign(globalThis, saved);
  }
});

test("finished content keeps what a script drew in it while its mark is the same, and follows the markup when the mark changes", () => {
  const saved = { document: globalThis.document, Node: globalThis.Node };
  Object.assign(globalThis, { document: { createElement: () => new FakeTemplate() }, Node: { ELEMENT_NODE: 1 } });
  try {
    // An Assistant answer whose math KaTeX has drawn: the markup still holds the formula's source.
    const answer = (mark, words) => `<div class="assistant-msg" data-patch-static="${mark}"><p>${words}</p></div>`;
    const chat = new FakeElement("div");
    patchChildren(chat, `<p>Asked</p>${answer("7-a", "x squared")}`);
    const node = chat.childNodes[1];
    node.setAttributeNS(null, "data-drawn", "yes");
    node.childNodes[0].childNodes[0].nodeValue = "DRAWN BY A SCRIPT";
    // The next poll adds a line after it: the answer is not touched, down to the attribute the script set.
    patchChildren(chat, `<p>Asked</p>${answer("7-a", "x squared")}<p>More</p>`);
    assert.equal(chat.childNodes[1], node);
    assert.equal(node.getAttribute("data-drawn"), "yes");
    assert.equal(node.childNodes[0].childNodes[0].nodeValue, "DRAWN BY A SCRIPT");
    assert.equal(chat.childNodes.length, 3);
    // Another answer at that place (another mark) is patched as usual: its words, and nothing the script left.
    patchChildren(chat, `<p>Asked</p>${answer("9-b", "y cubed")}`);
    assert.equal(chat.childNodes[1].getAttribute("data-patch-static"), "9-b");
    assert.equal(chat.childNodes[1].childNodes[0].childNodes[0].nodeValue, "y cubed");
    assert.equal(chat.childNodes[1].getAttribute("data-drawn"), null);
    // Unmarked content is always brought to the markup.
    patchChildren(chat, "<p>Asked again</p>");
    assert.equal(chat.childNodes[0].childNodes[0].nodeValue, "Asked again");
    assert.equal(chat.childNodes.length, 1);
  } finally {
    Object.assign(globalThis, saved);
  }
});
