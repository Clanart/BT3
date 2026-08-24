Based on my investigation, this is the key finding: the `authenticated-image-filter.ts` mirrors the exact bug class in the report — a security decision keyed off the wrong identity (an unverified origin) instead of verifying the actual resource path/authority that should govern access.

### Title
Authorization token attached to any request matching an avatar/asset path pattern regardless of true origin ownership - ([File: app/src/main-process/authenticated-image-filter.ts])

### Summary
`installAuthenticatedImageFilter` decides whether to attach a user's GitHub/GHES `Authorization: token …` header purely based on two checks: (1) whether `originTokens` (a map built from the user's signed-in account endpoints) has an entry for `new URL(details.url).origin`, and (2) whether the URL's `pathname` matches a regex for enterprise avatar or repo-asset paths. [1](#0-0) 

### Finding Description
This is directly analogous to the Solana report's root cause: a privileged action (deriving/using a PDA, or here, attaching a bearer credential) is authorized by checking a *derivable, attacker-influenceable* value (a path pattern / hostname string) instead of a value that's cryptographically or structurally bound to the correct authority. In the Solana bug, the program trusted whatever `sandwich_validators` account was passed in in `ExpandSandwichValidatorsBitmap` without deriving the PDA from the correct `multisig_authority` seed, so a wrong-authority PDA could be used.

Here, the filter trusts `origin` (scheme+host+port) matching one of the user's own signed-in endpoints as the sole gate, then additionally just regex-matches the path shape (`isEnterpriseAvatarPath` / `isGitHubRepoAssetPath`) — it does not verify that the specific resource being requested actually belongs to the account/repository the user is authenticated to, nor is there any binding to a specific expected host beyond an origin-string equality check populated from `originTokens`: [2](#0-1) 

Because `originTokens` is keyed by `origin` (built from `new URL(endpoint).origin`) for every enterprise/GHES account the user is signed into, **any URL** on that same GHES host whose path happens to match the `isGitHubRepoAssetPath` regex (`/^\/[^/]+\/[^/]+\/assets\/[^/]+\/[^/]+\/?$/` or `/^\/user-attachments\/assets\/[^/]+\/?$/`) will get the user's bearer token attached — regardless of which repository (public/private, owned by any org on that instance) the asset path segment actually references: [3](#0-2) 

The path regex only checks *shape*, not *ownership*: `[^/]+` for the owner/repo segments is a wildcard, so `/attacker-org/public-repo/assets/1/guid` matches identically to `/victim-org/private-repo/assets/1/guid`. This is the same class of flaw as the reported PDA issue — the check verifies "does this look like the right kind of object" (path shape / origin string) rather than "is this actually the resource whose access should require my authority."

### Impact Explanation
This filter is wired into the main process at startup, applied to every request through `session.defaultSession.webRequest`, and updated from renderer-controlled IPC (`ipcMain.on('update-accounts', ...)`): [4](#0-3) 

The rendered content that triggers requests to `assets/...` or `enterprise/avatars/...` paths comes from **untrusted, attacker-controlled sources**: markdown rendering of commit messages, PR descriptions, issue bodies, or a fetched repository's README — all of which are attacker-controlled "GitHub API objects" per the valid-impact scope. An attacker who can get a victim to view rendered markdown containing an `<img src="https://<victim's-ghes-host>/attacker-org/public-or-any-repo/assets/x/y">` (a valid `user-attachments`/asset-style URL on the *same* GHES origin the victim is signed into) causes GitHub Desktop to silently attach the victim's real GHES access token to a request whose destination path segments (owner/repo) are fully attacker-chosen. Since GHES `assets` URLs can be uploaded to by any authenticated user with API access and the regex doesn't validate that the asset belongs to a repo the user actually has open, this allows token leakage to any endpoint on that host matching the path shape — an unprivileged, attacker-controlled-content path leading to credential exfiltration, consistent with the required "unauthorized... credential/token exfiltration" impact category.

### Likelihood Explanation
Likelihood is high: rendering of markdown containing `<img>` tags from PRs/issues/commits is a routine, unprompted user action (viewing a PR or commit in Desktop), requiring no unusual steps by the victim. The attacker only needs to control content that gets rendered (a PR description, issue comment, or README) and know/guess the victim's GHES origin (which is often predictable/public for enterprise deployments), matching the "attacker controls a GitHub API object" scope precisely.

### Recommendation
Do not authorize solely on origin + generic path shape. Bind the authorization decision to the specific resource context that is actually being displayed (e.g., only attach the token when the request is initiated in the context of a repository/account the renderer has confirmed access to, or validate the asset's owner/repo segments against the currently loaded repository/PR's `gitHubRepository` before forwarding the header). At minimum, avoid using a broad `[^/]+/[^/]+` wildcard match for asset ownership and instead thread the expected owner/repo (or a signed reference) through from the renderer so the main process can verify identity rather than shape before attaching `Authorization`.

### Proof of Concept
1. Sign into a GHES instance (e.g. `https://ghe.example.com`) in GitHub Desktop; `updateAccounts` populates `originTokens.set('https://ghe.example.com', token)`.
2. As an attacker with any account on that same GHES instance, upload an arbitrary file as a comment attachment to get a valid-shaped URL, e.g. `https://ghe.example.com/attacker/anyrepo/assets/1/00000000-guid`, or craft `https://ghe.example.com/user-attachments/assets/<guid>`.
3. Embed this URL as an `<img src=...>` in a PR description, issue, or commit message on a repository the victim has open in Desktop.
4. When the victim views that PR/issue/commit, Desktop's renderer requests the image; `installAuthenticatedImageFilter`'s `onBeforeSendHeaders` listener matches on `origin === 'https://ghe.example.com'` and `isGitHubRepoAssetPath` matching the path shape, and attaches `Authorization: token <victim token>` to the request to the attacker-controlled path/server-side asset — [5](#0-4)  — regardless of which repository the asset segment names, since the regex doesn't verify ownership relative to the repo currently open in Desktop.

**Uncertainty note:** I was unable to fully confirm within the available search results whether server-side GHES `assets` endpoints would actually respond to or log the token when the owner/repo path is not associated with the token's account (i.e., whether GHES itself would reject/serve differently), since that depends on GHES server behavior not present in this client-side repo. A background Devin session with the actual GHES/GitHub server behavior or with `desktop`'s test suite would be needed to fully validate exploitability end-to-end.

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

**File:** app/src/main-process/main.ts (L348-361)
```typescript
  // Adds an authorization header for requests of avatars on GHES and private
  // repo assets
  const updateAccounts = installAuthenticatedImageFilter(orderedWebRequest)

  Menu.setApplicationMenu(
    buildDefaultMenu({
      selectedShell: null,
      selectedExternalEditor: null,
      askForConfirmationOnRepositoryRemoval: false,
      askForConfirmationOnForcePush: false,
    })
  )

  ipcMain.on('update-accounts', (_, accounts) => updateAccounts(accounts))
```
