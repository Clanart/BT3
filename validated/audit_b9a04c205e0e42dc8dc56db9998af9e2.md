Based on the codebase evidence, I found a solid Desktop analog to the "handle handler crashes correctly" bug class: instead of a crash being silently swallowed, an internal Git command failure is silently converted into a default value that represents "no conflicts," which can let a user commit a file that Desktop still displays/treats as text with unresolved conflicts, while its true count was never re-checked.

### Title
Silent exception handling in `getConflictDetails` can zero out conflict-marker counts for a maliciously-crafted repository, letting users commit files that appear "resolved" - ([File: app/src/lib/git/status.ts])

### Summary
Desktop determines whether a conflicted file still has unresolved textual conflict markers by running `git diff --check` and `git diff --numstat`/`git check-attr` and storing per-path marker counts and binary-file lists in `getConflictDetails`. Any exception thrown by those Git invocations — for the *whole* set of conflicted files, not just the failing one — is caught, logged, and replaced with empty maps, exactly as the Penumbra report described (a handler that hits an unexpected error path should stop/escalate, but instead the error is swallowed and processing continues with a "default" answer).

### Finding Description
`getConflictDetails` wraps the calls to `getMergeConflictDetails`/`getRebaseConflictDetails`/`getWorkingDirectoryConflictDetails` in a try/catch that, on **any** thrown error, discards all partial results and returns empty `conflictCountsByPath`/`binaryFilePaths` collections for the entire status computation: [1](#0-0) 

`getWorkingDirectoryConflictDetails` similarly swallows an error from `getBinaryPaths` with a bare empty catch: [2](#0-1) 

Those maps feed directly into `parseConflictedState`, which sets `conflictMarkerCount: conflictDetails.conflictCountsByPath.get(path) || 0` whenever the path isn't in the (now-empty) map: [3](#0-2) 

`conflictMarkerCount === 0` is exactly the signal the UI/state layer uses to treat a conflicted file as resolved: [4](#0-3) [5](#0-4) 

The underlying Git calls that can throw are `getFilesWithConflictMarkers` (`git diff --check`) and `getBinaryPaths` (`git diff --numstat` + `git check-attr --stdin`), both of which operate on attacker-controlled repository content (working tree files, conflicted paths, `.gitattributes` merge drivers): [6](#0-5) [7](#0-6) 

Because a repository author fully controls file names, `.gitattributes` `merge` driver assignments, and file contents that will be checked out into the conflicted index (e.g. via a crafted merge commit that a victim later fetches and merges/rebases), an unexpected exit code or process error from any of these Git subcommands (not part of the explicit `successExitCodes` set) causes the *entire* per-repository conflict detail computation to be discarded — silently, for every conflicted file in that status refresh, not just the offending one.

### Impact Explanation
If the conflict-detection helper throws for any conflicted file (e.g. due to a crafted filename/attribute combination that makes `check-attr --stdin` or `diff --numstat` exit unexpectedly), Desktop falls back to reporting `conflictMarkerCount: 0` for text conflicts across the board. The `mapStatus` and `hasUnresolvedConflicts` helpers then present these files as `"Resolved"`, unblocking the "Continue"/commit action in the conflicts UI even though the working tree files may still contain literal `<<<<<<<`/`=======`/`>>>>>>>` markers. This is a silent-corruption-of-what-the-user-commits scenario matching the "Valid Impact" criteria: a malicious/fetched repository can cause the local Desktop state (and the commit content it stages/pushes) to diverge from what the user believes they resolved, without any indication of failure.

### Likelihood Explanation
This requires the attacker to control the content of a repository the victim merges/rebases (a plausible untrusted-repo scenario for Desktop), and for one of the auxiliary Git calls (`diff --check`, `diff --numstat`, `check-attr --stdin`) to exit with a status Desktop didn't anticipate. This is a narrower trigger than a guaranteed crash, so likelihood is moderate — it depends on finding a Git-level condition (unusual file names/attributes/encoding) that reliably makes one of these auxiliary commands fail rather than succeed with 0/2 exit codes. No PoC exit-code trigger was found in the indexed code; this should be validated experimentally against real Git behavior.

### Recommendation
- Do not blanket-swallow errors in `getConflictDetails`/`getWorkingDirectoryConflictDetails`; distinguish "expected/benign" failures (e.g., `HEAD` missing) from unexpected ones.
- On an unexpected failure, surface an explicit "unknown/could not determine conflict state" status for the affected file(s) rather than defaulting to `conflictMarkerCount: 0`, so the UI never silently treats an indeterminate conflict as resolved.
- Fail closed (treat as still-conflicted / block commit) rather than fail open when conflict-marker detection cannot complete.

### Proof of Concept
Conceptual: 
1. Attacker crafts a repository/branch such that merging it produces conflicted files whose paths or `.gitattributes` `merge` driver assignment cause `git check-attr --stdin` or `git diff --numstat` to exit with a status outside the expected `successExitCodes`.
2. Victim fetches/merges this branch in Desktop; `getStatus` → `getConflictDetails` throws inside `getMergeConflictDetails`.
3. The catch in `getConflictDetails` (app/src/lib/git/status.ts:492-497) discards results, returning empty maps; all conflicted files get `conflictMarkerCount: 0`.
4. `mapStatus`/`hasUnresolvedConflicts` report every file as "Resolved," and Desktop's conflict dialog allows the user to commit the merge with unresolved conflict markers still present in the files.

Because I could not confirm inside the indexed code an exact Git invocation that reliably produces such an unexpected exit code, this should be treated as a plausible-but-unverified analog requiring hands-on confirmation before being reported as a concrete vulnerability.

### Citations

**File:** app/src/lib/git/status.ts (L83-120)
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
    case UnmergedEntrySummary.BothModified: {
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

**File:** app/src/lib/git/status.ts (L439-447)
```typescript
  let binaryFilePaths: ReadonlyArray<string> = []
  try {
    // its totally fine if HEAD doesn't exist, which throws an error
    binaryFilePaths = await getBinaryPaths(
      repository,
      'HEAD',
      conflictedFilesInIndex
    )
  } catch (error) {}
```

**File:** app/src/lib/git/status.ts (L468-502)
```typescript
async function getConflictDetails(
  repository: Repository,
  mergeHeadFound: boolean,
  conflictedFilesInIndex: ReadonlyArray<IStatusEntry>,
  rebaseInternalState: RebaseInternalState | null
): Promise<ConflictFilesDetails> {
  try {
    if (mergeHeadFound) {
      return await getMergeConflictDetails(repository, conflictedFilesInIndex)
    }

    if (rebaseInternalState !== null) {
      return await getRebaseConflictDetails(repository, conflictedFilesInIndex)
    }

    // If there's conflicted files in the index but we don't have a merge head
    // or a rebase internal state, then we're likely in a situation where a
    // stash has introduced conflicts
    if (conflictedFilesInIndex.length > 0) {
      return await getWorkingDirectoryConflictDetails(
        repository,
        conflictedFilesInIndex
      )
    }
  } catch (error) {
    log.error(
      'Unexpected error from git operations in getConflictDetails',
      error
    )
  }
  return {
    conflictCountsByPath: new Map<string, number>(),
    binaryFilePaths: new Array<string>(),
  }
}
```

**File:** app/src/lib/status.ts (L22-45)
```typescript
export function mapStatus(status: AppFileStatus): string {
  switch (status.kind) {
    case AppFileStatusKind.New:
    case AppFileStatusKind.Untracked:
      return 'New'
    case AppFileStatusKind.Modified:
      return 'Modified'
    case AppFileStatusKind.Deleted:
      return 'Deleted'
    case AppFileStatusKind.Renamed:
      return 'Renamed'
    case AppFileStatusKind.Conflicted:
      if (isConflictWithMarkers(status)) {
        const conflictsCount = status.conflictMarkerCount
        return conflictsCount > 0 ? 'Conflicted' : 'Resolved'
      }

      return 'Conflicted'
    case AppFileStatusKind.Copied:
      return 'Copied'
    default:
      return assertNever(status, `Unknown file status ${status}`)
  }
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

**File:** app/src/lib/git/diff.ts (L948-997)
```typescript
export async function getBinaryPaths(
  repository: Repository,
  ref: string,
  conflictedFilesInIndex: ReadonlyArray<IStatusEntry>
): Promise<ReadonlyArray<string>> {
  const [detectedBinaryFiles, conflictedFilesUsingBinaryMergeDriver] =
    await Promise.all([
      getDetectedBinaryFiles(repository, ref),
      getFilesUsingBinaryMergeDriver(repository, conflictedFilesInIndex),
    ])

  return Array.from(
    new Set([...detectedBinaryFiles, ...conflictedFilesUsingBinaryMergeDriver])
  )
}

/**
 * Runs diff --numstat to get the list of files that have changed and which
 * Git have detected as binary files
 */
async function getDetectedBinaryFiles(repository: Repository, ref: string) {
  const { stdout } = await git(
    ['diff', '--numstat', '-z', ref],
    repository.path,
    'getBinaryPaths'
  )

  return Array.from(stdout.matchAll(binaryListRegex), m => m[1])
}

const binaryListRegex = /-\t-\t(?:\0.+\0)?([^\0]*)/gi

async function getFilesUsingBinaryMergeDriver(
  repository: Repository,
  files: ReadonlyArray<IStatusEntry>
) {
  const { stdout } = await git(
    ['check-attr', '--stdin', '-z', 'merge'],
    repository.path,
    'getConflictedFilesUsingBinaryMergeDriver',
    {
      stdin: files.map(f => f.path).join('\0'),
    }
  )

  return createLogParser({ path: '', attr: '', value: '' })
    .parse(stdout)
    .filter(x => x.attr === 'merge' && x.value === 'binary')
    .map(x => x.path)
}
```
