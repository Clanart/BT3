## Title
Path-traversal via missing path-separator boundary check in `resolveWithin` allows escaping repository root through sibling-directory prefix collision — (`app/src/lib/path.ts`)

## Summary
`resolveWithin` (and its `resolveWithinPosix`/`resolveWithinWin32` variants) is Desktop's central guard against path traversal: it resolves a caller-supplied relative path against a `rootPath` and is supposed to return `null` if the result escapes the root. The final check is a plain string comparison, `realResolved.startsWith(realRoot)` [1](#0-0) , which has no path-separator boundary check. Just like the Footium `generationId > _maxGenerationId` off-by-one that let `maxGenerationId` itself slip through an inclusive/exclusive boundary, this check lets any sibling path whose name is a *string prefix* of the root's basename slip through the "must be inside root" boundary (e.g. `repo` vs `repo.wiki`, `repo` vs `repo-secrets`).

## Finding Description
`_resolveWithin` normalizes and resolves the requested path, then validates containment using:
```
return realResolved.startsWith(realRoot) ? resolved : null
``` [1](#0-0) 

`String.prototype.startsWith` does not know about path separators. If `realRoot` is `/Users/victim/Documents/GitHub/myrepo` and `realResolved` is `/Users/victim/Documents/GitHub/myrepo.wiki/SECRET.md` (a real, distinct sibling directory), the `startsWith` check returns `true` even though `myrepo.wiki` is not underneath `myrepo` at all. The correct check needs to verify a separator boundary (e.g. `realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)`).

This guard is exercised with attacker-influenced input in `openRepositoryFromUrl`, the handler for `x-github-client://openRepo` deep links, where a user-clicked link supplies both the target repository `url` and an untrusted `filepath` segment:
```
const resolved = await resolveWithin(repository.path, filepath)
if (resolved !== null) {
  shell.showItemInFolder(resolved)
} else { ... }
``` [2](#0-1) 

The only other check applied to `filepath` is `isAbsolute(filepath)` [3](#0-2) , which does nothing to stop a relative `..`-based escape that lands in a same-prefix sibling directory. The unit test suite for `resolveWithin` covers `..` traversal, symlink traversal, and null bytes, but never tests the sibling-prefix collision case [4](#0-3) , so this gap is undetected by existing guards/tests.

The same primitive is also reachable through `buildConflictContext`, which calls `resolveWithin(workingDirectory, file.path)` to sandbox reads of merge-conflicted file paths [5](#0-4) , making the dispatcher deep-link path the strongest, most directly attacker-triggerable instance since it requires only a clicked link, not a crafted repository state.

## Impact Explanation
This breaks the "the resolved path is guaranteed to reside at, or underneath this path" invariant documented directly above `_resolveWithin` [6](#0-5) . Via a single clicked `x-github-client://openRepo` link, an attacker can cause Desktop to reveal (`shell.showItemInFolder`) a file located outside the intended repository, as long as the victim has any sibling directory whose name is a superstring of the target repo's directory name — a very common real-world case for GitHub Desktop users, since Desktop itself clones wiki repositories into a `<repo>.wiki` sibling folder, and many developers keep multiple similarly-named repo clones (`repo`, `repo-private`, `repo-secrets`, `repo2`) side by side in the same parent folder. This is a file read/disclosure outside the repository triggered purely by a link click, matching the accepted impact class.

## Likelihood Explanation
Likelihood is elevated because: (1) the trigger is a deep link that a user can be induced to click, requiring no local access, credentials, or malware; (2) the vulnerable check is the sole boundary enforcement in the shared `resolveWithin` helper, used across multiple security-sensitive call sites; (3) the specific sibling-prefix bypass is not covered by any existing test, meaning it can regress or persist silently; (4) the necessary preconditions (existence of a same-prefix sibling directory) are common due to Desktop's own `.wiki` clone naming convention.

## Recommendation
Change the containment check in `_resolveWithin` to require a path-separator boundary, not just a string prefix, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Add regression tests covering sibling directories that share a name prefix (e.g. `repo` vs `repo.wiki`, `repo` vs `repo2`) for both `resolveWithinPosix` and `resolveWithinWin32`.

## Proof of Concept
1. Victim has GitHub Desktop with a repository cloned at `/Users/victim/Documents/GitHub/myrepo`, and (per Desktop's own convention, or simply because they cloned two similarly named repos) also has `/Users/victim/Documents/GitHub/myrepo.wiki` containing a file `SECRET.md`.
2. Attacker sends the victim a link:
   `x-github-client://openRepo/https://github.com/owner/myrepo?filepath=..%2Fmyrepo.wiki%2FSECRET.md`
3. Victim clicks the link. Desktop routes to `openRepositoryFromUrl`, resolves/opens `myrepo`, then calls:
   `resolveWithin('/Users/victim/Documents/GitHub/myrepo', '../myrepo.wiki/SECRET.md')` [7](#0-6) 
4. Inside `_resolveWithin`, `resolved` becomes `/Users/victim/Documents/GitHub/myrepo.wiki/SECRET.md`; `realRoot` is `/Users/victim/Documents/GitHub/myrepo`. Since `realResolved.startsWith(realRoot)` is `true` (pure string prefix match) [1](#0-0) , the function returns the path instead of `null`.
5. `shell.showItemInFolder(resolved)` reveals `SECRET.md` from the sibling `myrepo.wiki` folder in the OS file explorer, outside the repository the link claimed to target.

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

**File:** app/test/unit/path-test.ts (L44-102)
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

    if (!__WIN32__) {
      it('fails for paths that use a symlink to traverse outside of the root', async () => {
        const tempDir = await mkdtemp(join(tmpdir(), 'path-test'))
        const symlinkName = 'dangerzone'
        const symlinkPath = join(tempDir, symlinkName)

        try {
          await symlink(resolve(tempDir, '..', '..'), symlinkPath)
          assert((await resolveWithin(tempDir, symlinkName)) === null)
        } finally {
          await unlink(symlinkPath)
          await rmdir(tempDir)
        }
      })

      it('succeeds for paths that use a symlink to traverse outside of the root and then back again', async () => {
        const tempDir = await mkdtemp(join(tmpdir(), 'path-test'))
        const symlinkName = 'dangerzone'
        const symlinkPath = join(tempDir, symlinkName)

        try {
          await symlink(resolve(tempDir, '..', '..'), symlinkPath)
          const throughSymlinkPath = join(
            symlinkName,
            basename(resolve(tempDir, '..')),
            basename(tempDir)
          )
          assert.equal(
            await resolveWithin(tempDir, throughSymlinkPath),
            resolve(tempDir, throughSymlinkPath)
          )
        } finally {
          await unlink(symlinkPath)
          await rmdir(tempDir)
        }
      })
    }
  })
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
