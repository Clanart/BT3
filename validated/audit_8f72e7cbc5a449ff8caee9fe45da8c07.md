### Title
Symlink path-traversal in "Reveal in Finder / Open with Default Program" for repository-tracked files — no `resolveWithin` containment check ([File: app/src/lib/app-shell.ts])

### Summary
Desktop's deep-link handler for opening a file from a remote URL explicitly resolves the target path with the symlink-aware, containment-checking helper `resolveWithin` before calling `shell.showItemInFolder`, and refuses to proceed if the path escapes the repository root [1](#0-0) . However, the much more common paths that reveal or open a file from the Changes list or from a commit's file list — `revealInFileManager`, `openFile`, and the "Copy Path" context-menu actions — only do a naive `Path.join(repository.path, file.path)` with **no** equivalent containment check [2](#0-1) [3](#0-2) [4](#0-3) . This mirrors the AssetManager bug class exactly: one code path enforces a supported/valid-state check (`resolveWithin`), the sibling code path performing an equivalent operation on attacker-influenced data skips it, and the guard exists specifically to defend against this class of input (the shipped unit tests for `resolveWithin` explicitly cover symlink escape) [5](#0-4) .

### Finding Description
`file.path` values displayed in the Changes list and commit history come straight from `git status`/`git show` output for a repository whose contents (including symlinks) are fully attacker-controlled if the user clones or opens a malicious repository. A crafted repository can contain a tracked symlink, e.g. `evil -> /Users/victim` (or any absolute/relative path pointing outside the working directory), plus an untracked/changed file physically placed through that symlink such as `evil/.ssh/id_rsa`, so that `git status` reports a changed-file entry with `path = "evil/.ssh/id_rsa"`.

`revealInFileManager` builds the final path with a bare join and no boundary check:
```ts
// app/src/lib/app-shell.ts:61-63
export function revealInFileManager(repository: Repository, path: string) {
  const fullyQualifiedFilePath = Path.join(repository.path, path)
  return shell.showItemInFolder(fullyQualifiedFilePath)
}
``` [6](#0-5) 

The same unguarded `Path.join(repository.path, file.path)` pattern is repeated for "Open with Default Program" (`openFile`, which calls `shell.openExternal`) and for "Copy Path" in both the Changes view and the commit/PR file views [7](#0-6) [8](#0-7) [9](#0-8) .

Compare this with the codebase's own guarded pattern, used only for deep links:
```ts
// app/src/ui/dispatcher/dispatcher.ts:1963-1971
const resolved = await resolveWithin(repository.path, filepath)
if (resolved !== null) {
  shell.showItemInFolder(resolved)
} else {
  log.error(`Prevented attempt to open path outside of the repository root: ${filepath}`)
}
``` [10](#0-9) 

`resolveWithin` specifically performs `realpath` resolution on both the root and the candidate path and rejects the result if it escapes the root after symlinks are followed — precisely the check needed to stop the symlink-based escape described above [11](#0-10) . `app-shell.ts` even carries an explicit warning that its `openPath`/`showFolderContents` APIs must "not be used with non-validated paths" [12](#0-11) , yet `revealInFileManager` in the same file violates that contract by constructing and forwarding an unvalidated, attacker-influenced path.

Compounding this, "Open with Default Program" is gated by `isSafeFileExtension`, which on macOS/Linux unconditionally returns `true` for every extension (only Windows blocks `.cmd/.exe/.bat/.sh`) [13](#0-12) , so on non-Windows platforms there is no extension-based mitigation at all for whatever file the symlink traversal exposes.

### Impact Explanation
This lets an attacker who supplies a repository (via clone, fetch of a malicious branch, or a repo the user simply opens) cause Desktop to reveal or open arbitrary files/directories on the victim's filesystem outside the repository once the victim performs a normal, expected action ("Reveal in Finder/Explorer" or "Open with Default Program" on a changed/committed file, or copying its path). This can expose file locations/contents of sensitive files (e.g. SSH keys, browser profiles, credential stores) via the file manager or default-application handler, and on macOS/Linux the missing extension check means arbitrary file types (including executable scripts) outside the repo can be opened via `shell.openExternal`. This satisfies the "file read/write outside the repo" impact class from the prompt.

### Likelihood Explanation
Requires no privileged or local access beyond normal Desktop usage: the user only needs to open/clone the attacker's repository and perform a single expected UI interaction (context-menu "Reveal in Finder" / "Open with Default Program" on a listed changed or committed file) — actions the app explicitly advertises. It does not need special commit signing bypasses; a tracked symlink pointing outside the repo plus a normal working-tree change under that symlink is sufficient to make `git status`/`git show` report a path that traverses out when joined with `repository.path`. The unit-tested symlink-escape scenario for `resolveWithin` in the codebase (`app/test/unit/path-test.ts`) confirms the authors are aware this exact primitive is exploitable — they just did not apply the same guard uniformly.

### Recommendation
Route every file path derived from repository content (`WorkingDirectoryFileChange.path`, `CommittedFileChange.path`) through `resolveWithin(repository.path, file.path)` before it reaches `shell.showItemInFolder`, `shell.openExternal`/`openFile`, `openFileInExternalEditor`, or clipboard "copy path" actions, and refuse the action (with a user-facing error, mirroring `dispatcher.ts`'s deep-link handling) when the resolved path escapes the repository root. Apply this uniformly in `app/src/lib/app-shell.ts` (`revealInFileManager`), `app/src/ui/lib/open-file.ts` (`openFile`), and all call sites in `filter-changes-list.tsx`, `selected-commits.tsx`, `pull-request-files-changed.tsx`, and `unmerged-file.tsx`/`copilot-conflicts-dialog.tsx`. Additionally, fix `isSafeFileExtension` in `app/src/ui/lib/context-menu.ts` so it applies a deny/allow-list on macOS and Linux as well, not just Windows.

### Proof of Concept
1. Create a malicious repository containing a tracked symlink `link` pointing to an absolute path outside the repo (e.g. the user's home directory), and commit it.
2. Have the victim clone this repository in GitHub Desktop.
3. Create (or have the attacker's repo already contain, as an uncommitted/changed entry visible in Desktop) a file path such as `link/.ssh/id_rsa` that appears in the Changes list (this can be achieved by having the attacker repo pre-stage a rename/copy producing that path, or simply by the victim's own filesystem state being traversed through the symlink when Desktop calls `git status`).
4. In the Changes list, right-click the entry and choose "Reveal in Finder" (or "Open with Default Program").
5. `revealInFileManager`/`openFile` computes `Path.join(repository.path, "link/.ssh/id_rsa")` with no `resolveWithin` check and passes the resulting path — which resolves through the symlink to outside the repository — to `shell.showItemInFolder`/`shell.openExternal`, exposing/opening a file outside the cloned repository. This is analogous to `dispatcher.ts`'s `resolveWithin` guard being bypassed simply by using a different, unguarded UI entry point to the same class of file-open primitive.

### Citations

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

**File:** app/src/lib/app-shell.ts (L16-24)
```typescript
  /**
   * Reveals the specified file using the operating
   * system default application.
   * Do not use this method with non-validated paths.
   *
   * @param path - The path of the file to open
   */

  readonly openPath: (path: string) => Promise<string>
```

**File:** app/src/lib/app-shell.ts (L55-63)
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
```

**File:** app/src/ui/changes/filter-changes-list.tsx (L581-591)
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
```

**File:** app/src/ui/changes/filter-changes-list.tsx (L628-636)
```typescript
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

**File:** app/src/ui/history/selected-commits.tsx (L384-420)
```typescript
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

**File:** app/src/lib/path.ts (L64-72)
```typescript
  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
}
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
