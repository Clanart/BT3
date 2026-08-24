## Finding



### Title
Symlink Path Escape in `discardChanges` Bypasses `resolveWithin` Guard, Enabling File Deletion/Move Outside Repository - (File: `app/src/lib/stores/git-store.ts`)

### Summary
`GitStore.discardChanges` resolves working-directory file paths reported by `git status` using plain `Path.resolve`/`Path.join` and then passes the result to `shell.moveItemToTrash` or `rm`, without ever calling the codebase's dedicated `resolveWithin` guard (`app/src/lib/path.ts`) that other file-touching code paths (`app-store.ts`, `dispatcher.ts`, `copilot-conflict-context.ts`) use to block symlink/traversal escapes. A cloned or fetched repository that contains a symlinked directory pointing outside the working tree can cause discard-changes to delete or move files outside the repository.

### Finding Description
`app/src/lib/path.ts` defines `resolveWithin`, which resolves a path relative to a root and rejects it (`realResolved.startsWith(realRoot)` check via `realpath`) if it escapes the root — including via a symlink, as explicitly covered by the unit test `app/test/unit/path-test.ts:66-78` ("fails for paths that use a symlink to traverse outside of the root"). This guard is consistently applied where user-visible, repository-relative paths are turned into filesystem operations, e.g. in `dispatcher.ts` (`openRepositoryFromUrl`) and `copilot-conflict-context.ts` (`buildConflictContext`).

`GitStore.discardChanges`, however, resolves paths directly without this guard: [1](#0-0) 

`file.path` originates from `git status` parsing (`app/src/lib/git/status.ts`) and therefore reflects whatever paths exist in the working directory of a repository the user cloned/fetched. Git can track symlinks as ordinary blobs. If a malicious repository commits a symlinked directory (e.g. `link -> ../../../../` or an absolute path to a sensitive directory) and any process (including git operations, hooks, or the user) subsequently creates a file underneath that symlinked path, `git status` will report an untracked/modified file such as `link/payload.txt`. `Path.resolve(this.repository.path, "link/payload.txt")` textually stays "inside" the repository, but the OS follows the `link` symlink at the filesystem level, so `shell.moveItemToTrash(...)` (or, in the permanent-delete fallback, `rm(Path.join(...))`) operates on a location outside the repository root.

The existing `resolveWithin` guard would catch exactly this case (it does a `realpath` comparison), but it is not invoked anywhere in `discardChanges`, so nothing stops the escape.

### Impact Explanation
This allows a crafted, attacker-controlled repository (clone/fetch source) to cause GitHub Desktop to move-to-trash or permanently delete (`rm`) files outside the intended repository directory once the user performs a normal "Discard Changes" action. This matches the allowed impact category "file write or read outside the repo" from unprivileged, attacker-controlled repository content — no local/physical access, admin rights, or pre-existing malware is required.

### Likelihood Explanation
Discard Changes is a routine, frequently used Desktop feature. The only attacker requirement is that the victim clones/fetches a repository containing a committed symlink pointing to a sensitive/external location and that a file appears under that symlinked path in the working tree (e.g., via a build step, git hook, or the attacker priming the repo state) before the user discards it. The `resolveWithin` guard already exists and is unit-tested for exactly this symlink scenario elsewhere in the codebase, showing the maintainers are aware of the risk class, but it was not applied to this call site — an inconsistency, not a missing capability.

### Recommendation
In `GitStore.discardChanges`, resolve `file.path` (and `file.status.oldPath` for renames/copies) through `resolveWithin(this.repository.path, file.path)` before calling `shell.moveItemToTrash` or `rm`, and skip/reject the operation if `resolveWithin` returns `null`, mirroring the pattern already used in `app/src/ui/dispatcher/dispatcher.ts` and `app/src/lib/copilot-conflict-context.ts`.

### Proof of Concept
1. Attacker publishes a repository containing a committed symlink `evil -> /Users/victim/Documents` (or, cross-platform, a relative traversal target) at the working tree root.
2. Victim clones the repository with GitHub Desktop.
3. Any process (e.g. a build script executed by the user, or a subsequent `git checkout`/hook) creates a file under `evil/` in the working tree, e.g. `evil/notes.txt`, which now aliases a file inside the victim's real Documents folder.
4. `git status` reports `evil/notes.txt` as untracked/modified; the Changes list shows it as a normal file to discard.
5. The victim selects "Discard Changes" on this file. `GitStore.discardChanges` calls `Path.resolve(this.repository.path, "evil/notes.txt")`, which the OS resolves through the `evil` symlink to `/Users/victim/Documents/notes.txt`, and `shell.moveItemToTrash` (or `rm` in the permanent-discard fallback) acts on that external file — outside the cloned repository, without any `resolveWithin` check blocking it. [2](#0-1) [3](#0-2)

### Citations

**File:** app/src/lib/stores/git-store.ts (L1558-1582)
```typescript
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
