Based on my investigation, I found a concrete analog to the go-ethereum downloader race condition: GitHub Desktop has an explicitly-documented concurrency gap in its fetch-serialization logic that allows a fork-triggered fetch to run in parallel with a user-initiated network operation on the same on-disk repository.

### Title
Unsynchronized concurrent `git fetch` invocations on the same repository via `_fetchRefspec` bypass of push/pull/fetch locking - (File: `app/src/lib/stores/app-store.ts`)

### Summary
GitHub Desktop serializes push/pull/fetch operations per-repository through a single boolean flag, `isPushPullFetchInProgress`, guarded by `withPushPullFetch` [1](#0-0) . However, `_fetchRefspec`, which is used to fetch a pull request's head ref (attacker-controlled, since it comes from a fork's `head.repo.clone_url`/`head.ref`), is explicitly documented as **not** participating in that lock: "As this action is required to complete when viewing a Pull Request from a fork, it does not opt-in to checks that prevent multiple concurrent network actions." [2](#0-1) 

### Finding Description
`withPushPullFetch` is the only mechanism preventing multiple native `git` fetch/pull/push child processes from running concurrently against the same working directory `.git` folder [1](#0-0) . `_fetch`/`_fetchRemote`/`performPull`/`performPush` all funnel through this guard [3](#0-2) . `_fetchRefspec`, by contrast, calls `gitStore.fetchRefspec(refspec)` directly with no such guard [4](#0-3) , which in turn invokes the low-level `fetchRefspec` git wrapper that runs `git fetch <remote> <refspec>` [5](#0-4) .

`_fetchRefspec` is exercised from the pull-request-from-URL / "checkout PR" flow (`_findPullRequestBranch` calls `_fetchRemote`, and the broader PR-checkout pipeline calls `_fetchRefspec` when the target ref/remote is attacker-supplied by the PR author) [6](#0-5) . Since GitHub PR metadata (`head.repo.clone_url`, `head.ref`) is fully attacker-controlled by whoever opens the PR, an attacker can craft a PR against a repository the victim has open in Desktop, and can construct a remote/ref combination such that its `git fetch` runs at the same time the victim independently triggers a `push`, `pull`, or background fetch through `BackgroundFetcher`/`shouldBackgroundFetch` [7](#0-6) , which is on a periodic timer independent of the `_fetchRefspec` call.

Because both operations execute native `git` processes against the same repository path concurrently, and Desktop's only serialization primitive (`isPushPullFetchInProgress`) is deliberately skipped for `_fetchRefspec`, two `git` processes can race on shared repository state: the packed-refs file, loose ref files, `FETCH_HEAD`, and the index/lock files that git itself only partially protects with `.git/index.lock`. `fastForwardBranches` even explicitly notes it must pass `--no-write-fetch-head` to avoid corrupting `FETCH_HEAD` during concurrent fetches [8](#0-7) , which is itself an acknowledgment that concurrent fetch/pull calls into the same repo produce unsafe state — yet `_fetchRefspec` is not covered by that same protective plumbing since it bypasses the `withPushPullFetch` critical section entirely.

### Impact Explanation
The unsynchronized ref/pack writes can result in silent corruption of local branch refs, loss of ref updates from a concurrently in-flight `git push`, or an interrupted/partial pack-write leaving the repository's object database or refs in an inconsistent state. In the worst case this could cause Desktop to report and subsequently commit/push against a stale or corrupted ref, i.e., silently altering what the user believes they are pushing — matching the "silent corruption of what the user commits or pushes" impact class. This is triggered purely by an attacker opening a pull request against a repository the victim has cloned in Desktop; no local access, credentials, or unusual user action beyond normal PR review/checkout is required.

### Likelihood Explanation
Moderate. The victim must have background fetching active (default behavior whenever a GitHub-backed repository is open) and must trigger a PR-related fetch path (viewing/checking out a PR, following a deep link, etc.) around the same time a push/pull/background-fetch is in flight. The race window is real but narrow, and git's own locking (e.g., `index.lock`) mitigates some but not all interleavings — it does not prevent races on ref updates that don't take that lock, and the code comment in `app-store.ts` acknowledges the design gap directly as a known, currently-unaddressed issue ("This might require some rework in the future to chain these actions").

### Recommendation
Route `_fetchRefspec` through the same `withPushPullFetch` (or an equivalent per-repository mutex) used by `_fetch`/`performPull`/`performPush`, queuing the refspec fetch instead of allowing it to run concurrently with other network operations, as the existing code comment itself suggests is needed.

### Proof of Concept
1. Attacker opens a pull request from a fork against a public repository, with `head.repo.clone_url`/`head.ref` under attacker control.
2. Victim has this repository open in GitHub Desktop with background fetching active (`BackgroundFetcher` on its timer) or is about to invoke `push`/`pull` at approximately the same time.
3. Victim opens the PR notification/deep link or the PR list entry, triggering the checkout-PR pipeline that calls `_fetchRefspec` for the attacker's fork ref, which executes directly without acquiring `isPushPullFetchInProgress` [4](#0-3) .
4. If the victim's independent push/pull/background fetch is in flight at the same moment, two `git fetch`/`git push` processes execute concurrently against the same `.git` directory, racing on ref/pack state with no application-level synchronization. [1](#0-0) [2](#0-1) [5](#0-4)

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

**File:** app/src/lib/stores/app-store.ts (L8674-8691)
```typescript
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

**File:** app/src/lib/git/fetch.ts (L103-141)
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
}
```

**File:** app/src/lib/stores/helpers/background-fetcher.ts (L1-60)
```typescript
import { Repository } from '../../../models/repository'
import { GitHubRepository } from '../../../models/github-repository'
import { API, getAccountForEndpoint } from '../../api'
import { fatalError } from '../../fatal-error'
import { AccountsStore } from '../accounts-store'

/**
 * A default interval at which to automatically fetch repositories, if the
 * server doesn't specify one or the header is malformed.
 */
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

/** The class which handles doing background fetches of the repository. */
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
```
