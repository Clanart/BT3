## Finding

### Title
Trampoline credential helper trusts an attacker-controlled `WWW-Authenticate` header to classify a remote as GitHub Enterprise, triggering an unverified sign-in/credential flow - ([File: app/src/lib/trampoline/trampoline-credential-helper.ts])

### Summary
`getEndpointKind()` in the Git credential-helper trampoline decides whether a remote host should be treated as a GitHub/GitHub Enterprise endpoint. One of its "happy path" checks trusts the literal contents of the `WWW-Authenticate` header that Git forwards from the remote server, without any independent verification, before ever reaching the network-based `isGitHubHost()` probe. Any git server (or MITM/plaintext proxy) an attacker controls can therefore self-declare `realm="GitHub"` and be classified as an `'enterprise'` endpoint, causing GitHub Desktop to prompt the user to sign in to that arbitrary attacker-supplied URL as though it were a trusted GitHub Enterprise instance.

### Finding Description
When Git needs credentials for a remote, it invokes the credential helper and forwards any `WWW-Authenticate` headers it received as `wwwauth[n]=...` parameters. `getEndpointKind()` reads these attacker-supplied values directly: [1](#0-0) 

This check runs **before** the protocol sanity check and before `isGitHubHost()` — the function that actually performs a network probe and validates the `x-github-request-id` response header as a trust signal: [2](#0-1) 

The `isGitHubHost()` verification exists precisely because hostname/header heuristics are not trustworthy on their own — it performs an out-of-band HEAD request and checks for a header the server can't easily forge in the context Desktop trusts (`x-github-request-id`) as seen in `app/src/lib/api.ts`: [3](#0-2) 

However, the `wwwauth[...]` shortcut in `getEndpointKind` bypasses this verification entirely: it returns `'enterprise'` purely because the attacker-controlled server chose to send `WWW-Authenticate: Basic realm="GitHub"` in its 401 response, with no cross-check against `isGitHubHost()`, no TLS/host validation, and — critically — this check happens *before* the `credentialUrl.protocol !== 'https:'` guard that would otherwise exclude non-HTTPS hosts from being considered GitHub-like.

Once classified as non-`generic`, `getCredential()` uses that classification to decide whether to prompt the user for a GitHub sign-in against the attacker's endpoint: [4](#0-3) 

This mirrors the structure of the original report exactly: a value derived from untrusted/attacker-influenceable input (there: a rounded-to-zero debt conversion; here: an attacker-chosen header string) is consumed directly by a security-relevant classification (there: "is debt effectively zero"; here: "is this host GitHub-trustworthy") without the guard that was supposed to enforce correctness (there: checking pending debt in the debt token; here: the `isGitHubHost()` network probe), letting the attacker force the privileged branch of the logic.

### Impact Explanation
Any repository whose remote the user fetches/pushes/clones from — including a plain HTTP remote, a spoofed proxy response, or a malicious server on a compromised network — can trigger the "enterprise" classification and elicit `ui.promptForGitHubSignIn(endpoint)` for a URL fully controlled by the attacker. If the resulting sign-in flow subsequently binds a real GitHub token/account record to that attacker-controlled endpoint (as is typical for GitHub Desktop's "add enterprise account" flow, which stores accounts keyed by the endpoint URL used during sign-in), every future authenticated API call made through that `Account` (e.g., via `API.fromAccount`) would send the user's OAuth/PAT token to the attacker's server — this is a credential/token exfiltration path, and at minimum it is an unwanted authentication prompt bound to an unverified endpoint (unauthorized account binding), both of which are explicitly in-scope impact categories.

### Likelihood Explanation
The precondition is simply that the user interacts with an attacker-controlled or attacker-influenced git remote (adding it, fetching it, or being MITM'd on an insecure network) — no local access, no admin rights, and no prior compromise are required. Setting a custom `WWW-Authenticate` header on a 401 response is trivial for any HTTP(S) server operator to do, and Git forwards this header to the credential helper as part of normal credential-helper protocol behavior, so the attacker primitive is fully realistic and requires no unnatural user steps beyond normal git operations.

### Recommendation
Remove the `wwwauth[...]` realm shortcut, or at minimum require it to be corroborated by the same `isGitHubHost()` network verification (checking `x-github-request-id`) used for the fallback path before classifying an endpoint as `'enterprise'`. Also move the HTTPS-only requirement (`credentialUrl.protocol !== 'https:'`) ahead of any heuristic-based classification so that plaintext HTTP hosts can never be treated as GitHub-trustworthy regardless of header content.

### Proof of Concept
1. Attacker stands up an HTTP(S) server that returns `401` with header `WWW-Authenticate: Basic realm="GitHub"` for any Git smart-HTTP credential negotiation request.
2. Victim adds this server as a remote in GitHub Desktop and performs a fetch/pull/push (e.g., `git fetch` triggered through the UI).
3. Git captures the `WWW-Authenticate` header and forwards it to the trampoline credential helper as `wwwauth[0]=Basic realm="GitHub"`.
4. `getEndpointKind()` returns `'enterprise'` at `app/src/lib/trampoline/trampoline-credential-helper.ts:157-163` without calling `isGitHubHost()`.
5. `getCredential()` at lines 107-125 finds no existing account for the attacker's endpoint and calls `ui.promptForGitHubSignIn(endpoint)`, presenting the user a GitHub sign-in prompt scoped to the attacker's URL.

Note: full confirmation that the resulting sign-in flow persists an `Account` bound to the attacker endpoint (and that subsequent API calls would leak the token to that endpoint) requires tracing `trampoline-ui-helper.ts` and the enterprise sign-in dialog implementation, which were not available within the current investigation; this should be verified in a follow-up before treating the exfiltration impact as fully confirmed rather than "prompt for sign-in against an unverified host" (already a concrete authentication-boundary violation on its own).

### Citations

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L152-166)
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

**File:** app/src/lib/api.ts (L2429-2454)
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
```
