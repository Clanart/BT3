## Title
Copilot conflict resolution can silently discard a user's partial manual edits when a file has one resolved and one still-unresolved hunk - (File: `app/src/lib/stores/app-store.ts`)

### Summary
`_applyCopilotConflictResolutions` writes Copilot's AI-generated resolution over the on-disk file using content that was reassembled from a **stale snapshot** of the file (`rawContent`, captured minutes earlier when the conflict context was built for the LLM prompt). Before writing, it only skips the write if the file is *fully* resolved (`!hasUnresolvedConflicts`). If the user has manually edited part of the file (e.g., resolved one of several hunks, or touched unrelated code outside a conflict region) but conflict markers are still present anywhere else in the file, the guard does not trigger, and the entire file — including the user's just-made edits — is overwritten with Copilot's stale-snapshot-derived content and then `git add`-ed. [1](#0-0) 

### Finding Description
`buildConflictContext` reads each conflicted file from disk once, at the moment the Copilot resolution flow starts, and stores the full text as `rawContent` on the `IFileConflictContext`. [2](#0-1) 

Later, `reassembleResolutions` splices the model's per-hunk resolved text into that same stale `ctx.rawContent` to produce the final `resolvedContent` for each file. [3](#0-2) 

The dialog can remain open for as long as the model call takes (there is an explicit loading/interstitial state and even a "return to conflicts" path while the request is in flight), during which the user is free to keep editing the conflicted files in their own editor. [4](#0-3) [5](#0-4) 

When the user finally clicks "Continue Merge", `_applyCopilotConflictResolutions` runs the write path. Its only staleness guard is:
```
const onDiskFile = state.changesState.workingDirectory.files.find(f => f.path === resolution.path)
if (onDiskFile !== undefined && isConflictedFileStatus(onDiskFile.status) && !hasUnresolvedConflicts(onDiskFile.status)) {
  continue
}
await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
``` [1](#0-0) 

This check only protects the case where the user resolved *every* conflict marker in the file (so git status reports it as no-longer-conflicted). It does not protect:
- a file where the user manually fixed one hunk but a second hunk in the same file is still unresolved (still `hasUnresolvedConflicts === true`), or
- edits made to non-conflicted parts of the same file (since the entire file is overwritten wholesale from the stale `rawContent`, not just the conflicted regions).

In either case the code falls through and blindly `writeFile`s the stale, Copilot-derived full file content, discarding the user's just-made edits, and then stages it with `git add`, so the corrupted content becomes what the user commits/pushes as part of the merge/rebase/cherry-pick. Notably, the project's own changelog documents an earlier, closely related incident of exactly this class ("Copilot conflict resolution data loss where file content outside conflicted regions was overwritten…"), confirming this is a real, previously-manifested failure mode of this code path, and the fix that shipped (the `hasUnresolvedConflicts` check) is narrow and does not cover the partial-resolution case identified above. [6](#0-5) 

This is the direct structural analog of the ZetaChain report's broken invariant: work performed against fresh state ("dirty" state / the user's live edits) is not reconciled before a separate subsystem (the Cosmos SDK precompile call / here, the deferred Copilot file write) commits its own, older snapshot, silently clobbering the newer state.

### Impact Explanation
The corrupted value is the on-disk content of a conflicted file that is about to be committed as part of a merge/rebase/cherry-pick. A user who partially hand-resolves a multi-hunk conflict (a very natural workflow — reviewing one hunk, fixing it, then invoking "Resolve with Copilot" for the rest, or simply editing the file while the Copilot request is in flight) can have that work silently discarded and replaced by AI-generated content without any warning, and the corrupted file is then `git add`ed and becomes part of the commit/merge result. This is exactly the "silent corruption of what the user commits or pushes" impact class called out as valid, and requires no elevated privileges — only normal use of the Copilot conflict-resolution feature during a merge/rebase/cherry-pick with multi-hunk conflicts.

### Likelihood Explanation
The window is real and not contrived: the Copilot resolution call is asynchronous (LLM round-trip), the UI explicitly supports returning to the conflicts view or leaving the loading state open while it runs, and "Continue Merge" reads from a `state` snapshot without re-reading the file's current on-disk content or status right before the write. Any file with more than one conflict hunk where the user resolves a subset manually while Copilot is still working (or before clicking "Continue Merge") triggers the bug — this doesn't require an unnatural sequence of steps, just normal interleaving of manual and AI-assisted conflict resolution, which the UI's own dropdown (mixing "Use Copilot's suggestion" per-file with manual "ours/theirs" per other files) actively encourages. [7](#0-6) 

### Recommendation
Before writing `resolvedContent`, re-read the file's current on-disk content and diff it against the `rawContent` snapshot used to build the resolution; if the live content has changed at all outside of what will be spliced in (or if it no longer matches the hunks the model resolved), skip the write and surface the conflict back to the user instead of silently overwriting. Alternatively, apply resolutions as a true patch/splice against the file's *current* content (re-parsing conflict markers at write time) rather than against a cached full-file string captured earlier in the flow.

### Proof of Concept
1. Start a merge/rebase/cherry-pick that produces a file with **two or more** conflict hunks in the same file.
2. Trigger "Resolve with Copilot" (`dispatcher.attemptCopilotConflictResolution` → `_startCopilotConflictResolution`), which reads `rawContent` once via `buildConflictContext`.
3. While the request is in flight (or after it returns but before clicking "Continue Merge"), manually resolve one of the two hunks yourself in an editor, leaving the other hunk's markers intact and saving the file.
4. Click "Continue Merge" (`applyCopilotConflictResolutions` → `_applyCopilotConflictResolutions`). Because `hasUnresolvedConflicts(onDiskFile.status)` is still `true` (the second hunk is still unresolved), the skip check at [8](#0-7)  does not fire.
5. The file is overwritten wholesale with `resolution.resolvedContent` (built from the stale `rawContent` captured in step 2), discarding the manual edit made in step 3, and the corrupted file is `git add`ed and becomes part of the finished commit.

### Citations

**File:** app/src/lib/stores/app-store.ts (L6905-6912)
```typescript
  /**
   * Orchestrate Copilot conflict resolution: call the API, emit progress
   * updates, and transition to the result dialog on success. File writes are
   * deferred until the user confirms (see _applyCopilotConflictResolutions).
   *
   * This shouldn't be called directly. See `Dispatcher`.
   */
  public async _startCopilotConflictResolution(
```

**File:** app/src/lib/stores/app-store.ts (L7073-7091)
```typescript
      // Store resolutions and transition to the result dialog.
      // Files are NOT written to disk yet — that happens when the user
      // clicks "Continue Merge" (see _applyCopilotConflictResolutions).
      this.repositoryStateCache.updateMultiCommitOperationState(
        repository,
        () => ({
          step: {
            kind: MultiCommitOperationStepKind.ShowCopilotConflicts,
            conflictState,
          },
          copilotResolutions: result.resolutions,
          copilotResolutionSummary: result.summary,
          copilotSkippedFiles: result.skippedFiles,
          copilotResolutionProgress: null,
          copilotResolutionAbortController: null,
        })
      )

      this.emitUpdate()
```

**File:** app/src/lib/stores/app-store.ts (L7241-7259)
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
```

**File:** app/src/lib/copilot-conflict-context.ts (L429-461)
```typescript
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

      const hunks = extractConflictHunks(content)
      if (hunks.length === 0) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'No conflict markers found',
        }
      }

      // Gate on the size of the conflict content we'd actually send to the
      // model, not the whole-file size.
      const hunkSkipReason = getHunkSkipReason(hunks)
      if (hunkSkipReason !== null) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: hunkSkipReason,
        }
      }

      return { path: file.path, hunks, rawContent: content }
    })
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L609-641)
```typescript
export function reassembleResolutions(
  rawResolutions: ReadonlyArray<IRawFileResolution>,
  fileContexts: ReadonlyArray<IFileConflictContext>
): ReadonlyArray<IFileResolution> {
  const contextByPath = new Map(fileContexts.map(f => [f.path, f]))

  return rawResolutions.map(raw => {
    // Delete-vs-modify resolutions carry an action, not hunk content.
    // Pass through without reassembly — the resolution is applied as a
    // ManualConflictResolution, not a file write.
    if (raw.action !== undefined) {
      return {
        path: raw.path,
        resolvedContent: '',
        reasoning: raw.reasoning,
        deleteConflictAction: raw.action,
      }
    }

    const ctx = contextByPath.get(raw.path)
    if (ctx?.rawContent === undefined) {
      throw new CopilotValidationError(
        `Cannot reassemble resolution for "${raw.path}": original file content is unavailable`
      )
    }

    const resolvedContent = reassembleResolvedFile(ctx.rawContent, raw.hunks)
    return {
      path: raw.path,
      resolvedContent,
      reasoning: raw.reasoning,
    }
  })
```

