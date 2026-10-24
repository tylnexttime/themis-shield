import { api, request, streamChat, GateError } from "./api.js";
import { $, node, button, plain, date, numeric, badge, counts, fields, table, markdown } from "./render.js";
import { installReasons, why } from "./reasons.js";

const cleanURL = new URL(location.href);
if (cleanURL.searchParams.has("t")) {
  cleanURL.searchParams.delete("t");
  history.replaceState(null, "", cleanURL.pathname + cleanURL.search + cleanURL.hash);
}

/* One scrolling message column; findings are never inside the fixed composer. */
const findings = $("findings");
$("messages").append(findings);

const state = {
  ready: false, busy: false, reading: false, screen: "chat", settings: {},
  models: { local: [], cloud: [] }, turns: [], attachments: [], preview: null, retry: null,
  conversation: crypto.randomUUID(), localOnly: false, held: new Map(),
  calibrations: new Map(), stopped: false, controller: null
};
const idPath = id => encodeURIComponent(id);
const jobID = job => job?.job_id ?? job?.id;
const markingClass = marking => typeof marking === "string" ? marking : marking?.class || "Not supplied";
// The server enforces the marking policy; this client-side check is a conservative belt over it.
const mandatoryLocal = marking => marking?.local_only === true
  || /\b(RESTRICTED|SECRET|TOP[_ ]SECRET)\b/i.test(markingClass(marking));
const unexpired = value => !value || Number.isFinite(Date.parse(value)) && Date.parse(value) > Date.now();
const approved = job => job?.stage1 === "approved" && job?.package === "ready" && job?.stage2 === "approved" && unexpired(job.expiry);
const terminalJob = job => /refused|withdrawn|expired|completed/i.test(job?.status || "") ||
  job?.stage1 === "refused" || job?.stage2 === "refused" || job?.package === "failed" || !unexpired(job?.expiry);
