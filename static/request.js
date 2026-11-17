// @ts-check

/** An HTTP failure retains its status so callers can offer the right recovery. */
export class ApiError extends Error {
  /** @param {string} message @param {number} [status] @param {unknown} [body] @param {boolean} [unreadable] */
  constructor(message, status = 0, body = null, unreadable = false) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
    // An answer came that was not JSON (a proxy's 502, a body cut off): what the request did is unknown, like no answer at all.
    this.unreadable = unreadable;
  }
}

const CANT_REACH = "Can’t reach your server. Check your connection and try again.";
// How long a read waits before it asks again after an answer it could not read (FRONTEND "A RESTART NOBODY SEES"), in ms.
const READ_AGAIN = [500, 1500, 3000];

/**
 * Shared JSON transport. Never retry a write here: a lost write response can hide a
 * completed operation. Each workflow owns explicit, idempotent recovery. Only a GET
 * whose answer could not be read (a proxy's empty 502, a body cut off) asks again.
 * `onConflict` hears a 404 or 409 answering a WRITE (the record changed or went away, so the shell reads its state again),
 * never one answering a read: a page reading a missing record shows that itself, and a shell refresh would render the page,
 * which would read it again, in a loop (bugs-frontend-1, 2026-10-02).
 * @param {{fetcher?: typeof fetch, onConflict?: (status: number) => void, wait?: (ms: number) => Promise<unknown>}} [options]
 */
export function createApiClient({ fetcher = globalThis.fetch.bind(globalThis), onConflict, wait = (ms) => new Promise((done) => setTimeout(done, ms)) } = {}) {
  /**
   * @param {string} path
   * @param {string} method
   * @param {unknown} data
   * @param {Record<string, string>} extraHeaders
   * @param {number | undefined} timeoutMs
   */
  async function send(path, method, data, extraHeaders, timeoutMs) {
    const multipart = data instanceof FormData;
    try {
      return await fetcher(path, {
        method,
        cache: "no-store",
        signal: AbortSignal.timeout(timeoutMs || (multipart ? 120000 : 60000)),
        headers: {
          ...(data === undefined ? {} : multipart
            ? { "X-Teaching-Request": "1" }
            : { "Content-Type": "application/json", "X-Teaching-Request": "1" }),
          ...extraHeaders,
        },
        body: data === undefined ? undefined : multipart ? data : JSON.stringify(data),
      });
    } catch {
      throw new ApiError(CANT_REACH);
    }
  }

  /**
   * @template [T=unknown]
   * @param {Response} response
   * @param {string} [method] the request's method: only a write's 404 or 409 reaches onConflict
   * @returns {Promise<T>}
   */
  async function read(response, method = "GET") {
    if (response.status === 204) return /** @type {T} */ (/** @type {unknown} */ (null));
    /** @type {unknown} */
    let result;
    try {
      result = await response.json();
    } catch (cause) {
      // The request's timer also covers its body: one that had not finished coming is no answer in time, and asking again adds load.
      if (cause instanceof Error && cause.name === "TimeoutError") throw new ApiError(CANT_REACH);
      throw new ApiError("The server response could not be read. Refresh the page to check the result.", response.status, null, true);
    }
    if (!response.ok) {
      if ((response.status === 409 || response.status === 404) && String(method || "GET").toUpperCase() !== "GET") onConflict?.(response.status);
      const message = result && typeof result === "object" && "error" in result && typeof result.error === "string"
        ? result.error : "Something went wrong. Please try again.";
      throw new ApiError(message, response.status, result);
    }
    return /** @type {T} */ (result);
  }

  /**
   * @template [T=unknown]
   * @param {string} path
   * @param {string} [method]
   * @param {unknown} [data]
   * @param {Record<string, string>} [extraHeaders]
   * @param {number} [timeoutMs]
   * @returns {Promise<T>}
   */
  async function api(path, method = "GET", data, extraHeaders = {}, timeoutMs) {
    for (let attempt = 0; ; attempt += 1) {
      try {
        return await read(await send(path, method, data, extraHeaders, timeoutMs), method);
      } catch (error) {
        const again = method.toUpperCase() === "GET" && error instanceof ApiError && error.unreadable ? READ_AGAIN[attempt] : undefined;
        if (again === undefined) throw error;
        await wait(again);
      }
    }
  }

  // The last tagged answer of the few paths read through revalidate, oldest first: parsed bodies of up to 2 MB, page memory only.
  /** @type {Map<string, {etag: string, value: unknown}>} */
  const kept = new Map();
  const KEEP = 4;
  /**
   * A polled GET the server answers with a strong ETag (over the exact body). It sends If-None-Match with the tag of the answer
   * kept for this path, and on 304 Not Modified returns that same parsed value again, so an unchanged poll moves no body. The
   * value is shared between calls: callers must not change it. `etag` is null when the server sent none.
   * @template [T=unknown]
   * @param {string} path
   * @returns {Promise<{data: T, etag: string | null}>}
   */
  api.revalidate = async function revalidate(path) {
    const known = kept.get(path);
    let response = await send(path, "GET", undefined, known ? { "If-None-Match": known.etag } : {}, undefined);
    kept.delete(path);
    // A 304 is handled before any body is read (it has none), and never reaches onConflict.
    if (response.status === 304 && known) {
      kept.set(path, known);
      return { data: /** @type {T} */ (known.value), etag: known.etag };
    }
    // A 304 to a read that sent no tag cannot be used: ask once more plainly (a second one fails as unreadable).
    if (response.status === 304) response = await send(path, "GET", undefined, {}, undefined);
    const value = await read(response);
    const etag = response.headers.get("ETag");
    if (etag) {
      kept.set(path, { etag, value });
      for (const key of kept.keys()) if (kept.size > KEEP) kept.delete(key);
    }
    return { data: /** @type {T} */ (value), etag };
  };
  return api;
}

/**
 * One file to a POST route with its byte progress: the same POST and same-origin header as api(), through XMLHttpRequest because
 * fetch reports no upload progress. Never retried here: a retry is the instructor's choice. Each caller says how long to wait,
 * what a timeout and an unreadable answer mean for it, and how the bytes sent are shown (`onProgress` hears the raw loaded/total).
 * @param {string} url
 * @param {File} file
 * @param {{timeout: number, timeoutMessage: string, unreadableMessage: string, onProgress: (loaded: number, total: number) => void}} options
 * @returns {Promise<any>}
 */
export function uploadFileWithProgress(url, file, { timeout, timeoutMessage, unreadableMessage, onProgress }) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest(), data = new FormData();
    data.append("file", file, file.name);
    request.open("POST", url);
    request.setRequestHeader("X-Teaching-Request", "1");
    request.timeout = timeout;
    request.upload.onprogress = (event) => { if (event.lengthComputable) onProgress(event.loaded, event.total); };
    request.onerror = () => reject(new Error(CANT_REACH));
    request.ontimeout = () => reject(new Error(timeoutMessage));
    request.onload = () => {
      /** @type {any} */
      let body = null;
      try { body = JSON.parse(request.responseText); } catch { /* reported below */ }
      if (!body) return reject(new Error(unreadableMessage));
      if (request.status < 200 || request.status >= 300) return reject(new Error(typeof body.error === "string" ? body.error : "Something went wrong. Please try again."));
      resolve(body);
    };
    request.send(data);
  });
}
