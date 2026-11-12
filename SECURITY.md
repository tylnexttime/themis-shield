# Security policy

themis-shield is a **preview** (0.4.0). It is meant for rehearsals on
**synthetic data only**: the gate refuses to start unless
`[gate] synthetic_only = true`, and that switch is an attestation you make,
not a detector. Do not put real personal information through it.

## Reporting a bypass or a leak

Please report privately, through GitHub's private vulnerability reporting:
open <https://github.com/tylnexttime/themis-shield/security/advisories/new>
(Security tab, "Report a vulnerability"). Do not open a public issue, pull
request or discussion for a bypass until it is fixed.

Include, where you can:

- the version or commit, your OS and the configuration you used (remove any
  key or passphrase; the config file should never hold one anyway);
- the smallest **synthetic** input that shows the problem, and what left the
  device or what you expected to stay local;
- whether the problem needs local models, a particular rule pack or profile.

We aim to acknowledge a report within a week. This is a small project with no
bug bounty.

## What counts

In scope, for example:

- an original value (anything the detectors found and replaced) reaching the
  cloud request, a log, the register, a handover package or a hook response;
- a request leaving the device without the approval the policy requires, or
  after the approved clean package changed;
- a way around the fail-closed paths: startup checks, marking routing
  (`RESTRICTED` and similar staying local, for markings the gate recognises:
  see [Known limits](#known-limits)), the hook client's non-zero exits, the
  outgoing-request check;
- reading the vault without the user's key, or another local user's vault;
- the loopback server accepting requests from another host, origin or
  without the gate token;
- an Operator action (approve, refuse, escalate, the Operator queue) accepted
  with the client's credential (the gate token or the chat session), or the
  Operator token accepted as a client credential on `/v1` or the hooks API;
- a secret or credential being read from configuration files instead of the
  named environment variables.

Out of scope:

- detection misses that the published evaluation already reports
  (`eval/RESULTS.md`): missed quasi-identifiers, the known released leaks,
  look-alikes held for review. New, reproducible miss patterns are still
  welcome as ordinary issues with synthetic examples;
- someone with access to the user's account or files (the register's
  append-only triggers stop application bugs, not a local administrator);
- the skeleton modules listed in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
  which are not wired into the running gate;
- third-party models, engines and cloud services themselves.

## Known limits

Behaviour that is known and documented, so that nobody relies on the opposite. See also the README's
"What it does not do yet".

- **Markings written with look-alike letters.** Marking detection reads the
  text and its NFKC form. NFKC does not fold letters from other scripts, so a
  marking that uses one (a Cyrillic Е inside `RESTRICTED`) is not recognised:
  the document is classified as unmarked and routed as such. Only the
  personal-data decision then stands between it and the cloud.
- **Restoration is lexical outside URLs.** A placeholder that the cloud reply
  writes inside a URL (a scheme run such as `https://...`, a markdown link or
  image target, or an HTML `src`/`href`/`action` value) is left as the
  placeholder and counted as `withheld_in_url` in the `themis` report. Every
  other placeholder it issued is put back wherever the reply writes it,
  without looking at the context. A placeholder in a command, in code, in a
  tool-call argument that is not a URL, or in any other non-URL context is
  restored to the real value there. A client that executes the reply (running
  the command or the tool call) or builds a request from it can send that
  value onwards. See "Restore safety" in
  [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- **The Operator credential is separate, not a second person.** Approval
  needs the Operator token (`operator-token`) or a browser session opened with
  a one-use code minted by it; the gate token that API clients, agents and
  hooks hold is refused there. Both token files sit under the same OS user's
  profile, so a process running as that user can read both and approve. The
  separation stops a client that holds only the gate token; it does not prove
  that a second human approved.
