## Analysis

The external report's core theme—duplicated/inconsistent validation logic that silently drifts and creates a security gap—maps to a real, distinct bug in the Desktop protocol-handler code path: `app/src/lib/path.ts`'s `_resolveWithin` helper implements the "must stay within root" check with a boundary-unsafe `String.prototype.startsWith` comparison, and this single flawed primitive is the sole guard used by the `x-github-client://openRepo` deep-link handler.

### Title
Path-traversal via boundary-unsafe prefix check in `resolveWithin` lets a malicious deep link reveal files outside the cloned repository - (File: app/src/lib/path.ts)

### Summary
`Dispatcher.openRepositoryFromUrl` handles the `x-github-client://openRepo` custom protocol (parsed by `parse-app-url.ts` into an `IOpenRepositoryFromURLAction` with an attacker-controlled `filepath` field) and calls `resolveWithin(repository.path, filepath)` to confine the requested file to the repository directory before calling `shell.showItemInFolder(resolved)`. [1](#0-0) 

The containment check performed by `_resolveWithin` compares the resolved real path to the repository's real root using a plain string prefix test with no path-separator boundary: [2](#0-1) 

### Finding Description
`_resolveWithin` computes `resolved = resolve(normalizedRoot, normalizedRelative)` and then validates it with `realResolved.startsWith(realRoot)`. Because `startsWith` only checks a literal string prefix, a sibling directory whose name happens to begin with the same characters as the repository's directory name will incorrectly pass the check — e.g. root `/Users/foo/repo` and resolved path `/Users/foo/repo-secret/file` both satisfy `startsWith`, even though `repo-secret` is a completely different, sibling folder.

An attacker can trigger this by crafting `filepath` in the deep link as a relative path containing `..` segments that walk up one directory and back down into a same-prefixed sibling, e.g. `filepath=../repo-secret/some/file`. `Path.resolve(normalizedRoot, '../repo-secret/some/file')` yields `/Users/foo/repo-secret/some/file`, which still passes the buggy `startsWith(realRoot)` guard because that string literally starts with `/Users/foo/repo`.

The only other gate before this check is `isAbsolute(filepath)`, which does nothing to stop relative traversal segments: [3](#0-2) 

The `filepath` value itself is not otherwise sanitized before being handed to `resolveWithin`, so the only defense against escaping the repository directory is this single, flawed comparison — matching the report's duplicate-code lesson: relying on one under-specified check (instead of a well-defined invariant like requiring a trailing separator or exact match) creates a silent hole that is easy to miss during review.

### Impact Explanation
If a user's system has (or an attacker can predict/create) a directory sibling to a cloned repository whose name shares the repository name as a prefix (a very plausible scenario — e.g., `myproject` vs. `myproject-backup`, `myproject.bak`, `myproject-old`, or an attacker-planted directory of that name), a malicious deep link can cause Desktop to call `shell.showItemInFolder` on a file located in that sibling directory, outside the intended repository root. This is a file-disclosure/path-traversal primitive (reveals the existence and location of files outside the repo, and opens the OS file explorer at that path) driven entirely by a link the user clicks — matching the "attacker controls a link or deep link the user clicks... result is file read outside the repo" impact category.

### Likelihood Explanation
Likelihood depends on the presence of a same-prefixed sibling directory next to the target repository, which is a common naming pattern for backups (`repo-old`, `repo.bak`, `repo2`) or could be planted by an attacker in advance (e.g., via a prior unrelated interaction, or if the app's default clone location has predictable/creatable siblings). The attacker only needs the victim to click a single `x-github-client://openRepo?...&filepath=...` link — no other privileges or local access required.

### Recommendation
Fix the boundary check in `_resolveWithin` (`app/src/lib/path.ts`) to require an exact match or a proper separator boundary, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Apply the same fix to `resolveWithinPosix`/`resolveWithinWin32` paths (which share `_resolveWithin`), and add a regression test asserting that a sibling directory sharing the root's name as a prefix (e.g. root `/tmp/repo`, target `/tmp/repo-evil/file`) is rejected.

### Proof of Concept
1. Attacker prepares (or relies on an existing) sibling directory next to the victim's cloned repo, e.g. victim has `/Users/victim/Documents/myapp` cloned, and a directory `/Users/victim/Documents/myapp-secrets/token.txt` exists (backup folder, old clone, or attacker-influenced path).
2. Attacker sends the victim a link: `x-github-client://openRepo/https://github.com/org/myapp?filepath=..%2Fmyapp-secrets%2Ftoken.txt`.
3. Desktop parses the URL via `parse-app-url.ts`, producing an `IOpenRepositoryFromURLAction` with `filepath = "../myapp-secrets/token.txt"`.
4. `Dispatcher.openRepositoryFromUrl` locates/opens the `myapp` repository, then calls `resolveWithin(repository.path, "../myapp-secrets/token.txt")`. [4](#0-3) 
5. Inside `_resolveWithin`, `resolve(normalizedRoot, "../myapp-secrets/token.txt")` yields `/Users/victim/Documents/myapp-secrets/token.txt`, which passes the flawed `startsWith(realRoot)` check because it textually begins with `/Users/victim/Documents/myapp`. [5](#0-4) 
6. `shell.showItemInFolder(resolved)` reveals the out-of-repo file `token.txt` to the user/OS file browser, confirming the traversal outside the intended repository boundary.

### Citations

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
