### Title
`AccountsStore.refresh()` lost-update race re-persists a signed-out account and its stale API-fetched data - (File: `app/src/lib/stores/accounts-store.ts`)

### Summary
`AccountsStore` mirrors the Perennial `Vault.settle()` bug class: one code path (`refresh()`) captures a snapshot of in-memory state, performs an async side-effecting operation, and then unconditionally persists the *stale* snapshot back to storage — overwriting a concurrent, more-authoritative mutation made by another path (`removeAccount()`) to the same underlying store in the interim.

### Finding Description
`refresh()` snapshots `this.accounts`, maps each account through an async network call, and only after all promises resolve does it overwrite `this.accounts` and call `save()`: [1](#0-0) 

Each element of that snapshot is refreshed via `tryUpdateAccount` → `fetchUser(account.endpoint, account.token)`, i.e. a live GitHub/GHE API call whose latency and response content are controlled by whatever server is reachable at `account.endpoint`: [2](#0-1) [3](#0-2) 

Meanwhile, `removeAccount()` deletes the token from the OS keychain and filters the account out of `this.accounts`, then persists that authoritative removal: [4](#0-3) 

Because `refresh()` holds its own local variable derived from `this.accounts.map(...)` and does a blind `this.accounts = <result>; this.save()` at the end, if `removeAccount()` executes and completes *during* the in-flight `Promise.all` in `refresh()`, the later-resolving `refresh()` call clobbers `this.accounts` with the stale array that still contains the just-removed account — and persists it to `localStorage` via `save()`: [5](#0-4) 

This exactly parallels the Vault bug: `_credit()`'s direct storage write (≈ `removeAccount()`'s keychain delete + filter) is silently undone by a later flush of a stale in-memory snapshot (≈ `_saveContext()` overwriting `context.local.shares`).

The attacker-controlled trigger for the race is the GitHub/GHE API response itself. `fetchUser` calls to a malicious or compromised GitHub Enterprise endpoint (or a MITM/proxy sitting in front of it — well within the stated "attacker controls a GitHub API object / remote or proxy response" impact class) can be arbitrarily delayed or return attacker-chosen profile data (name, avatarURL, emails, plan). A hostile/compromised endpoint can:
1. Return a `401`/invalid-credential response that the app maps to a token-invalidation event, driving `_removeAccount` for that account while a `refresh()` for the same account list is already in flight (`onTokenInvalidated` → `_removeAccount` referenced in `app-store.ts`).
2. Deliberately stall the `fetchUser` response to widen the race window so the removal happens strictly inside the `Promise.all` gap.
3. Let the delayed response resolve, causing `refresh()`'s stale snapshot (still containing the now-removed account, together with attacker-supplied profile fields) to be written back to `localStorage`, effectively resurrecting a signed-out account entry with attacker-influenced metadata.

### Impact Explanation
A removed/signed-out account can be silently reintroduced into persisted local storage (the `users` key) after the user explicitly signed out, defeating the sign-out action's intent. Depending on subsequent app logic that trusts `AccountsStore` state (e.g. UI showing the account as still authenticated, or code paths keyed off account presence), this can cause confused-deputy behavior and persistence of attacker-influenced account metadata even though the corresponding OS keychain token was already deleted by `removeAccount()` — leaving the persisted store and the keychain in an inconsistent state. This is a real, silent-corruption-of-local-state class bug analogous to the reported Vault issue, though the blast radius here is a client-side desktop state store rather than an on-chain balance.

### Likelihood Explanation
Requires a specific timing window (an in-flight `refresh()` racing a concurrent `removeAccount()`), which in turn requires an attacker (malicious/compromised GHE endpoint or network MITM) to control response timing or trigger a token-invalidation signal for the affected account. This is plausible but not trivially deterministic — it depends on the app calling `refresh()` and `removeAccount()` around the same time, which occurs under normal signed-in usage (periodic/on-demand refresh combined with a 401-triggered auto-sign-out). No local/physical access, admin rights, or pre-existing malware is required — only a hostile/compromised remote endpoint the user has already added as a GitHub Enterprise account, or a network-position attacker able to influence/delay that endpoint's responses.

### Recommendation
Make `refresh()` merge into the *current* `this.accounts` rather than blindly overwriting it, analogous to the Vault fix's approach of applying deltas directly to the authoritative state instead of persisting a stale snapshot:
- Recompute the accounts map by endpoint/id keys at the time `Promise.all` resolves (similar to `addAccount()`'s merge-by-endpoint pattern), only updating fields for accounts that still exist in `this.accounts`, instead of replacing the whole array.
- Alternatively, guard `refresh()`'s final write with a check that no account in the refreshed snapshot has been removed since the snapshot was taken (e.g. version/generation counter), discarding stale results for removed accounts.

### Proof of Concept
Not independently executable without a live/malicious GHE endpoint and instrumented timing, but the race is demonstrable via a unit test analogous to existing ones in `app/test/unit/accounts-store-test.ts`:
1. Add an account, `await accountsStore.loadingPromise`.
2. Call `accountsStore.refresh()` where the injected `fetchUser`/API stub is a controllable deferred promise (same pattern as `createDeferred` used in `app/test/unit/stores/copilot-store-test.ts`).
3. Before resolving that deferred promise, call and await `accountsStore.removeAccount(account)`.
4. Resolve the deferred promise so `refresh()` completes.
5. Observe `dataStore.getItem('users')` (or `accountsStore.getAll()`) still contains the removed account — showing `removeAccount()`'s persisted deletion was overwritten by `refresh()`'s stale write, mirroring the Vault `_credit()`/`_saveContext()` overwrite. [6](#0-5)

### Citations

**File:** app/src/lib/stores/accounts-store.ts (L128-180)
```typescript
  /** Refresh all accounts by fetching their latest info from the API. */
  public async refresh(): Promise<void> {
    this.accounts = await Promise.all(
      this.accounts.map(acc => this.tryUpdateAccount(acc))
    )

    this.save()
    this.emitUpdate(this.accounts)
  }

  /**
   * Attempts to update the Account with new information from
   * the API.
   *
   * If the update fails for whatever reason this function
   * will return the old Account instance. Usually updates fails
   * due to connectivity issues but in the future we should
   * investigate whether we're able to detect here that the
   * token is definitely not valid anymore and let the
   * user know that they've been signed out.
   */
  private async tryUpdateAccount(account: Account): Promise<Account> {
    try {
      return await updatedAccount(account)
    } catch (e) {
      log.warn(`Error refreshing account '${account.login}'`, e)
      return account
    }
  }

  /**
   * Remove the account from the store.
   */
  public async removeAccount(account: Account): Promise<void> {
    await this.loadingPromise

    try {
      await this.secureStore.deleteItem(
        getKeyForAccount(account),
        account.login
      )
    } catch (e) {
      log.error(`Error removing account '${account.login}'`, e)
      this.emitError(e)
      return
    }

    this.accounts = this.accounts.filter(
      a => !(a.endpoint === account.endpoint && a.id === account.id)
    )

    this.save()
  }
```

**File:** app/src/lib/stores/accounts-store.ts (L251-258)
```typescript
  private save() {
    const usersWithoutTokens = this.accounts.map(account =>
      account.withToken('')
    )
    this.dataStore.setItem('users', JSON.stringify(usersWithoutTokens))

    this.emitUpdate(this.accounts)
  }
```

**File:** app/src/lib/stores/accounts-store.ts (L261-269)
```typescript
async function updatedAccount(account: Account): Promise<Account> {
  if (!account.token) {
    return fatalError(
      `Cannot update an account which doesn't have a token: ${account.login}`
    )
  }

  return fetchUser(account.endpoint, account.token)
}
```
