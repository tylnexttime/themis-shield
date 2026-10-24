# Security policy

themis-shield is a **preview** (0.2.0). It is meant for rehearsals on
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
  (`RESTRICTED` and similar staying local), the hook client's non-zero exits,
  the outgoing-request check;
- reading the vault without the user's key, or another local user's vault;
- the loopback server accepting requests from another host, origin or
  without the gate token;
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
