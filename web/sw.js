// fledge-lite service worker.
//
// The Fledge GUI (in an iframe) calls the REST API at  <scope>api/<session>/fledge/...
// We intercept those requests and relay each one to the tab that owns <session>.
// That tab forwards it to a Web Worker running the Fledge core in Pyodide.
// Every other request (GUI assets, Pyodide files) goes to the network as normal.

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  const apiPrefix = new URL("api/", self.registration.scope);
  if (url.origin !== apiPrefix.origin || !url.pathname.startsWith(apiPrefix.pathname)) return;
  event.respondWith(relay(event.request, url, apiPrefix.pathname));
});

async function relay(request, url, apiPath) {
  const rest = url.pathname.slice(apiPath.length);            // <session>/fledge/...
  const cut = rest.indexOf("/");
  const session = rest.slice(0, cut);
  const path = rest.slice(cut) + url.search;                  // /fledge/...?...
  const body = ["GET", "HEAD"].includes(request.method) ? null : await request.arrayBuffer();
  const headers = {};
  request.headers.forEach((v, k) => (headers[k] = v));
  const msg = { type: "fledge-request", session, method: request.method, path, headers, body };

  // Ask every window; only the tab that owns this session answers. Looking the tab up
  // each time (instead of remembering it) survives the browser stopping this worker.
  const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  return new Promise((resolve) => {
    let done = false;
    const finish = (r) => { if (!done) { done = true; resolve(r); } };
    for (const w of windows) {
      const channel = new MessageChannel();
      channel.port1.onmessage = (ev) => {
        const r = ev.data;
        const nullBody = r.status === 204 || r.status === 304 || request.method === "HEAD";
        finish(new Response(nullBody ? null : r.body, { status: r.status, headers: r.headers }));
      };
      w.postMessage(msg, [channel.port2]);
    }
    setTimeout(() => finish(new Response(
      JSON.stringify({ message: `fledge-lite: no open tab owns session ${session}. Reload the page.` }),
      { status: 503, headers: { "Content-Type": "application/json" } })), 60000);
  });
}
