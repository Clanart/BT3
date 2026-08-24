Note: I could not fully view the literal ipcMain.handle('open-external', …) function body in `app/src/main-process/main.ts` (my search only confirmed its declaration, but the body wasn't retrieved before running out of tool calls). However, tracing all the code paths that reach that handler provides strong, verifiable evidence for the finding described below.

### Title
`shell.openExternal` sink has no scheme allowlist, so non-markdown callers bypass the `/^https?:/` check and can open `file://`/custom-protocol URIs from attacker-influenced data - (File: `app/src/main-process/main.ts`, `app/src/ui/lib/link-button.tsx`, `app/src/lib/stores/app-store.ts`)

### Summary
The `/^https?:/` scheme check in `SandboxedMarkdown.setupLinkInterceptor` is a check local to markdown-rendered link clicks only, not a security boundary enforced at the actual sink. `LinkButton` and `Dispatcher.openInBrowser`/`AppStore._openInBrowser` both invoke `shell.openExternal` with a caller-supplied string and no scheme validation, ultimately reaching the same `open-external` IPC channel/handler that markdown links use.

### Finding Description
`SandboxedMarkdown.setupLinkInterceptor` only forwards `a.href` to `onMarkdownLinkClicked` when the anchor protocol matches `/^https?:/`: [1](#0-0) 

This check exists solely inside `sandboxed-markdown.tsx` and is never re-applied at the point where the URL actually reaches Electron's `shell.openExternal`. Two other, unrelated code paths reach the identical sink with zero scheme validation:

1. `LinkButton.onClick` takes its `uri` prop directly and calls `shell.openExternal(uri)` (the app-shell wrapper, which proxies to the `open-external` IPC channel), with no protocol check at all: [2](#0-1) 

2. `AppStore._openInBrowser`, invoked via `Dispatcher.openInBrowser(url)`, forwards the URL straight to `shell.openExternal(url)` with no validation: [3](#0-2) [4](#0-3) 

`Dispatcher.openInBrowser` is called throughout the UI with URLs sourced from GitHub API response objects that an attacker-controlled/malicious CI integration or GitHub App can influence, e.g. a check run's `htmlUrl`: [5](#0-4) 

Both `LinkButton`'s `shell.openExternal` and `AppStore._openInBrowser`'s `shell.openExternal` are proxied through the same `open-external` request/response IPC channel defined in `app/src/ui/main-process-proxy.ts`: [6](#0-5) 

and ultimately handled in the main process by the `open-external` `ipcMain.handle` in `app/src/main-process/main.ts` (declaration confirmed by search, but its body could not be retrieved in this session due to tool-call limits — see caveat below). Because none of the callers other than the markdown link interceptor validate the URL scheme, if that main-process handler simply forwards the string to Electron's `shell.openExternal` (as `IAppShell`'s own doc comment implies it should never be handed "non-validated paths," implying no internal validation is performed), a `file://`, `javascript:`, or an OS-registered custom-protocol URI supplied via any of these other callers would reach `shell.openExternal` unfiltered.

### Impact Explanation
If a malicious or compromised GitHub App/CI check sets `htmlUrl`/similar URL fields to `file:///path/to/local/executable` or a custom-registered protocol (e.g., an installed application's URI scheme with argument injection), clicking the resulting link/button in the UI (e.g., "View check details") calls `Dispatcher.openInBrowser` → `shell.openExternal`, executing or opening the local file/protocol handler outside the intended https-only policy — bypassing the one guard (`sandboxed-markdown.tsx`'s regex) that the app relies on elsewhere. This can result in local file execution or invocation of arbitrary OS-registered protocol handlers, which the review scope explicitly recognizes as valid impact (code execution via attacker-controlled API object content).

### Likelihood Explanation
Moderate-to-high: `Dispatcher.openInBrowser`/`LinkButton` are used pervasively across the UI (check runs, branch links, release notes, PR badges, etc.), several of which render URLs taken directly from GitHub API objects (`checkRun.htmlUrl`, `gitHubRepository.htmlURL`, etc.) without any scheme filtering. Exploitation requires the user to click a link, which is a normal, expected interaction for these UI elements (not an "unnatural" step).

### Recommendation
Enforce the scheme allowlist at the sink, not just in `sandboxed-markdown.tsx`. Add a centralized scheme check (e.g., `/^https?:/`) inside the `open-external` `ipcMain.handle` in `app/src/main-process/main.ts` before calling `shell.openExternal`, and/or add the same guard inside `LinkButton.onClick` and `AppStore._openInBrowser` so that every caller of `shell.openExternal` is protected regardless of which UI path produced the URL.

### Proof of Concept
Because I was unable to retrieve the literal source of the `open-external` `ipcMain.handle` body in `app/src/main-process/main.ts` in this session (tool-call budget exhausted), I cannot present a fully confirmed end-to-end PoC against that exact handler code. The above chain (`Dispatcher.openInBrowser`/`LinkButton` → `shell.openExternal` IPC proxy → `open-external` handler) is verified via static tracing; a focused test would:
1. Construct an `IRefCheck`-like object (or PR/branch object) with `htmlUrl` set to `file:///Applications/Calculator.app` (or a Windows/Linux equivalent) as if returned from a check-run/GitHub API response.
2. Render `ci-check-run-popover.tsx` (or trigger any other `dispatcher.openInBrowser(url)` call site) and simulate the "View check details" click.
3. Assert that `shell.openExternal` (mocked) is invoked with the `file://` URL unmodified, and/or invoke the `open-external` IPC channel directly with a `file://` payload in `app/test/unit/ipc-contract-test.ts`-style test to observe whether the main-process handler applies any filtering.

**Caveat:** Due to index/tool limits, I could not directly view the body of the `ipcMain.handle('open-external', …)` registration in `app/src/main-process/main.ts` to confirm definitively whether it performs any additional filtering that I am unaware of. I recommend starting a full Devin session (with file-system access) to inspect that exact handler code and confirm whether any allowlist exists there before treating this as a confirmed, unmitigated vulnerability.

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L7594-7597)
```typescript
  /** Takes a URL and opens it using the system default application */
  public _openInBrowser(url: string): Promise<boolean> {
    return shell.openExternal(url)
  }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1586-1589)
```typescript
  /** Open the URL in a browser */
  public openInBrowser(url: string): Promise<boolean> {
    return this.appStore._openInBrowser(url)
  }
```

**File:** app/src/ui/check-runs/ci-check-run-popover.tsx (L152-169)
```typescript
  private onViewCheckDetails = (checkRun: IRefCheck): void => {
    if (checkRun.htmlUrl === null && this.props.repository.htmlURL === null) {
      // A check run may not have a url depending on how it is setup.
      // However, the repository should have one; Thus, we shouldn't hit this
      return
    }

    // Some checks do not provide htmlURLS like ones for the legacy status
    // object as they do not have a view in the checks screen. In that case we
    // will just open the PR and they can navigate from there... a little
    // dissatisfying tho more of an edgecase anyways.
    const url =
      checkRun.htmlUrl ??
      `${this.props.repository.htmlURL}/pull/${this.props.prNumber}`

    this.props.dispatcher.openInBrowser(url)
    this.props.dispatcher.incrementMetric('viewsCheckOnline')
  }
```

**File:** app/src/ui/main-process-proxy.ts (L146-146)
```typescript
export const openExternal = invokeProxy('open-external', 1)
```
