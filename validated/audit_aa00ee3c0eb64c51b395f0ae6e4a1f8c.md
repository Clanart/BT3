## Title
Attacker-Controlled Git Server Can Spoof GitHub-Host Detection to Trigger Unwanted GitHub Sign-In / Account Binding via the Credential Helper Trampoline - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The original report's root cause is a boolean state (`exitAdministrator` assigned or not) being used as a *trust signal* that is derived from data the contract cannot fully control, and which defaults to a permissive/optimistic outcome (`getDeadStatus()` returns `false`) instead of failing closed, letting later privileged code paths (`cancelDepositWhileDead`, `DirectExitAdministrator.withdraw`) rely on that same unverifiable signal and get stuck. The general vulnerability class is: **a security-relevant classification is derived from attacker-influenceable signals with no independent verification, and that classification then drives a privileged action.**

Desktop has a structurally analogous pattern in `getEndpointKind()` in `trampoline-credential-helper.ts`. When Git needs credentials for a remote it doesn't recognize, Desktop classifies the remote host as `github.com` / `ghe.com` / `enterprise` / `generic` by trusting response headers the *remote server itself* controls (`WWW-Authenticate: realm="GitHub"`, or a `x-github-request-id` header on a probe `HEAD` request). That classification then determines whether GitHub Desktop automatically prompts the user to sign in to GitHub for that host.

### Finding Description
`getEndpointKind()` classifies the credential target as follows: [1](#0-0) 

- If a `WWW-Authenticate` header captured from the failed Git request contains `realm="GitHub"`, the host is classified `'enterprise'`.
- Otherwise, Desktop makes a `HEAD` request to `${endpoint}/meta` and classifies the host as `'enterprise'` if the response contains an `x-github-request-id` header, via `isGitHubHost()`: [2](#0-1) 

Both signals — the `WWW-Authenticate` header on the original Git request and the `x-github-request-id` header on the `/meta` probe — are entirely under the control of whatever server the Git remote (or a proxy sitting in front of it) resolves to. A malicious git host or man‑in‑the‑middle proxy can trivially emit either header, since neither is cryptographically bound to GitHub in any way (no TLS pinning, no signature check — just a header string match).

That classification is then consumed in `getCredential()`, the entry point invoked by Git's `credential.helper=desktop` for every authentication attempt: [3](#0-2) 

If `endpointKind !== 'generic'` and no existing account is already registered for that endpoint, Desktop calls `ui.promptForGitHubSignIn(endpoint)` — the exact same flow used to add a legitimate GitHub Enterprise account — for the attacker-controlled `endpoint`. This is the direct analog of the report's broken invariant: a downstream, security-sensitive branch (`promptForGitHubSignIn` / account creation) trusts a classification (`getDeadStatus()` in the original report, `endpointKind` here) that is derived from a value the attacker can set, with no independent verification such as certificate pinning, a signed GitHub identity assertion, or user confirmation that "this is really GitHub."

### Impact Explanation
Because `getEndpointKind()` can be tricked into returning `'enterprise'` for any attacker-controlled host, cloning or fetching a hostile repository (or being routed through a hostile proxy) can cause GitHub Desktop to unprompted-ly surface its GitHub sign-in UI, bound to the attacker's endpoint, and — if the user completes it — bind a new "Enterprise" account entry to that attacker-controlled endpoint (`findGitHubTrampolineAccount`/account store keyed by that endpoint going forward). This falls squarely in the accepted impact category "unauthorized OAuth or account binding": the app initiates or offers an authentication ceremony against a host it has not actually verified belongs to GitHub, based purely on spoofable protocol-level signals.

### Likelihood Explanation
The trigger requires nothing beyond the normal, expected user action of adding/cloning a repository whose remote resolves to an attacker's HTTPS server, or a network path where a proxy is attacker-influenced — both of which are explicitly in-scope per the "Valid Impact" list ("a git remote/proxy response"). No local access, malware, or leaked credentials are needed; a single crafted 401 response with a `WWW-Authenticate: realm="GitHub"` header, or a `/meta` HEAD response with `x-github-request-id` set, is sufficient to flip the classification.

### Recommendation
Do not derive a "this is a GitHub host" trust decision solely from response headers the remote server controls. At minimum:
- Require corroborating evidence that cannot be forged by an arbitrary HTTPS server (e.g., only trust `isGitHubHost()`/`WWW-Authenticate` heuristics to *suggest* a prompt, but always show the resolved hostname prominently and require explicit user confirmation before initiating any GitHub sign-in flow for a previously-unknown host).
- Consider rate/attempt limiting and an explicit "Add GitHub Enterprise account for `<host>`?" confirmation dialog distinct from the normal credential-prompt UX, so users aren't silently walked into an account-binding flow triggered by network responses.
- Audit other consumers of `isGitHubHost()`/`getEndpointKind()`-style classification for the same fail-permissive pattern.

### Proof of Concept
1. Stand up an HTTPS server (or MITM proxy) at `https://evil.example.com` that:
   - Responds to unauthenticated Git requests with `401` and header `WWW-Authenticate: realm="GitHub"`, or
   - Responds to `HEAD /meta` with header `x-github-request-id: 1`.
2. In GitHub Desktop, clone (or add as remote and fetch) a repository at `https://evil.example.com/whatever/repo.git`.
3. Git invokes Desktop's credential helper (`credential.helper=desktop`), which calls `getEndpointKind()`; the spoofed header causes it to return `'enterprise'`. [1](#0-0) 
4. Since no account exists for `evil.example.com`, `getCredential()` invokes `ui.promptForGitHubSignIn('https://evil.example.com')`, surfacing GitHub Desktop's native "sign in to GitHub Enterprise" UI bound to the attacker's host. [4](#0-3) 

Note: I was not able to inspect `trampoline-ui-helper.ts`'s `promptForGitHubSignIn` implementation or the downstream OAuth token-exchange code in this session (index/tool budget limits), so the exact secondary consequences of a completed sign-in against the spoofed endpoint (e.g., whether the app's embedded OAuth client secret is sent to the attacker's server during token exchange) are not fully verified here and should be confirmed by a full Devin session with repository access before treating the OAuth-secret-leak sub-claim as established; the account-binding/unwanted-sign-in-prompt behavior itself, however, is directly supported by the cited code.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L94-125)
```typescript
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
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L153-178)
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
```

**File:** app/src/lib/api.ts (L2465-2491)
```typescript
  // Add a unique identifier to the URL to make sure our certificate error
  // supression only catches this request
  const metaUrl = `${endpoint}/meta?ghd=${crypto.randomUUID()}`

  const ac = new AbortController()
  const timeoutId = setTimeout(() => ac.abort(), 2000)
  suppressCertificateErrorFor(metaUrl)
  try {
    const response = await fetch(metaUrl, {
      headers: { 'user-agent': getUserAgent() },
      signal: ac.signal,
      credentials: 'omit',
      method: 'HEAD',
      redirect: 'error',
    })

    tryUpdateEndpointVersionFromResponse(endpoint, response)

    return response.headers.has('x-github-request-id')
  } catch (e) {
    log.debug(`isGitHubHost: failed with endpoint ${endpoint}`, e)
    return undefined
  } finally {
    clearTimeout(timeoutId)
    clearCertificateErrorSuppressionFor(metaUrl)
  }
}
```
