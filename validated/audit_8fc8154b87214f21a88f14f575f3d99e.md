[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** app/src/lib/pull-request-refs.ts (L25-29)
```typescript
 * Reuses the shared {@linkcode IssueReference} pattern but, unlike the
 * mention-linkifier, only accepts *bare* same-repo references: we drop
 * cross-repo `owner/repo#N` and URL-style (`/issues/`, `/pull/`) markers
 * because callers resolve these numbers against the *current* repository's
 * pull requests, so another repo's `#1` would be a wrong-repo false match.
```

**File:** app/src/lib/pull-request-refs.ts (L52-59)
```typescript
        // Only bare, same-repo references via `#`/`gh-`; skip cross-repo
        // prefixes and URL-style markers we can't safely resolve here.
        if (ownerOrOwnerRepo !== undefined) {
          continue
        }
        if (marker !== '#' && marker?.toLowerCase() !== 'gh-') {
          continue
        }
```

**File:** app/src/lib/pull-request-refs.ts (L78-98)
```typescript
export function findPullRequestsByNumbers(
  numbers: ReadonlyArray<number>,
  pullRequests: ReadonlyArray<PullRequest>
): ReadonlyArray<PullRequest> {
  if (numbers.length === 0 || pullRequests.length === 0) {
    return []
  }

  const byNumber = new Map<number, PullRequest>()
  for (const pr of pullRequests) {
    byNumber.set(pr.pullRequestNumber, pr)
  }

  const result: Array<PullRequest> = []
  for (const prNumber of numbers) {
    const match = byNumber.get(prNumber)
    if (match !== undefined) {
      result.push(match)
    }
  }
  return result
```
