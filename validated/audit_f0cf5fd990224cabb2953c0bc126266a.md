### Title
Copilot conflict-resolution "Continue Merge" flow can silently discard a user's externally-resolved delete/modify conflict decision - ([File: app/src/lib/stores/app-store.ts])

### Summary
`_applyCopilotConflictResolutions` in `app-store.ts` applies AI-generated (Copilot) conflict resolutions when the user clicks "Continue Merge" after a merge/rebase/cherry-pick. For ordinary text/content conflicts it explicitly re-checks the live on-disk file status before overwriting content, to avoid clobbering a resolution the user made outside the dialog. The delete-vs-modify conflict branch of the exact same function does **not** perform that equivalent re-check before recording the AI's `ours`/`theirs` choice as the resolution to be staged, mirroring the class of bug in the original report: one code path (multi/"general") enforces "don't override what's already been resolved," while a sibling path (single/"special-case", here delete-vs-modify) skips that check and blindly applies the automated value.

### Finding Description
In `_applyCopilotConflictResolutions`: [1](#0-0) 

For regular content conflicts, before overwriting a file with the Copilot-resolved content, the code explicitly checks `hasUnresolvedConflicts(onDiskFile.status)` on the *current* working-directory status and skips the write if the user already resolved the file externally, precisely to avoid "silently clobbering their work."

The delete-vs-modify branch of the same loop has no analogous check: [2](#0-1) 

It looks the file up in `state.changesState.workingDirectory.files`, derives `deletedSide` from its status via `getDeletedSideFromStatus`, and unconditionally calls `_updateManualConflictResolution` with the AI's `keep`/`delete` decision — there is no check equivalent to `hasUnresolvedConflicts` guarding this branch against a resolution the user already made (e.g. running `git rm`/`git add` manually, or resolving in an external tool) while the Copilot result dialog was open.

The stored manual resolution is later consumed at commit time by `createMergeCommit` → `stageManualConflictResolution`: [3](#0-2) 

which does its own guard (`isConflictedFileStatus(status)`), but that guard depends on the status object captured in the `files` array passed through `_finishConflictedMerge`/`createMergeCommit`, which is the cached `workingDirectory` state, not necessarily re-derived at commit-time from disk. Unlike the content-write path, the delete-conflict path has no explicit, function-local defense-in-depth check mirroring `hasUnresolvedConflicts`, so its safety depends entirely on `getDeletedSideFromStatus`'s (unverified in this review) handling of an already-resolved status and on the freshness of the cached working-directory snapshot — a strictly weaker invariant than the one intentionally added for the content-conflict branch a few lines above it.

### Impact Explanation
If the asymmetry is exploitable (i.e., if `getDeletedSideFromStatus`/the cached status do not always reflect the user's just-made external resolution), a maliciously crafted delete-vs-modify conflict — trivial for an attacker who controls the incoming branch/PR being merged (delete a file on their side while the user's side modifies it, or vice versa) — could cause the Copilot flow to silently apply the AI's `keep`/`delete` choice for that path instead of the user's actual resolution when the user clicks "Continue Merge." This is exactly the "silent corruption of what the user commits or pushes" impact category: the user's chosen resolution (deleting a file they wanted kept, or keeping a file they wanted deleted) can be reverted without any further confirmation.

### Likelihood Explanation
Moderate-to-low confidence. This requires: (1) a delete-vs-modify conflict originating from an untrusted branch/PR, (2) the user resolving that specific file externally while the Copilot conflict dialog remains open, and (3) the cached working-directory snapshot/`getDeletedSideFromStatus` not reflecting that external resolution by the time "Continue Merge" is clicked. I was unable to inspect the implementation of `getDeletedSideFromStatus` or confirm exactly when/how the working-directory cache is refreshed relative to file-system events during this dialog's lifetime, so I cannot conclusively state the guard is absent end-to-end — only that the explicit, intentional check present in the sibling content-conflict branch is missing from this branch.

### Recommendation
Add the same "already resolved externally" guard to the delete-vs-modify branch that exists in the content-conflict branch: before calling `_updateManualConflictResolution`, re-fetch/verify the file's live conflict status (e.g. via `hasUnresolvedConflicts`) and skip applying the AI's `keep`/`delete` decision if the file is no longer in an unresolved conflicted state. Ensure `_finishConflictedMerge`/`createMergeCommit` also re-validates conflict status against a freshly-read git status rather than a potentially stale cached `workingDirectory` snapshot before staging manual resolutions.

### Proof of Concept
1. Attacker crafts a branch/PR that deletes `secrets-config.yml` while the user's branch modifies it (or the reverse).
2. User merges the attacker's branch in Desktop; a delete-vs-modify conflict is detected and routed to Copilot conflict resolution.
3. While the Copilot result dialog is open, the user manually resolves the conflict outside Desktop (e.g. `git rm secrets-config.yml && git add secrets-config.yml` in a terminal) to explicitly keep the deletion.
4. User clicks "Continue Merge" in Desktop. `_applyCopilotConflictResolutions` hits the `resolution.deleteConflictAction !== undefined` branch and, absent the missing check, records Copilot's `keep` decision via `_updateManualConflictResolution`, overriding the user's manual deletion.
5. At commit time, `stageManualConflictResolution` may re-add the file depending on whether the cached status reflects the user's terminal-side change, resulting in the file being silently restored/re-committed against the user's explicit intent. [4](#0-3)

### Citations

**File:** app/src/lib/stores/app-store.ts (L7196-7231)
```typescript
    for (const resolution of copilotResolutions) {
      if (manualResolutions.has(resolution.path)) {
        continue
      }

      // Delete-vs-modify conflicts are resolved by setting a manual
      // resolution (ours/theirs) rather than writing file content.
      // The existing stageManualConflictResolution flow handles the
      // actual git checkout --ours/--theirs and staging at commit time.
      if (resolution.deleteConflictAction !== undefined) {
        const file = state.changesState.workingDirectory.files.find(
          f => f.path === resolution.path
        )
        if (file === undefined) {
          continue
        }
        const deletedSide = getDeletedSideFromStatus(file)
        if (deletedSide === undefined) {
          continue
        }
        // "keep" → choose the non-deleted side, "delete" → choose the deleted side
        const manualChoice =
          resolution.deleteConflictAction === 'keep'
            ? deletedSide === 'ours'
              ? ManualConflictResolution.theirs
              : ManualConflictResolution.ours
            : deletedSide === 'ours'
            ? ManualConflictResolution.ours
            : ManualConflictResolution.theirs
        this._updateManualConflictResolution(
          repository,
          resolution.path,
          manualChoice
        )
        continue
      }
```

**File:** app/src/lib/stores/app-store.ts (L7241-7256)
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
```

**File:** app/src/lib/git/stage.ts (L22-44)
```typescript
export async function stageManualConflictResolution(
  repository: Repository,
  file: WorkingDirectoryFileChange,
  manualResolution: ManualConflictResolution
): Promise<void> {
  const { status } = file
  // if somehow the file isn't in a conflicted state
  if (!isConflictedFileStatus(status)) {
    log.error(`tried to manually resolve unconflicted file (${file.path})`)
    return
  }

  if (isConflictWithMarkers(status) && status.conflictMarkerCount === 0) {
    // If somehow the user used the Desktop UI to solve the conflict via ours/theirs
    // but afterwards resolved manually the conflicts via an editor, used the manually
    // resolved file.
    return
  }

  const chosen =
    manualResolution === ManualConflictResolution.theirs
      ? status.entry.them
      : status.entry.us
```
