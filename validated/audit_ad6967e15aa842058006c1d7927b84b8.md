## Title
Attacker-controlled `WWW-Authenticate` header spoofs GitHub host classification in trampoline credential helper, forcing sign-in prompts / blocking generic credential fallback for arbitrary remotes - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The `getEndpointKind` helper used by GitHub Desktop's git-credential trampoline classifies an unknown remote host as GitHub-Enterprise-like purely by inspecting the `WWW-Authenticate` header content that the remote server returned to Git, which Git forwards to the credential helper as `wwwauth[...]` fields. A malicious or compromised git server (or a MITM proxy on an HTTP remote) can freely set `WWW-Authenticate: realm="GitHub"` on its 401 response and be treated by Desktop as an "enterprise"/GitHub-classified endpoint even though it is not `*.ghe.com`/dot-com and fails the DNS-based `isGitHubHost` check.

### Finding Description
`getEndpointKind` first checks the URL against real endpoint predicates (`isGist`, `isDotCom`, `isGHE`), which are safe, host-based checks. [1](#0-0) 

It then falls back to trusting attacker-suppliable data: any `wwwauth[...]` credential field containing `realm="GitHub"` is treated as sufficient evidence the host is a GitHub Enterprise instance, bypassing the network-based `isGitHubHost` probe that would otherwise be used as the last resort. [2](#0-1) 

This value feeds directly into `getCredential`, which changes behavior based on `endpointKind`. When the kind is not `'generic'` and no existing account matches the endpoint, Desktop skips both the generic (`getGenericCredential`) and external (`getExternalCredential`) credential-helper code paths - which would otherwise transparently supply credentials from the OS keychain / git-credential-manager for that host - and instead always surfaces a native "sign in to GitHub" prompt (`ui.promptForGitHubSignIn`) for the attacker-chosen endpoint. [3](#0-2) 

The broken invariant: "an endpoint is treated as a trusted GitHub/GHE host" should only be established through domain-based validation (`isDotCom`/`isGHE`) or the outbound `isGitHubHost` probe (which validates the server actually speaks the GitHub API), never through content an untrusted remote controls in its own HTTP response headers. `getEndpointKind` conflates a heuristic ("happy path") with an authorization decision that changes credential routing and user-facing dialogs.

### Impact Explanation
- A remote fully controlled by the attacker (self-hosted git server the victim is asked to clone/fetch from, or an HTTP MITM on a plain-HTTP remote) can force GitHub Desktop to present a "Sign in to GitHub" dialog scoped to an attacker-chosen host/endpoint, rather than the expected generic-credential or external-credential-helper flow.
- This subverts the user's configured generic credential helper (`useExternalCredentialHelper`) entirely for that host, since the `endpointKind !== 'generic'` branch short-circuits before ever reaching `getGenericCredential`/`getExternalCredential`.
- Because this is purely content-driven (an HTTP response header value), it satisfies the "attacker controls ... a git remote/proxy response" precondition, and the consequence — forcing an unwarranted GitHub sign-in/account-association flow tied to an arbitrary attacker-chosen endpoint — falls under "unauthorized OAuth or account binding" in the accepted impact categories.
- It is a UI/trust-decision bug rather than a direct token leak: real domain checks (`isDotCom`, `isGHE`) still gate the safe classification path, and the actual credential lookup for GitHub accounts (`findGitHubTrampolineAccount`) still matches by exact origin, so previously-stored, unrelated account tokens are not sent to the spoofed host by this bug alone. The primary impact is misleading trust-decision behavior (bypassing generic/external credential helper, coercing a GitHub sign-in prompt for an attacker-controlled host) rather than guaranteed credential exfiltration.

### Likelihood Explanation
Triggering this requires only that the user perform an ordinary git operation (fetch/clone/push) against a remote the attacker controls, or a plain-HTTP remote the attacker can intercept — no special user action beyond "add/use this remote" is needed, and the header is set purely server-side. This matches a normal, low-friction workflow (adding a URL under attacker's influence), which increases likelihood, but the value delivered to the attacker is limited (a phishing-style sign-in prompt for a host they already control) rather than a full compromise, so overall severity/likelihood of concrete damage is moderate.

### Recommendation
Remove reliance on the self-reported `WWW-Authenticate` realm string as a basis for classifying a host as GitHub/Enterprise. Only classify a host as GitHub-like via the pre-existing account lookup (`findGitHubTrampolineAccount`) or the authoritative `isGitHubHost` network probe (which validates the server's actual API surface), and otherwise fall through to the `generic` classification. If the header-based heuristic must be kept for UX reasons (avoiding an extra network round trip), it should never be sufficient on its own to suppress the generic/external credential-helper path or to trigger a GitHub sign-in prompt — it should at most be used to decide the *order* in which credential sources are tried, with `isGitHubHost` (or an explicit user confirmation) required before treating an unknown endpoint as authoritative.

### Proof of Concept
1. Stand up a git server (e.g., a small HTTP server implementing the `git-http-backend` smart protocol) that responds to authentication challenges with `WWW-Authenticate: Basic realm="GitHub"`.
2. In GitHub Desktop, add this server's URL as a remote (e.g., `https://attacker.example.com/foo.git`) and perform a fetch/clone that requires authentication.
3. Git invokes the credential helper trampoline; `parseCredential` populates `wwwauth[0]` from the header, and `getEndpointKind` (having failed `isGist`/`isDotCom`/`isGHE`) matches the `realm="GitHub"` regex and returns `'enterprise'` without ever calling `isGitHubHost`. [4](#0-3) 
4. `getCredential` sees `endpointKind !== 'generic'` and, finding no existing account for `apiEndpoint`, calls `ui.promptForGitHubSignIn('https://attacker.example.com')` instead of consulting the user's configured generic/external credential helper. [5](#0-4) 
5. The user is shown a native "Sign in to GitHub" dialog scoped to the attacker's endpoint, even though this host is neither `github.com` nor a real GHE instance — confirming the classification/trust decision was driven entirely by attacker-controlled response content.

Note: I was not able to fully trace the downstream `ui.promptForGitHubSignIn` implementation or confirm whether any additional confirmation dialog exists there that might mitigate the UX impact; a Devin session with full repo access would be needed to inspect that flow in detail.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L93-135)
```typescript
/** Implementation of the 'get' git credential helper command */
async function getCredential(cred: Credential, store: Store, token: string) {
  const ghCred = await getGitHubCredential(cred, store)

  if (ghCred) {
    return ghCred
  }

  const endpointKind = await getEndpointKind(cred, store)
  const accounts = await store.getAll()

  const endpoint = `${getCredentialUrl(cred)}`
  const apiEndpoint = getAPIEndpoint(endpoint)

  // If it appears as if the endpoint is a GitHub host and we don't have an
  // account for that endpoint then we should prompt the user to sign in.
  if (
    endpointKind !== 'generic' &&
    !accounts.some(a => a.endpoint === apiEndpoint)
  ) {
    if (getIsBackgroundTaskEnvironment(token)) {
      debug('background task environment, skipping prompt')
      return undefined
    }

    const account = await ui.promptForGitHubSignIn(endpoint)

    if (!account) {
      setHasRejectedCredentialsForEndpoint(token, endpoint)
    }

    return credWithAccount(cred, account)
  }

  // GitHub.com/GHE creds are only stored internally
  if (endpointKind !== 'generic') {
    return undefined
  }

  return useExternalCredentialHelper()
    ? getExternalCredential(cred, token)
    : getGenericCredential(cred, token)
}
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L137-151)
```typescript
const getEndpointKind = async (cred: Credential, store: Store) => {
  const credentialUrl = getCredentialUrl(cred)
  const endpoint = `${credentialUrl}`

  if (isGist(endpoint)) {
    return 'generic'
  }

  if (isDotCom(endpoint)) {
    return 'github.com'
  }

  if (isGHE(endpoint)) {
    return 'ghe.com'
  }
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L152-179)
```typescript

  // When Git attempts to authenticate with a host it captures any
  // WWW-Authenticate headers and forwards them to the credential helper. We
  // use them as a happy-path to determine if the host is a GitHub host without
  // having to resort to making a request ourselves.
  for (const [k, v] of cred.entries()) {
    if (k.startsWith('wwwauth[')) {
      if (v.includes('realm="GitHub"')) {
        return 'enterprise'
      } else if (/realm="(GitLab|Gitea|Atlassian Bitbucket)"/.test(v)) {
        return 'generic'
      }
    }
  }

  const existingAccount = await findGitHubTrampolineAccount(store, endpoint)
  if (existingAccount) {
    return isDotCom(existingAccount.endpoint) ? 'github.com' : 'enterprise'
  }

  // All GitHub hosts use HTTPS, so if the protocol is not HTTPS we can
  // assume that this is not a GitHub host.
  if (credentialUrl.protocol !== 'https:') {
    return 'generic'
  }

  return (await isGitHubHost(endpoint)) ? 'enterprise' : 'generic'
}
```
