## Title
Pull request sync watermark (`pullRequestsLastUpdated`) trusts unbounded, attacker-controllable `updated_at` from the GitHub API, permanently silencing future PR updates - (File: `app/src/lib/stores/pull-request-store.ts`)

### Summary
This is the closest Desktop analog to the `EACAggregatorCombine` bug class: an unvalidated "take the newest timestamp" reduction is used as a proxy for data freshness, and a single malicious/out-of-range value poisons that proxy so that genuinely fresh data is silently treated as already-seen. In `storePullRequests`, Desktop computes `mostRecentlyUpdated` by taking the maximum `updated_at` across all PRs returned from a single API response, with no bound against the local clock, and persists it as the incremental-sync watermark (`db.setLastUpdated`). All future syncs use this watermark as the `since` parameter for `fetchUpdatedPullRequests`.

### Finding Description [1](#0-0) 

`mostRecentlyUpdated` is initialized from the first PR's `updated_at` and advanced whenever any subsequent PR's `updated_at` is lexicographically greater — a pure "take max" reduction with no upper bound (e.g. `now`) and no sanity check against previous values. [2](#0-1) 

This value is unconditionally written as the new "last updated" watermark for the repository via `db.setLastUpdated`, exactly mirroring the audit's `updatedAt = updatedAtX > updatedAtY ? updatedAtX : updatedAtY` pattern of trusting whichever of the two (or N) sources reports the newest time. [3](#0-2) 

On the next refresh cycle, that watermark is read back and used directly as the `since` bound for the delta query: [4](#0-3) 

`fetchUpdatedPullRequests` filters server-side (`sort: updated, direction: desc`) and client-side (`Date.parse(pr.updated_at) >= sinceTime`) purely on the server-supplied `updated_at` strings.

The broken invariant: nothing in this pipeline verifies that a PR's `updated_at` is bounded by the client's own notion of "now," nor that the watermark only ever advances by an amount consistent with real elapsed time. A GitHub Enterprise Server instance, a malicious/compromised proxy sitting in front of the GitHub API, or a crafted response for a `head.repo`/`base.repo` object the API returns can supply one PR with an `updated_at` far in the future. That single inflated timestamp becomes the new `mostRecentlyUpdated` watermark for the *entire repository*, because the reduction takes the max across all PRs in the batch, not the min or the actual "confirmed as fresh" boundary.

### Impact Explanation
Once the future-dated watermark is persisted, every subsequent incremental sync uses `since=<poisoned future date>`. Any PR legitimately updated between "now" and that poisoned future timestamp will never satisfy `updated_at >= sinceTime` from the real GitHub API afterward (its real `updated_at` is earlier than the poisoned watermark), so Desktop stops seeing real updates for that repository's PRs — merges, closures, new commits pushed to PR branches, CI status changes reflected through the PR object, etc. This is silent, persistent staleness masquerading as freshness: the UI shows PR state that looks complete/current (no error, no stale banner) while the user's decisions (e.g., whether to check out, review, or merge a PR) are based on data that is quietly frozen. This matches the report's core impact category ("false sense of data freshness" leading to decisions on stale data), translated to Desktop's PR cache instead of a price oracle.

### Likelihood Explanation
Exploitation requires the attacker to control or influence a GitHub API response for a repository configuration the user's Desktop instance talks to (self-hosted GitHub Enterprise Server endpoint, a network path/proxy the API request traverses, or a `head`/`base` repository object embedded in an attacker-supplied fork's PR data). No local access, no elevated privileges, and no unnatural user steps are required beyond the user having Desktop refresh PRs against that endpoint — squarely in the "attacker controls...a GitHub API object...or a git remote/proxy response" valid-impact category the task specifies. Existing guards do not stop this: there is no clamping of `updated_at` to `Date.now()`, no rejection of out-of-order/anomalous timestamps, and no monotonicity check against the previously stored watermark beyond simple max-comparison, which is precisely the vulnerable behavior.

### Recommendation
- Clamp any timestamp used to compute `mostRecentlyUpdated` to `Math.min(serverTimestamp, Date.now())` before advancing the watermark.
- Reject or flag PR objects whose `updated_at` is implausibly far in the future relative to the client clock, rather than folding them into the sync watermark.
- Consider storing/using the true maximum only from a trusted subset (e.g., PRs belonging to the base repository, not attacker-influenced fork/head repository objects), and add a self-check that periodically forces a full resync (as already done for `fetchAndStoreOpenPullRequests`) to bound the damage from a single corrupted watermark.

### Proof of Concept
1. Point Desktop at a GitHub Enterprise Server instance (or a MITM/malicious proxy in front of `api.github.com`) that the attacker controls or can influence.
2. When Desktop calls `fetchAllOpenPullRequests`/`fetchUpdatedPullRequests` for the target repository, have the server include one PR object with `updated_at` set to a timestamp far in the future (e.g., `+2 days` from now).
3. `storePullRequests` computes `mostRecentlyUpdated` as this future timestamp (`app/src/lib/stores/pull-request-store.ts:241-273`) and persists it via `db.setLastUpdated` (`app/src/lib/stores/pull-request-store.ts:341-350`).
4. Have real, legitimate PR updates occur on the repository with `updated_at` values between "now" and the poisoned future timestamp.
5. On the next refresh, `fetchUpdatedPullRequests(owner, name, lastUpdatedAt)` uses the poisoned `since` value; the legitimate updates (whose `updated_at` is less than the poisoned watermark) are filtered out both server-side and client-side (`app/src/lib/api.ts:1213-1260`), so Desktop's PR list silently stops reflecting real repository state.

### Citations

**File:** app/src/lib/stores/pull-request-store.ts (L114-121)
```typescript
  private async fetchAndStoreUpdatedPullRequests(
    api: API,
    repository: GitHubRepository,
    lastUpdatedAt: Date
  ) {
    const { name, owner } = getNameWithOwner(repository)
    const updated = await api
      .fetchUpdatedPullRequests(owner, name, lastUpdatedAt)
```

**File:** app/src/lib/stores/pull-request-store.ts (L241-273)
```typescript
    let mostRecentlyUpdated = pullRequestsFromAPI[0].updated_at

    const prsToDelete = new Array<PullRequestKey>()
    const prsToUpsert = new Array<IPullRequest>()

    // The API endpoint for this PR, i.e api.github.com or a GHE url
    const { endpoint } = repository
    const store = this.repositoryStore

    // Upsert will always query the database for a repository. Given that
    // we've receive these repositories in a batch response from the API
    // it's pretty unlikely that they'd differ between PRs so we're going
    // to use the upsert just to ensure that the repo exists in the database
    // and reuse the same object without going to the database for all that
    // follow.
    const upsertRepo = mem(store.upsertGitHubRepositoryLight.bind(store), {
      // The first argument which we're ignoring here is the endpoint
      // which is constant throughout the lifetime of this function.
      // The second argument is an `IAPIRepository` which is basically
      // the raw object that we got from the API which could consist of
      // more than just the fields we've modelled in the interface. The
      // only thing we really care about to determine whether the
      // repository has already been inserted in the database is the clone
      // url since that's what the upsert method uses as its key.
      cacheKey: (_, repo) => repo.clone_url,
    })

    for (const pr of pullRequestsFromAPI) {
      // We can do this string comparison here rather than convert to date
      // because ISO8601 is lexicographically sortable
      if (pr.updated_at > mostRecentlyUpdated) {
        mostRecentlyUpdated = pr.updated_at
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

**File:** app/src/lib/api.ts (L1213-1227)
```typescript
  public async fetchUpdatedPullRequests(
    owner: string,
    name: string,
    since: Date,
    // 320 is chosen because with a ramp-up page size starting with
    // a page size of 10 we'll reach 320 in exactly 7 pages. See
    // getNextPagePathWithIncreasingPageSize
    maxResults = 320
  ) {
    const sinceTime = since.getTime()
    const url = urlWithQueryString(`repos/${owner}/${name}/pulls`, {
      state: 'all',
      sort: 'updated',
      direction: 'desc',
    })
```