const localFits = model => model.available === true && model.variants?.some(variant => variant.fits_now === true);
function listResponse(data, key) {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.[key])) return data[key];
  throw new GateError(`The ${key} list is incomplete.`);
}
function fail(error) {
  if (error?.name === "AbortError") return;
  $("error").textContent = error?.message || "The local action failed. Nothing further was sent.";
  $("error").hidden = false;
}
function notice(text) { $("notice").textContent = text; }
async function action(work) {
  $("error").hidden = true;
  $("error").textContent = "";
  try { return await work(); } catch (error) { fail(error); return null; }
}
function setBusy(value, text = "") {
  state.busy = value;
  $("composer-status").textContent = text;
  const disabled = !state.ready || value || state.reading || !!state.retry;
  for (const id of ["prompt", "files", "check"]) $(id).disabled = disabled;
  $("chat-local").disabled = !state.ready || value || state.reading;
  $("chat-cloud").disabled = !state.ready || value || state.reading || state.localOnly;
  $("new-chat").disabled = value || state.reading;
  $("discard-preview").disabled = value;
  for (const control of $("attachments").querySelectorAll("button")) control.disabled = disabled;
  for (const control of findings.querySelectorAll("button")) control.disabled = value;
}
function invalidate() {
  state.preview = null;
  findings.hidden = true;
  findings.replaceChildren();
  $("discard-preview").hidden = !state.retry;
}
function makeSnapshot() {
  const current = { role: "user", text: $("prompt").value, attachments: state.attachments.map(file => ({ ...file })) };
  if (!current.text.trim() && !current.attachments.length) throw new GateError("Enter a synthetic message or attach supported text.");
  const turns = [...state.turns.map(turn => ({
    role: turn.role, text: turn.text, attachments: (turn.attachments || []).map(file => ({ ...file }))
  })), current];
  const messages = turns.map(turn => ({ role: turn.role, content: turn.text }));
  if (JSON.stringify(messages).length > 6 * 1024 * 1024) throw new GateError("The complete conversation is too large. Do not split work to evade major-job review.");
  return {
    turns, messages, current,
    previewText: turns.map(turn => `[${turn.role}]\n${turn.text}`).join("\n\n"),
    previewAttachments: turns.flatMap(turn => turn.attachments),
    models: { local: $("chat-local").value, cloud: $("chat-cloud").value },
    conversation: state.conversation, localOnly: state.localOnly
  };
}
const previewBody = (snapshot, intent) => ({ text: snapshot.previewText, attachments: snapshot.previewAttachments, intent });
function routeOptions(preview) {
  const input = preview.route_options;
  const rows = Array.isArray(input) ? input : Object.entries(input || {}).map(([route, reasons]) => ({ route, reasons }));
  return rows.map(row => typeof row === "string"
    ? { route: row, reasons: preview.route_reasons?.[row] || [] }
    : { route: row?.route ?? row?.option, reasons: row?.reasons ?? row?.reason ?? row?.reason_code ?? [] }
  ).filter(row => ["local", "cloud"].includes(row.route));
}
function validatePreview(preview) {
  if (!preview || typeof preview.preview_id !== "string" || !["RELEASE", "ASK", "BLOCK"].includes(preview.decision)
      || typeof preview.marking?.class !== "string" || !Array.isArray(preview.findings?.replacements)
      || !preview.findings?.counts_by_label || typeof preview.size?.routine !== "boolean"
      || !Number.isInteger(preview.size?.documents) || !Number.isInteger(preview.size?.words)
      || preview.size.documents < 0 || preview.size.words < 0
      || preview.decision !== "BLOCK" && !routeOptions(preview).length) throw new GateError("Incomplete preview. No send is available.");
  return preview;
}
const fingerprint = preview => JSON.stringify({
  marking: preview.marking, findings: preview.findings, size: preview.size, decision: preview.decision,
  reasons: preview.reasons, options: routeOptions(preview)
});
function tierTimes(tiers) {
  return ["1", "2", "3"].map((tier, index) => {
    const names = ["fast check", "finder E4B", "finder 26B"], item = tiers?.[tier];
    return !item ? `${names[index]}: not supplied` : item.ran === false ? `${names[index]}: not run`
      : `${names[index]} ${numeric(Number.isFinite(item.ms) ? item.ms / 1000 : null, " s")}`;
  }).join(" · ");
}
function renderPreview(record, message = "") {
  const { value: preview, snapshot } = record;
  findings.replaceChildren(node("h2", "Pre-send findings"));
  if (message) findings.append(node("p", message, "muted"));
  findings.append(node("p", `${preview.marking.label || preview.marking.class} — ${why(preview.marking.reason_code)}`));
  if (mandatoryLocal(preview.marking) || preview.marking.banner) findings.append(node("p",
    preview.marking.banner || `${preview.marking.label || preview.marking.class}: stays on your computer; local model only; slower`, "protected"));
  findings.append(counts(preview.findings.counts_by_label),
    node("p", `${preview.findings.replacements.length} items will be replaced with placeholders.`),
    node("p", `Size check: ${preview.size.routine ? "routine size" : "major — review board required"} · ` +
      `${numeric(preview.size.documents)} documents · ${numeric(preview.size.words)} words. Routine size does not override other major flags.`),
    node("p", tierTimes(preview.tiers)),
    node("p", `Technical decision: ${preview.decision}. ${why(preview.reasons)}`));
  const options = node("ul");
  for (const option of routeOptions(preview)) options.append(node("li",
    `${option.route === "local" ? "Local" : "Cloud (de-identified)"} — ${why(option.reasons)}`));
  findings.append(options);
  if (preview.documents?.length > 1) {
    const details = node("details"), summary = node("summary", "Per-document checks and stage status");
    details.append(summary);
    for (const document of preview.documents) details.append(node("p",
      `Document ${document.document}: ${document.decision} · ${tierTimes(document.tiers)} · ` +
      Object.entries(document.stage_status || {}).map(([stage, status]) => `${stage}: ${status.status}`).join("; ")));
    findings.append(details);
  }
  findings.append(node("p", "Nothing leaves for cloud inference during preview. Changed findings require a fresh confirmation.", "muted"));
  const controls = node("div", null, "actions");
  if (preview.decision !== "BLOCK") for (const option of routeOptions(preview)) {
    if (option.route === "cloud" && (snapshot.localOnly || mandatoryLocal(preview.marking))) continue;
    const model = snapshot.models[option.route];
    const eligible = option.route === "local" ? state.models.local.some(item => item.id === model && localFits(item))
      : state.models.cloud.some(item => item.id === model);
    controls.append(button(option.route === "local" ? "Send using local model" : "Confirm and send de-identified to cloud",
      () => action(() => sendChecked(option.route)), { disabled: !eligible || state.busy, primary: true }));
  }
  else findings.append(node("p", "Blocked. Edit and check again; there is no override.", "protected"));
  findings.append(controls);
  findings.hidden = false;
  $("discard-preview").hidden = false;
  findings.scrollIntoView({ block: "start" });
}
async function checkDraft() {
  if (!state.ready || state.busy || state.reading) return;
  setBusy(true, "Checking locally…");
  try {
    const snapshot = state.retry?.snapshot || makeSnapshot();
    const intent = snapshot.localOnly ? "local" : state.retry?.intent || "auto";
    state.preview = { value: validatePreview(await api("/api/preview", {
      method: "POST", body: previewBody(snapshot, intent)
    })), snapshot, intent };
  } finally { setBusy(false); }
  if (state.preview) { renderPreview(state.preview); notice("Findings ready. Review before choosing a route."); }
}
function appendUser(turn, intent) {
  $("messages").querySelector(".empty")?.remove();
  const article = node("article", null, "message user"), header = node("header"), body = node("div");
  header.append(node("strong", "You"), badge("", `Requested: ${intent === "local" ? "Local" : "Cloud (de-identified)"}`));
  markdown(body, turn.text);
  article.append(header, body);
  if (turn.attachments.length) article.append(node("p", `Attached in memory: ${turn.attachments.map(file => file.name).join(", ")}`, "muted"));
  $("messages").insertBefore(article, findings);
}
function appendAssistant() {
  const article = node("article", null, "message assistant"), header = node("header"), route = node("span");
  route.append(badge("", "Checking route…"));
  header.append(node("strong", "Gate answer"), route);
  const body = node("div"), reason = node("p", "Waiting for verified route metadata.", "why"), extras = node("div");
  article.append(header, body, reason, extras);
  $("messages").insertBefore(article, findings);
  return { article, route, body, reason, extras, follow: true };
}
async function sendChecked(intent) {
  if (!state.ready || state.busy || state.reading || !state.preview) return;
  const old = state.preview, snapshot = old.snapshot;
  if (!routeOptions(old.value).some(option => option.route === intent)
      || intent === "cloud" && (snapshot.localOnly || mandatoryLocal(old.value.marking))) throw new GateError("This route is not eligible.");
  setBusy(true, "Rechecking before sending…");
  try {
    const fresh = validatePreview(await api("/api/preview", { method: "POST", body: previewBody(snapshot, intent) }));
    state.preview = { value: fresh, snapshot, intent };
    if (fresh.decision === "BLOCK" || fingerprint(fresh) !== fingerprint(old.value)
        || !routeOptions(fresh).some(option => option.route === intent)
        || intent === "cloud" && mandatoryLocal(fresh.marking)) {
      setBusy(false);
      renderPreview(state.preview, "The recheck changed eligibility or findings. Review and choose again. Nothing further was sent.");
      return;
    }
    if (state.retry && !approved(await api(`/api/jobs/${idPath(state.retry.job)}`))) throw new GateError("The held job is not currently approved.");
    const model = snapshot.models[intent];
    if (!(intent === "local" ? state.models.local.some(item => item.id === model && localFits(item))
      : state.models.cloud.some(item => item.id === model))) throw new GateError("The selected model is not eligible.");
    const isRetry = !!state.retry;
    if (!isRetry) appendUser(snapshot.current, intent);
    if (mandatoryLocal(fresh.marking) || snapshot.localOnly) { state.localOnly = true; $("local-lock").hidden = false; }
    const answer = appendAssistant(), controller = new AbortController();
    state.controller = controller;
    let text = "", scheduled = null;
    const render = () => {
      scheduled = null;
      markdown(answer.body, text);
      if (answer.follow) answer.article.scrollIntoView({ block: "end" });
    };
    try {
      const metadata = await streamChat({
        preview_id: fresh.preview_id, messages: snapshot.messages,
        model: intent === "local" ? "themis/local:" + model : model,
        confirm_cloud: intent === "cloud", conversation_id: snapshot.conversation
      }, delta => {
        const log = $("messages");
        answer.follow = log.scrollHeight - log.scrollTop - log.clientHeight < 100;
        text += delta;
        if (text.length > 2 * 1024 * 1024) throw new GateError("Answer exceeds the local display limit. Check the register.");
        if (!scheduled) scheduled = setTimeout(render, 90);
      }, controller.signal);
      if (scheduled) clearTimeout(scheduled);
      render();
      finishAnswer(answer, text, metadata, snapshot, intent);
      if (["local", "cloud-tokenised"].includes(metadata.route)) state.turns = [
        ...snapshot.turns, { role: "assistant", text, attachments: [] }
      ];
      if (!isRetry) { $("prompt").value = ""; state.attachments = []; renderAttachments(); }
      state.retry = null;
      invalidate();
      notice(metadata.route === "held" ? "Held for approval. This app never sends automatically." : "Response complete.");
    } catch (error) {
      if (scheduled) clearTimeout(scheduled);
      render();
      answer.route.replaceChildren(badge("", "Unverified outcome"));
      answer.reason.textContent = "No verified final route was received. Check the register before retrying.";
      throw error;
    } finally { state.controller = null; }
  } finally { setBusy(false); if (state.preview) renderPreview(state.preview); }
}
function finishAnswer(answer, text, metadata, snapshot, intent) {
  const labels = { local: "Local", "cloud-tokenised": "Cloud (de-identified)", held: "Held", blocked: "Blocked" };
  answer.route.replaceChildren(badge(metadata.route, labels[metadata.route]));
  answer.reason.textContent = `Why: ${why(metadata.reason)}`;
  if (metadata.tiers_ms) answer.extras.append(node("p", "Tier times: " + Object.entries(metadata.tiers_ms)
    .map(([tier, ms]) => `tier ${tier} ${numeric(Number.isFinite(ms) ? ms / 1000 : null, " s")}`).join(" · "), "muted"));
  if (metadata.unknown_tokens || metadata.dropped_tokens) answer.extras.append(node("p",
    `Fidelity warning: ${numeric(metadata.unknown_tokens)} unknown tokens; ${numeric(metadata.dropped_tokens)} omitted tokens. Do not guess values.`, "protected"));
  if (metadata.route === "cloud-tokenised") addCloudView(answer, metadata.job_id, text);
  if (metadata.route === "held") startHeld(answer, metadata.job_id, snapshot, intent);
  if (state.localOnly && metadata.route === "cloud-tokenised") fail(new GateError("Safety mismatch: cloud processing reported for a local-only conversation. Stop and investigate."));
}
function addCloudView(answer, id, restored) {
  const label = node("label"), toggle = node("input"), view = node("div", null, "cloud-view");
  toggle.type = "checkbox";
  label.append(toggle, document.createTextNode(" Show what the cloud saw"));
  view.hidden = true;
  answer.extras.append(label, view);
  let loaded = false, pending = false;
  toggle.addEventListener("change", () => action(async () => {
    view.hidden = !toggle.checked;
    if (!toggle.checked || loaded || pending) return;
    pending = true;
    try {
      const data = await api(`/api/jobs/${idPath(id)}/cloud-view`);
      if (data.payload === undefined || data.answer === undefined) throw new GateError("Cloud-view fields are incomplete.");
      view.replaceChildren();
      for (const [heading, value] of [
        ["Tokenised outbound payload", data.payload], ["Tokenised cloud answer", data.answer],
        ["Restored answer — local display", restored]
      ]) {
        const column = node("section");
        column.append(node("h3", heading), node("pre", plain(value)));
        view.append(column);
      }
      loaded = true;
    } catch (error) { toggle.checked = false; view.hidden = true; throw error; }
    finally { pending = false; }
  }));
}
function stageIndicators(job) {
  if (["local", "LOCAL_ONLY"].includes(job.route)) return node("p", "Local only: no cloud approval stages", "muted");
  const result = node("div", null, "actions");
  result.append(badge(job.stage1, `Stage 1: ${job.stage1 ?? "unknown"}`),
    badge(job.package, `Package: ${job.package ?? "unknown"}`), badge(job.stage2, `Stage 2: ${job.stage2 ?? "unknown"}`));
  return result;
}
function startHeld(answer, id, snapshot, intent) {
  const box = node("section"), detail = node("div"), pollStatus = node("p", "Checking approval status…", "muted");
  const send = button("Send now", () => action(async () => {
    if (state.busy || state.reading) throw new GateError("Finish the current local action first.");
    state.retry = { job: id, snapshot, intent };
    invalidate(); setBusy(false); await showScreen("chat"); await checkDraft();
    notice("Held job rechecked. Review and confirm; nothing was auto-sent.");
  }), { disabled: true });
  const record = { id, detail, pollStatus, send, timer: null, errors: 0, polling: false };
  box.append(node("h3", `Held job ${id}`), detail, pollStatus, send,
    button("Check status again", () => action(() => pollHeld(record))));
  answer.extras.append(box);
  state.held.set(record, record);
  action(() => pollHeld(record));
}
async function pollHeld(record) {
  if (state.stopped || record.polling || !state.held.has(record)) return;
  clearTimeout(record.timer);
  record.polling = true;
  try {
    const job = await api(`/api/jobs/${idPath(record.id)}`);
    record.errors = 0;
    record.detail.replaceChildren(stageIndicators(job), fields([
      ["Status", job.status], ["Due", date(job.due)], ["Approval expiry", date(job.expiry)]
    ]));
    record.send.disabled = !approved(job);
    if (approved(job)) { record.pollStatus.textContent = "Approved and ready. Send now opens a fresh preview; never auto-sends."; return; }
    if (terminalJob(job)) { record.pollStatus.textContent = "This job cannot be sent. Polling stopped."; return; }
    record.pollStatus.textContent = "Waiting for approval. Next check in 30 seconds; no automatic send.";
    record.timer = setTimeout(() => action(() => pollHeld(record)), 30000);
  } catch (error) {
    record.send.disabled = true;
    const seconds = Math.min(120, 30 * 2 ** Math.min(record.errors++, 2));
    record.pollStatus.textContent = `Status check failed. Send disabled. Next check in ${seconds} seconds.`;
    record.timer = setTimeout(() => action(() => pollHeld(record)), seconds * 1000);
    throw error;
  } finally { record.polling = false; }
}
function renderAttachments() {
  $("attachments").replaceChildren();
  state.attachments.forEach((file, index) => {
    const li = node("li");
    li.append(node("span", `${file.name} (${numeric(file.text.length)} characters)`),
      button(`Remove ${file.name}`, () => { state.attachments.splice(index, 1); invalidate(); renderAttachments(); },
        { disabled: state.busy || state.reading || !!state.retry }));
    $("attachments").append(li);
  });
}
async function readFiles(files) {
  if (!state.ready || state.busy || state.reading || state.retry) return;
  const chosen = Array.from(files);
  if (!chosen.length) return;
  state.reading = true; invalidate(); setBusy(false, "Reading text files locally…");
  try {
    if (chosen.some(file => !/\.(txt|md|csv)$/i.test(file.name))) throw new GateError("Choose only .txt, .md or .csv UTF-8 text.");
    if (chosen.some(file => file.size > 2 * 1024 * 1024)
        || state.attachments.reduce((n, file) => n + file.bytes, 0) + chosen.reduce((n, file) => n + file.size, 0) > 4 * 1024 * 1024) {
      throw new GateError("Attachments exceed 2 MiB per file or 4 MiB per draft.");
    }
    const loaded = [];
    for (const file of chosen) {
      let text;
      try { text = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer()); }
      catch { throw new GateError("An attachment is not UTF-8. No files from this selection were attached."); }
      if (/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(text)) throw new GateError("Unsupported binary controls. No files from this selection were attached.");
      loaded.push({ name: file.name, text, bytes: file.size });
    }
    state.attachments.push(...loaded); renderAttachments(); notice("Text read in this browser only. Check before sending.");
  } finally { state.reading = false; $("files").value = ""; setBusy(false); }
}
function populateModels() {
  for (const id of ["chat-local", "default-local"]) {
    const select = $(id), previous = select.value || state.settings.default_local_model;
    select.replaceChildren(node("option", "Choose an available local model")); select.firstChild.value = "";
    for (const model of state.models.local) {
      const option = node("option", model.display_name || model.id);
      option.value = model.id; option.disabled = !localFits(model); select.append(option);
    }
    if (state.models.local.some(model => model.id === previous && localFits(model))) select.value = previous;
    select.disabled = !state.ready || state.busy;
  }
  for (const id of ["chat-cloud", "default-cloud"]) {
    const select = $(id), previous = select.value || state.settings.default_cloud_model;
    select.replaceChildren(node("option", "Choose an approved cloud model")); select.firstChild.value = "";
    for (const model of state.models.cloud) {
      const option = node("option", model.display_name || model.id); option.value = model.id; select.append(option);
    }
    if (state.models.cloud.some(model => model.id === previous)) select.value = previous;
    select.disabled = !state.ready || state.busy || id === "chat-cloud" && state.localOnly;
  }
}
function renderCatalogue() {
  $("local-models").replaceChildren();
  for (const model of state.models.local) {
    const card = node("article", null, "card");
    card.append(node("h3", model.display_name || model.id), node("p", `${model.runtime} · ${(model.roles || []).join(", ")}`),
      node("p", `Reading rate (prefill), tokens/hour: ${numeric(model.tokens_per_hour_estimate)}`));
    if (!model.available) card.append(node("p", `Unavailable: ${model.unavailable_reason || "not configured"}`, "muted"));
    for (const variant of model.variants || []) {
      const row = node("section"), progress = node("progress"), status = node("p", "", "muted");
      progress.max = 100; progress.hidden = true; progress.setAttribute("aria-label", `Calibration progress for ${model.id}, ${variant.mode}`);
      status.setAttribute("role", "status");
      const calibrate = button("Calibrate on this device", () => action(() =>
        calibrateModel(model, variant, { progress, status, calibrate })), { disabled: !model.available || !variant.fits_now });
      row.append(node("h3", `${variant.mode} · ${variant.fits_now ? "fits now" : "does not fit now"}`),
        node("p", `Prompt: ${numeric(variant.expected_prompt_tok_s, " tok/s")} · Generation: ${numeric(variant.expected_gen_tok_s, " tok/s")}`),
        node("p", variant.measured_label || "estimated (reference device)", "muted"), calibrate, progress, status);
      card.append(row);
    }
    $("local-models").append(card);
  }
  $("cloud-models").replaceChildren();
  for (const model of state.models.cloud) {
    const card = node("article", null, "card");
    card.append(node("h3", model.display_name || model.id), fields([
      ["Model ID", model.id], ["Approved destination", model.destination_id], ["Deployment", model.deployment_type]
    ])); $("cloud-models").append(card);
  }
}
async function loadModels() {
  const data = await api("/api/models");
  if (!Array.isArray(data?.local) || !Array.isArray(data?.cloud)
      || [...data.local, ...data.cloud].some(model => typeof model.id !== "string")) throw new GateError("Incomplete model catalogue.");
  state.models = data; populateModels(); renderCatalogue();
}
async function calibrateModel(model, variant, ui) {
  const key = `${model.id}:${variant.mode}`;
  if (state.calibrations.has(key)) throw new GateError("This variant is already being calibrated.");
  ui.calibrate.disabled = true; ui.progress.hidden = false; ui.progress.removeAttribute("value");
  ui.status.textContent = "Starting local calibration…";
  try {
    const result = await api("/api/calibrate", { method: "POST", body: { model_id: model.id, mode: variant.mode } });
    if (typeof result?.job !== "string") throw new GateError("No calibration job ID supplied.");
    const record = { key, id: result.job, ui, start: Date.now(), timer: null };
    state.calibrations.set(key, record); await pollCalibration(record);
  } catch (error) {
    ui.progress.hidden = true; ui.calibrate.disabled = false;
    ui.status.textContent = "Calibration failed; no measurement invented."; state.calibrations.delete(key); throw error;
  }
}
async function pollCalibration(record) {
  if (state.stopped) return;
  try {
    const result = await api(`/api/calibrate/${idPath(record.id)}`);
    record.ui.status.textContent = `Calibration ${record.id}: ${result.status || "running"}`;
    if (/^(completed|complete|succeeded|done)$/i.test(result.status)) {
      state.calibrations.delete(record.key); await loadModels(); await loadCapacity(); return;
    }
    if (/^(failed|cancelled|blocked)$/i.test(result.status) || Date.now() - record.start > 30 * 60 * 1000) throw new GateError("Calibration did not complete within the polling window.");
    record.timer = setTimeout(() => action(() => pollCalibration(record)), 2000);
  } catch (error) {
    state.calibrations.delete(record.key); record.ui.calibrate.disabled = false; record.ui.progress.hidden = true;
    record.ui.status.textContent = "Calibration status unverified; backend work may still be running."; throw error;
  }
}
async function loadCapacity() {
  const data = await api("/api/capacity");
  if (!Array.isArray(data.tiers)) throw new GateError("Incomplete capacity table. No rates invented.");
  const rate = data.end_to_end_input_tokens_per_hour, job = data.reference_job;
  let line = "Measured end-to-end checking: not supplied; no pipeline capacity measurement is assumed.";
  if (Number.isFinite(rate) && rate > 0 && job?.measured === true && data.measured_utc) {
    line = `Measured end-to-end checking: about ${numeric(Math.round(rate / 100) * 100)} input tokens/hour ` +
      `on the ${job.documents}-document reference job, measured ${date(data.measured_utc).split(",")[0]}.`;
  }
  $("capacity").replaceChildren(node("p", line), table("Capacity supplied by the gate",
    ["Tier / model", "Prefill tok/s", "Decode tok/s", "Reading rate (prefill), tokens/hour", "Evidence"],
    data.tiers.map(row => [
      row.model_id ?? row.tier, numeric(row.prefill_tok_s), numeric(row.decode_tok_s),
      numeric(Number.isFinite(row.prefill_tok_s) ? row.prefill_tok_s * 3600 : null),
      row.measured_label ?? row.label ?? (row.measured === true ? "Measured" : "Estimated")
    ])));
}
async function loadRuntime() {
  const data = await api("/api/runtime"), resources = data.resources || {};
  $("runtime").replaceChildren(fields([
    ["Loaded model IDs", (data.loaded_models || []).join(", ") || "None"],
    ["RAM available / total", `${numeric(resources.ram_available_gb)} / ${numeric(resources.ram_total_gb)} GB`],
    ["VRAM free / total", `${numeric(resources.vram_free_gb)} / ${numeric(resources.vram_total_gb)} GB`],
    ["Queued work", numeric(Array.isArray(data.queue) ? data.queue.length : data.queue)]
  ]));
}
async function loadJobs() {
  const status = $("job-status").value.trim();
  const jobs = listResponse(await api("/api/jobs" + (status ? `?status=${encodeURIComponent(status)}` : "")), "jobs");
  $("job-statuses").replaceChildren(...[...new Set(jobs.map(job => job.status))].map(value => {
    const option = node("option"); option.value = value; return option;
  }));
  $("jobs-table").replaceChildren(table("Safe job register",
    ["Job", "Route", "Marking", "Status", "Stage 1 / package / stage 2", "Due", "Actions"],
    jobs.map(job => {
      const id = jobID(job), controls = node("div", null, "actions");
      controls.append(button("Details", () => action(() => openJob(id)), { disabled: !id }),
        button("Handover", () => action(async () => { $("handover-id").value = id; await showScreen("handover"); }),
          { disabled: !id || ["local", "LOCAL_ONLY"].includes(job.route) }));
      return [id || "Missing job ID", job.route, markingClass(job.marking ?? job.marking_class),
        job.status, stageIndicators(job), date(job.due), controls];
    })));
}
async function openJob(id) {
  const job = await api(`/api/jobs/${idPath(id)}`);
  $("drawer-title").textContent = `Job ${id}`;
  $("drawer-content").replaceChildren(stageIndicators(job), fields([
    ["Route", job.route], ["Marking", markingClass(job.marking ?? job.marking_class)], ["Status", job.status],
    ["Clean summary", job.summary || "Not supplied"], ["Due", date(job.due)], ["Expiry", date(job.expiry)],
    ["Tier", plain(job.tier)], ["Timings", plain(job.timings)]
  ]), node("h3", "Finding counts"), counts(job.counts));
  if (!$("job-drawer").open) $("job-drawer").showModal();
}
const operatorEnabled = () => state.settings.operator_console_enabled === true || state.settings.pilot_flags?.operator_console_enabled === true;
async function loadOperator() {
  if (!operatorEnabled()) throw new GateError("The Operator console is disabled.");
  const queue = listResponse(await api("/api/operator/queue"), "jobs");
  $("operator-queue").replaceChildren();
  for (const job of queue) {
    const id = jobID(job), card = node("article", null, "card");
    card.append(node("h3", id || "Missing job ID"), node("p", job.summary || "No clean summary supplied."),
      counts(job.counts), fields([
        ["Marking", markingClass(job.marking ?? job.marking_class)],
        ["Size", `${numeric(job.size?.documents)} documents · ${numeric(job.size?.words)} words`],
        ["Major flags", job.major_flags?.length ? job.major_flags.map(why).join(" ") : "None assessed"],
        ["Due", date(job.due)]
      ]), stageIndicators(job));
    const label = node("label", "Decision reason — controlled safe code"), reason = node("select");
    for (const [code, text] of [
      ["ROUTINE_APPROVED", "Routine scope approved"], ["REFUSED", "Requested processing refused"],
      ["OPERATOR_DOUBT", "Refer an uncertain decision"], ["MAJOR", "Major work requires the review board"]
    ]) { const option = node("option", text); option.value = code; reason.append(option); }
    label.append(reason);
    const actions = node("div", null, "actions");
    async function decide(kind, stage) {
      for (const control of actions.querySelectorAll("button")) control.disabled = true;
      try {
        await api(`/api/operator/${idPath(id)}/${kind}`, {
          method: "POST", body: stage ? { stage, reason: reason.value } : { reason: reason.value }
        });
        notice("Operator decision recorded. Confirmation and sending remain separate."); await loadOperator();
      } finally { for (const control of actions.querySelectorAll("button")) control.disabled = false; }
    }
    actions.append(
      button("Approve stage 1", () => action(() => decide("approve", 1)), { disabled: !id || job.stage1 !== "requested" }),
      button("Approve stage 2", () => action(() => decide("approve", 2)), { disabled: !id || job.package !== "ready" || job.stage2 !== "pending" }),
      button("Refuse", () => action(() => decide("refuse")), { disabled: !id }),
      button("Escalate to review board", () => action(() => decide("escalate")), { disabled: !id })
    );
    card.append(label, actions); $("operator-queue").append(card);
  }
  if (!queue.length) $("operator-queue").append(node("p", "No jobs awaiting an Operator decision."));
}
function safeManifest(manifest) {
  return fields([
    ["Manifest hash", manifest.manifest_hash], ["Clean hashes", (manifest.files || []).map(file => `${file.path}: ${file.sha256}`).join("\n")],
    ["Marking", manifest.marking_class], ["Created", date(manifest.created_utc)],
    ["Clean summary", manifest.problem_summary_deid || "Not supplied"], ["Counts", plain(manifest.pii_counts)]
  ]);
}
async function buildHandover() {
  const id = $("handover-id").value.trim();
  if (!id) throw new GateError("Enter a job ID.");
  const job = await api(`/api/jobs/${idPath(id)}`);
  if (["local", "LOCAL_ONLY"].includes(job.route) || mandatoryLocal(job.marking ?? job.marking_class)) throw new GateError("Local-only work cannot enter cloud handover.");
  const manifest = await api("/api/handover", { method: "POST", body: { job_id: id } });
  const result = $("handover-result"), peer = node("pre", "Open the placeholders-only peer view."), peerStatus = node("p", "", "muted");
  result.replaceChildren(node("h2", `Clean package for ${id}`), safeManifest(manifest));
  const noteLabel = node("label", "Peer-check note — controlled safe code"), note = node("select");
  for (const code of ["CHECKED", "REVIEW_REQUIRED"]) { const option = node("option", why(code)); option.value = code; note.append(option); }
  noteLabel.append(note);
  let peerLoaded = false;
  const check = button("Mark peer-checked", () => action(async () => {
    if (!peerLoaded) throw new GateError("Open and review the peer view first.");
    await api(`/api/handover/${idPath(id)}/peer-check`, { method: "POST", body: { checked: true, note: note.value } });
    peerStatus.textContent = "Peer check recorded; this is not confirmation or approval.";
  }), { disabled: true });
  const controls = node("div", null, "actions");
  controls.append(button("Open peer view — placeholders only", () => action(async () => {
    const data = await api(`/api/handover/${idPath(id)}/peer-view`);
    if (typeof data.text !== "string") throw new GateError("Incomplete peer view.");
    peer.textContent = data.text; peerLoaded = true; check.disabled = false;
  })), check, button("Download approved zip — no vault", () => action(() => downloadHandover(id))));
  result.append(controls, peer, noteLabel, peerStatus);
}
async function downloadHandover(id) {
  const job = await api(`/api/jobs/${idPath(id)}`);
  if (["local", "LOCAL_ONLY"].includes(job.route) || mandatoryLocal(job.marking ?? job.marking_class) || !approved(job)) throw new GateError("Download held: no current approved cloud package.");
  const response = await request(`/api/handover/${idPath(id)}/download`, { accept: "application/zip" });
  if (!/application\/(zip|x-zip-compressed|octet-stream)/i.test(response.headers.get("content-type") || "")) throw new GateError("The download is not a zip.");
  const url = URL.createObjectURL(await response.blob()), anchor = node("a");
  anchor.href = url; anchor.download = `themis-${id.replace(/[^a-zA-Z0-9_-]/g, "_")}.zip`;
  document.body.append(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 30000);
  notice("Clean zip downloaded. Never add originals or the vault.");
}
function renderSettings() {
  $("local-target").value = state.settings.local_target || "laptop";
  const flags = {
    ...(state.settings.pilot_flags || {}),
    pilot_fixture_enabled: state.settings.pilot_fixture_enabled,
    operator_console_enabled: state.settings.operator_console_enabled
  };
  $("pilot-flags").replaceChildren(...Object.entries(flags).filter(([, value]) => typeof value === "boolean")
    .flatMap(([name, value]) => [node("dt", name), node("dd", value ? "Enabled" : "Disabled")]));
  $("operator-nav").hidden = !operatorEnabled(); $("pilot-band").hidden = !operatorEnabled(); $("save-settings").disabled = !state.ready;
}
async function saveSettings() {
  if (!state.ready) throw new GateError("Settings are not ready.");
  const local = $("default-local").value, cloud = $("default-cloud").value;
  if (!state.models.local.some(model => model.id === local && localFits(model))
      || !state.models.cloud.some(model => model.id === cloud)) throw new GateError("Choose eligible defaults.");
  $("save-settings").disabled = true;
  try {
    state.settings = await api("/api/settings", { method: "POST", body: {
      local_target: "laptop", default_local_model: local, default_cloud_model: cloud
    } });
    renderSettings(); notice("Defaults saved. Rehearsal flags were not changed.");
  } finally { $("save-settings").disabled = !state.ready; }
}
async function showScreen(name) {
  if (name === "operator" && !operatorEnabled()) throw new GateError("The Operator console is disabled.");
  state.screen = name;
  for (const screen of document.querySelectorAll(".screen")) screen.hidden = screen.id !== `screen-${name}`;
  for (const control of document.querySelectorAll("[data-screen]")) {
    if (control.dataset.screen === name) control.setAttribute("aria-current", "page"); else control.removeAttribute("aria-current");
  }
  $("main").focus({ preventScroll: true });
  if (state.ready && name === "jobs") await loadJobs();
  if (state.ready && name === "operator") await loadOperator();
}
$("composer").addEventListener("submit", event => { event.preventDefault(); action(checkDraft); });
$("prompt").addEventListener("input", invalidate);
for (const id of ["chat-local", "chat-cloud"]) $(id).addEventListener("change", () => { state.retry = null; invalidate(); setBusy(false); });
$("discard-preview").addEventListener("click", () => { state.retry = null; invalidate(); setBusy(false); });
$("files").addEventListener("change", event => action(() => readFiles(event.target.files)));
for (const eventName of ["dragenter", "dragover"]) $("drop-zone").addEventListener(eventName, event => {
  event.preventDefault(); if (!state.busy && !state.reading) $("drop-zone").classList.add("dragging");
});
$("drop-zone").addEventListener("dragleave", () => $("drop-zone").classList.remove("dragging"));
$("drop-zone").addEventListener("drop", event => {
  event.preventDefault(); $("drop-zone").classList.remove("dragging");
  if (event.dataTransfer.files.length) action(() => readFiles(event.dataTransfer.files));
  else fail(new GateError("Drop local text files only; remote links are unsupported."));
});
window.addEventListener("dragover", event => event.preventDefault());
window.addEventListener("drop", event => event.preventDefault());
$("new-chat").addEventListener("click", () => {
  if (state.busy || state.reading) return;
  if ((state.turns.length || state.held.size || $("prompt").value || state.attachments.length)
      && !window.confirm("Discard page-memory conversation and stop polling? Backend jobs are not cancelled.")) return;
  for (const record of state.held.values()) clearTimeout(record.timer);
  state.held.clear(); state.turns = []; state.attachments = []; state.retry = null; state.localOnly = false;
  state.conversation = crypto.randomUUID(); $("prompt").value = ""; $("local-lock").hidden = true;
  $("messages").replaceChildren(node("p", "Start with a synthetic question or attach supported text files.", "empty"), findings);
  invalidate(); renderAttachments(); setBusy(false); $("prompt").focus();
});
for (const control of document.querySelectorAll("[data-screen]")) control.addEventListener("click", () => action(() => showScreen(control.dataset.screen)));
$("refresh-models").addEventListener("click", () => action(async () => {
  if (state.busy) throw new GateError("Wait for the current send."); invalidate(); await loadModels(); await loadCapacity(); await loadRuntime();
}));
$("refresh-jobs").addEventListener("click", () => action(loadJobs));
$("job-filter").addEventListener("submit", event => { event.preventDefault(); action(loadJobs); });
$("clear-filter").addEventListener("click", () => { $("job-status").value = ""; action(loadJobs); });
$("refresh-operator").addEventListener("click", () => action(loadOperator));
$("close-drawer").addEventListener("click", () => $("job-drawer").close());
$("handover-form").addEventListener("submit", event => { event.preventDefault(); action(buildHandover); });
$("settings-form").addEventListener("submit", event => { event.preventDefault(); action(saveSettings); });
window.addEventListener("pagehide", () => {
  state.stopped = true; state.controller?.abort();
  for (const record of state.held.values()) clearTimeout(record.timer);
  for (const record of state.calibrations.values()) clearTimeout(record.timer);
  state.held.clear(); state.calibrations.clear(); state.turns = []; state.attachments = [];
  state.preview = null; state.retry = null; $("messages").replaceChildren(); $("handover-result").replaceChildren(); $("prompt").value = "";
});
window.addEventListener("pageshow", event => { if (event.persisted) location.reload(); });
async function boot() {
  await action(async () => {
    installReasons((await api("/api/reasons")).reasons);
    state.settings = await api("/api/settings"); await loadModels();
    state.ready = true; populateModels(); renderSettings(); setBusy(false);
    notice("Local session ready. Synthetic data only; check before sending.");
  });
  if (state.ready) { await action(loadCapacity); await action(loadRuntime); }
  else notice("The app is not ready. Sending remains disabled. Reopen through the local launcher.");
}
boot();
