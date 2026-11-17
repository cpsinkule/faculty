// Workspace preferences only. These views edit a draft of the existing settings.
import {recentModels, RECENT_MODEL_LIMIT, MODEL_PROVIDERS, providersFor, selectedProvider, isSubscription, planName, PLANS} from "./grading-model-picker.js";
// Six sections of settings, each short enough to read without hunting (requirement, 2026-10-03): the grading
// mode and its Cloud team; the models outside the team; the connections they run on; the review policy; the limits; and availability and costs.
export const SECTIONS = [["defaults", "Defaults", "The grading mode for new batches and the Cloud team that grades them"],
  ["models", "Other models", "Rubric drafting, the assistant, the coding agent and the benchmark"],
  ["connections", "Connections", "Models and providers"], ["review", "What holds a response back", "The review policy, checks and spot-checks"],
  ["advanced", "Advanced", "Request limits, retries and local compute"],
  ["availability", "Availability and costs", "Whether each compute service answers now, and what grading has cost"]];
export const TIERS = [["cloud_standard", "Cloud"]];
// The grading schemes, in the order the picker offers them. Choosing one shows its own models and options directly below it.
// Cloud (internally `cloud_standard`) and the local mode are the only ones (requirement, 2026-09-23).
export const SCHEMES = [["cloud_standard", "Cloud", "Two independent graders, an arbitrator for disagreements, and a reviewer for other flags."],
  ["local", "Fully local", "Read and grade on this machine."]];
export const ROLES = [["reader", "Handwriting reader", "Reads the image without the rubric or solution."],
  ["primary", "Primary grader", "Grades the image and transcript against the rubric."],
  ["second", "Second grader", "Grades independently and never sees the primary grader’s decisions. Text-only models get the transcript, not the crops."],
  ["verifier", "Additional reviewer · final arbitrator", "Called only for a grading disagreement. Adopts either grade, writes a new one, or sends the response to your review."],
  ["flag_reviewer", "Flag reviewer", "Reviews other flagged responses: upholds the standing grade or leaves it for your review."]];
export const BLANK = {name: "", kind: "hosted", base_url: "https://openrouter.ai/api/v1", key_env: "OPENROUTER_API_KEY", model: "", lineage: "", modality: "vision", structured: "json_schema",
  reasoning: false, reasoning_tokens: 8192, max_tokens: 8192, price_in: 0, price_out: 0, upstream: "", aggregator: true, batch_api: false, max_inflight: 256, profile: "", enabled: true, notes: ""};
// What a role may ask of a connection about thinking, from the catalogue facts saved with it; the server's
// config.reasoning_choices/reasoning_default are the authority and these mirror them for display. null: nothing is
// known (a manual connection, or one saved before the facts existed), so the connection's own tick decides.
export const EFFORTS = ["minimal", "low", "medium", "high", "xhigh", "max"];
export const REASONING_LABELS = {off: "No reasoning", on: "Reasoning on", minimal: "Minimal effort", low: "Low effort", medium: "Medium effort", high: "High effort", xhigh: "Extra-high effort", max: "Maximum effort"};
export function reasoningChoices(endpoint) {
  const facts = endpoint?.reasoning_options;
  if (!facts) return null;
  if (!facts.supported) return ["off"];
  const levels = EFFORTS.filter(effort => facts.efforts.includes(effort));
  return [...(facts.mandatory ? [] : ["off"]), ...(levels.length ? levels : ["on"])];
}
export function reasoningDefault(endpoint) {
  const choices = reasoningChoices(endpoint);
  if (!choices) return null;
  const levels = choices.filter(choice => choice !== "off");
  if (!levels.length || (!endpoint.reasoning && choices.includes("off"))) return "off";
  if (!endpoint.reasoning) return levels[0];
  const preferred = endpoint.reasoning_options.default_effort;
  return levels.includes(preferred) ? preferred : levels[Math.floor(levels.length / 2)];
}
// One possible configuration is simply used, and said; several are a choice beside the model.
export function reasoningMarkup(endpoint, name, chosen, h) {
  const choices = reasoningChoices(endpoint);
  if (!endpoint) return "";
  if (!choices) return `<p class="compute-reasoning-fixed" data-reasoning-fixed>Reasoning follows this connection’s own setting.</p>`;
  if (choices.length === 1) return `<p class="compute-reasoning-fixed" data-reasoning-fixed>${choices[0] === "off" ? "Never thinks" : choices[0] === "on" ? "Always thinks" : `Always thinks · ${h.e(REASONING_LABELS[choices[0]])}`}</p>`;
  const current = choices.includes(chosen) ? chosen : reasoningDefault(endpoint);
  return h.select("Reasoning", `reasoning-${name}`, choices.map(choice => [choice, `${REASONING_LABELS[choice]}${endpoint.reasoning_options.mandatory && choice === choices[0] ? " (least)" : ""}`]), current);
}
export const NUMBERS = ["reasoning_tokens", "max_tokens", "price_in", "price_out", "max_inflight"], FLAGS = ["reasoning", "aggregator", "batch_api", "enabled"];

const fold = (id, title, description, content) => `<details class="compute-fold" data-compute-fold="${id}"><summary><span><strong>${title}</strong><small>${description}</small></span></summary><div class="compute-fold-body">${content}</div></details>`;
const section = (title, content) => `<section class="compute-card"><h3>${title}</h3>${content}</section>`;
// One sentence of help under each switch; the evidence and the measurements behind it sit in a closed "Why" (grun:P13).
const why = text => text ? `<details class="compute-why"><summary>Why</summary><p>${text}</p></details>` : "";
const toggle = (h, title, name, checked, help, reason = "") => `<div class="compute-toggle">${h.check(title, name, checked)}<p>${help}</p>${why(reason)}</div>`;

