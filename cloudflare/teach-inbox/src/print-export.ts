import { Buffer } from "node:buffer";
import { createHash, createHmac, timingSafeEqual } from "node:crypto";

const MAX_BODY = 4_800_000;
const MAX_MESSAGE_BODY = 200_000;
const MAX_PDF = 3_500_000;
const MAX_PROVIDER_RESPONSE = 16_384;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const EMAIL = /^[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}$/;
const HASH = /^[0-9a-f]{64}$/;
const FIELDS = ["export_id","recipient","subject","text","html","filename","attachment_sha256","pdf_base64"];
// Plain messages (response card invitations and confirmations): no attachment, optional Reply-To.
const MESSAGE_FIELDS = ["mail_id","recipient","subject","text","html","reply_to"];
const DEFINITE = ["E_VALIDATION_ERROR","E_FIELD_MISSING","E_SENDER_NOT_VERIFIED","E_RECIPIENT_NOT_ALLOWED",
  "E_RECIPIENT_SUPPRESSED","E_SENDER_DOMAIN_NOT_AVAILABLE","E_CONTENT_TOO_LARGE"];

class DeliveryFailure extends Error {
  constructor(readonly status: 400 | 429 | 503, readonly retryAfter = 0) { super("Email delivery unavailable"); }
}

// An unclassified provider outcome, described only by its HTTP status and the provider's
// error identifier (never a message, address or payload), so the log can say why a send is uncertain.
class ProviderOutcome extends Error {
  constructor(readonly status: number, readonly reason: string) { super("Provider outcome"); }
}

function providerReason(detail: unknown): string {
  const record = detail && typeof detail === "object" ? detail as Record<string, unknown> : {};
  const name = typeof record.name === "string" ? record.name : typeof record.error_name === "string" ? record.error_name : "";
  return name.replace(/[^A-Za-z0-9_]/g, "").slice(0, 40) || "non_json";
}

function retryAfter(value: string | null, fallback: number): number {
  const seconds = value && /^\d+$/.test(value) ? Number(value) : NaN;
  return Number.isFinite(seconds) ? Math.max(1, Math.min(86_400, seconds)) : fallback;
}

async function providerJSON(response: Response): Promise<unknown> {
  if (!response.body) throw new Error();
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const next = await reader.read();
      if (next.done) break;
      size += next.value.byteLength;
      if (size > MAX_PROVIDER_RESPONSE) { await reader.cancel(); throw new Error(); }
      chunks.push(next.value);
    }
  } finally { reader.releaseLock(); }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

