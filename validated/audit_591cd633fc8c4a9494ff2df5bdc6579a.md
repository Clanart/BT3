### Title
Path-containment check in `resolveWithin()` uses a bare `startsWith` without a separator boundary, allowing escape into sibling directories via a deep-link `filepath` - (File: app/src/lib/path.ts)

### Summary
`resolveWithin()` is Desktop's core "is this path inside the repo?" invariant, analogous to the Solidity `checkInvariant()` modifier in the report: it's meant to guarantee a resolved path can never fall outside a trusted root. The final containment check uses plain string prefix matching without verifying a path-separator boundary, so a sibling directory that merely shares the root directory's name as a prefix will incorrectly pass the check.

### Finding Description
The containment guarantee is implemented as: [1](#0-0) 

```
const resolved = resolve(normalizedRoot, normalizedRelative)
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
```

`String.prototype.startsWith` performs a raw character-prefix comparison. If `realRoot` is `/Users/victim/Documents/GitHub/myrepo`, then `/Users/victim/Documents/GitHub/myrepo-secrets/token.txt` also "starts with" `realRoot` even though it lives in a completely different, sibling directory (`myrepo-secrets`, not `myrepo`). There is no check that the character immediately following `realRoot` in `realResolved` is a path separator (or that `realResolved === realRoot`). Contrast this with the deliberate fix already applied elsewhere in the same codebase for an analogous check, `isClonePathSensitive()`, which correctly appends the separator: `clonePath.startsWith(sensitive + Path.sep)` [2](#0-1) . `resolveWithin` lacks this boundary check, so the "balance is bounded" style invariant it's supposed to enforce silently fails to hold for sibling-prefixed paths.

The existing unit tests for `resolveWithin` cover `..`-traversal and symlink escapes, but never test the sibling-directory-prefix case, so the gap is unexercised: [3](#0-2) .

### Impact Explanation
`resolveWithin` is used to gate a real filesystem action reachable from a deep link that the user clicks, matching the required "attacker controls a … deep link the user clicks" primitive. In `dispatcher.ts`, `openRepositoryFromUrl()` takes the `filepath` query parameter straight from a `x-github-client://openRepo/...` URL and passes it, together with the local repository path, into `resolveWithin`, then calls `shell.showItemInFolder()` on whatever path is returned: [4](#0-3) . The `filepath` itself comes unmodified from `parseAppURL`'s query-string parsing with only an absolute-path check performed upstream: [5](#0-4) , [6](#0-5) .

If a victim has (or the attacker can otherwise cause the creation of, e.g. via a prior clone of a similarly-named repo) a sibling directory whose name is prefixed by the target repository's directory name (e.g. `.../GitHub/myrepo` and `.../GitHub/myrepo-secrets`), a crafted deep link with `filepath=../myrepo-secrets/some-file` will resolve to a path outside the intended repository, pass the broken containment check, and be handed to the OS shell to reveal/open — i.e., a "file read outside the repo" primitive driven purely by a link click, matching the valid-impact class in the task.

The same broken primitive (`resolveWithin`) also gates file reads for AI conflict-resolution context building from merge-conflict file paths [7](#0-6) , meaning content from a fetched/merged branch could similarly be redirected to sibling paths that were never meant to be read for that feature, widening the blast radius beyond the deep-link vector.

### Likelihood Explanation
Exploitation requires: (1) the victim clicking a crafted `x-github-client://openRepo/...` deep link (a normal, low-friction Desktop feature, no local access or malware needed) and (2) the existence of a filesystem sibling directory whose name is a prefix-extension of the repository directory name. Condition (2) is the main constraining factor — it depends on the victim's local directory layout rather than being fully attacker-controlled — which lowers likelihood relative to a fully remote, precondition-free bug, but it is not implausible (e.g., users cloning `repo` and `repo-private`, `repo` and `repo.bak`, `repo` and `repo2`, etc., is common). The bug is a straightforward, deterministic logic defect (missing separator check) rather than a probabilistic race, so once the precondition is met, the bypass is 100% reliable.

### Recommendation
In `_resolveWithin()` (`app/src/lib/path.ts`), replace the bare prefix check with a boundary-aware comparison, e.g.:
```
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
(using the same `join`/`resolve` module passed in, so behavior matches for `Path.posix`/`Path.win32` variants too). Add a regression test asserting that a sibling directory sharing the root's name as a prefix (e.g. root `foo`, target `foo-secrets`) is rejected by `resolveWithin`, mirroring the existing `..`-traversal and symlink tests in `app/test/unit/path-test.ts`.

### Proof of Concept
1. Locally have two sibling directories: `~/Documents/GitHub/myrepo` (open in Desktop, tracked) and `~/Documents/GitHub/myrepo-secrets/token.txt` (any file outside the tracked repo).
2. Attacker sends the victim a link:
   `x-github-client://openRepo/https://github.com/owner/myrepo?filepath=..%2Fmyrepo-secrets%2Ftoken.txt`
3. Desktop's URL handler parses this into `{ url, filepath: '../myrepo-secrets/token.txt' }` via `parseAppURL` [5](#0-4) .
4. `openRepositoryFromUrl` resolves/opens `myrepo`, then calls `resolveWithin(repository.path, '../myrepo-secrets/token.txt')` [8](#0-7) .
5. Inside `_resolveWithin`, `resolved` normalizes to `~/Documents/GitHub/myrepo-secrets/token.txt`, and `realResolved.startsWith(realRoot)` is `true` because the string `.../myrepo-secrets/token.txt` starts with `.../myrepo` — despite `token.txt` residing entirely outside the `myrepo` directory tree [1](#0-0) .
6. `shell.showItemInFolder(resolved)` is invoked on the out-of-repo file, silently defeating the "Prevented attempt to open path outside of the repository root" guard message that would otherwise be logged [9](#0-8) .

### Citations

**File:** app/src/lib/path.ts (L66-71)
```typescript
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
```

**File:** app/src/lib/git/clone.ts (L40-44)
```typescript
  for (const sensitive of sensitiveLocations) {
    if (clonePath === sensitive || clonePath.startsWith(sensitive + Path.sep)) {
      return true
    }
  }
```

**File:** app/test/unit/path-test.ts (L44-50)
```typescript
  describe('resolveWithin', () => {
    const root = process.cwd()

    it('fails for paths outside of the root', async () => {
      assert((await resolveWithin(root, join('..'))) === null)
      assert((await resolveWithin(root, join('..', '..'))) === null)
    })
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
