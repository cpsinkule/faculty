import { afterEach, expect, it, vi } from "vitest";
import { Buffer } from "node:buffer";
import { createHash, createHmac } from "node:crypto";
import worker from "../src/print-export";

const secret = "test-export-secret-0000000000000000000000";
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
// Include non-UTF-8 bytes: binary PDFs must survive without text conversion.
const bytes = Buffer.concat([Buffer.from("%PDF-1.7\n"), Buffer.from([0, 128, 255, 254, 13, 10]), Buffer.from("\n%%EOF")]);
function payload() {
  return {export_id:crypto.randomUUID(),recipient:"owner@example.test",subject:"Print job",text:"Print all pages",html:"<p>Print all pages</p>",
    filename:`print-${crypto.randomUUID()}.pdf`,attachment_sha256:createHash("sha256").update(bytes).digest("hex"),pdf_base64:bytes.toString("base64")};
}
function request(data=payload(), overrides:Record<string,string>={}) {
  const body=JSON.stringify(data), stamp=String(Math.floor(Date.now()/1000));
  return new Request("https://relay.example.test/send",{method:"POST",body,headers:{"Content-Type":"application/json",
    "Content-Length":String(Buffer.byteLength(body)),"X-Faculty-Timestamp":stamp,
    "X-Faculty-Signature":createHmac("sha256",secret).update(stamp+"."+body).digest("hex"),...overrides}});
}
function fixture() {
  const send=vi.fn().mockResolvedValue({messageId:"provider-message-1"});
  return {send,env:{EXPORT_SECRET:secret,MAIL_FROM:"faculty@print.example.test",MESSAGE_FROM:"exam@example.test",MESSAGE_FROM_NAME:"Professor Example",EMAIL:{send}}};
}
it("attaches the exact PDF and uses the fixed sender and chosen custom recipient", async () => {
  const {send,env}=fixture(), data=payload();
  const result=await worker.fetch(request(data),env);
  expect(result.status).toBe(202);
  expect(await result.json()).toMatchObject({export_id:data.export_id,attachment_sha256:data.attachment_sha256,status:"accepted",message_id:"provider-message-1"});
  expect(send).toHaveBeenCalledOnce();
  const message = send.mock.calls[0][0];
  expect(message).toMatchObject({from:env.MAIL_FROM,to:data.recipient,attachments:[{
    filename:data.filename,type:"application/pdf",disposition:"attachment"}]});
  // The native binding accepts actual file content, not REST-style base64.
  // Blob applies the binding's string-versus-binary content semantics here.
  const attachment = Buffer.from(await new Blob([message.attachments[0].content]).arrayBuffer());
  expect(attachment).toEqual(bytes);
  expect(createHash("sha256").update(attachment).digest("hex")).toBe(data.attachment_sha256);
});
it("rejects missing, invalid, expired, or tampered authentication before sending", async () => {
  for (const headers of [{"X-Faculty-Signature":""},{"X-Faculty-Signature":"0".repeat(64)},{"X-Faculty-Timestamp":"1700000000"}]) {
    const {send,env}=fixture();
    expect((await worker.fetch(request(payload(),headers),env)).status).toBe(401);
    expect(send).not.toHaveBeenCalled();
  }
});
it("rejects multiple recipients, changed bytes, excess fields and oversized bodies", async () => {
  for (const changes of [{recipient:"one@example.test,two@example.test"},{attachment_sha256:"0".repeat(64)},
    {subject:"Print\r\nBcc: other@example.test"},{pdf_base64:"invalid base64"},{from:"fake@example.test"}]) {
    const {send,env}=fixture();
    expect((await worker.fetch(request({...payload(),...changes}),env)).status).toBe(400);
    expect(send).not.toHaveBeenCalled();
  }
  const {send,env}=fixture();
  expect((await worker.fetch(request(payload(),{"Content-Length":"5000000"}),env)).status).toBe(413);
  expect(send).not.toHaveBeenCalled();
});
it("does not retry uncertain delivery or expose provider error details", async () => {
  const {send,env}=fixture();
  send.mockRejectedValue(new Error("Private provider details"));
  const result=await worker.fetch(request(),env);
  expect(result.status).toBe(502);
  expect(await result.text()).not.toContain("Private provider");
  expect(send).toHaveBeenCalledOnce();
});
it("exposes no unauthenticated status, GET or arbitrary endpoint", async () => {
  const {send,env}=fixture();
  expect((await worker.fetch(new Request("https://relay.example.test/send"),env)).status).toBe(404);
  expect((await worker.fetch(new Request("https://relay.example.test/"),env)).status).toBe(404);
  expect(send).not.toHaveBeenCalled();
});
function message() {
  return {mail_id:crypto.randomUUID(),recipient:"student@example.test",subject:"IMPORTANT: Information about your upcoming Test 1 for MATH 140",
    text:"Dear Alice,\n\nOpen your response card: https://respond.example.test/r/token",html:"<p>Dear Alice,</p><p><a href=\"https://respond.example.test/r/token\">card</a></p>",reply_to:"instructor@example.test"};
}
function messageRequest(data=message(), overrides:Record<string,string>={}) {
  const body=JSON.stringify(data), stamp=String(Math.floor(Date.now()/1000));
  return new Request("https://relay.example.test/message",{method:"POST",body,headers:{"Content-Type":"application/json",
    "Content-Length":String(Buffer.byteLength(body)),"X-Faculty-Timestamp":stamp,
    "X-Faculty-Signature":createHmac("sha256",secret).update(stamp+"."+body).digest("hex"),...overrides}});
}
it("sends a plain message from the named instructor sender, to the chosen recipient, with a Reply-To", async () => {
  const {send,env}=fixture(), data=message();
  const result=await worker.fetch(messageRequest(data),env);
  expect(result.status).toBe(202);
  expect(await result.json()).toEqual({mail_id:data.mail_id,status:"accepted",message_id:"provider-message-1"});
  expect(send).toHaveBeenCalledOnce();
  expect(send.mock.calls[0][0]).toEqual({from:{name:"Professor Example",email:"exam@example.test"},to:data.recipient,subject:data.subject,text:data.text,html:data.html,replyTo:data.reply_to});
  const plain={...message(),reply_to:""};
  const {send:send2,env:env2}=fixture();
  expect((await worker.fetch(messageRequest(plain),env2)).status).toBe(202);
  expect(send2.mock.calls[0][0]).not.toHaveProperty("replyTo");
});
it("rejects malformed messages, attachments on the message route, and bad authentication", async () => {
  for (const changes of [{recipient:"one@example.test,two@example.test"},{subject:"Hi\r\nBcc: x@example.test"},{reply_to:"not an address"},
    {mail_id:"print-1"},{extra:"field"},{text:""},{html:"x".repeat(40001)}]) {
    const {send,env}=fixture();
    expect((await worker.fetch(messageRequest({...message(),...changes} as never),env)).status).toBe(400);
    expect(send).not.toHaveBeenCalled();
  }
  const {send,env}=fixture();
  const {reply_to,...rest}=message();
  expect((await worker.fetch(messageRequest({...rest,pdf_base64:"JVBERi0="} as never),env)).status).toBe(400);
  expect((await worker.fetch(messageRequest(message(),{"X-Faculty-Signature":"0".repeat(64)}),env)).status).toBe(401);
  expect((await worker.fetch(messageRequest(message(),{"Content-Length":"300000"}),env)).status).toBe(413);
  expect(send).not.toHaveBeenCalled();
});
it("reports a rejected or uncertain message without provider details", async () => {
  const {send,env}=fixture();
  send.mockRejectedValue(Object.assign(new Error("Recipient suppressed privately"),{code:"E_RECIPIENT_SUPPRESSED"}));
  const rejected=await worker.fetch(messageRequest(),env);
  expect(rejected.status).toBe(400);
  expect(await rejected.text()).not.toContain("privately");
  send.mockRejectedValue(new Error("Timeout privately"));
  const uncertain=await worker.fetch(messageRequest(),env);
  expect(uncertain.status).toBe(502);
  expect(await uncertain.text()).not.toContain("privately");
});

