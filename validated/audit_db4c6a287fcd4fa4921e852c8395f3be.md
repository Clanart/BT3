[1](#0-0) [2](#0-1)

### Citations

**File:** app/src/lib/stores/app-store.ts (L1-1)
```typescript
import * as Path from 'path'
```

**File:** app/src/models/remote.ts (L1-10)
```typescript
/**
 * This is the magic remote name prefix
 * for when we add a remote on behalf of
 * the user.
 */
export const ForkedRemotePrefix = 'github-desktop-'

export function forkPullRequestRemoteName(remoteName: string) {
  return `${ForkedRemotePrefix}${remoteName}`
}
```
