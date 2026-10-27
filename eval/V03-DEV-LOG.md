# v0.3 development log

What changed between v0.2.1 and the v0.3 kit, and what each change did on development data. Written on 27/10/2026,
before the v0.3 test split (split D) was opened; the frozen plan is [`FROZEN-PLAN-v0.3.md`](FROZEN-PLAN-v0.3.md).

Every number here comes from **replaying captured finder answers**, so two runs differ only in the code after the
finders, and every change was measured against a ratchet: released leaks never above the baseline, and recall
(overall, sensitive statements, quasi-identifiers) never below it by more than 0.001 / 0.005 / 0.005. A change that
broke the ratchet was not adopted.

## Development data

- **The v0.1 set** (521 cases, 60 clean) and **split C** (371 cases, 95 clean): both opened, so both are development
  data now. Split C's numbers are **in-sample**: the v0.3 fixes were written after reading its failures.
- **A private real-document slice** (78 documents, 17 with no personal information, 468 gold personal spans): one of
  the authors' own personal documents (statements, contracts, invoices, letters, identity papers, emails, some read by
  OCR), used with his consent, processed only on the local machine, and never published. Two labellers marked them
  independently (same label on 392 of 393 shared spans) and disagreements were settled by written rule. After the
  first run it became development data too, and it is reported only in aggregate.

## The changes

| Lane | Change | Default | What it did |
|---|---|---|---|
| 0.2.1 | Unicode hardening (markings, injection signal, rule packs) | on | security fix; no published case changed |
| 1 | seven deterministic friction fixes (secret placeholders, organisation contacts, checksum context, card and ACN rules, table role words, title tokens, contact brackets) | on | split C friction 0.200 → 0.147 |
| 1 | review fixes (placeholders with default values are secrets; a given name that is also a title word is kept; relabelled numbers keep their checksum result) | on | closed a bypass found by an independent review; no decision changed |
| 1 | real-document fixes (secret false positives in URLs, prose, authorisation codes, document ids, organisation codes, machine-readable zones; whitespace-tolerant finder matching; address line numbers) | on | real slice: secret holds 17 → 4, friction 0.235 → 0.176, full coverage 0.962 → 0.972 |
| 2 | known-miss register with a final sweep, reviewed false-positive catalogue, cloud trip-wire, placeholder allocator safety | off (allocator on) | nothing changes while off (tested) |
| 4 | typed-decision tier (prefill-only questions to the resident 26B) | off; arm 2 turns it on | families Q-M, Q-N, Q-P kept; Q-S rejected (raised released leaks: the 26B confidently called some real people fiction), Q-I rejected (raised friction) |
| 5 | detector canary (start-up, reload, periodic; failing canary blocks cloud work) and per-stage timings | on | decisions byte-identical; passes live on the real models |

## Results on development data

| Set | Configuration | Released leaks | R_any | Sensitive R_any | Quasi-ID R_any | Clean friction | Over-redaction |
|---|---|---|---|---|---|---|---|
| v0.1 set (521) | v0.2.1 | 0 | 0.9989 | 0.9970 | 0.9891 | 0.050 (3/60) | 306 |
| v0.1 set (521) | v0.3 kit | 0 | 0.9989 | 0.9970 | 0.9891 | 0.050 (3/60) | 306 |
| v0.1 set (521) | v0.3 + typed | 0 | 0.9989 | 0.9970 | 0.9891 | 0.050 (3/60) | 306 |
| split C (371, in-sample) | v0.2.1 | 0 | 0.9902 | 0.9537 | 0.9762 | 0.200 (19/95) | 605 |
| split C (371, in-sample) | v0.3 kit | 0 | 0.9902 | 0.9537 | 0.9762 | 0.147 (14/95) | 600 |
| split C (371, in-sample) | v0.3 + typed | 0 | 0.9902 | 0.9537 | 0.9762 | **0.116 (11/95)** | 600 |
| real slice (78) | v0.2.1 | 11 | 0.974 | n/a | n/a | 0.235 (4/17) | 722 of 1,771 |
| real slice (78) | v0.3 kit | 11 | 0.974 | n/a | n/a | 0.176 (3/17) | 723 of 1,771 |

## What the real documents taught us

- **Fixes tuned on synthetic data barely moved real friction.** On the real slice, v0.2.1 and the synthetic-tuned
  fixes were almost identical; the real causes were different (high-entropy URL pieces and prose read as secrets,
  finder values split across line breaks in the source).
- **Over-redaction is the real cost.** The gate hid nearly every business phone number, reference number, invoice
  number and transaction date, and labelled 286 dates as dates of birth where there were three. A cloud model could
  not summarise expenses from that output. This is the motivation for v0.4's typed placeholders, task profiles and
  utility measurement ([docs/ROADMAP.md](../docs/ROADMAP.md)).
- **Released leaks on the real slice** (11 spans, none of them an identifier, account, card, passport or secret):
  dates inside recording file names, a clearance statement no finder treats as criminal history, a unit number on
  its own, and a fragment of an OCR-garbled email address.
- **Long tables can overflow the finder's answer.** A spreadsheet full of names and dates exceeded the 26B's 8,192-token
  output; the stage failed and the gate BLOCKed the document (fail-closed). The fix, finders listing each distinct
  value once, changes the finder prompt and waits for v0.4.
- **Memory is the binding constraint on an 8 GB laptop.** Parallel evaluation processes, and PII-Tracer reloading
  while the 26B is resident, made model loads fail closed; runs now hold a lock and run one at a time.
