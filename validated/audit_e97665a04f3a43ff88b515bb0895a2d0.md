Based on the investigation, I did not find a Desktop analog that reproduces the exact bug class from the report (an identity field implicitly-but-insufficiently validated via a signature-verification side effect, enabling a different actor to be blamed). The closest structurally similar pattern I could confirm with direct code evidence is a **missing-SHA-pinning issue in the pull-request checkout flow**, which shares the report's core defect shape: a value that identifies "what/who is being trusted" (`PullRequest.head`) is resolved by a mutable name reference instead of the specific commit that was reviewed, so the content that ends up checked out can silently diverge from what the app told the user it was fetching.

### Title
PR checkout resolves the head branch by name, not by the reviewed `head.sha`, allowing silent substitution of a fork's content - (File: `app/src/lib/stores/app-store.ts`)

### Summary
`AppStore._findPullRequestBranch` and `AppStore._checkoutPullRequest` locate and check out a pull request's source branch strictly by remote name + `headRefName`. Neither function compares the fetched branch tip against `PullRequest.head.sha`, the specific commit the PR metadata (and any UI diff the user reviewed) referred to.

### Finding Description
`_findPullRequestBranch` [1](#0-0)  adds/reuses a remote for `headCloneUrl` (a value taken directly from the GitHub API's `pr.head.repo.clone_url`, which is attacker-controlled since the head repo belongs to whoever opened the PR/fork), fetches it, and resolves `${remote.name}/${headRefName}` to a `Branch`. It never reads or checks `pullRequest.head.sha`. `_checkoutPullRequest` [2](#0-1)  then checks out whatever branch object was returned. The `PullRequest`/`PullRequestRef` model does carry a `sha` field [3](#0-2) , and it is populated when pull requests are synced from the API [4](#0-3) , but that value is only used for storage/comparison purposes elsewhere, not to pin or verify what gets checked out.

This mirrors the report's broken invariant: the value used to establish trust in "what am I about to check out and build on" (the branch name) is a different, more mutable value than the one the user's mental model / prior review was anchored to (`head.sha`). Just as `ProposerIndex` was implicitly, but insufficiently, tied to the correct proposer via a signature check meant for a different purpose (RANDAO), here the checkout path is implicitly, but insufficiently, tied to "the PR reviewed" via a mutable branch name instead of the immutable commit reference the API actually returned.

### Impact Explanation
Because the head repository/branch is attacker-controlled (any user can open a PR from their own fork), the PR author can force-push new commits to the head branch at any time after opening the PR. If a maintainer reviewed the PR diff earlier (or simply trusts the PR number/branch), then later clicks "Checkout this PR" in Desktop, `_findPullRequestBranch` will fetch and check out whatever is currently at the tip of that branch — not the commit that was actually reviewed. This can silently substitute different file contents (build scripts, `.git/hooks`-adjacent config, `.gitattributes` filters, editor/CI configuration, etc.) into the user's working directory under the same PR identity, without any diff/warning that the content changed. This falls into the "silent corruption of what the user commits or pushes" bucket, since any local commits made on top of the checked-out branch, or a subsequent push, will now be built on attacker-updated content rather than the reviewed one.

### Likelihood Explanation
Likelihood is limited by the fact that this requires a time-of-check/time-of-use window: the attacker must force-push between the moment a maintainer forms trust in the PR (e.g., via GitHub's web review or an earlier local checkout) and the moment they check out again in Desktop, and the app must be re-fetching/re-resolving the branch tip. This is a fairly common workflow (checking out a PR to test it locally, sometimes more than once, or after CI reruns), so the primitive is realistic, but it also mirrors a general limitation of "checkout by PR" tools rather than a Desktop-specific coding defect — I could not find any explicit statement in the docs (`docs/technical/pull-requests.md`) [5](#0-4)  that Desktop intends to pin to a specific SHA, so this may be accepted/intended behavior rather than an oversight.

### Recommendation
When checking out a pull request, compare the resolved branch tip SHA against `pullRequest.head.sha` from the most recent API fetch, and surface a warning (similar to the existing branch-protection/repo-rules warnings in `commit-message.tsx`) if the branch has moved since the PR was last synced, requiring explicit user acknowledgment before checkout proceeds.

### Proof of Concept
1. Attacker forks the target repo and opens a PR (`head.repo.clone_url` = attacker's fork, `head.ref` = `feature`).
2. Maintainer reviews the PR diff on github.com and later clicks "Checkout this PR" in Desktop, which calls `dispatcher.checkoutPullRequest` → `AppStore._checkoutPullRequest` → `_findPullRequestBranch` [2](#0-1) .
3. Before the maintainer checks out (or between repeat checkouts), the attacker force-pushes new commits to `feature` in their fork.
4. `_findPullRequestBranch` fetches the fork remote and resolves `remoteName/feature` to whatever is currently there [6](#0-5) , with no comparison to the previously known `pullRequest.head.sha`.
5. Desktop checks out the new, unreviewed commits under the same PR context, and the maintainer may commit/push further work on top of it without any indication the content changed.

**Confidence note:** I was not able to find any existing SHA-pinning or "branch changed" warning logic elsewhere in the codebase for this flow (searches for `headSha`/`expectedSha`/`verifyCommit` in this context returned no hits in the PR-checkout code paths), but I could not rule out that this is treated as expected/by-design behavior for a "checkout latest PR state" feature, so I'd weight this as a plausible but not certain match to the report's bug class rather than a confirmed regression.

### Citations

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

**File:** app/src/lib/stores/pull-request-store.ts (L303-323)
```typescript
      const headRepo = await upsertRepo(endpoint, pr.head.repo)

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
        body: pr.body,
        author: pr.user.login,
        draft: pr.draft ?? false,
      })
```

**File:** docs/technical/pull-requests.md (L1-43)
```markdown
# Checking out pull requests from a forked repository
PR [#3602](https://github.com/desktop/desktop/pull/3602) introduced the ability to checkout a branch from a forked repository. In order to accomplish this, we needed a way to manage remotes on your behalf. This document is intended to detail the process we developed to make checking out PRs as frictionless as possible.

## Removing Remotes
One of the goals of our design was to ensure that we don’t cause your remotes — `.git/refs/remotes` — to grow unbounded. We prevent this by cleaning up after ourselves. We determined that a remote is a candidate for removal when it meets the certain conditions:
* Start with our prefix
* The PR associated with the remote is closed

The implementation of the function that does this work can be found [here](https://github.com/desktop/desktop/blob/34a05b155ff69bb19cc4da5b2caa89856e3e63fb/app/src/lib/stores/pull-request-store.ts#L91-L110).

```ts
forkedRemotesToDelete(
  remotes: ReadonlyArray<IRemote>,
  openPullRequests: ReadonlyArray<PullRequest>
): ReadonlyArray<IRemote> {
    const forkedRemotes = remotes.filter(remote =>
      remote.name.startsWith(ForkedRemotePrefix)
    )
    const remotesOfPullRequests = new Set<string>()
    openPullRequests.forEach(openPullRequest => {
      const { gitHubRepository } = openPullRequest.head
      if (gitHubRepository != null && gitHubRepository.cloneURL != null) {
        remotesOfPullRequests.add(gitHubRepository.cloneURL)
      }
    })
    const forkedRemotesToDelete = forkedRemotes.filter(
      forkedRemote => !remotesOfPullRequests.has(forkedRemote.url)
    )

    return forkedRemotesToDelete
}
```

## Magic Remote Prefix
One of the main problems we needed to solve was determining which remotes are no longer needed and can be cleaned. We decided to prefix the remotes we add on your behalf with a magic string: `github-desktop-`

```ts
export const ForkedRemotePrefix = 'github-desktop-'
```
[Code](https://github.com/desktop/desktop/blob/34a05b155ff69bb19cc4da5b2caa89856e3e63fb/app/src/lib/stores/pull-request-store.ts#L26)

## What does this mean for me?
Doing this essentially gives us a namespace that we can safely work in. We chose the prefix `github-desktop-` because we are confident that your own remote names will never start with this prefix. This means that in order for GitHub Desktop to work as expected, you should never add a remote that starts with our prefix. We feel that this is an acceptable compromise.
```
