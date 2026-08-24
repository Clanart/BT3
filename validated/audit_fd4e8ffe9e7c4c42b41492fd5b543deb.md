[1](#0-0)

### Citations

**File:** app/src/lib/repository-matching.ts (L137-148)
```typescript
export function urlsMatch(url1: string, url2: string) {
  const firstIdentifier = parseRepositoryIdentifier(url1)
  const secondIdentifier = parseRepositoryIdentifier(url2)

  return (
    firstIdentifier !== null &&
    secondIdentifier !== null &&
    firstIdentifier.hostname === secondIdentifier.hostname &&
    firstIdentifier.owner === secondIdentifier.owner &&
    firstIdentifier.name === secondIdentifier.name
  )
}
```
