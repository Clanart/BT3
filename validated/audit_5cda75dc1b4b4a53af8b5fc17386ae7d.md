## Finding: Path-containment check in `resolveWithin` is a prefix match without a directory-boundary guard

### Title
Deep-link `filepath` can escape the intended repository via `resolveWithin`'s unguarded `startsWith` containment check - (File: `app/src/lib/path.ts`)

### Summary
`resolveWithin`, the function GitHub Desktop uses everywhere it must guarantee "this path stays inside the repository," decides containment with `realResolved.startsWith(realRoot)`. That check treats any path whose string representation is *prefixed* by the root's real path as "inside," even when the two paths are actually siblings (e.g. `/Users/name/repo` vs `/Users/name/repo-backup`). This is the same class of bug as the report's core theme: two values meant to represent the same boundary (declared root vs. actually-resolved path) are compared with an inexact/boundary-unaware method, so the guard silently passes for inputs it was designed to reject.

### Finding Description
`resolveWithin` in [1](#0-0)  resolves the untrusted path segments to an absolute path, `realpath`s both the root and the resolved path, and then does:
```
return realResolved.startsWith(realRoot) ? resolved : null
```
There is no check that `realResolved === realRoot` or that it starts with `realRoot + path.sep`. Consequently, if a directory exists on disk whose name is the repository's directory name plus an arbitrary suffix (e.g. repository at `/Users/name/project` and an unrelated sibling directory `/Users/name/project-secrets`), a resolved path inside that sibling directory will incorrectly pass the containment check, because the string `"/Users/name/project-secrets/foo"` starts with `"/Users/name/project"`.

This guard is relied upon by attacker-reachable, deep-link-driven code. `parseAppURL` extracts a `filepath` query parameter straight from an `x-github-client://openrepo/...?filepath=...` URL that a user can be lured into clicking (a link the user clicks, per the accepted attacker model): [2](#0-1) . `Dispatcher.openRepositoryFromUrl` then calls `resolveWithin(repository.path, filepath)` and, if it returns non-null, opens that resolved path with `shell.showItemInFolder`: [3](#0-2) . The only other check performed is `isAbsolute(filepath)`, which does nothing to stop the sibling-prefix bypass since the final segment is still processed as a relative path joined onto the (correct) root before the flawed `startsWith` comparison is applied.

The same `resolveWithin` primitive is reused for the Copilot conflict-resolution flow, where it gates which absolute path on disk gets overwritten with model-generated content via `writeFile`: [4](#0-3)  and for reading files into an AI request: [5](#0-4) . Any future or existing caller that trusts `resolveWithin`'s null/non-null result as an authoritative "is this inside the repo" answer inherits the same boundary defect.

The existing unit tests only cover `..`-traversal, null-byte, and symlink-escape cases; there is no test for the sibling-directory-name-prefix case, confirming the gap was not considered: [6](#0-5) .

### Impact Explanation
This falls under "a link or deep link the user clicks... resulting in... file write or read outside the repo." Depending on the caller:
- Via `openRepositoryFromUrl`, an attacker-crafted `filepath` can cause Desktop to reveal/open a file outside the repository (in a similarly-named sibling directory) via `shell.showItemInFolder`.
- Via the Copilot conflict-resolution `writeFile` path, if a sibling directory sharing the repository's directory name as a prefix exists (plausible in common layouts like `project` / `project-old`, `project` / `project.bak`, or a user's own second checkout `project2`), the "outside repo" guard can be silently defeated, writing content to unexpected locations — i.e., silent corruption of files the guard was supposed to protect.

### Likelihood Explanation
Exploiting this requires a coincidental (but not rare) directory-naming collision on the victim's filesystem — sibling directories that share a common prefix with the repository path are a very ordinary occurrence (backups, forks, versioned checkouts). No admin rights, local malware, or leaked credentials are required; the trigger is simply the user clicking a maliciously crafted GitHub Desktop deep link containing a `filepath` parameter, which is squarely within the accepted "link the user clicks" attacker model.

### Recommendation
Change the containment check in `_resolveWithin` (`app/src/lib/path.ts`) to require an exact match or a match followed by the platform path separator, e.g.:
```
return realResolved === realRoot || realResolved.startsWith(realRoot + sep)
  ? resolved
  : null
```
using the `sep` from the same `options` object (`Path.sep` / `Path.win32.sep` / `Path.posix.sep`) that's passed in, and add a regression test using two directories that share a common name prefix but are not nested (e.g. `root` and `root-evil`) to lock in the fix.

### Citations

**File:** app/src/lib/path.ts (L64-71)
```typescript
  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
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
