# Changelog

All notable changes to themis-shield. Dates are day-first (DD/MM/YYYY).

## 0.2.1 (27/10/2026)

Security hardening: invisible Unicode characters and compatibility forms no
longer hide a security marking, the injection signal or a rule-pack value.

- Markings: a zero-width character, soft hyphen or similar inside a marking
  (SECRET with a zero-width joiner, CONFIDENTIAL with a zero-width space or a
  soft hyphen), or a marking in full-width letters, was not detected, so a
  local-only document could be released. The marking gate now also reads each
  surface through a shared detection view (every Unicode
  Default_Ignorable_Code_Point removed, NFKC per character) and keeps the
  stricter result: the classification never drops below the raw text's, and
  findings still carry offsets into the original text. Lines holding only
  invisible characters no longer push a banner out of the first three lines.
- Injection signal: fires on the raw text or the detection view, so one
  invisible character inside "ignore all previous instructions" no longer
  hides it.
- Rule packs: the view ignores every default-ignorable code point, not only
  U+200B, U+2060 and U+FEFF, so a ZWNJ, ZWJ, soft hyphen, variation selector
  or tag character inside a phone number or email address no longer hides it
  in whole or in part. Spans cover the whole original value, invisible
  characters included, so substitution removes them too.
- `normalise.detection_view` is the one shared helper; `rules.normalised_view`
  uses it.
- Replay of the captured v0.2 finder answers through this code reproduces the
  v0.2 final runs byte for byte on dev+test (521 cases) and split C (371
  cases): no prediction or decision changed, so every published number stands.
  The published cases contain no invisible character the v0.2 code mishandled,
  so the replay shows no regression; the new tests show the fix.
- Residual: the LLM finders and PII-Tracer still read the raw text (their
  prompts are hashed for capture and replay). Look-alike letters from other
  scripts that NFKC does not fold are not covered.

## 0.2.0 (25/10/2026)

- Tested once on a fresh held-out split written by an independent author (another Claude model): 0 released
  leaks in 3,686 gold spans, quasi-identifier R_any 0.976 (v0.1 policy: 0.738), clean friction 0.200 (v0.1
  policy: 0.358). Fails its own pass rule on friction (limit 0.15); see eval/RESULTS-v0.2.md.

- Policy: when both LLM finders return nothing, only PII-Tracer findings that
  survive the allow-list, with a confidence of at least 0.9 (a floor fitted on
  one development case), count as contrary evidence, by label; a tracer
  document sensitivity of 0.5 or more still holds the document; a date the
  tracer flags that cannot be a date of birth no longer holds a document.
  `[policy] quasi_id_ask` makes a found quasi-identifier hold the document
  for ASK.
- Detection: a column-aware pass marks rare categorical values (role, title,
  team, site, attribute) as quasi-identifiers in tables with identifier-like
  columns or an "anonymised" label; tracer dates with a time of day, bare
  weekdays and the organisation's public dates become OTHER_DATE, and a bare
  weekday or time is cancelled whichever stage found it (known cost: a
  personal date on a public date, or a weekday that was the only touch on a
  statement, goes unredacted); trailing
  punctuation no longer defeats the role-mailbox allow-list; a USI must
  contain a digit (this drops about 1 in 18 genuine USIs from the rule, in
  exchange for about 1 in 32 ordinary ten-letter words no longer matching); the organisation's own ABN is allow-listed even when
  checksum-valid.
- `[detection] version` and `[policy] version` (`v0.2` default; `v0.1`
  restores the evaluated v0.1 behaviour as a negative control).
- Evaluation runner: `--cases`, `--capture-finders` / `--replay-finders`
  (refuses a changed prompt, or a changed model file name, launch arguments
  or context; the weights' content is not hashed), `--gate-version`; timings moved to
  `timings.jsonl`. Development log in `eval/V02-DEV-LOG.md`.

## 0.1.0 (24/10/2026)

First public preview, for synthetic rehearsal only.

- Local gate in front of an OpenAI-compatible cloud endpoint: security
  markings checked first, then rule packs (`core`, `au`, `intl`), PII-Tracer
  and two local LLM finders; opaque placeholder tokens (`⟦P:3fa9c1⟧`) in the
  outgoing request, restored locally in the answer.
- Encrypted per-user vault (AES-256-GCM; DPAPI on Windows, passphrase
  elsewhere), a SQLite job register with second-party approval, a web app
  with an Operator console, and handover packages.
- Claude Code managed hooks (six events), closed by default and opened
  through the `[hooks]` config section.
- Local model runtime: one resident model at a time, owned llama-server
  processes, optional Strata attach and Marian launch planning.
- `themis serve` explains each startup refusal with a reason code;
  `themis --version`; `config/rehearsal.example.toml` for a working
  synthetic rehearsal.
- Evaluation kit and results, measured at tag `eval-kit-v0.1-frozen`
  (`eval/RESULTS.md`).
