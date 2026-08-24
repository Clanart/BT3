### Title
Symlinked repository entries bypass `resolveWithin` and allow file disclosure/execution outside the repository via "Reveal in Finder" / "Open with Default Program" - ([File: app/src/lib/app-shell.ts])

### Summary
GitHub Desktop has a hardened, `realpath`-based path-containment check (`resolveWithin`) that is only invoked on **one** of several code paths that turn a repository-relative, git-provided path into a full filesystem path to open. The "URL open" flow (`openRepositoryFromUrl`) uses it, but the much more commonly used context-menu actions — "Reveal in Finder/Explorer", "Open with Default Program", and "Open in External Editor" for both working-directory changes and committed files — build the target path with a bare `Path.join(repository.path, path)` and never verify that the resolved path (after following symlinks) still lives inside the repository. A cloned/fetched repository can therefore ship a tracked symlink whose target points anywhere on disk, and the very first time a user right-clicks that file, Desktop will follow the symlink and read/open/reveal the real target outside the repo.

### Finding Description
`resolveWithin` in `app/src/lib/path.ts` is explicitly designed to defend against exactly this class of issue: it resolves the path and then calls `fs.promises.realpath` on both the root and the resolved path, rejecting anything whose canonical (symlink-following) location escapes the root: [1](#0-0) 

That guard is used for the deep-link `filepath` parameter in `dispatcher.ts`: [2](#0-1) 

However, `revealInFileManager` — the function backing every "Reveal in Finder/Explorer" menu item across the Changes list, History/commit file list, and Pull Request file list — performs a plain join with no symlink/containment check at all: [3](#0-2) 

The same unguarded pattern is repeated for "Open with Default Program" and "Open in External Editor" in the Changes sidebar, History view, and PR files view: [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) 

The only check applied before enabling "Open with Default Program" is `isSafeFileExtension(extension)`, which inspects the extension of the *symlink's own name* (e.g. `notes.txt`) — not the type of the real target the symlink points to: [8](#0-7) 

Because `file.path` values (`WorkingDirectoryFileChange.path`, `CommittedFileChange.path`) come directly from `git status`/`git show` output of a cloned/fetched repository, an attacker fully controls the tracked entry name and, since it can be a symlink, controls where `Path.join(repository.path, path)` — after OS-level symlink resolution by `shell.showItemInFolder` / `shell.openExternal` / `electronShell.openPath` — actually ends up pointing. There is no repository-wide symlink sanitization elsewhere in the codebase (a grep for `symlink` handling in `app/src/**` shows no logic dealing with tracked/checked-out symlink targets, only CLI-install symlinks), confirming this path is not covered by any other guard.

### Impact Explanation
A malicious or compromised repository can ship a tracked symlink (e.g. `README.txt -> /Users/victim/.ssh/id_ed25519` or `notes.txt -> C:\Users\victim\AppData\Roaming\...\some-secret-file`). Once the victim clones/fetches this repo in Desktop and simply right-clicks that entry in the Changes, History, or Pull Request file list and selects "Reveal in Finder/Explorer" (enabled by default, no extension restriction) or "Open with Default Program" (only blocked by an extension check on the symlink name, trivially bypassed by naming it `secret.txt`), Desktop will resolve/open the real target file outside the repository. This constitutes disclosure of arbitrary files reachable by the Desktop process's OS user (credentials, SSH keys, config files) and, depending on the resolved file type and OS shell association, could result in execution of an attacker-chosen program outside the repository sandbox — satisfying the "file read outside the repo" / potential code-execution impact criteria.

### Likelihood Explanation
Exploitation requires only the normal, expected user action of cloning a repository and interacting with its file list via a single context-menu click — no special privileges, no local/prior compromise, and no unnatural steps. Tracked symlinks are a standard git feature and Desktop already checks out symlinks as part of normal operation; git itself does not prevent an out-of-tree symlink target. Given the project already invested in an explicit `realpath`-based containment primitive (`resolveWithin`) for one narrow flow, the fact that the far more frequently used context-menu file actions omit it substantially increases the practical likelihood of this being reachable and previously unnoticed.

### Recommendation
Route all repository-relative-path-to-filesystem-path conversions used for "Reveal in Finder/Explorer", "Open with Default Program", and "Open in External Editor" through `resolveWithin` (or an equivalent symlink-aware containment check) before calling `shell.showItemInFolder`, `shell.openExternal`, or `electronShell.openPath`. Reject (with a user-visible error, mirroring the `openRepositoryFromUrl` behavior) any path whose canonical location falls outside the repository root, and consider flagging tracked symlinks pointing outside the working directory during status computation so the UI can warn or disable these actions for such entries.

### Proof of Concept
1. Attacker creates a repository containing a tracked symlink, e.g. on a POSIX system:
   `ln -s ~/.ssh/id_ed25519 secret.txt && git add secret.txt && git commit -m "add notes"`
2. Victim clones this repository with GitHub Desktop.
3. In the Changes/History list, victim right-clicks `secret.txt` and selects "Reveal in Finder" (or "Open with Default Program", since `.txt` passes `isSafeFileExtension`).
4. `revealInFileManager(repository, 'secret.txt')` computes `Path.join(repository.path, 'secret.txt')` and calls `shell.showItemInFolder`, which follows the symlink and reveals/opens `~/.ssh/id_ed25519` — a file entirely outside the cloned repository — with no containment check comparable to `resolveWithin` ever being applied.

### Citations

**File:** app/src/lib/path.ts (L64-71)
```typescript
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

**File:** app/src/ui/history/selected-commits.tsx (L383-420)
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

**File:** app/src/ui/changes/filter-changes-list.tsx (L657-664)
```typescript
  private getDefaultContextMenu(
    file: WorkingDirectoryFileChange
  ): ReadonlyArray<IMenuItem> {
    const { id, path, status } = file

    const extension = Path.extname(path)
    const isSafeExtension = isSafeFileExtension(extension)

```
