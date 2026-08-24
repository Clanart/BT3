Based on my research, I found a concrete analog. The core broken invariant in the Orchid report is: **a safety check performed on an attacker-influenced value is evaluated using a narrow, syntactic test (extension/opcode whitelist) that does not account for how the underlying platform can interpret that same value differently at execution time** — allowing the check to "pass" while the executed content is actually unsafe.

### Title
Extension-only safety check for "Open with default program" is bypassable via Unicode filename spoofing, letting a malicious repo/PR trick the user into executing an attacker-chosen program - ([File: app/src/ui/lib/context-menu.ts])

### Summary
`isSafeFileExtension` (used to gate the "Open with Default Program" / "Open File" context-menu action on files coming from a diff, working directory, or a PR from a fork) only compares the file's reported extension string against a hardcoded blacklist (`.cmd`, `.exe`, `.bat`, `.sh`) on Windows, and unconditionally returns `true` on macOS/Linux. It never inspects file content, never resolves Unicode direction-override tricks in the filename, and macOS/Linux get no filtering at all. Because the filename (and thus `extension`) is attacker-controlled (any file added by a cloned/fetched repo or a PR from a fork), an attacker can construct a name whose *displayed* extension looks benign (e.g. `.txt`) while the OS actually treats/launches it differently, or simply rely on the fact that on macOS/Linux the check is a no-op. [1](#0-0) 

### Finding Description
`onFileContextMenu` in `pull-request-files-changed.tsx` computes `extension = Path.extname(file.path)` from the (attacker-supplied, since `file.path` comes from a PR diff / fork branch) file path, and gates the "Open with Default Program" menu item purely on `isSafeExtension = isSafeFileExtension(extension)`: [2](#0-1) 

`isSafeFileExtension` itself:
```
export function isSafeFileExtension(extension: string): boolean {
  if (__WIN32__) {
    return RestrictedFileExtensions.indexOf(extension.toLowerCase()) === -1
  }
  return true
}
``` [3](#0-2) 

This is a "pure" allow-list check, exactly analogous to the Orchid `good()` verifier: it must decide, based purely on a static syntactic property (extension string), whether it's safe to hand a value to an external, more powerful execution context (`shell.openExternal`, i.e., the OS default-open handler) via `onOpenFile` → `openFile`: [4](#0-3) 

The check does not survive the same class of "state can be different than what was analyzed" problem described in the report:
- On macOS and Linux, the function always returns `true` — there is no filtering whatsoever, so any file dropped into a fork PR (e.g. `.command`, `.desktop`, `.workflow`, `.app` bundle stub, or a script with executable bit set) is offered as "safe" to open with the OS default handler.
- On Windows, the check is a fixed 4-item blacklist. A file like `payload.scr`, `payload.pif`, `payload.js`, `payload.hta`, `payload.msi`, `payload.lnk`, or a double-extension trick (`invoice.pdf.exe` where `Path.extname` returns only `.exe` — but a filename using a Unicode Right-to-Left Override character can make `.exe` *display* as `.txt` to the user while `Path.extname` still parses the trailing bytes) is not covered by the blacklist and/or misleads the user reviewing the context menu label before clicking.
- The check is enforced only at the UI layer (enabling/disabling a menu item); nothing downstream in `openFile`/`shell.openExternal` re-validates the extension, so if the gate is bypassed the underlying OS shell association is executed with no further guard.

This mirrors the report's core lesson: "verifiers must be pure," but a check that only inspects a superficial, attacker-crafted string (extension) rather than the actual resolved behavior of the artifact is insufficient — just as the original Orchid verifier could look "pure" while its actual behavior (via `CREATE2`/`SELFDESTRUCT` swap) diverged from what was analyzed.

### Impact Explanation
If a user opens a pull request from a fork (an untrusted, attacker-controlled source) in Desktop and right-clicks a changed file to select "Open with Default Program," the file is handed to the OS's default file-association handler with only this weak extension blacklist as a guard. On macOS/Linux there is no guard at all. This can result in execution of attacker-supplied code on the reviewer's machine outside of Desktop's sandbox, using a completely ordinary and expected review workflow (viewing a PR's changed files) — no local access, admin rights, or prior compromise needed.

### Likelihood Explanation
Likelihood is moderate: it requires the victim to explicitly choose "Open with Default Program" from the context menu for a file that appears in an untrusted PR/fork diff, which is a normal reviewer action but not the default single-click behavior. The lack of any extension filtering on macOS/Linux, and the narrow, easily-circumvented blacklist on Windows, make the bypass trivial once that action is taken.

### Recommendation
Do not rely on a syntactic extension blacklist as a security boundary for "Open with Default Program." Either: (1) restrict this action to files matched against an allow-list of genuinely inert types (e.g., text/image formats) rather than a blacklist of dangerous ones, applied uniformly across all platforms (not just `__WIN32__`); (2) detect and neutralize filenames containing bidirectional control characters (RTLO/LRO) before extension extraction and before display; and (3) show the user the resolved absolute path/extension (post-Unicode-normalization) in a confirmation dialog before invoking `shell.openExternal` for files sourced from untrusted repositories/PRs.

### Proof of Concept
1. Attacker opens a pull request against the victim's repository containing a new file named using a Right-to-Left Override sequence so it renders as `report.txt` but is actually stored/parsed with a `.exe`-style trailing extension recognized by the OS shell (or, simpler on macOS/Linux: a file named `run.command` or `run.desktop`, or any file with the executable bit set and no restricted extension).
2. Victim opens the PR in GitHub Desktop's "Files changed" view (`pull-request-files-changed.tsx`) and right-clicks the file, selecting "Open with Default Program." `isSafeFileExtension` returns `true` (macOS/Linux: unconditionally; Windows: extension not in the 4-item blacklist).
3. `dispatcher` invokes `openFile` → `shell.openExternal('file://' + fullPath)`, causing the OS to launch the file according to its actual type/handler, executing attacker-controlled code.

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

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L176-200)
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
