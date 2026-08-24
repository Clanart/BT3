Confirmed: `openrepo://` deep links are parsed by `parseAppURL` (`app/src/lib/parse-app-url.ts:98-125`) into an `IOpenRepositoryFromURLAction` carrying a fully attacker-controlled `filepath` query parameter, which flows to `Dispatcher.openRepositoryFromUrl` (`app/src/ui/dispatcher/dispatcher.ts:1940-1972`) and ultimately into `resolveWithin`.

### Title
Deep-link `filepath` can escape the cloned repository via a naive prefix check in `resolveWithin` - (File: `app/src/lib/path.ts`)

### Summary
`resolveWithin()` is the app's core sandbox primitive for containing file paths within a repository root, used for deep-link file reveals, Copilot conflict-resolution writes, and Copilot conflict-context reads. Its containment check uses `String.prototype.startsWith` on the resolved real path without verifying a trailing path separator, so a sibling directory whose name merely starts with the root directory's name is treated as "inside" the root.

### Finding Description
`_resolveWithin` in `app/src/lib/path.ts` computes: [1](#0-0) 
```
const resolved = resolve(normalizedRoot, normalizedRelative)
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
```
This is the classic "prefix without separator" containment bug: if `realRoot` is `/Users/victim/Documents/GitHub/my-repo`, then a resolved path of `/Users/victim/Documents/GitHub/my-repo-secrets/token.json` also satisfies `startsWith(realRoot)`, even though it lives in a completely different, sibling directory. The function is documented to guarantee the returned path "resides underneath" root, but the check does not enforce that boundary.

This function is reachable from an attacker-controlled deep link. `openrepo://` URLs are parsed by `parseAppURL`, which extracts an unrestricted `filepath` query parameter into `IOpenRepositoryFromURLAction`: [2](#0-1) 

`Dispatcher.openRepositoryFromUrl` handles this action. It only rejects `filepath` if it is an absolute path, then resolves the (relative) path with `resolveWithin(repository.path, filepath)` and passes the result straight to `shell.showItemInFolder`: [3](#0-2) 

Because `resolveWithin`'s check tolerates any path that merely shares the root directory's name as a string prefix, an attacker can craft a relative `filepath` such as `../my-repo-secrets/token.json` (where `my-repo` is the known/predictable clone directory name embedded in the same `openrepo://` URL). `Path.resolve` legitimately walks up one directory and back down into the sibling `my-repo-secrets`, `realpath` succeeds if the target exists, and the buggy `startsWith` check accepts it as "within root" — bypassing the sandbox entirely.

### Impact Explanation
This lets a link the victim clicks (an `x-github-desktop://openrepo/...&filepath=...` deep link) cause Desktop to reveal (via `shell.showItemInFolder`) an arbitrary file outside the cloned repository, provided a sibling directory sharing the repo's name as a prefix exists on disk (a common pattern for users who keep related repos/configs together, e.g. `project` and `project-secrets`, `app` and `app-config`). This is a "file read/reveal outside the repo" primitive driven by user-clicked link content — squarely in the reported valid-impact category. The same broken primitive (`resolveWithin`) also backs Copilot conflict-resolution file writes (`app/src/lib/stores/app-store.ts:7233-7258`) and conflict-context reads (`app/src/lib/copilot-conflict-context.ts:390-407`), so the blast radius of fixing the underlying check extends beyond the deep-link path, though those paths are gated by additional git-status matching that reduces attacker control there.

### Likelihood Explanation
Requires the victim to click a maliciously crafted `openrepo://` deep link and a sibling directory with the right name prefix to already exist locally — a real but non-trivial precondition (not guaranteed for every target), which is why this is a boundary-check defect rather than a universally exploitable one. It requires no local/physical access, no admin rights, and no prior malware — only the natural act of clicking a link, matching the report's valid attacker model.

### Recommendation
Fix `_resolveWithin` in `app/src/lib/path.ts` to require an exact match or a path-separator boundary:
```
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
(using the platform-appropriate `sep`/`join.sep` for the `options` passed in, since POSIX/Win32 variants are supported). Add regression tests mirroring `app/test/unit/path-test.ts` for sibling-directory prefix collisions (e.g., root `/tmp/foo` vs. target `/tmp/foobar/secret`).

### Proof of Concept
1. Victim has cloned a repo to `~/Documents/GitHub/my-repo` and separately keeps `~/Documents/GitHub/my-repo-secrets/token.json`.
2. Attacker sends: `x-github-desktop://openrepo/https://github.com/owner/my-repo?filepath=../my-repo-secrets/token.json`
3. Victim clicks the link; Desktop registers/opens `my-repo` (cloning or matching existing repo per `doesRepositoryMatchUrl`, `app/src/ui/dispatcher/dispatcher.ts:1920-1938`) and calls `openRepositoryFromUrl`.
4. `filepath` is relative, so the `isAbsolute` guard passes; `resolveWithin(repoPath, '../my-repo-secrets/token.json')` resolves to a path outside `repoPath` but the buggy `startsWith` check treats it as contained.
5. `shell.showItemInFolder(resolved)` reveals `token.json` from outside the repository in Explorer/Finder, disclosing its existence/location to the attacker's crafted flow (and, depending on OS file-manager behavior, the file itself is now visible/selected to the user, and any subsequent Desktop feature reusing `resolveWithin` on similarly attacker-influenced paths could instead write to it).

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
