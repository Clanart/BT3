### Title
Deep-link "Open Pull Request in Desktop" silently adds an attacker-controlled remote and checks out its branch with no confirmation dialog - (File: app/src/ui/dispatcher/dispatcher.ts)

### Summary
The `x-github-client://openRepo/...?pr=<n>` protocol handler lets any web page or link cause Desktop to fetch a specific pull request from the GitHub API and immediately add a new git remote pointing at the PR's head repository and check out its branch — all without ever showing the user a confirmation dialog that discloses the remote URL/owner that is about to be added and fetched into their local repository. This mirrors the "Gas Fee Not Shown" bug class: an action with material, attacker-influenced side effects (here, adding a remote and fetching/checking out third-party content) executes without surfacing the decisive parameter (the fork clone URL/owner) for user approval.

### Finding Description
The protocol handler registered in `app/src/main-process/main.ts` (`handleAppURL` → `parseAppURL`) accepts URLs like `x-github-client://openRepo/<url>?pr=<n>`, validating only that `pr` is numeric and that `branch` matches `pr/\d+` [1](#0-0) .

This is dispatched to `dispatchURLAction` → `openRepositoryFromUrl` → `openPullRequestFromUrl` in `app/src/ui/dispatcher/dispatcher.ts`, which calls `this.appStore.fetchPullRequest(url, pr)` to pull PR data from the GitHub API, then — with no intervening confirmation UI — calls `_checkoutPullRequest` using `pullRequest.head.repo.owner.login`, `pullRequest.head.repo.clone_url`, and `pullRequest.head.ref` taken directly from the API response [2](#0-1) .

`_checkoutPullRequest`/`_findPullRequestBranch` in `app-store.ts` then silently calls `addRemote(repository, forkRemoteName, headCloneUrl)` for any clone URL not already present as a remote, fetches it, and creates/checks out a local branch tracking it [3](#0-2) . None of this path shows the user a dialog naming the fork owner or clone URL before the remote is added and content fetched — contrast with other destructive/consequential actions in the app (force push, checkout commit, discard changes) which explicitly render a `Dialog`/`ConfirmForcePush`/`ConfirmCheckoutCommitDialog` component summarizing the action before proceeding [4](#0-3) [5](#0-4) .

The `pr` number is attacker-controlled input embedded in a link (the same "attacker controls...a link...the user clicks" primitive as the report), and `pullRequest.head.repo.clone_url`/`owner.login` is an attacker-influenced GitHub API object (anyone can open a PR from their own fork against a public repository), so the resulting remote/branch is effectively attacker-chosen, yet it is silently written into `.git/config` and checked out.

### Impact Explanation
Once the fork remote is added and its branch is checked out, the user's working directory now contains attacker-supplied file contents (including build scripts, editor configs, `.vscode/tasks.json`, git hooks via `core.hooksPath` if committed, etc.) without any dialog telling the user which remote/owner is being pulled from. Since Desktop already treats "adding untrusted repositories may automatically execute files" as security-relevant (see `MissingRepository`/`AddExistingRepository` "unsafe directory" warnings) [6](#0-5) , silently checking out unreviewed fork content via a clicked link is a comparable trust boundary crossing — the user never approved the specific source, matching the report's "confirm before a consequential value is applied" defect class.

### Likelihood Explanation
The path is reachable purely by the user clicking a link (browser or any app that can open `x-github-client://` / `github-mac://` / `github-windows://` URLs) with a PR number the attacker controls; the URL format is validated only for structural well-formedness (`/^\d+$/`, `/^pr\/\d+$/`, `testForInvalidChars`), not for repository trust [7](#0-6) . No account privilege beyond opening a PR (which anyone can do from their own fork) is required.

### Recommendation
Before calling `_checkoutPullRequest` from `openPullRequestFromUrl` (deep-link path only — the existing in-app "Pull Requests" list checkout can remain frictionless since the user is already viewing the PR context), show a confirmation dialog that explicitly displays the resolved `pullRequest.head.repo.owner.login`, `pullRequest.head.repo.clone_url`, and branch name, requiring explicit user approval before the remote is added and fetched.

### Proof of Concept
1. Attacker forks any public repository the target user has cloned in Desktop, adds malicious content to a branch, and opens a PR against the upstream from that fork.
2. Attacker sends the target a link: `x-github-client://openRepo/https://github.com/<upstream-owner>/<repo>?pr=<attacker-pr-number>`.
3. User clicks the link; Desktop opens, calls `handleAppURL` → `parseAppURL` → `dispatchURLAction` → `openPullRequestFromUrl` [8](#0-7) .
4. `_checkoutPullRequest` adds a new remote pointing at the attacker's fork clone URL and checks out the attacker's branch with zero confirmation dialog shown to the user [9](#0-8) .

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

**File:** app/src/ui/rebase/confirm-force-push.tsx (L36-68)
```typescript
  public render() {
    return (
      <Dialog
        title="Are you sure you want to force push?"
        dismissDisabled={this.state.isLoading}
        onDismissed={this.props.onDismissed}
        onSubmit={this.onForcePush}
        type="warning"
      >
        <DialogContent>
          <p>
            A force push will rewrite history on{' '}
            <Ref>{this.props.upstreamBranch}</Ref>. Any collaborators working on
            this branch will need to reset their own local branch to match the
            history of the remote.
          </p>
          <div>
            <Checkbox
              label="Do not show this message again"
              value={
                this.state.askForConfirmationOnForcePush
                  ? CheckboxValue.Off
                  : CheckboxValue.On
              }
              onChange={this.onAskForConfirmationOnForcePushChanged}
            />
          </div>
        </DialogContent>
        <DialogFooter>
          <OkCancelButtonGroup destructive={true} okButtonText="I'm sure" />
        </DialogFooter>
      </Dialog>
    )
```

**File:** app/src/ui/checkout/confirm-checkout-commit.tsx (L38-76)
```typescript
  public render() {
    const title = __DARWIN__ ? 'Checkout Commit?' : 'Checkout commit?'

    return (
      <Dialog
        id="checkout-commit"
        type="warning"
        title={title}
        loading={this.state.isCheckingOut}
        disabled={this.state.isCheckingOut}
        onSubmit={this.onSubmit}
        onDismissed={this.props.onDismissed}
        ariaDescribedBy="checking-out-commit-confirmation"
        role="alertdialog"
      >
        <DialogContent>
          <Row id="checking-out-commit-confirmation">
            Checking out a commit will create a detached HEAD, and you will no
            longer be on any branch. Are you sure you want to checkout this
            commit?
          </Row>
          <Row>
            <Checkbox
              label="Do not show this message again"
              value={
                this.state.confirmCheckoutCommit
                  ? CheckboxValue.Off
                  : CheckboxValue.On
              }
              onChange={this.onaskForConfirmationOnCheckoutCommitChanged}
            />
          </Row>
        </DialogContent>
        <DialogFooter>
          <OkCancelButtonGroup destructive={true} okButtonText="Checkout" />
        </DialogFooter>
      </Dialog>
    )
  }
```

**File:** app/src/ui/missing-repository.tsx (L111-128)
```typescript
    if (isPathUnsafe) {
      return (
        <UiView id="missing-repository-view">
          <div className="title-container">
            <div className="title">
              {this.props.repository.name} is potentially unsafe
            </div>
            <div className="details">
              <p>
                The Git repository at <Ref>{unsafePath}</Ref> appears to be
                owned by another user on your machine. Adding untrusted
                repositories may automatically execute files in the repository.
              </p>
              <p>
                If you trust the owner of the directory you can add an exception
                for this directory in order to continue.
              </p>
            </div>
```

**File:** app/src/main-process/main.ts (L159-168)
```typescript
function handleAppURL(url: string) {
  log.info('Processing protocol url')
  const action = parseAppURL(url)
  onDidLoad(window => {
    // This manual focus call _shouldn't_ be necessary, but is for Chrome on
    // macOS. See https://github.com/desktop/desktop/issues/973.
    window.focus()
    window.sendURLAction(action)
  })
}
```
