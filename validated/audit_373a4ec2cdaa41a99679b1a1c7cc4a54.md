[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** app/src/lib/find-account.ts (L16-28)
```typescript
async function canAccessRepositoryUsingAPI(
  account: Account,
  owner: string,
  name: string
): Promise<boolean> {
  const api = API.fromAccount(account)
  const repository = await api.fetchRepository(owner, name)
  if (repository) {
    return true
  } else {
    return false
  }
}
```

**File:** app/src/lib/find-account.ts (L45-54)
```typescript
  // We have a couple of strategies to try to figure out what account we
  // should use to authenticate the URL:
  //
  //  1. Try to parse a remote out of the URL.
  //    1. If that works, try to find an account for that host.
  //    2. If we don't find an account move on to our next strategy.
  //  2. Try to parse an owner/name.
  //    1. If that works, find the first account that can access it.
  //  3. And if all that fails then throw our hands in the air because we
  //     truly don't care.
```

**File:** app/src/lib/find-account.ts (L93-106)
```typescript
    for (const account of sortedAccounts) {
      if (hostname != null) {
        const htmlURL = URL.parse(getHTMLURL(account.endpoint))
        const accountHost = htmlURL.hostname
        if (accountHost !== hostname) {
          continue
        }
      }

      const canAccess = await canAccessRepository(account, owner, name)
      if (canAccess) {
        return account
      }
    }
```
