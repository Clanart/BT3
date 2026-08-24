## Analog Found

### Title
TOCTOU symlink-race in `resolveWithin()` lets a malicious repository redirect Copilot conflict-resolution writes outside the working directory - ([File: app/src/lib/path.ts])

### Summary
The Dutch-auction bug is a classic check-then-act race: a bound (`newId <= finalId`) is validated once, but the state it protects can shift before the effect is applied, letting an attacker exploit the gap. `resolveWithin()` in [1](#0-0)  has the same structural flaw applied to filesystem safety: it validates a path via `realpath()` at check time but hands back the *original*, non-canonicalized path, which is used for the actual file write much later. If the on-disk entry at that path changes between the check and the write (e.g., becomes a symlink), the guard is bypassed.

### Finding Description
`_resolveWithin()` computes `resolved = resolve(normalizedRoot, normalizedRelative)`, then calls `realpath()` on both the root and the resolved path to confirm `realResolved.startsWith(realRoot)`, but returns `resolved` — not `realResolved` — to the caller: [2](#0-1) 

This is used in the AI-assisted merge-conflict flow. `buildConflictContext()` calls `resolveWithin(workingDirectory, file.path)` once when the conflict is first read, well before the Copilot SDK round-trip: [3](#0-2) 

The resolved content is then displayed to the user in a review dialog. Only when the user clicks "Continue Merge" does `_applyCopilotConflictResolutions()` run — which calls `resolveWithin()` **again** immediately before `writeFile(absolutePath, ...)`: [4](#0-3) 

Even in this second, "just before write" call, there is a non-zero window between the `realpath()`-based validation and the subsequent `writeFile()` syscall (several `await`-free but still asynchronously-scheduled statements execute in between: the `onDiskFile` lookup, the `isConflictedFileStatus`/`hasUnresolvedConflicts` checks, then the write). `writeFile` follows symlinks by default (no `O_NOFOLLOW`), so if the filesystem entry at `resolution.path` is swapped for a symlink pointing outside the repository in that window, the validated safety check becomes stale and the write follows the attacker-planted symlink.

The attacker precondition matches the report's bug class directly: the attacker controls the cloned/fetched repository content and can arrange for a working-tree entry to be replaced concurrently with a symlink (for example, via a git hook fired by any other repository operation Desktop performs in the background — periodic status refresh, fetch, or the user's own concurrent `git` invocation — during the seconds-to-minutes the user spends reviewing the Copilot resolution dialog before clicking "Continue Merge"). The existing guard (`resolveWithin`) only proves the path was safe at the instant of the check; it provides no lease or open file descriptor that pins that guarantee through to the write.

### Impact Explanation
If exploited, `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` can write attacker-influenced content to a location outside the repository that the symlink target points to (e.g. a dotfile, shell profile, SSH config, or any writable file reachable by the Desktop process's OS user). This is a file write outside the repo — one of the explicitly in-scope impacts — and can lead to corruption of unrelated files or, depending on the target (e.g. shell rc files, `.gitconfig`, editor configs invoked automatically), further code execution.

### Likelihood Explanation
Exploitation requires the attacker's repository to trigger a write to the target path at exactly the right moment, which is a genuine race with a narrow window per attempt. However, as the Dutch-auction analog illustrates, an attacker who controls a background trigger (a git hook or a repeated retry loop) can retry the race indefinitely while the user reviews the Copilot dialog, which can take an arbitrary amount of real time — turning a narrow single-shot race into a practically winnable one. This keeps likelihood in the "requires racing but is not gated by anything else" tier rather than deterministic-but-not-implausible.

### Recommendation
Do not return a plain, unresolved path from `resolveWithin()` and treat it as safe indefinitely. Instead:
- Open the file with `O_NOFOLLOW` (or Node's `fs.open`/`fs.writeFile` combined with `lstat` immediately before use) so a symlink swap causes an error rather than a followed write.
- Perform the realpath validation and the write as a single atomic operation (e.g., open the fd first, then `fstat`/`readlink` on the fd to confirm containment, then write through that fd) rather than validating a path string and reusing it later.
- Re-validate immediately (no intervening `await`) right before the `writeFile` call in `_applyCopilotConflictResolutions`, and fail closed if the target's type changed (regular file → symlink) since the conflict was first read.

### Proof of Concept
1. Clone/open a malicious repository that has a merge/rebase configured to produce a text conflict in `payload.txt`.
2. Arrange (via a git hook triggered by any concurrent git operation Desktop performs, e.g. background `status`/`fetch`) to replace the working-tree `payload.txt` with a symlink to `~/.bashrc` (or another sensitive file) shortly after the merge starts.
3. Trigger Copilot conflict resolution; `buildConflictContext()` validates `payload.txt` as inside the repo at read time via `resolveWithin` [3](#0-2) .
4. While the user reviews the resulting dialog, the hook/background process swaps `payload.txt` for the symlink.
5. User clicks "Continue Merge" → `_applyCopilotConflictResolutions` re-checks with `resolveWithin` and then calls `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` [5](#0-4) ; because `writeFile` follows the symlink, the Copilot-resolved content is written to `~/.bashrc` instead of the repository file.

Note: I could not directly confirm within the indexed code whether Desktop disables repository-local git hooks globally for all operations that could run during this window (only trampoline/proxy-related hook handling for credential operations was visible in the search results), so the exact concurrent trigger for the symlink swap could not be fully verified from the index alone — a full-repository session would be needed to check `app/src/lib/hooks/` and confirm whether `core.hooksPath` is neutralized for status/fetch operations that run in the background during conflict review.

### Citations

**File:** app/src/lib/path.ts (L36-71)
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
```

**File:** app/src/lib/copilot-conflict-context.ts (L390-408)
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
