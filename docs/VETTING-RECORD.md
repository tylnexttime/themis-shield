# Vetting records

When a data release (a folder of documents) is de-identified for use with a cloud model, someone has to be able to
show later what was done to it, how well the method is known to work, and who accepted the risk that remains.
`themis vet` produces that evidence: one **vetting record** per release, in JSON for machines and Markdown for
people, written by the same pipeline that guards a chat request.

The record is evidence for a decision, never the decision. themis-shield fills in what a machine can know and leaves
the judgement, and every sign-off, to people.

## Run it

```powershell
themis vet .\release-folder --out .\vetted\R-2026-11-01 --codebook .\codebooks\R-2026-11-01.db `
  --asset DA-0042 --object synthetic-roster --release R-2026-11-01 `
  --purpose "Summarise roster coverage by month" --route cloud-deidentified `
  --reference UC-0007 --config config\local.toml
```

- **Exit 0:** the record is ready for review. **Exit 3:** some documents are held for ASK; resolve them (below) and
  run again into a new folder. **Exit 2:** refused (bad arguments, missing models, a failing detector canary, an
  existing codebook file, or a record that would have contained a protected value).
- `--asset`, `--object`, `--release` and `--reference` take your own register's identifiers; themis-shield checks only
  their shape. Use `--reference` for each related record (a use case, a change request).
- Like everything else here, it runs only with `[gate] synthetic_only = true`.

## What it writes

```
<out>/release/doc-0001.csv ...        de-identified documents (RELEASE, or ASK resolved "release"); names never travel
<out>/vetting-record.json             the record (schema "themis-shield/vetting-record", version 1)
<out>/vetting-record.md               the same record for people
<out>/local-only/document-index.json  document positions -> original file names; keep it with the codebook
<codebook>                            the placeholder map, an encrypted vault file for this release only
```

Every document goes through the chat path's own steps, in the same order:
1. the marking check: a local-only marking keeps the document out of the derived release;
2. tiered detection;
3. the policy decision;
4. the reviewed false-positive catalogue, if enabled;
5. substitution;
6. the known-miss final sweep, if enabled.

Then every derived document is checked against every value protected anywhere in the release. A name found in one
document and missed in another holds the second one (`BLOCK:original-in-output`), and the record counts it as a
detection miss for a person to look at.

The codebook is a separate vault file, not the chat vault, because the chat vault forgets an idle conversation after
four hours. The file is protected by the deployment's key protector. Where it lives, who may restore from it, and
when it is destroyed are the custodian's decisions, recorded in part 5.

## The seven parts

| # | Part | Who fills it | What themis-shield writes |
|---|---|---|---|
| 1 | Identity | generated | asset, object, release id, the release's content sha256 (over its documents' hashes, never their names), document count, references, purpose, requested route |
| 2 | Classification and personal information | a person | the markings the gate read, by class, and how many documents it kept local; the marking decision, the personal-information determination and the ceiling check are left to the person |
| 3 | De-identification run | generated, checked by a person | version, configuration sha256, detection and policy versions, which optional tiers were on, RELEASE / ASK / BLOCK counts, findings by type (counts only), each ASK and how it was resolved, the derived release's hash, and one row per document (position, hash, marking class, decision, reasons) |
| 4 | Does it work? | a person, peer-checked | the **method baseline**: the published result of the latest frozen evaluation for this version, including its verdict. For 0.3.0 that is split D: **FAIL**, 28 released spans out of 5,841. A version not yet evaluated says which result it inherits. Everything about *this* release (spot check, quasi-identifiers, small groups, linkage, residual risk and the criterion it was judged against) is left to people |
| 5 | Codebook custody | the custodian | a store handle, the number of entries, an integrity hash over the placeholders (never the values), and the key protector; restoration roles, key rotation, retention and disposal are left to the custodian |
| 6 | Inspection checklist | generated, then a person | the automated checks (every file read as text, no protected value in any derived document, resolutions matched to the right content), then the person's own checks and what was not checked |
| 7 | Sign-offs | people | two empty rows: data custodian, and information security. Your organisation decides who signs, on what criteria |

## Rules the record keeps

- **Counts, never values.** No document text, finding value, file name or codebook entry is written into it. Before
  anything is saved, the serialised record is searched for every value the run protected, and a match refuses the
  whole run (this is how a purpose such as "check what <a name> wrote" is caught).
- **One record per release.** The release is identified by its content, so changed content is a new release and needs
  a new record and a new codebook.
- **Nothing says "approved".** A record with unresolved ASKs is `incomplete`; otherwise it is `ready-for-review`.

## Resolving an ASK

A person reviews each held document and writes a resolutions file. Each entry names the document by its position
*and* its content hash, so a resolution can never apply to different content:

```json
{"documents": {"6": {"sha256": "<the document's sha256 from the record>", "resolution": "release",
                     "by_role": "data custodian", "note": "names are fictional characters"}}}
```

`resolution` is `release` (the document is de-identified and included) or `exclude`. Run `themis vet` again with
`--resolutions` into a new output folder and a new codebook file. The note is checked like the rest of the record, so
it must not contain the values it is about.

## Using it from your own tools

`themis_shield.vetting` exposes the pieces: `vet_release(router, documents, release, out_dir, ...)` runs a release
through an existing router; `build_record(...)` and `render_markdown(...)` turn per-document outcomes from any
pipeline into the same record; `method_baseline(version)` returns the published baseline. The JSON keys are stable
within schema version 1.