async function sendEmail(env: PrintExportEnv, route: "send" | "message", data: Record<string,string>, pdf?: Buffer): Promise<string> {
  const provider = String(env.EMAIL_PROVIDER ?? "cloudflare");
  if (provider !== "cloudflare" && provider !== "resend") throw new DeliveryFailure(503);
  const sender = route === "message" ? env.MESSAGE_FROM || env.MAIL_FROM : env.MAIL_FROM;
  const name = route === "message" ? env.MESSAGE_FROM_NAME : "";
  if (!address(sender) || (name && (name.length > 300 || /[\u0000-\u001f\u007f]/.test(name)))) throw new DeliveryFailure(503);
  if (provider === "cloudflare") {
    // The native binding accepts file bytes and performs its own MIME encoding.
    const result = await env.EMAIL.send({from:name ? {name, email:sender} : sender,
      to:data.recipient, subject:data.subject, text:data.text, html:data.html,
      ...(data.reply_to ? {replyTo:data.reply_to} : {}),
      ...(pdf ? {attachments:[{filename:data.filename,content:pdf,type:"application/pdf",disposition:"attachment" as const}]} : {})});
    if (!result.messageId) throw new Error();
    return result.messageId;
  }
  if (!env.RESEND_API_KEY || /\s/.test(env.RESEND_API_KEY)) throw new DeliveryFailure(503);
  let response: Response;
  try {
    // The Workers runtime rejects redirect mode "error" with a TypeError; "manual" hands any
    // redirect back as a 3xx response, which the status handling below leaves unconfirmed.
    response = await fetch("https://api.resend.com/emails", {
    method:"POST", redirect:"manual", signal:AbortSignal.timeout(30_000),
    // Resend's edge bans requests by browser signature; identify this client plainly.
    headers:{"Authorization":`Bearer ${env.RESEND_API_KEY}`,"Content-Type":"application/json","Accept":"application/json",
      "User-Agent":"Faculty-Print-Export/1.0",
      // Stable across queue retries, separate for plain messages and print exports.
      // Resend retains keys for 24 hours; this does not authorize retrying uncertain sends.
      "Idempotency-Key":`faculty-${route}-${route === "message" ? data.mail_id : data.export_id}`},
    body:JSON.stringify({from:name ? `"${name.replace(/["\\]/g, "\\$&")}" <${sender}>` : sender,
      to:data.recipient, subject:data.subject, text:data.text, html:data.html,
      ...(data.reply_to ? {reply_to:data.reply_to} : {}),
      ...(pdf ? {attachments:[{filename:data.filename,content:data.pdf_base64,content_type:"application/pdf"}]} : {})}),
    });
  } catch (error) {
    // Nothing reached the provider, or the connection failed before a response: unconfirmed.
    throw new ProviderOutcome(0, error instanceof Error ? error.name.replace(/[^A-Za-z0-9_]/g, "").slice(0, 40) || "fetch_failed" : "fetch_failed");
  }
  if (response.status === 429) {
    const error = await providerJSON(response).catch(() => null);
    // Retry only documented pre-acceptance rate/quota rejections, never a proxy's generic 429.
    if (error && typeof error === "object" && "name" in error &&
        ["rate_limit_exceeded","daily_quota_exceeded","monthly_quota_exceeded"].includes(String(error.name))) {
      throw new DeliveryFailure(429, retryAfter(response.headers.get("Retry-After"), error.name === "rate_limit_exceeded" ? 60 : 86_400));
    }
    throw new ProviderOutcome(429, providerReason(error));
  }
  if (response.status !== 200) {
    const detail = await providerJSON(response).catch(() => null);
    // A conflicting/in-flight idempotency key (409) may refer to an accepted send.
    // Server, redirect, network and other unknown outcomes remain unconfirmed.
    if ([400,401,403,404,405,413,422].includes(response.status)) throw new DeliveryFailure(400);
    throw new ProviderOutcome(response.status, providerReason(detail));
  }
  const result = await providerJSON(response).catch(() => null);
  if (!result || typeof result !== "object" || !("id" in result) || typeof result.id !== "string" || !UUID.test(result.id)) {
    throw new ProviderOutcome(200, "unexpected_body");
  }
  return result.id;
}

function address(value: string) { return EMAIL.test(value) && value.length <= 254 && !value.includes(".."); }
function fields(value: unknown, names: string[]): Record<string,string> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const entries = Object.entries(value);
  if (entries.length !== names.length || entries.some(([key,val]) => !names.includes(key) || typeof val !== "string")) return null;
  return Object.fromEntries(entries) as Record<string,string>;
}
function outcome(error: unknown, event: string, id: string): Response {
  const code = error instanceof Error && "code" in error ? String(error.code) : "";
  const pause = error instanceof DeliveryFailure && error.status === 429 ? error.retryAfter :
    code === "E_RATE_LIMIT_EXCEEDED" ? 60 : code === "E_DAILY_LIMIT_EXCEEDED" ? 3600 : 0;
  if (pause) {
    console.log(JSON.stringify({event:`${event}_rate_limited`,id}));
    return Response.json({error:"rate_limited",retry_after_seconds:pause},
      {status:429,headers:{"Retry-After":String(pause),"Cache-Control":"no-store"}});
  }
  if (error instanceof DeliveryFailure && error.status === 503) {
    console.log(JSON.stringify({event:`${event}_unavailable`,id}));
    return new Response("Unavailable",{status:503,headers:{"Cache-Control":"no-store"}});
  }
  const definite = DEFINITE.includes(code) || (error instanceof DeliveryFailure && error.status === 400);
  // Never log provider exceptions: they can contain addresses or payloads. An unclassified
  // provider outcome contributes only its status and error identifier.
  const provider = error instanceof ProviderOutcome ? {provider_status:error.status, provider_reason:error.reason} : {};
  console.log(JSON.stringify({event:definite?`${event}_rejected`:`${event}_uncertain`,id,...provider}));
  return new Response(definite?"Email rejected":"Delivery unconfirmed",{status:definite?400:502,headers:{"Cache-Control":"no-store"}});
}

