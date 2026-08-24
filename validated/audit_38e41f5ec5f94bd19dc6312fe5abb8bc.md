## Title
Copilot conflict resolution silently overwrites edits made to a still-conflicted file during the (up to 100+ second) resolution window - (File: `app/src/lib/stores/app-store.ts`)

## Summary
GitHub Desktop's Copilot-powered conflict resolution reads each conflicted file's content once, sends the extracted hunks to an LLM, and — only when the user clicks "Continue Merge" later — splices the model's hunk-level output back into the *original, stale* file content and writes the result to disk. The only staleness check performed at write time is "does the file still contain conflict markers at all"; it does not verify that the file's content is unchanged since it was read. Any edit the user makes to a still-conflicted file during the resolution window (which the code itself measures in tens of seconds, sometimes minutes) is silently discarded and replaced by the stale, pre-edit reassembly when the resolutions are applied and staged for commit.

## Finding Description
The flow works as follows:

1. `buildConflictContext` reads each conflicted file from disk and stores the full text as `rawContent` at analysis start: [1](#0-0) 
2. The model returns only per-hunk resolved text; `reassembleResolutions` splices that hunk text into the **captured** `ctx.rawContent` to produce the full resolved file: [2](#0-1) 
3. This can take a long time — the app itself tracks buckets up to 120+ seconds for a single resolution run: [3](#0-2) 
4. Resolutions are *not* written immediately; they are stashed in state and only applied later when the user clicks "Continue Merge" on the result dialog: [4](#0-3) 
5. At apply time, `_applyCopilotConflictResolutions` writes `resolution.resolvedContent` (built from the stale `rawContent`) straight to disk, gated only by whether the file **still has any unresolved conflict markers at all**: [5](#0-4) 

The comment at that guard explicitly acknowledges the intent — to avoid clobbering a file the user *fully* resolved externally — but the check is binary (`hasUnresolvedConflicts`), not content-equality based. If the user, during the (potentially multi-minute) analysis/review window, edits the same file — for example manually fixing one hunk while other hunks in the same file (or other files in a multi-file conflict set) are still unresolved — the file still reports `isConflictedFileStatus` + `hasUnresolvedConflicts === true`, so the guard does not trigger, and the entire file is overwritten with the model's reassembly based on the pre-edit snapshot. There is no content hash/mtime check comparing the on-disk file at write time to the `rawContent` that was actually sent to the model, and no diff/confirmation shown to the user before the overwrite.

This mirrors the report's root cause exactly: a value (`goldPrice`/`goldRefund` in the report; here, the full file content basis for the reassembled resolution) is captured at one point in a multi-phase, delay-spanning operation and is applied unconditionally later, with the only "freshness" check being too coarse to catch intervening state changes.

## Impact Explanation
The overwritten content is written to disk and immediately `git add`-ed: [6](#0-5)  — it becomes exactly what gets committed (completing the merge/rebase/cherry-pick) and subsequently pushed, with no warning that the user's intervening edit to that same file was discarded. This is a silent corruption of what the user commits/pushes: work performed by the user during the resolution window on a conflicted file can vanish without any diff, prompt, or log entry surfacing the discrepancy.

## Likelihood Explanation
The window is not contrived: the app's own instrumentation anticipates resolution runs lasting past 15s/30s/60s/120s thresholds, and users are expected to review the summary/result dialog (potentially editing files in an external editor per the dialog's own "Open in {editor}" action) before clicking Continue: [7](#0-6) . Editing a still-conflicted file (e.g., resolving one of several hunks by hand while leaving another marker in place, common in larger conflict sets) is an ordinary, expected user action in this flow — not an unnatural step — and is exactly the case the existing "no remaining conflict markers" guard fails to catch.

## Recommendation
Capture a content fingerprint (e.g. hash or mtime+size) of each file's `rawContent` at the time it is read for Copilot analysis, and re-check that fingerprint against the on-disk file immediately before writing `resolvedContent` in `_applyCopilotConflictResolutions`. If the file changed at all (not just "fully resolved"), skip the overwrite and surface the conflict to the user instead of silently discarding their edit — analogous to storing `goldPrice`/`goldRefund` in the order to avoid relying on a value that may have drifted since it was captured.

## Proof of Concept
1. Start a merge/rebase that produces conflicts in `file.txt` with two separate hunks (A and B).
2. Click "Resolve with Copilot"; while the request is in flight (or while reviewing the result dialog before clicking Continue), manually edit `file.txt` to resolve hunk A yourself, leaving hunk B's conflict markers in place (so the file is still reported as conflicted).
3. Click "Continue Merge" in the Copilot result dialog.
4. Observe that `_applyCopilotConflictResolutions` (`app/src/lib/stores/app-store.ts:7169-7269`) overwrites `file.txt` entirely with the model's reassembled content computed from the pre-edit `rawContent`, discarding your manual resolution of hunk A with no warning, and stages the result via `git add` for the commit that completes the operation.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L429-438)
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

**File:** app/src/lib/stores/app-store.ts (L7093-7106)
```typescript
      // Record resolution timing buckets
      const elapsedSeconds = (performance.now() - resolveStartTime) / 1000
      if (elapsedSeconds > 15) {
        this.statsStore.increment('copilotConflictResolutionOver15sCount')
      }
      if (elapsedSeconds > 30) {
        this.statsStore.increment('copilotConflictResolutionOver30sCount')
      }
      if (elapsedSeconds > 60) {
        this.statsStore.increment('copilotConflictResolutionOver60sCount')
      }
      if (elapsedSeconds > 120) {
        this.statsStore.increment('copilotConflictResolutionOver120sCount')
      }
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

**File:** app/src/lib/stores/app-store.ts (L7262-7268)
```typescript
    if (pathsToStage.length > 0) {
      await git(
        ['add', '--', ...pathsToStage],
        repository.path,
        'copilotConflictResolution'
      )
    }
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

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx (L212-234)
```typescript
  private onOverflowMenuClick = (path: string) => {
    const { repository, dispatcher, resolvedExternalEditor } = this.props
    const absolutePath = join(repository.path, path)

    const items: IMenuItem[] = []

    if (resolvedExternalEditor !== null) {
      items.push({
        label: `Open in ${resolvedExternalEditor}`,
        action: () => this.props.openFileInExternalEditor(absolutePath),
      })
    }

    items.push(
      {
        label: OpenWithDefaultProgramLabel,
        action: () => openFile(absolutePath, dispatcher),
      },
      {
        label: RevealInFileManagerLabel,
        action: () => revealInFileManager(repository, path),
      }
    )
```
