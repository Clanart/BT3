## Title
Copilot conflict-resolution hunk splicing trusts model-reported hunk *order/count* rather than validating content correspondence, allowing an attacker-controlled repository to silently corrupt committed file content — ([File: app/src/lib/copilot-conflict-resolution.ts])

## Summary
This is a structural analog of the Solend `check_ixns` bug: a "prove-it's-correct" check that validates only a coarse, positional property (instruction count/order in Solend; hunk *count* and file-path membership here) instead of validating that each element genuinely corresponds to what it claims to replace. In GitHub Desktop's Copilot-assisted merge/rebase/cherry-pick conflict resolution, `validateResolutionPaths` and `reassembleResolvedFile` accept an LLM-authored JSON payload and splice its `resolvedContent` hunks into the on-disk file **purely by ordinal position**, with no verification that a given hunk's content actually replaces the conflict it is claimed to replace.

## Finding Description
The flow is:
1. `parseCopilotConflictResolution` (`app/src/lib/copilot-conflict-resolution.ts:281-466`) parses the model's raw JSON and only checks types/shape and that a hunk's `resolvedContent` doesn't still contain literal conflict markers.
2. `validateResolutionPaths` (`app/src/lib/copilot-conflict-resolution.ts:473-521`) checks that returned file paths are a subset of expected paths and that the **hunk count per file matches** the expected conflict-hunk count — it never checks that hunk *i* actually corresponds to conflict *i*'s ours/theirs content. [1](#0-0) 
3. `reassembleResolvedFile` (`app/src/lib/copilot-conflict-resolution.ts:549-599`) walks the original file and splices each hunk resolution in strictly by **order, not by location/content matching**, as the function's own docstring states. [2](#0-1) 
4. The reassembled content is written to disk and `git add`-ed in `_applyCopilotConflictResolutions` when the user clicks "Continue Merge/Rebase/Cherry-pick". [3](#0-2) 

The project's own changelog confirms this exact code path has previously produced silent corruption: *"Resolve Copilot conflict resolution data loss where file content outside conflicted regions was overwritten when using AI-assisted conflict resolution — #22349"*, in 3.5.13-beta3. [4](#0-3) 

This confirms the invariant being checked ("N hunks in, N hunks spliced, in order") is not sufficient to guarantee the spliced content is *correct*, and mirrors the reported class of bug: the check validates a positional/count property of a sequence rather than the semantic correctness of each element in that sequence. Just as `check_refresh`'s `required_post_ixs` used the pre-instruction validation mechanism without checking it actually corresponded to the required post-condition, `validateResolutionPaths`/`reassembleResolvedFile` validate hunk *count* without checking hunk *identity/content correspondence* to the conflict it replaces.

The attacker-reachable surface is the conflict content itself: file content with conflict markers, commit messages, and PR title/description are all gathered from the repository/GitHub API and placed into the prompt sent to the Copilot model (`ConflictResolutionSystemPrompt`, `app/src/lib/copilot-conflict-resolution.ts:190-254`). An attacker who controls a branch, commit, or PR that ends up on one side of a merge/rebase/cherry-pick conflict controls text that is fed verbatim into the model's context, and the model's output is trusted for correspondence based only on hunk count. I was not able to fully verify, within the available iterations, whether the exact prompt-construction code (in `copilot-store.ts`) neutralizes prompt-injection-style content before insertion — this remains an open question that should be checked directly in that file.

## Impact Explanation
If a malicious or compromised branch/PR/commit is merged, rebased onto, or cherry-picked in Desktop, and the user has "Resolve with Copilot" (or the "Always use Copilot" auto-mode, `CopilotConflictResolutionAlwaysNudge`) enabled, the model's hunk-ordering/content can diverge from the conflicts it's meant to resolve while still passing the only real gate (`validateResolutionPaths`'s count check). This produces file content on disk that gets staged and committed/pushed by the user without them noticing (they are relying on a UI diff review, which is opt-in effort, not a hard gate) — matching "silent corruption of what the user commits or pushes."

## Likelihood Explanation
Likelihood is moderate: it requires (a) a repository conflict scenario where an attacker's content is one side of the conflict, and (b) the user invoking Copilot-based resolution and clicking "Continue" without carefully diffing every resolved file (a gate the project explicitly nudges users toward disabling scrutiny for, via the "Always use Copilot" feature). The project has already shipped one fix in this exact area (#22349), indicating the splice logic is fragile and prone to this bug class recurring under different input shapes (e.g., chunked/parallel prompt batches via `createDependencyAwareChunks`, retries, or malformed/adversarial hunk counts that still pass the count check but not content correspondence).

## Recommendation
- Validate hunk correspondence, not just count: require the model to echo back (or the app to independently verify) that each `resolvedContent` hunk is plausibly derived from the specific ours/theirs/base content of the conflict at that ordinal position (e.g., diff similarity check against ours+theirs union) before splicing.
- Treat any resolved hunk whose content diverges too far from both `ours` and `theirs` as a validation failure requiring manual review, rather than silently accepting it once the count matches.
- Ensure `reassembleResolvedFile` fails closed (raises a `CopilotValidationError`) rather than proceeding when hunk-to-conflict mapping cannot be corroborated.
- Audit prompt construction (`copilot-store.ts`) for injection of repository-controlled text (commit messages, PR descriptions, file contents) and add explicit delimiting/escaping so those fields cannot be interpreted as instructions by the model.

## Proof of Concept
Conceptual reproduction (bounded by available investigation):
1. Attacker opens/merges a branch whose commits/PR description contain crafted natural-language instructions embedded in commit messages or conflicting file content (this text is placed into the Copilot prompt per `ConflictResolutionSystemPrompt`).
2. User triggers a merge/rebase/cherry-pick against this branch in Desktop and clicks "Resolve with Copilot".
3. The model returns a JSON response where the *number* of hunks per file matches `expectedHunkCounts` (satisfying `validateResolutionPaths`) but the hunk *content* for a given conflict does not correspond to that conflict's actual ours/theirs region — e.g., content intended for conflict 2 is emitted for conflict 1, or subtly altered/backdoored content is emitted while claiming to resolve a benign-looking conflict.
4. `reassembleResolvedFile` splices these hunks in strictly by order (`app/src/lib/copilot-conflict-resolution.ts:584-591`), producing a file whose non-conflicted-looking sections are actually attacker-influenced.
5. User reviews the "Changes" tab casually (or has "Always use Copilot" enabled) and clicks "Continue", writing the corrupted content to disk and staging it (`app/src/lib/stores/app-store.ts:7258-7259`), which is then committed/pushed.

Note: I could not, within the available tool budget, obtain the exact prompt-assembly code in `app/src/lib/stores/copilot-store.ts` to confirm whether attacker-controlled fields are sanitized before being embedded in the model prompt, nor trace the parallel-chunk merge path (`createDependencyAwareChunks`) end-to-end for additional ordering hazards. These would be the next things to verify to fully confirm exploitability versus the existing #22349 fix's actual scope.

### Citations

**File:** app/src/lib/copilot-conflict-resolution.ts (L509-519)
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
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L529-538)
```typescript
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
```

**File:** app/src/lib/stores/app-store.ts (L7233-7259)
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
```

**File:** changelog.json (L93-96)
```json
    "3.5.13-beta3": [
      "[Fixed] Recover conflict dialog from permanently frozen state when conflict state becomes invalid, preventing users from needing to restart the app - #22348",
      "[Fixed] Resolve Copilot conflict resolution data loss where file content outside conflicted regions was overwritten when using AI-assisted conflict resolution - #22349"
    ],
```