export default {
  async fetch(request: Request, env: PrintExportEnv): Promise<Response> {
    // A separate transport-only Worker; it has no Faculty catalog, inbound-mail
    // binding, or capability to choose which private artifact or student gets mail.
    const url = new URL(request.url);
    const route = url.pathname === "/send" ? "send" : url.pathname === "/message" ? "message" : null;
    if (!route || request.method !== "POST" || url.search) return new Response("Not found", {status:404});
    const maxBody = route === "send" ? MAX_BODY : MAX_MESSAGE_BODY;
    const stamp = request.headers.get("X-Faculty-Timestamp") || "";
    const signature = request.headers.get("X-Faculty-Signature") || "";
    if (!env.EXPORT_SECRET || env.EXPORT_SECRET.length < 32) return new Response("Unavailable", {status:503});
    if (!/^\d{10,12}$/.test(stamp) || Math.abs(Date.now()/1000 - Number(stamp)) > 300 || !HASH.test(signature)) {
      return new Response("Unauthorized", {status:401});
    }
    const declared = Number(request.headers.get("Content-Length"));
    if (!request.body || !Number.isSafeInteger(declared) || declared <= 0) return new Response("Invalid length", {status:400});
    if (declared > maxBody) return new Response("Too large", {status:413});
    const reader = request.body.getReader();
    const chunks: Uint8Array[] = [];
    let size = 0;
    try {
      for (;;) {
        const next = await reader.read();
        if (next.done) break;
        size += next.value.byteLength;
        if (size > maxBody) { await reader.cancel(); return new Response("Too large", {status:413}); }
        chunks.push(next.value);
      }
    } finally { reader.releaseLock(); }
    if (size !== declared) return new Response("Invalid length", {status:400});
    const raw = Buffer.concat(chunks);
    const expected = createHmac("sha256", env.EXPORT_SECRET).update(stamp+".").update(raw).digest();
    if (!timingSafeEqual(Buffer.from(signature,"hex"), expected)) return new Response("Unauthorized", {status:401});
    let parsed: unknown;
    try { parsed = JSON.parse(raw.toString("utf8")); } catch { return new Response(route === "send" ? "Invalid export" : "Invalid message", {status:400}); }

    if (route === "message") {
      const data = fields(parsed, MESSAGE_FIELDS);
      if (!data || !UUID.test(data.mail_id) || !address(data.recipient)
          || !data.subject || data.subject.length > 300 || /[\r\n]/.test(data.subject)
          || !data.text || data.text.length > 20000 || !data.html || data.html.length > 40000
          || (data.reply_to !== "" && !address(data.reply_to))) return new Response("Invalid message", {status:400});
      try {
        // Plain messages come from the instructor-facing sender; print exports keep the fixed relay sender.
        const messageId = await sendEmail(env, "message", data);
        console.log(JSON.stringify({event:"message_email_accepted",id:data.mail_id}));
        return Response.json({mail_id:data.mail_id,status:"accepted",message_id:messageId}, {status:202,headers:{"Cache-Control":"no-store"}});
      } catch (error) { return outcome(error, "message_email", data.mail_id); }
    }

    let data: Record<string,string>;
    let pdf: Buffer;
    try {
      const value = fields(parsed, FIELDS);
      if (!value) throw new Error();
      data = value;
      if (!UUID.test(data.export_id) || !address(data.recipient)
          || !HASH.test(data.attachment_sha256) || !/^print-[0-9a-f-]{36}\.pdf$/.test(data.filename)
          || !data.subject || data.subject.length > 300 || /[\r\n]/.test(data.subject)
          || !data.text || data.text.length > 3000 || !data.html || data.html.length > 6000) throw new Error();
      pdf = Buffer.from(data.pdf_base64,"base64");
      if (pdf.length > MAX_PDF) return new Response("Too large", {status:413});
      if (!pdf.subarray(0,5).equals(Buffer.from("%PDF-")) || pdf.toString("base64") !== data.pdf_base64
          || createHash("sha256").update(pdf).digest("hex") !== data.attachment_sha256) throw new Error();
    } catch { return new Response("Invalid export", {status:400}); }
    try {
      const messageId = await sendEmail(env, "send", data, pdf);
      console.log(JSON.stringify({event:"print_email_accepted",id:data.export_id}));
      return Response.json({export_id:data.export_id,attachment_sha256:data.attachment_sha256,
        status:"accepted",message_id:messageId}, {status:202,headers:{"Cache-Control":"no-store"}});
    } catch (error) { return outcome(error, "print_email", data.export_id); }
  },
} satisfies ExportedHandler<PrintExportEnv>;
