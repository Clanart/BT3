Now I found a solid analog. Look closely at `installAuthenticatedImageFilter`:

```
app/src/main-process/authenticated-image-filter.ts
31|  orderedWebRequest.onBeforeSendHeaders.addEventListener(async details => {
32|    const { origin, pathname } = new URL(details.url)
33|    const token = originTokens.get(origin)
34|
35|    if (
36|      token &&
37|      (isEnterpriseAvatarPath(pathname) || isGitHubRepoAssetPath(pathname))
38|    ) {
39|      return {
40|        requestHeaders: {
41|          ...details.requestHeaders,
42|          Authorization: `token ${token}`,
43|        },
44|      }
45|    }
```

This handler only checks `origin` and a **path shape regex** (`isGitHubRepoAssetPath`), never the repository owner/name embedded in that path, before attaching the signed-in user's real API token as an `Authorization` header. That's exactly the same bug class as `SecuritizeSwap::buy`: the code verifies a *claim* (the URL "looks like" a repo-asset path) instead of verifying the actual authorization relationship (that the requesting content belongs to a repo/account the user is entitled to authenticate against), then performs a privileged action (attaching the OAuth token) on behalf of the user without confirming the true target.

### Title
Authenticated-image webRequest filter attaches the signed-in user's GitHub token to any URL that merely matches a path-shape regex, not the account's own asset - (File: `app/src/main-process/authenticated-image-filter.ts`)

### Summary
`installAuthenticatedImageFilter` [1](#0-0)  is a `webRequest.onBeforeSendHeaders` interceptor that adds `Authorization: token <user's real OAuth token>` to any outgoing request whose `origin` matches a known GitHub endpoint and whose `pathname` matches one of two permissive regexes for "avatar" or "repo asset" URLs. It never checks that the path actually belongs to a repository the signed-in account is expected to interact with, nor does it check who initiated the request. Any renderer content that can cause Desktop to load an image/URL under `github.com` or the enterprise host (e.g. a `<img>`/markdown-rendered attachment link inside a cloned repo's README, commit body, PR description, or issue comment surfaced in Desktop's UI) with a path matching `/[^/]+\/[^/]+\/assets\/[^/]+\/[^/]+` or `/user-attachments/assets/...` will have the user's live GitHub token silently attached by Desktop's main process.

### Finding Description
The filter is installed globally on the Electron session's web requests [2](#0-1) , so it fires for every request from every renderer surface in Desktop that can trigger network loads referencing github.com/GHE paths (markdown previews, diffs, PR/issue content, commit bodies, README rendering, etc. — all of which can originate from a cloned/fetched, attacker-controlled repository). Just like the audited `SecuritizeSwap::buy`, which validated only that `_senderInvestorId` was *some* registered investor but never that `msg.sender` actually *was* that investor, this filter validates only that a URL *looks like* a legitimate asset request (origin + regex-shaped path) but never validates the actual "ownership"/authorization relationship: which repository/account the asset path belongs to, or whether the current request is something the signed-in user actually intended to authenticate. The broken invariant is: "attach my private bearer token only to requests I intend to authenticate as myself" — the guard used (`origin` + generic path regex) is not the same relationship as "this asset belongs to a resource the user is permitted/expects to send credentials to," so the token can be forwarded to fetch **any** path shaped like `/{owner}/{repo}/assets/{id}/{guid}` for a repo the token owner has no relation to.

### Impact Explanation
The token attached is the user's live OAuth API token, capable of privileged GitHub API actions depending on granted scopes. A malicious repo/README/PR/issue rendered inside Desktop can force Desktop to make a background request carrying the victim's token to a path under `github.com`/GHE that the filter blindly matches, effectively an authenticated SSRF-style primitive that abuses the always-signed-in user's credentials against attacker-chosen `owner/repo/assets/...` paths without any user interaction beyond viewing the malicious content. Even though the destination is `github.com` itself (limiting classic exfiltration to a third party), it still allows unauthorized authenticated GitHub API calls under the victim's identity to endpoints/resources the victim never intended to authorize, which matches the report's "anyone can force [the account holder] to perform an action against their financial/account status" bug class translated to "anyone can force the user's Desktop client to authenticate arbitrary matching requests as them."

### Likelihood Explanation
Likelihood is moderate to high: the trigger requires only that the attacker control content that Desktop renders and that references a matching path (a cloned repo's rendered Markdown, PR/issue body, or commit description containing an `<img src="https://github.com/owner/repo/assets/...">` or `user-attachments/assets/...` URL) — no local access, no admin rights, and no social engineering beyond the user opening/viewing attacker-supplied repository content that Desktop already displays as part of normal workflows (this satisfies the "attacker controls a cloned/fetched repository ... or a GitHub API object" criterion in Valid Impact).

### Recommendation
Before attaching the Authorization header, validate that the `owner/repo` segment of the asset path corresponds to a GitHub repository/account that the signed-in account is actually associated with (e.g., cross-check against the repositories/accounts known to Desktop, or restrict token attachment to asset requests explicitly issued in the context of a specific, currently-open repository/account pairing) rather than any URL that merely satisfies the origin + generic path-shape check.

### Proof of Concept
1. Attacker creates a public repository and includes in its `README.md` (or a PR/issue description) rendered by Desktop's Markdown viewer: `![x](https://github.com/attacker-org/attacker-repo/assets/1/00000000-0000-0000-0000-000000000000)`.
2. Victim opens/clones this repository in GitHub Desktop and views the README/PR/issue containing that markup (a normal workflow action, not requiring elevated access).
3. Desktop's renderer issues the image request; `installAuthenticatedImageFilter`'s `onBeforeSendHeaders` handler sees `origin === 'https://github.com'` and `pathname` matching `isGitHubRepoAssetPath`, and attaches `Authorization: token <victim's real token>` [3](#0-2)  — without ever checking that `attacker-org/attacker-repo` is a resource the victim's token holder actually intends to authenticate against. [4](#0-3) [5](#0-4)

### Citations

**File:** app/src/main-process/authenticated-image-filter.ts (L1-64)
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
}
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
