## Title
Unvalidated CI check-run `htmlUrl` passed directly to `shell.openExternal()` allows arbitrary URI-scheme invocation - (File: `app/src/ui/check-runs/ci-check-run-popover.tsx`)

### Summary
The bug-class seed is "create a privileged effect (`_mint`) on attacker-influenced input without validating that the target can safely receive it, and don't document why the unsafe primitive is used." The Desktop analog is structurally the same shape: Desktop takes a URL supplied by a remote GitHub API object (a CI check run's `htmlUrl`) and hands it, unvalidated, straight to Electron's `shell.openExternal()` — the “unsafe primitive” for this codebase — while a sibling code path (`sandboxed-markdown.tsx`) demonstrates the safe pattern of restricting to `http(s):` before doing the same operation, but that guard is not applied here.

### Finding Description
`CICheckRunPopover.onViewCheckDetails` builds a URL directly from `checkRun.htmlUrl`, a field that originates from the GitHub Checks API (or, for legacy "status" checks, a repo-owner-controlled `details_url`), and passes it straight to `dispatcher.openInBrowser`: [1](#0-0) 

`Dispatcher.openInBrowser` / `AppStore._openInBrowser` performs no validation at all before invoking Electron's `shell.openExternal`: [2](#0-1) 

The main-process IPC handler for `open-external` likewise does not restrict the scheme — it only *logs* when the URL is `http(s)`, but still calls `shell.openExternal(path)` for every other scheme unconditionally: [3](#0-2) 

Contrast this with the codebase's own established mitigation for the exact same class of attacker-controlled-URL problem: `SandboxedMarkdown` explicitly enforces an `http(s):` allow-list before ever invoking a link click handler that leads to `openExternal`: [4](#0-3) 

`onViewCheckDetails`/`onViewJobStep` in `ci-check-run-popover.tsx` (and the analogous flow in `pull-request-checks-failed.tsx`) bypass this allow-list entirely because they don't route through markdown rendering — the check-run URL is a raw API string consumed directly by a button `onClick` handler.

### Impact Explanation
`shell.openExternal` with an unrestricted scheme is Electron's version of "mint to any address without checking it can safely receive the token": passing a non-`http(s)` URI (e.g. a Windows-registered custom protocol handler such as `search-ms:`, `ms-officecmd:`, or a `file:`/UNC-style path) to `shell.openExternal` can invoke OS-level protocol handlers or file associations that other installed applications register, which has historically been used to achieve remote code execution in other Electron apps that failed to allow-list URL schemes before calling `shell.openExternal`. Because `checkRun.htmlUrl` is set by whatever produced the CI check (a workflow, a third-party CI integration, or for legacy commit statuses, anyone with push/status-write access to the repo), an attacker who can create a check run/commit status on a repository the victim has open in Desktop can supply an arbitrary URI as `details_url`, and it will reach `shell.openExternal` the moment the victim clicks "View check details."

### Likelihood Explanation
This requires no local access, no leaked credentials, and no unnatural user steps beyond the ordinary "view check details" click that Desktop's PR/checks UI exists to encourage. The attacker primitive (setting an arbitrary `details_url` on a check run or legacy commit status) is within normal, unprivileged use of the Checks/Statuses API by anyone who can push to or run CI against the repository, or via a malicious/compromised GitHub App/CI integration. Desktop's own `sandboxed-markdown.tsx` guard shows the project is aware this class of "attacker-controlled URL → open externally" needs scheme restriction, but that fix was not applied to the CI-check-details code path.

### Recommendation
Apply the same `/^https?:/` (or a stricter allow-list, e.g. also excluding `file:`/UNC-like forms) validation used in `sandboxed-markdown.tsx`'s `setupLinkInterceptor` to every call site that ends up in `shell.openExternal`, in particular `AppStore._openInBrowser` (so it's enforced centrally) and/or the `open-external` IPC handler in `main.ts`, rejecting non-`http(s)` schemes instead of merely logging. If a non-restrictive `openExternal` is intentionally required somewhere, document the reason at that call site as the report recommends for `_mint()`.

### Proof of Concept
1. As a user or automation with permission to report commit status/check-run results on a repository (e.g., via the Statuses or Checks API), create a check run/commit status with `details_url` (surfaced as `checkRun.htmlUrl`) set to a non-`http(s)` URI known to be handled by a locally installed application/protocol handler (e.g., a Windows `search-ms:` URI or another OS-specific handler capable of triggering unwanted execution).
2. Victim opens the PR/commit in GitHub Desktop and views the Checks popover, then clicks "View check details" for that check run.
3. `onViewCheckDetails` calls `dispatcher.openInBrowser(checkRun.htmlUrl)` → `AppStore._openInBrowser` → `shell.openExternal(url)` with no scheme validation, invoking the OS handler for the attacker-chosen URI on the victim's machine.

Note: I could not fully confirm from the indexed code whether `IRefCheck.htmlUrl` values are ever pre-filtered by an upstream GitHub API client layer (e.g., `app/src/lib/api.ts`) before reaching the UI; a Devin session with full repo access would be needed to trace `IRefCheck` construction end-to-end and confirm no sanitization occurs before this sink.

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L7594-7597)
```typescript
  /** Takes a URL and opens it using the system default application */
  public _openInBrowser(url: string): Promise<boolean> {
    return shell.openExternal(url)
  }
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
