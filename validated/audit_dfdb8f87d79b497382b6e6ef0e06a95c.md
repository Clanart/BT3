Confirmed. This is a real analog of the Besu bug class: an unbounded/unvalidated field parsed from an attacker-influenced external source is checked with an unconditional `throw`, and that throw aborts a batch-processing loop mid-way, silently discarding work that had already been accumulated for the earlier (valid) items in the same batch, with the "malformed" item's poison effect not being isolated by any catch at the right layer.

### Title
Single malformed PR in a GitHub API pull-request page aborts and drops the entire batch update - (File: `app/src/lib/stores/pull-request-store.ts`)

### Summary
`PullRequestStore.storePullRequests` iterates over an array of pull requests fetched directly from the GitHub API and, for every entry whose `base.repo` is `null`, calls `fatalError(...)`, which unconditionally throws. [1](#0-0) [2](#0-1) 

### Finding Description
`storePullRequests` builds `prsToDelete`/`prsToUpsert` incrementally in a `for...of` loop over `pullRequestsFromAPI`, and only commits them to IndexedDB in a single transaction *after* the loop finishes. [3](#0-2) [4](#0-3) 

The loop guards `pr.head.repo == null` gracefully (deletes and `continue`s), but the symmetric case for `pr.base.repo === null` is treated as an unrecoverable invariant violation and calls `fatalError`, which is just `throw new Error(msg)` — an unchecked exception, not a `ValidationResult`/return value: [5](#0-4) [2](#0-1) 

This exception propagates out of `storePullRequests` → `storePullRequestsAndEmitUpdate` → `fetchAndStoreOpenPullRequests` / `fetchAndStoreUpdatedPullRequests` → `fetchAndStorePullRequests`, none of which catch it: [6](#0-5) [7](#0-6) 

It is only caught much higher up, in `refreshPullRequests`, which merely logs it: [8](#0-7) 

Because the DB write happens in one transaction after the loop completes, throwing partway through the loop means **none** of the PRs processed before the poisoned entry are persisted or deleted for that refresh cycle — this mirrors the Besu report's pattern precisely: an unbounded/unchecked field from an untrusted batch triggers a raw exception instead of a validation result, which aborts collection of an in-flight batch and silently drops legitimately-processed items, with no isolation/disconnect mechanism for the "bad" source. `pr.base.repo` can be `null` from the GitHub API for the same class of reason `pr.head.repo` can (e.g., the base fork/repo was deleted, transferred, or made private between listing and detail-fetch, or a malicious/compromised GHE-style endpoint returns a crafted payload) — the API response is attacker-influenceable via a custom GitHub Enterprise endpoint the app is configured against, or via the app's fallback to raw API objects that pass through with only partial validation upstream in `api.ts`.

### Impact Explanation
Silent corruption of the user-visible pull request cache: valid PRs the user already fetched (including ones about to be deleted because they were merged/closed) are dropped from processing for that refresh cycle, and since `lastUpdatedAt`/`setLastUpdated` is never advanced (the whole transaction is skipped), the local PR store can drift out of sync with GitHub silently, with only a `log.error` and no user-facing signal. This matches the report's "impact" bucket of silent corruption of state the user relies on (though for Desktop this is PR metadata rather than a value the user commits/pushes, so severity is more moderate than the Besu case, which affected consensus-relevant transaction propagation).

### Likelihood Explanation
Moderate. It requires a GitHub (or GHE) API response where a PR entry in the fetched page has a `null` `base.repo` while other entries in the same batch are valid — a legitimate edge case (base repo deleted/renamed mid-window) that GitHub's `head.repo` case already anticipates but `base.repo` does not. No local access or malware is needed; the attacker-influenced input is the API response content for a repository the app already talks to.

### Recommendation
Handle `pr.base.repo === null` the same way `pr.head.repo == null` is handled: skip/delete that single PR and `continue`, rather than calling `fatalError`/throwing. If it must remain a hard invariant, wrap the per-PR body of the loop in a `try/catch` (or validate/report and `continue`) so a single malformed entry cannot invalidate previously-accumulated deletions/upserts for the rest of the batch.

### Proof of Concept
1. Configure Desktop against a repository whose GitHub API `pulls` endpoint returns a page containing multiple PRs, where PR #2 has `base.repo: null` (achievable via a custom/compromised GHE instance, or a race where the base repo is deleted between the list and detail call).
2. Trigger a PR refresh (`PullRequestStore.refreshPullRequests`).
3. `storePullRequests` processes PR #1 (added to `prsToUpsert`), then hits PR #2 and calls `fatalError('PR cannot have a null base repo')`, throwing.
4. The exception propagates uncaught through `storePullRequestsAndEmitUpdate`/`fetchAndStore*PullRequests`, is swallowed by the `.catch` in `refreshPullRequests`, and the DB transaction (which would have stored PR #1 and any subsequent valid PRs, e.g. PR #3) never runs — the local PR cache silently fails to update for this cycle, with no user notification and no advancement of `lastUpdated`.

### Citations

**File:** app/src/lib/stores/pull-request-store.ts (L60-66)
```typescript
    const promise = this.fetchAndStorePullRequests(repo, account)
      .catch(err => {
        log.error(`Error refreshing pull requests for '${repo.fullName}'`, err)
      })
      .then(() => {
        this.currentRefreshOperations.delete(repo.dbID)
      })
```

**File:** app/src/lib/stores/pull-request-store.ts (L81-103)
```typescript
  private async fetchAndStorePullRequests(
    repo: GitHubRepository,
    account: Account
  ) {
    const api = API.fromAccount(account)
    const lastUpdatedAt = await this.db.getLastUpdated(repo)

    // If we don't have a lastUpdatedAt that mean we haven't fetched any PRs
    // for the repository yet which in turn means we only have to fetch the
    // currently open PRs. If we have fetched before we get all PRs
    // If we have a lastUpdatedAt that mean we have fetched PRs
    // for the repository before. If we have fetched before we get all PRs
    // that have been modified since the last time we fetched so that we
    // can prune closed issues from our database. Note that since
    // `api.fetchUpdatedPullRequests` returns all issues modified _at_ or
    // after the timestamp we give it we will always get at least one issue
    // back. See `storePullRequests` for details on how that's handled.
    if (!lastUpdatedAt) {
      return this.fetchAndStoreOpenPullRequests(api, repo)
    } else {
      return this.fetchAndStoreUpdatedPullRequests(api, repo, lastUpdatedAt)
    }
  }
```

**File:** app/src/lib/stores/pull-request-store.ts (L105-112)
```typescript
  private async fetchAndStoreOpenPullRequests(
    api: API,
    repository: GitHubRepository
  ) {
    const { name, owner } = getNameWithOwner(repository)
    const open = await api.fetchAllOpenPullRequests(owner, name)
    await this.storePullRequestsAndEmitUpdate(open, repository)
  }
```

**File:** app/src/lib/stores/pull-request-store.ts (L233-245)
```typescript
  private async storePullRequests(
    pullRequestsFromAPI: ReadonlyArray<IAPIPullRequest>,
    repository: GitHubRepository
  ) {
    if (pullRequestsFromAPI.length === 0) {
      return false
    }

    let mostRecentlyUpdated = pullRequestsFromAPI[0].updated_at

    const prsToDelete = new Array<PullRequestKey>()
    const prsToUpsert = new Array<IPullRequest>()

```

**File:** app/src/lib/stores/pull-request-store.ts (L268-301)
```typescript
    for (const pr of pullRequestsFromAPI) {
      // We can do this string comparison here rather than convert to date
      // because ISO8601 is lexicographically sortable
      if (pr.updated_at > mostRecentlyUpdated) {
        mostRecentlyUpdated = pr.updated_at
      }

      // We know the base repo isn't null since that's where we got the PR from
      // in the first place.
      if (pr.base.repo === null) {
        return fatalError('PR cannot have a null base repo')
      }

      const baseGitHubRepo = await upsertRepo(endpoint, pr.base.repo)

      if (pr.state === 'closed') {
        prsToDelete.push(getPullRequestKey(baseGitHubRepo, pr.number))
        continue
      }

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
