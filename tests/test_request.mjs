import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("../static/request.js", import.meta.url), "utf8");
const { createApiClient, ApiError, uploadFileWithProgress } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

test("JSON and multipart writes retain same-origin request markers without overriding multipart boundaries", async () => {
  const calls = [];
  const api = createApiClient({ fetcher: async (path, options) => {
    calls.push({ path, ...options });
    return Response.json({ saved: true });
  } });
  assert.deepEqual(await api("/api/example", "POST", { section: "001" }), { saved: true });
  assert.equal(calls[0].cache, "no-store");
  assert.equal(calls[0].headers["X-Teaching-Request"], "1");
  assert.equal(calls[0].headers["Content-Type"], "application/json");
  assert.equal(calls[0].body, '{"section":"001"}');
  const form = new FormData(); form.set("name", "Example");
  await api("/api/import", "POST", form);
  assert.equal(calls[1].body, form);
  assert.equal(calls[1].headers["Content-Type"], undefined);
  assert.equal(calls[1].headers["X-Teaching-Request"], "1");
});

test("conflicting writes expose status and server errors, refresh once, and never retry", async () => {
  let calls = 0;
  const conflicts = [];
  const api = createApiClient({ fetcher: async () => {
    calls += 1; return Response.json({ error: "The chart changed." }, { status: 409 });
  }, onConflict: (status) => conflicts.push(status) });
  await assert.rejects(api("/api/seating/example", "PATCH", {}), (error) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 409);
    assert.equal(error.message, "The chart changed.");
    assert.deepEqual(error.body, { error: "The chart changed." });
    return true;
  });
  assert.equal(calls, 1);
  assert.deepEqual(conflicts, [409]);
});

test("lost or unreadable queue responses are surfaced without replaying a potentially completed write", async () => {
  for (const result of [() => { throw new TypeError("network failure"); }, () => new Response("<html>proxy failure</html>", { status: 502 })]) {
    let calls = 0;
    const api = createApiClient({ fetcher: async () => { calls += 1; return result(); } });
    await assert.rejects(api("/api/queue", "POST", { request_id: "retained-request" }), ApiError);
    assert.equal(calls, 1);
  }
});

test("a read asks again after an answer it could not read (FRONTEND \"A RESTART NOBODY SEES\"); a write never does", async () => {
  const lost = () => new Response("", { status: 502 });
  const answers = [lost(), new Response('{"rows":', { status: 200 }), Response.json({ rows: [] })];
  const waits = [], methods = [];
  const api = createApiClient({ wait: async (ms) => waits.push(ms), fetcher: async (_path, options) => { methods.push(options.method); return answers.shift(); } });
  assert.deepEqual(await api("/api/assignments"), { rows: [] });
  assert.deepEqual(waits, [500, 1500]);
  assert.deepEqual(methods, ["GET", "GET", "GET"]);

  let calls = 0;
  const failing = createApiClient({ wait: async () => {}, fetcher: async () => { calls += 1; return lost(); } });
  await assert.rejects(failing("/api/assignments"), (error) => error.unreadable === true && error.status === 502);
  assert.equal(calls, 4);

  for (const method of ["POST", "PATCH", "DELETE"]) {
    calls = 0;
    await assert.rejects(failing("/api/queue", method, {}), (error) => error.unreadable === true);
    assert.equal(calls, 1, method);
  }
});

test("a request waits a minute by itself, as a busy Faculty is not an unreachable one; its caller and a multipart upload set their own", async () => {
  const waits = [];
  const realTimeout = AbortSignal.timeout;
  AbortSignal.timeout = (ms) => { waits.push(ms); return realTimeout.call(AbortSignal, ms); };
  try {
    const api = createApiClient({ fetcher: async () => Response.json({}) });
    await api("/api/state");
    await api("/api/save", "POST", {});
    await api("/api/send", "POST", {}, {}, 180000);
    await api("/api/import", "POST", new FormData());
  } finally { AbortSignal.timeout = realTimeout; }
  assert.deepEqual(waits, [60000, 60000, 180000, 120000]);
});