// "Next Cloud batch uses": each Cloud role with the model the draft names and every option a batch would use with it (instructor,
// 2026-09-27: the settings chosen replace the connection's test state). `known(name)` gives the connection and the draft's choices;
// a problem that would stop a batch (no model, a key not set or not usable for its host, a failed test) is still said beside them.
const money4 = value => `$${Number(value).toLocaleString(undefined, {maximumFractionDigits: 4})}`;
export function teamSettings(endpoint, facts, h) {
  const subscription = isSubscription(endpoint);
  const provider = subscription ? planName(endpoint) : endpoint.kind === 'dedicated' ? 'Dedicated GPU'
    : endpoint.name === 'nebius' ? 'Nebius (built-in)' : /openrouter\.ai/.test(endpoint.base_url || '') ? 'OpenRouter' : /ai-gateway\.vercel\.sh/.test(endpoint.base_url || '') ? 'Vercel AI Gateway' : 'Other connection';
  const choices = reasoningChoices(endpoint), chosen = choices ? (choices.includes(facts.reasoning) ? facts.reasoning : reasoningDefault(endpoint)) : null;
  const reasoning = choices ? `${REASONING_LABELS[chosen]}${facts.reasoning && choices.includes(facts.reasoning) ? '' : ' (default)'}` : endpoint.reasoning ? 'Reasoning on (connection setting)' : 'No reasoning (connection setting)';
  const bundle = endpoint.bundle_size ?? (subscription ? 6 : 1);
  const items = [
    ['Provider', provider + (endpoint.upstream ? ` · host ${endpoint.upstream}` : /openrouter|vercel/i.test(provider) ? ' · any host' : '')],
    ['Reasoning', reasoning],
    ['Responses per call', bundle === 1 ? '1 (each alone)' : String(bundle)],
    subscription ? ['Concurrency', `${endpoint.sessions ?? 2} call${(endpoint.sessions ?? 2) === 1 ? '' : 's'} at once`] : ['Requests in flight', facts.full ? 'No limit (Full concurrency)' : String(endpoint.max_inflight ?? 256)],
    ['Answer limit', subscription ? "The model's own maximum" : facts.limits ? `${Number(endpoint.max_tokens).toLocaleString()} tokens per response${chosen && chosen !== 'off' ? ` + ${Number(endpoint.reasoning_tokens).toLocaleString()} reasoning` : ''}` : endpoint.output_limit ? `Up to ${Number(endpoint.output_limit).toLocaleString()} tokens (limits off)` : "The host's default (limits off)"],
    ['Price', subscription ? 'No API billing' : endpoint.price_in == null && endpoint.price_out == null ? 'Unknown' : `${money4(endpoint.price_in)} / ${money4(endpoint.price_out)} per 1M`],
    ...(facts.fallback ? [['Fallback', facts.fallback]] : []),
  ];
  return `<dl class="compute-team-settings">${items.map(([label, value]) => `<div><dt>${h.e(label)}</dt> <dd>${h.e(value)}</dd></div>`).join('')}</dl>`;
}
export function teamSummaryMarkup(models, known, h) {
  const rows = ROLES.map(([role, label]) => {
    const name = models[role] || "", facts = name ? known(name, role) : null;
    const problem = !name ? "No model chosen" : facts?.unusable ? "Key not usable for this host" : facts?.key === false ? "Key not set" : facts?.test === "failed" ? "Test failed" : "";
    const settings = facts?.endpoint ? teamSettings(facts.endpoint, facts, h) : "";
    // The role's name opens its controls below, where a problem named here is fixed.
    return `<tr data-team-role="${h.e(role)}" data-grading-role="${h.e(role)}"><th scope="row"><button type="button" class="compute-role-jump" data-jump-role="${h.e(role)}" title="Go to its model">${h.e(label.split(" · ")[0])}</button></th><td>${name ? `<strong>${h.e(name)}</strong>${facts?.model ? `<small>${h.e(facts.model)}</small>` : ""}` : "—"}</td><td>${problem ? `<span class="grading-chip grading-chip-${!name || facts?.key === false ? "attention" : "failed"}">${h.e(problem)}</span>` : ""}${settings}</td></tr>`;
  }).join("");
  return `<table class="compute-team-summary"><caption>Next Cloud batch uses</caption><thead><tr><th scope="col">Role</th><th scope="col">Model</th><th scope="col">Settings</th></tr></thead><tbody>${rows}</tbody></table>`;
}
export function modelChoices(s, current, role, provider = "") {
  return [["", role === 'rubric_reviewer' && (!provider || provider === selectedProvider(s, '', role)) ? 'Use the Cloud primary grader' : role === 'assistant_model' ? 'None (assistant off)' : role === 'coding_model' ? 'Claude Code (its default model)' : "None"], ...recentModels(s, current, role, provider).map(name => {
    let label = name === 'nebius' ? 'Nebius (built-in)' : name;
    if (role === 'second') label += ` · ${name === 'nebius' || s.endpoints.find(e => e.name === name)?.modality === 'vision' ? 'images + transcript' : 'transcript only'}`;
    return [name, label];
  })];
}
const providerControl = (h, name, provider, role = '') => `<div class="compute-provider">${h.select('Provider', `provider-${name}`, providersFor(role), provider)}</div>`;
// Keep the role's accessible name while giving each control a short visible label.
const modelControl = (s, h, label, name, current, role, provider) => `<label class="field">Model<select name="${h.e(name)}" aria-label="${h.e(label)}">${modelChoices(s, current, role, provider).map(([value, title]) => `<option value="${h.e(value)}" ${value === current ? 'selected' : ''}>${h.e(title)}</option>`).join('')}</select></label>`;
const modelSelector = (s, h, label, role, tier = "", title = "", providers = {}) => {
  const name = tier ? `tier-${tier}-${role}` : role, current = tier ? s.tiers[tier][role] : s[role];
  const provider = providers[name] || selectedProvider(s, current, role);
  return `<div class="compute-model-selector" data-model-field="${name}" data-role="${role}" data-tier="${tier}">${providerControl(h, name, provider, role)}<div class="compute-model-choice">${modelControl(s, h, label, name, current, role, provider)}${h.button('browse-role-models', `Browse ${MODEL_PROVIDERS[provider].label}`, `data-role="${role}" data-tier="${tier}" data-field="${name}" aria-label="Browse models for ${h.e(title ? title + ' ' : '')}${h.e(label)}"`)}</div><div class="compute-model-options">${tier || ['rubric_reviewer', 'assistant_model', 'coding_model'].includes(role) ? `<div class="compute-reasoning" data-reasoning-slot="${name}"></div>` : ""}<div class="compute-host" data-host-slot="${name}" data-host-label="${h.e(label)}"></div></div></div><p class="compute-model-price" id="${name}-price" data-model-price="${name}" aria-live="polite" hidden></p><p class="grading-model-warning" id="${name}-scan-warning" data-model-scan-warning="${name}" aria-live="polite"></p>`;
};

