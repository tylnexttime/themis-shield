# themis-shield

**See everything locally, so the cloud doesn't have to.**

themis-shield is a local-first privacy gate for cloud AI. Before a prompt or document leaves your machine for a cloud
language model, themis-shield reads all of it on your own hardware, finds the personal and sensitive information, and
then does one of three things:

- **RELEASE**: replace every finding with an opaque placeholder, send the cleaned text to the cloud model, and put
  the original values back into the answer locally. The cloud never sees the real values.
- **ASK**: hold the job and show you what was found, because something sensitive (a health condition, a criminal
  record, a family-violence disclosure) needs a human decision.
- **BLOCK**: refuse to send. The cause can be a secret, a classification marking that means local-only, or a
  detection stage that failed. When the work has to stay local, themis-shield says so and can answer with a local
  model sized for your machine.

> In Greek myth, Themis holds the scales, and she was the oracle at Delphi before Apollo: she weighs and she
> advises. Justitia wears a blindfold; Themis doesn't, and that's the point. The gate is the one part that has to see
> everything. The cloud is the part that ends up blindfolded.

**Status: v0.1.0, preview.**
- **Evaluation:** detection and policy were evaluated on a public synthetic data set, with 5 released leaks in 2,601
  gold spans on the held-out split. It still **failed our own pre-registered pass rule**, because it asks too often on
  clean documents (see [Evaluation](#evaluation)).
- **The round trip** (de-identify, send, restore) is covered by automated tests against a stand-in cloud endpoint.
- **Not cleared for real personal data.** By design, v0.1 refuses to start unless it is configured for synthetic
  data.

---

- [Why](#why) · [So what](#so-what) · [Words we use](#words-we-use) · [What v0.1 lets you do](#what-v01-lets-you-do) ·
  [What it does not do yet](#what-it-does-not-do-yet)
- [How it works](#how-it-works) · [Quick start](#quick-start) · [Claude Code hooks](#claude-code-hooks) ·
  [Evaluation](#evaluation) · [Back ends](#back-ends-llamacpp-marian-strata) · [Next](#next)
- [The journey](#the-journey) · [Sources](#sources-and-acknowledgements) · [Who made this](#who-made-this) ·
  [Licence](#licence) · [Security](SECURITY.md) · [Changelog](CHANGELOG.md) · [Architecture](docs/ARCHITECTURE.md)

## Why

Cloud language models are very good, and the documents people most want help with are full of other people:
names, phone numbers, health notes, a line about someone's court case, a staff list in a spreadsheet. Pasting that
into a cloud model sends it to someone else's computer, often in another country, under terms you didn't negotiate.
OWASP lists this among the top ten risks for LLM applications
([LLM02:2025 Sensitive Information Disclosure](https://genai.owasp.org/llmrisk/llm022025-sensitive-information-disclosure/)).

The usual answers are all-or-nothing: ban cloud AI, or trust it with everything. Data protection practice has a
well-understood middle path, **pseudonymisation**: personal data is replaced so that it can't be tied to a person
without extra information, which is kept separately and protected
([GDPR Art. 4(5)](https://eur-lex.europa.eu/eli/reg/2016/679/oj);
[NIST SP 800-188](https://csrc.nist.gov/pubs/sp/800/188/final);
[OAIC, De-identification and the Privacy Act](https://www.oaic.gov.au/privacy/privacy-guidance-for-organisations-and-government-agencies/handling-personal-information/de-identification-and-the-privacy-act)).
In themis-shield the "extra information" is the placeholder map, and it never leaves your machine.

Doing this by hand is slow and error-prone. Doing it with a single regex library or a single model misses things:
names in a table column, initials, a nickname three paragraphs after the full name, "the only woman on the night
shift". LLMs can infer who someone is from exactly this kind of indirect detail
([Staab et al., 2023](https://arxiv.org/abs/2310.07298)), and each type of PII detector fails in its own way once
text gets messy ([Zafar & Nowaczyk, 2026](https://arxiv.org/abs/2609.03464)). So detection has to be layered, it has
to run locally, and it has to be honest about what it isn't sure of.

Perplexity showed the shape of the answer with a small on-device detector that lets an application redact, keep
data local, or ask before routing to the cloud
([PII-TRACE](https://www.perplexity.ai/hub/blog/pii-trace-detecting-personal-data-before-it-leaves-the-device)).
themis-shield builds that idea into a complete, open, auditable round trip that runs on an ordinary laptop.

## So what

- **You can use cloud models on real work without handing over the people in it.** The cloud sees
  `⟦P:3fa9c1⟧ reported ⟦S:91b2e0⟧`. You see the answer with the real name and statement back in place.
- **When the cloud is the wrong place, you're told, and you can stay local.** Classification markings, secrets and
  anything your policy marks local-only never leave the device.
- **Uncertainty becomes a human decision, not a silent leak.** Sensitive statements go to ASK with the findings
  shown, instead of being guessed at.
- **It's measurable.** It ships with a public synthetic evaluation set, a scorer, a frozen evaluation plan and the
  numbers from our runs, so you can check our claims and test your own configuration before trusting it.
- **It's adoptable.** Everything specific to an organisation lives in a profiles folder outside the code: marking
  schemes, an allow-list of the organisation's own public names, which levels stay local, the cloud endpoint, the
  local model catalogue. Use themis-shield as a dependency and keep your settings in your own repository.

## Words we use

- **Gate:** themis-shield itself: the local check every prompt passes before it may leave.
- **Tier 1 / tier 2:** the cheap detectors (rules and PII-Tracer, on the CPU) and the local LLM finders (on the GPU).
  In v0.1 every document goes through both tiers ("thorough mode").
- **Quasi-identifier:** a detail that isn't a name but can single someone out, such as a rare job title next to a
  postcode.
- **Released leak:** a piece of personal data left unprotected in a document the gate let through to the cloud.
- **Friction:** clean documents (no personal data) that the gate held for ASK anyway.
- **Frozen kit:** code, prompts and settings fixed in git before a test run, so the result can't be tuned afterwards.
- **MoE, `-ncmoe`, KV cache:** a mixture-of-experts model only activates part of itself per token. llama.cpp's
  `-ncmoe N` keeps the experts of N layers in system RAM, which is how a 26B model fits an 8 GB GPU. The KV cache is
  the model's working memory for the context.

## What v0.1 lets you do

- **Run a privacy-gated chat assistant locally.** `themis serve` starts a web app on `127.0.0.1`, opened with a
  one-use code. It shows what was found and what was sent, holds each cloud job until a second, configured identity
  (the Operator) approves it, and streams the cloud answer with your original values restored. It also serves
  OpenAI-compatible `/v1/chat/completions` and `/v1/models` endpoints on loopback, so tools like
  [Kilo Code](examples/kilo.example.json) can use it as their provider.
- **Use any OpenAI-compatible cloud endpoint** (OpenAI, Azure OpenAI, or a compatible gateway). The API key stays
  in an environment variable whose *name* is in the config, and only the configured model can be addressed.
- **Detect personal data in layers:**
  - rule packs with checksums: `core` (emails, URLs, keys and secrets) is always on; `au` (TFN, Medicare, ABN, USI,
    Australian phone numbers) and `intl` (E.164 phone numbers, IBAN) are opt-in, and the rehearsal config enables
    both;
  - Perplexity's [PII-Tracer](https://huggingface.co/perplexity-ai/PII-Tracer) on the CPU;
  - local Gemma 4 finders via [llama.cpp](https://github.com/ggml-org/llama.cpp), with label definitions and
    indirect-phrasing examples (prompt v2);
  - propagation (the same name again, in tables, as initials, in other casing) and an allow-list built from your
    organisation profile.
- **Check classification markings first.** Profile-driven: `generic` (CONFIDENTIAL, RESTRICTED, DO NOT SHARE, …)
  by default, `au-pspf` for the
  [Australian Protective Security Policy Framework](https://www.protectivesecurity.gov.au/), or your own scheme.
  Which tiers need confirmation and which stay local-only is configuration.
- **Keep the placeholder map safe.** Placeholders are opaque tokens like `⟦P:3fa9c1⟧`. The letter is a coarse class
  (P person, C contact, A address, I identifier, D date, S sensitive statement, Q quasi-identifier), and the rest is
  random, so a placeholder reveals neither the value nor a count. The map lives in a local vault with AES-256-GCM. On
  Windows the key is wrapped with
  [DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata); elsewhere it is
  derived from a passphrase with scrypt. Without a real protector, the vault refuses to start.
- **Gate Claude Code** through managed hooks ([details](#claude-code-hooks)).
- **Start locally and hand over deliberately.** Build a de-identified package, record that a peer checked it (a
  recorded step, not an authenticated second person), bind an approval to its hash, then hand over exactly that
  package. The package is re-scanned before export, and the vault never travels.
- **Keep a job register.** Every job gets a `JOB-YYYY-NNNN` record in a local SQLite store. The history tables
  (audit, evidence, approvals) are append-only: database triggers refuse updates and deletes. A job's status moves
  forward through audited transitions, and jobs can't be deleted. This guards against mistakes, not against someone
  with access to the file. Cloud-bound jobs use two-stage approval: approve the de-identified summary first, then
  bind the approval to the hash of the finished package.
- **Fit the work to the machine.** The runtime keeps a catalogue of local models with launch settings and speeds
  measured on the device. Its scheduler checks free GPU and system memory before starting a model, keeps one model
  resident at a time, and orders a batch by model so each loads once. If the gate is killed, its model servers are
  cleaned up: a Windows Job Object, plus a startup check that matches pid, start time and executable name before it
  kills anything.
- **Use bigger local models when the work must stay local:** [Strata](https://github.com/Niko1221/Strata) as an
  attached answering model, and [Marian](https://github.com/tylnexttime/marian) to plan launch settings from
  measurements ([details](#back-ends-llamacpp-marian-strata)).
- **Reproduce our evaluation** with `eval/`: the generators, the cases, the scorer, the runner, the frozen plan.

## What it does not do yet

Read this section before you rely on anything above.

- **It is not cleared for real personal data.** v0.1 refuses to start unless `[gate] synthetic_only = true`. That is
  a switch you set deliberately, not a detector. Everything published here was measured on synthetic text written by
  two generators that share one author family. Before anyone points it at real data, run the evaluation on a sample
  of your own documents, labelled by people, with the same frozen-kit discipline.
- **De-identified is not anonymous.** Pseudonymised text is still personal data under the GDPR (Recital 26), and
  the context around a placeholder can still single someone out. ASK exists because of this; it doesn't make the
  risk zero ([OAIC](https://www.oaic.gov.au/privacy/privacy-guidance-for-organisations-and-government-agencies/handling-personal-information/de-identification-and-the-privacy-act);
  [NIST SP 800-188](https://csrc.nist.gov/pubs/sp/800/188/final)).
- **Quasi-identifiers don't trigger ASK.** When the detectors find one it is replaced, but the policy doesn't hold
  the document for a human. A rare job title the detectors miss in an "anonymised" table goes out. All five leaks on
  our test split were of this kind.
- **Too much friction.** On our test split, 11 of 36 documents with no personal data were held for ASK, which fails
  our own pass rule (see [Evaluation](#evaluation)). About a third of institutional look-alikes are still redacted.
- **Sensitive statements depend on the LLM finders.** They scored well on synthetic text thanks to prompt v2, but
  real writing is more varied. A dedicated sentence-level classifier is on the roadmap.
- **Prompt injection against the detectors** is tested only with our own synthetic injection cases. We found no
  published study of injection attacks on PII detectors; treat it as an open risk.
- **English first, Australian-first taxonomy.** The label set includes Australian identifiers (TFN, Medicare, ABN,
  USI). Other national identifiers are covered by generic patterns and the LLM finders, not by checksummed rules.
- **Only OpenAI-compatible cloud APIs.** There is no native Anthropic Messages leg: approvals are bound to the hash
  of the exact OpenAI-shaped request, so a second wire format needs translation inside the router. Claude Code is
  supported through hooks instead.
- **Text only.** No file uploads yet (PDF, Word, images).
- **No central register.** The register is a local SQLite store; transport, authentication and synchronisation to a
  shared service are skeletons. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) lists every module that is still a
  skeleton.
- **Windows-tested only.** The code is written to be portable (paths, the passphrase vault), but no Linux or macOS
  run has been done.
- **Slow on purpose.** Every document goes through both finders, taking a median of 10 seconds (mean 14) on an
  8 GB laptop GPU. It is built for careful work, not for high volume.
- **No recorded run against a commercial cloud model since this rebuild.** The cloud leg is tested against a stand-in
  endpoint.

## How it works

```
 your prompt or document
          │
          ▼
 ┌──────────────────────┐   marked local-only? ──────────► BLOCK for cloud · offer a local model
 │ 1. markings check    │   (profile-driven: generic, au-pspf, or your own)
 └──────────┬───────────┘
            ▼
 ┌──────────────────────┐   tier 1 (CPU, fast)
 │ 2. detection         │     rule packs (checksummed IDs, emails, URLs, secrets)
 │                      │     PII-Tracer token classifier
 │                      │     propagate: the same name in tables, initials, other casing
 │                      │     allow-list from your organisation profile
 │                      │   tier 2 (GPU; every document in v0.1)
 │                      │     Gemma 4 E4B and Gemma 4 26B-A4B finders via llama-server
 │                      │     union of everything, then propagate and allow-list again
 └──────────┬───────────┘
            ▼
 ┌──────────────────────┐
 │ 3. decision          │   RELEASE · BLOCK (secret, failed stage) · ASK, for any of: a sensitive statement;
 │                      │   injection signs; finders silent (or finding far fewer people) while the classifier
 │                      │   found something; finder values the text doesn't actually contain
 └──────────┬───────────┘
            ▼ RELEASE (or ASK, approved)
 ┌──────────────────────┐
 │ 4. substitute        │   values → opaque placeholders (⟦P:3fa9c1⟧); the map is sealed in the local vault
 └──────────┬───────────┘
            ▼
 ┌──────────────────────┐
 │ 5. cloud call        │   held until the Operator approves; the exact outgoing request is checked to contain
 └──────────┬───────────┘   none of the original values, then hashed, and the approval is bound to that hash
            ▼
 ┌──────────────────────┐
 │ 6. restore           │   placeholders in the streamed answer → original values, locally
 └──────────────────────┘
```

Every stage is bounded and fails closed. A stage that fails or returns malformed output produces BLOCK, never a
silent RELEASE. The web app checks the final request for every original value. The Claude Code hooks path goes
further and re-scans its replacement text until nothing new is found (at most three passes, then BLOCK). It needs
to, because it hands text back to another program. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) has the details.

## Quick start

**Requirements:**
- Python 3.12.
- Windows 10 or 11; other platforms are untested.
- For the full stack: an NVIDIA GPU with 8 GB of VRAM, 32 GB of RAM, llama.cpp's `llama-server`, and the model
  weights below.

**1. Install.**

```powershell
git clone https://github.com/tylnexttime/themis-shield
cd themis-shield
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,tracer]"   # tracer = PII-Tracer (torch, transformers)
.\.venv\Scripts\python.exe -m pytest                             # offline test suite, about a minute
.\.venv\Scripts\themis.exe --version
```

**2. Get the engine and the models.** Nothing is bundled; download them from their publishers. By default themis
looks for:

```
%LOCALAPPDATA%\themis-shield\engine\llama-server.exe                   llama.cpp release (we used build b11760)
%LOCALAPPDATA%\themis-shield\models\gemma-4-E4B-it-Q4_K_M.gguf          light finder
%LOCALAPPDATA%\themis-shield\models\gemma-4-26B-A4B-it-UD-IQ3_XXS.gguf  heavy finder (what we evaluated)
%LOCALAPPDATA%\themis-shield\models\pii-tracer\                         PII-Tracer folder
```

```powershell
$M = "$env:LOCALAPPDATA\themis-shield\models"
hf download unsloth/gemma-4-E4B-it-GGUF gemma-4-E4B-it-Q4_K_M.gguf --local-dir $M
hf download unsloth/gemma-4-26B-A4B-it-GGUF gemma-4-26B-A4B-it-UD-IQ3_XXS.gguf --local-dir $M
hf download perplexity-ai/PII-Tracer --local-dir "$M\pii-tracer"
```

The llama-server build comes from [ggml-org/llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases); pick
the CUDA build for your GPU. Sources:
- [unsloth/gemma-4-E4B-it-GGUF](https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF)
- [unsloth/gemma-4-26B-A4B-it-GGUF](https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF); UD-IQ4_XS with
  `-ncmoe 20` is the larger alternative
- [perplexity-ai/PII-Tracer](https://huggingface.co/perplexity-ai/PII-Tracer)

PII-Tracer's model code runs with `trust_remote_code`. themis loads only your local copy (`local_files_only`,
safetensors). Read the folder's Python files before first use, and record the revision you reviewed in
`[detection] tracer_artefact_manifest_id`. To keep the files elsewhere, point a runtime settings file at your own
folders.

**3. Configure a synthetic rehearsal.**

```powershell
Copy-Item config\rehearsal.example.toml config\local.toml      # gitignored; set [cloud] base_url and model
$env:THEMIS_CLOUD_API_KEY = "<your key>"                        # the variable's NAME is set by [cloud] api_key_env
```

[`config/rehearsal.example.toml`](config/rehearsal.example.toml) is a complete configuration for synthetic data.
It turns on the switches the minimal [`config/themis.example.toml`](config/themis.example.toml) keeps off: that one
starts but refuses every chat. It authorises the cloud and local routes, turns on the Operator console with a
separate approver identity, and uses the evaluated pipeline (rule packs `core,au,intl`, the
`"a training and research institute"` finder context, and the fictional `harbourline-eval` profile). Every switch is
explained next to it.

**4. Start and send.**

```powershell
.\.venv\Scripts\themis.exe serve --config config\local.toml --open    # http://127.0.0.1:8742
```

`--open` creates the local gate token and opens the browser with a one-use code. Without `--open`, run
`themis serve --provision-token` once first.

Send a chat message. If it would go to the cloud, it is held. Open the **Operator** tab, approve the job, then go back
and re-send: approval never sends anything by itself.

If startup is refused, the line `Themis startup refused [CODE]: …` says why: a missing model, the engine, the vault
protector, the token, the port. `--skip-model-check` starts without local models, but every request that needs them
is then blocked.

Other entry points: `themis profiles` (shipped and external profiles), `themis runtime status` (models, back ends,
memory), `themis register --help` (job register), `themis hooks` (lists the hook events). Off Windows, set
`[vault] protector = "passphrase"` and `THEMIS_VAULT_PASSPHRASE` (at least 12 characters).

To adopt it in an organisation, put your own `markings/`, `institution/`, `catalogue/` and `cloud/` JSON files in a
folder you control and set `[profiles] dir` (or `THEMIS_PROFILES_DIR`). A file with a shipped name overrides the
shipped profile.

## Claude Code hooks

themis-shield can sit in front of [Claude Code](https://code.claude.com/docs/en/hooks) through six hooks:
UserPromptSubmit, PreToolUse, PostToolUse, MessageDisplay, SessionStart and Stop. The gate never reads or proxies
your Claude credentials; the hooks talk only to the local gate.

- **Deploy** through [managed settings](https://code.claude.com/docs/en/managed-settings) with
  `allowManagedHooksOnly`. [`examples/claude-code-managed-settings.example.json`](examples/claude-code-managed-settings.example.json)
  shows the shape. It sets `"onFailure": "block"` (Claude Code 2.1.295 or later): hooks fail *open* without it.
- **Hooks need a running `themis serve`** and its gate token.
- **Closed by default** (`[hooks]` in the config). A prompt with personal data is blocked, and a de-identified draft
  is written to `<app data>\hooks\<session>\` (or `[hooks] drafts_dir`) for you to paste. Other prompts are refused,
  and file tools are denied. Claude Code doesn't let a hook rewrite a prompt, so the experience is "paste the draft",
  not transparent rewriting.
- **To let clean prompts and tool calls through,** set `[hooks] cloud_authorised = true` together with
  `[gate] synthetic_cloud_authorised = true`, and list the workspace directories in `[hooks] roots`. Optional keys:
  `allowed_tools` (a subset of Read, Write, Edit, Glob, Grep), `drafts_dir`, `destination_id`, `model`,
  `deployment_type`.

## Evaluation

themis-shield ships a fully synthetic evaluation set ([`eval/`](eval/README.md)): 521 cases (225 dev, 296 test) with
4,483 gold spans in 27 labels, hard negatives (institutional look-alikes that must *not* be redacted), and
prompt-injection cases. The organisation in it, "Harbourline Institute", is invented, and every identifier is
invented, and checksum-valid where its format has a checksum. Two independent generators wrote it. It is split by
template, so no sentence frame is shared between dev and test. `eval/run_eval.py` runs the real detection and policy
(rules, PII-Tracer, Gemma 4 E4B and 26B-A4B through llama.cpp, union, allow-list, policy), and `eval/score.py`
scores it.

**Method.** We chose the 26B quantisation with a rule written before the baselines ran, and tuned prompt v2 on dev
only, in two iterations. Then we froze the kit. The pass rule and a negative control are in
[`eval/FROZEN-PLAN.md`](eval/FROZEN-PLAN.md), committed before the test split was opened. We ran test once per arm
and changed nothing afterwards.

| Test split (296 cases, 2,601 gold spans), thorough mode | Prompt v2 (frozen) | Prompt v1 (negative control) |
|---|---|---|
| **Released leaks** (unprotected spans in cases the gate let through) | **5** | 12 |
| R_any (gold spans touched by a redaction) | **0.997** | 0.977 |
| R_full (gold spans fully covered) | **0.984** | 0.911 |
| Sensitive statements, R_any (8 categories) | **1.000** | 0.859 |
| Precision (any label) | 0.926 | 0.921 |
| Released leaks in the 44 injection cases | 0 | 0 |
| Decisions RELEASE / ASK / BLOCK | 74 / 198 / 24 | 89 / 183 / 24 |
| Clean cases held for ASK (of 36) | 11 | 2 |

**Verdict: v2 failed our own pass rule.** It met four conditions: released leaks ≤ 5, R_any ≥ 0.98, sensitive
R_any ≥ 0.95, and no failed stage. It missed the fifth, friction on clean cases ≤ 0.25, at **0.306**.
- **Why:** all 11 of those documents hit one rule. Both LLM finders correctly found nothing, while PII-Tracer had
  flagged something (mostly meeting dates and role mailboxes) or rated the document as sensitive, so the policy held
  them for a human. That's a policy and tier-1 problem, not a prompt problem. The fix is the first item in
  [Next](#next).
- **What leaked:** all five released leaks are job titles in one "anonymised" table layout, where the DOB and
  postcode beside them were redacted. Quasi-identifiers don't trigger ASK in v0.1. The control leaked the same five,
  plus seven more.
- **Over-redaction:** about a third of the hard negatives are still redacted, almost all of them by tier 1.

**Quantisation.** On dev, Gemma 4 26B-A4B **UD-IQ3_XXS** matched UD-IQ4_XS: 12 released leaks each, and sensitive
R_any 0.909 vs 0.912. It generated 36 tokens/s against 24. Under the rule written beforehand, we adopted it. The lead
came from Claude Tyl's Marian measurements under desktop memory pressure.

**Speed** (one laptop: 8 GB laptop GPU, 31.5 GB RAM, llama.cpp b11760). Median 10 s per document and mean 14 s, with
both finders on every document; 296 cases took 70 minutes. Gemma 4 E4B generates at 62 tokens/s, and the 26B
UD-IQ3_XXS at 36 tokens/s with `-ncmoe 18`. The 1,700-token v2 instructions are a fixed prefix that llama-server
caches: on test, 88% of the finder prompt tokens came from the cache.

**Provenance.** The runs used our private development history at a frozen commit. This repository was squashed from
that history for publication. Every file the v2 arm depends on is byte-identical here; the sha256 list is in
[`eval/RESULTS.md`](eval/RESULTS.md). That covers the detectors, the prompt, the policy, the eval profile and
catalogue patch, the runner, the scorer and the cases. The v1 control's demo profile has been rewritten since.

**Caveats.**
- Synthetic text is easier than real documents. Identifier recall was 1.000 for both prompts, so this set doesn't
  separate identifier detectors.
- The set, the prompt examples and the harness come from the same model family.
- Each figure is a single run on one machine.

Read these numbers as a regression baseline and an upper bound, not a production estimate. Full details: every run,
the crashed run and its fix, the label mapping, and the per-label tables are in
[`eval/RESULTS.md`](eval/RESULTS.md).

## Back ends: llama.cpp, Marian, Strata

themis-shield runs local models through [llama.cpp](https://github.com/ggml-org/llama.cpp)'s `llama-server`, which
it starts, supervises and stops itself: loopback only, allow-listed arguments, one model resident at a time. Two
optional back ends extend that.

### Marian: measured launch settings for llama.cpp

[Marian](https://github.com/tylnexttime/marian) (Apache-2.0) is our sibling project. It measures what your machine
can do with a llama.cpp model and plans the fastest configuration that fits a requested context (context size,
KV-cache type, GPU/CPU expert split), and it never extrapolates. Use it when the right `-ncmoe` and KV settings depend
on the machine, which on consumer GPUs they do.

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[marian]"    # from a source checkout; installs Marian from GitHub
python -m marian profile model.gguf --engine path\to\llama-server --ctx 8192,16384 --kv f16,q8_0   # once, GPU free
```

Then add `"planner": "marian"` to a llama.cpp catalogue entry. Before each load, themis-shield asks Marian (from
saved profiles only) for that entry's context. The plan passes the same argument allow-list as the catalogue, and the
runtime log records which planner chose the arguments. Newer Marian profiles record the model's own share of GPU
memory separately from the desktop's, and themis-shield uses that figure when it's there. Without Marian, without a
profile for this machine and model, or when nothing measured fits, the catalogue's arguments are used and the log
says why. On our 8 GB laptop, for the **UD-IQ4_XS** 26B weights at 16K context, Marian's plan matched the setting we
had chosen by hand: `-ncmoe 20`, f16 KV, measured at 26.6 tokens/s. We haven't profiled the UD-IQ3_XXS default yet.

A catalogue entry can also name an alternative engine, such as a llama-server built with Marian's expert-cache
patches: `"engine_path": "marian-patched/llama-server"` (relative to `[runtime] engines_dir`), with allow-listed
`"engine_args"` (`--moe-cache-mib`, `--moe-tier-mib`, `--moe-ring-mib`, `--moe-miss-source`).

### Strata: a large local answering model you run yourself

[Strata](https://github.com/Niko1221/Strata) (MIT) runs mixture-of-experts models far larger than your GPU by
streaming experts between GPU, RAM and SSD, and serves an OpenAI-compatible endpoint. Use it for the "do it offline"
path: work that must stay on the device and needs a stronger model than the small finders. On an 8 GB laptop GPU,
expect about 10-12 tokens/s and minutes to the first token on long prompts.

themis-shield never starts, installs or updates Strata. Start Strata your own way (loopback only), then declare it in
a catalogue profile:

```json
{"id": "strata-qwen3.8-flash-next", "runtime": "strata",
 "endpoint": "http://127.0.0.1:8095/v1", "served_model": "qwen3.8-flash-next-gyro-s"}
```

Before attaching, themis-shield checks `/health` and `/v1/models`. It refuses any endpoint that isn't loopback, sends
one request at a time with a thinking-model token floor and timeouts derived from measured speeds, and never returns
the model's reasoning. Strata is an answering model only, never a PII finder. While it is attached, its configured GPU
memory counts as occupied, so themis-shield won't start a llama-server beside it that can't fit. We tested the
attachment live against Strata serving Qwen3.8-Flash-Next on the same laptop.

## Next

Each item below is a known gap from our own runs, in the order we plan to work on them. A fix that changes detection
or policy needs a **fresh test split**: v0.1's test split has been seen and can't be used to validate it.

Items 1 to 3 are implemented for v0.2 and tuned on development data only (`eval/V02-DEV-LOG.md`); they are not
claimed until they pass a fresh held-out split.

1. **Friction.** Stop the empty-finder rule from holding clean documents: when both LLM finders return nothing,
   weigh what PII-Tracer flagged by label and allow-list, instead of asking every time.
2. **Quasi-identifiers.** Let them count in the policy, and add a column-aware pass that treats rare values in
   "anonymised" tables (job titles, small-group attributes) as quasi-identifiers.
3. **Tier-1 false positives:**
   - PII-Tracer labels public meeting times as dates of birth;
   - a trailing full stop lets a role mailbox escape the allow-list;
   - the USI checksum matches ordinary ten-letter words about one time in 32;
   - the organisation's own checksum-valid ABN is never allow-listed.
4. **A sentence-level sensitive-statement classifier**, fine-tuned on synthetic data, alongside the span finders.
5. **A fast path.** Tier 1 alone for short, low-sensitivity text, evaluated before it is offered.
6. **A real-data evaluation protocol:** how to sample, label and score your own documents with the frozen-kit
   discipline, before unlocking `synthetic_only`.
7. **Platform and reach:** Linux and macOS runs, file inputs (PDF, Word), a native Anthropic Messages leg, a central
   register service.

Contributions, critiques and independent evaluations are welcome. To report a bypass or a leak privately, see
[SECURITY.md](SECURITY.md).

## The journey

The code in this repository came together in four days, from 21 to 24 October 2026, on top of a question we'd been
turning over for longer. The repository itself starts with this release; the early experiments described here
happened in private work that isn't part of its history. We're writing the journey down because most of what makes
it work was learned by being wrong first.

**The question.** Could we use the strongest cloud models on documents that contain other people's personal
information, without sending that information? And could we answer it on one laptop: an 8 GB laptop GPU, 32 GB of
RAM, no data centre?

**A lab before a product.** Before writing any gate code, we built a synthetic evaluation set and lined up candidate
detectors: a rules library, half a dozen small and mid-sized local LLMs, and Perplexity's PII-Tracer when it
appeared. Three lessons arrived on the first day:

1. *No single detector is enough.* The best single model caught about nine in ten personal-data spans. Rules,
   PII-Tracer and an LLM finder fail on different things, so together they caught almost everything.
2. *Deterministic code does the boring parts better than any model.* Once a name has been found, finding it again
   in a table column, as initials or in different casing is code, not inference.
3. *Model family matters more than size or architecture.* A Gemma 4 mixture-of-experts model with about 4B active
   parameters was the only local model that reliably caught quasi-identifiers and sensitive statements. Bigger models
   from another family did worse.

**Over-redaction is a real cost.** Our first stack also blanked out public, institutional names (an office, a site,
a product code), which makes a cloud answer useless. The fix was an allow-list built from a deployment profile, and
it taught us a lesson the hard way: *a bare place name can't be allow-listed*, because the same word is also
someone's suburb in a home address. Only longer phrases can be.

**Independent evidence, or it didn't happen.** We froze the whole kit (code, prompts, configuration) as a git tag,
ran a held-back test split once, then wrote a second evaluation set with a different generator and its own profile
and ran the frozen kit on that. On those earlier synthetic sets, since retired (this repository ships a new one
built from scratch), the full stack released one leaked span out of about three thousand, and none from the
prompt-injection cases. The weak spot was, and still is, sensitive statements written indirectly.

**From detector to round trip.** A detector that finds things isn't a gate. The gate grew the parts that make a
result usable:

- placeholders that keep documents readable;
- an encrypted local vault;
- local restoration of the answer;
- a policy that turns findings into RELEASE, ASK or BLOCK;
- classification markings checked *before* personal data;
- a job register with two-stage approval.

**Fitting it on a laptop.** The hard engineering problem is running two LLM finders, a classifier and normal desktop
work on 8 GB of VRAM. The answer was tiers: cheap checks first, a scheduler that knows what fits, and a catalogue of
local models with speeds measured on the device, not guessed. Marian came out of the same question: measure what the
machine can do, then plan and launch to fit. While measuring Marian, Claude Tyl found that a smaller quantisation of
the 26B finder ran much faster under memory pressure. Our evaluation confirmed it kept the same recall, so this
release uses it.

**Then a router and an app.** A gate is only useful where people already work. So it became a local chat-style web
app, and Claude Code hooks for people who work in a terminal.

**Then this repository.** We rebuilt it as a clean, general, open project:

- every organisation-specific setting moved out to profiles;
- a new public evaluation set with two independent generators;
- prompt v2, tuned on the development split only;
- a frozen plan with a pass rule and a negative control written before the test split was opened;
- a passphrase-protected vault for macOS and Linux;
- two independent reviews, one for accuracy and one for anything that shouldn't be published;
- this README.

## Sources and acknowledgements

Every external source is listed in [docs/SOURCES.md](docs/SOURCES.md): link, licence, what we use it for, and when
we checked it. In short:

- **Engine:** [llama.cpp](https://github.com/ggml-org/llama.cpp) (MIT, the ggml authors), including the
  [`--n-cpu-moe`](https://github.com/ggml-org/llama.cpp/pull/15077) expert offload that makes a 26B MoE fit 8 GB.
- **Models:** [Gemma 4](https://huggingface.co/google/gemma-4-26B-A4B-it) (Google DeepMind, Apache-2.0), in
  [Unsloth](https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF) dynamic GGUF quantisations;
  [PII-Tracer](https://huggingface.co/perplexity-ai/PII-Tracer) (Perplexity, MIT), and its paper
  [PII-TRACE](https://arxiv.org/abs/2609.22200).
- **Related projects:** [Marian](https://github.com/tylnexttime/marian) (Apache-2.0, our sibling project),
  [Strata](https://github.com/Niko1221/Strata) (MIT), and [Project Maya](https://github.com/mw00/project-maya) (MIT),
  whose tiered expert storage inspired Marian's.
- **Libraries:** FastAPI, Uvicorn, HTTPX, pyca/cryptography (AES-GCM, scrypt), psutil, PyTorch, Transformers, pytest.
- **Guidance:** OWASP Top 10 for LLM Applications 2025, GDPR Art. 4(5), NIST SP 800-188 and NIST IR 8053,
  ISO/IEC 20889:2018, and the OAIC's de-identification guidance.

No third-party code is vendored. Model weights and the engine are downloaded by you, under their own licences.

## Who made this

themis-shield is a collaboration between **Chris Tyl** ([tylnexttime](https://github.com/tylnexttime)) and
**Alder**, an AI collaborator (Anthropic's Claude, working with a persistent memory across sessions). **Claude Tyl**,
Alder's sibling instance (also Claude), built and measured Marian. Much of the code was written by AI coding agents
and then reviewed, tested and measured by us. The decisions are ours.

## Licence

[Apache License 2.0](LICENSE). Copyright 2026 tylnexttime. See [NOTICE](NOTICE).
