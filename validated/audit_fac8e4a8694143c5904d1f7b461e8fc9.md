Based on the investigation, the strongest and most defensible analog to the reported bug class (a "confirm" step trusting stale/attacker-influenced data captured in an earlier step, silently clobbering state that should require fresh validation) is in GitHub Desktop's AI-assisted conflict resolution ("Resolve with Copilot") write-back path.

### Title
Copilot conflict resolution silently discards partially-resolved hunks by overwriting the whole file with stale AI content - (File: `app/src/lib/stores/app-store.ts`)

### Summary
`_startCopilotConflictResolution` reads each conflicted file's content once, sends it to the Copilot SDK, and stores the AI's full-file `resolvedContent` per path in `copilotResolutions` state [1](#0-0) . When the user clicks "Continue Merge", `_applyCopilotConflictResolutions` writes that stale, previously-computed `resolvedContent` verbatim to disk for every resolution, staging it with `git add` [2](#0-1) . The only guard against staleness checks whether the file is *fully* resolved externally (`!hasUnresolvedConflicts`), in which case the write is skipped [3](#0-2) . This mirrors the `confirmTerms()` flaw's structure: a privileged "confirm" action re-uses input captured earlier without re-validating it against the *current* authoritative state before committing it.

### Finding Description
A file with multiple conflict hunks (trivially produced by any attacker-controlled fetched branch/PR that conflicts with the user's work in more than one place) is analyzed once by Copilot, which returns one `resolvedContent` string for the entire file, reassembled from the raw content read at analysis time via `reassembleResolutions` [4](#0-3) . If the user manually edits/resolves *one* hunk in that file (e.g., via their editor or the manual conflicts dialog) while another hunk in the same file remains unresolved, `getStatus` still reports the file as conflicted (`hasUnresolvedConflicts` is true), so the "already resolved externally" skip does not trigger. Clicking "Continue" then overwrites the *entire* file with the AI's stale, pre-edit reconstruction, silently discarding the user's manual work on the other hunk(s) with no diff confirmation forced on the user (diff review is only shown if they proactively open the "Changes" tab for that specific file) [5](#0-4) . This is essentially the same broken invariant as the `confirmTerms()` bug: a two-step confirm flow where the confirming step trusts data computed in step one instead of re-deriving/re-validating it against the just-in-time truth (current file content) before it becomes an irreversible action (staged and committed).

### Impact Explanation
The result is silent corruption of what the user ultimately commits/pushes — the exact "silent corruption" category called out as valid impact. A user could believe they resolved a conflict correctly (having manually fixed one hunk) only to have that fix erased and replaced by Copilot's earlier, now-incorrect reconstruction, without any error or forced review.

### Likelihood Explanation
This requires only an ordinary multi-hunk merge/rebase/cherry-pick conflict against attacker-influenced remote content (very common) plus the user exercising the normal supported workflow of touching up one hunk manually before continuing — no unnatural steps, no local/admin access. Given AI Copilot conflict resolution is a newly shipped feature, this staleness window is a straightforward corollary of the design (fetch context once, write once, weak re-validation), similar in kind to a data-loss bug this same subsystem already had fixed once before per the changelog entry for #22349 [6](#0-5) , indicating the underlying "trust stale resolution instead of re-checking against current truth" pattern in this file is a recurring source of bugs.

### Recommendation
Before writing `resolution.resolvedContent`, re-derive or hash-compare the current on-disk raw content against the `rawContent` captured when the resolution was generated (already available in `IFileConflictContext.rawContent`) and refuse/require confirmation if they differ, rather than gating solely on the coarse "still has any unresolved conflict" status. Alternatively, detect per-hunk drift and only apply resolutions for hunks/files whose underlying content is unchanged since analysis.

### Proof of Concept
1. Start a merge/rebase against a branch that conflicts in a file with two separate conflict hunks.
2. Run "Resolve with Copilot"; wait for `copilotResolutions` to be populated (`_startCopilotConflictResolution` completes) [7](#0-6) .
3. While the result dialog is open (before clicking Continue), manually edit the file to correctly resolve the first hunk only, leaving the second hunk's markers in place.
4. Click "Continue Merge" — `_applyCopilotConflictResolutions` sees `hasUnresolvedConflicts` is still true (second hunk unresolved) and overwrites the whole file with the AI's original (now stale) full-file content, discarding the user's manual fix to hunk one [8](#0-7) .

Note: I was unable to fully trace whether any additional revalidation exists elsewhere in the multi-commit-operation flow (e.g., a hash check added after the #22349 fix) that might further mitigate this specific partial-hunk scenario, since the index does not expose the complete diff of that historical fix. A Devin session with full file access would be needed to confirm whether any residual guard beyond `hasUnresolvedConflicts` was added.

### Citations

**File:** app/src/lib/stores/app-store.ts (L6912-6935)
```typescript
  public async _startCopilotConflictResolution(
    repository: Repository
  ): Promise<void> {
    const state = this.repositoryStateCache.get(repository)
    const { multiCommitOperationState } = state
    if (multiCommitOperationState === null) {
      return
    }

    const { step } = multiCommitOperationState
    if (
      step.kind !== MultiCommitOperationStepKind.ShowCopilotConflictsLoading
    ) {
      return
    }

    const { conflictState } = step
    const account = getAccountForCopilotConflictResolution(
      this.accounts,
      repository
    )
    if (!account) {
      return
    }
```

**File:** app/src/lib/stores/app-store.ts (L7073-7089)
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
```

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

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx (L662-716)
```typescript
  public render() {
    const { operationKind, workingDirectory, model } = this.props
    const { isContinuing, selectedTab } = this.state

    const unmergedFiles = getUnmergedFiles(workingDirectory)
    const operation = __DARWIN__ ? operationKind : operationKind.toLowerCase()

    const hasUnresolvedSkippedFiles = this.hasUnresolvedSkippedFiles()

    const modelLabel =
      model.reasoningEffort !== undefined
        ? `${model.modelName} · ${formatReasoningEffort(model.reasoningEffort)}`
        : model.modelName

    return (
      <Dialog
        id="copilot-conflicts-dialog"
        titleId={CopilotConflictsDialogTitleId}
        dismissDisabled={isContinuing}
        onDismissed={this.props.onDismissed}
        onSubmit={this.onContinue}
        loading={isContinuing}
        disabled={isContinuing}
      >
        <DialogHeader
          title={`Resolve conflicts before ${operationKind}`}
          titleId={CopilotConflictsDialogTitleId}
          showCloseButton={!isContinuing}
          onCloseButtonClick={this.props.onDismissed}
          loading={isContinuing}
        >
          <div className="copilot-conflicts-dialog-model-row">
            <span className="copilot-conflicts-dialog-model">{modelLabel}</span>
            <Button
              className="copilot-conflicts-dialog-settings-button"
              tooltip="Configure Copilot in app settings"
              ariaLabel="Configure Copilot in app settings"
              onClick={this.onOpenCopilotSettings}
            >
              <Octicon symbol={octicons.sliders} />
            </Button>
          </div>
        </DialogHeader>
        <DialogContent>
          <TabBar
            selectedIndex={selectedTab}
            onTabClicked={this.onTabSelected}
            type={TabBarType.Tabs}
          >
            <span>Summary</span>
            <span>Changes</span>
          </TabBar>
          {this.renderTabContent(unmergedFiles)}
        </DialogContent>
        <DialogFooter>
```

**File:** changelog.json (L93-96)
```json
    "3.5.13-beta3": [
      "[Fixed] Recover conflict dialog from permanently frozen state when conflict state becomes invalid, preventing users from needing to restart the app - #22348",
      "[Fixed] Resolve Copilot conflict resolution data loss where file content outside conflicted regions was overwritten when using AI-assisted conflict resolution - #22349"
    ],
```
