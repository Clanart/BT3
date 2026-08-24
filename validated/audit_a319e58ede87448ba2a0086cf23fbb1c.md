### Title
`conflictMarkerCount === 0` is trusted as sole proof of resolution, allowing an attacker-crafted merge to be silently committed without conflict content ever being reviewed - (File: `app/src/lib/status.ts`)

### Summary
Analogous to the Nouns Builder `state()` function using a single boundary comparison on a vote tally to gate a state transition, GitHub Desktop uses a single boundary comparison on `conflictMarkerCount` (`=== 0` / `> 0`) to gate the "is this file actually resolved" state transition that controls whether conflicted content is silently staged and committed. The count is produced externally by `git diff --check` and is never re-validated against the literal file content at the point of trust, so any conflict content that `git diff --check`'s narrow "leftover conflict marker" diagnostic fails to flag causes Desktop to treat a still-conflicted/attacker-crafted file as resolved.

### Finding Description
`getFilesWithConflictMarkers` derives the authoritative marker count purely by parsing `git diff --check` output for the string `leftover conflict marker`: [1](#0-0) 

That number becomes `conflictMarkerCount` on the file's status object: [2](#0-1) 

This single integer is then treated as ground truth by multiple independent call sites that decide whether a file is safe to stage/commit without further user review, using nothing more than an equality/threshold check on the count — exactly the same pattern as the vulnerable `Governor.state()` comparison:

- `hasUnresolvedConflicts` — `status.conflictMarkerCount > 0`: [3](#0-2) 
- `mapStatus` renders the file as "Resolved" the moment the count hits zero: [4](#0-3) 
- `isFileResolvedExternally` in the Copilot conflicts flow treats `conflictMarkerCount === 0` as proof the user (or an editor) fixed the file, which **skips writing/overwriting it** and lets the on-disk content stand as-is: [5](#0-4) 
- `_applyCopilotConflictResolutions` uses the exact same check to decide not to touch a file before staging it (`git add`) as part of finishing the merge: [6](#0-5) 
- `stageManualConflictResolution` also short-circuits and does nothing when `conflictMarkerCount === 0`, silently accepting whatever is on disk: [7](#0-6) 

None of these call sites independently re-parse the file for literal `<<<<<<<`/`=======`/`>>>>>>>` content before staging/committing — they all defer to the single count produced once by `git diff --check`. Desktop's own conflict-hunk parser (used for the Copilot flow) demonstrates that marker detection is fragile and pattern-sensitive: it explicitly has to special-case markers appearing "inside content" and malformed hunks with no closing marker, showing that marker recognition is not a solved, unambiguous problem: [8](#0-7) 

Because the merge/rebase/cherry-pick source is attacker-controlled (a remote branch, PR, or fetched ref the victim merges/rebases against), an attacker can craft a file whose conflicted region does not match git's specific "leftover conflict marker" line pattern (e.g., through marker-adjacent formatting that `git diff --check` doesn't flag as a leftover marker) while the region is still effectively unresolved/attacker-controlled content. Since Desktop's guards only check `conflictMarkerCount`, such a file would be displayed as "Resolved," automatically staged, and committed/pushed without the user ever reviewing its actual content — a silent corruption of what the user commits or pushes, sourced entirely from an attacker-controlled repository object.

### Impact Explanation
If exploited, a victim who merges/rebases/cherry-picks a malicious branch could have Desktop silently commit and push content the victim never reviewed and did not intend — directly matching the "silent corruption of what the user commits or pushes" impact class from an attacker-controlled remote/repository object. This could be used to smuggle backdoored code, secrets exfiltration hooks, or misleading source changes into the victim's commit history and upstream repository under the victim's identity.

### Likelihood Explanation
Medium. It requires the specific conflict content to evade git's `diff --check` "leftover conflict marker" heuristic while still being treated as content the user intended to accept, and it also requires the user to go through Desktop's guided merge-conflict flow (manual or Copilot-assisted) rather than manually inspecting every file. This mirrors the C4 finding's own severity reasoning (the Governor bug also required a narrow edge condition — exact 50/50 votes — to trigger, and was still rated Medium). I was not able to fully verify git's exact `diff --check` marker regex against Desktop's own marker regexes (`oursMarker`, `baseMarker`, `separatorMarker`, `theirsMarker` in `app/src/lib/copilot-conflict-context.ts`) in this session to construct a concrete byte-for-byte bypass string, so likelihood should be treated as a plausible architectural weakness pending a dedicated crafted-input verification pass rather than a fully demonstrated exploit.

### Recommendation
Do not rely solely on the externally-derived `conflictMarkerCount` integer as proof of resolution at commit/stage time. Before staging or skipping a file in `_applyCopilotConflictResolutions`, `stageManualConflictResolution`, and `hasUnresolvedConflicts`, re-scan the actual file content directly for literal conflict-marker sequences (`<<<<<<<`, `=======`, `>>>>>>>`, and diff3 `|||||||`) using a single canonical, well-tested matcher shared by all these call sites, rather than trusting a count computed once via `git diff --check`'s output parsing. Treat any mismatch between the cached count and a fresh scan as "still conflicted" and block the automatic staging/commit path.

### Proof of Concept
A full working PoC requires confirming the exact divergence between git's `diff --check` "leftover conflict marker" regex and the literal conflict-marker content the attacker plants in a merge source (something I could not fully verify from the indexed code in this session — `app/src/lib/copilot-conflict-context.ts`'s marker regex definitions were not retrievable before the tool budget ran out). Conceptually the PoC path is:
1. Attacker prepares a branch/commit whose changes, when merged/rebased into the victim's branch, produce a conflicted file containing content that functions as an unresolved conflict artifact but does not trigger git's `leftover conflict marker` diagnostic in `git diff --check` (e.g., by exploiting a formatting edge case in that check, analogous to the marker-recognition edge cases already acknowledged in `app/test/unit/copilot-conflict-context-test.ts`).
2. Victim merges/rebases against the attacker's branch in Desktop; `getFilesWithConflictMarkers` records `conflictMarkerCount: 0` for the crafted file.
3. `hasUnresolvedConflicts`/`isFileResolvedExternally`/`mapStatus` report the file as fully "Resolved."
4. The user proceeds through the merge/conflict dialog (or Copilot conflict-resolution "Continue"); `_applyCopilotConflictResolutions`/`stageManualConflictResolution` skip rewriting the file and stage it as-is via `git add`.
5. The merge commit is created and later pushed, embedding the attacker's unreviewed content.

Because I could not verify the exact marker-regex mismatch that would make step 1 concrete, this should be treated as a strong architectural lead requiring a dedicated fuzzing/verification pass against `git diff --check`'s marker regex versus Desktop's own detectors before being escalated as a confirmed, reproducible vulnerability.

### Citations

**File:** app/src/lib/git/diff-check.ts (L9-26)
```typescript
export async function getFilesWithConflictMarkers(
  repositoryPath: string
): Promise<Map<string, number>> {
  const { stdout } = await git(
    ['diff', '--check'],
    repositoryPath,
    'getFilesWithConflictMarkers',
    { successExitCodes: new Set([0, 2]) }
  )

  const files = new Map<string, number>()
  const matches = stdout.matchAll(/^(.+):\d+: leftover conflict marker/gm)

  for (const [, path] of matches) {
    files.set(path, (files.get(path) ?? 0) + 1)
  }

  return files
```

**File:** app/src/lib/git/status.ts (L83-104)
```typescript
function parseConflictedState(
  entry: UnmergedEntry,
  path: string,
  conflictDetails: ConflictFilesDetails
): ConflictedFileStatus {
  switch (entry.action) {
    case UnmergedEntrySummary.BothAdded: {
      const isBinary = conflictDetails.binaryFilePaths.includes(path)
      if (!isBinary) {
        return {
          kind: AppFileStatusKind.Conflicted,
          entry,
          conflictMarkerCount:
            conflictDetails.conflictCountsByPath.get(path) || 0,
        }
      } else {
        return {
          kind: AppFileStatusKind.Conflicted,
          entry,
        }
      }
    }
```

**File:** app/src/lib/status.ts (L33-37)
```typescript
    case AppFileStatusKind.Conflicted:
      if (isConflictWithMarkers(status)) {
        const conflictsCount = status.conflictMarkerCount
        return conflictsCount > 0 ? 'Conflicted' : 'Resolved'
      }
```

**File:** app/src/lib/status.ts (L65-84)
```typescript
/**
 * Determine if we have any conflict markers or if its been resolved manually
 */
export function hasUnresolvedConflicts(
  status: ConflictedFileStatus,
  manualResolution?: ManualConflictResolution
) {
  // if there's a manual resolution, the file does not have unresolved conflicts
  if (manualResolution !== undefined) {
    return false
  }

  if (isConflictWithMarkers(status)) {
    // text file may have conflict markers present
    return status.conflictMarkerCount > 0
  }

  // binary file doesn't contain markers
  return true
}
```

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx (L361-373)
```typescript
  private isFileResolvedExternally(file: WorkingDirectoryFileChange): boolean {
    if (!isConflictedFile(file.status)) {
      return false
    }
    // A file with no remaining conflict markers has been resolved in an editor.
    // This wins even when a Current/Incoming choice was previously picked from
    // the dropdown — the on-disk edit is the source of truth, so we show the
    // resolved state rather than the stale dropdown selection.
    if (isConflictWithMarkers(file.status)) {
      return file.status.conflictMarkerCount === 0
    }
    return false
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

**File:** app/src/lib/git/stage.ts (L34-39)
```typescript
  if (isConflictWithMarkers(status) && status.conflictMarkerCount === 0) {
    // If somehow the user used the Desktop UI to solve the conflict via ours/theirs
    // but afterwards resolved manually the conflicts via an editor, used the manually
    // resolved file.
    return
  }
```

**File:** app/test/unit/copilot-conflict-context-test.ts (L326-357)
```typescript
    it('does not treat markers inside content as boundaries', () => {
      // Conflict markers must start at column 0 with exactly 7 characters
      const content = [
        '<<<<<<< HEAD',
        'const s = "<<<<<<< not a real marker"',
        '=======',
        'const s = ">>>>>>> also not real"',
        '>>>>>>> feature',
      ].join('\n')

      const hunks = extractConflictHunks(content)

      assert.equal(hunks.length, 1)
      assert.equal(
        hunks[0].oursContent,
        'const s = "<<<<<<< not a real marker"'
      )
      assert.equal(hunks[0].theirsContent, 'const s = ">>>>>>> also not real"')
    })

    it('skips a malformed hunk with no closing marker', () => {
      const content = [
        '<<<<<<< HEAD',
        'ours',
        '=======',
        'theirs without closing marker',
      ].join('\n')

      const hunks = extractConflictHunks(content)

      assert.equal(hunks.length, 0)
    })
```
