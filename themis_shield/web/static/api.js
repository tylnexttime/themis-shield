/* Same-origin cookie authentication. No bearer token is held by JavaScript. */
import { why } from "./reasons.js";

export class GateError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = "GateError";
    this.status = status;
  }
}
export async function request(path, { method = "GET", body, signal, accept = "application/json" } = {}) {
  if (!path.startsWith("/api/")) throw new GateError("Unsupported local endpoint.");
  let response;
  try {
    response = await fetch(path, {
      method, credentials: "same-origin", mode: "same-origin", redirect: "error", cache: "no-store",
      headers: { Accept: accept, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }), signal
    });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new GateError("Cannot reach the local gate. No automatic retry was made. Check the register before retrying any send.");
  }
  if (!response.ok) {
    let code = "";
    try {
      const data = await response.json();
      const candidate = data.error?.code ?? data.reason_code ?? data.reason ?? data.detail?.reason;
      if (typeof candidate === "string" && /^[A-Za-z0-9_:.-]{1,128}$/.test(candidate)) code = candidate;
    } catch { /* Never echo arbitrary error bodies. */ }
    if (response.status === 401 || response.status === 403) {
      throw new GateError("The local session is missing or this action is not permitted. Open through the local launcher. " + why(code), response.status);
    }
    throw new GateError(
      `The gate refused the request (${response.status}): ${why(code)} Check the register before retrying a send.`,
      response.status
    );
  }
  return response;
}
export async function api(path, options) {
  const response = await request(path, options);
  if (response.status === 204) return null;
  if (!(response.headers.get("content-type") || "").includes("application/json")) {
    throw new GateError("Unexpected local response format.");
  }
  try { return await response.json(); }
  catch { throw new GateError("The gate returned invalid JSON."); }
}
export async function streamChat(body, onChunk, signal) {
  const response = await request("/api/chat", { method: "POST", body, signal, accept: "text/event-stream" });
  if (!(response.headers.get("content-type") || "").includes("text/event-stream") || !response.body) {
    throw new GateError("The chat response was not a stream. Check the register.");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "", dataLines = [], finished = false, metadata = null, finalSeen = false, interim = {};
  const maxEvent = 2 * 1024 * 1024;
  function dispatch() {
    if (!dataLines.length) return;
    const value = dataLines.join("\n");
    dataLines = [];
    if (value === "[DONE]") { finished = true; return; }
    if (finished || finalSeen) throw new GateError("The stream continued after its final event.");
    let chunk;
    try { chunk = JSON.parse(value); }
    catch { throw new GateError("A stream event was malformed. Check the register."); }
    if (chunk.error) throw new GateError(why(chunk.error.code));
    const content = chunk.choices?.[0]?.delta?.content;
    if (content !== undefined && typeof content !== "string") throw new GateError("Unsupported stream content.");
    if (chunk.themis !== undefined && chunk.themis && chunk.themis.route === undefined) {
      // An interim restore report from the router's stream (restored/unknown/dropped counts), not the final event.
      interim = { ...interim, ...chunk.themis };
    } else if (chunk.themis !== undefined) {
      const gate = { ...interim, ...chunk.themis };
      if (!gate || !["local", "cloud-tokenised", "held", "blocked"].includes(gate.route)
          || typeof gate.reason !== "string"
          || (["cloud-tokenised", "held"].includes(gate.route) && typeof gate.job_id !== "string")) {
        throw new GateError("Incomplete final route metadata. Check the register.");
      }
      metadata = gate;
      finalSeen = true;
    }
    onChunk(content || "", chunk.themis || null);
  }
  function consume(eof = false) {
    for (;;) {
      const match = /[\r\n]/.exec(buffer);
      if (!match) break;
      const at = match.index;
      if (buffer[at] === "\r" && at === buffer.length - 1 && !eof) break;
      const width = buffer[at] === "\r" && buffer[at + 1] === "\n" ? 2 : 1;
      const line = buffer.slice(0, at);
      buffer = buffer.slice(at + width);
      if (!line) dispatch();
      else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).replace(/^ /, ""));
        if (dataLines.reduce((n, part) => n + part.length, 0) > maxEvent) throw new GateError("Stream event too large.");
      }
      if (finished) break;
    }
    if (buffer.length > maxEvent) throw new GateError("Stream event too large.");
    if (eof && !finished) {
      if (buffer.startsWith("data:")) dataLines.push(buffer.slice(5).replace(/^ /, ""));
      buffer = "";
      dispatch();
    }
  }
  try {
    while (!finished) {
      const part = await reader.read();
      buffer += decoder.decode(part.value, { stream: !part.done });
      consume(part.done);
      if (part.done) break;
    }
    if (!metadata || !finished) throw new GateError("The stream ended without a verified final route and terminator. Check the register.");
    return metadata;
  } finally {
    try { await reader.cancel(); } catch { /* No retry. */ }
    reader.releaseLock();
  }
}
