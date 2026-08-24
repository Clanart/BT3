## Title
Path traversal via prefix-only containment check in `resolveWithin` allows deep-link `filepath` to escape the repository directory - (File: `app/src/lib/path.ts`)

## Summary
The `_resolveWithin` helper that gates the `x-github-client://openRepo` deep-link's `filepath` parameter uses a naive `String.prototype.startsWith` comparison to decide whether a resolved path is "inside" the repository root. Because the check does not verify a path-separator boundary after the root, any sibling directory whose name begins with the repository's directory name (e.g. `reponame-old`, `reponame2`, `reponame-backup`) is incorrectly treated as being inside `reponame`, letting `../<repoFolder>-old/...` traversal segments pass the check.

## Finding Description
The deep-link handler `openRepositoryFromUrl` in `app/src/ui/dispatcher/dispatcher.ts` only rejects absolute `filepath` values: [1](#0-0) 

Since `isAbsolute()` does not reject relative traversal sequences, a value such as `../reponame-old/secret` passes this guard and is handed to `resolveWithin(repository.path, filepath)`.

`resolveWithin` delegates to `_resolveWithin`, whose containment check is: [2](#0-1) 

`resolve(normalizedRoot, normalizedRelative)` correctly computes an absolute path (e.g. `/Users/x/reponame-old/secret` when root is `/Users/x/reponame` and the segment is `../reponame-old/secret`), but the final check `realResolved.startsWith(realRoot)` is a pure string-prefix comparison with no trailing path-separator boundary check. Because `"/Users/x/reponame-old/secret"` textually starts with `"/Users/x/reponame"`, the function returns the resolved path as valid even though `reponame-old` is a completely different, sibling directory outside `reponame`.

The existing unit tests only cover exact-root traversal (`join('..', basename(root))` resolving back to the same root) and symlink-based escapes, not the sibling-directory-with-shared-prefix case, so this boundary condition is untested and unguarded: [3](#0-2) 

## Impact Explanation
If the resolved path passes the check, `shell.showItemInFolder(resolved)` is invoked, which opens the OS file browser and reveals/highlights the target file's location: [4](#0-3) 

This discloses the existence, name, and location of a file outside the clicked repository to the user who followed the attacker-crafted link — a "read outside the repo" style disclosure per the program's valid-impact definition, gated on a coincidental (or attacker-guessable) sibling directory name pattern (e.g., `<repo>-old`, `<repo>.bak`, `<repo>2`) existing next to the actual repository on disk.

## Likelihood Explanation
Exploitation requires that the victim's clicked-repo directory has a sibling directory whose name begins with the repo's own folder name (common patterns like backups, forks-with-suffix, or duplicate clones with numeric suffixes are plausible but not guaranteed). The attacker fully controls the `url`/`pr`/`branch` and `filepath` fields of the deep link, so the only non-attacker-controlled variable is the victim's local directory layout, making this a real but environment-dependent path-traversal bug rather than a guaranteed-successful attack.

## Recommendation
Fix the boundary check in `_resolveWithin` (`app/src/lib/path.ts`) to require that `realResolved` equals `realRoot` or starts with `realRoot + path.sep`, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + sep)
  ? resolved
  : null
```
and add a regression test for the sibling-directory-with-shared-prefix case (e.g., root `.../reponame` vs. `.../reponame-old`).

## Proof of Concept
1. Victim has cloned/has two folders side-by-side: `~/dev/reponame` (the repo the link opens) and `~/dev/reponame-old/secret` (any file).
2. Attacker crafts: `x-github-client://openRepo/https://github.com/org/reponame?filepath=../reponame-old/secret`.
3. `openRepositoryFromUrl` resolves `repository` to `~/dev/reponame`; `filepath = '../reponame-old/secret'` is not absolute, so it passes `isAbsolute` check.
4. `resolveWithin('~/dev/reponame', '../reponame-old/secret')` computes `resolved = '~/dev/reponame-old/secret'`; `realResolved.startsWith(realRoot)` evaluates true because `'~/dev/reponame-old/secret'.startsWith('~/dev/reponame')` is true.
5. `shell.showItemInFolder('~/dev/reponame-old/secret')` runs, revealing the file to the user outside the actual `reponame` repository.

### Citations

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

**File:** app/src/lib/path.ts (L64-71)
```typescript
  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
```

**File:** app/test/unit/path-test.ts (L47-63)
```typescript
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
