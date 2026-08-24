This is a solid analog. Let me confirm the exact API type definition and the full call chain from `target_url` to `shell.openExternal`.### Title
Unvalidated commit-status `target_url` opened via `shell.openExternal` allows arbitrary URI-scheme invocation - ([File: app/src/lib/ci-checks/ci-checks.ts])

### Summary
The GitHub Desktop CI-checks feature converts a legacy commit-status API object into a `IRefCheck` and stores the API-provided `target_url` verbatim as `htmlUrl`, with no validation of scheme or origin. That value is later handed straight to `shell.openExternal` when the user clicks "View check details"/"View on GitHub", without the `https:`-only guard that Desktop applies to markdown links. This mirrors the `callTakerTokenAndFillOrder()` pattern in the report: an unrestricted "call/open anything" primitive fed by attacker-controlled input, with no allow-list of acceptable targets.

### Finding Description
`apiStatusToRefCheck` copies the raw API field into the check model with no sanitization: [1](#0-0) 

This `htmlUrl` (originating from `target_url`, a free-form string field on the Statuses API object) is then used directly as a navigation target in the check-runs UI: [2](#0-1) [3](#0-2) 

Both call `dispatcher.openInBrowser(url)`, which resolves to: [4](#0-3) 

which invokes the renderer→main IPC proxy `openExternal`: [5](#0-4) 

and the main-process handler performs **no scheme allow-listing** — the `http/https` check only gates a log line, not the actual call: [6](#0-5) 

Elsewhere in the codebase, Desktop *does* apply a strict allow-list before opening externally-sourced links — e.g. the sandboxed markdown link interceptor only forwards `http:`/`https:` links to `onMarkdownLinkClicked`: [7](#0-6) 

and the GitHub Enterprise URL validator explicitly rejects non-`https:` protocols: [8](#0-7) 

The check-runs `target_url` path has no equivalent guard anywhere along the chain (`apiStatusToRefCheck` → `onViewCheckDetails`/`onViewOnGitHub` → `openInBrowser` → `open-external` IPC → `shell.openExternal`).

### Impact Explanation
`target_url` on a commit status is set by whatever CI system/GitHub App posts the status via the Statuses API. On any repository (including public repos where third-party/forked-PR-triggered CI apps commonly post statuses), the value is effectively attacker-influenced content that flows unmodified into `shell.openExternal`. Because there is no scheme restriction, this is a broader "open any URI" primitive than intended (comparable to letting `callTakerTokenAndFillOrder` call arbitrary functions on arbitrary contracts instead of a bounded interface) — it enables:
- Invocation of any OS-registered custom protocol handler with attacker-chosen data (protocol handler abuse, e.g. Windows `search:`/`ms-*:` handlers or third-party app URI schemes that accept command-like arguments).
- Opening `file://` paths, which on some platforms `shell.openExternal`/`ShellExecute` will execute directly rather than just display.
- Any future OS/browser handler quirks that specifically distinguish based on scheme, without users having any indication in the UI that this "GitHub link" could resolve to a non-http(s) target.

The severity is bounded by the fact that this still requires a deliberate user click on "View check details," and exploitation ultimately depends on what a locally-registered protocol handler does with the URI — but that is exactly the same "requires flexibility limited only to what's essential" gap the original report calls out.

### Likelihood Explanation
Likelihood is moderate: any repository owner/maintainer that integrates a third-party CI/status-reporting app (very common) creates a channel where `target_url` content is not fully trusted by the Desktop client, and a malicious or compromised CI integration (or one that blindly echoes fork-controlled data into the status URL) could set an arbitrary URI. No local access, admin rights, or social engineering beyond "user clicks the normal check-details button in the app they're already using" is required.

### Recommendation
Apply the same allow-list discipline used elsewhere in the codebase (e.g. `enterprise-validate-url.ts`, the markdown link interceptor) to `checkRun.htmlUrl` before it is passed to `openInBrowser`/`shell.openExternal`: parse the URL and reject/ignore anything whose protocol isn't `http:`/`https:`. Additionally, harden the main-process `open-external` IPC handler itself to reject non-`http(s)` schemes by default (or require callers to explicitly opt into non-browser schemes), rather than only using the scheme check for logging.

### Proof of Concept
1. As a repository maintainer, install/enable a CI integration that posts commit statuses via the Statuses API (or compromise one, or use an app that forwards a fork-supplied value into the status).
2. Configure/trigger that integration to post a status with `target_url` set to a non-http(s) URI, e.g. a locally-registered custom protocol URI or a `file://` path (`target_url: "some-installed-app://payload"`).
3. In Desktop, open the pull request / commit and view the CI checks popover or the "Checks Failed" notification for that commit.
4. Click "View check details" / "View on GitHub".
5. Observe that `apiStatusToRefCheck` → `onViewCheckDetails`/`onViewOnGitHub` → `dispatcher.openInBrowser` → IPC `open-external` → `shell.openExternal` is invoked with the raw `target_url`, with no scheme check performed at any point in the chain, unlike the `https:`-only checks applied to markdown-rendered links elsewhere in the app.

### Citations

**File:** app/src/lib/ci-checks/ci-checks.ts (L56-80)
```typescript
export function apiStatusToRefCheck(apiStatus: IAPIRefStatusItem): IRefCheck {
  let state: APICheckStatus
  let conclusion: APICheckConclusion | null = null

  if (apiStatus.state === 'success') {
    state = APICheckStatus.Completed
    conclusion = APICheckConclusion.Success
  } else if (apiStatus.state === 'pending') {
    state = APICheckStatus.InProgress
  } else {
    state = APICheckStatus.Completed
    conclusion = APICheckConclusion.Failure
  }

  return {
    id: apiStatus.id,
    name: apiStatus.context,
    description: getCheckRunShortDescription(state, conclusion),
    status: state,
    conclusion,
    appName: '',
    checkSuiteId: null,
    htmlUrl: apiStatus.target_url,
  }
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

**File:** app/src/lib/stores/app-store.ts (L7594-7597)
```typescript
  /** Takes a URL and opens it using the system default application */
  public _openInBrowser(url: string): Promise<boolean> {
    return shell.openExternal(url)
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
