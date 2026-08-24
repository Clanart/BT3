### Title
Missing scheme allow-listing allows attacker-controlled `details_url`/`html_url` on check runs to be opened via `shell.openExternal`, enabling local file access via `file://` links clicked from the "Checks Failed" dialog - (File: `app/src/ui/notifications/pull-request-checks-failed.tsx`)

### Summary
Check-run data (`IRefCheck`, sourced from the GitHub Checks API `html_url`/`details_url` fields) is surfaced unmodified in the `PullRequestChecksFailed` dialog. When a user clicks "View on GitHub" or a job step, Desktop calls `dispatcher.openInBrowser(url)` with that attacker/API-influenced URL, all the way down to Electron's `shell.openExternal`, with no scheme allow-listing at any point in the chain.

### Finding Description
The flow from the checks-failed notification to the dialog and the eventual link-open action is:

1. `NotificationsStore.handleChecksFailedEvent` fetches check runs via `getChecksForRef` and invokes `this.onChecksFailedCallback?.(repository, pullRequest, checks)` unmodified. [1](#0-0) 

2. `Dispatcher.onChecksFailedNotification` forwards the same `checks: ReadonlyArray<IRefCheck>` to `appStore.onChecksFailedNotification`, which eventually shows the `PullRequestChecksFailed` popup with those checks as props — no field is sanitized or re-validated. [2](#0-1) 

3. In the dialog, `onViewOnGitHub` reads `checkRun.htmlUrl` directly (falling back to the PR URL only if `htmlUrl` is `null`) and passes it straight to `dispatcher.openInBrowser(url)` with no scheme check: [3](#0-2) 

Similarly, `onViewJobStep` builds a URL from `checkRun.htmlUrl` via `getCheckRunStepURL` and opens it the same way: [4](#0-3) [5](#0-4) 

4. `dispatcher.openInBrowser` → `appStore._openInBrowser` calls `shell.openExternal(url)` with the raw string, no validation: [6](#0-5) 

5. `shell.openExternal` (in `app-shell.ts`) is the `openExternal` IPC proxy, which invokes the `open-external` IPC channel: [7](#0-6) [8](#0-7) 

6. The main-process handler for `open-external` only *logs* when the URL starts with `http://`/`https://`, but does **not** reject or otherwise gate on scheme before calling Electron's `shell.openExternal(path)` — any scheme (including `file://` or a registered custom-protocol handler) is passed through unconditionally: [9](#0-8) 

This is in contrast to other URL-accepting entry points in the codebase that do enforce an `https:`-only allow-list, e.g. `validateURL` for GitHub Enterprise addresses and `isValidBYOKBaseUrl` for Copilot BYOK base URLs: [10](#0-9) [11](#0-10) 

No equivalent allow-list exists for `IRefCheck.htmlUrl` / job step URLs before they reach `shell.openExternal`.

### Impact Explanation
`checkRun.htmlUrl` and the related job-step URL are populated from the GitHub Checks API `html_url`/`details_url` fields, which a check-run creator (e.g. a GitHub App or CI integration with `checks:write` permission on the repository, or a malicious Actions workflow contributing a check run in an org/repo the victim has access to) can set to an arbitrary string, not necessarily an `https://github.com/...` URL. If such a value uses the `file://` scheme, `shell.openExternal('file:///path/to/local/file')` will cause the OS to open that local path with its default handler — which can disclose local file contents (e.g., opening a document with a text/PDF viewer) or, on Windows, execute a script/shortcut if the target extension is associated with an interpreter. If a custom-protocol handler is registered on the victim's OS (e.g., by another installed app), the crafted URL could invoke that handler's arbitrary behavior. This matches the "file read/write outside the repo" and potential code-execution class in the bounty's valid-impact list, contingent on the OS's default handler association for the crafted target.

### Likelihood Explanation
Exploitation requires the user to open the "Checks Failed" notification/dialog and click "View on GitHub" or a specific job step for a check run whose `html_url`/`details_url` was set to a malicious scheme by the check-run's creator. This is plausible for any repository where an attacker can create a check run pointing at that PR's commit (e.g., an org member with a bot/app token, or an Actions workflow with `checks:write`), and requires no unusual local access — only a single click on a link inside a legitimate-looking Desktop dialog, which is within the accepted "clicked links" attack surface for this program.

### Recommendation
Add scheme allow-listing before calling `shell.openExternal`/the `open-external` IPC handler — reject or refuse to open anything that is not `http:` or `https:` (mirroring `validateURL`/`isValidBYOKBaseUrl`). This should be enforced centrally in the `open-external` IPC handler in `app/src/main-process/main.ts` (rather than logging-only for http/https and passing everything else through), so that all call sites (including check-run/job-step URLs, PR comment/review URLs, etc.) are protected uniformly.

### Proof of Concept
1. As an actor able to create/update a check run on the target PR's head commit (e.g., via a GitHub App/service with `checks:write`, or crafting an Actions job), set the check run's `details_url` (surfaced to Desktop as `IRefCheck.htmlUrl`) to `file:///Users/victim/Desktop/secrets.txt` (or a Windows path `file:///C:/Users/victim/malicious.bat` if targeting execution via file association).
2. Trigger the "Pull Request checks failed" notification for that PR in Desktop; click the notification to invoke `dispatcher.onChecksFailedNotification`, opening `PullRequestChecksFailed`.
3. In the dialog, click "View on GitHub" (or select the check run's job step) — this calls `onViewOnGitHub`/`onViewJobStep` → `dispatcher.openInBrowser(url)`.
4. Observe that Desktop invokes `shell.openExternal('file:///Users/victim/Desktop/secrets.txt')` via the `open-external` IPC channel without scheme validation, causing the OS to open the local file/path instead of a browser page.

### Citations

**File:** app/src/lib/stores/notifications-store.ts (L401-405)
```typescript
    const onClick = () => {
      this.statsStore.increment('checksFailedNotificationClicked')

      this.onChecksFailedCallback?.(repository, pullRequest, checks)
    }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L4181-4187)
```typescript
  public onChecksFailedNotification(
    repository: RepositoryWithGitHubRepository,
    pullRequest: PullRequest,
    checks: ReadonlyArray<IRefCheck>
  ) {
    this.appStore.onChecksFailedNotification(repository, pullRequest, checks)
  }
```

**File:** app/src/ui/notifications/pull-request-checks-failed.tsx (L250-268)
```typescript
  private onViewJobStep = (step: IAPIWorkflowJobStep): void => {
    const { repository, pullRequest, dispatcher } = this.props
    const checkRun = this.selectedCheck

    if (checkRun === undefined) {
      return
    }

    const url = getCheckRunStepURL(
      checkRun,
      step,
      repository.gitHubRepository,
      pullRequest.pullRequestNumber
    )

    if (url !== null) {
      dispatcher.openInBrowser(url)
    }
  }
```

**File:** app/src/ui/notifications/pull-request-checks-failed.tsx (L375-390)
```typescript
  private onViewOnGitHub = (checkRun: IRefCheck) => {
    const { repository, pullRequest, dispatcher } = this.props

    // Some checks do not provide htmlURLS like ones for the legacy status
    // object as they do not have a view in the checks screen. In that case we
    // will just open the PR and they can navigate from there... a little
    // dissatisfying tho more of an edgecase anyways.
    const url =
      checkRun.htmlUrl ??
      `${repository.gitHubRepository.htmlURL}/pull/${pullRequest.pullRequestNumber}`
    if (url === null) {
      // The repository should have a htmlURL.
      return
    }
    dispatcher.openInBrowser(url)
  }
```

**File:** app/src/lib/ci-checks/ci-checks.ts (L570-588)
```typescript
export function getCheckRunStepURL(
  checkRun: IRefCheck,
  step: IAPIWorkflowJobStep,
  repository: GitHubRepository,
  pullRequestNumber: number
): string | null {
  if (checkRun.htmlUrl === null && repository.htmlURL === null) {
    // A check run may not have a url depending on how it is setup.
    // However, the repository should have one; Thus, we shouldn't hit this
    return null
  }

  const url =
    checkRun.htmlUrl !== null
      ? `${checkRun.htmlUrl}/#step:${step.number}:1`
      : `${repository.htmlURL}/pull/${pullRequestNumber}`

  return url
}
```

**File:** app/src/lib/stores/app-store.ts (L7595-7597)
```typescript
  public _openInBrowser(url: string): Promise<boolean> {
    return shell.openExternal(url)
  }
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

**File:** app/src/ui/main-process-proxy.ts (L146-146)
```typescript
export const openExternal = invokeProxy('open-external', 1)
```

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

**File:** app/src/ui/lib/enterprise-validate-url.ts (L32-42)
```typescript
  if (!url.protocol) {
    const error = new Error('Invalid URL')
    error.name = InvalidURLErrorName
    throw error
  }

  if (url.protocol !== 'https:') {
    const error = new Error('Invalid protocol')
    error.name = InvalidProtocolErrorName
    throw error
  }
```

**File:** app/src/lib/copilot/byok.ts (L237-250)
```typescript
export function isValidBYOKBaseUrl(value: string): boolean {
  try {
    const parsed = new URL(value)
    if (parsed.protocol === 'https:') {
      return true
    }
    if (parsed.protocol === 'http:' && isLocalBaseUrl(value)) {
      return true
    }
    return false
  } catch {
    return false
  }
}
```
