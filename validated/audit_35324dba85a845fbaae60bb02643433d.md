## Analysis

The report's underlying bug class is a **missing binding/linkage check between an artifact that was validated and the state it is later applied against** — allowing stale or attacker-influenced data to be accepted without re-verification against current ground truth. In `ExecutorFacet.proveBlocks`, the proof was never bound to the specific committed blocks it was proving.

Searching the GitHub Desktop codebase for the analogous pattern (an entry point where attacker-controlled repository content flows through a "prove/generate now, apply later" pipeline without re-binding to the current on-disk state), the strongest match is the **Copilot conflict-resolution write path**.

### Title
Silent corruption of committed content due to unbound file-state check in Copilot conflict-resolution apply path - (File: `app/src/lib/stores/app-store.ts`)

### Summary
Copilot-generated conflict resolutions are computed once, fully reassembled into `resolvedContent`, and cached in memory [1](#0-0) . When the user later clicks "Continue Merge", `_applyCopilotConflictResolutions` writes that cached `resolvedContent` verbatim to disk [2](#0-1) . The only guard against staleness is a coarse git-status check ("is this file still reported as conflicted with unresolved markers") [3](#0-2)  — there is no verification that the on-disk conflicted content at apply time still matches (byte-for-byte or hash-for-hash) the content that was actually fed into the resolution/reassembly step at generation time.

### Finding Description
The reassembly logic (`reassembleResolvedFile`) matches per-hunk resolutions to conflict marker blocks **positionally, by order, not by identity or content hash**, as its own doc comment states: "matched by order, not by line number" [4](#0-3) . Validation (`validateResolutionPaths`) only checks that hunk *counts* match the expected count captured at generation time [5](#0-4)  — it never re-derives or hashes the actual conflict content that was extracted from disk.

Between the moment the conflict hunks are extracted from the working directory to build the Copilot prompt (`gatherConflictResolutionContext` → `buildConflictContext`) and the moment the user confirms and `_applyCopilotConflictResolutions` writes `resolution.resolvedContent`, an arbitrary amount of time can pass (the model call itself, plus however long the user leaves the result dialog open) [6](#0-5) . During that window the working tree can change through normal, attacker-reachable Desktop flows: a background `fetch` that updates refs, an `amend`/`abort`+`retry` of the same rebase/cherry-pick/merge with a different upstream state, or a malicious remote/proxy response that alters what a subsequent fetch delivers. If the underlying conflict for a path shifts (different hunk boundaries, additional/removed conflict blocks, or a completely different "theirs" side after a forced-push or altered fetch response), the on-disk file will still show as "conflicted with unresolved markers" (`isConflictedFileStatus && hasUnresolvedConflicts`) and thus pass the only implemented guard. The stale `resolvedContent` — computed against the old, no-longer-current conflict — is then written and immediately staged (`git add`) [7](#0-6) , silently overwriting a merge/rebase/cherry-pick resolution the user believes reflects the current diff.

This mirrors the `proveBlocks` flaw precisely: a proof/resolution is generated for a particular input, but the code that consumes it later only checks a coarse existence/status predicate ("is a proof present" / "is the file still marked conflicted") instead of re-binding the applied artifact to the exact input it was computed against.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes." The resolved file written to disk and staged for commit can diverge from both the file the user reviewed and the file actually reflecting the current merge base, without any error, warning, or diff shown at apply time. Because the write is immediately followed by `git add`, the corrupted content becomes part of the next commit/push with no further human-visible checkpoint.

### Likelihood Explanation
The `_applyCopilotConflictResolutions` code path is reached by any user who uses "Resolve with Copilot" during a merge/rebase/cherry-pick and leaves the result dialog open even briefly (the dialog itself invites review, encouraging delay) [8](#0-7) . Triggering a ref/content change during that window (e.g., an automatic background fetch, or the user re-fetching) is a normal, low-effort action, not one requiring elevated privileges. I was not able to fully trace whether Desktop performs any automatic background fetch during an in-progress rebase/merge/cherry-pick conflict state (this would raise likelihood further) — that would need direct verification in a live session, and the index does not expose every scheduler/polling code path.

### Recommendation
When building the Copilot resolution, capture a hash (or the exact raw content) of each conflicted file at extraction time and store it alongside the resolution. In `_applyCopilotConflictResolutions`, before writing `resolution.resolvedContent`, re-read the current on-disk conflicted file and compare it (hash or full content) to the captured baseline; if it differs, treat the file the same way the existing "user resolved it externally" branch does — skip the Copilot write and require manual resolution, surfacing this to the user. This closes the same class of gap identified in the `proveBlocks` finding: never apply a previously computed result to state without re-verifying it against the current state it claims to describe.

### Proof of Concept
1. Start a merge/rebase/cherry-pick that produces conflicts in `file.ts`.
2. Click "Resolve with Copilot"; Desktop extracts hunks from `file.ts`, sends them to Copilot, and caches the fully reassembled `resolvedContent` in `copilotResolutions` [1](#0-0) .
3. While the result dialog is open (before clicking "Continue Merge"), cause the working copy's conflicted region for `file.ts` to change — e.g., abort and redo the operation against an updated ref delivered by a subsequent `fetch` from a remote/proxy, keeping the file in a conflicted state with unresolved markers.
4. Click "Continue Merge". `_applyCopilotConflictResolutions` sees `isConflictedFileStatus` true and `hasUnresolvedConflicts` true, so it does not skip the file, and writes the stale cached `resolvedContent` over the new conflict content, then stages it [9](#0-8) .
5. The resulting commit contains content that was never generated against, or reviewed for, the actual final conflict — a silent integrity break in what gets committed/pushed.

### Citations

**File:** app/src/lib/stores/app-store.ts (L7073-7092)
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

**File:** app/src/lib/stores/app-store.ts (L7233-7268)
```typescript
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
    }
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

**File:** app/src/lib/copilot-conflict-resolution.ts (L528-547)
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
