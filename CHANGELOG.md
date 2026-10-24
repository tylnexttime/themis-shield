# Changelog

All notable changes to themis-shield. Dates are day-first (DD/MM/YYYY).

## 0.2.0 (25/10/2026)

- Tested once on a fresh held-out split written by an independent author (another Claude model): 0 released
  leaks in 3,686 gold spans, quasi-identifier R_any 0.976 (v0.1 policy: 0.738), clean friction 0.200 (v0.1
  policy: 0.358). Fails its own pass rule on friction (limit 0.15); see eval/RESULTS-v0.2.md.

- Policy: when both LLM finders return nothing, only PII-Tracer findings that
  survive the allow-list count as contrary evidence, by label; a date the
  tracer flags that cannot be a date of birth no longer holds a document.
  `[policy] quasi_id_ask` makes a found quasi-identifier hold the document
  for ASK.
- Detection: a column-aware pass marks rare categorical values (role, title,
  team, site, attribute) as quasi-identifiers in tables with identifier-like
  columns or an "anonymised" label; tracer dates with a time of day, bare
  weekdays and the organisation's public dates become OTHER_DATE; trailing
  punctuation no longer defeats the role-mailbox allow-list; a USI must
  contain a digit; the organisation's own ABN is allow-listed even when
  checksum-valid.
- `[detection] version` and `[policy] version` (`v0.2` default; `v0.1`
  restores the evaluated v0.1 behaviour as a negative control).
- Evaluation runner: `--cases`, `--capture-finders` / `--replay-finders`
  (refuses a changed prompt or model), `--gate-version`; timings moved to
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
