### Title
Copilot conflict-resolution splices per-hunk resolutions by position, not identity, allowing order-mismatched hunks to silently corrupt the reassembled file - (File: `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
The `verifyTransaction` bug pattern in the report is: a validity check confirms a *quantity* (signature count ≥ threshold) but never confirms *identity/order* (that the seller's signature is actually among/positioned as expected), so a correctly-sized but differently-ordered input silently produces the wrong outcome. The same broken invariant exists in GitHub Desktop's Copilot conflict-resolution feature: `validateResolutionPaths` only checks that the **number** of hunks returned by the model matches the number of conflicts expected per file, while `reassembleResolvedFile` splices each hunk into the file strictly by **positional index**, trusting that the model returned hunks in file order. Nothing in the code verifies that hunk *i* returned by the model actually corresponds to conflict block *i* in the file.

### Finding Description
`reassembleResolvedFile` walks the raw on-disk file (which still contains `<<<<<<<`/`=======`/`>>>>>>>` markers) and, for every well-formed conflict block it encounters, splices in `hunkResolutions[hunkIndex]` and increments `hunkIndex`: [1](#0-0) 

The function's own doc comment admits the match is positional: "matched by order, not by line number" [2](#0-1) .

Before reassembly, `validateResolutionPaths` is the only guard, and it validates paths, duplicates, missing files, and **hunk count**, but never hunk order or content correspondence: [3](#0-2) 

The model (Copilot) is instructed via the system prompt to return hunks in "Conflict 1 of N", "Conflict 2 of N" order [4](#0-3) , but this is a prompt convention, not an enforced invariant — exactly analogous to the smart-contract bug where the *specification* required signature-order independence but the *implementation* silently depended on a specific array position, and the count-only check (`sigV.length` threshold) masked the real failure mode.

The model's output is untrusted, LLM-generated text derived from repository content the app feeds into the prompt (commit messages, PR descriptions, and the conflicting file/hunk text itself) [5](#0-4) . An attacker who controls commits merged/rebased against the victim's branch controls part of that prompt content and can attempt to induce the model (via prompt injection embedded in code comments, commit messages, or conflicting content) to emit `hunks` with correct count but swapped/misordered entries relative to the conflicts they were meant to resolve.

Once returned, resolutions are staged directly to disk and `git add`-ed once the user clicks "Continue Merge", with no re-validation of hunk-to-conflict correspondence: [6](#0-5) 

The only mitigation is a UI diff preview shown before the user confirms [7](#0-6) , but this shows the *final reassembled text*, not hunk-by-hunk provenance — a reviewer has no signal that content was spliced into the wrong location within a multi-hunk file, especially when hunk contents are superficially similar in shape/length.

### Impact Explanation
If the model returns per-file hunk resolutions with the right total count but in a scrambled order, `reassembleResolvedFile` will silently graft the resolution meant for one conflict region into an unrelated conflict region of the same file. This is exactly the "silent corruption of what the user commits or pushes" impact category: the file that gets `writeFile`'d and `git add`'d can contain logic from the wrong conflict site (e.g., a security check meant to replace one hunk ends up replacing a different hunk, or vice versa), while the two conflicting branches' code is spliced in the wrong places. This happens without any thrown error, since count-based validation passes.

### Likelihood Explanation
This requires: (1) a merge/rebase/cherry-pick conflict with **multiple hunks in the same file** (single-hunk files are unaffected since there is nothing to reorder), (2) the Copilot conflict-resolution model to actually return the hunks out of order — this is not attacker-controlled deterministically, since it depends on LLM behavior/prompt-injection success, which is inherently unreliable. There is also a human-in-the-loop diff review step before the corrupted content is written, which would need to be missed by the reviewer. This lowers likelihood relative to a fully deterministic bug, but the underlying code path has zero structural protection against the failure mode — the only barrier is model reliability plus user vigilance, not a code-level invariant. I was not able to fully confirm from the index whether hunk order is otherwise pinned by the prompt structure (e.g., interleaved per-hunk markers that would make misordering harder for the model to produce); this is an area where a background agent with full repository access (including `copilot-conflict-context.ts` prompt formatting) would need to verify exactly how strongly file/hunk order is anchored in the prompt.

### Recommendation
Do not rely on positional array order to correlate model-returned hunks with conflict blocks in the file. Instead:
- Tag each conflict block with a stable, verifiable identifier (e.g., an index or hash of its original marker content) and require the model to echo that identifier back per hunk.
- In `reassembleResolvedFile`/`validateResolutionPaths`, verify the returned identifier matches the conflict block being spliced before insertion, rejecting (falling back to manual resolution) on mismatch rather than assuming order.
- Consider surfacing per-hunk diffs (not just whole-file diffs) in the review UI so a user can see exactly which resolved block replaced which original conflict marker block.

### Proof of Concept
Not independently reproducible from static code alone since it requires inducing specific LLM output ordering; however, the code-level proof of the missing invariant is direct:

1. Given a file with two conflicts (hunks `A` and `B` in that file order), a compliant `IRawFileResolution` might return `hunks: [resolutionForB, resolutionForA]` (correct count = 2).
2. `validateResolutionPaths` passes because `resolution.hunks.length === expectedCount` (2 === 2) [8](#0-7) .
3. `reassembleResolvedFile` then splices `hunkResolutions[0]` (intended for conflict B) into the *first* conflict block encountered in the file (conflict A), and `hunkResolutions[1]` into conflict B's location [9](#0-8) .
4. The resulting file is written to disk and staged with no error raised [10](#0-9) .

### Citations

**File:** app/src/lib/copilot-conflict-resolution.ts (L195-201)
```typescript
You will receive:
- Labels for both sides (branch names or commit refs)
- Conflict markers from each file (ours, theirs, optionally base)
- Context lines surrounding each conflict
- Delete-vs-modify conflicts where one side deleted a file and the other modified it
- When available: recent commit messages and/or PR title/description for intent

```

**File:** app/src/lib/copilot-conflict-resolution.ts (L245-245)
```typescript
hunks: An ordered array with one entry per conflict in the file, matching the "Conflict 1 of N", "Conflict 2 of N" order from the input. Each entry's resolvedContent is ONLY the merged content that replaces that specific conflict marker block (the region between <<<<<<< and >>>>>>>). Do NOT include surrounding non-conflicted code — the application splices each resolution into the original file automatically. If the resolution is to accept one side entirely, return that side's content verbatim. For an intentional deletion, use an empty string. For delete-vs-modify conflicts, hunks must be an empty array.
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L509-520)
```typescript
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
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L533-536)
```typescript
 * through verbatim. Each conflict marker block (`<<<<<<<` through
 * `>>>>>>>`, with a `=======` separator in between) is replaced with the
 * corresponding entry from `hunkResolutions` (matched by order, not by
 * line number). This guarantees that all non-conflicted code is preserved
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

