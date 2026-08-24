## #Vulnerability found for this question.

### Title
Incomplete Windows-executable blacklist in `isSafeFileExtension` lets "Open with Default Program" execute untrusted repo payloads (`.scr`, `.com`, `.js`, `.vbs`, `.ps1`, `.msi`, `.lnk`, `.pif`, etc.) - (File: `app/src/ui/lib/context-menu.ts`)

### Summary
The `enabled` state of the "Open with Default Program" context-menu action in the Changes list, History/commit view, and Pull Request "Files changed" view is gated solely on `isSafeFileExtension(extension)`. That function only blacklists four extensions on Windows (`.cmd`, `.exe`, `.bat`, `.sh`) and treats every other extension — including well-known Windows auto-executing types — as "safe", enabling the menu item.

### Finding Description
`isSafeFileExtension` in `app/src/ui/lib/context-menu.ts` is defined as: [1](#0-0) 

```
const RestrictedFileExtensions = ['.cmd', '.exe', '.bat', '.sh']
...
export function isSafeFileExtension(extension: string): boolean {
  if (__WIN32__) {
    return RestrictedFileExtensions.indexOf(extension.toLowerCase()) === -1
  }
  return true
}
```

This list omits numerous extensions that Windows Explorer/`shell.openPath` will natively execute or auto-run through a scripting host without any additional prompt beyond the OS's own SmartScreen/Mark-of-the-Web behavior, e.g. `.scr`, `.com`, `.pif`, `.lnk` (executed directly by the shell), and `.js`, `.vbs`, `.wsf` (run via `wscript.exe`/`cscript.exe`), as well as `.msi` (silently invokes the Windows Installer) and `.ps1` (in many environments PowerShell scripts are directly executable depending on file association/policy).

This `isSafeFileExtension` check is the *only* gate used before wiring up the "Open with Default Program" menu item's `enabled` flag in three call sites that operate on attacker-controlled, repository-provided file paths:

- `app/src/ui/changes/filter-changes-list.tsx` (working directory changes list), lines 662–663 and 796–799 / 810–811 and 830–833.
- `app/src/ui/history/selected-commits.tsx` (History tab / committed files), lines 398–420.
- `app/src/ui/open-pull-request/pull-request-files-changed.tsx` (PR files-changed view), lines 178–199. [2](#0-1) [3](#0-2) [4](#0-3) 

When the user clicks the (enabled) menu item, the code paths funnel into `openFile()`: [5](#0-4) 

```
export async function openFile(fullPath: string, dispatcher: Dispatcher): Promise<void> {
  const result = await shell.openExternal(`file://${fullPath}`)
  ...
}
```

`shell.openExternal`/`shell.openPath` ultimately delegates to Electron's `shell.openPath`, which hands the file to the OS's default handler for that extension — the exact behavior described in the question (script/executable runs on double-click/"Open with Default Program").

Since a cloned or fetched repository's working-directory (or historical commit / PR diff) content is fully attacker-controlled, an attacker can commit a file named e.g. `payload.js`, `payload.scr`, `payload.com`, `payload.lnk`, or `payload.msi`. None of these extensions are in `RestrictedFileExtensions`, so `isSafeFileExtension` returns `true`, the "Open with Default Program" menu item is `enabled`, and choosing it invokes the OS default handler for that file — resulting in script/executable execution.

### Impact Explanation
This allows arbitrary code execution on the victim's machine originating purely from cloning/fetching an attacker-controlled repository and the victim right-clicking a suspicious file in Desktop's Changes list, History view, or PR Files Changed view and choosing "Open with Default Program" (a normal, expected action GitHub Desktop explicitly offers and enables for the file). This matches the "Valid Impact" criteria: attacker controls a cloned/fetched repository, and the result is code execution via a native application feature that is supposed to protect against exactly this class of file.

### Likelihood Explanation
Likelihood is moderate: it requires the victim to explicitly choose "Open with Default Program" on a file from an untrusted repo (a single right-click + one menu click — not an "unnatural" multi-step exploit chain), and GitHub Desktop's own extension blacklist gives users false confidence that dangerous file types are blocked (only 4 of many dangerous Windows extensions are actually blocked). Files named to look benign (e.g. `invoice.js`, `resume.pdf.lnk` if the UI truncates trailing extension display) increase the chance a user clicks through.

### Recommendation
Expand `RestrictedFileExtensions` in `app/src/ui/lib/context-menu.ts` to include additional Windows-executable/script extensions such as `.scr`, `.com`, `.pif`, `.lnk`, `.js`, `.jse`, `.vbs`, `.vbe`, `.wsf`, `.wsh`, `.ps1`, `.msi`, `.msc`, `.hta`, `.cpl`, `.reg`, `.jar`, and consider switching from a blacklist to a curated allowlist approach (or delegating to Windows' own list of executable file types, e.g. via `PATHEXT`-style enumeration) so newly-recognized dangerous extensions are not silently permitted by default.

### Proof of Concept
1. Create/clone a repository containing an untracked file `payload.js` (or `payload.scr`) with e.g. `WScript.Shell` popup/exec code.
2. Open the repository in GitHub Desktop on Windows; go to the Changes tab.
3. Right-click `payload.js` → "Open with default program" is enabled (not greyed out) because `isSafeFileExtension('.js')` returns `true`.
4. Click it; `openFile()` → `shell.openExternal('file://...payload.js')` → Windows invokes `wscript.exe` to run the script, executing attacker-controlled code.

### Citations

**File:** app/src/ui/lib/context-menu.ts (L1-39)
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
}
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
