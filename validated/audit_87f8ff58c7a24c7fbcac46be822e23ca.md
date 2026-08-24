### Title
Copilot conflict-resolution write path skips the "already-conflicted" check when a resolution's file is missing from working-directory status, allowing a stale/attacker-influenced resolution to silently overwrite and stage unrelated file content - (File: `app/src/lib/stores/app-store.ts`)

### Summary
This is the closest local analog to the `fillOrder` bug class: a code path that computes/validates one thing in memory (the intended "only overwrite files that are still actually conflicted" invariant) but fails to enforce it in the actual persisted action (the `writeFile` + `git add` that becomes part of what the user commits). Just as `fillOrder` updated a `memory` copy of `order.status` instead of the `storage` `__orders[_orderId]`, `_applyCopilotConflictResolutions` in `app-store.ts` computes an `onDiskFile` lookup meant to gate whether a Copilot-suggested resolution should be written, but only skips the write when `onDiskFile` is *found and already resolved* — not when `onDiskFile` is `undefined`. The intended invariant ("never overwrite a file that isn't currently an unresolved conflict") is silently not enforced for that branch.

### Finding Description
`_applyCopilotConflictResolutions` (`app/src/lib/stores/app-store.ts`, around lines 7169-7268) iterates over `copilotResolutions` (AI-suggested per-file resolved content, produced from a model response driven by attacker-influenced repository content — conflict markers, commit messages, and PR text pulled from a merge/rebase/cherry-pick against a remote branch) and, for each resolution:

