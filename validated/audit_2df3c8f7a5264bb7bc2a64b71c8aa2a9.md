Confirmed: `merge()` in `app/src/lib/git/merge.ts` runs `git merge <branch-name>` by ref name, not by pinned SHA, and the background fetcher in `app/src/lib/stores/helpers/background-fetcher.ts` auto-fetches remote-tracking refs on an interval (as low as 5 minutes) while any dialog is open. This confirms the TOCTOU path: the merge/rebase preview is computed once, but the actual git operation re-resolves the branch name against whatever the remote-tracking ref currently points to.

### Title
Merge/Rebase Confirmation Dialog Executes Against Stale, Unrevalidated Branch State - ([File: app/src/ui/multi-commit-operation/choose-branch/merge-choose-branch-dialog.tsx])

### Summary
The "Merge into Current Branch" and "Rebase Current Branch" dialogs compute a one-time preview (ahead/behind counts, mergeability/conflict status) for a selected branch, then let the user click "Merge"/"Start Rebase" based on that preview. The actual git operation is executed later by branch *name*, not by the SHA that was previewed, and the app performs periodic unattended background fetches of the same remote-tracking ref while the dialog remains open. This creates a check-then-act gap in which the state that generated the "clean merge, N commits" preview the user approved can silently diverge from the state that is actually merged/rebased.

