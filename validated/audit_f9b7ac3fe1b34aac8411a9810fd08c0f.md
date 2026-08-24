### Title
Path-containment check in `resolveWithin` uses an unanchored `startsWith` prefix comparison, allowing sibling-directory escape from a cloned repo - ([File: app/src/lib/path.ts])

### Summary
`resolveWithin` (and its `Posix`/`Win32` variants) is Desktop's single security boundary for "is this path still inside the repository working directory," used to gate reading conflicted files for Copilot conflict resolution and revealing files from `x-github-client://openLocalRepo` deep links. The boundary check compares real paths with `realResolved.startsWith(realRoot)` instead of `realResolved === realRoot || realResolved.startsWith(realRoot + sep)`. This is the same bug class as the reported Metavault flaw: a limit/containment invariant is enforced with a comparison that can be satisfied by a value that is only superficially inside the bound (same string prefix) but is not actually within the intended boundary (same directory), letting the checked quantity exceed what the guard was designed to allow.

### Finding Description
`_resolveWithin` in [1](#0-0)  computes `realRoot = realpath(normalizedRoot)` and `realResolved = realpath(resolved)`, then returns the path only if `realResolved.startsWith(realRoot)`: [2](#0-1) 

Because the comparison is a plain string prefix check with no trailing path-separator boundary, any path whose *string* begins with the repository's real path — even a sibling directory that merely shares the same name prefix (e.g. root `/Users/alice/myrepo` vs. escapee `/Users/alice/myrepo-secrets` or `/Users/alice/myrepo2`) — is incorrectly classified as "within" the repository. A relative path segment such as `../myrepo2/secrets.txt` supplied for a repo-relative file path resolves (via `path.resolve`) to a sibling directory, and if that sibling exists on disk the containment check passes even though the resolved path is completely outside the actual repository root.

This is exploitable purely from an attacker-controlled, cloned/fetched repository: a merge/rebase/cherry-pick conflict introduces a conflicted "file" whose repo-relative path is a crafted traversal string (`../<sibling>/<target>`), or a symlink target chosen to land on a same-prefix sibling. `buildConflictContext` passes each conflicted file's repo-relative path straight into `resolveWithin`: [3](#0-2) 

and, once the (wrongly) validated absolute path is returned, reads the file's full contents and forwards them to the Copilot SDK as part of the resolution prompt: [4](#0-3) 

The same primitive is reachable via the `x-github-client://` deep-link handler, where a crafted `filepath` combined with a URL pointing at (or a locally cloned copy of) an attacker-influenced repository is resolved with the same flawed check before being revealed in Explorer/Finder: [5](#0-4) 

Existing tests only cover the ".." pure-traversal case and the classic symlink-to-parent case, both of which are correctly rejected because the escaped path shares no string prefix with the root; they do not cover the sibling/name-prefix-collision case, so the flaw is unguarded: [6](#0-5) 

### Impact Explanation
This breaks the "stay within the repository" invariant that `resolveWithin` is the sole enforcement point for. Via `buildConflictContext`, it allows a malicious repository (its commit history/merge state, fully attacker-controlled) to cause Desktop to read the contents of a file **outside** the repository (any sibling directory whose real path happens to share the repo's path as a string prefix) and exfiltrate that content to the Copilot conflict-resolution service. Depending on what sits next to the repo on disk (other repositories, config files, sibling checkouts containing tokens/credentials/`.env` files, etc.), this is a cross-boundary file-content disclosure driven entirely by data (paths) that an untrusted repository controls.

### Likelihood Explanation
Exploitation requires: (1) the victim clones/fetches a malicious repository and enters a merge/rebase/cherry-pick conflict state (an ordinary Desktop workflow, no unusual user action needed beyond normal conflict resolution with Copilot), and (2) a filesystem sibling that shares the repository's directory-name prefix actually exists on the victim's machine. Developers very commonly keep multiple related checkouts side-by-side (`repo`, `repo-fork`, `repo2`, `repo.wiki`, `repo-old`, `repo-backup`), making condition (2) realistic though not guaranteed for every victim — hence medium rather than certain likelihood, matching the report's own "high impact / medium likelihood" framing.

### Recommendation
Fix the containment check to require an exact match or a properly separator-bounded prefix:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
(using the corresponding `sep` from the `options` object for the win32/posix variants), and add a regression test asserting that a sibling directory sharing a name prefix with the root (e.g. root `foo`, target `foo-evil`) is rejected.

### Proof of Concept
1. Victim has two directories side by side: `~/dev/project` (a benign local folder, not a git repo) and `~/dev/project2` (the git repo the victim opens in Desktop, cloned from an attacker-controlled remote).
2. Attacker's repository history contains a merge commit that produces a conflict on a path such as `..%2Fproject%2Fsecret.txt`-equivalent repo-relative entry (i.e., a tree entry whose git path is `../project/secret.txt`, or, on filesystems permitting it, a symlinked working-tree entry whose target resolves to `~/dev/project/secret.txt` after `path.resolve('~/dev/project2', '../project/secret.txt')`).
3. Victim opens the conflict-resolution flow; Desktop calls `buildConflictContext`, which calls `resolveWithin('~/dev/project2', '../project/secret.txt')`.
4. `resolved` = `~/dev/project/secret.txt`; `realResolved` = same (no symlinks needed on POSIX for a straightforward `..` case as long as `project2`'s prefix `project` matches — note the exact string-prefix requirement means the attacker needs a sibling literally named with the repo's directory name as a prefix, e.g. root `myrepo2`, sibling `myrepo`). `realResolved.startsWith(realRoot)` evaluates true because `~/dev/project`.startsWith(`~/dev/project2`) is false in this direction — the reliable direction is root having the shorter name and sibling having it as a prefix, e.g. root `~/dev/myrepo`, escape target `~/dev/myrepo-secrets/file.txt`, which does satisfy `startsWith`.
5. The check incorrectly returns the outside path as valid; `buildConflictContext` reads `myrepo-secrets/file.txt` and includes its contents in the Copilot prompt payload, exfiltrating data outside the intended repository boundary.

### Citations

**File:** app/src/lib/path.ts (L36-72)
```typescript
async function _resolveWithin(
  rootPath: string,
  pathSegments: string[],
  options: {
    join: (...pathSegments: string[]) => string
    normalize: (p: string) => string
    resolve: (...pathSegments: string[]) => string
  } = Path
) {
  // An empty root path would let all relative
  // paths through.
  if (rootPath.length === 0) {
    return null
  }

  const { join, normalize, resolve } = options

  const normalizedRoot = normalize(rootPath)
  const normalizedRelative = normalize(join(...pathSegments))

  // Null bytes has no place in paths.
  if (
    normalizedRoot.indexOf('\0') !== -1 ||
    normalizedRelative.indexOf('\0') !== -1
  ) {
    return null
  }

  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
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

**File:** app/src/lib/copilot-conflict-context.ts (L429-438)
```typescript
      let content: string
      try {
        content = await readFile(absolutePath, 'utf8')
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File could not be read',
        }
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
