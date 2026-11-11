"""The shipped browser UI (themis_shield/web/static): it is served, its scripts parse, and what it shows is wired.

Nothing here needs a browser. A green suite once said nothing about whether the page loads; the first headless Chrome
run found that an Operator-only session could not load /api.js (the boundary read "/api" as a prefix, so the page's own
script was an API path), and that the page asked for /favicon.ico on every load. Those are pinned below, together with
a parse check for every shipped script that cannot be skipped: a small tokenizer (strings, template literals with
nested ${ }, regular expressions, comments, balanced brackets) tested on deliberately broken snippets, plus
`node --check` as a second opinion when Node is on PATH.
"""

from __future__ import annotations

import asyncio
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import tomllib

import httpx
import pytest

from themis_shield.web.app import create_app

from router_fakes import harness

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "themis_shield"
STATIC = PACKAGE / "web" / "static"
SCRIPT_SUFFIXES = {".js", ".mjs", ".cjs"}
# Every kind of file the package data may carry. A new kind must be looked at: is it a script that needs a parse check?
KNOWN_SUFFIXES = SCRIPT_SUFFIXES | {".css", ".html", ".svg", ".json"}

CLIENT = "SYNTHETIC_LOCAL_GATE_TOKEN_1234567890"
OPERATOR = "SYNTHETIC_LOCAL_OPERATOR_TOKEN_0987654321"
ORIGIN = "http://127.0.0.1:8742"


# --------------------------------------------------------------------------------------------- the tokenizer ---
_REGEX_AFTER_WORD = {
    "return", "typeof", "case", "in", "of", "delete", "void", "throw", "new", "else", "do", "instanceof", "yield",
    "await",
}
_REGEX_AFTER_CHAR = {"", "(", ",", "=", ":", "[", "!", "&", "|", "?", "{", ";", "+", "-", "*", "%", "<", ">", "~", "^"}
_CLOSERS = {"(": ")", "[": "]", "{": "}"}


def js_problems(source: str) -> list[str]:
    """Syntax problems a tokenizer can see: unbalanced or mismatched brackets, unterminated strings, template
    literals, comments and regular expressions. Not a parser (it does not check grammar), but a script that is
    truncated, or has a lost quote, backtick or bracket, cannot pass."""
    problems: list[str] = []
    stack: list[tuple[str, int]] = []  # "(", "[", "{" or "${" (a template expression) with the index it opened at
    n = len(source)

    def where(index: int) -> str:
        return f"line {source.count(chr(10), 0, index) + 1}"

    def template(j: int, start: int) -> int:
        # Scan a template literal's text from j; stop after the closing backtick, or after a "${" (pushed).
        while j < n:
            char = source[j]
            if char == "\\":
                j += 2
            elif char == "`":
                return j + 1
            elif char == "$" and source.startswith("${", j):
                stack.append(("${", j))
                return j + 2
            else:
                j += 1
        problems.append(f"unterminated template literal from {where(start)}")
        return n

    i = 0
    previous = ""  # last significant character ("a" after a word, number, string, template or regex)
    word = ""
    while i < n:
        char = source[i]
        if char.isspace() or char == "\ufeff":
            i += 1
        elif source.startswith("//", i):
            end = source.find("\n", i)
            i = n if end < 0 else end
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            if end < 0:
                problems.append(f"unterminated comment from {where(i)}")
                break
            i = end + 2
        elif char in "'\"":
            j = i + 1
            while j < n and source[j] != char:
                if source[j] == "\n":
                    break
                j += 2 if source[j] == "\\" else 1
            if j >= n or source[j] != char:
                problems.append(f"unterminated string from {where(i)}")
                j = min(j, n - 1)
            i, previous, word = j + 1, "a", ""
        elif char == "`":
            before = len(stack)
            i = template(i + 1, i)
            previous, word = ("{", "") if len(stack) > before else ("a", "")
        elif char == "/":
            # A "/" starts a regular expression after an operator, an opening bracket or a keyword like return;
            # after a value, a closing bracket or "}" it is a division.
            if previous in _REGEX_AFTER_CHAR or word in _REGEX_AFTER_WORD:
                j, in_class = i + 1, False
                while j < n and source[j] != "\n" and (in_class or source[j] != "/"):
                    if source[j] == "\\":
                        j += 1
                    elif source[j] == "[":
                        in_class = True
                    elif source[j] == "]":
                        in_class = False
                    j += 1
                if j >= n or source[j] != "/":
                    problems.append(f"unterminated regular expression from {where(i)}")
                    i = j
                else:
                    j += 1
                    while j < n and source[j].isalpha():
                        j += 1
                    i, previous, word = j, "a", ""
            else:
                i, previous, word = i + 1, "/", ""
        elif char.isalnum() or char in "_$":
            j = i
            while j < n and (source[j].isalnum() or source[j] in "_$"):
                j += 1
            i, previous, word = j, "a", source[i:j]
        elif char in _CLOSERS:
            stack.append((char, i))
            i, previous, word = i + 1, char, ""
        elif char in ")]}":
            if not stack:
                problems.append(f"unexpected {char!r} at {where(i)}")
                i += 1
                continue
            opener, at = stack.pop()
            if opener == "${":
                if char == "}":
                    before = len(stack)
                    i = template(i + 1, at)
                    previous, word = ("{", "") if len(stack) > before else ("a", "")
                else:
                    problems.append(f"{char!r} at {where(i)} inside the ${{ from {where(at)}")
                    i += 1
                continue
            if _CLOSERS[opener] != char:
                problems.append(f"{char!r} at {where(i)} does not match {opener!r} from {where(at)}")
            i, previous, word = i + 1, char, ""
        else:
            i, previous, word = i + 1, char, ""
    for opener, at in stack:
        problems.append(f"{opener!r} from {where(at)} is never closed")
    return problems


