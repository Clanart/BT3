## Title
Symlink-based path escape in `discardChanges` allows a malicious repository to delete/trash files outside the repository — (File: `app/src/lib/stores/git-store.ts`)

### Summary
The reported Solana bug is a "trust the caller-supplied identifier instead of deriving/validating it" flaw: the handler uses whatever PDA account the signer passes instead of re-deriving it from trusted seeds, so a wrong (attacker-chosen) account gets its data wiped. The same class of bug — using an attacker-influenced path/identifier directly instead of validating it against a trusted root — exists in GitHub Desktop's `discardChanges` implementation, which resolves working-directory file paths coming from `git status` output directly with `Path.resolve`/`Path.join` instead of validating them stay inside the repository root, unlike other, newer code paths in the same codebase that consistently use `resolveWithin` for exactly this purpose.

### Finding Description
`GitStore.discardChanges` iterates over `WorkingDirectoryFileChange` objects (whose `.path` values originate from parsing `git status --porcelain=2 -z`, see `parsePorcelainStatus`/`buildStatusMap` in `app/src/lib/status-parser.ts` and `app/src/lib/git/status.ts`) and, for files to be trashed, calls: [1](#0-0) 

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

`file.path` is joined/resolved with the repository root using plain `Path.resolve`/`Path.join`, **without** the `resolveWithin` boundary check that the rest of the modern codebase now uses for exactly this scenario (symlink and traversal escapes), e.g. in: [2](#0-1) [3](#0-2) [4](#0-3) 

`Path.resolve`/`Path.join` only perform lexical normalization — they do not call `realpath` and therefore do not detect that a path component is a symlink pointing outside the repository. The project's own tests demonstrate this exact escape vector is real and must be defended against with `resolveWithin`: [5](#0-4) 

A malicious repository can commit a symlink (e.g. `evil -> ../../../../some/sensitive/dir`) as a tracked file. Once cloned/checked out, any subsequent file that appears (via `git status`) to live "under" `evil/…` — for example an untracked file the victim later creates inside what they believe is a normal repo subfolder, or one delivered by a crafted checkout/submodule state — will have a `file.path` like `evil/target-file`. When the user selects "Discard Changes" on that entry, `Path.resolve(repository.path, 'evil/target-file')` follows the symlink at the OS level during the actual file operation and `shell.moveItemToTrash`/`rm` acts on a path **outside** the repository, silently deleting or trashing an arbitrary file the attacker chose via the symlink target.

### Impact Explanation
This breaks the invariant that "Discard Changes" only ever affects files inside the opened repository's working directory. A crafted/cloned repository (fully attacker-controlled content, no local access or credentials needed) can cause GitHub Desktop to delete or move-to-trash arbitrary files elsewhere on the victim's filesystem once the victim performs a completely ordinary, expected action (discarding changes on a file shown in the Changes list). This is a file-write/delete-outside-repo primitive driven purely by a cloned/fetched repository's contents, matching the accepted impact class (attacker controls a cloned repo → file write/delete outside the repo).

### Likelihood Explanation
Likelihood is high in the sense that no unusual steps are required beyond the normal Desktop workflow: clone/open a malicious repo and click "Discard Changes" on a listed file — an action Desktop explicitly encourages users to take to "clean up" their working directory. The existing hardening (`resolveWithin`, dedicated symlink-escape tests) shows the maintainers are aware of and defend against this exact primitive elsewhere, but `discardChanges` — one of the most destructive operations in the app — was not migrated to use it.

### Recommendation
In `GitStore.discardChanges` (`app/src/lib/stores/git-store.ts`), replace the raw `Path.resolve`/`Path.join` calls with `resolveWithin(this.repository.path, file.path)` (already used elsewhere, e.g. `app/src/lib/copilot-conflict-context.ts` and `app/src/ui/dispatcher/dispatcher.ts`), and refuse to trash/remove the file (surfacing an error instead) whenever the resolved path is `null`, mirroring the pattern:
```
const resolved = await resolveWithin(this.repository.path, file.path)
if (resolved === null) {
  // refuse the operation; log/report instead of touching the filesystem
} else {
  await this.shell.moveItemToTrash(resolved)
}
```
Apply the same fix to the `rm(Path.join(this.repository.path, file.path))` fallback path.

### Proof of Concept
1. Attacker creates a repository containing a tracked symlink `evil` pointing to a directory outside any repo root (e.g. `../../../../Users/victim/Documents`), and commits it.
2. Victim clones the repository with GitHub Desktop. The checkout materializes `evil` as a real symlink in the working tree.
3. Through any means that causes `git status` to report a change under `evil/...,` (e.g., a later fetch/checkout that adds a tracked file at `evil/important-file.txt`, or victim inadvertently saving a file there via another tool while the repo is open), Desktop's Changes list shows `evil/important-file.txt`.
4. Victim selects that entry and clicks "Discard Changes."
5. `GitStore.discardChanges` calls `Path.resolve(repository.path, 'evil/important-file.txt')` and `shell.moveItemToTrash(...)`, which — following the OS-level symlink — trashes/deletes the real file living outside the repository at the attacker-chosen target location, without any warning that the destroyed file is outside the repo.

### Citations

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
