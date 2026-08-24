Found a concrete analog: the `stageManualConflictResolution` / manual "ours"/"theirs" resolution logic (`app/src/lib/git/stage.ts` and `ManualConflictResolution` model) hard-codes the git status-derived `status.entry.us` / `status.entry.them` fields as authoritative for "ours" and "theirs" without accounting for the documented rebase inversion, while a sibling code path (`getResolutionDiff` in `app/src/lib/git/diff.ts`) explicitly documents that git swaps these roles during a rebase.

### Title
Manual conflict resolution stages the wrong side's changes during a rebase because "ours"/"theirs" are not remapped like the sibling diff-preview code does - (File: `app/src/lib/git/stage.ts`)

### Summary
`git`'s definition of "ours" (stage #2) and "theirs" (stage #3) inverts during a `rebase` compared to a `merge`: during a merge, "ours" is the current branch and "theirs" is the branch being merged in; during a rebase, git treats the upstream/target branch as "ours" and the commit being replayed as "theirs". `app/src/lib/git/diff.ts`'s `getResolutionDiff` explicitly documents this and pushes the responsibility of remapping onto its caller [1](#0-0) . The manual conflict staging path, `stageManualConflictResolution`, reads `status.entry.us`/`status.entry.them` (the raw git stage 2/3 mapping from `mapStatus` in `app/src/lib/status-parser.ts`) and then calls `checkoutConflictedFile`/`git checkout --ours|--theirs` directly based on the user's UI selection of "Current"/"Incoming" without any branch/operation-kind-aware inversion [2](#0-1) [3](#0-2) .

### Finding Description
This is the same broken-invariant shape as the TWAP oracle bug: an external, potentially attacker-influenced system (git itself, or more precisely a maliciously crafted upstream/rebase target repository controlling the direction/participants of a rebase) can silently swap the "order" of two named sides (`ours`/`theirs` here, `token0`/`token1` there), while the calling code labels UI options ("Current branch" vs "Incoming branch") and stages/writes data assuming the label always maps to the same git stage.

- `ManualConflictResolution` is a plain `ours`/`theirs` enum passed straight to `git checkout --ours`/`--theirs` [4](#0-3) .
- The UI (`getManualResolutionMenuItems`, `getBranchForResolution` in `app/src/ui/lib/conflicts/unmerged-file.tsx`) labels these choices with `ourBranch`/`theirBranch` strings supplied by the caller [5](#0-4) .
- Those branch labels are computed in `app-store.ts`'s conflict-branch resolution logic, which does correctly special-case rebase (`ourBranch = conflictState.baseBranch`, `theirBranch = conflictState.targetBranch`) vs merge (`ourBranch = conflictState.currentBranch`) [6](#0-5) .
- However, `stageManualConflictResolution` and `checkoutConflictedFile` never consult `conflictState.kind`/operation type — they always pass the user's `ManualConflictResolution.ours`/`theirs` selection straight through to `git checkout --ours|--theirs`, trusting that git's internal stage-2/stage-3 assignment for the *current operation* matches what the branch-label logic assumed [7](#0-6) .
- The `getResolutionDiff` docstring proves the Desktop team is aware git can invert this mapping and explicitly punts the remap responsibility to "the caller" [1](#0-0)  — but I could not find equivalent remap logic in the staging/checkout path within the available index, only in the diff-preview path.

### Impact Explanation
If the ours/theirs label-to-git-stage mapping is inconsistent between the branch-labeling code (`app-store.ts`) and the actual `git checkout --ours/--theirs` semantics for a given operation (rebase vs merge vs cherry-pick), a user who clicks "Use current file from X" could have git silently check out and stage the *other* side's content — i.e., committing/pushing code the user did not intend to keep, potentially reintroducing reverted or malicious changes from an attacker-controlled remote/rebase target, with no error or warning (silent corruption of what the user commits).

### Likelihood Explanation
This requires the user to be resolving a merge conflict via a rebase (not just a merge) and to interact with the manual "Use current/incoming" resolution UI — a common, unprivileged workflow when pulling/rebasing against an untrusted or compromised remote. Given the codebase itself documents this git quirk as a known pitfall in one code path but I could not confirm the same safeguard exists in the staging path with the tools available, likelihood is plausible but **not fully confirmed** — I could not locate the exact runtime wiring that determines `ManualConflictResolution` values from `conflictState.kind` before they reach `stageManualConflictResolution`, so it's possible there's a remap step elsewhere in the dispatcher that I didn't surface via search.

### Recommendation
Verify (and if missing, add) that any path constructing a `ManualConflictResolution` for `stageManualConflictResolution`/`checkoutConflictedFile` is aware of `MultiCommitOperationKind` (merge vs rebase vs cherry-pick) and remaps `ours`⇄`theirs` for rebase exactly as `getResolutionDiff`'s comment describes, mirroring the ourBranch/theirBranch selection already done in `app-store.ts`'s conflict-branch logic.

### Proof of Concept
Not independently reproducible from the indexed code alone — I was unable to trace the full call chain from the manual-resolution dropdown UI event through to `stageManualConflictResolution` to confirm whether an operation-kind-aware remap step exists somewhere in `dispatcher.ts` or `app-store.ts` that isn't covered by the current search results. **This should be verified with a full checkout of the repo** (e.g., via a Devin session) by: (1) starting a `rebase` where the target branch modifies a file and the branch being replayed also modifies it, (2) opening the manual conflict resolution dropdown, (3) selecting "Use current file from `<ourBranch>`", and (4) inspecting whether the staged content actually matches `<ourBranch>`'s version or the rebased commit's version, confirming or refuting the swap.

### Citations

**File:** app/src/lib/git/diff.ts (L428-434)
```typescript
 * 2. **Stage mode** — pass `stage: 'ours' | 'theirs'` to read from the
 *    merge index (`git show :2:<path>` or `git show :3:<path>`).
 *    These always refer to git's definition: `ours` = stage 2 (HEAD at
 *    merge time), `theirs` = stage 3 (the commit being merged in). Note
 *    that during a rebase, git swaps these — the upstream branch is "ours"
 *    and the rebased commit is "theirs". The caller is responsible for
 *    mapping user-facing labels to the correct git side.
```

**File:** app/src/lib/git/stage.ts (L22-52)
```typescript
export async function stageManualConflictResolution(
  repository: Repository,
  file: WorkingDirectoryFileChange,
  manualResolution: ManualConflictResolution
): Promise<void> {
  const { status } = file
  // if somehow the file isn't in a conflicted state
  if (!isConflictedFileStatus(status)) {
    log.error(`tried to manually resolve unconflicted file (${file.path})`)
    return
  }

  if (isConflictWithMarkers(status) && status.conflictMarkerCount === 0) {
    // If somehow the user used the Desktop UI to solve the conflict via ours/theirs
    // but afterwards resolved manually the conflicts via an editor, used the manually
    // resolved file.
    return
  }

  const chosen =
    manualResolution === ManualConflictResolution.theirs
      ? status.entry.them
      : status.entry.us

  const addedInBoth =
    status.entry.us === GitStatusEntry.Added &&
    status.entry.them === GitStatusEntry.Added

  if (chosen === GitStatusEntry.UpdatedButUnmerged || addedInBoth) {
    await checkoutConflictedFile(repository, file, manualResolution)
  }
```

**File:** app/src/lib/git/checkout.ts (L221-234)
```typescript
/**
 * Check out either stage #2 (ours) or #3 (theirs) for a conflicted
 * file.
 */
export async function checkoutConflictedFile(
  repository: Repository,
  file: WorkingDirectoryFileChange,
  resolution: ManualConflictResolution
) {
  await git(
    ['checkout', `--${resolution}`, '--', file.path],
    repository.path,
    'checkoutConflictedFile'
  )
```

**File:** app/src/models/manual-conflict-resolution.ts (L1-9)
```typescript
// NOTE: These strings have semantic value, they're passed directly
// as `--ours` and `--theirs` to git checkout. Please be careful
// when modifying this type.
export enum ManualConflictResolution {
  theirs = 'theirs',
  ours = 'ours',
}


```

**File:** app/src/ui/lib/conflicts/unmerged-file.tsx (L496-509)
```typescript
/** returns the name of the branch that corresponds to the chosen manual resolution */
function getBranchForResolution(
  manualResolution: ManualConflictResolution | undefined,
  ourBranch?: string,
  theirBranch?: string
): string | undefined {
  if (manualResolution === ManualConflictResolution.ours) {
    return ourBranch
  }
  if (manualResolution === ManualConflictResolution.theirs) {
    return theirBranch
  }
  return undefined
}
```

**File:** app/src/lib/stores/app-store.ts (L3200-3224)
```typescript
    const { manualResolutions } = conflictState
    let ourBranch, theirBranch

    if (isMergeConflictState(conflictState)) {
      theirBranch = await this.getMergeConflictsTheirBranch(
        repository,
        status.squashMsgFound,
        multiCommitOperationState
      )
      ourBranch = conflictState.currentBranch
    } else if (isRebaseConflictState(conflictState)) {
      theirBranch = conflictState.targetBranch
      ourBranch = conflictState.baseBranch
    } else if (isCherryPickConflictState(conflictState)) {
      if (
        multiCommitOperationState !== null &&
        multiCommitOperationState.operationDetail.kind ===
          MultiCommitOperationKind.CherryPick &&
        multiCommitOperationState.operationDetail.sourceBranch !== null
      ) {
        theirBranch =
          multiCommitOperationState.operationDetail.sourceBranch.name
      }
      ourBranch = conflictState.targetBranchName
    } else {
```
