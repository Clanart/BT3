## Finding

### Title
Copilot conflict-resolution file writes validate path containment only once, before the write, with no post-write re-verification — ([File: app/src/lib/stores/app-store.ts])

### Summary
The reported smart-contract bug is about an invariant (`totalStaked ≥ totalFrozen`) that is checked only *before* a mutating call, when it also needs to be re-checked *after* the mutation, because the mutation itself can move the system out of the invariant. The closest structural analog in GitHub Desktop is the path-containment invariant enforced by `resolveWithin` in `app/src/lib/path.ts`, used in `AppStore._applyCopilotConflictResolutions` (`app/src/lib/stores/app-store.ts`) to guard `writeFile()` calls that persist AI-generated conflict resolutions to disk. The containment check is performed once, before the write, and is never re-verified against the actual location that gets written to after resolution.

### Finding Description
`resolveWithin()` [1](#0-0)  determines whether a repository-relative path stays inside the repository root by calling `realpath()` on both the root and the resolved target, comparing the two, and — critically — **returning the pre-`realpath` joined path** (`resolved`), not the `realpath`-verified one (`realResolved`):

```
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
```

`_applyCopilotConflictResolutions` uses this single, one-shot check as the *only* safety gate before writing attacker-influenced content to disk:

```
const absolutePath = await resolveWithin(repository.path, resolution.path)
if (absolutePath === null) { ... continue }
...
await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
``` [2](#0-1) 

Between the `resolveWithin` check and the `writeFile` call there is no re-verification that `absolutePath` still resolves inside the repository. Because `resolution.path` and `resolution.resolvedContent` both ultimately derive from repository-relative conflict paths gathered earlier in `buildConflictContext` (`app/src/lib/copilot-conflict-context.ts`, lines 390–407) — content that comes from a cloned/fetched repository the attacker controls — a crafted repository can place a symlink at a conflicted path (e.g. `foo` → `/Users/victim/.ssh`) after the containment check has already run once during context-building, and again swap it back before/around the final write. The design assumes "checked once, before, is enough" instead of re-validating the invariant immediately before the actual write (e.g., opening the destination with `O_NOFOLLOW`/`fs.realpath` re-check right at write time, mirroring how the original finding wants the `totalStaked ≥ totalFrozen` invariant checked both before *and* after `_updateTotalFrozen`).

The same one-shot pattern also underlies `buildConflictContext`, which resolves the path once with `resolveWithin`, then does a separate `stat()` and `readFile()` on the *original* string path rather than a locked/`realpath`-fixed handle [3](#0-2) , giving further windows where the on-disk target can be repointed between the safety check and the actual filesystem operation.

### Impact Explanation
If the containment check can be invalidated between validation and use, Desktop can be made to write AI-resolved (attacker-influenceable) file content to a path outside the cloned repository — e.g., overwriting files reachable via a symlink placed at a conflict path. Because the path is derived from data in a repository the attacker controls (conflict paths from a crafted merge/rebase), this falls squarely in scope: attacker-controlled repository content, code/file-write consequence outside the intended repo, no local/physical access or pre-existing malware required beyond enticing the user into a Copilot-assisted conflict resolution flow.

### Likelihood Explanation
Likelihood is moderate-to-low in practice because it requires winning a narrow timing window (symlink swap) during an operation that is otherwise gated by a single `resolveWithin` call, and the current implementation does correctly reject symlink escapes *at check time* (confirmed by `app/test/unit/path-test.ts` symlink tests, lines 65–101). The weakness is specifically the absence of a second, write-time confirmation of the invariant, which is exactly the gap the original report calls out (check only done once, not both before and after the state-changing operation).

### Recommendation
Re-validate the containment invariant immediately before (and ideally atomically with) the write, rather than relying on a single check performed earlier in the flow. Concretely: resolve and re-check the path a second time right before `writeFile` in `_applyCopilotConflictResolutions`, or use file-descriptor-based writes (open with `O_NOFOLLOW`, then `fstat`/`realpath` on the descriptor) so the path cannot be swapped out from under the check between validation and use — mirroring the recommendation from the original report to check the invariant both before and after the mutating operation.

### Proof of Concept
1. Attacker crafts a repository whose merge produces a conflicted file at `evil-link` (repository-relative).
2. At the moment `buildConflictContext`/first `resolveWithin` check runs, `evil-link` is a regular file inside the repo (passes containment check).
3. Before `_applyCopilotConflictResolutions` performs `writeFile(absolutePath, ...)`, the on-disk entry at that path is replaced by a symlink pointing outside the repository (e.g., via a background process influenced by repository content, or a race exploited through a slow Copilot turn that gives a wide window between context-build and apply).
4. `writeFile` follows the symlink and writes Copilot-resolved content outside the intended repository root, since no second containment check occurs at write time. [4](#0-3) [2](#0-1)

### Citations

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

**File:** app/src/lib/copilot-conflict-context.ts (L390-438)
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
```