// An optional spare for a grader, used only for a request that exhausted its attempt cap, then dropped again at once.
export const FALLBACK_ROLES = ["reader", "primary", "second"];
const FALLBACK_LABEL = {reader: "handwriting reader", primary: "primary grader", second: "second grader"};
const fallbackSelector = (s, h, tier, role, title, providers) => {
  const name = `fallback-${tier}-${role}`, current = (s.role_fallbacks?.[tier] || {})[role] || "", what = FALLBACK_LABEL[role];
  const provider = providers[name] || selectedProvider(s, current);
  return `<details class="compute-fold compute-fallback" data-compute-fold="${name}" ${current ? "open" : ""}><summary><span><strong>Fallback ${what}</strong><small>${current ? "On failure, retries once with " + h.e(current) : "Optional"}</small></span></summary><div class="compute-fold-body">`
    + `<div class="compute-model-selector" data-model-field="${name}" data-role="${role}" data-tier="${tier}" data-fallback="true">${providerControl(h, name, provider)}<div class="compute-model-choice">${modelControl(s, h, `Fallback for ${title} ${what}`, name, current, role, provider)}${h.button('browse-role-models', `Browse ${MODEL_PROVIDERS[provider].label}`, `data-role="${role}" data-tier="${tier}" data-field="${name}" aria-label="Browse fallback models for ${h.e(title)} ${what}"`)}</div><div class="compute-model-options"><div class="compute-host" data-host-slot="${name}" data-host-label="Fallback for ${h.e(title)} ${what}"></div></div></div>`
    + `<p class="compute-model-price" id="${name}-price" data-model-price="${name}" hidden></p><p class="grading-model-warning" id="${name}-scan-warning" data-model-scan-warning="${name}"></p>`
    + `<p class="supporting-copy">Asked once, only after a request to your ${what} failed its last retry or stayed rate limited. The next response goes back to your ${what}; it costs nothing while that one works, and uses its own default reasoning.</p></div></details>`;
};

