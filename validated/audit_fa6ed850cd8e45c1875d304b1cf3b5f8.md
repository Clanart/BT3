Found a concrete, code-evidenced analog. The StRSR bug is fundamentally a **boundary/containment check that is subtly incomplete**, letting an attacker slip past a guard that looks correct at first glance. GitHub Desktop has the same class of bug in its path-containment primitive `resolveWithin`, which gates several attacker-reachable write/read operations.

### Title
Path-containment check in `resolveWithin` uses a raw `String.startsWith` prefix test, allowing writes/reads to escape the repository into sibling-named directories - (File: app/src/lib/path.ts)

### Summary
`resolveWithin` (and its `Win32`/`Posix` variants) is the sole guard Desktop uses to ensure a repository-relative path — including paths taken from git conflict metadata, deep-link file paths, and AI-generated conflict resolutions — stays inside the repository root before the app reads or writes to it. The final containment test is a plain string prefix comparison with no path-separator boundary check, so a resolved path in a sibling directory whose name happens to start with the repository's directory name (e.g. `…/repo-secrets/`) is incorrectly treated as "inside" `…/repo`.

### Finding Description
`_resolveWithin` in [1](#0-0)  computes:

```
const resolved = resolve(normalizedRoot, normalizedRelative)
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
```

`realResolved.startsWith(realRoot)` is true whenever `realResolved` begins with the exact character sequence of `realRoot`, even if the next character is not a path separator. If the repository lives at `/Users/alice/project` and a sibling directory `/Users/alice/project-secrets` (or `/Users/alice/project.bak`, `/Users/alice/project-old`, etc.) exists on disk — a very common pattern for backups, forks, or related checkouts — then a symlink placed inside `project` that resolves to `/Users/alice/project-secrets/anything` passes the check and is returned as a "safe" absolute path.

This primitive is the only gate protecting several attacker-reachable sinks:
- The Copilot merge-conflict auto-resolution writer, which calls `resolveWithin(repository.path, resolution.path)` and then `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` using a conflicted file path taken from git status/AI output [2](#0-1) .
- The conflict-context reader that feeds file content to the Copilot SDK, using the same `resolveWithin` guard before `readFile` [3](#0-2) .
- The `x-github-desktop://openRepo` deep-link handler, which resolves a user/attacker-supplied `filepath` query parameter against the repository root before calling `shell.showItemInFolder` [4](#0-3) .

The existing unit tests only exercise symlinks that escape entirely outside the root, or traverse out-and-back into the same root; they never test the "sibling with same prefix" case, so this gap is unexercised [5](#0-4) .

### Impact Explanation
An attacker who controls a cloned/fetched repository (e.g. a malicious branch a victim merges/rebases, or a repo the victim opens via a crafted deep link) can commit a symlink at a conflicted file path that targets a plausibly-named sibling directory outside the real repository root. When Desktop's Copilot conflict-resolution flow writes its AI-generated resolution to that path, the write follows the symlink and lands outside the repository — a file write outside the repo under attacker influence over both the destination (crafted symlink) and, to a degree, the content (the AI resolution derived from attacker-controlled conflict text). This is the same class of impact called out as valid: "file write... outside the repo" and "silent corruption of what the user commits."

### Likelihood Explanation
Exploitation requires the victim to have a real, pre-existing directory alongside the repository whose name shares the repository directory name as a prefix (e.g. `repo` and `repo-backup`) — a common but not universal setup for developers who keep backups/forks side by side. It also requires the victim to invoke a flow that both reads/writes through `resolveWithin` and is triggered by attacker-controlled repository content (merge/rebase conflict from an attacker branch, or a crafted deep link). This mirrors the original StRSR finding's judged severity: a boundary condition that is unsafe only in a bounded, situational window, but whose existing guard code gives a false sense of safety (`resolveWithin` looks like proper containment but silently permits the sibling-prefix case).

### Recommendation
Fix `_resolveWithin` in `app/src/lib/path.ts` to require a path-separator boundary (or exact equality) after the prefix match, e.g.:
```
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep) ? resolved : null
```
Add a regression test covering a sibling directory that shares the root's name as a prefix (`root` vs `root-secret`) to lock in the fix, alongside the existing symlink-escape tests in `app/test/unit/path-test.ts`.

### Proof of Concept
1. On disk: `/Users/alice/project` (the git repo Desktop has open) and `/Users/alice/project-secrets/leak.txt` (any directory sharing the `project` prefix).
2. Attacker crafts a branch where the file `notes.txt` is, on their side, a symlink to `../project-secrets/leak.txt`; the victim's side has `notes.txt` as a normal file with different content, producing a merge conflict.
3. Victim merges the attacker's branch in Desktop and uses Copilot-assisted conflict resolution; Desktop stages the conflicted file and, at some point, `notes.txt` in the working tree is (or becomes, via checkout of the "theirs" side) the symlink.
4. When `_applyCopilotConflictResolutions` calls `resolveWithin(repository.path, 'notes.txt')`, `realpath` resolves through the symlink to `/Users/alice/project-secrets/leak.txt`, which `startsWith('/Users/alice/project')` — passing the containment check — and the function proceeds to `writeFile` the AI-generated resolution content into `/Users/alice/project-secrets/leak.txt`, overwriting a file physically outside the intended repository root. [6](#0-5)  is the exact code responsible; [2](#0-1)  is the concrete attacker-reachable sink that trusts its return value without further boundary validation.

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

**File:** app/test/unit/path-test.ts (L65-100)
```typescript
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
```
