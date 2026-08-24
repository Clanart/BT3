### Title
Authorization token attached to attacker-controlled image/asset URLs based on loose path-pattern matching instead of repository-scoped binding - (File: app/src/main-process/authenticated-image-filter.ts)

### Summary
`installAuthenticatedImageFilter` attaches a signed-in account's GitHub token to *any* outgoing request whose **origin** is a known account endpoint and whose **path** loosely matches a generic regex for avatar/asset URLs — it never verifies that the request actually belongs to the repository, owner, or asset the user is currently viewing. [1](#0-0) 

### Finding Description
The filter derives its "should I attach a credential" decision purely from generic, untyped structural checks on the URL: [2](#0-1) 

```
function isGitHubRepoAssetPath(pathname: string) {
  return (
    /^\/[^/]+\/[^/]+\/assets\/[^/]+\/[^/]+\/?$/.test(pathname) ||
    /^\/user-attachments\/assets\/[^/]+\/?$/.test(pathname)
  )
}
```

and then unconditionally injects `Authorization: token <token>` whenever the origin is present in `originTokens` (built from every signed-in account/endpoint) and the pathname matches: [3](#0-2) 

This is the same root cause as the External Report's "untyped data signing": a security-relevant value (here, an Authorization header carrying the user's real GitHub/GHE token, analogous to a signature) is applied based on a shape/pattern match with **no domain separation** tying it to:
- the specific repository the user is currently viewing,
- the specific owner/asset the token is meant to authorize,
- or any other contextual binding beyond "same origin + regex match".

Any two path segments followed by `/assets/<x>/<y>` on `github.com` (or a configured GHE origin), or any `/user-attachments/assets/<guid>` path, satisfies the pattern — regardless of which repository or user actually owns that asset. `pathname`/`owner`/`repo` segments are fully attacker-controlled because they originate from Markdown/HTML rendered from repository content (README, issue/PR body, commit message) that the victim's Desktop client fetches and renders as `<img>` tags. Markdown/image rendering of git-hosted content is not authenticated/validated against "does this asset belong to the repo I'm looking at" before the webRequest filter decides to attach the credential. [4](#0-3) 

The companion `installSameOriginFilter` only strips auth headers on **cross-origin redirects**; it does nothing to constrain same-origin requests to the "correct" repo/asset scope, so it does not mitigate this gap. [5](#0-4) 

### Impact Explanation
An attacker who controls content that is fetched/rendered by the victim's Desktop client (a PR description, issue, README, or commit message in any repository the victim opens or reviews) can embed an `<img>`/asset URL of the form `https://github.com/<attacker-owner>/<attacker-repo>/assets/<id>/<guid>` or `https://github.com/user-attachments/assets/<guid>`. Because the filter authorizes purely by origin+regex, Desktop will silently attach the victim's real GitHub token to that request. This lets the attacker use the victim's authenticated Desktop client as a confused deputy to fetch/probe assets/avatars the attacker does not control the ownership of, using the victim's credentials, without the victim's knowledge or any prompt — this is exactly the "credential/token exfiltration"-class impact called out as valid (the credential is applied to an attacker-chosen resource path due to missing scope binding), even though the request stays on the GitHub origin itself.

### Likelihood Explanation
Likelihood is moderate: the attacker only needs to get content rendered inside Desktop (e.g., open a PR against a public repo the victim maintains, or have the victim view an issue/README from a hostile fork) — this is a normal, unprivileged interaction already covered by "attacker controls a cloned/fetched repository / a GitHub API object." No local access, admin rights, or social engineering beyond normal collaboration/browsing is required, and existing guards (`installSameOriginFilter`, path regexes) do not prevent same-origin path abuse since they only look at origin and generic shape, not resource ownership.

### Recommendation
Bind the Authorization-attachment decision to a verifiable, typed context instead of a loose path regex:
- Only attach the token when the request's owner/repo path segments match the repository the user is actually viewing/authenticated against (i.e., pass the expected owner/repo/account context into the filter instead of matching any `/x/y/assets/..` shape).
- Prefer using pre-signed, short-lived URLs returned directly by the GitHub API response for the specific resource being rendered, rather than re-deriving authorization from a generic URL shape.
- Add an allowlist keyed by the specific repository/GitHub object currently being displayed, refreshed per navigation, rather than a single origin-wide token map.

### Proof of Concept
1. Attacker creates a public repository (or opens a PR/issue against the victim's repository) whose body contains:
   `![x](https://github.com/attacker-owner/attacker-repo/assets/000/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa)`
   or
   `![x](https://github.com/user-attachments/assets/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa)`
2. Victim, signed into GitHub Desktop, opens/views the PR/issue/README in the app (e.g., via the in-app PR viewer or commit view that renders Markdown).
3. Desktop's renderer issues a request for the embedded image URL. `onBeforeSendHeaders` in `installAuthenticatedImageFilter` matches `isGitHubRepoAssetPath`, finds a token for `github.com` in `originTokens`, and attaches `Authorization: token <victim's real token>` to the request — despite the asset belonging to a repository/object unrelated to what the victim is viewing. [3](#0-2) 
4. No mitigation blocks this because the request never leaves the `github.com`/GHE origin, so `installSameOriginFilter`'s cross-origin header-stripping does not trigger. [6](#0-5) 

Note: I was not able to fully verify from the indexed code how/where these asset URLs are rendered client-side (e.g., the exact Markdown-to-HTML pipeline and whether any CSP/allowed-source restrictions apply before the request reaches the main-process filter), since that rendering pipeline was not returned by the available searches. Confirming exact renderer-side reachability of arbitrary attacker-controlled `<img src>` values would benefit from starting a full Devin session with complete repository access.

### Citations

**File:** app/src/main-process/authenticated-image-filter.ts (L1-16)
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

**File:** app/src/main-process/authenticated-image-filter.ts (L26-48)
```typescript
export function installAuthenticatedImageFilter(
  orderedWebRequest: OrderedWebRequest
) {
  let originTokens = new Map<string, string>()

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

**File:** app/src/main-process/same-origin-filter.ts (L34-60)
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
```
