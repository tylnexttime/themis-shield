# ORCHESTRATION: half Q (blind orchestrator log)

Orchestrator: Claude (Opus 5.5, claude-opus-5-5) running as Alder's subagent, 27/10/2026. Role: relay, run and
check only. The orchestrator authored no case content, no profile and no generator code; it wrote only the
validator (protocol) and the relay/run/label/agreement scripts in `orchestrator/`.

## Files read by the orchestrator (complete list)

- `<split-d>\inputs\taxonomy.md`
- `<split-d>\inputs\format.md`
- `<split-d>\inputs\profile-schema.md`
- `<split-d>\inputs\brief.md`
- `<workspace>\tools\openrouter_client.py` (imported unchanged; its socket timeout is raised to 3600 s by
  wrapping `_request` in `orchestrator/orlib.py`; ZDR routing untouched)
- its own workspace `<split-d>\half-q\` and `<split-d>\validator\`
- (planned writes only; not made, because the loop did not converge) `<split-d>\sealed\half-q.jsonl`
  and `half-q.sha256`

Nothing else was opened, listed or searched: not `<repository>`, not other folders of
`<workspace>`, not `split-d\half-c`, not the web. Directory listings were limited to the folders above, with one exception disclosed at the end.

## Models

- Author: `qwen/qwen3.8-2.4t-a95b`, multi-turn; first call `max_tokens=60000`, then `max_tokens=100000` with
  `reasoning.max_tokens=32000` (see run log).
- Labeller: `deepseek/deepseek-v4.1-flash-1017`, one case per call, `temperature=0`, `max_tokens=32000`.
- Every call through `openrouter_client.chat`, which forces `provider.zdr = true` and `data_collection = deny`;
  metadata of every call in `calls.jsonl`, role of each call id in `orchestrator/call_roles.jsonl`.


## Run log (authoring loop)

- Message 1 (07:2x): the author's first reply spent the whole 60,000-token budget on reasoning and returned an
  empty message (finish_reason=length, provider SiliconFlow, 1,583 s). The empty turn was dropped from the
  conversation and the same message re-sent with `max_tokens=100000` and `reasoning.max_tokens=32000` (relay
  parameters only; no content change). From then on the author delivered profile.json and generate.py in parts.
- Relay mechanics added during the run (no content effect): "next part" continuation messages; trimming of
  superseded file versions from the context sent to the author (the latest delivered version always verbatim);
  broad transport retries after one broken chunked read (OpenRouter `IncompleteRead`); a 600 s faulthandler dump
  in the sandbox so a hang reports its stack.
- Validator changes during the run (protocol only): failure messages for TFN/Medicare/ABN now show the residue the
  validator computes; the profile check also rejects the ATO's own ABN (a real organisation's number) as "not
  invented". Mutation test re-run after each change: 26/26.

## Outcome: did NOT converge in 15 rounds (stopped as the brief requires)

| Round | generate.py (chars) | guard | `generate.py` | determinism / self-check | validator |
|---|---|---|---|---|---|
| 1 | 168,168 | clean | hung (faulthandler dump at 600 s in a random-retry loop) | - | profile only: ABN fails mod-89 |
| 2 | 157,120 | clean | own checks fail (558 cases, duplicate texts, label short) | - | profile ABN fails |
| 3 | 160,858 | clean | own checks fail (label quotas) | - | profile ABN fails |
| 4 | (6 parts) | - | did not compile (SyntaxError), not installed | - | - |
| 5 | 89,635 | clean | KeyError in a case builder | - | profile passes from here |
| 6 | 89,668 | clean | own checks fail (label quotas, duplicate texts) | - | - |
| 7 | 92,103 | clean | own checks fail (label quotas) | - | - |
| 8 | 92,327 | clean | own checks fail, counts identical to round 7 | - | - |
| 9 | 96,930 | clean | 198 cases | identical / pass | 66 failures (SSN block, NINO format, non-test cards, profiled-org hard negatives, manifest sha) + quota shortfalls |
| 10 | 98,231 | clean | NameError | - | - |
| 11 | 97,015 | clean | own checks fail (profiled-org hard negatives) | - | - |
| 12 | 97,089 | clean | 196 cases | identical / pass | 1 rule failure (MANIFEST sha256 != file: sha of the in-memory text, file written with CRLF) + quota shortfalls |
| 13 | 99,235 | clean | own checks fail (regression: label quotas, profiled-org hard negatives) | - | - |
| 14 | 98,154 | clean | own checks fail (label quotas) | - | - |
| 15 | 97,883 | clean | own checks fail (5 profiled-org hard negatives) | - | - |

Closest state: round 12 (`orchestrator/versions/generate.py.v11`, 97,089 chars). Its quota shortfalls per the
validator: indirect share 44% (target 60%); negative statements missing for family_violence and sexuality; 4
tables with QUASI_ID (target 20); 10 hard-to-spot cases marked (target 15); clean docs missing public_news,
policy_statement and public_date look-alikes; 2 scripts/languages (target 5); profiled-organisation cases 31%
(target 40-60%); other workplaces not identified (organisation field null outside the profiled org).

Where it stalled: the author's generator either stopped in its own build checks (label quotas, or the brief's
"hard negatives in profiled cases come from the profile" rule, which the author adopted after the validator
reported it in round 9) or, when it did build, fell short on the brief's structural quotas. Revisions oscillated
between versions that built and versions that regressed on label counts.

Not done, because step 2 did not converge: no labeller run, no agreement (AGREEMENT.md says so), no review round,
no PROVENANCE.md request, nothing written to `split-d\sealed\`. Files left in half-q: profile.json and
generate.py (round 15, the author's latest; it does not build), every earlier version in `orchestrator/versions/`,
the whole conversation (`orchestrator/conversation.json`), raw replies (`orchestrator/responses/`), every
feedback message sent (`orchestrator/feedback_roundNN.md`) and build record.

## Guard trips and sandbox

- Static guard: 0 trips in 15 rounds (every installed generate.py was clean).
- Runtime sandbox (audit hook): 0 violations. One hang (round 1) was ended by the faulthandler timeout.
- One delivery rejected before installation for not compiling (round 4).

## Calls, providers, tokens, cost (from calls.jsonl)

- 99 logged calls, all with `zdr_requested: true` (forced by the client): 97 author calls, 2 pings.
  One further author call failed in transport (`IncompleteRead`) and was retried; the client logs successes only.
- Providers seen: author `SiliconFlow` (all 97 author calls); pings `Venice` (Qwen) and `InferenceNet` (DeepSeek).
- Tokens: prompt 6,101,397; completion 705,538 (of which reasoning 202,466).
- Cost reported by OpenRouter: USD 7.64 (author USD 7.64; pings < USD 0.001). Labeller: not run.

## Orchestrator's own feedback content

Feedback messages contained only: the guard result, the generator's exit codes and stdout/stderr tails, the
orchestrator's determinism re-run result, the self-check output, the validator's output (rule, case id, offsets,
rule text, computed checksum residues, quota table) and a fixed paragraph describing how the validator reads
genres, long documents, organisation and marking fields. Three factual observations were appended: round 8
(label counts identical to round 7), round 13 (round 12 had built; round 13 regressed; rounds remaining) and
round 14 (next build is the last). No content, examples, phrasings or names were suggested.

## Blindness note (one slip, disclosed)

At the end of the run one `ls` of `<split-d>\` (the parent of the allowed folders) was run,
which displayed the folder names `half-c`, `half-q`, `inputs`, `sealed`, `validator`. Nothing inside `half-c` or
any other folder was opened, listed or searched.

## Phase 2: method change after round 15 (tool-calling author)

Decided by the coordinator after the round-15 stall report: the loop of "author delivers whole files as text, the
orchestrator runs them" was a design flaw, not comparable to half C, whose author could run its own code. From
here the author works through OpenRouter function calling, so both halves are authored under comparable
conditions.

- Pre-check: a two-turn `add` tool test with `qwen/qwen3.8-2.4t-a95b` under forced ZDR (`require_parameters: true`
  added so only tool-capable ZDR providers are routed) returned a proper tool call and a final answer (provider
  Venice).
- Starting files: the author's own round-12 generate.py (`orchestrator/versions/generate.py.v11`, sha256
  82dbb7f9427964b9...) and its profile.json (unchanged since round 5) restored verbatim; the round-15 generate.py
  is archived as `orchestrator/versions/generate.py.v14`. The author was told in plain words that these are its
  own round-12 files and that it now has tools.
- Tools (executed by the orchestrator in `orchestrator/toolloop.py`, every call logged to `tools.jsonl`):
  `read_file` (half-q top-level files and the four inputs; orchestrator files refused), `replace_in_file`
  (exact unique match), `write_file` (generate.py, profile.json, PROVENANCE.md, .md/.txt notes; cases.jsonl and
  manifest.json only via generate.py), `run_build` (static guard, sandboxed `python -I generate.py`, a second run
  for byte-identity, `--self-check`; first ~4,000 characters), `run_validator` (check_cases.py --profile --quotas
  --half Q; failures, warnings, quota table). No shell, no network, no other paths.
- Context bound: older tool outputs and large old tool-call arguments are replaced by short notes; the latest
  run_build and run_validator results and the last 8 tool results stay verbatim.
- Budget: 250 tool calls or USD 30. Done = build + self-check pass, validator exit 0, quotas met; checked by the
  orchestrator whenever the author replies without a tool call (results returned verbatim if not done).

### Phase 2 results

- Interruption: after tool call 22 the loop was stopped once and resumed to widen the verbatim window (the author
  was re-reading dropped file ranges): tool outputs are now kept verbatim newest-first up to ~200,000 characters
  (at least the last 8), and the loop repairs any unanswered tool calls on resume.
- Build phase: DONE after 119 tool calls, USD 4.60 (1 text reply, confirmed by the orchestrator's own re-run):
  201 cases, build + self-check pass, byte-identical second run, validator exit 0, all quotas met.
- Blind labeller (DeepSeek) on all 201 cases; 378 disagreement items written mechanically to `review_items.md`
  (taxonomy rows quoted once at the top); one review round by tools (message: `orchestrator/msg_review.md`).
  The author recorded a decision for every item in `review_decisions.md` (ranges marked fixed cover 359 item
  numbers, kept 23) and fixed its generator. Review phase DONE at tool call 188 (cumulative USD 12.09).
- Rebuild after review: 199 cases (139 texts changed, 2 cases dropped); build, self-check, validator exit 0, quotas
  met; changed texts re-labelled blind. AGREEMENT.md has the before/after table and every kept item with reason.
- PROVENANCE.md: written by the author (requested in `orchestrator/msg_prov.md`). Its first version named the
  wrong model and date; the orchestrator relayed the facts (model id `qwen/qwen3.8-2.4t-a95b`, date 27 October
  2026, `orchestrator/msg_prov2.md`) and the author corrected them. generate.py and profile.json unchanged by both.
- Sealed: `split-d/sealed/half-q.jsonl`, sha256
  `3fb5dc5ec227056cbe3f458356d36bf2089490f9b344cad6c4516bafe8d3a07a` (in `sealed/half-q.sha256`). The phase-1
  "planned writes only" line above is superseded by this.
- Tool calls used in phase 2: 190 of 250 (read_file 71, replace_in_file 80, write_file 4, run_build 26,
  run_validator 9; 5 returned errors, e.g. a non-unique `old`). Author cost in phase 2: USD 12.24.
- Providers seen in phase 2: author Novita (178 calls) and Together (15); function-calling test Venice; labeller
  InferenceNet (546) and OpenInference (1). All 841 logged calls carry `zdr_requested: true`.

### Half C (step 5, after half Q was sealed)

- Read only `half-c/cases.jsonl`, `half-c/profile.json` and `half-c/manifest.json` (the validator's manifest
  check), plus one directory listing of `half-c/`. Nothing in half C was changed; its author was not contacted.
  Written into half C: `AGREEMENT.md` and `VALIDATOR.md` only (as instructed). Labeller working copy and raw
  outputs: `half-q/xc/`.
- Validator: 0 rule failures, 0 warnings; one quota row short by a heuristic (profiled-organisation share 29%
  estimated from name mentions; half C has no organisation field); indirect/negative/hard-to-spot not marked (n/a).
- Labeller: 205 cases, 1 unmappable value, 60 disagreement items.

### Totals (whole run, from calls.jsonl)

- 841 calls; cost USD 20.40 (phase-1 author 7.64, phase-2 author 12.24, labeller 0.52 for both halves, tests and
  pings < 0.01).
