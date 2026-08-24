## Analog Found

<br>

### Title
Copilot conflict-resolution write path does not deduplicate case-colliding paths, silently discarding one file's resolved content on commit - (File: `app/src/lib/stores/app-store.ts`)

### Summary
The external report describes a class of bug where a list-based operation (adding `tokenIds`) is validated element-by-element but never checked for **duplicates that collide on the underlying resource**, so a later loop silently clobbers/loses one of the entries. The GitHub Desktop analog is the "Resolve with Copilot" merge-conflict feature: `validateResolutionPaths` only rejects *string-identical* duplicate paths, and the write-back loop in `AppStore._applyCopilotConflictResolutions` writes each resolution to `resolveWithin(repository.path, resolution.path)` without checking whether two logically distinct conflicted paths resolve to the **same file on disk** (case-insensitive filesystems, which are the default on macOS and Windows). A case-only rename conflict — trivially producible by an attacker's branch/PR — creates exactly this situation, and the second `writeFile` silently overwrites the first, with the corrupted result then staged with `git add` and eventually committed without any error being raised.

### Finding Description
`validateResolutionPaths` in [1](#0-0)  guards against duplicate resolutions using a `Set<string>` of the normalized path strings:

```
const returnedPaths = new Set(resolutions.map(r => r.path))
...
if (returnedPaths.size !== resolutions.length) {
  throw new CopilotValidationError('Copilot returned duplicate file paths in resolutions')
}
```

This check (and the earlier `normalizeLLMPath` step at [2](#0-1) ) is purely a string-equality check. It does not account for the fact that two *different* repository-relative paths — e.g. `Foo.txt` and `foo.txt` — can resolve to the very same file on a case-insensitive filesystem (default on macOS/APFS and Windows/NTFS).

Such a pair of distinct-but-colliding conflicted paths is a real, reachable state: a case-only rename (`git mv foo.txt Foo.txt`) on one branch combined with a modification of `foo.txt` on the other branch produces a genuine two-entry merge conflict where both `foo.txt` and `Foo.txt` appear as separate `IFileConflictContext` entries built in [3](#0-2) . Both entries independently pass `resolveWithin` (which just checks the resolved path lies under the repo root) and are sent to Copilot as two separate files, each producing its own `IFileResolution`.

Because `validateResolutionPaths` treats these as two distinct, valid paths, both resolutions proceed to the write-back loop:

```
const absolutePath = await resolveWithin(repository.path, resolution.path)
...
await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
pathsToStage.push(resolution.path)
``` [4](#0-3) 

On a case-insensitive filesystem, `resolveWithin(repo, 'Foo.txt')` and `resolveWithin(repo, 'foo.txt')` return paths that point at the *same inode*. The second `writeFile` call silently overwrites whatever the first call wrote — there is no error, since `writeFile` on a case-insensitive FS just truncates and rewrites the same physical file. The loop then does:

```
await git(['add', '--', ...pathsToStage], repository.path, 'copilotConflictResolution')
``` [5](#0-4) 

`git add` stages both logical paths (`Foo.txt` and `foo.txt`), but both index entries now contain the content of whichever resolution was written last — the first file's Copilot-resolved content is silently discarded and replaced by the second file's content under a different path in the index. No exception is thrown anywhere in this path; the app reports the merge as successfully resolved.

### Impact Explanation
This results in **silent corruption of what the user commits/pushes**: the file that "lost" the race is committed with the wrong resolved content (potentially incomplete/broken code, or code containing conflict-resolution material from a completely different file), while the UI and git report a clean, successful conflict resolution. An attacker who crafts a branch/PR designed to be merged (via a case-only rename plus a colliding modify) can induce this on a victim who uses the "Resolve with Copilot" feature, without needing any local access, admin rights, leaked credentials, or unusual user steps beyond the ordinary merge-and-resolve workflow.

### Likelihood Explanation
Case-only rename conflicts are an established, naturally-occurring git conflict class, and Desktop already has dedicated logic elsewhere for case-insensitive filesystem handling in other parts of the codebase, indicating the team is aware such collisions occur. The trigger requires nothing more than a maliciously crafted branch history that a user merges/rebases against and then chooses to resolve with the Copilot conflict-resolution feature — no special access needed, only that the victim uses this feature (which requires no unnatural steps, it's a first-party button in the merge-conflict dialog).

### Recommendation
- In `validateResolutionPaths` (and/or before building `pathsToStage` in `_applyCopilotConflictResolutions`), detect path collisions using the *resolved, case-normalized* absolute path (or `realpath`) rather than the raw string, and reject/flag resolutions whose paths collide on the current filesystem.
- Alternatively, resolve all `absolutePath`s up front and de-duplicate/serialize writes per unique real path, surfacing a conflict-resolution error (mirroring the "duplicate file paths" error already thrown for exact string duplicates) instead of allowing a silent overwrite-then-stage sequence.

### Proof of Concept
1. Attacker prepares a branch `feature` from `main` that renames `main.txt` to `Main.txt` (case-only rename) with different content changes.
2. Victim's `main` branch independently modifies `main.txt`.
3. Victim merges `feature` into `main` on macOS or Windows (case-insensitive filesystem). Git reports a conflict touching both `main.txt` and `Main.txt` as two separate conflicted paths.
4. Victim clicks "Resolve with Copilot". `buildConflictContext` reads both paths — both resolve to the same on-disk file — and sends two file contexts to the model, which returns two independent `IFileResolution` entries, one for `main.txt` and one for `Main.txt`.
5. `validateResolutionPaths` sees two distinct strings, no duplicate error is raised.
6. `_applyCopilotConflictResolutions` writes `main.txt`'s resolved content, then writes `Main.txt`'s resolved content to the same physical file, silently overwriting the first.
7. Both `main.txt` and `Main.txt` are staged via `git add`; the commit that follows silently contains the wrong/lost content for one of the two logical paths, with no error or warning shown to the user.

### Citations

**File:** app/src/lib/copilot-conflict-resolution.ts (L266-272)
```typescript
function normalizeLLMPath(raw: string): string {
  return raw
    .trim()
    .replace(/\\/g, '/')
    .replace(/^\.\//, '')
    .replace(/\/\/+/g, '/')
}
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L473-495)
```typescript
export function validateResolutionPaths(
  resolutions: ReadonlyArray<IRawFileResolution>,
  expectedFiles: ReadonlyArray<IFileConflictContext>
): void {
  const expectedPaths = new Set(expectedFiles.map(f => f.path))
  const expectedHunkCounts = new Map(
    expectedFiles.map(f => [f.path, f.hunks.length])
  )
  const returnedPaths = new Set(resolutions.map(r => r.path))

  for (const path of returnedPaths) {
    if (!expectedPaths.has(path)) {
      throw new CopilotValidationError(
        `Copilot returned resolution for unexpected file: ${path}`
      )
    }
  }

  if (returnedPaths.size !== resolutions.length) {
    throw new CopilotValidationError(
      'Copilot returned duplicate file paths in resolutions'
    )
  }
```

**File:** app/src/lib/copilot-conflict-context.ts (L367-461)
```typescript
export async function buildConflictContext(
  ourLabel: string,
  theirLabel: string,
  workingDirectory: string,
  files: ReadonlyArray<{
    readonly path: string
    /** Which side deleted the file (for delete-vs-modify conflicts). */
    readonly deletedSide?: 'ours' | 'theirs'
  }>
): Promise<ICopilotConflictContext> {
  const results = await Promise.all(
    files.map(async (file): Promise<IFileConflictContext> => {
      // Delete-vs-modify conflicts have no text markers on disk. Include
      // them in the context with metadata so the model can recommend
      // keep or delete — no file content is needed.
      if (file.deletedSide !== undefined) {
        return {
          path: file.path,
          hunks: [],
          deleteConflict: { deletedSide: file.deletedSide },
        }
      }

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

      // Guard against reading pathologically large files into memory. This is
      // a memory-safety bound only — resolvability is decided from the conflict
      // hunks below, not the whole-file size.
      try {
        const fileStat = await stat(absolutePath)
        if (fileStat.size > MAX_CONFLICT_FILE_READ_SIZE) {
          return {
            path: file.path,
            hunks: [],
            skippedReason: 'File too large to resolve automatically',
          }
        }
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File could not be read',
        }
      }

      let content: string
      try {
        content = await readFile(absolutePath, 'utf8')
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File could not be read',
        }
      }

      const hunks = extractConflictHunks(content)
      if (hunks.length === 0) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'No conflict markers found',
        }
      }

      // Gate on the size of the conflict content we'd actually send to the
      // model, not the whole-file size.
      const hunkSkipReason = getHunkSkipReason(hunks)
      if (hunkSkipReason !== null) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: hunkSkipReason,
        }
      }

      return { path: file.path, hunks, rawContent: content }
    })
```

**File:** app/src/lib/stores/app-store.ts (L7233-7259)
```typescript
      const absolutePath = await resolveWithin(repository.path, resolution.path)
      if (absolutePath === null) {
        log.warn(
          `Copilot resolution skipped: path outside repository: ${resolution.path}`
        )
        continue
      }

      // If the user resolved this file externally (e.g. in their editor) while
      // the result dialog was open, git status will report it with no remaining
      // conflict markers. Overwriting it with Copilot's stored content would
      // silently clobber their work, so skip it and let their resolution stand.
      // This mirrors how the manual conflicts dialog determines a file is
      // resolved (`hasUnresolvedConflicts`).
      const onDiskFile = state.changesState.workingDirectory.files.find(
        f => f.path === resolution.path
      )
      if (
        onDiskFile !== undefined &&
        isConflictedFileStatus(onDiskFile.status) &&
        !hasUnresolvedConflicts(onDiskFile.status)
      ) {
        continue
      }

      await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
      pathsToStage.push(resolution.path)
```

**File:** app/src/lib/stores/app-store.ts (L7262-7268)
```typescript
    if (pathsToStage.length > 0) {
      await git(
        ['add', '--', ...pathsToStage],
        repository.path,
        'copilotConflictResolution'
      )
    }
```
