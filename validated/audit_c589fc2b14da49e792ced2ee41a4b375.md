### Title
Path-boundary check in `resolveWithin` uses unanchored `startsWith`, allowing sibling-directory escape when resolving attacker-controlled conflict file paths - (File: `app/src/lib/path.ts`)

### Summary
`resolveWithin`/`resolveWithinPosix`/`resolveWithinWin32` are the app's core guard against path traversal when resolving repository-relative paths supplied by external/untrusted data (e.g., merge-conflict file lists). The guard resolves the target path, then checks containment with a raw string `startsWith` comparison instead of verifying a path-segment boundary, so a target that lives in a *sibling* directory whose name has the root directory's name as a prefix will incorrectly be treated as "inside" the root.

### Finding Description
The core logic in `_resolveWithin`: [1](#0-0) 
resolves the joined/normalized path and then does:
```js
return realResolved.startsWith(realRoot) ? resolved : null
```
This is a classic unanchored-prefix bug: `startsWith` does not require a path separator after `realRoot`. If the repository root is `/Users/alice/project` and there exists a sibling path on disk such as `/Users/alice/project-secrets` (or `/Users/alice/projectX`), then a resolved path like `/Users/alice/project-secrets/config.json` passes the check even though it is **not** inside the repository — it only shares a string prefix.

This is structurally the same class of bug as the Bond Protocol report: a security-relevant boundary/invariant computation (there: rounding direction for decay timing; here: containment boundary for path resolution) is implemented slightly wrong, silently weakening a guard that downstream code trusts completely.

The guard is consumed directly with attacker/repository-controlled input in `buildConflictContext`, where `file.path` comes from merge-conflict metadata (attacker-controllable via a crafted repository/branch that produces a merge conflict) and is resolved against the working directory before being read and sent to the Copilot conflict-resolution flow: [2](#0-1) 

The existing unit tests for `resolveWithin` only cover `..`-traversal and symlink-traversal cases, not the sibling-prefix case, so the flaw is not caught by the current test suite: [3](#0-2) 

### Impact Explanation
If an attacker can get a victim to fetch/merge a branch or repository that produces a merge conflict whose file path resolves (once joined+normalized) to a sibling directory sharing the repository's directory name as a prefix, `resolveWithin` will incorrectly return a path outside the repository as "safe." The caller then reads that file's contents and includes them in the conflict-resolution context sent to Copilot — an out-of-repo file read. Because `resolveWithin` is the app's generic sandboxing primitive (also referenced from `app-store.ts` and `dispatcher.ts`), any current or future caller that trusts it for write operations would have an equivalent out-of-repo file write risk. This matches the "read/write outside the repo, attacker controls a cloned/fetched repository" impact category.

### Likelihood Explanation
Exploitation requires a specific, but realistically achievable, precondition: a sibling directory next to the repository whose name is prefixed by the repository directory's name (e.g., users who clone multiple related repos like `project` and `project-fork`, or backup directories like `project.bak` — note `.` also is not a separator so this also matches), combined with the victim triggering a merge that leaves attacker-chosen conflicting paths. This is not guaranteed on every system, so likelihood is moderate rather than high, but it requires no local/admin access, no malware, and no unnatural user steps beyond a normal merge/fetch workflow — it fits squarely within the valid-impact criteria.

### Recommendation
Change the containment check in `_resolveWithin` to require a proper path-segment boundary, e.g.:
```js
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
(using the platform-appropriate separator from `options`), and add a regression test using a sibling directory whose name is prefixed by the root directory's name to prevent recurrence.

### Proof of Concept
1. Create `/tmp/project` (repository root) and `/tmp/project-secret/config.json` (sibling directory with a prefixed name).
2. Call `resolveWithin('/tmp/project', '../project-secret/config.json')`.
3. `resolve()` produces `/tmp/project-secret/config.json`; `realpath` resolves both root and target; the check `realResolved.startsWith(realRoot)` evaluates `'/tmp/project-secret/config.json'.startsWith('/tmp/project')` → `true`, so the function returns the out-of-root path instead of `null`.
4. In `buildConflictContext`, an attacker-crafted repository state that surfaces a conflicting `file.path` of `../project-secret/config.json` would cause `resolveWithin(workingDirectory, file.path)` to succeed and the file content of `/tmp/project-secret/config.json` to be read and forwarded as conflict context, despite being outside the working directory. [4](#0-3)

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

**File:** app/src/lib/copilot-conflict-context.ts (L390-431)
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
```

**File:** app/test/unit/path-test.ts (L44-101)
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

      it('succeeds for paths that use a symlink to traverse outside of the root and then back again', async () => {
        const tempDir = await mkdtemp(join(tmpdir(), 'path-test'))
        const symlinkName = 'dangerzone'
        const symlinkPath = join(tempDir, symlinkName)

        try {
          await symlink(resolve(tempDir, '..', '..'), symlinkPath)
          const throughSymlinkPath = join(
            symlinkName,
            basename(resolve(tempDir, '..')),
            basename(tempDir)
          )
          assert.equal(
            await resolveWithin(tempDir, throughSymlinkPath),
            resolve(tempDir, throughSymlinkPath)
          )
        } finally {
          await unlink(symlinkPath)
          await rmdir(tempDir)
        }
      })
    }
```
