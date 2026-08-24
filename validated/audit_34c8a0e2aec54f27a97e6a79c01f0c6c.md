## Analysis

The external report's underlying bug class is: **a security check validates a value at one instant, but the action taken later re-derives/re-resolves that value against live, mutable state — so the state can change between "check" and "use," defeating the guard.** In `MinVotingPowerCondition`, a token balance is checked and then consumed atomically after being artificially inflated. The closest structural analog in this codebase is a **check-then-use (TOCTOU) gap between a symlink-containment check and the actual file read** in the Copilot merge-conflict-resolution pipeline.

`resolveWithin()` in [1](#0-0)  validates containment by calling `realpath()` on the *candidate* resolved path and checking that the canonicalized result starts with the canonicalized root — but it returns the **un-canonicalized, syntactic `resolved` path**, not the checked `realResolved` value.

`buildConflictContext()` then uses that returned syntactic path for `stat()` and `readFile()` — separate syscalls performed **after** the check, at [2](#0-1) . Those syscalls resolve any symlink in the path fresh, against whatever the filesystem looks like at that later moment — not the state that was validated. The file content read this way, `rawContent`, is forwarded unmodified into the Copilot prompt via `formatConflictContextForPrompt()` at [3](#0-2)  and sent to an external AI service.

### Title
Symlink TOCTOU in Copilot conflict-context file reads defeats path-containment guard (`resolveWithin`) - (File: app/src/lib/copilot-conflict-context.ts)

### Summary
`resolveWithin()` checks containment using `realpath()` but returns the pre-canonicalization path, and `buildConflictContext()` performs `stat`/`readFile` on that path in separate, later syscalls. If the on-disk symlink target for a conflicted path changes between the containment check and the read, the guard is bypassed and an out-of-repository file can be read and forwarded to the Copilot prompt.

### Finding Description
`_resolveWithin()` computes `realRoot = realpath(normalizedRoot)` and `realResolved = realpath(resolved)`, and only returns `resolved` (the syntactic, symlink-unaware path) if `realResolved.startsWith(realRoot)`: [1](#0-0) . This means the safety decision is made based on the filesystem state *at check time*, but the returned value that callers subsequently use for I/O does not pin that state — it's just a path string.

`buildConflictContext()` consumes this return value across two more `await` boundaries — `stat(absolutePath)` and then `readFile(absolutePath, 'utf8')` — each of which independently re-resolves any symlinks in `absolutePath` against the live filesystem: [2](#0-1) . There is no `fstat`-then-`fread` on a held file descriptor and no re-verification of containment after the initial check, so nothing prevents the path from resolving to a different, unchecked target by the time the actual read occurs.

This mirrors the report's core defect: a security-relevant value (containment of the path within the repo) is established once and then trusted for a subsequent privileged action (`readFile`), while the actual object backing that value (the symlink target) remains mutable in between — just as `isGranted`'s balance check in `MinVotingPowerCondition` is trusted for a subsequent privileged action (`createProposal`/vote) while the underlying balance remains mutable via flashloan.

### Impact Explanation
If exploited, this allows reading and exfiltrating the contents of a file located anywhere on the user's filesystem that the OS user account can access — including SSH private keys, cloud credential files, or Desktop's own locally cached data — by having its content silently spliced into the Copilot conflict-resolution prompt and transmitted to the Copilot backend, all triggered by opening a merge/rebase/cherry-pick conflict in a maliciously crafted clone. This is a credential/file exfiltration primitive originating from attacker-controlled repository content.

### Likelihood Explanation
Likelihood is low-to-moderate in practice: the attacker needs the on-disk symlink for one of the conflicted paths to change *between* the `resolveWithin` check and the subsequent `stat`/`readFile` calls, which requires a concurrent writer process racing the two awaits. Git does not execute cloned repository hooks automatically, so there is no direct, self-contained way for the malicious repository alone to win this race without some other concurrent process (e.g., a sync client, another git process, or a background job) mutating the path at the right moment. This makes the finding a genuine but narrow TOCTOU rather than a trivially reproducible one-shot exploit.

### Recommendation
- Have `resolveWithin()` return the canonicalized (`realResolved`) path rather than the syntactic `resolved` path, and have all downstream I/O (`stat`, `readFile`) operate on that canonical, already-verified path.
- Alternatively, open the file once (`fs.open`), then use `fstat`/`read` on the resulting file descriptor so the containment check and the read operate on the same inode, eliminating the re-resolution race entirely.
- Consider rejecting symlinks outright for conflicted-file paths before reading them, since legitimate git conflict content is never itself a dangling/foreign symlink target in this flow.

### Proof of Concept
1. Have a background process (e.g., a scheduled task, a sync client, or another instance of git operating on the same working directory) poised to swap a regular file at `repo/config.json` for a symlink pointing to `~/.ssh/id_rsa` immediately after it is read once.
2. Trigger a merge conflict on `repo/config.json` in Desktop and open the Copilot AI conflict resolution flow, causing `buildConflictContext()` to process this file.
3. Between the `resolveWithin(workingDirectory, 'config.json')` call ( [4](#0-3) ) succeeding (because at that instant the path resolves inside the repo) and the subsequent `stat`/`readFile` calls, swap the symlink to point at `~/.ssh/id_rsa`.
4. `readFile(absolutePath, 'utf8')` now reads `~/.ssh/id_rsa` content instead of the repo file, and that content is embedded verbatim into the Copilot prompt ( [3](#0-2) ) and transmitted off-device.

### Citations

**File:** app/src/lib/path.ts (L66-71)
```typescript
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

**File:** app/src/lib/copilot-conflict-context.ts (L571-583)
```typescript
      parts.push('Ours (current branch):')
      parts.push(makeFencedBlock(hunk.oursContent, lang))
      parts.push('')

      if (hunk.baseContent !== null) {
        parts.push('Base (common ancestor):')
        parts.push(makeFencedBlock(hunk.baseContent, lang))
        parts.push('')
      }

      parts.push('Theirs (incoming branch):')
      parts.push(makeFencedBlock(hunk.theirsContent, lang))
      parts.push('')
```
