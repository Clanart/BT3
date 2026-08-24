## #Valid — the `http/https` check is cosmetic, not a gate

### Title
Insufficient scheme validation in `open-external` IPC handler allows `shell.openExternal` to be invoked with arbitrary URI schemes (e.g. `file://`, custom protocol handlers) sourced from attacker-controlled check-run/job-step URLs - (File: `app/src/main-process/main.ts`)

### Summary
The `open-external` IPC handler checks whether the URL starts with `http://`/`https://`, but that check is only used to decide whether to write a log line — it does **not** gate the call to `shell.openExternal(path)`, which executes unconditionally regardless of the outcome of the check.

### Finding Description
The handler is: [1](#0-0) 

```
ipcMain.handle('open-external', async (_, path: string) => {
  const pathLowerCase = path.toLowerCase()
  if (
    pathLowerCase.startsWith('http://') ||
    pathLowerCase.startsWith('https://')
  ) {
    log.info(`opening in browser: ${path}`)
  }

  try {
    await shell.openExternal(path)
    return true
  } catch (e) {
    log.error(`Call to openExternal failed: '${e}'`)
    return false
  }
})
```

The `if` block has no `else`/`return`; it is purely informational logging. Whatever scheme `path` has — `http://`, `https://`, `file://`, `javascript:`, or any OS-registered custom protocol (`myapp://`, `ms-word:`, etc.) — execution falls through to `shell.openExternal(path)` unconditionally. There is no allow-list, no scheme validation, and no rejection path.

This handler is reachable from the renderer via `openExternal = invokeProxy('open-external', 1)` [2](#0-1) , exposed as `shell.openExternal` in `app-shell.ts` [3](#0-2) , and ultimately called by `dispatcher.openInBrowser(url)` for check-run and job-step URLs: [4](#0-3) 

```
private onViewJobStep = (
  checkRun: IRefCheck,
  step: IAPIWorkflowJobStep
): void => {
  const { repository, prNumber, dispatcher } = this.props

  const url = getCheckRunStepURL(checkRun, step, repository, prNumber)

  if (url !== null) {
    dispatcher.openInBrowser(url)
    this.props.dispatcher.incrementMetric('viewsCheckJobStepOnline')
  }
}
```

`getCheckRunStepURL` and `checkRun.htmlUrl` are derived from GitHub Check Run API objects (`details_url`/step data), which are values supplied by whatever GitHub App/CI integration reported the check run — not values that GitHub Desktop itself constructs or validates against a scheme allow-list.

### Impact Explanation
If an attacker can get a non-`http(s)` URL into a check run's `htmlUrl`/step URL (or any other caller of `shell.openExternal`/`dispatcher.openInBrowser` that passes attacker-influenced strings), clicking "View check details" / "View job step" in Desktop causes the OS shell to resolve and act on that URI. Depending on OS and installed handlers, `shell.openExternal` can:
- Open/execute local files via `file://` (Windows historically has had RCE issues with `shell.openExternal` and executable file paths/UNC paths).
- Invoke any registered custom protocol handler with attacker-supplied data, which is a known vector for triggering unintended actions in other installed applications (protocol handler injection).

This is a real "click a malicious link surfaced by the app" scenario the review scope explicitly considers in-scope ("clicked links ... attacker controls ... an API object").

### Likelihood Explanation
Exploitability depends on whether GitHub's Checks API actually allows `details_url` (or job step URLs) to be set to a non-`http(s)` value by a third-party Check-Author app — this could not be confirmed from the client code alone, since Desktop performs no validation and simply trusts whatever came back from the API/model layer. Regardless of that external constraint, the code-level defect is clear and unambiguous: the scheme check in `main.ts` is dead code with respect to security — it affects only logging, not control flow.

### Recommendation
Make the prefix check actually gate the call, e.g.:

```
if (!pathLowerCase.startsWith('http://') && !pathLowerCase.startsWith('https://')) {
  log.error(`Blocked attempt to open external URL with disallowed scheme: '${path}'`)
  return false
}
log.info(`opening in browser: ${path}`)
await shell.openExternal(path)
```

Additionally, validate/normalize check-run and job-step URLs (`getCheckRunStepURL`, `checkRun.htmlUrl`) to only allow `https://` URLs on trusted hosts before passing them to `dispatcher.openInBrowser`.

### Proof of Concept
From the renderer devtools console (or any code path that can reach `main-process-proxy.ts`):
```js
await require('electron').ipcRenderer.invoke('open-external', 'file:///etc/passwd')
```
or on Windows:
```js
await require('electron').ipcRenderer.invoke('open-external', 'file://C:/Windows/System32/calc.exe')
```
Tracing `main.ts:581-597` shows `shell.openExternal` is called unconditionally in both cases — the `if` block only affects whether `log.info` fires, confirming the handler does not reject non-`http(s)` schemes before invoking `shell.openExternal`.

### Citations

**File:** app/src/main-process/main.ts (L581-597)
```typescript
  ipcMain.handle('open-external', async (_, path: string) => {
    const pathLowerCase = path.toLowerCase()
    if (
      pathLowerCase.startsWith('http://') ||
      pathLowerCase.startsWith('https://')
    ) {
      log.info(`opening in browser: ${path}`)
    }

    try {
      await shell.openExternal(path)
      return true
    } catch (e) {
      log.error(`Call to openExternal failed: '${e}'`)
      return false
    }
  })
```

**File:** app/src/ui/main-process-proxy.ts (L146-146)
```typescript
export const openExternal = invokeProxy('open-external', 1)
```

**File:** app/src/lib/app-shell.ts (L43-53)
```typescript
export const shell: IAppShell = {
  // Since Electron 13, shell.trashItem doesn't work from the renderer process
  // on Windows. Therefore, we must invoke it from the main process. See
  // https://github.com/electron/electron/issues/29598
  moveItemToTrash,
  beep: electronShell.beep,
  openExternal,
  showItemInFolder,
  showFolderContents,
  openPath: electronShell.openPath,
}
```

**File:** app/src/ui/check-runs/ci-check-run-popover.tsx (L171-183)
```typescript
  private onViewJobStep = (
    checkRun: IRefCheck,
    step: IAPIWorkflowJobStep
  ): void => {
    const { repository, prNumber, dispatcher } = this.props

    const url = getCheckRunStepURL(checkRun, step, repository, prNumber)

    if (url !== null) {
      dispatcher.openInBrowser(url)
      this.props.dispatcher.incrementMetric('viewsCheckJobStepOnline')
    }
  }
```
