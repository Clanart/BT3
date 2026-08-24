## Analysis

The Sherlock report's broken invariant is: **two async operations mutate related/shared state without a mutual-exclusion or sequencing guarantee, so an out-of-order or overlapping completion leaves the system in an inconsistent state that requires manual intervention to fix.** In the LayerZero case that's the Ledger balance vs. the Solana vault; the closest verifiable Desktop analog is the explicit, intentionally-unguarded network/git operation `_fetchRefspec`, which is documented in the code itself as bypassing the mutual-exclusion guard (`withPushPullFetch`) that every other network/git-mutating action (`_pull`, `_fetch`, `_push`) uses.

### Title
Unsynchronized `git fetch` via `_fetchRefspec` can race with push/pull/fetch and corrupt local ref/working state - (File: app/src/lib/stores/app-store.ts)

### Summary
`AppStore._fetch`, `_pull`, and `performPush`/push-related flows are all wrapped in `withPushPullFetch`, a flag-based mutex (`state.isPushPullFetchInProgress`) that prevents any two of these repository-mutating git network operations from running concurrently on the same repository. [1](#0-0) 
`_fetchRefspec`, used to fetch pull-request refs from forks, explicitly opts out of this guard, with a comment acknowledging the gap: [2](#0-1) 

### Finding Description
`_fetchRefspec` calls `gitStore.fetchRefspec(refspec)` directly, which shells out to `git fetch <remote> <refspec>` in the repository's working directory without acquiring `isPushPullFetchInProgress`. [3](#0-2) 
Meanwhile, `_pull`/`performPull` and `_fetch`/`performFetch` run `git pull`/`git fetch`, then `fastForwardBranches` (which itself invokes `git fetch . --stdin` to fast-forward local branch refs) and `_refreshRepository`, all inside the same critical section guarded by `withPushPullFetch`. [4](#0-3) [5](#0-4) 

Because `_fetchRefspec` is triggered from viewing a pull request from a fork — an action a user can perform by simply clicking a link (e.g. an "Open in Desktop" PR link, or navigating the PR list) sourced from GitHub API/PR data that names the fork remote and refspec — an attacker who controls a fork/PR (unprivileged, no local access needed) can cause the victim's Desktop client to run `git fetch <attacker-fork> <refspec>` at an arbitrary moment chosen by the attacker (e.g., by making the PR/fork trigger a slow, large, or ref-heavy fetch), overlapping with the victim's own concurrent pull/push/fetch of `origin`. Two `git fetch`/`git pull` processes running concurrently against the same repository can interleave writes to `.git` (packed-refs, loose refs, FETCH_HEAD, index-related state during fast-forward), a scenario for which git itself is not fully safe without external locking — Desktop's own error handling models exactly this class of corruption (`DugiteError.LockFileAlreadyExists`, "A lock file already exists in the repository, which blocks this operation from completing"). [6](#0-5) 
The `withPushPullFetch` mutex exists specifically to prevent this class of race for every other flow; `_fetchRefspec` is the sole documented exception.

This mirrors the Sherlock bug's structure precisely: a required ordering/serialization guarantee ("ordered execution option" / "no concurrent network operations") is enforced everywhere except one code path, and the missing enforcement is acknowledged in a comment rather than fixed, leaving state (in this case, local refs/branch tracking state and the in-progress push/pull's fast-forward result) able to end up corrupted or inconsistent, requiring the user to manually resolve stale/locked git state.

### Impact Explanation
If a fetch of a fork PR overlaps with the user's own pull/push, the resulting local repository state (refs, FETCH_HEAD, packed-refs) can become corrupted or inconsistent with what the UI reports (e.g., branch tracking info, ahead/behind counts, or working directory diff computed by `_refreshRepository`), potentially causing the user to build on, commit to, or push from an unexpected base — i.e., "silent corruption of what the user commits or pushes," which is an explicitly in-scope impact category. In the worst case a lock file is left behind and subsequent git operations fail until the user manually deletes it (`ConfigLockFileExists`/`LockFileAlreadyExists` UI already exists to handle this class of failure), matching the original report's "may require manual intervention by an administrator/user to resolve."

### Likelihood Explanation
Requires only that a victim opens/views a pull request from a fork (a normal, expected user action, not "unnatural") while Desktop happens to also be doing a background or user-initiated fetch/pull/push on the same repository — background fetching runs periodically and automatically via `BackgroundFetcher`, increasing the overlap window. [7](#0-6) 
The attacker only needs to control a fork (trivial, unprivileged) and induce the victim to view the PR — no credentials, no local access, no malware.

### Recommendation
Route `_fetchRefspec` through the same `withPushPullFetch` mutex (or a per-repository git-operation queue) used by `_pull`/`_fetch`/push flows so that fork-refspec fetches cannot run concurrently with other repository-mutating git network operations. If blocking PR-from-fork loading is unacceptable, queue the refspec fetch to run after any in-flight push/pull/fetch completes rather than bypassing the guard entirely.

### Proof of Concept
1. Attacker opens a PR from a fork against a repository the victim has cloned in Desktop.
2. Victim opens the PR in Desktop (or Desktop auto-loads PR info), triggering `Dispatcher` → `AppStore._fetchRefspec(repository, refspec)`, which runs unguarded. [8](#0-7) 
3. Simultaneously (or via the periodic `BackgroundFetcher`, or the victim manually pressing "Fetch origin"/"Pull"), Desktop runs `performFetch`/`performPull`, which is guarded by `withPushPullFetch` but does not know about or wait for the `_fetchRefspec` git process already running. [9](#0-8) 
4. Two `git fetch` invocations execute concurrently against the same `.git` directory, one of which can leave lock files or partially-written ref state, corrupting the local repository state that Desktop subsequently reports as ahead/behind/branch status and that the user later commits/pushes against.

*Note: the exact severity (whether concurrent `git fetch` invocations against the same repo directory reliably corrupt refs versus merely fail with a lock error) depends on git/dugite's internal locking behavior, which was not verifiable from the indexed source alone — this would need to be confirmed experimentally in a live Desktop session.*

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L5452-5612)
```typescript
  public async _pull(repository: Repository): Promise<void> {
    return this.withRefreshedGitHubRepository(repository, repository => {
      return this.performPull(repository)
    })
  }

  /** This shouldn't be called directly. See `Dispatcher`. */
  private async performPull(repository: Repository): Promise<void> {
    return this.withPushPullFetch(repository, async () => {
      const gitStore = this.gitStoreCache.get(repository)
      const remote = gitStore.currentRemote

      if (!remote) {
        throw new Error('The repository has no remotes.')
      }

      const state = this.repositoryStateCache.get(repository)
      const tip = state.branchesState.tip

      if (tip.kind === TipState.Unborn) {
        throw new Error('The current branch is unborn.')
      }

      if (tip.kind === TipState.Detached) {
        throw new Error('The current repository is in a detached HEAD state.')
      }

      if (tip.kind === TipState.Valid) {
        let mergeBase: string | null = null
        let gitContext: GitErrorContext | undefined = undefined

        if (tip.branch.upstream !== null) {
          mergeBase = await getMergeBase(
            repository,
            tip.branch.name,
            tip.branch.upstream
          )

          gitContext = {
            kind: 'pull',
            theirBranch: tip.branch.upstream,
            currentBranch: tip.branch.name,
          }
        }

        const title = `Pulling ${remote.name}`
        const kind = 'pull'
        this.updatePushPullFetchProgress(repository, {
          kind,
          title,
          value: 0,
          remote: remote.name,
        })

        try {
          // Let's say that a pull takes twice as long as a fetch,
          // this is of course highly inaccurate.
          let pullWeight = 2
          let fetchWeight = 1

          // Let's leave 10% at the end for refreshing
          const refreshWeight = 0.1

          // Scale pull and fetch weights to be between 0 and 0.9.
          const scale = (1 / (pullWeight + fetchWeight)) * (1 - refreshWeight)

          pullWeight *= scale
          fetchWeight *= scale

          const retryAction: RetryAction = {
            type: RetryActionType.Pull,
            repository,
          }

          if (gitStore.pullWithRebase) {
            this.statsStore.increment('pullWithRebaseCount')
          } else {
            this.statsStore.increment('pullWithDefaultSettingCount')
          }

          let aborted = false
          const pullSucceeded = await gitStore
            .performFailableOperation(
              async () => {
                await pullRepo(repository, remote, {
                  progressCallback: progress => {
                    this.updatePushPullFetchProgress(repository, {
                      ...progress,
                      value: progress.value * pullWeight,
                    })
                  },
                  onHookFailure: (hookName, terminalOutput) =>
                    new Promise(resolve => {
                      this._showPopup({
                        type: PopupType.HookFailed,
                        hookName,
                        terminalOutput,
                        resolve: resolution => {
                          if (resolution === 'abort') {
                            aborted = true
                          }
                          resolve(resolution)
                        },
                      })
                    }),
                })
                return true
              },
              { gitContext, retryAction }
            )
            .catch(err => (aborted ? false : Promise.reject(err)))

          // If the pull failed we shouldn't try to update the remote HEAD
          // because there's a decent chance that it failed either because we
          // didn't have the correct credentials (which we won't this time
          // either) or because there's a network error which likely will
          // persist for the next operation as well.
          if (pullSucceeded) {
            // Updating the local HEAD symref isn't critical so we don't want
            // to show an error message to the user and have them retry the
            // entire pull operation if it fails.
            await updateRemoteHEAD(repository, remote, false).catch(e =>
              log.error('Failed updating remote HEAD', e)
            )
          }

          const refreshStartProgress = pullWeight + fetchWeight
          const refreshTitle = __DARWIN__
            ? 'Refreshing Repository'
            : 'Refreshing repository'

          this.updatePushPullFetchProgress(repository, {
            kind: 'generic',
            title: refreshTitle,
            description: 'Fast-forwarding branches',
            value: refreshStartProgress,
          })

          await this.fastForwardBranches(repository)

          this.updatePushPullFetchProgress(repository, {
            kind: 'generic',
            title: refreshTitle,
            value: refreshStartProgress + refreshWeight * 0.5,
          })

          if (mergeBase) {
            await gitStore.reconcileHistory(mergeBase)
          }

          // manually refresh branch protections after the push, to ensure
          // any new branch will immediately report as protected
          await this.refreshBranchProtectionState(repository)

          await this._refreshRepository(repository)
        } finally {
          this.updatePushPullFetchProgress(repository, null)
        }
      }
    })
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

**File:** app/src/lib/git/core.ts (L553-554)
```typescript
    case DugiteError.LockFileAlreadyExists:
      return 'A lock file already exists in the repository, which blocks this operation from completing.'
```

**File:** app/src/lib/stores/helpers/background-fetcher.ts (L26-61)
```typescript
export class BackgroundFetcher {
  /** The handle for our setTimeout invocation. */
  private timeoutHandle: number | null = null

  /** Flag to indicate whether `stop` has been called. */
  private stopped = false

  public constructor(
    private readonly repository: Repository,
    private readonly accountsStore: AccountsStore,
    private readonly fetch: (repository: Repository) => Promise<void>,
    private readonly shouldPerformFetch: (
      repository: Repository
    ) => Promise<boolean>
  ) {}

  /** Start background fetching. */
  public start(withInitialSkew: boolean) {
    if (this.stopped) {
      fatalError('Cannot start a background fetcher that has been stopped.')
    }

    const gitHubRepository = this.repository.gitHubRepository
    if (!gitHubRepository) {
      return
    }

    if (withInitialSkew) {
      this.timeoutHandle = window.setTimeout(
        () => this.performAndScheduleFetch(gitHubRepository),
        skewInterval()
      )
    } else {
      this.performAndScheduleFetch(gitHubRepository)
    }
  }
```
