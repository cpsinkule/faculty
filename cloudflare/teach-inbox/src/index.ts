import { Buffer } from "node:buffer";
import { createHash, createHmac } from "node:crypto";

const MAX_RAW = 25 * 1024 * 1024;
const PENDING = "pending/";
const ID = /^[0-9a-f-]{36}$/;
type Policy = { label: string; senders: string[]; review_only?: boolean };
type Delivery = { id: string };

function report(event: string, id?: string, status?: number) {
  // No email addresses, filenames, raw data, URL parameters, or exception text.
  console.log(JSON.stringify({ event, ...(id ? { id } : {}), ...(status ? { status } : {}) }));
}

export function policyFor(recipient: string, encoded: string): Policy | undefined {
  const policies: Record<string, Policy> = JSON.parse(encoded);
  const hash = createHash("sha256").update(recipient.toLowerCase()).digest("hex");
  return policies[hash];
}

async function* rawChunks(body: ReadableStream<Uint8Array>) {
  const reader = body.getReader();
  try {
    for (;;) {
      const next = await reader.read();
      if (next.done) return;
      yield next.value;
    }
  } finally { reader.releaseLock(); }
}

// Two streaming passes keep even a 25 MiB scan below Worker memory limits.
// The first signs; the second sends exactly the same JSON bytes from immutable R2.
export async function* envelope(object: R2ObjectBody): AsyncGenerator<Uint8Array> {
  const meta = object.customMetadata;
  if (!meta?.recipient || !meta.sender || !meta.received_at || object.size > MAX_RAW) {
    throw new Error("Invalid retained envelope");
  }
  const prefix = JSON.stringify({ schema_version: 2, recipient: meta.recipient,
    sender: meta.sender, received_at: meta.received_at }).slice(0, -1) + ',"raw_email_base64":"';
  yield Buffer.from(prefix);
  let carry = Buffer.alloc(0);
  let total = 0;
  for await (const chunk of rawChunks(object.body)) {
    total += chunk.byteLength;
    if (total > MAX_RAW) throw new Error("Message size limit exceeded");
    const bytes = Buffer.concat([carry, chunk]);
    const length = bytes.length - bytes.length % 3;
    if (length) yield Buffer.from(bytes.subarray(0, length).toString("base64"));
    carry = Buffer.from(bytes.subarray(length));
  }
  if (total !== object.size) throw new Error("Retained message size mismatch");
  if (carry.length) yield Buffer.from(carry.toString("base64"));
  yield Buffer.from('"}');
}

function stream(iterator: AsyncGenerator<Uint8Array>): ReadableStream<Uint8Array> {
  return new ReadableStream({
    async pull(controller) {
      try {
        const next = await iterator.next();
        if (next.done) controller.close(); else controller.enqueue(next.value);
      } catch (error) { controller.error(error); }
    },
    async cancel() { await iterator.return(undefined); },
  });
}

async function block(env: Env, id: string, status: number) {
  await env.MAIL.put(`blocked/${id}`, JSON.stringify({ status, at: new Date().toISOString() }));
  report("delivery_needs_attention", id, status);
}

export async function deliver(env: Env, id: string): Promise<boolean> {
  if (!ID.test(id)) return true; // A corrupt queue reference must not become an R2 path.
  const key = PENDING + id;
  if (await env.MAIL.head(`blocked/${id}`)) return true;
  const object = await env.MAIL.get(key);
  if (!object) return true; // Already accepted, or a stale duplicate queue entry.
  const url = new URL(env.INBOX_URL);
  if (url.protocol !== "https:" || url.pathname !== "/api/inbox/email" || url.search || url.username || url.password) {
    throw new Error("Invalid gateway URL");
  }
  const stamp = String(Math.floor(Date.now() / 1000));
  const hmac = createHmac("sha256", env.TEACH_INBOX_WEBHOOK_SECRET).update(`${stamp}.${id}.`);
  const digest = createHash("sha256");
  for await (const bytes of envelope(object)) { hmac.update(bytes); digest.update(bytes); }
  const bodyHash = digest.digest("hex");
  const second = await env.MAIL.get(key);
  if (!second) return true;
  if (second.etag !== object.etag || JSON.stringify(second.customMetadata) !== JSON.stringify(object.customMetadata)) {
    throw new Error("Retained envelope changed");
  }
  const response = await fetch(url, {
    method: "POST", redirect: "manual", signal: AbortSignal.timeout(60000),
    headers: { "Content-Type": "application/json", "X-Teaching-Timestamp": stamp,
      "X-Teaching-Delivery": id, "X-Teaching-Signature": hmac.digest("hex") },
    body: stream(envelope(second)),
  });
  // The application explicitly acknowledges the exact bytes and durable receipt.
  if (response.status === 202) {
    if (!response.body) throw new Error("Invalid receipt");
    const chunks: Uint8Array[] = [];
    let length = 0;
    const reader = response.body.getReader();
    try {
      for (;;) {
        const next = await reader.read();
        if (next.done) break;
        length += next.value.byteLength;
        if (length > 4096) throw new Error("Invalid receipt");
        chunks.push(next.value);
      }
    } finally { await reader.cancel(); }
    const receipt = JSON.parse(Buffer.concat(chunks).toString());
    if (receipt.delivery_id !== id || receipt.body_sha256 !== bodyHash ||
        typeof receipt.id !== "string" || !ID.test(receipt.id)) throw new Error("Invalid receipt");
    await env.MAIL.delete(key);
    report("delivered", id);
    return true;
  }
  await response.body?.cancel();
  if (response.status >= 400 && response.status < 500 && response.status !== 408 && response.status !== 429) {
    await block(env, id, response.status);
    return true; // Retain raw data; explicit recovery after fixing configuration/data.
  }
  report("delivery_retry", id, response.status);
  return false;
}