**File:** changelog.json (L93-96)
```json
    "3.5.13-beta3": [
      "[Fixed] Recover conflict dialog from permanently frozen state when conflict state becomes invalid, preventing users from needing to restart the app - #22348",
      "[Fixed] Resolve Copilot conflict resolution data loss where file content outside conflicted regions was overwritten when using AI-assisted conflict resolution - #22349"
    ],
```

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx (L148-187)
```typescript
  private getResolutionForFile(path: string): CopilotFileResolutionChoice {
    return getResolutionChoiceForFile(
      path,
      this.props.conflictState.manualResolutions
    )
  }

  private onResolutionDropdownClick = (path: string) => {
    const currentChoice = this.getResolutionForFile(path)
    const { ourBranch, theirBranch } = this.props.conflictState
    const fileStatus = this.getConflictedFileStatus(path)
    const { oursLabel, theirsLabel } = getOursTheirsLabels(
      fileStatus,
      ourBranch,
      theirBranch
    )

    const items: ReadonlyArray<IMenuItem> = [
      {
        label: "Use Copilot's suggestion",
        type: 'checkbox',
        checked: currentChoice === 'copilot',
        action: () => this.setResolution(path, 'copilot'),
      },
      {
        label: oursLabel,
        type: 'checkbox',
        checked: currentChoice === 'ours',
        action: () => this.setResolution(path, 'ours'),
      },
      {
        label: theirsLabel,
        type: 'checkbox',
        checked: currentChoice === 'theirs',
        action: () => this.setResolution(path, 'theirs'),
      },
    ]

    showContextualMenu(items)
  }
```
