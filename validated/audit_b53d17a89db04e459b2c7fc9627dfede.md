[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** app/src/lib/git/fetch.ts (L92-101)
```typescript
export async function fetchRefspec(
  repository: Repository,
  remote: IRemote,
  refspec: string
): Promise<void> {
  await git(['fetch', remote.name, refspec], repository.path, 'fetchRefspec', {
    successExitCodes: new Set([0, 128]),
    env: await envForRemoteOperation(remote.url),
  })
}
```

**File:** app/src/lib/stores/git-store.ts (L1-1)
```typescript
import * as Path from 'path'
```

**File:** app/src/lib/stores/app-store.ts (L1-1)
```typescript
import * as Path from 'path'
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1-1)
```typescript
import { Disposable } from 'event-kit'
```
