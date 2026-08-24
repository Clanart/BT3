## Title
Path-prefix boundary check in `resolveWithin()` allows escaping the repository root when a sibling directory name shares a prefix - (File: `app/src/lib/path.ts`)

### Summary
`resolveWithin()` (and its `resolveWithinPosix`/`resolveWithinWin32` variants) is the central guard used across GitHub Desktop to confine attacker-influenced, repository-relative paths (Copilot conflict-resolution file paths, `x-github-client://openRepo` deep-link `filepath` values, etc.) to the repository's working directory. The final containment check is a raw string `startsWith()` comparison between `realResolved` and `realRoot` with no path-separator boundary check, which is the same class of boundary-condition defect as the reported bug: a comparison operator/condition that is correct in the common case but wrong exactly at the boundary, allowing an off-by-a-character escape.

### Finding Description
The containment check is: [1](#0-0) 

`realResolved.startsWith(realRoot)` treats `realRoot` as a plain string prefix instead of as a path component boundary. If `realRoot` is `/Users/victim/Documents/GitHub/repo` and the resolved+realpath'd target is `/Users/victim/Documents/GitHub/repo-secrets/config`, the `startsWith` check returns `true` even though `repo-secrets` is a completely different, sibling directory that is not "at or underneath" `repo` — because the string `/Users/victim/Documents/GitHub/repo-secrets/...` literally begins with the characters `/Users/victim/Documents/GitHub/repo`. The function's own doc comment claims the result is "guaranteed to reside at, or underneath" `rootPath`, but the implementation does not enforce that guarantee at the boundary. [2](#0-1) 

The lexical `resolve()` step (before `realpath`) always starts from `normalizedRoot`, so under normal (non-symlink) conditions the resolved candidate is naturally confined to `rootPath` or an ancestor reached via `..`. However, `resolveWithin` is explicitly designed to also accept absolute path segments ("In the case of an absolute path segment this method will essentially only verify that the absolute path is equal to or deeper in the directory tree than the root path") — and it is this absolute-path acceptance path, combined with the unguarded `startsWith`, that removes the safety margin: an attacker-controlled absolute path pointing at `rootPath + "-something"` (a sibling that happens to share the root path as a string prefix) passes the check.

Concretely, the primary consumers pass attacker-controlled relative paths, not absolute ones, and current call-sites reject absolute segments explicitly (e.g. the deep-link handler checks `isAbsolute(filepath)` first: [3](#0-2) ). But `resolveWithin` is a shared, general-purpose security primitive (also used for Copilot conflict file resolution: [4](#0-3) ) whose contract explicitly supports absolute inputs and whose test suite does not include a sibling-directory-name test case: [5](#0-4) . Any current or future caller that forwards an attacker-controlled path without first rejecting absolute paths (or a path containing a drive-relative/UNC form on Windows) inherits this boundary flaw silently, exactly like the original report's `getPrice()` — a boundary function used by multiple call-sites (`mint()` variants) that individually assumed correct behavior at the edge.

### Impact Explanation
If a caller relies on `resolveWithin`'s documented guarantee ("resolved path is guaranteed to reside at, or underneath this path") without independently re-validating containment or rejecting absolute segments, an attacker who controls the path value (e.g., a GitHub API object field, a crafted repository file, or a future deep-link/IPC parameter) can cause Desktop to read, reveal, or operate on a file outside the intended repository directory — i.e., file read/disclosure outside the repo, matching the "file read/write outside the repo" impact class explicitly called out as valid for this task.

### Likelihood Explanation
Likelihood is currently low-to-moderate: the known call-sites (`dispatcher.ts` deep link handler, Copilot conflict context builder) either reject absolute paths up front or only ever pass relative, repository-tracked file paths, so no fully attacker-triggerable end-to-end path was confirmed with the available tools. The defect is nonetheless real and latent in the shared primitive itself, and the existing regression test suite does not cover the sibling-prefix boundary case, so a regression or a new caller that trusts the function's documented contract (or passes an absolute, attacker-influenced path) would silently reintroduce a traversal bug, consistent with the reported bug class (a boundary check that "does not work properly in this scenario").

### Recommendation
Replace the raw prefix comparison with a boundary-aware check, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Add regression tests for the sibling-directory case (e.g. root `/a/b/repo` vs. resolved `/a/b/repo-evil`) for both POSIX and Windows variants, mirroring the pattern already used in `clone.ts`'s `isClonePathSensitive`, which correctly checks `clonePath === sensitive || clonePath.startsWith(sensitive + Path.sep)`. [6](#0-5) 

### Proof of Concept
Not independently executed (no filesystem/terminal access in this environment). Based on static analysis of `app/src/lib/path.ts:66-71`, the following would demonstrate the flaw if `resolveWithin` were invoked with an absolute path segment:

```ts
// root: /tmp/victim/repo
// pathSegments: ['/tmp/victim/repo-secret/config.json']  (absolute)
const result = await resolveWithin('/tmp/victim/repo', '/tmp/victim/repo-secret/config.json')
// Expected: null (repo-secret is a sibling, not a subdirectory)
// Actual (per code path): resolved is returned because
//   '/tmp/victim/repo-secret/config.json'.startsWith('/tmp/victim/repo') === true
```

This mirrors the existing test pattern in `app/test/unit/path-test.ts` (lines 44-63) but with a sibling directory whose name is a superstring of the root's basename — a case not present in the current test suite. [5](#0-4)

### Citations

**File:** app/src/lib/path.ts (L13-28)
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
 *
 * @param rootPath     The path to the root path. The resolved path
 *                     is guaranteed to reside at, or underneath this
 *                     path.
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

**File:** app/src/lib/git/clone.ts (L40-44)
```typescript
  for (const sensitive of sensitiveLocations) {
    if (clonePath === sensitive || clonePath.startsWith(sensitive + Path.sep)) {
      return true
    }
  }
```
