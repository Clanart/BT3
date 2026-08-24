## Finding: Attacker-Controlled `WWW-Authenticate` Header Spoofs GitHub Host Trust Determination

The reported smart-contract bug is a case of an **ambiguous/boundary condition being silently folded into one branch of a binary decision, granting an unearned advantage** (price-unchanged treated as "down wins" instead of a neutral third case). The structural analog in GitHub Desktop is the credential-helper's `getEndpointKind` heuristic, which folds an attacker-influenceable signal into the "trusted GitHub host" branch without a neutral/unverified state.

### Title
Trampoline credential helper trusts attacker-controlled `WWW-Authenticate: realm="GitHub"` header to classify arbitrary git remotes as GitHub Enterprise - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`getEndpointKind()` decides whether a git remote should be treated as `github.com`, `ghe.com`, `enterprise`, or `generic`. When none of the deterministic checks (known dotcom/GHE hostnames, existing stored account) match, it falls back to inspecting the `WWW-Authenticate` header that git forwarded from the remote server's HTTP response, treating a `realm="GitHub"` value as proof of a GitHub Enterprise host: [1](#0-0) 

This header is fully attacker-controlled: it originates from whatever HTTP response the remote (or a MITM proxy sitting on the connection) sends back when git attempts to fetch/push, exactly the "attacker controls ... a git remote/proxy response" category of valid impact.

### Finding Description
Like the `UpDown.sol` bug, which collapsed an indeterminate case ("price unchanged") into the "down" branch instead of a distinct neutral outcome, `getEndpointKind` collapses an indeterminate/unverifiable signal (a self-reported HTTP header from an untrusted server) into the "this is a real GitHub Enterprise host" branch: [2](#0-1) 

There is no cryptographic or out-of-band verification that the responding server is actually a GitHub Enterprise instance — the classification is based purely on a string the server chose to send. Downstream, `getCredential()` uses this classification to decide whether to invoke `ui.promptForGitHubSignIn(endpoint)`, i.e., Desktop's native "Sign in to GitHub Enterprise" UI, for an arbitrary attacker-controlled `endpoint`: [3](#0-2) 

Because `endpointKind !== 'generic'` short-circuits the generic/external-credential-helper path entirely (lines 127-134), a malicious remote can force Desktop out of the normal "hand off to system credential manager" flow and into its own trusted GitHub sign-in UI, using nothing but a spoofed response header — no valid TLS cert pinning or endpoint allow-list is consulted at this branch point, unlike the `isDotCom`/`isGHE` checks earlier in the same function which rely on fixed hostnames: [4](#0-3) 

### Impact Explanation
A user who adds/clones from a malicious or compromised remote (or is MITM'd on an unauthenticated `http://` transport before the protocol check trips — the protocol short-circuit only rejects non-`https:` URLs, so an attacker-controlled `https` endpoint with a self-signed-but-accepted or otherwise reachable server still reaches the header-based branch) can make GitHub Desktop present its native "GitHub Enterprise sign-in" dialog for the attacker's endpoint. This blurs the trust boundary the report's "third case / refund" recommendation is meant to preserve: instead of falling into a neutral "generic/unknown" bucket that defers to the plain external credential helper, the attacker-influenced signal pushes the flow into Desktop's own privileged GitHub authentication UX, which is otherwise reserved for verified dotcom/GHE endpoints.

### Likelihood Explanation
Triggering this requires nothing more than the user performing an ordinary git operation (fetch/push/clone) against a remote under attacker control, or a proxy/MITM able to inject a response header — this satisfies the "attacker controls ... a git remote/proxy response" valid-impact category, with no local access, admin rights, or pre-existing malware needed.

### Recommendation
Do not classify a remote as `enterprise`/trusted based solely on the self-reported `WWW-Authenticate` realm string. As with the report's recommendation to add an explicit third/neutral case rather than silently favoring one branch, `getEndpointKind` should treat an unverifiable `WWW-Authenticate` hint as, at most, a weak signal that still requires the standard `isGitHubHost()` network probe (which validates via the `x-github-request-id` response header over a real request) before granting the `enterprise` classification, or should route such ambiguous cases to the `generic` credential path by default.

### Proof of Concept
1. Stand up an HTTPS server that responds to git's credential probe with `WWW-Authenticate: Basic realm="GitHub"`.
2. In GitHub Desktop, clone or fetch from that server's URL as a remote.
3. Observe `getEndpointKind` returning `'enterprise'` at [5](#0-4)  purely from the spoofed header, with no prior account for that endpoint.
4. Observe Desktop invoke `ui.promptForGitHubSignIn(endpoint)` for the attacker's endpoint at [6](#0-5)  instead of falling back to the generic/external credential helper path a truly-unknown host would take.

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L153-165)
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

**File:** app/src/lib/endpoint-capabilities.ts (L47-62)
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
```
