Based on my research, I found a concrete Desktop analog: an intentional bypass of the concurrency guard that protects git network operations, exploitable via attacker-controlled PR/fork metadata.

### Title
Intentional Bypass of `withPushPullFetch` Concurrency Guard in `_fetchRefspec` Allows Concurrent Git Processes Triggered by Malicious Fork/PR - (File: `app/src/lib/stores/app-store.ts`)

### Summary
The report's underlying bug class is a "single-writer" invariant guard around a git-mutating operation that either (a) blocks legitimate concurrent callers (the Move case, DoS) or (b) is deliberately skipped for one caller, allowing two independent git-mutating operations to run concurrently against the same repository (the Desktop case, corruption risk). In Desktop, the guard is `withPushPullFetch`, which sets `isPushPullFetchInProgress` to serialize push/pull/fetch operations against a `Repository`. `_fetchRefspec` explicitly does not use this guard so that a PR-from-fork view can complete, which means it can run at the same time as a user-initiated push/pull/fetch on the same working directory.

### Finding Description
`withPushPullFetch` is the mutual-exclusion primitive Desktop uses to prevent two network git operations from mutating the same repository's `.git` state concurrently: [1](#0-0) 

`_pull`/`performPull` and `_fetch`/`performFetch` are routed through this guard, so a user cannot run a manual pull while a fetch is already in progress: [2](#0-1) 

However, `_fetchRefspec` — used specifically "when viewing a Pull Request from a fork" — is explicitly documented and implemented to skip this guard: [3](#0-2) 

The comment on `_fetch` right below it confirms the intended invariant that is broken: "this method will not perform the fetch of the specified remote if _any_ fetches or pulls are currently in-progress" — an invariant `_fetchRefspec` does not honor: [4](#0-3) 

`fetchRefspec` itself shells out to `git fetch <remote> <refspec>` directly against `repository.path`, mutating remote-tracking refs and `FETCH_HEAD`-adjacent state in the same on-disk `.git` directory that a concurrently-running push/pull is also mutating: [5](#0-4) 

The refspec and remote are derived from PR/fork metadata that is attacker-controlled: `_findPullRequestBranch` adds a fork remote from `headCloneUrl` and fetches by `headRefName`, both sourced from the GitHub PR object of a fork the attacker owns: [6](#0-5) 

Because `_fetchRefspec`'s call to `gitStore.fetchRefspec` runs outside `withPushPullFetch`, an attacker who controls a fork/PR (i.e., an unprivileged, no-write-access external contributor) can cause a fetch against their own attacker-controlled remote to execute concurrently with the victim's legitimate push/pull, simply by having the victim view or interact with that PR while a push/pull is in flight. This breaks the exact "no concurrent git-mutating operation on this repository" invariant that the reentry_check / withPushPullFetch pattern was designed to enforce, but in the opposite direction from the Move report: instead of a legitimate second caller being blocked (DoS), a caller is deliberately allowed to race with another, unsynchronized libgit2/git-cli process operating on the same working directory and `.git` metadata.

### Impact Explanation
Two concurrent, un-coordinated `git` child processes writing to the same repository's ref store/pack files/`FETCH_HEAD` can interleave in ways not tested or guarded against by Desktop's application-level state machine (`repositoryStateCache`), since that state machine assumes serialized access via `withPushPullFetch`. This can result in the application refreshing/caching an inconsistent view of branches and tips (e.g., `_refreshRepository` reading state mid-race), and in the worst case corrupts what the local git metadata reflects about the user's branches/remotes for a subsequent commit or push, without any error being surfaced to the user (silent corruption of local repo state that later feeds into what gets pushed). This matches the "silent corruption of what the user commits or pushes" impact category, and the trigger is entirely attacker-controlled (a malicious PR/fork's `headCloneUrl`/`headRefName`), requiring no local access, admin rights, or prior compromise — only that the victim view a malicious PR while another network operation is running.

### Likelihood Explanation
Likelihood is moderate-to-low: it requires precise timing (an in-flight push/pull/fetch overlapping with the moment the victim views/interacts with a malicious PR from a fork), and I could not fully verify from the index alone whether the two concurrent `git` invocations would produce observable corruption versus git's own internal locking (e.g., `.git/index.lock`, ref-transaction locks) safely serializing the writes at the git level. Git's own file-locking for refs/objects may mitigate outright corruption in many cases, reducing this to a more limited race (stale progress UI, incorrect ahead/behind display, or a failed second git process) rather than guaranteed data corruption. This uncertainty should be validated with a live repro.

### Recommendation
Route `_fetchRefspec` through the same `withPushPullFetch` (or an equivalent per-repository mutex) guard used by `_pull`/`_fetch`/`_push`, queuing the fork/PR refspec fetch behind any in-progress network operation instead of bypassing the guard, as the existing code comment itself flags as needed ("This might require some rework in the future to chain these actions").

### Proof of Concept
1. Open a repository in Desktop with a push or pull in progress (e.g., a large push to a slow remote).
2. While that operation is in flight, view/select a Pull Request from an attacker-controlled fork (attacker sets `headCloneUrl`/`headRefName` in their PR/fork metadata) such that Desktop calls `_fetchRefspec` — this bypasses `withPushPullFetch`'s `isPushPullFetchInProgress` check.
3. Observe that Desktop now runs two `git` processes concurrently against the same `repository.path`: the original push/pull and `git fetch <attacker-remote> <attacker-refspec>` from `fetch.ts`'s `fetchRefspec`, violating the single-writer invariant the rest of the network-operation code relies on.
4. Repro of actual corruption (vs. benign git-level serialization) requires running this scenario end-to-end in a live environment, which is outside what the static index can confirm.

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

**File:** app/src/lib/stores/app-store.ts (L5452-5460)
```typescript
  public async _pull(repository: Repository): Promise<void> {
    return this.withRefreshedGitHubRepository(repository, repository => {
      return this.performPull(repository)
    })
  }

  /** This shouldn't be called directly. See `Dispatcher`. */
  private async performPull(repository: Repository): Promise<void> {
    return this.withPushPullFetch(repository, async () => {
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

**File:** app/src/lib/stores/app-store.ts (L5887-5899)
```typescript
  /**
   * Fetch all relevant remotes in the the repository.
   *
   * See gitStore.fetch for more details.
   *
   * Note that this method will not perform the fetch of the specified remote
   * if _any_ fetches or pulls are currently in-progress.
   */
  public _fetch(repository: Repository, fetchType: FetchType): Promise<void> {
    return this.withRefreshedGitHubRepository(repository, repository => {
      return this.performFetch(repository, fetchType)
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
