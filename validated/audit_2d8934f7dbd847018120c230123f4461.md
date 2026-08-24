### Title
Origin-scoped (not repository-scoped) Authorization header injection for GitHub asset URLs enables cross-repository credential attachment triggered by attacker-controlled content - (File: `app/src/main-process/authenticated-image-filter.ts`)

### Summary
`installAuthenticatedImageFilter` attaches the signed-in user's real GitHub token as an `Authorization` header to *any* outgoing request whose URL matches a generic "asset" path pattern on a known GitHub/GHE origin, without validating that the path corresponds to a repository the current UI context actually relates to. Because Desktop renders untrusted markdown from PRs, commits, issues, and notifications (which can embed `<img>` tags), an attacker who merely controls content rendered inside the app can cause Desktop to make an authenticated request, using the victim's token, to an arbitrary `/owner/repo/assets/...` path on that origin — a path that has nothing to do with the repository the user opened Desktop for.

### Finding Description
The filter keys the token purely by request `origin`:

<cite repo="Annirich/desktop--005" path="app/src/main-process/authenticated-image-filter.ts" start="31="34" /> [1](#0-0) 

`isGitHubRepoAssetPath` is a broad regex — `/^\/[^/]+\/[^/]+\/assets\/[^/]+\/[^/]+\/?$/` — matching **any** `owner/repo` pair, not just the repository currently open in Desktop or one the API "matched" for the current window. Combined with the origin-only lookup in `originTokens`: [2](#0-1) 

this means: whenever the renderer process loads *any* image URL of that shape on `github.com`/a GHE host for which the user has signed in, the main process silently attaches `Authorization: token <PAT>` — regardless of which repository, PR, or notification triggered the image load. This is the same broken-invariant pattern as the report's seed: a decision (here, "should this request carry my authenticated token") is made using a coarse, unrelated proxy value (origin) instead of the actual scoping value (repository/asset ownership) that should gate it, and nothing re-validates that scope before the credential is attached.

Existing mitigations don't cover this: `installSameOriginFilter` only strips auth headers on cross-origin *redirects*, and does nothing about a same-origin request to a resource that the current UI context has no business fetching. [3](#0-2) 

### Impact Explanation
An attacker who can get arbitrary markdown/image content rendered by Desktop (e.g., a PR description or comment on any repo the victim has cloned/is viewing, a release body, or a notification) can embed an `<img>` reference to `https://github.com/{target-owner}/{target-repo}/assets/{userId}/{guid}`. Desktop will automatically attach the victim's live token to that request even though the target repo is unrelated to the content's actual origin repository. This lets a low-privilege/untrusted contributor:
- Use the victim's authenticated session to probe existence/visibility of private assets attached to repositories the attacker does not have access to (private information disclosure oracle), and
- More broadly demonstrates that token attachment is not scoped to the repository context that legitimately justified issuing it, which is exactly the "effective vs. raw value" disconnect described in the seed report — an authorization decision is made against a value (origin) that doesn't reflect the real, narrower entitlement (repository access) it's meant to represent.

### Likelihood Explanation
Likelihood is moderate: it requires only that the victim view attacker-supplied markdown/HTML content inside Desktop (PR body, comment, notification, or release note) referencing the crafted asset URL — a very ordinary, low-friction action for a public/collaborative repository, no local access, no admin rights, and no prior credential leak required.

### Recommendation
Scope the Authorization attachment to the specific repository (and, ideally, asset) that the current UI context is legitimately displaying, rather than by host origin alone. At minimum, cross-check the `owner/repo` segment in the asset path against the repository associated with the rendering surface (PR, notification, commit) before attaching the token, and avoid injecting credentials for asset paths unrelated to the currently-loaded content.

### Proof of Concept
1. Sign in to GitHub Desktop with an account that has push/read access to a private repository `victim-org/private-repo`, which has an image asset at `https://github.com/victim-org/private-repo/assets/12345/abcde-guid`.
2. As an unrelated attacker, open a PR/issue comment (or craft a notification payload) on a public repository the victim has cloned, containing:
   `<img src="https://github.com/victim-org/private-repo/assets/12345/abcde-guid">`
3. When the victim views that PR/comment/notification in Desktop, the renderer requests the image; `installAuthenticatedImageFilter` matches the `isGitHubRepoAssetPath` regex and origin `https://github.com`, and attaches `Authorization: token <victim-PAT>` to the request — even though the image path belongs to a completely different, unrelated repository from the one being viewed.
4. Observe (e.g. via a local proxy) that the request to the unrelated `victim-org/private-repo` asset succeeds with the attached token, confirming the origin-only scoping does not enforce repository-level relevance before injecting credentials. [4](#0-3)

### Citations

**File:** app/src/main-process/authenticated-image-filter.ts (L9-16)
```typescript
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

**File:** app/src/main-process/same-origin-filter.ts (L34-52)
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
```

**File:** app/src/main-process/main.ts (L348-350)
```typescript
  // Adds an authorization header for requests of avatars on GHES and private
  // repo assets
  const updateAccounts = installAuthenticatedImageFilter(orderedWebRequest)
```
