## #Valid Vulnerability found for this question.

### Title
Unrestricted "Open with Default Program" allows execution of tracked macOS/Linux executable files (e.g. `.command` scripts) - (File: `app/src/ui/lib/open-file.ts`)

### Summary
The `isSafeFileExtension` guard that gates the "Open with Default Program" context-menu action is only enforced on Windows. On macOS and Linux it unconditionally returns `true`, so any file tracked in or added to a repository — regardless of extension or executable bit — can be opened via `openFile`, which calls `shell.openExternal('file://' + fullPath)`. On macOS, certain extensions (most notably `.command`) are treated by the OS as directly executable when "opened," so this path can be used to launch attacker-controlled code with no additional confirmation.

### Finding Description
`isSafeFileExtension` in [1](#0-0)  only checks against `RestrictedFileExtensions` (`.cmd`, `.exe`, `.bat`, `.sh`) when `__WIN32__` is true; for all other platforms it always returns `true`: [2](#0-1) [1](#0-0) 

This value is used to enable/disable the `OpenWithDefaultProgramLabel` context-menu entry across the Changes list, History/commit file list, and Pull Request file list views, e.g.: [3](#0-2) [4](#0-3) [5](#0-4) 

When invoked, `openFile` performs no additional validation of the file type or executability before calling `shell.openExternal`: [6](#0-5) 

Notably, the codebase elsewhere demonstrates that the developers are aware that opening arbitrary paths on macOS can trigger execution — `showFolderContents` explicitly checks `isApplicationBundle` before opening a directory, specifically to avoid inadvertently launching an `.app` bundle: [7](#0-6) [8](#0-7) 

No equivalent mitigation exists in `openFile`/`isSafeFileExtension` for macOS/Linux. In particular, a `.command` file is treated by macOS Finder/LaunchServices as directly runnable when opened (this is the entire purpose of the `.command` extension), and git preserves the POSIX executable bit on tracked blobs. A repository can therefore ship a tracked `payload.command` file with the executable bit set; after cloning, the file appears in Desktop's Changes/History/PR file lists, "Open with Default Program" is enabled (since `isSafeFileExtension` always returns `true` on macOS/Linux), and clicking it causes `shell.openExternal('file://.../payload.command')` to launch Terminal.app and execute the script contents.

Additionally, because the file arrived via `git clone`/`fetch` rather than a browser download, macOS does not apply the `com.apple.quarantine` extended attribute, so the usual Gatekeeper "unidentified developer" prompt that normally accompanies double-clicking downloaded executables does not appear.

### Impact Explanation
Successful exploitation results in arbitrary code execution in the context of the logged-in user account, triggered by a single click on a "Open with Default Program" menu entry that GitHub Desktop unconditionally offers for any tracked file on macOS/Linux. This satisfies the program's "code execution" impact criterion arising from attacker-controlled repository content.

### Likelihood Explanation
The action requires the victim to right-click a specific file and select "Open with Default Program" — this is a normal, single, expected interaction with the application's own UI (not an unnatural multi-step social-engineering flow), and the menu item is presented as enabled with no warning that the target is executable. An attacker only needs the victim to clone/open a malicious repository and interact with the standard Changes/History context menu, making this a realistic and directly reachable path.

### Recommendation
Extend `isSafeFileExtension` (or add an equivalent guard consumed by `openFile`) to cover platform-appropriate dangerous types on macOS/Linux — at minimum `.command` files, and ideally executables in general (checking the POSIX executable bit and/or using `isApplicationBundle`-style detection for `.app` bundles/other executable content) — before enabling the "Open with Default Program" action or before `openFile` invokes `shell.openExternal`. Consider defaulting to `revealInFileManager` for such files instead of unconditionally opening them, mirroring the mitigation already used in `showFolderContents`.

### Proof of Concept
1. Create a repository containing a file `payload.command` with content:
   ```
   #!/bin/bash
   touch /tmp/pwned
   ```
   and set the executable bit (`chmod +x payload.command`, `git add --chmod=+x payload.command` or commit with mode `100755`).
2. Push and have the victim clone the repository with GitHub Desktop on macOS.
3. In the Changes tab (or History/PR file list) right-click `payload.command`.
4. Observe "Open with Default Program" is enabled (`isSafeFileExtension` returns `true` unconditionally on macOS).
5. Click it; `openFile` calls `shell.openExternal('file://.../payload.command')`; Terminal.app launches and executes the script, creating `/tmp/pwned` — confirming code execution rather than mere file reveal.

### Citations

**File:** app/src/ui/lib/context-menu.ts (L1-1)
```typescript
const RestrictedFileExtensions = ['.cmd', '.exe', '.bat', '.sh']
```

**File:** app/src/ui/lib/context-menu.ts (L34-39)
```typescript
export function isSafeFileExtension(extension: string): boolean {
  if (__WIN32__) {
    return RestrictedFileExtensions.indexOf(extension.toLowerCase()) === -1
  }
  return true
}
```

**File:** app/src/ui/changes/filter-changes-list.tsx (L790-799)
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

**File:** app/src/lib/is-application-bundle.ts (L1-18)
```typescript
import { execFile } from './exec-file'

/**
 * Attempts to determine if the provided path is an application bundle or not.
 *
 * macOS differs from the other platforms we support in that a directory can
 * also be an application and therefore executable making it unsafe to open
 * directories on macOS as we could conceivably end up launching an application.
 *
 * This application uses file metadata (the `mdls` tool to be exact) to
 * determine whether a path is actually an application bundle or otherwise
 * executable.
 *
 * NOTE: This method will always return false when not running on macOS.
 */
export async function isApplicationBundle(path: string): Promise<boolean> {
  if (process.platform !== 'darwin') {
    return false
```

**File:** app/src/ui/main-process-proxy.ts (L123-143)
```typescript
  // On macOS a directory might also be an app bundle and if it is
  // and we attempt to open it we're gonna execute that app which
  // it far from ideal so we'll look up the metadata for the path
  // and attempt to determine whether it's an app bundle or not.
  //
  // If we fail loading the metadata we'll assume it's an app bundle
  // out of an abundance of caution.
  const isBundle = await isApplicationBundle(path).catch(err => {
    log.error(`Failed to load metadata for path '${path}'`, err)
    return true
  })

  if (isBundle) {
    log.info(
      `Preventing direct open of path '${path}' as it appears to be an application bundle`
    )

    await _showItemInFolder(path)
  } else {
    UNSAFE_openDirectory(path)
  }
```
