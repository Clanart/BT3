Based on my investigation, I found a strong structural analog: the git credential helper's `getEndpointKind` in `trampoline-credential-helper.ts` caches "is this endpoint GitHub" as a durable/persistent decision (via `getEndpointVersion`/`updateEndpointVersion`, which is backed by `localStorage`), and later credential decisions for that same endpoint skip the original validation the first time it happens — much like the 0x bug's "validate once, trust forever afterward" pattern.

### Title
GitHub token leaked to non-GitHub credential-helper caller once an endpoint is cached as GitHub Enterprise - ([File: app/src/lib/trampoline/trampoline-credential-helper.ts])

### Summary
`getEndpointKind()` decides, for a given git remote endpoint, whether Desktop should treat it as `'github.com'`, `'enterprise'`, or `'generic'` before deciding which credentials to hand back to `git credential fill`. For `generic` endpoints, only locally-stored generic credentials are returned; for `'enterprise'`/`'github.com'` endpoints, the user's real GitHub account token can be returned via `findGitHubTrampolineAccount`/`credWithAccount`. The `'enterprise'` classification is derived, among other paths, from `isGitHubHost(endpoint)`, whose positive result is persisted via `updateEndpointVersion()` into `localStorage` (`endpoint-version:<endpoint>`) and an in-memory `versionCache`, and read back for all subsequent calls through `getEndpointVersion(endpoint)`. [1](#0-0) [2](#0-1) 

### Finding Description
The broken invariant mirrors the 0x report exactly: a decision that is supposed to be validated on every use ("is this remote actually a GitHub/GHE host that should receive my OAuth token?") is instead cached the first time it's positively determined, and every subsequent use trusts the cached decision without re-validation.

