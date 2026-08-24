[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** app/src/lib/find-account.ts (L57-62)
```typescript
    const account =
      allAccounts.find(a => {
        const htmlURL = getHTMLURL(a.endpoint)
        const parsedEndpoint = URL.parse(htmlURL)
        return parsedURL.hostname === parsedEndpoint.hostname
      }) || null
```

**File:** app/src/lib/find-account.ts (L93-100)
```typescript
    for (const account of sortedAccounts) {
      if (hostname != null) {
        const htmlURL = URL.parse(getHTMLURL(account.endpoint))
        const accountHost = htmlURL.hostname
        if (accountHost !== hostname) {
          continue
        }
      }
```

**File:** app/src/ui/clone-repository/clone-repository.tsx (L732-753)
```typescript
  private async resolveCloneInfo(): Promise<IAPIRepositoryCloneInfo | null> {
    const { url, lastParsedIdentifier } = this.getSelectedTabState()

    if (url.endsWith('.wiki.git')) {
      return { url }
    }

    const account = await findAccountForRemoteURL(url, this.props.accounts)
    if (lastParsedIdentifier !== null && account !== null) {
      const api = API.fromAccount(account)
      const { owner, name } = lastParsedIdentifier
      // Respect the user's preference if they provided an SSH URL
      const protocol = parseRemote(url)?.protocol

      return api.fetchRepositoryCloneInfo(owner, name, protocol).catch(err => {
        log.error(`Failed to look up repository clone info for '${url}'`, err)
        return { url }
      })
    }

    return { url }
  }
```
