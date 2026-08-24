## Title
Discard Changes resolves attacker-controlled repository paths without the symlink-escape guard used elsewhere in the codebase, enabling file writes/deletes outside the repository — (File: `app/src/lib/stores/git-store.ts`)

### Summary
GitHub Desktop already has a dedicated utility, `resolveWithin` in `app/src/lib/path.ts`, whose explicit purpose is to resolve a repository-relative path safely — rejecting the result if `realpath` shows it escapes the repository root via a symlink or traversal segment. This utility is applied when reading conflicted files for Copilot conflict resolution (`app/src/lib/copilot-conflict-context.ts:390-407`). However, the much older and more consequential "Discard Changes" code path in `GitStore.discardChanges` (`app/src/lib/stores/git-store.ts:1545-1649`) resolves the same kind of attacker-influenced, repository-relative `file.path` values using plain `Path.resolve`/`Path.join`, with no symlink or containment check at all.

### Finding Description
`GitStore.discardChanges` is invoked by the Dispatcher (`app/src/ui/dispatcher/dispatcher.ts:906-913` → `AppStore._discardChanges`, `app/src/lib/stores/app-store.ts:5696-5721`) whenever a user discards changes to one or more `WorkingDirectoryFileChange` entries shown in the Changes list. For each file it does: [1](#0-0) 

```
if (file.status.kind !== AppFileStatusKind.Deleted && !foundSubmodule) {
  if (moveToTrash) {
    await this.shell.moveItemToTrash(
      Path.resolve(this.repository.path, file.path)
    )
    ...
    await rm(Path.join(this.repository.path, file.path))
  } ...
```

`file.path` (and `file.status.oldPath` for renames, also pushed unvalidated into `pathsToCheckout`/`pathsToReset`) originates from parsed `git status` output for the repository — i.e., content that is fully controlled by whoever authored the cloned/fetched repository, since a malicious repo can commit a tracked symlink entry at any path. Because a symlink is a legitimate git blob type (mode `120000`), a hostile repository can commit a symlink whose target points outside the working directory (e.g. into the user's home directory or another sensitive path). When Desktop later builds `Path.resolve(repository.path, file.path)` for that entry, the OS filesystem/shell APIs (`shell.moveItemToTrash`, `fs.rm`) will follow the symlink component when accessing it, so the actual filesystem operation lands on the symlink's target — outside the repository the guard is supposed to protect.

This is precisely the threat model the project already codified in `resolveWithin`: [2](#0-1) 

```
async function _resolveWithin(...) {
  ...
  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)
  return realResolved.startsWith(realRoot) ? resolved : null
}
```

and it is enforced for Copilot file reads: [3](#0-2) 

But `discardChanges`, `discardChangesFromSelection`'s underlying `git apply` target, and the reset/checkout helpers all build paths the same unsafe way, with no equivalent `resolveWithin`/`realpath` check before the destructive filesystem operation runs.

### Impact Explanation
"Discard changes" is one of the most common, completely unprompted actions a user performs in Desktop (single click, "Discard Changes" button in the Changes list or context menu — see `app/src/ui/discard-changes/discard-changes-dialog.tsx`). If the working directory contains a symlinked entry pointing outside the repo (planted by cloning/fetching a malicious repository, or checking out an attacker-supplied branch/PR), discarding "changes" to that entry can move an arbitrary file/directory outside the repository into the OS trash (data loss) or, in the `moveToTrash === false` / fallback `rm` path, delete it outright — satisfying the "file write or read outside the repo" impact class from a fully unprivileged attacker-controlled repository, without any unnatural steps beyond normal repository browsing and a click on "Discard changes."

### Likelihood Explanation
The attacker only needs the victim to clone/fetch/open a malicious repository and interact with the Changes panel — a completely ordinary workflow that Desktop is built around, no special settings or elevated Desktop options required. The gap is real: the codebase demonstrably contains the correct fix pattern (`resolveWithin`) but does not apply it to `discardChanges`, `discardChangesFromSelection`, `resetPaths`, or `checkoutIndex` call sites in `git-store.ts`, all of which consume repository/status-supplied relative paths.

### Recommendation
Route every repository-relative path derived from git status/diff output (`file.path`, `file.status.oldPath`) through `resolveWithin` (or an equivalent `realpath`-based containment check) before it is used in `shell.moveItemToTrash`, `fs.rm`, `resetPaths`, or `checkoutIndex` inside `GitStore.discardChanges` and `discardChangesFromSelection`. If the resolved path escapes the repository root, refuse the operation and surface an error instead of performing the filesystem action.

### Proof of Concept
1. Attacker publishes a repository containing a tracked symlink `evil-link` (mode `120000`) targeting an absolute path outside the repo, e.g. the victim's home directory subpath.
2. Victim clones/fetches and opens the repository in GitHub Desktop; the symlink is checked out as-is (default `core.symlinks=true`).
3. Attacker's repo/branch is crafted so that `git status` reports `evil-link` as a changed/untracked entry in the Changes list (e.g. via a subsequent commit that changes the symlink target/content, or an untracked file placed at that path through repository setup instructions the attacker convinces the victim to run, e.g. via a documented "postClone" script — no admin rights needed, just repo content).
4. Victim clicks "Discard Changes" on `evil-link` in the Changes panel.
5. `GitStore.discardChanges` calls `Path.resolve(repository.path, 'evil-link')`, which the OS resolves through the symlink to the external target when `shell.moveItemToTrash`/`fs.rm` executes, moving/deleting the external target instead of anything inside the repository. [4](#0-3)

### Citations

**File:** app/src/lib/stores/git-store.ts (L1545-1583)
```typescript
  public async discardChanges(
    files: ReadonlyArray<WorkingDirectoryFileChange>,
    moveToTrash: boolean = true,
    askForConfirmationOnDiscardChangesPermanently: boolean = false
  ): Promise<void> {
    const pathsToCheckout = new Array<string>()
    const pathsToReset = new Array<string>()

    const submodules = await listSubmodules(this.repository)

    for (const file of files) {
      const foundSubmodule = submodules.some(s => s.path === file.path)

      if (file.status.kind !== AppFileStatusKind.Deleted && !foundSubmodule) {
        if (moveToTrash) {
          try {
            await this.shell.moveItemToTrash(
              Path.resolve(this.repository.path, file.path)
            )
          } catch (e) {
            if (askForConfirmationOnDiscardChangesPermanently) {
              throw new DiscardChangesError(e, this.repository, files)
            }

            // The user has received the confirmation dialog in past and has
            // chosen to always discard the changes permanently if trash failes.
            // We need to remove the file manually.
            if (file.status.kind === AppFileStatusKind.Untracked) {
              await rm(Path.join(this.repository.path, file.path))
            }
          }
        } else if (moveToTrash === false) {
          // The user has received the confirmation dialog and has chosen to
          // discard the changes permanently. We need to remove the file
          // manually.
          if (file.status.kind === AppFileStatusKind.Untracked) {
            await rm(Path.join(this.repository.path, file.path))
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
