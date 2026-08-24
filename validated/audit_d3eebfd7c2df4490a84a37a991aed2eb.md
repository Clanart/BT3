### Title
Path-containment bypass via `startsWith` prefix check in `resolveWithin` allows deep-link `filepath` parameter to escape repository root into a sibling directory - ([File: app/src/lib/path.ts])

### Summary
`_resolveWithin` in `app/src/lib/path.ts` validates that a resolved path stays inside a given root directory by doing `realResolved.startsWith(realRoot)` with no trailing path-separator boundary check. [1](#0-0)  This is reachable from the `x-github-client://openRepo` deep-link handler `openRepositoryFromUrl`, which passes a user-controlled `filepath` query parameter to `resolveWithin(repository.path, filepath)` after only checking that the path is not absolute. [2](#0-1)  A relative `filepath` containing `..` segments can resolve into a sibling folder whose name merely starts with the same string as the repository's directory name, bypassing the intended "must stay inside repo" invariant, analogous to the Lombard report's pattern of a containment check that fails to cover a valid-looking-but-distinct case.

### Finding Description
The core invariant that is supposed to hold is: *any path derived from `filepath` in an `openRepo` deep link must resolve to a location inside `repository.path`*. The guard is implemented as:
```
return realResolved.startsWith(realRoot) ? resolved : null
``` [3](#0-2) 
`startsWith` is a string-prefix comparison, not a path-boundary comparison. If the repository directory is `/Users/alice/Documents/repo`, a resolved path of `/Users/alice/Documents/repo-secrets/file.txt` also "starts with" `/Users/alice/Documents/repo` even though it is a completely different, sibling directory. `dispatcher.ts` calls this with only one guard beforehand - rejecting absolute `filepath` values - and does not reject `..` traversal segments, so a relative `filepath` such as `../repo-secrets/file.txt` will make `Path.resolve(root, filepath)` land squarely in the sibling folder, and the buggy `startsWith` check will accept it. [4](#0-3) 

The existing unit tests for `resolveWithin` only cover: `..`/`../..` outside the root, symlink escapes, and null-byte rejection - none of them exercise the "escape into an unrelated sibling with an overlapping name prefix" case, so this class of bypass is not covered by the guards the developers already added. [5](#0-4) 

### Impact Explanation
The `filepath` is passed on to `shell.showItemInFolder(resolved)`, i.e., the escaped path is opened in the OS file explorer. [6](#0-5)  This is triggered purely by having the victim click an attacker-crafted `x-github-client://openRepo?...&filepath=...` deep link — the classic "link the user clicks" primitive named in the impact criteria — and results in the app revealing/opening a file or folder located outside the boundary the code explicitly tries to enforce ("file write or read outside the repo" category). This is most reliably exploitable in common GitHub Desktop clone layouts where users keep multiple related repositories as numbered/suffixed siblings under one parent folder (e.g., `repo`, `repo2`, `repo-old`, `repo-private`), which is a normal, unforced user habit, not "unnatural steps."

### Likelihood Explanation
The attack surface (custom URL protocol handler for opening repositories) is externally reachable by any web page or message that gets the user to click a link, matching the report's own valid-impact criteria for a "link or deep link the user clicks." Exploitability depends on there being a sibling directory whose name is a prefix-extension of the target repo directory's name — a scenario that is plausible but not universal, since it depends on the victim's local folder layout, so likelihood is moderate rather than guaranteed for a given user, though the class of bug (missing separator check on `startsWith`) is a variant of a very common, well-documented containment-check flaw.

### Recommendation
In `_resolveWithin` (`app/src/lib/path.ts`), require an exact match or a proper separator boundary, e.g.:
```
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Add a regression test mirroring the existing `resolveWithin` suite that specifically covers the sibling-directory-with-shared-prefix case (e.g., root `/tmp/foo`, candidate `/tmp/foobar`) to prevent regressions of this exact bypass class.

### Proof of Concept
1. Assume the victim has GitHub Desktop with a cloned repository at `~/Projects/repo` and another unrelated folder at `~/Projects/repo-secrets` containing a sensitive file `secret.txt`.
2. The victim has `repo` open/known to Desktop (matches by `url`/`pr`/`branch` in `openRepositoryFromUrl`).
3. Attacker sends the victim a link:
   `x-github-client://openRepo/https://github.com/owner/repo?branch=main&filepath=..%2Frepo-secrets%2Fsecret.txt`
4. `openRepositoryFromUrl` resolves `repository` to `~/Projects/repo`, sees `filepath = '../repo-secrets/secret.txt'` is not absolute, calls `resolveWithin('~/Projects/repo', '../repo-secrets/secret.txt')`. [7](#0-6) 
5. `Path.resolve` produces `~/Projects/repo-secrets/secret.txt`; `realResolved.startsWith(realRoot)` where `realRoot = '~/Projects/repo'` evaluates true because the sibling path string begins with that exact substring. [1](#0-0) 
6. `shell.showItemInFolder` opens/reveals `secret.txt`, a file entirely outside the intended repository root, purely from clicking the link.

### Citations

**File:** app/src/lib/path.ts (L66-71)
```typescript
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

**File:** app/test/unit/path-test.ts (L44-78)
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
```
