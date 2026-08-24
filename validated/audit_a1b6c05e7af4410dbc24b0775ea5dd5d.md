## Title
`installAuthenticatedImageFilter` attaches the user's GitHub token to *any* matching asset URL requested by the renderer regardless of which repository it belongs to - (File: `app/src/main-process/authenticated-image-filter.ts`)

### Summary
The Tapioca `USD0.flashLoan()` bug boils down to a broken invariant: the code checks an allowance/authorization that is scoped to the *wrong* principal (`address(this)` instead of the actual caller), so any attacker-chosen `receiver` gets treated as implicitly consenting. The same "authorize by loose pattern-match instead of verifying the true relationship between requester and resource" pattern exists in GitHub Desktop's `installAuthenticatedImageFilter`, which decides whether to attach a user's OAuth/PAT `Authorization` header to a network request purely by checking the request's **origin** and a **regex on the URL path** - not by verifying that the resource actually belongs to content the user explicitly opened, or that the current webContents/document is the one Desktop itself generated.

### Finding Description
`installAuthenticatedImageFilter` hooks `onBeforeSendHeaders` for the entire session and injects `Authorization: token <token>` on every outgoing request whose origin matches a signed-in account's endpoint origin and whose path matches `isEnterpriseAvatarPath` or `isGitHubRepoAssetPath`: [1](#0-0) 

The path matchers are broad, generic patterns (`/^\/[^/]+\/[^/]+\/assets\/[^/]+\/[^/]+\/?$/` for any `owner/repo/assets/...` and `/^\/user-attachments\/assets\/[^/]+\/?$/`), with no check that:
- the asset path corresponds to the repository currently open in Desktop,
- the request was initiated by Desktop's own UI rather than by rendering attacker-supplied markdown/HTML (e.g., a PR description, issue body, or commit comment fetched from the GitHub API),
- the account whose token gets attached is even related to the repo referenced in the path. [2](#0-1) 

This is installed globally for the session in `main.ts`, so it applies to every `webContents` in the app, not just a specific trusted document: [3](#0-2) 

Compare this to `installSameOriginFilter`, which exists specifically to prevent auth headers from leaking to *unintended* origins on redirects - showing the project is aware that credential attachment must be scoped tightly to the intended target, yet `installAuthenticatedImageFilter` only scopes by origin+path-shape, not by the actual owning repository/context: [4](#0-3) 

### Impact Explanation
Because the check is "does this URL merely look like an avatar/asset path on a known GitHub origin", any GitHub-hosted content that Desktop renders (PR/issue bodies, commit messages, README previews, etc. - all of which are attacker-controlled GitHub API objects when viewing PRs/issues from other users) can embed an `<img>` tag pointing at `/user-attachments/assets/<guid>` or `/<owner>/<repo>/assets/<id>/<guid>` for **a different, private repository** that the signed-in user's token happens to have read access to. Desktop will silently attach the user's real GitHub token to that request, causing an unintended, non-interactive authenticated fetch of a private asset the attacker could not otherwise access, purely by getting the victim to view a page containing the crafted image reference. This mirrors the flash-loan bug's core flaw: authorization was granted based on matching the wrong entity/scope instead of confirming that the resource genuinely belongs to the context that is supposed to authorize the action.

### Likelihood Explanation
Exploitation only requires: (1) the victim has previously signed in to GitHub/GHES in Desktop, and (2) the victim views GitHub content (a PR, issue, comment) that Desktop renders, containing a crafted `<img>` URL under the matched path patterns. No local access, no malware, and no explicit user "click to open a private asset" is needed - the token attachment happens transparently in `onBeforeSendHeaders` for any qualifying request the renderer issues.

### Recommendation
Scope the token attachment to the exact asset/avatar URLs that Desktop itself constructed for the currently relevant repository/account (e.g., pass an explicit allow-list of expected URLs per rendered view) rather than matching on origin + generic path shape. At minimum, bind the check to the specific repository context (owner/repo) the currently displayed content belongs to, analogous to how `installSameOriginFilter` binds header stripping to the original request's origin rather than a loosely-matched destination.

### Proof of Concept
1. Victim signs into GitHub Enterprise/GitHub.com in Desktop with a token that has read access to a private repo `victim-org/secret-repo`.
2. Attacker opens a PR or issue in a public repo the victim also views in Desktop, embedding in the body:
   `![x](https://ghes.example.com/victim-org/secret-repo/assets/1/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee)`
3. When Desktop renders/previews this content, the renderer issues a GET for that image URL.
4. `installAuthenticatedImageFilter`'s `onBeforeSendHeaders` handler matches origin (`ghes.example.com`) plus `isGitHubRepoAssetPath`, and attaches `Authorization: token <victim's token>` regardless of the fact the asset belongs to an unrelated repository the attacker chose.
5. The private asset is fetched using the victim's credentials without any explicit consent for that specific resource, purely as a side effect of viewing attacker-controlled content.

Note: I could not fully verify within the available index whether Desktop's markdown/HTML renderer sanitizes or strips raw `<img>` tags before rendering PR/issue bodies (this would determine whether step 2-3 is directly reachable via markdown or requires another rendering surface such as commit comments/notifications). This should be confirmed against the markdown rendering pipeline before treating this as fully exploitable; if markdown sanitization already blocks arbitrary `<img src>` to attacker-chosen GitHub paths, the core design flaw in `authenticated-image-filter.ts` (over-broad path/origin matching instead of context-bound scoping) still stands as a latent gap.

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

**File:** app/src/main-process/main.ts (L337-350)
```typescript
  const orderedWebRequest = new OrderedWebRequest(
    session.defaultSession.webRequest
  )

  // Ensures auth-related headers won't traverse http redirects to hosts
  // on different origins than the originating request.
  installSameOriginFilter(orderedWebRequest)

  // Ensures Alive websocket sessions are initiated with an acceptable Origin
  installAliveOriginFilter(orderedWebRequest)

  // Adds an authorization header for requests of avatars on GHES and private
  // repo assets
  const updateAccounts = installAuthenticatedImageFilter(orderedWebRequest)
```

**File:** app/src/main-process/same-origin-filter.ts (L1-33)
```typescript
import { OrderedWebRequest } from './ordered-webrequest'

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
 *
 * That's the reason why this filter exists. It will look at all initiated
 * requests and store their origin along with their request ID. The request id
 * will be the same for any subsequent redirect requests but the urls will be
 * changing. Upon each request we will check to see if we've seen the request
 * id before and if so if the origin matches. If the origin doesn't match we'll
 * strip some potentially dangerous headers from the redirect request.
 *
 * 1. https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API
 * 2. https://fetch.spec.whatwg.org/#http-network-or-cache-fetch
 * 3. https://github.com/whatwg/fetch/issues/763
 *
 * @param orderedWebRequest
 */
```
