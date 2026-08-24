## Analysis

The Securitize report's broken invariant is: **a value computed at time T1 (before an external/attacker-influenced operation) is trusted and used at time T2 without re-validating it against the actual state after the operation**, causing corrupted output for the user. The closest analog in GitHub Desktop is not in git plumbing but in the Copilot-assisted merge-conflict resolution feature, where AI-generated `resolvedContent` computed from a **snapshot of conflicted file content** is spliced back into the working tree later, keyed only on ordinal hunk position, with staleness checked against the wrong signal (whether the file is still "conflicted"), not whether the underlying conflicted content is still the one that was analyzed.

### Title
Stale Copilot conflict resolutions can be spliced into unrelated conflict content by ordinal position, silently corrupting committed file contents - (File: app/src/lib/stores/app-store.ts, app/src/lib/copilot-conflict-resolution.ts)

### Summary
`_applyCopilotConflictResolutions` writes AI-produced `resolution.resolvedContent` to disk and stages it for commit. That content was reassembled earlier by `reassembleResolvedFile`, which splices per-hunk resolutions into a **captured snapshot** of the conflicted file (`ctx.rawContent`) purely by the ordinal position of `<<<<<<<`/`=======`/`>>>>>>>` blocks [1](#0-0) . When the user later clicks "Continue Merge", the app re-checks only whether the file is *still conflicted* — not whether its conflict content is *still the same* content that was fed to the model [2](#0-1) . If the on-disk conflict markers for that path change in count, order, or content between analysis and confirmation (e.g., a new fetch/rebase retry pulls in different remote content, or the same path is re-conflicted by a different pair of branches), the stale, positionally-matched `resolvedContent` is still written verbatim via `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` and then `git add`-ed [3](#0-2) .

### Finding Description
`reassembleResolutions`/`reassembleResolvedFile` do not embed or later verify any content hash/checksum tying a hunk resolution back to the exact conflict block it was generated from — matching is done purely by array order (`hunkIndex`) against the `rawContent` captured at prompt-build time [4](#0-3) [5](#0-4) . The `IFileResolution.resolvedContent` is stored in `multiCommitOperationState.copilotResolutions` and is only applied when the user later confirms via `_applyCopilotConflictResolutions` [6](#0-5) .

The only re-validation performed before the write is a status check: skip the file if it's no longer reporting a conflicted git status with unresolved markers [2](#0-1) . This guard protects against the narrow case of "user resolved it externally, file is now clean" — it does **not** protect against the case where the file is still conflicted, but with **different** conflict content than what `rawContent` captured (different hunk count, different hunk order, or same shape but different substance because the remote/branch content changed). In that scenario `reassembleResolvedFile` will happily replace hunk N of the *current* file with the model's resolution for hunk N of the *old* file, because matching is purely by index, not content equality (there is no fingerprint of `ctx.rawContent` checked before write).

This mirrors the audit finding's pattern precisely: a value is computed against one snapshot of external/untrusted state (`navProvider.rate()` after redemption vs. calculated pre-redemption assumption; here, `resolvedContent` computed against one snapshot of the conflicted file) and is later applied to a fresh authoritative source of truth (actual stable-coin balance vs. actual on-disk conflict content) without re-deriving or re-checking equivalence.

### Impact Explanation
Because `resolvedContent` originates partly from the untrusted repository being merged/rebased (attacker-controlled branch content, commit messages, PR titles/descriptions gathered as prompt context in `copilot-conflict-context.ts`), and because the write path trusts stale positional resolutions without content verification, the practical impact is **silent corruption of what the user commits and pushes**: code from a stale, unrelated, or attacker-influenced conflict resolution can be spliced into a different conflict block than the one it was generated for, producing a wrong merge result that git happily stages and commits (`git add`) without any diff review forcing function beyond what's shown in the (now stale) result dialog. This falls squarely in-scope as "silent corruption of what the user commits or pushes."

### Likelihood Explanation
Requires a plausible, unprivileged sequence: user starts Copilot conflict resolution on a merge/rebase/cherry-pick against a repository with an attacker-influenced branch, the resolution is computed and stored in state, and before the user clicks "Continue Merge" the underlying conflicted content for the same path changes (e.g., additional fetch, retry after abort-and-restart of a conflicting operation, or interaction with the manual conflicts dialog running concurrently) while the multi-commit-operation state retains the old `copilotResolutions`. The code comments around `ownsCurrentRun` and the on-disk staleness check show the developers were aware of race/staleness issues in this exact flow but only closed the "already resolved externally" gap, not the "conflict content changed but is still conflicted" gap. No local/physical access or admin rights are required — only normal use of the merge/conflict UI on a repository whose remote content an attacker can influence.

### Recommendation
Before writing `resolution.resolvedContent` in `_applyCopilotConflictResolutions`, re-read the current on-disk conflicted content for the path, re-extract its conflict hunks (via `extractConflictHunks`), and compare it (or the block-boundaries/hash) against the `rawContent` that produced the stored resolution. If they differ, treat the stored resolution as stale — drop it and fall back to letting the user resolve that file manually — rather than positionally splicing content generated for a different snapshot of the file.

### Proof of Concept
1. Start a merge/rebase in Desktop against a repository/remote branch under attacker influence, producing a conflicted file `foo.ts` with two conflict hunks.
2. Trigger "Resolve with Copilot"; the app captures `rawContent` for `foo.ts`, sends it to the model, and stores the two-hunk `resolvedContent` in `multiCommitOperationState.copilotResolutions` [7](#0-6) .
3. Before confirming, without leaving the app in an obviously "resolved" state, cause `foo.ts`'s conflict markers to be regenerated with different content (e.g., abort and restart the operation against a different ref, or a background fetch updates the incoming branch and the merge is retried) such that `foo.ts` is still reported as conflicted but its conflict-marker content/order no longer matches the captured `rawContent`.
4. Click "Continue Merge" in `CopilotConflictsDialog`, invoking `applyCopilotConflictResolutions` → `_applyCopilotConflictResolutions` [8](#0-7) . The status check passes (file is still conflicted with unresolved markers), so the stale `resolvedContent` — generated for the old conflict — is written to `foo.ts` and staged, producing a committed file whose content does not correspond to the actual merge that took place.

### Citations

**File:** app/src/lib/copilot-conflict-resolution.ts (L528-551)
```typescript
/**
 * Reassemble a fully resolved file by splicing per-hunk resolutions into
 * the original file content (which still has conflict markers on disk).
 *
 * Walks the original file line-by-line. Non-conflicted lines are copied
 * through verbatim. Each conflict marker block (`<<<<<<<` through
 * `>>>>>>>`, with a `=======` separator in between) is replaced with the
 * corresponding entry from `hunkResolutions` (matched by order, not by
 * line number). This guarantees that all non-conflicted code is preserved
 * exactly, and the model's output is only responsible for the small
 * resolved sections.
 *
 * A `<<<<<<<` line that is not followed by both a `=======` separator and
 * a closing `>>>>>>>` before EOF is treated as regular file content (not a
 * conflict block) and copied through unchanged to avoid data loss from
 * malformed or stray markers.
 *
 * @param rawContent - The full file content on disk, including conflict markers
 * @param hunkResolutions - Per-hunk resolved content, in the order they appear in the file
 * @returns The reassembled file with all conflicts resolved
 */
export function reassembleResolvedFile(
  rawContent: string,
  hunkResolutions: ReadonlyArray<IHunkResolution>
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L580-591)
```typescript

      // Skip through the entire conflict marker block
      i = closingIndex + 1

      // Splice in the resolved content for this hunk
      if (hunkIndex < hunkResolutions.length) {
        const resolved = hunkResolutions[hunkIndex].resolvedContent
        if (resolved.length > 0) {
          resultLines.push(...resolved.split(/\r?\n/))
        }
      }
      hunkIndex++
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L609-642)
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

**File:** app/src/lib/stores/app-store.ts (L7169-7181)
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

**File:** app/src/lib/stores/app-store.ts (L7258-7267)
```typescript
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

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx (L128-140)
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
```
