## Title
Path traversal in "Reveal in File Manager" / "Open with default program" for PR files and commit history — attacker-controlled `file.path` escapes repository root — (File: `app/src/lib/app-shell.ts`)

### Summary
`revealInFileManager()` builds the file-manager target path with a plain `Path.join(repository.path, path)` and no containment check, unlike the equivalent deep-link handler (`openRepositoryFromUrl` in `app/src/ui/dispatcher/dispatcher.ts`) which explicitly guards against traversal via `resolveWithin()`. The `path` value passed into `revealInFileManager` and the sibling `Path.join(repository.path, path)` calls in `pull-request-files-changed.tsx` originates from `CommittedFileChange.path`, i.e., a file path taken from a diff/commit belonging to a pull request (attacker-controlled fork) or a fetched/cloned repository.

### Finding Description
`revealInFileManager` is defined as: [1](#0-0) 

It performs `Path.join(repository.path, path)` with no call to `resolveWithin`/`resolveWithinPosix`/`resolveWithinWin32` (the project's own sanctioned traversal guard, defined in `app/src/lib/path.ts`) and no rejection of absolute paths or `..` segments, unlike the analogous, already-hardened flow for URL-triggered file opens: [2](#0-1) 

The `path` argument reaching `revealInFileManager` comes from `CommittedFileChange.path` for a pull request's changed files, which is exactly the kind of value the codebase treats as untrusted elsewhere (`resolveWithin` guards it for the deep-link `filepath` case). In `pull-request-files-changed.tsx`, the context menu builds both the `revealInFileManager` action and an `Open with default program` action from the same `file.path`, both using unguarded `Path.join`: [3](#0-2) [4](#0-3) 

The same unguarded pattern (`revealInFileManager`, `Path.join(repository.path, path)`) recurs in `app/src/ui/history/selected-commits.tsx`, `app/src/ui/lib/conflicts/unmerged-file.tsx`, `app/src/ui/changes/filter-changes-list.tsx`, and `app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx`.

`CommittedFileChange.path` for pull requests is sourced from the PR diff data (an attacker-controlled fork's commit tree), while Git itself normally rejects `..` path components inside a tree object during checkout — but the *displayed diff path* used purely for UI/file-manager actions is not necessarily re-validated against the same rule the same way `resolveWithin` enforces it for the deep-link case. Because the code path that shows/reveals a file for a PR bypasses the `resolveWithin` containment guard entirely, a crafted diff entry containing traversal segments (e.g. `../../../../Library/LaunchAgents/evil.plist` or a Windows UNC/absolute-looking segment) that survives into `CommittedFileChange.path` would cause `revealInFileManager`/`openFile` to target a path outside the repository, revealing or opening arbitrary attacker-chosen files/locations on the victim's machine when the victim right-clicks "Reveal in File Manager" or "Open with default program" on a file from a malicious PR/commit.

### Impact Explanation
If reachable, this breaks the invariant "actions on repository-diff file entries stay confined to the repository working directory," analogous to the missing-access-control class in the seed report (a state-changing/file-system operation lacking the guard its sibling operations already apply). Consequences: revealing (and via "Open with default program", opening/executing) a file outside the intended repository tree chosen by whoever authored the PR/commit — potential disclosure of file existence/location, or executing an unintended file if a malicious extension/association exists.

### Likelihood Explanation
Exploitability depends on whether Git/the diff-parsing layer that produces `CommittedFileChange.path` for PR/commit views ever allows a traversal-bearing path to survive (Git's tree/index format and dugite's diff parsing generally reject `..`/absolute paths as tree entries, which is why this could not be fully confirmed as reachable from local code alone). This is why it is flagged as **medium-confidence**: the missing `resolveWithin` guard is a real, concrete inconsistency versus the hardened `filepath` deep-link handler, but I could not verify within the indexed code whether the diff/tree parser upstream already strips or rejects such entries before they reach `CommittedFileChange.path`.

### Recommendation
Apply the same `resolveWithin` (or `resolveWithinPosix`/`resolveWithinWin32`) containment check used in `dispatcher.ts`'s `openRepositoryFromUrl` to `revealInFileManager` in `app/src/lib/app-shell.ts`, and to every `Path.join(repository.path, file.path)` call feeding `openFile`/`shell.openExternal`/`Path.extname`-based menu actions (`pull-request-files-changed.tsx`, `selected-commits.tsx`, `unmerged-file.tsx`, `filter-changes-list.tsx`, `copilot-conflicts-dialog.tsx`), rejecting the action if the resolved path falls outside `repository.path`.

### Proof of Concept
Not independently verified end-to-end (would require confirming that dugite/Git diff parsing lets a `..`-containing path through as a `CommittedFileChange.path`). Conceptually:
1. Attacker opens a PR (or the victim fetches/clones a malicious remote) whose diff introduces a file entry with a crafted relative path (e.g. containing `..` segments) that is surfaced as `CommittedFileChange.path`.
2. Victim opens "Files changed" for that PR/commit in Desktop and right-clicks the file, choosing "Reveal in File Manager" or "Open with default program".
3. `revealInFileManager`/`onOpenFile` calls `Path.join(repository.path, file.path)` with no `resolveWithin` check [5](#0-4) [6](#0-5) , opening/revealing a location outside the repository chosen by the diff author.

Given the unverified reachability of a traversal-bearing `CommittedFileChange.path` through Git's own diff/tree validation, I'm flagging this with reduced confidence rather than as a fully proven exploit chain.

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

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L160-199)
```typescript
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