export function settingsMarkup(s, profiles, selected, h, providers = {}) {
  const CLOUD = ["cloud_standard"];
  // A scheme's own choices sit under the picker and appear with it. Every block stays in the form while it is hidden, so
  // switching schemes loses no draft and `capture` still reads each tier's models; only the picked scheme is shown.
  const shown = modes => (modes.includes(s.default_tier) ? "" : "hidden");
  const scheme = (modes, content) => `<div class="compute-scheme" data-scheme-for="${modes.join(" ")}" ${shown(modes)}>${content}</div>`;
  const team = (tier, title) => `<section class="compute-card compute-team"><header class="compute-team-heading"><div><h3 tabindex="-1">${title} team</h3><p>Two independent graders, then the reviewer a response needs.</p></div></header>${ROLES.map(([role, label, help]) =>
    `<div class="compute-role" data-compute-role="${role}"><div class="compute-role-description"><h4>${label}</h4><p>${help}</p></div><div class="compute-role-controls">${modelSelector(s, h, label, role, tier, title, providers)}${!s.tiers[tier][role] ? '<p class="grading-warning">Choose a model before starting a Cloud batch.</p>' : ''}${FALLBACK_ROLES.includes(role) && s.tiers[tier][role] ? fallbackSelector(s, h, tier, role, title, providers) : ''}</div></div>`).join('')}</section>`;
  const defaults = `<section class="compute-card compute-summary" data-team-summary></section>` + section("Grading mode for new batches", `<fieldset class="compute-modes"><legend class="sr-only">Default tier for new batches</legend>${SCHEMES.map(([key, title, description]) =>
    `<label class="compute-mode"><input type="radio" name="default_tier" value="${key}" ${s.default_tier === key ? "checked" : ""}><span><strong>${title}</strong><small>${description}</small></span></label>`).join("")}</fieldset><p class="supporting-copy">A batch can still choose another mode when it starts, including Mathpix + local grading.</p><div class="compute-toggles">${toggle(h, "Start grading batches by themselves", "auto_grade", s.auto_grade !== false,
      "Once new, reset or changed responses have stayed the same for about two minutes, they are graded in this mode. Off: you start every batch.",
      "A question is graded only while its rubric is approved, its answer areas are confirmed and its rubric finalization stands. No batch starts while another batch of the assignment runs, or after its last batch was cancelled or reset: starting a batch yourself resumes it. Flagged responses still wait for your review.")}`
    + toggle(h, "Run the flag review by itself", "auto_flag_review", s.auto_flag_review !== false,
      "On: about two minutes after a grading batch finishes, the Rubric drafting and review model rules on that batch's flagged grades, exactly as the Review step's Ask button would, so you see only what it leaves. Off: it runs only when you press that button.",
      "Blank answers, grades with no line ticked, incomplete pairs and anything your rubric finalization flags are never sent. A grade is sent once: what a review leaves, fails on or is stopped before stays with you and the button. Not after a batch you cancelled or reset, and not for batches that finished more than a day ago.")
    + toggle(h, "Certify late papers by themselves", "auto_certify", s.auto_certify !== false,
      "On: once an assignment is certified, a student's late paper (a makeup, a DR exam, a paper that came after) is certified by itself when it is graded and nothing of it is still flagged, and goes to Gradescope the way the assignment's papers went. A makeup that ends without a paper is given its 0 the same way, and a student given 0 who is then owed a makeup is left out. Off: you certify again yourself.",
      "Only students with no certified grade of their own work: a rescan that changes a certified grade waits for you, and so does a paper that is still flagged, has a part missing, or meets a value you typed in the gradebook.")
    + toggle(h, "Send to Gradescope and publish after certifying", "auto_gradescope", s.auto_gradescope !== false,
      "On: the Certify dialog offers “Send to Gradescope and publish the grades”, ticked. Faculty then makes the assignment on Gradescope where a section has none, sends what is missing, and publishes each section; a late paper it certifies is published where you had already published. Off: you send and publish yourself.",
      "A section is published only when every paper there is fully graded as Faculty has it; otherwise it stays unpublished and Certify says why. Faculty never unpublishes and never emails students. Untick the box in the Certify dialog to keep one assignment back; turning this off also stops anything still waiting to go.")
    + `<div data-scheme-for="cloud_standard" data-direct-reading ${shown(["cloud_standard"])}>${toggle(h, "Read the handwriting without OCR", "direct_reading", s.direct_reading,
      "Cloud only: the Cloud reader reads each crop itself instead of GLM-OCR and Mathpix, at one extra reader call per response.",
      "Measured on 12 hard readings checked by eye: it stops a reader copying the printed question’s number over the digits a student actually wrote. It leaves no independent reading beside the reader, so the two checks that ask for one are waived and recorded on each response. Gemini 3.8 Flash gained most as the reader; qwen3-vl-32b gains little. An answer area with nothing written on it is kept as a blank answer and waits for your review; it is not a failed reading.")}
      ${toggle(h, "Read multiple choice by the marked option", "choice_reader", s.choice_reader,
        "Cloud only: a multiple-choice question (its rubric is the drafted Correct choice line and the line for any other answer, its options printed) is read by OpenAI Decisions, one request per answer area, and graded from the marked option. Set a question to written work at Key &amp; Rubric Finalization.",
        "A read counts only when both option orders name the same option with confidence of at least the calibrated cutoff; otherwise, or when nothing or several options are marked, the response is read and graded as written work. Such a grade is final without a second grader, as you chose; every other check still applies.")}
      ${toggle(h, "Read beyond a cut-off answer area", "reading_page_recovery", s.reading_page_recovery,
        "With direct reading: let the reader request one whole-page look when an answer continues outside its area.",
        "Off by default while this is evaluated on representative scans. It adds reader calls only when context is requested, preserves the first reading, and retains the page and added writing for review. Unresolved context still raises a reading concern under your review policy. Whole pages may include a student's name. Masked areas keep their existing review path.")}</div></div>`)
    + scheme(["local"], section("Fully local", `<p class="supporting-copy">Reading and grading run on this machine, with no cloud request. Model names, runtimes and the built-in Nebius connection are in ${h.button("local-settings", "Advanced settings")}.</p>`))
    + scheme(CLOUD, team("cloud_standard", "Cloud"))
    + scheme(CLOUD, `<details class="compute-why"><summary>How the Cloud team decides</summary><p>Agreement compares rubric selections, not just totals. The additional reviewer arbitrates disagreements permanently; its flags always go directly to manual review. The flag reviewer reviews other flags and can only uphold the standing grade or request your review. Readers and both reviewers need image support.</p><p>Responses to the same question and exam version at the same step can share one call, up to a role’s responses per call; the question and its rubric are sent once, and each response still gets its own answer and checks. A role’s host and responses per call belong to its connection, so every role using that connection changes with it.</p><p>Dropdowns show your ${RECENT_MODEL_LIMIT} most recent choices; choose a provider to filter them and Browse its catalogue. Other connections keeps manually configured providers available.</p></details>`)
    + `<div data-reasoning-facts></div>`
    + section("Handwriting readers", `<div class="compute-default-grid">${toggle(h, "Use local handwriting recognition", "glm_enabled", s.glm_enabled, "GLM-OCR runs on this machine.")}${toggle(h, "Use Mathpix", "mathpix_enabled", s.mathpix_enabled, "Adds a Mathpix reading in hybrid and Cloud batches. Uses your configured account.")}</div>`);
  // Rubric preparation, the assistant, the coding agent and the benchmark reference belong to no grading mode: their own section.
  const models = section('Rubric drafting and review', modelSelector(s, h, 'Rubric drafting and review model', 'rubric_reviewer', '', '', providers) + '<p class="supporting-copy">One image-capable model drafts rubrics from blank templates and reviews rubrics before grading; it never changes your rubrics’ wording or scores.</p><details class="compute-why"><summary>Why</summary><p>One model does both rubric jobs, whichever mode grades the responses. It <strong>drafts</strong> a version’s questions and rubrics from its blank template when you ask for that under Rubrics &amp; Templates, for any assignment (exam, quiz, homework or handout), and you edit every draft before a question is created; drafting reads the template’s pages, so it needs a model that reads images. It <strong>reviews</strong> the assignment’s rubrics, problems and answer keys before grading, suggesting clarifications and possible errors for you to confirm in Key &amp; Rubric Finalization; the review never changes your rubrics’ wording or scores. A model with stronger reasoning suits both jobs.</p></details>'
      // The rubric check while a quiz is drafted (requirement, 2026-10-11): a revision's last step, or left to the approval.
      + toggle(h, "Check the rubrics while a quiz is drafted", "draft_rubric_check", s.draft_rubric_check !== false,
        "On: each revision of a quiz draft ends with the rubric check Key &amp; Rubric Finalization would run, so you answer its decisions in the draft and approving can record the finalization. Off: a revision ends once its rubrics and answer keys are written, and the check runs after you approve.",
        "The check is the same either way; the switch only moves the wait. Measured on a quiz of three problems in four versions, it added about 7.5 minutes to a first revision. A later revision asks again only about the problems that changed, but each revision you replace has paid for its own check. A draft already checked keeps its check, and a check that fails while drafting is a warning: the approval then starts it as it does with the switch off."))
    + section('Faculty assistant', modelSelector(s, h, 'Assistant model', 'assistant_model', '', '', providers) + '<p class="supporting-copy">The model behind the Assistant (top bar, Ctrl/⌘ I). It makes the changes you let it make by itself; everything else waits for your Approve.</p><details class="compute-why"><summary>Which models</summary><p>Any connection works: your Claude subscription (run by Claude Code), your Gemini subscription (run by Antigravity), your ChatGPT subscription or an OpenRouter or Vercel model (both run by Codex; gateway models are billed per token by that account, and a pinned host is not applied), or another compatible host. The model reads Faculty through its own pages and sees what your pages show, student names and grades included; credentials and card links are never sent. Changes to calendar events, TA contacts, content drafts, gradebook columns and due dates, and retries of failed work are made at once; every other change is an Approve card in the assistant. A model that is good with tools and long answers suits it best.</p></details>')
    + section('Coding agent', modelSelector(s, h, 'Coding agent', 'coding_model', '', '', providers) + '<p class="supporting-copy">When you ask the assistant to change Faculty itself, it hands the change to this agent: Claude Code on your Claude subscription, or Codex on your ChatGPT subscription or an OpenRouter or Vercel model. Leave it empty for Claude Code’s default model.</p>')
    + section("Benchmark model", modelSelector(s, h, "Benchmark reference model", "yardstick", "", "", providers) + '<p class="supporting-copy">A separate reference for comparing grading quality.</p>');
  const connections = `<section class="compute-card"><header class="compute-card-heading"><div><h3>Model connections</h3><p class="supporting-copy">Browse OpenRouter or Vercel AI Gateway to fill in a connection, choose a subscription (Claude, ChatGPT or Gemini) to grade on your own plan, or add one manually.</p></div><div class="grading-actions">${providerControl(h, "connections", providers.connections || "openrouter")}${h.button("browse-models", `Browse ${MODEL_PROVIDERS[providers.connections || "openrouter"].label}`)}${h.button("add-endpoint", "Add connection")}</div></header><div data-endpoints></div><div class="compute-connection-footer">${h.button("connection-status", "Refresh status")}<span class="supporting-copy">Tests use the saved connection and send a small synthetic request.</span></div></section>
    <p class="supporting-copy">Local models and the built-in Nebius connection are in ${h.button("local-settings", "Advanced settings")}.</p>`;
  const review = section("Review policy", `<p class="supporting-copy">Which failed checks keep a response for you instead of finalizing it.</p><fieldset class="compute-modes"><legend class="sr-only">Which checks require your review</legend>${[
      ["all_checks", "Every check", "Any failed check keeps the response for you: reading problems, incomplete evidence, key mismatches, a grader asking for a look, and the rest."],
      ["disagreement_and_work", "Disagreement, and full credit on little work", "Only two things keep a response for you: the grading team did not agree, and full credit was proposed where the rubric wants work and little is visible. Every other check still runs and is shown on the response, but no longer blocks."]]
      .map(([key, title, description]) => `<label class="compute-mode"><input type="radio" name="risk-review_policy" value="${key}" ${(s.risk.review_policy || "all_checks") === key ? "checked" : ""}><span><strong>${title}</strong><small>${description}</small></span></label>`).join("")}</fieldset>${why("Measured on 235 real responses of one exam: of the 121 the narrow policy would waive, reading all 13 disputed papers by hand found 3 points of certain error and about 4 more of debatable generosity, against 87 fewer reviews. A selection that cannot be scored, and your own confirmed rubric finalization, are never waived.")}`)
    + section("Checks on proposed grades", toggle(h, "Check the written math", "symbolic_enabled", s.symbolic_enabled, "Run symbolic checks on the written work and equivalent answer forms."))
    + fold("risk", "Acceptance requirements", "Answer keys and evidence of work. Reviewers record why a grade can stand.", toggle(h, "Require a confirmed answer-key match for full credit", "risk-require_key_match", s.risk.require_key_match, "Only full-credit proposals must match a confirmed answer key. Proposals below full credit still undergo all other checks. A key match never awards credit by itself.")
      + toggle(h, "Require supporting work for full credit", "risk-require_work_shown", s.risk.require_work_shown, "Only full-credit proposals need sufficient supporting work when the rubric requires it. Missing work alone does not block proposals below full credit; all other checks still apply.")
      + '<p class="supporting-copy">The arbitrator can adopt or revise a disputed grade. Its unresolved concerns always require manual review. The flag reviewer can uphold an unchanged standing grade; neither reviewer can override your confirmed rubric rules or your own review.</p>')
    + fold("thresholds", "Confidence thresholds", "Model-reported signals, not measured probabilities of a correct grade.", `${toggle(h, "Use model-reported confidence to flag responses", "risk-confidence_checks", s.risk.confidence_checks !== false, "When off, a confidence number a model reports about itself sends nothing to review or extra checking and keeps nothing from acceptance. What a model names (an ambiguity, an unclear mark, a review reason), reader agreement, grader disagreement and every contradiction still do. The values below are kept and still recorded with each response.")}<div class="grading-columns" data-confidence-thresholds>${[
      ["risk-min_confidence", "Reported grader confidence for extra checking", s.risk.min_confidence], ["risk-min_ocr_agreement", "Reader agreement for acceptance", s.risk.min_ocr_agreement],
      ["risk-min_ocr_rate", "Mathpix per-symbol confidence", s.risk.min_ocr_rate], ["ocr_threshold", "OCR provider confidence", s.ocr_threshold],
      ["visual_threshold", "Reported visual reading confidence", s.visual_threshold], ["grading_threshold", "Reported grading confidence for review", s.grading_threshold]]
      .map(([key, label, value]) => h.field(label, key, value, "number", `min="0" max="1" step="any"${key === "risk-min_ocr_agreement" ? "" : " data-confidence-field"}`)).join("")}</div><p class="supporting-copy">These 0–1 values are model-reported confidence, not measured accuracy. Thresholds stay fixed until you change them. Review → Regrading and diagnostics → Compare with approved work shows observed corrections by confidence range and review flag. Missing evidence and unresolved ambiguity still require review.</p>`)
    + fold("trial", "Local and older workflows", "Trial acceptance: an optional measurement; new cloud teams finalize passing results automatically.", toggle(h, "Test automatic-acceptance rules", "trial_acceptance", s.trial_acceptance, "Trial results are never final grades and are not exported as final.")
      + `<div class="grading-columns">${h.field("Blind share of trial acceptances", "blind_share", s.blind_share, "number", 'min="0" max="1" step="0.05"')}${h.field("Error limit for real acceptance (upper 95% bound)", "error_limit", s.error_limit, "number", 'min="0.0001" max="0.5" step="any"')}${h.field("Audit share once real acceptance is on", "audit_share", s.audit_share, "number", 'min="0.01" max="1" step="0.01"')}</div><p class="supporting-copy">For local and legacy workflows, enable benchmark-based automatic acceptance per question from Review → Trial acceptance only after your blind reviews meet the error limit. Changed inputs or a failed audit end or suspend that permission.</p>`);
  const caps = (s.endpoints || []).map((e, i) => `<div class="compute-cap-row"><p><strong>${h.e(e.name || "Unnamed")}</strong><small>${h.e(e.model || "No model ID")}</small></p>${[["max_tokens", "Answer tokens", 256], ["reasoning_tokens", "Reasoning tokens", 0]].map(([key, label, min]) =>
    `<label class="field"><span class="compute-cap-label field-label">${label}</span><input type="number" name="endpoint-${i}-${key}" aria-label="${label} · ${h.e(e.name || i)}" value="${h.e(e[key])}" min="${min}" max="65536" step="1" required></label>`).join("")}</div>`).join("");
  const requests = section("Cloud requests", `<div class="compute-toggles">${toggle(h, "Full concurrency", "full_concurrency", s.full_concurrency, "Start every cloud request as soon as it is ready, setting aside the in-flight limits.",
        "The components-in-flight limit and each connection’s own limit are set aside. A host that answers “too busy” still slows its own connection down, answer areas are still cut a few at a time, and Mathpix keeps its per-minute pace. Each cloud batch shows an estimated cost before it starts, and every request’s cost is recorded. Spending limits are your OpenRouter and Vercel accounts’ own: a request a gateway refuses for credit fails at once, and you can retry it after raising the limit.")
      + toggle(h, "Limit how long a model may write or think", "token_limits", s.token_limits, "On: each request uses its connection’s own answer and reasoning caps, set below; off: the most its host allows.",
        "Each OpenRouter or hosted model you assign to a team keeps its own caps, not one shared number. The model is not told the numbers and is not asked to be brief. Off, each request asks for the most its host is published to allow (its answer limit, shown under Host), and the host stops it there. A thinking request gets both caps: the answer keeps its own room beside reasoning.")}</div>`
      + `<div class="compute-caps" data-token-limit-fields ${s.token_limits ? "" : "hidden"}>${caps ? `<div class="compute-cap-row compute-cap-head" aria-hidden="true"><span>Connection</span><span>Answer tokens</span><span>Reasoning tokens</span></div>${caps}` : '<p class="supporting-copy">Add a model connection, then set its caps here.</p>'}</div>`);
  const advanced = requests + fold("throughput", "Batch limits and retries", "Concurrency, timeouts, retry limits and cost estimates.", `<div class="grading-columns">${[
      ["max_batch_responses", "Responses per batch", 'min="1" max="20000"'], ["max_cloud_jobs", "Cloud batches at once", 'min="1" max="32"'],
      ["concurrency", "Cloud components in flight", 'min="1" max="256"'], ["mathpix_per_minute", "Mathpix requests per minute", 'min="1" max="5000"'],
      ["timeout", "Request timeout (seconds)", 'min="10" max="600"'], ["max_retries", "Attempt cap for confirmed failures", 'min="0" max="10"'],
      ["unknown_resends", "Resends when the outcome is unknown", 'min="0" max="5"'], ["cost_per_response", "Custom estimate per response ($)", 'min="0" max="100" step="0.001"']]
      .map(([key, label, limits]) => h.field(label, key, s[key], "number", limits)).join("")}</div><p class="supporting-copy">A custom estimate of 0 uses model prices. Model connections have no request timeout: an answer arrives only when the model has finished writing, however long it thinks, and a dropped connection is still noticed. The request timeout applies to Mathpix, dedicated GPUs and local models, and is how long a restart waits before resending an interrupted request. Confirmed failures retry up to the attempt cap. Unknown outcomes use the provider’s job ID when available, then the resend limit. Keep Mathpix’s pace within your account’s limit.</p>`)
    + fold("local", "Local models", "Model names and runtimes on this machine.", `<div class="grading-columns">${h.field("Visual verifier model", "local_verifier_model", s.local_verifier_model)}${h.field("Grading model", "local_grader_model", s.local_grader_model)}${h.select("Verifier backend", "local_verifier_backend", [["compatible", "Compatible chat API"], ["ollama", "Ollama"]], s.local_verifier_backend)}${h.select("Grader backend", "local_grader_backend", [["compatible", "Compatible chat API"], ["ollama", "Ollama"]], s.local_grader_backend)}</div><p class="supporting-copy">Loopback addresses are set on the server with FACULTY_GRADING_VERIFIER_URL and FACULTY_GRADING_GRADER_URL. Local GPU work runs serially; Ollama unloads between worker turns.</p>`)
    + fold("nebius", "Nebius built-in connection", "Optional provider available to Cloud.", `<div class="grading-columns">${h.field("Cloud model", "nebius_model", s.nebius_model)}${h.select("Compute", "nebius_compute", [["hosted", "Hosted inference"], ["managed_endpoint", "Faculty-managed inference endpoint"]], s.nebius_compute)}</div><p class="supporting-copy">Uses NEBIUS_API_KEY and optionally NEBIUS_GRADING_URL on the server.</p>`)
    + fold("dedicated", "Dedicated GPU provider", "Optional infrastructure for specialist or custom models.", toggle(h, "Enable dedicated GPU connections", "dedicated_enabled", s.dedicated_enabled, "Enables the dedicated connections configured under Connections.")
      + h.area("Resource profiles (JSON)", "dedicated_profiles", profiles) + '<p class="supporting-copy">Resource profiles describe the GPU, memory, runtime and scaling. Saving preferences does not provision a GPU.</p>');
  // Read only when the section is opened (grading-settings.js `loadAvailability`); nothing here is saved.
  const availability = '<div data-compute-health aria-live="polite"></div>';
  const panels = {defaults, models, connections, review, advanced, availability};
  return `<form class="grading-settings grading-form" novalidate><div class="compute-layout"><nav class="compute-nav" role="tablist" aria-label="Compute settings sections" aria-orientation="vertical">${SECTIONS.map(([key, title]) =>
    `<button type="button" role="tab" id="compute-tab-${key}" aria-label="${title}" aria-controls="compute-panel-${key}" aria-selected="${key === selected}" tabindex="${key === selected ? 0 : -1}" data-compute-tab="${key}"><strong>${title}</strong></button>`).join("")}</nav><div class="compute-content">${SECTIONS.map(([key, title, description]) =>
    `<div role="tabpanel" id="compute-panel-${key}" aria-labelledby="compute-tab-${key}" data-compute-panel="${key}" ${key === selected ? "" : "hidden"}><header class="compute-panel-heading"><h3>${title}</h3><p>${description}.</p></header>${panels[key]}</div>`).join("")}</div></div>
    <footer class="compute-savebar"><div><p data-settings-status role="status">All changes saved</p>${h.error}</div><div class="grading-actions">${h.button("discard-settings", "Discard changes", "disabled")}<button class="button primary" type="submit" disabled>Save compute settings</button></div></footer></form>`;
}

