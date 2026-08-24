## Finding

The bug-class in the report—an incomplete/broken boundary check that lets a value escape its intended cap because the guard condition doesn't actually enforce containment—has a direct analog in GitHub Desktop's path-containment guard `resolveWithin`.

### Title
Path-containment check in `resolveWithin` uses a raw string-prefix comparison, allowing escape to sibling directories via a crafted deep-link `filepath` - (File: `app/src/lib/path.ts`)

### Summary
`resolveWithin` (and its POSIX/Win32 variants) is GitHub Desktop's central guard used to ensure that an untrusted, attacker-influenced relative path stays inside a given repository root before the app touches the filesystem with it. The final containment check is a bare `String.prototype.startsWith()` comparison with no path-separator boundary check, so any resolved path whose string representation merely begins with the root path's characters — even if it's actually a sibling directory (e.g. `myrepo-secrets` vs `myrepo`) — is treated as "inside" the root.

### Finding Description
`_resolveWithin` in [1](#0-0)  computes the final verdict as:

```ts
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)

return realResolved.startsWith(realRoot) ? resolved : null
```

This mirrors the report's broken-invariant pattern: the guard's job is to prevent the value from crossing a boundary (there: `inflationEpoch` capped at `MAX_INFLATION_PERIODS`; here: `realResolved` capped at being *inside* `realRoot`), but the actual comparison silently fails to enforce that boundary. `String.startsWith` has no concept of path-segment boundaries, so `"/Users/victim/Documents/GitHub/myrepo-secrets".startsWith("/Users/victim/Documents/GitHub/myrepo")` evaluates to `true`, even though `myrepo-secrets` is a sibling directory, not a subdirectory of `myrepo`.

The function's own docstring explicitly promises stronger semantics than what's implemented: "the resolved path is guaranteed to reside at, or underneath this path" [2](#0-1) , which is not true when a sibling directory happens to share the root directory name as a string prefix.

`resolveWithin` is relied upon as the sole safety check for attacker-influenced paths in at least two places:
- `dispatcher.ts`'s handling of the `filepath` query parameter from an `x-github-client://openRepo/...` deep link, where the only other check is rejecting absolute paths: [3](#0-2) 
- `app-store.ts`'s Copilot merge-conflict auto-resolution flow, which uses `resolveWithin` to gate writing AI-generated content to disk: [4](#0-3) 

Neither call site adds any additional separator-boundary check on top of `resolveWithin`, so both fully depend on the (broken) guarantee.

### Impact Explanation
Via a deep link (`x-github-client://openRepo/<url>?filepath=...`) that a user clicks, an attacker can supply a `filepath` value such as `../<repoDirName>-something/target`. If the resolved path happens to land on a sibling directory whose name has the repository's directory name as a prefix (a very common naming convention for backups, forks, or "-old"/"-secrets"/"-v2" folders), `resolveWithin` will incorrectly treat it as "inside" the repo and `shell.showItemInFolder` will be invoked on a path outside the intended repository root. In the Copilot conflict-resolution write path, the same broken check gates an actual file *write* of AI-resolved content — if reachable with such a path, it could silently corrupt a file outside the repository. This matches the "silent corruption of what the user commits" / "file write outside the repo" impact categories.

### Likelihood Explanation
Reaching the bug requires the resolved path's realpath string to coincidentally share the exact root path as a literal prefix while actually pointing to a different directory — i.e., a sibling directory whose name extends the repo directory's name. This is a real and common naming pattern (e.g., `project` and `project-backup`, `project` and `project.bak`) but is not universally guaranteed, so exploitation is opportunistic/environment-dependent rather than always reliable. It does not require local access, admin rights, or prior malware — only a user clicking a crafted link.

### Recommendation
Fix the boundary check in `_resolveWithin` to require a path-separator boundary (or exact equality) rather than a raw string prefix, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Add a regression test analogous to the existing symlink-escape tests in [5](#0-4)  that specifically covers the sibling-directory-with-shared-prefix case.

### Proof of Concept
1. Victim has a repository cloned at `/Users/victim/Documents/GitHub/myrepo` and, coincidentally or by convention, also has a directory `/Users/victim/Documents/GitHub/myrepo-secrets` containing sensitive files.
2. Attacker sends the victim a link: `x-github-client://openRepo/https://github.com/attacker/whatever?filepath=..%2Fmyrepo-secrets%2Fconfig.json`
3. `parseAppURL` parses this into an `IOpenRepositoryFromURLAction` with `filepath = "../myrepo-secrets/config.json"` [6](#0-5) .
4. `dispatcher.openRepositoryFromUrl` opens/clones the target repo, then calls `resolveWithin(repository.path, filepath)` [7](#0-6) .
5. `resolved` computes to `/Users/victim/Documents/GitHub/myrepo-secrets/config.json`; `realResolved.startsWith(realRoot)` (`realRoot = ".../myrepo"`) is `true` due to the missing separator check, so the guard passes and `shell.showItemInFolder` opens the file outside the intended repository. [8](#0-7)

### Citations

**File:** app/src/lib/path.ts (L13-24)
```typescript
/**
 * Resolve one or more path sequences into an absolute path underneath
 * or at the given root path.
 *
 * The path segments are expected to be relative paths although
 * providing an absolute path is also supported. In the case of an
 * absolute path segment this method will essentially only verify
 * that the absolute path is equal to or deeper in the directory
 * tree than the root path.
 *
 * If the fully resolved path does not reside underneath the root path
 * this method will return null.
```

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

**File:** app/src/lib/stores/app-store.ts (L7233-7259)
```typescript
      const absolutePath = await resolveWithin(repository.path, resolution.path)
      if (absolutePath === null) {
        log.warn(
          `Copilot resolution skipped: path outside repository: ${resolution.path}`
        )
        continue
      }

      // If the user resolved this file externally (e.g. in their editor) while
      // the result dialog was open, git status will report it with no remaining
      // conflict markers. Overwriting it with Copilot's stored content would
      // silently clobber their work, so skip it and let their resolution stand.
      // This mirrors how the manual conflicts dialog determines a file is
      // resolved (`hasUnresolvedConflicts`).
      const onDiskFile = state.changesState.workingDirectory.files.find(
        f => f.path === resolution.path
      )
      if (
        onDiskFile !== undefined &&
        isConflictedFileStatus(onDiskFile.status) &&
        !hasUnresolvedConflicts(onDiskFile.status)
      ) {
        continue
      }

      await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
      pathsToStage.push(resolution.path)
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
