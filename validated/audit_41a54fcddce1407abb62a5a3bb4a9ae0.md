### Title
Symlinked working-directory entries from an untrusted clone bypass path-containment checks and let "Open"/"Reveal"/editor actions escape the repository - (File: app/src/lib/app-shell.ts, app/src/ui/lib/open-file.ts)

### Summary
The Otter Audits report's core invariant break is: *one code path enforces that a user-supplied handle must resolve to a specific, safe location, while a sibling code path that consumes conceptually the same data does not enforce it, so the unconstrained data becomes unusable/dangerous once it reaches the unguarded path.* The direct GitHub Desktop analog is path containment for files reported by `git status`/`git log`. Desktop added an explicit “guard against path traversal and symlink escapes” helper, `resolveWithin`, and uses it in exactly one place — `buildConflictContext` — before reading conflict file contents. [1](#0-0) [2](#0-1) 

Every other consumer of the same `WorkingDirectoryFileChange.path` / `CommittedFileChange.path` values (which originate from `git status --porcelain=2` and are attacker-influenced by the committed tree of a cloned/fetched repository) instead does a naive `Path.join(repository.path, file.path)` with no `resolveWithin`/`realpath` check, then hands the result to Electron's shell APIs. [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8) 

### Finding Description
`git status`/`git log` paths are derived from the working tree, which is populated by checking out whatever tree entries exist in the cloned/fetched repository — including symlink entries (git file mode `120000`). A malicious repository can commit a symlink, e.g. `report.pdf -> /Users/victim/.ssh/id_rsa` (or, on Windows, a symlink pointing at an arbitrary UNC/absolute path). After clone/fetch/checkout, this symlink is a literal filesystem symlink under `repository.path`.

