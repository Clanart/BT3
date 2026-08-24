### Title
Path-boundary check in `resolveWithin` uses unanchored `startsWith`, allowing traversal into sibling directories - (File: `app/src/lib/path.ts`)

### Summary
The reported Move contract bug is a boundary-comparison flaw: an assertion checks `balance > amount` instead of `balance >= amount`, so the *equal* boundary case is wrongly rejected. The same class of bug — a boundary check that fails to correctly delimit "inside" vs "outside" a range — exists in GitHub Desktop's path-confinement helper `resolveWithin`, except here the flaw goes the other way: the check incorrectly *accepts* paths that are outside the intended boundary because it only checks a string prefix without verifying a path-separator boundary.

### Finding Description
`_resolveWithin` in `app/src/lib/path.ts` is meant to guarantee that a resolved path is "at, or underneath" a given root path: [1](#0-0) 

The final containment check is:
```ts
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
``` [2](#0-1) 

`String.prototype.startsWith` performs a raw character-prefix comparison, not a path-segment comparison. It does not require the character immediately following `realRoot` inside `realResolved` to be a path separator (or the string to end there). Consequently, if the resolved real path is a *sibling* directory whose name happens to share the root's name as a prefix (e.g. root `/Users/victim/Documents/GitHub/repo` and a sibling `/Users/victim/Documents/GitHub/repo-evil`), the check `"/Users/.../repo-evil/secret".startsWith("/Users/.../repo")` evaluates to `true`, and the function returns the out-of-root path as "resolved" instead of `null`.

This is the exact broken-invariant shape as the reported Move bug: a comparison operator/semantics that doesn't correctly treat the boundary, except instead of rejecting a valid equal case, it *accepts* an invalid case that shares a prefix. The existing null-byte check and `realpath` symlink resolution (added specifically to stop traversal, per the tests in `app/test/unit/path-test.ts`) do nothing to stop this, because they operate before this final string-prefix comparison and a sibling directory is a completely legitimate, symlink-free path.

### Impact Explanation
`resolveWithin` is used by `dispatcher.ts` to sanitize the `filepath` parameter of the `x-github-client://openRepo/...` deep link before revealing a file in the OS file manager: [3](#0-2) 

The `filepath` is attacker-controlled (comes from a deep link a user clicks) and is checked only for being non-absolute before being passed to `resolveWithin(repository.path, filepath)`. If `repository.path` is `/Users/victim/Documents/GitHub/repo` and the attacker can get the user's environment to contain (or craft the relative path so that `realpath` resolves into) a sibling path like `/Users/victim/Documents/GitHub/repo-secrets/...`, the boundary check would incorrectly treat that sibling as "within root" and call `shell.showItemInFolder` on it — revealing/opening a file outside the intended repository. The same primitive is reused in `app/src/lib/copilot-conflict-context.ts` to constrain file reads while assembling AI conflict-resolution context, and any other place where a repository-relative path derived from repository content (a "sensitive location" the desktop app tries to protect, per `app/src/lib/git/clone.ts`'s `isClonePathSensitive`) is validated with this helper.

### Likelihood Explanation
Exploitation requires an attacker-controlled relative path (deep-link filepath, or a path derived from cloned/fetched repo content) plus a locally-existing sibling directory whose name is a superstring of the repository directory's basename. This is a real but narrower precondition than a pure directory-traversal (`../`) bug, since it depends on the coincidental/attacker-influenced existence of a same-prefix sibling folder rather than working unconditionally. It is nonetheless a genuine broken invariant in a function explicitly documented and tested as a security boundary (`resolveWithin` docstring: "the resolved path is guaranteed to reside at, or underneath this path").

### Recommendation
Fix the boundary check to require a path-separator (or exact match) after the shared prefix, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
This mirrors the report's fix pattern of correcting the comparison so that the boundary condition (root itself, or root + separator) is the actual inclusion criterion rather than a raw substring/prefix test.

### Proof of Concept
1. Create `/tmp/repo` and `/tmp/repo-evil/secret.txt`.
2. Call `resolveWithin('/tmp/repo', '../repo-evil/secret.txt')`.
3. `normalizedRelative` resolves via `join`/`normalize`/`resolve` to `/tmp/repo-evil/secret.txt`; `realResolved` = `/tmp/repo-evil/secret.txt`.
4. `realResolved.startsWith('/tmp/repo')` is `true` (because `"repo-evil"` starts with `"repo"`), so the function returns `/tmp/repo-evil/secret.txt` instead of `null`, even though that path is not underneath `/tmp/repo`.
5. In the deep-link flow, an attacker link `x-github-client://openRepo/<url>?filepath=..%2Frepo-evil%2Fsecret.txt` reaching `dispatcher.ts`'s `openRepositoryFromUrl` would cause `shell.showItemInFolder` to reveal the out-of-root file. [4](#0-3)

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
