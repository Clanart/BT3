### Title
Alive event repository matching ignores `endpoint`, allowing cross-host repo confusion - (`app/src/lib/stores/notifications-store.ts`)

### Summary
`isValidRepositoryForEvent` in `notifications-store.ts` only compares `event.owner`/`event.repo` against `gitHubRepository.owner.login`/`gitHubRepository.name`, never checking `gitHubRepository.endpoint` (or any repository identifier like a numeric GitHub ID). This confirms the reported behavior.

### Finding Description
The function is implemented as: [1](#0-0) 

Both branches (the fork/parent branch and the default branch) compare only `owner.login` and `name` string fields between the locally-tracked `GitHubRepository` and the incoming `DesktopAliveEvent`. There is no comparison of `gitHubRepository.endpoint`, and no comparison of any stable repository ID. This function is used by `isRecentRepositoryEvent`: [2](#0-1) 

and (per the grep results) by the handlers for checks-failed, PR-review-submit and PR-comment events elsewhere in the same file, gating whether the event is treated as belonging to the user's tracked repository and used to drive follow-up actions such as fetching the pull request and eventually `dispatcher.checkoutPullRequest`.

Because `owner`/`repo` are plain strings and no endpoint/host or ID check is present, a repository named e.g. `owner/repo` on `github.com` cannot be distinguished from an identically-named `owner/repo` on a GitHub Enterprise Server instance (or vice versa) purely by this function's logic.

### Impact Explanation
If an attacker could get such a mismatched-endpoint event delivered and accepted, the notification/callback flow could operate on the wrong `RepositoryWithGitHubRepository`, potentially leading the user to fetch/checkout a pull request ref that the attacker controls on a different host but with a same-named repo, i.e., `dispatcher.checkoutPullRequest` could act on attacker-influenced data associated with the wrong repository context.

### Likelihood Explanation
This is **not independently exploitable by an unprivileged external attacker** under the stated scope. Alive events are delivered via GitHub's Alive/push-notification service, which is authenticated per signed-in account/session; a remote attacker with no privileged access has no direct mechanism to inject arbitrary `DesktopAliveEvent` payloads into a victim's Desktop client. The scenario requires either compromising GitHub's Alive delivery infrastructure or an already-authenticated account being tricked into interacting with a same-named repo across `github.com` and a GHES instance the user also uses — a fairly narrow, low-likelihood condition, and I could not verify from the available code whether `DesktopAliveEvent`/`alive-store.ts` already carries or checks an endpoint elsewhere in the delivery pipeline (I was unable to inspect `alive-store.ts` before running out of tool calls, so it's uncertain whether upstream code scopes the event to a specific endpoint/account before this function is reached).

### Recommendation
Add an explicit endpoint (and ideally repository ID) comparison in `isValidRepositoryForEvent`, e.g. compare `gitHubRepository.endpoint === event.endpoint` in addition to owner/name, to eliminate any possibility of cross-host repository confusion regardless of upstream guarantees.

### Proof of Concept
Given the code above, calling `isValidRepositoryForEvent(repo, event)` where `repo.gitHubRepository = { owner: { login: 'owner' }, name: 'repo', endpoint: 'https://github.com' }` and `event = { owner: 'owner', repo: 'repo', endpoint: 'https://ghes.example.com', ... }` returns `true`, since `endpoint` is never inspected — confirming the described gap in the matching logic. Whether this is reachable by an unprivileged remote attacker depends on upstream Alive event delivery/authentication, which was not verifiable within the available context.

### Citations

**File:** app/src/lib/stores/notifications-store.ts (L434-456)
```typescript
  private isValidRepositoryForEvent(
    repository: RepositoryWithGitHubRepository,
    event: DesktopAliveEvent
  ) {
    // If it's a fork and set to contribute to the parent repository, try to
    // match the parent repository.
    if (
      isRepositoryWithForkedGitHubRepository(repository) &&
      getForkContributionTarget(repository) === ForkContributionTarget.Parent
    ) {
      const parentRepository = repository.gitHubRepository.parent
      return (
        parentRepository.owner.login === event.owner &&
        parentRepository.name === event.repo
      )
    }

    const ghRepository = repository.gitHubRepository
    return (
      ghRepository.owner.login === event.owner &&
      ghRepository.name === event.repo
    )
  }
```

**File:** app/src/lib/stores/notifications-store.ts (L458-464)
```typescript
  private isRecentRepositoryEvent(event: DesktopAliveEvent) {
    return this.recentRepositories.some(
      r =>
        isRepositoryWithGitHubRepository(r) &&
        this.isValidRepositoryForEvent(r, event)
    )
  }
```
