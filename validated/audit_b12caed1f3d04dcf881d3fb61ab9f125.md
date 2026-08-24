### Title
Deep-link `openrepo?pr=` action silently adds an attacker-controlled fork remote and checks out its branch without confirmation - (File: app/src/ui/dispatcher/dispatcher.ts)

### Summary
The GitHub-flavored bug (using an unauthenticated/proxy price source instead of the actual asset price, so an attacker-controlled input silently drives a security-relevant decision) maps in Desktop to `openPullRequestFromUrl`. When a user opens an `x-github-client://openRepo/<url>?pr=<n>` deep link, Desktop fetches the PR object from the GitHub API and then feeds `pullRequest.head.repo.clone_url` — a value entirely controlled by whoever opened that PR — straight into `_checkoutPullRequest`, which adds a new git remote and checks out the fork's branch, with no additional confirmation step tied to the specific PR/fork being trusted.

### Finding Description
`parseAppURL` recognizes the `openrepo` deep-link action and extracts an optional `pr` number with only a numeric-format check [1](#0-0) . `dispatchURLAction` routes this to `openRepositoryFromUrl`, which, when a `pr` is present, calls `openPullRequestFromUrl(url, pr)` [2](#0-1) .

`openPullRequestFromUrl` fetches the PR object from the API, tries to find a matching *already cloned* repository via `getRepositoryFromPullRequest` (which just compares `head`/`base` `clone_url` against the user's known repositories), and — critically — always finishes by calling `_checkoutPullRequest` with `pullRequest.head.repo.owner.login`, `pullRequest.head.repo.clone_url`, and `pullRequest.head.ref` taken directly from the fetched PR API object [3](#0-2) .

`_checkoutPullRequest`/`_findPullRequestBranch` use that attacker-supplied `headCloneUrl` to add a new remote (`addRemote(repository, forkRemoteName, headCloneUrl)`) if one matching it doesn't already exist, then fetch that remote and check out `headRefName` as a new local branch `pr/<n>` [4](#0-3) .

The broken invariant: the app treats the PR's `head.repo.clone_url`/`head.ref` — fields fully controlled by whoever opens the PR (an unprivileged GitHub account) — as trustworthy enough to trigger, from a single clicked link, an automatic remote-add + fetch + checkout against the user's already-existing local repository, with no UI step that surfaces "you are about to fetch code from `<attacker-fork-url>` and check it out" tied specifically to this action. The only gate is the OS-level "Open in GitHub Desktop?" handshake for the custom protocol, which conveys nothing about the PR number, fork owner, or branch being checked out.

### Impact Explanation
An attacker can craft a link like `x-github-client://openrepo/https://github.com/<popular-org>/<popular-repo>?pr=<attacker-owned-PR-number>` where the PR is opened from the attacker's own fork with any branch content. If the victim already has that repository cloned in Desktop (a common baseline assumption for a "GitHub Desktop" user who works on well-known open-source projects) and clicks the link, Desktop will:
1. Add a new remote pointing at the attacker's fork.
2. Fetch it (network egress to attacker-chosen host content is bounded to git objects, but the fetch itself is unconditional).
3. Silently create and check out a local branch `pr/<n>` containing the attacker's code, replacing the victim's working directory contents.

This is a "silent corruption of what the user commits or pushes" primitive: if the victim, believing they're still on their own branch, edits files and commits/pushes, they may unknowingly build on top of, or merge, attacker-supplied code. If the checked-out fork contains repo tooling that Desktop or an IDE auto-runs (e.g., `.vscode` tasks, git hooks the user later invokes, package manager scripts a user runs "as normal" after checkout), this escalates toward code execution — though that step requires an additional user action outside Desktop itself.

### Likelihood Explanation
The user-facing trigger is only a single click on a link (or a webpage/embed containing the custom URI), which is within the accepted attack surface ("a link or deep link the user clicks"). No local access, admin rights, or prior malware is required. The precondition that the victim already has the target repository cloned is realistic for popular repositories with many Desktop-using contributors/maintainers. The PR itself is attacker-controlled and free to create (anyone can open a PR from their own fork against a public repo).

### Recommendation
Before invoking `_checkoutPullRequest` from a deep-link-triggered flow, require explicit, PR-specific user confirmation that displays the fork owner/URL and target branch, similar to the confirmation already implied by the manual "Checkout PR" UI flow. Additionally, treat `pr`-originated deep links distinctly from `branch`/plain `open-repository-from-url` actions: surface a dialog naming the exact remote URL and ref about to be fetched/checked out, and let the user cancel, rather than silently performing `addRemote` + `fetch` + `checkout` as soon as an already-known repository is matched.

### Proof of Concept
1. Victim has `https://github.com/octocat/Hello-World` cloned in GitHub Desktop.
2. Attacker forks `octocat/Hello-World`, pushes a branch with modified files, and opens PR #N from the fork against `octocat/Hello-World`.
3. Attacker sends the victim a link: `x-github-client://openRepo/https://github.com/octocat/Hello-World?pr=N`.
4. Victim clicks the link and confirms the generic OS "Open in GitHub Desktop" prompt (which shows no information about the specific PR/fork).
5. `parseAppURL` → `dispatchURLAction` → `openPullRequestFromUrl` fetches PR #N, matches the existing local repository, then calls `_checkoutPullRequest` with the attacker's fork `clone_url` and branch.
6. Desktop adds a `github-desktop-<attacker>` remote, fetches it, and checks out `pr/N` locally — replacing the working tree with attacker content without any PR-specific confirmation dialog.

Note: I could not fully verify from the indexed code whether any additional confirmation dialog exists specifically inside `openPullRequestFromUrl`'s "existing repository" branch (versus the "new clone" branch, which does show a `CloneRepository` popup) — the code path traced shows no such dialog is invoked before `_checkoutPullRequest` in the "existing repository" case, but a Devin session with full repo access would be needed to double-check for any confirmation UI elsewhere in `selectRepository`/`_refreshRepository` that might intercept this flow.

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
