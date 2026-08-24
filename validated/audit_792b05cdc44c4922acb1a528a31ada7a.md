### Title
Directory-boundary check in `resolveWithin()` uses an unanchored `startsWith` prefix test, allowing path traversal to sibling directories via the `x-github-desktop://openRepo` deep-link `filepath` parameter - (File: `app/src/lib/path.ts`)

### Summary
Github Desktop's `x-github-desktop://openRepo?...&filepath=...` deep link handler resolves an attacker-supplied `filepath` value against the target repository root using `resolveWithin()`, which is supposed to guarantee the resolved path stays inside the repository. The containment check is implemented as a raw string-prefix comparison (`realResolved.startsWith(realRoot)`) instead of verifying a path-separator boundary, so a resolved path in a sibling directory whose name happens to start with the repository directory's name will incorrectly pass validation.

### Finding Description
`resolveWithin()` computes the real (symlink-resolved) root and target paths and then decides containment purely with: [1](#0-0) 

`String.prototype.startsWith` has no concept of path segment boundaries. If the repository lives at `/Users/victim/repo` and there exists a sibling directory `/Users/victim/repo-secrets` (or any directory whose name is prefixed by `repo`), then a resolved path such as `/Users/victim/repo-secrets/passwords.txt` satisfies `realResolved.startsWith(realRoot)` even though it is *not* nested under the repository root at all. This is a classic CWE-22-style "unanchored prefix" boundary bug — structurally identical in nature to the source report's broken invariant (a comparison operator that fails to correctly enforce the intended boundary condition), just manifesting as a path check instead of a price check.

This helper is the sole guard used to validate a fully attacker-controlled value from the app's custom URL protocol handler. `parseAppURL()` extracts `filepath` directly from the query string of an `x-github-desktop://openrepo` URL with no path sanitization beyond leaving it as a raw string: [2](#0-1) 

The dispatcher then only rejects absolute paths and otherwise trusts `resolveWithin()` as the sole containment check before acting on the resolved path: [3](#0-2) 

The same broken primitive (`resolveWithin`/`_resolveWithin`) is reused as the security boundary in other attacker-adjacent flows, including resolving conflicted file paths for the Copilot-assisted merge-conflict feature [4](#0-3)  and writing Copilot's resolved file content back to disk [5](#0-4) , meaning the same class of defect is trusted repeatedly as an "outside-the-repo" guard.

Existing tests only cover `..`-traversal and symlink-escape cases and never test the sibling-prefix scenario, so the gap is not caught: [6](#0-5) 

### Impact Explanation
An attacker who gets a victim to click a crafted `x-github-desktop://openrepo?url=...&filepath=...` deep link can cause Desktop to resolve `filepath` against the cloned/opened repository root and, if the containment check is bypassed via a same-prefix sibling directory, act on a path outside the intended repository tree. In the currently reachable `openRepositoryFromUrl` path the action taken is `shell.showItemInFolder(resolved)` — an information-disclosure/UI-confusion primitive (revealing files/folders outside the repo to the user, potentially useful for further social-engineering or chained attacks). Because `resolveWithin` is reused elsewhere as the "path must stay in repo" guard for file *writes* (e.g., Copilot conflict-resolution content), the same broken invariant is a much higher-severity primitive (writing attacker-influenced content outside the repository) wherever an attacker can also control the destination `path`/`filepath` value going into `resolveWithin`.

### Likelihood Explanation
Exploitation requires the resolved sibling path to exist and its name to share the repository directory name as a prefix — this is a real but directory-naming-dependent precondition, which is why this is presented as a boundary-logic defect rather than a guaranteed always-exploitable bug. However, the flaw is deterministic given the naming precondition (no race condition, no privilege requirement, and the deep link is clickable by any external actor per GitHub's documented "Open in Desktop" protocol), and it undermines every caller that relies on `resolveWithin` for security, increasing the overall likelihood that at least one call site is affected in a given environment.

### Recommendation
Fix the boundary check in `_resolveWithin` to require a path-separator boundary (or exact equality with root), e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Add a regression test asserting that a sibling directory whose name is prefixed by the root directory's basename (e.g. root `.../repo`, sibling `.../repo-evil`) is rejected.

### Proof of Concept
1. Victim has a repository opened locally at `/Users/victim/Documents/GitHub/repo`, and a sibling directory `/Users/victim/Documents/GitHub/repo-secrets/passwords.txt` also exists (e.g., created by another tool, backup folder, or another repo clone naming convention).
2. Attacker crafts and gets the victim to click:
   `x-github-desktop://openrepo?url=https://github.com/owner/repo&filepath=..%2Frepo-secrets%2Fpasswords.txt`
3. `parseAppURL` extracts `filepath = "../repo-secrets/passwords.txt"` [7](#0-6) .
4. `openRepositoryFromUrl` passes it (not absolute, so the guard is skipped) into `resolveWithin(repository.path, filepath)` [8](#0-7) .
5. Inside `_resolveWithin`, `resolved` evaluates to `/Users/victim/Documents/GitHub/repo-secrets/passwords.txt`, and `realResolved.startsWith(realRoot)` is `true` because `"...repo-secrets/passwords.txt".startsWith("...repo")` is true, even though the file is not under `repo/` [1](#0-0) .
6. `shell.showItemInFolder(resolved)` is invoked on the out-of-repo file, confirming the boundary bypass.

### Citations

**File:** app/src/lib/path.ts (L66-71)
```typescript
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
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
