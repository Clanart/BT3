Based on my investigation, the strongest analog to the Synapse bridge's "unvalidated low-level call parameter" is the `open-external` IPC handler and its downstream `shell.openExternal` sink, which accepts a URL string and dispatches it to the OS shell without validating the scheme/target for anything other than logging purposes.

### Title
Unvalidated protocol/target passed to `shell.openExternal` from IPC allows opening arbitrary OS handlers on links sourced from GitHub API content - (File: app/src/main-process/main.ts)

### Summary
The Synapse bridge finding is about a powerful "generic call" primitive (`target.call(callData)`) that accepts unrestricted low-level input without verifying what action it actually triggers, leading to a "successful" but semantically wrong operation. In GitHub Desktop, the analogous generic and powerful primitive is `shell.openExternal`, exposed to the renderer via the `open-external` IPC handler [1](#0-0) . It accepts any string and only inspects it to decide whether to log it as an `http(s)` navigation — it does not restrict or validate the scheme/target before invoking the OS-level open action.

### Finding Description
The handler is: [1](#0-0) 

This is a thin pass-through: any string reaching this IPC channel is forwarded to `shell.openExternal`, an OS-level "low-level call" that can invoke arbitrary registered URI-scheme handlers (not limited to `http`/`https`). The `pathLowerCase.startsWith('http://')` check only controls a log message — it performs no allow-listing or rejection of other schemes.

This sink is reachable from renderer code paths that render attacker-influenceable content:
- `LinkButton.onClick` calls `shell.openExternal(uri)` unconditionally whenever a `uri` prop is set [2](#0-1) , with no protocol check at this layer.
- Various dispatcher call sites (`onMarkdownLinkClicked`) forward whatever URL was clicked straight to `dispatcher.openInBrowser(url)` → `shell.openExternal` [3](#0-2) [4](#0-3) [5](#0-4) .
- `_openInBrowser` in the app store is the single fan-in point that calls `shell.openExternal(url)` with no additional validation [6](#0-5) .

The one place that does add a guard is `SandboxedMarkdown`'s link interceptor, which only forwards clicks when `/^https?:/.test(a.protocol)` [7](#0-6) . This means rendered PR/issue/commit body markdown is constrained to `http(s)` links. However, this check exists only at that one call site — it is not enforced at the shared sink (`_openInBrowser` / the `open-external` IPC handler / `LinkButton`). Any other code path that builds a `LinkButton` `uri` or calls `dispatcher.openInBrowser` directly from GitHub-API-derived data (e.g., repository `htmlURL`-based link builders such as `buildPullRequestUrl`/`buildCommitUrl` in the Copilot conflicts summary [8](#0-7) ) inherits no scheme restriction, relying entirely on the assumption that `htmlURL` is always a well-formed `https://github.com/...` value returned by a trusted GitHub.com/GHE API.

Because Desktop supports arbitrary self-configured GitHub Enterprise endpoints, and PR/issue/comment/commit metadata is fetched from that endpoint's API, a malicious or compromised GHE server (or a MITM'd/rogue endpoint a user is convinced to add) is in a position to return crafted `html_url` or comment-body values feeding these code paths, some of which reach `shell.openExternal` without the `https?:` restriction that only the markdown-comment path enforces.

### Impact Explanation
`shell.openExternal` with an unvalidated scheme is a well-documented Electron anti-pattern: on Windows it can invoke arbitrary URI protocol handlers registered on the system (e.g., `search-ms:`, `ms-msdt:`, or other third-party handlers), and depending on installed software this can lead to code execution or file access initiated purely by a user clicking a link inside GitHub Desktop, entirely outside the intended `https://` browsing action, silently different from what the UI/label implied — mirroring the "call succeeds but does something other than what's expected" pattern from the source finding.

### Likelihood Explanation
Exploitation requires the victim to click a rendered link and (in the most complete chain) to be connected to a GHE endpoint the attacker influences, or to encounter a UI element whose `uri`/`openInBrowser` argument is built from unsanitized API data outside the one hardened markdown path. This is a real but narrower path than the fully generic `shell.openExternal` sink itself — the one confirmed protocol guard (`sandboxed-markdown.tsx`) reduces likelihood for the highest-traffic surface (PR/commit/issue body rendering), but the guard is not centralized at the shared sink, so it is not a systemic guarantee for all current or future call sites.

### Recommendation
Centralize URL/scheme validation at the shared sink rather than at individual call sites: enforce the `^https?:` (or an explicit allow-list) check inside `_openInBrowser` in `app-store.ts` and/or inside the `open-external` IPC handler in `main.ts`, so that every caller (current and future) of `shell.openExternal` is protected regardless of whether the calling component remembers to validate the scheme itself.

### Proof of Concept
Not independently verified end-to-end (would require confirming that a specific GHE-API-sourced field can reach `LinkButton`/`openInBrowser` with a non-`https` value before the code path in `sandboxed-markdown.tsx`'s interceptor is invoked). What is directly confirmed from local code is: (1) the `open-external` IPC handler performs no scheme validation beyond a logging check [1](#0-0) ; (2) `LinkButton` and `_openInBrowser` call `shell.openExternal` unconditionally [2](#0-1) [6](#0-5) ; and (3) only one call site (`sandboxed-markdown.tsx`) restricts to `https?:` [7](#0-6) , confirming the guard is not applied uniformly at the shared sink.

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

**File:** app/src/ui/notifications/pull-request-comment-like.tsx (L162-164)
```typescript
  private onMarkdownLinkClicked = (url: string) => {
    this.props.dispatcher.openInBrowser(url)
  }
```

**File:** app/src/ui/pull-request-quick-view.tsx (L160-162)
```typescript
  private onMarkdownLinkClicked = (url: string) => {
    this.props.dispatcher.openInBrowser(url)
  }
```

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx (L500-502)
```typescript
  private onMarkdownLinkClicked = (url: string): void => {
    this.props.dispatcher.openInBrowser(url)
  }
```

**File:** app/src/lib/stores/app-store.ts (L7594-7597)
```typescript
  /** Takes a URL and opens it using the system default application */
  public _openInBrowser(url: string): Promise<boolean> {
    return shell.openExternal(url)
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

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-resolution-summary.tsx (L184-215)
```typescript
function buildPullRequestUrl(
  gitHubRepository: GitHubRepository | null,
  prNumber: number
): string | null {
  const base = gitHubRepository?.htmlURL ?? null
  return base !== null ? `${base}/pull/${prNumber}` : null
}

function buildCommitUrl(
  gitHubRepository: GitHubRepository | null,
  sha: string
): string | null {
  const base = gitHubRepository?.htmlURL ?? null
  return base !== null ? `${base}/commit/${sha}` : null
}

/**
 * Render a reference title as a link when we have a URL, or as plain
 * text otherwise.
 */
function renderTitle(text: string, url: string | null): JSX.Element {
  if (url === null) {
    return (
      <span className="copilot-conflicts-summary-reference-title">{text}</span>
    )
  }
  return (
    <LinkButton uri={url} className="copilot-conflicts-summary-reference-title">
      {text}
    </LinkButton>
  )
}
```
