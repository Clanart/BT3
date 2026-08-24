### Title
`LinkButton`/`shell.openExternal` accept attacker-controlled, non-http(s) URIs from GitHub API responses with no scheme validation - ([File: app/src/ui/lib/link-button.tsx])

### Summary
`_validateSigner` in the Buffer report broke a "should not revert / should be safely rejected" invariant by feeding unchecked untrusted input straight into a sensitive primitive (`ECDSA.recover`). The Desktop analog is structurally identical: `LinkButton` and the main-process `open-external` IPC handler feed an attacker/server-controlled string straight into Electron's `shell.openExternal` with no scheme allow-listing, even though the codebase demonstrably knows how to do this validation correctly elsewhere (`sandboxed-markdown.tsx`).

### Finding Description
`LinkButton.onClick` unconditionally calls `shell.openExternal(uri)` for whatever `uri` prop it is given: [1](#0-0) 

This component is used throughout the app to render values that originate from the GitHub API response objects, i.e. attacker-controllable if the user is pointed at a malicious/compromised GitHub Enterprise Server endpoint or the API traffic is intercepted/tampered by a malicious proxy (both are in-scope attacker models per this task's rules: "a GitHub API object ... or a git remote/proxy response"). Examples found:
- `comment.html_url` on the "Open in Browser" button of a PR review comment dialog: [2](#0-1) 
- `user.html_url` / `externalURL` (comment.html_url) rendered as clickable author name and timestamp links: [3](#0-2) 
- `secret.bypassURL` rendered directly as a clickable "Bypass" link in the secret-scanning push-protection dialog: [4](#0-3) 

None of these call sites, nor `LinkButton` itself, validate that the `uri` begins with `http://`/`https://` before handing it to `shell.openExternal`.

Critically, the main-process IPC handler that actually performs the action also does not enforce a scheme allow-list — it only *logs* when the value happens to be http(s), but calls `shell.openExternal(path)` unconditionally for any string: [5](#0-4) 

The codebase's own security model shows the intended guard exists elsewhere but was not applied consistently: `sandboxed-markdown.tsx`'s link interceptor explicitly checks `/^https?:/.test(a.protocol)` before invoking a click callback for markdown-rendered links, precisely because untrusted content (rendered from repo/PR/commit data) must not be allowed to trigger arbitrary-scheme navigation: [6](#0-5) 

That check is exactly the "try/guard before the dangerous call" pattern the Buffer report's fix (`ECDSA.tryRecover`) represents — but it is missing from `LinkButton` and from the `open-external` IPC handler, which is the actual choke point for every "Open in Browser"/link click in the renderer. So a value that GitHub's own markdown-link code path defends against is left completely unguarded when it arrives via structured API JSON fields (`html_url`, `bypass_url`, etc.) instead of markdown text.

### Impact Explanation
`shell.openExternal` on Electron passes the string to the OS shell facility (`ShellExecute` on Windows, `NSWorkspace` on macOS, `xdg-open` on Linux). If a compromised/malicious GHES endpoint, or a MITM'd/malicious proxy in front of the GitHub API, returns a crafted `html_url`/`bypass_url` value using a non-http(s) scheme (e.g. a registered custom protocol handler, `file:`, or an OS URI scheme known to have RCE-capable handlers), a single natural user click ("Open in Browser", clicking a PR author's name, or clicking "Bypass" on a secret-scanning warning) invokes that scheme through the OS. This matches the task's valid-impact criteria: code execution / silent trust abuse reachable via "a GitHub API object" or "a git remote/proxy response", without any local/physical access, admin rights, or social-engineering beyond the single expected click the UI already invites.

### Likelihood Explanation
Requires the user to be talking to a compromised/malicious GHES server or a tampered API response (network-adjacent attacker or malicious enterprise server operator), and requires a single ordinary click on a link the UI presents as trustworthy (e.g. "Open in Browser"). No unusual/unnatural steps are needed on the user's part — clicking such links is the intended workflow. The severity depends on which URI schemes are registered as handlers on the victim's OS, which is somewhat environment-dependent, but the missing validation itself is unconditional and present on every `LinkButton` render path plus the shared IPC entry point.

### Recommendation
Enforce an `http(s)`-only allow-list at the single choke point before invoking the OS shell: validate scheme inside the `open-external` IPC handler in `app/src/main-process/main.ts` (reject/log-and-no-op for non-`http`/`https` schemes) and/or inside `LinkButton.onClick` in `app/src/ui/lib/link-button.tsx`, mirroring the existing `/^https?:/` check already used in `app/src/ui/lib/sandboxed-markdown.tsx`. Apply the same validation to any other direct `shell.openExternal` call sites that consume API-derived URLs.

### Proof of Concept
1. Configure GitHub Desktop against a malicious/compromised GitHub Enterprise Server endpoint (or intercept/tamper with the API response via a malicious proxy) such that a pull request comment/review API response returns `html_url` (or a secret-scanning push-protection response returns `bypass_url`) set to a non-http(s) URI, e.g. a URI scheme registered to a vulnerable local application/protocol handler.
2. User receives/opens the corresponding PR comment/review notification dialog and clicks "Open in Browser" (`app/src/ui/notifications/pull-request-comment.tsx:128`), or opens the secret-scanning push-protection dialog and clicks "Bypass" (`app/src/ui/secret-scanning/push-protection-error-dialog.tsx:143-150`).
3. `LinkButton.onClick` (`app/src/ui/lib/link-button.tsx:76-92`) calls `shell.openExternal(uri)` unconditionally.
4. The renderer-to-main IPC proxy invokes `ipcMain.handle('open-external', ...)` (`app/src/main-process/main.ts:581-597`), which calls Electron's `shell.openExternal(path)` without checking the scheme, handing the crafted URI to the OS shell.

Note: I could not fully verify from the index which specific OS-registered protocol handlers would be reachable/exploitable on a given victim machine (this depends on installed third-party software), since that is outside static code coverage. The missing scheme-validation gap itself, however, is directly confirmed in the cited files.

### Citations

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

**File:** app/src/ui/notifications/pull-request-comment.tsx (L123-129)
```typescript
    const openInBrowserText = __DARWIN__ ? 'Open in Browser' : 'Open in browser'

    return (
      <Row>
        <div className="footer-links">
          <LinkButton uri={comment.html_url}>{openInBrowserText}</LinkButton>
        </div>
```

**File:** app/src/ui/notifications/pull-request-comment-like.tsx (L116-124)
```typescript
          <div className="summary">
            <LinkButton uri={user.html_url} className="author">
              {user.login}
            </LinkButton>{' '}
            {eventVerb} your pull request{' '}
            <LinkButton uri={externalURL} className="submission-date">
              {relativeReviewDate}
            </LinkButton>
          </div>
```

**File:** app/src/ui/secret-scanning/push-protection-error-dialog.tsx (L140-151)
```typescript
  private renderBypassButton = (secret: ISecretScanResult) => {
    if (secret.requiresApproval) {
      return (
        <LinkButton
          ariaLabel={`Bypass ${secret.description}`}
          uri={secret.bypassURL}
          onClick={this.props.onDelegatedBypassLinkClick}
        >
          Bypass
        </LinkButton>
      )
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

**File:** app/src/ui/lib/sandboxed-markdown.tsx (L288-305)
```typescript
  /**
   * We still want to be able to navigate to links provided in the markdown.
   * However, we want to intercept them an verify they are valid links first.
   */
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
