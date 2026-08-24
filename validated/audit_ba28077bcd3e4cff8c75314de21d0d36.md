Based on my investigation, the strongest analog to the Size "cap check blocks legitimate operation" bug class is in GitHub Desktop's Copilot conflict resolution feature, where a **size-based skip guard, driven entirely by attacker-controlled repository content, silently removes a file from the model's resolution set** — but the downstream "Continue Merge" path does not verify that skipped files were actually resolved before writing/staging, creating a path for unresolved conflict markers to be committed.

### Title
Size-gated conflict-skip guard lets an oversized/pathological conflict hunk (attacker-controlled via merge input) bypass Copilot resolution and reach the commit unresolved - (File: app/src/lib/copilot-conflict-context.ts)

### Summary
`getHunkSkipReason` and the file-size check in `buildConflictContext` (`app/src/lib/copilot-conflict-context.ts`) enforce hard caps — `MAX_CONFLICT_FILE_READ_SIZE` (10MB) and `MAX_CONFLICT_CONTENT_SIZE` (256KB) — before a conflicted file's content is sent to the Copilot model. [1](#0-0) [2](#0-1) 
These are the exact same "cap check computed from attacker-influenceable input can block the intended, legitimate flow" pattern described in the Size report: the checked value (conflict-hunk size, aggregated across `ours`/`theirs`/`base` sides) is fully controlled by whoever authored the branch/commit being merged, fetched, or cherry-picked — i.e., an untrusted remote contributor. When the cap trips, the file is marked with a `skippedReason` and excluded from the model's resolution set rather than being resolved. [3](#0-2) 

