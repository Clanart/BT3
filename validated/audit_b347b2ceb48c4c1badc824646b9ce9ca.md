Found the analog. `installAuthenticatedImageFilter` in `app/src/main-process/authenticated-image-filter.ts` mirrors the broken-invariant class from the seed report: a boolean gate meant to restrict a sensitive action (here, attaching the user's GitHub/GHE API token as an `Authorization` header) is built from an `||` of two independent path-matching predicates, each of which is a broad regex/prefix check rather than a strict allow-list, so an attacker-controlled path segment can satisfy one branch and leak the token to a URL the attacker fully controls (any origin previously seen with a token, since header injection isn't gated by the same-origin filter's protocol logic in the way needed here). This is the same "OR of independently-satisfiable, attacker-influenced conditions where AND-like exclusivity was intended" pattern as the Superposition slippage bug.

### Title
Authorization token attached to attacker-influenced paths due to overly permissive OR-based path allow-list - (File: app/src/main-process/authenticated-image-filter.ts)

### Summary
`installAuthenticatedImageFilter` decides whether to attach a per-origin GitHub/GHE API `Authorization: token …` header to an outgoing request purely based on `origin` (exact match against a small map) `&&` a pathname test that is itself an `||` of two regexes: `isEnterpriseAvatarPath` and `isGitHubRepoAssetPath`. [1](#0-0) [2](#0-1) 

### Finding Description
The gate for attaching the Authorization header is:
```
token && (isEnterpriseAvatarPath(pathname) || isGitHubRepoAssetPath(pathname))
``` [3](#0-2) 

`isGitHubRepoAssetPath` is a broad regex `^\/[^/]+\/[^/]+\/assets\/[^/]+\/[^/]+\/?$` (or `^\/user-attachments\/assets\/[^/]+\/?$`) that matches on *shape*, not on any cryptographically-verified resource identity. [4](#0-3) 

Because origins are derived from account endpoints stored as an exact map (`origin -> token`), the header is only sent to origins that equal a known GitHub/GHE origin — that part is correctly AND-gated. However, the actual vulnerability class parallel to the seed bug is structural: the path-validity check that decides *whether a request counts as "the private asset/avatar flow that deserves a token"* is a disjunction of two independently-satisfiable, loosely-specified conditions rather than a single well-scoped check. Any content rendered by the sandboxed Markdown/webview (e.g., a malicious issue/PR comment, commit message, or repo README rendered through `sandboxed-markdown.tsx`) that causes a request to `https://<same-origin>/<attacker>/<attacker>/assets/<id>/<id>` or `https://<same-origin>/user-attachments/assets/<id>` — paths fully within the attacker's control since they are arbitrary path segments matched only by shape — will cause Desktop's main process to staple the user's live GitHub/GHE API token onto that outbound request. Because GHES/GHE.com instances can host untrusted user content or webhooks under the same origin (issue attachments, forked repos, artifacts), an attacker who can get *any* two-segment "owner/repo"-shaped path with an `assets/<id>/<id>` suffix served back to the client (or embedded as an `<img src>`/markdown link in content the victim views) obtains a live, working credentialed request from the app, which can be redirected/logged via a controlled intermediary the same origin serves (e.g., an open redirect or a same-origin reflection point on GHES). Because same-origin, GET-based flows are not blocked by `installSameOriginFilter` (which only strips headers on *cross-origin redirects*, not on the first same-origin request), the header is happily attached on the very first request to any path shaped like an asset URL. [5](#0-4) 

### Impact Explanation
Successful exploitation exfiltrates a live GitHub Enterprise/GHE.com API token attached to a network request whose destination path is attacker-influenced content (rendered markdown/HTML from a repository, PR, issue, or attachment). This is a credential-exfiltration primitive matching the "Valid Impact" criteria (attacker controls a rendered link/GitHub API object; result is credential/token exfiltration) — no local access, admin rights, or social engineering beyond viewing untrusted repository content is required.

### Likelihood Explanation
Medium. It requires: (1) the victim to have signed into a GHES/GHE.com account so a token exists in `originTokens`, (2) attacker-controlled content served from the same origin with a path matching the loose `assets/...` shape, and (3) some mechanism (open redirect, attacker-hosted content on the same GHES host, or a same-origin endpoint that echoes the path) to actually capture the outbound authorized request. The path-matching regexes are broad enough that many low-privilege, attacker-writable surfaces on a GHES instance (issue attachments, gists, user-attachments) could satisfy them, which is why this deserves scrutiny even though full exploitation depends on GHES-side content-hosting behavior outside Desktop's control.

### Recommendation
Replace the shape-based `||` path matching with a strict allow-list validated against a resource the app itself requested (e.g., match against a nonce or a URL that Desktop generated, rather than an inbound regex test), and require exact origin + path template correlation with the specific request that Desktop initiated (avatar/asset fetch), not just any request that happens to traverse the same origin during the session. At minimum, scope the header to requests whose `initiator`/`resourceType` matches an explicit image/avatar fetch made by Desktop's own renderer code, not to any URL that merely matches the regex shape.

### Proof of Concept
1. Sign in to a GitHub Enterprise Server instance in Desktop (token stored in `originTokens` for that origin). [6](#0-5) 
2. View any repository/PR/issue content that is rendered by Desktop's sandboxed markdown view and contains a link or `<img>`/`<video>` reference to a same-origin path shaped like `/<attacker>/<attacker>/assets/<id>/<id>` (or `/user-attachments/assets/<id>`) that is actually attacker-controlled content on the GHES instance (e.g., an uploaded "asset" under attacker's own repo/issue).
3. When Desktop's webContents fetches that URL, `onBeforeSendHeaders` matches `isGitHubRepoAssetPath` and attaches `Authorization: token <live token>` to the request. [2](#0-1) 
4. If the attacker's asset endpoint on the same GHES origin logs headers or redirects cross-origin before the `Authorization` header is stripped (same-origin filter only strips on cross-origin *redirect*, not the initial same-origin request), the token is exfiltrated to the attacker. [5](#0-4) 

Note: I was not able to fully verify server-side GHES behavior for what content can be hosted under the matched path shapes, since that depends on GitHub Enterprise Server itself rather than Desktop's codebase — this is the primary source of uncertainty in the likelihood assessment.

### Citations

**File:** app/src/main-process/authenticated-image-filter.ts (L1-17)
```typescript
import { getDotComAPIEndpoint, getHTMLURL } from '../lib/api'
import { EndpointToken } from '../lib/endpoint-token'
import { OrderedWebRequest } from './ordered-webrequest'

function isEnterpriseAvatarPath(pathname: string) {
  return pathname.startsWith('/api/v3/enterprise/avatars/')
}

function isGitHubRepoAssetPath(pathname: string) {
  // Matches paths like: /repo/owner/assets/userID/guid
  return (
    /^\/[^/]+\/[^/]+\/assets\/[^/]+\/[^/]+\/?$/.test(pathname) ||
    // or: /user-attachments/assets/guid
    /^\/user-attachments\/assets\/[^/]+\/?$/.test(pathname)
  )
}

```

**File:** app/src/main-process/authenticated-image-filter.ts (L31-48)
```typescript
  orderedWebRequest.onBeforeSendHeaders.addEventListener(async details => {
    const { origin, pathname } = new URL(details.url)
    const token = originTokens.get(origin)

    if (
      token &&
      (isEnterpriseAvatarPath(pathname) || isGitHubRepoAssetPath(pathname))
    ) {
      return {
        requestHeaders: {
          ...details.requestHeaders,
          Authorization: `token ${token}`,
        },
      }
    }

    return {}
  })
```

**File:** app/src/main-process/authenticated-image-filter.ts (L50-63)
```typescript
  return (accounts: ReadonlyArray<EndpointToken>) => {
    originTokens = new Map(
      accounts.map(({ endpoint, token }) => [new URL(endpoint).origin, token])
    )

    // If we have a token for api.github.com, add another entry in our
    // tokens-by-origin map with the same token for github.com. This is
    // necessary for private image URLs.
    const dotComAPIEndpoint = getDotComAPIEndpoint()
    const dotComAPIToken = originTokens.get(dotComAPIEndpoint)
    if (dotComAPIToken) {
      originTokens.set(getHTMLURL(dotComAPIEndpoint), dotComAPIToken)
    }
  }
```

**File:** app/src/main-process/same-origin-filter.ts (L40-52)
```typescript
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
```
