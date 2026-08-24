Based on my research, I found a plausible analog. The core pattern in the reported `VaderPoolV2.rescue` bug is: **an operation that accepts an unvalidated target and executes it without checking whether the source of that target is trustworthy**, allowing an outside actor (via a value the app didn't originate) to trigger an action with consequences the user didn't intend.

### Title
Unrestricted URI scheme passed to `shell.openExternal` from attacker-controlled Markdown links (PR/review/comment bodies) - (File: app/src/main-process/main.ts)

### Summary
The `open-external` IPC handler in the main process calls `shell.openExternal(path)` on any string received from the renderer, with no allow-list restricting the URI scheme to `http/https`. [1](#0-0) 
This handler is reachable from renderer code paths that render **untrusted, attacker-authored content** — e.g., Markdown link clicks inside pull request review comments and PR check notifications — which pass the clicked URL straight through to `dispatcher.openInBrowser(url)` → `shell.openExternal(url)`. [2](#0-1) [3](#0-2) 

### Finding Description
The comment/review body rendered via `SandboxedMarkdown` originates from the GitHub API (an object fully controlled by any user who can comment on or review a PR in a repository the victim has open in Desktop) — this matches the "attacker controls a GitHub API object" primitive. [4](#0-3) 
When the user clicks a link inside that body, `onMarkdownLinkClicked` forwards the raw `url` string with no scheme validation to `dispatcher.openInBrowser`, which in turn calls `shell.openExternal(url)` via the IPC round-trip in `main.ts`. [1](#0-0) 
The only scheme-aware logic in the handler is a `startsWith('http://')/('https://')` check used purely to decide whether to log the URL — it is not used to gate execution, so any other scheme (`file:`, a third-party registered custom protocol handler, a UNC-style path, etc.) is passed to `shell.openExternal` unchanged.

This mirrors the `rescue` bug class: a function that performs a consequential action (here, invoking the OS shell/protocol-handler resolution machinery) with **no check on who or what supplied the input**, allowing a party that never should have that capability (a PR commenter) to trigger behavior intended only for links the user typed or that came from trusted, first-party GitHub UI.

### Impact Explanation
`shell.openExternal` with an unconstrained scheme is a well-documented Electron footgun: depending on what's installed on the victim's machine, a crafted URI (custom protocol handlers registered by other installed applications, `file://` links to local scripts/executables, or Windows-specific handler argument-injection patterns) can result in unintended local application launches or, in vulnerable third-party handlers, command execution. Even absent full RCE, it enables reliably tricking the victim into opening attacker-chosen local paths/handlers merely by leaving a comment on a PR the victim reviews in Desktop — a zero-interaction-beyond-one-click primitive originating entirely from remote, attacker-supplied content.

### Likelihood Explanation
High: no local access, no admin rights, and no unnatural user steps are required — commenting on a public PR (or a PR in a repo the victim has cloned) is a normal, low-privilege GitHub action, and clicking a link in a PR comment is expected user behavior that Desktop's own UI explicitly supports (`onMarkdownLinkClicked`). The `open-external` IPC handler applies no allow-list, so the guard that would normally stop this (restricting to `http:`/`https:`) simply does not exist in the reachable code.

### Recommendation
In the `open-external` IPC handler (and/or centrally in `dispatcher.openInBrowser`), enforce an explicit scheme allow-list (`http:` and `https:` only) before calling `shell.openExternal`, rejecting or warning on any other scheme, consistent with Electron's own security guidance on validating `openExternal` targets.

### Proof of Concept
1. Attacker opens or comments on a pull request (or leaves a PR review) in any repository the victim has added to GitHub Desktop, embedding a Markdown link with a non-`http(s)` URI, e.g. `[click me](file:///some/path)` or a URI for a locally-registered custom protocol handler.
2. Victim opens that PR's comments/checks-failed notification in Desktop, which renders the body via `SandboxedMarkdown`. [4](#0-3) 
3. Victim clicks the link; `onMarkdownLinkClicked` calls `dispatcher.openInBrowser(url)` with the raw attacker-supplied URL. [2](#0-1) 
4. This reaches the `open-external` IPC handler, which calls `shell.openExternal(path)` with no scheme check gating execution. [1](#0-0) 

Note: I was not able to fully trace whether `SandboxedMarkdown`'s renderer performs any scheme sanitization before invoking `onMarkdownLinkClicked` (that component's internals were not returned by my searches), so I cannot confirm with certainty that no upstream filtering exists — this would need direct inspection of `SandboxedMarkdown`'s source, which the index did not surface. If you need full certainty, a Devin session with full repo access could verify this specific path end-to-end.

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

**File:** app/src/ui/notifications/pull-request-comment-like.tsx (L162-164)
```typescript
  private onMarkdownLinkClicked = (url: string) => {
    this.props.dispatcher.openInBrowser(url)
  }
```

**File:** app/src/ui/notifications/pull-request-comment-like.tsx (L166-182)
```typescript
  private renderReviewBody() {
    const { body, emoji, pullRequest } = this.props
    const { base } = pullRequest

    return (
      <SandboxedMarkdown
        markdown={body}
        emoji={emoji}
        baseHref={base.gitHubRepository.htmlURL ?? undefined}
        repository={base.gitHubRepository}
        onMarkdownLinkClicked={this.onMarkdownLinkClicked}
        markdownContext={'PullRequestComment'}
        underlineLinks={this.props.underlineLinks}
        ariaLabel="Pull request markdown comment"
      />
    )
  }
```

**File:** app/src/lib/stores/app-store.ts (L7594-7597)
```typescript
  /** Takes a URL and opens it using the system default application */
  public _openInBrowser(url: string): Promise<boolean> {
    return shell.openExternal(url)
  }
```
