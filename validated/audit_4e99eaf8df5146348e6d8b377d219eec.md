## Title
Authorization token is attached to any URL matching a generic `/owner/repo/assets/...` or `/user-attachments/assets/...` pattern, regardless of which repository the rendered content actually belongs to - ([File: app/src/main-process/authenticated-image-filter.ts](app/src/main-process/authenticated-image-filter.ts))

## Summary
The external report's broken invariant is: a derived/auto-generated resource is exposed and made accessible without the access-control scoping the user (and the underlying object) is supposed to have, and there is no way to constrain or revoke it. The closest reachable Desktop analog is `installAuthenticatedImageFilter`, which decides whether to attach the signed-in user's real OAuth/PAT token to an outgoing image request purely by matching the request's **origin + pathname shape**, not by any binding to the specific repository or PR/issue context that is currently being rendered.

## Finding Description
`installAuthenticatedImageFilter` in `app/src/main-process/authenticated-image-filter.ts` installs a `webRequest.onBeforeSendHeaders` hook that attaches `Authorization: token <token>` to any request whose pathname matches: [1](#0-0) 

and whose origin has a known token in `originTokens` (built from all signed-in accounts): [2](#0-1) 

Two properties make this broader than intended:
1. `isGitHubRepoAssetPath` matches **any** `/owner/repo/assets/id/guid` path — it never checks that `owner/repo` is the repository whose content (PR/issue/commit body) is currently rendered in the webview/renderer.
2. `isGitHubRepoAssetPath`'s second branch matches `/user-attachments/assets/<guid>` with **no owner/repo scoping at all**.

Because rendered Markdown for issues, PRs, and commit descriptions in Desktop can embed arbitrary `<img>` tags, and that Markdown content is attacker-influenced (an attacker can open a PR/issue/comment on any public repository, or push a commit message, containing an `<img src>`), this filter will unconditionally attach the victim's real API token to the fetch of *any* asset GUID an attacker chooses to reference — not just assets that belong to the content being viewed. Since `user-attachments/assets/<guid>` GUIDs are opaque but not secret in the sense that they can leak via other channels (email notifications, chat, screenshots, other markdown), an attacker who obtains such a GUID belonging to a private attachment can craft public content that Desktop will render, causing Desktop to fetch that private asset with the victim's own token attached — a confused-deputy use of the credential outside the intended scope. `installSameOriginFilter` only strips credentials on cross-origin **redirects**; it does not protect against this because the request never leaves the legitimate GitHub/GHES origin: [3](#0-2) 

The `OrderedWebRequest` composition applies listeners in sequence based on the live `details.url`/origin at each step, so there is no per-repository or per-request-origin binding for the image filter either: [4](#0-3) 

## Impact Explanation
The token attached is the user's real GitHub/GHES API credential. While the immediate visible effect is only that the (still-authenticated) victim's own Electron renderer fetches and displays the image, the underlying design flaw is that credential attachment is decoupled from the actual authorization/ownership context of the rendered content. This is a token-misuse/confused-deputy primitive reachable purely from attacker-controlled repository content (issue/PR/comment/commit body rendered by Desktop) with no local access, malware, or social engineering beyond "victim opens/views the object in Desktop" — squarely in the "unauthorized use of credentials via an attacker-controlled GitHub API object" category. Severity is bounded by the fact that the fetched content is only rendered locally to the victim, not exfiltrated to the attacker directly, so it does not reach full "credential exfiltration."

## Likelihood Explanation
Moderate. The main constraint is that the attacker needs a valid `owner/repo/assets/.../guid` or `user-attachments/assets/guid` reference to a resource of interest — these GUIDs are not brute-forceable but can leak through normal GitHub workflows (email notifications, cross-posted links, screenshots). No privileged access, host compromise, or unusual user action is required beyond viewing attacker-supplied repository content that Desktop already renders by design (PR/issue bodies).

## Recommendation
Scope the Authorization attachment to the repository/context actually being displayed (e.g., pass down the specific `owner/repo` the current view belongs to and require the path's `owner/repo` to match) rather than matching on a generic path shape across the whole origin. For `user-attachments/assets` (which carries no owner/repo in the path), consider not attaching long-lived account tokens automatically, or validating server-side ownership/response before treating the fetch as legitimate, and add regression tests asserting the filter refuses to attach tokens for asset paths unrelated to the currently open repository/PR.

## Proof of Concept
1. Sign in to Desktop with an account that has access to a private repository containing a private attachment at `https://github.com/private-owner/private-repo/assets/<userID>/<guid>` (or a `user-attachments/assets/<guid>` reference).
2. Obtain that GUID via any normal leakage channel (e.g., a GitHub email notification HTML source, shared chat link, screenshot metadata).
3. As an unrelated, unprivileged attacker, open a public issue/PR/comment on any repository containing `<img src="https://github.com/private-owner/private-repo/assets/<userID>/<guid>">` (or the `user-attachments/assets` equivalent).
4. Have the victim view that issue/PR in GitHub Desktop.
5. `installAuthenticatedImageFilter` matches the path via `isGitHubRepoAssetPath`/`isEnterpriseAvatarPath` and attaches the victim's real `Authorization: token <token>` header to the request regardless of the fact that `private-owner/private-repo` has nothing to do with the repository being viewed — demonstrating that credential attachment is not scoped to the rendering context.

Note: I could not find any additional runtime code that ties the currently-rendered repository context into this filter's origin/token map (e.g., no per-repo allow-list was found in `app/src/main-process/authenticated-image-filter.ts` or its call site in `app/src/main-process/main.ts`), and no unit tests for this file were found in the index, so I cannot rule out that mitigations exist elsewhere in code not covered by the index; a full-repo Devin session would be needed to exhaustively confirm there is no additional scoping check before treating this as fully unmitigated.

### Citations

**File:** app/src/main-process/authenticated-image-filter.ts (L5-16)
```typescript
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

**File:** app/src/main-process/same-origin-filter.ts (L54-72)
```typescript
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
```

**File:** app/src/main-process/ordered-webrequest.ts (L160-187)
```typescript
    this.onBeforeSendHeaders = new AsyncListenerSet(
      webRequest.onBeforeSendHeaders.bind(webRequest),
      async (listeners, initialDetails) => {
        let details = initialDetails
        let response: BeforeSendResponse = {}

        for (const listener of listeners) {
          response = await listener(details)
          if (response.cancel === true) {
            break
          }

          if (response.requestHeaders !== undefined) {
            // I have no idea why there's a discrepancy of types here.
            // details.requestHeaders is a Record<string, string> but
            // BeforeSendResponse["requestHeaders"] is a
            // Record<string, (string) | (string[])>. Chances are this was done
            // to make it easier for filters but it makes it trickier for us as
            // we have to ensure the next filter gets headers as a
            // Record<string, string>
            const requestHeaders = flattenHeaders(response.requestHeaders)
            details = { ...details, requestHeaders }
          }
        }

        return details
      }
    )
```
