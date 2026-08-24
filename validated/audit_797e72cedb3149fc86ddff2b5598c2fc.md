### Title
Path Containment Missing on `discardChanges()` Trust of Git-Reported File Paths - ([File: app/src/lib/stores/git-store.ts])

### Summary
The external report describes a missing-access-control pattern: `burnFrom()` performs a destructive operation (`_burn`) directly on attacker-supplied input without validating that the caller/holder relationship is authorized. The GitHub Desktop analog is `GitStore.discardChanges()` [1](#0-0) , which performs a destructive operation (permanently deleting/trashing a working-directory file) using a `file.path` value taken directly from `git status` output, without ever validating that the resolved path stays inside the repository root — unlike other destructive/file-write code paths in this same codebase which do perform that check via `resolveWithin()`.

### Finding Description
`discardChanges()` builds a filesystem path for every file it is asked to discard and immediately acts on it: [2](#0-1) 

Both `this.shell.moveItemToTrash(Path.resolve(this.repository.path, file.path))` and `rm(Path.join(this.repository.path, file.path))` build the target path with plain `Path.resolve`/`Path.join`, with no verification that the result is contained within `this.repository.path`. `file.path` is not a value the user typed — it comes straight out of `git status --porcelain=2 -z` parsing [3](#0-2)  and [4](#0-3) , which is attacker-controllable content in a cloned/fetched repository (e.g., via committed paths, submodule entries, or symlinked directories checked out into the working tree that make a nested path resolve outside the repo on the filesystem).

This is notable because the codebase already has a purpose-built defense for exactly this class of bug — `resolveWithin()` in `app/src/lib/path.ts`, which normalizes, checks for null bytes, resolves symlinks via `realpath`, and rejects any path that escapes the root: [5](#0-4) 

`resolveWithin()` is actively used to guard other destructive/write operations that consume repo-relative, attacker-influenced paths, such as the Copilot conflict-resolution file writer [6](#0-5)  and the conflict context reader [7](#0-6) , and deep-link file reveal handling [8](#0-7) . `discardChanges()` in `git-store.ts` was not updated to use this guard, meaning the "allowance check" pattern that exists elsewhere in the codebase is missing exactly where a destructive filesystem action (trash/`rm`) is taken on a git-supplied path.

### Impact Explanation
If a working-directory entry's path can be made to resolve (after symlink resolution) outside the repository root — for example through a symlinked directory component introduced by a malicious clone/checkout, or a crafted rename/copy `oldPath`/`path` pair from `git status` — `moveItemToTrash`/`rm` will act on that external path when the user discards changes or (for `AppFileStatusKind.Untracked`) permanently deletes. This can delete or move-to-trash files anywhere the app process has permission to write, outside the user's intended repository, mirroring the "destroy something without proper authorization" impact class from the report (there it was token balances; here it's arbitrary filesystem entries reachable via a symlink-augmented working tree).

### Likelihood Explanation
Requires an attacker-controlled repository (cloned or fetched, matching the in-scope threat model) that introduces a symlinked directory or crafted rename/copy path into the working tree, and requires the victim user to invoke "Discard changes" on the affected file(s) — a normal, expected Desktop action, not an unnatural user step. No admin rights, local access, or leaked credentials needed. This is a real, exploitable gap given `resolveWithin` exists specifically to close this hole elsewhere but isn't applied here.

### Recommendation
Route the file/oldPath values used in `discardChanges()` through `resolveWithin(this.repository.path, file.path)` (and for `file.status.oldPath` in the rename/copy branch) before calling `moveItemToTrash` or `rm`, and abort/skip with a logged warning when the result is `null`, consistent with the pattern already used in `app-store.ts` and `copilot-conflict-context.ts`.

### Proof of Concept
Not independently verified end-to-end (would require constructing a git repository whose checked-out working tree contains a symlinked directory such that a tracked/untracked file path resolves outside the repo root, then triggering "Discard Changes" in the UI on that file). Conceptual PoC based on code inspection:
1. Attacker crafts a repository where, after clone/checkout, a directory component in the working tree is a symlink pointing outside the repo (e.g., `evil -> /home/victim`), and a tracked/untracked file is reported by `git status` at `evil/.ssh/authorized_keys` or similar.
2. Victim clones/fetches this repository in GitHub Desktop and opens the Changes view; `git status` reports the crafted path as untracked/modified.
3. Victim selects "Discard Changes" for that file (or "Discard All Changes").
4. `GitStore.discardChanges()` builds `Path.resolve(this.repository.path, file.path)` / `Path.join(...)` [9](#0-8)  and calls `moveItemToTrash`/`rm` on the symlink-resolved location outside the repository, without any `resolveWithin` containment check, unlike the guarded code paths cited above.

### Citations

**File:** app/src/lib/stores/git-store.ts (L1545-1549)
```typescript
  public async discardChanges(
    files: ReadonlyArray<WorkingDirectoryFileChange>,
    moveToTrash: boolean = true,
    askForConfirmationOnDiscardChangesPermanently: boolean = false
  ): Promise<void> {
```

**File:** app/src/lib/stores/git-store.ts (L1558-1574)
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
```

**File:** app/src/lib/status-parser.ts (L105-119)
```typescript
function parseChangedEntry(field: string): IStatusEntry {
  const match = changedEntryRe.exec(field)

  if (!match) {
    log.debug(`parseChangedEntry parse error: ${field}`)
    throw new Error(`Failed to parse status line for changed entry`)
  }

  return {
    kind: 'entry',
    statusCode: match[1],
    submoduleStatusCode: match[2],
    path: match[8],
  }
}
```

**File:** app/src/lib/git/status.ts (L233-254)
```typescript
  const parsed = parsePorcelainStatus(stdout)
  const headers = parsed.filter(isStatusHeader)
  const entries = parsed.filter(isStatusEntry)

  const mergeHeadFound = await isMergeHeadSet(repository)
  const conflictedFilesInIndex = entries.filter(e =>
    conflictStatusCodes.includes(e.statusCode)
  )
  const rebaseInternalState = await getRebaseInternalState(repository)

  const conflictDetails = await getConflictDetails(
    repository,
    mergeHeadFound,
    conflictedFilesInIndex,
    rebaseInternalState
  )

  // Map of files keyed on their paths.
  const files = entries.reduce(
    (files, entry) => buildStatusMap(files, entry, conflictDetails),
    new Map<string, WorkingDirectoryFileChange>()
  )
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
