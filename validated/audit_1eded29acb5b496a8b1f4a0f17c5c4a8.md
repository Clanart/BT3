[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** app/src/main-process/main.ts (L39-39)
```typescript
import * as ipcMain from './ipc-main'
```

**File:** app/src/main-process/ipc-main.ts (L53-66)
```typescript
function safeListener<E extends IpcMainEvent | IpcMainInvokeEvent, R>(
  listener: (event: E, ...a: any) => R
) {
  return (event: E, ...args: any) => {
    if (!isTrustedIPCSender(event.sender)) {
      log.error(
        `IPC message received from invalid sender: ${event.senderFrame?.url}`
      )
      return
    }

    return listener(event, ...args)
  }
}
```

**File:** app/src/main-process/trusted-ipc-sender.ts (L9-16)
```typescript
/** Adds a WebContents instance to the set of trusted IPC senders. */
export const addTrustedIPCSender = (wc: WebContents) => {
  trustedSenders.add(wc.id)
  wc.on('destroyed', () => trustedSenders.delete(wc.id))
}

/** Returns true if the given WebContents is a trusted sender of IPC messages. */
export const isTrustedIPCSender = (wc: WebContents) => trustedSenders.has(wc.id)
```

**File:** app/src/main-process/shell.ts (L10-16)
```typescript
 * CAUTION: This method should never be used to open user-provided or derived
 * paths. It's sole use is to open _directories_ that we know to be safe, no
 * verification is performed to ensure that the provided path isn't actually
 * an executable.
 *
 * @param path directory to open
 */
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1972)
```typescript
  private async openRepositoryFromUrl(action: IOpenRepositoryFromURLAction) {
    const { url, pr, branch, filepath } = action

    let repository: Repository | null

    if (pr !== null) {
      repository = await this.openPullRequestFromUrl(url, pr)
    } else if (branch !== null) {
      repository = await this.openBranchNameFromUrl(url, branch)
    } else {
      repository = await this.openOrCloneRepository(url)
    }

    if (repository === null) {
      return
    }

    if (filepath !== null) {
      if (isAbsolute(filepath)) {
        log.error(`Refusing to open absolute path: ${filepath}`)
        return
      }

      const resolved = await resolveWithin(repository.path, filepath)

      if (resolved !== null) {
        shell.showItemInFolder(resolved)
      } else {
        log.error(
          `Prevented attempt to open path outside of the repository root: ${filepath}`
        )
      }
    }
```
