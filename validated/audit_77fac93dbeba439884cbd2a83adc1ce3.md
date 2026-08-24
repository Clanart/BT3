[1](#0-0) [2](#0-1)

### Citations

**File:** app/src/lib/git/remote.ts (L29-37)
```typescript
export async function addRemote(
  repository: Repository,
  name: string,
  url: string
): Promise<IRemote> {
  await git(['remote', 'add', name, url], repository.path, 'addRemote')

  return { url, name }
}
```

**File:** app/src/lib/git/remote.ts (L95-112)
```typescript
export async function updateRemoteHEAD(
  repository: Repository,
  remote: IRemote,
  isBackgroundTask: boolean
): Promise<void> {
  const options = {
    successExitCodes: new Set([0, 1, 128]),
    env: await envForRemoteOperation(remote.url),
    isBackgroundTask,
  }

  await git(
    ['remote', 'set-head', '-a', remote.name],
    repository.path,
    'updateRemoteHEAD',
    options
  )
}
```
