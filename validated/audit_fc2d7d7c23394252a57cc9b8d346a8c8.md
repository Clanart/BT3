This is a genuine analog: the `git-store`'s trampoline credential helper decides whether an untrusted git remote host is treated as a "GitHub host" (and therefore eligible for `promptForGitHubSignIn`, which can bind an OAuth account/token to that host) based on a version cache that is written once and never expires or is re-validated — the same "trust a snapshot without checking recency" flaw as the Chainlink `roundId`/`updatedAt` bug.

### Title
Endpoint GitHub-host trust decision relies on a stale, unbounded version cache with no re-validation - (File: `app/src/lib/api.ts`, `app/src/lib/endpoint-capabilities.ts`)

### Summary
`isGitHubHost()` in `app/src/lib/api.ts` is the function GitHub Desktop uses to decide, for an arbitrary remote/credential endpoint, whether that endpoint is "a GitHub host" it should treat as first-party (triggering account binding / sign-in prompts via the trampoline credential helper). Before doing a live network probe it takes a shortcut: [1](#0-0) 

`getEndpointVersion(endpoint)` reads from an in-memory/`localStorage`-backed cache keyed only by `endpoint` string, with no expiry, no freshness check, and no signal of "how it was obtained" (see `versionCache`/`rawVersionCache` in `endpoint-capabilities.ts`, lines 35-39, 70-100). If any prior response — at any point in the past, from any code path calling `tryUpdateEndpointVersionFromResponse` — set a version for that endpoint string, `isGitHubHost` short-circuits to `true` forever, skipping the actual `/meta` HEAD probe and its `x-github-request-id` check.

### Finding Description
The root cause mirrors the oracle bug's structure exactly: a value obtained from an untrusted external source (`x-github-enterprise-version` response header) is cached and later consumed as ground truth for a security decision, without validating "is this still current / was it from the same host identity being asked about now". There is no equivalent of Chainlink's `roundId`/`updatedAt` check here — no timestamp, no re-validation window, no invalidation on IP/host reassignment, and the cache is keyed purely on the URL string, not any binding to certificate identity.

Concretely:
- `updateEndpointVersion` is called from multiple code paths that receive a `Response` for a given `endpoint` string (e.g. `ghRequest`, `requestOAuthToken`, `isGitHubHost` itself) — [2](#0-1) .
- Once set, `getEndpointVersion` returns the cached `SemVer` indefinitely — [3](#0-2) .
- `isGitHubHost` treats presence of *any* cached version as proof the endpoint is a genuine GitHub host, bypassing the network verification (`x-github-request-id` header check against a live `/meta` probe) that is otherwise the only real verification step — [4](#0-3) .
- `trampoline-credential-helper.ts`'s `getEndpointKind` calls `isGitHubHost(endpoint)` as the last-resort classifier for an arbitrary git remote host encountered during `fetch`/`clone`/`push`, and if it returns true, classifies the host as `'enterprise'` and invokes `ui.promptForGitHubSignIn(endpoint)` [5](#0-4) , which can lead the user to authenticate/bind a GitHub account credential flow against that endpoint [6](#0-5) .

The attacker-controlled input is the git remote/proxy endpoint (an untrusted repository's remote URL, or a network position that previously answered a probe/API request for that same hostname with a forged `x-github-enterprise-version` header, e.g. a shared corporate DNS name, a reused IP, or a host later repurposed/compromised). Because the cache is never invalidated or time-boxed, a single stale/forged response is enough to make the app treat that endpoint as trusted GitHub Enterprise indefinitely, even after the underlying server identity has changed.

### Impact Explanation
If `isGitHubHost` incorrectly reports `true` for a non-GitHub or now-different host due to a stale cache entry, the trampoline credential helper classifies it as `'enterprise'` and drives the user through `promptForGitHubSignIn`, which is the flow used to associate a GitHub account/token with that endpoint. This can result in unauthorized OAuth/account binding to a host that is not actually the entity it was originally verified against, and/or normal git credential flows for that endpoint being funneled through GitHub-specific (rather than generic) credential handling, deviating from user expectations about which service is receiving sign-in prompts. This fits the "unauthorized OAuth or account binding" and "attacker controls...a git remote/proxy response" categories.

### Likelihood Explanation
Moderate-to-low. Exploitation requires the attacker to get a forged/legitimate-looking `x-github-enterprise-version` header accepted for a given endpoint string at some point (e.g. via a compromised or previously-GHE host now repurposed, DNS/IP reuse, or a corporate proxy that later points the same hostname elsewhere), and for the victim to interact with that same endpoint again later (e.g. add it as a remote or clone from it) without the app re-verifying. This is not a trivial one-click remote exploit, but it does not require local access, admin rights, or prior malware — only control of one endpoint response at some point in the host's lifetime, which matches the "attacker controls...a git remote/proxy response" primitive in the report's valid-impact list.

### Recommendation
Treat the endpoint-version cache purely as a performance hint, not as proof of identity:
- Add a TTL/expiry to `versionCache`/`rawVersionCache` entries (analogous to checking `updatedAt`/`roundId` staleness) so cached values older than a bounded window are not used to skip live verification.
- Do not let `getEndpointVersion(endpoint) !== null` alone satisfy the "GitHub host" determination in `isGitHubHost`; always perform (or periodically re-perform) the live `/meta` request-id check, especially before using it as the basis for prompting for credential/account binding.
- Bind the cache entry to something more resistant to host reuse (e.g. combine with TLS certificate fingerprint pinning or at least re-validate on certificate/host change) rather than keying solely on the endpoint string.

### Proof of Concept
1. At time T0, an attacker-influenced host `https://ghe.example.com` responds once (e.g., to any `ghRequest`/`requestOAuthToken` call, or to `isGitHubHost`'s own `/meta` probe) with header `x-github-enterprise-version: 3.9.0`. `updateEndpointVersion` persists this to `localStorage` under key `endpoint-version:https://ghe.example.com` with no expiry [7](#0-6) .
2. At time T1 (days/weeks later), the DNS entry / infrastructure behind `ghe.example.com` changes (reused hostname, expired domain re-registered, or corporate proxy repointed) to a host no longer actually running GHE.
3. The user adds/clones a repository whose remote points at `https://ghe.example.com/...`. Desktop's trampoline credential helper calls `getEndpointKind`, which falls through to `isGitHubHost(endpoint)` [8](#0-7) .
4. `isGitHubHost` finds `getEndpointVersion(endpoint) !== null` still true from the stale T0 cache entry and returns `true` immediately, skipping the live `/meta` HEAD probe entirely [1](#0-0) .
5. `getEndpointKind` returns `'enterprise'`, and `getCredential` invokes `ui.promptForGitHubSignIn(endpoint)`, prompting the user to sign in / bind a GitHub-style account to a host that is no longer verified as a genuine GitHub Enterprise server at the time of the prompt.

Note: I was not able to fully trace whether `promptForGitHubSignIn`'s downstream OAuth flow performs any additional server-side verification that would catch this at a later step (e.g., during `requestOAuthToken`), since that UI component's implementation was outside what I could pull from the index. If that step re-validates the host cryptographically, the practical impact would be reduced to a spurious/misleading sign-in prompt rather than actual token exfiltration — this residual uncertainty should be checked with the full source (a Devin session would have access to the complete `trampoline-ui-helper.ts` and OAuth callback code to confirm).

### Citations

**File:** app/src/lib/api.ts (L2397-2405)
```typescript
function tryUpdateEndpointVersionFromResponse(
  endpoint: string,
  response: Response
) {
  const gheVersion = response.headers.get('x-github-enterprise-version')
  if (gheVersion !== null) {
    updateEndpointVersion(endpoint, gheVersion)
  }
}
```

**File:** app/src/lib/api.ts (L2461-2491)
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
  }
}
```

**File:** app/src/lib/endpoint-capabilities.ts (L70-86)
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
```

**File:** app/src/lib/endpoint-capabilities.ts (L91-100)
```typescript
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L107-125)
```typescript
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L167-178)
```typescript
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