@pytest.mark.parametrize("source", [
    "function f(a) { return a; }",
    "const s = 'it\\'s'; const t = \"a\\\"b\";",  # escaped quotes inside strings
    "const t = `a ${x} b ${ `nested ${y}` } c`;",
    "const t = `an escaped \\` backtick and \\${ not an expression`;",
    "const re = /[\"'`(){}]/g; const ok = re.test(x);",  # quotes and brackets inside a regex, and in a class
    "const re = /a\\/b[/]c/i;",  # an escaped slash and a slash inside a class
    "const half = total / 2 / 3;",  # division is not a regex
    "const q = (a + b) / 2; const r = x[0] / 2;",
    "if (x) { y(); } // a comment with ( [ { ' \" ` and no end",
    "/* a block comment with ( and ` and ' */ let z = 1;",
    "const url = 'http://example.test/a//b'; const t = `//${x}//`;",
    "return_value = typeof /x/ === 'object' ? [1, 2, {a: 3}] : null;",
    "x = cond ? `a` : `b${c}`; y = {k: `v`}[k];",
])
def test_tokenizer_accepts_valid_snippets(source):
    assert js_problems(source) == []


@pytest.mark.parametrize("source,expected", [
    ("function f(a) { return a;", "'{' from line 1 is never closed"),
    ("function f(a) { return a; }}", "unexpected '}'"),
    ("call(1, 2];", "does not match '('"),
    ("const a = [1, 2);", "does not match '['"),
    ("const s = 'abc;\nlet x = 1;", "unterminated string from line 1"),
    ("const s = \"abc", "unterminated string"),
    ("const t = `abc", "unterminated template literal"),
    ("const t = `abc ${x`;", "unterminated template literal"),
    ("const t = `abc ${x + (1}`;", "does not match '('"),
    ("const t = `abc ${x ;", "never closed"),
    ("/* a comment that never ends\nlet x = 1;", "unterminated comment"),
    ("const re = /abc;\nlet y = 2;", "unterminated regular expression"),
    ("if (a) {\n  b();\n", "never closed"),
])
def test_tokenizer_rejects_broken_snippets(source, expected):
    problems = js_problems(source)
    assert problems and any(expected in problem for problem in problems), problems


# ------------------------------------------------------------------------------------------- shipped scripts ---
def shipped_files() -> list[Path]:
    package_data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = package_data["tool"]["setuptools"]["package-data"]["themis_shield"]
    return sorted({path for pattern in patterns for path in PACKAGE.glob(pattern) if path.is_file()})


def shipped_scripts() -> list[Path]:
    return [path for path in shipped_files() if path.suffix in SCRIPT_SUFFIXES]


