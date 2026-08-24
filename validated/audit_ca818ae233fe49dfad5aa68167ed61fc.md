### Title
Unconfirmed silent checkout of attacker-controlled PR branch via `x-github-client://openrepo` deep link - (File: `app/src/ui/dispatcher/dispatcher.ts`)

### Summary
GitHub Desktop registers custom URL protocols (`x-github-client`, `github-mac`, etc.) that any web page, email, or chat message can trigger with an `openRepo` action [1](#0-0) . When the URL includes a `pr` query parameter, Desktop parses it with no ownership/trust check [2](#0-1) , fetches the PR from the GitHub API for the attacker-chosen `url`, matches it against the user's already-open repositories purely by hostname/owner/name equality, and — if a match is found — automatically adds a remote for the PR's fork and checks out the fork's branch with **no confirmation dialog at all**, unlike the equivalent in-app "Checkout PR" button flow.

### Finding Description
The dispatch chain is:
1. `parseAppURL` extracts `url`, `pr`, `branch`, `filepath` straight from the untrusted deep link, only validating that `pr` is numeric and `branch` matches a ref-name regex [2](#0-1) .
2. `dispatchURLAction` routes `open-repository-from-url` actions to `openRepositoryFromUrl` [3](#0-2) .
3. Because `pr` is set, `openPullRequestFromUrl` is invoked: it calls `fetchPullRequest(url, pr)` against the GitHub API for whatever `url` the attacker supplied, then tries to match the PR's head/base clone URL against the *user's currently open repositories* using only hostname+owner+repo-name comparison (`urlsMatch`/`doesRepositoryMatchUrl`) [4](#0-3) .
4. If a match is found, the matching repository is silently selected and `_checkoutPullRequest` is called immediately — no popup, no "do you want to check out PR #N from fork X?" prompt [5](#0-4) .
5. `_findPullRequestBranch`/`_checkoutPullRequest` in `app-store.ts` will add a new git remote for the fork's `clone_url` (attacker-controlled fork, sourced from the API response) if none exists, fetch it, and check out `pr/<number>` as a local branch [6](#0-5) .
6. The subsequent `_checkoutBranch` call only shows a confirmation dialog when the working directory has *uncommitted changes*; on a clean tree it silently overwrites the working directory contents with the attacker's fork/branch content and switches HEAD [7](#0-6) .

Unlike the in-app "Checkout this PR" button (an explicit, deliberate user action performed while already reviewing a specific PR in Desktop's UI), this path is reachable purely by getting the victim to click a link — no interaction with Desktop's PR list, no visible confirmation that a branch switch/fork remote addition is about to happen.

### Impact Explanation
A victim who has a target repository already open in Desktop can be lured (e.g., via a chat message, email, or malicious webpage) into clicking a crafted `x-github-client://openrepo/<repo-url>?pr=<attacker-PR-number>` link. If the repo/owner/name matches one of the user's open repositories, Desktop will silently:
- Add a new remote pointing at the attacker's fork.
- Fetch and check out the attacker's branch content into the working directory of the user's already-existing, real local clone, with no dialog explaining what happened.

This corrupts the state of what the user believes they are working on: if the developer is unaware that HEAD moved to `pr/<n>`, subsequent edits/commits/pushes may be built on top of attacker-supplied code, or the developer may inadvertently open/execute attacker-controlled files (build scripts, editor task configs) that get materialized on disk without their knowledge — matching the "silent corruption of what the user commits or pushes" and "attacker controls ... a git remote ... or the result is code execution" impact classes.

### Likelihood Explanation
This requires no local access, no leaked credentials, and no admin rights — only that the victim click a link while having a matching repository already open in Desktop, and that the current working tree be clean (which is the common case for a developer who just finished a commit/push). The custom protocol handlers are registered by default on all Desktop installs [1](#0-0) , and macOS additionally routes these via `app.on('open-url', ...)` without any allow-list check on which repos may be operated on [8](#0-7) .

### Recommendation
Require explicit user confirmation before adding a remote and checking out a PR branch that was triggered via a URL/protocol-handler action (as opposed to an explicit UI "Checkout PR" click), showing the fork owner/URL and target branch prior to fetch/checkout. Additionally, avoid silently reusing/selecting an already-open repository based solely on loose owner/name/hostname matching of attacker-supplied `url`/API data.

### Proof of Concept
1. Victim has `github.com/acme/webapp` open in Desktop with a clean working tree.
2. Attacker opens a PR (or any PR already exists) against `acme/webapp` from a fork they control, containing a modified `package.json`/build script or altered source file.
3. Attacker sends the victim a link:
   `x-github-client://openrepo/https://github.com/acme/webapp?pr=<PR_NUMBER>`
4. Victim clicks the link. Desktop's `open-url`/protocol-launcher handler invokes `handleAppURL` → `parseAppURL` → `dispatchURLAction` → `openPullRequestFromUrl` [9](#0-8) [10](#0-9) .
5. Because the repo URL matches the victim's already-open `acme/webapp`, Desktop silently selects that repository, adds a `github-desktop-<fork-owner>` remote, fetches, and checks out `pr/<PR_NUMBER>` [6](#0-5) .
6. Since there are no uncommitted changes, `_checkoutBranch` proceeds without any confirmation dialog [11](#0-10) , and the victim's working directory now silently contains the attacker's fork content.

### Citations

**File:** app/src/main-process/main.ts (L105-116)
```typescript
const possibleProtocols = new Set(['x-github-client'])
if (__DEV_SECRETS__) {
  possibleProtocols.add('x-github-desktop-dev-auth')
} else {
  possibleProtocols.add('x-github-desktop-auth')
}
// Also support Desktop Classic's protocols.
if (__DARWIN__) {
  possibleProtocols.add('github-mac')
} else if (__WIN32__) {
  possibleProtocols.add('github-windows')
}
```

**File:** app/src/main-process/main.ts (L204-210)
```typescript
app.on('will-finish-launching', () => {
  // macOS only
  app.on('open-url', (event, url) => {
    event.preventDefault()
    handleAppURL(url)
  })
})
```

**File:** app/src/lib/parse-app-url.ts (L98-124)
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
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1920-2048)
```typescript
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

  private async openRepositoryFromUrl(action: IOpenRepositoryFromURLAction) {
    const { url, pr, branch, filepath } = action

    let repository: Repository | null

    if (pr !== null) {
      repository = await this.openPullRequestFromUrl(url, pr)
    } else if (branch !== null) {
      repository = await this.openBranchNameFromUrl(url, branch)
    } else {
      repository = await this.openOrCloneRepository(url)
    }

    if (repository === null) {
      return
    }

    if (filepath !== null) {
      if (isAbsolute(filepath)) {
        log.error(`Refusing to open absolute path: ${filepath}`)
        return
      }

      const resolved = await resolveWithin(repository.path, filepath)

      if (resolved !== null) {
        shell.showItemInFolder(resolved)
      } else {
        log.error(
          `Prevented attempt to open path outside of the repository root: ${filepath}`
        )
      }
    }
  }

  private async openBranchNameFromUrl(
    url: string,
    branchName: string
  ): Promise<Repository | null> {
    const repository = await this.openOrCloneRepository(url)

    if (repository === null) {
      return null
    }

    // ensure a fresh clone repository has it's in-memory state
    // up-to-date before performing the "Clone in Desktop" steps
    await this.appStore._refreshRepository(repository)

    // if the repo has a remote, fetch before switching branches to ensure
    // the checkout will be successful. This operation could be a no-op.
    await this.appStore._fetch(repository, FetchType.UserInitiatedTask)

    await this.checkoutLocalBranch(repository, branchName)

    return repository
  }

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L2118-2120)
```typescript
      case 'open-repository-from-url':
        this.openRepositoryFromUrl(action)
        break
```

**File:** app/src/lib/stores/app-store.ts (L4578-4643)
```typescript
  public async _checkoutBranch(
    repository: Repository,
    branch: Branch,
    explicitStrategy?: UncommittedChangesStrategy
  ): Promise<Repository> {
    const repositoryState = this.repositoryStateCache.get(repository)
    const { changesState, branchesState } = repositoryState
    const { currentBranchProtected, stashEntry } = changesState
    const { tip } = branchesState
    const hasChanges = changesState.workingDirectory.files.length > 0

    // No point in checking out the currently checked out branch.
    if (tip.kind === TipState.Valid && tip.branch.name === branch.name) {
      return repository
    }

    // If the branch is checked out in another worktree, switch to that worktree
    // instead of checking out the branch in the current worktree.
    const wt = repositoryState.worktrees.find(wt => wt.branch === branch.ref)

    if (wt) {
      return this._switchWorktree(repository, wt)
    }

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
