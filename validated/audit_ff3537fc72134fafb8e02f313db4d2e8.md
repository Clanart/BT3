Based on my investigation, I found a strong analog in `installAuthenticatedImageFilter`, which mirrors the "blacklist" bug-class: a security check is only enforced by *pathname pattern matching* against an untrusted, attacker-influenced value, rather than validating that the resource actually belongs to a repository the signed-in user is authorized to access — the exact same class of flaw as the Stablecoin bug, where a security decision (blacklist/authorization) is evaluated at the wrong layer and never actually enforced against the real access-controlled resource.

### Title
Authenticated image filter attaches OAuth bearer token to any same-origin URL matching a loose asset-path regex, regardless of repo access - (File: `app/src/main-process/authenticated-image-filter.ts`)

### Summary
`installAuthenticatedImageFilter` attaches the user's GitHub/GHES `Authorization` token to any outgoing request whose origin matches a known enterprise/GitHub endpoint and whose *pathname* matches a broad regex, without verifying that the underlying resource (owner/repo/asset id) is something the signed-in account is actually entitled to see.

### Finding Description
The filter decides whether to attach a bearer token purely from two client-observable properties: the request `origin` (matched against a token map keyed by known endpoints) and the request `pathname` (matched with `isEnterpriseAvatarPath` / `isGitHubRepoAssetPath`): [1](#0-0) [2](#0-1) 

The regex `^\/[^/]+\/[^/]+\/assets\/[^/]+\/[^/]+\/?$` matches `/{owner}/{repo}/assets/{userId}/{guid}` for **any** owner/repo string, not just the repository currently open in Desktop. Because the token is looked up solely by `origin` (e.g. `https://github.example.com`) and the check never verifies the `{owner}/{repo}` segment against the repository the user is working with or has access to, any HTML/Markdown content rendered inside Desktop's webviews (rendered commit/PR/issue bodies, READMEs, diffs, etc., which can originate from a cloned/fetched repository or a fetched GitHub API object) that references an `<img src="https://<enterprise-host>/<attacker-owner>/<attacker-repo>/assets/.../...">` will cause Desktop to silently attach the user's real API token to a request aimed at a repository path the attacker chose, on the shared enterprise origin.

This is structurally identical to the Stablecoin flaw: a security-relevant decision (blacklist / token authorization) is computed from a static, easily-satisfied condition (destination address / URL pathname shape) rather than being enforced at the point where the actual protected resource is accessed (transfer of funds / disclosure of the bearer token to a specific repo's private asset endpoint).

### Impact Explanation
An attacker who controls content that gets rendered inside Desktop (e.g., a commit message, PR/issue body, or README fetched from a GitHub API object or a malicious/compromised repository) can cause the embedded `<img>` tag to leak the victim's live GHES bearer token to an attacker-chosen path on the enterprise host. Depending on what that path resolves to server-side (proxies, redirects, logging, or any endpoint under that pattern), this can lead to token exfiltration to infrastructure the attacker influences, going beyond the intended "load this repo's private image" use case. This matches the report's severity class: a manually-relied-upon access boundary (only attach the token to requests for the current resource's private assets) that is not actually enforced.

### Likelihood Explanation
The unprivileged attacker primitive matches the required threat model precisely: the attacker only needs to control content rendered from a cloned/fetched repository or a GitHub API object (e.g., PR/issue/commit body markdown) — no local access, no malware, no leaked credentials. Because Desktop automatically renders remote markdown/HTML containing `<img>` tags, exploitation only requires the victim to view content authored by the attacker (a normal, expected user action, not an unnatural step).

### Recommendation
Scope the token attachment to the actual repository context Desktop is currently interacting with (or to the exact resource identifiers returned by a trusted API response), rather than a generic pathname regex matched against attacker-controllable segments. At minimum, validate the `{owner}/{repo}` (or user/guid) segments against an allow-list derived from the currently open repository/PR/issue, not from the URL alone.

### Proof of Concept
1. Sign in to a GHES instance in Desktop so `originTokens` contains a token for `https://github.example.com`. [3](#0-2) 
2. View a PR/issue/commit whose body/README (fetched from the repo or API) contains: `<img src="https://github.example.com/attacker-owner/attacker-repo/assets/000/aaaa.png">`.
3. When Desktop's webview requests that image, `onBeforeSendHeaders` matches `origin` (`https://github.example.com`, has a token) and `pathname` (`/attacker-owner/attacker-repo/assets/000/aaaa.png`, matches `isGitHubRepoAssetPath`), and unconditionally injects `Authorization: token <victim-token>` into the request sent to the attacker-controlled path. [2](#0-1) 
4. No check confirms the image path corresponds to the actual repository being viewed or a repository the account is permitted to access — the same-origin filter (`same-origin-filter.ts`) does not help here since the request is not cross-origin. [4](#0-3) 

**Caveat:** I could not fully verify what specific server behavior on GHES-side would make this exfiltratable beyond the shared origin (e.g., logging, open redirects, or reverse proxies under that host), since that depends on the GHES server implementation outside this repository's index; the client-side over-broad trust decision itself is confirmed in the code above.

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