export function endpointEditor(e, h) {
  if (isSubscription(e)) return subscriptionEditor(e, h);
  return `<div class="compute-endpoint-editor"><div class="grading-columns">${h.field("Connection name", "name", e.name, "text", 'required maxlength="60"')}${h.field("Model ID", "model", e.model, "text", 'required maxlength="200"')}${h.field("Key variable on the server", "key_env", e.key_env, "text", 'pattern="[A-Z][A-Z0-9_]*_API_KEY" placeholder="OPENROUTER_API_KEY"')}</div>
    ${h.field("Base URL", "base_url", e.base_url, "url", "required")}${h.check("Enabled", "enabled", e.enabled)}<p class="supporting-copy">The key variable is a name such as OPENROUTER_API_KEY, or AI_GATEWAY_API_KEY, never the key itself.</p>
    ${fold("connection-capabilities", "Capabilities and provider", "Image support, output format and routing.", `<div class="grading-columns">${h.select("Provider kind", "kind", [["hosted", "Hosted endpoint"], ["dedicated", "Dedicated GPU provider"]], e.kind)}${h.select("Input", "modality", [["vision", "Images and text"], ["text", "Text only"]], e.modality)}${h.select("Structured output", "structured", [["json_schema", "JSON schema"], ["json_object", "JSON object"], ["none", "Prompt contract only"]], e.structured)}${h.field("Host (pinned upstream provider)", "upstream", e.upstream)}</div>${h.check("Aggregator: record reported cost and upstream host", "aggregator", e.aggregator)}${h.check("Supports the OpenAI-style batch API", "batch_api", e.batch_api)}`)}
    ${fold("connection-limits", "Prices and request limits", "Token prices, concurrency and this connection’s own caps.", `<div class="grading-columns">${h.field("$ per million input tokens", "price_in", e.price_in, "number", 'min="0" max="10000" step="0.001"')}${h.field("$ per million output tokens", "price_out", e.price_out, "number", 'min="0" max="10000" step="0.001"')}${h.field("Requests in flight", "max_inflight", e.max_inflight, "number", 'min="1" max="256"')}${h.field("Answer token limit", "max_tokens", e.max_tokens, "number", 'min="256" max="65536"')}${h.field("Reasoning token budget", "reasoning_tokens", e.reasoning_tokens, "number", 'min="0" max="65536"')}</div>${h.check("Separate the model’s reasoning from its answer", "reasoning", e.reasoning)}<p class="supporting-copy">These caps belong to this connection. Defaults can edit every connection’s numbers and has one workspace switch that sends or omits them. There is no shared budget across models.</p>`)}
    ${fold("connection-notes", "Notes and GPU profile", "Optional connection notes and dedicated profile name.", h.field("Notes", "notes", e.notes) + h.field("GPU profile (dedicated)", "profile", e.profile))}</div>`;
}

