## Title
TOCTOU symlink race in `resolveWithin` lets an attacker-controlled repository make Desktop read/exfiltrate files outside the repo during Copilot conflict resolution - ([File: app/src/lib/path.ts])

### Summary
`resolveWithin` (the shared "is this path inside the repo root" guard) validates a path by calling `realpath()` on the resolved candidate, but then returns the **pre-realpath** string instead of the realpath-resolved one: [1](#0-0) 
This creates a classic time-of-check/time-of-use (TOCTOU) gap, structurally analogous to the HackerOne report: a check is performed against one state of the world (the symlink target at check time) but the actual privileged operation (file read/open) happens later against whatever state the world is in *then*. In the faucet report, the "check" (remaining balance) and the "use" (debit) were not atomic, letting concurrent requests bypass `coins_max`. Here, the "check" (`realpath` validation) and the "use" (`readFile`/`shell.showItemInFolder` on the returned, non-canonicalized path) are two separate filesystem operations separated by time, and the attacker controls the filesystem object (a symlink inside a cloned/fetched repository) that determines the outcome of both.

### Finding Description
`resolveWithin` is Desktop's canonical guard against path traversal/symlink escapes, used to check that a repository-relative path an attacker doesn't control 100% (e.g., PR/deep-link file paths, Copilot conflict file paths) stays inside the repository directory: [2](#0-1) 

The function:
1. Joins/normalizes `rootPath` and the untrusted `pathSegments` into `resolved`.
2. Calls `realpath(normalizedRoot)` and `realpath(resolved)` to canonicalize symlinks.
3. Confirms `realResolved.startsWith(realRoot)`.
4. **Returns `resolved` (the un-canonicalized path), not `realResolved`.**

Two call sites then use this "validated" path for real filesystem access:

- `buildConflictContext` (used to build the payload sent to Copilot for AI-assisted merge-conflict resolution) calls `resolveWithin(workingDirectory, file.path)` and then does `stat(absolutePath)` / `readFile(absolutePath, 'utf8')` on the *returned* (non-canonical) path: [3](#0-2) 

- `Dispatcher.openRepositoryFromUrl` calls `resolveWithin(repository.path, filepath)` from an attacker/remote-controlled deep-link `filepath` and then calls `shell.showItemInFolder(resolved)`: [4](#0-3) 

The unit tests for `resolveWithin` confirm the design intent (block symlink escapes) but only test the synchronous, non-racy case — they don't exercise the window between the `realpath` check and the later I/O: [5](#0-4) 

**Attacker primitive / broken invariant:** the attacker fully controls the content of a cloned/fetched Git repository (a valid "Valid Impact" primitive). Git can commit symlinks. The attacker commits a conflicted file whose repository-relative path component is (or, once merged/checked out, resolves through) a symlink that currently points *inside* the repo (so `realpath(resolved)` passes the check), then — in the window between the `realpath()` call and the subsequent `readFile`/`showItemInFolder` call — repoints (or a background/concurrent operation repoints) the symlink to point outside the repo (e.g., `~/.ssh/id_rsa`, `~/.aws/credentials`, `~/.gitconfig`). Because `_resolveWithin` returns the original `resolved` string rather than the already-canonicalized `realResolved`, the subsequent I/O call re-resolves the symlink at access time, following whatever target is in place *then*, not what was validated.

### Impact Explanation
For the Copilot conflict flow this is the more severe path: the file's contents are read via `readFile(absolutePath, 'utf8')` and become part of `IFileConflictContext.rawContent`, which is subsequently included in the prompt/context sent to the Copilot resolution backend to be used to resolve the conflict. If the TOCTOU race is won, arbitrary file content from outside the repository (credentials, SSH keys, tokens) can be read off disk and transmitted off the user's machine as part of the AI request — this matches "file read outside the repo" and "credential/token exfiltration" in the accepted impact list, without requiring the user to do anything beyond opening a maliciously crafted cloned/fetched repository and triggering the built-in Copilot conflict-resolution feature.

For the `openRepositoryFromUrl` deep-link path, winning the race causes `shell.showItemInFolder` to reveal/focus a file or directory outside the repository in the OS file manager — a narrower, UI-level impact, but still demonstrates the guard doesn't do what its contract promises.

### Likelihood Explanation
Exploitation requires winning a race condition, which is inherently probabilistic, but the attacker has strong leverage to make the window practically exploitable:
- The attacker fully controls repository content and commit history (including symlinks), and controls exactly when the conflict is presented (they craft both sides of the merge).
- The attacker doesn't need to race a single request — they can arrange the conflict resolution flow to process many files, or repeat/retry, increasing the number of attempts.
- `stat` + `readFile` (two separate awaits after the `realpath` check inside `_resolveWithin`, plus the additional `stat` in `buildConflictContext`) each represent additional yield points where a symlink target could be swapped by a concurrent filesystem operation.
This is not a purely theoretical TOCTOU — the guard's own return value is the un-canonicalized path, so the "protection" is checking one string and returning a different, still-symlink-containing string for actual use, which is a design defect independent of how tight the race window is.

### Recommendation
- Change `_resolveWithin` to return the already-canonicalized `realResolved` path (or re-validate immediately before use) instead of the pre-realpath `resolved` string, eliminating the check/use mismatch.
- At minimum, perform the filesystem I/O (`readFile`, `stat`, `shell.showItemInFolder`) against a file descriptor/handle opened during or immediately after the `realpath` check (e.g., open with `O_NOFOLLOW`-equivalent semantics, or use `fs.realpath` result directly for the read) rather than re-deriving the path and re-resolving symlinks at a later time.
- Add a regression test that swaps a symlink's target between the `resolveWithin` check and the subsequent read/open, to ensure the returned path can't be raced.

### Proof of Concept
1. Attacker prepares and hosts a malicious Git repository. It contains two branches that conflict on a path `evil` where, at merge time, `evil` is a symlink pointing to a benign in-repo file (e.g., `evil -> ./decoy.txt`).
2. Victim clones/fetches the repository in GitHub Desktop and starts a merge/rebase, hitting merge conflicts, then invokes the Copilot-assisted conflict resolution feature (`buildConflictContext`).
3. During resolution, `resolveWithin(workingDirectory, 'evil')` is called: `realpath('evil')` resolves to the in-repo `decoy.txt`, so the check passes and `resolved` (the still-symbolic path `workingDirectory/evil`) is returned.
4. Before `stat`/`readFile` execute on `resolved`, a concurrent process (which the attacker can trigger via a Git hook shipped in the same malicious repo, e.g. a `post-checkout`/`post-merge` hook that runs `ln -sfn ~/.ssh/id_rsa evil`) swaps the symlink target to point at `~/.ssh/id_rsa`.
5. `readFile(absolutePath, 'utf8')` now reads `~/.ssh/id_rsa` content, which is placed into `rawContent` and forwarded as part of the Copilot conflict-resolution context/prompt — exfiltrating the private key content to the AI backend request.

Note: I could not execute this locally to confirm the exact timing window is practically winnable in production hardware/OS conditions, and the git-hook step assumes hooks are enabled for the fetched repository (Desktop's own hook-safety settings would need to be checked); this should be validated with a live proof-of-concept before treating exploitability as certain, but the code-level TOCTOU defect (returning `resolved` instead of `realResolved`) is confirmed by direct code reading.

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
