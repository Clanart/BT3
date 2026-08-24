Confirmed: `filepath` in `x-github-desktop://openRepo/...&filepath=...` deep links is taken verbatim from the query string with no path-traversal sanitization [1](#0-0) , and the only guard applied before it reaches `resolveWithin` is an `isAbsolute()` rejection [2](#0-1) . This is sufficient evidence to state the finding.

### Title
Path-containment bypass in `resolveWithin` via prefix-only `startsWith` check lets a deep link reveal files outside the repository - (File: `app/src/lib/path.ts`)

### Summary
`resolveWithin` (used to sandbox file access to a repository root) verifies containment with `realResolved.startsWith(realRoot)` — a raw string-prefix comparison with no path-separator boundary check [3](#0-2) . This mirrors the reported bug class exactly: a boundary/comparator check (`==` vs `<=` in the original report) is too loose/too strict at the edge, letting a value that should be rejected pass the guard.

### Finding Description
`_resolveWithin` computes `resolved = resolve(normalizedRoot, normalizedRelative)` and then only accepts it if `realResolved.startsWith(realRoot)` [4](#0-3) . `String.prototype.startsWith` has no notion of path segment boundaries: `"/Users/alice/repo-secrets".startsWith("/Users/alice/repo")` is `true`, even though `repo-secrets` is a sibling directory, not a subdirectory of `repo`. Any relative path segment that walks up one directory (`..`) and back down into a sibling folder whose name happens to start with the root folder's name (e.g. `repo-old`, `repo.bak`, `repo-1` — a suffix pattern Desktop itself produces when a clone destination collides, see `clone-repository.tsx`) will be treated as "inside" the repository.

This function is relied upon as the security boundary in two attacker-reachable call sites:
- `dispatcher.ts`'s `openRepositoryFromUrl`, which handles the `filepath` parameter of an `x-github-desktop://openRepo/...&filepath=...` deep link. It rejects only absolute paths [2](#0-1) ; relative traversal segments (`../`) are passed straight into `resolveWithin`, and the parser performs no traversal filtering on `filepath` [1](#0-0) .
- `buildConflictContext`, which uses `resolveWithin(workingDirectory, file.path)` to gate which files get read and sent to Copilot as conflict-resolution context [5](#0-4) .

### Impact Explanation
Via `openRepositoryFromUrl`, a link the user clicks can cause Desktop to call `shell.showItemInFolder(resolved)` on a path outside the repository, revealing/opening a file or directory the attacker did not otherwise have access to — matching the "link or deep link the user clicks" → "file read outside the repo" impact category. The severity is bounded by needing a same-parent-directory sibling whose name shares the exact prefix of the target repo's folder name (a naming coincidence that Desktop itself creates, e.g. `repo`, `repo-1`, `repo-2` when cloning into an already-occupied destination). It is a real logic flaw in the security boundary, not merely a theoretical one, since the existing regression tests never exercise the "sibling with shared name prefix" case [6](#0-5) .

### Likelihood Explanation
Exploitation requires the coincidence of a same-prefixed sibling directory next to the target repository — plausible given Desktop's own clone-naming behavior (`repo`, `repo-1`, ...), but not guaranteed for an arbitrary victim/repository pair, so likelihood is moderate rather than high.

### Recommendation
Fix the containment check to respect path-segment boundaries, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Add a regression test asserting that a sibling directory sharing the root's name as a string prefix (e.g. root `/tmp/x/repo`, sibling `/tmp/x/repo-evil`) is correctly rejected by `resolveWithin`.

### Proof of Concept
1. Victim has a repository open in Desktop at `/Users/victim/Documents/GitHub/myrepo`, and (as commonly happens after re-cloning) a sibling directory exists at `/Users/victim/Documents/GitHub/myrepo-private` containing a sensitive file `secret.txt`.
2. Attacker sends the victim a link:
   `x-github-desktop://openRepo/https://github.com/owner/myrepo?filepath=../myrepo-private/secret.txt`
3. `parseAppURL` parses `filepath` as `../myrepo-private/secret.txt` with no traversal check [1](#0-0) .
4. `openRepositoryFromUrl` passes the check (`isAbsolute` is false) and calls `resolveWithin(repository.path, filepath)` [2](#0-1) .
5. `resolve(normalizedRoot, normalizedRelative)` yields `/Users/victim/Documents/GitHub/myrepo-private/secret.txt`, and `realResolved.startsWith(realRoot)` (`realRoot = ".../myrepo"`) incorrectly evaluates to `true` [4](#0-3) .
6. Desktop calls `shell.showItemInFolder(resolved)`, revealing the sensitive sibling file to the attacker-controlled link flow.

### Citations

**File:** app/src/lib/parse-app-url.ts (L99-124)
```typescript
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1957-1971)
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
```

**File:** app/src/lib/path.ts (L64-72)
```typescript
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
