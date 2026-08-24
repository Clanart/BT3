Based on my investigation, I found a real GitHub Desktop analog to the "missing consent prompt for state-changing action" bug class: the `x-github-client://openrepo/...?pr=N` deep-link flow silently adds a new git remote and checks out a fork-controlled branch without ever prompting the user, even though the equivalent UI-initiated "checkout PR" action goes through the same code path with no additional gate either — but crucially the *deep-link-triggered remote add* is entirely attacker-steerable content (the fork owner/URL come straight from the GitHub API response for the PR) reaching directly into `addRemote`.

### Title
Deep-link "Open PR in Desktop" flow adds an attacker-controlled git remote and checks out a fork branch without any user confirmation - ([File: app/src/ui/dispatcher/dispatcher.ts])

### Summary
The `x-github-client://openrepo/<url>?pr=<num>` protocol handler resolves a pull request via the GitHub API and, if a matching local repository already exists, immediately adds a new remote pointing at the PR's head fork and checks it out — with zero confirmation dialog, unlike essentially every other repository-mutating action in Desktop (remove repository, delete worktree, force push, discard changes, etc.), which all have dedicated "Are you sure?" dialogs.

### Finding Description
`parseAppURL` recognizes the `openrepo` action and extracts an optional `pr` query parameter [1](#0-0) . This is dispatched to `openRepositoryFromUrl`, which for the PR case calls `openPullRequestFromUrl` [2](#0-1) .

`openPullRequestFromUrl` fetches the pull request object from the API (`fetchPullRequest`), and — if a matching repository is already present in Desktop — immediately calls `_checkoutPullRequest` with the PR's `head.repo.clone_url` and `head.ref`, values taken directly from the API response, with no dialog shown to the user at any point in this path: [3](#0-2) .

`_checkoutPullRequest` → `_findPullRequestBranch` then adds a brand-new remote (`addRemote(repository, forkRemoteName, headCloneUrl)`) whenever the head clone URL doesn't match an existing remote, fetches it, creates a local `pr/<number>` branch tracking it, and checks it out — again, no confirmation step exists in this call chain: [4](#0-3) .

By contrast, `_checkoutBranch` (used for ordinary branch switches) *does* have a mandatory confirmation gate for uncommitted-changes handling [5](#0-4) , and the rest of the app enforces confirmation prompts for essentially every other destructive/state-changing action (repository removal, worktree deletion, force push, discard changes) via dedicated dialog components [6](#0-5) [7](#0-6) [8](#0-7) . The PR-checkout-from-URL path has no analogous "do you want to add remote `github-desktop-<owner>` and check out this branch?" prompt — the deep link alone is sufficient to trigger it.

### Impact Explanation
An attacker who controls (or can influence) a pull request's head repository/branch metadata — or who simply crafts a link pointing `pr=` at an arbitrary PR number against a repository the victim already has cloned in Desktop — can cause Desktop to silently: (1) register a new remote under the attacker's control, and (2) check out that remote's branch into the user's working tree, all triggered purely by the user clicking a link (browser "Open in Desktop" button or any deep link). Because Desktop's own guard against overwriting uncommitted work (`_checkoutBranch`'s `AskForConfirmation` branch) only fires when there are dirty working-directory changes, a clean checkout proceeds with no prompt whatsoever, meaning a link click alone can move HEAD to attacker-authored content without any explicit "yes, do this" step from the user — the same broken invariant flagged in the report (state-changing action skipping the consent step that its sibling operation enforces).

### Likelihood Explanation
The `pr=` deep-link parameter is validated only for numeric format (`/^\d+$/`) [9](#0-8) , and the head repo/branch values are trusted directly from the API response tied to that PR number. Any repository the victim has already opened in Desktop is reachable via `doesRepositoryMatchUrl` matching on origin/upstream HTML URL, so an attacker only needs to know (or guess) a PR number on a repo the victim has cloned, and get them to click a `x-github-client://openrepo/...?pr=N` link (or the equivalent GitHub.com "Open in Desktop" button, which is directly reachable by anyone who can open a PR against a public repo).

### Recommendation
Introduce an explicit confirmation dialog before `openPullRequestFromUrl`/`_checkoutPullRequest` adds a new remote or checks out a branch when triggered via the URL/CLI-deep-link path (mirroring the existing `askForConfirmationOn*` settings pattern used elsewhere in the app), especially when the operation would add a previously-unknown remote pointing at a fork. At minimum, surface the fork owner/URL to the user and require acknowledgment before Desktop registers the remote and switches HEAD.

### Proof of Concept
1. Victim has a repository (e.g. `github.com/org/app`) already added in GitHub Desktop.
2. Attacker opens a pull request against `org/app` from a malicious fork/branch, or otherwise obtains a PR number.
3. Attacker sends the victim a link: `x-github-client://openrepo/https://github.com/org/app?pr=<N>` (or the GitHub.com "Open this in GitHub Desktop" button on that PR).
4. Victim clicks it; Desktop resolves the PR via the API, finds the existing local repo, calls `_checkoutPullRequest`, which adds remote `github-desktop-<forkOwner>` pointing at the fork's `clone_url`, fetches it, creates/checks out local branch `pr/<N>` — with no dialog shown at any point in this chain [10](#0-9) [11](#0-10) .

### Citations

**File:** app/src/lib/parse-app-url.ts (L98-112)
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
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1951)
```typescript
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

**File:** app/src/lib/stores/app-store.ts (L4602-4629)
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

**File:** app/src/ui/remove-repository/confirm-remove-repository.tsx (L52-96)
```typescript
  public render() {
    const isRemovingRepository = this.state.isRemovingRepository

    return (
      <Dialog
        id="confirm-remove-repository"
        key="remove-repository-confirmation"
        type="warning"
        title={__DARWIN__ ? 'Remove Repository' : 'Remove repository'}
        dismissDisabled={isRemovingRepository}
        loading={isRemovingRepository}
        disabled={isRemovingRepository}
        onDismissed={this.props.onDismissed}
        onSubmit={this.onSubmit}
      >
        <DialogContent>
          <p>
            Are you sure you want to remove the repository "
            {this.props.repository.name}" from GitHub Desktop?
          </p>
          <div className="description">
            <p>The repository will be removed from GitHub Desktop:</p>
            <p>
              <Ref>{this.props.repository.path}</Ref>
            </p>
          </div>

          <div>
            <Checkbox
              label={'Also move this repository to ' + TrashNameLabel}
              value={
                this.state.deleteRepoFromDisk
                  ? CheckboxValue.On
                  : CheckboxValue.Off
              }
              onChange={this.onConfirmRepositoryDeletion}
            />
          </div>
        </DialogContent>
        <DialogFooter>
          <OkCancelButtonGroup destructive={true} okButtonText="Remove" />
        </DialogFooter>
      </Dialog>
    )
  }
```

**File:** app/src/ui/worktrees/delete-worktree-dialog.tsx (L40-74)
```typescript
  public render() {
    const name = Path.basename(this.props.worktreePath)

    return (
      <Dialog
        id="delete-worktree"
        title={__DARWIN__ ? 'Delete Worktree' : 'Delete worktree'}
        type="warning"
        onSubmit={this.onSubmit}
        onDismissed={this.props.onDismissed}
        disabled={this.state.isDeleting}
        loading={this.state.isDeleting}
        role="alertdialog"
        ariaDescribedBy="delete-worktree-confirmation"
      >
        <DialogContent>
          <p id="delete-worktree-confirmation">
            Are you sure you want to delete the worktree <Ref>{name}</Ref>?
          </p>
          <Checkbox
            label="Do not show this message again"
            value={
              this.state.confirmWorktreeRemoval
                ? CheckboxValue.Off
                : CheckboxValue.On
            }
            onChange={this.onConfirmWorktreeRemovalChanged}
          />
        </DialogContent>
        <DialogFooter>
          <OkCancelButtonGroup destructive={true} okButtonText="Delete" />
        </DialogFooter>
      </Dialog>
    )
  }
```

**File:** app/src/ui/preferences/prompts.tsx (L233-323)
```typescript
  public render() {
    return (
      <DialogContent>
        <div className="advanced-section">
          <h2 id="show-confirm-dialog-heading">
            Show a confirmation dialog before...
          </h2>
          <div role="group" aria-labelledby="show-confirm-dialog-heading">
            <Checkbox
              label="Removing repositories"
              value={
                this.state.confirmRepositoryRemoval
                  ? CheckboxValue.On
                  : CheckboxValue.Off
              }
              onChange={this.onConfirmRepositoryRemovalChanged}
            />
            <Checkbox
              label="Discarding changes"
              value={
                this.state.confirmDiscardChanges
                  ? CheckboxValue.On
                  : CheckboxValue.Off
              }
              onChange={this.onConfirmDiscardChangesChanged}
            />
            <Checkbox
              label="Discarding changes permanently"
              value={
                this.state.confirmDiscardChangesPermanently
                  ? CheckboxValue.On
                  : CheckboxValue.Off
              }
              onChange={this.onConfirmDiscardChangesPermanentlyChanged}
            />
            <Checkbox
              label="Discarding stash"
              value={
                this.state.confirmDiscardStash
                  ? CheckboxValue.On
                  : CheckboxValue.Off
              }
              onChange={this.onConfirmDiscardStashChanged}
            />
            <Checkbox
              label="Checking out a commit"
              value={
                this.state.confirmCheckoutCommit
                  ? CheckboxValue.On
                  : CheckboxValue.Off
              }
              onChange={this.onConfirmCheckoutCommitChanged}
            />
            <Checkbox
              label="Force pushing"
              value={
                this.state.confirmForcePush
                  ? CheckboxValue.On
                  : CheckboxValue.Off
              }
              onChange={this.onConfirmForcePushChanged}
            />
            <Checkbox
              label="Undo commit"
              value={
                this.state.confirmUndoCommit
                  ? CheckboxValue.On
                  : CheckboxValue.Off
              }
              onChange={this.onConfirmUndoCommitChanged}
            />
            <Checkbox
              label="Overriding commit message with generated message"
              value={
                this.state.confirmCommitMessageOverride
                  ? CheckboxValue.On
                  : CheckboxValue.Off
              }
              onChange={this.onConfirmCommitMessageOverrideChanged}
            />
            <Checkbox
              label="Removing worktrees"
              value={
                this.state.confirmWorktreeRemoval
                  ? CheckboxValue.On
                  : CheckboxValue.Off
              }
              onChange={this.onConfirmWorktreeRemovalChanged}
            />
            {this.renderCommittingFilteredChangesPrompt()}
          </div>
```
