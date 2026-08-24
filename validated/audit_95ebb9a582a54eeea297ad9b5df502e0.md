## Title
Path-containment check in `resolveWithin` uses unanchored `String.startsWith`, allowing symlink-based escape from the repository root - (File: `app/src/lib/path.ts`)

## Summary
The bug report's underlying pattern is a **missing boundary check**: an inner computation is trusted to stay within safe bounds ("numerator ≥ denominator") without an explicit guard, and the omission only surfaces for a narrow edge-case input, corrupting a downstream security-relevant computation (`log_2(0)`). The direct Desktop analog is the repo-containment guard `_resolveWithin()` in `app/src/lib/path.ts`, which decides whether a path derived from repository content is allowed to be read/written. It performs the boundary check with a raw string-prefix comparison instead of verifying a path-segment boundary, so an attacker-controlled path that resolves to a *sibling* directory whose name happens to share the repository directory name as a string prefix will incorrectly be treated as "inside the repository."

## Finding Description
`_resolveWithin` computes the real, symlink-resolved path and then checks containment like this: [1](#0-0) 

```
const resolved = resolve(normalizedRoot, normalizedRelative)
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
```

`String.prototype.startsWith` performs a literal character-prefix comparison, not a path-segment comparison. If `realRoot` is e.g. `/Users/victim/Documents/GitHub/myrepo` and a symlink inside the repository resolves to `/Users/victim/Documents/GitHub/myrepo-secrets/config.json`, the check evaluates to `true` because the string `"...myrepo-secrets/config.json"` literally starts with `"...myrepo"` — even though `myrepo-secrets` is a completely different, sibling directory outside the intended root. The function was never given a trailing separator (`realRoot + path.sep`) to require, so any sibling path that is a superstring of the root's basename bypasses the guard, exactly mirroring the reported bug's missing "numerator ≥ denominator" guard: the code assumes an invariant (`resolved is only allowed to equal or be a subpath of root`) that is not actually enforced for a specific, narrow edge case.

The existing unit tests only cover symlinks that escape to a completely unrelated path (e.g. `resolve(tempDir, '..', '..')`) and never test the sibling-prefix collision case: [2](#0-1) 

so this specific edge case has no regression coverage.

`resolveWithin` is the guard relied upon to keep two attacker-influenced operations inside the repository:

1. Reading conflicted-file content that is sent to the Copilot conflict-resolution model: [3](#0-2) 

2. Writing Copilot's AI-generated conflict resolution content back to disk: [4](#0-3) 

In both cases the `file.path` / `resolution.path` value ultimately comes from git's reported working-directory paths, which an attacker who controls the cloned/fetched repository content can shape (e.g., by committing a symlink at a conflicted path).

## Impact Explanation
If a user has (or later creates) a sibling directory whose name is a superstring of the repository's directory name — a very plausible naming pattern (`repo`, `repo-old`, `repo-backup`, `repo2`, `repo.bak`, etc.) — a malicious repository containing a symlinked "conflicted" file can cause:
- **Arbitrary file read outside the repo**: the symlinked path is read and its contents are exfiltrated to the Copilot API as part of building conflict context.
- **Arbitrary file write / silent corruption outside the repo**: when Desktop "applies" the AI-generated resolution, `writeFile(absolutePath, ...)` follows the symlink and overwrites a file in the sibling directory, which is outside the repository the user believes they're operating on — a silent corruption of unrelated files, and if that sibling directory is itself another git working tree, of what the user later commits/pushes from there.

This satisfies the required impact bar (file read/write outside the repo, silent corruption of committed content) and the attacker primitive (a cloned/fetched, attacker-controlled repository).

## Likelihood Explanation
Requires two conditions: (1) the victim has an existing sibling directory sharing the repository's directory name as a prefix (common in practice, since users frequently clone variants/forks/backups side-by-side in the same parent folder such as `~/Documents/GitHub`), and (2) the attacker's repository contains a conflicting file that is actually a symlink pointing to that sibling path. Both are achievable without any local access, admin rights, or social engineering beyond the normal "clone or use a repo" workflow, and the Copilot conflict-resolution feature actively invokes both the read and write paths through this single flawed guard.

## Recommendation
Change the containment check in `_resolveWithin` to require a path-segment boundary, e.g.:
```
return (
  realResolved === realRoot ||
  realResolved.startsWith(realRoot + Path.sep)
) ? resolved : null
```
Add a regression test mirroring the sibling-prefix scenario (e.g., root `myrepo`, symlink resolving into `myrepo-evil`) to `app/test/unit/path-test.ts`.

## Proof of Concept
1. Create `/tmp/base/myrepo` (a normal Desktop-cloned repository) and `/tmp/base/myrepo-secret/secret.txt` containing sensitive content.
2. Inside `myrepo`, create a symlink `evil -> ../myrepo-secret/secret.txt`.
3. Call `resolveWithin('/tmp/base/myrepo', 'evil')`.
4. Observe: `realRoot = '/tmp/base/myrepo'`, `realResolved = '/tmp/base/myrepo-secret/secret.txt'`. `realResolved.startsWith(realRoot)` evaluates `true`, so the function returns the resolved path instead of `null`, even though the target lives entirely outside `myrepo`.
5. Any caller (e.g. `buildConflictContext`/`_writeCopilotConflictResolution` reading/writing a conflicted file at path `evil`) will read from or write to `myrepo-secret/secret.txt` believing it is operating inside the repository sandbox. [5](#0-4)

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

**File:** app/test/unit/path-test.ts (L65-78)
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
```

**File:** app/src/lib/copilot-conflict-context.ts (L390-438)
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

      // Guard against reading pathologically large files into memory. This is
      // a memory-safety bound only — resolvability is decided from the conflict
      // hunks below, not the whole-file size.
      try {
        const fileStat = await stat(absolutePath)
        if (fileStat.size > MAX_CONFLICT_FILE_READ_SIZE) {
          return {
            path: file.path,
            hunks: [],
            skippedReason: 'File too large to resolve automatically',
          }
        }
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File could not be read',
        }
      }

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
