This confirms the flow with no host allow-list. The `main.ts` `open-external` IPC handler only checks the URL scheme (`http://`/`https://`) for logging purposes — it never validates the host against the repository's GitHub endpoint before calling `shell.openExternal(path)`.### Title
Unvalidated GitHub API `html_url` opened via `shell.openExternal` allows host-spoofed link presented as trusted PR/comment URL - (File: `app/src/ui/lib/link-button.tsx`)

### Summary
`LinkButton.onClick` calls `shell.openExternal(uri)` on any `uri` prop it is given, with no validation of the URL's host against the repository's GitHub endpoint. `PullRequestCommentLike.renderTimelineItem` and `PullRequestComment`/`PullRequestReview` pass `comment.html_url` / `review.html_url` / `user.html_url` — fields taken directly from GitHub API response objects (`IAPIComment`, `IAPIIdentity`) — straight into `LinkButton`'s `uri` prop as `externalURL`.

### Finding Description
- `LinkButton.onClick` unconditionally forwards `this.props.uri` to `shell.openExternal(uri)` with no allow-list or host check: [1](#0-0) 
- `PullRequestComment.render` and `PullRequestReview.render` pass `comment.html_url` / `review.html_url` — server-supplied API fields — as `externalURL` into `PullRequestCommentLike`: [2](#0-1) 
- `PullRequestCommentLike.renderTimelineItem` renders `externalURL` (and `user.html_url`) unmodified inside a `LinkButton`: [3](#0-2) 
- The footer "Open in Browser" link also passes `comment.html_url` directly: [4](#0-3) 
- The eventual `shell.openExternal` call chain (`app/src/lib/app-shell.ts` → `main-process-proxy.ts` `openExternal` → IPC `open-external` handler in main process) does not validate the URL host either; the main-process handler only inspects the scheme prefix (`http://`/`https://`) for logging purposes, not for host allow-listing: [5](#0-4) [6](#0-5) 

Since `IAPIComment`/`IAPIIdentity`/pull-request-review objects are deserialized directly from GitHub API JSON responses, any actor able to control the content of a pull request comment/review body via the GitHub API (e.g., a malicious/compromised app, a proxy MITM of the API response, or a crafted webhook-fed object) controls the `html_url` string that ends up passed unmodified to `shell.openExternal`.

### Impact Explanation
`shell.openExternal` opens the given URI in the user's default OS handler (browser, or a custom protocol handler if the OS has one registered). If the host is not restricted to the expected GitHub endpoint, an attacker-controlled comment/review object could set `html_url` to an arbitrary domain, causing GitHub Desktop's "Open in Browser" / relative-date / author-name link — labeled as pointing to the PR/comment on the correct GitHub host — to actually navigate the user to an attacker's site. This is a link-spoofing/UI-trust issue: the user believes they're being taken to the real GitHub PR/comment page. This does not itself achieve code execution or credential exfiltration within GitHub Desktop's IPC/renderer boundary, since `shell.openExternal` only hands the URL off to the OS shell (subject to Electron/OS URL-scheme restrictions), and the user must actively click the link.

### Likelihood Explanation
Requires the attacker to control values returned from the GitHub API for a PR review/comment (`html_url`) that get displayed in one of these notification dialogs, and the user must be induced to click the link. GitHub API responses for these fields normally come from GitHub itself, so an attacker would need to control a fork/comment/review through a scenario where Desktop trusts an untrusted API response (e.g., custom GitHub Enterprise endpoint compromise, a MITM of the API/proxy channel, or an app-generated review/comment via the API on a repo the victim interacts with). This is a somewhat narrow but plausible amplification path already covered by the "attacker controls a GitHub API object" scope.

### Recommendation
Validate the host of `uri` in `LinkButton.onClick` (or centrally before calling `shell.openExternal`) against the repository's configured GitHub endpoint (`gitHubRepository.endpoint`) before opening it externally, similar to protections that should exist for markdown link click-through. Reject or warn on mismatched hosts rather than silently opening them.

### Proof of Concept
```tsx
// Mock IAPIComment
const maliciousComment: IAPIComment = {
  html_url: 'https://evil.example.com/steal',
  // ...other required fields
} as IAPIComment

// Render <PullRequestComment comment={maliciousComment} ... />
// User clicks "Open in Browser" or the relative-date LinkButton
// -> LinkButton.onClick fires -> shell.openExternal('https://evil.example.com/steal')
// Assertion: shell.openExternal called with the attacker-controlled, unmodified URL,
// with no host check against repository.gitHubRepository.endpoint.
``` [7](#0-6) [8](#0-7)

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

**File:** app/src/ui/notifications/pull-request-comment.tsx (L78-83)
```typescript
        eventDate={new Date(comment.created_at)}
        eventVerb="commented on"
        eventIconSymbol={icon.symbol}
        eventIconClass={icon.className}
        externalURL={comment.html_url}
        user={comment.user}
```

**File:** app/src/ui/notifications/pull-request-comment.tsx (L126-129)
```typescript
      <Row>
        <div className="footer-links">
          <LinkButton uri={comment.html_url}>{openInBrowserText}</LinkButton>
        </div>
```

**File:** app/src/ui/notifications/pull-request-comment-like.tsx (L117-124)
```typescript
            <LinkButton uri={user.html_url} className="author">
              {user.login}
            </LinkButton>{' '}
            {eventVerb} your pull request{' '}
            <LinkButton uri={externalURL} className="submission-date">
              {relativeReviewDate}
            </LinkButton>
          </div>
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
