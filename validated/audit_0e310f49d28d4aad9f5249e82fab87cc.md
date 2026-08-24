### Title
Missing path-containment check in `revealInFileManager`/"Open with Default Program" allows a malicious PR/commit file path to escape the repository root - (File: `app/src/lib/app-shell.ts`)

### Summary
The ReaperFarm bug was a missing access-control/validation check on an attacker-influenced value that let it be used unchecked in a sensitive operation. The Desktop analog is the same shape of bug: `revealInFileManager` builds the filesystem path to open with a raw `Path.join(repository.path, path)` and no containment check, while a sibling code path (`Dispatcher.openRepositoryFromUrl`) that does the exact same kind of thing explicitly validates the path with `resolveWithin` before use. The unguarded function is reachable from file lists whose `path` strings originate from external, less-trusted data (GitHub API pull-request file lists), not just local git status.

### Finding Description
`revealInFileManager` simply joins the untrusted relative `path` onto the repository root and hands the result to the OS shell, with no traversal or symlink check: [1](#0-0) 

Contrast this with the deep-link handler, which treats the same class of value (a relative file path meant to stay inside the repo) as untrusted and explicitly guards it with `resolveWithin` and an `isAbsolute` check before calling the identical `shell.showItemInFolder`: [2](#0-1) 

`resolveWithin` is the app's dedicated containment primitive (it normalizes, rejects null bytes, and — critically — resolves symlinks via `realpath` and verifies the result still starts with the real root) and is used elsewhere for exactly this kind of untrusted-path resolution (e.g. `buildConflictContext`): [3](#0-2) [4](#0-3) 

`revealInFileManager` (and its sibling "Open with Default Program" flow via `onOpenFile`) is wired up directly from the pull-request "Files changed" view context menu, using `file.path` taken straight from a `CommittedFileChange` that is populated from the PR's file list (an attacker-influenced GitHub API object, since the PR author controls what shows up there) with no sanitization before being joined to the repo path: [5](#0-4) [6](#0-5) 

The same unguarded `revealInFileManager` call is reused for local changes, history, conflict resolution, and the Copilot conflicts dialog, meaning every one of those callers inherits the missing containment check: [7](#0-6) 

Because `Path.join`/`Path.resolve` collapse `..` segments arithmetically rather than clamping to a root, a `file.path` value containing `..` components (or a value that resolves through a symlinked working-directory entry) can cause `fullyQualifiedFilePath` to point outside `repository.path` entirely, and existing guards (`pathExists`, `isSafeFileExtension`) do nothing to stop this because they operate on the already-escaped path, not on whether the path is within the repo.

### Impact Explanation
If reachable, this lets a value that a remote party influences (a PR file path or crafted commit file path) be used to open Explorer/Finder, or with "Open with Default Program", to execute/open an arbitrary file already present on the victim's disk outside the cloned repository — a read/execute-outside-repo primitive of the kind explicitly listed as in-scope.

### Likelihood Explanation
This is lower-confidence than a directly demonstrated PoC: normal git tooling rejects tree entries whose names contain `..` path components, and it is not confirmed from the index alone whether the GitHub PR "files changed" API can be made to surface such a component to `CommittedFileChange.path` without Desktop's own diff/name-status parsing rejecting or normalizing it first. This uncertainty could not be fully resolved with the available search results, since the code that constructs `CommittedFileChange` entries from the PR API response was not located in this pass. Given that another call site (`openRepositoryFromUrl`) treats structurally identical values as untrusted and explicitly guards them with `resolveWithin`, while `revealInFileManager` does not, this is a genuine inconsistency in the codebase's own trust model — the question is only how much attacker control actually reaches the unguarded path today.

### Recommendation
Route `revealInFileManager` (and the `onOpenFile`/`openFile` "Open with Default Program" path) through `resolveWithin(repository.path, path)` the same way `openRepositoryFromUrl` does, rejecting (and logging) any path that resolves outside the repository root before calling `shell.showItemInFolder`/`shell.openPath`.

### Proof of Concept
Conceptual PoC (not verified against the PR-file-list parsing code, which was not located):
1. Attacker opens a pull request whose file list contains an entry with path `..\..\..\..\Users\victim\AppData\Roaming\evil.exe` (or an equivalent traversal sequence).
2. Victim opens "Files changed" for that PR in Desktop and right-clicks the file, selecting "Reveal in Finder/Explorer" or "Open with Default Program".
3. `pull-request-files-changed.tsx`'s `onFileContextMenu`/`onOpenFile` builds `fullPath = Path.join(repository.path, file.path)`, which resolves outside the repository, and `revealInFileManager`/`openFile` opens/executes it with no containment check, unlike the equivalent `resolveWithin`-guarded deep-link handler in `dispatcher.ts`.

### Citations

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

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L86-98)
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

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L154-200)
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
      },
```

**File:** app/src/ui/history/selected-commits.tsx (L405-410)
```typescript
    const items: IMenuItem[] = [
      {
        label: RevealInFileManagerLabel,
        action: () => revealInFileManager(repository, file.path),
        enabled: fileExistsOnDisk,
      },
```
