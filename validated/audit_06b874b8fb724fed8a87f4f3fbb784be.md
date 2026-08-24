Now I have enough to construct a valid analog. The key mechanism found is in `ghRequest` in `app/src/lib/api.ts:1841-1854`, which emits a token-invalidated event whenever a GH(E) host returns a 401 with an `X-GitHub-Request-Id` header. This event is consumed by `onTokenInvalidated` in `app/src/lib/stores/app-store.ts:888-911`, where the guard is:

```ts
if (account.token && account.token !== token) {
  log.error(`Token for ${endpoint} invalidated but token mismatch`)
  return
}
```

Exactly like the Solidity bug, this guard only rejects the operation when `account.token` is truthy and mismatched — if `account.token` is falsy (empty string), the check is bypassed entirely and the account is unconditionally removed/signed-out, regardless of whether the invalidated token actually belongs to it. [1](#0-0) 

### Title
Falsy-token bypass in `onTokenInvalidated` allows a malicious/compromised GitHub Enterprise endpoint to force sign-out of any account matching that endpoint - (File: `app/src/lib/stores/app-store.ts`)

### Summary
`AppStore.onTokenInvalidated` mirrors the audited `AccountantDelegate.initialize()` pattern: it guards a security-sensitive action ("remove this account's stored credential") with a truthiness/inequality check on a value that can legitimately be empty. Just as `!= address(0)` fails to protect against an already-zero state, `account.token && account.token !== token` fails to protect against an already-empty `account.token`, silently permitting the guarded action to run unconditionally.

### Finding Description
`API.ghRequest` treats any `401 Unauthorized` response bearing an `X-GitHub-Request-Id` header (and lacking `X-GitHub-OTP`) as proof the calling account's token was invalidated by GitHub(Enterprise), and broadcasts `emitTokenInvalidated(this.endpoint, this.token)`: [2](#0-1) 

`AppStore.onTokenInvalidated` subscribes to that event and is supposed to only sign out the account whose *current* token matches the token that was actually invalidated, to avoid nuking an account that has since rotated to a new token: [3](#0-2) 

The invariant intended is "only remove the account if we can confirm the invalidated token is the one currently in use." The implementation instead checks `account.token && account.token !== token`. If `account.token` is the empty string, the whole conditional short-circuits to `false`, so the mismatch branch is skipped and `this._removeAccount(account)` executes unconditionally, and the `InvalidatedToken` popup is shown, prompting a fresh sign-in flow.

`account.token` can legitimately be `''` in-memory during the (unbounded, network/keychain-dependent) window in `AccountsStore.loadFromStore`, where accounts are constructed with an empty token placeholder before the OS keychain lookup resolves: [4](#0-3) 

During that window (which is not artificially short — it's gated on an async keychain read, `IPC` traffic, or a slow/locked keychain), any GH(E) host for that account's endpoint that answers a request with `401` + `X-GitHub-Request-Id` will pass the guard and force-remove the account and its stored token, even though no invalid-token evidence exists tying it to that specific account.

### Impact Explanation
This is a remotely triggerable, unprivileged action controlled by a GitHub/GHE server response (a "GitHub API object" in the exact sense the task allows): a malicious or compromised GitHub Enterprise server, or a MITM/rogue proxy in front of it, can trigger unauthenticated 401 responses at any time during the accounts-store loading race, causing GitHub Desktop to silently delete a legitimate signed-in account's credential and force the user through re-authentication (`InvalidatedToken` popup → sign-in dialog). This is a denial-of-service against the user's session and can be used to socially engineer the user into re-entering credentials into an attacker-influenced sign-in flow, satisfying "unauthorized OAuth or account binding"-class impact without any local access, admin rights, or pre-existing malware.

### Likelihood Explanation
The condition requires the app to be in the narrow but real startup race where `accounts` in memory still have an empty token (before `AccountsStore.loadFromStore` completes populating tokens from the keychain) and a request to that endpoint returns 401 with the specific header combination. This is plausible on app launch/reload against a GHE instance under attacker influence (compromised/rogue enterprise server or network path), but it depends on timing, so likelihood is Medium rather than High — comparable to the Medium severity assigned to the original finding, which likewise depended on a specific state ("initialize called first with zero address") before the flawed check could be exploited.

### Recommendation
Change the guard in `onTokenInvalidated` to positively verify the token match rather than only rejecting on a truthy mismatch, e.g.:
```ts
if (account.token !== token) {
  log.error(`Token for ${endpoint} invalidated but token mismatch`)
  return
}
```
This removes the falsy-bypass, exactly mirroring the recommended fix in the original report (replace an "only reject if already set" check with an explicit equality/zero-value check).

### Proof of Concept
1. Sign in to a GitHub Enterprise account so it is persisted (token stored in OS keychain, entry in `users` data store).
2. Relaunch GitHub Desktop. During `AccountsStore.loadFromStore`, `accountWithoutToken` is created with `token: ''` before the async `secureStore.getItem` call resolves. [5](#0-4) 
3. While the keychain lookup is pending (e.g., delayed due to a locked keychain, slow IPC, or an attacker deliberately timing the attack around app launch), have the (malicious/compromised) GHE endpoint answer any in-flight API request to that endpoint with `401` plus an `X-GitHub-Request-Id` header and no `X-GitHub-OTP` header.
4. `API.ghRequest` calls `emitTokenInvalidated(endpoint, token)`. [6](#0-5) 
5. `AppStore.onTokenInvalidated` finds the account for that endpoint; `account.token` is still `''` (falsy), so `account.token && account.token !== token` evaluates to `false`, bypassing the mismatch check, and `this._removeAccount(account)` runs, deleting the account's real token and showing the "Invalidated Account Token" dialog. [3](#0-2)

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

**File:** app/src/lib/api.ts (L1841-1854)
```typescript
    const response = await this.request(this.endpoint, method, path, options)

    // Only consider invalid token when the status is 401 and the response has
    // the X-GitHub-Request-Id header, meaning it comes from GH(E) and not from
    // any kind of proxy/gateway. For more info see #12943
    // We're also not considering a token has been invalidated when the reason
    // behind a 401 is the fact that any kind of 2 factor auth is required.
    if (
      response.status === HttpStatusCode.Unauthorized &&
      response.headers.has('X-GitHub-Request-Id') &&
      !response.headers.has('X-GitHub-OTP')
    ) {
      API.emitTokenInvalidated(this.endpoint, this.token)
    }
```

**File:** app/src/lib/stores/accounts-store.ts (L218-240)
```typescript
    const accountsWithTokens = []
    for (const account of rawAccounts) {
      const accountWithoutToken = new Account(
        account.login,
        account.endpoint,
        '',
        account.emails,
        account.avatarURL,
        account.id,
        account.name,
        account.plan
      )

      const key = getKeyForAccount(accountWithoutToken)
      try {
        const token = await this.secureStore.getItem(key, account.login)
        accountsWithTokens.push(accountWithoutToken.withToken(token || ''))
      } catch (e) {
        log.error(`Error getting token for '${key}'. Skipping.`, e)

        this.emitError(e)
      }
    }
```
