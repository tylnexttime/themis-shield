/* Exact server codes; unknown-code fallback remains deliberately visible. */
let reasons = Object.freeze({});
export function installReasons(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid reason catalogue.");
  const clean = {};
  for (const [code, text] of Object.entries(value)) {
    if (typeof text !== "string" || !text.trim()) throw new Error("Incomplete reason catalogue.");
    clean[code] = text;
  }
  reasons = Object.freeze(clean);
}
export function why(code) {
  if (Array.isArray(code)) return code.map(why).join(" ");
  if (!code) return "No additional reason supplied.";
  return reasons[code] ||
    `The gate supplied reason code ${String(code)}. No further explanation is mapped in this app.`;
}
