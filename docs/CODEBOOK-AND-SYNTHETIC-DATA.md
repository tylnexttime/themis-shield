# A codebook, and data that is partly real

**Status: design for v0.4, not built yet.** This page explains two features planned in
[ROADMAP.md](ROADMAP.md#v04-design-keep-the-work-possible): the codebook, which will be on by default, and partially
synthetic data, which will be optional. The ideas are much older than language models, and that is why we trust them.

## Start with a person, not a model

Hand someone a stack of bank statements, insurance letters and a car lease with every name, number and date blacked
out, and ask them to summarise the household's spending and find better deals. They will struggle. Which black box is
the account holder? Is this box a date of birth or a policy number? Did the lease start in May or end in May?

Now hand them the same stack with two additions:

1. **A sheet that says what each box is.** "Box 3fa9c1 is a bank account number. Box 91b2e0 is a date of birth.
   Box 0c44a7 is a person, the same person every time you see 0c44a7."
2. **Believable stand-ins in some of the boxes**, so the document reads like a document again. Some of those values
   are real and some are invented, and they are not told which.

The task becomes easy, and the person still never learns who the household is. Chris Tyl put it in one sentence while
we were designing this: it is much easier to do the work with a redacted document, a dictionary that explains what is
what, and ideally some synthetic data, where some of it can be real and nobody has to know which.

## The codebook: what every shared dataset already has

Statistical agencies and research data archives have released confidential data for decades, and they never release
it bare. Every confidentialised file comes with a **codebook**: what each variable is, its type, its coding, what was
masked and how. Analysts make far fewer mistakes when they know what a column means, even when they cannot see its
values.

A language model is no different. Today themis-shield sends `⟦I:3fa9c1⟧`, and the coarse letter `I` says only
"identifier". The codebook will travel with each request:

```
⟦I:3fa9c1⟧  bank account number
⟦D:91b2e0⟧  date of birth
⟦P:0c44a7⟧  a person (the account holder, marked "self" by the requester)
```

The model learns the type, never the value, and it is asked to answer in the IDs. Restoration stays exact, because it
replaces IDs and nothing else. The codebook will be on by default: it costs almost nothing in privacy and removes most
of the guessing.

## Partially synthetic data: an idea from 1993

In 1993 Roderick Little set out a model-based theory for analysing data that had been masked for confidentiality, with
synthetic records among the masking methods it covers ([Little, 1993](#sources)). Ten years later Jerome Reiter worked
out how to draw valid statistical conclusions from **partially synthetic** data: files that keep the real units but
replace some of their values with synthetic ones ([Reiter, 2003](#sources)). An analyst receives a dataset in which some
values are real and some are drawn from a model, cannot tell which, and can still get sound answers. The point of the
whole line of work is that **data can stay useful while no single value can be trusted to be real.**

themis-shield's optional semi-synthetic mode applies that idea to documents:

- names, addresses and identifiers become realistic surrogates, each carrying its ID (`Mara Quinlan ⟦P:3fa9c1⟧`);
- some classes the task needs, such as transaction dates, keep values that look exactly like the rest;
- the cloud model is not told which values are real, and is asked to keep each ID next to the value it refers to;
- restoration uses the IDs only. A surrogate that comes back without its ID is never guessed at; it is flagged for the
  person to check. Models shorten and misspell fake names, and a guess could restore the wrong person: that is why an
  earlier design rejected surrogates, and why this one anchors everything to the ID.

## Randomness, not secrecy

There is one twist that matters because themis-shield is open source. If the rule were "dates always pass through",
anyone could read the code and know that every date the cloud sees is real. In an open tool, which values are real can
only stay unknown through **per-job randomness**.

Clinical data shows how. The MIMIC-IV critical-care database shifts every date by a random number of days, one offset
per patient, into a future period ([MIMIC-IV](#sources)). A four-hour gap between two measurements is still four hours,
so each patient's timeline still makes sense, but no shifted date is the real one, and two patients' dates cannot be
compared.

The semi-synthetic mode will do the same per job: one random offset for all the job's dates (in whole weeks, so
weekdays survive), so durations, intervals and month-to-month patterns hold while the calendar dates do not; the
decoder shifts them back. Amounts can take a consistent per-job scale (ratios and trends survive), opt-in, because some
tasks need exact sums.

## What it costs

Every value that passes through, and every generalised attribute (an age band instead of a date of birth, a region
instead of an address, a car's make and model instead of its plate), tells the cloud a little more. Together they can
single someone out. So:

- the codebook is on by default; surrogates, pass-through classes, date shifting and generalised attributes are off
  by default and chosen per task;
- a risky combination of kept attributes holds the document for a human, like any other uncertain finding;
- the evaluation will measure both sides: how much leaks, and how much worse the cloud's answer is than on the
  original (see the utility measurement in [ROADMAP.md](ROADMAP.md#a-utility-measurement-next-to-the-leak-measurement)).

## Sources

- Little, R. J. A. (1993). Statistical analysis of masked data. *Journal of Official Statistics* 9(2), 407-426.
  [Record](https://www.proquest.com/openview/970596f2406469cc1d5edae5d4d0d890/1?pq-origsite=gscholar&cbl=105444).
- Reiter, J. P. (2003). Inference for partially synthetic, public use microdata sets. *Survey Methodology* 29(2),
  181-188. [Statistics Canada](https://www150.statcan.gc.ca/n1/en/catalogue/12-001-X20030026785).
- MIMIC-IV, PhysioNet: dates shifted into the future by a random per-patient offset in days; intervals within a
  patient preserved; patients not temporally comparable. [physionet.org/content/mimiciv](https://physionet.org/content/mimiciv/3.1).

Checked on 27/10/2026.
