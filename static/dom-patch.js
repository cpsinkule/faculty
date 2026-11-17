// Redrawing a polled page without rebuilding it (requirement, 2026-09-26).
// Replacing innerHTML on every poll threw away every element: the flow graph's moving dashes started over, thumbnails were decoded
// again, open lists and focus were lost, and the page went blank while the next answer was fetched. These helpers bring an element
// to new markup while keeping the elements that are still there: children are matched by position and tag, attributes and text are
// brought up to date, and anything else is replaced.
//
// Only listeners assigned as properties (`button.onclick = ...`) or delegated to an ancestor survive a patch safely: a caller binds
// again after patching, and a property assignment replaces the earlier handler. Elements bound with addEventListener must be named
// in `opaque` (a selector): they are always replaced whole, so binding them again binds a fresh element once.
//
// A list whose members come and go (a filtered list: "Pages that need a look" loses a page once it is identified) gives each item a
// `data-patch-key`. Keyed children are matched by key, not position: an item that stays is moved into place with what the
// instructor typed or opened in it, and one item's element is never re-used for another (a recovery form typed for one page must
// never end up posting to the next page's id). Keys are compared among siblings only.
//
// Content that never changes once drawn, and that a script has since built inside (an Assistant answer with its math set by
// KaTeX), carries `data-patch-static="<mark>"`: while the new markup has the same mark at that place, the element and everything
// in it are left untouched.

/**
 * @param {Element} target
 * @param {string} html
 * @param {string} [opaque]
 */
export function patchChildren(target, html, opaque = "") {
  const template = document.createElement("template");
  template.innerHTML = html;
  children(target, template.content, opaque);
}

/**
 * Bring `target` (same tag) to `source`: its attributes and its children.
 * @param {Element} target
 * @param {Element} source
 * @param {string} [opaque]
 */
export function patchElement(target, source, opaque = "") {
  if (!same(target, source, opaque)) { target.replaceWith(source); return; }
  attributes(target, source);
  children(target, source, opaque);
}

/** @param {Node} node */
const keyOf = (node) => node.nodeType === Node.ELEMENT_NODE ? /** @type {Element} */ (node).getAttribute("data-patch-key") : null;

/** An element's `data-patch-static` mark, or null. @param {Node} node */
const staticOf = (node) => node.nodeType === Node.ELEMENT_NODE ? /** @type {Element} */ (node).getAttribute("data-patch-static") : null;

/** @param {ParentNode} into @param {ParentNode} from @param {string} opaque */
function children(into, from, opaque) {
  const want = [...from.childNodes];
  // Each keyed child that is here now, by its key (the first one, should two share it); a key is claimed once.
  const keyed = new Map();
  for (const node of into.childNodes) { const key = keyOf(node); if (key !== null && !keyed.has(key)) keyed.set(key, node); }
  want.forEach((next, index) => {
    let old = into.childNodes[index] || null;
    const key = keyOf(next);
    if (key !== null) {
      const match = keyed.get(key);
      keyed.delete(key);
      // A new item goes in here; one that was elsewhere in the list moves here, keeping its state.
      if (!match) { into.insertBefore(next, old); return; }
      if (match !== old) into.insertBefore(match, old);
      old = match;
    } else if (old && keyOf(old) !== null) {
      // Plain markup never takes a keyed item's element: it goes in before it (the item moves on or is removed below).
      into.insertBefore(next, old);
      return;
    }
    if (!old) into.appendChild(next);
    else if (!same(old, next, opaque)) old.replaceWith(next);
    else if (next.nodeType === Node.ELEMENT_NODE) {
      // Finished content (an Assistant answer, whose math a script has drawn inside it) is left exactly as it is while its mark
      // stays the same; another mark at this place is other content and is patched as usual.
      const fixed = staticOf(next);
      if (fixed !== null && fixed === staticOf(old)) return;
      attributes(/** @type {Element} */ (old), /** @type {Element} */ (next));
      children(/** @type {Element} */ (old), /** @type {Element} */ (next), opaque);
    } else if (old.nodeValue !== next.nodeValue) old.nodeValue = next.nodeValue;
  });
  while (into.childNodes.length > want.length) into.childNodes[want.length].remove();
}

/** @param {Node} old @param {Node} next @param {string} opaque */
function same(old, next, opaque) {
  if (old.nodeType !== next.nodeType || old.nodeName !== next.nodeName) return false;
  if (old.nodeType !== Node.ELEMENT_NODE) return true;
  const element = /** @type {Element} */ (old);
  if (opaque && element.matches(opaque)) return false;
  // Another item of a keyed list is another element (patchElement on a keyed node lands here too).
  if (keyOf(old) !== keyOf(next)) return false;
  // An input that changes type is another control; keep none of it.
  return element.getAttribute("type") === /** @type {Element} */ (next).getAttribute("type") || element.nodeName !== "INPUT";
}

// Attributes the page's own scripts set and server markup never carries, kept through a patch: none today (motion.js's
// off-screen mark left with the cube graph, 2026-10-03); add one here if a page script starts setting such an attribute
// on patched elements, or the next poll strips it. A disclosure's open state is the instructor's: a patch may open a <details>, never close one the instructor opened
// (a poll that changed a count left "Recover using printed alias" folded over what was typed in it). Only while it is the
// same disclosure: one whose other attributes change (a list that moved, so another item's fold is now at this position)
// follows the new markup as before.
const CLIENT_OWNED = new Set();

/** @param {Element} old @param {Element} next */
function sameDisclosure(old, next) {
  if (old.nodeName !== "DETAILS" || !old.hasAttribute("open")) return false;
  const mine = [...old.attributes].filter((attribute) => attribute.name !== "open");
  const theirs = [...next.attributes].filter((attribute) => attribute.name !== "open");
  return mine.length === theirs.length && mine.every((attribute) => next.getAttributeNS(attribute.namespaceURI, attribute.localName) === attribute.value);
}

/** @param {Element} old @param {Element} next */
function attributes(old, next) {
  const keepOpen = sameDisclosure(old, next);
  for (const attribute of [...old.attributes]) {
    if (!next.hasAttributeNS(attribute.namespaceURI, attribute.localName) && !CLIENT_OWNED.has(attribute.name)
        && !(keepOpen && attribute.name === "open")) old.removeAttributeNS(attribute.namespaceURI, attribute.localName);
  }
  for (const attribute of [...next.attributes]) {
    if (old.getAttributeNS(attribute.namespaceURI, attribute.localName) !== attribute.value) old.setAttributeNS(attribute.namespaceURI, attribute.name, attribute.value);
  }
}
