### Title
Unvalidated URI scheme passed from renderer-rendered repository content to `shell.openExternal` - (File: `app/src/main-process/main.ts`, `app/src/ui/lib/link-button.tsx`)

### Summary
`LinkButton`, used by `RichText` to render links found inside repository-derived text (commit messages, PR/issue titles and bodies fetched from the GitHub API, release notes), calls `shell.openExternal(uri)` directly on click with no protocol check. The main-process IPC handler for `open-external` also does not enforce an allow-list of schemes before invoking `shell.openExternal`. This is inconsistent with `SandboxedMarkdown`, which explicitly restricts link navigation to `http(s):` protocols before forwarding a URL to be opened externally.

### Finding Description
`RichText`'s `getElements` renders `TokenType.Link` tokens as `<LinkButton uri={token.url}>` [1](#0-0) . `LinkButton`'s click handler unconditionally calls `shell.openExternal(uri)` with no scheme validation [2](#0-1) . That `shell` wrapper forwards to the renderer-to-main IPC bridge's `openExternal` [3](#0-2) , which is handled in the main process by `ipcMain.handle('open-external', ...)`. That handler only logs when the path starts with `http://`/`https://` but does not reject or sanitize any other scheme before calling `shell.openExternal(path)` [4](#0-3) .

By contrast, the sandboxed markdown renderer (used for rendered Markdown, e.g. release notes body) explicitly restricts navigable links to `http(s):` before invoking the same external-open callback: `if (/^https?:/.test(a.protocol)) { this.props.onMarkdownLinkClicked?.(a.href) }` [5](#0-4) . `RichText`/`LinkButton`, which is used for plain-text rich rendering of commit messages, PR/issue text, and release note line items (`entry.message` from `ReleaseNote`) [6](#0-5) , has no equivalent guard.

The broken invariant is: "any URI that reaches `shell.openExternal` must have been validated to be a benign, navigable web URL." That invariant is enforced in the sandboxed Markdown path but not in the `LinkButton`/`RichText` path or in the main-process IPC handler, which is supposed to be the last line of defense but does not perform any allow-listing either.

### Impact Explanation
If the `Tokenizer` (`app/src/lib/text-token-parser.ts`) produces link tokens for schemes other than `http`/`https` from attacker-controlled repository content (e.g. an issue/PR body or commit message containing a crafted URI), clicking that rendered link results in `shell.openExternal` being invoked with an unvalidated string. Electron's `shell.openExternal` on Windows is known to resolve certain crafted strings (e.g. UNC paths, custom registered protocol handlers, or executable-resolving strings) to launch local applications or leak NTLM credentials to attacker-controlled hosts, without further user confirmation beyond the initial link click. This would give an attacker who controls repository/API content a path to code execution or credential exfiltration triggered by a single click inside Desktop's UI, matching the "attacker controls a GitHub API object or a link the user clicks" impact category.

### Likelihood Explanation
Exploitability hinges on whether the `Tokenizer` restricts matched URL tokens strictly to `http(s)`. I could not verify the contents of `text-token-parser.ts` with the tools available in this session, so I cannot confirm whether non-`http(s)` schemes can actually reach a `Link` token today. What is confirmed is that the two rendering paths (`sandboxed-markdown.tsx` vs. `link-button.tsx`) apply inconsistent protocol enforcement, and the main-process `open-external` handler is not a safety net since it doesn't reject any scheme.

### Recommendation
- Add an explicit scheme allow-list (`http:`/`https:`, and possibly `mailto:`) in `LinkButton.onClick` before calling `shell.openExternal`, mirroring the check already present in `sandboxed-markdown.tsx`.
- Enforce the same allow-list defensively in the main-process `open-external` IPC handler in `app/src/main-process/main.ts`, rejecting (not just logging) non-`http(s)` schemes, since the renderer is the untrusted boundary and IPC handlers should not assume callers already validated input.
- Audit `text-token-parser.ts` to confirm it only ever emits `Link` tokens for `http(s)` URLs.

### Proof of Concept
Conceptual PoC (pending confirmation of `Tokenizer` behavior):
1. Attacker creates a GitHub issue/PR/commit whose text contains a crafted non-`http(s)` URI recognized by the `Tokenizer` as a link (e.g., a `file://` path or a registered custom protocol pointing to a local resource).
2. Desktop renders this text via `RichText`, producing a `<LinkButton uri="<crafted-uri>">`.
3. The user clicks the rendered link inside Desktop.
4. `LinkButton.onClick` calls `shell.openExternal(uri)` with no scheme check [7](#0-6) , and the main process's `open-external` handler forwards it unchanged to Electron's `shell.openExternal` [8](#0-7) , potentially triggering unintended local resource access.

### Citations

**File:** app/src/ui/lib/rich-text.tsx (L62-69)
```typescript
      case TokenType.Link:
        if (renderUrlsAsLinks !== false) {
          const title = token.text !== token.url ? token.url : undefined
          return (
            <LinkButton key={index} uri={token.url} title={title}>
              {token.text}
            </LinkButton>
          )
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

**File:** app/src/ui/lib/sandboxed-markdown.tsx (L292-304)
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
```

**File:** app/src/ui/release-notes/release-notes-dialog.tsx (L36-46)
```typescript
    for (const [i, entry] of releaseEntries.entries()) {
      options.push(
        <li key={i}>
          <RichText
            text={entry.message}
            emoji={this.props.emoji}
            renderUrlsAsLinks={true}
            repository={DesktopFakeRepository}
          />
        </li>
      )
```
