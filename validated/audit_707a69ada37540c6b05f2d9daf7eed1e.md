### Title
Path-containment check in `resolveWithin` allows escape to sibling directories that share a name prefix - ([File: app/src/lib/path.ts])

### Summary
`_resolveWithin` (used by `resolveWithin`, `resolveWithinPosix`, `resolveWithinWin32`) decides whether a resolved path is "inside" a repository root using a raw string-prefix test: [1](#0-0) 

`realResolved.startsWith(realRoot)` has no trailing path-separator check. Any resolved path whose string representation begins with the *characters* of `realRoot` — even if it is actually a sibling directory (e.g. `repo` vs `repo-secrets` or `repo2`) — passes the containment test. This is the same class of bug as the report's core issue: a boundary/inclusion check that is evaluated with an incorrect comparison and therefore lets values outside the intended set be treated as inside it.

### Finding Description
`resolveWithin(rootPath, ...pathSegments)` is the app's general-purpose guard against directory-traversal, and is invoked from an attacker-influenced entry point: the custom URL protocol handler.

Deep links of the form `x-github-client://openRepo/<url>?filepath=<path>` (also `github-mac://`/`github-windows://`) are parsed by `parseAppURL`, which only rejects absolute-looking `filepath` values further downstream: [2](#0-1) 

`dispatcher.openRepositoryFromUrl` then takes the `filepath` query parameter — fully attacker controlled via a link the user clicks — checks only that it is not an absolute path, and calls `resolveWithin`: [3](#0-2) 

The `isAbsolute(filepath)` check does nothing to stop `../`-relative traversal; it only blocks paths that already start with `/` or a drive letter. The traversal protection is therefore delegated entirely to `resolveWithin`'s prefix check, which is broken for any target directory that happens to share a name prefix with the repository directory (a very common situation for cloned repos sitting next to forks, backups, or "-old"/"-secrets"/"2" suffixed copies in the same parent folder, which GitHub Desktop itself creates when a name collision occurs during clone).

The same primitive is reused for AI conflict-resolution file reads: [4](#0-3) 

Existing unit tests only cover symlink escapes and simple `..`/`../..` traversal, not the sibling-prefix case, so the gap is unexercised: [5](#0-4) 

### Impact Explanation
When the containment check is bypassed, `openRepositoryFromUrl` calls `shell.showItemInFolder(resolved)` on a path outside the repository the link claims to target — an unprompted file-system read/reveal triggered purely by the user clicking a link, matching the in-scope impact "file... read outside the repo." In the Copilot conflict-context path, a crafted conflicted file path in a fetched/merged repository could cause the app to `readFile` and forward the contents of a sibling directory's file (outside the working repo) into the AI conflict-resolution context, an out-of-repo read/exfiltration via a cloned/fetched repository object.

### Likelihood Explanation
Exploitation requires: (1) the victim has (or is induced to create, e.g. by cloning a maliciously-named repo which Desktop will suffix like `name-1`/`name2` on collision) a directory adjacent to the target repository whose name has the target repository's directory name as a string prefix, and (2) the victim clicks a crafted `x-github-client://openRepo/...&filepath=../sibling/secret` link. This is a real, no-privilege, no-local-access primitive (link click) but depends on a somewhat specific directory-naming coincidence, so likelihood is moderate rather than trivial.

### Recommendation
Fix the containment comparison in `_resolveWithin` to require a path-separator boundary, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
```
applied with the correct `sep` for the `options` module in use (`Path.sep`, `Path.posix.sep`, or `Path.win32.sep`), and add a regression test for the sibling-prefix case (`repo` vs `repo2`/`repo-evil`) mirroring the existing symlink-escape tests.

### Proof of Concept
1. Victim has `~/Documents/GitHub/myrepo` (open in Desktop) and a sibling `~/Documents/GitHub/myrepo-secrets/secret.txt`.
2. Attacker sends: `x-github-client://openRepo/https://github.com/owner/myrepo?filepath=..%2Fmyrepo-secrets%2Fsecret.txt`
3. Victim clicks the link. `parseAppURL` accepts it (`filepath` has no invalid chars); `openRepositoryFromUrl` sees `isAbsolute('../myrepo-secrets/secret.txt') === false`, so it proceeds to `resolveWithin(repository.path, filepath)`.
4. Inside `_resolveWithin`, `resolved` becomes `~/Documents/GitHub/myrepo-secrets/secret.txt`; `realRoot` = `~/Documents/GitHub/myrepo`. Since `realResolved.startsWith(realRoot)` is true (the string `"myrepo-secrets"` starts with `"myrepo"`), the function returns the path as "safe."
5. `shell.showItemInFolder(resolved)` reveals/opens the sibling secret file, even though it is outside `myrepo`.

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

**File:** app/src/lib/parse-app-url.ts (L98-125)
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

**File:** app/test/unit/path-test.ts (L44-58)
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
```
