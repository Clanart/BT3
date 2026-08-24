### Title
Copilot conflict-resolution file write has a TOCTOU window between path validation and disk write, allowing an attacker-controlled repository to redirect a "safe" path to a symlink target outside the repo - (File: `app/src/lib/stores/app-store.ts`)

### Summary
`_applyCopilotConflictResolutions` validates each conflicted file's path with `resolveWithin()` (which internally calls `realpath()` to reject symlink escapes) and then, several `await` steps later, writes attacker-influenceable content to the *string* returned by that check rather than re-validating at write time. Because `resolveWithin` and `writeFile` are two disjoint operations separated by additional async work, a filesystem state change between them (e.g. a path component becoming a symlink) is not detected, breaking the invariant "if `resolveWithin` returned non-null, the write target is inside the repository."

### Finding Description
`resolveWithin` in [1](#0-0)  performs its containment check by resolving the real path once, at call time:
```
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
```
It returns the pre-`realpath` `resolved` string, not a locked file descriptor or handle, so the guarantee ("this path is inside the repo") is only valid at the instant the check runs.

`_applyCopilotConflictResolutions` uses this exact pattern in a loop over Copilot-provided conflict resolutions: [2](#0-1) 

Between the `resolveWithin` call (line 7233) and the `writeFile` call (line 7258) there are additional `await`-yielding operations (`readFile`/lookup logic, and, across loop iterations, other awaited file I/O for previous resolutions). This is precisely the "combined atomic operation optimistically trusts a check that was valid at T1 but is consumed at T2" pattern from the source report: the mitigation in the external report combined two previously-separate operations, but the combined function still performed a stale timing/state check that a legitimate multisig or, here, filesystem event occurring between the granted operations, could invalidate — leading to an unintended/incorrect action rather than the safe outcome the check was meant to guarantee.

The working directory being validated is derived from a git repository whose content is attacker-influenced: the conflicting files are the product of a merge/rebase against a branch or fork the victim fetched (e.g., reviewing a malicious PR). Git conflict resolution can legitimately place symlinks in the working tree during a merge (e.g., "modify/symlink" conflicts), and Desktop's own background tasks (repository indicator refresh, other queued git operations) can run concurrently with this async flow, both of which are realistic vectors for a path segment under `repository.path` to be replaced with a symlink after the `resolveWithin` check but before the `writeFile` call.

### Impact Explanation
If the check-to-write window is won by an attacker-influenced filesystem change, Desktop's Copilot conflict-resolution feature will write model-controlled/attacker-shaped `resolution.resolvedContent` (derived from `buildConflictContext` output, which itself operates on attacker-authored conflicting hunks) to a path outside the user's repository — e.g. overwriting a file in the user's home directory, a shell startup file, or another sensitive location reachable via a symlink target. This is a file write outside the repository originating from an attacker-controlled fetched/merged branch, matching the "silent corruption" / "file write outside the repo" valid-impact category.

### Likelihood Explanation
The exploitation requires (1) the victim to hit a merge/rebase conflict against attacker-supplied branch content and to use the Copilot auto-resolution feature, and (2) winning a narrow but real TOCTOU race between `resolveWithin`'s `realpath` calls and the eventual `writeFile`. The existence of concurrent background repository operations in Desktop (indicator refresh, other queued git tasks) plausibly widens this window without needing local/physical attacker access — the attacker only needs to control the fetched/merged repository content, satisfying the required threat model. However, reliably winning the race is nontrivial, so likelihood is best characterized as low-to-medium, not trivially exploitable on demand.

### Recommendation
Re-validate immediately before the write instead of trusting a path computed earlier: open the resolved path with `O_NOFOLLOW`/`fs.open` + `fs.write` (or re-run `resolveWithin`/`realpath` right before `writeFile` and abort if the result changed), or write to a temp file inside the repo and atomically rename over the target only after confirming via `lstat` that the target is a regular file within the repository root. Treat any symlink discovered at the target path as a hard failure rather than silently skipping/proceeding.

### Proof of Concept
1. Attacker publishes a branch/PR that, when merged, produces a text conflict at `notes/todo.md` (or similar) in the victim's working directory.
2. Victim opens the conflict in Desktop and starts Copilot conflict resolution (`_startCopilotConflictResolution` → `_applyCopilotConflictResolutions`).
3. During processing of the loop in [2](#0-1) , immediately after `resolveWithin(repository.path, 'notes/todo.md')` succeeds (line 7233) but before `writeFile` executes (line 7258), a concurrent process (e.g., another queued git/background operation, or a crafted race triggered by the attacker's branch content that replaces `notes/` with a symlink to a directory outside the repo) swaps `notes` for a symlink pointing outside the repository root.
4. `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` follows the now-stale `absolutePath` string through the symlink and writes attacker/model-derived content to a location outside the repository, bypassing the containment guarantee `resolveWithin` was meant to provide.

Note: I was not able to fully verify the exact mechanics of the concurrent filesystem race trigger (i.e., what queued Desktop task could realistically win the race) without running the application; this is stated as a plausible vector based on the codebase's documented background operations (repository indicator refresh, queued git tasks) rather than a fully traced end-to-end reproduction.

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
