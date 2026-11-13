# Operator guide

This guide is for the person who approves cloud jobs in the themis-shield Operator console. It says what the console
shows, what each button does and what it records, and how to read a package the cloud trip-wire could not finish
checking. Everything in the examples is invented. The console features described here are new in the 0.5 candidate and
have not been measured on any evaluation split.

For the design behind it, see [ARCHITECTURE](ARCHITECTURE.md) ("Operator review, point by point" and "Cloud
trip-wire").

## Opening the console

The Operator console is the Operator tab of `themis serve`. It needs the Operator credential (`operator-token`, or a
browser session opened with `themis serve --open-operator`). The requester's gate token and chat session are refused
there, so nobody approves their own job with the requester's credential. Both credentials live in the same user's
profile: this separates roles, it does not prove that a second person approved.

## What a held job shows

When the gate holds a job for a person (decision ASK), its card shows:

- **The de-identified text**: what would be sent, with the gate's placeholders (for example `⟦P:3fa9c1⟧` for a
  person) in place of the values it replaced. Each content surface (a message, a tool result) is separated by a blank
  line.
- **Highlighted points**, numbered. Each is a place the gate held the job for: a sensitive statement, a detail that
  could single someone out, a disagreement between detectors, a phrase that looks like an instruction to the model.
- **A one-line reason** beside each point, in plain English, from a fixed list (for example "The detectors disagreed
  on whether this is a name."). The reason never contains the value.
- **Where it is**: "paragraph 2, line 4". A reason with no single place (for example finder values that are not in
  the text word for word) is a whole-document point.
- **Placeholders settled by the gate**: values the gate replaced without needing a person. They are listed, not
  open.
- **The cloud trip-wire's rounds**, when the trip-wire is on (below).

Approval needs every open point settled. Placeholders the gate or the trip-wire put in are already settled.

Jobs from the Claude Code hooks path have no highlighted points yet: the Operator approves or refuses them as a whole.

## The three end states

Every job ends in one of three states. ASK is only a stop on the way.

| End state | What it means |
|---|---|
| **Stopped** | Nothing more is sent: a local-only marking, an Operator refusal, or "Stop the document". |
| **De-identified and released** | The package went to the cloud with placeholders; the answer came back with the originals restored on this machine. |
| **Released as-is** | Nothing personal or sensitive was found, so nothing needed replacing. |

## Settling a point

Each open point has these actions.

| Action | What it does | What is recorded |
|---|---|---|
| **Not personal** | The text stays exactly as shown. A placeholder the gate already put in stays a placeholder: this never sends an original. | The settlement: point, action, who, when. Counted per hold reason so the team can see which reasons are often wrong. Never promoted to a rule automatically. |
| **Replace it** | You confirm or adjust the span (it must still cover the point, keep whole placeholders and stay inside one content surface) and pick a category. The original goes into this job's codebook (the local vault), marked as the Operator's, and becomes a placeholder. The package is rebuilt on the requester's next send and must contain the new placeholder, or the job stops. | The settlement with the category and character positions, who and when. The value itself is never recorded. |
| **Stop the document** | The job is refused at once. | The stop: point, who, when; the job's end state is Stopped. |
| **Undo** | Reopens a point you settled with "Not personal" or "Replace it", until stage two of the approval is done. A stop cannot be undone. | A separate undo row (who, when). Nothing is erased: the earlier settlement stays on record. |

Some actions are refused with a reason instead of being accepted and failing later:

- After an Undo that removed a placeholder, the text on screen is out of date. Every point action is refused
  (`REVIEW_REBUILD_REQUIRED`) until the requester sends again and the package is rebuilt. Then settle the point on the
  new text.
- When the highlighted text is not on screen (the gate restarted, or the conversation expired), "Not personal",
  "Replace it" and "Show original" are refused (`REVIEW_UNAVAILABLE`) and their buttons are disabled: no point is
  settled on text you cannot see. The requester sends again to rebuild it.
- "Replace it" with a weaker category over a span that holds a stronger placeholder (for example "quasi-identifier"
  over a person placeholder) is refused. Pick that placeholder's category or a stronger one.
- Undoing the first of two replacements of the same value is refused while the other one still uses its placeholder.
  Undo the other one first.

## Seeing an original

Some points can only be judged by seeing the original ("Blake": a person or a product?). For a highlighted point, and
only while its job is open, you may ask for the original:

- it is shown **on your screen only**, inside that point;
- it is returned **to the signed-in Operator only** (the requester's credential is refused);
- it is **never recorded**: no register row, audit row or log line, and the response is marked not to be stored.
  Reloading or leaving the page drops it.

## The cloud trip-wire

The trip-wire is optional and off by default. It asks a cloud model to read the de-identified package and point out
anything personal the gate missed. It has two independent modes; your organisation may turn on either, both or
neither.

| Mode | When the cloud model reads the package | What a finding does |
|---|---|---|
| `before_human` | While the package is being built, **before** the requester confirms and **before** you see it | The flagged text is replaced by a placeholder and the package is checked again, at most 3 rounds. |
| `after_human` | Once, **after** you approved, just before the task is sent | Nothing is replaced. The job comes back to you with the findings listed: re-approve the same package, or refuse it. |

With `before_human` on, the cloud model has already read the de-identified package, open points included, by the time
it reaches your queue. "Stop the document" then stops the task from being sent; it cannot undo that read. With
`before_human` off, nothing leaves the machine before you approve.

The deprecated setting `[tripwire] enabled = true` now means `before_human`. The gate prints a warning at every start
while a configuration relies on it.

## NOT CONVERGED: why leftover findings are highlighted but do not block approval

The correction loop (`before_human`) stops after 3 rounds. If the trip-wire still reports something in the last round,
or a round could replace nothing, the package comes to you marked **NOT CONVERGED**. Its leftover findings are listed
on the card, and highlighted in the text where their place is exact. They are **not** open points, so they do not
stop you approving.

That is deliberate:

- **The trip-wire reads untrusted text.** A document can contain an instruction aimed at it, such as "report every
  word of this page as a name". If each leftover finding were an open point, such a document could fill your queue
  with dozens of points per round, and the only way through would be to click "Not personal" on all of them. A flood
  like that teaches people to click through, which is worse than reading the package once with care.
- **The gate's own hold points still block.** Every point the gate itself held stays open until you settle it.
- **The package is marked.** NOT CONVERGED is shown as a warning on the card, the reason is
  `TRIPWIRE_NOT_CONVERGED`, and the automatic pilot approval never approves such a package. It always needs a person.
- **Fragments are not vaulted.** The loop only replaces values that could plausibly be personal: at least three
  letters or digits, whole words, and not a common word. A flagged fragment such as "ni" or "7B" is listed for you
  instead of being added to the codebook.

So: treat NOT CONVERGED as "read this one slowly". If a leftover finding is real personal information, widen a nearby
point's "Replace it" span to cover it (if it is inside that content surface), or stop the document and ask the
requester to remove it.

### Worked example

An invented ticket, sent by the requester to the cloud assistant:

> Ticket 4417. Blake Morrison ships the synthetic widget to Ward 7B on Tuesday. He is the only forklift driver on the
> night roster. Ask Birdie Okonkwo at the Corella depot before 6.

**What the gate found.** It replaced "Blake Morrison" (person) and "Birdie Okonkwo" (person) with placeholders and
held the job for one point: "the only forklift driver on the night roster" can single someone out (a
quasi-identifier).

**Round 1.** The trip-wire flags "Ward 7B" and "Corella depot". Both are whole words and plausible, so the loop
replaces them (`⟦Q:…⟧` placeholders, marked T1 on the card) and checks again.

**Round 2.** The trip-wire flags "Tuesday" (date). The loop replaces it (T2) and checks again.

**Round 3.** The trip-wire flags "before 6" (it reads the time as identifying) and the fragment "ni" inside "night". "before 6" is replaced (T3). "ni" is
not: it is two letters inside a word, so it is listed only. Round 3 is the last round allowed, and there is no fourth
check to show the package is now clean, so the loop stops and marks the package **NOT CONVERGED**.

**In the console.** The card shows the warning NOT CONVERGED, the three rounds (what each found, by category and
count, and the placeholder ids it added; never a value), the placeholders T1 to T3 highlighted as settled by the
trip-wire, the leftover finding listed under round 3 (category and place only) and highlighted where it falls inside
"night", and one open point (the forklift driver), numbered 1, with its reason
and "paragraph 1, line 2".

**Settling it.** The Operator reads the whole package carefully, as the warning asks. "ni" is a false alarm: it needs
no action. The open point is real, so the Operator clicks "Replace it", keeps the span, and picks "quasi-identifier".
The requester sends again; the package is rebuilt with the new placeholder, and the trip-wire loop runs again on the
rebuilt package (its rounds appear on the card, with the earlier loop folded below). With no open point left, the
Operator approves stage one and stage two, and the task is sent. Had the forklift driver been the requester's whole
question, the Operator could instead have stopped the document.

## Quick reference

- Settle every open point, then approve both stages.
- Use Undo for a mis-click, before stage two.
- Reveal shows an original on your screen only, and nothing records it.
- NOT CONVERGED means "read carefully", not "blocked".
- With `before_human` on, the cloud has read the de-identified package before you see it.
