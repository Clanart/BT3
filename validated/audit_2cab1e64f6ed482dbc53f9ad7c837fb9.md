### Title
Path-prefix boundary check in `resolveWithin` allows escape into sibling directories via crafted deep-link `filepath` - ([File: app/src/lib/path.ts])

### Summary
`_resolveWithin` in `app/src/lib/path.ts` validates that a resolved path stays inside a root directory using `realResolved.startsWith(realRoot)`, without requiring a path-separator boundary after the prefix match. A resolved path in a sibling directory whose name has the repository's directory name as a string prefix (e.g. `myrepo-evil`) will incorrectly pass validation. This guard is used to gate `filepath` from the `x-github-client://openRepo/...?filepath=...` deep link handler in `dispatcher.ts`, so an attacker-controlled deep link/URL can point to files outside the intended repository root.

### Finding Description
`_resolveWithin` computes `realRoot` and `realResolved` via `realpath` and then does: [1](#0-0) 

The check `realResolved.startsWith(realRoot)` is a naive string-prefix test. It does not verify that the next character after the matched prefix is a path separator (or that `realResolved === realRoot`). Consequently, if `realRoot` is e.g. `/Users/victim/Documents/GitHub/myrepo` and an attacker can cause the resolved path to be `/Users/victim/Documents/GitHub/myrepo-secrets/passwords.txt` (a sibling directory whose name happens to share the root as a prefix), `startsWith` returns `true` even though `myrepo-secrets` is a completely different directory outside `myrepo`. The function will happily return this path as "resolved within root."

This guard function is the sole protection used by the `x-github-client://openRepo/...` deep-link handler for the `filepath` query parameter: [2](#0-1) 

The `filepath` value comes directly from an attacker-controlled URL that a user can be tricked into clicking (a classic "attacker controls a deep link the user clicks" primitive). The only other check performed before `resolveWithin` is `isAbsolute(filepath)`, which does nothing to stop this sibling-prefix bypass since the crafted value can still be expressed as a relative path (e.g. `../myrepo-secrets/passwords.txt` when the current repo directory is `myrepo`). If Desktop has cloned/opened repos with names that are prefixes of each other on disk (a very plausible real-world layout, e.g. `Documents/GitHub/foo` and `Documents/GitHub/foo-internal`), a link opened against the `foo` repository can be crafted to resolve to a file physically located under `foo-internal`, and the check will not reject it.

The same primitive is used by `copilot-conflict-context.ts` to gate which on-disk file contents get read and forwarded to the Copilot conflict-resolution flow: [3](#0-2) 
Here the "repository-relative" `file.path` values originate from git conflict metadata for the working tree, which is generally not attacker-influenced in the same direct way as a deep link, but it shares the identical unsafe boundary check.

Existing unit tests for `resolveWithin` do not cover the sibling-prefix case at all — they test `..`-traversal, null bytes, and symlink escapes, but never a target whose real path merely shares the same string prefix as the root without a separator: [4](#0-3) 
This confirms the boundary-check gap was not considered by the guard's authors — directly analogous to the audited liquidation code where the `afterRatio > beforeRatio` check "worked" for the common case but didn't account for the edge condition that let attackers slip past the intended bound.

### Impact Explanation
The `filepath` parameter is used with `shell.showItemInFolder(resolved)`, i.e. the OS file manager is instructed to reveal/select the file. This is a "read outside the repo" primitive at minimum: an attacker-crafted `x-github-client://openRepo/...` link can make Desktop reveal a path from a sibling repository/directory that the user did not intend to expose, disclosing the existence/location of unrelated local files or repositories to whatever surface consumes the reveal action. Given that `resolveWithin` is a shared security primitive (also relied upon for reading working-directory conflict content that is sent to Copilot for automated resolution), a bypass of its core invariant broadens the blast radius beyond the single call site audited here.

### Likelihood Explanation
Exploitation requires: (1) the user has (or can be induced to clone) two repositories on disk whose folder names share a prefix relationship (e.g. `repo` and `repo-2`, or `repo` and `repo.bak`) — a common and unremarkable directory-naming pattern — and (2) the user clicks an attacker-supplied `x-github-client://openRepo/<matching-repo-url>?filepath=<crafted-relative-path>` link. Both preconditions are plausible without any local/physical access, malware, or leaked credentials, matching the "attacker controls a deep link the user clicks" primitive explicitly listed as valid impact. The main uncertainty is how easily an attacker can guarantee the target sibling directory name in the victim's environment; this makes the practical exploitation somewhat opportunistic/environment-dependent rather than universally reliable, which is why likelihood should be assessed as low-to-moderate.

### Recommendation
Fix `_resolveWithin` to require an exact match or a trailing separator boundary, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Add regression tests specifically for the sibling-prefix case (e.g. root `/tmp/foo`, candidate resolving to `/tmp/foo-bar/secret`) to prevent recurrence, mirroring the existing `..`-traversal and symlink-escape tests.

### Proof of Concept
1. Suppose the victim has two directories: `~/Documents/GitHub/myrepo` (a GitHub Desktop repository matching remote `https://github.com/acme/myrepo`) and a sibling `~/Documents/GitHub/myrepo-private` containing sensitive files.
2. Attacker sends the victim a link: `x-github-client://openRepo/https://github.com/acme/myrepo?filepath=..%2Fmyrepo-private%2Fsecret.txt`.
3. `parseAppURL` extracts `filepath = "../myrepo-private/secret.txt"`; `isAbsolute` check passes (it's relative). [2](#0-1) 
4. `resolveWithin(repository.path, "../myrepo-private/secret.txt")` resolves to `~/Documents/GitHub/myrepo-private/secret.txt`. `realRoot` = `.../myrepo`, `realResolved` = `.../myrepo-private/secret.txt`. `realResolved.startsWith(realRoot)` evaluates `true` because `"myrepo-private/secret.txt".startsWith("myrepo")` is true as a raw string comparison, even though the boundary is not a path separator. [1](#0-0) 
5. The guard incorrectly returns the out-of-root path, and `shell.showItemInFolder` reveals `secret.txt` from the sibling `myrepo-private` directory — a file entirely outside the intended repository boundary.

### Citations

**File:** app/src/lib/path.ts (L66-72)
```typescript
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
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
