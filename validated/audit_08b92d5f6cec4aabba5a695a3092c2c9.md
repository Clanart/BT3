### Title
Directory-boundary check in `resolveWithin()` uses unanchored `startsWith`, allowing sibling-directory escape from an attacker-controlled deep-link `filepath` - ([File: app/src/lib/path.ts])

### Summary
The bug-class in the report is a broken invariant caused by an incorrect boundary condition (misplaced parentheses making a guard silently pass invalid input). The Desktop analog is in `_resolveWithin()` in [1](#0-0)  which validates that a resolved path stays inside a root directory using a plain string `startsWith()` comparison with no trailing path-separator anchor. This is the exact same "invariant looks correct but has an off-by-boundary flaw" pattern as the reported fee formula.

### Finding Description
`_resolveWithin` computes `resolved = resolve(normalizedRoot, normalizedRelative)` and then validates containment with:
```
return realResolved.startsWith(realRoot) ? resolved : null
``` [2](#0-1) 

`String.prototype.startsWith` performs a raw character-prefix comparison, not a path-segment comparison. Any sibling directory whose name is a superstring of the root directory's basename will incorrectly satisfy the check, e.g. root `/Users/victim/Documents/GitHub/repo` and resolved path `/Users/victim/Documents/GitHub/repo-secrets/token.txt` — `"...GitHub/repo-secrets/token.txt".startsWith("...GitHub/repo")` evaluates to `true`, even though `repo-secrets` is a completely separate directory. This can be reached with a purely relative traversal (`../repo-secrets/token.txt`) — no absolute path or symlink is required, and the existing null-byte and `realpath` checks do not catch it because the resulting path is a real, ordinary path, not a symlink escape.

This function is called from `openRepositoryFromUrl` in the dispatcher when handling the `x-github-client://openRepo/...` deep link, using the attacker-controlled `filepath` query parameter:
```
if (isAbsolute(filepath)) { ... return }
const resolved = await resolveWithin(repository.path, filepath)
if (resolved !== null) { shell.showItemInFolder(resolved) }
``` [3](#0-2) 

The `filepath` value comes straight from `parseAppURL`'s query-string parsing with no traversal sanitization (only `branch` is checked against `testForInvalidChars`; `filepath` is passed through unmodified) [4](#0-3) . The `isAbsolute(filepath)` guard blocks only fully-qualified paths, not `../` relative traversal, so the flawed `startsWith` boundary check is the only thing standing between the attacker input and files outside the repository.

### Impact Explanation
An attacker who gets a victim to click a `x-github-client://openRepo/<repo-url>?filepath=../<sibling-dir-prefix-match>/<file>` deep link can cause Desktop to resolve and reveal (via `shell.showItemInFolder`) a file location outside the intended repository, as long as a sibling folder sharing the repo's name as a prefix exists on disk (a very common situation, since users routinely clone multiple repos with related names — e.g., `repo`/`repo-backup`, `desktop`/`desktop-beta` — into the same parent "GitHub" folder). This is exactly the class of "unprivileged, remote-link-triggered path/data escape" that is in scope: attacker controls a clickable deep link, and the broken invariant lets a value escape validation the same way the fee-calculation invariant silently broke in the original report.

### Likelihood Explanation
Medium. It requires: (1) the victim clicking a crafted `x-github-client://` link (standard Desktop deep-link flow, no unusual steps), and (2) a sibling directory existing whose name is a superstring of the repository directory name — a common but not guaranteed real-world condition. The same flawed primitive (`_resolveWithin`) is reused by other callers (`app-store.ts`, `copilot-conflict-context.ts`); I was not able to fully audit those additional call sites within the available investigation to determine if they expose a stronger (write) primitive, so likelihood/impact could be understated.

### Recommendation
Fix the boundary check to compare path segments, not raw strings, e.g.:
```
const rel = Path.relative(realRoot, realResolved)
return rel === '' || (!rel.startsWith('..') && !Path.isAbsolute(rel))
```
or explicitly require `realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)`.

### Proof of Concept
1. Victim has cloned two repositories into the same parent folder: `~/Documents/GitHub/repo` and `~/Documents/GitHub/repo-private` (the latter containing a sensitive file `secret.txt`).
2. Attacker sends the victim a link:
   `x-github-client://openRepo/https://github.com/owner/repo?filepath=..%2Frepo-private%2Fsecret.txt`
3. `parseAppURL` extracts `filepath = "../repo-private/secret.txt"` unmodified [4](#0-3) .
4. `isAbsolute(filepath)` is `false`, so the guard in `dispatcher.ts` passes it through to `resolveWithin(repository.path, filepath)` [5](#0-4) .
5. Inside `_resolveWithin`, `resolved` becomes `~/Documents/GitHub/repo-private/secret.txt`, and `realResolved.startsWith(realRoot)` (`realRoot = "~/Documents/GitHub/repo"`) evaluates `true` because of the unanchored prefix match [2](#0-1) .
6. Desktop calls `shell.showItemInFolder(resolved)`, revealing/opening the location of `secret.txt`, a file entirely outside the `repo` repository, from a link click alone.

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