// A subscription connection (`grading/providers/agent_sdk.py`, `codex_sdk.py`, `antigravity.py`): the plan's program on the server,
// signed in with the instructor's own plan. It has no URL, key or prices; what it has is how many responses of one question share a
// call, and how many calls run at once.
export const SUBSCRIPTION_NUMBERS = ["bundle_size", "sessions"];
// How many runs a subscription connection may have at once: a typed number (requirement, 2026-09-28; the server checks the range,
// `config.SUBSCRIPTION`). At most 256 responses are in flight per connection, calls × responses per call.
export const CONCURRENCY_MAX = 256;
function subscriptionEditor(e, h) {
  const {plan, program} = PLANS[e.kind];
  return `<div class="compute-endpoint-editor"><div class="grading-columns">${h.field("Connection name", "name", e.name, "text", 'required maxlength="60"')}${e.kind === 'codex_sdk'
    ? h.field("OpenAI model", "model", e.model, "text", 'required maxlength="60" pattern="(gpt|codex|o)[a-z0-9.\\-]*"')
    : e.kind === 'antigravity' ? h.field("Model on the Google plan", "model", e.model, "text", 'required maxlength="60" pattern="[a-z][a-z0-9.\\-]+"')
    : h.field("Claude model", "model", e.model, "text", 'required maxlength="60" pattern="claude-[a-z0-9.\\-]+|opus|sonnet|haiku|fable"')}</div>
    <div class="grading-columns">${h.field("Responses per call", "bundle_size", e.bundle_size ?? 6, "number", 'min="1" max="20" step="1" required')}${h.field("Concurrency (calls at once)", "sessions", e.sessions ?? 2, "number", `min="1" max="${CONCURRENCY_MAX}" step="1" required`)}</div>
    ${apiFallbackMarkup(e, 'api_fallback', h)}
    ${h.check("Enabled", "enabled", e.enabled)}
    <p class="supporting-copy">${e.kind === 'agent_sdk' ? `Starts on your ${plan} subscription through ${program} on the server.` : `Runs on your ${plan} subscription through ${program} on the server, with no API billing.`} Responses of one batch to the same question (one exam version) at the same step are sent together, up to this many per call; each still gets its own answer and its own checks. A reached limit pauses this connection until it resets${e.kind === 'agent_sdk' ? ', unless direct API fallback is enabled for grading and rubric work' : ''}. Keep batch concurrency at least responses per call × calls at once so calls fill.</p></div>`;
}

