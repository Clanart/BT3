### Title
Stale account token remains usable for authenticated image/asset requests after sign-out (no revocation check on the origin-token cache) - ([File: app/src/main-process/authenticated-image-filter.ts])

### Summary
GitHub Desktop's core sign-out path (`_removeAccount`) does two things: remove the account from `AccountsStore` and call the GitHub API to revoke the OAuth token server-side via `deleteToken`. [1](#0-0)  Most in-memory consumers (Copilot model/quota caches, `ApiRepositoriesStore`) subscribe to `AccountsStore.onDidUpdate` and prune their cached state accordingly. [2](#0-1) [3](#0-2) 

However, the main-process authenticated-image filter keeps its own independent `originTokens` map (`endpoint origin -> token`), which is only refreshed when something explicitly calls the function returned by `installAuthenticatedImageFilter`. [4](#0-3)  Because this consumer is a plain closure in the main process rather than a subscriber to `AccountsStore`'s update event, whether/when it is refreshed depends entirely on the caller in `main.ts` invoking the returned updater function — and unlike `ApiRepositoriesStore`/`CopilotStore`, there is no direct evidence in the reachable code that this callback is wired to fire specifically and immediately on `removeAccount`/`onTokenInvalidated`. If the origin-token map is only refreshed on renderer/account IPC events that aren't guaranteed to fire on every token invalidation/removal path (e.g. `onTokenInvalidated` in `app-store.ts`), the previously valid token remains cached in the map and continues to be attached as an `Authorization: token <token>` header to any request matching the enterprise-avatar or repo-asset path patterns. [5](#0-4) 

### Finding Description
The broken invariant is the same as the HackerOne report: a token that should be dead (account signed out / token invalidated) continues to grant read access to protected resources because the enforcement point storing/checking the credential is not tied to the authoritative account/token lifecycle. In Desktop's case the "hacker-reference API" analog is `installAuthenticatedImageFilter`'s `originTokens` map: it is populated from `EndpointToken[]` and used unconditionally to add an `Authorization` header to matching outbound requests for private avatars and repo/user-attachment assets. [6](#0-5)  Nothing in this module re-validates the token against `AccountsStore`, and the invalidation path (`onTokenInvalidated` in `app-store.ts`, which calls `_removeAccount`) lives in the renderer/app-store layer, not in the main process where this cache resides. [7](#0-6) 

### Impact Explanation
If the update callback is not called synchronously (or at all) for every account removal/token-invalidation event, an attacker-controlled or expired/invalidated token would still be attached by Desktop to authenticated image requests to enterprise avatar/asset endpoints — leaking a live `Authorization: token …` header to whatever origin serves those assets, and continuing to authorize fetches of private GHE avatars/attachments after the user believes they are signed out. This matches the "credential/token exfiltration" and "unprivileged... GitHub API object" impact classes in scope.

### Likelihood Explanation
Medium/Low-confidence: the vulnerable pattern (a second, independent token cache in `authenticated-image-filter.ts` that isn't a direct subscriber of `AccountsStore.onDidUpdate`, unlike `ApiRepositoriesStore`/`CopilotStore` which explicitly are) is confirmed by the source. [4](#0-3)  What I could not confirm within the available tool budget is the exact call site(s) in `app/src/main-process/main.ts` that invoke the returned updater function and whether they are triggered on every `removeAccount`/`onTokenInvalidated` event or only on app startup / explicit IPC refreshes — the index only surfaced the import and two references in `main.ts` without full context of the wiring. This is a real limitation of the index coverage, so this should be treated as a lead for verification with full file access rather than a fully confirmed exploit.

### Recommendation
Wire the `originTokens` refresh callback returned by `installAuthenticatedImageFilter` directly to `AccountsStore.onDidUpdate` (or explicitly call it inside `_removeAccount`/`onTokenInvalidated` before completing sign-out) so that a revoked/removed account's token is purged from the main-process cache at the same moment it is purged from `AccountsStore` and `deleteToken` is called, closing the same time-of-check/time-of-use gap that the HackerOne report described for HackerOne's own API.

### Proof of Concept
Not independently verified end-to-end due to missing visibility into the exact `main.ts` wiring of the updater callback; conceptually:
1. Sign in to a GitHub Enterprise account with private avatars/assets enabled.
2. Load a window/image that triggers an authenticated avatar/asset fetch, populating `originTokens` for that origin.
3. Sign out (or have the token invalidated server-side, triggering `onTokenInvalidated`).
4. If the callback that refreshes `originTokens` is not invoked synchronously on that path, a subsequent request to the same private avatar/asset origin will still be sent with the old `Authorization: token <revoked-token>` header from `authenticated-image-filter.ts`.

Given the uncertainty noted above, I'd recommend treating this as a **candidate** finding requiring a Devin session with full file/terminal access to confirm the exact `main.ts` call sites before filing it as confirmed.

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L8023-8029)
```typescript
  public async _removeAccount(account: Account) {
    log.info(
      `[AppStore] removing account ${account.login} (${account.name}) from store`
    )
    await this.accountsStore.removeAccount(account)
    await deleteToken(account)
  }
```

**File:** app/src/lib/stores/copilot-store.ts (L773-807)
```typescript
  /** Prunes account-scoped model metadata when accounts are removed. */
  private onAccountsUpdated = (accounts: ReadonlyArray<Account>): void => {
    const accountKeys = new Set(accounts.map(getCopilotAccountCacheKey))
    let prunedCache = false

    for (const key of this.modelCaches.keys()) {
      if (!accountKeys.has(key)) {
        this.modelCaches.delete(key)
        prunedCache = true
      }
    }

    for (const key of this.modelsInFlight.keys()) {
      if (!accountKeys.has(key)) {
        this.modelsInFlight.delete(key)
      }
    }

    for (const key of this.quotaCaches.keys()) {
      if (!accountKeys.has(key)) {
        this.quotaCaches.delete(key)
        prunedCache = true
      }
    }

    for (const key of this.quotasInFlight.keys()) {
      if (!accountKeys.has(key)) {
        this.quotasInFlight.delete(key)
      }
    }

    this.signedInAccountKeys.clear()
    for (const key of accountKeys) {
      this.signedInAccountKeys.add(key)
    }
```

**File:** app/src/lib/stores/api-repositories-store.ts (L114-131)
```typescript
  private onAccountsChanged = (accounts: ReadonlyArray<Account>) => {
    const newState = new Map<Account, IAccountRepositories>()

    for (const account of accounts) {
      for (const [key, value] of this.accountState.entries()) {
        // Check to see whether the accounts store only emitted an
        // updated Account for the same login and endpoint meaning
        // that we don't need to discard our cached data.
        if (accountEquals(key, account)) {
          newState.set(account, value)
          break
        }
      }
    }

    this.accountState = newState
    this.emitUpdate()
  }
```

**File:** app/src/main-process/authenticated-image-filter.ts (L26-63)
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
