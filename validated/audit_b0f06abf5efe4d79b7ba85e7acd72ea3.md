[1](#0-0) [2](#0-1)

### Citations

**File:** app/src/lib/stores/helpers/find-upstream-remote.ts (L5-21)
```typescript
/** The name for a fork's upstream remote. */
export const UpstreamRemoteName = 'upstream'

/**
 * Find the upstream remote based on the parent repository and the list of
 * remotes.
 */
export function findUpstreamRemote(
  parent: GitHubRepository,
  remotes: ReadonlyArray<IRemote>
): IRemote | null {
  const upstream = remotes.find(r => r.name === UpstreamRemoteName)
  if (!upstream) {
    return null
  }

  return repositoryMatchesRemote(parent, upstream) ? upstream : null
```

**File:** app/src/lib/stores/git-store.ts (L1-1)
```typescript
import * as Path from 'path'
```
