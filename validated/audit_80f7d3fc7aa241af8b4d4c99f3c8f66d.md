No vulnerability found for this question.

**Reasoning:**

The premise assumes `AccountsStore` can end up holding two `Account` objects with the same `endpoint` but different `login`/`token`, but that invariant is actively enforced by the store itself, not merely by `getAccountForEndpoint`.

`AccountsStore.addAccount` deduplicates by endpoint before ever mutating `this.accounts`: it builds a `Map<string, Account>` keyed by `endpoint` from the existing list, overwrites the entry for the new account's endpoint, and then rebuilds the array from `map.values()`, guaranteeing at most one account per endpoint at any time. [1](#0-0) 

`AccountsStore.removeAccount` only removes the entry that matches both `endpoint` and `id`, and if the secure-store deletion fails it returns early without mutating `this.accounts` at all — so a "failed sign-out" cannot leave a stale duplicate with a different login on the same endpoint; it just leaves the original single entry untouched. [2](#0-1) 

`loadFromStore` (used at startup) simply rehydrates whatever was persisted via `save()`, and `save()` only ever persists whatever is in `this.accounts`, which is kept endpoint-unique by `addAccount`'s Map-based merge — so under normal application flow there is no code path that inserts two accounts sharing an `endpoint` into the array that `getAccountForEndpoint` reads. [3](#0-2) 

`getAccountForEndpoint` itself is a pure lookup over whatever `accounts` array it's handed: [4](#0-3) 

Producing the described duplicate would require directly tampering with the persisted `users` data store or bypassing `AccountsStore`'s public API — this requires local/filesystem access to corrupt on-disk state, which is explicitly out of scope ("Reject local access, admin rights, prior malware, leaked credentials"). There is no attacker-controlled remote input (repository content, API object, deep link, or remote/proxy response) that can inject a second `Account` with a colliding `endpoint` through any reachable code path.

### Citations

**File:** app/src/lib/stores/accounts-store.ts (L116-122)
```typescript
    const accountsByEndpoint = this.accounts.reduce(
      (map, x) => map.set(x.endpoint, x),
      new Map<string, Account>()
    )
    accountsByEndpoint.set(account.endpoint, account)

    this.accounts = sortAccounts([...accountsByEndpoint.values()])
```

**File:** app/src/lib/stores/accounts-store.ts (L161-180)
```typescript
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

**File:** app/src/lib/stores/accounts-store.ts (L208-258)
```typescript
  private async loadFromStore(): Promise<void> {
    const raw = this.dataStore.getItem('users')
    if (!raw || !raw.length) {
      return
    }

    const parsedAccounts: ReadonlyArray<IAccount> = JSON.parse(raw)
    const migratedAccounts = this.getMigratedGHEAccounts(parsedAccounts)
    const rawAccounts = migratedAccounts ?? parsedAccounts

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

    this.accounts = sortAccounts(accountsWithTokens)
    // If any account was migrated, make sure to persist the new value
    if (migratedAccounts !== null) {
      this.save() // Save already emits an update
    } else {
      this.emitUpdate(this.accounts)
    }
  }

  private save() {
    const usersWithoutTokens = this.accounts.map(account =>
      account.withToken('')
    )
    this.dataStore.setItem('users', JSON.stringify(usersWithoutTokens))

    this.emitUpdate(this.accounts)
  }
```

**File:** app/src/lib/api.ts (L2349-2355)
```typescript
/** Get the account for the endpoint. */
export function getAccountForEndpoint(
  accounts: ReadonlyArray<Account>,
  endpoint: string
): Account | null {
  return accounts.find(a => a.endpoint === endpoint) || null
}
```