class _Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts, self.links, self.icons = [], [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("src"):
            self.scripts.append(attrs["src"])
        if tag == "link" and attrs.get("href"):
            self.links.append(attrs["href"])
            if "icon" in (attrs.get("rel") or "").lower().split():
                self.icons.append(attrs["href"])


def _local(reference: str, origin: Path = STATIC) -> Path | None:
    """The shipped file a page or import reference points at (None for anything that leaves the app)."""
    if re.match(r"^[a-z][a-z0-9+.-]*:|^//", reference, re.I):
        return None
    path = reference.split("#")[0].split("?")[0]
    base = STATIC if path.startswith("/") else origin.parent
    return (base / path.lstrip("/")).resolve()


def _imports(path: Path) -> list[tuple[str, list[str]]]:
    """(file, imported names) of every static or dynamic import; names are listed for `import { a, b } from` only."""
    source = path.read_text(encoding="utf-8")
    quoted = r"""["'](?P<src>[^"']+)["']"""
    found = [(match["src"], [part.split(" as ")[0].strip() for part in match["names"].split(",") if part.strip()])
             for match in re.finditer(r"^\s*import\s*\{(?P<names>[^}]*)\}\s*from\s*" + quoted, source, re.M)]
    for pattern in (r"^\s*import\s+(?!\{)[^\"';{]+\sfrom\s*" + quoted, r"^\s*import\s*" + quoted,
                    r"\bimport\(\s*" + quoted + r"\s*\)"):
        found += [(match["src"], []) for match in re.finditer(pattern, source, re.M)]
    return found


def _exports(path: Path) -> set[str]:
    source = path.read_text(encoding="utf-8")
    names = set(re.findall(r"^export\s+(?:async\s+)?(?:function\*?|class|const|let|var)\s+([A-Za-z_$][\w$]*)",
                           source, re.M))
    for group in re.findall(r"^export\s*\{([^}]*)\}", source, re.M):
        names |= {part.split(" as ")[-1].strip() for part in group.split(",") if part.strip()}
    return names


def test_package_data_ships_only_kinds_of_file_that_are_checked():
    files = shipped_files()
    assert {path.name for path in files} >= {"index.html", "app.js", "api.js", "render.js", "reasons.js", "app.css"}
    unknown = [path for path in files if path.suffix not in KNOWN_SUFFIXES]
    assert not unknown, f"a new kind of shipped file: is it a script that needs a parse check? {unknown}"
    # A script in a sub-folder of web/static is not matched by the "web/static/*" package-data pattern: not shipped.
    assert {path for path in STATIC.rglob("*") if path.suffix in SCRIPT_SUFFIXES} <= set(shipped_scripts())


@pytest.mark.parametrize("script", shipped_scripts(), ids=lambda path: path.name)
def test_every_shipped_script_has_balanced_brackets_strings_and_templates(script):
    assert js_problems(script.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("script", shipped_scripts(), ids=lambda path: path.name)
def test_the_checker_would_catch_damage_to_every_shipped_script(script):
    source = script.read_text(encoding="utf-8")
    last = max(source.rfind(char) for char in ")]}")
    assert js_problems(source[:last] + source[last + 1:]), "a lost closing bracket went unnoticed"
    assert js_problems(source + "\nconst broken = `never closed;\n"), "an unterminated template went unnoticed"
    assert js_problems(source + "\nconst broken = 'never closed;\n"), "an unterminated string went unnoticed"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH (the Python-side checks still run)")
@pytest.mark.parametrize("script", shipped_scripts(), ids=lambda path: path.name)
def test_node_accepts_every_shipped_script_as_a_module(script, tmp_path):
    copy = tmp_path / (script.stem + ".mjs")  # the page loads them as ES modules; .js alone is read as CommonJS
    copy.write_bytes(script.read_bytes())
    checked = subprocess.run([shutil.which("node"), "--check", str(copy)], capture_output=True, text=True, timeout=60)
    assert checked.returncode == 0, checked.stderr


def test_the_page_references_only_files_that_exist_and_every_script_is_used():
    assets = _Assets()
    assets.feed((STATIC / "index.html").read_text(encoding="utf-8"))
    assert assets.scripts, "index.html loads no script"
    # Without an icon link the browser asks for /favicon.ico, which is not shipped: a 404 console error on every load.
    assert assets.icons, "index.html declares no icon"
    roots = []
    for reference in assets.scripts + assets.links:
        target = _local(reference)
        assert target is not None, f"index.html reaches outside the app: {reference} (the page's CSP forbids it)"
        assert target.is_file() and STATIC in target.parents, f"index.html references {reference}: not shipped"
        if reference in assets.scripts:
            roots.append(target)
    scripts = {path.resolve() for path in shipped_scripts()}
    reached, pending = set(), list(roots)
    while pending:
        script = pending.pop()
        if script in reached:
            continue
        reached.add(script)
        assert script in scripts, f"{script.name} is loaded but is not shipped as a script"
        for reference, names in _imports(script):
            target = _local(reference, script)
            assert target is not None and target.is_file(), f"{script.name} imports {reference}: not shipped"
            missing = [name for name in names if name not in _exports(target)]
            assert not missing, f"{script.name} imports {missing} from {target.name}, which does not export them"
            pending.append(target)
    assert not scripts - reached, f"shipped but never loaded or imported: {sorted(p.name for p in scripts - reached)}"


# ------------------------------------------------------------------------------------ served and displayed ---
def _client(app, token=None):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 12345)), base_url=ORIGIN,
        headers={"Authorization": "Bearer " + token} if token else {},
    )


