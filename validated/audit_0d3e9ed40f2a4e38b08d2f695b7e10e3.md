### Title
`resolveWithin()` path-containment check uses unanchored `String.startsWith`, allowing sibling-directory escape via crafted deep-link `filepath` - ([File: app/src/lib/path.ts])

### Summary
`resolveWithin()` / `_resolveWithin()` is the sole containment guard used to validate that an attacker-influenced relative path (e.g., the `filepath` query parameter of an `x-github-client://openRepo/...` deep link) stays inside a repository directory before it is used to reveal a file in the Finder/Explorer. The final containment test, `realResolved.startsWith(realRoot)`, does not check for a path separator after the prefix, so a resolved path in a *sibling* directory whose name happens to start with the repository directory's name (e.g. `repo` vs `repo-secrets`) incorrectly passes the check.

### Finding Description
The guard is defined as: [1](#0-0) 

`resolve(normalizedRoot, normalizedRelative)` will correctly collapse `..` segments, so a `filepath` such as `../repo-secrets/id_rsa` combined with `rootPath = /Users/victim/Documents/GitHub/repo` resolves to `/Users/victim/Documents/GitHub/repo-secrets/id_rsa`. The subsequent `realpath()`-normalized string comparison, `realResolved.startsWith(realRoot)`, evaluates to `true` because the string `"…/repo-secrets/id_rsa"` starts with the substring `"…/repo"` — even though `repo-secrets` is a completely different, sibling directory. No trailing path-separator is appended to `realRoot` before the comparison, so the containment invariant ("resolved path must be at or under root") is silently violated, exactly the same class of failure as the reported Solidity bug: the guard *looks* correct but the arithmetic/string comparison it relies on does not actually enforce what the comment promises.

This function is the only defense used before opening an attacker-influenced path derived from a GitHub Desktop deep link: [2](#0-1) 

The `filepath` action is only checked for being non-absolute (`isAbsolute(filepath)`), then passed straight into `resolveWithin(repository.path, filepath)`. If that returns non-null the app calls `shell.showItemInFolder(resolved)` on the attacker-crafted path.

### Impact Explanation
An attacker who controls a link the user clicks (`x-github-client://openRepo/<github-url>?branch=...&filepath=../sibling-dir-with-matching-prefix/target-file`) can cause GitHub Desktop to reveal/open a file located outside the intended repository, in any sibling directory whose name is prefixed by the repository directory's name. Because GitHub Desktop's default clone layout places all repositories under a common parent folder (e.g., `~/Documents/GitHub/<repo>`), and common naming conventions create prefix-sharing siblings (`<repo>`, `<repo>.wiki`, `<repo>-old`, `<repo>-backup`, worktree folders named `<repo>-feature`, etc.), this is a realistic escape from the intended sandboxing directory. This falls squarely within the accepted impact category ("a link or deep link the user clicks... result is... file read outside the repo").

### Likelihood Explanation
The attack requires only a single click on a deep link (`open-repository-from-url` handling is registered as a documented custom protocol handler, no local access, no malware, no admin rights). The attacker does need some knowledge/guess of a sibling folder name sharing a prefix with the repository's folder name, which is a real-world-plausible but not universal precondition — this keeps the likelihood at medium rather than certain, since exploitation depends on the target's specific directory layout. `sanitizeCloneName`/`isClonePathSensitive` (in `clone.ts`) were clearly hardened against a related class of bug (path traversal in the *clone destination*), but that hardening does not cover this separate `filepath` deep-link flow, which still relies solely on the flawed `startsWith` check.

### Recommendation
Fix the containment check in `_resolveWithin` in `app/src/lib/path.ts` to require an exact match or a path-separator boundary, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Add a regression test mirroring the existing `resolveWithin` symlink tests in `app/test/unit/path-test.ts`, using a sibling directory whose name is a superstring of the root directory's basename (e.g. root `foo`, sibling `foo-evil`) to ensure resolution is rejected.

### Proof of Concept
1. Attacker sets up (or targets a victim already having) two sibling directories under the user's default clone parent, e.g. `~/Documents/GitHub/desktop` (the victim's real clone) and `~/Documents/GitHub/desktop-secrets` (containing a sensitive file `token.txt`), or relies on GitHub Desktop's own worktree/wiki-clone naming to create such a sibling automatically.
2. Attacker sends the victim a link:
   `x-github-client://openRepo/https://github.com/desktop/desktop?filepath=..%2Fdesktop-secrets%2Ftoken.txt`
3. Victim clicks the link; `handleAppURL` → `parseAppURL` parses it into an `IOpenRepositoryFromURLAction` with `filepath = "../desktop-secrets/token.txt"`.
4. `dispatcher.ts` `openRepositoryFromUrl` calls `resolveWithin(repository.path, filepath)`.
5. `_resolveWithin` resolves to `~/Documents/GitHub/desktop-secrets/token.txt`, and `realResolved.startsWith(realRoot)` evaluates true because `"…/desktop-secrets/…".startsWith("…/desktop")`.
6. `shell.showItemInFolder(resolved)` opens/highlights `token.txt` outside the repository in Explorer/Finder — demonstrating the containment bypass. [3](#0-2) [4](#0-3)

### Citations

**File:** app/src/lib/path.ts (L36-72)
```typescript
async function _resolveWithin(
  rootPath: string,
  pathSegments: string[],
  options: {
    join: (...pathSegments: string[]) => string
    normalize: (p: string) => string
    resolve: (...pathSegments: string[]) => string
  } = Path
) {
  // An empty root path would let all relative
  // paths through.
  if (rootPath.length === 0) {
    return null
  }

  const { join, normalize, resolve } = options

  const normalizedRoot = normalize(rootPath)
  const normalizedRelative = normalize(join(...pathSegments))

  // Null bytes has no place in paths.
  if (
    normalizedRoot.indexOf('\0') !== -1 ||
    normalizedRelative.indexOf('\0') !== -1
  ) {
    return null
  }

  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
}
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1972)
```typescript
  private async openRepositoryFromUrl(action: IOpenRepositoryFromURLAction) {
    const { url, pr, branch, filepath } = action

    let repository: Repository | null

    if (pr !== null) {
      repository = await this.openPullRequestFromUrl(url, pr)
    } else if (branch !== null) {
      repository = await this.openBranchNameFromUrl(url, branch)
    } else {
      repository = await this.openOrCloneRepository(url)
    }

    if (repository === null) {
      return
    }

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
