### Title
Stale/incomplete hardcoded sensitive-header allowlist in cross-origin redirect sanitizer permits credential leakage - (File: app/src/main-process/same-origin-filter.ts)

### Summary
The external report's bug class is a **stale security-relevant constant**: a value meant to track the current trust/version boundary (`"3.0-alpha"`) was never updated when the protocol moved to `"5.0-alpha"`, silently weakening the EIP712 domain separation. The closest verifiable analog in GitHub Desktop is `installSameOriginFilter` in `app/src/main-process/same-origin-filter.ts`, which relies on a small, hardcoded `unsafeHeaders` set to decide which headers must be stripped when a cross-origin HTTP redirect occurs. Just like the wrong version string, this is a fixed value that must be kept in lock-step with everything else in the codebase that adds sensitive headers to `fetch` requests — if it isn't, the "security boundary" (stripping sensitive headers on cross-origin redirects) silently stops covering headers it should.

### Finding Description
`installSameOriginFilter` explicitly exists to compensate for the fact that `fetch` has no way to prevent credential leakage across an http redirect to a different origin [1](#0-0) . Its enforcement is entirely dependent on one hardcoded constant:

```ts
const unsafeHeaders = new Set(['authentication', 'authorization', 'cookie'])
``` [2](#0-1) 

On a cross-origin redirect, only headers in this exact set are stripped; everything else in `details.requestHeaders` is forwarded unchanged to the new origin [3](#0-2) .

Elsewhere in the same main process, `installAuthenticatedImageFilter` injects a **different** authorization-style header for GHES avatar/asset requests:

```ts
requestHeaders: {
  ...details.requestHeaders,
  Authorization: `token ${token}`,
},
``` [4](#0-3) 

This header is named `Authorization`, which the `unsafeHeaders` set does cover today (case-insensitively, via `k.toLowerCase()`), so as of the current snapshot there's no active leak from that particular filter. However, the safety net that prevents header leakage on redirect is a single, manually maintained lowercase string list, not derived from where headers actually get attached in the codebase. Any future header added anywhere in the main process's `onBeforeSendHeaders` chain (e.g. a new authenticated header for a new backend integration, a session header, a custom token header) will silently bypass the redirect-sanitization filter unless a developer remembers to also update this unrelated file. This is structurally identical to the reported bug: a security-relevant constant (`version: "3.0-alpha"` / `unsafeHeaders`) that must be manually kept in sync with the rest of the system and is not derived or validated against the actual current state, so it silently goes stale as the codebase evolves.

### Impact Explanation
If a new sensitive header (e.g. session cookie surrogate, bearer token, custom auth header) is introduced anywhere in Desktop's HTTP request pipeline without a matching update to `unsafeHeaders` in `same-origin-filter.ts`, that header would be forwarded to an attacker-controlled cross-origin redirect target. Since `Server`/API responses (including from a malicious/compromised GHES instance, a malicious remote proxy, or a crafted redirect chain) fully control the `Location` header and thus the redirect target, this could exfiltrate credentials to an attacker-controlled origin, matching the "credential/token exfiltration" impact category via a "git remote/proxy response" attacker vector.

### Likelihood Explanation
Currently no header is known to slip through the allowlist (both `Authorization` variants used in the codebase are covered), so this is a **latent/structural weakness** rather than an actively exploitable bug in the present snapshot — much like the PoCo report itself was "Acknowledged" as a correctness bug rather than a demonstrated exploit. The likelihood of eventual exploitation grows any time a new authenticated header is added to a `webRequest` filter (as already happened once with `installAuthenticatedImageFilter`) without an explicit review of `same-origin-filter.ts`, since the two files are only related by developer discipline, not code linkage.

### Recommendation
Instead of a hardcoded, manually maintained `unsafeHeaders` set, centralize the definition of "sensitive header names" (e.g. export a shared constant used both by `authenticated-image-filter.ts` and `same-origin-filter.ts`), or default to a stricter allowlist-based approach (only forward headers known to be safe) rather than a denylist that must be updated whenever new sensitive headers are introduced. At minimum, add an explicit code comment/test that fails CI whenever a new `Authorization`-like header is added elsewhere without updating this set.

### Proof of Concept
This is a structural/latent-risk analog rather than a directly exploitable PoC given current header usage, since both existing authenticated headers happen to already match `unsafeHeaders`. Concretely demonstrating exploitation would require:
1. Adding (or finding) a header injection point in the main process request pipeline that uses a header name outside `{authentication, authorization, cookie}` (e.g. a hypothetical `X-Desktop-Token`).
2. Getting the target server (a malicious GHES instance, an attacker-controlled avatar/asset host, or a MITM proxy) to respond with a redirect (`3xx` + `Location`) to an attacker-controlled origin.
3. Observing that `onBeforeSendHeaders` forwards the un-denylisted header to the new origin per the logic at `app/src/main-process/same-origin-filter.ts:54-72`, exfiltrating it to the attacker. [5](#0-4)

### Citations

**File:** app/src/main-process/same-origin-filter.ts (L3-19)
```typescript
/**
 * Installs a web request filter to prevent cross domain leaks of auth headers
 *
 * GitHub Desktop uses the fetch[1] web API for all of our API requests. When fetch
 * is used in a browser and it encounters an http redirect to another origin
 * domain CORS policies will apply to prevent submission of credentials[2].
 *
 * In our case however there's no concept of same-origin (and even if there were
 * it'd be problematic because we'd be making cross-origin request constantly to
 * GitHub.com and GHE instances) so the `credentials: same-origin` setting won't
 * help us.
 *
 * This is normally not a problem until http redirects get involved. When making
 * an authenticated request to an API endpoint which in turn issues a redirect
 * to another domain fetch will happily pass along our token to the second
 * domain and there's no way for us to prevent that from happening[3] using
 * the vanilla fetch API.
```

**File:** app/src/main-process/same-origin-filter.ts (L34-77)
```typescript
export function installSameOriginFilter(orderedWebRequest: OrderedWebRequest) {
  // A map between the request ID and the _initial_ request origin
  const requestOrigin = new Map<number, string>()
  const safeProtocols = new Set(['devtools:', 'file:', 'chrome-extension:'])
  const unsafeHeaders = new Set(['authentication', 'authorization', 'cookie'])

  orderedWebRequest.onBeforeRequest.addEventListener(async details => {
    const { protocol, origin } = new URL(details.url)

    // This is called once for the initial request and then once for each
    // "subrequest" thereafter, i.e. a request to https://foo/bar which gets
    // redirected to https://foo/baz will trigger this twice and we only
    // care about capturing the initial request origin
    if (!safeProtocols.has(protocol) && !requestOrigin.has(details.id)) {
      requestOrigin.set(details.id, origin)
    }

    return {}
  })

  orderedWebRequest.onBeforeSendHeaders.addEventListener(async details => {
    const initialOrigin = requestOrigin.get(details.id)
    const { origin } = new URL(details.url)

    if (initialOrigin === undefined || initialOrigin === origin) {
      return { requestHeaders: details.requestHeaders }
    }

    const sanitizedHeaders: Record<string, string> = {}

    for (const [k, v] of Object.entries(details.requestHeaders)) {
      if (!unsafeHeaders.has(k.toLowerCase())) {
        sanitizedHeaders[k] = v
      }
    }

    log.debug(`Sanitizing cross-origin redirect to ${origin}`)
    return { requestHeaders: sanitizedHeaders }
  })

  orderedWebRequest.onCompleted.addEventListener(details =>
    requestOrigin.delete(details.id)
  )
}
```

**File:** app/src/main-process/authenticated-image-filter.ts (L39-44)
```typescript
      return {
        requestHeaders: {
          ...details.requestHeaders,
          Authorization: `token ${token}`,
        },
      }
```