**File:** app/src/lib/stores/app-store.ts (L7169-7259)
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
```

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-changes.tsx (L153-230)
```typescript
  private async loadDiffForFile(file: CommittedFileChange) {
    const requestId = ++this.diffRequestId
    const choice = getResolutionChoiceForFile(
      file.path,
      this.props.manualResolutions
    )

    if (choice === 'ours' || choice === 'theirs') {
      this.setState({
        diff: null,
        fileContents: null,
        noResolution: false,
        diffError: false,
      })
      try {
        const result = await getResolutionDiff(
          this.props.repository,
          file.path,
          { stage: choice },
          this.state.hideWhitespaceInDiff
        )

        if (this.mounted && requestId === this.diffRequestId) {
          this.setState({
            diff: result.diff,
            fileContents: this.buildFileContents(file, result),
          })
        }
      } catch (e) {
        log.error('Failed to compute resolution diff', e)
        if (this.mounted && requestId === this.diffRequestId) {
          this.setState({ diff: null, fileContents: null, diffError: true })
        }
      }
      return
    }

    const resolution = this.props.copilotResolutions?.find(
      r => r.path === file.path
    )

    if (resolution === undefined) {
      this.setState({
        diff: null,
        fileContents: null,
        noResolution: true,
        diffError: false,
      })
      return
    }

    this.setState({
      diff: null,
      fileContents: null,
      noResolution: false,
      diffError: false,
    })

    try {
      const result = await getResolutionDiff(
        this.props.repository,
        file.path,
        { content: resolution.resolvedContent },
        this.state.hideWhitespaceInDiff
      )

      if (this.mounted && requestId === this.diffRequestId) {
        this.setState({
          diff: result.diff,
          fileContents: this.buildFileContents(file, result),
        })
      }
    } catch (e) {
      log.error('Failed to compute resolution diff', e)
      if (this.mounted && requestId === this.diffRequestId) {
        this.setState({ diff: null, fileContents: null, diffError: true })
      }
    }
```
