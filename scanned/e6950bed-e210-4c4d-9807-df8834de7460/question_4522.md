# Q4522: removeMostRecentSSHCredential: credential/token/passphrase sent to an unauthorized host

## Question
Can an attacker-controlled remote URL or redirect cause `removeMostRecentSSHCredential` in [app/src/lib/ssh/ssh-credential-storage.ts] to release a stored GitHub token or git credential to a host other than the one the credential was authorized for?

## Target
- File/function: [app/src/lib/ssh/ssh-credential-storage.ts] — `removeMostRecentSSHCredential`
- Entrypoint: A crafted remote URL, redirect, proxy/PAC response, or askpass/credential-helper prompt
- Attacker controls: remote host/URL, HTTP redirect target, proxy/PAC string, certificate presented by the server
- Exploit idea: Can an attacker-controlled remote URL or redirect cause `removeMostRecentSSHCredential` in [app/src/lib/ssh/ssh-credential-storage.ts] to release a stored GitHub token or git credential to a host other than the one the credential was authorized for?
- Invariant to test: a stored credential is only ever released to the exact host/endpoint the user authorized it for
- Expected Immunefi impact: Critical - GitHub token, PAT, git credential, or SSH passphrase exfiltrated to an attacker-controlled host (target scope: "Critical. A GitHub OAuth token, PAT, generic git credential, or SSH passphrase held by Desktop is sent to, or accepted f...")
- Fast validation: Point the flow at a mismatched or attacker host in a test and assert `removeMostRecentSSHCredential` withholds the credential instead of sending it
