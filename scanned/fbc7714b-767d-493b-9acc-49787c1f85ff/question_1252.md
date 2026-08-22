# Q1252: SSHKeyPassphrase: credential/token/passphrase sent to an unauthorized host

## Question
Can a malicious proxy/PAC response or certificate-error path reaching `SSHKeyPassphrase` in [app/src/ui/ssh/ssh-key-passphrase.tsx] route Desktop's authenticated request or its credential through an attacker-controlled intermediary?

## Target
- File/function: [app/src/ui/ssh/ssh-key-passphrase.tsx] — `SSHKeyPassphrase`
- Entrypoint: A crafted remote URL, redirect, proxy/PAC response, or askpass/credential-helper prompt
- Attacker controls: remote host/URL, HTTP redirect target, proxy/PAC string, certificate presented by the server
- Exploit idea: Can a malicious proxy/PAC response or certificate-error path reaching `SSHKeyPassphrase` in [app/src/ui/ssh/ssh-key-passphrase.tsx] route Desktop's authenticated request or its credential through an attacker-controlled intermediary?
- Invariant to test: a stored credential is only ever released to the exact host/endpoint the user authorized it for
- Expected Immunefi impact: Critical - GitHub token, PAT, git credential, or SSH passphrase exfiltrated to an attacker-controlled host (target scope: "Critical. A GitHub OAuth token, PAT, generic git credential, or SSH passphrase held by Desktop is sent to, or accepted f...")
- Fast validation: Point the flow at a mismatched or attacker host in a test and assert `SSHKeyPassphrase` withholds the credential instead of sending it