When Desktop reports this path as changed/untracked/committed, the UI's context menu handlers build a "full path" with plain `Path.join(repository.path, path)` and pass it straight to:
- `openFile()` → `shell.openExternal('file://' + fullPath)` (Open with Default Program) [10](#0-9) 
- `revealInFileManager()` → `shell.showItemInFolder(fullyQualifiedFilePath)` [11](#0-10) 
- `dispatcher.openInExternalEditor(fullPath)` / `openFileInExternalEditor(absolutePath)` — launches the user's configured external editor on the resolved path [12](#0-11) [13](#0-12) 

None of these call sites resolve the symlink and verify the *real* target stays inside `repository.path`, unlike `buildConflictContext`, which explicitly does this via `resolveWithin` before reading file bytes: [1](#0-0) 

`resolveWithin` itself proves the intended guard: it calls `realpath` on both the root and the resolved path and rejects anything whose real path doesn't start with the real root, specifically to defeat symlink traversal — this is validated by dedicated tests. [14](#0-13) [15](#0-14) 

Only `isSafeFileExtension` gates the "Open with Default Program" menu item, and that check operates on the *reported* file extension (e.g. `.pdf`), not on the extension/type of whatever the symlink actually resolves to on disk — so a symlink named `report.pdf` pointing at a `.exe`/`.bat`/`.command` outside the repo passes the "safe extension" gate while the OS opens/executes the real target. `revealInFileManager` and "Open in external editor" have no extension gate at all. [16](#0-15) [17](#0-16) 

This mirrors the report's broken invariant precisely: `resolveWithin`/associated-account-style containment is enforced in the "sensitive" newly-added feature (AI conflict resolution reading raw file bytes) but not in the older, broader set of features (Changes list, History, PR file list, conflict resolution overflow menu) that consume the exact same attacker-controlled path values and perform file-system operations that are just as sensitive (open/execute/reveal outside the repo).

### Impact Explanation
An attacker who controls a cloned/fetched repository can plant a symlink tree entry that, once checked out, redirects Desktop's "Open with Default Program," "Reveal in File Manager," and "Open in External Editor" actions to an arbitrary path on the victim's filesystem. Depending on the target:
- Reading/opening a sensitive file (SSH keys, cloud credential files, browser profile data) via the default handler discloses its contents.
- Pointing the symlink at an executable/script and relying on the victim to click "Open with Default Program" (or an editor plugin that auto-executes) can lead to code execution outside the sandboxed repo directory.
- `revealInFileManager`/`showItemInFolder` at minimum discloses the existence and location of files/directories outside the repo (e.g., pointing a symlink at `..\..\..\Users\<victim>\Desktop`).

This satisfies the "attacker controls a cloned/fetched repository ... result is code execution, file write or read outside the repo" impact class from the task's valid-impact list.

### Likelihood Explanation
Medium. It requires the victim to interact with a file from a menu (double-click "Open," "Reveal," or "Open in external editor") after cloning/fetching the malicious repository and viewing it in the Changes/History/PR panes — a very ordinary Desktop workflow, not an unnatural user step. The main environmental caveat is that symlink creation during checkout depends on `core.symlinks` (default true on macOS/Linux; requires Developer Mode or admin privilege on Windows), so the strongest platform is macOS/Linux. The existence of a dedicated `resolveWithin` symlink-escape guard added specifically for the AI-conflict-context feature indicates the maintainers are already aware of this exact bug class in this codebase but have not applied it consistently everywhere the same tainted path data flows.

### Recommendation
Route every place that turns a repository-relative `file.path`/`oldPath` into a filesystem path for an OS-level action (`openFile`, `revealInFileManager`, `openInExternalEditor`, `showFolderContents`) through `resolveWithin`/`resolveWithinPosix`/`resolveWithinWin32` (or an equivalent real-path containment check), rejecting or warning when the resolved real path escapes `repository.path`, exactly as already done in `buildConflictContext`. Centralize this in `app-shell.ts`'s `revealInFileManager` and `open-file.ts`'s `openFile` so all current and future callers inherit the guard instead of re-implementing `Path.join` themselves.

### Proof of Concept
1. Attacker creates a repository containing a symlink tree entry `payload.txt -> /Users/victim/.ssh/id_rsa` (mode `120000`), commits it, and hosts it for the victim to clone.
2. Victim clones the repo in GitHub Desktop; `core.symlinks=true` causes git to materialize the symlink in the working directory.
3. Victim opens the repository in Desktop, sees `payload.txt` in History/Changes, right-clicks and selects "Open with Default Program" (`onOpenItem` → `openFile(Path.join(repository.path, 'payload.txt'), dispatcher)`). [5](#0-4) 
4. `shell.openExternal('file://' + fullPath)` follows the symlink and opens `id_rsa` in the OS default text viewer, disclosing its contents — no `resolveWithin`/realpath check ever runs on this path, unlike the equivalent check present in `buildConflictContext`. [10](#0-9) [1](#0-0)

### Citations

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

**File:** app/src/lib/path.ts (L36-71)
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
```

**File:** app/src/lib/app-shell.ts (L55-64)
```typescript
/**
 * Reveals a file from a repository in the native file manager.
 *
 * @param repository The currently active repository instance
 * @param path The path of the file relative to the root of the repository
 */
export function revealInFileManager(repository: Repository, path: string) {
  const fullyQualifiedFilePath = Path.join(repository.path, path)
  return shell.showItemInFolder(fullyQualifiedFilePath)
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

**File:** app/src/ui/history/selected-commits.tsx (L405-420)
```typescript
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

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L86-97)
```typescript
  private onOpenFile = (path: string) => {
    const fullPath = Path.join(this.props.repository.path, path)
    this.onOpenBinaryFile(fullPath)
  }

  /**
   * Opens a binary file in an the system-assigned application for
   * said file type.
   */
  private onOpenBinaryFile = (fullPath: string) => {
    openFile(fullPath, this.props.dispatcher)
  }
```

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx (L212-234)
```typescript
  private onOverflowMenuClick = (path: string) => {
    const { repository, dispatcher, resolvedExternalEditor } = this.props
    const absolutePath = join(repository.path, path)

    const items: IMenuItem[] = []

    if (resolvedExternalEditor !== null) {
      items.push({
        label: `Open in ${resolvedExternalEditor}`,
        action: () => this.props.openFileInExternalEditor(absolutePath),
      })
    }

    items.push(
      {
        label: OpenWithDefaultProgramLabel,
        action: () => openFile(absolutePath, dispatcher),
      },
      {
        label: RevealInFileManagerLabel,
        action: () => revealInFileManager(repository, path),
      }
    )
```

**File:** app/src/ui/lib/conflicts/unmerged-file.tsx (L396-418)
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
      {
        label: RevealInFileManagerLabel,
        action: () => revealInFileManager(repository, relativeFilePath),
      },
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

**File:** app/src/lib/stores/app-store.ts (L7605-7626)
```typescript
  /** Open a path to a repository or file using the user's configured editor */
  public async _openInExternalEditor(fullPath: string): Promise<void> {
    const { selectedExternalEditor, useCustomEditor, customEditor } =
      this.getState()

    try {
      if (useCustomEditor && customEditor) {
        await launchCustomExternalEditor(fullPath, customEditor)
      } else {
        const match = await findEditorOrDefault(selectedExternalEditor)
        if (match === null) {
          this.emitError(
            new ExternalEditorError(
              `No suitable editors installed for GitHub Desktop to launch. Install ${suggestedExternalEditor.name} for your platform and restart GitHub Desktop to try again.`,
              { suggestDefaultEditor: true }
            )
          )
          return
        }

        await launchExternalEditor(fullPath, match)
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