test("a read refused or cut off by its timer is not asked again, and a body the timer ended is no answer rather than an unreadable one", async () => {
  let calls = 0;
  const refused = createApiClient({ wait: async () => {}, fetcher: async () => { calls += 1; return Response.json({ error: "No." }, { status: 503 }); } });
  await assert.rejects(refused("/api/state"), (error) => error.status === 503 && !error.unreadable);
  assert.equal(calls, 1);

  calls = 0;
  const timedOut = createApiClient({ wait: async () => {}, fetcher: async () => {
    calls += 1;
    return { status: 200, ok: true, json: async () => { throw new DOMException("The operation timed out.", "TimeoutError"); } };
  } });
  await assert.rejects(timedOut("/api/student-support"), (error) => !error.unreadable && error.status === 0 && error.message.startsWith("Can’t reach"));
  assert.equal(calls, 1);
});

test("empty successful responses remain null and reads carry no accidental body or write marker", async () => {
  const api = createApiClient({ fetcher: async (_path, options) => {
    assert.equal(options.method, "GET");
    assert.equal(options.body, undefined);
    assert.deepEqual(options.headers, {});
    return new Response(null, { status: 204 });
  } });
  assert.equal(await api("/api/example"), null);
});

test("revalidate sends the kept tag, answers a 304 with the same parsed value before reading any body, and never reports it as a conflict", async () => {
  const calls = [], conflicts = [];
  let body = { rows: [1, 2] }, tag = '"one"';
  const api = createApiClient({ fetcher: async (path, options) => {
    calls.push({ path, headers: options.headers, method: options.method, cache: options.cache });
    if (options.headers["If-None-Match"] === tag) return new Response(null, { status: 304, headers: { ETag: tag } });
    return Response.json(body, { headers: { ETag: tag } });
  }, onConflict: (status) => conflicts.push(status) });
  const first = await api.revalidate("/api/inbox/jobs/a");
  assert.deepEqual(first, { data: { rows: [1, 2] }, etag: '"one"' });
  assert.deepEqual(calls[0].headers, {});
  assert.equal(calls[0].method, "GET");
  assert.equal(calls[0].cache, "no-store");
  const again = await api.revalidate("/api/inbox/jobs/a");
  assert.equal(calls[1].headers["If-None-Match"], '"one"');
  assert.equal(again.data, first.data);  // the kept object itself: nothing was parsed
  assert.equal(again.etag, '"one"');
  body = { rows: [3] }; tag = '"two"';
  const changed = await api.revalidate("/api/inbox/jobs/a");
  assert.deepEqual(changed, { data: { rows: [3] }, etag: '"two"' });
  assert.deepEqual(conflicts, []);
  // The plain client is unchanged: no tag is sent and nothing is kept for it.
  await api("/api/inbox/jobs/a");
  assert.deepEqual(calls.at(-1).headers, {});
});

test("revalidate keeps only tagged answers of the last four paths, drops a path's answer when its read fails, and asks plainly after an unusable 304", async () => {
  const sent = [];
  let fail = false, stray = false;
  const api = createApiClient({ fetcher: async (path, options) => {
    sent.push([path, options.headers["If-None-Match"] || null]);
    if (fail) return Response.json({ error: "Inbox job not found." }, { status: 404 });
    if (stray) { stray = false; return new Response(null, { status: 304 }); }
    if (options.headers["If-None-Match"]) return new Response(null, { status: 304 });
    return path === "/untagged" ? Response.json({ path }) : Response.json({ path }, { headers: { ETag: `"${path}"` } });
  } });
  assert.equal((await api.revalidate("/untagged")).etag, null);
  await api.revalidate("/untagged");
  assert.deepEqual(sent.at(-1), ["/untagged", null]);
  for (const path of ["/1", "/2", "/3", "/4", "/5"]) await api.revalidate(path);
  await api.revalidate("/1");
  assert.deepEqual(sent.at(-1), ["/1", null]);  // the oldest of five fell out
  await api.revalidate("/5");
  assert.deepEqual(sent.at(-1), ["/5", '"/5"']);
  fail = true;
  await assert.rejects(api.revalidate("/5"), (error) => error instanceof ApiError && error.status === 404);
  fail = false;
  await api.revalidate("/5");
  assert.deepEqual(sent.at(-1), ["/5", null]);
  // A 304 to a read that sent no tag is not used: the same path is asked once more without one.
  stray = true;
  const count = sent.length;
  assert.deepEqual((await api.revalidate("/6")).data, { path: "/6" });
  assert.deepEqual(sent.slice(count), [["/6", null], ["/6", null]]);
});

