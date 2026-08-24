## Title
Concurrent, un-synchronized `git fetch` from an attacker-controlled fork remote can race Desktop's push/pull/fetch machinery and corrupt local repository state - (File: `app/src/lib/stores/app-store.ts`)

### Summary
Analogous to M-26 (an operation, `rebalance()`, running before an async cross-chain effect, `xTransfer()`, has actually completed, because the two are not ordered/synchronized), GitHub Desktop has an internal "network op is in progress" invariant — `isPushPullFetchInProgress` — that is meant to serialize all git network operations against a repository so that fetch/pull/push never run concurrently. `AppStore._fetchRefspec` explicitly bypasses this guard, and it is triggered automatically (with no explicit user action) whenever a user merely hovers over a pull request coming from a fork.

### Finding Description
`AppStore._fetch`, `_fetchRemote`, and `performPush`/`performPull` all funnel through `withPushPullFetch`, which checks/sets `state.isPushPullFetchInProgress` to prevent two network git operations from running at the same time on the same working directory: [1](#0-0) , and used by `_fetch`/`performFetch` [2](#0-1)  and by `performPush` [3](#0-2) .

`_fetchRefspec`, however, is documented to intentionally skip this mutex: "it does not opt-in to checks that prevent multiple concurrent network actions" because it must complete while a user is simply *viewing* a PR from a fork: [4](#0-3) .

This method is wired up to the UI hover/quick-view flow with no deliberate user confirmation: hovering a pull request list item for 250ms schedules `pullRequestBeingViewed` state [5](#0-4) , which renders `PullRequestQuickView` [6](#0-5) . That flow ultimately drives a fetch of the PR head refspec from the (possibly attacker-owned/fork) remote via `git-store.fetchRefspec` → `fetchRefspec()` git wrapper, which runs a raw `git fetch <remote> <refspec>` in the repository's working directory with no locking beyond git's own internal ref-lock: [7](#0-6) .

Because `_fetchRefspec` does not check or set `isPushPullFetchInProgress`, it can run at the exact same time as a user-initiated `push`, `pull`, or `fetch` (which do check/set that flag) is executing `performFetch`/`performPush`/`performPull` against the same repository path (see `performFetch`, `performPush` calling `withPushPullFetch`, and `fastForwardBranches` mutating refs mid-operation): [8](#0-7) [9](#0-8) . Two concurrent `git fetch`/`git push` invocations against the same `.git` directory can race on shared git internals (`packed-refs`, loose ref updates, `FETCH_HEAD`, index/objects gc, `git-store` in-memory cache overwritten by whichever finishes last), producing torn or inconsistent local refs/branch state — mirroring the M-26 pattern where one asynchronous git-state-mutating action (`xTransfer`/fund arrival) is not guaranteed to have completed/ordered correctly before a dependent state-reading action (`rebalance`/push, checkout, ahead-behind calculation) executes.

### Impact Explanation
This is not merely a UI glitch: since git fetch/push touch shared on-disk state (refs, packed-refs, object store, `FETCH_HEAD`), running them concurrently without the app-level lock that every other network path in Desktop relies on can corrupt the local repository's git metadata. The user does not need to do anything unusual — merely hovering the pull request list while a background/user-initiated fetch or a push is already inflight is enough, and the pull request (and the head repo/URL it points to) is attacker-controlled data delivered via the GitHub API. The corrupted or inconsistent ref state can subsequently cause Desktop's ahead/behind computation, branch fast-forwarding, or push machinery to act on stale or incorrect repository state (i.e., "rebalance before funds arrive" analog), potentially leading to silently wrong commit history being pushed/tracked or repository state becoming inconsistent between Desktop's in-memory `git-store` cache and the actual on-disk refs.

### Likelihood Explanation
Likelihood is moderate: it requires timing overlap between a hover-triggered quick-view fetch and a manual push/pull/fetch, which is plausible in normal usage (e.g., a user starts a push and then browses the PR list while it's in flight), and the PR/fork data is fully attacker-controlled since any external contributor can open a PR against a public repository. The code comment in `_fetchRefspec` [10](#0-9)  itself acknowledges the concurrency shortcut exists and calls out the need for "some rework in the future to chain these actions," indicating the maintainers were aware of the risk but had not resolved it.

### Recommendation
Route `_fetchRefspec` through the same `isPushPullFetchInProgress` / `withPushPullFetch` serialization used by `_fetch`, `performPush`, and `performPull` (or a separate queue/mutex keyed by repository) so that no two git network operations can mutate a given repository's refs concurrently. If immediate quick-view responsiveness is required, queue the refspec fetch to run after any in-progress push/pull/fetch completes rather than bypassing the guard entirely.

### Proof of Concept
Conceptual sequence (exact reproduction requires local instrumentation, which I could not execute in this environment):
1. Open a repository with an open pull request from a fork in GitHub Desktop.
2. Start a `git push` (or long-running `git pull`) on the repository so `isPushPullFetchInProgress` is `true` (`performPush` → `withPushPullFetch`).
3. While the push/pull is in flight, hover over the fork PR in the pull request list for >250ms; this calls `onMouseEnterPullRequestListItem` → renders `PullRequestQuickView` → dispatches a call that reaches `AppStore._fetchRefspec`, which does **not** check `isPushPullFetchInProgress` and immediately runs `git fetch <fork-remote> <refspec>` in the same working directory concurrently with the ongoing push's `git push`/`git fetch` sequence.
4. Both git processes operate on the same `.git` directory (refs, `FETCH_HEAD`, packed-refs) simultaneously, which can result in inconsistent ref state, lost updates, or Desktop's cached `git-store` state diverging from the actual on-disk repository — the local analog of "acting on state before the concurrent operation is safely complete." [4](#0-3)

### Citations

**File:** app/src/lib/stores/app-store.ts (L5191-5325)
```typescript
  private async performPush(
    repository: Repository,
    options?: PushOptions
  ): Promise<void> {
    const state = this.repositoryStateCache.get(repository)
    const { remote } = state
    if (remote === null) {
      this._showPopup({
        type: PopupType.PublishRepository,
        repository,
      })

      return
    }

    return this.withPushPullFetch(repository, async () => {
      const branch = this.getBranchToPush(repository, options)

      if (branch === undefined) {
        return
      }

      const remoteName = branch.upstreamRemoteName || remote.name

      const pushTitle = `Pushing to ${remoteName}`

      // Emit an initial progress even before our push begins
      // since we're doing some work to get remotes up front.
      this.updatePushPullFetchProgress(repository, {
        kind: 'push',
        title: pushTitle,
        value: 0,
        remote: remoteName,
        branch: branch.name,
      })

      // Let's say that a push takes roughly twice as long as a fetch,
      // this is of course highly inaccurate.
      let pushWeight = 2.5
      let fetchWeight = 1

      // Let's leave 10% at the end for refreshing
      const refreshWeight = 0.1

      // Scale pull and fetch weights to be between 0 and 0.9.
      const scale = (1 / (pushWeight + fetchWeight)) * (1 - refreshWeight)

      pushWeight *= scale
      fetchWeight *= scale

      const retryAction: RetryAction = {
        type: RetryActionType.Push,
        repository,
      }

      // This is most likely not necessary and is only here out of
      // an abundance of caution. We're introducing support for
      // automatically configuring Git proxies based on system
      // proxy settings and therefore need to pass along the remote
      // url to functions such as push, pull, fetch etc.
      //
      // Prior to this we relied primarily on the `branch.remote`
      // property and used the `remote.name` as a fallback in case the
      // branch object didn't have a remote name (i.e. if it's not
      // published yet).
      //
      // The remote.name is derived from the current tip first and falls
      // back to using the defaultRemote if the current tip isn't valid
      // or if the current branch isn't published. There's however no
      // guarantee that they'll be refreshed at the exact same time so
      // there's a theoretical possibility that `branch.remote` and
      // `remote.name` could be out of sync. I have no reason to suspect
      // that's the case and if it is then we already have problems as
      // the `fetchRemotes` call after the push already relies on the
      // `remote` and not the `branch.remote`. All that said this is
      // a critical path in the app and somehow breaking pushing would
      // be near unforgivable so I'm introducing this `safeRemote`
      // temporarily to ensure that there's no risk of us using an
      // out of sync remote name while still providing envForRemoteOperation
      // with an url to use when resolving proxies.
      //
      // I'm also adding a non fatal exception if this ever happens
      // so that we can confidently remove this safeguard in a future
      // release.
      const safeRemote: IRemote = { name: remoteName, url: remote.url }

      if (safeRemote.name !== remote.name) {
        sendNonFatalException(
          'remoteNameMismatch',
          new Error('The current remote name differs from the branch remote')
        )
      }

      const gitStore = this.gitStoreCache.get(repository)
      await gitStore.performFailableOperation(
        async () => {
          let aborted = false
          await pushRepo(
            repository,
            safeRemote,
            branch.name,
            branch.upstreamWithoutRemote,
            gitStore.tagsToPush,
            {
              onHookFailure: this.onHookFailure(() => (aborted = true)),
              ...options,
            },
            progress => {
              this.updatePushPullFetchProgress(repository, {
                ...progress,
                title: pushTitle,
                value: pushWeight * progress.value,
              })
            }
          ).catch(err => (aborted ? undefined : Promise.reject(err)))

          if (aborted) {
            return
          }

          gitStore.clearTagsToPush()

          await gitStore.fetchRemotes([safeRemote], false, fetchProgress => {
            this.updatePushPullFetchProgress(repository, {
              ...fetchProgress,
              value: pushWeight + fetchProgress.value * fetchWeight,
            })
          })

          const refreshTitle = __DARWIN__
            ? 'Refreshing Repository'
            : 'Refreshing repository'
          const refreshStartProgress = pushWeight + fetchWeight

          this.updatePushPullFetchProgress(repository, {
```

**File:** app/src/lib/stores/app-store.ts (L5427-5450)
```typescript
  private async withPushPullFetch(
    repository: Repository,
    fn: () => Promise<void>
  ): Promise<void> {
    const state = this.repositoryStateCache.get(repository)
    // Don't allow concurrent network operations.
    if (state.isPushPullFetchInProgress) {
      return
    }

    this.repositoryStateCache.update(repository, () => ({
      isPushPullFetchInProgress: true,
    }))
    this.emitUpdate()

    try {
      await fn()
    } finally {
      this.repositoryStateCache.update(repository, () => ({
        isPushPullFetchInProgress: false,
      }))
      this.emitUpdate()
    }
  }
```

**File:** app/src/lib/stores/app-store.ts (L5866-5885)
```typescript
  /**
   * Fetch a specific refspec for the repository.
   *
   * As this action is required to complete when viewing a Pull Request from
   * a fork, it does not opt-in to checks that prevent multiple concurrent
   * network actions. This might require some rework in the future to chain
   * these actions.
   *
   */
  public async _fetchRefspec(
    repository: Repository,
    refspec: string
  ): Promise<void> {
    return this.withRefreshedGitHubRepository(repository, async repository => {
      const gitStore = this.gitStoreCache.get(repository)
      await gitStore.fetchRefspec(refspec)

      return this._refreshRepository(repository)
    })
  }
```

**File:** app/src/lib/stores/app-store.ts (L5924-5987)
```typescript
  private async performFetch(
    repository: Repository,
    fetchType: FetchType,
    remotes?: IRemote[]
  ): Promise<void> {
    await this.withPushPullFetch(repository, async () => {
      const gitStore = this.gitStoreCache.get(repository)

      try {
        const fetchWeight = 0.9
        const refreshWeight = 0.1
        const isBackgroundTask = fetchType === FetchType.BackgroundTask

        const progressCallback = (progress: IFetchProgress) => {
          this.updatePushPullFetchProgress(repository, {
            ...progress,
            value: progress.value * fetchWeight,
          })
        }

        if (remotes === undefined) {
          await gitStore.fetch(isBackgroundTask, progressCallback)
        } else {
          await gitStore.fetchRemotes(
            remotes,
            isBackgroundTask,
            progressCallback
          )
        }

        const refreshTitle = __DARWIN__
          ? 'Refreshing Repository'
          : 'Refreshing repository'

        this.updatePushPullFetchProgress(repository, {
          kind: 'generic',
          title: refreshTitle,
          description: 'Fast-forwarding branches',
          value: fetchWeight,
        })

        await this.fastForwardBranches(repository)

        this.updatePushPullFetchProgress(repository, {
          kind: 'generic',
          title: refreshTitle,
          value: fetchWeight + refreshWeight * 0.5,
        })

        // manually refresh branch protections after the push, to ensure
        // any new branch will immediately report as protected
        await this.refreshBranchProtectionState(repository)

        await this._refreshRepository(repository)
      } finally {
        this.updatePushPullFetchProgress(repository, null)

        if (fetchType === FetchType.UserInitiatedTask) {
          if (repository.gitHubRepository != null) {
            this._refreshIssues(repository.gitHubRepository)
          }
        }
      }
    })
```

**File:** app/src/ui/branches/branches-container.tsx (L138-159)
```typescript
  private renderPullRequestQuickView = (): JSX.Element | null => {
    if (
      !enablePullRequestQuickView() ||
      this.state.pullRequestBeingViewed === null
    ) {
      return null
    }

    const { pr, prListItemTop } = this.state.pullRequestBeingViewed

    return (
      <PullRequestQuickView
        dispatcher={this.props.dispatcher}
        emoji={this.props.emoji}
        pullRequest={pr}
        pullRequestItemTop={prListItemTop}
        onMouseEnter={this.onMouseEnterPullRequestQuickView}
        onMouseLeave={this.onMouseLeavePullRequestQuickView}
        underlineLinks={this.props.underlineLinks}
      />
    )
  }
```

**File:** app/src/ui/branches/branches-container.tsx (L404-414)
```typescript
  private onMouseEnterPullRequestListItem = (
    pr: PullRequest,
    prListItemTop: number
  ) => {
    this.clearPullRequestQuickViewTimer()
    this.setState({ pullRequestBeingViewed: null })
    this.pullRequestQuickViewTimerId = window.setTimeout(
      () => this.setState({ pullRequestBeingViewed: { pr, prListItemTop } }),
      250
    )
  }
```

**File:** app/src/lib/git/fetch.ts (L91-101)
```typescript
/** Fetch a given refspec from the given remote. */
export async function fetchRefspec(
  repository: Repository,
  remote: IRemote,
  refspec: string
): Promise<void> {
  await git(['fetch', remote.name, refspec], repository.path, 'fetchRefspec', {
    successExitCodes: new Set([0, 128]),
    env: await envForRemoteOperation(remote.url),
  })
}
```
