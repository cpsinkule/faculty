import { env } from 'cloudflare:workers';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { Buffer } from 'node:buffer';
import { createHash, createHmac } from 'node:crypto';
import worker, { deliver, envelope, recover, policyFor } from '../src/index';

const sender = 'scanner@example.test';
const recipient = 'scan-opaque@inbox.example.test';
const metadata = { sender, recipient, received_at: '2026-09-13T12:00:00Z' };
const policy = JSON.stringify({[createHash('sha256').update(recipient).digest('hex')]:{label:'Test scanner',senders:[sender]}});
const raw = Buffer.from('From: scanner@example.test\r\nTo: '+recipient+'\r\n\r\nSynthetic scanner email.\x00\xff', 'latin1');

beforeEach(async () => {
  const objects = await env.MAIL.list();
  await Promise.all(objects.objects.map(o => env.MAIL.delete(o.key)));
});
afterEach(() => vi.restoreAllMocks());

async function retain(bytes=raw) {
  const id = crypto.randomUUID();
  await env.MAIL.put('pending/'+id, bytes, {customMetadata:metadata});
  return id;
}

// EmailEvent.raw is not an HTTP body with an inferred Content-Length.
function emailStream(bytes=raw) {
  return new ReadableStream<Uint8Array>({start(controller) {
    controller.enqueue(bytes.subarray(0, 17));
    controller.enqueue(bytes.subarray(17));
    controller.close();
  }});
}

it('stores an email stream with no intrinsic length before publishing its queue reference', async () => {
  const send = vi.fn().mockImplementation(async ({id}) => {
    const saved = await env.MAIL.get('pending/'+id);
    expect(Buffer.from(await saved!.arrayBuffer())).toEqual(raw);
    expect(saved!.customMetadata?.sender).toBe(sender);
  });
  await worker.email({from:sender,to:recipient,rawSize:raw.length,raw:emailStream(),setReject:vi.fn()},
    {...env,INTAKE_POLICIES:policy,DELIVERIES:{send}});
  expect(send).toHaveBeenCalledOnce();
});

it('refuses truncated or oversized email streams without queueing a receipt', async () => {
  const send = vi.fn();
  vi.spyOn(console,'log').mockImplementation(()=>{});
  for (const declared of [raw.length-1, raw.length+1]) {
    await expect(worker.email({from:sender,to:recipient,rawSize:declared,raw:emailStream(),setReject:vi.fn()},
      {...env,INTAKE_POLICIES:policy,DELIVERIES:{send}})).rejects.toThrow('could not retain');
  }
  expect(send).not.toHaveBeenCalled();
  expect((await env.MAIL.list({prefix:'pending/'})).objects).toHaveLength(0);
});

it('cancels the email stream if R2 fails before consuming it', async () => {
  const cancel = vi.fn();
  const send = vi.fn();
  vi.spyOn(console,'log').mockImplementation(()=>{});
  const input = new ReadableStream<Uint8Array>({cancel});
  const put = vi.fn().mockRejectedValue(new Error('storage unavailable'));
  await expect(worker.email({from:sender,to:recipient,rawSize:raw.length,raw:input,setReject:vi.fn()},
    {...env,INTAKE_POLICIES:policy,MAIL:{put},DELIVERIES:{send}})).rejects.toThrow('could not retain');
  expect(cancel).toHaveBeenCalledOnce();
  expect(send).not.toHaveBeenCalled();
});

it('streams identical JSON and preserves all raw bytes, including base64 boundaries', async () => {
  for (const count of [1,2,3,4,65537]) {
    const input = Buffer.alloc(count, 251);
    const id = await retain(input);
    const object = await env.MAIL.get('pending/'+id);
    const chunks = [];
    for await (const bytes of envelope(object!)) chunks.push(bytes);
    const value = JSON.parse(Buffer.concat(chunks).toString());
    expect(value).toEqual({schema_version:2,...metadata,raw_email_base64:input.toString('base64')});
  }
});

