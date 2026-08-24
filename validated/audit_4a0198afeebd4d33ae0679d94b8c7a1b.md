## Analysis

The Sherlock finding is about a broken invariant: the code retrieves a validity signal (`updatedAt`) from an external/untrusted data source but never uses it to gate the subsequent security-relevant computation — the price is used regardless of whether the underlying data is fresh/valid.

The closest structural analog in GitHub Desktop is the `open-external` IPC handler in the main process: it computes a scheme classification for a URL supplied by the (untrusted) renderer, but that classification is used **only for logging** — the privileged action (`shell.openExternal`) is executed unconditionally regardless of the outcome of the check.

### Title
Renderer-controlled URLs are passed to `shell.openExternal` without enforcing a scheme allow-list, enabling OS-level command execution via crafted deep links/API content - (File: `app/src/main-process/main.ts`)

### Summary
The `open-external` IPC handler in the Electron main process inspects whether a URL starts with `http://` or `https://`, but that check is only used to decide whether to write an info log line. The actual privileged call, `shell.openExternal(path)`, executes on the raw, unvalidated `path` string for **any** scheme, exactly mirroring the audited bug where a freshness/validity value is fetched but never used to gate the dependent calculation.

### Finding Description [1](#0-0) 

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
    ...
```

The `pathLowerCase` classification is computed and then discarded — there is no `else` branch, no `return false`, and no throw for values that don't start with `http(s)://`. Every string reaching this handler, regardless of scheme (`file:`, custom registered protocol handlers, UNC-style paths, etc.), is forwarded straight to Electron's `shell.openExternal`, which asks the OS shell to open it with whatever handler is registered for that scheme/target.

`shell` (the renderer-facing wrapper) exposes `openExternal` directly to UI code with no scheme validation of its own: [2](#0-1) . Several call sites feed this with values that ultimately originate from renderer content that can be influenced by fetched GitHub API objects or user-clicked links, e.g. `LinkButton.onClick`, which calls `shell.openExternal(uri)` for whatever `uri` prop it is given, with **no protocol check at all**: [3](#0-2) . Markdown link handling (`sandboxed-markdown.tsx`) does perform an `/^https?:/` test before invoking its callback, but that callback (`onMarkdownLinkClicked`) simply forwards straight to `shell.openExternal` again: [4](#0-3) ; not every consumer of `LinkButton`/`openExternal` in the codebase is guaranteed to apply that same filter, and the final enforcement point — the main-process IPC handler — does not itself enforce it either, so any renderer-side gap becomes exploitable.

### Impact Explanation
Because the final gate (the main-process handler) doesn't actually restrict schemes, any code path in the renderer that can construct/relay a URL is a potential trigger for `shell.openExternal` on an attacker-chosen non-http(s) target. Historically, `shell.openExternal` with unsanitized input has been used to escape Electron sandboxes and execute code, because certain OS-registered URI schemes/handlers (e.g., legacy `file://` UNC paths, `search-ms:`, other third-party protocol handlers) can be abused to run arbitrary local binaries or scripts when invoked with attacker-controlled arguments. This matches the "silent corruption"/"code execution via link the user clicks or a GitHub API object" impact category: the attacker doesn't need any special privilege beyond getting a URL in front of the app (e.g., a PR/issue body or notification link), and the intended safety check exists in the code but is never actually enforced against the dangerous operation — precisely as in the wstETH oracle bug where the "guard variable" (`updatedAt`) is present but not checked.

### Likelihood Explanation
Reachability is only partially provable from the index: `sandboxed-markdown.tsx` does add a scheme test before calling its `onMarkdownLinkClicked` prop, and I could not exhaustively confirm every renderer call site that ultimately calls `shell.openExternal`/`ipcRenderer.invoke('open-external', ...)`. `LinkButton`, however, demonstrably performs no scheme validation before opening the URI, and the main-process handler — the last line of defense — also fails to enforce one. This makes it plausible but not fully confirmed from the visible code that a fully unauthenticated non-http(s) URL can reach `shell.openExternal`; a background agent with full repo access should audit all `uri`/`path` producers feeding `LinkButton` and the `open-external` IPC channel to confirm an end-to-end reachable path (e.g., from a PR/issue comment link, a release-notes URL, or a custom protocol deep link).

### Recommendation
Make the scheme check in the `open-external` main-process handler authoritative rather than advisory: reject (return `false`/throw) for any `path` that doesn't start with `http://` or `https://` before calling `shell.openExternal`, and add the same allow-list check inside `LinkButton.onClick` and any other renderer call sites that pass externally-sourced strings to `shell.openExternal`, so validation isn't solely dependent on each caller remembering to filter.

### Proof of Concept
Conceptual PoC (not fully verified against live behavior, since the index doesn't expose the complete list of renderer call sites):
1. An attacker crafts a GitHub PR/issue comment, or a `x-github-client://` style deep-link payload, containing a link whose `href`/`uri` is a non-`http(s)` scheme registered with a vulnerable local application (or a legacy `file:`/UNC-style path).
2. If that value reaches a `LinkButton` (no scheme check) or otherwise reaches `shell.openExternal`/`ipcRenderer.invoke('open-external', ...)` without going through the markdown-specific `/^https?:/` filter, it is passed to the main-process handler.
3. `app/src/main-process/main.ts:581-597` performs its scheme test purely for logging and unconditionally calls `shell.openExternal(path)`, invoking the OS's handler for that scheme, potentially causing local code execution outside the app's own security boundary.

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

**File:** app/src/ui/lib/link-button.tsx (L76-92)
```typescript
  private onClick = (event: React.MouseEvent<HTMLAnchorElement>) => {
    event.preventDefault()

    if (this.props.disabled) {
      return
    }

    const uri = this.props.uri
    if (uri) {
      shell.openExternal(uri)
    }

    const onClick = this.props.onClick
    if (onClick) {
      onClick()
    }
  }
```

**File:** app/src/ui/release-notes/release-notes-dialog.tsx (L206-208)
```typescript
  private onMarkdownLinkClicked = (url: string) => {
    shell.openExternal(url)
  }
```