const resendId = "00000000-0000-0000-0000-000000000000";
function resendFixture() {
  const original=fixture();
  const fetch=vi.fn().mockImplementation(async () => Response.json({id:resendId}));
  vi.stubGlobal("fetch",fetch);
  return {...original,fetch,env:{...original.env,EMAIL_PROVIDER:"resend",RESEND_API_KEY:"re_test_only_no_real_key"}};
}
it("uses Resend for signed plain mail, preserving the named sender, content, Reply-To and receipt", async () => {
  const {send,fetch,env}=resendFixture(), data=message();
  const response=await worker.fetch(messageRequest(data),env);
  expect(response.status).toBe(202);
  expect(await response.json()).toEqual({mail_id:data.mail_id,status:"accepted",message_id:resendId});
  expect(send).not.toHaveBeenCalled();
  expect(fetch).toHaveBeenCalledOnce();
  const [url,options]=fetch.mock.calls[0];
  expect(url).toBe("https://api.resend.com/emails");
  expect(options).toMatchObject({method:"POST",redirect:"manual",headers:{Authorization:`Bearer ${env.RESEND_API_KEY}`,
    "Content-Type":"application/json",Accept:"application/json","Idempotency-Key":`faculty-message-${data.mail_id}`}});
  expect(options.signal).toBeInstanceOf(AbortSignal);
  expect(JSON.parse(options.body)).toEqual({from:'"Professor Example" <exam@example.test>',to:data.recipient,
    subject:data.subject,text:data.text,html:data.html,reply_to:data.reply_to});
  expect(response.headers.get("Cache-Control")).toBe("no-store");
});
it("uses Resend for print exports with exactly the validated binary PDF and original attachment receipt", async () => {
  const {send,fetch,env}=resendFixture(), data=payload();
  const response=await worker.fetch(request(data),env);
  expect(response.status).toBe(202);
  expect(await response.json()).toEqual({export_id:data.export_id,attachment_sha256:data.attachment_sha256,status:"accepted",message_id:resendId});
  expect(send).not.toHaveBeenCalled();
  const options=fetch.mock.calls[0][1], body=JSON.parse(options.body);
  expect(options.headers["Idempotency-Key"]).toBe(`faculty-send-${data.export_id}`);
  expect(body).toEqual({from:env.MAIL_FROM,to:data.recipient,subject:data.subject,text:data.text,html:data.html,
    attachments:[{filename:data.filename,content:data.pdf_base64,content_type:"application/pdf"}]});
  expect(Buffer.from(body.attachments[0].content,"base64")).toEqual(bytes);
});
it("keeps Resend idempotency keys stable for the same job and separates plain mail from print exports", async () => {
  const {fetch,env}=resendFixture(), data=message(), pdf={...payload(),export_id:data.mail_id};
  await worker.fetch(messageRequest(data),env);
  await worker.fetch(messageRequest(data),env);
  await worker.fetch(request(pdf),env);
  expect(fetch.mock.calls.map(call => call[1].headers["Idempotency-Key"])).toEqual([
    `faculty-message-${data.mail_id}`,`faculty-message-${data.mail_id}`,`faculty-send-${data.mail_id}`]);
  expect(fetch.mock.calls[0][1].body).toBe(fetch.mock.calls[1][1].body);
});
it("omits blank Reply-To and safely quotes configured Resend display names", async () => {
  const {fetch,env}=resendFixture();
  await worker.fetch(messageRequest({...message(),reply_to:""}),{...env,MESSAGE_FROM_NAME:'Professor "Example" \\ Team'});
  const body=JSON.parse(fetch.mock.calls[0][1].body);
  expect(body.from).toBe('"Professor \\"Example\\" \\\\ Team" <exam@example.test>');
  expect(body).not.toHaveProperty("reply_to");
  await worker.fetch(messageRequest(),{...env,MESSAGE_FROM:"",MESSAGE_FROM_NAME:""});
  expect(JSON.parse(fetch.mock.calls[1][1].body).from).toBe(env.MAIL_FROM);
});
it("fails closed for unknown providers, missing credentials, and invalid configured sender headers", async () => {
  const {send,fetch,env}=resendFixture();
  for (const change of [{EMAIL_PROVIDER:"other"},{EMAIL_PROVIDER:""},{RESEND_API_KEY:""},{RESEND_API_KEY:undefined},
    {RESEND_API_KEY:"bad\nvalue"},{MESSAGE_FROM:"one@example.test,two@example.test"},{MESSAGE_FROM_NAME:"Name\r\nBcc: x@example.test"}]) {
    const response=await worker.fetch(messageRequest(),{...env,...change});
    expect(response.status).toBe(503);
    expect(await response.text()).toBe("Unavailable");
  }
  expect(send).not.toHaveBeenCalled();
  expect(fetch).not.toHaveBeenCalled();
});
it("retains explicit Cloudflare and unset-provider sending without contacting Resend", async () => {
  const {send,fetch,env}=resendFixture();
  for (const provider of [undefined,"cloudflare"]) {
    expect((await worker.fetch(messageRequest(),{...env,EMAIL_PROVIDER:provider})).status).toBe(202);
  }
  expect(send).toHaveBeenCalledTimes(2);
  expect(fetch).not.toHaveBeenCalled();
});
it("authenticates and validates both routes before making any Resend request", async () => {
  const {send,fetch,env}=resendFixture();
  for (const invalid of [messageRequest(message(),{"X-Faculty-Signature":"0".repeat(64)}),
    request(payload(),{"X-Faculty-Signature":"0".repeat(64)}),
    messageRequest({...message(),from:"attacker@example.test"} as never),
    request({...payload(),attachment_sha256:"0".repeat(64)}),
    messageRequest({...message(),recipient:"one@example.test,two@example.test"})]) {
    expect([400,401]).toContain((await worker.fetch(invalid,env)).status);
  }
  expect(send).not.toHaveBeenCalled();
  expect(fetch).not.toHaveBeenCalled();
});
it.each(["rate_limit_exceeded","daily_quota_exceeded","monthly_quota_exceeded"])("returns a safe pause for documented Resend %s rejections", async name => {
  const {send,fetch,env}=resendFixture();
  fetch.mockImplementation(async () => Response.json({name,message:"Private provider details"},{status:429,headers:{"Retry-After":"125"}}));
  for (const input of [messageRequest(),request()]) {
    const response=await worker.fetch(input,env);
    expect(response.status).toBe(429);
    expect(response.headers.get("Retry-After")).toBe("125");
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    expect(await response.json()).toEqual({error:"rate_limited",retry_after_seconds:125});
  }
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(send).not.toHaveBeenCalled();
});
it("bounds Resend retry intervals and uses conservative defaults when no usable interval is supplied", async () => {
  const {fetch,env}=resendFixture();
  for (const [value,expected] of [["0",1],["99999999",86400],["-1",60],["nonsense",60],["1.5",60],[null,60]] as const) {
    fetch.mockImplementation(async () => Response.json({name:"rate_limit_exceeded"},{status:429,headers:value === null ? {} : {"Retry-After":value}}));
    const response=await worker.fetch(messageRequest(),env);
    expect(await response.json()).toEqual({error:"rate_limited",retry_after_seconds:expected});
    expect(response.headers.get("Retry-After")).toBe(String(expected));
  }
  fetch.mockImplementation(async () => Response.json({name:"monthly_quota_exceeded"},{status:429}));
  expect(await (await worker.fetch(messageRequest(),env)).json()).toEqual({error:"rate_limited",retry_after_seconds:86400});
});
it("returns safe pauses for Cloudflare's explicit rate and daily quota rejections", async () => {
  const {send,env}=fixture();
  for (const [code,delay] of [["E_RATE_LIMIT_EXCEEDED",60],["E_DAILY_LIMIT_EXCEEDED",3600]] as const) {
    send.mockRejectedValue(Object.assign(new Error("Private provider details"),{code}));
    for (const input of [messageRequest(),request()]) {
      const response=await worker.fetch(input,env);
      expect(response.status).toBe(429);
      expect(response.headers.get("Retry-After")).toBe(String(delay));
      expect(await response.json()).toEqual({error:"rate_limited",retry_after_seconds:delay});
    }
  }
  expect(send).toHaveBeenCalledTimes(4);
});
it.each([400,401,403,404,405,413,422])("reports Resend HTTP%s as rejection without exposing provider details", async status => {
  const {fetch,env}=resendFixture(), log=vi.spyOn(console,"log").mockImplementation(() => {});
  fetch.mockImplementation(async () => Response.json({message:"Private provider details"},{status}));
  const response=await worker.fetch(messageRequest(),env);
  expect(response.status).toBe(400);
  expect(await response.text()).toBe("Email rejected");
  expect(JSON.stringify(log.mock.calls)).not.toContain("Private");
  expect(fetch).toHaveBeenCalledOnce();
});
it("never retries Resend network errors, ambiguous responses, or malformed provider acceptance", async () => {
  const {fetch,env}=resendFixture(), log=vi.spyOn(console,"log").mockImplementation(() => {});
  const outcomes=[
    () => { throw new Error("Private timeout details"); },
    () => { throw new DOMException("Private timeout details","TimeoutError"); },
    () => Response.json({name:"concurrent_idempotent_requests"},{status:409}),
    () => Response.json({name:"invalid_idempotent_request"},{status:409}),
    () => Response.json({name:"application_error"},{status:500}),
    () => Response.json({name:"service_unavailable"},{status:503}),
    () => new Response("redirect",{status:307,headers:{Location:"https://other.example.test"}}),
    () => Response.json({name:"unknown_error"},{status:429}),
    () => new Response("Proxy rejection",{status:429}),
    () => Response.json({id:resendId},{status:201}),
    () => Response.json({}),
    () => Response.json({id:123}),
    () => Response.json({id:"Private provider details"}),
    () => new Response("not JSON"),
    () => new Response(" ".repeat(16_385)+JSON.stringify({id:resendId})),
  ];
  for (const outcome of outcomes) {
    fetch.mockClear().mockImplementation(outcome);
    const response=await worker.fetch(messageRequest(),env);
    expect(response.status).toBe(502);
    expect(await response.text()).toBe("Delivery unconfirmed");
    expect(response.headers.has("Retry-After")).toBe(false);
    expect(fetch).toHaveBeenCalledOnce();
  }
  expect(JSON.stringify(log.mock.calls)).not.toContain("Private");
});
