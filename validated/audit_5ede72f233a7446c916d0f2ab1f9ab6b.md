## Analysis

The Nouns bug's broken invariant is: **a trust decision (a vote) is bound only to a mutable identifier (the proposal ID), not to the content that was actually reviewed (description/transactions), so the content can change out from under an inattentive party between review and consequence.**

The closest analog in GitHub Desktop is the pull-request-notification "switch to pull request" flow, where the user's decision to act is bound to a PR *number*, but Desktop resolves and checks out the *live* branch content rather than the content that triggered the notification, with no re-confirmation step.

### Title
Notification-driven PR checkout resolves the head branch by name instead of the reviewed SHA, letting a fork owner swap code after a "checks failed"/"review submitted" notification is shown - ([File: app/src/lib/stores/app-store.ts])

### Summary
`Dispatcher.checkoutPullRequest` / `AppStore._checkoutPullRequest` / `AppStore._findPullRequestBranch` check out a pull request's head branch by re-resolving `remote/<headRefName>` at click time. The `PullRequest`/`PullRequestRef` model does carry a cached `sha` [1](#0-0) , but neither `_checkoutPullRequest` nor `_findPullRequestBranch` accept or verify that SHA — they operate purely on `headRefName` [2](#0-1) . The two callers of this path are notification-reaction dialogs: `PullRequestChecksFailed`'s "Switch to Pull Request" button [3](#0-2)  and `PullRequestReview`'s equivalent button, both invoked in direct response to a Desktop notification about a specific past event on the PR (a review submission or a check-run failure at a specific `commit_sha`) [4](#0-3) . The notification handlers themselves look up the PR only by `pull_request_number` from a locally cached list [5](#0-4) .

### Finding Description
- Desktop's alive/notification events for PRs (`pr-checks-failed`, `pr-review-submit`) are keyed only by `pull_request_number` and are resolved against the locally cached `PullRequest` fetched earlier [5](#0-4) .
- When the user clicks the resulting dialog's "Switch to Pull Request" action, `dispatcher.checkoutPullRequest(repository, pullRequest)` is called, passing along `pullRequest.head.gitHubRepository.cloneURL` and `pullRequest.head.ref` (a branch **name**, not the associated `sha`) [6](#0-5) .
- `_findPullRequestBranch` then fetches the remote (owned by the PR author, potentially a fork attacker controls) and resolves whatever the *current* tip of `remote/<headRefName>` is, fetching it fresh if not already known [7](#0-6) . There is no comparison to the `sha` that was associated with the review/checks-failed event that triggered the notification the user is reacting to.
- Because the identity check is "does this remote/branch name match," not "does this commit match what was reviewed/notified," an attacker who controls the PR's source repository (a fork) can force-push new, different commits to that same branch between the time the notification fires (based on an old SHA) and the time the user clicks through, and Desktop will silently check out the new tip.

### Impact Explanation
This breaks the same invariant as the Nouns finding: the user's trust decision ("I want to look at/act on the PR that had this review/these failing checks") is bound to a mutable identifier (branch name / PR number) rather than to the content (SHA) that justified the decision. A malicious or compromised fork owner can use this gap to get a maintainer to check out attacker-controlled code that was never the code referenced by the notification. Since checkout replaces the working directory and sets the base for any subsequent local commits, if the maintainer builds/opens the project or continues committing on top, this can lead to code execution via build scripts/hooks or to those maintainer commits being built on unreviewed attacker content — a "silent corruption of what the user commits/pushes" as called out in the impact criteria.

### Likelihood Explanation
Requires only that the attacker control a fork used as a PR head branch (an ordinary contributor capability) and time a force-push to land after a check-run/review notification fires but before the maintainer clicks through — a race that is entirely under attacker control since they choose when to push. It requires no local access, no leaked credentials, and no unnatural user steps: clicking "Switch to Pull Request" from a legitimate Desktop notification is exactly the intended, expected workflow.

### Recommendation
Thread the `sha` already present on `PullRequestRef`/the notification's `commit_sha` through `checkoutPullRequest` → `_checkoutPullRequest` → `_findPullRequestBranch`, and after fetching/resolving the branch, compare the resolved tip SHA to the SHA associated with the triggering event. If they differ, warn the user ("This pull request has new commits since this notification was generated — do you want to proceed?") instead of silently checking out the new tip, analogous to binding the vote to content rather than only to the proposal identifier in the original report.

### Proof of Concept
1. Attacker opens PR #1 from fork `attacker/repo` branch `feature`, with benign commit `A`.
2. Maintainer has PR #1 open in Desktop; a reviewer approves it, and/or CI fails, generating a Desktop Alive notification carrying `pull_request_number: 1` (and, for checks, `commit_sha: A`).
3. Immediately after the notification is generated but before the maintainer clicks it, attacker force-pushes commit `B` (with a malicious build script or altered source) to `attacker/repo:feature`.
4. Maintainer clicks "Switch to Pull Request" in the notification dialog. `checkoutPullRequest` resolves `github-desktop-attacker/feature` fresh from the remote, which is now `B`, and checks it out with no indication that the branch changed since the review/check event that prompted the click [8](#0-7) .
5. Maintainer, believing they are looking at the previously-reviewed/CI-checked code `A`, is now on commit `B` and may build, run, or commit atop it.

### Citations

**File:** app/src/models/pull-request.ts (L8-20)
```typescript
export class PullRequestRef {
  /**
   * @param ref The name of the ref.
   * @param sha The SHA of the ref.
   * @param gitHubRepository The GitHub repository in which this ref lives. It could be null if the
   *                         repository was deleted after the PR was opened.
   */
  public constructor(
    public readonly ref: string,
    public readonly sha: string,
    public readonly gitHubRepository: GitHubRepository
  ) {}
}
```

**File:** app/src/lib/stores/app-store.ts (L8613-8631)
```typescript
  public async _checkoutPullRequest(
    repository: RepositoryWithGitHubRepository,
    prNumber: number,
    headRepoOwner: string,
    headCloneUrl: string,
    headRefName: string
  ): Promise<void> {
    const prBranch = await this._findPullRequestBranch(
      repository,
      prNumber,
      headRepoOwner,
      headCloneUrl,
      headRefName
    )
    if (prBranch !== undefined) {
      await this._checkoutBranch(repository, prBranch)
      this.statsStore.increment('prBranchCheckouts')
    }
  }
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

**File:** app/src/ui/notifications/pull-request-checks-failed.tsx (L392-406)
```typescript
  private onSubmit = async (event: React.MouseEvent<HTMLButtonElement>) => {
    event.preventDefault()
    const { dispatcher, repository, pullRequest } = this.props

    this.props.dispatcher.incrementMetric(
      'checksFailedDialogSwitchToPullRequestCount'
    )

    this.setState({ switchingToPullRequest: true })
    await dispatcher.selectRepository(repository)
    await dispatcher.checkoutPullRequest(repository, pullRequest)
    this.setState({ switchingToPullRequest: false })

    this.props.onDismissed()
  }
```

**File:** app/src/ui/notifications/pull-request-review.tsx (L140-165)
```typescript
  private onSubmit = async (event: React.MouseEvent<HTMLButtonElement>) => {
    event.preventDefault()

    const {
      dispatcher,
      repository,
      pullRequest,
      shouldChangeRepository,
      shouldCheckoutBranch,
      review,
    } = this.props

    const isApprovedReview = review.state === 'APPROVED'

    // Only switch to the PR when needed, if it's not an approved review
    if (!isApprovedReview && (shouldChangeRepository || shouldCheckoutBranch)) {
      this.setState({ switchingToPullRequest: true })
      await dispatcher.selectRepository(repository)
      await dispatcher.checkoutPullRequest(repository, pullRequest)
      this.setState({ switchingToPullRequest: false })

      dispatcher.recordPullRequestReviewDialogSwitchToPullRequest(review.state)
    }

    this.props.onDismissed()
  }
```

**File:** app/src/lib/stores/notifications-store.ts (L235-246)
```typescript
    const pullRequests = await this.pullRequestCoordinator.getAllPullRequests(
      repository
    )
    const pullRequest = pullRequests.find(
      pr => pr.pullRequestNumber === event.pull_request_number
    )

    // If the PR is not in cache, it probably means the user didn't work on it
    // from Desktop, so we can maybe ignore it?
    if (pullRequest === undefined) {
      return
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