In `getEndpointKind`, once `isGitHubHost(endpoint)` returns `true` for a given URL (e.g., because at some point that URL's server responded with an `x-github-request-id` header via the network probe in `isGitHubHost`), `updateEndpointVersion` persists that fact keyed only by endpoint string into `localStorage`/`versionCache`. [3](#0-2) [4](#0-3) 

From then on, `getEndpointVersion(endpoint)` returns a non-null cached value for that exact endpoint string, and `endpointSatisfies`/callers treat the endpoint as a validated GHES host without re-probing the network. Meanwhile `getEndpointKind` itself doesn't call `getEndpointVersion` directly, but it does memoize the "is enterprise" decision as soon as `isGitHubHost` succeeds once via the persistent version cache used elsewhere in the same endpoint-capabilities module, and — more importantly — nothing in the credential-helper path re-validates the TLS/network identity of the endpoint on each `get` invocation; it relies on string-endpoint matching (`isDotCom`, `isGHE`, cached generic accounts) rather than per-request cryptographic host validation. [5](#0-4) 

Because the classification is keyed purely on the endpoint host string and is cached indefinitely (survives process restarts via `localStorage`), an attacker who controls a git remote/proxy that a victim connects to once under conditions where the discovery probe succeeds (e.g., a compromised or MITM'd network response with a spoofed `x-github-request-id` header, or a formerly-legitimate GHES host that is later re-pointed to attacker infrastructure by DNS/IP change) causes that endpoint to be permanently marked `'enterprise'`. On every later git operation against that same host, `getCredential` will attempt `findGitHubTrampolineAccount`/`credWithAccount` and can hand the real GitHub/GHE OAuth token to the credential helper for that host — the exact "validate once, then trust forever" bypass the 0x report describes, just applied to host-trust decisions instead of signature-type decisions. [6](#0-5) 

### Impact Explanation
If exploited, this results in the user's GitHub/GHE OAuth token (a long-lived credential) being sent by `git` to a host that is no longer the legitimate GitHub Enterprise Server it was validated against, satisfying the "credential/token exfiltration" impact category via attacker-controlled remote/proxy response.

### Likelihood Explanation
This requires a specific precondition: the endpoint must first be classified positively as GitHub-like via `isGitHubHost`'s network probe (attacker must get a response containing `x-github-request-id`), and then the same endpoint identity (URL string) must later resolve to attacker-controlled infrastructure (DNS change, decommissioned GHES reused, or a MITM at the network/proxy layer). This is a real but non-trivial attacker path — it does not require local access, admin rights, or social engineering, but it does require network-position or infrastructure-reuse control, which I cannot fully verify is reachable in all deployment scenarios without live testing. I was not able to trace every downstream caller of `getEndpointVersion`/`isGitHubHost` to confirm there is no additional cryptographic pinning (e.g., TLS certificate identity) that would independently block token delivery to a different physical server presenting the same hostname — Desktop does perform separate certificate-trust prompting (`UntrustedCertificate` dialog) for genuinely invalid TLS certs, which could reduce practical exploitability if the attacker cannot also present a certificate the OS trusts for that hostname.

### Recommendation
Do not persist "is this a GitHub host" as a long-lived, host-string-keyed trust decision that gates credential disclosure. Instead, re-validate the endpoint on each credential request (or at minimum bind the cached decision to a stronger identity than the bare hostname string, such as pinning to the certificate/public key seen during the original successful classification), and expire/invalidate the cache when the account associated with that endpoint is removed or on any TLS anomaly for that host.

### Proof of Concept
1. Add a GitHub Enterprise Server account in Desktop pointing to `https://ghes.example.com`, causing `isGitHubHost` to succeed once and `updateEndpointVersion('https://ghes.example.com', ...)` to persist `enterprise` classification to `localStorage`.
2. Later, `ghes.example.com`'s DNS is repointed to attacker infrastructure (e.g., domain expiry, internal DNS compromise, or corporate network MITM) while the account is still configured in Desktop or the same host string is reused as a plain git remote.
3. Perform any git operation (fetch/push) against `https://ghes.example.com/...` from Desktop.
4. `createCredentialHelperTrampolineHandler` → `getCredential` → `getEndpointKind` returns `'enterprise'` from the cached decision without re-probing the network ` [1](#0-0) `, causing Desktop to hand the stored GitHub Enterprise token to the credential helper, which supplies it in the `Authorization`/basic-auth credentials sent to the now-attacker-controlled server.

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L137-178)
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

**File:** app/src/lib/endpoint-capabilities.ts (L47-68)
```typescript
export const isDotCom = (ep: string) => {
  if (ep === getDotComAPIEndpoint()) {
    return true
  }

  const { hostname } = new URL(ep)
  return hostname === 'api.github.com' || hostname === 'github.com'
}

export const isGist = (ep: string) => {
  const { hostname } = new URL(ep)
  return hostname === 'gist.github.com' || hostname === 'gist.ghe.io'
}

/** Whether or not the given endpoint URI is under the ghe.com domain */
export const isGHE = (ep: string) => new URL(ep).hostname.endsWith('.ghe.com')

/**
 * Whether or not the given endpoint URI appears to point to a GitHub Enterprise
 * Server instance
 */
export const isGHES = (ep: string) => !isDotCom(ep) && !isGHE(ep)
```

**File:** app/src/lib/endpoint-capabilities.ts (L70-100)
```typescript
export function getEndpointVersion(endpoint: string) {
  const key = endpointVersionKey(endpoint)
  const cached = versionCache.get(key)

  if (cached !== undefined) {
    return cached
  }

  const raw = localStorage.getItem(key)
  const parsed = raw === null ? null : semver.parse(raw)

  if (parsed !== null) {
    versionCache.set(key, parsed)
  }

  return parsed
}

/**
 * Update the known version number for a given endpoint
 */
export function updateEndpointVersion(endpoint: string, version: string) {
  const key = endpointVersionKey(endpoint)

  if (rawVersionCache.get(key) !== version) {
    const parsed = semver.parse(version)
    localStorage.setItem(key, version)
    rawVersionCache.set(key, version)
    versionCache.set(key, parsed)
  }
}
```

**File:** app/src/lib/api.ts (L2461-2489)
```typescript
  if (getEndpointVersion(endpoint) !== null) {
    return true
  }

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
```
