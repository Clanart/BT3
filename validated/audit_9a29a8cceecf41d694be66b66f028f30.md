Given the iteration limit, I was not able to fully trace the `update-accounts` IPC round-trip to confirm the exact timing window, but the code I did examine supports a clear structural analog to the reported bug class.

### Title
Stale/Unsynchronized Token-to-Origin Cache Silently Re-Authenticates Image Requests After Account Sign-Out or Token Invalidation - (File: `app/src/main-process/authenticated-image-filter.ts`)

### Summary
`installAuthenticatedImageFilter` maintains a main-process-local `originTokens` map (`endpoint origin -> token`) that is used to automatically attach `Authorization: token <token>` headers to any outgoing request whose pathname matches a broad regex for GHE avatars or GitHub repo/user-attachment assets. [1](#0-0) 
This mirrors the reported bug class: a security-sensitive lookup (`vaultAssetToAdapter` in the report, `originTokens` here) is derived from a mutable source of truth (the router / the `AccountsStore`) but is only refreshed out-of-band, so it can silently diverge from the true, current account/token state and get used with a stale or removed credential.

### Finding Description
The `originTokens` map is only rebuilt when the closure returned by `installAuthenticatedImageFilter` is explicitly invoked with a fresh `EndpointToken[]` list: [2](#0-1) 
This callback (`updateAccounts`) is wired up once in `main.ts` and is driven by an IPC message (`update-accounts`) sent from the renderer/`AppStore` whenever the `AccountsStore` emits an update, not synchronously with account removal or token invalidation. [3](#0-2) 

Meanwhile, `AppStore.onTokenInvalidated` removes the account from the in-memory/renderer accounts list on token invalidation: [4](#0-3) 
but the main process's `originTokens` map used by the WebRequest filter is a *separate, independently-maintained cache* that only reflects this change once the corresponding IPC update propagates and is processed. Until that happens, the main process filter continues to hold and use the old token — directly analogous to the router's `_supportedAssets` continuing to reference an adapter the router itself no longer considers valid.

The consuming check itself has no defense against staleness: it doesn't verify the token is still valid, doesn't correlate with the current signed-in accounts, and doesn't distinguish "recently signed out" from "currently signed in." It simply trusts whatever is in the local map: [5](#0-4) 

The attacker-reachable trigger is content the user did not create but merely views: any GitHub API object rendered in Desktop (a PR description, issue body, commit message, or comment) can embed an `<img>` tag whose `src` path matches the broad `isGitHubRepoAssetPath` regex (`/^\/[^/]+\/[^/]+\/assets\/[^/]+\/[^/]+\/?$/` or `/^\/user-attachments\/assets\/[^/]+\/?$/`) on the user's configured GitHub/GHE host. Rendering that content causes the renderer to issue the matching request, and the main process filter attaches whatever token currently sits in `originTokens` — even if that token belongs to an account the user has already signed out of or that GitHub has already invalidated.

### Impact Explanation
If the cached token is stale relative to the true account state (e.g., just signed out, or invalidated after a leaked-token report to GitHub), Desktop will keep using it to authenticate requests to arbitrary matching paths without validating it against the current account list. This is a credential-handling correctness bug: a token that the user/GitHub believes is dead is silently kept alive and used by Desktop, undermining the sign-out/invalidation guarantee. It does not require any local access, admin rights, or social engineering — merely rendering attacker-authored content that is already part of normal repository collaboration (PR/issue/commit bodies).

### Likelihood Explanation
Likelihood is moderate: it depends on a timing window between an account being removed/invalidated in the renderer and the corresponding IPC update reaching and being applied in the main process, and it requires the victim to view GitHub-rendered content containing a matching asset-style image path around that window. This is a real, structurally-supported race rather than a purely theoretical one, since the two stores (`AccountsStore` in renderer, `originTokens` in main process) are explicitly decoupled and reconciled asynchronously, exactly the "governance forgets to update the dependent state" scenario described in the original report.

### Recommendation
- Have the main process validate tokens against the live account state at request time rather than trusting a locally cached map that can silently drift out of sync.
- Emit and synchronously await an explicit "accounts changed" acknowledgment before considering removal/invalidation complete, similar to the report's suggestion to emit an event on `vaultAssetToAdapter` changes.
- Add an explicit invalidation step: when `onTokenInvalidated` fires, immediately push an authoritative update to the main-process filter rather than relying on the general accounts-changed propagation path.
- Tighten `isGitHubRepoAssetPath`/`isEnterpriseAvatarPath` matching and consider binding the attached token to the specific account/repository context rather than solely to origin, reducing the blast radius of any stale entry.

### Proof of Concept
1. Sign in to Desktop with an account (or a GHE account) so `originTokens` contains `{ <origin>: <token> }`. [2](#0-1) 
2. Trigger a condition that invalidates the token server-side, or sign out, initiating `onTokenInvalidated`/removal in the renderer's `AccountsStore`. [4](#0-3) 
3. Before the corresponding `update-accounts` IPC message reaches and is applied in the main process, open/view a PR, issue, or commit (a GitHub API object) that embeds an image whose path matches `/^\/[^/]+\/[^/]+\/assets\/[^/]+\/[^/]+\/?$/`.
4. Observe that the main process's `onBeforeSendHeaders` listener still attaches `Authorization: token <old token>` to that request using the stale `originTokens` entry, since the check performs no live validation against the current account list. [5](#0-4)

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L888-911)
```typescript
  private onTokenInvalidated = (endpoint: string, token: string) => {
    const account = getAccountForEndpoint(this.accounts, endpoint)

    if (account === null) {
      return
    }

    // If we have a token for the account but it doesn't match the token that
    // was invalidated that likely means that someone held onto an account for
    // longer than they should have which is bad but what's even worse is if we
    // invalidate an active account.
    if (account.token && account.token !== token) {
      log.error(`Token for ${endpoint} invalidated but token mismatch`)
      return
    }

    // If the token was invalidated for an account, sign out from that account
    this._removeAccount(account)

    this._showPopup({
      type: PopupType.InvalidatedToken,
      account,
    })
  }
```
