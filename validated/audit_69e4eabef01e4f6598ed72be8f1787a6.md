## Confirmed candidate: `GitStore.discardChanges()` resolves untracked-file paths without the same containment check used elsewhere in the codebase

### Title
Inconsistent path-containment validation between `discardChanges()` (unchecked `Path.join`) and other file-write paths (checked via `resolveWithin`) - (File: `app/src/lib/stores/git-store.ts`)

### Summary
The report's bug class is an asymmetric-invariant issue: two operations that should share the same safety guarantee actually enforce different levels of validation, and the weaker one can be reached to corrupt data outside the intended boundary. In Desktop, `discardChanges()` in `app/src/lib/stores/git-store.ts` builds destination paths for `moveItemToTrash`/`rm` using raw `Path.resolve`/`Path.join` on `file.path` (untracked entries reported by `git status`), while the analogous file-write path added for Copilot conflict resolution in `app/src/lib/stores/app-store.ts` explicitly validates the same kind of git-status-derived path with `resolveWithin()` before touching disk.

### Finding Description
`resolveWithin()` (`app/src/lib/path.ts:36-100`) is the app's dedicated guard against directory-traversal in paths that originate from external/untrusted sources (e.g., a path reported by `git status` in a cloned repo). It is used, for example, in the Copilot conflict-resolution flow: [1](#0-0) 
which explicitly rejects any resolution path that escapes the repository root before calling `writeFile`.

`discardChanges()`, however, takes `file.path` — a string parsed straight out of `git status --porcelain -z` output by `parseUntrackedEntry()` (`app/src/lib/status-parser.ts:172-182`, no `..`/traversal filtering) — and feeds it directly into `Path.resolve`/`Path.join` with no `resolveWithin` check: [2](#0-1) 

The status parser comment itself notes: "filenames containing special characters are not specially formatted; no quoting or backslash-escaping is performed" (`app/src/lib/status-parser.ts:70-72`), i.e., whatever bytes git reports as an untracked path (including `../` sequences) pass through unmodified into `IStatusEntry.path`, and from there into `WorkingDirectoryFileChange.path`, and from there into `discardChanges()`.

### Impact Explanation
If an attacker can make `git status` report an "untracked" entry whose path contains `../` segments (e.g., via a crafted `.git/info/exclude`, a nested/symlinked working tree, or a submodule/worktree layout that causes git to report a path outside the repo root — the exact vector needs to be confirmed against real git behavior), `Path.resolve(repository.path, file.path)` would resolve **outside** the repository, and Desktop would call `shell.moveItemToTrash()` or `rm()` on that resolved path when the user clicks "Discard Changes." This is a file-delete-outside-repo primitive, matching the report's "silent corruption of what the user commits" / "file write or read outside the repo" impact class — mirroring how the deposit/withdraw bug let an unvalidated path slip through one code path while a parallel path (withdraw / Copilot writeFile) enforced the check.

### Likelihood Explanation
Confidence is **moderate, not confirmed**: I verified the code asymmetry (one path uses `resolveWithin`, the sibling path does not), but I was not able to fully verify, within the available tools, whether `git status --porcelain -z` can actually be coerced into emitting an untracked-entry path containing `../` for a file physically located outside the repository's working tree (normal git behavior restricts status entries to the working tree, so this may require an unusual condition such as a symlinked/relocated `.git` dir, nested worktrees, or a malicious `core.worktree`/`.gitmodules` setup). Without confirming a concrete attacker-controlled trigger for such a path, this should be treated as a plausible but unproven analog.

### Recommendation
Route `file.path` (and `file.status.oldPath` for renames) in `discardChanges()` through `resolveWithin(this.repository.path, file.path)` before calling `moveItemToTrash`/`rm`, exactly as done in the Copilot conflict-resolution code path, and skip/reject any file whose resolved path escapes the repository root — making the two code paths consistent instead of one being hardened and the other not.

### Proof of Concept
Not independently verified end-to-end (requires confirming a git status trigger for out-of-tree untracked paths); the concrete corrupted-value chain is:
1. `git status --porcelain -z` reports an untracked path such as `../../.ssh/authorized_keys` for some contrived repository state.
2. `parseUntrackedEntry()` stores it verbatim as `IStatusEntry.path` (`app/src/lib/status-parser.ts:172-182`).
3. It becomes `WorkingDirectoryFileChange.path`.
4. User selects "Discard changes"; `GitStore.discardChanges()` computes `Path.resolve(this.repository.path, file.path)` (`app/src/lib/stores/git-store.ts:1561-1563`) — no `resolveWithin` check — resolving to a path outside the repo, which is then passed to `shell.moveItemToTrash()`/`rm()`. [3](#0-2) [1](#0-0) [4](#0-3) [5](#0-4)

### Citations

**File:** app/src/lib/stores/app-store.ts (L7233-7239)
```typescript
      const absolutePath = await resolveWithin(repository.path, resolution.path)
      if (absolutePath === null) {
        log.warn(
          `Copilot resolution skipped: path outside repository: ${resolution.path}`
        )
        continue
      }
```

**File:** app/src/lib/stores/git-store.ts (L1556-1583)
```typescript
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

**File:** app/src/lib/path.ts (L95-100)
```typescript
export function resolveWithin(
  rootPath: string,
  ...pathSegments: string[]
): Promise<string | null> {
  return _resolveWithin(rootPath, pathSegments)
}
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
