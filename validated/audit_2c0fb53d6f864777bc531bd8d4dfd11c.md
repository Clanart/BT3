## Finding

The codebase already hardens several path-handling entry points against directory-traversal / symlink escape using `resolveWithin()`, which resolves both the root and target through `realpath` and rejects anything that resolves outside the root [1](#0-0) . This guard is explicitly applied to the deep-link `filepath` action (`x-github-client://openrepo/...&filepath=...`, a link the user clicks) [2](#0-1)  and to the Copilot conflict-resolution file reader, which comments explicitly that it's guarding "against path traversal and symlink escapes (cross-platform)" [3](#0-2) .

However, the exact same class of repository-relative path — a `path` string taken from `WorkingDirectoryFileChange` / `CommittedFileChange` (derived from `git status`/`git diff`, i.e. from the contents of a cloned/fetched, attacker-influenced working tree) — is joined onto `repository.path` with plain `Path.join`, with **no** `resolveWithin`/`realpath` containment check, in numerous other consumers that then hand the resulting path to the OS shell:

- `revealInFileManager()` – `Path.join(repository.path, path)` → `shell.showItemInFolder(...)` [4](#0-3) 
- `onOpenItem()` in the Changes sidebar – `Path.join(...)` → `openFile()` → `shell.openExternal('file://'+fullPath)` [5](#0-4) 
- `open-file.ts`'s `openFile()` itself performs no path validation before calling `shell.openExternal` [6](#0-5) 
- Commit-history context menu (`selected-commits.tsx`), Pull Request files-changed view, and conflict-resolution menus all build `fullPath`/`absolutePath` via raw `Path.join(repository.path, file.path)` and pass it to `revealInFileManager`, `openFile`, or an external editor launcher [7](#0-6) [8](#0-7) [9](#0-8) 

The project's own test suite demonstrates the exact threat model this omission exposes: `resolveWithin` is tested against a working-tree symlink that points two levels above the root, showing the maintainers are aware that a symlink physically placed inside a repository's working directory can be used to make a nominally "relative" path resolve outside the repository root [10](#0-9) . A cloned or fetched repository fully controls the symlinks placed in its working tree, so a malicious repo can commit a symlink (e.g. `notes -> ../../../../../..`) that git status/diff will surface as an ordinary changed/untracked path entry. When the victim right-clicks that entry and chooses "Reveal in Finder/Explorer" or "Open with Default Program," the OS resolves the symlink component and the action is carried out on a location outside the repository, without ever going through the `resolveWithin` check that protects the deep-link and Copilot-conflict code paths.

### Title
Repository-controlled symlinks bypass the `resolveWithin` containment check for "Reveal in File Manager" / "Open with Default Program" - (File: `app/src/lib/app-shell.ts`, `app/src/ui/lib/open-file.ts`)

### Summary
`resolveWithin()` is the invariant Desktop uses to guarantee a repository-relative path cannot resolve outside the repository root, including through symlinks, and it is enforced for the deep-link `filepath` action and the Copilot conflict-file reader. The same invariant is not enforced for `revealInFileManager`, `openFile`, and the many UI call sites (Changes list, commit history, PR files-changed view, conflict-resolution menus) that build an absolute path from a `WorkingDirectoryFileChange`/`CommittedFileChange` path with plain `Path.join` before invoking `shell.showItemInFolder`/`shell.openExternal`.

### Finding Description
`resolveWithin` normalizes and `realpath`s both the root and the candidate path and rejects results that don't start with the real root [1](#0-0) . It is deliberately used where the path segment originates from data an attacker can influence remotely: the deep-link `filepath` query parameter [2](#0-1)  and conflicted-file paths reported by git during a merge/rebase/cherry-pick [3](#0-2) .

The file paths surfaced by `git status`/`git diff` for a cloned or fetched repository are exactly as attacker-influenced (the repository's working tree, including any symlinks committed to it, is fully controlled by whoever authored/pushed the repo). Yet `revealInFileManager` performs only `Path.join(repository.path, path)` with no realpath/containment check before calling Electron's `shell.showItemInFolder` [4](#0-3) , and `openFile` performs no validation at all before calling `shell.openExternal('file://'+fullPath)` [6](#0-5) . Both are reachable from many context menus that pass `repository.path`-joined, git-derived paths straight through [5](#0-4) [7](#0-6) [8](#0-7) [9](#0-8) .

Because a symlink placed anywhere in the working tree can point outside the repository, `Path.join(repository.path, path)` for a path that traverses through such a symlink is textually inside the repo, but the OS-level shell calls (`shell.showItemInFolder`, `shell.openExternal`) will follow the symlink and act on the real, out-of-repo target — precisely the scenario `resolveWithin`'s own test suite exists to catch [10](#0-9) .

### Impact Explanation
This lets a malicious/compromised repository, once cloned or fetched, cause routine one-click UI actions ("Reveal in File Manager," "Open with Default Program," "Open in External Editor" from the changes list, commit history, or PR review views) to operate on a file/folder outside the repository, disclosing its location or opening it with the OS default handler. This satisfies "the attacker controls a cloned/fetched repository ... and the result is ... file read outside the repo."

### Likelihood Explanation
Requires only that the victim clone/fetch an attacker-authored repository containing a symlink and subsequently interact with a routine, everyday context-menu action ("Reveal in Finder," "Open with Default Program") on the corresponding entry in the Changes/History/PR views — no unusual steps or special privileges are needed, matching how a normal Desktop user already inspects new or unfamiliar repositories.

### Recommendation
Route every repository-relative path used for these shell-facing actions (`revealInFileManager`, `openFile`, and their callers across `filter-changes-list.tsx`, `selected-commits.tsx`, `pull-request-files-changed.tsx`, `unmerged-file.tsx`, `copilot-conflicts-dialog.tsx`) through `resolveWithin(repository.path, path)` before invoking `shell.showItemInFolder`/`shell.openExternal`, mirroring the guard already applied in `dispatcher.ts`'s `openRepositoryFromUrl` and `copilot-conflict-context.ts`. Reject or refuse the action (as those two call sites already do) when the resolved path escapes the repository root.

### Proof of Concept
1. Attacker creates a repository containing a symlink, e.g. `git symlink escape -> ../../../../../../` (or an OS-appropriate deep traversal), committed to the tree, plus an additional file placed such that git reports a changed/untracked path like `escape/some-file`.
2. Victim clones/fetches this repository in GitHub Desktop.
3. In the Changes list (or History/PR-files view), the victim right-clicks the `escape/some-file` entry and selects "Reveal in File Manager" or "Open with Default Program."
4. `revealInFileManager`/`openFile` compute `Path.join(repository.path, 'escape/some-file')` with no containment check, and the OS resolves the `escape` symlink, causing the Finder/Explorer window or default-app open to operate on a location outside the cloned repository (e.g. the user's home directory tree), rather than being rejected the way the equivalent deep-link `filepath` action or Copilot conflict-file read would be.

### Citations

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

**File:** app/src/ui/history/selected-commits.tsx (L382-420)
```typescript
    } = this.props

    const fullPath = Path.join(repository.path, file.path)
    const fileExistsOnDisk = await pathExists(fullPath)
    if (!fileExistsOnDisk) {
      showContextualMenu([
        {
          label: __DARWIN__
            ? 'File Does Not Exist on Disk'
            : 'File does not exist on disk',
          enabled: false,
        },
      ])
      return
    }

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

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L154-199)
```typescript
  private onFileContextMenu = async (
    file: CommittedFileChange,
    event: React.MouseEvent<HTMLDivElement>
  ) => {
    event.preventDefault()

    const { repository } = this.props

    const fullPath = Path.join(repository.path, file.path)
    const fileExistsOnDisk = await pathExists(fullPath)
    if (!fileExistsOnDisk) {
      showContextualMenu([
        {
          label: __DARWIN__
            ? 'File Does Not Exist on Disk'
            : 'File does not exist on disk',
          enabled: false,
        },
      ])
      return
    }

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
