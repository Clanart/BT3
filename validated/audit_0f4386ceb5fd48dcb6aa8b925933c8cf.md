### Title
`resolveWithin()` path-containment check uses an unanchored string-prefix comparison, allowing directory-traversal escape via `x-github-client://openrepo` deep-link `filepath` and Copilot conflict-resolution paths - ([File: app/src/lib/path.ts])

### Summary
The report's broken invariant is a "generic, unguarded operation that should exclude a specific case but doesn't" (any token, including `esToken`, could be withdrawn). The closest real analog in GitHub Desktop is the shared containment primitive `resolveWithin()`, which is meant to guarantee "the resolved path is inside `rootPath`, or nothing" but implements that guarantee with a bare `String.prototype.startsWith()` check that has no path-separator boundary, so it can be satisfied by paths that are merely string-prefixed by the root, not actually contained in it.

### Finding Description
`resolveWithin()` is the single trust boundary used across Desktop to keep git-derived or URL-derived relative paths inside a repository root: [1](#0-0) 

```
const resolved = resolve(normalizedRoot, normalizedRelative)
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
```

`startsWith(realRoot)` treats `realRoot` as a plain string prefix, not a directory boundary. If the user's repository directory name is itself a prefix of a sibling path (e.g. root `.../GitHub/app` and a sibling `.../GitHub/app-secrets`, or `.../GitHub/app` vs `.../GitHub/app2`, `app (1)`, `app-fork`, etc. — patterns Desktop itself produces when cloning multiple related repos into the same parent folder), then a resolved path that actually lives in the sibling directory will still pass the check, because the string `.../GitHub/app-secrets/id_rsa` starts with `.../GitHub/app`. No separator (`path.sep`) is appended to `realRoot` before the comparison, so `foo` and `foobar` are treated as "contained."

This is the same class of bug as the audited contract: a function meant to reject a specific excluded case (paths outside root / esToken) instead accepts it because the exclusion check is too weak.

`resolveWithin()` is exercised on two attacker-reachable inputs:
1. **Deep link `filepath` parameter.** `app/src/lib/parse-app-url.ts` parses `openrepo://...?filepath=...` URLs (lines 98-124) and passes the raw querystring value through untouched. `dispatcher.ts` then only rejects absolute paths and otherwise trusts `resolveWithin`: [2](#0-1) 
2. **Copilot conflict-resolution paths.** `buildConflictContext()` reads file content from `resolveWithin(workingDirectory, file.path)` results [3](#0-2) , and `app-store.ts` writes AI-resolved content back with the same primitive [4](#0-3) .

The existing unit tests for `resolveWithin` only cover `..`-only traversal and symlink-based escape to an unrelated directory [5](#0-4) ; they do not cover the sibling-prefix case, so the flaw is unexercised and unguarded.

### Impact Explanation
- Via the `filepath` deep-link path, a crafted `x-github-client://openrepo/...&filepath=../<sibling>` link that a user clicks causes `shell.showItemInFolder(resolved)` to reveal a file outside the intended repository, once the sibling-prefix condition holds [6](#0-5) .
- Via the Copilot conflict-resolution path, an attacker who can shape merge-conflict content (e.g., through a malicious branch/PR the victim merges) could get file content read from, or model-authored content written to, a path outside the working directory if the prefix condition is met, i.e. silent corruption of file content the user believes is scoped to their repo [4](#0-3) .

This matches the requested impact class: attacker-influenced link/deep-link and repository content leading to file read/write outside the repo.

### Likelihood Explanation
Exploitability is conditional, not universal: it requires the victim's repository directory name to be a string prefix of another reachable path (sibling directory, or a longer directory name sharing the same prefix) on their filesystem. This is a real-world occurrence given Desktop's own default clone-naming behavior (`repo`, `repo-1`, `repo (1)`, forks cloned side-by-side), but it is not guaranteed for every victim, so likelihood is Low–Medium rather than High. I was not able to fully confirm (within available tool budget) whether any additional caller-side check (e.g., filename sanitization prior to `resolveWithin`) further narrows this in the Copilot flow beyond what's shown above; this should be re-verified against the full `copilot-conflict-context.ts` file before treating it as fully confirmed for that code path.

### Recommendation
Change the containment check in `_resolveWithin()` (`app/src/lib/path.ts`) to compare against the root with a trailing separator, e.g.:
```
const rootWithSep = realRoot.endsWith(sep) ? realRoot : realRoot + sep
return (realResolved === realRoot || realResolved.startsWith(rootWithSep)) ? resolved : null
```
Add regression tests covering sibling-prefix paths (e.g. root `/tmp/app` vs candidate `/tmp/app-evil/secret`) for both POSIX and Windows path variants, mirroring the existing symlink-escape tests in `app/test/unit/path-test.ts`.

### Proof of Concept
1. Victim has two directories side by side: `~/Documents/GitHub/app` (the real, currently-open repository) and `~/Documents/GitHub/app-secrets` (any sibling whose name is prefixed by `app`), the latter containing a sensitive file `id_rsa`.
2. Attacker sends the victim a link: `x-github-client://openrepo/https://github.com/owner/app&filepath=../app-secrets/id_rsa`.
3. Victim clicks it. Desktop parses it via `parseAppURL` into an `IOpenRepositoryFromURLAction` with `filepath = "../app-secrets/id_rsa"` [7](#0-6) .
4. `openRepositoryFromUrl` confirms `filepath` is not absolute, then calls `resolveWithin(repository.path, filepath)` [8](#0-7) .
5. Inside `_resolveWithin`, `resolved` becomes `~/Documents/GitHub/app-secrets/id_rsa`; `realResolved.startsWith(realRoot)` evaluates true because `"...GitHub/app-secrets/id_rsa".startsWith("...GitHub/app")` is `true` despite `app-secrets` not being inside `app` [9](#0-8) .
6. `shell.showItemInFolder(resolved)` opens the OS file manager pointed at the file outside the repository, confirming the containment bypass.

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

**File:** app/test/unit/path-test.ts (L47-78)
```typescript
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
```

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
