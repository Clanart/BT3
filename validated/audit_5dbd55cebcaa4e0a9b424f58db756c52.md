Based on the investigation, I found a concrete Desktop analog: an unrestricted `shell.openExternal` sink reachable from attacker-controlled repository content (commit messages, PR/issue bodies, release notes rendered as markdown), mirroring the report's "public function that should be gated" pattern — here the gate (protocol validation) exists in comment/intent but is never actually enforced before the privileged OS call.

### Title
Unrestricted `open-external` IPC handler allows arbitrary URI-scheme dispatch from repository/link content, enabling local code execution via OS protocol handlers - (File: app/src/main-process/main.ts)

### Summary
The `open-external` IPC handler in the main process only *inspects* the URL scheme to decide whether to log it as "opening in browser," but it unconditionally forwards the value to Electron's `shell.openExternal`, regardless of scheme. [1](#0-0) 

This handler is invoked from renderer UI components such as `LinkButton`, which calls `shell.openExternal(uri)` whenever a user clicks any rendered link, including links produced by parsing untrusted repository content (commit messages, linkified issue/PR references, markdown bodies). [2](#0-1) 

### Finding Description
Electron's own security guidance explicitly warns against passing untrusted, non-validated URLs to `shell.openExternal`, because on some platforms (particularly Windows) arbitrary URI schemes can be routed to other installed applications/registered protocol handlers with attacker-controlled arguments, which historically has enabled remote code execution outside the Electron sandbox.

In this codebase, the intended guard is present only as a no-op: the scheme check exists purely to decide the *log message* ("opening in browser") — it does not `return`/reject when the scheme is anything other than `http://`/`https://`: [1](#0-0) 

The IPC channel itself is otherwise properly protected by the trusted-sender check (`isTrustedIPCSender`), so the vulnerability is not "any process can call it" — it's that the *renderer's own legitimate call path*, when fed attacker-supplied link text from repository content, can smuggle an arbitrary protocol/URI through to the OS shell. [3](#0-2) 

The reachable sink is the generic `LinkButton` component, used broadly across the UI to render clickable URIs, including ones derived from parsed/linkified repository content (commit mentions, issue/PR links, etc. produced by the markdown/text-token pipeline). [4](#0-3) [5](#0-4) 

### Impact Explanation
This satisfies the required impact class: "attacker controls ... a link ... the user clicks" and the result is potential "code execution ... outside the repo" / sandbox escape. A malicious commit author, PR author, or issue author can craft link/text content that, once rendered and clicked inside Desktop, is passed unchanged to `shell.openExternal` with a non-http(s) scheme, letting the OS resolve it against arbitrary registered protocol/URI handlers rather than a browser — a known Electron code-execution vector distinct from ordinary "open a link in a browser" behavior.

### Likelihood Explanation
The click-through requires normal user interaction (clicking a link shown in the app), which is a natural, expected user action in Desktop's UI (viewing commit/issue/PR content), not an unnatural/social-engineering step. The gating logic already exists in the code (implying the developers intended to restrict this), but it was implemented incorrectly (log-only, not enforcing), which increases the likelihood this is an unintentional regression rather than an accepted risk.

### Recommendation
In the `open-external` handler, reject (return `false`/throw) for any URL whose scheme is not `http:` or `https:` before calling `shell.openExternal`, rather than only branching for logging purposes:
```ts
ipcMain.handle('open-external', async (_, path: string) => {
  const pathLowerCase = path.toLowerCase()
  if (!pathLowerCase.startsWith('http://') && !pathLowerCase.startsWith('https://')) {
    log.error(`Refusing to open non-http(s) URL: ${path}`)
    return false
  }
  ...
})
```
Apply the same allow-list check at the `LinkButton`/renderer layer as defense in depth, since link content can originate directly from untrusted repository data.

### Proof of Concept
1. Attacker opens a pull request or pushes a commit whose message/body contains text that Desktop's markdown/text-token pipeline linkifies into a clickable URL with a non-`http(s)` scheme (e.g., a custom registered protocol handler URI).
2. Victim opens the PR/commit/issue view in GitHub Desktop and clicks the rendered link.
3. `LinkButton.onClick` calls `shell.openExternal(uri)` with the attacker-supplied scheme. [6](#0-5) 
4. The IPC `open-external` handler in the main process logs the URL differently depending on scheme, but forwards it to Electron's `shell.openExternal` unconditionally, allowing the OS to hand off execution to whatever handler is registered for that scheme. [1](#0-0) 

**Note on completeness:** I was not able to view the contents of `app/src/lib/app-shell.ts` (the wrapper `LinkButton` imports `shell` from) within the tool budget, so the exact intermediate call (whether it goes through the `open-external` IPC channel directly or some other path) is inferred from naming conventions and the `main-process-proxy.ts` `openExternal` export rather than confirmed line-by-line. A Devin session with full file access should verify `app-shell.ts` to close this gap before treating the PoC as fully confirmed end-to-end.

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

**File:** app/src/main-process/ipc-main.ts (L53-66)
```typescript
function safeListener<E extends IpcMainEvent | IpcMainInvokeEvent, R>(
  listener: (event: E, ...a: any) => R
) {
  return (event: E, ...args: any) => {
    if (!isTrustedIPCSender(event.sender)) {
      log.error(
        `IPC message received from invalid sender: ${event.senderFrame?.url}`
      )
      return
    }

    return listener(event, ...args)
  }
}
```

**File:** app/src/lib/text-token-parser.ts (L38-52)
```typescript
export type HyperlinkMatch = {
  readonly kind: TokenType.Link
  // The text to display inside the rendered link, e.g. @shiftkey
  readonly text: string
  // The URL to launch when clicking on the link
  readonly url: string
}

export type PlainText = {
  readonly kind: TokenType.Text
  // The text to render.
  readonly text: string
}

export type TokenResult = PlainText | EmojiMatch | HyperlinkMatch
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
