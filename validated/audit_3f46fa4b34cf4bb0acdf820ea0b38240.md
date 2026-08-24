### Title
Discarding changes trusts unsanitized `git status` paths, allowing a symlink in a cloned repository to redirect file deletion outside the repo - (File: app/src/lib/stores/git-store.ts)

### Summary
`GitStore.discardChanges` builds the on-disk path to delete/trash purely from the relative path string reported by `git status`, using `Path.resolve`/`Path.join` with no containment check. Elsewhere in the same codebase (`app/src/lib/copilot-conflict-context.ts`, `app/src/ui/dispatcher/dispatcher.ts`, `app/src/lib/stores/app-store.ts`) the maintainers introduced `resolveWithin()` (`app/src/lib/path.ts`) specifically to "guard against path traversal and symlink escapes" before touching the filesystem. That guard is not applied to the discard-changes code path, so a symlink committed by an attacker-controlled repository can cause Desktop's "Discard Changes" feature to operate on a filesystem location outside the intended repository directory when the user discards what they believe is a local, in-repo change.

### Finding Description
`GitStore.discardChanges` iterates the `WorkingDirectoryFileChange` list (built from `git status --porcelain=2`, see `app/src/lib/git/status.ts:190-350`) and for every non-deleted, non-submodule file does: [1](#0-0) 
```
if (file.status.kind !== AppFileStatusKind.Deleted && !foundSubmodule) {
  if (moveToTrash) {
    try {
      await this.shell.moveItemToTrash(
        Path.resolve(this.repository.path, file.path)
      )
    } catch (e) {
      ...
      if (file.status.kind === AppFileStatusKind.Untracked) {
        await rm(Path.join(this.repository.path, file.path))
      }
    }
  }
```
Both `Path.resolve` and `Path.join` perform purely lexical path composition — they do not consult the filesystem and do not detect that an intermediate path component is a symbolic link. `file.path` (and `file.status.oldPath` used a few lines later for renames/copies at `app/src/lib/stores/git-store.ts:1586-1601`) is untrusted repository content: it is derived from tree entries and working-tree entries that a cloned or fetched repository can freely define, including tracked symbolic links.

The codebase already recognizes this exact class of bug. `app/src/lib/path.ts` implements `resolveWithin()`, whose doc comment states the resolved path is "guaranteed to reside at, or underneath" the given root, and its test suite explicitly verifies protection against "a symlink to traverse outside of the root" (`app/test/unit/path-test.ts:65-78`). `resolveWithin` is used in `app/src/lib/copilot-conflict-context.ts:390-407` precisely because conflicted-file paths originate from repository content and must not be trusted blindly, with the comment "Guard against path traversal and symlink escapes (cross-platform)." The same defensive pattern is used in `app/src/ui/dispatcher/dispatcher.ts` and `app/src/lib/stores/app-store.ts`.

`GitStore.discardChanges`, `discardChangesFromSelection` (`app/src/lib/git/apply.ts:102-119`), and `revealInFileManager` (`app/src/lib/app-shell.ts:61-64`) do not use `resolveWithin` and instead compose paths with plain `Path.resolve`/`Path.join`/`Path.join`, meaning a symlinked path component supplied by the repository's own tracked content is followed by the OS at the moment of deletion/trashing, sending the operation to whatever location the symlink target points to.

### Impact Explanation
This falls squarely into the report's requested class: attacker controls a cloned/fetched repository, and the result is a file-system operation (deletion via `shell.moveItemToTrash` or `fs.rm`) that can be redirected outside the repository the user believes they are operating on. Discard Changes is one of Desktop's most routine, low-friction actions (a single confirmation dialog, performed constantly), so a victim who clones or opens a malicious/compromised repository containing a tracked symlink and then discards "changes" touching a path through that symlink risks having an out-of-repo file or directory silently trashed or permanently deleted (when trash fails and the fallback `rm` path is taken for untracked entries). This is a corruption/data-loss primitive outside the intended sandbox of the repository working directory, matching "file write or read outside the repo" and "silent corruption" impact categories.

### Likelihood Explanation
Likelihood depends on git's directory-walk behavior around symlinked paths reported by `git status`, which the tool could not fully verify without executing git in this session. What is certain and directly observable in the code is the structural weakness: the discard path explicitly skips the containment check (`resolveWithin`) that the same repository's authors added elsewhere for the identical purpose (repository-controlled, potentially symlinked relative paths feeding a filesystem call). This is a real, demonstrable gap between "protected" and "unprotected" code paths handling equivalent untrusted input, not a purely hypothetical concern — but full end-to-end exploitability (i.e., confirming a concrete `git status` porcelain sequence that yields a `file.path`/`file.status.oldPath` traversing a symlink into content outside the repo root) would need to be validated experimentally against the bundled `dugite`/git version, which is out of scope for a read-only code review.

### Recommendation
Route all repository-relative paths used for destructive filesystem operations (`GitStore.discardChanges`, `discardChangesFromSelection`, `revealInFileManager`, and any other consumer of `WorkingDirectoryFileChange.path`/`oldPath`) through `resolveWithin(repository.path, file.path)` (and `oldPath`) before calling `shell.moveItemToTrash`, `rm`, or `Path.join`-based file access, and abort/skip the operation (surfacing an error to the user) when `resolveWithin` returns `null`, mirroring the pattern already implemented in `app/src/lib/copilot-conflict-context.ts`.

### Proof of Concept
Not independently executed in this session (no filesystem/terminal access). Conceptual PoC to be verified by a Devin session with repo execution access:
1. Create a malicious repository containing a tracked symbolic link, e.g. `evil-link -> ../../../../Desktop` (or another sensitive out-of-repo directory), and commit it.
2. Have the victim clone this repository with GitHub Desktop.
3. Arrange for `git status` to report a working-tree change under a path that traverses `evil-link` (e.g., an untracked or modified file whose relative path component resolves through the symlink) — this step needs empirical confirmation against the git/dugite version bundled with Desktop, since git's untracked-file directory walk does not always recurse into symlinked directories.
4. In Desktop, select "Discard changes" on that entry.
5. Observe whether `GitStore.discardChanges` (`app/src/lib/stores/git-store.ts:1558-1573`) calls `shell.moveItemToTrash`/`rm` on the resolved path and whether the OS follows the symlink, deleting/trashing content outside the cloned repository directory — compare against `resolveWithin`'s explicit symlink-escape test in `app/test/unit/path-test.ts:65-78`, which demonstrates the exact traversal `Path.resolve`/`Path.join` fail to catch.

### Citations

**File:** app/src/lib/stores/git-store.ts (L1558-1573)
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
```
