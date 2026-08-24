### Title
TOCTOU on stale `tip` causes `_abortSquashMerge` to `reset --hard` to a sha invalidated by a concurrent background fetch fast-forward, silently discarding newly-fetched/merged commits - ([File: app/src/lib/stores/app-store.ts])

### Summary
`_abortSquashMerge` mirrors the GRANDPA bug's structure exactly: it reads a piece of shared state (`branchesState.tip`) from the cache, then `await`s a separate git-mutating operation (`_finishConflictedMerge`, which runs `git commit`), and afterwards uses the *pre-await* `tip` value to run `reset(repository, GitResetMode.Hard, tip.branch.tip.sha)` — without re-reading current branch state or re-verifying that `tip.branch.tip.sha` still reflects the parent of what's now on disk. In the intervening await, a completely independent code path (`fastForwardBranches`, invoked from the periodic `BackgroundFetcher` / `performFetch`) can update the very same local branch ref via `git fetch . --stdin` refspec fast-forwards driven by attacker-controlled remote ref advertisements. There is no lock shared between `_abortSquashMerge` and the fetch/fast-forward path (`withPushPullFetch` is not used here), so the two can interleave freely.

### Finding Description [1](#0-0) 

The sequence is:
1. `branchesState.tip` is captured synchronously at the very top of `_abortSquashMerge`.
2. `await this._finishConflictedMerge(...)` runs, which internally calls `gitStore.performFailableOperation(() => createMergeCommit(...))` — an async git process invocation that yields the event loop for a nontrivial amount of time.
3. After the await resolves, the code reuses the *stale* `tip` captured in step 1 (comment explicitly says "we have not reloaded the status, this tip is the tip before the squash commit above") and issues `reset(repository, GitResetMode.Hard, tip.branch.tip.sha)`.

During the window opened by step 2's await, the `BackgroundFetcher` (started via `startBackgroundFetching`, see [2](#0-1)  and the fetch loop in [3](#0-2) ) can run `performFetch` → `fastForwardBranches`, which fast-forwards local branch refs in bulk via `git fetch . --stdin` using refspecs derived from the just-fetched (attacker-influenced) remote tracking refs: [4](#0-3) 

If the branch currently involved in the squash-merge abort is fast-forwarded during this window (e.g., because it tracks an upstream the attacker controls, or because another local branch update happens to coincide), the `reset --hard` uses a `sha` value that is no longer the actual current tip's parent — it is now *behind* the just-fast-forwarded branch. The `git reset --hard <stale-sha>` will forcibly move `HEAD` and the branch ref back to that stale value, silently discarding the newly fast-forwarded commits from the branch and rewriting the working directory to match the older state, with no error, warning, or re-validation shown to the user. This is the direct analog of the GRANDPA invariant violation: a value read under a "lock" that is released, then reused later to perform a state-mutating "finalize"-style action after a concurrent finalizer has already advanced the same piece of state — except here the invariant broken is "the branch has not moved since we captured `tip`," and there is no check equivalent to `enacts_change` guard before the reset is issued.

### Impact Explanation
This falls squarely under "silent corruption of what the user commits or pushes": commits that were legitimately fast-forwarded from a remote (potentially including commits the user was about to build on, review, or push) can be silently wiped from the local branch by an unrelated abort-squash-merge action, with no confirmation dialog and no diff shown before the hard reset. Because `reset --hard` also rewrites the working directory, any uncommitted work colliding with the discarded state is lost as well. There's no code-execution or sandbox-escape vector here, but it satisfies the "corruption of what gets committed/pushed" bar because subsequent commits/pushes will be based on the silently-rolled-back tree without the user's knowledge.

