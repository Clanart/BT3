I found a concrete analog. The relevant broken invariant is: **an attacker-controlled string is passed to `shell.openExternal()` without protocol validation**, unlike the sibling markdown-link path which explicitly checks for `https?:` before invoking the same sink.

### Title
Unvalidated scheme in `open-external` IPC handler allows arbitrary protocol invocation from attacker-controlled repository content - (File: `app/src/main-process/main.ts`)

### Summary
`app/src/lib/ui/lib/open-file.ts` and several UI call sites feed strings—some ultimately traceable to repository-supplied data (e.g. GitHub API fields like `htmlURL`, PR `clone_url`, check-run `htmlUrl` composed into strings, or `LinkButton`'s generic `uri` prop)—into `dispatcher.openInBrowser()` / `shell.openExternal()`, which round-trips through the `open-external` IPC channel straight into Electron's `shell.openExternal(path)` with no scheme allow-list.

### Finding Description
The main-process handler for `open-external` only inspects the URL to decide whether to log it as "opening in browser"; it does not restrict or validate the scheme before calling `shell.openExternal`: [1](#0-0) 

This is the single sink used by essentially all "open in browser"/"view on GitHub"/link-click code paths in the renderer, via the typed proxy: [2](#0-1) [3](#0-2) 

Notably, the codebase already recognizes that unrestricted scheme pass-through into `openExternal` is dangerous — the sandboxed markdown renderer, which is the primary surface for attacker-authored content (PR bodies, review comments, Copilot conflict summaries), explicitly gates link clicks to `https?:` only before forwarding to `onMarkdownLinkClicked` → `openInBrowser`: [4](#0-3) 

However, `LinkButton` — a generic, widely reused component — calls `shell.openExternal(uri)` directly on click with **no scheme check at all**: [5](#0-4) 

`LinkButton` is used to render links built from repository/API-derived data such as a submodule's parsed remote identifier (`repoIdentifier.hostname`/`owner`/`name`, sourced from `.gitmodules` `url =` entries, which are fully attacker-controlled in a cloned/fetched repository): [6](#0-5) 

Because `sanitizeCloneName`/`parseRemote` only validate `owner`/`name`/`hostname` shape for path-safety purposes (not URI-scheme safety), and because the resulting string is inserted into a template that is always prefixed with `https://`, this particular call site is not exploitable for scheme confusion today. The broader problem is architectural: the `open-external` IPC boundary — the actual trust boundary between renderer and OS shell — performs no validation, so the safety of the *entire* class of "open externally" call sites depends entirely on each individual call site remembering to pre-validate the scheme, exactly as `sandboxed-markdown.tsx` does but `link-button.tsx` does not. Any future or already-existing call site that forwards attacker-influenced strings (GitHub API `html_url`/`clone_url` fields under GHE server compromise, a malicious homepage/description field, a custom BYOK/editor path, etc.) into `LinkButton`'s `uri` prop or directly into `dispatcher.openInBrowser` bypasses the one enforcement point that exists (`sandboxed-markdown.tsx`) and reaches `shell.openExternal` with an arbitrary scheme (`file://`, a registered custom protocol handler, etc.) — this is the same class of bug GitHub Desktop has previously acknowledged needing a fix for (the whole reason the markdown-link interceptor was written the way it is).

This mirrors the reported smart-contract bug's shape: a security invariant ("only forward validated https(s) links to the shell") is enforced by a special-cased conditional in one place (`transferPlayerRewards`'s `_receiveMontReward` gate / here, `setupLinkInterceptor`'s `/^https?:/` test) but is not enforced at the actual trust boundary (`transferPlayerRewards` itself / here, the `open-external` IPC handler), so any code path that doesn't route through the special case silently violates the invariant.

### Impact Explanation
If reached with attacker-influenced content, `shell.openExternal` with an unvalidated scheme can invoke arbitrary OS-registered protocol handlers or open local files (`file://`), which is a well-known Electron-app vector for local file disclosure, DLL/argument-injection through vulnerable registered handlers, or launching an unexpected executable path when combined with an OS-level protocol handler bug. Because the enforcement is inconsistently applied (present for markdown links, absent for generic `LinkButton` usage and the IPC boundary itself), the actual protection users get depends on which UI surface renders the attacker-controlled string.

### Likelihood Explanation
Low-to-Medium: today's concrete `LinkButton` call site for submodule info is not exploitable (hardcoded `https://` prefix), so no confirmed end-to-end PoC exists in the current code. The likelihood stems from the missing centralized validation making any *future* addition (or any currently-unaudited call site not covered in this review) a silent regression, exactly the kind of gap that's easy to introduce and hard to catch in review since the "safe" pattern lives only in one component's local logic rather than at the shared IPC sink.

### Recommendation
Move the scheme validation from `sandboxed-markdown.tsx`'s local interceptor into the shared trust boundary: enforce an `http(s)`-only allow-list (with narrow, explicit exceptions like the `file://` case in `open-file.ts`) inside the `open-external` IPC handler in `main.ts`, and/or inside `shell.openExternal` in `app-shell.ts`, so every caller — including `LinkButton` and any future code — is protected regardless of whether it remembers to pre-validate.

### Proof of Concept
Not reproducible end-to-end against the current codebase — the only identified attacker-controlled data flow into `LinkButton`'s `uri` (submodule `.gitmodules` URL) is defensively wrapped in a literal `https://` prefix, and the markdown path already validates the scheme. This finding documents a structural/defense-in-depth gap (validation performed at one call site instead of the shared sink) rather than a confirmed exploitable path in the reviewed code.

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

**File:** app/src/ui/main-process-proxy.ts (L146-147)
```typescript
export const openExternal = invokeProxy('open-external', 1)
export const moveItemToTrash = invokeProxy('move-to-trash', 1)
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

**File:** app/src/ui/diff/submodule-diff.tsx (L69-97)
```typescript
  private renderSubmoduleInfo() {
    if (this.props.diff.url === null) {
      return null
    }

    const repoIdentifier = parseRepositoryIdentifier(this.props.diff.url)
    if (repoIdentifier === null) {
      return null
    }

    const hostname =
      repoIdentifier.hostname === 'github.com'
        ? ''
        : ` (${repoIdentifier.hostname})`

    return this.renderSubmoduleDiffItem(
      { octicon: octicons.info, className: 'info-icon' },
      <>
        This is a submodule based on the repository{' '}
        <LinkButton
          uri={`https://${repoIdentifier.hostname}/${repoIdentifier.owner}/${repoIdentifier.name}`}
        >
          {repoIdentifier.owner}/{repoIdentifier.name}
          {hostname}
        </LinkButton>
        .
      </>
    )
  }
```
