### Title
Symlink Path-Traversal in "Open File" / "Open in External Editor" Actions Bypasses `resolveWithin` Guard - (File: `app/src/ui/changes/sidebar.tsx`, `app/src/ui/history/selected-commits.tsx`, `app/src/ui/app.tsx`)

### Summary
GitHub Desktop already contains a dedicated defense-in-depth helper, `resolveWithin`/`resolveWithinPosix`/`resolveWithinWin32`, that resolves a repo-relative path and verifies (via `realpath`) that it does not escape the repository root, explicitly to stop symlink-based directory traversal [1](#0-0) . This guard is correctly used for the `x-github-desktop://openLocalRepo` deep-link file handler [2](#0-1)  and for the Copilot conflict-resolution file reader [3](#0-2) . However, the "Open with Default Program" and "Open in External Editor" actions in the Changes list, the History/commit file list, and the app-level fallback handler build the same kind of repo-relative path with plain `Path.join()` and never call `resolveWithin`, so a tracked symlink that escapes the repository is followed without any check.

### Finding Description
The unsafe pattern appears in three places that all take a `path` value taken directly from `WorkingDirectoryFileChange.path` (working-tree status) or a committed file's path (history view) and join it onto the repository root:

- `app/src/ui/changes/sidebar.tsx`:
```
private onOpenItem = (path: string) => {
  const fullPath = Path.join(this.props.repository.path, path)
  openFile(fullPath, this.props.dispatcher)
}
``` [4](#0-3) 

- `app/src/ui/history/selected-commits.tsx` (same pattern for files inside an arbitrary, potentially not-yet-checked-out commit): [5](#0-4) 

- `app/src/ui/app.tsx`, the fallback "open in external editor" handler:
```
private onOpenInExternalEditor = (path: string) => {
  ...
  const fullPath = Path.join(repository.path, path)
  this.props.dispatcher.openInExternalEditor(fullPath)
}
``` [6](#0-5) 

`openFile()` hands the joined path straight to Electron's `shell.openExternal('file://' + fullPath)` with no further validation [7](#0-6) .

Git tracks symlinks as regular blobs (mode `120000`) whose content is the link target string. A malicious/attacker-controlled repository can commit a symlink such as `notes.txt -> ../../../../.ssh/id_rsa` (POSIX) or a `.lnk`/junction-style equivalent on Windows. Unlike historical tree-entry-name traversal (`CVE-2014-9390`, where the *name* itself contained `..`), this attack uses a *valid* file name whose *symlink target* points outside the working directory — a technique that `readRefFile`/`resolveWithin`'s own unit tests explicitly acknowledge as a real bypass class ("fails for paths that use a symlink to traverse outside of the root") [8](#0-7) . Git itself does not prevent checking out symlinks whose target lies outside the repo; only the application-level `resolveWithin` guard, which is missing here, prevents the escape.

The `isSafeFileExtension` gate used before enabling "Open with Default Program" only inspects the file's extension string [9](#0-8) ; it does nothing to detect that the file is a symlink or that its target resolves outside the repository, so it provides no protection against this vector.

### Impact Explanation
When the victim clones/opens a repository containing such a symlink and simply double-clicks the file in the Changes list, selects "Open with Default Program", or opens it in their external editor, Desktop resolves the symlink via the OS and hands the *real* out-of-repo file to `shell.openExternal` or to the external editor process. Depending on the target and the OS's file-type association, this can:
- Leak the contents of sensitive files (SSH keys, cloud credential files, `.netrc`, browser profile files) by opening them in a viewer/editor, satisfying "read outside the repo".
- If the symlink targets an executable or script that the OS treats as launchable when opened (e.g., `.cmd`/`.bat`/`.desktop`/`.app` depending on platform file-open semantics), this can escalate to code execution triggered by what looks like an ordinary "open this file" action inside a repository the user is casually browsing.

This matches the impact class called out for this program: attacker controls a cloned/fetched repository, and the result is file read/execution outside the repo directory.

### Likelihood Explanation
Medium. The attacker only needs to get the victim to clone or open the repository and interact with the Changes list or history file list the way any user normally would (double-click / "Open with Default Program" / "Open in external editor") — no unusual or "unnatural" steps are required beyond opening a file that appears to be part of the project, which is standard workflow in Desktop. The same class of bug was already identified and fixed for the deep-link file-opening path (`dispatcher.ts`) and the Copilot conflict reader, showing the maintainers recognize this exact risk, but the fix was not consistently applied to the older Changes/History "open file" code paths.

### Recommendation
Route all repo-relative "open file" resolutions through the existing `resolveWithin` (or `resolveWithinPosix`/`resolveWithinWin32`) helper before calling `openFile`/`shell.openExternal`/`dispatcher.openInExternalEditor`, mirroring the pattern already used in `dispatcher.ts`'s `openRepositoryFromUrl`:
- `app/src/ui/changes/sidebar.tsx` `onOpenItem`
- `app/src/ui/history/selected-commits.tsx` `onOpenItem`
- `app/src/ui/app.tsx` `onOpenInExternalEditor`

If `resolveWithin` returns `null` (path escapes the repo, including via symlink), refuse to open the file and log/alert instead, exactly as done in `dispatcher.ts` [10](#0-9) .

### Proof of Concept
1. Attacker creates a repository containing a committed symlink, e.g. on POSIX:
   `ln -s ../../../../.ssh/id_rsa payload.txt && git add payload.txt && git commit -m "notes"`
2. Victim clones the repository in GitHub Desktop and checks it out; `payload.txt` appears as a normal file (e.g., shown as untracked/changed, or present in history).
3. Victim right-clicks `payload.txt` in the Changes list and selects "Open with Default Program" (routes through `sidebar.tsx`'s `onOpenItem` → `Path.join(repository.path, 'payload.txt')` → `openFile` → `shell.openExternal('file://<repo>/payload.txt')`), or double-clicks it in the History file list (`selected-commits.tsx`).
4. The OS resolves the symlink and opens `~/.ssh/id_rsa` in the victim's default text viewer/editor, disclosing the private key content — with no `resolveWithin` check ever performed, unlike the equivalent, already-hardened deep-link path in `dispatcher.ts`.

### Citations

**File:** app/src/lib/path.ts (L36-72)
```typescript
async function _resolveWithin(
  rootPath: string,
  pathSegments: string[],
  options: {
    join: (...pathSegments: string[]) => string
    normalize: (p: string) => string
    resolve: (...pathSegments: string[]) => string
  } = Path
) {
  // An empty root path would let all relative
  // paths through.
  if (rootPath.length === 0) {
    return null
  }

  const { join, normalize, resolve } = options

  const normalizedRoot = normalize(rootPath)
  const normalizedRelative = normalize(join(...pathSegments))

  // Null bytes has no place in paths.
  if (
    normalizedRoot.indexOf('\0') !== -1 ||
    normalizedRelative.indexOf('\0') !== -1
  ) {
    return null
  }

  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
}
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1957-1972)
```typescript
    if (filepath !== null) {
      if (isAbsolute(filepath)) {
        log.error(`Refusing to open absolute path: ${filepath}`)
        return
      }

      const resolved = await resolveWithin(repository.path, filepath)

      if (resolved !== null) {
        shell.showItemInFolder(resolved)
      } else {
        log.error(
          `Prevented attempt to open path outside of the repository root: ${filepath}`
        )
      }
    }
```

**File:** app/src/lib/copilot-conflict-context.ts (L390-407)
```typescript
      // Guard against path traversal and symlink escapes (cross-platform)
      let absolutePath: string | null
      try {
        absolutePath = await resolveWithin(workingDirectory, file.path)
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File path could not be resolved safely',
        }
      }
      if (absolutePath === null) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File path is outside the repository',
        }
      }
```

**File:** app/src/ui/changes/sidebar.tsx (L277-285)
```typescript
  /**
   * Open file with default application.
   *
   * @param path The path of the file relative to the root of the repository
   */
  private onOpenItem = (path: string) => {
    const fullPath = Path.join(this.props.repository.path, path)
    openFile(fullPath, this.props.dispatcher)
  }
```

**File:** app/src/ui/history/selected-commits.tsx (L287-295)
```typescript
  /**
   * Open file with default application.
   *
   * @param path The path of the file relative to the root of the repository
   */
  private onOpenItem = (path: string) => {
    const fullPath = Path.join(this.props.repository.path, path)
    openFile(fullPath, this.props.dispatcher)
  }
```

**File:** app/src/ui/app.tsx (L3429-3437)
```typescript
  private onOpenInExternalEditor = (path: string) => {
    const repository = this.state.selectedState?.repository
    if (repository === undefined) {
      return
    }

    const fullPath = Path.join(repository.path, path)
    this.props.dispatcher.openInExternalEditor(fullPath)
  }
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

**File:** app/test/unit/path-test.ts (L65-78)
```typescript
    if (!__WIN32__) {
      it('fails for paths that use a symlink to traverse outside of the root', async () => {
        const tempDir = await mkdtemp(join(tmpdir(), 'path-test'))
        const symlinkName = 'dangerzone'
        const symlinkPath = join(tempDir, symlinkName)

        try {
          await symlink(resolve(tempDir, '..', '..'), symlinkPath)
          assert((await resolveWithin(tempDir, symlinkName)) === null)
        } finally {
          await unlink(symlinkPath)
          await rmdir(tempDir)
        }
      })
```

**File:** app/src/ui/changes/filter-changes-list.tsx (L657-663)
```typescript
  private getDefaultContextMenu(
    file: WorkingDirectoryFileChange
  ): ReadonlyArray<IMenuItem> {
    const { id, path, status } = file

    const extension = Path.extname(path)
    const isSafeExtension = isSafeFileExtension(extension)
```