### Likelihood Explanation
Likelihood is moderate-to-low: it requires the user to be in an active squash-merge-with-conflicts flow and aborting it (`_abortSquashMerge`) at the same moment the periodic background fetcher (which runs automatically for any repo with a GitHub remote once selected, per `startBackgroundFetching`) performs `fastForwardBranches` on the same branch. An attacker who controls the remote (fork, compromised remote, or MITM'd proxy) cannot force exact timing but can increase the probability by serving frequent ref updates and by the victim leaving Desktop open with the repository selected (background fetch runs at least every `MinimumInterval` — 5 minutes — and is retried on every app focus/pull). No local access, admin rights, or leaked credentials are required — only a repository whose remote the attacker influences and a user who happens to abort a squash-merge conflict during a periodic background sync window.

### Recommendation
`_abortSquashMerge` should not reuse a `tip` value captured before the intervening `await`. Instead, after `_finishConflictedMerge` resolves, it should re-fetch the current branch state (e.g., via `getStatus`/`gitStore` refresh) and compute the reset target from the freshly-read state, or use `git rev-parse HEAD~1` / an explicit pre-recorded commit sha object captured immediately before the commit is made (not derived from potentially-stale cached `branchesState`), and abort with an error if the branch tip observed at reset time does not match what was expected immediately prior to the squash commit. More generally, any git-mutating operation that spans an `await` boundary should either hold the same `withPushPullFetch`-style mutual-exclusion lock used by fetch/pull/push, or re-validate the state it intends to act upon right before issuing the mutating command.

### Proof of Concept
1. Clone a repository whose remote is attacker-controlled (or a fork with a permissive collaborator), and check out a branch tracking that remote.
2. Leave GitHub Desktop open and selected on this repository so `BackgroundFetcher` is active (`startBackgroundFetching`).
3. Trigger a squash-merge that results in conflicts, entering the `_abortSquashMerge` path deliberately (e.g., via merge conflicts dialog → Abort).
4. While `_finishConflictedMerge`'s `createMergeCommit` git process is running (the await window in `_abortSquashMerge`), have the attacker-controlled remote respond to the concurrently-scheduled background fetch with new commits fast-forwardable onto the local branch, triggering `fastForwardBranches` to advance the branch ref.
5. Once `_abortSquashMerge` resumes and issues `reset(repository, GitResetMode.Hard, tip.branch.tip.sha)` with the stale `tip.branch.tip.sha`, observe that the branch and working directory are hard-reset behind the commits that were just fast-forwarded in step 4, with no warning to the user, and those commits disappear from the local branch history until another fetch/refresh re-syncs them (and any local edits made in the interim are lost).

Note: precise, deterministic timing of the race could not be fully validated statically (it depends on JS event-loop scheduling and external git process durations); this assessment is based on directly reading the source of `_abortSquashMerge`, `fastForwardBranches`, and `BackgroundFetcher` and confirming the absence of any shared lock or re-validation between them.

### Citations

**File:** app/src/lib/stores/app-store.ts (L2390-2414)
```typescript
  private startBackgroundFetching(
    repository: Repository,
    withInitialSkew: boolean
  ) {
    if (this.currentBackgroundFetcher) {
      fatalError(
        `We should only have on background fetcher active at once, but we're trying to start background fetching on ${repository.name} while another background fetcher is still active!`
      )
    }

    if (!repository.gitHubRepository) {
      return
    }

    // Todo: add logic to background checker to check the API before fetching
    // similar to what's being done in `refreshAllIndicators`
    const fetcher = new BackgroundFetcher(
      repository,
      this.accountsStore,
      r => this._fetch(r, FetchType.BackgroundTask),
      r => this.shouldBackgroundFetch(r, null)
    )
    fetcher.start(withInitialSkew)
    this.currentBackgroundFetcher = fetcher
  }
```

**File:** app/src/lib/stores/app-store.ts (L7494-7530)
```typescript
  /** This shouldn't be called directly. See `Dispatcher`. */
  public async _abortSquashMerge(repository: Repository): Promise<void> {
    const gitStore = this.gitStoreCache.get(repository)
    const {
      branchesState,
      changesState: { workingDirectory },
    } = this.repositoryStateCache.get(repository)

    const commitResult = await this._finishConflictedMerge(
      repository,
      workingDirectory,
      new Map<string, ManualConflictResolution>()
    )

    // By committing, we clear out the SQUASH_MSG (and anything else git would
    // choose to store for the --squash merge operation)
    if (commitResult === undefined) {
      log.error(
        `[_abortSquashMerge] - Could not abort squash merge - commiting squash msg failed`
      )
      return
    }

    // Since we have not reloaded the status, this tip is the tip before the
    // squash commit above.
    const { tip } = branchesState
    if (tip.kind !== TipState.Valid) {
      log.error(
        `[_abortSquashMerge] - Could not abort squash merge - tip was invalid`
      )
      return
    }

    await gitStore.performFailableOperation(() =>
      reset(repository, GitResetMode.Hard, tip.branch.tip.sha)
    )
  }
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

**File:** app/src/lib/git/fetch.ts (L103-140)
```typescript
export async function fastForwardBranches(
  repository: Repository,
  branches: ReadonlyArray<ITrackingBranch>
): Promise<void> {
  if (branches.length === 0) {
    return
  }

  const refPairs = branches.map(branch => `${branch.upstreamRef}:${branch.ref}`)

  await git(
    [
      'fetch',
      '.',
      // Make sure we don't try to update branches that can't be fast-forwarded
      // even if the user disabled this via the git config option
      // `fetch.showForcedUpdates`
      '--show-forced-updates',
      // Prevent `git fetch` from touching the `FETCH_HEAD`
      '--no-write-fetch-head',
      // Take branch refs from stdin to circumvent shell max line length
      // limitations (mainly on Windows)
      '--stdin',
    ],
    repository.path,
    'fastForwardBranches',
    {
      // Fetch exits with an exit code of 1 if one or more refs failed to update
      // which is what we expect will happen
      successExitCodes: new Set([0, 1]),
      env: {
        // This will make sure the reflog entries are correct after
        // fast-forwarding the branches.
        GIT_REFLOG_ACTION: 'pull',
      },
      stdin: refPairs.join('\n'),
    }
  )
```
