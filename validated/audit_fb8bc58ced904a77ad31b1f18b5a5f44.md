[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** app/src/lib/find-account.ts (L55-69)
```typescript
  const parsedURL = parseRemote(urlOrRepositoryAlias)
  if (parsedURL) {
    const account =
      allAccounts.find(a => {
        const htmlURL = getHTMLURL(a.endpoint)
        const parsedEndpoint = URL.parse(htmlURL)
        return parsedURL.hostname === parsedEndpoint.hostname
      }) || null

    // If we find an account whose hostname matches the URL to be cloned, it's
    // always gonna be our best bet for success. We're not gonna do better.
    if (account) {
      return account
    }
  }
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

**File:** app/src/lib/repository-matching.ts (L33-43)
```typescript
  for (const account of accounts) {
    const htmlURL = getHTMLURL(account.endpoint)
    const { hostname } = URL.parse(htmlURL)
    const parsedRemote = parseRemote(remote)

    if (parsedRemote !== null && hostname !== null) {
      if (parsedRemote.hostname.toLowerCase() === hostname.toLowerCase()) {
        return { name: parsedRemote.name, owner: parsedRemote.owner, account }
      }
    }
  }
```

**File:** app/src/lib/remote-parsing.ts (L55-64)
```typescript
export function parseRemote(url: string): IGitRemoteURL | null {
  for (const { protocol, regex } of remoteRegexes) {
    const match = regex.exec(url)
    if (match !== null && match.length >= 4) {
      return { protocol, hostname: match[1], owner: match[2], name: match[3] }
    }
  }

  return null
}
```
