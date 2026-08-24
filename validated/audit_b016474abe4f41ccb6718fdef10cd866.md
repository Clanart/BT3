## Finding: Path traversal in history file-list context menu via unsanitized `CommittedFileChange.path`

The reported path is real and reproducible in the code as written. `app/src/ui/history/selected-commits.tsx` builds the fully-qualified file path for its context-menu actions with a plain `Path.join`, not the hardened boundary-check helper (`resolveWithin`) that this same codebase already uses elsewhere for comparable untrusted-path scenarios.

### The sink [1](#0-0) 

`onContextMenu` computes `fullPath = Path.join(repository.path, file.path)` and wires it into `revealInFileManager(repository, file.path)`, `onOpenItem(file.path)` (→ `openFile`), and two `clipboard.writeText` calls — all without ever verifying that the joined result stays inside `repository.path`. `Path.join`/`Path.normalize` collapse `..` segments arithmetically; they do not clamp the result to a root directory, so a `file.path` such as `../../../../.ssh/id_rsa` resolves outside the repository.

`revealInFileManager` itself performs the same unguarded join: [2](#0-1) 

Note that the “Copy File Path”/“Copy Relative File Path” menu entries are wired to `fullPath`/`file.path` unconditionally (no `enabled: fileExistsOnDisk` gate), so even the existence check at the top of `onContextMenu` does not prevent the traversal-computed absolute path from being copied to the clipboard.

### Where the untrusted value originates

`file.path` is a `CommittedFileChange.path`, populated straight from `git log --raw --numstat -z` parsing with no path validation: [3](#0-2) 

`parseRawLogWithNumstat` simply takes whatever string git prints as the tree entry name and puts it into `CommittedFileChange.path`; there is no check for `..` components, absolute paths, or null bytes.

### Same pattern exists elsewhere — but a hardened alternative already exists in this codebase

The identical `Path.join(repository.path, file.path)` pattern (mentioned in the prompt) is duplicated in: [4](#0-3) [5](#0-4) [6](#0-5) 

By contrast, this same codebase already recognized this exact hazard class (repo-relative path from an untrusted/attacker-influenced source being joined onto `repository.path`) and fixed it properly in the deep-link handler, using `resolveWithin` to enforce containment and rejecting absolute paths outright: [7](#0-6) 

and in the Copilot conflict-context builder: [8](#0-7) 

`resolveWithin` (`app/src/lib/path.ts`, lines 36-100) normalizes, rejects null bytes, resolves via `realpath`, and only returns a path if it truly resides under the root — the correct containment check that `selected-commits.tsx` and its sibling files omit.

### Assessment against the review criteria

- **Untrusted-input path traced**: attacker-crafted commit tree entry → `git log --raw` output → `parseRawLogWithNumstat` → `CommittedFileChange.path` → `selected-commits.tsx: onContextMenu` → `Path.join(repository.path, file.path)` → `revealInFileManager`/`openFile`/`clipboard.writeText`.
- **Wrong value**: `fullPath` in `onContextMenu` (and the `revealInFileManager(repository, file.path)` call inside `app-shell.ts`) is computed with unbounded `Path.join` instead of `resolveWithin`.
- **Existing checks**: `pathExists(fullPath)` only gates whether "Reveal"/"Open with default program" are enabled — it does not restrict the path itself, and does not gate the clipboard copy actions at all. There is no boundary/containment check anywhere in this code path, unlike the `resolveWithin`-protected paths elsewhere in the app.

### Caveat / residual uncertainty

Whether a crafted tree entry containing `..` actually survives an ordinary `git fetch`/`clone` and shows up verbatim in `git log --raw` output depends on the installed git client's own object-safety checks (`fsck`, path verification during tree-walking) — that behavior lives in the user's system `git` binary, not in this repository, so I can't confirm from the codebase alone whether git would reject or sanitize such an object before Desktop ever parses it. This is a caveat for the PoC/likelihood assessment, not for the code-level finding: assuming git surfaces such a path (a documented git plumbing hazard class, not unique to this app), Desktop's own containment logic here is insufficient to stop it, whereas equivalent code elsewhere in the same app already defends against it correctly.

### Recommendation

In `app/src/ui/history/selected-commits.tsx` (and the duplicated logic in `filter-changes-list.tsx`, `pull-request-files-changed.tsx`, `unmerged-file.tsx`, `copilot-conflicts-dialog.tsx`), replace the raw `Path.join(repository.path, file.path)` computation with `resolveWithin(repository.path, file.path)`, reject the action (disable menu items, skip clipboard copy) when it returns `null`, and use the resolved path everywhere instead of a hand-built join — mirroring the fix already applied in `dispatcher.ts`'s `openRepositoryFromUrl` and `copilot-conflict-context.ts`.

### Citations

**File:** app/src/ui/history/selected-commits.tsx (L371-429)
```typescript
  private onContextMenu = async (
    file: CommittedFileChange,
    event: React.MouseEvent<HTMLDivElement>
  ) => {
    event.preventDefault()

    const {
      selectedCommits,
      localCommitSHAs,
      repository,
      externalEditorLabel,
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
      { type: 'separator' },
      {
        label: CopyFilePathLabel,
        action: () => clipboard.writeText(fullPath),
      },
      {
        label: CopyRelativeFilePathLabel,
        action: () => clipboard.writeText(Path.normalize(file.path)),
      },
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

**File:** app/src/lib/git/log.ts (L299-316)
```typescript
      const status = forceUnwrap(
        'Invalid log output (status)',
        lineComponents.at(-1)
      )
      const oldPath = /^R|C/.test(status)
        ? forceUnwrap('Missing old path', lines.at(++i))
        : undefined

      const path = forceUnwrap('Missing path', lines.at(++i))

      files.push(
        new CommittedFileChange(
          path,
          mapStatus(status, oldPath, srcMode, dstMode),
          sha,
          parentCommitish
        )
      )
```

**File:** app/src/ui/changes/filter-changes-list.tsx (L581-636)
```typescript
  private getCopyPathMenuItem = (
    file: WorkingDirectoryFileChange
  ): IMenuItem => {
    return {
      label: CopyFilePathLabel,
      action: () => {
        const fullPath = Path.join(this.props.repository.path, file.path)
        clipboard.writeText(fullPath)
      },
    }
  }

  private getCopyRelativePathMenuItem = (
    file: WorkingDirectoryFileChange
  ): IMenuItem => {
    return {
      label: CopyRelativeFilePathLabel,
      action: () => clipboard.writeText(Path.normalize(file.path)),
    }
  }

  private getCopySelectedPathsMenuItem = (
    files: WorkingDirectoryFileChange[]
  ): IMenuItem => {
    return {
      label: CopySelectedPathsLabel,
      action: () => {
        const fullPaths = files.map(file =>
          Path.join(this.props.repository.path, file.path)
        )
        clipboard.writeText(fullPaths.join(EOL))
      },
    }
  }

  private getCopySelectedRelativePathsMenuItem = (
    files: WorkingDirectoryFileChange[]
  ): IMenuItem => {
    return {
      label: CopySelectedRelativePathsLabel,
      action: () => {
        const paths = files.map(file => Path.normalize(file.path))
        clipboard.writeText(paths.join(EOL))
      },
    }
  }

  private getRevealInFileManagerMenuItem = (
    file: WorkingDirectoryFileChange
  ): IMenuItem => {
    return {
      label: RevealInFileManagerLabel,
      action: () => revealInFileManager(this.props.repository, file.path),
      enabled: file.status.kind !== AppFileStatusKind.Deleted,
    }
  }
```

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L154-212)
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
      { type: 'separator' },
      {
        label: CopyFilePathLabel,
        action: () => clipboard.writeText(fullPath),
      },
      {
        label: CopyRelativeFilePathLabel,
        action: () => clipboard.writeText(Path.normalize(file.path)),
      },
      { type: 'separator' },
    ]

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