export async function recover(env: Env) {
  const saved = await env.MAIL.get("system/recovery-cursor");
  const cursor = saved ? await saved.text() : undefined;
  const page = await env.MAIL.list({ prefix: PENDING, limit: 100, ...(cursor ? { cursor } : {}) });
  for (const object of page.objects) {
    if (Date.now() - object.uploaded.getTime() < 60 * 60 * 1000) continue;
    const id = object.key.slice(PENDING.length);
    if (!ID.test(id) || await env.MAIL.head(`blocked/${id}`)) continue;
    await env.DELIVERIES.send({ id });
    report("recovered", id);
  }
  await env.MAIL.put("system/recovery-cursor", page.truncated ? page.cursor : "");
}

export default {
  async fetch() { return new Response("Not found\n", { status: 404 }); },
  async email(message, env) {
    const policy = policyFor(message.to, env.INTAKE_POLICIES);
    // The server lowercases the same list (teach_inbox/auth.py); a mixed-case entry must match here too.
    const from = message.from.toLowerCase();
    if (!policy || !policy.senders.some(s => typeof s === "string" && s.toLowerCase() === from)) {
      message.setReject("This destination does not accept this submission.");
      return;
    }
    if (!Number.isSafeInteger(message.rawSize) || message.rawSize <= 0 || message.rawSize > MAX_RAW) {
      message.setReject("The entire email must be no more than 25 MiB.");
      return;
    }
    const id = crypto.randomUUID();
    // EmailEvent.raw has no intrinsic length. R2 needs a known-length stream;
    // enforce Cloudflare's rawSize while preserving backpressure and raw bytes.
    const sized = new FixedLengthStream(message.rawSize);
    const abort = new AbortController();
    const copying = message.raw.pipeTo(sized.writable, { signal: abort.signal });
    try {
      await Promise.all([copying, env.MAIL.put(PENDING + id, sized.readable, {
        customMetadata: { recipient: message.to.toLowerCase(), sender: message.from.toLowerCase(),
          received_at: new Date().toISOString() },
        httpMetadata: { contentType: "message/rfc822" },
      })]);
    } catch {
      abort.abort();
      await copying.catch(() => {});
      report("storage_failed", id);
      // Do not report successful receipt if the raw message was not stored.
      throw new Error("Teaching Inbox could not retain the submission");
    }
    report("received", id);
    try { await env.DELIVERIES.send({ id }); }
    catch { report("queue_recovery_pending", id); } // Scheduled recovery scans R2.
  },
  async queue(batch, env) {
    for (const message of batch.messages) {
      try {
        if (message.body && typeof message.body.id === "string" && await deliver(env, message.body.id)) message.ack();
        else message.retry({ delaySeconds: 300 });
      } catch {
        report("delivery_retry", typeof message.body?.id === "string" && ID.test(message.body.id) ? message.body.id : undefined);
        message.retry({ delaySeconds: 300 });
      }
    }
  },
  async scheduled(_controller, env) {
    try { await recover(env); }
    catch { report("recovery_failed"); throw new Error("Inbox recovery failed"); }
  },
} satisfies ExportedHandler<Env, Delivery>;
