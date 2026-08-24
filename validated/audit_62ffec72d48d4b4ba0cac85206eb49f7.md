### Title
`open-external` IPC handler performs a cosmetic protocol check but allows `shell.openExternal()` on any string, letting attacker-controlled GitHub content invoke arbitrary registered URL-protocol handlers - ([File: app/src/main-process/main.ts])

### Summary
The 0x report's broken invariant is: a single narrow validation (`sig == 0x415565b0`) is used as a gate, but once it passes, the powerful downstream primitive (`transformERC20`, which can call arbitrary registered transformer addresses) is invoked with no further restriction, and the set of "allowed" targets can grow silently over time (new transformers). The same shape exists in GitHub Desktop's external-link opening path: a single, cosmetic check ("does this look like http/https, just for logging") gates nothing, and the powerful, OS-level primitive `shell.openExternal()` — which can invoke *any* registered custom URL-protocol handler on the system, not just a browser — is invoked unconditionally with the string.

### Finding Description
The main-process IPC handler for `open-external` is: [1](#0-0) 

```
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
```

The `http/https` check here is purely for a log message — it is not a guard. If the string does **not** start with `http(s)://`, execution simply falls through to the exact same `shell.openExternal(path)` call. This is structurally identical to the 0x `canCall()` bug: the check that exists is not actually enforcing anything about what happens next; "if it passes, all calls are allowed... if it fails, [the same thing happens] without further validation."

`shell.openExternal` is a broad Electron/OS primitive: on Windows and macOS it will hand the string off to the OS's registered protocol-handler resolution, which can invoke arbitrary installed applications registered for custom URL schemes (e.g. `mailto:`, custom app schemes registered by other installed software, or in older Electron versions, even execute local file paths via `file:`/UNC-style strings). This is the same class of primitive as 0x's transformer system: a fixed, minimal check up front, followed by delegation to an open-ended, extensible set of "handlers" that Desktop does not control and cannot enumerate.

The renderer-side proxy for this channel is a thin, typed wrapper with no validation of its own: [2](#0-1) 

The one place that *does* correctly gate protocol before calling `dispatcher.openInBrowser`/`shell.openExternal` is the sandboxed markdown link interceptor, which explicitly checks `/^https?:/.test(a.protocol)` before firing the callback: [3](#0-2) 

However, this check exists **only** in that one call site. There are numerous other callers of `dispatcher.openInBrowser`/`_openInBrowser` throughout the UI (menu items, preferences, PR check-run popovers, notifications, branch dropdown, repository settings, etc.) that pass URLs sourced from GitHub API objects or other repository-controlled content, and none of these callers re-validate the scheme before the string reaches the unguarded `open-external` IPC handler: [4](#0-3) 

Because the actual security boundary (main process IPC handler) does not enforce the http/https restriction, it relies entirely on every renderer call site independently doing the right thing — which is exactly the fragile "allow by default, no allowlist" pattern the report criticizes for 0x.

### Impact Explanation
If any attacker-controlled string (a GitHub API field, PR/issue title, check-run details URL, or other externally-fetched content) reaches `dispatcher.openInBrowser`/`shell.openExternal(...)` without being pre-filtered to `http(s)`, the `open-external` main-process handler will pass it straight to `shell.openExternal`. Depending on OS and installed software, this can:
- Invoke another installed application's custom URL-scheme handler with attacker-chosen data (parameter injection into that handler, potentially leading to code execution in that other app), or
- Trigger unexpected OS actions from a scheme Desktop never intended to support, since there is no allowlist of acceptable schemes at the trust boundary (main process), only an opportunistic check that doesn't gate anything.

This matches the report's "New transformers are allowed by default" theme: Desktop's `open-external` handler will happily execute any future or third-party-installed protocol handler without the Desktop team ever reviewing or approving it, exactly as 0x would silently accept any newly-deployed transformer.

### Likelihood Explanation
Exploitability depends on finding a call site where a non-`http(s)` string sourced from untrusted repository/API content reaches `dispatcher.openInBrowser` without going through the `sandboxed-markdown.tsx` interceptor's protocol check. I was not able to fully audit every one of the ~15 call sites found (`app-store.ts`, `app.tsx`, `ci-check-run-popover.tsx`, `pull-request-checks-failed.tsx`, `preferences.tsx`, `branch-dropdown.tsx`, `repository-settings.tsx`, etc.) in the time available to confirm whether any of them pass raw, unsanitized attacker-controlled strings. This is the key uncertainty: the vulnerable *sink* (`main.ts` `open-external` handler) is confirmed and unguarded, but I could not conclusively trace a fully attacker-controlled *source* into it within this session. A background Devin session with full file access should audit each `openInBrowser` call site to confirm whether an untrusted value (e.g., a check-run `detailsUrl`, PR body-derived href not going through `SandboxedMarkdown`, etc.) is passed directly.

### Recommendation
Move the protocol allowlist enforcement into the main-process trust boundary itself rather than relying on caller discipline:
```
ipcMain.handle('open-external', async (_, path: string) => {
  const isAllowed = /^https?:\/\//i.test(path) || /^mailto:/i.test(path)
  if (!isAllowed) {
    log.warn(`Refusing to open external link with disallowed scheme: ${path}`)
    return false
  }
  ...
})
```
This makes the check in `main.ts` an actual gate (matching the report's recommendation of an allowlist for known-safe targets) instead of a log-only no-op, and removes dependence on every renderer call site independently re-implementing the `https?` check that currently only exists in `sandboxed-markdown.tsx`.

### Proof of Concept
Conceptual PoC (pending confirmation of a fully attacker-controlled source call site):
1. A GitHub Enterprise/attacker-controlled repository or PR exposes a field (e.g., a check-run "details URL", or a non-markdown-rendered API-provided link) containing a non-`http(s)` URI, e.g. `some-other-app://payload?arg=...`.
2. That value flows into a call site invoking `dispatcher.openInBrowser(url)` / `dispatcher._openInBrowser(url)` (e.g. [5](#0-4) ) without the `/^https?:/` gate used in `sandboxed-markdown.tsx`.
3. This reaches `shell.openExternal(url)` via the IPC round-trip `main-process-proxy.ts:openExternal` → `main.ts` `open-external` handler, which performs no real validation.
4. `shell.openExternal` hands the URI to the OS, which resolves it against any locally registered custom protocol handler, executing that handler with attacker-supplied parameters. [1](#0-0)

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

**File:** app/src/lib/stores/app-store.ts (L7594-7597)
```typescript
  /** Takes a URL and opens it using the system default application */
  public _openInBrowser(url: string): Promise<boolean> {
    return shell.openExternal(url)
  }
```

**File:** app/src/ui/check-runs/ci-check-run-popover.tsx (L1-1)
```typescript
import * as React from 'react'
```
