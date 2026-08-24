## Title
Path-boundary check in `resolveWithin()` uses unanchored `startsWith`, allowing deep-link `filepath` to escape the intended repository root - (File: `app/src/lib/path.ts`)

### Summary
Same broken-invariant class as the oracle report: a "sanity check" that looks correct in the common case silently passes corrupted/out-of-range data in an edge case, and the caller trusts the "valid" result without further verification. Here the invariant `resolveWithin()` is supposed to enforce — *"the resolved path is at or below `rootPath`"* — is implemented with a bare string `startsWith(realRoot)` comparison with no path-separator boundary check, so a sibling directory whose name merely shares the root's name as a prefix (e.g. `repo` vs `repo-secret`) is incorrectly treated as "inside" the root.

### Finding Description
`_resolveWithin()` validates that a resolved path stays inside `rootPath` like this: [1](#0-0) 

```
  const resolved = resolve(normalizedRoot, normalizedRelative)
  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)
  return realResolved.startsWith(realRoot) ? resolved : null
```

`realResolved.startsWith(realRoot)` is true for any path whose textual prefix matches `realRoot`, regardless of whether the next character is a path separator. If `rootPath` is `/Users/alice/repo`, a relative segment such as `../repo-secret/file.txt` resolves (via `path.resolve`) to `/Users/alice/repo-secret/file.txt`. That string literally starts with `/Users/alice/repo`, so the guard reports it as "inside the root" even though it is a completely different, sibling directory.

This function is the *only* validation standing between attacker-controlled input and file-system access in at least two callers that consume untrusted, remote-originated data:

1. **Deep link ("Open in Desktop") `filepath` parameter** — fully attacker-controlled, parsed from a clicked link with only an `isAbsolute()` check (which does not block `../` traversal) before being handed to `resolveWithin`: [2](#0-1) 

The URL itself is parsed with no restriction on the `filepath` query value: [3](#0-2) 

2. **Copilot conflict-resolution context builder**, which reads and forwards file *contents*: [4](#0-3) 

The existing unit tests only cover the "traverse out and back into the exact same root" and symlink cases — they never exercise the sibling-directory-with-shared-prefix scenario, so the boundary defect is unverified: [5](#0-4) 

### Impact Explanation
For the deep-link path: an attacker who gets a user to click a crafted `x-github-client://openRepo/...?filepath=../<repoName>-something/<file>` link (or the `github-mac`/`github-windows` legacy scheme) can cause Desktop to call `shell.showItemInFolder()` on a path outside the intended repository whenever a sibling directory happens to share the repository folder's name as a prefix — a common occurrence for forks/clones named `repo`, `repo-backup`, `repo-old`, `repo.bak`, etc. This is a file-system boundary escape driven entirely by a link the user clicked, matching the "file read/reveal outside the repo via a link the user clicks" impact category.

For the Copilot conflict-context path, the consequence is more severe: file *content* outside the working directory could be read and forwarded into the AI conflict-resolution context if a conflict entry's path can be made to satisfy the same sibling-prefix condition, resulting in exfiltration of file contents rather than just path disclosure.

### Likelihood Explanation
Exploitability depends on a sibling directory with a matching name prefix existing next to the target repository — a condition attackers cannot always guarantee, but a common one in practice (users routinely keep `repo`, `repo2`, `repo-old`, `repo-backup` side by side). The `filepath` deep-link vector requires no prior authentication or local access — just clicking a link — and the app's own `isAbsolute()` guard does not stop the `../` relative traversal needed to trigger the flaw, so the existing guard gives a false sense of safety, mirroring the audit's observation that "the resulting price will have a lower denominator and will be corrupted" while still passing the check.

### Recommendation
Fix `_resolveWithin()` in `app/src/lib/path.ts` to require an exact match or a match followed by the platform path separator:

```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + sep)
  ? resolved
  : null
```
(using the separator appropriate to the `options` module, i.e. `Path.sep`/`Path.posix.sep`/`Path.win32.sep`). Add regression tests for sibling directories that share a name prefix with the root (e.g. root `.../repo` vs. resolved `.../repo-secret/file`).

### Proof of Concept
1. Set up two sibling directories: `~/Documents/repo` (a tracked GitHub Desktop repository) and `~/Documents/repo-secret/passwords.txt` (any file outside the repo).
2. Craft a deep link: `x-github-client://openRepo/https://github.com/owner/repo?filepath=..%2Frepo-secret%2Fpasswords.txt`.
3. Have the victim click the link (e.g. embedded on a web page or in an issue/PR body they open with "Open in Desktop").
4. `parseAppURL` accepts the `filepath` value unchanged (no `../` validation) → `dispatchURLAction` → `openRepositoryFromUrl` → `isAbsolute('../repo-secret/passwords.txt')` is `false`, so the guard is bypassed → `resolveWithin('~/Documents/repo', '../repo-secret/passwords.txt')` resolves to `~/Documents/repo-secret/passwords.txt`, and because `realResolved.startsWith(realRoot)` is true (string `"...repo-secret..."` starts with `"...repo"`), the function returns the path as "valid".
5. `shell.showItemInFolder(resolved)` opens Explorer/Finder revealing `passwords.txt`, a file outside the intended repository, solely as a result of a link the user clicked.

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

**File:** app/src/lib/copilot-conflict-context.ts (L390-408)
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
