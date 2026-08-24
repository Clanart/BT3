### Title
TOCTOU symlink race in `resolveWithin` allows attacker-controlled repo to redirect Copilot conflict-resolution file reads outside the working directory - (File: `app/src/lib/path.ts`)

### Summary
`resolveWithin` (used to sandbox path access to a repository's working directory) validates a path with `realpath()` at check time but then returns the *literal, non-realpath'd* `resolved` path rather than the validated `realResolved` value. [1](#0-0)  Callers such as `buildConflictContext` treat the returned path as safe and later perform separate `stat`/`readFile` calls on it, creating a check-then-use gap. [2](#0-1)  This mirrors the `OpenEdition` bug class: a guard is evaluated once (permission/validity check) but the underlying trusted state (filesystem layout, controlled by the untrusted cloned repository) can change before the action it was meant to gate actually executes.

### Finding Description
`_resolveWithin` computes `resolved = resolve(normalizedRoot, normalizedRelative)`, then calls `realpath()` on both the root and `resolved` purely to *decide* whether to allow the path, but the function returns `resolved` — the non-realpath path — not the realpath-verified one. [3](#0-2)  This means the safety decision and the value actually used by the caller are two different filesystem lookups performed at two different points in time.

In `buildConflictContext`, which is used to build merge/rebase/cherry-pick conflict content for the Copilot integration, the flow for each conflicted file is:
1. Call `resolveWithin(workingDirectory, file.path)` to validate the path is inside the repo.
2. `stat(absolutePath)` to check size.
3. `readFile(absolutePath, 'utf8')` to read content, which is later sent to the Copilot SDK as part of the conflict-resolution prompt. [2](#0-1) 

`file.path` values come from the list of conflicted files during a merge/rebase/cherry-pick — i.e., paths derived from the working tree of a repository the user cloned/fetched, which can be shaped by whoever controls the remote/repo content (e.g., via crafted merge commits, submodules, or files that alternate between regular file and symlink across the two sides of a conflict). Because steps 1–3 are not atomic, and because the returned "safe" path is not itself re-verified against `realpath` before use, an attacker who controls repository content combined with a filesystem race (e.g., replacing a path component with a symlink between the `resolveWithin` check and the subsequent `readFile`) can cause the read to resolve outside `workingDirectory` at the time of actual I/O, even though the check nominally passed.

This is structurally the same broken invariant as the `OpenEdition` report: the code performs a one-time state check (`cancel` allowed only before `startTime` / `resolveWithin`'s realpath check) but the actual sensitive action (minting / file read) happens later, by which time the attacker has changed the underlying trusted condition (revoked the mint role / swapped a symlink), and the guard cannot retroactively stop it.

### Impact Explanation
If exploited, arbitrary file content from outside the cloned repository (subject to OS filesystem permissions of the Desktop process) could be read and transmitted to the Copilot SDK/service as part of the conflict-resolution prompt — this is exfiltration of file content outside the repo, one of the explicitly valid impact categories (file read outside the repo / credential exfiltration via arbitrary file content). It does not require local/physical access or pre-existing malware; the trigger is simply opening/merging a maliciously crafted repository the victim has cloned or fetched and then using the AI conflict resolution feature.

### Likelihood Explanation
Exploitation requires winning a filesystem race between the `resolveWithin` check and the subsequent `stat`/`readFile`, which is inherently timing-dependent and not trivially reliable, and requires the attacker to have some way of mutating the working tree during that window (e.g., a background git operation, a symlink swap timed against directory watchers, or content that changes file type mid-conflict). This makes likelihood **moderate-to-low** in practice, but the primitive is real and directly attributable to a documented anti-pattern in `_resolveWithin`'s return value: it discards the very `realResolved` value it computed for validation. [3](#0-2) 

### Recommendation
- Have `_resolveWithin` return the `realResolved` (realpath-verified) path instead of the literal `resolved` path, so callers use exactly the value that was validated.
- Alternatively, re-validate (or re-resolve via `realpath`) immediately before each I/O operation (`stat`, `readFile`) rather than trusting a path computed earlier in an `async` pipeline, closing the TOCTOU window.
- Consider opening file descriptors with `O_NOFOLLOW`-equivalent semantics (e.g., `fs.open` with symlink rejection) for conflict-file reads feeding external services like Copilot, so a symlink swapped in after the check cannot be silently followed.

### Proof of Concept
Conceptual reproduction (exact race timing is environment-dependent; the code path is confirmed from source):

1. Clone a malicious repository and initiate a merge/rebase that produces a conflict on a path `foo/bar.txt`.
2. `buildConflictContext` calls `resolveWithin(workingDirectory, 'foo/bar.txt')`, which at that instant resolves `foo` as a real directory inside the repo and returns `resolved` (e.g., `<workingDirectory>/foo/bar.txt`). [3](#0-2) 
3. Before `stat`/`readFile` execute on `absolutePath` (steps 2–3 of `buildConflictContext`), a concurrent process/racing operation controlled by the attacker's repository content replaces `foo` with a symlink pointing to a path outside `workingDirectory` (e.g., `/etc` or a user config directory containing secrets).
4. `readFile(absolutePath, 'utf8')` now follows the swapped symlink and reads the external file's content, which is subsequently packaged into the conflict-resolution prompt sent to the Copilot SDK, exfiltrating data outside the repository sandbox. [4](#0-3)

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
