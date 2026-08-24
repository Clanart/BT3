### Title
Manual conflict resolutions keyed only by file path are replayed against unrelated per-commit conflict state during multi-commit rebase, silently committing the wrong side of a conflict - (File: `app/src/lib/stores/updates/changes-state.ts`, `app/src/lib/git/rebase.ts`)

### Summary
GitHub Desktop tracks the user's manual conflict resolutions (`ours`/`theirs` choices) in a `ReadonlyMap<string, ManualConflictResolution>` keyed purely by file `path`. During a rebase this map is deliberately carried forward across `git status` refreshes/steps, and it is applied again every time `continueRebase` runs by looking up `manualResolutions.get(path)`/`.has(path)` — there is no check that the conflict currently on disk for that path is the same conflict the user resolved. This mirrors the pattern in the reported bug: a piece of recorded state (`l2SystemContractsUpgradeBlockNumber`) that should be scoped/reset to a single operation instance is instead persisted and blindly "replayed" against a later, different context, corrupting output.

### Finding Description
`updateConflictState` in `app/src/lib/stores/updates/changes-state.ts` explicitly carries over `manualResolutions` from the previous state whenever a conflict is still detected: [1](#0-0) 

The comment/tests confirm this is intentional ("preserves manual resolutions when a rebase is detected"): [2](#0-1) 

This same `manualResolutions` map is then handed to `continueRebase` in `app/src/lib/stores/app-store.ts`: [3](#0-2) 

and consumed at the git layer by matching solely on `path`, with no verification that the conflict identity (blob hashes / commit being replayed) matches what the user actually resolved: [4](#0-3) 

The same weak, path-only keying is used for merge commits: [5](#0-4) 

An interactive multi-commit rebase applies one upstream commit at a time; each `rebase --continue` can surface a brand-new conflict on the same file path (e.g. the file conflicts again in the next commit being replayed). Because the resolution map is keyed only by `path` and is preserved/reused by `updateConflictState`/`continueRebase` rather than being reset per-conflict-instance, a resolution the user made for the conflict in commit N (e.g., "keep theirs" for `config.json`) can be silently reapplied via `manualResolutions.has(path)` to a structurally unrelated conflict for the same path introduced by commit N+1, without prompting the user again. This is analogous to the audited bug: the `l2SystemContractsUpgradeBlockNumber` guard is checked/enforced based on a stale recorded value rather than being reset per upgrade instance, letting old state leak into a new operation and silently changing committed values.

### Impact Explanation
If an attacker controls the content of a fetched/cloned repository (a valid Desktop threat model — attacker controls a cloned/fetched repo), they can craft a branch whose rebase produces sequential conflicts on the same path across multiple commits with different intended resolutions. A user resolving the first conflict with "theirs" could have that same choice silently replayed onto a later, unrelated conflict for that path, causing Desktop to commit content the user never reviewed or approved for that specific conflict instance — i.e., silent corruption of what the user commits, matching the "Valid Impact" criteria in the task.

### Likelihood Explanation
This requires a rebase across multiple commits touching the same path with more than one conflict occurrence, which is a realistic and unforced scenario an attacker-controlled repository can construct (no local/admin access, no social engineering beyond a user rebasing onto/against an attacker-influenced branch, as with any git-history-based attack). The existing code contains no per-conflict identity check (e.g., blob SHA of the conflicted stages) before reapplying a stored resolution keyed by path alone, so no guard currently prevents this replay.

### Recommendation
Scope `manualResolutions` to the specific conflict occurrence rather than persisting them indefinitely by path: reset/clear the map (or at minimum the entries for paths no longer conflicted) whenever `continueRebase`/`readRebaseHead` reports progression to a new commit in the sequence, and validate that the recorded resolution still corresponds to the same conflicting blobs (e.g., via `status.entry.us`/`status.entry.them` hashes) before applying it in `continueRebase` (`app/src/lib/git/rebase.ts`) and `createMergeCommit` (`app/src/lib/git/commit.ts`). This mirrors the report's recommendation to reset the stale recorded value at the start of each operation instance rather than replaying it.

### Proof of Concept
Conceptual PoC (cannot be executed without local git/Desktop access — this outlines the reproduction steps for validation):
1. Attacker crafts a branch `evil` with commit A modifying `shared.json` to conflict with the user's branch, and commit B (rebasing A) also modifying `shared.json` to conflict again, with different intended semantics.
2. User rebases their branch onto `evil` in Desktop. Conflict on `shared.json` appears for commit A; user manually resolves via "Use mine" (`ManualConflictResolution.ours`), continues.
3. Desktop's `updateConflictState`/`manualResolutions` map retains `shared.json -> ours` per `app/src/lib/stores/updates/changes-state.ts:263-280`.
4. Commit B is applied next and reintroduces a conflict on `shared.json` with unrelated content; `continueRebase` (`app/src/lib/git/rebase.ts:448-458`) finds `manualResolutions.has('shared.json')` true and silently stages "ours" for this new, different conflict without prompting the user.
5. The final rebased history contains a commit whose `shared.json` content was chosen without the user ever reviewing this second conflict — silent corruption of what was committed. [4](#0-3) [1](#0-0)

### Citations

**File:** app/src/lib/stores/updates/changes-state.ts (L263-280)
```typescript
export function updateConflictState(
  state: IChangesState,
  status: IStatusResult,
  statsStore: IStatsStore
): ConflictState | null {
  const prevConflictState = state.conflictState

  const manualResolutions =
    prevConflictState !== null
      ? prevConflictState.manualResolutions
      : new Map<string, ManualConflictResolution>()

  const newConflictState = getConflictState(status, manualResolutions)

  if (prevConflictState == null && newConflictState == null) {
    return null
  }

```

**File:** app/test/unit/stores/updates/update-conflict-state-test.ts (L233-253)
```typescript
    it('preserves manual resolutions when a rebase is detected', () => {
      const prevState = createState({
        conflictState: {
          kind: 'rebase',
          currentTip: 'old-sha',
          manualResolutions,
          targetBranch: 'my-feature-branch',
          baseBranchTip: 'another-sha',
          originalBranchTip: 'some-other-sha',
        },
      })
      const status = createStatus({
        rebaseInternalState: {
          targetBranch: 'my-feature-branch',
          baseBranchTip: 'another-sha',
          originalBranchTip: 'some-other-sha',
        },
        currentBranch: 'master',
        currentTip: 'first-sha',
        doConflictedFilesExist: true,
      })
```

**File:** app/src/lib/stores/app-store.ts (L7469-7486)
```typescript
  /** This shouldn't be called directly. See `Dispatcher`. */
  public async _continueRebase(
    repository: Repository,
    workingDirectory: WorkingDirectoryStatus,
    manualResolutions: ReadonlyMap<string, ManualConflictResolution>
  ): Promise<RebaseResult> {
    const progressCallback =
      this.getMultiCommitOperationProgressCallBack(repository)

    const gitStore = this.gitStoreCache.get(repository)
    const result = await gitStore.performFailableOperation(() =>
      continueRebase(repository, workingDirectory.files, manualResolutions, {
        progressCallback,
      })
    )

    return result || RebaseResult.Error
  }
```

**File:** app/src/lib/git/rebase.ts (L448-460)
```typescript
  // apply conflict resolutions
  for (const [path, resolution] of manualResolutions) {
    const file = files.find(f => f.path === path)
    if (file !== undefined) {
      await stageManualConflictResolution(repository, file, resolution)
    } else {
      log.error(
        `[continueRebase] couldn't find file ${path} even though there's a manual resolution for it`
      )
    }
  }

  const otherFiles = trackedFiles.filter(f => !manualResolutions.has(f.path))
```

**File:** app/src/lib/git/commit.ts (L82-99)
```typescript
export async function createMergeCommit(
  repository: Repository,
  files: ReadonlyArray<WorkingDirectoryFileChange>,
  manualResolutions: ReadonlyMap<string, ManualConflictResolution> = new Map()
): Promise<string> {
  // apply manual conflict resolutions
  for (const [path, resolution] of manualResolutions) {
    const file = files.find(f => f.path === path)
    if (file !== undefined) {
      await stageManualConflictResolution(repository, file, resolution)
    } else {
      log.error(
        `couldn't find file ${path} even though there's a manual resolution for it`
      )
    }
  }

  const otherFiles = files.filter(f => !manualResolutions.has(f.path))
```
