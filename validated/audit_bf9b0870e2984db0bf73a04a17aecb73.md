Found the concrete analog. In `app/src/ui/notifications/pull-request-comment-like.tsx:117`, the GitHub API field `user.html_url` (returned as part of a PR review/comment API object, i.e. `IAPIIdentity`) is passed directly into `<LinkButton uri={user.html_url}>` with no protocol validation. `LinkButton.onClick` (`app/src/ui/lib/link-button.tsx:76-92`) calls `shell.openExternal(uri)` unconditionally for whatever string is supplied — there is no `https?:` check like the one that exists for markdown links (`app/src/ui/lib/sandboxed-markdown.tsx:292-305`, which explicitly checks `/^https?:/.test(a.protocol)` before invoking its callback). The main-process IPC handler behind `shell.openExternal` (`app/src/main-process/main.ts:581-597`, `open-external`) also performs no protocol allow-listing — it only logs when the string happens to start with `http(s)://` but calls `shell.openExternal(path)` regardless of protocol.

### Title
Unvalidated GitHub API `html_url` field passed to `shell.openExternal` in PR review/comment notifications - (File: app/src/ui/notifications/pull-request-comment-like.tsx)

### Summary
Author identity objects returned by the GitHub API for PR reviews/comments (`IAPIIdentity`, containing `login`, `avatar_url`, `html_url`) are rendered as clickable `LinkButton`s. `LinkButton` forwards its `uri` prop straight to `shell.openExternal` without validating the scheme, unlike the markdown link path in the same UI area, which explicitly restricts navigation to `http:`/`https:`.

### Finding Description
`PullRequestCommentLike.renderTimelineItem` (`app/src/ui/notifications/pull-request-comment-like.tsx:117`) does:
```
<LinkButton uri={user.html_url} className="author">
```
where `user` is an `IAPIIdentity` sourced from a GitHub (or GitHub Enterprise) API response for a pull request review/comment author. `LinkButton.onClick` (`app/src/ui/lib/link-button.tsx:76-92`) does:
```
const uri = this.props.uri
if (uri) {
  shell.openExternal(uri)
}
```
with no scheme check. `shell.openExternal` ultimately routes through the main-process `open-external` IPC handler (`app/src/main-process/main.ts:581-597`), which also performs no protocol allow-list — it only conditionally logs for `http/https` but calls `shell.openExternal(path)` for any string. Electron's `shell.openExternal` is documented to be dangerous with untrusted, non-`http(s)` input: URIs using OS-registered custom protocol handlers, `file:`, or Windows UNC-style paths (`\\host\share`) can trigger unintended local application launches, credential-leaking SMB/NTLM auth attempts, or (depending on installed protocol handlers) command execution. The same file's `onMarkdownLinkClicked` handler for the *comment body* correctly delegates to `SandboxedMarkdown`'s `setupLinkInterceptor` (`app/src/ui/lib/sandboxed-markdown.tsx:292-305`), which enforces `/^https?:/.test(a.protocol)` before calling back — showing the developers are aware of this class of risk for markdown bodies but did not apply the same guard to the `html_url` field used for the author link, and comment permalink (`externalURL`, also passed unchecked into a sibling `LinkButton` at line 121).

### Impact Explanation
This differs from the referenced Uniswap report only in mechanism, but the broken invariant is analogous: a value that should be constrained/validated before being used to perform a sensitive action (here, "the URI passed to the OS shell must be `http(s)`") is not checked, and an untrusted, externally-supplied object (a GitHub API PR review/comment payload) supplies that value. If an attacker can control (or a compromised/malicious GHES endpoint or man-in-the-middle can inject) an `html_url`/`avatar_url`/`externalURL` field with a non-`http(s)` scheme, clicking the resulting "author" link in a PR-review notification dialog invokes `shell.openExternal` with attacker-controlled input, which can be leveraged for local file execution via OS-registered protocol handlers, forced UNC-path navigation causing NTLM credential leakage to an attacker host, or launching other locally installed vulnerable applications registered for arbitrary protocols.

### Likelihood Explanation
Exploitation requires GitHub Desktop to render a PR review/comment whose `user.html_url` (or `externalURL`) diverges from the expected `https://<host>/...` form. On github.com this field is server-controlled and normally safe, but GitHub Desktop also supports GitHub Enterprise Server endpoints, where the API response is fully attacker-controlled if the user connects to (or is redirected to) a malicious/compromised GHES-like endpoint — squarely within the "attacker controls a GitHub API object" category in scope. The user interaction required is a single click on an author name/timestamp shown in a legitimate-looking PR review notification, which is a normal, expected user action, not an "unnatural" step.

### Recommendation
Apply the same protocol allow-list used in `sandboxed-markdown.tsx`'s `setupLinkInterceptor` to `LinkButton.onClick`, and/or to the `open-external` IPC handler in `main.ts`, rejecting any URI whose scheme is not `http:`/`https:` before calling `shell.openExternal`. At minimum, sanitize `user.html_url`, `avatar_url`, and `externalURL` fields sourced from API responses before using them as `LinkButton` `uri` props anywhere in the notifications/PR-review UI.

### Proof of Concept
1. Point GitHub Desktop at a malicious/compromised GitHub Enterprise-like API endpoint (or intercept/tamper with an API response in transit, e.g. via a misconfigured or attacker-controlled proxy).
2. Craft a pull request review/comment API payload whose `user.html_url` is set to a non-`https` URI, e.g. `file:///C:/some/malicious/path` or a UNC path like `\\attacker-host\share\evil`, instead of a normal `https://.../<user>` URL.
3. Trigger the PR review notification in Desktop so `PullRequestCommentLike` renders with this payload (`app/src/ui/notifications/pull-request-comment-like.tsx:117`).
4. The victim clicks the author name link, which calls `shell.openExternal(user.html_url)` unchecked, causing the OS to process the crafted URI (e.g. attempting SMB authentication against `attacker-host`, leaking NTLM credentials, or invoking a registered protocol handler with attacker-controlled arguments). [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** app/src/ui/notifications/pull-request-comment-like.tsx (L105-125)
```typescript
    return (
      <div className="timeline-item-container">
        {this.renderDashedTimelineLine('top')}
        <div className={timelineItemClass}>
          <Avatar
            accounts={accounts}
            user={userAvatar}
            title={null}
            size={40}
          />
          {this.renderReviewIcon()}
          <div className="summary">
            <LinkButton uri={user.html_url} className="author">
              {user.login}
            </LinkButton>{' '}
            {eventVerb} your pull request{' '}
            <LinkButton uri={externalURL} className="submission-date">
              {relativeReviewDate}
            </LinkButton>
          </div>
        </div>
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
