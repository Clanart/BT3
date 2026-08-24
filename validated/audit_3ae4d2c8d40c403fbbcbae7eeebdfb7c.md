## Title
Discarding changes in a cloned repository can delete/overwrite files outside the repo via symlinked working-tree paths - (File: `app/src/lib/stores/git-store.ts`)

### Summary
`GitStore.discardChanges()` resolves each `WorkingDirectoryFileChange.path` directly against the repository root with `Path.resolve()`/`Path.join()` and then either moves the resulting file to the trash or unlinks it, with no check that the resolved location actually stays inside the repository working directory. The codebase already has a dedicated helper, `resolveWithin()`, built exactly to guard against this class of path escape (it `realpath`s both the root and the target and verifies containment), but it is only used in `copilot-conflict-context.ts`, not in the discard-changes path.

### Finding Description
`discardChanges()` iterates over the files the user asked to discard and, for each one, does: [1](#0-0) 

Both `Path.resolve(this.repository.path, file.path)` (used for `shell.moveItemToTrash`) and `Path.join(this.repository.path, file.path)` (used for the manual `rm` fallback) trust `file.path` as reported by `git status` for the working directory, with no traversal/symlink containment check comparable to `resolveWithin`: [2](#0-1) 

That helper explicitly `realpath`s the resolved target and rejects it unless it is still nested under the repo root — precisely because a plain `Path.resolve`/`join` check cannot detect that an intermediate path component is a symlink pointing outside the tree: [3](#0-2) 

The broken invariant: Desktop's discard/delete operations are only supposed to touch files inside the cloned repository's working directory. If an attacker-controlled repository (one the user clones, or a branch/PR the user fetches) contains a tracked-then-locally-modified path where a path segment is a symlink to a location outside the repo (e.g. a directory symlink such as `link -> /`), git will report a normal "modified"/"new file" status entry for a nested path under that symlink. `discardChanges()` resolves that path with plain `Path.resolve`, which happily follows through the symlink target, and the subsequent `shell.moveItemToTrash` or `rm` call deletes/moves whatever file that resolves to — which can be outside the repository entirely.

### Impact Explanation
This lets an attacker who controls the contents of a repository (via a symlinked path checked into the tree, or introduced as an untracked/new file with a symlinked parent directory) cause GitHub Desktop to delete or move arbitrary files on the victim's filesystem outside the cloned repo, once the victim opens the "Changes" tab and clicks "Discard Changes" (or "Discard all changes") on the affected entry — an ordinary, expected user action, not an unnatural workaround. This is an out-of-repo file-deletion primitive triggered purely by cloning/fetching attacker content and performing a normal Desktop action.

### Likelihood Explanation
Discarding changes is one of the most common actions in Desktop's daily workflow, and the vulnerable code path (`AppFileStatusKind.Untracked`/modified handling in `discardChanges`) is hit for any new or modified file, not just an edge case. The only requirement is that the attacker gets a symlinked path into the user's working tree (via clone of a malicious repo, a branch checkout, or an applied patch/PR that creates such a path) — no local access, admin rights or social engineering beyond "clone/open this repo" is needed.

### Recommendation
Route every path used by `discardChanges()` (and `discardChangesFromSelection`) through `resolveWithin()`/`resolveWithinPosix()`/`resolveWithinWin32()` (as already done in `copilot-conflict-context.ts`) before calling `shell.moveItemToTrash` or `rm`, and skip/reject any file whose resolved real path escapes the repository root instead of performing the destructive operation.

### Proof of Concept
1. Attacker publishes a repository containing a symlinked directory (e.g. `evil -> /Users/victim` on macOS/Linux, or an equivalent reparse point on Windows) with a tracked file nested under it, e.g. `evil/.ssh/known_hosts`, then locally the victim's checkout has this file appear "modified" or "untracked" after some interaction (or attacker ships it as untracked content in an archive/branch).
2. Victim clones/fetches and opens the repo in GitHub Desktop; the Changes list shows the "modified"/"new" file `evil/.ssh/known_hosts`.
3. Victim clicks "Discard changes" on that entry (or "Discard all").
4. `discardChanges()` calls `Path.resolve(this.repository.path, 'evil/.ssh/known_hosts')`, which resolves through the symlink to `/Users/victim/.ssh/known_hosts`, and `shell.moveItemToTrash`/`rm` acts on that file outside the repository — corrupting or deleting a file the user never intended to touch, entirely outside the guard that `resolveWithin` would have provided.

### Citations

**File:** app/src/lib/stores/git-store.ts (L1558-1583)
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
