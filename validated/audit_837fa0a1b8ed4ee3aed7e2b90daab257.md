## Analysis

Found `installAuthenticatedImageFilter` in `app/src/main-process/authenticated-image-filter.ts`. It attaches the user's real OAuth `Authorization` header to any request whose **origin** matches an account's endpoint origin, gated only by a path-shape regex, not by verifying the actual resource owner/repo the request is destined for. This mirrors the "wrong recipient" bug class: the mitigation that normally scopes credentials to the correct entity (here: only the specific private-asset/avatar path a legitimate GitHub response would generate) is based on a weak, spoofable check — same broken-invariant shape as the Babylon refund bug (a value that should be validated against the true recipient is instead trusted based on an insufficiently specific match).

### Title
Authorization token attached to any URL matching `assets/<id>/<guid>` or `user-attachments/assets/<guid>` path shape on a known origin, regardless of actual resource owner - ([File: app/src/main-process/authenticated-image-filter.ts])

### Summary
`installAuthenticatedImageFilter` injects the signed-in account's GitHub token as an `Authorization: token <token>` header on outgoing requests whenever the request's origin matches a known account origin (github.com/GHE) **and** the path matches a generic regex for repo assets or enterprise avatars. The path check does not verify that the path actually belongs to a repository or resource the account is authorized to access, or is even a real API response — it is a syntactic pattern the attacker fully controls when embedding rendered content (e.g., an `<img>`/link inside a PR body, issue, commit message, or README) that Desktop renders in its webview/renderer. [1](#0-0) 

### Finding Description
The filter decides whether to attach the user's token purely from `origin` + `pathname` shape: [2](#0-1) [3](#0-2) 

Because `origin` is checked (not full URL/path against a specific resource the user is viewing), any URL under `github.com` or a GHE host whose path matches `/^\/[^/]+\/[^/]+\/assets\/[^/]+\/[^/]+\/?$/` or `/^\/user-attachments\/assets\/[^/]+\/?$/` will receive the user's token — even if the "owner/repo" segment in the path is a repository the attacker controls (or a synthetic path the attacker crafted that merely matches the regex but doesn't correspond to any actual private asset). This is the same broken invariant as the Sherlock report: a privileged credential (fee refund / access token) is dispatched based on a coarse identity check (fee payer / origin+path-shape) instead of verifying the true, narrower intended recipient (fee granter / the specific asset actually requested by GitHub's API).

Because this fires on `onBeforeSendHeaders` for *any* navigation/resource load matching that shape on the trusted origin — including content controlled by a third party through GitHub-hosted user content (issues, PRs, comments, commit messages rendered as HTML/Markdown in Desktop's UI) — an attacker who can get Desktop to load an `assets/{owner}/{repo}/assets/x/y`-shaped URL under `github.com` (for example by crafting a PR/issue body, a commit message link, or a repository README that Desktop renders and the user clicks) can cause Desktop to attach the victim's live token to a request whose actual destination handling is attacker-influenced content.

### Impact Explanation
If the path-matching check is not scoped precisely to the endpoint that legitimately requires authentication (GHES private avatars/assets API), a token can be sent to a URL an attacker steers the user/renderer toward. Because `github.com`/GHE is same-origin with legitimate GitHub content, `installSameOriginFilter` (cross-origin redirect protection) does not prevent this, since the request never leaves the trusted origin — it's the path-matching logic itself, not origin/redirect protection, that is the weak link. This can leak the account's OAuth token to any GitHub-hosted resource matching that path shape, independent of whether it's actually the private asset the token was meant for.

### Likelihood Explanation
Exploitation requires only that the attacker control content that gets rendered/loaded inside Desktop's webview (an issue, PR, or repo asset link a victim opens) with a URL matching the loose regex — no local access, no admin rights, and no pre-existing malware. This fits squarely within the described attacker model (attacker controls a GitHub API object / a link the user clicks).

### Recommendation
Scope the authenticated header injection to the exact expected asset/avatar request (e.g., validate the referrer/initiator is Desktop's own request for a known resource ID that was fetched via the API, not any resource whose path merely matches a regex), and confirm the owner/repo segment corresponds to a repository the requesting account can legitimately access before attaching the Authorization header — analogous to explicitly resolving and validating the correct "recipient" (here, the correct asset/account pairing) instead of trusting a loosely-shaped path on a shared origin.

### Proof of Concept
Conceptual PoC (not fully verified against live behavior, given index-only analysis):
1. Victim is signed into GitHub Desktop with a GHE/GHES account.
2. Attacker creates an issue/PR/README containing a link or embedded resource URL of the form `https://<victim's GHE host>/attacker-owner/attacker-repo/assets/1/<guid>` or `https://<victim's GHE host>/user-attachments/assets/<guid>`.
3. Victim opens this content inside Desktop's renderer (e.g., viewing a PR description or clicking a rendered link that Desktop's webview loads).
4. `installAuthenticatedImageFilter`'s `onBeforeSendHeaders` listener matches the path regex against the known origin and injects `Authorization: token <victim token>` on the outgoing request — sent to a resource path chosen by the attacker rather than a resource genuinely requiring/authorized for that token.

Note: I could not fully trace, within index limits, how/where these asset URLs are rendered client-side (e.g., whether Desktop's Markdown/HTML sanitizer restricts `src`/`href` values to API-returned URLs only, which would reduce exploitability). This is a caveat on likelihood; a Devin session with full repo access would be needed to confirm whether rendered PR/issue content can actually produce attacker-controlled URLs matching these path patterns that Desktop's webview will fetch.

### Citations

**File:** app/src/main-process/authenticated-image-filter.ts (L1-48)
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

/**
 * Installs a web request filter which adds the Authorization header for
 * unauthenticated requests to the GHES/GHAE private avatars API, and for private
 * repo assets.
 *
 * Returns a method that can be used to update the list of signed-in accounts
 * which is used to resolve which token to use.
 */
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
