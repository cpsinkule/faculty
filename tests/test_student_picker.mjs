import assert from "node:assert/strict";
import test from "node:test";
import { studentPicker } from "../static/student-picker.js";

// FRONTEND: a server UIN search can find a student outside the initial list. Choosing that result must keep its label.
class Control {
  constructor() { this.events = new Map(); this.attributes = new Map(); this.value = ""; this.hidden = true; this.style = {}; }
  addEventListener(type, listener) { this.events.set(type, listener); }
  fire(type, event = {}) { this.events.get(type)?.({ preventDefault() {}, stopPropagation() {}, ...event }); }
  setAttribute(key, value) { this.attributes.set(key, value); }
  getAttribute(key) { return this.attributes.get(key); }
  removeAttribute(key) { this.attributes.delete(key); }
  getBoundingClientRect() { return {width: 360, left: 10, top: 20, bottom: 50}; }
  querySelectorAll() { return []; }
  querySelector() { return null; }
  dispatchEvent() {}
  select() {}
}

test("a remotely found student stays named after selection and when the picker opens again", async () => {
  const saved = Object.fromEntries(["CSS", "innerWidth", "innerHeight", "addEventListener", "setTimeout", "clearTimeout"].map(key => [key, globalThis[key]]));
  const timers = [];
  Object.assign(globalThis, { CSS: { escape: value => value }, innerWidth: 1440, innerHeight: 900,
    addEventListener() {}, setTimeout: callback => { timers.push(callback); return timers.length; }, clearTimeout() {} });
  try {
    const input = new Control(), value = new Control(), list = new Control(), box = new Control();
    const nodes = { "[data-picker-value]": value, "[data-picker-text]": input, "[role=listbox]": list };
    box.querySelector = selector => nodes[selector];
    const found = { value: "remote-student", text: "Rivera, Ana · Section 501" };
    let resolveSearch;
    const chosen = [];
    const picker = studentPicker(String, { id: "student", label: "Student", value: "", groups: [],
      search: () => new Promise(resolve => { resolveSearch = resolve; }) });
    picker.bind({ querySelector: () => box }, selected => chosen.push(selected));
    input.fire("focus");
    input.value = "1234";
    input.fire("input");
    timers.shift()();
    resolveSearch([found]);
    await Promise.resolve();
    assert.match(list.innerHTML, /Rivera, Ana/);
    input.fire("keydown", { key: "Enter" });
    assert.equal(value.value, found.value);
    assert.equal(input.value, found.text);
    assert.deepEqual(chosen, [found.value]);
    input.fire("focus");
    assert.equal(input.placeholder, found.text);
    input.fire("keydown", { key: "Escape" });
    assert.equal(input.value, found.text);
  } finally { Object.assign(globalThis, saved); }
});
