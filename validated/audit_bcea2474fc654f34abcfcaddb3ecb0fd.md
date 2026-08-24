## Summary

The Erigon bug is a **classic unsynchronized concurrent-access bug**: two independent execution paths (staged-sync pipeline and `MergeLoop`) read/write the same mutable fields on a shared `Aggregator` object with no serialization. The closest functional analog in GitHub Desktop is not a Go-style memory race (the renderer/main process is single-threaded JS), but the same *class* of bug: **two independently-triggered `git` child-process operations mutating the same on-disk repository state with no mutual-exclusion guard**, where one of the triggers is attacker-influenced (a PR/fork object from the GitHub API, reachable via a deep link).

### Title
Unsynchronized concurrent `git fetch` via `_fetchRefspec` bypasses the push/pull/fetch mutual-exclusion guard - (File: `app/src/lib/stores/app-store.ts`)

### Finding Description
`AppStore` serializes all network git operations (`fetch`, `pull`, `push`) through `withPushPullFetch`, which uses the `isPushPullFetchInProgress` flag in `repositoryStateCache` as a mutex to prevent two concurrent git network operations on the same repository: [1](#0-0) 

However, `_fetchRefspec` — used to fetch a PR ref, including PRs from forks — explicitly does **not** go through this guard, by design, per its own comment: [2](#0-1) 

This means `_fetchRefspec` can run its `git fetch <remote> <refspec>` (looping over every remote via `gitStore.fetchRefspec`) at the exact same time as a user-initiated `_pull`/`_push`/`_fetch`, which itself runs `gitStore.fetch`/`fetchRemotes` followed by `fastForwardBranches` and `_refreshRepository`: [3](#0-2) [4](#0-3) 

The trigger for `_fetchRefspec`/the PR-checkout flow is reachable from an `x-github-desktop://` deep link handled by the dispatcher, which parses attacker-supplied PR data from the GitHub API (`pullRequest.head.repo.clone_url`, `owner.login`, `ref`) and drives `_checkoutPullRequest` → `_findPullRequestBranch`, adding a fork remote and fetching it without acquiring the push/pull/fetch lock: [5](#0-4) [6](#0-5) 

### Impact Explanation
If a user clicks an attacker-crafted "Open in GitHub Desktop" / PR deep link while a normal push/pull/fetch is in flight (or vice-versa — user clicks the link, then immediately hits "Push"), two `git fetch` (or fetch + push) child processes end up running concurrently against the same working directory outside of Desktop's own serialization. Desktop's `fastForwardBranches`/`_refreshRepository` logic snapshots `gitStore.tip`/`gitStore.allBranches` before the interleaved network call resolves, so the branch state used to decide what gets fast-forwarded, what remote is added (`forkPullRequestRemoteName`), and what `_refreshRepository` reports back to the UI can reflect a stale or partially-updated view of refs written by the concurrent operation. Because `updatePushPullFetchProgress` and the `isPushPullFetchInProgress` flag are only set/read by the guarded path, the unguarded `_fetchRefspec` flow's completion is invisible to that state machine, so the UI can present a "fetch finished" / "up to date" status while a second, attacker-triggered fetch against a fork remote is still mutating refs — risking the user acting on (and subsequently committing/pushing against) an inconsistent view of the repository's branch/ref state.

### Likelihood Explanation
This requires only a single click on an attacker-supplied link that happens to coincide with (or be immediately followed by) the user performing an ordinary push/pull/fetch — no local access, no malware, no elevated privileges. The concurrency gap is explicitly acknowledged in the source comment ("does not opt-in to checks that prevent multiple concurrent network actions... might require some rework"), confirming the developers are aware the guard is intentionally skipped rather than accidentally omitted, but no code exists to reconcile the two paths.

### Recommendation
Route `_fetchRefspec` (and the PR-checkout/fork-remote-add path it feeds) through the same `withPushPullFetch`/`isPushPullFetchInProgress` serialization used by `_pull`/`_push`/`_fetch`, queuing rather than racing when a network operation is already in progress, and re-validate `gitStore.tip`/`allBranches` after any awaited fetch before acting on them (mirroring the staleness checks already used elsewhere, e.g. `_changeFileSelection`).

### Proof of Concept
1. Open a repository in Desktop and begin a `Pull` on a large remote so `withPushPullFetch` is holding `isPushPullFetchInProgress = true`.
2. While the pull is running, click an attacker-supplied deep link of the form `x-github-client://openRepo/https://github.com/<owner>/<repo>?pr=<n>` pointing at a PR whose head is a fork the attacker controls.
3. Observe `openPullRequestFromUrl` → `_checkoutPullRequest` → `_findPullRequestBranch` → `_fetchRemote`/`_fetchRefspec` execute a `git fetch` against the fork remote concurrently with the in-flight `_pull`'s `git fetch`/fast-forward, with no shared lock (`isPushPullFetchInProgress` never observed by the fork-fetch path).
4. Compare the resulting local ref state/branch list surfaced in the UI against what `git for-each-ref` on disk actually shows immediately after — look for a fast-forward decision made from stale `gitStore.tip`/`allBranches` captured before the second fetch's refs landed.

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

**File:** app/src/lib/stores/app-store.ts (L5924-5977)
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
```

**File:** app/src/lib/stores/app-store.ts (L8633-8692)
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1998-2048)
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

    return repository
  }
```
