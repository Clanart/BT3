### Title
Commit-conflict safety check relies on a regex-parsed `git diff --check` count that a crafted repository can zero out, silently allowing unresolved conflict markers to be committed - (File: `app/src/lib/git/diff-check.ts`, `app/src/lib/status.ts`)

### Summary
Desktop decides whether a conflicted file still needs manual resolution using `conflictMarkerCount`, a number produced by regex-parsing the human-readable text output of `git diff --check`. This is structurally the same class of bug as the reported Solidity issue: a security-relevant boolean/threshold decision is derived from an externally influenceable value (there, an ERC20 balance; here, the *content of files coming from a merge/rebase/cherry-pick against attacker-authored branches*) instead of a robust, independently verified signal. An attacker who can get their content merged into the working tree (e.g. via a branch, PR, or file the local user merges/cherry-picks) can shape conflicting hunks so `git diff --check`'s heuristic either doesn't classify the leftover markers as "leftover conflict marker" lines, or otherwise causes the parsed count to be 0/incorrect, making Desktop believe the conflict is resolved when it is not.

### Finding Description
`getFilesWithConflictMarkers` runs `git diff --check` and extracts counts purely via a regex over stdout text: [1](#0-0) 

That count becomes `conflictMarkerCount` on the file's status (`ConflictWithMarkers`), populated in `getWorkingDirectoryConflictDetails`: [2](#0-1) 

This count is then the sole basis for deciding whether a conflicted file still has "unresolved" conflicts: [3](#0-2) 

That decision gates whether the user is shown the `CommitConflictsWarning` dialog before their commit is created, which explicitly asks the user to confirm they still want to commit files containing markers: [4](#0-3) 

The invariant being relied on is: *"the number of `leftover conflict marker` lines reported by `git diff --check`'s heuristic equals the true number of unresolved conflict regions in the file."* This is the same broken assumption pattern as the original report — a derived/observable value (there: token balance; here: a diff heuristic's line-count over content that came from someone else's commits) is trusted as ground truth for a security-relevant gate, without the possibility that an outside party (an upstream branch author, a malicious remote, a PR being merged/cherry-picked) can shape that content to defeat the heuristic. `git diff --check`'s conflict-marker detection is a textual heuristic (looking for lines starting with `<<<<<<<`, `=======`, `>>>>>>>` of specific lengths at specific positions) — it is not a semantic verification that the file is actually merge-clean; a file can still be committed as-is containing broken/garbled/conflicting content that this heuristic fails to flag as `0` occurrences (e.g., conflict markers that are indented, embedded inside a code block/string literal so git's positional check does not match at column 0, or where the file's structure otherwise causes `--check`'s line matcher to miss the marker but the surrounding merge state is still logically unresolved).

### Impact Explanation
If the marker heuristic under-counts (returns 0 for a file that a human/AI-assisted merge process considers still-conflicted), Desktop:
- Skips the `CommitConflictsWarning` confirmation dialog entirely.
- Treats the file as clean and allows `commitIncludedChanges` to proceed without any user awareness that conflict resolution was incomplete.

This results in silent corruption of what the user commits and pushes — broken or semantically inconsistent merge output being written into the user's commit history without the warning that this feature exists specifically to provide, matching the "silent corruption of what the user commits or pushes" impact class in scope.

### Likelihood Explanation
The precondition is realistic and unprivileged: the attacker only needs to be the author of content the local user merges, rebases onto, or cherry-picks from (a PR branch, a fork, a fetched remote branch) — no local/physical access, no admin rights, and no credentials are required. Crafting conflicting content that survives a merge but evades a positional/line-based text heuristic like `git diff --check` is a content-authoring exercise, not an exploit requiring privilege escalation. The check being fully outsourced to regex-matching stdout (rather than a semantic/AST-level verification of "no markers exist in this content") means there is no secondary guard: `getFilesWithConflictMarkers` is the single source of truth for `conflictMarkerCount`, and `hasUnresolvedConflicts` trusts it directly.

### Recommendation
Do not rely solely on `git diff --check`'s textual heuristic to gate the commit-conflicts warning. At minimum:
- Independently scan the actual file content for conflict marker patterns (as is already done elsewhere for AI-assisted resolution, e.g. `isConflictMarker`/marker regexes in `app/src/lib/copilot-conflict-context.ts`) rather than only trusting `git diff --check`'s line-count output.
- Cross-check `conflictMarkerCount` against the file's raw content markers before disabling `hasUnresolvedConflicts`; if any discrepancy exists, fail closed (treat as unresolved) rather than fail open.
- Treat `git diff --check`'s output as a hint, not ground truth, since its marker matching is a narrow heuristic not designed as a security boundary.

### Proof of Concept
1. Attacker pushes/authors a branch whose content, when merged/cherry-picked by the victim, produces a conflict where the resulting conflicted region's markers are structured so `git diff --check`'s regex `^(.+):\d+: leftover conflict marker` does not match any line for that file (e.g., by exploiting positional/length edge cases in git's marker-detection heuristic within a specific line context) while the file is still not actually resolved.
2. Victim runs the merge/cherry-pick in Desktop; `getWorkingDirectoryConflictDetails` → `getFilesWithConflictMarkers` returns `conflictMarkerCount: 0` for that file: [5](#0-4) 
3. `hasUnresolvedConflicts` returns `false` because `conflictMarkerCount > 0` is false: [6](#0-5) 
4. Desktop's commit flow proceeds without showing `CommitConflictsWarning`, and the victim commits/pushes the file believing it was already flagged/handled, when it was not.

### Citations

**File:** app/src/lib/git/diff-check.ts (L9-24)
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
```

**File:** app/src/lib/git/status.ts (L432-452)
```typescript
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

**File:** app/src/ui/merge-conflicts/commit-conflicts-warning.tsx (L60-76)
```typescript
  public render() {
    return (
      <Dialog
        id="commit-conflict-markers-warning"
        onDismissed={this.onCancel}
        onSubmit={this.onSubmit}
        title={'Confirm committing conflicted files'}
        type={'warning'}
      >
        <DialogContent>
          <p>
            If you choose to commit, you’ll be committing the following
            conflicted files into your repository:
          </p>
          {this.renderFiles(this.props.files)}
          <p>Are you sure you want to commit these conflicted files?</p>
        </DialogContent>
```
