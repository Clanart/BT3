### Title
Missing `isSafeFileExtension` guard on "Open with Default Program" for conflicted files allows execution of attacker-supplied executables from a cloned repo - ([File: app/src/ui/lib/conflicts/unmerged-file.tsx])

### Summary
`openFile()` (`app/src/ui/lib/open-file.ts`) is Desktop's `shell.openExternal(file://…)` wrapper for the "Open with Default Program" action. Every other call site that exposes this action to the user first gates it behind `isSafeFileExtension(extension)` so that dangerous extensions cannot be launched directly by Desktop. The merge/rebase/cherry-pick conflict-resolution UI (`app/src/ui/lib/conflicts/unmerged-file.tsx`) wires the same "Open with Default Program" menu item straight to `openFile()` with no extension check at all, breaking the invariant that "Open with Default Program" is only reachable for safe extensions.

### Finding Description
The safe pattern used elsewhere in the codebase always pairs `openFile`/`onOpenItem` with an `isSafeFileExtension` check before enabling the menu item: [1](#0-0) [2](#0-1) 

In both of these, the `OpenWithDefaultProgramLabel` action is `enabled: enabled && isSafeExtension` (or `isSafeExtension && fileExistsOnDisk`), so a file whose extension is not in the vetted safe list can never trigger `openFile`.

`unmerged-file.tsx`, however, builds the "Open with Default Program" menu entry for a merge-conflict marker dropdown without any extension check: [3](#0-2) 

`openFile` itself performs no filtering either — it simply forwards to `shell.openExternal`: [4](#0-3) 

which resolves to Electron's `shell.openExternal`, launching the OS default handler for the file: [5](#0-4) 

An attacker who controls a repository the victim clones, fetches, or merges from (e.g. a branch/PR crafted to conflict on merge) can introduce a conflicted file with a dangerous extension (e.g. `.exe`, `.cmd`, `.scr`, `.msi` on Windows, `.desktop`/`.sh` with an OS-registered handler on Linux/macOS). When the victim opens Desktop's "Resolve conflicts" view for that merge/rebase/cherry-pick and uses the marker-conflict dropdown's "Open with Default Program" entry, Desktop calls `openFile(absoluteFilePath, dispatcher)` unconditionally — bypassing the `isSafeFileExtension` guard that exists precisely to stop this scenario elsewhere in the app.

### Impact Explanation
Where the guard exists (Changes list, PR files-changed view, commit history view), Desktop deliberately refuses to hand risky extensions to the OS shell to prevent inadvertent code execution from untrusted repository content. The conflict-resolution path has no such check, so a maliciously crafted merge conflict can cause the victim's OS to execute or open an attacker-controlled file directly from a cloned/fetched repository, with only a single click through Desktop's own UI (no separate OS “Open anyway” prompt is required, since Desktop — not the OS file explorer — invokes the open). This can lead to arbitrary code execution on the victim's machine, which is the injected/critical-path equivalent of "asset misdirected to an unvalidated recipient" from the original report (unchecked transfer to a non-validating destination causing an irreversible/dangerous outcome).

### Likelihood Explanation
Reaching this requires an attacker-controlled remote/branch that produces a text merge conflict containing a dangerously-named path, and the victim voluntarily choosing "Open with Default Program" from the conflict dropdown while resolving that specific file — this is a normal step in Desktop's supported "resolve conflicts externally" workflow, not an unnatural user action. No admin rights, local access, or pre-existing malware are required; the only attacker capability needed is control over content in a repository the victim interacts with, which matches the required threat model (attacker controls a cloned/fetched repository).

### Recommendation
Apply the same `isSafeFileExtension(Path.extname(relativeFilePath))` gate used in `filter-changes-list.tsx` and `pull-request-files-changed.tsx` to the "Open with Default Program" menu item in `app/src/ui/lib/conflicts/unmerged-file.tsx` (both `makeMarkerConflictDropdownClickHandler` and any other conflict-resolution menu builder that exposes this action), disabling/removing the item for unsafe extensions exactly as is done for the Changes and Pull Request file lists.

### Proof of Concept
1. Attacker prepares a branch/PR that, when merged/rebased/cherry-picked onto the victim's checked-out branch, produces a text conflict in a file named e.g. `payload.cmd` (or any OS-executable-associated extension) containing conflict markers.
2. Victim fetches/pulls this branch in GitHub Desktop and starts a merge/rebase that conflicts.
3. In the "Resolve conflicts" dialog, the victim opens the marker-conflict dropdown for `payload.cmd` and clicks "Open with Default Program" — reaching `makeMarkerConflictDropdownClickHandler` in `app/src/ui/lib/conflicts/unmerged-file.tsx` at [6](#0-5) .
4. `openFile` calls `shell.openExternal('file://' + absoluteFilePath)` with no extension filtering, so the OS launches `payload.cmd` (or the equivalent dangerous handler) directly, executing attacker-controlled content — whereas the identical action on the Changes list or PR Files Changed view would have been disabled by `isSafeFileExtension`.

### Citations

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

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L196-200)
```typescript
      {
        label: OpenWithDefaultProgramLabel,
        action: () => this.onOpenFile(file.path),
        enabled: isSafeExtension && fileExistsOnDisk,
      },
```

**File:** app/src/ui/lib/conflicts/unmerged-file.tsx (L396-414)
```typescript
/** makes a click handling function for marker conflict actions */
const makeMarkerConflictDropdownClickHandler = (
  relativeFilePath: string,
  repository: Repository,
  dispatcher: Dispatcher,
  status: ConflictsWithMarkers,
  ourBranch: string | undefined,
  theirBranch: string | undefined,
  setIsFileResolutionOptionsMenuOpen: (
    isFileResolutionOptionsMenuOpen: boolean
  ) => void
) => {
  return () => {
    const absoluteFilePath = join(repository.path, relativeFilePath)
    const items: IMenuItem[] = [
      {
        label: OpenWithDefaultProgramLabel,
        action: () => openFile(absoluteFilePath, dispatcher),
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

**File:** app/src/lib/app-shell.ts (L43-52)
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
```
