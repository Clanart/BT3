### Title
Deep-link `openrepo://.../?pr=N` silently checks out an attacker-controlled fork branch into a victim's *already-trusted* local repository without confirmation - ([File: app/src/ui/dispatcher/dispatcher.ts])

### Summary
GitHub Desktop's `x-github-client://openrepo/<owner>/<repo>?pr=<N>` deep link handler resolves a pull request purely from attacker-influenceable PR API data and, if it matches an existing local repository (by clone/html URL string comparison only), automatically fetches and checks out the PR's *head* branch — which can point to an arbitrary fork/branch chosen by the PR author — with no ownership/authorship check and no user confirmation before the checkout occurs. This mirrors the Astaria bug class: authorization is derived from an attacker-controllable identity match ("URL string equality") rather than a real trust/consent check, so a value fully controlled by the untrusted party (the PR's `head.repo.clone_url` / `head.ref`) drives a sensitive action (checkout) against the victim's own resource.

### Finding Description
The flow is:

1. A deep link is parsed by `parseAppURL` into an `open-repository-from-url` action carrying an optional `pr` number, taken directly from the URL query string. [1](#0-0) 

2. `Dispatcher.openRepositoryFromUrl` routes PR-bearing actions to `openPullRequestFromUrl`. [2](#0-1) 

3. `openPullRequestFromUrl` fetches the PR from the GitHub API using the `url`/`pr` values taken from the link, then calls `getRepositoryFromPullRequest`, which scans the user's already-open repositories and matches one whose `htmlURL` (origin) or upstream `htmlURL` string-equals the PR's `head.repo.clone_url` or `base.repo.clone_url`. [3](#0-2) 

4. If a match is found, Desktop selects that repository and, without any additional prompt, calls `_checkoutPullRequest` with `pullRequest.head.repo.owner.login`, `pullRequest.head.repo.clone_url`, and `pullRequest.head.ref` — all fields controlled by whoever opened the PR (the attacker, if they authored it against the matched repository). [4](#0-3) 

5. `_findPullRequestBranch`/`_checkoutPullRequest` then adds a remote for the head clone URL (if not already present), fetches it, creates/uses a local branch tracking it, and performs an actual `git checkout`, overwriting the working directory contents with the attacker-supplied branch. [5](#0-4) 

The matching predicate that gates this entire chain is a pure string comparison (`urlsMatch` on parsed owner/name/hostname) — it establishes no relationship other than "this PR happens to target/originate-from a repo whose URL matches one you already have open." [6](#0-5) 

Just as in the Astaria report — where `receiver == holder` was treated as sufficient proof of authorization even though `receiver` was attacker-supplied — here "PR head/base URL equals a known repo" is treated as sufficient justification to auto-select the repository and immediately check out attacker-controlled branch content, even though the PR number, head owner, head clone URL and head ref are all values the attacker (PR author) fully controls. No step verifies that the *user* asked to view or trust this specific PR/fork before code is fetched and checked out into their working tree.

### Impact Explanation
An attacker who can get a victim to click a link (e.g. embedded in an email, chat message, or malicious webpage) pointing at `x-github-client://openrepo/<owner>/<repo>?pr=<N>` for a public repository the victim already has cloned in Desktop can cause Desktop to:
- silently add a new remote pointing at the attacker's fork,
- fetch it,
- and check out the attacker's branch into the victim's existing, trusted local clone,

all without the victim reviewing or approving the specific fork/branch being introduced. This corresponds to "silent corruption of what the user commits or pushes": if the victim doesn't notice the branch switch and later stages/commits/pushes on top of it, or runs build/test tooling against the checked-out attacker content, it can lead to code execution via build scripts/hooks or the introduction of malicious commits into the victim's workflow.

### Likelihood Explanation
Requires only a link click by the victim (an accepted attacker primitive per the scope) plus the attacker being able to open a pull request against a public repository the victim has open in Desktop — both low-cost, no elevated privileges, no local access, and no leaked credentials needed. The comment in the code itself (`"triggering an 'Open PR from Desktop' action from a browser"`) confirms this is a reachable, intended entry point rather than dead code.

### Recommendation
Before performing the automatic `_checkoutPullRequest` fetch/checkout for a PR resolved via a deep link, surface an explicit confirmation dialog naming the fork owner/URL and branch that will be fetched and checked out, and require the user's affirmative action. Additionally, do not treat URL-string equality between a PR's `head`/`base` repo and a locally known repository as sufficient authorization to auto-select and mutate that repository's working directory — treat deep-link-originated PR checkouts identically to a manual "Checkout PR" action, which should always be a deliberate, visible user gesture rather than a side effect of merely opening a link.

### Proof of Concept
1. Attacker forks `victim-org/target-repo` (a repo the victim already has cloned in Desktop) and pushes a branch containing a malicious `.git/hooks`-triggering script or a modified build/config file.
2. Attacker opens a pull request from their fork's branch against `victim-org/target-repo`, obtaining PR number `N`.
3. Attacker sends the victim a link: `x-github-client://openrepo/victim-org/target-repo?pr=N`.
4. Victim clicks the link. Desktop's `parseAppURL` → `dispatchURLAction` → `openRepositoryFromUrl` → `openPullRequestFromUrl` pipeline fires. [7](#0-6) 
5. `getRepositoryFromPullRequest` finds the victim's existing local clone (its `htmlURL` matches `base.repo.clone_url`), Desktop selects it, then immediately calls `_checkoutPullRequest` with the attacker's `head.repo.clone_url` / `head.ref`. [8](#0-7) 
6. `_findPullRequestBranch` adds a `github-desktop-<owner>` remote for the attacker's fork, fetches it, and checks out `pr/N` locally — all without any confirmation dialog shown to the victim. [9](#0-8)

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

**File:** app/src/lib/repository-matching.ts (L137-148)
```typescript
export function urlsMatch(url1: string, url2: string) {
  const firstIdentifier = parseRepositoryIdentifier(url1)
  const secondIdentifier = parseRepositoryIdentifier(url2)

  return (
    firstIdentifier !== null &&
    secondIdentifier !== null &&
    firstIdentifier.hostname === secondIdentifier.hostname &&
    firstIdentifier.owner === secondIdentifier.owner &&
    firstIdentifier.name === secondIdentifier.name
  )
}
```