def test_every_shipped_file_loads_without_a_credential_but_the_api_does_not(tmp_path):
    # An Operator-only browser session has no client cookie. "/api.js" read as an API path (the prefix "/api") answered
    # 401, so the module graph, and with it the whole UI, never started for the Operator.
    h = harness(tmp_path)
    app = create_app(h.router, token=CLIENT, operator_token=OPERATOR)

    async def exercise():
        async with _client(app) as anonymous:
            page = await anonymous.get("/")
            assert page.status_code == 200 and "text/html" in page.headers["content-type"]
            for path in sorted(STATIC.iterdir()):
                served = await anonymous.get("/" + path.name)
                assert served.status_code == 200, f"/{path.name} answered {served.status_code}"
                assert served.content == path.read_bytes()
                if path.suffix == ".js":  # a module script is refused by the browser unless the type says JavaScript
                    assert "javascript" in served.headers["content-type"], served.headers["content-type"]
            for path in ("/api/settings", "/api/models", "/api", "/v1/models", "/health"):
                assert (await anonymous.get(path)).status_code == 401, path
            assert (await anonymous.get("/api/operator/queue")).status_code == 401
        # Presenting a credential to a static file changes nothing, and the Operator's cannot reach the client API.
        async with _client(app, OPERATOR) as operator:
            assert (await operator.get("/api.js")).status_code == 200
            assert (await operator.get("/v1/models")).status_code == 401

    try:
        asyncio.run(exercise())
    finally:
        h.cloud.close()


def test_withheld_in_url_reaches_the_page_and_the_page_shows_it(tmp_path):
    # R-01: the server passes the count in the final stream event; app.js must display it beside the other counts.
    h = harness(tmp_path)
    h.router.operator_console_enabled = True
    h.router.operator_identity = "synthetic-operator"
    h.fake.answer = "Summary for {token}. ![status](https://collector.example/p.png?n={token})"
    app = create_app(h.router, token=CLIENT, operator_token=OPERATOR)

    def final_event(response):
        events = [json.loads(line[6:]) for line in response.text.splitlines()
                  if line.startswith("data: {")]
        return [event["themis"] for event in events if event.get("themis", {}).get("route")][-1]

    async def exercise():
        async with _client(app, CLIENT) as client, _client(app, OPERATOR) as operator:
            preview = (await client.post("/api/preview", json={
                "text": "[user]\nMara Lindahl asks a synthetic question.", "attachments": [], "intent": "cloud",
            })).json()
            chat = {
                "preview_id": preview["preview_id"], "model": "themis/auto", "confirm_cloud": False,
                "messages": [{"role": "user", "content": "Mara Lindahl asks a synthetic question."}],
                "conversation_id": "synthetic-app",
            }
            held = final_event(await client.post("/api/chat", json=chat))
            assert held["route"] == "held" and held["withheld_in_url"] == 0
            for stage in (1, 2):
                approved = await operator.post(f"/api/operator/{held['job_id']}/approve", json={
                    "stage": stage, "reason": "ROUTINE_APPROVED"})
                assert approved.status_code == 200, approved.text
            sent = await client.post("/api/chat", json=chat)
            text = "".join(
                choice["delta"].get("content", "")
                for line in sent.text.splitlines() if line.startswith("data: {")
                for choice in json.loads(line[6:]).get("choices", [])
            )
            assert "collector.example/p.png?n=\u27e6P:" in text  # not restored inside the address
            assert text.startswith("Summary for Mara Lindahl.")  # restored beside it
            return final_event(sent)

    try:
        report = asyncio.run(exercise())
    finally:
        h.cloud.close()
    assert report["route"] == "cloud-tokenised"
    assert (report["withheld_in_url"], report["restored"], report["unknown_tokens"]) == (1, 1, 0)
    # The page reads the field next to unknown_tokens / dropped_tokens (headless Chrome shows it: runs/webui).
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "metadata.withheld_in_url" in script and "metadata.unknown_tokens" in script