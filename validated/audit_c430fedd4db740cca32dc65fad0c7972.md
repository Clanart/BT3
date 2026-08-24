## Title
`resolveWithin()` uses a raw string-prefix check instead of a path-boundary check, allowing sandbox escape into sibling directories - (File: `app/src/lib/path.ts`)

### Summary
`_resolveWithin()` in [1](#0-0)  is documented as guaranteeing that a resolved path is "at, or underneath" a given root path, but the actual containment check is a naive `String.prototype.startsWith()` comparison with no path-separator boundary. This is exactly the report's bug class: the function's contract ("never return true / always stay within root") is not what the implementation actually checks.

### Finding Description
The containment test is:

```
return realResolved.startsWith(realRoot) ? resolved : null
``` [2](#0-1) 

`startsWith` matches on raw characters, not path components. If `realRoot` is `/Users/victim/Documents/GitHub/project` and a candidate resolves to `/Users/victim/Documents/GitHub/project-secrets/notes.txt`, `realResolved.startsWith(realRoot)` is `true` even though `project-secrets` is a completely different, sibling directory — not "underneath" `project` at all. The same holds for case-insensitive filesystems (default on macOS/Windows) or any directory whose name is a superstring of the root's name (a very common pattern: `repo`, `repo-old`, `repo.bak`, `repo2`, `Repo` vs `repository`, etc.).

This function is relied on as the sole traversal guard for attacker-influenced paths in at least two places:
- `openRepositoryFromUrl` in the deep-link/protocol handler, where `filepath` comes directly from a `x-github-client://openRepo/...&filepath=...` URL that a user can be lured into clicking, and is resolved against `repository.path` before being passed to `shell.showItemInFolder` [3](#0-2) , with the filepath itself coming from `parseAppURL` [4](#0-3) .
- `buildConflictContext`, which resolves conflicted file paths against the repository working directory before reading file contents to send to Copilot [5](#0-4) .

The existing unit tests only cover `..`-traversal and symlink escape cases [6](#0-5) ; they do not cover the sibling-directory prefix-collision case, so the gap is not caught.

### Impact Explanation
Where this guard is bypassed, an attacker-controlled deep link can cause GitHub Desktop to call `shell.showItemInFolder` on, or `buildConflictContext` to read and exfiltrate (to an AI service) the contents of, a file that lives outside the intended repository root — despite the code explicitly believing it has confirmed containment ("Prevented attempt to open path outside of the repository root" branch is bypassed). This is a file-read/disclosure primitive triggered purely by the user clicking a link, matching the "file read outside the repo" and "link/deep link the user clicks" categories.

### Likelihood Explanation
Exploitability depends on the victim already having (or the attacker being able to induce, e.g. via a previous "Clone in Desktop" flow that names a folder after the untrusted repo/owner name) a sibling directory whose name is a prefix-extension of the target repository's folder name. This is a common real-world naming pattern (`project`, `project-old`, `project.bak`, case variants) but is not guaranteed to exist, so likelihood is moderate rather than universal — the root cause, however, is a clear and reproducible logic defect independent of that precondition.

### Recommendation
Fix the containment check to enforce an actual path-boundary comparison instead of a raw string prefix, e.g.:

```ts
const relative = path.relative(realRoot, realResolved)
const isWithin =
  relative === '' ||
  (!relative.startsWith('..') && !path.isAbsolute(relative))
return isWithin ? resolved : null
```

or equivalently ensure `realResolved === realRoot || realResolved.startsWith(realRoot + path.sep)`. Add regression tests for sibling-directory names that share a prefix with the root (including case-insensitive filesystem scenarios).

### Proof of Concept
1. Victim has cloned two repos locally: `/Users/victim/Documents/GitHub/demo` and `/Users/victim/Documents/GitHub/demo-internal` (the latter containing a sensitive file, e.g. `notes.txt`).
2. Attacker sends the victim a link: `x-github-client://openRepo/https://github.com/attacker/demo?filepath=../demo-internal/notes.txt`.
3. `parseAppURL` extracts `filepath = ../demo-internal/notes.txt` [4](#0-3) ; it is not absolute so it passes the `isAbsolute` check in `openRepositoryFromUrl` [7](#0-6) .
4. `resolveWithin('/Users/victim/Documents/GitHub/demo', '../demo-internal/notes.txt')` resolves to `/Users/victim/Documents/GitHub/demo-internal/notes.txt`; `realResolved.startsWith(realRoot)` evaluates true because the string `".../demo-internal/notes.txt"` starts with the string `".../demo"` — the missing directory-separator boundary check turns the "stay within `demo`" guarantee into a false positive [2](#0-1) .
5. `shell.showItemInFolder(resolved)` reveals the file from the sibling `demo-internal` repository, outside the repository the guard was meant to confine access to.

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

**File:** app/test/unit/path-test.ts (L44-101)
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
```
