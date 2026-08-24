Confirmed: the persistence of `prsToUpsert`/`prsToDelete`/`mostRecentlyUpdated` all happens in a single `db.transaction` call *after* the `for` loop finishes. This means the `fatalError('PR cannot have a null base repo')` at [1](#0-0)  throws synchronously mid-loop, before the transaction commits, discarding every PR processed in that batch — including legitimate ones already accumulated in `prsToUpsert`.

### Title
Malformed GitHub API pull-request payload (`base.repo: null`) throws instead of degrading gracefully, silently discarding an entire pull-request sync batch - (File: `app/src/lib/stores/pull-request-store.ts`)

### Summary
`PullRequestStore.storePullRequests` iterates over pull requests returned from the GitHub/GHE REST API and asserts an invariant that `pr.base.repo` is never `null`, using `fatalError()` (a `throw new Error`) when it is. This is inconsistent with the adjacent, functionally identical case for `pr.head.repo === null`, which is handled gracefully (logged and the PR skipped). The report's underlying bug class — an `assert`-style invariant check on attacker/server-influenced input causing a hard revert/throw instead of a soft failure, leading to stuck/inconsistent application state — maps directly onto this code path.

### Finding Description
`storePullRequests` is fed `pullRequestsFromAPI: ReadonlyArray<IAPIPullRequest>`, data returned directly from `api.fetchAllOpenPullRequests` / `api.fetchUpdatedPullRequests`, i.e. an untrusted GitHub API object as defined in the scope (this includes a compromised/malicious GitHub Enterprise Server the user has configured, or a network path capable of tampering with API responses, e.g. a misbehaving proxy/MITM on the corporate network for GHE users).

For each `pr` in the batch:
```
if (pr.base.repo === null) {
  return fatalError('PR cannot have a null base repo')
}
``` [1](#0-0) 

versus the sibling check just a few lines later that degrades gracefully instead of throwing:
```
if (pr.head.repo == null) {
  log.debug(...)
  prsToDelete.push(getPullRequestKey(baseGitHubRepo, pr.number))
  continue
}
``` [2](#0-1) 

`fatalError` is defined as an unconditional `throw new Error(msg)` [3](#0-2) . All persistence (`prsToUpsert`, `prsToDelete`, and the new `lastUpdated` watermark) is only written in a single Dexie transaction *after* the loop completes: [4](#0-3) . Because the throw happens mid-loop, none of that batch's data — including PRs that were validly processed before the malformed entry was encountered — is persisted, and `lastUpdated` is not advanced.

The exception propagates up through `fetchAndStoreOpenPullRequests` / `fetchAndStoreUpdatedPullRequests` [5](#0-4)  to `refreshPullRequests`, where it is caught and merely logged: [6](#0-5) . This "catch and log" is exactly analogous to the Solidity `processCrossChainCallback` pattern of gracefully marking the transaction `Failed` instead of reverting — except Desktop's own internal invariant check throws before that graceful boundary is reached, discarding useful work and leaving the PR/branch state (`openPullRequests`, associated `currentPullRequest`, "force-push" badges) stale indefinitely, since `lastUpdatedAt` is never advanced and every subsequent refresh will re-fetch and hit the same poisoned entry again on the next attempted `fetchUpdatedPullRequests` call, because the watermark that gates incremental fetching never advances.

### Impact Explanation
A single crafted/corrupted PR object from a GitHub Enterprise Server the user trusts (or any response injected on that network path) permanently blocks pull-request list refresh for that repository: no new or updated PR data is stored, `currentPullRequest`/`openPullRequests` used to drive branch-protection UI, "Open Pull Request" dialog data, and CI-check notifications become stale, and the condition self-perpetuates because the timestamp watermark used to bound future queries is never advanced (see `fetchAndStoreUpdatedPullRequests`'s use of `lastUpdatedAt` [7](#0-6) ). This is a silent, indefinite desync between what GitHub Desktop shows and the real PR state — the direct analog to the report's "transactions remaining in a Pending state indefinitely" and "inconsistent transaction states."

### Likelihood Explanation
Requires the attacker to control (or corrupt in transit) a GitHub/GHE API JSON response so that a PR object's `base.repo` field is `null` while `base` itself is present — plausible for a GHE instance under attacker influence or a tampering proxy, matching the allowed "GitHub API object" / "proxy response" threat model. No local access, credentials, or social engineering needed; this is a pure server/response-trust issue reachable through the normal periodic PR-refresh background job.

### Recommendation
Replace the `fatalError` invariant with graceful handling identical to the `pr.head.repo == null` branch: log the anomaly, skip/queue the offending PR for deletion, and continue processing the rest of the batch so that valid PRs are still committed and the `lastUpdated` watermark still advances for the entries that were valid.

### Proof of Concept
1. Configure Desktop against a GitHub Enterprise Server endpoint (or intercept the `GET /repos/{owner}/{repo}/pulls` response for `api.github.com` via a network path the attacker controls).
2. Return a PR array where one entry has `"base": { "repo": null, ... }` while all other fields look valid.
3. Trigger `PullRequestStore.refreshPullRequests` (this happens automatically on repository selection/background refresh).
4. Observe: `storePullRequests` throws inside the loop [8](#0-7) ; the error is swallowed by the `.catch` in `refreshPullRequests` [9](#0-8) ; no PRs from the batch (including legitimate ones before the poisoned entry in iteration order) are persisted, and `pullRequestsLastUpdated` is not advanced, so every subsequent refresh repeats the same failure indefinitely.

### Citations

**File:** app/src/lib/stores/pull-request-store.ts (L51-70)
```typescript
  public refreshPullRequests(repo: GitHubRepository, account: Account) {
    const currentOp = this.currentRefreshOperations.get(repo.dbID)

    if (currentOp !== undefined) {
      return currentOp
    }

    this.lastRefreshForRepository.set(repo.dbID, Date.now())

    const promise = this.fetchAndStorePullRequests(repo, account)
      .catch(err => {
        log.error(`Error refreshing pull requests for '${repo.fullName}'`, err)
      })
      .then(() => {
        this.currentRefreshOperations.delete(repo.dbID)
      })

    this.currentRefreshOperations.set(repo.dbID, promise)
    return promise
  }
```

**File:** app/src/lib/stores/pull-request-store.ts (L105-151)
```typescript
  private async fetchAndStoreOpenPullRequests(
    api: API,
    repository: GitHubRepository
  ) {
    const { name, owner } = getNameWithOwner(repository)
    const open = await api.fetchAllOpenPullRequests(owner, name)
    await this.storePullRequestsAndEmitUpdate(open, repository)
  }

  private async fetchAndStoreUpdatedPullRequests(
    api: API,
    repository: GitHubRepository,
    lastUpdatedAt: Date
  ) {
    const { name, owner } = getNameWithOwner(repository)
    const updated = await api
      .fetchUpdatedPullRequests(owner, name, lastUpdatedAt)
      .catch(e =>
        // Any other error we'll bubble up but these ones we
        // can handle, see below.
        e instanceof MaxResultsError || e instanceof APIError
          ? Promise.resolve(null)
          : Promise.reject(e)
      )

    if (updated !== null) {
      return await this.storePullRequestsAndEmitUpdate(updated, repository)
    } else {
      // If we fail to load updated pull requests either because
      // there's too many updated PRs since the last time we
      // fetched (and it's likely that it'll be much more
      // efficient to just load the open PRs) or it's because the
      // API told us we couldn't load PRs (rate limit or permissions
      // problems). In either case we delete the PRs we've got
      // for this repo and attempt to load just the open ones.
      //
      // This scenario can happen for repositories that are
      // very active while simultaneously infrequently used
      // by the user. Think of a very active open source repository
      // where the user only visits once a year to make a contribution.
      // It's likely that there's at most a few hundred PRs open but
      // the number of merged PRs since the last time we fetched could
      // number in the thousands.
      await this.db.deleteAllPullRequestsInRepository(repository)
      await this.fetchAndStoreOpenPullRequests(api, repository)
    }
  }
```

**File:** app/src/lib/stores/pull-request-store.ts (L275-279)
```typescript
      // We know the base repo isn't null since that's where we got the PR from
      // in the first place.
      if (pr.base.repo === null) {
        return fatalError('PR cannot have a null base repo')
      }
```

**File:** app/src/lib/stores/pull-request-store.ts (L288-301)
```typescript
      // `pr.head.repo` represents the source of the pull request. It might be
      // a branch associated with the current repository, or a fork of the
      // current repository.
      //
      // In cases where the user has removed the fork of the repository after
      // opening a pull request, this can be `null`, and the app will not store
      // this pull request.
      if (pr.head.repo == null) {
        log.debug(
          `Unable to store pull request #${pr.number} for repository ${repository.fullName} as it has no head repository associated with it`
        )
        prsToDelete.push(getPullRequestKey(baseGitHubRepo, pr.number))
        continue
      }
```

**File:** app/src/lib/stores/pull-request-store.ts (L341-350)
```typescript
    await this.db.transaction(
      'rw',
      this.db.pullRequests,
      this.db.pullRequestsLastUpdated,
      async () => {
        await this.db.deletePullRequests(prsToDelete)
        await this.db.putPullRequests(prsToUpsert)
        await this.db.setLastUpdated(repository, new Date(mostRecentlyUpdated))
      }
    )
```

**File:** app/src/lib/fatal-error.ts (L1-4)
```typescript
/** Throw an error. */
export function fatalError(msg: string): never {
  throw new Error(msg)
}
```