test("a 404 or 409 answering a GET, plain or revalidated, never calls onConflict; a write's still calls it once", async () => {
  // A link to a missing record used to loop: the read's 404 refreshed the shell, which rendered the page, which read it again.
  for (const status of [404, 409]) {
    const conflicts = [];
    let calls = 0;
    const api = createApiClient({ fetcher: async () => {
      calls += 1; return Response.json({ error: "Inbox job not found." }, { status });
    }, onConflict: (code) => conflicts.push(code) });
    await assert.rejects(api("/api/inbox/jobs/missing"), (error) => error instanceof ApiError && error.status === status && error.message === "Inbox job not found.");
    await assert.rejects(api("/api/inbox/jobs/missing", "GET"), ApiError);
    await assert.rejects(api.revalidate("/api/inbox/jobs/missing"), ApiError);
    assert.deepEqual(conflicts, [], `a read's ${status} is the page's to show`);
    assert.equal(calls, 3, "nothing is retried");
    for (const method of ["POST", "PATCH", "PUT", "DELETE"]) await assert.rejects(api("/api/inbox/jobs/missing", method, {}), ApiError);
    assert.deepEqual(conflicts, [status, status, status, status], "each write's answer refreshes the shell once");
    assert.equal(calls, 7);
  }
});

test("an upload with progress is one POST with the same-origin marker, its caller's timeout and messages, and is never retried", async () => {
  const requests = [];
  globalThis.XMLHttpRequest = class {
    constructor() { this.upload = {}; this.sent = []; requests.push(this); }
    open(method, url) { this.method = method; this.url = url; }
    setRequestHeader(name, value) { this.headers = { ...this.headers, [name]: value }; }
    send(data) { this.sent.push(data); }
  };
  try {
    const file = new File(["%PDF"], "scan.pdf");
    const options = (progress) => ({ timeout: 4000, timeoutMessage: "Too slow.", unreadableMessage: "Unreadable.", onProgress: (loaded, total) => progress.push([loaded, total]) });
    const start = (progress = []) => {
      const upload = uploadFileWithProgress("/api/example/uploads", file, options(progress));
      return [upload, requests.at(-1), progress];
    };
    const answer = (request, status, text) => { request.status = status; request.responseText = text; request.onload(); };

    let [upload, request, progress] = start();
    assert.equal(request.method, "POST");
    assert.equal(request.url, "/api/example/uploads");
    assert.deepEqual(request.headers, { "X-Teaching-Request": "1" });
    assert.equal(request.timeout, 4000);
    assert.equal(request.sent.length, 1);
    assert.equal(request.sent[0].get("file").name, "scan.pdf");
    request.upload.onprogress({ lengthComputable: false, loaded: 1, total: 0 });
    request.upload.onprogress({ lengthComputable: true, loaded: 2, total: 4 });
    answer(request, 201, '{"id":"s1"}');
    assert.deepEqual(await upload, { id: "s1" });
    assert.deepEqual(progress, [[2, 4]], "only a length the browser knows is reported");

    [upload, request] = start();
    request.onerror();
    await assert.rejects(upload, (error) => !(error instanceof ApiError) && error.message === "Can’t reach your server. Check your connection and try again.");
    [upload, request] = start();
    request.ontimeout();
    await assert.rejects(upload, { message: "Too slow." });
    for (const text of ["", "null", "<html>502</html>"]) {
      [upload, request] = start();
      answer(request, 502, text);
      await assert.rejects(upload, { message: "Unreadable." });
    }
    [upload, request] = start();
    answer(request, 400, '{"error":"Not a PDF."}');
    await assert.rejects(upload, { message: "Not a PDF." });
    for (const text of ["{}", '{"error":{"code":1}}']) {
      [upload, request] = start();
      answer(request, 500, text);
      await assert.rejects(upload, { message: "Something went wrong. Please try again." });
    }
    [upload, request] = start();
    answer(request, 302, '{"id":"s2"}');
    await assert.rejects(upload, { message: "Something went wrong. Please try again." });
    assert.equal(requests.every((each) => each.sent.length === 1), true, "no request is sent twice");
  } finally {
    delete globalThis.XMLHttpRequest;
  }
});
