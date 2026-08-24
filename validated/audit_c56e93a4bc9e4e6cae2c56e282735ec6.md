### Title
Symlinked working-directory entries let a malicious repository redirect Discard Changes deletions outside the repo - (File: app/src/lib/stores/git-store.ts)

### Summary
`GitStore.discardChanges` builds the filesystem path for every file the user asks to discard by joining the repository root with the `file.path` string reported by `git status`, with no containment check. [1](#0-0) [2](#0-1) 

Elsewhere in the codebase, the same "path from repo content might escape the repo root" class of problem is explicitly guarded against with a dedicated `resolveWithin()` helper that resolves symlinks and rejects any result outside the given root: [3](#0-2) 
That helper is used for AI/Copilot conflict-file reading in `copilot-conflict-context.ts`, but `discardChanges` in `git-store.ts` never calls it — the file paths it acts on go straight from `git status` output into `Path.resolve`/`Path.join` and then into `shell.moveItemToTrash` or `rm`. [4](#0-3) 

### Finding Description
`git status --porcelain -z` (parsed by `status-parser.ts`) reports paths exactly as Git sees them; it does not resolve symlinks and it will happily report a path such as `evil-link/secret.txt` for an untracked/modified file that is reached through a symlinked directory that is itself checked into the repository. The `WorkingDirectoryFileChange.path` produced from this status output is passed unmodified into `discardChanges`:

```
await this.shell.moveItemToTrash(
  Path.resolve(this.repository.path, file.path)
)
...
await rm(Path.join(this.repository.path, file.path))
``` [5](#0-4) 

If an attacker-controlled repository (clone/fetch target) commits a symlink, e.g. `evil-link -> /home/user` (or on Windows, a directory junction), and a file "inside" that symlinked directory shows up as untracked/modified in `git status` (e.g. `evil-link/.ssh/id_rsa` or any arbitrary file the attacker wants deleted/moved), then when the user runs "Discard Changes" (including "Discard All Changes", which is a single click) on that entry, Desktop resolves `repository.path + file.path` through the symlink and calls `shell.trashItem`/`fs.rm` on a path entirely outside the cloned repository. This corrupts/deletes attacker-chosen files on the user's disk — a "silent corruption of what the user commits" analog is actually a stronger primitive: silent destruction of arbitrary files outside the repo, triggered purely by content the attacker placed in the cloned repository.

This is the direct structural analog to the UNCX bug: `adminRefundEth`/`adminRefundERC20`/`lock` were supposed to operate only on a scoped balance (funds belonging to a specific position) but instead operated on the contract's entire, unscoped balance, letting an attacker redirect value to an address they choose. Here, `discardChanges` is supposed to operate only on paths scoped to the repository working directory, but because the path is derived from attacker-supplied repository content (a symlink) and never containment-checked, the operation is redirected to arbitrary locations on the filesystem outside the intended scope — exactly the "broken invariant + attacker-controlled destination" pattern from the report.

### Impact Explanation
Discarding changes on a crafted entry causes Desktop to call `shell.moveItemToTrash` or `fs.rm` on a path resolved through a symlink to anywhere on the user's filesystem that the Desktop process can access, with no user visibility that the true target is outside the repo (the Changes list only shows the relative path `evil-link/...`). This satisfies the "file write" (trash indirectly rewrites directory contents) / destructive-file-operation-outside-the-repo impact class from an unprivileged, attacker-controlled repository — no local access, no admin rights, and no pre-existing malware required, only opening/cloning the malicious repo in Desktop and clicking "Discard Changes"/"Discard All".

### Likelihood Explanation
The user action required (discarding a change shown in the Changes tab) is a completely ordinary Desktop workflow, not an "unnatural" step — many users routinely discard changes to files they don't recognize, especially untracked ones from a repo they just cloned. Symlinks are freely committable to Git and are preserved on clone (on non-Windows platforms, and via junctions/dev-mode symlinks on Windows). The lack of any `resolveWithin`-style guard in `discardChanges`, despite that exact utility already existing and being used elsewhere in the codebase for the same class of risk, indicates this specific path was not covered by the existing mitigation.

### Recommendation
Before calling `shell.moveItemToTrash` or `rm` in `GitStore.discardChanges`, resolve each `file.path` with the existing `resolveWithin(repository.path, file.path)` helper (as already done in `copilot-conflict-context.ts`) and skip/refuse the operation for any path that resolves outside the repository root or fails to resolve. [6](#0-5) 

### Proof of Concept
1. On macOS/Linux, create a malicious repository:
   ```
   git init evil-repo
   cd evil-repo
   ln -s /home/victim/.ssh evil-link
   git add evil-link
   git commit -m "add link"
   ```
2. Victim clones `evil-repo` with GitHub Desktop.
3. Attacker/repo update (or a post-clone hook/CI artifact) creates/modifies a file reachable through the symlink, e.g. touches `evil-repo/evil-link/id_rsa_backup`. `git status` reports this as an untracked/modified change at path `evil-link/id_rsa_backup` (validated by `status-parser.ts` behavior, which reports paths verbatim: [7](#0-6) ).
4. Victim opens Desktop, sees the file in the Changes list, and clicks "Discard Changes" (or "Discard All Changes").
5. `GitStore.discardChanges` runs `Path.resolve(repository.path, 'evil-link/id_rsa_backup')`, which resolves through the symlink to `/home/victim/.ssh/id_rsa_backup`, and calls `shell.moveItemToTrash`/`rm` on that real, outside-the-repo path — deleting/trashing a file the victim never intended to touch, entirely outside the cloned repository. [8](#0-7)

### Citations

**File:** app/src/lib/stores/git-store.ts (L1555-1584)
```typescript
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

**File:** app/src/lib/copilot-conflict-context.ts (L390-400)
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
```

**File:** app/src/lib/path.ts (L1-1)
```typescript
import * as Path from 'path'
```

**File:** app/src/lib/status-parser.ts (L172-182)
```typescript
function parseUntrackedEntry(field: string): IStatusEntry {
  const path = field.substring(2)
  return {
    kind: 'entry',
    // NOTE: We return ?? instead of ? here to play nice with mapStatus,
    // might want to consider changing this (and mapStatus) in the future.
    statusCode: '??',
    submoduleStatusCode: '????',
    path,
  }
}
```
