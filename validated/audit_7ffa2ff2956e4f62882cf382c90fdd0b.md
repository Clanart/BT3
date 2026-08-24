### Title
Conflict-marker resolution status is derived from a regex-parsed `git diff --check` line count that a crafted merge conflict can zero out, causing Desktop to silently treat unresolved conflicts as resolved and commit conflict markers - (File: `app/src/lib/git/diff-check.ts`)

### Summary
Analogous to the Autonolas bug — a critical gating value (`effectiveBond`) is derived from an approximate, out-of-band re-computation that can diverge from ground truth in edge cases and is never re-validated — GitHub Desktop derives its "is this conflicted file actually resolved" signal (`conflictMarkerCount`) from a fragile regex parse of `git diff --check` stdout rather than from an authoritative check of the file content. When the regex fails to match a line that Git did emit (or an attacker's file content prevents `git diff --check` from emitting the expected `leftover conflict marker` line at all), `conflictMarkerCount` silently reports `0`, `mapStatus()` reports the file as `'Resolved'` [1](#0-0) , `hasUnresolvedConflicts()` returns `false` [2](#0-1) , and Desktop allows the file to be committed as-is — potentially with real `<<<<<<<`/`=======`/`>>>>>>>` conflict markers still embedded in the committed blob.

### Finding Description
`getFilesWithConflictMarkers()` runs `git diff --check` and extracts leftover-conflict-marker counts per path using the regex:
```
/^(.+):\d+: leftover conflict marker/gm
``` [3](#0-2) 

This count feeds directly into the file's `AppFileStatus`:
```
conflictMarkerCount: conflictDetails.conflictCountsByPath.get(path) || 0,
``` [4](#0-3) 

...which is then used as the single source of truth for whether a conflict is "resolved":
```
case AppFileStatusKind.Conflicted:
  if (isConflictWithMarkers(status)) {
    const conflictsCount = status.conflictMarkerCount
    return conflictsCount > 0 ? 'Conflicted' : 'Resolved'
  }
``` [5](#0-4) 
```
if (isConflictWithMarkers(status)) {
  return status.conflictMarkerCount > 0
}
``` [6](#0-5) 

The same "no remaining conflict markers = resolved, on-disk edit is the source of truth" logic is repeated in the conflict resolution UI, explicitly overriding any prior manual choice: [7](#0-6) 

And in the manual-resolution staging path, a `conflictMarkerCount === 0` short-circuits and simply leaves the file as-is instead of applying `ours`/`theirs` resolution:
```
if (isConflictWithMarkers(status) && status.conflictMarkerCount === 0) {
  // If somehow the user used the Desktop UI to solve the conflict via ours/theirs
  // but afterwards resolved manually the conflicts via an editor, used the manually
  // resolved file.
  return
}
``` [8](#0-7) 

**The broken invariant:** the code assumes `git diff --check`'s "leftover conflict marker" line count is an exact, always-emitted proxy for "the working-tree file still literally contains conflict markers." That assumption is not guaranteed:
- `git diff --check` only flags conflict-marker lines it recognizes as being at the start of a line in a way its heuristic catches; it is a warning heuristic, not a guaranteed exhaustive scan of every occurrence of `<<<<<<<`/`=======`/`>>>>>>>` (e.g., markers embedded mid-line, in binary-ish content that alters diff behavior, or on lines Git's `diff.c` marker-heuristic skips can fail to be flagged as `leftover conflict marker`).
- The regex `^(.+):\d+: leftover conflict marker` requires the path field to not itself confuse the parser; a path containing a colon followed by digits (or unusual characters produced by `core.quotepath`/rename detection quoting) can shift what is captured as the "path" group, causing `files.set(path, …)` to record the marker count under the wrong (or a non-existent) path key. In `getMergeConflictDetails`/`getRebaseConflictDetails`/`getWorkingDirectoryConflictDetails` this map is looked up by exact `entry.path` [9](#0-8) , so a mismatch of the captured group vs. the real path means `conflictCountsByPath.get(path)` misses and falls back to `|| 0` — i.e., "no markers", exactly like the tokenomics bug's `if (incentives[4] > curMaxBond)` never firing when the value should have moved the other way.

Unlike the tokenomics `effectiveBond` case where the missing branch is explicit (no `else`), here the missing safety net is the same in spirit: there is no independent, ground-truth verification that the file content is actually marker-free before Desktop reports `'Resolved'` and allows staging/committing. The `.diff --check` heuristic is the sole gate, with a `|| 0` fallback that always resolves ambiguity in the direction of "resolved," never in the direction of "still conflicted."

### Impact Explanation
This directly matches the requested impact class of "silent corruption of what the user commits or pushes." A repository author can craft a merge/rebase scenario (e.g., via a branch merged locally, or via content designed to be checked out from a malicious remote) where a conflicted file's on-disk content still contains conflict markers but `git diff --check` does not attribute a `leftover conflict marker` count to that exact path key. Desktop's UI will then show the file as `'Resolved'`/render the "All conflicted files have been resolved" success banner [10](#0-9) , the commit button becomes available, and the user commits and can push a file that visibly (to Git, GitHub, and any downstream tooling) still contains `<<<<<<<`/`=======`/`>>>>>>>` markers baked into tracked source, silently corrupting the commit and potentially the code that consumes it (e.g., broken build/config files pushed to a shared branch).

### Likelihood Explanation
The attacker primitive required — a cloned/fetched repository whose merge produces a conflict with unusual path characters or marker placement that `git diff --check`'s regex/heuristic does not perfectly enumerate — is within the report's allowed "attacker controls a cloned/fetched repository" category, and requires no local/admin access, no leaked credentials, and no unnatural user steps beyond the normal merge/rebase-and-commit workflow that Desktop is built to support. However, exploitability depends on finding a concrete Git diff-check/regex edge case that reliably produces a mismatch (e.g., a path containing `:` immediately followed by digits, or a marker form that `diff --check`'s C heuristic does not flag); I was not able to execute `git diff --check` against a live crafted repository to confirm a specific byte-for-byte bypass, so likelihood should be treated as plausible-but-unconfirmed based on static code review only.

### Recommendation
Do not rely solely on the regex-parsed `git diff --check` count to decide "resolved" vs. "conflicted." Independently verify the absence of conflict markers by scanning the actual working-tree file content for `<<<<<<<`, `=======`/`|||||||`, and `>>>>>>>` marker lines (as Git's own merge machinery defines them) before setting `conflictMarkerCount: 0` or before allowing `stageManualConflictResolution`/commit to proceed. Additionally, harden the regex/path-matching in `getFilesWithConflictMarkers` (e.g., use `git diff --check -z` or `--name-only`/machine-readable output) so that unusual path characters cannot desynchronize the count-to-path mapping, and fail closed (treat as still-conflicted) rather than falling back to `|| 0` when a path cannot be confidently matched.

### Proof of Concept
Conceptual reproduction (not executed against a live repo in this session):
1. Create two branches that conflict in a file whose path, after any Git quoting/rename handling, could plausibly be mis-split by the regex `^(.+):\d+: leftover conflict marker` (e.g., exploring paths/content combinations where the greedy `(.+)` captures more or less than the true path before the `:\d+:` separator), or find/construct conflict-marker placement that `git diff --check` does not flag as `leftover conflict marker` for that specific line.
2. Merge the branches in a repository opened in Desktop, producing a conflicted file whose actual content retains markers.
3. Observe `getFilesWithConflictMarkers()` returning a `conflictCountsByPath` map that does not contain (or misattributes) an entry for the conflicted path [11](#0-10) .
4. Observe `conflictMarkerCount` resolve to `0` via the `|| 0` fallback [12](#0-11) , `mapStatus` reporting `'Resolved'` [5](#0-4) , and the commit UI permitting the commit of the file with markers still present on disk.

Because I could not run Git commands in this environment to confirm a concrete marker/path combination that defeats the regex, this should be validated with a live repro before being treated as a confirmed exploitable finding rather than a structural weakness.

### Citations

**File:** app/src/lib/status.ts (L33-39)
```typescript
    case AppFileStatusKind.Conflicted:
      if (isConflictWithMarkers(status)) {
        const conflictsCount = status.conflictMarkerCount
        return conflictsCount > 0 ? 'Conflicted' : 'Resolved'
      }

      return 'Conflicted'
```

**File:** app/src/lib/status.ts (L68-83)
```typescript
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
```

**File:** app/src/lib/git/diff-check.ts (L9-27)
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
}
```

**File:** app/src/lib/git/status.ts (L88-104)
```typescript
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

**File:** app/src/lib/git/status.ts (L392-453)
```typescript
async function getMergeConflictDetails(
  repository: Repository,
  conflictedFilesInIndex: ReadonlyArray<IStatusEntry>
) {
  const conflictCountsByPath = await getFilesWithConflictMarkers(
    repository.path
  )
  const binaryFilePaths = await getBinaryPaths(
    repository,
    'MERGE_HEAD',
    conflictedFilesInIndex
  )
  return {
    conflictCountsByPath,
    binaryFilePaths,
  }
}

async function getRebaseConflictDetails(
  repository: Repository,
  conflictedFilesInIndex: ReadonlyArray<IStatusEntry>
) {
  const conflictCountsByPath = await getFilesWithConflictMarkers(
    repository.path
  )
  const binaryFilePaths = await getBinaryPaths(
    repository,
    'REBASE_HEAD',
    conflictedFilesInIndex
  )
  return {
    conflictCountsByPath,
    binaryFilePaths,
  }
}

/**
 * We need to do these operations to detect conflicts that were the result
 * of popping a stash into the index
 */
async function getWorkingDirectoryConflictDetails(
  repository: Repository,
  conflictedFilesInIndex: ReadonlyArray<IStatusEntry>
) {
  const conflictCountsByPath = await getFilesWithConflictMarkers(
    repository.path
  )
  let binaryFilePaths: ReadonlyArray<string> = []
  try {
    // its totally fine if HEAD doesn't exist, which throws an error
    binaryFilePaths = await getBinaryPaths(
      repository,
      'HEAD',
      conflictedFilesInIndex
    )
  } catch (error) {}

  return {
    conflictCountsByPath,
    binaryFilePaths,
  }
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

**File:** app/src/lib/git/stage.ts (L34-39)
```typescript
  if (isConflictWithMarkers(status) && status.conflictMarkerCount === 0) {
    // If somehow the user used the Desktop UI to solve the conflict via ours/theirs
    // but afterwards resolved manually the conflicts via an editor, used the manually
    // resolved file.
    return
  }
```

**File:** app/src/ui/multi-commit-operation/dialog/conflicts-dialog.tsx (L229-260)
```typescript
  public renderBanner(conflictedFilesCount: number) {
    const { workingDirectory, manualResolutions } = this.props
    const countResolved = getResolvedFiles(
      workingDirectory,
      manualResolutions
    ).length

    if (countResolved > 0) {
      this.hasSeenResolvedFiles = true
    }

    if (countResolved === 0 && !this.hasSeenResolvedFiles) {
      return
    }

    if (countResolved === 0) {
      return <DialogSuccess>All resolutions have been undone.</DialogSuccess>
    }

    if (conflictedFilesCount === 0) {
      return (
        <DialogSuccess>All conflicted files have been resolved. </DialogSuccess>
      )
    }

    const conflictPluralized = countResolved === 1 ? 'file has' : 'files have'
    return (
      <DialogSuccess>
        {countResolved} conflicted {conflictPluralized} been resolved.
      </DialogSuccess>
    )
  }
```