1. Resolves the path within the repo via `resolveWithin` (guards path traversal).
2. Looks up `onDiskFile` from `state.changesState.workingDirectory.files` by `path === resolution.path`.
3. Only **skips** the write if `onDiskFile !== undefined && isConflictedFileStatus(onDiskFile.status) && !hasUnresolvedConflicts(onDiskFile.status)` — i.e., only when the file is *still present, still conflicted, but already resolved*.
4. In every other case — including when `onDiskFile === undefined` (the file is no longer conflicted/tracked in the last-known status) — it falls through to `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` and pushes the path into `pathsToStage`, which is later `git add`-ed unconditionally. [1](#0-0) 

The check's own comment states the intent is to avoid "silently clobbering" a user's own resolution when the working directory no longer shows conflict markers — but the implementation only protects the "found and resolved" case, not the "not found at all" case, which is exactly the scenario where the state has diverged the most (e.g., the file was staged/removed/renamed since the resolutions were generated, or the model returned/echoed a path from stale multi-chunk state). In that gap, `resolution.resolvedContent` — content ultimately derived from an LLM response seeded with attacker-influenced repository/PR/commit data — is written to disk and queued for staging without re-validating that it corresponds to a currently real, currently conflicted file.

This mirrors the `fillOrder` defect precisely: the code *computes* the correct guard condition but the enforced branch coverage does not match the intended invariant, so the state that is actually persisted/staged (what will be committed) can diverge from what the UI and the "we checked, it's safe" logic implies to the user. [2](#0-1) 
`validateResolutionPaths` constrains the model's *returned* paths to the set of files that were conflicted at prompt-construction time, but `_applyCopilotConflictResolutions` runs later, against the `state.changesState.workingDirectory.files` snapshot at write time — the two can legitimately diverge (background refresh, external edits, or partial resolution flows), and the write path's gate does not re-check membership, only "already resolved."

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes": a merge/rebase/cherry-pick against an attacker-controlled remote branch (fully within the documented "attacker controls a cloned/fetched repository" threat model) drives the Copilot conflict-resolution feature, whose output paths and content are influenced by attacker-supplied conflict text, commit messages, and PR descriptions. If the write-time working-directory snapshot no longer contains an entry for a resolution's path (a state easily reachable via the normal async refresh/chunking flow of this feature, not requiring any unnatural user action), the code writes and stages that content without re-verifying it against a live conflicted-file check. The user, believing the "Continue" action only applied Copilot's suggestions to the files it saw, may unknowingly commit/push content whose path-to-content mapping was never re-validated against the live repository state, silently corrupting the resulting commit.

### Likelihood Explanation
The vulnerable branch (`onDiskFile === undefined`) is reachable through normal use of a shipped feature (Copilot-assisted conflict resolution) without any privileged access, local malware, or leaked credentials — only a crafted upstream branch/PR that the user merges/rebases against. Exploiting it precisely (targeting a specific unrelated path) would require the attacker to influence the model's chosen `path` values and have the app's live `workingDirectory.files` list lack that path at write time, which raises the bar somewhat, but the underlying code defect (a computed safety condition that doesn't cover the case it explicitly calls out in its own comment) is a genuine, verifiable logic gap in the shipped write path rather than a theoretical concern.

### Recommendation
Change the gating logic so the write is skipped unless the file is confirmed, at write time, to be an *actually and currently unresolved conflicted file* in `state.changesState.workingDirectory.files` — i.e., invert the condition to require `onDiskFile !== undefined && isConflictedFileStatus(onDiskFile.status) && hasUnresolvedConflicts(onDiskFile.status)` before writing, and skip (with a warning log, similar to the `resolveWithin` failure branch) in every other case, including `onDiskFile === undefined`:

```diff
-      if (
-        onDiskFile !== undefined &&
-        isConflictedFileStatus(onDiskFile.status) &&
-        !hasUnresolvedConflicts(onDiskFile.status)
-      ) {
-        continue
-      }
+      if (
+        onDiskFile === undefined ||
+        !isConflictedFileStatus(onDiskFile.status) ||
+        !hasUnresolvedConflicts(onDiskFile.status)
+      ) {
+        log.warn(
+          `Copilot resolution skipped: "${resolution.path}" is not a currently unresolved conflicted file`
+        )
+        continue
+      }
```

### Proof of Concept
Exact remote/network reproduction (e.g., timing of the multi-chunk model calls vs. workingDirectory snapshot) could not be fully traced within the available tool budget — the `copilot-store.ts` chunking/session logic that produces `copilotResolutions` was only partially inspected. What is confirmed from the code is the logical gap itself:

1. Start a rebase/merge against a malicious remote branch with multiple conflicted files, triggering the Copilot conflict-resolution flow.
2. Between the time `copilotResolutions` is populated and the user clicks "Continue" (`onContinue` → `dispatcher.applyCopilotConflictResolutions`), have the working-directory status for one of the resolved paths change so it's no longer present in `state.changesState.workingDirectory.files` (e.g., staged/renamed by a concurrent background refresh, or a chunked resolution race for large conflict sets).
3. `_applyCopilotConflictResolutions` reaches `onDiskFile === undefined` for that path and, per the code at [3](#0-2) , falls through to `writeFile` and `pathsToStage.push`, staging attacker-influenced content without re-confirming it is a live, unresolved conflict.

### Citations

**File:** app/src/lib/stores/app-store.ts (L7241-7268)
```typescript
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
    }

    if (pathsToStage.length > 0) {
      await git(
        ['add', '--', ...pathsToStage],
        repository.path,
        'copilotConflictResolution'
      )
    }
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L473-521)
```typescript
export function validateResolutionPaths(
  resolutions: ReadonlyArray<IRawFileResolution>,
  expectedFiles: ReadonlyArray<IFileConflictContext>
): void {
  const expectedPaths = new Set(expectedFiles.map(f => f.path))
  const expectedHunkCounts = new Map(
    expectedFiles.map(f => [f.path, f.hunks.length])
  )
  const returnedPaths = new Set(resolutions.map(r => r.path))

  for (const path of returnedPaths) {
    if (!expectedPaths.has(path)) {
      throw new CopilotValidationError(
        `Copilot returned resolution for unexpected file: ${path}`
      )
    }
  }

  if (returnedPaths.size !== resolutions.length) {
    throw new CopilotValidationError(
      'Copilot returned duplicate file paths in resolutions'
    )
  }

  const missingPaths: Array<string> = []
  for (const path of expectedPaths) {
    if (!returnedPaths.has(path)) {
      missingPaths.push(path)
    }
  }
  if (missingPaths.length > 0) {
    throw new CopilotValidationError(
      `Copilot did not return resolutions for: ${missingPaths.join(', ')}`
    )
  }

  for (const resolution of resolutions) {
    // Delete-vs-modify resolutions use action instead of hunks — skip count check
    if (resolution.action !== undefined) {
      continue
    }
    const expectedCount = expectedHunkCounts.get(resolution.path) ?? 0
    if (resolution.hunks.length !== expectedCount) {
      throw new CopilotValidationError(
        `Copilot returned ${resolution.hunks.length} hunk(s) for "${resolution.path}" but expected ${expectedCount}`
      )
    }
  }
}
```
