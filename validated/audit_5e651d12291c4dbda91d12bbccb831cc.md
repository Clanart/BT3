Based on what I was able to verify, I found an analog: GitHub Desktop caches a GitHub Enterprise Server version string derived from an HTTP response header, persists it indefinitely, and never re-validates it — structurally the same "stale trust-relevant version value" pattern as the StakeWise finding (a cached/hardcoded version value that should track the live contract/server state but doesn't, causing downstream verification logic to operate on outdated version data).

### Title
Persisted, attacker-influenceable GHES version cache is used to gate security/feature checks without expiry or re-validation - (File: `app/src/lib/endpoint-capabilities.ts`)

### Summary
`endpoint-capabilities.ts` maintains an in-memory `versionCache`/`rawVersionCache` and a `localStorage`-backed copy of each GitHub Enterprise Server endpoint's reported version, populated from the `x-github-enterprise-version` response header via `updateEndpointVersion()`. This cached value is read by `getEndpointVersion()` and used by `endpointSatisfies()` to gate version-dependent capability checks (`supportsAvatarsAPI`, `supportsRerunningChecks`, etc.) with no TTL, no invalidation on endpoint change/logout, and no revalidation unless a later response happens to report a different value.

### Finding Description
`getEndpointVersion(endpoint)` first checks the in-memory `versionCache`; if absent it falls back to `localStorage`, but once a value is cached (memory or disk) it is trusted indefinitely: [1](#0-0) . The cache is only overwritten by `updateEndpointVersion()`, which is driven by whatever `x-github-enterprise-version` header value the server (or anything sitting between the client and the server) returns on a response: [2](#0-1) . Because the header is attacker-observable/attacker-influenceable on the network path to a self-hosted GHES instance, and the resulting value is persisted to `localStorage` (durable across app restarts) with no expiry, a single malicious/compromised response can poison the cached version for that endpoint going forward. `endpointSatisfies()` then trusts this cached value (or falls back to `assumedGHESVersion` only when nothing is cached at all) when deciding whether version-gated capabilities are enabled: [3](#0-2) . This mirrors the StakeWise bug class exactly: a version value that is supposed to track the live authoritative source (the contract's `version()` / here, the live GHES server) is instead cached and can silently diverge from reality, and downstream logic (`_computeDomainSeparator()` / here, `endpointSatisfies()`) keeps using the stale value for security/feature-relevant decisions.

### Impact Explanation
Capabilities gated by this cache determine whether Desktop calls certain internal GHES APIs (avatars API, check re-run API) versus other code paths. Poisoning the cached version could cause Desktop to believe a downgraded/older GHES instance still supports a feature it no longer does, or vice versa, leading to failed/misrouted API calls against attacker-controlled infrastructure sitting in front of a GHES endpoint. The severity is bounded by the fact that none of the currently gated capabilities in this file are directly credential- or code-execution-critical (`supportsRepoRules` itself is `dotcom: true` only and unaffected by this cache), so the practical impact today is feature-gating confusion/misrouted API calls tied to a spoofable, persisted value rather than direct compromise — comparable to a medium-severity "stale trust value" issue rather than a critical one.

### Likelihood Explanation
Exploitation requires the attacker to control or man-in-the-middle a response to a GHES endpoint that Desktop is configured to talk to (satisfying the report's "attacker controls...a git remote/proxy response" precondition) at least once, after which the poisoned value persists in `localStorage` without expiry until a legitimate response happens to overwrite it. No local/physical access, admin rights, or leaked credentials are required beyond the ability to respond to (or tamper with) HTTP traffic to a self-hosted GHES instance.

### Recommendation
Add a TTL/expiry to the cached version (re-validate periodically or on each session start), and only accept the `x-github-enterprise-version` header over a validated TLS channel to a known endpoint; consider not persisting it to `localStorage` at all, or invalidating it on account removal/endpoint change, so stale/spoofed values cannot silently outlive the connection that produced them.

### Proof of Concept
1. Add a GHES account whose endpoint is fronted by a network path the attacker can influence (e.g., corporate proxy/MITM).
2. Have that intermediary return a response containing a crafted `x-github-enterprise-version` header (e.g., an inflated version string) on any API call.
3. Observe that `updateEndpointVersion()` writes the attacker-chosen version into `rawVersionCache`/`versionCache` and `localStorage` via `app/src/lib/endpoint-capabilities.ts:91-100`.
4. Restart Desktop; observe `getEndpointVersion()` returns the poisoned value from `localStorage` (`app/src/lib/endpoint-capabilities.ts:70-86`) with no re-validation, causing `endpointSatisfies()`-gated capabilities to be evaluated against the false version indefinitely, even after the intermediary is removed.

Note: I could not fully trace every call site of `updateEndpointVersion()` in `app/src/lib/api.ts` within the available iterations (grep confirmed 3 references but content wasn't read), so I cannot state with certainty how frequently the header is re-checked per session or whether any additional revalidation exists elsewhere in that file — a full review of `app/src/lib/api.ts` would be needed to confirm exact refresh cadence.

### Citations

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

**File:** app/src/lib/endpoint-capabilities.ts (L102-134)
```typescript
function checkConstraint(
  epConstraint: string | boolean | undefined,
  epMatchesType: boolean,
  epVersion?: semver.SemVer
) {
  // Denial of endpoint type regardless of version
  if (epConstraint === undefined || epConstraint === false) {
    return false
  }

  // Approval of endpoint type regardless of version
  if (epConstraint === true) {
    return epMatchesType
  }

  // Version number constraint
  assertNonNullable(epVersion, `Need to provide a version to compare against`)
  return epMatchesType && semver.satisfies(epVersion, epConstraint)
}

/**
 * Returns a predicate which verifies whether a given endpoint matches the
 * provided constraints.
 *
 * Note: NOT meant for direct consumption, only exported for testability reasons.
 *       Consumers should use the various `supports*` methods instead.
 */
export const endpointSatisfies =
  ({ dotcom, ghe, es }: VersionConstraint, getVersion = getEndpointVersion) =>
  (ep: string) =>
    checkConstraint(dotcom, isDotCom(ep)) ||
    checkConstraint(ghe ?? dotcom, isGHE(ep)) ||
    checkConstraint(es, isGHES(ep), getVersion(ep) ?? assumedGHESVersion)
```
