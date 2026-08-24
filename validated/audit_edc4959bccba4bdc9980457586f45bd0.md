### Title
Unvalidated `shell.openExternal` calls with attacker-controlled GitHub API URLs allow arbitrary URI-scheme invocation - (File: `app/src/main-process/main.ts`)

### Summary
GitHub Desktop's `open-external` IPC handler forwards any string the renderer supplies straight to Electron's `shell.openExternal`, without restricting the scheme to `http`/`https`. Several UI surfaces call `dispatcher.openInBrowser()` (which ends up at this handler) using URLs taken directly from GitHub API objects (e.g. CI check-run URLs, PR URLs) that originate from a repository a user has cloned, fetched, or otherwise interacted with — including forks and third-party/Enterprise endpoints. Unlike the sandboxed-markdown link path, which explicitly checks `/^https?:/.test(a.protocol)` before ever invoking a link-click callback, these API-driven callers perform no scheme validation, so a malicious API response/CI provider can smuggle a non-http URI to `shell.openExternal`.

### Finding Description
The IPC handler in `app/src/main-process/main.ts` only *logs* when the scheme is http/https but does not reject other schemes before calling `shell.openExternal`: [1](#0-0) 

This handler is reached via `app/src/lib/app-shell.ts`'s `openExternal` export, which is used as `shell.openExternal` throughout the renderer: [2](#0-1) 

`Dispatcher.openInBrowser` / `AppStore._openInBrowser` route straight to `shell.openExternal(url)`: [3](#0-2) 

Multiple UI components call this with URLs sourced from GitHub API objects rather than from user-typed input, e.g. check-run popovers, failed-check notifications, and the branch/PR dropdown — none of which validate the URL scheme before calling `openInBrowser`: [4](#0-3) 

By contrast, the one place in the codebase that *does* handle untrusted, repo-originated link content (rendered Markdown from issues/PR bodies/commit messages) explicitly gates the callback on the link's protocol before it's even handed to `openInBrowser`: [5](#0-4) 

This shows the project is aware that externally-sourced URLs need scheme filtering, but that guard is not applied uniformly — it protects Markdown-rendered links only, not URLs pulled directly from API response fields (such as a check run's `details_url`/`html_url`) that a malicious CI workflow, GitHub App, or Enterprise-server-in-the-middle can control.

### Impact Explanation
`shell.openExternal` in Electron has repeatedly been the vector for OS command execution/injection via crafted URI schemes (e.g., `microsoft-edge:`, `search-ms:`, or SMB/other custom-protocol handlers registered on the victim's machine) when the scheme is not restricted to `http(s)`. Because Desktop’s `open-external` IPC handler does not enforce this restriction, any surface that forwards a non-user-typed, externally influenced URL (CI check-run URL, PR object URL, etc.) to `openInBrowser` inherits this weakness, potentially leading to unintended program execution or, on vulnerable OS/registered-handler combinations, code execution outside the app's sandbox — matching the "unprivileged issue where the attacker controls a GitHub API object" impact class.

### Likelihood Explanation
Likelihood is moderate: it requires a malicious PR/fork/CI integration (or a compromised/malicious Enterprise/proxy endpoint) to populate a check-run or PR field with a crafted non-`https` URI, and requires the victim to click the resulting UI element (e.g., "View check run" in `ci-check-run-popover.tsx` or a failed-check notification). No local access, admin rights, or prior malware is needed — only attacker control over data returned by the GitHub API/CI provider for a repository the victim has added to Desktop, and normal user interaction with the Desktop UI.

### Recommendation
Enforce scheme validation centrally, at the `open-external` IPC handler in `app/src/main-process/main.ts` (or in `app/src/ui/main-process-proxy.ts`'s `openExternal` wrapper), rejecting any URL whose protocol is not `http:`/`https:` before calling `shell.openExternal`, mirroring the check already used in `setupLinkInterceptor` in `app/src/ui/lib/sandboxed-markdown.tsx`. This closes the gap for all callers (check-run popovers, PR notifications, branch dropdown, etc.) instead of relying on each call site to individually validate the URL.

### Proof of Concept
1. Fork a repository and configure a CI workflow/check whose reported check-run `details_url`/`html_url` is set to a crafted non-`https` URI (e.g., a Windows shell-handler URI known to trigger unintended program execution via `shell.openExternal`).
2. Open a pull request from the fork against a repository the victim has open in GitHub Desktop.
3. Victim opens the PR's checks in Desktop and clicks "View check run" / the failed-check notification link, which invokes `dispatcher.openInBrowser(checkRun.htmlUrl)` (`app/src/ui/check-runs/ci-check-run-popover.tsx`, `app/src/ui/notifications/pull-request-checks-failed.tsx`).
4. The URL reaches `shell.openExternal` in `app/src/main-process/main.ts` unchecked, triggering whatever behavior the crafted URI scheme causes on the victim's OS.

Note: I could not fully trace the exact field (`checkRun.htmlUrl`/`detailsUrl`) back to its raw API deserialization point within the indexing limits of this session to confirm there is no upstream sanitization of the scheme; a full audit of `app/src/lib/ci-check-run.ts`/API mapping code and the `ci-check-run-popover.tsx` component would be needed to conclusively rule out an existing guard there. Given index coverage limits, a Devin session with full repo access is recommended to verify this end-to-end before treating it as confirmed exploitable.

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

**File:** app/src/lib/stores/app-store.ts (L7595-7597)
```typescript
  public _openInBrowser(url: string): Promise<boolean> {
    return shell.openExternal(url)
  }
```

**File:** app/src/ui/notifications/pull-request-comment-like.tsx (L162-164)
```typescript
  private onMarkdownLinkClicked = (url: string) => {
    this.props.dispatcher.openInBrowser(url)
  }
```

**File:** app/src/ui/lib/sandboxed-markdown.tsx (L292-305)
```typescript
  private setupLinkInterceptor(doc: Document): void {
    doc.addEventListener('click', ev => {
      if (doc.defaultView && ev.target instanceof doc.defaultView.Element) {
        const a = ev.target.closest('a')
        if (a !== null) {
          ev.preventDefault()

          if (/^https?:/.test(a.protocol)) {
            this.props.onMarkdownLinkClicked?.(a.href)
          }
        }
      }
    })
  }
```
