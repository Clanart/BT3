Confirmed: `filepath` comes directly from the `x-github-desktop://openRepo?...&filepath=...` deep link query string with no path-traversal sanitization at parse time [1](#0-0) , and is passed straight through `resolveWithin(repository.path, filepath)` before being handed to `shell.showItemInFolder` [2](#0-1) . The only guard, `resolveWithin`, contains the boundary-check bug analogous to the Uniswap seed report.

I have enough evidence to produce the finding.

### Title
Directory-boundary check in `resolveWithin` uses unanchored `startsWith`, allowing sibling-directory escape via `x-github-desktop://openRepo` deep-link `filepath` parameter - (File: `app/src/lib/path.ts`)

### Summary
`resolveWithin()` is the sole guard that keeps a user-controlled relative path confined to a repository's directory tree. Its final check, `realResolved.startsWith(realRoot)` [3](#0-2) , is a raw string-prefix comparison with no trailing path-separator anchor. This is the exact same bug class as the Uniswap seed report: a boundary comparison that treats an "outside" value as "inside" because the comparison operator/method doesn't correctly encode the boundary. Here, any real path that merely shares a *string* prefix with the root (e.g. a sibling directory `myrepo-evil` next to `myrepo`) is incorrectly accepted as being "within" `myrepo`.

### Finding Description
`_resolveWithin` resolves `rootPath` and the attacker-influenced `pathSegments` to real, symlink-resolved absolute paths and then checks containment with:

```ts
return realResolved.startsWith(realRoot) ? resolved : null
``` [3](#0-2) 

`String.prototype.startsWith` performs no path-boundary awareness: `"/Users/victim/Documents/GitHub/myrepo-exfil".startsWith("/Users/victim/Documents/GitHub/myrepo")` is `true` even though `myrepo-exfil` is a completely different, sibling directory to `myrepo`. A relative path such as `../myrepo-exfil/secrets.txt`, once `resolve()`d against `rootPath = .../GitHub/myrepo`, lands in `.../GitHub/myrepo-exfil/secrets.txt` — outside the intended root — yet still passes the `startsWith` check because the prefix string matches.

Notably, the developers were aware of this exact pitfall elsewhere in the same codebase: `isClonePathSensitive()` in `clone.ts` deliberately appends the separator when doing the analogous check, `clonePath.startsWith(sensitive + Path.sep)` [4](#0-3) , but this hardening was not applied to `resolveWithin`.

The reachable attacker-controlled sink is the `filepath` query parameter of the `x-github-desktop://openRepo` deep link, which is parsed with no path sanitization beyond generic branch/pr checks [5](#0-4) . `Dispatcher.openRepositoryFromUrl` passes it straight to `resolveWithin(repository.path, filepath)` and, if non-null, reveals the resolved path via `shell.showItemInFolder(resolved)` [2](#0-1) . The only other check present is a simple `isAbsolute(filepath)` rejection, which does not defend against this relative-path sibling-escape.

Existing tests for `resolveWithin` only cover: pure `..` traversal (root has no matching prefix so it correctly fails), symlink escape/return, null bytes, and absolute paths staying within root [6](#0-5) . None of them exercise the sibling-directory-with-shared-prefix scenario, so the flaw is untested and unnoticed.

### Impact Explanation
This breaks the "file read outside the repo" invariant explicitly called out as in-scope: an attacker who controls a deep link (e.g. via a crafted "Open in Desktop" button on a malicious webpage, or any link the victim clicks) can cause GitHub Desktop to resolve and expose a path outside the target repository, as long as a sibling directory/file name happens to extend the repository directory name as a string. `shell.showItemInFolder` will open the OS file manager selecting that out-of-repo file, disclosing its existence/location to whatever process handles the reveal, and enabling further user-assisted disclosure. Because `resolveWithin` is a generic helper, any other future/other caller relying on it for containment inherits the same silent bypass.

### Likelihood Explanation
Exploitation requires: (1) the victim has a local clone whose GitHub remote matches the URL in the deep link (so `openOrCloneRepository` resolves to an existing repository) or accepts a fresh clone into an attacker-influenced name, and (2) a sibling path exists whose name is a superstring of the repository directory name. Repository directory names are frequently the project name and are predictable/attacker-influenceable when the attacker also controls the clone URL used earlier in the flow (repository names get derived from the URL via `sanitizeCloneName`, giving the attacker some control over the exact prefix string that must be matched by the sibling). This makes it plausible in realistic scenarios such as GitHub organizations where forks/related repos are cloned into adjacently-named folders (`repo`, `repo-fork`, `repo.wiki`, `repo-backup`), and the click-a-link precondition matches the stated valid attacker primitive.

### Recommendation
Anchor the containment check on a path boundary, not a raw string prefix:

```ts
return realResolved === realRoot ||
  realResolved.startsWith(realRoot.endsWith(Path.sep) ? realRoot : realRoot + Path.sep)
  ? resolved
  : null
```

Apply the same pattern consistently everywhere `startsWith` is used for path/root containment checks (this file already does it correctly in `clone.ts`'s `isClonePathSensitive`, which should be used as the reference implementation).

### Proof of Concept
1. Victim has two folders under the same parent directory: `/Users/victim/Documents/GitHub/myrepo` (a normal repo Desktop has open) and `/Users/victim/Documents/GitHub/myrepo-secret-notes/todo.txt` (an unrelated sibling directory that happens to extend `myrepo`'s name as a string).
2. Attacker sends the victim a link: `x-github-desktop://openRepo/https://github.com/owner/myrepo?filepath=../myrepo-secret-notes/todo.txt`.
3. `parseAppURL` extracts `filepath = "../myrepo-secret-notes/todo.txt"` with no traversal check [7](#0-6) .
4. `Dispatcher.openRepositoryFromUrl` finds/opens the `myrepo` repository, then calls `resolveWithin(repository.path, filepath)` [8](#0-7) .
5. Inside `_resolveWithin`, `resolved` becomes `/Users/victim/Documents/GitHub/myrepo-secret-notes/todo.txt`, and `realResolved.startsWith(realRoot)` evaluates `true` because `realRoot = "/Users/victim/Documents/GitHub/myrepo"` is a literal string prefix of `realResolved` [3](#0-2) .
6. The check passes, and `shell.showItemInFolder(resolved)` reveals `todo.txt` from the sibling directory — a file that is not part of `myrepo` — confirming the boundary bypass.

### Citations

**File:** app/src/lib/parse-app-url.ts (L98-124)
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

**File:** app/src/lib/path.ts (L68-71)
```typescript
  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
```

**File:** app/src/lib/git/clone.ts (L40-44)
```typescript
  for (const sensitive of sensitiveLocations) {
    if (clonePath === sensitive || clonePath.startsWith(sensitive + Path.sep)) {
      return true
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
