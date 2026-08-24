Confirmed: `filepath` from the `x-github-client://openrepo` deep link is passed through with no path-traversal filtering beyond an `isAbsolute` check [1](#0-0) , and is handed to `resolveWithin(repository.path, filepath)` in `openRepositoryFromUrl` before calling `shell.showItemInFolder(resolved)` [2](#0-1) .

### Title
Prefix-only directory containment check in `resolveWithin` allows deep-link `filepath` to escape the repository into sibling directories - (File: app/src/lib/path.ts)

### Summary
The utilization-math bug in the Sherlock report is really a "missing boundary in a containment check" bug: a value that should be capped/bounded by a threshold isn't, so the guard silently accepts values it shouldn't. The Desktop analog is structurally identical: `_resolveWithin()` is supposed to guarantee a resolved path stays *underneath* a root directory, but the final check is a raw string-prefix comparison with no path-separator boundary, so it accepts paths in **sibling** directories whose name happens to start with the root directory's name.

### Finding Description
`_resolveWithin` computes the final containment decision with:

```ts
return realResolved.startsWith(realRoot) ? resolved : null
``` [3](#0-2) 

`String.prototype.startsWith` has no notion of path segment boundaries. If `realRoot` is `/Users/victim/Documents/GitHub/my-repo` and a sibling directory `/Users/victim/Documents/GitHub/my-repo-backup` (or `my-repo2`, `my-repo.bak`, etc.) exists, then `realResolved = /Users/victim/Documents/GitHub/my-repo-backup/secrets.txt` satisfies `realResolved.startsWith(realRoot)` even though it is not inside `my-repo` at all — it's a completely different directory tree that merely shares a name prefix. The function returns the path as "safely resolved" instead of `null`.

This function is the sole containment guard used by `openRepositoryFromUrl` for the `filepath` query parameter of the `x-github-client://openrepo` protocol handler:

```ts
if (isAbsolute(filepath)) { ... return }
const resolved = await resolveWithin(repository.path, filepath)
if (resolved !== null) {
  shell.showItemInFolder(resolved)
}
``` [2](#0-1) 

The `filepath` value comes straight from the deep-link URL's querystring with no traversal filtering — only an "absolute path" rejection and, for `branch`, a character check; `filepath` gets no equivalent sanitization at all [1](#0-0) . A relative path like `../my-repo-backup/secrets.txt` passes the `isAbsolute` check and is handed unmodified to `resolveWithin`.

Existing guards do not stop this path because: (1) `isAbsolute(filepath)` only blocks absolute paths, not `..`-relative ones; (2) `_resolveWithin` does correctly reject plain `..` escapes when there's no sibling matching the prefix (as covered by its unit tests) [4](#0-3) , but the tests never exercise the "sibling directory with root name as a prefix" case, so the boundary-check gap goes uncaught; (3) the symlink-based traversal is separately guarded via `realpath` [5](#0-4) , but that guard is irrelevant here since no symlink is needed — plain `..` traversal into a real sibling folder is sufficient.

### Impact Explanation
An attacker who gets a victim to click a crafted `x-github-client://openrepo/OWNER/NAME?filepath=../SIBLING/secretfile` link can cause Desktop to call `shell.showItemInFolder()` on a file outside the intended repository, provided a sibling directory exists whose name is prefixed by the repository directory's name (a very common naming pattern for people who keep forks, backups, or old checkouts side-by-side, e.g. `myrepo`, `myrepo-old`, `myrepo.bak`, `myrepo-v2`). This discloses the existence/location of files outside the repo boundary that `resolveWithin` is explicitly meant to prevent, and is a break of the "unprivileged attacker via a link the user clicks results in file access outside the repo" impact class. The severity is bounded by what `shell.showItemInFolder` does (opens Finder/Explorer highlighting the file) rather than exfiltrating file contents directly, but the same `resolveWithin` primitive gates other more sensitive call sites (e.g. file reads inside `copilot-conflict-context.ts` for automated conflict resolution) [6](#0-5) , so any code path that trusts `resolveWithin`'s containment guarantee for read/write operations inherits this same escape.

### Likelihood Explanation
Exploitability depends on the victim already having a directory that shares the repository folder's name as a prefix, which is a realistic but not guaranteed precondition (common for developers who clone forks/variants side by side). No special privileges, malware, or credentials are required — only a single click on an attacker-supplied link, matching valid unprivileged Desktop threat scenarios.

### Recommendation
Fix `_resolveWithin` to check for an actual path-segment boundary rather than a raw string prefix, e.g.:

```ts
return realResolved === realRoot ||
  realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```

Add regression tests covering the sibling-directory-with-shared-prefix case (`root = /a/b/repo`, escape target `/a/b/repo-evil`) for both POSIX and Windows path variants.

### Proof of Concept
1. Victim has `~/Documents/GitHub/my-repo` (a Desktop-tracked repository) and, separately, `~/Documents/GitHub/my-repo-backup/secret.txt` (any unrelated directory whose name is prefixed by `my-repo`).
2. Attacker sends the victim a link: `x-github-client://openrepo/owner/my-repo?filepath=..%2Fmy-repo-backup%2Fsecret.txt`.
3. Desktop's `parseAppURL` parses this into `{ name: 'open-repository-from-url', filepath: '../my-repo-backup/secret.txt', ... }` [1](#0-0) .
4. `openRepositoryFromUrl` locates/opens `my-repo`, sees `filepath` is not absolute, and calls `resolveWithin(repository.path, filepath)` [2](#0-1) .
5. Inside `_resolveWithin`, `resolved` becomes `~/Documents/GitHub/my-repo-backup/secret.txt`; `realResolved.startsWith(realRoot)` evaluates true because `"...my-repo-backup/secret.txt"` starts with `"...my-repo"` as a raw string, even though it is a sibling directory [3](#0-2) .
6. The function returns the out-of-repo path as valid, and `shell.showItemInFolder(resolved)` opens the OS file manager pointed at `secret.txt`, outside the repository boundary the guard was meant to enforce.

### Citations

**File:** app/src/lib/parse-app-url.ts (L98-124)
```typescript
  if (actionName === 'openrepo') {
    const pr = getQueryStringValue(query, 'pr')
    const branch = getQueryStringValue(query, 'branch')
    const filepath = getQueryStringValue(query, 'filepath')

    if (pr != null) {
      if (!/^\d+$/.test(pr)) {
        return unknown
      }

      // we also expect the branch for a forked PR to be a given ref format
      if (branch != null && !/^pr\/\d+$/.test(branch)) {
        return unknown
      }
    }

    if (branch != null && testForInvalidChars(branch)) {
      return unknown
    }

    return {
      name: 'open-repository-from-url',
      url: parsedPath,
      branch,
      pr,
      filepath,
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

**File:** app/src/lib/path.ts (L64-71)
```typescript
  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
```

**File:** app/test/unit/path-test.ts (L47-50)
```typescript
    it('fails for paths outside of the root', async () => {
      assert((await resolveWithin(root, join('..'))) === null)
      assert((await resolveWithin(root, join('..', '..'))) === null)
    })
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
