### Title
`resolveWithin` boundary check uses naive prefix `startsWith` without separator, allowing sibling-directory escape - ([File: app/src/lib/path.ts])

### Summary
Just like the zkasm bug where `bytecodeLength < jumpDst` fails to reject the exact boundary value `dst == bytecodeLength`, GitHub Desktop's `_resolveWithin` helper validates that a resolved path stays inside a root directory using a **string-prefix** check that doesn't verify the boundary is a real path separator. This lets a resolved path that lands in a *sibling* directory (whose name merely starts with the same characters as the root) pass the "is-within-root" check.

### Finding Description
`_resolveWithin` computes the real, symlink-resolved root and target paths and then does: [1](#0-0) 

```
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)

return realResolved.startsWith(realRoot) ? resolved : null
```

This is a classic "boundary is not the exact edge" flaw: `"/Users/victim/Documents/GitHub/MyRepoSecrets/file.txt".startsWith("/Users/victim/Documents/GitHub/MyRepo")` evaluates to `true`, even though `MyRepoSecrets` is a completely different, sibling directory — not a path "within" `MyRepo`. The function's own documentation promises the resolved path "is guaranteed to reside at, or underneath" the root [2](#0-1) , but the implementation does not enforce that the character immediately following the root in `realResolved` is a path separator (or that the strings are exactly equal) — exactly the same class of missing-boundary mistake as the original `bytecodeLength < jumpDst` vs `dst < bytecodeLength` bug, where the equality/edge case is not excluded.

This helper is relied upon as a security backstop for attacker-influenced path segments: it is called from `Dispatcher.openRepositoryFromUrl` to validate a `filepath` argument taken directly from a deep-link URL before calling `shell.showItemInFolder`: [3](#0-2) 

```
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

`resolveWithin` is also used in `app/src/lib/stores/app-store.ts` and `app/src/lib/copilot-conflict-context.ts` as the general-purpose "stay inside this directory" guard for other attacker-influenceable inputs. Because the check accepts values only sharing a string prefix (not a true subdirectory relationship), any code path where `filepath` (or another root's descendant path) can be crafted with `..` traversal that ultimately resolves to a sibling directory sharing the root's name as a prefix (e.g., `../MyRepo-backup/…`, `../MyRepoSecrets/…`) will incorrectly be treated as "inside" the repository and passed on to filesystem-revealing operations like `shell.showItemInFolder`.

### Impact Explanation
An attacker who controls a `x-github-client://openRepo` style deep link (per the Valid Impact criteria: "a link or deep link the user clicks") can supply a `filepath` value that traverses out of the intended repository directory into an adjacent directory whose name happens to share the repository directory's name as a prefix. `resolveWithin`'s broken invariant means Desktop will treat this out-of-tree path as validated and reveal/act on it (e.g., `shell.showItemInFolder`), silently defeating the "confine operations to the repository root" guarantee documented for this function. Since this same helper is the general containment primitive reused elsewhere in the codebase, any future or existing caller that trusts its return value as proof of containment inherits the same escape.

### Likelihood Explanation
Exploitation requires the attacker to know (or guess) that the victim's clone directory name is a prefix of another directory name on disk (e.g., `MyRepo` vs `MyRepo-old`, `MyRepo.bak`, `MyRepository`) — a scenario that is common in practice because developers frequently keep multiple related clones or backup copies side-by-side with prefix-based naming conventions. No local access, admin rights, or prior malware is needed; the only requirement is convincing the user to click a crafted deep link, which matches the "unprompted" but standard Desktop deep-link flow already exercised by `openRepositoryFromUrl`.

### Recommendation
Change the containment check to require an exact match or that the boundary character is a path separator, e.g.:
```
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
This mirrors the report's fix pattern of tightening a boundary comparison (`dst < bytecodeLength` instead of `bytecodeLength < jumpDst`) to explicitly exclude the ambiguous edge case.

### Proof of Concept
1. Victim has two directories: `/Users/victim/Documents/GitHub/MyRepo` (a Desktop-managed clone) and `/Users/victim/Documents/GitHub/MyRepoSecrets` (unrelated sensitive folder).
2. Attacker crafts a deep link such as `x-github-client://openRepo/https://github.com/owner/MyRepo?filepath=../MyRepoSecrets/passwords.txt` and gets the victim to click it.
3. `openRepositoryFromUrl` in `app/src/ui/dispatcher/dispatcher.ts` calls `resolveWithin(repository.path, filepath)` with `repository.path = "/Users/victim/Documents/GitHub/MyRepo"`.
4. `_resolveWithin` computes `resolved = "/Users/victim/Documents/GitHub/MyRepoSecrets/passwords.txt"`; `realResolved.startsWith(realRoot)` returns `true` because the string `"MyRepoSecrets"` starts with `"MyRepo"`.
5. `resolveWithin` incorrectly returns the resolved path instead of `null`, and `shell.showItemInFolder(resolved)` reveals/opens the sensitive sibling file, defeating the intended containment guarantee. [1](#0-0) [4](#0-3)

### Citations

**File:** app/src/lib/path.ts (L23-28)
```typescript
 * If the fully resolved path does not reside underneath the root path
 * this method will return null.
 *
 * @param rootPath     The path to the root path. The resolved path
 *                     is guaranteed to reside at, or underneath this
 *                     path.
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