export function apiFallbackMarkup(endpoint, name, h) {
  if (endpoint?.kind !== 'agent_sdk') return '';
  return h.check('Use direct Anthropic API when the subscription limit is reached', name, endpoint.api_fallback === true)
    + '<p class="supporting-copy">For grading and rubric work: uses the same Claude model, with separate API billing, until the subscription resets. Requires an Anthropic API key in Faculty’s grading credentials.</p>';
}

// Responses per call for a Claude subscription connection, beside the model it belongs to (requirement, 2026-09-27). The server
// checks the range (`config.SUBSCRIPTION`); one sends every response alone. The fields and their one-line note come apart so the
// fields can stand in a row beside the role's reasoning and host, with every note under the row.
export function bundleMarkup(endpoint, name, label, h) {
  const subscription = isSubscription(endpoint), current = endpoint.bundle_size ?? (subscription ? 6 : 1);
  const choices = Array.from({length: 20}, (_, i) => i + 1).map(n => [n, n === 1 ? '1 (each response alone)' : `${n} responses`]);
  const sessions = endpoint.sessions ?? 2;
  const pace = subscription ? `${h.e(sessions)} call${sessions === 1 ? '' : 's'} at once; fewer responses per call use more of your plan.`
    : current > 1 ? `Up to ${h.e(endpoint.max_inflight ?? 256)} requests in flight; the question and its rubric are sent once per request.` : '';
  const caveat = current > 1 ? 'More than one response per call has not been measured for reliability yet.' : '';
  return {fields: `<label class="field">Responses per call<select name="bundle-${h.e(name)}" aria-label="Responses per call for ${h.e(label)}" data-bundle-select data-connection="${h.e(endpoint.name)}">${choices.map(([value, title]) => `<option value="${value}" ${value === current ? 'selected' : ''}>${h.e(title)}</option>`).join('')}</select></label>`
    + (subscription ? `<label class="field">Concurrency (calls at once)<input type="number" name="sessions-${h.e(name)}" aria-label="Concurrency for ${h.e(label)}" data-sessions-select data-connection="${h.e(endpoint.name)}" min="1" max="${CONCURRENCY_MAX}" step="1" required value="${h.e(sessions)}"></label>` : ''),
  notes: pace || caveat ? `<p class="supporting-copy compute-host-note">${[pace, caveat].filter(Boolean).join(' ')}</p>` : ''};
}

