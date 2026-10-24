# VS Code client policy example

The administrator controls client deployment, licences, extension provenance, pinned versions,
managed settings and effective enforcement. These examples are not installed
controls.

## AllowedExtensions

Validate identifiers and policy encoding against the installed release:

```json
{
  "kilocode.kilo-code": true,
  "anthropic.claude-code": true,
  "*": false
}
```

AllowedExtensions does not verify VSIX origin. The administrator must control installation
sources and updates separately.

## Kilo Code

- Lock its custom provider to the local API gate using administrator-managed settings.
- The gate holds the cloud API key; never use Claude.ai tokens.
- Disable the Kilo gateway, unsupported Claude Code provider and autocomplete.
- Set `KILO_DISABLE_SESSION_INGEST=true`.
- Set VS Code `TelemetryLevel=off`.
- Do not claim a Windows sandbox; Kilo has none.

`examples/kilo.example.json` is policy intent, not a verified extension schema.

## Claude Code: managed-hooks mode

Distribute `examples/claude-code-managed-settings.example.json` through the
managed-settings location.

- Pin Claude Code >= 2.1.295 to an approved regression-tested build.
- Set `allowManagedHooksOnly: true`.
- Use command hooks with `onFailure: block` for every supported gate event.
- SessionStart and Stop cannot block; they are logging/context only.
- Turn telemetry off before tool execution; PostToolUse runs too late to
  protect original output captured by telemetry.
- Use the user's direct Claude Code login. No custom subscription endpoint,
  credential forwarding, credential-store reads or transcript reads by the gate.
- Pin the Python executable, package and import path to administrator-controlled artefacts.

The current hooks are incomplete and must not be deployed for real cloud work.
MessageDisplay failure displays original canonical content, which must already
contain placeholders. A PostToolUse block withholding output is unverified;
PreToolUse marking preflight is mandatory before permitting file reads.

This no-credential-intermediation reading is the project's own, not Anthropic's ruling.
Confirmation ask: “We use managed hooks for local de-identification, no proxy;
please confirm.”

## Smallest effective-control checks

1. Force UserPromptSubmit's command to exit 1. The prompt must not reach Claude.
2. Try installing a user/project/plugin bypass hook. It must not run.
3. Capture connections during Kilo autocomplete/provider switching; any
   unapproved model or gateway connection is a policy failure.
4. Capture telemetry during a synthetic tool call. Original tool output must
   not enter telemetry.
