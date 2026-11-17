// The readable name of a connection, for pages that show which model does a job (the subscription plans' lists live in the picker).
import {CLAUDE_MODELS, CODEX_MODELS, GEMINI_MODELS} from "./grading-model-picker.js";

// "subscription-claude-opus-5-5" -> "Claude Opus 5.5", "google-gemini-4-argon" -> "Gemini 4 Argon". A connection named by the model picker (`connectionFor`) is the model id behind
// a provider prefix, with "-2", "-3" when that name was taken; any other name is shown as it is. The exact id is tried first, so a
// model whose id ends in a number (claude-sonnet-5) is never cut short.
const NAMES = new Map([...GEMINI_MODELS, ...CLAUDE_MODELS, ...CODEX_MODELS].map(([id, label]) => [id, label]));
export function modelDisplayName(name) {
  const text = String(name || "");
  const id = text.replace(/^(subscription|chatgpt|google|vercel)-/, "");
  return NAMES.get(id) || NAMES.get(id.replace(/-\d+$/, "")) || text;
}
