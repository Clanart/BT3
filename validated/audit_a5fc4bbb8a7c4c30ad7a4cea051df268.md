### Title
Path-containment check in `resolveWithin` uses a bare string-prefix comparison, allowing sibling-directory escape from attacker-controlled deep-link `filepath` - (File: `app/src/lib/path.ts`)

### Summary
GitHub Desktop's `resolveWithin`/`_resolveWithin` helper is the security boundary used to guarantee that a user- or attacker-supplied relative path stays inside a given root directory (repository working directory). The final containment check is a raw `String.prototype.startsWith` comparison between the resolved real path and the real root path, with no verification that a path separator follows the root. This is the same class of defect as the external report: a boundary/containment check that is subtly wrong at the edge, so a value that should be rejected (or in the DA case, accepted) slips through the guard.

### Finding Description
`_resolveWithin` computes the real, symlink-resolved root and target paths and then does: [1](#0-0) 

```
const resolved = resolve(normalizedRoot, normalizedRelative)
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
```

`startsWith` performs a raw character-prefix comparison with no separator boundary check. If `realRoot` is `/Users/victim/Documents/GitHub/Hello-World` and the attacker-influenced relative path resolves (after `../` traversal) to `/Users/victim/Documents/GitHub/Hello-World-Private/secret.txt`, the check incorrectly returns `true` because the string `".../Hello-World-Private/secret.txt"` literally starts with `".../Hello-World"` — even though `Hello-World-Private` is a completely different, sibling directory outside the intended root. The same flaw exists for the Windows/POSIX variants (`resolveWithinWin32`, `resolveWithinPosix`) since they share `_resolveWithin`.

This function is the sole containment guard used for two attacker-reachable code paths:
1. Deep-link `openrepo` action `filepath` parameter — parsed from an arbitrary URL by `parseAppURL` (`app/src/lib/parse-app-url.ts`, `filepath` extracted from the query string with no traversal sanitization), and consumed in the dispatcher: [2](#0-1) 
Only an `isAbsolute(filepath)` check is performed before calling `resolveWithin(repository.path, filepath)`; relative traversal segments like `../` are not rejected before being handed to the flawed containment check.
2. Copilot merge-conflict file resolution, where conflicted file paths (which can originate from a malicious/crafted repository state) are resolved via the same helper before being read from disk: [3](#0-2) 

### Impact Explanation
If exploitable, this allows a crafted deep link (`x-github-client://openrepo/<owner>/<repo>?filepath=../SiblingDir/secret`) or a crafted repository with an attacker-influenced conflicted path to cause Desktop to treat a file outside the intended repository root as "safely resolved," leading to `shell.showItemInFolder` revealing a file outside the repo, or the Copilot conflict-resolution feature reading file contents outside the working directory and sending them to the Copilot backend — an out-of-repo file read that the containment guard was specifically designed to prevent.

### Likelihood Explanation
Exploitation requires a sibling directory whose name happens to be a super-string prefix match relative to the resolved traversal target (e.g., `Hello-World` vs. `Hello-World-Private`), which is a real but not universal precondition — it depends on the victim's local directory layout (e.g., multiple repos cloned side-by-side under the same parent, a common Desktop workflow: `~/Documents/GitHub/<repo>` naming patterns often share prefixes such as `foo` and `foo-archive`, `foo-fork`, `foo.wiki`, etc.). This keeps likelihood moderate rather than trivial, but the underlying guard logic is unconditionally wrong regardless of directory layout — it is a genuine boundary defect, not a hypothetical.

### Recommendation
Fix `_resolveWithin` in `app/src/lib/path.ts` to require an exact match or a match followed immediately by the platform path separator:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
(using the separator appropriate to the `options` module, i.e. `Path.sep`/`options.sep`, consistent with how `isClonePathSensitive` in `app/src/lib/git/clone.ts` already correctly does `clonePath.startsWith(sensitive + Path.sep)`).

### Proof of Concept
1. Victim has two repositories cloned locally: `~/Documents/GitHub/Hello-World` and `~/Documents/GitHub/Hello-World-Private/secret.txt`.
2. Victim clicks an attacker-supplied link: `x-github-client://openrepo/octocat/Hello-World?filepath=..%2FHello-World-Private%2Fsecret.txt`.
3. `parseAppURL` extracts `filepath = "../Hello-World-Private/secret.txt"` with no traversal check (`app/src/lib/parse-app-url.ts`).
4. `openRepositoryFromUrl` in `dispatcher.ts` passes this to `resolveWithin(repository.path, filepath)` after only checking `isAbsolute(filepath)` (false, since it's relative).
5. Inside `_resolveWithin`, `resolved` becomes `~/Documents/GitHub/Hello-World-Private/secret.txt`, and `realResolved.startsWith(realRoot)` evaluates true because `realRoot = ~/Documents/GitHub/Hello-World` is a string-prefix of `realResolved`.
6. The function returns the resolved path instead of `null`, and `shell.showItemInFolder(resolved)` reveals the file outside the `Hello-World` repository.

Note: I could not run the application to dynamically confirm the deep-link flow end-to-end (no execution environment available), so this analysis is based on static code review of the cited files; the core `startsWith` boundary defect itself is directly verifiable in `app/src/lib/path.ts`.

### Citations

**File:** app/src/lib/path.ts (L66-71)
```typescript
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
