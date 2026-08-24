## Finding

Analog identified: **`AppStore._findPullRequestBranch`** in `app/src/lib/stores/app-store.ts:8633-8721`, used by `_checkoutPullRequest` (`app/src/lib/stores/app-store.ts:8613-8631`).

### Title
Pull request checkout trusts the fetched ref tip without validating it against the previously recorded `head.sha` - (File: `app/src/lib/stores/app-store.ts`)

### Summary
When GitHub Desktop stores a pull request fetched from the API, it records both the head ref name and the head commit SHA together as one unit of state [1](#0-0) . However, when the user actually checks out that pull request, only the ref name (`headRefName`) is used to locate/fetch the branch; the recorded `head.sha` is never re-checked against what is actually fetched from the remote before creating the local tracking branch [2](#0-1) .

### Finding Description
`_checkoutPullRequest` is invoked with `prNumber`, `headRepoOwner`, `headCloneUrl`, and `headRefName` taken straight from the PR object (e.g. from `checkoutPullRequest` in the dispatcher or from a deep link) [3](#0-2) . `_findPullRequestBranch` adds/looks up a remote for `headCloneUrl` (which is fully controlled by the PR author's fork), fetches it, and finds the remote branch purely by ref-name match (`remote.name}/${headRefName}`), then creates a local branch `pr/${prNumber}` pointing at whatever commit that ref currently resolves to [4](#0-3) . At no point is the resulting branch tip compared to the `sha` that was recorded for this PR when Desktop last synced pull request metadata (`prsToUpsert.push({..., head: { ref: pr.head.ref, sha: pr.head.sha, ...} })` [1](#0-0) ). The `sha` field is stored but never used as an invariant check during checkout — exactly the same class of bug as the reported issue: a secondary identifier (`sha` / `ending_checkpoint_sequence_num`) is recorded alongside a primary identifier (`ref` / `blob ID`), but only the primary identifier is validated before the value is trusted and acted upon.

### Impact Explanation
Because the PR author (an untrusted, unprivileged attacker with respect to the victim's Desktop instance) fully controls their own fork and can force-push new commits to the PR's head branch at any time between when Desktop fetched the PR metadata (and the user reviewed diffs/CI checks for `pr.head.sha`) and when the user actually clicks "Checkout" in Desktop, `_findPullRequestBranch` will silently pull down whatever commit currently sits at that ref — not the commit the user saw when they decided to check it out. If the user then builds/tests that code, or commits on top of it and pushes, they can unknowingly commit atop and act on attacker-supplied content that never went through the review the user believed they performed. This is "silent corruption of what the user commits/pushes," rooted in a missing cross-check between two values that are supposed to travel together.

### Likelihood Explanation
Any pull request, including ones from unprivileged/anonymous forks, can trigger this path — checking out PRs from forks is a core, expected Desktop feature, and force-pushing to one's own fork branch requires no special privilege. The only user action needed is the normal "Checkout" action on a pull request they already intended to check out (no unnatural steps), making this a realistic TOCTOU condition rather than a contrived scenario.

### Recommendation
When checking out a pull request, compare the SHA of the fetched/existing branch tip against the `head.sha` last recorded for that PR (or re-fetch the PR from the API immediately before checkout) and warn the user (or refuse) if it has changed, mirroring the report's remediation of tracking both identifiers together and validating both, not just one.

### Proof of Concept
1. Attacker opens a PR from a fork against a repository the victim uses in Desktop; PR head is commit A (benign).
2. Victim reviews commit A in Desktop/GitHub and clicks "Checkout" is queued but not yet completed, or victim checks out and later revisits.
3. Attacker force-pushes commit B (malicious) to the exact same branch name before/at the moment `_findPullRequestBranch` fetches the fork remote (`this._fetchRemote(repository, remote, ...)` at [5](#0-4) ).
4. Desktop creates local branch `pr/<N>` pointing at commit B without ever comparing it to the previously recorded `pr.head.sha` for commit A [6](#0-5) .
5. Victim now has commit B checked out believing it is the reviewed PR content, and any local commits/pushes build on top of it.

Note: This finding is based on the code available through search/index; I did not have the ability to run the app or exhaustively trace every UI path (e.g., whether some other layer surfaces a warning on ref-tip mismatch). If such a check exists elsewhere and wasn't surfaced by search, it would mitigate this issue — I could not find one in `app-store.ts`, `dispatcher.ts`, or `pull-request-store.ts`.

### Citations

**File:** app/src/lib/stores/pull-request-store.ts (L305-319)
```typescript
      prsToUpsert.push({
        number: pr.number,
        title: pr.title,
        createdAt: pr.created_at,
        updatedAt: pr.updated_at,
        head: {
          ref: pr.head.ref,
          sha: pr.head.sha,
          repoId: headRepo.dbID,
        },
        base: {
          ref: pr.base.ref,
          sha: pr.base.sha,
          repoId: baseGitHubRepo.dbID,
        },
```

**File:** app/src/lib/stores/app-store.ts (L8633-8721)
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

    if (existingBranch === undefined) {
      this.emitError(
        new Error(
          `Couldn't find branch '${headRefName}' in remote '${remote.name}'. ` +
            `A common reason for this is that the PR author has deleted their ` +
            `branch or their forked repository.`
        )
      )
      return
    }

    // For fork remotes we checkout the ref as pr/[123] instead of using the
    // head ref name since many PRs from forks are created from their default
    // branch so we'll have a very high likelihood of a conflicting local branch
    const isForkRemote =
      remote.name !== gitStore.defaultRemote?.name &&
      remote.name !== gitStore.upstreamRemote?.name

    if (isForkRemote) {
      return await this._createBranch(
        repository,
        `pr/${prNumber}`,
        remoteRef,
        false
      )
    }

    return existingBranch
  }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L2507-2523)
```typescript
  /** Checks out a PR whose ref exists locally or in a forked repo. */
  public async checkoutPullRequest(
    repository: RepositoryWithGitHubRepository,
    pullRequest: PullRequest
  ): Promise<void> {
    if (pullRequest.head.gitHubRepository.cloneURL === null) {
      return
    }

    return this.appStore._checkoutPullRequest(
      repository,
      pullRequest.pullRequestNumber,
      pullRequest.head.gitHubRepository.owner.login,
      pullRequest.head.gitHubRepository.cloneURL,
      pullRequest.head.ref
    )
  }
```
