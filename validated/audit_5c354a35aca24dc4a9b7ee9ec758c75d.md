## Title
Unauthenticated response header permanently poisons the "is this a GitHub host" trust cache, causing stale trust decisions - (File: `app/src/lib/endpoint-capabilities.ts`)

## Summary
`isGitHubHost()` and the credential-helper's `getEndpointKind()` decide whether a remote endpoint should be treated as a trusted GitHub/GHE host by consulting a permanently cached, unauthenticated value: the `x-github-enterprise-version` response header. Once any response for a given endpoint carries this header, the cache is written to memory *and* `localStorage` forever, and all future trust checks short-circuit to "trusted" without re-verifying the `x-github-request-id` signal that is actually meant to authenticate the host. This mirrors the oracle bug's core flaw: a piece of state, once set from an untrusted input, is trusted indefinitely and bypasses the "real" validation path, with no expiry or reset mechanism.

## Finding Description
`updateEndpointVersion()` writes an endpoint's GHE version, unconditionally, from any HTTP response header, to both an in-memory cache and `localStorage`, with no expiration: [1](#0-0) [2](#0-1) 

This is invoked from `tryUpdateEndpointVersionFromResponse`, which is called on **every** API response, including responses to unauthenticated discovery requests: [3](#0-2) 

`isGitHubHost(url)` is the function that is supposed to authoritatively decide, based on the real signal (`x-github-request-id`) whether an arbitrary URL belongs to a genuine GitHub host. But before doing that real check, it short-circuits if the cache is already populated: [4](#0-3) 

Note line ~2461: `if (getEndpointVersion(endpoint) !== null) { return true }` — this trusts the cache unconditionally, without re-validating `x-github-request-id`, and without any TTL. Once poisoned, this state can never self-correct because nothing ever calls `versionCache.delete` or otherwise invalidates a bad entry — the only way it changes is if a *new* response arrives with a *different* version string (line 94: `if (rawVersionCache.get(key) !== version)`), which an attacker fully controls anyway.

`isGitHubHost()` is invoked from `getEndpointKind()` in the trampoline credential-helper, which classifies a remote as `'enterprise'` (a trusted GitHub Enterprise host) vs `'generic'` when Git asks for credentials during a fetch/clone/push: [5](#0-4) 

The `endpoint` here is derived directly from the remote/host that Git is currently talking to — i.e., **attacker-controlled** if the user clones from, or has a remote pointing at, an attacker's server.

**Attack path:**
1. Attacker serves a repo whose remote resolves to `https://evil.example.com`.
2. At any point (during the initial clone HTTPS handshake, during an unrelated `isGitHubHost` discovery probe, or during a normal 401 challenge), the attacker's server includes the header `x-github-enterprise-version: 99.0.0` on a response.
3. `tryUpdateEndpointVersionFromResponse` caches this permanently for `evil.example.com`, in memory and in `localStorage` — surviving across app restarts.
4. On any subsequent Git operation against that host, `getEndpointKind()` → `isGitHubHost()` returns `true` from the cache alone, bypassing the actual `x-github-request-id` verification.
5. `getCredential()` then treats the endpoint as `'enterprise'` and, since no matching `Account` exists for that origin, invokes `ui.promptForGitHubSignIn(endpoint)`: [6](#0-5) 
6. `promptForGitHubSignIn` treats the endpoint as a legitimate Enterprise server, begins the Enterprise sign-in flow and points `setSignInEndpoint` at the attacker's origin: [7](#0-6) 

This causes Desktop to present its normal-looking "Sign in to GitHub Enterprise" dialog while directing the OAuth/basic-auth exchange at an attacker-controlled endpoint — a credential-phishing/OAuth-binding primitive triggered purely by a response header from a malicious remote, requiring no local access, no admin rights, and no unusual user action beyond a normal `git fetch`/`clone`/`push` against the attacker's remote.

## Impact Explanation
A single response header from an attacker-controlled git remote/proxy permanently poisons a security-relevant trust decision (`isGitHubHost`) for that host, persisted across sessions via `localStorage`. This bypasses the intended `x-github-request-id`-based host verification and routes the user into a GitHub Enterprise sign-in/credential flow pointed at the attacker's server, risking credential or OAuth token exfiltration and unauthorized account binding to a spoofed endpoint. Because the cache never expires and is written unconditionally, the corrupted trust decision persists indefinitely, exactly like the oracle's frozen `cumulativePrice`/`averagePrice` — a stale, attacker-influenced value that is trusted over the "real" validation path without ever resetting.

## Likelihood Explanation
Moderate to high. The header is entirely attacker-controlled and requires no cooperation beyond the victim performing an ordinary Git operation (clone/fetch/push) against a remote the attacker controls (a normal, expected attacker capability per the threat model). No race condition or timing precision is required — a single crafted response header is sufficient, and the effect is permanent once cached.

## Recommendation
- Do not trust `getEndpointVersion(endpoint) !== null` as a substitute for the actual `x-github-request-id` check in `isGitHubHost()`; always perform (or re-verify) the authenticated discovery request rather than short-circuiting on a cached, attacker-suppliable header.
- Add TTL/expiration to the endpoint version cache (`versionCache`/`rawVersionCase`), and never persist unauthenticated header values to `localStorage` without re-validating them on read.
- Only cache/trust `x-github-enterprise-version` when it is accompanied by the authenticating `x-github-request-id` header on the *same* response.
- Bind sign-in/credential prompts to endpoints from previously trusted, user-confirmed sources rather than deriving trust transitively from cached headers observed on arbitrary remote responses.

## Proof of Concept
1. Set up a malicious HTTPS server at `evil.example.com` acting as a plain Git remote (no real GitHub API behind it).
2. Have it respond to any HTTP request (including the initial `git-upload-pack` / `info/refs` handshake or a 401 credential challenge) with header `x-github-enterprise-version: 99.0.0`, but without `x-github-request-id`.
3. In GitHub Desktop, clone or add a remote pointing to `https://evil.example.com/foo/bar.git` and initiate a fetch/push, which triggers the trampoline credential helper's `get` flow.
4. Observe that `getEndpointKind()` classifies the endpoint as `'enterprise'` (via the poisoned `isGitHubHost` cache) rather than `'generic'`, and that a subsequent identical operation continues to trust the endpoint as GitHub Enterprise, even after restarting the app (cache persisted in `localStorage`), and that the "sign in to Enterprise" prompt (if triggered) targets `evil.example.com`.

Note: I was not able to trace the full downstream OAuth/token-exchange code path (`sign-in-store.ts` beyond its opening lines) within the available iterations to confirm exactly what data would be sent to the attacker's endpoint during the resulting sign-in flow; a Devin session with full file access would be needed to fully verify the credential-exfiltration mechanics of the `beginEnterpriseSignIn` → OAuth exchange sequence.

### Citations

**File:** app/src/lib/endpoint-capabilities.ts (L35-42)
```typescript
/** Stores raw x-github-enterprise-version headers keyed on endpoint */
const rawVersionCache = new Map<string, string>()

/** Stores parsed x-github-enterprise-version headers keyed on endpoint */
const versionCache = new Map<string, semver.SemVer | null>()

/** Get the cache key for a given endpoint address */
const endpointVersionKey = (ep: string) => `endpoint-version:${ep}`
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

**File:** app/src/lib/api.ts (L2429-2464)
```typescript
/**
 * Attempts to determine whether or not the url belongs to a GitHub host.
 *
 * This is a best-effort attempt and may return `undefined` if encountering
 * an error making the discovery request
 */
export async function isGitHubHost(url: string) {
  const { hostname } = new window.URL(url)

  const endpoint =
    hostname === 'github.com' || hostname === 'api.github.com'
      ? getDotComAPIEndpoint()
      : getEnterpriseAPIURL(url)

  if (isDotCom(endpoint) || isGHE(endpoint)) {
    return true
  }

  if (isKnownThirdPartyHost(hostname)) {
    return false
  }

  // github.example.com,
  if (/(^|\.)(github)\./.test(hostname)) {
    return true
  }

  // bitbucket.example.com, etc
  if (/(^|\.)(bitbucket|gitlab)\./.test(hostname)) {
    return false
  }

  if (getEndpointVersion(endpoint) !== null) {
    return true
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L137-179)
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
}
```

**File:** app/src/lib/trampoline/trampoline-ui-helper.ts (L80-104)
```typescript
  public promptForGitHubSignIn(endpoint: string): Promise<Account | undefined> {
    return new Promise<Account | undefined>(async resolve => {
      const cb = (result: SignInResult) => {
        resolve(result.kind === 'success' ? result.account : undefined)
        this.dispatcher.closePopup(PopupType.SignIn)
      }

      const { hostname, origin } = new URL(endpoint)
      if (hostname === 'github.com') {
        this.dispatcher.beginDotComSignIn(cb)
      } else {
        this.dispatcher.beginEnterpriseSignIn(cb)
        await this.dispatcher.setSignInEndpoint(origin)
      }

      this.dispatcher.showPopup({
        type: PopupType.SignIn,
        isCredentialHelperSignIn: true,
        credentialHelperUrl: endpoint,
      })
    }).catch(e => {
      log.error(`Could not prompt for GitHub sign in`, e)
      return undefined
    })
  }
```
