I found a solid analog: the same class of bug as the Solidity report — an implicit containment invariant that silently breaks due to a naive check — exists in GitHub Desktop's path-containment guard.

### Title
Path-containment bypass in `resolveWithin` via naive `String.prototype.startsWith` prefix check allows file read/write outside the repository - (File: `app/src/lib/path.ts`)

### Summary
`resolveWithin` (and its POSIX/Win32 variants) is the sole guard used to make sure a repository-relative path supplied from data derived from the working tree/git status (e.g. a conflicted file's path) cannot escape the intended root directory before the app reads or writes it. The containment check is implemented as a plain string prefix comparison, `realResolved.startsWith(realRoot)`, with no path-separator boundary check.

### Finding Description [1](#0-0) 

```
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)

return realResolved.startsWith(realRoot) ? resolved : null
```

Just like the Trident bug where `feeGrowthBelow + feeGrowthAbove ≤ feeGrowthGlobal` was assumed but not enforced by the tick-crossing update logic, here the code assumes `startsWith(root)` implies "is inside root", but that's only true if `root` is followed by a path separator (or is an exact match). A sibling directory whose name simply *starts with* the same characters as the root directory name breaks the invariant: e.g. root `/Users/victim/Documents/GitHub/myrepo` and resolved path `/Users/victim/Documents/GitHub/myrepo-secrets/config` both satisfy `startsWith`, yet the second path is a completely different directory outside the intended repo.

This helper is the security boundary for two attacker-reachable, repo-content-driven paths:
- `resolveWithin(workingDirectory, file.path)` when building the Copilot conflict-resolution context, where `file.path` comes from the set of conflicted files in the working tree: [2](#0-1) 
- `resolveWithin(repository.path, resolution.path)` when writing Copilot's resolved content back to disk: [3](#0-2) 

In the write path in particular, once `resolveWithin` returns a non-null path, the code proceeds straight to `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` with no further containment re-check.

### Impact Explanation
If the resolved absolute path lands in a sibling directory that merely shares a name prefix with the repository directory (a common real-world situation — think `myrepo` next to `myrepo-old`, `myrepo.bak`, `myrepo2`, or a differently-cased/synced folder on case-insensitive filesystems), the guard incorrectly treats it as "inside the repo":
- **Read path**: file content from outside the repository could be read into memory and (depending on flow) included in the context sent to the Copilot API, i.e. cross-boundary information disclosure.
- **Write path**: `_applyCopilotConflictResolutions` will `writeFile` model-controlled or attacker-influenced content to a path outside the repository the user thought they were operating on — a from-repo primitive that can silently corrupt or plant a file elsewhere on disk.

### Likelihood Explanation
Exploitation requires attacker-influenced repository-relative path strings for conflicted files (reachable from a crafted merge/rebase/cherry-pick scenario against a hostile branch/remote) **and** the existence of a sibling directory whose name is a prefix-extension of the repo's directory name on the victim's machine. This second condition narrows real-world likelihood, but such naming collisions are not contrived (backup folders, differently versioned clones, `repo-fork`, etc.), and the check gives a false sense of security since it is documented/tested as a path-traversal guard yet the missing separator check is exactly the well-known `startsWith` containment pitfall — the same bug class as `path.ts`'s own test suite exercises for symlinks but never tests for this prefix case. Existing tests validate `..`-traversal and symlink escapes, but do not cover the sibling-prefix scenario: [4](#0-3) 

### Recommendation
Fix the containment check in `_resolveWithin` to require a directory-boundary match, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Add a regression test asserting that a sibling directory sharing a name prefix (e.g. root `.../myrepo` vs. target `.../myrepo-evil/secret`) is rejected.

### Proof of Concept
1. Suppose the repository lives at `/Users/victim/Documents/GitHub/myrepo`, and a sibling directory `/Users/victim/Documents/GitHub/myrepo-secrets/config` exists on disk (containing sensitive data, or simply a location the attacker wants written to).
2. During a merge/rebase/cherry-pick against an attacker-controlled branch, craft the working tree so a "conflicted file" entry resolves (via `path.join`/`path.resolve` against the repo root) to `../myrepo-secrets/config` — normalized/resolved this becomes `/Users/victim/Documents/GitHub/myrepo-secrets/config`.
3. `resolveWithin(workingDirectory, file.path)` calls `realpath` on both the root and resolved path, then checks `realResolved.startsWith(realRoot)`. Since `/Users/victim/Documents/GitHub/myrepo-secrets/config`.startsWith(`/Users/victim/Documents/GitHub/myrepo`) is `true`, the function returns the resolved path instead of `null`, bypassing the intended containment check [5](#0-4) .
4. Downstream, `buildConflictContext` reads that out-of-repo file into the Copilot conflict-resolution prompt [6](#0-5) , or `_applyCopilotConflictResolutions` writes attacker/model-influenced content to that out-of-repo path via `writeFile` [3](#0-2) .

### Citations

**File:** app/src/lib/path.ts (L64-71)
```typescript
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

**File:** app/src/lib/copilot-conflict-context.ts (L429-438)
```typescript
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

**File:** app/test/unit/path-test.ts (L44-63)
```typescript
  describe('resolveWithin', () => {
    const root = process.cwd()

    it('fails for paths outside of the root', async () => {
      assert((await resolveWithin(root, join('..'))) === null)
      assert((await resolveWithin(root, join('..', '..'))) === null)
    })

    it('succeeds for paths that traverse out, and then back into, the root', async () => {
      assert.equal(await resolveWithin(root, join('..', basename(root))), root)
    })

    it('fails for paths containing null bytes', async () => {
      assert((await resolveWithin(root, 'foo\0bar')) === null)
    })

    it('succeeds for absolute relative paths as long as they stay within the root', async () => {
      const parent = resolve(root, '..')
      assert.equal(await resolveWithin(parent, root), root)
    })
```
