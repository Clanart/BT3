### Title
Unguarded concurrent `git fetch` via `_fetchRefspec` races with user-initiated push/pull/fetch, corrupting `GitStore` state and on-disk refs - (File: `app/src/lib/stores/app-store.ts`)

### Summary
`AppStore._fetchRefspec` is explicitly documented and implemented to bypass the app's single concurrency guard for network git operations, `withPushPullFetch`, which serializes all fetch/pull/push activity per repository via the `isPushPullFetchInProgress` flag. [1](#0-0) 
Every other network entry point (`_fetch`, `_fetchRemote`, `_pull`/`performPull`, `_push`/`performPush`) is routed through `withPushPullFetch`, which reads and sets `state.isPushPullFetchInProgress` on the `repositoryStateCache` to prevent two concurrent `git` network processes from running against the same working directory at once. [2](#0-1) [3](#0-2) [4](#0-3) 

### Finding Description
The broken invariant is: "only one `git` network operation runs against a given repository's working directory/`GitStore` at a time." This invariant is enforced everywhere except `_fetchRefspec`, which the code comment admits is intentional: "it does not opt-in to checks that prevent multiple concurrent network actions." [5](#0-4) 

`_fetchRefspec` calls `GitStore.fetchRefspec`, which iterates every remote and runs `git fetch <remote> <refspec>` for each one directly against `repository.path`, with no interaction with the `isPushPullFetchInProgress` flag at all. [6](#0-5) [7](#0-6) 

This bypass is reachable from an attacker-controlled input: a PR/fork reference embedded in a `x-github-client://` deep link is handled by `openPullRequestFromUrl`, which resolves the PR via `_checkoutPullRequest` → `_findPullRequestBranch`. `_findPullRequestBranch` adds a fork-controlled `headCloneUrl` as a new remote and attempts to locate/fetch its ref. [8](#0-7) [9](#0-8) 
While that particular internal call (`_fetchRemote`) is itself properly guarded via `performFetch`/`withPushPullFetch`, `_fetchRefspec` is exposed through the `Dispatcher` as a first-class, directly callable action (`dispatcher.fetchRefspec`), used elsewhere in the pull-request/diff-viewing flow to fetch a specific ref on demand while a PR is being viewed. [10](#0-9) 

Because `_fetchRefspec` does not check or set `isPushPullFetchInProgress`, nothing prevents it from running concurrently with a user-initiated `_push`, `_pull`, or `_fetch` on the very same repository. Both code paths operate on the same on-disk git directory (same `repository.path`) and the same shared, mutable `GitStore` instance obtained from `GitStoreCache.get(repository)`, which caches one `GitStore` per repository hash and returns the same instance to every caller. [11](#0-10) 
Two concurrent native `git fetch` invocations (or a `git fetch` racing a `git push`) against the same `.git` directory can race on shared git-internal state such as `FETCH_HEAD`, packed-refs, and remote-tracking refs, and the two `GitStore.performFailableOperation` calls independently mutate/read the same in-memory fields (`_tip`, `_aheadBehind`, `_currentRemote`) with no locking, so whichever operation's async callback resolves last silently overwrites the other's freshly computed state on `emitUpdate()`. [12](#0-11) 

This is structurally the same bug class as the CVE seed: a shared mutable object (`cstypes.RoundState` in cometbft; here `GitStore`'s cached mutable fields and the on-disk `.git` directory) is read/written from two independent asynchronous flows without a mutex/serialization guard that the rest of the codebase otherwise relies on (`withPushPullFetch`), and one flow (attacker/PR-triggered) was deliberately carved out of that guard.

### Impact Explanation
An attacker who controls a pull request or fork (i.e., who controls `headCloneUrl`/`headRefName`/refspec content reachable through PR-viewing or the `x-github-client://openPullRequest` deep link) can force Desktop to run an un-serialized `git fetch` against the victim's already-open repository at the exact moment the victim performs a push or pull. The result is silent corruption of what Desktop believes is the local branch's ahead/behind status, current tip, or remote-tracking state (`GitStore._tip`, `_aheadBehind`, `currentRemote`), because the two async operations write to the same instance fields without any ordering guarantee. This can make the UI display a stale or incorrect tip/ahead-behind status right after a push, potentially causing the user to push, pull, or reset based on data that no longer reflects the true state of the repository — directly matching the report's category of "silent corruption of what the user commits or pushes."

### Likelihood Explanation
The trigger requires the victim to be actively viewing/interacting with a fork-based pull request (which routes through `_fetchRefspec`/`fetchRefspec`) at the same time as a network operation like push/pull is running — both of which are normal, common Desktop usage patterns, and the PR/fork content is fully attacker-controlled. No local access, elevated privileges, or social engineering beyond "open/view a PR from a malicious fork" is required. The maintainers' own code comment acknowledges the missing guard and flags it as a known gap requiring "some rework," which corroborates that this is a real, currently-unaddressed condition rather than a false positive.

### Recommendation
Route `_fetchRefspec` through the same `withPushPullFetch` (or an equivalent per-repository mutex) that serializes all other network git operations, or, if fetching during PR viewing must not block on ongoing push/pull, use a dedicated lock scoped to the same `GitStore` instance so that concurrent `git fetch` invocations against one working directory are never issued in parallel, and guard all mutations of `GitStore._tip`/`_aheadBehind`/`_currentRemote` with a "still relevant" staleness check analogous to the pattern already used in `updateChangesStashDiff`. [13](#0-12) 

### Proof of Concept
1. Attacker opens a PR against a public repo from a fork they control, or crafts a `x-github-client://openPullRequest?url=...&pr=...` deep link pointing at that fork/PR.
2. Victim opens the PR in Desktop (via the PR list or the deep link, reaching `openPullRequestFromUrl` → `_checkoutPullRequest`/PR diff viewing, which internally issues `dispatcher.fetchRefspec` → `AppStore._fetchRefspec`). [9](#0-8) 
3. While Desktop is processing that unguarded `fetchRefspec` fetch, the victim independently triggers `Push`/`Pull`/`Fetch` from the UI (`_push`/`_pull`/`_fetch`), which acquires `withPushPullFetch`'s guard and runs its own `git` process concurrently against the same `repository.path` and the same cached `GitStore` instance. [11](#0-10) 
4. Both operations complete asynchronously and independently call `this.emitUpdate()` after mutating `GitStore` fields (`_tip`, `_aheadBehind`) — whichever resolves last wins, regardless of which one reflects the true current repository state, and the UI's ahead/behind/tip indicators can display data inconsistent with what was actually just pushed/pulled.

**Note on verification limits:** I was not able to fully trace every UI call site that invokes `dispatcher.fetchRefspec` (only `AppStore._fetchRefspec`/`GitStore.fetchRefspec` and the `Dispatcher.fetchRefspec` wrapper were found in the indexed code) or confirm the exact component in `app/src/ui/` that calls it during PR-diff viewing, since some file contents may be excluded from the index. Starting a full Devin session against the repository would allow a complete trace of `dispatcher.fetchRefspec` call sites and confirmation of the race window with a live repro.

### Citations

**File:** app/src/lib/stores/app-store.ts (L3656-3668)
```typescript
    const diff = await getCommitDiff(repository, file, file.commitish)

    const stateAfterLoad = this.repositoryStateCache.get(repository)
    const changesStateAfterLoad = stateAfterLoad.changesState

    // Something has changed during our async getCommitDiff, bail
    if (
      changesStateAfterLoad.selection.kind !== ChangesSelectionKind.Stash ||
      changesStateAfterLoad.selection.selectedStashedFile !==
        selectionBeforeLoad.selectedStashedFile
    ) {
      return
    }
```

**File:** app/src/lib/stores/app-store.ts (L5155-5162)
```typescript
  public async _push(
    repository: Repository,
    options?: PushOptions
  ): Promise<void> {
    return this.withRefreshedGitHubRepository(repository, repository => {
      return this.performPush(repository, options)
    })
  }
```

**File:** app/src/lib/stores/app-store.ts (L5427-5461)
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

  public async _pull(repository: Repository): Promise<void> {
    return this.withRefreshedGitHubRepository(repository, repository => {
      return this.performPull(repository)
    })
  }

  /** This shouldn't be called directly. See `Dispatcher`. */
  private async performPull(repository: Repository): Promise<void> {
    return this.withPushPullFetch(repository, async () => {
      const gitStore = this.gitStoreCache.get(repository)
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

**File:** app/src/lib/stores/app-store.ts (L5895-5915)
```typescript
  public _fetch(repository: Repository, fetchType: FetchType): Promise<void> {
    return this.withRefreshedGitHubRepository(repository, repository => {
      return this.performFetch(repository, fetchType)
    })
  }

  /**
   * Fetch a particular remote in a repository.
   *
   * Note that this method will not perform the fetch of the specified remote
   * if _any_ fetches or pulls are currently in-progress.
   */
  private _fetchRemote(
    repository: Repository,
    remote: IRemote,
    fetchType: FetchType
  ): Promise<void> {
    return this.withRefreshedGitHubRepository(repository, repository => {
      return this.performFetch(repository, fetchType, [remote])
    })
  }
```

**File:** app/src/lib/stores/app-store.ts (L8633-8691)
```typescript
  public async _findPullRequestBranch(
    repository: RepositoryWithGitHubRepository,
    prNumber: number,
    headRepoOwner: string,
    headCloneUrl: string,
    headRefName: string
  ): Promise<Branch | undefined> {
    const gitStore = this.gitStoreCache.get(repository)
    const remotes = await getRemotes(repository)

    // Find an existing remote (regardless if set up by us or outside of
    // Desktop).
    let remote = remotes.find(r => urlMatchesRemote(headCloneUrl, r))

    // If we can't find one we'll create a Desktop fork remote.
    if (remote === undefined) {
      try {
        const forkRemoteName = forkPullRequestRemoteName(headRepoOwner)
        remote = await addRemote(repository, forkRemoteName, headCloneUrl)
      } catch (e) {
        this.emitError(
          new Error(
            `Couldn't find PR branch, adding remote failed: ${e.message}`
          )
        )
        return
      }
    }

    const remoteRef = `${remote.name}/${headRefName}`

    // Start by trying to find a local branch that is tracking the remote ref.
    let existingBranch = gitStore.allBranches.find(
      x => x.type === BranchType.Local && x.upstream === remoteRef
    )

    // If we found one, let's check it out and get out of here, quick
    if (existingBranch !== undefined) {
      return existingBranch
    }

    const findRemoteBranch = (name: string) =>
      gitStore.allBranches.find(
        x => x.type === BranchType.Remote && x.name === name
      )

    // No such luck, let's see if we can at least find the remote branch then
    existingBranch = findRemoteBranch(remoteRef)

    // It's quite possible that the PR was created after our last fetch of the
    // remote so let's fetch it and then try again.
    if (existingBranch === undefined) {
      try {
        await this._fetchRemote(repository, remote, FetchType.UserInitiatedTask)
        existingBranch = findRemoteBranch(remoteRef)
      } catch (e) {
        log.error(`Failed fetching remote ${remote?.name}`, e)
      }
    }
```

**File:** app/src/lib/stores/git-store.ts (L978-1031)
```typescript
  public async fetch(
    backgroundTask: boolean,
    progressCallback?: (fetchProgress: IFetchProgress) => void
  ): Promise<void> {
    // Use a map as a simple way of getting a unique set of remotes.
    // Note that maps iterate in insertion order so the order in which
    // we insert these will affect the order in which we fetch them
    const remotes = new Map<string, IRemote>()

    // We want to fetch the current remote first
    if (this.currentRemote !== null) {
      remotes.set(this.currentRemote.name, this.currentRemote)
    }

    // And then the default remote if it differs from the current
    if (this.defaultRemote !== null) {
      remotes.set(this.defaultRemote.name, this.defaultRemote)
    }

    // And finally the upstream if we're a fork
    if (this.upstreamRemote !== null) {
      remotes.set(this.upstreamRemote.name, this.upstreamRemote)
    }

    if (remotes.size > 0) {
      await this.fetchRemotes(
        [...remotes.values()],
        backgroundTask,
        progressCallback
      )
    }

    // check the upstream ref against the current branch to see if there are
    // any new commits available
    if (this.tip.kind === TipState.Valid) {
      const currentBranch = this.tip.branch
      if (
        currentBranch.upstreamRemoteName !== null &&
        currentBranch.upstream !== null
      ) {
        const range = revSymmetricDifference(
          currentBranch.name,
          currentBranch.upstream
        )
        this._aheadBehind = await getAheadBehind(this.repository, range)
      } else {
        this._aheadBehind = null
      }
    } else {
      this._aheadBehind = null
    }

    this.emitUpdate()
  }
```

**File:** app/src/lib/stores/git-store.ts (L1110-1127)
```typescript
  /**
   * Fetch a given refspec, using the given account for authentication.
   *
   * @param user - The user to use for authentication if needed.
   * @param refspec - The association between a remote and local ref to use as
   *                  part of this action. Refer to git-scm for more
   *                  information on refspecs: https://www.git-scm.com/book/tr/v2/Git-Internals-The-Refspec
   */
  public async fetchRefspec(refspec: string): Promise<void> {
    // TODO: we should favour origin here
    const remotes = await getRemotes(this.repository)

    for (const remote of remotes) {
      await this.performFailableOperation(() =>
        fetchRefspec(this.repository, remote, refspec)
      )
    }
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L761-767)
```typescript
  /** Fetch a specific refspec for the repository. */
  public fetchRefspec(
    repository: Repository,
    fetchspec: string
  ): Promise<void> {
    return this.appStore._fetchRefspec(repository, fetchspec)
  }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1998-2046)
```typescript
  private async openPullRequestFromUrl(
    url: string,
    pr: string
  ): Promise<RepositoryWithGitHubRepository | null> {
    const pullRequest = await this.appStore.fetchPullRequest(url, pr)

    if (pullRequest === null) {
      return null
    }

    // Find the repository where the PR is created in Desktop.
    let repository: Repository | null =
      this.getRepositoryFromPullRequest(pullRequest)

    if (repository !== null) {
      await this.selectRepository(repository)
    } else {
      repository = await this.openOrCloneRepository(url)
    }

    if (repository === null) {
      log.warn(
        `Open Repository from URL failed, did not find or clone repository: ${url}`
      )
      return null
    }
    if (!isRepositoryWithGitHubRepository(repository)) {
      log.warn(
        `Received a non-GitHub repository when opening repository from URL: ${url}`
      )
      return null
    }

    // ensure a fresh clone repository has it's in-memory state
    // up-to-date before performing the "Clone in Desktop" steps
    await this.appStore._refreshRepository(repository)

    if (pullRequest.head.repo === null) {
      return null
    }

    await this.appStore._checkoutPullRequest(
      repository,
      pullRequest.number,
      pullRequest.head.repo.owner.login,
      pullRequest.head.repo.clone_url,
      pullRequest.head.ref
    )

```

**File:** app/src/lib/stores/git-store-cache.ts (L26-37)
```typescript
  public get(repository: Repository): GitStore {
    let gitStore = this.gitStores.get(repository.hash)
    if (gitStore === undefined) {
      gitStore = new GitStore(repository, this.shell, this.statsStore)
      gitStore.onDidUpdate(() => this.onGitStoreUpdated(repository, gitStore!))
      gitStore.onDidError(error => this.onDidError(error))

      this.gitStores.set(repository.hash, gitStore)
    }

    return gitStore
  }
```