### Finding Description
`MergeChooseBranchDialog.updateStatus()` computes `mergeStatus` and `commitCount` from `determineMergeability(repository, currentBranch, branch)` and `getAheadBehind(repository, range)` at selection time [1](#0-0) . The `start()` handler that fires on "Merge" simply passes the previously selected `Branch` object and cached `mergeStatus` to `dispatcher.mergeBranch` [2](#0-1)  without re-checking that the branch's remote-tracking ref is unchanged.

`_mergeBranch` in the app store then calls `gitStore.merge(sourceBranch, ...)`, which resolves down to `merge()` in `app/src/lib/git/merge.ts`, executing `git merge <branch>` using the branch **name** string, not the SHA that was displayed in the preview [3](#0-2) . The same by-name pattern applies to `updateRebasePreview`, whose preview is computed from `baseBranch.tip.sha` and `targetBranch.tip.sha` snapshotted when the branch was selected [4](#0-3) , while the actual rebase later runs `git rebase <upstream> <branch>` against whatever the ref currently resolves to.

Meanwhile, `BackgroundFetcher` autonomously fetches the repository's remotes on an interval as short as 5 minutes (`MinimumInterval`), driven entirely by server-controlled polling intervals, independent of any dialog being open [5](#0-4) [6](#0-5) . If a compromised/malicious git remote, MITM proxy, or an attacker with push access to the tracked branch updates the remote ref between the moment the user reviews the merge/rebase preview and the moment they click confirm, the background fetch moves the local remote-tracking branch forward or sideways, and the subsequent `git merge`/`git rebase` operates on this new, unreviewed content — not what the "N commits, clean merge" dialog showed the user.

Existing guards do not stop this: `MergeChooseBranchDialog.updateStatus` only re-validates against stale data by comparing `this.state.selectedBranch.tip.sha !== branch.tip.sha` for *its own* async preview race (in case the user re-selects a different branch while a fetch is running) [7](#0-6)  — this check protects against the UI overwriting itself with stale results, but it does not re-verify branch state at the moment `start()` invokes the actual merge, and the merge command itself never receives or checks the previewed SHA at all.

### Impact Explanation
This allows an attacker who controls a git remote/proxy response (or has push access to a branch the victim is about to merge/rebase, e.g. via a compromised or malicious collaborator/server) to have the victim silently merge or rebase in commits the victim never reviewed and did not approve — the confirmation dialog showed different, safe-looking content. This directly matches "silent corruption of what the user commits or pushes," since the merge commit or rebased history the victim eventually pushes upstream can now contain attacker-injected commits.

### Likelihood Explanation
The window is realistically exploitable: background fetch intervals can be as short as 5 minutes (`MinimumInterval`), and the choose-branch dialogs remain open for as long as a user takes to read the preview and click a button — plausibly long enough to overlap with a fetch cycle, especially if the attacker (controlling the remote/proxy) can also influence the `Fetch-Interval` the server reports via `getFetchPollInterval`. No local access, admin rights, or social engineering beyond normal Desktop usage against an attacker-controlled/compromised remote is required.

### Recommendation
1. Capture the exact commit SHA(s) shown in the merge/rebase preview and pass that SHA (not just the mutable branch name) through to the actual git operation, e.g. `git merge <sha>` or verifying `git rev-parse <branch>` equals the previewed SHA immediately before invoking `merge()`/`rebase()`.
2. If the branch's tip has moved since the preview was generated, abort the operation and force the user to re-review an updated preview rather than silently proceeding.
3. Consider suppressing/deferring background fetches while a merge/rebase confirmation dialog with a pending preview is open, or invalidate/refresh the preview immediately after any fetch completes.

### Proof of Concept
1. Victim has Desktop open with a repository tracked against an attacker-controlled or compromised git remote (e.g. shared self-hosted server, MITM proxy, or a branch the attacker can push to).
2. Victim opens "Merge into Current Branch..." and selects `feature-branch`. `updateStatus()` runs `determineMergeability`/`getAheadBehind` and shows "This will merge 2 commits ... clean merge."
3. Before the victim clicks "Merge," `BackgroundFetcher.performAndScheduleFetch` triggers (interval controlled/observed by the attacker) and fetches `feature-branch`, which the attacker has just force-pushed with additional/different commits.
4. Victim clicks "Merge," trusting the previously displayed 2-commit clean-merge summary. `MergeChooseBranchDialog.start()` calls `dispatcher.mergeBranch(repository, selectedBranch, mergeStatus, ...)` with the stale `Branch` object; `_mergeBranch` executes `gitStore.merge(sourceBranch, ...)` → `merge()` runs `git merge feature-branch` by name.
5. Git resolves `feature-branch` to its *current* (attacker-updated) tip, merging in commits the victim never saw in the preview, silently corrupting the resulting merge commit that the victim will subsequently push.

Note: I was not able to fully trace whether any additional UI-level re-fetch suppression exists elsewhere in the codebase (e.g. a global "network operation in progress" lock that might coincidentally block background fetch while the dialog is open) beyond `withPushPullFetch`'s guard on push/pull/fetch dispatcher actions — background fetch appears to be a separate code path (`BackgroundFetcher`) not gated by that same lock, but a full audit of all call sites was not exhaustively completed given tool constraints.

### Citations

**File:** app/src/ui/multi-commit-operation/choose-branch/merge-choose-branch-dialog.tsx (L38-57)
```typescript
  private start = () => {
    if (!this.canStart()) {
      return
    }

    const { selectedBranch, mergeStatus } = this.state
    const { operation, dispatcher, repository } = this.props
    if (!selectedBranch) {
      return
    }

    dispatcher.mergeBranch(
      repository,
      selectedBranch,
      mergeStatus,
      operation === MultiCommitOperationKind.Squash
    )

    dispatcher.closePopup(PopupType.MultiCommitOperation)
  }
```

**File:** app/src/ui/multi-commit-operation/choose-branch/merge-choose-branch-dialog.tsx (L102-140)
```typescript
  private updateStatus = async (branch: Branch) => {
    const { currentBranch, repository } = this.props

    const mergeStatus = await determineMergeability(
      repository,
      currentBranch,
      branch
    ).catch<MergeTreeResult>(e => {
      log.error('Failed determining mergeability', e)
      return { kind: ComputedAction.Clean }
    })

    // The user has selected a different branch since we started or the branch
    // has changed, so don't update the preview with stale data.
    //
    // We don't have to check if the state changed from underneath us if we
    // loaded the status from cache, because that means we never kicked off an
    // async operation.
    if (this.state.selectedBranch?.tip.sha !== branch.tip.sha) {
      return
    }

    // Can't go forward if the merge status is invalid, no need to check commit count
    if (mergeStatus.kind === ComputedAction.Invalid) {
      this.setState({ mergeStatus })
      return
    }

    // Commit count is used in the UI output as well as determining whether the
    // submit button is enabled
    const range = revSymmetricDifference('', branch.name)
    const aheadBehind = await getAheadBehind(repository, range)
    const commitCount = aheadBehind ? aheadBehind.behind : 0

    if (this.state.selectedBranch.tip.sha !== branch.tip.sha) {
      return
    }

    this.setState({ commitCount, mergeStatus })
```

**File:** app/src/lib/git/merge.ts (L30-59)
```typescript
export async function merge(
  repository: Repository,
  branch: string,
  options?: MergeOptions
): Promise<MergeResult> {
  const onTerminalOutputAvailable = options?.onTerminalOutputAvailable
    ? createMultiOperationTerminalOutputCallback(
        options?.onTerminalOutputAvailable
      )
    : undefined

  const args = ['merge']

  if (options?.squash) {
    args.push('--squash')
  }

  if (options?.noVerify) {
    args.push('--no-verify')
  }

  args.push(branch)

  const { exitCode, stdout } = await git(args, repository.path, 'merge', {
    expectedErrors: new Set([GitError.MergeConflicts]),
    interceptHooks: ['pre-merge-commit', 'post-merge', 'commit-msg'],
    onHookProgress: options?.onHookProgress,
    onHookFailure: options?.onHookFailure,
    onTerminalOutputAvailable,
  })
```

**File:** app/src/ui/lib/update-branch.ts (L33-87)
```typescript
export async function updateRebasePreview(
  baseBranch: Branch,
  targetBranch: Branch,
  repository: Repository,
  onUpdate: (rebasePreview: RebasePreview | null) => void
) {
  const computingRebaseForBranch = baseBranch.name

  onUpdate({
    kind: ComputedAction.Loading,
  })

  const commitsBehind = await promiseWithMinimumTimeout(
    () =>
      getCommitsBetweenCommits(
        repository,
        targetBranch.tip.sha,
        baseBranch.tip.sha
      ),
    500
  )

  const commitsAhead = await promiseWithMinimumTimeout(
    () =>
      getCommitsBetweenCommits(
        repository,
        baseBranch.tip.sha,
        targetBranch.tip.sha
      ),
    500
  )

  // if the branch being track has changed since we started this work, abandon
  // any further state updates (this function is re-entrant if the user is
  // using the keyboard to quickly switch branches)
  if (computingRebaseForBranch !== baseBranch.name) {
    onUpdate(null)
    return
  }

  // if we are unable to find any commits to rebase, indicate that we're
  // unable to proceed with the rebase
  if (commitsBehind === null) {
    onUpdate({
      kind: ComputedAction.Invalid,
    })
    return
  }

  onUpdate({
    kind: ComputedAction.Clean,
    commitsAhead: commitsAhead ?? [],
    commitsBehind: commitsBehind,
  })
}
```

**File:** app/src/lib/stores/helpers/background-fetcher.ts (L11-23)
```typescript
const DefaultFetchInterval = 1000 * 60 * 60

/**
 * A minimum fetch interval, to protect against the server accidentally sending
 * us a crazy value.
 */
const MinimumInterval = 1000 * 5 * 60

/**
 * An upper bound to the skew that should be applied to the fetch interval to
 * prevent clients from accidentally syncing up.
 */
const SkewUpperBound = 30 * 1000
```

**File:** app/src/lib/stores/helpers/background-fetcher.ts (L77-116)
```typescript
  /** Perform a fetch and schedule the next one. */
  private async performAndScheduleFetch(
    repository: GitHubRepository
  ): Promise<void> {
    if (this.stopped) {
      return
    }

    const shouldFetch = await this.shouldPerformFetch(this.repository)

    if (this.stopped) {
      return
    }

    if (shouldFetch) {
      try {
        await this.fetch(this.repository)
      } catch (e) {
        const ghRepo = this.repository.gitHubRepository
        const repoName =
          ghRepo !== null ? ghRepo.fullName : this.repository.name

        log.error(`Error performing periodic fetch for '${repoName}'`, e)
      }
    }

    if (this.stopped) {
      return
    }

    const interval = await this.getFetchInterval(repository)
    if (this.stopped) {
      return
    }

    this.timeoutHandle = window.setTimeout(
      () => this.performAndScheduleFetch(repository),
      interval
    )
  }
```
