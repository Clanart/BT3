# Q1515: withTrampolineEnv: credential/token/passphrase sent to an unauthorized host

## Question
Can the askpass/credential-helper flow via `withTrampolineEnv` in [app/src/lib/trampoline/trampoline-environment.ts] be driven by attacker-supplied prompt or URL text to return a token/passphrase for the wrong origin?

## Target
- File/function: [app/src/lib/trampoline/trampoline-environment.ts] — `withTrampolineEnv`
- Entrypoint: A crafted remote URL, redirect, proxy/PAC response, or askpass/credential-helper prompt
- Attacker controls: remote host/URL, HTTP redirect target, proxy/PAC string, certificate presented by the server
- Exploit idea: Can the askpass/credential-helper flow via `withTrampolineEnv` in [app/src/lib/trampoline/trampoline-environment.ts] be driven by attacker-supplied prompt or URL text to return a token/passphrase for the wrong origin?
- Invariant to test: a stored credential is only ever released to the exact host/endpoint the user authorized it for
- Expected Immunefi impact: Critical - GitHub token, PAT, git credential, or SSH passphrase exfiltrated to an attacker-controlled host (target scope: "Critical. A GitHub OAuth token, PAT, generic git credential, or SSH passphrase held by Desktop is sent to, or accepted f...")
- Fast validation: Point the flow at a mismatched or attacker host in a test and assert `withTrampolineEnv` withholds the credential instead of sending it