it('only deletes after a valid durable acknowledgment for the exact signed body', async () => {
  const id = await retain();
  const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
    expect(String(url)).toBe(env.INBOX_URL);
    const body = await new Response(init!.body).text();
    const headers = new Headers(init!.headers);
    const signature = createHmac('sha256',env.TEACH_INBOX_WEBHOOK_SECRET)
      .update(headers.get('X-Teaching-Timestamp')+'.'+id+'.'+body).digest('hex');
    expect(headers.get('X-Teaching-Signature')).toBe(signature);
    expect(Buffer.from(JSON.parse(body).raw_email_base64,'base64')).toEqual(raw);
    expect(await env.MAIL.head('pending/'+id)).not.toBeNull();
    return Response.json({id:crypto.randomUUID(),delivery_id:id,body_sha256:createHash('sha256').update(body).digest('hex')},{status:202});
  });
  expect(await deliver(env,id)).toBe(true);
  expect(await env.MAIL.head('pending/'+id)).toBeNull();
  expect(await deliver(env,id)).toBe(true);
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it('retains originals after network failure, server failure, redirects and false acknowledgments', async () => {
  const id = await retain();
  const fetcher = vi.spyOn(globalThis, 'fetch');
  fetcher.mockRejectedValueOnce(new Error('network'));
  await expect(deliver(env,id)).rejects.toThrow();
  fetcher.mockResolvedValueOnce(new Response('offline',{status:503}));
  expect(await deliver(env,id)).toBe(false);
  fetcher.mockResolvedValueOnce(new Response('',{status:302,headers:{Location:'https://wrong.example'}}));
  expect(await deliver(env,id)).toBe(false);
  fetcher.mockResolvedValueOnce(Response.json({id:crypto.randomUUID()},{status:202}));
  await expect(deliver(env,id)).rejects.toThrow('Invalid receipt');
  expect(await env.MAIL.head('pending/'+id)).not.toBeNull();
});

it('quarantines permanent rejection without deleting data or retrying it endlessly', async () => {
  const id = await retain();
  const fetcher = vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response('forbidden',{status:403}));
  expect(await deliver(env,id)).toBe(true);
  expect(await env.MAIL.head('pending/'+id)).not.toBeNull();
  expect(await env.MAIL.head('blocked/'+id)).not.toBeNull();
  expect(await deliver(env,id)).toBe(true);
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it('retains a received email even if queue publication fails, without logging sender or alias', async () => {
  const send = vi.fn().mockRejectedValue(new Error('queue down'));
  const log = vi.spyOn(console,'log').mockImplementation(()=>{});
  const reject = vi.fn();
  await worker.email({from:sender,to:recipient,rawSize:raw.length,raw:new Response(raw).body!,setReject:reject},
    {...env,INTAKE_POLICIES:policy,DELIVERIES:{send}});
  expect(reject).not.toHaveBeenCalled();
  const list = await env.MAIL.list({prefix:'pending/'});
  expect(list.objects).toHaveLength(1);
  expect(Buffer.from(await (await env.MAIL.get(list.objects[0].key))!.arrayBuffer())).toEqual(raw);
  expect(JSON.stringify(log.mock.calls)).not.toContain(sender);
  expect(JSON.stringify(log.mock.calls)).not.toContain(recipient);
});

it('uses exact destination capabilities and expected envelope sender, without a public HTTP ingress', async () => {
  expect(policyFor(recipient,policy)?.label).toBe('Test scanner');
  expect(policyFor('guessed@inbox.example.test',policy)).toBeUndefined();
  for (const [from,to] of [['attacker@example.test',recipient],[sender,'guessed@inbox.example.test']]) {
    const reject = vi.fn();
    await worker.email({from,to,rawSize:raw.length,raw:new Response(raw).body!,setReject:reject}, {...env,INTAKE_POLICIES:policy});
    expect(reject).toHaveBeenCalledOnce();
  }
  expect((await env.MAIL.list({prefix:'pending/'})).objects).toHaveLength(0);
  expect((await worker.fetch()).status).toBe(404);
});

it('recovers durable receipts even after their original queue message has expired', async () => {
  const id = await retain();
  const send = vi.fn().mockResolvedValue(undefined);
  vi.spyOn(Date,'now').mockReturnValue(Date.now()+2*60*60*1000);
  await recover({...env,DELIVERIES:{send}});
  expect(send).toHaveBeenCalledWith({id});
  expect(await env.MAIL.head('pending/'+id)).not.toBeNull();
});

it('compares the envelope sender with the allow-list without regard to case, as the server does', async () => {
  const mixed = JSON.stringify({[createHash('sha256').update(recipient).digest('hex')]:{label:'Test scanner',senders:['Scanner@Example.Test']}});
  const accepted = vi.fn();
  await worker.email({from:'scanner@example.test',to:recipient,rawSize:raw.length,raw:new Response(raw).body!,setReject:accepted}, {...env,INTAKE_POLICIES:mixed});
  expect(accepted).not.toHaveBeenCalled();
  expect((await env.MAIL.list({prefix:'pending/'})).objects).toHaveLength(1);
  const refused = vi.fn();
  await worker.email({from:'other@example.test',to:recipient,rawSize:raw.length,raw:new Response(raw).body!,setReject:refused}, {...env,INTAKE_POLICIES:mixed});
  expect(refused).toHaveBeenCalledOnce();
  expect((await env.MAIL.list({prefix:'pending/'})).objects).toHaveLength(1);
});
