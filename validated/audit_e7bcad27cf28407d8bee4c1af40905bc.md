### Title
`LinkButton` opens attacker-controlled GitHub API `html_url` fields via `shell.openExternal` with no protocol validation - (File: `app/src/ui/lib/link-button.tsx`)

### Summary
GitHub Desktop's `SandboxedMarkdown` component explicitly restricts which link clicks are forwarded to the app (`/^https?:/.test(a.protocol)`) before invoking `onMarkdownLinkClicked`, but the generic `LinkButton` UI component used throughout the notification/PR/comment surfaces performs **no protocol check at all** and calls `shell.openExternal(uri)` directly with whatever string it is given. Several call sites feed this `uri` prop directly from fields on GitHub API response objects (`comment.html_url`, `review.html_url`, `user.html_url`) rather than from application-constructed URLs, so a value originating from an untrusted/attacker-influenced API response is passed straight to Electron's OS-level "open" primitive.

### Finding Description
`LinkButton.onClick` is:
```
private onClick = (event: React.MouseEvent<HTMLAnchorElement>) => {
  ...
  const uri = this.props.uri
  if (uri) {
    shell.openExternal(uri)
  }
  ...
}
``` [1](#0-0) 

Unlike `SandboxedMarkdown`, which sanitizes markdown and additionally gates outbound link clicks to `https?:` protocols before calling the `onMarkdownLinkClicked` callback: [2](#0-1) 

`LinkButton` has no equivalent guard, and `shell.openExternal`/`_openInBrowser` in the main process and app store likewise perform no protocol validation before invoking Electron's `shell.openExternal`: [3](#0-2) [4](#0-3) 

`LinkButton` is used to render clickable links whose `uri` is populated directly from GitHub API payload fields — e.g. the PR comment/review author link and the "submission date" / "open in browser" links use `user.html_url`, `comment.html_url`, and `review.html_url` straight from the API response: [5](#0-4) [6](#0-5) [7](#0-6) 

These `html_url` values are typed as plain strings coming off the deserialized API JSON — there is no runtime scheme allow-list applied before they reach `LinkButton`/`shell.openExternal`, in contrast to the explicit `https?:` check that exists for markdown-rendered links, and in contrast to the dedicated `validateURL()` helper used elsewhere for Enterprise server URL input: [8](#0-7) 

### Impact Explanation
Electron's `shell.openExternal` is a documented sharp edge: on Windows it will hand the given string to `ShellExecute`, meaning URIs with non-`http(s)` schemes can be dispatched to arbitrary registered protocol handlers or trigger OS behaviors that go well beyond "open a webpage" — e.g. `file://\\attacker-share\resource` (SMB share access enabling NTLM hash/credential leakage), `search-ms:` (remote-controlled Explorer search), or any other locally-registered custom URI scheme that resolves to executing another application with attacker-supplied arguments. Because this string is sourced from a GitHub API object (comment/review/user `html_url`) rather than a value the user typed or a value already vetted as `https`, any actor able to influence what the Desktop client receives as "the GitHub API response" — a malicious/compromised GitHub Enterprise Server, a network-level or TLS-downgraded MITM against the API endpoint, or a subverted GraphQL/REST proxy the Enterprise deployment routes through — can smuggle a dangerous URI into a field the UI treats as trustworthy. Clicking the resulting rendered link (e.g. "Open in Browser" on a PR comment/review notification, or the commenter's name) invokes it via `shell.openExternal` unchecked.

### Likelihood Explanation
This requires the user to click a link inside a Desktop-rendered PR/notification dialog, which is a normal, expected user interaction (not a contrived or "unnatural" step). The attacker-control surface required — the ability to shape what the GitHub API returns to the client for `html_url` on a comment/review/user object — matches the "attacker controls a GitHub API object" category explicitly listed as an in-scope vector. Likelihood is moderate: it depends on the attacker having some ability to influence the API responses Desktop consumes (most plausible against GitHub Enterprise Server deployments or a compromised network path), rather than being exploitable purely from a public github.com PR, since github.com itself always returns well-formed `https://github.com/...` `html_url` values.

### Recommendation
Apply the same protocol allow-list used by `SandboxedMarkdown`'s link interceptor (`/^https?:/`) inside `LinkButton.onClick` before calling `shell.openExternal`, or centralize this validation inside the `openExternal` wrapper in `app-shell.ts` / the `open-external` IPC handler in `main.ts` so every caller benefits uniformly, rather than relying on each individual call site to have already produced a safe `https://` URL.

### Proof of Concept
1. Position the Desktop client against a GitHub Enterprise Server (or a MITM'd/compromised proxy in front of the GitHub API) that the attacker controls or can tamper with responses from.
2. Have that server return a pull-request review or comment payload where `html_url` (or `user.html_url`) is set to a dangerous URI, e.g. `file://\\attacker-host\share\payload` or a locally-registered custom scheme URI with attacker-chosen arguments, instead of a normal `https://` GitHub link.
3. Desktop surfaces this as a PR review/comment notification dialog and renders the value via `<LinkButton uri={comment.html_url}>` / `<LinkButton uri={user.html_url}>` per `app/src/ui/notifications/pull-request-comment.tsx:128` and `app/src/ui/notifications/pull-request-comment-like.tsx:117-121`.
4. The user clicks "Open in Browser" (or the commenter's name) — `LinkButton.onClick` fires and calls `shell.openExternal(uri)` in `app/src/ui/lib/link-button.tsx:76-92` with no scheme validation, dispatching the crafted URI to the OS shell.

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

**File:** app/src/lib/stores/app-store.ts (L7594-7597)
```typescript
  /** Takes a URL and opens it using the system default application */
  public _openInBrowser(url: string): Promise<boolean> {
    return shell.openExternal(url)
  }
```

**File:** app/src/ui/notifications/pull-request-comment-like.tsx (L105-129)
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
        {bottomLine}
      </div>
    )
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

**File:** app/src/ui/notifications/pull-request-review.tsx (L128-134)
```typescript
    const openInBrowserText = __DARWIN__ ? 'Open in Browser' : 'Open in browser'

    return (
      <Row>
        <div className="footer-links">
          <LinkButton uri={review.html_url}>{openInBrowserText}</LinkButton>
        </div>
```

**File:** app/test/unit/enterprise-validate-url-test.ts (L1-19)
```typescript
import { describe, it } from 'node:test'
import assert from 'node:assert'
import { validateURL } from '../../src/ui/lib/enterprise-validate-url'

describe('validateURL', () => {
  it('passes through a valid url', () => {
    const url = 'https://ghe.io:9000'
    const result = validateURL(url)
    assert.equal(result, url)
  })

  it('prepends https if no protocol is provided', () => {
    const url = validateURL('ghe.io')
    assert.equal(url, 'https://ghe.io')
  })

  it('throws if given an invalid protocol', () => {
    assert.throws(() => validateURL('ftp://ghe.io'))
  })
```
