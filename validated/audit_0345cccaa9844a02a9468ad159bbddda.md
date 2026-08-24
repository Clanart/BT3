## Title
Incomplete Extension Blocklist Allows Execution of Attacker-Controlled Files via "Open with Default Program" - (File: `app/src/ui/lib/context-menu.ts`)

## Summary
GitHub Desktop's `isSafeFileExtension` function is meant to prevent the "Open with Default Program" context-menu action from launching dangerous file types. Like the ERC-721/ERC-20 report, this is a duck-typing/allowlist-vs-blocklist mismatch: the function assumes a small, hardcoded set of extensions is dangerous, and treats everything else — including files that behave like executables — as "safe," even though the underlying primitive (`shell.openExternal`) will happily execute them.

## Finding Description
`isSafeFileExtension` is defined as: [1](#0-0) 

On Windows it only blocks four extensions (`.cmd`, `.exe`, `.bat`, `.sh`) and returns `true` for everything else; on non-Windows platforms it unconditionally returns `true` for **every** extension, including `.command`, `.workflow`, `.scpt`, `.app` bundles referenced by path, `.desktop`, `.pkg`, `.msi`, `.ps1`, `.vbs`, `.js`, `.jar`, `.scr`, `.hta`, `.wsf`, `.msc`, `.lnk`, etc. — none of which are on the blocklist despite being executable via the OS shell association.

This value feeds directly into the "Open with Default Program" menu item across multiple UI surfaces that render attacker-influenced content: the Changes tab, the commit-history file list, and the Open Pull Request file-diff viewer: [2](#0-1) [3](#0-2) [4](#0-3) 

When enabled, the action calls `openFile`, which invokes `shell.openExternal('file://<path>')` with no further validation: [5](#0-4) 

`shell.openExternal` on `file://` URLs is equivalent to double-clicking the file in the OS — it launches whatever handler is registered for that extension, which for the extensions above means direct code execution (shell scripts, PowerShell, JScript/VBScript hosts, or macOS `open`-launchable bundles/scripts).

## Impact Explanation
A user who clones or fetches an attacker-controlled repository, or who reviews a pull request's changed files inside Desktop, can be induced to click a legitimately-labeled "Open with Default Program" entry on a file that the app told them was safe to open (the menu item is `enabled`). This results in unprompted execution of attacker-supplied code on the user's machine with the user's own privileges — no local/physical access, admin rights, or pre-existing malware needed, matching the "unprivileged attacker controls a cloned/fetched repository ... result is code execution" impact class named in the task.

## Likelihood Explanation
The action requires a single natural user interaction (right-click → "Open with Default Program"), which is an intended and commonly used feature for reviewing changed files, not an "unnatural" step. Because the guard function silently reports safety for the vast majority of dangerous extensions (all of them on macOS/Linux, and most on Windows), the existing check gives users false confidence without actually restricting anything meaningful — the same "fallback assumption is wrong" defect described in the ERC-721 report, where the decimals()-based type check let an unsupported asset type flow through a path built for a different type.

## Recommendation
Replace the tiny hardcoded blocklist in `isSafeFileExtension` with a real allowlist of extensions known to be safe to hand to the OS shell (documents, images, text, etc.), defaulting to "unsafe" for anything not recognized, on all platforms — not just Windows. Alternatively, disable "Open with Default Program" entirely for files whose extension is executable-capable per platform (`.command`, `.app`, `.workflow`, `.scpt`, `.pkg`, `.msi`, `.ps1`, `.vbs`, `.js`, `.jar`, `.scr`, `.hta`, `.wsf`, `.msc`, `.lnk`, etc.), and keep the list under active maintenance.

## Proof of Concept
1. Create/clone a repository under attacker control that includes a committed file with a non-blocklisted-but-executable extension, e.g. `payload.command` on macOS (a shell script) or `payload.msi` / `payload.hta` on Windows.
2. Open the repository in GitHub Desktop; the file shows up in the Changes tab (or in an opened pull request's file list).
3. Right-click the file → the "Open with Default Program" item is enabled because `isSafeFileExtension('.command')` (or `.msi`/`.hta`) returns `true`. [6](#0-5) 
4. Clicking it calls `openFile` → `shell.openExternal('file://...')`, which launches the file via its OS handler, executing attacker-controlled code. [7](#0-6)

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

**File:** app/src/ui/changes/filter-changes-list.tsx (L790-800)
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

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L178-200)
```typescript
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
```

**File:** app/src/ui/lib/open-file.ts (L1-17)
```typescript
import { shell } from '../../lib/app-shell'
import { Dispatcher } from '../dispatcher'

export async function openFile(
  fullPath: string,
  dispatcher: Dispatcher
): Promise<void> {
  const result = await shell.openExternal(`file://${fullPath}`)

  if (!result) {
    const error = {
      name: 'no-external-program',
      message: `Unable to open file ${fullPath} in an external program. Please check you have a program associated with this file extension`,
    }
    await dispatcher.postError(error)
  }
}
```
