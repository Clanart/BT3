### Title
Missing path-separator boundary in `resolveWithin`'s containment check allows escape to sibling directories - ([File: app/src/lib/path.ts])

### Summary
`_resolveWithin` in [1](#0-0)  validates that a resolved path stays inside a root directory using a plain string-prefix test (`realResolved.startsWith(realRoot)`) instead of checking for an exact match or a root-plus-separator prefix. This is the same class of bug as the ENS finding: `equals`/containment logic compares a length-relation (`>=`/`startsWith`) where an exact boundary check is required, so a "sibling" value that merely shares the same leading characters is incorrectly accepted as "contained."

### Finding Description
The helper is documented as guaranteeing the resolved path "resides at, or underneath" `rootPath` [2](#0-1) , but the actual check performed is:

```ts
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
``` [3](#0-2) 

`String.prototype.startsWith` performs a raw character-prefix comparison with no path-separator boundary. If `realRoot` is `/Users/victim/Documents/GitHub/repo` and, after symlink resolution, `realResolved` is `/Users/victim/Documents/GitHub/repo-secrets/config` (or any other directory that textually begins with the same characters as `realRoot`, e.g. `repo2`, `repository`, `repo.bak`), the check returns true even though that path is a completely different, sibling directory outside the intended root. This mirrors the ENS report's flaw: a length/prefix relation (`self.length >= offset + other.length`, `startsWith`) is used where an exact boundary (`==`, or prefix + separator) is required.

`resolveWithin` is the security gate used in two attacker-reachable call sites:
- `Dispatcher.openRepositoryFromUrl`, where `filepath` comes directly from an `x-github-client` deep link (`github-desktop://openRepo/...`) that is fully attacker-controlled, and `repository.path` is the root: [4](#0-3) 
- `buildConflictContext`, which resolves conflicted file paths (potentially influenced by a fetched/merged remote branch) against the repository's working directory: [5](#0-4) 

### Impact Explanation
Because the comparison is a raw string prefix rather than a segment-bounded prefix, an attacker who can influence a symlink target inside a cloned repository (a symlink is ordinary tracked content an attacker fully controls in a malicious repo) can cause `realpath(resolved)` to land in a directory whose name happens to share the root's prefix, and the guard will treat it as "inside" the repository. Depending on the caller:
- In `openRepositoryFromUrl`, this results in `shell.showItemInFolder` being invoked on a path outside the repository the user clicked to open — an escape of the "stay inside repository root" invariant the code explicitly tries to enforce (comment: "Prevented attempt to open path outside of the repository root") [6](#0-5) .
- In `buildConflictContext`, a similar escape would cause file content from outside the repository to be read and forwarded into the Copilot conflict-resolution prompt [7](#0-6) .

### Likelihood Explanation
The existing unit tests for `resolveWithin` only exercise the case where a symlink points fully outside the root (e.g., to `../..`), which is correctly rejected because the escaped path shares no textual prefix with the root at all [8](#0-7) . They do **not** cover the "prefix-but-not-boundary" case (e.g., root `.../repo` vs. resolved `.../repo-evil/...`), so this exact class of bypass is untested and unguarded. Exploitation still depends on a directory adjacent to the repository sharing a name-prefix with it (or the repository name itself being attacker-influenced during clone, which is a related but separate surface), which limits, but does not eliminate, real-world likelihood — this is a structural correctness bug in the boundary check regardless of how often the naming coincidence occurs in practice.

### Recommendation
Replace the raw prefix check with a boundary-aware comparison, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
This mirrors the fix in the ENS report (require an exact boundary/length match rather than a bare `>=`/prefix relation). Apply the analogous fix to `clone.ts`'s `isClonePathSensitive`, which has the same pattern (`clonePath.startsWith(sensitive + Path.sep)` is already correct there, but should be reviewed for consistency) [9](#0-8) .

### Proof of Concept
1. Victim has a repository cloned at `/Users/victim/Documents/GitHub/repo`.
2. A sibling directory `/Users/victim/Documents/GitHub/repo-secrets` exists (e.g., from an unrelated project) containing sensitive files.
3. Attacker crafts (or gets the victim to open, via a malicious repo containing a symlink, or via a deep link) a path such that after `resolve`/`realpath`, `realResolved` becomes `/Users/victim/Documents/GitHub/repo-secrets/token.txt`.
4. `realResolved.startsWith(realRoot)` evaluates `"/Users/victim/Documents/GitHub/repo-secrets/token.txt".startsWith("/Users/victim/Documents/GitHub/repo")` → `true`, so `_resolveWithin` returns the escaped path instead of `null`.
5. Depending on the call site, this path is passed to `shell.showItemInFolder` (dispatcher.ts) or `readFile` (copilot-conflict-context.ts), exposing content outside the intended repository root.

### Citations

**File:** app/src/lib/path.ts (L13-24)
```typescript
/**
 * Resolve one or more path sequences into an absolute path underneath
 * or at the given root path.
 *
 * The path segments are expected to be relative paths although
 * providing an absolute path is also supported. In the case of an
 * absolute path segment this method will essentially only verify
 * that the absolute path is equal to or deeper in the directory
 * tree than the root path.
 *
 * If the fully resolved path does not reside underneath the root path
 * this method will return null.
```

**File:** app/src/lib/path.ts (L66-71)
```typescript
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
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

**File:** app/src/lib/copilot-conflict-context.ts (L411-431)
```typescript
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

**File:** app/src/lib/git/clone.ts (L40-44)
```typescript
  for (const sensitive of sensitiveLocations) {
    if (clonePath === sensitive || clonePath.startsWith(sensitive + Path.sep)) {
      return true
    }
  }
```