### Finding Description
`IFileConflictContext.rawContent` is only populated for files that pass the size checks; skipped files carry `hunks: []` and a `skippedReason` instead. [4](#0-3) 
When the model does return a per-hunk resolution for a path, `reassembleResolutions` requires `ctx.rawContent` to exist and throws `CopilotValidationError` otherwise — so the *hard* failure case (model tries to resolve a skipped file) is guarded. [5](#0-4) 

However, the write path, `_applyCopilotConflictResolutions` in `app/src/lib/stores/app-store.ts`, only iterates `copilotResolutions` (the array of files the model *did* resolve) and writes/stages those; skipped files are tracked separately as `copilotSkippedFiles` and are simply absent from `copilotResolutions`. [6](#0-5) 
The one on-disk safety check present — comparing `hasUnresolvedConflicts(onDiskFile.status)` — exists only to avoid *clobbering* a file the user already resolved externally; it does not force resolution of a skipped file before the merge/rebase/cherry-pick is allowed to continue. [7](#0-6) 
Whether the `CopilotConflictsDialog`'s "Continue" button is hard-blocked when `copilotSkippedFiles` is non-empty could not be fully confirmed from the index — the dialog does receive `copilotSkippedFiles` as a prop and renders it, but the `onContinue` handler I inspected calls `applyCopilotConflictResolutions` unconditionally and does not appear to gate on outstanding skipped files. [8](#0-7) 

### Impact Explanation
If a skipped file is allowed to proceed to `git add`/commit without the user manually resolving it (i.e., without picking ours/theirs via the dropdown), the file on disk still contains raw `<<<<<<<`/`=======`/`>>>>>>>` conflict markers. Committing and pushing that file is exactly the "silent corruption of what the user commits or pushes" scenario called out as in-scope impact: the user believes Copilot resolved all conflicts (as with the Size report, the guard is invisible/automatic), but a crafted incoming branch/PR with an oversized diff hunk (trivial to construct — e.g., a single minified line over 5,000 characters, or combined hunk content over 256KB) causes that file to be silently excluded from AI resolution while the merge is still allowed to complete.

### Likelihood Explanation
The attacker primitive is a git branch/PR/fetched ref with a conflicting region deliberately built to exceed `MAX_CONFLICT_LINE_LENGTH` (5000 chars) or `MAX_CONFLICT_CONTENT_SIZE` (256KB) — no local access, malware, or leaked credentials needed, only an ordinary contributor triggering a merge/rebase/cherry-pick against a repository they control content in. Given this is a newly shipped feature (`3.6.0`/`3.5.13-beta1`, "Resolve merge conflicts with Copilot"), and a closely related data-loss bug was already fixed in this same subsystem (`3.5.13-beta3`: "Resolve Copilot conflict resolution data loss where file content outside conflicted regions was overwritten"), the code path is young and has already shown one file-integrity defect class in this exact area. [9](#0-8) 

### Recommendation
Mirror the Size fix approach ("control the amount of emitted debt token" — i.e., make the guard fail closed for the risky operation rather than silently proceeding): require that any file present in `copilotSkippedFiles` (or otherwise excluded from `copilotResolutions` due to size) must have an explicit user resolution choice (ours/theirs/manual) recorded before `_applyCopilotConflictResolutions`/`onContinue` is permitted to proceed, and add an assertion in `_applyCopilotConflictResolutions` that no file with `isConflictWithMarkers` status remains unaddressed after staging.

### Proof of Concept
1. Attacker pushes/opens a branch whose merge with the target produces a conflict where the combined `ours+theirs+base` hunk content exceeds 256KB (e.g., a single large generated/minified data file section), or contains a line >5000 characters.
2. Victim runs a merge/rebase/cherry-pick in Desktop and clicks "Resolve with Copilot".
3. `buildConflictContext`/`getHunkSkipReason` mark that file `skippedReason: 'Conflict region too large to resolve automatically'`; the model resolves all other files normally. [10](#0-9) 
4. In the result dialog, victim clicks "Continue Merge" without noticing/addressing the skipped file (unverified whether the UI blocks this).
5. `_applyCopilotConflictResolutions` stages only the resolved files; if the skipped file is not separately forced into a resolved state, `git add`/commit proceeds while that file still contains literal conflict markers, corrupting the resulting commit. [11](#0-10) 

**Note:** I could not fully verify, from the indexed code alone, whether `copilot-conflicts-dialog.tsx`'s continue flow or `base-multi-commit-operation.tsx` enforces a hard block when `copilotSkippedFiles` is non-empty — this is the crux of whether the issue is fully exploitable or already mitigated at the UI layer. Confirming this requires reading the full `base-multi-commit-operation.tsx` and the rest of `copilot-conflicts-dialog.tsx`, which exceeded what the index surfaced in this session. If a hard block exists, this finding should be downgraded to informational; if not, it stands as a valid Medium-severity analog to the reported bug class. Given index size limits, a Devin session with full file access is recommended to conclusively verify this gap.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L24-48)
```typescript
/** Conflict context for a single file */
export interface IFileConflictContext {
  /** Repository-relative file path */
  readonly path: string
  /** All conflict hunks in the file (empty if skipped or delete-vs-modify) */
  readonly hunks: ReadonlyArray<IConflictHunk>
  /** If the file was skipped, the reason why (shown in prompt so Copilot knows) */
  readonly skippedReason?: string
  /**
   * The full file content on disk (including conflict markers). Used after
   * the model responds to reassemble the resolved file by splicing per-hunk
   * resolutions into the original content. Omitted when the file is skipped.
   */
  readonly rawContent?: string
  /**
   * Present when this is a delete-vs-modify conflict (no text markers).
   * One side deleted the file while the other modified it; the model
   * responds with `"action": "keep"` or `"action": "delete"` instead of
   * per-hunk resolutions.
   */
  readonly deleteConflict?: {
    /** Which side of the merge deleted the file. */
    readonly deletedSide: 'ours' | 'theirs'
  }
}
```

**File:** app/src/lib/copilot-conflict-context.ts (L127-146)
```typescript
/**
 * Absolute upper bound (in bytes) on a conflicted file we'll read into memory.
 *
 * This is a memory-safety guard only, not a resolvability heuristic — we only
 * ever send the *conflict hunks* to the model, never the whole file, so a large
 * file with a small conflict is still perfectly resolvable. Files above this
 * size are skipped before reading to avoid loading pathological blobs (e.g. a
 * multi-megabyte generated lockfile) into a string.
 */
const MAX_CONFLICT_FILE_READ_SIZE = 10_485_760 // 10MB

/**
 * Maximum length (in characters) of any single line within a conflict hunk.
 *
 * Mirrors the diff renderer's `MaxCharactersPerLine`. Conflicts containing a
 * line longer than this are almost always minified/generated content where a
 * line-oriented resolution is meaningless, so we skip them rather than sending
 * an enormous single line to the model.
 */
const MAX_CONFLICT_LINE_LENGTH = 5000
```

**File:** app/src/lib/copilot-conflict-context.ts (L281-315)
```typescript
/**
 * Determine whether a file's conflict hunks are too large or too unwieldy to
 * send to the model, returning a human-readable skip reason or null when the
 * conflict is resolvable.
 *
 * We gate on the size of the conflict content itself (what we actually send)
 * rather than the whole-file size, so a large file with a small conflict is
 * still resolved. Two conditions trigger a skip:
 *   1. Any single conflict line exceeds `MAX_CONFLICT_LINE_LENGTH` (minified or
 *      generated content where a line-oriented resolution is meaningless).
 *   2. The combined ours/base/theirs content exceeds `MAX_CONFLICT_CONTENT_SIZE`
 *      (protects prompt size and output quality).
 */
export function getHunkSkipReason(
  hunks: ReadonlyArray<IConflictHunk>
): string | null {
  let totalContent = 0

  for (const hunk of hunks) {
    const sides = [hunk.oursContent, hunk.theirsContent, hunk.baseContent ?? '']
    for (const side of sides) {
      totalContent += side.length
      for (const line of side.split('\n')) {
        if (line.length > MAX_CONFLICT_LINE_LENGTH) {
          return 'Conflict contains lines too long to resolve automatically'
        }
      }
    }
    if (totalContent > MAX_CONFLICT_CONTENT_SIZE) {
      return 'Conflict region too large to resolve automatically'
    }
  }

  return null
}
```

**File:** app/src/lib/copilot-conflict-context.ts (L409-427)
```typescript
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
```

**File:** app/src/lib/copilot-conflict-context.ts (L449-458)
```typescript
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
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L609-633)
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
```

**File:** app/src/lib/stores/app-store.ts (L7169-7267)
```typescript
  public async _applyCopilotConflictResolutions(
    repository: Repository
  ): Promise<void> {
    const state = this.repositoryStateCache.get(repository)
    const { multiCommitOperationState } = state
    if (multiCommitOperationState === null) {
      return
    }

    const { copilotResolutions, step } = multiCommitOperationState
    if (copilotResolutions === null || copilotResolutions.length === 0) {
      return
    }

    // Respect any manual overrides the user chose in the result dialog
    const manualResolutions =
      step.kind === MultiCommitOperationStepKind.ShowCopilotConflicts
        ? step.conflictState.manualResolutions
        : new Map<string, ManualConflictResolution>()

    this.statsStore.increment('copilotConflictResolutionAcceptedCount')
    if (manualResolutions.size > 0) {
      this.statsStore.increment('copilotConflictResolutionWithOverridesCount')
    }

    const pathsToStage: string[] = []

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
    }

    if (pathsToStage.length > 0) {
      await git(
        ['add', '--', ...pathsToStage],
        repository.path,
        'copilotConflictResolution'
      )
```

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx (L128-141)
```typescript
  private onContinue = async () => {
    this.setState({ isContinuing: true })
    try {
      // Write Copilot resolutions to disk before continuing the operation.
      // Done here (shared) so it works for merge, rebase, and cherry-pick.
      await this.props.dispatcher.applyCopilotConflictResolutions(
        this.props.repository
      )
      await this.props.onContinueAfterConflicts()
    } catch (e) {
      this.setState({ isContinuing: false })
      throw e
    }
  }
```

**File:** changelog.json (L76-96)
```json
    "3.6.0": [
      "[New] Git worktree support for managing multiple working directories of the same repository - #22102. Thanks @devxoul!",
      "[New] Resolve merge conflicts with Copilot - #22265",
      "[Added] Allow users to stop an ongoing request to generate a commit message with Copilot - #22324. Thanks @say25!",
      "[Fixed] Recover conflict dialog from permanently frozen state when conflict state becomes invalid, preventing users from needing to restart the app - #22348",
      "[Fixed] Fix Warp terminal detection on Windows to support the new Warp registry path with fallback to the legacy path - #22264. Thanks @Cocodrulo!",
      "[Fixed] Items in lists such as branches and changed files are properly announced by screen readers on Windows - #22219",
      "[Fixed] No longer prompts to initialize Git LFS for repositories that use non-LFS Git filters - #22180. Thanks @RonanLB!",
      "[Fixed] Remove misleading pointer cursor from the example link preview in Accessibility preferences - #22244",
      "[Improved] Update Electron to version 42.0.1 - #22104"
    ],
    "3.5.13-beta4": [
      "[Fixed] Worktree list is updated after after renaming or deleting worktrees - #22335 Thanks @pol-rivero!",
      "[Fixed] The toolbar correctly calculates available space when worktree dropdown isn't visible - #22356. Thanks @pol-rivero!",
      "[Fixed] Toolbar space allocation calculates available space based on order of toolbar buttons - #22364. Thanks @pol-rivero!",
      "[Fixed] Resolve issue where error messages in the Preferences dialog were not displayed correctly - #22363"
    ],
    "3.5.13-beta3": [
      "[Fixed] Recover conflict dialog from permanently frozen state when conflict state becomes invalid, preventing users from needing to restart the app - #22348",
      "[Fixed] Resolve Copilot conflict resolution data loss where file content outside conflicted regions was overwritten when using AI-assisted conflict resolution - #22349"
    ],
```
