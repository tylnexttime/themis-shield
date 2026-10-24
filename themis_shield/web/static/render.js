export const $ = id => document.getElementById(id);

export function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined && text !== null) element.textContent = String(text);
  if (className) element.className = className;
  return element;
}

export function button(text, action, { disabled = false, primary = false } = {}) {
  const result = node("button", text, primary ? "primary" : "");
  result.type = "button";
  result.disabled = disabled;
  result.addEventListener("click", action);
  return result;
}

export function plain(value) {
  if (value === undefined || value === null) return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

export function date(value) {
  if (!value) return "Not supplied";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Invalid server date";
  return new Intl.DateTimeFormat("en-AU", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23"
  }).format(parsed) + " local time";
}

export function numeric(value, suffix = "") {
  return Number.isFinite(value)
    ? new Intl.NumberFormat("en-AU", { maximumFractionDigits: 1 }).format(value) + suffix
    : "—";
}

export function badge(value, label = value) {
  const known = new Set([
    "local", "cloud-tokenised", "held", "blocked",
    "requested", "approved", "refused", "building", "ready", "failed", "pending"
  ]);
  return node("span", label ?? "Unknown", "badge " + (known.has(value) ? value : ""));
}

export function counts(data) {
  const container = node("div", null, "counts");
  for (const [label, count] of Object.entries(data || {})) {
    if (Number.isInteger(count) && count >= 0) container.append(badge("", `${label}: ${count}`));
  }
  if (!container.children.length) container.append(node("span", "No counts supplied", "muted"));
  return container;
}

export function fields(items) {
  const dl = node("dl");
  for (const [label, value] of items) dl.append(node("dt", label), node("dd", value));
  return dl;
}

export function table(caption, headings, rows) {
  const result = node("table");
  result.append(node("caption", caption));
  const head = node("thead");
  const heading = node("tr");
  for (const label of headings) {
    const th = node("th", label);
    th.scope = "col";
    heading.append(th);
  }
  head.append(heading);
  const body = node("tbody");
  for (const cells of rows) {
    const tr = node("tr");
    for (const cell of cells) {
      const td = node("td");
      td.append(cell instanceof Node ? cell : document.createTextNode(String(cell ?? "—")));
      tr.append(td);
    }
    body.append(tr);
  }
  result.append(head, body);
  return result;
}

/*
 * Markdown-lite, constructed exclusively with DOM text nodes.
 * Raw HTML, images and links are text, never executable HTML or network requests.
 */
export function markdown(target, source) {
  const fragment = document.createDocumentFragment();
  const lines = String(source).replace(/\r\n?/g, "\n").split("\n");
  let paragraph = [];
  let list = null;
  let code = null;
  let language = "";

  function flushParagraph() {
    if (paragraph.length) fragment.append(node("p", paragraph.join("\n")));
    paragraph = [];
  }
  function flushCode() {
    const pre = node("pre");
    const inner = node("code", code.join("\n"));
    if (language) inner.setAttribute("aria-label", `${language} code`);
    pre.append(inner);
    fragment.append(pre);
    code = null;
  }

  for (const line of lines) {
    const fence = /^\s*```(.*)$/.exec(line);
    if (code !== null) {
      if (fence) flushCode();
      else code.push(line);
      continue;
    }
    if (fence) {
      flushParagraph();
      list = null;
      code = [];
      language = fence[1].trim().slice(0, 40);
      continue;
    }
    if (!line.trim()) {
      flushParagraph();
      list = null;
      continue;
    }
    const item = /^\s*(?:([-*+])\s+|(\d+)[.)]\s+)(.*)$/.exec(line);
    if (item) {
      flushParagraph();
      const kind = item[2] ? "OL" : "UL";
      if (!list || list.tagName !== kind) {
        list = node(kind.toLowerCase());
        fragment.append(list);
      }
      list.append(node("li", item[3]));
    } else {
      list = null;
      paragraph.push(line);
    }
  }
  if (code !== null) flushCode();
  flushParagraph();
  target.classList.add("markdown");
  target.replaceChildren(fragment);
}
