Found the exact analog: the `_resolveWithin` boundary check in `app/src/lib/path.ts` uses a raw `String.prototype.startsWith` comparison between the resolved real path and the real root path, with no separator-boundary check — the same class of bug as the CKB `rich-indexer` prefix-boundary flaw (a lexicographic/string-prefix comparison that is not anchored on a component boundary, causing the check to accept values it shouldn't).

### Title
Path-containment bypass via unanchored `startsWith` prefix check in `resolveWithin` - ([File: app/src/lib/path.ts])

### Summary
`resolveWithin`/`resolveWithinPosix`/`resolveWithinWin32` are the app's central guard used to make sure a path derived from external, attacker-influenced input (deep-link `filepath`, archive/session content, etc.) stays inside a trusted root directory before being handed to `shell.showItemInFolder` or similar file operations.

### Finding Description
The guard resolves both `rootPath` and the candidate path to their real (symlink-resolved) paths and then does: [1](#0-0) 

`realResolved.startsWith(realRoot)` is a raw string-prefix test with no trailing separator appended to `realRoot`. This is exactly the invariant class broken in the CKB report: a boundary check computed from a byte/character prefix without accounting for where the "true" boundary actually falls. Here, if `realRoot` is e.g. `/Users/victim/Documents/GitHub/project` and an attacker-influenced path resolves to a sibling directory `/Users/victim/Documents/GitHub/project-evil/secret.txt`, then `realResolved.startsWith(realRoot)` is `true` even though `project-evil` is a completely different directory, not a subpath of `project`. The check silently returns the resolved path as if it were validated to be "underneath" the root, defeating the purpose of the function.

This mirrors the CKB bug precisely at the abstraction level: both use naive prefix/lexicographic comparisons for a boundary test (upper-bound for a range query there, containment for a filesystem root here) without normalizing for the true delimiter that defines "belongs to X" vs. "merely starts with the same characters as X."

### Impact Explanation
`resolveWithin` is called from `dispatcher.ts`'s `openRepositoryFromUrl` handler with a `filepath` argument that comes from a deep link (`x-github-client://openRepo/...`), i.e., fully attacker-controlled via a link the user clicks: [2](#0-1) 
If any repository on disk has a sibling directory whose name is a superstring of the repository path (e.g. `repo` vs `repo-secrets`, or more realistically achievable on typical multi-repo checkout layouts), a crafted deep link could cause Desktop to reveal/open a file located outside the intended repository root, bypassing the explicit "prevent path traversal" comment's guarantee. The risk is bounded by needing a naturally-colliding sibling directory name to exist, but it is a real logic break in a function whose entire contract is "guaranteed to reside at, or underneath, this path."

### Likelihood Explanation
Exploitability requires a specific directory-naming coincidence (sibling directory name prefixed by the root path string) to exist on the victim's machine, which is not fully attacker-controlled and makes real-world exploitation situational rather than reliable. This lowers likelihood substantially compared to the original CKB bug (which affected ~2.4% of all mainnet script args deterministically). The existing tests only cover `..` traversal and symlink escape via `resolveWithin`, not the sibling-prefix collision case: [3](#0-2) 
so the gap is untested and unnoticed, but it is a narrow edge case, not the primary attack surface (unlike the `.git` clone-path traversal issue in `remote-parsing.ts`/`clone.ts`, which is already defended by `sanitizeCloneName` and dedicated tests).

### Recommendation
Change the boundary check to anchor on a path-separator boundary, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
(using the appropriate `options`-provided separator for the win32/posix variants), so that `startsWith` can never match a same-prefixed sibling directory.

### Proof of Concept
1. On disk, have two directories: `/Users/victim/Documents/GitHub/project` (the trusted repository root) and `/Users/victim/Documents/GitHub/project-evil` (attacker-influenced, e.g. cloned earlier or pre-existing).
2. Trigger a deep link such as `x-github-client://openRepo/0/https://github.com/owner/project?filepath=../project-evil/secret.txt` handled by `openRepositoryFromUrl`.
3. `resolveWithin(repository.path, filepath)` resolves `filepath` to `/Users/victim/Documents/GitHub/project-evil/secret.txt`.
4. `realResolved.startsWith(realRoot)` evaluates `'/Users/victim/Documents/GitHub/project-evil/secret.txt'.startsWith('/Users/victim/Documents/GitHub/project')` → `true`, incorrectly passing the containment check.
5. `shell.showItemInFolder(resolved)` is called on a path outside the intended repository, revealing/opening a file the guard was supposed to block.

Note: I could not find a git history/blame or changelog confirming whether this exact code path has been hardened in a newer version than what's indexed; the analysis above is based solely on the current state of `app/src/lib/path.ts` in this repository snapshot.

### Citations

**File:** app/src/lib/path.ts (L68-71)
```typescript
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
