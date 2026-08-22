# Q5805: deleteMostRecentSSHCredential: credential/token/passphrase sent to an unauthorized host

## Question
Does `deleteMostRecentSSHCredential` in [app/src/lib/ssh/ssh-credential-storage.ts] match the requesting host against stored credentials loosely enough (substring, missing port/scheme check) that an attacker host is treated as the authorized endpoint and handed the secret?

## Target
- File/function: [app/src/lib/ssh/ssh-credential-storage.ts] — `deleteMostRecentSSHCredential`
- Entrypoint: A crafted remote URL, redirect, proxy/PAC response, or askpass/credential-helper prompt
- Attacker controls: remote host/URL, HTTP redirect target, proxy/PAC string, certificate presented by the server
- Exploit idea: Does `deleteMostRecentSSHCredential` in [app/src/lib/ssh/ssh-credential-storage.ts] match the requesting host against stored credentials loosely enough (substring, missing port/scheme check) that an attacker host is treated as the authorized endpoint and handed the secret?
- Invariant to test: a stored credential is only ever released to the exact host/endpoint the user authorized it for
- Expected Immunefi impact: Critical - GitHub token, PAT, git credential, or SSH passphrase exfiltrated to an attacker-controlled host (target scope: "Critical. A GitHub OAuth token, PAT, generic git credential, or SSH passphrase held by Desktop is sent to, or accepted f...")
- Fast validation: Point the flow at a mismatched or attacker host in a test and assert `deleteMostRecentSSHCredential` withholds the credential instead of sending it
