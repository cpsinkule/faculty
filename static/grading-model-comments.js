import {mathText} from "./grading-math.js";

// Presentation of recorded authorship only. A Faculty flag is not a model comment
// merely because it mentions a model. Unknown authors keep the ordinary text color.
// A grade the flag review re-ticked (`grading/flag_resolution.py`, committee source "flag_resolution") or the multiple-choice
// reader scored (`grading/choice.py`, "choice_reader") carries that author's own notes, not a primary and a secondary grader's.
const NAMES = {primary: "Primary grader", second: "Secondary grader", verifier: "Additional reviewer",
  arbiter: "Final arbitrator", flag_reviewer: "Flag reviewer", flag_resolution: "Flag review", reader: "Reader",
  choice_reader: "Multiple-choice reader", jev: "Jev"};
export function modelRole(value) {
  const key = String(value || "").split(".").at(-1).replace(/_fallback$/, "");
  const role = ({secondary: "second", reviewer: "verifier", first: "primary", one_shot: "primary",
    final_arbitrator: "arbiter", tag_team_reviewer: "arbiter"})[key] || key;
  return Object.hasOwn(NAMES, role) ? role : "";
}

export function proposalRole(signals = {}) {
  // A reviewer may adopt another grader's wording or author a replacement. Use
  // the retained source, never the effective `models.primary` endpoint name.
  if (signals.tag_team) return signals.arbiter?.decision === "revise" && signals.tag_team.completed ? "arbiter" : "primary";
  if (signals.committee) return modelRole(signals.committee.source);
  return "primary";
}

export function modelLabel(role, label, h) {
  const key = modelRole(role);
  return `<strong class="grading-model-label"${key ? ` data-grading-role="${key}"` : ""}>${h.e(label || NAMES[key] || "Model")}</strong>`;
}

export function modelComment(role, text, h) {
  const key = modelRole(role);
  return key ? `<span class="grading-model-comment" data-grading-role="${key}">${mathText(text, h)}</span>` : mathText(text, h);
}

export function applicationLabel(applied, h, label = applied ? "apply" : "do not apply") {
  return `<strong class="grading-application-label" data-grading-applied="${applied ? "yes" : "no"}">${h.e(label)}</strong>`;
}

export function proposalComment(signals, text, h) {
  const role = proposalRole(signals), label = signals?.tag_team ? role === "arbiter" ? "Tag-Team reviewer" : "First grader"
    : signals?.one_shot ? "One-shot grader" : NAMES[role] || "Model";
  return `${modelLabel(role, `${label}:`, h)} ${modelComment(role, text, h)}`;
}

const PREFIXES = [
  ["primary", /^(?:primary|Primary grader|One-shot grader(?: \([^:\n]+\))?):(?=\s|$)/i],
  ["second", /^(?:second|secondary|Second grader(?: \([^:\n]+\))?|Secondary grader):(?=\s|$)/i],
  ["verifier", /^(?:verifier|reviewer|Additional reviewer|Adversarial reviewer):(?=\s|$)/i],
  ["arbiter", /^(?:arbiter|The final arbitrator[^:\n]+|Tag-Team reviewer):(?=\s|$)/i],
  ["flag_reviewer", /^(?:flag_reviewer|The flag reviewer [^:\n]+):(?=\s|$)/i],
  ["reader", /^(?:Reader|Uncertain reading of [^:\n]+|Uncertain layout from [^:\n]+):(?=\s|$)/i],
  ["jev", /^Jev could not break the tie:(?=\s|$)/i],
];
/** Which role a review reason is written by, from its recorded prefix ("primary:", "Uncertain reading of …:"), or "". */
export function reasonRole(value) {
  const text = String(value || "");
  return PREFIXES.find(([, pattern]) => pattern.test(text))?.[0] || "";
}

export function reviewReasonMarkup(value, h) {
  const text = String(value || "");
  for (const [role, pattern] of PREFIXES) {
    const prefix = text.match(pattern)?.[0];
    if (prefix) return `${modelLabel(role, prefix, h)}${modelComment(role, text.slice(prefix.length), h)}`;
  }
  return mathText(text, h);
}