// The host ("provider") serving a gateway model: OpenRouter's endpoint slug or Vercel's provider name, saved as the connection's
// `upstream`. The list is read from the gateway only when the instructor opens the control; this screen fetches nothing to draw itself.
const money = value => value == null ? '?' : `$${Number(value).toLocaleString(undefined, {maximumFractionDigits: 3})}`;
export function hostLabel(host, endpoint) {
  const facts = [host.quantization, host.price_in != null || host.price_out != null ? `${money(host.price_in)} / ${money(host.price_out)} per 1M` : '',
    host.uptime != null ? `${host.uptime.toFixed(1)}% up` : '',
    endpoint.structured === 'json_schema' && !host.structured ? 'no JSON schema' : '', endpoint.modality === 'vision' && host.vision === false ? 'no images' : ''];
  return [host.name, ...facts].filter(Boolean).join(' · ');
}
export function hostMarkup(endpoint, name, label, listing, h) {
  const current = endpoint.upstream || '', hosts = listing?.hosts || [];
  const choices = [['', 'Any host (the gateway chooses)'], ...hosts.map(host => [host.id, hostLabel(host, endpoint)])];
  if (current && !hosts.some(host => host.id === current)) choices.push([current, listing ? `${current} (not listed now)` : current]);
  const chosen = hosts.find(host => host.id === current);
  const warning = chosen && endpoint.structured === 'json_schema' && !chosen.structured ? 'Warning: this host does not list JSON-schema output, which every grading request asks for.'
    : chosen && endpoint.modality === 'vision' && chosen.vision === false ? 'Warning: this host does not list image input.' : '';
  // Nothing is said before the list is opened: the list itself is read only then.
  const note = [listing?.notice || (listing ? `${hosts.length} host${hosts.length === 1 ? '' : 's'} listed by ${listing.provider === 'vercel' ? 'Vercel AI Gateway' : 'OpenRouter'}.` : ''),
    endpoint.output_limit ? `With token limits off, answers may use up to ${Number(endpoint.output_limit).toLocaleString()} tokens, the most ${current ? 'this host accepts' : 'every host accepts'}.` : ''].filter(Boolean).join(' ');
  return {field: `<label class="field">Host<select name="host-${h.e(name)}" aria-label="Host for ${h.e(label)}" data-host-select data-connection="${h.e(endpoint.name)}">${choices.map(([value, title]) => `<option value="${h.e(value)}" ${value === current ? 'selected' : ''}>${h.e(title)}</option>`).join('')}</select></label>`,
    notes: `${note ? `<p class="supporting-copy compute-host-note" data-host-note>${h.e(note)}</p>` : ''}${warning ? `<p class="grading-model-warning">${h.e(warning)}</p>` : ''}`};
}

// Availability and costs (moved here from the retired Grading index, 2026-10-18): each compute service's state from
// `/api/grading/health`, and the recorded cost of the latest batches. `state` is null while reading, {error} after a failed read.
const HEALTH_NAMES = {glm: "Local handwriting reader", mathpix: "Mathpix", local_verifier: "Local visual verifier", local_grader: "Local grader", nebius: "Nebius"};
export const COST_NOTE = "Estimates are made before a batch; recorded cost comes from each request’s token usage or the host’s own figure. Neither is a bill.";
export function availabilityMarkup(state, h) {
  if (!state) return '<p class="supporting-copy" role="status">Checking compute availability…</p>';
  if (state.error) return `<p role="alert">${h.e(state.error)}</p>${h.button("retry-health", "Try again")}`;
  const health = state.health || {};
  const entries = [...Object.entries(health).filter(([key]) => !["checked_at", "endpoints"].includes(key)), ...Object.entries(health.endpoints || {})];
  return `<dl class="grading-health">${entries.map(([key, value]) => `<div><dt>${h.e(HEALTH_NAMES[key] || key)}</dt><dd>${h.e(String(value?.status || "unknown").replaceAll("_", " "))}</dd></div>`).join("")}</dl>`
    + `<p class="supporting-copy" data-compute-cost>$${h.e(Number(state.cost || 0).toFixed(2))} recorded from usage. ${h.e(COST_NOTE)}</p>${h.button("retry-health", "Check again")}`;
}
