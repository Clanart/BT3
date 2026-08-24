Based on the investigation, the strongest analog to the "any ID can stop any DCA" pattern is GitHub Desktop's **"Open in Desktop" deep-link → PR checkout** flow, where a URL a user clicks (fully attacker-controlled) drives repository selection and an automatic branch checkout with no confirmation and no verification that the checked-out content matches what the user expects.

### Title
Deep link `x-github-client://openrepo/<url>?pr=<n>` silently checks out an attacker-chosen fork/branch into an existing local repository without confirmation - (File: `app/src/ui/dispatcher/dispatcher.ts`)

### Summary
The Solidity report describes `stopDca`, which trusts a caller-supplied `_taskId` without validating that it actually belongs to the caller-owned `dcaId`, letting anyone hijack another user's task by ID. The closest Desktop analog is the `open-repository-from-url` protocol handler: both the target repository URL and PR number in the link are fully attacker-controlled, and Desktop performs a privileged, state-changing action (adding a remote and checking out a branch) purely from information taken from the link and the fetched PR object, without any additional confirmation step or check that the two correlated values (the repo the user thinks they're opening vs. the PR/fork actually checked out) genuinely correspond to something the user intended.

### Finding Description
`parseAppURL` extracts `url`, `pr`, `branch`, and `filepath` straight out of a `x-github-client://openrepo/...` URL with only superficial format validation (numeric `pr`, ref-shaped `branch`) — no semantic validation ties `pr` to `url` beyond calling the GitHub API with them together. [1](#0-0) 

`main.ts` treats any URL matching a registered protocol scheme as trusted enough to dispatch straight into the renderer: [2](#0-1) 

`dispatchURLAction` then routes `open-repository-from-url` actions directly to `openRepositoryFromUrl` with no additional gate: [3](#0-2) 

`openPullRequestFromUrl` fetches the PR object from the API using the attacker-supplied `url`/`pr`, matches it against an already-open local repository purely by comparing clone URLs (`getRepositoryFromPullRequest`/`doesRepositoryMatchUrl`), and then — with no user confirmation dialog — calls `_checkoutPullRequest` using `pullRequest.head.repo.clone_url` and `pullRequest.head.ref`, values that come entirely from the PR object an attacker can create (any PR, from any fork, against any public/target repo the victim happens to already have cloned): [4](#0-3) 

`_checkoutPullRequest`/`_findPullRequestBranch` will silently `addRemote(repository, forkRemoteName, headCloneUrl)` for an arbitrary fork URL and create/checkout a local branch (`pr/<n>`) tracking it, with no prompt to the user confirming this is the fork/branch they intended: [5](#0-4) 

The only "ownership" check that exists is an implicit one — the repo is matched by remote URL to something already cloned locally — but there is no check that the *PR* being checked out was authored by, reviewed by, or otherwise associated with the user's expectations; any PR number against that URL works, exactly like `stopDca` accepting any `_taskId` as long as the caller owns *some* `dcaId`.

### Impact Explanation
An attacker who gets a victim to click a crafted `x-github-client://openrepo/...` link (e.g., embedded in a webpage, chat message, or malicious "Open in Desktop" button) can force Desktop to:
- Add a new remote pointing at an attacker-chosen fork into the victim's already-open repository.
- Silently check out an attacker-chosen branch/PR ref as the new working tree state, with no confirmation dialog.

If the victim, believing they are still on their own work, makes further commits or pushes without checking the branch indicator carefully, their next actions operate on top of attacker-supplied history/content — this is the "silent corruption of what the user commits or pushes" class of impact the task calls out.

### Likelihood Explanation
Likelihood is moderate: it requires a user to click a deep link (a normal, common action for this scheme — it's how GitHub.com's real "Open in Desktop" PR button works), and it requires the target `url` to reference a repository the victim already has cloned locally so the flow goes through `_checkoutPullRequest` (rather than the clone-confirmation `openOrCloneRepository` path shown to a popup). No local access, malware, or leaked credentials are needed — only a link click, matching the report's threat model of "a link or deep link the user clicks."

### Recommendation
- **Short term:** Before calling `_checkoutPullRequest` from a URL-triggered action, show an explicit confirmation dialog naming the fork owner/URL and branch that will be checked out, mirroring how `CloneRepository` already prompts via a popup in `openOrCloneRepository`.
- **Long term:** Track provenance of PR-checkout requests (e.g., require the PR's base repo to be the same GitHub repository the user is currently in, and surface a diff/summary of what remote and ref are about to be added) and add regression tests for `openPullRequestFromUrl`/`_checkoutPullRequest` that assert no remote/branch mutation occurs without user confirmation when the PR head repository differs from any previously known remote.

### Proof of Concept
1. Attacker publishes/sends a link: `x-github-client://openrepo/https://github.com/victim-org/legit-repo?pr=1234`, where PR #1234 is an open pull request the attacker created from their own fork of `legit-repo` with a branch containing attacker-chosen file content.
2. Victim, who already has `legit-repo` cloned in Desktop, clicks the link.
3. `main.ts`'s `open-url`/`--protocol-launcher` handler calls `handleAppURL` → `parseAppURL` → `dispatchURLAction` → `openRepositoryFromUrl` → `openPullRequestFromUrl`. [2](#0-1) 
4. Desktop fetches PR #1234, matches `legit-repo` to the victim's already-open local repository by clone URL, and without any prompt calls `_checkoutPullRequest(repository, 1234, attackerOwner, attackerForkCloneUrl, attackerBranchRef)`. [6](#0-5) 
5. `_findPullRequestBranch` adds a new remote for the attacker's fork and checks out `pr/1234` locally, with the victim's working directory now reflecting attacker-supplied content with no confirmation dialog ever shown. [7](#0-6)

### Citations

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L2118-2120)
```typescript
      case 'open-repository-from-url':
        this.openRepositoryFromUrl(action)
        break
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
