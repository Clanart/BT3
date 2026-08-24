## Title
Missing regex anchoring in `issueUrl()` allows link-text spoofing in `IssueLinkFilter`, causing malicious hrefs to be disguised as trusted `#N` issue references - (File: `app/src/lib/markdown-filters/issue-link-filter.ts`)

### Summary
`issueUrl()` builds a `RegExp` used both to decide whether an anchor is a "GitHub issue/pull/discussion link" and to rewrite its visible text, but the pattern has no `^`/`$` (or `\b`-based) anchors constraining it to the *entire* string. `RegExp.prototype.test`/`match` succeed as long as the trusted GitHub issue URL appears anywhere as a substring of the anchor's `href`/`textContent`. This lets an attacker embed a full trusted-repo issue URL as a query parameter (or any substring position) of an otherwise attacker-controlled URL and have `IssueLinkFilter` relabel it with a trusted-looking `#N` reference while leaving the real, malicious `href` intact.

### Finding Description
`issueUrl()` constructs the pattern without anchoring: [1](#0-0) 

`isGitHubIssuePullDiscussionLink()` uses this unanchored regex as the final gate for accepting an anchor into the tree walker, after only checking the href contains `issue|pull|discussion`, isn't a pull-request-tab URL, and doesn't end in a `.ext`-like suffix: [2](#0-1) 

The tree walker only accepts anchors where `el.href === el.innerText` (i.e., raw auto-linked URLs, which `marked`'s GFM autolinking produces with identical href/text): [3](#0-2) 

`filter()` then matches the anchor's `textContent` against the same unanchored regex, extracts `refNumber`/`anchor` from wherever they occur in the string, and **only replaces `textContent`** on a clone of the original node — the `href` attribute is preserved verbatim from the original (malicious) anchor: [4](#0-3) 

Because a bare autolinked URL like `https://evil.example.com/?u=https://github.com/owner/repo/issues/1` has `href === innerText` equal to that full string, and the trusted GitHub issue URL appears as a substring, all three of `isIssuePullOrDiscussion`, the negative checks, and `issueUrl(repository).test(anchor.href)` pass. The filter then rewrites the visible text to `#1 (comment)` while the anchor's `href` remains `https://evil.example.com/?u=https://github.com/owner/repo/issues/1`.

At render time, `SandboxedMarkdown.setupLinkInterceptor()` only checks the anchor's `protocol` is `http(s):` before invoking `onMarkdownLinkClicked(a.href)`; it does not validate the host against the trusted repository's GitHub endpoint: [5](#0-4) 

Every consumer of `SandboxedMarkdown` wires `onMarkdownLinkClicked` straight to `dispatcher.openInBrowser(url)`, e.g.: [6](#0-5) [7](#0-6) 

and the main process `open-external` IPC handler forwards to `shell.openExternal(path)` after only a protocol-based logging check, with no host allow-list: [8](#0-7) 

So the full chain — untrusted markdown content (issue body, PR comment, review, etc.) → `IssueLinkFilter` relabels a malicious href as `#1` → user clicks trusting the `#1` label → `setupLinkInterceptor` → `onMarkdownLinkClicked` → `dispatcher.openInBrowser` → `shell.openExternal` — opens the attacker's URL in the user's default browser while the UI displayed a benign-looking `#1` issue reference.

### Impact Explanation
This is a link/label spoofing issue that can be used to trick a user into visiting an attacker-controlled URL (e.g., a phishing page or an OAuth-consent-mimicking page) by disguising it as an internal repository issue link. `shell.openExternal` opens in the OS default browser (not Electron), so the immediate impact is limited to browser-based phishing/credential harvesting rather than direct code execution or IPC/sandbox escape. The attacker fully controls the payload merely by posting a comment/issue body/PR description in a repository the victim views in Desktop.

### Likelihood Explanation
High feasibility: any GitHub user (not necessarily with write/push access) can post an issue comment, PR description, or discussion post containing a raw autolinked URL such as `https://evil.example.com/?u=https://github.com/<owner>/<repo>/issues/1`. GFM autolinking (`marked`, `gfm: true`) will produce an anchor with `href === textContent` equal to that full string, satisfying the tree-walker's equality precondition. No special repo permissions or unusual user interaction beyond a normal click on what looks like a `#1` issue reference are required.

### Recommendation
Anchor the regexp in `issueUrl()` to match the entire href value (e.g., wrap with `^` and `$`, or verify `new URL(anchor.href).href` equals the matched substring / that the match spans the full string), and additionally validate in `isGitHubIssuePullDiscussionLink()`/`filter()` that the matched issue URL constitutes the whole `anchor.href` (not merely a substring) before rewriting the visible text. Consider also validating `new URL(anchor.href).origin` equals `getHTMLURL(repository.endpoint)`'s origin explicitly.

### Proof of Concept
Render the following markdown through `SandboxedMarkdown` with a `repository` whose `htmlURL` is `https://github.com`:

```
https://evil.example.com/?u=https://github.com/owner/repo/issues/1
```

Expected/observed behavior:
1. `marked` autolinks this into `<a href="https://evil.example.com/?u=https://github.com/owner/repo/issues/1">https://evil.example.com/?u=https://github.com/owner/repo/issues/1</a>` (href === innerText).
2. `IssueLinkFilter.createFilterTreeWalker`'s `acceptNode` accepts it because `issueUrl(repository).test(anchor.href)` is true (substring match).
3. `filter()` replaces the node's `textContent` with `#1 (comment)` while leaving `href` unchanged.
4. Inspecting the resulting DOM: `anchor.textContent === '#1 (comment)'` but `new URL(anchor.href).host === 'evil.example.com'`.
5. Clicking the rendered link triggers `setupLinkInterceptor` → `onMarkdownLinkClicked('https://evil.example.com/?u=...')` → `dispatcher.openInBrowser` → `shell.openExternal`, opening the attacker's domain despite the displayed `#1` label. [1](#0-0) [4](#0-3)

### Citations

**File:** app/src/lib/markdown-filters/issue-link-filter.ts (L9-22)
```typescript
export function issueUrl(repository: GitHubRepository): RegExp {
  const gitHubURL = getHTMLURL(repository.endpoint)
  return new RegExp(
    escapeRegExp(gitHubURL) +
      '/' +
      /** A regexp that searches for the owner/name pattern in issue href */
      /(?<nameWithOwner>\w+(?:-\w+)*\/[.\w-]+)/.source +
      '/' +
      /(?:issues|pull|discussions)/.source +
      '/' +
      /** A regexp that searches for the number and #anchor of an issue reference */
      /(?<refNumber>\d+)(?<anchor>#[\w-]+)?\b/.source
  )
}
```

**File:** app/src/lib/markdown-filters/issue-link-filter.ts (L59-71)
```typescript
  public createFilterTreeWalker(doc: Document): TreeWalker {
    return doc.createTreeWalker(doc.body, NodeFilter.SHOW_ELEMENT, {
      acceptNode: (el: Element) => {
        return (el.parentNode !== null &&
          ['CODE', 'PRE', 'A'].includes(el.parentNode.nodeName)) ||
          !isElement(el, 'a') ||
          el.href !== el.innerText ||
          !this.isGitHubIssuePullDiscussionLink(el)
          ? NodeFilter.FILTER_SKIP
          : NodeFilter.FILTER_ACCEPT
      },
    })
  }
```

**File:** app/src/lib/markdown-filters/issue-link-filter.ts (L77-96)
```typescript
  private isGitHubIssuePullDiscussionLink(anchor: HTMLAnchorElement) {
    const isIssuePullOrDiscussion = /(issue|pull|discussion)/.test(anchor.href)
    if (!isIssuePullOrDiscussion) {
      return false
    }

    const isPullRequestTab = /\d+\/(files|commits|conflicts|checks)/.test(
      anchor.href
    )
    if (isPullRequestTab) {
      return false
    }

    const isURlCustomFormat = /\.[a-z]+\z/.test(anchor.href)
    if (isURlCustomFormat) {
      return false
    }

    return issueUrl(this.repository).test(anchor.href)
  }
```

**File:** app/src/lib/markdown-filters/issue-link-filter.ts (L107-126)
```typescript
  public async filter(node: Node): Promise<ReadonlyArray<Node> | null> {
    const { textContent: text } = node
    if (!isElement(node, 'a') || text === null) {
      return null
    }

    const match = text.match(issueUrl(this.repository))
    if (match === null || match.groups === undefined) {
      return null
    }

    const { refNumber, anchor } = match.groups
    const newNode = node.cloneNode(true)
    newNode.textContent = this.getConsistentIssueReferenceText(
      refNumber,
      anchor
    )

    return [newNode]
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
