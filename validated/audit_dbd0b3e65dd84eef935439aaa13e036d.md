## Title
Character-count size cap in Copilot conflict-resolution prompt building silently under-measures multi-byte UTF-8 content, allowing prompt/output budget bypass - (File: app/src/lib/copilot-conflict-context.ts)

### Summary
The Arbitrum report's broken invariant is a unit mismatch (KB passed where bytes were expected) that made a size-derived guard compute a lower value than the real quantity, weakening the protection the guard was meant to provide. The closest verifiable analog in GitHub Desktop is in the Copilot AI-assisted merge-conflict resolution feature: the size guard that decides whether a conflict's content is "small enough" to safely send to the model measures **JS string `.length`** (UTF-16 code units) but is documented and used as if it were measuring bytes/character count consistently with the model's actual payload size, understating true payload size for non-ASCII/multi-byte content from an attacker-controlled repository.

### Finding Description
`getHunkSkipReason` sums `side.length` for `oursContent`/`theirsContent`/`baseContent` and compares the accumulated total against `MAX_CONFLICT_CONTENT_SIZE = 262_144 // 256KB`: [1](#0-0) [2](#0-1) 

`String.prototype.length` in JavaScript counts UTF-16 code units, not bytes. For content containing multi-byte UTF-8 characters (e.g. CJK text, emoji, or other non-ASCII content that an attacker fully controls inside a cloned/fetched repository's conflicted file), the actual UTF-8 byte size of the hunk content sent downstream can be up to ~3x larger than `.length` reports for BMP characters (and up to 2x further for surrogate-pair/astral characters counted as 2 units but 4 bytes). The comment at line 149 explicitly frames the bound as protecting "prompt size," implying a byte/content-size intention that the `.length`-based measurement does not actually enforce.

The same file also enforces `MAX_CONFLICT_LINE_LENGTH = 5000` and `MAX_CONFLICT_FILE_READ_SIZE = 10_485_760` using different units (character count vs. `fs.stat().size` in bytes) without reconciling them — mirroring the report's core issue where one unit (bytes) is silently substituted for another (kilobytes) at the boundary between two different measurement systems (`asmEstimate` in KB vs. `dataPricer.UpdateModel`'s bytes parameter).

### Impact Explanation
An attacker who controls a cloned/fetched repository (e.g., via a malicious branch merged by the victim, triggering AI conflict resolution) can craft conflicting hunks using multi-byte UTF-8 characters that pass the `.length`-based `MAX_CONFLICT_CONTENT_SIZE` check while actually being multiple times larger in real byte/token size than intended. This defeats the stated purpose of the guard ("protects prompt size and output quality (truncation/malformed JSON)"), and — per the code's own comment — the failure mode is truncation or malformed model output, which the codebase's own changelog documents as a *prior* real-world consequence of this feature class: `changelog.json` records a fixed data-loss bug ("Resolve Copilot conflict resolution data loss where file content outside conflicted regions was overwritten when using AI-assisted conflict resolution - #22349"). [3](#0-2) 
Because Copilot's resolution is later written directly to disk and staged via `git add` when the user clicks "Continue Merge," an attacker-crafted conflict that exceeds intended size budgets (while evading the size gate) increases the likelihood of degraded/incomplete model output being silently written into the user's commit: [4](#0-3) 

### Likelihood Explanation
Moderate-to-low. It requires: (1) the victim to have Copilot conflict resolution enabled, (2) a merge/rebase/cherry-pick that produces conflicts against attacker-influenced content, and (3) the attacker's crafted content to specifically exploit the UTF-16-vs-byte gap to slip past the size gate while still causing truncation/degraded output at the model boundary. The existing guard does catch the common/ASCII case, and other complementary caps (`MAX_CONFLICT_LINE_LENGTH`, `MAX_CONFLICT_FILE_READ_SIZE`) reduce but do not eliminate the gap because they use the same character-counting approach rather than true byte length. No fully automated/verifiable end-to-end exploit chain (attacker-crafted repo → guaranteed corrupted commit) was confirmed in the available code; the connection to prior data-loss bug #22349 supports plausibility but the current code's guard against *that* specific fixed issue is not the same code path being flagged here.

### Recommendation
- Measure conflict content size using `Buffer.byteLength(content, 'utf8')` instead of `.length` in `getHunkSkipReason` (and consistently in `MAX_CONFLICT_LINE_LENGTH` checks) so the guard reflects the actual payload size sent to the model.
- Reconcile units across all conflict-context size constants (`MAX_CONFLICT_FILE_READ_SIZE` in bytes, `MAX_CONFLICT_CONTENT_SIZE`/`MAX_CONFLICT_LINE_LENGTH` in characters) and document/enforce them under one unit, analogous to the report's recommendation to rename/rectify `asmEstimate` vs `asmEstimateKb`.
- Add regression tests with multi-byte UTF-8 and astral-plane content to confirm the size gate rejects payloads whose true byte size exceeds the intended cap, not just their UTF-16 code-unit count.

### Proof of Concept
Conceptual (not executed against a live instance):
1. Attacker prepares a branch whose file introduces a merge conflict where the "ours"/"theirs"/"base" hunk content is filled with multi-byte characters (e.g., repeated CJK text) such that `.length` (UTF-16 units) stays just under `262_144`, but `Buffer.byteLength(content, 'utf8')` is 2–3x larger.
2. Victim merges/rebases the attacker's branch with Copilot conflict resolution enabled; `getHunkSkipReason` computes `totalContent` via `.length` and returns `null` (not skipped) because the character-count sum is under the cap: [5](#0-4) 
3. The oversized-in-bytes hunk content is sent to the model, increasing risk of truncation/malformed JSON output as anticipated by the code's own comments.
4. If the model's response is accepted, `_applyCopilotConflictResolutions` writes `resolution.resolvedContent` directly to disk and stages it via `git add`, without re-validating that the resolved content correctly reflects all of the original file outside the conflict markers: [6](#0-5) 

**Note on confidence**: I was unable to fully verify `reassembleResolvedFile`'s current line/hunk-matching logic (the function that reconstructs the final file from raw per-hunk resolutions) before the tool budget ran out, so I cannot confirm whether the currently-shipped reassembly logic still has any residual boundary-miscount issue beyond the one already fixed in #22349. This should be reviewed directly in `app/src/lib/copilot-conflict-resolution.ts` for a fully conclusive assessment.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L148-156)
```typescript
/**
 * Maximum combined size (in characters) of the actual conflict content in a
 * single file — the sum of the ours/base/theirs text across every hunk.
 *
 * Unlike a whole-file cap, this measures what we actually send to the model, so
 * it protects prompt size and output quality (truncation/malformed JSON)
 * without penalising large files whose conflicts are small.
 */
const MAX_CONFLICT_CONTENT_SIZE = 262_144 // 256KB
```

**File:** app/src/lib/copilot-conflict-context.ts (L294-315)
```typescript
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

**File:** changelog.json (L93-96)
```json
    "3.5.13-beta3": [
      "[Fixed] Recover conflict dialog from permanently frozen state when conflict state becomes invalid, preventing users from needing to restart the app - #22348",
      "[Fixed] Resolve Copilot conflict resolution data loss where file content outside conflicted regions was overwritten when using AI-assisted conflict resolution - #22349"
    ],
```

**File:** app/src/lib/stores/app-store.ts (L7169-7268)
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
    }
```
