### Title
Copilot conflict-resolution action field can bypass hunk validation, silently leaving conflict markers in a "resolved" file - ([File: app/src/lib/copilot-conflict-resolution.ts])

### Summary
The External Report's root cause is a validation function (`validateWeights`) that checks only a *shape* invariant (array lengths match) rather than the *substance* of the data, allowing a degenerate/empty case to skip the code path that would normally perform the real safety work (token transfer). GitHub Desktop's Copilot conflict-resolution pipeline has the same class of bug: `validateResolutionPaths` checks only that the *set of paths* matches and that hunk counts match for "normal" resolutions, but it unconditionally exempts any resolution carrying an `action` field from the hunk-count check — without ever verifying that the file in question is actually a delete-vs-modify conflict. This lets a model response (whose content is influenced by attacker-controlled repository text) skip real reassembly for a normal merge conflict, resulting in that file being silently left with unresolved `<<<<<<<`/`=======`/`>>>>>>>` markers.

### Finding Description
`parseCopilotConflictResolution` accepts any resolution entry that includes `action: 'keep' | 'delete'` and stores it with `hunks: []`, with no check that the corresponding file is actually a delete-vs-modify conflict: [1](#0-0) 

`validateResolutionPaths` then verifies only that the returned path set exactly matches the expected conflicted files, and — critically — skips the hunk-count check entirely whenever `resolution.action !== undefined`, regardless of whether that path was actually flagged as a delete-conflict in `expectedFiles`: [2](#0-1) 

`reassembleResolutions` then passes action-carrying resolutions straight through without touching the original conflict-marker content at all: [3](#0-2) 

Finally, `_applyCopilotConflictResolutions` handles any resolution with `deleteConflictAction !== undefined` by looking up `getDeletedSideFromStatus(file)`; if the file's actual on-disk status is a normal (non-delete) conflict, this returns `undefined` and the loop just `continue`s — the file is neither written nor staged, yet it was already accepted as "resolved" by every prior validation step: [4](#0-3) 

This mirrors the reported invariant break precisely: `validateWeights`/`validateResolutionPaths` both validate an easily-satisfied structural property (matching lengths / matching path sets) while omitting the substantive check that would prevent the degenerate branch (`action` present but no real delete-conflict / zero weights but tokens present) from bypassing the code that does the real work (`pullUnderlying` transfer / `reassembleResolvedFile` marker splicing).

### Impact Explanation
The conflicted file's content Copilot receives (surrounding context lines, commit messages, PR titles/bodies used in the prompt) originates from repository data that an attacker fully controls if they can get their branch/commits merged, rebased, or cherry-picked against — i.e. the classic "attacker controls a cloned/fetched repository" scenario. A response that sets `action: "keep"` for a file whose real state is a normal two-sided text conflict (not an actual delete/modify conflict) causes Desktop to silently skip writing any resolved content for that file. The result dialog and the "Continue Merge" flow give the user (and the app's own state machine) false confidence that all conflicted files were handled — `validateResolutionPaths` passed, and the file appears "resolved" in `copilotResolutions`. If the operation proceeds, the file is left on disk with literal `<<<<<<<`/`=======`/`>>>>>>>` markers, which can be committed and pushed as-is once the user completes the merge, silently corrupting what the user commits (broken code, corrupted config/data files) with no clear warning that this specific file was skipped.

### Likelihood Explanation
Exploitation requires only that an attacker's conflicting content (or accompanying commit/PR text used as prompt context) can steer the model toward emitting an `action` field for a path that isn't truly a delete-vs-modify conflict — a prompt-injection-style influence over LLM output. It does not require any local access, credentials, or malware, and the app performs no independent verification that `action` is only legitimate for genuine delete/modify conflicts before accepting/exempting the resolution from hunk-count validation. The risk is bounded by (a) the model's own tendency to follow the system prompt's field-usage rules, and (b) a secondary skip inside `_applyCopilotConflictResolutions` that avoids writing anything at all — so the concrete damage is "the file is silently left unresolved" rather than a worse content-injection outcome, but the missing cross-check in `validateResolutionPaths` is the same class of gap as the reported bug.

### Recommendation
In `validateResolutionPaths`, only allow the hunk-count exemption when the corresponding entry in `expectedFiles` actually carries `deleteConflict` metadata (i.e., cross-check `resolution.action` against `f.deleteConflict !== undefined`, not merely against the presence of the field in the model's own output). Reject (throw `CopilotValidationError`) any resolution that supplies `action` for a path that Desktop did not itself identify as a delete-vs-modify conflict, mirroring the report's fix of validating substance (`_tokens.length > 0`) rather than shape alone.

### Proof of Concept
1. Trigger a merge/rebase/cherry-pick against a repository where `file.ts` has a normal two-sided text conflict (both sides modified the same region — not a delete-vs-modify conflict).
2. Craft the conflicting content/commit message so that, when fed into the Copilot prompt, the model (via prompt injection) emits a resolution for `file.ts` with `"action": "keep", "hunks": []` instead of real per-hunk `resolvedContent`.
3. `parseCopilotConflictResolution` accepts this entry (app/src/lib/copilot-conflict-resolution.ts:403-421); `validateResolutionPaths` skips the hunk-count check because `resolution.action !== undefined` (lines 509-513), even though `expectedFiles` for `file.ts` has no `deleteConflict` metadata.
4. `reassembleResolutions` passes the entry through with `resolvedContent: ''` and `deleteConflictAction: 'keep'` (lines 619-626).
5. In `_applyCopilotConflictResolutions`, `getDeletedSideFromStatus(file)` returns `undefined` for this genuinely non-delete conflict, so the loop `continue`s without writing or staging `file.ts` (app/src/lib/stores/app-store.ts:7205-7231).
6. The user, seeing the result dialog report the file as resolved, clicks "Continue Merge"; `file.ts` still contains raw conflict markers on disk and is committed/pushed unresolved.

*Note: I could not locate/inspect the exact body of `getDeletedSideFromStatus` in the indexed code (only its call sites), so the "returns undefined for non-delete conflicts" behavior is inferred from its usage pattern and naming rather than confirmed by reading its implementation directly.*

### Citations

**File:** app/src/lib/copilot-conflict-resolution.ts (L403-421)
```typescript
    // Parse optional action for delete-vs-modify conflicts
    const action =
      rawAction === 'keep' || rawAction === 'delete' ? rawAction : undefined

    // Delete-vs-modify resolutions use action instead of hunks
    if (action !== undefined) {
      if (typeof reasoning !== 'string' || reasoning.trim().length === 0) {
        throw new CopilotValidationError(
          `Copilot returned an invalid conflict resolution payload: "reasoning" at index ${i} must be a non-empty string`
        )
      }
      validated.push({
        path: normalizeLLMPath(path),
        hunks: [],
        reasoning,
        action,
      })
      continue
    }
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L509-521)
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
}
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L609-626)
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
```

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
