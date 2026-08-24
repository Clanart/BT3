[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [2](#0-1) [5](#0-4)

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

**File:** app/src/main-process/authenticated-image-filter.ts (L50-53)
```typescript
  return (accounts: ReadonlyArray<EndpointToken>) => {
    originTokens = new Map(
      accounts.map(({ endpoint, token }) => [new URL(endpoint).origin, token])
    )
```

**File:** app/src/ui/lib/sandboxed-markdown.tsx (L392-399)
```typescript
        <iframe
          title="sandboxed-markdown-component"
          className="sandboxed-markdown-component"
          sandbox="allow-same-origin"
          ref={this.onFrameRef}
          onLoad={this.refreshHeight}
          aria-label={this.props.ariaLabel}
        />
```

**File:** app/src/main-process/same-origin-filter.ts (L54-72)
```typescript
  orderedWebRequest.onBeforeSendHeaders.addEventListener(async details => {
    const initialOrigin = requestOrigin.get(details.id)
    const { origin } = new URL(details.url)

    if (initialOrigin === undefined || initialOrigin === origin) {
      return { requestHeaders: details.requestHeaders }
    }

    const sanitizedHeaders: Record<string, string> = {}

    for (const [k, v] of Object.entries(details.requestHeaders)) {
      if (!unsafeHeaders.has(k.toLowerCase())) {
        sanitizedHeaders[k] = v
      }
    }

    log.debug(`Sanitizing cross-origin redirect to ${origin}`)
    return { requestHeaders: sanitizedHeaders }
  })
```
