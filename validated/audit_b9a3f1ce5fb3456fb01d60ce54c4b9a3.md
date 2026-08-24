### Title
Deep-link "Open Pull Request" handler silently checks out an attacker's fork branch into an existing repository with no confirmation - (File: app/src/ui/dispatcher/dispatcher.ts)

### Summary
The external report describes a case where an owner-gated action (`reduceWeight`) trusts a value (`marketcap`) that can be manipulated by an outside party right before the privileged call executes, letting an attacker influence the outcome of a check the victim believed was safe. The GitHub Desktop analog is the `x-github-client://openRepo` deep-link flow: clicking an attacker-crafted link causes Desktop to resolve a pull request via the GitHub API and then automatically add a remote for, fetch, and check out the PR author's fork branch into whichever of the user's *existing* local repositories matches the URL — without any "are you sure" prompt specific to checking out untrusted code, unlike the manual "click on a PR in the list" flow.

### Finding Description
The `x-github-client://openRepo?url=...&pr=NNN` URL is parsed by `parseAppURL` in [1](#0-0)  and dispatched to `openRepositoryFromUrl`, which for a `pr` parameter calls `openPullRequestFromUrl`: [2](#0-1) 

This function fetches the pull request from the GitHub API (`fetchPullRequest`), locates a matching *already-open* local repository purely by comparing the origin/upstream remote URL string to the PR's `head`/`base` clone URLs via `getRepositoryFromPullRequest` / `doesRepositoryMatchUrl`: [3](#0-2) 

and then unconditionally calls `_checkoutPullRequest`, which adds the PR head repo as a new remote (`github-desktop-<owner>`) and fetches/checks out its branch: [4](#0-3) 

The head repository/URL/ref values passed into this chain (`pullRequest.head.repo.clone_url`, `.owner.login`, `.ref`) originate from the PR object itself — i.e., they describe *the attacker's own fork*, since anyone can open a PR against a public repo. The victim only needs to have that repository already added in Desktop and click a link (e.g. embedded in an issue comment, README, or external site) referencing `pr=<attacker's PR number>`.

The actual checkout goes through `_checkoutBranch`, whose only safety gate is for *uncommitted local changes* (stash/overwrite dialogs): [5](#0-4) 

If the working directory is clean — the common case right after opening Desktop or finishing other work — none of these dialogs fire, and the branch switch proceeds silently with no message indicating the content now checked out comes from an untrusted fork.

### Impact Explanation
This lets an unprivileged attacker (anyone who can open a PR/fork against a public repo the victim has cloned) cause Desktop to silently replace the working directory contents of the victim's existing local repository with the attacker's own branch, with only a single link click and no in-app warning that the content is foreign. This matches the "silent corruption of what the user commits or pushes" category: if the user, believing they are still on their own branch/work, edits files, commits, or pushes, they may unknowingly build on top of or publish attacker-controlled content, or an editor/build tool invoked afterwards (Desktop's "Open in External Editor"/"Open Command Prompt" actions trust the repository path implicitly) may execute attacker-supplied build scripts. This is strictly weaker than a guaranteed RCE but represents the same "trusted context corrupted by attacker-controlled remote data at the moment before a privileged/consequential operation" class as the smart-contract report.

### Likelihood Explanation
Requires the victim to click a single external `x-github-client://openRepo?...&pr=N` link while already having the target repository added in Desktop, and to have no uncommitted changes at the time (a common state). No local access, no leaked credentials, and no unusual/multi-step user action are required beyond a normal link click — consistent with the "Valid Impact" criteria (attacker controls a GitHub API object / a link the user clicks). The regex validation in `parseAppURL` (`/^\d+$/` for `pr`, `/^pr\/\d+$/` for `branch`) only validates format, not trust/provenance of the referenced content.

### Recommendation
Before automatically checking out a PR branch reached via the deep-link flow (as opposed to the user explicitly clicking a PR entry in Desktop's own PR list), surface an explicit confirmation dialog identifying the fork/owner and branch about to be checked out, similar to the existing `StashAndSwitchBranch`/`ConfirmCheckoutCommit` popups, so the user can verify legitimacy before working-directory contents change. Consider also gating deep-link driven remote/fork checkouts behind the same "review changes before switching" UX already used for local uncommitted-changes conflicts.

### Proof of Concept
1. Attacker forks a public repository the victim has already cloned with Desktop (e.g. `origin` remote pointing at `github.com/victim-org/project`).
2. Attacker opens a pull request from their fork against that repository, with commits of their choosing.
3. Attacker sends the victim a link: `x-github-client://openRepo/https://github.com/victim-org/project?pr=<PR_NUMBER>`.
4. Victim, who has the repository open in Desktop with no local uncommitted changes, clicks the link.
5. Desktop's `openPullRequestFromUrl` → `getRepositoryFromPullRequest` matches the existing local repo by origin URL, then `_checkoutPullRequest`/`_findPullRequestBranch` silently adds the attacker's fork as a remote, fetches it, and checks out `pr/<PR_NUMBER>` in the victim's working directory — with no warning dialog since the working tree was clean.

Note: I was unable to fully trace end-to-end whether an OS-level protocol handler registration additionally restricts which processes can invoke `x-github-client://` links (e.g. requiring the app already be running vs. cold start) since that registration logic lives in native/OS install manifests not indexed here; a Devin session with full repo/filesystem access would be needed to confirm the exact protocol-handler registration and any existing UI mitigations that may have been added after this snapshot.

### Citations

**File:** app/src/lib/parse-app-url.ts (L98-125)
```typescript
  if (actionName === 'openrepo') {
    const pr = getQueryStringValue(query, 'pr')
    const branch = getQueryStringValue(query, 'branch')
    const filepath = getQueryStringValue(query, 'filepath')

    if (pr != null) {
      if (!/^\d+$/.test(pr)) {
        return unknown
      }

      // we also expect the branch for a forked PR to be a given ref format
      if (branch != null && !/^pr\/\d+$/.test(branch)) {
        return unknown
      }
    }

    if (branch != null && testForInvalidChars(branch)) {
      return unknown
    }

    return {
      name: 'open-repository-from-url',
      url: parsedPath,
      branch,
      pr,
      filepath,
    }
  }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1890-1938)
```typescript
  private getRepositoryFromPullRequest(
    pullRequest: IAPIPullRequest
  ): RepositoryWithGitHubRepository | null {
    const state = this.appStore.getState()
    const repositories = state.repositories
    const headUrl = pullRequest.head.repo?.clone_url
    const baseUrl = pullRequest.base.repo?.clone_url

    // This likely means that the base repository has been deleted
    // and we don't support checking out from refs/pulls/NNN/head
    // yet so we'll bail for now.
    if (headUrl === undefined || baseUrl === undefined) {
      return null
    }

    for (const repository of repositories) {
      if (this.doesRepositoryMatchUrl(repository, headUrl)) {
        return repository
      }
    }

    for (const repository of repositories) {
      if (this.doesRepositoryMatchUrl(repository, baseUrl)) {
        return repository
      }
    }

    return null
  }

  private doesRepositoryMatchUrl(
    repo: Repository | CloningRepository,
    url: string
  ): repo is RepositoryWithGitHubRepository {
    if (repo instanceof Repository && isRepositoryWithGitHubRepository(repo)) {
      const originRepoUrl = repo.gitHubRepository.htmlURL
      const upstreamRepoUrl = repo.gitHubRepository.parent?.htmlURL ?? null

      if (originRepoUrl !== null && urlsMatch(originRepoUrl, url)) {
        return true
      }

      if (upstreamRepoUrl !== null && urlsMatch(upstreamRepoUrl, url)) {
        return true
      }
    }

    return false
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

**File:** app/src/lib/stores/app-store.ts (L4602-4643)
```typescript
    let strategy = explicitStrategy ?? this.uncommittedChangesStrategy

    // The user hasn't been presented with an explicit choice
    if (explicitStrategy === undefined) {
      // Even if the user has chosen to "always stash on current branch" in
      // preferences we still want to let them know changes might be lost
      if (strategy === UncommittedChangesStrategy.StashOnCurrentBranch) {
        if (hasChanges && stashEntry !== null) {
          const type = PopupType.ConfirmOverwriteStash
          this._showPopup({ type, repository, branchToCheckout: branch })
          return repository
        }
      }
    }

    // Always move changes to new branch if we're on a detached head, unborn
    // branch, or a protected branch.
    if (tip.kind !== TipState.Valid || currentBranchProtected) {
      strategy = UncommittedChangesStrategy.MoveToNewBranch
    }

    if (strategy === UncommittedChangesStrategy.AskForConfirmation) {
      if (hasChanges) {
        const type = PopupType.StashAndSwitchBranch
        this._showPopup({ type, branchToCheckout: branch, repository })
        return repository
      }
    }

    return this.withRefreshedGitHubRepository(repository, repository => {
      // We always want to end with refreshing the repository regardless of
      // whether the checkout succeeded or not in order to present the most
      // up-to-date information to the user.
      return this.checkoutImplementation(repository, branch, strategy)
        .then(() => this.onSuccessfulCheckout(repository, branch))
        .catch(async e => {
          this.emitError(new CheckoutError(e, repository, branch))
        })
        .then(() => this.refreshAfterCheckout(repository, branch.name))
        .finally(() => this.updateCheckoutProgress(repository, null))
    })
  }
```

**File:** app/src/lib/stores/app-store.ts (L8613-8721)
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
