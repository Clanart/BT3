## Title
Cache poisoning of GitHub-host detection via spoofed `x-github-enterprise-version` response header allows permanent bypass of `isGitHubHost()` verification — (File: `app/src/lib/api.ts`)

## Summary
`isGitHubHost()` caches the enterprise version reported by any HTTP response for a given endpoint — including on the very code path meant to *reject* a host — without verifying that the response actually came from a genuine GitHub server. A one-time spoofed `x-github-enterprise-version` header from an attacker-controlled host is written to `localStorage` and causes `isGitHubHost()` to unconditionally return `true` for that host on every subsequent call, across app restarts, skipping the actual network verification.

## Finding Description
`tryUpdateEndpointVersionFromResponse()` blindly trusts the `x-github-enterprise-version` header on any response and persists it via `updateEndpointVersion()`, which writes straight to `localStorage` with no origin validation: [1](#0-0) [2](#0-1) 

Inside `isGitHubHost()`, when an endpoint is not already known as `dotcom`/`ghe`/a known third party, the function issues a `HEAD /meta` request and decides trust based on the presence of `x-github-request-id`: [3](#0-2) 

Critically, `tryUpdateEndpointVersionFromResponse(endpoint, response)` is called **before** the `x-github-request-id` check and regardless of its outcome. So even when the function correctly determines the host is *not* GitHub (no `x-github-request-id` header, returns `false`), it still caches whatever `x-github-enterprise-version` value the attacker's server sent.

On every future call, the fast-path check at the top of the function short-circuits before any network verification happens at all: [4](#0-3) 

Because `getEndpointVersion()` first checks an in-memory `versionCache` and falls back to `localStorage`, the poisoned value survives process restarts: [5](#0-4) 

The reachable, attacker-influenced call path is through the git credential-helper trampoline, which calls `isGitHubHost(endpoint)` with the endpoint derived from a git remote's credential URL (fully attacker-controlled if the user adds/fetches from an untrusted remote): [6](#0-5) 

## Impact Explanation
Once poisoned, `getEndpointKind()` will classify the attacker's non-GitHub host as `'enterprise'` forever, causing `getCredential()` to treat it like a GitHub Enterprise endpoint instead of a generic git host: [7](#0-6) 

This changes how Desktop stores/retrieves credentials for that host and can trigger the GitHub-style sign-in prompt flow (`ui.promptForGitHubSignIn`) for a server that is not actually GitHub, effectively binding that arbitrary attacker-controlled host into the "enterprise" account trust category rather than the generic-credential path. This qualifies as an unauthorized account-binding/host-classification bypass, matching the reportable "unauthorized OAuth or account binding" impact category.

## Likelihood Explanation
Exploitation requires only that the user perform a git operation (clone/fetch/push) against an attacker-controlled remote once, causing the credential trampoline to invoke `isGitHubHost()` against that host, which the attacker's server can respond to (the `/meta` HEAD request) with a crafted header. No credentials, local access, or user interaction beyond normal git usage against a malicious remote are needed, and no expiration exists — the poisoning is permanent (per-endpoint, in `localStorage`).

## Recommendation
Only call `tryUpdateEndpointVersionFromResponse()` in `isGitHubHost()` after confirming `response.headers.has('x-github-request-id')` is `true` (i.e., only cache the version when the host has already been positively verified as GitHub), rather than caching on every response unconditionally.

## Proof of Concept
1. Stand up a mock HTTPS server that responds to `HEAD /meta?...` with header `x-github-enterprise-version: 3.9.0` but without `x-github-request-id`.
2. Call `isGitHubHost('https://attacker.example.com/some/repo.git')` (e.g. by adding/fetching from that remote so the credential trampoline invokes `getEndpointKind`). First call returns `false` (correct), but `updateEndpointVersion` is invoked and persists `endpoint-version:https://attacker.example.com` to `localStorage`.
3. Restart the app (or just call again) with network disabled/blocked for that host.
4. Call `isGitHubHost('https://attacker.example.com/...')` again — it now returns `true` immediately via the `getEndpointVersion(endpoint) !== null` fast path at [4](#0-3) , without making any network request, confirming the permanent cache-poisoning bypass.

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

**File:** app/src/lib/api.ts (L2461-2463)
```typescript
  if (getEndpointVersion(endpoint) !== null) {
    return true
  }
```

**File:** app/src/lib/api.ts (L2467-2491)
```typescript
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L101-125)
```typescript
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L172-179)
```typescript
  // All GitHub hosts use HTTPS, so if the protocol is not HTTPS we can
  // assume that this is not a GitHub host.
  if (credentialUrl.protocol !== 'https:') {
    return 'generic'
  }

  return (await isGitHubHost(endpoint)) ? 'enterprise' : 'generic'
}
```
